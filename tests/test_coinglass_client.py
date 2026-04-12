"""Tests for Coinglass client — new data types: Top L/S Ratio, CVD, Basis.

TDD: write failing tests first, then implement.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from unittest.mock import patch

import pytest

from adapters.coinglass_client import (
    CoinglassClient,
    TopLSRatioBar,
    CVDBar,
    BasisBar,
    OIBar,
    FundingRateBar,
    LiquidationBar,
    TakerVolumeBar,
)


# ── Data classes ──────────────────────────────────────────────────────


def test_top_ls_ratio_bar_fields() -> None:
    bar = TopLSRatioBar(
        timestamp=datetime(2026, 1, 1),
        long_percent=60.5,
        short_percent=39.5,
        ratio=1.53,
    )
    assert bar.long_percent == 60.5
    assert bar.short_percent == 39.5
    assert bar.ratio == 1.53


def test_cvd_bar_fields() -> None:
    bar = CVDBar(
        timestamp=datetime(2026, 1, 1),
        buy_vol=280_504_501.21,
        sell_vol=247_025_969.49,
        cvd=10_975_605.00,
    )
    assert bar.buy_vol == 280_504_501.21
    assert bar.sell_vol == 247_025_969.49
    assert bar.cvd == 10_975_605.00


def test_basis_bar_fields() -> None:
    bar = BasisBar(
        timestamp=datetime(2026, 1, 1),
        open_basis=0.0512,
        close_basis=0.0495,
    )
    assert bar.open_basis == 0.0512
    assert bar.close_basis == 0.0495


# ── Mock HTTP server for client tests ────────────────────────────────

MOCK_TOP_LS_RESPONSE = {
    "code": "0",
    "data": [
        {
            "time": 1704067200000,  # 2024-01-01 00:00 UTC
            "top_position_long_percent": 55.2,
            "top_position_short_percent": 44.8,
            "top_position_long_short_ratio": 1.232,
        },
        {
            "time": 1704081600000,  # 2024-01-01 04:00 UTC
            "top_position_long_percent": 58.1,
            "top_position_short_percent": 41.9,
            "top_position_long_short_ratio": 1.387,
        },
    ],
}

MOCK_CVD_RESPONSE = {
    "code": "0",
    "data": [
        {
            "time": 1704067200000,
            "agg_taker_buy_vol": 280504501.21,
            "agg_taker_sell_vol": 247025969.49,
            "cum_vol_delta": 10975605.00,
        },
        {
            "time": 1704081600000,
            "agg_taker_buy_vol": 300000000.00,
            "agg_taker_sell_vol": 310000000.00,
            "cum_vol_delta": -5000000.00,
        },
    ],
}

MOCK_BASIS_RESPONSE = {
    "code": "0",
    "data": [
        {
            "time": 1704067200000,
            "open_basis": 0.0512,
            "close_basis": 0.0495,
            "open_change": 34.29,
            "close_change": 33.08,
        },
        {
            "time": 1704081600000,
            "open_basis": 0.0495,
            "close_basis": 0.0530,
            "open_change": 33.08,
            "close_change": 35.42,
        },
    ],
}

# Pair-level (BTCUSDT) endpoints — Strategy C main series
# All response fields verified against live Coinglass API on 2026-04-10

MOCK_PAIR_OI_RESPONSE = {
    "code": "0",
    "data": [
        {
            "time": 1704067200000,
            "open": "6720131978",
            "high": "6724383023",
            "low": "6713865410",
            "close": "6724383023",
        },
        {
            "time": 1704068100000,  # +15m
            "open": "6724383023",
            "high": "6724612216",
            "low": 6722441838.35,
            "close": 6722441838.35,
        },
    ],
}

MOCK_PAIR_FUNDING_RESPONSE = {
    "code": "0",
    "data": [
        {
            "time": 1704067200000,
            "open": "0.003327",
            "high": "0.003327",
            "low": "0.002933",
            "close": "0.002933",
        },
        {
            "time": 1704068100000,
            "open": "0.002933",
            "high": "0.003100",
            "low": "0.002850",
            "close": "0.003050",
        },
    ],
}

MOCK_PAIR_LIQUIDATION_RESPONSE = {
    "code": "0",
    "data": [
        {
            "time": 1704067200000,
            "long_liquidation_usd": "12345.67",
            "short_liquidation_usd": "98765.43",
        },
        {
            "time": 1704068100000,
            "long_liquidation_usd": "0",
            "short_liquidation_usd": "5000.00",
        },
    ],
}

MOCK_PAIR_TAKER_VOLUME_RESPONSE = {
    "code": "0",
    "data": [
        {
            "time": 1704067200000,
            "taker_buy_volume_usd": "27145645.4238",
            "taker_sell_volume_usd": "29482062.3163",
        },
        {
            "time": 1704068100000,
            "taker_buy_volume_usd": "30000000.00",
            "taker_sell_volume_usd": "25000000.00",
        },
    ],
}

MOCK_PAIR_CVD_RESPONSE = {
    "code": "0",
    "data": [
        {
            "time": 1704067200000,
            "taker_buy_vol": 27145645.4238,
            "taker_sell_vol": 29482062.3163,
            "cum_vol_delta": -2336416.8925,
        },
        {
            "time": 1704068100000,
            "taker_buy_vol": 30000000.00,
            "taker_sell_vol": 25000000.00,
            "cum_vol_delta": 2663583.1075,
        },
    ],
}

MOCK_STABLECOIN_OI_RESPONSE = {
    "code": "0",
    "data": [
        {
            "time": 1704067200000,
            "open": 193833.93,
            "high": 193894.47,
            "low": 193775.12,
            "close": 193876.37,
        },
        {
            "time": 1704068100000,
            "open": 193876.37,
            "high": 194000.00,
            "low": 193800.00,
            "close": 193950.00,
        },
    ],
}


class _MockHandler(BaseHTTPRequestHandler):
    """Serve mock Coinglass API responses."""

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        responses = {
            "/api/futures/top-long-short-position-ratio/history": MOCK_TOP_LS_RESPONSE,
            "/api/futures/aggregated-cvd/history": MOCK_CVD_RESPONSE,
            "/api/futures/basis/history": MOCK_BASIS_RESPONSE,
            "/api/futures/open-interest/history": MOCK_PAIR_OI_RESPONSE,
            "/api/futures/funding-rate/history": MOCK_PAIR_FUNDING_RESPONSE,
            "/api/futures/liquidation/history": MOCK_PAIR_LIQUIDATION_RESPONSE,
            "/api/futures/v2/taker-buy-sell-volume/history": MOCK_PAIR_TAKER_VOLUME_RESPONSE,
            "/api/futures/cvd/history": MOCK_PAIR_CVD_RESPONSE,
            "/api/futures/open-interest/aggregated-stablecoin-history": MOCK_STABLECOIN_OI_RESPONSE,
        }
        body = responses.get(path, {"code": "0", "data": []})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, *args):
        pass  # suppress log output


@pytest.fixture(scope="module")
def mock_server():
    server = HTTPServer(("127.0.0.1", 0), _MockHandler)
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture
def client(mock_server):
    c = CoinglassClient(api_key="test-key", base_url=mock_server)
    c.MIN_REQUEST_INTERVAL = 0  # no rate limiting in tests
    return c


# ── Client fetch tests ───────────────────────────────────────────────


def test_fetch_top_ls_ratio_history(client: CoinglassClient) -> None:
    bars = client.fetch_top_ls_ratio_history(
        exchange="Binance", symbol="BTCUSDT", interval="4h",
    )
    assert len(bars) == 2
    assert bars[0].long_percent == 55.2
    assert bars[0].short_percent == 44.8
    assert bars[0].ratio == 1.232
    assert bars[0].timestamp == datetime(2024, 1, 1, 0, 0)
    assert bars[1].timestamp == datetime(2024, 1, 1, 4, 0)


def test_fetch_cvd_history(client: CoinglassClient) -> None:
    bars = client.fetch_cvd_history(
        symbol="BTC", exchange_list="Binance", interval="4h",
    )
    assert len(bars) == 2
    assert bars[0].buy_vol == 280504501.21
    assert bars[0].sell_vol == 247025969.49
    assert bars[0].cvd == 10975605.00
    assert bars[1].cvd == -5000000.00


def test_fetch_basis_history(client: CoinglassClient) -> None:
    bars = client.fetch_basis_history(
        exchange="Binance", symbol="BTCUSDT", interval="4h",
    )
    assert len(bars) == 2
    assert bars[0].open_basis == 0.0512
    assert bars[0].close_basis == 0.0495
    assert bars[1].close_basis == 0.0530


# ── Strategy C: pair-level (BTCUSDT) endpoints ────────────────────────


def test_fetch_pair_oi_history(client: CoinglassClient) -> None:
    """Pair-level OI history (per-exchange BTCUSDT)."""
    bars = client.fetch_pair_oi_history(
        exchange="Binance", symbol="BTCUSDT", interval="15m",
    )
    assert len(bars) == 2
    assert isinstance(bars[0], OIBar)
    assert bars[0].close == 6724383023.0
    assert bars[1].close == 6722441838.35
    assert bars[0].timestamp == datetime(2024, 1, 1, 0, 0)


def test_fetch_pair_funding_rate_history(client: CoinglassClient) -> None:
    """Pair-level funding rate history (per-exchange BTCUSDT)."""
    bars = client.fetch_pair_funding_rate_history(
        exchange="Binance", symbol="BTCUSDT", interval="15m",
    )
    assert len(bars) == 2
    assert isinstance(bars[0], FundingRateBar)
    assert bars[0].close == 0.002933
    assert bars[1].close == 0.003050


def test_fetch_pair_liquidation_history(client: CoinglassClient) -> None:
    """Pair-level liquidation history (per-exchange BTCUSDT)."""
    bars = client.fetch_pair_liquidation_history(
        exchange="Binance", symbol="BTCUSDT", interval="15m",
    )
    assert len(bars) == 2
    assert isinstance(bars[0], LiquidationBar)
    assert bars[0].long_usd == 12345.67
    assert bars[0].short_usd == 98765.43
    assert bars[1].long_usd == 0.0
    assert bars[1].short_usd == 5000.00


def test_fetch_pair_taker_volume_history(client: CoinglassClient) -> None:
    """Pair-level taker buy/sell volume history (v2 endpoint, per-exchange BTCUSDT)."""
    bars = client.fetch_pair_taker_volume_history(
        exchange="Binance", symbol="BTCUSDT", interval="15m",
    )
    assert len(bars) == 2
    assert isinstance(bars[0], TakerVolumeBar)
    assert bars[0].buy_usd == 27145645.4238
    assert bars[0].sell_usd == 29482062.3163
    assert bars[1].buy_usd == 30000000.00


def test_fetch_pair_cvd_history(client: CoinglassClient) -> None:
    """Pair-level CVD history (per-exchange BTCUSDT, no 'agg_' prefix)."""
    bars = client.fetch_pair_cvd_history(
        exchange="Binance", symbol="BTCUSDT", interval="15m",
    )
    assert len(bars) == 2
    assert isinstance(bars[0], CVDBar)
    assert bars[0].buy_vol == 27145645.4238
    assert bars[0].sell_vol == 29482062.3163
    assert bars[0].cvd == -2336416.8925
    assert bars[1].cvd == 2663583.1075


def test_fetch_stablecoin_oi_history(client: CoinglassClient) -> None:
    """Aggregated stablecoin-margined OI history (cross-exchange, BTC coin-level)."""
    bars = client.fetch_stablecoin_oi_history(
        symbol="BTC",
        exchange_list="Binance,OKX,Bybit",
        interval="15m",
    )
    assert len(bars) == 2
    assert isinstance(bars[0], OIBar)
    assert bars[0].close == 193876.37
    assert bars[1].close == 193950.00
    assert bars[0].timestamp == datetime(2024, 1, 1, 0, 0)
