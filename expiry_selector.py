"""
=============================================================================
Expiry Selector
Balfund Trading Private Limited
=============================================================================
Rules from strategy document:
  - 1st–15th of month   → use Current Month expiry
  - After 15th          → use Next Month expiry
  - Priority: Current → Next → Far Month

Monthly expiry = last Thursday of each month.
(Note: NIFTY shifted Thursday→Tuesday on Sep 1 2025, but Dhan expiry list
 is authoritative — we use it directly without assuming day of week.)
=============================================================================
"""

import logging
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

from config import EXPIRY_CUTOFF_DAY

log = logging.getLogger("ExpirySelector")


def _parse_dates(expiry_list: List[str]) -> List[date]:
    """Parse list of YYYY-MM-DD strings to date objects, filter future + today."""
    dates = []
    for s in expiry_list:
        try:
            d = datetime.strptime(s.strip(), "%Y-%m-%d").date()
            dates.append(d)
        except ValueError:
            continue
    return sorted(dates)


def get_monthly_expiries(expiry_list: List[str]) -> List[date]:
    """
    Filter expiry list to monthly expiries only.
    Monthly expiries are the last Thursday (or Tuesday post-Sep 2025) of each month.
    Strategy: pick one expiry per calendar month — the last one in that month.
    """
    all_dates = _parse_dates(expiry_list)
    by_month: dict = {}
    for d in all_dates:
        key = (d.year, d.month)
        if key not in by_month or d > by_month[key]:
            by_month[key] = d
    return sorted(by_month.values())


def select_expiry(expiry_list: List[str],
                  today: Optional[date] = None,
                  logger=None) -> Tuple[Optional[str], str]:
    """
    Select the appropriate expiry date based on strategy rules.

    Rules:
      - Day 1-15 of month  → current month expiry
      - Day 16-31 of month → next month expiry
      - Falls back to far month if criteria not met

    Args:
        expiry_list: List of "YYYY-MM-DD" strings from Dhan API
        today:       Date to evaluate (defaults to today)
        logger:      Optional logger

    Returns:
        Tuple of:
          - expiry string "YYYY-MM-DD" (or None if no expiries found)
          - label string: "CURRENT_MONTH" | "NEXT_MONTH" | "FAR_MONTH"
    """
    lgr = logger or log
    today = today or date.today()

    monthly = get_monthly_expiries(expiry_list)
    # Only future expiries (today or later)
    future = [d for d in monthly if d >= today]

    if not future:
        lgr.error("No future monthly expiries found in expiry list!")
        return None, "NONE"

    day_of_month = today.day

    if day_of_month <= EXPIRY_CUTOFF_DAY:
        # Before/on 15th — prefer current month expiry
        current_month_expiries = [d for d in future if d.year == today.year and d.month == today.month]
        if current_month_expiries:
            chosen = current_month_expiries[0]
            lgr.info("Expiry selected (day %d ≤ %d): CURRENT MONTH %s",
                     day_of_month, EXPIRY_CUTOFF_DAY, chosen)
            return chosen.strftime("%Y-%m-%d"), "CURRENT_MONTH"
        # Current month expiry already passed → fall to next month
        if len(future) >= 1:
            chosen = future[0]
            lgr.info("Current month expiry passed, using NEXT MONTH: %s", chosen)
            return chosen.strftime("%Y-%m-%d"), "NEXT_MONTH"
    else:
        # After 15th — prefer next month expiry
        next_month = today.month % 12 + 1
        next_year  = today.year + (1 if today.month == 12 else 0)
        next_month_expiries = [d for d in future
                               if d.year == next_year and d.month == next_month]
        if next_month_expiries:
            chosen = next_month_expiries[0]
            lgr.info("Expiry selected (day %d > %d): NEXT MONTH %s",
                     day_of_month, EXPIRY_CUTOFF_DAY, chosen)
            return chosen.strftime("%Y-%m-%d"), "NEXT_MONTH"
        # Fall back to whatever is next available
        if future:
            chosen = future[0]
            lgr.info("Falling back to first available: %s", chosen)
            return chosen.strftime("%Y-%m-%d"), "FAR_MONTH"

    if future:
        chosen = future[0]
        lgr.info("Far month fallback expiry: %s", chosen)
        return chosen.strftime("%Y-%m-%d"), "FAR_MONTH"

    return None, "NONE"


def is_first_trading_day_after_monthly_expiry(expiry_list: List[str],
                                              today: Optional[date] = None) -> bool:
    """
    Returns True if today is the first trading day after a monthly expiry.
    Used to trigger the 9:30 AM entry signal check.

    Logic: if the most recent past monthly expiry was yesterday or within
    the last 3 calendar days (to handle weekends/holidays).
    """
    today = today or date.today()
    monthly = get_monthly_expiries(expiry_list)
    past = [d for d in monthly if d < today]
    if not past:
        return False
    last_expiry = past[-1]
    days_since = (today - last_expiry).days
    return 1 <= days_since <= 3  # within 3 calendar days (covers Mon after Thur/Tue expiry)
