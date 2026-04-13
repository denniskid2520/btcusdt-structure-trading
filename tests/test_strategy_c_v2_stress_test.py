"""Tests for the Phase 8 stress test suite."""
from __future__ import annotations

import pytest

from research.strategy_c_v2_stress_test import (
    ShockResult,
    SlippageResult,
    StressConfig,
    StressVerdict,
    classify_shock,
    estimate_slippage_impact,
    run_stress_suite,
)


# ── StressConfig basics ────────────────────────────────────────────


def test_stress_config_2x_liquidation_distance_is_50pct() -> None:
    c = StressConfig(exchange_leverage=2.0, max_actual_frac=2.0)
    assert c.liquidation_adverse_move == pytest.approx(0.5)


def test_stress_config_3x_liquidation_distance_is_33pct() -> None:
    c = StressConfig(exchange_leverage=3.0, max_actual_frac=3.0)
    assert c.liquidation_adverse_move == pytest.approx(1.0 / 3.0)


def test_stress_config_5x_liquidation_distance_is_20pct() -> None:
    c = StressConfig(exchange_leverage=5.0, max_actual_frac=5.0)
    assert c.liquidation_adverse_move == pytest.approx(0.2)


# ── classify_shock ─────────────────────────────────────────────────


def test_classify_shock_10pct_on_2x_survives_comfortably() -> None:
    c = StressConfig(exchange_leverage=2.0, max_actual_frac=2.0)
    sr = classify_shock(0.10, historical_max_adverse=0.065, config=c)
    assert sr.combined_adverse == pytest.approx(0.165)
    assert not sr.liq_touched
    assert sr.verdict == "survives"


def test_classify_shock_40pct_on_2x_survives_tight() -> None:
    """On 2x, liq distance = 50%. 40% shock + 6.5% historical = 46.5%
    combined. That's within 5pp of liq → 'survives_tight'."""
    c = StressConfig(exchange_leverage=2.0, max_actual_frac=2.0)
    sr = classify_shock(0.40, historical_max_adverse=0.065, config=c)
    assert sr.combined_adverse == pytest.approx(0.465)
    assert sr.verdict == "survives_tight"
    assert not sr.liq_touched


def test_classify_shock_40pct_on_3x_liquidates() -> None:
    """On 3x, liq distance = 33.3%. 40% shock > 33.3% → liquidates."""
    c = StressConfig(exchange_leverage=3.0, max_actual_frac=3.0)
    sr = classify_shock(0.40, historical_max_adverse=0.065, config=c)
    assert sr.verdict == "liquidates"
    assert sr.liq_touched


def test_classify_shock_20pct_on_5x_liquidates_due_to_historical() -> None:
    """5x liq = 20%. Even a 20% shock alone with 0% historical hits
    exactly the liq line. Verdict = liquidates (boundary is >=)."""
    c = StressConfig(exchange_leverage=5.0, max_actual_frac=5.0)
    sr = classify_shock(0.20, historical_max_adverse=0.065, config=c)
    # 0.065 + 0.20 = 0.265 > 0.20 liq → liquidates
    assert sr.verdict == "liquidates"


def test_classify_shock_15pct_on_5x_survives() -> None:
    """15% shock + 6.5% historical = 21.5% on 5x (liq=20%) → liquidates."""
    c = StressConfig(exchange_leverage=5.0, max_actual_frac=5.0)
    sr = classify_shock(0.15, historical_max_adverse=0.065, config=c)
    assert sr.verdict == "liquidates"


def test_classify_shock_10pct_on_5x_survives_tight() -> None:
    """10% shock + 6.5% historical = 16.5% on 5x (liq=20%) → tight survival
    (within 5pp of liq line)."""
    c = StressConfig(exchange_leverage=5.0, max_actual_frac=5.0)
    sr = classify_shock(0.10, historical_max_adverse=0.065, config=c)
    assert sr.verdict == "survives_tight"


def test_classify_shock_equity_loss_scales_with_max_frac() -> None:
    """Equity loss = combined * max_actual_frac."""
    c = StressConfig(exchange_leverage=3.0, max_actual_frac=3.0)
    sr = classify_shock(0.10, historical_max_adverse=0.05, config=c)
    # 0.15 * 3.0 = 0.45 → 45% equity loss
    assert sr.equity_loss_pct == pytest.approx(0.45)


# ── estimate_slippage_impact ──────────────────────────────────────


def test_slippage_zero_stops_is_noop() -> None:
    sr = estimate_slippage_impact(
        slip_pct=0.01,
        num_stop_exits=0,
        avg_actual_frac=2.0,
        num_trades=50,
        baseline_return_pct=100.0,
    )
    assert sr.adjusted_return_pct == pytest.approx(100.0)
    assert sr.return_delta_pp == 0.0


def test_slippage_large_drag_collapses_operationally() -> None:
    """1% slippage on 20 stop exits at frac=2 = 20 * 0.01 * 2 = 40% drag.
    Baseline 250% → adjusted = (1 + 2.5) * (1 - 0.4) - 1 = 1.1 = 110%.
    Delta = -140pp, beyond the -50pp collapse threshold →
    operationally_acceptable = False.
    """
    sr = estimate_slippage_impact(
        slip_pct=0.01,
        num_stop_exits=20,
        avg_actual_frac=2.0,
        num_trades=70,
        baseline_return_pct=250.0,
    )
    assert sr.extra_drag_on_stops == pytest.approx(0.4)
    assert sr.adjusted_return_pct == pytest.approx(110.0, abs=0.1)
    assert sr.return_delta_pp == pytest.approx(-140.0, abs=0.1)
    assert not sr.operationally_acceptable


def test_slippage_tiny_impact_stays_acceptable() -> None:
    """0.1% slippage on 20 stop exits at frac=2 = 4% drag. Baseline
    250% → (1+2.5) * 0.96 - 1 = 2.36. Delta -14pp."""
    sr = estimate_slippage_impact(
        slip_pct=0.001,
        num_stop_exits=20,
        avg_actual_frac=2.0,
        num_trades=70,
        baseline_return_pct=250.0,
    )
    assert sr.return_delta_pp == pytest.approx(-14.0, abs=0.1)
    assert sr.operationally_acceptable


def test_slippage_delta_is_negative() -> None:
    """Slippage always makes the return WORSE, delta must be <= 0."""
    sr = estimate_slippage_impact(
        slip_pct=0.005,
        num_stop_exits=25,
        avg_actual_frac=1.5,
        num_trades=75,
        baseline_return_pct=200.0,
    )
    assert sr.return_delta_pp <= 0.0


# ── full stress suite ─────────────────────────────────────────────


def test_stress_suite_pass_at_2x_typical_d1_long() -> None:
    """D1_long primary-like: 73 trades, PF 2.23, 60% win rate,
    worst_adverse 6.51%, 22 stops, frac=1.333, return +143%.
    Should pass all filters at 2x."""
    v = run_stress_suite(
        config=StressConfig(exchange_leverage=2.0, max_actual_frac=1.333),
        historical_max_adverse=0.0651,
        num_trades=73,
        num_stop_exits=22,
        avg_actual_frac=1.333,
        baseline_return_pct=143.45,
        profit_factor=2.23,
        win_rate=0.60,
    )
    # fails the 100 trade filter
    assert not v.shortlist_pass
    assert "trade count 73" in v.shortlist_reason


def test_stress_suite_fail_historical_liquidation() -> None:
    """A cell whose worst historical adverse exceeds liq distance fails."""
    v = run_stress_suite(
        config=StressConfig(exchange_leverage=5.0, max_actual_frac=5.0),
        historical_max_adverse=0.22,  # > 20% liq distance
        num_trades=150,
        num_stop_exits=30,
        avg_actual_frac=5.0,
        baseline_return_pct=400.0,
        profit_factor=2.5,
        win_rate=0.60,
    )
    assert not v.shortlist_pass
    assert v.historical_liquidated
    assert "historical adverse" in v.shortlist_reason


def test_stress_suite_fail_on_weak_pf() -> None:
    v = run_stress_suite(
        config=StressConfig(exchange_leverage=2.0, max_actual_frac=2.0),
        historical_max_adverse=0.065,
        num_trades=150,
        num_stop_exits=40,
        avg_actual_frac=2.0,
        baseline_return_pct=180.0,
        profit_factor=1.60,   # < 2.0
        win_rate=0.58,
    )
    assert not v.shortlist_pass
    assert "profit factor 1.60" in v.shortlist_reason


def test_stress_suite_fail_on_weak_win_rate() -> None:
    v = run_stress_suite(
        config=StressConfig(exchange_leverage=2.0, max_actual_frac=2.0),
        historical_max_adverse=0.065,
        num_trades=150,
        num_stop_exits=40,
        avg_actual_frac=2.0,
        baseline_return_pct=180.0,
        profit_factor=2.20,
        win_rate=0.50,  # < 0.55
    )
    assert not v.shortlist_pass
    assert "win rate" in v.shortlist_reason


def test_stress_suite_emits_shock_verdicts_for_all_levels() -> None:
    v = run_stress_suite(
        config=StressConfig(exchange_leverage=3.0, max_actual_frac=3.0),
        historical_max_adverse=0.065,
        num_trades=120,
        num_stop_exits=30,
        avg_actual_frac=3.0,
        baseline_return_pct=400.0,
        profit_factor=2.5,
        win_rate=0.60,
    )
    # default shock levels = (0.10, 0.15, 0.20, 0.30, 0.40)
    assert len(v.shock_results) == 5
    shock_levels = [sr.shock_pct for sr in v.shock_results]
    assert shock_levels == [0.10, 0.15, 0.20, 0.30, 0.40]


def test_stress_suite_emits_slippage_results_for_all_levels() -> None:
    v = run_stress_suite(
        config=StressConfig(exchange_leverage=3.0, max_actual_frac=3.0),
        historical_max_adverse=0.065,
        num_trades=120,
        num_stop_exits=30,
        avg_actual_frac=3.0,
        baseline_return_pct=400.0,
        profit_factor=2.5,
        win_rate=0.60,
    )
    assert len(v.slippage_results) == 4
    slip_levels = [sr.slip_pct for sr in v.slippage_results]
    assert slip_levels == [0.001, 0.003, 0.005, 0.010]
