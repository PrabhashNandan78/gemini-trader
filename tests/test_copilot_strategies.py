"""Unit tests for Adaptive Strategy Engine and 3:1 Net R:R Enforcement."""

import unittest
from copilot.cost_model import TransactionCostModel, MarketAssetType
from copilot.risk_engine import UserRiskConfig
from copilot.strategies import AdaptiveStrategyEngine


class TestAdaptiveStrategies(unittest.TestCase):

    def setUp(self):
        self.engine = AdaptiveStrategyEngine()
        self.config_nse = UserRiskConfig(
            market="NSE",
            broker="PAPER",
            currency="INR",
            balance=100000.0,
            max_risk_pct=1.0,
            min_allocation=1000.0,
            max_allocation=50000.0,
        )

        # Generate 40 bars with strong bullish momentum for testing
        self.bars = []
        price = 2400.0
        for i in range(45):
            price += 3.5
            self.bars.append({
                "timestamp": 1700000000 + (i * 300),
                "open": price - 1.0,
                "high": price + 2.0,
                "low": price - 1.5,
                "close": price,
                "volume": 25000,
            })

    def test_cost_model_nse_statutory_charges(self):
        # Buy at 2400, Sell at 2500, Qty 10
        friction = TransactionCostModel.calculate_total_friction(
            market=MarketAssetType.EQUITY_NSE,
            entry_price=2400.0,
            exit_price=2500.0,
            quantity=10,
        )
        self.assertGreater(friction["total_friction"], 0.0)
        self.assertGreater(friction["entry_fees"], 0.0)
        self.assertGreater(friction["exit_fees"], 0.0)
        self.assertIn("brokerage", friction["breakdown"])
        self.assertIn("stt", friction["breakdown"])
        self.assertIn("turnover_fee", friction["breakdown"])
        self.assertIn("gst", friction["breakdown"])
        self.assertIn("stamp_duty", friction["breakdown"])
        self.assertIn("slippage", friction["breakdown"])

    def test_net_reward_to_risk_calculation(self):
        # Entry 100, SL 95 (Gross Risk = 5), TP 116 (Gross Reward = 16 -> Gross R:R = 3.20)
        rr_info = TransactionCostModel.compute_net_reward_to_risk(
            market=MarketAssetType.EQUITY_NSE,
            entry_price=100.0,
            stop_loss=95.0,
            take_profit=116.0,
            quantity=100,
        )
        self.assertIn("net_rr", rr_info)
        self.assertIn("net_risk", rr_info)
        self.assertIn("net_reward", rr_info)
        # Net R:R after friction should be slightly lower than gross 3.20, but >= 3.0
        self.assertGreaterEqual(rr_info["net_rr"], 3.0)
        self.assertLess(rr_info["net_rr"], 3.25)

    def test_minimum_3r_enforcement_rejects_sub_3r(self):
        # If gross target is only 2.0R (e.g. SL 95, TP 110), Net R:R will be < 3.0
        rr_info = TransactionCostModel.compute_net_reward_to_risk(
            market=MarketAssetType.EQUITY_NSE,
            entry_price=100.0,
            stop_loss=95.0,
            take_profit=110.0,
            quantity=100,
        )
        self.assertLess(rr_info["net_rr"], 3.0)

    def test_strategy_engine_analysis_generates_valid_ticket_or_no_trade(self):
        res = self.engine.analyze_and_generate_ticket(
            bars=self.bars,
            symbol="RELIANCE.NS",
            market="NSE",
            user_config=self.config_nse,
        )
        self.assertIn("decision", res)
        self.assertIn("regime", res)
        self.assertIn("selected_strategy", res)

        if res["decision"] == "TRADE":
            ticket = res["ticket"]
            self.assertIsNotNone(ticket)
            # Planned Net R:R MUST be >= 3.0
            self.assertGreaterEqual(ticket["planned_rr"], 3.0)
            self.assertGreater(ticket["quantity"], 0)
            self.assertGreater(ticket["allocated_capital"], 0)
            self.assertGreater(ticket["risk_amount"], 0)
        else:
            self.assertIsNone(res["ticket"])
            self.assertGreater(len(res["reasons"]), 0)

    def test_choppy_regime_forces_no_trade(self):
        # Generate choppy flat bars
        choppy_bars = [
            {
                "timestamp": 1700000000 + (i * 300),
                "open": 2000.0,
                "high": 2000.5,
                "low": 1999.5,
                "close": 2000.0,
                "volume": 1000,
            }
            for i in range(40)
        ]
        res = self.engine.analyze_and_generate_ticket(
            bars=choppy_bars,
            symbol="TCS.NS",
            market="NSE",
            user_config=self.config_nse,
        )
        self.assertEqual(res["decision"], "NO_TRADE")
        self.assertGreater(len(res["reasons"]), 0)


if __name__ == "__main__":
    unittest.main()
