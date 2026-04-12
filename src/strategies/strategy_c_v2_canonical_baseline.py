"""Strategy C v2 Phase 8 — canonical baseline single-source-of-truth.

This module is the **only** place the Phase 8 canonical OOS metrics
for the deployment cells are stored. Reports, the report consistency
guard, retrospective paper runs, and the day-30 recommendation logic
all import from here.

## Three concepts, kept strictly separate

Every canonical cell records three ORTHOGONAL concepts that must
never be conflated in a report:

1. **Exchange leverage** (`exchange_leverage`): the exchange setting
   the sleeve runs at. For Phase 8 all cells use **2x** on Binance
   USDT-M perpetuals. This is a property of the exchange account,
   not of the strategy.

2. **actual_frac** (`actual_frac` + dynamic multiplier range): the
   strategy's effective notional as a fraction of the SLEEVE
   equity. For fixed cells this is a single constant. For dynamic
   cells this is a base value multiplied by a per-trade conviction
   multiplier in [0.5, 1.5]. **This is the strategy-level exposure
   and is what every Phase 8 OOS number is computed on.**

3. **Portfolio allocation** (`portfolio_allocation_default`,
   discussed separately): an account-level layer on top of the
   sleeve. A sleeve can be deployed at 1.0x (full), 0.5x, 0.25x,
   etc. of its intended equity. The canonical metrics ALWAYS
   assume 1.0x allocation — the strategy-level result. Reports may
   additionally discuss reduced allocations as a separate layer at
   the END, but the canonical numbers are never "diluted" by
   pretending a lower allocation is the primary result.

Reports violate the strict-separation rule if they:
- Present strategy return multiplied by 0.5 (half allocation) as
  the "deployment" number
- Mix actual_frac and exchange_leverage into a single "leverage"
  value
- Use allocation as an excuse to report lower-than-real strategy DD

The strategy-level leveraged futures result always comes first.

## Why this module exists

The Phase 6 final-recommendation report listed D1_long_primary at
+173.06% OOS / DD 9.27%. The underlying Phase 6 expanded-sweep CSV
had it at +143.45% / DD 12.97%. The discrepancy was a transcription
error that nothing caught. Seven subsequent independent measurements
all confirmed the CSV's number.

Going forward, recommendation numbers cannot be hand-typed into a
report narrative — they must come from this file (for canonical
cells) or from an explicitly cited source CSV row (for research
cells outside the deployment stack). The consistency guard
(`strategy_c_v2_report_consistency`) enforces this.

## What this file contains

Six `CanonicalCell` records for the Phase 8 deployment stack:

    - D1_long_primary             — PRIMARY, fixed frac=1.333
    - D1_long_dynamic             — SHADOW, dynamic [0.667, 2.000]
    - D1_long_dynamic_adaptive    — SHADOW, dynamic + adaptive hold
    - D1_long_frac2_shadow        — SHADOW, fixed frac=2.000 (max)
    - C_long_backup               — BACKUP, fixed frac=1.000
    - C_long_dynamic              — SHADOW, dynamic [0.500, 1.500]

Each record has:
    .cell_id          — unique string identifier
    .config           — CanonicalCellConfig (exchange_leverage,
                        actual_frac, stop params, etc.)
    .metrics          — CanonicalMetrics (OOS return, DD, etc.)
    .measured_at      — ISO date of the canonical walk-forward run
    .source_report    — path to the baseline reconciliation report
    .notes            — free-form provenance

All metrics were produced by fresh canonical walk-forward runs on
2026-04-12 and match prior independent measurements to the basis
point. See `strategy_c_v2_phase8_canonical_baseline.md`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


StopSemantics = Literal["strategy_close_stop", "exchange_intrabar_stop"]
StopTrigger = Literal["close", "wick"]
SignalFamily = Literal["rsi_only", "rsi_and_macd"]
SleeveRole = Literal["primary", "backup", "shadow"]


@dataclass(frozen=True)
class CanonicalCellConfig:
    """Exact backtest/live parameters for one canonical cell.

    Every field here must match the Phase 8 baseline report §3 and
    must be what the backtester consumed when it produced the metrics
    in the matching `CanonicalMetrics` record.

    The three strictly-separated concepts:
        exchange_leverage: the exchange setting (e.g., 2.0 for 2x
            perpetuals). This is NOT the strategy's effective
            exposure; it's what the exchange thinks the account is
            running.
        actual_frac: the strategy's effective notional as a fraction
            of SLEEVE equity on a typical trade. For fixed cells this
            is a constant. For dynamic cells this is the BASE value;
            the per-trade multiplier modifies it in [0.5, 1.5].
        portfolio_allocation_default: a separate account-level layer
            on top of the sleeve. 1.0 means the sleeve is deployed at
            full equity; 0.5 means half, etc. Canonical metrics are
            always computed at 1.0.
    """
    signal_family: SignalFamily
    rsi_period: int
    side: Literal["long", "short"]
    hold_bars: int
    stop_loss_pct: float
    stop_semantics: StopSemantics
    stop_trigger: StopTrigger
    risk_per_trade: float

    # ── The three strictly-separated concepts ───────────────────
    exchange_leverage: float            # exchange account leverage (e.g., 2.0)
    actual_frac: float                  # base strategy frac of sleeve equity
    portfolio_allocation_default: float = 1.0  # sleeve-level account allocation

    use_dynamic_sizing: bool = False
    use_adaptive_hold: bool = False

    fee_per_side: float = 0.0005
    slip_per_side: float = 0.0001

    def __post_init__(self) -> None:
        if self.exchange_leverage <= 0:
            raise ValueError(
                f"exchange_leverage must be > 0, got {self.exchange_leverage}"
            )
        if self.actual_frac < 0:
            raise ValueError(
                f"actual_frac must be >= 0, got {self.actual_frac}"
            )
        if self.actual_frac > self.exchange_leverage:
            # Running frac above exchange leverage is impossible
            # without cross-margin shenanigans we don't model.
            raise ValueError(
                f"actual_frac {self.actual_frac} exceeds "
                f"exchange_leverage {self.exchange_leverage}"
            )
        if not (0 < self.portfolio_allocation_default <= 1):
            raise ValueError(
                f"portfolio_allocation_default must be in (0, 1], "
                f"got {self.portfolio_allocation_default}"
            )

    @property
    def round_trip_cost_per_frac(self) -> float:
        """Round-trip cost drag per unit of actual_frac.

        round_trip = 2 * (fee_per_side + slip_per_side)
        Per-trade equity drag = round_trip_cost_per_frac * actual_frac.
        """
        return 2.0 * (self.fee_per_side + self.slip_per_side)

    @property
    def actual_frac_min(self) -> float:
        """Minimum effective actual_frac when dynamic sizing is on.

        For fixed cells this equals `actual_frac`. For dynamic cells
        this is `actual_frac * 0.5` (the multiplier floor).
        """
        if self.use_dynamic_sizing:
            return self.actual_frac * 0.5
        return self.actual_frac

    @property
    def actual_frac_max(self) -> float:
        if self.use_dynamic_sizing:
            return self.actual_frac * 1.5
        return self.actual_frac

    @property
    def liquidation_adverse_move(self) -> float:
        """Approximate liquidation distance ignoring maintenance margin.

        For 2x isolated perpetual futures, the liquidation distance
        is ~1/leverage = 50%. A long position at entry_price P has
        isolated margin = exposure / leverage; liquidation fires
        when loss equals margin, i.e., when price has moved by
        1/leverage (regardless of actual_frac, because isolated
        margin scales with exposure).

        Maintenance margin adds a small buffer (~0.4% for BTCUSDT)
        that we conservatively ignore here — the real liquidation
        distance is slightly less.
        """
        return 1.0 / self.exchange_leverage

    @property
    def sleeve_label(self) -> str:
        """Human-readable sleeve label, e.g. "2x leveraged futures sleeve"."""
        return f"{self.exchange_leverage:g}x leveraged perpetual futures sleeve"

    @property
    def stop_config_str(self) -> str:
        """Human-readable stop config, e.g. "1.5% / strategy_close_stop"."""
        return f"{self.stop_loss_pct * 100:g}% / {self.stop_semantics}"


@dataclass(frozen=True)
class CanonicalMetrics:
    """OOS metrics for one canonical cell.

    All percentages are stored as DECIMAL FRACTIONS:
        0.1434 = +14.34% return
        0.1297 = 12.97% drawdown
        -0.0568 = -5.68% worst trade

    These numbers assume portfolio_allocation=1.0 (the strategy-level
    leveraged futures result). Reports that want to discuss a
    0.5x or 0.25x allocation must do so in a SEPARATE section and
    must NOT present the diluted numbers as the primary result.
    """
    num_trades: int
    oos_return: float              # compounded, fraction; +1.4345 = +143.45%
    max_dd: float                  # fraction; 0.1297 = 12.97%
    profit_factor: float
    worst_trade_pnl: float         # fraction; -0.0568 = -5.68%
    worst_adverse_move: float      # fraction; signal-level, not sizing-level
    positive_windows: int
    total_windows: int
    stops_fired: int

    @property
    def positive_window_ratio(self) -> float:
        if self.total_windows == 0:
            return 0.0
        return self.positive_windows / self.total_windows

    def return_pct_str(self) -> str:
        """Human-readable return as a +/-XXX.XX% string."""
        return f"{self.oos_return * 100:+.2f}%"

    def dd_pct_str(self) -> str:
        return f"{self.max_dd * 100:.2f}%"

    def worst_trade_pct_str(self) -> str:
        return f"{self.worst_trade_pnl * 100:+.2f}%"


@dataclass(frozen=True)
class LiquidationSafety:
    """Liquidation safety margin for a cell.

    liquidation_adverse_move: the approximate adverse price move
        that would trigger liquidation (= 1/exchange_leverage).
    worst_adverse_move: the worst adverse excursion observed during
        the canonical walk-forward (from CanonicalMetrics).
    buffer_pp: liquidation_adverse_move - worst_adverse_move in
        percentage points. Larger is safer.
    buffer_multiple: liquidation_adverse_move / worst_adverse_move,
        i.e., "how many times worse would the worst trade have to be
        to liquidate". Larger is safer.
    """
    liquidation_adverse_move: float
    worst_adverse_move: float
    buffer_pp: float
    buffer_multiple: float

    @property
    def is_safe(self) -> bool:
        """A cell is "safe" if its worst observed adverse move is
        at least 3x below the liquidation distance.

        This is a conservative threshold. Phase 6 tail-event stress
        used a 40% shock test which all frac ≤ 2.0 cells survived.
        A 3x buffer on the worst OBSERVED move gives extra headroom
        for tail events worse than anything in the 4-year sample.
        """
        return self.buffer_multiple >= 3.0

    def summary_str(self) -> str:
        return (
            f"liq@{self.liquidation_adverse_move * 100:.0f}% / "
            f"worst_adv={self.worst_adverse_move * 100:.2f}% / "
            f"buffer={self.buffer_multiple:.2f}x"
        )


def compute_liquidation_safety(
    config: CanonicalCellConfig,
    metrics: CanonicalMetrics,
) -> LiquidationSafety:
    liq = config.liquidation_adverse_move
    worst = metrics.worst_adverse_move
    buffer_pp = liq - worst
    mult = liq / worst if worst > 0 else float("inf")
    return LiquidationSafety(
        liquidation_adverse_move=liq,
        worst_adverse_move=worst,
        buffer_pp=buffer_pp,
        buffer_multiple=mult,
    )


@dataclass(frozen=True)
class CanonicalCell:
    cell_id: str
    description: str
    role: SleeveRole
    config: CanonicalCellConfig
    metrics: CanonicalMetrics
    measured_at: str               # ISO date YYYY-MM-DD
    source_report: str             # e.g. "strategy_c_v2_phase8_canonical_baseline.md"
    notes: str = ""

    @property
    def liquidation_safety(self) -> LiquidationSafety:
        return compute_liquidation_safety(self.config, self.metrics)


# ── shared parameters ───────────────────────────────────────────────
#
# All 6 canonical cells run on Binance USDT-M BTCUSDT perp at 2x
# exchange leverage, isolated margin mode. The cost assumptions are
# 0.05% fee per side + 0.01% slippage per side = 0.12% round-trip
# per unit of actual_frac.

_EXCHANGE_LEVERAGE_2X = 2.0
_FEE_PER_SIDE = 0.0005
_SLIP_PER_SIDE = 0.0001


_CANONICAL_MEASUREMENT_DATE = "2026-04-12"
_BASELINE_REPORT = "strategy_c_v2_phase8_canonical_baseline.md"


# ── D1_long_primary (PRIMARY) ───────────────────────────────────────


D1_LONG_PRIMARY = CanonicalCell(
    cell_id="D1_long_primary",
    description=(
        "Primary deployment sleeve. rsi_only_20 long on 4h BTCUSDT, "
        "fixed actual_frac=1.333, fixed 11-bar hold, "
        "1.5% close-trigger stop on a 2x leveraged futures sleeve."
    ),
    role="primary",
    config=CanonicalCellConfig(
        signal_family="rsi_only",
        rsi_period=20,
        side="long",
        hold_bars=11,
        stop_loss_pct=0.015,
        stop_semantics="strategy_close_stop",
        stop_trigger="close",
        risk_per_trade=0.02,
        exchange_leverage=_EXCHANGE_LEVERAGE_2X,
        actual_frac=0.02 / 0.015,    # = 1.3333...
        portfolio_allocation_default=1.0,
        use_dynamic_sizing=False,
        use_adaptive_hold=False,
        fee_per_side=_FEE_PER_SIDE,
        slip_per_side=_SLIP_PER_SIDE,
    ),
    metrics=CanonicalMetrics(
        num_trades=73,
        oos_return=1.4345,
        max_dd=0.1297,
        profit_factor=2.23,
        worst_trade_pnl=-0.0568,
        worst_adverse_move=0.0651,
        positive_windows=7,
        total_windows=8,
        stops_fired=22,
    ),
    measured_at=_CANONICAL_MEASUREMENT_DATE,
    source_report=_BASELINE_REPORT,
    notes=(
        "The canonical Phase 8 primary. Agreed by 8 independent "
        "measurements. Phase 6 final recommendation incorrectly "
        "claimed +173.06% / 9.27% DD — that number is fabricated "
        "and does not correspond to any row in the Phase 6 "
        "expanded_sweep CSV or any downstream data."
    ),
)


# ── D1_long_dynamic (SHADOW) ────────────────────────────────────────


D1_LONG_DYNAMIC = CanonicalCell(
    cell_id="D1_long_dynamic",
    description=(
        "Shadow sleeve — D1_long with dynamic sizing. "
        "actual_frac in [0.667, 2.000] via 4-component conviction multiplier."
    ),
    role="shadow",
    config=CanonicalCellConfig(
        signal_family="rsi_only",
        rsi_period=20,
        side="long",
        hold_bars=11,
        stop_loss_pct=0.015,
        stop_semantics="strategy_close_stop",
        stop_trigger="close",
        risk_per_trade=0.02,
        exchange_leverage=_EXCHANGE_LEVERAGE_2X,
        actual_frac=0.02 / 0.015,
        portfolio_allocation_default=1.0,
        use_dynamic_sizing=True,           # ← difference
        use_adaptive_hold=False,
        fee_per_side=_FEE_PER_SIDE,
        slip_per_side=_SLIP_PER_SIDE,
    ),
    metrics=CanonicalMetrics(
        num_trades=73,
        oos_return=1.6432,
        max_dd=0.1481,
        profit_factor=2.17,
        worst_trade_pnl=-0.0774,
        worst_adverse_move=0.0651,
        positive_windows=7,
        total_windows=8,
        stops_fired=22,
    ),
    measured_at=_CANONICAL_MEASUREMENT_DATE,
    source_report=_BASELINE_REPORT,
    notes=(
        "Expected delta vs D1_long_primary: +20.87 pp return, "
        "+1.84 pp DD, -2.06 pp worst trade. Trade count identical "
        "(sizing does not modify signal selection). Dynamic "
        "multiplier caps at 1.5 so actual_frac tops out at "
        "exchange_leverage=2.0 on full-conviction bars — fully "
        "margined at that point."
    ),
)


# ── D1_long_dynamic_adaptive (SHADOW) ──────────────────────────────


D1_LONG_DYNAMIC_ADAPTIVE = CanonicalCell(
    cell_id="D1_long_dynamic_adaptive",
    description=(
        "Shadow sleeve — D1_long with dynamic sizing AND adaptive hold. "
        "Max-conviction combined modifier stack."
    ),
    role="shadow",
    config=CanonicalCellConfig(
        signal_family="rsi_only",
        rsi_period=20,
        side="long",
        hold_bars=11,
        stop_loss_pct=0.015,
        stop_semantics="strategy_close_stop",
        stop_trigger="close",
        risk_per_trade=0.02,
        exchange_leverage=_EXCHANGE_LEVERAGE_2X,
        actual_frac=0.02 / 0.015,
        portfolio_allocation_default=1.0,
        use_dynamic_sizing=True,           # ← difference
        use_adaptive_hold=True,             # ← difference
        fee_per_side=_FEE_PER_SIDE,
        slip_per_side=_SLIP_PER_SIDE,
    ),
    metrics=CanonicalMetrics(
        num_trades=64,
        oos_return=2.0455,
        max_dd=0.1636,
        profit_factor=2.35,
        worst_trade_pnl=-0.0774,
        worst_adverse_move=0.0651,
        positive_windows=6,
        total_windows=8,
        stops_fired=24,
    ),
    measured_at=_CANONICAL_MEASUREMENT_DATE,
    source_report=_BASELINE_REPORT,
    notes=(
        "Expected delta vs D1_long_primary: +61.10 pp return, "
        "+3.39 pp DD, -2.06 pp worst trade. Trade count drops to "
        "64 because adaptive hold cuts low-score trades short, "
        "which collapses some follow-on signals."
    ),
)


# ── D1_long_frac2_shadow (SHADOW, new) ─────────────────────────────


D1_LONG_FRAC2_SHADOW = CanonicalCell(
    cell_id="D1_long_frac2_shadow",
    description=(
        "High-return shadow sleeve — D1_long at fixed actual_frac=2.000 "
        "(the exchange leverage cap). Fully margined on every trade. "
        "Same signal/hold/stop as D1_long_primary, just more exposure."
    ),
    role="shadow",
    config=CanonicalCellConfig(
        signal_family="rsi_only",
        rsi_period=20,
        side="long",
        hold_bars=11,
        stop_loss_pct=0.015,
        stop_semantics="strategy_close_stop",
        stop_trigger="close",
        risk_per_trade=0.02,
        exchange_leverage=_EXCHANGE_LEVERAGE_2X,
        actual_frac=2.0,                   # ← difference: max frac on 2x
        portfolio_allocation_default=1.0,
        use_dynamic_sizing=False,
        use_adaptive_hold=False,
        fee_per_side=_FEE_PER_SIDE,
        slip_per_side=_SLIP_PER_SIDE,
    ),
    metrics=CanonicalMetrics(
        num_trades=73,
        oos_return=2.5913,                 # +259.13% canonical fresh run
        max_dd=0.1909,                      # 19.09%
        profit_factor=2.23,                 # same as primary (PF is ratio-invariant to frac)
        worst_trade_pnl=-0.0851,            # 1.5x primary's -5.68% (2.0/1.333 ≈ 1.5)
        worst_adverse_move=0.0651,          # same signal stream
        positive_windows=7,
        total_windows=8,
        stops_fired=22,
    ),
    measured_at=_CANONICAL_MEASUREMENT_DATE,
    source_report=_BASELINE_REPORT,
    notes=(
        "Canonical walk-forward 2026-04-12: fixed frac=2.0 on the "
        "D1_long signal stream produces +259.13% / DD 19.09% / "
        "worst trade -8.51% on 73 trades. PF stays at 2.23 because "
        "it's a win/loss ratio and scales trivially with frac. "
        "Expected delta vs D1_long_primary: +115.68 pp return, "
        "+6.12 pp DD, -2.83 pp worst trade. Worst observed adverse "
        "move is 6.51% — liquidation distance on 2x isolated is "
        "~50%, so the cell has a ~7.7x liquidation buffer. Phase 6 "
        "tail-event stress validated frac ≤ 2.0 as the hard ceiling "
        "(40% shock → -80% equity, no liquidation)."
    ),
)


# ── C_long_backup (BACKUP) ──────────────────────────────────────────


C_LONG_BACKUP = CanonicalCell(
    cell_id="C_long_backup",
    description=(
        "Backup deployment sleeve. rsi_and_macd_14 long on 4h BTCUSDT, "
        "fixed actual_frac=1.000, fixed 4-bar hold, 2% close-trigger "
        "stop on a 2x leveraged futures sleeve."
    ),
    role="backup",
    config=CanonicalCellConfig(
        signal_family="rsi_and_macd",
        rsi_period=14,
        side="long",
        hold_bars=4,
        stop_loss_pct=0.02,
        stop_semantics="strategy_close_stop",
        stop_trigger="close",
        risk_per_trade=0.02,
        exchange_leverage=_EXCHANGE_LEVERAGE_2X,
        actual_frac=1.0,                    # 0.02 / 0.02 = 1.0
        portfolio_allocation_default=1.0,
        use_dynamic_sizing=False,
        use_adaptive_hold=False,
        fee_per_side=_FEE_PER_SIDE,
        slip_per_side=_SLIP_PER_SIDE,
    ),
    metrics=CanonicalMetrics(
        num_trades=178,
        oos_return=1.0626,
        max_dd=0.1810,
        profit_factor=1.70,
        worst_trade_pnl=-0.0662,
        worst_adverse_move=0.0736,
        positive_windows=6,
        total_windows=8,
        stops_fired=17,
    ),
    measured_at=_CANONICAL_MEASUREMENT_DATE,
    source_report=_BASELINE_REPORT,
)


# ── C_long_dynamic (SHADOW) ────────────────────────────────────────


C_LONG_DYNAMIC = CanonicalCell(
    cell_id="C_long_dynamic",
    description=(
        "Shadow sleeve — C_long with dynamic sizing. "
        "actual_frac in [0.500, 1.500]. Not paired with adaptive "
        "hold (hurts by -58 pp per manual_edge study)."
    ),
    role="shadow",
    config=CanonicalCellConfig(
        signal_family="rsi_and_macd",
        rsi_period=14,
        side="long",
        hold_bars=4,
        stop_loss_pct=0.02,
        stop_semantics="strategy_close_stop",
        stop_trigger="close",
        risk_per_trade=0.02,
        exchange_leverage=_EXCHANGE_LEVERAGE_2X,
        actual_frac=1.0,
        portfolio_allocation_default=1.0,
        use_dynamic_sizing=True,            # ← difference
        use_adaptive_hold=False,
        fee_per_side=_FEE_PER_SIDE,
        slip_per_side=_SLIP_PER_SIDE,
    ),
    metrics=CanonicalMetrics(
        num_trades=178,
        oos_return=1.3597,
        max_dd=0.1708,
        profit_factor=1.79,
        worst_trade_pnl=-0.0723,
        worst_adverse_move=0.0736,
        positive_windows=6,
        total_windows=8,
        stops_fired=17,
    ),
    measured_at=_CANONICAL_MEASUREMENT_DATE,
    source_report=_BASELINE_REPORT,
    notes=(
        "Expected delta vs C_long_backup: +29.71 pp return, "
        "-1.02 pp DD (DD actually improves), -0.61 pp worst trade. "
        "Trade count identical. Only canonical cell where dynamic "
        "sizing improves DD."
    ),
)


# ── registry ────────────────────────────────────────────────────────


CANONICAL_CELLS: dict[str, CanonicalCell] = {
    D1_LONG_PRIMARY.cell_id: D1_LONG_PRIMARY,
    D1_LONG_DYNAMIC.cell_id: D1_LONG_DYNAMIC,
    D1_LONG_DYNAMIC_ADAPTIVE.cell_id: D1_LONG_DYNAMIC_ADAPTIVE,
    D1_LONG_FRAC2_SHADOW.cell_id: D1_LONG_FRAC2_SHADOW,
    C_LONG_BACKUP.cell_id: C_LONG_BACKUP,
    C_LONG_DYNAMIC.cell_id: C_LONG_DYNAMIC,
}


def get_canonical_cell(cell_id: str) -> CanonicalCell:
    """Look up a canonical cell by id.

    Raises KeyError with a helpful message if the id is unknown.
    """
    if cell_id not in CANONICAL_CELLS:
        raise KeyError(
            f"Unknown canonical cell: {cell_id!r}. "
            f"Known cells: {sorted(CANONICAL_CELLS.keys())}"
        )
    return CANONICAL_CELLS[cell_id]


def list_canonical_cell_ids() -> list[str]:
    return sorted(CANONICAL_CELLS.keys())


def get_primary_cell() -> CanonicalCell:
    return D1_LONG_PRIMARY


def get_backup_cell() -> CanonicalCell:
    return C_LONG_BACKUP


def list_shadow_cells() -> list[CanonicalCell]:
    """Return the shadow cells (dynamic / dynamic+adaptive / frac2 variants)."""
    return [
        D1_LONG_DYNAMIC,
        D1_LONG_DYNAMIC_ADAPTIVE,
        D1_LONG_FRAC2_SHADOW,
        C_LONG_DYNAMIC,
    ]


def list_cells_by_role(role: SleeveRole) -> list[CanonicalCell]:
    return [c for c in CANONICAL_CELLS.values() if c.role == role]


def compute_expected_delta(
    cell_id: str,
    baseline_id: str | None = None,
) -> dict[str, float]:
    """Compute the expected performance delta vs a baseline cell.

    Strategy-level delta only (assumes portfolio_allocation=1.0 for
    both cells). For allocation-adjusted deltas, scale outside this
    function — keep the three concepts separate.

    Args:
        cell_id: the shadow / modified cell to compare.
        baseline_id: the fixed baseline to compare against. If None,
            auto-picks D1_long_primary for D1_long_* cells and
            C_long_backup for C_long_* cells.

    Returns:
        dict with keys: delta_return, delta_dd, delta_worst_trade,
        delta_profit_factor, delta_num_trades. All in the same units
        as the metrics (fractions, not percentage points).
    """
    cell = get_canonical_cell(cell_id)
    if baseline_id is None:
        if cell_id.startswith("D1_long"):
            baseline_id = D1_LONG_PRIMARY.cell_id
        elif cell_id.startswith("C_long"):
            baseline_id = C_LONG_BACKUP.cell_id
        else:
            raise ValueError(
                f"Cannot auto-pick baseline for {cell_id!r}; "
                f"pass baseline_id explicitly."
            )
    baseline = get_canonical_cell(baseline_id)
    return {
        "delta_return": cell.metrics.oos_return - baseline.metrics.oos_return,
        "delta_dd": cell.metrics.max_dd - baseline.metrics.max_dd,
        "delta_worst_trade": (
            cell.metrics.worst_trade_pnl - baseline.metrics.worst_trade_pnl
        ),
        "delta_profit_factor": (
            cell.metrics.profit_factor - baseline.metrics.profit_factor
        ),
        "delta_num_trades": float(
            cell.metrics.num_trades - baseline.metrics.num_trades
        ),
    }


# ── portfolio allocation layer (kept strictly separate) ────────────


def apply_portfolio_allocation(
    metrics: CanonicalMetrics,
    allocation: float,
) -> dict[str, float]:
    """Scale canonical metrics by a portfolio allocation layer.

    This is the ONLY place where the allocation layer is applied,
    and it produces a NEW dict with explicitly allocation-scaled
    fields — so there's no risk of confusing allocation-scaled
    numbers with the strategy-level canonical metrics.

    Args:
        metrics: CanonicalMetrics at portfolio_allocation=1.0.
        allocation: sleeve allocation in (0, 1]. 1.0 = full sleeve,
            0.5 = half, etc.

    Returns:
        dict with scaled return/DD/worst_trade (the scalable metrics).
        num_trades, profit_factor, positive_windows, stops_fired,
        worst_adverse_move are NOT scaled — they are properties of
        the strategy, not of the allocation.

    Note:
        Allocation-scaled return is only approximately linear because
        compounded returns don't scale trivially. For small trades
        this is a decent approximation; for larger moves the
        non-linearity matters. This function uses the linear-first-
        order scaling for simplicity — reports using these numbers
        should note they're approximate.
    """
    if not (0 < allocation <= 1):
        raise ValueError(
            f"allocation must be in (0, 1], got {allocation}"
        )
    return {
        "allocation": allocation,
        "scaled_oos_return_approx": metrics.oos_return * allocation,
        "scaled_max_dd_approx": metrics.max_dd * allocation,
        "scaled_worst_trade_pnl_approx": metrics.worst_trade_pnl * allocation,
        # Not scalable — these are strategy-level properties
        "num_trades": metrics.num_trades,
        "profit_factor": metrics.profit_factor,
        "worst_adverse_move": metrics.worst_adverse_move,
        "positive_windows": metrics.positive_windows,
        "stops_fired": metrics.stops_fired,
    }
