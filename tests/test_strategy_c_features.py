"""Feature engineering tests — Baseline A + Baseline B precision study features.

Baseline A (trend confirmation): taker_delta_norm, taker_delta_norm_z32,
oi_pct_change_z32, basis_z96, fr_close_z96.

Baseline B (precision-first event study) adds per-bar primitives cvd_delta,
basis_change, fr_spread, agg_u_oi_pct, plus z-scores cvd_delta_z32,
long_liq_z32, short_liq_z32, basis_change_z32, fr_spread_z96, agg_u_oi_pct_z32.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from data.strategy_c_dataset import StrategyCBar
from data.strategy_c_features import (
    StrategyCFeatureBar,
    compute_features,
    rolling_zscore,
)


# ── Test helpers ──────────────────────────────────────────────────────


def _make_bar(
    i: int,
    *,
    taker_buy_usd: float = 1e6,
    taker_sell_usd: float = 1e6,
    taker_delta_usd: float | None = None,
    oi_pct_change: float = 0.0,
    basis: float = 0.05,
    funding: float = 0.001,
    funding_oi_weighted: float = 0.001,
    long_liq_usd: float = 0.0,
    short_liq_usd: float = 0.0,
    cvd: float = 0.0,
    stablecoin_oi: float = 1e5,
) -> StrategyCBar:
    """Build a test bar. Timestamp = 2026-01-01 00:00 + i*15m."""
    if taker_delta_usd is None:
        taker_delta_usd = taker_buy_usd - taker_sell_usd
    return StrategyCBar(
        timestamp=datetime(2026, 1, 1) + timedelta(minutes=15 * i),
        open=70000.0,
        close=70000.0,
        volume=100.0,
        oi_close=6.0e9,
        oi_pct_change=oi_pct_change,
        funding=funding,
        long_liq_usd=long_liq_usd,
        short_liq_usd=short_liq_usd,
        liq_imbalance=0.0,
        taker_buy_usd=taker_buy_usd,
        taker_sell_usd=taker_sell_usd,
        taker_delta_usd=taker_delta_usd,
        cvd=cvd,
        basis=basis,
        funding_oi_weighted=funding_oi_weighted,
        stablecoin_oi=stablecoin_oi,
    )


# ── rolling_zscore ────────────────────────────────────────────────────


def test_rolling_zscore_first_n_minus_1_are_none() -> None:
    """Before the window is full, z-score is undefined."""
    z = rolling_zscore([1.0, 2.0, 3.0, 4.0, 5.0], window=3)
    assert z[0] is None
    assert z[1] is None
    assert z[2] is not None


def test_rolling_zscore_known_values() -> None:
    """Window=3 over [1,2,3]: mean=2, std=sqrt(2/3)≈0.8165, z=(3-2)/0.8165≈1.2247."""
    z = rolling_zscore([1.0, 2.0, 3.0, 4.0, 5.0], window=3)
    assert z[2] == pytest.approx(1.2247, abs=1e-3)
    assert z[3] == pytest.approx(1.2247, abs=1e-3)  # rolling window shifts: [2,3,4]
    assert z[4] == pytest.approx(1.2247, abs=1e-3)


def test_rolling_zscore_constant_series_is_zero() -> None:
    """A flat series has zero std — we return 0.0, not NaN."""
    z = rolling_zscore([5.0, 5.0, 5.0, 5.0, 5.0], window=3)
    assert z[2] == 0.0
    assert z[3] == 0.0
    assert z[4] == 0.0


def test_rolling_zscore_negative_deviation() -> None:
    """Value below the window mean produces a negative z."""
    z = rolling_zscore([3.0, 3.0, 3.0, 0.0], window=3)
    # window=[3,3,0], mean=2, var = ((3-2)^2+(3-2)^2+(0-2)^2)/3 = 6/3=2, std=sqrt(2)
    # z = (0-2)/sqrt(2) = -sqrt(2) ≈ -1.4142
    assert z[3] == pytest.approx(-1.4142, abs=1e-3)


# ── taker_delta_norm ──────────────────────────────────────────────────


def test_taker_delta_norm_basic() -> None:
    """taker_delta_norm = taker_delta / (buy + sell). (6e6-4e6) / 10e6 = 0.2."""
    bars = [_make_bar(0, taker_buy_usd=6e6, taker_sell_usd=4e6)]
    # Need enough bars to pad past z-score warmup? No — taker_delta_norm is per-bar, not rolling.
    # Just check the raw value is exposed (even if z-scores are None).
    feats = compute_features(bars, warmup=False)
    assert feats[0].taker_delta_norm == pytest.approx(0.2)


def test_taker_delta_norm_zero_volume_is_zero() -> None:
    """When no taker volume at all, norm defaults to 0.0 (not NaN)."""
    bars = [_make_bar(0, taker_buy_usd=0.0, taker_sell_usd=0.0, taker_delta_usd=0.0)]
    feats = compute_features(bars, warmup=False)
    assert feats[0].taker_delta_norm == 0.0


# ── compute_features end-to-end ───────────────────────────────────────


def test_compute_features_returns_all_baseline_a_and_b_fields() -> None:
    """Feature bars carry every Baseline A + B field after warmup."""
    # Need at least 96 bars so the z_96 window is full.
    bars = [
        _make_bar(
            i,
            taker_buy_usd=1e6 + i * 1e3,
            taker_sell_usd=1e6,
            long_liq_usd=1e3 + i * 10,
            short_liq_usd=2e3 + i * 20,
            cvd=i * 100.0,
            stablecoin_oi=1e5 + i * 50,
            basis=0.05 + i * 0.0001,
            funding=0.001 + i * 1e-5,
            funding_oi_weighted=0.001,
        )
        for i in range(100)
    ]
    feats = compute_features(bars)
    assert len(feats) == 5  # warmup drops first 95
    f = feats[0]
    assert isinstance(f, StrategyCFeatureBar)
    assert f.timestamp == bars[95].timestamp
    # Baseline A fields
    assert f.taker_delta_norm is not None
    assert f.taker_delta_norm_z32 is not None
    assert f.oi_pct_change_z32 is not None
    assert f.basis_z96 is not None
    assert f.fr_close_z96 is not None
    # Baseline B primitives
    assert f.cvd_delta is not None
    assert f.basis_change is not None
    assert f.fr_spread is not None
    assert f.agg_u_oi_pct is not None
    # Baseline B z-scores
    assert f.cvd_delta_z32 is not None
    assert f.long_liq_z32 is not None
    assert f.short_liq_z32 is not None
    assert f.basis_change_z32 is not None
    assert f.fr_spread_z96 is not None
    assert f.agg_u_oi_pct_z32 is not None


def test_cvd_delta_and_basis_change_are_diffs() -> None:
    """cvd_delta[i] = cvd[i] - cvd[i-1]; bar 0 has None."""
    bars = [_make_bar(i, cvd=i * 1000.0, basis=0.05 + i * 0.001) for i in range(3)]
    feats = compute_features(bars, warmup=False)
    assert feats[0].cvd_delta is None
    assert feats[0].basis_change is None
    assert feats[1].cvd_delta == pytest.approx(1000.0)
    assert feats[1].basis_change == pytest.approx(0.001)
    assert feats[2].cvd_delta == pytest.approx(1000.0)


def test_fr_spread_is_funding_minus_oi_weighted() -> None:
    bars = [_make_bar(0, funding=0.003, funding_oi_weighted=0.001)]
    feats = compute_features(bars, warmup=False)
    assert feats[0].fr_spread == pytest.approx(0.002)


def test_agg_u_oi_pct_is_stablecoin_oi_pct_change() -> None:
    bars = [
        _make_bar(0, stablecoin_oi=100.0),
        _make_bar(1, stablecoin_oi=110.0),
    ]
    feats = compute_features(bars, warmup=False)
    assert feats[0].agg_u_oi_pct is None
    assert feats[1].agg_u_oi_pct == pytest.approx(0.10)


def test_rolling_zscore_handles_none_in_window() -> None:
    """If any value in the window is None, z is None (diff-series warmup safety)."""
    # bar 0 has None diff → z over window starting at bar 0 must be None.
    z = rolling_zscore([None, 1.0, 2.0, 3.0], window=3)
    assert z[0] is None
    assert z[1] is None
    assert z[2] is None  # window contains None at index 0
    assert z[3] is not None  # window is [1.0, 2.0, 3.0], all clean


def test_compute_features_warmup_drops_incomplete_rows() -> None:
    """With warmup=True (default), rows before z_96 is computable are dropped."""
    bars = [_make_bar(i) for i in range(100)]
    feats = compute_features(bars)
    # First 95 rows dropped (z_96 needs 96 samples).
    assert len(feats) == 5
    assert feats[0].timestamp == bars[95].timestamp


def test_compute_features_noarmup_keeps_all_with_nones() -> None:
    """With warmup=False, all rows are returned but early z-scores are None."""
    bars = [_make_bar(i) for i in range(100)]
    feats = compute_features(bars, warmup=False)
    assert len(feats) == 100
    # First row: z-scores should be None (not enough history).
    assert feats[0].taker_delta_norm_z32 is None
    assert feats[0].basis_z96 is None
    # Last row: z-scores should be computable.
    assert feats[-1].taker_delta_norm_z32 is not None
    assert feats[-1].basis_z96 is not None
    assert feats[-1].cvd_delta_z32 is not None
