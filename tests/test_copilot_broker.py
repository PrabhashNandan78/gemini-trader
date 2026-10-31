"""Unit tests for Broker Adapters and Live Execution Safety Gates."""

import os
import tempfile
import unittest
from copilot.broker_adapter import (
    PaperBrokerAdapter,
    ZerodhaBrokerAdapter,
    AlpacaBrokerAdapter,
    create_broker_adapter,
)
from copilot.paper_engine import PaperTradingEngine
from copilot.risk_engine import OrderTicket, UserRiskConfig


class TestBrokerAdapters(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_file = os.path.join(self.temp_dir.name, "paper_state.json")
        self.config = UserRiskConfig(
            market="NSE",
            broker="PAPER",
            currency="INR",
            balance=100000.0,
            mode="paper",
        )
        self.paper_engine = PaperTradingEngine(
            state_file_path=self.state_file,
            initial_balance=100000.0,
            currency="INR",
            risk_config=self.config,
        )
        self.ticket = OrderTicket(
            symbol="RELIANCE.NS",
            market="NSE",
            side="BUY",
            entry_price=2500.0,
            stop_loss=2450.0,
            take_profit=2670.0,
            quantity=5.0,
            allocated_capital=12500.0,
            risk_amount=250.0,
            planned_rr=3.2,
            strategy="TrendFollowing",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_paper_broker_adapter_lifecycle(self):
        adapter = PaperBrokerAdapter(paper_engine=self.paper_engine, user_config=self.config)
        auth = adapter.authenticate()
        self.assertTrue(auth["authenticated"])

        balance_info = adapter.get_account_balance()
        self.assertEqual(balance_info["balance"], 100000.0)

        # Place order
        res = adapter.place_order(self.ticket)
        self.assertTrue(res["success"])
        self.assertEqual(len(adapter.get_positions()), 1)

        # Emergency stop
        halt = adapter.emergency_stop()
        self.assertEqual(halt["status"], "HALTED")
        self.assertEqual(len(adapter.get_positions()), 0)

    def test_zerodha_live_safety_gate_blocks_unconfirmed_order(self):
        # Even if mode is live, without confirm_live=True, order MUST be blocked!
        live_config = UserRiskConfig(
            market="NSE",
            broker="ZERODHA",
            mode="live",
            balance=100000.0,
        )
        adapter = ZerodhaBrokerAdapter(
            api_key="mock_key",
            access_token="mock_token",
            mode="live",
            user_config=live_config,
        )
        # Call place_order without confirm_live
        res = adapter.place_order(self.ticket, confirm_live=False)
        self.assertFalse(res["success"])
        self.assertEqual(res["safety_gate"], "FAILED_CONFIRMATION")

    def test_zerodha_live_safety_gate_blocks_when_unauthenticated(self):
        live_config = UserRiskConfig(
            market="NSE",
            broker="ZERODHA",
            mode="live",
            balance=100000.0,
        )
        # No credentials provided
        adapter = ZerodhaBrokerAdapter(
            api_key="",
            access_token="",
            mode="live",
            user_config=live_config,
        )
        res = adapter.place_order(self.ticket, confirm_live=True)
        self.assertFalse(res["success"])
        self.assertEqual(res["safety_gate"], "UNAUTHENTICATED")

    def test_alpaca_live_safety_gate_blocks_unconfirmed_order(self):
        live_config = UserRiskConfig(
            market="CRYPTO",
            broker="ALPACA",
            mode="live",
            balance=100000.0,
        )
        adapter = AlpacaBrokerAdapter(
            api_key="mock_key",
            secret_key="mock_secret",
            mode="live",
            user_config=live_config,
        )
        res = adapter.place_order(self.ticket, confirm_live=False)
        self.assertFalse(res["success"])
        self.assertEqual(res["safety_gate"], "FAILED_CONFIRMATION")

    def test_create_broker_adapter_factory(self):
        adapter1 = create_broker_adapter("PAPER", mode="paper")
        self.assertIsInstance(adapter1, PaperBrokerAdapter)

        adapter2 = create_broker_adapter("ZERODHA", mode="paper")
        self.assertIsInstance(adapter2, ZerodhaBrokerAdapter)

        adapter3 = create_broker_adapter("ALPACA", mode="paper")
        self.assertIsInstance(adapter3, AlpacaBrokerAdapter)


if __name__ == "__main__":
    unittest.main()
