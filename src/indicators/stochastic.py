"""Stochastic Oscillator primitive — Lane's Full Stochastic formulation.

Used by Strategy C v2 feature family A (price/technical). Pure function.

Formula (George Lane):
    raw_k  = 100 * (close - lowest_low_n) / (highest_high_n - lowest_low_n)
    slow_k = SMA(raw_k, smooth_k)
    slow_d = SMA(slow_k, smooth_d)

We return `slow_k` as `.k` and `slow_d` as `.d`. For the classic "fast"
stochastic, call with smooth_k=1; for "slow" with smooth_k=3, smooth_d=3.

Warmup indices:
    raw_k   → k_period - 1
    slow_k  → k_period + smooth_k - 2
    slow_d  → k_period + smooth_k + smooth_d - 3

Before those indices, the corresponding field is None (or the whole
StochasticPoint is None if slow_k isn't ready yet).

Edge case:
    If highest_high == lowest_low over the window (flat range), raw_k is
    undefined (0/0). We default to 50.0 (mid-range).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StochasticPoint:
    """One bar's stochastic state.

    `k` is the smoothed %K (slow_k). `d` is the signal line (SMA of slow_k);
    it is None while slow_d is still in warmup even if slow_k is ready.
    """
    k: float
    d: float | None


def stochastic(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    *,
    k_period: int = 14,
    smooth_k: int = 3,
    smooth_d: int = 3,
) -> list[StochasticPoint | None]:
    """Compute the Full Stochastic Oscillator.

    Args:
        highs: Per-bar high prices.
        lows:  Per-bar low prices.
        closes: Per-bar close prices.
        k_period: Lookback window for raw %K. Must be > 0.
        smooth_k: SMA length applied to raw %K to form slow %K. Must be > 0.
        smooth_d: SMA length applied to slow %K to form %D. Must be > 0.

    Returns:
        A list of length len(closes). Each entry is either None (warmup
        before slow %K is ready) or a StochasticPoint. Within a
        StochasticPoint, `.d` may still be None during the %D warmup window.

    Raises:
        ValueError: on bad parameters or mismatched input lengths.
    """
    if k_period <= 0:
        raise ValueError(f"k_period must be positive, got {k_period}")
    if smooth_k <= 0:
        raise ValueError(f"smooth_k must be positive, got {smooth_k}")
    if smooth_d <= 0:
        raise ValueError(f"smooth_d must be positive, got {smooth_d}")
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError(
            f"highs/lows/closes lengths must match, got "
            f"{len(highs)}/{len(lows)}/{len(closes)}"
        )

    n = len(closes)
    out: list[StochasticPoint | None] = [None] * n

    # Step 1: raw %K per bar.
    raw_k: list[float | None] = [None] * n
    for i in range(k_period - 1, n):
        window_high = max(highs[i - k_period + 1 : i + 1])
        window_low = min(lows[i - k_period + 1 : i + 1])
        rng = window_high - window_low
        if rng == 0.0:
            raw_k[i] = 50.0
        else:
            raw_k[i] = 100.0 * (closes[i] - window_low) / rng

    # Step 2: slow %K = SMA(raw_k, smooth_k). Needs smooth_k consecutive raw_k values.
    slow_k: list[float | None] = [None] * n
    slow_k_warmup = (k_period - 1) + (smooth_k - 1)
    for i in range(slow_k_warmup, n):
        window = raw_k[i - smooth_k + 1 : i + 1]
        # Guard against any None in window (shouldn't happen post-warmup, but safe).
        if any(x is None for x in window):
            continue
        slow_k[i] = sum(window) / smooth_k  # type: ignore[arg-type]

    # Step 3: slow %D = SMA(slow_k, smooth_d).
    slow_d: list[float | None] = [None] * n
    slow_d_warmup = slow_k_warmup + (smooth_d - 1)
    for i in range(slow_d_warmup, n):
        window = slow_k[i - smooth_d + 1 : i + 1]
        if any(x is None for x in window):
            continue
        slow_d[i] = sum(window) / smooth_d  # type: ignore[arg-type]

    # Assemble. A StochasticPoint is populated once slow_k is ready; .d waits on slow_d.
    for i in range(n):
        if slow_k[i] is None:
            out[i] = None
        else:
            out[i] = StochasticPoint(k=slow_k[i], d=slow_d[i])  # type: ignore[arg-type]

    return out
