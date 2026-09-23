"""OverQueue for Windows: watches your Overwatch queue and pushes it to your iPhone and Apple Watch.

Battle.net is asked what you're doing over its own debug port, which means starting it in
developer mode (`--remote-debugging-port`) -- see `devmode`. `--presence-source` picks
something else: `auto` uses the port only if Battle.net already has one open and never
starts or restarts Battle.net itself. Either way this app only reads, and Overwatch is
never touched.
"""
from __future__ import annotations

import argparse
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

import devmode
import updater as up
from detector import FOUND, QUEUEING, Detector
from relay import Relay
from roleselect import RoleSelect
from updater import Updater
from channel import CURRENT, REAL
from version import VERSION

POLL_SECONDS = 1.0
GUI_REFRESH_MS = 500
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

_APPDATA = os.environ.get("APPDATA") or os.path.expanduser("~")
DATA_DIR = CURRENT.data_dir
# The app was called OW Queue before this. Its folder holds the pairing code every paired
# phone was scanned against, so the folder moves across rather than leaving everyone to scan
# a new one; after that the old name is never looked at again.
LEGACY_DATA_DIR = os.path.join(_APPDATA, "OWQueue")
PAIRING_FILE = CURRENT.pairing_file

log = logging.getLogger("overqueue")


def adopt_legacy_data_dir():
    """Moves the old folder over, once, before anything reads or writes the new one."""
    # Only the real app ever went by the old name; OverQueue Dev starts a pairing of its own.
    if CURRENT != REAL or os.path.exists(DATA_DIR) or not os.path.isdir(LEGACY_DATA_DIR):
        return
    try:
        os.rename(LEGACY_DATA_DIR, DATA_DIR)
    except OSError:
        pass  # A new pairing code is a nuisance, not a reason not to start.


def setup_logging():
    if sys.stdout:  # None in the installed build, which has no console
        log.addHandler(logging.StreamHandler(sys.stdout))
    log.setLevel(logging.INFO)
    adopt_legacy_data_dir()
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except OSError:
        return
    handler = logging.handlers.RotatingFileHandler(
        os.path.join(DATA_DIR, "overqueue.log"), maxBytes=1_000_000, backupCount=2, encoding="utf-8")
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
    """Reads Battle.net once a second on a background thread and reports state changes.
    Only changes: the worker keeps whatever it was last told until it's told something else,
    so a fifteen-minute queue costs one request."""

    def __init__(self, relay, presence):
        self.relay = relay
        self.presence = presence
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
            since_found = self.detector.since_found(now)
        if before != after:
            log.info("Presence %s/%s, role select %s -> %s at %ds", reading, mode, role_select,
                     after, int(elapsed))
        key = after[:2]
        if key[0] is not None and key != self._sent:
            self._sent = key
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


def _update_button(state, version, percent):
    """The footer button's text, colour, and whether pressing it does anything."""
    if state == up.CHECKING:
        return "Checking…", MUTED, False
    if state == up.UP_TO_DATE:
        return "Up to date", MUTED, True
    if state == up.AVAILABLE:
        return "Update to %s" % version, GOOD, True
    if state == up.DOWNLOADING:
        return "Downloading… %d%%" % percent, MUTED, False
    if state == up.FAILED:
        return "Update failed — retry", WARN, True
    return "Check for updates", MUTED, True


class App(QWidget):
    def __init__(self, pair_id, relay, watcher, updater):
        super().__init__()
        self.pair_id = pair_id
        self.relay = relay
        self.watcher = watcher
        self.updater = updater
        self._qr_for = None
        self._restarting = False
        self._qr_requested_with = None  # the phones paired when "Show QR code" was pressed, None when not pressed
        self._checks_updates = up.checks_for_updates()

        self.setWindowTitle(CURRENT.name)
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

        self.restart_button = QPushButton("Restart Battle.net", font=_font(10))
        self.restart_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.restart_button.clicked.connect(self.restart_battlenet)
        restart_row = QHBoxLayout()
        restart_row.addStretch()
        restart_row.addWidget(self.restart_button)
        restart_row.addStretch()
        layout.addSpacing(8)
        layout.addLayout(restart_row)

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

        self.version_label = QLabel("%s %s" % (CURRENT.name, VERSION), font=_font(9))
        self.version_label.setStyleSheet("color: %s;" % MUTED)
        self.update_button = QPushButton(font=_font(9))
        self.update_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_button.clicked.connect(self.updater.press)
        # Builds from source never check, so there's nothing for the button to do.
        self.update_button.setVisible(self._checks_updates)
        footer = QHBoxLayout()
        footer.addWidget(self.version_label)
        footer.addStretch()
        footer.addWidget(self.update_button)
        layout.addSpacing(10)
        layout.addLayout(footer)

        disclaimer = QLabel("Not affiliated with Overwatch or Blizzard Entertainment.", font=_font(8))
        disclaimer.setStyleSheet("color: %s;" % MUTED)
        disclaimer.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(6)
        layout.addWidget(disclaimer)

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

        self.restart_button.setVisible(self.watcher.presence.debug is not None)
        self.restart_button.setEnabled(not self._restarting)
        self.restart_button.setText("Restarting Battle.net…" if self._restarting
                                    else "Restart Battle.net")

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
            self._set(self.phone_label, "Scan this code with the %s app on your phone" % CURRENT.name, MUTED)
        if show_qr and self._qr_for != self.pair_id:
            self._draw_qr()
        self.qr.setVisible(show_qr)
        self.show_button.setText("Hide QR code" if show_qr else "Show QR code")
        self.show_button.setVisible(bool(paired))
        self.relay.expect_phone(show_qr)

        self._set(self.server_label, "Can't reach the notification server — retrying", WARN)
        self.server_label.setVisible(not self.relay.reachable)

        self._render_update()

    def _render_update(self):
        if not self._checks_updates:
            return
        state, version, percent = self.updater.snapshot()
        if state == up.INSTALLING:
            return QApplication.instance().quit()  # the installer closes us and starts us again
        text, color, enabled = _update_button(state, version, percent)
        self.update_button.setEnabled(enabled)
        if self.update_button.text() == text:
            return
        self.update_button.setText(text)
        self.update_button.setStyleSheet(
            "QPushButton { color: %s; background: transparent; border: none; padding: 2px 4px; }"
            "QPushButton:hover { background: #262a33; }" % color)

    def _draw_qr(self):
        code = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
        code.add_data("%s://pair?id=%s" % (CURRENT.pair_scheme, self.pair_id))
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

    def restart_battlenet(self):
        """Closes Battle.net and reopens it with its debug port on. Off the GUI thread,
        because it waits for Battle.net to go away and come back; the 500ms refresh picks
        the button back up when it's done. No confirmation: the button says what it does,
        and a Battle.net restart leaves a running game alone."""
        if self._restarting:
            return
        self._restarting = True
        self.refresh()
        threading.Thread(target=self._restart, daemon=True, name="battlenet-restart").start()

    def _restart(self):
        try:
            self.watcher.presence.restart()
        except Exception:  # noqa: BLE001 - a button press must never take the app down
            log.exception("Restarting Battle.net failed")
        finally:
            self._restarting = False

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


class _Parser(argparse.ArgumentParser):
    """Complains by raising rather than by printing or exiting: the installed build has no
    console at all, so `--help` or a stray argument would otherwise be a silent death on
    startup -- argparse writing to a `sys.stdout` that is None."""

    def error(self, message):
        raise ValueError(message)

    def exit(self, status=0, message=None):
        raise ValueError(message or "nothing to do but print and quit")

    def _print_message(self, message, file=None):
        if (file or sys.stdout) is not None:
            super()._print_message(message, file)


def parse_args(argv=None):
    parser = _Parser(prog=CURRENT.name, description=__doc__.splitlines()[0])
    parser.add_argument("--presence-source", choices=("devmode", "auto"), default="devmode",
                        help="how to read Battle.net's presence (default: start Battle.net in "
                             "developer mode and read over its debug port; 'auto' uses a debug "
                             "port only if one is already open)")
    parser.add_argument("--battlenet-port", type=int, default=devmode.DEBUG_PORT,
                        help="the --remote-debugging-port to start Battle.net with (default: %d)"
                             % devmode.DEBUG_PORT)
    try:
        return parser.parse_args(argv)
    except ValueError as problem:
        log.warning("Ignoring the command line: %s", problem)
        return parser.parse_args([])


def main():
    setup_logging()
    args = parse_args()
    pair_id = load_pair_id()
    relay = Relay(pair_id, log=log.info)
    reader = devmode.Reader(args.presence_source, args.battlenet_port, log=log.info)
    watcher = Watcher(relay, reader)
    updater = Updater(DATA_DIR, log=log.info)
    stop = threading.Event()
    threading.Thread(target=relay.run, args=(stop,), daemon=True).start()
    threading.Thread(target=watcher.run, args=(stop,), daemon=True).start()
    threading.Thread(target=watcher.role_select.prefetch, daemon=True).start()
    threading.Thread(target=updater.run, args=(stop,), daemon=True).start()
    # Off the GUI thread: it waits out a Battle.net that's still starting, then a restart.
    threading.Thread(target=reader.start, args=(stop,), daemon=True, name="battlenet-devmode").start()

    app = QApplication(sys.argv)
    app.aboutToQuit.connect(stop.set)
    window = App(pair_id, relay, watcher, updater)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
