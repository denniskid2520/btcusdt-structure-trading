# Strategy C v2 — Phase 11: Forward Validation & Paper Deployment

_Date: 2026-04-12_
_Status: Research discovery FROZEN. Deployment-validation mode._

---

## 11A. Canonical deployment configs (LOCKED)

These four configs are frozen. No strategy-logic changes permitted.

### 1. B_balanced_4x — FINAL AGGRESSIVE

```yaml
candidate_id: B_balanced_4x
role: final_aggressive

# Regime gate (4h)
regime_indicator: RSI(20) on 4h BTCUSDT close
regime_threshold: > 70
regime_direction: long only
regime_check_interval: every 4h bar close (00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC)

# Execution layer (1h)
execution_timeframe: 1h
entry_mode: hybrid (pullback + breakout)
pullback_threshold: 0.75%  # dip from zone high
breakout_threshold: 0.25%  # new high delta over 8-bar lookback
max_entries_per_zone: 6
cooldown_after_exit: 2 bars (1h)
base_entry: first 1h bar of each new regime zone

# Sizing
exchange_leverage: 4x isolated
base_frac: 3.0
max_frac: 4.0
dynamic_sizing: on (multiplier [0.5, 1.5], clipped at max_frac)
adaptive_exit: on

# Stops (dual-stop architecture)
alpha_stop_pct: 1.25%
alpha_stop_semantics: strategy_close_stop (check 1h close, fill next 1h open)
catastrophe_stop_pct: 2.5%
catastrophe_stop_semantics: exchange_intrabar_stop (check 1h wick, fill at level)

# Hold
hold_hours: 24
hold_exec_bars: 24 (1h bars)

# Cost assumptions
fee_per_side: 0.05%
slip_per_side: 0.01%
exec_layer_extra_slip: 0.02% per side (pullback-entry spread penalty)
round_trip_cost: 0.12% per unit of actual_frac (base)
round_trip_cost_enhanced: 0.16% per unit of actual_frac (with exec penalty)

# OOS canonical metrics (exec-aware cost, portfolio_allocation=1.0)
num_trades: 150
win_rate: 69.3%
profit_factor: 4.63
simple_return: +848.4%
ending_equity_simple: $94,844
compounded_return: +167,618%
max_dd: 18.6%
worst_trade: -8.3%
avg_pnl_per_trade: +5.66%
avg_win: +10.41%
avg_loss: -5.09%
alpha_stop_exits: 14
catastrophe_stop_exits: 17
stop_exit_fraction: 20.7%
worst_15m_adverse_nonstop: 2.18%
liquidation_distance: 25.0%
historical_liquidated: NO
shock_10: survives
shock_15: survives
shock_20: tight (2.82pp buffer)
shock_30: liquidates
shock_40: liquidates
top5_zone_pnl_share: 26.9%
```

### 2. B_balanced_3x — FALLBACK

```yaml
candidate_id: B_balanced_3x
role: fallback

# Same template as B_balanced_4x except:
exchange_leverage: 3x isolated
base_frac: 2.0
max_frac: 3.0

# OOS canonical metrics
num_trades: 150
win_rate: 69.3%
profit_factor: 4.63
simple_return: +565.6%
ending_equity_simple: $66,562
max_dd: 12.7%
worst_trade: -5.6%
avg_pnl_per_trade: +3.77%
shock_15: survives
shock_20: survives
shock_30: tight
```

### 3. A_density_4x — HIGH-SAMPLE SHADOW

```yaml
candidate_id: A_density_4x
role: shadow_high_sample

# Execution layer differences:
entry_mode: hybrid (pullback + breakout)
pullback_threshold: 0.75%
breakout_threshold: 0.50%
hold_hours: 8
hold_exec_bars: 8 (1h bars)

# Same leverage as final:
exchange_leverage: 4x isolated
base_frac: 3.0
max_frac: 4.0

# OOS canonical metrics
num_trades: 264
win_rate: 67.4%
profit_factor: 3.30
simple_return: +721.6%
ending_equity_simple: $82,159
max_dd: 15.6%
worst_trade: -8.3%
avg_pnl_per_trade: +2.73%
shock_15: survives
shock_20: tight
```

### 4. B_balanced_5x — HIGH-RETURN SHADOW

```yaml
candidate_id: B_balanced_5x
role: shadow_high_return

# Same template as B_balanced_4x except:
exchange_leverage: 5x isolated
base_frac: 3.33
max_frac: 5.0

# OOS canonical metrics
num_trades: 150
win_rate: 69.3%
profit_factor: 4.63
simple_return: +941.8%
ending_equity_simple: $104,176
max_dd: 20.5%
worst_trade: -9.3%
shock_15: tight (2.72pp buffer)
shock_20: liquidates
```

---

## 11B. Paper deployment spec

### Order of operations per cron cycle

The paper runner executes every **1 hour** (1h bar close). Within
each cycle:

```
EVERY 1H BAR CLOSE:
  1. FETCH latest 1h bar (OHLCV + funding if settlement)
  2. CHECK 4H REGIME (every 4th hour: 00, 04, 08, 12, 16, 20 UTC)
     - compute RSI(20) on latest 4h close
     - if RSI > 70: regime_active = true, record zone_id
     - if RSI <= 70: regime_active = false
  3. IF position is OPEN:
     a. check catastrophe stop (1h wick vs stop level)
        → if fires: record exit, mark position CLOSED
     b. check alpha stop (1h close vs stop level)
        → if fires: queue exit at next 1h open
     c. check hold expiry (bars_held >= hold_exec_bars)
        → if fires: queue exit at next 1h open
     d. check opposite flip (RSI drops below 30 for short signal)
        → if fires: queue exit at next 1h open
     e. else: HOLD, update bars_held += 1
  4. IF position is CLOSED and regime_active:
     a. check cooldown (bars since last exit >= cooldown_bars + hold_exec_bars)
     b. check max_entries for current zone
     c. check entry signal:
        - pullback: zone_high - close >= pullback_pct * zone_high
        - breakout: close > max(last 8 bars high) * (1 + breakout_pct)
        - hybrid: either fires
     d. if entry signal fires:
        → compute actual_frac (dynamic sizing if on)
        → compute alpha_stop_level = fill_price * (1 - 0.0125)
        → compute catastrophe_level = fill_price * (1 - 0.025)
        → queue entry at next 1h open
        → record entry in telemetry
  5. IF exit was queued: execute at current bar open, record fill
  6. IF entry was queued: execute at current bar open, record fill
  7. WRITE telemetry row
```

### State machine

```
STATES: FLAT → PENDING_ENTRY → OPEN → PENDING_EXIT → FLAT

Transitions:
  FLAT + entry_signal       → PENDING_ENTRY
  PENDING_ENTRY + bar_open  → OPEN (fill at open, set stops)
  OPEN + catastrophe_wick   → FLAT (immediate fill at level)
  OPEN + alpha_close        → PENDING_EXIT
  OPEN + hold_expiry        → PENDING_EXIT
  OPEN + opposite_flip      → PENDING_EXIT
  PENDING_EXIT + bar_open   → FLAT (fill at open)
```

### Regime zone tracking

```
zone_id: auto-increment counter
zone_start_ts: timestamp of first 4h bar where RSI(20) > 70
zone_end_ts: timestamp of first 4h bar where RSI(20) <= 70
zone_entry_count: number of entries in this zone (base + re-entries)
zone_high: running max of 1h close within the zone
```

---

## 11C. Telemetry schema

Every paper/live trade logs one row with these fields:

```
candidate_id:           string    # "B_balanced_4x" / "B_balanced_3x" / etc.
trade_id:               int       # auto-increment per candidate
zone_id:                int       # which regime zone this trade belongs to
zone_entry_number:      int       # 1 = base, 2+ = re-entry

# Timing
regime_signal_ts:       datetime  # 4h bar close that activated the zone
exec_signal_ts:         datetime  # 1h bar close that triggered the entry
entry_fill_ts:          datetime  # next 1h bar open where fill happens
exit_fill_ts:           datetime  # 1h bar where exit fills

# Entry
entry_type:             string    # "base" / "reentry_pullback" / "reentry_breakout"
intended_entry_price:   float     # the 1h close when signal fired
realized_fill_price:    float     # actual fill (paper: next open; live: exchange fill)
entry_slippage:         float     # realized - intended (as fraction of intended)

# Sizing
actual_frac:            float     # position_frac used for this trade
dynamic_multiplier:     float     # if dynamic sizing on, the multiplier applied
base_frac:              float     # the candidate's base_frac
notional_usd:           float     # actual_frac * account_equity at entry

# Stops
alpha_stop_level:       float     # absolute price
catastrophe_stop_level: float     # absolute price

# Hold
hold_bars_target:       int       # hold_exec_bars (or adaptive override)
hold_bars_actual:       int       # how many bars the trade actually held

# Exit
exit_reason:            string    # alpha_stop / catastrophe_stop / time_stop /
                                  # opposite_flip / end_of_series
exit_price:             float     # realized exit fill
exit_slippage:          float     # realized vs model

# PnL decomposition
gross_pnl:              float     # (exit - entry) / entry * side * actual_frac
funding_pnl:            float     # -side * sum(funding_during_hold) * actual_frac
cost_pnl:               float     # -round_trip_cost * actual_frac
net_pnl:                float     # gross + funding - cost

# Safety flags
monitor_flags:          list[str] # ["hostile_funding", "wick_near_stop", etc.]
max_adverse_during_trade: float   # worst price excursion during hold (from 1h bars)
```

Example row:
```json
{
  "candidate_id": "B_balanced_4x",
  "trade_id": 1,
  "zone_id": 1,
  "zone_entry_number": 1,
  "regime_signal_ts": "2026-04-13T00:00:00",
  "exec_signal_ts": "2026-04-13T01:00:00",
  "entry_fill_ts": "2026-04-13T02:00:00",
  "entry_type": "base",
  "intended_entry_price": 85000.0,
  "realized_fill_price": 85010.0,
  "entry_slippage": 0.00012,
  "actual_frac": 3.0,
  "alpha_stop_level": 83937.5,
  "catastrophe_stop_level": 82875.0,
  "hold_bars_target": 24,
  "exit_reason": "time_stop",
  "exit_price": 86200.0,
  "net_pnl": 0.0396,
  "max_adverse_during_trade": 0.008
}
```

---

## 11D-E. 30-day engineering validation plan

### What we're testing (NOT strategy performance — engineering correctness)

| Check | Method | Pass criterion |
|-------|--------|----------------|
| **Signal timing** | Compare paper signal timestamps against offline 4h RSI computation | ≤ 1 bar drift in 30 days |
| **Regime zone alignment** | Paper zone_start/end matches offline replay | exact match |
| **Stop placement** | alpha_stop = fill * (1 - 0.0125), catastrophe = fill * (1 - 0.025) | exact to 4 decimal places |
| **Stop trigger correctness** | alpha fires on 1h close breach, catastrophe fires on 1h wick breach | 0 mismatches |
| **Fill quality** | entry_slippage and exit_slippage within [-0.3%, +0.3%] of intended | ≤ 2 outliers |
| **Funding reconciliation** | sum(funding_pnl) matches Binance funding history query | within 0.01% |
| **Telemetry completeness** | every trade has all fields populated, no nulls in required fields | 100% complete |
| **Monitor-state consistency** | no transitions outside the defined state machine | 0 violations |
| **Re-entry logic** | re-entries only fire when: zone active AND cooldown satisfied AND max_entries not reached | 0 violations |
| **Hold duration** | no trade held longer than hold_exec_bars + 1 (allow 1 bar for exit delay) | 0 violations |

### 30-day engineering gates (pass/fail)

```
GATE 1: signal_timing_drift ≤ 1 bar           → PASS/FAIL
GATE 2: stop_placement_errors == 0             → PASS/FAIL
GATE 3: stop_trigger_mismatches == 0           → PASS/FAIL
GATE 4: fill_slippage_outliers ≤ 2             → PASS/FAIL
GATE 5: funding_reconciliation_error ≤ 0.01%   → PASS/FAIL
GATE 6: telemetry_completeness == 100%         → PASS/FAIL
GATE 7: state_machine_violations == 0          → PASS/FAIL
GATE 8: reentry_logic_violations == 0          → PASS/FAIL
```

**All 8 gates must pass to proceed to 90-day behavioral validation.**

If B_balanced_4x fails any gate:
- investigate and fix the engineering bug
- re-run 30-day paper
- if the bug is in the STRATEGY LOGIC (not infra): freeze 4x, default to B_balanced_3x

---

## 11F. 90-day behavioral validation plan

After 30-day engineering gates pass, continue the paper run to 90 days total. At 90 days compare realized metrics against expected OOS bands.

### Expected bands (from OOS walk-forward)

For B_balanced_4x over a 90-day window (~540 1h bars), the expected
per-90-day statistics (derived from the 8-window OOS at ~180 days per window, scaled):

| Metric | Expected per 90d | Acceptable band |
|--------|------------------:|----------------|
| Trade count | ~19 (150 / 8 windows) | 10-30 |
| Win rate | 69.3% | 55%-85% (small-sample variance at n≈19) |
| Profit factor | 4.63 | ≥ 2.0 |
| Simple return (4x) | ~106% per window | > 0% (any positive is acceptable at 90d) |
| Stop-exit fraction | 20.7% | 10%-35% |
| Worst trade | −8.3% | no worse than −15% |
| Avg slippage | ~0.05% | ≤ 0.3% |

### 90-day comparison checklist

```
[ ] realized trade count within [10, 30]
[ ] realized WR within [55%, 85%]
[ ] realized PF ≥ 2.0
[ ] realized simple return > 0%
[ ] no single trade worse than -15%
[ ] avg realized slippage ≤ 0.3%
[ ] stop-exit fraction within [10%, 35%]
[ ] 4x and 3x divergence: 4x return ≈ 1.5x of 3x return (frac ratio)
[ ] A_density_4x provides at least 1.5x the trade count of B_balanced_4x
[ ] B_balanced_5x worst trade within 1.2x of B_balanced_4x worst trade
```

---

## 11G. Promotion / fallback / halt rules

### Promotion rule (B_balanced_4x → live deployment)

Promote B_balanced_4x to live if:
1. All 8 engineering gates pass at 30 days
2. At 90 days: realized PF ≥ 2.0 AND no single trade worse than −15% AND realized slippage ≤ 0.3% avg
3. No regime-zone-level anomaly (e.g., all PnL from one zone)

### Fallback rule (4x → 3x)

Fall back from B_balanced_4x to B_balanced_3x if:
1. Any engineering gate fails at 30 days AND the fix requires strategy-logic change
2. At 90 days: realized PF < 1.5 (not just < 2.0 — allow some degradation from small sample)
3. Realized worst trade exceeds −15%
4. Average slippage exceeds 0.5%
5. A 15%+ adverse intraday move occurs during an open 4x trade (the tight-at-20% cliff was hit)

### Halt rule (5x shadow)

Halt B_balanced_5x shadow if:
1. Any trade's realized adverse move exceeds 12% (approaching the 20% liq cliff on 5x)
2. Realized slippage on 5x trades exceeds 0.5% average
3. 5x worst trade exceeds −12%
4. A 15%+ shock event occurs and the 5x position would have been within 3pp of liquidation

### Continue rule (A_density_4x shadow)

Continue A_density_4x as long as:
1. Its trade count is ≥ 1.5x B_balanced_4x trade count (density serving its purpose)
2. Its PF ≥ 2.0 (quality preserved)
3. Its stop-exit fraction ≤ 25%
If it fails any of these, deprioritize it — but no need to halt (it's shadow, not deployment).

### Re-optimization trigger

Do NOT restart strategy optimization unless:
1. 90-day realized PF < 1.0 (the strategy is net-losing)
2. Trade count drops to 0 for 30+ days (regime never activates)
3. A structural market change makes 4h RSI(20) regime gating clearly broken (e.g., BTC moves to a permanently low-vol regime where RSI never reaches 70)

If any of these trigger: re-open Phase 9-level parameter sweep on the SAME frozen D1 family. Do NOT open new families.
