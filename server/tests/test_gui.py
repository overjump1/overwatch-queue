"""The window builds, wires up, and reflects the server's state.

Runs on Qt's offscreen platform, so it needs no display — but it does need PyQt6, and
skips itself when that isn't installed.
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "server"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from owqserver import protocol, queueroles, queuevision, queuewatch  # noqa: E402
from owqserver.catalog import Catalog                            # noqa: E402
from owqserver.pairing import Pairing                            # noqa: E402
from owqserver.activitytokens import ActivityTokens              # noqa: E402
from owqserver.pushtokens import PushTokens                      # noqa: E402
from owqserver.queueserver import SCENARIOS, QueueServer         # noqa: E402

try:
    from PyQt6.QtWidgets import QApplication
    from owqserver.gui import ControlPanel
    APP = QApplication.instance() or QApplication([])
except ImportError:                                              # pragma: no cover
    APP = None


@unittest.skipIf(APP is None, "PyQt6 isn't installed")
class PanelTests(unittest.TestCase):
    def setUp(self):
        pairing = Pairing(token="3f2504e0-4f89-41d3-9a0c-0305e82c3301",
                          port=8902, path=os.devnull)
        self.server = QueueServer(pairing, Catalog(), push_tokens=PushTokens(os.devnull),
                        activity_tokens=ActivityTokens(os.devnull))
        self.panel = ControlPanel(self.server)     # no start(): no socket needed here

    def tearDown(self):
        self.panel.close()

    # ------------------------------------------------------------ pairing

    def test_the_qr_carries_the_pairing_url(self):
        self.assertIn("token=3f2504e0-4f89-41d3-9a0c-0305e82c3301", self.panel._pair_url())
        self.assertGreater(self.panel.qr_label.pixmap().width(), 150)

    def test_a_new_address_redraws_the_code(self):
        before = self.panel.qr_label.pixmap().toImage()
        self.panel.address_picker.addItem("10.0.0.5")
        self.panel.address_picker.setCurrentText("10.0.0.5")
        self.assertIn("host=10.0.0.5", self.panel._pair_url())
        self.assertNotEqual(before, self.panel.qr_label.pixmap().toImage())

    def test_a_nonsense_port_is_put_back(self):
        self.panel.port_field.setText("no")
        self.panel._change_port()
        self.assertEqual(self.panel.port_field.text(), "8902")

    # ------------------------------------------------------------ state

    def test_jump_buttons_track_the_transition_table(self):
        self.assertFalse(self.panel.jump_buttons["matchFound"].isEnabled())
        self.server.apply(protocol.searching("competitive", "tank", protocol.now(), 240))
        self.panel._refresh()
        self.assertTrue(self.panel.jump_buttons["matchFound"].isEnabled())
        self.assertFalse(self.panel.jump_buttons["heroSelect"].isEnabled())

    def test_skip_buttons_are_live_only_while_searching(self):
        self.assertFalse(self.panel.skip_one.isEnabled())
        self.server.apply(protocol.searching("quickPlay", "flex", protocol.now(), 40))
        self.panel._refresh()
        self.assertTrue(self.panel.skip_one.isEnabled())

    def test_stop_is_live_only_during_a_scenario(self):
        self.assertFalse(self.panel.stop_scenario.isEnabled())
        self.server.play(SCENARIOS[0])
        self.panel._refresh()
        self.assertTrue(self.panel.stop_scenario.isEnabled())
        self.server.stop_scenario()

    def test_the_status_line_says_where_the_queue_is(self):
        self.server.apply(protocol.searching("competitive", "tank", protocol.now(), 240))
        self.panel._refresh()
        self.assertIn("Searching", self.panel.status.text())
        self.assertIn("sequence 1", self.panel.status.text())
        self.assertIn("0 devices", self.panel.status.text())

    # ------------------------------------------------------------ widgets

    def test_choosing_an_open_queue_mode_disables_the_role_picker(self):
        self.panel.mode_picker.setCurrentIndex(protocol.MODES.index("mysteryHeroes"))
        self.assertFalse(self.panel.role_picker.isEnabled())
        self.assertEqual(self.panel.controls.effective_role, "open")
        self.panel.mode_picker.setCurrentIndex(protocol.MODES.index("competitive"))
        self.assertTrue(self.panel.role_picker.isEnabled())

    def test_the_estimate_slider_reaches_the_queue(self):
        self.server.apply(protocol.searching("competitive", "tank", protocol.now(), 240))
        self.panel.estimate_slider.setValue(320)
        self.assertEqual(self.server.session.phase["data"]["estimatedWait"], 320)
        self.assertIn("5:20", self.panel.estimate_label.text())

    def test_the_log_keeps_the_newest_lines(self):
        for i in range(450):
            self.panel._write_log("line %d" % i)
        lines = self.panel.log_view.toPlainText().splitlines()
        self.assertLessEqual(len(lines), 400)
        self.assertIn("line 449", lines[-1])

    def test_it_renders(self):
        self.panel.resize(1060, 800)
        self.panel.show()
        APP.processEvents()
        picture = self.panel.grab()
        self.assertEqual((picture.width(), picture.height()), (1060, 800))


class _StubWatcher:
    """A watcher that reports whatever the test wants, without a thread or a screen."""

    def __init__(self, latest=None, running=True):
        self.latest = latest
        self.running = running
        self.stopped = False

    def stop(self):
        # Closing the panel stops the server, and stopping the server stops the watcher.
        self.stopped = True


def _seen(state, mode="competitive", elapsed=0.0, source="self"):
    return queuewatch.QueueObservation(state, queuevision.MENU_PILL, mode, elapsed, 0.0,
                                       source, 0.9)


@unittest.skipIf(APP is None, "PyQt6 isn't installed")
class QueueVisionPanelTests(unittest.TestCase):
    """The line that says what the watcher can see, and the dropdown beside it."""

    def setUp(self):
        pairing = Pairing(token="3f2504e0-4f89-41d3-9a0c-0305e82c3301",
                          port=8903, path=os.devnull)
        self.server = QueueServer(pairing, Catalog(), push_tokens=PushTokens(os.devnull),
                                  activity_tokens=ActivityTokens(os.devnull))
        self.panel = ControlPanel(self.server)

    def tearDown(self):
        self.panel.close()

    def _show(self, watcher):
        self.server.queue_watcher = watcher
        self.panel._refresh_queue_vision()
        return self.panel.queue_state.text()

    def test_it_says_so_when_nothing_is_watching(self):
        self.assertEqual(self._show(None), "Not watching.")

    def test_a_watcher_that_has_seen_nothing_says_so_too(self):
        """Distinct from "not watching" on purpose: one means the feature is off, the
        other means it is on and the screen has no queue on it."""
        self.assertIn("no queue", self._show(_StubWatcher(_seen(queuewatch.IDLE))))

    def test_a_queue_shows_its_state_mode_and_wait(self):
        text = self._show(_StubWatcher(_seen(queuewatch.SEARCHING_IN_GAME,
                                             elapsed=137.0)))
        self.assertIn("Searching, in a game", text)
        self.assertIn("Competitive", text)
        self.assertIn("2:17", text)

    def test_the_gap_where_the_banner_is_hidden_is_named_as_such(self):
        self.assertIn("out of sight",
                      self._show(_StubWatcher(_seen(queuewatch.SEARCHING_HIDDEN))))

    def test_it_says_whether_the_wait_was_read_or_guessed(self):
        self.assertIn("timed from here",
                      self._show(_StubWatcher(_seen(queuewatch.SEARCHING_MENU))))
        self.assertIn("read off the screen",
                      self._show(_StubWatcher(_seen(queuewatch.SEARCHING_MENU,
                                                    source="timer"))))

    def test_a_match_is_picked_out_from_the_rest(self):
        self._show(_StubWatcher(_seen(queuewatch.GAME_FOUND)))
        self.assertIn("f99e1a", self.panel.queue_state.styleSheet())

    def test_the_dropdown_follows_a_mode_the_watcher_found(self):
        """Otherwise the panel says one mode while the phone shows another, and the
        first place anyone looks for that bug is the app."""
        self.controls_mode("quickPlay")
        self._show(_StubWatcher(_seen(queuewatch.SEARCHING_MENU, mode="quickPlay")))
        self.assertEqual(self.panel.mode_picker.currentText(), "Quick Play")

    def test_following_it_does_not_write_the_mode_back(self):
        """`setCurrentIndex` fires the same signal a human click does. Blocking it keeps
        the panel from answering the watcher back."""
        self.controls_mode("arcade")
        self._show(_StubWatcher(_seen(queuewatch.SEARCHING_MENU, mode="arcade")))
        self.assertEqual(self.panel.controls.mode, "arcade")
        self.assertFalse(self.panel.role_picker.isEnabled())    # arcade has no role queue

    def controls_mode(self, mode):
        self.panel.controls.mode = mode


def _selection(roles, mode=None):
    return queueroles.RoleSelection(frozenset(roles), mode, {}, True)


@unittest.skipIf(APP is None, "PyQt6 isn't installed")
class RoleDetectionPanelTests(unittest.TestCase):
    """What clicking "Detect from screen" does with whatever the scan comes back with.

    `_role_scanned` is exercised directly rather than through the worker thread it
    normally arrives from — the threading is `_jump`'s pattern re-used verbatim, and
    already covered by nothing failing to deadlock in every other test in this file.
    What's worth testing here is what the panel *does* with an answer once it has one.
    """

    def setUp(self):
        pairing = Pairing(token="3f2504e0-4f89-41d3-9a0c-0305e82c3301",
                          port=8904, path=os.devnull)
        self.server = QueueServer(pairing, Catalog(), push_tokens=PushTokens(os.devnull),
                                  activity_tokens=ActivityTokens(os.devnull))
        self.panel = ControlPanel(self.server)
        self.panel.detect_role_button.setEnabled(False)   # as it is mid-scan

    def tearDown(self):
        self.panel.close()

    def test_a_single_checked_role_is_applied_to_the_dropdown(self):
        self.panel._role_scanned(_selection(["support"]))
        self.assertEqual(self.panel.role_picker.currentText(), "Support")
        self.assertEqual(self.panel.controls.role, "support")

    def test_more_than_one_checked_role_is_applied_as_flex(self):
        self.panel._role_scanned(_selection(["tank", "damage"]))
        self.assertEqual(self.panel.controls.role, "flex")

    def test_nothing_checked_leaves_the_dropdown_alone(self):
        self.panel.controls.role = "tank"
        self.panel._role_scanned(_selection([]))
        self.assertEqual(self.panel.controls.role, "tank")

    def test_no_screen_to_read_leaves_the_dropdown_alone(self):
        self.panel.controls.role = "tank"
        self.panel._role_scanned(None)
        self.assertEqual(self.panel.controls.role, "tank")

    def test_the_button_is_re_enabled_once_an_answer_comes_back(self):
        self.panel._role_scanned(None)
        self.assertTrue(self.panel.detect_role_button.isEnabled())

    def test_applying_a_role_does_not_write_it_straight_back(self):
        """`setCurrentIndex` fires the same signal a human click does. Blocking it keeps
        the panel from answering its own scan back — the same guard `_sync_mode_picker`
        needs for the same reason."""
        seen = []
        self.panel.role_picker.currentIndexChanged.connect(lambda _: seen.append(1))
        self.panel._role_scanned(_selection(["support"]))
        self.assertEqual(seen, [])

    def test_a_mode_named_by_the_screen_follows_when_the_current_mode_queues_by_role(self):
        self.controls_mode("quickPlay")
        self.panel._role_scanned(_selection(["support"], mode="competitive"))
        self.assertEqual(self.panel.controls.mode, "competitive")

    def controls_mode(self, mode):
        self.panel.controls.mode = mode


if __name__ == "__main__":
    unittest.main()
