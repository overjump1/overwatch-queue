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

from owqserver import protocol, queuevision, queuewatch                       # noqa: E402
from owqserver.catalog import Catalog                                        # noqa: E402
from owqserver.pairing import Pairing                                        # noqa: E402
from owqserver.activitytokens import ActivityTokens                          # noqa: E402
from owqserver.pushtokens import PushTokens                                  # noqa: E402
from owqserver.queueserver import QueueServer                                # noqa: E402

try:
    from PyQt6.QtWidgets import QApplication
    from owqserver.gui import ControlPanel
    APP = QApplication.instance() or QApplication([])
except ImportError:                                              # pragma: no cover
    APP = None


def _server(port):
    pairing = Pairing(token="3f2504e0-4f89-41d3-9a0c-0305e82c3301",
                      port=port, path=os.devnull)
    return QueueServer(pairing, Catalog(), push_tokens=PushTokens(os.devnull),
                       activity_tokens=ActivityTokens(os.devnull))


@unittest.skipIf(APP is None, "PyQt6 isn't installed")
class PanelTests(unittest.TestCase):
    def setUp(self):
        self.server = _server(8902)
        self.panel = ControlPanel(self.server)     # no start(): no socket needed here

    def tearDown(self):
        self.panel.close()

    # ------------------------------------------------------------ pairing

    def test_the_qr_carries_the_pairing_url(self):
        self.assertIn("token=3f2504e0-4f89-41d3-9a0c-0305e82c3301", self.panel._pair_url())
        self.assertGreater(self.panel.qr_label.pixmap().width(), 150)

    def test_a_new_token_redraws_the_code(self):
        before = self.panel.qr_label.pixmap().toImage()
        self.panel.pairing.regenerate()
        self.panel._refresh_qr()
        self.assertNotEqual(before, self.panel.qr_label.pixmap().toImage())

    # ------------------------------------------------------------ pages

    def test_the_pair_page_shows_first(self):
        self.assertEqual(self.panel.stack.currentIndex(), 0)

    def test_a_connected_device_switches_to_the_status_page(self):
        _pair_a_device(self.server, "Tomer's iPhone")
        self.panel._refresh()
        self.assertEqual(self.panel.stack.currentIndex(), 1)

    def test_pair_another_device_goes_back_to_the_qr_page(self):
        _pair_a_device(self.server, "Tomer's iPhone")
        self.panel._refresh()
        self.panel._show_pair_page()
        self.assertEqual(self.panel.stack.currentIndex(), 0)

    def test_a_phone_that_locks_its_screen_does_not_put_the_code_back_up(self):
        """The phone drops off MQTT seconds after the screen locks — which is exactly when
        the Live Activity is the only thing still showing the queue."""
        _pair_a_device(self.server, "Tomer's iPhone")
        self.panel._refresh()
        self.server.mqtt._clients.clear()
        self.panel._refresh()
        self.assertEqual(self.panel.stack.currentIndex(), 1)

    def test_a_phone_that_registered_for_pushes_is_paired_across_a_restart(self):
        self.server.push_tokens.register("phone", "device-token", "sandbox")
        panel = ControlPanel(self.server)
        try:
            self.assertEqual(panel.stack.currentIndex(), 1)
        finally:
            panel.close()

    # ------------------------------------------------------------ delivery

    def test_it_says_when_pushing_is_not_set_up(self):
        # Forced rather than assumed: `QueueServer._make_push_client` reads real config
        # out of `~/.overwatch-queue/`, so this would otherwise pass or fail depending
        # on whether the machine running the test happens to have push set up at all.
        self.server.apns = None
        self.panel._refresh()
        self.assertIn("Pushing isn't set up", self.panel.delivery.text())

    def test_a_connected_phone_is_named_as_driving_its_own_activity(self):
        self.server.apns = _PushConfigured()
        _pair_a_device(self.server, "Tomer's iPhone")
        self.panel._refresh()
        self.assertIn("driving its own", self.panel.delivery.text())

    def test_an_absent_phone_is_named_as_being_pushed_to(self):
        self.server.apns = _PushConfigured()
        self.panel._refresh()
        self.assertIn("pushing to its Live Activity", self.panel.delivery.text())

    # ------------------------------------------------------------ state

    def test_the_phase_headline_follows_the_session(self):
        self.server.apply(protocol.searching("competitive", "tank", protocol.now(), 240))
        self.panel._refresh()
        self.assertEqual(self.panel.phase_label.text(), "Searching")

    def test_the_devices_list_names_who_is_paired(self):
        _pair_a_device(self.server, "Tomer's iPhone")
        self.panel._refresh()
        items = [self.panel.devices.item(i).text() for i in range(self.panel.devices.count())]
        self.assertTrue(any("Tomer's iPhone" in item for item in items))

    # ------------------------------------------------------------ manual override

    def test_setting_idle_resets_the_session(self):
        self.server.apply(protocol.searching("competitive", "tank", protocol.now(), 240))
        self.panel.phase_picker.setCurrentIndex(0)     # idle
        self.panel._set_phase()
        self.assertEqual(self.server.session.kind, "idle")

    def test_setting_searching_starts_a_queue(self):
        self.panel.phase_picker.setCurrentIndex(1)     # searching
        self.panel._set_phase()
        self.assertEqual(self.server.session.kind, "searching")

    def test_setting_a_jump_phase_applies_it(self):
        self.server.apply(protocol.searching("competitive", "tank", protocol.now(), 240))
        index = self.panel.phase_picker.findText("Match found")
        self.panel.phase_picker.setCurrentIndex(index)
        self.panel._set_phase()
        self.assertEqual(self.server.session.kind, "matchFound")

    def test_it_renders(self):
        self.panel.show()
        APP.processEvents()
        picture = self.panel.grab()
        from owqserver.gui import WINDOW_SIZE
        self.assertEqual((picture.width(), picture.height()), WINDOW_SIZE)


@unittest.skipIf(APP is None, "PyQt6 isn't installed")
class BattlenetRelaunchPanelTests(unittest.TestCase):
    """The manual "it's open but not working" button, and the once-per-launch shortcut
    check — both just hand off to `bnetpresence`, faked out here the same way
    `tests/test_bnetpresence.py` fakes its own edges."""

    def setUp(self):
        from PyQt6.QtWidgets import QMessageBox
        from owqserver import bnetpresence
        self._question = QMessageBox.question
        self._ensure_shortcut = bnetpresence.ensure_shortcut_targets_debug_port
        self._battlenet_running = bnetpresence.battlenet_running
        # The panel fires its own startup shortcut-check thread the moment presence is
        # on (see `_ensure_battlenet_shortcut`) — stubbed to safe no-ops before that
        # thread can exist, so building the panel below never touches the real Windows
        # shortcut or a real Battle.net, no matter how the thread's timing lands.
        bnetpresence.ensure_shortcut_targets_debug_port = lambda port=None, log=None: False
        bnetpresence.battlenet_running = lambda: False

        self.server = _server(8905)
        self.server.presence_enabled = True     # as if presence were actually on
        self.panel = ControlPanel(self.server)

    def tearDown(self):
        from PyQt6.QtWidgets import QMessageBox
        from owqserver import bnetpresence
        QMessageBox.question = self._question
        bnetpresence.ensure_shortcut_targets_debug_port = self._ensure_shortcut
        bnetpresence.battlenet_running = self._battlenet_running
        self.panel.close()

    def test_the_button_is_hidden_when_presence_is_off(self):
        # `isHidden()`, not `isVisible()`: the panel is never `.show()`n in these tests,
        # which makes every widget report as not visible regardless of its own explicit
        # `setVisible` — `isHidden()` reflects that call directly.
        server = _server(8906)                  # presence_enabled defaults to False
        panel = ControlPanel(server)
        try:
            self.assertTrue(panel.relaunch_battlenet.isHidden())
        finally:
            panel.close()

    def test_the_button_is_shown_when_presence_is_on(self):
        self.assertFalse(self.panel.relaunch_battlenet.isHidden())

    def test_declining_the_confirmation_does_nothing(self):
        from PyQt6.QtWidgets import QMessageBox
        calls = []
        self.server.relaunch_battlenet = lambda: calls.append(1)
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.No)
        self.panel._relaunch_battlenet()
        self.assertEqual(calls, [])

    def test_confirming_calls_the_server_and_re_enables_the_button(self):
        from PyQt6.QtWidgets import QMessageBox
        calls = []

        def fake_relaunch():
            calls.append(1)
            return True

        self.server.relaunch_battlenet = fake_relaunch
        QMessageBox.question = staticmethod(
            lambda *a, **k: QMessageBox.StandardButton.Yes)
        self.panel._relaunch_battlenet()
        self.assertTrue(self._wait_for(lambda: calls == [1]))
        APP.processEvents()
        self.assertTrue(self.panel.relaunch_battlenet.isEnabled())

    @staticmethod
    def _wait_for(condition, timeout=2.0):
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return True
            APP.processEvents()
            time.sleep(0.01)
        return condition()

    def test_startup_check_relaunches_only_if_the_shortcut_changed_and_it_was_running(self):
        self._run_startup_check(shortcut_changed=True, was_running=True)
        self.assertEqual(self.relaunch_calls, [1])

    def test_startup_check_does_not_relaunch_if_nothing_was_running(self):
        self._run_startup_check(shortcut_changed=True, was_running=False)
        self.assertEqual(self.relaunch_calls, [])

    def test_startup_check_does_not_relaunch_if_the_shortcut_was_already_right(self):
        self._run_startup_check(shortcut_changed=False, was_running=True)
        self.assertEqual(self.relaunch_calls, [])

    def _run_startup_check(self, shortcut_changed: bool, was_running: bool):
        from owqserver import bnetpresence
        original_ensure = bnetpresence.ensure_shortcut_targets_debug_port
        original_running = bnetpresence.battlenet_running
        bnetpresence.ensure_shortcut_targets_debug_port = (
            lambda port=None, log=None: shortcut_changed)
        bnetpresence.battlenet_running = lambda: was_running
        self.relaunch_calls = []
        self.server.relaunch_battlenet = lambda: self.relaunch_calls.append(1)
        try:
            self.panel._ensure_battlenet_shortcut()
        finally:
            bnetpresence.ensure_shortcut_targets_debug_port = original_ensure
            bnetpresence.battlenet_running = original_running


class _PushConfigured:
    """Stands in for a configured push client. The panel only asks whether there is one;
    `QueueServer.stop()` closes it, so it has to be closeable."""

    def close(self):
        pass


def _pair_a_device(server, name, client_id="phone-client-id"):
    from owqserver.mqttclient import Client as MQTTTestClient
    phone = MQTTTestClient(client_id)
    phone.identity = {"kind": "phone", "name": name}
    phone.authorized = True
    server.mqtt._clients[client_id] = phone
    return phone


@unittest.skipIf(APP is None, "PyQt6 isn't installed")
class QueueVisionPanelTests(unittest.TestCase):
    """The line that says what the watcher can see."""

    def setUp(self):
        self.server = _server(8903)
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
class RoleDetectionPanelTests(unittest.TestCase):
    """What the queue watcher's role callback does with whatever the scan comes back
    with — no button to click any more, but the background detection still runs."""

    def setUp(self):
        self.server = _server(8904)
        self.panel = ControlPanel(self.server)

    def tearDown(self):
        self.panel.close()

    def test_a_single_checked_role_is_applied(self):
        self.panel._role_scanned(_selection(["support"]))
        self.assertEqual(self.panel.controls.role, "support")

    def test_more_than_one_checked_role_is_applied_as_flex(self):
        self.panel._role_scanned(_selection(["tank", "damage"]))
        self.assertEqual(self.panel.controls.role, "flex")

    def test_nothing_checked_leaves_the_role_alone(self):
        self.panel.controls.role = "tank"
        self.panel._role_scanned(_selection([]))
        self.assertEqual(self.panel.controls.role, "tank")

    def test_no_screen_to_read_leaves_the_role_alone(self):
        self.panel.controls.role = "tank"
        self.panel._role_scanned(None)
        self.assertEqual(self.panel.controls.role, "tank")

    def test_a_mode_named_by_the_screen_follows_when_the_current_mode_queues_by_role(self):
        self.panel.controls.mode = "quickPlay"
        self.panel._role_scanned(_selection(["support"], mode="competitive"))
        self.assertEqual(self.panel.controls.mode, "competitive")


def _selection(roles, mode=None):
    from owqserver import queueroles
    return queueroles.RoleSelection(frozenset(roles), mode, {}, True)


if __name__ == "__main__":
    unittest.main()
