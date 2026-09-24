"""When the pairing code is up. Only then does the PC ask the worker who's paired, so it hides
itself after a few minutes rather than asking for as long as the PC is left open."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import QR_TIMEOUT_SECONDS, QrCode  # noqa: E402


class QrCodeTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.qr = QrCode(clock=lambda: self.now)

    def wait(self, seconds):
        self.now += seconds

    def test_it_is_up_while_nothing_is_paired(self):
        self.assertTrue(self.qr.update([]))
        self.wait(QR_TIMEOUT_SECONDS - 1)
        self.assertTrue(self.qr.update([]))

    def test_it_hides_itself_after_five_minutes(self):
        self.qr.update([])
        self.wait(QR_TIMEOUT_SECONDS)
        self.assertFalse(self.qr.update([]))
        self.wait(3600)
        self.assertFalse(self.qr.update([]))

    def test_show_brings_it_back_for_another_five_minutes(self):
        self.qr.update([])
        self.wait(QR_TIMEOUT_SECONDS)
        self.qr.update([])
        self.qr.toggle([])
        self.assertTrue(self.qr.update([]))
        self.wait(QR_TIMEOUT_SECONDS - 1)
        self.assertTrue(self.qr.update([]))
        self.wait(1)
        self.assertFalse(self.qr.update([]))

    def test_a_new_code_starts_the_five_minutes_again(self):
        self.qr.update([])
        self.wait(QR_TIMEOUT_SECONDS - 1)
        self.qr.update([])
        self.qr.restart()
        self.wait(2)
        self.assertTrue(self.qr.update([]))

    def test_it_goes_once_a_phone_pairs(self):
        self.qr.update([])
        self.assertFalse(self.qr.update(["iPhone"]))

    def test_showing_it_to_add_a_phone_times_out_too(self):
        self.assertFalse(self.qr.update(["iPhone"]))
        self.qr.toggle(["iPhone"])
        self.assertTrue(self.qr.update(["iPhone"]))
        self.wait(QR_TIMEOUT_SECONDS)
        self.assertFalse(self.qr.update(["iPhone"]))
        # Hidden like any paired PC, with "Show QR code" to bring it back.
        self.assertFalse(self.qr.update(["iPhone"]))
        self.qr.toggle(["iPhone"])
        self.assertTrue(self.qr.update(["iPhone"]))

    def test_the_code_shown_to_add_a_phone_goes_once_it_is_added(self):
        self.qr.update(["iPhone"])
        self.qr.toggle(["iPhone"])
        self.qr.update(["iPhone"])
        self.assertFalse(self.qr.update(["iPhone", "Android"]))

    def test_hide_hides_it(self):
        self.qr.update(["iPhone"])
        self.qr.toggle(["iPhone"])
        self.qr.update(["iPhone"])
        self.qr.toggle(["iPhone"])
        self.assertFalse(self.qr.update(["iPhone"]))


if __name__ == "__main__":
    unittest.main()
