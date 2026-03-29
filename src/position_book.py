"""Position book — central trade/position tracker with Greek tracking, P&L attribution, and journal.

Storage: Supabase `positions` + `pnl_history` tables, with JSON file fallback.

Usage:
    from src.position_book import add_position, get_positions, get_portfolio_summary
    add_position(ticker="SPY", type="stock", qty=100, entry_price=230.50)
"""

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Local fallback
POSITIONS_DIR = Path(__file__).parent.parent / "data" / "positions"
POSITIONS_FILE = POSITIONS_DIR / "positions.json"


def _db():
    from src.db import get_client
    return get_client()


def _user_id():
    from src.db import get_user_id
    return get_user_id()


def _load_local() -> list[dict]:
    if not POSITIONS_FILE.exists():
        return []
    try:
        with open(POSITIONS_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def _save_local(positions: list[dict]):
    POSITIONS_DIR.mkdir(parents=True, exist_ok=True)
    with open(POSITIONS_FILE, "w") as f:
        json.dump(positions, f, indent=2, default=str)


# ─── CORE CRUD ────────────────────────────────────────────────────────────────

def add_position(ticker: str, type: str, qty: int, entry_price: float,
                  details: dict = None, source_page: str = None) -> str:
    """Add a new position. Returns position ID."""
    pos_id = str(uuid.uuid4())[:8]
    entry = {
        "id": pos_id, "user_id": _user_id(), "ticker": ticker, "type": type,
        "qty": qty, "entry_price": entry_price,
        "entry_date": datetime.now().isoformat(),
        "details": details or {}, "source_page": source_page or "",
        "status": "open", "close_price": None, "close_date": None,
        "notes": "", "greeks": {}, "alerts": {},
        "journal": {"entry_thesis": "", "exit_thesis": "", "tags": [], "notes": []},
    }

    db = _db()
    if db:
        try:
            row = {**entry}
            row["details"] = json.dumps(row["details"])
            row["greeks"] = json.dumps(row["greeks"])
            row["alerts"] = json.dumps(row["alerts"])
            row["journal"] = json.dumps(row["journal"])
            db.table("positions").insert(row).execute()
            return pos_id
        except Exception as e:
            logger.warning(f"Supabase position insert failed: {e}")

    # Local fallback
    positions = _load_local()
    positions.append(entry)
    _save_local(positions)
    return pos_id


def close_position(pos_id: str, close_price: float, exit_thesis: str = ""):
    """Close a position by ID."""
    db = _db()
    if db:
        try:
            update = {"status": "closed", "close_price": close_price,
                      "close_date": datetime.now().isoformat()}
            db.table("positions").update(update).eq("id", pos_id).eq("user_id", _user_id()).execute()
            if exit_thesis:
                set_exit_thesis(pos_id, exit_thesis)
            return
        except Exception as e:
            logger.warning(f"Supabase position close failed: {e}")

    positions = _load_local()
    for p in positions:
        if p["id"] == pos_id:
            p["status"] = "closed"
            p["close_price"] = close_price
            p["close_date"] = datetime.now().isoformat()
            if exit_thesis:
                p.setdefault("journal", {})["exit_thesis"] = exit_thesis
            break
    _save_local(positions)


def remove_position(pos_id: str):
    """Remove a position entirely."""
    db = _db()
    if db:
        try:
            db.table("positions").delete().eq("id", pos_id).eq("user_id", _user_id()).execute()
            return
        except Exception as e:
            logger.warning(f"Supabase position remove failed: {e}")

    positions = _load_local()
    positions = [p for p in positions if p["id"] != pos_id]
    _save_local(positions)


def get_positions(status: str = "open") -> list[dict]:
    """Get all positions, optionally filtered by status."""
    db = _db()
    if db:
        try:
            q = db.table("positions").select("*").eq("user_id", _user_id())
            if status:
                q = q.eq("status", status)
            result = q.order("entry_date", desc=True).execute()
            rows = result.data or []
            # Parse JSONB fields
            for r in rows:
                for jf in ("details", "greeks", "alerts", "journal"):
                    if isinstance(r.get(jf), str):
                        try:
                            r[jf] = json.loads(r[jf])
                        except Exception:
                            r[jf] = {}
            return rows
        except Exception as e:
            logger.warning(f"Supabase positions read failed: {e}")

    positions = _load_local()
    if status:
        return [p for p in positions if p.get("status") == status]
    return positions


def get_portfolio_summary() -> dict:
    """Portfolio-level summary with live prices and alerts."""
    positions = get_positions("open")
    if not positions:
        return {"n_positions": 0, "total_pnl": 0, "positions": [], "alerts": []}

    enriched = []
    try:
        from src.data_engine import fetch_massive_data
        price_cache = {}
        for pos in positions:
            tk = pos["ticker"]
            if tk not in price_cache:
                px = fetch_massive_data(tk, 5)
                if px is not None and not px.empty:
                    price_cache[tk] = float(px["Close"].iloc[-1])
                else:
                    price_cache[tk] = None

            current = price_cache.get(tk)
            multiplier = 100 if pos["type"] not in ("stock",) else 1
            entry_val = pos["entry_price"] * abs(pos["qty"]) * multiplier
            current_val = current * abs(pos["qty"]) * multiplier if current else entry_val

            pnl = (current - pos["entry_price"]) * pos["qty"] if pos["type"] == "stock" and current else 0

            enriched.append({
                **pos, "current_price": current,
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl / entry_val * 100, 1) if entry_val > 0 else 0,
                "market_value": round(current_val, 2),
            })
    except Exception as e:
        logger.warning(f"Portfolio enrichment failed: {e}")
        enriched = positions

    total_pnl = sum(p.get("pnl", 0) for p in enriched)
    by_type = {}
    for p in enriched:
        t = p["type"]
        by_type[t] = by_type.get(t, 0) + 1

    return {
        "n_positions": len(enriched), "total_pnl": round(total_pnl, 2),
        "positions_by_type": by_type, "positions": enriched,
        "alerts": check_alerts(),
    }


# ─── GREEK TRACKING ──────────────────────────────────────────────────────────

def update_greeks(pos_id: str, spot: float = None) -> dict | None:
    """Recompute Greeks for a position."""
    positions = get_positions(status=None)
    pos = next((p for p in positions if p["id"] == pos_id), None)
    if not pos:
        return None

    details = pos.get("details", {})
    pos_type = pos.get("type", "stock")
    greeks = {"delta": 0, "gamma": 0, "theta": 0, "vega": 0,
              "delta_dollars": 0, "gamma_dollars": 0, "theta_dollars": 0, "vega_dollars": 0,
              "updated_at": datetime.now().isoformat()}

    if pos_type == "stock":
        greeks["delta"] = 1.0
        greeks["delta_dollars"] = float(pos["qty"]) * (spot or 0)
    elif pos_type in ("call", "put"):
        strike = details.get("strike")
        exp = details.get("expiration")
        if strike and exp and spot and spot > 0:
            try:
                import pandas as pd
                from src.options_models import bs_greeks
                dte = max((pd.to_datetime(exp) - pd.Timestamp.now()).days, 0)
                T = max(dte / 365.0, 0.001)
                iv = details.get("iv", 0.25) or 0.25
                g = bs_greeks(spot, strike, T, 0.045, iv, pos_type)
                mult = pos["qty"] * 100
                greeks.update({
                    "delta": g["delta"], "gamma": g["gamma"],
                    "theta": g["theta"], "vega": g["vega"],
                    "delta_dollars": g["delta"] * mult, "gamma_dollars": g["gamma"] * mult,
                    "theta_dollars": g["theta"] * mult, "vega_dollars": g["vega"] * mult,
                })
            except Exception as e:
                logger.warning(f"Greek computation failed for {pos_id}: {e}")

    # Persist
    db = _db()
    if db:
        try:
            db.table("positions").update({"greeks": json.dumps(greeks)})\
                .eq("id", pos_id).eq("user_id", _user_id()).execute()
        except Exception:
            pass
    else:
        local = _load_local()
        for p in local:
            if p["id"] == pos_id:
                p["greeks"] = greeks
                break
        _save_local(local)

    return greeks


def get_portfolio_greeks() -> dict:
    """Aggregate Greeks across all open positions."""
    positions = get_positions("open")
    totals = {"net_delta": 0, "net_gamma": 0, "net_theta": 0, "net_vega": 0}
    for p in positions:
        g = p.get("greeks", {})
        totals["net_delta"] += g.get("delta_dollars", 0)
        totals["net_gamma"] += g.get("gamma_dollars", 0)
        totals["net_theta"] += g.get("theta_dollars", 0)
        totals["net_vega"] += g.get("vega_dollars", 0)
    totals["n_positions"] = len(positions)
    totals["updated_at"] = datetime.now().isoformat()
    return totals


# ─── P&L ATTRIBUTION ──────────────────────────────────────────────────────────

def record_daily_pnl(pos_id: str, spot: float, iv: float,
                      prev_spot: float, prev_iv: float) -> dict | None:
    """Compute and store one day's P&L attribution."""
    positions = get_positions(status=None)
    pos = next((p for p in positions if p["id"] == pos_id), None)
    if not pos:
        return None

    g = pos.get("greeks", {})
    dS = spot - prev_spot
    dIV = iv - prev_iv
    mult = abs(pos["qty"]) * (100 if pos["type"] not in ("stock",) else 1)

    entry = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "delta_pnl": round(g.get("delta", 0) * dS * mult, 2),
        "gamma_pnl": round(0.5 * g.get("gamma", 0) * (dS ** 2) * mult, 2),
        "theta_pnl": round(g.get("theta", 0) * mult, 2),
        "vega_pnl": round(g.get("vega", 0) * dIV * mult, 2),
        "spot": spot, "iv": iv,
    }
    entry["total_pnl"] = round(entry["delta_pnl"] + entry["gamma_pnl"] + entry["theta_pnl"] + entry["vega_pnl"], 2)

    db = _db()
    if db:
        try:
            db.table("pnl_history").upsert({
                "position_id": pos_id, **entry
            }, on_conflict="position_id,date").execute()
            return entry
        except Exception as e:
            logger.warning(f"Supabase PnL insert failed: {e}")

    # Local fallback
    local = _load_local()
    for p in local:
        if p["id"] == pos_id:
            if "pnl_history" not in p:
                p["pnl_history"] = []
            p["pnl_history"] = [h for h in p["pnl_history"] if h.get("date") != entry["date"]]
            p["pnl_history"].append(entry)
            p["pnl_history"] = p["pnl_history"][-252:]
            break
    _save_local(local)
    return entry


# ─── ALERTS ───────────────────────────────────────────────────────────────────

def set_alerts(pos_id: str, **kwargs) -> None:
    """Set alert thresholds."""
    db = _db()
    if db:
        try:
            # Fetch current alerts, merge
            result = db.table("positions").select("alerts").eq("id", pos_id).execute()
            current = {}
            if result.data:
                raw = result.data[0].get("alerts", "{}")
                current = json.loads(raw) if isinstance(raw, str) else raw
            current.update({k: v for k, v in kwargs.items() if v is not None})
            db.table("positions").update({"alerts": json.dumps(current)}).eq("id", pos_id).execute()
            return
        except Exception:
            pass

    positions = _load_local()
    for p in positions:
        if p["id"] == pos_id:
            p.setdefault("alerts", {}).update({k: v for k, v in kwargs.items() if v is not None})
            break
    _save_local(positions)


def check_alerts() -> list[dict]:
    """Check all open positions against alert thresholds."""
    positions = get_positions("open")
    triggered = []
    for p in positions:
        alerts = p.get("alerts", {})
        g = p.get("greeks", {})
        if not alerts:
            continue
        checks = [
            ("max_delta", abs(g.get("delta_dollars", 0)), "Delta exposure"),
            ("max_loss_pct", abs(p.get("pnl_pct", 0)), "Loss %"),
        ]
        for key, current, label in checks:
            threshold = alerts.get(key)
            if threshold and current > threshold:
                triggered.append({"pos_id": p["id"], "ticker": p["ticker"],
                                  "alert_type": label, "current": round(current, 2),
                                  "threshold": threshold, "severity": "breach"})
            elif threshold and current > threshold * 0.8:
                triggered.append({"pos_id": p["id"], "ticker": p["ticker"],
                                  "alert_type": label, "current": round(current, 2),
                                  "threshold": threshold, "severity": "warning"})
    return triggered


# ─── TRADE JOURNAL ────────────────────────────────────────────────────────────

def _update_journal(pos_id: str, updates: dict) -> None:
    """Internal: merge updates into journal JSONB."""
    db = _db()
    if db:
        try:
            result = db.table("positions").select("journal").eq("id", pos_id).execute()
            current = {}
            if result.data:
                raw = result.data[0].get("journal", "{}")
                current = json.loads(raw) if isinstance(raw, str) else raw
            current.update(updates)
            db.table("positions").update({"journal": json.dumps(current)}).eq("id", pos_id).execute()
            return
        except Exception:
            pass

    positions = _load_local()
    for p in positions:
        if p["id"] == pos_id:
            p.setdefault("journal", {"entry_thesis": "", "exit_thesis": "", "tags": [], "notes": []})
            p["journal"].update(updates)
            break
    _save_local(positions)


def set_entry_thesis(pos_id: str, thesis: str, tags: list[str] = None,
                      signal_source: str = None, signal_conviction: float = None) -> None:
    updates = {"entry_thesis": thesis}
    if tags:
        updates["tags"] = tags
    if signal_source:
        updates["signal_source"] = signal_source
    if signal_conviction is not None:
        updates["signal_conviction"] = signal_conviction
    _update_journal(pos_id, updates)


def set_exit_thesis(pos_id: str, thesis: str) -> None:
    _update_journal(pos_id, {"exit_thesis": thesis})


def add_journal_note(pos_id: str, note: str) -> None:
    db = _db()
    if db:
        try:
            result = db.table("positions").select("journal").eq("id", pos_id).execute()
            journal = {}
            if result.data:
                raw = result.data[0].get("journal", "{}")
                journal = json.loads(raw) if isinstance(raw, str) else raw
            notes = journal.get("notes", [])
            notes.append({"timestamp": datetime.now().isoformat(), "text": note})
            journal["notes"] = notes
            db.table("positions").update({"journal": json.dumps(journal)}).eq("id", pos_id).execute()
            return
        except Exception:
            pass

    positions = _load_local()
    for p in positions:
        if p["id"] == pos_id:
            j = p.setdefault("journal", {"entry_thesis": "", "exit_thesis": "", "tags": [], "notes": []})
            j["notes"].append({"timestamp": datetime.now().isoformat(), "text": note})
            break
    _save_local(positions)
