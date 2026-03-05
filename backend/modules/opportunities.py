"""
Opportunities module — Polymarket prediction markets + unusual options flow.

Polymarket: Public API, no key required.
Options flow: yfinance options chain — flags unusual volume vs. open interest.
"""
import requests
import yfinance as yf
import pandas as pd
from datetime import datetime, timezone, date, timedelta
from typing import Optional


# ── Polymarket ─────────────────────────────────────────────────────────────────

POLYMARKET_API = "https://gamma-api.polymarket.com/markets"

FINANCE_KEYWORDS = [
    "stock", "market", "fed", "rate", "inflation", "recession", "gdp",
    "earnings", "nasdaq", "dow", "s&p", "bitcoin", "crypto", "oil",
    "dollar", "treasury", "yield", "ipo", "merger", "acquisition",
    "tariff", "trade", "economy", "economic", "fiscal", "monetary",
    "interest rate", "cpi", "nfp", "jobs", "unemployment",
]


def _is_finance_related(question: str) -> bool:
    q = question.lower()
    return any(kw in q for kw in FINANCE_KEYWORDS)


def get_polymarket_opportunities(limit: int = 50) -> list[dict]:
    """
    Fetch active Polymarket prediction markets and filter for finance-related ones.
    Returns markets sorted by volume, with implied odds.
    """
    try:
        resp = requests.get(
            POLYMARKET_API,
            params={
                "limit": limit,
                "active": True,
                "closed": False,
                "order": "volume",
                "ascending": False,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            return []

        markets = resp.json()
        results = []
        for m in markets:
            question = m.get("question") or m.get("title") or ""
            if not _is_finance_related(question):
                continue

            # Extract outcomes and probabilities
            outcomes = m.get("outcomes", [])
            outcome_prices = m.get("outcomePrices", [])

            parsed_outcomes = []
            if isinstance(outcomes, list) and isinstance(outcome_prices, list):
                for i, outcome in enumerate(outcomes):
                    price = float(outcome_prices[i]) if i < len(outcome_prices) else None
                    if price is not None:
                        parsed_outcomes.append({
                            "name": outcome,
                            "probability": round(price * 100, 1),
                        })
            elif isinstance(outcomes, str):
                # Sometimes serialized as JSON string
                import json
                try:
                    outcomes = json.loads(outcomes)
                    prices = json.loads(outcome_prices) if isinstance(outcome_prices, str) else []
                    for i, o in enumerate(outcomes):
                        p = float(prices[i]) if i < len(prices) else None
                        if p is not None:
                            parsed_outcomes.append({"name": o, "probability": round(p * 100, 1)})
                except Exception:
                    pass

            volume = m.get("volume") or m.get("volume24hr") or 0
            try:
                volume = float(volume)
            except (TypeError, ValueError):
                volume = 0

            end_date = m.get("endDate") or m.get("endDateIso")
            results.append({
                "id": m.get("id") or m.get("conditionId", ""),
                "question": question,
                "outcomes": parsed_outcomes,
                "volume_usd": round(volume, 0),
                "end_date": str(end_date)[:10] if end_date else None,
                "url": f"https://polymarket.com/event/{m.get('slug', m.get('id', ''))}",
                "liquidity": m.get("liquidity"),
            })

        results.sort(key=lambda r: r["volume_usd"], reverse=True)
        return results[:20]

    except Exception as e:
        return [{"error": str(e), "question": "Failed to load Polymarket data"}]


# ── Unusual Options Flow ───────────────────────────────────────────────────────

def _score_option_unusualness(row) -> float:
    """
    Score how unusual an option contract is.
    High score = high volume relative to OI, with decent premium.
    """
    volume = row.get("volume") or 0
    oi = row.get("openInterest") or 1
    last_price = row.get("lastPrice") or 0

    vol_oi_ratio = volume / oi if oi > 0 else 0
    score = 0

    if vol_oi_ratio > 5:
        score += 3
    elif vol_oi_ratio > 2:
        score += 2
    elif vol_oi_ratio > 1:
        score += 1

    if last_price > 1:
        score += 1
    if volume > 1000:
        score += 1
    if volume > 5000:
        score += 1

    return score


def get_unusual_options_flow(symbols: list[str], top_n: int = 20) -> list[dict]:
    """
    Scan options chains for given symbols and surface unusual activity:
    - Volume significantly exceeds open interest
    - Near-term expirations (0-45 DTE)
    - Meaningful premium (last price > $0.50)

    Returns a list of flagged contracts sorted by unusualness score.
    """
    unusual = []
    today = date.today()
    cutoff = today + timedelta(days=45)

    for sym in symbols:
        try:
            ticker = yf.Ticker(sym)
            expirations = ticker.options
            if not expirations:
                continue

            current_price = None
            try:
                hist = ticker.history(period="1d")
                if not hist.empty:
                    current_price = float(hist["Close"].iloc[-1])
            except Exception:
                pass

            for exp_str in expirations:
                exp_date = date.fromisoformat(exp_str)
                if exp_date > cutoff:
                    continue  # Only look at near-term

                dte = (exp_date - today).days

                try:
                    chain = ticker.option_chain(exp_str)
                except Exception:
                    continue

                for contract_type, contracts in [("call", chain.calls), ("put", chain.puts)]:
                    if contracts.empty:
                        continue

                    for _, row in contracts.iterrows():
                        volume = row.get("volume") or 0
                        oi = row.get("openInterest") or 0
                        last_price = row.get("lastPrice") or 0
                        strike = row.get("strike") or 0

                        # Filter: meaningful volume, not pennies
                        if volume < 100 or last_price < 0.10:
                            continue

                        score = _score_option_unusualness({
                            "volume": volume,
                            "openInterest": oi,
                            "lastPrice": last_price,
                        })

                        if score < 2:
                            continue

                        # Calculate implied move
                        pct_from_current = None
                        if current_price and strike:
                            pct_from_current = round((strike - current_price) / current_price * 100, 1)

                        implied_vol = row.get("impliedVolatility")
                        premium_total = round(float(volume) * float(last_price) * 100, 0)

                        unusual.append({
                            "symbol": sym,
                            "type": contract_type.upper(),
                            "expiration": exp_str,
                            "dte": dte,
                            "strike": round(float(strike), 2),
                            "last_price": round(float(last_price), 2),
                            "volume": int(volume),
                            "open_interest": int(oi),
                            "vol_oi_ratio": round(float(volume) / max(oi, 1), 1),
                            "implied_vol": round(float(implied_vol) * 100, 1) if implied_vol else None,
                            "pct_from_current": pct_from_current,
                            "premium_total": premium_total,
                            "score": score,
                            "note": (
                                f"{'CALL' if contract_type == 'call' else 'PUT'} "
                                f"${strike} exp {exp_str} ({dte}d) — "
                                f"vol {int(volume):,} vs OI {int(oi):,} "
                                f"({volume/max(oi,1):.1f}x)"
                            ),
                        })

        except Exception:
            continue

    unusual.sort(key=lambda x: x["score"], reverse=True)
    return unusual[:top_n]
