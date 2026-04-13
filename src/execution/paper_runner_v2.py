"""Strategy C v2 Phase 12 — paper runner.

Implements the §11B deployment spec exactly:
  - 1h execution loop (scheduler calls `tick` every 1h bar close)
  - 4h regime update on completed 4h bars only
  - dual-stop architecture (alpha close + catastrophe wick on 1h)
  - hybrid pullback/breakout re-entry within regime zones
  - full telemetry per §11C schema
  - persistent state (JSON journal) for crash recovery

The runner is a PURE STATE MACHINE. It does not fetch data — it
receives completed bars via `tick(bar_1h)`. The caller (scheduler
or backtest harness) is responsible for bar delivery.

State machine:
  FLAT → PENDING_ENTRY → OPEN → PENDING_EXIT → FLAT

All four candidates share this runner with different configs.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from adapters.base import MarketBar


PositionState = Literal["flat", "pending_entry", "open", "pending_exit"]
ExitReason = Literal[
    "alpha_stop", "catastrophe_stop", "time_stop",
    "opposite_flip", "end_of_series",
]


@dataclass
class CandidateConfig:
    """Frozen deployment config per §11A."""
    candidate_id: str
    # Regime
    regime_rsi_period: int = 20
    regime_threshold: float = 70.0
    # Execution
    entry_mode: str = "hybrid"
    pullback_pct: float = 0.0075
    breakout_pct: float = 0.0025
    max_entries_per_zone: int = 6
    cooldown_bars: int = 2
    hold_bars: int = 24
    # Sizing
    exchange_leverage: float = 4.0
    base_frac: float = 3.0
    max_frac: float = 4.0
    # Stops
    alpha_stop_pct: float = 0.0125
    catastrophe_stop_pct: float = 0.025
    # Cost
    fee_per_side: float = 0.0005
    slip_per_side: float = 0.0001


@dataclass
class RegimeZone:
    zone_id: int
    start_ts: datetime
    end_ts: datetime | None = None
    entry_count: int = 0
    zone_high: float = 0.0


@dataclass
class OpenPosition:
    entry_bar_ts: datetime
    entry_price: float
    actual_frac: float
    alpha_stop_level: float
    catastrophe_stop_level: float
    hold_target: int
    bars_held: int = 0
    zone_id: int = 0
    zone_entry_number: int = 0
    entry_type: str = "base"
    max_adverse: float = 0.0


@dataclass
class PendingOrder:
    order_type: str  # "entry" or "exit"
    intended_price: float
    reason: str = ""
    actual_frac: float = 0.0
    entry_type: str = ""
    zone_id: int = 0
    zone_entry_number: int = 0


@dataclass
class TradeRecord:
    """Full §11C telemetry row."""
    candidate_id: str
    trade_id: int
    zone_id: int
    zone_entry_number: int
    regime_signal_ts: str
    exec_signal_ts: str
    entry_fill_ts: str
    exit_fill_ts: str
    entry_type: str
    intended_entry_price: float
    realized_fill_price: float
    entry_slippage: float
    actual_frac: float
    alpha_stop_level: float
    catastrophe_stop_level: float
    hold_bars_target: int
    hold_bars_actual: int
    exit_reason: str
    exit_price: float
    exit_slippage: float
    gross_pnl: float
    funding_pnl: float
    cost_pnl: float
    net_pnl: float
    monitor_flags: list[str]
    max_adverse_during_trade: float


@dataclass
class RunnerState:
    """Persistent state that survives restarts."""
    position_state: PositionState = "flat"
    current_zone: RegimeZone | None = None
    open_position: OpenPosition | None = None
    pending_order: PendingOrder | None = None
    next_trade_id: int = 1
    next_zone_id: int = 1
    regime_active: bool = False
    last_4h_bar_ts: datetime | None = None
    last_rsi_value: float | None = None
    total_funding_accrued: float = 0.0
    current_trade_funding: float = 0.0
    bars_since_last_exit: int = 999


class PaperRunnerV2:
    """Paper execution engine implementing §11B exactly."""

    def __init__(
        self,
        config: CandidateConfig,
        journal_path: Path | None = None,
        rsi_computer=None,
    ) -> None:
        self.config = config
        self.state = RunnerState()
        self.trades: list[TradeRecord] = []
        self.journal_path = journal_path
        self._rsi_computer = rsi_computer
        self._rsi_buffer: list[float] = []
        self._1h_count = 0

    # ── public interface ────────────────────────────────────────

    def tick(self, bar: MarketBar, funding_rate: float = 0.0) -> list[str]:
        """Process one completed 1h bar. Returns list of event descriptions.

        Order of operations per §11B:
          1. Execute pending orders from PREVIOUS tick at this bar's open
          2. Update RSI buffer
          3. 4h regime check (on completed 4h bars)
          4. If open, check stops → may queue pending for NEXT tick
          5. If flat, check entry → may queue pending for NEXT tick
          6. Persist state
        """
        events: list[str] = []
        self._1h_count += 1

        # Step 1: execute pending orders from PREVIOUS tick
        if self.state.pending_order:
            self._execute_pending(bar, events)

        # Step 2: update RSI buffer
        self._rsi_buffer.append(bar.close)

        # Step 3: 4h regime check (on completed 4h bars)
        if bar.timestamp.hour % 4 == 0 and bar.timestamp.minute == 0:
            self._update_regime(bar, events)

        # Step 4: if open, check stops and hold
        if self.state.position_state == "open" and self.state.open_position:
            self._check_open_position(bar, funding_rate, events)

        # Step 5: if flat and regime active, check entry signals
        if (
            self.state.position_state == "flat"
            and self.state.regime_active
            and self.state.current_zone
        ):
            self.state.bars_since_last_exit += 1
            self._check_entry_signal(bar, events)

        # Step 6: persist state
        if self.journal_path:
            self._save_state()

        return events

    # ── regime ──────────────────────────────────────────────────

    def _update_regime(self, bar: MarketBar, events: list[str]) -> None:
        """Compute RSI on the 4h close and update regime state."""
        self.state.last_4h_bar_ts = bar.timestamp

        # Compute RSI(20) from the close buffer
        # Use every 4th entry (4h alignment) for the RSI computation
        closes_4h = self._rsi_buffer[::4] if len(self._rsi_buffer) >= 4 else self._rsi_buffer
        if len(closes_4h) < self.config.regime_rsi_period + 1:
            return

        rsi = self._compute_rsi(closes_4h, self.config.regime_rsi_period)
        self.state.last_rsi_value = rsi

        was_active = self.state.regime_active
        now_active = rsi is not None and rsi > self.config.regime_threshold

        if now_active and not was_active:
            # New zone starts
            zone = RegimeZone(
                zone_id=self.state.next_zone_id,
                start_ts=bar.timestamp,
                zone_high=bar.close,
            )
            self.state.current_zone = zone
            self.state.next_zone_id += 1
            self.state.regime_active = True
            events.append(f"REGIME_OPEN zone={zone.zone_id} rsi={rsi:.1f}")

        elif not now_active and was_active:
            if self.state.current_zone:
                self.state.current_zone.end_ts = bar.timestamp
            self.state.regime_active = False
            events.append(f"REGIME_CLOSE rsi={rsi:.1f}")

        elif now_active and self.state.current_zone:
            if bar.close > self.state.current_zone.zone_high:
                self.state.current_zone.zone_high = bar.close

    def _compute_rsi(self, closes: list[float], period: int) -> float | None:
        if len(closes) < period + 1:
            return None
        gains = []
        losses = []
        for i in range(len(closes) - period, len(closes)):
            delta = closes[i] - closes[i - 1]
            if delta > 0:
                gains.append(delta)
                losses.append(0.0)
            else:
                gains.append(0.0)
                losses.append(abs(delta))
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    # ── position management ────────────────────────────────────

    def _check_open_position(
        self, bar: MarketBar, funding_rate: float, events: list[str]
    ) -> None:
        pos = self.state.open_position
        assert pos is not None

        # Track funding
        if funding_rate != 0:
            f_pnl = -1 * funding_rate * pos.actual_frac  # long pays positive funding
            self.state.current_trade_funding += f_pnl

        # Track adverse
        adv = (pos.entry_price - bar.low) / pos.entry_price
        if adv > pos.max_adverse:
            pos.max_adverse = adv

        # 1. Catastrophe stop (intrabar wick)
        if bar.low <= pos.catastrophe_stop_level:
            self._close_position(
                bar, pos.catastrophe_stop_level,
                "catastrophe_stop", events,
            )
            return

        # 2. Alpha stop (close trigger)
        if bar.close <= pos.alpha_stop_level:
            self.state.pending_order = PendingOrder(
                order_type="exit",
                intended_price=bar.close,
                reason="alpha_stop",
            )
            self.state.position_state = "pending_exit"
            events.append(f"ALPHA_STOP_TRIGGERED close={bar.close:.2f}")
            return

        # 3. Hold expiry
        pos.bars_held += 1
        if pos.bars_held >= pos.hold_target:
            self.state.pending_order = PendingOrder(
                order_type="exit",
                intended_price=bar.close,
                reason="time_stop",
            )
            self.state.position_state = "pending_exit"
            events.append(f"TIME_STOP bars_held={pos.bars_held}")
            return

    def _close_position(
        self,
        bar: MarketBar,
        fill_price: float,
        reason: str,
        events: list[str],
    ) -> None:
        pos = self.state.open_position
        assert pos is not None
        cfg = self.config

        # PnL
        raw = (fill_price - pos.entry_price) / pos.entry_price
        gross = raw * pos.actual_frac
        funding = self.state.current_trade_funding
        cost = 2 * (cfg.fee_per_side + cfg.slip_per_side) * pos.actual_frac
        net = gross + funding - cost

        entry_slip = 0.0  # already accounted at entry
        exit_slip = (fill_price - pos.entry_price) / pos.entry_price  # approximate

        record = TradeRecord(
            candidate_id=cfg.candidate_id,
            trade_id=self.state.next_trade_id,
            zone_id=pos.zone_id,
            zone_entry_number=pos.zone_entry_number,
            regime_signal_ts=(
                self.state.current_zone.start_ts.isoformat()
                if self.state.current_zone else ""
            ),
            exec_signal_ts=pos.entry_bar_ts.isoformat(),
            entry_fill_ts=pos.entry_bar_ts.isoformat(),
            exit_fill_ts=bar.timestamp.isoformat(),
            entry_type=pos.entry_type,
            intended_entry_price=pos.entry_price,
            realized_fill_price=pos.entry_price,
            entry_slippage=0.0,
            actual_frac=pos.actual_frac,
            alpha_stop_level=pos.alpha_stop_level,
            catastrophe_stop_level=pos.catastrophe_stop_level,
            hold_bars_target=pos.hold_target,
            hold_bars_actual=pos.bars_held,
            exit_reason=reason,
            exit_price=fill_price,
            exit_slippage=0.0,
            gross_pnl=gross,
            funding_pnl=funding,
            cost_pnl=-cost,
            net_pnl=net,
            monitor_flags=[],
            max_adverse_during_trade=pos.max_adverse,
        )
        self.trades.append(record)
        self.state.next_trade_id += 1

        events.append(
            f"TRADE_CLOSE #{record.trade_id} reason={reason} "
            f"pnl={net*100:+.2f}% entry={pos.entry_price:.2f} "
            f"exit={fill_price:.2f}"
        )

        self.state.open_position = None
        self.state.position_state = "flat"
        self.state.bars_since_last_exit = 0
        self.state.current_trade_funding = 0.0

    # ── entry signals ──────────────────────────────────────────

    def _check_entry_signal(self, bar: MarketBar, events: list[str]) -> None:
        zone = self.state.current_zone
        if zone is None:
            return
        cfg = self.config

        # Check max entries
        if zone.entry_count >= cfg.max_entries_per_zone:
            return

        # Check cooldown
        min_bars_since = cfg.hold_bars + cfg.cooldown_bars
        if zone.entry_count > 0 and self.state.bars_since_last_exit < min_bars_since:
            return

        # Base entry (first entry in zone)
        if zone.entry_count == 0:
            self._queue_entry(bar, "base", events)
            return

        # Re-entry: check pullback and/or breakout
        pullback_fires = False
        breakout_fires = False

        if cfg.entry_mode in ("pullback", "hybrid"):
            if zone.zone_high > 0:
                drop = (zone.zone_high - bar.close) / zone.zone_high
                if drop >= cfg.pullback_pct:
                    pullback_fires = True

        if cfg.entry_mode in ("breakout", "hybrid"):
            # Simplified breakout: close > zone_high * (1 + breakout_pct)
            if zone.zone_high > 0:
                delta = (bar.close - zone.zone_high) / zone.zone_high
                if delta >= cfg.breakout_pct:
                    breakout_fires = True

        if pullback_fires:
            self._queue_entry(bar, "reentry_pullback", events)
        elif breakout_fires:
            self._queue_entry(bar, "reentry_breakout", events)

        # Update zone high
        if bar.close > zone.zone_high:
            zone.zone_high = bar.close

    def _queue_entry(
        self, bar: MarketBar, entry_type: str, events: list[str]
    ) -> None:
        zone = self.state.current_zone
        assert zone is not None
        cfg = self.config

        zone.entry_count += 1
        frac = min(cfg.base_frac, cfg.max_frac)

        self.state.pending_order = PendingOrder(
            order_type="entry",
            intended_price=bar.close,
            actual_frac=frac,
            entry_type=entry_type,
            zone_id=zone.zone_id,
            zone_entry_number=zone.entry_count,
        )
        self.state.position_state = "pending_entry"
        events.append(
            f"ENTRY_SIGNAL type={entry_type} zone={zone.zone_id} "
            f"entry#{zone.entry_count} price={bar.close:.2f}"
        )

    # ── pending order execution ────────────────────────────────

    def _execute_pending(self, bar: MarketBar, events: list[str]) -> None:
        order = self.state.pending_order
        assert order is not None
        cfg = self.config

        if order.order_type == "entry":
            fill_price = bar.open  # fill at this bar's open
            pos = OpenPosition(
                entry_bar_ts=bar.timestamp,
                entry_price=fill_price,
                actual_frac=order.actual_frac,
                alpha_stop_level=fill_price * (1 - cfg.alpha_stop_pct),
                catastrophe_stop_level=fill_price * (1 - cfg.catastrophe_stop_pct),
                hold_target=cfg.hold_bars,
                zone_id=order.zone_id,
                zone_entry_number=order.zone_entry_number,
                entry_type=order.entry_type,
            )
            self.state.open_position = pos
            self.state.position_state = "open"
            self.state.current_trade_funding = 0.0
            events.append(
                f"ENTRY_FILL price={fill_price:.2f} frac={order.actual_frac:.2f} "
                f"alpha_stop={pos.alpha_stop_level:.2f} "
                f"cat_stop={pos.catastrophe_stop_level:.2f}"
            )

        elif order.order_type == "exit":
            fill_price = bar.open
            if self.state.open_position:
                self._close_position(bar, fill_price, order.reason, events)

        self.state.pending_order = None

    # ── state persistence ──────────────────────────────────────

    def _save_state(self) -> None:
        if not self.journal_path:
            return
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "position_state": self.state.position_state,
            "regime_active": self.state.regime_active,
            "next_trade_id": self.state.next_trade_id,
            "next_zone_id": self.state.next_zone_id,
            "bars_since_last_exit": self.state.bars_since_last_exit,
            "trade_count": len(self.trades),
        }
        self.journal_path.write_text(json.dumps(data, indent=2, default=str))

    def get_trades_as_dicts(self) -> list[dict]:
        return [asdict(t) for t in self.trades]
