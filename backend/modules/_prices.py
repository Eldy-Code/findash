"""
Multi-source price fetcher.

Priority order:
  1. yfinance (free, no key, works on most local machines)
  2. Finnhub   (free API key — set FINNHUB_API_KEY in .env)
  3. Alpha Vantage (free API key — set ALPHAVANTAGE_API_KEY in .env)

Each source returns a dict of:
  symbol -> { price, prev_close, change, change_pct }
"""
import os
import time
import logging
from typing import Optional

import requests

logger = logging.getLogger("findash.prices")

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/html,*/*",
})

PriceDict = dict  # { price, prev_close, change, change_pct }


# ── Public API ─────────────────────────────────────────────────────────────────

def get_quotes(symbols: list[str]) -> dict[str, PriceDict]:
    """
    Fetch current quotes for a list of symbols.
    Tries yfinance first, then Finnhub, then Alpha Vantage for any missing.
    Returns a dict keyed by symbol (uppercase).
    """
    symbols = [s.upper() for s in symbols if s]
    if not symbols:
        return {}

    result: dict[str, PriceDict] = {}

    # --- Source 1: yfinance ---
    try:
        result.update(_yfinance_quotes(symbols))
    except Exception as e:
        logger.warning(f"yfinance failed: {e}")

    missing = [s for s in symbols if s not in result]

    # --- Source 2: Finnhub ---
    finnhub_key = os.getenv("FINNHUB_API_KEY", "").strip()
    if missing and finnhub_key:
        try:
            result.update(_finnhub_quotes(missing, finnhub_key))
        except Exception as e:
            logger.warning(f"Finnhub failed: {e}")
        missing = [s for s in symbols if s not in result]

    # --- Source 3: Alpha Vantage ---
    av_key = os.getenv("ALPHAVANTAGE_API_KEY", "").strip()
    if missing and av_key:
        try:
            result.update(_alphavantage_quotes(missing, av_key))
        except Exception as e:
            logger.warning(f"Alpha Vantage failed: {e}")

    return result


def get_history(symbol: str, period: str = "1y") -> dict:
    """
    Return OHLCV history for charting.
    period: 1d 5d 1mo 3mo 6mo 1y 2y 5y
    Returns { dates, opens, highs, lows, closes, volumes }
    """
    try:
        import yfinance as yf
        hist = yf.Ticker(symbol.upper()).history(period=period, auto_adjust=True)
        if not hist.empty:
            return {
                "dates":   hist.index.strftime("%Y-%m-%d").tolist(),
                "closes":  [round(float(c), 2) for c in hist["Close"]],
                "opens":   [round(float(c), 2) for c in hist["Open"]],
                "highs":   [round(float(c), 2) for c in hist["High"]],
                "lows":    [round(float(c), 2) for c in hist["Low"]],
                "volumes": [int(v) for v in hist["Volume"]],
            }
    except Exception as e:
        logger.warning(f"History via yfinance failed for {symbol}: {e}")

    return {"dates": [], "closes": [], "opens": [], "highs": [], "lows": [], "volumes": []}


# ── Source implementations ─────────────────────────────────────────────────────

def _yfinance_quotes(symbols: list[str]) -> dict[str, PriceDict]:
    import yfinance as yf

    result = {}

    if len(symbols) == 1:
        sym = symbols[0]
        hist = yf.Ticker(sym).history(period="2d", auto_adjust=True)
        if not hist.empty and len(hist) >= 1:
            price = float(hist["Close"].iloc[-1])
            prev  = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else price
            result[sym] = _make_quote(price, prev)
        return result

    # Multi-ticker download
    raw = yf.download(
        symbols, period="2d", auto_adjust=True,
        progress=False, group_by="ticker",
    )

    for sym in symbols:
        try:
            # yfinance 1.x: MultiIndex columns -> raw[sym]["Close"]
            # yfinance 0.2.x: same structure with group_by="ticker"
            if sym in raw.columns.get_level_values(0):
                col = raw[sym]["Close"].dropna()
            elif "Close" in raw.columns:
                # Single-symbol fallback (shouldn't happen with multi)
                col = raw["Close"].dropna()
            else:
                continue

            if len(col) >= 1:
                price = float(col.iloc[-1])
                prev  = float(col.iloc[-2]) if len(col) >= 2 else price
                result[sym] = _make_quote(price, prev)
        except Exception:
            # Fall back to individual fetch
            try:
                hist = yf.Ticker(sym).history(period="2d", auto_adjust=True)
                if not hist.empty:
                    price = float(hist["Close"].iloc[-1])
                    prev  = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else price
                    result[sym] = _make_quote(price, prev)
            except Exception:
                pass

    return result


def _finnhub_quotes(symbols: list[str], api_key: str) -> dict[str, PriceDict]:
    """
    Finnhub GET /quote — free tier: 60 calls/min.
    Returns { c: current, d: change, dp: change%, pc: prev_close }
    """
    result = {}
    base = "https://finnhub.io/api/v1/quote"
    for sym in symbols:
        try:
            r = _SESSION.get(base, params={"symbol": sym, "token": api_key}, timeout=8)
            if r.status_code != 200:
                continue
            d = r.json()
            price = d.get("c") or d.get("l")  # current or last
            prev  = d.get("pc")
            if price:
                result[sym] = _make_quote(float(price), float(prev) if prev else float(price))
            time.sleep(0.05)  # gentle rate-limit
        except Exception as e:
            logger.debug(f"Finnhub {sym}: {e}")
    return result


def _alphavantage_quotes(symbols: list[str], api_key: str) -> dict[str, PriceDict]:
    """
    Alpha Vantage GLOBAL_QUOTE — free tier: 25 calls/day, 5/min.
    """
    result = {}
    base = "https://www.alphavantage.co/query"
    for sym in symbols:
        try:
            r = _SESSION.get(
                base,
                params={"function": "GLOBAL_QUOTE", "symbol": sym, "apikey": api_key},
                timeout=10,
            )
            if r.status_code != 200:
                continue
            q = r.json().get("Global Quote", {})
            price = q.get("05. price")
            prev  = q.get("08. previous close")
            if price:
                result[sym] = _make_quote(float(price), float(prev) if prev else float(price))
            time.sleep(0.25)  # 5 req/min limit
        except Exception as e:
            logger.debug(f"AlphaVantage {sym}: {e}")
    return result


def _make_quote(price: float, prev_close: float) -> PriceDict:
    change     = round(price - prev_close, 4)
    change_pct = round((price - prev_close) / prev_close * 100, 2) if prev_close else 0.0
    return {
        "price":      round(price, 4),
        "prev_close": round(prev_close, 4),
        "change":     change,
        "change_pct": change_pct,
    }
