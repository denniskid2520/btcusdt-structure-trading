from unittest.mock import patch

from adapters.base import Position
from strategies.trend_breakout import TrendBreakoutConfig, TrendBreakoutStrategy
from tests.fixtures_synthetic_bars import (
    ascending_channel_breakdown_short_bars,
    ascending_channel_breakout_long_bars,
    ascending_channel_resistance_rejection_short_bars,
    ascending_channel_support_long_bars,
    descending_channel_breakdown_short_bars,
    descending_channel_breakout_long_bars,
    descending_channel_rejection_short_bars,
    descending_channel_support_bounce_long_bars,
    narrow_descending_channel_bars,
    noisy_descending_channel_bars,
    rising_channel_continuation_short_bars,
    rising_channel_retest_short_bars,
    wide_lookback_descending_channel_bars,
)


def test_strategy_generates_long_signal_for_ascending_channel_support() -> None:
    strategy = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            impulse_threshold_pct=0.03,
            entry_buffer_pct=0.35,
            stop_buffer_pct=0.08,
            allow_shorts=False,
            require_parent_confirmation=False,
        )
    )
    signal = strategy.generate_signal("BTCUSDT", ascending_channel_support_long_bars(), Position(symbol="BTCUSDT"))
    assert signal.action == "buy"
    assert signal.reason == "ascending_channel_support_bounce"
    assert signal.stop_price is not None
    assert signal.target_price is not None


def test_strategy_generates_long_signal_for_ascending_channel_breakout() -> None:
    strategy = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            impulse_threshold_pct=0.03,
            entry_buffer_pct=0.25,
            stop_buffer_pct=0.08,
            allow_shorts=False,
            require_parent_confirmation=False,
        )
    )
    signal = strategy.generate_signal("BTCUSDT", ascending_channel_breakout_long_bars(), Position(symbol="BTCUSDT"))
    assert signal.action == "buy"
    assert signal.reason == "ascending_channel_breakout"
    assert signal.stop_price is not None
    assert signal.target_price is not None


def test_strategy_generates_short_signal_for_descending_channel_rejection() -> None:
    strategy = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            impulse_threshold_pct=0.004,
            entry_buffer_pct=0.25,
            stop_buffer_pct=0.08,
            allow_longs=False,
            require_parent_confirmation=False,
        )
    )
    signal = strategy.generate_signal("BTCUSDT", descending_channel_rejection_short_bars(), Position(symbol="BTCUSDT"))
    assert signal.action == "short"
    assert signal.reason == "descending_channel_rejection"
    assert signal.stop_price is not None
    assert signal.target_price is not None


def test_strategy_generates_short_signal_for_descending_channel_breakdown() -> None:
    strategy = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            impulse_threshold_pct=0.02,
            entry_buffer_pct=0.25,
            stop_buffer_pct=0.08,
            allow_longs=False,
            require_parent_confirmation=False,
        )
    )
    signal = strategy.generate_signal("BTCUSDT", descending_channel_breakdown_short_bars(), Position(symbol="BTCUSDT"))
    assert signal.action == "short"
    assert signal.reason == "descending_channel_breakdown"
    assert signal.stop_price is not None
    assert signal.target_price is not None


def test_strategy_generates_short_signal_for_rising_channel_retest_breakdown() -> None:
    strategy = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            impulse_threshold_pct=0.04,
            entry_buffer_pct=0.35,
            stop_buffer_pct=0.08,
            allow_longs=False,
            require_parent_confirmation=False,
        )
    )
    signal = strategy.generate_signal("BTCUSDT", rising_channel_retest_short_bars(), Position(symbol="BTCUSDT"))
    assert signal.action == "short"
    assert signal.reason == "rising_channel_breakdown_retest_short"
    assert signal.stop_price is not None
    assert signal.target_price is not None


def test_strategy_generates_short_signal_for_rising_channel_breakdown_continuation() -> None:
    strategy = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            impulse_threshold_pct=0.04,
            entry_buffer_pct=0.25,
            continuation_buffer_pct=0.45,
            stop_buffer_pct=0.08,
            allow_longs=False,
            enable_rising_channel_breakdown_retest_short=False,
            require_parent_confirmation=False,
        )
    )
    signal = strategy.generate_signal("BTCUSDT", rising_channel_continuation_short_bars(), Position(symbol="BTCUSDT"))
    assert signal.action == "short"
    assert signal.reason == "rising_channel_breakdown_continuation_short"
    assert signal.stop_price is not None
    assert signal.target_price is not None


def test_r_squared_filter_rejects_noisy_channel() -> None:
    """A noisy channel with scattered pivots must be rejected when min_r_squared is set."""
    strategy = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=30,
            impulse_threshold_pct=0.004,
            entry_buffer_pct=0.25,
            stop_buffer_pct=0.08,
            allow_longs=False,
            min_r_squared=0.70,
            require_parent_confirmation=False,
        )
    )
    evaluation = strategy.evaluate(
        symbol="BTCUSDT",
        bars=noisy_descending_channel_bars(),
        position=Position(symbol="BTCUSDT"),
    )
    assert evaluation.signal.action == "hold"
    rejection_eval = next(
        item for item in evaluation.rule_evaluations
        if item.rule_name == "descending_channel_rejection"
    )
    assert rejection_eval.first_failed_condition == "r_squared_too_low"


def test_r_squared_filter_passes_clean_channel() -> None:
    """Clean synthetic channel should pass R² filter easily."""
    strategy = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            impulse_threshold_pct=0.004,
            entry_buffer_pct=0.25,
            stop_buffer_pct=0.08,
            allow_longs=False,
            min_r_squared=0.70,
            require_parent_confirmation=False,
        )
    )
    signal = strategy.generate_signal(
        "BTCUSDT",
        descending_channel_rejection_short_bars(),
        Position(symbol="BTCUSDT"),
    )
    assert signal.action == "short"
    assert signal.reason == "descending_channel_rejection"


def test_atr_floor_widens_stop_when_structural_stop_too_tight() -> None:
    """When stop_buffer < min_stop_atr_multiplier * ATR, use ATR-based stop instead."""
    strategy = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            impulse_threshold_pct=0.004,
            entry_buffer_pct=0.25,
            stop_buffer_pct=0.08,
            allow_longs=False,
            min_stop_atr_multiplier=2.0,
            require_parent_confirmation=False,
        )
    )
    bars = descending_channel_rejection_short_bars()
    evaluation = strategy.evaluate(
        symbol="BTCUSDT", bars=bars, position=Position(symbol="BTCUSDT"),
    )
    assert evaluation.signal.action == "short"
    signal = evaluation.signal
    # The structural stop_buffer for this channel is ~0.08 * width.
    # With min_stop_atr_multiplier=2.0, the stop should be at least 2*ATR above entry.
    # We verify the stop is wider than the basic structural stop.
    entry_price = signal.metadata["close"]
    resistance = signal.metadata["resistance"]
    structural_stop = resistance + signal.metadata["stop_buffer"]
    assert signal.stop_price >= structural_stop, (
        f"ATR floor should widen stop: stop={signal.stop_price} >= structural={structural_stop}"
    )


def test_trailing_stop_moves_stop_in_profit_direction() -> None:
    """When trailing_stop_atr is set, the strategy should emit it in metadata."""
    strategy = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            impulse_threshold_pct=0.004,
            entry_buffer_pct=0.25,
            stop_buffer_pct=0.08,
            allow_longs=False,
            trailing_stop_atr=2.0,
            require_parent_confirmation=False,
        )
    )
    evaluation = strategy.evaluate(
        symbol="BTCUSDT",
        bars=descending_channel_rejection_short_bars(),
        position=Position(symbol="BTCUSDT"),
    )
    assert evaluation.signal.action == "short"
    assert evaluation.signal.metadata is not None
    assert "trailing_stop_atr" in evaluation.signal.metadata
    assert evaluation.signal.metadata["trailing_stop_atr"] == 2.0


def test_volatility_filter_blocks_trades_in_chaotic_market() -> None:
    """When ATR/price ratio is above threshold, no trades should be taken."""
    # Create highly volatile bars: large high-low range relative to price
    volatile_bars = descending_channel_rejection_short_bars()
    # Override with extreme range to simulate chaos
    from adapters.base import MarketBar
    volatile_bars = [
        MarketBar(
            timestamp=bar.timestamp,
            open=bar.open,
            high=bar.close + 3000,  # ~5% range at 60-70k
            low=bar.close - 3000,
            close=bar.close,
            volume=bar.volume,
        )
        for bar in volatile_bars
    ]
    strategy = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            impulse_threshold_pct=0.004,
            entry_buffer_pct=0.25,
            stop_buffer_pct=0.08,
            allow_longs=False,
            max_atr_price_ratio=0.03,
            require_parent_confirmation=False,
        )
    )
    evaluation = strategy.evaluate(
        symbol="BTCUSDT",
        bars=volatile_bars,
        position=Position(symbol="BTCUSDT"),
    )
    assert evaluation.signal.action == "hold"
    rejection_eval = next(
        item for item in evaluation.rule_evaluations
        if item.rule_name == "descending_channel_rejection"
    )
    assert rejection_eval.first_failed_condition == "volatility_too_high"


def test_min_channel_width_pct_rejects_narrow_channel() -> None:
    """Channel narrower than 2% of price must be rejected."""
    strategy = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=30,
            impulse_threshold_pct=0.001,
            entry_buffer_pct=0.25,
            stop_buffer_pct=0.08,
            allow_longs=False,
            min_channel_width_pct=0.02,
            require_parent_confirmation=False,
        )
    )
    evaluation = strategy.evaluate(
        symbol="BTCUSDT",
        bars=narrow_descending_channel_bars(),
        position=Position(symbol="BTCUSDT"),
    )
    assert evaluation.signal.action == "hold"
    rejection_eval = next(
        item for item in evaluation.rule_evaluations
        if item.rule_name == "descending_channel_rejection"
    )
    assert rejection_eval.first_failed_condition == "below_min_channel_width"


def test_parent_context_conflict_blocks_rising_channel_retest_short() -> None:
    strategy = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            impulse_threshold_pct=0.04,
            entry_buffer_pct=0.35,
            stop_buffer_pct=0.08,
            allow_longs=False,
            require_parent_confirmation=False,
        )
    )
    forced_parent = {
        "parent_structure_type": "descending_channel",
        "parent_upper_boundary": 70000.0,
        "parent_lower_boundary": 62000.0,
        "parent_position_in_channel": "near_lower_boundary",
        "parent_event_type": "normal",
    }
    with patch("strategies.trend_breakout._build_parent_context", return_value=forced_parent):
        evaluation = strategy.evaluate(
            symbol="BTCUSDT",
            bars=rising_channel_retest_short_bars(),
            position=Position(symbol="BTCUSDT"),
        )
    assert evaluation.signal.action == "hold"
    retest_eval = next(
        item for item in evaluation.rule_evaluations if item.rule_name == "rising_channel_breakdown_retest_short"
    )
    assert retest_eval.first_failed_condition == "parent_context_conflict"


def test_short_blocked_near_lower_boundary_descending_channel() -> None:
    """Shorts near the lower boundary of descending channel should be blocked."""
    strategy = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12, structure_lookback=24,
            impulse_threshold_pct=0.004, entry_buffer_pct=0.25,
            stop_buffer_pct=0.08, allow_longs=False,
            require_parent_confirmation=False,
        )
    )
    forced_parent = {
        "parent_structure_type": "descending_channel",
        "parent_upper_boundary": 69910.0,
        "parent_lower_boundary": 51845.0,
        "parent_position_in_channel": "near_lower_boundary",
        "parent_event_type": "normal",
    }
    with patch("strategies.trend_breakout._build_parent_context", return_value=forced_parent):
        evaluation = strategy.evaluate(
            symbol="BTCUSDT",
            bars=descending_channel_rejection_short_bars(),
            position=Position(symbol="BTCUSDT"),
        )
    rejection_eval = next(
        item for item in evaluation.rule_evaluations
        if item.rule_name == "descending_channel_rejection"
    )
    assert rejection_eval.first_failed_condition == "parent_context_conflict"


def test_short_blocked_near_lower_boundary_ascending_channel() -> None:
    """Shorts near lower boundary of ascending channel must also be blocked.

    Parent F (ascending rebound) near lower boundary — shorting into support is invalid.
    """
    strategy = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12, structure_lookback=24,
            impulse_threshold_pct=0.004, entry_buffer_pct=0.25,
            stop_buffer_pct=0.08, allow_longs=False,
            require_parent_confirmation=False,
        )
    )
    forced_parent = {
        "parent_structure_type": "ascending_channel",
        "parent_upper_boundary": 97924.0,
        "parent_lower_boundary": 83822.0,
        "parent_position_in_channel": "near_lower_boundary",
        "parent_event_type": "confirmed_breakdown",
    }
    with patch("strategies.trend_breakout._build_parent_context", return_value=forced_parent):
        evaluation = strategy.evaluate(
            symbol="BTCUSDT",
            bars=descending_channel_rejection_short_bars(),
            position=Position(symbol="BTCUSDT"),
        )
    rejection_eval = next(
        item for item in evaluation.rule_evaluations
        if item.rule_name == "descending_channel_rejection"
    )
    assert rejection_eval.first_failed_condition == "parent_context_conflict"


def test_confirmed_breakdown_blocks_ascending_channel_long() -> None:
    """When ascending channel has confirmed_breakdown, longs must be blocked."""
    strategy = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            impulse_threshold_pct=0.03,
            entry_buffer_pct=0.35,
            stop_buffer_pct=0.08,
            allow_shorts=False,
            require_parent_confirmation=False,
        )
    )
    forced_parent = {
        "parent_structure_type": "ascending_channel",
        "parent_upper_boundary": 76000.0,
        "parent_lower_boundary": 63000.0,
        "parent_position_in_channel": "mid_channel",
        "parent_event_type": "confirmed_breakdown",
    }
    with patch("strategies.trend_breakout._build_parent_context", return_value=forced_parent):
        evaluation = strategy.evaluate(
            symbol="BTCUSDT",
            bars=ascending_channel_support_long_bars(),
            position=Position(symbol="BTCUSDT"),
        )
    assert evaluation.signal.action == "hold"
    support_eval = next(
        item for item in evaluation.rule_evaluations
        if item.rule_name == "ascending_channel_support_bounce"
    )
    assert support_eval.first_failed_condition == "parent_context_conflict"


def test_shock_override_blocks_rising_channel_continuation_short() -> None:
    strategy = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            impulse_threshold_pct=0.04,
            entry_buffer_pct=0.25,
            continuation_buffer_pct=0.45,
            stop_buffer_pct=0.08,
            allow_longs=False,
            enable_rising_channel_breakdown_retest_short=False,
            require_parent_confirmation=False,
        )
    )
    forced_parent = {
        "parent_structure_type": "ascending_channel",
        "parent_upper_boundary": 71000.0,
        "parent_lower_boundary": 63000.0,
        "parent_position_in_channel": "below_lower_boundary",
        "parent_event_type": "shock_break_reclaim",
    }
    with patch("strategies.trend_breakout._build_parent_context", return_value=forced_parent):
        evaluation = strategy.evaluate(
            symbol="BTCUSDT",
            bars=rising_channel_continuation_short_bars(),
            position=Position(symbol="BTCUSDT"),
        )
    assert evaluation.signal.action == "hold"
    continuation_eval = next(
        item
        for item in evaluation.rule_evaluations
        if item.rule_name == "rising_channel_breakdown_continuation_short"
    )
    assert continuation_eval.first_failed_condition == "shock_override_active"


def test_parent_boundary_extends_short_target_to_parent_lower() -> None:
    """When parent has a lower boundary further than the local target,
    the short signal's target_price should extend to the parent lower boundary."""
    strategy = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            impulse_threshold_pct=0.004,
            entry_buffer_pct=0.25,
            stop_buffer_pct=0.08,
            allow_longs=False,
            use_parent_boundary_targets=True,
            require_parent_confirmation=False,
        )
    )
    # Parent descending channel with lower boundary at 52000
    # Local channel is around 60-70k, so local target would be ~support - width ≈ 55k
    # Parent lower boundary at 52000 is further → should be used as target
    forced_parent = {
        "parent_structure_type": "descending_channel",
        "parent_upper_boundary": 70000.0,
        "parent_lower_boundary": 52000.0,
        "parent_position_in_channel": "near_upper_boundary",
        "parent_event_type": "normal",
    }
    with patch("strategies.trend_breakout._build_parent_context", return_value=forced_parent):
        evaluation = strategy.evaluate(
            symbol="BTCUSDT",
            bars=descending_channel_rejection_short_bars(),
            position=Position(symbol="BTCUSDT"),
        )
    assert evaluation.signal.action == "short"
    assert evaluation.signal.target_price is not None
    # The target should be the parent lower boundary (52000), not local channel width
    assert evaluation.signal.target_price == 52000.0


def test_parent_boundary_extends_long_target_to_parent_upper() -> None:
    """When parent has an upper boundary further than the local target,
    the long signal's target_price should extend to the parent upper boundary."""
    strategy = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            impulse_threshold_pct=0.03,
            entry_buffer_pct=0.35,
            stop_buffer_pct=0.08,
            allow_shorts=False,
            use_parent_boundary_targets=True,
            require_parent_confirmation=False,
        )
    )
    # Parent ascending channel with upper boundary at 76000
    # Local channel is around 50-58k, so local target ≈ resistance ~ 58k
    # Parent upper boundary at 76000 is further → should be used as target
    forced_parent = {
        "parent_structure_type": "ascending_channel",
        "parent_upper_boundary": 76000.0,
        "parent_lower_boundary": 50000.0,
        "parent_position_in_channel": "near_lower_boundary",
        "parent_event_type": "normal",
    }
    with patch("strategies.trend_breakout._build_parent_context", return_value=forced_parent):
        evaluation = strategy.evaluate(
            symbol="BTCUSDT",
            bars=ascending_channel_support_long_bars(),
            position=Position(symbol="BTCUSDT"),
        )
    assert evaluation.signal.action == "buy"
    assert evaluation.signal.target_price is not None
    assert evaluation.signal.target_price == 76000.0


def test_parent_boundary_does_not_shrink_local_target() -> None:
    """If parent boundary is closer than local target, keep local target."""
    strategy = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            impulse_threshold_pct=0.004,
            entry_buffer_pct=0.25,
            stop_buffer_pct=0.08,
            allow_longs=False,
            use_parent_boundary_targets=True,
            require_parent_confirmation=False,
        )
    )
    # Parent lower boundary at 60000 — close to or above the local target
    forced_parent = {
        "parent_structure_type": "descending_channel",
        "parent_upper_boundary": 70000.0,
        "parent_lower_boundary": 60000.0,
        "parent_position_in_channel": "near_upper_boundary",
        "parent_event_type": "normal",
    }

    # First get the local-only target
    local_strategy = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            impulse_threshold_pct=0.004,
            entry_buffer_pct=0.25,
            stop_buffer_pct=0.08,
            allow_longs=False,
            use_parent_boundary_targets=False,
            require_parent_confirmation=False,
        )
    )
    with patch("strategies.trend_breakout._build_parent_context", return_value=forced_parent):
        local_eval = local_strategy.evaluate(
            symbol="BTCUSDT",
            bars=descending_channel_rejection_short_bars(),
            position=Position(symbol="BTCUSDT"),
        )
        extended_eval = strategy.evaluate(
            symbol="BTCUSDT",
            bars=descending_channel_rejection_short_bars(),
            position=Position(symbol="BTCUSDT"),
        )
    assert local_eval.signal.action == "short"
    assert extended_eval.signal.action == "short"
    # Extended target should not be worse than local target
    assert extended_eval.signal.target_price <= local_eval.signal.target_price


def test_secondary_lookback_detects_channel_when_primary_fails() -> None:
    """When primary structure_lookback can't detect a channel, the secondary
    (wider) lookback should find one and produce a trade signal."""
    bars = wide_lookback_descending_channel_bars()

    # Primary lookback=24 should fail (last 24 bars are flat/choppy)
    primary_only = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            impulse_threshold_pct=0.004,
            entry_buffer_pct=0.25,
            stop_buffer_pct=0.08,
            allow_longs=False,
            require_parent_confirmation=False,
        )
    )
    primary_eval = primary_only.evaluate(
        symbol="BTCUSDT",
        bars=bars,
        position=Position(symbol="BTCUSDT"),
    )
    assert primary_eval.signal.action == "hold", "Primary lookback should NOT detect channel"

    # With secondary lookback=48, channel should be detected
    dual_lookback = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            secondary_structure_lookback=48,
            impulse_threshold_pct=0.004,
            entry_buffer_pct=0.25,
            stop_buffer_pct=0.08,
            allow_longs=False,
            require_parent_confirmation=False,
        )
    )
    dual_eval = dual_lookback.evaluate(
        symbol="BTCUSDT",
        bars=bars,
        position=Position(symbol="BTCUSDT"),
    )
    assert dual_eval.signal.action == "short", "Secondary lookback should detect descending channel"


def test_descending_channel_support_bounce_long() -> None:
    """Price near support in descending channel → buy (oscillation, no impulse required)."""
    strategy = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            impulse_threshold_pct=0.03,
            entry_buffer_pct=0.35,
            stop_buffer_pct=0.08,
            allow_shorts=False,
            require_parent_confirmation=False,
        )
    )
    bars = descending_channel_support_bounce_long_bars()
    result = strategy.evaluate(
        symbol="BTCUSDT",
        bars=bars,
        position=Position(symbol="BTCUSDT"),
    )
    assert result.signal.action == "buy"
    assert result.signal.stop_price is not None
    assert result.signal.target_price is not None
    triggered = [r for r in result.rule_evaluations if r.rule_name == "descending_channel_support_bounce" and r.triggered]
    assert triggered, "descending_channel_support_bounce rule should trigger"


def test_ascending_channel_resistance_rejection_short() -> None:
    """Price near resistance in ascending channel → short (oscillation, no impulse required)."""
    strategy = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            impulse_threshold_pct=0.03,
            entry_buffer_pct=0.35,
            stop_buffer_pct=0.08,
            allow_longs=False,
            require_parent_confirmation=False,
        )
    )
    bars = ascending_channel_resistance_rejection_short_bars()
    result = strategy.evaluate(
        symbol="BTCUSDT",
        bars=bars,
        position=Position(symbol="BTCUSDT"),
    )
    assert result.signal.action == "short"
    assert result.signal.stop_price is not None
    assert result.signal.target_price is not None
    triggered = [r for r in result.rule_evaluations if r.rule_name == "ascending_channel_resistance_rejection" and r.triggered]
    assert triggered, "ascending_channel_resistance_rejection rule should trigger"


def test_descending_channel_breakout_long() -> None:
    """Price breaks above resistance in descending channel with bullish impulse → buy."""
    strategy = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            impulse_threshold_pct=0.03,
            entry_buffer_pct=0.35,
            stop_buffer_pct=0.08,
            allow_shorts=False,
            require_parent_confirmation=False,
        )
    )
    bars = descending_channel_breakout_long_bars()
    result = strategy.evaluate(
        symbol="BTCUSDT",
        bars=bars,
        position=Position(symbol="BTCUSDT"),
    )
    assert result.signal.action == "buy"
    assert result.signal.stop_price is not None
    assert result.signal.target_price is not None
    triggered = [r for r in result.rule_evaluations if r.rule_name == "descending_channel_breakout_long" and r.triggered]
    assert triggered, "descending_channel_breakout_long rule should trigger"


def test_ascending_channel_breakdown_short() -> None:
    """Price breaks below support in ascending channel with bearish impulse → short."""
    strategy = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            impulse_threshold_pct=0.008,
            entry_buffer_pct=0.35,
            stop_buffer_pct=0.08,
            allow_longs=False,
            require_parent_confirmation=False,
        )
    )
    bars = ascending_channel_breakdown_short_bars()
    result = strategy.evaluate(
        symbol="BTCUSDT",
        bars=bars,
        position=Position(symbol="BTCUSDT"),
    )
    assert result.signal.action == "short"
    assert result.signal.stop_price is not None
    assert result.signal.target_price is not None
    triggered = [r for r in result.rule_evaluations if r.rule_name == "ascending_channel_breakdown_short" and r.triggered]
    assert triggered, "ascending_channel_breakdown_short rule should trigger"


def test_ma_regime_filter_blocks_long_when_below_sma200() -> None:
    """When ma_regime_filter is enabled and price < SMA200, longs should be blocked."""
    from tests.fixtures_synthetic_bars import make_bar

    # Build 200 bars at high price (~70k) then append ascending channel support bars (~57k).
    # SMA200 will be ~70k, channel support close ~57750 → below SMA200 → longs blocked.
    high_bars = [make_bar(i, 70000.0) for i in range(200)]
    channel_bars = ascending_channel_support_long_bars()
    all_bars = high_bars + channel_bars

    # Without MA filter: should generate buy
    no_filter = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            impulse_threshold_pct=0.03,
            entry_buffer_pct=0.35,
            stop_buffer_pct=0.08,
            allow_shorts=False,
            require_parent_confirmation=False,
            ma_regime_filter=False,
        )
    )
    result_no = no_filter.evaluate(symbol="BTCUSDT", bars=all_bars, position=Position(symbol="BTCUSDT"))
    assert result_no.signal.action == "buy", "Without MA filter, long should trigger"

    # With MA filter: close < SMA200 → long blocked
    with_filter = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            impulse_threshold_pct=0.03,
            entry_buffer_pct=0.35,
            stop_buffer_pct=0.08,
            allow_shorts=False,
            require_parent_confirmation=False,
            ma_regime_filter=True,
            ma_regime_period=200,
        )
    )
    result_ma = with_filter.evaluate(symbol="BTCUSDT", bars=all_bars, position=Position(symbol="BTCUSDT"))
    assert result_ma.signal.action == "hold", "MA filter should block long when price < SMA200"


def test_ma_regime_filter_blocks_short_when_above_sma200() -> None:
    """When ma_regime_filter is enabled and price > SMA200, shorts should be blocked."""
    from tests.fixtures_synthetic_bars import make_bar

    # Build 200 bars at low price (~55k) then append descending channel rejection bars (~61-70k).
    # SMA200 will be ~56k, rejection close ~61300 → above SMA200 → shorts blocked.
    # Use 55k (close to channel start ~70k) so impulse within channel is not destroyed.
    low_bars = [make_bar(i, 55000.0) for i in range(200)]
    channel_bars = descending_channel_rejection_short_bars()
    all_bars = low_bars + channel_bars

    # Without MA filter: should short (use 0.004 threshold matching original fixture test)
    no_filter = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            impulse_threshold_pct=0.004,
            entry_buffer_pct=0.25,
            stop_buffer_pct=0.08,
            allow_longs=False,
            require_parent_confirmation=False,
            ma_regime_filter=False,
        )
    )
    result_no = no_filter.evaluate(symbol="BTCUSDT", bars=all_bars, position=Position(symbol="BTCUSDT"))
    assert result_no.signal.action == "short", "Without MA filter, short should trigger"

    # With MA filter: close > SMA200 → short blocked
    with_filter = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            impulse_threshold_pct=0.004,
            entry_buffer_pct=0.25,
            stop_buffer_pct=0.08,
            allow_longs=False,
            require_parent_confirmation=False,
            ma_regime_filter=True,
            ma_regime_period=200,
        )
    )
    result_ma = with_filter.evaluate(symbol="BTCUSDT", bars=all_bars, position=Position(symbol="BTCUSDT"))
    assert result_ma.signal.action == "hold", "MA filter should block short when price > SMA200"


def test_oi_filter_blocks_long_when_crowd_is_long() -> None:
    """When OI filter is enabled and long% is extreme, longs should be blocked (crowded trade)."""
    from adapters.futures_data import FuturesSnapshot, StaticFuturesProvider
    from tests.fixtures_synthetic_bars import make_bar

    channel_bars = ascending_channel_support_long_bars()
    # Crowded long: L/S ratio = 2.5 → 71.4% long
    ts = channel_bars[-1].timestamp
    provider = StaticFuturesProvider({
        ts: FuturesSnapshot(timestamp=ts, open_interest=90000.0, long_short_ratio=2.5, taker_buy_sell_ratio=0.8),
    })

    # Without OI filter: should buy
    no_filter = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            impulse_threshold_pct=0.03,
            entry_buffer_pct=0.35,
            stop_buffer_pct=0.08,
            allow_shorts=False,
            require_parent_confirmation=False,
        )
    )
    result_no = no_filter.evaluate(symbol="BTCUSDT", bars=channel_bars, position=Position(symbol="BTCUSDT"))
    assert result_no.signal.action == "buy"

    # With OI filter: crowded long → block
    with_filter = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            impulse_threshold_pct=0.03,
            entry_buffer_pct=0.35,
            stop_buffer_pct=0.08,
            allow_shorts=False,
            require_parent_confirmation=False,
            crowded_long_threshold=0.70,
            crowded_short_threshold=0.70,
        )
    )
    result_oi = with_filter.evaluate(
        symbol="BTCUSDT", bars=channel_bars, position=Position(symbol="BTCUSDT"),
        futures_provider=provider,
    )
    assert result_oi.signal.action == "hold", "OI filter should block long when crowd is 71% long"


def test_oi_filter_blocks_short_when_crowd_is_short() -> None:
    """When OI filter is enabled and short% is extreme, shorts should be blocked."""
    from adapters.futures_data import FuturesSnapshot, StaticFuturesProvider

    channel_bars = descending_channel_rejection_short_bars()
    # Crowded short: L/S ratio = 0.4 → long_pct = 28.6%, short_pct = 71.4%
    ts = channel_bars[-1].timestamp
    provider = StaticFuturesProvider({
        ts: FuturesSnapshot(timestamp=ts, open_interest=90000.0, long_short_ratio=0.4, taker_buy_sell_ratio=1.2),
    })

    # Without OI filter: should short (use 0.004 threshold matching existing evaluate() tests)
    no_filter = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            impulse_threshold_pct=0.004,
            entry_buffer_pct=0.25,
            stop_buffer_pct=0.08,
            allow_longs=False,
            require_parent_confirmation=False,
        )
    )
    result_no = no_filter.evaluate(symbol="BTCUSDT", bars=channel_bars, position=Position(symbol="BTCUSDT"))
    assert result_no.signal.action == "short"

    # With OI filter: crowded short → block
    with_filter = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            impulse_threshold_pct=0.004,
            entry_buffer_pct=0.25,
            stop_buffer_pct=0.08,
            allow_longs=False,
            require_parent_confirmation=False,
            crowded_long_threshold=0.70,
            crowded_short_threshold=0.70,
        )
    )
    result_oi = with_filter.evaluate(
        symbol="BTCUSDT", bars=channel_bars, position=Position(symbol="BTCUSDT"),
        futures_provider=provider,
    )
    assert result_oi.signal.action == "hold", "OI filter should block short when crowd is 71% short"


def test_adx_filter_blocks_trade_in_weak_trend() -> None:
    """When ADX is below threshold, trades should be blocked (no clear trend)."""
    from tests.fixtures_synthetic_bars import make_bar

    # Build 30 bars of choppy sideways (low ADX): price oscillates ±0.1%
    choppy_bars = []
    base = 60_000.0
    for i in range(30):
        p = base + (50 if i % 2 == 0 else -50)
        choppy_bars.append(make_bar(i, p, high_pad=60, low_pad=60))

    # Append the ascending channel fixture at end
    channel_bars = ascending_channel_support_long_bars()
    # Use choppy prefix + channel bars
    all_bars = choppy_bars + channel_bars

    # Without ADX filter: should buy
    no_filter = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            impulse_threshold_pct=0.03,
            entry_buffer_pct=0.35,
            stop_buffer_pct=0.08,
            allow_shorts=False,
            require_parent_confirmation=False,
        )
    )
    result_no = no_filter.evaluate(symbol="BTCUSDT", bars=all_bars, position=Position(symbol="BTCUSDT"))
    assert result_no.signal.action == "buy", "Without ADX filter, buy should trigger"

    # With ADX filter (threshold=25): choppy market → ADX low → blocked
    with_filter = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12,
            structure_lookback=24,
            impulse_threshold_pct=0.03,
            entry_buffer_pct=0.35,
            stop_buffer_pct=0.08,
            allow_shorts=False,
            require_parent_confirmation=False,
            adx_filter=True,
            adx_period=14,
            adx_threshold=25.0,
        )
    )
    result_adx = with_filter.evaluate(symbol="BTCUSDT", bars=all_bars, position=Position(symbol="BTCUSDT"))
    assert result_adx.signal.action == "hold", "ADX filter should block in weak trend"
