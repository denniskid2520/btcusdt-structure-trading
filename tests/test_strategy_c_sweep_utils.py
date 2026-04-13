"""Tests for Strategy C sweep utilities used by the Baseline C grid runner.

Three small helpers, all pure functions:

    temporal_split(series, train_frac)
        Chronological split — first `train_frac` fraction goes to train,
        the remainder to holdout. No shuffling. Raises on bad fraction.

    percentile_threshold(values, pct)
        Nth percentile (linear interpolation) of a non-None value stream.
        pct is 0..100. None values are skipped. Raises on empty input.

    passes_min_trades(metrics, min_train, min_holdout)
        Given a row with 'train_num_trades' and 'holdout_num_trades', return
        True iff both counts meet their minimums.
"""
from __future__ import annotations

import pytest

from research.strategy_c_sweep import (
    passes_min_trades,
    percentile_threshold,
    temporal_split,
)


# ── temporal_split ───────────────────────────────────────────────────


def test_temporal_split_70_30_on_10_items() -> None:
    data = list(range(10))
    train, holdout = temporal_split(data, train_frac=0.7)
    assert train == [0, 1, 2, 3, 4, 5, 6]
    assert holdout == [7, 8, 9]


def test_temporal_split_preserves_order() -> None:
    """Non-shuffled: train ends right before holdout begins."""
    data = list(range(100))
    train, holdout = temporal_split(data, train_frac=0.8)
    assert train[-1] == 79
    assert holdout[0] == 80
    assert len(train) + len(holdout) == 100


def test_temporal_split_edge_fractions_valid() -> None:
    data = list(range(5))
    # 0.0 → all holdout
    tr, ho = temporal_split(data, train_frac=0.0)
    assert tr == []
    assert ho == data
    # 1.0 → all train
    tr, ho = temporal_split(data, train_frac=1.0)
    assert tr == data
    assert ho == []


def test_temporal_split_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        temporal_split([1, 2, 3], train_frac=-0.1)
    with pytest.raises(ValueError):
        temporal_split([1, 2, 3], train_frac=1.1)


def test_temporal_split_empty_is_empty() -> None:
    tr, ho = temporal_split([], train_frac=0.7)
    assert tr == []
    assert ho == []


# ── percentile_threshold ─────────────────────────────────────────────


def test_percentile_threshold_50th_is_median() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    # 50th percentile with linear interpolation over index → 3.0
    assert percentile_threshold(values, 50.0) == pytest.approx(3.0)


def test_percentile_threshold_skips_none() -> None:
    values = [None, 1.0, None, 2.0, 3.0, 4.0, 5.0, None]
    # Effective cleaned series is [1,2,3,4,5] → 50th ≈ 3.0
    assert percentile_threshold(values, 50.0) == pytest.approx(3.0)


def test_percentile_threshold_extremes() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    # 0th = min, 100th = max.
    assert percentile_threshold(values, 0.0) == pytest.approx(1.0)
    assert percentile_threshold(values, 100.0) == pytest.approx(5.0)


def test_percentile_threshold_60_80_95_monotone() -> None:
    values = [float(i) for i in range(1, 21)]  # 1..20
    p60 = percentile_threshold(values, 60.0)
    p80 = percentile_threshold(values, 80.0)
    p95 = percentile_threshold(values, 95.0)
    assert p60 < p80 < p95


def test_percentile_threshold_rejects_empty() -> None:
    with pytest.raises(ValueError):
        percentile_threshold([], 50.0)
    with pytest.raises(ValueError):
        percentile_threshold([None, None], 50.0)


def test_percentile_threshold_rejects_out_of_range_pct() -> None:
    with pytest.raises(ValueError):
        percentile_threshold([1.0, 2.0], -1.0)
    with pytest.raises(ValueError):
        percentile_threshold([1.0, 2.0], 100.1)


# ── passes_min_trades ────────────────────────────────────────────────


def test_passes_min_trades_both_meet_minimum() -> None:
    row = {"train_num_trades": 50, "holdout_num_trades": 20}
    assert passes_min_trades(row, min_train=30, min_holdout=10) is True


def test_passes_min_trades_train_below_minimum() -> None:
    row = {"train_num_trades": 20, "holdout_num_trades": 15}
    assert passes_min_trades(row, min_train=30, min_holdout=10) is False


def test_passes_min_trades_holdout_below_minimum() -> None:
    row = {"train_num_trades": 80, "holdout_num_trades": 5}
    assert passes_min_trades(row, min_train=30, min_holdout=10) is False


def test_passes_min_trades_exact_minimum_ok() -> None:
    """>= semantics: meeting the bar exactly counts as passing."""
    row = {"train_num_trades": 30, "holdout_num_trades": 10}
    assert passes_min_trades(row, min_train=30, min_holdout=10) is True
