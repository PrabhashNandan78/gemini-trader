"""Copilot REST API and WebSocket/HTTP Server for Web UI and Browser Extension.

Serves:
1. REST API for market regime detection, 3:1 ticket generation, and order execution.
2. Paper and Live broker state management.
3. Static files for the web dashboard and chart viewer.
4. CORS-enabled endpoints for companion browser extensions.
"""

import argparse
import http.server
import json
import math
import os
import random
import re
import socketserver
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from copilot.broker_adapter import BaseBrokerAdapter, PaperBrokerAdapter, create_broker_adapter
from copilot.cost_model import TransactionCostModel
from copilot.paper_engine import PaperTradingEngine
from copilot.regime import (
    MarketRegimeDetector,
    calc_adx,
    calc_atr,
    calc_bollinger_bands,
    calc_ema,
    calc_rsi,
    calc_sma,
)
from copilot.risk_engine import OrderTicket, RiskAndSizingEngine, UserRiskConfig
from copilot.strategies import AdaptiveStrategyEngine
from engine.market_data import MarketDataError
from copilot.market_data import (
    LiveMarketDataManager,
    MarketDataProvider,
    merge_candle_bars,
    resolve_instrument_info,
    InstrumentInfo,
)


class SignalDiagnosticsTracker:
    """Tracks signal evaluations, approvals, rejections, and execution metrics."""

    def __init__(self):
        self.signals_evaluated: int = 0
        self.buy_signals: int = 0
        self.sell_signals: int = 0
        self.no_trade_decisions: int = 0
        self.risk_approved_signals: int = 0
        self.executed_paper_orders: int = 0
        self.rejected_orders: int = 0
        self.most_recent_rejection_reason: Optional[str] = None
        self.last_evaluation_time: Optional[str] = None
        self.recent_events: List[Dict[str, Any]] = []

    def record_evaluation(self, decision: str, strategy: Optional[str] = None):
        self.signals_evaluated += 1
        self.last_evaluation_time = datetime.now(timezone.utc).isoformat()
        if decision == "BUY":
            self.buy_signals += 1
        elif decision == "SELL":
            self.sell_signals += 1
        else:
            self.no_trade_decisions += 1

    def record_risk_approval(self):
        self.risk_approved_signals += 1

    def record_execution(self, order_id: str, symbol: str, fill_price: float):
        self.executed_paper_orders += 1
        self.recent_events.append({
            "type": "EXECUTION",
            "time": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "order_id": order_id,
            "fill_price": fill_price,
        })
        if len(self.recent_events) > 50:
            self.recent_events.pop(0)

    def record_rejection(self, reason: str, symbol: Optional[str] = None):
        self.rejected_orders += 1
        self.most_recent_rejection_reason = reason
        self.recent_events.append({
            "type": "REJECTION",
            "time": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "reason": reason,
        })
        if len(self.recent_events) > 50:
            self.recent_events.pop(0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "signals_evaluated": self.signals_evaluated,
            "buy_signals": self.buy_signals,
            "sell_signals": self.sell_signals,
            "no_trade_decisions": self.no_trade_decisions,
            "risk_approved_signals": self.risk_approved_signals,
            "executed_paper_orders": self.executed_paper_orders,
            "rejected_orders": self.rejected_orders,
            "most_recent_rejection_reason": self.most_recent_rejection_reason or "None",
            "last_evaluation_time": self.last_evaluation_time,
            "recent_events": self.recent_events[-10:],
        }


class CopilotServerState:
    """Singleton server state maintaining the engine, broker, and user config."""

    def __init__(self, mode: str = "paper", broker_name: str = "PAPER", initial_balance: float = 100000.0):
        self.user_config = UserRiskConfig(
            market="NSE",
            broker=broker_name,
            currency="INR",
            balance=initial_balance,
            min_allocation=1000.0,
            max_allocation=50000.0,
            max_risk_pct=1.0,
            max_daily_loss=3000.0,
            max_open_exposure=75000.0,
            max_concurrent_positions=3,
            leverage=1.0,
            mode=mode,
            auto_execute_paper=False,
            new_entries_paused=False,
            reentry_cooldown_sec=60,
            max_positions_per_instrument=1,
            max_trades_per_day=10,
        )
        self.paper_engine = PaperTradingEngine(
            initial_balance=initial_balance,
            currency="INR",
            risk_config=self.user_config,
        )
        self.broker = create_broker_adapter(
            broker_name=broker_name,
            mode=mode,
            user_config=self.user_config,
            paper_engine=self.paper_engine,
        )
        self.market_data = LiveMarketDataManager()
        self.strategy_engine = AdaptiveStrategyEngine()
        self.last_executed_signals: Dict[Any, Any] = {}
        self.signal_diagnostics = SignalDiagnosticsTracker()
        self.last_trade_times: Dict[str, float] = {}
        self.active_context: Dict[str, Any] = {
            "symbol": None,
            "raw_symbol": None,
            "interval": "15m",
            "source": None,
            "status": "TRADINGVIEW NOT SYNCHRONIZED",
            "synced": False,
            "updated_at": 0.0,
            "unverified": True,
        }

    def update_config(self, new_params: Dict[str, Any]) -> UserRiskConfig:
        for k, v in new_params.items():
            if hasattr(self.user_config, k):
                field_val = getattr(self.user_config, k)
                if isinstance(field_val, bool):
                    if isinstance(v, str):
                        setattr(self.user_config, k, v.lower() in ("true", "1", "yes"))
                    else:
                        setattr(self.user_config, k, bool(v))
                elif isinstance(field_val, int):
                    setattr(self.user_config, k, int(v))
                elif isinstance(field_val, float):
                    setattr(self.user_config, k, float(v))
                else:
                    setattr(self.user_config, k, str(v))

        # Re-sync broker if mode or broker name changed
        self.broker = create_broker_adapter(
            broker_name=self.user_config.broker,
            mode=self.user_config.mode,
            user_config=self.user_config,
            paper_engine=self.paper_engine,
        )
        return self.user_config
        return self.user_config


# Global state holder for the HTTP handler
SERVER_STATE: Optional[CopilotServerState] = None


def generate_synthetic_bars(symbol: str, count: int = 80, interval_sec: int = 300) -> List[Dict[str, Any]]:
    """Generates realistic synthetic OHLCV intraday bars for offline testing and visualization."""
    base_price = 2450.0 if "RELIANCE" in symbol else (65000.0 if "BTC" in symbol else (3500.0 if "TCS" in symbol else 150.0))
    volatility = base_price * 0.002
    bars = []
    current = base_price

    now = int(datetime.now(timezone.utc).timestamp())
    start_time = now - (count * interval_sec)

    for i in range(count):
        bar_time = start_time + (i * interval_sec)
        delta = (random.random() - 0.48) * volatility * 2
        bar_open = current
        bar_close = max(1.0, bar_open + delta)
        high_extra = random.random() * volatility
        low_extra = random.random() * volatility
        bar_high = max(bar_open, bar_close) + high_extra
        bar_low = min(bar_open, bar_close) - low_extra
        volume = int(random.uniform(5000, 25000))
        current = bar_close

        bars.append({
            "timestamp": bar_time,
            "datetime_utc": datetime.fromtimestamp(bar_time, tz=timezone.utc).isoformat(),
            "datetime_local": datetime.fromtimestamp(bar_time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "open": round(bar_open, 2),
            "high": round(bar_high, 2),
            "low": round(bar_low, 2),
            "close": round(bar_close, 2),
            "volume": volume,
        })
    return bars


def clean_float(val: Optional[float], decimals: int = 2) -> Optional[float]:
    """Return rounded float or None if None, NaN, or Inf."""
    if val is None:
        return None
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, decimals)
    except (ValueError, TypeError):
        return None


def sanitize_for_json(obj: Any) -> Any:
    """Recursively convert float('nan') and float('inf') to None for RFC-compliant JSON."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    elif isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [sanitize_for_json(v) for v in obj]
    return obj


def normalize_market_symbol(raw_symbol: str, market: Optional[str] = None) -> Dict[str, Any]:
    """Normalizes raw ticker symbols from TradingView or user inputs to contract-aware provider symbols."""
    info = resolve_instrument_info(raw_symbol, market=market)
    return info.to_dict()


class CopilotRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Custom HTTP Request Handler serving REST API endpoints and web UI."""

    def __init__(self, *args, **kwargs):
        # Serve static assets from copilot/web
        web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
        super().__init__(*args, directory=web_dir, **kwargs)

    def _send_cors_headers(self) -> None:
        origin = self.headers.get("Origin")
        if origin and (origin.startswith("chrome-extension://") or "tradingview.com" in origin or origin in ("http://127.0.0.1:8765", "http://localhost:8765")):
            self.send_header("Access-Control-Allow-Origin", origin)
        else:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, DELETE")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def _send_json_response(self, data: Any, status: int = 200) -> None:
        try:
            sanitized = sanitize_for_json(data)
            payload = json.dumps(sanitized, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(payload)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            # Client closed connection early
            return
        except Exception as e:
            try:
                err_body = json.dumps({
                    "error": "Internal JSON serialization error",
                    "details": str(e),
                    "status": "ERROR"
                }).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err_body)))
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(err_body)
            except Exception:
                pass

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight requests."""
        self.send_response(204)
        self._send_cors_headers()
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query = urllib.parse.parse_qs(parsed_url.query)

        global SERVER_STATE
        if not SERVER_STATE:
            SERVER_STATE = CopilotServerState()

        if path == "/api/status":
            self.handle_get_status()
        elif path == "/api/diagnostics":
            self.handle_get_diagnostics()
        elif path == "/api/health":
            self.handle_get_health()
        elif path == "/api/config":
            self.handle_get_config()
        elif path == "/api/chart":
            self.handle_get_chart(query)
        elif path == "/api/active_context":
            self.handle_get_active_context()
        elif path == "/api/positions":
            self.handle_get_positions()
        elif path == "/api/orders":
            self.handle_get_orders(query)
        elif path == "/api/trades":
            self.handle_get_trades()
        elif path.startswith("/api/"):
            self._send_json_response({"error": f"API endpoint not found: {path}", "status": "NOT_FOUND"}, status=404)
        else:
            # Fallback to static web dashboard
            super().do_GET()

    def do_POST(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        global SERVER_STATE
        if not SERVER_STATE:
            SERVER_STATE = CopilotServerState()

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except Exception:
            payload = {}

        if path == "/api/config":
            self.handle_post_config(payload)
        elif path == "/api/active_context":
            self.handle_post_active_context(payload)
        elif path == "/api/analyze":
            self.handle_post_analyze(payload)
        elif path == "/api/order":
            self.handle_post_order(payload)
        elif path == "/api/close_position":
            self.handle_post_close_position(payload)
        elif path == "/api/close_all":
            self.handle_post_close_all()
        elif path == "/api/pause":
            self.handle_post_pause(payload)
        elif path == "/api/resume":
            self.handle_post_resume()
        elif path == "/api/cancel_all":
            self.handle_post_cancel_all()
        elif path == "/api/emergency_stop":
            self.handle_post_emergency_stop()
        elif path == "/api/reset":
            self.handle_post_reset(payload)
        else:
            self._send_json_response({"error": f"POST endpoint not found: {path}"}, status=404)

    # API HANDLERS

    def handle_get_status(self) -> None:
        global SERVER_STATE
        balance_info = SERVER_STATE.broker.get_account_balance()
        positions = SERVER_STATE.broker.get_positions()
        orders = SERVER_STATE.broker.get_orders(status="OPEN")
        state_snapshot = SERVER_STATE.paper_engine.get_state()

        self._send_json_response({
            "status": "ONLINE",
            "mode": SERVER_STATE.user_config.mode,
            "broker": SERVER_STATE.user_config.broker,
            "currency": SERVER_STATE.user_config.currency,
            "balance": balance_info.get("balance", 0.0),
            "equity": balance_info.get("equity", 0.0),
            "peak_equity": balance_info.get("peak_equity", 0.0),
            "max_drawdown_pct": balance_info.get("max_drawdown_pct", 0.0),
            "open_positions_count": len(positions),
            "open_orders_count": len(orders),
            "trades_count": len(state_snapshot.get("trade_history", [])),
            "emergency_halted": SERVER_STATE.broker.emergency_halted,
            "auto_execute_paper": SERVER_STATE.user_config.auto_execute_paper,
            "new_entries_paused": SERVER_STATE.user_config.new_entries_paused,
            "max_daily_loss": SERVER_STATE.user_config.max_daily_loss,
            "daily_realized_loss": SERVER_STATE.paper_engine.get_realized_loss_today(),
            "max_concurrent_positions": SERVER_STATE.user_config.max_concurrent_positions,
            "max_open_exposure": SERVER_STATE.user_config.max_open_exposure,
            "current_open_exposure": SERVER_STATE.paper_engine.get_current_open_exposure(),
            "stats": state_snapshot.get("stats", {}),
            "signal_diagnostics": SERVER_STATE.signal_diagnostics.to_dict(),
            "diagnostics": SERVER_STATE.signal_diagnostics.to_dict(),
            "reentry_cooldown_sec": SERVER_STATE.user_config.reentry_cooldown_sec,
            "max_positions_per_instrument": SERVER_STATE.user_config.max_positions_per_instrument,
        })

    def handle_get_diagnostics(self) -> None:
        global SERVER_STATE
        self._send_json_response(SERVER_STATE.signal_diagnostics.to_dict())

    def handle_get_health(self) -> None:
        self._send_json_response({
            "status": "OK",
            "version": "2.0.0",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def handle_get_config(self) -> None:
        global SERVER_STATE
        self._send_json_response(SERVER_STATE.user_config.to_dict())

    def handle_post_config(self, payload: Dict[str, Any]) -> None:
        global SERVER_STATE
        updated = SERVER_STATE.update_config(payload)
        self._send_json_response({
            "success": True,
            "message": "Configuration updated successfully",
            "config": updated.to_dict(),
        })

    def handle_get_active_context(self) -> None:
        global SERVER_STATE
        ctx = getattr(SERVER_STATE, "active_context", {}) or {}
        updated_at = ctx.get("updated_at", 0.0)
        now = time.time()
        age_sec = round(now - updated_at, 1) if updated_at > 0 else 999999.0

        if updated_at == 0.0 or age_sec > 60.0:
            status = "TRADINGVIEW NOT SYNCHRONIZED"
            synced = False
            msg = "No active TradingView tab detected within the last 60 seconds."
        elif ctx.get("unverified"):
            status = ctx.get("status", "UNVERIFIED")
            synced = False
            msg = ctx.get("reason", "Chart context unverified")
        else:
            status = ctx.get("status", "SYNCHRONIZED")
            synced = (status == "SYNCHRONIZED")
            msg = "TradingView active chart synchronized"

        self._send_json_response({
            "success": True,
            "synced": synced,
            "status": status,
            "message": msg,
            "active_context": ctx,
            "age_sec": age_sec,
        })

    def handle_post_active_context(self, payload: Dict[str, Any]) -> None:
        global SERVER_STATE
        raw_symbol = str(payload.get("raw_symbol") or payload.get("symbol") or "").strip()
        raw_interval = str(payload.get("interval", "15m")).strip().lower()
        unverified = bool(payload.get("unverified", False))
        reason = payload.get("reason")
        source = payload.get("source", "tradingview")

        allowed_intervals = ["1m", "2m", "5m", "15m", "30m", "60m", "1h", "1d"]
        if raw_interval not in allowed_intervals:
            raw_interval = "15m"

        if unverified or not raw_symbol or raw_symbol.upper() == "UNVERIFIED":
            SERVER_STATE.active_context = {
                "symbol": "UNVERIFIED",
                "raw_symbol": raw_symbol,
                "interval": raw_interval,
                "source": source,
                "status": "UNVERIFIED",
                "unverified": True,
                "reason": reason or "Chart context unverified",
                "updated_at": time.time(),
                "synced": False,
            }
            self._send_json_response({"success": True, "active_context": SERVER_STATE.active_context})
            return

        sym_info = normalize_market_symbol(raw_symbol)
        if not sym_info.get("supported"):
            reason_msg = sym_info.get("reason", f"Unsupported symbol: {raw_symbol}")
            status_code = sym_info.get("status", "UNSUPPORTED_INSTRUMENT")
            SERVER_STATE.active_context = {
                "symbol": raw_symbol,
                "raw_symbol": raw_symbol,
                "tradingview_symbol": sym_info.get("tradingview_symbol", raw_symbol),
                "canonical_id": sym_info.get("canonical_id", "UNKNOWN"),
                "instrument_type": sym_info.get("instrument_type", "unknown"),
                "interval": raw_interval,
                "source": source,
                "status": status_code,
                "unverified": True,
                "reason": reason_msg,
                "updated_at": time.time(),
                "synced": False,
            }
            self._send_json_response({"success": True, "active_context": SERVER_STATE.active_context})
            return

        # Valid supported instrument
        SERVER_STATE.active_context = {
            "symbol": sym_info["mapped_symbol"],
            "raw_symbol": raw_symbol,
            "tradingview_symbol": sym_info.get("tradingview_symbol", raw_symbol),
            "canonical_id": sym_info.get("canonical_id"),
            "display_name": sym_info.get("display_name", raw_symbol),
            "market": sym_info.get("market", "NSE"),
            "exchange": sym_info.get("exchange", "NSE"),
            "instrument_type": sym_info.get("instrument_type", "spot"),
            "contract_denomination": sym_info.get("contract_denomination", "USD"),
            "settlement_currency": sym_info.get("settlement_currency", "USD"),
            "interval": raw_interval,
            "source": source,
            "status": "SYNCHRONIZED",
            "unverified": False,
            "is_proxy": sym_info.get("is_proxy", False),
            "proxy_target": sym_info.get("proxy_target"),
            "proxy_notice": sym_info.get("proxy_notice"),
            "updated_at": time.time(),
            "synced": True,
        }
        self._send_json_response({"success": True, "active_context": SERVER_STATE.active_context})

    def handle_get_chart(self, query: Dict[str, List[str]]) -> None:
        global SERVER_STATE
        symbols = query.get("symbol")
        if not symbols or not symbols[0].strip():
            self._send_json_response({"error": "Missing or empty 'symbol' parameter", "status": "ERROR"}, status=400)
            return

        raw_symbol = symbols[0].strip()
        market_param = query.get("market", ["NSE"])[0].upper()
        interval = query.get("interval", ["5m"])[0].lower()
        try:
            count = max(10, min(500, int(query.get("count", ["80"])[0])))
        except ValueError:
            count = 80

        # Validate interval
        allowed_intervals = ["1m", "2m", "5m", "15m", "30m", "60m", "1h", "1d"]
        if interval not in allowed_intervals:
            self._send_json_response({
                "error": f"Unsupported interval '{interval}'. Supported intervals: {', '.join(allowed_intervals)}",
                "symbol": raw_symbol,
                "status": "ERROR"
            }, status=400)
            return

        # Normalize symbol
        sym_info = normalize_market_symbol(raw_symbol, market=market_param)
        if not sym_info["supported"]:
            self._send_json_response({
                "error": sym_info.get("reason", f"Unsupported symbol '{raw_symbol}'"),
                "symbol": raw_symbol,
                "status": "UNSUPPORTED_SYMBOL"
            }, status=400)
            return

        mapped_symbol = sym_info["mapped_symbol"]
        source_label = sym_info["source_label"]
        market = sym_info["market"]

        # Map interval to historical range
        if interval in ("1m", "2m"):
            range_str = "1d"
        elif interval in ("5m", "15m", "30m"):
            range_str = "5d"
        elif interval in ("60m", "1h"):
            range_str = "1mo"
        else:
            range_str = "6mo"

        # Fetch real candles from MarketDataProvider
        bars = []
        meta = {}
        try:
            _, fetched_bars, meta = SERVER_STATE.market_data.get_bars(symbol=mapped_symbol, range_str=range_str, interval=interval)
            if fetched_bars:
                bars = fetched_bars[-count:]
        except MarketDataError as e:
            self._send_json_response({
                "error": f"Market data provider error for {raw_symbol} ({mapped_symbol}): {str(e)}",
                "symbol": raw_symbol,
                "mapped_symbol": mapped_symbol,
                "market": market,
                "status": "ERROR"
            }, status=502)
            return
        except Exception as e:
            self._send_json_response({
                "error": f"Failed to retrieve market data for {raw_symbol} ({mapped_symbol}): {str(e)}",
                "symbol": raw_symbol,
                "mapped_symbol": mapped_symbol,
                "market": market,
                "status": "ERROR"
            }, status=502)
            return

        if not bars or len(bars) < 5:
            self._send_json_response({
                "error": f"No candles available for {raw_symbol} ({mapped_symbol}) at interval {interval}",
                "symbol": raw_symbol,
                "mapped_symbol": mapped_symbol,
                "market": market,
                "status": "NO_DATA"
            }, status=404)
            return

        # Sort bars chronologically
        bars.sort(key=lambda b: b["timestamp"])

        # Compute quantitative indicators
        closes = [float(b["close"]) for b in bars]
        highs = [float(b["high"]) for b in bars]
        lows = [float(b["low"]) for b in bars]

        ema9 = calc_ema(closes, 9)
        ema21 = calc_ema(closes, 21)
        ema50 = calc_ema(closes, 50)
        bb = calc_bollinger_bands(closes, period=20, mult=2.0)
        atr14 = calc_atr(highs, lows, closes, period=14)
        rsi14 = calc_rsi(closes, period=14)
        plus_di, minus_di, adx_series = calc_adx(highs, lows, closes, period=14)

        # Detect regime
        regime_info = MarketRegimeDetector.detect_regime(bars)

        # Attach clean computed indicators to bars
        chart_bars = []
        for i, b in enumerate(bars):
            chart_bars.append({
                "time": b["timestamp"],
                "datetime": b.get("datetime_local", ""),
                "open": round(float(b["open"]), 4),
                "high": round(float(b["high"]), 4),
                "low": round(float(b["low"]), 4),
                "close": round(float(b["close"]), 4),
                "volume": int(b.get("volume", 0)),
                "ema9": clean_float(ema9[i]),
                "ema21": clean_float(ema21[i]),
                "ema50": clean_float(ema50[i]),
                "bb_upper": clean_float(bb.upper[i]),
                "bb_middle": clean_float(bb.middle[i]),
                "bb_lower": clean_float(bb.lower[i]),
                "rsi": clean_float(rsi14[i]),
                "adx": clean_float(adx_series[i]),
            })

        health_info = meta.get("data_health") if isinstance(meta, dict) else None
        if not health_info and hasattr(SERVER_STATE.market_data, "get_data_health"):
            health_info = SERVER_STATE.market_data.get_data_health(mapped_symbol, interval)
        if not health_info:
            health_info = {
                "status": "LIVE",
                "is_stale": False,
                "provider": source_label,
                "last_updated_iso": chart_bars[-1]["datetime"] if chart_bars else None,
                "last_updated_ts": chart_bars[-1]["time"] if chart_bars else None,
                "data_age_sec": max(0, int(time.time() - (chart_bars[-1]["time"] if chart_bars else time.time()))),
                "candle_count": len(chart_bars),
                "error": None,
            }

        feed_label = health_info.get("provider", source_label)

        self._send_json_response({
            "symbol": raw_symbol,
            "mapped_symbol": mapped_symbol,
            "display_name": sym_info.get("display_name", raw_symbol),
            "market": market,
            "source": feed_label,
            "feed_label": feed_label,
            "interval": interval,
            "is_proxy": sym_info.get("is_proxy", False),
            "proxy_target": sym_info.get("proxy_target"),
            "proxy_notice": sym_info.get("proxy_notice"),
            "regime": regime_info,
            "bars_count": len(chart_bars),
            "bars": chart_bars,
            "data_health": health_info,
        })

    def handle_post_analyze(self, payload: Dict[str, Any]) -> None:
        global SERVER_STATE
        raw_symbol = str(payload.get("symbol", "RELIANCE.NS")).strip()
        market_param = str(payload.get("market", SERVER_STATE.user_config.market)).strip().upper()
        interval = str(payload.get("interval", "5m")).strip().lower()

        allowed_intervals = ["1m", "2m", "5m", "15m", "30m", "60m", "1h", "1d"]
        if interval not in allowed_intervals:
            interval = "5m"

        sym_info = normalize_market_symbol(raw_symbol, market=market_param)
        if not sym_info["supported"]:
            reason_msg = sym_info.get("reason", f"Unsupported symbol: {raw_symbol}")
            SERVER_STATE.signal_diagnostics.record_evaluation("NO_TRADE")
            SERVER_STATE.signal_diagnostics.record_rejection(reason_msg, symbol=raw_symbol)
            self._send_json_response({
                "error": reason_msg,
                "symbol": raw_symbol,
                "status": "UNSUPPORTED_SYMBOL",
                "is_proxy": False,
                "proxy_target": None,
                "proxy_notice": None,
                "analysis": {
                    "decision": "NO_TRADE",
                    "selected_strategy": None,
                    "regime": {
                        "regime": "UNKNOWN",
                        "trend_strength": "NONE",
                        "volatility_state": "UNKNOWN",
                        "adx": 0.0,
                        "confidence": 0.0,
                    },
                    "ticket": None,
                    "reasons": [reason_msg],
                },
                "market_tick_events": [],
            }, status=400)
            return

        mapped_symbol = sym_info["mapped_symbol"]
        source_label = sym_info["source_label"]
        market = sym_info["market"]

        # Map interval to historical range
        if interval in ("1m", "2m"):
            range_str = "1d"
        elif interval in ("5m", "15m", "30m"):
            range_str = "5d"
        elif interval in ("60m", "1h"):
            range_str = "1mo"
        else:
            range_str = "6mo"

        # Fetch real bars
        bars = []
        meta = {}
        try:
            _, fetched_bars, meta = SERVER_STATE.market_data.get_bars(symbol=mapped_symbol, range_str=range_str, interval=interval)
            if fetched_bars and len(fetched_bars) >= 20:
                bars = fetched_bars
        except Exception as e:
            self._send_json_response({
                "analysis": {
                    "decision": "NO_TRADE",
                    "selected_strategy": None,
                    "regime": {
                        "regime": "UNKNOWN",
                        "trend_strength": "NONE",
                        "volatility_state": "UNKNOWN",
                        "adx": 0.0,
                        "confidence": 0.0,
                    },
                    "ticket": None,
                    "reasons": [f"Market data provider error for {raw_symbol} ({mapped_symbol}): {str(e)}"],
                },
                "symbol": raw_symbol,
                "mapped_symbol": mapped_symbol,
                "market_tick_events": [],
                "error": str(e),
            }, status=502)
            return

        if not bars or len(bars) < 20:
            self._send_json_response({
                "analysis": {
                    "decision": "NO_TRADE",
                    "selected_strategy": None,
                    "regime": {
                        "regime": "UNKNOWN",
                        "trend_strength": "NONE",
                        "volatility_state": "UNKNOWN",
                        "adx": 0.0,
                        "confidence": 0.0,
                    },
                    "ticket": None,
                    "reasons": [f"Insufficient historical bars ({len(bars)}/20) for {raw_symbol} ({mapped_symbol}) at interval {interval}."],
                },
                "symbol": raw_symbol,
                "mapped_symbol": mapped_symbol,
                "market_tick_events": [],
            }, status=404)
            return

        # Feed latest tick into paper engine to trigger pending stops/targets
        latest_bar = bars[-1]
        events = SERVER_STATE.paper_engine.process_market_tick(mapped_symbol, latest_bar)

        # Stale data check
        now_ts = time.time()
        ts_val = latest_bar.get("timestamp", 0)
        if isinstance(ts_val, (int, float)):
            bar_ts = float(ts_val)
        elif isinstance(ts_val, str):
            try:
                bar_ts = datetime.fromisoformat(ts_val.replace("Z", "+00:00")).timestamp()
            except Exception:
                try:
                    bar_ts = float(ts_val)
                except Exception:
                    bar_ts = now_ts
        else:
            bar_ts = now_ts

        interval_sec = {
            "1m": 60, "2m": 120, "5m": 300, "15m": 900,
            "30m": 1800, "60m": 3600, "1h": 3600, "1d": 86400
        }.get(interval, 900)

        health_info = meta.get("data_health") if isinstance(meta, dict) else None
        if not health_info and hasattr(SERVER_STATE.market_data, "get_data_health"):
            health_info = SERVER_STATE.market_data.get_data_health(mapped_symbol, interval)
        if not health_info:
            health_info = {
                "status": "LIVE",
                "is_stale": False,
                "provider": source_label,
                "last_updated_iso": latest_bar.get("datetime_local"),
                "last_updated_ts": latest_bar.get("timestamp"),
                "data_age_sec": max(0, int(now_ts - bar_ts)),
                "candle_count": len(bars),
                "error": None,
            }

        is_stale = False
        stale_msg = None
        if health_info and health_info.get("is_stale"):
            is_stale = True
            stale_msg = f"Market data feed is STALE ({health_info.get('status')}: age {health_info.get('data_age_sec', 0)}s). New paper entries blocked."
        elif market == "CRYPTO" and (now_ts - bar_ts) > (interval_sec * 4):
            is_stale = True
            stale_msg = f"Market data feed is STALE (last candle: {latest_bar.get('datetime_local', bar_ts)}, latency > {round((now_ts - bar_ts)/60)}m)."

        # Run adaptive strategy engine
        analysis = SERVER_STATE.strategy_engine.analyze_and_generate_ticket(
            bars=bars,
            symbol=mapped_symbol,
            market=market,
            user_config=SERVER_STATE.user_config,
        )

        strat_name = analysis.get("selected_strategy", "Unknown")
        decision = analysis.get("decision", "NO TRADE")
        SERVER_STATE.signal_diagnostics.record_evaluation(decision, strategy=strat_name)

        if is_stale and stale_msg:
            analysis["decision"] = "NO TRADE"
            analysis["ticket"] = None
            analysis.setdefault("reasons", []).insert(0, stale_msg)

        # Attach contract and context awareness to ticket if present
        ticket_data = analysis.get("ticket")
        if ticket_data:
            ticket_data["tradingview_symbol"] = sym_info.get("tradingview_symbol", raw_symbol)
            ticket_data["canonical_id"] = sym_info.get("canonical_id")
            ticket_data["instrument_type"] = sym_info.get("instrument_type", "spot")
            ticket_data["data_provider"] = sym_info.get("source_label") or sym_info.get("provider_name")
            ticket_data["timeframe"] = interval
            ticket_data["candle_timestamp"] = latest_bar.get("timestamp")
            ticket_data["strategy_id"] = strat_name
            if analysis.get("decision") == "TRADE":
                SERVER_STATE.signal_diagnostics.record_risk_approval()

        # Automatic paper execution check
        auto_exec_cfg = SERVER_STATE.user_config.auto_execute_paper
        if "auto_execute" in payload:
            auto_exec_cfg = bool(payload["auto_execute"])

        candle_sig = (mapped_symbol, interval, latest_bar.get("timestamp"), strat_name)
        is_fallback_data = bool(health_info and health_info.get("is_fallback"))

        if not auto_exec_cfg:
            analysis["auto_execution"] = {
                "enabled": False,
                "executed": False,
                "status": "MANUAL_APPROVAL_REQUIRED",
                "reason": "Auto Paper Execution disabled. Manual approval required.",
            }
        elif is_fallback_data:
            fallback_reason = "CACHED FALLBACK: Fallback fixtures cannot generate executable paper trades."
            SERVER_STATE.signal_diagnostics.record_rejection(fallback_reason, symbol=mapped_symbol)
            analysis["auto_execution"] = {
                "enabled": auto_exec_cfg,
                "executed": False,
                "reason": fallback_reason,
            }
        elif is_stale and stale_msg:
            SERVER_STATE.signal_diagnostics.record_rejection(stale_msg, symbol=mapped_symbol)
            analysis["auto_execution"] = {
                "enabled": auto_exec_cfg,
                "executed": False,
                "reason": stale_msg,
            }
        elif SERVER_STATE.user_config.emergency_stop or SERVER_STATE.broker.emergency_halted:
            rejection_reason = "Emergency stop is active. Automated trading is halted."
            SERVER_STATE.signal_diagnostics.record_rejection(rejection_reason, symbol=mapped_symbol)
            analysis["auto_execution"] = {
                "enabled": auto_exec_cfg,
                "executed": False,
                "reason": rejection_reason,
            }
        elif SERVER_STATE.user_config.new_entries_paused:
            rejection_reason = "New trade entries are currently PAUSED by user."
            SERVER_STATE.signal_diagnostics.record_rejection(rejection_reason, symbol=mapped_symbol)
            analysis["auto_execution"] = {
                "enabled": auto_exec_cfg,
                "executed": False,
                "reason": rejection_reason,
            }
        elif SERVER_STATE.last_executed_signals.get(mapped_symbol) == latest_bar.get("timestamp") or candle_sig in SERVER_STATE.last_executed_signals:
            rejection_reason = f"Duplicate signal already executed for candle timestamp {latest_bar.get('timestamp')}."
            SERVER_STATE.signal_diagnostics.record_rejection(rejection_reason, symbol=mapped_symbol)
            analysis["auto_execution"] = {
                "enabled": auto_exec_cfg,
                "executed": False,
                "reason": rejection_reason,
            }
        elif mapped_symbol in SERVER_STATE.paper_engine.positions:
            rejection_reason = f"MAX POSITIONS REACHED: active position already open for {mapped_symbol}."
            SERVER_STATE.signal_diagnostics.record_rejection(rejection_reason, symbol=mapped_symbol)
            analysis["auto_execution"] = {
                "enabled": auto_exec_cfg,
                "executed": False,
                "reason": rejection_reason,
            }
        elif (now_ts - SERVER_STATE.last_trade_times.get(mapped_symbol, 0)) < SERVER_STATE.user_config.reentry_cooldown_sec:
            rem_sec = int(SERVER_STATE.user_config.reentry_cooldown_sec - (now_ts - SERVER_STATE.last_trade_times.get(mapped_symbol, 0)))
            rejection_reason = f"RE-ENTRY COOLDOWN ACTIVE: {rem_sec}s remaining for {mapped_symbol}."
            SERVER_STATE.signal_diagnostics.record_rejection(rejection_reason, symbol=mapped_symbol)
            analysis["auto_execution"] = {
                "enabled": auto_exec_cfg,
                "executed": False,
                "reason": rejection_reason,
            }
        else:
            is_trade = analysis.get("decision") == "TRADE"

            if is_trade and ticket_data:
                # Cross-instrument check against active context
                active_raw = SERVER_STATE.active_context.get("raw_symbol")
                active_mapped = SERVER_STATE.active_context.get("symbol")
                if SERVER_STATE.active_context.get("synced") and active_raw and (raw_symbol != active_raw and mapped_symbol != active_mapped):
                    rejection_reason = f"CONTEXT MISMATCH: analyzed symbol {raw_symbol} does not match active chart context {active_raw}."
                    SERVER_STATE.signal_diagnostics.record_rejection(rejection_reason, symbol=mapped_symbol)
                    analysis["auto_execution"] = {
                        "enabled": auto_exec_cfg,
                        "executed": False,
                        "reason": rejection_reason,
                    }
                else:
                    ticket_obj = OrderTicket.from_dict(ticket_data)
                    order_res = SERVER_STATE.broker.place_order(ticket_obj, confirm_live=False)
                    if order_res.get("success"):
                        SERVER_STATE.last_executed_signals[mapped_symbol] = latest_bar.get("timestamp")
                        SERVER_STATE.last_executed_signals[candle_sig] = order_res.get("order_id")
                        SERVER_STATE.last_trade_times[mapped_symbol] = now_ts
                        SERVER_STATE.signal_diagnostics.record_execution(
                            order_res.get("order_id"), mapped_symbol, order_res.get("fill_price", 0.0)
                        )
                        analysis["auto_execution"] = {
                            "enabled": True,
                            "executed": True,
                            "order_id": order_res.get("order_id"),
                            "fill_price": order_res.get("fill_price"),
                            "quantity": order_res.get("quantity"),
                            "entry_friction": order_res.get("entry_friction"),
                        }
                        events.append({
                            "event": "AUTO_PAPER_ORDER_FILLED",
                            "order_id": order_res.get("order_id"),
                            "symbol": mapped_symbol,
                            "quantity": order_res.get("quantity"),
                            "fill_price": order_res.get("fill_price"),
                        })
                    else:
                        rejection_reason = order_res.get("reason", "Order rejected by risk gates.")
                        SERVER_STATE.signal_diagnostics.record_rejection(rejection_reason, symbol=mapped_symbol)
                        analysis["auto_execution"] = {
                            "enabled": True,
                            "executed": False,
                            "reason": rejection_reason,
                            "violations": order_res.get("violations", []),
                        }
            else:
                reasons_list = analysis.get("reasons") or []
                no_setup_reason = reasons_list[0] if reasons_list else "No trade setup."
                analysis["auto_execution"] = {
                    "enabled": auto_exec_cfg,
                    "executed": False,
                    "status": "NO_TRADE_SETUP",
                    "reason": no_setup_reason,
                }

        feed_label = health_info.get("provider", source_label)

        self._send_json_response({
            "symbol": raw_symbol,
            "mapped_symbol": mapped_symbol,
            "display_name": sym_info.get("display_name", raw_symbol),
            "feed_label": feed_label,
            "interval": interval,
            "is_proxy": sym_info.get("is_proxy", False),
            "proxy_target": sym_info.get("proxy_target"),
            "proxy_notice": sym_info.get("proxy_notice"),
            "analysis": analysis,
            "market_tick_events": events,
            "stale_data_warning": stale_msg,
            "data_health": health_info,
            "auto_execute_paper": SERVER_STATE.user_config.auto_execute_paper,
            "new_entries_paused": SERVER_STATE.user_config.new_entries_paused,
        })

    def handle_post_close_all(self) -> None:
        global SERVER_STATE
        res = SERVER_STATE.paper_engine.close_all_positions(reason="MANUAL_CLOSE_ALL")
        self._send_json_response(res)

    def handle_post_pause(self, payload: Dict[str, Any]) -> None:
        global SERVER_STATE
        if "paused" in payload:
            SERVER_STATE.user_config.new_entries_paused = bool(payload["paused"])
        else:
            SERVER_STATE.user_config.new_entries_paused = True
        self._send_json_response({
            "success": True,
            "new_entries_paused": SERVER_STATE.user_config.new_entries_paused,
            "message": "New entries paused" if SERVER_STATE.user_config.new_entries_paused else "New entries active",
        })

    def handle_post_resume(self) -> None:
        global SERVER_STATE
        SERVER_STATE.broker.emergency_halted = False
        SERVER_STATE.user_config.emergency_stop = False
        SERVER_STATE.user_config.new_entries_paused = False
        self._send_json_response({
            "success": True,
            "emergency_halted": False,
            "new_entries_paused": False,
            "message": "System resumed. All circuit breakers cleared.",
        })

    def handle_post_order(self, payload: Dict[str, Any]) -> None:
        global SERVER_STATE
        ticket_data = payload.get("ticket")
        confirm_live = bool(payload.get("confirm_live", False))

        if not ticket_data:
            self._send_json_response({"success": False, "reason": "Missing ticket payload in order request"}, status=400)
            return

        ticket = OrderTicket.from_dict(ticket_data)

        # Context freshness and cross-instrument verification
        now_ts = time.time()
        ctx = getattr(SERVER_STATE, "active_context", {}) or {}
        if ctx.get("synced"):
            updated_at = ctx.get("updated_at", 0.0)
            if updated_at > 0 and (now_ts - updated_at) > 60:
                ctx["synced"] = False
                ctx["status"] = "CONTEXT_EXPIRED"
                reason = "ACTIVE CONTEXT EXPIRED: TradingView context older than 60 seconds."
                SERVER_STATE.signal_diagnostics.record_rejection(reason, symbol=ticket.symbol)
                self._send_json_response({"success": False, "reason": reason, "status": "CONTEXT_EXPIRED"}, status=400)
                return

            active_raw = ctx.get("raw_symbol")
            active_mapped = ctx.get("symbol")
            active_canon = ctx.get("canonical_id")
            ticket_tv = ticket.tradingview_symbol
            ticket_sym = ticket.symbol
            ticket_canon = ticket.canonical_id

            is_match = False
            if ticket_tv and active_raw and (ticket_tv.upper() == active_raw.upper() or ticket_tv.upper().replace("DELTA:", "") == active_raw.upper().replace("DELTA:", "")):
                is_match = True
            elif ticket_sym and active_mapped and (ticket_sym.upper() == active_mapped.upper()):
                is_match = True
            elif ticket_canon and active_canon and (ticket_canon.upper() == active_canon.upper()):
                is_match = True

            if not is_match and active_raw:
                reason = f"CONTEXT MISMATCH: order instrument {ticket_tv or ticket_sym} does not match active chart context {active_raw}."
                SERVER_STATE.signal_diagnostics.record_rejection(reason, symbol=ticket.symbol)
                self._send_json_response({"success": False, "reason": reason, "status": "CONTEXT_MISMATCH"}, status=400)
                return

        # Re-entry cooldown check
        last_trade = SERVER_STATE.last_trade_times.get(ticket.symbol, 0)
        cooldown = SERVER_STATE.user_config.reentry_cooldown_sec
        if (now_ts - last_trade) < cooldown:
            rem = int(cooldown - (now_ts - last_trade))
            reason = f"RE-ENTRY COOLDOWN ACTIVE: {rem}s remaining for {ticket.symbol}"
            SERVER_STATE.signal_diagnostics.record_rejection(reason, symbol=ticket.symbol)
            self._send_json_response({"success": False, "reason": reason, "status": "COOLDOWN_ACTIVE"}, status=400)
            return

        # Max positions per instrument check
        if ticket.symbol in SERVER_STATE.paper_engine.positions:
            reason = f"MAX POSITIONS REACHED: active position already open for {ticket.symbol}"
            SERVER_STATE.signal_diagnostics.record_rejection(reason, symbol=ticket.symbol)
            self._send_json_response({"success": False, "reason": reason, "status": "MAX_POSITIONS_REACHED"}, status=400)
            return

        # Stale data safety check: if cached market data for this symbol is explicitly stale or fallback, block new paper orders
        health = SERVER_STATE.market_data.get_data_health(ticket.symbol, "15m") if hasattr(SERVER_STATE.market_data, "get_data_health") else None
        if health and health.get("candle_count", 0) > 0:
            if health.get("is_fallback"):
                reason = f"Cached fallback market data detected ({health.get('status')}). Orders blocked until fresh data arrives."
                SERVER_STATE.signal_diagnostics.record_rejection(reason, symbol=ticket.symbol)
                self._send_json_response({
                    "success": False,
                    "reason": reason,
                    "status": "CACHED_FALLBACK"
                }, status=400)
                return
            if health.get("is_stale"):
                reason = f"Stale market data detected ({health.get('status')}, age {health.get('data_age_sec', 0)}s). Orders blocked until fresh data arrives."
                SERVER_STATE.signal_diagnostics.record_rejection(reason, symbol=ticket.symbol)
                self._send_json_response({
                    "success": False,
                    "reason": reason,
                    "status": "STALE_DATA"
                }, status=400)
                return

        result = SERVER_STATE.broker.place_order(ticket, confirm_live=confirm_live)
        if result.get("success"):
            SERVER_STATE.last_trade_times[ticket.symbol] = now_ts
            SERVER_STATE.signal_diagnostics.record_execution(
                result.get("order_id", ""), ticket.symbol, result.get("fill_price", 0.0)
            )
        else:
            SERVER_STATE.signal_diagnostics.record_rejection(result.get("reason", "Order rejected by risk gates."), symbol=ticket.symbol)
        self._send_json_response(result)

    def handle_get_positions(self) -> None:
        global SERVER_STATE
        positions = SERVER_STATE.broker.get_positions()
        self._send_json_response({"positions": positions})

    def handle_get_orders(self, query: Dict[str, List[str]]) -> None:
        global SERVER_STATE
        status = query.get("status", ["OPEN"])[0]
        orders = SERVER_STATE.broker.get_orders(status=status)
        self._send_json_response({"orders": orders})

    def handle_post_close_position(self, payload: Dict[str, Any]) -> None:
        global SERVER_STATE
        symbol = payload.get("symbol")
        if not symbol:
            self._send_json_response({"success": False, "reason": "Missing symbol to close"}, status=400)
            return

        if symbol in SERVER_STATE.paper_engine.positions:
            pos = SERVER_STATE.paper_engine.positions[symbol]
            res = SERVER_STATE.paper_engine.close_position(
                symbol=symbol,
                exit_price=pos["current_price"],
                reason="MANUAL_WEB_CLOSE",
            )
            self._send_json_response(res)
        else:
            self._send_json_response({"success": False, "reason": f"No active position for {symbol}"}, status=404)

    def handle_post_cancel_all(self) -> None:
        global SERVER_STATE
        count = SERVER_STATE.broker.cancel_all_orders()
        self._send_json_response({"success": True, "cancelled_orders": count})

    def handle_post_emergency_stop(self) -> None:
        global SERVER_STATE
        res = SERVER_STATE.broker.emergency_stop()
        self._send_json_response(res)

    def handle_post_reset(self, payload: Dict[str, Any]) -> None:
        global SERVER_STATE
        confirm = bool(payload.get("confirm", False))
        init_balance = payload.get("initial_balance")
        res = SERVER_STATE.paper_engine.reset_account(
            confirm=confirm,
            initial_balance=float(init_balance) if init_balance else None,
        )
        self._send_json_response(res)

    def handle_get_trades(self) -> None:
        global SERVER_STATE
        snapshot = SERVER_STATE.paper_engine.get_state()
        self._send_json_response({
            "stats": snapshot.get("stats", {}),
            "trade_history": snapshot.get("trade_history", []),
        })


def run_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Launch the Copilot HTTP server."""
    global SERVER_STATE
    SERVER_STATE = CopilotServerState()

    socketserver.TCPServer.allow_reuse_address = True
    server_address = (host, port)
    httpd = socketserver.TCPServer(server_address, CopilotRequestHandler)
    print(f"================================================================")
    print(f"AI Trading Copilot Server running on http://{host}:{port}")
    print(f"Web Dashboard: http://{host}:{port}/")
    print(f"API Base:      http://{host}:{port}/api/status")
    print(f"Mode:          {SERVER_STATE.user_config.mode.upper()}")
    print(f"Currency:      {SERVER_STATE.user_config.currency}")
    print(f"Planned R:R:   Minimum 3.0:1 NET of statutory fees and slippage")
    print(f"================================================================")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nCopilot Server shutting down gracefully...")
        httpd.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Trading Copilot Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind")
    parser.add_argument("--port", type=int, default=8765, help="Port to listen on")
    args = parser.parse_args()
    run_server(args.host, args.port)

