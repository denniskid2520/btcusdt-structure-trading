"""Tests for futures derivatives data (OI, long/short ratio, taker ratio)."""

from __future__ import annotations

from datetime import datetime

from adapters.futures_data import FuturesSnapshot, FuturesDataProvider, StaticFuturesProvider


def test_futures_snapshot_has_required_fields() -> None:
    snap = FuturesSnapshot(
        timestamp=datetime(2026, 1, 1),
        open_interest=90000.0,
        long_short_ratio=1.85,
        taker_buy_sell_ratio=0.95,
    )
    assert snap.open_interest == 90000.0
    assert snap.long_short_ratio == 1.85
    assert snap.taker_buy_sell_ratio == 0.95


def test_static_provider_returns_snapshot_for_known_timestamp() -> None:
    ts = datetime(2026, 3, 15, 12, 0)
    data = {ts: FuturesSnapshot(timestamp=ts, open_interest=85000.0, long_short_ratio=1.5, taker_buy_sell_ratio=1.1)}
    provider = StaticFuturesProvider(data)
    snap = provider.get_snapshot("BTCUSDT", ts)
    assert snap is not None
    assert snap.open_interest == 85000.0


def test_static_provider_returns_nearest_snapshot() -> None:
    """Should return the closest available snapshot within tolerance."""
    ts1 = datetime(2026, 3, 15, 12, 0)
    ts2 = datetime(2026, 3, 15, 16, 0)
    data = {
        ts1: FuturesSnapshot(timestamp=ts1, open_interest=85000.0, long_short_ratio=1.5, taker_buy_sell_ratio=1.1),
        ts2: FuturesSnapshot(timestamp=ts2, open_interest=86000.0, long_short_ratio=1.6, taker_buy_sell_ratio=0.9),
    }
    provider = StaticFuturesProvider(data)
    # Query between the two — should return nearest
    query = datetime(2026, 3, 15, 15, 0)
    snap = provider.get_snapshot("BTCUSDT", query)
    assert snap is not None
    assert snap.open_interest == 86000.0  # ts2 is closer (1h vs 3h)


def test_static_provider_returns_none_when_empty() -> None:
    provider = StaticFuturesProvider({})
    snap = provider.get_snapshot("BTCUSDT", datetime(2026, 1, 1))
    assert snap is None


def test_futures_snapshot_long_pct() -> None:
    snap = FuturesSnapshot(
        timestamp=datetime(2026, 1, 1),
        open_interest=90000.0,
        long_short_ratio=1.85,
        taker_buy_sell_ratio=1.0,
    )
    # L/S = 1.85 means long_pct = 1.85 / (1 + 1.85) = 64.9%
    assert abs(snap.long_pct - 64.9) < 0.5
    assert abs(snap.short_pct - 35.1) < 0.5
