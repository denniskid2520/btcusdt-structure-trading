"""Strategy C — precision-first event study tools.

Before we can design Baseline B, we need to measure whether liquidation-driven
events even have an edge. This module gives us the three primitives to do so:

    find_events(feats, side, z_threshold) -> list[Event]
        Scan a feature series for bars whose long_liq_z32 (side=+1) or
        short_liq_z32 (side=-1) crossed a threshold. None z-scores (warmup)
        are skipped.

    measure_forward_returns(feats, events, horizons, fee_per_side, slippage_per_side)
        For each event, simulate "enter at bar[i+1].open, exit at bar[i+1+h].open"
        with round-trip cost subtraction. Long events → raw ret; short events →
        sign-flipped raw ret. Events whose longest horizon runs past the last
        bar are dropped.

    bucket_events(results, feats_by_idx, key_fn, horizon, cost)
        Group EventResults by a user-provided key function on the trigger bar,
        and summarise each bucket: count, avg, median, win_rate.

The three primitives compose: you find events, measure their forward returns
with realistic cost, then slice them by any feature of interest (flow sign,
basis sign, funding regime, ...) to see which slices carry net-of-cost edge.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Mapping, Sequence

from data.strategy_c_features import StrategyCFeatureBar


# ── Dataclasses ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Event:
    """A single liquidation-spike trigger at a feature bar."""

    index: int          # position in the feats list
    timestamp: datetime
    side: int           # +1 long event, -1 short event
    trigger_z: float    # the z-score that tripped the threshold


@dataclass(frozen=True)
class EventResult:
    """An event plus its cost-adjusted forward returns at each horizon."""

    event: Event
    entry_px: float
    fwd_returns: dict[int, float]   # horizon (bars) -> net return


# ── find_events ────────────────────────────────────────────────────────


def find_events(
    feats: Sequence[StrategyCFeatureBar],
    *,
    side: int,
    z_threshold: float,
) -> list[Event]:
    """Return every bar whose liquidation z-score crossed the threshold.

    Args:
        feats: Feature bar series (ascending timestamps).
        side: +1 to scan long_liq_z32 (long events), -1 to scan short_liq_z32.
        z_threshold: Minimum z-score to count as a trigger (strict >).

    Returns:
        Events sorted by bar index (duplicates-free by construction).

    Raises:
        ValueError: if side is not ±1.
    """
    if side not in (1, -1):
        raise ValueError(f"side must be +1 or -1, got {side}")

    events: list[Event] = []
    for i, f in enumerate(feats):
        z = f.long_liq_z32 if side == 1 else f.short_liq_z32
        if z is None:
            continue
        if z > z_threshold:
            events.append(
                Event(index=i, timestamp=f.timestamp, side=side, trigger_z=z)
            )
    return events


# ── measure_forward_returns ────────────────────────────────────────────


def measure_forward_returns(
    feats: Sequence[StrategyCFeatureBar],
    events: Sequence[Event],
    *,
    horizons: Sequence[int],
    fee_per_side: float = 0.0005,
    slippage_per_side: float = 0.0001,
) -> list[EventResult]:
    """Simulate entry-at-next-open, exit-at-next-open+h for each event.

    Execution model (mirrors the Strategy C backtest):
        - Entry at bar[event.index + 1].open
        - Exit  at bar[event.index + 1 + h].open for each horizon h
        - Raw return is sign-adjusted by event.side (+1 long, -1 short)
        - Round-trip cost = 2 * (fee_per_side + slippage_per_side) is deducted

    Events where the longest horizon runs past the last bar are silently
    dropped — they cannot be evaluated honestly.

    Args:
        feats: Feature bar series.
        events: Event list from find_events.
        horizons: Iterable of integer horizons in bars (e.g. (1, 2, 4)).
        fee_per_side: Per-side taker fee (e.g. 0.0005 = 5 bps).
        slippage_per_side: Per-side slippage (e.g. 0.0001 = 1 bp).

    Returns:
        EventResult list in event order, minus dropped events.
    """
    if not horizons:
        raise ValueError("horizons must contain at least one value")
    if any(h <= 0 for h in horizons):
        raise ValueError("horizons must be positive integers")

    n = len(feats)
    max_h = max(horizons)
    round_trip_cost = 2.0 * (fee_per_side + slippage_per_side)

    results: list[EventResult] = []
    for ev in events:
        entry_idx = ev.index + 1
        # Need bar[entry_idx + max_h] to exist so every horizon can be exited.
        if entry_idx + max_h >= n:
            continue

        entry_px = feats[entry_idx].open
        fwd_rets: dict[int, float] = {}
        for h in horizons:
            exit_px = feats[entry_idx + h].open
            raw = ev.side * (exit_px - entry_px) / entry_px
            fwd_rets[h] = raw - round_trip_cost

        results.append(
            EventResult(event=ev, entry_px=entry_px, fwd_returns=fwd_rets)
        )
    return results


# ── bucket_events ──────────────────────────────────────────────────────


def bucket_events(
    results: Sequence[EventResult],
    feats_by_idx: Mapping[int, StrategyCFeatureBar],
    *,
    key_fn: Callable[[StrategyCFeatureBar], str],
    horizon: int,
    cost: float = 0.0,
) -> dict[str, dict[str, float]]:
    """Group event results by a feature-level key and summarise each bucket.

    Args:
        results: EventResult list from measure_forward_returns.
        feats_by_idx: Map from Event.index to the feature bar at that index,
            so key_fn can look at the trigger-bar context (flow sign, regime,...).
        key_fn: Function (StrategyCFeatureBar) -> bucket label.
        horizon: Which horizon (in fwd_returns) to summarise.
        cost: Extra threshold applied to the win check — a result is a win
            iff fwd_returns[horizon] > cost. Pass 0 to count raw positive P&L.

    Returns:
        {bucket_label: {"count": n, "avg": mean, "median": med, "win_rate": wr}}

    Events whose fwd_returns dict does not contain the requested horizon are
    silently skipped.
    """
    raw: dict[str, list[float]] = {}
    for r in results:
        feat = feats_by_idx.get(r.event.index)
        if feat is None:
            continue
        val = r.fwd_returns.get(horizon)
        if val is None:
            continue
        key = key_fn(feat)
        raw.setdefault(key, []).append(val)

    summary: dict[str, dict[str, float]] = {}
    for key, vals in raw.items():
        n = len(vals)
        if n == 0:
            continue
        avg = sum(vals) / n
        sorted_vals = sorted(vals)
        if n % 2 == 1:
            median = sorted_vals[n // 2]
        else:
            median = (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2.0
        wins = sum(1 for v in vals if v > cost)
        summary[key] = {
            "count": n,
            "avg": avg,
            "median": median,
            "win_rate": wins / n,
        }
    return summary
