"""Strategy C — Baseline C (return/frequency balanced scorers).

Motivation:
    Baseline B was precision-first and found no holdout edge on either the 47d
    or 83d 15m BTCUSDT dataset: tight thresholds bought tiny samples on the
    wrong side of round-trip cost. Baseline C keeps Baseline B's score-based
    machinery but splits the signal into THREE testable hypotheses and sweeps
    looser thresholds (percentile 60-95) to find the return-frequency sweet
    spot instead of the highest-precision corner.

Three scoring families, one shared signal emitter:

    REVERSAL — fade cascades, expect mean reversion
        After a liquidation cascade, the surviving participants often drive a
        short-term reversal. We want evidence of panic in progress (flow still
        heavy in the liquidation direction) and then take the OPPOSITE side.

        long_rev_score  = long_liq_z32
                          - taker_delta_norm_z32       (flow still selling)
                          - cvd_delta_z32
                          - basis_change_z32           (premium collapsing)
                          - oi_pct_change_z32          (OI unwinding)
                          + 0.5 * liq_imbalance        (long_liq dominates)
                          - 0.5 * |fr_spread_z96|      (funding sanity penalty)

        short_rev_score = short_liq_z32
                          + taker_delta_norm_z32       (flow still buying)
                          + cvd_delta_z32
                          + basis_change_z32           (premium overheating)
                          + oi_pct_change_z32          (OI building)
                          - 0.5 * liq_imbalance        (short_liq dominates)
                          - 0.5 * |fr_spread_z96|

    CONTINUATION — ride cascades, momentum ignition
        A liquidation cascade can also be the ignition spark for a trend.
        Here we take the DIRECTION of the move and require flow + basis + OI
        to confirm the ignition on the trigger bar.

        long_cont_score  = short_liq_z32               (shorts wiped → rip up)
                           + taker_delta_norm_z32      (buyers confirm)
                           + cvd_delta_z32
                           + basis_change_z32
                           + agg_u_oi_pct_z32          (stablecoin OI flowing in)
                           - 0.5 * |fr_spread_z96|

        short_cont_score = long_liq_z32                (longs wiped → rip down)
                           - taker_delta_norm_z32
                           - cvd_delta_z32
                           - basis_change_z32
                           - agg_u_oi_pct_z32
                           - 0.5 * |fr_spread_z96|

    HYBRID — regime switch on |fr_close_z96|
        Uses the absolute funding-close z-score as the regime indicator:
            |fr_close_z96| >= stress_threshold  →  reversal family
            |fr_close_z96| <  stress_threshold  →  continuation family
        Idea: when funding is extreme, the book is already crowded and
        cascades tend to reverse; when funding is calm, cascades tend to
        ignite fresh trends.

Signal emission is identical across modes:
    - Flat if either score is None (warmup).
    - Long fires iff long_score >= long_threshold AND long_score > short_score.
    - Short fires iff short_score >= short_threshold AND short_score > long_score.
    - Equal scores → flat.

Threshold selection is external to this module: the sweep driver computes
percentile thresholds on the training split only, then passes them in as
`long_threshold` / `short_threshold` when generating signals for the whole
series. This is what makes the holdout honest.
"""
from __future__ import annotations

from typing import Literal, Sequence

from data.strategy_c_features import StrategyCFeatureBar

Mode = Literal["reversal", "continuation", "hybrid"]


# ── Warmup helpers ───────────────────────────────────────────────────


def _reversal_core_ok(f: StrategyCFeatureBar, *, include_cvd: bool) -> bool:
    """True iff every non-cvd reversal input is defined."""
    required = (
        f.long_liq_z32,
        f.short_liq_z32,
        f.taker_delta_norm_z32,
        f.basis_change_z32,
        f.oi_pct_change_z32,
        f.fr_spread_z96,
    )
    if any(z is None for z in required):
        return False
    if include_cvd and f.cvd_delta_z32 is None:
        return False
    return True


def _continuation_core_ok(f: StrategyCFeatureBar, *, include_cvd: bool) -> bool:
    """True iff every non-cvd continuation input is defined."""
    required = (
        f.long_liq_z32,
        f.short_liq_z32,
        f.taker_delta_norm_z32,
        f.basis_change_z32,
        f.agg_u_oi_pct_z32,
        f.fr_spread_z96,
    )
    if any(z is None for z in required):
        return False
    if include_cvd and f.cvd_delta_z32 is None:
        return False
    return True


# ── Reversal scores ──────────────────────────────────────────────────


def reversal_long_score(
    f: StrategyCFeatureBar, *, include_cvd: bool = True,
) -> float | None:
    """Long reversal: buy after a long-liq cascade while sellers still dominate."""
    if not _reversal_core_ok(f, include_cvd=include_cvd):
        return None
    base = (
        f.long_liq_z32
        - f.taker_delta_norm_z32
        - f.basis_change_z32
        - f.oi_pct_change_z32
        + 0.5 * f.liq_imbalance
        - 0.5 * abs(f.fr_spread_z96)
    )
    if include_cvd:
        return base - f.cvd_delta_z32
    return base


def reversal_short_score(
    f: StrategyCFeatureBar, *, include_cvd: bool = True,
) -> float | None:
    """Short reversal: sell after a short-liq squeeze while buyers still dominate."""
    if not _reversal_core_ok(f, include_cvd=include_cvd):
        return None
    base = (
        f.short_liq_z32
        + f.taker_delta_norm_z32
        + f.basis_change_z32
        + f.oi_pct_change_z32
        - 0.5 * f.liq_imbalance
        - 0.5 * abs(f.fr_spread_z96)
    )
    if include_cvd:
        return base + f.cvd_delta_z32
    return base


# ── Continuation scores ──────────────────────────────────────────────


def continuation_long_score(
    f: StrategyCFeatureBar, *, include_cvd: bool = True,
) -> float | None:
    """Long continuation: ride the up-cascade after shorts get squeezed."""
    if not _continuation_core_ok(f, include_cvd=include_cvd):
        return None
    base = (
        f.short_liq_z32
        + f.taker_delta_norm_z32
        + f.basis_change_z32
        + f.agg_u_oi_pct_z32
        - 0.5 * abs(f.fr_spread_z96)
    )
    if include_cvd:
        return base + f.cvd_delta_z32
    return base


def continuation_short_score(
    f: StrategyCFeatureBar, *, include_cvd: bool = True,
) -> float | None:
    """Short continuation: ride the down-cascade after longs get wiped."""
    if not _continuation_core_ok(f, include_cvd=include_cvd):
        return None
    base = (
        f.long_liq_z32
        - f.taker_delta_norm_z32
        - f.basis_change_z32
        - f.agg_u_oi_pct_z32
        - 0.5 * abs(f.fr_spread_z96)
    )
    if include_cvd:
        return base - f.cvd_delta_z32
    return base


# ── Hybrid scores (regime switch) ────────────────────────────────────


def hybrid_long_score(
    f: StrategyCFeatureBar,
    *,
    stress_threshold: float = 1.0,
    include_cvd: bool = True,
) -> float | None:
    """Hybrid: use reversal when funding-close z is extreme, else continuation."""
    if f.fr_close_z96 is None:
        return None
    if abs(f.fr_close_z96) >= stress_threshold:
        return reversal_long_score(f, include_cvd=include_cvd)
    return continuation_long_score(f, include_cvd=include_cvd)


def hybrid_short_score(
    f: StrategyCFeatureBar,
    *,
    stress_threshold: float = 1.0,
    include_cvd: bool = True,
) -> float | None:
    if f.fr_close_z96 is None:
        return None
    if abs(f.fr_close_z96) >= stress_threshold:
        return reversal_short_score(f, include_cvd=include_cvd)
    return continuation_short_score(f, include_cvd=include_cvd)


# ── Vectorised score helpers ─────────────────────────────────────────


def long_scores(
    feats: Sequence[StrategyCFeatureBar],
    *,
    mode: Mode,
    stress_threshold: float = 1.0,
    include_cvd: bool = True,
) -> list[float | None]:
    """Per-bar long scores for the requested mode."""
    if mode == "reversal":
        return [reversal_long_score(f, include_cvd=include_cvd) for f in feats]
    if mode == "continuation":
        return [continuation_long_score(f, include_cvd=include_cvd) for f in feats]
    if mode == "hybrid":
        return [
            hybrid_long_score(
                f, stress_threshold=stress_threshold, include_cvd=include_cvd
            )
            for f in feats
        ]
    raise ValueError(f"unknown mode: {mode!r}")


def short_scores(
    feats: Sequence[StrategyCFeatureBar],
    *,
    mode: Mode,
    stress_threshold: float = 1.0,
    include_cvd: bool = True,
) -> list[float | None]:
    """Per-bar short scores for the requested mode."""
    if mode == "reversal":
        return [reversal_short_score(f, include_cvd=include_cvd) for f in feats]
    if mode == "continuation":
        return [continuation_short_score(f, include_cvd=include_cvd) for f in feats]
    if mode == "hybrid":
        return [
            hybrid_short_score(
                f, stress_threshold=stress_threshold, include_cvd=include_cvd
            )
            for f in feats
        ]
    raise ValueError(f"unknown mode: {mode!r}")


# ── Signal emitter ───────────────────────────────────────────────────


def baseline_c_signals(
    feats: Sequence[StrategyCFeatureBar],
    *,
    mode: Mode,
    long_threshold: float,
    short_threshold: float,
    stress_threshold: float = 1.0,
    include_cvd: bool = True,
) -> list[int]:
    """Emit +1 / -1 / 0 per bar for the requested Baseline C mode.

    Args:
        feats: Feature bar series, ascending timestamps.
        mode: "reversal", "continuation", or "hybrid".
        long_threshold: Long score must be >= this to fire.
        short_threshold: Short score must be >= this to fire.
        stress_threshold: Only used when mode=="hybrid".
        include_cvd: If False, drop cvd_delta_z32 from every score.

    Returns:
        Integer signal list parallel to `feats`.
    """
    if mode not in ("reversal", "continuation", "hybrid"):
        raise ValueError(f"unknown mode: {mode!r}")

    ls = long_scores(
        feats,
        mode=mode,
        stress_threshold=stress_threshold,
        include_cvd=include_cvd,
    )
    ss = short_scores(
        feats,
        mode=mode,
        stress_threshold=stress_threshold,
        include_cvd=include_cvd,
    )

    out: list[int] = []
    for l, s in zip(ls, ss):
        if l is None or s is None:
            out.append(0)
            continue

        long_ok = l >= long_threshold
        short_ok = s >= short_threshold

        if long_ok and not short_ok:
            out.append(1)
        elif short_ok and not long_ok:
            out.append(-1)
        elif long_ok and short_ok:
            if l > s:
                out.append(1)
            elif s > l:
                out.append(-1)
            else:
                out.append(0)
        else:
            out.append(0)
    return out
