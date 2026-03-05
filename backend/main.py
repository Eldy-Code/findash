"""
FinDash — FastAPI backend.
Run with: uvicorn backend.main:app --reload
"""
import os
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Depends, File, UploadFile, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("findash")

from backend.database import get_db, init_db
from backend.models import PortfolioSnapshot, Position
from backend.modules import portfolio as portfolio_mod
from backend.modules import market as market_mod
from backend.modules import research as research_mod
from backend.modules import social as social_mod
from backend.modules import opportunities as opp_mod
from backend.modules import tracker as tracker_mod

# ── Scheduler ─────────────────────────────────────────────────────────────────

scheduler = BackgroundScheduler()


def _daily_refresh_job():
    """Refresh suggestion prices every day at market close."""
    from backend.database import SessionLocal
    db = SessionLocal()
    try:
        result = tracker_mod.refresh_suggestion_prices(db)
        logger.info(f"Daily price refresh: {result}")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler.add_job(_daily_refresh_job, "cron", hour=16, minute=30, timezone="America/New_York")
    scheduler.start()
    logger.info("FinDash started.")
    yield
    scheduler.shutdown()


# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="FinDash",
    description="Personal financial dashboard — portfolio, research, social, and opportunities.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static frontend
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def root():
    index = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    return {"message": "FinDash API running. See /docs for API reference."}


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


# ── Portfolio ──────────────────────────────────────────────────────────────────

@app.post("/api/portfolio/upload")
async def upload_portfolio(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload a Fidelity positions CSV export."""
    content = await file.read()
    try:
        parsed = portfolio_mod.parse_fidelity_csv(content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    snapshot = PortfolioSnapshot(
        source_filename=file.filename,
        total_value=parsed["total_value"],
        total_cost=parsed["total_cost"],
        total_gain_loss=parsed["total_gain_loss"],
        total_gain_loss_pct=parsed["total_gain_loss_pct"],
        today_gain_loss=parsed["today_gain_loss"],
        today_gain_loss_pct=parsed["today_gain_loss_pct"],
    )
    db.add(snapshot)
    db.flush()

    for pos_data in parsed["positions"]:
        pos = Position(snapshot_id=snapshot.id, **pos_data)
        db.add(pos)

    db.commit()
    db.refresh(snapshot)

    alerts = portfolio_mod.get_risk_alerts(parsed["positions"])
    return {
        "snapshot_id": snapshot.id,
        "total_value": parsed["total_value"],
        "total_gain_loss": parsed["total_gain_loss"],
        "total_gain_loss_pct": parsed["total_gain_loss_pct"],
        "today_gain_loss": parsed["today_gain_loss"],
        "today_gain_loss_pct": parsed["today_gain_loss_pct"],
        "position_count": len(parsed["positions"]),
        "alerts": alerts,
    }


@app.get("/api/portfolio/latest")
def get_latest_portfolio(db: Session = Depends(get_db)):
    """Return the most recently uploaded portfolio snapshot."""
    snapshot = (
        db.query(PortfolioSnapshot)
        .order_by(PortfolioSnapshot.created_at.desc())
        .first()
    )
    if not snapshot:
        return {"snapshot": None, "positions": [], "alerts": []}

    positions = [
        {
            "id": p.id,
            "account_name": p.account_name,
            "account_number": p.account_number,
            "symbol": p.symbol,
            "description": p.description,
            "quantity": p.quantity,
            "last_price": p.last_price,
            "current_value": p.current_value,
            "avg_cost": p.avg_cost,
            "cost_basis": p.cost_basis,
            "gain_loss_dollar": p.gain_loss_dollar,
            "gain_loss_pct": p.gain_loss_pct,
            "today_gain_loss_dollar": p.today_gain_loss_dollar,
            "today_gain_loss_pct": p.today_gain_loss_pct,
            "percent_of_account": p.percent_of_account,
            "position_type": p.position_type,
        }
        for p in snapshot.positions
    ]

    alerts = portfolio_mod.get_risk_alerts(positions)

    return {
        "snapshot": {
            "id": snapshot.id,
            "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
            "source_filename": snapshot.source_filename,
            "total_value": snapshot.total_value,
            "total_cost": snapshot.total_cost,
            "total_gain_loss": snapshot.total_gain_loss,
            "total_gain_loss_pct": snapshot.total_gain_loss_pct,
            "today_gain_loss": snapshot.today_gain_loss,
            "today_gain_loss_pct": snapshot.today_gain_loss_pct,
        },
        "positions": positions,
        "alerts": alerts,
    }


@app.get("/api/portfolio/snapshots")
def list_snapshots(db: Session = Depends(get_db)):
    snapshots = (
        db.query(PortfolioSnapshot)
        .order_by(PortfolioSnapshot.created_at.desc())
        .limit(30)
        .all()
    )
    return [
        {
            "id": s.id,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "source_filename": s.source_filename,
            "total_value": s.total_value,
            "total_gain_loss_pct": s.total_gain_loss_pct,
        }
        for s in snapshots
    ]


# ── Market ─────────────────────────────────────────────────────────────────────

@app.get("/api/market/summary")
def get_market_summary():
    """Major index prices and day performance."""
    return market_mod.get_market_summary()


@app.get("/api/market/sectors")
def get_sector_performance():
    """S&P sector ETF performance."""
    return market_mod.get_sector_performance()


@app.get("/api/market/movers")
def get_top_movers(top_n: int = 10):
    """Top gainers and losers from the large-cap universe."""
    return market_mod.get_top_movers(top_n=top_n)


@app.get("/api/market/events")
def get_economic_events(days_ahead: int = 30, db: Session = Depends(get_db)):
    """Upcoming economic events + earnings for held positions."""
    macro_events = market_mod.get_economic_events(days_ahead=days_ahead)

    # Get symbols from latest snapshot
    snapshot = (
        db.query(PortfolioSnapshot)
        .order_by(PortfolioSnapshot.created_at.desc())
        .first()
    )
    earnings_events = []
    if snapshot:
        symbols = [p.symbol for p in snapshot.positions if p.symbol and not p.symbol.startswith("F")]
        earnings_events = market_mod.get_earnings_calendar(symbols[:20])  # Limit to avoid slowness

    all_events = macro_events + earnings_events
    all_events.sort(key=lambda e: e.get("date") or "")
    return all_events


# ── Research ───────────────────────────────────────────────────────────────────

@app.get("/api/research/{symbol}")
def research_symbol(symbol: str, db: Session = Depends(get_db)):
    """Deep-dive research on a symbol with recommendation."""
    symbol = symbol.upper()

    # Check if it's a held position to pass gain/loss context
    gain_loss_pct = None
    snapshot = (
        db.query(PortfolioSnapshot)
        .order_by(PortfolioSnapshot.created_at.desc())
        .first()
    )
    if snapshot:
        for pos in snapshot.positions:
            if pos.symbol == symbol:
                gain_loss_pct = pos.gain_loss_pct
                break

    return research_mod.research_symbol(symbol, gain_loss_pct=gain_loss_pct)


@app.get("/api/research/{symbol}/history")
def get_price_history(symbol: str, period: str = "1y"):
    """OHLCV price history for charting (1d, 5d, 1mo, 3mo, 6mo, 1y, 2y)."""
    from backend.modules._prices import get_history
    return get_history(symbol.upper(), period=period)


@app.get("/api/portfolio/history")
def get_portfolio_history(db: Session = Depends(get_db)):
    """Portfolio value across all uploaded snapshots (for line chart)."""
    snapshots = (
        db.query(PortfolioSnapshot)
        .order_by(PortfolioSnapshot.created_at)
        .all()
    )
    return [
        {
            "date": s.created_at.strftime("%Y-%m-%d %H:%M") if s.created_at else None,
            "total_value": s.total_value,
            "total_gain_loss_pct": s.total_gain_loss_pct,
            "today_gain_loss_pct": s.today_gain_loss_pct,
        }
        for s in snapshots
    ]


@app.get("/api/research/portfolio/all")
def research_all_positions(db: Session = Depends(get_db)):
    """
    Research all positions in the latest portfolio snapshot.
    Returns a quick summary (action + rationale) for each.
    """
    snapshot = (
        db.query(PortfolioSnapshot)
        .order_by(PortfolioSnapshot.created_at.desc())
        .first()
    )
    if not snapshot:
        return []

    results = []
    for pos in snapshot.positions:
        sym = pos.symbol
        if not sym or sym.startswith("F"):  # Skip Fidelity cash positions
            continue
        try:
            report = research_mod.research_symbol(sym, gain_loss_pct=pos.gain_loss_pct)
            results.append({
                "symbol": sym,
                "description": pos.description,
                "action": report["recommendation"]["action"],
                "rationale": report["recommendation"]["rationale"],
                "gain_loss_pct": pos.gain_loss_pct,
                "current_value": pos.current_value,
            })
        except Exception as e:
            results.append({"symbol": sym, "error": str(e)})

    return results


# ── Social ─────────────────────────────────────────────────────────────────────

@app.get("/api/social/reddit")
def reddit_scan(symbols: str = "", db: Session = Depends(get_db)):
    """
    Scan Reddit for symbol mentions.
    ?symbols=AAPL,TSLA or leave empty to use portfolio positions.
    """
    if symbols:
        sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        snapshot = (
            db.query(PortfolioSnapshot)
            .order_by(PortfolioSnapshot.created_at.desc())
            .first()
        )
        sym_list = [p.symbol for p in snapshot.positions if p.symbol] if snapshot else []

    if not sym_list:
        return {"error": "No symbols provided and no portfolio uploaded"}

    return social_mod.scan_reddit(sym_list[:15])  # Cap at 15 to avoid timeouts


@app.get("/api/social/trends")
def google_trends(symbols: str = "", db: Session = Depends(get_db)):
    """Google Trends for symbols. Use portfolio positions if no symbols given."""
    if symbols:
        sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        snapshot = (
            db.query(PortfolioSnapshot)
            .order_by(PortfolioSnapshot.created_at.desc())
            .first()
        )
        sym_list = [p.symbol for p in snapshot.positions if p.symbol] if snapshot else []

    return social_mod.get_google_trends(sym_list[:10])


@app.get("/api/social/arbitrage")
def social_arbitrage(symbols: str = "", db: Session = Depends(get_db)):
    """Find social arbitrage plays — high buzz, price hasn't moved yet."""
    if symbols:
        sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        snapshot = (
            db.query(PortfolioSnapshot)
            .order_by(PortfolioSnapshot.created_at.desc())
            .first()
        )
        sym_list = [p.symbol for p in snapshot.positions if p.symbol] if snapshot else []

    if not sym_list:
        return []

    return social_mod.find_social_arbitrage(sym_list[:15])


# ── Opportunities ──────────────────────────────────────────────────────────────

@app.get("/api/opportunities/polymarket")
def polymarket_opportunities():
    """Finance-related Polymarket prediction markets sorted by volume."""
    return opp_mod.get_polymarket_opportunities()


@app.get("/api/opportunities/options")
def unusual_options(symbols: str = "", db: Session = Depends(get_db)):
    """
    Unusual options flow for given symbols (or portfolio positions).
    High volume vs. open interest signals institutional activity.
    """
    if symbols:
        sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        snapshot = (
            db.query(PortfolioSnapshot)
            .order_by(PortfolioSnapshot.created_at.desc())
            .first()
        )
        sym_list = [p.symbol for p in snapshot.positions if p.symbol] if snapshot else []

    if not sym_list:
        return []

    return opp_mod.get_unusual_options_flow(sym_list[:10])  # Cap to keep it fast


# ── Tracker ────────────────────────────────────────────────────────────────────

class SuggestionCreate(BaseModel):
    symbol: str
    action: str
    rationale: str
    source: str = "manual"
    entry_price: float
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None
    notes: Optional[str] = None


class SuggestionClose(BaseModel):
    close_price: Optional[float] = None


@app.get("/api/tracker/suggestions")
def get_suggestions(db: Session = Depends(get_db)):
    """All open and closed suggestions with performance data."""
    return tracker_mod.get_all_suggestions(db)


@app.post("/api/tracker/suggestions")
def create_suggestion(body: SuggestionCreate, db: Session = Depends(get_db)):
    """Add a new tracked suggestion."""
    suggestion = tracker_mod.add_suggestion(
        db=db,
        symbol=body.symbol,
        action=body.action,
        rationale=body.rationale,
        source=body.source,
        entry_price=body.entry_price,
        target_price=body.target_price,
        stop_loss=body.stop_loss,
        notes=body.notes,
    )
    return {"id": suggestion.id, "symbol": suggestion.symbol, "action": suggestion.action}


@app.put("/api/tracker/suggestions/{suggestion_id}/close")
def close_suggestion(suggestion_id: int, body: SuggestionClose, db: Session = Depends(get_db)):
    """Close a suggestion and record final P&L."""
    sug = tracker_mod.close_suggestion(db, suggestion_id, body.close_price)
    if not sug:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    return {"id": sug.id, "final_pnl_pct": sug.final_pnl_pct, "close_price": sug.close_price}


@app.delete("/api/tracker/suggestions/{suggestion_id}")
def delete_suggestion(suggestion_id: int, db: Session = Depends(get_db)):
    """Permanently delete a suggestion."""
    ok = tracker_mod.delete_suggestion(db, suggestion_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    return {"deleted": True}


@app.post("/api/tracker/refresh")
def refresh_prices(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Manually trigger a price refresh for all open suggestions."""
    result = tracker_mod.refresh_suggestion_prices(db)
    return result


# Auto-save research recommendations as suggestions
@app.post("/api/tracker/suggestions/from-research/{symbol}")
def save_research_suggestion(symbol: str, db: Session = Depends(get_db)):
    """Run research on a symbol and auto-save the recommendation as a tracked suggestion."""
    symbol = symbol.upper()

    gain_loss_pct = None
    snapshot = (
        db.query(PortfolioSnapshot)
        .order_by(PortfolioSnapshot.created_at.desc())
        .first()
    )
    if snapshot:
        for pos in snapshot.positions:
            if pos.symbol == symbol:
                gain_loss_pct = pos.gain_loss_pct
                break

    report = research_mod.research_symbol(symbol, gain_loss_pct=gain_loss_pct)
    rec = report["recommendation"]
    price = report["key_stats"].get("current_price") or 0

    sug = tracker_mod.add_suggestion(
        db=db,
        symbol=symbol,
        action=rec["action"],
        rationale=" | ".join(rec["rationale"]),
        source="research",
        entry_price=price,
    )
    return {"id": sug.id, "action": rec["action"], "symbol": symbol}
