#!/usr/bin/env python3
"""Market Transaction Cost & Slippage Engine.

Provides accurate, statutory fee models for:
  - Indian Equities (NSE): Brokerage, STT, exchange turnover, GST, SEBI charges, stamp duty, slippage.
  - Cryptocurrencies: Exchange maker/taker fees, bid-ask spread, slippage.
  - US Equities: Regulatory fees (SEC, FINRA TAF), slippage.

Enforces calculation of net profit, net risk, and net reward-to-risk ratios.
"""

from dataclasses import dataclass
from typing import Dict, Any, Optional, Tuple


@dataclass
class CostBreakdown:
    entry_brokerage: float
    exit_brokerage: float
    stt_tax: float
    exchange_charges: float
    gst_tax: float
    sebi_charges: float
    stamp_duty: float
    estimated_slippage: float
    total_roundtrip_cost: float
    cost_per_unit: float


class MarketAssetType:
    EQUITY_NSE = "equity_in"
    INDEX_NSE = "index"
    CRYPTO = "crypto"
    EQUITY_US = "equity_us"


class NetRRResult(tuple):
    def __new__(cls, net_rr, net_risk, net_reward, costs):
        return super().__new__(cls, (net_rr, net_risk, net_reward, costs))

    @property
    def net_rr(self):
        return self[0]

    @property
    def net_risk(self):
        return self[1]

    @property
    def net_reward(self):
        return self[2]

    @property
    def costs(self):
        return self[3]

    def __getitem__(self, item):
        if isinstance(item, str):
            if item in ("net_rr", "rr"):
                return self[0]
            if item == "net_risk":
                return self[1]
            if item == "net_reward":
                return self[2]
            if item in ("costs", "cost_breakdown"):
                return self[3]
        return super().__getitem__(item)

    def __contains__(self, item):
        if item in ("net_rr", "net_risk", "net_reward", "costs"):
            return True
        return super().__contains__(item)

    def get(self, key, default=None):
        try:
            return self[key]
        except Exception:
            return default


class TransactionCostModel:
    """Calculates realistic transaction costs, taxes, and slippage."""

    @staticmethod
    def detect_market_type(symbol: str, market: str = "NSE") -> str:
        sym = (symbol or "").upper()
        mkt = (market or "").upper()
        if sym.endswith(".NS") or sym.endswith(".BO") or mkt in ("NSE", "BSE", "INDIA"):
            return MarketAssetType.EQUITY_NSE
        if "-USD" in sym or "/USD" in sym or "USDT" in sym or mkt in ("CRYPTO", "BINANCE"):
            return MarketAssetType.CRYPTO
        return MarketAssetType.EQUITY_US

    @staticmethod
    def calculate_slippage(price: float, market_type: str = MarketAssetType.EQUITY_NSE) -> float:
        if market_type == MarketAssetType.CRYPTO:
            return price * 0.0005
        elif market_type == MarketAssetType.EQUITY_NSE:
            return price * 0.0004
        else:
            return price * 0.0003

    @classmethod
    def calculate_total_friction(
        cls,
        market: str,
        entry_price: float,
        exit_price: float,
        quantity: float,
    ) -> Dict[str, Any]:
        m_type = market if market in (MarketAssetType.EQUITY_NSE, MarketAssetType.CRYPTO, MarketAssetType.EQUITY_US, MarketAssetType.INDEX_NSE) else cls.detect_market_type(market)
        cb = cls.get_costs(m_type, quantity, entry_price, exit_price)
        return {
            "total_friction": cb.total_roundtrip_cost,
            "entry_fees": cb.entry_brokerage + cb.stamp_duty,
            "exit_fees": cb.exit_brokerage + cb.stt_tax + cb.exchange_charges + cb.gst_tax + cb.sebi_charges,
            "entry_slippage": cb.estimated_slippage / 2.0,
            "exit_slippage": cb.estimated_slippage / 2.0,
            "breakdown": {
                "brokerage": cb.entry_brokerage + cb.exit_brokerage,
                "stt": cb.stt_tax,
                "turnover_fee": cb.exchange_charges,
                "gst": cb.gst_tax,
                "sebi": cb.sebi_charges,
                "stamp_duty": cb.stamp_duty,
                "slippage": cb.estimated_slippage,
            },
        }

    @staticmethod
    def calculate_nse_equity_costs(
        shares: float,
        entry_price: float,
        exit_price: float,
        slippage_pct: float = 0.0004,  # 0.04% standard large-cap slippage
    ) -> CostBreakdown:
        """Calculates exact statutory charges for Indian NSE intraday equities.
        
        Statutory rates (NSE Intraday):
          - Brokerage: min(₹20, 0.03% of turnover) per leg
          - STT (Securities Transaction Tax): 0.025% on sell side only
          - Exchange Turnover Charge (NSE): 0.00297% on both legs
          - GST: 18% on (Brokerage + Exchange Turnover Charge)
          - SEBI Turnover Fee: ₹10 per crore (0.0001% of turnover)
          - Stamp Duty: 0.003% on buy side only
          - Slippage: Configurable (default 0.04% round trip)
        """
        turnover_buy = float(shares) * float(entry_price)
        turnover_sell = float(shares) * float(exit_price)
        total_turnover = turnover_buy + turnover_sell

        # Brokerage (Discount broker standard: ₹20 or 0.03%, whichever is lower per order)
        brokerage_buy = min(20.0, turnover_buy * 0.0003)
        brokerage_sell = min(20.0, turnover_sell * 0.0003)
        total_brokerage = brokerage_buy + brokerage_sell

        # STT (Intraday equity: 0.025% on sell leg)
        stt = turnover_sell * 0.00025

        # Exchange turnover charge (0.00297% on both legs)
        exchange_charges = total_turnover * 0.0000297

        # GST (18% on brokerage + exchange charges)
        gst = (total_brokerage + exchange_charges) * 0.18

        # SEBI charges (0.0001%)
        sebi = total_turnover * 0.000001

        # Stamp duty (0.003% on buy side)
        stamp_duty = turnover_buy * 0.00003

        # Slippage (estimated price impact)
        slippage = total_turnover * (slippage_pct / 2.0)

        total_cost = (
            total_brokerage
            + stt
            + exchange_charges
            + gst
            + sebi
            + stamp_duty
            + slippage
        )
        cost_per_unit = total_cost / max(float(shares), 1e-9)

        return CostBreakdown(
            entry_brokerage=round(brokerage_buy, 2),
            exit_brokerage=round(brokerage_sell, 2),
            stt_tax=round(stt, 2),
            exchange_charges=round(exchange_charges, 2),
            gst_tax=round(gst, 2),
            sebi_charges=round(sebi, 2),
            stamp_duty=round(stamp_duty, 2),
            estimated_slippage=round(slippage, 2),
            total_roundtrip_cost=round(total_cost, 2),
            cost_per_unit=round(cost_per_unit, 4),
        )

    @staticmethod
    def calculate_crypto_costs(
        units: float,
        entry_price: float,
        exit_price: float,
        taker_fee_pct: float = 0.001,   # 0.10% standard spot taker fee
        slippage_pct: float = 0.0005,   # 0.05% slippage/spread buffer
    ) -> CostBreakdown:
        """Calculates exchange fees and spread/slippage for Crypto spot trading."""
        turnover_buy = float(units) * float(entry_price)
        turnover_sell = float(units) * float(exit_price)
        total_turnover = turnover_buy + turnover_sell

        fee_buy = turnover_buy * taker_fee_pct
        fee_sell = turnover_sell * taker_fee_pct
        slippage = total_turnover * slippage_pct

        total_cost = fee_buy + fee_sell + slippage
        cost_per_unit = total_cost / max(float(units), 1e-9)

        return CostBreakdown(
            entry_brokerage=round(fee_buy, 4),
            exit_brokerage=round(fee_sell, 4),
            stt_tax=0.0,
            exchange_charges=0.0,
            gst_tax=0.0,
            sebi_charges=0.0,
            stamp_duty=0.0,
            estimated_slippage=round(slippage, 4),
            total_roundtrip_cost=round(total_cost, 4),
            cost_per_unit=round(cost_per_unit, 6),
        )

    @staticmethod
    def calculate_us_equity_costs(
        shares: float,
        entry_price: float,
        exit_price: float,
        slippage_pct: float = 0.0003,
    ) -> CostBreakdown:
        """Calculates US equity regulatory charges and slippage."""
        turnover_buy = float(shares) * float(entry_price)
        turnover_sell = float(shares) * float(exit_price)
        total_turnover = turnover_buy + turnover_sell

        # SEC Section 31 fee (on sell only: ~$27.80 per million)
        sec_fee = turnover_sell * 0.0000278
        # FINRA TAF ($0.000166 per share on sell, max $8.30)
        finra_taf = min(8.30, float(shares) * 0.000166)
        slippage = total_turnover * (slippage_pct / 2.0)

        total_cost = sec_fee + finra_taf + slippage
        cost_per_unit = total_cost / max(float(shares), 1e-9)

        return CostBreakdown(
            entry_brokerage=0.0,
            exit_brokerage=0.0,
            stt_tax=0.0,
            exchange_charges=round(sec_fee + finra_taf, 4),
            gst_tax=0.0,
            sebi_charges=0.0,
            stamp_duty=0.0,
            estimated_slippage=round(slippage, 4),
            total_roundtrip_cost=round(total_cost, 4),
            cost_per_unit=round(cost_per_unit, 6),
        )

    @classmethod
    def get_costs(
        cls,
        asset_class: str,
        qty: float,
        entry_price: float,
        exit_price: float,
    ) -> CostBreakdown:
        """Dispatches cost calculation by asset class."""
        if asset_class in ("equity_in", "index"):
            return cls.calculate_nse_equity_costs(qty, entry_price, exit_price)
        elif asset_class == "crypto":
            return cls.calculate_crypto_costs(qty, entry_price, exit_price)
        else:
            return cls.calculate_us_equity_costs(qty, entry_price, exit_price)

    @classmethod
    def compute_net_reward_to_risk(
        cls,
        asset_class: Optional[str] = None,
        side: str = "BUY",
        entry_price: float = 0.0,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        qty: float = 1.0,
        market: Optional[str] = None,
        quantity: Optional[float] = None,
    ) -> NetRRResult:
        """Calculates net risk, net reward, and net reward-to-risk ratio after all costs.
        
        Returns:
            NetRRResult(net_rr, net_risk, net_reward, cost_breakdown)
        """
        ac = asset_class or market or MarketAssetType.EQUITY_NSE
        effective_qty = quantity if quantity is not None else qty
        is_long = (side or "BUY").upper() == "BUY"
        if is_long:
            gross_risk_per_unit = float(entry_price) - float(stop_loss)
            gross_reward_per_unit = float(take_profit) - float(entry_price)
        else:
            gross_risk_per_unit = float(stop_loss) - float(entry_price)
            gross_reward_per_unit = float(entry_price) - float(take_profit)

        if gross_risk_per_unit <= 0 or gross_reward_per_unit <= 0:
            costs = cls.get_costs(ac, effective_qty, entry_price, stop_loss)
            return NetRRResult(0.0, 0.0, 0.0, costs)

        # Estimate roundtrip costs at target exit and stop exit
        cost_at_target = cls.get_costs(ac, effective_qty, entry_price, take_profit)
        cost_at_stop = cls.get_costs(ac, effective_qty, entry_price, stop_loss)

        total_gross_risk = gross_risk_per_unit * float(effective_qty)
        total_gross_reward = gross_reward_per_unit * float(effective_qty)

        # Net Risk = Gross loss + all costs paid when hitting stop
        net_risk = total_gross_risk + cost_at_stop.total_roundtrip_cost

        # Net Reward = Gross profit - all costs paid when hitting target
        net_reward = total_gross_reward - cost_at_target.total_roundtrip_cost

        net_rr = net_reward / max(net_risk, 1e-9)
        return NetRRResult(round(net_rr, 2), round(net_risk, 2), round(net_reward, 2), cost_at_target)
