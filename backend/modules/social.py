"""
Social module — Reddit sentiment scanning + Google Trends.
Identifies social arbitrage plays: tickers with rising social interest
but minimal price movement.

Reddit: Uses PRAW if credentials are set, otherwise falls back to
        Reddit's public JSON API (no key required, rate-limited).
Trends: Uses pytrends (no key required).
"""
import os
import re
import time
import requests
import yfinance as yf
from datetime import datetime, timezone
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "FinDash/1.0")

SOCIAL_SUBREDDITS = [
    "wallstreetbets",
    "stocks",
    "investing",
    "options",
    "StockMarket",
]

# Regex to find ticker symbols (1-5 uppercase letters, not common English words)
TICKER_PATTERN = re.compile(r"\b([A-Z]{1,5})\b")
COMMON_WORDS = {
    "A", "I", "THE", "AND", "OR", "BUT", "IN", "ON", "AT", "TO", "BE",
    "IS", "IT", "HE", "SHE", "WE", "US", "MY", "BY", "DO", "GO", "UP",
    "AI", "FD", "DD", "ATH", "CEO", "IPO", "ETF", "WSB", "IMO", "FWIW",
    "TBH", "OP", "PS", "VS", "NGL", "FOR", "NOT", "ALL", "NEW", "BUY",
    "SELL", "HOLD", "PUT", "CALL", "PM", "DM", "IV", "OTM", "ITM", "ATM",
    "YOY", "QOQ", "MOM", "EPS", "PE", "PEG", "EV", "EBITDA", "GAAP",
    "SEC", "IRS", "FED", "GDP", "CPI", "NFP", "VIX", "SPX", "SPY",
    "IF", "OF", "AS", "AN", "AM", "ARE", "WAS", "WERE", "HAS", "HAD",
}


def _extract_tickers(text: str) -> list[str]:
    found = TICKER_PATTERN.findall(text or "")
    return [t for t in found if t not in COMMON_WORDS and len(t) >= 2]


# ── Reddit via public JSON API (no credentials needed) ────────────────────────

def _reddit_public_search(subreddit: str, query: str = "", sort: str = "hot", limit: int = 25) -> list[dict]:
    """Fetch posts from Reddit's public JSON API."""
    try:
        if query:
            url = f"https://www.reddit.com/r/{subreddit}/search.json"
            params = {"q": query, "sort": sort, "t": "day", "limit": limit, "restrict_sr": 1}
        else:
            url = f"https://www.reddit.com/r/{subreddit}/{sort}.json"
            params = {"limit": limit, "t": "day"}

        headers = {"User-Agent": REDDIT_USER_AGENT}
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
        posts = data.get("data", {}).get("children", [])
        return [p["data"] for p in posts]
    except Exception:
        return []


def _reddit_praw(subreddits: list[str], symbols: list[str], limit: int = 50) -> list[dict]:
    """Use PRAW if credentials are available."""
    try:
        import praw
        reddit = praw.Reddit(
            client_id=REDDIT_CLIENT_ID,
            client_secret=REDDIT_CLIENT_SECRET,
            user_agent=REDDIT_USER_AGENT,
            read_only=True,
        )
        posts = []
        combined = reddit.subreddit("+".join(subreddits))
        for submission in combined.hot(limit=limit):
            posts.append({
                "id": submission.id,
                "title": submission.title,
                "selftext": submission.selftext,
                "score": submission.score,
                "num_comments": submission.num_comments,
                "url": f"https://reddit.com{submission.permalink}",
                "subreddit": submission.subreddit.display_name,
                "created_utc": submission.created_utc,
            })
        return posts
    except Exception:
        return []


def scan_reddit(symbols: list[str], subreddits: Optional[list[str]] = None) -> dict:
    """
    Scan Reddit for mentions of given symbols.
    Returns per-symbol mention counts + top posts.
    """
    subs = subreddits or SOCIAL_SUBREDDITS
    all_posts = []

    use_praw = bool(REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET)

    if use_praw:
        all_posts = _reddit_praw(subs, symbols)
    else:
        # Fall back to public API — scrape hot posts from each sub
        for sub in subs[:3]:  # Limit to avoid rate limiting
            posts = _reddit_public_search(sub, sort="hot", limit=25)
            for p in posts:
                p["subreddit"] = sub
                all_posts.append(p)
            time.sleep(0.5)  # Respect rate limit

    # Count mentions per symbol
    mention_counts: dict[str, int] = {s: 0 for s in symbols}
    top_posts: dict[str, list] = {s: [] for s in symbols}

    for post in all_posts:
        combined_text = f"{post.get('title', '')} {post.get('selftext', '')}".upper()
        mentioned = [s for s in symbols if s.upper() in combined_text]
        for sym in mentioned:
            mention_counts[sym] += 1
            score = post.get("score", 0)
            top_posts[sym].append({
                "title": post.get("title", ""),
                "score": score,
                "num_comments": post.get("num_comments", 0),
                "url": post.get("url") or f"https://reddit.com{post.get('permalink', '')}",
                "subreddit": post.get("subreddit", ""),
            })

    # Sort top posts by score
    for sym in symbols:
        top_posts[sym] = sorted(top_posts[sym], key=lambda p: p["score"], reverse=True)[:5]

    return {
        "mentions": mention_counts,
        "top_posts": top_posts,
        "total_posts_scanned": len(all_posts),
        "subreddits": subs,
        "method": "praw" if use_praw else "public_api",
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Google Trends ──────────────────────────────────────────────────────────────

def get_google_trends(symbols: list[str]) -> dict:
    """
    Fetch relative Google search interest for given symbols over the past 7 days.
    Returns interest scores (0-100 scale) and weekly trend direction.
    """
    if not symbols:
        return {"trends": {}, "error": "No symbols provided"}

    try:
        from pytrends.request import TrendReq
        pytrends = TrendReq(hl="en-US", tz=360, timeout=(10, 25))

        # pytrends handles max 5 symbols per request
        chunks = [symbols[i:i+5] for i in range(0, len(symbols), 5)]
        all_interest: dict[str, dict] = {}

        for chunk in chunks:
            try:
                pytrends.build_payload(chunk, cat=0, timeframe="now 7-d", geo="US")
                df = pytrends.interest_over_time()
                if df.empty:
                    continue
                for sym in chunk:
                    if sym in df.columns:
                        values = df[sym].tolist()
                        current = values[-1] if values else 0
                        week_ago = values[0] if values else 0
                        trend_pct = ((current - week_ago) / week_ago * 100) if week_ago > 0 else 0
                        all_interest[sym] = {
                            "current_interest": int(current),
                            "week_ago_interest": int(week_ago),
                            "trend_pct": round(trend_pct, 1),
                            "trending_up": trend_pct > 10,
                            "values": [int(v) for v in values[-24:]],  # Last 24 data points
                        }
                time.sleep(1)  # Avoid rate limit
            except Exception:
                continue

        return {"trends": all_interest, "generated_at": datetime.now(timezone.utc).isoformat()}
    except ImportError:
        return {"trends": {}, "error": "pytrends not installed"}
    except Exception as e:
        return {"trends": {}, "error": str(e)}


# ── Social Arbitrage Scanner ───────────────────────────────────────────────────

def find_social_arbitrage(symbols: list[str]) -> list[dict]:
    """
    Identify potential social arbitrage plays:
    High Reddit mentions + rising Google Trends, but price has NOT yet moved.
    These are early signals before retail FOMO pushes price.
    """
    if not symbols:
        return []

    reddit_data = scan_reddit(symbols)
    trends_data = get_google_trends(symbols)

    mentions = reddit_data.get("mentions", {})
    trends = trends_data.get("trends", {})

    plays = []
    for sym in symbols:
        mention_count = mentions.get(sym, 0)
        trend_info = trends.get(sym, {})
        trend_pct = trend_info.get("trend_pct", 0)
        trending_up = trend_info.get("trending_up", False)

        # Only flag if there's social activity
        if mention_count < 2 and not trending_up:
            continue

        # Get price change to check if it's already moved
        try:
            hist = yf.Ticker(sym).history(period="5d", auto_adjust=True)
            if hist.empty or len(hist) < 2:
                price_change_pct = None
            else:
                prev = hist["Close"].iloc[-2]
                curr = hist["Close"].iloc[-1]
                price_change_pct = round((curr - prev) / prev * 100, 2) if prev else None
        except Exception:
            price_change_pct = None

        # Score the opportunity: high social + low price move = best arbitrage
        social_score = (mention_count * 2) + (trend_pct / 10)
        price_move = abs(price_change_pct) if price_change_pct is not None else 0
        arb_score = social_score - (price_move * 0.5)

        plays.append({
            "symbol": sym,
            "reddit_mentions": mention_count,
            "google_trend_pct": trend_pct,
            "price_change_pct_today": price_change_pct,
            "arb_score": round(arb_score, 2),
            "signal": (
                "Strong" if arb_score > 10 else
                "Moderate" if arb_score > 5 else
                "Weak"
            ),
            "note": (
                f"High social interest (+{trend_pct:.0f}% search trend, {mention_count} Reddit mentions) "
                f"with {'minimal' if price_move < 2 else 'some'} price movement today"
            ),
        })

    plays.sort(key=lambda p: p["arb_score"], reverse=True)
    return plays
