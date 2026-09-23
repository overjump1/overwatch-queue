"""Manual notification tester: sends queue states to the worker by hand, as the PC app would.

Close the real OverQueue app first, or its heartbeat will overwrite whatever you set here.
Run with:  python test.py
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

import version

# The same worker and pairing the app itself uses (see relay.py and app.py).
WORKER_URL = os.environ.get("OVERQUEUE_WORKER_URL", version.WORKER_URL).rstrip("/")
PAIRING_FILE = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "OverQueue", "pairing.json")
MODES = ["competitive", "quickPlay", "arcade", "stadium", "mysteryHeroes", "custom"]
HEARTBEAT_SECONDS = 30  # the worker ends everything after 180s without a report


def saved_pair_id():
    try:
        with open(PAIRING_FILE, encoding="utf-8") as handle:
            return json.load(handle).get("id", "")
    except (OSError, ValueError, AttributeError):
        return ""


def request(method, url, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json", "User-Agent": "OWQueue-test/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")
    except Exception as problem:  # noqa: BLE001
        return 0, str(problem)


class Signals(QObject):
    line = pyqtSignal(str)


class Tester(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OverQueue – notification tester")
        self.resize(560, 480)
        self.signals = Signals()
        self.signals.line.connect(self._append)

        self.state = "idle"
        self.queued_at = None  # monotonic time the queue started
        self.found_at = None   # monotonic time the match was found
        self.wait = 0.0        # final queue wait once found

        self.pair = QLineEdit(saved_pair_id())
        self.pair.setPlaceholderText("32-character pairing id")
        self.mode = QComboBox()
        self.mode.addItems(MODES)
        self.late = QSpinBox()
        self.late.setRange(0, 3600)
        self.late.setSuffix(" s ago")
        self.late.setToolTip("How long ago the match was found. 60+ tests the 'picked up late' path.")
        self.heartbeat = QCheckBox("Heartbeat every %ds (keeps the worker from timing out)" % HEARTBEAT_SECONDS)
        self.heartbeat.setChecked(True)
        self.current = QLabel()
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)

        buttons = QHBoxLayout()
        for text, handler in [("Start queue", self.queue), ("Match found", self.found),
                              ("Idle / leave", self.idle), ("Check status", self.check)]:
            button = QPushButton(text)
            button.clicked.connect(handler)
            buttons.addWidget(button)

        form = QGridLayout()
        form.addWidget(QLabel("Pair id"), 0, 0)
        form.addWidget(self.pair, 0, 1)
        form.addWidget(QLabel("Mode"), 1, 0)
        form.addWidget(self.mode, 1, 1)
        form.addWidget(QLabel("Match found"), 2, 0)
        form.addWidget(self.late, 2, 1)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Close the real OverQueue app first. Lock your phone, then press a button."))
        layout.addLayout(form)
        layout.addWidget(self.heartbeat)
        layout.addLayout(buttons)
        layout.addWidget(self.current)
        layout.addWidget(self.log)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._beat)
        self.timer.start(HEARTBEAT_SECONDS * 1000)
        self._show_state()
        if not self.pair.text():
            self._append("No pairing found at %s. Paste the pair id." % PAIRING_FILE)
        self.check()

    # Buttons

    def queue(self):
        self.state, self.queued_at, self.found_at = "queueing", time.monotonic(), None
        self._send()

    def found(self):
        now = time.monotonic()
        self.wait = now - self.queued_at if self.queued_at else 0.0
        self.state, self.found_at = "found", now - self.late.value()
        self._send()

    def idle(self):
        self.state, self.queued_at, self.found_at = "idle", None, None
        self._send()

    def check(self):
        pair_id = self._pair_id()
        if pair_id:
            self._async("GET", "/v1/pair/%s/state" % pair_id, None, "status")

    # Plumbing

    def _body(self):
        now = time.monotonic()
        if self.state == "queueing":
            elapsed, since_found = now - self.queued_at, 0.0
        elif self.state == "found":
            elapsed, since_found = self.wait, now - self.found_at
        else:
            elapsed, since_found = 0.0, 0.0
        return {"state": self.state, "mode": None if self.state == "idle" else self.mode.currentText(),
                "elapsed": round(elapsed), "sinceFound": round(since_found)}

    def _send(self, label="send"):
        self._show_state()
        pair_id = self._pair_id()
        if pair_id:
            self._async("POST", "/v1/pair/%s/state" % pair_id, self._body(), label)

    def _beat(self):
        if self.heartbeat.isChecked() and self.state != "idle":
            self._send("heartbeat")

    def _pair_id(self):
        pair_id = self.pair.text().strip().lower()
        if len(pair_id) != 32 or any(c not in "0123456789abcdef" for c in pair_id):
            self._append("Pair id must be 32 hex characters.")
            return None
        return pair_id

    def _async(self, method, path, body, label):
        def work():
            status, text = request(method, WORKER_URL + path, body)
            sent = " %s" % json.dumps(body) if body else ""
            self.signals.line.emit("%s %s%s\n  -> %s %s" % (time.strftime("%H:%M:%S"), label, sent, status, text))
        threading.Thread(target=work, daemon=True).start()

    def _show_state(self):
        self.current.setText("Reporting: <b>%s</b>" % self.state)

    def _append(self, text):
        self.log.appendPlainText(text)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = Tester()
    window.show()
    sys.exit(app.exec())
