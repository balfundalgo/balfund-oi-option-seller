"""
=============================================================================
SL Manager
Balfund Trading Private Limited
=============================================================================
Manages stop-loss orders for sold option positions.

Rules:
  - Initial SL = sold_premium × 1.382 (38.2% above entry price)
  - SL placed as STOP_LOSS_MARKET BUY order every morning at 9:30 AM
  - Trail: when current premium ≤ 50% of sold_premium → move SL to entry price
  - Gap breach: if LTP > SL price at 9:30 AM opening → close immediately
  - If SL order triggered overnight → close hedge leg at 9:30 AM
=============================================================================
"""

import logging
import time
from typing import Optional, Dict, Any

from config import SL_MULTIPLIER, TRAIL_TRIGGER_PCT, PRODUCT_TYPE

log = logging.getLogger("SLManager")


class SLManager:
    """Manages SL orders for the OI option seller."""

    def __init__(self, dhan_client, position_store, live_mode: bool = False, logger=None):
        self.dhan          = dhan_client
        self.store         = position_store
        self.live_mode     = live_mode
        self.logger        = logger or log

    # ── SL Price Computation ─────────────────────────────────────────────────

    def compute_sl_price(self, sold_premium: float) -> float:
        """SL = sold_premium × 1.382, rounded to 2 decimal places."""
        return round(sold_premium * SL_MULTIPLIER, 2)

    def compute_trail_sl_price(self, sold_premium: float) -> float:
        """Trail SL = sold_premium (cost-to-cost, i.e., entry price)."""
        return round(sold_premium, 2)

    def should_trail(self, position: Dict[str, Any], current_premium: float) -> bool:
        """
        Returns True if trail condition is met:
        current_premium ≤ sold_premium × 50%
        and trail has NOT already been triggered.
        """
        if position.get("trail_triggered"):
            return False
        threshold = position["sold_premium"] * TRAIL_TRIGGER_PCT
        return current_premium <= threshold

    # ── Gap Breach Check ─────────────────────────────────────────────────────

    def check_gap_breach(self, position: Dict[str, Any], current_premium: float) -> bool:
        """
        Returns True if LTP already exceeds SL price at 9:30 AM opening.
        When this happens, we must close immediately without waiting.
        """
        sl_price = float(position.get("sl_price", 0))
        if sl_price <= 0:
            sl_price = self.compute_sl_price(float(position["sold_premium"]))
        is_breach = current_premium >= sl_price
        if is_breach:
            self.logger.warning(
                "⚠️ GAP BREACH detected: current_premium=%.2f >= sl_price=%.2f for %s %d",
                current_premium, sl_price,
                position.get("opt_type", "").upper(),
                int(position.get("strike", 0)),
            )
        return is_breach

    # ── SL Order Triggered Check ──────────────────────────────────────────────

    def check_sl_triggered(self, sl_order_id: str) -> bool:
        """
        Check if the SL order was triggered (TRADED status).
        Returns True if SL was hit.
        """
        if not sl_order_id:
            return False
        try:
            status = self.dhan.get_order_status(sl_order_id)
            order_status = str(status.get("orderStatus", "")).upper()
            filled_qty   = int(status.get("filledQty") or 0)
            is_traded    = order_status == "TRADED" or filled_qty > 0
            if is_traded:
                avg = float(status.get("averageTradedPrice") or 0)
                self.logger.info(
                    "SL order %s TRIGGERED at avg_price=%.2f", sl_order_id, avg)
            return is_traded
        except Exception as e:
            self.logger.warning("SL status check failed for %s: %s", sl_order_id, e)
            return False

    # ── Place SL Order ────────────────────────────────────────────────────────

    def place_sl_order(self, position: Dict[str, Any]) -> Optional[str]:
        """
        Place a STOP_LOSS_MARKET BUY order for the sold leg.
        (When triggered, it buys back the sold option at market price.)

        Returns: order_id string or None if failed / paper mode
        """
        sl_price   = float(position.get("sl_price") or self.compute_sl_price(position["sold_premium"]))
        security_id = str(position["security_id"])
        quantity    = int(position["lot_size"]) * int(position.get("lots", 1))
        opt_type    = position.get("opt_type", "option").upper()
        strike      = int(position.get("strike", 0))

        self.logger.info(
            "Placing SL order: BUY %s %d | qty=%d | trigger=%.2f | live=%s",
            opt_type, strike, quantity, sl_price, self.live_mode,
        )

        if not self.live_mode:
            self.logger.info("[PAPER] SL order simulated at trigger=%.2f", sl_price)
            return f"PAPER_SL_{int(time.time())}"

        try:
            resp = self.dhan.place_order(
                transaction_type  = "BUY",
                security_id       = security_id,
                exchange_segment  = "NSE_FNO",
                quantity          = quantity,
                order_type        = "STOP_LOSS_MARKET",
                trigger_price     = sl_price,
                product_type      = PRODUCT_TYPE,
            )
            order_id = resp.get("orderId", "")
            if order_id:
                self.logger.info("SL order placed: orderId=%s trigger=%.2f", order_id, sl_price)
                return order_id
            self.logger.error("SL order returned no orderId: %s", resp)
            return None
        except Exception as e:
            self.logger.error("SL order placement failed: %s", e)
            return None

    def cancel_sl_order(self, sl_order_id: str) -> bool:
        """Cancel an existing SL order (e.g., before replacing with updated SL)."""
        if not sl_order_id or sl_order_id.startswith("PAPER_"):
            return True
        self.logger.info("Cancelling SL order: %s", sl_order_id)
        return self.dhan.cancel_order(sl_order_id)

    # ── Morning SL Routine ────────────────────────────────────────────────────

    def morning_sl_routine(
        self,
        leg: str,
        position: Dict[str, Any],
        current_premium: float,
        close_spread_fn,
    ) -> str:
        """
        Full 9:30 AM SL management for one leg.
        Returns event description string.

        Steps:
          1. Check if existing SL order was already triggered overnight
          2. Check gap breach at opening
          3. Check trail condition
          4. Cancel old SL order and place fresh one with updated price
        """
        events = []

        # Step 1: Check if SL was already triggered overnight
        old_sl_id = position.get("sl_order_id", "")
        if old_sl_id and self.check_sl_triggered(old_sl_id):
            self.logger.warning("SL was triggered overnight for %s — closing hedge", leg.upper())
            close_spread_fn(leg, position, reason="SL_TRIGGERED_OVERNIGHT", close_main=False)
            return "SL_TRIGGERED_OVERNIGHT → hedge closed"

        # Step 2: Check gap breach
        if self.check_gap_breach(position, current_premium):
            close_spread_fn(leg, position, reason="GAP_BREACH")
            return f"GAP_BREACH at ₹{current_premium:.2f} — position closed"

        # Step 3: Check trail
        sold_premium = float(position["sold_premium"])
        trail_triggered = position.get("trail_triggered", False)
        new_sl_price = float(position.get("sl_price") or self.compute_sl_price(sold_premium))

        if not trail_triggered and self.should_trail(position, current_premium):
            new_sl_price = self.compute_trail_sl_price(sold_premium)
            self.store.mark_trail_triggered(leg)
            events.append(f"TRAIL → SL moved to ₹{new_sl_price:.2f} (cost-to-cost)")
            self.logger.info("Trail triggered for %s — new SL=%.2f", leg.upper(), new_sl_price)
        else:
            # Ensure SL is at correct price (in case it changed from re-entry)
            expected_sl = (
                self.compute_trail_sl_price(sold_premium) if trail_triggered
                else self.compute_sl_price(sold_premium)
            )
            new_sl_price = expected_sl

        # Step 4: Cancel old SL, place new one
        if old_sl_id:
            self.cancel_sl_order(old_sl_id)
            time.sleep(0.5)

        self.store.update_position(leg, sl_price=new_sl_price)
        new_sl_id = self.place_sl_order({**position, "sl_price": new_sl_price})

        if new_sl_id:
            self.store.update_sl_order_id(leg, new_sl_id, new_sl_price)
            events.append(f"SL placed @ ₹{new_sl_price:.2f} (order {new_sl_id[:12]})")
        else:
            events.append("SL placement FAILED — check manually!")

        return " | ".join(events) if events else f"SL refreshed @ ₹{new_sl_price:.2f}"
