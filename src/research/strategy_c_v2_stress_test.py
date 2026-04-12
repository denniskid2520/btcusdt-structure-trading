"""Strategy C v2 Phase 8 stress test suite.

Evaluates a completed backtest against:

1. **Historical OOS liquidation check** — did any open trade ever touch
   a price that would have liquidated the isolated position?
2. **Synthetic gap/shock stress** — if an adverse shock of a given
   magnitude happens during the worst-case open trade, does the
   position liquidate? Apply ON TOP of the observed worst adverse
   move to find the tail-survival limit.
3. **Slippage stress** — re-compute net return assuming catastrophe
   stop fills slip by a given amount. This tests operational
   fragility.

The module is intentionally pure: it takes a backtest result and
config, returns structured verdicts. No I/O, no walk-forward replay.

Usage:

    result = run_v2_backtest(...)
    verdict = run_stress_suite(
        trades=result.trades,
        worst_adverse_move=max_trade_adverse_move,
        cell_config=StressConfig(
            exchange_leverage=3.0,
            max_actual_frac=3.0,
        ),
        shock_levels=(0.10, 0.15, 0.20, 0.30, 0.40),
        slippage_levels=(0.001, 0.003, 0.005, 0.010),
    )

The returned `StressVerdict` has:
    - per-shock liquidation results
    - per-slippage net-return impact
    - overall pass/fail of Phase 8 shortlist filter
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Sequence


ShockVerdict = Literal["survives", "survives_tight", "liquidates"]


@dataclass(frozen=True)
class StressConfig:
    """Leverage + sizing config needed to evaluate stress results."""
    exchange_leverage: float    # e.g., 3.0, 5.0
    max_actual_frac: float       # the cap (may equal exchange_leverage)
    starting_equity_usd: float = 10_000.0

    @property
    def liquidation_adverse_move(self) -> float:
        """Approximate liquidation distance (ignoring maintenance margin).

        For isolated mode: liq at price move = 1/leverage.
        A 40% adverse move on 3x isolated (liq at 33.3%) → liquidated.
        """
        return 1.0 / self.exchange_leverage


@dataclass(frozen=True)
class ShockResult:
    shock_pct: float                  # the synthetic shock fraction (0.10 = 10%)
    liq_touched: bool                 # did shock touch the liquidation price?
    combined_adverse: float           # worst historical adverse + shock
    equity_loss_pct: float            # fraction of equity lost at shock + historical
    verdict: ShockVerdict
    tight_threshold: float = 0.05     # "tight" = within 5pp of liq distance


@dataclass(frozen=True)
class SlippageResult:
    slip_pct: float                   # 0.001 = 0.1%
    extra_drag_on_stops: float        # additional loss fraction on stop fills only
    adjusted_return_pct: float        # approximate total OOS return after slippage
    return_delta_pp: float            # pp change vs unslipped
    operationally_acceptable: bool    # True if not collapsed by slippage


@dataclass(frozen=True)
class StressVerdict:
    config: StressConfig
    historical_max_adverse: float     # worst OOS adverse excursion seen in trades
    historical_liquidated: bool       # True if historical adverse >= liq distance
    shock_results: tuple[ShockResult, ...]
    slippage_results: tuple[SlippageResult, ...]
    shortlist_pass: bool              # True if passes Phase 8 hard filters
    shortlist_reason: str             # why it passed or what failed


def classify_shock(
    shock_pct: float,
    historical_max_adverse: float,
    config: StressConfig,
    tight_threshold: float = 0.05,
) -> ShockResult:
    """Classify a single shock level against the config.

    The shock is assumed to hit on top of the observed worst adverse
    excursion (conservative — assumes worst trade gets stacked with
    a tail event).
    """
    combined = historical_max_adverse + shock_pct
    liq_distance = config.liquidation_adverse_move
    liq_touched = combined >= liq_distance
    equity_loss = combined * config.max_actual_frac

    if liq_touched:
        verdict: ShockVerdict = "liquidates"
    elif liq_distance - combined < tight_threshold:
        verdict = "survives_tight"
    else:
        verdict = "survives"

    return ShockResult(
        shock_pct=shock_pct,
        liq_touched=liq_touched,
        combined_adverse=combined,
        equity_loss_pct=equity_loss,
        verdict=verdict,
        tight_threshold=tight_threshold,
    )


def estimate_slippage_impact(
    slip_pct: float,
    num_stop_exits: int,
    avg_actual_frac: float,
    num_trades: int,
    baseline_return_pct: float,
    collapse_threshold_pp: float = -50.0,
) -> SlippageResult:
    """Estimate slippage impact on OOS compounded return.

    Model:
        Each stop-type exit takes an additional `slip_pct * avg_frac`
        loss. This compounds across `num_stop_exits` trades.

        approximate_drag = num_stop_exits * slip_pct * avg_frac
        adjusted_return = (1 + baseline) * (1 - drag) - 1

    This is a first-order approximation. The true compounded effect
    depends on when the slipped trades occur in the sequence.

    `operationally_acceptable` is True iff the return degradation is
    less severe than `collapse_threshold_pp`. A cell that goes from
    +250% to +100% at 1% slippage is still acceptable; a cell that
    goes from +250% to -50% is not.
    """
    if num_stop_exits == 0 or num_trades == 0:
        return SlippageResult(
            slip_pct=slip_pct,
            extra_drag_on_stops=0.0,
            adjusted_return_pct=baseline_return_pct,
            return_delta_pp=0.0,
            operationally_acceptable=True,
        )
    drag_per_trade = slip_pct * avg_actual_frac
    total_drag_linear = num_stop_exits * drag_per_trade
    # Compound the drag into the baseline return
    baseline_mult = 1.0 + baseline_return_pct / 100.0
    adjusted_mult = baseline_mult * (1.0 - total_drag_linear)
    adjusted_return_pct = (adjusted_mult - 1.0) * 100.0
    delta_pp = adjusted_return_pct - baseline_return_pct
    operationally_acceptable = delta_pp >= collapse_threshold_pp
    return SlippageResult(
        slip_pct=slip_pct,
        extra_drag_on_stops=total_drag_linear,
        adjusted_return_pct=adjusted_return_pct,
        return_delta_pp=delta_pp,
        operationally_acceptable=operationally_acceptable,
    )


def run_stress_suite(
    *,
    config: StressConfig,
    historical_max_adverse: float,
    num_trades: int,
    num_stop_exits: int,
    avg_actual_frac: float,
    baseline_return_pct: float,
    profit_factor: float,
    win_rate: float,
    shock_levels: Sequence[float] = (0.10, 0.15, 0.20, 0.30, 0.40),
    slippage_levels: Sequence[float] = (0.001, 0.003, 0.005, 0.010),
    min_trade_count: int = 100,
    min_profit_factor: float = 2.0,
    min_win_rate: float = 0.55,
    critical_slippage_level: float = 0.010,
) -> StressVerdict:
    """Run the full Phase 8 stress suite on a completed cell.

    Applies all five filters from the Phase 8 brief:
        1. trade count >= min_trade_count
        2. profit factor >= min_profit_factor
        3. win rate >= min_win_rate
        4. no historical OOS liquidation
        5. critical slippage level does not collapse return
    """
    historical_liquidated = (
        historical_max_adverse >= config.liquidation_adverse_move
    )

    shock_results = tuple(
        classify_shock(s, historical_max_adverse, config)
        for s in shock_levels
    )
    slippage_results = tuple(
        estimate_slippage_impact(
            slip_pct=s,
            num_stop_exits=num_stop_exits,
            avg_actual_frac=avg_actual_frac,
            num_trades=num_trades,
            baseline_return_pct=baseline_return_pct,
        )
        for s in slippage_levels
    )

    # Build the pass/fail verdict + reason
    reasons: list[str] = []
    if num_trades < min_trade_count:
        reasons.append(
            f"trade count {num_trades} < {min_trade_count}"
        )
    if profit_factor < min_profit_factor:
        reasons.append(
            f"profit factor {profit_factor:.2f} < {min_profit_factor}"
        )
    if win_rate < min_win_rate:
        reasons.append(
            f"win rate {win_rate:.2%} < {min_win_rate:.0%}"
        )
    if historical_liquidated:
        reasons.append(
            f"historical adverse {historical_max_adverse:.2%} >= "
            f"liq distance {config.liquidation_adverse_move:.2%}"
        )
    critical_slip = next(
        (sr for sr in slippage_results if sr.slip_pct == critical_slippage_level),
        None,
    )
    if critical_slip is not None and not critical_slip.operationally_acceptable:
        reasons.append(
            f"{critical_slippage_level:.1%} slippage collapses return "
            f"by {critical_slip.return_delta_pp:.1f}pp"
        )

    shortlist_pass = len(reasons) == 0
    reason_str = "; ".join(reasons) if reasons else "all filters pass"

    return StressVerdict(
        config=config,
        historical_max_adverse=historical_max_adverse,
        historical_liquidated=historical_liquidated,
        shock_results=shock_results,
        slippage_results=slippage_results,
        shortlist_pass=shortlist_pass,
        shortlist_reason=reason_str,
    )


def format_verdict(verdict: StressVerdict) -> str:
    """Human-readable multi-line summary of a stress verdict."""
    lines = []
    lines.append(
        f"Stress Verdict @ {verdict.config.exchange_leverage:g}x leverage, "
        f"max_frac={verdict.config.max_actual_frac:g}"
    )
    lines.append(
        f"  Historical: worst adverse {verdict.historical_max_adverse:.2%} "
        f"vs liq distance {verdict.config.liquidation_adverse_move:.2%} "
        f"→ {'LIQUIDATED' if verdict.historical_liquidated else 'survived'}"
    )
    lines.append("  Shock stress:")
    for sr in verdict.shock_results:
        lines.append(
            f"    shock={sr.shock_pct * 100:>5.1f}% → "
            f"combined_adverse={sr.combined_adverse * 100:>6.2f}% → "
            f"equity_loss={sr.equity_loss_pct * 100:>6.2f}% → "
            f"{sr.verdict}"
        )
    lines.append("  Slippage stress:")
    for sr in verdict.slippage_results:
        lines.append(
            f"    slip={sr.slip_pct * 100:>4.2f}% → "
            f"adjusted_return={sr.adjusted_return_pct:>+7.2f}% "
            f"(Δ {sr.return_delta_pp:>+6.1f}pp) → "
            f"{'OK' if sr.operationally_acceptable else 'COLLAPSE'}"
        )
    lines.append(
        f"  SHORTLIST: {'PASS' if verdict.shortlist_pass else 'FAIL'} "
        f"— {verdict.shortlist_reason}"
    )
    return "\n".join(lines)
