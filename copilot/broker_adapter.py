"""Broker Adapter Interface and Implementations for Paper, Zerodha Kite, and Alpaca.

Provides:
1. BaseBrokerAdapter: Abstract base protocol for order lifecycle and risk safety.
2. PaperBrokerAdapter: Direct integration with PaperTradingEngine.
3. ZerodhaBrokerAdapter: Kite Connect protocol implementation for Indian Equities (NSE).
4. AlpacaBrokerAdapter: Alpaca Markets protocol for US Equities and Crypto.
5. Live Safety Gate: Prohibits unconfirmed live execution; enforces emergency kill switch.
"""

import abc
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from copilot.cost_model import TransactionCostModel
from copilot.paper_engine import PaperTradingEngine
from copilot.risk_engine import OrderTicket, UserRiskConfig


class BaseBrokerAdapter(abc.ABC):
    """Abstract base broker adapter defining the standard lifecycle methods."""

    def __init__(self, mode: str = "paper", user_config: Optional[UserRiskConfig] = None):
        self.mode = mode.lower()
        self.user_config = user_config
        self.emergency_halted: bool = False
        self._last_order_timestamp: float = 0.0

    @abc.abstractmethod
    def authenticate(self) -> Dict[str, Any]:
        """Authenticate or verify connectivity with the broker.

        Returns dict with status, user_id, message.
        """
        pass

    @abc.abstractmethod
    def get_account_balance(self) -> Dict[str, Any]:
        """Retrieve account balance, cash, equity, and currency."""
        pass

    @abc.abstractmethod
    def get_positions(self) -> List[Dict[str, Any]]:
        """Retrieve active open positions."""
        pass

    @abc.abstractmethod
    def get_orders(self, status: str = "OPEN") -> List[Dict[str, Any]]:
        """Retrieve orders by status."""
        pass

    @abc.abstractmethod
    def place_order(self, ticket: OrderTicket, confirm_live: bool = False) -> Dict[str, Any]:
        """Place an order with planned SL and TP targets."""
        pass

    @abc.abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an individual pending order."""
        pass

    @abc.abstractmethod
    def cancel_all_orders(self) -> int:
        """Cancel all pending orders."""
        pass

    @abc.abstractmethod
    def emergency_stop(self) -> Dict[str, Any]:
        """Halt trading, cancel all orders, and liquidate all positions."""
        pass

    @abc.abstractmethod
    def reconcile_orders(self) -> Dict[str, Any]:
        """Reconcile internal engine state with broker order books."""
        pass


class PaperBrokerAdapter(BaseBrokerAdapter):
    """Paper trading broker adapter wrapping the PaperTradingEngine."""

    def __init__(self, paper_engine: Optional[PaperTradingEngine] = None, user_config: Optional[UserRiskConfig] = None):
        super().__init__(mode="paper", user_config=user_config)
        self.engine = paper_engine or PaperTradingEngine(risk_config=user_config)

    def authenticate(self) -> Dict[str, Any]:
        return {
            "authenticated": True,
            "broker": "PAPER_SIMULATOR",
            "mode": "paper",
            "message": "Paper trading engine online and ready.",
        }

    def get_account_balance(self) -> Dict[str, Any]:
        state = self.engine.get_state()
        return {
            "balance": state["balance"],
            "equity": state["equity"],
            "peak_equity": state["peak_equity"],
            "max_drawdown_pct": state["max_drawdown_pct"],
            "currency": state["currency"],
            "mode": "paper",
        }

    def get_positions(self) -> List[Dict[str, Any]]:
        return list(self.engine.positions.values())

    def get_orders(self, status: str = "OPEN") -> List[Dict[str, Any]]:
        if status.upper() == "OPEN" or status.upper() == "PENDING":
            return [o for o in self.engine.orders.values() if o.get("status") == "PENDING"]
        return list(self.engine.orders.values())

    def place_order(self, ticket: OrderTicket, confirm_live: bool = False) -> Dict[str, Any]:
        if self.emergency_halted:
            return {
                "success": False,
                "reason": "Emergency halt is active. Reset required before trading.",
            }
        return self.engine.place_order(ticket)

    def cancel_order(self, order_id: str) -> bool:
        if order_id in self.engine.orders:
            self.engine.orders[order_id]["status"] = "CANCELLED"
            self.engine.save_state()
            return True
        return False

    def cancel_all_orders(self) -> int:
        count = 0
        for o in self.engine.orders.values():
            if o.get("status") == "PENDING":
                o["status"] = "CANCELLED"
                count += 1
        if count > 0:
            self.engine.save_state()
        return count

    def emergency_stop(self) -> Dict[str, Any]:
        self.emergency_halted = True
        return self.engine.emergency_stop()

    def reconcile_orders(self) -> Dict[str, Any]:
        state = self.engine.get_state()
        return {
            "reconciled": True,
            "positions_count": len(state["positions"]),
            "orders_count": len(state["orders"]),
            "balance": state["balance"],
            "equity": state["equity"],
        }


class ZerodhaBrokerAdapter(BaseBrokerAdapter):
    """Zerodha Kite Connect REST API adapter for Indian Equities (NSE).

    Adheres strictly to Kite Connect v3 endpoints:
    - Base: https://api.kite.trade
    - Headers: X-Kite-Version: 3, Authorization: token {api_key}:{access_token}
    - Live safety guard: requires explicit confirm_live=True flag and authenticated token.
    """

    BASE_URL = "https://api.kite.trade"

    def __init__(
        self,
        api_key: Optional[str] = None,
        access_token: Optional[str] = None,
        mode: str = "paper",
        user_config: Optional[UserRiskConfig] = None,
    ):
        super().__init__(mode=mode, user_config=user_config)
        self.api_key = api_key or os.environ.get("ZERODHA_API_KEY", "")
        self.access_token = access_token or os.environ.get("ZERODHA_ACCESS_TOKEN", "")
        self.paper_fallback = PaperBrokerAdapter(user_config=user_config)

    def _get_headers(self) -> Dict[str, str]:
        return {
            "X-Kite-Version": "3",
            "Authorization": f"token {self.api_key}:{self.access_token}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

    def _http_request(self, endpoint: str, data: Optional[Dict[str, Any]] = None, method: str = "GET") -> Dict[str, Any]:
        """Make an authenticated HTTP request to Kite Connect API."""
        url = f"{self.BASE_URL}{endpoint}"
        headers = self._get_headers()

        encoded_data = None
        if data:
            encoded_data = urllib.parse.urlencode(data).encode("utf-8")

        req = urllib.request.Request(url, data=encoded_data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
                return payload
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8")
            try:
                err_json = json.loads(err_body)
                return {"status": "error", "message": err_json.get("message", str(e)), "code": e.code}
            except Exception:
                return {"status": "error", "message": str(e), "code": e.code}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def authenticate(self) -> Dict[str, Any]:
        if not self.api_key or not self.access_token:
            return {
                "authenticated": False,
                "broker": "ZERODHA_KITE",
                "mode": self.mode,
                "message": "Missing ZERODHA_API_KEY or ZERODHA_ACCESS_TOKEN. Live orders are blocked.",
            }

        res = self._http_request("/user/profile")
        if res.get("status") == "success":
            user_data = res.get("data", {})
            return {
                "authenticated": True,
                "broker": "ZERODHA_KITE",
                "user_id": user_data.get("user_id"),
                "user_name": user_data.get("user_name"),
                "mode": self.mode,
            }
        return {
            "authenticated": False,
            "broker": "ZERODHA_KITE",
            "message": res.get("message", "Authentication failed"),
        }

    def get_account_balance(self) -> Dict[str, Any]:
        if self.mode == "paper" or not self.access_token:
            return self.paper_fallback.get_account_balance()

        res = self._http_request("/user/margins/equity")
        if res.get("status") == "success":
            data = res.get("data", {})
            available_cash = float(data.get("available", {}).get("cash", 0.0))
            net = float(data.get("net", available_cash))
            return {
                "balance": available_cash,
                "equity": net,
                "currency": "INR",
                "mode": "live",
            }
        return {"balance": 0.0, "equity": 0.0, "currency": "INR", "error": res.get("message")}

    def get_positions(self) -> List[Dict[str, Any]]:
        if self.mode == "paper" or not self.access_token:
            return self.paper_fallback.get_positions()

        res = self._http_request("/portfolio/positions")
        if res.get("status") == "success":
            net_positions = res.get("data", {}).get("net", [])
            positions = []
            for p in net_positions:
                if p.get("quantity", 0) != 0:
                    positions.append({
                        "symbol": p.get("tradingsymbol"),
                        "quantity": p.get("quantity"),
                        "entry_price": p.get("average_price"),
                        "current_price": p.get("last_price"),
                        "unrealized_pnl": p.get("m2m"),
                        "side": "BUY" if p.get("quantity", 0) > 0 else "SELL",
                    })
            return positions
        return []

    def get_orders(self, status: str = "OPEN") -> List[Dict[str, Any]]:
        if self.mode == "paper" or not self.access_token:
            return self.paper_fallback.get_orders(status)

        res = self._http_request("/orders")
        if res.get("status") == "success":
            all_orders = res.get("data", [])
            if status.upper() == "OPEN":
                return [o for o in all_orders if o.get("status") in ("OPEN", "TRIGGER PENDING")]
            return all_orders
        return []

    def place_order(self, ticket: OrderTicket, confirm_live: bool = False) -> Dict[str, Any]:
        """Place order on Zerodha Kite.

        Safety Gate: If mode is live, confirm_live MUST be explicitly True.
        """
        if self.emergency_halted:
            return {
                "success": False,
                "reason": "Emergency halt active. Trading blocked.",
            }

        if self.mode == "paper":
            return self.paper_fallback.place_order(ticket)

        # LIVE SAFETY GATE
        if not confirm_live:
            return {
                "success": False,
                "reason": "LIVE ORDER BLOCKED: explicit confirmation required (confirm_live=True).",
                "safety_gate": "FAILED_CONFIRMATION",
            }

        auth = self.authenticate()
        if not auth.get("authenticated"):
            return {
                "success": False,
                "reason": f"Live order rejected: broker unauthenticated ({auth.get('message')})",
                "safety_gate": "UNAUTHENTICATED",
            }

        # Map symbol (e.g. RELIANCE.NS -> RELIANCE)
        tradingsymbol = ticket.symbol.replace(".NS", "").replace(".BO", "")
        variety = "regular"

        order_payload = {
            "tradingsymbol": tradingsymbol,
            "exchange": "NSE",
            "transaction_type": ticket.side.upper(),
            "order_type": "MARKET",
            "quantity": int(ticket.quantity),
            "product": "MIS",  # Intraday
            "validity": "DAY",
            "tag": "copilot3r",
        }

        res = self._http_request(f"/orders/{variety}", data=order_payload, method="POST")
        if res.get("status") == "success":
            order_id = res.get("data", {}).get("order_id")
            return {
                "success": True,
                "order_id": order_id,
                "broker": "ZERODHA_KITE",
                "mode": "live",
                "symbol": ticket.symbol,
                "quantity": int(ticket.quantity),
                "planned_sl": ticket.stop_loss,
                "planned_tp": ticket.take_profit,
            }
        return {
            "success": False,
            "reason": res.get("message", "Order placement failed on Zerodha"),
            "raw_response": res,
        }

    def cancel_order(self, order_id: str) -> bool:
        if self.mode == "paper" or not self.access_token:
            return self.paper_fallback.cancel_order(order_id)
        res = self._http_request(f"/orders/regular/{order_id}", method="DELETE")
        return res.get("status") == "success"

    def cancel_all_orders(self) -> int:
        if self.mode == "paper" or not self.access_token:
            return self.paper_fallback.cancel_all_orders()
        open_orders = self.get_orders(status="OPEN")
        count = 0
        for o in open_orders:
            oid = o.get("order_id")
            if oid and self.cancel_order(oid):
                count += 1
        return count

    def emergency_stop(self) -> Dict[str, Any]:
        """Liquidate all live Kite positions and cancel open orders immediately."""
        self.emergency_halted = True
        cancelled = self.cancel_all_orders()
        positions = self.get_positions()
        closed_positions = []

        if self.mode == "live" and self.access_token:
            for p in positions:
                sym = p.get("symbol")
                qty = abs(p.get("quantity", 0))
                side = "SELL" if p.get("quantity", 0) > 0 else "BUY"
                if qty > 0:
                    payload = {
                        "tradingsymbol": sym,
                        "exchange": "NSE",
                        "transaction_type": side,
                        "order_type": "MARKET",
                        "quantity": qty,
                        "product": "MIS",
                        "validity": "DAY",
                        "tag": "emerg_halt",
                    }
                    self._http_request("/orders/regular", data=payload, method="POST")
                    closed_positions.append({"symbol": sym, "quantity": qty, "side": side})

        return {
            "status": "HALTED",
            "emergency_stop": True,
            "cancelled_orders": cancelled,
            "closed_positions": closed_positions,
        }

    def reconcile_orders(self) -> Dict[str, Any]:
        orders = self.get_orders("ALL")
        positions = self.get_positions()
        return {
            "broker": "ZERODHA_KITE",
            "mode": self.mode,
            "open_orders": len([o for o in orders if o.get("status") in ("OPEN", "TRIGGER PENDING")]),
            "open_positions": len(positions),
            "reconciled": True,
        }


class AlpacaBrokerAdapter(BaseBrokerAdapter):
    """Alpaca Markets REST API adapter for US Equities and Crypto.

    Supports native Bracket Orders (entry + take-profit + stop-loss) on Alpaca.
    Uses separate endpoints for Paper vs Live:
    - Paper: https://paper-api.alpaca.markets
    - Live:  https://api.alpaca.markets
    """

    PAPER_URL = "https://paper-api.alpaca.markets"
    LIVE_URL = "https://api.alpaca.markets"

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        mode: str = "paper",
        user_config: Optional[UserRiskConfig] = None,
    ):
        super().__init__(mode=mode, user_config=user_config)
        self.api_key = api_key or os.environ.get("APCA_API_KEY_ID", "")
        self.secret_key = secret_key or os.environ.get("APCA_API_SECRET_KEY", "")
        self.base_url = self.LIVE_URL if self.mode == "live" else self.PAPER_URL
        self.paper_fallback = PaperBrokerAdapter(user_config=user_config)

    def _get_headers(self) -> Dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Content-Type": "application/json",
        }

    def _http_request(self, endpoint: str, data: Optional[Dict[str, Any]] = None, method: str = "GET") -> Dict[str, Any]:
        url = f"{self.base_url}{endpoint}"
        headers = self._get_headers()
        encoded = json.dumps(data).encode("utf-8") if data else None

        req = urllib.request.Request(url, data=encoded, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8")
            try:
                return json.loads(err_body)
            except Exception:
                return {"message": str(e), "code": e.code}
        except Exception as e:
            return {"message": str(e)}

    def authenticate(self) -> Dict[str, Any]:
        if not self.api_key or not self.secret_key:
            return {
                "authenticated": False,
                "broker": "ALPACA",
                "mode": self.mode,
                "message": "Missing APCA_API_KEY_ID or APCA_API_SECRET_KEY.",
            }
        res = self._http_request("/v2/account")
        if "id" in res:
            return {
                "authenticated": True,
                "broker": "ALPACA",
                "account_id": res.get("id"),
                "status": res.get("status"),
                "mode": self.mode,
            }
        return {
            "authenticated": False,
            "broker": "ALPACA",
            "message": res.get("message", "Alpaca authentication failed"),
        }

    def get_account_balance(self) -> Dict[str, Any]:
        if not self.api_key:
            return self.paper_fallback.get_account_balance()
        res = self._http_request("/v2/account")
        if "cash" in res:
            return {
                "balance": float(res.get("cash", 0.0)),
                "equity": float(res.get("equity", 0.0)),
                "buying_power": float(res.get("buying_power", 0.0)),
                "currency": res.get("currency", "USD"),
                "mode": self.mode,
            }
        return self.paper_fallback.get_account_balance()

    def get_positions(self) -> List[Dict[str, Any]]:
        if not self.api_key:
            return self.paper_fallback.get_positions()
        res = self._http_request("/v2/positions")
        if isinstance(res, list):
            positions = []
            for p in res:
                positions.append({
                    "symbol": p.get("symbol"),
                    "quantity": float(p.get("qty", 0)),
                    "entry_price": float(p.get("avg_entry_price", 0)),
                    "current_price": float(p.get("current_price", 0)),
                    "unrealized_pnl": float(p.get("unrealized_pl", 0)),
                    "side": p.get("side", "long").upper(),
                })
            return positions
        return []

    def get_orders(self, status: str = "OPEN") -> List[Dict[str, Any]]:
        if not self.api_key:
            return self.paper_fallback.get_orders(status)
        alpaca_status = "open" if status.upper() == "OPEN" else "all"
        res = self._http_request(f"/v2/orders?status={alpaca_status}&limit=50")
        if isinstance(res, list):
            return res
        return []

    def place_order(self, ticket: OrderTicket, confirm_live: bool = False) -> Dict[str, Any]:
        """Place native bracket order on Alpaca (includes entry, TP, and SL)."""
        if self.emergency_halted:
            return {"success": False, "reason": "Emergency halt active."}

        if self.mode == "live" and not confirm_live:
            return {
                "success": False,
                "reason": "LIVE ORDER BLOCKED: explicit confirmation required (confirm_live=True).",
                "safety_gate": "FAILED_CONFIRMATION",
            }

        if not self.api_key or not self.secret_key:
            return self.paper_fallback.place_order(ticket)

        # Alpaca bracket order
        symbol = ticket.symbol.replace("-USD", "/USD") if "/" not in ticket.symbol and "-USD" in ticket.symbol else ticket.symbol
        order_payload = {
            "symbol": symbol,
            "qty": ticket.quantity,
            "side": ticket.side.lower(),
            "type": "market",
            "time_in_force": "day",
            "order_class": "bracket",
            "take_profit": {"limit_price": round(ticket.take_profit, 2)},
            "stop_loss": {"stop_price": round(ticket.stop_loss, 2)},
        }

        res = self._http_request("/v2/orders", data=order_payload, method="POST")
        if "id" in res:
            return {
                "success": True,
                "order_id": res.get("id"),
                "broker": "ALPACA",
                "mode": self.mode,
                "symbol": ticket.symbol,
                "quantity": ticket.quantity,
                "status": res.get("status"),
            }
        return {
            "success": False,
            "reason": res.get("message", "Alpaca order placement failed"),
            "raw_response": res,
        }

    def cancel_order(self, order_id: str) -> bool:
        if not self.api_key:
            return self.paper_fallback.cancel_order(order_id)
        res = self._http_request(f"/v2/orders/{order_id}", method="DELETE")
        return "code" not in res

    def cancel_all_orders(self) -> int:
        if not self.api_key:
            return self.paper_fallback.cancel_all_orders()
        res = self._http_request("/v2/orders", method="DELETE")
        return len(res) if isinstance(res, list) else 0

    def emergency_stop(self) -> Dict[str, Any]:
        self.emergency_halted = True
        if not self.api_key:
            return self.paper_fallback.emergency_stop()
        # Cancel all orders
        self._http_request("/v2/orders", method="DELETE")
        # Close all positions
        res = self._http_request("/v2/positions", method="DELETE")
        return {
            "status": "HALTED",
            "emergency_stop": True,
            "closed_positions": res if isinstance(res, list) else [],
        }

    def reconcile_orders(self) -> Dict[str, Any]:
        orders = self.get_orders("ALL")
        positions = self.get_positions()
        return {
            "broker": "ALPACA",
            "mode": self.mode,
            "open_orders": len([o for o in orders if o.get("status") == "open"]),
            "open_positions": len(positions),
            "reconciled": True,
        }


def create_broker_adapter(
    broker_name: str,
    mode: str = "paper",
    user_config: Optional[UserRiskConfig] = None,
    paper_engine: Optional[PaperTradingEngine] = None,
) -> BaseBrokerAdapter:
    """Factory creating the appropriate broker adapter instance."""
    name = broker_name.upper()
    if name in ("ZERODHA", "KITE", "NSE"):
        return ZerodhaBrokerAdapter(mode=mode, user_config=user_config)
    elif name in ("ALPACA", "US", "CRYPTO"):
        return AlpacaBrokerAdapter(mode=mode, user_config=user_config)
    else:
        return PaperBrokerAdapter(paper_engine=paper_engine, user_config=user_config)
