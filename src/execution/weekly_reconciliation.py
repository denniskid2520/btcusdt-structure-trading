"""Phase 13D — weekly reconciliation report.

Reads telemetry JSONL for all 4 candidates, compares live paper
results against expected OOS bands, and produces a structured
weekly report.

Usage:
    python -m execution.weekly_reconciliation

Run manually every week or on a Sunday cron.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

DATA_DIR = PROJECT_ROOT / "data" / "paper_state"

# Expected OOS per-trade statistics (from Phase 10 canonical)
EXPECTED = {
    "B_balanced_4x": {
        "avg_pnl": 0.0566, "wr": 0.693, "pf": 4.63,
        "stop_frac": 0.207, "worst_trade": -0.083,
        "trades_per_180d": 19,  # 150 / 8 windows
    },
    "B_balanced_3x": {
        "avg_pnl": 0.0377, "wr": 0.693, "pf": 4.63,
        "stop_frac": 0.207, "worst_trade": -0.056,
        "trades_per_180d": 19,
    },
    "A_density_4x": {
        "avg_pnl": 0.0273, "wr": 0.674, "pf": 3.30,
        "stop_frac": 0.140, "worst_trade": -0.083,
        "trades_per_180d": 33,  # 264 / 8 windows
    },
    "B_balanced_5x": {
        "avg_pnl": 0.0628, "wr": 0.693, "pf": 4.63,
        "stop_frac": 0.207, "worst_trade": -0.093,
        "trades_per_180d": 19,
    },
}


def load_telemetry(candidate_id: str) -> list[dict]:
    telem_file = DATA_DIR / candidate_id / "telemetry.jsonl"
    if not telem_file.exists():
        return []
    rows = []
    for line in telem_file.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def load_daily_summaries(candidate_id: str) -> list[dict]:
    summary_file = DATA_DIR / candidate_id / "daily_summary.jsonl"
    if not summary_file.exists():
        return []
    rows = []
    for line in summary_file.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def generate_report() -> str:
    lines = []
    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)

    lines.append(f"# Weekly Reconciliation Report — {now.strftime('%Y-%m-%d')}")
    lines.append(f"Period: {week_ago.strftime('%Y-%m-%d')} to {now.strftime('%Y-%m-%d')}")
    lines.append("")

    for cid in EXPECTED:
        exp = EXPECTED[cid]
        trades = load_telemetry(cid)
        summaries = load_daily_summaries(cid)

        # Separate historical replay from forward trades
        replay_trades = [t for t in trades if t.get("historical_replay", False)]
        forward_trades = [t for t in trades if not t.get("historical_replay", False)]
        week_trades = [t for t in forward_trades
                       if t.get("entry_fill_ts", "") >= week_ago.isoformat()]

        lines.append(f"## {cid}")
        lines.append(f"  Total trades (all time): {len(trades)} "
                     f"(replay: {len(replay_trades)}, forward: {len(forward_trades)})")
        lines.append(f"  Forward trades this week: {len(week_trades)}")

        # Use FORWARD trades only for validation metrics
        all_trades = forward_trades

        if all_trades:
            pnls = [t["net_pnl"] for t in all_trades]
            wins = sum(1 for p in pnls if p > 0)
            n = len(pnls)
            wr = wins / n if n else 0
            worst = min(pnls) if pnls else 0
            stop_exits = sum(1 for t in all_trades
                            if t["exit_reason"] in ("alpha_stop", "catastrophe_stop"))
            stop_frac = stop_exits / n if n else 0

            lines.append(f"  Realized WR: {wr*100:.1f}% (expected: {exp['wr']*100:.1f}%)")
            lines.append(f"  Realized worst trade: {worst*100:+.2f}% "
                        f"(expected bound: {exp['worst_trade']*100:+.2f}%)")
            lines.append(f"  Realized stop fraction: {stop_frac*100:.1f}% "
                        f"(expected: {exp['stop_frac']*100:.1f}%)")
            lines.append(f"  Avg PnL: {sum(pnls)/n*100:+.2f}% "
                        f"(expected: {exp['avg_pnl']*100:+.2f}%)")

            # Deviation flags
            if worst < exp["worst_trade"] * 1.5:
                lines.append(f"  [FLAG] worst trade exceeds 1.5x expected bound")
            if n >= 5 and wr < exp["wr"] - 0.15:
                lines.append(f"  [FLAG] WR significantly below expected")
        else:
            lines.append(f"  No trades yet")

        # Gate summary from daily logs
        if summaries:
            recent = summaries[-7:] if len(summaries) >= 7 else summaries
            gate_fails = sum(1 for s in recent
                           if not s.get("gates", {}).get("all_pass", True))
            lines.append(f"  Gate failures in last 7 logs: {gate_fails}")

        lines.append("")

    # Cross-candidate comparison
    lines.append("## Cross-candidate divergence")
    for a, b in [("B_balanced_4x", "B_balanced_3x"),
                 ("B_balanced_4x", "A_density_4x"),
                 ("B_balanced_4x", "B_balanced_5x")]:
        ta = load_telemetry(a)
        tb = load_telemetry(b)
        na, nb = len(ta), len(tb)
        lines.append(f"  {a} vs {b}: {na} vs {nb} trades")
        if na > 0 and nb > 0:
            avg_a = sum(t["net_pnl"] for t in ta) / na
            avg_b = sum(t["net_pnl"] for t in tb) / nb
            lines.append(f"    avg PnL: {avg_a*100:+.2f}% vs {avg_b*100:+.2f}%")
    lines.append("")

    return "\n".join(lines)


def main():
    report = generate_report()
    print(report)

    # Also save to file
    report_dir = PROJECT_ROOT / "reports"
    report_dir.mkdir(exist_ok=True)
    report_file = report_dir / f"weekly_{datetime.utcnow().strftime('%Y%m%d')}.md"
    report_file.write_text(report)
    print(f"\nSaved to {report_file}")


if __name__ == "__main__":
    main()
