"""Tests for Strategy C v2 Phase 3 signal filters.

Phase 3 introduces two post-hoc filters that transform a signal vector
before it reaches the backtester. These filters are strategy-agnostic:
they work on any {-1, 0, +1} signal stream produced by any strategy in
the Phase 2 literature family or later Phase 3 families.

Filters:
    apply_side_filter(signals, side)
        side="long"  → zero out -1 signals
        side="short" → zero out +1 signals
        side="both"  → identity

    apply_funding_filter(signals, features, *, ...)
        Veto long signals when funding is hostile to longs (e.g. rate is
        too positive) and short signals when funding is hostile to shorts
        (e.g. rate is too negative). Reads `funding_rate` by default, or
        `funding_cum_24h` when `use_cum_24h=True`.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from strategies.strategy_c_v2_filters import (
    apply_funding_filter,
    apply_side_filter,
)


# ── helper: minimal feature stub ────────────────────────────────────


class _FeatStub:
    def __init__(
        self,
        *,
        funding_rate: float | None = None,
        funding_cum_24h: float | None = None,
        ts: datetime = datetime(2024, 1, 1),
    ) -> None:
        self.funding_rate = funding_rate
        self.funding_cum_24h = funding_cum_24h
        self.timestamp = ts


# ── apply_side_filter ───────────────────────────────────────────────


def test_side_filter_long_drops_shorts() -> None:
    sigs = [1, -1, 0, 1, -1, 0]
    out = apply_side_filter(sigs, side="long")
    assert out == [1, 0, 0, 1, 0, 0]


def test_side_filter_short_drops_longs() -> None:
    sigs = [1, -1, 0, 1, -1, 0]
    out = apply_side_filter(sigs, side="short")
    assert out == [0, -1, 0, 0, -1, 0]


def test_side_filter_both_is_identity() -> None:
    sigs = [1, -1, 0, 1, -1, 0]
    out = apply_side_filter(sigs, side="both")
    assert out == [1, -1, 0, 1, -1, 0]
    assert out is not sigs  # returns a new list, not mutating


def test_side_filter_empty() -> None:
    assert apply_side_filter([], side="long") == []


def test_side_filter_invalid_side_raises() -> None:
    with pytest.raises(ValueError, match="side"):
        apply_side_filter([1, -1, 0], side="weird")  # type: ignore[arg-type]


def test_side_filter_preserves_length() -> None:
    sigs = [0, 1, -1, 1, -1, 0, 0, 1]
    for side in ("long", "short", "both"):
        assert len(apply_side_filter(sigs, side=side)) == len(sigs)  # type: ignore[arg-type]


# ── apply_funding_filter (rate-based) ───────────────────────────────


def test_funding_filter_blocks_longs_above_max_long_funding() -> None:
    """Long signals are blocked when funding_rate exceeds max_long_funding."""
    signals = [1, 1, 1]
    features = [
        _FeatStub(funding_rate=0.0001),   # below threshold — allow
        _FeatStub(funding_rate=0.0005),   # above 0.0003 — block
        _FeatStub(funding_rate=0.00005),  # below — allow
    ]
    out = apply_funding_filter(signals, features, max_long_funding=0.0003)
    assert out == [1, 0, 1]


def test_funding_filter_blocks_shorts_below_min_short_funding() -> None:
    """Short signals are blocked when funding_rate is below min_short_funding."""
    signals = [-1, -1, -1]
    features = [
        _FeatStub(funding_rate=-0.0001),  # above -0.0003 — allow
        _FeatStub(funding_rate=-0.0005),  # below threshold — block
        _FeatStub(funding_rate=0.0001),   # above — allow
    ]
    out = apply_funding_filter(signals, features, min_short_funding=-0.0003)
    assert out == [-1, 0, -1]


def test_funding_filter_only_affects_matching_side() -> None:
    """max_long_funding should only affect long signals, not shorts."""
    signals = [1, -1]
    features = [
        _FeatStub(funding_rate=0.001),
        _FeatStub(funding_rate=0.001),
    ]
    out = apply_funding_filter(signals, features, max_long_funding=0.0003)
    # Long is blocked, short is unaffected
    assert out == [0, -1]


def test_funding_filter_zero_signal_unchanged() -> None:
    signals = [0, 0, 0]
    features = [_FeatStub(funding_rate=0.01) for _ in range(3)]
    out = apply_funding_filter(signals, features, max_long_funding=0.0001, min_short_funding=-0.0001)
    assert out == [0, 0, 0]


def test_funding_filter_none_rate_is_safe_default() -> None:
    """When funding_rate is None (warmup), the filter should default to
    ALLOWING the trade (we cannot prove hostility, so do not block)."""
    signals = [1, -1]
    features = [_FeatStub(funding_rate=None), _FeatStub(funding_rate=None)]
    out = apply_funding_filter(
        signals, features,
        max_long_funding=0.0001, min_short_funding=-0.0001,
    )
    assert out == [1, -1]


def test_funding_filter_no_thresholds_is_identity() -> None:
    """No thresholds set → no blocking, even with extreme funding."""
    signals = [1, -1, 0]
    features = [_FeatStub(funding_rate=0.1) for _ in range(3)]
    out = apply_funding_filter(signals, features)
    assert out == [1, -1, 0]


# ── apply_funding_filter (cum_24h mode) ─────────────────────────────


def test_funding_filter_use_cum_24h_reads_funding_cum_24h_field() -> None:
    """With use_cum_24h=True, the filter checks funding_cum_24h instead."""
    signals = [1, 1]
    features = [
        _FeatStub(funding_rate=0.0001, funding_cum_24h=0.0001),  # rate OK, cum OK
        _FeatStub(funding_rate=0.0001, funding_cum_24h=0.001),   # rate OK, cum hostile
    ]
    # Threshold on cum_24h
    out = apply_funding_filter(
        signals, features,
        max_long_funding=0.0005,
        use_cum_24h=True,
    )
    # In cum_24h mode, only the 2nd should be blocked (cum > 0.0005)
    assert out == [1, 0]


def test_funding_filter_cum_24h_none_allows() -> None:
    """None cum_24h → allow (safe default, same as rate None)."""
    signals = [1]
    features = [_FeatStub(funding_cum_24h=None)]
    out = apply_funding_filter(
        signals, features,
        max_long_funding=0.0001,
        use_cum_24h=True,
    )
    assert out == [1]


# ── length / validation ─────────────────────────────────────────────


def test_funding_filter_length_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        apply_funding_filter(
            [1, 0, 1],
            [_FeatStub(funding_rate=0.0)],
            max_long_funding=0.0001,
        )


def test_funding_filter_empty_inputs() -> None:
    assert apply_funding_filter([], [], max_long_funding=0.0001) == []
