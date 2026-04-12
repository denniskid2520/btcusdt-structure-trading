"""Tests for Strategy C v2 Phase 4 live monitoring primitives.

The live monitor is a pure state-machine that accepts a feature snapshot
and an optional open-position descriptor, and returns a monitoring status
with:
    - current_regime:    "long_trend" | "short_trend" | "neutral" | "unknown"
    - current_signal:    +1 | 0 | -1
    - hostile_funding:   bool (is funding in a hostile zone for the position?)
    - early_exit_reason: str | None (reason to exit the position now, if any)
    - action:            "enter_long" | "enter_short" | "hold" | "exit"
                         | "stand_aside"

Intended usage pattern:
    Every 4h bar close, the live runner:
      1. fetches the latest bar + computes the feature snapshot
      2. calls `compute_monitor_state(snapshot, open_position)`
      3. acts on `state.action` (enter / hold / exit / stand aside)
    This file defines the pure state computation. Side effects (order
    placement, journaling) live elsewhere.

Important contract: NO re-training happens here. The monitor only
EVALUATES the deployed strategy's rules against fresh data. Any
optimization of thresholds or parameters is a SEPARATE offline process.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from strategies.strategy_c_v2_live_monitor import (
    LivePositionState,
    MonitorConfig,
    MonitorState,
    compute_monitor_state,
)


# ── helper: minimal feature stub ────────────────────────────────────


class _Feat:
    """Duck-typed feature row carrying only the fields the monitor reads."""

    def __init__(
        self,
        *,
        rsi: float | None = None,
        funding_rate: float | None = None,
        funding_cum_24h: float | None = None,
        close: float = 100.0,
        timestamp: datetime = datetime(2024, 1, 1),
    ) -> None:
        self.rsi_21 = rsi  # candidate A uses rsi_21 by default
        self.rsi_14 = rsi
        self.rsi_30 = rsi
        self.funding_rate = funding_rate
        self.funding_cum_24h = funding_cum_24h
        self.close = close
        self.timestamp = timestamp


def _cfg(**overrides) -> MonitorConfig:
    defaults = dict(
        rsi_field="rsi_21",
        rsi_upper=70.0,
        rsi_lower=30.0,
        hostile_long_funding=0.0005,
        hostile_short_funding=-0.0005,
        max_hold_bars=12,
    )
    defaults.update(overrides)
    return MonitorConfig(**defaults)


# ── regime classification ────────────────────────────────────────────


def test_monitor_classifies_long_trend_when_rsi_above_upper() -> None:
    snap = _Feat(rsi=75.0, funding_rate=0.0001)
    state = compute_monitor_state(snap, None, _cfg())
    assert state.current_regime == "long_trend"
    assert state.current_signal == 1
    assert state.action == "enter_long"


def test_monitor_classifies_short_trend_when_rsi_below_lower() -> None:
    snap = _Feat(rsi=25.0, funding_rate=0.0)
    state = compute_monitor_state(snap, None, _cfg())
    assert state.current_regime == "short_trend"
    assert state.current_signal == -1
    assert state.action == "enter_short"


def test_monitor_classifies_neutral_when_rsi_in_midrange() -> None:
    snap = _Feat(rsi=50.0, funding_rate=0.0)
    state = compute_monitor_state(snap, None, _cfg())
    assert state.current_regime == "neutral"
    assert state.current_signal == 0
    assert state.action == "stand_aside"


def test_monitor_unknown_when_rsi_is_none() -> None:
    snap = _Feat(rsi=None, funding_rate=0.0)
    state = compute_monitor_state(snap, None, _cfg())
    assert state.current_regime == "unknown"
    assert state.action == "stand_aside"


# ── hostile funding detection ────────────────────────────────────────


def test_monitor_detects_hostile_funding_for_long_position() -> None:
    snap = _Feat(rsi=75.0, funding_rate=0.001)  # 0.001 > 0.0005
    state = compute_monitor_state(snap, None, _cfg())
    assert state.hostile_funding is True


def test_monitor_no_hostile_funding_when_rate_safe() -> None:
    snap = _Feat(rsi=75.0, funding_rate=0.0001)
    state = compute_monitor_state(snap, None, _cfg())
    assert state.hostile_funding is False


def test_monitor_hostile_funding_ignored_when_none() -> None:
    snap = _Feat(rsi=75.0, funding_rate=None)
    state = compute_monitor_state(snap, None, _cfg())
    assert state.hostile_funding is False


# ── open position: hold / exit decisions ─────────────────────────────


def test_monitor_holds_long_position_when_regime_still_long() -> None:
    snap = _Feat(rsi=72.0, funding_rate=0.0001)
    pos = LivePositionState(
        side="long",
        entry_time=datetime(2024, 1, 1),
        entry_price=100.0,
        bars_held=5,
    )
    state = compute_monitor_state(snap, pos, _cfg())
    assert state.action == "hold"
    assert state.early_exit_reason is None


def test_monitor_exits_long_position_on_opposite_regime_flip() -> None:
    snap = _Feat(rsi=25.0, funding_rate=0.0)  # opposite of long
    pos = LivePositionState(
        side="long",
        entry_time=datetime(2024, 1, 1),
        entry_price=100.0,
        bars_held=5,
    )
    state = compute_monitor_state(snap, pos, _cfg())
    assert state.action == "exit"
    assert state.early_exit_reason == "opposite_signal"


def test_monitor_exits_short_position_on_opposite_regime_flip() -> None:
    snap = _Feat(rsi=75.0, funding_rate=0.0)
    pos = LivePositionState(
        side="short",
        entry_time=datetime(2024, 1, 1),
        entry_price=100.0,
        bars_held=5,
    )
    state = compute_monitor_state(snap, pos, _cfg())
    assert state.action == "exit"
    assert state.early_exit_reason == "opposite_signal"


def test_monitor_exits_on_max_hold_reached() -> None:
    snap = _Feat(rsi=72.0, funding_rate=0.0001)
    pos = LivePositionState(
        side="long",
        entry_time=datetime(2024, 1, 1),
        entry_price=100.0,
        bars_held=12,  # == max_hold_bars
    )
    state = compute_monitor_state(snap, pos, _cfg(max_hold_bars=12))
    assert state.action == "exit"
    assert state.early_exit_reason == "time_stop"


def test_monitor_does_not_exit_on_hostile_funding_alone_for_long() -> None:
    """Per Phase 3 finding: hostile long funding does NOT warrant exit.

    The live monitor flags hostile funding but does not act on it for
    existing longs (Phase 3 funding filter showed long-veto hurts).
    """
    snap = _Feat(rsi=75.0, funding_rate=0.002)  # hostile
    pos = LivePositionState(
        side="long",
        entry_time=datetime(2024, 1, 1),
        entry_price=100.0,
        bars_held=5,
    )
    state = compute_monitor_state(snap, pos, _cfg())
    assert state.hostile_funding is True
    assert state.action == "hold"  # NOT "exit"
    assert state.early_exit_reason is None


def test_monitor_exits_short_on_hostile_funding_for_shorts() -> None:
    """Hostile funding for shorts (very negative) DOES warrant exit.

    Per Phase 3 funding filter finding, short trades in very negative
    funding regimes are where the short-side drawdown lives.
    """
    snap = _Feat(rsi=25.0, funding_rate=-0.001)  # below -0.0005 hostile threshold
    pos = LivePositionState(
        side="short",
        entry_time=datetime(2024, 1, 1),
        entry_price=100.0,
        bars_held=5,
    )
    state = compute_monitor_state(snap, pos, _cfg())
    assert state.hostile_funding is True
    assert state.action == "exit"
    assert state.early_exit_reason == "hostile_funding_short"


# ── new-entry blocking when funding is hostile for the intended side ──


def test_monitor_blocks_new_short_entry_in_hostile_negative_funding() -> None:
    """Opening a new SHORT in hostile negative funding is blocked.

    This implements the Phase 3 short-veto finding — short entries in
    hostile-to-shorts funding regimes should not fire.
    """
    snap = _Feat(rsi=25.0, funding_rate=-0.001)  # short signal + hostile for shorts
    state = compute_monitor_state(snap, None, _cfg())
    # Signal would be -1, but funding vetoes the entry.
    assert state.current_signal == -1
    assert state.hostile_funding is True
    assert state.action == "stand_aside"
    assert state.blocked_reason == "hostile_funding_short_entry"


def test_monitor_allows_new_long_entry_even_in_hostile_funding() -> None:
    """New long entries are NOT blocked by hostile funding (Phase 3 lesson).

    Phase 3 funding filter showed blocking longs in hot funding hurts
    returns. So the monitor permits long entries regardless of funding.
    """
    snap = _Feat(rsi=75.0, funding_rate=0.002)  # hostile to longs, but we allow it
    state = compute_monitor_state(snap, None, _cfg())
    assert state.hostile_funding is True
    assert state.action == "enter_long"
    assert state.blocked_reason is None


# ── sanity ───────────────────────────────────────────────────────────


def test_monitor_state_shape() -> None:
    snap = _Feat(rsi=75.0, funding_rate=0.0001)
    state = compute_monitor_state(snap, None, _cfg())
    # Shape sanity — has all expected attributes
    assert hasattr(state, "current_regime")
    assert hasattr(state, "current_signal")
    assert hasattr(state, "hostile_funding")
    assert hasattr(state, "early_exit_reason")
    assert hasattr(state, "action")
    assert hasattr(state, "blocked_reason")
    # Phase 8 additions
    assert hasattr(state, "actual_frac")
    assert hasattr(state, "sizing_multiplier")
    assert hasattr(state, "sizing_components")
    assert hasattr(state, "hold_bars_override")
    assert hasattr(state, "hold_regime")
    assert hasattr(state, "stop_level")


# ── Phase 8: extended feature stub ──────────────────────────────────


class _RichFeat:
    """Feature stub carrying all sizing + hold + stop fields."""

    def __init__(
        self,
        *,
        rsi: float | None = None,
        rsi_14: float | None = None,
        funding_rate: float | None = None,
        macd_hist: float | None = None,
        ema_50: float | None = None,
        ema_200: float | None = None,
        rv_4h: float | None = None,
        close: float = 100.0,
        timestamp: datetime = datetime(2024, 1, 1),
    ) -> None:
        self.rsi_21 = rsi
        self.rsi_14 = rsi_14 if rsi_14 is not None else rsi
        self.rsi_30 = rsi
        self.funding_rate = funding_rate
        self.macd_hist = macd_hist
        self.ema_50 = ema_50
        self.ema_200 = ema_200
        self.rv_4h = rv_4h
        self.close = close
        self.timestamp = timestamp


# ── Phase 8: MonitorConfig validation ───────────────────────────────


def test_config_rejects_invalid_stop_loss_pct() -> None:
    import pytest
    with pytest.raises(ValueError, match="stop_loss_pct must be in"):
        MonitorConfig(stop_loss_pct=1.5)
    with pytest.raises(ValueError, match="stop_loss_pct must be in"):
        MonitorConfig(stop_loss_pct=-0.1)


def test_config_rejects_unknown_stop_semantics() -> None:
    import pytest
    with pytest.raises(ValueError, match="stop_semantics must be"):
        MonitorConfig(stop_semantics="bogus")  # type: ignore[arg-type]


def test_config_rejects_unknown_signal_family() -> None:
    import pytest
    with pytest.raises(ValueError, match="signal_family must be"):
        MonitorConfig(signal_family="bogus")  # type: ignore[arg-type]


def test_config_rejects_negative_base_frac() -> None:
    import pytest
    with pytest.raises(ValueError, match="base_frac must be >= 0"):
        MonitorConfig(base_frac=-0.1)


# ── Phase 8: rsi_and_macd signal family ─────────────────────────────


def test_monitor_rsi_and_macd_requires_both_confirmations_long() -> None:
    """rsi_and_macd family: RSI > 70 AND macd_hist > 0 → long."""
    feat = _RichFeat(rsi=75.0, macd_hist=0.5, funding_rate=0.0001)
    cfg = MonitorConfig(signal_family="rsi_and_macd", rsi_field="rsi_21")
    state = compute_monitor_state(feat, None, cfg)
    assert state.current_signal == 1
    assert state.action == "enter_long"


def test_monitor_rsi_and_macd_blocks_when_macd_opposite() -> None:
    """RSI > 70 but macd_hist ≤ 0 → no signal."""
    feat = _RichFeat(rsi=75.0, macd_hist=-0.1, funding_rate=0.0001)
    cfg = MonitorConfig(signal_family="rsi_and_macd", rsi_field="rsi_21")
    state = compute_monitor_state(feat, None, cfg)
    assert state.current_signal == 0
    assert state.action == "stand_aside"


def test_monitor_rsi_and_macd_short_requires_both() -> None:
    feat = _RichFeat(rsi=25.0, macd_hist=-0.3, funding_rate=0.0)
    cfg = MonitorConfig(signal_family="rsi_and_macd", rsi_field="rsi_21")
    state = compute_monitor_state(feat, None, cfg)
    assert state.current_signal == -1
    assert state.action == "enter_short"


def test_monitor_rsi_and_macd_none_macd_yields_no_signal() -> None:
    feat = _RichFeat(rsi=75.0, macd_hist=None, funding_rate=0.0001)
    cfg = MonitorConfig(signal_family="rsi_and_macd", rsi_field="rsi_21")
    state = compute_monitor_state(feat, None, cfg)
    assert state.current_signal == 0


# ── Phase 8: stop level computation ─────────────────────────────────


def test_monitor_emits_stop_level_for_long_entry() -> None:
    """When stop_loss_pct > 0, stop_level = close * (1 - pct) for long."""
    feat = _RichFeat(rsi=75.0, funding_rate=0.0, close=100.0)
    cfg = _cfg(stop_loss_pct=0.015)
    state = compute_monitor_state(feat, None, cfg)
    assert state.action == "enter_long"
    assert state.stop_level == pytest.approx(98.5, abs=1e-6)


def test_monitor_emits_stop_level_for_short_entry() -> None:
    """stop_level = close * (1 + pct) for short."""
    feat = _RichFeat(rsi=25.0, funding_rate=0.0, close=100.0)
    cfg = _cfg(stop_loss_pct=0.02)
    state = compute_monitor_state(feat, None, cfg)
    assert state.action == "enter_short"
    assert state.stop_level == pytest.approx(102.0, abs=1e-6)


def test_monitor_no_stop_level_when_stop_loss_pct_zero() -> None:
    feat = _RichFeat(rsi=75.0, funding_rate=0.0, close=100.0)
    cfg = _cfg(stop_loss_pct=0.0)
    state = compute_monitor_state(feat, None, cfg)
    assert state.stop_level is None


def test_monitor_no_stop_level_for_stand_aside() -> None:
    """Non-entry actions carry stop_level = None."""
    feat = _RichFeat(rsi=50.0, funding_rate=0.0)  # neutral → stand_aside
    cfg = _cfg(stop_loss_pct=0.015)
    state = compute_monitor_state(feat, None, cfg)
    assert state.action == "stand_aside"
    assert state.stop_level is None


# ── Phase 8: fixed sizing path ──────────────────────────────────────


def test_monitor_fixed_sizing_reports_base_frac_on_entry() -> None:
    """use_dynamic_sizing=False → actual_frac == base_frac."""
    feat = _RichFeat(rsi=75.0, funding_rate=0.0, close=100.0)
    cfg = _cfg(use_dynamic_sizing=False, base_frac=1.333)
    state = compute_monitor_state(feat, None, cfg)
    assert state.action == "enter_long"
    assert state.actual_frac == pytest.approx(1.333, abs=1e-6)
    assert state.sizing_multiplier == 1.0
    assert state.sizing_components == {}


def test_monitor_stand_aside_reports_zero_actual_frac() -> None:
    feat = _RichFeat(rsi=50.0, funding_rate=0.0)
    cfg = _cfg(base_frac=1.333)
    state = compute_monitor_state(feat, None, cfg)
    assert state.actual_frac == 0.0


# ── Phase 8: dynamic sizing path ────────────────────────────────────


def test_monitor_dynamic_sizing_emits_max_multiplier_on_full_conviction() -> None:
    """All 4 sizing components score 1 → multiplier = 1.5 → actual_frac = 1.5 * base."""
    feat = _RichFeat(
        rsi=75.0,              # triggers signal
        rsi_14=90.0,           # RSI extremity = 1.0
        funding_rate=0.0001,    # favorable long funding = 1.0
        ema_50=100,
        ema_200=90,              # trend aligned
        rv_4h=0.010,             # mid band
        close=100.0,
    )
    cfg = _cfg(
        use_dynamic_sizing=True,
        base_frac=1.333,
        stop_loss_pct=0.015,
    )
    state = compute_monitor_state(feat, None, cfg)
    assert state.action == "enter_long"
    assert state.sizing_multiplier == pytest.approx(1.5, abs=1e-6)
    assert state.actual_frac == pytest.approx(1.333 * 1.5, abs=1e-6)
    assert "rsi_extremity" in state.sizing_components
    assert "trend_alignment" in state.sizing_components
    assert "funding_favorable" in state.sizing_components
    assert "rv_mid_band" in state.sizing_components


def test_monitor_dynamic_sizing_emits_min_multiplier_on_zero_conviction() -> None:
    feat = _RichFeat(
        rsi=75.0,
        rsi_14=70.0,           # extremity 0
        funding_rate=0.002,     # hostile → 0
        ema_50=90,
        ema_200=100,             # opposed → 0
        rv_4h=0.001,             # below mid band → 0
    )
    cfg = _cfg(use_dynamic_sizing=True, base_frac=1.333)
    state = compute_monitor_state(feat, None, cfg)
    assert state.action == "enter_long"
    assert state.sizing_multiplier == pytest.approx(0.5, abs=1e-6)
    assert state.actual_frac == pytest.approx(1.333 * 0.5, abs=1e-6)


# ── Phase 8: adaptive hold path ─────────────────────────────────────


def test_monitor_adaptive_hold_extends_when_score_high() -> None:
    """All 3 components score 1 → extend to 1.5 × base."""
    feat = _RichFeat(
        rsi=75.0,
        rsi_14=80.0,           # > 78 extremity
        ema_50=100,
        ema_200=90,              # aligned
        funding_rate=0.0001,    # < 0.0002 tailwind
        rv_4h=0.010,
        close=100.0,
    )
    cfg = _cfg(
        use_adaptive_hold=True,
        max_hold_bars=11,
        stop_loss_pct=0.015,
    )
    state = compute_monitor_state(feat, None, cfg)
    assert state.action == "enter_long"
    assert state.hold_bars_override == 16  # int(11 * 1.5)
    assert state.hold_regime == "extend"


def test_monitor_adaptive_hold_compresses_when_score_zero() -> None:
    feat = _RichFeat(
        rsi=75.0,
        rsi_14=72.0,
        ema_50=90,
        ema_200=100,             # opposed
        funding_rate=0.001,     # not tailwind
        close=100.0,
    )
    cfg = _cfg(use_adaptive_hold=True, max_hold_bars=11)
    state = compute_monitor_state(feat, None, cfg)
    assert state.hold_bars_override == 5  # int(11 * 0.5)
    assert state.hold_regime == "compress"


def test_monitor_adaptive_hold_off_emits_none_override() -> None:
    feat = _RichFeat(rsi=75.0, funding_rate=0.0, close=100.0)
    cfg = _cfg(use_adaptive_hold=False)
    state = compute_monitor_state(feat, None, cfg)
    assert state.action == "enter_long"
    assert state.hold_bars_override is None
    assert state.hold_regime == "base"


# ── Phase 8: open position preserves entry-time sizing/hold/stop ────


def test_monitor_open_position_reports_entry_time_frac_hold_stop() -> None:
    """When there's an open position, monitor reports the entry-time
    sizing / hold override / stop level from LivePositionState, not
    recomputed from current features.

    This is critical for parity: the backtester records position_frac
    at entry and uses it for the whole trade. The live monitor must
    do the same.
    """
    feat = _RichFeat(rsi=72.0, funding_rate=0.0, close=101.0)  # hold
    pos = LivePositionState(
        side="long",
        entry_time=datetime(2024, 1, 1),
        entry_price=100.0,
        bars_held=3,
        position_frac=1.666,          # entry-time frac (>1 mean non-default)
        max_hold_override=16,          # entry-time adaptive hold
        stop_level=98.5,                # entry-time stop
    )
    cfg = _cfg(use_dynamic_sizing=True, use_adaptive_hold=True)
    state = compute_monitor_state(feat, pos, cfg)
    assert state.action == "hold"
    assert state.actual_frac == pytest.approx(1.666, abs=1e-6)
    assert state.hold_bars_override == 16
    assert state.stop_level == 98.5


def test_monitor_open_position_uses_max_hold_override_for_time_stop() -> None:
    """Adaptive-hold override wins over config.max_hold_bars for the
    time-stop check."""
    feat = _RichFeat(rsi=72.0, funding_rate=0.0)
    pos = LivePositionState(
        side="long",
        entry_time=datetime(2024, 1, 1),
        entry_price=100.0,
        bars_held=5,  # < config.max_hold_bars=12 but ≥ override=5
        position_frac=1.333,
        max_hold_override=5,
    )
    cfg = _cfg(max_hold_bars=12)
    state = compute_monitor_state(feat, pos, cfg)
    assert state.action == "exit"
    assert state.early_exit_reason == "time_stop"


def test_monitor_open_position_no_override_uses_config_max_hold() -> None:
    feat = _RichFeat(rsi=72.0, funding_rate=0.0)
    pos = LivePositionState(
        side="long",
        entry_time=datetime(2024, 1, 1),
        entry_price=100.0,
        bars_held=12,
        position_frac=1.333,
        max_hold_override=None,
    )
    cfg = _cfg(max_hold_bars=12)
    state = compute_monitor_state(feat, pos, cfg)
    assert state.action == "exit"
    assert state.early_exit_reason == "time_stop"
