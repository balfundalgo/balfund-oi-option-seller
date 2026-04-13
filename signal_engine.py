"""
=============================================================================
Signal Engine
Balfund Trading Private Limited
=============================================================================
Implements the core signal logic from the strategy document.

Primary Signal (OI Method):
  Compare current 3:20 PM values vs previous day values.
  Price↓ + OI↑ → SHORT_BUILDUP  → SELL
  Price↓ + OI↓ → LONG_UNWINDING → SELL
  Price↑ + OI↑ → LONG_BUILDUP   → EXIT
  Price↑ + OI↓ → SHORT_COVERING → EXIT

Alt Exit (Premium Difference Method) — Section 8:
  Premium difference 1-10 points + OI diff > 1 → EXIT

Key insight: Dhan option chain API provides previous_close_price and
previous_oi directly — no extra snapshot needed for primary signal!
=============================================================================
"""

import logging
from enum import Enum
from typing import Optional, Dict, Any

from config import (
    ALT_EXIT_PREM_DIFF_MIN, ALT_EXIT_PREM_DIFF_MAX, ALT_EXIT_OI_DIFF_MIN,
)

log = logging.getLogger("SignalEngine")


class Signal(str, Enum):
    SHORT_BUILDUP  = "SHORT_BUILDUP"   # Price↓ OI↑ → SELL
    LONG_UNWINDING = "LONG_UNWINDING"  # Price↓ OI↓ → SELL
    LONG_BUILDUP   = "LONG_BUILDUP"    # Price↑ OI↑ → EXIT
    SHORT_COVERING = "SHORT_COVERING"  # Price↑ OI↓ → EXIT
    NEUTRAL        = "NEUTRAL"         # No directional signal
    UNKNOWN        = "UNKNOWN"         # Insufficient data


def is_entry_signal(sig: Signal) -> bool:
    """Returns True if signal indicates we should SELL the option."""
    return sig in (Signal.SHORT_BUILDUP, Signal.LONG_UNWINDING)


def is_exit_signal(sig: Signal) -> bool:
    """Returns True if signal indicates we should EXIT (buy back) the option."""
    return sig in (Signal.LONG_BUILDUP, Signal.SHORT_COVERING)


def compute_signal(leg_data: Optional[Dict[str, Any]], logger=None) -> Signal:
    """
    Compute the primary OI signal for a single CE or PE leg.

    Uses data directly from Dhan option chain API:
      - leg_data["last_price"]           → current premium at 3:20 PM
      - leg_data["previous_close_price"] → previous day close (3:30 PM)
      - leg_data["oi"]                   → current OI
      - leg_data["previous_oi"]          → previous day OI

    Args:
        leg_data: Dict from option_chain.fetch_and_parse() for a single leg
        logger:   Optional logger

    Returns:
        Signal enum value
    """
    lgr = logger or log

    if not leg_data:
        return Signal.UNKNOWN

    curr_price = float(leg_data.get("last_price", 0) or 0)
    prev_price = float(leg_data.get("previous_close_price", 0) or 0)
    curr_oi    = int(leg_data.get("oi", 0) or 0)
    prev_oi    = int(leg_data.get("previous_oi", 0) or 0)

    if prev_price <= 0 or prev_oi <= 0:
        lgr.warning("Insufficient prev data: prev_price=%.2f prev_oi=%d", prev_price, prev_oi)
        return Signal.UNKNOWN

    price_up = curr_price > prev_price
    price_dn = curr_price < prev_price
    oi_up    = curr_oi > prev_oi
    oi_dn    = curr_oi < prev_oi

    lgr.debug(
        "Signal check: curr_price=%.2f prev_price=%.2f (%.2f%%) | "
        "curr_oi=%d prev_oi=%d (%.1f%%)",
        curr_price, prev_price, (curr_price - prev_price) / prev_price * 100,
        curr_oi, prev_oi, (curr_oi - prev_oi) / prev_oi * 100,
    )

    if price_dn and oi_up:
        sig = Signal.SHORT_BUILDUP
    elif price_dn and oi_dn:
        sig = Signal.LONG_UNWINDING
    elif price_up and oi_up:
        sig = Signal.LONG_BUILDUP
    elif price_up and oi_dn:
        sig = Signal.SHORT_COVERING
    else:
        sig = Signal.NEUTRAL

    lgr.info(
        "Signal: %s | price %.2f→%.2f | OI %d→%d",
        sig.value, prev_price, curr_price, prev_oi, curr_oi,
    )
    return sig


def compute_alt_exit(
    curr_price: float,
    curr_oi: int,
    snapshot_price: float,   # from 3:30 PM snapshot stored the day before
    snapshot_oi: int,
    logger=None,
) -> bool:
    """
    Alt exit signal (Premium Difference Method, Section 8):
      Exit if:
        - |snapshot_price - curr_price| is in range [1, 10]
        - |curr_oi - snapshot_oi| > 1

    Args:
        curr_price:     Current premium at 3:20 PM
        curr_oi:        Current OI at 3:20 PM
        snapshot_price: Previous day 3:30 PM premium (from position_store snapshot)
        snapshot_oi:    Previous day 3:30 PM OI (from position_store snapshot)
        logger:         Optional logger

    Returns:
        True if alt exit conditions are met
    """
    lgr = logger or log

    if snapshot_price <= 0 or snapshot_oi <= 0:
        return False  # No snapshot available

    prem_diff = abs(snapshot_price - curr_price)
    oi_diff   = abs(curr_oi - snapshot_oi)

    alt_exit = (
        ALT_EXIT_PREM_DIFF_MIN <= prem_diff <= ALT_EXIT_PREM_DIFF_MAX
        and oi_diff > ALT_EXIT_OI_DIFF_MIN
    )

    lgr.info(
        "Alt exit check: prem_diff=%.2f (range %d-%d) | oi_diff=%d (min >%d) → %s",
        prem_diff, ALT_EXIT_PREM_DIFF_MIN, ALT_EXIT_PREM_DIFF_MAX,
        oi_diff, ALT_EXIT_OI_DIFF_MIN,
        "EXIT" if alt_exit else "HOLD",
    )
    return alt_exit


def signal_label(sig: Signal) -> str:
    """Human-readable signal label with action."""
    labels = {
        Signal.SHORT_BUILDUP:  "⬇ SHORT BUILDUP  → SELL",
        Signal.LONG_UNWINDING: "⬇ LONG UNWINDING → SELL",
        Signal.LONG_BUILDUP:   "⬆ LONG BUILDUP   → EXIT",
        Signal.SHORT_COVERING: "⬆ SHORT COVERING → EXIT",
        Signal.NEUTRAL:        "─ NEUTRAL         → HOLD",
        Signal.UNKNOWN:        "? UNKNOWN         → SKIP",
    }
    return labels.get(sig, sig.value)
