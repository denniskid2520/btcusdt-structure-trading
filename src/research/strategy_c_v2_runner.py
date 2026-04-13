"""Shared helpers for Strategy C v2 walk-forward runners.

Extracted from the Phase 2 runner so Phase 3 sweeps can reuse the data
loading and single-cell execution logic without re-implementing it.

Public surface:
    TimeframeData  — loaded bars + features + funding_per_bar + splits
    load_timeframe_data(tf_name, klines_path, bar_hours, funding_records)
    build_funding_per_bar(bars, records)
    run_cell(name, tf_data, signal_fn, *, hold_bars, ...) -> dict
    stitch_equity(curves)
    combined_profit_factor(pnls)
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Sequence

from adapters.base import MarketBar
from adapters.binance_futures import FundingRateRecord
from data.strategy_c_v2_features import (
    StrategyCV2Features,
    compute_features_v2,
)
from research.strategy_c_v2_backtest import (
    NO_LOSS_PROFIT_FACTOR,
    run_v2_backtest,
)
from research.strategy_c_v2_walk_forward import (
    WalkForwardSplit,
    walk_forward_splits,
)


# ── loaders ──────────────────────────────────────────────────────────


def load_klines_csv(path: str) -> list[MarketBar]:
    bars: list[MarketBar] = []
    with open(path, "r") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            ts = datetime.fromisoformat(row["timestamp"])
            bars.append(
                MarketBar(
                    timestamp=ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
            )
    return bars


def load_funding_csv(path: str) -> list[FundingRateRecord]:
    records: list[FundingRateRecord] = []
    with open(path, "r") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            ts = datetime.fromisoformat(row["timestamp"])
            mark_raw = row["mark_price"]
            mark = float(mark_raw) if mark_raw else None
            records.append(
                FundingRateRecord(
                    timestamp=ts,
                    funding_rate=float(row["funding_rate"]),
                    mark_price=mark,
                )
            )
    return records


def build_funding_per_bar(
    bars: list[MarketBar],
    records: list[FundingRateRecord],
) -> list[float]:
    """Align settlement rates to execution bars for the backtester.

    Non-funding bars carry 0.0. Funding bars carry the matching settlement
    rate. Matches the backtester's "funding charged iff entry_idx ≤ k < exit_idx"
    contract.
    """
    fund_map: dict[datetime, float] = {}
    for r in records:
        key = r.timestamp.replace(minute=0, second=0, microsecond=0)
        fund_map[key] = r.funding_rate

    out = [0.0] * len(bars)
    for i, bar in enumerate(bars):
        ts = bar.timestamp
        if ts.minute == 0 and ts.hour in (0, 8, 16):
            key = ts.replace(minute=0, second=0, microsecond=0)
            rate = fund_map.get(key)
            if rate is not None:
                out[i] = rate
    return out


# ── timeframe bundle ────────────────────────────────────────────────


@dataclass
class TimeframeData:
    """Everything needed to run a sweep on one timeframe."""
    name: str
    bar_hours: float
    bars: list[MarketBar]
    features: list[StrategyCV2Features]
    funding_per_bar: list[float]
    splits: list[WalkForwardSplit]


def load_timeframe_data(
    name: str,
    klines_path: str,
    bar_hours: float,
    funding_records: list[FundingRateRecord],
    *,
    train_months: int = 24,
    test_months: int = 6,
    step_months: int = 6,
) -> TimeframeData:
    bars = load_klines_csv(klines_path)
    features = compute_features_v2(bars, funding_records=funding_records, bar_hours=bar_hours)
    funding_per_bar = build_funding_per_bar(bars, funding_records)
    timestamps = [b.timestamp for b in bars]
    splits = walk_forward_splits(
        timestamps,
        train_months=train_months,
        test_months=test_months,
        step_months=step_months,
    )
    return TimeframeData(
        name=name,
        bar_hours=bar_hours,
        bars=bars,
        features=features,
        funding_per_bar=funding_per_bar,
        splits=splits,
    )


# ── equity stitching + metrics ──────────────────────────────────────


def stitch_equity(per_split_curves: list[list[float]]) -> list[float]:
    """Stitch per-split equity curves into one contiguous compounded curve."""
    combined: list[float] = []
    prev = 1.0
    for curve in per_split_curves:
        for e in curve:
            combined.append(e * prev)
        if combined:
            prev = combined[-1]
    return combined


def max_dd_of(curve: list[float]) -> float:
    if not curve:
        return 0.0
    peak = curve[0]
    max_dd = 0.0
    for e in curve:
        if e > peak:
            peak = e
        if peak > 0:
            dd = (peak - e) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd


def combined_profit_factor(all_pnls: list[float]) -> float:
    wins = sum(r for r in all_pnls if r > 0)
    losses = sum(r for r in all_pnls if r < 0)
    if losses < 0:
        return wins / abs(losses)
    if wins > 0:
        return NO_LOSS_PROFIT_FACTOR
    return 0.0


# ── single-cell runner ──────────────────────────────────────────────


SignalFn = Callable[[Sequence[StrategyCV2Features]], list[int]]


def run_cell(
    name: str,
    tf: TimeframeData,
    signal_fn: SignalFn,
    *,
    hold_bars: int,
    cooldown_bars: int = 0,
    fee_per_side: float = 0.0005,
    slip_per_side: float = 0.0001,
    allow_opposite_flip_exit: bool = True,
    atr_field: str | None = None,
    atr_trail_k: float | None = None,
    stop_loss_pct: float | None = None,
    stop_trigger: str = "wick",
    stop_slip_pct: float = 0.0,
    stop_semantics: str | None = None,
    risk_per_trade: float | None = None,
    effective_leverage: float | None = None,
    extra_fields: dict[str, object] | None = None,
) -> dict:
    """Run one strategy configuration across all walk-forward splits.

    Args:
        name: Human-readable cell name for the results row.
        tf: Loaded TimeframeData bundle.
        signal_fn: Callable that takes a feature slice and returns a same-length
            list of {-1, 0, +1} signals. It must be pure — only read features.
        hold_bars: Backtester time-stop.
        cooldown_bars: Backtester cooldown.
        fee_per_side, slip_per_side: Cost model.
        allow_opposite_flip_exit: Backtester exit policy.
        atr_field: If provided, read this ATR field from each feature row
            (e.g. "atr_14", "atr_30") and wire it into the backtester as
            the ATR trailing stop series. Requires `atr_trail_k`.
        atr_trail_k: ATR multiplier for the trailing stop. Must be > 0 when
            atr_field is supplied.
        extra_fields: Optional extra columns to stamp on the result row
            (e.g. strategy family, parameter values).

    Returns:
        A dict with:
            timeframe, strategy (name), hold_bars,
            num_splits, total_oos_trades,
            agg_compounded_return, combined_max_dd, combined_profit_factor,
            positive_windows_frac, avg_exposure_time, enough_trades,
            total_gross_pnl, total_funding_pnl, total_cost, avg_hold_bars,
            exit_time_stop_frac, exit_opposite_flip_frac, exit_atr_trail_frac,
            plus any fields in `extra_fields`.
    """
    full_signals = signal_fn(tf.features)

    # Optionally build a full ATR series from the features
    full_atr: list[float | None] | None = None
    if atr_field is not None:
        full_atr = [getattr(f, atr_field) for f in tf.features]

    per_split_curves: list[list[float]] = []
    per_split_metrics: list[dict[str, float]] = []
    all_trade_pnls: list[float] = []
    all_trade_gross: list[float] = []
    all_trade_funding: list[float] = []
    all_trade_cost: list[float] = []
    all_trade_hold: list[int] = []
    all_stopped_pnls: list[float] = []  # only stop_loss_* exits
    all_adverse_moves: list[float] = []  # max adverse % per trade, for liq safety
    exit_reason_counts: dict[str, int] = {}
    positive_windows = 0

    for split in tf.splits:
        test_bars = tf.bars[split.test_lo : split.test_hi]
        test_signals = full_signals[split.test_lo : split.test_hi]
        test_funding = tf.funding_per_bar[split.test_lo : split.test_hi]
        test_atr = (
            full_atr[split.test_lo : split.test_hi]
            if full_atr is not None
            else None
        )

        bt = run_v2_backtest(
            bars=test_bars,
            signals=test_signals,
            funding_per_bar=test_funding,
            hold_bars=hold_bars,
            cooldown_bars=cooldown_bars,
            fee_per_side=fee_per_side,
            slip_per_side=slip_per_side,
            allow_opposite_flip_exit=allow_opposite_flip_exit,
            atr_values=test_atr,
            atr_trail_k=atr_trail_k if test_atr is not None else None,
            stop_loss_pct=stop_loss_pct,
            stop_trigger=stop_trigger,  # type: ignore[arg-type]
            stop_slip_pct=stop_slip_pct,
            stop_semantics=stop_semantics,  # type: ignore[arg-type]
            risk_per_trade=risk_per_trade,
            effective_leverage=effective_leverage,
        )

        per_split_curves.append(bt.equity_curve)
        per_split_metrics.append(bt.metrics)
        for t in bt.trades:
            all_trade_pnls.append(t.net_pnl)
            all_trade_gross.append(t.gross_pnl)
            all_trade_funding.append(t.funding_pnl)
            all_trade_cost.append(t.cost)
            all_trade_hold.append(t.hold_bars)
            exit_reason_counts[t.exit_reason] = exit_reason_counts.get(t.exit_reason, 0) + 1
            if t.exit_reason in ("stop_loss_long", "stop_loss_short"):
                all_stopped_pnls.append(t.net_pnl)
            # Worst adverse move during the hold (for liquidation safety).
            # For long: max over held bars of (entry - low) / entry.
            # For short: max over held bars of (high - entry) / entry.
            worst_adverse = 0.0
            for k in range(t.entry_idx, t.exit_idx):
                bar_k = test_bars[k]
                if t.side > 0:
                    adv = (t.entry_price - bar_k.low) / t.entry_price
                else:
                    adv = (bar_k.high - t.entry_price) / t.entry_price
                if adv > worst_adverse:
                    worst_adverse = adv
            all_adverse_moves.append(worst_adverse)
        if bt.metrics["compounded_return"] > 0:
            positive_windows += 1

    combined_curve = stitch_equity(per_split_curves)
    combined_return = (combined_curve[-1] - 1.0) if combined_curve else 0.0
    combined_dd = max_dd_of(combined_curve)
    num_splits = len(tf.splits)
    total_trades = int(sum(m["num_trades"] for m in per_split_metrics))
    pos_frac = positive_windows / num_splits if num_splits else 0.0
    avg_exposure = (
        sum(m["exposure_time"] for m in per_split_metrics) / num_splits
        if num_splits
        else 0.0
    )
    pf = combined_profit_factor(all_trade_pnls)

    total_gross = sum(all_trade_gross)
    total_funding = sum(all_trade_funding)
    total_cost = sum(all_trade_cost)
    avg_hold = (sum(all_trade_hold) / len(all_trade_hold)) if all_trade_hold else 0.0

    # Exit reason fractions (grouped by family)
    def _frac(prefix: str) -> float:
        if total_trades == 0:
            return 0.0
        matching = sum(v for k, v in exit_reason_counts.items() if k.startswith(prefix))
        return matching / total_trades

    # Stop-loss / worst-trade / liquidation-safety stats
    worst_trade_pnl = min(all_trade_pnls) if all_trade_pnls else 0.0
    avg_stopped_loss = (
        sum(all_stopped_pnls) / len(all_stopped_pnls) if all_stopped_pnls else 0.0
    )
    worst_adverse_move = max(all_adverse_moves) if all_adverse_moves else 0.0
    n_stopped = len(all_stopped_pnls)

    # Liquidation safety margin at L={1, 2, 3, 5}:
    #   liq distance ≈ 1/L - maintenance_margin (~0.4% for BTC tier 1)
    #   safety = liq_distance - worst_adverse_move
    #   positive = safe; negative = would have been liquidated on worst trade
    MAINT_MM = 0.004

    def liq_safety(L: float) -> float:
        return (1.0 / L - MAINT_MM) - worst_adverse_move

    row = {
        "timeframe": tf.name,
        "strategy": name,
        "hold_bars": hold_bars,
        "num_splits": num_splits,
        "total_oos_trades": total_trades,
        "agg_compounded_return": combined_return,
        "combined_max_dd": combined_dd,
        "combined_profit_factor": pf,
        "positive_windows_frac": pos_frac,
        "avg_exposure_time": avg_exposure,
        "enough_trades": total_trades >= 30,
        # PnL decomposition
        "total_gross_pnl": total_gross,
        "total_funding_pnl": total_funding,
        "total_cost_pnl": -total_cost,  # cost is positive, PnL impact is negative
        "avg_hold_bars": avg_hold,
        # Exit reason fractions
        "exit_time_stop_frac": _frac("time_stop") + _frac("end_of_series"),
        "exit_opposite_flip_frac": _frac("opposite_flip"),
        "exit_atr_trail_frac": _frac("atr_trail"),
        "exit_stop_loss_frac": _frac("stop_loss"),
        # Phase 5A new metrics
        "worst_trade_pnl": worst_trade_pnl,
        "n_stopped_out": n_stopped,
        "avg_stopped_loss": avg_stopped_loss,
        "worst_adverse_move": worst_adverse_move,
        "liq_safety_1x": liq_safety(1.0),
        "liq_safety_2x": liq_safety(2.0),
        "liq_safety_3x": liq_safety(3.0),
        "liq_safety_5x": liq_safety(5.0),
    }
    if extra_fields:
        row.update(extra_fields)
    return row


# ── printable row formatter ─────────────────────────────────────────


def format_row(row: dict) -> str:
    pf = row["combined_profit_factor"]
    pf_str = f"{pf:>6.2f}" if pf < NO_LOSS_PROFIT_FACTOR else "   inf"
    return (
        f"  {row['strategy']:<38} "
        f"h={row['hold_bars']:>3}  "
        f"n={row['total_oos_trades']:>5d}  "
        f"ret={row['agg_compounded_return'] * 100:>+8.2f}%  "
        f"dd={row['combined_max_dd'] * 100:>5.2f}%  "
        f"pos={row['positive_windows_frac'] * 100:>5.1f}%  "
        f"pf={pf_str}  "
        f"expo={row['avg_exposure_time'] * 100:>5.1f}%"
    )
