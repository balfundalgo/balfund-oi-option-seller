"""
=============================================================================
Option Chain — Fetch & Parse
Balfund Trading Private Limited
=============================================================================
Fetches NIFTY option chain from Dhan API and returns a clean structure
ready for use by signal_engine and strike_selector.
=============================================================================
"""

import logging
from typing import Dict, Any, List, Optional, Tuple

log = logging.getLogger("OptionChain")


def fetch_and_parse(dhan_client, expiry: str, logger=None) -> Tuple[float, Dict[str, Any]]:
    """
    Fetch NIFTY option chain for given expiry and parse into clean structure.

    Args:
        dhan_client: DhanClient instance
        expiry:      "YYYY-MM-DD" expiry date string
        logger:      Optional logger

    Returns:
        Tuple of:
          - nifty_ltp (float): Current NIFTY spot price
          - strikes (dict): Keyed by float strike price
            {
              25000.0: {
                "ce": {
                  "security_id":         int,
                  "last_price":          float,  ← current premium
                  "oi":                  int,    ← current OI
                  "previous_close_price": float, ← prev day close
                  "previous_oi":         int,    ← prev day OI
                  "delta":               float,  ← already provided by Dhan
                  "iv":                  float,
                  "top_bid_price":       float,
                  "top_ask_price":       float,
                  "volume":              int,
                },
                "pe": { ... same fields ... }
              },
              ...
            }
    """
    lgr = logger or log
    resp = dhan_client.get_option_chain(expiry)

    if resp.get("status") != "success":
        lgr.error("Option chain API returned non-success: %s", resp)
        return 0.0, {}

    data       = resp.get("data", {})
    nifty_ltp  = float(data.get("last_price", 0))
    oc_raw     = data.get("oc", {})

    strikes: Dict[float, Any] = {}

    for strike_str, legs in oc_raw.items():
        try:
            strike = float(strike_str)
        except (ValueError, TypeError):
            continue

        parsed: Dict[str, Any] = {}

        for opt_type in ("ce", "pe"):
            leg = legs.get(opt_type)
            if not leg:
                parsed[opt_type] = None
                continue

            greeks = leg.get("greeks", {}) or {}
            delta_raw = greeks.get("delta", 0.0)
            # For PE, delta is negative — we store absolute value for comparison
            delta_abs = abs(float(delta_raw)) if delta_raw is not None else 0.0

            parsed[opt_type] = {
                "security_id":          int(leg.get("security_id", 0)),
                "last_price":           float(leg.get("last_price", 0) or 0),
                "oi":                   int(leg.get("oi", 0) or 0),
                "previous_close_price": float(leg.get("previous_close_price", 0) or 0),
                "previous_oi":          int(leg.get("previous_oi", 0) or 0),
                "previous_volume":      int(leg.get("previous_volume", 0) or 0),
                "volume":               int(leg.get("volume", 0) or 0),
                "average_price":        float(leg.get("average_price", 0) or 0),
                "delta":                delta_abs,
                "delta_signed":         float(delta_raw) if delta_raw is not None else 0.0,
                "theta":                float(greeks.get("theta", 0) or 0),
                "gamma":                float(greeks.get("gamma", 0) or 0),
                "vega":                 float(greeks.get("vega", 0)  or 0),
                "iv":                   float(leg.get("implied_volatility", 0) or 0),
                "top_bid_price":        float(leg.get("top_bid_price", 0) or 0),
                "top_ask_price":        float(leg.get("top_ask_price", 0) or 0),
                "top_bid_qty":          int(leg.get("top_bid_quantity", 0) or 0),
                "top_ask_qty":          int(leg.get("top_ask_quantity", 0) or 0),
            }

        strikes[strike] = parsed

    lgr.info("Option chain parsed: %d strikes, NIFTY LTP=%.2f, expiry=%s",
             len(strikes), nifty_ltp, expiry)
    return nifty_ltp, strikes


def get_leg_data(strikes: Dict[float, Any],
                 strike: float,
                 opt_type: str) -> Optional[Dict[str, Any]]:
    """
    Get CE or PE data for a specific strike from parsed option chain.

    Args:
        strikes:  Parsed strikes dict from fetch_and_parse()
        strike:   Strike price (float)
        opt_type: "ce" or "pe"

    Returns:
        Leg data dict or None if not found
    """
    row = strikes.get(float(strike))
    if row is None:
        return None
    return row.get(opt_type.lower())
