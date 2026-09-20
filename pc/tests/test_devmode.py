import os
import sys
import threading
import unittest
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import devmode  # noqa: E402
import presence as p  # noqa: E402


class ClassifyTests(unittest.TestCase):
    """`subscribeSelfPresence` records, in the shape Battle.net hands them back."""

    def test_a_queue(self):
        self.assertEqual(devmode.classify({"program_id": "Pro", "program_name": "Overwatch",
                                           "rich_presence": "Competitive: In Queue"}),
                         (p.QUEUEING, "competitive"))

    def test_a_match(self):
        self.assertEqual(devmode.classify({"program_id": "Pro", "rich_presence": "Quick Play: In Game"}),
                         (p.IN_GAME, "quickPlay"))

    def test_the_menus(self):
        self.assertEqual(devmode.classify({"program_name": "Overwatch", "rich_presence": "In Menus"}),
                         (p.MENUS, None))

    def test_another_game_is_elsewhere_whatever_it_says(self):
        self.assertEqual(devmode.classify({"program_id": "WTCG", "program_name": "Hearthstone",
                                           "rich_presence": "Ranked: In Queue"}),
                         (p.ELSEWHERE, None))

    def test_no_program_named_is_unknown_not_elsewhere(self):
        self.assertEqual(devmode.classify({"rich_presence": ""}), (p.UNKNOWN, None))

    def test_no_record_is_unknown(self):
        self.assertEqual(devmode.classify(None), (p.UNKNOWN, None))


class AppShellTests(unittest.TestCase):
    def test_a_page_wins_over_a_webview(self):
        webview = {"type": "webview", "webSocketDebuggerUrl": "ws://webview"}
        page = {"type": "page", "webSocketDebuggerUrl": "ws://page"}
        self.assertIs(devmode._app_shell([webview, page]), page)

    def test_a_webview_will_do_when_there_is_no_page(self):
        webview = {"type": "webview", "webSocketDebuggerUrl": "ws://webview"}
        self.assertIs(devmode._app_shell([{"type": "page"}, webview]), webview)

    def test_nothing_attached_is_no_target(self):
        self.assertIsNone(devmode._app_shell([{"type": "page"}]))
        self.assertIsNone(devmode._app_shell([]))


class _FakeDebugPort:
    """Stands in for the real one: `answers` is read off one per `read()`."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.connected = False

    def read(self):
        record = self.answers.pop(0)
        self.connected = record is not None
        return devmode.classify(record)


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.lines = []
        self.reader = devmode.Reader("memory", log=self.lines.append)
        self.reader.memory = _FakeMemory()

    def test_memory_only_never_opens_a_debug_port(self):
        self.assertIsNone(self.reader.debug)
        self.reader.start()
        self.assertEqual(self.reader.read(), (p.MENUS, None))
        self.assertEqual(self.reader.source, "memory")

    def test_the_debug_port_wins_while_it_answers(self):
        self.reader.debug = _FakeDebugPort([{"program_id": "Pro", "rich_presence": "Arcade: In Queue"}])
        self.assertEqual(self.reader.read(), (p.QUEUEING, "arcade"))
        self.assertEqual(self.reader.source, "devmode")
        self.assertTrue(self.reader.connected)
        self.assertEqual(self.reader.memory.reads, 0)

    def test_a_quiet_debug_port_falls_back_to_memory(self):
        self.reader.debug = _FakeDebugPort([None])
        self.assertEqual(self.reader.read(), (p.MENUS, None))
        self.assertEqual(self.reader.source, "memory")
        self.assertEqual(self.reader.memory.reads, 1)

    def test_falling_back_rebaselines_the_memory_reader(self):
        """The copy counts from before the app read over the debug port say nothing about
        what arrived while it wasn't looking."""
        self.reader.debug = _FakeDebugPort([{"program_id": "Pro", "rich_presence": "In Menus"}, None])
        self.reader.read()
        self.assertEqual(self.reader.memory.rebaselines, 0)
        self.reader.read()
        self.assertEqual(self.reader.memory.rebaselines, 1)

    def test_neither_source_connected_is_not_connected(self):
        self.reader.debug = _FakeDebugPort([None])
        self.reader.memory.connected = False
        self.reader.read()
        self.assertFalse(self.reader.connected)


class _FakeMemory:
    def __init__(self):
        self.reads = 0
        self.rebaselines = 0
        self.connected = True

    def read(self):
        self.reads += 1
        return p.MENUS, None

    def rebaseline(self):
        self.rebaselines += 1


class EnsureTests(unittest.TestCase):
    """`ensure` decides whether to touch Battle.net at all; the launching itself is stubbed."""

    def setUp(self):
        self.saved = {name: getattr(devmode, name)
                      for name in ("AVAILABLE", "running", "port_open", "launch", "relaunch")}
        devmode.AVAILABLE = True
        self.did = []
        devmode.launch = lambda port=0, log=None: self.did.append("launch") or True
        devmode.relaunch = lambda port=0, log=None, stop=None: self.did.append("relaunch") or True

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(devmode, name, value)

    def test_a_battlenet_that_is_not_running_is_started_in_developer_mode(self):
        devmode.running = lambda: False
        self.assertTrue(devmode.ensure(grace_seconds=0))
        self.assertEqual(self.did, ["launch"])

    def test_a_battlenet_that_already_has_the_port_is_left_alone(self):
        devmode.running, devmode.port_open = lambda: True, lambda port=0: True
        self.assertFalse(devmode.ensure(grace_seconds=0))
        self.assertEqual(self.did, [])

    def test_a_battlenet_without_the_port_is_restarted_once_the_grace_runs_out(self):
        devmode.running, devmode.port_open = lambda: True, lambda port=0: False
        self.assertTrue(devmode.ensure(grace_seconds=0))
        self.assertEqual(self.did, ["relaunch"])

    def test_a_battlenet_that_closes_while_we_wait_is_not_restarted(self):
        alive = [True, False]
        devmode.running = lambda: alive.pop(0)
        devmode.port_open = lambda port=0: False
        self.assertFalse(devmode.ensure(grace_seconds=0))
        self.assertEqual(self.did, [])

    def test_nothing_happens_where_there_is_no_debug_port_to_open(self):
        devmode.AVAILABLE = False
        devmode.running = lambda: False
        self.assertFalse(devmode.ensure(grace_seconds=0))
        self.assertEqual(self.did, [])


class RelaunchTests(unittest.TestCase):
    """Nothing here may actually close anything: `subprocess.run` is stubbed for the whole
    class, so a regression in the guard fails the assertion instead of killing the
    developer's own Battle.net mid-game."""

    def setUp(self):
        self.saved = (devmode.presence.pids_named, devmode.subprocess.run)
        self.killed = []
        devmode.subprocess.run = lambda *args, **kwargs: self.killed.append(args) or self.fail(
            "relaunch tried to close Battle.net for real")
        self.lines = []

    def tearDown(self):
        devmode.presence.pids_named, devmode.subprocess.run = self.saved

    def test_an_open_overwatch_is_never_interrupted(self):
        devmode.presence.pids_named = lambda name: frozenset({42}) if name == "overwatch.exe" else frozenset()
        self.assertFalse(devmode.relaunch(log=self.lines.append))
        self.assertIn("Overwatch is open", self.lines[0])
        self.assertEqual(self.killed, [])

    def test_the_app_quitting_stops_it_before_anything_closes(self):
        devmode.presence.pids_named = lambda name: frozenset()
        self.assertFalse(devmode.relaunch(log=self.lines.append, stop=_set_event()))
        self.assertEqual((self.killed, self.lines), ([], []))


class WhyTests(unittest.TestCase):
    """What a `taskkill` that didn't work gets reported as -- an elevated Battle.net says
    "Access is denied" rather than leaving the log with an unexplained refusal."""

    def test_it_quotes_taskkill(self):
        self.assertEqual(devmode._why(_Closing(1, stderr="ERROR: Access is denied.\n")),
                         "ERROR: Access is denied.")

    def test_it_falls_back_to_the_exit_code(self):
        self.assertEqual(devmode._why(_Closing(128)), "exit code 128")

    def test_a_kill_that_worked_means_something_restarted_it(self):
        self.assertEqual(devmode._why(_Closing(0, stdout="SUCCESS")), "it's still there")


class _Closing:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _set_event():
    event = threading.Event()
    event.set()
    return event


class ModeTests(unittest.TestCase):
    def test_devmode_degrades_to_memory_where_it_cannot_work(self):
        saved = devmode.AVAILABLE
        devmode.AVAILABLE = False
        try:
            reader = devmode.Reader("devmode", log=lambda message: None)
        finally:
            devmode.AVAILABLE = saved
        self.assertEqual(reader.mode, "memory")
        self.assertIsNone(reader.debug)


@unittest.skipUnless(p.AVAILABLE, "Windows only")
class PidsNamedTests(unittest.TestCase):
    def test_it_finds_a_process_windows_always_runs(self):
        self.assertTrue(p.pids_named("csrss.exe"))

    def test_nothing_is_running_under_a_made_up_name(self):
        self.assertEqual(p.pids_named("no-such-program-42.exe"), frozenset())


class RebaselineTests(unittest.TestCase):
    def test_it_keeps_the_reading_and_forgets_the_counts(self):
        """The reading stands; it's the counts either side of the gap that can't be
        compared, so the next scan is a fresh baseline and the one after it can read an
        arrival off it."""
        reader = p.Presence(log=lambda message: None)
        tracker = reader._tracker
        tracker.observe(Counter({("status", "In Menus"): 9}))
        reader.rebaseline()
        self.assertIs(reader._tracker, tracker)
        # A scan mid-queue teaches it nothing: the counts grew while nobody was looking.
        self.assertEqual(tracker.observe(Counter({("status", "In Menus"): 9,
                                                  ("status", "Competitive: In Queue"): 40})),
                         (p.MENUS, None))
        self.assertEqual(tracker.observe(Counter({("status", "In Menus"): 9,
                                                  ("status", "Competitive: In Queue"): 41})),
                         (p.QUEUEING, "competitive"))


if __name__ == "__main__":
    unittest.main()
