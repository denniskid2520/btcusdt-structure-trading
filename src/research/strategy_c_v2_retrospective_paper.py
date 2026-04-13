"""Strategy C v2 Phase 8 retrospective paper runner.

Replays a historical slice of features bar-by-bar through the live
monitor state machine, recording the entry + exit decisions as paper
trades. This is the bridge between:

  - `run_v2_backtest` (vectorised historical backtest)
  - `compute_monitor_state` (per-bar live decision maker)

For every decision point, the retrospective runner:

  1. Builds a feature snapshot for bar i
  2. Calls `compute_monitor_state(snapshot, open_pos, config)`
  3. Records the decision in a paper log
  4. Opens/closes `LivePositionState` based on `state.action`
  5. Advances to bar i+1

The critical property this module delivers is **parity**: if you
configure the retrospective runner with the same cell config you
pass to `run_v2_backtest`, the two code paths must produce
identical (side, signal_time, actual_frac, hold_override,
stop_level) per trade. The parity test (`tests/test_strategy_c_v2_parity.py`)
enforces this invariant.

The retrospective runner is intentionally small — it does not
re-implement signal, sizing, or exit logic. All of that is delegated
to `compute_monitor_state`, which in turn uses the shared
`strategy_c_v2_dynamic_sizing` module. The backtester calls the same
shared module with the same configs. One bug, three places catch it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Sequence

from adapters.base import MarketBar
from strategies.strategy_c_v2_live_monitor import (
    LivePositionState,
    MonitorConfig,
    MonitorState,
    compute_monitor_state,
)


@dataclass(frozen=True)
class PaperDecision:
    """One (bar_index, monitor_state) decision record.

    These are produced for EVERY bar the retrospective runner walks
    through, including bars where action=="hold" or "stand_aside".
    """
    bar_index: int
    timestamp: datetime
    state: MonitorState


@dataclass(frozen=True)
class PaperTrade:
    """One closed retrospective paper trade.

    The fields here are the minimum the parity test needs to match
    against `V2Trade` from run_v2_backtest. The retrospective runner
    populates more fields than the backtester's V2Trade — only the
    intersection has to match for parity.
    """
    entry_bar_index: int            # bar where the entry decision fired (the signal bar)
    entry_fill_index: int           # bar where the fill happens (entry_bar+1)
    entry_fill_time: datetime
    entry_fill_price: float
    exit_fill_index: int
    exit_fill_time: datetime
    exit_fill_price: float
    side: int                       # +1 long / -1 short
    actual_frac: float
    hold_bars_used: int             # what max_hold was used for THIS trade
    stop_level: float | None
    exit_reason: str                # "time_stop" / "opposite_flip" / "stop_loss_long" / ...


@dataclass(frozen=True)
class PaperRunResult:
    decisions: tuple[PaperDecision, ...]
    trades: tuple[PaperTrade, ...]


class _SnapshotProxy:
    """Adapter that combines a feature row with a bar's close.

    The live monitor reads `.close` from the snapshot, but the
    feature dataclass may not directly expose bar OHLCV. This proxy
    layers `close = bar.close` onto the feature row so the monitor
    can compute stop levels.
    """

    def __init__(self, feature: Any, close: float, timestamp: datetime) -> None:
        self._feature = feature
        self.close = close
        self.timestamp = timestamp

    def __getattr__(self, name: str) -> Any:
        # Only reached if `name` is not already set on self.
        return getattr(self._feature, name, None)


def _stop_hit(
    bar: MarketBar,
    side: int,
    stop_level: float,
    stop_semantics: str,
) -> bool:
    """Check whether the fixed stop fires on `bar` for a position with
    the given `side` and `stop_level`.

    Uses the same close-trigger / wick-trigger semantics as
    `run_v2_backtest`.
    """
    if stop_semantics == "strategy_close_stop":
        if side > 0:
            return bar.close <= stop_level
        return bar.close >= stop_level
    else:  # exchange_intrabar_stop
        if side > 0:
            return bar.low <= stop_level
        return bar.high >= stop_level


def run_retrospective_paper(
    bars: Sequence[MarketBar],
    features: Sequence[Any],
    signals_external: Sequence[int] | None,
    config: MonitorConfig,
) -> PaperRunResult:
    """Walk a historical slice bar-by-bar through the live monitor.

    Args:
        bars: Execution-frame bars (chronological).
        features: Same-length feature rows. Each row must expose the
            attributes the config references (rsi_field, funding_rate,
            ema_fast/slow/rv fields if dynamic sizing is enabled,
            macd_hist_field if signal_family is rsi_and_macd).
        signals_external: Optional pre-computed signal vector. When
            supplied, the runner uses these signals verbatim instead
            of asking the monitor to compute the signal from rsi/macd.
            Use this when the signal source is outside the monitor's
            own signal-family logic (e.g., signals computed with an
            RSI period not directly supported by the feature row).
        config: Live monitor config. Must have `base_frac` set to
            the cell's base position_frac.

    Returns:
        PaperRunResult with per-bar decisions + closed trades.

    Parity guarantee:
        For the same inputs, `run_retrospective_paper` and
        `run_v2_backtest` must produce trades that match on:
          (side, entry_fill_time, actual_frac, hold_bars_used,
           stop_level, exit_reason)

        within float tolerance for stop_level and actual_frac,
        exact for everything else. This is enforced by
        `tests/test_strategy_c_v2_parity.py`.
    """
    n = len(bars)
    if len(features) != n:
        raise ValueError(
            f"features length {len(features)} != bars length {n}"
        )
    if signals_external is not None and len(signals_external) != n:
        raise ValueError(
            f"signals_external length {len(signals_external)} != bars length {n}"
        )

    decisions: list[PaperDecision] = []
    trades: list[PaperTrade] = []

    # Monitor state machine: single open position at a time
    open_pos: LivePositionState | None = None
    open_bar_index: int = -1
    open_hold_bars_used: int = 0

    i = 0
    while i < n:
        bar = bars[i]
        snap = _SnapshotProxy(features[i], bar.close, bar.timestamp)

        # ── 1. override the signal if external source is provided ─
        if signals_external is not None:
            # Inject the external signal via monkeypatched attributes
            # so compute_monitor_state reads it through the normal
            # code path. We do this by setting rsi / macd_hist values
            # that produce the same signal, but the cleanest way is
            # to pass a "signal override" through a private attribute
            # that the monitor can look for.
            # For simplicity, we replicate the signal-reading logic
            # inline here by calling compute_monitor_state with a
            # proxy that forces rsi into the right range.
            # BUT that couples the retrospective runner to the
            # monitor's internal signal logic. Cleaner: bypass the
            # monitor signal computation by injecting synthetic
            # rsi values that produce the right signal.
            ext_sig = signals_external[i]
            # We build a special snapshot that forces the signal:
            snap = _SignalInjectedSnapshot(
                base=snap,
                force_signal=ext_sig,
                rsi_field=config.rsi_field,
                rsi_upper=config.rsi_upper,
                rsi_lower=config.rsi_lower,
            )

        # ── 2. tick open position bars_held if we have one ────────
        if open_pos is not None:
            open_pos_live = LivePositionState(
                side=open_pos.side,
                entry_time=open_pos.entry_time,
                entry_price=open_pos.entry_price,
                bars_held=i - open_bar_index - 1,
                position_frac=open_pos.position_frac,
                max_hold_override=open_pos.max_hold_override,
                stop_level=open_pos.stop_level,
            )
        else:
            open_pos_live = None

        # ── 3. check intrabar stop loss BEFORE monitor evaluation ─
        # run_v2_backtest fires the stop AT bar j (the first bar
        # where breach occurs) and exits at bar j+1 open. We do the
        # same here — if the stop fires on bar i, we close at i+1.
        stop_fired = False
        if open_pos_live is not None and open_pos_live.stop_level is not None:
            side_int = 1 if open_pos_live.side == "long" else -1
            if _stop_hit(bar, side_int, open_pos_live.stop_level, config.stop_semantics):
                # Stop fires this bar → fill at next bar open
                fill_idx = i + 1
                if fill_idx >= n:
                    fill_idx = n - 1
                    fill_price = bars[fill_idx].close
                    exit_reason = (
                        f"stop_loss_{'long' if side_int > 0 else 'short'}_end_of_series"
                    )
                else:
                    fill_price = bars[fill_idx].open
                    if config.stop_semantics == "exchange_intrabar_stop":
                        # Emulate backtester: exchange_intrabar_stop
                        # fills AT the stop level
                        fill_price = open_pos_live.stop_level
                    exit_reason = f"stop_loss_{'long' if side_int > 0 else 'short'}"

                trades.append(
                    PaperTrade(
                        entry_bar_index=open_bar_index,
                        entry_fill_index=open_bar_index + 1,
                        entry_fill_time=open_pos_live.entry_time,
                        entry_fill_price=open_pos_live.entry_price,
                        exit_fill_index=fill_idx,
                        exit_fill_time=bars[fill_idx].timestamp,
                        exit_fill_price=fill_price,
                        side=side_int,
                        actual_frac=open_pos_live.position_frac,
                        hold_bars_used=(
                            open_pos_live.max_hold_override
                            if open_pos_live.max_hold_override is not None
                            else config.max_hold_bars
                        ),
                        stop_level=open_pos_live.stop_level,
                        exit_reason=exit_reason,
                    )
                )
                open_pos = None
                open_bar_index = -1
                stop_fired = True
                # Advance i to the fill index + 0 (fill at next open,
                # but we check for new entries starting from fill_idx).
                # To match backtester's `next_i = actual_exit_idx`, we
                # set i = fill_idx and continue (no +1).
                i = fill_idx
                continue

        # ── 4. evaluate monitor (action decision) ─────────────────
        state = compute_monitor_state(snap, open_pos_live, config)
        decisions.append(
            PaperDecision(
                bar_index=i,
                timestamp=bar.timestamp,
                state=state,
            )
        )

        # ── 5. apply action ──────────────────────────────────────
        action = state.action

        if action in ("enter_long", "enter_short") and open_pos is None:
            # Fill at next bar open
            fill_idx = i + 1
            if fill_idx >= n:
                i += 1
                continue
            fill_price = bars[fill_idx].open
            side_str = "long" if action == "enter_long" else "short"
            side_int = 1 if side_str == "long" else -1

            # Recompute stop_level from the FILL price, not the
            # signal-bar close. The backtester uses entry_price (=
            # fill price) to anchor the stop. A production live
            # runner must do the same: the monitor's stop_level
            # output at signal-bar close is provisional (it doesn't
            # know the fill yet); after the fill, the runner
            # anchors the stop to the fill price.
            runner_stop_level: float | None = None
            if config.stop_loss_pct > 0:
                if side_int > 0:
                    runner_stop_level = fill_price * (1.0 - config.stop_loss_pct)
                else:
                    runner_stop_level = fill_price * (1.0 + config.stop_loss_pct)

            open_pos = LivePositionState(
                side=side_str,
                entry_time=bars[fill_idx].timestamp,
                entry_price=fill_price,
                bars_held=0,
                position_frac=state.actual_frac,
                max_hold_override=state.hold_bars_override,
                stop_level=runner_stop_level,
            )
            open_bar_index = fill_idx - 1
            # Advance past the signal bar — the fill happens at
            # i+1. Next decision is i+1 onwards.
            i += 1
            continue

        if action == "exit" and open_pos is not None:
            # Backtester exit-fill semantics (see strategy_c_v2_backtest):
            #   time_stop  → actual_exit_idx = entry_idx + hold_bars (CURRENT bar)
            #   flip/stop  → actual_exit_idx = j + 1 (next bar, j = breach bar)
            #
            # In monitor terms, at bar i the monitor detects the
            # condition. For time_stop, the "condition bar" IS the
            # exit bar (bars_held just reached hold). For flip, the
            # condition bar is the breach bar and the exit fills the
            # next bar. So:
            #   time_stop → fill_idx = i
            #   else      → fill_idx = i + 1
            raw_reason = state.early_exit_reason or "time_stop"
            if raw_reason == "time_stop":
                fill_idx = i
            else:
                fill_idx = i + 1

            if fill_idx >= n:
                fill_idx = n - 1
                fill_price = bars[fill_idx].close
                exit_reason = (
                    "end_of_series" if raw_reason == "time_stop"
                    else f"{raw_reason}_end_of_series"
                )
            else:
                fill_price = bars[fill_idx].open
                exit_reason = raw_reason
                if exit_reason == "opposite_signal":
                    exit_reason = "opposite_flip"

            side_int = 1 if open_pos.side == "long" else -1
            trades.append(
                PaperTrade(
                    entry_bar_index=open_bar_index,
                    entry_fill_index=open_bar_index + 1,
                    entry_fill_time=open_pos.entry_time,
                    entry_fill_price=open_pos.entry_price,
                    exit_fill_index=fill_idx,
                    exit_fill_time=bars[fill_idx].timestamp,
                    exit_fill_price=fill_price,
                    side=side_int,
                    actual_frac=open_pos.position_frac,
                    hold_bars_used=(
                        open_pos.max_hold_override
                        if open_pos.max_hold_override is not None
                        else config.max_hold_bars
                    ),
                    stop_level=open_pos.stop_level,
                    exit_reason=exit_reason,
                )
            )
            open_pos = None
            open_bar_index = -1
            # Advance cursor past the fill. For time_stop (fill_idx == i)
            # we need +1 to leave the current bar; for flip/stop
            # (fill_idx == i + 1) setting i = fill_idx leaves us at the
            # fill bar so a new signal can fire there — matching
            # backtester's next_i = actual_exit_idx.
            if raw_reason == "time_stop":
                i = fill_idx + 1
            else:
                i = fill_idx
            continue

        # Hold / stand_aside — advance one bar
        i += 1

    # Close any still-open position at end-of-series
    if open_pos is not None:
        last_idx = n - 1
        fill_price = bars[last_idx].close
        side_int = 1 if open_pos.side == "long" else -1
        trades.append(
            PaperTrade(
                entry_bar_index=open_bar_index,
                entry_fill_index=open_bar_index + 1,
                entry_fill_time=open_pos.entry_time,
                entry_fill_price=open_pos.entry_price,
                exit_fill_index=last_idx,
                exit_fill_time=bars[last_idx].timestamp,
                exit_fill_price=fill_price,
                side=side_int,
                actual_frac=open_pos.position_frac,
                hold_bars_used=(
                    open_pos.max_hold_override
                    if open_pos.max_hold_override is not None
                    else config.max_hold_bars
                ),
                stop_level=open_pos.stop_level,
                exit_reason="end_of_series",
            )
        )

    return PaperRunResult(
        decisions=tuple(decisions),
        trades=tuple(trades),
    )


class _SignalInjectedSnapshot:
    """Snapshot wrapper that coerces the monitor's signal computation
    to match an externally-provided signal value.

    The live monitor reads `rsi` from the snapshot and compares to
    `config.rsi_upper` / `config.rsi_lower` to produce the signal.
    To inject a pre-computed signal verbatim, we replace the rsi
    attribute with a synthetic value on the correct side of the
    thresholds.
    """

    def __init__(
        self,
        base: Any,
        force_signal: int,
        rsi_field: str,
        rsi_upper: float,
        rsi_lower: float,
    ) -> None:
        self._base = base
        if force_signal > 0:
            self._forced_rsi = rsi_upper + 1.0
        elif force_signal < 0:
            self._forced_rsi = rsi_lower - 1.0
        else:
            # Neutral: between thresholds
            self._forced_rsi = (rsi_upper + rsi_lower) / 2.0
        self._rsi_field = rsi_field
        # Also force macd_hist to match so rsi_and_macd family
        # agrees with the forced signal.
        if force_signal > 0:
            self._forced_macd_hist = 1.0
        elif force_signal < 0:
            self._forced_macd_hist = -1.0
        else:
            self._forced_macd_hist = 0.0

    def __getattr__(self, name: str) -> Any:
        if name == self._rsi_field:
            return self._forced_rsi
        if name == "macd_hist":
            return self._forced_macd_hist
        return getattr(self._base, name, None)
