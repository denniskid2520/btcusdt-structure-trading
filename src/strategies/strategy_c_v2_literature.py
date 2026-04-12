"""Strategy C v2 literature benchmark family (F1).

Five rule-based strategies that live at the bottom of the leaderboard
specifically to give every subsequent family a number to beat:

    rsi_only_signals     — trend-following RSI (Stefaniuk 2025)
    macd_only_signals    — sign of the MACD histogram
    rsi_and_macd_signals — AND gate of the two
    buy_and_hold_signals — fires a single +1 at bar 0
    flat_signals         — never trades

These are all "persistent" signals: if the condition holds across multiple
bars, the function emits +1 (or -1) at every qualifying bar. The backtester
opens one trade at the first qualifying bar, holds for `hold_bars`, and only
opens a second trade after the cooldown expires. So a 100-bar stretch of
RSI > 70 does NOT produce 100 overlapping longs — it produces a sequence of
disjoint long trades spaced `hold_bars + cooldown` apart.

Each function takes a `features` stream and returns a list of ints in
{-1, 0, +1} with the same length. The "feature" type is duck-typed: the
function only reads the attributes it needs (`rsi_14`, `rsi_30`, `macd_hist`),
so callers can pass either `StrategyCV2Features` or a test stub.

References:
    Stefaniuk (2025) — "Using Informer networks with RSI/MACD features on BTC
        USDT-M perpetual 1h bars." Trend-following interpretation of RSI:
        > 70 = strong momentum long, < 30 = strong momentum short.
    Murphy (1999) — Technical Analysis of the Financial Markets. MACD
        histogram sign as trend confirmation.
"""
from __future__ import annotations

from typing import Any, Sequence

Features = Sequence[Any]  # duck-typed: we only read attributes we need
RsiOverride = Sequence[float | None]  # pre-computed RSI series for arbitrary periods


# ── rsi_only ────────────────────────────────────────────────────────


def rsi_only_signals(
    features: Features,
    *,
    rsi_period: int = 14,
    upper: float = 70.0,
    lower: float = 30.0,
    rsi_override: Sequence[float | None] | None = None,
) -> list[int]:
    """Trend-following RSI signal (Stefaniuk 2025).

    Rule per bar:
        rsi > upper → +1 (long)
        rsi < lower → -1 (short)
        else        → 0

    Args:
        features: Stream exposing `rsi_14` and/or `rsi_30` attributes.
        rsi_period: 14 or 30, selects which RSI field to read. Ignored
            when `rsi_override` is provided.
        upper: Upper trend-following threshold (default 70).
        lower: Lower trend-following threshold (default 30).
        rsi_override: Optional pre-computed RSI series, same length as
            `features`. When supplied, the function reads `rsi_override[i]`
            at bar i instead of `f.rsi_14` / `f.rsi_30`. Use this to sweep
            arbitrary RSI periods (21, 34, 42, ...) without extending the
            feature dataclass.

    Returns:
        List of ints in {-1, 0, +1}, one per input feature row.

    Raises:
        ValueError: if `rsi_override` is provided with a length that
            doesn't match `features`.
    """
    if rsi_override is not None and len(rsi_override) != len(features):
        raise ValueError(
            f"rsi_override length {len(rsi_override)} != features length {len(features)}"
        )

    out: list[int] = []
    for i, f in enumerate(features):
        if rsi_override is not None:
            rsi = rsi_override[i]
        else:
            rsi = f.rsi_14 if rsi_period == 14 else f.rsi_30
        if rsi is None:
            out.append(0)
        elif rsi > upper:
            out.append(1)
        elif rsi < lower:
            out.append(-1)
        else:
            out.append(0)
    return out


# ── macd_only ───────────────────────────────────────────────────────


def macd_only_signals(features: Features) -> list[int]:
    """MACD histogram sign as the sole signal.

    Rule per bar:
        macd_hist > 0 → +1
        macd_hist < 0 → -1
        else (None, 0) → 0
    """
    out: list[int] = []
    for f in features:
        h = f.macd_hist
        if h is None or h == 0:
            out.append(0)
        elif h > 0:
            out.append(1)
        else:
            out.append(-1)
    return out


# ── rsi_and_macd (AND gate) ─────────────────────────────────────────


def rsi_and_macd_signals(
    features: Features,
    *,
    rsi_period: int = 14,
    upper: float = 70.0,
    lower: float = 30.0,
    rsi_override: Sequence[float | None] | None = None,
) -> list[int]:
    """Both RSI and MACD histogram must confirm the direction.

    Rule per bar:
        rsi > upper AND macd_hist > 0 → +1
        rsi < lower AND macd_hist < 0 → -1
        else → 0

    The `rsi_override` parameter behaves identically to `rsi_only_signals`:
    when supplied it is read instead of `f.rsi_14` / `f.rsi_30`, so
    arbitrary RSI periods can be swept without feature-module changes.
    """
    if rsi_override is not None and len(rsi_override) != len(features):
        raise ValueError(
            f"rsi_override length {len(rsi_override)} != features length {len(features)}"
        )

    out: list[int] = []
    for i, f in enumerate(features):
        if rsi_override is not None:
            rsi = rsi_override[i]
        else:
            rsi = f.rsi_14 if rsi_period == 14 else f.rsi_30
        h = f.macd_hist
        if rsi is None or h is None:
            out.append(0)
            continue
        if rsi > upper and h > 0:
            out.append(1)
        elif rsi < lower and h < 0:
            out.append(-1)
        else:
            out.append(0)
    return out


# ── buy-and-hold and flat baselines ─────────────────────────────────


def buy_and_hold_signals(features: Features) -> list[int]:
    """Fire a single +1 at bar 0, then stay silent.

    The runner is expected to use a very large `hold_bars` (e.g. len(bars))
    so the one trade held from bar 1 open to bar n-1 close becomes the
    canonical buy-and-hold benchmark.
    """
    n = len(features)
    if n == 0:
        return []
    out = [0] * n
    out[0] = 1
    return out


def flat_signals(features: Features) -> list[int]:
    """Never trade. Exists so the leaderboard always has a zero reference."""
    return [0] * len(features)
