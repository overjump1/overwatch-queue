"""The control panel window: pair a phone, then drive the queue by hand.

This replaces the app's old in-app debug panel — same idea, other end of the wire. Every
button pushes a real snapshot through the real socket, so what the phone does here is
what it will do when this program reads the game instead of a mouse click.

What a button *means* lives in `controls.py`; this file is the window around it. Socket
callbacks arrive on their own threads, so they come in over Qt signals — the only safe
way to reach a widget from another thread.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QObject, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QPixmap
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout, QGroupBox,
                             QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow,
                             QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy,
                             QSlider, QVBoxLayout, QWidget)

from . import protocol, qr
from .controls import Controls
from .pairing import local_addresses
from .queueserver import SCENARIOS

QR_SCALE = 6
LOG_LINES = 400

JUMPS = [("Match Found", "matchFound"), ("Map Vote", "mapVote"),
         ("Hero Select", "heroSelect"), ("In Game", "inGame"), ("Cancelled", "cancelled")]
ROLES = ["tank", "damage", "support", "flex"]

PHASE_NAMES = {"idle": "Idle", "searching": "Searching", "matchFound": "Match found",
               "mapVote": "Map vote", "heroSelect": "Hero select", "inGame": "In game",
               "cancelled": "Cancelled"}

# The app's palette, so the two halves of the project look like one thing.
NIGHT = "#080b14"
DEEP_BLUE = "#0d192e"
PANEL = "#111c33"
ORANGE = "#f99e1a"
WHITE = "#f6f8fc"
MUTED = "#8b95ab"
SUPPORT = "#71c77e"

STYLE = """
QMainWindow, QWidget { background: %(night)s; color: %(white)s; }
/* Labels sit inside the panels, so they must not paint the window colour over them. */
QLabel, QCheckBox { background: transparent; }
QGroupBox {
    border: 1px solid #1d2c4a; border-radius: 10px;
    margin-top: 16px; padding: 14px 14px 12px 14px; background: %(deep)s;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 12px; padding: 0 6px;
    color: %(muted)s; font-weight: 600; text-transform: uppercase;
}
QPushButton {
    background: %(panel)s; border: 1px solid #24365a; border-radius: 7px;
    padding: 7px 14px; min-height: 20px; color: %(white)s;
}
QPushButton:hover:!disabled { border-color: %(orange)s; }
QPushButton:pressed:!disabled { background: #1a2947; }
QPushButton:disabled { color: #46516b; border-color: #1a253d; background: #0c1526; }
QPushButton#primary { background: %(orange)s; border-color: %(orange)s; color: #201400;
                      font-weight: 600; }
QPushButton#primary:hover { background: #ffb340; }
QPushButton#danger:hover { border-color: #ea6e53; color: #ea6e53; }
QComboBox, QLineEdit {
    background: %(panel)s; border: 1px solid #24365a; border-radius: 7px;
    padding: 6px 10px; color: %(white)s; selection-background-color: %(orange)s;
}
QComboBox:disabled { color: #46516b; }
QComboBox QAbstractItemView {
    background: %(panel)s; color: %(white)s; border: 1px solid #24365a;
    selection-background-color: %(orange)s; selection-color: #201400;
}
QListWidget, QPlainTextEdit {
    background: %(panel)s; border: 1px solid #1d2c4a; border-radius: 8px;
    padding: 6px; color: %(white)s;
}
QLabel#muted { color: %(muted)s; }
QLabel#status { color: %(muted)s; padding: 4px 2px; }
QSlider::groove:horizontal { height: 4px; background: #24365a; border-radius: 2px; }
QSlider::handle:horizontal {
    background: %(orange)s; width: 14px; height: 14px;
    margin: -6px 0; border-radius: 7px;
}
QSlider::sub-page:horizontal { background: %(orange)s; border-radius: 2px; }
QCheckBox { color: %(white)s; }
QCheckBox::indicator {
    width: 15px; height: 15px; border-radius: 4px;
    border: 1px solid #24365a; background: %(panel)s;
}
QCheckBox::indicator:checked { background: %(orange)s; border-color: %(orange)s; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #24365a; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #33497a; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QStatusBar { background: %(night)s; }
QStatusBar::item { border: none; }
""" % {"night": NIGHT, "deep": DEEP_BLUE, "panel": PANEL, "white": WHITE,
       "muted": MUTED, "orange": ORANGE}


class _Bridge(QObject):
    """Carries socket-thread callbacks onto the GUI thread."""
    logged = pyqtSignal(str)
    changed = pyqtSignal()


class ControlPanel(QMainWindow):
    def __init__(self, server):
        super().__init__()
        self.server = server
        self.controls = Controls(server)
        self.pairing = server.pairing

        self.setWindowTitle("Overwatch Queue Server")
        self.setStyleSheet(STYLE)
        self.setMinimumSize(1000, 760)

        self._build()
        self._refresh_qr()
        self._refresh()

        self._bridge = _Bridge()
        self._bridge.logged.connect(self._write_log)
        self._bridge.changed.connect(self._refresh)
        server.log = self._bridge.logged.emit
        server.on_change = self._bridge.changed.emit

    # ------------------------------------------------------------ layout

    def _build(self):
        central = QWidget()
        self.setCentralWidget(central)
        columns = QHBoxLayout(central)
        columns.setContentsMargins(16, 16, 16, 12)
        columns.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(14)
        pairing = self._pairing_box()
        pairing.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        left.addWidget(pairing)
        left.addWidget(self._devices_box(), 1)
        columns.addLayout(left, 0)

        right = QVBoxLayout()
        right.setSpacing(14)
        for panel in (self._queue_box(), self._jump_box(),
                      self._scenario_box(), self._options_row()):
            panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            right.addWidget(panel)
        right.addWidget(self._log_box(), 1)      # only the log takes the slack
        columns.addLayout(right, 1)

        self.status = QLabel("Starting…")
        self.status.setObjectName("status")
        self.statusBar().addWidget(self.status)

    def _pairing_box(self) -> QGroupBox:
        box = QGroupBox("Pair a phone")
        layout = QVBoxLayout(box)
        layout.setSpacing(10)

        self.qr_label = QLabel()
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.qr_label, 0, Qt.AlignmentFlag.AlignHCenter)

        caption = QLabel("Scan this in the app, once.")
        caption.setObjectName("muted")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(caption)

        addresses = local_addresses()
        self.address_picker = QComboBox()
        self.address_picker.addItems(addresses)
        self.address_picker.currentIndexChanged.connect(self._refresh_qr)
        layout.addLayout(_labelled("This PC", self.address_picker))

        self.port_field = QLineEdit(str(self.pairing.port))
        self.port_field.setFixedWidth(90)
        self.port_field.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.port_field.editingFinished.connect(self._change_port)
        layout.addLayout(_labelled("Port", self.port_field))

        self.token_label = QLabel()
        self.token_label.setObjectName("muted")
        self.token_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.token_label)

        copy = QPushButton("Copy pairing link")
        copy.clicked.connect(self._copy_link)
        layout.addWidget(copy)

        regenerate = QPushButton("New token (unpairs every device)")
        regenerate.setObjectName("danger")
        regenerate.clicked.connect(self._regenerate)
        layout.addWidget(regenerate)
        return box

    def _devices_box(self) -> QGroupBox:
        box = QGroupBox("Paired now")
        layout = QVBoxLayout(box)
        self.devices = QListWidget()
        self.devices.setFixedWidth(300)
        layout.addWidget(self.devices)
        return box

    def _queue_box(self) -> QGroupBox:
        box = QGroupBox("Queue")
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        grid.setColumnStretch(4, 1)

        self.mode_picker = QComboBox()
        self.mode_picker.addItems([protocol.MODE_NAMES[mode] for mode in protocol.MODES])
        self.mode_picker.setCurrentIndex(protocol.MODES.index(self.controls.mode))
        self.mode_picker.currentIndexChanged.connect(self._mode_changed)
        grid.addWidget(_label("Mode"), 0, 0)
        grid.addWidget(self.mode_picker, 0, 1)

        self.role_picker = QComboBox()
        self.role_picker.addItems([protocol.ROLE_NAMES[role] for role in ROLES])
        self.role_picker.setCurrentIndex(ROLES.index(self.controls.role))
        self.role_picker.currentIndexChanged.connect(self._role_changed)
        grid.addWidget(_label("Role"), 0, 2)
        grid.addWidget(self.role_picker, 0, 3)

        self.estimate_label = QLabel()
        self.estimate_slider = QSlider(Qt.Orientation.Horizontal)
        self.estimate_slider.setRange(10, 600)
        self.estimate_slider.setSingleStep(5)
        self.estimate_slider.setValue(self.controls.estimate)
        self.estimate_slider.valueChanged.connect(self._estimate_changed)
        grid.addWidget(self.estimate_label, 1, 0, 1, 2)
        grid.addWidget(self.estimate_slider, 1, 2, 1, 3)

        buttons = QHBoxLayout()
        start = QPushButton("Start queue")
        start.setObjectName("primary")
        start.clicked.connect(self.controls.start_queue)
        buttons.addWidget(start)
        self.skip_one = QPushButton("Skip ahead 1 min")
        self.skip_one.clicked.connect(lambda: self.server.shift_start(60))
        buttons.addWidget(self.skip_one)
        self.skip_five = QPushButton("Skip ahead 5 min")
        self.skip_five.clicked.connect(lambda: self.server.shift_start(300))
        buttons.addWidget(self.skip_five)
        buttons.addStretch(1)
        grid.addLayout(buttons, 2, 0, 1, 5)
        return box

    def _jump_box(self) -> QGroupBox:
        box = QGroupBox("Jump to phase")
        layout = QVBoxLayout(box)
        row = QHBoxLayout()
        row.setSpacing(8)

        self.jump_buttons = {}
        for title, kind in JUMPS:
            button = QPushButton(title)
            button.clicked.connect(lambda _, k=kind: self.controls.jump(k))
            row.addWidget(button)
            self.jump_buttons[kind] = button
        reset = QPushButton("Reset to idle")
        reset.setObjectName("danger")
        reset.clicked.connect(self.controls.reset)
        row.addWidget(reset)
        row.addStretch(1)
        layout.addLayout(row)

        layout.addWidget(_muted("Greyed-out steps aren't reachable from here — the same "
                                "transition rules the app enforces on the way in.", wrap=True))
        return box

    def _scenario_box(self) -> QGroupBox:
        box = QGroupBox("Scenarios")
        grid = QGridLayout(box)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)

        for row, scenario in enumerate(SCENARIOS):
            button = QPushButton(scenario.name)
            button.setFixedWidth(190)
            button.clicked.connect(lambda _, s=scenario: self.server.play(s))
            grid.addWidget(button, row, 0)
            grid.addWidget(_muted(scenario.detail), row, 1)

        self.stop_scenario = QPushButton("Stop scenario")
        self.stop_scenario.setObjectName("danger")
        self.stop_scenario.setFixedWidth(190)
        self.stop_scenario.clicked.connect(self._stop_scenario)
        grid.addWidget(self.stop_scenario, len(SCENARIOS), 0)
        return box

    def _options_row(self) -> QWidget:
        row = QFrame()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(4, 0, 4, 0)
        self.honour_cancel = QCheckBox("Honour cancel from the phone")
        self.honour_cancel.setChecked(self.server.honour_cancel)
        self.honour_cancel.toggled.connect(self._honour_cancel_changed)
        layout.addWidget(self.honour_cancel)
        layout.addWidget(_muted("Off is the honest case: Overwatch usually won't let you out."))
        layout.setSpacing(12)
        layout.addStretch(1)
        return row

    def _log_box(self) -> QGroupBox:
        box = QGroupBox("Traffic")
        layout = QVBoxLayout(box)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(LOG_LINES)
        self.log_view.setFont(QFont(_monospace(), 11))
        layout.addWidget(self.log_view)
        return box

    # ------------------------------------------------------------ widget events

    def _mode_changed(self, index: int):
        self.controls.mode = protocol.MODES[index]
        self.role_picker.setEnabled(self.controls.queues_by_role)

    def _role_changed(self, index: int):
        self.controls.role = ROLES[index]

    def _estimate_changed(self, value: int):
        self.controls.set_estimate(value)
        self.estimate_label.setText("Estimated wait   %s" % _clock(value))

    def _honour_cancel_changed(self, checked: bool):
        self.server.honour_cancel = checked

    def _stop_scenario(self):
        self.server.stop_scenario()
        self.server.log("Scenario stopped")
        self._refresh()

    # ------------------------------------------------------------ pairing

    def _pair_url(self) -> str:
        return self.pairing.url(self.address_picker.currentText())

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
        self.token_label.setText("Token   %s" % self.pairing.short_token)

    def _copy_link(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(self._pair_url())
        self.server.log("Pairing link copied to the clipboard")

    def _regenerate(self):
        answer = QMessageBox.question(
            self, "New pairing token",
            "Every device paired with this PC stops working until it scans the new "
            "code.\n\nGenerate a new token?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.pairing.regenerate()
        self.server.push_tokens.clear()
        for client in self.server.ws.clients:
            client.close(reason="unpaired")
        self._refresh_qr()
        self.server.log("New pairing token — every device has to scan again")

    def _change_port(self):
        try:
            port = int(self.port_field.text())
            if not 1024 <= port <= 65535:
                raise ValueError
        except ValueError:
            self.port_field.setText(str(self.pairing.port))
            return
        if port == self.pairing.port:
            return

        was = self.pairing.port
        self.server.stop()
        self.pairing.port = port
        try:
            self.server.start()
        except OSError as problem:
            self.pairing.port = was
            self.port_field.setText(str(was))
            self.server.start()
            QMessageBox.critical(self, "Port %d isn't free" % port, str(problem))
            return
        self.pairing.save()
        self._refresh_qr()
        self.server.log("Now listening on port %d" % port)

    # ------------------------------------------------------------ plumbing

    def _write_log(self, message: str):
        self.log_view.appendPlainText(
            "%s  %s" % (protocol.iso(protocol.now())[11:19], message))

    def _refresh(self):
        session = self.server.session
        for kind, button in self.jump_buttons.items():
            button.setEnabled(session.can_go_to(kind))
        searching = session.kind == "searching"
        self.skip_one.setEnabled(searching)
        self.skip_five.setEnabled(searching)
        self.stop_scenario.setEnabled(self.server.scenario_running)
        self.estimate_label.setText("Estimated wait   %s" % _clock(self.controls.estimate))

        clients = self.server.clients
        self.devices.clear()
        for client in clients:
            self.devices.addItem("%s — %s" % (client.name, client.host))
        if not clients:
            self.devices.addItem("Waiting for a phone to scan the code…")

        self.status.setText("%s   ·   sequence %d   ·   %d device%s connected"
                            % (PHASE_NAMES.get(session.kind, session.kind), session.sequence,
                               len(clients), "" if len(clients) == 1 else "s"))

    def closeEvent(self, event):
        self.server.stop()
        super().closeEvent(event)


# ---------------------------------------------------------------- small helpers

def _label(text: str) -> QLabel:
    return QLabel(text)


def _muted(text: str, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setObjectName("muted")
    label.setWordWrap(wrap)
    return label


def _labelled(title: str, widget: QWidget) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addWidget(_label(title))
    row.addStretch(1)
    row.addWidget(widget)
    return row


def _clock(seconds: int) -> str:
    return "%d:%02d" % (seconds // 60, seconds % 60)


def _monospace() -> str:
    return "Menlo, Consolas, monospace"


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
