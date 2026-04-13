# Strategy C v2 — Phase 8 Canonical Baseline (Leveraged Futures Sleeves)

_Date: 2026-04-12_
_Phase: 8_
_Deliverable: 1 of 6_
_Status: BLOCKING for Phase 8 paper deployment — must be clean before
any live config code ships._

<!-- canonical-metrics
cell: D1_long_primary
source: canonical
oos_return: 1.4345
max_dd: 0.1297
num_trades: 73
profit_factor: 2.23
worst_trade_pnl: -0.0568
worst_adverse_move: 0.0651
positive_windows: 7
stops_fired: 22
-->

<!-- canonical-metrics
cell: D1_long_dynamic
source: canonical
oos_return: 1.6432
max_dd: 0.1481
num_trades: 73
profit_factor: 2.17
worst_trade_pnl: -0.0774
worst_adverse_move: 0.0651
positive_windows: 7
stops_fired: 22
-->

<!-- canonical-metrics
cell: D1_long_dynamic_adaptive
source: canonical
oos_return: 2.0455
max_dd: 0.1636
num_trades: 64
profit_factor: 2.35
worst_trade_pnl: -0.0774
worst_adverse_move: 0.0651
positive_windows: 6
stops_fired: 24
-->

<!-- canonical-metrics
cell: D1_long_frac2_shadow
source: canonical
oos_return: 2.5913
max_dd: 0.1909
num_trades: 73
profit_factor: 2.23
worst_trade_pnl: -0.0851
worst_adverse_move: 0.0651
positive_windows: 7
stops_fired: 22
-->

<!-- canonical-metrics
cell: C_long_backup
source: canonical
oos_return: 1.0626
max_dd: 0.1810
num_trades: 178
profit_factor: 1.70
worst_trade_pnl: -0.0662
worst_adverse_move: 0.0736
positive_windows: 6
stops_fired: 17
-->

<!-- canonical-metrics
cell: C_long_dynamic
source: canonical
oos_return: 1.3597
max_dd: 0.1708
num_trades: 178
profit_factor: 1.79
worst_trade_pnl: -0.0723
worst_adverse_move: 0.0736
positive_windows: 6
stops_fired: 17
-->

## 0. What this document is

This is the canonical Phase 8 baseline for the six leveraged-futures
sleeves that make up the Strategy C v2 deployment stack. It has two
strictly-separated layers:

- **Layer 1 — Strategy-level leveraged futures result** (§2–§7).
  Every number in this layer is the raw strategy output on a 2x
  leveraged BTCUSDT perpetual sleeve at its intended `actual_frac`
  and full account allocation. This is the deployment goal and the
  "true" performance characteristic of each cell.
- **Layer 2 — Optional portfolio allocation layer** (§8). A
  discussion of whether each sleeve should be deployed at 0.25x,
  0.5x, or 1.0x of account equity. This is a SEPARATE decision on
  top of the strategy-level result and must never be conflated
  with it in reports.

The three concepts are kept strictly separate throughout this
document:

| # | Concept | Values (Phase 8) |
|---|---------|------------------|
| 1 | **Exchange leverage** | 2x (isolated) on Binance USDT-M for every cell |
| 2 | **actual_frac** | Strategy's effective notional / sleeve equity; varies by cell (1.000 / 1.333 / 2.000; dynamic cells span a range) |
| 3 | **Portfolio allocation** | Account-level sleeve size; discussed separately in §8 |

Reports that state return / DD / trades / PF / worst trade without
explicitly labelling which of these three concepts they refer to
are **non-compliant** with the Phase 8 reporting contract.

## 1. Why this document exists

The Phase 6 final-recommendation report listed D1_long_primary at
**+173.06% OOS / DD 9.27%**. Phase 7 parity, Phase 7 retrospective,
and the entire `manual_edge_extraction` branch all independently
measured D1_long_primary at **+143.45% OOS / DD 12.97%**. Eight
independent measurements agree on the lower number. The higher
number is a transcription-error fabrication that does not
correspond to any row in any research data.

Going forward:
1. Every canonical cell lives in
   `src/strategies/strategy_c_v2_canonical_baseline.py` as a
   `CanonicalCell` with machine-readable config + metrics.
2. Reports declare their numeric claims in
   `<!-- canonical-metrics -->` blocks cross-checked by
   `strategy_c_v2_report_consistency`. The guard fails the build on
   any drift.
3. `run_v2_backtest`, the retrospective paper runner, and
   `compute_monitor_state` all call the **shared**
   `strategy_c_v2_dynamic_sizing` module for sizing / adaptive hold
   logic — no re-implementation allowed.
4. The parity test `tests/test_strategy_c_v2_parity.py` verifies the
   three code paths produce identical trades on a fixed historical
   slice. This gate must pass before the real paper cron starts.

---

# Layer 1 — Strategy-level leveraged futures result

## 2. The six leveraged futures sleeves

| Cell | Role | Exchange Leverage | actual_frac | Stop Config | Stop Semantics |
|------|------|------------------:|------------:|-------------|----------------|
| **D1_long_primary** | primary | 2x | 1.333 (fixed) | 1.5% | strategy_close_stop |
| **D1_long_dynamic** | shadow | 2x | 1.333 × [0.5, 1.5] = [0.667, 2.000] | 1.5% | strategy_close_stop |
| **D1_long_dynamic_adaptive** | shadow | 2x | 1.333 × [0.5, 1.5] = [0.667, 2.000] | 1.5% | strategy_close_stop |
| **D1_long_frac2_shadow** | shadow | 2x | 2.000 (fixed, max) | 1.5% | strategy_close_stop |
| **C_long_backup** | backup | 2x | 1.000 (fixed) | 2% | strategy_close_stop |
| **C_long_dynamic** | shadow | 2x | 1.000 × [0.5, 1.5] = [0.500, 1.500] | 2% | strategy_close_stop |

Every cell runs on Binance USDT-M BTCUSDT perpetual at 2x isolated
leverage. The strategy owns the sleeve equity at 1.0x portfolio
allocation — the canonical metrics in §4 are all computed on this
full-sleeve-equity assumption. Allocation layering is §8 only.

## 3. Cell configuration — full params

### 3.1 Shared parameters across all 6 cells

| Parameter | Value |
|-----------|-------|
| Instrument | BTCUSDT perpetual (Binance USDT-M) |
| Bar interval | 4h |
| Walk-forward window | 24m train / 6m test / 6m step |
| OOS test windows | 8 (covering 2022-04 → 2026-04) |
| Cost — fee | 0.05% per side |
| Cost — slip | 0.01% per side |
| Cost — round-trip drag | 0.12% × actual_frac |
| Funding | Real Binance 5-year USDT-M funding CSV at settlement bars |
| Exchange leverage | 2x (isolated) |
| Portfolio allocation for canonical | 1.0 (full sleeve) |
| Stop slippage on fill | 0.0% (close-trigger → next-open fill) |

### 3.2 D1_long family (signal: rsi_only_20 long, hold 11, stop 1.5%)

All four D1_long cells share the same base signal stream. The
difference is only sizing and hold logic.

| Parameter | Value |
|-----------|-------|
| Signal family | `rsi_only_20 > 70` long entries |
| RSI period | 20 |
| Side | long only |
| Base hold | 11 bars |
| Stop loss | 1.5% (close trigger) |
| Risk per trade | 2.0% of sleeve equity |
| Base `actual_frac` | `min(risk / stop, exchange_leverage)` = `min(0.02 / 0.015, 2.0)` = **1.333** |

Cell-specific differences:

- **D1_long_primary**: `actual_frac=1.333` fixed, `hold_bars=11` fixed
- **D1_long_dynamic**: base 1.333 × 4-component conviction
  multiplier in [0.5, 1.5] → `actual_frac ∈ [0.667, 2.000]`,
  `hold_bars=11` fixed
- **D1_long_dynamic_adaptive**: same dynamic sizing + 3-component
  adaptive hold score → `hold_bars ∈ {6, 11, 16}`
- **D1_long_frac2_shadow**: `actual_frac=2.000` fixed (= full
  exchange leverage cap), `hold_bars=11` fixed. Fully margined on
  every trade.

### 3.3 C_long family (signal: rsi_and_macd_14 long, hold 4, stop 2%)

| Parameter | Value |
|-----------|-------|
| Signal family | `rsi_and_macd_14` long (RSI(14) > 70 AND MACD hist > 0) |
| RSI period | 14 |
| Side | long only |
| Base hold | 4 bars |
| Stop loss | 2.0% (close trigger) |
| Risk per trade | 2.0% of sleeve equity |
| Base `actual_frac` | `min(0.02 / 0.02, 2.0)` = **1.000** |

- **C_long_backup**: `actual_frac=1.000` fixed, `hold_bars=4` fixed
- **C_long_dynamic**: base 1.000 × multiplier in [0.5, 1.5] →
  `actual_frac ∈ [0.500, 1.500]`, `hold_bars=4` fixed. NOT paired
  with adaptive hold (manual_edge study: adaptive hold on C_long
  collapses return by ~58 pp because the 4-bar base hold is already
  the structural optimum).

## 4. Canonical OOS metrics (leveraged futures, portfolio_allocation=1.0)

**Every number in this section is the strategy-level leveraged
futures result at the cell's intended `actual_frac` on a 2x isolated
perpetual sleeve with full account allocation. This is Layer 1.**

### 4.1 Full leveraged-futures results table

| Cell | Lev | actual_frac | Trades | OOS Return | Max DD | PF | Worst Trade | Liq Safety |
|------|----:|------------:|-------:|-----------:|-------:|---:|------------:|-----------|
| **D1_long_primary** | 2x | 1.333 | 73 | **+143.45%** | 12.97% | 2.23 | −5.68% | liq@50% / worst_adv=6.51% / buffer=7.68x |
| **D1_long_dynamic** | 2x | [0.667, 2.000] | 73 | **+164.32%** | 14.81% | 2.17 | −7.74% | liq@50% / worst_adv=6.51% / buffer=7.68x |
| **D1_long_dynamic_adaptive** | 2x | [0.667, 2.000] | 64 | **+204.55%** | 16.36% | 2.35 | −7.74% | liq@50% / worst_adv=6.51% / buffer=7.68x |
| **D1_long_frac2_shadow** | 2x | 2.000 | 73 | **+259.13%** | 19.09% | 2.23 | −8.51% | liq@50% / worst_adv=6.51% / buffer=7.68x |
| **C_long_backup** | 2x | 1.000 | 178 | **+106.26%** | 18.10% | 1.70 | −6.62% | liq@50% / worst_adv=7.36% / buffer=6.79x |
| **C_long_dynamic** | 2x | [0.500, 1.500] | 178 | **+135.97%** | 17.08% | 1.79 | −7.23% | liq@50% / worst_adv=7.36% / buffer=6.79x |

### 4.2 Notes on the table

1. **Liquidation safety column** — `liq@50%` is the approximate
   liquidation adverse move on 2x isolated (= 1 / exchange_leverage
   = 50%, ignoring maintenance margin). `worst_adv` is the worst
   observed adverse excursion on any trade in the 4-year OOS
   walk-forward. `buffer=Nx` is `liq / worst_adv` — how many times
   worse the worst trade would have to be to trigger liquidation.
   Phase 8 policy: a cell must have buffer ≥ 3x to be considered
   safe. All 6 cells clear this threshold.

2. **PF is invariant to frac scaling** — D1_long_primary and
   D1_long_frac2_shadow both show PF 2.23 because profit factor is
   a gross_wins / gross_losses ratio, and scaling all trade PnLs by
   the same frac multiplier cancels in the ratio.

3. **Trade count identical across sizing variants of the same
   family** — D1_long cells at frac=1.333, dynamic, and frac=2.0
   all show 73 trades because sizing does not change the signal
   stream. Only `D1_long_dynamic_adaptive` shows 64 trades because
   adaptive hold modulates the per-trade hold duration and this
   collapses some follow-on signals that would otherwise have
   triggered during the longer baseline hold.

4. **Worst adverse move identical across sizing variants** — it's
   a property of the signal and the price path, not of how large
   the position is. D1_long variants all show 6.51%; C_long
   variants all show 7.36%.

5. **Worst trade scales with frac** — at frac=2.0, the worst trade
   is 1.5x worse than at frac=1.333 (−8.51% vs −5.68%) because
   trade PnL scales linearly with frac.

### 4.3 Deltas between strategy variants

All deltas are at portfolio_allocation=1.0 (layer 1 only).

**D1_long triplet vs primary:**

| Cell | Δ Return | Δ DD | Δ Worst | Note |
|------|---------:|-----:|--------:|------|
| dynamic | +20.87 pp | +1.84 pp | −2.06 pp | sizing alone |
| dynamic_adaptive | +61.10 pp | +3.39 pp | −2.06 pp | sizing + adaptive hold |
| frac2_shadow | **+115.68 pp** | **+6.12 pp** | **−2.83 pp** | max frac, fixed |

**C_long pair vs backup:**

| Cell | Δ Return | Δ DD | Δ Worst | Note |
|------|---------:|-----:|--------:|------|
| dynamic | +29.71 pp | **−1.02 pp** | −0.61 pp | sizing alone |

Only `C_long_dynamic` improves DD while adding return — every
D1_long modifier trades DD for return.

## 5. Cost and funding assumptions

### 5.1 Round-trip cost drag

```
round_trip_pct = (fee + slip) * 2 = (0.0005 + 0.0001) * 2 = 0.0012
cost_drag_per_trade = round_trip_pct * actual_frac
```

Per-trade cost drag by cell (signal-bar case):

| Cell | actual_frac | per-trade cost drag |
|------|------------:|--------------------:|
| D1_long_primary | 1.333 | 0.160% |
| D1_long_dynamic | 0.667 – 2.000 (mean ≈ 1.333) | 0.080% – 0.240% |
| D1_long_dynamic_adaptive | 0.667 – 2.000 (mean ≈ 1.333) | 0.080% – 0.240% |
| D1_long_frac2_shadow | 2.000 | 0.240% |
| C_long_backup | 1.000 | 0.120% |
| C_long_dynamic | 0.500 – 1.500 (mean ≈ 1.000) | 0.060% – 0.180% |

### 5.2 Funding

Real Binance USDT-M 5-year historical funding CSV is loaded and
applied at each settlement bar (every 8 hours) for any position
open during the settlement. Formula:

```
funding_payment = position_notional * funding_rate
                = (sleeve_equity * actual_frac) * funding_rate
```

Phase 3 established the asymmetric funding-veto rule (shorts only,
never longs). This is preserved in both the baseline and the
dynamic variants via `compute_monitor_state`.

### 5.3 Stop semantics

**All 6 cells use `strategy_close_stop`**. At each bar close, if
`(side == long AND close ≤ entry × (1 − stop_pct))` or
`(side == short AND close ≥ entry × (1 + stop_pct))`, the stop
fires. The fill executes at the **next bar's open price**.
Stop slippage on fill = 0%.

## 6. Walk-forward details

| Parameter | Value |
|-----------|-------|
| Train window | 24 months |
| Test window | 6 months |
| Step | 6 months |
| Total test coverage | 48 months (8 × 6m OOS windows) |

OOS return is compounded across all 8 windows — equity is continuous
from window to window, with no reset. Max DD is the largest
peak-to-trough drawdown on the stitched equity curve. This
methodology is identical to Phase 4 / 5A / 6 / 7 / manual_edge.

## 7. Phase 8 canonical commitment

The following numbers are **immutable** for Phase 8 promotion /
halt decisions. They represent the strategy-level leveraged
futures result at `portfolio_allocation=1.0`.

### 7.1 Primary deployment sleeve

**D1_long_primary**: 2x leveraged perpetual futures sleeve,
actual_frac=1.333, +143.45% OOS / DD 12.97% / 73 trades / PF 2.23 /
worst trade −5.68% / pos win 7/8 / stops 22 / liq buffer 7.68x

### 7.2 Backup deployment sleeve

**C_long_backup**: 2x leveraged perpetual futures sleeve,
actual_frac=1.000, +106.26% OOS / DD 18.10% / 178 trades / PF 1.70 /
worst trade −6.62% / pos win 6/8 / stops 17 / liq buffer 6.79x

### 7.3 Shadow sleeves — expected retrospective outcomes

**D1_long_dynamic**: +164.32% / DD 14.81% / 73 trades / PF 2.17 /
worst −7.74%. Δ vs primary: **+20.87 pp return, +1.84 pp DD**.

**D1_long_dynamic_adaptive**: +204.55% / DD 16.36% / 64 trades /
PF 2.35 / worst −7.74%. Δ vs primary: **+61.10 pp return, +3.39 pp DD**.

**D1_long_frac2_shadow**: +259.13% / DD 19.09% / 73 trades / PF 2.23 /
worst −8.51%. Δ vs primary: **+115.68 pp return, +6.12 pp DD**.

**C_long_dynamic**: +135.97% / DD 17.08% / 178 trades / PF 1.79 /
worst −7.23%. Δ vs backup: **+29.71 pp return, −1.02 pp DD**.

### 7.4 Promotion rules (anchored to layer-1 numbers)

A shadow sleeve PROMOTES to primary only if, after 30 days of
paper telemetry at its full leveraged config:

1. Realized paper PnL per trade within ±1σ of expected per-trade
   PnL implied by the §7.3 canonical return
2. Realized paper worst-trade within 1.5x the retrospective
   worst-trade for the cell
3. Realized slippage < 0.3% per fill on average
4. Stop-semantics divergence fires < 3 times in 30 days
5. Liquidation buffer stays ≥ 3x on observed adverse moves
6. For dynamic cells: multiplier output stays in [0.5, 1.5], median
   near 1.0
7. For frac2_shadow: no single adverse excursion exceeds 15% during
   the 30-day window (gives the cell 35% of remaining headroom
   before liquidation)

A shadow sleeve is HALTED if:

1. Realized paper PnL per trade worse than 2σ below expected
2. Any single realized trade exceeds 2x the retrospective worst
3. Dynamic sizing output observed outside [0.5, 1.5] (bug)
4. Live fill price diverges from paper model by > 0.5% on 2+ fills
   in any rolling 7-day window
5. Liquidation buffer drops below 3x at any point

---

# Layer 2 — Optional portfolio allocation layer

## 8. Sleeve sizing — how much account to give each sleeve

**This is a SEPARATE decision on top of the strategy-level result in
§4. It does not modify the canonical metrics. It only determines
what fraction of the total account each sleeve runs on.**

The canonical metrics in §4 assume `portfolio_allocation = 1.0` (the
sleeve runs on 100% of the account). That is also what Phase 8
paper deployment will use: each sleeve runs at full allocation
inside its own virtual sub-account so the strategy-level result is
directly measurable in paper.

### 8.1 Why deploy at full allocation in paper

The deployment and testing goal is to validate the strategy as
true leveraged perpetual futures logic. Running each sleeve at a
reduced allocation (e.g., 0.5x) during paper would:

1. Blur the strategy-level result with an allocation-driven
   dilution effect
2. Make the promotion rules in §7.4 harder to apply because the
   realized numbers would need to be un-diluted before comparing
3. Hide tail-event stress in the sleeve — a smaller allocation
   makes the worst drawdown look smaller in account terms even
   though the sleeve itself is just as stressed

The correct ordering is: validate the sleeve at 1.0x first, then
discuss account-level allocation as a separate deployment
decision after the sleeve-level validation is in hand.

### 8.2 Approximate account-level impact at fractional allocation

For reference only. These numbers are first-order linear
approximations — compounded returns do not scale perfectly
linearly, but the approximation is close enough for allocation
sizing decisions.

| Cell | Strategy Return (1.0x) | @ 0.5x Account | @ 0.25x Account |
|------|-----------------------:|---------------:|----------------:|
| D1_long_primary | +143.45% | ≈ +71.73% | ≈ +35.86% |
| D1_long_dynamic | +164.32% | ≈ +82.16% | ≈ +41.08% |
| D1_long_dynamic_adaptive | +204.55% | ≈ +102.28% | ≈ +51.14% |
| D1_long_frac2_shadow | +259.13% | ≈ +129.57% | ≈ +64.78% |
| C_long_backup | +106.26% | ≈ +53.13% | ≈ +26.57% |
| C_long_dynamic | +135.97% | ≈ +67.99% | ≈ +33.99% |

And max DD at fractional allocation:

| Cell | Strategy DD (1.0x) | @ 0.5x Account | @ 0.25x Account |
|------|-------------------:|---------------:|----------------:|
| D1_long_primary | 12.97% | ≈ 6.49% | ≈ 3.24% |
| D1_long_dynamic | 14.81% | ≈ 7.41% | ≈ 3.70% |
| D1_long_dynamic_adaptive | 16.36% | ≈ 8.18% | ≈ 4.09% |
| D1_long_frac2_shadow | 19.09% | ≈ 9.55% | ≈ 4.77% |
| C_long_backup | 18.10% | ≈ 9.05% | ≈ 4.53% |
| C_long_dynamic | 17.08% | ≈ 8.54% | ≈ 4.27% |

Note: PF, positive windows, trade count, and worst_adverse_move
are **not** scalable — they are properties of the strategy, not
of the allocation. A sleeve deployed at 0.5x still shows the same
178 trades, the same 1.70 PF, and the same 7.36% worst adverse
move. Only account-level return / DD / worst trade PnL scale with
allocation.

### 8.3 When allocation matters

The account-level allocation decision is appropriate when:

- Multiple sleeves run simultaneously and compete for account
  equity (not the Phase 8 paper case — each sleeve runs in its
  own virtual sub-account)
- The user wants to blend a canonical sleeve with other strategies
  outside Strategy C v2
- The user wants to reduce tail-event exposure below the sleeve's
  intrinsic worst-trade at the cost of return

**Phase 8 paper deployment intentionally uses allocation = 1.0 for
every sleeve in its own sub-account.** The comparison against §7.3
is done at the strategy-level layer. Allocation sizing is a
post-validation decision that follows Phase 8.

---

## 9. Forensic audit of the Phase 6 discrepancy

### 9.1 What Phase 6 recommendation said

> D1_long_primary: +173.06% OOS compounded return, max DD 9.27%,
> 73 trades, PF 2.48, positive windows 7/8

### 9.2 Ground truth (8 independent measurements)

+143.45% / DD 12.97% / 73 trades / PF 2.23 / pos 7/8 / worst −5.68%

Agreed by:
- Phase 6 expanded_sweep.csv (close-stop row)
- Phase 7 stop-semantics parity study
- Phase 7 retrospective simulation
- manual_edge_extraction regime filter baseline
- manual_edge_extraction dynamic sizing baseline
- manual_edge_extraction pyramiding baseline
- manual_edge_extraction adaptive exit baseline
- Phase 8 fresh canonical run (2026-04-12)

### 9.3 Search for the fabricated number

An exhaustive search of `strategy_c_v2_phase6_expanded_sweep.csv`
for any row with `170 ≤ oos_return ≤ 176` or `9.0 ≤ max_dd ≤ 9.5`
on a D1_long-family row: **zero matches**. The highest D1_long
return in the entire Phase 6 sweep is the wick-trigger variant at
+163.52% (which is the `exchange_intrabar_stop` semantics, not
`strategy_close_stop`).

Conclusion: the Phase 6 recommendation headline is a pure
transcription error. It does not correspond to any row in any
research data. The correct D1_long_primary number is +143.45% /
12.97% — locked in this document and in `CANONICAL_CELLS`.

### 9.4 Downstream impact

None. Phase 7 and manual_edge_extraction both loaded the Phase 6
CSV directly (not the recommendation narrative) and used the
correct +143.45% number. The error was confined to one narrative
paragraph in one report file.

### 9.5 Why this can't happen again

1. `CANONICAL_CELLS` in
   `src/strategies/strategy_c_v2_canonical_baseline.py` is the
   single source of truth. Any recommendation number must resolve
   through a canonical cell lookup or a specific CSV row citation.
2. The machine-readable `<!-- canonical-metrics -->` blocks in
   every Phase 8 report are checked by
   `strategy_c_v2_report_consistency`. The guard fails the build
   on any drift between a block value and the canonical record.
3. Tests in `tests/test_strategy_c_v2_canonical_baseline.py`
   explicitly pin the six canonical metric sets AND explicitly
   reject any future edit that tries to restore +173.06% / 9.27%
   (see `test_d1_long_primary_return_is_not_phase6_recommendation`).

---

## 10. Sign-off conditions for Phase 8 paper deployment

Before the first paper-trading cron job runs, the following gates
must all be green:

1. ✅ This reconciliation report exists (delivered — this document)
2. ✅ `strategy_c_v2_canonical_baseline.py` SSoT module committed
   with all 6 cells and liquidation safety helpers
3. ✅ `strategy_c_v2_dynamic_sizing.py` shared module committed;
   backtester, retrospective runner, and live monitor all call it
4. ✅ `strategy_c_v2_report_consistency.py` guard committed; this
   report passes the guard (self-check test)
5. ✅ `MonitorConfig` / `MonitorState` / `LivePositionState`
   extended with `use_dynamic_sizing`, `base_frac`,
   `dynamic_sizing_config`, `use_adaptive_hold`,
   `adaptive_hold_config`, `stop_loss_pct`, `stop_semantics`,
   `actual_frac`, `sizing_multiplier`, `sizing_components`,
   `hold_bars_override`, `hold_regime`, `stop_level`, and
   `LivePositionState.max_hold_override`
6. ✅ Parity test `tests/test_strategy_c_v2_parity.py` passes:
   backtest vs retrospective vs live monitor produce identical
   trades on fixed historical slices for all 6 cells' configs
7. ⏳ Retrospective paper run on each of the 6 cells over a 200+
   bar real data slice, writing `PaperTradeLogEntry` output for
   comparison against §7.3 canonical numbers
8. ⏳ Dry-run of the cron integration for one trading day with no
   live orders

Gates 7-8 are the next work items. All prior gates are green. The
canonical baseline is locked. The next operational step is to
instantiate each of the 6 cells as its own virtual sub-account for
paper telemetry.

---

## 11. Summary

| Item | Status |
|------|--------|
| Phase 6 +173.06% fabrication | **Confirmed as transcription error** |
| Canonical baseline for 6 sleeves | **Locked** in `CANONICAL_CELLS` |
| Three concepts (leverage / frac / allocation) | **Strictly separated** |
| Layer 1 strategy-level results | §2–§7 |
| Layer 2 allocation layer | §8 (separate, optional) |
| SSoT module | `strategy_c_v2_canonical_baseline.py` |
| Report guard | `strategy_c_v2_report_consistency.py`, self-check passes |
| Shared sizing / adaptive hold module | `strategy_c_v2_dynamic_sizing.py` |
| Parity gate | `tests/test_strategy_c_v2_parity.py`, 10 tests green |
| Deployment blocker cleared | ✅ for gates 1–6, ⏳ for gates 7–8 |

Phase 8 may proceed to gates 7-8: retrospective paper run on real
data for each of the 6 sleeves in its own virtual sub-account, at
`portfolio_allocation = 1.0`, using the strategy-level canonical
metrics in §4 as the comparison reference.
