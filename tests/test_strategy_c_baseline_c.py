"""Tests for Strategy C Baseline C — return/frequency balanced scorers.

Three independent score families, one shared signal emitter:

    REVERSAL (fade cascades, expect mean reversion)
        long_rev_score  = long_liq_z32                              (trigger)
                          - taker_delta_norm_z32                    (flow still selling)
                          - cvd_delta_z32
                          - basis_change_z32                         (premium collapsing)
                          - oi_pct_change_z32                        (OI unwinding)
                          + 0.5 * liq_imbalance                      (long_liq dominates)
                          - 0.5 * abs(fr_spread_z96)                 (funding-sanity penalty)

        short_rev_score = short_liq_z32
                          + taker_delta_norm_z32                    (flow still buying)
                          + cvd_delta_z32
                          + basis_change_z32                         (premium overheating)
                          + oi_pct_change_z32                        (OI blowing out)
                          - 0.5 * liq_imbalance                      (short_liq dominates)
                          - 0.5 * abs(fr_spread_z96)

    CONTINUATION (ride cascades, momentum ignition)
        long_cont_score  = short_liq_z32                            (shorts wiped → rip up)
                           + taker_delta_norm_z32                    (buyers confirm)
                           + cvd_delta_z32
                           + basis_change_z32
                           + agg_u_oi_pct_z32                        (stablecoin OI in)
                           - 0.5 * abs(fr_spread_z96)

        short_cont_score = long_liq_z32                             (longs wiped → rip down)
                           - taker_delta_norm_z32
                           - cvd_delta_z32
                           - basis_change_z32
                           - agg_u_oi_pct_z32
                           - 0.5 * abs(fr_spread_z96)

    HYBRID (regime switch via |fr_close_z96|)
        If |fr_close_z96| >= stress_threshold → use reversal scores.
        Else → use continuation scores.
        The stress_threshold is a Baseline C hyperparameter (default 1.0).

Signal emission is shared across modes:
    - Bar flat if any score is None (warmup).
    - Long fires if long_score >= long_threshold AND long_score > short_score.
    - Short fires if short_score >= short_threshold AND short_score > long_score.
    - Ties → flat.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from data.strategy_c_features import StrategyCFeatureBar
from strategies.strategy_c_baseline_c import (
    baseline_c_signals,
    continuation_long_score,
    continuation_short_score,
    hybrid_long_score,
    hybrid_short_score,
    reversal_long_score,
    reversal_short_score,
)


def _feat(
    i: int = 0,
    *,
    long_liq_z32: float | None = 0.0,
    short_liq_z32: float | None = 0.0,
    taker_delta_norm_z32: float | None = 0.0,
    cvd_delta_z32: float | None = 0.0,
    basis_change_z32: float | None = 0.0,
    oi_pct_change_z32: float | None = 0.0,
    agg_u_oi_pct_z32: float | None = 0.0,
    fr_spread_z96: float | None = 0.0,
    fr_close_z96: float | None = 0.0,
    liq_imbalance: float = 0.0,
) -> StrategyCFeatureBar:
    return StrategyCFeatureBar(
        timestamp=datetime(2026, 2, 16) + timedelta(minutes=15 * i),
        open=100.0,
        close=100.0,
        taker_delta_norm=0.0,
        cvd_delta=0.0,
        basis_change=0.0,
        fr_spread=0.0,
        agg_u_oi_pct=0.0,
        liq_imbalance=liq_imbalance,
        taker_delta_norm_z32=taker_delta_norm_z32,
        oi_pct_change_z32=oi_pct_change_z32,
        basis_z96=0.0,
        fr_close_z96=fr_close_z96,
        cvd_delta_z32=cvd_delta_z32,
        long_liq_z32=long_liq_z32,
        short_liq_z32=short_liq_z32,
        basis_change_z32=basis_change_z32,
        fr_spread_z96=fr_spread_z96,
        agg_u_oi_pct_z32=agg_u_oi_pct_z32,
    )


# ── Reversal scores ──────────────────────────────────────────────────


def test_reversal_long_score_sums_terms() -> None:
    """long_rev = long_liq - taker - cvd - basis - oi + 0.5*imbalance - 0.5*|fr_spread|"""
    f = _feat(
        long_liq_z32=3.0,
        taker_delta_norm_z32=-1.0,     # flow selling → -(-1) = +1
        cvd_delta_z32=-0.5,            # -(-0.5) = +0.5
        basis_change_z32=-0.2,         # -(-0.2) = +0.2
        oi_pct_change_z32=-0.3,        # -(-0.3) = +0.3
        liq_imbalance=0.4,             # +0.5*0.4 = +0.2
        fr_spread_z96=1.0,             # -0.5*1 = -0.5
    )
    # 3 + 1 + 0.5 + 0.2 + 0.3 + 0.2 - 0.5 = 4.7
    assert reversal_long_score(f) == pytest.approx(4.7)


def test_reversal_short_score_mirrors_long() -> None:
    f = _feat(
        short_liq_z32=3.0,
        taker_delta_norm_z32=1.0,       # flow buying → +1
        cvd_delta_z32=0.5,              # +0.5
        basis_change_z32=0.2,           # +0.2
        oi_pct_change_z32=0.3,          # +0.3
        liq_imbalance=-0.4,             # -0.5*(-0.4) = +0.2
        fr_spread_z96=-1.0,             # -0.5*1 = -0.5
    )
    # 3 + 1 + 0.5 + 0.2 + 0.3 + 0.2 - 0.5 = 4.7
    assert reversal_short_score(f) == pytest.approx(4.7)


def test_reversal_scores_return_none_on_warmup() -> None:
    f = _feat(long_liq_z32=None)
    assert reversal_long_score(f) is None
    assert reversal_short_score(f) is None


def test_reversal_scores_tolerate_none_cvd_when_include_cvd_false() -> None:
    f = _feat(long_liq_z32=2.0, cvd_delta_z32=None)
    assert reversal_long_score(f, include_cvd=True) is None
    assert reversal_long_score(f, include_cvd=False) == pytest.approx(2.0)


# ── Continuation scores ──────────────────────────────────────────────


def test_continuation_long_score_triggers_on_short_liq_spike() -> None:
    """Shorts wiped → expected rip-up → LONG. Confirmation = buyers + basis + OI."""
    f = _feat(
        short_liq_z32=3.0,               # trigger
        taker_delta_norm_z32=1.0,         # buyers dominant
        cvd_delta_z32=0.5,
        basis_change_z32=0.2,             # basis rising
        agg_u_oi_pct_z32=0.8,             # stablecoin OI flowing in
        fr_spread_z96=1.0,                # -0.5*|1| = -0.5
    )
    # 3 + 1 + 0.5 + 0.2 + 0.8 - 0.5 = 5.0
    assert continuation_long_score(f) == pytest.approx(5.0)


def test_continuation_short_score_triggers_on_long_liq_spike() -> None:
    """Longs wiped → expected cascade down → SHORT. Confirmation = sellers + basis drop + OI."""
    f = _feat(
        long_liq_z32=3.0,
        taker_delta_norm_z32=-1.0,
        cvd_delta_z32=-0.5,
        basis_change_z32=-0.2,
        agg_u_oi_pct_z32=-0.8,
        fr_spread_z96=-1.0,
    )
    # 3 + 1 + 0.5 + 0.2 + 0.8 - 0.5 = 5.0
    assert continuation_short_score(f) == pytest.approx(5.0)


def test_continuation_scores_return_none_on_warmup() -> None:
    f = _feat(agg_u_oi_pct_z32=None)
    assert continuation_long_score(f) is None
    assert continuation_short_score(f) is None


def test_continuation_include_cvd_false_drops_cvd() -> None:
    f = _feat(
        short_liq_z32=1.0,
        cvd_delta_z32=99.0,      # would dominate if included
        agg_u_oi_pct_z32=0.0,
    )
    # include_cvd=True:  1 + 0 + 99 + 0 + 0 - 0 = 100
    # include_cvd=False: 1 + 0 + 0 + 0 - 0 = 1
    assert continuation_long_score(f, include_cvd=True) == pytest.approx(100.0)
    assert continuation_long_score(f, include_cvd=False) == pytest.approx(1.0)


# ── Hybrid scores (regime switch on |fr_close_z96|) ──────────────────


def test_hybrid_uses_reversal_in_stressed_regime() -> None:
    """When |fr_close_z96| >= stress_threshold, hybrid picks reversal scores."""
    f = _feat(
        fr_close_z96=1.5,         # stressed (>= 1.0)
        long_liq_z32=3.0,
        taker_delta_norm_z32=-1.0,
        liq_imbalance=0.0,
        fr_spread_z96=0.0,
    )
    # Expected = reversal_long_score = 3 + 1 + 0 + 0 + 0 + 0 - 0 = 4.0
    expected = reversal_long_score(f)
    assert hybrid_long_score(f, stress_threshold=1.0) == pytest.approx(expected)


def test_hybrid_uses_continuation_in_calm_regime() -> None:
    """When |fr_close_z96| < stress_threshold, hybrid picks continuation scores."""
    f = _feat(
        fr_close_z96=0.3,         # calm (< 1.0)
        short_liq_z32=3.0,
        taker_delta_norm_z32=1.0,
        agg_u_oi_pct_z32=0.5,
        fr_spread_z96=0.0,
    )
    expected = continuation_long_score(f)
    assert hybrid_long_score(f, stress_threshold=1.0) == pytest.approx(expected)


def test_hybrid_short_same_switching_rule() -> None:
    """Short-side hybrid follows the same |fr_close_z96| switch."""
    # Stressed → reversal short
    f_stressed = _feat(
        fr_close_z96=-2.0,
        short_liq_z32=2.0,
        taker_delta_norm_z32=1.0,
    )
    assert hybrid_short_score(f_stressed, stress_threshold=1.0) == pytest.approx(
        reversal_short_score(f_stressed)
    )
    # Calm → continuation short
    f_calm = _feat(
        fr_close_z96=0.1,
        long_liq_z32=2.0,
        taker_delta_norm_z32=-1.0,
    )
    assert hybrid_short_score(f_calm, stress_threshold=1.0) == pytest.approx(
        continuation_short_score(f_calm)
    )


def test_hybrid_warmup_none_on_any_missing_input() -> None:
    f = _feat(fr_close_z96=None)
    assert hybrid_long_score(f) is None
    assert hybrid_short_score(f) is None


# ── baseline_c_signals ───────────────────────────────────────────────


def test_signals_reversal_mode_fires_long() -> None:
    """Reversal mode, long_liq spike with selling flow → long signal."""
    f = _feat(
        long_liq_z32=3.0,
        taker_delta_norm_z32=-1.0,   # -(-1) = +1 contribution
    )
    sigs = baseline_c_signals(
        [f], mode="reversal", long_threshold=3.5, short_threshold=3.5,
    )
    # reversal_long_score = 3 + 1 + 0 + 0 + 0 + 0 - 0 = 4.0 → above 3.5
    assert sigs == [1]


def test_signals_continuation_mode_fires_short_on_long_liq() -> None:
    """Continuation mode uses long_liq for SHORT trigger (cascade ignition)."""
    f = _feat(
        long_liq_z32=3.0,
        taker_delta_norm_z32=-1.0,
        agg_u_oi_pct_z32=-1.0,
    )
    sigs = baseline_c_signals(
        [f], mode="continuation", long_threshold=5.0, short_threshold=4.5,
    )
    # continuation_short_score = 3 + 1 + 0 + 0 + 1 - 0 = 5.0 → above 4.5
    assert sigs == [-1]


def test_signals_hybrid_mode_switches_on_fr_close_z96() -> None:
    """Hybrid picks reversal when stressed, continuation when calm."""
    f_stressed = _feat(
        fr_close_z96=2.0,              # stressed → reversal mode active
        long_liq_z32=3.0,
        taker_delta_norm_z32=-1.0,
        fr_spread_z96=0.0,
    )
    # reversal_long_score = 4.0 (clears 3.5)
    sigs = baseline_c_signals(
        [f_stressed], mode="hybrid", long_threshold=3.5, short_threshold=3.5,
    )
    assert sigs == [1]


def test_signals_flat_on_tie() -> None:
    """Equal scores → refuse to break tie, stay flat."""
    f = _feat(long_liq_z32=2.0, short_liq_z32=2.0)
    sigs = baseline_c_signals(
        [f], mode="reversal", long_threshold=0.0, short_threshold=0.0,
    )
    assert sigs == [0]


def test_signals_flat_on_warmup() -> None:
    f = _feat(long_liq_z32=None)
    sigs = baseline_c_signals(
        [f], mode="reversal", long_threshold=0.0, short_threshold=0.0,
    )
    assert sigs == [0]


def test_signals_rejects_unknown_mode() -> None:
    f = _feat(long_liq_z32=1.0)
    with pytest.raises(ValueError):
        baseline_c_signals(
            [f], mode="rocket-science", long_threshold=0.0, short_threshold=0.0,
        )


def test_signals_include_cvd_false_path() -> None:
    """include_cvd=False must allow None cvd_delta_z32 without killing the signal."""
    f = _feat(long_liq_z32=4.0, cvd_delta_z32=None)
    sigs = baseline_c_signals(
        [f], mode="reversal", long_threshold=3.5, short_threshold=3.5,
        include_cvd=False,
    )
    assert sigs == [1]
