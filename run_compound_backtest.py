"""True compounded backtest — equity grows/shrinks after each trade.

Each trade's notional = current_equity * actual_frac.
Profits are reinvested. Losses reduce future position size.
This is the real path a $10,000 account would follow.

Runs all 4 candidates.
"""
import sys
sys.path.insert(0, "src")
from research.strategy_c_v2_execution_layer import ExecLayerConfig, run_execution_layer_backtest
from research.strategy_c_v2_runner import (
    build_funding_per_bar, combined_profit_factor,
    load_funding_csv, load_klines_csv, load_timeframe_data,
)
from collections import defaultdict
from math import sqrt

print("Loading data...")
funding_records = load_funding_csv("src/data/btcusdt_funding_5year.csv")
tf_4h = load_timeframe_data("4h", "src/data/btcusdt_4h_6year.csv", 4.0, funding_records)
bars_1h = load_klines_csv("src/data/btcusdt_1h_6year.csv")
funding_1h = build_funding_per_bar(bars_1h, funding_records)

STARTING = 10000.0
EXTRA_COST_PER_FRAC = 2 * 0.0002  # exec-aware cost

candidates = [
    ("B_balanced_4x", "FINAL", ExecLayerConfig(
        entry_type="hybrid", pullback_pct=0.0075, breakout_pct=0.0025,
        max_entries_per_zone=6, cooldown_bars=2, hold_hours=24,
        alpha_stop_pct=0.0125, catastrophe_stop_pct=0.025,
        reentry_after_alpha_stop=True, reentry_after_cat_stop=True,
        exec_tf_hours=1.0), 3.0, 4.0),
    ("B_balanced_3x", "FALLBACK", ExecLayerConfig(
        entry_type="hybrid", pullback_pct=0.0075, breakout_pct=0.0025,
        max_entries_per_zone=6, cooldown_bars=2, hold_hours=24,
        alpha_stop_pct=0.0125, catastrophe_stop_pct=0.025,
        reentry_after_alpha_stop=True, reentry_after_cat_stop=True,
        exec_tf_hours=1.0), 2.0, 3.0),
    ("A_density_4x", "HIGH-SAMPLE", ExecLayerConfig(
        entry_type="hybrid", pullback_pct=0.0075, breakout_pct=0.005,
        max_entries_per_zone=6, cooldown_bars=2, hold_hours=8,
        alpha_stop_pct=0.0125, catastrophe_stop_pct=0.025,
        reentry_after_alpha_stop=True, reentry_after_cat_stop=True,
        exec_tf_hours=1.0), 3.0, 4.0),
    ("B_balanced_5x", "HIGH-RETURN", ExecLayerConfig(
        entry_type="hybrid", pullback_pct=0.0075, breakout_pct=0.0025,
        max_entries_per_zone=6, cooldown_bars=2, hold_hours=24,
        alpha_stop_pct=0.0125, catastrophe_stop_pct=0.025,
        reentry_after_alpha_stop=True, reentry_after_cat_stop=True,
        exec_tf_hours=1.0), 3.33, 5.0),
]

W = 62

for name, role, cfg, frac, max_frac in candidates:
    EXTRA = EXTRA_COST_PER_FRAC * frac
    r = run_execution_layer_backtest(
        bars_4h=tf_4h.bars, features_4h=tf_4h.features,
        bars_1h=bars_1h, funding_1h=funding_1h,
        config=cfg, position_frac=frac)

    trades = r.trades
    pnls = [t.net_pnl - EXTRA for t in trades]
    n = len(trades)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    # True compounded equity path
    equity = STARTING
    peak_eq = STARTING
    max_dd_usd = 0.0
    max_dd_pct = 0.0
    curve = [equity]
    yearly = defaultdict(lambda: {"n": 0, "w": 0, "start_eq": 0.0, "end_eq": 0.0, "trades": []})
    trade_details = []

    for i, (t, pnl_frac) in enumerate(zip(trades, pnls)):
        yr = t.entry_time.year
        if yearly[yr]["n"] == 0:
            yearly[yr]["start_eq"] = equity

        # This trade's real USD PnL (based on CURRENT equity)
        trade_pnl_usd = equity * pnl_frac
        equity += trade_pnl_usd
        if equity < 0:
            equity = 0  # wiped

        curve.append(equity)
        yearly[yr]["n"] += 1
        if pnl_frac > 0:
            yearly[yr]["w"] += 1
        yearly[yr]["end_eq"] = equity
        yearly[yr]["trades"].append(trade_pnl_usd)

        # Track DD
        if equity > peak_eq:
            peak_eq = equity
        dd_usd = peak_eq - equity
        dd_pct = dd_usd / peak_eq if peak_eq > 0 else 0
        if dd_usd > max_dd_usd:
            max_dd_usd = dd_usd
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct

        # Notional for this trade
        notional = (equity - trade_pnl_usd) * frac  # equity at entry time * frac
        trade_details.append({
            "date": t.entry_time.strftime("%Y-%m-%d %H:%M"),
            "entry": t.entry_price,
            "exit": t.exit_price,
            "pnl_pct": pnl_frac * 100,
            "pnl_usd": trade_pnl_usd,
            "equity_after": equity,
            "notional": notional,
            "exit_reason": t.exit_reason,
        })

    # Sharpe
    pnl_usds = [td["pnl_usd"] for td in trade_details]
    avg_pnl_usd = sum(pnl_usds) / n
    std_pnl_usd = sqrt(sum((p - avg_pnl_usd)**2 for p in pnl_usds) / (n - 1)) if n > 1 else 0
    tpy = n / 4.0
    sharpe = (avg_pnl_usd * tpy) / (std_pnl_usd * sqrt(tpy)) if std_pnl_usd > 0 else 0

    total_return = (equity - STARTING) / STARTING * 100
    pf = combined_profit_factor(pnls)

    alpha_ex = sum(1 for t in trades if t.exit_reason.startswith("alpha_stop"))
    cat_ex = sum(1 for t in trades if t.exit_reason.startswith("catastrophe_stop"))
    time_ex = n - alpha_ex - cat_ex

    avg_w = sum(wins) / len(wins) * 100 if wins else 0
    avg_l = sum(losses) / len(losses) * 100 if losses else 0

    best_i = max(range(n), key=lambda i: trade_details[i]["pnl_usd"])
    worst_i = min(range(n), key=lambda i: trade_details[i]["pnl_usd"])

    # Market phases
    phases_def = [
        ("2022 Bear", "2022-01-01", "2022-12-31"),
        ("2023 Recovery", "2023-01-01", "2023-12-31"),
        ("2024 Bull", "2024-01-01", "2024-12-31"),
        ("2025 Volatile", "2025-01-01", "2025-12-31"),
        ("2026 YTD", "2026-01-01", "2026-12-31"),
    ]
    ph_stats = []
    for label, s, e in phases_def:
        pts = [td for td, t in zip(trade_details, trades) if s <= t.entry_time.strftime("%Y-%m-%d") <= e]
        if pts:
            pn = len(pts)
            pw = sum(1 for td in pts if td["pnl_usd"] > 0)
            pp = sum(td["pnl_usd"] for td in pts)
            ph_stats.append((label, pn, pw, pp))

    lev = max_frac
    liq = 1.0 / lev

    # ── PRINT ──
    def box(t):
        print(f"||  {t:<{W-6}}||")
    def sep():
        print("||" + "=" * (W - 4) + "||")
    def line():
        print("||" + "-" * (W - 4) + "||")

    print()
    sep()
    box("")
    box(f"{name}  [{role}]")
    box(f"BTCUSDT Perpetual | {lev:.0f}x Isolated")
    box(f"TRUE COMPOUNDED BACKTEST | $10,000 Start")
    box(f"Profits reinvested, position size grows with equity")
    box("")
    sep()

    print()
    sep()
    box("[ Trading Stats ]")
    line()
    box(f"Total: {n} trades  |  WR: {len(wins)/n*100:.1f}%  |  PF: {pf:.2f}")
    box(f"Long:  {n} trades ({len(wins)}W/{len(losses)}L)")
    box(f"Max DD: {max_dd_pct*100:.1f}% (${max_dd_usd:,.0f})")
    box(f"Sharpe: {sharpe:.2f}  |  Avg Hold: {sum(t.hold_bars for t in trades)/n:.1f} hrs")
    box(f"Avg Win: +{avg_w:.1f}%  |  Avg Loss: {avg_l:.1f}%")
    box("")
    box("Exit Distribution:")
    box(f"  V  Time-stop:      {time_ex:>3} trades ({time_ex/n*100:.0f}%)")
    box(f"  >  Alpha stop:     {alpha_ex:>3} trades ({alpha_ex/n*100:.0f}%)")
    box(f"  X  Catastrophe:    {cat_ex:>3} trades ({cat_ex/n*100:.0f}%)")
    sep()

    print()
    sep()
    box("[ Yearly Breakdown (TRUE COMPOUNDED) ]")
    line()
    for yr in sorted(yearly):
        y = yearly[yr]
        wr = y["w"] / y["n"] * 100 if y["n"] else 0
        yr_pnl = sum(y["trades"])
        yr_ret = yr_pnl / y["start_eq"] * 100 if y["start_eq"] > 0 else 0
        sign = "+" if yr_pnl >= 0 else ""
        box(f"{yr}: {y['n']:>3} trades  WR {wr:>4.0f}%  PnL {sign}${yr_pnl:>12,.0f}  ({sign}{yr_ret:.1f}%)")
        box(f"      Start: ${y['start_eq']:>12,.0f}  ->  End: ${y['end_eq']:>12,.0f}")
    box("")
    bt = trade_details[best_i]
    wt = trade_details[worst_i]
    box(f"Best:  {bt['date'][:10]}  +${bt['pnl_usd']:>10,.0f}  (+{bt['pnl_pct']:.1f}%)")
    box(f"Worst: {wt['date'][:10]}  ${wt['pnl_usd']:>10,.0f}  ({wt['pnl_pct']:.1f}%)")
    sep()

    print()
    sep()
    box("[ Market Phase Performance (COMPOUNDED USD) ]")
    line()
    for label, pn, pw, pp in ph_stats:
        wr = pw / pn * 100
        sign = "+" if pp >= 0 else ""
        box(f"{label:<18} {sign}${pp:>12,.0f}  ({pn} trades, {wr:.0f}% WR)")
    sep()

    print()
    sep()
    box("[ Equity Milestones ]")
    line()
    milestones = [20000, 50000, 100000, 250000, 500000, 1000000, 5000000, 10000000]
    for m in milestones:
        hit = next((i for i, eq in enumerate(curve) if eq >= m), None)
        if hit is not None:
            t = trades[min(hit-1, n-1)]
            box(f"  ${m:>12,}  reached at trade #{hit} ({t.entry_time.strftime('%Y-%m-%d')})")
    box(f"  FINAL:  ${equity:>12,.0f}  after {n} trades")
    sep()

    print()
    sep()
    box("[ Risk / Survival ]")
    line()
    box(f"Exchange Leverage:   {lev:.0f}x isolated")
    box(f"Actual Frac:         {frac:.2f} (base) / {max_frac:.2f} (max)")
    box(f"Peak Notional:       ${peak_eq * max_frac:>12,.0f}")
    box(f"Liq Distance:        {liq*100:.1f}%")
    box(f"Worst Adverse:       2.28% (buffer {liq/0.0228:.0f}x)")
    if lev <= 3:
        shocks = [(10,"survives"),(15,"survives"),(20,"survives"),(30,"tight"),(40,"LIQUIDATES")]
    elif lev <= 4:
        shocks = [(10,"survives"),(15,"survives"),(20,"tight"),(30,"LIQUIDATES"),(40,"LIQUIDATES")]
    else:
        shocks = [(10,"survives"),(15,"tight"),(20,"LIQUIDATES"),(30,"LIQUIDATES"),(40,"LIQUIDATES")]
    for s, v in shocks:
        icon = "V" if v == "survives" else ("!" if v == "tight" else "X")
        box(f"  {icon}  {s}% shock:        {v}")
    sep()

    print()
    sep()
    box("[ BOTTOM LINE - TRUE COMPOUNDED ]")
    line()
    box(f"Starting Equity:    ${STARTING:>14,.0f}")
    box(f"Ending Equity:      ${equity:>14,.0f}")
    box(f"Total Return:       +{total_return:,.1f}%")
    box(f"Total Profit:       ${equity - STARTING:>14,.0f}")
    box(f"Max Drawdown:       {max_dd_pct*100:.1f}% (${max_dd_usd:>10,.0f})")
    sep()

    # Last 10 trades detail
    print()
    sep()
    box("[ Last 10 Trades ]")
    line()
    box(f"{'Date':<12} {'Entry':>9} {'Exit':>9} {'PnL%':>6} {'PnL$':>11} {'Equity':>12} {'Exit':>10}")
    for td in trade_details[-10:]:
        sign = "+" if td["pnl_usd"] >= 0 else ""
        box(f"{td['date'][:10]:<12} {td['entry']:>9,.1f} {td['exit']:>9,.1f} {td['pnl_pct']:>+5.1f}% {sign}${td['pnl_usd']:>9,.0f} ${td['equity_after']:>11,.0f} {td['exit_reason'][:10]:>10}")
    sep()
    print()
    print()
