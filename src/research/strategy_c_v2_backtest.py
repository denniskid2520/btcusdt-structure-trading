"""Strategy C v2 cost + funding aware backtester.

Single-position, 1x notional, explicit fees/slippage/funding cashflows.
Entry at t+1 open after signal; exit at exit-bar open. Supports time-stop
and opposite-signal-flip exits. Timeframe-agnostic: the caller passes an
execution-frame bar stream and a same-length signal vector (signals from
higher timeframes must be pre-aligned to the execution frame).

Funding convention (pinned by tests):
    A funding event at bar k is charged against a trade iff
        entry_idx <= k < exit_idx
    That is:
      - Entry exactly at a funding bar → pays that funding (still held through
        the settlement tick).
      - Exit exactly at a funding bar → does NOT pay (already out by the tick).

Cost convention:
    round_trip = 2 * (fee_per_side + slip_per_side), applied once per trade
    and subtracted from the net PnL at close.

Sign convention:
    signals[i] in {+1, 0, -1} is the decision made at the CLOSE of bar i.
    Positive funding rate → longs pay, shorts receive.
    funding_pnl = -side * sum(funding_per_bar[entry_idx : exit_idx])
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import sqrt
from typing import Literal, Sequence

from adapters.base import MarketBar


DEFAULT_FEE_PER_SIDE = 0.0005
DEFAULT_SLIP_PER_SIDE = 0.0001
NO_LOSS_PROFIT_FACTOR = 9999.0

StopTrigger = Literal["close", "wick"]
StopSemantics = Literal["strategy_close_stop", "exchange_intrabar_stop"]


@dataclass(frozen=True)
class V2Trade:
    """One closed trade record with full PnL decomposition."""
    entry_idx: int
    entry_time: datetime
    entry_price: float
    exit_idx: int
    exit_time: datetime
    exit_price: float
    side: int
    hold_bars: int
    gross_pnl: float
    funding_pnl: float
    cost: float
    net_pnl: float
    exit_reason: str  # "time_stop" | "opposite_flip" | "end_of_series" | "opposite_flip_end_of_series"


@dataclass(frozen=True)
class V2BacktestResult:
    """Output of run_v2_backtest."""
    trades: list[V2Trade]
    equity_curve: list[float]
    metrics: dict[str, float]


def run_v2_backtest(
    bars: Sequence[MarketBar],
    signals: Sequence[int],
    funding_per_bar: Sequence[float],
    *,
    hold_bars: int,
    cooldown_bars: int = 0,
    fee_per_side: float = DEFAULT_FEE_PER_SIDE,
    slip_per_side: float = DEFAULT_SLIP_PER_SIDE,
    allow_opposite_flip_exit: bool = True,
    atr_values: Sequence[float | None] | None = None,
    atr_trail_k: float | None = None,
    stop_loss_pct: float | None = None,
    stop_trigger: StopTrigger = "wick",
    stop_slip_pct: float = 0.0,
    stop_semantics: StopSemantics | None = None,
    alpha_stop_pct: float | None = None,
    catastrophe_stop_pct: float | None = None,
    catastrophe_slip_pct: float = 0.0,
    risk_per_trade: float | None = None,
    effective_leverage: float | None = None,
    position_frac_override: Sequence[float | None] | None = None,
    hold_bars_override: Sequence[int | None] | None = None,
) -> V2BacktestResult:
    """Run a single-position v2 backtest.

    Args:
        bars: Execution-frame MarketBar stream (chronological).
        signals: Same-length {+1, 0, -1} decision per bar (at bar close).
        funding_per_bar: Same-length funding rate per bar; 0 outside
            settlement bars. Positive rate = longs pay.
        hold_bars: Time-stop exit after this many bars. Must be > 0.
        cooldown_bars: Bars to wait after exit before reading next signal.
            Must be >= 0.
        fee_per_side: One-way fee fraction.
        slip_per_side: One-way slippage fraction.
        allow_opposite_flip_exit: If True, exit early on opposite signal.
        atr_values: Optional ATR stream, one per bar. Must be provided
            together with `atr_trail_k` to enable the trailing stop.
            None entries disable the stop check for that specific bar
            (the stop holds its last known level).
        atr_trail_k: ATR multiplier for the trailing stop distance.
            Must be > 0 if atr_values is provided. Exit fires at
            bar[j+1].open when the stop is breached; exit_reason is
            "atr_trail_long" or "atr_trail_short".

    Returns:
        V2BacktestResult with trades, per-bar equity curve, and metrics.
    """
    n = len(bars)
    if len(signals) != n:
        raise ValueError(
            f"signals length {len(signals)} != bars length {n}"
        )
    if len(funding_per_bar) != n:
        raise ValueError(
            f"funding_per_bar length {len(funding_per_bar)} != bars length {n}"
        )
    if hold_bars <= 0:
        raise ValueError(f"hold_bars must be > 0, got {hold_bars}")
    if cooldown_bars < 0:
        raise ValueError(f"cooldown_bars must be >= 0, got {cooldown_bars}")

    # ATR trailing stop validation
    atr_enabled = atr_values is not None
    if atr_enabled:
        if atr_trail_k is None or atr_trail_k <= 0:
            raise ValueError(
                f"atr_trail_k must be > 0 when atr_values is provided, got {atr_trail_k}"
            )
        if len(atr_values) != n:
            raise ValueError(
                f"atr_values length {len(atr_values)} != bars length {n}"
            )
    elif atr_trail_k is not None:
        # k without values is silently ignored (but we don't raise)
        pass

    # Stop-loss validation
    stop_loss_enabled = stop_loss_pct is not None
    if stop_loss_enabled:
        if stop_loss_pct <= 0 or stop_loss_pct >= 1:
            raise ValueError(
                f"stop_loss_pct must be in (0, 1), got {stop_loss_pct}"
            )
        if stop_trigger not in ("close", "wick"):
            raise ValueError(
                f"stop_trigger must be 'close' or 'wick', got {stop_trigger!r}"
            )
    if stop_slip_pct < 0:
        raise ValueError(
            f"stop_slip_pct must be >= 0, got {stop_slip_pct}"
        )
    if stop_semantics is not None and stop_semantics not in (
        "strategy_close_stop",
        "exchange_intrabar_stop",
    ):
        raise ValueError(
            f"stop_semantics must be 'strategy_close_stop' or "
            f"'exchange_intrabar_stop', got {stop_semantics!r}"
        )

    # Dual-stop architecture validation (Phase 8 aggressive)
    dual_stop_enabled = (
        alpha_stop_pct is not None or catastrophe_stop_pct is not None
    )
    if dual_stop_enabled:
        if alpha_stop_pct is not None and (
            alpha_stop_pct <= 0 or alpha_stop_pct >= 1
        ):
            raise ValueError(
                f"alpha_stop_pct must be in (0, 1), got {alpha_stop_pct}"
            )
        if catastrophe_stop_pct is not None and (
            catastrophe_stop_pct <= 0 or catastrophe_stop_pct >= 1
        ):
            raise ValueError(
                f"catastrophe_stop_pct must be in (0, 1), "
                f"got {catastrophe_stop_pct}"
            )
        if (
            alpha_stop_pct is not None
            and catastrophe_stop_pct is not None
            and catastrophe_stop_pct <= alpha_stop_pct
        ):
            raise ValueError(
                f"catastrophe_stop_pct {catastrophe_stop_pct} must be > "
                f"alpha_stop_pct {alpha_stop_pct} (catastrophe is the "
                f"wider tail backstop, alpha is the tight normal stop)"
            )
        if dual_stop_enabled and stop_loss_pct is not None:
            raise ValueError(
                "Cannot mix dual-stop (alpha/catastrophe) with legacy "
                "stop_loss_pct. Pick one mode."
            )
    if catastrophe_slip_pct < 0:
        raise ValueError(
            f"catastrophe_slip_pct must be >= 0, got {catastrophe_slip_pct}"
        )

    # Position sizing validation
    if risk_per_trade is not None:
        if stop_loss_pct is None and alpha_stop_pct is None:
            raise ValueError(
                "risk_per_trade requires stop_loss_pct or alpha_stop_pct "
                "to compute position size"
            )
        if risk_per_trade <= 0 or risk_per_trade >= 1:
            raise ValueError(
                f"risk_per_trade must be in (0, 1), got {risk_per_trade}"
            )

    if effective_leverage is not None:
        if effective_leverage <= 0:
            raise ValueError(
                f"effective_leverage must be > 0, got {effective_leverage}"
            )

    # Compute default position_frac (fraction of equity used per trade).
    # In dual-stop mode, size against the alpha (tight) stop, not the
    # catastrophe (wide) stop — the alpha stop is the "normal" loss size.
    if risk_per_trade is not None:
        sizing_stop_pct = (
            alpha_stop_pct if alpha_stop_pct is not None else stop_loss_pct
        )
        default_position_frac = risk_per_trade / sizing_stop_pct
    else:
        default_position_frac = 1.0
    if effective_leverage is not None and default_position_frac > effective_leverage:
        default_position_frac = effective_leverage

    # Validate per-signal overrides (manual_edge_extraction branch)
    if position_frac_override is not None:
        if len(position_frac_override) != n:
            raise ValueError(
                f"position_frac_override length {len(position_frac_override)} "
                f"!= bars length {n}"
            )
        for v in position_frac_override:
            if v is not None and v < 0:
                raise ValueError(
                    f"position_frac_override entries must be >= 0 or None, "
                    f"got {v}"
                )
    if hold_bars_override is not None:
        if len(hold_bars_override) != n:
            raise ValueError(
                f"hold_bars_override length {len(hold_bars_override)} "
                f"!= bars length {n}"
            )
        for v in hold_bars_override:
            if v is not None and v <= 0:
                raise ValueError(
                    f"hold_bars_override entries must be > 0 or None, "
                    f"got {v}"
                )

    round_trip_cost = 2.0 * (fee_per_side + slip_per_side)

    trades: list[V2Trade] = []
    i = 0

    while i < n - 1:
        sig = signals[i]
        if sig == 0:
            i += 1
            continue

        # Resolve per-trade position_frac (override wins over default).
        if position_frac_override is not None and position_frac_override[i] is not None:
            trade_position_frac = position_frac_override[i]
        else:
            trade_position_frac = default_position_frac

        # frac=0 is the "skip this trade" convention.
        if trade_position_frac == 0:
            i += 1
            continue

        # Resolve per-trade hold_bars (override wins over default).
        if hold_bars_override is not None and hold_bars_override[i] is not None:
            trade_hold_bars = hold_bars_override[i]
        else:
            trade_hold_bars = hold_bars

        side = 1 if sig > 0 else -1
        entry_idx = i + 1
        if entry_idx >= n:
            break  # no room for entry bar

        entry_price = bars[entry_idx].open
        entry_time = bars[entry_idx].timestamp

        intended_exit_idx = entry_idx + trade_hold_bars
        actual_exit_idx = intended_exit_idx
        exit_reason = "time_stop"

        # Unified exit scan: ATR trailing stop AND opposite flip are both
        # evaluated bar-by-bar; whichever fires first wins.
        scan_hi = min(intended_exit_idx, n)

        # Initialise ATR trailing state if enabled
        if atr_enabled:
            if side > 0:
                high_water = entry_price
                # Initial stop level uses the ATR at the entry bar if available,
                # otherwise defer to the first bar that has one.
                init_atr = atr_values[entry_idx] if entry_idx < n else None
                trail_stop: float | None = (
                    entry_price - atr_trail_k * init_atr
                    if init_atr is not None
                    else None
                )
            else:
                low_water = entry_price
                init_atr = atr_values[entry_idx] if entry_idx < n else None
                trail_stop = (
                    entry_price + atr_trail_k * init_atr
                    if init_atr is not None
                    else None
                )
        else:
            trail_stop = None
            high_water = entry_price
            low_water = entry_price

        # Fixed stop-loss level (computed once at entry)
        if stop_loss_enabled:
            if side > 0:
                fixed_stop_level = entry_price * (1 - stop_loss_pct)
            else:
                fixed_stop_level = entry_price * (1 + stop_loss_pct)
        else:
            fixed_stop_level = None

        # Dual-stop levels (Phase 8 aggressive) computed once at entry
        alpha_level: float | None = None
        catastrophe_level: float | None = None
        if dual_stop_enabled:
            if alpha_stop_pct is not None:
                if side > 0:
                    alpha_level = entry_price * (1 - alpha_stop_pct)
                else:
                    alpha_level = entry_price * (1 + alpha_stop_pct)
            if catastrophe_stop_pct is not None:
                if side > 0:
                    catastrophe_level = entry_price * (1 - catastrophe_stop_pct)
                else:
                    catastrophe_level = entry_price * (1 + catastrophe_stop_pct)

        # Effective trigger mode — semantics (if set) overrides the
        # legacy stop_trigger param.
        if stop_semantics == "strategy_close_stop":
            effective_trigger = "close"
        elif stop_semantics == "exchange_intrabar_stop":
            effective_trigger = "wick"
        else:
            effective_trigger = stop_trigger

        for j in range(entry_idx, scan_hi):
            bar_j = bars[j]

            # ── 1a. DUAL-STOP: catastrophe (intrabar wick, highest priority)
            if dual_stop_enabled and catastrophe_level is not None:
                if side > 0:
                    if bar_j.low <= catastrophe_level:
                        actual_exit_idx = j + 1
                        exit_reason = "catastrophe_stop_long"
                        break
                else:
                    if bar_j.high >= catastrophe_level:
                        actual_exit_idx = j + 1
                        exit_reason = "catastrophe_stop_short"
                        break

            # ── 1b. DUAL-STOP: alpha (close-trigger, fill next open)
            if dual_stop_enabled and alpha_level is not None:
                if side > 0:
                    if bar_j.close <= alpha_level:
                        actual_exit_idx = j + 1
                        exit_reason = "alpha_stop_long"
                        break
                else:
                    if bar_j.close >= alpha_level:
                        actual_exit_idx = j + 1
                        exit_reason = "alpha_stop_short"
                        break

            # ── 1c. LEGACY single stop (only runs if dual-stop is off)
            if not dual_stop_enabled and stop_loss_enabled:
                if side > 0:
                    breach_price = bar_j.low if effective_trigger == "wick" else bar_j.close
                    if breach_price <= fixed_stop_level:
                        actual_exit_idx = j + 1
                        exit_reason = "stop_loss_long"
                        break
                else:
                    breach_price = bar_j.high if effective_trigger == "wick" else bar_j.close
                    if breach_price >= fixed_stop_level:
                        actual_exit_idx = j + 1
                        exit_reason = "stop_loss_short"
                        break

            # ── 2. Opposite-flip check (bar j close → exit at j+1)
            if allow_opposite_flip_exit:
                flip = signals[j]
                if flip != 0 and ((flip > 0) != (side > 0)):
                    actual_exit_idx = j + 1
                    exit_reason = "opposite_flip"
                    break

            # ── 3. ATR trailing stop check. Convention: we check the *prior*
            # bar's stop against this bar's extremes BEFORE updating the stop
            # with this bar's high/low. That models "the stop was set at last
            # bar close; this bar's intra-bar movement either hits it or
            # doesn't." Then we update the high/low water mark and recompute
            # the stop for the next iteration.
            if atr_enabled:
                bar_j = bars[j]
                if side > 0:
                    if trail_stop is not None and bar_j.low <= trail_stop:
                        actual_exit_idx = j + 1
                        exit_reason = "atr_trail_long"
                        break
                    # Update for next iteration
                    if bar_j.high > high_water:
                        high_water = bar_j.high
                    atr_j = atr_values[j]
                    if atr_j is not None:
                        candidate = high_water - atr_trail_k * atr_j
                        if trail_stop is None or candidate > trail_stop:
                            trail_stop = candidate
                else:
                    if trail_stop is not None and bar_j.high >= trail_stop:
                        actual_exit_idx = j + 1
                        exit_reason = "atr_trail_short"
                        break
                    if bar_j.low < low_water:
                        low_water = bar_j.low
                    atr_j = atr_values[j]
                    if atr_j is not None:
                        candidate = low_water + atr_trail_k * atr_j
                        if trail_stop is None or candidate < trail_stop:
                            trail_stop = candidate

        # Truncate at series end.
        truncated = False
        if actual_exit_idx >= n:
            actual_exit_idx = n - 1
            truncated = True

        if truncated:
            exit_price = bars[actual_exit_idx].close
            if exit_reason == "time_stop":
                exit_reason = "end_of_series"
            else:
                exit_reason = f"{exit_reason}_end_of_series"
        else:
            exit_price = bars[actual_exit_idx].open

        # Phase 7: exchange_intrabar_stop fills AT the stop level
        # (approximating a resting exchange stop order), not at the
        # next-bar open. Only applies when the exit is a stop_loss.
        if (
            stop_semantics == "exchange_intrabar_stop"
            and exit_reason.startswith("stop_loss")
            and fixed_stop_level is not None
        ):
            exit_price = fixed_stop_level

        # Phase 8 aggressive: dual-stop fill semantics.
        #   catastrophe_stop_* → fills AT the catastrophe level
        #       (exchange resting stop, intrabar)
        #   alpha_stop_*       → fills at next-bar open (already set above)
        if (
            exit_reason.startswith("catastrophe_stop")
            and catastrophe_level is not None
        ):
            exit_price = catastrophe_level
            # Catastrophe slippage: wider tail fill, typically 0.2-1%
            if catastrophe_slip_pct > 0:
                if side > 0:
                    exit_price = exit_price * (1 - catastrophe_slip_pct)
                else:
                    exit_price = exit_price * (1 + catastrophe_slip_pct)

        # Stop-fill slippage — applied to ALL stop-type exits.
        # stop_slip_pct is the legacy + alpha slippage parameter.
        # Catastrophe uses its own catastrophe_slip_pct applied above.
        if (
            stop_slip_pct > 0
            and (
                exit_reason.startswith("stop_loss")
                or exit_reason.startswith("alpha_stop")
            )
        ):
            if side > 0:
                exit_price = exit_price * (1 - stop_slip_pct)
            else:
                exit_price = exit_price * (1 + stop_slip_pct)

        exit_time = bars[actual_exit_idx].timestamp
        hold = actual_exit_idx - entry_idx

        raw_gross = (exit_price - entry_price) / entry_price * side
        gross_pnl = raw_gross * trade_position_frac
        funding_sum = sum(
            funding_per_bar[k] for k in range(entry_idx, actual_exit_idx)
        )
        funding_pnl = -side * funding_sum * trade_position_frac
        trade_cost = round_trip_cost * trade_position_frac
        net_pnl = gross_pnl + funding_pnl - trade_cost

        trades.append(
            V2Trade(
                entry_idx=entry_idx,
                entry_time=entry_time,
                entry_price=entry_price,
                exit_idx=actual_exit_idx,
                exit_time=exit_time,
                exit_price=exit_price,
                side=side,
                hold_bars=hold,
                gross_pnl=gross_pnl,
                funding_pnl=funding_pnl,
                cost=trade_cost,
                net_pnl=net_pnl,
                exit_reason=exit_reason,
            )
        )

        # Advance past exit + cooldown.
        next_i = actual_exit_idx + cooldown_bars
        if next_i <= i:
            # Safety guard — should not fire on sane inputs.
            next_i = i + 1
        i = next_i

    equity_curve = _build_equity_curve(trades, n)
    metrics = _compute_v2_metrics(trades, equity_curve, total_bars=n)

    return V2BacktestResult(
        trades=trades,
        equity_curve=equity_curve,
        metrics=metrics,
    )


# ── helpers ──────────────────────────────────────────────────────────


def _build_equity_curve(trades: list[V2Trade], n: int) -> list[float]:
    """Step-function compounded equity, one point per bar.

    Equity multiplies by (1 + net_pnl) at each trade's exit_idx and stays
    flat elsewhere. Trades must be sorted by exit_idx (which they are by
    construction from run_v2_backtest).
    """
    if n == 0:
        return []

    curve = [1.0] * n
    equity = 1.0
    trade_pos = 0

    for k in range(n):
        while trade_pos < len(trades) and trades[trade_pos].exit_idx <= k:
            equity *= 1.0 + trades[trade_pos].net_pnl
            trade_pos += 1
        curve[k] = equity

    return curve


def _compute_v2_metrics(
    trades: list[V2Trade],
    equity_curve: list[float],
    *,
    total_bars: int,
) -> dict[str, float]:
    """Produce the 12-metric dict shared across the v2 leaderboard."""
    n = len(trades)

    if n == 0:
        final_eq = equity_curve[-1] if equity_curve else 1.0
        return {
            "num_trades": 0.0,
            "net_pnl": 0.0,
            "compounded_return": final_eq - 1.0,
            "profit_factor": 0.0,
            "win_rate": 0.0,
            "avg_pnl": 0.0,
            "trade_sharpe": 0.0,
            "trade_sortino": 0.0,
            "max_dd": 0.0,
            "turnover": 0.0,
            "avg_hold_bars": 0.0,
            "exposure_time": 0.0,
        }

    rets = [t.net_pnl for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r < 0]

    net_pnl = sum(rets)
    compounded_return = equity_curve[-1] - 1.0 if equity_curve else 0.0

    gross_win = sum(wins)
    gross_loss = sum(losses)
    if gross_loss < 0:
        profit_factor = gross_win / abs(gross_loss)
    elif gross_win > 0:
        profit_factor = NO_LOSS_PROFIT_FACTOR
    else:
        profit_factor = 0.0

    win_rate = len(wins) / n
    avg_pnl = net_pnl / n

    # Trade-level Sharpe (sample std, ddof=1).
    if n > 1:
        mean = net_pnl / n
        var = sum((r - mean) ** 2 for r in rets) / (n - 1)
        std = sqrt(var) if var > 0 else 0.0
        trade_sharpe = mean / std if std > 0 else 0.0
    else:
        trade_sharpe = 0.0

    # Trade-level Sortino (downside deviation vs MAR=0).
    if n > 1 and losses:
        mean = net_pnl / n
        down_sq = sum(min(r, 0.0) ** 2 for r in rets) / n
        down_std = sqrt(down_sq)
        trade_sortino = mean / down_std if down_std > 0 else 0.0
    elif n >= 1 and not losses:
        # Every trade is a non-loss → infinite-like Sortino; reuse sentinel.
        trade_sortino = NO_LOSS_PROFIT_FACTOR
    else:
        trade_sortino = 0.0

    # Max drawdown on the compounded equity curve.
    peak = equity_curve[0] if equity_curve else 1.0
    max_dd = 0.0
    for e in equity_curve:
        if e > peak:
            peak = e
        if peak > 0:
            dd = (peak - e) / peak
            if dd > max_dd:
                max_dd = dd

    total_hold = sum(t.hold_bars for t in trades)
    exposure_time = total_hold / total_bars if total_bars > 0 else 0.0

    return {
        "num_trades": float(n),
        "net_pnl": net_pnl,
        "compounded_return": compounded_return,
        "profit_factor": profit_factor,
        "win_rate": win_rate,
        "avg_pnl": avg_pnl,
        "trade_sharpe": trade_sharpe,
        "trade_sortino": trade_sortino,
        "max_dd": max_dd,
        "turnover": float(n),
        "avg_hold_bars": total_hold / n,
        "exposure_time": exposure_time,
    }
