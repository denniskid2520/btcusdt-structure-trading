"""Live-context validation for Case10 (Parent F + G).

Evaluates the ascending-rebound-channel breakdown-retest-short setup by
checking each structural condition individually.  Outputs a report dict
that a human can inspect line by line.

This module does NOT place orders or modify strategy rules.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from statistics import mean
from typing import Any

from context.parent_structures import PARENT_F, PARENT_G, ParentStructure


# ── Channel geometry helpers ─────────────────────────────────────────

def _fit_line(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    """Return (slope, intercept) from list of (x, y) pairs."""
    if len(points) < 2:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x_mean = mean(xs)
    y_mean = mean(ys)
    denom = sum((x - x_mean) ** 2 for x in xs)
    if denom == 0:
        return None
    numer = sum((x - x_mean) * (y - y_mean) for x, y in points)
    slope = numer / denom
    intercept = y_mean - slope * x_mean
    return slope, intercept


def _date_to_ordinal(d: date) -> float:
    return float(d.toordinal())


def _compute_channel_boundaries(
    parent: ParentStructure,
) -> dict[str, Any]:
    """Fit upper / lower boundary lines from parent key highs and lows."""
    high_points = [
        (_date_to_ordinal(kl.date), kl.price)
        for kl in parent.key_highs
        if kl.price > 0
    ]
    low_points = [
        (_date_to_ordinal(kl.date), kl.price)
        for kl in parent.key_lows
        if kl.price > 0
    ]

    upper_fit = _fit_line(high_points)
    lower_fit = _fit_line(low_points)
    return {
        "upper_slope": upper_fit[0] if upper_fit else None,
        "upper_intercept": upper_fit[1] if upper_fit else None,
        "lower_slope": lower_fit[0] if lower_fit else None,
        "lower_intercept": lower_fit[1] if lower_fit else None,
    }


def _boundary_value_at(slope: float, intercept: float, d: date) -> float:
    return slope * _date_to_ordinal(d) + intercept


# ── Condition checkers ───────────────────────────────────────────────

@dataclass(frozen=True)
class ConditionResult:
    name: str
    passed: bool
    detail: str
    value: Any = None


def check_parent_f_bearish_impulse() -> ConditionResult:
    """Parent F must be bearish_impulse_then_ascending_rebound_channel
    and must have ended with a downside breakdown."""
    stype = PARENT_F.structure_type
    has_breakdown = any(
        "breakdown" in t.description.lower()
        for t in PARENT_F.transitions
    )
    passed = stype == "bearish_impulse_then_ascending_rebound_channel" and has_breakdown
    return ConditionResult(
        name="parent_f_bearish_impulse_with_breakdown",
        passed=passed,
        detail=f"type={stype}, breakdown_transition={has_breakdown}",
        value={"structure_type": stype, "has_breakdown": has_breakdown},
    )


def check_parent_g_ascending_channel() -> ConditionResult:
    """Parent G must be an ascending rebound channel."""
    stype = PARENT_G.structure_type
    passed = "ascending" in stype and "channel" in stype
    return ConditionResult(
        name="parent_g_is_ascending_channel",
        passed=passed,
        detail=f"type={stype}",
        value=stype,
    )


def check_parent_g_channel_boundaries() -> tuple[ConditionResult, dict[str, Any]]:
    """Compute Parent G upper/lower boundaries and verify they are rising."""
    bounds = _compute_channel_boundaries(PARENT_G)
    upper_slope = bounds["upper_slope"]
    lower_slope = bounds["lower_slope"]
    both_rising = (
        upper_slope is not None
        and lower_slope is not None
        and upper_slope > 0
        and lower_slope > 0
    )
    return (
        ConditionResult(
            name="parent_g_channel_slopes_rising",
            passed=both_rising,
            detail=f"upper_slope={upper_slope:.4f}/day, lower_slope={lower_slope:.4f}/day"
            if upper_slope is not None and lower_slope is not None
            else "insufficient_data",
            value=bounds,
        ),
        bounds,
    )


def check_breakdown_event(bounds: dict[str, Any]) -> ConditionResult:
    """2026-03-29 breakdown: price (65000) must be below the lower
    boundary projected to that date."""
    breakdown_date = date(2026, 3, 29)
    breakdown_price = 65000.0
    lower_slope = bounds.get("lower_slope")
    lower_intercept = bounds.get("lower_intercept")
    if lower_slope is None or lower_intercept is None:
        return ConditionResult(
            name="breakdown_below_lower_bound",
            passed=False,
            detail="cannot compute — missing lower boundary",
        )
    lower_at_date = _boundary_value_at(lower_slope, lower_intercept, breakdown_date)
    passed = breakdown_price < lower_at_date
    return ConditionResult(
        name="breakdown_below_lower_bound",
        passed=passed,
        detail=f"breakdown_price={breakdown_price:.0f}, lower_bound_at_date={lower_at_date:.0f}, delta={breakdown_price - lower_at_date:.0f}",
        value={
            "breakdown_date": str(breakdown_date),
            "breakdown_price": breakdown_price,
            "lower_bound_at_date": round(lower_at_date, 0),
        },
    )


def check_retest_event(bounds: dict[str, Any]) -> ConditionResult:
    """2026-04-01 retest: price (~69310) should be near / at / below
    the old lower boundary projected to that date, acting as resistance."""
    retest_date = date(2026, 4, 1)
    retest_price = 69310.0
    lower_slope = bounds.get("lower_slope")
    lower_intercept = bounds.get("lower_intercept")
    if lower_slope is None or lower_intercept is None:
        return ConditionResult(
            name="retest_near_lower_bound_as_resistance",
            passed=False,
            detail="cannot compute — missing lower boundary",
        )
    lower_at_date = _boundary_value_at(lower_slope, lower_intercept, retest_date)
    distance = retest_price - lower_at_date
    distance_pct = (distance / lower_at_date) * 100 if lower_at_date != 0 else 0.0
    # retest is valid if price reached up to or slightly above the old
    # lower bound (within ~3% tolerance) then failed to reclaim
    within_zone = distance_pct <= 3.0
    return ConditionResult(
        name="retest_near_lower_bound_as_resistance",
        passed=within_zone,
        detail=f"retest_price={retest_price:.0f}, lower_bound_at_date={lower_at_date:.0f}, distance_pct={distance_pct:+.2f}%",
        value={
            "retest_date": str(retest_date),
            "retest_price": retest_price,
            "lower_bound_at_date": round(lower_at_date, 0),
            "distance_pct": round(distance_pct, 2),
        },
    )


def check_retest_failure_confirmed(
    current_price: float | None,
    current_date: date | None,
    bounds: dict[str, Any],
) -> ConditionResult:
    """After the retest on 2026-04-01, price must close below the retest
    level to confirm rejection.  This is the gate for entry."""
    retest_price = 69310.0
    lower_slope = bounds.get("lower_slope")
    lower_intercept = bounds.get("lower_intercept")

    if current_price is None or current_date is None:
        return ConditionResult(
            name="retest_failure_confirmed",
            passed=False,
            detail="no current price provided — cannot evaluate",
        )

    if lower_slope is not None and lower_intercept is not None:
        lower_now = _boundary_value_at(lower_slope, lower_intercept, current_date)
    else:
        lower_now = retest_price

    # Confirmation: current price is below both the retest high and
    # the projected lower boundary
    below_retest = current_price < retest_price
    below_lower = current_price < lower_now
    confirmed = below_retest and below_lower

    missing_conditions: list[str] = []
    if not below_retest:
        missing_conditions.append(
            f"price ({current_price:.0f}) still >= retest level ({retest_price:.0f})"
        )
    if not below_lower:
        missing_conditions.append(
            f"price ({current_price:.0f}) still >= projected lower bound ({lower_now:.0f})"
        )

    if confirmed:
        detail = (
            f"CONFIRMED: price={current_price:.0f} < retest={retest_price:.0f} "
            f"and < lower_bound={lower_now:.0f}"
        )
    else:
        detail = f"NOT confirmed: {'; '.join(missing_conditions)}"

    return ConditionResult(
        name="retest_failure_confirmed",
        passed=confirmed,
        detail=detail,
        value={
            "current_price": current_price,
            "current_date": str(current_date),
            "retest_price": retest_price,
            "projected_lower_bound": round(lower_now, 0),
            "below_retest": below_retest,
            "below_lower_bound": below_lower,
            "confirmed": confirmed,
            "missing_conditions": missing_conditions,
        },
    )


# ── Trade plan ───────────────────────────────────────────────────────

def compute_trade_plan(
    bounds: dict[str, Any],
    current_date: date,
) -> dict[str, Any]:
    """If all conditions pass, compute entry / stop / targets.

    Uses the rising_channel_breakdown_retest_short logic from the
    existing TrendBreakoutStrategy: stop above the retest high / old
    lower boundary, targets based on channel width.
    """
    lower_slope = bounds.get("lower_slope")
    lower_intercept = bounds.get("lower_intercept")
    upper_slope = bounds.get("upper_slope")
    upper_intercept = bounds.get("upper_intercept")

    if lower_slope is None or upper_slope is None:
        return {"error": "cannot compute — missing boundary data"}

    lower_now = _boundary_value_at(lower_slope, lower_intercept, current_date)
    upper_now = _boundary_value_at(upper_slope, upper_intercept, current_date)
    channel_width = upper_now - lower_now

    retest_high = 69310.0
    invalidation = max(retest_high, lower_now)
    stop_buffer_pct = 0.008  # ~0.8% above invalidation
    stop = invalidation * (1 + stop_buffer_pct)

    # Entry: at or slightly below the retest rejection zone
    entry = retest_high * 0.995  # ~0.5% below retest for confirmation

    # Target 1: breakdown low (65000 area)
    target_1 = 65000.0
    # Target 2: full channel width projection below lower boundary
    target_2 = lower_now - channel_width

    return {
        "side": "short",
        "entry": round(entry, 0),
        "stop": round(stop, 0),
        "target_1": round(target_1, 0),
        "target_2": round(target_2, 0),
        "invalidation": f"close above {round(invalidation, 0)} (retest high / projected lower bound)",
        "channel_width": round(channel_width, 0),
        "risk_reward_t1": round((entry - target_1) / (stop - entry), 2) if stop > entry else 0,
        "risk_reward_t2": round((entry - target_2) / (stop - entry), 2) if stop > entry else 0,
    }


# ── Full report assembly ─────────────────────────────────────────────

def validate_case10_live_setup(
    current_price: float | None = None,
    current_date: date | None = None,
) -> dict[str, Any]:
    """Run all conditions for Case10 and return a structured report."""
    eval_date = current_date or date.today()

    conditions: list[ConditionResult] = []

    # 1. Parent F context
    conditions.append(check_parent_f_bearish_impulse())

    # 2. Parent G is ascending channel
    conditions.append(check_parent_g_ascending_channel())

    # 3. Channel boundaries
    boundary_result, bounds = check_parent_g_channel_boundaries()
    conditions.append(boundary_result)

    # 4. Breakdown event
    conditions.append(check_breakdown_event(bounds))

    # 5. Retest event
    conditions.append(check_retest_event(bounds))

    # 6. Retest failure confirmation (requires live price)
    retest_confirmation = check_retest_failure_confirmed(current_price, eval_date, bounds)
    conditions.append(retest_confirmation)

    all_passed = all(c.passed for c in conditions)

    # Channel levels for the report
    lower_slope = bounds.get("lower_slope")
    lower_intercept = bounds.get("lower_intercept")
    upper_slope = bounds.get("upper_slope")
    upper_intercept = bounds.get("upper_intercept")

    channel_info: dict[str, Any] = {}
    if lower_slope is not None and upper_slope is not None:
        lower_now = _boundary_value_at(lower_slope, lower_intercept, eval_date)
        upper_now = _boundary_value_at(upper_slope, upper_intercept, eval_date)
        channel_info = {
            "upper_boundary_current": round(upper_now, 0),
            "lower_boundary_current": round(lower_now, 0),
            "channel_width_current": round(upper_now - lower_now, 0),
        }

    report: dict[str, Any] = {
        "report_type": "current_live_setup_report",
        "case_id": "Case10",
        "evaluation_date": str(eval_date),
        "current_price": current_price,
        "parent_structure_summary": {
            "parent_f": {
                "name": PARENT_F.name,
                "type": PARENT_F.structure_type,
                "period": f"{PARENT_F.period_start} -> {PARENT_F.period_end}",
                "transition": "downside breakdown 2026-01-29, selloff to 60000",
            },
            "parent_g": {
                "name": PARENT_G.name,
                "type": PARENT_G.structure_type,
                "period": f"{PARENT_G.period_start} -> {PARENT_G.period_end}",
                "key_highs": [
                    {"date": str(kl.date), "price": kl.price}
                    for kl in PARENT_G.key_highs
                ],
                "key_lows": [
                    {"date": str(kl.date), "price": kl.price, "label": kl.label}
                    for kl in PARENT_G.key_lows
                ],
                "midline_retest": "2026-03-25 at 72026",
            },
        },
        "local_structure_summary": {
            "type": "ascending_rebound_channel",
            "breakdown_date": "2026-03-29",
            "breakdown_price": 65000,
            "retest_date": "2026-04-01",
            "retest_price": 69310,
        },
        "channel_boundaries": channel_info,
        "conditions": [
            {
                "name": c.name,
                "passed": c.passed,
                "detail": c.detail,
            }
            for c in conditions
        ],
        "all_conditions_met": all_passed,
    }

    if all_passed:
        report["trade_valid"] = True
        report["trade_plan"] = compute_trade_plan(bounds, eval_date)
        report["next_action"] = "setup valid — see trade_plan for entry/stop/targets"
    else:
        report["trade_valid"] = False
        report["trade_plan"] = None
        failing = [c for c in conditions if not c.passed]
        report["blocking_conditions"] = [
            {"name": c.name, "detail": c.detail}
            for c in failing
        ]
        report["next_action"] = (
            "setup NOT yet valid — see blocking_conditions for what is still missing"
        )

    return report


def save_report(report: dict[str, Any], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    return path
