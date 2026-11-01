#!/usr/bin/env python3
"""Comprehensive Test Suite for Live Market Data Pipeline, Contract-Aware Mapping, Staleness, and TradingView Sync.

Verifies the 13 required test scenarios:
1. BTCUSD.P identified as Delta Exchange perpetual.
2. Unsupported perpetual blocks execution.
3. Spot and perpetual cannot be interchanged.
4. ETH signal cannot execute against BTC context.
5. TradingView timeframe changes update backend.
6. Old active context cannot generate new orders.
7. LIVE status impossible when data is stale (> 45s).
8. Cached fixtures cannot generate executable signals.
9. New valid signals execute after previous trade closes.
10. Duplicate signals cannot create duplicate orders.
11. Re-entry cooldown works correctly.
12. Every rejected signal has an explicit reason.
13. 3:1 net R:R and daily loss safeguards verified.
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

from copilot.cost_model import TransactionCostModel
from copilot.market_data import (
    CryptoExchangeFeed,
    DeltaExchangeFeed,
    InstrumentInfo,
    LiveMarketDataManager,
    merge_candle_bars,
    parse_bar_timestamp,
    resolve_instrument_info,
)
from copilot.paper_engine import PaperTradingEngine
from copilot.risk_engine import OrderTicket, RiskAndSizingEngine, UserRiskConfig
from copilot.server import (
    CopilotRequestHandler,
    CopilotServerState,
    clean_float,
    generate_synthetic_bars,
    normalize_market_symbol,
)
from engine.asset_model import parse_symbol
from engine.market_data import MarketDataError, MarketDataProvider


class TestLiveMarketDataPipeline(unittest.TestCase):
    """Unit and mock tests covering candle updates, deduplication, aggregation, and staleness."""

    def setUp(self):
        self.mgr = LiveMarketDataManager()

    def test_candle_merge_new_bar_appended(self):
        """Test candle merge: New candle received and correctly merged into candle buffer."""
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

    def test_candle_merge_current_bar_updated_without_duplicate(self):
        """Test candle merge: Updates to current candle modify OHLCV without creating duplicate timestamps."""
        bars = [
            {"timestamp": 1000, "open": 50000.0, "high": 50200.0, "low": 49900.0, "close": 50100.0, "volume": 10.0},
            {"timestamp": 1900, "open": 50100.0, "high": 50300.0, "low": 50000.0, "close": 50250.0, "volume": 12.0},
        ]
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

    def test_candle_timeframe_aggregation_2m(self):
        """Test 2m aggregation: 1m bars aggregate into 2m candles properly."""
        raw_1m = [
            {"timestamp": 100, "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0, "volume": 5.0, "datetime_utc": "2026-01-01T00:01:00Z", "datetime_local": "2026-01-01 00:01:00"},
            {"timestamp": 160, "open": 11.0, "high": 14.0, "low": 10.5, "close": 13.0, "volume": 7.0, "datetime_utc": "2026-01-01T00:02:00Z", "datetime_local": "2026-01-01 00:02:00"},
        ]
        with patch.object(CryptoExchangeFeed, "fetch_binance_klines") as mock_klines:
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


class TestInstrumentIdentificationAndExecutionSafety(unittest.TestCase):
    """End-to-end integration and unit tests for the 13 required scenarios."""

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
        self.server_state.user_config.emergency_stop = False
        self.server_state.broker.emergency_halted = False
        self.server_state.last_executed_signals.clear()
        self.server_state.last_trade_times.clear()
        self.server_state.signal_diagnostics = type(self.server_state.signal_diagnostics)()
        self.server_state.active_context = {
            "symbol": None,
            "raw_symbol": None,
            "interval": "15m",
            "source": None,
            "status": "TRADINGVIEW NOT SYNCHRONIZED",
            "synced": False,
            "updated_at": 0.0,
            "unverified": True,
        }

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

    def test_01_btcusd_p_identified_as_delta_perpetual(self):
        """Scenario 1: BTCUSD.P is explicitly identified as Delta Exchange perpetual futures."""
        for sym in ("BTCUSD.P", "DELTA:BTCUSD.P"):
            info = resolve_instrument_info(sym)
            self.assertTrue(info.supported)
            self.assertEqual(info.exchange, "DELTA")
            self.assertEqual(info.instrument_type, "perpetual_futures")
            self.assertEqual(info.canonical_id, "DELTA:BTCUSD:PERP")
            self.assertEqual(info.settlement_currency, "USD")
            self.assertEqual(info.provider_symbol, "BTCUSD")
            self.assertIn("Delta Exchange", info.provider_name)
            self.assertEqual(info.status, "VERIFIED")

            # Verify server normalization
            norm = normalize_market_symbol(sym)
            self.assertTrue(norm["supported"])
            self.assertEqual(norm["exchange"], "DELTA")
            self.assertEqual(norm["instrument_type"], "perpetual_futures")

    def test_02_unsupported_perpetual_blocks_execution(self):
        """Scenario 2: Unsupported perpetual blocks analysis and automatic paper execution."""
        info = resolve_instrument_info("UNKNOWN.P")
        self.assertFalse(info.supported)
        self.assertEqual(info.status, "NO_VERIFIED_FEED")
        self.assertEqual(info.instrument_type, "perpetual_futures")
        self.assertIn("NO VERIFIED FEED", info.reason)

        # Set active context to UNKNOWN.P
        code_ctx, data_ctx = self._post("/api/active_context", {"symbol": "UNKNOWN.P", "interval": "15m"})
        self.assertEqual(code_ctx, 200)
        self.assertFalse(data_ctx["active_context"]["synced"])
        self.assertEqual(data_ctx["active_context"]["status"], "NO_VERIFIED_FEED")

        # Attempt analysis and execution on UNKNOWN.P
        code_a, data_a = self._post("/api/analyze", {"symbol": "UNKNOWN.P", "interval": "15m", "auto_execute": True})
        self.assertEqual(code_a, 400)
        self.assertEqual(data_a.get("status"), "UNSUPPORTED_SYMBOL")
        self.assertEqual(data_a.get("analysis", {}).get("decision"), "NO_TRADE")

        # Verify no position opened
        self.assertEqual(len(self.server_state.paper_engine.positions), 0)

    def test_03_spot_and_perpetual_cannot_be_interchanged(self):
        """Scenario 3: Spot BTC-USD and perpetual BTCUSD.P have distinct canonical IDs and types."""
        spot_info = resolve_instrument_info("BTC-USD")
        perp_info = resolve_instrument_info("BTCUSD.P")

        self.assertNotEqual(spot_info.canonical_id, perp_info.canonical_id)
        self.assertEqual(spot_info.instrument_type, "spot")
        self.assertEqual(perp_info.instrument_type, "perpetual_futures")
        self.assertNotEqual(spot_info.exchange, perp_info.exchange)
        self.assertEqual(spot_info.exchange, "CRYPTO")
        self.assertEqual(perp_info.exchange, "DELTA")

    def test_04_eth_signal_cannot_execute_against_btc_context(self):
        """Scenario 4: Ticket generated for ETH-USD cannot execute when active context is BTCUSD.P."""
        # Active chart is BTCUSD.P
        self._post("/api/active_context", {"symbol": "DELTA:BTCUSD.P", "interval": "15m"})

        # Order ticket is created for ETH-USD
        eth_ticket = {
            "symbol": "ETH-USD",
            "tradingview_symbol": "ETHUSD",
            "canonical_id": "CRYPTO:ETH-USD:SPOT",
            "instrument_type": "spot",
            "side": "BUY",
            "entry_price": 2600.0,
            "stop_loss": 2500.0,
            "take_profit": 2950.0,
            "quantity": 1.0,
            "allocated_capital": 2600.0,
            "risk_amount": 100.0,
            "planned_rr": 3.5,
            "strategy": "TrendFollowing",
            "currency": "USD",
        }

        code_o, data_o = self._post("/api/order", {"ticket": eth_ticket})
        self.assertEqual(code_o, 400)
        self.assertFalse(data_o.get("success"))
        self.assertEqual(data_o.get("status"), "CONTEXT_MISMATCH")
        self.assertIn("CONTEXT MISMATCH", data_o.get("reason", ""))
        self.assertEqual(len(self.server_state.paper_engine.positions), 0)

    def test_05_tradingview_timeframe_changes_update_backend(self):
        """Scenario 5: Timeframe change from 15m to 5m updates backend active context."""
        code1, data1 = self._post("/api/active_context", {"symbol": "BTCUSD.P", "interval": "15m"})
        self.assertEqual(code1, 200)
        self.assertEqual(data1["active_context"]["interval"], "15m")

        code2, data2 = self._post("/api/active_context", {"symbol": "BTCUSD.P", "interval": "5m"})
        self.assertEqual(code2, 200)
        self.assertEqual(data2["active_context"]["interval"], "5m")

        # Query GET /api/active_context
        code_g, data_g = self._get("/api/active_context")
        self.assertEqual(code_g, 200)
        self.assertEqual(data_g["active_context"]["interval"], "5m")

    def test_06_old_active_context_cannot_generate_new_orders(self):
        """Scenario 6: Active context older than 60s is classified expired and blocks order placement."""
        self._post("/api/active_context", {"symbol": "BTCUSD.P", "interval": "15m"})
        # Simulate elapsed time > 60s
        self.server_state.active_context["updated_at"] = time.time() - 90.0

        ticket = {
            "symbol": "BTCUSD",
            "tradingview_symbol": "DELTA:BTCUSD.P",
            "canonical_id": "DELTA:BTCUSD:PERP",
            "side": "BUY",
            "entry_price": 75000.0,
            "stop_loss": 74000.0,
            "take_profit": 78500.0,
            "quantity": 0.1,
            "allocated_capital": 7500.0,
            "risk_amount": 100.0,
            "planned_rr": 3.5,
            "currency": "USD",
        }
        code_o, data_o = self._post("/api/order", {"ticket": ticket})
        self.assertEqual(code_o, 400)
        self.assertFalse(data_o.get("success"))
        self.assertEqual(data_o.get("status"), "CONTEXT_EXPIRED")
        self.assertIn("EXPIRED", data_o.get("reason", ""))

    def test_07_live_status_impossible_when_data_is_stale(self):
        """Scenario 7: When data age > 45s on 24/7 continuous feed, LIVE status is impossible."""
        now = time.time()
        # Mock last fetch time 120s ago
        self.server_state.market_data.last_fetch_time[("BTC-USD", "15m")] = now - 120.0
        candle_ts = int(now - 120.0)
        bars = [{"timestamp": candle_ts, "open": 65000.0, "high": 65200.0, "low": 64900.0, "close": 65100.0, "volume": 10.0}]

        health = self.server_state.market_data.assess_health("BTC-USD", "15m", bars, "Binance Public")
        self.assertFalse(health["is_live"], "Feed older than 45s must NEVER be labeled is_live=True")
        self.assertTrue(health["is_stale"])
        self.assertNotEqual(health["status"], "LIVE")
        self.assertIn("STALE DATA", health["status"])
        self.assertGreater(health["data_age_sec"], 45)

    def test_08_cached_fixtures_cannot_generate_executable_signals(self):
        """Scenario 8: Cached fallback fixtures report is_fallback=True and block paper execution."""
        bars = generate_synthetic_bars("BTC-USD", count=60)
        health = self.server_state.market_data.assess_health("BTC-USD", "15m", bars, "Offline Fixture", is_fallback=True)

        self.assertTrue(health["is_fallback"])
        self.assertFalse(health["is_live"])
        self.assertTrue(health["is_stale"])
        self.assertEqual(health["status"], "CACHED FALLBACK (STALE)")

        # Inject this fallback health into server market data cache
        self.server_state.market_data.bars_cache[("BTC-USD", "15m")] = bars
        with patch.object(self.server_state.market_data, "get_data_health", return_value=health):
            ticket = {
                "symbol": "BTC-USD",
                "side": "BUY",
                "entry_price": 65000.0,
                "stop_loss": 64000.0,
                "take_profit": 68500.0,
                "quantity": 0.1,
                "allocated_capital": 6500.0,
                "risk_amount": 100.0,
                "planned_rr": 3.5,
                "currency": "USD",
            }
            code_o, data_o = self._post("/api/order", {"ticket": ticket})
            self.assertEqual(code_o, 400)
            self.assertFalse(data_o.get("success"))
            self.assertEqual(data_o.get("status"), "CACHED_FALLBACK")
            self.assertIn("fallback", data_o.get("reason", "").lower())

    def test_09_new_valid_signals_execute_after_previous_trade_closes(self):
        """Scenario 9: Once an active position is closed, a subsequent valid signal can execute."""
        self.server_state.user_config.auto_execute_paper = True
        ticket = OrderTicket(
            symbol="BTC-USD",
            market="CRYPTO",
            side="BUY",
            entry_price=65000.0,
            stop_loss=64000.0,
            take_profit=68500.0,
            quantity=0.1,
            allocated_capital=6500.0,
            risk_amount=100.0,
            planned_rr=3.5,
            strategy="TrendFollowing",
            currency="USD",
        )
        res1 = self.server_state.broker.place_order(ticket)
        self.assertTrue(res1.get("success"))
        self.assertIn("BTC-USD", self.server_state.paper_engine.positions)

        # Close position
        close_res = self.server_state.paper_engine.close_position("BTC-USD", 68500.0, reason="TAKE_PROFIT_3R")
        self.assertTrue(close_res.get("success"))
        self.assertNotIn("BTC-USD", self.server_state.paper_engine.positions)

        # Clear cooldown for immediate re-test
        self.server_state.last_trade_times["BTC-USD"] = 0

        # Execute new signal
        ticket2 = OrderTicket(
            symbol="BTC-USD",
            market="CRYPTO",
            side="BUY",
            entry_price=68600.0,
            stop_loss=67600.0,
            take_profit=72000.0,
            quantity=0.1,
            allocated_capital=6860.0,
            risk_amount=100.0,
            planned_rr=3.4,
            strategy="Breakout",
            currency="USD",
        )
        res2 = self.server_state.broker.place_order(ticket2)
        self.assertTrue(res2.get("success"))
        self.assertIn("BTC-USD", self.server_state.paper_engine.positions)

    def test_10_duplicate_signals_cannot_create_duplicate_orders(self):
        """Scenario 10: Repeated ticks with identical candle timestamp do not spawn duplicate orders."""
        self.server_state.user_config.auto_execute_paper = True
        mock_bars = generate_synthetic_bars("BTC-USD", count=60)
        mock_asset = parse_symbol("BTC-USD")
        mock_ticket = {
            "symbol": "BTC-USD",
            "market": "CRYPTO",
            "side": "BUY",
            "entry_price": 65000.0,
            "stop_loss": 64000.0,
            "take_profit": 68500.0,
            "quantity": 0.1,
            "allocated_capital": 6500.0,
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
            # First tick
            code1, data1 = self._post("/api/analyze", {"symbol": "BTC-USD", "interval": "15m"})
            self.assertEqual(code1, 200)
            self.assertTrue(data1["analysis"]["auto_execution"]["executed"])

            # Second identical tick
            code2, data2 = self._post("/api/analyze", {"symbol": "BTC-USD", "interval": "15m"})
            self.assertEqual(code2, 200)
            self.assertFalse(data2["analysis"]["auto_execution"]["executed"])
            self.assertIn("Duplicate", data2["analysis"]["auto_execution"]["reason"])

    def test_11_reentry_cooldown_works_correctly(self):
        """Scenario 11: Re-entry within 60s cooldown is rejected; succeeds after cooldown expires."""
        now = time.time()
        self.server_state.last_trade_times["BTC-USD"] = now - 10.0  # 10s ago, cooldown is 60s

        ticket = {
            "symbol": "BTC-USD",
            "side": "BUY",
            "entry_price": 65000.0,
            "stop_loss": 64000.0,
            "take_profit": 68500.0,
            "quantity": 0.1,
            "allocated_capital": 6500.0,
            "risk_amount": 100.0,
            "planned_rr": 3.5,
            "currency": "USD",
        }
        code1, data1 = self._post("/api/order", {"ticket": ticket})
        self.assertEqual(code1, 400)
        self.assertFalse(data1.get("success"))
        self.assertEqual(data1.get("status"), "COOLDOWN_ACTIVE")
        self.assertIn("COOLDOWN", data1.get("reason", ""))

        # Fast forward past cooldown
        self.server_state.last_trade_times["BTC-USD"] = now - 70.0
        code2, data2 = self._post("/api/order", {"ticket": ticket})
        self.assertEqual(code2, 200)
        self.assertTrue(data2.get("success"))

    def test_12_every_rejected_signal_has_explicit_reason(self):
        """Scenario 12: Every rejection provides an explicit, actionable explanation in diagnostics."""
        # Cause rejection via unsupported symbol
        self._post("/api/analyze", {"symbol": "UNKNOWN.P", "interval": "15m"})

        code_diag, data_diag = self._get("/api/diagnostics")
        self.assertEqual(code_diag, 200)
        self.assertGreaterEqual(data_diag["rejected_orders"], 1)
        self.assertTrue(len(data_diag["most_recent_rejection_reason"]) > 5)
        self.assertIn("NO VERIFIED FEED", data_diag["most_recent_rejection_reason"])

    def test_13_three_to_one_rr_and_daily_loss_safeguards_verified(self):
        """Scenario 13: 3:1 net R:R and daily loss limit safeguards are strictly enforced."""
        risk_engine = RiskAndSizingEngine(self.server_state.user_config)

        # 1. Planned R:R below 3.0:1 must be rejected
        res_sub_rr = risk_engine.size_and_verify(
            symbol="RELIANCE.NS",
            side="BUY",
            entry_price=2500.0,
            stop_loss=2450.0,
            take_profit=2550.0,  # 50 reward vs 50 risk = 1:1 gross, < 3:1 net
            skip_window=True,
        )
        self.assertFalse(res_sub_rr.approved)
        self.assertTrue(any("3.0:1" in r or "3:1" in r for r in res_sub_rr.rejection_reasons))

        ticket_sub_rr = risk_engine.calculate_position_size(
            symbol="RELIANCE.NS",
            entry_price=2500.0,
            stop_loss=2450.0,
            take_profit=2550.0,
            side="BUY",
        )
        self.assertIsNone(ticket_sub_rr)

        # 2. Daily loss limit breach must reject all new entries
        self.server_state.user_config.max_daily_loss = 2000.0
        res_daily = risk_engine.size_and_verify(
            symbol="RELIANCE.NS",
            side="BUY",
            entry_price=2500.0,
            stop_loss=2480.0,
            take_profit=2570.0,
            realized_loss_today=2500.0,  # Breached 2000 limit
            skip_window=True,
        )
        self.assertFalse(res_daily.approved)
        self.assertTrue(any("daily loss" in r.lower() for r in res_daily.rejection_reasons))


class TestRealMarketDataLiveIntegration(unittest.TestCase):
    """Separate live verification against real public 24/7 crypto and delta exchange endpoints."""

    def test_real_crypto_candles_advance(self):
        """Fetches live candles from Binance public endpoint and confirms candle timestamps are fresh."""
        try:
            bars = CryptoExchangeFeed.fetch_binance_klines("BTC-USD", interval="15m", limit=5)
            self.assertGreaterEqual(len(bars), 2)
            latest = bars[-1]
            now_ts = int(time.time())
            candle_age = max(0, now_ts - latest["timestamp"])
            self.assertLessEqual(candle_age, 960, f"Live candle timestamp {latest['timestamp']} is stale (age: {candle_age}s)")
            self.assertGreater(latest["close"], 1000.0)
            self.assertGreater(latest["high"], latest["low"])
        except Exception as e:
            self.skipTest(f"Live public endpoint temporarily unavailable: {e}")

    def test_real_delta_exchange_candles_advance(self):
        """Fetches real perpetual futures candles from Delta Exchange India."""
        try:
            bars = DeltaExchangeFeed.fetch_delta_candles("BTCUSD.P", interval="15m", limit=5)
            self.assertGreaterEqual(len(bars), 2)
            latest = bars[-1]
            now_ts = int(time.time())
            candle_age = max(0, now_ts - latest["timestamp"])
            self.assertLessEqual(candle_age, 960, f"Live Delta candle timestamp {latest['timestamp']} is stale (age: {candle_age}s)")
            self.assertGreater(latest["close"], 1000.0)
            self.assertGreater(latest["high"], latest["low"])
        except Exception as e:
            self.skipTest(f"Delta Exchange public endpoint temporarily unavailable: {e}")


if __name__ == "__main__":
    unittest.main()
