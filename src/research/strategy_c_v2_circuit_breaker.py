"""Strategy C v2 Phase 8B — circuit-breaker study.

Two breaker types, tested independently:

1. **Adverse-move breaker** — if the price moves X% against the
   entry during an open trade (checked on intrabar resolution),
   exit immediately at the breaker level. This is tighter than the
   catastrophe stop and acts as a hard circuit breaker independent
   of the alpha/catastrophe stop architecture.

2. **Equity-DD breaker** — if the running account equity drops X%
   from its peak (across all trades, not per-trade), flatten
   immediately. This is a portfolio-level kill switch.

Both breakers use **intrabar replay**: during each open 4h trade,
we walk the 1h or 15m bars to find the earliest moment the breaker
would have fired. This avoids the false safety of 4h-bar
resolution where a 15% intrabar spike is hidden inside a bar whose
close looks normal.

Usage:
    trades_4h = run_v2_backtest(...)  # Row 4 config
    bars_1h = load_klines_csv("src/data/btcusdt_1h_6year.csv")
    result = run_breaker_study(
        trades_4h=trades_4h,
        bars_4h=bars_4h,
        bars_hires=bars_1h,
        breaker_pcts=[0.08, 0.10, 0.12, 0.15],
        exchange_leverage=4.0,
    )
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

from adapters.base import MarketBar
from research.strategy_c_v2_backtest import V2Trade


@dataclass(frozen=True)
class BreakerEvent:
    """One circuit-breaker firing during a trade."""
    trade_index: int
    breaker_type: str           # "adverse_move" or "equity_dd"
    breaker_threshold: float    # e.g. 0.10 for 10%
    fired_at: datetime
    fired_price: float          # price that triggered the breaker
    adverse_move_at_fire: float # actual adverse move when breaker fired
    entry_price: float
    original_exit_price: float
    original_exit_reason: str
    pnl_with_breaker: float     # net PnL if exited at breaker level
    pnl_without_breaker: float  # original net PnL


@dataclass(frozen=True)
class BreakerStudyResult:
    """Result of running one breaker threshold on a set of trades."""
    breaker_threshold: float
    breaker_type: str
    num_trades: int
    breaker_fires: int
    breaker_fire_rate: float
    # Metrics WITH breaker applied
    oos_return_with: float
    max_dd_with: float
    worst_trade_with: float
    pf_with: float
    win_count_with: int
    win_rate_with: float
    # Metrics WITHOUT breaker (original)
    oos_return_without: float
    max_dd_without: float
    worst_trade_without: float
    pf_without: float
    win_rate_without: float
    # Events
    events: tuple[BreakerEvent, ...]


def _build_timestamp_index(bars: Sequence[MarketBar]) -> dict[datetime, int]:
    """Map timestamps to indices for fast lookup."""
    return {b.timestamp: i for i, b in enumerate(bars)}


def _find_hires_range(
    bars_hires: Sequence[MarketBar],
    ts_index: dict[datetime, int],
    entry_time: datetime,
    exit_time: datetime,
) -> tuple[int, int]:
    """Find the hi-res bar index range [lo, hi) covering [entry_time, exit_time).

    Falls back to binary search on timestamps if exact match not found.
    """
    timestamps = [b.timestamp for b in bars_hires]
    lo = bisect_left(timestamps, entry_time)
    hi = bisect_right(timestamps, exit_time)
    return lo, hi


def _compute_max_adverse_intrabar(
    bars_hires: Sequence[MarketBar],
    lo: int,
    hi: int,
    entry_price: float,
    side: int,
) -> tuple[float, int, float]:
    """Walk hi-res bars and find the max adverse excursion.

    Returns (max_adverse_pct, bar_index, price_at_max).
    """
    max_adv = 0.0
    max_idx = lo
    max_price = entry_price
    for i in range(lo, min(hi, len(bars_hires))):
        bar = bars_hires[i]
        if side > 0:
            adv = (entry_price - bar.low) / entry_price
            price = bar.low
        else:
            adv = (bar.high - entry_price) / entry_price
            price = bar.high
        if adv > max_adv:
            max_adv = adv
            max_idx = i
            max_price = price
    return max_adv, max_idx, max_price


def _find_breaker_fire_time(
    bars_hires: Sequence[MarketBar],
    lo: int,
    hi: int,
    entry_price: float,
    side: int,
    breaker_pct: float,
) -> tuple[bool, int, float]:
    """Walk hi-res bars and find the FIRST bar where adverse move >= breaker_pct.

    Returns (fired, bar_index, price_at_fire).
    """
    for i in range(lo, min(hi, len(bars_hires))):
        bar = bars_hires[i]
        if side > 0:
            adv = (entry_price - bar.low) / entry_price
            price = bar.low
        else:
            adv = (bar.high - entry_price) / entry_price
            price = bar.high
        if adv >= breaker_pct:
            # Breaker fires — compute the fill price at the breaker level
            if side > 0:
                fill = entry_price * (1.0 - breaker_pct)
            else:
                fill = entry_price * (1.0 + breaker_pct)
            return True, i, fill
    return False, -1, 0.0


def _compute_trade_pnl(
    entry_price: float,
    exit_price: float,
    side: int,
    position_frac: float,
    round_trip_cost: float,
) -> float:
    """Compute net PnL for a single trade."""
    raw_gross = (exit_price - entry_price) / entry_price * side
    gross = raw_gross * position_frac
    cost = round_trip_cost * position_frac
    return gross - cost


def _build_equity_curve(pnls: list[float]) -> list[float]:
    """Build a compounded equity curve from per-trade PnLs."""
    curve = [1.0]
    eq = 1.0
    for p in pnls:
        eq *= (1.0 + p)
        curve.append(eq)
    return curve


def _max_dd_from_curve(curve: list[float]) -> float:
    if not curve:
        return 0.0
    peak = curve[0]
    dd = 0.0
    for e in curve:
        if e > peak:
            peak = e
        if peak > 0:
            d = (peak - e) / peak
            if d > dd:
                dd = d
    return dd


def _profit_factor(pnls: list[float]) -> float:
    wins = sum(p for p in pnls if p > 0)
    losses = sum(p for p in pnls if p < 0)
    if losses < 0:
        return wins / abs(losses)
    elif wins > 0:
        return 9999.0
    return 0.0


def run_adverse_move_breaker(
    *,
    trades_4h: list[V2Trade],
    bars_4h: Sequence[MarketBar],
    bars_hires: Sequence[MarketBar],
    breaker_pct: float,
    position_fracs: list[float],
    round_trip_cost: float = 0.0012,
) -> BreakerStudyResult:
    """Run the adverse-move breaker study on Row 4 trades.

    For each trade, walk the hi-res bars during the trade's holding
    period. If the adverse move reaches `breaker_pct`, record a
    breaker fire and compute the alternative PnL.

    Args:
        trades_4h: V2Trade list from run_v2_backtest.
        bars_4h: The 4h bar stream (used for timestamp alignment).
        bars_hires: 1h or 15m bar stream (used for intrabar replay).
        breaker_pct: The adverse-move threshold (e.g., 0.10 for 10%).
        position_fracs: Per-trade actual_frac (same order as trades_4h).
        round_trip_cost: Round-trip cost per unit of frac.

    Returns:
        BreakerStudyResult with metrics both WITH and WITHOUT the
        breaker, plus the list of breaker events.
    """
    hires_timestamps = [b.timestamp for b in bars_hires]
    events: list[BreakerEvent] = []
    pnls_with: list[float] = []
    pnls_without: list[float] = []

    for ti, trade in enumerate(trades_4h):
        frac = position_fracs[ti] if ti < len(position_fracs) else 1.0
        pnl_orig = trade.net_pnl
        pnls_without.append(pnl_orig)

        # Skip trades already exited by alpha/catastrophe stop.
        # The position was closed intrabar at the stop level;
        # scanning 1h bars AFTER the stop fired would include
        # post-exit price action and produce phantom breaker fires.
        if trade.exit_reason.startswith(
            ("alpha_stop", "catastrophe_stop", "stop_loss")
        ):
            pnls_with.append(pnl_orig)
            continue

        # Find hi-res bar range for this trade
        lo = bisect_left(hires_timestamps, trade.entry_time)
        hi = bisect_right(hires_timestamps, trade.exit_time)

        fired, fire_idx, fill_price = _find_breaker_fire_time(
            bars_hires, lo, hi,
            trade.entry_price, trade.side,
            breaker_pct,
        )

        if fired:
            pnl_breaker = _compute_trade_pnl(
                trade.entry_price, fill_price,
                trade.side, frac, round_trip_cost,
            )
            pnls_with.append(pnl_breaker)
            events.append(BreakerEvent(
                trade_index=ti,
                breaker_type="adverse_move",
                breaker_threshold=breaker_pct,
                fired_at=bars_hires[fire_idx].timestamp,
                fired_price=fill_price,
                adverse_move_at_fire=breaker_pct,
                entry_price=trade.entry_price,
                original_exit_price=trade.exit_price,
                original_exit_reason=trade.exit_reason,
                pnl_with_breaker=pnl_breaker,
                pnl_without_breaker=pnl_orig,
            ))
        else:
            pnls_with.append(pnl_orig)

    # Compute metrics for both paths
    curve_with = _build_equity_curve(pnls_with)
    curve_without = _build_equity_curve(pnls_without)

    num_trades = len(trades_4h)
    wins_with = sum(1 for p in pnls_with if p > 0)
    wins_without = sum(1 for p in pnls_without if p > 0)

    return BreakerStudyResult(
        breaker_threshold=breaker_pct,
        breaker_type="adverse_move",
        num_trades=num_trades,
        breaker_fires=len(events),
        breaker_fire_rate=len(events) / num_trades if num_trades else 0.0,
        oos_return_with=(curve_with[-1] - 1.0) if curve_with else 0.0,
        max_dd_with=_max_dd_from_curve(curve_with),
        worst_trade_with=min(pnls_with) if pnls_with else 0.0,
        pf_with=_profit_factor(pnls_with),
        win_count_with=wins_with,
        win_rate_with=wins_with / num_trades if num_trades else 0.0,
        oos_return_without=(curve_without[-1] - 1.0) if curve_without else 0.0,
        max_dd_without=_max_dd_from_curve(curve_without),
        worst_trade_without=min(pnls_without) if pnls_without else 0.0,
        pf_without=_profit_factor(pnls_without),
        win_rate_without=wins_without / num_trades if num_trades else 0.0,
        events=tuple(events),
    )


def run_equity_dd_breaker(
    *,
    trades_4h: list[V2Trade],
    bars_4h: Sequence[MarketBar],
    bars_hires: Sequence[MarketBar],
    breaker_pct: float,
    position_fracs: list[float],
    round_trip_cost: float = 0.0012,
) -> BreakerStudyResult:
    """Run the equity-DD breaker study.

    The equity-DD breaker fires if running account equity drops
    `breaker_pct` from its peak at any point during a trade
    (checked on hi-res bars). When it fires, the current trade
    is closed at the estimated mark-to-market price and all
    subsequent trades are skipped (the system is "killed").

    This is more drastic than per-trade adverse-move breaker:
    it shuts down the entire strategy after a bad sequence.
    """
    hires_timestamps = [b.timestamp for b in bars_hires]
    events: list[BreakerEvent] = []
    pnls_with: list[float] = []
    pnls_without: list[float] = []

    equity = 1.0
    peak_equity = 1.0
    killed = False

    for ti, trade in enumerate(trades_4h):
        frac = position_fracs[ti] if ti < len(position_fracs) else 1.0
        pnl_orig = trade.net_pnl
        pnls_without.append(pnl_orig)

        if killed:
            # After kill switch, all subsequent trades are skipped
            pnls_with.append(0.0)
            continue

        # Walk hi-res bars during this trade to check equity DD
        lo = bisect_left(hires_timestamps, trade.entry_time)
        hi = bisect_right(hires_timestamps, trade.exit_time)

        breaker_fired = False
        for i in range(lo, min(hi, len(bars_hires))):
            bar = bars_hires[i]
            # Estimate unrealized PnL at this bar
            if trade.side > 0:
                mtm = (bar.low - trade.entry_price) / trade.entry_price * frac
            else:
                mtm = (trade.entry_price - bar.high) / trade.entry_price * frac
            current_equity = equity * (1.0 + mtm)
            if current_equity > peak_equity:
                peak_equity = current_equity
            dd = (peak_equity - current_equity) / peak_equity if peak_equity > 0 else 0.0
            if dd >= breaker_pct:
                # Equity-DD breaker fires
                if trade.side > 0:
                    fill_price = bar.low
                else:
                    fill_price = bar.high
                pnl_breaker = _compute_trade_pnl(
                    trade.entry_price, fill_price,
                    trade.side, frac, round_trip_cost,
                )
                pnls_with.append(pnl_breaker)
                events.append(BreakerEvent(
                    trade_index=ti,
                    breaker_type="equity_dd",
                    breaker_threshold=breaker_pct,
                    fired_at=bar.timestamp,
                    fired_price=fill_price,
                    adverse_move_at_fire=dd,
                    entry_price=trade.entry_price,
                    original_exit_price=trade.exit_price,
                    original_exit_reason=trade.exit_reason,
                    pnl_with_breaker=pnl_breaker,
                    pnl_without_breaker=pnl_orig,
                ))
                equity *= (1.0 + pnl_breaker)
                killed = True
                breaker_fired = True
                break

        if not breaker_fired:
            pnls_with.append(pnl_orig)
            equity *= (1.0 + pnl_orig)
            if equity > peak_equity:
                peak_equity = equity

    num_trades = len(trades_4h)
    curve_with = _build_equity_curve(pnls_with)
    curve_without = _build_equity_curve(pnls_without)
    wins_with = sum(1 for p in pnls_with if p > 0)
    wins_without = sum(1 for p in pnls_without if p > 0)

    return BreakerStudyResult(
        breaker_threshold=breaker_pct,
        breaker_type="equity_dd",
        num_trades=num_trades,
        breaker_fires=len(events),
        breaker_fire_rate=len(events) / num_trades if num_trades else 0.0,
        oos_return_with=(curve_with[-1] - 1.0) if curve_with else 0.0,
        max_dd_with=_max_dd_from_curve(curve_with),
        worst_trade_with=min(pnls_with) if pnls_with else 0.0,
        pf_with=_profit_factor(pnls_with),
        win_count_with=wins_with,
        win_rate_with=wins_with / num_trades if num_trades else 0.0,
        oos_return_without=(curve_without[-1] - 1.0) if curve_without else 0.0,
        max_dd_without=_max_dd_from_curve(curve_without),
        worst_trade_without=min(pnls_without) if pnls_without else 0.0,
        pf_without=_profit_factor(pnls_without),
        win_rate_without=wins_without / num_trades if num_trades else 0.0,
        events=tuple(events),
    )
