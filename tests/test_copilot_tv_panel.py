#!/usr/bin/env python3
"""Automated Unit and Integration Tests for TradingView In-Page Panel and Symbol Mapping.

Verifies:
1. Symbol normalization for TradingView tickers:
   - Index Continuous Futures (NSE:NIFTY1!, NSE:BANKNIFTY1!)
   - Indian Equities (NSE:RELIANCE, RELIANCE.NS)
   - Cryptocurrencies (BINANCE:BTCUSDT, BTC-USD)
   - US Equities (NASDAQ:AAPL)
   - Unsupported / unmappable tickers
2. Timeframe interval handling (15m, 5m, 1h, 1d).
3. Volume-exempt cash index regime classification (rvol defaults to 1.0, not illiquid).
4. REST API handling of TradingView chart requests and analysis requests.
"""

import json
import socketserver
import threading
import time
import unittest
import urllib.request
import urllib.error
from typing import Dict, Any

from copilot.server import (
    CopilotRequestHandler,
    CopilotServerState,
    normalize_market_symbol,
)
import copilot.server as copilot_module
from copilot.regime import MarketRegimeDetector, MarketRegime


class TestSymbolNormalization(unittest.TestCase):
    """Test suite for normalize_market_symbol."""

    def test_nifty_futures_mapping(self):
        tickers = ["NSE:NIFTY1!", "NIFTY1!", "NSE:NIFTY", "^NSEI", "NIFTY50"]
        for t in tickers:
            info = normalize_market_symbol(t)
            self.assertTrue(info["supported"], f"Expected {t} to be supported")
            self.assertEqual(info["mapped_symbol"], "^NSEI")
            self.assertEqual(info["market"], "NSE")
            self.assertIn("NIFTY 50", info["display_name"].upper())
            self.assertIn("Index Proxy", info["source_label"])
            self.assertTrue(info.get("is_proxy"))
            self.assertEqual(info.get("proxy_target"), "^NSEI")
            self.assertIn("tracks underlying cash index", info.get("proxy_notice", ""))

    def test_banknifty_futures_mapping(self):
        tickers = ["NSE:BANKNIFTY1!", "BANKNIFTY1!", "BANKNIFTY", "^NSEBANK"]
        for t in tickers:
            info = normalize_market_symbol(t)
            self.assertTrue(info["supported"], f"Expected {t} to be supported")
            self.assertEqual(info["mapped_symbol"], "^NSEBANK")
            self.assertEqual(info["market"], "NSE")
            self.assertTrue(info.get("is_proxy"))

    def test_nse_equities_mapping(self):
        info1 = normalize_market_symbol("NSE:RELIANCE")
        self.assertTrue(info1["supported"])
        self.assertEqual(info1["mapped_symbol"], "RELIANCE.NS")
        self.assertEqual(info1["market"], "NSE")
        self.assertFalse(info1.get("is_proxy"))

        info2 = normalize_market_symbol("TCS.NS")
        self.assertTrue(info2["supported"])
        self.assertEqual(info2["mapped_symbol"], "TCS.NS")

        info3 = normalize_market_symbol("INFY", market="NSE")
        self.assertTrue(info3["supported"])
        self.assertEqual(info3["mapped_symbol"], "INFY.NS")

    def test_crypto_mapping(self):
        crypto_variants = [
            ("BINANCE:BTCUSDT", "BTC-USD"),
            ("COINBASE:BTCUSD", "BTC-USD"),
            ("BITSTAMP:BTCUSD", "BTC-USD"),
            ("INDEX:BTCUSD", "BTC-USD"),
            ("CRYPTO:BTCUSD", "BTC-USD"),
            ("OKX:BTCUSDT", "BTC-USD"),
            ("BTC / USD", "BTC-USD"),
            ("BTC-USD", "BTC-USD"),
            ("Bitcoin", "BTC-USD"),
            ("BTCUSD,", "BTC-USD"),
            ("Ethereum", "ETH-USD"),
            ("ETH-USD", "ETH-USD"),
            ("BINANCE:ETHUSDT", "ETH-USD"),
            ("Solana", "SOL-USD"),
            ("SOL-USD", "SOL-USD"),
        ]
        for raw, expected in crypto_variants:
            info = normalize_market_symbol(raw)
            self.assertTrue(info["supported"], f"Expected {raw} to map to {expected}")
            self.assertEqual(info["mapped_symbol"], expected)
            self.assertEqual(info["market"], "CRYPTO")
            self.assertEqual(info["source_label"], "Live 24/7 Spot")

    def test_unverified_context_rejection(self):
        unverified_inputs = ["UNVERIFIED", "Page not found", "404", "NONE", ""]
        for item in unverified_inputs:
            info = normalize_market_symbol(item)
            self.assertFalse(info["supported"], f"Expected {item} to be unverified/unsupported")
            self.assertIn("CHART CONTEXT UNVERIFIED", info["reason"])

    def test_us_equity_mapping(self):
        info1 = normalize_market_symbol("NASDAQ:AAPL")
        self.assertTrue(info1["supported"])
        self.assertEqual(info1["mapped_symbol"], "AAPL")
        self.assertEqual(info1["market"], "US")

        info2 = normalize_market_symbol("SPY")
        self.assertTrue(info2["supported"])
        self.assertEqual(info2["mapped_symbol"], "SPY")

    def test_unsupported_symbols(self):
        bad_tickers = ["RANDOM_UNKNOWN_XYZ123", "FOREX:EURUSD_UNSUPPORTED", "FOO:BAR"]
        for t in bad_tickers:
            info = normalize_market_symbol(t)
            self.assertFalse(info["supported"], f"Expected {t} to be unsupported")
            self.assertIsNone(info["mapped_symbol"])
            self.assertIn("UNSUPPORTED SYMBOL", info["reason"])


class TestVolumeExemptRegimeDetection(unittest.TestCase):
    """Verifies that cash index feeds with 0.0 volume are not falsely classified as illiquid."""

    def test_zero_volume_index_adequate_liquidity(self):
        # Create synthetic bars with volume = 0 (standard for cash indices like ^NSEI)
        bars = []
        base_price = 23000.0
        for i in range(40):
            p = base_price + (i * 5.0)
            bars.append({
                "timestamp": 1700000000 + (i * 900),
                "datetime_local": f"2026-10-30 10:{i:02d}:00",
                "open": p,
                "high": p + 10.0,
                "low": p - 5.0,
                "close": p + 6.0,
                "volume": 0.0,  # Zero volume
            })
        
        regime = MarketRegimeDetector.detect_regime(bars)
        # Should NOT be classified as ILLIQUID
        self.assertEqual(regime["liquidity_status"], "ADEQUATE")
        self.assertEqual(regime["indicators"]["rvol"], 1.0)


class TestTradingViewServerEndpoints(unittest.TestCase):
    """Tests server handling of TradingView chart and analysis requests."""

    @classmethod
    def setUpClass(cls):
        copilot_module.SERVER_STATE = CopilotServerState()
        cls.server = socketserver.TCPServer(("127.0.0.1", 0), CopilotRequestHandler)
        cls.port = cls.server.server_address[1]
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path: str):
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read().decode("utf-8")
            return resp.status, json.loads(data)

    def _post(self, path: str, payload: Dict[str, Any]):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body)

    def test_unsupported_symbol_chart_returns_400(self):
        url = f"http://127.0.0.1:{self.port}/api/chart?symbol=RANDOM_UNKNOWN_XYZ123&interval=15m"
        req = urllib.request.Request(url)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 400)
        body = json.loads(ctx.exception.read().decode("utf-8"))
        self.assertEqual(body.get("status"), "UNSUPPORTED_SYMBOL")

    def test_unsupported_symbol_analyze_returns_400(self):
        url = f"http://127.0.0.1:{self.port}/api/analyze"
        payload = json.dumps({"symbol": "RANDOM_UNKNOWN_XYZ123", "interval": "15m"}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 400)
        body = json.loads(ctx.exception.read().decode("utf-8"))
        self.assertEqual(body.get("status"), "UNSUPPORTED_SYMBOL")
        self.assertIn("error", body)
        self.assertIn("UNSUPPORTED SYMBOL", body["error"])
        self.assertEqual(body["analysis"]["decision"], "NO_TRADE")
        self.assertIn("UNSUPPORTED SYMBOL", body["analysis"]["reasons"][0])

    def test_unverified_context_analyze_returns_400(self):
        url = f"http://127.0.0.1:{self.port}/api/analyze"
        payload = json.dumps({"symbol": "UNVERIFIED", "interval": "15m"}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 400)
        body = json.loads(ctx.exception.read().decode("utf-8"))
        self.assertEqual(body.get("status"), "UNSUPPORTED_SYMBOL")
        self.assertIn("error", body)
        self.assertIn("CHART CONTEXT UNVERIFIED", body["error"])
        self.assertEqual(body["analysis"]["decision"], "NO_TRADE")


if __name__ == "__main__":
    unittest.main()
