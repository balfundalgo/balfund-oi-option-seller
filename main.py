"""
=============================================================================
OI Option Seller — Main Orchestrator
Balfund Trading Private Limited
=============================================================================
Runs the scheduled jobs and wires all modules together.

Scheduled jobs:
  09:30 AM → morning_routine()   — SL management, gap breach, trail
  15:20 PM → afternoon_routine() — Signal check, entry/exit
  15:30 PM → snapshot_routine()  — Save premium+OI for next day alt-exit

Also provides get_snapshot() for GUI dashboard polling.
=============================================================================
"""

import logging
import threading
import time
from collections import deque
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from config import (
    MORNING_CHECK_HOUR, MORNING_CHECK_MIN,
    AFTERNOON_CHECK_HOUR, AFTERNOON_CHECK_MIN,
    SNAPSHOT_HOUR, SNAPSHOT_MIN,
    SCHEDULER_TICK_SEC, LTP_POLL_INTERVAL,
    MARKET_OPEN_HOUR, MARKET_OPEN_MIN,
    MARKET_CLOSE_HOUR, MARKET_CLOSE_MIN,
    OPTION_CHAIN_RATE_LIMIT_SEC,
)
from dhan_client       import DhanClient
from option_chain      import fetch_and_parse, get_leg_data
from expiry_selector   import select_expiry, is_first_trading_day_after_monthly_expiry
from strike_selector   import select_sell_strike, select_hedge_strike, validate_net_credit
from signal_engine     import (Signal, compute_signal, compute_alt_exit,
                                is_entry_signal, is_exit_signal, signal_label)
from position_store    import PositionStore, LEG_CE, LEG_PE
from sl_manager        import SLManager
from order_executor    import OrderExecutor

log = logging.getLogger("OIOptionSeller")


class OIOptionSellerApp:
    """
    Main application class for OI-based NIFTY option selling strategy.
    Instantiated by the GUI, provides start/stop/get_snapshot/square_off_all.
    """

    def __init__(
        self,
        client_id:      str,
        access_token:   str,
        live_mode:      bool  = False,
        lot_multiplier: int   = 1,
        order_type:     str   = "LIMIT",
        limit_offset:   float = 2.0,
        delta_min:      float = 0.25,
        delta_max:      float = 0.33,
        sell_prem_min:  float = 200.0,
        sell_prem_max:  float = 300.0,
        hedge_prem_min: float = 50.0,
        hedge_prem_max: float = 90.0,
        min_net_credit: float = 100.0,
        logger=None,
    ):
        self.client_id      = client_id
        self.access_token   = access_token
        self.live_mode      = live_mode
        self.lot_multiplier = max(1, int(lot_multiplier))
        self.order_type     = order_type
        self.limit_offset   = limit_offset
        self.delta_min      = float(delta_min)
        self.delta_max      = float(delta_max)
        self.sell_prem_min  = float(sell_prem_min)
        self.sell_prem_max  = float(sell_prem_max)
        self.hedge_prem_min = float(hedge_prem_min)
        self.hedge_prem_max = float(hedge_prem_max)
        self.min_net_credit = float(min_net_credit)
        self.logger         = logger or log

        self.dhan     = DhanClient(client_id, access_token, logger=self.logger)
        self.store    = PositionStore(logger=self.logger)
        self.sl_mgr   = SLManager(self.dhan, self.store, live_mode, logger=self.logger)
        self.executor = OrderExecutor(
            self.dhan, live_mode, order_type, limit_offset, logger=self.logger)

        self._lot_size:       int  = 0   # fetched on start
        self._expiry_list:    List[str]  = []
        self._target_expiry:  Optional[str] = None
        self._expiry_label:   str  = ""
        self._nifty_ltp:      float = 0.0
        self._curr_strikes:   dict  = {}   # last fetched parsed option chain

        self._running        = False
        self._stop_event     = threading.Event()
        self._events: deque  = deque(maxlen=200)
        self._lock           = threading.Lock()
        self._start_time:    Optional[float] = None

        # For GUI dashboard
        self._last_signals:  Dict[str, str] = {LEG_CE: "—", LEG_PE: "—"}
        self._last_check:    str = "—"
        self._next_check:    str = "—"
        self._ltp_cache:     Dict[str, float] = {}   # sec_id → current LTP estimate
        self._realized_pnl:  float = 0.0

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        """Start the app: fetch lot size, expiry list, then kick off scheduler."""
        self._stop_event.clear()
        self._running = True
        self._start_time = time.time()

        self._log("🚀 OI Option Seller starting...")
        self._log(f"   Mode: {'🔴 LIVE' if self.live_mode else '📄 PAPER'}  |  "
                  f"Lots: {self.lot_multiplier}  |  Orders: {self.order_type}")
        self._log(f"   Delta: {self.delta_min}–{self.delta_max}  |  "
                  f"Sell ₹{self.sell_prem_min}–₹{self.sell_prem_max}  |  "
                  f"Hedge ₹{self.hedge_prem_min}–₹{self.hedge_prem_max}  |  "
                  f"Min Credit ₹{self.min_net_credit}")

        # Fetch lot size
        self._lot_size = self.dhan.fetch_nifty_lot_size()
        self._log(f"   NIFTY lot size: {self._lot_size}")

        # Fetch expiry list
        self._expiry_list = self.dhan.get_expiry_list()
        if not self._expiry_list:
            self._log("❌ Could not fetch expiry list — check API token")
            self._running = False
            return

        # Select expiry
        self._target_expiry, self._expiry_label = select_expiry(
            self._expiry_list, logger=self.logger)
        self._log(f"   Target expiry: {self._target_expiry} ({self._expiry_label})")

        # Start scheduler thread
        threading.Thread(target=self._scheduler_loop, name="Scheduler", daemon=True).start()
        # Start LTP poll thread
        threading.Thread(target=self._ltp_poll_loop, name="LTPPoller", daemon=True).start()

        self._log("✅ Scheduler running — waiting for next scheduled event")
        self._update_next_check_label()

    def stop(self):
        """Stop all background threads."""
        self._running = False
        self._stop_event.set()
        self._log("⏹ Strategy stopped")

    def square_off_all(self):
        """Emergency square off — close all open positions immediately."""
        self._log("⬛ SQUARE OFF ALL triggered")
        for leg in [LEG_CE, LEG_PE]:
            pos = self.store.get_position(leg)
            if pos:
                self._close_spread(leg, pos, reason="MANUAL_SQUARE_OFF")

    # ── Scheduler ─────────────────────────────────────────────────────────────

    def _scheduler_loop(self):
        """Main scheduler: checks time every SCHEDULER_TICK_SEC seconds."""
        while self._running and not self._stop_event.is_set():
            now = datetime.now()
            h, m = now.hour, now.minute

            if now.weekday() >= 5:   # skip weekends
                time.sleep(SCHEDULER_TICK_SEC)
                continue

            # 09:30 AM
            if h == MORNING_CHECK_HOUR and m == MORNING_CHECK_MIN:
                job = f"morning_{now.date().isoformat()}"
                if not self.store.job_fired_today("morning"):
                    self.store.mark_job_fired("morning")
                    threading.Thread(target=self._morning_routine,
                                     name="MorningJob", daemon=True).start()

            # 15:20 PM
            elif h == AFTERNOON_CHECK_HOUR and m == AFTERNOON_CHECK_MIN:
                if not self.store.job_fired_today("afternoon"):
                    self.store.mark_job_fired("afternoon")
                    threading.Thread(target=self._afternoon_routine,
                                     name="AfternoonJob", daemon=True).start()

            # 15:30 PM
            elif h == SNAPSHOT_HOUR and m == SNAPSHOT_MIN:
                if not self.store.job_fired_today("snapshot"):
                    self.store.mark_job_fired("snapshot")
                    threading.Thread(target=self._snapshot_routine,
                                     name="SnapshotJob", daemon=True).start()

            time.sleep(SCHEDULER_TICK_SEC)

    # ── Morning Routine (9:30 AM) ─────────────────────────────────────────────

    def _morning_routine(self):
        self._log("☀️ === 9:30 AM MORNING ROUTINE ===")
        self._last_check = f"{datetime.now().strftime('%H:%M')} (Morning)"

        try:
            # Refresh expiry
            self._refresh_expiry()

            # Fetch option chain for current positions
            nifty_ltp, strikes = self._fetch_chain()
            self._nifty_ltp = nifty_ltp

            any_pos = self.store.any_position_open()

            if any_pos:
                for leg in [LEG_CE, LEG_PE]:
                    pos = self.store.get_position(leg)
                    if not pos:
                        continue
                    # Get current premium for this position's strike
                    leg_data = get_leg_data(strikes, pos["strike"], pos["opt_type"])
                    curr_prem = leg_data["last_price"] if leg_data else pos["sold_premium"]

                    result = self.sl_mgr.morning_sl_routine(
                        leg, pos, curr_prem,
                        close_spread_fn=self._close_spread,
                    )
                    self._log(f"   SL [{leg.upper()}]: {result}")
            else:
                self._log("   No open positions — skipping SL management")

            # First trading day after expiry → check entry signals at 9:30 AM too
            if is_first_trading_day_after_monthly_expiry(self._expiry_list):
                self._log("   📅 First trading day after expiry — checking entry signals at 9:30 AM")
                self._run_signal_check(strikes, is_first_day=True)

        except Exception as e:
            self._log(f"❌ Morning routine error: {e}")
            self.logger.exception("Morning routine error")

        self._update_next_check_label()

    # ── Afternoon Routine (3:20 PM) ───────────────────────────────────────────

    def _afternoon_routine(self):
        self._log("🌆 === 3:20 PM AFTERNOON ROUTINE ===")
        self._last_check = f"{datetime.now().strftime('%H:%M')} (Afternoon)"

        try:
            self._refresh_expiry()
            nifty_ltp, strikes = self._fetch_chain()
            self._nifty_ltp = nifty_ltp
            self._run_signal_check(strikes, is_first_day=False)
        except Exception as e:
            self._log(f"❌ Afternoon routine error: {e}")
            self.logger.exception("Afternoon routine error")

        self._update_next_check_label()

    # ── Core Signal Logic ─────────────────────────────────────────────────────

    def _run_signal_check(self, strikes: dict, is_first_day: bool = False):
        """Run signal check for both CE and PE simultaneously."""
        self._log(f"   📊 Running signal check — NIFTY LTP: {self._nifty_ltp:.2f}")

        for leg, ot in [(LEG_CE, "ce"), (LEG_PE, "pe")]:
            try:
                self._process_leg_signal(leg, ot, strikes, is_first_day)
            except Exception as e:
                self._log(f"   ❌ Signal error [{leg.upper()}]: {e}")
                self.logger.exception("Signal check error for %s", leg)

    def _process_leg_signal(
        self, leg: str, opt_type: str, strikes: dict, is_first_day: bool
    ):
        pos = self.store.get_position(leg)

        if pos:
            # ── EXISTING POSITION: check exit signal ──────────────────────
            leg_data = get_leg_data(strikes, pos["strike"], opt_type)
            curr_prem = leg_data["last_price"] if leg_data else pos["sold_premium"]
            curr_oi   = leg_data["oi"] if leg_data else 0

            sig = compute_signal(leg_data, logger=self.logger)
            self._last_signals[leg] = signal_label(sig)
            self._log(f"   [{leg.upper()}] Signal: {signal_label(sig)}")

            # Primary exit
            if is_exit_signal(sig):
                self._log(f"   [{leg.upper()}] EXIT signal → closing spread")
                self._close_spread(leg, pos, reason=sig.value)
                return

            # Alt exit check (if snapshot exists)
            snap = self.store.get_snapshot_for_leg(leg, pos["strike"])
            if snap:
                alt = compute_alt_exit(
                    curr_prem, curr_oi,
                    snap.get("premium", 0), snap.get("oi", 0),
                    logger=self.logger,
                )
                if alt:
                    self._log(f"   [{leg.upper()}] ALT EXIT signal → closing spread")
                    self._close_spread(leg, pos, reason="ALT_EXIT_PREM_DIFF")
                    return

            self._log(f"   [{leg.upper()}] Holding position — no exit signal")

        else:
            # ── NO POSITION: check entry signal ───────────────────────────
            # We need to find the right strike first, then check signal
            # Select candidate strike and check its signal
            sell_strike = select_sell_strike(
                strikes, opt_type, self._nifty_ltp,
                delta_min=self.delta_min, delta_max=self.delta_max,
                sell_prem_min=self.sell_prem_min, sell_prem_max=self.sell_prem_max,
                logger=self.logger)

            if sell_strike is None:
                self._log(f"   [{leg.upper()}] No qualifying strike found for entry")
                self._last_signals[leg] = "No qualifying strike"
                return

            # Get signal for the selected strike
            candidate_leg_data = get_leg_data(strikes, sell_strike["strike"], opt_type)
            sig = compute_signal(candidate_leg_data, logger=self.logger)
            self._last_signals[leg] = signal_label(sig)
            self._log(f"   [{leg.upper()}] Strike {int(sell_strike['strike'])} Signal: {signal_label(sig)}")

            if not is_entry_signal(sig):
                self._log(f"   [{leg.upper()}] No entry signal — waiting")
                return

            # Validate sell premium at entry
            curr_prem = sell_strike["premium"]
            if not (self.sell_prem_min <= curr_prem <= self.sell_prem_max):
                self._log(f"   [{leg.upper()}] Premium ₹{curr_prem:.2f} outside sell range — skipping")
                return

            # Select hedge
            hedge_strike = select_hedge_strike(
                strikes, opt_type, sell_strike["strike"], self._nifty_ltp,
                hedge_prem_min=self.hedge_prem_min, hedge_prem_max=self.hedge_prem_max,
                logger=self.logger)

            if hedge_strike is None:
                self._log(f"   [{leg.upper()}] No hedge strike found — skipping entry")
                return

            # Validate net credit
            if not validate_net_credit(curr_prem, hedge_strike["premium"],
                                        min_net_credit=self.min_net_credit, logger=self.logger):
                self._log(f"   [{leg.upper()}] Insufficient net credit — skipping")
                return

            # Execute the spread
            self._enter_spread(leg, sell_strike, hedge_strike)

    # ── Snapshot Routine (3:30 PM) ────────────────────────────────────────────

    def _snapshot_routine(self):
        self._log("📸 === 3:30 PM SNAPSHOT ===")
        try:
            # Only snapshot strikes we're actually holding
            snap: Dict[str, Any] = {"date": date.today().isoformat(), "ce": {}, "pe": {}}
            has_pos = False

            for leg, ot in [(LEG_CE, "ce"), (LEG_PE, "pe")]:
                pos = self.store.get_position(leg)
                if not pos:
                    continue
                has_pos = True
                # Use cached strikes for current premium/OI
                leg_data = get_leg_data(self._curr_strikes, pos["strike"], ot)
                if leg_data:
                    snap[ot][str(int(pos["strike"]))] = {
                        "premium": leg_data["last_price"],
                        "oi":      leg_data["oi"],
                    }
                    self._log(f"   Snapshot [{leg.upper()}] {int(pos['strike'])} "
                              f"premium={leg_data['last_price']:.2f} OI={leg_data['oi']}")

            if has_pos:
                self.store.save_snapshot(snap)
            else:
                self._log("   No positions — skipping snapshot")

        except Exception as e:
            self._log(f"❌ Snapshot error: {e}")
            self.logger.exception("Snapshot error")

    # ── Enter / Close Spread ──────────────────────────────────────────────────

    def _enter_spread(
        self,
        leg: str,
        sell_strike: Dict[str, Any],
        hedge_strike: Dict[str, Any],
    ):
        ot         = sell_strike["opt_type"]
        quantity   = self._lot_size * self.lot_multiplier
        label      = f"{ot.upper()}_{int(sell_strike['strike'])}"

        self._log(
            f"   🟢 ENTERING SPREAD [{leg.upper()}] | "
            f"SELL {ot.upper()} {int(sell_strike['strike'])} @ ₹{sell_strike['premium']:.2f} | "
            f"HEDGE {int(hedge_strike['strike'])} @ ₹{hedge_strike['premium']:.2f} | "
            f"qty={quantity}"
        )

        result = self.executor.place_sell_spread(
            sell_security_id   = sell_strike["security_id"],
            sell_ref_price     = sell_strike["premium"],
            hedge_security_id  = hedge_strike["security_id"],
            hedge_ref_price    = hedge_strike["premium"],
            quantity           = quantity,
            label              = label,
        )

        if not result.get("success"):
            self._log(f"   ❌ Spread entry FAILED: {result.get('reason', 'unknown')}")
            return

        sold_prem   = result["sell_fill_price"]
        hedge_prem  = result["hedge_fill_price"] or hedge_strike["premium"]
        sl_price    = self.sl_mgr.compute_sl_price(sold_prem)

        position = {
            "strike":          sell_strike["strike"],
            "security_id":     sell_strike["security_id"],
            "hedge_strike":    hedge_strike["strike"],
            "hedge_sec_id":    hedge_strike["security_id"],
            "sold_premium":    sold_prem,
            "hedge_premium":   hedge_prem,
            "lot_size":        self._lot_size,
            "lots":            self.lot_multiplier,
            "expiry":          self._target_expiry,
            "entry_time":      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "sl_price":        sl_price,
            "sl_order_id":     "",
            "trail_triggered": False,
            "opt_type":        ot,
        }

        self.store.set_position(leg, position)

        # Place initial SL order
        sl_id = self.sl_mgr.place_sl_order(position)
        if sl_id:
            self.store.update_sl_order_id(leg, sl_id, sl_price)

        net_credit = sold_prem - hedge_prem
        self._log(
            f"   ✅ Position opened: {ot.upper()} {int(sell_strike['strike'])} | "
            f"net credit=₹{net_credit:.2f} | SL=₹{sl_price:.2f}"
        )

    def _close_spread(
        self,
        leg: str,
        position: Dict[str, Any],
        reason: str = "",
        close_main: bool = True,
    ):
        ot       = position.get("opt_type", "option").upper()
        strike   = int(position.get("strike", 0))
        quantity = int(position["lot_size"]) * int(position.get("lots", 1))

        self._log(
            f"   🔴 CLOSING SPREAD [{leg.upper()}] | {ot} {strike} | "
            f"qty={quantity} | reason={reason}"
        )

        # Cancel SL order first
        sl_id = position.get("sl_order_id", "")
        if sl_id:
            self.sl_mgr.cancel_sl_order(sl_id)
            time.sleep(0.3)

        # Get current prices from cached strikes for limit order reference
        leg_data   = get_leg_data(self._curr_strikes, position["strike"], position["opt_type"])
        hedge_data = get_leg_data(self._curr_strikes, position["hedge_strike"], position["opt_type"])

        main_ref  = leg_data["last_price"]  if leg_data  else position["sold_premium"]
        hedge_ref = hedge_data["last_price"] if hedge_data else position["hedge_premium"]

        if close_main:
            result = self.executor.close_sell_spread(
                sell_security_id   = str(position["security_id"]),
                sell_ref_price     = main_ref,
                hedge_security_id  = str(position["hedge_sec_id"]),
                hedge_ref_price    = hedge_ref,
                quantity           = quantity,
                label              = f"{ot}_{strike}",
            )
            close_price = result.get("close_fill_price", main_ref)
        else:
            # Main SL was already triggered — only close the hedge
            ok, hedge_fill, _ = self.executor.close_single_leg(
                str(position["hedge_sec_id"]), hedge_ref, quantity, "SELL",
                label=f"HEDGE_CLOSE_{ot}_{strike}",
            )
            close_price = position["sold_premium"]   # SL price (already filled)
            hedge_ref   = hedge_fill

        # Compute realized P&L
        sold_prem  = float(position["sold_premium"])
        hedge_prem = float(position["hedge_premium"])
        qty_total  = quantity

        pnl_main  = (sold_prem  - close_price) * qty_total
        pnl_hedge = (hedge_ref  - hedge_prem ) * qty_total
        net_pnl   = pnl_main - pnl_hedge   # hedge PnL is additive when premium fell

        with self._lock:
            self._realized_pnl += net_pnl

        self._log(
            f"   ✅ Position closed: {ot} {strike} | "
            f"entry=₹{sold_prem:.2f} exit=₹{close_price:.2f} | "
            f"P&L=₹{net_pnl:+.2f} | Total P&L=₹{self._realized_pnl:+.2f}"
        )

        self.store.clear_position(leg)

    # ── LTP Poll Loop ─────────────────────────────────────────────────────────

    def _ltp_poll_loop(self):
        """
        Polls option chain every LTP_POLL_INTERVAL seconds during market hours
        to refresh current premium/OI for dashboard display.
        """
        while self._running and not self._stop_event.is_set():
            now = datetime.now()
            in_market = (
                now.weekday() < 5
                and (now.hour, now.minute) >= (MARKET_OPEN_HOUR, MARKET_OPEN_MIN)
                and (now.hour, now.minute) <= (MARKET_CLOSE_HOUR, MARKET_CLOSE_MIN)
            )

            if in_market and self._target_expiry:
                try:
                    nifty_ltp, strikes = self._fetch_chain()
                    self._nifty_ltp = nifty_ltp
                except Exception as e:
                    self.logger.warning("LTP poll error: %s", e)

            time.sleep(LTP_POLL_INTERVAL)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _fetch_chain(self):
        """Fetch and cache option chain."""
        nifty_ltp, strikes = fetch_and_parse(
            self.dhan, self._target_expiry, logger=self.logger)
        with self._lock:
            self._curr_strikes = strikes
        return nifty_ltp, strikes

    def _refresh_expiry(self):
        """Re-evaluate expiry selection (may change after 15th of month)."""
        self._expiry_list = self.dhan.get_expiry_list() or self._expiry_list
        new_expiry, new_label = select_expiry(self._expiry_list, logger=self.logger)
        if new_expiry != self._target_expiry:
            self._log(f"   ⚡ Expiry updated: {self._target_expiry} → {new_expiry} ({new_label})")
            self._target_expiry = new_expiry
            self._expiry_label  = new_label

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}]  {msg}"
        with self._lock:
            self._events.appendleft(entry)
        self.logger.info(msg)

    def _update_next_check_label(self):
        now  = datetime.now()
        h, m = now.hour, now.minute
        if (h, m) < (MORNING_CHECK_HOUR, MORNING_CHECK_MIN):
            self._next_check = f"09:30 AM (Morning SL + Signals)"
        elif (h, m) < (AFTERNOON_CHECK_HOUR, AFTERNOON_CHECK_MIN):
            self._next_check = f"15:20 PM (Signal Check)"
        elif (h, m) < (SNAPSHOT_HOUR, SNAPSHOT_MIN):
            self._next_check = f"15:30 PM (Snapshot)"
        else:
            self._next_check = f"09:30 AM Tomorrow"

    # ── Snapshot for GUI ──────────────────────────────────────────────────────

    def get_snapshot(self) -> Dict[str, Any]:
        """Called by GUI every second to refresh dashboard display."""
        with self._lock:
            positions = self.store.get_all_positions()
            events    = list(self._events)[:30]
            strikes   = dict(self._curr_strikes)
            nifty_ltp = self._nifty_ltp

        def _enrich_position(leg: str, pos):
            if pos is None:
                return None
            ot = pos.get("opt_type", leg)
            leg_data   = get_leg_data(strikes, pos["strike"], ot)
            hedge_data = get_leg_data(strikes, pos["hedge_strike"], ot)
            curr_prem  = leg_data["last_price"]  if leg_data  else pos["sold_premium"]
            curr_hedge = hedge_data["last_price"] if hedge_data else pos["hedge_premium"]
            qty        = int(pos["lot_size"]) * int(pos.get("lots", 1))
            pnl_main   = (pos["sold_premium"]  - curr_prem ) * qty
            pnl_hedge  = (curr_hedge - pos["hedge_premium"] ) * qty
            net_pnl    = pnl_main - pnl_hedge
            return {
                **pos,
                "curr_prem":   curr_prem,
                "curr_hedge":  curr_hedge,
                "net_pnl":     net_pnl,
                "sl_pct":      ((curr_prem / pos["sold_premium"]) - 1) * 100,
            }

        return {
            "running":        self._running,
            "live_mode":      self.live_mode,
            "nifty_ltp":      nifty_ltp,
            "expiry":         self._target_expiry or "—",
            "expiry_label":   self._expiry_label,
            "lot_size":       self._lot_size,
            "lot_multiplier": self.lot_multiplier,
            "ce_position":    _enrich_position(LEG_CE, positions.get(LEG_CE)),
            "pe_position":    _enrich_position(LEG_PE, positions.get(LEG_PE)),
            "last_signal_ce": self._last_signals.get(LEG_CE, "—"),
            "last_signal_pe": self._last_signals.get(LEG_PE, "—"),
            "last_check":     self._last_check,
            "next_check":     self._next_check,
            "realized_pnl":   self._realized_pnl,
            "events":         events,
            "order_type":     self.order_type,
            "uptime_secs":    int(time.time() - self._start_time) if self._start_time else 0,
        }
