"""OW Queue for Windows: watches your Overwatch queue and pushes it to your iPhone and Apple Watch."""
from __future__ import annotations

import ctypes
import json
import logging
import logging.handlers
import os
import secrets
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox

import qrcode

from detector import FOUND, QUEUEING, Detector
from presence import Presence
from relay import Relay
from roleselect import RoleSelect

POLL_SECONDS = 1.0
GUI_REFRESH_MS = 500

BG = "#0f1115"
CARD = "#181b22"
TEXT = "#f2f3f5"
MUTED = "#8a8f98"
GOOD = "#34c759"
WARN = "#ff9f0a"
MODE_COLORS = {
    "quickPlay": "#3b94f6",
    "competitive": "#ec477e",
    "arcade": "#4dcc84",
    "stadium": "#ffc74c",
    "mysteryHeroes": "#a376f4",
    "custom": "#9aa0a6",
}
MODE_NAMES = {
    "quickPlay": "Quick Play",
    "competitive": "Competitive",
    "arcade": "Arcade",
    "stadium": "Stadium",
    "mysteryHeroes": "Mystery Heroes",
    "custom": "Custom Game",
}

DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "OWQueue")
PAIRING_FILE = os.path.join(DATA_DIR, "pairing.json")

log = logging.getLogger("owqueue")


def setup_logging():
    log.addHandler(logging.StreamHandler(sys.stdout))
    log.setLevel(logging.INFO)
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except OSError:
        return
    handler = logging.handlers.RotatingFileHandler(
        os.path.join(DATA_DIR, "owqueue.log"), maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    log.addHandler(handler)


def load_pair_id():
    try:
        with open(PAIRING_FILE, encoding="utf-8") as handle:
            pair_id = json.load(handle).get("id", "")
        if len(pair_id) == 32 and all(c in "0123456789abcdef" for c in pair_id):
            return pair_id
    except (OSError, ValueError, AttributeError):
        pass
    try:
        return new_pair_id()
    except OSError as problem:
        log.warning("Couldn't save the pairing code, it will change on restart: %s", problem)
        return secrets.token_hex(16)


def new_pair_id():
    pair_id = secrets.token_hex(16)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PAIRING_FILE + ".tmp", "w", encoding="utf-8") as handle:
        json.dump({"id": pair_id}, handle)
    os.replace(PAIRING_FILE + ".tmp", PAIRING_FILE)
    return pair_id


def clock(seconds):
    seconds = int(seconds)
    if seconds >= 3600:
        return "%d:%02d:%02d" % (seconds // 3600, seconds // 60 % 60, seconds % 60)
    return "%d:%02d" % (seconds // 60, seconds % 60)


class Watcher:
    """Reads Battle.net once a second on a background thread and reports state changes."""

    def __init__(self, relay):
        self.relay = relay
        self.presence = Presence(log=log.info)
        self.role_select = RoleSelect(os.path.join(DATA_DIR, "icons"), log=log.info)
        self.detector = Detector(time.monotonic())
        self.lock = threading.Lock()
        self._sent = None

    def run(self, stop):
        while not stop.is_set():
            began = time.monotonic()
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                log.exception("Watcher tick failed")
            stop.wait(max(0.1, POLL_SECONDS - (time.monotonic() - began)))

    def tick(self):
        reading, mode = self.presence.read()
        role_select = self.role_select.visible() if self.detector.wants_role_check(reading, mode) else None
        now = time.monotonic()
        with self.lock:
            before = (self.detector.state, self.detector.mode, self.detector.holding_for_role_select)
            self.detector.step(now, reading, mode, role_select)
            after = (self.detector.state, self.detector.mode, self.detector.holding_for_role_select)
            elapsed = self.detector.elapsed(now)
        if before != after:
            log.info("Presence %s/%s, role select %s -> %s", reading, mode, role_select, after)
        key = after[:2]
        if key[0] is not None and key != self._sent:
            self._sent = key
            self.relay.publish(key[0], key[1], elapsed)

    def snapshot(self):
        with self.lock:
            d = self.detector
            return d.state, d.mode, d.elapsed(time.monotonic()), d.holding_for_role_select, self.presence.connected


class App:
    def __init__(self, root, pair_id, relay, watcher):
        self.root = root
        self.pair_id = pair_id
        self.relay = relay
        self.watcher = watcher
        self._qr_for = None

        root.title("OW Queue")
        root.configure(bg=BG)
        root.resizable(False, False)

        frame = tk.Frame(root, bg=BG, padx=24, pady=20)
        frame.pack()

        card = tk.Frame(frame, bg=CARD, padx=20, pady=16)
        card.pack(fill="x")
        self.state_label = tk.Label(card, font=("Segoe UI Semibold", 20), bg=CARD, fg=TEXT, width=18, anchor="w")
        self.state_label.pack(anchor="w")
        self.mode_label = tk.Label(card, font=("Segoe UI", 12), bg=CARD, fg=MUTED, anchor="w")
        self.mode_label.pack(anchor="w")
        self.timer_label = tk.Label(card, font=("Segoe UI Light", 36), bg=CARD, fg=TEXT, anchor="w")
        self.timer_label.pack(anchor="w")

        self.bnet_label = tk.Label(frame, font=("Segoe UI", 10), bg=BG, anchor="w", justify="left", wraplength=300)
        self.bnet_label.pack(fill="x", pady=(14, 0))
        self.phone_label = tk.Label(frame, font=("Segoe UI", 10), bg=BG, anchor="w", justify="left", wraplength=300)
        self.phone_label.pack(fill="x", pady=(4, 0))
        self.server_label = tk.Label(frame, font=("Segoe UI", 10), bg=BG, fg=WARN, anchor="w")
        self.server_label.pack(fill="x", pady=(4, 0))

        self.qr = tk.Canvas(frame, width=232, height=232, bg="white", highlightthickness=0)
        self.qr.pack(pady=(14, 0))

        self.button = tk.Button(frame, text="Reset QR code", command=self.reset, font=("Segoe UI", 10),
                  bg=CARD, fg=TEXT, activebackground="#262a33", activeforeground=TEXT,
                  relief="flat", padx=12, pady=6, cursor="hand2")
        self.button.pack(pady=(14, 0))

        self.refresh()

    def refresh(self):
        try:
            self._render()
        except Exception:  # noqa: BLE001
            log.exception("GUI refresh failed")
        self.root.after(GUI_REFRESH_MS, self.refresh)

    def _render(self):
        state, mode, elapsed, holding, connected = self.watcher.snapshot()
        color = MODE_COLORS.get(mode, TEXT)
        if state == QUEUEING:
            self.state_label.config(text="In queue", fg=color)
            self.timer_label.config(text=clock(elapsed), fg=TEXT)
        elif state == FOUND:
            self.state_label.config(text="Match found!", fg=GOOD)
            self.timer_label.config(text=clock(elapsed), fg=MUTED)
        elif holding:
            self.state_label.config(text="Picking roles…", fg=color)
            self.timer_label.config(text="–:––", fg=MUTED)
        else:
            self.state_label.config(text="Not in queue", fg=TEXT)
            self.timer_label.config(text="–:––", fg=MUTED)
        self.mode_label.config(text=MODE_NAMES.get(mode, "Overwatch") if state in (QUEUEING, FOUND) or holding else " ")

        if connected:
            self.bnet_label.config(text="● Battle.net connected", fg=GOOD)
        else:
            self.bnet_label.config(text="● Battle.net not found — open it and log in", fg=WARN)

        if self.relay.phone_paired:
            self.phone_label.config(text="● iPhone paired", fg=GOOD)
            self.qr.pack_forget()
        else:
            self.phone_label.config(text="Scan this code with the OW Queue app on your iPhone", fg=MUTED)
            if not self.qr.winfo_ismapped():
                self.qr.pack(pady=(14, 0), before=self.button)
            if self._qr_for != self.pair_id:
                self._draw_qr()

        self.server_label.config(text="" if self.relay.reachable else "Can't reach the notification server — retrying")

    def _draw_qr(self):
        code = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
        code.add_data("owq://pair?id=%s" % self.pair_id)
        code.make(fit=True)
        matrix = code.get_matrix()
        cell = 232 // len(matrix)
        offset = (232 - cell * len(matrix)) // 2
        self.qr.delete("all")
        for y, row in enumerate(matrix):
            for x, dark in enumerate(row):
                if dark:
                    x0, y0 = offset + x * cell, offset + y * cell
                    self.qr.create_rectangle(x0, y0, x0 + cell, y0 + cell, fill="black", width=0)
        self._qr_for = self.pair_id

    def reset(self):
        if not messagebox.askyesno("Reset QR code", "Make a new pairing code?\n\n"
                                   "Your iPhone will stop getting updates until it scans the new code."):
            return
        try:
            self.pair_id = new_pair_id()
        except OSError as problem:
            messagebox.showerror("Reset QR code", "Couldn't save the new code: %s" % problem)
            return
        log.info("Pairing reset")
        self.relay.change_pair(self.pair_id)


def main():
    setup_logging()
    if os.name == "nt":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:  # noqa: BLE001
            pass
    pair_id = load_pair_id()
    relay = Relay(pair_id, log=log.info)
    watcher = Watcher(relay)
    stop = threading.Event()
    threading.Thread(target=relay.run, args=(stop,), daemon=True).start()
    threading.Thread(target=watcher.run, args=(stop,), daemon=True).start()
    threading.Thread(target=watcher.role_select.prefetch, daemon=True).start()

    root = tk.Tk()
    App(root, pair_id, relay, watcher)

    def close():
        stop.set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    root.mainloop()


if __name__ == "__main__":
    main()
