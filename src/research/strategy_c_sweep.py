"""Strategy C sweep utilities — building blocks for the Baseline C grid runner.

Three pure helpers the sweep driver composes:

    temporal_split(series, train_frac)
        Chronological split. No shuffling. This is what makes the holdout
        honest: thresholds are chosen on the train slice, never on the future.

    percentile_threshold(values, pct)
        Linear-interpolated percentile over a non-None value stream. Used to
        turn the Baseline C long/short score distribution (train split only)
        into a concrete long_threshold / short_threshold number.

    passes_min_trades(metrics_row, min_train, min_holdout)
        Guardrail to drop configurations that sit on tiny sample sizes. The
        precision-first Baseline B trap was declaring a holdout cell with
        n=8 a "winner" — this filter makes that impossible.
"""
from __future__ import annotations

from typing import Mapping, Sequence, TypeVar

T = TypeVar("T")


# ── temporal_split ───────────────────────────────────────────────────


def temporal_split(
    series: Sequence[T],
    *,
    train_frac: float,
) -> tuple[list[T], list[T]]:
    """Chronological split into (train, holdout).

    The first `train_frac` fraction of `series` (by position) goes to train,
    the remainder to holdout. This is the only split style that's honest for
    time-series evaluation — no random shuffling.

    Args:
        series: Any Sequence in chronological order.
        train_frac: Fraction in [0.0, 1.0]. 0.0 → all holdout, 1.0 → all train.

    Returns:
        (train_list, holdout_list).

    Raises:
        ValueError: if `train_frac` is outside [0.0, 1.0].
    """
    if not 0.0 <= train_frac <= 1.0:
        raise ValueError(f"train_frac must be in [0, 1], got {train_frac}")

    n = len(series)
    if n == 0:
        return [], []

    cut = int(n * train_frac)
    return list(series[:cut]), list(series[cut:])


# ── percentile_threshold ─────────────────────────────────────────────


def percentile_threshold(
    values: Sequence[float | None],
    pct: float,
) -> float:
    """Linear-interpolated percentile of a possibly-None value stream.

    `pct` is 0..100. None entries are dropped before computing the percentile
    so warmup rows don't distort the threshold. Matches numpy.percentile's
    default linear interpolation but stays dependency-free.

    Args:
        values: Possibly-None float stream (e.g. long_scores across the train
            split, which has None at every warmup bar).
        pct: Percentile in [0, 100].

    Returns:
        The interpolated percentile value.

    Raises:
        ValueError: if `values` contains no non-None entries or `pct` is out
            of range.
    """
    if not 0.0 <= pct <= 100.0:
        raise ValueError(f"pct must be in [0, 100], got {pct}")

    clean = sorted(v for v in values if v is not None)
    if not clean:
        raise ValueError("percentile_threshold: no non-None values")

    if len(clean) == 1:
        return float(clean[0])

    # numpy-style linear interpolation: position in [0, n-1].
    rank = (pct / 100.0) * (len(clean) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(clean) - 1)
    frac = rank - lo
    return float(clean[lo] + frac * (clean[hi] - clean[lo]))


# ── passes_min_trades ────────────────────────────────────────────────


def passes_min_trades(
    row: Mapping[str, float],
    *,
    min_train: int,
    min_holdout: int,
) -> bool:
    """True iff `row` has at least min_train train trades AND min_holdout holdout trades.

    Args:
        row: Dict carrying 'train_num_trades' and 'holdout_num_trades' keys.
        min_train: Lower bound on train_num_trades (inclusive).
        min_holdout: Lower bound on holdout_num_trades (inclusive).
    """
    train_n = row.get("train_num_trades", 0)
    holdout_n = row.get("holdout_num_trades", 0)
    return train_n >= min_train and holdout_n >= min_holdout
