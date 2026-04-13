"""
=============================================================================
OI Option Seller — Configuration
Balfund Trading Private Limited
=============================================================================
All strategy parameters in one place. Edit here to tune the strategy.
=============================================================================
"""

# ─── NIFTY Instrument ────────────────────────────────────────────────────────
NIFTY_SCRIP_ID  = 13          # Security ID for NIFTY index on Dhan
NIFTY_SEG       = "IDX_I"    # Exchange segment
NIFTY_LOT_DEFAULT = 75       # Fallback if instrument master is unavailable

# ─── Strike Selection ────────────────────────────────────────────────────────
DELTA_MIN          = 0.25    # Minimum absolute delta for OTM strike selection
DELTA_MAX          = 0.33    # Maximum absolute delta for OTM strike selection
STRIKE_MULTIPLE    = 100     # Prefer strikes that are multiples of this

# ─── Premium Ranges ──────────────────────────────────────────────────────────
SELECT_PREMIUM_MIN = 150     # Minimum premium for initial strike filtering
SELECT_PREMIUM_MAX = 300     # Maximum premium for initial strike filtering
SELL_PREMIUM_MIN   = 200     # Minimum premium at actual entry (sold leg)
SELL_PREMIUM_MAX   = 300     # Maximum premium at actual entry (sold leg)
HEDGE_PREMIUM_MIN  = 50      # Minimum premium for hedge leg
HEDGE_PREMIUM_MAX  = 90      # Maximum premium for hedge leg
MIN_NET_CREDIT     = 100     # Minimum net credit required (sell - hedge)

# ─── Risk Management ─────────────────────────────────────────────────────────
SL_MULTIPLIER     = 1.382    # Stop loss = sold_premium × 1.382 (38.2% above)
TRAIL_TRIGGER_PCT = 0.50     # Trail SL when premium decays to 50% of sold value
# Trail SL moves to sold_premium (cost-to-cost) when trail is triggered

# ─── Alt Exit (Premium Difference Method) ────────────────────────────────────
ALT_EXIT_PREM_DIFF_MIN = 1   # Minimum premium difference (points) for alt exit
ALT_EXIT_PREM_DIFF_MAX = 10  # Maximum premium difference (points) for alt exit
ALT_EXIT_OI_DIFF_MIN   = 1   # Minimum OI difference to qualify alt exit

# ─── Execution Schedule (IST) ────────────────────────────────────────────────
MORNING_CHECK_HOUR    = 9    # 9:30 AM — SL placement, gap check, first-day entry
MORNING_CHECK_MIN     = 30
AFTERNOON_CHECK_HOUR  = 15   # 3:20 PM — Signal check + entry/exit
AFTERNOON_CHECK_MIN   = 20
SNAPSHOT_HOUR         = 15   # 3:30 PM — Snapshot for alt-exit next day
SNAPSHOT_MIN          = 30

# ─── Expiry Rules ─────────────────────────────────────────────────────────────
EXPIRY_CUTOFF_DAY = 15       # Before 15th → current month, after → next month

# ─── Order Settings ──────────────────────────────────────────────────────────
PRODUCT_TYPE        = "MARGIN"  # NRML equivalent for positional F&O on Dhan
DEFAULT_ORDER_TYPE  = "LIMIT"
LIMIT_OFFSET        = 2.0       # Rupees to add/subtract for limit orders
MARKET_FALLBACK_SEC = 10        # Seconds before falling back from LIMIT to MARKET

# ─── Dhan API ─────────────────────────────────────────────────────────────────
DHAN_BASE_URL     = "https://api.dhan.co/v2"
INSTRUMENT_CSV_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
OPTION_CHAIN_RATE_LIMIT_SEC = 3   # Dhan rate limit: 1 request per 3 seconds

# ─── Misc ─────────────────────────────────────────────────────────────────────
SCHEDULER_TICK_SEC = 10          # How often the scheduler thread checks the clock
LTP_POLL_INTERVAL  = 60          # Seconds between dashboard LTP refreshes during market hours
MARKET_OPEN_HOUR   = 9
MARKET_OPEN_MIN    = 15
MARKET_CLOSE_HOUR  = 15
MARKET_CLOSE_MIN   = 35
