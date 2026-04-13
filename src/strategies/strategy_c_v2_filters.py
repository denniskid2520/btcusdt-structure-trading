"""Strategy C v2 Phase 3 signal filters.

Pure post-hoc transforms on {-1, 0, +1} signal vectors. Each filter
returns a new list of the same length, leaving the input untouched.

These are strategy-agnostic: they work on any signal stream produced by
any Phase 2 or Phase 3 strategy. They exist because Phase 3 needs to ask:
    - What does long-only / short-only do to the same base strategy?
    - What does a funding-regime veto do to the same base strategy?
without rewriting the strategy every time.

Conventions:
    - "long" side filter drops all -1 signals (long or flat).
    - "short" side filter drops all +1 signals (short or flat).
    - "both" is the identity — every signal passes through.
    - Funding filter is "safe by default": when the relevant funding
      field is None (warmup), the filter does NOT block. You cannot
      prove the regime is hostile, so you do not block the trade.
"""
from __future__ import annotations

from typing import Any, Literal, Sequence

Side = Literal["long", "short", "both"]


# ── side filter ─────────────────────────────────────────────────────


def apply_side_filter(signals: Sequence[int], *, side: Side) -> list[int]:
    """Filter a signal stream to a single side (or pass everything).

    Args:
        signals: Input signal vector in {-1, 0, +1}.
        side: "long" → zero out -1; "short" → zero out +1; "both" → identity.

    Returns:
        A new list of the same length.

    Raises:
        ValueError: if `side` is not one of the three allowed values.
    """
    if side == "long":
        return [s if s > 0 else 0 for s in signals]
    if side == "short":
        return [s if s < 0 else 0 for s in signals]
    if side == "both":
        return list(signals)
    raise ValueError(
        f"side must be 'long', 'short', or 'both'; got {side!r}"
    )


# ── funding filter ──────────────────────────────────────────────────


def apply_funding_filter(
    signals: Sequence[int],
    features: Sequence[Any],
    *,
    max_long_funding: float | None = None,
    min_short_funding: float | None = None,
    use_cum_24h: bool = False,
) -> list[int]:
    """Veto signals when funding is hostile to the direction.

    Args:
        signals: Signal stream, same length as features.
        features: Feature rows exposing `funding_rate` (default) or
            `funding_cum_24h` (when `use_cum_24h=True`).
        max_long_funding: If set, long signals are vetoed (→ 0) when the
            funding field exceeds this threshold. Typical value: 0.0003
            (30 bp per 8h ≈ hostile to longs).
        min_short_funding: If set, short signals are vetoed when the
            funding field falls below this threshold. Typical value:
            -0.0003 (hostile to shorts).
        use_cum_24h: If True, read `funding_cum_24h` instead of
            `funding_rate`. Use for "funding has been hostile for a day"
            regime filtering rather than "funding was hostile at the
            last settlement."

    Returns:
        A new list of the same length. Zero signals and None funding
        fields are passed through unchanged (safe default).

    Raises:
        ValueError: if signals and features have different lengths.
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

        val = feat.funding_cum_24h if use_cum_24h else feat.funding_rate
        if val is None:
            # Can't prove hostility during warmup — pass through.
            out.append(sig)
            continue

        if sig > 0 and max_long_funding is not None and val > max_long_funding:
            out.append(0)
        elif sig < 0 and min_short_funding is not None and val < min_short_funding:
            out.append(0)
        else:
            out.append(sig)

    return out
