import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import presence as p  # noqa: E402
from detector import FOUND, IDLE, QUEUEING, Detector  # noqa: E402


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
