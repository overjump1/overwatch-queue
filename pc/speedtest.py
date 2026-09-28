"""The notification speed test: sends a test alert to every paired device and shows how long each
step took to reach it.

The worker pushes the test to all of them at once and each device says when it got it, all timed
on the worker's own clock, so a phone whose clock is off doesn't skew anything. The one step the
worker can't see is this PC reaching it, which is timed here: half the request's round trip, less
the time the worker spent on it.
"""
from __future__ import annotations

import math
import threading
import time

from PyQt6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QFontMetricsF, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

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
    "phone": "Check OverQueue's notifications are on and no Focus hides them, then open it once.",
    "watch": "Wear the Watch unlocked, check OverQueue's notifications are on, and open it once.",
    "android": "Allow OverQueue's notifications, turn off battery optimisation for it, and open it once.",
}
# The rest is on the cards' tooltips, so the window itself stays to the timelines.
ABOUT = ("Under %d s is fast, under %d s is OK. Each time includes the device's short reply saying it "
         "got the alert." % (FAST_MS // 1000, OK_MS // 1000))
WATCH_NOTE = ("With your iPhone nearby, the Watch gets its alerts through the iPhone and answers back the "
              "same way, so it's a little slower than the iPhone.")
STALE_ERRORS = ("UNREGISTERED", "NOT_FOUND", "INVALID_ARGUMENT", "BadDeviceToken", "Unregistered")

FAST, OK, SLOW, WAITING, FAILED = "fast", "ok", "slow", "waiting", "failed"

# The four stops on the way, and the three steps between them. `stage` is the step under way;
# ARRIVED is past the last one.
YOUR_PC, SERVER, PUSH_SERVICE, DEVICE = range(4)
ARRIVED = DEVICE
SENDING = "sending"  # the card shown before the worker has said which devices it's sending to


def seconds(ms):
    """0.08 s, 1.30 s, 12.4 s."""
    value = max(0.0, ms) / 1000
    return "%.1f s" % value if value >= 10 else "%.2f s" % value


def verdict(total_ms):
    if total_ms < FAST_MS:
        return FAST
    return OK if total_ms < OK_MS else SLOW


def device_results(view, pc_ms, finished):
    """What the dialog shows for each device in the worker's `view` of the test. Each has its
    `stops`, the `times` of the three steps between them (None until known), the step under way
    (`stage`), the step that failed (`failed_at`) if one did, a total, a verdict, and anything worth
    saying about it. `finished`: the test is over, so a device that hasn't answered isn't going to."""
    results = []
    for kind, name, service in DEVICES:
        device = (view.get("devices") or {}).get(kind)
        if device is None:
            continue
        to_service, to_device, error = device.get("toServiceMs"), device.get("toDeviceMs"), device.get("error")
        result = _card(kind, name, service)
        result.update(times=[pc_ms, to_service, to_device], stage=PUSH_SERVICE)
        if error:
            stale = any(code in error for code in STALE_ERRORS)
            result.update(verdict=FAILED, failed_at=SERVER, times=[pc_ms, None, None], note=(
                "Out of date on this device: open OverQueue on it, then test again."
                if stale else "%s turned it away (%s)." % (service, error)))
        elif to_device is not None:
            total = pc_ms + to_service + to_device
            result.update(total_ms=total, verdict=verdict(total), stage=ARRIVED)
        elif finished:
            result.update(verdict=FAILED, failed_at=PUSH_SERVICE, note="Didn't arrive in %d s. %s" % (
                view.get("timeoutSeconds", 60), TIPS[kind]))
        results.append(result)
    return results


def _card(kind, name, service):
    """A device's card before anything is known about the trip."""
    return {"kind": kind, "name": name, "total_ms": None, "failed_at": None, "verdict": WAITING,
            "stops": ["Your PC", "Server", service, name], "times": [None, None, None], "stage": YOUR_PC,
            "note": None}


def sending(kinds=()):
    """The cards up while the PC's request is still on its way to the worker: the devices the
    last test went to, back at the start, so testing again doesn't reshuffle the window. The
    first test doesn't know them yet, and has one card for them all."""
    cards = [_card(kind, name, service) for kind, name, service in DEVICES if kind in kinds]
    if not cards:
        cards = [_card(SENDING, "Your devices", "")]
        cards[0]["stops"][PUSH_SERVICE] = "Push service"
    return cards


def about(result):
    """A card's tooltip."""
    return ABOUT + ("\n\n" + WATCH_NOTE if result["kind"] == "watch" else "")


def summary(results):
    """One line for the log, so a slow report can be looked into afterwards."""
    parts = []
    for result in results:
        if result["total_ms"] is None:
            parts.append("%s %s" % (result["name"], result["verdict"]))
        else:
            parts.append("%s %s (%s)" % (result["name"], seconds(result["total_ms"]),
                                         ", ".join(seconds(ms) for ms in result["times"])))
    return "Notification test: " + "; ".join(parts)


def headline(results):
    """The line next to the buttons once a test is over."""
    arrived = sum(result["total_ms"] is not None for result in results)
    if arrived == len(results):
        return "Your %s got it." % results[0]["name"] if arrived == 1 else "All your devices got it."
    if not arrived:
        return "None of your devices got it."
    return "%d of your %d devices got it." % (arrived, len(results))


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


CARD_WIDTH = 480
PAD = 16
NODE_RADIUS = 18
NODES_Y = 88
NOTE_Y = 140
ACCENT = "#3b94f6"
TRACK = "#2a2f39"
NODE_FILL = "#1f232b"
# How long a step takes to fill in once it's done, so a card that turns up with steps already done
# still plays them through in order, and how long a stop pulses when the trip reaches it.
FILL_SECONDS = 0.35
PULSE_SECONDS = 0.6
# How long the dot takes to cross the step that's under way, over and over until it's done.
CROSSING_SECONDS = 1.1
FRAME_MS = 16
ICONS = {SENDING: "phone", "phone": "phone", "watch": "watch", "android": "android"}
FAILED_STEPS = {YOUR_PC: "failed", SERVER: "turned away", PUSH_SERVICE: "no reply"}
VERDICTS = {FAST: "Fast", OK: "OK", SLOW: "Slow", FAILED: "Didn't arrive", WAITING: "On its way"}


class Timeline(QWidget):
    """One device's card: the four stops the test alert passes through, with the time each step
    took, filling in as the alert gets further.

    It's painted in one go instead of built from labels. Only the picture changes from frame to
    frame, so nothing is laid out again or rebuilt while it animates."""

    def __init__(self, colors, parent=None):
        super().__init__(parent)
        self.colors = colors
        self.result = None
        self.created = time.monotonic()
        # When each step starts to fill in, in order: a step never starts before the one ahead of
        # it has finished, so steps that were done by the time the card heard about them still play.
        self._filled_from = {}
        self.setFixedWidth(CARD_WIDTH)

    def set_result(self, result):
        now = time.monotonic()
        done = result["failed_at"] if result["failed_at"] is not None else result["stage"]
        for step in range(done):
            if step not in self._filled_from:
                self._filled_from[step] = max(now, self._reached(step))
        self.result = result
        self.setToolTip(about(result))
        self.setFixedHeight(self._height())
        self.update()

    def animating(self):
        """Whether the next frame would look any different."""
        result = self.result
        if result is None:
            return False
        if result["failed_at"] is None and result["stage"] < ARRIVED:
            return True
        return time.monotonic() < self._reached(len(self._filled_from)) + PULSE_SECONDS

    def _reached(self, stop):
        """When the picture gets to `stop`: the moment the step into it has filled."""
        if stop == YOUR_PC:
            return self.created
        started = self._filled_from.get(stop - 1)
        return started + FILL_SECONDS if started is not None else float("inf")

    def _height(self):
        note = self.result and self.result["note"]
        if not note:
            return NOTE_Y
        # Measured on this widget, so it's the same size it'll be painted at.
        metrics = QFontMetricsF(_font(8), self)
        rect = metrics.boundingRect(QRectF(0, 0, CARD_WIDTH - 2 * PAD, 1000), int(Qt.TextFlag.TextWordWrap), note)
        return NOTE_Y + math.ceil(rect.height()) + PAD

    def _step_color(self):
        result = self.result
        if result["stage"] == ARRIVED and time.monotonic() >= self._reached(DEVICE):
            return QColor({FAST: self.colors["good"], OK: self.colors["warn"],
                           SLOW: self.colors["bad"]}[result["verdict"]])
        return QColor(ACCENT)

    def paintEvent(self, event):
        if self.result is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        now = time.monotonic()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self.colors["card"]))
        painter.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 12, 12)
        self._paint_header(painter, now)
        self._paint_steps(painter, now)
        self._paint_stops(painter, now)
        if self.result["note"]:
            painter.setFont(_font(8))
            painter.setPen(QColor(self.colors["muted"]))
            painter.drawText(QRectF(PAD, NOTE_Y, self.width() - 2 * PAD, self.height() - NOTE_Y),
                             Qt.TextFlag.TextWordWrap, self.result["note"])
        painter.end()

    def _paint_header(self, painter, now):
        result, colors = self.result, self.colors
        _icon(painter, ICONS[result["kind"]], QPointF(PAD + 11, 27), 22, QColor(colors["text"]))
        painter.setFont(_font(11, QFont.Weight.DemiBold))
        painter.setPen(QColor(colors["text"]))
        painter.drawText(QRectF(PAD + 30, 14, 220, 26), Qt.AlignmentFlag.AlignVCenter, result["name"])

        # The verdict, in a pill at the right, and the total left of it.
        arrived = result["stage"] == ARRIVED and now >= self._reached(DEVICE)
        kind = result["verdict"] if arrived or result["verdict"] in (FAILED, WAITING) else WAITING
        color = QColor({FAST: colors["good"], OK: colors["warn"], SLOW: colors["bad"], FAILED: colors["bad"],
                        WAITING: colors["muted"]}[kind])
        text = VERDICTS[kind]
        painter.setFont(_font(9, QFont.Weight.DemiBold))
        width = QFontMetrics(painter.font()).horizontalAdvance(text) + 22
        pill = QRectF(self.width() - PAD - width, 16, width, 22)
        fill = QColor(color)
        fill.setAlpha(40)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(pill, 11, 11)
        painter.setPen(color)
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)
        if arrived:
            right = pill.left() - 10
            painter.setFont(_font(13, QFont.Weight.DemiBold))
            painter.setPen(QColor(colors["text"]))
            total = seconds(result["total_ms"])
            width = QFontMetrics(painter.font()).horizontalAdvance(total)
            painter.drawText(QRectF(right - width, 12, width, 30), Qt.AlignmentFlag.AlignVCenter, total)
            painter.setFont(_font(9))
            painter.setPen(QColor(colors["muted"]))
            label = QFontMetrics(painter.font()).horizontalAdvance("total ")
            painter.drawText(QRectF(right - width - label, 12, label, 30), Qt.AlignmentFlag.AlignVCenter, "total")

    def _centre(self, stop):
        column = (self.width() - 2 * PAD) / 4
        return QPointF(PAD + column * (stop + 0.5), NODES_Y)

    def _paint_steps(self, painter, now):
        result, colors = self.result, self.colors
        color = self._step_color()
        for step in range(3):
            start = self._centre(step) + QPointF(NODE_RADIUS + 6, 0)
            end = self._centre(step + 1) - QPointF(NODE_RADIUS + 6, 0)
            label_rect = QRectF(start.x() - 10, NODES_Y - 32, end.x() - start.x() + 20, 18)
            painter.setPen(_pen(QColor(TRACK), 3))
            painter.drawLine(start, end)

            filled_from = self._filled_from.get(step)
            if step == result["failed_at"] and now >= self._reached(step):
                bad = QColor(colors["bad"])
                painter.setPen(_pen(bad, 3, Qt.PenStyle.DashLine))
                painter.drawLine(start, end)
                self._step_label(painter, label_rect, FAILED_STEPS[step], bad)
            elif filled_from is not None:
                progress = min(1.0, max(0.0, (now - filled_from) / FILL_SECONDS))
                if progress > 0:
                    painter.setPen(_pen(color, 3))
                    painter.drawLine(start, start + (end - start) * _ease(progress))
                if progress >= 1 and result["times"][step] is not None:
                    self._step_label(painter, label_rect, seconds(result["times"][step]), QColor(colors["text"]))
            elif step == result["stage"] and result["failed_at"] is None and now >= self._reached(step):
                self._paint_crossing(painter, start, end, now - self._reached(step))
                # From when the card turned up, which is about when the worker sent it on, rather
                # than from when the steps before it finished playing.
                waited = now - self.created
                self._step_label(painter, label_rect, "%d s…" % waited if waited >= 1 else "…",
                                 QColor(colors["muted"]))

    def _paint_crossing(self, painter, start, end, elapsed):
        """The dot running along the step that's under way, trailing a fading tail."""
        phase = (elapsed % CROSSING_SECONDS) / CROSSING_SECONDS
        head = start + (end - start) * _ease(phase)
        tail = QColor(ACCENT)
        for i in range(8, 0, -1):
            tail.setAlphaF(0.08 * (9 - i) / 8 * min(1.0, phase * 4))
            point = head - QPointF(i * 4, 0)
            if point.x() >= start.x():
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(tail)
                painter.drawEllipse(point, 3, 3)
        glow = QColor(ACCENT)
        glow.setAlpha(60)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(head, 8, 8)
        painter.setBrush(QColor(ACCENT))
        painter.drawEllipse(head, 4.5, 4.5)

    def _step_label(self, painter, rect, text, color):
        painter.setFont(_font(9))
        painter.setPen(color)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_stops(self, painter, now):
        result, colors = self.result, self.colors
        color = self._step_color()
        icons = ("pc", "server", "cloud", ICONS[result["kind"]])
        failed_stop = result["failed_at"] + 1 if result["failed_at"] is not None else None
        heading_to = result["stage"] + 1 if result["failed_at"] is None and result["stage"] < ARRIVED else None
        column = (self.width() - 2 * PAD) / 4
        for stop in range(4):
            centre = self._centre(stop)
            reached_at = self._reached(stop)
            reached = now >= reached_at and stop != failed_stop
            if reached:
                fill = QColor(color)
                fill.setAlpha(45)
                painter.setBrush(fill)
                painter.setPen(QPen(color, 1.5))
            else:
                painter.setBrush(QColor(NODE_FILL))
                edge = QColor(colors["bad"]) if stop == failed_stop and now >= self._reached(stop - 1) else QColor(TRACK)
                painter.setPen(QPen(edge, 1.5))
            painter.drawEllipse(centre, NODE_RADIUS, NODE_RADIUS)
            _icon(painter, icons[stop], centre, 20, QColor(colors["text"] if reached else colors["muted"]))

            # A ring going out from a stop the moment the alert gets there, and a slow breath
            # around the one it's heading for.
            ring = QColor(color)
            if reached and now - reached_at < PULSE_SECONDS:
                phase = (now - reached_at) / PULSE_SECONDS
                ring.setAlphaF(0.6 * (1 - phase))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(ring, 2))
                painter.drawEllipse(centre, NODE_RADIUS + 10 * phase, NODE_RADIUS + 10 * phase)
            elif stop == heading_to and now >= self._reached(stop - 1):
                ring = QColor(ACCENT)
                ring.setAlphaF(0.25 + 0.25 * _breath(now))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(ring, 1.5, Qt.PenStyle.DashLine))
                painter.drawEllipse(centre, NODE_RADIUS + 4, NODE_RADIUS + 4)

            painter.setFont(_font(8))
            painter.setPen(QColor(colors["text"] if reached else colors["muted"]))
            painter.drawText(QRectF(centre.x() - column / 2 + 2, NODES_Y + NODE_RADIUS + 6, column - 4, 32),
                             int(Qt.AlignmentFlag.AlignHCenter) | int(Qt.TextFlag.TextWordWrap), result["stops"][stop])


def _pen(color, width, style=Qt.PenStyle.SolidLine):
    return QPen(color, width, style, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)


def _ease(t):
    """Quick off the mark, gentle into the stop."""
    return 1 - (1 - t) ** 3


def _breath(now):
    phase = (now % 1.6) / 1.6
    return 1 - abs(2 * phase - 1)


def _icon(painter, name, centre, size, color):
    """A line icon drawn on a 24-unit grid, `size` pixels across, centred on `centre`."""
    painter.save()
    painter.translate(centre.x() - size / 2, centre.y() - size / 2)
    painter.scale(size / 24, size / 24)
    painter.setPen(_pen(color, 1.8))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    dots = []
    if name == "pc":
        path.addRoundedRect(QRectF(3, 4, 18, 12), 2, 2)
        path.moveTo(12, 16)
        path.lineTo(12, 20)
        path.moveTo(8, 20)
        path.lineTo(16, 20)
    elif name == "server":
        path.addRoundedRect(QRectF(4, 3.5, 16, 7), 2, 2)
        path.addRoundedRect(QRectF(4, 13.5, 16, 7), 2, 2)
        dots = [QPointF(8, 7), QPointF(8, 17)]
    elif name == "cloud":
        cloud = QPainterPath()
        cloud.addEllipse(QPointF(12, 11.5), 5.5, 5.5)
        for centre, radius in ((QPointF(17, 15.5), 4), (QPointF(7, 16), 3.5)):
            bump = QPainterPath()
            bump.addEllipse(centre, radius, radius)
            cloud = cloud.united(bump)
        base = QPainterPath()
        base.addRect(QRectF(7, 15.5, 10, 4))
        path = cloud.united(base)
    elif name == "watch":
        path.addRoundedRect(QRectF(6, 6.5, 12, 11), 3.5, 3.5)
        for top, edge in ((6.5, 3), (17.5, 21)):
            path.moveTo(8.5, top)
            path.lineTo(9.3, edge)
            path.lineTo(14.7, edge)
            path.lineTo(15.5, top)
        path.moveTo(18, 11)
        path.lineTo(19.5, 11)
    else:  # a phone: "phone" has the home bar at the bottom, "android" a camera at the top
        path.addRoundedRect(QRectF(7, 2.5, 10, 19), 2.5, 2.5)
        if name == "android":
            dots = [QPointF(12, 5.5)]
        else:
            path.moveTo(10.5, 18.5)
            path.lineTo(13.5, 18.5)
    painter.drawPath(path)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    for dot in dots:
        painter.drawEllipse(dot, 1, 1)
    painter.restore()


class SpeedTestDialog(QDialog):
    """Runs a test when opened and shows it as it comes in: one timeline per paired device."""

    def __init__(self, parent, pair_id, colors, log=print):
        super().__init__(parent)
        self.pair_id = pair_id
        self.colors = colors
        self.log = log
        self.test = None
        self.started_at = None
        self.snapshot = {"results": [], "finished": False, "error": None}
        self.timelines = {}
        self.kinds = ()  # the devices the last test went to
        self.tallest = 0
        self.signals = _Signals()
        self.signals.update.connect(self._show)
        self.frames = QTimer(self, interval=FRAME_MS, timeout=self._animate)

        self.setWindowTitle("Notification speed test")
        self.setFixedWidth(CARD_WIDTH + 40)
        self.setStyleSheet(parent.styleSheet() if parent else "")
        # No heading: the title bar already says what this is.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 18)
        layout.setSpacing(10)
        self.cards = QVBoxLayout()
        self.cards.setSpacing(10)
        layout.addLayout(self.cards)
        layout.addStretch()  # whatever the window has over, below the cards rather than between them

        self.status = QLabel(font=_font(9), wordWrap=True, textFormat=Qt.TextFormat.RichText)
        self.again = QPushButton("Test again", font=_font(10))
        self.again.clicked.connect(self.run)
        close = QPushButton("Close", font=_font(10))
        close.clicked.connect(self.close)
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.again)
        bottom.addWidget(close)
        layout.addLayout(bottom)

    def run(self):
        if self.test:
            self.test.cancel()
        self.started_at = time.monotonic()
        self.test = Test(self.pair_id, self.signals.update.emit, log=self.log)
        self._show({"results": [], "finished": False, "error": None}, restart=True)
        self.test.start()

    def closeEvent(self, event):
        if self.test:
            self.test.cancel()
        self.frames.stop()
        super().closeEvent(event)

    def _show(self, snapshot, restart=False):
        """Something changed: at most a few times a test, when the worker answers and as each
        device does. The cards are only made again when the devices on them change."""
        self.snapshot = snapshot
        results = snapshot["results"]
        if results:
            self.kinds = [result["kind"] for result in results]
        elif snapshot["error"]:
            results = [dict(card, failed_at=YOUR_PC, verdict=FAILED) for card in sending(self.kinds)]
        elif not snapshot["finished"]:
            results = sending(self.kinds)
        kinds = [result["kind"] for result in results]
        if restart or kinds != list(self.timelines):
            for timeline in self.timelines.values():
                self.cards.removeWidget(timeline)
                timeline.deleteLater()
            self.timelines = {kind: Timeline(self.colors, self) for kind in kinds}
            for timeline in self.timelines.values():
                self.cards.addWidget(timeline)
        for result in results:
            self.timelines[result["kind"]].set_result(result)
        self._status()
        self.refresh()
        # Like the main window: the width is fixed, and the height is whatever the cards and the
        # wrapped text under them need at that width (adjustSize() comes up short of the wrapping).
        # It only ever grows while it's open: shrinking as a test starts over would pull the
        # buttons out from under the pointer.
        self.tallest = max(self.tallest, self.layout().totalHeightForWidth(self.width()))
        self.setFixedHeight(self.tallest)
        if not self.frames.isActive():
            self.frames.start()

    def _animate(self):
        moving = [timeline for timeline in self.timelines.values() if timeline.animating()]
        for timeline in moving:
            timeline.update()
        if not moving:
            self.frames.stop()

    def _status(self):
        snapshot, colors = self.snapshot, self.colors
        if snapshot["error"]:
            line, color = snapshot["error"], colors["warn"]
        elif not snapshot["results"]:
            line, color = "Sending…", colors["muted"]
        elif not snapshot["finished"]:
            line, color = "Waiting for your devices…", colors["muted"]
        else:
            line, color = headline(snapshot["results"]), colors["text"]
        self.status.setText('<span style="color: %s;">%s</span>' % (color, line))

    def refresh(self):
        """Also called on the app's own timer, for the "Test again" countdown. Only text that has
        actually changed is set, so this costs nothing the rest of the time."""
        elapsed = time.monotonic() - self.started_at if self.started_at else 0
        wait = TEST_INTERVAL_SECONDS - elapsed
        self.again.setEnabled(self.snapshot["finished"] and wait <= 0)
        self.again.setText("Test again" if wait <= 0 else "Test again (%d s)" % (wait + 0.999))


def _font(size, weight=QFont.Weight.Normal):
    font = QFont("Segoe UI", size)
    font.setWeight(weight)
    return font
