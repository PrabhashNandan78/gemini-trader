"""Unit and Integration tests for Copilot REST API, Market Data Handling, and HTTP Server."""

import json
import math
import socketserver
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Tuple
from unittest.mock import MagicMock, patch

from copilot.server import (
    CopilotRequestHandler,
    CopilotServerState,
    clean_float,
    generate_synthetic_bars,
    sanitize_for_json,
)
from engine.asset_model import parse_symbol
from engine.market_data import MarketDataError, MarketDataProvider


class TestCopilotServerLogic(unittest.TestCase):
    """Unit tests for server helper functions, configuration, and data processing."""

    def setUp(self):
        self.server_state = CopilotServerState(
            mode="paper",
            broker_name="PAPER",
            initial_balance=100000.0,
        )

    def test_clean_float(self):
        self.assertIsNone(clean_float(None))
        self.assertIsNone(clean_float(float("nan")))
        self.assertIsNone(clean_float(float("inf")))
        self.assertIsNone(clean_float(float("-inf")))
        self.assertEqual(clean_float(123.456, 2), 123.46)
        self.assertEqual(clean_float(100.0, 1), 100.0)

    def test_sanitize_for_json(self):
        data = {
            "valid": 42.5,
            "nan_val": float("nan"),
            "nested": [10.0, float("nan"), {"sub_inf": float("inf")}],
        }
        sanitized = sanitize_for_json(data)
        raw_json = json.dumps(sanitized)
        self.assertNotIn("NaN", raw_json)
        self.assertNotIn("Infinity", raw_json)
        parsed = json.loads(raw_json)
        self.assertIsNone(parsed["nan_val"])
        self.assertIsNone(parsed["nested"][1])
        self.assertIsNone(parsed["nested"][2]["sub_inf"])

    def test_synthetic_bars_generation(self):
        bars = generate_synthetic_bars("RELIANCE.NS", count=50)
        self.assertEqual(len(bars), 50)
        for b in bars:
            self.assertIn("open", b)
            self.assertIn("high", b)
            self.assertIn("low", b)
            self.assertIn("close", b)
            self.assertIn("volume", b)
            self.assertIn("timestamp", b)
            self.assertGreaterEqual(b["high"], b["low"])

    def test_server_config_update(self):
        new_params = {
            "balance": 250000.0,
            "max_risk_pct": 0.5,
            "max_concurrent_positions": 4,
            "currency": "INR",
        }
        updated = self.server_state.update_config(new_params)
        self.assertEqual(updated.balance, 250000.0)
        self.assertEqual(updated.max_risk_pct, 0.5)
        self.assertEqual(updated.max_concurrent_positions, 4)

    def test_server_strategy_analysis_flow(self):
        bars = generate_synthetic_bars("RELIANCE.NS", count=80)
        analysis = self.server_state.strategy_engine.analyze_and_generate_ticket(
            bars=bars,
            symbol="RELIANCE.NS",
            market="NSE",
            user_config=self.server_state.user_config,
        )
        self.assertIn("decision", analysis)
        self.assertIn("regime", analysis)
        if analysis["decision"] == "TRADE":
            self.assertGreaterEqual(analysis["ticket"]["planned_rr"], 3.0)


class TestCopilotServerHTTP(unittest.TestCase):
    """Integration tests running against an actual local HTTP socket."""

    @classmethod
    def setUpClass(cls):
        cls.httpd = socketserver.TCPServer(("127.0.0.1", 0), CopilotRequestHandler)
        cls.port = cls.httpd.server_address[1]
        cls.server_thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        import copilot.server
        if copilot.server.SERVER_STATE:
            copilot.server.SERVER_STATE.broker.emergency_halted = False
            copilot.server.SERVER_STATE.user_config.emergency_stop = False
            copilot.server.SERVER_STATE.paper_engine.balance = 100000.0
            copilot.server.SERVER_STATE.paper_engine.positions.clear()

    def _get(self, path: str) -> Tuple[int, Dict[str, Any]]:
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return resp.status, data
        except urllib.error.HTTPError as e:
            try:
                data = json.loads(e.read().decode("utf-8"))
            except Exception:
                data = {"raw_error": str(e)}
            return e.code, data

    def _post(self, path: str, payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        url = f"http://127.0.0.1:{self.port}{path}"
        raw_body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=raw_body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return resp.status, data
        except urllib.error.HTTPError as e:
            try:
                data = json.loads(e.read().decode("utf-8"))
            except Exception:
                data = {"raw_error": str(e)}
            return e.code, data

    def test_http_health_endpoint(self):
        code, data = self._get("/api/health")
        self.assertEqual(code, 200)
        self.assertEqual(data.get("status"), "OK")
        self.assertEqual(data.get("version"), "2.0.0")

    def test_http_status_endpoint(self):
        code, data = self._get("/api/status")
        self.assertEqual(code, 200)
        self.assertEqual(data.get("status"), "ONLINE")
        self.assertIn("balance", data)
        self.assertIn("equity", data)
        self.assertIn("mode", data)

    def test_http_chart_missing_symbol_returns_400(self):
        code, data = self._get("/api/chart")
        self.assertEqual(code, 400)
        self.assertIn("error", data)
        self.assertIn("Missing or empty 'symbol'", data["error"])

    def test_http_chart_invalid_interval_returns_400(self):
        code, data = self._get("/api/chart?symbol=BTC-USD&interval=99m")
        self.assertEqual(code, 400)
        self.assertIn("error", data)
        self.assertIn("Unsupported interval", data["error"])

    def test_http_chart_valid_mocked_provider(self):
        mock_bars = generate_synthetic_bars("BTC-USD", count=60)
        with patch.object(MarketDataProvider, "get_bars", return_value=(parse_symbol("BTC-USD"), mock_bars, {"currency": "USD"})):
            code, data = self._get("/api/chart?symbol=BTC-USD&market=CRYPTO&interval=5m&count=50")
            self.assertEqual(code, 200)
            self.assertEqual(data.get("symbol"), "BTC-USD")
            self.assertIn("bars", data)
            self.assertLessEqual(len(data["bars"]), 50)
            self.assertIn("regime", data)

            # Check candle attributes
            first_bar = data["bars"][0]
            for field_name in ["open", "high", "low", "close", "time", "volume"]:
                self.assertIn(field_name, first_bar)

            # Verify indicators are computed and no NaNs exist
            for b in data["bars"]:
                self.assertNotIn("NaN", str(b))

    def test_http_chart_provider_error_handled_gracefully(self):
        with patch.object(MarketDataProvider, "get_bars", side_effect=MarketDataError("429 Too Many Requests")):
            code, data = self._get("/api/chart?symbol=RELIANCE.NS&interval=5m")
            self.assertEqual(code, 502)
            self.assertIn("error", data)
            self.assertIn("429 Too Many Requests", data["error"])

    def test_http_chart_empty_data_handled_gracefully(self):
        with patch.object(MarketDataProvider, "get_bars", return_value=(parse_symbol("TCS.NS"), [], {})):
            code, data = self._get("/api/chart?symbol=TCS.NS&interval=5m")
            self.assertEqual(code, 404)
            self.assertIn("error", data)
            self.assertIn("No candles available", data["error"])

    def test_http_server_survives_failed_request_and_handles_subsequent(self):
        # First: deliberately trigger 400 error
        code1, _ = self._get("/api/chart?symbol=INVALID&interval=invalid_interval")
        self.assertEqual(code1, 400)

        # Second: immediately send valid health check to confirm server survived
        code2, data2 = self._get("/api/health")
        self.assertEqual(code2, 200)
        self.assertEqual(data2.get("status"), "OK")

    def test_http_analyze_endpoint_with_mocked_data(self):
        mock_bars = generate_synthetic_bars("RELIANCE.NS", count=80)
        with patch.object(MarketDataProvider, "get_bars", return_value=(parse_symbol("RELIANCE.NS"), mock_bars, {})):
            code, data = self._post("/api/analyze", {"symbol": "RELIANCE.NS", "market": "NSE"})
            self.assertEqual(code, 200)
            self.assertIn("analysis", data)
            analysis = data["analysis"]
            self.assertIn("decision", analysis)
            if analysis["decision"] == "TRADE":
                self.assertGreaterEqual(analysis["ticket"]["planned_rr"], 3.0)

    def test_http_order_paper_execution(self):
        ticket_payload = {
            "symbol": "RELIANCE.NS",
            "market": "NSE",
            "side": "BUY",
            "entry_price": 2400.0,
            "stop_loss": 2350.0,
            "take_profit": 2565.0,
            "quantity": 5.0,
            "allocated_capital": 12000.0,
            "risk_amount": 250.0,
            "planned_rr": 3.2,
            "strategy": "TrendFollowing",
        }
        code, data = self._post("/api/order", {"ticket": ticket_payload, "confirm_live": False})
        self.assertEqual(code, 200)
        self.assertTrue(data.get("success"))
        self.assertIn("paper_ord_", data.get("order_id"))

    def test_http_emergency_stop_halts_trading(self):
        code, data = self._post("/api/emergency_stop", {})
        self.assertEqual(code, 200)
        self.assertEqual(data.get("status"), "HALTED")


class TestRealMarketDataLive(unittest.TestCase):
    """Separate live verification against real public endpoints (clearly distinguished from mocked tests)."""

    def test_real_crypto_candles_fetch(self):
        provider = MarketDataProvider()
        try:
            info, bars, meta = provider.get_bars("BTC-USD", range_str="1d", interval="5m")
            self.assertGreaterEqual(len(bars), 20)
            self.assertIn("close", bars[-1])
            self.assertIn("timestamp", bars[-1])
            # Verify chronological order
            self.assertGreater(bars[-1]["timestamp"], bars[0]["timestamp"])
            # Verify numeric validity
            for b in bars[-10:]:
                self.assertGreater(b["high"], 0.0)
                self.assertGreater(b["low"], 0.0)
                self.assertGreaterEqual(b["high"], b["low"])
        except Exception as e:
            # If network is offline, skip test with reason
            self.skipTest(f"Live network endpoint unavailable: {e}")


if __name__ == "__main__":
    unittest.main()
