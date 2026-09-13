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


def _pb_bytes(number, data):
    return _pb_varint((number << 3) | 2) + _pb_varint(len(data)) + data


def _pb_int(number, value):
    return _pb_varint(number << 3) + _pb_varint(value)


ACCOUNT = 482667935
FRIEND = 421644415
_BN = 0x424E


def _operation(group, field, text, program=_BN):
    """One field operation laid out as found live: key (program, group, field, index), a
    value whose field 4 is the text (absent for a cleared field), then a microsecond
    timestamp."""
    key = _pb_int(1, program) + _pb_int(2, group) + _pb_int(3, field) + _pb_int(4, 0)
    value = _pb_bytes(4, text.encode("utf-8")) if text is not None else b""
    return _pb_bytes(1, key) + _pb_bytes(2, value) + _pb_int(4, 1789310403192274)


def _message(account, *operations):
    """The account id, then one block per operation, each block starting with the account
    id again - the first also carrying the 13-byte game-account submessage seen live."""
    blocks = []
    for i, operation in enumerate(operations):
        extra = (_pb_bytes(2, b"\x08\x92\xd2\x96\xb2\x03\x10\xef\xe4\xc1\x02\x18\x02")
                 if i == 0 else b"")
        blocks.append(_pb_bytes(2, _pb_int(1, account) + extra + _pb_bytes(3, operation)))
    return _pb_int(1, account) + b"".join(blocks)


def _status(text, group=2, field=22):
    return _operation(group, field, text)


def _program(text):
    return _operation(2, 21, text)


IDENTITY = queuepresence.Identity("overjump#2179", str(ACCOUNT))


def _record(status, program="Pro", account=str(ACCOUNT), battletag="overjump#2179"):
    """A record as found in a renderer with the Battle.net window open: account id and
    battletag as text, a status varint and timestamp, program id and name, real name, status,
    then a second timestamp - `status=None` leaves field 13 out, the way a live update
    naming only a new program did."""
    body = (_pb_bytes(1, account.encode()) + _pb_bytes(2, battletag.encode()) + _pb_int(3, 2) +
            _pb_int(4, 1789302245274) + _pb_bytes(6, program.encode()) +
            _pb_bytes(7, b"Overwatch") + _pb_int(10, 0) + _pb_bytes(11, "Tomer ady".encode()))
    if status is not None:
        body += _pb_bytes(13, status.encode())
    return body + _pb_int(14, 1789302245684) + _pb_bytes(20, b"US")


class CollectFieldOpsTests(unittest.TestCase):
    """`collect_presence` - decoding field operations and records out of raw memory bytes.
    Pure, so no process and no platform needed."""

    def collect(self, *chunks):
        return dict(bnetpresence.collect_presence(list(chunks), IDENTITY))

    def test_reads_status_and_program_operations(self):
        chunk = b"\x00" * 7 + _message(ACCOUNT, _program("Pro"), _status("Quick Play: In Queue"))
        self.assertEqual(self.collect(chunk),
                         {("program", "Pro"): 1, ("status", "Quick Play: In Queue"): 1})

    def test_the_group_one_status_field_counts_as_status_too(self):
        """It alone carried "In App" when Overwatch was quit."""
        self.assertEqual(self.collect(_message(ACCOUNT, _status("In App", group=1, field=25))),
                         {("status", "In App"): 1})

    def test_a_cleared_status_is_an_empty_one(self):
        self.assertEqual(self.collect(_message(ACCOUNT, _status(None))), {("status", ""): 1})

    def test_a_friends_presence_is_never_ours(self):
        """Friends' presence sits in the same memory in the same shape."""
        chunk = _message(FRIEND, _status("Quick Play: In Queue")) + \
            _message(ACCOUNT, _status("In Menus"))
        self.assertEqual(self.collect(chunk), {("status", "In Menus"): 1})

    def test_other_fields_and_other_programs_are_ignored(self):
        chunk = _message(ACCOUNT, _operation(2, 5, "overjump#2179"),
                         _operation(2, 22, "In Menus", program=0x50726F))
        self.assertEqual(self.collect(chunk), {})

    def test_every_copy_is_counted(self):
        copy = _message(ACCOUNT, _status("Practice Range"))
        chunk = copy + b"\x00" * 16 + copy + b"\x00" * 3 + copy
        self.assertEqual(self.collect(chunk), {("status", "Practice Range"): 3})

    def test_reads_records_too(self):
        """With the Battle.net window open, changes arrive as records in a renderer."""
        self.assertEqual(self.collect(b"\x00" * 5 + _record("In Menus")),
                         {("program", "Pro"): 1, ("status", "In Menus"): 1})

    def test_a_record_without_a_status_only_names_the_program(self):
        self.assertEqual(self.collect(_record(None)), {("program", "Pro"): 1})

    def test_a_friends_record_is_never_ours(self):
        chunk = _record("Quick Play: In Queue", account=str(FRIEND), battletag="friend#1234")
        self.assertEqual(self.collect(chunk), {})

    def test_adjacent_records_do_not_mix(self):
        chunk = _record("In App", program="App") + _record("Quick Play: In Queue")
        self.assertEqual(self.collect(chunk), {("program", "App"): 1, ("status", "In App"): 1,
                                               ("program", "Pro"): 1,
                                               ("status", "Quick Play: In Queue"): 1})

    def test_a_truncated_message_does_not_raise(self):
        self.assertEqual(self.collect(_message(ACCOUNT, _status("Quick Play: In Queue"))[:14]), {})


_TEXTS = {"pro": "Pro", "app": "App", "bsap": "BSAp", "menus": "In Menus",
          "queue": "Quick Play: In Queue", "range": "Practice Range", "inapp": "In App",
          "empty": ""}


class PresenceTrackerTests(unittest.TestCase):
    """`PresenceTracker` - which value is current, judged by what arrived. The main test
    replays a sequence measured live: Overwatch launched, then leave queue, Practice Range,
    leave it, queue, quit Overwatch - with the Battle.net window closed to the tray."""

    IDENTITY = queuepresence.Identity("overjump#2179", str(ACCOUNT))

    def setUp(self):
        self.tracker = bnetpresence.PresenceTracker(self.IDENTITY)

    def read(self, **counts):
        from collections import Counter
        observed = Counter()
        for name, count in counts.items():
            kind, _, text = name.partition("_")
            observed[(kind, _TEXTS[text])] = count
        record = self.tracker.observe(observed)
        reading = queuepresence.parse_record(record, 0.0, identity=self.IDENTITY) if record else None
        return (reading.state, reading.mode) if reading else None

    def test_a_first_read_that_disagrees_with_itself_is_unknown(self):
        """Measured: after quitting Overwatch, "Practice Range" copies outnumbered "In App"."""
        self.assertEqual(self.read(program_pro=1, program_bsap=2, status_inapp=3, status_range=2),
                         (queuepresence.UNKNOWN, None))

    def test_a_first_read_that_agrees_with_itself_is_taken(self):
        self.assertEqual(self.read(program_app=2, program_bsap=5, status_inapp=11, status_empty=5),
                         (queuepresence.ELSEWHERE, None))

    def test_the_measured_sequence_reads_in_order(self):
        base = dict(program_app=2, program_bsap=5, status_inapp=11, status_empty=5)
        self.read(**base)
        self.assertEqual(self.read(**base, program_pro=7), (queuepresence.UNKNOWN, None))
        # Leaving the queue brought the old status along with the new, 2 copies to 6.
        self.assertEqual(self.read(**base, program_pro=7, status_menus=6, status_queue=2),
                         (queuepresence.MENUS, None))
        self.assertEqual(self.read(**base, program_pro=7, status_menus=6, status_queue=2,
                                   status_range=7)[0], queuepresence.PLAYING_OTHER)
        self.assertEqual(self.read(**base, program_pro=7, status_menus=11, status_queue=2,
                                   status_range=7), (queuepresence.MENUS, None))
        self.assertEqual(self.read(**base, program_pro=7, status_menus=11, status_queue=9,
                                   status_range=7), (queuepresence.QUEUEING, "quickPlay"))
        # Quitting sent only the group-one "In App"; the program field stayed "Pro".
        quit_counts = dict(base, status_inapp=17)
        self.assertEqual(self.read(**quit_counts, program_pro=7, status_menus=11, status_queue=9,
                                   status_range=7), (queuepresence.ELSEWHERE, None))

    def test_leftovers_that_do_not_grow_never_win_back(self):
        self.read(program_pro=3, status_menus=4)
        self.read(program_pro=3, status_menus=4, status_queue=2)
        for _ in range(3):
            state = self.read(program_pro=3, status_menus=4, status_queue=2)
        self.assertEqual(state, (queuepresence.QUEUEING, "quickPlay"))

    def test_copies_disappearing_changes_nothing(self):
        self.read(program_pro=3, status_menus=4)
        self.read(program_pro=3, status_menus=4, status_queue=2)
        self.assertEqual(self.read(program_pro=1, status_menus=1, status_queue=1),
                         (queuepresence.QUEUEING, "quickPlay"))

    def test_a_change_after_every_copy_was_recycled_still_arrives(self):
        """Measured live: sitting unchanged, every copy was gone within minutes."""
        self.read(program_pro=3, status_menus=4)
        self.read(program_pro=3, status_menus=4, status_queue=2)
        self.assertEqual(self.read(), (queuepresence.QUEUEING, "quickPlay"))
        self.assertEqual(self.read(program_pro=1, status_menus=1),
                         (queuepresence.MENUS, None))

    def test_a_real_status_beats_an_empty_one_arriving_with_it(self):
        """Overwatch clears its status while loading, then sets one moments later."""
        self.read(program_app=2, status_inapp=4)
        self.assertEqual(self.read(program_app=2, status_inapp=4, program_pro=5,
                                   status_empty=9, status_menus=2),
                         (queuepresence.MENUS, None))

    def test_a_value_never_seen_beats_an_old_one_that_merely_grew(self):
        self.read(program_pro=3, status_menus=4, status_range=1)
        self.assertEqual(self.read(program_pro=3, status_menus=9, status_range=1, status_queue=1),
                         (queuepresence.QUEUEING, "quickPlay"))


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
