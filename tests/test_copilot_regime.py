"""Unit tests for Quantitative Indicators and Market Regime Detection."""

import math
import unittest
from copilot.regime import (
    calc_sma,
    calc_ema,
    calc_atr,
    calc_rsi,
    calc_bollinger_bands,
    calc_adx,
    MarketRegimeDetector,
    MarketRegimeType,
)


class TestIndicatorsAndRegime(unittest.TestCase):

    def setUp(self):
        # 30 bars of simulated ascending trend data
        self.bull_closes = [100.0 + (i * 1.5) for i in range(35)]
        self.bull_highs = [c + 1.0 for c in self.bull_closes]
        self.bull_lows = [c - 0.8 for c in self.bull_closes]
        self.bull_bars = [
            {
                "timestamp": 1700000000 + (i * 300),
                "open": self.bull_closes[i] - 0.5,
                "high": self.bull_highs[i],
                "low": self.bull_lows[i],
                "close": self.bull_closes[i],
                "volume": 10000 + (i * 100),
            }
            for i in range(35)
        ]

    def test_calc_sma(self):
        series = [10, 20, 30, 40, 50]
        sma3 = calc_sma(series, 3)
        self.assertTrue(math.isnan(sma3[0]))
        self.assertTrue(math.isnan(sma3[1]))
        self.assertAlmostEqual(sma3[2], 20.0)
        self.assertAlmostEqual(sma3[3], 30.0)
        self.assertAlmostEqual(sma3[4], 40.0)

    def test_calc_ema(self):
        series = [10, 10, 10, 20, 20, 20]
        ema3 = calc_ema(series, 3)
        self.assertTrue(math.isnan(ema3[0]))
        self.assertTrue(math.isnan(ema3[1]))
        self.assertEqual(ema3[2], 10.0)
        self.assertGreater(ema3[3], 10.0)

    def test_calc_atr(self):
        atr = calc_atr(self.bull_highs, self.bull_lows, self.bull_closes, period=14)
        self.assertEqual(len(atr), len(self.bull_closes))
        self.assertIsNotNone(atr[-1])
        self.assertGreater(atr[-1], 0.0)

    def test_calc_rsi(self):
        rsi = calc_rsi(self.bull_closes, period=14)
        self.assertEqual(len(rsi), len(self.bull_closes))
        # Strictly rising series should have high RSI
        self.assertIsNotNone(rsi[-1])
        self.assertGreater(rsi[-1], 70.0)

    def test_calc_bollinger_bands(self):
        bb = calc_bollinger_bands(self.bull_closes, period=20, std_dev=2.0)
        self.assertIn("upper", bb)
        self.assertIn("middle", bb)
        self.assertIn("lower", bb)
        self.assertIn("bandwidth", bb)
        self.assertGreater(bb["upper"][-1], bb["middle"][-1])
        self.assertGreater(bb["middle"][-1], bb["lower"][-1])

    def test_calc_adx(self):
        plus_di, minus_di, adx = calc_adx(self.bull_highs, self.bull_lows, self.bull_closes, period=14)
        self.assertEqual(len(adx), len(self.bull_closes))
        self.assertIsNotNone(adx[-1])
        self.assertGreater(adx[-1], 20.0)

    def test_regime_bull_trend(self):
        regime = MarketRegimeDetector.detect_regime(self.bull_bars)
        self.assertEqual(regime["regime"], MarketRegimeType.TREND_BULL.value)
        self.assertGreater(regime["adx"], 20.0)
        self.assertGreater(regime["confidence"], 0.5)

    def test_regime_choppy_flat(self):
        # 35 bars of flat/choppy prices
        flat_bars = [
            {
                "timestamp": 1700000000 + (i * 300),
                "open": 100.0 + (0.1 if i % 2 == 0 else -0.1),
                "high": 100.2,
                "low": 99.8,
                "close": 100.0 + (-0.1 if i % 2 == 0 else 0.1),
                "volume": 2000,
            }
            for i in range(35)
        ]
        regime = MarketRegimeDetector.detect_regime(flat_bars)
        self.assertIn(regime["regime"], [MarketRegimeType.LOW_VOL_CHOPPY.value, MarketRegimeType.RANGE_BOUND.value])

    def test_regime_empty_bars(self):
        regime = MarketRegimeDetector.detect_regime([])
        self.assertEqual(regime["regime"], MarketRegimeType.LOW_VOL_CHOPPY.value)
        self.assertEqual(regime["confidence"], 0.0)


if __name__ == "__main__":
    unittest.main()
