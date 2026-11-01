"""Paper Trading Engine with Atomic State Persistence and Trade Analytics.

Provides first-class paper trading simulation:
1. Real-time / bar-by-bar order processing and fills simulation.
2. Statutory charges, exchange fees, and slippage deduction using TransactionCostModel.
3. Stop-loss and take-profit automatic trigger simulation.
4. Separate, isolated atomic state persistence to trader/state/paper_state.json.
5. Robust trade history recording and performance metrics (Win Rate, Profit Factor, Max Drawdown).
6. Clean restart recovery and explicit, confirmed account resets.
"""

import copy
import json
import math
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from copilot.cost_model import TransactionCostModel, MarketAssetType
from copilot.risk_engine import OrderTicket, UserRiskConfig, RiskAndSizingEngine


class PaperTradingEngine:
    """Independent paper trading engine simulating live fills, slippage,

    statutory costs, and automated bracket management with persistent state.
    """

    def __init__(
        self,
        state_file_path: Optional[str] = None,
        initial_balance: float = 100000.0,
        currency: str = "INR",
        risk_config: Optional[UserRiskConfig] = None,
    ):
        if state_file_path:
            self.state_file_path = state_file_path
        else:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self.state_file_path = os.path.join(base_dir, "trader", "state", "paper_state.json")

        self.initial_balance = float(initial_balance)
        self.currency = currency
        self.risk_config = risk_config or UserRiskConfig(
            market="NSE",
            broker="PAPER",
            currency=currency,
            balance=self.initial_balance,
            mode="paper",
        )
        self.risk_engine = RiskAndSizingEngine(self.risk_config)

        # In-memory state
        self.balance: float = self.initial_balance
        self.equity: float = self.initial_balance
        self.peak_equity: float = self.initial_balance
        self.max_drawdown_pct: float = 0.0
        self.positions: Dict[str, Dict[str, Any]] = {}
        self.orders: Dict[str, Dict[str, Any]] = {}
        self.trade_history: List[Dict[str, Any]] = []
        self.stats: Dict[str, Any] = {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "breakeven_trades": 0,
            "win_rate_pct": 0.0,
            "profit_factor": 0.0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "net_pnl": 0.0,
            "average_r": 0.0,
            "average_win": 0.0,
            "average_loss": 0.0,
            "max_drawdown_pct": 0.0,
        }

        # Initialize or load state
        self._ensure_directory()
        if os.path.exists(self.state_file_path):
            self.load_state()
        else:
            self.save_state()

    def _ensure_directory(self) -> None:
        """Ensure destination directory exists."""
        dir_path = os.path.dirname(self.state_file_path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)

    def save_state(self) -> None:
        """Save paper trading state atomically to prevent corrupted reads."""
        self._ensure_directory()
        state_data = {
            "version": "1.0",
            "mode": "paper",
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "currency": self.currency,
            "initial_balance": self.initial_balance,
            "balance": round(self.balance, 4),
            "equity": round(self.equity, 4),
            "peak_equity": round(self.peak_equity, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "positions": self.positions,
            "orders": self.orders,
            "trade_history": self.trade_history,
            "stats": self.stats,
        }
        temp_path = f"{self.state_file_path}.{os.getpid()}_{time.time_ns()}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(state_data, f, indent=2)
            for attempt in range(5):
                try:
                    os.replace(temp_path, self.state_file_path)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.05)
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    def load_state(self) -> bool:
        """Load state from persistent file."""
        if not os.path.exists(self.state_file_path):
            return False
        try:
            with open(self.state_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.currency = data.get("currency", self.currency)
            self.initial_balance = float(data.get("initial_balance", self.initial_balance))
            self.balance = float(data.get("balance", self.balance))
            self.equity = float(data.get("equity", self.equity))
            self.peak_equity = float(data.get("peak_equity", self.equity))
            self.max_drawdown_pct = float(data.get("max_drawdown_pct", 0.0))
            self.positions = data.get("positions", {})
            self.orders = data.get("orders", {})
            self.trade_history = data.get("trade_history", [])
            self.stats = data.get("stats", self.stats)
            return True
        except Exception:
            return False

    def place_order(self, ticket: OrderTicket) -> Dict[str, Any]:
        """Validate, size, and place a simulated paper order.

        Market orders are filled immediately with realistic slippage and statutory costs.
        Limit and stop orders are added to pending orders.
        """
        # Synchronize dynamic risk metrics before validation
        self.risk_engine.daily_realized_loss = self.get_realized_loss_today()
        validation = self.risk_engine.validate_order(
            ticket=ticket,
            open_positions=self.positions,
            pending_orders=self.orders,
        )

        if not validation["allowed"]:
            return {
                "success": False,
                "order_id": None,
                "reason": validation["reason"],
                "violations": validation["violations"],
            }

        order_id = f"paper_ord_{uuid.uuid4().hex[:10]}"
        now_iso = datetime.now(timezone.utc).isoformat()

        # Calculate entry friction and slippage
        market_type = TransactionCostModel.detect_market_type(ticket.symbol, ticket.market)
        slippage_per_unit = TransactionCostModel.calculate_slippage(ticket.entry_price, market_type)

        if ticket.side.upper() == "BUY":
            fill_price = ticket.entry_price + slippage_per_unit
        else:
            fill_price = ticket.entry_price - slippage_per_unit

        required_capital = fill_price * ticket.quantity
        if required_capital > self.balance:
            return {
                "success": False,
                "order_id": None,
                "reason": f"Insufficient paper balance: required {required_capital:.2f}, available {self.balance:.2f}",
                "violations": ["INSUFFICIENT_BALANCE"],
            }

        cost_breakdown = TransactionCostModel.calculate_total_friction(
            market=market_type,
            entry_price=fill_price,
            exit_price=ticket.take_profit,
            quantity=ticket.quantity,
        )
        entry_friction = cost_breakdown["entry_fees"] + cost_breakdown["entry_slippage"]

        # Deduct capital and entry friction from cash balance
        self.balance -= (required_capital + entry_friction)

        # Create paper position
        position = {
            "position_id": f"pos_{uuid.uuid4().hex[:8]}",
            "order_id": order_id,
            "symbol": ticket.symbol,
            "market": ticket.market,
            "side": ticket.side.upper(),
            "quantity": ticket.quantity,
            "entry_price": round(fill_price, 4),
            "current_price": round(fill_price, 4),
            "stop_loss": round(ticket.stop_loss, 4),
            "take_profit": round(ticket.take_profit, 4),
            "planned_rr": round(ticket.planned_rr, 2),
            "strategy": ticket.strategy,
            "entry_time": now_iso,
            "entry_friction": round(entry_friction, 4),
            "unrealized_pnl": 0.0,
            "unrealized_pnl_pct": 0.0,
        }
        self.positions[ticket.symbol] = position

        # Record filled order
        order_record = {
            "order_id": order_id,
            "symbol": ticket.symbol,
            "market": ticket.market,
            "side": ticket.side.upper(),
            "quantity": ticket.quantity,
            "requested_price": round(ticket.entry_price, 4),
            "fill_price": round(fill_price, 4),
            "slippage": round(slippage_per_unit, 4),
            "status": "FILLED",
            "created_at": now_iso,
            "filled_at": now_iso,
            "strategy": ticket.strategy,
            "friction_paid": round(entry_friction, 4),
        }
        self.orders[order_id] = order_record

        self._recompute_equity()
        self.save_state()

        return {
            "success": True,
            "order_id": order_id,
            "fill_price": round(fill_price, 4),
            "quantity": ticket.quantity,
            "entry_friction": round(entry_friction, 4),
            "position": position,
        }

    def process_market_tick(self, symbol: str, bar: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Evaluate a new market bar against open positions and orders for the symbol.

        Checks stop-loss and take-profit breach, updating equity and triggering simulated exits.
        Returns a list of triggered event descriptions.
        """
        events = []
        if symbol not in self.positions:
            return events

        pos = self.positions[symbol]
        high = float(bar.get("high", bar.get("close", pos["current_price"])))
        low = float(bar.get("low", bar.get("close", pos["current_price"])))
        close = float(bar.get("close", pos["current_price"]))
        open_price = float(bar.get("open", close))
        timestamp = bar.get("timestamp", datetime.now(timezone.utc).isoformat())

        pos["current_price"] = close

        # Check Stop Loss and Take Profit independently to detect ambiguous bars
        sl_hit = False
        tp_hit = False

        if pos["side"] == "BUY":
            if low <= pos["stop_loss"]:
                sl_hit = True
            if high >= pos["take_profit"]:
                tp_hit = True
        else:  # SELL
            if high >= pos["stop_loss"]:
                sl_hit = True
            if low <= pos["take_profit"]:
                tp_hit = True

        # Conservative execution: if an ambiguous candle touches both Stop-Loss and Take-Profit,
        # prioritize defensive risk management and assume Stop-Loss was triggered first.
        if sl_hit:
            exit_price = pos["stop_loss"]
            closed = self.close_position(
                symbol=symbol,
                exit_price=exit_price,
                reason="STOP_LOSS" if not tp_hit else "STOP_LOSS_AMBIGUOUS_BAR",
                exit_time=timestamp,
            )
            events.append({"event": "STOP_LOSS_TRIGGERED", "details": closed})
        elif tp_hit:
            exit_price = pos["take_profit"]
            closed = self.close_position(
                symbol=symbol,
                exit_price=exit_price,
                reason="TAKE_PROFIT_3R",
                exit_time=timestamp,
            )
            events.append({"event": "TAKE_PROFIT_TRIGGERED", "details": closed})
        else:
            # Update unrealized PnL
            if pos["side"] == "BUY":
                pos["unrealized_pnl"] = round((close - pos["entry_price"]) * pos["quantity"], 4)
            else:
                pos["unrealized_pnl"] = round((pos["entry_price"] - close) * pos["quantity"], 4)
            pos["unrealized_pnl_pct"] = round(
                (pos["unrealized_pnl"] / (pos["entry_price"] * pos["quantity"])) * 100.0, 2
            )
            self._recompute_equity()
            self.save_state()

        return events

    def close_position(
        self,
        symbol: str,
        exit_price: float,
        reason: str = "MANUAL",
        exit_time: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Close an open position, deducting exit fees and recording trade metrics."""
        if symbol not in self.positions:
            return {"success": False, "reason": f"No open position for {symbol}"}

        pos = self.positions[symbol]
        now_iso = exit_time or datetime.now(timezone.utc).isoformat()
        qty = pos["quantity"]
        entry_price = pos["entry_price"]
        market_type = TransactionCostModel.detect_market_type(symbol, pos["market"])

        # Calculate exit friction
        slippage = TransactionCostModel.calculate_slippage(exit_price, market_type)
        if pos["side"] == "BUY":
            realized_exit_price = exit_price - slippage
            gross_pnl = (realized_exit_price - entry_price) * qty
        else:
            realized_exit_price = exit_price + slippage
            gross_pnl = (entry_price - realized_exit_price) * qty

        cost_breakdown = TransactionCostModel.calculate_total_friction(
            market=market_type,
            entry_price=entry_price,
            exit_price=realized_exit_price,
            quantity=qty,
        )
        exit_fees = cost_breakdown["exit_fees"] + cost_breakdown["exit_slippage"]
        total_friction = pos["entry_friction"] + exit_fees

        net_pnl = gross_pnl - exit_fees  # entry_friction was already deducted from cash on open
        total_net_trade_pnl = gross_pnl - total_friction

        # Return capital to cash balance
        returned_cash = (realized_exit_price * qty) - exit_fees
        self.balance += returned_cash

        # Calculate R multiple achieved: net_pnl / planned_initial_risk
        initial_risk_per_share = abs(entry_price - pos["stop_loss"])
        initial_risk_total = initial_risk_per_share * qty
        r_multiple = round(total_net_trade_pnl / initial_risk_total, 2) if initial_risk_total > 0 else 0.0

        trade_record = {
            "trade_id": f"trade_{uuid.uuid4().hex[:8]}",
            "position_id": pos["position_id"],
            "symbol": symbol,
            "market": pos["market"],
            "side": pos["side"],
            "quantity": qty,
            "entry_price": entry_price,
            "exit_price": round(realized_exit_price, 4),
            "stop_loss": pos["stop_loss"],
            "take_profit": pos["take_profit"],
            "entry_time": pos["entry_time"],
            "exit_time": now_iso,
            "gross_pnl": round(gross_pnl, 2),
            "total_friction": round(total_friction, 2),
            "net_pnl": round(total_net_trade_pnl, 2),
            "return_pct": round((total_net_trade_pnl / (entry_price * qty)) * 100.0, 2),
            "r_multiple": r_multiple,
            "exit_reason": reason,
            "strategy": pos["strategy"],
        }

        self.trade_history.append(trade_record)
        del self.positions[symbol]

        self._recompute_stats()
        self._recompute_equity()
        self.save_state()

        return {
            "success": True,
            "trade": trade_record,
            "balance": round(self.balance, 2),
            "equity": round(self.equity, 2),
        }

    def close_all_positions(self, reason: str = "MANUAL_CLOSE_ALL") -> Dict[str, Any]:
        """Close all open paper positions at their current price."""
        results = []
        for symbol, pos in list(self.positions.items()):
            res = self.close_position(
                symbol=symbol,
                exit_price=pos["current_price"],
                reason=reason,
            )
            results.append({"symbol": symbol, "result": res})
        return {
            "success": True,
            "closed_count": len(results),
            "results": results,
            "balance": round(self.balance, 2),
            "equity": round(self.equity, 2),
        }

    def get_realized_loss_today(self) -> float:
        """Calculate total realized loss for trades closed today (UTC date)."""
        today_date = datetime.now(timezone.utc).date()
        today_losses = 0.0
        for trade in self.trade_history:
            exit_time_str = trade.get("exit_time")
            if not exit_time_str:
                continue
            try:
                if isinstance(exit_time_str, (int, float)):
                    t_date = datetime.fromtimestamp(float(exit_time_str), tz=timezone.utc).date()
                elif "T" in str(exit_time_str):
                    t_date = datetime.fromisoformat(str(exit_time_str).replace("Z", "+00:00")).date()
                else:
                    t_date = datetime.strptime(str(exit_time_str)[:10], "%Y-%m-%d").date()
                if t_date == today_date and trade.get("net_pnl", 0.0) < 0:
                    today_losses += abs(trade["net_pnl"])
            except Exception:
                continue
        return round(today_losses, 2)

    def get_current_open_exposure(self) -> float:
        """Calculate total capital committed to active open positions."""
        return sum(
            float(pos.get("entry_price", 0.0)) * float(pos.get("quantity", 0.0))
            for pos in self.positions.values()
        )

    def emergency_stop(self) -> Dict[str, Any]:
        """Emergency circuit breaker: cancels all pending orders and liquidates all positions."""
        cancelled_orders = 0
        for oid, order in list(self.orders.items()):
            if order.get("status") == "PENDING":
                order["status"] = "CANCELLED"
                cancelled_orders += 1

        liquidated = []
        for symbol, pos in list(self.positions.items()):
            res = self.close_position(
                symbol=symbol,
                exit_price=pos["current_price"],
                reason="EMERGENCY_STOP",
            )
            liquidated.append({"symbol": symbol, "result": res})

        self.save_state()
        return {
            "status": "HALTED",
            "emergency_stop_triggered": True,
            "cancelled_orders": cancelled_orders,
            "liquidated_positions": liquidated,
            "final_balance": round(self.balance, 2),
            "final_equity": round(self.equity, 2),
        }

    def reset_account(
        self,
        confirm: bool = False,
        initial_balance: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Completely reset paper trading account to pristine initial state.

        Requires confirm=True to avoid accidental resets.
        """
        if not confirm:
            return {
                "success": False,
                "reason": "Confirmation required: pass confirm=True to reset paper account.",
            }

        if initial_balance is not None and initial_balance > 0:
            self.initial_balance = float(initial_balance)

        self.balance = self.initial_balance
        self.equity = self.initial_balance
        self.peak_equity = self.initial_balance
        self.max_drawdown_pct = 0.0
        self.positions.clear()
        self.orders.clear()
        self.trade_history.clear()
        self.stats = {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "breakeven_trades": 0,
            "win_rate_pct": 0.0,
            "profit_factor": 0.0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "net_pnl": 0.0,
            "average_r": 0.0,
            "average_win": 0.0,
            "average_loss": 0.0,
            "max_drawdown_pct": 0.0,
        }

        self.save_state()
        return {
            "success": True,
            "message": "Paper trading account successfully reset.",
            "initial_balance": self.initial_balance,
            "currency": self.currency,
        }

    def get_state(self) -> Dict[str, Any]:
        """Return the complete current snapshot of the paper account."""
        self._recompute_equity()
        return {
            "mode": "paper",
            "currency": self.currency,
            "initial_balance": round(self.initial_balance, 2),
            "balance": round(self.balance, 2),
            "equity": round(self.equity, 2),
            "peak_equity": round(self.peak_equity, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "open_positions_count": len(self.positions),
            "positions": self.positions,
            "orders_count": len(self.orders),
            "orders": self.orders,
            "trades_count": len(self.trade_history),
            "trade_history": self.trade_history[-50:],  # return last 50 trades in snapshot
            "stats": self.stats,
        }

    def _recompute_equity(self) -> None:
        """Update equity based on cash balance plus unrealized PnL."""
        unrealized = sum(p.get("unrealized_pnl", 0.0) for p in self.positions.values())
        positions_market_val = sum(p.get("current_price", 0.0) * p.get("quantity", 0.0) for p in self.positions.values())
        self.equity = self.balance + positions_market_val

        if self.equity > self.peak_equity:
            self.peak_equity = self.equity

        if self.peak_equity > 0:
            dd = ((self.peak_equity - self.equity) / self.peak_equity) * 100.0
            if dd > self.max_drawdown_pct:
                self.max_drawdown_pct = dd
        self.stats["max_drawdown_pct"] = round(self.max_drawdown_pct, 2)

    def _recompute_stats(self) -> None:
        """Update aggregate performance stats across closed trades."""
        total = len(self.trade_history)
        if total == 0:
            return

        wins = [t for t in self.trade_history if t["net_pnl"] > 0]
        losses = [t for t in self.trade_history if t["net_pnl"] < 0]
        be = [t for t in self.trade_history if t["net_pnl"] == 0]

        gross_profit = sum(t["net_pnl"] for t in wins)
        gross_loss = abs(sum(t["net_pnl"] for t in losses))
        net_pnl = sum(t["net_pnl"] for t in self.trade_history)

        win_rate = (len(wins) / total) * 100.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
        avg_r = sum(t.get("r_multiple", 0.0) for t in self.trade_history) / total
        avg_win = (gross_profit / len(wins)) if wins else 0.0
        avg_loss = (gross_loss / len(losses)) if losses else 0.0

        self.stats.update({
            "total_trades": total,
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "breakeven_trades": len(be),
            "win_rate_pct": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "net_pnl": round(net_pnl, 2),
            "average_r": round(avg_r, 2),
            "average_win": round(avg_win, 2),
            "average_loss": round(avg_loss, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
        })

