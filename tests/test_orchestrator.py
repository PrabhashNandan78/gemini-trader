import io
import json
import os
import sys
import unittest

import run_trader


class TestOrchestrator(unittest.TestCase):
    def setUp(self):
        self._stdout = sys.stdout
        self._stderr = sys.stderr
        sys.stdout = io.StringIO()
        sys.stderr = io.StringIO()

    def tearDown(self):
        sys.stdout = self._stdout
        sys.stderr = self._stderr

    def test_cmd_clock_india(self):
        parser = run_trader.build_parser()
        args = parser.parse_args(["clock", "--symbol", "RELIANCE.NS"])
        code = run_trader.cmd_clock(args)
        out = sys.stdout.getvalue()
        self.assertIn("RELIANCE.NS", out)
        self.assertIn("Asia/Kolkata", out)

    def test_cmd_clock_crypto(self):
        parser = run_trader.build_parser()
        args = parser.parse_args(["clock", "--symbol", "BTC-USD"])
        code = run_trader.cmd_clock(args)
        out = sys.stdout.getvalue()
        self.assertIn("BTC-USD", out)
        self.assertIn("OPEN", out)
        self.assertIn("24/7", out)

    def test_cmd_gemini_tools_export(self):
        parser = run_trader.build_parser()
        args = parser.parse_args(["gemini-tools"])
        code = run_trader.cmd_gemini_tools(args)
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists("gemini_tools.json"))

    def test_cmd_paper_trade_dry_run(self):
        parser = run_trader.build_parser()
        args = parser.parse_args([
            "paper-trade",
            "--symbol", "RELIANCE.NS",
            "--action", "BUY",
            "--shares", "2",
            "--limit", "1160.0",
            "--stop", "1140.0",
            "--target", "1200.0",
            "--skip-window",
        ])
        code = run_trader.cmd_paper_trade(args)
        self.assertEqual(code, 0)
        out = sys.stdout.getvalue()
        self.assertIn("DRY RUN", out)
        self.assertIn("RELIANCE.NS", out)

    def test_cmd_paper_trade_crypto_fractional(self):
        parser = run_trader.build_parser()
        args = parser.parse_args([
            "paper-trade",
            "--symbol", "BTC-USD",
            "--action", "BUY",
            "--shares", "0.01",
            "--limit", "80000.0",
            "--stop", "79000.0",
            "--target", "83000.0",
            "--skip-window",
        ])
        code = run_trader.cmd_paper_trade(args)
        self.assertEqual(code, 0)
        out = sys.stdout.getvalue()
        self.assertIn("BTC-USD", out)
        self.assertIn("0.01", out)

    def test_cmd_paper_trade_rejects_missing_stop(self):
        parser = run_trader.build_parser()
        args = parser.parse_args([
            "paper-trade",
            "--symbol", "RELIANCE.NS",
            "--action", "BUY",
            "--shares", "10",
            "--limit", "1160.0",
            "--target", "1200.0",
        ])
        code = run_trader.cmd_paper_trade(args)
        self.assertNotEqual(code, 0)
        out = sys.stdout.getvalue()
        self.assertIn("REJECTED", out)


if __name__ == "__main__":
    unittest.main()
