"""The control panel window: pair a phone, then watch (or hand-drive) the queue.

This replaces the app's old in-app debug panel — same idea, other end of the wire. Two
pages: pair a phone with a QR code, then the state of the queue once one is connected.
Vision and Battle.net presence run automatically whenever they're available; `run.py`'s
`--no-vision` / `--no-queue-vision` / `--no-presence` flags are the only way to turn them
off.

What a button *means* lives in `controls.py`; this file is the window around it. Socket
callbacks arrive on their own threads, so they come in over Qt signals — the only safe
way to reach a widget from another thread.
"""
from __future__ import annotations

import threading
import time

from PyQt6.QtCore import Qt, QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QPixmap
from PyQt6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QListWidget,
                             QMainWindow, QMessageBox, QPushButton, QSizePolicy,
                             QStackedWidget, QVBoxLayout, QWidget)

from . import bnetpresence, protocol, qr, queuepresence, queuevision, queuewatch
from .controls import Controls
from .pairing import local_addresses

QR_SCALE = 6

QUEUE_STATE_NAMES = {
    queuewatch.SEARCHING_MENU: "Searching, in the menu",
    queuewatch.SEARCHING_IN_GAME: "Searching, in a game",
    queuewatch.SEARCHING_HIDDEN: "Searching, banner out of sight",
    queuewatch.GAME_FOUND: "Game found",
}

PRESENCE_STATE_NAMES = {
    queuepresence.QUEUEING: "In a queue",
    queuepresence.IN_GAME: "In a game",
    queuepresence.MENUS: "In the menus",
    queuepresence.PLAYING_OTHER: "Playing something else in Overwatch",
    queuepresence.ELSEWHERE: "Playing something other than Overwatch",
}

PHASES = ["idle", "searching", "matchFound", "mapVote", "heroSelect", "inGame", "cancelled"]
PHASE_NAMES = {"idle": "Idle", "searching": "Searching", "matchFound": "Match found",
               "mapVote": "Map vote", "heroSelect": "Hero select", "inGame": "In game",
               "cancelled": "Cancelled"}

CARD_WIDTH = 400
WINDOW_SIZE = (480, 700)

# The app's palette, so the two halves of the project look like one thing.
NIGHT = "#080b14"
DEEP_BLUE = "#0d1a30"
PANEL = "#132140"
PANEL_LIGHT = "#182747"
ORANGE = "#f99e1a"
WHITE = "#f6f8fc"
MUTED = "#8b95ab"
BORDER = "#22335a"

STYLE = """
QMainWindow, QWidget { background: %(night)s; color: %(white)s; font-size: 13px; }
/* Labels sit inside the panels, so they must not paint the window colour over them. */
QLabel { background: transparent; }
QFrame#header {
    background: %(deep)s; border-bottom: 1px solid %(border)s;
}
QLabel#brand { color: %(white)s; font-weight: 700; letter-spacing: 3px; font-size: 12px; }
QFrame#card {
    border: 1px solid %(border)s; border-radius: 18px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 %(panelLight)s, stop:1 %(deep)s);
}
QFrame#qrFrame { background: #fbfbfe; border-radius: 14px; }
QPushButton {
    background: %(panel)s; border: 1px solid %(border)s; border-radius: 9px;
    padding: 10px 18px; min-height: 20px; color: %(white)s; font-weight: 500;
}
QPushButton:hover:!disabled { border-color: %(orange)s; }
QPushButton:pressed:!disabled { background: #1a2947; }
QPushButton#primary { background: %(orange)s; border-color: %(orange)s; color: #201400;
                      font-weight: 700; }
QPushButton#primary:hover { background: #ffb340; }
QPushButton#danger { color: #e7ecf7; }
QPushButton#danger:hover { border-color: #ea6e53; color: #ea6e53; }
QPushButton#link {
    background: transparent; border: none; color: %(muted)s; text-decoration: underline;
    padding: 4px; font-weight: 500;
}
QPushButton#link:hover { color: %(orange)s; }
QComboBox, QLineEdit {
    background: %(panel)s; border: 1px solid %(border)s; border-radius: 9px;
    padding: 8px 12px; color: %(white)s; selection-background-color: %(orange)s;
}
QComboBox:focus, QLineEdit:focus { border-color: %(orange)s; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background: %(panel)s; color: %(white)s; border: 1px solid %(border)s;
    selection-background-color: %(orange)s; selection-color: #201400;
    outline: none; padding: 4px;
}
QListWidget {
    background: %(panel)s; border: 1px solid %(border)s; border-radius: 12px;
    padding: 8px; color: %(white)s;
}
QListWidget::item { padding: 4px 2px; }
QLabel#muted { color: %(muted)s; }
QLabel#eyebrow {
    color: %(orange)s; font-weight: 700; letter-spacing: 2px; font-size: 11px;
}
QFrame#divider { background: %(border)s; max-height: 1px; min-height: 1px; border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: %(border)s; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #33497a; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
""" % {"night": NIGHT, "deep": DEEP_BLUE, "panel": PANEL, "panelLight": PANEL_LIGHT,
       "white": WHITE, "muted": MUTED, "orange": ORANGE, "border": BORDER}


class _Bridge(QObject):
    """Carries socket-thread callbacks onto the GUI thread."""
    changed = pyqtSignal()
    role_scanned = pyqtSignal(object)


class ControlPanel(QMainWindow):
    def __init__(self, server):
        super().__init__()
        self.server = server
        self.controls = Controls(server)
        self.pairing = server.pairing
        # Whether the pairing code is being asked for, as opposed to shown because nothing
        # has ever paired. Starts off: `_has_paired` decides the opening page.
        self._show_pair = False
        self._seen_a_device = False

        self.setWindowTitle("Overwatch Queue Server")
        self.setStyleSheet(STYLE)
        self.setFixedSize(*WINDOW_SIZE)

        self._build()
        self._refresh_qr()
        self._refresh()

        self._bridge = _Bridge()
        self._bridge.changed.connect(self._refresh)
        self._bridge.role_scanned.connect(self._role_scanned)
        server.on_change = self._bridge.changed.emit

        watcher = server.watch_queue(self.controls)
        if watcher is not None:
            watcher.on_role_detected = self._bridge.role_scanned.emit
        # The watcher only calls back when the state *moves*, but the wait it is
        # reporting goes up every second, so the line showing it is ticked from here
        # rather than from the poll thread.
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._refresh_queue_vision)
        self._tick.timeout.connect(self._refresh_presence)
        self._tick.start(1000)
        self._refresh_queue_vision()
        self._refresh_presence()

    # ------------------------------------------------------------ layout

    def _build(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._header())

        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.stack.addWidget(_centered(self._pair_page()))
        self.stack.addWidget(_centered(self._status_page()))

    def _header(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("header")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.setSpacing(10)

        dot = QLabel()
        dot.setFixedSize(9, 9)
        dot.setStyleSheet("background: %s; border-radius: 4px;" % ORANGE)
        layout.addWidget(dot)

        title = QLabel("OVERWATCH QUEUE")
        title.setObjectName("brand")
        layout.addWidget(title)
        layout.addStretch(1)
        return bar

    def _pair_page(self) -> QWidget:
        card = QFrame()
        card.setObjectName("card")
        card.setFixedWidth(CARD_WIDTH)
        layout = QVBoxLayout(card)
        layout.setSpacing(14)
        layout.setContentsMargins(32, 30, 32, 26)

        title = QLabel("Pair a phone")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(QFont(_sans(), 17, QFont.Weight.DemiBold))
        layout.addWidget(title)

        qr_frame = QFrame()
        qr_frame.setObjectName("qrFrame")
        qr_layout = QVBoxLayout(qr_frame)
        qr_layout.setContentsMargins(18, 18, 18, 18)
        self.qr_label = QLabel()
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr_layout.addWidget(self.qr_label)
        layout.addWidget(qr_frame, 0, Qt.AlignmentFlag.AlignHCenter)

        caption = QLabel("Scan this in the app, once.")
        caption.setObjectName("muted")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(caption)

        layout.addWidget(_divider())

        copy = QPushButton("Copy pairing link")
        copy.clicked.connect(self._copy_link)
        layout.addWidget(copy)

        regenerate = QPushButton("New QR code")
        regenerate.setObjectName("danger")
        regenerate.clicked.connect(self._regenerate)
        layout.addWidget(regenerate)

        self.back_to_status = QPushButton("Back to queue status")
        self.back_to_status.setObjectName("link")
        self.back_to_status.clicked.connect(self._show_status_page)
        layout.addWidget(self.back_to_status, 0, Qt.AlignmentFlag.AlignHCenter)
        return card

    def _status_page(self) -> QWidget:
        card = QFrame()
        card.setObjectName("card")
        card.setFixedWidth(CARD_WIDTH)
        layout = QVBoxLayout(card)
        layout.setSpacing(14)
        layout.setContentsMargins(32, 30, 32, 26)

        eyebrow = QLabel("Queue status")
        eyebrow.setObjectName("eyebrow")
        eyebrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(eyebrow)

        self.phase_label = QLabel("Idle")
        self.phase_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.phase_label.setFont(QFont(_sans(), 30, QFont.Weight.Bold))
        layout.addWidget(self.phase_label)

        self.queue_state = QLabel("Not watching.")
        self.queue_state.setObjectName("muted")
        self.queue_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.queue_state.setWordWrap(True)
        layout.addWidget(self.queue_state)

        self.presence_state = QLabel("Not asking.")
        self.presence_state.setObjectName("muted")
        self.presence_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.presence_state.setWordWrap(True)
        layout.addWidget(self.presence_state)

        self.relaunch_battlenet = QPushButton("Battle.net open but not working? Relaunch it")
        self.relaunch_battlenet.setObjectName("link")
        self.relaunch_battlenet.setVisible(self.server.presence_enabled)
        self.relaunch_battlenet.clicked.connect(self._relaunch_battlenet)
        layout.addWidget(self.relaunch_battlenet, 0, Qt.AlignmentFlag.AlignHCenter)

        layout.addWidget(_divider())

        devices_label = QLabel("Paired now")
        devices_label.setObjectName("eyebrow")
        layout.addWidget(devices_label)
        self.devices = QListWidget()
        self.devices.setFixedHeight(96)
        layout.addWidget(self.devices)

        self.delivery = QLabel()
        self.delivery.setObjectName("muted")
        self.delivery.setWordWrap(True)
        layout.addWidget(self.delivery)

        layout.addWidget(_divider())

        override_label = QLabel("Manual override")
        override_label.setObjectName("eyebrow")
        layout.addWidget(override_label)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.phase_picker = QComboBox()
        self.phase_picker.addItems([PHASE_NAMES[phase] for phase in PHASES])
        row.addWidget(self.phase_picker, 1)
        set_phase = QPushButton("Set")
        set_phase.setObjectName("primary")
        set_phase.clicked.connect(self._set_phase)
        row.addWidget(set_phase)
        layout.addLayout(row)

        pair_another = QPushButton("Pair another device")
        pair_another.setObjectName("link")
        pair_another.clicked.connect(self._show_pair_page)
        layout.addWidget(pair_another, 0, Qt.AlignmentFlag.AlignHCenter)
        return card

    # ------------------------------------------------------------ page switching

    def _show_pair_page(self):
        self._show_pair = True
        self.stack.setCurrentIndex(0)

    def _show_status_page(self):
        self._show_pair = False
        self.stack.setCurrentIndex(1)

    # ------------------------------------------------------------ widget events

    def _relaunch_battlenet(self):
        """Closing and reopening Battle.net can end an active game, so this asks first —
        same as regenerating the pairing token does. The relaunch itself runs off the
        GUI thread since it waits (up to ~10s) for Battle.net to actually exit before
        starting it again; blocking the panel for that long would look like a hang."""
        answer = QMessageBox.question(
            self, "Relaunch Battle.net",
            "This closes Battle.net and opens it again with its debug port on. Any game "
            "or queue Battle.net itself is in the middle of will be interrupted.\n\n"
            "Relaunch it?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.relaunch_battlenet.setEnabled(False)
        self.server.log("Relaunching Battle.net…")

        def work():
            self.server.relaunch_battlenet()
            self._bridge.changed.emit()

        threading.Thread(target=work, daemon=True, name="battlenet-relaunch").start()

    def _set_phase(self):
        kind = PHASES[self.phase_picker.currentIndex()]
        if kind == "idle":
            self.controls.reset()
        elif kind == "searching":
            self.controls.start_queue()
        else:
            self.controls.jump(kind)

    def _refresh_queue_vision(self):
        watcher = self.server.queue_watcher
        seen = watcher.latest if watcher else None
        if not watcher or not watcher.running:
            return self._say_queue("Not watching.", MUTED)
        if seen is None or not seen.in_queue:
            if seen is not None and not seen.looking:
                return self._say_queue("Watching — Overwatch isn't in front.", MUTED)
            return self._say_queue("Watching — no queue on screen.", MUTED)

        # The mode shown is the one being broadcast, not the hue of the latest frame.
        # They agree once the phase has settled, and while they don't it is the
        # broadcast one the phone is showing — a panel that disagreed with the watch
        # would send you looking for a bug in the wrong half of the project. The raw
        # per-frame reading is a tuning question, and `queuewatch_debug.py` prints it.
        #
        # "read off the screen" or "timed from here" rather than the wire's own word for
        # it: the difference being pointed at is whether the number came off the game's
        # clock or ours, which is the one thing about it worth knowing at a glance.
        self._say_queue(
            "%s   ·   %s   ·   %s waited (%s)"
            % (QUEUE_STATE_NAMES.get(seen.state, seen.state),
               protocol.MODE_NAMES.get(self.controls.mode, self.controls.mode),
               _clock(int(seen.queue_elapsed)),
               "read off the screen" if seen.source == "timer" else "timed from here")
            + ("" if seen.looking else "   ·   Overwatch isn't in front, so this is the "
                                       "last thing actually seen"),
            ORANGE if seen.state == queuewatch.GAME_FOUND else WHITE)

    def _say_queue(self, text: str, colour: str):
        self._say(self.queue_state, text, colour)

    def _refresh_presence(self):
        watcher = self.server.presence_watcher
        if not watcher or not watcher.running:
            return self._say_presence("Not asking.", MUTED)

        seen = watcher.latest
        if seen is None:
            return self._say_presence("Battle.net — connecting…", MUTED)
        if watcher.dead:
            return self._say_presence(
                "Battle.net isn't answering — still trying. Right after Battle.net "
                "starts this can take a few minutes; the screen covers the queue until "
                "it does.", ORANGE)
        if not seen.known:
            return self._say_presence("Battle.net isn't answering.", MUTED)

        age = max(0, int(time.monotonic() - seen.at))
        detail = "%r" % seen.text if seen.text else PRESENCE_STATE_NAMES.get(
            seen.state, seen.state)
        self._say_presence("%s · %s ago" % (detail, _clock(age)), WHITE)

    def _say_presence(self, text: str, colour: str):
        self._say(self.presence_state, text, colour)

    @staticmethod
    def _say(label, text: str, colour: str):
        label.setText(text)
        label.setStyleSheet("color: %s;" % colour)

    def _role_scanned(self, result):
        if result is None:
            return
        role = result.effective_role
        if role is None:
            return
        self.controls.role = role
        if result.mode and result.mode != self.controls.mode and self.controls.queues_by_role:
            self.controls.mode = result.mode

    # ------------------------------------------------------------ pairing

    def _pair_url(self) -> str:
        return self.pairing.url(local_addresses()[0])

    def _refresh_qr(self):
        rows = qr.encode(self._pair_url(), ecl="M").rows()
        image = QImage(len(rows), len(rows), QImage.Format.Format_RGB32)
        dark, light = QColor("#0b0f18").rgb(), QColor("white").rgb()
        for y, row in enumerate(rows):
            for x, module in enumerate(row):
                image.setPixel(x, y, dark if module else light)
        # Nearest-neighbour on purpose: smoothing a QR is how you make it unscannable.
        self.qr_label.setPixmap(QPixmap.fromImage(image).scaled(
            len(rows) * QR_SCALE, len(rows) * QR_SCALE,
            Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation))

    def _copy_link(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(self._pair_url())

    def _regenerate(self):
        answer = QMessageBox.question(
            self, "New QR code",
            "Every device paired with this PC stops working until it scans the new "
            "code.\n\nGenerate a new code?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.pairing.regenerate()
        self.server.push_tokens.clear()
        self.server.activity_tokens.clear()
        self.server.restart_broker()
        self._refresh_qr()

    # ------------------------------------------------------------ plumbing

    def _refresh(self):
        session = self.server.session
        self.phase_label.setText(PHASE_NAMES.get(session.kind, session.kind))
        self.phase_label.setStyleSheet(
            "color: %s;" % (ORANGE if session.kind == "matchFound" else WHITE))

        clients = self.server.clients
        if clients:
            self._seen_a_device = True
            self._show_pair = False
        self.devices.clear()
        for client in clients:
            self.devices.addItem("%s — %s" % (client.name, client.host))
        if not clients:
            self.devices.addItem("Nothing connected right now")

        self._refresh_queue_vision()
        self._refresh_presence()
        self._refresh_delivery(clients)
        self.relaunch_battlenet.setEnabled(True)

        self.stack.setCurrentIndex(0 if self._show_pair or not self._has_paired() else 1)

    def _has_paired(self) -> bool:
        """Whether a phone has ever used this code — not whether one is connected now.

        A phone drops off MQTT within seconds of its screen locking, which is exactly when
        the Live Activity becomes the only thing still showing the queue. Treating that as
        "unpaired" put the pairing code back on screen every time the phone went in a
        pocket. The stored tokens are what make this survive a server restart too: a phone
        that registered for pushes in an earlier run is still paired.
        """
        if self._seen_a_device or self.server.clients:
            return True
        return bool(self.server.push_tokens.get("phone")
                    or self.server.activity_tokens.start_token())

    def _refresh_delivery(self, clients):
        """What is — or isn't — reaching the phone while it's away.

        Pushing is optional and silently absent when it isn't configured, and the shape of
        that is confusing from the phone end: everything works while the app is open, and
        the Live Activity simply stops the moment the screen locks. Saying so here is the
        only place that's visible from.
        """
        # The same rule the server pushes by — see `QueueServer._push_activity`, which is
        # skipped entirely while a phone is live over MQTT and driving its own activity.
        phone_here = any((client.identity or {}).get("kind") == "phone" for client in clients)
        if self.server.apns is None:
            return self._say(self.delivery,
                             "Pushing isn't set up, so the Live Activity goes quiet the "
                             "moment the phone locks. See server/README.md — apns.json or "
                             "push_relay.json.", ORANGE)
        if phone_here:
            return self._say(self.delivery,
                             "Phone connected — it's driving its own Live Activity.", MUTED)
        self._say(self.delivery, "Phone away — pushing to its Live Activity.", MUTED)

    def closeEvent(self, event):
        self.server.stop()
        super().closeEvent(event)


# ---------------------------------------------------------------- small helpers

def _divider() -> QFrame:
    line = QFrame()
    line.setObjectName("divider")
    return line


def _centered(widget: QWidget) -> QWidget:
    wrapper = QWidget()
    layout = QVBoxLayout(wrapper)
    layout.addStretch(1)
    row = QHBoxLayout()
    row.addStretch(1)
    widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    row.addWidget(widget)
    row.addStretch(1)
    layout.addLayout(row)
    layout.addStretch(1)
    return wrapper


def _clock(seconds: int) -> str:
    return "%d:%02d" % (seconds // 60, seconds % 60)


def _sans() -> str:
    return "Segoe UI, -apple-system, Helvetica, sans-serif"


def run(server):
    """Opens the window and runs until it's closed."""
    import sys
    from PyQt6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setApplicationName("Overwatch Queue Server")
    panel = ControlPanel(server)
    server.start()
    panel.show()
    return app.exec()
