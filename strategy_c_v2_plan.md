# Strategy C v2 — 5-Year Walk-Forward Research Program

_Date: 2026-04-11_
_Worktree: `claude/strategy-c-orderflow`_
_Status: Phase 1 in progress (plan + data foundation + deliverables #1-2)_

---

## 1. Why v2 exists

Strategy C Baselines A/B/C are closed out as **useful negative evidence**, not live
candidates. Each one was a short-window (47-day or 83-day), single-slice, percentile-
swept rule stack on 15m order-flow composites. The findings were consistent:

- **Baseline A** (slow 4-condition confirmation): 427 trades over 47 days, 32% win,
  **-45.3% equity**, Sharpe -0.32. Entered too late to survive a 0.12% round-trip.
- **Baseline B** (precision percentiles): the high-precision holdout cells had
  **n=8 trades** — unusable sample sizes, false winners.
- **Baseline C** (return/frequency balanced, 3 score families × 360 cells × 2 datasets):
  - Best 47d cell: Hybrid pct=90/h=4/cd=2/stress=1.0, holdout **-4.33%** vs B&H -5.41%,
    n=112, pf=0.80, dd=8.84%, exposure 31.5%.
  - Best 83d cell: Reversal pct=95/h=4/cd=0, holdout **-4.98%** vs B&H -5.99%, n=149,
    pf=0.84.
  - Winning family is window-dependent (Hybrid on 47d, Reversal on 83d) — not robust.
  - Pareto frontier slopes *down*: higher frequency → worse return, every time.
  - Train systematically *worse* than holdout → likely regime shift or
    percentile-calibration survivorship, not overfitting.
  - Continuation is the worst family on both datasets (robust negative finding).
  - `pair_cvd` remains redundant with `taker_delta`. Drop verdict maintained.

The A/B/C program already answered one question definitively: **short-window
15m composite-score sweeps are cost-dominated and window-fragile**. There is no
reason to run another percentile sweep on the same 47/83-day slices.

What v2 does differently, in one line: **longer history, rolling walk-forward,
multi-timeframe features, literature-benchmarked baselines, and a cost-aware score
model — not just another percentile grid.**

---

## 2. Research objective

> Maximize **out-of-sample compounded return** for a BTCUSDT perpetual strategy
> on 15m execution, while maintaining a meaningful trade count and an acceptable
> drawdown. Do **not** optimize for win rate alone.

Primary metrics (all OOS, aggregated across walk-forward test windows):
1. Compounded return (the leaderboard key)
2. Trade count (must be meaningful — reject tiny-sample cells)
3. Max drawdown
4. Profit factor
5. Fraction of OOS windows with positive compounded return (robustness)

Secondary:
- OOS Sharpe / Sortino (trade-level)
- Avg net return per trade after cost
- Average hold bars, exposure time
- Turnover

Explicit non-goals:
- Win rate alone
- Precision at the expense of frequency (A/B/C already falsified this)
- Any result that leans on a single small holdout slice

---

## 3. Architectural decision: two tracks

**The Coinglass STANDARD plan hard-limits 15m history to ~90 days.** This is a
binding constraint, not a preference. It means a single unified 5-year feature
matrix including Coinglass pair-level endpoints is impossible. If we force it,
we either (a) shrink the whole program to 90 days — reverting to the exact
regime A/B/C already failed at — or (b) forward-fill Coinglass features from
nothing for 99% of the walk-forward and pretend it's real data.

Neither is acceptable. So v2 decomposes into two tracks, run in parallel:

### Track A — Binance-only, 5-year full walk-forward

- **History window:** 2020-04-05 → 2026-04-05 (≈6 years, >5 usable after 1y warmup)
- **Data:** Binance USDT-M perp klines 15m/1h/4h/1d + `/fapi/v1/fundingRate` history
  + Binance OI history where available. *No Coinglass features in this track.*
- **Strategies:** all four families (literature, continuation, reversal-without-liq,
  regime-switch hybrid) — the reversal variant here fires on price/volume reversal
  signals only, no Coinglass liquidation stream.
- **Validation:** rolling walk-forward, **24m train / 6m test**, step 6m. Aggregate
  all OOS test windows into a single OOS return stream. This is the leaderboard
  track. **OOS compounded return on Track A is the v2 success metric.**

### Track B — Binance + Coinglass, 90-day overlap only

- **History window:** last 90 days that Coinglass STANDARD 15m history supports.
- **Data:** Everything in Track A *plus* Coinglass supplemental endpoints where
  they have overlap — liquidations, imbalance, taker delta, L/S ratios, cross-ex
  basis, OI-weighted funding, funding spread, stablecoin OI, Coinbase premium.
  `pair_cvd` stays dropped by default (Baseline C verdict).
- **Strategies:** all four families, but with Coinglass features switched on.
  The liquidation-reversal family is **only** meaningful on Track B since it
  needs the liquidation cascade stream.
- **Validation:** single temporal 70/30 split (we do not have room for walk-forward
  in 90 days). Min-trade guardrails from Baseline C: min_train=30, min_holdout=15.
  Track B does *not* produce a leaderboard result. It only answers deliverable #8:
  **does adding Coinglass features, when they're available, produce a measurable lift
  on top of the Track A Binance-only feature set for the same 90-day slice?**

### Why this mapping is honest

- User deliverable #8 is literally "Binance-only vs Binance+Coinglass comparison".
  The two-track architecture is a direct implementation of that deliverable, not a
  workaround.
- Track A gives us the 5-year OOS equity curve the user asked for (deliverable #9).
- Track B gives us the Coinglass marginal-lift answer on the only window where the
  comparison is fair (the 90-day overlap).
- Neither track pretends to have data it doesn't.

---

## 4. Feature families

Per user spec, organised by source availability.

### (A) Binance price/technical — full 6-year history, both tracks

Computed on 15m bars with derived 1h/4h/1d features via multi-timeframe lookback.

| Family        | Features                                               |
|---------------|--------------------------------------------------------|
| Returns       | ret_1, ret_4, ret_8, ret_16, ret_32 (log or pct)       |
| Realized vol  | rv_1h, rv_4h, rv_1d, rv_7d (rolling std of log returns)|
| Momentum      | mom_30 (close vs close-30)                             |
| RSI           | rsi_14, rsi_30 (Wilder, already implemented)           |
| MACD          | macd, macd_signal, macd_hist (12/26/9, already impl.)  |
| Stochastic    | stoch_k_30, stoch_d_30, stoch_k_200, stoch_d_200       |
| Moving avgs   | sma_20, sma_50, sma_200, ema_20, ema_50, ema_200       |
| MA ratios     | close/sma_20, close/sma_50, close/sma_200, ema cross   |
| Bollinger     | bb_mid, bb_upper, bb_lower, bb_width, bb_pctb (20, 2.0)|
| ATR           | atr_14, atr_30 (Wilder)                                |
| Calendar      | hour_of_day, day_of_week, is_weekend                   |

**Missing primitives to build this session:** Bollinger Bands, Stochastic, ATR.
RSI/MACD/EMA already exist in `src/indicators/volume_profile.py` and
`src/research/macro_cycle.py`.

### (B) Binance perp structure — coverage varies, both tracks

| Feature             | Source                           | History horizon         |
|---------------------|----------------------------------|-------------------------|
| funding_rate        | `/fapi/v1/fundingRate`           | 5-year (need to wire)   |
| bars_to_next_funding| Derived                          | 5-year                  |
| funding_cum_24h     | Rolling sum                      | 5-year                  |
| open_interest       | `/futures/data/openInterestHist` | ~30d only (Binance lim) |
| mark_vs_last        | markPrice - close                | Live only (no history)  |
| basis_perp_vs_spot  | perp_close - spot_close          | 5-year if spot fetched  |

**Honest caveats:**
- Binance `/futures/data/openInterestHist` is capped at ~30 days. 5-year OI history
  would need a third-party archive or Coinglass (which is 90-day). For Track A,
  OI features are either (a) omitted, (b) limited to the last 30-90 days
  (asymmetric across the walk-forward), or (c) sourced from a separate archive.
  **Phase 1 punts on this** — we build the walk-forward without OI for Track A
  and add it explicitly in a Phase-3 ablation.
- `mark_vs_last` has no usable history. Drop for Track A, include for Track B.
- `basis_perp_vs_spot` *does* have 5-year history if we fetch Binance spot
  BTCUSDT 15m. This is a Phase-2 add-on.

### (C) Coinglass supplemental — **Track B only** (90-day overlap)

| Feature                     | Endpoint                                  |
|-----------------------------|-------------------------------------------|
| long_liq_z32                | `/api/futures/liquidation/aggregated-history` |
| short_liq_z32               | same                                      |
| liq_imbalance               | derived                                   |
| taker_delta_norm_z32        | `/api/futures/taker-buy-sell-volume/history` |
| fr_close_z96                | `/api/futures/funding-rate/history`       |
| fr_spread_z96               | derived (OI-weighted vs vanilla)          |
| oi_weighted_funding         | `/api/futures/funding-rate/ohlc-history`  |
| stablecoin_oi               | `/api/stablecoin-margin-oi`               |
| cross_ex_basis              | derived                                   |
| long_short_ratio            | `/api/futures/long-short-ratio/history`   |
| top_trader_ls               | `/api/futures/top-long-short-account-ratio` |
| coinbase_premium            | third-party, Phase 4 only                 |

`pair_cvd` explicitly **dropped** — Baseline C confirmed redundancy with
`taker_delta_norm_z32`.

---

## 5. Strategy families

All four families target the same objective — OOS compounded return on the
walk-forward — but come from different hypotheses about *why* 15m BTC has
edge.

### F1. Literature benchmark — RSI/MACD (Stefaniuk-style)

Rule-based baseline drawing from the survey the user supplied. Not expected
to be the winner; it exists to **give every other family a number to beat**
and to validate that our walk-forward machinery reproduces the literature
result range on our data.

- RSI(14) + MACD(12,26,9) both long and short
- Trend-following interpretation: RSI > 70 = strong momentum long, RSI < 30 = short,
  with MACD histogram confirmation (Stefaniuk's exact rule, run on 15m and 1h).
- Cost: 0.12% round-trip (fee + slippage) + funding.

### F2. Multi-timeframe continuation

Hypothesis: 15m entries should only fire when the higher-timeframe trend and
momentum agree. The 4h / 1h filter decides *direction*; the 15m stream decides
*timing*.

- Filter: 4h EMA(50) > EMA(200) for longs (mirror for shorts), 1h RSI > 50.
- Trigger: 15m momentum burst + volume spike, entered at t+1 open.
- Exit: fixed hold (4/8/16 bars) OR opposite filter flip OR ATR-based stop.

### F3. Liquidation-reversal with flow-flip confirmation (**Track B only**)

Hypothesis: forced liquidation cascades create short-horizon price reversals
once aggregate taker flow flips in the opposite direction.

- Trigger: `long_liq_z32` spike ≥ Nσ
- Confirm: `taker_delta_norm_z32` flips from negative to positive within K bars
- Entry: t+1 open, long
- Exit: fixed hold (4/8/16) OR flow re-flips
- Mirror for shorts on `short_liq_z32` spikes

### F4. Regime-switch hybrid

Hypothesis: the *right* strategy depends on the volatility / funding regime.

- Regime gate: rolling RV(1h) percentile AND `|funding|` percentile
- High-stress regime → use F3 (reversal) if Track B, else F2 with tighter stops
- Calm regime → use F2 (continuation)
- Switching is deterministic (no state, just a per-bar rule)

---

## 6. Modeling upgrade

After all four rule-based families are measured on the walk-forward, we add a
**cost-aware score model** as a ranker:

- Input: feature matrix (A + B, optionally C on Track B)
- Target: realized next-K-bar return after subtracting round-trip cost and
  funding, where K ∈ {8, 16, 32}
- Model: logistic regression first (interpretable, cheap, stable), then
  gradient-boosted trees (LightGBM or XGBoost) if logistic is insufficient
- Training: per-walk-forward-window (retrain on each 24m train slice)
- Prediction: score every bar, rank by expected return after cost
- Signal: top-quantile rank → long; bottom-quantile → short; cooldown applied

The score model lives in Phase 4. Phases 1-3 are rule-based only, so we always
have a readable baseline even if the model underperforms.

---

## 7. Validation protocol

### Track A — rolling walk-forward

- Train window: 24 months (~70,000 bars @ 15m)
- Test window: 6 months (~17,500 bars @ 15m)
- Step: 6 months (non-overlapping test windows)
- Total test windows: `(total_bars - 24m) / 6m` ≈ 8–9 fully rebuilt windows
  across 5 usable years
- **Every decision** (threshold, percentile, weight, hyperparameter) is computed
  on the 24m train slice only. The 6m test slice is scored cold.
- Aggregate: concatenate all test slices into a single contiguous OOS equity
  curve. This is the v2 OOS return number.

### Track B — single 70/30 split (reused from Baseline C)

- `temporal_split(series, train_frac=0.70)` — already in
  `src/research/strategy_c_sweep.py`, well-tested.
- Min-trade guardrails: `passes_min_trades(row, min_train=30, min_holdout=15)` —
  already in the same module.

### Trade execution model

Same contract as Baseline A/B/C, unchanged:
- Entry at **t+1 open** after signal bar close
- Exit at **t+hold open** (fixed hold) or opposite-flip whichever first
- Cost per round-trip: **0.12%** = 2 × (0.05% fee + 0.01% slippage)
- Plus per-bar **funding**: `funding_rate * side * (bars_held / funding_interval)`
- Leverage: **1x notional** for the leaderboard (no margin stacking). Leverage
  is a deployment decision layered on top, not a backtest parameter we optimise.

### Rejection rules

- Any cell with OOS test-window trade count < 30 is marked "insufficient" and
  excluded from the leaderboard (A/B/C lesson).
- Any cell whose OOS equity curve is dominated by a single outlier bar is
  flagged (check: max single-trade PnL vs total PnL).
- Any strategy whose rule-based version beats its model version is the winner —
  simpler model wins ties.

---

## 8. Phase decomposition and session scoping

This is a multi-session research program. Nine deliverables will not fit in
one context window, and pretending otherwise would produce either a rushed plan
or a rushed implementation. Explicit scope per phase:

### Phase 1 — Plan + data foundation + deliverables #1-2 (**this session**)

1. ✅ `strategy_c_v2_plan.md` (this file)
2. TDD wire `BinanceFuturesAdapter.fetch_funding_rate_history` against
   `/fapi/v1/fundingRate`
3. Backfill `src/data/btcusdt_funding_5year.csv` covering 2020-04 → 2026-04
4. TDD add Bollinger Bands, Stochastic, ATR to `src/indicators/`
5. Scaffold `src/data/strategy_c_v2_dataset.py` and
   `src/data/strategy_c_v2_features.py` with dataclass + signatures only (no
   feature implementations yet — Phase 2)
6. Write deliverable #1 — `strategy_c_v2_data_coverage.md`: full table of every
   data source, its history horizon, hard gaps, and what it blocks
7. Write deliverable #2 — `strategy_c_v2_feature_matrix.md`: per-feature
   timeframe / source / history / track membership

**Phase 1 success criteria:** `btcusdt_funding_5year.csv` exists and passes a
chronological-ordering sanity check; all new indicator primitives pass TDD;
deliverables #1 and #2 are honest about every gap.

### Phase 2 — First trustworthy 5-year OOS benchmark report (**this session**)

Narrowed scope per user brief 2026-04-11: the one goal is to produce the first
trustworthy 5-year out-of-sample benchmark report for Strategy C v2. No more
data plumbing unless strictly required — reuse the on-disk 6-year Binance
OHLCV and the 5-year funding CSV from Phase 1.

**Priority order inside Phase 2:**

1. **Rolling walk-forward harness** — `src/research/strategy_c_v2_walk_forward.py`
   - 24 months train / 6 months test, step 6 months
   - Strict temporal splits, no shuffling
   - No leakage from normalisation or indicator warmups
   - Features generated per split OR with honest purge/warmup handling
   - Aggregate every OOS test window into one combined report

2. **Cost + funding aware backtester** — `src/research/strategy_c_v2_backtest.py`
   - 1x notional only
   - Fees + slippage + funding cashflows included
   - Funding applied **only** when the position is held through the funding
     timestamp (not pro-rated)
   - Signal timeframe may differ from execution timeframe
   - 15m execution with 15m / 1h / 4h signal frames
   - Hold horizons: 4 / 8 / 16 / 32 bars
   - Exits: time-stop OR opposite-signal flip

3. **Track A feature module implementation** — replaces the Phase 1 stub in
   `src/data/strategy_c_v2_features.py`
   - Returns 1/4/8/16/32, RV 1h/4h/1d/7d, RSI 14/30, MACD + signal, MOM 30,
     Stochastic k30/d30/k200/d200, SMA/EMA ratios, Bollinger, ATR,
     hour-of-day, weekday
   - Funding rate features from the 5-year Binance funding history CSV
   - **Skip OI** in Track A — no honest 5-year coverage, Phase 1 decision

4. **Literature benchmark family** — `src/strategies/strategy_c_v2_literature.py`
   - RSI-only, MACD-only, RSI+MACD, buy-and-hold, flat (no-trade)
   - Run under the **same** cost model on 15m, 1h, 4h
   - Purpose: identify which timeframe is least cost-dominated and set the
     number every future family has to beat

5. **First 5-year OOS leaderboard** — runs walk-forward across all cells
   and ranks by compounded return, max DD, profit factor, trade count,
   fraction of positive OOS windows, exposure time
   - Explicit: **not** ranked by win rate alone
   - Explicit: **reject** cells with tiny OOS trade counts

6. **Recommendation** — which timeframe and which family deserve the
   next research cycle, written up as a deliverable

**Phase 2 explicit non-goals (constraints):**

- No `pair_cvd` reintroduced into the default Track A stack
- No Track B (Binance+Coinglass 90-day) mixed into model selection yet
- No XGBoost / complex modelling until literature benchmark and
  multi-timeframe rule baselines are complete
- No global one-shot optimisation over the full 5 years

**Phase 2 deliverables (files produced):**

- `strategy_c_v2_literature_benchmark.md` — literature benchmark report
  across 15m / 1h / 4h (Deliverable #3, expanded scope)
- `strategy_c_v2_oos_leaderboard.md` — first 5-year OOS leaderboard
  (Deliverable #4, promoted earlier than original plan)
- `strategy_c_v2_next_cycle_recommendation.md` — TF + family recommendation
  for the next research cycle

### Phase 3 — Convert Phase 2 findings into a scalable strategy framework (**this session**)

User brief 2026-04-11 (post Phase 2 acceptance):

> Convert the benchmark findings into a scalable strategy framework that
> improves return robustness, preserves meaningful trade count, and reduces
> drawdown.

Anchor Phase 2 findings (do not re-litigate):
- 15m rule-based alpha is shelved — 15m becomes **execution-only**, not a
  primary alpha discovery frame.
- 4h is the strongest alpha candidate. Best cell: `rsi_only_30 hold=16`
  → +138% OOS, 13.9% DD — but only 52 trades (thin).
- 1h is the strongest robustness / trade-count candidate. Best cell:
  `rsi_and_macd_14 hold=32` → +106% OOS, 41.6% DD, 510 trades, 7/8
  positive windows.
- 4-year perp long pays ~28% funding; funding is a first-class variable,
  not a rounding error.

**Phase 3 priority order:**

1. **Robustness study** on the Phase 2 winners
   - 4h rsi_only_30 hold=16: perturb RSI length {21, 30, 34, 42}, hold
     {8, 12, 16, 24, 32}
   - 1h rsi_and_macd_14 hold=32: perturb RSI length and MACD gate
     modestly, hold {16, 24, 32, 48}
   - Report: is the edge broad or point-fragile?

2. **Directional decomposition**
   - For each winning cell, produce long-only, short-only, long-short
     variants
   - Understand where the edge comes from under perp funding costs

3. **Funding-aware strategy design**
   - Treat funding as a regime feature, not just a cashflow
   - Entry veto in extreme positive funding regimes (longs)
   - Entry veto in deeply negative funding (shorts)
   - Funding-spread veto / filter
   - Measure impact on return, DD, exposure time

4. **Multi-timeframe framework**
   - 4h = regime / direction
   - 1h = setup confirmation
   - 15m = execution timing only (NOT alpha discovery)
   - Test trade-off improvement across return, trades, DD, exposure

5. **First Track B Coinglass overlay test**
   - Coinglass as overlay on top of best Track A, NOT as primary trigger
   - Features: stablecoin OI change, OI-weighted funding, funding spread,
     liquidation imbalance, taker buy/sell delta, Coinbase premium
   - Used as entry veto / regime veto / exit refinement
   - On 90-day overlap only

6. **Cost-aware score model** (only after steps 1-5)
   - Logistic regression first, then gradient boosting if ready
   - Targets: next 8/16/32 bar net return after cost, or positive-return binary
   - Only on the promoted 4h/1h candidate family — not the full search space

**Phase 3 explicit non-goals:**
- No new data plumbing unless strictly required
- No more 15m percentile sweeps
- No thin single-cell winner promoted without robustness testing
- No score model before the rule-based framework lands

**Phase 3 deliverables (files produced):**
- `strategy_c_v2_phase3_robustness.md` — robustness report for 4h and 1h winners
- `strategy_c_v2_phase3_directional.md` — long / short / long-short decomposition
- `strategy_c_v2_phase3_funding_filter.md` — funding-aware filter report
- `strategy_c_v2_phase3_mtf.md` — multi-timeframe framework report
- `strategy_c_v2_phase3_coinglass_overlay.md` — Track A vs Track A+Coinglass
- `strategy_c_v2_phase3_recommendation.md` — primary Strategy C candidate pick

### Phase 4 — Candidate consolidation (**this session**)

User brief 2026-04-12 (post Phase 3 acceptance):

> Turn the current 4h winners into deployment-ready candidates using
> robustness testing, directional decomposition, exit refinement, and
> real-time monitoring logic. Real-time data for live monitoring,
> execution timing, and risk vetoes — NOT for daily re-optimization.

Locked findings (do NOT re-litigate):
- 15m is execution-only
- 4h is primary alpha
- Long is dominant edge
- MACD-only is filter, not trigger
- Funding is first-class
- Coinglass is overlay-only, must prove additive on Track A

**Phase 4 priority order:**

1. **Candidate consolidation** — report A, B, C explicitly with OOS
   return, max DD, PF, trade count, exposure, positive windows, AND
   funding drag contribution (new column)
   - A: 4h rsi_only_21 hold=12 (side=both)
   - B: 4h rsi_only_30 hold=16 (side=both)
   - C: 4h rsi_and_macd_14 hold=4 long-only

2. **Robustness band test** — small perturbations around each candidate:
   RSI period ±, hold ±, long-only vs both, time-stop vs opposite-flip
   exit, ATR trailing stop vs none. Establishes that the candidate is
   a broad optimum, not a single-cell pick.

3. **Exit refinement** — ATR trailing stop tests. Can DD be reduced
   below 15% without sacrificing more than 20 pp of return?

4. **Live monitoring design** — logic for live regime state, live
   signal state, hostile funding / crowding veto, early exit, paper-
   trading state tracking. **Design only**, not automated re-training.

5. **Coinglass overlay on 4h candidates** — only as entry veto /
   regime veto / exit refinement. Must prove additive on 4h Track A.

6. **Production recommendation** — primary + backup for paper deploy.

**Phase 4 non-goals:**
- No daily re-optimization from live data
- No new strategy families beyond A, B, C
- No 15m alpha discovery
- No XGBoost yet (rule-based first)
- No Coinglass replacing the main trigger

**Phase 4 deliverables:**
- `strategy_c_v2_phase4_candidates.md` — candidate comparison table
- `strategy_c_v2_phase4_robustness_band.md` — robustness band report
- `strategy_c_v2_phase4_exit_refinement.md` — ATR trailing stop report
- `strategy_c_v2_phase4_live_monitoring.md` — live monitoring design
- `strategy_c_v2_phase4_coinglass_overlay.md` — Track A+Coinglass comparison
- `strategy_c_v2_phase4_final_recommendation.md` — primary + backup pick

### Phase 5A — Stop-loss + leverage/risk overlay research (**this session**)

User brief 2026-04-12 (post Phase 4 acceptance):

> Add fixed stop-loss and leverage/risk overlay research on top of the
> promoted candidates. Goal: identify the highest-return version that
> still preserves survival and robust OOS behavior. Do not promote 5x
> directly. Use 2x as the realistic near-term deployment candidate
> unless 3x with hard stops clearly survives and remains robust.

**In scope for Phase 5A:**
- Candidate A both (4h rsi_only_21 h=12 both)
- Candidate A long-only
- Candidate C long-only (4h rsi_and_macd_14 h=4 long)
- D1 shadow: rsi_only_20 h=11 both (Phase 4 robustness-band highest-return)
- D2 shadow: rsi_only_28 h=18 both (Phase 4 robustness-band best risk-adj)

**Research grid:**
- Effective leverage: 1x / 2x / 3x (exploratory: 5x)
- Fixed stop-loss: 1.5% / 2.0% / 2.5% / 3.0%
- Stop trigger: MARK_PRICE (close-based) vs CONTRACT_PRICE (wick-based)
- Account-risk sizing: 1.0% / 1.5% / 2.0% per trade

**Per-cell metrics:**
- OOS compounded return
- Max drawdown
- Profit factor
- Trade count
- Exposure time
- Funding drag
- Fee/slippage drag (cost PnL)
- Average stopped loss
- Worst trade
- Liquidation safety margin per leverage level

**Phase 5A non-goals:**
- No direct promotion of 5x (exploratory only)
- No new signal families
- No MTF framework changes
- D1/D2 shadow-only — monitored but not deployed
- No re-optimization from live data

**Phase 5A deliverables:**
- `strategy_c_v2_phase5a_stop_loss_leverage.md` — Phase 5A research report
- `strategy_c_v2_phase5a_stop_loss_leverage.csv` — full per-cell data
- Updated `strategy_c_v2_phase4_final_recommendation.md` — corrected
  margin-efficiency math + Phase 5A verdict on leverage tier
- Backtester extension: `stop_loss_pct`, `stop_trigger`,
  `risk_per_trade`, `effective_leverage` params, all TDD-covered

### Phase 6 — D1 promotion + expanded risk budget + stress tests (**this session**)

User brief 2026-04-12 (post Phase 5A acceptance):

> Keep A_both and C_long as paper-deployment baseline candidates, but
> move D1 out of shadow and make it the primary research target for
> return expansion. Do not conclude 2x is globally optimal — Phase 5A
> only tested cells where position_frac capped at 2x. Find the
> highest-return version that still survives stop-gap risk and remains
> robust OOS.

**Phase 6 candidates:**
- A_both (4h rsi_only_21 h=12 both) — safety baseline
- A_long (long-only variant)
- C_long (4h rsi_and_macd_14 h=4 long) — long-only baseline
- **D1_both promoted**: 4h rsi_only_20 h=11 both
- **D1_long**: same rule, long-only variant
- D2 stays secondary shadow

**Phase 6 tasks:**

1. **D1 promotion study** — robustness band around D1:
   - RSI period ± nearby
   - Hold ± nearby
   - Both-sides vs long-only
   - Time-stop vs opposite-flip exit
   - Answer: is D1 a broad optimum or a single sharp one?

2. **Expanded risk-budget study** — extend Phase 5A grid:
   - risk_per_trade: 2.0 / 2.5 / 3.0 / 4.0% (up from 2% max)
   - stop_loss_pct: 1.0 / 1.25 / 1.5 / 2.0% (tighter, down to 1%)
   - effective leverage: 2x / 3x / 5x
   - Report actual position_frac per cell (not just L)

3. **Stop execution realism** — add gap/slippage to stop fills:
   - Mild: 0.10%
   - Medium: 0.30%
   - Severe: 1.00%
   - Report return degradation

4. **Tail-event stress** — synthetic shocks:
   - 20% / 1 day
   - 30% / 2 days
   - 40% / 1 day
   - Check liquidation, worst equity impact, whether stops still survive

5. **Directional decomposition** — D1 and A both-sides vs long-only:
   - Is the extra return from shorts, or does long-only preserve it?

6. **Decision framework** — classify each candidate:
   - paper-deploy now
   - return-expansion candidate
   - reject

**Phase 6 deliverables:**
- `strategy_c_v2_phase6_d1_promotion.md`
- `strategy_c_v2_phase6_expanded_risk_budget.md`
- `strategy_c_v2_phase6_stop_slippage_stress.md`
- `strategy_c_v2_phase6_tail_event_stress.md`
- `strategy_c_v2_phase6_directional.md`
- `strategy_c_v2_phase6_final_recommendation.md`

### Phase 7 — Paper deployment + execution-parity validation (**this session**)

User brief 2026-04-12 (post Phase 6 acceptance):

> paper deployment + execution-parity validation. Do NOT start a broad
> new research cycle. Before paper deployment, explicitly separate two
> stop semantics: strategy_close_stop (evaluated only at completed bar
> close) vs exchange_intrabar_stop (triggered intrabar by live/mark
> price). Track both separately. Do NOT assume they are equivalent.

**Deployment set** (locked from Phase 6):
- Primary paper: D1_long, sl=1.5%, close trigger, risk=2%, L=2x, actual_frac=1.333
- Backup paper: C_long, sl=2.0%, close trigger, risk=2%, L=2x, actual_frac=1.0
- Shadow paper: D1_long, sl=1.25%, close trigger, risk=2.5%, L=2x, actual_frac=2.0

**What this session can and cannot do**:
- CAN: build the paper-deployment infrastructure (telemetry, safety
  controls, reconciliation analyzer, stop-semantics split).
- CAN: run a retrospective simulation of the last 30 days (2026-03-06
  → 2026-04-05) on historical 4h data, producing the full telemetry
  log format and all six deliverables with concrete numbers.
- CAN: run the stop-semantics parity study (strategy_close_stop vs
  exchange_intrabar_stop) on the full 5-year OOS window to establish
  the expected divergence in trade/stop/pnl terms.
- CANNOT: actually run for 30 calendar days of live-data. That's a
  forward-looking process that needs persistent infrastructure and
  real calendar time. Phase 7 delivers the infra and a retrospective
  run; Phase 7-LIVE is a separate activity the user triggers when ready.

**Phase 7 tasks:**

1. **Paper-deployment simulation for the last 30 days**
2. **Stop-semantics parity study** on D1_long and C_long (5-year)
3. **Live telemetry** format (PaperTradeLogEntry) capturing every
   field the brief lists
4. **Daily + weekly reconciliation** against the backtest counterfactual
5. **Safety controls** (stale data, incomplete bar, stop mismatch)
6. **Day-30 decision** classification framework

**Phase 7 deliverables:**
- `strategy_c_v2_phase7_paper_deployment_log.md`
- `strategy_c_v2_phase7_stop_semantics_parity.md`
- `strategy_c_v2_phase7_execution_quality.md`
- `strategy_c_v2_phase7_funding_slippage_reconciliation.md`
- `strategy_c_v2_phase7_three_cell_comparison.md`
- `strategy_c_v2_phase7_day30_recommendation.md`

### Phase 8 — Live paper deployment (forward-looking, separate session)

Only reached after Phase 7 produces the infrastructure. Scope:
- Actual 30 calendar days of paper fills on the deployment set
- Safety control alerts firing in real time
- Daily reconciliation run automatically
- Deliverable #9: live OOS equity curves + promote/iterate/discard call

---

### Research branch: manual_edge_extraction (**parallel to deployment path**)

User brief 2026-04-12 (post Phase 7 acceptance):

> Do NOT discard the Phase 6/7 deployment path. Open a parallel branch
> `manual_edge_extraction` to identify which discretionary behaviors
> likely account for the gap between manual BTC futures trading and
> the current systematic version, and test whether they can be
> codified on top of D1_long / C_long.

**Baseline for comparison** (Phase 7 numbers, frozen):
- D1_long_primary @ strategy_close_stop → +143.45% / DD 12.97% / 73 trades / worst −5.68%
- C_long_backup @ strategy_close_stop → +106.26% / DD 18.10% / 178 trades / worst −6.62%

**Research question**: which additive modifiers to D1_long / C_long
push OOS return meaningfully higher WITHOUT breaking the survival
properties that Phase 6-7 already verified (tail survival at frac ≤ 2,
slippage resistance, worst-trade bound, walk-forward discipline)?

**Four research families** (the Phase 8 brief's task list):

1. **Regime selection** — trade only in favorable regimes (4h / 1d
   trend filter, volatility expansion, long-only bull regime, hostile
   funding veto, event-risk veto).
2. **Dynamic sizing** — vary `actual_position_frac` by setup quality
   instead of fixed per-trade frac.
3. **Pyramiding / add-on** — add to winning trades on confirmed
   continuation instead of single fixed entry.
4. **Exit adaptation** — hold strong-trend trades longer, exit
   weak-trend trades earlier; structure-aware or regime-aware exit
   extension/compression.

**Non-goals:**
- No return-only curve-fitting
- No breaking walk-forward discipline
- No discarding Phase 6/7 deployment path
- No new signal families beyond D1_long / C_long bases
- No ATR trailing retread (Phase 4 already rejected it)

**Deliverables (6):**
- `strategy_c_v2_manual_edge_hypothesis.md`
- `strategy_c_v2_manual_edge_regime_filter.md`
- `strategy_c_v2_manual_edge_dynamic_sizing.md`
- `strategy_c_v2_manual_edge_pyramiding.md`
- `strategy_c_v2_manual_edge_adaptive_exit.md`
- `strategy_c_v2_manual_edge_recommendation.md`

---

## 9. File layout

```
strategy-c-orderflow/
  strategy_c_v2_plan.md                       # this file (Phase 1)
  strategy_c_v2_data_coverage.md              # Deliverable #1 (Phase 1)
  strategy_c_v2_feature_matrix.md             # Deliverable #2 (Phase 1)
  strategy_c_v2_literature_benchmark.md       # Deliverable #3 (Phase 2)
  strategy_c_v2_leaderboard.md                # Deliverable #4 (Phase 3)
  strategy_c_v2_best_continuation.md          # Deliverable #5 (Phase 3)
  strategy_c_v2_best_reversal.md              # Deliverable #6 (Phase 3)
  strategy_c_v2_best_hybrid.md                # Deliverable #7 (Phase 3)
  strategy_c_v2_binance_vs_coinglass.md       # Deliverable #8 (Phase 4)
  strategy_c_v2_final_recommendation.md       # Deliverable #9 (Phase 5)

  run_strategy_c_v2_walk_forward.py           # Phase 3 driver

  src/
    adapters/
      binance_futures.py                      # EXTEND Phase 1: fetch_funding_rate_history
    data/
      btcusdt_15m_6year.csv                   # EXISTS (210,219 rows, 2020-04 → 2026-04)
      btcusdt_1h_6year.csv                    # EXISTS ( 52,560 rows)
      btcusdt_4h_6year.csv                    # EXISTS ( 13,147 rows)
      btcusdt_1d_6year.csv                    # EXISTS (  2,192 rows)
      btcusdt_funding_5year.csv               # NEW    Phase 1
      strategy_c_v2_dataset.py                # NEW    Phase 1 (container + loader)
      strategy_c_v2_features.py               # NEW    Phase 1 (signatures)
                                              # IMPL   Phase 2
    indicators/
      volume_profile.py                       # EXISTS (has _rsi_from_closes)
      bollinger.py                            # NEW    Phase 1 TDD
      stochastic.py                           # NEW    Phase 1 TDD
      atr.py                                  # NEW    Phase 1 TDD
    research/
      backtest_strategy_c.py                  # EXISTS (Baseline A/B/C backtester)
      strategy_c_sweep.py                     # EXISTS (temporal_split, percentile_threshold, passes_min_trades)
      strategy_c_v2_walk_forward.py           # NEW    Phase 2
      strategy_c_v2_backtest.py               # NEW    Phase 2 (cost + funding)
    strategies/
      strategy_c_v2_literature.py             # NEW    Phase 2 (F1 RSI/MACD)
      strategy_c_v2_continuation.py           # NEW    Phase 3 (F2 MTF)
      strategy_c_v2_reversal.py               # NEW    Phase 3 (F3 liq, Track B)
      strategy_c_v2_hybrid.py                 # NEW    Phase 3 (F4 regime switch)
      strategy_c_v2_score_model.py            # NEW    Phase 4 (cost-aware ranker)

  tests/
    test_binance_funding_history.py           # NEW    Phase 1
    test_indicators_bollinger.py              # NEW    Phase 1
    test_indicators_stochastic.py             # NEW    Phase 1
    test_indicators_atr.py                    # NEW    Phase 1
    test_strategy_c_v2_dataset.py             # NEW    Phase 1 (skeleton)
    test_strategy_c_v2_features.py            # NEW    Phase 1 (skeleton)
    test_strategy_c_v2_walk_forward.py        # NEW    Phase 2
    test_strategy_c_v2_backtest.py            # NEW    Phase 2
    test_strategy_c_v2_literature.py          # NEW    Phase 2
    test_strategy_c_v2_continuation.py        # NEW    Phase 3
    test_strategy_c_v2_reversal.py            # NEW    Phase 3
    test_strategy_c_v2_hybrid.py              # NEW    Phase 3
    test_strategy_c_v2_score_model.py         # NEW    Phase 4
```

---

## 10. Existing infrastructure inventory (Phase 1 reuse)

What's already on disk, not rebuilt:

| File / module                                   | Content                                              |
|-------------------------------------------------|------------------------------------------------------|
| `src/data/btcusdt_15m_6year.csv`                | 210,219 rows, 2020-04-05 → 2026-04-05 (timestamp,OHLCV) |
| `src/data/btcusdt_1h_6year.csv`                 |  52,560 rows                                         |
| `src/data/btcusdt_4h_6year.csv`                 |  13,147 rows                                         |
| `src/data/btcusdt_1d_6year.csv`                 |   2,192 rows                                         |
| `src/data/btcusdt_1w_6year.csv`                 |     313 rows                                         |
| `src/adapters/binance_futures.py`               | `BinanceFuturesAdapter.fetch_range / fetch_multi`    |
| `src/adapters/coinglass_client.py`              | 8 endpoints, 90-day 15m hard limit                   |
| `src/indicators/volume_profile.py`              | `_rsi_from_closes(closes, period)` (Wilder)          |
| `src/research/macro_cycle.py`                   | `compute_macd(12,26,9)`, `_ema`, `compute_sma200_ratio` |
| `src/data/mtf_bars.py`                          | `MultiTimeframeBars.get_history(tf, as_of, lookback)` (no-lookahead) |
| `src/research/backtest_strategy_c.py`           | t+1 open backtest, compounded_return, profit_factor, exposure_time |
| `src/research/strategy_c_sweep.py`              | `temporal_split`, `percentile_threshold`, `passes_min_trades` |

What's missing, must build:

| Item                                           | Phase  |
|------------------------------------------------|--------|
| `BinanceFuturesAdapter.fetch_funding_rate_history` | P1  |
| `src/data/btcusdt_funding_5year.csv`           | P1     |
| `src/indicators/bollinger.py`                  | P1     |
| `src/indicators/stochastic.py`                 | P1     |
| `src/indicators/atr.py`                        | P1     |
| `src/data/strategy_c_v2_dataset.py` (container)| P1     |
| `src/data/strategy_c_v2_features.py` (sigs)    | P1     |
| Rolling walk-forward harness                   | P2     |
| Cost + funding aware backtester                | P2     |
| Feature module implementation                  | P2     |
| Strategy families F1 / F2 / F3 / F4            | P2/P3  |
| Cost-aware score model                         | P4     |

---

## 11. Hard caveats (up front, not buried)

1. **Coinglass STANDARD plan caps 15m history at ~90 days.** This is why
   Track A is Binance-only and Track B is 90-day overlap only. Not a
   workaround — a hard data constraint we make honest about in deliverable #1.
2. **Binance OI history is ~30 days.** For Track A walk-forward, OI features
   are either omitted or sourced from a third-party archive. Phase 1 punts.
3. **Funding rate is 8h on Binance perp** (every 8h settlement). We
   forward-fill to 15m and expose `bars_to_next_funding` as an explicit
   feature so the model can see the funding clock.
4. **Walk-forward 24m/6m needs ≥30m of history.** 5 years supports ~8 test
   windows. This is the minimum viable; we don't try to stretch it.
5. **Stefaniuk literature results are published on 1h bars**, not 15m. Our
   F1 implementation runs on both 1h and 15m so the comparison is fair and
   the 15m result isn't silently apples-to-oranges.
6. **Leverage is 1x notional in the leaderboard.** No margin stacking during
   research. Leverage and position sizing are deployment decisions, applied
   after a strategy wins the OOS leaderboard.
7. **`pair_cvd` stays dropped.** Baseline C verdict. If v2 finds evidence
   against that drop we'll revisit, but we don't re-litigate it by default.

---

## 12. This session's exit criteria

At end of Phase 1 (this session), the worktree must contain:

- [x] `strategy_c_v2_plan.md` (this file)
- [ ] `BinanceFuturesAdapter.fetch_funding_rate_history` implemented with tests
- [ ] `src/data/btcusdt_funding_5year.csv` on disk, verified
- [ ] `src/indicators/bollinger.py` + tests, green
- [ ] `src/indicators/stochastic.py` + tests, green
- [ ] `src/indicators/atr.py` + tests, green
- [ ] `src/data/strategy_c_v2_dataset.py` skeleton + tests, green
- [ ] `src/data/strategy_c_v2_features.py` signatures + tests, green
- [ ] `strategy_c_v2_data_coverage.md` (deliverable #1)
- [ ] `strategy_c_v2_feature_matrix.md` (deliverable #2)

And Phase 2 can start cold from the exit state without any "where did I leave
this" hunt.
