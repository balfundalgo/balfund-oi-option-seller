"""
=============================================================================
Strike Selector
Balfund Trading Private Limited
=============================================================================
Selects the best strike for selling and the hedge strike.

Sell Strike Rules:
  - Delta: 0.25 – 0.33 (absolute value)
  - Premium: ₹150 – ₹300 (for selection), ₹200 – ₹300 (at entry)
  - Prefer multiples of 100
  - OTM only (CE above spot, PE below spot)

Hedge Strike Rules:
  - Same expiry as sold strike
  - Further OTM than sold strike
  - Premium: ₹50 – ₹90
  - Prefer the strike closest to ₹70 (middle of range)
=============================================================================
"""

import logging
from typing import Dict, Any, Optional, List, Tuple

from config import (
    DELTA_MIN, DELTA_MAX, STRIKE_MULTIPLE,
    SELECT_PREMIUM_MIN, SELECT_PREMIUM_MAX,
    SELL_PREMIUM_MIN, SELL_PREMIUM_MAX,
    HEDGE_PREMIUM_MIN, HEDGE_PREMIUM_MAX,
    MIN_NET_CREDIT,
)
# NOTE: The config values above are used as DEFAULTS only.
# All callers should pass explicit params from user settings.

log = logging.getLogger("StrikeSelector")


def _is_multiple_of(strike: float, multiple: int) -> bool:
    return abs(strike % multiple) < 0.01


def select_sell_strike(
    strikes: Dict[float, Any],
    opt_type: str,                 # "ce" or "pe"
    nifty_ltp: float,
    delta_min:     float = DELTA_MIN,
    delta_max:     float = DELTA_MAX,
    sell_prem_min: float = SELL_PREMIUM_MIN,
    sell_prem_max: float = SELL_PREMIUM_MAX,
    logger=None,
) -> Optional[Dict[str, Any]]:
    """
    Select the best OTM strike to sell.

    All range parameters are user-configurable via GUI (fall back to config.py defaults).

    Selection logic:
      1. Filter: delta in [delta_min, delta_max], premium in [150, sell_prem_max]
      2. OTM filter: CE strikes > spot, PE strikes < spot
      3. Prefer multiples of 100 (1st priority)
      4. Among qualifying, pick the one closest to mid-delta
         and whose premium is in [sell_prem_min, sell_prem_max]

    Returns:
        Dict with strike info or None if no suitable strike found.
    """
    lgr = logger or log
    ot  = opt_type.lower()

    # Use a slightly wider filter for initial scan (150 floor), then stricter at entry
    scan_prem_min = min(150.0, sell_prem_min)

    candidates: List[Dict[str, Any]] = []

    for strike, legs in strikes.items():
        leg = legs.get(ot)
        if leg is None:
            continue

        delta   = leg["delta"]       # already absolute value
        premium = leg["last_price"]

        # OTM filter
        if ot == "ce" and strike <= nifty_ltp:
            continue
        if ot == "pe" and strike >= nifty_ltp:
            continue

        # Delta filter (user-configurable)
        if not (delta_min <= delta <= delta_max):
            continue

        # Broad premium scan filter
        if not (scan_prem_min <= premium <= sell_prem_max):
            continue

        candidates.append({
            "strike":       strike,
            "security_id":  leg["security_id"],
            "premium":      premium,
            "delta":        delta,
            "iv":           leg["iv"],
            "bid":          leg["top_bid_price"],
            "ask":          leg["top_ask_price"],
            "oi":           leg["oi"],
            "prev_close":   leg["previous_close_price"],
            "prev_oi":      leg["previous_oi"],
            "is_multiple":  _is_multiple_of(strike, STRIKE_MULTIPLE),
            "opt_type":     ot,
        })

    if not candidates:
        lgr.warning("No %s candidates in delta %.2f-%.2f / prem ₹%.0f-₹%.0f for LTP=%.2f",
                    ot.upper(), delta_min, delta_max, scan_prem_min, sell_prem_max, nifty_ltp)
        return None

    # Sort: 1st preference — multiples of 100; 2nd — closest delta to midpoint
    TARGET_DELTA = (delta_min + delta_max) / 2
    candidates.sort(key=lambda x: (not x["is_multiple"], abs(x["delta"] - TARGET_DELTA)))

    # Find one whose premium meets the entry criteria (user-configured sell range)
    for c in candidates:
        if sell_prem_min <= c["premium"] <= sell_prem_max:
            lgr.info(
                "Sell strike selected: %s %d | premium=%.2f | delta=%.4f | secId=%d",
                ot.upper(), int(c["strike"]), c["premium"], c["delta"], c["security_id"]
            )
            return c

    # Best available even if outside sell range — log warning
    best = candidates[0]
    lgr.warning(
        "No %s strike in ₹%.0f-₹%.0f range; best: %d premium=%.2f (outside sell range)",
        ot.upper(), sell_prem_min, sell_prem_max, int(best["strike"]), best["premium"]
    )
    return None


def select_hedge_strike(
    strikes: Dict[float, Any],
    opt_type: str,
    sold_strike: float,
    nifty_ltp: float,
    hedge_prem_min: float = HEDGE_PREMIUM_MIN,
    hedge_prem_max: float = HEDGE_PREMIUM_MAX,
    logger=None,
) -> Optional[Dict[str, Any]]:
    """
    Select the hedge strike further OTM than sold_strike.
    Premium must be in [hedge_prem_min, hedge_prem_max] (user-configurable).
    Picks the strike closest to the midpoint of the hedge range.

    For CE: hedge strike > sold_strike (more OTM call)
    For PE: hedge strike < sold_strike (more OTM put)
    """
    lgr = logger or log
    ot  = opt_type.lower()
    TARGET_HEDGE_PREM = (hedge_prem_min + hedge_prem_max) / 2

    candidates: List[Dict[str, Any]] = []

    for strike, legs in strikes.items():
        leg = legs.get(ot)
        if leg is None:
            continue

        # Must be further OTM than sold_strike
        if ot == "ce" and strike <= sold_strike:
            continue
        if ot == "pe" and strike >= sold_strike:
            continue

        premium = leg["last_price"]

        if not (hedge_prem_min <= premium <= hedge_prem_max):
            continue

        candidates.append({
            "strike":      strike,
            "security_id": leg["security_id"],
            "premium":     premium,
            "delta":       leg["delta"],
            "bid":         leg["top_bid_price"],
            "ask":         leg["top_ask_price"],
            "opt_type":    ot,
        })

    if not candidates:
        lgr.warning("No %s hedge candidates in ₹%.0f-₹%.0f range beyond strike %d",
                    ot.upper(), hedge_prem_min, hedge_prem_max, int(sold_strike))
        return None

    # Pick closest to midpoint of hedge range
    candidates.sort(key=lambda x: abs(x["premium"] - TARGET_HEDGE_PREM))
    best = candidates[0]

    lgr.info(
        "Hedge strike selected: %s %d | premium=%.2f | secId=%d",
        ot.upper(), int(best["strike"]), best["premium"], best["security_id"]
    )
    return best


def validate_net_credit(sell_premium: float, hedge_premium: float,
                        min_net_credit: float = MIN_NET_CREDIT, logger=None) -> bool:
    """Check if net credit (sell - hedge) meets user-configured minimum."""
    lgr = logger or log
    net = sell_premium - hedge_premium
    if net < min_net_credit:
        lgr.warning("Net credit ₹%.2f < minimum ₹%.0f (sell=%.2f hedge=%.2f)",
                    net, min_net_credit, sell_premium, hedge_premium)
        return False
    lgr.info("Net credit validated: ₹%.2f (sell=%.2f - hedge=%.2f)", net, sell_premium, hedge_premium)
    return True
