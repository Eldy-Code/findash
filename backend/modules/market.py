"""
Market module — indices, sectors, top movers, economic events calendar.
Uses yfinance for all price data (free, no API key required).
"""
import yfinance as yf
import pandas as pd
from datetime import date, datetime, timedelta
from typing import Optional


# ── Major indices ──────────────────────────────────────────────────────────────
INDEX_TICKERS = {
    "S&P 500": "^GSPC",
    "Dow Jones": "^DJI",
    "NASDAQ": "^IXIC",
    "Russell 2000": "^RUT",
    "VIX": "^VIX",
}

# ── Sector ETFs ────────────────────────────────────────────────────────────────
SECTOR_ETFS = {
    "Technology": "XLK",
    "Financials": "XLF",
    "Healthcare": "XLV",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Utilities": "XLU",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Communication": "XLC",
}

# ── Large-cap universe for movers ─────────────────────────────────────────────
LARGE_CAP_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "BRK-B", "LLY", "AVGO",
    "JPM", "TSLA", "UNH", "XOM", "V", "MA", "COST", "JNJ", "HD", "PG",
    "ABBV", "MRK", "AMD", "CVX", "BAC", "KO", "ORCL", "WMT", "MCD", "CRM",
    "NFLX", "TMO", "IBM", "GS", "CAT", "NOW", "AMAT", "INTC", "AMT", "RTX",
    "T", "VZ", "DIS", "PFE", "BA", "GE", "SBUX", "NEE", "PM", "BMY",
    "QCOM", "HON", "UPS", "MDT", "C", "SPGI", "AXP", "BLK", "DE", "CI",
]

# ── 2026 FOMC meeting dates ────────────────────────────────────────────────────
FOMC_DATES_2026 = [
    "2026-01-28", "2026-03-18", "2026-05-06",
    "2026-06-17", "2026-07-29", "2026-09-16",
    "2026-11-04", "2026-12-16",
]

# ── 2026 CPI release dates (approximate BLS schedule) ─────────────────────────
CPI_DATES_2026 = [
    "2026-01-14", "2026-02-11", "2026-03-11", "2026-04-10",
    "2026-05-13", "2026-06-10", "2026-07-15", "2026-08-12",
    "2026-09-11", "2026-10-14", "2026-11-12", "2026-12-11",
]

# ── 2026 Jobs Report (NFP) dates (first Friday of month) ──────────────────────
NFP_DATES_2026 = [
    "2026-01-09", "2026-02-06", "2026-03-06", "2026-04-03",
    "2026-05-08", "2026-06-05", "2026-07-10", "2026-08-07",
    "2026-09-04", "2026-10-02", "2026-11-06", "2026-12-04",
]


def _pct_change(info: dict) -> Optional[float]:
    prev = info.get("previousClose") or info.get("regularMarketPreviousClose")
    curr = info.get("currentPrice") or info.get("regularMarketPrice")
    if prev and curr:
        return round((curr - prev) / prev * 100, 2)
    return None


def _safe_ticker_info(ticker_obj) -> dict:
    try:
        hist = ticker_obj.history(period="2d")
        if hist.empty or len(hist) < 1:
            return {}
        close = hist["Close"].iloc[-1]
        prev = hist["Close"].iloc[-2] if len(hist) >= 2 else close
        chg = close - prev
        chg_pct = (chg / prev * 100) if prev else 0
        return {
            "price": round(float(close), 2),
            "change": round(float(chg), 2),
            "change_pct": round(float(chg_pct), 2),
            "volume": int(hist["Volume"].iloc[-1]) if "Volume" in hist.columns else None,
        }
    except Exception:
        return {}


def get_market_summary() -> dict:
    """Return major index prices and percentage changes for today."""
    results = {}
    tickers = yf.Tickers(" ".join(INDEX_TICKERS.values()))
    for name, sym in INDEX_TICKERS.items():
        t = tickers.tickers.get(sym) or yf.Ticker(sym)
        info = _safe_ticker_info(t)
        results[name] = {"symbol": sym, **info}
    return results


def get_sector_performance() -> list[dict]:
    """Return today's performance for each S&P sector ETF."""
    results = []
    tickers = yf.Tickers(" ".join(SECTOR_ETFS.values()))
    for sector, sym in SECTOR_ETFS.items():
        t = tickers.tickers.get(sym) or yf.Ticker(sym)
        info = _safe_ticker_info(t)
        results.append({"sector": sector, "symbol": sym, **info})
    # Sort by change_pct descending
    results.sort(key=lambda x: x.get("change_pct") or 0, reverse=True)
    return results


def get_top_movers(top_n: int = 10) -> dict:
    """Return top gainers and losers from the large-cap universe."""
    try:
        raw = yf.download(
            " ".join(LARGE_CAP_UNIVERSE),
            period="2d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        close = raw["Close"]
        if close.shape[0] < 2:
            return {"gainers": [], "losers": []}

        prev = close.iloc[-2]
        curr = close.iloc[-1]
        chg_pct = ((curr - prev) / prev * 100).dropna().sort_values(ascending=False)

        def row(sym, pct):
            return {
                "symbol": sym,
                "change_pct": round(float(pct), 2),
                "price": round(float(curr[sym]), 2) if sym in curr else None,
            }

        gainers = [row(s, p) for s, p in chg_pct.head(top_n).items()]
        losers = [row(s, p) for s, p in chg_pct.tail(top_n).items()][::-1]
        return {"gainers": gainers, "losers": losers}
    except Exception as e:
        return {"gainers": [], "losers": [], "error": str(e)}


def get_earnings_calendar(symbols: list[str]) -> list[dict]:
    """Get upcoming earnings dates for the given symbols via yfinance."""
    events = []
    for sym in symbols:
        try:
            t = yf.Ticker(sym)
            cal = t.calendar
            if cal is None:
                continue
            # calendar can be a dict with 'Earnings Date' as a list or Timestamp
            earnings_date = None
            if isinstance(cal, dict):
                ed = cal.get("Earnings Date")
                if ed:
                    earnings_date = ed[0] if isinstance(ed, list) else ed
            if earnings_date:
                events.append({
                    "type": "Earnings",
                    "symbol": sym,
                    "date": str(earnings_date)[:10],
                    "label": f"{sym} Earnings",
                })
        except Exception:
            continue
    return events


def get_economic_events(days_ahead: int = 30) -> list[dict]:
    """Return upcoming macro events within the next N days."""
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)
    events = []

    def add_events(dates, event_type, label):
        for d_str in dates:
            d = date.fromisoformat(d_str)
            if today <= d <= cutoff:
                events.append({"type": event_type, "date": d_str, "label": label, "symbol": None})

    add_events(FOMC_DATES_2026, "FOMC", "Fed Interest Rate Decision")
    add_events(CPI_DATES_2026, "CPI", "CPI Inflation Report")
    add_events(NFP_DATES_2026, "NFP", "Jobs Report (NFP)")

    events.sort(key=lambda e: e["date"])
    return events
