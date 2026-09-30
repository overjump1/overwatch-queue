"""The notification speed test: what it shows, and how it talks to the worker."""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import relay as r  # noqa: E402
import speedtest as st  # noqa: E402

PAIR_ID = "a" * 32
TEST_ID = "b" * 32


def view(devices, timeout=60):
    return {"testId": TEST_ID, "elapsedMs": 0, "timeoutSeconds": timeout, "devices": devices}


class FormattingTests(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(st.seconds(80), "0.08 s")
        self.assertEqual(st.seconds(1300), "1.30 s")
        self.assertEqual(st.seconds(12_400), "12.4 s")
        self.assertEqual(st.seconds(-5), "0.00 s")

    def test_verdicts(self):
        self.assertEqual(st.verdict(1999), st.FAST)
        self.assertEqual(st.verdict(2000), st.OK)
        self.assertEqual(st.verdict(5000), st.SLOW)


class ResultTests(unittest.TestCase):
    def test_an_arrived_device_adds_its_steps_up(self):
        [android] = st.device_results(view({"android": {"toServiceMs": 300, "toDeviceMs": 900, "error": None}}),
                                      pc_ms=100, finished=False)
        self.assertEqual(android["name"], "Android phone")
        self.assertEqual(android["total_ms"], 1300)
        self.assertEqual(android["verdict"], st.FAST)
        self.assertEqual(android["times"], [100, 300, 900])
        self.assertEqual(android["stage"], st.ARRIVED)
        self.assertIsNone(android["failed_at"])
        self.assertEqual(android["stops"], ["Your PC", "Server", "Google", "Android phone"])
        self.assertIsNone(android["note"])

    def test_the_iphone_and_watch_are_done_once_apple_has_it(self):
        phone, watch = st.device_results(view({
            "phone": {"toServiceMs": 300, "toDeviceMs": None, "error": None},
            "watch": {"toServiceMs": 400, "toDeviceMs": None, "error": None},
        }), pc_ms=100, finished=False)
        self.assertEqual(phone["stops"], ["Your PC", "Server", "Apple", "iPhone"])
        self.assertEqual(phone["times"], [100, 300, None])
        self.assertEqual(phone["total_ms"], 400)
        self.assertEqual(phone["stage"], st.ARRIVED)
        self.assertEqual(watch["total_ms"], 500)
        self.assertEqual(watch["verdict"], st.FAST)

    def test_the_iphone_and_watch_say_what_their_time_leaves_out_on_hover(self):
        for kind in ("phone", "watch"):
            self.assertIn(st.UNTIMED_NOTE, st.about(st.sending([kind])[0]))
        self.assertNotIn(st.UNTIMED_NOTE, st.about(st.sending(["android"])[0]))

    def test_devices_come_in_a_fixed_order_and_only_if_paired(self):
        results = st.device_results(view({
            "android": {"toServiceMs": 1, "toDeviceMs": 1, "error": None},
            "phone": {"toServiceMs": 1, "toDeviceMs": 1, "error": None},
        }), pc_ms=0, finished=False)
        self.assertEqual([result["kind"] for result in results], ["phone", "android"])

    def test_a_device_still_on_its_way_is_waiting_until_the_test_ends(self):
        devices = {"android": {"toServiceMs": 200, "toDeviceMs": None, "error": None}}
        [waiting] = st.device_results(view(devices), pc_ms=0, finished=False)
        self.assertEqual(waiting["verdict"], st.WAITING)
        self.assertEqual(waiting["stage"], st.PUSH_SERVICE)
        self.assertEqual(waiting["times"], [0, 200, None])
        self.assertEqual(waiting["stops"][2], "Google")
        [late] = st.device_results(view(devices, timeout=60), pc_ms=0, finished=True)
        self.assertEqual(late["verdict"], st.FAILED)
        self.assertEqual(late["failed_at"], st.PUSH_SERVICE)
        self.assertTrue(late["note"].startswith("Didn't arrive in 60 s."))
        self.assertIn(st.TIPS["android"], late["note"])

    def test_testing_again_starts_the_last_devices_over(self):
        phone, watch = st.sending(["watch", "phone"])
        self.assertEqual([phone["kind"], watch["kind"]], ["phone", "watch"])
        self.assertEqual(phone["stage"], st.YOUR_PC)
        self.assertEqual(phone["times"], [None, None, None])
        self.assertEqual(phone["verdict"], st.WAITING)

    def test_the_first_test_has_one_card_for_every_device(self):
        [card] = st.sending()
        self.assertEqual(card["kind"], st.SENDING)
        self.assertEqual(card["stops"], ["Your PC", "Server", "Push service", "Your devices"])

    def test_a_stale_registration_says_how_to_fix_it(self):
        [phone] = st.device_results(view({"phone": {"toServiceMs": None, "toDeviceMs": None,
                                                    "error": "UNREGISTERED"}}), pc_ms=0, finished=False)
        self.assertEqual(phone["verdict"], st.FAILED)
        self.assertEqual(phone["failed_at"], st.SERVER)
        self.assertIn("open QueueFox on it", phone["note"])

    def test_the_log_line_has_every_step(self):
        results = st.device_results(view({
            "phone": {"toServiceMs": 300, "toDeviceMs": None, "error": None},
            "android": {"toServiceMs": 300, "toDeviceMs": 900, "error": None},
            "watch": {"toServiceMs": None, "toDeviceMs": None, "error": None},
        }), pc_ms=100, finished=True)
        self.assertEqual(st.summary(results),
                         "Notification test: iPhone 0.40 s (0.10 s, 0.30 s, not timed); Apple Watch failed; "
                         "Android phone 1.30 s (0.10 s, 0.30 s, 0.90 s)")


class HeadlineTests(unittest.TestCase):
    def results(self, devices):
        return st.device_results(view(devices), pc_ms=100, finished=True)

    def test_every_device_got_it(self):
        self.assertEqual(st.headline(self.results({
            "phone": {"toServiceMs": 300, "toDeviceMs": None, "error": None},
            "android": {"toServiceMs": 300, "toDeviceMs": 2000, "error": None},
        })), "All your devices got it.")

    def test_one_device(self):
        self.assertEqual(st.headline(self.results({"phone": {"toServiceMs": 300, "toDeviceMs": None, "error": None}})),
                         "Your iPhone got it.")

    def test_counts_the_ones_that_didnt(self):
        self.assertEqual(st.headline(self.results({
            "phone": {"toServiceMs": 300, "toDeviceMs": None, "error": None},
            "android": {"toServiceMs": 300, "toDeviceMs": None, "error": None},
        })), "1 of your 2 devices got it.")
        self.assertEqual(st.headline(self.results({"android": {"toServiceMs": 300, "toDeviceMs": None, "error": None}})),
                         "None of your devices got it.")


class TimelineTests(unittest.TestCase):
    """The cards are painted by hand, so paint one in every state there is and check nothing raises."""

    COLORS = {"text": "#fff", "muted": "#888", "good": "#0f0", "warn": "#fa0", "bad": "#f00", "card": "#222"}

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_paints_every_state(self):
        from PyQt6.QtGui import QImage
        colors = self.COLORS
        states = [{
            "phone": {"toServiceMs": 300, "toDeviceMs": None, "error": None},
            "watch": {"toServiceMs": None, "toDeviceMs": None, "error": None},
            "android": {"toServiceMs": 300, "toDeviceMs": None, "error": None},
        }, {
            "phone": {"toServiceMs": None, "toDeviceMs": None, "error": "UNREGISTERED"},
            "android": {"toServiceMs": 300, "toDeviceMs": 900, "error": None},
        }]
        results = st.sending() + st.sending(["phone", "watch"])
        for devices in states:
            for finished in (False, True):
                results += st.device_results(view(devices), pc_ms=100, finished=finished)
        for result in results:
            timeline = st.Timeline(colors)
            timeline.set_result(result)
            for age in (0.2, 60):  # mid-animation, then settled
                timeline.created = time.monotonic() - age
                timeline._filled_from = {step: timeline.created for step in timeline._filled_from}
                timeline.render(QImage(timeline.size(), QImage.Format.Format_ARGB32))
            self.assertGreater(timeline.height(), 0)

    def test_the_window_never_shrinks_while_its_open(self):
        dialog = st.SpeedTestDialog(None, PAIR_ID, self.COLORS)
        done = st.device_results(view({
            "phone": {"toServiceMs": 300, "toDeviceMs": None, "error": None},
            "watch": {"toServiceMs": None, "toDeviceMs": None, "error": None},
        }), pc_ms=100, finished=True)
        dialog._show({"results": done, "finished": True, "error": None})
        height = dialog.height()
        dialog._show({"results": [], "finished": False, "error": None}, restart=True)
        self.assertEqual(list(dialog.timelines), ["phone", "watch"])
        self.assertEqual(dialog.height(), height)
        dialog._show({"results": [], "finished": True, "error": "Nothing is paired yet: scan the QR code first.",
                      "failed_at": st.SERVER})
        self.assertEqual(dialog.height(), height)
        self.assertEqual({timeline.result["failed_at"] for timeline in dialog.timelines.values()}, {st.SERVER})
        dialog.close()


class RunTests(unittest.TestCase):
    """A run against a fake worker: start, then poll until every device has answered."""

    def setUp(self):
        self.calls = []
        self.answers = []
        real = r._request
        r._request = self.request
        self.addCleanup(setattr, r, "_request", real)
        real_poll = st.POLL_SECONDS
        st.POLL_SECONDS = 0.01
        self.addCleanup(setattr, st, "POLL_SECONDS", real_poll)
        self.updates = []
        self.done = threading.Event()

    def request(self, method, path, body=None):
        self.calls.append((method, path))
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]

    def on_update(self, snapshot):
        self.updates.append(snapshot)
        if snapshot["finished"]:
            self.done.set()

    def run_test(self):
        st.Test(PAIR_ID, self.on_update, log=lambda *args: None).start()
        self.assertTrue(self.done.wait(5))
        return self.updates[-1]

    def test_polls_until_every_device_has_answered(self):
        waiting = {"android": {"toServiceMs": 300, "toDeviceMs": None, "error": None}}
        arrived = {"android": {"toServiceMs": 300, "toDeviceMs": 900, "error": None}}
        self.answers = [(200, dict(view(waiting), handledMs=300)), (200, view(waiting)), (200, view(arrived))]
        final = self.run_test()
        self.assertEqual(self.calls[0], ("POST", "/v1/pair/%s/test" % PAIR_ID))
        self.assertEqual(self.calls[1], ("GET", "/v1/pair/%s/test/%s" % (PAIR_ID, TEST_ID)))
        self.assertEqual(len(self.calls), 3)
        self.assertIsNone(final["error"])
        [android] = final["results"]
        self.assertEqual(android["verdict"], st.FAST)

    def test_too_soon(self):
        self.answers = [(429, {"error": "too_soon", "retryAfter": 9})]
        final = self.run_test()
        self.assertEqual(final["error"], "A test just ran. Try again in 9 s.")
        self.assertEqual(final["failed_at"], st.SERVER)  # it answered, just not yes

    def test_nothing_paired(self):
        self.answers = [(409, None)]
        self.assertIn("Nothing is paired", self.run_test()["error"])

    def test_an_unreachable_worker_is_an_error_not_a_crash(self):
        def fail(*args, **kwargs):
            raise OSError("no network")
        r._request = fail
        final = self.run_test()
        self.assertEqual(final["error"], "Couldn't reach the notification server.")
        self.assertEqual(final["failed_at"], st.YOUR_PC)


if __name__ == "__main__":
    unittest.main()
