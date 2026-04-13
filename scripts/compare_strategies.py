"""Compare: split capital across 3 strategies vs integrated approach."""
from __future__ import annotations

import logging
import sys

sys.path.insert(0, "src")
logging.disable(logging.CRITICAL)

from adapters.base import OrderRequest, Position
from data.backfill import load_bars_from_csv
from execution.paper_broker import PaperBroker
from research.backtest import run_backtest
from risk.limits import RiskLimits, allow_order, calculate_order_quantity
from strategies.trend_breakout import TrendBreakoutConfig, TrendBreakoutStrategy

bars = load_bars_from_csv("src/data/btcusdt_4h_5year.csv")


def sma(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def calc_rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains = losses = 0.0
    for i in range(-period, 0):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains += diff
        else:
            losses += abs(diff)
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


# --- Strategy A: Simple MA Crossover ---
def run_ma(bars_list, cash=10000.0):
    pos = None
    closes: list[float] = []
    for b in bars_list:
        closes.append(b.close)
        price = b.close
        ma50 = sma(closes, 50)
        ma200 = sma(closes, 200)
        if ma50 is None or ma200 is None:
            continue
        if pos is None:
            if ma50 > ma200 * 1.01:
                qty = cash * 0.9 / price
                pos = {"s": "long", "e": price, "q": qty}
                cash -= qty * price * 1.001
            elif ma50 < ma200 * 0.99:
                qty = cash * 0.9 / price
                pos = {"s": "short", "e": price, "q": qty, "m": qty * price}
                cash -= qty * price * 1.001
        else:
            if pos["s"] == "long":
                if ma50 < ma200 or price < pos["e"] * 0.95:
                    cash += pos["q"] * price * 0.999
                    pos = None
            else:
                if ma50 > ma200 or price > pos["e"] * 1.05:
                    pnl = (pos["e"] - price) * pos["q"]
                    cash += pos["m"] + pnl - pos["q"] * price * 0.001
                    pos = None
    if pos:
        if pos["s"] == "long":
            cash += pos["q"] * bars_list[-1].close * 0.999
        else:
            pnl = (pos["e"] - bars_list[-1].close) * pos["q"]
            cash += pos["m"] + pnl
    return cash


# --- Strategy B: RSI Mean Reversion ---
def run_rsi_strat(bars_list, cash=10000.0):
    pos = None
    closes: list[float] = []
    for b in bars_list:
        closes.append(b.close)
        price = b.close
        r = calc_rsi(closes, 14)
        if len(closes) < 20:
            continue
        if pos is None:
            if r < 30:
                qty = cash * 0.9 / price
                pos = {"s": "long", "e": price, "q": qty}
                cash -= qty * price * 1.001
            elif r > 70:
                qty = cash * 0.9 / price
                pos = {"s": "short", "e": price, "q": qty, "m": qty * price}
                cash -= qty * price * 1.001
        else:
            if pos["s"] == "long":
                if r > 60 or price < pos["e"] * 0.95 or price > pos["e"] * 1.08:
                    cash += pos["q"] * price * 0.999
                    pos = None
            else:
                if r < 40 or price > pos["e"] * 1.05 or price < pos["e"] * 0.92:
                    pnl = (pos["e"] - price) * pos["q"]
                    cash += pos["m"] + pnl - pos["q"] * price * 0.001
                    pos = None
    if pos:
        if pos["s"] == "long":
            cash += pos["q"] * bars_list[-1].close * 0.999
        else:
            pnl = (pos["e"] - bars_list[-1].close) * pos["q"]
            cash += pos["m"] + pnl
    return cash


# --- Strategy C: Channel (no narrative) ---
def run_channel(bars_list, cash=10000.0):
    r = run_backtest(
        bars=bars_list,
        symbol="BTCUSDT",
        strategy=TrendBreakoutStrategy(
            TrendBreakoutConfig(
                impulse_lookback=12, structure_lookback=24, secondary_structure_lookback=48,
                pivot_window=2, min_pivot_highs=2, min_pivot_lows=2,
                impulse_threshold_pct=0.02, entry_buffer_pct=0.20, stop_buffer_pct=0.08,
                min_r_squared=0.0, min_stop_atr_multiplier=1.5, time_stop_bars=84,
                use_narrative_regime=False, require_parent_confirmation=False,
                enable_ascending_channel_resistance_rejection=False,
                enable_descending_channel_breakout_long=False,
                enable_ascending_channel_breakdown_short=False,
            )
        ),
        broker=PaperBroker(initial_cash=cash, fee_rate=0.001, slippage_rate=0.0005),
        limits=RiskLimits(),
    )
    return r.final_equity, r.total_trades, r.max_drawdown_pct


# --- Integrated: Channel + MA Regime Filter ---
def run_channel_with_ma_filter(bars_list, cash=10000.0):
    strategy = TrendBreakoutStrategy(
        TrendBreakoutConfig(
            impulse_lookback=12, structure_lookback=24, secondary_structure_lookback=48,
            pivot_window=2, min_pivot_highs=2, min_pivot_lows=2,
            impulse_threshold_pct=0.02, entry_buffer_pct=0.20, stop_buffer_pct=0.08,
            min_r_squared=0.0, min_stop_atr_multiplier=1.5, time_stop_bars=84,
            use_narrative_regime=False, require_parent_confirmation=False,
            enable_ascending_channel_resistance_rejection=False,
            enable_descending_channel_breakout_long=False,
            enable_ascending_channel_breakdown_short=False,
        )
    )
    broker = PaperBroker(initial_cash=cash, fee_rate=0.001, slippage_rate=0.0005)
    limits = RiskLimits()
    closes: list[float] = []
    trades = 0
    peak_eq = cash
    max_dd = 0.0

    for i in range(len(bars_list)):
        closes.append(bars_list[i].close)
        price = bars_list[i].close
        if i < 200:
            continue

        ma50_val = sma(closes, 50)
        ma200_val = sma(closes, 200)
        pos = broker.get_position("BTCUSDT")

        # Manage open position
        if pos.is_open:
            stop_p = getattr(pos, "stop_price", None)
            target_p = getattr(pos, "target_price", None)
            do_exit = False
            if pos.side == "long":
                if (stop_p and bars_list[i].low <= stop_p) or (target_p and bars_list[i].high >= target_p):
                    do_exit = True
                    side = "sell"
            elif pos.side == "short":
                if (stop_p and bars_list[i].high >= stop_p) or (target_p and bars_list[i].low <= target_p):
                    do_exit = True
                    side = "cover"
            if do_exit:
                broker.submit_order(
                    OrderRequest(symbol="BTCUSDT", side=side, quantity=pos.quantity, timestamp=bars_list[i].timestamp),
                    price,
                )
                trades += 1
        else:
            # Evaluate channel strategy
            window = bars_list[max(0, i - 120) : i + 1]
            signal = strategy.generate_signal("BTCUSDT", window, Position(symbol="BTCUSDT"))

            # MA regime filter
            if signal.action == "buy" and (ma50_val is None or ma200_val is None or ma50_val <= ma200_val):
                signal = type(signal)(action="hold", confidence=0.0, reason="ma_filter")
            if signal.action == "short" and (ma50_val is None or ma200_val is None or ma50_val >= ma200_val):
                signal = type(signal)(action="hold", confidence=0.0, reason="ma_filter")

            if signal.action in ("buy", "short"):
                c = broker.get_cash()
                qty = calculate_order_quantity(c, price, limits)
                if qty > 0:
                    order = OrderRequest(
                        symbol="BTCUSDT", side=signal.action, quantity=qty,
                        timestamp=bars_list[i].timestamp,
                        metadata={
                            "stop_price": signal.stop_price,
                            "target_price": signal.target_price,
                            "reason": signal.reason,
                        },
                    )
                    if allow_order(c, order, price, 0, limits, pos):
                        broker.submit_order(order, price)

        eq = broker.mark_to_market("BTCUSDT", price)
        peak_eq = max(peak_eq, eq)
        dd = (peak_eq - eq) / peak_eq * 100
        max_dd = max(max_dd, dd)

    # Close any open position
    pos = broker.get_position("BTCUSDT")
    if pos.is_open:
        side = "sell" if pos.side == "long" else "cover"
        broker.submit_order(
            OrderRequest(symbol="BTCUSDT", side=side, quantity=pos.quantity, timestamp=bars_list[-1].timestamp),
            bars_list[-1].close,
        )
        trades += 1

    final_eq = broker.mark_to_market("BTCUSDT", bars_list[-1].close)
    return final_eq, trades, max_dd


# ====================== RUN ======================
print(f"Period: {bars[0].timestamp.date()} to {bars[-1].timestamp.date()} ({len(bars)} bars)")
print(f"BTC: ${bars[0].close:,.0f} -> ${bars[-1].close:,.0f}")
print()

ma_eq = run_ma(bars, 10000)
rsi_eq = run_rsi_strat(bars, 10000)
ch_eq, ch_trades, ch_dd = run_channel(bars, 10000)
combo_eq, combo_trades, combo_dd = run_channel_with_ma_filter(bars, 10000)
bh = 10000 * bars[-1].close / bars[0].close

split_total = ma_eq + rsi_eq + ch_eq

print("=" * 65)
print("  APPROACH 1: Split $10k x 3 strategies (your idea)")
print("=" * 65)
print(f"  {'MA Crossover':<25} ${ma_eq:>9,.0f}  ({(ma_eq/10000-1)*100:+.1f}%)")
print(f"  {'RSI Mean Reversion':<25} ${rsi_eq:>9,.0f}  ({(rsi_eq/10000-1)*100:+.1f}%)")
print(f"  {'Channel (no filter)':<25} ${ch_eq:>9,.0f}  ({(ch_eq/10000-1)*100:+.1f}%)")
print(f"  {'─' * 40}")
print(f"  {'TOTAL ($30k start)':<25} ${split_total:>9,.0f}  ({(split_total/30000-1)*100:+.1f}%)")
print()
print("=" * 65)
print("  APPROACH 2: $10k Channel + MA as regime filter")
print("=" * 65)
print(f"  {'Channel + MA filter':<25} ${combo_eq:>9,.0f}  ({(combo_eq/10000-1)*100:+.1f}%)  trades={combo_trades}  DD={combo_dd:.1f}%")
print()
print("=" * 65)
print("  REFERENCE")
print("=" * 65)
print(f"  {'Channel alone':<25} ${ch_eq:>9,.0f}  ({(ch_eq/10000-1)*100:+.1f}%)  trades={ch_trades}  DD={ch_dd:.1f}%")
print(f"  {'Buy & Hold':<25} ${bh:>9,.0f}  ({(bh/10000-1)*100:+.1f}%)")
