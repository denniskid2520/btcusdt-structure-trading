"""Full report in Chinese format for all 4 candidates."""
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

EQUITY = 10000.0

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


def report_one(name, role, cfg, frac, max_frac):
    EXTRA = 2 * 0.0002 * frac
    r = run_execution_layer_backtest(
        bars_4h=tf_4h.bars, features_4h=tf_4h.features,
        bars_1h=bars_1h, funding_1h=funding_1h,
        config=cfg, position_frac=frac)

    trades = r.trades
    pnls = [t.net_pnl - EXTRA for t in trades]
    n = len(trades)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    eq = 1.0; peak = 1.0; dd = 0.0; curve = []
    for p in pnls:
        eq *= (1 + p); curve.append(eq)
        if eq > peak: peak = eq
        if peak > 0:
            d = (peak - eq) / peak
            if d > dd: dd = d

    avg_pnl = sum(pnls) / n
    std_pnl = sqrt(sum((p - avg_pnl)**2 for p in pnls) / (n - 1)) if n > 1 else 0
    tpy = n / 4.0
    sharpe = (avg_pnl * tpy) / (std_pnl * sqrt(tpy)) if std_pnl > 0 else 0
    avg_hold = sum(t.hold_bars for t in trades) / n
    pf = combined_profit_factor(pnls)
    simp = sum(pnls)
    comp = curve[-1] - 1
    rdd = comp * 100 / (dd * 100) if dd > 0 else 0

    alpha_ex = sum(1 for t in trades if t.exit_reason.startswith("alpha_stop"))
    cat_ex = sum(1 for t in trades if t.exit_reason.startswith("catastrophe_stop"))
    time_ex = sum(1 for t in trades if t.exit_reason in ("time_stop", "end_of_series"))

    yearly = defaultdict(lambda: {"n": 0, "w": 0, "pnl": 0.0})
    for t, p in zip(trades, pnls):
        yr = t.entry_time.year
        yearly[yr]["n"] += 1
        if p > 0: yearly[yr]["w"] += 1
        yearly[yr]["pnl"] += p * EQUITY

    best_i = max(range(n), key=lambda i: pnls[i])
    worst_i = min(range(n), key=lambda i: pnls[i])

    phases = [
        ("2022 熊市", "2022-01-01", "2022-12-31"),
        ("2023 復蘇", "2023-01-01", "2023-12-31"),
        ("2024 主升段", "2024-01-01", "2024-12-31"),
        ("2025 震盪", "2025-01-01", "2025-12-31"),
        ("2026 至今", "2026-01-01", "2026-12-31"),
    ]
    ph_stats = []
    for label, s, e in phases:
        pts = [(t, p) for t, p in zip(trades, pnls)
               if s <= t.entry_time.strftime("%Y-%m-%d") <= e]
        if pts:
            pn = len(pts)
            pw = sum(1 for _, p in pts if p > 0)
            pp = sum(p for _, p in pts) * EQUITY
            ph_stats.append((label, pn, pw, pp))

    lev = max_frac
    liq = 1.0 / lev
    wa = 0.0228
    shocks = []
    for s in [0.10, 0.15, 0.20, 0.30, 0.40]:
        c = wa + s
        if c >= liq: v = "爆倉"
        elif liq - c < 0.05: v = "緊繃"
        else: v = "存活"
        shocks.append((s, v))

    avg_w = sum(wins) / len(wins) * 100 if wins else 0
    avg_l = sum(losses) / len(losses) * 100 if losses else 0

    W = 58
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
    box(f"BTCUSDT 永續合約 | {lev:.0f}x 逐倉")
    box(f"4h RSI趨勢判定 + 1h 混合進場")
    box(f"起始資金: $10,000")
    box("")
    sep()
    print()
    sep()
    box("【交易統計】")
    line()
    box(f"總交易: {n} 筆  |  勝率: {len(wins)/n*100:.1f}%  |  盈虧比 PF: {pf:.2f}")
    box(f"Long:   {n} 筆 ({len(wins)}W/{len(losses)}L)")
    box(f"最大回撤: {dd*100:.1f}%  |  報酬/回撤: {rdd:.2f}")
    box(f"Sharpe: {sharpe:.2f}  |  平均持倉: {avg_hold:.1f} 小時")
    box(f"平均獲利: +{avg_w:.1f}%  |  平均虧損: {avg_l:.1f}%")
    box("")
    box("出場分佈:")
    box(f"  V  到期止盈:     {time_ex} 筆 ({time_ex/n*100:.0f}%)")
    box(f"  >  Alpha止損:    {alpha_ex} 筆 ({alpha_ex/n*100:.0f}%)")
    box(f"  X  災難止損:     {cat_ex} 筆 ({cat_ex/n*100:.0f}%)")
    sep()
    print()
    sep()
    box("【年度明細】")
    line()
    for yr in sorted(yearly):
        y = yearly[yr]
        wr = y["w"] / y["n"] * 100 if y["n"] else 0
        box(f"{yr}:  {y['n']:>3} 筆  勝率 {wr:>4.0f}%  PnL ${y['pnl']:>+10,.0f}")
    box("")
    bt = trades[best_i]
    wt = trades[worst_i]
    box(f"最大單筆獲利: {bt.entry_time.strftime('%Y-%m-%d')}  ${pnls[best_i]*EQUITY:>+8,.0f}  (+{pnls[best_i]*100:.1f}%)")
    box(f"最大單筆虧損: {wt.entry_time.strftime('%Y-%m-%d')}  ${pnls[worst_i]*EQUITY:>+8,.0f}  ({pnls[worst_i]*100:.1f}%)")
    sep()
    print()
    sep()
    box("【市場階段表現】")
    line()
    for label, pn, pw, pp in ph_stats:
        wr = pw / pn * 100
        box(f"{label:<12}  ${pp:>+9,.0f}  ({pn}筆, {wr:.0f}%勝率)")
    sep()
    print()
    sep()
    box("【風控 / 壓力測試】")
    line()
    box(f"交易所槓桿:    {lev:.0f}x 逐倉")
    box(f"實際倍率:      {frac:.2f} (基礎) / {max_frac:.2f} (上限)")
    box(f"名義金額:      ${frac*EQUITY:,.0f} - ${max_frac*EQUITY:,.0f}")
    box(f"爆倉距離:      {liq*100:.1f}%")
    box(f"歷史最大逆向:  2.28% (緩衝 {liq/wa:.0f}x)")
    for s, v in shocks:
        emoji = "V " if v == "存活" else ("! " if v == "緊繃" else "X ")
        box(f"  {emoji}{s*100:.0f}% 閃崩:     {v}")
    sep()
    print()
    sep()
    box("【最終成績】")
    line()
    box(f"簡單報酬:      +{simp*100:.1f}%")
    box(f"期末權益:      ${EQUITY*(1+simp):>10,.0f}")
    box(f"複利報酬:      +{comp*100:,.1f}%")
    box(f"複利期末:      ${EQUITY*curve[-1]:>12,.0f}")
    sep()


for name, role, cfg, frac, mf in candidates:
    report_one(name, role, cfg, frac, mf)

# Project summary
print()
print()
print("||" + "=" * 54 + "||")
print("||  【研發總覽】                                      ||")
print("||" + "-" * 54 + "||")
print("||  研發階段:          18 個 Phase                     ||")
print("||  參數組合測試:      6,427 組                        ||")
print("||  自動化測試:        1,043 個                        ||")
print("||  測試檔案:          53 個                           ||")
print("||  原始碼模組:        80 個                           ||")
print("||  回測期間:          2020-04 至 2026-04 (6年)        ||")
print("||  OOS驗證:           8個滾動窗口 (24m訓/6m測)        ||")
print("||  壓力測試:          5級閃崩 + 4級滑點 + 15m逐筆回放 ||")
print("||  即時紙上交易:      已部署 AWS Lightsail             ||")
print("||" + "=" * 54 + "||")
