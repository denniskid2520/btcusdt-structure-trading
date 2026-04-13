"""Strategy C v2 rolling walk-forward split harness.

Pure function, standard-library only. Returns a list of `WalkForwardSplit`
descriptors that carry both calendar boundaries and integer index ranges
for a given chronological timestamp series.

Contract:
    - `timestamps` must be strictly ascending (ValueError otherwise)
    - `train_months`, `test_months`, `step_months` must all be > 0
    - train/test adjacency: train_end == test_start AND train_hi == test_lo
    - The last split is included iff test_end <= timestamps[-1]. No partial
      or fractional test windows at the right edge — this is the anti-leakage
      property downstream code depends on.
    - There is no overlap between a split's train slice and its own test slice.
      Overlap BETWEEN splits depends on step_months vs train_months (train
      windows overlap when step < train) and step_months vs test_months (test
      windows overlap when step < test). The caller decides.

Usage:
    >>> from research.strategy_c_v2_walk_forward import walk_forward_splits
    >>> splits = walk_forward_splits(
    ...     timestamps, train_months=24, test_months=6, step_months=6,
    ... )
    >>> for s in splits:
    ...     train_slice = bars[s.train_lo : s.train_hi]
    ...     test_slice  = bars[s.test_lo  : s.test_hi]
    ...     # fit thresholds / normalisers on train_slice, score test_slice
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence


@dataclass(frozen=True)
class WalkForwardSplit:
    """One rolling (train, test) window descriptor.

    All index ranges are half-open [lo, hi).
    """
    index: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    train_lo: int
    train_hi: int
    test_lo: int
    test_hi: int


def _add_months(dt: datetime, months: int) -> datetime:
    """Add `months` calendar months to `dt`, clamping day to month length.

    Mirrors `dateutil.relativedelta(months=N)` without pulling in dateutil.
    Jan 31 + 1 month → Feb 28 (or Feb 29 in a leap year). Negative months
    and zero are both valid. Raises ValueError if the resulting year would
    be < 1.
    """
    total = dt.month - 1 + months
    new_year = dt.year + total // 12
    new_month = total % 12 + 1
    if new_year < 1:
        raise ValueError(f"month arithmetic underflowed year: {dt!r} + {months}m")

    day = dt.day
    while day > 0:
        try:
            return dt.replace(year=new_year, month=new_month, day=day)
        except ValueError:
            day -= 1
    raise ValueError(f"could not add {months} months to {dt!r}")


def walk_forward_splits(
    timestamps: Sequence[datetime],
    *,
    train_months: int,
    test_months: int,
    step_months: int,
) -> list[WalkForwardSplit]:
    """Build rolling walk-forward splits over a chronological series.

    Args:
        timestamps: Strictly ascending datetimes, one per bar.
        train_months: Calendar-month length of the training window.
        test_months: Calendar-month length of the test window.
        step_months: Calendar-month advance between consecutive splits.

    Returns:
        List of WalkForwardSplit descriptors in chronological order.

    Raises:
        ValueError: on non-positive month args, or on timestamps that are
            not strictly ascending.
    """
    if train_months <= 0:
        raise ValueError(f"train_months must be > 0, got {train_months}")
    if test_months <= 0:
        raise ValueError(f"test_months must be > 0, got {test_months}")
    if step_months <= 0:
        raise ValueError(f"step_months must be > 0, got {step_months}")

    n = len(timestamps)
    if n == 0:
        return []

    # Strict ascending check.
    for i in range(1, n):
        if timestamps[i] <= timestamps[i - 1]:
            raise ValueError(
                f"timestamps must be strictly sorted ascending; "
                f"violation at index {i}: "
                f"{timestamps[i - 1]!r} !< {timestamps[i]!r}"
            )

    splits: list[WalkForwardSplit] = []
    anchor = timestamps[0]
    last_ts = timestamps[-1]
    index = 0

    while True:
        train_start = anchor
        train_end = _add_months(anchor, train_months)
        test_start = train_end
        test_end = _add_months(anchor, train_months + test_months)

        # Refuse partial test windows at the right edge.
        if test_end > last_ts:
            break

        train_lo = bisect_left(timestamps, train_start)
        train_hi = bisect_left(timestamps, train_end)
        test_lo = train_hi  # strict adjacency — no gap, no index leakage
        test_hi = bisect_left(timestamps, test_end)

        # If the test window doesn't actually contain any bars, we're done.
        if test_hi <= test_lo:
            break
        # Same guard for train window (paranoia — shouldn't fire on sane input).
        if train_hi <= train_lo:
            break

        splits.append(
            WalkForwardSplit(
                index=index,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                train_lo=train_lo,
                train_hi=train_hi,
                test_lo=test_lo,
                test_hi=test_hi,
            )
        )
        index += 1

        anchor = _add_months(anchor, step_months)

    return splits
