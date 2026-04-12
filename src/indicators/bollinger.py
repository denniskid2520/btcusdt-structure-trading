"""Bollinger Bands primitive — canonical population-std definition.

Used by Strategy C v2 feature family A (price/technical). Pure function,
no dependencies beyond the standard library.

Formula (Bollinger 1993):
    middle = SMA(close, period)
    std    = population standard deviation of close over period (ddof=0)
    upper  = middle + k * std
    lower  = middle - k * std
    width  = upper - lower
    pctb   = (close - lower) / width, defaults to 0.5 when width == 0

Warmup:
    The first (period - 1) bars have no full window, so `bollinger_bands`
    returns None at those indices. From index (period - 1) onward every
    point is populated.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class BollingerBand:
    """One bar's Bollinger Band state.

    All prices in the same units as input closes. `width = upper - lower`.
    `pctb` is the close's position within the band: 0.0 = at lower,
    1.0 = at upper, 0.5 = at middle. When the series is constant over the
    window (width == 0), pctb defaults to 0.5.
    """
    upper: float
    middle: float
    lower: float
    width: float
    pctb: float


def bollinger_bands(
    closes: list[float],
    *,
    period: int = 20,
    k: float = 2.0,
) -> list[BollingerBand | None]:
    """Compute Bollinger Bands for a close-price stream.

    Args:
        closes: Sequential close prices.
        period: Rolling window length. Must be > 0.
        k: Band width in standard deviations. Must be >= 0.

    Returns:
        A list the same length as `closes`. Entries at indices < (period - 1)
        are None (warmup). From index (period - 1) onward every entry is a
        BollingerBand computed over the `period`-wide window ending at that
        index.

    Raises:
        ValueError: if `period <= 0` or `k < 0`.
    """
    if period <= 0:
        raise ValueError(f"period must be positive, got {period}")
    if k < 0:
        raise ValueError(f"k must be non-negative, got {k}")

    n = len(closes)
    out: list[BollingerBand | None] = [None] * n

    for i in range(period - 1, n):
        window = closes[i - period + 1 : i + 1]
        mean = sum(window) / period
        variance = sum((x - mean) ** 2 for x in window) / period  # population std (ddof=0)
        std = math.sqrt(variance)
        upper = mean + k * std
        lower = mean - k * std
        width = upper - lower
        if width == 0.0:
            pctb = 0.5
        else:
            pctb = (closes[i] - lower) / width
        out[i] = BollingerBand(
            upper=upper,
            middle=mean,
            lower=lower,
            width=width,
            pctb=pctb,
        )

    return out
