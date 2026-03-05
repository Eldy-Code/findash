from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from backend.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=utcnow)
    source_filename = Column(String)
    total_value = Column(Float)
    total_cost = Column(Float)
    total_gain_loss = Column(Float)
    total_gain_loss_pct = Column(Float)
    today_gain_loss = Column(Float)
    today_gain_loss_pct = Column(Float)

    positions = relationship("Position", back_populates="snapshot", cascade="all, delete-orphan")


class Position(Base):
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, index=True)
    snapshot_id = Column(Integer, ForeignKey("portfolio_snapshots.id"))
    account_name = Column(String)
    account_number = Column(String)
    symbol = Column(String, index=True)
    description = Column(String)
    quantity = Column(Float)
    last_price = Column(Float)
    current_value = Column(Float)
    avg_cost = Column(Float)
    cost_basis = Column(Float)
    gain_loss_dollar = Column(Float)
    gain_loss_pct = Column(Float)
    today_gain_loss_dollar = Column(Float)
    today_gain_loss_pct = Column(Float)
    percent_of_account = Column(Float)
    position_type = Column(String)  # Cash, Margin, etc.

    snapshot = relationship("PortfolioSnapshot", back_populates="positions")


class Suggestion(Base):
    __tablename__ = "suggestions"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=utcnow)
    symbol = Column(String, index=True)
    action = Column(String)       # BUY, SELL, REDUCE, WATCH, AVOID
    rationale = Column(Text)
    source = Column(String)       # research, social, options, polymarket, risk, manual
    entry_price = Column(Float)
    target_price = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=True)
    notes = Column(Text, nullable=True)
    is_open = Column(Boolean, default=True)
    closed_at = Column(DateTime, nullable=True)
    close_price = Column(Float, nullable=True)
    final_pnl_pct = Column(Float, nullable=True)

    updates = relationship("SuggestionUpdate", back_populates="suggestion", cascade="all, delete-orphan")


class SuggestionUpdate(Base):
    __tablename__ = "suggestion_updates"

    id = Column(Integer, primary_key=True, index=True)
    suggestion_id = Column(Integer, ForeignKey("suggestions.id"))
    updated_at = Column(DateTime, default=utcnow)
    current_price = Column(Float)
    pnl_dollar = Column(Float)
    pnl_pct = Column(Float)

    suggestion = relationship("Suggestion", back_populates="updates")
