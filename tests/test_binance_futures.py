"""Tests for Binance Futures data adapter."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from adapters.binance_futures import BinanceFuturesAdapter, FundingRateRecord


# Sample kline response from Binance fapi
_SAMPLE_KLINE = [
    1609459200000,   # open time (2021-01-01 00:00 UTC)
    "29000.00",      # open
    "29500.00",      # high
    "28800.00",      # low
    "29300.00",      # close
    "12345.678",     # volume
    1609473599999,   # close time
    "361111111.11",  # quote volume
    5000,            # trades
    "6000.123",      # taker buy volume
    "175555555.55",  # taker buy quote volume
    "0",             # ignore
]


def _make_klines(n: int, start_ms: int = 1609459200000, interval_ms: int = 14400000) -> list[list]:
    """Generate n sample klines starting from start_ms."""
    rows = []
    for i in range(n):
        open_ms = start_ms + i * interval_ms
        rows.append([
            open_ms,
            str(29000 + i * 100),
            str(29500 + i * 100),
            str(28800 + i * 100),
            str(29300 + i * 100),
            str(1000 + i),
            open_ms + interval_ms - 1,
            "0", 0, "0", "0", "0",
        ])
    return rows


class TestBinanceFuturesAdapter:

    def test_base_url_is_futures(self):
        adapter = BinanceFuturesAdapter()
        assert "fapi" in adapter.base_url

    def test_parse_kline_to_marketbar(self):
        adapter = BinanceFuturesAdapter()
        bar = adapter._parse(_SAMPLE_KLINE)
        assert bar.open == 29000.0
        assert bar.high == 29500.0
        assert bar.low == 28800.0
        assert bar.close == 29300.0
        assert bar.volume == 12345.678
        assert bar.timestamp == datetime(2021, 1, 1, 0, 0, 0)

    @patch("adapters.binance_futures.urlopen")
    def test_fetch_ohlcv_calls_fapi_endpoint(self, mock_urlopen):
        klines = _make_klines(5)
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(klines).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        adapter = BinanceFuturesAdapter()
        bars = adapter.fetch_ohlcv("BTCUSDT", "4h", 5)

        assert len(bars) == 5
        call_url = mock_urlopen.call_args[0][0].full_url
        assert "/fapi/v1/klines" in call_url
        assert "BTCUSDT" in call_url

    @patch("adapters.binance_futures.urlopen")
    def test_fetch_ohlcv_returns_sorted_bars(self, mock_urlopen):
        klines = _make_klines(3)
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(klines).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        adapter = BinanceFuturesAdapter()
        bars = adapter.fetch_ohlcv("BTCUSDT", "4h", 3)

        assert bars[0].timestamp < bars[1].timestamp < bars[2].timestamp

    @patch("adapters.binance_futures.urlopen")
    def test_fetch_multi_timeframe(self, mock_urlopen):
        """fetch_multi returns dict of timeframe -> bars."""
        klines_4h = _make_klines(10, interval_ms=14400000)
        klines_1d = _make_klines(5, interval_ms=86400000)

        responses = [
            MagicMock(
                read=MagicMock(return_value=json.dumps(klines_4h).encode()),
                __enter__=lambda s: s,
                __exit__=MagicMock(return_value=False),
            ),
            MagicMock(
                read=MagicMock(return_value=json.dumps(klines_1d).encode()),
                __enter__=lambda s: s,
                __exit__=MagicMock(return_value=False),
            ),
        ]
        mock_urlopen.side_effect = responses

        adapter = BinanceFuturesAdapter()
        result = adapter.fetch_multi("BTCUSDT", {"4h": 10, "1d": 5})

        assert "4h" in result
        assert "1d" in result
        assert len(result["4h"]) == 10
        assert len(result["1d"]) == 5

    def test_unsupported_timeframe_raises(self):
        adapter = BinanceFuturesAdapter()
        with pytest.raises(ValueError, match="Unsupported"):
            adapter.fetch_ohlcv("BTCUSDT", "7h", 10)


# ─── fetch_funding_rate_history ──────────────────────────────────────
#
# Binance USDT-M Futures funding rate history endpoint:
#     GET /fapi/v1/fundingRate
#     Params: symbol, startTime (ms), endTime (ms), limit (max 1000)
#     Returns: [{symbol, fundingTime (ms), fundingRate (str), markPrice (str)}, ...]
#
# Funding settles every 8 hours → 3 records/day. 5 years ≈ 5,475 records.
# Max limit per request is 1000, so pagination over 5-year history needs
# ~6 round-trips.


_FUNDING_INTERVAL_MS = 8 * 3_600_000  # 8h


def _make_funding_rows(
    n: int,
    *,
    start_ms: int = 1_609_459_200_000,  # 2021-01-01 00:00 UTC
    rate_start: float = 0.0001,
    rate_step: float = 0.00001,
    mark_start: float = 29_000.0,
    mark_step: float = 50.0,
) -> list[dict]:
    """Build n funding-rate response rows at 8h cadence."""
    rows = []
    for i in range(n):
        rows.append({
            "symbol": "BTCUSDT",
            "fundingTime": start_ms + i * _FUNDING_INTERVAL_MS,
            "fundingRate": f"{rate_start + i * rate_step:.8f}",
            "markPrice": f"{mark_start + i * mark_step:.2f}",
        })
    return rows


def _mock_urlopen_from(payloads: list[list[dict]]) -> MagicMock:
    """Build a side_effect list of context-manager mocks for urlopen."""
    responses = []
    for payload in payloads:
        resp = MagicMock()
        resp.read.return_value = json.dumps(payload).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        responses.append(resp)
    return responses


class TestBinanceFuturesFundingHistory:

    def test_funding_rate_record_dataclass_fields(self):
        """FundingRateRecord exposes timestamp, funding_rate, mark_price."""
        rec = FundingRateRecord(
            timestamp=datetime(2021, 1, 1, 0, 0, 0),
            funding_rate=0.0001,
            mark_price=29_000.0,
        )
        assert rec.timestamp == datetime(2021, 1, 1, 0, 0, 0)
        assert rec.funding_rate == pytest.approx(0.0001)
        assert rec.mark_price == pytest.approx(29_000.0)

    @patch("adapters.binance_futures.urlopen")
    def test_fetch_funding_history_hits_correct_endpoint(self, mock_urlopen):
        rows = _make_funding_rows(3)
        mock_urlopen.side_effect = _mock_urlopen_from([rows])

        adapter = BinanceFuturesAdapter()
        start = datetime(2021, 1, 1)
        end = datetime(2021, 1, 2)
        records = adapter.fetch_funding_rate_history("BTCUSDT", start=start, end=end)

        assert len(records) == 3
        call_url = mock_urlopen.call_args_list[0][0][0].full_url
        assert "/fapi/v1/fundingRate" in call_url
        assert "symbol=BTCUSDT" in call_url

    @patch("adapters.binance_futures.urlopen")
    def test_fetch_funding_history_parses_to_records(self, mock_urlopen):
        rows = _make_funding_rows(1)
        mock_urlopen.side_effect = _mock_urlopen_from([rows])

        adapter = BinanceFuturesAdapter()
        records = adapter.fetch_funding_rate_history(
            "BTCUSDT",
            start=datetime(2021, 1, 1),
            end=datetime(2021, 1, 2),
        )

        assert len(records) == 1
        rec = records[0]
        assert isinstance(rec, FundingRateRecord)
        assert rec.timestamp == datetime(2021, 1, 1, 0, 0, 0)
        assert rec.funding_rate == pytest.approx(0.0001)
        assert rec.mark_price == pytest.approx(29_000.0)

    @patch("adapters.binance_futures.urlopen")
    def test_fetch_funding_history_passes_start_and_end_in_ms(self, mock_urlopen):
        mock_urlopen.side_effect = _mock_urlopen_from([_make_funding_rows(2)])

        adapter = BinanceFuturesAdapter()
        start = datetime(2021, 1, 1)
        end = datetime(2021, 1, 2)
        adapter.fetch_funding_rate_history("BTCUSDT", start=start, end=end)

        call_url = mock_urlopen.call_args_list[0][0][0].full_url
        # Both startTime and endTime should appear as ms epoch values.
        assert "startTime=1609459200000" in call_url
        # end of 2021-01-02 00:00 UTC = 1_609_545_600_000
        assert "endTime=1609545600000" in call_url

    @patch("adapters.binance_futures.urlopen")
    def test_fetch_funding_history_paginates_when_response_full(self, mock_urlopen):
        """If a page returns exactly `limit` rows, fetch the next page.

        Matches the established adapter contract in `fetch_range`:
          - full page (len == limit) → continue
          - partial page (len < limit) → terminate immediately
        So two responses (1000 full, 500 partial) → 2 calls, 1500 records.
        """
        page1 = _make_funding_rows(1000, start_ms=1_609_459_200_000)
        # page2 starts right after page1's last fundingTime
        page2_start = page1[-1]["fundingTime"] + _FUNDING_INTERVAL_MS
        page2 = _make_funding_rows(500, start_ms=page2_start)

        mock_urlopen.side_effect = _mock_urlopen_from([page1, page2])

        adapter = BinanceFuturesAdapter()
        records = adapter.fetch_funding_rate_history(
            "BTCUSDT",
            start=datetime(2021, 1, 1),
            end=datetime(2022, 1, 1),
        )

        assert len(records) == 1500
        # Page1 full → fetch page2. Page2 partial → stop. Two calls.
        assert mock_urlopen.call_count == 2

    @patch("adapters.binance_futures.urlopen")
    def test_fetch_funding_history_paginates_multiple_full_pages(self, mock_urlopen):
        """Two full pages followed by an empty page should trigger 3 calls.

        2000 funding rows * 8h interval ≈ 666 days ≈ 1.8 years. Range must
        span at least that so cursor never runs past end_ms.
        """
        page1 = _make_funding_rows(1000, start_ms=1_609_459_200_000)
        page2_start = page1[-1]["fundingTime"] + _FUNDING_INTERVAL_MS
        page2 = _make_funding_rows(1000, start_ms=page2_start)
        page3: list[dict] = []

        mock_urlopen.side_effect = _mock_urlopen_from([page1, page2, page3])

        adapter = BinanceFuturesAdapter()
        records = adapter.fetch_funding_rate_history(
            "BTCUSDT",
            start=datetime(2021, 1, 1),
            end=datetime(2024, 1, 1),
        )

        assert len(records) == 2000
        assert mock_urlopen.call_count == 3

    @patch("adapters.binance_futures.urlopen")
    def test_fetch_funding_history_returns_ascending(self, mock_urlopen):
        rows = _make_funding_rows(5)
        mock_urlopen.side_effect = _mock_urlopen_from([rows])

        adapter = BinanceFuturesAdapter()
        records = adapter.fetch_funding_rate_history(
            "BTCUSDT",
            start=datetime(2021, 1, 1),
            end=datetime(2021, 1, 2),
        )

        for a, b in zip(records, records[1:]):
            assert a.timestamp < b.timestamp

    @patch("adapters.binance_futures.urlopen")
    def test_fetch_funding_history_empty_response_terminates(self, mock_urlopen):
        """Empty initial page → empty result, not an infinite loop."""
        mock_urlopen.side_effect = _mock_urlopen_from([[]])

        adapter = BinanceFuturesAdapter()
        records = adapter.fetch_funding_rate_history(
            "BTCUSDT",
            start=datetime(2021, 1, 1),
            end=datetime(2021, 1, 2),
        )

        assert records == []
        assert mock_urlopen.call_count == 1

    @patch("adapters.binance_futures.urlopen")
    def test_fetch_funding_history_partial_page_terminates(self, mock_urlopen):
        """A page smaller than limit means we're done — don't call again."""
        rows = _make_funding_rows(50)
        mock_urlopen.side_effect = _mock_urlopen_from([rows])

        adapter = BinanceFuturesAdapter()
        records = adapter.fetch_funding_rate_history(
            "BTCUSDT",
            start=datetime(2021, 1, 1),
            end=datetime(2021, 1, 2),
        )

        assert len(records) == 50
        assert mock_urlopen.call_count == 1

    @patch("adapters.binance_futures.urlopen")
    def test_fetch_funding_history_handles_empty_markprice(self, mock_urlopen):
        """Binance returns markPrice='' for some old records. Parse as None.

        funding_rate is still required and must parse cleanly.
        """
        rows = [
            {
                "symbol": "BTCUSDT",
                "fundingTime": 1_609_459_200_000,
                "fundingRate": "0.0001",
                "markPrice": "",  # empty — real Binance quirk for early records
            },
            {
                "symbol": "BTCUSDT",
                "fundingTime": 1_609_459_200_000 + _FUNDING_INTERVAL_MS,
                "fundingRate": "0.00012",
                "markPrice": "29100.50",
            },
        ]
        mock_urlopen.side_effect = _mock_urlopen_from([rows])

        adapter = BinanceFuturesAdapter()
        records = adapter.fetch_funding_rate_history(
            "BTCUSDT",
            start=datetime(2021, 1, 1),
            end=datetime(2021, 1, 2),
        )

        assert len(records) == 2
        assert records[0].mark_price is None
        assert records[0].funding_rate == pytest.approx(0.0001)
        assert records[1].mark_price == pytest.approx(29100.50)
        assert records[1].funding_rate == pytest.approx(0.00012)

    @patch("adapters.binance_futures.urlopen")
    def test_fetch_funding_history_cursor_advances_past_last_fundingtime(self, mock_urlopen):
        """Second-page startTime must be strictly greater than page1's last fundingTime.

        Prevents duplicate records across pages.
        """
        page1 = _make_funding_rows(1000, start_ms=1_609_459_200_000)
        last_ft = page1[-1]["fundingTime"]
        page2: list[dict] = []
        mock_urlopen.side_effect = _mock_urlopen_from([page1, page2])

        adapter = BinanceFuturesAdapter()
        adapter.fetch_funding_rate_history(
            "BTCUSDT",
            start=datetime(2021, 1, 1),
            end=datetime(2022, 1, 1),
        )

        assert mock_urlopen.call_count == 2
        page2_url = mock_urlopen.call_args_list[1][0][0].full_url
        # The second-page startTime must be > last_ft.
        # We encode it as ms integer in the query string.
        assert f"startTime={last_ft + 1}" in page2_url or f"startTime={last_ft + _FUNDING_INTERVAL_MS}" in page2_url
