"""Strategy C 15m dataset — schema and aligner.

Unifies BTCUSDT 15m price + Coinglass derivatives data into one row per
timestamp, ready for feature engineering and backtesting.

Architecture:
    - Pair-level (BTCUSDT, per-exchange) is the main series.
    - Coin-level (BTC, cross-exchange aggregated) is background factors.
    - Alignment: inner join on timestamp; missing data drops the row.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from adapters.coinglass_client import (
    BasisBar,
    CoinglassClient,
    CVDBar,
    FundingRateBar,
    LiquidationBar,
    OIBar,
    TakerVolumeBar,
)


@dataclass(frozen=True)
class StrategyCBar:
    """One unified 15m bar for Strategy C.

    Aligned across BTCUSDT price + 6 pair-level Coinglass channels +
    2 cross-exchange background factors.
    """

    timestamp: datetime

    # Price (from Binance 15m OHLCV)
    open: float
    close: float
    volume: float

    # Pair-level Coinglass main series (BTCUSDT @ Binance)
    oi_close: float
    oi_pct_change: float          # vs previous bar; first bar = 0
    funding: float                 # close funding rate
    long_liq_usd: float            # long liquidations in USD this bar
    short_liq_usd: float           # short liquidations in USD this bar
    liq_imbalance: float           # (short - long) / (long + short); 0 if both 0.
                                    # Positive = more shorts liquidated = short squeeze / bullish pressure.
    taker_buy_usd: float           # taker buy volume USD
    taker_sell_usd: float          # taker sell volume USD
    taker_delta_usd: float         # buy - sell
    cvd: float                     # cumulative volume delta (provided by API)
    basis: float                   # close basis (futures premium)

    # Cross-exchange background factors
    funding_oi_weighted: float     # OI-weighted funding (BTC, all exchanges)
    stablecoin_oi: float           # aggregated stablecoin-margined OI close


# ── Aligner ──────────────────────────────────────────────────────────


def align_strategy_c_bars(
    price_bars: Sequence[tuple[datetime, float, float, float]],  # (ts, open, close, volume)
    oi_bars: Sequence[OIBar],
    funding_bars: Sequence[FundingRateBar],
    liquidation_bars: Sequence[LiquidationBar],
    taker_bars: Sequence[TakerVolumeBar],
    cvd_bars: Sequence[CVDBar] | None,  # None → skip pair_cvd (longer history)
    basis_bars: Sequence[BasisBar],
    funding_oi_weighted_bars: Sequence[FundingRateBar],
    stablecoin_oi_bars: Sequence[OIBar],
) -> list[StrategyCBar]:
    """Inner-join all sources on timestamp, compute derived fields, return sorted rows.

    Rules:
        - Output is sorted ascending by timestamp.
        - Only timestamps present in ALL sources are kept (intersection).
        - If cvd_bars is None, pair_cvd is dropped from the intersection and
          each output row's `cvd` field is set to 0.0. The feature layer will
          then compute cvd_delta as a flat 0.0 series (and z-scores will
          be 0/None); downstream code should use include_cvd=False scoring.
        - Derived fields:
            * oi_pct_change: (oi_t - oi_{t-1}) / oi_{t-1}; first row = 0
            * taker_delta_usd: buy - sell
            * liq_imbalance: (short - long) / (long + short); 0 if denominator is 0
    """
    # Index every source by timestamp for O(1) lookup
    price_map = {ts: (op, close, vol) for ts, op, close, vol in price_bars}
    oi_map = {b.timestamp: b for b in oi_bars}
    funding_map = {b.timestamp: b for b in funding_bars}
    liq_map = {b.timestamp: b for b in liquidation_bars}
    taker_map = {b.timestamp: b for b in taker_bars}
    basis_map = {b.timestamp: b for b in basis_bars}
    funding_w_map = {b.timestamp: b for b in funding_oi_weighted_bars}
    stable_oi_map = {b.timestamp: b for b in stablecoin_oi_bars}

    # Intersection of all timestamp sets (conditionally include cvd)
    common = (
        set(price_map)
        & set(oi_map)
        & set(funding_map)
        & set(liq_map)
        & set(taker_map)
        & set(basis_map)
        & set(funding_w_map)
        & set(stable_oi_map)
    )
    if cvd_bars is not None:
        cvd_map: dict[datetime, CVDBar] = {b.timestamp: b for b in cvd_bars}
        common &= set(cvd_map)
    else:
        cvd_map = {}
    sorted_ts = sorted(common)

    out: list[StrategyCBar] = []
    prev_oi: float | None = None

    for ts in sorted_ts:
        op, close, volume = price_map[ts]
        oi = oi_map[ts]
        fr = funding_map[ts]
        lq = liq_map[ts]
        tk = taker_map[ts]
        bs = basis_map[ts]
        fw = funding_w_map[ts]
        so = stable_oi_map[ts]
        cv_val = cvd_map[ts].cvd if cvd_bars is not None else 0.0

        # Derived: OI pct change vs previous bar
        if prev_oi is None or prev_oi == 0:
            oi_pct = 0.0
        else:
            oi_pct = (oi.close - prev_oi) / prev_oi
        prev_oi = oi.close

        # Derived: taker delta
        taker_delta = tk.buy_usd - tk.sell_usd

        # Derived: liquidation imbalance — (short - long) / total.
        # Positive = more shorts liquidated (bullish pressure from short squeeze).
        liq_total = lq.long_usd + lq.short_usd
        liq_imb = (lq.short_usd - lq.long_usd) / liq_total if liq_total > 0 else 0.0

        out.append(
            StrategyCBar(
                timestamp=ts,
                open=op,
                close=close,
                volume=volume,
                oi_close=oi.close,
                oi_pct_change=oi_pct,
                funding=fr.close,
                long_liq_usd=lq.long_usd,
                short_liq_usd=lq.short_usd,
                liq_imbalance=liq_imb,
                taker_buy_usd=tk.buy_usd,
                taker_sell_usd=tk.sell_usd,
                taker_delta_usd=taker_delta,
                cvd=cv_val,
                basis=bs.close_basis,
                funding_oi_weighted=fw.close,
                stablecoin_oi=so.close,
            )
        )

    return out


# ── Fetcher (orchestrator) ───────────────────────────────────────────


def fetch_strategy_c_bars(
    client: CoinglassClient,
    price_bars: Sequence[tuple[datetime, float, float]],
    *,
    exchange: str = "Binance",
    symbol: str = "BTCUSDT",
    interval: str = "15m",
    start_time: int | None = None,
    end_time: int | None = None,
    background_exchange_list: str = "Binance,OKX,Bybit",
    include_cvd: bool = True,
) -> list[StrategyCBar]:
    """Fetch all 8 Coinglass channels and align with provided price bars.

    The price_bars argument is the BTCUSDT 15m OHLCV (close + volume) loaded
    from disk or fetched from Binance — Coinglass doesn't expose price for
    futures pairs the same way, so we use the existing price source.

    Args:
        client: Authenticated CoinglassClient
        price_bars: List of (timestamp, close, volume) tuples
        exchange: Exchange for pair-level endpoints (default Binance)
        symbol: Trading pair (default BTCUSDT)
        interval: Bar interval (default 15m)
        start_time: Unix epoch seconds, lower bound (optional)
        end_time: Unix epoch seconds, upper bound (optional)
        background_exchange_list: Comma-separated exchanges for cross-exchange
            background factors (default Binance,OKX,Bybit)

    Returns:
        List of StrategyCBar sorted by timestamp, intersected across all sources.
    """
    # 6 pair-level main series
    oi = client.fetch_pair_oi_history(
        exchange=exchange, symbol=symbol, interval=interval,
        start_time=start_time, end_time=end_time,
    )
    funding = client.fetch_pair_funding_rate_history(
        exchange=exchange, symbol=symbol, interval=interval,
        start_time=start_time, end_time=end_time,
    )
    liq = client.fetch_pair_liquidation_history(
        exchange=exchange, symbol=symbol, interval=interval,
        start_time=start_time, end_time=end_time,
    )
    taker = client.fetch_pair_taker_volume_history(
        exchange=exchange, symbol=symbol, interval=interval,
        start_time=start_time, end_time=end_time,
    )
    cvd = None
    if include_cvd:
        cvd = client.fetch_pair_cvd_history(
            exchange=exchange, symbol=symbol, interval=interval,
            start_time=start_time, end_time=end_time,
        )
    basis = client.fetch_basis_history(
        exchange=exchange, symbol=symbol, interval=interval,
        start_time=start_time, end_time=end_time,
    )

    # 2 cross-exchange background factors
    funding_w = client.fetch_funding_rate_history(
        symbol="BTC", interval=interval,
        start_time=start_time, end_time=end_time,
    )
    stable_oi = client.fetch_stablecoin_oi_history(
        symbol="BTC", exchange_list=background_exchange_list, interval=interval,
        start_time=start_time, end_time=end_time,
    )

    return align_strategy_c_bars(
        price_bars=price_bars,
        oi_bars=oi,
        funding_bars=funding,
        liquidation_bars=liq,
        taker_bars=taker,
        cvd_bars=cvd,
        basis_bars=basis,
        funding_oi_weighted_bars=funding_w,
        stablecoin_oi_bars=stable_oi,
    )


# ── CSV save/load ────────────────────────────────────────────────────

CSV_HEADER = [
    "timestamp", "open", "close", "volume",
    "oi_close", "oi_pct_change", "funding",
    "long_liq_usd", "short_liq_usd", "liq_imbalance",
    "taker_buy_usd", "taker_sell_usd", "taker_delta_usd",
    "cvd", "basis",
    "funding_oi_weighted", "stablecoin_oi",
]


def save_strategy_c_csv(bars: Sequence[StrategyCBar], path: str) -> str:
    """Write bars to CSV with the canonical schema header."""
    import csv
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(CSV_HEADER)
        for b in bars:
            w.writerow([
                b.timestamp.isoformat(), b.open, b.close, b.volume,
                b.oi_close, b.oi_pct_change, b.funding,
                b.long_liq_usd, b.short_liq_usd, b.liq_imbalance,
                b.taker_buy_usd, b.taker_sell_usd, b.taker_delta_usd,
                b.cvd, b.basis,
                b.funding_oi_weighted, b.stablecoin_oi,
            ])
    return path


def load_strategy_c_csv(path: str) -> list[StrategyCBar]:
    """Load bars from CSV produced by save_strategy_c_csv."""
    import csv
    bars: list[StrategyCBar] = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            bars.append(
                StrategyCBar(
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    open=float(row["open"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                    oi_close=float(row["oi_close"]),
                    oi_pct_change=float(row["oi_pct_change"]),
                    funding=float(row["funding"]),
                    long_liq_usd=float(row["long_liq_usd"]),
                    short_liq_usd=float(row["short_liq_usd"]),
                    liq_imbalance=float(row["liq_imbalance"]),
                    taker_buy_usd=float(row["taker_buy_usd"]),
                    taker_sell_usd=float(row["taker_sell_usd"]),
                    taker_delta_usd=float(row["taker_delta_usd"]),
                    cvd=float(row["cvd"]),
                    basis=float(row["basis"]),
                    funding_oi_weighted=float(row["funding_oi_weighted"]),
                    stablecoin_oi=float(row["stablecoin_oi"]),
                )
            )
    return bars
