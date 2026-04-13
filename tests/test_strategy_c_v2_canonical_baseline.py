"""Tests for the Phase 8 canonical baseline single-source-of-truth.

These tests freeze the 6 canonical cells' numbers. Any change to a
metric value here requires a re-run of the walk-forward and an
explicit update to `strategy_c_v2_phase8_canonical_baseline.md`.

The canonical numbers (at portfolio_allocation=1.0, the pure
strategy-level leveraged futures result):

    Cell                          | Role    | L | frac  | Trades | OOS Return | Max DD | PF   | Worst
    ------------------------------|---------|--:|------:|-------:|-----------:|-------:|-----:|------:
    D1_long_primary               | primary |2x | 1.333 |     73 |  +143.45%  | 12.97% | 2.23 | -5.68%
    D1_long_dynamic               | shadow  |2x |[0.667,2.000]| 73 |+164.32%| 14.81% | 2.17 | -7.74%
    D1_long_dynamic_adaptive      | shadow  |2x |[0.667,2.000]| 64 |+204.55%| 16.36% | 2.35 | -7.74%
    D1_long_frac2_shadow          | shadow  |2x | 2.000 |     73 |  +259.13% | 19.09% | 2.23 | -8.51%
    C_long_backup                 | backup  |2x | 1.000 |    178 |  +106.26% | 18.10% | 1.70 | -6.62%
    C_long_dynamic                | shadow  |2x |[0.500,1.500]|178|+135.97%| 17.08% | 1.79 | -7.23%

The three strictly-separated concepts must stay orthogonal:
  - exchange_leverage (2x for all cells)
  - actual_frac (strategy's effective notional fraction of sleeve)
  - portfolio_allocation_default (1.0 for canonical metrics)
"""
from __future__ import annotations

import pytest

from strategies.strategy_c_v2_canonical_baseline import (
    CANONICAL_CELLS,
    C_LONG_BACKUP,
    C_LONG_DYNAMIC,
    CanonicalCell,
    CanonicalCellConfig,
    CanonicalMetrics,
    D1_LONG_DYNAMIC,
    D1_LONG_DYNAMIC_ADAPTIVE,
    D1_LONG_FRAC2_SHADOW,
    D1_LONG_PRIMARY,
    LiquidationSafety,
    apply_portfolio_allocation,
    compute_expected_delta,
    compute_liquidation_safety,
    get_backup_cell,
    get_canonical_cell,
    get_primary_cell,
    list_canonical_cell_ids,
    list_cells_by_role,
    list_shadow_cells,
)


# ── registry shape ──────────────────────────────────────────────────


def test_registry_contains_exactly_six_cells() -> None:
    assert len(CANONICAL_CELLS) == 6


def test_registry_has_all_deployment_cell_ids() -> None:
    assert set(CANONICAL_CELLS.keys()) == {
        "D1_long_primary",
        "D1_long_dynamic",
        "D1_long_dynamic_adaptive",
        "D1_long_frac2_shadow",
        "C_long_backup",
        "C_long_dynamic",
    }


def test_list_canonical_cell_ids_returns_sorted() -> None:
    ids = list_canonical_cell_ids()
    assert ids == sorted(ids)
    assert len(ids) == 6


def test_get_canonical_cell_returns_correct_record() -> None:
    cell = get_canonical_cell("D1_long_primary")
    assert cell.cell_id == "D1_long_primary"
    assert cell is D1_LONG_PRIMARY


def test_get_canonical_cell_unknown_raises_keyerror_with_hint() -> None:
    with pytest.raises(KeyError, match="Unknown canonical cell"):
        get_canonical_cell("D1_long_bogus")


def test_get_primary_cell_returns_d1_long_primary() -> None:
    assert get_primary_cell().cell_id == "D1_long_primary"
    assert get_primary_cell().role == "primary"


def test_get_backup_cell_returns_c_long_backup() -> None:
    assert get_backup_cell().cell_id == "C_long_backup"
    assert get_backup_cell().role == "backup"


def test_list_shadow_cells_returns_four_shadows() -> None:
    shadows = list_shadow_cells()
    shadow_ids = {c.cell_id for c in shadows}
    assert shadow_ids == {
        "D1_long_dynamic",
        "D1_long_dynamic_adaptive",
        "D1_long_frac2_shadow",
        "C_long_dynamic",
    }
    for s in shadows:
        assert s.role == "shadow"


def test_list_cells_by_role() -> None:
    assert len(list_cells_by_role("primary")) == 1
    assert len(list_cells_by_role("backup")) == 1
    assert len(list_cells_by_role("shadow")) == 4


# ── strict separation: three concepts never conflated ──────────────


def test_every_cell_records_exchange_leverage_separately() -> None:
    """Every cell must have `exchange_leverage` as a distinct field."""
    for cell in CANONICAL_CELLS.values():
        assert hasattr(cell.config, "exchange_leverage")
        assert cell.config.exchange_leverage == 2.0


def test_every_cell_records_actual_frac_separately() -> None:
    for cell in CANONICAL_CELLS.values():
        assert hasattr(cell.config, "actual_frac")
        assert cell.config.actual_frac > 0


def test_every_cell_records_portfolio_allocation_separately() -> None:
    """Canonical metrics are at portfolio_allocation=1.0 (full sleeve)."""
    for cell in CANONICAL_CELLS.values():
        assert hasattr(cell.config, "portfolio_allocation_default")
        assert cell.config.portfolio_allocation_default == 1.0


def test_actual_frac_never_exceeds_exchange_leverage() -> None:
    for cell in CANONICAL_CELLS.values():
        assert cell.config.actual_frac <= cell.config.exchange_leverage


def test_config_rejects_actual_frac_above_leverage() -> None:
    with pytest.raises(ValueError, match="exceeds"):
        CanonicalCellConfig(
            signal_family="rsi_only",
            rsi_period=20,
            side="long",
            hold_bars=11,
            stop_loss_pct=0.015,
            stop_semantics="strategy_close_stop",
            stop_trigger="close",
            risk_per_trade=0.02,
            exchange_leverage=2.0,
            actual_frac=3.0,  # > 2.0 is impossible in isolated mode
        )


def test_config_rejects_negative_exchange_leverage() -> None:
    with pytest.raises(ValueError, match="exchange_leverage"):
        CanonicalCellConfig(
            signal_family="rsi_only",
            rsi_period=20,
            side="long",
            hold_bars=11,
            stop_loss_pct=0.015,
            stop_semantics="strategy_close_stop",
            stop_trigger="close",
            risk_per_trade=0.02,
            exchange_leverage=-1.0,
            actual_frac=1.0,
        )


def test_config_rejects_bad_portfolio_allocation() -> None:
    with pytest.raises(ValueError, match="portfolio_allocation_default"):
        CanonicalCellConfig(
            signal_family="rsi_only",
            rsi_period=20,
            side="long",
            hold_bars=11,
            stop_loss_pct=0.015,
            stop_semantics="strategy_close_stop",
            stop_trigger="close",
            risk_per_trade=0.02,
            exchange_leverage=2.0,
            actual_frac=1.0,
            portfolio_allocation_default=1.5,  # > 1
        )


# ── D1_long_primary ─────────────────────────────────────────────────


def test_d1_long_primary_metrics_are_canonical() -> None:
    """Freezes D1_long_primary numbers. See Phase 8 baseline report §4."""
    m = D1_LONG_PRIMARY.metrics
    assert m.num_trades == 73
    assert m.oos_return == pytest.approx(1.4345, abs=1e-4)
    assert m.max_dd == pytest.approx(0.1297, abs=1e-4)
    assert m.profit_factor == pytest.approx(2.23, abs=1e-2)
    assert m.worst_trade_pnl == pytest.approx(-0.0568, abs=1e-4)
    assert m.worst_adverse_move == pytest.approx(0.0651, abs=1e-4)
    assert m.positive_windows == 7
    assert m.total_windows == 8
    assert m.stops_fired == 22


def test_d1_long_primary_config_is_canonical() -> None:
    c = D1_LONG_PRIMARY.config
    assert c.signal_family == "rsi_only"
    assert c.rsi_period == 20
    assert c.side == "long"
    assert c.hold_bars == 11
    assert c.stop_loss_pct == 0.015
    assert c.stop_semantics == "strategy_close_stop"
    assert c.stop_trigger == "close"
    assert c.risk_per_trade == 0.02
    assert c.exchange_leverage == 2.0
    assert c.actual_frac == pytest.approx(1.3333333, abs=1e-5)
    assert c.portfolio_allocation_default == 1.0
    assert c.use_dynamic_sizing is False
    assert c.use_adaptive_hold is False
    assert c.fee_per_side == 0.0005
    assert c.slip_per_side == 0.0001


def test_d1_long_primary_round_trip_cost_is_0_0012() -> None:
    c = D1_LONG_PRIMARY.config
    assert c.round_trip_cost_per_frac == pytest.approx(0.0012, abs=1e-6)


def test_d1_long_primary_is_primary_role() -> None:
    assert D1_LONG_PRIMARY.role == "primary"


# ── D1_long_dynamic ─────────────────────────────────────────────────


def test_d1_long_dynamic_metrics_are_canonical() -> None:
    m = D1_LONG_DYNAMIC.metrics
    assert m.num_trades == 73
    assert m.oos_return == pytest.approx(1.6432, abs=1e-4)
    assert m.max_dd == pytest.approx(0.1481, abs=1e-4)
    assert m.profit_factor == pytest.approx(2.17, abs=1e-2)
    assert m.worst_trade_pnl == pytest.approx(-0.0774, abs=1e-4)
    assert m.positive_windows == 7


def test_d1_long_dynamic_flags_dynamic_sizing_on_but_adaptive_off() -> None:
    c = D1_LONG_DYNAMIC.config
    assert c.use_dynamic_sizing is True
    assert c.use_adaptive_hold is False


def test_d1_long_dynamic_actual_frac_range_uses_multiplier() -> None:
    """Dynamic sizing multiplier [0.5, 1.5] → actual_frac [0.667, 2.000]."""
    c = D1_LONG_DYNAMIC.config
    assert c.actual_frac_min == pytest.approx(c.actual_frac * 0.5, abs=1e-6)
    assert c.actual_frac_max == pytest.approx(c.actual_frac * 1.5, abs=1e-6)
    # At max, fully margined on 2x
    assert c.actual_frac_max == pytest.approx(2.0, abs=1e-3)


# ── D1_long_dynamic_adaptive ────────────────────────────────────────


def test_d1_long_dynamic_adaptive_metrics_are_canonical() -> None:
    m = D1_LONG_DYNAMIC_ADAPTIVE.metrics
    assert m.num_trades == 64
    assert m.oos_return == pytest.approx(2.0455, abs=1e-4)
    assert m.max_dd == pytest.approx(0.1636, abs=1e-4)
    assert m.profit_factor == pytest.approx(2.35, abs=1e-2)
    assert m.worst_trade_pnl == pytest.approx(-0.0774, abs=1e-4)
    assert m.positive_windows == 6
    assert m.stops_fired == 24


def test_d1_long_dynamic_adaptive_flags_both_modifiers_on() -> None:
    c = D1_LONG_DYNAMIC_ADAPTIVE.config
    assert c.use_dynamic_sizing is True
    assert c.use_adaptive_hold is True


# ── D1_long_frac2_shadow (NEW high-return shadow sleeve) ────────────


def test_d1_long_frac2_shadow_is_registered() -> None:
    assert "D1_long_frac2_shadow" in CANONICAL_CELLS
    assert D1_LONG_FRAC2_SHADOW.role == "shadow"


def test_d1_long_frac2_shadow_metrics_are_canonical() -> None:
    """Freezes the fresh canonical walk-forward run 2026-04-12.

    D1_long signal stream + fixed frac=2.0 + strategy_close_stop
    at 1.5%, 11-bar hold, 2x exchange leverage.
    """
    m = D1_LONG_FRAC2_SHADOW.metrics
    assert m.num_trades == 73
    assert m.oos_return == pytest.approx(2.5913, abs=1e-4)
    assert m.max_dd == pytest.approx(0.1909, abs=1e-4)
    assert m.profit_factor == pytest.approx(2.23, abs=1e-2)
    assert m.worst_trade_pnl == pytest.approx(-0.0851, abs=1e-4)
    assert m.worst_adverse_move == pytest.approx(0.0651, abs=1e-4)
    assert m.positive_windows == 7
    assert m.stops_fired == 22


def test_d1_long_frac2_shadow_config_uses_max_leverage_frac() -> None:
    c = D1_LONG_FRAC2_SHADOW.config
    assert c.exchange_leverage == 2.0
    assert c.actual_frac == pytest.approx(2.0, abs=1e-6)
    # Fully margined: actual_frac equals the exchange leverage cap
    assert c.actual_frac == c.exchange_leverage


def test_d1_long_frac2_shadow_has_no_dynamic_or_adaptive_flags() -> None:
    c = D1_LONG_FRAC2_SHADOW.config
    assert c.use_dynamic_sizing is False
    assert c.use_adaptive_hold is False


def test_d1_long_frac2_shadow_shares_signal_stream_with_primary() -> None:
    """Same signals, same hold, same stop — only frac differs."""
    p = D1_LONG_PRIMARY.config
    s = D1_LONG_FRAC2_SHADOW.config
    assert s.signal_family == p.signal_family
    assert s.rsi_period == p.rsi_period
    assert s.hold_bars == p.hold_bars
    assert s.stop_loss_pct == p.stop_loss_pct
    assert s.stop_semantics == p.stop_semantics
    # Trade count identical because signals are identical
    assert D1_LONG_FRAC2_SHADOW.metrics.num_trades == D1_LONG_PRIMARY.metrics.num_trades
    # Worst adverse move identical because it's a signal-level property
    assert D1_LONG_FRAC2_SHADOW.metrics.worst_adverse_move == D1_LONG_PRIMARY.metrics.worst_adverse_move


def test_d1_long_frac2_shadow_profit_factor_equals_primary() -> None:
    """PF is a win/loss RATIO so it's invariant to frac scaling.

    Frac scales all trade PnLs by the same factor, so gross_wins and
    gross_losses scale by the same factor, and PF (= gross_wins /
    gross_losses) is unchanged.
    """
    assert D1_LONG_FRAC2_SHADOW.metrics.profit_factor == pytest.approx(
        D1_LONG_PRIMARY.metrics.profit_factor, abs=1e-2
    )


def test_d1_long_frac2_shadow_delta_vs_primary() -> None:
    """Expected deltas from fresh run: +115.68pp return, +6.12pp DD."""
    delta = compute_expected_delta("D1_long_frac2_shadow")
    assert delta["delta_return"] == pytest.approx(1.1568, abs=1e-4)
    assert delta["delta_dd"] == pytest.approx(0.0612, abs=1e-4)
    assert delta["delta_num_trades"] == 0.0


# ── C_long_backup ──────────────────────────────────────────────────


def test_c_long_backup_metrics_are_canonical() -> None:
    m = C_LONG_BACKUP.metrics
    assert m.num_trades == 178
    assert m.oos_return == pytest.approx(1.0626, abs=1e-4)
    assert m.max_dd == pytest.approx(0.1810, abs=1e-4)
    assert m.profit_factor == pytest.approx(1.70, abs=1e-2)
    assert m.worst_trade_pnl == pytest.approx(-0.0662, abs=1e-4)
    assert m.positive_windows == 6
    assert m.stops_fired == 17


def test_c_long_backup_config_is_canonical() -> None:
    c = C_LONG_BACKUP.config
    assert c.signal_family == "rsi_and_macd"
    assert c.rsi_period == 14
    assert c.hold_bars == 4
    assert c.stop_loss_pct == 0.02
    assert c.actual_frac == pytest.approx(1.0, abs=1e-6)
    assert c.exchange_leverage == 2.0
    assert c.use_dynamic_sizing is False
    assert c.use_adaptive_hold is False
    assert C_LONG_BACKUP.role == "backup"


# ── C_long_dynamic ─────────────────────────────────────────────────


def test_c_long_dynamic_metrics_are_canonical() -> None:
    m = C_LONG_DYNAMIC.metrics
    assert m.num_trades == 178
    assert m.oos_return == pytest.approx(1.3597, abs=1e-4)
    assert m.max_dd == pytest.approx(0.1708, abs=1e-4)
    assert m.profit_factor == pytest.approx(1.79, abs=1e-2)
    assert m.worst_trade_pnl == pytest.approx(-0.0723, abs=1e-4)
    assert m.positive_windows == 6


def test_c_long_dynamic_actual_frac_range() -> None:
    """Dynamic [0.5, 1.5] on base 1.0 → [0.5, 1.5]."""
    c = C_LONG_DYNAMIC.config
    assert c.actual_frac_min == pytest.approx(0.5, abs=1e-6)
    assert c.actual_frac_max == pytest.approx(1.5, abs=1e-6)


# ── liquidation safety ─────────────────────────────────────────────


def test_liquidation_safety_2x_leverage() -> None:
    """Liquidation distance on 2x isolated is 1/2 = 50%."""
    cell = D1_LONG_PRIMARY
    safety = compute_liquidation_safety(cell.config, cell.metrics)
    assert safety.liquidation_adverse_move == pytest.approx(0.5, abs=1e-6)


def test_liquidation_safety_buffer_multiple_d1_long_primary() -> None:
    """D1_long_primary worst_adverse 6.51% vs liq distance 50%.

    Buffer multiple = 0.50 / 0.0651 ≈ 7.68x
    """
    cell = D1_LONG_PRIMARY
    safety = compute_liquidation_safety(cell.config, cell.metrics)
    assert safety.buffer_multiple == pytest.approx(7.68, abs=1e-2)


def test_liquidation_safety_all_cells_are_safe() -> None:
    """Every canonical cell must have ≥3x buffer to count as safe.

    This enforces the hard-ceiling rule from Phase 6 tail-event stress.
    """
    for cell in CANONICAL_CELLS.values():
        safety = cell.liquidation_safety
        assert safety.is_safe, (
            f"{cell.cell_id} has buffer_multiple={safety.buffer_multiple:.2f}x, "
            f"below the 3x safety threshold"
        )


def test_liquidation_safety_frac2_shadow_has_same_worst_adverse() -> None:
    """frac=2.0 has the SAME worst adverse as frac=1.333 because
    worst_adverse_move is a price-movement property, not a sizing
    property. Therefore frac2_shadow has the same liquidation buffer
    as D1_long_primary in terms of PRICE distance — they just suffer
    different equity drawdowns at the same price move.
    """
    p = D1_LONG_PRIMARY.liquidation_safety
    f = D1_LONG_FRAC2_SHADOW.liquidation_safety
    assert p.worst_adverse_move == f.worst_adverse_move
    assert p.liquidation_adverse_move == f.liquidation_adverse_move
    assert p.buffer_multiple == pytest.approx(f.buffer_multiple, abs=1e-6)


def test_liquidation_safety_summary_str_format() -> None:
    s = LiquidationSafety(
        liquidation_adverse_move=0.5,
        worst_adverse_move=0.0651,
        buffer_pp=0.4349,
        buffer_multiple=7.68,
    )
    assert "liq@50%" in s.summary_str()
    assert "worst_adv=6.51%" in s.summary_str()
    assert "buffer=7.68x" in s.summary_str()


# ── portfolio allocation layer (strictly separate) ─────────────────


def test_apply_portfolio_allocation_scales_return_linearly() -> None:
    """At 0.5 allocation, approximated return is half the canonical."""
    scaled = apply_portfolio_allocation(D1_LONG_PRIMARY.metrics, 0.5)
    assert scaled["allocation"] == 0.5
    assert scaled["scaled_oos_return_approx"] == pytest.approx(
        1.4345 * 0.5, abs=1e-6
    )
    assert scaled["scaled_max_dd_approx"] == pytest.approx(
        0.1297 * 0.5, abs=1e-6
    )


def test_apply_portfolio_allocation_preserves_non_scalable_metrics() -> None:
    """num_trades, PF, positive_windows, worst_adverse are strategy
    properties, not allocation-scalable."""
    scaled = apply_portfolio_allocation(D1_LONG_PRIMARY.metrics, 0.25)
    assert scaled["num_trades"] == 73
    assert scaled["profit_factor"] == pytest.approx(2.23, abs=1e-2)
    assert scaled["worst_adverse_move"] == pytest.approx(0.0651, abs=1e-4)
    assert scaled["positive_windows"] == 7
    assert scaled["stops_fired"] == 22


def test_apply_portfolio_allocation_rejects_bad_input() -> None:
    with pytest.raises(ValueError, match="allocation must be in"):
        apply_portfolio_allocation(D1_LONG_PRIMARY.metrics, 0.0)
    with pytest.raises(ValueError, match="allocation must be in"):
        apply_portfolio_allocation(D1_LONG_PRIMARY.metrics, 1.5)


def test_apply_portfolio_allocation_at_full_is_identity() -> None:
    """Allocation=1.0 returns scaled values equal to the canonical."""
    scaled = apply_portfolio_allocation(D1_LONG_PRIMARY.metrics, 1.0)
    assert scaled["scaled_oos_return_approx"] == pytest.approx(
        D1_LONG_PRIMARY.metrics.oos_return, abs=1e-6
    )


# ── delta math ─────────────────────────────────────────────────────


def test_d1_long_dynamic_expected_delta_vs_primary() -> None:
    delta = compute_expected_delta("D1_long_dynamic")
    assert delta["delta_return"] == pytest.approx(0.2087, abs=1e-4)
    assert delta["delta_dd"] == pytest.approx(0.0184, abs=1e-4)
    assert delta["delta_num_trades"] == 0.0


def test_d1_long_dynamic_adaptive_expected_delta_vs_primary() -> None:
    delta = compute_expected_delta("D1_long_dynamic_adaptive")
    assert delta["delta_return"] == pytest.approx(0.6110, abs=1e-4)
    assert delta["delta_dd"] == pytest.approx(0.0339, abs=1e-4)
    assert delta["delta_num_trades"] == -9.0


def test_c_long_dynamic_expected_delta_vs_backup() -> None:
    delta = compute_expected_delta("C_long_dynamic")
    assert delta["delta_return"] == pytest.approx(0.2971, abs=1e-4)
    assert delta["delta_dd"] == pytest.approx(-0.0102, abs=1e-4)
    assert delta["delta_num_trades"] == 0.0


def test_expected_delta_can_override_baseline() -> None:
    delta = compute_expected_delta(
        "D1_long_frac2_shadow",
        baseline_id="D1_long_dynamic",
    )
    # +259.13% - +164.32% = +94.81 pp
    assert delta["delta_return"] == pytest.approx(0.9481, abs=1e-4)


def test_expected_delta_unknown_cell_raises() -> None:
    with pytest.raises(KeyError):
        compute_expected_delta("bogus")


def test_expected_delta_bad_baseline_raises() -> None:
    with pytest.raises(KeyError):
        compute_expected_delta("D1_long_primary", baseline_id="bogus")


# ── metrics helpers ─────────────────────────────────────────────────


def test_metrics_return_pct_str_matches_headline_format() -> None:
    assert D1_LONG_PRIMARY.metrics.return_pct_str() == "+143.45%"
    assert D1_LONG_FRAC2_SHADOW.metrics.return_pct_str() == "+259.13%"


def test_metrics_dd_pct_str_matches_headline_format() -> None:
    assert D1_LONG_PRIMARY.metrics.dd_pct_str() == "12.97%"
    assert D1_LONG_FRAC2_SHADOW.metrics.dd_pct_str() == "19.09%"


def test_metrics_worst_trade_pct_str_matches_headline_format() -> None:
    assert D1_LONG_PRIMARY.metrics.worst_trade_pct_str() == "-5.68%"
    assert D1_LONG_FRAC2_SHADOW.metrics.worst_trade_pct_str() == "-8.51%"


def test_metrics_positive_window_ratio() -> None:
    assert D1_LONG_PRIMARY.metrics.positive_window_ratio == pytest.approx(0.875)
    assert D1_LONG_DYNAMIC_ADAPTIVE.metrics.positive_window_ratio == pytest.approx(0.75)


# ── config helpers ─────────────────────────────────────────────────


def test_config_sleeve_label_format() -> None:
    assert D1_LONG_PRIMARY.config.sleeve_label == "2x leveraged perpetual futures sleeve"


def test_config_stop_config_str_format() -> None:
    assert D1_LONG_PRIMARY.config.stop_config_str == "1.5% / strategy_close_stop"
    assert C_LONG_BACKUP.config.stop_config_str == "2% / strategy_close_stop"


# ── provenance ──────────────────────────────────────────────────────


def test_all_cells_cite_baseline_report() -> None:
    for cell in CANONICAL_CELLS.values():
        assert cell.source_report == "strategy_c_v2_phase8_canonical_baseline.md"


def test_all_cells_measured_on_canonical_date() -> None:
    for cell in CANONICAL_CELLS.values():
        assert cell.measured_at == "2026-04-12"


def test_d1_long_primary_notes_record_phase6_fabrication() -> None:
    notes = D1_LONG_PRIMARY.notes
    assert "+173.06%" in notes
    assert "fabricated" in notes.lower() or "does not correspond" in notes.lower()


# ── rejection of phase 6 fabrication ────────────────────────────────


def test_d1_long_primary_return_is_not_phase6_recommendation() -> None:
    """Guard against any future edit that tries to restore +173.06%."""
    assert D1_LONG_PRIMARY.metrics.oos_return != pytest.approx(1.7306, abs=1e-4)
    assert D1_LONG_PRIMARY.metrics.max_dd != pytest.approx(0.0927, abs=1e-4)


def test_d1_long_primary_pf_is_not_phase6_recommendation() -> None:
    """Guard: the Phase 6 recommendation said PF 2.48; canonical is 2.23."""
    assert D1_LONG_PRIMARY.metrics.profit_factor != pytest.approx(2.48, abs=1e-2)
