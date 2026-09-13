"""The transports and the relaunch/cooldown guards around them — no real Battle.net,
just the module's own functions with their I/O edges swapped out by hand, the same way
the rest of this project prefers a plain substitute over a mocking framework.
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "server"))

from owqserver import bnetpresence, queuepresence                      # noqa: E402

WINDOWS_ONLY = "this whole module is a Windows-only extra, see bnetpresence's own docstring"


@unittest.skipUnless(bnetpresence.PRESENCE_AVAILABLE, WINDOWS_ONLY)
class LaunchBattlenetTests(unittest.TestCase):
    """`launch_battlenet` — the "restart the client" recovery path. Every real edge
    (is it running, where's the exe, actually starting it) is swapped for a fake so
    nothing here ever touches a real process."""

    def setUp(self):
        self._running = bnetpresence.battlenet_running
        self._exists = os.path.exists
        self._popen = __import__("subprocess").Popen
        bnetpresence._last_launch_attempt[0] = -1e9      # clear of any cooldown
        self.popened = []

    def tearDown(self):
        bnetpresence.battlenet_running = self._running
        os.path.exists = self._exists
        __import__("subprocess").Popen = self._popen

    def _fake_popen(self, args, **kwargs):
        self.popened.append(args)
        class _P:
            pass
        return _P()

    def test_does_nothing_if_already_running(self):
        bnetpresence.battlenet_running = lambda: True
        self.assertFalse(bnetpresence.launch_battlenet())
        self.assertEqual(self.popened, [])

    def test_launches_when_not_running_and_the_exe_is_found(self):
        bnetpresence.battlenet_running = lambda: False
        os.path.exists = lambda path: path == bnetpresence._BATTLENET_EXE_CANDIDATES[0]
        __import__("subprocess").Popen = self._fake_popen
        self.assertTrue(bnetpresence.launch_battlenet(port=1234))
        self.assertEqual(len(self.popened), 1)
        self.assertIn("--remote-debugging-port=1234", self.popened[0])

    def test_returns_false_with_no_exe_found(self):
        bnetpresence.battlenet_running = lambda: False
        os.path.exists = lambda path: False
        self.assertFalse(bnetpresence.launch_battlenet())
        self.assertEqual(self.popened, [])

    def test_a_second_attempt_inside_the_cooldown_does_nothing(self):
        bnetpresence.battlenet_running = lambda: False
        os.path.exists = lambda path: path == bnetpresence._BATTLENET_EXE_CANDIDATES[0]
        __import__("subprocess").Popen = self._fake_popen
        self.assertTrue(bnetpresence.launch_battlenet())
        self.assertFalse(bnetpresence.launch_battlenet())     # too soon
        self.assertEqual(len(self.popened), 1)

    def test_never_kills_or_touches_a_running_battlenet(self):
        """The one rule this whole feature exists to respect: a Battle.net that's
        running at all — mid-queue or not — is never the thing this function acts on."""
        bnetpresence.battlenet_running = lambda: True
        os.path.exists = lambda path: True
        __import__("subprocess").Popen = self._fake_popen
        bnetpresence.launch_battlenet()
        self.assertEqual(self.popened, [])


@unittest.skipUnless(bnetpresence.PRESENCE_AVAILABLE, WINDOWS_ONLY)
class ForceRelaunchBattlenetTests(unittest.TestCase):
    """`force_relaunch_battlenet` — the one path that *will* close a running Battle.net,
    because a person asked for exactly that. Every real edge swapped for a fake, same
    as `LaunchBattlenetTests` above."""

    def setUp(self):
        self._running = bnetpresence.battlenet_running
        self._exists = os.path.exists
        self._popen = __import__("subprocess").Popen
        self._run = __import__("subprocess").run
        self._sleep = __import__("time").sleep
        self._monotonic = __import__("time").monotonic
        bnetpresence._last_launch_attempt[0] = -1e9
        self.popened = []
        self.ran = []

    def tearDown(self):
        bnetpresence.battlenet_running = self._running
        os.path.exists = self._exists
        __import__("subprocess").Popen = self._popen
        __import__("subprocess").run = self._run
        __import__("time").sleep = self._sleep
        __import__("time").monotonic = self._monotonic

    def _fake_popen(self, args, **kwargs):
        self.popened.append(args)
        class _P:
            pass
        return _P()

    def _fake_run(self, args, **kwargs):
        self.ran.append(args[-1])
        class _C:
            returncode = 0
        return _C()

    def test_launches_directly_when_not_running_at_all(self):
        bnetpresence.battlenet_running = lambda: False
        os.path.exists = lambda path: path == bnetpresence._BATTLENET_EXE_CANDIDATES[0]
        __import__("subprocess").Popen = self._fake_popen
        __import__("subprocess").run = self._fake_run
        self.assertTrue(bnetpresence.force_relaunch_battlenet(port=1234))
        self.assertEqual(self.ran, [])                      # nothing running to close
        self.assertIn("--remote-debugging-port=1234", self.popened[0])

    def test_closes_a_running_one_before_relaunching(self):
        seen = {"n": 0}

        def running():
            seen["n"] += 1
            return seen["n"] <= 1        # running on the first check, gone after

        bnetpresence.battlenet_running = running
        os.path.exists = lambda path: path == bnetpresence._BATTLENET_EXE_CANDIDATES[0]
        __import__("subprocess").Popen = self._fake_popen
        __import__("subprocess").run = self._fake_run
        __import__("time").sleep = lambda seconds: None
        self.assertTrue(bnetpresence.force_relaunch_battlenet())
        self.assertEqual(len(self.ran), 1)
        self.assertIn("Stop-Process", self.ran[0])
        self.assertEqual(len(self.popened), 1)

    def test_gives_up_if_it_never_closes(self):
        bnetpresence.battlenet_running = lambda: True
        __import__("subprocess").run = self._fake_run
        __import__("subprocess").Popen = self._fake_popen
        __import__("time").sleep = lambda seconds: None
        clock = [0.0]

        def fake_monotonic():
            clock[0] += 5
            return clock[0]

        __import__("time").monotonic = fake_monotonic
        self.assertFalse(bnetpresence.force_relaunch_battlenet())
        self.assertEqual(self.popened, [])                  # never got to launching

    def test_returns_false_with_no_exe_found(self):
        bnetpresence.battlenet_running = lambda: False
        os.path.exists = lambda path: False
        __import__("subprocess").run = self._fake_run
        self.assertFalse(bnetpresence.force_relaunch_battlenet())
        self.assertEqual(self.popened, [])


def _pb_varint(value):
    out = bytearray()
    while True:
        byte, value = value & 0x7F, value >> 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _pb_string(number, text):
    data = text.encode("utf-8")
    return _pb_varint((number << 3) | 2) + _pb_varint(len(data)) + data


def _pb_int(number, value):
    return _pb_varint(number << 3) + _pb_varint(value)


def _presence_record(rich_presence, stamp_ms, program_id="Pro", program_name="Overwatch",
                     account_id="482667935", battletag="overjump#2179"):
    """A record laid out the way it was found in a live Battle.net's memory: id, tag, a
    status varint, a timestamp, program id and name, real name, rich presence, then a
    second timestamp — whose tag byte (`x`) sits directly after the rich-presence text,
    which is what used to glue itself onto it. `rich_presence=None` leaves field 13 out,
    the way a live update naming only a new program did."""
    rich = _pb_string(13, rich_presence) if rich_presence is not None else b""
    return (_pb_string(1, account_id) + _pb_string(2, battletag) + _pb_int(3, 2) +
            _pb_int(4, stamp_ms) + _pb_int(5, 368) + _pb_string(6, program_id) +
            _pb_string(7, program_name) + _pb_int(10, 0) + _pb_string(11, "Tomer ady") +
            rich + _pb_int(14, stamp_ms + 410) + _pb_string(20, "US"))


def _record(chunks, identity):
    return bnetpresence.find_presence_record(chunks, identity)[0]


_ONE_BYTE_HEADER = b'-%\x18"\x03\x00\x00\x00'       # map + hash field, as found live
_TWO_BYTE_HEADER = b'10\x18"\x03\x00\x00\x00'


def _v8_string(text, header=_ONE_BYTE_HEADER):
    if header == _TWO_BYTE_HEADER:
        data, length = text.encode("utf-16-le"), len(text)
    else:
        data, length = text.encode("latin-1"), len(text)
    body = header + length.to_bytes(4, "little") + data
    return body + b"\x00" * (-len(body) % 4)


def _presence_object(program_id="App", program_name="In Battle.net", rich="In App",
                     account_id="482667935", battletag="overjump#2179"):
    """The presence object as a fresh Battle.net lays it out: one-byte strings sharing a
    map and hash word, other small heap objects between some of them, and the real name
    as a two-byte string with a different map."""
    junk = b"\x99\xa1!(\xb1!t^\xb1!t^Q$t^\xbe\x8b\x00\x00}\x91\x86+"
    parts = [_v8_string(account_id), junk, _v8_string(battletag), junk,
             _v8_string(program_id), _v8_string(program_name),
             _v8_string("Tomer ady", _TWO_BYTE_HEADER)]
    if rich:
        parts.append(_v8_string(rich))
    parts += [_v8_string("US"), _v8_string("US")]
    return b"\x00" * 16 + b"".join(parts)


class PresenceObjectTests(unittest.TestCase):
    """`find_presence_record`'s object form — what Battle.net holds before any update
    record exists. Pure bytes, no process."""

    IDENTITY = queuepresence.Identity("overjump#2179", "482667935")

    def test_reads_the_object_before_any_update_exists(self):
        chunk = _presence_object(program_id="Pro", program_name="Overwatch",
                                 rich="Quick Play: In Queue")
        record, kind = bnetpresence.find_presence_record([chunk], self.IDENTITY)
        self.assertEqual(kind, bnetpresence.OBJECT_RECORD)
        self.assertEqual(record, {"id": "482667935", "battle_tag": "overjump#2179",
                                  "program_id": "Pro", "program_name": "Overwatch",
                                  "rich_presence": "Quick Play: In Queue"})
        reading = queuepresence.parse_record(record, 0.0, identity=self.IDENTITY)
        self.assertEqual((reading.state, reading.mode), (queuepresence.QUEUEING, "quickPlay"))

    def test_battlenet_itself_reads_as_elsewhere(self):
        record, _ = bnetpresence.find_presence_record([_presence_object()], self.IDENTITY)
        reading = queuepresence.parse_record(record, 0.0, identity=self.IDENTITY)
        self.assertEqual(reading.state, queuepresence.ELSEWHERE)

    def test_the_object_wins_over_an_update_that_only_names_the_program(self):
        """Measured live: launching Overwatch produced updates naming the program with no
        status text, while the object (and Battle.net's own window) said "In Menus"."""
        update = _presence_record(None, 1789308396752)
        current = _presence_object(program_id="Pro", program_name="Overwatch", rich="In Menus")
        record, kind = bnetpresence.find_presence_record([update, current], self.IDENTITY)
        self.assertEqual(kind, bnetpresence.OBJECT_RECORD)
        self.assertEqual(record["rich_presence"], "In Menus")

    def test_an_object_left_over_from_another_program_is_set_aside(self):
        """Update timestamps move when the program does, so they say which object is old."""
        stale_object = _presence_object(program_id="App", program_name="In Battle.net")
        update = _presence_record("In Menus", 1789305461335)
        record, kind = bnetpresence.find_presence_record([stale_object, update],
                                                         self.IDENTITY)
        self.assertEqual(kind, bnetpresence.UPDATE_RECORD)
        self.assertEqual(record["rich_presence"], "In Menus")

    def test_the_newest_update_decides_which_program_is_current(self):
        older = _presence_record("In App", 1789302245274, program_id="App",
                                 program_name="In Battle.net")
        newer = _presence_record(None, 1789305461335)
        app = _presence_object()
        overwatch = _presence_object(program_id="Pro", program_name="Overwatch", rich="In Menus")
        record, _ = bnetpresence.find_presence_record([app, app, older, newer, overwatch],
                                                      self.IDENTITY)
        self.assertEqual((record["program_id"], record["rich_presence"]), ("Pro", "In Menus"))

    def test_an_object_agreeing_with_the_updates_status_is_preferred(self):
        update = _presence_record("Quick Play: In Queue", 1789305461335)
        stale = _presence_object(program_id="Pro", program_name="Overwatch", rich="In Menus")
        current = _presence_object(program_id="Pro", program_name="Overwatch",
                                   rich="Quick Play: In Queue")
        record, _ = bnetpresence.find_presence_record([stale, stale, current, update],
                                                      self.IDENTITY)
        self.assertEqual(record["rich_presence"], "Quick Play: In Queue")

    def test_a_region_object_is_not_the_presence_object(self):
        """Live memory holds one that also starts with our id and battletag, then EU, EU."""
        region = b"\x00" * 16 + _v8_string("482667935") + _v8_string("overjump#2179") + \
            _v8_string("EU") + _v8_string("EU")
        self.assertEqual(bnetpresence.find_presence_record([region], self.IDENTITY),
                         (None, None))

    def test_no_rich_presence_is_never_filled_in_with_a_region_code(self):
        chunk = _presence_object(program_id="Pro", program_name="Overwatch", rich="")
        record, _ = bnetpresence.find_presence_record([chunk], self.IDENTITY)
        self.assertEqual(record["rich_presence"], "")

    def test_another_object_starting_with_our_id_is_not_the_presence_object(self):
        """Live memory holds an account-settings object that also starts with the id."""
        other = b"\x00" * 16 + _v8_string("482667935") + _v8_string("enUS") + \
            _v8_string("US") + _v8_string("Americas")
        self.assertEqual(bnetpresence.find_presence_record([other], self.IDENTITY),
                         (None, None))

    def test_a_friends_object_is_never_ours(self):
        friend = _presence_object(account_id="111222333", battletag="friend#1234")
        self.assertEqual(bnetpresence.find_presence_record([friend], self.IDENTITY),
                         (None, None))

    def test_the_most_common_object_wins(self):
        menus = _presence_object(program_id="Pro", program_name="Overwatch", rich="In Menus")
        app = _presence_object()
        record, _ = bnetpresence.find_presence_record([menus, menus, app], self.IDENTITY)
        self.assertEqual(record["program_id"], "Pro")


class NewestPresenceRecordTests(unittest.TestCase):
    """`find_presence_record` — decoding update records out of raw memory bytes.
    Pure, so no process and no platform needed."""

    IDENTITY = queuepresence.Identity("overjump#2179", "482667935")

    def test_decodes_the_fields_parse_record_reads(self):
        chunk = b"\x00noise\x00" + _presence_record("Quick Play: In Queue", 1789302245274)
        record = _record([chunk], self.IDENTITY)
        self.assertEqual(record, {"id": "482667935", "battle_tag": "overjump#2179",
                                  "program_id": "Pro", "program_name": "Overwatch",
                                  "rich_presence": "Quick Play: In Queue"})

    def test_reads_the_same_as_the_debug_port_through_parse_record(self):
        chunk = _presence_record("Competitive: In Queue", 1789302245274)
        record = _record([chunk], self.IDENTITY)
        reading = queuepresence.parse_record(record, 0.0, identity=self.IDENTITY)
        self.assertEqual((reading.state, reading.mode), (queuepresence.QUEUEING, "competitive"))

    def test_the_newest_copy_wins_wherever_it_sits(self):
        """Memory keeps stale copies of old records; address order means nothing."""
        newer = _presence_record("Quick Play: In Game", 1789302300000)
        older = _presence_record("Quick Play: In Queue", 1789302200000)
        record = _record([newer + b"\x00" * 8, older],
                                                     self.IDENTITY)
        self.assertEqual(record["rich_presence"], "Quick Play: In Game")

    def test_a_friends_record_is_never_ours(self):
        friend = _presence_record("Quick Play: In Queue", 1789302245274,
                                  account_id="111", battletag="friend#1234")
        self.assertIsNone(_record([friend], self.IDENTITY))

    def test_decoding_stops_where_the_next_record_starts(self):
        first = _presence_record("In App", 1789302245274, program_id="App",
                                 program_name="In Battle.net")
        second = _presence_record("Quick Play: In Queue", 1789302245274)
        record = _record([first + second], self.IDENTITY)
        # Equal timestamps: whichever decodes, it must be one whole record, never a mix.
        self.assertIn((record["program_id"], record["rich_presence"]),
                      [("App", "In App"), ("Pro", "Quick Play: In Queue")])

    def test_a_battletag_only_identity_still_finds_the_record(self):
        chunk = _presence_record("Quick Play: In Queue", 1789302245274)
        identity = queuepresence.Identity("overjump#2179")
        record = _record([chunk], identity)
        self.assertEqual(record["rich_presence"], "Quick Play: In Queue")

    def test_nothing_to_anchor_on_is_none(self):
        chunk = _presence_record("Quick Play: In Queue", 1789302245274)
        self.assertIsNone(_record([chunk], None))

    def test_a_truncated_record_does_not_raise(self):
        chunk = _presence_record("Quick Play: In Queue", 1789302245274)[:30]
        record = _record([chunk], self.IDENTITY)
        self.assertNotIn("rich_presence", record or {})


@unittest.skipUnless(bnetpresence.PRESENCE_AVAILABLE, WINDOWS_ONLY)
class OpenSourceTests(unittest.TestCase):

    def setUp(self):
        self._find_identity = bnetpresence.find_identity
        self._list_targets = bnetpresence._list_targets

    def tearDown(self):
        bnetpresence.find_identity = self._find_identity
        bnetpresence._list_targets = self._list_targets

    def test_off_is_always_a_null_source(self):
        source = bnetpresence.open_source(prefer="off")
        self.assertIsInstance(source, bnetpresence.NullSource)

    def test_memory_with_no_identity_is_a_null_source(self):
        bnetpresence.find_identity = lambda log=None: None
        source = bnetpresence.open_source(prefer="memory", identity=None)
        self.assertIsInstance(source, bnetpresence.NullSource)

    def test_memory_with_an_identity_is_a_memory_source(self):
        identity = queuepresence.Identity("me#1", "1")
        source = bnetpresence.open_source(prefer="memory", identity=identity)
        self.assertIsInstance(source, bnetpresence.MemorySource)

    def test_cdp_forced_with_no_port_reachable_is_a_null_source(self):
        def _raise(port):
            raise ConnectionError("no one's listening")
        bnetpresence._list_targets = _raise
        source = bnetpresence.open_source(prefer="cdp", port=1)
        self.assertIsInstance(source, bnetpresence.NullSource)

    def test_a_forced_cdp_failure_relaunches_only_when_asked(self):
        calls = []
        bnetpresence._list_targets = lambda port: (_ for _ in ()).throw(ConnectionError())
        original = bnetpresence.launch_battlenet
        bnetpresence.launch_battlenet = lambda port=9222, log=None: calls.append(port)
        try:
            bnetpresence.open_source(prefer="cdp", port=1, relaunch=False)
            self.assertEqual(calls, [])
            bnetpresence.open_source(prefer="cdp", port=1, relaunch=True)
            self.assertEqual(calls, [1])
        finally:
            bnetpresence.launch_battlenet = original


if __name__ == "__main__":
    unittest.main()
