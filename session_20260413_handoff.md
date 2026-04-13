# Session handoff — 2026-04-13

## Status: 24h dry-run in progress, check results when resuming

### What's running on Lightsail (13.209.14.27)

1. **Dry-run service** — `screen -r live_dryrun` (PID 36713)
   - B_balanced_3x only, dry_run=true (reads data, no orders)
   - Started: 2026-04-12 13:07 UTC
   - 24h window ends: 2026-04-13 ~13:07 UTC
   - Polling every 10s, processes completed 1h bars
   - Logs: `data/live_state/B_balanced_3x/service.log`
   - Events: `data/live_state/B_balanced_3x/events.jsonl`
   - State: `data/live_state/B_balanced_3x/state.json`
   - Alerts: `logs/live_alerts.jsonl`

2. **Paper cron** (hourly at :05) — all 4 candidates
   - Lock fix deployed (python uses _python.lock, shell uses .lock)
   - State: `data/paper_state/{candidate}/`

3. **API key** in `.env` (NOT in git):
   - Key: KwKTUk0Cy79cHDI3sJS... (Binance sub-account)
   - Balance at deployment: $10,000 USDT

### Resume checklist for tomorrow

1. **Pull 24h dry-run report** — run this:
   ```bash
   KEY="C:\Users\User\Documents\New project\.claude\btctrading.pem"
   ssh -i "$KEY" ubuntu@13.209.14.27 "tail -50 /home/ubuntu/btc-strategy-v2/data/live_state/B_balanced_3x/service.log"
   ssh -i "$KEY" ubuntu@13.209.14.27 "cat /home/ubuntu/btc-strategy-v2/data/live_state/B_balanced_3x/events.jsonl"
   ssh -i "$KEY" ubuntu@13.209.14.27 "cat /home/ubuntu/btc-strategy-v2/logs/live_alerts.jsonl 2>/dev/null || echo none"
   ```

2. **Produce the 24h engineering report** with stricter format:
   - expected 1h bars (24) vs processed
   - missed-bar catch-up count
   - duplicate-bar prevention count
   - 4h regime update count (expect 6)
   - balance-read success rate
   - telemetry write success rate
   - WARN / CRITICAL count
   - state-machine violation count
   - signal count / trade count
   - explicit zero-trade-is-ok statement if regime stayed off

3. **If dry-run is clean** → proceed to Phase 14B:
   - Write systemd unit file (replace screen)
   - Set max_cap_usd for micro-live
   - Flip dry_run=false for B_balanced_3x only
   - Set Binance leverage to 3x isolated BTCUSDT
   - Begin micro-live acceptance (3-7 days)

### Git state
- Committed: `8115fd1` on `claude/strategy-c-orderflow`
- Pushed to: https://github.com/denniskid2520/btcusdt-structure-trading/tree/claude/strategy-c-orderflow
- 89 files, 29,000 lines, 1043 tests passing
- .env NOT in git (contains API keys)

### Frozen candidate stack (DO NOT CHANGE)
- B_balanced_3x = first live (stage 1, micro-live next)
- B_balanced_4x = paper only (stage 2)
- A_density_4x = shadow/paper (stage 3)
- B_balanced_5x = shadow/paper (stage 3)

### DO NOT
- Change strategy logic
- Reopen research
- Change the candidate stack
- Modify parameters
