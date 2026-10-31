"""Unit tests for Risk and Position Sizing Engine and Safety Gates."""

import unittest
from copilot.risk_engine import UserRiskConfig, RiskAndSizingEngine, OrderTicket


class TestRiskAndSizingEngine(unittest.TestCase):

    def setUp(self):
        self.config = UserRiskConfig(
            market="NSE",
            broker="PAPER",
            currency="INR",
            balance=100000.0,
            min_allocation=1000.0,
            max_allocation=25000.0,
            max_risk_pct=1.0,           # Max risk per trade = 1% of 100k = ₹1,000
            max_daily_loss=3000.0,
            max_open_exposure=60000.0,
            max_concurrent_positions=2,
            leverage=1.0,
            mode="paper",
        )
        self.risk_engine = RiskAndSizingEngine(self.config)

    def test_twelve_parameters_present_and_configurable(self):
        d = self.config.to_dict()
        self.assertGreaterEqual(len(d), 12)
        for key in [
            "market", "broker", "currency", "balance",
            "min_allocation", "max_allocation", "max_risk_pct",
            "max_daily_loss", "max_open_exposure", "max_concurrent_positions",
            "leverage", "mode"
        ]:
            self.assertIn(key, d)

    def test_position_sizing_respects_risk_budget(self):
        # Entry 2500, SL 2450 -> Risk per share = 50.
        # Max risk budget = 1% of 100k = 1000.
        # Qty based on risk = 1000 / 50 = 20 shares.
        # Capital required = 20 * 2500 = 50,000.
        # But max_allocation is 25,000! So qty should be capped at 25000 / 2500 = 10 shares.
        ticket = self.risk_engine.calculate_position_size(
            symbol="RELIANCE.NS",
            entry_price=2500.0,
            stop_loss=2450.0,
            take_profit=2670.0,
            planned_rr=3.2,
            strategy="TrendFollowing",
        )
        self.assertIsNotNone(ticket)
        self.assertEqual(ticket.quantity, 10.0)
        self.assertEqual(ticket.allocated_capital, 25000.0)
        self.assertAlmostEqual(ticket.risk_amount, 537.93, delta=2.0)
        self.assertLessEqual(ticket.risk_amount, 1000.0)

    def test_position_sizing_one_unit_lower_bound(self):
        # High priced share e.g. 1500, min allocation 1000
        ticket = self.risk_engine.calculate_position_size(
            symbol="TCS.NS",
            entry_price=1500.0,
            stop_loss=1470.0,
            take_profit=1600.0,
            planned_rr=3.1,
            strategy="TrendFollowing",
        )
        self.assertIsNotNone(ticket)
        self.assertGreaterEqual(ticket.quantity, 1.0)

    def test_rejection_when_capital_below_single_share_price(self):
        low_balance_config = UserRiskConfig(
            market="NSE",
            broker="PAPER",
            balance=1000.0,
            min_allocation=500.0,
            max_allocation=800.0,
            max_risk_pct=1.0,
        )
        low_engine = RiskAndSizingEngine(low_balance_config)
        # Attempt to buy stock priced at 2500
        ticket = low_engine.calculate_position_size(
            symbol="RELIANCE.NS",
            entry_price=2500.0,
            stop_loss=2400.0,
            take_profit=2850.0,
            planned_rr=3.2,
            strategy="TrendFollowing",
        )
        self.assertIsNone(ticket)

    def test_max_concurrent_positions_safety_gate(self):
        open_positions = {
            "TCS.NS": {"quantity": 10, "current_price": 3000.0},
            "INFY.NS": {"quantity": 20, "current_price": 1500.0},
        }
        ticket = OrderTicket(
            symbol="RELIANCE.NS",
            market="NSE",
            side="BUY",
            entry_price=2500.0,
            stop_loss=2450.0,
            take_profit=2660.0,
            quantity=5.0,
            allocated_capital=12500.0,
            risk_amount=250.0,
            planned_rr=3.2,
            strategy="TrendFollowing",
        )
        val = self.risk_engine.validate_order(ticket, open_positions=open_positions)
        self.assertFalse(val["allowed"])
        self.assertIn("MAX_CONCURRENT_POSITIONS", val["violations"])

    def test_daily_loss_circuit_breaker(self):
        self.risk_engine.daily_realized_loss = 3500.0  # Exceeds max_daily_loss of 3000
        ticket = OrderTicket(
            symbol="RELIANCE.NS",
            market="NSE",
            side="BUY",
            entry_price=2500.0,
            stop_loss=2450.0,
            take_profit=2660.0,
            quantity=5.0,
            allocated_capital=12500.0,
            risk_amount=250.0,
            planned_rr=3.2,
            strategy="TrendFollowing",
        )
        val = self.risk_engine.validate_order(ticket)
        self.assertFalse(val["allowed"])
        self.assertIn("DAILY_LOSS_BREACHED", val["violations"])


if __name__ == "__main__":
    unittest.main()
