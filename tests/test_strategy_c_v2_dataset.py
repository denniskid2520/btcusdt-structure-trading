"""Tests for Strategy C v2 dataset scaffold.

This is a skeleton sanity check — the full loader implementation is deferred
to Phase 2. These tests pin the public interface so Phase 2 work can start
from a known contract without redesigning.

Public surface:
    - `StrategyCV2Bar` dataclass with 15m OHLCV + funding_rate + bars_to_next_funding
    - `load_strategy_c_v2_dataset(klines_csv, funding_csv, *, start, end)` function
      that currently raises NotImplementedError with a Phase-2 marker
"""
from __future__ import annotations

from dataclasses import fields
from datetime import datetime

import pytest

from data.strategy_c_v2_dataset import (
    StrategyCV2Bar,
    load_strategy_c_v2_dataset,
)


# ── dataclass surface ────────────────────────────────────────────────


def test_strategy_c_v2_bar_required_fields() -> None:
    names = {f.name for f in fields(StrategyCV2Bar)}
    expected = {
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "funding_rate",
        "bars_to_next_funding",
    }
    assert expected.issubset(names), f"missing fields: {expected - names}"


def test_strategy_c_v2_bar_constructs_from_all_fields() -> None:
    bar = StrategyCV2Bar(
        timestamp=datetime(2021, 1, 1, 0, 0, 0),
        open=29_000.0,
        high=29_500.0,
        low=28_800.0,
        close=29_300.0,
        volume=100.0,
        funding_rate=0.0001,
        bars_to_next_funding=31,
    )
    assert bar.timestamp == datetime(2021, 1, 1, 0, 0, 0)
    assert bar.close == pytest.approx(29_300.0)
    assert bar.funding_rate == pytest.approx(0.0001)
    assert bar.bars_to_next_funding == 31


def test_strategy_c_v2_bar_allows_optional_funding_fields() -> None:
    """Funding fields may be None during warmup before first funding settle."""
    bar = StrategyCV2Bar(
        timestamp=datetime(2020, 4, 1),
        open=6_800.0,
        high=6_850.0,
        low=6_780.0,
        close=6_820.0,
        volume=500.0,
        funding_rate=None,
        bars_to_next_funding=None,
    )
    assert bar.funding_rate is None
    assert bar.bars_to_next_funding is None


# ── loader contract (Phase 2 stub) ───────────────────────────────────


def test_load_strategy_c_v2_dataset_is_callable() -> None:
    # Phase 1 raises NotImplementedError with a Phase-2 marker so anyone
    # who tries to run it in Phase 1 gets a clear signal.
    with pytest.raises(NotImplementedError, match="Phase 2"):
        load_strategy_c_v2_dataset(
            klines_csv="src/data/btcusdt_15m_6year.csv",
            funding_csv="src/data/btcusdt_funding_5year.csv",
        )


def test_load_strategy_c_v2_dataset_accepts_optional_window() -> None:
    with pytest.raises(NotImplementedError, match="Phase 2"):
        load_strategy_c_v2_dataset(
            klines_csv="src/data/btcusdt_15m_6year.csv",
            funding_csv="src/data/btcusdt_funding_5year.csv",
            start=datetime(2021, 1, 1),
            end=datetime(2022, 1, 1),
        )
