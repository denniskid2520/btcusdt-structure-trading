"""ATR (Average True Range) primitive — Wilder's original formulation.

Used by Strategy C v2 feature family A (price/technical) for stop sizing,
volatility normalisation, and regime gating. Pure function, standard lib only.

Formula (Wilder 1978, "New Concepts in Technical Trading Systems"):
    TR[0] = high[0] - low[0]                 # no previous close → just bar range
    TR[i] = max(
        high[i] - low[i],
        |high[i] - close[i-1]|,
        |low[i]  - close[i-1]|,
    )   for i >= 1

    ATR[period - 1] = mean(TR[0 .. period - 1])             # simple mean seed
    ATR[i] = ((period - 1) * ATR[i - 1] + TR[i]) / period    for i >= period

This recurrence is equivalent to an EMA with α = 1 / period ("Wilder's
smoothing"), which is different from the standard EMA α = 2 / (N + 1).
"""
from __future__ import annotations


def atr(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    *,
    period: int = 14,
) -> list[float | None]:
    """Compute ATR for an OHLC stream.

    Args:
        highs: Per-bar high prices.
        lows:  Per-bar low prices.
        closes: Per-bar close prices.
        period: Lookback window. Must be > 0.

    Returns:
        A list of length len(closes). Indices < (period - 1) are None
        (warmup). From index (period - 1) onward every entry is a float.

    Raises:
        ValueError: on period <= 0 or mismatched input lengths.
    """
    if period <= 0:
        raise ValueError(f"period must be positive, got {period}")
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError(
            f"highs/lows/closes lengths must match, got "
            f"{len(highs)}/{len(lows)}/{len(closes)}"
        )

    n = len(closes)
    out: list[float | None] = [None] * n
    if n == 0:
        return out

    # Step 1: per-bar True Range.
    tr: list[float] = [0.0] * n
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        a = highs[i] - lows[i]
        b = abs(highs[i] - closes[i - 1])
        c = abs(lows[i] - closes[i - 1])
        tr[i] = max(a, b, c)

    if n < period:
        return out

    # Step 2: seed ATR at index period-1 as simple mean of first `period` TRs.
    seed = sum(tr[0:period]) / period
    out[period - 1] = seed

    # Step 3: Wilder recurrence thereafter.
    prev = seed
    for i in range(period, n):
        prev = ((period - 1) * prev + tr[i]) / period
        out[i] = prev

    return out
