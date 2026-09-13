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


@unittest.skipUnless(bnetpresence.PRESENCE_AVAILABLE, WINDOWS_ONLY)
class RelaunchIfMissingDebugPortTests(unittest.TestCase):
    """`relaunch_if_missing_debug_port` — only a Battle.net that's running *and* still has
    no debug port after the grace period gets relaunched. Time, the port, the process
    check and the relaunch itself are all fakes."""

    def setUp(self):
        self._running = bnetpresence.battlenet_running
        self._port_open = bnetpresence.debug_port_open
        self._force = bnetpresence.force_relaunch_battlenet
        self._sleep = __import__("time").sleep
        self._monotonic = __import__("time").monotonic
        self.relaunched = []
        bnetpresence.force_relaunch_battlenet = (
            lambda port=None, log=None: self.relaunched.append(port) or True)
        clock = [0.0]

        def sleep(seconds):
            clock[0] += seconds

        __import__("time").sleep = sleep
        __import__("time").monotonic = lambda: clock[0]

    def tearDown(self):
        bnetpresence.battlenet_running = self._running
        bnetpresence.debug_port_open = self._port_open
        bnetpresence.force_relaunch_battlenet = self._force
        __import__("time").sleep = self._sleep
        __import__("time").monotonic = self._monotonic

    def test_nothing_running_is_left_alone(self):
        bnetpresence.battlenet_running = lambda: False
        bnetpresence.debug_port_open = lambda port=None: False
        self.assertFalse(bnetpresence.relaunch_if_missing_debug_port())
        self.assertEqual(self.relaunched, [])

    def test_a_battlenet_with_its_port_open_is_left_alone(self):
        bnetpresence.battlenet_running = lambda: True
        bnetpresence.debug_port_open = lambda port=None: True
        self.assertFalse(bnetpresence.relaunch_if_missing_debug_port())
        self.assertEqual(self.relaunched, [])

    def test_a_battlenet_still_starting_up_is_given_its_grace_period(self):
        """The port opens a few seconds into startup. A panel opened right after
        Battle.net must not close it for being slow."""
        bnetpresence.battlenet_running = lambda: True
        checks = {"n": 0}

        def opens_on_the_fourth_look(port=None):
            checks["n"] += 1
            return checks["n"] >= 4

        bnetpresence.debug_port_open = opens_on_the_fourth_look
        self.assertFalse(bnetpresence.relaunch_if_missing_debug_port(grace_seconds=15))
        self.assertEqual(self.relaunched, [])

    def test_no_port_after_the_grace_period_relaunches_with_the_right_port(self):
        bnetpresence.battlenet_running = lambda: True
        bnetpresence.debug_port_open = lambda port=None: False
        self.assertTrue(bnetpresence.relaunch_if_missing_debug_port(port=4321))
        self.assertEqual(self.relaunched, [4321])

    def test_one_closed_during_the_grace_period_is_left_alone(self):
        looks = {"n": 0}

        def running():
            looks["n"] += 1
            return looks["n"] == 1               # running at the start, gone by the end

        bnetpresence.battlenet_running = running
        bnetpresence.debug_port_open = lambda port=None: False
        self.assertFalse(bnetpresence.relaunch_if_missing_debug_port())
        self.assertEqual(self.relaunched, [])


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
