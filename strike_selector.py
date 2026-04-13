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

log = logging.getLogger("StrikeSelector")


def _is_multiple_of(strike: float, multiple: int) -> bool:
    return abs(strike % multiple) < 0.01


def select_sell_strike(
    strikes: Dict[float, Any],
    opt_type: str,                 # "ce" or "pe"
    nifty_ltp: float,
    logger=None,
) -> Optional[Dict[str, Any]]:
    """
    Select the best OTM strike to sell.

    Selection logic:
      1. Filter: delta in [DELTA_MIN, DELTA_MAX], premium in [SELECT_PREMIUM_MIN, SELECT_PREMIUM_MAX]
      2. OTM filter: CE strikes > spot, PE strikes < spot
      3. Prefer multiples of 100 (1st priority)
      4. Among qualifying, pick the one closest to delta 0.29 (midpoint of range)
         and whose premium is in [SELL_PREMIUM_MIN, SELL_PREMIUM_MAX]

    Returns:
        Dict with strike info or None if no suitable strike found.
    """
    lgr = logger or log
    ot  = opt_type.lower()

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

        # Delta filter
        if not (DELTA_MIN <= delta <= DELTA_MAX):
            continue

        # Premium filter (for strike selection)
        if not (SELECT_PREMIUM_MIN <= premium <= SELECT_PREMIUM_MAX):
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
        lgr.warning("No %s candidates in delta/premium range for LTP=%.2f", ot.upper(), nifty_ltp)
        return None

    # Sort: 1st preference — multiples of 100; 2nd — closest delta to 0.29
    TARGET_DELTA = (DELTA_MIN + DELTA_MAX) / 2  # 0.29
    candidates.sort(key=lambda x: (not x["is_multiple"], abs(x["delta"] - TARGET_DELTA)))

    # Now try to find one whose ACTUAL premium (not just selection range) meets entry criteria
    for c in candidates:
        if SELL_PREMIUM_MIN <= c["premium"] <= SELL_PREMIUM_MAX:
            lgr.info(
                "Sell strike selected: %s %d | premium=%.2f | delta=%.4f | secId=%d",
                ot.upper(), int(c["strike"]), c["premium"], c["delta"], c["security_id"]
            )
            return c

    # If none meet the stricter ₹200-300 range, return best candidate with a warning
    best = candidates[0]
    lgr.warning(
        "No %s strike in ₹200-300 range; best candidate: %d premium=%.2f (outside sell range)",
        ot.upper(), int(best["strike"]), best["premium"]
    )
    return None


def select_hedge_strike(
    strikes: Dict[float, Any],
    opt_type: str,
    sold_strike: float,
    nifty_ltp: float,
    logger=None,
) -> Optional[Dict[str, Any]]:
    """
    Select the hedge strike further OTM than sold_strike.
    Premium must be in [HEDGE_PREMIUM_MIN, HEDGE_PREMIUM_MAX].
    Picks the strike closest to (HEDGE_PREMIUM_MIN + HEDGE_PREMIUM_MAX) / 2 = ₹70.

    For CE: hedge strike > sold_strike (more OTM call)
    For PE: hedge strike < sold_strike (more OTM put)
    """
    lgr = logger or log
    ot  = opt_type.lower()
    TARGET_HEDGE_PREM = (HEDGE_PREMIUM_MIN + HEDGE_PREMIUM_MAX) / 2  # ₹70

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

        if not (HEDGE_PREMIUM_MIN <= premium <= HEDGE_PREMIUM_MAX):
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
        lgr.warning("No %s hedge candidates in ₹50-90 range beyond strike %d",
                    ot.upper(), int(sold_strike))
        return None

    # Pick closest to ₹70
    candidates.sort(key=lambda x: abs(x["premium"] - TARGET_HEDGE_PREM))
    best = candidates[0]

    lgr.info(
        "Hedge strike selected: %s %d | premium=%.2f | secId=%d",
        ot.upper(), int(best["strike"]), best["premium"], best["security_id"]
    )
    return best


def validate_net_credit(sell_premium: float, hedge_premium: float, logger=None) -> bool:
    """Check if net credit (sell - hedge) meets minimum requirement."""
    lgr = logger or log
    net = sell_premium - hedge_premium
    if net < MIN_NET_CREDIT:
        lgr.warning("Net credit ₹%.2f < minimum ₹%d (sell=%.2f hedge=%.2f)",
                    net, MIN_NET_CREDIT, sell_premium, hedge_premium)
        return False
    lgr.info("Net credit validated: ₹%.2f (sell=%.2f - hedge=%.2f)", net, sell_premium, hedge_premium)
    return True
