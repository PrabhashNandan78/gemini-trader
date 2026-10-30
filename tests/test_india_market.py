import datetime
import unittest

from engine.asset_model import (
    AssetClass,
    NSE_HOLIDAYS,
    format_currency,
    get_market_session,
    is_market_open,
    parse_symbol,
    to_yahoo_ticker,
)
from engine.market_data import MarketDataProvider


class TestIndiaMarket(unittest.TestCase):
    def test_nse_ticker_parsing(self):
        meta = parse_symbol("RELIANCE.NS")
        self.assertEqual(meta.raw_symbol, "RELIANCE.NS")
        self.assertEqual(meta.asset_class, AssetClass.EQUITY_IN)
        self.assertEqual(meta.exchange, "NSE")
        self.assertEqual(meta.quote_currency, "INR")
        self.assertEqual(meta.timezone, "Asia/Kolkata")
        self.assertEqual(meta.bars_per_day, 75)
        self.assertFalse(meta.allow_fractional)

    def test_nse_other_tickers(self):
        for s in ("TCS.NS", "INFY.NS", "HDFCBANK.NS", "TATAMOTORS.NS"):
            meta = parse_symbol(s)
            self.assertEqual(meta.asset_class, AssetClass.EQUITY_IN)
            self.assertEqual(meta.exchange, "NSE")
            self.assertEqual(meta.quote_currency, "INR")

    def test_nifty_index_parsing(self):
        meta = parse_symbol("^NSEI")
        self.assertEqual(meta.asset_class, AssetClass.INDEX)
        self.assertEqual(meta.exchange, "NSE")
        self.assertEqual(meta.quote_currency, "INR")
        self.assertEqual(meta.timezone, "Asia/Kolkata")

    def test_yahoo_ticker_conversion_preserves_suffix(self):
        self.assertEqual(to_yahoo_ticker("RELIANCE.NS"), "RELIANCE.NS")
        self.assertEqual(to_yahoo_ticker("TCS.BO"), "TCS.BO")
        self.assertEqual(to_yahoo_ticker("^NSEI"), "^NSEI")

    def test_inr_currency_formatting(self):
        self.assertEqual(format_currency(1234.5, "INR"), "INR 1,234.50")
        self.assertEqual(format_currency(50000, "INR"), "INR 50,000.00")
        self.assertEqual(format_currency(1234.5, "INR", use_symbol=True), "\u20b91,234.50")

    def test_nse_trading_session_hours(self):
        session = get_market_session(AssetClass.EQUITY_IN)
        self.assertEqual(session["open"], (9, 15))
        self.assertEqual(session["close"], (15, 30))
        self.assertEqual(session["bars_per_day"], 75)
        self.assertEqual(session["timezone"], "Asia/Kolkata")

    def test_nse_holidays_defined(self):
        # 2026 Republic Day Jan 26, Independence Day Aug 15, Gandhi Jayanti Oct 2
        self.assertIn("2026-01-26", NSE_HOLIDAYS)
        self.assertIn("2026-10-02", NSE_HOLIDAYS)
        self.assertIn("2026-12-25", NSE_HOLIDAYS)

    def test_nse_market_open_check(self):
        # Wednesday 2026-06-10 at 10:30 AM IST (open)
        # Note: 10:30 IST is 05:00 UTC
        wednesday_open_utc = datetime.datetime(2026, 6, 10, 5, 0, tzinfo=datetime.timezone.utc)
        open_status, msg = is_market_open("RELIANCE.NS", wednesday_open_utc)
        self.assertTrue(open_status, msg)

        # Wednesday 2026-06-10 at 02:00 AM IST (closed, before 09:15)
        early_utc = datetime.datetime(2026, 6, 9, 20, 30, tzinfo=datetime.timezone.utc)
        open_status, msg = is_market_open("RELIANCE.NS", early_utc)
        self.assertFalse(open_status)
        self.assertTrue("closed" in msg.lower() or "pre-market" in msg.lower())

        # Saturday 2026-06-13 (closed on weekend)
        saturday_utc = datetime.datetime(2026, 6, 13, 5, 0, tzinfo=datetime.timezone.utc)
        open_status, msg = is_market_open("RELIANCE.NS", saturday_utc)
        self.assertFalse(open_status)
        self.assertIn("weekend", msg.lower())

        # Holiday 2026-01-26 (Republic Day)
        holiday_utc = datetime.datetime(2026, 1, 26, 5, 0, tzinfo=datetime.timezone.utc)
        open_status, msg = is_market_open("RELIANCE.NS", holiday_utc)
        self.assertFalse(open_status)
        self.assertIn("holiday", msg.lower())

    def test_market_data_provider_fixture_fallback(self):
        provider = MarketDataProvider(use_network=False)
        # When offline and no fixture found, returns gracefully empty list
        bars = provider.get_intraday_bars("UNKNOWN_TICKER.NS")
        self.assertEqual(bars, [])


if __name__ == "__main__":
    unittest.main()
