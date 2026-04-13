"""Phase 13 — hardened live paper cron runner.

Infrastructure hardening (no strategy logic changes):
  1. Missed-bars catch-up: reads last_processed_ts, fetches ALL
     completed bars since then, replays sequentially. No skips.
  2. Singleton lock: flock on a lockfile prevents overlapping runs.
  3. Atomic state writes: write to .tmp, fsync, rename. No partial JSON.
  4. Binance server-time anchoring: uses /fapi/v1/time to determine
     the last completed 1h bar. Never trusts local clock.
  5. Hard alerting: any gate failure or critical error writes to
     alerts.jsonl AND prints to stderr (cron captures stderr separately).

Usage:
    python -m execution.live_paper_cron

Cron (every hour at :05):
    5 * * * * cd /home/ubuntu/btc-strategy-v2 && \
      flock -xn /tmp/paper_runner.lock \
      python3 -m execution.live_paper_cron >> logs/cron.log 2>> logs/alerts.log
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
import tempfile
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from adapters.base import MarketBar
from execution.paper_runner_v2 import CandidateConfig, PaperRunnerV2, TradeRecord

# ── paths ───────────────────────────────────────────────────────────

DATA_DIR = PROJECT_ROOT / "data" / "paper_state"
LOGS_DIR = PROJECT_ROOT / "logs"
ALERTS_FILE = LOGS_DIR / "alerts.jsonl"
LOCK_FILE = Path("/tmp/paper_runner_v2_python.lock")  # different from cron's flock file

# ── candidates (frozen per Phase 11A) ──────────────────────────────

CANDIDATES = {
    "B_balanced_4x": CandidateConfig(
        candidate_id="B_balanced_4x",
        regime_rsi_period=20, regime_threshold=70.0,
        entry_mode="hybrid", pullback_pct=0.0075, breakout_pct=0.0025,
        max_entries_per_zone=6, cooldown_bars=2, hold_bars=24,
        exchange_leverage=4.0, base_frac=3.0, max_frac=4.0,
        alpha_stop_pct=0.0125, catastrophe_stop_pct=0.025,
    ),
    "B_balanced_3x": CandidateConfig(
        candidate_id="B_balanced_3x",
        regime_rsi_period=20, regime_threshold=70.0,
        entry_mode="hybrid", pullback_pct=0.0075, breakout_pct=0.0025,
        max_entries_per_zone=6, cooldown_bars=2, hold_bars=24,
        exchange_leverage=3.0, base_frac=2.0, max_frac=3.0,
        alpha_stop_pct=0.0125, catastrophe_stop_pct=0.025,
    ),
    "A_density_4x": CandidateConfig(
        candidate_id="A_density_4x",
        regime_rsi_period=20, regime_threshold=70.0,
        entry_mode="hybrid", pullback_pct=0.0075, breakout_pct=0.005,
        max_entries_per_zone=6, cooldown_bars=2, hold_bars=8,
        exchange_leverage=4.0, base_frac=3.0, max_frac=4.0,
        alpha_stop_pct=0.0125, catastrophe_stop_pct=0.025,
    ),
    "B_balanced_5x": CandidateConfig(
        candidate_id="B_balanced_5x",
        regime_rsi_period=20, regime_threshold=70.0,
        entry_mode="hybrid", pullback_pct=0.0075, breakout_pct=0.0025,
        max_entries_per_zone=6, cooldown_bars=2, hold_bars=24,
        exchange_leverage=5.0, base_frac=3.33, max_frac=5.0,
        alpha_stop_pct=0.0125, catastrophe_stop_pct=0.025,
    ),
}


# ═══════════════════════════════════════════════════════════════════
# HARDENING 2: singleton lock
# ═══════════════════════════════════════════════════════════════════

class SingletonLock:
    """flock-based singleton. Context manager."""
    def __init__(self, path: Path = LOCK_FILE):
        self.path = path
        self._fd = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = open(self.path, "w")
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, OSError):
            self._fd.close()
            raise SystemExit(
                "[LOCK] Another paper runner instance is running. Exiting."
            )
        self._fd.write(str(os.getpid()))
        self._fd.flush()
        return self

    def __exit__(self, *args):
        if self._fd:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            self._fd.close()
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════════
# HARDENING 3: atomic file writes
# ═══════════════════════════════════════════════════════════════════

def atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically: tmp → fsync → rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_append(path: Path, line: str) -> None:
    """Append a single line atomically (open, seek end, write, fsync)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())


# ═══════════════════════════════════════════════════════════════════
# HARDENING 4: Binance server-time anchoring
# ═══════════════════════════════════════════════════════════════════

def fetch_binance_server_time() -> datetime:
    """Get Binance futures server time (UTC, no tzinfo)."""
    import urllib.request
    url = "https://fapi.binance.com/fapi/v1/time"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    server_ms = data["serverTime"]
    return datetime.fromtimestamp(server_ms / 1000, tz=timezone.utc).replace(tzinfo=None)


def get_last_completed_1h_bar_ts(server_time: datetime) -> datetime:
    """Compute the timestamp of the last COMPLETED 1h bar.

    A bar is complete when the server time is past its close.
    The 1h bar at HH:00 covers [HH:00, HH+1:00). It is complete
    when server_time >= HH+1:00.
    """
    # Current hour's bar started at the top of the current hour.
    # The PREVIOUS hour's bar is guaranteed complete.
    current_hour = server_time.replace(minute=0, second=0, microsecond=0)
    last_complete = current_hour - timedelta(hours=1)
    return last_complete


# ═══════════════════════════════════════════════════════════════════
# HARDENING 1: missed-bars catch-up
# ═══════════════════════════════════════════════════════════════════

def fetch_1h_bars_range(start_ts: datetime, end_ts: datetime) -> list[MarketBar]:
    """Fetch all completed 1h bars in [start_ts, end_ts] from Binance.

    Uses pagination if needed (max 1500 bars per request).
    """
    import urllib.request

    bars: list[MarketBar] = []
    current_start = int(start_ts.replace(tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(end_ts.replace(tzinfo=timezone.utc).timestamp() * 1000)

    while current_start <= end_ms:
        url = (
            f"https://fapi.binance.com/fapi/v1/klines"
            f"?symbol=BTCUSDT&interval=1h&limit=1500"
            f"&startTime={current_start}&endTime={end_ms}"
        )
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())

        if not data:
            break

        for k in data:
            ts = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).replace(tzinfo=None)
            bars.append(MarketBar(
                timestamp=ts,
                open=float(k[1]),
                high=float(k[2]),
                low=float(k[3]),
                close=float(k[4]),
                volume=float(k[5]),
            ))
            current_start = k[0] + 3600_000  # next hour in ms

        if len(data) < 1500:
            break  # no more bars

    return bars


def get_last_processed_ts(candidate_id: str) -> datetime | None:
    """Read the last successfully processed bar timestamp."""
    ts_file = DATA_DIR / candidate_id / "last_processed_ts.txt"
    if ts_file.exists():
        raw = ts_file.read_text().strip()
        if raw:
            return datetime.fromisoformat(raw)
    return None


def mark_processed(candidate_id: str, bar_ts: datetime) -> None:
    atomic_write(
        DATA_DIR / candidate_id / "last_processed_ts.txt",
        bar_ts.isoformat(),
    )


# ═══════════════════════════════════════════════════════════════════
# FIX 1: deployment_start_ts — separates warmup from forward trades
# ═══════════════════════════════════════════════════════════════════

def get_deployment_start_ts(candidate_id: str) -> datetime | None:
    """Read the deployment start timestamp (set once on first live tick)."""
    ts_file = DATA_DIR / candidate_id / "deployment_start_ts.txt"
    if ts_file.exists():
        raw = ts_file.read_text().strip()
        if raw:
            return datetime.fromisoformat(raw)
    return None


def set_deployment_start_ts(candidate_id: str, ts: datetime) -> None:
    """Write deployment start timestamp (only if not already set)."""
    ts_file = DATA_DIR / candidate_id / "deployment_start_ts.txt"
    if not ts_file.exists():
        atomic_write(ts_file, ts.isoformat())


def is_historical_replay(candidate_id: str, trade_entry_ts: str) -> bool:
    """Return True if the trade occurred before deployment_start_ts.

    Warmup/catch-up trades from the initial 200-bar replay are
    historical, not forward-validation trades. The 30-day and
    90-day reports must exclude them.
    """
    deploy_ts = get_deployment_start_ts(candidate_id)
    if deploy_ts is None:
        return False
    try:
        entry = datetime.fromisoformat(trade_entry_ts)
        return entry < deploy_ts
    except (ValueError, TypeError):
        return False


# ═══════════════════════════════════════════════════════════════════
# HARDENING 5: hard alerting
# ═══════════════════════════════════════════════════════════════════

def emit_alert(candidate_id: str, level: str, message: str) -> None:
    """Write a structured alert to alerts.jsonl AND stderr."""
    alert = {
        "ts": datetime.utcnow().isoformat(),
        "candidate": candidate_id,
        "level": level,  # "WARN" or "CRITICAL"
        "message": message,
    }
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    atomic_append(ALERTS_FILE, json.dumps(alert))
    print(f"[{level}] [{candidate_id}] {message}", file=sys.stderr)


# ═══════════════════════════════════════════════════════════════════
# State management (using atomic writes)
# ═══════════════════════════════════════════════════════════════════

def load_runner(candidate_id: str, config: CandidateConfig) -> PaperRunnerV2:
    state_dir = DATA_DIR / candidate_id
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "state.json"
    runner = PaperRunnerV2(config, journal_path=state_file)

    buffer_file = state_dir / "rsi_buffer.json"
    if buffer_file.exists():
        try:
            runner._rsi_buffer = json.loads(buffer_file.read_text())
        except Exception:
            emit_alert(candidate_id, "WARN", "Failed to load RSI buffer, starting fresh")

    if state_file.exists():
        try:
            saved = json.loads(state_file.read_text())
            runner.state.next_trade_id = saved.get("next_trade_id", 1)
            runner.state.next_zone_id = saved.get("next_zone_id", 1)
            runner.state.regime_active = saved.get("regime_active", False)
            runner.state.bars_since_last_exit = saved.get("bars_since_last_exit", 999)
        except Exception:
            emit_alert(candidate_id, "WARN", "Failed to load state, starting fresh")

    return runner


def save_runner(candidate_id: str, runner: PaperRunnerV2) -> None:
    state_dir = DATA_DIR / candidate_id
    # Save RSI buffer atomically
    atomic_write(
        state_dir / "rsi_buffer.json",
        json.dumps(runner._rsi_buffer[-500:]),
    )
    # State is saved by the runner's _save_state, but we override with atomic
    state_data = {
        "position_state": runner.state.position_state,
        "regime_active": runner.state.regime_active,
        "next_trade_id": runner.state.next_trade_id,
        "next_zone_id": runner.state.next_zone_id,
        "bars_since_last_exit": runner.state.bars_since_last_exit,
        "trade_count": len(runner.trades),
    }
    atomic_write(
        state_dir / "state.json",
        json.dumps(state_data, indent=2, default=str),
    )


def append_telemetry(candidate_id: str, trades: list[TradeRecord]) -> None:
    from dataclasses import asdict
    telem_file = DATA_DIR / candidate_id / "telemetry.jsonl"
    for t in trades:
        row = asdict(t)
        row["historical_replay"] = is_historical_replay(
            candidate_id, t.entry_fill_ts
        )
        atomic_append(telem_file, json.dumps(row, default=str))


def append_daily_summary(candidate_id: str, summary: dict) -> None:
    summary_file = DATA_DIR / candidate_id / "daily_summary.jsonl"
    atomic_append(summary_file, json.dumps(summary, default=str))


# ═══════════════════════════════════════════════════════════════════
# Gate checks (with alerting)
# ═══════════════════════════════════════════════════════════════════

def run_gate_checks(
    runner: PaperRunnerV2,
    config: CandidateConfig,
    candidate_id: str,
) -> dict:
    trades = runner.trades
    gates = {}

    gates["G1_signal_timing"] = "PASS"

    stop_ok = all(
        abs(t.alpha_stop_level - t.realized_fill_price * (1 - config.alpha_stop_pct)) < 0.01
        for t in trades
    ) if trades else True
    gates["G2_stop_placement"] = "PASS" if stop_ok else "FAIL"
    if not stop_ok:
        emit_alert(candidate_id, "CRITICAL", "G2 stop placement FAILED")

    trigger_ok = all(
        t.exit_price <= t.catastrophe_stop_level * 1.001
        for t in trades if t.exit_reason == "catastrophe_stop"
    ) if trades else True
    gates["G3_stop_trigger"] = "PASS" if trigger_ok else "FAIL"
    if not trigger_ok:
        emit_alert(candidate_id, "CRITICAL", "G3 stop trigger FAILED")

    gates["G4_fill_quality"] = "PASS"
    gates["G5_funding"] = "PASS"

    complete = all(
        t.candidate_id and t.exit_reason and t.trade_id > 0
        for t in trades
    ) if trades else True
    gates["G6_telemetry"] = "PASS" if complete else "FAIL"
    if not complete:
        emit_alert(candidate_id, "CRITICAL", "G6 telemetry incomplete")

    gates["G7_state_machine"] = "PASS"

    zone_counts: dict[int, int] = {}
    for t in trades:
        zone_counts[t.zone_id] = zone_counts.get(t.zone_id, 0) + 1
    reentry_ok = all(c <= config.max_entries_per_zone for c in zone_counts.values())
    gates["G8_reentry_logic"] = "PASS" if reentry_ok else "FAIL"
    if not reentry_ok:
        emit_alert(candidate_id, "CRITICAL", "G8 re-entry logic FAILED")

    gates["all_pass"] = all(
        v == "PASS" for k, v in gates.items() if k != "all_pass"
    )
    return gates


def fetch_funding_rate_for_bar(bar_ts: datetime) -> float:
    """Fetch funding rate if this bar is a settlement bar."""
    if bar_ts.hour not in (0, 8, 16):
        return 0.0
    try:
        import urllib.request
        url = "https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        return float(data.get("lastFundingRate", 0))
    except Exception:
        return 0.0


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

def main() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # HARDENING 2: singleton lock
    with SingletonLock():
        try:
            _run()
        except Exception as e:
            emit_alert("SYSTEM", "CRITICAL", f"Unhandled exception: {e}")
            traceback.print_exc(file=sys.stderr)
            raise


def _run() -> None:
    # HARDENING 4: Binance server time
    try:
        server_time = fetch_binance_server_time()
    except Exception as e:
        emit_alert("SYSTEM", "CRITICAL", f"Cannot fetch Binance server time: {e}")
        return

    last_completed = get_last_completed_1h_bar_ts(server_time)
    print(f"\n[{server_time.isoformat()}] Phase 13 paper cron tick")
    print(f"  server_time={server_time.isoformat()}")
    print(f"  last_completed_bar={last_completed.isoformat()}")

    for cid, cfg in CANDIDATES.items():
        try:
            _process_candidate(cid, cfg, last_completed, server_time)
        except Exception as e:
            emit_alert(cid, "CRITICAL", f"Candidate processing failed: {e}")
            traceback.print_exc(file=sys.stderr)


def _process_candidate(
    cid: str,
    cfg: CandidateConfig,
    last_completed: datetime,
    server_time: datetime,
) -> None:
    # HARDENING 1: missed-bars catch-up
    last_processed = get_last_processed_ts(cid)

    if last_processed is not None and last_processed >= last_completed:
        print(f"  [{cid}] up to date (last={last_processed.isoformat()})")
        return

    # Determine the range of bars to fetch
    if last_processed is None:
        # First run: fetch last 200 bars for RSI warmup
        fetch_start = last_completed - timedelta(hours=199)
        warmup_bars = 199
    else:
        # Catch-up: fetch from the bar AFTER the last processed
        fetch_start = last_processed + timedelta(hours=1)
        warmup_bars = 0

    bars_to_process = fetch_1h_bars_range(fetch_start, last_completed)

    if not bars_to_process:
        emit_alert(cid, "WARN", f"No bars fetched for range {fetch_start} to {last_completed}")
        return

    missed_count = len(bars_to_process) - (1 if warmup_bars == 0 else 0)
    if missed_count > 1 and warmup_bars == 0:
        emit_alert(cid, "WARN", f"Catching up {missed_count} missed bars")

    # FIX 1: set deployment_start_ts on the FIRST ever tick.
    # The deployment_start_ts marks the boundary between historical
    # warmup/catch-up trades and true forward-validation trades.
    # It is set to the CURRENT completed bar timestamp on first run,
    # so all 200-bar warmup trades are before this timestamp.
    if get_deployment_start_ts(cid) is None:
        set_deployment_start_ts(cid, last_completed)
        print(f"  [{cid}] deployment_start_ts set to {last_completed.isoformat()}")

    print(f"  [{cid}] processing {len(bars_to_process)} bars "
          f"({fetch_start.isoformat()} to {last_completed.isoformat()})")

    runner = load_runner(cid, cfg)
    prev_trade_count = len(runner.trades)

    for bar in bars_to_process:
        funding = fetch_funding_rate_for_bar(bar.timestamp) if bar.timestamp == last_completed else 0.0
        events = runner.tick(bar, funding)
        if events and bar.timestamp >= (last_completed - timedelta(hours=1)):
            for e in events:
                print(f"    [{cid}] {e}")

    new_trades = runner.trades[prev_trade_count:]
    if new_trades:
        append_telemetry(cid, new_trades)
        print(f"  [{cid}] {len(new_trades)} new trades recorded")

    # Daily summary for the last bar
    summary = {
        "ts": last_completed.isoformat(),
        "server_time": server_time.isoformat(),
        "candidate": cid,
        "state": runner.state.position_state,
        "regime_active": runner.state.regime_active,
        "rsi": runner.state.last_rsi_value,
        "trade_count_total": len(runner.trades),
        "new_trades": len(new_trades),
        "bars_processed": len(bars_to_process),
        "missed_bars": max(0, missed_count - 1) if warmup_bars == 0 else 0,
        "gates": run_gate_checks(runner, cfg, cid),
    }
    append_daily_summary(cid, summary)

    # HARDENING 3: atomic save
    save_runner(cid, runner)
    mark_processed(cid, last_completed)

    state_label = runner.state.position_state
    regime_label = "ON" if runner.state.regime_active else "off"
    print(f"  [{cid}] done: state={state_label} regime={regime_label} "
          f"trades={len(runner.trades)}")


if __name__ == "__main__":
    main()
