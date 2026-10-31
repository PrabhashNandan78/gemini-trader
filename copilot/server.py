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
        payload = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(payload)

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
            self._send_json_response({"error": f"API endpoint not found: {path}"}, status=404)
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
        symbol = query.get("symbol", ["RELIANCE.NS"])[0].upper()
        market = query.get("market", ["NSE"])[0].upper()
        interval = query.get("interval", ["5m"])[0]
        count = int(query.get("count", ["80"])[0])

        # Attempt to get real bars from MarketDataProvider, fallback to synthetic
        bars = []
        source = "live_provider"
        try:
            _, fetched_bars, _ = SERVER_STATE.market_data.get_bars(symbol=symbol, range_str="5d", interval=interval)
            if fetched_bars and len(fetched_bars) >= 20:
                bars = fetched_bars[-count:]
        except Exception:
            bars = []

        if not bars:
            source = "synthetic_simulator"
            bars = generate_synthetic_bars(symbol, count=count)

        # Compute quantitative indicators
        closes = [b["close"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]

        ema9 = calc_ema(closes, 9)
        ema21 = calc_ema(closes, 21)
        ema50 = calc_ema(closes, 50)
        bb = calc_bollinger_bands(closes, period=20, std_dev=2.0)
        atr14 = calc_atr(highs, lows, closes, period=14)
        rsi14 = calc_rsi(closes, period=14)
        adx14 = calc_adx(highs, lows, closes, period=14)

        # Detect regime
        regime_info = MarketRegimeDetector.detect_regime(bars)

        # Attach computed indicators to bars
        chart_bars = []
        for i, b in enumerate(bars):
            chart_bars.append({
                "time": b["timestamp"],
                "datetime": b.get("datetime_local", ""),
                "open": b["open"],
                "high": b["high"],
                "low": b["low"],
                "close": b["close"],
                "volume": b.get("volume", 0),
                "ema9": round(ema9[i], 2) if i < len(ema9) and ema9[i] is not None else None,
                "ema21": round(ema21[i], 2) if i < len(ema21) and ema21[i] is not None else None,
                "ema50": round(ema50[i], 2) if i < len(ema50) and ema50[i] is not None else None,
                "bb_upper": round(bb["upper"][i], 2) if i < len(bb["upper"]) and bb["upper"][i] is not None else None,
                "bb_middle": round(bb["middle"][i], 2) if i < len(bb["middle"]) and bb["middle"][i] is not None else None,
                "bb_lower": round(bb["lower"][i], 2) if i < len(bb["lower"]) and bb["lower"][i] is not None else None,
                "rsi": round(rsi14[i], 2) if i < len(rsi14) and rsi14[i] is not None else None,
                "adx": round(adx14[i], 2) if i < len(adx14) and adx14[i] is not None else None,
            })

        self._send_json_response({
            "symbol": symbol,
            "market": market,
            "source": source,
            "regime": regime_info,
            "bars_count": len(chart_bars),
            "bars": chart_bars,
        })

    def handle_post_analyze(self, payload: Dict[str, Any]) -> None:
        global SERVER_STATE
        symbol = payload.get("symbol", "RELIANCE.NS").upper()
        market = payload.get("market", SERVER_STATE.user_config.market).upper()

        # Get bars
        bars = []
        try:
            _, fetched_bars, _ = SERVER_STATE.market_data.get_bars(symbol=symbol, range_str="5d", interval="5m")
            if fetched_bars and len(fetched_bars) >= 20:
                bars = fetched_bars
        except Exception:
            bars = []

        if not bars:
            bars = generate_synthetic_bars(symbol, count=80)

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
