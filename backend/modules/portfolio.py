"""
Portfolio module — Fidelity CSV parsing, P&L calculation, risk alerts.

Fidelity CSV export format (from Positions page):
- May have header metadata rows at the top
- Actual column header row contains "Symbol" or "Account Number"
- Data rows follow; may have trailing summary/footer rows
"""
import io
import re
import pandas as pd
from typing import Optional


FIDELITY_COLUMNS = {
    "Account Name": "account_name",
    "Account Number": "account_number",
    "Symbol": "symbol",
    "Description": "description",
    "Quantity": "quantity",
    "Last Price": "last_price",
    "Last Price Change": "last_price_change",
    "Current Value": "current_value",
    "Today's Gain/Loss Dollar": "today_gain_loss_dollar",
    "Today's Gain/Loss Percent": "today_gain_loss_pct",
    "Total Gain/Loss Dollar": "gain_loss_dollar",
    "Total Gain/Loss Percent": "gain_loss_pct",
    "Percent Of Account": "percent_of_account",
    "Cost Basis Total": "cost_basis",
    "Average Cost Basis": "avg_cost",
    "Type": "position_type",
}

RISK_THRESHOLD_PCT = 50.0   # alert when gain >= 50%
REDUCE_SIZE_PCT = 25.0       # suggested size reduction


def _clean_numeric(val) -> Optional[float]:
    """Strip $, %, commas and convert to float. Returns None if not convertible."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip().replace("$", "").replace(",", "").replace("%", "").replace("+", "")
    if s in ("", "--", "n/a", "N/A", "None"):
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


def parse_fidelity_csv(content: bytes | str) -> dict:
    """
    Parse a Fidelity positions CSV export.
    Returns a dict with:
      - positions: list of position dicts
      - total_value: float
      - total_cost: float
      - total_gain_loss: float
      - total_gain_loss_pct: float
      - today_gain_loss: float
      - today_gain_loss_pct: float
    """
    if isinstance(content, bytes):
        text = content.decode("utf-8", errors="replace")
    else:
        text = content

    lines = text.splitlines()

    # Find where the real header starts
    header_idx = _find_header_row(lines)
    csv_text = "\n".join(lines[header_idx:])

    try:
        df = pd.read_csv(io.StringIO(csv_text), thousands=",")
    except Exception as e:
        raise ValueError(f"Could not parse CSV: {e}")

    # Normalize column names
    df.columns = [c.strip().replace("\u2019", "'") for c in df.columns]

    # Drop rows that are clearly footers (no symbol or all-NaN)
    if "Symbol" in df.columns:
        df = df[df["Symbol"].notna()]
        df = df[~df["Symbol"].astype(str).str.startswith("Totals")]
        df = df[~df["Symbol"].astype(str).str.startswith("Account")]
        # Skip rows with no real ticker (cash positions have symbols like "FCASH**")
        # We keep them so the user sees full portfolio

    positions = []
    for _, row in df.iterrows():
        symbol = str(row.get("Symbol", "")).strip()
        if not symbol or symbol in ("nan", ""):
            continue

        pos = {
            "account_name": str(row.get("Account Name", "")).strip(),
            "account_number": str(row.get("Account Number", "")).strip(),
            "symbol": symbol,
            "description": str(row.get("Description", "")).strip(),
            "quantity": _clean_numeric(row.get("Quantity")),
            "last_price": _clean_numeric(row.get("Last Price")),
            "current_value": _clean_numeric(row.get("Current Value")),
            "avg_cost": _clean_numeric(row.get("Average Cost Basis")),
            "cost_basis": _clean_numeric(row.get("Cost Basis Total")),
            "gain_loss_dollar": _clean_numeric(row.get("Total Gain/Loss Dollar")),
            "gain_loss_pct": _clean_numeric(row.get("Total Gain/Loss Percent")),
            "today_gain_loss_dollar": _clean_numeric(row.get("Today's Gain/Loss Dollar")),
            "today_gain_loss_pct": _clean_numeric(row.get("Today's Gain/Loss Percent")),
            "percent_of_account": _clean_numeric(row.get("Percent Of Account")),
            "position_type": str(row.get("Type", "")).strip(),
        }
        positions.append(pos)

    # Aggregate totals
    total_value = sum(p["current_value"] or 0 for p in positions)
    total_cost = sum(p["cost_basis"] or 0 for p in positions)
    total_gain_loss = sum(p["gain_loss_dollar"] or 0 for p in positions)
    today_gain_loss = sum(p["today_gain_loss_dollar"] or 0 for p in positions)
    total_gain_loss_pct = (total_gain_loss / total_cost * 100) if total_cost else 0
    today_gain_loss_pct = (today_gain_loss / (total_value - today_gain_loss) * 100) if total_value else 0

    return {
        "positions": positions,
        "total_value": round(total_value, 2),
        "total_cost": round(total_cost, 2),
        "total_gain_loss": round(total_gain_loss, 2),
        "total_gain_loss_pct": round(total_gain_loss_pct, 2),
        "today_gain_loss": round(today_gain_loss, 2),
        "today_gain_loss_pct": round(today_gain_loss_pct, 2),
    }


def get_risk_alerts(positions: list[dict]) -> list[dict]:
    """
    Scan positions for risk conditions.
    Returns a list of alert dicts.

    Current rules:
      - Gain >= 50%: recommend reducing position by 25%
    """
    alerts = []
    for pos in positions:
        gain_pct = pos.get("gain_loss_pct")
        if gain_pct is None:
            continue

        if gain_pct >= RISK_THRESHOLD_PCT:
            current_value = pos.get("current_value") or 0
            reduce_value = current_value * (REDUCE_SIZE_PCT / 100)
            quantity = pos.get("quantity") or 0
            reduce_shares = quantity * (REDUCE_SIZE_PCT / 100)

            alerts.append({
                "type": "REDUCE_POSITION",
                "severity": "high" if gain_pct >= 100 else "medium",
                "symbol": pos["symbol"],
                "description": pos.get("description", ""),
                "gain_pct": round(gain_pct, 2),
                "current_value": round(current_value, 2),
                "message": (
                    f"{pos['symbol']} is up {gain_pct:.1f}% — consider reducing by "
                    f"{REDUCE_SIZE_PCT:.0f}% (≈{reduce_shares:.1f} shares / ${reduce_value:,.0f})"
                ),
                "suggested_action": f"SELL {reduce_shares:.1f} shares (${reduce_value:,.0f})",
            })

    # Sort by gain descending
    alerts.sort(key=lambda a: a["gain_pct"], reverse=True)
    return alerts
