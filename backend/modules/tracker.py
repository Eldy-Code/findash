"""
Tracker module — CRUD for suggestions and daily performance tracking.
All suggestions stay tracked until manually closed by the user.
"""
import yfinance as yf
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from backend.models import Suggestion, SuggestionUpdate


def utcnow():
    return datetime.now(timezone.utc)


def add_suggestion(
    db: Session,
    symbol: str,
    action: str,
    rationale: str,
    source: str,
    entry_price: float,
    target_price: float | None = None,
    stop_loss: float | None = None,
    notes: str | None = None,
) -> Suggestion:
    """Create a new tracked suggestion."""
    suggestion = Suggestion(
        symbol=symbol.upper(),
        action=action.upper(),
        rationale=rationale,
        source=source,
        entry_price=entry_price,
        target_price=target_price,
        stop_loss=stop_loss,
        notes=notes,
        is_open=True,
    )
    db.add(suggestion)
    db.commit()
    db.refresh(suggestion)

    # Add initial price update
    _add_price_update(db, suggestion, entry_price)
    return suggestion


def _add_price_update(db: Session, suggestion: Suggestion, current_price: float):
    entry = suggestion.entry_price or current_price
    pnl_dollar = current_price - entry
    pnl_pct = (pnl_dollar / entry * 100) if entry else 0

    if suggestion.action in ("SELL", "REDUCE", "SHORT"):
        pnl_dollar = -pnl_dollar
        pnl_pct = -pnl_pct

    update = SuggestionUpdate(
        suggestion_id=suggestion.id,
        current_price=current_price,
        pnl_dollar=round(pnl_dollar, 4),
        pnl_pct=round(pnl_pct, 2),
    )
    db.add(update)
    db.commit()


def refresh_suggestion_prices(db: Session) -> dict:
    """
    Fetch current prices for all open suggestions and record daily updates.
    Called by the scheduler daily (or on demand).
    """
    open_suggestions = db.query(Suggestion).filter(Suggestion.is_open == True).all()
    if not open_suggestions:
        return {"updated": 0, "errors": []}

    symbols = list({s.symbol for s in open_suggestions})
    prices = {}

    for sym in symbols:
        try:
            hist = yf.Ticker(sym).history(period="1d", auto_adjust=True)
            if not hist.empty:
                prices[sym] = float(hist["Close"].iloc[-1])
        except Exception:
            pass

    updated = 0
    errors = []
    for sug in open_suggestions:
        price = prices.get(sug.symbol)
        if price is None:
            errors.append(sug.symbol)
            continue
        _add_price_update(db, sug, price)
        updated += 1

    return {"updated": updated, "errors": errors}


def close_suggestion(db: Session, suggestion_id: int, close_price: float | None = None) -> Suggestion | None:
    """Mark a suggestion as closed with final P&L."""
    sug = db.query(Suggestion).filter(Suggestion.id == suggestion_id).first()
    if not sug:
        return None

    # Get latest price if not provided
    if close_price is None:
        try:
            hist = yf.Ticker(sug.symbol).history(period="1d", auto_adjust=True)
            close_price = float(hist["Close"].iloc[-1]) if not hist.empty else sug.entry_price
        except Exception:
            close_price = sug.entry_price

    entry = sug.entry_price or close_price
    final_pnl_pct = ((close_price - entry) / entry * 100) if entry else 0
    if sug.action in ("SELL", "REDUCE", "SHORT"):
        final_pnl_pct = -final_pnl_pct

    sug.is_open = False
    sug.closed_at = utcnow()
    sug.close_price = close_price
    sug.final_pnl_pct = round(final_pnl_pct, 2)
    db.commit()
    db.refresh(sug)
    return sug


def delete_suggestion(db: Session, suggestion_id: int) -> bool:
    sug = db.query(Suggestion).filter(Suggestion.id == suggestion_id).first()
    if not sug:
        return False
    db.delete(sug)
    db.commit()
    return True


def get_all_suggestions(db: Session) -> dict:
    """Return open and closed suggestions with their latest update."""
    all_sugs = db.query(Suggestion).order_by(Suggestion.created_at.desc()).all()

    def serialize(sug: Suggestion) -> dict:
        latest_update = (
            max(sug.updates, key=lambda u: u.updated_at)
            if sug.updates else None
        )
        return {
            "id": sug.id,
            "created_at": sug.created_at.isoformat() if sug.created_at else None,
            "symbol": sug.symbol,
            "action": sug.action,
            "rationale": sug.rationale,
            "source": sug.source,
            "entry_price": sug.entry_price,
            "target_price": sug.target_price,
            "stop_loss": sug.stop_loss,
            "notes": sug.notes,
            "is_open": sug.is_open,
            "closed_at": sug.closed_at.isoformat() if sug.closed_at else None,
            "close_price": sug.close_price,
            "final_pnl_pct": sug.final_pnl_pct,
            "current_price": latest_update.current_price if latest_update else None,
            "current_pnl_pct": latest_update.pnl_pct if latest_update else None,
            "current_pnl_dollar": latest_update.pnl_dollar if latest_update else None,
            "last_updated": latest_update.updated_at.isoformat() if latest_update else None,
            "update_history": [
                {
                    "date": u.updated_at.isoformat(),
                    "price": u.current_price,
                    "pnl_pct": u.pnl_pct,
                }
                for u in sorted(sug.updates, key=lambda u: u.updated_at)
            ],
        }

    open_sugs = [serialize(s) for s in all_sugs if s.is_open]
    closed_sugs = [serialize(s) for s in all_sugs if not s.is_open]

    # Performance stats
    win_rate = None
    avg_pnl = None
    if closed_sugs:
        pnls = [s["final_pnl_pct"] for s in closed_sugs if s["final_pnl_pct"] is not None]
        if pnls:
            wins = sum(1 for p in pnls if p > 0)
            win_rate = round(wins / len(pnls) * 100, 1)
            avg_pnl = round(sum(pnls) / len(pnls), 2)

    return {
        "open": open_sugs,
        "closed": closed_sugs,
        "stats": {
            "total": len(all_sugs),
            "open_count": len(open_sugs),
            "closed_count": len(closed_sugs),
            "win_rate": win_rate,
            "avg_pnl_pct": avg_pnl,
        },
    }
