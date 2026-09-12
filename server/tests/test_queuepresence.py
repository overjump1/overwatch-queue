"""Battle.net's rich presence, turned into this project's vocabulary — no Battle.net,
no screen, no clock. Pure string-in, tuple-out arithmetic, the same reason
`test_queuevision.py`'s own state-machine tests run without a pixel in sight.
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "server"))

from owqserver import queuepresence as qp                              # noqa: E402


class ParseConfirmedStringsTests(unittest.TestCase):
    """Every string actually seen on a real account, over a real CDP session — see
    `bnetpresence.CDPSource`'s own docstring for how it was confirmed live."""

    def test_in_menus(self):
        self.assertEqual(qp.parse("In Menus"), (qp.MENUS, None))

    def test_quick_play_in_queue(self):
        self.assertEqual(qp.parse("Quick Play: In Queue"), (qp.QUEUEING, "quickPlay"))

    def test_quick_play_in_game(self):
        self.assertEqual(qp.parse("Quick Play: In Game"), (qp.IN_GAME, "quickPlay"))

    def test_competitive_in_queue(self):
        self.assertEqual(qp.parse("Competitive: In Queue"), (qp.QUEUEING, "competitive"))

    def test_competitive_in_game(self):
        self.assertEqual(qp.parse("Competitive: In Game"), (qp.IN_GAME, "competitive"))

    def test_custom_game_in_game(self):
        self.assertEqual(qp.parse("Custom Game: In Game"), (qp.IN_GAME, "custom"))

    def test_practice_range(self):
        self.assertEqual(qp.parse("Practice Range"), (qp.PLAYING_OTHER, None))

    def test_tutorial(self):
        self.assertEqual(qp.parse("Tutorial"), (qp.PLAYING_OTHER, None))


class DegradationTests(unittest.TestCase):
    """The two asymmetric rules — see the module docstring for why they're not the same
    rule pointed in different directions."""

    def test_an_unmeasured_mode_still_names_a_real_queue(self):
        state, mode = qp.parse("Stadium: In Queue")
        self.assertEqual(state, qp.QUEUEING)
        self.assertIsNone(mode)

    def test_an_unmeasured_mode_still_names_a_real_match(self):
        state, mode = qp.parse("Mystery Heroes: In Game")
        self.assertEqual(state, qp.IN_GAME)
        self.assertIsNone(mode)

    def test_a_familiar_mode_does_not_rescue_an_unknown_activity(self):
        # "Quick Play" is in the table; "Looking for Group" is not a known activity at
        # all, and that alone is what must decide this, not the familiar prefix.
        self.assertEqual(qp.parse("Quick Play: Looking for Group"), (qp.UNKNOWN, None))

    def test_an_activity_with_no_mode_in_front_is_still_known(self):
        # Never actually seen with no mode, but the shape must still degrade safely.
        self.assertEqual(qp.parse("In Queue"), (qp.QUEUEING, None))


class MalformedInputTests(unittest.TestCase):

    def test_empty_string(self):
        self.assertEqual(qp.parse(""), (qp.UNKNOWN, None))

    def test_none(self):
        self.assertEqual(qp.parse(None), (qp.UNKNOWN, None))

    def test_availability_word_alone(self):
        self.assertEqual(qp.parse("Away"), (qp.UNKNOWN, None))

    def test_wrong_case_is_not_recognised(self):
        # The vocabulary is a fixed set of exact strings, not something to fuzzy-match —
        # a case difference is exactly the kind of thing worth surfacing as UNKNOWN
        # rather than silently normalising away a real change in Battle.net's wording.
        self.assertEqual(qp.parse("in queue"), (qp.UNKNOWN, None))

    def test_surrounding_whitespace_is_stripped(self):
        self.assertEqual(qp.parse("  In Menus  "), (qp.MENUS, None))

    def test_garbage_never_raises(self):
        import random
        random.seed(0)
        alphabet = "abcdefghijklmnopqrstuvwxyz :._-#" + "\x00\x01﻿"
        for _ in range(200):
            text = "".join(random.choice(alphabet) for _ in range(random.randint(0, 40)))
            state, mode = qp.parse(text)
            self.assertIn(state, (qp.UNKNOWN, qp.QUEUEING, qp.IN_GAME, qp.MENUS,
                                  qp.PLAYING_OTHER))


class InjectableTableTests(unittest.TestCase):
    """A test's own table can know a word the shipped one doesn't, without the shipped
    table having to pretend it recognises something never actually observed."""

    def test_a_custom_mode_word(self):
        state, mode = qp.parse("Stadium: In Queue", mode_words={"Stadium": "stadium"})
        self.assertEqual((state, mode), (qp.QUEUEING, "stadium"))

    def test_a_custom_standalone_activity(self):
        state, mode = qp.parse("Arcade Lobby", standalone={"Arcade Lobby": qp.MENUS})
        self.assertEqual((state, mode), (qp.MENUS, None))


class RecordParsingTests(unittest.TestCase):
    """`parse_record` — the shape `bnetpresence.CDPSource` actually reads: a structured
    `subscribeSelfPresence` record, not a bare string."""

    def test_a_real_record(self):
        record = {"battle_tag": "overjump#2179", "id": "482667935",
                 "program_id": "Pro", "program_name": "Overwatch",
                 "rich_presence": "Quick Play: In Queue"}
        reading = qp.parse_record(record, when=1.0)
        self.assertEqual(reading.state, qp.QUEUEING)
        self.assertEqual(reading.mode, "quickPlay")
        self.assertEqual(reading.battletag, "overjump#2179")
        self.assertEqual(reading.at, 1.0)

    def test_another_program_is_elsewhere_not_unknown(self):
        record = {"battle_tag": "overjump#2179", "program_id": "WoW",
                 "program_name": "World of Warcraft", "rich_presence": "In Menus"}
        reading = qp.parse_record(record, when=1.0)
        self.assertEqual(reading.state, qp.ELSEWHERE)

    def test_no_program_named_at_all_is_unknown_not_elsewhere(self):
        # Finding nothing is never treated as "not playing" — only a positively named
        # *other* program earns ELSEWHERE.
        record = {"battle_tag": "overjump#2179", "rich_presence": ""}
        reading = qp.parse_record(record, when=1.0)
        self.assertEqual(reading.state, qp.UNKNOWN)

    def test_no_record_at_all(self):
        self.assertIsNone(qp.parse_record(None, when=1.0))
        self.assertIsNone(qp.parse_record({}, when=1.0))

    def test_identity_filters_a_different_account(self):
        identity = qp.Identity("overjump#2179", "482667935")
        friends_record = {"battle_tag": "someoneelse#1234", "id": "999",
                          "program_id": "Pro", "program_name": "Overwatch",
                          "rich_presence": "Competitive: In Queue"}
        self.assertIsNone(qp.parse_record(friends_record, when=1.0, identity=identity))

    def test_identity_accepts_our_own_account(self):
        identity = qp.Identity("overjump#2179", "482667935")
        our_record = {"battle_tag": "overjump#2179", "id": "482667935",
                     "program_id": "Pro", "program_name": "Overwatch",
                     "rich_presence": "In Menus"}
        reading = qp.parse_record(our_record, when=1.0, identity=identity)
        self.assertIsNotNone(reading)
        self.assertEqual(reading.state, qp.MENUS)


class IdentityTests(unittest.TestCase):

    def test_matches_by_account_id_first(self):
        identity = qp.Identity("overjump#2179", 482667935)
        self.assertTrue(identity.matches(account_id="482667935"))
        self.assertTrue(identity.matches(account_id=482667935))

    def test_falls_back_to_battletag(self):
        identity = qp.Identity("overjump#2179", None)
        self.assertTrue(identity.matches(battletag="overjump#2179"))
        self.assertFalse(identity.matches(battletag="someoneelse#1234"))

    def test_matches_nothing_with_no_evidence(self):
        identity = qp.Identity("overjump#2179", "482667935")
        self.assertFalse(identity.matches())


if __name__ == "__main__":
    unittest.main()
