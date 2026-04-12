"""Tests for Strategy C Baseline A signal generator.

Baseline A (trend confirmation) — 4 conditions per side, all required:

    LONG  : taker_delta_norm_z32 >  1
            AND oi_pct_change_z32 > 0
            AND basis_z96         > 0
            AND fr_close_z96      < 2

    SHORT : taker_delta_norm_z32 < -1
            AND oi_pct_change_z32 < 0
            AND basis_z96         < 0
            AND fr_close_z96      > -2

Output: +1 (long), -1 (short), 0 (flat).
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from data.strategy_c_features import StrategyCFeatureBar
from strategies.strategy_c_baseline_a import baseline_a_signal, baseline_a_signals


def _feat(
    *,
    taker_delta_norm_z32: float | None = 0.0,
    oi_pct_change_z32: float | None = 0.0,
    basis_z96: float | None = 0.0,
    fr_close_z96: float | None = 0.0,
) -> StrategyCFeatureBar:
    return StrategyCFeatureBar(
        timestamp=datetime(2026, 2, 16),
        open=70000.0,
        close=70000.0,
        taker_delta_norm=0.0,
        cvd_delta=0.0,
        basis_change=0.0,
        fr_spread=0.0,
        agg_u_oi_pct=0.0,
        liq_imbalance=0.0,
        taker_delta_norm_z32=taker_delta_norm_z32,
        oi_pct_change_z32=oi_pct_change_z32,
        basis_z96=basis_z96,
        fr_close_z96=fr_close_z96,
        cvd_delta_z32=0.0,
        long_liq_z32=0.0,
        short_liq_z32=0.0,
        basis_change_z32=0.0,
        fr_spread_z96=0.0,
        agg_u_oi_pct_z32=0.0,
    )


# ── single-bar signal ────────────────────────────────────────────────


def test_long_when_all_four_long_conditions_met() -> None:
    f = _feat(
        taker_delta_norm_z32=1.5,
        oi_pct_change_z32=0.4,
        basis_z96=0.8,
        fr_close_z96=0.5,
    )
    assert baseline_a_signal(f) == 1


def test_short_when_all_four_short_conditions_met() -> None:
    f = _feat(
        taker_delta_norm_z32=-1.5,
        oi_pct_change_z32=-0.4,
        basis_z96=-0.8,
        fr_close_z96=-0.5,
    )
    assert baseline_a_signal(f) == -1


def test_flat_when_taker_delta_not_strong_enough_for_long() -> None:
    """taker_delta_norm_z32 must be STRICTLY > 1 (not >= 1)."""
    f = _feat(
        taker_delta_norm_z32=1.0,  # not > 1
        oi_pct_change_z32=0.4,
        basis_z96=0.8,
        fr_close_z96=0.5,
    )
    assert baseline_a_signal(f) == 0


def test_flat_when_oi_pct_zero_for_long() -> None:
    """oi_pct_change_z32 must be > 0, not >= 0."""
    f = _feat(
        taker_delta_norm_z32=1.5,
        oi_pct_change_z32=0.0,
        basis_z96=0.8,
        fr_close_z96=0.5,
    )
    assert baseline_a_signal(f) == 0


def test_flat_when_basis_negative_blocks_long() -> None:
    f = _feat(
        taker_delta_norm_z32=1.5,
        oi_pct_change_z32=0.4,
        basis_z96=-0.01,
        fr_close_z96=0.5,
    )
    assert baseline_a_signal(f) == 0


def test_flat_when_funding_too_hot_blocks_long() -> None:
    """fr_close_z96 must be < 2 for long (avoid entering an overheated market)."""
    f = _feat(
        taker_delta_norm_z32=1.5,
        oi_pct_change_z32=0.4,
        basis_z96=0.8,
        fr_close_z96=2.5,
    )
    assert baseline_a_signal(f) == 0


def test_flat_when_funding_too_cold_blocks_short() -> None:
    """fr_close_z96 must be > -2 for short."""
    f = _feat(
        taker_delta_norm_z32=-1.5,
        oi_pct_change_z32=-0.4,
        basis_z96=-0.8,
        fr_close_z96=-2.5,
    )
    assert baseline_a_signal(f) == 0


def test_flat_when_any_zscore_is_none() -> None:
    """During warmup, some z-scores are None — always flat."""
    f = _feat(
        taker_delta_norm_z32=None,
        oi_pct_change_z32=0.4,
        basis_z96=0.8,
        fr_close_z96=0.5,
    )
    assert baseline_a_signal(f) == 0


# ── batch signals across a series ─────────────────────────────────────


def _seq(features: list[StrategyCFeatureBar]) -> list[StrategyCFeatureBar]:
    """Attach distinct timestamps so the batch helper can index them."""
    out = []
    for i, f in enumerate(features):
        out.append(
            StrategyCFeatureBar(
                timestamp=datetime(2026, 2, 16) + timedelta(minutes=15 * i),
                open=f.open,
                close=f.close,
                taker_delta_norm=f.taker_delta_norm,
                cvd_delta=0.0,
                basis_change=0.0,
                fr_spread=0.0,
                agg_u_oi_pct=0.0,
                liq_imbalance=0.0,
                taker_delta_norm_z32=f.taker_delta_norm_z32,
                oi_pct_change_z32=f.oi_pct_change_z32,
                basis_z96=f.basis_z96,
                fr_close_z96=f.fr_close_z96,
                cvd_delta_z32=0.0,
                long_liq_z32=0.0,
                short_liq_z32=0.0,
                basis_change_z32=0.0,
                fr_spread_z96=0.0,
                agg_u_oi_pct_z32=0.0,
            )
        )
    return out


def test_baseline_a_signals_mixed_sequence() -> None:
    """Series with one long, one flat, one short bar."""
    feats = _seq([
        _feat(taker_delta_norm_z32=1.5, oi_pct_change_z32=0.4, basis_z96=0.5, fr_close_z96=0.0),   # long
        _feat(taker_delta_norm_z32=0.5, oi_pct_change_z32=0.4, basis_z96=0.5, fr_close_z96=0.0),   # flat (taker<=1)
        _feat(taker_delta_norm_z32=-1.5, oi_pct_change_z32=-0.4, basis_z96=-0.5, fr_close_z96=0.0),  # short
    ])
    signals = baseline_a_signals(feats)
    assert signals == [1, 0, -1]
