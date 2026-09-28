"""The notification speed test: sends a test alert to every paired device and shows how long each
step took to reach it.

The worker pushes the test to all of them at once and each device says when it got it, all timed
on the worker's own clock, so a phone whose clock is off doesn't skew anything. The one step the
worker can't see is this PC reaching it, which is timed here: half the request's round trip, less
the time the worker spent on it.
"""
from __future__ import annotations

import threading
import time

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

import relay

POLL_SECONDS = 1.0
# The worker takes one test per pairing this often; the button waits it out rather than asking.
TEST_INTERVAL_SECONDS = 15
FAST_MS = 2000
OK_MS = 5000

DEVICES = (
    # kind, name, where the push goes after the worker
    ("phone", "iPhone", "Apple"),
    ("watch", "Apple Watch", "Apple"),
    ("android", "Android phone", "Google"),
)

TIPS = {
    "phone": "Check Settings → Notifications → OverQueue is allowed and no Focus is hiding it, "
             "then open OverQueue on the iPhone once.",
    "watch": "Wear the Watch unlocked, check the Watch app on your iPhone → Notifications → "
             "OverQueue is on, and open OverQueue on the Watch once.",
    "android": "Allow OverQueue's notifications, turn off battery optimisation for it, and open "
               "it once.",
}
WATCH_NOTE = ("Timed to when the Watch got it. watchOS can hold the buzz a few seconds longer when "
              "the iPhone didn't get the same alert.")
STALE_ERRORS = ("UNREGISTERED", "NOT_FOUND", "INVALID_ARGUMENT", "BadDeviceToken", "Unregistered")

FAST, OK, SLOW, WAITING, FAILED = "fast", "ok", "slow", "waiting", "failed"


def seconds(ms):
    """0.08 s, 1.30 s, 12.4 s."""
    value = max(0.0, ms) / 1000
    return "%.1f s" % value if value >= 10 else "%.2f s" % value


def verdict(total_ms):
    if total_ms < FAST_MS:
        return FAST
    return OK if total_ms < OK_MS else SLOW


def device_results(view, pc_ms, finished):
    """What the dialog shows for each device in the worker's `view` of the test: a total, a
    verdict, the steps that make it up, and anything worth saying about it. `finished`: the test
    is over, so a device that hasn't answered isn't going to."""
    results = []
    for kind, name, service in DEVICES:
        device = (view.get("devices") or {}).get(kind)
        if device is None:
            continue
        to_service, to_device, error = device.get("toServiceMs"), device.get("toDeviceMs"), device.get("error")
        result = {"kind": kind, "name": name, "total_ms": None, "steps": [], "note": None}
        if error:
            stale = any(code in error for code in STALE_ERRORS)
            result.update(verdict=FAILED, note=(
                "This device's registration is out of date: open OverQueue on it, then test again."
                if stale else "%s's push service turned it away (%s)." % (service, error)))
        elif to_device is not None:
            total = pc_ms + to_service + to_device
            result.update(total_ms=total, verdict=verdict(total), steps=[
                ("Your PC → OverQueue server", pc_ms),
                ("Server → %s's push service" % service, to_service),
                ("%s → your %s" % (service, name), to_device),
            ], note=WATCH_NOTE if kind == "watch" else None)
        elif finished:
            result.update(verdict=FAILED, note="Didn't arrive within %d s. %s" % (
                view.get("timeoutSeconds", 60), TIPS[kind]))
        else:
            result.update(verdict=WAITING)
        results.append(result)
    return results


def summary(results):
    """One line for the log, so a slow report can be looked into afterwards."""
    parts = []
    for result in results:
        if result["total_ms"] is None:
            parts.append("%s %s" % (result["name"], result["verdict"]))
        else:
            parts.append("%s %s (%s)" % (result["name"], seconds(result["total_ms"]),
                                         ", ".join(seconds(ms) for _, ms in result["steps"])))
    return "Notification test: " + "; ".join(parts)


class Test:
    """One run, on its own thread. `on_update(snapshot)` is called from that thread with
    `{"results", "finished", "error"}` every time something changes."""

    def __init__(self, pair_id, on_update, log=print):
        self.pair_id = pair_id
        self.on_update = on_update
        self.log = log
        self.cancelled = threading.Event()

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="speed-test").start()

    def cancel(self):
        self.cancelled.set()

    def _report(self, results=(), finished=False, error=None):
        if not self.cancelled.is_set():
            self.on_update({"results": list(results), "finished": finished, "error": error})

    def _run(self):
        try:
            self._test()
        except Exception as problem:  # noqa: BLE001 - a test must never take the app down
            self.log("Notification test failed: %s" % problem)
            self._report(finished=True, error="Couldn't reach the notification server.")

    def _test(self):
        began = time.monotonic()
        status, view = relay._request("POST", "/v1/pair/%s/test" % self.pair_id, {})
        round_trip_ms = (time.monotonic() - began) * 1000
        if status == 429:
            wait = (view or {}).get("retryAfter", TEST_INTERVAL_SECONDS)
            return self._report(finished=True, error="A test just ran. Try again in %d s." % wait)
        if status == 410:
            return self._report(finished=True, error="This pairing was reset: scan the new QR code first.")
        if status == 409:
            return self._report(finished=True, error="Nothing is paired yet: scan the QR code first.")
        if status != 200 or not view:
            return self._report(finished=True, error="Couldn't reach the notification server.")
        pc_ms = max(0.0, (round_trip_ms - view.get("handledMs", 0)) / 2)
        test_id = view["testId"]
        deadline = began + view.get("timeoutSeconds", 60)
        results = device_results(view, pc_ms, finished=False)
        self._report(results)
        while any(r["verdict"] == WAITING for r in results):
            if self.cancelled.wait(POLL_SECONDS):
                return
            finished = time.monotonic() >= deadline
            status, polled = relay._request("GET", "/v1/pair/%s/test/%s" % (self.pair_id, test_id))
            if status == 200 and polled:
                view = polled
            results = device_results(view, pc_ms, finished)
            if finished:
                break
            self._report(results)
        self.log(summary(results))
        self._report(results, finished=True)


class _Signals(QObject):
    update = pyqtSignal(object)


class SpeedTestDialog(QDialog):
    """Runs a test when opened and shows it as it comes in: one card per paired device."""

    def __init__(self, parent, pair_id, colors, log=print):
        super().__init__(parent)
        self.pair_id = pair_id
        self.colors = colors
        self.log = log
        self.test = None
        self.started_at = None
        self.snapshot = {"results": [], "finished": False, "error": None}
        self.signals = _Signals()
        self.signals.update.connect(self._show)

        self.setWindowTitle("Notification speed test")
        self.setMinimumWidth(380)
        self.setStyleSheet(parent.styleSheet() if parent else "")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)
        title = QLabel("Notification speed test", font=_font(14, QFont.Weight.DemiBold))
        intro = QLabel("Sends a test alert to your paired devices and times each step until it "
                       "arrives.", font=_font(9), wordWrap=True)
        intro.setStyleSheet("color: %s;" % colors["muted"])
        layout.addWidget(title)
        layout.addWidget(intro)
        self.cards = QVBoxLayout()
        self.cards.setSpacing(8)
        layout.addLayout(self.cards)
        self.status = QLabel(font=_font(9), wordWrap=True)
        layout.addWidget(self.status)

        self.again = QPushButton("Test again", font=_font(10))
        self.again.clicked.connect(self.run)
        close = QPushButton("Close", font=_font(10))
        close.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(self.again)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def run(self):
        if self.test:
            self.test.cancel()
        self.started_at = time.monotonic()
        self.snapshot = {"results": [], "finished": False, "error": None}
        self.test = Test(self.pair_id, self.signals.update.emit, log=self.log)
        self.test.start()
        self.refresh(rebuild=True)

    def closeEvent(self, event):
        if self.test:
            self.test.cancel()
        super().closeEvent(event)

    def _show(self, snapshot):
        self.snapshot = snapshot
        self.refresh(rebuild=True)

    def refresh(self, rebuild=False):
        """Also called on the app's own timer, so the waiting counters and the button tick along."""
        snapshot = self.snapshot
        if rebuild or any(result["verdict"] == WAITING for result in snapshot["results"]):
            while self.cards.count():
                widget = self.cards.takeAt(0).widget()
                widget.setParent(None)
                widget.deleteLater()
            for result in snapshot["results"]:
                self.cards.addWidget(self._card(result))

        elapsed = time.monotonic() - self.started_at if self.started_at else 0
        if snapshot["error"]:
            self._set(self.status, snapshot["error"], self.colors["warn"])
        elif not snapshot["results"]:
            self._set(self.status, "Sending…", self.colors["muted"])
        elif snapshot["finished"]:
            self._set(self.status, "Times include each device's short reply saying it got the alert.",
                      self.colors["muted"])
        else:
            self._set(self.status, "Waiting for your devices… %d s" % elapsed, self.colors["muted"])
        wait = TEST_INTERVAL_SECONDS - elapsed
        self.again.setEnabled(snapshot["finished"] and wait <= 0)
        self.again.setText("Test again" if wait <= 0 else "Test again (%d s)" % (wait + 0.999))
        self.adjustSize()

    def _card(self, result):
        card = QFrame(objectName="card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)
        header = QHBoxLayout()
        header.addWidget(QLabel(result["name"], font=_font(11, QFont.Weight.DemiBold)))
        header.addStretch()
        text, color = self._headline(result)
        header.addWidget(self._label(text, color, _font(11, QFont.Weight.DemiBold)))
        layout.addLayout(header)
        for label, ms in result["steps"]:
            row = QHBoxLayout()
            row.addWidget(self._label(label, self.colors["muted"], _font(9)))
            row.addStretch()
            row.addWidget(self._label(seconds(ms), self.colors["text"], _font(9)))
            layout.addLayout(row)
        if result["note"]:
            note = self._label(result["note"], self.colors["muted"], _font(8))
            note.setWordWrap(True)
            layout.addWidget(note)
        return card

    def _headline(self, result):
        kind = result["verdict"]
        if kind == WAITING:
            elapsed = time.monotonic() - self.started_at if self.started_at else 0
            return "waiting… %d s" % elapsed, self.colors["muted"]
        if kind == FAILED:
            return "didn't arrive", self.colors["bad"]
        color = {FAST: self.colors["good"], OK: self.colors["warn"], SLOW: self.colors["bad"]}[kind]
        return "%s  ● %s" % (seconds(result["total_ms"]), {FAST: "fast", OK: "OK", SLOW: "slow"}[kind]), color

    @staticmethod
    def _label(text, color, font):
        label = QLabel(text, font=font)
        label.setStyleSheet("color: %s; background: transparent;" % color)
        return label

    @staticmethod
    def _set(label, text, color):
        label.setText(text)
        label.setStyleSheet("color: %s;" % color)


def _font(size, weight=QFont.Weight.Normal):
    font = QFont("Segoe UI", size)
    font.setWeight(weight)
    return font
