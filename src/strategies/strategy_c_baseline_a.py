"""Strategy C — Baseline A (trend confirmation).

The simplest possible signal that lines up price-agnostic order-flow with
market context. All four conditions on a side must be satisfied:

    LONG  : taker_delta_norm_z32 >  1
            oi_pct_change_z32    >  0
            basis_z96            >  0
            funding_z96          <  2     # not yet overheated

    SHORT : taker_delta_norm_z32 < -1
            oi_pct_change_z32    <  0
            basis_z96            <  0
            funding_z96          > -2     # not yet super cold

During warmup any z-score is None, which blocks both sides and returns 0.
Baseline A is NOT a full strategy — it's the simplest thing we can measure
against, so later variants have a honest benchmark.
"""
from __future__ import annotations

from typing import Sequence

from data.strategy_c_features import StrategyCFeatureBar


LONG_TAKER_THRESHOLD = 1.0
SHORT_TAKER_THRESHOLD = -1.0
FUNDING_HIGH_THRESHOLD = 2.0    # block longs above this
FUNDING_LOW_THRESHOLD = -2.0    # block shorts below this


def baseline_a_signal(f: StrategyCFeatureBar) -> int:
    """Emit +1 (long), -1 (short), 0 (flat) for a single feature bar."""
    # Any None means warmup — stay flat.
    if (
        f.taker_delta_norm_z32 is None
        or f.oi_pct_change_z32 is None
        or f.basis_z96 is None
        or f.fr_close_z96 is None
    ):
        return 0

    if (
        f.taker_delta_norm_z32 > LONG_TAKER_THRESHOLD
        and f.oi_pct_change_z32 > 0.0
        and f.basis_z96 > 0.0
        and f.fr_close_z96 < FUNDING_HIGH_THRESHOLD
    ):
        return 1

    if (
        f.taker_delta_norm_z32 < SHORT_TAKER_THRESHOLD
        and f.oi_pct_change_z32 < 0.0
        and f.basis_z96 < 0.0
        and f.fr_close_z96 > FUNDING_LOW_THRESHOLD
    ):
        return -1

    return 0


def baseline_a_signals(feats: Sequence[StrategyCFeatureBar]) -> list[int]:
    """Vectorized wrapper: return a parallel signal series."""
    return [baseline_a_signal(f) for f in feats]
