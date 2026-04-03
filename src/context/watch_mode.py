"""Watch / alert mode for Case10 live setup.

No order placement.  No strategy rule changes.  This module only:
1. Evaluates the current live setup conditions
2. Prints a human-readable status
3. Saves the report to disk

Usage:
    python -m context.watch_mode --price 67500
    python -m context.watch_mode --price 67500 --date 2026-04-03
"""

from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path

from context.live_setup_validator import save_report, validate_case10_live_setup


DEFAULT_REPORT_PATH = "data/reports/current_live_setup_report.json"


def _print_status(report: dict) -> None:
    print("=" * 68)
    print("  CASE10 LIVE SETUP WATCH — ascending channel breakdown retest short")
    print("=" * 68)
    print(f"  Date:  {report['evaluation_date']}")
    print(f"  Price: {report['current_price']}")
    print()

    # Parent summary
    pf = report["parent_structure_summary"]["parent_f"]
    pg = report["parent_structure_summary"]["parent_g"]
    print(f"  Parent F: {pf['type']}")
    print(f"            {pf['period']}")
    print(f"            Transition: {pf['transition']}")
    print()
    print(f"  Parent G: {pg['type']}")
    print(f"            {pg['period']}")
    print(f"            Midline retest: {pg['midline_retest']}")
    print()

    # Channel boundaries
    ch = report.get("channel_boundaries", {})
    if ch:
        print(f"  Channel upper boundary (now): {ch.get('upper_boundary_current')}")
        print(f"  Channel lower boundary (now): {ch.get('lower_boundary_current')}")
        print(f"  Channel width (now):          {ch.get('channel_width_current')}")
        print()

    # Local structure
    ls = report["local_structure_summary"]
    print(f"  Breakdown: {ls['breakdown_date']} at {ls['breakdown_price']}")
    print(f"  Retest:    {ls['retest_date']} at {ls['retest_price']}")
    print()

    # Conditions
    print("  CONDITIONS:")
    for c in report["conditions"]:
        icon = "PASS" if c["passed"] else "FAIL"
        print(f"    [{icon}] {c['name']}")
        print(f"           {c['detail']}")
    print()

    # Verdict
    if report["trade_valid"]:
        tp = report["trade_plan"]
        print("  >>> TRADE VALID <<<")
        print(f"  Side:           {tp['side']}")
        print(f"  Entry:          {tp['entry']}")
        print(f"  Stop:           {tp['stop']}")
        print(f"  Target 1:       {tp['target_1']}")
        print(f"  Target 2:       {tp['target_2']}")
        print(f"  Invalidation:   {tp['invalidation']}")
        print(f"  R:R (T1):       {tp['risk_reward_t1']}")
        print(f"  R:R (T2):       {tp['risk_reward_t2']}")
    else:
        print("  >>> TRADE NOT YET VALID <<<")
        for b in report.get("blocking_conditions", []):
            print(f"  - {b['name']}: {b['detail']}")
        print()
        print(f"  Next action: {report['next_action']}")

    print()
    print("  MODE: WATCH ONLY — no orders placed")
    print("=" * 68)


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch mode for Case10 live setup")
    parser.add_argument(
        "--price",
        type=float,
        default=None,
        help="Current BTCUSDT price (if omitted, report will flag missing price)",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Evaluation date in YYYY-MM-DD format (default: today)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=DEFAULT_REPORT_PATH,
        help=f"Output path for JSON report (default: {DEFAULT_REPORT_PATH})",
    )
    args = parser.parse_args()

    eval_date: date | None = None
    if args.date:
        eval_date = datetime.strptime(args.date, "%Y-%m-%d").date()

    report = validate_case10_live_setup(
        current_price=args.price,
        current_date=eval_date,
    )

    _print_status(report)
    path = save_report(report, args.output)
    print(f"  Report saved to: {path}")


if __name__ == "__main__":
    main()
