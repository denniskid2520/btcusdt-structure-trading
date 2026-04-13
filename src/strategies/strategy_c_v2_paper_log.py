"""Strategy C v2 Phase 7 paper-trade telemetry schema.

`PaperTradeLogEntry` captures every field the Phase 7 brief enumerates
for each closed paper trade. It is intentionally side-effect-free: the
live runner builds these entries and writes them to a JSONL or CSV
journal; this module defines only the shape.

Fields map 1:1 to the Phase 7 brief's "For every paper trade log" list.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

Side = Literal["long", "short"]
StopSemantics = Literal["strategy_close_stop", "exchange_intrabar_stop"]


@dataclass(frozen=True)
class PaperTradeLogEntry:
    """One closed paper trade — full execution + PnL telemetry.

    The live runner produces one of these per trade close, appends it
    to the JSONL journal, and uses it for daily / weekly reconciliation.
    """
    # Identity
    cell_label: str                     # "D1_long_primary" / "C_long_backup" / "D1_long_frac2_shadow"

    # Timing
    signal_timestamp: datetime          # bar close when the signal was evaluated
    completed_bar_timestamp: datetime   # the 4h bar whose close triggered the entry

    # Entry
    intended_entry_price: float         # modelled entry (next-bar open)
    paper_fill_entry: float             # what the paper runner actually recorded
    side: Side

    # Stop framework
    stop_semantics: StopSemantics
    stop_level: float                   # absolute price at which the stop fires
    stop_trigger_timestamp: datetime | None = None   # bar where the stop triggered, if any
    stop_fill_price: float | None = None             # actual paper fill price at stop
    stop_slippage_vs_model: float | None = None      # (paper_fill - model_fill) / model_fill

    # Position sizing
    actual_position_frac: float = 1.0   # notional as fraction of equity at entry

    # Lifecycle
    exit_reason: str = "time_stop"      # "time_stop" / "opposite_flip" / "stop_loss_*" / "end_of_series"
    exit_timestamp: datetime | None = None
    exit_price: float | None = None
    hold_bars: int = 0

    # PnL decomposition
    gross_pnl: float = 0.0              # (exit - entry)/entry * side * position_frac
    funding_pnl: float = 0.0            # -side * sum(funding) * position_frac
    cost_pnl: float = 0.0               # -round_trip_cost * position_frac
    net_pnl: float = 0.0                # gross + funding - cost

    # Monitor / safety flags
    monitor_flags: list[str] = field(default_factory=list)
    # e.g. ["hostile_funding_flagged_long", "wick_near_stop", "slippage_alert"]

    def to_dict(self) -> dict[str, Any]:
        """Flatten for CSV / JSONL serialisation."""
        d = asdict(self)
        # Serialise datetimes to ISO strings for easy journalling.
        for k in (
            "signal_timestamp",
            "completed_bar_timestamp",
            "stop_trigger_timestamp",
            "exit_timestamp",
        ):
            v = d.get(k)
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        d["monitor_flags"] = ",".join(self.monitor_flags) if self.monitor_flags else ""
        return d
