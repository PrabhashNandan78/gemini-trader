"""Unit tests for Copilot REST API and HTTP Server Request Handling."""

import json
import unittest
from copilot.server import CopilotServerState, generate_synthetic_bars


class TestCopilotServerLogic(unittest.TestCase):

    def setUp(self):
        self.server_state = CopilotServerState(
            mode="paper",
            broker_name="PAPER",
            initial_balance=100000.0,
        )

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


if __name__ == "__main__":
    unittest.main()
