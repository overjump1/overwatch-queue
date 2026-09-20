"""OW Queue for Windows: watches your Overwatch queue and pushes it to your iPhone and Apple Watch."""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import secrets
import sys
import threading
import time

import qrcode
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap
from PyQt6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                             QVBoxLayout, QWidget)

from detector import FOUND, QUEUEING, Detector
from presence import Presence
from relay import Relay
from roleselect import RoleSelect

POLL_SECONDS = 1.0
GUI_REFRESH_MS = 500
HEARTBEAT_SECONDS = 60
# Matches the worker: "Match found!" becomes "In a match" this long after the match is found.
PLAYING_AFTER_SECONDS = 60
QR_SIZE = 232
WINDOW_WIDTH = 360

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
    if sys.stdout:  # None in the installed build, which has no console
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
        self._sent_at = 0.0

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
            since_found = self.detector.since_found(now)
        if before != after:
            log.info("Presence %s/%s, role select %s -> %s at %ds", reading, mode, role_select,
                     after, int(elapsed))
        key = after[:2]
        # Re-sent every minute, with fresh times, so the worker knows the PC is still there.
        if key[0] is not None and (key != self._sent or now - self._sent_at >= HEARTBEAT_SECONDS):
            self._sent, self._sent_at = key, now
            self.relay.publish(key[0], key[1], elapsed, since_found)

    def snapshot(self):
        with self.lock:
            d = self.detector
            now = time.monotonic()
            return (d.state, d.mode, d.elapsed(now), d.since_found(now), d.holding_for_role_select,
                    self.presence.connected)


def _font(size, weight=QFont.Weight.Normal):
    font = QFont("Segoe UI", size)
    font.setWeight(weight)
    return font


class App(QWidget):
    def __init__(self, pair_id, relay, watcher):
        super().__init__()
        self.pair_id = pair_id
        self.relay = relay
        self.watcher = watcher
        self._qr_for = None
        self._qr_requested_with = None  # the phones paired when "Show QR code" was pressed, None when not pressed

        self.setWindowTitle("OW Queue")
        self.setStyleSheet(
            "QWidget { background: %s; color: %s; }"
            "QFrame#card { background: %s; border-radius: 12px; }"
            "QFrame#card QLabel { background: transparent; }"
            "QPushButton { background: %s; color: %s; border: none; border-radius: 8px; padding: 8px 16px; }"
            "QPushButton:hover { background: #262a33; }" % (BG, TEXT, CARD, CARD, TEXT))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(4)

        card = QFrame(objectName="card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 16, 20, 16)
        card_layout.setSpacing(2)
        self.state_label = QLabel(font=_font(20, QFont.Weight.DemiBold))
        self.mode_label = QLabel(font=_font(12))
        self.timer_label = QLabel(font=_font(36, QFont.Weight.Light))
        for label in (self.state_label, self.mode_label, self.timer_label):
            card_layout.addWidget(label)
        layout.addWidget(card)
        layout.addSpacing(10)

        self.bnet_label = QLabel(font=_font(10), wordWrap=True)
        self.phone_label = QLabel(font=_font(10), wordWrap=True)
        self.server_label = QLabel(font=_font(10), wordWrap=True)
        for label in (self.bnet_label, self.phone_label, self.server_label):
            layout.addWidget(label)

        self.qr = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.qr.setFixedSize(QR_SIZE, QR_SIZE)
        layout.addSpacing(10)
        layout.addWidget(self.qr, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.show_button = QPushButton("Show QR code", font=_font(10))
        self.show_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.show_button.clicked.connect(self.toggle_qr)
        self.button = QPushButton("Reset QR code", font=_font(10))
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.clicked.connect(self.reset)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch()
        buttons.addWidget(self.show_button)
        buttons.addWidget(self.button)
        buttons.addStretch()
        layout.addSpacing(10)
        layout.addLayout(buttons)

        self.timer = QTimer(self, interval=GUI_REFRESH_MS, timeout=self.refresh)
        self.timer.start()
        self.refresh()

    def refresh(self):
        try:
            self._render()
        except Exception:  # noqa: BLE001
            log.exception("GUI refresh failed")
        # Fixed width so the window doesn't jump around as the text changes; the height follows.
        self.setFixedSize(WINDOW_WIDTH, self.layout().totalHeightForWidth(WINDOW_WIDTH))

    @staticmethod
    def _set(label, text, color):
        label.setText(text)
        label.setStyleSheet("color: %s;" % color)

    def _render(self):
        state, mode, elapsed, since_found, holding, connected = self.watcher.snapshot()
        color = MODE_COLORS.get(mode, TEXT)
        mode_name = MODE_NAMES.get(mode, "Overwatch")
        if state == QUEUEING:
            self._set(self.state_label, "In queue", color)
            self._set(self.mode_label, mode_name, MUTED)
            self._set(self.timer_label, clock(elapsed), TEXT)
        elif state == FOUND and since_found >= PLAYING_AFTER_SECONDS:
            self._set(self.state_label, "In a match", color)
            self._set(self.mode_label, "%s · good luck, have fun!" % mode_name, MUTED)
            self._set(self.timer_label, clock(since_found), TEXT)
        elif state == FOUND:
            self._set(self.state_label, "Match found!", GOOD)
            self._set(self.mode_label, "%s · waited" % mode_name, MUTED)
            self._set(self.timer_label, clock(elapsed), MUTED)
        elif holding:
            self._set(self.state_label, "Picking roles…", color)
            self._set(self.mode_label, mode_name, MUTED)
            self._set(self.timer_label, "–:––", MUTED)
        else:
            self._set(self.state_label, "Not in queue", TEXT)
            self._set(self.mode_label, " ", MUTED)
            self._set(self.timer_label, "–:––", MUTED)

        if connected:
            self._set(self.bnet_label, "● Battle.net connected", GOOD)
        else:
            self._set(self.bnet_label, "● Battle.net not found — open it and log in", WARN)

        paired = list(self.relay.paired)
        if self._qr_requested_with is not None and not set(paired) <= set(self._qr_requested_with):
            self._qr_requested_with = None  # another phone just paired, so the code has done its job
        show_qr = not paired or self._qr_requested_with is not None
        if paired and show_qr:
            self._set(self.phone_label, "● %s paired — scan this code with another phone to add it"
                      % " and ".join(paired), GOOD)
        elif paired:
            self._set(self.phone_label, "● %s paired" % " and ".join(paired), GOOD)
        else:
            self._set(self.phone_label, "Scan this code with the OW Queue app on your phone", MUTED)
        if show_qr and self._qr_for != self.pair_id:
            self._draw_qr()
        self.qr.setVisible(show_qr)
        self.show_button.setText("Hide QR code" if show_qr else "Show QR code")
        self.show_button.setVisible(bool(paired))
        self.relay.expect_phone(show_qr)

        self._set(self.server_label, "Can't reach the notification server — retrying", WARN)
        self.server_label.setVisible(not self.relay.reachable)

    def _draw_qr(self):
        code = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
        code.add_data("owq://pair?id=%s" % self.pair_id)
        code.make(fit=True)
        matrix = code.get_matrix()
        ratio = self.devicePixelRatioF()
        pixmap = QPixmap(round(QR_SIZE * ratio), round(QR_SIZE * ratio))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(QColor("white"))
        cell = QR_SIZE // len(matrix)
        offset = (QR_SIZE - cell * len(matrix)) // 2
        painter = QPainter(pixmap)
        for y, row in enumerate(matrix):
            for x, dark in enumerate(row):
                if dark:
                    painter.fillRect(offset + x * cell, offset + y * cell, cell, cell, QColor("black"))
        painter.end()
        self.qr.setPixmap(pixmap)
        self._qr_for = self.pair_id

    def toggle_qr(self):
        self._qr_requested_with = None if self._qr_requested_with is not None else list(self.relay.paired)
        self.refresh()

    def reset(self):
        answer = QMessageBox.question(self, "Reset QR code", "Make a new pairing code?\n\n"
                                      "Your phone will stop getting updates until it scans the new code.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.pair_id = new_pair_id()
        except OSError as problem:
            QMessageBox.critical(self, "Reset QR code", "Couldn't save the new code: %s" % problem)
            return
        log.info("Pairing reset")
        self._qr_requested_with = None
        self.relay.change_pair(self.pair_id)
        self.refresh()


def main():
    setup_logging()
    pair_id = load_pair_id()
    relay = Relay(pair_id, log=log.info)
    watcher = Watcher(relay)
    stop = threading.Event()
    threading.Thread(target=relay.run, args=(stop,), daemon=True).start()
    threading.Thread(target=watcher.run, args=(stop,), daemon=True).start()
    threading.Thread(target=watcher.role_select.prefetch, daemon=True).start()

    app = QApplication(sys.argv)
    app.aboutToQuit.connect(stop.set)
    window = App(pair_id, relay, watcher)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
