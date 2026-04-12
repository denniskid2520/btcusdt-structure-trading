"""Strategy C v2 shared dynamic sizing + adaptive hold logic.

This module is the **single source of truth** for the two manual-edge
building blocks that Phase 8 deploys live:

1. **Dynamic sizing multiplier** — a composite conviction score over
   RSI extremity / trend alignment / funding favorability / RV
   mid-band → a position_frac multiplier in [0.5, 1.5].

2. **Adaptive hold override** — a composite score over
   trend alignment / RSI extremity / funding tailwind →
   a per-trade hold_bars override in {base*0.5, base, base*1.5}.

Why a shared module: both the historical backtest
(`strategy_c_v2_backtest.run_v2_backtest`), the retrospective paper
runner, and the live monitor (`strategy_c_v2_live_monitor`) call the
**same** functions from this file. If any one of them re-implemented
the score formula, the parity gate would drift and the Phase 6
fabrication-style issue could recur.

The caller provides:
  - a duck-typed feature snapshot (one bar's StrategyCV2Features row)
  - a trade side (+1 long / -1 short / 0 no-op)
  - the base hold or base frac

The module returns:
  - a multiplier (for sizing) or an explicit hold_bars_override (for
    adaptive exit)

Vectorised helpers at the bottom (`compute_position_frac_override`,
`compute_hold_bars_override`) exist for the historical backtester
which operates on whole signal streams; they are thin loops over the
single-snapshot functions so the semantics are identical by
construction.

**Frozen numbers**: the default config values here MUST match the
values that produced the canonical Phase 8 OOS metrics. Do not change
them without re-running the walk-forward and updating the canonical
baseline file.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence


# ── dynamic sizing ───────────────────────────────────────────────────


@dataclass(frozen=True)
class DynamicSizingConfig:
    """Configuration for the 4-component dynamic sizing score.

    The default values here are the ones that produced the canonical
    Phase 8 metrics:
      - D1_long_dynamic: +164.32% OOS / DD 14.81% / 73 trades
      - D1_long_dynamic_adaptive: +204.55% OOS / DD 16.36% / 64 trades
      - C_long_dynamic: +135.97% OOS / DD 17.08% / 178 trades

    Any change to a field here is a breaking change to the canonical
    baseline. Update this dataclass AND re-run the walk-forward AND
    update `strategy_c_v2_canonical_baseline.CANONICAL_CELLS`.
    """
    # Which components of the score are active. Order-independent.
    components: tuple[str, ...] = (
        "rsi_extremity",
        "trend_alignment",
        "funding_favorable",
        "rv_mid_band",
    )

    # Fields read from the feature snapshot
    rsi_field: str = "rsi_14"
    ema_fast_field: str = "ema_50"
    ema_slow_field: str = "ema_200"
    funding_field: str = "funding_rate"
    rv_field: str = "rv_4h"

    # RSI extremity thresholds
    rsi_long_threshold: float = 70.0
    rsi_short_threshold: float = 30.0
    rsi_extremity_span: float = 20.0  # how many RSI points count as "full conviction"

    # Funding favorability bands
    funding_long_favorable_max: float = 0.0003   # ≤ this → score 1.0 for long
    funding_long_marginal_max: float = 0.0008    # ≤ this → score 0.5 for long
    funding_short_favorable_min: float = -0.0003  # ≥ this → score 1.0 for short
    funding_short_marginal_min: float = -0.0008   # ≥ this → score 0.5 for short

    # Realized-vol mid-band (1h/4h units depending on rv_field)
    rv_min: float = 0.005
    rv_max: float = 0.020

    # Multiplier mapping: multiplier = min + avg_score * (max - min)
    multiplier_min: float = 0.5
    multiplier_max: float = 1.5

    def __post_init__(self) -> None:
        if self.multiplier_min > self.multiplier_max:
            raise ValueError(
                f"multiplier_min {self.multiplier_min} > "
                f"multiplier_max {self.multiplier_max}"
            )
        if self.multiplier_min < 0:
            raise ValueError(
                f"multiplier_min must be >= 0, got {self.multiplier_min}"
            )
        if self.rsi_extremity_span <= 0:
            raise ValueError(
                f"rsi_extremity_span must be > 0, got {self.rsi_extremity_span}"
            )
        for comp in self.components:
            if comp not in (
                "rsi_extremity",
                "trend_alignment",
                "funding_favorable",
                "rv_mid_band",
            ):
                raise ValueError(f"unknown sizing component: {comp}")


DEFAULT_DYNAMIC_SIZING_CONFIG = DynamicSizingConfig()


@dataclass(frozen=True)
class DynamicSizingResult:
    """Full output of a single dynamic-sizing evaluation.

    Used by the live monitor and paper log so every telemetry row can
    record exactly what sizing decision was made and why.
    """
    multiplier: float            # in [config.multiplier_min, config.multiplier_max]
    raw_avg_score: float         # in [0, 1]; 1.0 if no components were readable
    components_used: tuple[str, ...]  # which components were able to read their fields
    component_scores: dict[str, float]  # per-component score in [0, 1]


def compute_sizing_multiplier(
    feature_snapshot: Any,
    side: int,
    config: DynamicSizingConfig = DEFAULT_DYNAMIC_SIZING_CONFIG,
) -> DynamicSizingResult:
    """Compute the position_frac multiplier for a single signal.

    Args:
        feature_snapshot: Duck-typed object with `config.rsi_field`,
            `config.ema_fast_field`, `config.ema_slow_field`,
            `config.funding_field`, `config.rv_field` attributes.
            Any of these may be None.
        side: +1 for long entry, -1 for short entry, 0 for no signal
            (returns multiplier=1.0 by convention).
        config: sizing configuration. Use the default unless you have
            a documented reason not to.

    Returns:
        DynamicSizingResult with multiplier + diagnostic fields.

    Semantics for "no readable components":
        If none of the configured components can be evaluated (all
        required fields are None), the multiplier defaults to 1.0
        (neutral — no sizing decision made) and raw_avg_score is 1.0.
        This is the behavior the manual_edge_extraction sweep used.
    """
    if side == 0:
        return DynamicSizingResult(
            multiplier=1.0,
            raw_avg_score=1.0,
            components_used=(),
            component_scores={},
        )

    component_scores: dict[str, float] = {}

    # 1. RSI extremity — how far past the threshold is RSI?
    if "rsi_extremity" in config.components:
        rsi = getattr(feature_snapshot, config.rsi_field, None)
        if rsi is not None:
            if side > 0:
                raw = (rsi - config.rsi_long_threshold) / config.rsi_extremity_span
            else:
                raw = (config.rsi_short_threshold - rsi) / config.rsi_extremity_span
            # Clamp to [0, 1]
            score = min(max(raw, 0.0), 1.0)
            component_scores["rsi_extremity"] = score

    # 2. Trend alignment — EMA fast vs slow agrees with trade direction
    if "trend_alignment" in config.components:
        ema_fast = getattr(feature_snapshot, config.ema_fast_field, None)
        ema_slow = getattr(feature_snapshot, config.ema_slow_field, None)
        if ema_fast is not None and ema_slow is not None:
            aligned = (
                (ema_fast > ema_slow and side > 0)
                or (ema_fast < ema_slow and side < 0)
            )
            component_scores["trend_alignment"] = 1.0 if aligned else 0.0

    # 3. Funding favorability — three-band mapping (1.0 / 0.5 / 0.0)
    if "funding_favorable" in config.components:
        fund = getattr(feature_snapshot, config.funding_field, None)
        if fund is not None:
            if side > 0:
                if fund <= config.funding_long_favorable_max:
                    score = 1.0
                elif fund <= config.funding_long_marginal_max:
                    score = 0.5
                else:
                    score = 0.0
            else:
                if fund >= config.funding_short_favorable_min:
                    score = 1.0
                elif fund >= config.funding_short_marginal_min:
                    score = 0.5
                else:
                    score = 0.0
            component_scores["funding_favorable"] = score

    # 4. RV mid-band — realized vol in the productive middle
    if "rv_mid_band" in config.components:
        rv = getattr(feature_snapshot, config.rv_field, None)
        if rv is not None:
            in_band = config.rv_min < rv < config.rv_max
            component_scores["rv_mid_band"] = 1.0 if in_band else 0.0

    if not component_scores:
        # No components readable — neutral multiplier 1.0
        return DynamicSizingResult(
            multiplier=1.0,
            raw_avg_score=1.0,
            components_used=(),
            component_scores={},
        )

    avg = sum(component_scores.values()) / len(component_scores)
    mult_range = config.multiplier_max - config.multiplier_min
    multiplier = config.multiplier_min + avg * mult_range

    return DynamicSizingResult(
        multiplier=multiplier,
        raw_avg_score=avg,
        components_used=tuple(component_scores.keys()),
        component_scores=component_scores,
    )


# ── adaptive hold ────────────────────────────────────────────────────


@dataclass(frozen=True)
class AdaptiveHoldConfig:
    """Configuration for the 3-component adaptive hold score.

    The default values here are the ones that produced the canonical
    D1_long_dynamic_adaptive number: +204.55% OOS / DD 16.36% /
    64 trades.

    C_long is intentionally NOT paired with adaptive hold — the
    manual_edge_extraction adaptive exit study found that C_long's
    4-bar hold is already the structural optimum and any modulation
    collapses return by ~58 pp. Deploying C_long_dynamic + adaptive
    hold is a Phase 8 anti-pattern.
    """
    components: tuple[str, ...] = (
        "trend_alignment",
        "rsi_extremity",
        "funding_tailwind",
    )

    # Fields read from the feature snapshot
    rsi_field: str = "rsi_14"
    ema_fast_field: str = "ema_50"
    ema_slow_field: str = "ema_200"
    funding_field: str = "funding_rate"

    # RSI extremity thresholds (higher bar than sizing — "very extreme")
    rsi_long_extremity: float = 78.0
    rsi_short_extremity: float = 22.0

    # Funding tailwind — strict favorability
    funding_long_tailwind_max: float = 0.0002
    funding_short_tailwind_min: float = -0.0002

    # Hold modulation factors
    extend_factor: float = 1.5
    compress_factor: float = 0.5

    # Hard caps
    max_hold_cap: int = 20
    min_hold_floor: int = 2

    # Score thresholds
    extend_threshold: int = 2    # score ≥ this → extend
    compress_threshold: int = 0  # score == this → compress

    def __post_init__(self) -> None:
        if self.extend_factor < self.compress_factor:
            raise ValueError(
                f"extend_factor {self.extend_factor} must be >= "
                f"compress_factor {self.compress_factor}"
            )
        if self.min_hold_floor < 1:
            raise ValueError(
                f"min_hold_floor must be >= 1, got {self.min_hold_floor}"
            )
        if self.max_hold_cap < self.min_hold_floor:
            raise ValueError(
                f"max_hold_cap {self.max_hold_cap} < "
                f"min_hold_floor {self.min_hold_floor}"
            )
        for comp in self.components:
            if comp not in (
                "trend_alignment",
                "rsi_extremity",
                "funding_tailwind",
            ):
                raise ValueError(f"unknown adaptive-hold component: {comp}")


DEFAULT_ADAPTIVE_HOLD_CONFIG = AdaptiveHoldConfig()


@dataclass(frozen=True)
class AdaptiveHoldResult:
    """Full output of a single adaptive-hold evaluation."""
    hold_bars: int
    score: int                    # 0..len(components)
    components_used: tuple[str, ...]
    component_scores: dict[str, int]  # per-component: 0 or 1
    regime: str                   # "extend" / "base" / "compress"


def compute_hold_override(
    feature_snapshot: Any,
    side: int,
    base_hold: int,
    config: AdaptiveHoldConfig = DEFAULT_ADAPTIVE_HOLD_CONFIG,
) -> AdaptiveHoldResult:
    """Compute the per-trade hold_bars override for a single signal.

    Args:
        feature_snapshot: Duck-typed object with `config.rsi_field`,
            `config.ema_fast_field`, `config.ema_slow_field`,
            `config.funding_field` attributes.
        side: +1 long / -1 short / 0 no-op (returns base_hold).
        base_hold: the cell's default hold_bars.
        config: adaptive-hold configuration.

    Returns:
        AdaptiveHoldResult with hold_bars + diagnostic fields.
    """
    if side == 0:
        return AdaptiveHoldResult(
            hold_bars=base_hold,
            score=0,
            components_used=(),
            component_scores={},
            regime="base",
        )
    if base_hold < 1:
        raise ValueError(f"base_hold must be >= 1, got {base_hold}")

    component_scores: dict[str, int] = {}

    if "trend_alignment" in config.components:
        ema_fast = getattr(feature_snapshot, config.ema_fast_field, None)
        ema_slow = getattr(feature_snapshot, config.ema_slow_field, None)
        if ema_fast is not None and ema_slow is not None:
            aligned = (
                (side > 0 and ema_fast > ema_slow)
                or (side < 0 and ema_fast < ema_slow)
            )
            component_scores["trend_alignment"] = 1 if aligned else 0

    if "rsi_extremity" in config.components:
        rsi = getattr(feature_snapshot, config.rsi_field, None)
        if rsi is not None:
            if side > 0 and rsi > config.rsi_long_extremity:
                component_scores["rsi_extremity"] = 1
            elif side < 0 and rsi < config.rsi_short_extremity:
                component_scores["rsi_extremity"] = 1
            else:
                component_scores["rsi_extremity"] = 0

    if "funding_tailwind" in config.components:
        fund = getattr(feature_snapshot, config.funding_field, None)
        if fund is not None:
            if side > 0 and fund < config.funding_long_tailwind_max:
                component_scores["funding_tailwind"] = 1
            elif side < 0 and fund > config.funding_short_tailwind_min:
                component_scores["funding_tailwind"] = 1
            else:
                component_scores["funding_tailwind"] = 0

    score = sum(component_scores.values())

    if score >= config.extend_threshold:
        hold_bars = min(
            config.max_hold_cap,
            int(base_hold * config.extend_factor),
        )
        regime = "extend"
    elif score == config.compress_threshold:
        hold_bars = max(
            config.min_hold_floor,
            int(base_hold * config.compress_factor),
        )
        regime = "compress"
    else:
        hold_bars = base_hold
        regime = "base"

    return AdaptiveHoldResult(
        hold_bars=hold_bars,
        score=score,
        components_used=tuple(component_scores.keys()),
        component_scores=component_scores,
        regime=regime,
    )


# ── vectorised helpers for backtester / retrospective runner ─────────


def compute_position_frac_override(
    features: Sequence[Any],
    signals: Sequence[int],
    base_frac: float,
    config: DynamicSizingConfig = DEFAULT_DYNAMIC_SIZING_CONFIG,
) -> list[float | None]:
    """Build a per-signal position_frac override vector.

    This is what `run_v2_backtest(..., position_frac_override=...)`
    consumes. The vector has one entry per bar; non-signal bars are
    None (meaning "use the default"), signal bars carry
    `base_frac * compute_sizing_multiplier(...).multiplier`.

    Args:
        features: Sequence of duck-typed feature rows.
        signals: Same-length {+1, 0, -1} signal vector.
        base_frac: the cell's default position_frac (e.g., 1.333 for
            D1_long, 1.000 for C_long).
        config: sizing configuration.

    Returns:
        list of `float | None` with len(features) entries.

    Parity guarantee:
        For every bar with signal != 0,
        `out[i] == base_frac * compute_sizing_multiplier(features[i],
        signals[i], config).multiplier`.
        This is enforced by the test suite so backtest and live
        monitor cannot drift.
    """
    if len(features) != len(signals):
        raise ValueError(
            f"features length {len(features)} != signals length {len(signals)}"
        )
    if base_frac < 0:
        raise ValueError(f"base_frac must be >= 0, got {base_frac}")

    out: list[float | None] = [None] * len(features)
    for i, (f, s) in enumerate(zip(features, signals)):
        if s == 0:
            continue
        result = compute_sizing_multiplier(f, s, config)
        out[i] = base_frac * result.multiplier
    return out


def compute_hold_bars_override_vector(
    features: Sequence[Any],
    signals: Sequence[int],
    base_hold: int,
    config: AdaptiveHoldConfig = DEFAULT_ADAPTIVE_HOLD_CONFIG,
) -> list[int | None]:
    """Build a per-signal hold_bars override vector.

    This is what `run_v2_backtest(..., hold_bars_override=...)`
    consumes. Non-signal bars are None; signal bars carry the
    adaptive-hold output.

    Args:
        features: Sequence of duck-typed feature rows.
        signals: Same-length {+1, 0, -1} signal vector.
        base_hold: the cell's default hold_bars.
        config: adaptive-hold configuration.

    Returns:
        list of `int | None` with len(features) entries.

    Parity guarantee:
        For every bar with signal != 0,
        `out[i] == compute_hold_override(features[i], signals[i],
        base_hold, config).hold_bars`.
    """
    if len(features) != len(signals):
        raise ValueError(
            f"features length {len(features)} != signals length {len(signals)}"
        )

    out: list[int | None] = [None] * len(features)
    for i, (f, s) in enumerate(zip(features, signals)):
        if s == 0:
            continue
        result = compute_hold_override(f, s, base_hold, config)
        out[i] = result.hold_bars
    return out
