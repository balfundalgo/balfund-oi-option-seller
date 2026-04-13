"""
=============================================================================
Order Executor
Balfund Trading Private Limited
=============================================================================
Handles spread order placement (sell main leg + buy hedge leg) and closing.
Supports LIMIT (with MARKET fallback) and direct MARKET orders.
Product type: MARGIN (NRML equivalent for positional F&O on Dhan).
=============================================================================
"""

import time
import logging
from typing import Optional, Dict, Any, Tuple

from config import (
    PRODUCT_TYPE, LIMIT_OFFSET, MARKET_FALLBACK_SEC,
)

log = logging.getLogger("OrderExecutor")

EXCHANGE_SEGMENT = "NSE_FNO"


class OrderExecutor:
    """Executes spread orders (sell + hedge) for the OI option seller."""

    def __init__(self, dhan_client, live_mode: bool = False,
                 order_type: str = "LIMIT", limit_offset: float = LIMIT_OFFSET,
                 logger=None):
        self.dhan         = dhan_client
        self.live_mode    = live_mode
        self.order_type   = order_type.upper()   # "LIMIT" or "MARKET"
        self.limit_offset = float(limit_offset)
        self.logger       = logger or log

    # ── Internal order wrapper ────────────────────────────────────────────────

    def _execute(
        self,
        transaction_type: str,
        security_id: str,
        quantity: int,
        ref_price: float,
        label: str = "",
    ) -> Tuple[bool, float, str]:
        """
        Place order with LIMIT + MARKET fallback if not filled in time.

        Returns: (success, filled_price, order_id)
        """
        if not self.live_mode:
            self.logger.info("[PAPER] %s %s qty=%d price=%.2f", transaction_type, label, quantity, ref_price)
            return True, ref_price, f"PAPER_{int(time.time())}"

        # Determine limit price
        if self.order_type == "LIMIT":
            if transaction_type.upper() == "SELL":
                limit_price = round(ref_price - self.limit_offset, 2)   # accept slightly lower
            else:
                limit_price = round(ref_price + self.limit_offset, 2)   # accept slightly higher
        else:
            limit_price = 0.0

        order_type_api = "LIMIT" if self.order_type == "LIMIT" else "MARKET"

        try:
            resp = self.dhan.place_order(
                transaction_type = transaction_type,
                security_id      = security_id,
                exchange_segment = EXCHANGE_SEGMENT,
                quantity         = quantity,
                order_type       = order_type_api,
                price            = limit_price,
                product_type     = PRODUCT_TYPE,
            )
            order_id = resp.get("orderId", "")
            if not order_id:
                self.logger.error("No orderId returned: %s", resp)
                return False, 0.0, ""

            # Poll for fill
            deadline = time.time() + MARKET_FALLBACK_SEC
            while time.time() < deadline:
                time.sleep(2)
                status    = self.dhan.get_order_status(order_id)
                os_val    = str(status.get("orderStatus", "")).upper()
                filled_q  = int(status.get("filledQty") or 0)
                avg_price = float(status.get("averageTradedPrice") or 0)
                if os_val == "TRADED" or filled_q >= quantity:
                    fill_px = avg_price if avg_price > 0 else limit_price
                    self.logger.info("%s filled: orderId=%s avg=%.2f", label, order_id, fill_px)
                    return True, fill_px, order_id
                if os_val in ("REJECTED", "CANCELLED", "EXPIRED"):
                    self.logger.warning("%s order %s — %s", label, order_id, os_val)
                    break

            # LIMIT not filled → cancel and retry with MARKET
            if order_type_api == "LIMIT":
                self.logger.warning("%s LIMIT order not filled in %ds — falling back to MARKET",
                                    label, MARKET_FALLBACK_SEC)
                self.dhan.cancel_order(order_id)
                time.sleep(0.5)
                mkt_resp = self.dhan.place_order(
                    transaction_type = transaction_type,
                    security_id      = security_id,
                    exchange_segment = EXCHANGE_SEGMENT,
                    quantity         = quantity,
                    order_type       = "MARKET",
                    product_type     = PRODUCT_TYPE,
                )
                mkt_id = mkt_resp.get("orderId", "")
                time.sleep(1.5)
                mkt_status = self.dhan.get_order_status(mkt_id)
                mkt_price  = float(mkt_status.get("averageTradedPrice") or ref_price)
                self.logger.info("%s MARKET filled at %.2f", label, mkt_price)
                return True, mkt_price, mkt_id

            return False, 0.0, ""

        except Exception as e:
            self.logger.error("Order execution error [%s]: %s", label, e)
            return False, 0.0, ""

    # ── Spread Entry: Sell main + Buy hedge ───────────────────────────────────

    def place_sell_spread(
        self,
        sell_security_id: str,
        sell_ref_price: float,
        hedge_security_id: str,
        hedge_ref_price: float,
        quantity: int,
        label: str = "",
    ) -> Dict[str, Any]:
        """
        Execute the full spread:
          1. SELL the main option leg
          2. BUY the hedge leg

        Returns dict with fill info for both legs.
        """
        self.logger.info(
            "=== PLACING SELL SPREAD %s | qty=%d | sell_price=%.2f | hedge_price=%.2f ===",
            label, quantity, sell_ref_price, hedge_ref_price,
        )

        # 1. Sell main leg
        sell_ok, sell_fill, sell_oid = self._execute(
            "SELL", str(sell_security_id), quantity, sell_ref_price,
            label=f"SELL_{label}",
        )

        if not sell_ok:
            self.logger.error("SELL leg failed — not placing hedge")
            return {"success": False, "reason": "SELL_LEG_FAILED"}

        time.sleep(0.5)

        # 2. Buy hedge leg
        hedge_ok, hedge_fill, hedge_oid = self._execute(
            "BUY", str(hedge_security_id), quantity, hedge_ref_price,
            label=f"HEDGE_BUY_{label}",
        )

        if not hedge_ok:
            self.logger.error("HEDGE BUY failed after SELL was placed — manual intervention needed!")

        return {
            "success":          sell_ok,
            "sell_fill_price":  sell_fill,
            "sell_order_id":    sell_oid,
            "hedge_fill_price": hedge_fill if hedge_ok else 0.0,
            "hedge_order_id":   hedge_oid  if hedge_ok else "",
            "hedge_ok":         hedge_ok,
        }

    # ── Spread Exit: Buy back main + Close hedge ──────────────────────────────

    def close_sell_spread(
        self,
        sell_security_id: str,
        sell_ref_price: float,
        hedge_security_id: str,
        hedge_ref_price: float,
        quantity: int,
        label: str = "",
    ) -> Dict[str, Any]:
        """
        Exit the full spread:
          1. BUY BACK the sold main option (at market/limit)
          2. SELL the hedge option to close it

        Returns dict with fill info for both legs.
        """
        self.logger.info(
            "=== CLOSING SPREAD %s | qty=%d | buyback_price=%.2f | hedge_close_price=%.2f ===",
            label, quantity, sell_ref_price, hedge_ref_price,
        )

        # 1. Buy back main leg
        close_ok, close_fill, close_oid = self._execute(
            "BUY", str(sell_security_id), quantity, sell_ref_price,
            label=f"BUYBACK_{label}",
        )

        time.sleep(0.5)

        # 2. Close hedge (sell it back)
        hedge_ok, hedge_fill, hedge_oid = self._execute(
            "SELL", str(hedge_security_id), quantity, hedge_ref_price,
            label=f"HEDGE_CLOSE_{label}",
        )

        net_pnl = 0.0
        if close_ok:
            # P&L for the spread trade (approximate, per lot)
            # We'll compute actual P&L in main.py using stored entry prices
            pass

        return {
            "success":          close_ok,
            "close_fill_price": close_fill,
            "close_order_id":   close_oid,
            "hedge_fill_price": hedge_fill if hedge_ok else 0.0,
            "hedge_order_id":   hedge_oid  if hedge_ok else "",
        }

    # ── Single Leg Close (used when SL already triggered main leg) ───────────

    def close_single_leg(
        self,
        security_id: str,
        ref_price: float,
        quantity: int,
        transaction_type: str,  # "BUY" or "SELL"
        label: str = "",
    ) -> Tuple[bool, float, str]:
        """Close a single leg (e.g., hedge leg after SL triggered on main)."""
        return self._execute(transaction_type, str(security_id), quantity, ref_price, label)
