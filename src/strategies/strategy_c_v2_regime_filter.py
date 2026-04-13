"""Strategy C v2 manual_edge_extraction regime filters.

Pure post-hoc signal transforms that gate entries by market regime.
Each filter reads a feature row and decides whether a signal should
fire given the current regime. Unlike the Phase 3 side / funding
filters, these model discretionary "I only trade in favorable
regimes" behavior.

Three filters:

    apply_trend_filter          — higher-TF trend must match side
    apply_volatility_filter     — realised vol must be in a target band
    apply_rsi_extremity_filter  — RSI must be comfortably past trigger

All filters are additive: they can be stacked via composition.

Null-safety convention: when any required feature is None (warmup),
the filter passes the signal through unchanged. You cannot prove the
regime is hostile during warmup, so you do not block the trade.
"""
from __future__ import annotations

from typing import Any, Literal, Sequence

TrendMode = Literal[
    "ema_cross",            # ema_50 vs ema_200
    "close_vs_sma200",      # close vs sma_200
    "long_only_bull_regime",  # ema_cross but also blocks ALL shorts
]


# ── trend filter ────────────────────────────────────────────────────


def apply_trend_filter(
    signals: Sequence[int],
    features: Sequence[Any],
    *,
    mode: TrendMode,
) -> list[int]:
    """Block signals whose direction does not match the higher-TF trend.

    Args:
        signals: Signal stream (-1 / 0 / +1).
        features: Feature rows with ema_50, ema_200, sma_200, close.
        mode:
            - "ema_cross": long only when ema_50 > ema_200, short only when <.
            - "close_vs_sma200": long only when close > sma_200, short when <.
            - "long_only_bull_regime": ema_cross gate for longs, block ALL
               shorts regardless of regime.

    Returns:
        Signal stream with violating entries zeroed out.
    """
    if len(signals) != len(features):
        raise ValueError(
            f"signals length {len(signals)} != features length {len(features)}"
        )
    if mode not in ("ema_cross", "close_vs_sma200", "long_only_bull_regime"):
        raise ValueError(f"unknown trend mode: {mode!r}")

    out: list[int] = []
    for sig, feat in zip(signals, features):
        if sig == 0:
            out.append(0)
            continue

        if mode == "long_only_bull_regime":
            if sig < 0:
                # Block ALL shorts
                out.append(0)
                continue
            # Long: evaluate like ema_cross
            ema_50 = getattr(feat, "ema_50", None)
            ema_200 = getattr(feat, "ema_200", None)
            if ema_50 is None or ema_200 is None:
                out.append(sig)  # warmup pass-through
                continue
            out.append(sig if ema_50 > ema_200 else 0)
            continue

        if mode == "ema_cross":
            ema_50 = getattr(feat, "ema_50", None)
            ema_200 = getattr(feat, "ema_200", None)
            if ema_50 is None or ema_200 is None:
                out.append(sig)
                continue
            bullish = ema_50 > ema_200
            if sig > 0 and not bullish:
                out.append(0)
            elif sig < 0 and bullish:
                out.append(0)
            else:
                out.append(sig)
            continue

        # mode == "close_vs_sma200"
        close = getattr(feat, "close", None)
        sma_200 = getattr(feat, "sma_200", None)
        if close is None or sma_200 is None:
            out.append(sig)
            continue
        bullish = close > sma_200
        if sig > 0 and not bullish:
            out.append(0)
        elif sig < 0 and bullish:
            out.append(0)
        else:
            out.append(sig)

    return out


# ── volatility filter ───────────────────────────────────────────────


def apply_volatility_filter(
    signals: Sequence[int],
    features: Sequence[Any],
    *,
    min_rv: float | None = None,
    max_rv: float | None = None,
    rv_field: str = "rv_4h",
) -> list[int]:
    """Block signals when realized volatility is outside the target band.

    Args:
        signals: Signal stream.
        features: Feature rows with rv_4h (or specified rv_field).
        min_rv: If set, blocks when rv < min_rv (too quiet).
        max_rv: If set, blocks when rv > max_rv (too chaotic).
        rv_field: Which RV field to read (rv_1h / rv_4h / rv_1d / rv_7d).

    Returns:
        Filtered signal stream.
    """
    if len(signals) != len(features):
        raise ValueError(
            f"signals length {len(signals)} != features length {len(features)}"
        )
    out: list[int] = []
    for sig, feat in zip(signals, features):
        if sig == 0:
            out.append(0)
            continue
        rv = getattr(feat, rv_field, None)
        if rv is None:
            out.append(sig)
            continue
        if min_rv is not None and rv < min_rv:
            out.append(0)
            continue
        if max_rv is not None and rv > max_rv:
            out.append(0)
            continue
        out.append(sig)
    return out


# ── RSI extremity filter ────────────────────────────────────────────


def apply_rsi_extremity_filter(
    signals: Sequence[int],
    features: Sequence[Any],
    *,
    long_min_rsi: float | None = None,
    short_max_rsi: float | None = None,
    rsi_field: str = "rsi_14",
) -> list[int]:
    """Block signals unless RSI is comfortably past the trigger threshold.

    This filters the "barely past 70" reflex trades and keeps only
    setups where RSI is deeply above 70 (or below 30 for shorts).
    Models the manual behavior of "I only take longs when RSI is
    obviously trending."

    Args:
        signals: Signal stream.
        features: Feature rows with the specified rsi field.
        long_min_rsi: Minimum RSI to allow a long (e.g. 75 blocks
            signals that fired at RSI 71).
        short_max_rsi: Maximum RSI to allow a short (e.g. 25).
        rsi_field: Which RSI field to read.

    Returns:
        Filtered signal stream.
    """
    if len(signals) != len(features):
        raise ValueError(
            f"signals length {len(signals)} != features length {len(features)}"
        )
    out: list[int] = []
    for sig, feat in zip(signals, features):
        if sig == 0:
            out.append(0)
            continue
        rsi = getattr(feat, rsi_field, None)
        if rsi is None:
            out.append(sig)
            continue
        if sig > 0 and long_min_rsi is not None and rsi < long_min_rsi:
            out.append(0)
            continue
        if sig < 0 and short_max_rsi is not None and rsi > short_max_rsi:
            out.append(0)
            continue
        out.append(sig)
    return out
