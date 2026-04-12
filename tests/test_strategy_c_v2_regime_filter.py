"""Tests for Strategy C v2 manual_edge_extraction regime filters.

New filters added for the regime-selection study:

    apply_trend_filter     — require higher-TF trend to match side
    apply_volatility_filter — require RV in a target percentile band
    apply_rsi_extremity_filter — require RSI to be "comfortably past"
                                 the trigger threshold (conviction)

All filters are pure post-hoc transforms on signal streams. They zero
out signals where the regime condition fails, never flip the sign.
"""
from __future__ import annotations

import pytest

from strategies.strategy_c_v2_regime_filter import (
    apply_rsi_extremity_filter,
    apply_trend_filter,
    apply_volatility_filter,
)


class _Feat:
    """Duck-typed feature row."""

    def __init__(
        self,
        *,
        ema_50: float | None = None,
        ema_200: float | None = None,
        sma_200: float | None = None,
        close: float = 100.0,
        rv_4h: float | None = None,
        rsi_14: float | None = None,
        rsi_30: float | None = None,
    ) -> None:
        self.ema_50 = ema_50
        self.ema_200 = ema_200
        self.sma_200 = sma_200
        self.close = close
        self.rv_4h = rv_4h
        self.rsi_14 = rsi_14
        self.rsi_30 = rsi_30


# ── apply_trend_filter ──────────────────────────────────────────────


def test_trend_filter_blocks_long_when_ema50_below_ema200() -> None:
    """Long signal blocked when 4h trend is bearish (EMA50 < EMA200)."""
    signals = [1]
    features = [_Feat(ema_50=95.0, ema_200=100.0)]
    out = apply_trend_filter(signals, features, mode="ema_cross")
    assert out == [0]


def test_trend_filter_allows_long_when_ema50_above_ema200() -> None:
    signals = [1]
    features = [_Feat(ema_50=105.0, ema_200=100.0)]
    out = apply_trend_filter(signals, features, mode="ema_cross")
    assert out == [1]


def test_trend_filter_blocks_short_when_ema50_above_ema200() -> None:
    signals = [-1]
    features = [_Feat(ema_50=105.0, ema_200=100.0)]
    out = apply_trend_filter(signals, features, mode="ema_cross")
    assert out == [0]


def test_trend_filter_allows_short_when_ema50_below_ema200() -> None:
    signals = [-1]
    features = [_Feat(ema_50=95.0, ema_200=100.0)]
    out = apply_trend_filter(signals, features, mode="ema_cross")
    assert out == [-1]


def test_trend_filter_sma200_mode_uses_close_vs_sma200() -> None:
    """sma_200 mode: long allowed when close > sma_200, short when close < sma_200."""
    # Long allowed
    sigs = apply_trend_filter(
        [1],
        [_Feat(close=110.0, sma_200=100.0)],
        mode="close_vs_sma200",
    )
    assert sigs == [1]
    # Long blocked
    sigs = apply_trend_filter(
        [1],
        [_Feat(close=90.0, sma_200=100.0)],
        mode="close_vs_sma200",
    )
    assert sigs == [0]
    # Short allowed
    sigs = apply_trend_filter(
        [-1],
        [_Feat(close=90.0, sma_200=100.0)],
        mode="close_vs_sma200",
    )
    assert sigs == [-1]


def test_trend_filter_none_values_pass_through() -> None:
    """Warmup None values should pass through unchanged (safe default)."""
    signals = [1, -1]
    features = [
        _Feat(ema_50=None, ema_200=100.0),
        _Feat(ema_50=95.0, ema_200=None),
    ]
    out = apply_trend_filter(signals, features, mode="ema_cross")
    assert out == [1, -1]


def test_trend_filter_zero_signals_unchanged() -> None:
    signals = [0, 0, 0]
    features = [_Feat(ema_50=95.0, ema_200=100.0) for _ in range(3)]
    out = apply_trend_filter(signals, features, mode="ema_cross")
    assert out == [0, 0, 0]


def test_trend_filter_invalid_mode_raises() -> None:
    with pytest.raises(ValueError, match="mode"):
        apply_trend_filter([1], [_Feat()], mode="bogus")  # type: ignore[arg-type]


def test_trend_filter_long_only_mode_blocks_all_shorts() -> None:
    """long_only_bull_regime mode: allow long when bullish, block ALL shorts always."""
    sigs = apply_trend_filter(
        [1, -1, 1, -1],
        [
            _Feat(ema_50=105.0, ema_200=100.0),  # bullish — long allowed
            _Feat(ema_50=105.0, ema_200=100.0),  # bullish — short STILL blocked
            _Feat(ema_50=95.0, ema_200=100.0),   # bearish — long blocked
            _Feat(ema_50=95.0, ema_200=100.0),   # bearish — short still blocked
        ],
        mode="long_only_bull_regime",
    )
    assert sigs == [1, 0, 0, 0]


def test_trend_filter_length_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        apply_trend_filter([1, 0], [_Feat()], mode="ema_cross")


# ── apply_volatility_filter ─────────────────────────────────────────


def test_volatility_filter_allows_when_rv_in_band() -> None:
    sigs = apply_volatility_filter(
        [1],
        [_Feat(rv_4h=0.015)],
        min_rv=0.010,
        max_rv=0.030,
    )
    assert sigs == [1]


def test_volatility_filter_blocks_when_rv_too_low() -> None:
    sigs = apply_volatility_filter(
        [1],
        [_Feat(rv_4h=0.005)],
        min_rv=0.010,
    )
    assert sigs == [0]


def test_volatility_filter_blocks_when_rv_too_high() -> None:
    sigs = apply_volatility_filter(
        [1],
        [_Feat(rv_4h=0.050)],
        max_rv=0.030,
    )
    assert sigs == [0]


def test_volatility_filter_none_passes_through() -> None:
    sigs = apply_volatility_filter([1], [_Feat(rv_4h=None)], min_rv=0.010)
    assert sigs == [1]


def test_volatility_filter_no_bounds_is_identity() -> None:
    sigs = apply_volatility_filter([1, -1, 0], [_Feat(rv_4h=0.001) for _ in range(3)])
    assert sigs == [1, -1, 0]


# ── apply_rsi_extremity_filter ──────────────────────────────────────


def test_rsi_extremity_filter_allows_deep_long() -> None:
    """RSI at 80 is well past 70 → conviction long, allow."""
    sigs = apply_rsi_extremity_filter(
        [1],
        [_Feat(rsi_14=80.0)],
        long_min_rsi=75.0,
        short_max_rsi=25.0,
    )
    assert sigs == [1]


def test_rsi_extremity_filter_blocks_borderline_long() -> None:
    """RSI at 71 is barely past 70 → low conviction, block."""
    sigs = apply_rsi_extremity_filter(
        [1],
        [_Feat(rsi_14=71.0)],
        long_min_rsi=75.0,
        short_max_rsi=25.0,
    )
    assert sigs == [0]


def test_rsi_extremity_filter_rsi_field_selector() -> None:
    """With rsi_field='rsi_30', reads rsi_30 instead of rsi_14."""
    sigs = apply_rsi_extremity_filter(
        [1],
        [_Feat(rsi_14=90.0, rsi_30=71.0)],
        long_min_rsi=75.0,
        rsi_field="rsi_30",
    )
    assert sigs == [0]  # rsi_30 is below threshold


def test_rsi_extremity_filter_none_passes_through() -> None:
    sigs = apply_rsi_extremity_filter(
        [1],
        [_Feat(rsi_14=None)],
        long_min_rsi=75.0,
    )
    assert sigs == [1]
