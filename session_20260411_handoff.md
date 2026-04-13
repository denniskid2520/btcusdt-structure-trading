# Session handoff — 2026-04-11 → tomorrow

**Worktree:** `C:\Users\User\Documents\New project\.claude\worktrees\strategy-c-orderflow`
**Branch:** `claude/strategy-c-orderflow`
**Test suite:** 756 / 756 passing
**Git status:** uncommitted (not committed per safety policy — user can commit when ready)

## Where we stopped

End of Phase 3 (Strategy C v2). All six Phase 3 deliverables written.
Phase 3 converted the Phase 2 OOS map into a scalable strategy framework.

### Primary Strategy C v2 candidate (picked in Phase 3)

**`4h rsi_only_21 hold=12` (both sides), no filters**

- +142.77% OOS compounded return (48 months, 8 walk-forward windows)
- 20.89% max drawdown
- PF 1.83
- 7/8 positive OOS windows (87.5%)
- 107 trades
- First cell in the program to clear every Phase 2 promotion bar
- Implementation: `rsi_only_signals(features, rsi_period=21, upper=70.0, lower=30.0)` on 4h bars

### Secondary variants

- **Low-DD**: `4h rsi_only_30 hold=16 both` → +138.41% / 13.92% DD / PF 2.75 / 52 trades (misses n > 100)
- **Long-only**: `4h rsi_and_macd_14 hold=4 long-only` → +114.16% / 20.64% DD / 177 trades — clean risk profile

## Where to resume

Open `strategy_c_v2_phase3_recommendation.md` §6 for the Phase 4 work order.
Summary in order:

1. **Sensitivity test of the primary cell** — perturb RSI period {19..23}, hold {10..14}, cost {0.08, 0.12, 0.16}, cooldown {0, 1, 2, 4, 8}, upper/lower thresholds {65/35, 70/30, 75/25}. Is the primary cell fragile inside its own neighborhood?
2. **ATR trailing stop exit** on the primary cell — can it cut 20.89% DD to <15% without killing return?
3. **Spot BTCUSDT benchmark** — measure real "do-nothing" return (no funding drag). Phase 2 showed perp B&H is only +13% because of 28% funding drag; spot should be ≈+43%.
4. **Cost-aware score model** (logistic → XGBoost) on the 4h feature matrix. Compare to the rule-based primary.
5. **Track B deep-dive** — fetch longer Coinglass window, rerun overlay with multiple temporal splits.
6. **Paper deployment** of primary cell at 0.25× notional.

## Key files to read first next session

1. `strategy_c_v2_phase3_recommendation.md` — the primary candidate, full scorecard, Phase 4 plan
2. `strategy_c_v2_phase3_robustness.md` — why the edge is broad, not point-fragile
3. `strategy_c_v2_phase3_directional.md` — why the edge lives in longs
4. `strategy_c_v2_phase3_funding_filter.md` — why short-veto helps but long-veto hurts
5. `strategy_c_v2_phase3_mtf.md` — why naive MTF on 15m execution is cost-dominated (edge-triggered signals are the missing piece)
6. `strategy_c_v2_phase3_coinglass_overlay.md` — why Track B is deferred to Phase 4

## Infrastructure ready to use

Feature module, backtester, walk-forward harness, literature strategies, side filter, funding filter, MTF primitives, unified runner helpers — all TDD covered, all green.

New Phase 3 modules that Phase 4 can build on:
- `src/strategies/strategy_c_v2_filters.py` — side + funding filters
- `src/strategies/strategy_c_v2_mtf.py` — align_higher_to_lower + mtf_trend_signals
- `src/research/strategy_c_v2_runner.py` — shared sweep helpers (TimeframeData, run_cell, stitch_equity)

## Data artefacts on disk

Phase 2:
- `strategy_c_v2_literature_benchmark.csv` (63 rows)

Phase 3:
- `strategy_c_v2_phase3_robustness.csv` (132 rows — robustness + directional)
- `strategy_c_v2_phase3_funding_filter.csv` (60 rows)
- `strategy_c_v2_phase3_mtf.csv` (15 rows)
- `strategy_c_v2_phase3_track_b.csv` (21 rows)

All raw OOS measurements live in these CSVs. No re-running required for Phase 4 analysis.

## Runners (reproducible)

- `run_strategy_c_v2_literature_benchmark.py` — Phase 2 baseline, 15m/1h/4h
- `run_strategy_c_v2_phase3_sweep.py` — Phase 3 robustness + directional + funding filter
- `run_strategy_c_v2_phase3_mtf.py` — Phase 3 MTF framework
- `run_strategy_c_v2_phase3_track_b.py` — Phase 3 Track B overlay

Each runner is self-contained, loads data from `src/data/`, writes a CSV, prints a summary.

## Known gaps (Phase 4 should fix if relevant)

1. Spot BTCUSDT 5-year klines not fetched — needed for the honest B&H benchmark.
2. No ATR trailing stop in the backtester — currently only time-stop + opposite-flip exits.
3. MTF only has the basic AND-gate rule — edge-triggered variants not implemented.
4. Track B is a single 83-day slice; Phase 4 needs a longer Coinglass window.
5. No score model, no XGBoost (explicitly deferred from Phase 3).
6. Primary candidate has not been re-run under perturbed thresholds or tighter cooldown — that's the first Phase 4 task.
