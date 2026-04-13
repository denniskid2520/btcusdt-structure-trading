# Phase 13.5 — Live Execution Semantics + Account Mode

_Date: 2026-04-12_
_Status: deployment-only phase, no strategy logic changes_

---

## 1. Updated deployment config sheet

| Candidate | Mode | Stage | Leverage | Frac | Capital Mode | Notes |
|-----------|------|------:|----------|------|-------------|-------|
| **B_balanced_3x** | **LIVE** | 1 | 3x isolated | 2.0/3.0 | equity_linked | first real-money candidate |
| B_balanced_4x | paper | 2 | 4x isolated | 3.0/4.0 | equity_linked | paper until promotion |
| A_density_4x | paper | 3 | 4x isolated | 3.0/4.0 | equity_linked | shadow only |
| B_balanced_5x | paper | 3 | 5x isolated | 3.33/5.0 | equity_linked | shadow only |

---

## 2. Equity-linked capital mode spec

### Old model (fixed)
```
strategy_equity = $10,000 (hardcoded)
notional = strategy_equity * actual_frac
```

### New model (equity_linked)
```
available_balance = Binance GET /fapi/v2/balance → USDT.availableBalance
strategy_equity = available_balance * allocation_pct
notional = strategy_equity * actual_frac

Constraints:
  if available_balance < min_required_usd → HALT (do not trade)
  if max_cap_usd is set → strategy_equity = min(strategy_equity, max_cap_usd)
```

### Config per candidate
```yaml
B_balanced_3x:
  capital_mode: equity_linked
  allocation_pct: 1.0       # full account dedicated to this strategy
  min_required_usd: 100.0   # halt below $100
  max_cap_usd: null          # no cap (or set to e.g. $50,000 if desired)
```

### Dual reporting requirement
Every telemetry row and every report must include:
1. **Live metrics**: computed on `strategy_equity` (actual account size)
2. **Benchmark metrics**: normalized to $10,000 starting equity

This ensures comparability with the OOS backtest regardless of
actual account size.

---

## 3. Live execution-semantics spec

### Old model (cron at :05)
```
Every hour at :05 → fetch last completed bar → tick
Problem: up to 5-minute delay after bar close
```

### New model (bar-close triggered)
```
Poll loop (every ~10s):
  1. Fetch /fapi/v1/klines?symbol=BTCUSDT&interval=1h&limit=2
  2. Compare current_bar_open_time vs last_processed_bar_open_time
  3. If new bar detected → the PREVIOUS bar just completed
  4. Trigger strategy tick IMMEDIATELY with the completed bar
  5. For 4h regime: trigger only when bar.timestamp.hour % 4 == 0
```

### Timing guarantees
- Strategy tick fires within ~10s of bar close (vs ~5min in cron model)
- 4h regime update fires within ~10s of 4h bar close
- No partial-bar signals (only completed bars trigger ticks)
- Missed-bar catch-up still active (if the process was down, it
  replays all missed completed bars sequentially on restart)

### Process model
```
systemd service (not cron):
  ExecStart=python3 -m execution.live_executor
  Restart=always
  RestartSec=10

The service runs continuously, polling every 10s.
The cron-based paper runner continues in parallel for stage 2/3
candidates. Only B_balanced_3x (stage 1) uses the live executor.
```

---

## 4. Stop-order compatibility spec

### Dual-stop → Binance order mapping

| Strategy stop | Binance order type | Trigger | Fill | reduceOnly |
|---------------|-------------------|---------|------|------------|
| Alpha (1.25%) | STOP_MARKET | stopPrice = entry * (1-0.0125) | market fill | true |
| Catastrophe (2.5%) | STOP_MARKET | stopPrice = entry * (1-0.025) | market fill | true |

### Order lifecycle logging

Every stop placement logs a `StopOrderEvent`:
```
request  → sent to Binance
ack      → orderId received, order accepted
reject   → Binance returned error (→ HALT)
trigger  → stop price touched, order activated
fill     → execution complete, fill price recorded
cancel   → order cancelled (position closed by other means)
```

### Halt conditions
- **Stop placement reject**: if either alpha or catastrophe stop
  fails to place → immediately cancel the entry, flatten, halt
  the candidate. A naked position without stops is forbidden.
- **Stop order disappeared**: if position query shows open
  position but stop orders are missing → re-place stops
  immediately and emit CRITICAL alert.

### Order reconciliation (every tick)
```
1. Query open orders: GET /fapi/v1/openOrders?symbol=BTCUSDT
2. Query open positions: GET /fapi/v2/positionRisk?symbol=BTCUSDT
3. If position exists but stop orders missing → re-place + alert
4. If stop orders exist but no position → cancel orphaned stops
5. Log reconciliation result
```

---

## 5. Rollout plan

### Stage 1: B_balanced_3x LIVE

**When**: after Phase 13.5 code is deployed and passes a 24h
dry-run (live executor running, reading real balances, placing
NO orders — dry_run=true mode).

**Requirements before going live**:
- [ ] Binance sub-account API key with futures trading enabled
- [ ] Sub-account funded with initial USDT deposit
- [ ] Exchange leverage set to 3x isolated for BTCUSDT
- [ ] live_executor dry-run passes 24h with no errors
- [ ] Stop order dry-run (place + cancel) succeeds
- [ ] Account balance read succeeds
- [ ] Funding rate read succeeds

**Go-live sequence**:
1. Set `dry_run = false` in deployment config
2. Monitor first regime activation → first entry → first stop placement
3. Verify stop orders visible on Binance
4. Verify telemetry records both live_equity and benchmark metrics
5. Continue monitoring for 7 days before declaring stage 1 stable

### Stage 2: B_balanced_4x paper-until-promotion

**Stays paper** until:
1. Stage 1 (3x) has been live for 30+ days with no halt triggers
2. 3x live fills match paper fills within 0.3% slippage tolerance
3. Stop orders have been correctly placed and triggered at least
   twice in live
4. No CRITICAL alerts in the last 14 days

**Promotion to live**:
- Switch B_balanced_4x to mode="live" stage=1
- Increase exchange leverage to 4x isolated
- B_balanced_3x stays live in parallel (both run on same account
  if allocation_pct allows, or on separate sub-accounts)

### Stage 3: Shadows remain paper

A_density_4x and B_balanced_5x remain paper indefinitely unless:
- Stage 2 (4x) has been live for 30+ days
- A specific operational reason justifies promoting a shadow
  (e.g., density candidate provides better sample for regime
  detection)

---

## 6. Updated promotion / fallback / halt rules

### Promotion: paper → live
- All 8 engineering gates pass for 30 consecutive days
- No CRITICAL alerts in the last 14 days
- Live fills (for already-live candidates) match paper within 0.3%
- Account balance stays above min_required_usd

### Fallback: 4x → 3x
- If B_balanced_4x live shows 3+ CRITICAL alerts in 7 days
- If any single trade loss exceeds 2x the OOS worst trade (−16.6%)
- If stop placement fails more than once in 7 days
- If slippage exceeds 0.5% average over 5+ fills
- Action: switch B_balanced_4x to paper, keep B_balanced_3x live

### Halt: candidate → offline
- If account balance drops below min_required_usd
- If stop placement fails AND re-placement also fails (naked position)
- If Binance API is unreachable for 3+ consecutive ticks
- If a position exists with no stop orders for more than 1 bar
- Action: flatten all positions immediately, halt the candidate,
  emit CRITICAL alert, require manual review before restart

### Halt: B_balanced_5x shadow
- If any shadow trade's adverse move exceeds 12%
- If shadow worst trade exceeds −12%
- Action: halt the shadow, do not restart without explicit approval

### Re-optimization trigger (unchanged from Phase 11)
- Only if 90-day realized PF < 1.0
- Or if 0 trades for 30+ consecutive days
- Or if RSI(20) regime gating is structurally broken
- Action: re-open Phase 9-level parameter sweep on frozen D1 family
