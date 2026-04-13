# Phase 13.6 — Stop Semantics Correction + Micro-Live Acceptance

_Date: 2026-04-12_
_Status: deployment-only fix, no strategy logic changes_

---

## 1. Corrected stop semantics spec

### The problem with Phase 13.5

Phase 13.5 mapped BOTH stops to exchange STOP_MARKET orders:
```
alpha stop     → STOP_MARKET at entry * (1 - 0.0125)  ← WRONG
catastrophe    → STOP_MARKET at entry * (1 - 0.025)   ← correct
```

The alpha stop as STOP_MARKET changes the strategy from
**close-stop semantics** (check bar close, exit at next open) to
**intrabar-stop semantics** (check wick, exit at stop level). This
breaks parity with the backtest which uses `strategy_close_stop`.

### Corrected model

```
ALPHA STOP = CLIENT-SIDE CLOSE-STOP
  - evaluated by the paper_runner_v2 on each completed 1h bar
  - condition: bar.close <= alpha_stop_level
  - action: queue exit, execute MARKET order at next bar open
  - NO exchange order placed
  - matches backtest strategy_close_stop semantics exactly

CATASTROPHE STOP = EXCHANGE-SIDE STOP_MARKET
  - placed as a resting STOP_MARKET order on Binance
  - condition: price touches catastrophe level intrabar (wick)
  - action: exchange triggers market fill automatically
  - reduceOnly = true
  - full lifecycle logging: request → ack → reject → trigger → fill
  - this is pure tail protection, not strategy timing
```

### Why this matters

The OOS backtest that produced +848% / PF 4.63 / 69.3% WR used
`strategy_close_stop` for the alpha stop. If we switch to intrabar
trigger in live, the alpha stop fires on wicks that the strategy
intentionally ignores — it would exit trades prematurely on noise
that the close-based check would have survived. This breaks the
parity between backtest and live, making the OOS metrics unreliable
as a predictor of live performance.

The catastrophe stop at 2.5% is deliberately wider and exists for
a different purpose: protecting against flash crashes between bar
closes. It fires only on extreme intrabar moves that the client-
side check cannot catch. Putting this on the exchange is correct.

### Exit priority order (unchanged from backtest)

```
1. Catastrophe stop (exchange wick)  — highest priority
2. Alpha stop (client close check)   — fires on bar.close breach
3. Hold expiry (client bar count)    — time stop
4. Opposite flip (client RSI check)  — regime reversal
```

If catastrophe fires intrabar, the alpha close-check is moot
(position already closed). If alpha fires on close, the exit
happens at next bar open (catastrophe may or may not have also
been touching — catastrophe would have fired first if it was
breached during the bar).

---

## 2. Updated live rollout spec

### Stage 0: Dry-run (24 hours)

```
B_balanced_3x, dry_run=true
  - reads real Binance balance
  - computes real signals
  - logs all decisions
  - places NO orders (entry, exit, or stop)
  - validates: balance read, bar detection, signal timing,
    regime activation, alpha stop evaluation, catastrophe
    stop level computation
```

Pass criteria: 24 consecutive hours with no errors, correct
bar-close detection, balance reads succeed.

### Stage 1: Micro-live (3-7 days)

```
B_balanced_3x, dry_run=false, max_cap_usd=<user_set>
  - real money enabled
  - equity_linked with temporary max_cap_usd
  - strategy_equity = min(availableBalance * allocation_pct, max_cap_usd)
  - real entries via MARKET order
  - real catastrophe stop via STOP_MARKET
  - alpha stop: client-side only (no exchange order)
  - real exits via MARKET order at next bar open
```

Objective: validate the full exchange execution path with a
capped amount before committing the full balance.

### Stage 2: Full live

```
B_balanced_3x, dry_run=false, max_cap_usd=null
  - max_cap_usd removed
  - strategy_equity = availableBalance * allocation_pct
  - full equity-linked mode
```

### Stages for other candidates (unchanged)

- B_balanced_4x: paper only (stage 2, no real money)
- A_density_4x: shadow paper (stage 3)
- B_balanced_5x: shadow paper (stage 3)

---

## 3. Micro-live acceptance checklist

Run B_balanced_3x in micro-live for 3-7 days. Check each item:

### Entry execution
```
[ ] First regime activation detected correctly
[ ] Entry MARKET order placed at correct price
[ ] Fill price logged and within 0.1% of intended
[ ] actual_frac computed from strategy_equity correctly
[ ] Position visible on Binance after entry
```

### Catastrophe stop placement
```
[ ] STOP_MARKET order placed immediately after entry fill
[ ] Stop price = fill_price * (1 - 0.025) to 2 decimal places
[ ] Order acknowledged by Binance (orderId received)
[ ] Order visible in GET /fapi/v1/openOrders
[ ] reduceOnly = true confirmed
```

### Alpha stop (client-side)
```
[ ] Alpha level computed = fill_price * (1 - 0.0125)
[ ] Evaluated on each completed 1h bar
[ ] Does NOT place any exchange order
[ ] If triggered: exit queued for next bar open
```

### Position reconciliation
```
[ ] Every tick: position query matches expected state
[ ] If position exists but catastrophe stop missing → re-place + CRITICAL
[ ] If no position but catastrophe stop exists → cancel + log
```

### Exit execution
```
[ ] Time-stop exit: MARKET order at bar open after hold expiry
[ ] Alpha-stop exit: MARKET order at bar open after close breach
[ ] Catastrophe stop: exchange-triggered (verify via order status)
[ ] After exit: catastrophe stop order cancelled (if not already triggered)
[ ] Post-exit position = 0 confirmed
```

### Funding reconciliation
```
[ ] Funding rate fetched at settlement bars (00/08/16 UTC)
[ ] Funding PnL logged per trade
[ ] Reconciles with Binance income history within 0.01%
```

### Balance tracking
```
[ ] availableBalance read succeeds every tick
[ ] strategy_equity computed correctly
[ ] PnL matches: post_balance - pre_balance ≈ trade net_pnl
[ ] Both live_equity and benchmark_normalized logged
```

### Safety
```
[ ] No naked position (position without catastrophe stop) for > 1 bar
[ ] min_required_usd halt works (would halt if balance drops below)
[ ] max_cap_usd correctly caps strategy_equity during micro-live
[ ] All CRITICAL alerts fire correctly on simulated failures
```

---

## 4. Promotion rules

### Micro-live → full live

Promote B_balanced_3x from micro-live to full live when:
1. All checklist items above pass
2. At least 1 complete trade cycle (entry → hold → exit) observed
3. Fill slippage on all fills ≤ 0.3%
4. Catastrophe stop correctly placed on every entry
5. No CRITICAL alerts in the micro-live period
6. Balance reconciliation within 0.1% on all trades

Action: set `max_cap_usd = null` in the deployment config.

### Full live → 4x paper promotion (unchanged from Phase 13.5)

Promote B_balanced_4x from paper to live when:
1. B_balanced_3x has been full-live for 30+ days
2. 3x live fills match paper within 0.3%
3. Stop orders correctly placed and triggered at least twice
4. No CRITICAL alerts in last 14 days

### Halt conditions (unchanged)

- Balance below min_required_usd → flatten + halt
- Catastrophe stop placement fails → flatten + halt
- Naked position > 1 bar → flatten + halt + CRITICAL
- Binance API unreachable 3+ consecutive ticks → halt
