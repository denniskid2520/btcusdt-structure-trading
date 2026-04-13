#!/bin/bash
# Phase 13 — hardened deployment to AWS Lightsail
#
# Implements all 5 hardening items:
#   1. Missed-bars catch-up (in live_paper_cron.py)
#   2. Singleton lock via flock (in live_paper_cron.py + cron entry)
#   3. Atomic state writes (in live_paper_cron.py)
#   4. Binance server-time anchoring (in live_paper_cron.py)
#   5. Hard alerting to alerts.jsonl + stderr (in live_paper_cron.py)
#
# Prerequisites:
#   - SSH key at the path below
#   - Lightsail instance at 13.209.14.27
#   - Python 3.11+ on the instance

set -e

INSTANCE="13.209.14.27"
KEY_PATH="C:/Users/User/Documents/New project/.claude/btctrading.pem"
REMOTE_DIR="/home/ubuntu/btc-strategy-v2"

echo "=== Phase 13 hardened Lightsail deployment ==="

# 1. Create remote directory structure
echo "[1/6] Creating remote directories..."
ssh -i "$KEY_PATH" -o StrictHostKeyChecking=no ubuntu@$INSTANCE \
  "mkdir -p $REMOTE_DIR/{src/{adapters,data,execution,strategies,research,indicators},data/paper_state,logs,reports}"

# 2. Sync source files
echo "[2/6] Syncing source code..."
for dir in adapters execution strategies research data indicators; do
  scp -i "$KEY_PATH" -q -r \
    "src/$dir/" \
    ubuntu@$INSTANCE:$REMOTE_DIR/src/$dir/
done

# 3. Install cron jobs with flock protection
echo "[3/6] Installing cron jobs..."
ssh -i "$KEY_PATH" ubuntu@$INSTANCE bash -s << 'CRON_EOF'
REMOTE_DIR="/home/ubuntu/btc-strategy-v2"
LOCK="/tmp/paper_runner_v2.lock"

# Hourly paper tick at :05 with flock singleton
CRON_LINE="5 * * * * flock -xn $LOCK -c 'cd $REMOTE_DIR && PYTHONPATH=src python3 -m execution.live_paper_cron >> logs/cron.log 2>> logs/alerts.log'"

# Weekly reconciliation Sunday 00:15
WEEKLY_LINE="15 0 * * 0 cd $REMOTE_DIR && PYTHONPATH=src python3 -m execution.weekly_reconciliation >> logs/weekly.log 2>&1"

# Install (replace old entries if any)
(crontab -l 2>/dev/null | grep -v 'live_paper_cron' | grep -v 'weekly_reconciliation'; \
 echo "$CRON_LINE"; \
 echo "$WEEKLY_LINE") | crontab -

echo "Cron installed:"
crontab -l
CRON_EOF

# 4. Preflight: historical catch-up test
echo "[4/6] Running preflight catch-up test..."
ssh -i "$KEY_PATH" ubuntu@$INSTANCE bash -s << 'TEST_EOF'
cd /home/ubuntu/btc-strategy-v2
export PYTHONPATH=src

echo "--- Preflight: testing Binance connectivity + catch-up ---"
python3 -c "
import sys; sys.path.insert(0, 'src')
from execution.live_paper_cron import (
    fetch_binance_server_time,
    get_last_completed_1h_bar_ts,
    fetch_1h_bars_range,
)
from datetime import timedelta

server_time = fetch_binance_server_time()
last_bar = get_last_completed_1h_bar_ts(server_time)
print(f'Binance server time: {server_time}')
print(f'Last completed 1h bar: {last_bar}')

# Fetch 3 bars to verify connectivity
bars = fetch_1h_bars_range(last_bar - timedelta(hours=2), last_bar)
print(f'Fetched {len(bars)} bars')
for b in bars:
    print(f'  {b.timestamp} O={b.open:.2f} H={b.high:.2f} L={b.low:.2f} C={b.close:.2f}')
if len(bars) == 3:
    print('PREFLIGHT: Binance connectivity OK')
else:
    print('PREFLIGHT: WARN - expected 3 bars')
"

echo ""
echo "--- Preflight: running one full tick (catch-up mode) ---"
PYTHONPATH=src python3 -m execution.live_paper_cron

echo ""
echo "--- Preflight: verifying state files ---"
for cid in B_balanced_4x B_balanced_3x A_density_4x B_balanced_5x; do
  dir="data/paper_state/$cid"
  if [ -f "$dir/state.json" ] && [ -f "$dir/last_processed_ts.txt" ]; then
    echo "  $cid: state.json OK, last_ts=$(cat $dir/last_processed_ts.txt)"
  else
    echo "  $cid: MISSING state files"
  fi
done

echo ""
echo "--- Preflight: checking alerts ---"
if [ -f logs/alerts.jsonl ]; then
  alerts=$(wc -l < logs/alerts.jsonl)
  echo "Alerts: $alerts"
  if [ "$alerts" -gt 0 ]; then
    echo "Last alert:"
    tail -1 logs/alerts.jsonl
  fi
else
  echo "No alerts file (clean start)"
fi
TEST_EOF

# 5. Verify cron will fire
echo "[5/6] Verifying cron schedule..."
ssh -i "$KEY_PATH" ubuntu@$INSTANCE "crontab -l | grep paper"

# 6. Done
echo ""
echo "[6/6] Deployment complete"
echo ""
echo "  Instance:     $INSTANCE"
echo "  Remote dir:   $REMOTE_DIR"
echo "  Cron:         every hour at :05 (with flock)"
echo "  Weekly:       Sunday 00:15"
echo "  Logs:         $REMOTE_DIR/logs/cron.log"
echo "  Alerts:       $REMOTE_DIR/logs/alerts.log + alerts.jsonl"
echo "  State:        $REMOTE_DIR/data/paper_state/{candidate}/"
echo "  Telemetry:    $REMOTE_DIR/data/paper_state/{candidate}/telemetry.jsonl"
echo ""
echo "  Monitor alerts: ssh -i KEY ubuntu@$INSTANCE 'tail -f $REMOTE_DIR/logs/alerts.log'"
echo "  Check state:    ssh -i KEY ubuntu@$INSTANCE 'cat $REMOTE_DIR/data/paper_state/B_balanced_4x/state.json'"
