"""What the PC says to the worker, and how little of it there needs to be."""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import presence as p  # noqa: E402
import relay as r  # noqa: E402
from app import Watcher  # noqa: E402

PAIR_ID = "a" * 32
OTHER_ID = "b" * 32


class FakePresence:
    """Hands back whatever reading the test set, instead of asking Battle.net."""

    def __init__(self):
        self.reading = (p.MENUS, None)
        self.connected = True

    def read(self):
        return self.reading


class FakeRoleSelect:
    """Never manages to look at the screen, so a queue is confirmed on its first reading."""

    def visible(self):
        return None


class FakeRelay:
    def __init__(self):
        self.published = []

    def publish(self, state, mode, elapsed, since_found=0.0):
        self.published.append((state, mode))


class ReportTests(unittest.TestCase):
    """The PC speaks only when something changes. The worker keeps whatever it was last told
    until it's told otherwise, so there's nothing to keep alive: a queue that lasts a quarter
    of an hour is one request, and a PC that goes away leaves its last state standing."""

    def setUp(self):
        self.relay = FakeRelay()
        self.watcher = Watcher(self.relay, FakePresence())
        self.watcher.role_select = FakeRoleSelect()

    def test_a_queue_is_reported_once_however_long_it_lasts(self):
        self.watcher.presence.reading = (p.QUEUEING, "competitive")
        for _ in range(100):
            self.watcher.tick()
        self.assertEqual(self.relay.published, [("queueing", "competitive")])

    def test_an_idle_pc_is_reported_once_and_then_says_nothing(self):
        for _ in range(100):
            self.watcher.tick()
        self.assertEqual(self.relay.published, [("idle", None)])

    def test_every_change_is_reported(self):
        self.watcher.tick()
        self.watcher.presence.reading = (p.QUEUEING, "competitive")
        self.watcher.tick()
        self.watcher.presence.reading = (p.IN_GAME, "competitive")
        self.watcher.tick()
        self.watcher.presence.reading = (p.MENUS, None)
        self.watcher.tick()
        self.assertEqual([state for state, _ in self.relay.published],
                         ["idle", "queueing", "found", "idle"])


class PairingTests(unittest.TestCase):
    """Every report comes back saying who's paired, so the PC needs no second request."""

    def setUp(self):
        self.calls = []
        self.answer = {"status": {}}
        real = r._request
        r._request = self.request
        self.addCleanup(setattr, r, "_request", real)
        self.relay = r.Relay(PAIR_ID, log=lambda *args: None)

    def request(self, method, path, body=None):
        self.calls.append((method, path))
        return 200, dict(self.answer)

    def methods(self):
        return [method for method, _ in self.calls]

    def test_a_report_brings_back_who_is_paired(self):
        self.answer = {"status": {}, "phonePaired": True, "androidPaired": True}
        self.relay.publish("queueing", "competitive", 5.0)
        self.assertTrue(self.relay._send_pending())
        self.assertEqual(self.relay.paired, ["iPhone", "Android"])
        self.assertEqual(self.methods(), ["POST"])

    def test_an_answer_for_a_pairing_that_was_reset_is_dropped(self):
        self.relay._set_paired(OTHER_ID, ["iPhone"])
        self.assertEqual(self.relay.paired, [])

    def test_a_paired_pc_stops_asking(self):
        """The report answered it, and a pairing the worker knows about never goes away."""
        self.answer = {"status": {}, "phonePaired": True}
        self.relay.publish("queueing", "competitive", 5.0)
        stop = threading.Event()
        thread = threading.Thread(target=self.relay.run, args=(stop,), daemon=True)
        thread.start()
        deadline = time.monotonic() + 2
        while not self.calls and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.05)  # a check, if it were coming, follows the report in the same pass
        stop.set()
        self.relay._wake.set()
        thread.join(timeout=2)
        self.assertEqual(self.relay.paired, ["iPhone"])
        self.assertEqual(self.methods(), ["POST"])

    def test_a_worker_too_old_to_answer_falls_back_to_asking(self):
        """Reading a silent reply as "nobody's paired" would leave the QR code up for good."""
        self.answer = {"status": {}}  # no pairing keys at all
        self.relay.publish("queueing", "competitive", 5.0)
        self.assertTrue(self.relay._send_pending())
        self.assertEqual(self.relay.paired, [])
        self.answer = {"status": {}, "phonePaired": True}
        self.relay._check_paired()
        self.assertEqual(self.relay.paired, ["iPhone"])
        self.assertEqual(self.methods(), ["POST", "GET"])

    def test_a_pc_nobody_scanned_yet_does_ask(self):
        stop = threading.Event()
        thread = threading.Thread(target=self.relay.run, args=(stop,), daemon=True)
        thread.start()
        deadline = time.monotonic() + 2
        while not self.calls and time.monotonic() < deadline:
            time.sleep(0.01)
        stop.set()
        self.relay._wake.set()
        thread.join(timeout=2)
        self.assertEqual(self.methods(), ["GET"])


if __name__ == "__main__":
    unittest.main()
