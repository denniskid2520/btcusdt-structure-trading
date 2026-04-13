"""Tests for the Stochastic Oscillator primitive.

Full Stochastic (Lane):
    raw_k  = 100 * (close - lowest_low_n) / (highest_high_n - lowest_low_n)
    slow_k = SMA(raw_k, smooth_k)
    slow_d = SMA(slow_k, smooth_d)

Warmup:
    raw_k needs k_period bars
    slow_k needs k_period + smooth_k - 1 bars
    slow_d needs k_period + smooth_k + smooth_d - 2 bars

Flat-range edge case: if highest_high == lowest_low over the window, raw_k is
undefined (0/0). We default it to 50.0 (mid-range).
"""
from __future__ import annotations

import pytest

from indicators.stochastic import StochasticPoint, stochastic


# ── shape / warmup ───────────────────────────────────────────────────


def test_stochastic_shape_one_per_close() -> None:
    highs = [10.0, 12.0, 14.0, 13.0, 15.0]
    lows = [8.0, 9.0, 10.0, 11.0, 12.0]
    closes = [9.0, 11.0, 13.0, 12.0, 14.0]
    out = stochastic(highs, lows, closes, k_period=3, smooth_k=1, smooth_d=1)
    assert len(out) == len(closes)


def test_stochastic_warmup_before_k_period_is_none() -> None:
    highs = [10.0, 12.0, 14.0, 13.0, 15.0]
    lows = [8.0, 9.0, 10.0, 11.0, 12.0]
    closes = [9.0, 11.0, 13.0, 12.0, 14.0]
    out = stochastic(highs, lows, closes, k_period=3, smooth_k=1, smooth_d=1)
    assert out[0] is None
    assert out[1] is None
    assert out[2] is not None  # first full window


def test_stochastic_empty_input_returns_empty() -> None:
    assert stochastic([], [], [], k_period=14) == []


def test_stochastic_input_shorter_than_k_period_all_none() -> None:
    highs = [10.0, 12.0]
    lows = [8.0, 9.0]
    closes = [9.0, 11.0]
    out = stochastic(highs, lows, closes, k_period=5, smooth_k=1, smooth_d=1)
    assert out == [None, None]


def test_stochastic_slow_k_warmup_is_k_period_plus_smooth_k_minus_1() -> None:
    highs = [float(i) for i in range(10, 20)]
    lows = [float(i) for i in range(5, 15)]
    closes = [float(i) for i in range(7, 17)]
    out = stochastic(highs, lows, closes, k_period=3, smooth_k=3, smooth_d=1)
    # raw_k fills from index 2; slow_k (smooth=3) fills from index 2 + 2 = 4.
    for i in range(4):
        assert out[i] is None, f"expected None at i={i}"
    assert out[4] is not None


def test_stochastic_slow_d_warmup_is_k_period_plus_smooth_k_plus_smooth_d_minus_2() -> None:
    highs = [float(i) for i in range(10, 20)]
    lows = [float(i) for i in range(5, 15)]
    closes = [float(i) for i in range(7, 17)]
    out = stochastic(highs, lows, closes, k_period=3, smooth_k=3, smooth_d=3)
    # raw_k fills from i=2; slow_k from i=4; slow_d from i=6. Before that, slow_d is None.
    for i in range(6):
        assert out[i] is None or out[i].d is None
    assert out[6] is not None
    assert out[6].d is not None


# ── value correctness (smooth_k=smooth_d=1 so slow_k == raw_k, slow_d == slow_k) ──


def test_stochastic_raw_k_formula_at_first_full_window() -> None:
    highs = [10.0, 12.0, 14.0]
    lows = [8.0, 9.0, 10.0]
    closes = [9.0, 11.0, 13.0]
    out = stochastic(highs, lows, closes, k_period=3, smooth_k=1, smooth_d=1)
    p = out[2]
    assert p is not None
    # hh = 14, ll = 8, close = 13 → raw_k = 100 * (13 - 8) / (14 - 8) = 83.3333...
    assert p.k == pytest.approx(100.0 * (13.0 - 8.0) / (14.0 - 8.0))


def test_stochastic_at_high_of_range_is_100() -> None:
    highs = [10.0, 12.0, 14.0]
    lows = [8.0, 9.0, 10.0]
    closes = [9.0, 11.0, 14.0]  # last close == hh
    out = stochastic(highs, lows, closes, k_period=3, smooth_k=1, smooth_d=1)
    assert out[2] is not None
    assert out[2].k == pytest.approx(100.0)


def test_stochastic_at_low_of_range_is_0() -> None:
    highs = [10.0, 12.0, 14.0]
    lows = [8.0, 9.0, 10.0]
    closes = [9.0, 11.0, 8.0]  # last close == ll
    out = stochastic(highs, lows, closes, k_period=3, smooth_k=1, smooth_d=1)
    assert out[2] is not None
    assert out[2].k == pytest.approx(0.0)


def test_stochastic_at_mid_range_is_50() -> None:
    highs = [10.0, 12.0, 14.0]
    lows = [8.0, 9.0, 10.0]
    closes = [9.0, 11.0, 11.0]  # last close at mid of [8,14]
    out = stochastic(highs, lows, closes, k_period=3, smooth_k=1, smooth_d=1)
    assert out[2] is not None
    assert out[2].k == pytest.approx(50.0)


def test_stochastic_flat_range_defaults_k_to_50() -> None:
    highs = [10.0, 10.0, 10.0]
    lows = [10.0, 10.0, 10.0]
    closes = [10.0, 10.0, 10.0]
    out = stochastic(highs, lows, closes, k_period=3, smooth_k=1, smooth_d=1)
    assert out[2] is not None
    assert out[2].k == pytest.approx(50.0)


def test_stochastic_smooth_k_is_sma_of_raw_k() -> None:
    highs = [10.0, 12.0, 14.0, 15.0, 16.0]
    lows = [8.0, 9.0, 10.0, 11.0, 12.0]
    closes = [9.0, 11.0, 13.0, 14.0, 15.0]
    out_raw = stochastic(highs, lows, closes, k_period=3, smooth_k=1, smooth_d=1)
    out_smoothed = stochastic(highs, lows, closes, k_period=3, smooth_k=3, smooth_d=1)

    # At index 4, smoothed.k = (raw[2].k + raw[3].k + raw[4].k) / 3
    raw2 = out_raw[2]
    raw3 = out_raw[3]
    raw4 = out_raw[4]
    assert raw2 is not None and raw3 is not None and raw4 is not None
    expected_slow_k = (raw2.k + raw3.k + raw4.k) / 3.0

    p = out_smoothed[4]
    assert p is not None
    assert p.k == pytest.approx(expected_slow_k)


def test_stochastic_slow_d_is_sma_of_slow_k() -> None:
    highs = [10.0, 12.0, 14.0, 15.0, 16.0, 18.0, 20.0]
    lows = [8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 14.0]
    closes = [9.0, 11.0, 13.0, 14.0, 15.0, 17.0, 19.0]

    # Use smooth_k=1 so slow_k == raw_k and slow_d = SMA(raw_k, 3).
    out = stochastic(highs, lows, closes, k_period=3, smooth_k=1, smooth_d=3)
    # raw_k fills from i=2. slow_d (smooth=3) fills from i=2+2=4.
    p = out[4]
    assert p is not None
    assert p.d is not None
    # slow_d at i=4 = mean(raw_k[2], raw_k[3], raw_k[4])
    # use out_noD to fetch raw values
    out_raw = stochastic(highs, lows, closes, k_period=3, smooth_k=1, smooth_d=1)
    expected = (out_raw[2].k + out_raw[3].k + out_raw[4].k) / 3.0
    assert p.d == pytest.approx(expected)


# ── validation ──────────────────────────────────────────────────────


def test_stochastic_validation_k_period_positive() -> None:
    with pytest.raises(ValueError):
        stochastic([1.0, 2.0], [0.0, 1.0], [0.5, 1.5], k_period=0)


def test_stochastic_validation_smooth_k_positive() -> None:
    with pytest.raises(ValueError):
        stochastic([1.0, 2.0], [0.0, 1.0], [0.5, 1.5], k_period=3, smooth_k=0)


def test_stochastic_validation_smooth_d_positive() -> None:
    with pytest.raises(ValueError):
        stochastic([1.0, 2.0], [0.0, 1.0], [0.5, 1.5], k_period=3, smooth_k=1, smooth_d=0)


def test_stochastic_validation_lengths_must_match() -> None:
    with pytest.raises(ValueError):
        stochastic([1.0, 2.0], [0.0], [0.5, 1.5], k_period=3)
    with pytest.raises(ValueError):
        stochastic([1.0, 2.0], [0.0, 1.0], [0.5], k_period=3)
