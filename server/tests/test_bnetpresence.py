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


class _FakeShortcut:
    """A `.lnk` file's `TargetPath`/`Arguments`, plus every `subprocess.run` call made
    against it — standing in for the real COM object `ensure_shortcut_targets_debug_port`
    drives through PowerShell. `flaky_writes` reproduces the one real failure this
    module has actually seen: a write that reports success (exit 0) but the target
    quietly stays the old one — the reason that function verifies by reading back
    rather than trusting the exit code."""

    def __init__(self, target: str, args: str, flaky_writes: int = 0):
        self.target = target
        self.args = args
        self._flaky_writes_left = flaky_writes
        self.writes = []

    def run(self, command_args, **kwargs):
        script = command_args[-1]
        class _Completed:
            returncode = 0
            stdout = ""
        completed = _Completed()
        if "Write-Output" in script:
            completed.stdout = "%s\n%s\n" % (self.target, self.args)
        else:
            self.writes.append(script)
            if self._flaky_writes_left > 0:
                self._flaky_writes_left -= 1     # "succeeds" without changing anything
            else:
                self.target = bnetpresence._BATTLENET_EXE_CANDIDATES[0]
                self.args = "--remote-debugging-port=9222"
        return completed


@unittest.skipUnless(bnetpresence.PRESENCE_AVAILABLE, WINDOWS_ONLY)
class EnsureShortcutTargetsDebugPortTests(unittest.TestCase):
    """`ensure_shortcut_targets_debug_port` — reads and rewrites the Start Menu
    shortcut through fake `subprocess.run` calls; no real shortcut file is ever
    touched."""

    def setUp(self):
        self._exists = os.path.exists
        self._run = __import__("subprocess").run

    def tearDown(self):
        os.path.exists = self._exists
        __import__("subprocess").run = self._run

    def test_already_pointed_right_does_nothing(self):
        exe = bnetpresence._BATTLENET_EXE_CANDIDATES[0]
        os.path.exists = lambda path: True
        calls = []

        def run(args, **kwargs):
            calls.append(args[-1])
            class _C:
                stdout = exe + "\n--remote-debugging-port=9222\n"
                returncode = 0
            return _C()

        __import__("subprocess").run = run
        self.assertFalse(bnetpresence.ensure_shortcut_targets_debug_port(port=9222))
        self.assertEqual(len(calls), 1)                     # read only, no write

    def test_repoints_a_shortcut_still_aimed_at_the_launcher(self):
        exe = bnetpresence._BATTLENET_EXE_CANDIDATES[0]
        os.path.exists = lambda path: True
        shortcut = _FakeShortcut(
            target=r"C:\Program Files (x86)\Battle.net\Battle.net Launcher.exe",
            args="")
        __import__("subprocess").run = shortcut.run

        self.assertTrue(bnetpresence.ensure_shortcut_targets_debug_port(port=9222))
        self.assertEqual(len(shortcut.writes), 1)
        self.assertEqual(shortcut.target, exe)
        self.assertEqual(shortcut.args, "--remote-debugging-port=9222")

    def test_retries_once_if_the_write_does_not_take(self):
        """The one real failure this project has actually seen from `.Save()`: it
        reported success but the target quietly stayed the old one. Verified by
        reading the shortcut back, not assumed from a clean exit code."""
        exe = bnetpresence._BATTLENET_EXE_CANDIDATES[0]
        os.path.exists = lambda path: True
        shortcut = _FakeShortcut(
            target=r"C:\Program Files (x86)\Battle.net\Battle.net Launcher.exe",
            args="", flaky_writes=1)          # first write "succeeds" but doesn't stick
        __import__("subprocess").run = shortcut.run

        self.assertTrue(bnetpresence.ensure_shortcut_targets_debug_port(port=9222))
        self.assertEqual(len(shortcut.writes), 2)
        self.assertEqual(shortcut.target, exe)

    def test_gives_up_after_the_retry_also_does_not_take(self):
        exe = bnetpresence._BATTLENET_EXE_CANDIDATES[0]
        os.path.exists = lambda path: True
        shortcut = _FakeShortcut(
            target=r"C:\Program Files (x86)\Battle.net\Battle.net Launcher.exe",
            args="", flaky_writes=99)          # never actually takes
        __import__("subprocess").run = shortcut.run

        self.assertFalse(bnetpresence.ensure_shortcut_targets_debug_port(port=9222))
        self.assertEqual(len(shortcut.writes), 2)          # one retry, then give up

    def test_no_shortcut_file_does_nothing(self):
        os.path.exists = lambda path: False
        self.assertFalse(bnetpresence.ensure_shortcut_targets_debug_port())

    def test_no_battlenet_exe_found_does_nothing(self):
        os.path.exists = lambda path: path == bnetpresence.DEFAULT_SHORTCUT_PATH
        self.assertFalse(bnetpresence.ensure_shortcut_targets_debug_port())


class _FakeRegistry:
    """A single `HKCU\\...\\Run` value, plus a read/write count — standing in for the
    real Windows registry `ensure_autostart_has_debug_port` reads and writes through
    `winreg`."""

    def __init__(self, value=None, value_type=1):
        self.value = value          # `None` means the value doesn't exist at all
        self.value_type = value_type
        self.reads = 0
        self.writes = 0

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    def open_key(self, hive, path, reserved, access):
        if self.value is None:
            raise FileNotFoundError()
        return self._Key()

    def query_value(self, key, name):
        self.reads += 1
        return self.value, self.value_type

    def set_value(self, key, name, reserved, value_type, value):
        self.writes += 1
        self.value = value
        self.value_type = value_type


@unittest.skipUnless(bnetpresence.PRESENCE_AVAILABLE, WINDOWS_ONLY)
class EnsureAutostartHasDebugPortTests(unittest.TestCase):
    """`ensure_autostart_has_debug_port` — the fix for the actual reason a Start Menu
    shortcut fix alone wasn't enough: Battle.net's own auto-start entry has no flag on
    it, and Battle.net's single-instance guard means a later launch with one never
    takes effect while that unflagged copy is already running."""

    def setUp(self):
        import winreg
        self._open_key = winreg.OpenKey
        self._query_value = winreg.QueryValueEx
        self._set_value = winreg.SetValueEx

    def tearDown(self):
        import winreg
        winreg.OpenKey = self._open_key
        winreg.QueryValueEx = self._query_value
        winreg.SetValueEx = self._set_value

    def _install(self, registry):
        import winreg
        winreg.OpenKey = registry.open_key
        winreg.QueryValueEx = registry.query_value
        winreg.SetValueEx = registry.set_value

    def test_no_autostart_entry_does_nothing(self):
        self._install(_FakeRegistry(value=None))
        self.assertFalse(bnetpresence.ensure_autostart_has_debug_port(port=9222))

    def test_already_has_the_flag_does_nothing(self):
        registry = _FakeRegistry(
            value=r'"C:\Program Files (x86)\Battle.net\Battle.net.exe" --autostarted '
                 "--remote-debugging-port=9222")
        self._install(registry)
        self.assertFalse(bnetpresence.ensure_autostart_has_debug_port(port=9222))
        self.assertEqual(registry.writes, 0)

    def test_adds_the_flag_to_the_existing_command(self):
        registry = _FakeRegistry(
            value=r'"C:\Program Files (x86)\Battle.net\Battle.net.exe" --autostarted')
        self._install(registry)
        self.assertTrue(bnetpresence.ensure_autostart_has_debug_port(port=9222))
        self.assertEqual(registry.writes, 1)
        self.assertIn("--autostarted", registry.value)
        self.assertIn("--remote-debugging-port=9222", registry.value)

    def test_a_read_failure_does_nothing(self):
        def broken_open(*args, **kwargs):
            raise OSError("access denied")
        registry = _FakeRegistry(value="anything")
        registry.open_key = broken_open
        self._install(registry)
        self.assertFalse(bnetpresence.ensure_autostart_has_debug_port(port=9222))

    def test_a_write_that_does_not_take_reports_failure(self):
        """The same defensive shape as the shortcut fix: a write is verified by reading
        it back, not trusted just because it didn't raise."""
        registry = _FakeRegistry(
            value=r'"C:\Program Files (x86)\Battle.net\Battle.net.exe" --autostarted')

        def set_value_that_does_not_stick(key, name, reserved, value_type, value):
            registry.writes += 1              # reports success, but leaves value alone

        registry.set_value = set_value_that_does_not_stick
        self._install(registry)
        self.assertFalse(bnetpresence.ensure_autostart_has_debug_port(port=9222))


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
