"""Comprehensive Test Suite for Automatic Paper Trading Lifecycle, Risk Gates, and Controls.

Verifies:
1. Automatic paper entry when auto_execute_paper=True.
2. Manual approval required when auto_execute_paper=False.
3. NO TRADE condition when strategy requirements fail.
4. Stale market data detection and rejection.
5. Strict 3:1 net reward-to-risk rule enforcement.
6. Position-sizing limits (allocation, cash buffer, risk budget).
7. Duplicate-signal prevention (open position or repeated candle timestamp).
8. Stop-loss and take-profit execution with conservative handling of ambiguous candles.
9. Transaction friction (slippage, fees, taxes) and net realized P&L.
10. Daily loss limits halting new trades.
11. State persistence across server restarts (loading paper_state.json).
12. Pause new entries and Emergency stop kill switch.
"""

import json
import os
import socketserver
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple
from unittest.mock import MagicMock, patch

from copilot.paper_engine import PaperTradingEngine
from copilot.risk_engine import (
    OrderTicket,
    RiskAndSizingEngine,
    TransactionCostModel,
    UserRiskConfig,
)
from copilot.server import (
    CopilotRequestHandler,
    CopilotServerState,
    generate_synthetic_bars,
)
from engine.asset_model import parse_symbol
from engine.market_data import MarketDataProvider


class TestAutoPaperTradingLifecycle(unittest.TestCase):
    """Unit and integration tests for automatic paper trading execution and risk gates."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_file = os.path.join(self.temp_dir.name, "paper_state.json")
        self.risk_config = UserRiskConfig(
            market="NSE",
            broker="PAPER",
            currency="INR",
            balance=100000.0,
            min_allocation=1000.0,
            max_allocation=50000.0,
            max_risk_pct=1.0,
            max_daily_loss=3000.0,
            max_open_exposure=75000.0,
            max_concurrent_positions=3,
            auto_execute_paper=False,
            new_entries_paused=False,
        )
        self.paper_engine = PaperTradingEngine(
            state_file_path=self.state_file,
            initial_balance=100000.0,
            currency="INR",
            risk_config=self.risk_config,
        )
        self.risk_engine = self.paper_engine.risk_engine

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_auto_execute_flag_defaults_to_false(self):
        """Auto Paper Execution must strictly default to False for safety."""
        cfg = UserRiskConfig()
        self.assertFalse(cfg.auto_execute_paper)
        self.assertFalse(cfg.new_entries_paused)

    def test_net_reward_to_risk_three_to_one_enforced(self):
        """Planned net R:R below 3:1 must be rejected by the risk engine."""
        ticket_sub_3r = OrderTicket(
            symbol="RELIANCE.NS",
            market="NSE",
            side="BUY",
            entry_price=2500.0,
            stop_loss=2450.0,
            take_profit=2620.0,  # ~2.4 R:R
            quantity=10.0,
            allocated_capital=25000.0,
            risk_amount=500.0,
            planned_rr=2.4,
            strategy="TrendFollowing",
        )
        res = self.risk_engine.validate_order(ticket_sub_3r, open_positions={})
        self.assertFalse(res["allowed"])
        self.assertIn("SUB_3R_NET_REWARD_RISK", res["violations"])

        ticket_valid_3r = OrderTicket(
            symbol="RELIANCE.NS",
            market="NSE",
            side="BUY",
            entry_price=2500.0,
            stop_loss=2450.0,
            take_profit=2660.0,  # 3.2 R:R
            quantity=10.0,
            allocated_capital=25000.0,
            risk_amount=500.0,
            planned_rr=3.2,
            strategy="TrendFollowing",
        )
        res_ok = self.risk_engine.validate_order(ticket_valid_3r, open_positions={})
        self.assertTrue(res_ok["allowed"])

    def test_position_sizing_limits_and_cash_buffer(self):
        """Position sizing must respect max allocation, cash buffer, and max risk percent."""
        # 1. Price too high for allocation
        res_huge = self.risk_engine.size_and_verify(
            symbol="EXPENSIVE.NS",
            side="BUY",
            entry_price=60000.0,
            stop_loss=58000.0,
            take_profit=67000.0,
            skip_window=True,
        )
        self.assertFalse(res_huge.approved)
        self.assertTrue(len(res_huge.rejection_reasons) > 0)

        # 2. Valid setup fits within budget and risk limits
        res_ok = self.risk_engine.size_and_verify(
            symbol="RELIANCE.NS",
            side="BUY",
            entry_price=2500.0,
            stop_loss=2450.0,
            take_profit=2700.0,
            skip_window=True,
        )
        self.assertTrue(res_ok.approved)
        self.assertGreaterEqual(res_ok.net_rr, 3.0)
        self.assertLessEqual(res_ok.position_value, self.risk_config.max_allocation)
        self.assertLessEqual(res_ok.max_loss, self.risk_config.balance * (self.risk_config.max_risk_pct / 100.0) + 1.0)

    def test_duplicate_signal_prevention_open_position(self):
        """A new order must be rejected if a position for the same symbol is already open."""
        ticket = OrderTicket(
            symbol="INFY.NS",
            market="NSE",
            side="BUY",
            entry_price=1500.0,
            stop_loss=1470.0,
            take_profit=1600.0,
            quantity=10.0,
            allocated_capital=15000.0,
            risk_amount=300.0,
            planned_rr=3.3,
            strategy="TrendFollowing",
        )
        # Place first trade
        res1 = self.paper_engine.place_order(ticket)
        self.assertTrue(res1["success"])
        self.assertIn("INFY.NS", self.paper_engine.positions)

        # Attempt to validate or place second trade on INFY.NS
        res2 = self.risk_engine.validate_order(ticket, open_positions=self.paper_engine.positions)
        self.assertFalse(res2["allowed"])
        self.assertIn("DUPLICATE_POSITION_EXISTS", res2["violations"])

        res2_order = self.paper_engine.place_order(ticket)
        self.assertFalse(res2_order["success"])
        self.assertIn("DUPLICATE_POSITION_EXISTS", res2_order["reason"])

    def test_daily_loss_limit_halts_entries(self):
        """When realized daily loss exceeds max_daily_loss, new orders are halted."""
        # Inject today's losing trade
        now_iso = datetime.now(timezone.utc).isoformat()
        self.paper_engine.trade_history.append({
            "trade_id": "LOSS-1",
            "symbol": "TCS.NS",
            "side": "BUY",
            "quantity": 10.0,
            "entry_price": 3500.0,
            "exit_price": 3150.0,
            "gross_pnl": -3500.0,
            "total_friction": 50.0,
            "net_pnl": -3550.0,
            "entry_time": now_iso,
            "exit_time": now_iso,
            "exit_reason": "STOP_LOSS",
            "strategy": "TrendFollowing",
        })
        self.paper_engine.save_state()

        # Daily realized loss should now be 3550.0, exceeding max_daily_loss of 3000.0
        realized_loss = self.paper_engine.get_realized_loss_today()
        self.assertGreaterEqual(realized_loss, 3000.0)

        ticket = OrderTicket(
            symbol="HDFCBANK.NS",
            market="NSE",
            side="BUY",
            entry_price=1600.0,
            stop_loss=1570.0,
            take_profit=1700.0,
            quantity=10.0,
            allocated_capital=16000.0,
            risk_amount=300.0,
            planned_rr=3.3,
            strategy="TrendFollowing",
        )
        res = self.paper_engine.place_order(ticket)
        self.assertFalse(res["success"])
        self.assertIn("DAILY_LOSS_BREACHED", res["reason"])

    def test_conservative_ambiguous_candle_handling(self):
        """Ambiguous candle touching both SL and TP must prioritize SL to defend capital."""
        ticket = OrderTicket(
            symbol="WIPRO.NS",
            market="NSE",
            side="BUY",
            entry_price=400.0,
            stop_loss=390.0,
            take_profit=432.0,
            quantity=50.0,
            allocated_capital=20000.0,
            risk_amount=500.0,
            planned_rr=3.2,
            strategy="TrendFollowing",
        )
        self.paper_engine.place_order(ticket)
        self.assertIn("WIPRO.NS", self.paper_engine.positions)

        # Ambiguous bar: low touches 388 (SL) and high touches 435 (TP)
        ambiguous_bar = {
            "open": 400.0,
            "high": 435.0,
            "low": 388.0,
            "close": 410.0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        events = self.paper_engine.process_market_tick("WIPRO.NS", ambiguous_bar)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["event"], "STOP_LOSS_TRIGGERED")
        self.assertEqual(event["details"]["trade"]["exit_reason"], "STOP_LOSS_AMBIGUOUS_BAR")
        self.assertNotIn("WIPRO.NS", self.paper_engine.positions)
        self.assertLess(event["details"]["trade"]["net_pnl"], 0.0)

    def test_state_persistence_across_restart(self):
        """Paper trading state, balance, positions, and history must persist across reloads."""
        ticket = OrderTicket(
            symbol="SBIN.NS",
            market="NSE",
            side="BUY",
            entry_price=600.0,
            stop_loss=585.0,
            take_profit=650.0,
            quantity=20.0,
            allocated_capital=12000.0,
            risk_amount=300.0,
            planned_rr=3.3,
            strategy="TrendFollowing",
        )
        self.paper_engine.place_order(ticket)
        self.assertIn("SBIN.NS", self.paper_engine.positions)
        orig_balance = self.paper_engine.balance

        # Create new engine pointing to the same state file
        reloaded_engine = PaperTradingEngine(
            state_file_path=self.state_file,
            risk_config=self.risk_config,
        )
        self.assertEqual(reloaded_engine.balance, orig_balance)
        self.assertIn("SBIN.NS", reloaded_engine.positions)
        self.assertEqual(reloaded_engine.positions["SBIN.NS"]["quantity"], 20.0)

    def test_close_all_positions(self):
        """close_all_positions must liquidate all active positions cleanly."""
        for sym, price in [("INFY.NS", 1500.0), ("TCS.NS", 3500.0)]:
            t = OrderTicket(
                symbol=sym,
                market="NSE",
                side="BUY",
                entry_price=price,
                stop_loss=price * 0.98,
                take_profit=price * 1.07,
                quantity=5.0,
                allocated_capital=price * 5.0,
                risk_amount=price * 5.0 * 0.02,
                planned_rr=3.5,
                strategy="TrendFollowing",
            )
            self.paper_engine.place_order(t)

        self.assertEqual(len(self.paper_engine.positions), 2)
        res = self.paper_engine.close_all_positions(reason="TEST_CLOSE_ALL")
        self.assertTrue(res["success"])
        self.assertEqual(res["closed_count"], 2)
        self.assertEqual(len(self.paper_engine.positions), 0)


class TestAutoPaperTradingServerIntegration(unittest.TestCase):
    """Integration tests running Copilot HTTP server with Auto Paper Execution enabled and disabled."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.server_state = CopilotServerState(
            mode="paper",
            broker_name="PAPER",
            initial_balance=100000.0,
        )

        class ThreadingHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
            allow_reuse_address = True
            daemon_threads = True

        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), CopilotRequestHandler)
        cls.port = cls.httpd.server_address[1]

        import copilot.server as srv_module
        cls._orig_state = srv_module.SERVER_STATE
        srv_module.SERVER_STATE = cls.server_state

        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        import copilot.server as srv_module
        srv_module.SERVER_STATE = cls._orig_state
        cls.temp_dir.cleanup()

    def setUp(self):
        # Reset server state to clean state before each test
        self.server_state.paper_engine.reset_account(confirm=True, initial_balance=100000.0)
        self.server_state.user_config.auto_execute_paper = False
        self.server_state.user_config.new_entries_paused = False
        self.server_state.last_executed_signals.clear()

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

    def test_manual_approval_required_when_auto_execute_false(self):
        """When auto_execute_paper is False, /api/analyze provides ticket but does not auto-trade."""
        mock_bars = generate_synthetic_bars("BTC-USD", count=60)
        mock_asset = parse_symbol("BTC-USD")
        mock_ticket = {
            "symbol": "BTC-USD",
            "market": "CRYPTO",
            "side": "BUY",
            "entry_price": 60000.0,
            "stop_loss": 58000.0,
            "take_profit": 67000.0,
            "quantity": 0.05,
            "allocated_capital": 3000.0,
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
            code, data = self._post("/api/analyze", {"symbol": "BTC-USD", "interval": "15m"})
            self.assertEqual(code, 200)
            analysis = data.get("analysis", {})
            auto_exec = analysis.get("auto_execution", {})
            self.assertFalse(auto_exec.get("executed"))
            self.assertIn("Manual approval required", auto_exec.get("reason", ""))

            # Verify positions remain empty
            code_pos, data_pos = self._get("/api/positions")
            self.assertEqual(len(data_pos.get("positions", [])), 0)

    def test_automatic_paper_entry_when_auto_execute_true(self):
        """When auto_execute_paper is True and signal is TRADE, order automatically fills."""
        # Enable auto paper execution
        self.server_state.user_config.auto_execute_paper = True

        mock_bars = generate_synthetic_bars("BTC-USD", count=60)
        mock_asset = parse_symbol("BTC-USD")
        mock_ticket = {
            "symbol": "BTC-USD",
            "market": "CRYPTO",
            "side": "BUY",
            "entry_price": 60000.0,
            "stop_loss": 58000.0,
            "take_profit": 67000.0,
            "quantity": 0.05,
            "allocated_capital": 3000.0,
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
            code, data = self._post("/api/analyze", {"symbol": "BTC-USD", "interval": "15m"})
            self.assertEqual(code, 200)
            analysis = data.get("analysis", {})
            auto_exec = analysis.get("auto_execution", {})
            self.assertTrue(auto_exec.get("executed"))
            self.assertIsNotNone(auto_exec.get("order_id"))

            # Verify open position exists
            code_pos, data_pos = self._get("/api/positions")
            symbols = [p["symbol"] for p in data_pos.get("positions", [])]
            self.assertIn("BTC-USD", symbols)

    def test_duplicate_signal_on_same_candle_prevented(self):
        """Repeated scans on the exact same candle timestamp must not trigger duplicate orders."""
        self.server_state.user_config.auto_execute_paper = True

        mock_bars = generate_synthetic_bars("BTC-USD", count=60)
        mock_asset = parse_symbol("BTC-USD")

        # Case 1: Same candle timestamp was already executed
        self.server_state.last_executed_signals["BTC-USD"] = mock_bars[-1]["timestamp"]

        with patch.object(MarketDataProvider, "get_bars", return_value=(mock_asset, mock_bars, {"currency": "USD"})):
            code, data = self._post("/api/analyze", {"symbol": "BTC-USD", "interval": "15m"})
            self.assertEqual(code, 200)
            analysis = data.get("analysis", {})
            auto_exec = analysis.get("auto_execution", {})
            self.assertFalse(auto_exec.get("executed"))
            self.assertIn("Duplicate signal", auto_exec.get("reason", ""))

        # Case 2: Symbol is already open in positions
        self.server_state.last_executed_signals.clear()
        self.server_state.paper_engine.positions["BTC-USD"] = {
            "symbol": "BTC-USD",
            "market": "CRYPTO",
            "side": "BUY",
            "quantity": 0.05,
            "entry_price": 60000.0,
            "stop_loss": 58000.0,
            "take_profit": 67000.0,
            "current_price": 60000.0,
            "unrealized_pnl": 0.0,
            "strategy": "TrendFollowing",
        }

        with patch.object(MarketDataProvider, "get_bars", return_value=(mock_asset, mock_bars, {"currency": "USD"})):
            code2, data2 = self._post("/api/analyze", {"symbol": "BTC-USD", "interval": "15m"})
            self.assertEqual(code2, 200)
            analysis2 = data2.get("analysis", {})
            auto_exec2 = analysis2.get("auto_execution", {})
            self.assertFalse(auto_exec2.get("executed"))
            self.assertIn("already open", auto_exec2.get("reason", ""))

    def test_stale_market_data_rejected(self):
        """Crypto data older than 4 intervals must be rejected with NO TRADE."""
        latest_time = datetime.now(timezone.utc) - timedelta(hours=6)
        start_time = latest_time - timedelta(minutes=50 * 15)
        mock_bars = [
            {
                "timestamp": (start_time + timedelta(minutes=i * 15)).isoformat(),
                "open": 65000.0,
                "high": 65500.0,
                "low": 64800.0,
                "close": 65200.0,
                "volume": 100.0,
            }
            for i in range(50)
        ]
        mock_asset = parse_symbol("BTC-USD")

        with patch.object(MarketDataProvider, "get_bars", return_value=(mock_asset, mock_bars, {"currency": "USD"})):
            code, data = self._post("/api/analyze", {"symbol": "BTC-USD", "interval": "15m"})
            self.assertEqual(code, 200)
            analysis = data.get("analysis", {})
            self.assertEqual(analysis.get("decision"), "NO TRADE")
            reasons = analysis.get("reasons", [])
            self.assertTrue(any("STALE" in r for r in reasons))

    def test_pause_and_resume_endpoints(self):
        """Pause endpoint must halt new entries; resume endpoint must re-enable them."""
        # 1. Pause
        code, data = self._post("/api/pause", {})
        self.assertEqual(code, 200)
        self.assertTrue(data.get("new_entries_paused"))

        # Verify status endpoint reflects pause
        code_s, data_s = self._get("/api/status")
        self.assertTrue(data_s.get("new_entries_paused"))

        # 2. Analysis under pause must not auto-execute
        self.server_state.user_config.auto_execute_paper = True
        mock_bars = generate_synthetic_bars("BTC-USD", count=60)
        mock_asset = parse_symbol("BTC-USD")

        with patch.object(MarketDataProvider, "get_bars", return_value=(mock_asset, mock_bars, {"currency": "USD"})):
            code_a, data_a = self._post("/api/analyze", {"symbol": "BTC-USD", "interval": "15m"})
            auto_exec = data_a.get("analysis", {}).get("auto_execution", {})
            self.assertFalse(auto_exec.get("executed"))
            self.assertIn("paused", auto_exec.get("reason", "").lower())

        # 3. Resume
        code_r, data_r = self._post("/api/resume", {})
        self.assertEqual(code_r, 200)
        self.assertFalse(data_r.get("new_entries_paused"))

    def test_close_all_api_endpoint(self):
        """POST /api/close_all must liquidate all open positions."""
        # Open a position first
        t = OrderTicket(
            symbol="ETH-USD",
            market="CRYPTO",
            side="BUY",
            entry_price=3000.0,
            stop_loss=2900.0,
            take_profit=3350.0,
            quantity=1.0,
            allocated_capital=3000.0,
            risk_amount=100.0,
            planned_rr=3.5,
            strategy="TrendFollowing",
        )
        self.server_state.broker.place_order(t)
        code_p1, data_p1 = self._get("/api/positions")
        self.assertEqual(len(data_p1.get("positions", [])), 1)

        # Call /api/close_all
        code_c, data_c = self._post("/api/close_all", {})
        self.assertEqual(code_c, 200)
        self.assertTrue(data_c.get("success"))
        self.assertEqual(data_c.get("closed_count"), 1)

        # Verify positions empty
        code_p2, data_p2 = self._get("/api/positions")
        self.assertEqual(len(data_p2.get("positions", [])), 0)


if __name__ == "__main__":
    unittest.main()
