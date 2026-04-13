"""Strategy C v2 Phase 8 parity test — backtest vs retrospective
paper vs live monitor.

This test is the **critical gate** between "Gate 2 code changes" and
"real paper cron deployment". It verifies that three code paths
produce identical trade decisions for the same inputs:

  1. `run_v2_backtest` — vectorised historical backtest
  2. `run_retrospective_paper` — bar-by-bar replay driving the live
     monitor state machine
  3. The live monitor itself (called stepwise inside the
     retrospective runner)

Parity is defined as exact match on:
  - side (+1 / -1)
  - entry_fill_time (datetime)
  - actual_frac (within float tolerance)
  - hold_bars_used (exact int)
  - stop_level (within float tolerance)
  - exit_reason (string)

The test uses a fixed synthetic historical slice so the result is
deterministic and runs in milliseconds. A real-data sanity version
can be added later using a small 200-bar slice of BTCUSDT 4h data.

If any test in this file fails, **do not start the paper cron**.
The live monitor has drifted from the backtester and deploying it
would produce trades that don't match what the research showed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pytest

from adapters.base import MarketBar
from research.strategy_c_v2_backtest import run_v2_backtest
from research.strategy_c_v2_retrospective_paper import (
    PaperTrade,
    run_retrospective_paper,
)
from strategies.strategy_c_v2_dynamic_sizing import (
    compute_hold_bars_override_vector,
    compute_position_frac_override,
)
from strategies.strategy_c_v2_live_monitor import MonitorConfig


# ── synthetic feature + bar fixtures ───────────────────────────────


@dataclass
class _MutableFeature:
    """A mutable feature row for parity testing.

    Using a dataclass instead of the real StrategyCV2Features because
    we need to set specific field values per bar for deterministic
    signal generation.
    """
    timestamp: datetime
    close: float
    rsi_14: float | None = None
    rsi_21: float | None = None
    macd_hist: float | None = None
    ema_50: float | None = None
    ema_200: float | None = None
    funding_rate: float | None = None
    rv_4h: float | None = None


def _make_bar(
    t: datetime,
    open_: float,
    high: float,
    low: float,
    close: float,
) -> MarketBar:
    return MarketBar(
        timestamp=t,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1000.0,
    )


def _make_fixture_slice(
    n: int,
    signal_at: list[int],
    *,
    entry_prices: list[float] | None = None,
    stop_hit_at: int | None = None,
) -> tuple[list[MarketBar], list[_MutableFeature], list[int]]:
    """Build a deterministic historical slice.

    Args:
        n: number of bars
        signal_at: list of bar indices where a long signal fires
        entry_prices: optional custom close prices per bar
        stop_hit_at: optional bar index where the stop should fire
            (bar's close drops below entry * (1 - stop_pct))

    Returns:
        (bars, features, signals) with signals[i] = +1 at each signal_at
        index, 0 elsewhere.
    """
    start = datetime(2024, 1, 1)
    if entry_prices is None:
        entry_prices = [100.0 + i * 0.1 for i in range(n)]

    bars: list[MarketBar] = []
    for i in range(n):
        t = start + timedelta(hours=4 * i)
        c = entry_prices[i]
        bars.append(_make_bar(t, c, c * 1.002, c * 0.998, c))

    features: list[_MutableFeature] = []
    signals = [0] * n
    for i in range(n):
        f = _MutableFeature(
            timestamp=bars[i].timestamp,
            close=bars[i].close,
            rsi_14=50.0,      # neutral default
            rsi_21=50.0,
            macd_hist=0.0,
            ema_50=100.0,
            ema_200=100.0,
            funding_rate=0.0,
            rv_4h=0.010,
        )
        features.append(f)

    for idx in signal_at:
        signals[idx] = 1
        features[idx].rsi_14 = 90.0       # full extremity
        features[idx].rsi_21 = 90.0
        features[idx].macd_hist = 0.5
        features[idx].ema_50 = 110.0       # trend aligned
        features[idx].ema_200 = 100.0
        features[idx].funding_rate = 0.0001
        features[idx].rv_4h = 0.010

    # If we want a stop to fire, drop prices starting `stop_hit_at` bars
    # after the first entry
    if stop_hit_at is not None:
        for i in range(stop_hit_at, n):
            bars[i] = _make_bar(
                bars[i].timestamp,
                open_=bars[i].open,
                high=bars[i].high,
                low=bars[i].close * 0.95,  # deep wick
                close=bars[i].close * 0.97,
            )

    return bars, features, signals


# ── helper: compare trades ─────────────────────────────────────────


def _assert_trades_match(
    backtest_trades: list,
    paper_trades: tuple[PaperTrade, ...],
    *,
    label: str,
) -> None:
    assert len(backtest_trades) == len(paper_trades), (
        f"{label}: trade count mismatch — "
        f"backtest={len(backtest_trades)}, paper={len(paper_trades)}"
    )
    for k, (bt, pt) in enumerate(zip(backtest_trades, paper_trades)):
        assert bt.side == pt.side, (
            f"{label}[{k}]: side mismatch — bt={bt.side}, pt={pt.side}"
        )
        assert bt.entry_time == pt.entry_fill_time, (
            f"{label}[{k}]: entry_time mismatch — "
            f"bt={bt.entry_time}, pt={pt.entry_fill_time}"
        )
        assert bt.entry_price == pytest.approx(pt.entry_fill_price, abs=1e-6), (
            f"{label}[{k}]: entry_price mismatch — "
            f"bt={bt.entry_price}, pt={pt.entry_fill_price}"
        )
        assert bt.exit_time == pt.exit_fill_time, (
            f"{label}[{k}]: exit_time mismatch — "
            f"bt={bt.exit_time}, pt={pt.exit_fill_time}"
        )
        assert bt.exit_price == pytest.approx(pt.exit_fill_price, abs=1e-6), (
            f"{label}[{k}]: exit_price mismatch — "
            f"bt={bt.exit_price}, pt={pt.exit_fill_price}"
        )
        assert bt.exit_reason == pt.exit_reason, (
            f"{label}[{k}]: exit_reason mismatch — "
            f"bt={bt.exit_reason}, pt={pt.exit_reason}"
        )


# ── parity: fixed sizing, no stop ───────────────────────────────────


def test_parity_fixed_sizing_no_stop_single_trade() -> None:
    """Single long entry, fixed frac, time-stop exit at hold=5.

    Both paths must produce 1 trade with identical side/times/prices.
    """
    bars, features, signals = _make_fixture_slice(n=30, signal_at=[5])

    # ── Backtest path ──
    bt = run_v2_backtest(
        bars=bars,
        signals=signals,
        funding_per_bar=[0.0] * len(bars),
        hold_bars=5,
        risk_per_trade=0.02,
        stop_loss_pct=0.015,
        stop_trigger="close",
        stop_semantics="strategy_close_stop",
        effective_leverage=2.0,
    )

    # ── Retrospective paper path ──
    cfg = MonitorConfig(
        signal_family="rsi_only",
        rsi_field="rsi_21",
        max_hold_bars=5,
        base_frac=0.02 / 0.015,     # = 1.333
        stop_loss_pct=0.015,
        stop_semantics="strategy_close_stop",
    )
    paper = run_retrospective_paper(
        bars=bars,
        features=features,
        signals_external=signals,
        config=cfg,
    )

    assert len(bt.trades) == 1
    _assert_trades_match(bt.trades, paper.trades, label="fixed_sizing")

    # Also verify sizing / hold / stop match
    bt_t = bt.trades[0]
    pt = paper.trades[0]
    assert pt.actual_frac == pytest.approx(0.02 / 0.015, abs=1e-6)
    assert pt.hold_bars_used == 5
    assert pt.stop_level is not None
    assert pt.stop_level == pytest.approx(
        bt_t.entry_price * (1 - 0.015), abs=1e-6
    )


def test_parity_fixed_sizing_multiple_trades() -> None:
    bars, features, signals = _make_fixture_slice(
        n=60,
        signal_at=[5, 20, 40],
    )

    bt = run_v2_backtest(
        bars=bars,
        signals=signals,
        funding_per_bar=[0.0] * len(bars),
        hold_bars=5,
        risk_per_trade=0.02,
        stop_loss_pct=0.015,
        stop_trigger="close",
        stop_semantics="strategy_close_stop",
        effective_leverage=2.0,
    )
    cfg = MonitorConfig(
        signal_family="rsi_only",
        rsi_field="rsi_21",
        max_hold_bars=5,
        base_frac=0.02 / 0.015,
        stop_loss_pct=0.015,
        stop_semantics="strategy_close_stop",
    )
    paper = run_retrospective_paper(
        bars=bars,
        features=features,
        signals_external=signals,
        config=cfg,
    )

    assert len(bt.trades) >= 2
    _assert_trades_match(bt.trades, paper.trades, label="multi_trade")


# ── parity: dynamic sizing ──────────────────────────────────────────


def test_parity_dynamic_sizing_matches_backtest_override() -> None:
    """With dynamic sizing enabled, both paths must produce identical
    per-trade actual_frac values.

    This is the most important parity: if the live monitor's sizing
    drifts from the backtester's override vector, the canonical
    D1_long_dynamic +164.32% number becomes unreachable in live.
    """
    bars, features, signals = _make_fixture_slice(
        n=60,
        signal_at=[5, 25, 45],
    )
    base_frac = 0.02 / 0.015

    # Compute override vector (what backtester consumes)
    override = compute_position_frac_override(features, signals, base_frac)

    bt = run_v2_backtest(
        bars=bars,
        signals=signals,
        funding_per_bar=[0.0] * len(bars),
        hold_bars=5,
        risk_per_trade=0.02,
        stop_loss_pct=0.015,
        stop_trigger="close",
        stop_semantics="strategy_close_stop",
        effective_leverage=2.0,
        position_frac_override=override,
    )

    # Retrospective paper path with dynamic sizing on
    cfg = MonitorConfig(
        signal_family="rsi_only",
        rsi_field="rsi_21",
        max_hold_bars=5,
        base_frac=base_frac,
        use_dynamic_sizing=True,
        stop_loss_pct=0.015,
        stop_semantics="strategy_close_stop",
    )
    paper = run_retrospective_paper(
        bars=bars,
        features=features,
        signals_external=signals,
        config=cfg,
    )

    _assert_trades_match(bt.trades, paper.trades, label="dynamic_sizing")

    # Assert the sizing values match the override exactly
    for bt_t, pt in zip(bt.trades, paper.trades):
        expected_frac = override[bt_t.entry_idx - 1]  # entry_idx = signal_idx + 1
        assert expected_frac is not None
        assert pt.actual_frac == pytest.approx(expected_frac, abs=1e-6)


def test_parity_dynamic_sizing_all_signal_bars_get_multiplier() -> None:
    """Sanity: the computed override vector has the right per-bar structure."""
    bars, features, signals = _make_fixture_slice(
        n=30,
        signal_at=[5, 15, 25],
    )
    base_frac = 1.333
    override = compute_position_frac_override(features, signals, base_frac)

    # Non-signal bars are None
    for i in range(len(override)):
        if signals[i] == 0:
            assert override[i] is None
        else:
            assert override[i] is not None
            # Since features at signal bars have full conviction,
            # multiplier should be 1.5 → frac = 1.333 * 1.5 = 1.9995
            assert override[i] == pytest.approx(base_frac * 1.5, abs=1e-4)


# ── parity: adaptive hold ──────────────────────────────────────────


def test_parity_adaptive_hold_matches_backtest_override() -> None:
    """Adaptive hold on: backtester hold_bars_override and live
    monitor's per-trade max_hold_override must drive identical exit
    timing.
    """
    bars, features, signals = _make_fixture_slice(
        n=60,
        signal_at=[5, 30],
    )
    base_hold = 6

    hold_override = compute_hold_bars_override_vector(
        features, signals, base_hold
    )
    # At fully-aligned bars the adaptive hold extends to base*1.5 = 9
    # (since all 3 components score 1 in our fixture)
    for i, s in enumerate(signals):
        if s != 0:
            assert hold_override[i] == int(base_hold * 1.5)

    bt = run_v2_backtest(
        bars=bars,
        signals=signals,
        funding_per_bar=[0.0] * len(bars),
        hold_bars=base_hold,
        risk_per_trade=0.02,
        stop_loss_pct=0.015,
        stop_trigger="close",
        stop_semantics="strategy_close_stop",
        effective_leverage=2.0,
        hold_bars_override=hold_override,
    )

    cfg = MonitorConfig(
        signal_family="rsi_only",
        rsi_field="rsi_21",
        max_hold_bars=base_hold,
        base_frac=0.02 / 0.015,
        use_adaptive_hold=True,
        stop_loss_pct=0.015,
        stop_semantics="strategy_close_stop",
    )
    paper = run_retrospective_paper(
        bars=bars,
        features=features,
        signals_external=signals,
        config=cfg,
    )

    _assert_trades_match(bt.trades, paper.trades, label="adaptive_hold")

    # Verify hold_bars_used matches the override (= 9 for extended trades)
    for pt in paper.trades:
        assert pt.hold_bars_used == int(base_hold * 1.5)


# ── parity: dynamic sizing + adaptive hold combined ─────────────────


def test_parity_dynamic_sizing_plus_adaptive_hold_combined() -> None:
    """The D1_long_dynamic_adaptive cell configuration path."""
    bars, features, signals = _make_fixture_slice(
        n=60,
        signal_at=[5, 25, 45],
    )
    base_hold = 6
    base_frac = 0.02 / 0.015

    sizing_override = compute_position_frac_override(features, signals, base_frac)
    hold_override = compute_hold_bars_override_vector(
        features, signals, base_hold
    )

    bt = run_v2_backtest(
        bars=bars,
        signals=signals,
        funding_per_bar=[0.0] * len(bars),
        hold_bars=base_hold,
        risk_per_trade=0.02,
        stop_loss_pct=0.015,
        stop_trigger="close",
        stop_semantics="strategy_close_stop",
        effective_leverage=2.0,
        position_frac_override=sizing_override,
        hold_bars_override=hold_override,
    )

    cfg = MonitorConfig(
        signal_family="rsi_only",
        rsi_field="rsi_21",
        max_hold_bars=base_hold,
        base_frac=base_frac,
        use_dynamic_sizing=True,
        use_adaptive_hold=True,
        stop_loss_pct=0.015,
        stop_semantics="strategy_close_stop",
    )
    paper = run_retrospective_paper(
        bars=bars,
        features=features,
        signals_external=signals,
        config=cfg,
    )

    _assert_trades_match(bt.trades, paper.trades, label="combined")

    # All trades should use the extended hold (9 bars) at full frac (2.0)
    for pt in paper.trades:
        assert pt.actual_frac == pytest.approx(base_frac * 1.5, abs=1e-6)
        assert pt.hold_bars_used == 9


# ── parity: stop loss fires ─────────────────────────────────────────


def test_parity_stop_loss_fires_on_close_trigger() -> None:
    """When the stop fires, both paths must record the same
    exit_reason and fill at the same bar / price.
    """
    bars, features, signals = _make_fixture_slice(
        n=20,
        signal_at=[2],
        stop_hit_at=5,  # drop prices at bar 5 → stop fires
    )

    bt = run_v2_backtest(
        bars=bars,
        signals=signals,
        funding_per_bar=[0.0] * len(bars),
        hold_bars=10,
        risk_per_trade=0.02,
        stop_loss_pct=0.015,
        stop_trigger="close",
        stop_semantics="strategy_close_stop",
        effective_leverage=2.0,
    )
    assert len(bt.trades) == 1
    assert bt.trades[0].exit_reason == "stop_loss_long"

    cfg = MonitorConfig(
        signal_family="rsi_only",
        rsi_field="rsi_21",
        max_hold_bars=10,
        base_frac=0.02 / 0.015,
        stop_loss_pct=0.015,
        stop_semantics="strategy_close_stop",
    )
    paper = run_retrospective_paper(
        bars=bars,
        features=features,
        signals_external=signals,
        config=cfg,
    )
    _assert_trades_match(bt.trades, paper.trades, label="stop_fires")

    assert paper.trades[0].exit_reason == "stop_loss_long"
    assert paper.trades[0].stop_level is not None


# ── cell-level parity — D1_long_primary configuration ──────────────


def test_parity_d1_long_primary_config() -> None:
    """Full D1_long_primary deployment config on a synthetic slice.

    Mirrors the exact cell config from CANONICAL_CELLS, except on a
    small fixture to keep the test fast.
    """
    bars, features, signals = _make_fixture_slice(
        n=80,
        signal_at=[10, 35, 60],
    )

    bt = run_v2_backtest(
        bars=bars,
        signals=signals,
        funding_per_bar=[0.0] * len(bars),
        hold_bars=11,           # D1_long hold
        risk_per_trade=0.02,    # D1_long risk
        stop_loss_pct=0.015,    # D1_long stop
        stop_trigger="close",
        stop_semantics="strategy_close_stop",
        effective_leverage=2.0,
    )

    cfg = MonitorConfig(
        signal_family="rsi_only",
        rsi_field="rsi_21",
        max_hold_bars=11,
        base_frac=0.02 / 0.015,  # 1.333
        stop_loss_pct=0.015,
        stop_semantics="strategy_close_stop",
    )
    paper = run_retrospective_paper(
        bars=bars,
        features=features,
        signals_external=signals,
        config=cfg,
    )
    _assert_trades_match(bt.trades, paper.trades, label="D1_long_primary")


def test_parity_d1_long_dynamic_config() -> None:
    """Full D1_long_dynamic deployment config on a synthetic slice."""
    bars, features, signals = _make_fixture_slice(
        n=80,
        signal_at=[10, 35, 60],
    )
    base_frac = 0.02 / 0.015
    override = compute_position_frac_override(features, signals, base_frac)

    bt = run_v2_backtest(
        bars=bars,
        signals=signals,
        funding_per_bar=[0.0] * len(bars),
        hold_bars=11,
        risk_per_trade=0.02,
        stop_loss_pct=0.015,
        stop_trigger="close",
        stop_semantics="strategy_close_stop",
        effective_leverage=2.0,
        position_frac_override=override,
    )

    cfg = MonitorConfig(
        signal_family="rsi_only",
        rsi_field="rsi_21",
        max_hold_bars=11,
        base_frac=base_frac,
        use_dynamic_sizing=True,
        stop_loss_pct=0.015,
        stop_semantics="strategy_close_stop",
    )
    paper = run_retrospective_paper(
        bars=bars,
        features=features,
        signals_external=signals,
        config=cfg,
    )
    _assert_trades_match(bt.trades, paper.trades, label="D1_long_dynamic")


def test_parity_d1_long_dynamic_adaptive_config() -> None:
    """Full D1_long_dynamic_adaptive deployment config on a synthetic slice."""
    bars, features, signals = _make_fixture_slice(
        n=80,
        signal_at=[10, 35, 60],
    )
    base_frac = 0.02 / 0.015
    base_hold = 11

    sizing = compute_position_frac_override(features, signals, base_frac)
    hold = compute_hold_bars_override_vector(features, signals, base_hold)

    bt = run_v2_backtest(
        bars=bars,
        signals=signals,
        funding_per_bar=[0.0] * len(bars),
        hold_bars=base_hold,
        risk_per_trade=0.02,
        stop_loss_pct=0.015,
        stop_trigger="close",
        stop_semantics="strategy_close_stop",
        effective_leverage=2.0,
        position_frac_override=sizing,
        hold_bars_override=hold,
    )

    cfg = MonitorConfig(
        signal_family="rsi_only",
        rsi_field="rsi_21",
        max_hold_bars=base_hold,
        base_frac=base_frac,
        use_dynamic_sizing=True,
        use_adaptive_hold=True,
        stop_loss_pct=0.015,
        stop_semantics="strategy_close_stop",
    )
    paper = run_retrospective_paper(
        bars=bars,
        features=features,
        signals_external=signals,
        config=cfg,
    )
    _assert_trades_match(bt.trades, paper.trades, label="D1_long_dynamic_adaptive")
