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
import socketserver
import sys
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
from engine.market_data import MarketDataProvider, MarketDataError


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
        self.market_data = MarketDataProvider()
        self.strategy_engine = AdaptiveStrategyEngine()

    def update_config(self, new_params: Dict[str, Any]) -> UserRiskConfig:
        for k, v in new_params.items():
            if hasattr(self.user_config, k):
                # Type cast
                field_type = type(getattr(self.user_config, k))
                setattr(self.user_config, k, field_type(v))

        # Re-sync broker if mode or broker name changed
        self.broker = create_broker_adapter(
            broker_name=self.user_config.broker,
            mode=self.user_config.mode,
            user_config=self.user_config,
            paper_engine=self.paper_engine,
        )
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


class CopilotRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Custom HTTP Request Handler serving REST API endpoints and web UI."""

    def __init__(self, *args, **kwargs):
        # Serve static assets from copilot/web
        web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
        super().__init__(*args, directory=web_dir, **kwargs)

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, DELETE")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With")

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
        elif path == "/api/health":
            self.handle_get_health()
        elif path == "/api/config":
            self.handle_get_config()
        elif path == "/api/chart":
            self.handle_get_chart(query)
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
        elif path == "/api/analyze":
            self.handle_post_analyze(payload)
        elif path == "/api/order":
            self.handle_post_order(payload)
        elif path == "/api/close_position":
            self.handle_post_close_position(payload)
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
            "stats": state_snapshot.get("stats", {}),
        })

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

    def handle_get_chart(self, query: Dict[str, List[str]]) -> None:
        global SERVER_STATE
        symbols = query.get("symbol")
        if not symbols or not symbols[0].strip():
            self._send_json_response({"error": "Missing or empty 'symbol' parameter", "status": "ERROR"}, status=400)
            return

        symbol = symbols[0].strip().upper()
        market = query.get("market", ["NSE"])[0].upper()
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
                "symbol": symbol,
                "status": "ERROR"
            }, status=400)
            return

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
        try:
            _, fetched_bars, meta = SERVER_STATE.market_data.get_bars(symbol=symbol, range_str=range_str, interval=interval)
            if fetched_bars:
                bars = fetched_bars[-count:]
        except MarketDataError as e:
            self._send_json_response({
                "error": f"Market data provider error for {symbol}: {str(e)}",
                "symbol": symbol,
                "market": market,
                "status": "ERROR"
            }, status=502)
            return
        except Exception as e:
            self._send_json_response({
                "error": f"Failed to retrieve market data for {symbol}: {str(e)}",
                "symbol": symbol,
                "market": market,
                "status": "ERROR"
            }, status=502)
            return

        if not bars or len(bars) < 5:
            self._send_json_response({
                "error": f"No candles available for {symbol} at interval {interval}",
                "symbol": symbol,
                "market": market,
                "status": "NO_DATA"
            }, status=404)
            return

        # Accurate data source labeling
        if symbol.endswith(".NS") or symbol.endswith(".BO") or market == "NSE":
            source_label = "Delayed (15m NSE Feed)"
        elif any(c in symbol for c in ["-USD", "/USD", "USDT"]) or market in ("CRYPTO", "BINANCE"):
            source_label = "Live 24/7 Spot"
        else:
            source_label = "Live / Delayed US Feed"

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

        self._send_json_response({
            "symbol": symbol,
            "market": market,
            "source": source_label,
            "regime": regime_info,
            "bars_count": len(chart_bars),
            "bars": chart_bars,
        })

    def handle_post_analyze(self, payload: Dict[str, Any]) -> None:
        global SERVER_STATE
        symbol = str(payload.get("symbol", "RELIANCE.NS")).strip().upper()
        market = str(payload.get("market", SERVER_STATE.user_config.market)).strip().upper()

        # Fetch real bars
        bars = []
        try:
            _, fetched_bars, _ = SERVER_STATE.market_data.get_bars(symbol=symbol, range_str="5d", interval="5m")
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
                    "reasons": [f"Market data provider error for {symbol}: {str(e)}"],
                },
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
                    "reasons": [f"Insufficient historical bars ({len(bars)}/20) for {symbol} to establish statistical regime."],
                },
                "market_tick_events": [],
            }, status=404)
            return

        # Run adaptive strategy engine
        analysis = SERVER_STATE.strategy_engine.analyze_and_generate_ticket(
            bars=bars,
            symbol=symbol,
            market=market,
            user_config=SERVER_STATE.user_config,
        )

        # Feed latest tick into paper engine to trigger pending stops/targets
        latest_bar = bars[-1]
        events = SERVER_STATE.paper_engine.process_market_tick(symbol, latest_bar)

        self._send_json_response({
            "analysis": analysis,
            "market_tick_events": events,
        })
        events = SERVER_STATE.paper_engine.process_market_tick(symbol, latest_bar)

        self._send_json_response({
            "analysis": analysis,
            "market_tick_events": events,
        })

    def handle_post_order(self, payload: Dict[str, Any]) -> None:
        global SERVER_STATE
        ticket_data = payload.get("ticket")
        confirm_live = bool(payload.get("confirm_live", False))

        if not ticket_data:
            self._send_json_response({"success": False, "reason": "Missing ticket payload in order request"}, status=400)
            return

        ticket = OrderTicket.from_dict(ticket_data)
        result = SERVER_STATE.broker.place_order(ticket, confirm_live=confirm_live)
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

