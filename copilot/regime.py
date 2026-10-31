#!/usr/bin/env python3
"""Market Regime Detection & Quantitative Indicator Engine.

Analyzes intraday and daily OHLCV bars using standard mathematical formulas:
  - Exponential Moving Averages (EMA 9, 21, 50, 200)
  - Average True Range (ATR 14) and ATR Expansion Ratio
  - Wilder's Directional Movement Index (DMI: +DI, -DI, ADX 14)
  - Bollinger Bands (20 SMA +/- 2.0 SD) and Bandwidth
  - Relative Strength Index (RSI 14)
  - Volume SMA(20) and Relative Volume (RVOL)

Classifies market state into:
  - TREND_BULL: Strong bullish trend with directional momentum
  - TREND_BEAR: Strong bearish trend with downward momentum
  - VOLATILE_EXPANSION: Volatility breakout expansion
  - RANGE_BOUND: Horizontal trading channel with clear boundaries
  - LOW_VOL_CHOPPY: Compressed volatility with no directional edge (NO TRADE)
"""

import math
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Any, Tuple


class MarketRegime(str, Enum):
    TREND_BULL = "TREND_BULL"
    TREND_BEAR = "TREND_BEAR"
    VOLATILE_EXPANSION = "VOLATILE_EXPANSION"
    RANGE_BOUND = "RANGE_BOUND"
    LOW_VOL_CHOPPY = "LOW_VOL_CHOPPY"


MarketRegimeType = MarketRegime


@dataclass
class IndicatorSnapshot:
    price: float
    ema_9: float
    ema_21: float
    ema_50: float
    ema_200: Optional[float]
    atr_14: float
    atr_ratio: float
    adx_14: float
    plus_di: float
    minus_di: float
    rsi_14: float
    bb_upper: float
    bb_middle: float
    bb_lower: float
    bb_bandwidth: float
    rvol: float


@dataclass
class RegimeAnalysis:
    regime: str
    trend_strength: str        # STRONG, MODERATE, WEAK, NONE
    volatility_state: str      # EXPANDING, NORMAL, COMPRESSED
    liquidity_status: str      # ADEQUATE, THIN, ILLIQUID
    indicators: IndicatorSnapshot
    eligible_strategies: List[str]
    is_tradable: bool
    summary: str


# ---------------------------------------------------------------- Indicator Formulas

def calc_sma(values: List[float], period: int) -> List[float]:
    """Calculates Simple Moving Average."""
    if len(values) < period:
        return [float("nan")] * len(values)
    res = []
    window_sum = sum(values[:period])
    res.extend([float("nan")] * (period - 1))
    res.append(window_sum / period)
    for i in range(period, len(values)):
        window_sum += values[i] - values[i - period]
        res.append(window_sum / period)
    return res


def calc_ema(values: List[float], period: int) -> List[float]:
    """Calculates Exponential Moving Average."""
    if not values:
        return []
    if len(values) < period:
        return [float("nan")] * len(values)
    k = 2.0 / (period + 1.0)
    res = [float("nan")] * (period - 1)
    sma_seed = sum(values[:period]) / period
    res.append(sma_seed)
    prev = sma_seed
    for val in values[period:]:
        curr = (val * k) + (prev * (1.0 - k))
        res.append(curr)
        prev = curr
    return res


def calc_atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> List[float]:
    """Calculates Average True Range."""
    n = len(closes)
    if n < 2:
        return [float("nan")] * n
    tr = [highs[0] - lows[0]]
    for i in range(1, n):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr.append(max(hl, hc, lc))
    return calc_ema(tr, period)


def calc_rsi(closes: List[float], period: int = 14) -> List[float]:
    """Calculates Relative Strength Index."""
    n = len(closes)
    if n <= period:
        return [float("nan")] * n
    gains, losses = [], []
    for i in range(1, n):
        diff = closes[i] - closes[i - 1]
        gains.append(max(0.0, diff))
        losses.append(max(0.0, -diff))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    rsi_res = [float("nan")] * period
    if avg_loss == 0:
        rsi_res.append(100.0)
    else:
        rs = avg_gain / avg_loss
        rsi_res.append(100.0 - (100.0 / (1.0 + rs)))

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi_res.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_res.append(round(100.0 - (100.0 / (1.0 + rs)), 2))
    return rsi_res


class BollingerBandsResult(tuple):
    def __new__(cls, middle, upper, lower, bandwidth):
        return super().__new__(cls, (middle, upper, lower, bandwidth))

    @property
    def middle(self):
        return super().__getitem__(0)

    @property
    def upper(self):
        return super().__getitem__(1)

    @property
    def lower(self):
        return super().__getitem__(2)

    @property
    def bandwidth(self):
        return super().__getitem__(3)

    def __getitem__(self, item):
        if isinstance(item, str):
            if item in ("middle", "mid"):
                return super().__getitem__(0)
            if item == "upper":
                return super().__getitem__(1)
            if item == "lower":
                return super().__getitem__(2)
            if item in ("bandwidth", "bw"):
                return super().__getitem__(3)
        return super().__getitem__(item)

    def __contains__(self, item):
        if item in ("middle", "mid", "upper", "lower", "bandwidth", "bw"):
            return True
        return super().__contains__(item)


class ADXResult(tuple):
    def __new__(cls, plus_di, minus_di, adx):
        return super().__new__(cls, (plus_di, minus_di, adx))

    @property
    def plus_di(self):
        return super().__getitem__(0)

    @property
    def minus_di(self):
        return super().__getitem__(1)

    @property
    def adx(self):
        return super().__getitem__(2)

    def __getitem__(self, item):
        if isinstance(item, str):
            if item in ("plus_di", "+di"):
                return super().__getitem__(0)
            if item in ("minus_di", "-di"):
                return super().__getitem__(1)
            if item == "adx":
                return super().__getitem__(2)
        return super().__getitem__(item)


def calc_bollinger_bands(
    closes: List[float],
    period: int = 20,
    mult: float = 2.0,
    std_dev: Optional[float] = None,
) -> BollingerBandsResult:
    """Calculates Bollinger Bands (Middle, Upper, Lower, Bandwidth)."""
    multiplier = std_dev if std_dev is not None else mult
    n = len(closes)
    if n < period:
        nan_list = [float("nan")] * n
        return BollingerBandsResult(nan_list, nan_list, nan_list, nan_list)
    mid = calc_sma(closes, period)
    upper, lower, bandwidth = [], [], []
    for i in range(n):
        if math.isnan(mid[i]):
            upper.append(float("nan"))
            lower.append(float("nan"))
            bandwidth.append(float("nan"))
            continue
        window = closes[i - period + 1 : i + 1]
        variance = sum((x - mid[i]) ** 2 for x in window) / period
        sd = math.sqrt(variance)
        u = mid[i] + (multiplier * sd)
        l = mid[i] - (multiplier * sd)
        bw = (u - l) / max(mid[i], 1e-9)
        upper.append(round(u, 4))
        lower.append(round(l, 4))
        bandwidth.append(round(bw, 4))
    return BollingerBandsResult(mid, upper, lower, bandwidth)


def calc_adx(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> ADXResult:
    """Calculates Wilder's Directional Movement (+DI, -DI, ADX)."""
    n = len(closes)
    if n <= period:
        nan_list = [float("nan")] * n
        return ADXResult(nan_list, nan_list, nan_list)

    tr, plus_dm, minus_dm = [], [], []
    for i in range(1, n):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr.append(max(hl, hc, lc))

        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        if up_move > down_move and up_move > 0:
            plus_dm.append(up_move)
        else:
            plus_dm.append(0.0)

        if down_move > up_move and down_move > 0:
            minus_dm.append(down_move)
        else:
            minus_dm.append(0.0)

    # Wilder smoothing (initial sum)
    smooth_tr = sum(tr[:period])
    smooth_plus = sum(plus_dm[:period])
    smooth_minus = sum(minus_dm[:period])

    plus_di_list = [float("nan")] * period
    minus_di_list = [float("nan")] * period
    dx_list = []

    def calc_di_dx(s_tr, s_plus, s_minus):
        p_di = (s_plus / s_tr * 100.0) if s_tr > 0 else 0.0
        m_di = (s_minus / s_tr * 100.0) if s_tr > 0 else 0.0
        diff = abs(p_di - m_di)
        tot = p_di + m_di
        dx = (diff / tot * 100.0) if tot > 0 else 0.0
        return p_di, m_di, dx

    p_di, m_di, dx = calc_di_dx(smooth_tr, smooth_plus, smooth_minus)
    plus_di_list.append(round(p_di, 2))
    minus_di_list.append(round(m_di, 2))
    dx_list.append(dx)

    for i in range(period, len(tr)):
        smooth_tr = smooth_tr - (smooth_tr / period) + tr[i]
        smooth_plus = smooth_plus - (smooth_plus / period) + plus_dm[i]
        smooth_minus = smooth_minus - (smooth_minus / period) + minus_dm[i]
        p_di, m_di, dx = calc_di_dx(smooth_tr, smooth_plus, smooth_minus)
        plus_di_list.append(round(p_di, 2))
        minus_di_list.append(round(m_di, 2))
        dx_list.append(dx)

    # ADX is the Wilder smoothed average of DX
    if len(dx_list) < period:
        adx_list = [float("nan")] * len(plus_di_list)
        return ADXResult(plus_di_list, minus_di_list, adx_list)

    adx_smooth = sum(dx_list[:period]) / period
    adx_list = [float("nan")] * (period * 2 - 1)
    adx_list.append(round(adx_smooth, 2))
    for i in range(period, len(dx_list)):
        adx_smooth = (adx_smooth * (period - 1) + dx_list[i]) / period
        adx_list.append(round(adx_smooth, 2))

    return ADXResult(plus_di_list, minus_di_list, adx_list)


# ---------------------------------------------------------------- Market Regime Detector

class MarketRegimeDetector:
    """Evaluates multi-period bars to classify the current market regime."""

    @staticmethod
    def analyze(bars: List[Dict[str, Any]], asset_class: str = "equity_in") -> RegimeAnalysis:
        """Determines the market regime and eligible strategies from normalized bars."""
        if not bars or len(bars) < 25:
            # Insufficient bars for reliable regime determination
            dummy = IndicatorSnapshot(
                price=bars[-1]["close"] if bars else 0.0,
                ema_9=0.0, ema_21=0.0, ema_50=0.0, ema_200=None,
                atr_14=0.0, atr_ratio=1.0, adx_14=0.0, plus_di=0.0, minus_di=0.0,
                rsi_14=50.0, bb_upper=0.0, bb_middle=0.0, bb_lower=0.0, bb_bandwidth=0.0,
                rvol=1.0,
            )
            return RegimeAnalysis(
                regime=MarketRegime.LOW_VOL_CHOPPY,
                trend_strength="NONE",
                volatility_state="COMPRESSED",
                liquidity_status="THIN",
                indicators=dummy,
                eligible_strategies=[],
                is_tradable=False,
                summary="Insufficient historical bars (<25 bars) to establish statistical regime.",
            )

        closes = [float(b["close"]) for b in bars]
        highs = [float(b["high"]) for b in bars]
        lows = [float(b["low"]) for b in bars]
        vols = [float(b.get("volume", 0)) for b in bars]
        curr_price = closes[-1]

        # Calculate indicators
        ema9 = calc_ema(closes, 9)[-1]
        ema21 = calc_ema(closes, 21)[-1]
        ema50 = calc_ema(closes, 50)[-1] if len(closes) >= 50 else ema21
        ema200 = calc_ema(closes, 200)[-1] if len(closes) >= 200 else None

        atrs = calc_atr(highs, lows, closes, 14)
        atr14 = atrs[-1] if atrs and not math.isnan(atrs[-1]) else (highs[-1] - lows[-1])
        # ATR Ratio: current ATR vs 20-period average ATR
        valid_atrs = [a for a in atrs[-20:] if not math.isnan(a)]
        avg_atr = sum(valid_atrs) / len(valid_atrs) if valid_atrs else atr14
        atr_ratio = round(atr14 / max(avg_atr, 1e-9), 2)

        plus_di, minus_di, adx_vals = calc_adx(highs, lows, closes, 14)
        adx = adx_vals[-1] if adx_vals and not math.isnan(adx_vals[-1]) else 15.0
        p_di = plus_di[-1] if plus_di and not math.isnan(plus_di[-1]) else 20.0
        m_di = minus_di[-1] if minus_di and not math.isnan(minus_di[-1]) else 20.0

        rsis = calc_rsi(closes, 14)
        rsi = rsis[-1] if rsis and not math.isnan(rsis[-1]) else 50.0

        bb_mid, bb_u, bb_l, bb_bw = calc_bollinger_bands(closes, 20, 2.0)
        bw = bb_bw[-1] if bb_bw and not math.isnan(bb_bw[-1]) else 0.02
        b_mid = bb_mid[-1] if bb_mid and not math.isnan(bb_mid[-1]) else curr_price
        b_u = bb_u[-1] if bb_u and not math.isnan(bb_u[-1]) else curr_price * 1.02
        b_l = bb_l[-1] if bb_l and not math.isnan(bb_l[-1]) else curr_price * 0.98

        # Relative Volume (RVOL)
        # Note: Index cash feeds (e.g. ^NSEI, ^NSEBANK) do not report trading volume.
        # When all volumes are zero, default RVOL to 1.0 (adequate) rather than falsely penalizing as illiquid.
        vol_sma20 = sum(vols[-20:]) / 20.0 if len(vols) >= 20 else (sum(vols) / max(len(vols), 1))
        if vol_sma20 > 0:
            rvol = round(vols[-1] / vol_sma20, 2)
        else:
            rvol = 1.0

        snapshot = IndicatorSnapshot(
            price=round(curr_price, 4),
            ema_9=round(ema9, 4),
            ema_21=round(ema21, 4),
            ema_50=round(ema50, 4),
            ema_200=round(ema200, 4) if ema200 else None,
            atr_14=round(atr14, 4),
            atr_ratio=atr_ratio,
            adx_14=round(adx, 2),
            plus_di=round(p_di, 2),
            minus_di=round(m_di, 2),
            rsi_14=round(rsi, 2),
            bb_upper=round(b_u, 4),
            bb_middle=round(b_mid, 4),
            bb_lower=round(b_l, 4),
            bb_bandwidth=round(bw, 4),
            rvol=rvol,
        )

        # ---------------- Regime Classification Decision Logic
        # 1. Trend Evaluation
        is_bull_alignment = (curr_price > ema9 > ema21) and (p_di > m_di)
        is_bear_alignment = (curr_price < ema9 < ema21) and (m_di > p_di)

        if adx >= 25.0 and is_bull_alignment and rsi > 48.0:
            regime = MarketRegime.TREND_BULL
            trend_str = "STRONG" if adx > 35 else "MODERATE"
            vol_state = "EXPANDING" if atr_ratio > 1.15 else "NORMAL"
            eligible = ["TrendFollowing_EMA", "ORB_3R"]
            tradable = True
            summary = f"Bullish trend regime confirmed (ADX {adx:.1f}, EMA9 > EMA21, RSI {rsi:.1f}). Favouring trend-continuation setups."

        elif adx >= 25.0 and is_bear_alignment and rsi < 52.0:
            regime = MarketRegime.TREND_BEAR
            trend_str = "STRONG" if adx > 35 else "MODERATE"
            vol_state = "EXPANDING" if atr_ratio > 1.15 else "NORMAL"
            eligible = ["TrendFollowing_EMA"]
            tradable = True
            summary = f"Bearish trend regime confirmed (ADX {adx:.1f}, EMA9 < EMA21, RSI {rsi:.1f}). Short-side trend following eligible."

        elif atr_ratio >= 1.30 and rvol >= 1.25:
            regime = MarketRegime.VOLATILE_EXPANSION
            trend_str = "MODERATE"
            vol_state = "EXPANDING"
            eligible = ["VolatilityBreakout_ATR", "ORB_3R"]
            tradable = True
            summary = f"Volatility expansion regime detected (ATR ratio {atr_ratio}x, RVOL {rvol}x). Favouring breakout setups."

        elif adx < 22.0 and bw < 0.035 and atr_ratio < 0.85:
            regime = MarketRegime.LOW_VOL_CHOPPY
            trend_str = "NONE"
            vol_state = "COMPRESSED"
            eligible = []  # NO TRADE
            tradable = False
            summary = f"Low volatility choppy regime (ADX {adx:.1f}, compressed ATR ratio {atr_ratio}x). Edge is statistically absent; NO TRADE recommended."

        else:
            # Default Range-Bound / Mean-Reverting
            regime = MarketRegime.RANGE_BOUND
            trend_str = "WEAK"
            vol_state = "NORMAL"
            eligible = ["MeanReversion_RSI"]
            tradable = True
            summary = f"Range-bound oscillating regime (ADX {adx:.1f}, RSI {rsi:.1f}, BB Bandwidth {bw:.3f}). Favouring mean-reversion at range extremes."

        # Liquidity check
        liq_status = "ADEQUATE" if rvol >= 0.75 else ("THIN" if rvol >= 0.4 else "ILLIQUID")

        return RegimeAnalysis(
            regime=regime,
            trend_strength=trend_str,
            volatility_state=vol_state,
            liquidity_status=liq_status,
            indicators=snapshot,
            eligible_strategies=eligible,
            is_tradable=tradable,
            summary=summary,
        )

    @classmethod
    def detect_regime(cls, bars: List[Dict[str, Any]], asset_class: str = "equity_in") -> Dict[str, Any]:
        """Convenience wrapper returning a dictionary format for server and tests."""
        res = cls.analyze(bars, asset_class=asset_class)
        conf = 0.85 if res.regime in (MarketRegime.TREND_BULL, MarketRegime.TREND_BEAR) else (0.75 if res.regime == MarketRegime.VOLATILE_EXPANSION else 0.60)
        if not res.is_tradable:
            conf = 0.0
        return {
            "regime": res.regime,
            "trend_strength": res.trend_strength,
            "volatility_state": res.volatility_state,
            "liquidity_status": res.liquidity_status,
            "adx": res.indicators.adx_14,
            "confidence": conf,
            "indicators": res.indicators.__dict__,
            "summary": res.summary,
            "is_tradable": res.is_tradable,
        }
