"""The screen-reading half, tested without a screen.

Nothing here opens a window, captures a display or touches the mouse — these run on the
Mac this project is developed on, where the vision extras aren't even installed. What can
be tested that way is the part worth testing anyway: the matcher against images built to
have a known answer, the empty-slot test against synthetic patches, and the rules for
deciding whose pick is whose, which is the piece most likely to quietly credit a
teammate's hero to the player.
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "server"))

from owqserver import protocol, vision                            # noqa: E402
from owqserver.catalog import Catalog                             # noqa: E402
from owqserver.controls import Controls                           # noqa: E402
from owqserver.pairing import Pairing                             # noqa: E402
from owqserver.queueserver import QueueServer                     # noqa: E402

CATALOG = Catalog()


def _pick(slot, hero_key, score=0.8, is_self=False):
    return vision.SlotPick(slot, hero_key, score, is_self)


class SelfSlotTests(unittest.TestCase):
    """Which slot is the player's own.

    The player is not reliably slot one — role queue orders the slots by role — so the
    interesting cases are all the ones where guessing by position would be wrong.
    """

    def test_the_players_slot_is_found_by_hero_not_by_position(self):
        picks = vision.resolve_self_slot(
            [_pick(1, "reinhardt"), _pick(2, "ana"), _pick(3, "tracer")], "ana")
        self.assertEqual([p.slot for p in picks if p.is_self], [2])

    def test_no_slot_is_flagged_when_the_hero_is_unknown(self):
        picks = vision.resolve_self_slot([_pick(1, "reinhardt"), _pick(2, "ana")], None)
        self.assertEqual([p for p in picks if p.is_self], [])

    def test_nothing_is_flagged_when_two_players_share_a_hero(self):
        """Duplicates are legal outside role queue, and an ambiguous answer here would
        credit someone else's pick to the player. Better to flag nothing."""
        picks = vision.resolve_self_slot([_pick(1, "ana"), _pick(2, "ana")], "ana")
        self.assertEqual([p for p in picks if p.is_self], [])

    def test_slot_one_is_not_assumed(self):
        picks = vision.resolve_self_slot([_pick(1, "reinhardt"), _pick(2, "ana")], "ana")
        self.assertFalse(picks[0].is_self)


class TakenHeroTests(unittest.TestCase):
    def test_taken_excludes_the_players_own_pick(self):
        result = vision.ScanResult({}, vision.resolve_self_slot(
            [_pick(1, "reinhardt"), _pick(2, "ana"), _pick(3, "tracer")], "ana"))
        self.assertEqual(sorted(result.taken_hero_keys("ana")), ["reinhardt", "tracer"])

    def test_taken_falls_back_to_the_known_hero_when_no_slot_was_flagged(self):
        """Nothing flagged is the ambiguous case; the server still knows what it picked,
        and that's a better answer than listing our own hero as taken by a teammate."""
        result = vision.ScanResult({}, [_pick(1, "reinhardt"), _pick(2, "ana")])
        self.assertEqual(result.taken_hero_keys("ana"), ["reinhardt"])

    def test_available_keys_are_whatever_the_grid_matched(self):
        roster = {"ana": vision.RosterHit("ana", 0.8, 10, 10, 60, 60),
                  "tracer": vision.RosterHit("tracer", 0.9, 80, 10, 60, 60)}
        self.assertEqual(vision.ScanResult(roster, []).available_hero_keys,
                         ["ana", "tracer"])


class RegionTests(unittest.TestCase):
    """The regions are fractions of the monitor, so they have to survive a resolution
    change rather than only being right at the one they were calibrated on."""

    class _Shape:
        def __init__(self, h, w):
            self.shape = (h, w)

    def test_the_grid_strip_sits_inside_the_screen_at_any_resolution(self):
        for width, height in ((2560, 1440), (1920, 1080), (3840, 2160)):
            x0, y0, x1, y1 = vision.grid_region(self._Shape(height, width))
            self.assertTrue(0 <= x0 < x1 <= width)
            self.assertTrue(0 <= y0 < y1 <= height)

    def test_slots_march_left_to_right_without_overlapping(self):
        screen = self._Shape(1440, 2560)
        boxes = [vision.slot_region(i, screen) for i in range(vision.SLOT_COUNT)]
        for earlier, later in zip(boxes, boxes[1:]):
            self.assertLess(earlier[2], later[0], "slots should not overlap")


@unittest.skipUnless(vision.VISION_AVAILABLE, "vision extras aren't installed")
class MatcherTests(unittest.TestCase):
    """The matcher itself, against images with a known right answer."""

    def _canvas_with(self, patch, at):
        import numpy as np
        canvas = np.zeros((400, 600), dtype=np.uint8)
        canvas[at[1]:at[1] + patch.shape[0], at[0]:at[0] + patch.shape[1]] = patch
        return canvas

    def _template(self, size=64, seed=0):
        import numpy as np
        rng = np.random.default_rng(seed)
        return rng.integers(0, 255, (size, size), dtype=np.uint8)

    def test_it_finds_a_template_where_it_was_put(self):
        template = self._template()
        # Templates are matched at a fraction of their own size, so the thing planted on
        # the canvas has to be that same fraction for the search to have a hope.
        import cv2
        scaled = cv2.resize(template, (int(64 * vision.SCALES[2]),) * 2,
                            interpolation=cv2.INTER_AREA)
        canvas = self._canvas_with(scaled, (300, 200))
        found = vision.best_match(canvas, template)
        self.assertIsNotNone(found)
        score, x, y, _, _ = found
        self.assertGreater(score, 0.7)
        self.assertLess(abs(x - 300), 12)
        self.assertLess(abs(y - 200), 12)

    def test_an_empty_slot_reads_as_flat_and_a_portrait_does_not(self):
        import numpy as np
        flat = np.full((80, 80), 90, dtype=np.uint8)
        self.assertLess(float(flat.std()), vision.EMPTY_STD_THRESHOLD)
        self.assertGreater(float(self._template(80, seed=3).std()),
                           vision.EMPTY_STD_THRESHOLD)


class ClickConfidenceTests(unittest.TestCase):
    """Acting on a match is held to a higher bar than listing one.

    The bug this guards: a hero the highlight is sitting on matches badly, and clicking
    the best of a bad set of guesses puts the player on someone else's hero.
    """

    def test_clicking_needs_more_confidence_than_listing(self):
        self.assertGreater(vision.CLICK_MIN_SCORE, vision.GRID_MIN_SCORE)

    def test_a_hero_good_enough_to_list_can_still_be_too_weak_to_click(self):
        """0.62 is the shape of the real failure — above the listing floor, and measured
        on a live roster as the sort of score a wrong icon produces."""
        weak = vision.RosterHit("sierra", 0.62, 10, 10, 60, 60)
        self.assertGreaterEqual(weak.score, vision.GRID_MIN_SCORE)
        self.assertLess(weak.score, vision.CLICK_MIN_SCORE)


@unittest.skipUnless(vision.VISION_AVAILABLE, "vision extras aren't installed")
class SlotIdentityTests(unittest.TestCase):
    """A slot names a hero only when one wins it clearly.

    The bug this guards: a slot showing Wuyang read as Junker Queen at 0.62, with
    Brigitte 0.04 behind and the right answer nowhere near the top. Score alone accepted
    it; the gap is what gives it away.
    """

    class _Store:
        """Templates that are whatever the test says they are."""

        def __init__(self, templates):
            self._templates = templates

        def template(self, key):
            return self._templates.get(key)

    # Catalog portraits are 256 square, and `best_match` shrinks them by SLOT_SCALES
    # before searching, so a fixture only behaves like the real thing at that size.
    TEMPLATE_SIZE = 256

    def _noise(self, seed, size=TEMPLATE_SIZE, cells=8):
        """A portrait stand-in built from big blocks rather than per-pixel noise.

        Slot matching shrinks the template before comparing, and fine noise simply does
        not survive that — a template scored 0.04 against a resized copy of itself, and
        the averaging flattened the patch below the empty-slot variance test too. Coarse
        blocks behave the way real art does under the same resize.
        """
        import cv2
        import numpy as np
        blocks = np.random.default_rng(seed).integers(0, 255, (cells, cells), dtype=np.uint8)
        return cv2.resize(blocks, (size, size), interpolation=cv2.INTER_NEAREST)

    def _screen_with(self, template):
        """A full-size screen showing `template` in the first slot, sized the way a real
        slot portrait is relative to its catalog art."""
        import cv2
        import numpy as np
        screen = np.zeros((1440, 2560), dtype=np.uint8)
        x0, y0, x1, y1 = vision.slot_region(0, screen)
        side = min(x1 - x0, y1 - y0)
        drawn = cv2.resize(template, (side, side), interpolation=cv2.INTER_AREA)
        screen[y0:y0 + side, x0:x0 + side] = drawn
        return screen

    def test_a_clear_winner_is_reported(self):
        hero = self._noise(seed=1)
        screen = self._screen_with(hero)
        store = self._Store({"tracer": hero, "bastion": self._noise(seed=2)})
        picks = vision.scan_player_slots(screen, store, ["tracer", "bastion"])
        self.assertEqual([(p.slot, p.hero_key) for p in picks], [(1, "tracer")])

    def test_a_near_tie_is_reported_as_empty(self):
        """Two templates that score alike on the same patch is the shape of nothing
        matching. Naming either one would be a guess."""
        hero = self._noise(seed=3)
        store = self._Store({"a": hero, "b": hero})       # identical: a dead heat
        picks = vision.scan_player_slots(self._screen_with(hero), store, ["a", "b"])
        self.assertEqual(picks, [])

    def test_the_margin_is_wide_enough_to_matter(self):
        """0.04 was the measured gap on every wrong reading; the threshold has to sit
        above it, and below the 0.235 a correct one produced."""
        self.assertGreater(vision.SLOT_MARGIN, 0.04)
        self.assertLess(vision.SLOT_MARGIN, 0.235)


class WireFormatTests(unittest.TestCase):
    def test_a_scan_free_phase_carries_neither_new_field(self):
        """Absent, not empty. Empty would tell the app the roster is genuinely empty and
        nobody has picked; absent tells it nobody looked, which is the truth on a Mac or
        with the game closed."""
        data = protocol.hero_select("competitive", "tank")["data"]
        self.assertNotIn("availableHeroKeys", data)
        self.assertNotIn("teamPicks", data)

    def test_a_scanned_phase_carries_both(self):
        data = protocol.hero_select(
            "competitive", "tank", available=["ana", "tracer"],
            team_picks=[{"slot": 2, "heroKey": "ana", "isSelf": True}])["data"]
        self.assertEqual(data["availableHeroKeys"], ["ana", "tracer"])
        self.assertEqual(data["teamPicks"][0]["slot"], 2)
        self.assertTrue(data["teamPicks"][0]["isSelf"])


class VisionOffTests(unittest.TestCase):
    """With vision off — the default, and the only state on a Mac — nothing reaches for
    the screen and hero select behaves exactly as it did before any of this."""

    def setUp(self):
        self.server = QueueServer(Pairing(token="t", port=0), CATALOG)
        self.controls = Controls(self.server)

    def test_vision_is_off_unless_asked_for(self):
        self.assertFalse(self.server.vision_enabled)

    def test_scanning_returns_nothing_rather_than_reaching_for_the_screen(self):
        self.assertIsNone(self.server.scan_hero_select())

    def test_hero_select_still_builds_from_the_catalog(self):
        self.controls.mode, self.controls.role = "competitive", "tank"
        data = self.controls.hero_select_phase()["data"]
        tanks = {hero["key"] for hero in CATALOG.heroes_for("tank", "competitive")}
        self.assertTrue(set(data["takenHeroKeys"]) <= tanks)
        self.assertNotIn("availableHeroKeys", data)

    def test_asking_for_vision_without_the_extras_still_leaves_it_off(self):
        server = QueueServer(Pairing(token="t", port=0), CATALOG, vision_enabled=True)
        self.assertEqual(server.vision_enabled, vision.VISION_AVAILABLE)


if __name__ == "__main__":
    unittest.main()
