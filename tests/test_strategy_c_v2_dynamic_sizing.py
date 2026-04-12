"""Tests for the shared dynamic sizing + adaptive hold logic.

These tests are the **parity anchor** for Phase 8. The backtester, the
retrospective paper runner, and the live monitor all import from
`strategy_c_v2_dynamic_sizing`. If any of them drift, the parity test
in `tests/test_strategy_c_v2_parity.py` will catch it — but the unit
tests here pin the formula values so the drift can only ever happen
by editing this file and the default config simultaneously (which is
a very visible change).

The default config values MUST produce:
    - D1_long_dynamic:          +164.32% OOS (vs fixed +143.45%, +20.87 pp)
    - D1_long_dynamic_adaptive: +204.55% OOS (vs fixed +143.45%, +61.10 pp)
    - C_long_dynamic:           +135.97% OOS (vs fixed +106.26%, +29.71 pp)

Any change to the numeric constants below is a breaking change to
those numbers — do not merge without re-running the walk-forward.
"""
from __future__ import annotations

import pytest

from strategies.strategy_c_v2_dynamic_sizing import (
    DEFAULT_ADAPTIVE_HOLD_CONFIG,
    DEFAULT_DYNAMIC_SIZING_CONFIG,
    AdaptiveHoldConfig,
    AdaptiveHoldResult,
    DynamicSizingConfig,
    DynamicSizingResult,
    compute_hold_bars_override_vector,
    compute_hold_override,
    compute_position_frac_override,
    compute_sizing_multiplier,
)


# ── helper: minimal feature stub ────────────────────────────────────


class _Feat:
    """Duck-typed feature row carrying only sizing/hold-relevant fields."""

    def __init__(
        self,
        *,
        rsi_14: float | None = None,
        ema_50: float | None = None,
        ema_200: float | None = None,
        funding_rate: float | None = None,
        rv_4h: float | None = None,
    ) -> None:
        self.rsi_14 = rsi_14
        self.ema_50 = ema_50
        self.ema_200 = ema_200
        self.funding_rate = funding_rate
        self.rv_4h = rv_4h


# ── default config invariants (THE FROZEN NUMBERS) ──────────────────


def test_default_sizing_config_freezes_canonical_values() -> None:
    """Pins the exact numeric constants that produced canonical Phase 8 OOS.

    Any change to these constants breaks the canonical baseline.
    If this test fails, either revert your edit or re-run the
    walk-forward and update `strategy_c_v2_canonical_baseline`.
    """
    c = DEFAULT_DYNAMIC_SIZING_CONFIG
    assert c.components == (
        "rsi_extremity",
        "trend_alignment",
        "funding_favorable",
        "rv_mid_band",
    )
    assert c.rsi_field == "rsi_14"
    assert c.ema_fast_field == "ema_50"
    assert c.ema_slow_field == "ema_200"
    assert c.funding_field == "funding_rate"
    assert c.rv_field == "rv_4h"
    assert c.rsi_long_threshold == 70.0
    assert c.rsi_short_threshold == 30.0
    assert c.rsi_extremity_span == 20.0
    assert c.funding_long_favorable_max == 0.0003
    assert c.funding_long_marginal_max == 0.0008
    assert c.funding_short_favorable_min == -0.0003
    assert c.funding_short_marginal_min == -0.0008
    assert c.rv_min == 0.005
    assert c.rv_max == 0.020
    assert c.multiplier_min == 0.5
    assert c.multiplier_max == 1.5


def test_default_adaptive_config_freezes_canonical_values() -> None:
    """Pins the exact numeric constants for adaptive hold."""
    c = DEFAULT_ADAPTIVE_HOLD_CONFIG
    assert c.components == (
        "trend_alignment",
        "rsi_extremity",
        "funding_tailwind",
    )
    assert c.rsi_long_extremity == 78.0
    assert c.rsi_short_extremity == 22.0
    assert c.funding_long_tailwind_max == 0.0002
    assert c.funding_short_tailwind_min == -0.0002
    assert c.extend_factor == 1.5
    assert c.compress_factor == 0.5
    assert c.max_hold_cap == 20
    assert c.min_hold_floor == 2
    assert c.extend_threshold == 2
    assert c.compress_threshold == 0


# ── dynamic sizing — single snapshot ────────────────────────────────


def test_sizing_side_zero_returns_neutral_multiplier() -> None:
    feat = _Feat(rsi_14=75.0, ema_50=100, ema_200=90, funding_rate=0.0001, rv_4h=0.01)
    r = compute_sizing_multiplier(feat, 0)
    assert r.multiplier == 1.0
    assert r.components_used == ()


def test_sizing_all_components_max_gives_multiplier_max() -> None:
    """All 4 components scored 1.0 → multiplier = 1.5 (max).

    Long at rsi=90 (beyond the 70+20=90 extremity), ema_50>ema_200,
    funding≤favorable_max, rv in mid band.
    """
    feat = _Feat(
        rsi_14=90.0,            # extremity (90-70)/20 = 1.0
        ema_50=100,
        ema_200=90,              # trend aligned with long
        funding_rate=0.0001,     # ≤ 0.0003 → favorable 1.0
        rv_4h=0.01,              # ∈ (0.005, 0.020) → mid band 1.0
    )
    r = compute_sizing_multiplier(feat, +1)
    assert r.raw_avg_score == 1.0
    assert r.multiplier == 1.5
    assert set(r.components_used) == {
        "rsi_extremity",
        "trend_alignment",
        "funding_favorable",
        "rv_mid_band",
    }


def test_sizing_all_components_min_gives_multiplier_min() -> None:
    """All 4 components scored 0.0 → multiplier = 0.5 (min)."""
    feat = _Feat(
        rsi_14=70.0,            # extremity (70-70)/20 = 0.0 (at threshold)
        ema_50=90,
        ema_200=100,             # trend opposed to long
        funding_rate=0.002,      # > 0.0008 marginal → 0.0
        rv_4h=0.001,             # < 0.005 → out of band 0.0
    )
    r = compute_sizing_multiplier(feat, +1)
    assert r.raw_avg_score == 0.0
    assert r.multiplier == 0.5


def test_sizing_no_components_readable_returns_neutral() -> None:
    """If all feature fields are None, return multiplier=1.0 (neutral).

    This matches the behaviour of run_manual_edge_sweep's original
    compute_sizing_score_and_override which returned base_frac
    (i.e., multiplier=1.0) when n_components == 0.
    """
    feat = _Feat()  # all None
    r = compute_sizing_multiplier(feat, +1)
    assert r.multiplier == 1.0
    assert r.raw_avg_score == 1.0
    assert r.components_used == ()


def test_sizing_rsi_extremity_long_formula() -> None:
    """RSI extremity for long = (rsi - 70) / 20, clamped to [0, 1]."""
    # Half extremity → score 0.5
    feat = _Feat(rsi_14=80.0)  # (80-70)/20 = 0.5
    r = compute_sizing_multiplier(feat, +1)
    assert r.component_scores["rsi_extremity"] == 0.5
    assert r.raw_avg_score == 0.5
    assert r.multiplier == 1.0

    # Over the cap → clamped to 1.0
    feat = _Feat(rsi_14=95.0)  # (95-70)/20 = 1.25 → clamp 1.0
    r = compute_sizing_multiplier(feat, +1)
    assert r.component_scores["rsi_extremity"] == 1.0

    # Below threshold → clamped to 0
    feat = _Feat(rsi_14=60.0)  # (60-70)/20 = -0.5 → clamp 0.0
    r = compute_sizing_multiplier(feat, +1)
    assert r.component_scores["rsi_extremity"] == 0.0


def test_sizing_rsi_extremity_short_formula() -> None:
    """RSI extremity for short = (30 - rsi) / 20, clamped to [0, 1]."""
    feat = _Feat(rsi_14=20.0)  # (30-20)/20 = 0.5
    r = compute_sizing_multiplier(feat, -1)
    assert r.component_scores["rsi_extremity"] == 0.5

    feat = _Feat(rsi_14=5.0)  # (30-5)/20 = 1.25 → 1.0
    r = compute_sizing_multiplier(feat, -1)
    assert r.component_scores["rsi_extremity"] == 1.0


def test_sizing_funding_three_band_long() -> None:
    """Long funding bands: ≤0.0003 → 1, ≤0.0008 → 0.5, else 0."""
    # Favorable
    r = compute_sizing_multiplier(_Feat(funding_rate=0.0001), +1)
    assert r.component_scores["funding_favorable"] == 1.0
    # Marginal
    r = compute_sizing_multiplier(_Feat(funding_rate=0.0005), +1)
    assert r.component_scores["funding_favorable"] == 0.5
    # Hostile
    r = compute_sizing_multiplier(_Feat(funding_rate=0.002), +1)
    assert r.component_scores["funding_favorable"] == 0.0


def test_sizing_funding_three_band_short() -> None:
    """Short funding bands: ≥-0.0003 → 1, ≥-0.0008 → 0.5, else 0."""
    r = compute_sizing_multiplier(_Feat(funding_rate=0.0), -1)
    assert r.component_scores["funding_favorable"] == 1.0
    r = compute_sizing_multiplier(_Feat(funding_rate=-0.0005), -1)
    assert r.component_scores["funding_favorable"] == 0.5
    r = compute_sizing_multiplier(_Feat(funding_rate=-0.002), -1)
    assert r.component_scores["funding_favorable"] == 0.0


def test_sizing_trend_alignment_long() -> None:
    # Bull — ema_50 > ema_200, long aligned
    r = compute_sizing_multiplier(_Feat(ema_50=100, ema_200=90), +1)
    assert r.component_scores["trend_alignment"] == 1.0
    # Bear — ema_50 < ema_200, long opposed
    r = compute_sizing_multiplier(_Feat(ema_50=90, ema_200=100), +1)
    assert r.component_scores["trend_alignment"] == 0.0


def test_sizing_rv_mid_band() -> None:
    # In band
    r = compute_sizing_multiplier(_Feat(rv_4h=0.010), +1)
    assert r.component_scores["rv_mid_band"] == 1.0
    # Below band
    r = compute_sizing_multiplier(_Feat(rv_4h=0.003), +1)
    assert r.component_scores["rv_mid_band"] == 0.0
    # Above band
    r = compute_sizing_multiplier(_Feat(rv_4h=0.030), +1)
    assert r.component_scores["rv_mid_band"] == 0.0
    # At boundary → open interval, so 0.005 is OUT and 0.020 is OUT
    r = compute_sizing_multiplier(_Feat(rv_4h=0.005), +1)
    assert r.component_scores["rv_mid_band"] == 0.0
    r = compute_sizing_multiplier(_Feat(rv_4h=0.020), +1)
    assert r.component_scores["rv_mid_band"] == 0.0


def test_sizing_partial_components_averaged() -> None:
    """With 2 of 4 fields readable, multiplier averages those 2."""
    feat = _Feat(rsi_14=90.0, ema_50=100, ema_200=90)  # only 2 components
    r = compute_sizing_multiplier(feat, +1)
    assert r.component_scores["rsi_extremity"] == 1.0
    assert r.component_scores["trend_alignment"] == 1.0
    assert r.raw_avg_score == 1.0
    assert r.multiplier == 1.5
    assert len(r.components_used) == 2


# ── adaptive hold — single snapshot ─────────────────────────────────


def test_adaptive_side_zero_returns_base_hold() -> None:
    feat = _Feat(rsi_14=90, ema_50=100, ema_200=90, funding_rate=0)
    r = compute_hold_override(feat, 0, base_hold=11)
    assert r.hold_bars == 11
    assert r.regime == "base"


def test_adaptive_all_three_components_triggers_extend() -> None:
    """score ≥ 2 → extend_factor * base, capped at max_hold_cap."""
    feat = _Feat(
        rsi_14=80.0,       # > 78 extremity
        ema_50=100,
        ema_200=90,         # aligned
        funding_rate=0.0001,  # < 0.0002 tailwind
    )
    r = compute_hold_override(feat, +1, base_hold=11)
    assert r.score == 3
    assert r.regime == "extend"
    assert r.hold_bars == int(11 * 1.5)  # 16


def test_adaptive_extend_capped_at_max() -> None:
    feat = _Feat(rsi_14=85, ema_50=100, ema_200=90, funding_rate=0.0001)
    r = compute_hold_override(feat, +1, base_hold=20)  # 1.5 × 20 = 30 → cap 20
    assert r.hold_bars == 20


def test_adaptive_score_one_returns_base() -> None:
    """score == 1 → base_hold unchanged."""
    feat = _Feat(
        rsi_14=72.0,        # below 78 extremity → 0
        ema_50=100,
        ema_200=90,          # aligned → 1
        funding_rate=0.001,  # > 0.0002 tailwind → 0
    )
    r = compute_hold_override(feat, +1, base_hold=11)
    assert r.score == 1
    assert r.regime == "base"
    assert r.hold_bars == 11


def test_adaptive_score_zero_compresses() -> None:
    """score == 0 → compress_factor * base, floored at min_hold_floor."""
    feat = _Feat(
        rsi_14=72.0,         # < 78
        ema_50=90,
        ema_200=100,          # opposed
        funding_rate=0.001,  # > tailwind
    )
    r = compute_hold_override(feat, +1, base_hold=11)
    assert r.score == 0
    assert r.regime == "compress"
    assert r.hold_bars == int(11 * 0.5)  # 5


def test_adaptive_compress_floored_at_min() -> None:
    feat = _Feat(rsi_14=72, ema_50=90, ema_200=100, funding_rate=0.001)
    r = compute_hold_override(feat, +1, base_hold=3)  # 0.5 × 3 = 1 → floor 2
    assert r.hold_bars == 2


def test_adaptive_no_components_readable_is_score_zero() -> None:
    """All fields None → score 0 → compress."""
    feat = _Feat()
    r = compute_hold_override(feat, +1, base_hold=11)
    assert r.score == 0
    assert r.hold_bars == int(11 * 0.5)
    assert r.regime == "compress"


def test_adaptive_short_side_uses_mirrored_thresholds() -> None:
    feat = _Feat(
        rsi_14=20.0,          # < 22 extremity
        ema_50=90,
        ema_200=100,           # aligned with short
        funding_rate=0.0,     # > -0.0002 tailwind
    )
    r = compute_hold_override(feat, -1, base_hold=11)
    assert r.score == 3
    assert r.hold_bars == int(11 * 1.5)


def test_adaptive_raises_on_invalid_base_hold() -> None:
    with pytest.raises(ValueError, match="base_hold must be >= 1"):
        compute_hold_override(_Feat(), +1, base_hold=0)


# ── vectorised helpers ──────────────────────────────────────────────


def test_vector_position_frac_override_matches_single_snapshot() -> None:
    """The vectorised call MUST equal the single-snapshot call per bar.

    This is the parity property: backtester uses the vectorised helper,
    live monitor uses the single-snapshot helper, and they must agree.
    """
    features = [
        _Feat(rsi_14=90, ema_50=100, ema_200=90, funding_rate=0, rv_4h=0.01),   # signal
        _Feat(rsi_14=50, ema_50=100, ema_200=90, funding_rate=0, rv_4h=0.01),   # no signal
        _Feat(rsi_14=75, ema_50=90, ema_200=100, funding_rate=0.001, rv_4h=0.03),  # signal
    ]
    signals = [1, 0, 1]
    base_frac = 1.333

    vec = compute_position_frac_override(features, signals, base_frac)

    assert vec[0] == pytest.approx(
        base_frac * compute_sizing_multiplier(features[0], 1).multiplier
    )
    assert vec[1] is None
    assert vec[2] == pytest.approx(
        base_frac * compute_sizing_multiplier(features[2], 1).multiplier
    )


def test_vector_hold_override_matches_single_snapshot() -> None:
    features = [
        _Feat(rsi_14=80, ema_50=100, ema_200=90, funding_rate=0.0001),  # signal, extend
        _Feat(rsi_14=50, ema_50=100, ema_200=90, funding_rate=0),        # no signal
        _Feat(rsi_14=72, ema_50=100, ema_200=90, funding_rate=0),        # signal, base
    ]
    signals = [1, 0, 1]
    base_hold = 11

    vec = compute_hold_bars_override_vector(features, signals, base_hold)

    assert vec[0] == compute_hold_override(features[0], 1, base_hold).hold_bars
    assert vec[1] is None
    assert vec[2] == compute_hold_override(features[2], 1, base_hold).hold_bars


def test_vector_raises_on_length_mismatch() -> None:
    features = [_Feat(), _Feat()]
    signals = [1]
    with pytest.raises(ValueError, match="features length"):
        compute_position_frac_override(features, signals, 1.0)
    with pytest.raises(ValueError, match="features length"):
        compute_hold_bars_override_vector(features, signals, 10)


def test_vector_frac_override_rejects_negative_base_frac() -> None:
    with pytest.raises(ValueError, match="base_frac must be >= 0"):
        compute_position_frac_override([], [], -0.5)


# ── config validation ──────────────────────────────────────────────


def test_sizing_config_rejects_bad_multiplier_range() -> None:
    with pytest.raises(ValueError, match="multiplier_min"):
        DynamicSizingConfig(multiplier_min=2.0, multiplier_max=1.0)


def test_sizing_config_rejects_negative_multiplier_min() -> None:
    with pytest.raises(ValueError, match="multiplier_min must be >= 0"):
        DynamicSizingConfig(multiplier_min=-0.1)


def test_sizing_config_rejects_unknown_component() -> None:
    with pytest.raises(ValueError, match="unknown sizing component"):
        DynamicSizingConfig(components=("bogus",))


def test_adaptive_config_rejects_bad_factors() -> None:
    with pytest.raises(ValueError, match="extend_factor"):
        AdaptiveHoldConfig(extend_factor=0.3, compress_factor=0.5)


def test_adaptive_config_rejects_unknown_component() -> None:
    with pytest.raises(ValueError, match="unknown adaptive-hold component"):
        AdaptiveHoldConfig(components=("bogus",))
