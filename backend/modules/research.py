"""
Research module — per-symbol deep dive using yfinance.
Produces technicals, news, analyst data, and a rule-based recommendation.
"""
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from typing import Optional


def _safe_get(info: dict, *keys, default=None):
    for k in keys:
        v = info.get(k)
        if v is not None and v != "":
            return v
    return default


def _compute_rsi(closes: pd.Series, period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    delta = closes.diff().dropna()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    return round(float(val), 1) if not np.isnan(val) else None


def _compute_macd(closes: pd.Series) -> Optional[dict]:
    if len(closes) < 35:
        return None
    ema12 = closes.ewm(span=12, adjust=False).mean()
    ema26 = closes.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    histogram = macd - signal
    return {
        "macd": round(float(macd.iloc[-1]), 4),
        "signal": round(float(signal.iloc[-1]), 4),
        "histogram": round(float(histogram.iloc[-1]), 4),
        "bullish": float(macd.iloc[-1]) > float(signal.iloc[-1]),
    }


def _compute_moving_averages(closes: pd.Series) -> dict:
    result = {}
    for period in (20, 50, 200):
        if len(closes) >= period:
            ma = closes.rolling(period).mean().iloc[-1]
            result[f"ma{period}"] = round(float(ma), 2)
    current = closes.iloc[-1]
    result["above_ma20"] = current > result.get("ma20", 0)
    result["above_ma50"] = current > result.get("ma50", 0)
    result["above_ma200"] = current > result.get("ma200", 0)
    return result


def _rule_based_recommendation(
    rsi: Optional[float],
    macd: Optional[dict],
    ma: dict,
    analyst_rating: Optional[str],
    analyst_target: Optional[float],
    current_price: Optional[float],
    gain_loss_pct: Optional[float],
) -> dict:
    """
    Generate a recommendation based on technical + analyst signals.
    Returns action (BUY / SELL / HOLD / WATCH / REDUCE) and rationale bullets.
    """
    bullish_signals = 0
    bearish_signals = 0
    rationale = []

    # RSI
    if rsi is not None:
        if rsi < 30:
            bullish_signals += 2
            rationale.append(f"RSI {rsi} — oversold, potential bounce opportunity")
        elif rsi > 70:
            bearish_signals += 2
            rationale.append(f"RSI {rsi} — overbought, momentum may be fading")
        else:
            rationale.append(f"RSI {rsi} — neutral momentum")

    # MACD
    if macd:
        if macd["bullish"] and macd["histogram"] > 0:
            bullish_signals += 1
            rationale.append("MACD above signal line — bullish crossover")
        elif not macd["bullish"] and macd["histogram"] < 0:
            bearish_signals += 1
            rationale.append("MACD below signal line — bearish crossover")

    # Moving averages
    if ma.get("above_ma50") and ma.get("above_ma200"):
        bullish_signals += 1
        rationale.append("Trading above 50-day and 200-day MA — uptrend intact")
    elif not ma.get("above_ma50") and not ma.get("above_ma200"):
        bearish_signals += 1
        rationale.append("Trading below 50-day and 200-day MA — downtrend")
    elif ma.get("above_ma50") and not ma.get("above_ma200"):
        rationale.append("Above 50-day MA but below 200-day — mixed trend")

    # Analyst target
    if analyst_target and current_price and current_price > 0:
        upside = (analyst_target - current_price) / current_price * 100
        if upside > 15:
            bullish_signals += 1
            rationale.append(f"Analyst target ${analyst_target:.2f} (+{upside:.1f}% upside)")
        elif upside < -5:
            bearish_signals += 1
            rationale.append(f"Analyst target ${analyst_target:.2f} ({upside:.1f}% downside)")

    # Analyst rating
    if analyst_rating:
        rating_lower = analyst_rating.lower()
        if any(w in rating_lower for w in ("strong buy", "outperform", "overweight")):
            bullish_signals += 1
        elif any(w in rating_lower for w in ("sell", "underperform", "underweight")):
            bearish_signals += 1

    # Risk management overlay
    if gain_loss_pct and gain_loss_pct >= 50:
        bearish_signals += 1
        rationale.append(f"Position up {gain_loss_pct:.1f}% — elevated risk, consider taking profits")

    # Determine action
    net = bullish_signals - bearish_signals
    if net >= 3:
        action = "BUY"
    elif net >= 1:
        action = "WATCH"
    elif net == 0:
        action = "HOLD"
    elif net == -1:
        action = "REDUCE"
    else:
        action = "SELL"

    return {
        "action": action,
        "bullish_signals": bullish_signals,
        "bearish_signals": bearish_signals,
        "rationale": rationale,
    }


def research_symbol(symbol: str, gain_loss_pct: Optional[float] = None) -> dict:
    """
    Full research report for a symbol.
    Returns key stats, technicals, news, analyst data, and a recommendation.
    """
    ticker = yf.Ticker(symbol)

    # ── Key stats ──────────────────────────────────────────────────────────────
    try:
        info = ticker.info or {}
    except Exception:
        info = {}

    current_price = _safe_get(info, "currentPrice", "regularMarketPrice", "navPrice")
    market_cap = info.get("marketCap")
    pe_ratio = _safe_get(info, "trailingPE", "forwardPE")
    beta = info.get("beta")
    dividend_yield = info.get("dividendYield")
    analyst_target = info.get("targetMeanPrice")
    analyst_rating = _safe_get(info, "recommendationKey", "averageAnalystRating")
    fifty_two_week_high = info.get("fiftyTwoWeekHigh")
    fifty_two_week_low = info.get("fiftyTwoWeekLow")
    short_ratio = info.get("shortRatio")
    sector = info.get("sector")
    industry = info.get("industry")

    key_stats = {
        "current_price": current_price,
        "market_cap": market_cap,
        "pe_ratio": round(pe_ratio, 2) if pe_ratio else None,
        "beta": round(beta, 2) if beta else None,
        "dividend_yield": round(dividend_yield * 100, 2) if dividend_yield else None,
        "analyst_target": analyst_target,
        "analyst_rating": analyst_rating,
        "52w_high": fifty_two_week_high,
        "52w_low": fifty_two_week_low,
        "short_ratio": short_ratio,
        "sector": sector,
        "industry": industry,
    }

    # ── Technical analysis ─────────────────────────────────────────────────────
    technicals = {}
    try:
        hist = ticker.history(period="1y", auto_adjust=True)
        if not hist.empty:
            closes = hist["Close"]
            rsi = _compute_rsi(closes)
            macd = _compute_macd(closes)
            ma = _compute_moving_averages(closes)
            technicals = {"rsi": rsi, "macd": macd, **ma}
    except Exception:
        rsi, macd, ma = None, None, {}

    # ── News ───────────────────────────────────────────────────────────────────
    news_items = []
    try:
        raw_news = ticker.news or []
        for item in raw_news[:8]:
            news_items.append({
                "title": item.get("title"),
                "publisher": item.get("publisher"),
                "link": item.get("link"),
                "published_at": datetime.fromtimestamp(
                    item.get("providerPublishTime", 0), tz=timezone.utc
                ).strftime("%Y-%m-%d %H:%M"),
            })
    except Exception:
        pass

    # ── Recommendation ─────────────────────────────────────────────────────────
    rsi_val = technicals.get("rsi")
    macd_val = technicals.get("macd")
    recommendation = _rule_based_recommendation(
        rsi=rsi_val,
        macd=macd_val,
        ma={k: technicals.get(k) for k in ("above_ma20", "above_ma50", "above_ma200")},
        analyst_rating=analyst_rating,
        analyst_target=analyst_target,
        current_price=current_price,
        gain_loss_pct=gain_loss_pct,
    )

    return {
        "symbol": symbol,
        "name": info.get("longName") or info.get("shortName") or symbol,
        "key_stats": key_stats,
        "technicals": technicals,
        "news": news_items,
        "recommendation": recommendation,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
