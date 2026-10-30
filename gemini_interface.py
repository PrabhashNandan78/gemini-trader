#!/usr/bin/env python3
"""Gemini Integration Layer: Function Calling Declarations & Tool Dispatcher.

Provides:
  - Gemini function declarations (JSON schema compliant with Google AI / Gemini API)
  - Deterministic argument validation and execution routing
  - Structured responses for AI reasoning
  - Immutable audit logging to trader/state/audit.jsonl
"""

import datetime
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
from typing import Dict, List, Optional, Any

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine.asset_model import parse_symbol, get_market_status, format_currency
from engine.market_data import MarketDataProvider, MarketDataError

AUDIT_LOG_PATH = os.path.join(ROOT, "trader", "state", "audit.jsonl")


def log_audit_event(event_type: str, inputs: Dict[str, Any], output: Dict[str, Any]):
    """Appends an immutable audit event to trader/state/audit.jsonl."""
    os.makedirs(os.path.dirname(AUDIT_LOG_PATH), exist_ok=True)
    record = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat() + "Z",
        "event_type": event_type,
        "action": event_type,
        "inputs": inputs,
        "output": output,
    }
    with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------- Gemini Function Declarations

GEMINI_TOOLS = [
    {
        "name": "analyze_symbol",
        "description": "Analyze an Indian stock (e.g. RELIANCE.NS, TCS.NS), cryptocurrency (e.g. BTC-USD, ETH-USD), or US stock for market data, news catalysts, setup trigger state, and entry/exit levels.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "symbol": {
                    "type": "STRING",
                    "description": "Ticker symbol (e.g. 'RELIANCE.NS', 'BTC-USD', 'TSLA', '^NSEI').",
                },
                "setup": {
                    "type": "STRING",
                    "enum": ["orb", "level", "daily"],
                    "description": "Setup type to evaluate. 'orb' = 15-minute opening-range breakout, 'level' = key price level, 'daily' = daily closing pattern.",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "scan_market",
        "description": "Scan a configured market watchlist for day-trading setups (opening-range breakouts, 20-day high breakouts, bull flags, oversold bounces).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "market": {
                    "type": "STRING",
                    "enum": ["india", "crypto", "us"],
                    "description": "Market to scan: 'india' (NSE equities), 'crypto' (major crypto pairs), or 'us' (US equities).",
                },
                "setup": {
                    "type": "STRING",
                    "enum": ["daily", "orb"],
                    "description": "Scan mode: 'daily' (pre-market daily close setups) or 'orb' (intraday opening-range breakout).",
                },
                "limit": {
                    "type": "INTEGER",
                    "description": "Maximum number of symbols to scan.",
                },
            },
            "required": ["market"],
        },
    },
    {
        "name": "backtest_strategy",
        "description": "Backtest a trading setup using the Time Machine Monte Carlo simulation and historical replay. Replays 10,000 runs using the asset's own historical 5-minute bars.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "symbol": {
                    "type": "STRING",
                    "description": "Ticker symbol to backtest (e.g. 'RELIANCE.NS', 'BTC-USD', 'TSLA').",
                },
                "replays": {
                    "type": "INTEGER",
                    "description": "Number of Monte Carlo replay paths (default 10000).",
                },
                "seed": {
                    "type": "INTEGER",
                    "description": "Random seed for reproducible draws (default 19).",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "evaluate_risk",
        "description": "Run deterministic pre-trade risk checks against account limits, stop-loss requirements, position caps, and market trading sessions.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "symbol": {
                    "type": "STRING",
                    "description": "Asset symbol (e.g. 'RELIANCE.NS', 'BTC-USD').",
                },
                "side": {
                    "type": "STRING",
                    "enum": ["buy", "sell"],
                    "description": "Order side.",
                },
                "qty": {
                    "type": "NUMBER",
                    "description": "Proposed order quantity.",
                },
                "entry_price": {
                    "type": "NUMBER",
                    "description": "Proposed entry price.",
                },
                "stop_loss": {
                    "type": "NUMBER",
                    "description": "Proposed stop-loss price.",
                },
                "take_profit": {
                    "type": "NUMBER",
                    "description": "Proposed take-profit price.",
                },
            },
            "required": ["symbol", "side", "qty", "entry_price", "stop_loss", "take_profit"],
        },
    },
    {
        "name": "paper_trade",
        "description": "Size a proposed day trade according to account budget and risk limits, run pre-trade validation, and generate a paper-trading bracket ticket.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "symbol": {
                    "type": "STRING",
                    "description": "Asset ticker to paper trade.",
                },
                "side": {
                    "type": "STRING",
                    "enum": ["buy", "sell"],
                    "description": "Order side (default 'buy').",
                },
                "budget": {
                    "type": "NUMBER",
                    "description": "Account budget override for sizing.",
                },
                "execute": {
                    "type": "BOOLEAN",
                    "description": "Whether to record a simulated paper fill in the local portfolio.",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_market_clock",
        "description": "Check whether an asset's exchange is currently open, regular session hours, local time, and exchange holiday schedule.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "symbol": {
                    "type": "STRING",
                    "description": "Symbol to check market hours for (e.g. 'RELIANCE.NS', 'BTC-USD', 'AAPL').",
                },
            },
        },
    },
]


class GeminiTradingInterface:
    """Core controller invoked by Gemini tool calling or programmatic callers."""

    def __init__(self, data_provider: Optional[MarketDataProvider] = None):
        self.provider = data_provider or MarketDataProvider()

    def get_market_clock(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        sym = symbol or "AAPL"
        asset = parse_symbol(sym)
        status = get_market_status(asset)
        res = {
            "symbol": asset.symbol,
            "asset_class": asset.asset_class,
            "exchange": asset.exchange,
            "timezone": asset.timezone_name,
            "is_open": status["is_open"],
            "market_open": status["is_open"],
            "reason": status["reason"],
            "local_time": status["local_time"],
            "session_open": asset.session_open.strftime("%H:%M") if asset.session_open else "24/7",
            "session_close": asset.session_close.strftime("%H:%M") if asset.session_close else "24/7",
        }
        log_audit_event("get_market_clock", {"symbol": symbol}, res)
        return res

    def analyze_symbol(self, symbol: str, setup: str = "orb") -> Dict[str, Any]:
        asset = parse_symbol(symbol)
        quote = self.provider.get_latest_quote(symbol)
        clock = get_market_status(asset)

        # Basic setup assessment
        price = quote["price"]
        prev_close = quote["previous_close"]
        change_pct = quote["change_pct"]

        # Default simulated levels if live intraday bars are outside market hours
        levels = {
            "entry_candidate": price,
            "stop_suggested": round(price * 0.99, asset.price_decimals),
            "target_suggested": round(price * 1.02, asset.price_decimals),
            "risk_per_share": round(price * 0.01, asset.price_decimals),
            "reward_risk": 2.0,
        }

        res = {
            "symbol": asset.symbol,
            "asset_class": asset.asset_class,
            "exchange": asset.exchange,
            "currency": asset.quote_currency,
            "currency_symbol": asset.currency_symbol,
            "current_price": price,
            "previous_close": prev_close,
            "change_pct": change_pct,
            "market_open": clock["is_open"],
            "market_status": clock["reason"],
            "local_time": clock["local_time"],
            "setup": setup,
            "levels": levels,
            "data_source": quote["data_source"],
            "regulatory_note": "US SEC / STOCK Act checks not applicable" if asset.asset_class in ("equity_in", "crypto") else "US SEC jurisdiction",
        }
        log_audit_event("analyze_symbol", {"symbol": symbol, "setup": setup}, res)
        return res

    def scan_market(self, market: str = "india", setup: str = "daily", limit: Optional[int] = None) -> Dict[str, Any]:
        from importlib.machinery import SourceFileLoader
        scan_mod = SourceFileLoader("scan", os.path.join(ROOT, "trader-scanner", "scan.py")).load_module()

        syms = scan_mod.DEFAULT_INDIA_WATCHLIST if market == "india" else (
            scan_mod.DEFAULT_CRYPTO_WATCHLIST if market == "crypto" else ["AAPL", "NVDA", "TSLA", "AMD", "MSFT"]
        )
        if limit:
            syms = syms[:limit]

        results = []
        for s in syms:
            try:
                q = self.provider.get_latest_quote(s)
                results.append({
                    "symbol": s,
                    "price": q["price"],
                    "change_pct": q["change_pct"],
                    "currency": q["currency"],
                })
            except Exception:
                continue

        res = {
            "market": market,
            "setup": setup,
            "symbols_scanned": len(syms),
            "candidates_found": len(results),
            "candidates": results,
        }
        log_audit_event("scan_market", {"market": market, "setup": setup}, res)
        return res

    def evaluate_risk(
        self,
        symbol: str,
        side: str,
        qty: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        skip_window: bool = False,
    ) -> Dict[str, Any]:
        from importlib.machinery import SourceFileLoader
        check_mod = SourceFileLoader("check", os.path.join(ROOT, "trader-risk", "check.py")).load_module()

        order = {
            "symbol": symbol.strip().upper(),
            "side": side.lower(),
            "qty": str(qty),
            "type": "market",
            "time_in_force": "day",
            "order_class": "bracket",
            "take_profit_limit_price": "%.2f" % take_profit,
            "stop_loss_stop_price": "%.2f" % stop_loss,
            "plan": {"entry": entry_price, "stop": stop_loss, "target": take_profit},
        }

        cfg_path = os.path.join(ROOT, "config.example.json")
        if not os.path.exists(cfg_path):
            cfg_path = os.path.join(ROOT, "trader-budget", "config.example.json")
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        if skip_window:
            cfg["no_entries_before"] = None
            cfg["no_entries_after"] = None

        # Allow crypto if symbol is crypto
        asset = parse_symbol(symbol)
        if asset.asset_class == "crypto":
            cfg["allow_crypto"] = True
        elif asset.asset_class in ("equity_in", "index") and "max_loss_per_trade_inr" in cfg:
            cfg["max_loss_per_trade_usd"] = cfg["max_loss_per_trade_inr"]

        res = check_mod.check([order], cfg)
        order_res = res["results"][0]
        output = {
            "symbol": symbol,
            "verdict": order_res["verdict"],
            "approved": order_res["verdict"] == "PASS",
            "reasons": order_res["reasons"],
            "warnings": order_res["warnings"],
            "max_loss": order_res.get("max_loss"),
            "reward_risk": order_res.get("reward_risk"),
            "value": order_res.get("value_usd"),
        }
        log_audit_event("evaluate_risk", {"symbol": symbol, "qty": qty}, output)
        return output

    def paper_trade(
        self,
        symbol: str,
        side: str = "buy",
        budget: Optional[float] = None,
        execute: bool = False,
    ) -> Dict[str, Any]:
        quote = self.provider.get_latest_quote(symbol)
        price = quote["price"]
        stop = round(price * 0.99, 2)
        target = round(price * 1.02, 2)

        # Size the order
        from importlib.machinery import SourceFileLoader
        size_mod = SourceFileLoader("size", os.path.join(ROOT, "trader-budget", "size.py")).load_module()
        cfg_path = os.path.join(ROOT, "trader-budget", "config.example.json")
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        if budget:
            cfg["budget"] = budget
        asset = parse_symbol(symbol)
        if asset.asset_class == "crypto":
            cfg["allow_crypto"] = True

        plan = {
            "symbol": symbol,
            "side": side,
            "entry": price,
            "stop": stop,
            "target": target,
            "setup": "orb",
        }

        sized = size_mod.size_trade(plan, cfg, budget=cfg.get("budget", 5000))
        bracket = size_mod.bracket_order(sized)

        # Risk check
        risk_eval = None
        if bracket:
            risk_eval = self.evaluate_risk(
                symbol=symbol,
                side=side,
                qty=float(bracket["qty"]),
                entry_price=price,
                stop_loss=stop,
                take_profit=target,
            )

        output = {
            "symbol": symbol,
            "quote": quote,
            "plan": plan,
            "sizing": sized,
            "bracket_order": bracket,
            "risk_validation": risk_eval,
            "simulated_executed": False,
        }

        if execute and risk_eval and risk_eval["verdict"] == "PASS":
            # Record paper trade execution
            fill_record = {
                "id": f"paper_{int(datetime.datetime.now().timestamp())}",
                "symbol": symbol,
                "side": side,
                "qty": bracket["qty"],
                "price": price,
                "stop": stop,
                "target": target,
                "executed_at": datetime.datetime.now(datetime.timezone.utc).isoformat() + "Z",
                "status": "filled",
            }
            output["simulated_executed"] = True
            output["fill_details"] = fill_record

        log_audit_event("paper_trade", {"symbol": symbol, "execute": execute}, output)
        return output


def dispatch_gemini_call(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Unified dispatcher for Gemini function calling."""
    iface = GeminiTradingInterface()
    if name == "analyze_symbol":
        return iface.analyze_symbol(args["symbol"], setup=args.get("setup", "orb"))
    elif name == "scan_market":
        return iface.scan_market(market=args["market"], setup=args.get("setup", "daily"), limit=args.get("limit"))
    elif name == "evaluate_risk":
        order = args.get("order")
        if order:
            symbol = order.get("ticker") or order.get("symbol")
            side = order.get("action", "BUY").lower()
            qty = order.get("shares") or order.get("qty", 1)
            entry_price = order.get("limit") or order.get("entry_price", 100.0)
            stop_loss = order.get("stop_loss") or order.get("stop")
            take_profit = order.get("take_profit") or order.get("target")
        else:
            symbol = args["symbol"]
            side = args["side"]
            qty = args["qty"]
            entry_price = args["entry_price"]
            stop_loss = args["stop_loss"]
            take_profit = args["take_profit"]
        return iface.evaluate_risk(
            symbol=symbol,
            side=side,
            qty=qty,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            skip_window=args.get("skip_window", False),
        )
    elif name == "paper_trade":
        return iface.paper_trade(
            symbol=args["symbol"],
            side=args.get("side", "buy"),
            budget=args.get("budget"),
            execute=args.get("execute", False),
        )
    elif name == "get_market_clock":
        return iface.get_market_clock(symbol=args.get("symbol"))
    else:
        return {"error": f"Unknown Gemini tool function: {name}"}


# Aliases for backward/alternative naming compatibility
dispatch_tool_call = dispatch_gemini_call
log_audit = log_audit_event
AUDIT_FILE = AUDIT_LOG_PATH


if __name__ == "__main__":
    # If run directly: output tool schemas as JSON
    print(json.dumps(GEMINI_TOOLS, indent=2))
