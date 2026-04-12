"""Tests for Strategy C v2 multi-timeframe alignment and rules.

Phase 3 MTF framework — uses higher timeframes (4h, 1h) for regime /
direction and confirmation, with execution on a lower timeframe (15m).

The critical anti-leakage property being tested:

    A higher-timeframe feature at bar k (representing period [T_k, T_{k+1}))
    is only "known" at time T_{k+1} — i.e., at the END of the period. For a
    lower-timeframe bar at time t, the most recent higher-TF feature we can
    use is the one whose period END is strictly <= t. Using a feature from
    a period that hasn't closed yet would be look-ahead.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from strategies.strategy_c_v2_mtf import (
    align_higher_to_lower,
    mtf_trend_signals,
)


# ── align_higher_to_lower ───────────────────────────────────────────


def _ts_range(start: datetime, n: int, step_min: int) -> list[datetime]:
    return [start + timedelta(minutes=step_min * i) for i in range(n)]


def test_align_higher_to_lower_basic_4h_to_15m() -> None:
    """A 15m bar in [04:00, 08:00) should read the 00:00-04:00 4h feature."""
    # 4h bars at 00:00, 04:00, 08:00, 12:00 — each represents [T, T+4h)
    h_ts = _ts_range(datetime(2024, 1, 1), 4, step_min=240)
    h_vals = [10.0, 20.0, 30.0, 40.0]

    # 15m bars across the same day
    l_ts = _ts_range(datetime(2024, 1, 1), 96, step_min=15)

    out = align_higher_to_lower(l_ts, h_ts, h_vals, higher_period=timedelta(hours=4))

    # Bars at 00:00-03:45 (indices 0-15): before any 4h bar has closed → None
    for i in range(16):
        assert out[i] is None, f"expected None at i={i}, got {out[i]}"
    # Bar at 04:00 (index 16): 00:00 bar has just closed → read h_vals[0]
    assert out[16] == 10.0
    # Bar at 07:45 (index 31): still on the 00:00 bar
    assert out[31] == 10.0
    # Bar at 08:00 (index 32): 04:00 bar has just closed → read h_vals[1]
    assert out[32] == 20.0
    # Bar at 11:45 (index 47): still on the 04:00 bar
    assert out[47] == 20.0
    # Bar at 12:00 (index 48): 08:00 bar has just closed → read h_vals[2]
    assert out[48] == 30.0


def test_align_higher_to_lower_warmup_only_none() -> None:
    """All 15m bars are before the first 4h close → all None."""
    h_ts = [datetime(2024, 1, 1, 4, 0)]  # single 4h bar representing [04:00, 08:00)
    h_vals = [42.0]
    # All the 15m bars here are in [04:00, 08:00), before the 4h bar closes at 08:00
    l_ts = _ts_range(datetime(2024, 1, 1, 4, 0), 16, step_min=15)
    out = align_higher_to_lower(l_ts, h_ts, h_vals, higher_period=timedelta(hours=4))
    for v in out:
        assert v is None


def test_align_higher_to_lower_empty_higher_returns_all_none() -> None:
    l_ts = _ts_range(datetime(2024, 1, 1), 10, step_min=15)
    out = align_higher_to_lower(l_ts, [], [], higher_period=timedelta(hours=4))
    assert out == [None] * 10


def test_align_higher_to_lower_empty_lower_returns_empty() -> None:
    out = align_higher_to_lower([], [datetime(2024, 1, 1)], [1.0], higher_period=timedelta(hours=4))
    assert out == []


def test_align_higher_to_lower_mismatch_lengths_raises() -> None:
    l_ts = [datetime(2024, 1, 1)]
    with pytest.raises(ValueError):
        align_higher_to_lower(l_ts, [datetime(2024, 1, 1)], [1.0, 2.0], higher_period=timedelta(hours=4))


def test_align_higher_to_lower_none_values_pass_through() -> None:
    """Warmup None values in higher_vals should propagate cleanly."""
    h_ts = _ts_range(datetime(2024, 1, 1), 3, step_min=240)
    h_vals: list[float | None] = [None, 20.0, 30.0]
    l_ts = _ts_range(datetime(2024, 1, 1), 48, step_min=15)
    out = align_higher_to_lower(l_ts, h_ts, h_vals, higher_period=timedelta(hours=4))
    # 00:00-03:45: before any 4h close → None (aligned warmup)
    for i in range(16):
        assert out[i] is None
    # 04:00-07:45: reads h_vals[0] which is None → stays None
    for i in range(16, 32):
        assert out[i] is None
    # 08:00-11:45: reads h_vals[1] = 20.0
    for i in range(32, 48):
        assert out[i] == 20.0


# ── mtf_trend_signals ───────────────────────────────────────────────


def test_mtf_trend_signals_both_above_midline_goes_long() -> None:
    # higher = 4h, lower = 15m; simulate aligned streams directly
    higher_rsi = [60.0]
    lower_rsi = [55.0]
    out = mtf_trend_signals(higher_rsi, lower_rsi, higher_threshold=50.0, lower_threshold=50.0)
    assert out == [1]


def test_mtf_trend_signals_both_below_midline_goes_short() -> None:
    higher_rsi = [40.0]
    lower_rsi = [45.0]
    out = mtf_trend_signals(higher_rsi, lower_rsi, higher_threshold=50.0, lower_threshold=50.0)
    assert out == [-1]


def test_mtf_trend_signals_mixed_signals_stays_flat() -> None:
    # higher says long, lower says short → flat (no agreement)
    higher_rsi = [60.0]
    lower_rsi = [45.0]
    out = mtf_trend_signals(higher_rsi, lower_rsi, higher_threshold=50.0, lower_threshold=50.0)
    assert out == [0]


def test_mtf_trend_signals_none_inputs_flat() -> None:
    """None at either level forces flat (can't confirm)."""
    assert mtf_trend_signals([None], [60.0], higher_threshold=50.0, lower_threshold=50.0) == [0]
    assert mtf_trend_signals([60.0], [None], higher_threshold=50.0, lower_threshold=50.0) == [0]


def test_mtf_trend_signals_threshold_respected() -> None:
    higher_rsi = [72.0, 68.0]
    lower_rsi = [55.0, 55.0]
    # With higher_threshold=70, bar 0 passes, bar 1 fails.
    out = mtf_trend_signals(
        higher_rsi, lower_rsi,
        higher_threshold=70.0, lower_threshold=50.0,
    )
    assert out == [1, 0]


def test_mtf_trend_signals_length_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        mtf_trend_signals([1.0], [1.0, 2.0], higher_threshold=50.0, lower_threshold=50.0)
