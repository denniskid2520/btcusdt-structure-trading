"""Strategy C v2 feature module — Track A implementation.

Family A (price/technical from OHLCV) and Family B (funding rate from
Binance fundingRate history). Family C (Coinglass supplemental) is Track
B only and lives in a separate module added in Phase 4.

Design notes
============

- **Causal by construction.** Every feature at bar `i` depends only on
  `bars[0 : i + 1]`. This property is verified by
  `test_compute_features_v2_is_causal_prefix_stable`.

- **Warmup is None.** A feature that cannot be computed at bar `i` (due
  to insufficient history or dependency warmup) is explicitly None,
  never a silent 0. Strategies and the score model are expected to
  skip None values.

- **Vectorised per feature, linear overall.** Each indicator function
  does one pass over the close series and produces a list of the same
  length. Overall cost: O(n * n_features) for ~200k bars × ~30 features
  ≈ 6M floating-point ops, well under a second.

- **Timeframe-aware.** `bar_hours` lets the module compute rv_1h /
  rv_4h / rv_1d / rv_7d correctly regardless of whether the bars are
  15m, 1h, or 4h. Features that are degenerate on a given frame (e.g.
  stoch_k_200 on a series shorter than 200 bars) stay None.

- **Funding is optional.** If `funding_records=None`, all funding
  fields stay None. This keeps the function usable for pure
  price-based feature work without the 5-year funding CSV.

See `strategy_c_v2_plan.md` §5–§6 and `strategy_c_v2_feature_matrix.md`
for the full list of features and which strategies read them.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from adapters.base import MarketBar
from adapters.binance_futures import FundingRateRecord
from indicators.atr import atr
from indicators.bollinger import bollinger_bands
from indicators.stochastic import stochastic


# ── dataclass (identical shape to Phase 1 scaffold) ─────────────────


@dataclass(frozen=True)
class StrategyCV2Features:
    """One bar's Strategy C v2 Family A + Family B feature vector.

    Every float field is `float | None` — None during warmup. Calendar
    fields are always populated.
    """
    # anchor
    timestamp: datetime
    close: float

    # Family A — returns
    ret_1: float | None
    ret_4: float | None
    ret_8: float | None
    ret_16: float | None
    ret_32: float | None

    # Family A — realized volatility
    rv_1h: float | None
    rv_4h: float | None
    rv_1d: float | None
    rv_7d: float | None

    # Family A — momentum
    mom_30: float | None

    # Family A — RSI
    rsi_14: float | None
    rsi_30: float | None

    # Family A — MACD (12,26,9)
    macd: float | None
    macd_signal: float | None
    macd_hist: float | None

    # Family A — stochastic (Full, smooth_k=3, smooth_d=3)
    stoch_k_30: float | None
    stoch_d_30: float | None
    stoch_k_200: float | None
    stoch_d_200: float | None

    # Family A — moving averages
    sma_20: float | None
    sma_50: float | None
    sma_200: float | None
    ema_20: float | None
    ema_50: float | None
    ema_200: float | None

    # Family A — Bollinger (20, 2.0)
    bb_mid_20: float | None
    bb_upper_20: float | None
    bb_lower_20: float | None
    bb_width_20: float | None
    bb_pctb_20: float | None

    # Family A — ATR
    atr_14: float | None
    atr_30: float | None

    # Family A — calendar (always populated)
    hour_of_day: int
    day_of_week: int
    is_weekend: bool

    # Family B — Binance perp structure
    funding_rate: float | None
    bars_to_next_funding: int | None
    funding_cum_24h: float | None
    basis_perp_vs_spot: float | None


# ── main entry point ────────────────────────────────────────────────


def compute_features_v2(
    bars: Sequence[MarketBar],
    funding_records: Sequence[FundingRateRecord] | None = None,
    spot_bars: Sequence[MarketBar] | None = None,
    *,
    bar_hours: float = 0.25,
) -> list[StrategyCV2Features]:
    """Compute Track A features for an execution-frame bar stream.

    Args:
        bars: Chronological MarketBar stream.
        funding_records: Optional Binance fundingRate history. If None,
            funding_rate / bars_to_next_funding / funding_cum_24h stay None.
        spot_bars: Reserved for basis_perp_vs_spot. If None, that field
            stays None. (Phase 2 ships without spot fetching.)
        bar_hours: Length of each bar in hours. 0.25 = 15m, 1.0 = 1h,
            4.0 = 4h. Used to convert rv_1h/4h/1d/7d into bar counts.

    Returns:
        A list of StrategyCV2Features, one per input bar.
    """
    n = len(bars)
    if n == 0:
        return []

    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]

    # Returns (simple, per-bar)
    ret_1 = _return_series(closes, 1)
    ret_4 = _return_series(closes, 4)
    ret_8 = _return_series(closes, 8)
    ret_16 = _return_series(closes, 16)
    ret_32 = _return_series(closes, 32)

    # Realized volatility windows (in bar counts)
    rv_win_1h = max(2, round(1.0 / bar_hours))
    rv_win_4h = max(2, round(4.0 / bar_hours))
    rv_win_1d = max(2, round(24.0 / bar_hours))
    rv_win_7d = max(2, round(168.0 / bar_hours))
    log_rets = _log_return_series(closes)
    rv_1h = _rolling_pop_std(log_rets, rv_win_1h)
    rv_4h = _rolling_pop_std(log_rets, rv_win_4h)
    rv_1d = _rolling_pop_std(log_rets, rv_win_1d)
    rv_7d = _rolling_pop_std(log_rets, rv_win_7d)

    # Momentum
    mom_30 = _momentum_series(closes, 30)

    # RSI (Wilder)
    rsi_14 = _rsi_series(closes, 14)
    rsi_30 = _rsi_series(closes, 30)

    # MACD
    macd_line, macd_signal_line, macd_hist = _macd_series(closes, 12, 26, 9)

    # SMA / EMA
    sma_20 = _sma_series(closes, 20)
    sma_50 = _sma_series(closes, 50)
    sma_200 = _sma_series(closes, 200)
    ema_20 = _ema_series(closes, 20)
    ema_50 = _ema_series(closes, 50)
    ema_200 = _ema_series(closes, 200)

    # Bollinger (20, 2.0)
    bb = bollinger_bands(closes, period=20, k=2.0)

    # Stochastic (30 and 200)
    stoch_30 = stochastic(highs, lows, closes, k_period=30, smooth_k=3, smooth_d=3)
    stoch_200 = stochastic(highs, lows, closes, k_period=200, smooth_k=3, smooth_d=3)

    # ATR (14, 30)
    atr_14 = atr(highs, lows, closes, period=14)
    atr_30 = atr(highs, lows, closes, period=30)

    # Funding alignment
    if funding_records:
        funding_rate_series, bars_to_next_series, funding_cum_24h_series = _align_funding(
            bars, funding_records
        )
    else:
        funding_rate_series = [None] * n
        bars_to_next_series = [None] * n
        funding_cum_24h_series = [None] * n

    # Basis perp vs spot — Phase 2 ships without spot fetching.
    basis_series = [None] * n  # placeholder, wire when spot CSV is fetched

    # Assemble
    out: list[StrategyCV2Features] = []
    for i, bar in enumerate(bars):
        ts = bar.timestamp
        bb_i = bb[i]
        st30 = stoch_30[i]
        st200 = stoch_200[i]
        out.append(
            StrategyCV2Features(
                timestamp=ts,
                close=bar.close,
                # returns
                ret_1=ret_1[i],
                ret_4=ret_4[i],
                ret_8=ret_8[i],
                ret_16=ret_16[i],
                ret_32=ret_32[i],
                # realized vol
                rv_1h=rv_1h[i],
                rv_4h=rv_4h[i],
                rv_1d=rv_1d[i],
                rv_7d=rv_7d[i],
                # momentum
                mom_30=mom_30[i],
                # rsi
                rsi_14=rsi_14[i],
                rsi_30=rsi_30[i],
                # macd
                macd=macd_line[i],
                macd_signal=macd_signal_line[i],
                macd_hist=macd_hist[i],
                # stoch
                stoch_k_30=st30.k if st30 is not None else None,
                stoch_d_30=st30.d if st30 is not None else None,
                stoch_k_200=st200.k if st200 is not None else None,
                stoch_d_200=st200.d if st200 is not None else None,
                # ma
                sma_20=sma_20[i],
                sma_50=sma_50[i],
                sma_200=sma_200[i],
                ema_20=ema_20[i],
                ema_50=ema_50[i],
                ema_200=ema_200[i],
                # bollinger
                bb_mid_20=bb_i.middle if bb_i is not None else None,
                bb_upper_20=bb_i.upper if bb_i is not None else None,
                bb_lower_20=bb_i.lower if bb_i is not None else None,
                bb_width_20=bb_i.width if bb_i is not None else None,
                bb_pctb_20=bb_i.pctb if bb_i is not None else None,
                # atr
                atr_14=atr_14[i],
                atr_30=atr_30[i],
                # calendar
                hour_of_day=ts.hour,
                day_of_week=ts.weekday(),
                is_weekend=ts.weekday() >= 5,
                # family B
                funding_rate=funding_rate_series[i],
                bars_to_next_funding=bars_to_next_series[i],
                funding_cum_24h=funding_cum_24h_series[i],
                basis_perp_vs_spot=basis_series[i],
            )
        )
    return out


# ── indicator helpers (pure, vectorised) ────────────────────────────


def _return_series(closes: list[float], k: int) -> list[float | None]:
    n = len(closes)
    out: list[float | None] = [None] * n
    for i in range(k, n):
        prev = closes[i - k]
        if prev != 0:
            out[i] = closes[i] / prev - 1.0
    return out


def _log_return_series(closes: list[float]) -> list[float | None]:
    """log(close[i] / close[i-1]), None at index 0."""
    n = len(closes)
    out: list[float | None] = [None] * n
    for i in range(1, n):
        prev = closes[i - 1]
        if prev > 0 and closes[i] > 0:
            out[i] = math.log(closes[i] / prev)
    return out


def _rolling_pop_std(
    series: list[float | None], window: int
) -> list[float | None]:
    """Rolling population standard deviation over the last `window` values.

    None in the input is skipped — if the window contains any None, the
    output is None. The series is assumed to have None only at the head
    (warmup), not interleaved.
    """
    n = len(series)
    out: list[float | None] = [None] * n
    if window < 2:
        return out
    for i in range(window - 1, n):
        window_vals = series[i - window + 1 : i + 1]
        if any(v is None for v in window_vals):
            continue
        mean = sum(window_vals) / window  # type: ignore[arg-type]
        var = sum((v - mean) ** 2 for v in window_vals) / window  # type: ignore[operator]
        out[i] = math.sqrt(var)
    return out


def _momentum_series(closes: list[float], k: int) -> list[float | None]:
    n = len(closes)
    out: list[float | None] = [None] * n
    for i in range(k, n):
        out[i] = closes[i] - closes[i - k]
    return out


def rsi_series(closes: list[float], period: int) -> list[float | None]:
    """Public alias for the Wilder RSI series helper.

    Same semantics as the internal `_rsi_series` — exposed so strategy
    code can compute RSI at arbitrary periods without duplicating the
    implementation.
    """
    return _rsi_series(closes, period)


def _rsi_series(closes: list[float], period: int) -> list[float | None]:
    """Wilder's RSI for the full series. None for first `period` bars."""
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < period + 1:
        return out

    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        gains[i] = max(d, 0.0)
        losses[i] = max(-d, 0.0)

    avg_gain = sum(gains[1 : period + 1]) / period
    avg_loss = sum(losses[1 : period + 1]) / period
    out[period] = _rsi_from_avgs(avg_gain, avg_loss)

    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i] = _rsi_from_avgs(avg_gain, avg_loss)

    return out


def _rsi_from_avgs(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0 and avg_gain == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    if avg_gain == 0:
        return 0.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _ema_series(closes: list[float], period: int) -> list[float | None]:
    """Standard EMA (α = 2/(period+1)) with SMA seed at index period-1."""
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < period:
        return out
    seed = sum(closes[:period]) / period
    out[period - 1] = seed
    k = 2.0 / (period + 1)
    prev = seed
    for i in range(period, n):
        cur = closes[i] * k + prev * (1 - k)
        out[i] = cur
        prev = cur
    return out


def _sma_series(closes: list[float], period: int) -> list[float | None]:
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < period:
        return out
    running = sum(closes[:period])
    out[period - 1] = running / period
    for i in range(period, n):
        running += closes[i] - closes[i - period]
        out[i] = running / period
    return out


def _macd_series(
    closes: list[float],
    fast: int,
    slow: int,
    signal: int,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Standard MACD(12,26,9) series.

    Returns (macd_line, signal_line, histogram), each same length as closes.
    """
    n = len(closes)
    ema_fast = _ema_series(closes, fast)
    ema_slow = _ema_series(closes, slow)

    macd_line: list[float | None] = [None] * n
    for i in range(n):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            macd_line[i] = ema_fast[i] - ema_slow[i]  # type: ignore[operator]

    # Signal EMA on the valid tail of macd_line.
    signal_line: list[float | None] = [None] * n
    histogram: list[float | None] = [None] * n

    first_valid = None
    for i in range(n):
        if macd_line[i] is not None:
            first_valid = i
            break

    if first_valid is None or n - first_valid < signal:
        return macd_line, signal_line, histogram

    # SMA seed of signal EMA at index first_valid + signal - 1
    seed_lo = first_valid
    seed_hi = first_valid + signal
    seed = sum(macd_line[seed_lo:seed_hi]) / signal  # type: ignore[arg-type]
    seed_idx = seed_hi - 1
    signal_line[seed_idx] = seed
    histogram[seed_idx] = macd_line[seed_idx] - seed  # type: ignore[operator]

    k = 2.0 / (signal + 1)
    prev = seed
    for i in range(seed_idx + 1, n):
        cur = macd_line[i] * k + prev * (1 - k)  # type: ignore[operator]
        signal_line[i] = cur
        histogram[i] = macd_line[i] - cur  # type: ignore[operator]
        prev = cur

    return macd_line, signal_line, histogram


# ── funding alignment ───────────────────────────────────────────────


def _align_funding(
    bars: Sequence[MarketBar],
    funding_records: Sequence[FundingRateRecord],
) -> tuple[list[float | None], list[int | None], list[float | None]]:
    """Align Binance 8h funding rates to the execution bar timeline.

    Returns three parallel lists the same length as `bars`:
      - funding_rate: forward-filled most-recent settlement rate
      - bars_to_next_funding: bars until the next settlement (> current bar)
      - funding_cum_24h: sum of funding rates whose settle times fall in
          (bar.timestamp - 24h, bar.timestamp]
    """
    n = len(bars)
    funding_rate_out: list[float | None] = [None] * n
    bars_to_next_out: list[int | None] = [None] * n
    funding_cum_24h_out: list[float | None] = [None] * n

    if n == 0 or not funding_records:
        return funding_rate_out, bars_to_next_out, funding_cum_24h_out

    # Normalize funding record timestamps (strip sub-second noise that
    # Binance sometimes returns e.g. 00:00:00.002000).
    fund_times = [
        r.timestamp.replace(microsecond=0) for r in funding_records
    ]
    fund_rates = [r.funding_rate for r in funding_records]

    # Cumulative sum for O(log n) range queries on funding_cum_24h.
    cum = [0.0]
    for rate in fund_rates:
        cum.append(cum[-1] + rate)

    # Forward-fill funding_rate (most recent settlement at or before bar_ts).
    fund_idx = 0
    last_rate: float | None = None
    for i, bar in enumerate(bars):
        bar_ts = bar.timestamp
        while fund_idx < len(fund_times) and fund_times[fund_idx] <= bar_ts:
            last_rate = fund_rates[fund_idx]
            fund_idx += 1
        funding_rate_out[i] = last_rate

    # bars_to_next_funding uses funding TIMES (not bar indices), so it
    # keeps working past the end of the bar series too. For each bar we
    # look up the next funding time strictly greater than bar.timestamp
    # and express the gap in integer bar periods.
    #
    # For bars beyond the last funding time in the records, the field
    # stays None (we can't know future funding times).
    if n >= 2:
        bar_period = bars[1].timestamp - bars[0].timestamp
    else:
        bar_period = timedelta(minutes=15)
    bar_period_seconds = bar_period.total_seconds()
    if bar_period_seconds <= 0:
        bar_period_seconds = 1.0  # degenerate fallback — shouldn't fire

    for i, bar in enumerate(bars):
        idx = bisect_right(fund_times, bar.timestamp)
        if idx < len(fund_times):
            next_fund_time = fund_times[idx]
            delta_seconds = (next_fund_time - bar.timestamp).total_seconds()
            bars_to_next_out[i] = int(round(delta_seconds / bar_period_seconds))
        else:
            bars_to_next_out[i] = None

    # funding_cum_24h: for each bar, sum funding rates in (t-24h, t].
    window = timedelta(hours=24)
    for i, bar in enumerate(bars):
        t_high = bar.timestamp
        t_low = t_high - window
        # lo = first idx with timestamp > t_low (strict)
        lo = bisect_right(fund_times, t_low)
        # hi = first idx with timestamp > t_high → so [lo, hi) is the inclusive range for [t_low+ε, t_high]
        hi = bisect_right(fund_times, t_high)
        funding_cum_24h_out[i] = cum[hi] - cum[lo]

    return funding_rate_out, bars_to_next_out, funding_cum_24h_out
