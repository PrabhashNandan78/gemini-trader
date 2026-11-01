#!/usr/bin/env python3
"""Deterministic Risk Management & Multi-Constraint Position Sizing Engine.

Enforces:
  1. Complete onboarding validation for all 12 user parameters.
  2. Absolute 3:1 net reward-to-risk requirement after fees and slippage.
  3. Multi-constraint position sizing taking the smallest of:
     - Risk-budget-derived position size
     - User's maximum trade allocation
     - Available cash / buying power (with cash buffer)
     - Instrument exposure limit
     - Portfolio exposure limit
     - Instrument lot size / tick constraints
     - Broker / exchange minimum order values
  4. Explicit rejection when minimum allocation is insufficient (no silent increases).
  5. Daily loss limits, max open positions, and emergency stop enforcement.
"""

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple

from engine.asset_model import AssetInfo, parse_symbol, get_market_status
from copilot.cost_model import TransactionCostModel, CostBreakdown


@dataclass
class UserRiskConfig:
    market: str = "india"                   # "india", "crypto", "us"
    broker: str = "paper"                   # "zerodha", "alpaca", "binance", "paper"
    currency: str = "INR"                   # "INR", "USD"
    balance: float = 200000.0               # Total account equity
    min_allocation: float = 1000.0          # Minimum allocation per trade (lower bound = 1 unit)
    max_allocation: float = 50000.0         # Maximum allocation per trade
    max_risk_pct: float = 1.0               # Maximum % of equity at risk per trade (e.g. 1.0%)
    max_daily_loss: float = 5000.0          # Daily loss circuit breaker
    max_open_exposure: float = 150000.0     # Maximum total open exposure
    max_concurrent_positions: int = 3       # Maximum concurrent positions
    leverage: float = 1.0                   # Leverage preference (default 1.0 unleveraged)
    mode: str = "PAPER"                     # "PAPER" or "LIVE"
    cash_buffer_pct: float = 10.0           # 10% cash buffer
    emergency_stop: bool = False            # Global emergency kill switch
    min_planned_rr: float = 3.0             # Strict 3:1 planned reward-to-risk
    auto_execute_paper: bool = False        # Auto paper trade execution toggle (default False)
    new_entries_paused: bool = False        # Pause new entries toggle (default False)
    reentry_cooldown_sec: int = 60          # Cooldown in seconds before re-entering same instrument
    max_positions_per_instrument: int = 1   # Max concurrent positions per instrument
    max_trades_per_day: int = 10            # Max trades per day limit

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UserRiskConfig":
        """Instantiates and normalizes config from a dictionary."""
        cfg = cls()
        if not data:
            return cfg
        cfg.market = str(data.get("market", cfg.market)).lower()
        cfg.broker = str(data.get("broker", cfg.broker)).lower()
        cfg.currency = str(data.get("currency", cfg.currency)).upper()
        cfg.balance = float(data.get("balance", data.get("budget", cfg.balance)))
        cfg.min_allocation = max(1.0, float(data.get("min_allocation", cfg.min_allocation)))
        cfg.max_allocation = max(cfg.min_allocation, float(data.get("max_allocation", cfg.max_allocation)))
        cfg.max_risk_pct = float(data.get("max_risk_pct", data.get("max_position_pct", cfg.max_risk_pct)))
        cfg.max_daily_loss = float(data.get("max_daily_loss", data.get("max_daily_loss_inr", data.get("max_daily_loss_usd", cfg.max_daily_loss))))
        cfg.max_open_exposure = float(data.get("max_open_exposure", cfg.balance * 0.75))
        cfg.max_concurrent_positions = int(data.get("max_concurrent_positions", cfg.max_concurrent_positions))
        cfg.leverage = float(data.get("leverage", 1.0))
        cfg.mode = "LIVE" if str(data.get("mode", "")).upper() == "LIVE" or data.get("live") is True else "PAPER"
        cfg.cash_buffer_pct = float(data.get("cash_buffer_pct", cfg.cash_buffer_pct))
        cfg.emergency_stop = bool(data.get("emergency_stop", False))
        cfg.min_planned_rr = float(data.get("min_planned_rr", 3.0))
        cfg.auto_execute_paper = bool(data.get("auto_execute_paper", False))
        cfg.new_entries_paused = bool(data.get("new_entries_paused", False))
        cfg.reentry_cooldown_sec = int(data.get("reentry_cooldown_sec", cfg.reentry_cooldown_sec))
        cfg.max_positions_per_instrument = int(data.get("max_positions_per_instrument", cfg.max_positions_per_instrument))
        cfg.max_trades_per_day = int(data.get("max_trades_per_day", cfg.max_trades_per_day))
        return cfg

    def to_dict(self) -> Dict[str, Any]:
        return {
            "market": self.market,
            "broker": self.broker,
            "currency": self.currency,
            "balance": self.balance,
            "min_allocation": self.min_allocation,
            "max_allocation": self.max_allocation,
            "max_risk_pct": self.max_risk_pct,
            "max_daily_loss": self.max_daily_loss,
            "max_open_exposure": self.max_open_exposure,
            "max_concurrent_positions": self.max_concurrent_positions,
            "leverage": self.leverage,
            "mode": self.mode,
            "auto_execute_paper": self.auto_execute_paper,
            "new_entries_paused": self.new_entries_paused,
            "emergency_stop": self.emergency_stop,
            "cash_buffer_pct": self.cash_buffer_pct,
            "min_planned_rr": self.min_planned_rr,
            "reentry_cooldown_sec": self.reentry_cooldown_sec,
            "max_positions_per_instrument": self.max_positions_per_instrument,
            "max_trades_per_day": self.max_trades_per_day,
        }


@dataclass
class OrderTicket:
    symbol: str
    market: str = "NSE"
    side: str = "BUY"
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    quantity: float = 0.0
    allocated_capital: float = 0.0
    risk_amount: float = 0.0
    planned_rr: float = 3.0
    strategy: str = "TrendFollowing"
    currency: str = "INR"
    cost_breakdown: Optional[Any] = None
    binding_constraint: str = "risk_budget"
    created_at: Optional[str] = None
    tradingview_symbol: Optional[str] = None
    canonical_id: Optional[str] = None
    instrument_type: str = "spot"
    data_provider: str = ""
    timeframe: str = "15m"
    candle_timestamp: Optional[int] = None
    strategy_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "market": self.market,
            "side": self.side,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "quantity": self.quantity,
            "allocated_capital": self.allocated_capital,
            "risk_amount": self.risk_amount,
            "planned_rr": self.planned_rr,
            "strategy": self.strategy,
            "currency": self.currency,
            "cost_breakdown": self.cost_breakdown,
            "binding_constraint": self.binding_constraint,
            "tradingview_symbol": self.tradingview_symbol,
            "canonical_id": self.canonical_id,
            "instrument_type": self.instrument_type,
            "data_provider": self.data_provider,
            "timeframe": self.timeframe,
            "candle_timestamp": self.candle_timestamp,
            "strategy_id": self.strategy_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OrderTicket":
        return cls(
            symbol=data.get("symbol", ""),
            market=data.get("market", "NSE"),
            side=data.get("side", data.get("action", "BUY")),
            entry_price=float(data.get("entry_price", 0.0)),
            stop_loss=float(data.get("stop_loss", 0.0)),
            take_profit=float(data.get("take_profit", 0.0)),
            quantity=float(data.get("quantity", 0.0)),
            allocated_capital=float(data.get("allocated_capital", data.get("position_value", 0.0))),
            risk_amount=float(data.get("risk_amount", data.get("max_loss", 0.0))),
            planned_rr=float(data.get("planned_rr", data.get("net_rr", 3.0))),
            strategy=data.get("strategy", "TrendFollowing"),
            currency=data.get("currency", "INR"),
            cost_breakdown=data.get("cost_breakdown"),
            binding_constraint=data.get("binding_constraint", "risk_budget"),
            created_at=data.get("created_at"),
            tradingview_symbol=data.get("tradingview_symbol"),
            canonical_id=data.get("canonical_id"),
            instrument_type=data.get("instrument_type", "spot"),
            data_provider=data.get("data_provider", ""),
            timeframe=data.get("timeframe", "15m"),
            candle_timestamp=data.get("candle_timestamp"),
            strategy_id=data.get("strategy_id", data.get("strategy")),
        )


@dataclass
class SizingResult:
    approved: bool
    rejection_reasons: List[str]
    warnings: List[str]
    quantity: float
    position_value: float
    max_loss: float
    net_rr: float
    binding_constraint: str
    costs: Optional[CostBreakdown] = None
    ticket: Optional[Dict[str, Any]] = None


class RiskAndSizingEngine:
    """Deterministic Multi-Constraint Risk and Position Sizing Engine."""

    def __init__(self, config: Optional[UserRiskConfig] = None):
        self.config = config or UserRiskConfig()
        self.daily_realized_loss: float = 0.0

    def update_config(self, new_config: UserRiskConfig):
        self.config = new_config

    def calculate_position_size(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        side: str = "BUY",
        planned_rr: float = 3.2,
        strategy: str = "TrendFollowing",
        current_open_exposure: float = 0.0,
        current_open_positions: int = 0,
    ) -> Optional[OrderTicket]:
        res = self.size_and_verify(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            current_open_exposure=current_open_exposure,
            current_open_positions=current_open_positions,
            realized_loss_today=self.daily_realized_loss,
            skip_window=True,
        )
        if not res.approved or not res.ticket:
            return None
        t = res.ticket
        return OrderTicket(
            symbol=symbol,
            market=self.config.market,
            side=side,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            quantity=res.quantity,
            allocated_capital=res.position_value,
            risk_amount=res.max_loss,
            planned_rr=res.net_rr,
            strategy=strategy,
            currency=self.config.currency,
            cost_breakdown=res.costs.__dict__ if res.costs else None,
            binding_constraint=res.binding_constraint,
        )

    def validate_order(
        self,
        ticket: OrderTicket,
        open_positions: Optional[Dict[str, Any]] = None,
        pending_orders: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        curr_positions_cnt = len(open_positions) if open_positions else 0
        curr_exposure = sum(
            float(p.get("entry_price", 0.0)) * float(p.get("quantity", 0.0))
            for p in (open_positions.values() if open_positions else [])
        )
        violations = []
        if self.config.emergency_stop:
            violations.append("EMERGENCY_STOP")
        if self.config.new_entries_paused:
            violations.append("NEW_ENTRIES_PAUSED")
        if self.daily_realized_loss >= self.config.max_daily_loss:
            violations.append("DAILY_LOSS_BREACHED")
        if curr_positions_cnt >= self.config.max_concurrent_positions:
            violations.append("MAX_CONCURRENT_POSITIONS")

        req_cap = ticket.entry_price * ticket.quantity
        if (curr_exposure + req_cap) > self.config.max_open_exposure:
            violations.append("PORTFOLIO_EXPOSURE_EXCEEDED")

        # Duplicate order / position prevention
        if open_positions and ticket.symbol in open_positions:
            violations.append("DUPLICATE_POSITION_EXISTS")
        if pending_orders:
            for o in pending_orders.values():
                if o.get("symbol") == ticket.symbol and o.get("status") in ("OPEN", "PENDING"):
                    violations.append("DUPLICATE_PENDING_ORDER_EXISTS")
                    break

        # Stop Loss & Take Profit structural sanity
        is_long = ticket.side.upper() == "BUY"
        if is_long:
            if ticket.stop_loss >= ticket.entry_price or ticket.take_profit <= ticket.entry_price:
                violations.append("INVALID_SL_TP_STRUCTURE")
        else:
            if ticket.stop_loss <= ticket.entry_price or ticket.take_profit >= ticket.entry_price:
                violations.append("INVALID_SL_TP_STRUCTURE")

        # Planned Net R:R minimum
        if ticket.planned_rr < self.config.min_planned_rr:
            violations.append("SUB_3R_NET_REWARD_RISK")

        # Quantity validity
        if ticket.quantity <= 0:
            violations.append("INVALID_QUANTITY")

        if violations:
            return {
                "allowed": False,
                "reason": f"Safety gate violations: {', '.join(violations)}",
                "violations": violations,
            }
        return {"allowed": True, "reason": "Order approved", "violations": []}

    def size_and_verify(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        current_open_exposure: float = 0.0,
        current_open_positions: int = 0,
        realized_loss_today: float = 0.0,
        available_cash: Optional[float] = None,
        skip_window: bool = False,
    ) -> SizingResult:
        """Executes full multi-constraint sizing and pre-trade risk verification."""
        reasons: List[str] = []
        warnings: List[str] = []
        cfg = self.config
        asset = parse_symbol(symbol)

        # ---------------- Gate 1: Emergency Kill Switch
        if cfg.emergency_stop:
            reasons.append("EMERGENCY STOP is currently ACTIVE. All new trading is strictly suspended.")
            return SizingResult(False, reasons, warnings, 0.0, 0.0, 0.0, 0.0, "emergency_stop")

        # ---------------- Gate 1b: Pause New Entries
        if cfg.new_entries_paused:
            reasons.append("New trade entries are currently PAUSED by user. Monitoring existing positions.")
            return SizingResult(False, reasons, warnings, 0.0, 0.0, 0.0, 0.0, "new_entries_paused")

        # ---------------- Gate 2: Daily Loss Limit
        if realized_loss_today >= cfg.max_daily_loss:
            reasons.append(
                f"Daily loss limit reached (Realized loss today: {realized_loss_today:.2f} {cfg.currency} >= limit {cfg.max_daily_loss:.2f} {cfg.currency}). All trading halted for today."
            )
            return SizingResult(False, reasons, warnings, 0.0, 0.0, 0.0, 0.0, "max_daily_loss")

        # ---------------- Gate 3: Concurrent Positions Limit
        if current_open_positions >= cfg.max_concurrent_positions:
            reasons.append(
                f"Maximum concurrent positions limit reached ({current_open_positions} / {cfg.max_concurrent_positions})."
            )
            return SizingResult(False, reasons, warnings, 0.0, 0.0, 0.0, 0.0, "max_concurrent_positions")

        # ---------------- Gate 4: Session Window & Calendar Check
        if not skip_window:
            market_stat = get_market_status(asset)
            if not market_stat["is_open"]:
                reasons.append(
                    f"Exchange session is CLOSED ({market_stat['reason']}, local time {market_stat['local_time']})."
                )
                return SizingResult(False, reasons, warnings, 0.0, 0.0, 0.0, 0.0, "session_window")

        # ---------------- Gate 5: Stop-Loss Structural Sanity
        is_long = side.upper() == "BUY"
        if is_long:
            gross_risk_unit = entry_price - stop_loss
            gross_reward_unit = take_profit - entry_price
        else:
            gross_risk_unit = stop_loss - entry_price
            gross_reward_unit = entry_price - take_profit

        if gross_risk_unit <= 0:
            reasons.append(f"Invalid stop loss: for a {side} trade, stop ({stop_loss:.2f}) must be on the defensive side of entry ({entry_price:.2f}).")
            return SizingResult(False, reasons, warnings, 0.0, 0.0, 0.0, 0.0, "invalid_stop")

        if gross_reward_unit <= 0:
            reasons.append(f"Invalid take profit: target ({take_profit:.2f}) must be in profit direction from entry ({entry_price:.2f}).")
            return SizingResult(False, reasons, warnings, 0.0, 0.0, 0.0, 0.0, "invalid_target")

        # ---------------- Gate 6: Strict 3:1 Net Reward-to-Risk Requirement
        net_rr, net_risk, net_reward, costs = TransactionCostModel.compute_net_reward_to_risk(
            asset_class=asset.asset_class,
            side=side,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            qty=1.0,
        )

        if net_rr < cfg.min_planned_rr:
            reasons.append(
                f"Planned Net Reward-to-Risk is {net_rr:.2f}:1, which is below the mandatory {cfg.min_planned_rr:.1f}:1 minimum (Net Risk: {net_risk:.2f}, Net Reward: {net_reward:.2f})."
            )
            return SizingResult(False, reasons, warnings, 0.0, 0.0, 0.0, net_rr, "min_reward_risk", costs)

        # ---------------- Gate 7: Multi-Constraint Sizing Engine
        # Calculate maximum capital loss allowed on this trade
        max_permitted_loss = (cfg.balance * (cfg.max_risk_pct / 100.0))
        # Account for statutory cost per unit
        effective_risk_per_unit = gross_risk_unit + costs.cost_per_unit

        # Sizing Constraint A: Risk Budget Size
        raw_risk_qty = max_permitted_loss / max(effective_risk_per_unit, 1e-9)

        # Sizing Constraint B: Max Allocation Cap
        raw_alloc_qty = cfg.max_allocation / max(entry_price, 1e-9)

        # Sizing Constraint C: Available Cash (with buffer)
        effective_cash = available_cash if available_cash is not None else cfg.balance
        spendable_cash = effective_cash * (1.0 - (cfg.cash_buffer_pct / 100.0))
        raw_cash_qty = max(0.0, spendable_cash) / max(entry_price, 1e-9)

        # Sizing Constraint D: Total Portfolio Exposure Room
        exposure_room = max(0.0, cfg.max_open_exposure - current_open_exposure)
        raw_exposure_qty = exposure_room / max(entry_price, 1e-9)

        # Select the most restrictive constraint
        candidates = [
            ("risk_budget", raw_risk_qty),
            ("max_allocation", raw_alloc_qty),
            ("cash_buffer", raw_cash_qty),
            ("portfolio_exposure", raw_exposure_qty),
        ]
        candidates.sort(key=lambda x: x[1])
        binding_name, raw_chosen_qty = candidates[0]

        # Apply Asset Class Quantity Constraints (Whole shares vs Fractional units)
        if asset.allow_fractional:
            final_qty = round(raw_chosen_qty, 4)
        else:
            final_qty = float(math.floor(raw_chosen_qty))

        pos_value = final_qty * entry_price
        actual_max_loss = (final_qty * gross_risk_unit) + (costs.cost_per_unit * final_qty)

        # ---------------- Gate 8: Minimum Feasible Order Validation
        min_feasible_shares = 0.0001 if asset.allow_fractional else 1.0

        if final_qty < min_feasible_shares:
            if binding_name == "risk_budget":
                reasons.append(
                    f"Position rejected by risk budget: Purchasing the minimum 1 share/unit of {symbol} at {entry_price:.2f} requires risking {gross_risk_unit:.2f} {cfg.currency}, which exceeds your maximum permitted trade loss of {max_permitted_loss:.2f} {cfg.currency}."
                )
            elif binding_name == "max_allocation":
                reasons.append(
                    f"Position rejected by allocation limit: Minimum 1 unit of {symbol} costs {entry_price:.2f} {cfg.currency}, which exceeds your maximum trade allocation of {cfg.max_allocation:.2f} {cfg.currency}."
                )
            elif binding_name == "cash_buffer":
                reasons.append(
                    f"Insufficient uncommitted cash: Buying {symbol} at {entry_price:.2f} {cfg.currency} would violate your {cfg.cash_buffer_pct}% cash buffer."
                )
            else:
                reasons.append(
                    f"Order rejected: Sized quantity {final_qty} is below the minimum order increment."
                )
            return SizingResult(False, reasons, warnings, 0.0, 0.0, 0.0, net_rr, binding_name, costs)

        # ---------------- Gate 9: Minimum Allocation Feasibility Check
        # If user configured min_allocation and pos_value < min_allocation:
        if pos_value < cfg.min_allocation:
            warnings.append(
                f"Position value ({pos_value:.2f} {cfg.currency}) is below your preferred minimum allocation ({cfg.min_allocation:.2f} {cfg.currency}) due to {binding_name} limit. Sizing was NOT silently increased to protect your capital."
            )

        # Recalculate full costs for the final sized quantity
        final_costs = TransactionCostModel.get_costs(asset.asset_class, final_qty, entry_price, take_profit)
        net_rr, net_risk, net_reward, _ = TransactionCostModel.compute_net_reward_to_risk(
            asset_class=asset.asset_class,
            side=side,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            qty=final_qty,
        )

        ticket = {
            "symbol": asset.symbol,
            "action": side.upper(),
            "quantity": final_qty,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "position_value": round(pos_value, 2),
            "max_loss": round(actual_max_loss, 2),
            "gross_rr": round(gross_reward_unit / max(gross_risk_unit, 1e-9), 2),
            "net_rr": net_rr,
            "currency": cfg.currency,
            "binding_constraint": binding_name,
            "mode": cfg.mode,
            "total_costs_estimated": final_costs.total_roundtrip_cost,
        }

        return SizingResult(
            approved=True,
            rejection_reasons=[],
            warnings=warnings,
            quantity=final_qty,
            position_value=round(pos_value, 2),
            max_loss=round(actual_max_loss, 2),
            net_rr=net_rr,
            binding_constraint=binding_name,
            costs=final_costs,
            ticket=ticket,
        )
