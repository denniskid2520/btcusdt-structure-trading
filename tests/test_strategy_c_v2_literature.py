"""Tests for Strategy C v2 literature benchmark family (F1).

Five rule-based strategies:
    rsi_only_signals     — trend-following RSI (> upper long, < lower short)
    macd_only_signals    — sign of MACD histogram
    rsi_and_macd_signals — AND gate of the two
    buy_and_hold_signals — single long at bar 0
    flat_signals         — never trade

Each function maps a feature stream to a signal stream (same length, +1/0/-1).
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest

from strategies.strategy_c_v2_literature import (
    buy_and_hold_signals,
    flat_signals,
    macd_only_signals,
    rsi_and_macd_signals,
    rsi_only_signals,
)


# ── helper: build a stub feature row with only the fields under test ────


def _feat(
    *,
    rsi_14: float | None = None,
    rsi_30: float | None = None,
    macd_hist: float | None = None,
    ts: datetime = datetime(2024, 1, 1),
) -> "_FeatStub":
    return _FeatStub(
        rsi_14=rsi_14,
        rsi_30=rsi_30,
        macd_hist=macd_hist,
        timestamp=ts,
    )


class _FeatStub:
    """Duck-typed feature row carrying only the fields the literature
    strategies actually read. Using the real dataclass would force us
    to supply ~39 fields per row, which clutters the test."""

    def __init__(
        self,
        *,
        rsi_14: float | None,
        rsi_30: float | None,
        macd_hist: float | None,
        timestamp: datetime,
    ) -> None:
        self.rsi_14 = rsi_14
        self.rsi_30 = rsi_30
        self.macd_hist = macd_hist
        self.timestamp = timestamp


# ── rsi_only_signals ────────────────────────────────────────────────


def test_rsi_only_high_rsi_emits_long() -> None:
    feats = [_feat(rsi_14=75.0)]
    assert rsi_only_signals(feats) == [1]


def test_rsi_only_low_rsi_emits_short() -> None:
    feats = [_feat(rsi_14=25.0)]
    assert rsi_only_signals(feats) == [-1]


def test_rsi_only_mid_rsi_emits_flat() -> None:
    feats = [_feat(rsi_14=50.0)]
    assert rsi_only_signals(feats) == [0]


def test_rsi_only_warmup_none_is_flat() -> None:
    feats = [_feat(rsi_14=None)]
    assert rsi_only_signals(feats) == [0]


def test_rsi_only_uses_period_30_when_asked() -> None:
    feats = [_feat(rsi_14=80.0, rsi_30=50.0)]  # rsi_14 would be long, rsi_30 is flat
    assert rsi_only_signals(feats, rsi_period=30) == [0]


def test_rsi_only_accepts_rsi_override_for_arbitrary_periods() -> None:
    """When rsi_override is provided, it is read instead of rsi_14 / rsi_30.
    This lets the runner sweep RSI periods other than 14 and 30 without
    extending the feature dataclass."""
    feats = [_feat(rsi_14=80.0, rsi_30=50.0), _feat(rsi_14=10.0, rsi_30=50.0)]
    # Override says bar 0 is 50 (flat), bar 1 is 25 (short) — should override f.rsi_14
    override = [50.0, 25.0]
    assert rsi_only_signals(feats, rsi_override=override) == [0, -1]


def test_rsi_only_override_length_mismatch_raises() -> None:
    feats = [_feat(rsi_14=50.0)]
    with pytest.raises(ValueError, match="rsi_override"):
        rsi_only_signals(feats, rsi_override=[50.0, 60.0])


def test_rsi_only_override_handles_none() -> None:
    feats = [_feat(rsi_14=80.0)]
    assert rsi_only_signals(feats, rsi_override=[None]) == [0]


def test_rsi_only_custom_thresholds() -> None:
    feats = [_feat(rsi_14=62.0)]
    # Default 70/30 → flat. Custom 60/40 → long.
    assert rsi_only_signals(feats) == [0]
    assert rsi_only_signals(feats, upper=60.0, lower=40.0) == [1]


def test_rsi_only_full_sequence() -> None:
    feats = [
        _feat(rsi_14=None),  # warmup
        _feat(rsi_14=50.0),
        _feat(rsi_14=75.0),  # long
        _feat(rsi_14=80.0),  # long
        _feat(rsi_14=65.0),  # flat
        _feat(rsi_14=25.0),  # short
        _feat(rsi_14=29.0),  # short
        _feat(rsi_14=50.0),  # flat
    ]
    assert rsi_only_signals(feats) == [0, 0, 1, 1, 0, -1, -1, 0]


# ── macd_only_signals ───────────────────────────────────────────────


def test_macd_only_positive_hist_emits_long() -> None:
    feats = [_feat(macd_hist=0.5)]
    assert macd_only_signals(feats) == [1]


def test_macd_only_negative_hist_emits_short() -> None:
    feats = [_feat(macd_hist=-0.5)]
    assert macd_only_signals(feats) == [-1]


def test_macd_only_zero_hist_is_flat() -> None:
    feats = [_feat(macd_hist=0.0)]
    assert macd_only_signals(feats) == [0]


def test_macd_only_none_is_flat() -> None:
    feats = [_feat(macd_hist=None)]
    assert macd_only_signals(feats) == [0]


# ── rsi_and_macd_signals ────────────────────────────────────────────


def test_rsi_and_macd_both_must_agree_long() -> None:
    # Only long when both RSI says long AND MACD hist is positive.
    feats = [_feat(rsi_14=75.0, macd_hist=0.3)]
    assert rsi_and_macd_signals(feats) == [1]


def test_rsi_and_macd_rsi_long_macd_negative_blocks_long() -> None:
    feats = [_feat(rsi_14=75.0, macd_hist=-0.2)]
    assert rsi_and_macd_signals(feats) == [0]


def test_rsi_and_macd_both_short_emits_short() -> None:
    feats = [_feat(rsi_14=20.0, macd_hist=-0.5)]
    assert rsi_and_macd_signals(feats) == [-1]


def test_rsi_and_macd_rsi_short_macd_positive_blocks_short() -> None:
    feats = [_feat(rsi_14=20.0, macd_hist=0.3)]
    assert rsi_and_macd_signals(feats) == [0]


def test_rsi_and_macd_mid_rsi_is_flat() -> None:
    feats = [_feat(rsi_14=50.0, macd_hist=0.9)]
    assert rsi_and_macd_signals(feats) == [0]


def test_rsi_and_macd_none_is_flat() -> None:
    feats = [_feat(rsi_14=None, macd_hist=0.5)]
    assert rsi_and_macd_signals(feats) == [0]
    feats2 = [_feat(rsi_14=75.0, macd_hist=None)]
    assert rsi_and_macd_signals(feats2) == [0]


# ── buy_and_hold_signals ────────────────────────────────────────────


def test_buy_and_hold_single_long_at_bar_0() -> None:
    feats = [_feat(rsi_14=None)] * 10
    sigs = buy_and_hold_signals(feats)
    assert sigs[0] == 1
    assert sigs[1:] == [0] * 9


def test_buy_and_hold_empty_is_empty() -> None:
    assert buy_and_hold_signals([]) == []


def test_buy_and_hold_length_matches_features() -> None:
    feats = [_feat()] * 42
    assert len(buy_and_hold_signals(feats)) == 42


# ── flat_signals ────────────────────────────────────────────────────


def test_flat_signals_all_zeros() -> None:
    feats = [_feat(rsi_14=75.0, macd_hist=1.0)] * 5
    assert flat_signals(feats) == [0, 0, 0, 0, 0]


def test_flat_signals_empty_is_empty() -> None:
    assert flat_signals([]) == []


# ── signature length invariants ─────────────────────────────────────


def test_all_strategies_return_same_length_as_input() -> None:
    feats = [_feat(rsi_14=50.0, macd_hist=0.0)] * 13
    assert len(rsi_only_signals(feats)) == 13
    assert len(macd_only_signals(feats)) == 13
    assert len(rsi_and_macd_signals(feats)) == 13
    assert len(buy_and_hold_signals(feats)) == 13
    assert len(flat_signals(feats)) == 13
