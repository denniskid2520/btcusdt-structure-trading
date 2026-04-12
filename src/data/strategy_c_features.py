"""Feature engineering for Strategy C (Baseline A + Baseline B precision study).

Per-bar primitives:
    taker_delta_norm   = taker_delta / (buy + sell), in [-1, 1]
    cvd_delta          = cvd[i] - cvd[i-1]
    basis_change       = basis[i] - basis[i-1]
    fr_spread          = funding - funding_oi_weighted
    agg_u_oi_pct       = stablecoin_oi pct-change
    liq_imbalance      = (short_liq - long_liq) / total  (pass-through from raw bar)

Rolling z-scores (uses rolling_zscore helper):
    Z32 (8h window):
        taker_delta_norm_z32
        oi_pct_change_z32
        cvd_delta_z32
        long_liq_z32
        short_liq_z32
        basis_change_z32
        agg_u_oi_pct_z32
    Z96 (24h window):
        basis_z96         (kept for Baseline A back-compat)
        fr_close_z96      (z-score of the close funding rate)
        fr_spread_z96     (z-score of the exchange-local funding spread)

Warmup rule: widest window is 96 bars, so rows before bar 95 are dropped unless
`warmup=False` is passed. For the narrower z32 features this means the first 31
bars are also None, but those rows are always dropped anyway.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from data.strategy_c_dataset import StrategyCBar


Z32_WINDOW = 32  # 8 hours at 15m
Z96_WINDOW = 96  # 24 hours at 15m


@dataclass(frozen=True)
class StrategyCFeatureBar:
    """Feature bar carrying everything Baseline A and Baseline B need.

    All z-score fields are Optional because they're undefined during warmup.
    cvd_delta / basis_change / agg_u_oi_pct need a prior bar so bar[0] is None.
    """

    timestamp: datetime
    open: float
    close: float

    # ── per-bar primitives ────────────────────────────────────────────
    taker_delta_norm: float                     # [-1, 1], always defined
    cvd_delta: float | None                     # diff, None at bar 0
    basis_change: float | None                  # diff, None at bar 0
    fr_spread: float                            # always defined
    agg_u_oi_pct: float | None                  # pct-change, None at bar 0
    liq_imbalance: float                        # pass-through

    # ── Baseline A z-scores (kept for back-compat) ────────────────────
    taker_delta_norm_z32: float | None
    oi_pct_change_z32: float | None
    basis_z96: float | None
    fr_close_z96: float | None                  # renamed from funding_z96

    # ── Baseline B new z-scores ───────────────────────────────────────
    cvd_delta_z32: float | None
    long_liq_z32: float | None
    short_liq_z32: float | None
    basis_change_z32: float | None
    fr_spread_z96: float | None
    agg_u_oi_pct_z32: float | None


def rolling_zscore(values: Sequence[float | None], window: int) -> list[float | None]:
    """Rolling z-score over `values` with a trailing `window` of samples.

    z[i] = (values[i] - mean(window)) / std(window)

    Returns a list of the same length. None inputs (e.g., the first `cvd_delta`
    bar which is None) cause every window overlapping that index to also be
    None. This keeps the semantics clean without contaminating the series.
    If the rolling std is zero, emit 0.0 instead of NaN.
    """
    n = len(values)
    out: list[float | None] = [None] * n
    if window <= 0 or n < window:
        return out

    for i in range(window - 1, n):
        w = values[i - window + 1 : i + 1]
        if any(v is None for v in w):
            continue
        w_clean: list[float] = [v for v in w if v is not None]  # type: ignore[misc]
        mean = sum(w_clean) / window
        var = sum((v - mean) ** 2 for v in w_clean) / window
        if var == 0.0:
            out[i] = 0.0
        else:
            out[i] = (values[i] - mean) / (var ** 0.5)  # type: ignore[operator]

    return out


def _taker_delta_norm(buy: float, sell: float, delta: float) -> float:
    """delta / (buy + sell); 0.0 when both are zero (no trades)."""
    total = buy + sell
    if total <= 0:
        return 0.0
    return delta / total


def _diff(series: Sequence[float]) -> list[float | None]:
    """First difference; out[0] = None."""
    if not series:
        return []
    out: list[float | None] = [None]
    for i in range(1, len(series)):
        out.append(series[i] - series[i - 1])
    return out


def _pct_change(series: Sequence[float]) -> list[float | None]:
    """Percent change; out[0] = None. Returns 0.0 when prev is zero."""
    if not series:
        return []
    out: list[float | None] = [None]
    for i in range(1, len(series)):
        prev = series[i - 1]
        if prev == 0:
            out.append(0.0)
        else:
            out.append((series[i] - prev) / prev)
    return out


def compute_features(
    bars: Sequence[StrategyCBar],
    *,
    warmup: bool = True,
) -> list[StrategyCFeatureBar]:
    """Compute all Strategy C features in one pass.

    Args:
        bars: Aligned StrategyCBar sequence (timestamps strictly ascending).
        warmup: If True (default), drop rows whose z_96 window isn't full
            yet (first 95 bars). If False, keep all rows with None z-scores.

    Returns:
        List of StrategyCFeatureBar sorted by timestamp.
    """
    if not bars:
        return []

    # ── primitives ────────────────────────────────────────────────────
    taker_norm = [_taker_delta_norm(b.taker_buy_usd, b.taker_sell_usd, b.taker_delta_usd) for b in bars]
    cvd_delta = _diff([b.cvd for b in bars])
    basis_change = _diff([b.basis for b in bars])
    fr_spread = [b.funding - b.funding_oi_weighted for b in bars]
    agg_u_oi_pct = _pct_change([b.stablecoin_oi for b in bars])

    # ── z-scores ──────────────────────────────────────────────────────
    taker_norm_z32 = rolling_zscore(taker_norm, Z32_WINDOW)
    oi_pct_z32 = rolling_zscore([b.oi_pct_change for b in bars], Z32_WINDOW)
    cvd_delta_z32 = rolling_zscore(cvd_delta, Z32_WINDOW)
    long_liq_z32 = rolling_zscore([b.long_liq_usd for b in bars], Z32_WINDOW)
    short_liq_z32 = rolling_zscore([b.short_liq_usd for b in bars], Z32_WINDOW)
    basis_change_z32 = rolling_zscore(basis_change, Z32_WINDOW)
    agg_u_oi_pct_z32 = rolling_zscore(agg_u_oi_pct, Z32_WINDOW)

    basis_z96 = rolling_zscore([b.basis for b in bars], Z96_WINDOW)
    fr_close_z96 = rolling_zscore([b.funding for b in bars], Z96_WINDOW)
    fr_spread_z96 = rolling_zscore(fr_spread, Z96_WINDOW)

    # ── assemble ──────────────────────────────────────────────────────
    feats: list[StrategyCFeatureBar] = []
    for i, b in enumerate(bars):
        feats.append(
            StrategyCFeatureBar(
                timestamp=b.timestamp,
                open=b.open,
                close=b.close,
                taker_delta_norm=taker_norm[i],
                cvd_delta=cvd_delta[i],
                basis_change=basis_change[i],
                fr_spread=fr_spread[i],
                agg_u_oi_pct=agg_u_oi_pct[i],
                liq_imbalance=b.liq_imbalance,
                taker_delta_norm_z32=taker_norm_z32[i],
                oi_pct_change_z32=oi_pct_z32[i],
                basis_z96=basis_z96[i],
                fr_close_z96=fr_close_z96[i],
                cvd_delta_z32=cvd_delta_z32[i],
                long_liq_z32=long_liq_z32[i],
                short_liq_z32=short_liq_z32[i],
                basis_change_z32=basis_change_z32[i],
                fr_spread_z96=fr_spread_z96[i],
                agg_u_oi_pct_z32=agg_u_oi_pct_z32[i],
            )
        )

    if warmup:
        # Widest window is 96 → keep only rows where every z_96 is computable.
        return [
            f for f in feats
            if f.basis_z96 is not None
            and f.fr_close_z96 is not None
            and f.fr_spread_z96 is not None
        ]
    return feats
