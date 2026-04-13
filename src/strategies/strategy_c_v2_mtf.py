"""Strategy C v2 Phase 3 multi-timeframe primitives.

Two building blocks:

1. `align_higher_to_lower` — maps higher-timeframe feature values down to
   a lower-timeframe bar stream without look-ahead. A higher-TF feature
   at bar k (representing period [T_k, T_{k+1})) is only "known" at time
   T_{k+1}, so for a lower-TF bar at time t we use the largest k such
   that (T_k + higher_period) <= t.

2. `mtf_trend_signals` — the simplest multi-timeframe AND-gate rule. Long
   when the aligned higher-TF RSI is above its threshold AND the
   lower-TF RSI is above its threshold. Short when both are below.
   Otherwise flat. Passing None at either level forces flat (can't
   confirm a regime without data).

More sophisticated MTF rules (weighted blends, regime-switch, ATR-based
sizing) live in downstream Phase 3/4 modules. These two primitives are
the minimal, testable foundation.
"""
from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timedelta
from typing import Sequence


def align_higher_to_lower(
    lower_timestamps: Sequence[datetime],
    higher_timestamps: Sequence[datetime],
    higher_values: Sequence[float | None],
    *,
    higher_period: timedelta,
) -> list[float | None]:
    """Causally align a higher-TF feature stream to a lower-TF bar stream.

    Args:
        lower_timestamps: Chronological lower-TF bar timestamps (one per bar).
        higher_timestamps: Chronological higher-TF bar timestamps (one per bar).
            Each higher-TF bar `k` represents the half-open period
            `[higher_timestamps[k], higher_timestamps[k] + higher_period)`.
        higher_values: Feature values aligned to `higher_timestamps` (same
            length). May contain None for warmup.
        higher_period: Length of one higher-TF bar (e.g. `timedelta(hours=4)`).

    Returns:
        A list the same length as `lower_timestamps`. Entry `i` is the
        higher-TF value whose period END is <= `lower_timestamps[i]`, or
        None if no such period has closed yet (pure warmup).

    Raises:
        ValueError: if `higher_timestamps` and `higher_values` have
            different lengths.
    """
    if len(higher_timestamps) != len(higher_values):
        raise ValueError(
            f"higher_timestamps length {len(higher_timestamps)} != "
            f"higher_values length {len(higher_values)}"
        )
    n = len(lower_timestamps)
    out: list[float | None] = [None] * n
    if n == 0 or not higher_timestamps:
        return out

    # End time of each higher bar = start + period.
    higher_end_times = [t + higher_period for t in higher_timestamps]

    for i, t in enumerate(lower_timestamps):
        # Largest k such that higher_end_times[k] <= t
        k = bisect_right(higher_end_times, t) - 1
        if k >= 0:
            out[i] = higher_values[k]
    return out


def mtf_trend_signals(
    higher_rsi_aligned: Sequence[float | None],
    lower_rsi: Sequence[float | None],
    *,
    higher_threshold: float = 50.0,
    lower_threshold: float = 50.0,
) -> list[int]:
    """Simple MTF trend rule — higher AND lower must both confirm.

    Rule per bar (pre-aligned, same length):

        higher_rsi > higher_threshold AND lower_rsi > lower_threshold → +1
        higher_rsi < higher_threshold AND lower_rsi < lower_threshold → -1
        Otherwise (mixed, or any None) → 0

    Args:
        higher_rsi_aligned: Higher-TF RSI values, pre-aligned to the
            lower-TF bar stream via `align_higher_to_lower`.
        lower_rsi: Lower-TF RSI values, one per bar.
        higher_threshold: Required level on the higher TF (default 50).
        lower_threshold: Required level on the lower TF (default 50).

    Returns:
        A list of ints in {-1, 0, +1}, same length as the inputs.

    Raises:
        ValueError: if input lengths don't match.
    """
    if len(higher_rsi_aligned) != len(lower_rsi):
        raise ValueError(
            f"higher_rsi_aligned length {len(higher_rsi_aligned)} != "
            f"lower_rsi length {len(lower_rsi)}"
        )

    out: list[int] = []
    for h, l in zip(higher_rsi_aligned, lower_rsi):
        if h is None or l is None:
            out.append(0)
        elif h > higher_threshold and l > lower_threshold:
            out.append(1)
        elif h < higher_threshold and l < lower_threshold:
            out.append(-1)
        else:
            out.append(0)
    return out
