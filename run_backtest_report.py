"""Full backtest report for all 4 locked candidates."""
import sys
sys.path.insert(0, "src")

from research.strategy_c_v2_execution_layer import ExecLayerConfig, run_execution_layer_backtest
from research.strategy_c_v2_runner import (
    build_funding_per_bar, combined_profit_factor,
    load_funding_csv, load_klines_csv, load_timeframe_data,
)

print("Loading data...")
funding_records = load_funding_csv("src/data/btcusdt_funding_5year.csv")
tf_4h = load_timeframe_data("4h", "src/data/btcusdt_4h_6year.csv", 4.0, funding_records)
bars_1h = load_klines_csv("src/data/btcusdt_1h_6year.csv")
funding_1h = build_funding_per_bar(bars_1h, funding_records)

EQUITY = 10000.0
EXTRA = 2 * 0.0002

cfgs = {
    "B_balanced_4x": (ExecLayerConfig(
        entry_type="hybrid", pullback_pct=0.0075, breakout_pct=0.0025,
        max_entries_per_zone=6, cooldown_bars=2, hold_hours=24,
        alpha_stop_pct=0.0125, catastrophe_stop_pct=0.025,
        reentry_after_alpha_stop=True, reentry_after_cat_stop=True,
        exec_tf_hours=1.0), 3.0, 4.0, "FINAL"),
    "B_balanced_3x": (ExecLayerConfig(
        entry_type="hybrid", pullback_pct=0.0075, breakout_pct=0.0025,
        max_entries_per_zone=6, cooldown_bars=2, hold_hours=24,
        alpha_stop_pct=0.0125, catastrophe_stop_pct=0.025,
        reentry_after_alpha_stop=True, reentry_after_cat_stop=True,
        exec_tf_hours=1.0), 2.0, 3.0, "FALLBACK"),
    "A_density_4x": (ExecLayerConfig(
        entry_type="hybrid", pullback_pct=0.0075, breakout_pct=0.005,
        max_entries_per_zone=6, cooldown_bars=2, hold_hours=8,
        alpha_stop_pct=0.0125, catastrophe_stop_pct=0.025,
        reentry_after_alpha_stop=True, reentry_after_cat_stop=True,
        exec_tf_hours=1.0), 3.0, 4.0, "SHADOW-HS"),
    "B_balanced_5x": (ExecLayerConfig(
        entry_type="hybrid", pullback_pct=0.0075, breakout_pct=0.0025,
        max_entries_per_zone=6, cooldown_bars=2, hold_hours=24,
        alpha_stop_pct=0.0125, catastrophe_stop_pct=0.025,
        reentry_after_alpha_stop=True, reentry_after_cat_stop=True,
        exec_tf_hours=1.0), 3.33, 5.0, "SHADOW-HR"),
}

rows = {}
for cid, (cfg, frac, mf, role) in cfgs.items():
    r = run_execution_layer_backtest(
        bars_4h=tf_4h.bars, features_4h=tf_4h.features,
        bars_1h=bars_1h, funding_1h=funding_1h,
        config=cfg, position_frac=frac,
    )
    pnls = [t.net_pnl - EXTRA * frac for t in r.trades]
    n = len(r.trades)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    eq = 1.0
    peak_eq = 1.0
    dd = 0.0
    peak_usd = EQUITY
    dd_usd = 0.0
    for p in pnls:
        eq *= (1.0 + p)
        if eq > peak_eq: peak_eq = eq
        if peak_eq > 0:
            d = (peak_eq - eq) / peak_eq
            if d > dd: dd = d
        u = EQUITY * eq
        if u > peak_usd: peak_usd = u
        drop = peak_usd - u
        if drop > dd_usd: dd_usd = drop
    ast = sum(1 for t in r.trades if t.exit_reason.startswith("alpha_stop"))
    cst = sum(1 for t in r.trades if t.exit_reason.startswith("catastrophe_stop"))
    tst = sum(1 for t in r.trades if t.exit_reason in ("time_stop", "end_of_series"))
    flp = n - ast - cst - tst
    stopped = [p for t, p in zip(r.trades, pnls) if t.exit_reason.startswith(("alpha_stop", "catastrophe_stop"))]
    rows[cid] = dict(
        role=role, n=n, wins=len(wins), losses=len(losses),
        wr=len(wins)/n, pf=combined_profit_factor(pnls),
        comp=eq-1, simp=sum(pnls),
        end_comp=EQUITY*eq, end_simp=EQUITY*(1+sum(pnls)),
        avg=sum(pnls)/n, avgw=sum(wins)/len(wins) if wins else 0,
        avgl=sum(losses)/len(losses) if losses else 0,
        dd=dd, dd_usd=dd_usd, worst=min(pnls), worst_usd=min(pnls)*EQUITY,
        ast=ast, cst=cst, tst=tst, flp=flp,
        sf=(ast+cst)/n, avgstp=sum(stopped)/len(stopped) if stopped else 0,
        frac=frac, mf=mf, lev=mf,
        base=r.num_base_entries, re=r.num_reentries, zones=r.num_zones_used,
    )

# ══════════════════════════════════════════════════════════════
print()
print("=" * 130)
print("STRATEGY C v2 -- FINAL BACKTEST REPORT")
print("Walk-forward: 24m train / 6m test / 8 OOS windows (2022-04 to 2026-04)")
print("Starting equity: $10,000 | Cost model: execution-aware | Data: Binance 4h+1h+funding")
print("=" * 130)

# Comparison table
print()
hdr = (f"{'Candidate':<20} {'Role':<11} | {'Lev':>3} {'Frac':>4} | "
       f"{'Trades':>6} {'Base':>4} {'Re':>3} | {'WR':>5} {'PF':>5} | "
       f"{'Simple Ret':>10} {'End$(s)':>9} | {'Comp Ret':>12} {'End$(c)':>11} | "
       f"{'DD%':>5} {'DD$':>7} {'Wrst%':>6} {'Wrst$':>7} | "
       f"{'aS':>3} {'cS':>3} {'tS':>3} {'StpFr':>5}")
print(hdr)
print("-" * len(hdr))
for cid in ["B_balanced_4x", "B_balanced_3x", "A_density_4x", "B_balanced_5x"]:
    r = rows[cid]
    print(
        f"{cid:<20} {r['role']:<11} | "
        f"{r['lev']:>2.0f}x {r['frac']:>4.2f} | "
        f"{r['n']:>6} {r['base']:>4} {r['re']:>3} | "
        f"{r['wr']*100:>4.1f}% {r['pf']:>5.2f} | "
        f"{r['simp']*100:>+9.1f}% ${r['end_simp']:>8,.0f} | "
        f"{r['comp']*100:>+11.1f}% ${r['end_comp']:>9,.0f} | "
        f"{r['dd']*100:>4.1f}% ${r['dd_usd']:>6,.0f} "
        f"{r['worst']*100:>+5.1f}% ${r['worst_usd']:>6,.0f} | "
        f"{r['ast']:>3} {r['cst']:>3} {r['tst']:>3} "
        f"{r['sf']*100:>4.1f}%"
    )

# Per-trade quality
print()
print("=" * 90)
print("PER-TRADE QUALITY")
print("=" * 90)
for cid in ["B_balanced_4x", "B_balanced_3x", "A_density_4x", "B_balanced_5x"]:
    r = rows[cid]
    wlr = abs(r["avgw"] / r["avgl"]) if r["avgl"] else 0
    print(f"  {cid} ({r['role']}):")
    print(f"    Avg PnL:     {r['avg']*100:>+6.2f}% (${r['avg']*EQUITY:>+7,.0f})")
    print(f"    Avg win:     {r['avgw']*100:>+6.2f}% (${r['avgw']*EQUITY:>+7,.0f})")
    print(f"    Avg loss:    {r['avgl']*100:>+6.2f}% (${r['avgl']*EQUITY:>+7,.0f})")
    print(f"    W/L ratio:   {wlr:.2f}x")
    print(f"    Zones used:  {r['zones']}")
    print()

# Shock stress
print("=" * 90)
print("SHOCK STRESS (worst 15m adverse on non-stop trades = 2.28%)")
print("=" * 90)
wa = 0.0228
for cid in ["B_balanced_4x", "B_balanced_3x", "A_density_4x", "B_balanced_5x"]:
    r = rows[cid]
    liq = 1.0 / r["lev"]
    print(f"  {cid} (liq @ {liq*100:.1f}%):")
    for shock in [0.10, 0.15, 0.20, 0.30, 0.40]:
        c = wa + shock
        if c >= liq:
            v = "LIQUIDATES"
        elif liq - c < 0.05:
            v = "tight"
        else:
            v = "survives"
        print(f"    {shock*100:>4.0f}% shock -> {c*100:>5.1f}% combined -> {v}")
    print()

# Slippage
print("=" * 90)
print("SLIPPAGE STRESS (simple return after slippage drag)")
print("=" * 90)
print(f"  {'Candidate':<20} | {'0%':>8} | {'0.1%':>8} | {'0.3%':>8} | {'0.5%':>8} | {'1.0%':>8}")
print("  " + "-" * 70)
for cid in ["B_balanced_4x", "B_balanced_3x", "A_density_4x", "B_balanced_5x"]:
    r = rows[cid]
    ts = r["ast"] + r["cst"]
    base = r["simp"] * 100
    line = f"  {cid:<20} | {base:>+7.1f}%"
    for sl in [0.001, 0.003, 0.005, 0.010]:
        drag = ts * sl * r["frac"] * 100
        line += f" | {base - drag:>+7.1f}%"
    print(line)

# Verdict
print()
print("=" * 90)
print("VERDICT")
print("=" * 90)
for cid in ["B_balanced_4x", "B_balanced_3x", "A_density_4x", "B_balanced_5x"]:
    r = rows[cid]
    liq = 1.0 / r["lev"]
    ok = (r["n"] >= 100 and r["wr"] >= 0.60 and r["pf"] >= 2.0
          and wa < liq and r["simp"] >= 5.0)
    status = "PASS" if ok else "FAIL"
    print(f"  {cid} ({r['role']}): {status}")
    print(f"    trades={r['n']} WR={r['wr']*100:.1f}% PF={r['pf']:.2f} "
          f"simple={r['simp']*100:+.1f}% DD={r['dd']*100:.1f}%")
    if r["simp"] >= 5.0:
        print(f"    500% target: REACHED ({r['simp']*100:+.1f}%)")
    else:
        print(f"    500% target: NOT REACHED ({r['simp']*100:+.1f}%)")
    print()

print("Note: 'Simple return' = sum of per-trade PnL (fixed position size, no reinvestment).")
print("      'Compounded return' = product of (1+pnl) (full reinvestment of all profits).")
print("      Reality is between these two depending on reinvestment policy.")
