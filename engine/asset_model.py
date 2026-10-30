#!/usr/bin/env python3
"""Unified Asset Model, Market Calendars, and Symbol Normalization.

Supports:
  - Indian Equities (NSE / BSE), e.g. RELIANCE.NS, TCS.NS, INFY.NS, ^NSEI
  - Cryptocurrencies, e.g. BTC-USD, ETH-USD, SOL-USD, BTC-INR
  - US Equities (NYSE / NASDAQ), e.g. AAPL, TSLA, BRK.B
  - Standardized datetime & timezone conversions without third-party dependencies.
"""

import datetime
import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Any

# ---------------------------------------------------------------- Symbol Regexes

RE_US_EQUITY = re.compile(r"^[A-Z]{1,5}([.\-][A-Z]{1,2})?$")
RE_NSE_EQUITY = re.compile(r"^[A-Z0-9_\-]{1,20}\.(NS|BO)$", re.IGNORECASE)
RE_CRYPTO = re.compile(r"^[A-Z0-9]{2,10}[-\/](USD|USDT|USDC|INR|EUR|GBP|BTC|ETH)$", re.IGNORECASE)
RE_INDEX = re.compile(r"^\^[A-Z0-9]{2,10}$", re.IGNORECASE)

# Universal ticker regex that validates any of the supported markets
RE_ANY_TICKER = re.compile(
    r"^(?:[A-Z]{1,5}([.\-][A-Z]{1,2})?|[A-Z0-9_\-]{1,20}\.(?:NS|BO)|[A-Z0-9]{2,10}[-\/](?:USD|USDT|USDC|INR|EUR|GBP|BTC|ETH)|\^[A-Z0-9]{2,10})$",
    re.IGNORECASE,
)

# ---------------------------------------------------------------- Market Calendars

NYSE_HOLIDAYS: Dict[str, str] = {
    "2026-01-01": "New Year's Day",
    "2026-01-19": "Martin Luther King, Jr. Day",
    "2026-02-16": "Washington's Birthday",
    "2026-04-03": "Good Friday",
    "2026-05-25": "Memorial Day",
    "2026-06-19": "Juneteenth National Independence Day",
    "2026-07-03": "Independence Day (observed)",
    "2026-09-07": "Labor Day",
    "2026-11-26": "Thanksgiving Day",
    "2026-12-25": "Christmas Day",
    "2027-01-01": "New Year's Day",
    "2027-01-18": "Martin Luther King, Jr. Day",
    "2027-02-15": "Washington's Birthday",
    "2027-03-26": "Good Friday",
    "2027-05-31": "Memorial Day",
    "2027-06-18": "Juneteenth National Independence Day (observed)",
    "2027-07-05": "Independence Day (observed)",
    "2027-09-06": "Labor Day",
    "2027-11-25": "Thanksgiving Day",
    "2027-12-24": "Christmas Day (observed)",
}

NYSE_EARLY_CLOSE: Dict[str, str] = {
    "2026-11-27": "day after Thanksgiving",
    "2026-12-24": "Christmas Eve",
    "2027-11-26": "day after Thanksgiving",
}

NSE_HOLIDAYS: Dict[str, str] = {
    "2026-01-26": "Republic Day",
    "2026-02-17": "Mahashivratri",
    "2026-03-03": "Holi",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id / Eid-ul-Adha",
    "2026-06-26": "Muharram",
    "2026-08-15": "Independence Day",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-08": "Diwali Laxmi Pujan",
    "2026-11-10": "Diwali Balipratipada",
    "2026-11-24": "Gurunanak Jayanti",
    "2026-12-25": "Christmas",
    "2027-01-26": "Republic Day",
    "2027-03-22": "Holi",
    "2027-03-26": "Good Friday",
    "2027-04-14": "Dr. Ambedkar Jayanti",
    "2027-05-01": "Maharashtra Day",
    "2027-08-15": "Independence Day",
    "2027-10-02": "Mahatma Gandhi Jayanti",
    "2027-10-29": "Diwali",
    "2027-12-25": "Christmas",
}

# ---------------------------------------------------------------- Time Helpers

def _nth_sunday(year: int, month: int, n: int) -> datetime.date:
    d = datetime.date(year, month, 1)
    d += datetime.timedelta(days=(6 - d.weekday()) % 7)
    return d + datetime.timedelta(weeks=n - 1)


def eastern_offset(utc_dt: datetime.datetime) -> int:
    """Hours from UTC for US Eastern: -4 during EDT, -5 during EST."""
    y = utc_dt.year
    start = datetime.datetime.combine(_nth_sunday(y, 3, 2), datetime.time(7, 0))  # 2:00 EST
    end = datetime.datetime.combine(_nth_sunday(y, 11, 1), datetime.time(6, 0))   # 2:00 EDT
    naive = utc_dt.replace(tzinfo=None)
    return -4 if start <= naive < end else -5


IST_OFFSET = datetime.timedelta(hours=5, minutes=30)
TZ_IST = datetime.timezone(IST_OFFSET, name="IST")
TZ_UTC = datetime.timezone.utc


# ---------------------------------------------------------------- Unified Asset Model

class AssetClass:
    EQUITY_IN = "equity_in"
    CRYPTO = "crypto"
    EQUITY_US = "equity_us"
    INDEX = "index"


@dataclass
class AssetInfo:
    symbol: str
    asset_class: str          # "equity_in", "crypto", "equity_us", "index"
    exchange: str             # "NSE", "CRYPTO", "NYSE", "NASDAQ", "INDEX"
    base_currency: str        # e.g. "RELIANCE", "BTC", "AAPL"
    quote_currency: str       # "INR", "USD", etc.
    currency_symbol: str      # "₹", "$", etc.
    is_continuous_24_7: bool  # True for Crypto
    session_open: Optional[datetime.time]
    session_close: Optional[datetime.time]
    session_bars_5m: int      # 75 for NSE, 78 for US, 288 for 24h
    allow_fractional: bool
    price_decimals: int
    timezone_name: str        # "Asia/Kolkata", "America/New_York", "UTC"
    default_orb_minutes: int  # 15 mins default

    @property
    def raw_symbol(self) -> str:
        return self.symbol

    @property
    def timezone(self) -> str:
        return self.timezone_name

    @property
    def bars_per_day(self) -> int:
        return self.session_bars_5m

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "asset_class": self.asset_class,
            "exchange": self.exchange,
            "base_currency": self.base_currency,
            "quote_currency": self.quote_currency,
            "currency_symbol": self.currency_symbol,
            "is_continuous_24_7": self.is_continuous_24_7,
            "session_open": self.session_open.strftime("%H:%M") if self.session_open else None,
            "session_close": self.session_close.strftime("%H:%M") if self.session_close else None,
            "session_bars_5m": self.session_bars_5m,
            "allow_fractional": self.allow_fractional,
            "price_decimals": self.price_decimals,
            "timezone_name": self.timezone_name,
            "default_orb_minutes": self.default_orb_minutes,
        }


def parse_symbol(symbol: str) -> AssetInfo:
    """Classifies and normalizes any ticker into a unified AssetInfo object."""
    clean = symbol.strip().upper()
    
    # 1. Indian NSE/BSE Equity
    if clean.endswith(".NS") or clean.endswith(".BO"):
        base = clean.split(".")[0]
        exch = "NSE" if clean.endswith(".NS") else "BSE"
        return AssetInfo(
            symbol=clean,
            asset_class="equity_in",
            exchange=exch,
            base_currency=base,
            quote_currency="INR",
            currency_symbol="₹",
            is_continuous_24_7=False,
            session_open=datetime.time(9, 15),
            session_close=datetime.time(15, 30),
            session_bars_5m=75,
            allow_fractional=False,
            price_decimals=2,
            timezone_name="Asia/Kolkata",
            default_orb_minutes=15,
        )

    # 2. Cryptocurrency Pair
    norm_crypto = clean.replace("/", "-")
    if RE_CRYPTO.match(norm_crypto):
        parts = norm_crypto.split("-")
        base = parts[0]
        quote = parts[1]
        c_sym = "₹" if quote == "INR" else ("$" if quote in ("USD", "USDT", "USDC") else quote)
        return AssetInfo(
            symbol=norm_crypto,
            asset_class="crypto",
            exchange="CRYPTO",
            base_currency=base,
            quote_currency=quote,
            currency_symbol=c_sym,
            is_continuous_24_7=True,
            session_open=None,
            session_close=None,
            session_bars_5m=288,  # 24h of 5m bars
            allow_fractional=True,
            price_decimals=4 if quote in ("USD", "USDT", "INR") else 8,
            timezone_name="UTC",
            default_orb_minutes=15,
        )

    # 3. Market Index
    if clean.startswith("^"):
        if clean in ("^NSEI", "^NSEBANK", "^CNXIT", "^BSESN"):
            return AssetInfo(
                symbol=clean,
                asset_class="index",
                exchange="NSE",
                base_currency=clean[1:],
                quote_currency="INR",
                currency_symbol="₹",
                is_continuous_24_7=False,
                session_open=datetime.time(9, 15),
                session_close=datetime.time(15, 30),
                session_bars_5m=75,
                allow_fractional=False,
                price_decimals=2,
                timezone_name="Asia/Kolkata",
                default_orb_minutes=15,
            )
        else:
            return AssetInfo(
                symbol=clean,
                asset_class="index",
                exchange="US",
                base_currency=clean[1:],
                quote_currency="USD",
                currency_symbol="$",
                is_continuous_24_7=False,
                session_open=datetime.time(9, 30),
                session_close=datetime.time(16, 0),
                session_bars_5m=78,
                allow_fractional=False,
                price_decimals=2,
                timezone_name="America/New_York",
                default_orb_minutes=15,
            )

    # 4. Default: US Stock / ETF
    return AssetInfo(
        symbol=clean,
        asset_class="equity_us",
        exchange="US",
        base_currency=clean,
        quote_currency="USD",
        currency_symbol="$",
        is_continuous_24_7=False,
        session_open=datetime.time(9, 30),
        session_close=datetime.time(16, 0),
        session_bars_5m=78,
        allow_fractional=False,
        price_decimals=2,
        timezone_name="America/New_York",
        default_orb_minutes=15,
    )


# ---------------------------------------------------------------- Market Calendar Checks

def to_local_time(asset: AssetInfo, dt_utc: datetime.datetime) -> datetime.datetime:
    """Converts a UTC datetime (naive or aware) to the asset's local trading timezone (naive)."""
    if dt_utc.tzinfo is not None:
        utc = dt_utc.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    else:
        utc = dt_utc

    if asset.timezone_name == "Asia/Kolkata":
        return utc + IST_OFFSET
    elif asset.timezone_name == "America/New_York":
        off = eastern_offset(utc)
        return utc + datetime.timedelta(hours=off)
    else:
        # UTC for Crypto
        return utc


def to_utc(asset: AssetInfo, dt_local: datetime.datetime) -> datetime.datetime:
    """Converts an asset's local naive datetime to naive UTC."""
    if asset.timezone_name == "Asia/Kolkata":
        return dt_local - IST_OFFSET
    elif asset.timezone_name == "America/New_York":
        for off in (-4, -5):
            candidate = dt_local - datetime.timedelta(hours=off)
            if eastern_offset(candidate) == off:
                return candidate
        return dt_local + datetime.timedelta(hours=5)
    else:
        return dt_local


def get_market_status(asset: AssetInfo, dt_utc: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Returns the market open/closed status, session times, and reasons for an asset at a given UTC time."""
    now_utc = dt_utc or datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    local_dt = to_local_time(asset, now_utc)
    date_str = local_dt.date().isoformat()
    t = local_dt.time()

    # 1. Crypto is always open
    if asset.is_continuous_24_7:
        return {
            "symbol": asset.symbol,
            "market": asset.exchange,
            "is_open": True,
            "reason": "continuous 24/7 trading",
            "local_time": local_dt.isoformat(),
            "timezone": asset.timezone_name,
            "today": date_str,
        }

    # 2. Indian NSE
    if asset.asset_class in ("equity_in", "index") and asset.exchange == "NSE":
        weekday = local_dt.weekday()
        if weekday >= 5:
            return {
                "symbol": asset.symbol,
                "market": "NSE",
                "is_open": False,
                "reason": "weekend (%s)" % local_dt.strftime("%A"),
                "local_time": local_dt.isoformat(),
                "timezone": "Asia/Kolkata",
                "today": date_str,
            }
        if date_str in NSE_HOLIDAYS:
            return {
                "symbol": asset.symbol,
                "market": "NSE",
                "is_open": False,
                "reason": "NSE holiday: %s" % NSE_HOLIDAYS[date_str],
                "local_time": local_dt.isoformat(),
                "timezone": "Asia/Kolkata",
                "today": date_str,
            }
        
        open_time = asset.session_open or datetime.time(9, 15)
        close_time = asset.session_close or datetime.time(15, 30)
        
        if t < open_time:
            return {
                "symbol": asset.symbol,
                "market": "NSE",
                "is_open": False,
                "reason": "pre-market (opens at %s IST)" % open_time.strftime("%H:%M"),
                "local_time": local_dt.isoformat(),
                "timezone": "Asia/Kolkata",
                "today": date_str,
            }
        elif t >= close_time:
            return {
                "symbol": asset.symbol,
                "market": "NSE",
                "is_open": False,
                "reason": "market closed (closed at %s IST)" % close_time.strftime("%H:%M"),
                "local_time": local_dt.isoformat(),
                "timezone": "Asia/Kolkata",
                "today": date_str,
            }
        else:
            return {
                "symbol": asset.symbol,
                "market": "NSE",
                "is_open": True,
                "reason": "regular trading session (09:15 - 15:30 IST)",
                "local_time": local_dt.isoformat(),
                "timezone": "Asia/Kolkata",
                "today": date_str,
            }

    # 3. US Market (NYSE / NASDAQ)
    weekday = local_dt.weekday()
    if weekday >= 5:
        return {
            "symbol": asset.symbol,
            "market": "US",
            "is_open": False,
            "reason": "weekend (%s)" % local_dt.strftime("%A"),
            "local_time": local_dt.isoformat(),
            "timezone": "America/New_York",
            "today": date_str,
        }
    if date_str in NYSE_HOLIDAYS:
        return {
            "symbol": asset.symbol,
            "market": "US",
            "is_open": False,
            "reason": "NYSE holiday: %s" % NYSE_HOLIDAYS[date_str],
            "local_time": local_dt.isoformat(),
            "timezone": "America/New_York",
            "today": date_str,
        }
    
    close_time = datetime.time(13, 0) if date_str in NYSE_EARLY_CLOSE else (asset.session_close or datetime.time(16, 0))
    open_time = asset.session_open or datetime.time(9, 30)

    if t < open_time:
        return {
            "symbol": asset.symbol,
            "market": "US",
            "is_open": False,
            "reason": "pre-market (opens at %s ET)" % open_time.strftime("%H:%M"),
            "local_time": local_dt.isoformat(),
            "timezone": "America/New_York",
            "today": date_str,
        }
    elif t >= close_time:
        return {
            "symbol": asset.symbol,
            "market": "US",
            "is_open": False,
            "reason": "market closed (closed at %s ET)" % close_time.strftime("%H:%M"),
            "local_time": local_dt.isoformat(),
            "timezone": "America/New_York",
            "today": date_str,
        }
    else:
        return {
            "symbol": asset.symbol,
            "market": "US",
            "is_open": True,
            "reason": "regular trading session (%s - %s ET)" % (open_time.strftime("%H:%M"), close_time.strftime("%H:%M")),
            "local_time": local_dt.isoformat(),
            "timezone": "America/New_York",
            "today": date_str,
        }


def to_yahoo_ticker(sym: str) -> str:
    """Normalizes a symbol for Yahoo Finance query URLs.
    Preserves .NS and .BO suffixes for Indian equities; transforms BRK.B -> BRK-B for US equities.
    """
    s = sym.strip().upper()
    if s.endswith(".NS") or s.endswith(".BO") or s.startswith("^"):
        return s
    return s.replace(".", "-")


def get_market_session(asset_class: str) -> Dict[str, Any]:
    """Returns typical session boundaries for an asset class."""
    if asset_class in (AssetClass.EQUITY_IN, "equity_in"):
        return {
            "open": (9, 15),
            "close": (15, 30),
            "bars_per_day": 75,
            "timezone": "Asia/Kolkata",
            "continuous": False,
        }
    elif asset_class in (AssetClass.CRYPTO, "crypto"):
        return {
            "open": (0, 0),
            "close": (23, 59),
            "bars_per_day": 288,
            "timezone": "UTC",
            "continuous": True,
        }
    else:
        return {
            "open": (9, 30),
            "close": (16, 0),
            "bars_per_day": 78,
            "timezone": "America/New_York",
            "continuous": False,
        }


def is_market_open(symbol: str, dt_utc: Optional[datetime.datetime] = None) -> Tuple[bool, str]:
    """Returns (is_open, reason) for a given symbol at dt_utc."""
    asset = parse_symbol(symbol)
    status = get_market_status(asset, dt_utc)
    return status["is_open"], status["reason"]


def format_currency(amount: float, currency_or_symbol: str = "$", decimals: int = 2, use_symbol: bool = False) -> str:
    """Formats an amount with its currency code or symbol cleanly."""
    if amount is None:
        return "N/A"
    abs_amt = abs(amount)
    sign = "-" if amount < 0 else ""
    cur = currency_or_symbol
    if use_symbol:
        if cur in ("INR", "\u20b9"):
            sym = "\u20b9"
        elif cur in ("USD", "$"):
            sym = "$"
        else:
            sym = cur + " "
    else:
        if cur == "INR":
            sym = "INR "
        elif cur == "USD":
            sym = "USD "
        elif cur.startswith("$") or cur.startswith("\u20b9"):
            sym = cur
        else:
            sym = cur + " " if len(cur) > 1 and not cur.endswith(" ") else cur

    if decimals == 0:
        return "%s%s%d" % (sign, sym, int(round(abs_amt)))
    fmt = "%s%s{:,.%df}" % (sign, sym, decimals)
    return fmt.format(abs_amt)

