"""Strategy C v2 Phase 8C/9 — D1 execution-layer for trade-count lift.

Architecture:
    4h D1 RSI(20) > 70 = long permission zone (the REGIME GATE).
    1h or 15m bars within the zone = execution resolution.
    Pullback / breakout / hybrid re-entry logic generates ADDITIONAL
    entries within the same zone, each as a new independent trade.

Phase 9 extensions:
    - hybrid mode (pullback + breakout): both entry types fire in the
      same zone, whichever triggers first after cooldown
    - re-entry-after-stop: allow re-entry after alpha/catastrophe stop
      exits (not just after time-stop)
    - hold in hours: configurable hold_hours, converted to bars at
      runtime based on the execution timeframe
    - 15m support: execution bars can be 1h or 15m

No independent alpha family. No pyramiding.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Sequence

from adapters.base import MarketBar
from data.strategy_c_v2_features import rsi_series
from research.strategy_c_v2_backtest import (
    V2BacktestResult,
    V2Trade,
    run_v2_backtest,
)


EntryMode = Literal["pullback", "breakout", "hybrid"]


@dataclass(frozen=True)
class ExecLayerConfig:
    """Configuration for the execution-layer re-entry logic."""
    entry_type: str              # "pullback", "breakout", or "hybrid"
    pullback_pct: float           # pullback drop threshold (used in pullback/hybrid)
    breakout_pct: float           # breakout delta threshold (used in breakout/hybrid)
    max_entries_per_zone: int     # including the base entry
    cooldown_bars: int            # min exec-tf bars between entries in same zone
    hold_hours: int               # hold duration in hours
    alpha_stop_pct: float
    catastrophe_stop_pct: float
    reentry_after_alpha_stop: bool = True
    reentry_after_cat_stop: bool = True
    exec_tf_hours: float = 1.0    # 1.0 for 1h, 0.25 for 15m

    # Backward compat shims
    @property
    def threshold_pct(self) -> float:
        return self.pullback_pct

    @property
    def hold_4h_equiv(self) -> int:
        return max(1, round(self.hold_hours / 4.0))

    @property
    def cooldown_1h_bars(self) -> int:
        return self.cooldown_bars

    @property
    def hold_exec_bars(self) -> int:
        """Hold in execution-timeframe bars."""
        return max(1, round(self.hold_hours / self.exec_tf_hours))


@dataclass(frozen=True)
class ExecLayerResult:
    """Result of the execution-layer backtest."""
    trades: list[V2Trade]
    equity_curve: list[float]
    num_base_entries: int
    num_reentries: int
    num_zones_used: int


def _identify_regime_zones(
    bars_4h: Sequence[MarketBar],
    features_4h: Sequence,
) -> list[tuple[datetime, datetime, int, int]]:
    """Identify (start_time, end_time, bar_lo, bar_hi) of RSI(20) > 70 zones."""
    closes = [f.close for f in features_4h]
    rsi20 = rsi_series(closes, 20)
    zones: list[tuple[datetime, datetime, int, int]] = []
    in_zone = False
    zone_start_idx = -1
    for i, r in enumerate(rsi20):
        if r is not None and r > 70:
            if not in_zone:
                zone_start_idx = i
                in_zone = True
        else:
            if in_zone:
                zones.append((
                    bars_4h[zone_start_idx].timestamp,
                    bars_4h[i].timestamp,
                    zone_start_idx, i,
                ))
                in_zone = False
    if in_zone:
        zones.append((
            bars_4h[zone_start_idx].timestamp,
            bars_4h[-1].timestamp + timedelta(hours=4),
            zone_start_idx, len(bars_4h),
        ))
    return zones


def _generate_signals_in_zone(
    bars_exec: Sequence[MarketBar],
    zone_start_ts: datetime,
    zone_end_ts: datetime,
    config: ExecLayerConfig,
) -> list[tuple[int, str]]:
    """Generate (exec_bar_index, entry_type) pairs within one zone.

    Phase 9 extensions:
      - hybrid mode: check pullback AND breakout on every bar
      - re-entry-after-stop: the hold window check only blocks
        re-entry during the hold period; after any exit (including
        stops) re-entry is allowed if cooldown is satisfied
      - hold_exec_bars: hold in exec-tf units
    """
    ts_list = [b.timestamp for b in bars_exec]
    lo = bisect_left(ts_list, zone_start_ts)
    hi = bisect_right(ts_list, zone_end_ts)
    if hi <= lo:
        return []

    entries: list[tuple[int, str]] = []
    zone_high = 0.0
    last_entry_bar = -999
    hold_bars = config.hold_exec_bars

    for i in range(lo, hi):
        bar = bars_exec[i]
        if bar.close > zone_high:
            zone_high = bar.close

        bars_since_last = i - last_entry_bar
        at_max = len(entries) >= config.max_entries_per_zone
        in_hold = bars_since_last < hold_bars

        if at_max:
            continue
        if in_hold:
            continue

        # Base entry: first bar of the zone
        if len(entries) == 0:
            entries.append((i, "base"))
            last_entry_bar = i
            zone_high = bar.close
            continue

        in_cooldown = bars_since_last < (hold_bars + config.cooldown_bars)
        if in_cooldown:
            continue

        # Re-entry: check pullback and/or breakout
        pullback_fires = False
        breakout_fires = False

        if config.entry_type in ("pullback", "hybrid"):
            if zone_high > 0:
                drop = (zone_high - bar.close) / zone_high
                if drop >= config.pullback_pct:
                    pullback_fires = True

        if config.entry_type in ("breakout", "hybrid"):
            lookback = min(8, i - lo)
            prev_high = 0.0
            for j in range(max(lo, i - lookback), i):
                if bars_exec[j].high > prev_high:
                    prev_high = bars_exec[j].high
            if prev_high > 0:
                delta = (bar.close - prev_high) / prev_high
                if delta >= config.breakout_pct:
                    breakout_fires = True

        if pullback_fires:
            entries.append((i, "reentry_pullback"))
            last_entry_bar = i
        elif breakout_fires:
            entries.append((i, "reentry_breakout"))
            last_entry_bar = i

    return entries


def run_execution_layer_backtest(
    *,
    bars_4h: Sequence[MarketBar],
    features_4h: Sequence,
    bars_1h: Sequence[MarketBar] | None = None,
    bars_15m: Sequence[MarketBar] | None = None,
    funding_1h: Sequence[float] | None = None,
    funding_15m: Sequence[float] | None = None,
    config: ExecLayerConfig,
    position_frac: float,
    # Legacy compat: if bars_1h is passed but not bars_15m, use 1h
) -> ExecLayerResult:
    """Run the full execution-layer backtest."""
    # Select execution bars based on config
    if config.exec_tf_hours <= 0.25 and bars_15m is not None:
        bars_exec = bars_15m
        funding_exec = funding_15m or [0.0] * len(bars_15m)
    elif bars_1h is not None:
        bars_exec = bars_1h
        funding_exec = funding_1h or [0.0] * len(bars_1h)
    else:
        raise ValueError("Must provide bars_1h or bars_15m")

    zones = _identify_regime_zones(bars_4h, features_4h)

    n_exec = len(bars_exec)
    signals_exec = [0] * n_exec
    num_base = 0
    num_reentry = 0
    num_zones_used = 0

    for zone_start_ts, zone_end_ts, _, _ in zones:
        entries = _generate_signals_in_zone(
            bars_exec, zone_start_ts, zone_end_ts, config
        )
        if entries:
            num_zones_used += 1
        for idx, entry_type in entries:
            if 0 <= idx < n_exec:
                signals_exec[idx] = 1
                if entry_type == "base":
                    num_base += 1
                else:
                    num_reentry += 1

    frac_override = [None] * n_exec
    for i, s in enumerate(signals_exec):
        if s != 0:
            frac_override[i] = position_frac

    hold_exec = config.hold_exec_bars
    result = run_v2_backtest(
        bars=bars_exec,
        signals=signals_exec,
        funding_per_bar=funding_exec,
        hold_bars=hold_exec,
        fee_per_side=0.0005,
        slip_per_side=0.0001,
        alpha_stop_pct=config.alpha_stop_pct,
        catastrophe_stop_pct=config.catastrophe_stop_pct,
        effective_leverage=position_frac,
        position_frac_override=frac_override,
    )

    return ExecLayerResult(
        trades=result.trades,
        equity_curve=result.equity_curve,
        num_base_entries=num_base,
        num_reentries=num_reentry,
        num_zones_used=num_zones_used,
    )
