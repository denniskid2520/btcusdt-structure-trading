"""Phase 10 — Final lock validation.

6 combinations: 2 templates (density/balanced) x 3 tiers (3x/4x/5x).
Full validation stack + dual cost model + regime-zone concentration.
"""
from __future__ import annotations

import sys
import time
from bisect import bisect_left, bisect_right
from collections import defaultdict

sys.path.insert(0, "src")

from research.strategy_c_v2_backtest import V2Trade
from research.strategy_c_v2_circuit_breaker import _compute_max_adverse_intrabar
from research.strategy_c_v2_execution_layer import (
    ExecLayerConfig,
    ExecLayerResult,
    _identify_regime_zones,
    run_execution_layer_backtest,
)
from research.strategy_c_v2_runner import (
    build_funding_per_bar,
    combined_profit_factor,
    load_funding_csv,
    load_klines_csv,
    load_timeframe_data,
)

KLINES_4H = "src/data/btcusdt_4h_6year.csv"
KLINES_1H = "src/data/btcusdt_1h_6year.csv"
KLINES_15M = "src/data/btcusdt_15m_6year.csv"
FUNDING_CSV = "src/data/btcusdt_funding_5year.csv"
STARTING_EQUITY = 10_000.0
EXTRA_COST = 2 * 0.0002  # exec-aware round-trip penalty

TEMPLATES = {
    "A_density": ExecLayerConfig(
        entry_type="hybrid", pullback_pct=0.0075, breakout_pct=0.005,
        max_entries_per_zone=6, cooldown_bars=2, hold_hours=8,
        alpha_stop_pct=0.0125, catastrophe_stop_pct=0.025,
        reentry_after_alpha_stop=True, reentry_after_cat_stop=True,
        exec_tf_hours=1.0,
    ),
    "B_balanced": ExecLayerConfig(
        entry_type="hybrid", pullback_pct=0.0075, breakout_pct=0.0025,
        max_entries_per_zone=6, cooldown_bars=2, hold_hours=24,
        alpha_stop_pct=0.0125, catastrophe_stop_pct=0.025,
        reentry_after_alpha_stop=True, reentry_after_cat_stop=True,
        exec_tf_hours=1.0,
    ),
}

TIERS = [
    ("3x", 2.0, 3.0, 3.0),
    ("4x", 3.0, 4.0, 4.0),
    ("5x", 3.33, 5.0, 5.0),
]

SHOCKS = (0.10, 0.15, 0.20, 0.30, 0.40)
SLIPS = (0.001, 0.003, 0.005, 0.010)


def run_combo(tf_4h, bars_1h, funding_1h, bars_15m, config, frac, lev, label):
    result = run_execution_layer_backtest(
        bars_4h=tf_4h.bars, features_4h=tf_4h.features,
        bars_1h=bars_1h, funding_1h=funding_1h,
        config=config, position_frac=frac,
    )
    trades = result.trades
    n = len(trades)
    if n == 0:
        return None

    # --- Metrics under both cost models ---
    rows = []
    for cost_label, extra in [("simple", 0.0), ("exec_aware", EXTRA_COST * frac)]:
        pnls = [t.net_pnl - extra for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        eq = 1.0
        peak_eq = 1.0
        dd = 0.0
        peak_usd = STARTING_EQUITY
        dd_usd = 0.0
        for p in pnls:
            eq *= (1.0 + p)
            if eq > peak_eq: peak_eq = eq
            if peak_eq > 0:
                d = (peak_eq - eq) / peak_eq
                if d > dd: dd = d
            u = STARTING_EQUITY * eq
            if u > peak_usd: peak_usd = u
            drop = peak_usd - u
            if drop > dd_usd: dd_usd = drop

        a_stops = sum(1 for t in trades if t.exit_reason.startswith("alpha_stop"))
        c_stops = sum(1 for t in trades if t.exit_reason.startswith("catastrophe_stop"))
        total_stops = a_stops + c_stops
        stopped = [p for t, p in zip(trades, pnls)
                   if t.exit_reason.startswith(("alpha_stop", "catastrophe_stop"))]

        # 15m adverse replay (non-stop trades only)
        ts_15m = [b.timestamp for b in bars_15m]
        worst_adv = 0.0
        for t in trades:
            if t.exit_reason.startswith(("alpha_stop", "catastrophe_stop")):
                continue
            lo = bisect_left(ts_15m, t.entry_time)
            hi = bisect_right(ts_15m, t.exit_time)
            adv, _, _ = _compute_max_adverse_intrabar(bars_15m, lo, hi, t.entry_price, t.side)
            if adv > worst_adv:
                worst_adv = adv

        liq_dist = 1.0 / lev
        hist_liq = worst_adv >= liq_dist

        # Shocks
        shock_v = {}
        for s in SHOCKS:
            combined = worst_adv + s
            if combined >= liq_dist:
                shock_v[s] = "liq"
            elif liq_dist - combined < 0.05:
                shock_v[s] = "tight"
            else:
                shock_v[s] = "surv"

        # Slippage
        slip_v = {}
        for sl in SLIPS:
            drag = total_stops * sl * frac
            adj = eq * (1.0 - drag) - 1.0
            simp_drag = total_stops * sl * frac
            adj_simp = sum(pnls) - simp_drag
            slip_v[sl] = {"comp": adj, "simp": adj_simp,
                          "delta_simp_pp": (adj_simp - sum(pnls)) * 100}

        # Zone concentration
        zones = _identify_regime_zones(tf_4h.bars, tf_4h.features)
        zone_pnl = defaultdict(float)
        zone_count = defaultdict(int)
        for t in trades:
            for zi, (zs, ze, _, _) in enumerate(zones):
                if zs <= t.entry_time < ze:
                    zone_pnl[zi] += pnls[trades.index(t)] if t in trades else 0
                    zone_count[zi] += 1
                    break
        top5_zones = sorted(zone_pnl.items(), key=lambda x: -x[1])[:5]
        top5_pnl_share = sum(v for _, v in top5_zones) / sum(pnls) if sum(pnls) != 0 else 0

        rows.append({
            "label": f"{label} [{cost_label}]",
            "template": label.split("_")[0] + "_" + label.split("_")[1],
            "tier": label.split("_")[-1],
            "cost": cost_label,
            "n": n, "wins": len(wins), "losses": len(losses),
            "wr": len(wins) / n,
            "pf": combined_profit_factor(pnls),
            "comp_ret": eq - 1.0,
            "simp_ret": sum(pnls),
            "end_comp": STARTING_EQUITY * eq,
            "end_simp": STARTING_EQUITY * (1 + sum(pnls)),
            "avg_pnl": sum(pnls) / n,
            "avg_win": sum(wins) / len(wins) if wins else 0,
            "avg_loss": sum(losses) / len(losses) if losses else 0,
            "dd": dd, "dd_usd": dd_usd,
            "worst": min(pnls), "worst_usd": min(pnls) * STARTING_EQUITY,
            "a_stops": a_stops, "c_stops": c_stops,
            "stop_frac": total_stops / n,
            "avg_stopped": sum(stopped) / len(stopped) if stopped else 0,
            "avg_frac": frac, "max_frac": frac,
            "lev": lev, "liq_dist": liq_dist,
            "worst_adv": worst_adv, "hist_liq": hist_liq,
            "shocks": shock_v, "slips": slip_v,
            "base_entries": result.num_base_entries,
            "reentries": result.num_reentries,
            "zones_used": result.num_zones_used,
            "top5_zone_pnl_share": top5_pnl_share,
            "zone_count_dist": dict(zone_count),
        })
    return rows


def main():
    print("=" * 78)
    print("Phase 10 -- FINAL LOCK VALIDATION")
    print("=" * 78)

    print("\nLoading data...")
    t0 = time.time()
    funding_records = load_funding_csv(FUNDING_CSV)
    tf_4h = load_timeframe_data("4h", KLINES_4H, 4.0, funding_records)
    bars_1h = load_klines_csv(KLINES_1H)
    bars_15m = load_klines_csv(KLINES_15M)
    funding_1h = build_funding_per_bar(bars_1h, funding_records)
    print(f"  ({time.time()-t0:.1f}s)")

    all_rows = []
    for tmpl_name, config in TEMPLATES.items():
        for tier_label, frac, max_frac, lev in TIERS:
            label = f"{tmpl_name}_{tier_label}"
            print(f"\nRunning {label}...")
            rows = run_combo(tf_4h, bars_1h, funding_1h, bars_15m,
                             config, frac, lev, label)
            if rows:
                all_rows.extend(rows)
                r = rows[1]  # exec-aware
                print(f"  n={r['n']} WR={r['wr']*100:.1f}% PF={r['pf']:.2f} "
                      f"simp={r['simp_ret']*100:+.1f}% DD={r['dd']*100:.1f}%")

    # --- FINAL COMPARISON TABLE ---
    print("\n" + "=" * 78)
    print("PHASE 10 FINAL COMPARISON TABLE (exec-aware cost model)")
    print("=" * 78)
    ea = [r for r in all_rows if r["cost"] == "exec_aware"]
    print(f"\n{'Label':<25} | {'n':>4} {'WR':>5} {'PF':>5} | "
          f"{'SimpRet':>8} {'End$s':>8} {'CompRet':>11} | "
          f"{'AvgPnL':>6} {'AvgW':>6} {'AvgL':>6} | "
          f"{'DD%':>5} {'DD$':>7} {'Wrst%':>6} | "
          f"{'aS':>3} {'cS':>3} {'StF':>5} | "
          f"{'Adv15':>5} {'Liq?':>4} | "
          f"{'10':>4} {'15':>4} {'20':>4} {'30':>4} {'40':>4} | "
          f"{'s.1':>6} {'s.3':>6} {'s1':>6} | "
          f"{'Top5Z':>5}")
    print("-" * 195)
    for r in ea:
        sv = r["shocks"]
        sl = r["slips"]
        print(
            f"{r['label'].replace(' [exec_aware]',''):<25} | "
            f"{r['n']:>4} {r['wr']*100:>4.1f}% {r['pf']:>5.2f} | "
            f"{r['simp_ret']*100:>+7.1f}% ${r['end_simp']:>7,.0f} "
            f"{r['comp_ret']*100:>+10.1f}% | "
            f"{r['avg_pnl']*100:>5.2f}% {r['avg_win']*100:>5.2f}% "
            f"{r['avg_loss']*100:>5.2f}% | "
            f"{r['dd']*100:>4.1f}% ${r['dd_usd']:>6,.0f} "
            f"{r['worst']*100:>+5.1f}% | "
            f"{r['a_stops']:>3} {r['c_stops']:>3} "
            f"{r['stop_frac']*100:>4.1f}% | "
            f"{r['worst_adv']*100:>4.2f}% {'Y' if r['hist_liq'] else 'N':>4} | "
            f"{sv.get(0.10,'?'):>4} {sv.get(0.15,'?'):>4} "
            f"{sv.get(0.20,'?'):>4} {sv.get(0.30,'?'):>4} "
            f"{sv.get(0.40,'?'):>4} | "
            f"{sl[0.001]['delta_simp_pp']:>+5.0f}p "
            f"{sl[0.003]['delta_simp_pp']:>+5.0f}p "
            f"{sl[0.010]['delta_simp_pp']:>+5.0f}p | "
            f"{r['top5_zone_pnl_share']*100:>4.1f}%"
        )

    # --- SELECTION ---
    print("\n" + "=" * 78)
    print("SELECTION")
    print("=" * 78)

    for r in ea:
        sv = r["shocks"]
        sl = r["slips"]
        passes_all = (
            r["n"] >= 100
            and r["wr"] >= 0.60
            and r["pf"] >= 2.0
            and not r["hist_liq"]
            and sv.get(0.15) in ("surv", "tight")
            and r["simp_ret"] >= 5.0
        )
        print(f"\n  {r['label'].replace(' [exec_aware]','')}: "
              f"{'PASS' if passes_all else 'FAIL'}")
        print(f"    n={r['n']} WR={r['wr']*100:.1f}% PF={r['pf']:.2f} "
              f"simp={r['simp_ret']*100:+.1f}% DD={r['dd']*100:.1f}% "
              f"shk15={sv.get(0.15)} shk20={sv.get(0.20)}")
        sr1 = sl[0.010]["simp"]
        print(f"    simp@1%slip={sr1*100:+.1f}%")

    # Best
    passing = [r for r in ea
               if r["n"] >= 100 and r["wr"] >= 0.60 and r["pf"] >= 2.0
               and not r["hist_liq"]
               and r["shocks"].get(0.15) in ("surv", "tight")]
    if passing:
        # Rank by simple return
        passing.sort(key=lambda r: -r["simp_ret"])
        final = passing[0]
        fallback = next((r for r in passing if r["lev"] <= 3.0), passing[-1])
        shadow = next((r for r in passing if r["lev"] >= 5.0), None)

        print(f"\n{'='*78}")
        print("FINAL / FALLBACK / SHADOW")
        print(f"{'='*78}")

        print(f"\n  FINAL:    {final['label'].replace(' [exec_aware]','')}")
        print(f"            n={final['n']} WR={final['wr']*100:.1f}% PF={final['pf']:.2f}")
        print(f"            simple={final['simp_ret']*100:+.1f}% "
              f"(${final['end_simp']:,.0f}) DD={final['dd']*100:.1f}%")

        print(f"\n  FALLBACK: {fallback['label'].replace(' [exec_aware]','')}")
        print(f"            n={fallback['n']} WR={fallback['wr']*100:.1f}% PF={fallback['pf']:.2f}")
        print(f"            simple={fallback['simp_ret']*100:+.1f}% "
              f"(${fallback['end_simp']:,.0f}) DD={fallback['dd']*100:.1f}%")

        if shadow and shadow != final:
            print(f"\n  SHADOW:   {shadow['label'].replace(' [exec_aware]','')}")
            print(f"            n={shadow['n']} WR={shadow['wr']*100:.1f}% PF={shadow['pf']:.2f}")
            print(f"            simple={shadow['simp_ret']*100:+.1f}% "
                  f"(${shadow['end_simp']:,.0f}) DD={shadow['dd']*100:.1f}%")


if __name__ == "__main__":
    main()
