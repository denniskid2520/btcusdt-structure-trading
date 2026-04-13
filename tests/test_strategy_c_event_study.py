"""Tests for Strategy C event study tools (precision-first research).

The event study:
    1. find_events(feats, side, z_threshold) → list[Event]
       - side=+1  → trigger when long_liq_z32  > threshold (long-liq spike)
       - side=-1  → trigger when short_liq_z32 > threshold (short-liq spike)
    2. measure_forward_returns(feats, events, horizons, fees, slippage)
       - For each event, entry at bar[i+1].open, exit at bar[i+1+h].open
       - Returns EventResult with side-adjusted, cost-adjusted forward returns
    3. bucket_events(results, feats, key_fn)
       - Groups events by a user-provided bucketing function
       - Returns bucket summaries (count, avg, median, win_rate per horizon)
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from data.strategy_c_features import StrategyCFeatureBar
from research.event_study_strategy_c import (
    Event,
    EventResult,
    bucket_events,
    find_events,
    measure_forward_returns,
)


def _feat(
    i: int,
    *,
    open_: float = 100.0,
    close: float = 100.0,
    long_liq_z32: float | None = 0.0,
    short_liq_z32: float | None = 0.0,
    taker_delta_norm: float = 0.0,
    taker_delta_norm_z32: float | None = 0.0,
    cvd_delta: float | None = 0.0,
    cvd_delta_z32: float | None = 0.0,
    basis_change: float | None = 0.0,
    basis_change_z32: float | None = 0.0,
    fr_spread_z96: float | None = 0.0,
) -> StrategyCFeatureBar:
    return StrategyCFeatureBar(
        timestamp=datetime(2026, 2, 16) + timedelta(minutes=15 * i),
        open=open_,
        close=close,
        taker_delta_norm=taker_delta_norm,
        cvd_delta=cvd_delta,
        basis_change=basis_change,
        fr_spread=0.0,
        agg_u_oi_pct=0.0,
        liq_imbalance=0.0,
        taker_delta_norm_z32=taker_delta_norm_z32,
        oi_pct_change_z32=0.0,
        basis_z96=0.0,
        fr_close_z96=0.0,
        cvd_delta_z32=cvd_delta_z32,
        long_liq_z32=long_liq_z32,
        short_liq_z32=short_liq_z32,
        basis_change_z32=basis_change_z32,
        fr_spread_z96=fr_spread_z96,
        agg_u_oi_pct_z32=0.0,
    )


# ── find_events ───────────────────────────────────────────────────────


def test_find_long_events_triggers_on_long_liq_spike() -> None:
    feats = [
        _feat(0, long_liq_z32=0.5),  # below threshold
        _feat(1, long_liq_z32=2.5),  # HIT
        _feat(2, long_liq_z32=1.0),  # below
        _feat(3, long_liq_z32=3.0),  # HIT
    ]
    events = find_events(feats, side=1, z_threshold=2.0)
    assert len(events) == 2
    assert events[0].index == 1
    assert events[0].side == 1
    assert events[0].trigger_z == 2.5
    assert events[1].index == 3
    assert events[1].trigger_z == 3.0


def test_find_short_events_triggers_on_short_liq_spike() -> None:
    feats = [
        _feat(0, short_liq_z32=0.0),
        _feat(1, short_liq_z32=2.2),  # HIT
        _feat(2, short_liq_z32=1.9),
    ]
    events = find_events(feats, side=-1, z_threshold=2.0)
    assert len(events) == 1
    assert events[0].index == 1
    assert events[0].side == -1


def test_find_events_skips_none_z_score() -> None:
    """During warmup z-scores can be None — not a valid trigger."""
    feats = [_feat(0, long_liq_z32=None), _feat(1, long_liq_z32=3.0)]
    events = find_events(feats, side=1, z_threshold=2.0)
    assert len(events) == 1
    assert events[0].index == 1


# ── measure_forward_returns ───────────────────────────────────────────


def test_forward_returns_long_event_profit() -> None:
    """Long event at bar 0 → entry bar 1 open=100, exit bar 5 open=105 (+5%)."""
    feats = [
        _feat(0, long_liq_z32=3.0, open_=99, close=99),
        _feat(1, open_=100, close=100),
        _feat(2, open_=101, close=101),
        _feat(3, open_=102, close=102),
        _feat(4, open_=104, close=104),
        _feat(5, open_=105, close=105),
    ]
    events = [Event(index=0, timestamp=feats[0].timestamp, side=1, trigger_z=3.0)]
    results = measure_forward_returns(
        feats, events, horizons=(1, 2, 4),
        fee_per_side=0.0, slippage_per_side=0.0,
    )
    r = results[0]
    # h=1: entry=100, exit=bar[2].open=101 → +1%
    assert r.fwd_returns[1] == pytest.approx(0.01)
    # h=2: entry=100, exit=bar[3].open=102 → +2%
    assert r.fwd_returns[2] == pytest.approx(0.02)
    # h=4: entry=100, exit=bar[5].open=105 → +5%
    assert r.fwd_returns[4] == pytest.approx(0.05)


def test_forward_returns_short_event_sign_flip() -> None:
    """Short event: falling price is a profit."""
    feats = [
        _feat(0, short_liq_z32=3.0, open_=100, close=100),
        _feat(1, open_=100, close=100),
        _feat(2, open_=98, close=98),
    ]
    events = [Event(index=0, timestamp=feats[0].timestamp, side=-1, trigger_z=3.0)]
    results = measure_forward_returns(
        feats, events, horizons=(1,),
        fee_per_side=0.0, slippage_per_side=0.0,
    )
    # Short pays +2% when price drops from 100 to 98.
    assert results[0].fwd_returns[1] == pytest.approx(0.02)


def test_forward_returns_subtracts_round_trip_cost() -> None:
    """A flat-price event has zero raw return → net = −2 × (fee + slip)."""
    feats = [_feat(i, long_liq_z32=3.0 if i == 0 else 0.0, open_=100, close=100) for i in range(5)]
    events = [Event(index=0, timestamp=feats[0].timestamp, side=1, trigger_z=3.0)]
    results = measure_forward_returns(
        feats, events, horizons=(1,),
        fee_per_side=0.0005, slippage_per_side=0.0001,
    )
    assert results[0].fwd_returns[1] == pytest.approx(-0.0012)


def test_forward_returns_drops_event_too_close_to_end() -> None:
    """If horizon runs past the last bar, the event is dropped."""
    feats = [_feat(i, long_liq_z32=3.0 if i == 0 else 0.0, open_=100, close=100) for i in range(3)]
    events = [Event(index=0, timestamp=feats[0].timestamp, side=1, trigger_z=3.0)]
    # horizon=4 → needs bar[5], but we only have 3 bars → dropped.
    results = measure_forward_returns(
        feats, events, horizons=(4,),
        fee_per_side=0.0, slippage_per_side=0.0,
    )
    assert results == []


# ── bucket_events ─────────────────────────────────────────────────────


def test_bucket_events_by_flow_sign() -> None:
    """Group events into flow-confirms vs flow-contradicts buckets."""
    # 4 long events with varying taker_delta_norm signs.
    feats_by_idx = {
        0: _feat(0, long_liq_z32=3.0, taker_delta_norm=+0.5),
        10: _feat(10, long_liq_z32=3.0, taker_delta_norm=-0.5),
        20: _feat(20, long_liq_z32=3.0, taker_delta_norm=+0.1),
        30: _feat(30, long_liq_z32=3.0, taker_delta_norm=-0.1),
    }
    # Fake results with matching events
    results = [
        EventResult(
            event=Event(index=i, timestamp=feats_by_idx[i].timestamp, side=1, trigger_z=3.0),
            entry_px=100.0,
            fwd_returns={1: r},
        )
        for i, r in [(0, 0.02), (10, -0.01), (20, 0.005), (30, -0.003)]
    ]

    def key_fn(feat: StrategyCFeatureBar) -> str:
        return "pos" if feat.taker_delta_norm >= 0 else "neg"

    buckets = bucket_events(results, feats_by_idx, key_fn=key_fn, horizon=1, cost=0.0)
    # "pos": [0.02, 0.005] → count=2, avg=0.0125, wins=2 → win_rate=1.0
    assert buckets["pos"]["count"] == 2
    assert buckets["pos"]["avg"] == pytest.approx(0.0125)
    assert buckets["pos"]["win_rate"] == 1.0
    # "neg": [-0.01, -0.003] → count=2, wins=0 → win_rate=0.0
    assert buckets["neg"]["count"] == 2
    assert buckets["neg"]["win_rate"] == 0.0
