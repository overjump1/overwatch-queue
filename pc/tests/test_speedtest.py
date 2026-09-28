"""The notification speed test: what it shows, and how it talks to the worker."""
import os
import sys
import threading
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
        [phone] = st.device_results(view({"phone": {"toServiceMs": 300, "toDeviceMs": 900, "error": None}}),
                                    pc_ms=100, finished=False)
        self.assertEqual(phone["name"], "iPhone")
        self.assertEqual(phone["total_ms"], 1300)
        self.assertEqual(phone["verdict"], st.FAST)
        self.assertEqual([ms for _, ms in phone["steps"]], [100, 300, 900])
        self.assertEqual(phone["steps"][2][0], "Apple → your iPhone")
        self.assertIsNone(phone["note"])

    def test_the_watch_says_what_its_time_leaves_out(self):
        [watch] = st.device_results(view({"watch": {"toServiceMs": 300, "toDeviceMs": 900, "error": None}}),
                                    pc_ms=100, finished=True)
        self.assertEqual(watch["note"], st.WATCH_NOTE)

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
        [late] = st.device_results(view(devices, timeout=60), pc_ms=0, finished=True)
        self.assertEqual(late["verdict"], st.FAILED)
        self.assertTrue(late["note"].startswith("Didn't arrive within 60 s."))
        self.assertIn(st.TIPS["android"], late["note"])

    def test_a_stale_registration_says_how_to_fix_it(self):
        [phone] = st.device_results(view({"phone": {"toServiceMs": None, "toDeviceMs": None,
                                                    "error": "UNREGISTERED"}}), pc_ms=0, finished=False)
        self.assertEqual(phone["verdict"], st.FAILED)
        self.assertIn("open OverQueue on it", phone["note"])

    def test_the_log_line_has_every_step(self):
        results = st.device_results(view({
            "phone": {"toServiceMs": 300, "toDeviceMs": 900, "error": None},
            "watch": {"toServiceMs": 300, "toDeviceMs": None, "error": None},
        }), pc_ms=100, finished=True)
        self.assertEqual(st.summary(results),
                         "Notification test: iPhone 1.30 s (0.10 s, 0.30 s, 0.90 s); Apple Watch failed")


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
        waiting = {"phone": {"toServiceMs": 300, "toDeviceMs": None, "error": None}}
        arrived = {"phone": {"toServiceMs": 300, "toDeviceMs": 900, "error": None}}
        self.answers = [(200, dict(view(waiting), handledMs=300)), (200, view(waiting)), (200, view(arrived))]
        final = self.run_test()
        self.assertEqual(self.calls[0], ("POST", "/v1/pair/%s/test" % PAIR_ID))
        self.assertEqual(self.calls[1], ("GET", "/v1/pair/%s/test/%s" % (PAIR_ID, TEST_ID)))
        self.assertEqual(len(self.calls), 3)
        self.assertIsNone(final["error"])
        [phone] = final["results"]
        self.assertEqual(phone["verdict"], st.FAST)

    def test_too_soon(self):
        self.answers = [(429, {"error": "too_soon", "retryAfter": 9})]
        self.assertEqual(self.run_test()["error"], "A test just ran. Try again in 9 s.")

    def test_nothing_paired(self):
        self.answers = [(409, None)]
        self.assertIn("Nothing is paired", self.run_test()["error"])

    def test_an_unreachable_worker_is_an_error_not_a_crash(self):
        def fail(*args, **kwargs):
            raise OSError("no network")
        r._request = fail
        self.assertEqual(self.run_test()["error"], "Couldn't reach the notification server.")


if __name__ == "__main__":
    unittest.main()
