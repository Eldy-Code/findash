"""
Portfolio module — Fidelity CSV parsing, live price enrichment, P&L calculation, risk alerts.

CSV import: only trusts Symbol, Description, Quantity, Cost Basis, Avg Cost from the file.
All price/value fields are calculated from live market data via yfinance.
"""
import io
import pandas as pd
from typing import Optional
from backend.modules._prices import get_quotes


RISK_THRESHOLD_PCT = 50.0   # alert when gain >= 50%
REDUCE_SIZE_PCT = 25.0       # suggested size reduction

# Fidelity money market fund symbols and patterns
MONEY_MARKET_SYMBOLS = {"SPAXX", "FDRXX", "FZFXX", "SPRXX", "FMPXX", "FZDXX", "FTEXX"}

def _is_money_market(pos: dict) -> bool:
    sym = (pos.get("symbol") or "").upper().strip("*")
    desc = (pos.get("description") or "").lower()
    pos_type = (pos.get("position_type") or "").lower()
    return (
        sym in MONEY_MARKET_SYMBOLS
        or sym.startswith("FCASH")
        or "money market" in desc
        or "money market" in pos_type
        or "cash" in pos_type
    )


def _clean_numeric(val) -> Optional[float]:
    """Strip $, %, commas and convert to float. Returns None if not convertible."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip().replace("$", "").replace(",", "").replace("%", "").replace("+", "")
    if s in ("", "--", "n/a", "N/A", "None", "nan"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _find_header_row(lines: list[str]) -> int:
    """Find the index of the real column header row in the CSV."""
    for i, line in enumerate(lines):
        if "Symbol" in line and "Description" in line:
            return i
        if "Account Number" in line and "Symbol" in line:
            return i
    return 0


def enrich_with_live_prices(positions: list[dict]) -> list[dict]:
    """
    Fetch live prices for all positions via the multi-source price fetcher and
    calculate: current_value, gain_loss_dollar, gain_loss_pct,
    today_gain_loss_dollar, today_gain_loss_pct, percent_of_account.

    Money market positions are treated as $1/share (stable NAV).
    """
    tradeable = list(dict.fromkeys(
        p["symbol"] for p in positions
        if not _is_money_market(p) and (p.get("quantity") or 0) != 0
    ))

    quotes = get_quotes(tradeable) if tradeable else {}

    # Annotate positions
    for pos in positions:
        sym = pos["symbol"]
        qty = pos.get("quantity") or 0
        cost = pos.get("cost_basis") or 0

        if _is_money_market(pos):
            pos["last_price"] = 1.0
            pos["current_value"] = round(qty, 2)
            pos["gain_loss_dollar"] = round(qty - cost, 2) if cost else 0.0
            pos["gain_loss_pct"] = 0.0
            pos["today_gain_loss_dollar"] = 0.0
            pos["today_gain_loss_pct"] = 0.0
        elif sym in quotes and qty:
            q     = quotes[sym]
            price = q["price"]
            prev  = q["prev_close"]
            pos["last_price"] = round(price, 4)
            pos["current_value"] = round(price * qty, 2)
            pos["gain_loss_dollar"] = round(pos["current_value"] - cost, 2) if cost else None
            pos["gain_loss_pct"] = (
                round((pos["current_value"] - cost) / cost * 100, 2) if cost else None
            )
            pos["today_gain_loss_dollar"] = round((price - prev) * qty, 2)
            pos["today_gain_loss_pct"] = round((price - prev) / prev * 100, 2) if prev else 0.0
        else:
            # Price unavailable — leave values as None
            pos.setdefault("last_price", None)
            pos.setdefault("current_value", None)
            pos.setdefault("gain_loss_dollar", None)
            pos.setdefault("gain_loss_pct", None)
            pos.setdefault("today_gain_loss_dollar", None)
            pos.setdefault("today_gain_loss_pct", None)

    # Calculate percent_of_account based on live values
    total_val = sum(p.get("current_value") or 0 for p in positions)
    for pos in positions:
        cv = pos.get("current_value")
        pos["percent_of_account"] = round(cv / total_val * 100, 2) if cv and total_val else None

    return positions


def parse_fidelity_csv(content: bytes | str) -> dict:
    """
    Parse a Fidelity positions CSV.
    Only reads: Symbol, Description, Quantity, Cost Basis Total, Average Cost Basis.
    All price/value fields are derived from live market data.

    Returns a dict with:
      - positions: list of position dicts (price-enriched)
      - total_value, total_cost, total_gain_loss, total_gain_loss_pct
      - today_gain_loss, today_gain_loss_pct
    """
    if isinstance(content, bytes):
        text = content.decode("utf-8", errors="replace")
    else:
        text = content

    lines = text.splitlines()
    header_idx = _find_header_row(lines)
    csv_text = "\n".join(lines[header_idx:])

    try:
        df = pd.read_csv(io.StringIO(csv_text), thousands=",")
    except Exception as e:
        raise ValueError(f"Could not parse CSV: {e}")

    df.columns = [c.strip().replace("\u2019", "'") for c in df.columns]

    if "Symbol" in df.columns:
        df = df[df["Symbol"].notna()]
        df = df[~df["Symbol"].astype(str).str.startswith("Totals")]
        df = df[~df["Symbol"].astype(str).str.startswith("Account")]

    positions = []
    for _, row in df.iterrows():
        symbol = str(row.get("Symbol", "")).strip()
        if not symbol or symbol in ("nan", ""):
            continue

        pos = {
            "account_name":   str(row.get("Account Name", "")).strip(),
            "account_number": str(row.get("Account Number", "")).strip(),
            "symbol":         symbol,
            "description":    str(row.get("Description", "")).strip(),
            "quantity":       _clean_numeric(row.get("Quantity")),
            "avg_cost":       _clean_numeric(row.get("Average Cost Basis")),
            "cost_basis":     _clean_numeric(row.get("Cost Basis Total")),
            "position_type":  str(row.get("Type", "")).strip(),
            # Price fields populated by enrichment
            "last_price":             None,
            "current_value":          None,
            "gain_loss_dollar":       None,
            "gain_loss_pct":          None,
            "today_gain_loss_dollar": None,
            "today_gain_loss_pct":    None,
            "percent_of_account":     None,
        }
        positions.append(pos)

    # Enrich all positions with live prices
    positions = enrich_with_live_prices(positions)

    # Aggregate totals from live data
    total_value     = sum(p.get("current_value") or 0 for p in positions)
    total_cost      = sum(p.get("cost_basis") or 0 for p in positions)
    total_gain_loss = sum(p.get("gain_loss_dollar") or 0 for p in positions)
    today_gain_loss = sum(p.get("today_gain_loss_dollar") or 0 for p in positions)
    total_gain_loss_pct = (total_gain_loss / total_cost * 100) if total_cost else 0
    today_gain_loss_pct = (today_gain_loss / (total_value - today_gain_loss) * 100) if total_value else 0

    return {
        "positions": positions,
        "total_value":          round(total_value, 2),
        "total_cost":           round(total_cost, 2),
        "total_gain_loss":      round(total_gain_loss, 2),
        "total_gain_loss_pct":  round(total_gain_loss_pct, 2),
        "today_gain_loss":      round(today_gain_loss, 2),
        "today_gain_loss_pct":  round(today_gain_loss_pct, 2),
    }


def get_risk_alerts(positions: list[dict]) -> list[dict]:
    """
    Scan positions for risk conditions.
    Skips money market / cash positions.

    Rules:
      - Gain >= 50%: recommend reducing position by 25%
    """
    alerts = []
    for pos in positions:
        if _is_money_market(pos):
            continue

        gain_pct = pos.get("gain_loss_pct")
        if gain_pct is None:
            continue

        if gain_pct >= RISK_THRESHOLD_PCT:
            current_value = pos.get("current_value") or 0
            reduce_value  = current_value * (REDUCE_SIZE_PCT / 100)
            quantity      = pos.get("quantity") or 0
            reduce_shares = quantity * (REDUCE_SIZE_PCT / 100)

            alerts.append({
                "type":     "REDUCE_POSITION",
                "severity": "high" if gain_pct >= 100 else "medium",
                "symbol":   pos["symbol"],
                "description": pos.get("description", ""),
                "gain_pct": round(gain_pct, 2),
                "current_value": round(current_value, 2),
                "message": (
                    f"{pos['symbol']} is up {gain_pct:.1f}% — consider reducing by "
                    f"{REDUCE_SIZE_PCT:.0f}% (≈{reduce_shares:.1f} shares / ${reduce_value:,.0f})"
                ),
                "suggested_action": f"SELL {reduce_shares:.1f} shares (${reduce_value:,.0f})",
            })

    alerts.sort(key=lambda a: a["gain_pct"], reverse=True)
    return alerts
