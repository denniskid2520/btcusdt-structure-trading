"""Tests for the Strategy C v2 rolling walk-forward split harness.

Contract:
    walk_forward_splits(timestamps, *, train_months, test_months, step_months)
        -> list[WalkForwardSplit]

Semantics:
    - Chronological splits: first split's train starts at timestamps[0]
    - train window = [train_start, train_end), calendar-month length train_months
    - test window  = [test_start, test_end),  calendar-month length test_months
    - test_start == train_end (strict adjacency, no gap)
    - Next split: anchor += step_months
    - The last split is included iff test_end <= timestamps[-1] — no partial
      test windows at the right edge
    - timestamps must be strictly ascending (ValueError otherwise)
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from research.strategy_c_v2_walk_forward import (
    WalkForwardSplit,
    _add_months,
    walk_forward_splits,
)


# ── _add_months helper ──────────────────────────────────────────────


def test_add_months_simple() -> None:
    assert _add_months(datetime(2020, 1, 15), 1) == datetime(2020, 2, 15)


def test_add_months_year_rollover() -> None:
    assert _add_months(datetime(2020, 11, 15), 3) == datetime(2021, 2, 15)


def test_add_months_24_months_is_2_years() -> None:
    assert _add_months(datetime(2020, 4, 5), 24) == datetime(2022, 4, 5)


def test_add_months_zero_is_identity() -> None:
    assert _add_months(datetime(2020, 4, 5), 0) == datetime(2020, 4, 5)


def test_add_months_clamps_day_to_month_length() -> None:
    """Jan 31 + 1 month must yield Feb 28 (non-leap year)."""
    assert _add_months(datetime(2021, 1, 31), 1) == datetime(2021, 2, 28)


def test_add_months_leap_year_feb() -> None:
    """Jan 31 + 1 month in a leap year = Feb 29."""
    assert _add_months(datetime(2020, 1, 31), 1) == datetime(2020, 2, 29)


# ── helpers for test series ─────────────────────────────────────────


def _daily_ts(start: datetime, n_days: int) -> list[datetime]:
    return [start + timedelta(days=i) for i in range(n_days)]


def _six_year_daily() -> list[datetime]:
    """2020-04-05 → 2026-04-05 inclusive, daily cadence (2193 entries)."""
    start = datetime(2020, 4, 5)
    end = datetime(2026, 4, 5)
    n = (end - start).days + 1
    return _daily_ts(start, n)


# ── empty / degenerate ──────────────────────────────────────────────


def test_walk_forward_empty_series_returns_empty() -> None:
    assert walk_forward_splits([], train_months=24, test_months=6, step_months=6) == []


def test_walk_forward_series_shorter_than_train_window_returns_empty() -> None:
    ts = _daily_ts(datetime(2020, 1, 1), 30)
    out = walk_forward_splits(ts, train_months=24, test_months=6, step_months=6)
    assert out == []


def test_walk_forward_series_shorter_than_train_plus_test_returns_empty() -> None:
    """28 months of data should not admit a 24/6 split (short by 2 months)."""
    start = datetime(2020, 1, 1)
    end = _add_months(start, 28)
    n_days = (end - start).days + 1
    ts = _daily_ts(start, n_days)
    out = walk_forward_splits(ts, train_months=24, test_months=6, step_months=6)
    assert out == []


# ── split count on realistic series ─────────────────────────────────


def test_walk_forward_6_year_daily_yields_8_splits() -> None:
    """6 years of daily bars with 24/6/6 → 8 non-overlapping test windows."""
    ts = _six_year_daily()
    out = walk_forward_splits(ts, train_months=24, test_months=6, step_months=6)
    assert len(out) == 8


def test_walk_forward_exactly_30_months_yields_one_split() -> None:
    """30 months = 24 train + 6 test = exactly one split."""
    start = datetime(2020, 1, 1)
    end = _add_months(start, 30)
    n = (end - start).days + 1
    ts = _daily_ts(start, n)
    out = walk_forward_splits(ts, train_months=24, test_months=6, step_months=6)
    assert len(out) == 1


# ── adjacency + indexing ────────────────────────────────────────────


def test_walk_forward_train_ends_where_test_starts() -> None:
    ts = _six_year_daily()
    out = walk_forward_splits(ts, train_months=24, test_months=6, step_months=6)
    for s in out:
        assert s.train_end == s.test_start
        assert s.train_start < s.train_end < s.test_end


def test_walk_forward_train_hi_equals_test_lo() -> None:
    """No index leakage across the split boundary."""
    ts = _six_year_daily()
    out = walk_forward_splits(ts, train_months=24, test_months=6, step_months=6)
    for s in out:
        assert s.train_hi == s.test_lo


def test_walk_forward_indices_point_to_correct_timestamps() -> None:
    ts = _six_year_daily()
    out = walk_forward_splits(ts, train_months=24, test_months=6, step_months=6)
    for s in out:
        assert ts[s.train_lo] == s.train_start
        assert ts[s.test_lo] == s.test_start
        # hi is exclusive — the boundary ts is either at or just past train_end/test_end
        if s.train_hi < len(ts):
            assert ts[s.train_hi] >= s.train_end
        if s.test_hi < len(ts):
            assert ts[s.test_hi] >= s.test_end


def test_walk_forward_split_index_is_zero_based_monotonic() -> None:
    ts = _six_year_daily()
    out = walk_forward_splits(ts, train_months=24, test_months=6, step_months=6)
    for i, s in enumerate(out):
        assert s.index == i


# ── step semantics ──────────────────────────────────────────────────


def test_walk_forward_step_equals_test_gives_contiguous_non_overlapping_test_windows() -> None:
    ts = _six_year_daily()
    out = walk_forward_splits(ts, train_months=24, test_months=6, step_months=6)
    for a, b in zip(out, out[1:]):
        assert a.test_end == b.test_start
        assert a.test_hi == b.test_lo  # contiguous at index level too


def test_walk_forward_step_less_than_test_allows_overlapping_test_windows() -> None:
    """step_months < test_months → consecutive test windows overlap."""
    ts = _six_year_daily()
    out = walk_forward_splits(ts, train_months=24, test_months=12, step_months=6)
    assert len(out) >= 2
    for a, b in zip(out, out[1:]):
        # Next split's test starts before this split's test ends.
        assert b.test_start < a.test_end


def test_walk_forward_step_equals_train_gives_non_overlapping_train_windows() -> None:
    ts = _six_year_daily()
    out = walk_forward_splits(ts, train_months=24, test_months=6, step_months=24)
    for a, b in zip(out, out[1:]):
        assert a.train_end <= b.train_start


# ── coverage sanity on a realistic 15m series ───────────────────────


def test_walk_forward_15m_6y_series_yields_8_splits_and_realistic_sizes() -> None:
    """Sanity check on ≈6 years of 15m bars, exactly what v2 will run on."""
    start = datetime(2020, 4, 5)
    # 6 years * 365.25 days * 96 bars/day ≈ 210,384. Use a clean round number.
    n = 6 * 365 * 96 + 2 * 96  # 6y + 2 leap days
    ts = [start + timedelta(minutes=15 * i) for i in range(n)]
    out = walk_forward_splits(ts, train_months=24, test_months=6, step_months=6)
    assert len(out) == 8
    # Each train slice should be ≈ 24 months * 30 * 96 ≈ 69,120 bars (± month-length variation).
    for s in out:
        span = s.train_hi - s.train_lo
        assert 60_000 < span < 80_000, f"train span {span} outside plausible range"
        test_span = s.test_hi - s.test_lo
        assert 15_000 < test_span < 20_000, f"test span {test_span} outside plausible range"


# ── aggregated test-slice coverage ──────────────────────────────────


def test_walk_forward_aggregated_test_coverage_no_gaps_when_step_equals_test() -> None:
    """When step == test_months, concatenated test slices cover a contiguous range
    equal to (num_splits * test_months) right after the first train window."""
    ts = _six_year_daily()
    out = walk_forward_splits(ts, train_months=24, test_months=6, step_months=6)
    assert out[0].test_start == _add_months(ts[0], 24)
    for a, b in zip(out, out[1:]):
        assert a.test_end == b.test_start
    # Last test_end should be exactly train_months + num_splits * test_months into the series.
    expected_last_end = _add_months(ts[0], 24 + len(out) * 6)
    assert out[-1].test_end == expected_last_end


# ── validation ──────────────────────────────────────────────────────


def test_walk_forward_rejects_unsorted_timestamps() -> None:
    ts = [
        datetime(2020, 1, 1),
        datetime(2020, 3, 1),
        datetime(2020, 2, 1),  # out of order
        datetime(2020, 4, 1),
    ]
    with pytest.raises(ValueError, match="sorted"):
        walk_forward_splits(ts, train_months=24, test_months=6, step_months=6)


def test_walk_forward_rejects_duplicate_timestamps() -> None:
    """Strict ascending means equal timestamps are rejected too."""
    ts = [
        datetime(2020, 1, 1),
        datetime(2020, 1, 1),
        datetime(2020, 1, 2),
    ]
    with pytest.raises(ValueError, match="sorted"):
        walk_forward_splits(ts, train_months=24, test_months=6, step_months=6)


def test_walk_forward_rejects_nonpositive_train_months() -> None:
    ts = _six_year_daily()
    with pytest.raises(ValueError):
        walk_forward_splits(ts, train_months=0, test_months=6, step_months=6)
    with pytest.raises(ValueError):
        walk_forward_splits(ts, train_months=-1, test_months=6, step_months=6)


def test_walk_forward_rejects_nonpositive_test_months() -> None:
    ts = _six_year_daily()
    with pytest.raises(ValueError):
        walk_forward_splits(ts, train_months=24, test_months=0, step_months=6)


def test_walk_forward_rejects_nonpositive_step_months() -> None:
    ts = _six_year_daily()
    with pytest.raises(ValueError):
        walk_forward_splits(ts, train_months=24, test_months=6, step_months=0)


# ── dataclass shape ─────────────────────────────────────────────────


def test_walk_forward_split_exposes_all_expected_fields() -> None:
    """Pin the WalkForwardSplit public shape."""
    ts = _six_year_daily()
    out = walk_forward_splits(ts, train_months=24, test_months=6, step_months=6)
    s = out[0]
    assert isinstance(s, WalkForwardSplit)
    # Access each expected attribute.
    assert isinstance(s.index, int)
    assert isinstance(s.train_start, datetime)
    assert isinstance(s.train_end, datetime)
    assert isinstance(s.test_start, datetime)
    assert isinstance(s.test_end, datetime)
    assert isinstance(s.train_lo, int)
    assert isinstance(s.train_hi, int)
    assert isinstance(s.test_lo, int)
    assert isinstance(s.test_hi, int)
