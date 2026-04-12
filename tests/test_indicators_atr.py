"""Tests for the ATR (Average True Range) primitive.

Wilder (1978) definition:
    TR[0] = high[0] - low[0]       # no previous close → just the bar range
    TR[i] = max(high[i] - low[i],
                |high[i] - close[i-1]|,
                |low[i]  - close[i-1]|)  for i >= 1
    ATR[period - 1] = mean(TR[0..period-1])           # simple mean seed
    ATR[i]          = ((period - 1) * ATR[i-1] + TR[i]) / period  for i >= period

Equivalent to an EMA with α = 1/period (Wilder's smoothing).
"""
from __future__ import annotations

import pytest

from indicators.atr import atr


# ── shape / warmup ───────────────────────────────────────────────────


def test_atr_shape_one_per_close() -> None:
    highs = [10.0, 12.0, 14.0, 13.0, 15.0]
    lows = [8.0, 9.0, 10.0, 11.0, 12.0]
    closes = [9.0, 11.0, 13.0, 12.0, 14.0]
    out = atr(highs, lows, closes, period=3)
    assert len(out) == len(closes)


def test_atr_warmup_before_period_is_none() -> None:
    highs = [10.0, 12.0, 14.0, 13.0, 15.0]
    lows = [8.0, 9.0, 10.0, 11.0, 12.0]
    closes = [9.0, 11.0, 13.0, 12.0, 14.0]
    out = atr(highs, lows, closes, period=3)
    assert out[0] is None
    assert out[1] is None
    assert out[2] is not None  # first full-window value


def test_atr_empty_input_returns_empty() -> None:
    assert atr([], [], [], period=14) == []


def test_atr_input_shorter_than_period_all_none() -> None:
    highs = [10.0, 12.0]
    lows = [8.0, 9.0]
    closes = [9.0, 11.0]
    out = atr(highs, lows, closes, period=5)
    assert out == [None, None]


# ── true range correctness ──────────────────────────────────────────


def test_atr_first_tr_uses_high_minus_low() -> None:
    """TR[0] has no prev close; defined as high[0] - low[0]."""
    highs = [10.0, 10.0, 10.0]
    lows = [8.0, 8.0, 8.0]
    closes = [9.0, 9.0, 9.0]
    # All bars identical → TR[i>0] = max(2, |10-9|, |8-9|) = 2. TR[0] = 2.
    # First ATR seed at i=2 = mean(2, 2, 2) = 2.
    out = atr(highs, lows, closes, period=3)
    assert out[2] == pytest.approx(2.0)


def test_atr_tr_uses_max_of_three_for_gap_up() -> None:
    """Gap-up bar: previous close below current low → |low - prev_close| dominates."""
    highs = [10.0, 20.0]
    lows = [9.0, 15.0]
    closes = [9.5, 18.0]
    # TR[1] = max(20-15, |20-9.5|, |15-9.5|) = max(5, 10.5, 5.5) = 10.5
    # period=2 → seed at i=1 = mean(TR[0], TR[1]) = (1.0 + 10.5) / 2 = 5.75
    out = atr(highs, lows, closes, period=2)
    assert out[1] == pytest.approx(5.75)


def test_atr_tr_uses_max_of_three_for_gap_down() -> None:
    """Gap-down bar: previous close above current high → |high - prev_close| dominates."""
    highs = [20.0, 10.0]
    lows = [18.0, 8.0]
    closes = [19.5, 9.0]
    # TR[1] = max(10-8, |10-19.5|, |8-19.5|) = max(2, 9.5, 11.5) = 11.5
    # period=2 seed = (2.0 + 11.5) / 2 = 6.75
    out = atr(highs, lows, closes, period=2)
    assert out[1] == pytest.approx(6.75)


# ── Wilder smoothing correctness ────────────────────────────────────


def test_atr_first_atr_is_simple_mean_of_first_period_trs() -> None:
    highs = [10.0, 12.0, 14.0, 13.0, 15.0]
    lows = [8.0, 9.0, 10.0, 11.0, 12.0]
    closes = [9.0, 11.0, 13.0, 12.0, 14.0]
    # TR[0] = 10 - 8 = 2
    # TR[1] = max(12-9, |12-9|, |9-9|) = 3
    # TR[2] = max(14-10, |14-11|, |10-11|) = 4
    # ATR[2] = (2 + 3 + 4) / 3 = 3.0
    out = atr(highs, lows, closes, period=3)
    assert out[2] == pytest.approx(3.0)


def test_atr_subsequent_atr_uses_wilder_smoothing() -> None:
    highs = [10.0, 12.0, 14.0, 13.0, 15.0]
    lows = [8.0, 9.0, 10.0, 11.0, 12.0]
    closes = [9.0, 11.0, 13.0, 12.0, 14.0]
    # Seed ATR[2] = 3.0 (as above)
    # TR[3] = max(13-11, |13-13|, |11-13|) = 2
    # ATR[3] = ((3-1)*3.0 + 2) / 3 = 8/3 ≈ 2.6666...
    # TR[4] = max(15-12, |15-12|, |12-12|) = 3
    # ATR[4] = ((3-1)*ATR[3] + 3) / 3 = (16/3 + 3) / 3 = (25/3) / 3 = 25/9 ≈ 2.7777...
    out = atr(highs, lows, closes, period=3)
    assert out[3] == pytest.approx(8.0 / 3.0)
    assert out[4] == pytest.approx(25.0 / 9.0)


def test_atr_is_strictly_non_negative() -> None:
    highs = [100.0, 101.0, 102.0, 99.0, 98.0]
    lows = [99.0, 100.0, 100.5, 97.0, 96.0]
    closes = [99.5, 100.5, 101.5, 98.0, 97.0]
    out = atr(highs, lows, closes, period=3)
    for v in out:
        if v is not None:
            assert v >= 0.0


# ── validation ──────────────────────────────────────────────────────


def test_atr_period_must_be_positive() -> None:
    with pytest.raises(ValueError):
        atr([1.0, 2.0], [0.0, 1.0], [0.5, 1.5], period=0)
    with pytest.raises(ValueError):
        atr([1.0, 2.0], [0.0, 1.0], [0.5, 1.5], period=-1)


def test_atr_validation_lengths_must_match() -> None:
    with pytest.raises(ValueError):
        atr([1.0, 2.0], [0.0], [0.5, 1.5], period=2)
    with pytest.raises(ValueError):
        atr([1.0, 2.0], [0.0, 1.0], [0.5], period=2)
