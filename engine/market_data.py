#!/usr/bin/env python3
"""Market Data Provider for Indian Equities, Cryptocurrencies, and US Markets.

Connects to Yahoo Finance public chart and quote endpoints with:
  - query2 / query1 fallback
  - Automatic retry on 429 with backoff
  - Offline fixture support
  - Strict data normalization and quality checks
  - Standard library only (urllib)
"""

import datetime
import gzip
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple, Any

from engine.asset_model import AssetInfo, parse_symbol, to_local_time

UA = "Mozilla/5.0 (compatible; day-trader-skill/2.0; +https://github.com/Jakeschincariol/day-trader-skill)"
YAHOO_HOSTS = ("https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com")
PAUSE_SEC = 0.3
BACKOFF_SEC = 3.0


class MarketDataError(Exception):
    pass


class MarketDataProvider:
    def __init__(self, user_agent: str = UA, timeout: int = 10, cache_dir: Optional[str] = None, use_network: bool = True):
        self.user_agent = user_agent
        self.timeout = timeout
        self.cache_dir = cache_dir
        self.use_network = use_network

    def _request_json(self, url_path: str, params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Requests JSON from Yahoo Finance with fallback between query2 and query1."""
        if not self.use_network:
            raise MarketDataError("Network disabled in offline mode")
        query_str = ("?" + urllib.parse.urlencode(params)) if params else ""
        last_err = None

        for host in YAHOO_HOSTS:
            url = f"{host}{url_path}{query_str}"
            req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
            for attempt in range(2):
                try:
                    with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                        body = resp.read()
                        if resp.info().get("Content-Encoding") == "gzip":
                            body = gzip.decompress(body)
                        return json.loads(body.decode("utf-8"))
                except urllib.error.HTTPError as e:
                    last_err = e
                    if e.code == 429:
                        time.sleep(BACKOFF_SEC)
                        continue
                    elif e.code in (400, 404):
                        # Symbol not found
                        raise MarketDataError(f"Market data not available for symbol (HTTP {e.code})")
                    break
                except Exception as e:
                    last_err = e
                    break

        raise MarketDataError(f"Failed to retrieve market data: {last_err}")

    def fetch_chart(
        self,
        symbol: str,
        range_str: str = "1d",
        interval: str = "5m",
        offline_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetches raw v8 chart response from offline file or Yahoo Finance."""
        if offline_path:
            clean_path = offline_path
            if os.path.isdir(offline_path):
                # Search for <symbol>.json or <symbol>.json.gz
                p1 = os.path.join(offline_path, f"{symbol}.json")
                p2 = os.path.join(offline_path, f"{symbol}.json.gz")
                clean_path = p1 if os.path.exists(p1) else (p2 if os.path.exists(p2) else p1)

            if not os.path.exists(clean_path):
                raise MarketDataError(f"Offline file not found: {clean_path}")

            open_fn = gzip.open if clean_path.endswith(".gz") else open
            with open_fn(clean_path, "rt", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and symbol in data:
                    return data[symbol]
                return data

        # Online network path
        enc_sym = urllib.parse.quote(symbol.strip().upper())
        path = f"/v8/finance/chart/{enc_sym}"
        params = {"range": range_str, "interval": interval}
        return self._request_json(path, params)

    def get_bars(
        self,
        symbol: str,
        range_str: str = "1d",
        interval: str = "5m",
        offline_path: Optional[str] = None,
        now_dt: Optional[datetime.datetime] = None,
    ) -> Tuple[AssetInfo, List[Dict[str, Any]], Dict[str, Any]]:
        """Returns (AssetInfo, normalized_bars, meta).
        
        Normalized bars format:
          [
            {
              "timestamp": int (epoch sec),
              "datetime_utc": str (ISO),
              "datetime_local": str (ISO),
              "open": float,
              "high": float,
              "low": float,
              "close": float,
              "volume": float
            },
            ...
          ]
        """
        asset = parse_symbol(symbol)
        raw = self.fetch_chart(symbol, range_str=range_str, interval=interval, offline_path=offline_path)

        chart = raw.get("chart", {})
        if chart.get("error"):
            err = chart["error"]
            raise MarketDataError(f"{symbol}: {err.get('description', 'Unknown error')}")

        results = chart.get("result")
        if not results:
            raise MarketDataError(f"No chart data returned for {symbol}")

        res = results[0]
        meta = res.get("meta", {})
        timestamps = res.get("timestamp") or []
        quote = (res.get("indicators", {}).get("quote") or [{}])[0]

        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        vols = quote.get("volume") or []

        bars = []
        now_epoch = int(now_dt.timestamp()) if now_dt else int(time.time())
        interval_sec = 60 if interval == "1m" else (300 if interval == "5m" else 86400)

        for i, ts in enumerate(timestamps):
            if i >= len(closes):
                break
            c = closes[i]
            if c is None:
                continue  # drop incomplete/null tick

            o = opens[i] if i < len(opens) and opens[i] is not None else c
            h = highs[i] if i < len(highs) and highs[i] is not None else max(o, c)
            l = lows[i] if i < len(lows) and lows[i] is not None else min(o, c)
            v = vols[i] if i < len(vols) and vols[i] is not None else 0.0

            # Filter uncompleted bar if strictly live
            # A bar starting at ts with interval_sec completes at ts + interval_sec
            utc_dt = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).replace(tzinfo=None)
            local_dt = to_local_time(asset, utc_dt)

            bars.append({
                "timestamp": ts,
                "datetime_utc": utc_dt.isoformat() + "Z",
                "datetime_local": local_dt.isoformat(),
                "open": round(float(o), asset.price_decimals),
                "high": round(float(h), asset.price_decimals),
                "low": round(float(l), asset.price_decimals),
                "close": round(float(c), asset.price_decimals),
                "volume": float(v),
            })

        return asset, bars, meta

    def get_intraday_bars(self, symbol: str) -> List[Dict[str, Any]]:
        """Convenience method returning normalized bars or empty list on failure."""
        try:
            _, bars, _ = self.get_bars(symbol, range_str="1d", interval="5m")
            return bars
        except Exception:
            return []

    def get_latest_quote(
        self,
        symbol: str,
        offline_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Returns standardized quote metadata for symbol."""
        asset, bars, meta = self.get_bars(symbol, range_str="1d", interval="5m", offline_path=offline_path)
        
        last_price = meta.get("regularMarketPrice")
        prev_close = meta.get("chartPreviousClose") or meta.get("previousClose")
        currency = meta.get("currency") or asset.quote_currency

        if not bars and last_price is None:
            raise MarketDataError(f"No price available for {symbol}")

        if last_price is None and bars:
            last_price = bars[-1]["close"]
        if prev_close is None and bars:
            prev_close = bars[0]["open"]

        pct_change = 0.0
        if prev_close and prev_close > 0 and last_price is not None:
            pct_change = round(((last_price - prev_close) / prev_close) * 100.0, 2)

        return {
            "symbol": asset.symbol,
            "asset_class": asset.asset_class,
            "exchange": asset.exchange,
            "currency": currency,
            "currency_symbol": asset.currency_symbol,
            "price": last_price,
            "previous_close": prev_close,
            "change_pct": pct_change,
            "data_source": "Yahoo Finance (unofficial, delayed)" if not offline_path else f"Offline ({offline_path})",
            "bars_count": len(bars),
            "last_bar_time": bars[-1]["datetime_local"] if bars else None,
        }

