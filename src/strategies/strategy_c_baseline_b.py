"""Strategy C — Baseline B (precision-first, score-based signal).

Baseline B replaces the four boolean gates of Baseline A with two continuous
scores, long_score and short_score, that are above threshold only for the
strongest events. The thresholds are chosen on a training split (percentile
of the score distribution) and applied to a held-out evaluation split.

Scoring (higher = stronger signal for that side):

    long_score  =  long_liq_z32
                 + taker_delta_norm_z32    (buyers aggressive)
                 + cvd_delta_z32           (cumulative buying flow)
                 + basis_change_z32        (futures premium expanding)
                 + oi_pct_change_z32       (OI growing = new money long)
                 − |fr_spread_z96|         (penalty for weird funding basis)

    short_score =  short_liq_z32
                 − taker_delta_norm_z32    (sellers aggressive)
                 − cvd_delta_z32
                 − basis_change_z32
                 − oi_pct_change_z32
                 − |fr_spread_z96|

Any None input (warmup) makes the score None → no signal.

Signal emission:
    - Bar is flat if either score is None.
    - Long if long_score >= long_threshold AND long_score > short_score.
    - Short if short_score >= short_threshold AND short_score > long_score.
    - Tie → flat (we refuse to break ties).

The thresholds are NOT hard-coded — they are passed in. Typical use:
    1. Split feats into train (70%) / holdout (30%).
    2. Compute long_scores / short_scores on the train split.
    3. Pick percentile thresholds (95th, 97.5th, ...) on train.
    4. Apply to the full series and evaluate backtest on the holdout.
"""
from __future__ import annotations

from typing import Sequence

from data.strategy_c_features import StrategyCFeatureBar


def _row_zs_core(f: StrategyCFeatureBar) -> tuple[float | None, ...]:
    """Non-cvd z-scores used by both sides. If any is None we treat the row
    as warming up and refuse to emit either signal."""
    return (
        f.long_liq_z32,
        f.short_liq_z32,
        f.taker_delta_norm_z32,
        f.basis_change_z32,
        f.oi_pct_change_z32,
        f.fr_spread_z96,
    )


def long_score(f: StrategyCFeatureBar, *, include_cvd: bool = True) -> float | None:
    """Continuous long score for one bar, or None if any input is None.

    When include_cvd=False, the cvd_delta_z32 term is omitted from the score
    AND dropped from the warmup-None check — used to test whether pair_cvd
    carries any marginal information beyond the taker/basis components.
    """
    if any(z is None for z in _row_zs_core(f)):
        return None
    if include_cvd and f.cvd_delta_z32 is None:
        return None
    base = (
        f.long_liq_z32
        + f.taker_delta_norm_z32
        + f.basis_change_z32
        + f.oi_pct_change_z32
        - abs(f.fr_spread_z96)
    )
    if include_cvd:
        return base + f.cvd_delta_z32
    return base


def short_score(f: StrategyCFeatureBar, *, include_cvd: bool = True) -> float | None:
    """Continuous short score for one bar, or None if any input is None."""
    if any(z is None for z in _row_zs_core(f)):
        return None
    if include_cvd and f.cvd_delta_z32 is None:
        return None
    base = (
        f.short_liq_z32
        - f.taker_delta_norm_z32
        - f.basis_change_z32
        - f.oi_pct_change_z32
        - abs(f.fr_spread_z96)
    )
    if include_cvd:
        return base - f.cvd_delta_z32
    return base


def long_scores(
    feats: Sequence[StrategyCFeatureBar], *, include_cvd: bool = True,
) -> list[float | None]:
    """Vectorized: long_score per bar, preserving length."""
    return [long_score(f, include_cvd=include_cvd) for f in feats]


def short_scores(
    feats: Sequence[StrategyCFeatureBar], *, include_cvd: bool = True,
) -> list[float | None]:
    """Vectorized: short_score per bar, preserving length."""
    return [short_score(f, include_cvd=include_cvd) for f in feats]


def baseline_b_signals(
    feats: Sequence[StrategyCFeatureBar],
    *,
    long_threshold: float,
    short_threshold: float,
    include_cvd: bool = True,
) -> list[int]:
    """Emit +1 (long), -1 (short), 0 (flat) per bar.

    Args:
        feats: Feature bar series.
        long_threshold: long_score must be >= this for a long to fire.
        short_threshold: short_score must be >= this for a short to fire.
        include_cvd: If False, drop pair_cvd from the score (redundancy test).

    Returns:
        Parallel integer list of the same length as feats.
    """
    out: list[int] = []
    for f in feats:
        ls = long_score(f, include_cvd=include_cvd)
        ss = short_score(f, include_cvd=include_cvd)
        if ls is None or ss is None:
            out.append(0)
            continue

        long_qualifies = ls >= long_threshold
        short_qualifies = ss >= short_threshold

        if long_qualifies and not short_qualifies:
            out.append(1)
        elif short_qualifies and not long_qualifies:
            out.append(-1)
        elif long_qualifies and short_qualifies:
            # Both cleared — pick the stronger side; refuse ties.
            if ls > ss:
                out.append(1)
            elif ss > ls:
                out.append(-1)
            else:
                out.append(0)
        else:
            out.append(0)
    return out
