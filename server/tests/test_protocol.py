"""The shapes in docs/PROTOCOL.md, and the transition table the app enforces."""
from __future__ import annotations

import datetime
import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "server"))

from owqserver import protocol                                   # noqa: E402
from owqserver.pairing import Pairing                            # noqa: E402

ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class TimeTests(unittest.TestCase):
    def test_dates_are_whole_second_utc_with_a_z(self):
        # The app decodes with .iso8601, which rejects fractional seconds outright.
        self.assertRegex(protocol.iso(protocol.now()), ISO)
        odd = datetime.datetime(2023, 11, 14, 22, 13, 20, 123456,
                                tzinfo=datetime.timezone.utc)
        self.assertEqual(protocol.iso(odd), "2023-11-14T22:13:20Z")

    def test_local_times_are_converted_not_relabelled(self):
        aware = datetime.datetime(2023, 11, 14, 22, 13, 20,
                                  tzinfo=datetime.timezone(datetime.timedelta(hours=2)))
        self.assertEqual(protocol.iso(aware), "2023-11-14T20:13:20Z")


class EnvelopeTests(unittest.TestCase):
    def test_every_message_is_versioned(self):
        for raw in (protocol.heartbeat(), protocol.error("no_game", "Overwatch isn't running")):
            self.assertEqual(json.loads(raw)["v"], 1)

    def test_snapshot_carries_what_the_client_needs(self):
        session = protocol.QueueSession()
        data = json.loads(session.snapshot())["body"]["data"]
        self.assertEqual(set(data), {"sessionID", "sequence", "serverTime", "phase"})
        self.assertRegex(data["serverTime"], ISO)

    def test_error_reads_as_a_sentence(self):
        body = json.loads(protocol.error("no_game", "Overwatch isn't running"))["body"]
        self.assertEqual(body["type"], "error")
        self.assertEqual(body["data"], {"code": "no_game", "message": "Overwatch isn't running"})


class PairingURLTests(unittest.TestCase):
    """The exact string the QR carries. `Tests/PairingTests.swift` parses this shape, and
    `iOS/Info.plist` registers the scheme so the system camera can open it — three places
    that have to agree, in two languages, with a camera in between."""

    def url(self):
        return Pairing(token="3f2504e0-4f89-41d3-9a0c-0305e82c3301",
                       port=8787, path=os.devnull).url("192.168.1.14")

    def test_shape(self):
        self.assertEqual(
            self.url(),
            "owq://pair?host=192.168.1.14&port=8787"
            "&token=3f2504e0-4f89-41d3-9a0c-0305e82c3301")

    def test_scheme_is_the_one_the_app_registers(self):
        # Changing this means changing CFBundleURLTypes in iOS/Info.plist, or the camera
        # decodes the code and then has nothing to open.
        self.assertTrue(self.url().startswith("owq://pair?"))

    def test_every_field_the_app_needs_is_present(self):
        query = self.url().split("?", 1)[1]
        fields = dict(pair.split("=", 1) for pair in query.split("&"))
        self.assertEqual(set(fields), {"host", "port", "token"})
        self.assertTrue(all(fields.values()))


class PhaseTests(unittest.TestCase):
    def test_idle_carries_nothing(self):
        self.assertEqual(protocol.idle(), {"type": "idle"})

    def test_searching_matches_the_documented_keys(self):
        phase = protocol.searching("competitive", "tank", protocol.now(), 240)
        self.assertEqual(set(phase["data"]),
                         {"mode", "role", "startedAt", "estimatedWait", "groupSize"})

    def test_an_unknown_estimate_is_left_out_rather_than_guessed(self):
        # The app shows an indeterminate shimmer for a missing estimate, and a fake
        # progress bar for a made-up one.
        self.assertNotIn("estimatedWait", protocol.searching(
            "quickPlay", "flex", protocol.now())["data"])

    def test_deadlines_are_moments_not_countdowns(self):
        for phase in (protocol.match_found("quickPlay", "damage", 12, 15),
                      protocol.map_vote(["ilios"], 25),
                      protocol.hero_select("competitive", "tank", None, 40)):
            deadline = phase["data"].get("lockInAt") or phase["data"]["deadline"]
            self.assertRegex(deadline, ISO)

    def test_optional_fields_are_omitted_when_empty(self):
        self.assertNotIn("mapKey", protocol.in_game("quickPlay")["data"])
        self.assertNotIn("myVote", protocol.map_vote(["ilios"], 25)["data"])
        self.assertNotIn("myHeroKey", protocol.hero_select("quickPlay", "flex")["data"])

    def test_vote_options_keep_their_order_and_tallies(self):
        phase = protocol.map_vote(["ilios", "havana"], 20, {"ilios": 2, "havana": 1})
        self.assertEqual(phase["data"]["options"],
                         [{"mapKey": "ilios", "votes": 2}, {"mapKey": "havana", "votes": 1}])


class TransitionTests(unittest.TestCase):
    """Mirrors the table in QueuePhase.canTransition(to:) — if the app grows a rule,
    this fails until the server grows it too."""

    LEGAL = [("idle", "searching"), ("searching", "matchFound"),
             ("matchFound", "mapVote"), ("matchFound", "heroSelect"),
             ("matchFound", "inGame"), ("matchFound", "searching"),
             ("mapVote", "heroSelect"), ("mapVote", "inGame"),
             ("heroSelect", "inGame"), ("cancelled", "searching")]

    ILLEGAL = [("idle", "matchFound"), ("idle", "heroSelect"), ("searching", "mapVote"),
               ("searching", "heroSelect"), ("searching", "inGame"),
               ("heroSelect", "mapVote"), ("inGame", "searching"),
               ("inGame", "matchFound"), ("mapVote", "matchFound")]

    def test_legal_transitions(self):
        for current, target in self.LEGAL:
            self.assertTrue(protocol.can_transition(current, target), "%s → %s" % (current, target))

    def test_illegal_transitions(self):
        for current, target in self.ILLEGAL:
            self.assertFalse(protocol.can_transition(current, target), "%s → %s" % (current, target))

    def test_idle_and_cancelled_are_always_reachable(self):
        for kind in ("idle", "searching", "matchFound", "mapVote", "heroSelect", "inGame"):
            self.assertTrue(protocol.can_transition(kind, "idle"))
            self.assertTrue(protocol.can_transition(kind, "cancelled"))

    def test_resending_the_same_phase_is_a_payload_refresh(self):
        for kind in ("searching", "mapVote", "heroSelect", "inGame"):
            self.assertTrue(protocol.can_transition(kind, kind))


class ContentStateTests(unittest.TestCase):
    """The Live Activity push shape — the one place on this wire that uses Apple's
    reference-date epoch (2001-01-01) instead of ISO-8601, because push content-state is
    decoded with a plain JSONDecoder rather than this protocol's `.iso8601` one."""

    FIXED = datetime.datetime(2023, 11, 14, 22, 13, 20, tzinfo=datetime.timezone.utc)

    def test_reference_date_epoch_is_2001_not_1970(self):
        epoch_2001 = datetime.datetime(2001, 1, 1, tzinfo=datetime.timezone.utc)
        self.assertEqual(protocol.reference_date_seconds(epoch_2001), 0.0)

    def test_reference_date_matches_the_known_fixture(self):
        # The same instant Tests/WireFormatTests.swift pins as
        # `Date(timeIntervalSince1970: 1_700_000_000)` — cross-checks the two
        # implementations agree on the same date without sharing any code.
        self.assertEqual(protocol.reference_date_seconds(self.FIXED), 721692800.0)

    def test_content_state_converts_every_date_field_in_the_phase(self):
        phase = protocol.searching("competitive", "tank", self.FIXED, 240)
        state = protocol.content_state(phase, sequence=7)
        self.assertEqual(state["sequence"], 7)
        self.assertEqual(state["phase"]["type"], "searching")
        self.assertEqual(state["phase"]["data"]["startedAt"], 721692800.0)
        self.assertIsInstance(state["phase"]["data"]["startedAt"], float)

    def test_content_state_leaves_non_date_fields_untouched(self):
        phase = protocol.match_found("competitive", "tank", waited=137, lock_in_seconds=15)
        state = protocol.content_state(phase, sequence=2)
        self.assertEqual(state["phase"]["data"]["mode"], "competitive")
        self.assertEqual(state["phase"]["data"]["waited"], 137)
        self.assertIsInstance(state["phase"]["data"]["lockInAt"], float)

    def test_content_state_handles_a_payloadless_phase(self):
        state = protocol.content_state(protocol.idle(), sequence=0)
        self.assertEqual(state, {"phase": {"type": "idle"}, "sequence": 0})

    def test_content_state_does_not_mutate_the_original_phase(self):
        phase = protocol.searching("competitive", "tank", self.FIXED, 240)
        original_started_at = phase["data"]["startedAt"]
        protocol.content_state(phase, sequence=1)
        self.assertEqual(phase["data"]["startedAt"], original_started_at)
        self.assertIsInstance(phase["data"]["startedAt"], str)


class SessionTests(unittest.TestCase):
    def test_sequence_climbs_only_on_accepted_changes(self):
        session = protocol.QueueSession()
        self.assertTrue(session.advance(protocol.searching("quickPlay", "flex", protocol.now())))
        self.assertEqual(session.sequence, 1)
        self.assertFalse(session.advance(protocol.in_game("quickPlay")))
        self.assertEqual(session.sequence, 1)

    def test_reset_starts_a_new_session(self):
        session = protocol.QueueSession()
        session.advance(protocol.searching("quickPlay", "flex", protocol.now()))
        was = session.session_id
        session.reset()
        self.assertNotEqual(session.session_id, was)
        self.assertEqual((session.sequence, session.kind), (0, "idle"))

    def test_shifting_the_start_back_ages_the_queue(self):
        session = protocol.QueueSession()
        session.advance(protocol.searching("competitive", "tank", protocol.now(), 240))
        session.shift_start(300)
        self.assertGreaterEqual(session.elapsed(), 300)

    def test_shifting_does_nothing_outside_a_search(self):
        self.assertFalse(protocol.QueueSession().shift_start(60))


if __name__ == "__main__":
    unittest.main()
