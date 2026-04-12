"""Tests for the Strategy C v2 feature module (Track A implementation).

Phase 1 scaffolded the dataclass with NotImplementedError stubs. Phase 2
replaces the stub with a working implementation covering Family A
(price/technical, computed from OHLCV) and Family B (funding from Binance
fundingRate history).

Contract pinned by these tests:
    - `compute_features_v2(bars, funding_records=None, bar_hours=0.25)` →
      list[StrategyCV2Features] same length as bars
    - Calendar fields (hour_of_day, day_of_week, is_weekend) are always populated
    - All indicator fields are None during warmup
    - Returns, RSI, MACD, SMA, EMA, Bollinger, Stochastic, ATR are all
      causal (no look-ahead)
    - Funding fields (funding_rate, bars_to_next_funding, funding_cum_24h)
      are only populated when `funding_records` is provided
"""
from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timedelta

import pytest

from adapters.base import MarketBar
from adapters.binance_futures import FundingRateRecord
from data.strategy_c_v2_features import (
    StrategyCV2Features,
    compute_features_v2,
)


# ── helpers ──────────────────────────────────────────────────────────


def _make_bars(
    closes: list[float],
    *,
    start: datetime = datetime(2024, 1, 1, 0, 0, 0),
    interval_min: int = 15,
) -> list[MarketBar]:
    """Build synthetic MarketBars from close prices. Open == close for simplicity."""
    return [
        MarketBar(
            timestamp=start + timedelta(minutes=interval_min * i),
            open=c,
            high=c * 1.001,
            low=c * 0.999,
            close=c,
            volume=100.0,
        )
        for i, c in enumerate(closes)
    ]


def _make_funding(times_and_rates: list[tuple[datetime, float]]) -> list[FundingRateRecord]:
    return [
        FundingRateRecord(timestamp=t, funding_rate=r, mark_price=None)
        for t, r in times_and_rates
    ]


# ── dataclass shape (pinned from Phase 1, still required) ───────────


_FAMILY_A_FIELDS = {
    "ret_1", "ret_4", "ret_8", "ret_16", "ret_32",
    "rv_1h", "rv_4h", "rv_1d", "rv_7d",
    "mom_30",
    "rsi_14", "rsi_30",
    "macd", "macd_signal", "macd_hist",
    "stoch_k_30", "stoch_d_30", "stoch_k_200", "stoch_d_200",
    "sma_20", "sma_50", "sma_200",
    "ema_20", "ema_50", "ema_200",
    "bb_mid_20", "bb_upper_20", "bb_lower_20", "bb_width_20", "bb_pctb_20",
    "atr_14", "atr_30",
    "hour_of_day", "day_of_week", "is_weekend",
}

_FAMILY_B_FIELDS = {
    "funding_rate",
    "bars_to_next_funding",
    "funding_cum_24h",
    "basis_perp_vs_spot",
}


def test_features_dataclass_has_family_a_fields() -> None:
    names = {f.name for f in fields(StrategyCV2Features)}
    missing = _FAMILY_A_FIELDS - names
    assert not missing, f"missing Family A fields: {missing}"


def test_features_dataclass_has_family_b_fields() -> None:
    names = {f.name for f in fields(StrategyCV2Features)}
    missing = _FAMILY_B_FIELDS - names
    assert not missing, f"missing Family B fields: {missing}"


# ── shape and anchor ────────────────────────────────────────────────


def test_compute_features_v2_returns_one_per_bar() -> None:
    bars = _make_bars([100.0 + i for i in range(50)])
    out = compute_features_v2(bars)
    assert len(out) == len(bars)


def test_compute_features_v2_empty_input_returns_empty() -> None:
    assert compute_features_v2([]) == []


def test_compute_features_v2_timestamps_match_bars() -> None:
    bars = _make_bars([100.0 + i for i in range(20)])
    out = compute_features_v2(bars)
    for b, f in zip(bars, out):
        assert f.timestamp == b.timestamp


def test_compute_features_v2_close_anchor_matches_bar() -> None:
    bars = _make_bars([100.0 + i for i in range(20)])
    out = compute_features_v2(bars)
    for b, f in zip(bars, out):
        assert f.close == pytest.approx(b.close)


# ── calendar fields always populated ────────────────────────────────


def test_compute_features_v2_calendar_fields_always_populated() -> None:
    bars = _make_bars(
        [100.0] * 10,
        start=datetime(2024, 1, 6, 13, 30),  # Saturday 13:30 (weekday=5)
    )
    out = compute_features_v2(bars)
    assert out[0].hour_of_day == 13
    assert out[0].day_of_week == 5  # Saturday
    assert out[0].is_weekend is True


def test_compute_features_v2_weekday_is_false_on_monday() -> None:
    bars = _make_bars([100.0] * 3, start=datetime(2024, 1, 8, 9, 0))  # Monday
    out = compute_features_v2(bars)
    assert out[0].day_of_week == 0
    assert out[0].is_weekend is False


# ── returns ─────────────────────────────────────────────────────────


def test_compute_features_v2_ret_1_simple() -> None:
    closes = [100.0, 101.0, 102.01, 103.0303]
    bars = _make_bars(closes)
    out = compute_features_v2(bars)
    assert out[0].ret_1 is None  # no previous bar
    assert out[1].ret_1 == pytest.approx((101.0 - 100.0) / 100.0)
    assert out[2].ret_1 == pytest.approx((102.01 - 101.0) / 101.0)


def test_compute_features_v2_ret_32_is_none_before_32_bars() -> None:
    bars = _make_bars([100.0 + i for i in range(50)])
    out = compute_features_v2(bars)
    for i in range(32):
        assert out[i].ret_32 is None
    assert out[32] is not None and out[32].ret_32 is not None


def test_compute_features_v2_ret_4_on_rising_series() -> None:
    bars = _make_bars([100.0 + i for i in range(10)])
    out = compute_features_v2(bars)
    # ret_4 at index 4 = (104 - 100) / 100 = 0.04
    assert out[4].ret_4 == pytest.approx(0.04)


# ── momentum ────────────────────────────────────────────────────────


def test_compute_features_v2_mom_30_is_none_before_30() -> None:
    bars = _make_bars([100.0 + i for i in range(40)])
    out = compute_features_v2(bars)
    for i in range(30):
        assert out[i].mom_30 is None
    assert out[30].mom_30 == pytest.approx(30.0)  # 130 - 100


# ── RSI ─────────────────────────────────────────────────────────────


def test_compute_features_v2_rsi_14_is_none_before_14() -> None:
    bars = _make_bars([100.0 + i for i in range(30)])
    out = compute_features_v2(bars)
    for i in range(14):
        assert out[i].rsi_14 is None
    assert out[14].rsi_14 is not None


def test_compute_features_v2_rsi_14_on_monotonic_rise_is_100() -> None:
    """Strictly rising series has zero losses → RSI = 100."""
    bars = _make_bars([100.0 + i for i in range(30)])
    out = compute_features_v2(bars)
    assert out[20].rsi_14 == pytest.approx(100.0)


def test_compute_features_v2_rsi_14_on_monotonic_fall_is_0() -> None:
    """Strictly falling series has zero gains → RSI = 0."""
    bars = _make_bars([100.0 - i for i in range(30)])
    out = compute_features_v2(bars)
    assert out[20].rsi_14 == pytest.approx(0.0)


# ── MACD ────────────────────────────────────────────────────────────


def test_compute_features_v2_macd_warmup_is_none() -> None:
    """MACD needs slow + signal = 26 + 9 = 35 bars to produce a valid value."""
    bars = _make_bars([100.0 + i * 0.1 for i in range(50)])
    out = compute_features_v2(bars)
    assert out[10].macd is None
    assert out[10].macd_signal is None
    # After full warmup both are populated.
    assert out[40].macd is not None
    assert out[40].macd_signal is not None
    assert out[40].macd_hist is not None


def test_compute_features_v2_macd_hist_equals_macd_minus_signal() -> None:
    bars = _make_bars([100.0 + i * 0.1 for i in range(100)])
    out = compute_features_v2(bars)
    f = out[-1]
    assert f.macd is not None and f.macd_signal is not None and f.macd_hist is not None
    assert f.macd_hist == pytest.approx(f.macd - f.macd_signal)


# ── SMA / EMA ───────────────────────────────────────────────────────


def test_compute_features_v2_sma_20_is_none_before_20() -> None:
    bars = _make_bars([100.0] * 30)
    out = compute_features_v2(bars)
    for i in range(19):
        assert out[i].sma_20 is None
    assert out[19].sma_20 == pytest.approx(100.0)


def test_compute_features_v2_sma_20_on_arithmetic_series() -> None:
    bars = _make_bars([float(i) for i in range(1, 41)])  # 1..40
    out = compute_features_v2(bars)
    # sma_20 at index 19 = mean(1..20) = 10.5
    assert out[19].sma_20 == pytest.approx(10.5)
    # sma_20 at index 39 = mean(21..40) = 30.5
    assert out[39].sma_20 == pytest.approx(30.5)


def test_compute_features_v2_ema_50_warmup() -> None:
    bars = _make_bars([100.0] * 60)
    out = compute_features_v2(bars)
    assert out[48].ema_50 is None
    assert out[49].ema_50 == pytest.approx(100.0)


# ── Bollinger ───────────────────────────────────────────────────────


def test_compute_features_v2_bollinger_at_period() -> None:
    bars = _make_bars([100.0] * 25)
    out = compute_features_v2(bars)
    assert out[18].bb_mid_20 is None
    f = out[19]
    assert f.bb_mid_20 == pytest.approx(100.0)
    assert f.bb_upper_20 == pytest.approx(100.0)
    assert f.bb_lower_20 == pytest.approx(100.0)
    assert f.bb_width_20 == pytest.approx(0.0)
    assert f.bb_pctb_20 == pytest.approx(0.5)


def test_compute_features_v2_bollinger_on_varying_series() -> None:
    # Use a series with known std so we can verify mean/upper/lower
    closes = [float(i) for i in range(1, 21)]  # 1..20, mean=10.5, pop_std=sqrt(33.25)≈5.766
    bars = _make_bars(closes)
    out = compute_features_v2(bars)
    f = out[19]
    assert f.bb_mid_20 == pytest.approx(10.5)
    import math
    expected_std = math.sqrt(sum((x - 10.5) ** 2 for x in closes) / 20)
    assert f.bb_upper_20 == pytest.approx(10.5 + 2.0 * expected_std)
    assert f.bb_lower_20 == pytest.approx(10.5 - 2.0 * expected_std)


# ── Stochastic ──────────────────────────────────────────────────────


def test_compute_features_v2_stochastic_30_warmup() -> None:
    bars = _make_bars([100.0 + i * 0.5 for i in range(40)])
    out = compute_features_v2(bars)
    # Full stoch needs k_period + smooth_k - 1 for k, and + smooth_d - 1 for d.
    # 30 + 3 - 2 = 31 for k; 30 + 3 + 3 - 3 = 33 for d.
    assert out[30].stoch_k_30 is None  # still in warmup
    assert out[34].stoch_k_30 is not None


def test_compute_features_v2_stochastic_200_on_short_series_is_none() -> None:
    bars = _make_bars([100.0] * 50)
    out = compute_features_v2(bars)
    for f in out:
        assert f.stoch_k_200 is None


# ── ATR ─────────────────────────────────────────────────────────────


def test_compute_features_v2_atr_14_warmup() -> None:
    bars = _make_bars([100.0 + i for i in range(30)])
    out = compute_features_v2(bars)
    assert out[12].atr_14 is None
    assert out[13].atr_14 is not None


# ── Realized vol ────────────────────────────────────────────────────


def test_compute_features_v2_rv_1h_on_15m_bars() -> None:
    """With bar_hours=0.25 (15m), rv_1h uses 4 bars of log returns."""
    bars = _make_bars([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])
    out = compute_features_v2(bars, bar_hours=0.25)
    # At i=4, 4 log returns are available. At i<4, rv_1h is None.
    assert out[3].rv_1h is None  # only 3 log returns so far
    assert out[4].rv_1h is not None


# ── Funding features ────────────────────────────────────────────────


def test_compute_features_v2_funding_fields_none_when_no_records() -> None:
    bars = _make_bars([100.0] * 10)
    out = compute_features_v2(bars, funding_records=None)
    for f in out:
        assert f.funding_rate is None
        assert f.bars_to_next_funding is None
        assert f.funding_cum_24h is None


def test_compute_features_v2_funding_rate_forward_fills() -> None:
    """A 15m bar at 02:00 inherits the 00:00 settlement rate."""
    start = datetime(2024, 1, 1, 0, 0, 0)
    bars = _make_bars([100.0] * 40, start=start, interval_min=15)  # 00:00..09:45
    records = _make_funding([
        (datetime(2024, 1, 1, 0, 0, 0), 0.0001),
        (datetime(2024, 1, 1, 8, 0, 0), 0.0002),
    ])
    out = compute_features_v2(bars, funding_records=records)
    # Bar at 02:00 = index 8 → funding_rate should equal 0.0001 (from 00:00)
    assert out[8].funding_rate == pytest.approx(0.0001)
    # Bar at 09:00 = index 36 → funding_rate should equal 0.0002 (from 08:00)
    assert out[36].funding_rate == pytest.approx(0.0002)


def test_compute_features_v2_bars_to_next_funding_on_15m() -> None:
    """15m bars, funding every 8h → 32 bars between settlements."""
    start = datetime(2024, 1, 1, 0, 0, 0)
    bars = _make_bars([100.0] * 64, start=start, interval_min=15)
    records = _make_funding([
        (datetime(2024, 1, 1, 0, 0, 0), 0.0001),
        (datetime(2024, 1, 1, 8, 0, 0), 0.0002),
        (datetime(2024, 1, 1, 16, 0, 0), 0.0003),
    ])
    out = compute_features_v2(bars, funding_records=records)
    # Bar 0 is at 00:00 (funding) → next funding is bar 32 (08:00) → bars_to_next = 32
    assert out[0].bars_to_next_funding == 32
    # Bar 1 is at 00:15 → next funding at 08:00 → bars_to_next = 31
    assert out[1].bars_to_next_funding == 31
    # Bar 31 is at 07:45 → next funding at 08:00 → bars_to_next = 1
    assert out[31].bars_to_next_funding == 1
    # Bar 32 is at 08:00 → next funding is 16:00 → 32 bars away
    assert out[32].bars_to_next_funding == 32


def test_compute_features_v2_funding_cum_24h_is_rolling_sum_of_past_24h_settlements() -> None:
    """At each bar, funding_cum_24h = sum of settlements in (t-24h, t]."""
    start = datetime(2024, 1, 2, 0, 0, 0)  # well past the first settlement
    bars = _make_bars([100.0] * 4, start=start, interval_min=1)
    # Settlements every 8h across the 24h window
    records = _make_funding([
        (datetime(2024, 1, 1, 0, 0, 0), 0.0001),
        (datetime(2024, 1, 1, 8, 0, 0), 0.0002),
        (datetime(2024, 1, 1, 16, 0, 0), 0.0003),
        (datetime(2024, 1, 2, 0, 0, 0), 0.0004),
    ])
    out = compute_features_v2(bars, funding_records=records)
    # Bar 0 at 2024-01-02 00:00 → 24h window is (2024-01-01 00:00, 2024-01-02 00:00]
    # Settlements in that window: 08:00 (0.0002), 16:00 (0.0003), 2024-01-02 00:00 (0.0004)
    # 2024-01-01 00:00 is on the boundary → EXCLUDED by half-open lower bound.
    expected = 0.0002 + 0.0003 + 0.0004
    assert out[0].funding_cum_24h == pytest.approx(expected)


# ── no-lookahead property (causality spot check) ────────────────────


def test_compute_features_v2_is_causal_prefix_stable() -> None:
    """Computing on the prefix must give the same answer as slicing the full result."""
    bars_full = _make_bars([100.0 + i * 0.5 for i in range(200)])
    out_full = compute_features_v2(bars_full)
    out_prefix = compute_features_v2(bars_full[:100])
    # The first 100 features from the full run should match the 100-bar run exactly.
    for i in range(100):
        assert out_full[i].close == out_prefix[i].close
        assert out_full[i].ret_1 == out_prefix[i].ret_1
        assert out_full[i].sma_20 == out_prefix[i].sma_20
        assert out_full[i].rsi_14 == out_prefix[i].rsi_14
        assert out_full[i].macd == out_prefix[i].macd
        assert out_full[i].bb_mid_20 == out_prefix[i].bb_mid_20
        assert out_full[i].atr_14 == out_prefix[i].atr_14
