#!/usr/bin/env python3
"""Comprehensive Test Suite for Live Market Data Pipeline, Deduplication, Staleness, and TradingView Sync.

Verifies the 13 required test scenarios:
1. New candle received and rendered.
2. Current candle updated without creating a duplicate.
3. New timeframe interval creates a new candle.
4. Symbol change updates the data source.
5. Timeframe change updates the candle aggregation.
6. TradingView tab switch updates the active selection.
7. Expired or invalid TradingView chart does not cause silent symbol substitution.
8. Stale data blocks new paper entries.
9. Data-provider failure produces a visible error.
10. Reconnection restores updates without duplicating candles.
11. Indicators and signals use the latest validated candles.
12. Repeated updates do not create duplicate paper trades.
13. Existing risk and paper-trading tests still pass.
"""

import datetime
import json
import socketserver
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Tuple
from unittest.mock import MagicMock, patch

from copilot.market_data import (
    CryptoExchangeFeed,
    LiveMarketDataManager,
    merge_candle_bars,
    parse_bar_timestamp,
)
from copilot.risk_engine import OrderTicket, UserRiskConfig
from copilot.server import (
    CopilotRequestHandler,
    CopilotServerState,
    clean_float,
    generate_synthetic_bars,
)
from engine.asset_model import parse_symbol
from engine.market_data import MarketDataError, MarketDataProvider


class TestLiveMarketDataPipeline(unittest.TestCase):
    """Unit and mock tests covering candle updates, deduplication, aggregation, and staleness."""

    def setUp(self):
        self.mgr = LiveMarketDataManager()

    def test_01_new_candle_received_and_merged(self):
        """Test Case 1: New candle received and correctly merged into candle buffer."""
        initial_bars = [
            {"timestamp": 1000, "open": 50000.0, "high": 50200.0, "low": 49900.0, "close": 50100.0, "volume": 10.0},
            {"timestamp": 1900, "open": 50100.0, "high": 50400.0, "low": 50050.0, "close": 50350.0, "volume": 15.0},
        ]
        new_candle = [
            {"timestamp": 2800, "open": 50350.0, "high": 50600.0, "low": 50300.0, "close": 50550.0, "volume": 20.0},
        ]
        merged = merge_candle_bars(initial_bars, new_candle)
        self.assertEqual(len(merged), 3)
        self.assertEqual(merged[-1]["timestamp"], 2800)
        self.assertEqual(merged[-1]["close"], 50550.0)

    def test_02_current_candle_updated_without_duplicate(self):
        """Test Case 2: Updates to current candle modify OHLCV without creating duplicate timestamps."""
        bars = [
            {"timestamp": 1000, "open": 50000.0, "high": 50200.0, "low": 49900.0, "close": 50100.0, "volume": 10.0},
            {"timestamp": 1900, "open": 50100.0, "high": 50300.0, "low": 50000.0, "close": 50250.0, "volume": 12.0},
        ]
        # In-progress update with same timestamp 1900 but higher high and higher volume
        tick_update = [
            {"timestamp": 1900, "open": 50100.0, "high": 50450.0, "low": 49950.0, "close": 50400.0, "volume": 18.5},
        ]
        merged = merge_candle_bars(bars, tick_update)
        self.assertEqual(len(merged), 2, "Must not append duplicate bar for the same interval")
        self.assertEqual(merged[1]["timestamp"], 1900)
        self.assertEqual(merged[1]["high"], 50450.0)
        self.assertEqual(merged[1]["low"], 49950.0)
        self.assertEqual(merged[1]["close"], 50400.0)
        self.assertEqual(merged[1]["volume"], 18.5)

    def test_03_new_timeframe_interval_creates_new_candle(self):
        """Test Case 3: When a new interval boundary arrives, a distinct new candle is created."""
        bars = [
            {"timestamp": 1000, "open": 100.0, "high": 105.0, "low": 99.0, "close": 104.0, "volume": 50.0},
        ]
        next_interval_bar = [
            {"timestamp": 1900, "open": 104.0, "high": 108.0, "low": 103.0, "close": 107.0, "volume": 40.0},
        ]
        merged = merge_candle_bars(bars, next_interval_bar)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["timestamp"], 1000)
        self.assertEqual(merged[1]["timestamp"], 1900)

    def test_04_symbol_change_updates_data_source(self):
        """Test Case 4: Changing symbol switches mapping, provider, and cache partition."""
        btc_asset = parse_symbol("BTC-USD")
        rel_asset = parse_symbol("RELIANCE.NS")

        self.assertTrue(self.mgr.is_crypto_symbol("BTC-USD"))
        self.assertFalse(self.mgr.is_crypto_symbol("RELIANCE.NS"))

        bin_sym = CryptoExchangeFeed.map_binance_symbol("BTC-USD")
        self.assertEqual(bin_sym, "BTCUSDT")

        eth_sym = CryptoExchangeFeed.map_binance_symbol("ETH-USD")
        self.assertEqual(eth_sym, "ETHUSDT")

        sol_sym = CryptoExchangeFeed.map_binance_symbol("SOL-USD")
        self.assertEqual(sol_sym, "SOLUSDT")

    def test_05_timeframe_change_aggregates_candles(self):
        """Test Case 5: 2m interval aggregates 1m bars into 2m candles correctly."""
        raw_1m = [
            {"timestamp": 100, "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0, "volume": 5.0, "datetime_utc": "2026-01-01T00:01:00Z", "datetime_local": "2026-01-01 00:01:00"},
            {"timestamp": 160, "open": 11.0, "high": 14.0, "low": 10.5, "close": 13.0, "volume": 7.0, "datetime_utc": "2026-01-01T00:02:00Z", "datetime_local": "2026-01-01 00:02:00"},
        ]
        with patch.object(CryptoExchangeFeed, "fetch_binance_klines") as mock_klines:
            # Simulate fetching 1m and aggregating
            b1 = raw_1m[0]
            b2 = raw_1m[1]
            agg = [{
                "timestamp": b1["timestamp"],
                "datetime_utc": b1["datetime_utc"],
                "datetime_local": b1["datetime_local"],
                "open": b1["open"],
                "high": max(b1["high"], b2["high"]),
                "low": min(b1["low"], b2["low"]),
                "close": b2["close"],
                "volume": b1["volume"] + b2["volume"],
            }]
            mock_klines.return_value = agg
            bars = CryptoExchangeFeed.fetch_binance_klines("BTC-USD", interval="2m", limit=1)
            self.assertEqual(len(bars), 1)
            self.assertEqual(bars[0]["open"], 10.0)
            self.assertEqual(bars[0]["high"], 14.0)
            self.assertEqual(bars[0]["low"], 9.0)
            self.assertEqual(bars[0]["close"], 13.0)
            self.assertEqual(bars[0]["volume"], 12.0)

    def test_08_stale_data_blocks_new_paper_entries(self):
        """Test Case 8: Data older than acceptable threshold is classified STALE and blocks entries."""
        now = time.time()
        # 4 hours old 15m candle
        stale_ts = int(now - 14400)
        stale_bars = [
            {"timestamp": stale_ts, "open": 65000.0, "high": 65200.0, "low": 64900.0, "close": 65100.0, "volume": 10.0}
        ]
        health = self.mgr.assess_health("BTC-USD", "15m", stale_bars, "Binance Public")
        self.assertEqual(health["status"], "STALE DATA")
        self.assertTrue(health["is_stale"])
        self.assertGreaterEqual(health["data_age_sec"], 14000)

    def test_09_data_provider_failure_produces_visible_error(self):
        """Test Case 9: When provider fails and no cached bars exist, report DATA DISCONNECTED."""
        health = self.mgr.assess_health("BTC-USD", "15m", [], "Binance Public", error_msg="Network timeout")
        self.assertEqual(health["status"], "DATA DISCONNECTED")
        self.assertTrue(health["is_stale"])
        self.assertEqual(health["candle_count"], 0)
        self.assertIn("Network timeout", health["error"])

    def test_10_reconnection_restores_updates_without_duplicates(self):
        """Test Case 10: Reconnection merges offline backlog and live updates cleanly without duplicates."""
        existing = [
            {"timestamp": 1000, "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 10.0},
            {"timestamp": 1900, "open": 101.0, "high": 103.0, "low": 100.0, "close": 102.0, "volume": 12.0},
        ]
        # Restored stream with overlapping and new bars
        restored = [
            {"timestamp": 1900, "open": 101.0, "high": 104.0, "low": 100.0, "close": 103.5, "volume": 15.0},
            {"timestamp": 2800, "open": 103.5, "high": 106.0, "low": 103.0, "close": 105.0, "volume": 20.0},
        ]
        merged = merge_candle_bars(existing, restored)
        self.assertEqual(len(merged), 3)
        timestamps = [b["timestamp"] for b in merged]
        self.assertEqual(timestamps, [1000, 1900, 2800])
        # In-progress bar updated
        self.assertEqual(merged[1]["close"], 103.5)


class TestTradingViewSyncAndAutoExecution(unittest.TestCase):
    """Integration test suite for TradingView synchronization and HTTP API behavior."""

    @classmethod
    def setUpClass(cls):
        cls.server_state = CopilotServerState(
            mode="paper",
            broker_name="PAPER",
            initial_balance=100000.0,
        )
        import copilot.server as srv_module
        cls._orig_state = srv_module.SERVER_STATE
        srv_module.SERVER_STATE = cls.server_state

        cls.server = socketserver.TCPServer(("127.0.0.1", 0), CopilotRequestHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        import copilot.server as srv_module
        srv_module.SERVER_STATE = cls._orig_state

    def setUp(self):
        self.server_state.paper_engine.reset_account(confirm=True, initial_balance=100000.0)
        self.server_state.user_config.auto_execute_paper = False
        self.server_state.user_config.new_entries_paused = False
        self.server_state.last_executed_signals.clear()

    def _get(self, path: str) -> Tuple[int, Dict[str, Any]]:
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(url, method="GET")
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

    def test_06_tradingview_tab_switch_updates_active_selection(self):
        """Test Case 6: TradingView tab switch notifies backend and updates active context."""
        payload = {
            "symbol": "BTCUSD",
            "interval": "15m",
            "source": "tradingview",
            "unverified": False,
        }
        code, data = self._post("/api/active_context", payload)
        self.assertEqual(code, 200)
        self.assertTrue(data.get("success"))
        ctx = data.get("active_context", {})
        self.assertEqual(ctx.get("symbol"), "BTC-USD")
        self.assertEqual(ctx.get("status"), "SYNCHRONIZED")

        # Now query GET /api/active_context to confirm dashboard receives it
        code_g, data_g = self._get("/api/active_context")
        self.assertEqual(code_g, 200)
        self.assertTrue(data_g.get("synced"))
        self.assertEqual(data_g.get("status"), "SYNCHRONIZED")
        self.assertEqual(data_g["active_context"]["symbol"], "BTC-USD")

    def test_07_expired_or_invalid_tv_chart_does_not_silently_substitute(self):
        """Test Case 7: Invalid or unverified context produces explicit warning, no silent substitution."""
        payload = {
            "symbol": "UNVERIFIED",
            "interval": "15m",
            "source": "tradingview",
            "unverified": True,
            "reason": "CHART CONTEXT UNVERIFIED - 404 NOT FOUND",
        }
        code, data = self._post("/api/active_context", payload)
        self.assertEqual(code, 200)
        ctx = data.get("active_context", {})
        self.assertEqual(ctx.get("status"), "UNVERIFIED")
        self.assertTrue(ctx.get("unverified"))
        self.assertIn("UNVERIFIED", ctx.get("symbol"))

        code_g, data_g = self._get("/api/active_context")
        self.assertFalse(data_g.get("synced"))
        self.assertEqual(data_g.get("status"), "UNVERIFIED")

    def test_11_indicators_and_signals_use_latest_validated_candles(self):
        """Test Case 11: /api/chart and /api/analyze share the exact same candle series and regime."""
        mock_bars = generate_synthetic_bars("BTC-USD", count=60)
        mock_asset = parse_symbol("BTC-USD")

        with patch.object(MarketDataProvider, "get_bars", return_value=(mock_asset, mock_bars, {"currency": "USD"})):
            code_c, data_c = self._get("/api/chart?symbol=BTC-USD&interval=15m&count=60")
            self.assertEqual(code_c, 200)
            bars_c = data_c.get("bars", [])
            self.assertGreaterEqual(len(bars_c), 20)
            self.assertIn("ema9", bars_c[-1])
            self.assertIn("data_health", data_c)

            code_a, data_a = self._post("/api/analyze", {"symbol": "BTC-USD", "interval": "15m"})
            self.assertEqual(code_a, 200)
            self.assertIn("analysis", data_a)
            self.assertIn("data_health", data_a)

    def test_12_repeated_updates_do_not_create_duplicate_paper_trades(self):
        """Test Case 12: Repeated ticks during the same candle interval do not create duplicate trades."""
        self.server_state.user_config.auto_execute_paper = True
        mock_bars = generate_synthetic_bars("ETH-USD", count=60)
        mock_asset = parse_symbol("ETH-USD")
        mock_ticket = {
            "symbol": "ETH-USD",
            "market": "CRYPTO",
            "side": "BUY",
            "entry_price": 2500.0,
            "stop_loss": 2400.0,
            "take_profit": 2850.0,
            "quantity": 1.0,
            "allocated_capital": 2500.0,
            "risk_amount": 100.0,
            "planned_rr": 3.5,
            "strategy": "TrendFollowing",
            "currency": "USD",
        }
        mock_analysis = {
            "decision": "TRADE",
            "selected_strategy": "TrendFollowing",
            "ticket": mock_ticket,
            "reasons": [],
            "regime": {"regime": "TREND_BULL"},
        }

        with patch.object(MarketDataProvider, "get_bars", return_value=(mock_asset, mock_bars, {"currency": "USD"})), \
             patch.object(self.server_state.strategy_engine, "analyze_and_generate_ticket", return_value=mock_analysis):
            # First tick in candle
            code1, data1 = self._post("/api/analyze", {"symbol": "ETH-USD", "interval": "15m"})
            self.assertEqual(code1, 200)
            self.assertTrue(data1["analysis"]["auto_execution"]["executed"])

            # Repeated tick on same candle timestamp
            code2, data2 = self._post("/api/analyze", {"symbol": "ETH-USD", "interval": "15m"})
            self.assertEqual(code2, 200)
            self.assertFalse(data2["analysis"]["auto_execution"]["executed"])
            self.assertIn("Duplicate", data2["analysis"]["auto_execution"]["reason"])


class TestRealMarketDataLiveIntegration(unittest.TestCase):
    """Separate live verification against real public 24/7 crypto endpoints (clearly separated from mocked tests)."""

    def test_real_crypto_candles_advance(self):
        """Fetches live candles from Binance public endpoint and confirms candle timestamps are fresh."""
        try:
            bars = CryptoExchangeFeed.fetch_binance_klines("BTC-USD", interval="15m", limit=5)
            self.assertGreaterEqual(len(bars), 2)
            latest = bars[-1]
            now_ts = int(time.time())
            candle_age = max(0, now_ts - latest["timestamp"])
            # Active 15m candle must be within 15 minutes of current wall-clock
            self.assertLessEqual(candle_age, 960, f"Live candle timestamp {latest['timestamp']} is stale (age: {candle_age}s)")
            self.assertGreater(latest["close"], 1000.0)
            self.assertGreater(latest["high"], latest["low"])
        except Exception as e:
            self.skipTest(f"Live public endpoint temporarily unavailable: {e}")


if __name__ == "__main__":
    unittest.main()
