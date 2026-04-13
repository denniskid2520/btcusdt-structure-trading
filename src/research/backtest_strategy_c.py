"""Strategy C — minimal backtest engine for Baseline A.

Execution model (honest, minimal):
    - Signal generated at bar[t].close
    - Entry at bar[t+1].open
    - Hold for `hold_bars` bars OR exit when an opposite signal arrives
    - Exit at bar[t+1+hold_bars].open (time stop)
        OR bar[j+1].open where j is the first bar inside the hold with an
        opposite signal
    - No position stacking: signals during an open position are ignored
    - Fees and slippage deducted on each side

Metrics: net_pnl, compounded_return, profit_factor, trade_sharpe, trade_sortino,
max_dd, num_trades, win_rate, avg_pnl, turnover, avg_hold_bars, exposure_time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

from data.strategy_c_features import StrategyCFeatureBar


@dataclass(frozen=True)
class Trade:
    entry_ts: datetime
    exit_ts: datetime
    side: int           # +1 long, -1 short
    entry_px: float
    exit_px: float
    pnl_ret: float      # raw return before costs
    pnl_net: float      # return after fees + slippage
    hold_bars: int


@dataclass(frozen=True)
class BacktestResult:
    trades: list[Trade]
    equity_curve: list[float]  # 1.0 after trade 0, product of (1 + pnl_net)
    metrics: dict[str, float]


def run_strategy_c_backtest(
    feats: Sequence[StrategyCFeatureBar],
    signals: Sequence[int],
    *,
    hold_bars: int = 3,
    cooldown_bars: int = 0,
    fee_per_side: float = 0.0005,       # 0.05% Binance USDT-M taker
    slippage_per_side: float = 0.0001,  # 1 bp
) -> BacktestResult:
    """Simulate Strategy C trade-by-trade and aggregate metrics.

    Args:
        feats: StrategyCFeatureBar series, ascending timestamps.
        signals: Parallel integer list — +1/-1/0 per bar.
        hold_bars: Maximum bars to hold a position before time-stop.
        cooldown_bars: After an exit, ignore new signals for this many bars.
            0 (default) = back-to-back trading allowed.
        fee_per_side: Flat per-side taker fee (e.g. 0.0005 = 5 bps).
        slippage_per_side: Flat per-side slippage (e.g. 0.0001 = 1 bp).

    Returns:
        BacktestResult with trades, equity curve, and metrics dict.
    """
    assert len(feats) == len(signals), "feats and signals must be parallel"
    assert cooldown_bars >= 0, "cooldown_bars must be non-negative"
    n = len(feats)

    trades: list[Trade] = []
    i = 0
    while i < n - 1:
        sig = signals[i]
        if sig == 0:
            i += 1
            continue

        # Entry at next bar's open.
        entry_idx = i + 1
        if entry_idx >= n:
            break
        entry_ts = feats[entry_idx].timestamp
        entry_px = feats[entry_idx].open

        # Find exit: opposite signal inside hold window, else time stop.
        exit_idx: int | None = None
        scan_end = min(i + 1 + hold_bars, n - 1)  # last bar we can STILL exit from
        for j in range(i + 1, scan_end + 1):
            # If an opposite signal arrives at bar j, we exit at bar j+1 open.
            if signals[j] == -sig and (j + 1) < n:
                exit_idx = j + 1
                break

        if exit_idx is None:
            # Time stop: exit at bar (i + 1 + hold_bars) open.
            exit_idx = i + 1 + hold_bars
            if exit_idx >= n:
                # Not enough data to close the trade — abort it.
                break

        exit_ts = feats[exit_idx].timestamp
        exit_px = feats[exit_idx].open

        pnl_ret = sig * (exit_px - entry_px) / entry_px
        roundtrip_cost = 2 * (fee_per_side + slippage_per_side)
        pnl_net = pnl_ret - roundtrip_cost
        hold = exit_idx - entry_idx

        trades.append(
            Trade(
                entry_ts=entry_ts,
                exit_ts=exit_ts,
                side=sig,
                entry_px=entry_px,
                exit_px=exit_px,
                pnl_ret=pnl_ret,
                pnl_net=pnl_net,
                hold_bars=hold,
            )
        )

        # Resume scanning AFTER the exit bar + cooldown (no stacking, no chasing).
        i = exit_idx + cooldown_bars

    equity = _equity_curve(trades)
    metrics = _compute_metrics(trades, equity, total_bars=n)
    return BacktestResult(trades=trades, equity_curve=equity, metrics=metrics)


def _equity_curve(trades: Sequence[Trade]) -> list[float]:
    """Cumulative product of (1 + pnl_net), starting from 1.0."""
    curve = [1.0]
    for t in trades:
        curve.append(curve[-1] * (1.0 + t.pnl_net))
    return curve


NO_LOSS_PROFIT_FACTOR = 9999.0  # sentinel for "no losing trades" — rankable, finite


def _compute_metrics(
    trades: Sequence[Trade],
    equity: Sequence[float],
    *,
    total_bars: int,
) -> dict[str, float]:
    """Return the full Baseline A/B/C metric dict.

    Args:
        trades: Completed trades in chronological order.
        equity: Equity curve starting at 1.0, one entry per trade + 1.
        total_bars: Number of feature bars the signal series was evaluated
            over — used for exposure_time = sum(hold_bars) / total_bars.
    """
    if not trades:
        return {
            "net_pnl": 0.0,
            "compounded_return": 0.0,
            "profit_factor": 0.0,
            "trade_sharpe": 0.0,
            "trade_sortino": 0.0,
            "max_dd": 0.0,
            "num_trades": 0,
            "win_rate": 0.0,
            "avg_pnl": 0.0,
            "turnover": 0.0,
            "avg_hold_bars": 0.0,
            "exposure_time": 0.0,
        }

    n = len(trades)
    rets = [t.pnl_net for t in trades]
    net_pnl = sum(rets)  # simple sum of returns, kept for back-compat
    avg = net_pnl / n
    wins = sum(1 for r in rets if r > 0)
    win_rate = wins / n
    avg_hold = sum(t.hold_bars for t in trades) / n

    # Compounded return from the equity curve's final value (1.0 start).
    compounded_return = equity[-1] - 1.0 if equity else 0.0

    # Profit factor: sum of winning returns / |sum of losing returns|.
    gross_win = sum(r for r in rets if r > 0)
    gross_loss = sum(r for r in rets if r < 0)  # negative number
    if gross_loss < 0:
        profit_factor = gross_win / abs(gross_loss)
    elif gross_win > 0:
        profit_factor = NO_LOSS_PROFIT_FACTOR  # no losses → sentinel
    else:
        profit_factor = 0.0  # all zeros

    # Trade-level sharpe: mean / std (population). Not annualized.
    variance = sum((r - avg) ** 2 for r in rets) / n
    std = variance ** 0.5
    sharpe = avg / std if std > 0 else 0.0

    # Sortino: mean / downside std.
    downside = [r for r in rets if r < 0]
    if downside:
        d_mean = sum(downside) / len(downside)
        d_var = sum((r - d_mean) ** 2 for r in downside) / len(downside)
        d_std = d_var ** 0.5
        sortino = avg / d_std if d_std > 0 else 0.0
    else:
        sortino = 0.0

    # Max drawdown over the equity curve (peak-to-trough, fractional).
    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    # Turnover: each trade is 2 sides of 1x notional → simple proxy = 2 * num_trades.
    turnover = 2.0 * n

    # Exposure time: share of bars the strategy was actually holding a position.
    exposure_time = (
        sum(t.hold_bars for t in trades) / total_bars if total_bars > 0 else 0.0
    )

    return {
        "net_pnl": net_pnl,
        "compounded_return": compounded_return,
        "profit_factor": profit_factor,
        "trade_sharpe": sharpe,
        "trade_sortino": sortino,
        "max_dd": max_dd,
        "num_trades": n,
        "win_rate": win_rate,
        "avg_pnl": avg,
        "turnover": turnover,
        "avg_hold_bars": avg_hold,
        "exposure_time": exposure_time,
    }
