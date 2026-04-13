"""
=============================================================================
Dhan API v2 — Client Wrapper
Balfund Trading Private Limited
=============================================================================
Handles all HTTP communication with Dhan API v2.
Option chain, order placement, position fetch, instrument master.
=============================================================================
"""

import time
import logging
import requests
import pandas as pd
from io import StringIO
from typing import Optional, Dict, Any, List

from config import (
    DHAN_BASE_URL, INSTRUMENT_CSV_URL,
    NIFTY_SCRIP_ID, NIFTY_SEG, NIFTY_LOT_DEFAULT,
    OPTION_CHAIN_RATE_LIMIT_SEC,
)

log = logging.getLogger("DhanClient")


class DhanClient:
    """Thin wrapper around Dhan API v2 REST endpoints."""

    def __init__(self, client_id: str, access_token: str, logger=None):
        self.client_id    = str(client_id).strip()
        self.access_token = str(access_token).strip()
        self.logger       = logger or log
        self._headers     = {
            "Content-Type": "application/json",
            "access-token": self.access_token,
            "client-id":    self.client_id,
        }
        self._last_oc_call = 0.0  # rate limit tracker for option chain

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get(self, path: str, timeout: int = 15) -> Dict[str, Any]:
        r = requests.get(f"{DHAN_BASE_URL}{path}", headers=self._headers, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict, timeout: int = 15) -> Dict[str, Any]:
        r = requests.post(f"{DHAN_BASE_URL}{path}", json=body,
                          headers=self._headers, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def _put(self, path: str, body: dict, timeout: int = 15) -> Dict[str, Any]:
        r = requests.put(f"{DHAN_BASE_URL}{path}", json=body,
                         headers=self._headers, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def _delete(self, path: str, timeout: int = 15) -> bool:
        r = requests.delete(f"{DHAN_BASE_URL}{path}",
                            headers=self._headers, timeout=timeout)
        return r.status_code in (200, 202)

    def _corr_id(self) -> str:
        return f"ois{int(time.time() * 1000) % (10 ** 13)}"[:20]

    # ── Option Chain ──────────────────────────────────────────────────────────

    def get_option_chain(self, expiry: str) -> Dict[str, Any]:
        """
        Fetch full NIFTY option chain for given expiry.
        Respects 3-second rate limit automatically.

        Args:
            expiry: "YYYY-MM-DD" format

        Returns:
            Raw API response dict with 'data.last_price' and 'data.oc'
        """
        elapsed = time.time() - self._last_oc_call
        if elapsed < OPTION_CHAIN_RATE_LIMIT_SEC:
            time.sleep(OPTION_CHAIN_RATE_LIMIT_SEC - elapsed)

        body = {
            "UnderlyingScrip": NIFTY_SCRIP_ID,
            "UnderlyingSeg":   NIFTY_SEG,
            "Expiry":          expiry,
        }
        self.logger.info("Fetching option chain for expiry=%s", expiry)
        resp = self._post("/optionchain", body)
        self._last_oc_call = time.time()
        return resp

    def get_expiry_list(self) -> List[str]:
        """
        Fetch all active expiry dates for NIFTY options.

        Returns:
            List of "YYYY-MM-DD" strings, ascending order.
        """
        body = {
            "UnderlyingScrip": NIFTY_SCRIP_ID,
            "UnderlyingSeg":   NIFTY_SEG,
        }
        self.logger.info("Fetching NIFTY expiry list...")
        resp = self._post("/optionchain/expirylist", body)
        if resp.get("status") == "success":
            return sorted(resp.get("data", []))
        self.logger.warning("Expiry list fetch failed: %s", resp)
        return []

    # ── Orders ────────────────────────────────────────────────────────────────

    def place_order(self,
                    transaction_type: str,
                    security_id: str,
                    exchange_segment: str,
                    quantity: int,
                    order_type: str,
                    price: float = 0.0,
                    trigger_price: float = 0.0,
                    product_type: str = "MARGIN",
                    correlation_id: Optional[str] = None) -> Dict[str, Any]:
        """Place an order. Returns full Dhan order response."""
        body = {
            "dhanClientId":      self.client_id,
            "correlationId":     correlation_id or self._corr_id(),
            "transactionType":   transaction_type.upper(),
            "exchangeSegment":   exchange_segment,
            "productType":       product_type,
            "orderType":         order_type.upper(),
            "validity":          "DAY",
            "securityId":        str(security_id),
            "quantity":          int(quantity),
            "disclosedQuantity": 0,
            "price":             float(price),
            "triggerPrice":      float(trigger_price),
            "afterMarketOrder":  False,
            "amoTime":           "",
        }
        resp = self._post("/orders", body)
        self.logger.info("Order placed → type=%s secId=%s qty=%d orderId=%s status=%s",
                         transaction_type, security_id, quantity,
                         resp.get("orderId"), resp.get("orderStatus"))
        return resp

    def modify_order(self, order_id: str, order_type: str,
                     quantity: int, price: float,
                     trigger_price: float = 0.0,
                     validity: str = "DAY") -> Dict[str, Any]:
        """Modify an existing open order."""
        body = {
            "dhanClientId":  self.client_id,
            "orderType":     order_type.upper(),
            "quantity":      int(quantity),
            "price":         float(price),
            "triggerPrice":  float(trigger_price),
            "validity":      validity,
        }
        return self._put(f"/orders/{order_id}", body)

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order. Returns True if successful."""
        try:
            return self._delete(f"/orders/{order_id}")
        except Exception as e:
            self.logger.warning("Cancel failed for %s: %s", order_id, e)
            return False

    def get_order_status(self, order_id: str) -> Dict[str, Any]:
        """Get current status of a specific order."""
        try:
            return self._get(f"/orders/{order_id}")
        except Exception as e:
            self.logger.warning("Status poll failed for %s: %s", order_id, e)
            return {}

    def get_all_orders(self) -> List[Dict[str, Any]]:
        """Get all orders for today."""
        try:
            result = self._get("/orders")
            return result if isinstance(result, list) else []
        except Exception as e:
            self.logger.warning("get_all_orders failed: %s", e)
            return []

    # ── Positions & Funds ─────────────────────────────────────────────────────

    def get_positions(self) -> List[Dict[str, Any]]:
        """Get all open positions."""
        try:
            result = self._get("/positions")
            return result if isinstance(result, list) else []
        except Exception as e:
            self.logger.warning("get_positions failed: %s", e)
            return []

    def get_funds(self) -> Dict[str, Any]:
        """Get available funds / margin info."""
        try:
            return self._get("/funds")
        except Exception as e:
            self.logger.warning("get_funds failed: %s", e)
            return {}

    # ── Instrument Master ─────────────────────────────────────────────────────

    def fetch_nifty_lot_size(self) -> int:
        """
        Fetch NIFTY options lot size from instrument master CSV.
        Returns NIFTY_LOT_DEFAULT if unavailable.
        """
        try:
            self.logger.info("Fetching instrument master for lot size...")
            r = requests.get(INSTRUMENT_CSV_URL, timeout=30)
            r.raise_for_status()
            df = pd.read_csv(StringIO(r.text), low_memory=False)
            for col in ["EXCH_ID", "INSTRUMENT", "SYMBOL_NAME"]:
                if col in df.columns:
                    df[col] = df[col].astype(str).str.strip().str.upper()

            nifty = df[
                (df["SYMBOL_NAME"] == "NIFTY") &
                (df["INSTRUMENT"] == "OPTIDX") &
                (df["EXCH_ID"] == "NSE")
            ]
            if not nifty.empty and "LOT_SIZE" in nifty.columns:
                lot = nifty.iloc[0]["LOT_SIZE"]
                lot_int = int(float(lot)) if pd.notna(lot) and float(lot) > 0 else NIFTY_LOT_DEFAULT
                self.logger.info("NIFTY lot size fetched: %d", lot_int)
                return lot_int
            self.logger.warning("NIFTY not found in instrument master, using default %d", NIFTY_LOT_DEFAULT)
            return NIFTY_LOT_DEFAULT
        except Exception as e:
            self.logger.warning("Lot size fetch failed: %s — using default %d", e, NIFTY_LOT_DEFAULT)
            return NIFTY_LOT_DEFAULT
