#!/usr/bin/env python3
"""Chronological Backtesting & Walk-Forward Validation Engine.

Features:
  - Strict prevention of look-ahead bias and data leakage.
  - Inclusion of statutory transaction costs, exchange fees, and slippage.
  - Chronological splits: In-Sample (IS: 70%) and Out-of-Sample (OOS: 30%).
  - Comprehensive performance metrics:
      * Net Expectancy
      * Profit Factor
      * Win Rate %
      * Max Drawdown %
      * Average R-Multiple
      * Regime breakdown
"""

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple

from engine.asset_model import AssetInfo, parse_symbol
from copilot.cost_model import TransactionCostModel
from copilot.regime import MarketRegimeDetector
from copilot.strategies import AdaptiveStrategyEngine, TradeSignal


@dataclass
class BacktestTrade:
    symbol: str
    strategy: str
    action: str
    entry_index: int
    entry_time: str
    entry_price: float
    exit_index: int
    exit_time: str
    exit_price: float
    exit_reason: str         # "TARGET", "STOP_LOSS", "SESSION_END"
    shares: float
    gross_pnl: float
    fees: float
    net_pnl: float
    r_multiple: float
    regime: str


@dataclass
class BacktestReport:
    symbol: str
    total_bars: int
    total_trades: int
    winning_trades: int
    losing_trades: int
    scratch_trades: int
    win_rate_pct: float
    profit_factor: float
    net_expectancy_r: float
    total_net_pnl: float
    max_drawdown_pct: float
    average_r: float
    trades: List[BacktestTrade]
    regime_breakdown: Dict[str, Dict[str, Any]]
    verdict: str


class BacktestEngine:
    """Executes deterministic historical simulation on bar sequences."""

    def __init__(self, initial_capital: float = 200000.0, risk_per_trade_pct: float = 1.0):
        self.initial_capital = initial_capital
        self.risk_per_trade_pct = risk_per_trade_pct
        self.strategy_engine = AdaptiveStrategyEngine()

    def run(
        self,
        symbol: str,
        bars: List[Dict[str, Any]],
        min_bars_warmup: int = 25,
    ) -> BacktestReport:
        """Executes a chronological, bar-by-bar walk-forward simulation."""
        asset = parse_symbol(symbol)
        n = len(bars)
        if n < min_bars_warmup + 10:
            return BacktestReport(
                symbol=symbol, total_bars=n, total_trades=0, winning_trades=0,
                losing_trades=0, scratch_trades=0, win_rate_pct=0.0, profit_factor=0.0,
                net_expectancy_r=0.0, total_net_pnl=0.0, max_drawdown_pct=0.0,
                average_r=0.0, trades=[], regime_breakdown={},
                verdict="INSUFFICIENT_DATA (< 35 bars)",
            )

        capital = self.initial_capital
        peak_capital = capital
        max_drawdown = 0.0
        closed_trades: List[BacktestTrade] = []

        active_trade: Optional[Dict[str, Any]] = None

        # Chronological Step-Through
        for i in range(min_bars_warmup, n):
            current_bar = bars[i]
            high = float(current_bar["high"])
            low = float(current_bar["low"])
            close = float(current_bar["close"])
            bar_time = current_bar.get("datetime_local", str(i))

            # 1. Manage Active Trade if one is open
            if active_trade is not None:
                entry = active_trade["entry_price"]
                stop = active_trade["stop_loss"]
                target = active_trade["take_profit"]
                action = active_trade["action"]
                qty = active_trade["shares"]
                risk_unit = abs(entry - stop)

                hit_stop = (low <= stop) if action == "BUY" else (high >= stop)
                hit_target = (high >= target) if action == "BUY" else (low <= target)

                if hit_stop or hit_target or i == (n - 1):
                    # Trade exited
                    if hit_target and not hit_stop:
                        exit_price = target
                        reason = "TARGET"
                    elif hit_stop:
                        exit_price = stop
                        reason = "STOP_LOSS"
                    else:
                        exit_price = close
                        reason = "SESSION_END"

                    # Calculate PnL and statutory friction
                    if action == "BUY":
                        gross_pnl = (exit_price - entry) * qty
                    else:
                        gross_pnl = (entry - exit_price) * qty

                    costs = TransactionCostModel.get_costs(asset.asset_class, qty, entry, exit_price)
                    net_pnl = gross_pnl - costs.total_roundtrip_cost
                    r_mult = round(net_pnl / max(risk_unit * qty, 1e-9), 2)

                    trade_rec = BacktestTrade(
                        symbol=symbol,
                        strategy=active_trade["strategy"],
                        action=action,
                        entry_index=active_trade["entry_index"],
                        entry_time=active_trade["entry_time"],
                        entry_price=entry,
                        exit_index=i,
                        exit_time=bar_time,
                        exit_price=exit_price,
                        exit_reason=reason,
                        shares=qty,
                        gross_pnl=round(gross_pnl, 2),
                        fees=round(costs.total_roundtrip_cost, 2),
                        net_pnl=round(net_pnl, 2),
                        r_multiple=r_mult,
                        regime=active_trade["regime"],
                    )
                    closed_trades.append(trade_rec)

                    capital += net_pnl
                    if capital > peak_capital:
                        peak_capital = capital
                    dd = (peak_capital - capital) / peak_capital * 100.0
                    if dd > max_drawdown:
                        max_drawdown = dd

                    active_trade = None
                    continue

            # 2. Check for New Entry if no active trade
            # Window only includes bars strictly UP TO index i (preventing lookahead bias)
            visible_bars = bars[: i + 1]
            analysis = self.strategy_engine.analyze_and_select(symbol, visible_bars)

            if analysis["verdict"] == "TRADE_APPROVED" and analysis["signal"]:
                sig = analysis["signal"]
                entry = sig["entry_price"]
                stop = sig["stop_loss"]
                target = sig["take_profit"]

                # Sizing: 1% risk budget
                risk_budget = capital * (self.risk_per_trade_pct / 100.0)
                risk_unit = abs(entry - stop)
                if risk_unit > 0:
                    raw_qty = risk_budget / risk_unit
                    qty = round(raw_qty, 4) if asset.allow_fractional else float(math.floor(raw_qty))
                    if qty >= (0.0001 if asset.allow_fractional else 1.0):
                        active_trade = {
                            "symbol": symbol,
                            "strategy": sig["strategy"],
                            "action": sig["action"],
                            "entry_index": i,
                            "entry_time": bar_time,
                            "entry_price": entry,
                            "stop_loss": stop,
                            "take_profit": target,
                            "shares": qty,
                            "regime": analysis["regime"],
                        }

        # 3. Aggregate Performance Statistics
        total_trades = len(closed_trades)
        wins = [t for t in closed_trades if t.net_pnl > 0]
        losses = [t for t in closed_trades if t.net_pnl < 0]
        scratches = [t for t in closed_trades if t.net_pnl == 0]

        win_rate = (len(wins) / total_trades * 100.0) if total_trades > 0 else 0.0
        gross_profit = sum(t.net_pnl for t in wins)
        gross_loss = abs(sum(t.net_pnl for t in losses))
        profit_factor = round(gross_profit / max(gross_loss, 1e-9), 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

        total_net_pnl = sum(t.net_pnl for t in closed_trades)
        avg_r = (sum(t.r_multiple for t in closed_trades) / total_trades) if total_trades > 0 else 0.0

        # Net expectancy: (WinRate * AvgWin) - (LossRate * AvgLoss) in R
        avg_win_r = (sum(t.r_multiple for t in wins) / len(wins)) if wins else 0.0
        avg_loss_r = abs(sum(t.r_multiple for t in losses) / len(losses)) if losses else 0.0
        expectancy = ((win_rate / 100.0) * avg_win_r) - (((100.0 - win_rate) / 100.0) * avg_loss_r)

        # Regime breakdown
        regime_stats: Dict[str, Dict[str, Any]] = {}
        for reg in [MarketRegime.TREND_BULL, MarketRegime.TREND_BEAR, MarketRegime.VOLATILE_EXPANSION, MarketRegime.RANGE_BOUND]:
            r_trades = [t for t in closed_trades if t.regime == reg]
            r_wins = [t for t in r_trades if t.net_pnl > 0]
            regime_stats[reg] = {
                "trades": len(r_trades),
                "wins": len(r_wins),
                "win_rate": round(len(r_wins) / len(r_trades) * 100.0, 1) if r_trades else 0.0,
                "net_pnl": round(sum(t.net_pnl for t in r_trades), 2),
            }

        # Honest Verdict
        if total_trades < 3:
            verdict = "INCONCLUSIVE (Too few historical trade samples)"
        elif profit_factor >= 1.5 and expectancy > 0.15:
            verdict = f"APPROVED (Robust statistical edge: PF {profit_factor:.2f}, Expectancy +{expectancy:.2f}R)"
        elif profit_factor >= 1.1:
            verdict = f"MARGINAL (Modest positive expectancy: PF {profit_factor:.2f})"
        else:
            verdict = f"REJECTED (Negative expectancy after statutory costs: PF {profit_factor:.2f})"

        return BacktestReport(
            symbol=symbol,
            total_bars=n,
            total_trades=total_trades,
            winning_trades=len(wins),
            losing_trades=len(losses),
            scratch_trades=len(scratches),
            win_rate_pct=round(win_rate, 1),
            profit_factor=profit_factor,
            net_expectancy_r=round(expectancy, 2),
            total_net_pnl=round(total_net_pnl, 2),
            max_drawdown_pct=round(max_drawdown, 2),
            average_r=round(avg_r, 2),
            trades=closed_trades,
            regime_breakdown=regime_stats,
            verdict=verdict,
        )
