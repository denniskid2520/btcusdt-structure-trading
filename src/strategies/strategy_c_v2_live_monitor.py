"""Strategy C v2 live monitor — pure state-machine primitives.

This module defines the state a live runner needs to track to operate
a deployed Strategy C v2 candidate without re-training. Every 4h (or
whatever the deployment frame is), the live loop:

    1. Refreshes the feature snapshot (just the fields the monitor reads)
    2. Calls `compute_monitor_state(snap, open_position, config)`
    3. Acts on `state.action` ("enter_long" / "hold" / "exit" / ...)

**No re-training** happens in this code path. The monitor only evaluates
the deployed rule against fresh data — any knob tuning is a separate
offline research cycle.

The monitor codifies three Phase 3 findings:

- **RSI trend-following** (Stefaniuk-style): signal at bar close is
  +1 if RSI > upper, -1 if RSI < lower, else 0. For the
  `rsi_and_macd` family, signal requires BOTH rsi-extremity AND
  macd_hist sign confirmation.
- **Funding asymmetry**: hostile funding is a SHORT-SIDE veto only.
  Blocking longs in hot funding hurt returns by ~29 pp on 4 years of
  OOS data; blocking shorts in hostile negative funding helped. So
  the monitor blocks NEW SHORT entries when funding is hostile to
  shorts AND exits EXISTING shorts in that regime, but does NOT do
  either for longs.
- **Time-stop + opposite-flip exit**: matches the backtester's exit
  logic exactly so live decisions mirror backtest decisions.

Phase 8 additions (dynamic sizing, adaptive hold, stop levels):

- When `config.use_dynamic_sizing` is True, the monitor computes a
  conviction multiplier from
  `strategy_c_v2_dynamic_sizing.compute_sizing_multiplier` and
  reports `actual_frac = base_frac * multiplier` in MonitorState.
- When `config.use_adaptive_hold` is True, the monitor computes a
  per-trade hold override from
  `strategy_c_v2_dynamic_sizing.compute_hold_override` and reports
  it in `hold_bars_override`. The live runner uses this to override
  its own `max_hold_bars` check for the specific trade.
- When `config.stop_loss_pct > 0`, the monitor computes the absolute
  stop price for the intended entry and reports it in `stop_level`.

The dynamic sizing and adaptive hold helpers are imported from a
**shared** module — the same helpers that `run_v2_backtest` and the
retrospective paper runner use — so the three code paths cannot
drift. See `tests/test_strategy_c_v2_parity.py` for the parity gate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from strategies.strategy_c_v2_dynamic_sizing import (
    DEFAULT_ADAPTIVE_HOLD_CONFIG,
    DEFAULT_DYNAMIC_SIZING_CONFIG,
    AdaptiveHoldConfig,
    AdaptiveHoldResult,
    DynamicSizingConfig,
    DynamicSizingResult,
    compute_hold_override,
    compute_sizing_multiplier,
)


Side = Literal["long", "short"]
Regime = Literal["long_trend", "short_trend", "neutral", "unknown"]
Action = Literal[
    "enter_long",
    "enter_short",
    "hold",
    "exit",
    "stand_aside",
]
SignalFamily = Literal["rsi_only", "rsi_and_macd"]
StopSemantics = Literal["strategy_close_stop", "exchange_intrabar_stop"]


@dataclass(frozen=True)
class MonitorConfig:
    """Deployment parameters for the live monitor.

    These mirror the backtest parameters 1:1 — no live-only tuning allowed.

    Phase 8 additions (all backward-compatible, optional):
        signal_family: "rsi_only" (default, preserves Phase 3-7
            behaviour) or "rsi_and_macd" (C_long cells).
        macd_hist_field: feature field name for MACD histogram when
            signal_family is rsi_and_macd.
        stop_loss_pct: fixed stop loss as fraction of entry price.
            0 disables (default).
        stop_semantics: "strategy_close_stop" (default) or
            "exchange_intrabar_stop".
        use_dynamic_sizing / base_frac / dynamic_sizing_config:
            enable composite-score position sizing. When False,
            `actual_frac = base_frac` on every entry.
        use_adaptive_hold / adaptive_hold_config: enable per-trade
            hold modulation. When False, `hold_bars_override` is
            always None and the live runner uses max_hold_bars.
    """
    # ── signal source ───────────────────────────────────────────
    signal_family: SignalFamily = "rsi_only"
    rsi_field: str = "rsi_21"  # or rsi_14 / rsi_20 / rsi_30 depending on the candidate
    macd_hist_field: str = "macd_hist"
    rsi_upper: float = 70.0
    rsi_lower: float = 30.0

    # ── funding rules ───────────────────────────────────────────
    hostile_long_funding: float = 0.0005  # informational only for longs
    hostile_short_funding: float = -0.0005  # enforcement threshold for shorts

    # ── exit rules ──────────────────────────────────────────────
    max_hold_bars: int = 12

    # ── stop loss (Phase 8 addition) ────────────────────────────
    stop_loss_pct: float = 0.0  # 0 means no stop
    stop_semantics: StopSemantics = "strategy_close_stop"

    # ── dynamic sizing (Phase 8 addition) ───────────────────────
    use_dynamic_sizing: bool = False
    base_frac: float = 1.0
    dynamic_sizing_config: DynamicSizingConfig = field(
        default_factory=DynamicSizingConfig
    )

    # ── adaptive hold (Phase 8 addition) ────────────────────────
    use_adaptive_hold: bool = False
    adaptive_hold_config: AdaptiveHoldConfig = field(
        default_factory=AdaptiveHoldConfig
    )

    def __post_init__(self) -> None:
        if self.stop_loss_pct < 0 or self.stop_loss_pct >= 1:
            raise ValueError(
                f"stop_loss_pct must be in [0, 1), got {self.stop_loss_pct}"
            )
        if self.stop_semantics not in (
            "strategy_close_stop",
            "exchange_intrabar_stop",
        ):
            raise ValueError(
                f"stop_semantics must be 'strategy_close_stop' or "
                f"'exchange_intrabar_stop', got {self.stop_semantics!r}"
            )
        if self.signal_family not in ("rsi_only", "rsi_and_macd"):
            raise ValueError(
                f"signal_family must be 'rsi_only' or 'rsi_and_macd', "
                f"got {self.signal_family!r}"
            )
        if self.base_frac < 0:
            raise ValueError(
                f"base_frac must be >= 0, got {self.base_frac}"
            )


@dataclass(frozen=True)
class LivePositionState:
    """Snapshot of an open position the live runner is holding.

    The live runner keeps this in its own state (e.g. a small journal
    file). The monitor does not read this from anywhere — it is passed
    in as a pure input.

    Phase 8 addition: `position_frac` and `max_hold_override` let the
    monitor preserve the per-trade sizing and hold decisions across
    subsequent bars without recomputing them from stale features.
    """
    side: Side
    entry_time: datetime
    entry_price: float
    bars_held: int
    position_frac: float = 1.0          # actual_frac at entry time
    max_hold_override: int | None = None  # adaptive hold override at entry time
    stop_level: float | None = None     # absolute stop price


@dataclass(frozen=True)
class MonitorState:
    """Output of one monitor evaluation.

    `action` is what the live runner should do RIGHT NOW. Everything
    else is diagnostic state the runner can log / expose.

    Phase 8 new fields (all default to neutral values so existing
    callers are unaffected):
        actual_frac: effective position_frac for this decision. 0 for
            non-entry actions. For entry actions, base_frac when
            fixed sizing, or base_frac * sizing_multiplier when
            dynamic.
        sizing_multiplier: the dynamic multiplier output in
            [dynamic_sizing_config.multiplier_min, ...max]. 1.0 if
            use_dynamic_sizing is False.
        sizing_components: per-component score breakdown for
            telemetry / post-hoc analysis. Empty dict if fixed.
        hold_bars_override: per-trade hold override from adaptive
            exit logic. None if use_adaptive_hold is False.
        hold_regime: "extend" / "base" / "compress" diagnostic label
            matching the adaptive hold branch that fired.
        stop_level: absolute price at which the stop fires. None if
            stop_loss_pct == 0.
    """
    # ── existing ────────────────────────────────────────────────
    current_regime: Regime
    current_signal: int  # -1, 0, +1 (post-veto)
    hostile_funding: bool
    early_exit_reason: str | None
    action: Action
    blocked_reason: str | None
    rsi_value: float | None
    funding_rate: float | None

    # ── Phase 8 additions ───────────────────────────────────────
    actual_frac: float = 0.0
    sizing_multiplier: float = 1.0
    sizing_components: dict[str, float] = field(default_factory=dict)
    hold_bars_override: int | None = None
    hold_regime: str = "base"
    stop_level: float | None = None


# ── internal helpers ────────────────────────────────────────────────


def _compute_rsi_only_signal(
    rsi: float | None,
    config: MonitorConfig,
) -> int:
    """rsi_only family signal."""
    if rsi is None:
        return 0
    if rsi > config.rsi_upper:
        return 1
    if rsi < config.rsi_lower:
        return -1
    return 0


def _compute_rsi_and_macd_signal(
    rsi: float | None,
    macd_hist: float | None,
    config: MonitorConfig,
) -> int:
    """rsi_and_macd family signal — requires both RSI extremity AND
    MACD histogram sign confirmation."""
    if rsi is None or macd_hist is None:
        return 0
    if rsi > config.rsi_upper and macd_hist > 0:
        return 1
    if rsi < config.rsi_lower and macd_hist < 0:
        return -1
    return 0


def _classify_regime(signal: int, rsi: float | None) -> Regime:
    if rsi is None:
        return "unknown"
    if signal > 0:
        return "long_trend"
    if signal < 0:
        return "short_trend"
    return "neutral"


def _compute_stop_level(
    entry_price: float,
    side: int,
    stop_loss_pct: float,
) -> float | None:
    if stop_loss_pct <= 0:
        return None
    if side > 0:
        return entry_price * (1.0 - stop_loss_pct)
    if side < 0:
        return entry_price * (1.0 + stop_loss_pct)
    return None


# ── main entrypoint ─────────────────────────────────────────────────


def compute_monitor_state(
    feature_snapshot: Any,
    open_position: LivePositionState | None,
    config: MonitorConfig,
) -> MonitorState:
    """Evaluate the live monitor against a fresh feature snapshot.

    Args:
        feature_snapshot: Duck-typed object exposing `config.rsi_field`
            (e.g. "rsi_21"), `funding_rate`, `close`, `timestamp`,
            and — when signal_family is rsi_and_macd —
            `config.macd_hist_field`.
        open_position: Current open position, or None if flat.
        config: Deployment config (must match backtest parameters).

    Returns:
        MonitorState describing the recommended action and diagnostic context.
    """
    rsi = getattr(feature_snapshot, config.rsi_field, None)
    funding = getattr(feature_snapshot, "funding_rate", None)
    close = getattr(feature_snapshot, "close", None)

    # ── 1. compute raw signal (pre-veto) ─────────────────────────
    if config.signal_family == "rsi_only":
        raw_signal = _compute_rsi_only_signal(rsi, config)
    else:  # rsi_and_macd
        macd_hist = getattr(feature_snapshot, config.macd_hist_field, None)
        raw_signal = _compute_rsi_and_macd_signal(rsi, macd_hist, config)

    regime = _classify_regime(raw_signal, rsi)

    # ── 2. hostile funding detection ──────────────────────────────
    hostile_funding = False
    if funding is not None:
        if open_position is not None and open_position.side == "long":
            hostile_funding = funding > config.hostile_long_funding
        elif open_position is not None and open_position.side == "short":
            hostile_funding = funding < config.hostile_short_funding
        elif open_position is None:
            if raw_signal > 0:
                hostile_funding = funding > config.hostile_long_funding
            elif raw_signal < 0:
                hostile_funding = funding < config.hostile_short_funding

    # ── 3. decide action ──────────────────────────────────────────
    #
    # Contract: `current_signal` is the RAW pre-veto signal. The
    # veto only affects `action` and `blocked_reason`. This matches
    # Phase 4's test suite — the live runner knows the veto fired
    # because action==stand_aside AND blocked_reason is set, even
    # though current_signal is still non-zero.
    action: Action
    current_signal = raw_signal
    early_exit_reason: str | None = None
    blocked_reason: str | None = None

    # Defaults for Phase 8 new telemetry fields
    actual_frac = 0.0
    sizing_multiplier = 1.0
    sizing_components: dict[str, float] = {}
    hold_bars_override: int | None = None
    hold_regime = "base"
    stop_level: float | None = None

    if open_position is not None:
        # Managing an existing position
        effective_max_hold = (
            open_position.max_hold_override
            if open_position.max_hold_override is not None
            else config.max_hold_bars
        )
        if open_position.bars_held >= effective_max_hold:
            action = "exit"
            early_exit_reason = "time_stop"
        elif open_position.side == "long" and raw_signal == -1:
            action = "exit"
            early_exit_reason = "opposite_signal"
        elif open_position.side == "short" and raw_signal == 1:
            action = "exit"
            early_exit_reason = "opposite_signal"
        elif open_position.side == "short" and hostile_funding:
            action = "exit"
            early_exit_reason = "hostile_funding_short"
        else:
            action = "hold"
        # Report the open position's frac and hold override on every
        # bar so telemetry can see what's currently on the book.
        actual_frac = open_position.position_frac
        hold_bars_override = open_position.max_hold_override
        stop_level = open_position.stop_level
    else:
        # No open position — consider a new entry
        if raw_signal == 1:
            action = "enter_long"
        elif raw_signal == -1:
            if hostile_funding:
                action = "stand_aside"
                blocked_reason = "hostile_funding_short_entry"
            else:
                action = "enter_short"
        else:
            action = "stand_aside"

        # Compute sizing + hold + stop if we're actually going to enter
        if action in ("enter_long", "enter_short"):
            side_int = 1 if action == "enter_long" else -1

            if config.use_dynamic_sizing:
                sizing_result: DynamicSizingResult = compute_sizing_multiplier(
                    feature_snapshot,
                    side_int,
                    config.dynamic_sizing_config,
                )
                sizing_multiplier = sizing_result.multiplier
                sizing_components = dict(sizing_result.component_scores)
            else:
                sizing_multiplier = 1.0

            actual_frac = config.base_frac * sizing_multiplier

            if config.use_adaptive_hold:
                hold_result: AdaptiveHoldResult = compute_hold_override(
                    feature_snapshot,
                    side_int,
                    config.max_hold_bars,
                    config.adaptive_hold_config,
                )
                hold_bars_override = hold_result.hold_bars
                hold_regime = hold_result.regime
            else:
                hold_bars_override = None
                hold_regime = "base"

            if close is not None and config.stop_loss_pct > 0:
                stop_level = _compute_stop_level(
                    entry_price=close,
                    side=side_int,
                    stop_loss_pct=config.stop_loss_pct,
                )

    return MonitorState(
        current_regime=regime,
        current_signal=current_signal,
        hostile_funding=hostile_funding,
        early_exit_reason=early_exit_reason,
        action=action,
        blocked_reason=blocked_reason,
        rsi_value=rsi,
        funding_rate=funding,
        actual_frac=actual_frac,
        sizing_multiplier=sizing_multiplier,
        sizing_components=sizing_components,
        hold_bars_override=hold_bars_override,
        hold_regime=hold_regime,
        stop_level=stop_level,
    )
