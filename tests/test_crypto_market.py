import datetime
import importlib.util
import os
import unittest

from engine.asset_model import (
    AssetClass,
    format_currency,
    get_market_session,
    is_market_open,
    parse_symbol,
    to_yahoo_ticker,
)

# Dynamically import trader-budget/size.py and trader-risk/check.py
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


size_mod = load_module("size_mod", os.path.join(ROOT, "trader-budget", "size.py"))
risk_mod = load_module("risk_mod", os.path.join(ROOT, "trader-risk", "check.py"))


class TestCryptoMarket(unittest.TestCase):
    def test_crypto_ticker_parsing(self):
        meta = parse_symbol("BTC-USD")
        self.assertEqual(meta.raw_symbol, "BTC-USD")
        self.assertEqual(meta.asset_class, AssetClass.CRYPTO)
        self.assertEqual(meta.exchange, "CRYPTO")
        self.assertEqual(meta.base_currency, "BTC")
        self.assertEqual(meta.quote_currency, "USD")
        self.assertEqual(meta.timezone, "UTC")
        self.assertTrue(meta.allow_fractional)

    def test_crypto_inr_pair_parsing(self):
        meta = parse_symbol("BTC-INR")
        self.assertEqual(meta.asset_class, AssetClass.CRYPTO)
        self.assertEqual(meta.base_currency, "BTC")
        self.assertEqual(meta.quote_currency, "INR")
        self.assertTrue(meta.allow_fractional)

    def test_crypto_other_pairs(self):
        for s in ("ETH-USD", "SOL-USD", "DOGE-USD"):
            meta = parse_symbol(s)
            self.assertEqual(meta.asset_class, AssetClass.CRYPTO)
            self.assertTrue(meta.allow_fractional)

    def test_yahoo_ticker_conversion_crypto(self):
        self.assertEqual(to_yahoo_ticker("BTC-USD"), "BTC-USD")
        self.assertEqual(to_yahoo_ticker("ETH-INR"), "ETH-INR")

    def test_crypto_continuous_24_7(self):
        session = get_market_session(AssetClass.CRYPTO)
        self.assertTrue(session["continuous"])
        self.assertEqual(session["timezone"], "UTC")

        # Test on a weekend at midnight UTC: crypto is still open
        sunday_utc = datetime.datetime(2026, 6, 14, 0, 0, tzinfo=datetime.timezone.utc)
        open_status, msg = is_market_open("BTC-USD", sunday_utc)
        self.assertTrue(open_status)
        self.assertIn("24/7", msg)

    def test_crypto_fractional_position_sizing(self):
        # Entry $80,000, Stop $78,000 (risk = $2,000 per BTC).
        # Max loss $100 -> shares = 100 / 2000 = 0.05 BTC.
        risk_budget = 100.0
        risk_per_share = 2000.0
        qty = size_mod.shares_for(risk_budget, risk_per_share, allow_fractional=True)
        self.assertAlmostEqual(qty, 0.05, places=4)

    def test_crypto_fractional_bracket_order(self):
        plan = {
            "symbol": "BTC-USD",
            "entry": 80000.0,
            "stop": 78000.0,
            "target": 84000.0,
            "allow_crypto": True,
        }
        cfg = {
            "budget": 100000.0,
            "max_loss_per_trade_usd": 100.0,
            "allow_crypto": True,
        }
        sizing = size_mod.size_trade(plan, cfg)
        order = size_mod.bracket_order(sizing)
        self.assertIsNotNone(order)
        self.assertEqual(order["symbol"], "BTC-USD")
        self.assertEqual(float(order["qty"]), 0.05)

    def test_crypto_risk_check_fractional_qty(self):
        order = {
            "symbol": "BTC-USD",
            "side": "buy",
            "type": "market",
            "time_in_force": "day",
            "order_class": "bracket",
            "qty": "0.05",
            "take_profit_limit_price": "84000.00",
            "stop_loss_stop_price": "78000.00",
            "plan": {"entry": 80000.0, "stop": 78000.0, "target": 84000.0},
        }
        config = {
            "budget": 10000.0,
            "paper": True,
            "allow_crypto": True,
            "require_stop": True,
            "min_reward_risk": 2.0,
            "max_loss_per_trade_usd": 150.0,
            "max_daily_loss_usd": 500.0,
            "max_trades_per_day": 5,
        }
        # Run check
        verdict = risk_mod.check([order], config)
        res = verdict["results"][0]
        self.assertEqual(res["verdict"], "PASS", res.get("reasons"))


if __name__ == "__main__":
    unittest.main()
