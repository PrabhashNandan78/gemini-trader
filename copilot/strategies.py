#!/usr/bin/env python3
"""Adaptive Strategy Engine with Mandatory 3:1 Net Reward-to-Risk Enforcement.

Houses validated quantitative strategies:
  1. TrendFollowing_EMA: Trend following pullbacks in confirmed trends.
  2. ORB_3R: Opening Range Breakout engineered for 3:1 net reward-to-risk.
  3. VolatilityBreakout_ATR: Squeeze expansion breakout with ATR channel targets.
  4. MeanReversion_RSI: Range-bound extreme oversold/overbought counter-trend setups.

The AdaptiveStrategyEngine matches instrument, market, and detected regime
to the validated strategy, and returns NO TRADE if no setup satisfies the >= 3.0 net R:R rule.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional, Any, Tuple

from engine.asset_model import AssetInfo, parse_symbol
from copilot.cost_model import TransactionCostModel, CostBreakdown
from copilot.regime import MarketRegime, MarketRegimeDetector, RegimeAnalysis, IndicatorSnapshot


@dataclass
class TradeSignal:
    strategy_name: str
    symbol: str
    action: str              # "BUY" or "SELL"
    entry_price: float
    stop_loss: float
    take_profit: float
    gross_rr: float
    net_rr: float            # Must strictly be >= 3.0
    cost_breakdown: CostBreakdown
    rationale: str
    regime: str
    timestamp: str
    status: str = "VALIDATED"


class BaseStrategy(ABC):
    """Abstract base class for all trading strategies."""

    def __init__(self, name: str, applicable_regimes: List[str]):
        self.name = name
        self.applicable_regimes = applicable_regimes

    @abstractmethod
    def evaluate(
        self,
        bars: List[Dict[str, Any]],
        regime: RegimeAnalysis,
        asset: AssetInfo,
    ) -> Optional[TradeSignal]:
        """Evaluates bars and returns a TradeSignal if setup is valid, or None."""
        pass


# ---------------------------------------------------------------- Strategy 1: Trend Following EMA

class TrendFollowingStrategy(BaseStrategy):
    """Trend Following on Pullback to EMA21 with EMA9 continuation.
    
    Regimes: TREND_BULL, TREND_BEAR
    Target: 3.2x gross risk distance (guaranteeing >= 3.0 net R:R after costs).
    """

    def __init__(self):
        super().__init__(
            name="TrendFollowing_EMA",
            applicable_regimes=[MarketRegime.TREND_BULL, MarketRegime.TREND_BEAR],
        )

    def evaluate(
        self,
        bars: List[Dict[str, Any]],
        regime: RegimeAnalysis,
        asset: AssetInfo,
    ) -> Optional[TradeSignal]:
        if regime.regime not in self.applicable_regimes or len(bars) < 20:
            return None

        inds = regime.indicators
        curr_bar = bars[-1]
        prev_bar = bars[-2]
        curr_price = float(curr_bar["close"])
        atr = inds.atr_14

        # Minimum sensible ATR required to prevent division by near-zero spreads
        if atr <= 0.0001:
            return None

        if regime.regime == MarketRegime.TREND_BULL:
            # Bull Trend Setup:
            # Price pulled back near EMA21 and closed back above EMA9 with rising RSI
            low_touched_ema21 = float(prev_bar["low"]) <= inds.ema_21 * 1.002 or float(curr_bar["low"]) <= inds.ema_21 * 1.002
            closed_above_ema9 = curr_price > inds.ema_9
            momentum_ok = inds.rsi_14 >= 50.0 and inds.plus_di > inds.minus_di

            if low_touched_ema21 and closed_above_ema9 and momentum_ok:
                entry = curr_price
                # Stop loss placed below the recent swing low / below EMA21 - 0.5 ATR
                stop = round(min(float(curr_bar["low"]), float(prev_bar["low"])) - (0.2 * atr), asset.price_decimals)
                risk = entry - stop
                if risk <= (entry * 0.001):  # Stop too tight (less than 0.1%), adjust to 1.0 ATR
                    stop = round(entry - (1.0 * atr), asset.price_decimals)
                    risk = entry - stop

                # Calculate target to achieve >= 3.0 net R:R
                # Target multiplier = 3.25x risk to cover friction
                target = round(entry + (3.25 * risk), asset.price_decimals)

                # Compute exact net R:R after all statutory charges and slippage
                net_rr, net_risk, net_reward, costs = TransactionCostModel.compute_net_reward_to_risk(
                    asset_class=asset.asset_class,
                    side="BUY",
                    entry_price=entry,
                    stop_loss=stop,
                    take_profit=target,
                    qty=1.0,
                )

                if net_rr >= 3.0:
                    return TradeSignal(
                        strategy_name=self.name,
                        symbol=asset.symbol,
                        action="BUY",
                        entry_price=entry,
                        stop_loss=stop,
                        take_profit=target,
                        gross_rr=round((target - entry) / risk, 2),
                        net_rr=net_rr,
                        cost_breakdown=costs,
                        rationale=f"Bullish pullback to EMA21 ({inds.ema_21:.2f}) with close above EMA9 ({inds.ema_9:.2f}). ADX {inds.adx_14:.1f} confirms trend.",
                        regime=regime.regime,
                        timestamp=curr_bar.get("datetime_local", "N/A"),
                    )

        elif regime.regime == MarketRegime.TREND_BEAR and asset.asset_class == "crypto":
            # Bear Trend Setup (Crypto spot shorting/hedging where supported)
            high_touched_ema21 = float(prev_bar["high"]) >= inds.ema_21 * 0.998 or float(curr_bar["high"]) >= inds.ema_21 * 0.998
            closed_below_ema9 = curr_price < inds.ema_9
            momentum_ok = inds.rsi_14 <= 50.0 and inds.minus_di > inds.plus_di

            if high_touched_ema21 and closed_below_ema9 and momentum_ok:
                entry = curr_price
                stop = round(max(float(curr_bar["high"]), float(prev_bar["high"])) + (0.2 * atr), asset.price_decimals)
                risk = stop - entry
                if risk <= (entry * 0.001):
                    stop = round(entry + (1.0 * atr), asset.price_decimals)
                    risk = stop - entry

                target = round(entry - (3.25 * risk), asset.price_decimals)
                net_rr, net_risk, net_reward, costs = TransactionCostModel.compute_net_reward_to_risk(
                    asset_class=asset.asset_class,
                    side="SELL",
                    entry_price=entry,
                    stop_loss=stop,
                    take_profit=target,
                    qty=1.0,
                )
                if net_rr >= 3.0 and target > 0:
                    return TradeSignal(
                        strategy_name=self.name,
                        symbol=asset.symbol,
                        action="SELL",
                        entry_price=entry,
                        stop_loss=stop,
                        take_profit=target,
                        gross_rr=round((entry - target) / risk, 2),
                        net_rr=net_rr,
                        cost_breakdown=costs,
                        rationale=f"Bearish bounce into EMA21 ({inds.ema_21:.2f}) with downward rejection. ADX {inds.adx_14:.1f}.",
                        regime=regime.regime,
                        timestamp=curr_bar.get("datetime_local", "N/A"),
                    )

        return None


# ---------------------------------------------------------------- Strategy 2: ORB 3R Extended

class ORBStrategy(BaseStrategy):
    """15-Minute Opening Range Breakout with 3:1 Net Reward-to-Risk Target.
    
    Regimes: TREND_BULL, VOLATILE_EXPANSION
    Target: 3.25x OR risk distance to strictly ensure >= 3.0 net R:R after fees.
    """

    def __init__(self):
        super().__init__(
            name="ORB_3R",
            applicable_regimes=[MarketRegime.TREND_BULL, MarketRegime.VOLATILE_EXPANSION],
        )

    def evaluate(
        self,
        bars: List[Dict[str, Any]],
        regime: RegimeAnalysis,
        asset: AssetInfo,
    ) -> Optional[TradeSignal]:
        if len(bars) < 6:
            return None

        # Determine opening 3 bars (15-min range for 5-min bars)
        or_bars = bars[:3]
        or_high = max(float(b["high"]) for b in or_bars)
        or_low = min(float(b["low"]) for b in or_bars)
        or_mid = round((or_high + or_low) / 2.0, asset.price_decimals)

        curr_bar = bars[-1]
        curr_close = float(curr_bar["close"])

        # Breakout Trigger: Current candle closes above OR_high within 1.5% buffer
        if or_high < curr_close <= (or_high * 1.018):
            entry = curr_close
            stop = or_mid
            risk = entry - stop
            if risk <= (entry * 0.001):
                return None

            # Calculate 3:1 target with statutory friction buffer
            target = round(entry + (3.20 * risk), asset.price_decimals)

            net_rr, net_risk, net_reward, costs = TransactionCostModel.compute_net_reward_to_risk(
                asset_class=asset.asset_class,
                side="BUY",
                entry_price=entry,
                stop_loss=stop,
                take_profit=target,
                qty=1.0,
            )

            if net_rr >= 3.0:
                return TradeSignal(
                    strategy_name=self.name,
                    symbol=asset.symbol,
                    action="BUY",
                    entry_price=entry,
                    stop_loss=stop,
                    take_profit=target,
                    gross_rr=round((target - entry) / risk, 2),
                    net_rr=net_rr,
                    cost_breakdown=costs,
                    rationale=f"15-Minute Opening Range Breakout above {or_high:.2f}. Stop at OR mid ({stop:.2f}), target at 3.2R ({target:.2f}).",
                    regime=regime.regime,
                    timestamp=curr_bar.get("datetime_local", "N/A"),
                )

        return None


# ---------------------------------------------------------------- Strategy 3: Volatility Breakout ATR

class VolatilityBreakoutStrategy(BaseStrategy):
    """ATR Expansion Breakout beyond Bollinger Bands and Keltner Channels.
    
    Regimes: VOLATILE_EXPANSION
    Target: 3.5x ATR distance to ensure >= 3.0 net R:R after friction.
    """

    def __init__(self):
        super().__init__(
            name="VolatilityBreakout_ATR",
            applicable_regimes=[MarketRegime.VOLATILE_EXPANSION],
        )

    def evaluate(
        self,
        bars: List[Dict[str, Any]],
        regime: RegimeAnalysis,
        asset: AssetInfo,
    ) -> Optional[TradeSignal]:
        if regime.regime not in self.applicable_regimes or len(bars) < 20:
            return None

        inds = regime.indicators
        curr_bar = bars[-1]
        curr_price = float(curr_bar["close"])
        atr = inds.atr_14

        if atr <= 0.0001:
            return None

        # Breakout Trigger: Price closes outside the Upper Bollinger Band with RVOL > 1.2
        if curr_price > inds.bb_upper and inds.rvol >= 1.2:
            entry = curr_price
            # Stop loss at Bollinger Middle (20 SMA) or 1.0 ATR below entry
            stop = round(max(inds.bb_middle, entry - (1.1 * atr)), asset.price_decimals)
            risk = entry - stop
            if risk <= (entry * 0.001):
                return None

            target = round(entry + (3.30 * risk), asset.price_decimals)

            net_rr, net_risk, net_reward, costs = TransactionCostModel.compute_net_reward_to_risk(
                asset_class=asset.asset_class,
                side="BUY",
                entry_price=entry,
                stop_loss=stop,
                take_profit=target,
                qty=1.0,
            )

            if net_rr >= 3.0:
                return TradeSignal(
                    strategy_name=self.name,
                    symbol=asset.symbol,
                    action="BUY",
                    entry_price=entry,
                    stop_loss=stop,
                    take_profit=target,
                    gross_rr=round((target - entry) / risk, 2),
                    net_rr=net_rr,
                    cost_breakdown=costs,
                    rationale=f"Volatility expansion breakout above Upper Bollinger Band ({inds.bb_upper:.2f}) with RVOL {inds.rvol}x.",
                    regime=regime.regime,
                    timestamp=curr_bar.get("datetime_local", "N/A"),
                )

        return None


# ---------------------------------------------------------------- Strategy 4: Mean Reversion RSI

class MeanReversionStrategy(BaseStrategy):
    """Mean Reversion from Extreme Oversold Band at Support.
    
    Regimes: RANGE_BOUND
    Strictly enforces >= 3.0 net R:R across the range channel.
    """

    def __init__(self):
        super().__init__(
            name="MeanReversion_RSI",
            applicable_regimes=[MarketRegime.RANGE_BOUND],
        )

    def evaluate(
        self,
        bars: List[Dict[str, Any]],
        regime: RegimeAnalysis,
        asset: AssetInfo,
    ) -> Optional[TradeSignal]:
        if regime.regime not in self.applicable_regimes or len(bars) < 20:
            return None

        inds = regime.indicators
        curr_bar = bars[-1]
        curr_price = float(curr_bar["close"])
        atr = inds.atr_14

        # Oversold setup: RSI < 32 and price at or below Lower Bollinger Band
        if inds.rsi_14 < 32.0 and curr_price <= inds.bb_lower * 1.002:
            entry = curr_price
            # Tight stop below recent 5-bar swing low
            swing_low = min(float(b["low"]) for b in bars[-5:])
            stop = round(swing_low - (0.2 * atr), asset.price_decimals)
            risk = entry - stop
            if risk <= (entry * 0.001):
                return None

            # Target anchored at Upper Bollinger Band or 3.25R
            target = round(max(inds.bb_upper, entry + (3.25 * risk)), asset.price_decimals)

            net_rr, net_risk, net_reward, costs = TransactionCostModel.compute_net_reward_to_risk(
                asset_class=asset.asset_class,
                side="BUY",
                entry_price=entry,
                stop_loss=stop,
                take_profit=target,
                qty=1.0,
            )

            # In mean reversion, if the channel is too narrow to yield 3:1 net, REJECT
            if net_rr >= 3.0:
                return TradeSignal(
                    strategy_name=self.name,
                    symbol=asset.symbol,
                    action="BUY",
                    entry_price=entry,
                    stop_loss=stop,
                    take_profit=target,
                    gross_rr=round((target - entry) / risk, 2),
                    net_rr=net_rr,
                    cost_breakdown=costs,
                    rationale=f"Oversold bounce from Lower Bollinger Band ({inds.bb_lower:.2f}) with RSI {inds.rsi_14:.1f} in range-bound market.",
                    regime=regime.regime,
                    timestamp=curr_bar.get("datetime_local", "N/A"),
                )

        return None


# ---------------------------------------------------------------- Master Adaptive Strategy Engine

class AdaptiveStrategyEngine:
    """Orchestrates market regime detection and validated strategy selection.
    
    Guarantees that any generated signal strictly satisfies the 3:1 net R:R rule.
    If no strategy satisfies the criteria, cleanly returns NO TRADE.
    """

    def __init__(self):
        self.strategies: Dict[str, BaseStrategy] = {
            "TrendFollowing_EMA": TrendFollowingStrategy(),
            "ORB_3R": ORBStrategy(),
            "VolatilityBreakout_ATR": VolatilityBreakoutStrategy(),
            "MeanReversion_RSI": MeanReversionStrategy(),
        }

    def analyze_and_select(
        self,
        symbol: str,
        bars: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Runs end-to-end regime detection and strategy evaluation.
        
        Returns a comprehensive report dict containing:
          - symbol, asset_class, exchange
          - regime_analysis
          - selected_signal (or None)
          - verdict: "TRADE_APPROVED" or "NO_TRADE"
          - rejection_reason (if NO_TRADE)
        """
        asset = parse_symbol(symbol)
        regime = MarketRegimeDetector.analyze(bars, asset_class=asset.asset_class)

        output: Dict[str, Any] = {
            "symbol": asset.symbol,
            "asset_class": asset.asset_class,
            "exchange": asset.exchange,
            "currency": asset.quote_currency,
            "regime": regime.regime,
            "trend_strength": regime.trend_strength,
            "volatility_state": regime.volatility_state,
            "liquidity_status": regime.liquidity_status,
            "indicators": regime.indicators.__dict__,
            "eligible_strategies": regime.eligible_strategies,
            "verdict": "NO_TRADE",
            "signal": None,
            "rejection_reason": None,
        }

        # Gate 1: Tradable Regime Check
        if not regime.is_tradable:
            output["rejection_reason"] = f"Regime {regime.regime} is non-tradable ({regime.summary}). Preserving capital."
            return output

        # Gate 2: Liquidity Check
        if regime.liquidity_status == "ILLIQUID":
            output["rejection_reason"] = "Market is illiquid (RVOL too low); execution risks excessive slippage."
            return output

        # Gate 3: Strategy Evaluation Loop
        candidates: List[TradeSignal] = []
        for strat_name in regime.eligible_strategies:
            strategy = self.strategies.get(strat_name)
            if not strategy:
                continue
            signal = strategy.evaluate(bars, regime, asset)
            if signal:
                # Gate 4: Absolute Net 3:1 Reward-to-Risk Requirement
                if signal.net_rr >= 3.0:
                    candidates.append(signal)

        if not candidates:
            output["rejection_reason"] = (
                f"No strategy in regime {regime.regime} triggered a setup satisfying the minimum 3:1 net reward-to-risk requirement."
            )
            return output

        # Select highest net R:R candidate
        candidates.sort(key=lambda s: s.net_rr, reverse=True)
        best_signal = candidates[0]

        output["verdict"] = "TRADE_APPROVED"
        output["signal"] = {
            "strategy": best_signal.strategy_name,
            "action": best_signal.action,
            "entry_price": best_signal.entry_price,
            "stop_loss": best_signal.stop_loss,
            "take_profit": best_signal.take_profit,
            "gross_rr": best_signal.gross_rr,
            "net_rr": best_signal.net_rr,
            "cost_breakdown": best_signal.cost_breakdown.__dict__,
            "rationale": best_signal.rationale,
            "timestamp": best_signal.timestamp,
        }
        return output

    def analyze_and_generate_ticket(
        self,
        bars: List[Dict[str, Any]],
        symbol: str,
        market: str = "NSE",
        user_config: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Convenience method returning decision, regime, strategy, and sized ticket."""
        from copilot.risk_engine import RiskAndSizingEngine, UserRiskConfig

        cfg = user_config or UserRiskConfig(market=market)
        risk_engine = RiskAndSizingEngine(cfg)
        rep = self.analyze_and_select(symbol, bars)

        if rep.get("verdict") == "TRADE_APPROVED" and rep.get("signal"):
            sig = rep["signal"]
            ticket = risk_engine.calculate_position_size(
                symbol=symbol,
                entry_price=sig["entry_price"],
                stop_loss=sig["stop_loss"],
                take_profit=sig["take_profit"],
                side=sig["action"],
                planned_rr=sig["net_rr"],
                strategy=sig["strategy"],
            )
            if ticket:
                return {
                    "decision": "TRADE",
                    "selected_strategy": sig["strategy"],
                    "regime": {
                        "regime": rep["regime"],
                        "trend_strength": rep["trend_strength"],
                        "volatility_state": rep["volatility_state"],
                        "adx": rep["indicators"].get("adx_14", 0.0),
                        "confidence": 0.85,
                    },
                    "ticket": ticket.to_dict(),
                    "reasons": [],
                }
            else:
                return {
                    "decision": "NO_TRADE",
                    "selected_strategy": sig["strategy"],
                    "regime": {
                        "regime": rep["regime"],
                        "trend_strength": rep["trend_strength"],
                        "volatility_state": rep["volatility_state"],
                        "adx": rep["indicators"].get("adx_14", 0.0),
                        "confidence": 0.85,
                    },
                    "ticket": None,
                    "reasons": ["Risk and sizing engine rejected trade allocation (insufficient capital or parameter limit)."],
                }

        return {
            "decision": "NO_TRADE",
            "selected_strategy": None,
            "regime": {
                "regime": rep["regime"],
                "trend_strength": rep["trend_strength"],
                "volatility_state": rep["volatility_state"],
                "adx": rep["indicators"].get("adx_14", 0.0),
                "confidence": 0.0,
            },
            "ticket": None,
            "reasons": [rep.get("rejection_reason") or "No setup identified"],
        }
