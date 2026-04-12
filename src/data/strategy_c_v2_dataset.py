"""Strategy C v2 dataset container and loader scaffold.

This module holds the public interface for the Strategy C v2 data layer.
Implementation of the loader is **deferred to Phase 2** — this file
intentionally only carries the dataclass definition and a NotImplementedError
stub so downstream code (feature module, walk-forward harness) can import
the contract without blocking on the loader.

Contract:
    - `StrategyCV2Bar` — one 15m bar with OHLCV + aligned Binance perp
      funding metadata (forward-filled between 8h settlements).
    - `load_strategy_c_v2_dataset(klines_csv, funding_csv, *, start=None,
      end=None)` — loads the 6-year 15m Binance OHLCV CSV, joins the 8h
      funding-rate CSV, forward-fills per 15m bar, and computes
      bars_to_next_funding. Returns a chronological list of StrategyCV2Bar.

See `strategy_c_v2_plan.md` section 8 for phase decomposition.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class StrategyCV2Bar:
    """One 15m bar of the Strategy C v2 dataset.

    `funding_rate` is the most-recently-settled Binance USDT-M funding rate,
    forward-filled until the next 8h settlement. `bars_to_next_funding` is
    the number of 15m bars remaining until that next settlement (0..31 at
    15m cadence, since 8h = 32 bars).

    Both funding fields may be None during warmup before the first
    settlement in the dataset window.
    """
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    funding_rate: float | None
    bars_to_next_funding: int | None


def load_strategy_c_v2_dataset(
    klines_csv: str,
    funding_csv: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[StrategyCV2Bar]:
    """Load and align the Strategy C v2 dataset from CSV sources.

    Args:
        klines_csv: Path to the 15m Binance OHLCV CSV
            (e.g. `src/data/btcusdt_15m_6year.csv`).
        funding_csv: Path to the 8h Binance funding-rate CSV
            (e.g. `src/data/btcusdt_funding_5year.csv`).
        start: Optional chronological lower bound (inclusive).
        end: Optional chronological upper bound (exclusive).

    Returns:
        A chronological list of StrategyCV2Bar with funding_rate
        forward-filled and bars_to_next_funding populated.

    Raises:
        NotImplementedError: Phase 1 stub. Implementation lands in Phase 2
            of the Strategy C v2 program. See `strategy_c_v2_plan.md`.
    """
    raise NotImplementedError(
        "load_strategy_c_v2_dataset is a Phase 1 scaffold; the loader "
        "implementation is deferred to Phase 2 of the Strategy C v2 program. "
        "See strategy_c_v2_plan.md section 8."
    )
