# Strategy C v2 — CONSOLIDATED FINAL

_Date: 2026-04-12_
_Phases: 8B (breaker) + 8C (exec layer) + 8D (Coinglass)_
_Status: FINAL. One strategy, one account, one answer._

## The strategy

**D1 long with 1h pullback execution layer on BTCUSDT perpetual futures.**

```
REGIME GATE:       4h RSI(20) > 70 = long permission zone
EXECUTION:         1h pullback re-entry (1% dip from zone high)
MAX ENTRIES:       3 per regime zone (1 base + 2 re-entries)
HOLD:              6 x 4h = 24 x 1h bars per trade
DUAL STOP:         alpha 1.25% close-trigger + catastrophe 2.5% wick-trigger
DIRECTION:         long only
DATA:              Binance OHLCV 4h + 1h + funding (no Coinglass)
```

## The two deployable configs

### AGGRESSIVE — Row 4 base + exec layer (4x)

```
exchange_leverage:       4x isolated
base_frac:               3.0
max_frac:                4.0
starting_equity:         $10,000
peak_notional:           $40,000

--- Per-trade quality (the reliable metrics) ---
trades:                  136
win_rate:                70.6%
profit_factor:           5.04
max_drawdown:            14.9%
worst_trade:             -8.2%
stop_exit_fraction:      21.3%
avg_trade_pnl:           ~5.6%

--- Compounded path (theoretical upper bound) ---
compounded_return:       +170,369%
ending_equity:           $17,046,893

--- Shock profile ---
10% shock:               survives
15% shock:               survives
20% shock:               tight (2.9pp buffer)
30% shock:               liquidates

--- Slippage (on the 4h-only Row 4 base, 66 trades) ---
0.1% slip:               -82pp (still +698%)
0.3% slip:               -246pp (still +534%)
0.5% slip:               -410pp (still +371%)
```

### FALLBACK — Row 2 base + exec layer (3x)

```
exchange_leverage:       3x isolated
base_frac:               2.0
max_frac:                3.0
starting_equity:         $10,000
peak_notional:           $30,000

--- Per-trade quality ---
trades:                  136
win_rate:                70.6%
profit_factor:           5.04
max_drawdown:            10.1%
worst_trade:             -5.5%

--- Compounded path ---
compounded_return:       +17,457%
ending_equity:           $1,755,665

--- Shock profile ---
10/15/20% shock:         survives
30% shock:               liquidates
```

## How we got here — 4 phases

### Phase 8B: Circuit-breaker validation → breaker unnecessary

Replayed all Row 4 trades at 1h resolution. Worst intrabar adverse
on non-stop trades: **2.13%**. The existing dual-stop (alpha 1.25%
+ catastrophe 2.5%) already catches every dangerous excursion.
All 4 breaker thresholds (8/10/12/15%) fire **0 times** in the
historical OOS. No circuit breaker adds any value.

Row 4 promotion verdict: passes all criteria except 1% slip tail
stress (structural property of high leverage × many stops, not
fixable by breaker). At realistic slip (≤ 0.3%), Row 4 delivers
+534% on the 4h-only base. With the exec layer, per-trade quality
is significantly stronger.

### Phase 8C: Execution-layer trade-count lift → 136 trades achieved

The 4h D1 regime gates 138 zones over 6 years. Adding 1h pullback
re-entry within each zone lifts trade count from 66 to **136**
while dramatically improving quality:

| Metric | 4h-only (Row 4) | + exec layer |
|--------|----------------:|-------------:|
| Trades | 66 | **136** |
| Win rate | 48.5% | **70.6%** |
| Profit factor | 2.32 | **5.04** |
| Max DD | 31.1% | **14.9%** |
| Worst trade | −10.6% | **−8.2%** |

The pullback re-entry buys dips within confirmed momentum regimes
at better prices, with shorter holds that cut losses faster. The
1h resolution provides earlier exits that the 4h bars hide.

Trade-count ceiling: **136** (constrained by 138 regime zones ×
single-position rule). 200+ trades not reachable within the frozen
D1 family without lowering the RSI threshold (which would open the
signal family).

### Phase 8D: Coinglass overlay → REJECTED

Coinglass 4h data covers only the last OOS window (2025-10 to
2026-04). In that window: **3 baseline trades**. Tested OI
divergence veto, taker imbalance veto, and liquidation cascade
veto. Results:
- OI and taker: 0 vetoes (no effect)
- Liquidation cascade: vetoed 1 winning trade (reduced return)
- Sample size: n=3 (insufficient for any statistical conclusion)

**Verdict: Coinglass overlay formally rejected.** Insufficient 4h
history for validation. The final strategy runs on Binance-only
data (4h + 1h OHLCV + funding rate). If Coinglass is desired in
live deployment, it must be validated forward-only over 3+ months.

## What the compounded returns mean (and don't mean)

The +170,369% (aggressive) and +17,457% (fallback) numbers are
the compounded equity paths assuming perfect reinvestment of profits
across 136 trades at ~5.6% average PnL. They are mathematically
correct but operationally theoretical because:

1. **Market impact at scale**: a $10k account compounding to $17M
   would need to trade $68M notional at peak (4x × $17M). BTC perp
   liquidity at that size has meaningful market impact.
2. **Slippage scales with size**: the 0.1% slippage assumption
   breaks down at $10M+ positions.
3. **The per-trade edge is what's real**: 70.6% WR, PF 5.04,
   and 14.9% DD are the reliable metrics. The compounding path
   is the theoretical upper bound if the edge persists at all sizes.

For a fixed $10,000 account with constant position sizing (no
reinvestment), the accumulated return is:
- 136 trades × 5.6% avg PnL = **~762% total** ($10k → ~$86k)

That's the floor. The compounded number is the ceiling. Reality
will be somewhere in between depending on how aggressively you
reinvest.

## Three strictly-separated concepts (final)

| Concept | Aggressive | Fallback |
|---------|-----------|----------|
| Exchange leverage | 4x isolated | 3x isolated |
| actual_frac (base / max) | 3.0 / 4.0 | 2.0 / 3.0 |
| Portfolio allocation | 1.0 (full $10k) | 1.0 (full $10k) |

## What we are NOT doing

- No new strategy family (D1 is the only mainline)
- No Coinglass in the final strategy (rejected due to data limitation)
- No 15m execution (1h is sufficient, 15m would be slower for marginal gain)
- No pyramiding (single position per entry, max 3 entries per zone sequentially)
- No further research branching

## Full test suite

1033 tests pass across all modules:
- Backtest + dual-stop: 93 tests
- Stress test: 20 tests
- Dynamic sizing: 31 tests
- Canonical baseline: 62 tests
- Live monitor: 38 tests
- Report consistency: 33 tests
- Parity: 10 tests
- Walk-forward + features + filters + signals: ~746 tests
