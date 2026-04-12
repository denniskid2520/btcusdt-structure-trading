"""Tests for Strategy C Baseline B (score-based, event-driven signals).

Baseline B is precision-first:
    - Compute a continuous long_score and short_score per bar.
    - Signals only fire when the score is above a percentile threshold
      chosen on the TRAINING split (no lookahead).
    - The winning side (higher score) emits +1 or -1; ties are broken in
      favour of flat; None z-scores (warmup) always produce 0.

Scoring (higher = stronger):
    long_score  = long_liq_z32 + taker_delta_norm_z32 + cvd_delta_z32
                  + basis_change_z32 + oi_pct_change_z32 - |fr_spread_z96|

    short_score = short_liq_z32 - taker_delta_norm_z32 - cvd_delta_z32
                  - basis_change_z32 - oi_pct_change_z32 - |fr_spread_z96|

Signal logic:
    - Both sides computed.
    - long fires if long_score >= long_threshold AND long_score > short_score.
    - short fires if short_score >= short_threshold AND short_score > long_score.
    - Otherwise flat.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from data.strategy_c_features import StrategyCFeatureBar
from strategies.strategy_c_baseline_b import (
    baseline_b_signals,
    long_score,
    long_scores,
    short_score,
    short_scores,
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
    fr_spread_z96: float | None = 0.0,
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
        liq_imbalance=0.0,
        taker_delta_norm_z32=taker_delta_norm_z32,
        oi_pct_change_z32=oi_pct_change_z32,
        basis_z96=0.0,
        fr_close_z96=0.0,
        cvd_delta_z32=cvd_delta_z32,
        long_liq_z32=long_liq_z32,
        short_liq_z32=short_liq_z32,
        basis_change_z32=basis_change_z32,
        fr_spread_z96=fr_spread_z96,
        agg_u_oi_pct_z32=0.0,
    )


# ── long_score / short_score ─────────────────────────────────────────


def test_long_score_sums_constituents() -> None:
    f = _feat(
        long_liq_z32=3.0,
        taker_delta_norm_z32=1.0,
        cvd_delta_z32=0.5,
        basis_change_z32=0.2,
        oi_pct_change_z32=0.3,
        fr_spread_z96=-1.0,  # |fr_spread| = 1.0 → penalty
    )
    # 3.0 + 1.0 + 0.5 + 0.2 + 0.3 - 1.0 = 4.0
    assert long_score(f) == pytest.approx(4.0)


def test_short_score_flips_flow_signs() -> None:
    f = _feat(
        short_liq_z32=3.0,
        taker_delta_norm_z32=-1.0,   # sellers dominant → +1 to short score
        cvd_delta_z32=-0.5,          # cvd down → +0.5
        basis_change_z32=-0.2,       # premium contracting → +0.2
        oi_pct_change_z32=-0.3,      # OI contracting → +0.3
        fr_spread_z96=2.0,           # penalty −2.0
    )
    # 3.0 - (-1.0) - (-0.5) - (-0.2) - (-0.3) - |2.0| = 3+1+0.5+0.2+0.3-2 = 3.0
    assert short_score(f) == pytest.approx(3.0)


def test_scores_return_none_on_any_warmup() -> None:
    f = _feat(long_liq_z32=None)
    assert long_score(f) is None
    assert short_score(f) is None


def test_fr_spread_penalty_is_absolute() -> None:
    """|fr_spread_z96| punishes both positive and negative spreads equally."""
    f_pos = _feat(long_liq_z32=1.0, fr_spread_z96=+2.0)
    f_neg = _feat(long_liq_z32=1.0, fr_spread_z96=-2.0)
    assert long_score(f_pos) == long_score(f_neg)
    assert long_score(f_pos) == pytest.approx(1.0 - 2.0)  # −1.0


# ── batch helpers ────────────────────────────────────────────────────


def test_long_scores_parallel_series() -> None:
    feats = [
        _feat(0, long_liq_z32=2.0),
        _feat(1, long_liq_z32=None),       # warmup → None
        _feat(2, long_liq_z32=3.0, fr_spread_z96=1.0),
    ]
    scores = long_scores(feats)
    assert len(scores) == 3
    assert scores[0] == pytest.approx(2.0)
    assert scores[1] is None
    assert scores[2] == pytest.approx(2.0)  # 3 − 1


def test_short_scores_parallel_series() -> None:
    feats = [
        _feat(0, short_liq_z32=2.0),
        _feat(1, short_liq_z32=None),
    ]
    scores = short_scores(feats)
    assert scores[0] == pytest.approx(2.0)
    assert scores[1] is None


# ── baseline_b_signals ───────────────────────────────────────────────


def test_signal_fires_long_when_long_score_above_threshold() -> None:
    feats = [_feat(0, long_liq_z32=3.0)]  # long_score = 3.0
    sigs = baseline_b_signals(feats, long_threshold=2.5, short_threshold=2.5)
    assert sigs == [1]


def test_signal_fires_short_when_short_score_above_threshold() -> None:
    feats = [_feat(0, short_liq_z32=3.0, taker_delta_norm_z32=-1.0)]
    # short_score = 3.0 - (-1) = 4.0
    sigs = baseline_b_signals(feats, long_threshold=10.0, short_threshold=2.5)
    assert sigs == [-1]


def test_signal_flat_when_both_scores_below_threshold() -> None:
    feats = [_feat(0, long_liq_z32=1.0, short_liq_z32=1.0)]
    sigs = baseline_b_signals(feats, long_threshold=5.0, short_threshold=5.0)
    assert sigs == [0]


def test_signal_winning_side_gets_picked_when_both_qualify() -> None:
    """If BOTH sides clear their thresholds, the higher score wins."""
    # long_score = long_liq(3.0) + taker_z(2.0) = 5.0
    # short_score = short_liq(3.0) - taker_z(2.0) = 1.0
    # Both above threshold 0.5 → long wins.
    f = _feat(
        long_liq_z32=3.0,
        short_liq_z32=3.0,
        taker_delta_norm_z32=2.0,
    )
    sigs = baseline_b_signals([f], long_threshold=0.5, short_threshold=0.5)
    assert sigs == [1]


def test_signal_flat_during_warmup() -> None:
    f = _feat(long_liq_z32=None)
    sigs = baseline_b_signals([f], long_threshold=0.0, short_threshold=0.0)
    assert sigs == [0]


def test_signal_flat_when_tie() -> None:
    """Equal scores → no action (refuse to break ties)."""
    # long_score = 2.0, short_score = 2.0 → tie → flat.
    f = _feat(long_liq_z32=2.0, short_liq_z32=2.0)
    sigs = baseline_b_signals([f], long_threshold=0.0, short_threshold=0.0)
    assert sigs == [0]


# ── include_cvd=False path ───────────────────────────────────────────


def test_long_score_nocvd_drops_cvd_term() -> None:
    """With include_cvd=False, cvd_delta_z32 is omitted from the sum."""
    f = _feat(
        long_liq_z32=3.0,
        taker_delta_norm_z32=1.0,
        cvd_delta_z32=99.0,          # would dominate if included
        basis_change_z32=0.2,
        oi_pct_change_z32=0.3,
        fr_spread_z96=0.0,
    )
    # include_cvd=True:  3 + 1 + 99 + 0.2 + 0.3 - 0 = 103.5
    # include_cvd=False: 3 + 1 + 0.2 + 0.3 - 0 = 4.5
    assert long_score(f, include_cvd=True) == pytest.approx(103.5)
    assert long_score(f, include_cvd=False) == pytest.approx(4.5)


def test_short_score_nocvd_drops_cvd_term() -> None:
    f = _feat(
        short_liq_z32=3.0,
        taker_delta_norm_z32=-1.0,
        cvd_delta_z32=-99.0,         # would dominate if included
        basis_change_z32=-0.2,
        oi_pct_change_z32=-0.3,
        fr_spread_z96=0.0,
    )
    # include_cvd=True:  3 - (-1) - (-99) - (-0.2) - (-0.3) - 0 = 103.5
    # include_cvd=False: 3 - (-1) - (-0.2) - (-0.3) - 0 = 4.5
    assert short_score(f, include_cvd=True) == pytest.approx(103.5)
    assert short_score(f, include_cvd=False) == pytest.approx(4.5)


def test_scores_nocvd_tolerate_none_cvd() -> None:
    """With include_cvd=False, a None cvd_delta_z32 is not a warmup blocker."""
    f = _feat(long_liq_z32=2.0, cvd_delta_z32=None)
    assert long_score(f, include_cvd=True) is None
    assert long_score(f, include_cvd=False) == pytest.approx(2.0)
