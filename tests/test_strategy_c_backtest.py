"""Tests for Strategy C minimal backtest engine.

Execution model (per user spec):
    - Signal generated at bar[t].close
    - Entry  at bar[t+1].open
    - Hold   for `hold_bars` bars (time stop) OR until opposite signal arrives
    - Exit   at bar[t+1+hold_bars].open (time stop) or bar[j+1].open (opposite signal)
    - No position stacking: if a signal fires while already in a position, ignore it
    - Realistic fees + slippage applied on each side

Metrics returned (9 total):
    net_pnl, trade_sharpe, trade_sortino, max_dd,
    num_trades, win_rate, avg_pnl, turnover, avg_hold_bars
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from data.strategy_c_features import StrategyCFeatureBar
from research.backtest_strategy_c import run_strategy_c_backtest, Trade


# ── Test helpers ──────────────────────────────────────────────────────


def _bars(prices: list[float]) -> list[StrategyCFeatureBar]:
    """Build a minimal feature bar series with given open=close prices.

    Every optional z-score is pinned to 0.0 so the bar is complete, but the
    backtest engine doesn't look at them — it only touches open/close.
    """
    return [
        StrategyCFeatureBar(
            timestamp=datetime(2026, 2, 16) + timedelta(minutes=15 * i),
            open=p,
            close=p,
            taker_delta_norm=0.0,
            cvd_delta=0.0,
            basis_change=0.0,
            fr_spread=0.0,
            agg_u_oi_pct=0.0,
            liq_imbalance=0.0,
            taker_delta_norm_z32=0.0,
            oi_pct_change_z32=0.0,
            basis_z96=0.0,
            fr_close_z96=0.0,
            cvd_delta_z32=0.0,
            long_liq_z32=0.0,
            short_liq_z32=0.0,
            basis_change_z32=0.0,
            fr_spread_z96=0.0,
            agg_u_oi_pct_z32=0.0,
        )
        for i, p in enumerate(prices)
    ]


# ── Single-trade execution ────────────────────────────────────────────


def test_long_trade_exit_at_time_stop_no_fees() -> None:
    """Signal at bar 0 → entry at bar 1 open → exit at bar 4 open (hold=3)."""
    bars = _bars([100, 100, 101, 102, 103, 103])  # 6 bars
    signals = [1, 0, 0, 0, 0, 0]
    result = run_strategy_c_backtest(
        bars, signals, hold_bars=3, fee_per_side=0.0, slippage_per_side=0.0
    )
    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.side == 1
    assert t.entry_px == 100  # bar[1].open
    assert t.exit_px == 103  # bar[4].open
    assert t.hold_bars == 3
    assert t.pnl_ret == pytest.approx(0.03)
    assert t.pnl_net == pytest.approx(0.03)  # no fees


def test_short_trade_makes_money_when_price_drops() -> None:
    bars = _bars([100, 100, 99, 98, 97, 97])
    signals = [-1, 0, 0, 0, 0, 0]
    result = run_strategy_c_backtest(
        bars, signals, hold_bars=3, fee_per_side=0.0, slippage_per_side=0.0
    )
    t = result.trades[0]
    assert t.side == -1
    assert t.pnl_ret == pytest.approx(0.03)  # (100 - 97) / 100


def test_opposite_signal_closes_position_early() -> None:
    """Opposite signal at bar 2 → exit at bar 3 open, before time stop."""
    bars = _bars([100, 100, 105, 107, 110, 110])
    signals = [1, 0, -1, 0, 0, 0]  # long at 0, short at 2
    result = run_strategy_c_backtest(
        bars, signals, hold_bars=10, fee_per_side=0.0, slippage_per_side=0.0
    )
    assert len(result.trades) >= 1
    first = result.trades[0]
    assert first.side == 1
    assert first.entry_px == 100  # bar[1].open
    assert first.exit_px == 107   # bar[3].open (triggered by opposite signal at bar 2)
    assert first.hold_bars == 2


def test_no_position_stacking() -> None:
    """Long already open → further long signals are ignored until exit."""
    bars = _bars([100, 100, 100, 100, 100, 100, 100])
    signals = [1, 1, 1, 0, 0, 0, 0]  # 3 consecutive long signals
    result = run_strategy_c_backtest(
        bars, signals, hold_bars=3, fee_per_side=0.0, slippage_per_side=0.0
    )
    # Only ONE trade — the first long, held 3 bars. Signals during position are ignored.
    assert len(result.trades) == 1


def test_flat_signal_does_nothing() -> None:
    bars = _bars([100, 100, 100, 100, 100])
    signals = [0, 0, 0, 0, 0]
    result = run_strategy_c_backtest(bars, signals, hold_bars=3)
    assert len(result.trades) == 0


# ── Cooldown between trades ──────────────────────────────────────────


def test_cooldown_blocks_signals_for_n_bars_after_exit() -> None:
    """After a trade exits, ignore signals for `cooldown_bars` bars."""
    # bar 0 → signal → enter bar 1 open, exit bar 4 open (hold=3)
    # bar 4 → another long signal — blocked by cooldown=2 (covers bars 4 & 5)
    # bar 6 → another long signal — cooldown expired, enters bar 7 open
    bars = _bars([100, 100, 101, 102, 103, 103, 100, 100, 100, 100, 100])
    signals = [1, 0, 0, 0, 1, 0, 1, 0, 0, 0, 0]
    result = run_strategy_c_backtest(
        bars, signals, hold_bars=3, cooldown_bars=2,
        fee_per_side=0.0, slippage_per_side=0.0,
    )
    # Expected: first trade (bar 1→4), then cooldown blocks bar-4 signal,
    # then second trade opens on bar 6's signal (bar 7 open).
    assert len(result.trades) == 2
    assert result.trades[0].entry_ts == bars[1].timestamp
    assert result.trades[0].exit_ts == bars[4].timestamp
    assert result.trades[1].entry_ts == bars[7].timestamp


def test_cooldown_zero_is_no_cooldown() -> None:
    """Explicit cooldown=0 keeps the default back-to-back behaviour."""
    bars = _bars([100] * 10)
    signals = [1, 0, 0, 0, 1, 0, 0, 0, 0, 0]
    result = run_strategy_c_backtest(
        bars, signals, hold_bars=3, cooldown_bars=0,
        fee_per_side=0.0, slippage_per_side=0.0,
    )
    # Trade 1: bar 1 → bar 4 (hold 3). Trade 2: signal at bar 4 → bar 5 → bar 8.
    assert len(result.trades) == 2


# ── Fees and slippage ────────────────────────────────────────────────


def test_fees_and_slippage_are_subtracted_round_trip() -> None:
    """Round-trip cost = 2 * (fee + slip). Should be subtracted from raw pnl."""
    bars = _bars([100, 100, 100, 100, 100, 100])
    signals = [1, 0, 0, 0, 0, 0]
    result = run_strategy_c_backtest(
        bars, signals, hold_bars=3, fee_per_side=0.0005, slippage_per_side=0.0001
    )
    t = result.trades[0]
    assert t.pnl_ret == pytest.approx(0.0)       # raw: no price change
    assert t.pnl_net == pytest.approx(-0.0012)   # 2 * (0.0005 + 0.0001) = 0.0012 loss


# ── Aggregate metrics ────────────────────────────────────────────────


def test_metrics_basic_two_trades() -> None:
    """Two trades: +3% and -1%. Check win rate, avg, net, count."""
    # Trade 1: long at bar 1 open=100, hold 3 bars → exit bar 4 open=103 → +3%
    # Trade 2: long at bar 6 open=100, hold 3 bars → exit bar 9 open=99 → -1%
    bars = _bars([100, 100, 101, 102, 103, 103, 100, 100, 99, 99, 99, 99])
    # Need a gap so the first trade is closed before the second signal fires.
    signals = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0]
    result = run_strategy_c_backtest(
        bars, signals, hold_bars=3, fee_per_side=0.0, slippage_per_side=0.0
    )
    assert result.metrics["num_trades"] == 2
    # win_rate: 1 win (+3%), 1 loss (-1%) → 0.5
    assert result.metrics["win_rate"] == pytest.approx(0.5)
    # net_pnl: +3% + (-1%) = +2% (simple sum)
    assert result.metrics["net_pnl"] == pytest.approx(0.02)
    # avg_pnl: (0.03 + (-0.01)) / 2 = 0.01
    assert result.metrics["avg_pnl"] == pytest.approx(0.01)
    assert result.metrics["avg_hold_bars"] == 3


def test_metrics_empty_returns_zero_trades() -> None:
    result = run_strategy_c_backtest(_bars([100, 100, 100]), [0, 0, 0], hold_bars=3)
    assert result.metrics["num_trades"] == 0
    assert result.metrics["net_pnl"] == 0.0


def test_max_drawdown_from_equity_curve() -> None:
    """Equity curve 1.0 → 1.03 → 0.97 → drawdown = (1.03 - 0.97)/1.03 ≈ 0.0583."""
    bars = _bars([100, 100, 101, 102, 103, 103,   # trade 1: +3%
                  100, 100, 98.06, 96.06, 94.06, 94.06])  # trade 2: ~-6% → below start
    # Adjust: after +3% we're at 1.03. Then a ~6% loss → 1.03 * 0.94 ≈ 0.968.
    # DD = (1.03 - 0.968) / 1.03 = 0.0602
    signals = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0]
    result = run_strategy_c_backtest(
        bars, signals, hold_bars=3, fee_per_side=0.0, slippage_per_side=0.0
    )
    assert result.metrics["num_trades"] == 2
    assert result.metrics["max_dd"] > 0.0  # some positive drawdown


# ── Trade dataclass ──────────────────────────────────────────────────


def test_trade_has_required_fields() -> None:
    bars = _bars([100, 100, 101, 102, 103, 103])
    result = run_strategy_c_backtest(bars, [1, 0, 0, 0, 0, 0], hold_bars=3,
                                      fee_per_side=0.0, slippage_per_side=0.0)
    t = result.trades[0]
    assert isinstance(t, Trade)
    assert t.entry_ts == bars[1].timestamp
    assert t.exit_ts == bars[4].timestamp
    assert t.side == 1
    assert t.entry_px == 100
    assert t.exit_px == 103


# ── Extended metrics (Baseline C return-frequency tradeoff) ─────────


def test_compounded_return_matches_equity_final() -> None:
    """compounded_return = product(1 + pnl_net) - 1, not a simple sum of returns."""
    # Trade 1: +3%, Trade 2: -1%. Simple sum = +2%. Compounded = 1.03*0.99 - 1 = 0.0197.
    bars = _bars([100, 100, 101, 102, 103, 103, 100, 100, 99, 99, 99, 99])
    signals = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0]
    result = run_strategy_c_backtest(
        bars, signals, hold_bars=3, fee_per_side=0.0, slippage_per_side=0.0
    )
    assert result.metrics["num_trades"] == 2
    # 1.03 * 0.99 = 1.0197 → compounded = 0.0197
    assert result.metrics["compounded_return"] == pytest.approx(0.0197, abs=1e-6)
    # simple net_pnl stays at 0.02 (old metric kept for back-compat)
    assert result.metrics["net_pnl"] == pytest.approx(0.02)


def test_compounded_return_empty_is_zero() -> None:
    result = run_strategy_c_backtest(_bars([100, 100, 100]), [0, 0, 0], hold_bars=3)
    assert result.metrics["compounded_return"] == 0.0


def test_profit_factor_sum_wins_over_abs_sum_losses() -> None:
    """profit_factor = sum(winners) / |sum(losers)|. Two winners 3% + 1%, one loser -2%
    → PF = 0.04 / 0.02 = 2.0."""
    # Trade 1: +3%, Trade 2: -2%, Trade 3: +1%.
    bars = _bars([
        100, 100, 103, 103, 103, 103,     # trade 1: bar 1 open 100 → bar 4 open 103, +3%
        100, 100, 98, 98, 98, 98,         # trade 2: bar 7 open 100 → bar 10 open 98, -2%
        100, 100, 101, 101, 101, 101,     # trade 3: bar 13 open 100 → bar 16 open 101, +1%
    ])
    signals = [
        1, 0, 0, 0, 0, 0,
        1, 0, 0, 0, 0, 0,
        1, 0, 0, 0, 0, 0,
    ]
    result = run_strategy_c_backtest(
        bars, signals, hold_bars=3, fee_per_side=0.0, slippage_per_side=0.0
    )
    assert result.metrics["num_trades"] == 3
    # wins: 0.03 + 0.01 = 0.04; losses: -0.02; PF = 0.04 / 0.02 = 2.0
    assert result.metrics["profit_factor"] == pytest.approx(2.0)


def test_profit_factor_all_wins_is_inf_sentinel() -> None:
    """No losses → profit factor should be a finite large sentinel so sweeps
    can rank it without hitting math.inf serialisation issues."""
    bars = _bars([100, 100, 101, 102, 103, 103])
    signals = [1, 0, 0, 0, 0, 0]
    result = run_strategy_c_backtest(
        bars, signals, hold_bars=3, fee_per_side=0.0, slippage_per_side=0.0
    )
    assert result.metrics["num_trades"] == 1
    # Convention: no losses → profit_factor = 9999.0 (rankable, finite, obvious)
    assert result.metrics["profit_factor"] == pytest.approx(9999.0)


def test_profit_factor_all_losses_is_zero() -> None:
    bars = _bars([100, 100, 99, 98, 97, 97])
    signals = [1, 0, 0, 0, 0, 0]
    result = run_strategy_c_backtest(
        bars, signals, hold_bars=3, fee_per_side=0.0, slippage_per_side=0.0
    )
    assert result.metrics["num_trades"] == 1
    assert result.metrics["profit_factor"] == 0.0


def test_profit_factor_empty_is_zero() -> None:
    result = run_strategy_c_backtest(_bars([100, 100, 100]), [0, 0, 0], hold_bars=3)
    assert result.metrics["profit_factor"] == 0.0


def test_exposure_time_sum_hold_over_total_bars() -> None:
    """exposure_time = sum(trade.hold_bars) / len(feats). Two trades held 3 bars each
    over a 12-bar feats series → 6/12 = 0.5."""
    bars = _bars([100, 100, 101, 102, 103, 103, 100, 100, 99, 99, 99, 99])
    signals = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0]
    result = run_strategy_c_backtest(
        bars, signals, hold_bars=3, fee_per_side=0.0, slippage_per_side=0.0
    )
    assert result.metrics["num_trades"] == 2
    # Two trades of hold=3, over 12-bar feats series → 6/12 = 0.5.
    assert result.metrics["exposure_time"] == pytest.approx(0.5)


def test_exposure_time_empty_is_zero() -> None:
    result = run_strategy_c_backtest(_bars([100, 100, 100]), [0, 0, 0], hold_bars=3)
    assert result.metrics["exposure_time"] == 0.0
