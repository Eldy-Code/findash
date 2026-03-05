"""
Market module — indices, sectors, top movers, economic events calendar.
Uses _prices.py multi-source fetcher (yfinance → Finnhub → Alpha Vantage).
"""
from datetime import date, timedelta
from typing import Optional

from backend.modules._prices import get_quotes


# ── Symbol maps ────────────────────────────────────────────────────────────────

INDEX_TICKERS = {
    "S&P 500":     "^GSPC",
    "Dow Jones":   "^DJI",
    "NASDAQ":      "^IXIC",
    "Russell 2000": "^RUT",
    "VIX":         "^VIX",
}

SECTOR_ETFS = {
    "Technology":             "XLK",
    "Financials":             "XLF",
    "Healthcare":             "XLV",
    "Energy":                 "XLE",
    "Industrials":            "XLI",
    "Consumer Discretionary": "XLY",
    "Consumer Staples":       "XLP",
    "Utilities":              "XLU",
    "Materials":              "XLB",
    "Real Estate":            "XLRE",
    "Communication":          "XLC",
}

LARGE_CAP_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "BRK-B", "LLY", "AVGO",
    "JPM", "TSLA", "UNH", "XOM", "V",   "MA",   "COST", "JNJ",  "HD",  "PG",
    "ABBV", "MRK", "AMD", "CVX", "BAC", "KO",   "ORCL", "WMT",  "MCD", "CRM",
    "NFLX", "TMO", "IBM", "GS",  "CAT", "NOW",  "AMAT", "INTC", "AMT", "RTX",
    "T",    "VZ",  "DIS", "PFE", "BA",  "GE",   "SBUX", "NEE",  "PM",  "BMY",
    "QCOM", "HON", "UPS", "MDT", "C",   "SPGI", "AXP",  "BLK",  "DE",  "CI",
]

# ── 2026 Calendar dates ────────────────────────────────────────────────────────

FOMC_DATES_2026 = [
    "2026-01-28", "2026-03-18", "2026-05-06",
    "2026-06-17", "2026-07-29", "2026-09-16",
    "2026-11-04", "2026-12-16",
]
CPI_DATES_2026 = [
    "2026-01-14", "2026-02-11", "2026-03-11", "2026-04-10",
    "2026-05-13", "2026-06-10", "2026-07-15", "2026-08-12",
    "2026-09-11", "2026-10-14", "2026-11-12", "2026-12-11",
]
NFP_DATES_2026 = [
    "2026-01-09", "2026-02-06", "2026-03-06", "2026-04-03",
    "2026-05-08", "2026-06-05", "2026-07-10", "2026-08-07",
    "2026-09-04", "2026-10-02", "2026-11-06", "2026-12-04",
]


# ── Public functions ───────────────────────────────────────────────────────────

def get_market_summary() -> dict:
    """Return major index prices and percentage changes for today."""
    syms = list(INDEX_TICKERS.values())
    quotes = get_quotes(syms)
    result = {}
    for name, sym in INDEX_TICKERS.items():
        q = quotes.get(sym, {})
        result[name] = {
            "symbol":     sym,
            "price":      q.get("price"),
            "change":     q.get("change"),
            "change_pct": q.get("change_pct"),
        }
    return result


def get_sector_performance() -> list[dict]:
    """Return today's performance for each S&P sector ETF."""
    syms = list(SECTOR_ETFS.values())
    quotes = get_quotes(syms)
    results = []
    for sector, sym in SECTOR_ETFS.items():
        q = quotes.get(sym, {})
        results.append({
            "sector":     sector,
            "symbol":     sym,
            "price":      q.get("price"),
            "change":     q.get("change"),
            "change_pct": q.get("change_pct"),
        })
    results.sort(key=lambda x: x.get("change_pct") or 0, reverse=True)
    return results


def get_top_movers(top_n: int = 10) -> dict:
    """Return top gainers and losers from the large-cap universe."""
    try:
        quotes = get_quotes(LARGE_CAP_UNIVERSE)
        if not quotes:
            return {"gainers": [], "losers": [], "error": "No price data available"}

        ranked = sorted(
            [
                {"symbol": sym, "price": q["price"], "change_pct": q["change_pct"]}
                for sym, q in quotes.items()
                if q.get("price") and q.get("change_pct") is not None
            ],
            key=lambda x: x["change_pct"],
            reverse=True,
        )
        return {
            "gainers": ranked[:top_n],
            "losers":  list(reversed(ranked[-top_n:])),
        }
    except Exception as e:
        return {"gainers": [], "losers": [], "error": str(e)}


def get_earnings_calendar(symbols: list[str]) -> list[dict]:
    """Get upcoming earnings dates for the given symbols via yfinance."""
    events = []
    try:
        import yfinance as yf
    except ImportError:
        return events

    for sym in symbols:
        try:
            t = yf.Ticker(sym)
            cal = t.calendar
            if cal is None:
                continue
            earnings_date = None
            if isinstance(cal, dict):
                ed = cal.get("Earnings Date")
                if ed:
                    earnings_date = ed[0] if isinstance(ed, list) else ed
            if earnings_date:
                events.append({
                    "type":   "Earnings",
                    "symbol": sym,
                    "date":   str(earnings_date)[:10],
                    "label":  f"{sym} Earnings",
                })
        except Exception:
            continue
    return events


def get_economic_events(days_ahead: int = 30) -> list[dict]:
    """Return upcoming macro events within the next N days."""
    today  = date.today()
    cutoff = today + timedelta(days=days_ahead)
    events = []

    def add_events(dates, event_type, label):
        for d_str in dates:
            d = date.fromisoformat(d_str)
            if today <= d <= cutoff:
                events.append({"type": event_type, "date": d_str, "label": label, "symbol": None})

    add_events(FOMC_DATES_2026, "FOMC", "Fed Interest Rate Decision")
    add_events(CPI_DATES_2026,  "CPI",  "CPI Inflation Report")
    add_events(NFP_DATES_2026,  "NFP",  "Jobs Report (NFP)")

    events.sort(key=lambda e: e["date"])
    return events
