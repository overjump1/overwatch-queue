"""Reading real maps off the vote screen, tested mostly without the OCR model itself.

The model is a real, heavy, optional dependency — `MAP_VOTE_READING_AVAILABLE` guards it
the same way `queuevision`/`vision` guard `cv2`, and most of what's worth testing here
(the catalog match, the "fewer than two real cards" refusal) has no pixels in it at all.
The one thing that does need real ones — whether the model itself reads the game's actual
UI font correctly — was verified this session against all four map-vote screens in the
project's own recording, twelve cards, twelve correct reads; that verification lives in
this module's own docstring, not repeated here as a slow, model-dependent test.
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "server"))

from owqserver import queuemapvote as qmv                        # noqa: E402
from owqserver.catalog import Catalog                             # noqa: E402


class CatalogMapNameTests(unittest.TestCase):
    def setUp(self):
        self.catalog = Catalog()

    def test_an_exact_reading_matches(self):
        self.assertEqual(self.catalog.key_for_map_name("NEPAL"), "nepal")

    def test_case_and_surrounding_space_do_not_matter(self):
        self.assertEqual(self.catalog.key_for_map_name("  nepal  "), "nepal")

    def test_a_clipped_reading_still_matches(self):
        # The regression a real capture found: one card's crop clipped a trailing
        # letter off "Antarctic Peninsula". Close enough to trust, not identical.
        self.assertEqual(self.catalog.key_for_map_name("ANTARCTIC PENINSUL"),
                         "antarctic-peninsula")

    def test_nothing_close_refuses_rather_than_guesses(self):
        self.assertIsNone(self.catalog.key_for_map_name("COMPLETELY UNRELATED TEXT"))

    def test_empty_text_refuses(self):
        self.assertIsNone(self.catalog.key_for_map_name(""))


class ScanRefusalTests(unittest.TestCase):
    """`scan`'s own "win clearly or say nothing" floor, with `_read_name` stubbed so
    none of this needs the real model installed."""

    def setUp(self):
        self.catalog = Catalog()
        self._real_capture = qmv._capture
        self._real_name_crop = qmv._name_crop
        self._real_read_name = qmv._read_name
        self._real_available = qmv.MAP_VOTE_READING_AVAILABLE
        self.addCleanup(setattr, qmv, "_capture", self._real_capture)
        self.addCleanup(setattr, qmv, "_name_crop", self._real_name_crop)
        self.addCleanup(setattr, qmv, "_read_name", self._real_read_name)
        self.addCleanup(setattr, qmv, "MAP_VOTE_READING_AVAILABLE", self._real_available)
        qmv._capture = lambda: None
        qmv._name_crop = lambda frame, x_range: x_range      # anything - unread below
        qmv.MAP_VOTE_READING_AVAILABLE = True

    def _stub_reads(self, *names):
        it = iter(names)
        qmv._read_name = lambda crop: next(it, "")

    def test_three_real_names_are_all_kept(self):
        self._stub_reads("NEPAL", "CIRCUIT ROYAL", "NEW QUEEN STREET")
        result = qmv.scan(self.catalog)
        self.assertTrue(result.on_screen)
        self.assertEqual(result.map_keys, ["nepal", "circuit-royal", "new-queen-street"])

    def test_two_of_three_cards_reading_is_still_enough(self):
        # A vote offering only two maps looks exactly like this: one empty slot.
        self._stub_reads("NEPAL", "", "SAMOA")
        result = qmv.scan(self.catalog)
        self.assertTrue(result.on_screen)
        self.assertEqual(result.map_keys, ["nepal", "samoa"])

    def test_only_one_card_reading_is_not_enough_to_trust(self):
        self._stub_reads("NEPAL", "", "")
        result = qmv.scan(self.catalog)
        self.assertFalse(result.on_screen)
        self.assertEqual(result.map_keys, [])

    def test_nothing_legible_anywhere_is_not_on_screen(self):
        self._stub_reads("", "", "")
        result = qmv.scan(self.catalog)
        self.assertFalse(result.on_screen)

    def test_the_extra_being_unavailable_refuses_before_capturing_anything(self):
        qmv.MAP_VOTE_READING_AVAILABLE = False
        called = []
        qmv._capture = lambda: called.append(True)
        result = qmv.scan(self.catalog)
        self.assertFalse(result.on_screen)
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
