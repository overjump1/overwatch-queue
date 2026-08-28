"""The panel's buttons, without the panel: which phase each one produces and when."""
from __future__ import annotations

import contextlib
import datetime
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "server"))

from owqserver import protocol                                   # noqa: E402
from owqserver.catalog import Catalog                            # noqa: E402
from owqserver.controls import Controls                          # noqa: E402
from owqserver.pairing import Pairing                            # noqa: E402
from owqserver.pushtokens import PushTokens                      # noqa: E402
from owqserver.queueserver import SCENARIOS, QueueServer         # noqa: E402

CATALOG = Catalog()


@contextlib.contextmanager
def _clock_we_control():
    """Replaces the server's idea of `now` with one a test can move forward.

    Yields a function taking seconds-since-start and returning the new now.
    """
    base = protocol.now()
    offset = {"seconds": 0}
    real_now = protocol.now
    protocol.now = lambda: base + datetime.timedelta(seconds=offset["seconds"])
    try:
        def advance(seconds):
            offset["seconds"] = seconds
            return protocol.now()
        yield advance
    finally:
        protocol.now = real_now


def _deadline_of(phase):
    """The moment a phase is racing, as a datetime — or None if it isn't racing one."""
    data = phase.get("data") or {}
    stamp = data.get("deadline") or data.get("lockInAt")
    if stamp is None:
        return None
    return datetime.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=datetime.timezone.utc)


def make_controls():
    server = QueueServer(Pairing(token="t", port=0, path=os.devnull), CATALOG,
                        push_tokens=PushTokens(os.devnull))
    return Controls(server), server


class RoleTests(unittest.TestCase):
    def test_role_queue_modes_use_the_chosen_role(self):
        controls, _ = make_controls()
        for mode in ("quickPlay", "competitive", "stadium"):
            controls.mode = mode
            controls.role = "support"
            self.assertTrue(controls.queues_by_role)
            self.assertEqual(controls.effective_role, "support")

    def test_open_queue_modes_ignore_it(self):
        controls, _ = make_controls()
        for mode in ("arcade", "mysteryHeroes", "custom"):
            controls.mode = mode
            controls.role = "support"
            self.assertFalse(controls.queues_by_role)
            self.assertEqual(controls.effective_role, "open")


class PhaseTests(unittest.TestCase):
    def setUp(self):
        self.controls, self.server = make_controls()

    def test_start_queue_begins_a_fresh_session(self):
        self.controls.start_queue()
        first = self.server.session.session_id
        self.controls.start_queue()
        self.assertNotEqual(self.server.session.session_id, first)
        self.assertEqual(self.server.session.kind, "searching")

    def test_every_jump_builds_a_phase_the_server_accepts(self):
        self.controls.start_queue()
        for kind in ("matchFound", "mapVote", "heroSelect", "inGame"):
            self.assertTrue(self.controls.jump(kind), "server refused %s" % kind)
            self.assertEqual(self.server.session.kind, kind)

    def test_map_vote_offers_keys_the_app_has_art_for(self):
        phase = self.controls.phase_for("mapVote")
        keys = [option["mapKey"] for option in phase["data"]["options"]]
        self.assertEqual(len(keys), 3)
        self.assertEqual(len(set(keys)), 3)
        known = {entry["key"] for entry in CATALOG.maps}
        self.assertTrue(set(keys) <= known)

    def test_hero_select_takes_heroes_from_the_queued_role(self):
        self.controls.mode, self.controls.role = "competitive", "tank"
        phase = self.controls.phase_for("heroSelect")
        tanks = {hero["key"] for hero in CATALOG.heroes_for("tank", "competitive")}
        self.assertTrue(set(phase["data"]["takenHeroKeys"]) <= tanks)
        self.assertEqual(len(phase["data"]["takenHeroKeys"]), 3)

    def test_the_winning_map_carries_from_the_vote_into_the_game(self):
        self.controls.start_queue()
        self.controls.jump("matchFound")
        self.controls.jump("mapVote")
        options = self.server.session.phase["data"]["options"]
        winner = options[1]["mapKey"]
        options[1]["votes"] = 3
        self.server.session.patch(options=options)

        self.controls.jump("heroSelect")
        self.assertEqual(self.server.session.phase["data"]["mapKey"], winner)
        self.controls.jump("inGame")
        self.assertEqual(self.server.session.phase["data"]["mapKey"], winner)

    def test_an_unvoted_map_pool_still_settles_on_one_of_the_options(self):
        self.controls.start_queue()
        self.controls.jump("matchFound")
        self.controls.jump("mapVote")
        offered = {o["mapKey"] for o in self.server.session.phase["data"]["options"]}
        self.controls.jump("heroSelect")
        self.assertIn(self.server.session.phase["data"]["mapKey"], offered)

    def test_waited_uses_the_real_elapsed_time(self):
        self.controls.start_queue()
        self.server.shift_start(200)
        phase = self.controls.phase_for("matchFound")
        self.assertGreaterEqual(phase["data"]["waited"], 200)

    def test_waited_falls_back_to_the_estimate_from_a_standing_start(self):
        self.controls.estimate = 137
        self.assertEqual(self.controls.phase_for("matchFound")["data"]["waited"], 137)

    def test_an_unknown_phase_is_a_mistake_not_a_silent_no_op(self):
        with self.assertRaises(ValueError):
            self.controls.phase_for("victory")


class EstimateTests(unittest.TestCase):
    def test_changing_it_mid_queue_revises_the_live_estimate(self):
        controls, server = make_controls()
        controls.start_queue()
        controls.set_estimate(400)
        self.assertEqual(server.session.phase["data"]["estimatedWait"], 400)

    def test_changing_it_while_idle_touches_nothing(self):
        controls, server = make_controls()
        controls.set_estimate(400)
        self.assertEqual(server.session.kind, "idle")
        self.assertEqual(server.session.sequence, 0)


class ScenarioTests(unittest.TestCase):
    def test_every_scenario_is_a_run_of_legal_transitions(self):
        _, server = make_controls()
        for scenario in SCENARIOS:
            session = protocol.QueueSession()
            for build, hold in scenario.steps(CATALOG):
                phase = build()
                self.assertTrue(session.advance(phase),
                                "%s: %s → %s is illegal" % (scenario.name, session.kind,
                                                            phase["type"]))
                self.assertGreater(hold, 0)

    def test_scenario_deadlines_start_when_the_step_does(self):
        """A scenario is laid out in one go but plays out over minutes, so a phase built
        up front carries a deadline that has been running down ever since — the app used
        to reach hero select after that phase's deadline had already expired, and a
        countdown past its own deadline is what crashed it. Each phase has to be built
        when its own step is applied.

        Run against a clock we move ourselves, so this is the real timeline (minutes of
        holds) rather than what a test is willing to sit through.
        """
        for scenario in SCENARIOS:
            steps = scenario.steps(CATALOG)
            with _clock_we_control() as advance:
                elapsed = 0
                for index, (build, hold) in enumerate(steps):
                    started = advance(elapsed)
                    deadline = _deadline_of(build())
                    if deadline is not None:
                        self.assertGreater(
                            deadline, started,
                            "%s step %d starts a phase whose deadline has already passed"
                            % (scenario.name, index))
                    elapsed += hold

    def test_scenarios_are_described_for_the_button(self):
        for scenario in SCENARIOS:
            self.assertTrue(scenario.name and scenario.detail)

    def test_a_jump_takes_over_from_a_running_scenario(self):
        controls, server = make_controls()
        server.play(SCENARIOS[0])
        controls.jump("cancelled")
        self.assertFalse(server.scenario_running)
        self.assertEqual(server.session.kind, "cancelled")


if __name__ == "__main__":
    unittest.main()
