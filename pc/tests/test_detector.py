import os
import sys
import time
import unittest
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import presence as p  # noqa: E402
from detector import FOUND, IDLE, QUEUEING, Detector  # noqa: E402


def _scan(menus=0, queue=0, program=0):
    """One scan of Battle.net's memory: how many copies of each presence string it held."""
    counts = {}
    if menus:
        counts[("status", "In Menus")] = menus
    if queue:
        counts[("status", "Competitive: In Queue")] = queue
    if program:
        counts[("program", "Pro")] = program
    return Counter(counts)


class DetectorTests(unittest.TestCase):
    def setUp(self):
        self.d = Detector(0)
        self.d.step(0, p.MENUS, None)

    def test_quick_queue_and_found(self):
        self.d.step(1, p.QUEUEING, "arcade")
        self.assertEqual((self.d.state, self.d.mode), (QUEUEING, "arcade"))
        self.d.step(90, p.IN_GAME, "arcade")
        self.assertEqual(self.d.state, FOUND)
        self.assertEqual(self.d.elapsed(200), 89)

    def test_since_found_counts_from_the_match(self):
        self.d.step(1, p.QUEUEING, "quickPlay")
        self.assertEqual(self.d.since_found(50), 0)
        self.d.step(90, p.IN_GAME, "quickPlay")
        self.assertEqual(self.d.since_found(150), 60)
        self.d.step(160, p.MENUS, None)
        self.assertEqual(self.d.since_found(170), 0)

    def test_role_select_holds_until_it_closes(self):
        self.assertTrue(self.d.wants_role_check(p.QUEUEING, "competitive"))
        self.d.step(1, p.QUEUEING, "competitive", role_select=True)
        self.d.step(2, p.QUEUEING, "competitive", role_select=True)
        self.assertEqual(self.d.state, IDLE)
        self.assertTrue(self.d.holding_for_role_select)
        self.d.step(3, p.QUEUEING, "competitive", role_select=False)
        self.assertEqual(self.d.state, IDLE)
        self.d.step(4, p.QUEUEING, "competitive", role_select=False)
        self.assertEqual(self.d.state, QUEUEING)
        self.assertEqual(self.d.started_at, 3)
        self.assertFalse(self.d.wants_role_check(p.QUEUEING, "competitive"))

    def test_role_select_keeps_holding_when_vision_cannot_look(self):
        self.d.step(1, p.QUEUEING, "competitive", role_select=True)
        for t in range(2, 6):
            self.d.step(t, p.QUEUEING, "competitive", role_select=None)
        self.assertEqual(self.d.state, IDLE)
        self.assertTrue(self.d.holding_for_role_select)

    def test_role_select_closed_back_to_menus(self):
        self.d.step(1, p.QUEUEING, "competitive", role_select=True)
        self.d.step(2, p.MENUS, None)
        self.assertEqual(self.d.state, IDLE)
        self.assertFalse(self.d.holding_for_role_select)

    def test_in_game_from_idle_is_not_a_match(self):
        self.d.step(1, p.IN_GAME, "quickPlay")
        self.assertEqual(self.d.state, IDLE)

    def test_practice_range_keeps_queue(self):
        self.d.step(1, p.QUEUEING, "quickPlay")
        self.d.step(5, p.PLAYING_OTHER, None)
        self.assertEqual(self.d.state, QUEUEING)

    def test_custom_game_while_queueing_is_not_found(self):
        self.d.step(1, p.QUEUEING, "quickPlay")
        self.d.step(5, p.IN_GAME, "custom")
        self.assertEqual(self.d.state, QUEUEING)

    def test_leaving_queue(self):
        self.d.step(1, p.QUEUEING, "quickPlay")
        self.d.step(5, p.MENUS, None)
        self.assertEqual((self.d.state, self.d.mode), (IDLE, None))

    def test_leaving_queue_for_another_game(self):
        self.d.step(1, p.QUEUEING, "quickPlay")
        self.d.step(5, p.ELSEWHERE, None)
        self.assertEqual(self.d.state, IDLE)

    def test_requeue_after_cancel_restarts_the_timer(self):
        self.d.step(1, p.QUEUEING, "arcade")
        self.d.step(50, p.MENUS, None)
        self.assertEqual(self.d.elapsed(55), 0)
        self.d.step(60, p.QUEUEING, "arcade")
        self.assertEqual(self.d.state, QUEUEING)
        self.assertEqual(self.d.elapsed(70), 10)

    def test_cancelled_queue_is_never_a_match(self):
        self.d.step(1, p.QUEUEING, "quickPlay")
        self.d.step(5, p.MENUS, None)
        self.d.step(9, p.IN_GAME, "quickPlay")
        self.assertEqual(self.d.state, IDLE)
        self.assertEqual(self.d.since_found(20), 0)


        self.d.step(1, p.QUEUEING, "quickPlay")
        self.d.step(5, p.IN_GAME, "quickPlay")
        self.d.step(600, p.GAME_ENDING, "quickPlay")
        self.assertEqual(self.d.state, IDLE)

    def test_a_long_presence_loss_resumes_the_queue_with_its_timer(self):
        self.d.step(1, p.QUEUEING, "competitive")
        self.d.step(62, p.UNKNOWN, None)
        self.assertEqual(self.d.state, IDLE)
        self.d.step(70, p.QUEUEING, "competitive")
        self.assertEqual((self.d.state, self.d.started_at), (QUEUEING, 1))
        self.assertEqual(self.d.elapsed(80), 79)

    def test_resume_skips_the_role_select_gate(self):
        self.d.step(1, p.QUEUEING, "competitive")
        self.d.step(62, p.UNKNOWN, None)
        self.d.step(70, p.QUEUEING, "competitive", role_select=True)
        self.assertEqual((self.d.state, self.d.started_at), (QUEUEING, 1))
        self.assertFalse(self.d.holding_for_role_select)

    def test_a_cancelled_queue_is_not_resumed(self):
        self.d.step(1, p.QUEUEING, "arcade")
        self.d.step(50, p.MENUS, None)
        self.d.step(60, p.QUEUEING, "arcade")
        self.assertEqual(self.d.started_at, 60)

    def test_a_queue_cancelled_during_the_blackout_is_not_resumed(self):
        self.d.step(1, p.QUEUEING, "competitive")
        self.d.step(62, p.UNKNOWN, None)
        self.d.step(71, p.PLAYING_OTHER, None)
        self.d.step(100, p.QUEUEING, "competitive")
        self.assertEqual(self.d.started_at, 100)

    def test_a_match_found_during_the_blackout_does_not_resume_later(self):
        self.d.step(1, p.QUEUEING, "competitive")
        self.d.step(62, p.UNKNOWN, None)
        self.d.step(71, p.IN_GAME, "competitive")
        self.d.step(100, p.QUEUEING, "competitive")
        self.assertEqual(self.d.started_at, 100)

    def test_resume_expires(self):
        self.d.step(1, p.QUEUEING, "competitive")
        self.d.step(62, p.UNKNOWN, None)
        self.d.step(160, p.QUEUEING, "competitive")
        self.assertEqual(self.d.started_at, 160)

    def test_presence_lost(self):
        self.d.step(1, p.QUEUEING, "quickPlay")
        self.d.step(30, p.UNKNOWN, None)
        self.assertEqual(self.d.state, QUEUEING)
        self.d.step(62, p.UNKNOWN, None)
        self.assertEqual(self.d.state, IDLE)

    def test_starts_unknown_until_first_reading(self):
        d = Detector(0)
        d.step(10, p.UNKNOWN, None)
        self.assertIsNone(d.state)
        d.step(61, p.UNKNOWN, None)
        self.assertEqual(d.state, IDLE)


class RelayReportTests(unittest.TestCase):
    def test_times_are_aged_to_when_the_report_is_sent(self):
        import relay
        now = relay.time.monotonic()
        found = relay._report({"state": "found", "mode": "arcade", "elapsed": 90.0, "sinceFound": 5.0, "at": now - 40})
        self.assertEqual((found["elapsed"], found["sinceFound"]), (90, 45))
        queued = relay._report({"state": "queueing", "mode": None, "elapsed": 10.0, "sinceFound": 0.0, "at": now - 20})
        self.assertEqual((queued["elapsed"], queued["sinceFound"]), (30, 0))


class ParseTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(p.parse("Competitive: In Queue"), (p.QUEUEING, "competitive"))
        self.assertEqual(p.parse("Quick Play: In Game"), (p.IN_GAME, "quickPlay"))
        self.assertEqual(p.parse("In Menus"), (p.MENUS, None))
        self.assertEqual(p.parse("Something: In Queue"), (p.QUEUEING, None))
        self.assertEqual(p.parse("Loading"), (p.UNKNOWN, None))


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.t = p.Tracker()
        self.t.observe(_scan(menus=9))
        self.t.observe(_scan(menus=9, queue=3))

    def test_tracker_takes_newest_arrival(self):
        tracker = p.Tracker()
        self.assertEqual(tracker.observe(Counter({("status", "In Menus"): 2})), (p.MENUS, None))
        self.assertEqual(tracker.observe(Counter({("status", "In Menus"): 2, ("status", "Quick Play: In Queue"): 3})),
                         (p.QUEUEING, "quickPlay"))

    def test_the_scan_after_a_blip_does_not_flip_the_reading(self):
        # The bug: one unreadable scan used to make every value look like it had just
        # arrived, so the next scan picked whichever string had the most copies.
        self.t.observe(Counter())
        self.assertEqual(self.t.observe(_scan(menus=9, queue=3)), (p.QUEUEING, "competitive"))

    def test_a_blank_scan_reads_unknown(self):
        self.assertEqual(self.t.observe(Counter()), (p.UNKNOWN, None))

    def test_a_partial_scan_does_not_move_the_reading(self):
        self.assertEqual(self.t.observe(_scan(menus=4, queue=1), complete=False),
                         (p.QUEUEING, "competitive"))
        self.assertEqual(self.t.observe(_scan(menus=9, queue=3)), (p.QUEUEING, "competitive"))

    def test_a_real_change_after_a_blip_is_still_seen(self):
        self.t.observe(Counter())
        self.t.observe(_scan(menus=9, queue=3))
        self.assertEqual(self.t.observe(_scan(menus=12, queue=3)), (p.MENUS, None))

    def test_growth_during_a_partial_scan_still_counts(self):
        tracker = p.Tracker()
        tracker.observe(_scan(menus=9))
        self.assertEqual(tracker.observe(_scan(queue=5), complete=False), (p.QUEUEING, "competitive"))

    def test_rebaseline_keeps_the_last_reading(self):
        self.t.rebaseline()
        self.assertEqual(self.t.observe(_scan(menus=9, queue=3)), (p.QUEUEING, "competitive"))

    def test_rebaseline_still_sees_the_next_change(self):
        self.t.rebaseline()
        self.t.observe(_scan(menus=9, queue=3))
        self.assertEqual(self.t.observe(_scan(menus=10, queue=3)), (p.MENUS, None))

    def test_rebaseline_takes_an_unambiguous_reading(self):
        self.t.rebaseline()
        self.assertEqual(self.t.observe(_scan(menus=5)), (p.MENUS, None))

    def test_peaks_fall_back_after_a_sustained_shrink(self):
        for _ in range(p.PEAK_SHRINK_POLLS):
            self.assertEqual(self.t.observe(_scan(menus=2, queue=1)), (p.QUEUEING, "competitive"))
        self.assertEqual(self.t.observe(_scan(menus=3, queue=1)), (p.MENUS, None))

    def test_incomplete_scans_never_lower_the_peaks(self):
        for _ in range(p.PEAK_SHRINK_POLLS * 2):
            self.t.observe(_scan(menus=2, queue=1), complete=False)
        self.assertEqual(self.t.observe(_scan(menus=3, queue=1), complete=False),
                         (p.QUEUEING, "competitive"))
        self.assertEqual(self.t.observe(_scan(menus=9, queue=3)), (p.QUEUEING, "competitive"))

    def test_a_partial_scan_cannot_become_the_baseline(self):
        # A scan that missed processes reads low; taking it as the baseline used to make the
        # next whole scan look like every value had just arrived.
        self.t.rebaseline()
        self.assertEqual(self.t.observe(_scan(menus=4, queue=1), complete=False),
                         (p.QUEUEING, "competitive"))
        self.assertEqual(self.t.observe(_scan(menus=9, queue=3)), (p.QUEUEING, "competitive"))
        self.assertEqual(self.t.observe(_scan(menus=9, queue=3)), (p.QUEUEING, "competitive"))

    def test_a_partial_baseline_still_sees_the_next_change(self):
        self.t.rebaseline()
        self.t.observe(_scan(menus=4, queue=1), complete=False)
        self.t.observe(_scan(menus=9, queue=3))
        self.assertEqual(self.t.observe(_scan(menus=10, queue=3)), (p.MENUS, None))

    def test_a_never_whole_scan_still_tracks(self):
        self.t.rebaseline()
        self.t.observe(_scan(menus=4, queue=1), complete=False)
        self.assertEqual(self.t.observe(_scan(menus=5, queue=1), complete=False), (p.MENUS, None))

    def test_a_healthy_scan_resets_the_shrink_count(self):
        for _ in range(p.PEAK_SHRINK_POLLS * 2):
            self.t.observe(_scan(menus=2, queue=1))
            self.t.observe(_scan(menus=9, queue=3))
        self.assertEqual(self.t.observe(_scan(menus=3, queue=1)), (p.QUEUEING, "competitive"))


class PresencePidTests(unittest.TestCase):
    def setUp(self):
        self.roles, self.retry = p._battlenet_roles, p.ROLE_RETRY_SECONDS
        p.ROLE_RETRY_SECONDS = 0.0
        self.presence = p.Presence(log=lambda message: None)

    def tearDown(self):
        p._battlenet_roles, p.ROLE_RETRY_SECONDS = self.roles, self.retry

    @staticmethod
    def _unavailable():
        raise OSError("no CIM for you")

    def test_role_lookup_failure_falls_back_to_every_pid(self):
        p._battlenet_roles = self._unavailable
        self.presence._main = 7
        self.presence._resolve_pids(frozenset({100, 200}))
        self.assertEqual(self.presence._pids, [100, 200])
        self.assertEqual(self.presence._known_pids, frozenset())
        self.assertEqual(self.presence._main, 7)

    def test_role_lookup_recovers_on_the_next_read(self):
        p._battlenet_roles = self._unavailable
        self.presence._resolve_pids(frozenset({100, 200}))
        p._battlenet_roles = lambda: ([100], [200])
        self.presence._resolve_pids(frozenset({100, 200}))
        self.assertEqual(self.presence._pids, [100, 200])
        self.assertEqual(self.presence._known_pids, frozenset({100, 200}))

    def test_a_process_change_rebaselines_rather_than_resets(self):
        p._battlenet_roles = lambda: ([100], [200])
        self.presence._resolve_pids(frozenset({100, 200}))
        tracker = self.presence._tracker
        tracker.observe(_scan(menus=9))
        tracker.observe(_scan(menus=9, queue=3))
        p._battlenet_roles = lambda: ([100], [300])
        self.presence._resolve_pids(frozenset({100, 300}))
        self.assertIs(self.presence._tracker, tracker)
        self.assertEqual(tracker.observe(_scan(menus=9, queue=3)), (p.QUEUEING, "competitive"))

    def test_a_process_change_is_not_throttled(self):
        p.ROLE_RETRY_SECONDS = 60.0
        calls = []
        p._battlenet_roles = lambda: (calls.append(1), ([100], [200] if len(calls) == 1 else [300]))[1]
        self.presence._resolve_pids(frozenset({100, 200}))
        self.presence._resolve_pids(frozenset({100, 300}))
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.presence._pids, [100, 300])

    def test_a_failed_lookup_is_throttled(self):
        p.ROLE_RETRY_SECONDS = 60.0
        calls = []
        p._battlenet_roles = lambda: (calls.append(1), self._unavailable())[1]
        self.presence._resolve_pids(frozenset({100, 200}))
        self.presence._resolve_pids(frozenset({100, 200}))
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.presence._pids, [100, 200])

    def test_a_slow_failed_lookup_is_throttled_from_when_it_finished(self):
        p.ROLE_RETRY_SECONDS = 0.1
        calls = []

        def slow():
            calls.append(1)
            time.sleep(0.15)          # longer than the retry window, as a CIM timeout would be
            raise OSError("no CIM for you")

        p._battlenet_roles = slow
        self.presence._resolve_pids(frozenset({100, 200}))
        self.presence._resolve_pids(frozenset({100, 200}))
        self.assertEqual(len(calls), 1)

    def test_battlenet_closing_clears_the_pids(self):
        p._battlenet_roles = lambda: ([100], [200])
        self.presence._resolve_pids(frozenset({100, 200}))
        self.presence._resolve_pids(frozenset())
        self.assertEqual((self.presence._pids, self.presence._main), ([], None))
        self.assertEqual(self.presence._known_pids, frozenset())


if __name__ == "__main__":
    unittest.main()
