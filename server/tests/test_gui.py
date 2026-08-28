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

from owqserver import protocol                                   # noqa: E402
from owqserver.catalog import Catalog                            # noqa: E402
from owqserver.pairing import Pairing                            # noqa: E402
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
        self.server = QueueServer(pairing, Catalog(), push_tokens=PushTokens(os.devnull))
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


if __name__ == "__main__":
    unittest.main()
