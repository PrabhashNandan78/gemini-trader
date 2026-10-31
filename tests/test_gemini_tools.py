import json
import os
import unittest

import gemini_interface


class TestGeminiTools(unittest.TestCase):
    def test_gemini_tool_declarations_valid(self):
        tools = gemini_interface.GEMINI_TOOLS
        self.assertIsInstance(tools, list)
        self.assertGreaterEqual(len(tools), 6)
        tool_names = [t["name"] for t in tools]
        for expected in ("analyze_symbol", "scan_market", "backtest_strategy",
                         "evaluate_risk", "paper_trade", "get_market_clock"):
            self.assertIn(expected, tool_names)

        for t in tools:
            self.assertIn("name", t)
            self.assertIn("description", t)
            self.assertIn("parameters", t)
            self.assertEqual(t["parameters"]["type"], "OBJECT")

    def test_dispatch_tool_call_clock(self):
        res = gemini_interface.dispatch_tool_call("get_market_clock", {"symbol": "RELIANCE.NS"})
        self.assertIn("symbol", res)
        self.assertIn("market_open", res)
        self.assertEqual(res["symbol"], "RELIANCE.NS")

    def test_dispatch_tool_call_clock_crypto(self):
        res = gemini_interface.dispatch_tool_call("get_market_clock", {"symbol": "BTC-USD"})
        self.assertIn("symbol", res)
        self.assertTrue(res["market_open"])
        self.assertEqual(res["timezone"], "UTC")

    def test_dispatch_tool_call_risk_evaluation(self):
        order = {
            "ticker": "RELIANCE.NS",
            "action": "BUY",
            "type": "LIMIT",
            "limit": 1160.0,
            "shares": 50,
            "stop_loss": 1140.0,
            "take_profit": 1200.0,
        }
        res = gemini_interface.dispatch_tool_call("evaluate_risk", {"order": order})
        self.assertIn("approved", res)
        self.assertIn("reasons", res)

    def test_dispatch_unknown_tool(self):
        res = gemini_interface.dispatch_tool_call("nonexistent_tool", {})
        self.assertIn("error", res)

    def test_audit_logging_writes_records(self):
        gemini_interface.log_audit("test_action", {"test_key": "test_val"}, {"status": "ok"})
        audit_path = gemini_interface.AUDIT_FILE
        self.assertTrue(os.path.exists(audit_path))
        with open(audit_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        self.assertGreater(len(lines), 0)
        last_rec = json.loads(lines[-1])
        self.assertEqual(last_rec["action"], "test_action")
        self.assertEqual(last_rec["inputs"]["test_key"], "test_val")


if __name__ == "__main__":
    unittest.main()

