"""Tests for Strategy C 15m dataset — schema and aligner.

TDD: write failing tests first, then implement.

The dataset unifies BTCUSDT 15m price + Coinglass derivatives data into one
row per timestamp, ready for feature engineering and backtesting.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from adapters.coinglass_client import (
    BasisBar,
    CVDBar,
    FundingRateBar,
    LiquidationBar,
    OIBar,
    TakerVolumeBar,
)
from data.strategy_c_dataset import (
    StrategyCBar,
    align_strategy_c_bars,
)


# ── Schema ────────────────────────────────────────────────────────────


def test_strategy_c_bar_fields() -> None:
    """The unified bar carries price + all 6 Coinglass channels + 2 background factors."""
    bar = StrategyCBar(
        timestamp=datetime(2026, 1, 1, 0, 0),
        open=69995.0,
        close=70000.0,
        volume=123.45,
        oi_close=6_700_000_000.0,
        oi_pct_change=0.005,
        funding=0.003,
        long_liq_usd=12345.0,
        short_liq_usd=98765.0,
        liq_imbalance=0.78,  # more shorts liquidated (bullish)
        taker_buy_usd=27_000_000.0,
        taker_sell_usd=29_000_000.0,
        taker_delta_usd=-2_000_000.0,
        cvd=-2_336_000.0,
        basis=0.0495,
        funding_oi_weighted=0.0035,
        stablecoin_oi=193_876.0,
    )
    assert bar.close == 70000.0
    assert bar.oi_pct_change == 0.005
    assert bar.taker_delta_usd == -2_000_000.0
    assert bar.liq_imbalance == 0.78


# ── Aligner ──────────────────────────────────────────────────────────


def _ts(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 1, 1, hour, minute)


def _make_oi(ts_list: list[datetime], closes: list[float]) -> list[OIBar]:
    return [
        OIBar(timestamp=t, open=c, high=c, low=c, close=c)
        for t, c in zip(ts_list, closes)
    ]


def _make_funding(ts_list: list[datetime], closes: list[float]) -> list[FundingRateBar]:
    return [
        FundingRateBar(timestamp=t, open=c, high=c, low=c, close=c)
        for t, c in zip(ts_list, closes)
    ]


def _make_liq(ts_list: list[datetime], longs: list[float], shorts: list[float]) -> list[LiquidationBar]:
    return [
        LiquidationBar(timestamp=t, long_usd=l, short_usd=s)
        for t, l, s in zip(ts_list, longs, shorts)
    ]


def _make_taker(ts_list: list[datetime], buys: list[float], sells: list[float]) -> list[TakerVolumeBar]:
    return [
        TakerVolumeBar(timestamp=t, buy_usd=b, sell_usd=s)
        for t, b, s in zip(ts_list, buys, sells)
    ]


def _make_cvd(ts_list: list[datetime], cvds: list[float]) -> list[CVDBar]:
    return [
        CVDBar(timestamp=t, buy_vol=0, sell_vol=0, cvd=c)
        for t, c in zip(ts_list, cvds)
    ]


def _make_basis(ts_list: list[datetime], closes: list[float]) -> list[BasisBar]:
    return [
        BasisBar(timestamp=t, open_basis=c, close_basis=c)
        for t, c in zip(ts_list, closes)
    ]


def test_align_basic_three_bars_all_present() -> None:
    """When all sources have the same 3 timestamps, output has 3 rows with everything filled."""
    ts = [_ts(0, 0), _ts(0, 15), _ts(0, 30)]
    price = [(t, 70000.0 + i - 5, 70000.0 + i, 100.0 + i) for i, t in enumerate(ts)]  # (ts, open, close, vol)

    bars = align_strategy_c_bars(
        price_bars=price,
        oi_bars=_make_oi(ts, [6.0e9, 6.01e9, 6.05e9]),
        funding_bars=_make_funding(ts, [0.001, 0.0012, 0.0015]),
        liquidation_bars=_make_liq(ts, [100.0, 200.0, 0.0], [50.0, 0.0, 1000.0]),
        taker_bars=_make_taker(ts, [1e6, 2e6, 3e6], [1.5e6, 1e6, 2e6]),
        cvd_bars=_make_cvd(ts, [10.0, 20.0, 30.0]),
        basis_bars=_make_basis(ts, [0.05, 0.051, 0.052]),
        funding_oi_weighted_bars=_make_funding(ts, [0.0011, 0.0013, 0.0016]),
        stablecoin_oi_bars=_make_oi(ts, [193800.0, 193850.0, 193900.0]),
    )

    assert len(bars) == 3
    assert bars[0].timestamp == ts[0]
    assert bars[0].open == 69995.0
    assert bars[0].close == 70000.0
    assert bars[0].volume == 100.0
    assert bars[0].oi_close == 6.0e9
    assert bars[0].funding == 0.001


def test_align_oi_pct_change_first_bar_zero() -> None:
    """oi_pct_change on the first bar is 0 (no previous bar)."""
    ts = [_ts(0, 0), _ts(0, 15)]
    price = [(t, 70000.0, 70000.0, 100.0) for t in ts]

    bars = align_strategy_c_bars(
        price_bars=price,
        oi_bars=_make_oi(ts, [1000.0, 1010.0]),
        funding_bars=_make_funding(ts, [0.0, 0.0]),
        liquidation_bars=_make_liq(ts, [0, 0], [0, 0]),
        taker_bars=_make_taker(ts, [0, 0], [0, 0]),
        cvd_bars=_make_cvd(ts, [0, 0]),
        basis_bars=_make_basis(ts, [0.0, 0.0]),
        funding_oi_weighted_bars=_make_funding(ts, [0.0, 0.0]),
        stablecoin_oi_bars=_make_oi(ts, [1.0, 1.0]),
    )
    assert bars[0].oi_pct_change == 0.0
    assert bars[1].oi_pct_change == pytest.approx(0.01)  # (1010 - 1000) / 1000


def test_align_taker_delta_and_liq_imbalance() -> None:
    """Derived fields: taker_delta_usd = buy - sell, liq_imbalance = (short - long) / (long + short)."""
    ts = [_ts(0, 0)]
    price = [(ts[0], 70000.0, 70000.0, 100.0)]

    bars = align_strategy_c_bars(
        price_bars=price,
        oi_bars=_make_oi(ts, [1.0]),
        funding_bars=_make_funding(ts, [0.0]),
        liquidation_bars=_make_liq(ts, [300.0], [100.0]),
        taker_bars=_make_taker(ts, [5e6], [3e6]),
        cvd_bars=_make_cvd(ts, [0]),
        basis_bars=_make_basis(ts, [0.0]),
        funding_oi_weighted_bars=_make_funding(ts, [0.0]),
        stablecoin_oi_bars=_make_oi(ts, [1.0]),
    )
    assert bars[0].taker_delta_usd == 2_000_000.0
    # (short - long) / total = (100 - 300) / 400 = -0.5
    # Negative = more longs liquidated (bearish flush).
    assert bars[0].liq_imbalance == pytest.approx(-0.5)


def test_align_liq_imbalance_zero_when_both_zero() -> None:
    """When both long and short liquidations are zero, imbalance is 0 (not NaN)."""
    ts = [_ts(0, 0)]
    price = [(ts[0], 70000.0, 70000.0, 100.0)]

    bars = align_strategy_c_bars(
        price_bars=price,
        oi_bars=_make_oi(ts, [1.0]),
        funding_bars=_make_funding(ts, [0.0]),
        liquidation_bars=_make_liq(ts, [0.0], [0.0]),
        taker_bars=_make_taker(ts, [0, 0], [0, 0]),
        cvd_bars=_make_cvd(ts, [0]),
        basis_bars=_make_basis(ts, [0.0]),
        funding_oi_weighted_bars=_make_funding(ts, [0.0]),
        stablecoin_oi_bars=_make_oi(ts, [1.0]),
    )
    assert bars[0].liq_imbalance == 0.0


def test_align_filters_to_common_timestamps() -> None:
    """When sources have different time ranges, output is the intersection (no missing data)."""
    ts_full = [_ts(0, 0), _ts(0, 15), _ts(0, 30), _ts(0, 45)]
    ts_partial = [_ts(0, 15), _ts(0, 30)]  # OI only has middle 2 bars

    price = [(t, 70000.0, 70000.0, 100.0) for t in ts_full]

    bars = align_strategy_c_bars(
        price_bars=price,
        oi_bars=_make_oi(ts_partial, [1.0, 1.1]),  # only 2 bars
        funding_bars=_make_funding(ts_full, [0.0] * 4),
        liquidation_bars=_make_liq(ts_full, [0]*4, [0]*4),
        taker_bars=_make_taker(ts_full, [0]*4, [0]*4),
        cvd_bars=_make_cvd(ts_full, [0]*4),
        basis_bars=_make_basis(ts_full, [0.0] * 4),
        funding_oi_weighted_bars=_make_funding(ts_full, [0.0] * 4),
        stablecoin_oi_bars=_make_oi(ts_full, [1.0] * 4),
    )
    # Result is intersection — only the 2 bars where OI is present
    assert len(bars) == 2
    assert bars[0].timestamp == _ts(0, 15)
    assert bars[1].timestamp == _ts(0, 30)


def test_align_preserves_chronological_order() -> None:
    """Output rows are in ascending timestamp order."""
    ts = [_ts(0, 0), _ts(0, 15), _ts(0, 30)]
    price = [(t, 70000.0, 70000.0, 100.0) for t in ts]

    bars = align_strategy_c_bars(
        price_bars=price,
        oi_bars=_make_oi(ts, [1.0, 2.0, 3.0]),
        funding_bars=_make_funding(ts, [0.0] * 3),
        liquidation_bars=_make_liq(ts, [0]*3, [0]*3),
        taker_bars=_make_taker(ts, [0]*3, [0]*3),
        cvd_bars=_make_cvd(ts, [0]*3),
        basis_bars=_make_basis(ts, [0.0] * 3),
        funding_oi_weighted_bars=_make_funding(ts, [0.0] * 3),
        stablecoin_oi_bars=_make_oi(ts, [1.0] * 3),
    )
    timestamps = [b.timestamp for b in bars]
    assert timestamps == sorted(timestamps)
