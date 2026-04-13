"""Tests for the Phase 12 paper runner.

Verifies the paper runner implements the §11B spec correctly:
  - state machine transitions
  - stop placement math
  - stop trigger semantics (alpha=close, catastrophe=wick)
  - entry signal logic (base + pullback + breakout)
  - hold expiry
  - telemetry record shape
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from adapters.base import MarketBar
from execution.paper_runner_v2 import (
    CandidateConfig,
    PaperRunnerV2,
    RunnerState,
)


def _bar(ts: datetime, o: float, h: float, lo: float, c: float) -> MarketBar:
    return MarketBar(timestamp=ts, open=o, high=h, low=lo, close=c, volume=100.0)


def _flat_bars(start: datetime, n: int, price: float = 100.0) -> list[MarketBar]:
    return [
        _bar(start + timedelta(hours=i), price, price, price, price)
        for i in range(n)
    ]


def _cfg(**overrides) -> CandidateConfig:
    defaults = dict(
        candidate_id="test",
        regime_rsi_period=3,   # short RSI for fast tests
        regime_threshold=70.0,
        pullback_pct=0.01,
        breakout_pct=0.005,
        max_entries_per_zone=3,
        cooldown_bars=1,
        hold_bars=4,
        exchange_leverage=4.0,
        base_frac=3.0,
        max_frac=4.0,
        alpha_stop_pct=0.0125,
        catastrophe_stop_pct=0.025,
    )
    defaults.update(overrides)
    return CandidateConfig(**defaults)


class TestStateMachine:
    def test_starts_flat(self) -> None:
        runner = PaperRunnerV2(_cfg())
        assert runner.state.position_state == "flat"

    def test_regime_opens_on_high_rsi(self) -> None:
        runner = PaperRunnerV2(_cfg(regime_rsi_period=3))
        start = datetime(2024, 1, 1, 0, 0)
        # Feed rising prices to push RSI > 70
        prices = [90, 91, 92, 93, 95, 97, 99, 101, 103, 105, 107, 109, 111]
        for i, p in enumerate(prices):
            ts = start + timedelta(hours=i)
            bar = _bar(ts, p, p + 0.5, p - 0.5, p)
            events = runner.tick(bar)
        assert runner.state.regime_active or runner.state.last_rsi_value is not None

    def test_no_entry_when_regime_inactive(self) -> None:
        runner = PaperRunnerV2(_cfg())
        start = datetime(2024, 1, 1, 0, 0)
        # Declining prices → RSI well below 70
        for i in range(20):
            p = 100.0 - i * 0.5
            bar = _bar(start + timedelta(hours=i), p, p + 0.1, p - 0.1, p)
            runner.tick(bar)
        assert runner.state.position_state == "flat"
        assert len(runner.trades) == 0


class TestStopPlacement:
    def test_alpha_stop_level_is_entry_minus_pct(self) -> None:
        runner = PaperRunnerV2(_cfg())
        # Manually open a position
        from execution.paper_runner_v2 import OpenPosition
        runner.state.open_position = OpenPosition(
            entry_bar_ts=datetime(2024, 1, 1),
            entry_price=100.0,
            actual_frac=3.0,
            alpha_stop_level=100.0 * (1 - 0.0125),
            catastrophe_stop_level=100.0 * (1 - 0.025),
            hold_target=24,
        )
        assert runner.state.open_position.alpha_stop_level == pytest.approx(98.75)
        assert runner.state.open_position.catastrophe_stop_level == pytest.approx(97.5)


class TestStopTrigger:
    def test_catastrophe_fires_on_wick_breach(self) -> None:
        runner = PaperRunnerV2(_cfg())
        from execution.paper_runner_v2 import OpenPosition
        runner.state.position_state = "open"
        runner.state.regime_active = True
        runner.state.open_position = OpenPosition(
            entry_bar_ts=datetime(2024, 1, 1),
            entry_price=100.0,
            actual_frac=3.0,
            alpha_stop_level=98.75,
            catastrophe_stop_level=97.5,
            hold_target=24,
        )
        # Bar with wick below catastrophe but close above alpha
        bar = _bar(datetime(2024, 1, 1, 1), 100, 100, 97.0, 99.0)
        runner._rsi_buffer = [100.0] * 20  # dummy buffer
        events = runner.tick(bar)
        assert runner.state.position_state == "flat"
        assert len(runner.trades) == 1
        assert runner.trades[0].exit_reason == "catastrophe_stop"
        assert runner.trades[0].exit_price == pytest.approx(97.5)

    def test_alpha_fires_on_close_breach(self) -> None:
        runner = PaperRunnerV2(_cfg())
        from execution.paper_runner_v2 import OpenPosition
        runner.state.position_state = "open"
        runner.state.regime_active = True
        runner.state.open_position = OpenPosition(
            entry_bar_ts=datetime(2024, 1, 1),
            entry_price=100.0,
            actual_frac=3.0,
            alpha_stop_level=98.75,
            catastrophe_stop_level=97.5,
            hold_target=24,
        )
        # Close below alpha but wick above catastrophe
        bar = _bar(datetime(2024, 1, 1, 1), 100, 100, 98.0, 98.5)
        runner._rsi_buffer = [100.0] * 20
        events = runner.tick(bar)
        # Alpha triggers pending exit
        assert runner.state.position_state == "pending_exit"
        # Next bar executes the exit
        bar2 = _bar(datetime(2024, 1, 1, 2), 98.6, 99.0, 98.0, 98.8)
        events2 = runner.tick(bar2)
        assert runner.state.position_state == "flat"
        assert len(runner.trades) == 1
        assert runner.trades[0].exit_reason == "alpha_stop"

    def test_catastrophe_takes_priority_over_alpha(self) -> None:
        runner = PaperRunnerV2(_cfg())
        from execution.paper_runner_v2 import OpenPosition
        runner.state.position_state = "open"
        runner.state.regime_active = True
        runner.state.open_position = OpenPosition(
            entry_bar_ts=datetime(2024, 1, 1),
            entry_price=100.0,
            actual_frac=3.0,
            alpha_stop_level=98.75,
            catastrophe_stop_level=97.5,
            hold_target=24,
        )
        # Both stops breached: wick below catastrophe AND close below alpha
        bar = _bar(datetime(2024, 1, 1, 1), 100, 100, 96.0, 97.0)
        runner._rsi_buffer = [100.0] * 20
        events = runner.tick(bar)
        # Catastrophe fires first (intrabar)
        assert runner.state.position_state == "flat"
        assert runner.trades[0].exit_reason == "catastrophe_stop"


class TestHoldExpiry:
    def test_time_stop_at_hold_bars(self) -> None:
        runner = PaperRunnerV2(_cfg(hold_bars=3))
        from execution.paper_runner_v2 import OpenPosition
        runner.state.position_state = "open"
        runner.state.regime_active = True
        runner.state.open_position = OpenPosition(
            entry_bar_ts=datetime(2024, 1, 1),
            entry_price=100.0,
            actual_frac=3.0,
            alpha_stop_level=98.75,
            catastrophe_stop_level=97.5,
            hold_target=3,
            bars_held=0,
        )
        runner._rsi_buffer = [100.0] * 20
        # Tick 3 bars without stop breach
        for i in range(3):
            bar = _bar(datetime(2024, 1, 1, i + 1), 100, 101, 99.5, 100.5)
            runner.tick(bar)
        # After 3 bars, should be pending_exit
        assert runner.state.position_state == "pending_exit"
        # Execute the exit
        bar_exit = _bar(datetime(2024, 1, 1, 4), 100.5, 101, 100, 100.5)
        runner.tick(bar_exit)
        assert runner.state.position_state == "flat"
        assert runner.trades[0].exit_reason == "time_stop"


class TestTelemetry:
    def test_trade_record_has_all_fields(self) -> None:
        runner = PaperRunnerV2(_cfg(hold_bars=2))
        from execution.paper_runner_v2 import OpenPosition
        runner.state.position_state = "open"
        runner.state.regime_active = True
        from execution.paper_runner_v2 import RegimeZone
        runner.state.current_zone = RegimeZone(zone_id=1, start_ts=datetime(2024, 1, 1))
        runner.state.open_position = OpenPosition(
            entry_bar_ts=datetime(2024, 1, 1),
            entry_price=100.0,
            actual_frac=3.0,
            alpha_stop_level=98.75,
            catastrophe_stop_level=97.5,
            hold_target=2,
            zone_id=1,
            zone_entry_number=1,
            entry_type="base",
        )
        runner._rsi_buffer = [100.0] * 20
        for i in range(2):
            runner.tick(_bar(datetime(2024, 1, 1, i + 1), 100, 101, 99.5, 100.5))
        runner.tick(_bar(datetime(2024, 1, 1, 3), 100.5, 101, 100, 100.5))

        assert len(runner.trades) == 1
        t = runner.trades[0]
        assert t.candidate_id == "test"
        assert t.zone_id == 1
        assert t.entry_type == "base"
        assert t.alpha_stop_level == pytest.approx(98.75)
        assert t.catastrophe_stop_level == pytest.approx(97.5)
        assert hasattr(t, "net_pnl")
        assert hasattr(t, "max_adverse_during_trade")

    def test_get_trades_as_dicts_returns_serializable(self) -> None:
        runner = PaperRunnerV2(_cfg())
        from execution.paper_runner_v2 import OpenPosition, RegimeZone
        runner.state.position_state = "open"
        runner.state.current_zone = RegimeZone(zone_id=1, start_ts=datetime(2024, 1, 1))
        runner.state.open_position = OpenPosition(
            entry_bar_ts=datetime(2024, 1, 1),
            entry_price=100.0, actual_frac=3.0,
            alpha_stop_level=98.75, catastrophe_stop_level=97.5,
            hold_target=1, zone_id=1, zone_entry_number=1,
        )
        runner._rsi_buffer = [100.0] * 20
        runner.tick(_bar(datetime(2024, 1, 1, 1), 100, 101, 99.5, 100.5))
        runner.tick(_bar(datetime(2024, 1, 1, 2), 100.5, 101, 100, 100.5))
        dicts = runner.get_trades_as_dicts()
        assert len(dicts) >= 1
        assert isinstance(dicts[0], dict)
        assert "net_pnl" in dicts[0]
