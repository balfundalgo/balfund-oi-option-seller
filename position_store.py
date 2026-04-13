"""
=============================================================================
Position Store
Balfund Trading Private Limited
=============================================================================
JSON-backed persistent storage for:
  - Open positions (CE and PE legs with their hedge)
  - 3:30 PM daily snapshots for alt-exit method
  - Daily job tracking (which scheduled jobs fired today)

Survives app restarts — reads from JSON on init.
=============================================================================
"""

import json
import logging
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger("PositionStore")

# Keys for position legs
LEG_CE = "ce"
LEG_PE = "pe"


class PositionStore:
    """
    Thread-safe JSON store for positions and daily snapshots.

    Position structure per leg:
    {
      "strike":          25000,
      "security_id":     42500,
      "hedge_strike":    25100,
      "hedge_sec_id":    42501,
      "sold_premium":    245.0,
      "hedge_premium":   70.0,
      "lot_size":        75,
      "lots":            1,
      "expiry":          "2026-04-24",
      "entry_time":      "2026-04-14 15:20:00",
      "sl_price":        338.6,
      "sl_order_id":     "1234567890",
      "trail_triggered": false,
      "opt_type":        "ce"
    }

    Snapshot structure:
    {
      "date": "2026-04-14",
      "ce": { "<strike>": { "premium": 245.0, "oi": 5000000 } },
      "pe": { "<strike>": { "premium": 220.0, "oi": 4000000 } }
    }
    """

    def __init__(self, store_path: Optional[Path] = None, logger=None):
        self.logger = logger or log
        if store_path is None:
            import sys
            if getattr(sys, "frozen", False):
                base = Path(sys.executable).parent
            else:
                base = Path(__file__).resolve().parent
            store_path = base / "positions.json"
        self.path = Path(store_path)
        self._lock = threading.Lock()
        self._data = self._load()

    # ── Load / Save ───────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception as e:
                self.logger.warning("Could not read position store: %s", e)
        return {
            "positions":    {LEG_CE: None, LEG_PE: None},
            "snapshot":     None,
            "jobs_fired":   {},   # date_str → list of job names fired today
        }

    def _save(self):
        try:
            self.path.write_text(
                json.dumps(self._data, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as e:
            self.logger.error("Could not save position store: %s", e)

    # ── Positions ─────────────────────────────────────────────────────────────

    def get_position(self, leg: str) -> Optional[Dict[str, Any]]:
        """Get CE or PE position dict, or None if no open position."""
        with self._lock:
            return self._data["positions"].get(leg)

    def set_position(self, leg: str, position_data: Dict[str, Any]):
        """Save a new open position for CE or PE leg."""
        with self._lock:
            self._data["positions"][leg] = position_data
            self._save()
        self.logger.info("Position saved: %s %s strike=%s entry=%.2f",
                         leg.upper(),
                         position_data.get("opt_type", "").upper(),
                         position_data.get("strike"),
                         position_data.get("sold_premium", 0))

    def update_position(self, leg: str, **kwargs):
        """Update specific fields in an existing position."""
        with self._lock:
            pos = self._data["positions"].get(leg)
            if pos is None:
                return
            pos.update(kwargs)
            self._data["positions"][leg] = pos
            self._save()

    def clear_position(self, leg: str):
        """Remove a position after it's been closed."""
        with self._lock:
            self._data["positions"][leg] = None
            self._save()
        self.logger.info("Position cleared: %s", leg.upper())

    def has_position(self, leg: str) -> bool:
        """Returns True if there is an open position for this leg."""
        with self._lock:
            return self._data["positions"].get(leg) is not None

    def any_position_open(self) -> bool:
        """Returns True if either CE or PE position is open."""
        with self._lock:
            return any(v is not None for v in self._data["positions"].values())

    def get_all_positions(self) -> Dict[str, Optional[Dict[str, Any]]]:
        """Return dict of all positions."""
        with self._lock:
            return dict(self._data["positions"])

    # ── SL Order Tracking ─────────────────────────────────────────────────────

    def update_sl_order_id(self, leg: str, order_id: str, sl_price: float):
        """Update the SL order ID and price after placing/replacing SL."""
        self.update_position(leg, sl_order_id=order_id, sl_price=sl_price)
        self.logger.info("SL order updated: %s | orderId=%s | sl_price=%.2f",
                         leg.upper(), order_id, sl_price)

    def mark_trail_triggered(self, leg: str):
        """Mark that trailing SL has been triggered (cost-to-cost)."""
        self.update_position(leg, trail_triggered=True)
        self.logger.info("Trail SL triggered for %s — moved to cost", leg.upper())

    # ── Daily Snapshot (for alt-exit) ─────────────────────────────────────────

    def save_snapshot(self, snapshot: Dict[str, Any]):
        """
        Save 3:30 PM daily snapshot for alt-exit calculation.

        snapshot = {
          "date": "YYYY-MM-DD",
          "ce": { "<strike>": { "premium": float, "oi": int } },
          "pe": { "<strike>": { "premium": float, "oi": int } }
        }
        """
        with self._lock:
            self._data["snapshot"] = snapshot
            self._save()
        self.logger.info("Snapshot saved for %s", snapshot.get("date"))

    def get_snapshot(self) -> Optional[Dict[str, Any]]:
        """Get last saved daily snapshot."""
        with self._lock:
            return self._data.get("snapshot")

    def get_snapshot_for_leg(self, leg: str, strike: float) -> Optional[Dict[str, Any]]:
        """
        Get snapshot data for a specific leg and strike.
        Returns {"premium": float, "oi": int} or None.
        """
        with self._lock:
            snap = self._data.get("snapshot")
            if not snap:
                return None
            leg_snap = snap.get(leg, {})
            return leg_snap.get(str(int(strike)))

    # ── Job Deduplication ─────────────────────────────────────────────────────

    def mark_job_fired(self, job_name: str, for_date: Optional[str] = None):
        """Record that a scheduled job has run today."""
        date_str = for_date or date.today().isoformat()
        with self._lock:
            if "jobs_fired" not in self._data:
                self._data["jobs_fired"] = {}
            if date_str not in self._data["jobs_fired"]:
                self._data["jobs_fired"][date_str] = []
            if job_name not in self._data["jobs_fired"][date_str]:
                self._data["jobs_fired"][date_str].append(job_name)
            self._save()

    def job_fired_today(self, job_name: str) -> bool:
        """Returns True if this job has already run today."""
        date_str = date.today().isoformat()
        with self._lock:
            return job_name in self._data.get("jobs_fired", {}).get(date_str, [])

    def reset_jobs_for_today(self):
        """Clear job-fired records for today (call if you want to re-run jobs)."""
        date_str = date.today().isoformat()
        with self._lock:
            if "jobs_fired" in self._data:
                self._data["jobs_fired"][date_str] = []
            self._save()

    # ── Debug ─────────────────────────────────────────────────────────────────

    def summary(self) -> str:
        """One-line summary of current state."""
        with self._lock:
            ce  = self._data["positions"].get(LEG_CE)
            pe  = self._data["positions"].get(LEG_PE)
            snap = self._data.get("snapshot")
            ce_str  = f"CE:{ce['strike']}@{ce['sold_premium']:.0f}" if ce else "CE:None"
            pe_str  = f"PE:{pe['strike']}@{pe['sold_premium']:.0f}" if pe else "PE:None"
            sn_str  = f"snap:{snap['date']}" if snap else "snap:None"
            return f"{ce_str} | {pe_str} | {sn_str}"
