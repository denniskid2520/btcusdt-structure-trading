"""Futures derivatives data: OI, long/short ratio, taker buy/sell ratio.

Provides both a live Binance fetcher and a static provider for backtesting.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class FuturesSnapshot:
    """Single point-in-time derivatives data."""

    timestamp: datetime
    open_interest: float
    long_short_ratio: float
    taker_buy_sell_ratio: float

    @property
    def long_pct(self) -> float:
        return self.long_short_ratio / (1 + self.long_short_ratio) * 100

    @property
    def short_pct(self) -> float:
        return 100.0 - self.long_pct


class FuturesDataProvider(ABC):
    @abstractmethod
    def get_snapshot(self, symbol: str, timestamp: datetime) -> FuturesSnapshot | None:
        raise NotImplementedError


class StaticFuturesProvider(FuturesDataProvider):
    """In-memory provider for backtesting — preloaded with historical data."""

    def __init__(self, data: dict[datetime, FuturesSnapshot]) -> None:
        self._data = data
        self._sorted_ts = sorted(data.keys())

    def get_snapshot(self, symbol: str, timestamp: datetime) -> FuturesSnapshot | None:
        if not self._sorted_ts:
            return None
        # Binary search for nearest timestamp within 4h tolerance
        best_ts = min(self._sorted_ts, key=lambda t: abs((t - timestamp).total_seconds()))
        if abs((best_ts - timestamp).total_seconds()) > 4 * 3600:
            return None
        return self._data[best_ts]


class BinanceFuturesProvider(FuturesDataProvider):
    """Live provider that fetches from Binance Futures public API."""

    _BASE = "https://fapi.binance.com"

    def get_snapshot(self, symbol: str, timestamp: datetime) -> FuturesSnapshot | None:
        try:
            oi = self._fetch_oi(symbol)
            ls = self._fetch_long_short(symbol)
            taker = self._fetch_taker(symbol)
            return FuturesSnapshot(
                timestamp=timestamp,
                open_interest=oi,
                long_short_ratio=ls,
                taker_buy_sell_ratio=taker,
            )
        except Exception:
            return None

    def fetch_history(self, symbol: str, period: str = "4h", limit: int = 500) -> list[FuturesSnapshot]:
        """Fetch historical snapshots (up to ~30 days back)."""
        oi_data = self._get_json(f"/futures/data/openInterestHist?symbol={symbol}&period={period}&limit={limit}")
        ls_data = self._get_json(f"/futures/data/topLongShortAccountRatio?symbol={symbol}&period={period}&limit={limit}")
        taker_data = self._get_json(f"/futures/data/takerlongshortRatio?symbol={symbol}&period={period}&limit={limit}")

        # Index by timestamp
        ls_by_ts = {d["timestamp"]: d for d in ls_data}
        taker_by_ts = {d["timestamp"]: d for d in taker_data}

        snapshots: list[FuturesSnapshot] = []
        for row in oi_data:
            ts_ms = row["timestamp"]
            ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).replace(tzinfo=None)
            ls_row = ls_by_ts.get(ts_ms, {})
            taker_row = taker_by_ts.get(ts_ms, {})
            snapshots.append(FuturesSnapshot(
                timestamp=ts,
                open_interest=float(row.get("sumOpenInterest", 0)),
                long_short_ratio=float(ls_row.get("longShortRatio", 0)),
                taker_buy_sell_ratio=float(taker_row.get("buySellRatio", 0)),
            ))
        return snapshots

    def _fetch_oi(self, symbol: str) -> float:
        data = self._get_json(f"/fapi/v1/openInterest?symbol={symbol}")
        return float(data["openInterest"])

    def _fetch_long_short(self, symbol: str) -> float:
        data = self._get_json(f"/futures/data/topLongShortAccountRatio?symbol={symbol}&period=4h&limit=1")
        return float(data[0]["longShortRatio"]) if data else 0.0

    def _fetch_taker(self, symbol: str) -> float:
        data = self._get_json(f"/futures/data/takerlongshortRatio?symbol={symbol}&period=4h&limit=1")
        return float(data[0]["buySellRatio"]) if data else 0.0

    def _get_json(self, path: str) -> dict | list:
        url = f"{self._BASE}{path}"
        req = Request(url, method="GET")
        req.add_header("Accept", "application/json")
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
