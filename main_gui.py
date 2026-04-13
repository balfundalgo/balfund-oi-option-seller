"""
=============================================================================
OI Option Seller — GUI
Balfund Trading Private Limited
=============================================================================
CustomTkinter GUI with same colour palette and design language as
dhan-ha-trader-v9. Tabs: Token Manager | OI Strategy.
=============================================================================
"""

import os
import sys
import json
import threading
from datetime import datetime
from pathlib import Path

import customtkinter as ctk
from tkinter import messagebox

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).resolve().parent

ENV_FILE      = BASE_DIR / ".env"
SETTINGS_FILE = BASE_DIR / "settings.json"


# ── .env helpers ─────────────────────────────────────────────────────────────

def _load_env() -> dict:
    data = {}
    if ENV_FILE.exists():
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip().strip('"').strip("'")
    return data


def _save_env_key(key: str, value: str):
    lines = []
    found = False
    if ENV_FILE.exists():
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        for i, line in enumerate(lines):
            if line.strip().startswith(f"{key}="):
                lines[i] = f"{key}={value}\n"
                found = True
                break
    if not found:
        lines.append(f"{key}={value}\n")
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.writelines(lines)
    os.environ[key] = value


# ── Colour Palette (identical to dhan-ha-trader) ─────────────────────────────

DARK_BG    = "#0d1117"
PANEL_BG   = "#161b22"
CARD_BG    = "#21262d"
ACCENT     = "#238636"
ACCENT_H   = "#2ea043"
RED_COL    = "#da3633"
RED_H      = "#b91c1c"
ORANGE_COL = "#d29922"
CYAN_COL   = "#58a6ff"
WHITE_COL  = "#e6edf3"
GREY_COL   = "#8b949e"
BORDER     = "#30363d"
LIVE_COL   = "#f85149"
PURPLE_COL = "#bc8cff"
TEAL_COL   = "#39d353"

F_TITLE  = ("Segoe UI", 20, "bold")
F_HEAD   = ("Segoe UI", 15, "bold")
F_LABEL  = ("Segoe UI", 13)
F_BTN    = ("Segoe UI", 13, "bold")
F_MONO   = ("Consolas", 12)
F_MONO_S = ("Consolas", 11)
F_SMALL  = ("Segoe UI", 11)

ORDER_TYPES   = ["LIMIT", "MARKET"]
LOT_MULT_OPTS = ["1", "2", "3", "4", "5"]

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")


# ══════════════════════════════════════════════════════════════════════════════
#  TOKEN TAB (identical structure to dhan-ha-trader)
# ══════════════════════════════════════════════════════════════════════════════

class TokenTab(ctk.CTkFrame):
    def __init__(self, master, on_token_saved):
        super().__init__(master, fg_color=DARK_BG)
        self.on_token_saved = on_token_saved
        self._build()
        self._load_saved()

    def _build(self):
        ctk.CTkLabel(self, text="🔑  Dhan API — Token Manager",
                     font=F_TITLE, text_color=WHITE_COL).pack(pady=(30, 4))
        ctk.CTkLabel(self,
                     text="Credentials saved locally in .env — never uploaded anywhere.",
                     font=F_SMALL, text_color=GREY_COL).pack(pady=(0, 20))

        form = ctk.CTkFrame(self, fg_color=PANEL_BG, corner_radius=14)
        form.pack(padx=80, fill="x")

        def _row(label, show=""):
            row = ctk.CTkFrame(form, fg_color="transparent")
            row.pack(fill="x", padx=28, pady=10)
            ctk.CTkLabel(row, text=label, width=180, anchor="w",
                         font=F_LABEL, text_color=WHITE_COL).pack(side="left")
            e = ctk.CTkEntry(row, show=show, width=440, height=38,
                             fg_color=CARD_BG, border_color=BORDER,
                             text_color=WHITE_COL, font=F_MONO_S)
            e.pack(side="left", padx=(10, 0))
            return e

        ctk.CTkFrame(form, fg_color="transparent", height=10).pack()
        self.e_client = _row("Client ID")
        self.e_pin    = _row("PIN  (6-digit)", show="●")
        self.e_totp   = _row("TOTP Secret",   show="●")
        self.e_token  = _row("Access Token")
        ctk.CTkFrame(form, fg_color="transparent", height=10).pack()

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(pady=20)
        ctk.CTkButton(btn_row, text="💾  Save Credentials", width=200, height=42,
                      fg_color=CARD_BG, hover_color=BORDER, text_color=WHITE_COL,
                      font=F_BTN, command=self._save_creds).pack(side="left", padx=10)
        self.gen_btn = ctk.CTkButton(btn_row, text="⚡  Generate Token", width=200, height=42,
                      fg_color=ACCENT, hover_color=ACCENT_H, text_color=WHITE_COL,
                      font=F_BTN, command=self._generate_token)
        self.gen_btn.pack(side="left", padx=10)
        ctk.CTkButton(btn_row, text="✅  Verify Token", width=200, height=42,
                      fg_color=CARD_BG, hover_color=BORDER, text_color=WHITE_COL,
                      font=F_BTN, command=self._verify_token).pack(side="left", padx=10)

        ctk.CTkLabel(self, text="Log", anchor="w",
                     font=("Segoe UI", 12, "bold"), text_color=GREY_COL
                     ).pack(padx=80, anchor="w", pady=(14, 2))
        self.log_box = ctk.CTkTextbox(self, height=150, font=F_MONO_S,
                                      fg_color=PANEL_BG, text_color=WHITE_COL,
                                      border_color=BORDER, border_width=1)
        self.log_box.pack(padx=80, fill="x")
        self.log_box.configure(state="disabled")

        # Shared token section
        sf = ctk.CTkFrame(self, fg_color=PANEL_BG, corner_radius=12)
        sf.pack(padx=80, fill="x", pady=(14, 0))
        ctk.CTkLabel(sf, text="🔗  dhan-token-generator  (shared token)",
                     font=("Segoe UI", 12, "bold"), text_color=CYAN_COL
                     ).pack(side="left", padx=16, pady=10)
        self.shared_lbl = ctk.CTkLabel(sf, text="Checking…", width=280,
                                        font=F_SMALL, text_color=GREY_COL)
        self.shared_lbl.pack(side="left", padx=10)
        ctk.CTkButton(sf, text="🔄  Load from Token Generator", width=240, height=34,
                      fg_color=CARD_BG, hover_color=BORDER, text_color=WHITE_COL,
                      font=F_BTN, command=self._load_from_shared
                      ).pack(side="right", padx=16, pady=8)
        self.after(600, self._check_shared_status)

    def _load_saved(self):
        env = _load_env()
        self.e_client.insert(0, env.get("DHAN_CLIENT_ID", ""))
        self.e_pin.insert(0, env.get("DHAN_PIN", ""))
        self.e_totp.insert(0, env.get("DHAN_TOTP_SECRET", ""))
        self.e_token.insert(0, env.get("DHAN_ACCESS_TOKEN", ""))

    def _log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{ts}]  {msg}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _save_creds(self):
        _save_env_key("DHAN_CLIENT_ID",   self.e_client.get().strip())
        _save_env_key("DHAN_PIN",         self.e_pin.get().strip())
        _save_env_key("DHAN_TOTP_SECRET", self.e_totp.get().strip())
        token = self.e_token.get().strip()
        if token:
            _save_env_key("DHAN_ACCESS_TOKEN", token)
        self._log("✅  Credentials saved to .env")

    def _generate_token(self):
        self._save_creds()
        self.gen_btn.configure(state="disabled", text="⏳  Generating…")
        self._log("⏳  Generating token via TOTP…")
        def _run():
            try:
                from dhan_token_manager import load_config, get_fresh_token
                cfg   = load_config()
                token = get_fresh_token(cfg, force_new=True)
                _save_env_key("DHAN_ACCESS_TOKEN", token)
                cl = cfg["client_id"]
                def _done():
                    self.e_token.delete(0, "end"); self.e_token.insert(0, token)
                    self._log(f"✅  Token generated: {token[:28]}…")
                    self.on_token_saved(cl, token)
                    self.gen_btn.configure(state="normal", text="⚡  Generate Token")
                self.after(0, _done)
            except Exception as e:
                err = str(e)
                self.after(0, lambda: self._log(f"❌  Error: {err}"))
                self.after(0, lambda: self.gen_btn.configure(state="normal", text="⚡  Generate Token"))
        threading.Thread(target=_run, daemon=True).start()

    def _verify_token(self):
        self._log("🔍  Verifying token…")
        def _run():
            try:
                from dhan_token_manager import load_config, verify_token
                cfg = load_config()
                if verify_token(cfg["client_id"], cfg["access_token"]):
                    self.after(0, lambda: self._log("✅  Token VALID."))
                    self.after(0, lambda: self.on_token_saved(cfg["client_id"], cfg["access_token"]))
                else:
                    self.after(0, lambda: self._log("❌  Token INVALID or expired."))
            except Exception as e:
                err = str(e)
                self.after(0, lambda: self._log(f"❌  {err}"))
        threading.Thread(target=_run, daemon=True).start()

    def _check_shared_status(self):
        try:
            from dhan_token_manager import SHARED_TOKEN_FILE, read_shared_token
            shared = read_shared_token()
            if shared.get("access_token"):
                self.shared_lbl.configure(
                    text=f"✅  {shared['access_token'][:22]}…", text_color="#3fb950")
            elif SHARED_TOKEN_FILE.exists():
                self.shared_lbl.configure(text="⚠️  File empty/invalid", text_color=ORANGE_COL)
            else:
                self.shared_lbl.configure(text="Not found", text_color=GREY_COL)
        except Exception as e:
            self.shared_lbl.configure(text=f"Error: {e}", text_color=RED_COL)

    def _load_from_shared(self):
        try:
            from dhan_token_manager import read_shared_token, SHARED_TOKEN_FILE
            shared = read_shared_token()
            if not shared.get("access_token"):
                self._log(f"❌  No token found at {SHARED_TOKEN_FILE}")
                return
            token = shared["access_token"]
            cid   = shared.get("client_id", self.e_client.get().strip())
            self.e_token.delete(0, "end"); self.e_token.insert(0, token)
            _save_env_key("DHAN_ACCESS_TOKEN", token)
            if cid:
                _save_env_key("DHAN_CLIENT_ID", cid)
            self._log(f"✅  Loaded from token generator: {token[:28]}…")
            self.on_token_saved(cid, token)
            self._check_shared_status()
        except Exception as e:
            self._log(f"❌  {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  STRATEGY TAB
# ══════════════════════════════════════════════════════════════════════════════

class StrategyTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color=DARK_BG)
        self._client_id    = ""
        self._access_token = ""
        self._app          = None
        self._running      = False
        self._build()
        self._load_settings()

    def set_credentials(self, client_id, token):
        self._client_id    = client_id
        self._access_token = token

    def _build(self):
        # ── Top bar ───────────────────────────────────────────────────────────
        top = ctk.CTkFrame(self, fg_color=PANEL_BG, corner_radius=0)
        top.pack(fill="x")
        ctk.CTkLabel(top, text="📉  OI Positional Option Seller — NIFTY",
                     font=F_HEAD, text_color=WHITE_COL).pack(side="left", padx=20, pady=14)
        self.status_lbl = ctk.CTkLabel(
            top, text="⏹  Stopped", width=180, height=34,
            fg_color=CARD_BG, corner_radius=8, font=F_BTN, text_color=GREY_COL)
        self.status_lbl.pack(side="right", padx=20)

        # ── Row 1: Mode + Order Type ──────────────────────────────────────────
        row1 = ctk.CTkFrame(self, fg_color=CARD_BG, corner_radius=10)
        row1.pack(fill="x", padx=14, pady=(10, 4))

        ctk.CTkLabel(row1, text="Mode:", font=F_LABEL,
                     text_color=WHITE_COL).pack(side="left", padx=(16, 8), pady=10)
        self._mode_var = ctk.StringVar(value="PAPER")
        ctk.CTkRadioButton(row1, text="📄 Paper", variable=self._mode_var, value="PAPER",
                           font=F_LABEL, text_color=WHITE_COL,
                           fg_color=ACCENT, hover_color=ACCENT_H,
                           command=self._on_mode_change).pack(side="left", padx=6)
        ctk.CTkRadioButton(row1, text="🔴 Live",  variable=self._mode_var, value="LIVE",
                           font=F_LABEL, text_color=LIVE_COL,
                           fg_color=LIVE_COL, hover_color="#c0392b",
                           command=self._on_mode_change).pack(side="left", padx=6)
        self.live_warn = ctk.CTkLabel(row1, text="", font=("Segoe UI", 11, "bold"),
                                       text_color=LIVE_COL)
        self.live_warn.pack(side="left", padx=(4, 20))

        ctk.CTkLabel(row1, text="Order:", font=F_LABEL,
                     text_color=WHITE_COL).pack(side="left", padx=(4, 6))
        self.order_dd = ctk.CTkOptionMenu(
            row1, values=ORDER_TYPES, width=130, height=36,
            fg_color=PANEL_BG, button_color=BORDER, button_hover_color=ACCENT,
            text_color=WHITE_COL, font=F_LABEL, dropdown_font=F_LABEL,
            command=self._on_order_change)
        self.order_dd.set("LIMIT")
        self.order_dd.pack(side="left", padx=6)

        # Limit offset (shown only for LIMIT)
        self.lmt_frame = ctk.CTkFrame(row1, fg_color="transparent")
        ctk.CTkLabel(self.lmt_frame, text="Offset:", font=F_LABEL,
                     text_color=WHITE_COL).pack(side="left", padx=(10, 6))
        self.e_lmt = ctk.CTkEntry(self.lmt_frame, width=70, height=36,
                                   fg_color=PANEL_BG, border_color=BORDER,
                                   text_color=WHITE_COL, font=F_MONO_S)
        self.e_lmt.insert(0, "2.0")
        self.e_lmt.pack(side="left")
        ctk.CTkLabel(self.lmt_frame, text="pts", font=F_SMALL,
                     text_color=GREY_COL).pack(side="left", padx=(4, 0))
        self.lmt_frame.pack(side="left")

        # ── Row 2: Lot Multiplier ─────────────────────────────────────────────
        row2 = ctk.CTkFrame(self, fg_color=CARD_BG, corner_radius=10)
        row2.pack(fill="x", padx=14, pady=(0, 4))

        ctk.CTkLabel(row2, text="Lot Multiplier:", font=F_LABEL,
                     text_color=WHITE_COL).pack(side="left", padx=(16, 8), pady=10)
        self.lot_dd = ctk.CTkOptionMenu(
            row2, values=LOT_MULT_OPTS, width=80, height=36,
            fg_color=PANEL_BG, button_color=BORDER, button_hover_color=ACCENT,
            text_color=WHITE_COL, font=F_LABEL, dropdown_font=F_LABEL)
        self.lot_dd.set("1")
        self.lot_dd.pack(side="left", padx=6)

        ctk.CTkLabel(row2,
                     text="(lot size fetched live from Dhan instrument master)",
                     font=F_SMALL, text_color=GREY_COL).pack(side="left", padx=12)

        # ── Row 3: Strategy info labels ───────────────────────────────────────
        row3 = ctk.CTkFrame(self, fg_color=CARD_BG, corner_radius=10)
        row3.pack(fill="x", padx=14, pady=(0, 4))

        def _badge(parent, key, val, key_col=GREY_COL, val_col=CYAN_COL):
            ctk.CTkLabel(parent, text=key, font=F_SMALL,
                         text_color=key_col).pack(side="left", padx=(14, 2), pady=8)
            ctk.CTkLabel(parent, text=val, font=("Segoe UI", 11, "bold"),
                         text_color=val_col).pack(side="left", padx=(0, 10))

        _badge(row3, "Delta Range:",      "0.25 – 0.33")
        _badge(row3, "Sell Premium:",     "₹200 – ₹300")
        _badge(row3, "Hedge Premium:",    "₹50 – ₹90")
        _badge(row3, "Min Net Credit:",   "₹100")
        _badge(row3, "Stop Loss:",        "38.2%", val_col=RED_COL)
        _badge(row3, "Trail at:",         "50% decay", val_col=ORANGE_COL)
        _badge(row3, "Checks:",           "09:30 AM  &  15:20 PM")

        # ── Row 4: Buttons ────────────────────────────────────────────────────
        btn_row = ctk.CTkFrame(self, fg_color=CARD_BG, corner_radius=10)
        btn_row.pack(fill="x", padx=14, pady=(0, 8))

        self.start_btn = ctk.CTkButton(
            btn_row, text="▶  Start", width=140, height=38,
            fg_color=ACCENT, hover_color=ACCENT_H,
            text_color=WHITE_COL, font=F_BTN, command=self._start)
        self.start_btn.pack(side="left", padx=14, pady=10)

        self.stop_btn = ctk.CTkButton(
            btn_row, text="■  Stop", width=140, height=38,
            fg_color=RED_COL, hover_color=RED_H,
            text_color=WHITE_COL, font=F_BTN, state="disabled", command=self._stop)
        self.stop_btn.pack(side="left", padx=4)

        self.sqoff_btn = ctk.CTkButton(
            btn_row, text="⬛  Square Off All", width=200, height=38,
            fg_color=ORANGE_COL, hover_color="#b45309",
            text_color=WHITE_COL, font=F_BTN, state="disabled", command=self._square_off)
        self.sqoff_btn.pack(side="left", padx=12)

        ctk.CTkButton(
            btn_row, text="💾  Save Settings", width=160, height=38,
            fg_color=CARD_BG, hover_color=BORDER,
            text_color=CYAN_COL, font=F_BTN, command=self._save_settings
        ).pack(side="left", padx=8)

        self.info_lbl = ctk.CTkLabel(btn_row, text="", font=F_SMALL, text_color=GREY_COL)
        self.info_lbl.pack(side="left", padx=8)

        # ── Dashboard (monospace text area) ───────────────────────────────────
        ctk.CTkLabel(self, text="Live Dashboard", anchor="w",
                     font=("Segoe UI", 12, "bold"), text_color=GREY_COL
                     ).pack(padx=14, anchor="w", pady=(4, 0))

        self.dash = ctk.CTkTextbox(
            self, font=F_MONO, fg_color=PANEL_BG,
            text_color=WHITE_COL, border_color=BORDER, border_width=1, wrap="none")
        self.dash.pack(fill="both", expand=True, padx=14, pady=(2, 4))
        self.dash.configure(state="disabled")

        # ── Event Log ─────────────────────────────────────────────────────────
        ctk.CTkLabel(self, text="Event Log", anchor="w",
                     font=("Segoe UI", 12, "bold"), text_color=GREY_COL
                     ).pack(padx=14, anchor="w")
        self.event_log = ctk.CTkTextbox(
            self, height=140, font=F_MONO_S,
            fg_color=PANEL_BG, text_color=WHITE_COL,
            border_color=BORDER, border_width=1)
        self.event_log.pack(fill="x", padx=14, pady=(2, 12))
        self.event_log.configure(state="disabled")

    # ── Settings ──────────────────────────────────────────────────────────────

    def _collect_settings(self) -> dict:
        return {
            "mode":         self._mode_var.get(),
            "order_type":   self.order_dd.get(),
            "lmt_offset":   self.e_lmt.get(),
            "lot_mult":     self.lot_dd.get(),
        }

    def _apply_settings(self, s: dict):
        if s.get("mode") in ("PAPER", "LIVE"):
            self._mode_var.set(s["mode"]); self._on_mode_change()
        if s.get("order_type") in ORDER_TYPES:
            self.order_dd.set(s["order_type"]); self._on_order_change(s["order_type"])
        if s.get("lmt_offset"):
            self.e_lmt.delete(0, "end"); self.e_lmt.insert(0, str(s["lmt_offset"]))
        if s.get("lot_mult") in LOT_MULT_OPTS:
            self.lot_dd.set(s["lot_mult"])

    def _save_settings(self):
        try:
            SETTINGS_FILE.write_text(
                json.dumps(self._collect_settings(), indent=2), encoding="utf-8")
            self._elog("💾  Settings saved.")
        except Exception as e:
            self._elog(f"❌  Save failed: {e}")

    def _load_settings(self):
        if not SETTINGS_FILE.exists():
            return
        try:
            self._apply_settings(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_mode_change(self):
        self.live_warn.configure(
            text="⚠️  REAL ORDERS WILL BE PLACED"
            if self._mode_var.get() == "LIVE" else "")

    def _on_order_change(self, val):
        if val == "LIMIT":
            self.lmt_frame.pack(side="left")
        else:
            self.lmt_frame.pack_forget()

    # ── Start ─────────────────────────────────────────────────────────────────

    def _start(self):
        if self._running:
            return

        if not self._client_id or not self._access_token:
            env = _load_env()
            self._client_id    = env.get("DHAN_CLIENT_ID", "")
            self._access_token = env.get("DHAN_ACCESS_TOKEN", "")

        if not self._access_token:
            try:
                from dhan_token_manager import read_shared_token
                shared = read_shared_token()
                if shared.get("access_token"):
                    self._access_token = shared["access_token"]
                    if shared.get("client_id"):
                        self._client_id = shared["client_id"]
            except Exception:
                pass

        if not self._client_id or not self._access_token:
            messagebox.showerror("No Credentials", "Please generate a token in the Token Manager tab first.")
            return

        is_live = self._mode_var.get() == "LIVE"
        if is_live and not messagebox.askyesno(
            "⚠️  LIVE TRADING",
            "REAL orders will be placed on your Dhan account.\n\nThis strategy sells NIFTY options with margin.\n\nAre you sure?"
        ):
            return

        try:
            lot_mult    = int(self.lot_dd.get())
            lmt_offset  = float(self.e_lmt.get() or 2.0)
            order_type  = self.order_dd.get()
        except ValueError:
            messagebox.showerror("Invalid Settings", "Please check Lot Multiplier and Offset values.")
            return

        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.sqoff_btn.configure(state="normal")
        self.status_lbl.configure(
            text="⏳  Starting…",
            text_color=LIVE_COL if is_live else ORANGE_COL)
        self._elog(f"Starting — {'🔴 LIVE' if is_live else '📄 Paper'} | "
                   f"Lots={lot_mult} | Order={order_type} | Offset=₹{lmt_offset}")
        self.info_lbl.configure(
            text=f"{'🔴 LIVE' if is_live else '📄 Paper'}  |  Lots={lot_mult}  |  {order_type}")

        def _run():
            try:
                from main import OIOptionSellerApp
                self._app = OIOptionSellerApp(
                    client_id      = self._client_id,
                    access_token   = self._access_token,
                    live_mode      = is_live,
                    lot_multiplier = lot_mult,
                    order_type     = order_type,
                    limit_offset   = lmt_offset,
                )
                self._app.start()
                self._running = True
                run_col = LIVE_COL if is_live else "#3fb950"
                run_txt = "🔴  LIVE" if is_live else "🟢  Running"
                self.after(0, lambda: self.status_lbl.configure(text=run_txt, text_color=run_col))
                self.after(0, lambda: self._elog("✅  Strategy started."))
                self.after(0, self._poll_dashboard)
            except Exception as e:
                err = str(e)
                self._running = False
                self.after(0, lambda: self._elog(f"❌  {err}"))
                self.after(0, lambda: self.status_lbl.configure(text="❌  Error", text_color=RED_COL))
                self.after(0, lambda: self.start_btn.configure(state="normal"))
                self.after(0, lambda: self.stop_btn.configure(state="disabled"))
                self.after(0, lambda: self.sqoff_btn.configure(state="disabled"))

        threading.Thread(target=_run, daemon=True).start()

    # ── Stop ──────────────────────────────────────────────────────────────────

    def _stop(self):
        if not self._running or self._app is None:
            return
        self._running = False
        self.status_lbl.configure(text="⏹  Stopping…", text_color=ORANGE_COL)
        def _run():
            try:
                self._app.stop()
            except Exception:
                pass
            self._app = None
            self.after(0, lambda: self.status_lbl.configure(text="⏹  Stopped", text_color=GREY_COL))
            self.after(0, lambda: self.start_btn.configure(state="normal"))
            self.after(0, lambda: self.stop_btn.configure(state="disabled"))
            self.after(0, lambda: self.sqoff_btn.configure(state="disabled"))
            self.after(0, lambda: self.info_lbl.configure(text=""))
            self.after(0, lambda: self._elog("✅  Stopped."))
        threading.Thread(target=_run, daemon=True).start()

    # ── Square Off ────────────────────────────────────────────────────────────

    def _square_off(self):
        if not self._running or self._app is None:
            return
        is_live = getattr(self._app, "live_mode", False)
        msg = ("Close ALL LIVE NIFTY positions with MARKET orders?\n\nAre you sure?"
               if is_live else "Close ALL paper positions at current LTP?")
        if not messagebox.askyesno("Square Off All", msg):
            return
        self._elog("⬛  Squaring off all positions…")
        def _run():
            try:
                self._app.square_off_all()
                self.after(0, lambda: self._elog("✅  All positions squared off."))
            except Exception as e:
                err = str(e)
                self.after(0, lambda: self._elog(f"❌  {err}"))
        threading.Thread(target=_run, daemon=True).start()

    # ── Dashboard Rendering ───────────────────────────────────────────────────

    def _poll_dashboard(self):
        if not self._running or self._app is None:
            return
        try:
            snap = self._app.get_snapshot()
            self._render_dashboard(snap)
            # Mirror latest events to event log
            events = snap.get("events", [])
            if events:
                self._sync_event_log(events)
        except Exception as e:
            self._elog(f"Dashboard error: {e}")
        self.after(1500, self._poll_dashboard)

    def _sync_event_log(self, events: list):
        """Mirror app events into the GUI event log box."""
        self.event_log.configure(state="normal")
        self.event_log.delete("1.0", "end")
        for ev in reversed(events):
            self.event_log.insert("end", f"{ev}\n")
        self.event_log.see("end")
        self.event_log.configure(state="disabled")

    def _render_dashboard(self, snap: dict):
        lines = []
        now   = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
        mode  = "🔴 LIVE" if snap.get("live_mode") else "📄 Paper"
        W     = 110   # total dashboard width

        def div(char="─"):
            return char * W

        def _prem_bar(curr, sold, sl):
            """Visual bar showing premium progress toward SL."""
            if sold <= 0:
                return ""
            pct = min(1.0, curr / sl) if sl > 0 else 0
            filled = int(pct * 20)
            bar = "█" * filled + "░" * (20 - filled)
            return f"[{bar}]  {pct*100:.0f}% of SL"

        lines.append(div("═"))
        lines.append(
            f"  {mode}  │  NIFTY: {snap['nifty_ltp']:,.2f}  │  "
            f"Expiry: {snap['expiry']} ({snap['expiry_label']})  │  "
            f"Lot: {snap['lot_size']} × {snap['lot_multiplier']}  │  {now}"
        )
        lines.append(
            f"  Last Check: {snap['last_check']}  │  "
            f"Next Check: {snap['next_check']}  │  "
            f"Uptime: {_fmt_uptime(snap['uptime_secs'])}"
        )
        lines.append(div("═"))

        # ── CE Position ───────────────────────────────────────────────────────
        ce = snap.get("ce_position")
        lines.append("  📞 CALL OPTION (CE)")
        lines.append(div())
        if ce:
            qty      = int(ce["lot_size"]) * int(ce.get("lots", 1))
            net_cr   = ce["sold_premium"] - ce["hedge_premium"]
            trail    = "✓ TRAILED" if ce.get("trail_triggered") else ""
            lines.append(
                f"  Sold Strike:  {int(ce['strike'])} CE  │  "
                f"SecID: {ce['security_id']}  │  "
                f"Expiry: {ce.get('expiry', '—')}  │  "
                f"Entry: {ce.get('entry_time', '—')[:16]}"
            )
            lines.append(
                f"  Sold @ ₹{ce['sold_premium']:>8.2f}  │  "
                f"Current: ₹{ce['curr_prem']:>8.2f}  │  "
                f"SL: ₹{ce['sl_price']:>8.2f}  │  "
                f"Qty: {qty:>4}  {trail}"
            )
            lines.append(
                f"  Hedge:  {int(ce['hedge_strike'])} CE  │  "
                f"Bought @ ₹{ce['hedge_premium']:>7.2f}  │  "
                f"Current: ₹{ce['curr_hedge']:>7.2f}"
            )
            lines.append(
                f"  Net Credit: ₹{net_cr:>7.2f}  │  "
                f"MTM P&L: {'+'if ce['net_pnl']>=0 else ''}₹{ce['net_pnl']:>10,.2f}  │  "
                f"{_prem_bar(ce['curr_prem'], ce['sold_premium'], ce['sl_price'])}"
            )
            lines.append(f"  Signal: {snap.get('last_signal_ce', '—')}")
        else:
            lines.append(f"  No CE position open")
            lines.append(f"  Signal: {snap.get('last_signal_ce', '—')}")

        lines.append(div("═"))

        # ── PE Position ───────────────────────────────────────────────────────
        pe = snap.get("pe_position")
        lines.append("  📉 PUT OPTION (PE)")
        lines.append(div())
        if pe:
            qty      = int(pe["lot_size"]) * int(pe.get("lots", 1))
            net_cr   = pe["sold_premium"] - pe["hedge_premium"]
            trail    = "✓ TRAILED" if pe.get("trail_triggered") else ""
            lines.append(
                f"  Sold Strike:  {int(pe['strike'])} PE  │  "
                f"SecID: {pe['security_id']}  │  "
                f"Expiry: {pe.get('expiry', '—')}  │  "
                f"Entry: {pe.get('entry_time', '—')[:16]}"
            )
            lines.append(
                f"  Sold @ ₹{pe['sold_premium']:>8.2f}  │  "
                f"Current: ₹{pe['curr_prem']:>8.2f}  │  "
                f"SL: ₹{pe['sl_price']:>8.2f}  │  "
                f"Qty: {qty:>4}  {trail}"
            )
            lines.append(
                f"  Hedge:  {int(pe['hedge_strike'])} PE  │  "
                f"Bought @ ₹{pe['hedge_premium']:>7.2f}  │  "
                f"Current: ₹{pe['curr_hedge']:>7.2f}"
            )
            lines.append(
                f"  Net Credit: ₹{net_cr:>7.2f}  │  "
                f"MTM P&L: {'+'if pe['net_pnl']>=0 else ''}₹{pe['net_pnl']:>10,.2f}  │  "
                f"{_prem_bar(pe['curr_prem'], pe['sold_premium'], pe['sl_price'])}"
            )
            lines.append(f"  Signal: {snap.get('last_signal_pe', '—')}")
        else:
            lines.append(f"  No PE position open")
            lines.append(f"  Signal: {snap.get('last_signal_pe', '—')}")

        lines.append(div("═"))

        # ── Summary ───────────────────────────────────────────────────────────
        total_mtm = (ce["net_pnl"] if ce else 0) + (pe["net_pnl"] if pe else 0)
        realized  = snap.get("realized_pnl", 0)
        lines.append(
            f"  {'TOTAL MTM P&L:':20s}  "
            f"{'+'if total_mtm>=0 else ''}₹{total_mtm:>12,.2f}    │    "
            f"{'REALIZED P&L:':18s}  "
            f"{'+'if realized>=0 else ''}₹{realized:>12,.2f}"
        )
        lines.append(div("═"))
        lines.append(
            f"  Strategy: OI Positional Option Seller  │  "
            f"Instrument: NIFTY Monthly Options  │  "
            f"Order: {snap.get('order_type', 'LIMIT')}"
        )

        self.dash.configure(state="normal")
        self.dash.delete("1.0", "end")
        self.dash.insert("end", "\n".join(lines))
        self.dash.configure(state="disabled")

    # ── Log helper ────────────────────────────────────────────────────────────

    def _elog(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.event_log.configure(state="normal")
        self.event_log.insert("end", f"[{ts}]  {msg}\n")
        self.event_log.see("end")
        self.event_log.configure(state="disabled")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_uptime(secs: int) -> str:
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s"
    elif m > 0:
        return f"{m}m {s:02d}s"
    return f"{s}s"


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN WINDOW
# ══════════════════════════════════════════════════════════════════════════════

class MainApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("OI Option Seller  |  Balfund Trading Pvt. Ltd.")
        self.geometry("1440x960")
        self.minsize(1200, 780)
        self.configure(fg_color=DARK_BG)
        self._build()

    def _build(self):
        # Header bar
        hdr = ctk.CTkFrame(self, fg_color=PANEL_BG, corner_radius=0, height=52)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(
            hdr,
            text="  BALFUND TRADING PVT. LTD.  |  OI Positional Option Seller  |  NIFTY",
            font=("Segoe UI", 14, "bold"), text_color=CYAN_COL
        ).pack(side="left", padx=18)
        ctk.CTkLabel(
            hdr,
            text="Signal Source: Open Interest + Premium  │  Use Live Mode with caution",
            font=F_SMALL, text_color=GREY_COL
        ).pack(side="right", padx=18)

        # Tab view
        tabs = ctk.CTkTabview(
            self, fg_color=DARK_BG,
            segmented_button_fg_color=PANEL_BG,
            segmented_button_selected_color=ACCENT,
            segmented_button_unselected_color=PANEL_BG,
            segmented_button_selected_hover_color=ACCENT_H,
            text_color=WHITE_COL,
        )
        tabs.pack(fill="both", expand=True)
        tabs.add("🔑  Token Manager")
        tabs.add("📉  OI Strategy")

        self.strategy_tab = StrategyTab(tabs.tab("📉  OI Strategy"))
        self.strategy_tab.pack(fill="both", expand=True)

        self.token_tab = TokenTab(
            tabs.tab("🔑  Token Manager"),
            on_token_saved=self._on_token_saved,
        )
        self.token_tab.pack(fill="both", expand=True)

    def _on_token_saved(self, client_id, token):
        self.strategy_tab.set_credentials(client_id, token)

    def on_closing(self):
        if self.strategy_tab._running and self.strategy_tab._app:
            self.strategy_tab._app.stop()
        self.destroy()


if __name__ == "__main__":
    app = MainApp()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()
