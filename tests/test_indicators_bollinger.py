"""Tests for Bollinger Bands primitive.

Bollinger Bands:
    middle = SMA(close, period)
    std    = population std of close over period (ddof=0, canonical Bollinger)
    upper  = middle + k * std
    lower  = middle - k * std
    width  = upper - lower
    pctb   = (close - lower) / (upper - lower), 0.5 when width == 0

The primitive returns one BollingerBand per input close, with None for warmup
bars (index < period - 1).
"""
from __future__ import annotations

import math

import pytest

from indicators.bollinger import BollingerBand, bollinger_bands


# ── warmup and shape ─────────────────────────────────────────────────


def test_bollinger_returns_one_entry_per_close() -> None:
    closes = [1.0, 2.0, 3.0, 4.0, 5.0]
    out = bollinger_bands(closes, period=3, k=2.0)
    assert len(out) == len(closes)


def test_bollinger_warmup_is_none_before_period() -> None:
    closes = [1.0, 2.0, 3.0, 4.0, 5.0]
    out = bollinger_bands(closes, period=3, k=2.0)
    assert out[0] is None
    assert out[1] is None
    # Index 2 is the first full-window point (period=3).
    assert out[2] is not None


def test_bollinger_empty_input_returns_empty() -> None:
    assert bollinger_bands([], period=20, k=2.0) == []


def test_bollinger_input_shorter_than_period_all_none() -> None:
    closes = [1.0, 2.0, 3.0]
    out = bollinger_bands(closes, period=5, k=2.0)
    assert out == [None, None, None]


# ── value correctness ───────────────────────────────────────────────


def test_bollinger_middle_equals_sma() -> None:
    closes = [1.0, 2.0, 3.0, 4.0, 5.0]
    out = bollinger_bands(closes, period=5, k=2.0)
    last = out[-1]
    assert last is not None
    assert last.middle == pytest.approx(3.0)  # mean of 1..5


def test_bollinger_population_std_k2_known_values() -> None:
    """For [1,2,3,4,5], pop std = sqrt(2), so ±k=2 bands = 3 ± 2*sqrt(2)."""
    closes = [1.0, 2.0, 3.0, 4.0, 5.0]
    out = bollinger_bands(closes, period=5, k=2.0)
    last = out[-1]
    assert last is not None
    expected_std = math.sqrt(2.0)
    assert last.upper == pytest.approx(3.0 + 2.0 * expected_std)
    assert last.lower == pytest.approx(3.0 - 2.0 * expected_std)


def test_bollinger_upper_lower_symmetric_around_middle() -> None:
    closes = [10.0, 11.0, 12.0, 11.0, 13.0, 14.0, 12.0]
    out = bollinger_bands(closes, period=5, k=2.0)
    for band in out:
        if band is None:
            continue
        assert band.upper - band.middle == pytest.approx(band.middle - band.lower)


def test_bollinger_width_equals_upper_minus_lower() -> None:
    closes = [10.0, 11.0, 12.0, 11.0, 13.0, 14.0, 12.0]
    out = bollinger_bands(closes, period=5, k=2.0)
    for band in out:
        if band is None:
            continue
        assert band.width == pytest.approx(band.upper - band.lower)


def test_bollinger_pctb_at_upper_is_one() -> None:
    """When close == upper band, %B should be 1.0."""
    # Constant series except last pushed up; period=5 with last close at upper.
    closes = [10.0, 10.0, 10.0, 10.0, 20.0]
    out = bollinger_bands(closes, period=5, k=2.0)
    last = out[-1]
    assert last is not None
    # Upper computed from all 5 points; last close = 20 may or may not equal upper.
    # Easier: manually target a case where close == upper.
    # mean = 12, pop_std = sqrt(((2)^2 + 3*(2)^2 + (8)^2) / 5)
    #                   = sqrt((4+4+4+4+64)/5) = sqrt(80/5) = sqrt(16) = 4
    # upper (k=2) = 12 + 8 = 20; lower = 12 - 8 = 4; last close = 20 → %B = 1
    assert last.middle == pytest.approx(12.0)
    assert last.upper == pytest.approx(20.0)
    assert last.lower == pytest.approx(4.0)
    assert last.pctb == pytest.approx(1.0)


def test_bollinger_pctb_at_lower_is_zero() -> None:
    """When close == lower band, %B should be 0.0."""
    # Mirror of the above: last close pulls down.
    closes = [20.0, 20.0, 20.0, 20.0, 10.0]
    out = bollinger_bands(closes, period=5, k=2.0)
    last = out[-1]
    assert last is not None
    # mean = 18, pop_std = sqrt(((2)^2 * 4 + (8)^2) / 5) = sqrt(80/5) = 4
    # upper = 26, lower = 10, last close = 10 → %B = 0
    assert last.middle == pytest.approx(18.0)
    assert last.upper == pytest.approx(26.0)
    assert last.lower == pytest.approx(10.0)
    assert last.pctb == pytest.approx(0.0)


def test_bollinger_pctb_at_middle_is_half() -> None:
    closes = [9.0, 10.0, 11.0, 10.0, 10.0]
    out = bollinger_bands(closes, period=5, k=2.0)
    last = out[-1]
    assert last is not None
    assert last.middle == pytest.approx(10.0)
    # Last close = 10 = middle → pctb = 0.5.
    assert last.pctb == pytest.approx(0.5)


def test_bollinger_zero_width_pctb_defaults_to_half() -> None:
    """Constant series → std=0 → upper=middle=lower. %B defaults to 0.5."""
    closes = [5.0] * 5
    out = bollinger_bands(closes, period=5, k=2.0)
    last = out[-1]
    assert last is not None
    assert last.upper == pytest.approx(5.0)
    assert last.lower == pytest.approx(5.0)
    assert last.middle == pytest.approx(5.0)
    assert last.width == pytest.approx(0.0)
    assert last.pctb == pytest.approx(0.5)


def test_bollinger_k_parameter_scales_bands() -> None:
    closes = [1.0, 2.0, 3.0, 4.0, 5.0]
    out_k1 = bollinger_bands(closes, period=5, k=1.0)
    out_k2 = bollinger_bands(closes, period=5, k=2.0)
    b1 = out_k1[-1]
    b2 = out_k2[-1]
    assert b1 is not None and b2 is not None
    # k=2 bands should be exactly twice as wide as k=1 bands.
    assert (b2.upper - b2.middle) == pytest.approx(2.0 * (b1.upper - b1.middle))
    assert (b2.middle - b2.lower) == pytest.approx(2.0 * (b1.middle - b1.lower))


# ── validation ──────────────────────────────────────────────────────


def test_bollinger_period_must_be_positive() -> None:
    with pytest.raises(ValueError):
        bollinger_bands([1.0, 2.0], period=0, k=2.0)
    with pytest.raises(ValueError):
        bollinger_bands([1.0, 2.0], period=-1, k=2.0)


def test_bollinger_k_must_be_non_negative() -> None:
    with pytest.raises(ValueError):
        bollinger_bands([1.0, 2.0, 3.0], period=3, k=-1.0)
