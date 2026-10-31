"""Unit tests for Paper Trading Engine, State Persistence, and Execution Lifecycle."""

import os
import tempfile
import unittest
from copilot.paper_engine import PaperTradingEngine
from copilot.risk_engine import OrderTicket, UserRiskConfig


class TestPaperTradingEngine(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_file = os.path.join(self.temp_dir.name, "paper_state.json")
        self.config = UserRiskConfig(
            market="NSE",
            broker="PAPER",
            currency="INR",
            balance=100000.0,
            min_allocation=1000.0,
            max_allocation=50000.0,
            max_risk_pct=1.0,
            max_daily_loss=5000.0,
            max_open_exposure=80000.0,
            max_concurrent_positions=5,
        )
        self.engine = PaperTradingEngine(
            state_file_path=self.state_file,
            initial_balance=100000.0,
            currency="INR",
            risk_config=self.config,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_state_persistence_and_reload(self):
        self.assertTrue(os.path.exists(self.state_file))
        # Modify balance and save
        self.engine.balance = 95000.0
        self.engine.save_state()

        # Create new engine instance from the same state file
        new_engine = PaperTradingEngine(state_file_path=self.state_file)
        self.assertEqual(new_engine.balance, 95000.0)

    def test_place_paper_order_fills_and_deducts_fees(self):
        ticket = OrderTicket(
            symbol="RELIANCE.NS",
            market="NSE",
            side="BUY",
            entry_price=2400.0,
            stop_loss=2350.0,
            take_profit=2560.0,
            quantity=10.0,
            allocated_capital=24000.0,
            risk_amount=500.0,
            planned_rr=3.2,
            strategy="TrendFollowing",
        )
        res = self.engine.place_order(ticket)
        self.assertTrue(res["success"])
        self.assertIn("RELIANCE.NS", self.engine.positions)
        self.assertGreater(res["entry_friction"], 0.0)
        # Cash balance reduced
        self.assertLess(self.engine.balance, 100000.0 - 24000.0)

    def test_market_tick_triggers_take_profit(self):
        ticket = OrderTicket(
            symbol="RELIANCE.NS",
            market="NSE",
            side="BUY",
            entry_price=2400.0,
            stop_loss=2350.0,
            take_profit=2560.0,
            quantity=10.0,
            allocated_capital=24000.0,
            risk_amount=500.0,
            planned_rr=3.2,
            strategy="TrendFollowing",
        )
        self.engine.place_order(ticket)

        # Bar exceeds take profit target (2560.0)
        winning_bar = {
            "open": 2550.0,
            "high": 2570.0,
            "low": 2540.0,
            "close": 2565.0,
            "timestamp": "2026-10-31T10:00:00Z",
        }
        events = self.engine.process_market_tick("RELIANCE.NS", winning_bar)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "TAKE_PROFIT_TRIGGERED")
        self.assertNotIn("RELIANCE.NS", self.engine.positions)
        self.assertEqual(len(self.engine.trade_history), 1)

        closed_trade = self.engine.trade_history[0]
        self.assertGreater(closed_trade["net_pnl"], 0.0)
        self.assertGreaterEqual(closed_trade["r_multiple"], 3.0)
        self.assertEqual(self.engine.stats["winning_trades"], 1)

    def test_market_tick_triggers_stop_loss(self):
        ticket = OrderTicket(
            symbol="TCS.NS",
            market="NSE",
            side="BUY",
            entry_price=3500.0,
            stop_loss=3450.0,
            take_profit=3660.0,
            quantity=5.0,
            allocated_capital=17500.0,
            risk_amount=250.0,
            planned_rr=3.2,
            strategy="ORB_3R",
        )
        self.engine.place_order(ticket)

        # Bar breaches stop loss (3450.0)
        losing_bar = {
            "open": 3460.0,
            "high": 3465.0,
            "low": 3440.0,
            "close": 3445.0,
            "timestamp": "2026-10-31T10:30:00Z",
        }
        events = self.engine.process_market_tick("TCS.NS", losing_bar)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "STOP_LOSS_TRIGGERED")
        self.assertNotIn("TCS.NS", self.engine.positions)
        self.assertEqual(self.engine.stats["losing_trades"], 1)

    def test_emergency_stop_liquidates_all(self):
        ticket1 = OrderTicket(
            symbol="RELIANCE.NS",
            market="NSE",
            side="BUY",
            entry_price=2400.0,
            stop_loss=2350.0,
            take_profit=2560.0,
            quantity=5.0,
            allocated_capital=12000.0,
            risk_amount=250.0,
            planned_rr=3.2,
            strategy="TrendFollowing",
        )
        self.engine.place_order(ticket1)
        self.assertEqual(len(self.engine.positions), 1)

        res = self.engine.emergency_stop()
        self.assertEqual(res["status"], "HALTED")
        self.assertEqual(len(self.engine.positions), 0)

    def test_reset_account_requires_explicit_confirmation(self):
        unconfirmed = self.engine.reset_account(confirm=False)
        self.assertFalse(unconfirmed["success"])

        confirmed = self.engine.reset_account(confirm=True, initial_balance=50000.0)
        self.assertTrue(confirmed["success"])
        self.assertEqual(self.engine.balance, 50000.0)
        self.assertEqual(len(self.engine.trade_history), 0)


if __name__ == "__main__":
    unittest.main()

