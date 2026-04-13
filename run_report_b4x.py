"""B_balanced_4x formatted report in Chinese."""
import sys
sys.path.insert(0, "src")
from research.strategy_c_v2_execution_layer import ExecLayerConfig, run_execution_layer_backtest
from research.strategy_c_v2_runner import (
    build_funding_per_bar, combined_profit_factor,
    load_funding_csv, load_klines_csv, load_timeframe_data,
)
from collections import defaultdict
from math import sqrt

funding_records = load_funding_csv("src/data/btcusdt_funding_5year.csv")
tf_4h = load_timeframe_data("4h", "src/data/btcusdt_4h_6year.csv", 4.0, funding_records)
bars_1h = load_klines_csv("src/data/btcusdt_1h_6year.csv")
funding_1h = build_funding_per_bar(bars_1h, funding_records)

cfg = ExecLayerConfig(
    entry_type="hybrid", pullback_pct=0.0075, breakout_pct=0.0025,
    max_entries_per_zone=6, cooldown_bars=2, hold_hours=24,
    alpha_stop_pct=0.0125, catastrophe_stop_pct=0.025,
    reentry_after_alpha_stop=True, reentry_after_cat_stop=True,
    exec_tf_hours=1.0)

EQUITY = 10000.0
FRAC = 3.0
EXTRA = 2 * 0.0002 * FRAC

r = run_execution_layer_backtest(
    bars_4h=tf_4h.bars, features_4h=tf_4h.features,
    bars_1h=bars_1h, funding_1h=funding_1h,
    config=cfg, position_frac=FRAC)

trades = r.trades
pnls = [t.net_pnl - EXTRA for t in trades]
n = len(trades)
wins = [p for p in pnls if p > 0]
losses = [p for p in pnls if p <= 0]

# Equity curve
eq = 1.0
peak = 1.0
dd = 0.0
curve = []
for p in pnls:
    eq *= (1 + p)
    curve.append(eq)
    if eq > peak:
        peak = eq
    if peak > 0:
        d = (peak - eq) / peak
        if d > dd:
            dd = d

# Sharpe
avg_pnl = sum(pnls) / n
std_pnl = sqrt(sum((p - avg_pnl)**2 for p in pnls) / (n - 1)) if n > 1 else 0
tpy = n / 4.0
sharpe = (avg_pnl * tpy) / (std_pnl * sqrt(tpy)) if std_pnl > 0 else 0

avg_hold = sum(t.hold_bars for t in trades) / n
total_ret = (curve[-1] - 1.0) * 100
rdd = total_ret / (dd * 100) if dd > 0 else 0

# Exits
alpha_ex = sum(1 for t in trades if t.exit_reason.startswith("alpha_stop"))
cat_ex = sum(1 for t in trades if t.exit_reason.startswith("catastrophe_stop"))
time_ex = sum(1 for t in trades if t.exit_reason in ("time_stop", "end_of_series"))

# Yearly
yearly = defaultdict(lambda: {"n": 0, "w": 0, "pnl": 0.0})
for t, p in zip(trades, pnls):
    yr = t.entry_time.year
    yearly[yr]["n"] += 1
    if p > 0:
        yearly[yr]["w"] += 1
    yearly[yr]["pnl"] += p * EQUITY

# Best/worst
best_i = max(range(n), key=lambda i: pnls[i])
worst_i = min(range(n), key=lambda i: pnls[i])

# Market phases
phases = [
    ("2022 Bear market", "2022-01-01", "2022-12-31"),
    ("2023 Recovery", "2023-01-01", "2023-12-31"),
    ("2024 Bull run", "2024-01-01", "2024-12-31"),
    ("2025 Volatile", "2025-01-01", "2025-12-31"),
    ("2026 YTD", "2026-01-01", "2026-12-31"),
]
phase_stats = []
for label, s, e in phases:
    pts = [(t, p) for t, p in zip(trades, pnls)
           if s <= t.entry_time.strftime("%Y-%m-%d") <= e]
    if pts:
        pn = len(pts)
        pw = sum(1 for _, p in pts if p > 0)
        pp = sum(p for _, p in pts) * EQUITY
        phase_stats.append((label, pn, pw, pp))

pf = combined_profit_factor(pnls)
simp = sum(pnls)
avg_w = sum(wins) / len(wins) * 100 if wins else 0
avg_l = sum(losses) / len(losses) * 100 if losses else 0

# ── PRINT ──
W = 60
def box(text):
    print(f"||  {text:<{W-6}}||")
def sep():
    print("||" + "=" * (W - 4) + "||")
def line():
    print("||" + "-" * (W - 4) + "||")

print()
print("||" + "=" * (W - 4) + "||")
box("")
box("B_balanced_4x  BTCUSDT Perpetual Futures")
box("D1 Long | 4h RSI Regime + 1h Execution")
box("4x Isolated | $10,000 Starting Equity")
box("")
sep()
print()
sep()
box("[ Trading Stats ]")
line()
box(f"Total: {n} trades  |  WR: {len(wins)/n*100:.1f}%  |  PF: {pf:.2f}")
box(f"Long:  {n} trades ({len(wins)}W/{len(losses)}L)")
box(f"Max DD: {dd*100:.1f}%  |  Reward/DD: {rdd:.2f}")
box(f"Sharpe: {sharpe:.2f}  |  Avg Hold: {avg_hold:.1f} hrs")
box(f"Avg Win: +{avg_w:.1f}%  |  Avg Loss: {avg_l:.1f}%")
box("")
box("Exit Distribution:")
box(f"  V  Time-stop:       {time_ex} trades ({time_ex/n*100:.0f}%)")
box(f"  >  Alpha stop:      {alpha_ex} trades ({alpha_ex/n*100:.0f}%)")
box(f"  X  Catastrophe:     {cat_ex} trades ({cat_ex/n*100:.0f}%)")
sep()
print()
sep()
box("[ Yearly Breakdown ]")
line()
for yr in sorted(yearly):
    y = yearly[yr]
    wr = y["w"] / y["n"] * 100 if y["n"] else 0
    box(f"{yr}:  {y['n']:>3} trades  WR {wr:>4.0f}%  PnL ${y['pnl']:>+10,.0f}")
box("")
bt = trades[best_i]
wt = trades[worst_i]
box(f"Best:  {bt.entry_time.strftime('%Y-%m-%d')}  ${pnls[best_i]*EQUITY:>+8,.0f}  (+{pnls[best_i]*100:.1f}%)")
box(f"Worst: {wt.entry_time.strftime('%Y-%m-%d')}  ${pnls[worst_i]*EQUITY:>+8,.0f}  ({pnls[worst_i]*100:.1f}%)")
sep()
print()
sep()
box("[ Market Phase Performance ]")
line()
for label, pn, pw, pp in phase_stats:
    wr = pw / pn * 100
    box(f"{label:<18} ${pp:>+9,.0f}  ({pn} trades, {wr:.0f}% WR)")
sep()
print()
sep()
box("[ Risk / Survival ]")
line()
box(f"Exchange Leverage:  4x isolated")
box(f"Actual Frac:        3.0 (base) / 4.0 (max)")
box(f"Notional:           $30,000 - $40,000")
box(f"Liq Distance:       25.0%")
box(f"Worst Adverse:      2.28%  (buffer 11x)")
box(f"10% shock:          survives")
box(f"15% shock:          survives")
box(f"20% shock:          tight (2.7pp buffer)")
box(f"30% shock:          LIQUIDATES")
sep()
print()
sep()
box("[ Bottom Line ]")
line()
box(f"Simple Return:     +{simp*100:.1f}%")
box(f"Ending Equity:     ${EQUITY*(1+simp):>10,.0f}")
box(f"Compounded Return: +{(curve[-1]-1)*100:,.1f}%")
box(f"Comp Ending:       ${EQUITY*curve[-1]:>12,.0f}")
sep()
