"""Naming the screen, tested without one.

The same split as the rest of this project's vision tests: the parts with no pixels in
them — collapsing a hash to a Hamming distance, an empty cache answering nothing — run
anywhere, and the parts that need an image build their own rather than reaching for a
real screenshot, because what's under test is the pipeline, not whether a fixture
resembles Overwatch's actual art.
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "server"))

from owqserver import stagevision as sv                           # noqa: E402

HAVE_CV2 = sv.STAGE_VISION_AVAILABLE
SCREEN = (1920, 1080)

if HAVE_CV2:
    import cv2
    import numpy as np


# ---------------------------------------------------------------- image fixtures

def scenery(seed=0):
    """A textured, unsaturated full screen — ordinary gameplay, not a menu."""
    rng = np.random.default_rng(seed)
    noise = rng.integers(20, 90, size=(SCREEN[1], SCREEN[0], 3), dtype=np.uint8)
    return cv2.GaussianBlur(noise, (0, 0), 3)


def _amber_bar(strip, box):
    x, y, w, h = box
    patch = np.zeros((h, w, 3), np.uint8)
    patch[:, :] = (int(17 / 2), 200, 230)          # hue 17: inside AMBER_HUE_RANGE
    strip[y:y + h, x:x + w] = cv2.cvtColor(patch, cv2.COLOR_HSV2BGR)
    return strip


def map_vote_screen(strip, outer=120):
    """Both amber beams flanking "VOTE FOR A MAP", at their real measured position —
    only the *inner* ends `looks_like_map_vote` actually reads, per its own docstring, so
    `outer` (how far the fixture's beam extends *beyond* that) is free to vary without
    changing whether this is recognised, the same freedom the vote count's own width
    gives the real beams on a real screen."""
    left = sv.queuevision._fractional_box(sv.MAP_VOTE_LEFT_REGION, *SCREEN)
    right = sv.queuevision._fractional_box(sv.MAP_VOTE_RIGHT_REGION, *SCREEN)
    lx, ly, lw, lh = left
    rx, ry, rw, rh = right
    _amber_bar(strip, (lx - outer, ly, lw + outer, lh))
    _amber_bar(strip, (rx, ry, rw + outer, rh))
    return strip


def prepare_screen(strip, seconds=32):
    """A small cluster of bright, amber, digit-sized marks where the countdown next to
    "Prepare to Attack" always sits — not a real `M:SS` render, since `looks_like_prepare`
    only asks whether marks are there, not what they say."""
    x, y, w, h = sv.queuevision._fractional_box(sv.PREPARE_TIMER_REGION, *SCREEN)
    text = "%d:%02d" % (seconds // 60, seconds % 60)
    cv2.putText(strip, text, (x, y + h - 2), cv2.FONT_HERSHEY_DUPLEX, h / 26.0,
               (40, 140, 230), max(1, int(h / 16)), cv2.LINE_AA)
    return strip


# ---------------------------------------------------------------- map vote

@unittest.skipUnless(HAVE_CV2, "vision extras aren't installed")
class MapVoteTests(unittest.TestCase):
    def test_ordinary_scenery_is_not_the_vote_screen(self):
        self.assertFalse(sv.looks_like_map_vote(scenery(1)))

    def test_both_beams_together_are_the_vote_screen(self):
        self.assertTrue(sv.looks_like_map_vote(map_vote_screen(scenery(2))))

    def test_a_wider_count_still_reads_as_the_vote_screen(self):
        """A bigger vote count pushes each beam's outer end further out — only the inner
        end, which these fixtures share regardless of `outer`, is ever read."""
        self.assertTrue(sv.looks_like_map_vote(map_vote_screen(scenery(3), outer=300)))

    def test_only_one_beam_is_not_the_vote_screen(self):
        """A single warm-coloured object sitting in one of the two positions is exactly
        the coincidence a lone fixed crop can't rule out — both have to show."""
        strip = scenery(4)
        left = sv.queuevision._fractional_box(sv.MAP_VOTE_LEFT_REGION, *SCREEN)
        lx, ly, lw, lh = left
        _amber_bar(strip, (lx - 120, ly, lw + 120, lh))
        self.assertFalse(sv.looks_like_map_vote(strip))

    def test_the_beams_own_row_has_to_be_amber_not_merely_bright(self):
        """A white or blue bar in the same place is not this ornament — only its
        particular hue is."""
        strip = scenery(5)
        left = sv.queuevision._fractional_box(sv.MAP_VOTE_LEFT_REGION, *SCREEN)
        right = sv.queuevision._fractional_box(sv.MAP_VOTE_RIGHT_REGION, *SCREEN)
        for x, y, w, h in (left, right):
            patch = np.zeros((h, w, 3), np.uint8)
            patch[:, :] = (int(212 / 2), 200, 230)      # blue, not amber
            strip[y:y + h, x:x + w] = cv2.cvtColor(patch, cv2.COLOR_HSV2BGR)
        self.assertFalse(sv.looks_like_map_vote(strip))

    def test_a_whole_screen_lit_amber_is_not_the_vote_screen(self):
        """The regression a full-video sweep found: an orange VFX flash and a gold-toned
        room each lit up both beam regions at once, symmetry included, because the
        *entire screen* was that colour rather than two isolated beams — exactly the
        coincidence the two-beam requirement was meant to rule out, defeated by the
        coincidence being screen-wide instead of one-sided. What a beam has that a colour
        wash doesn't is edges: the strip right next to it is plain and dark on a real
        vote screen, and just as amber as the "beam" on this fixture."""
        patch = np.zeros((SCREEN[1], SCREEN[0], 3), np.uint8)
        patch[:, :] = (int(17 / 2), 200, 230)
        strip = cv2.cvtColor(patch, cv2.COLOR_HSV2BGR)
        self.assertFalse(sv.looks_like_map_vote(strip))


# ---------------------------------------------------------------- prepare to attack

@unittest.skipUnless(HAVE_CV2, "vision extras aren't installed")
class PrepareTests(unittest.TestCase):
    def test_ordinary_scenery_is_not_the_prepare_banner(self):
        self.assertFalse(sv.looks_like_prepare(scenery(1)))

    def test_a_countdown_in_its_place_is_the_prepare_banner(self):
        self.assertTrue(sv.looks_like_prepare(prepare_screen(scenery(2))))

    def test_a_different_time_remaining_is_still_the_prepare_banner(self):
        """The banner's own position never moves, whatever the label or the time left —
        two real captures at 0:32 and 0:40 agreed on it within a few pixels."""
        self.assertTrue(sv.looks_like_prepare(prepare_screen(scenery(3), seconds=59)))

    def test_a_single_bright_mark_is_not_a_timer(self):
        """One glyph is not `M:SS` — at least a units digit, a tens digit and a colon
        have to be there, the same floor `queuedigits.segment` sets for the queue's own
        timer."""
        strip = scenery(4)
        x, y, w, h = sv.queuevision._fractional_box(sv.PREPARE_TIMER_REGION, *SCREEN)
        cv2.putText(strip, "7", (x, y + h - 2), cv2.FONT_HERSHEY_DUPLEX, h / 26.0,
                   (40, 140, 230), max(1, int(h / 16)), cv2.LINE_AA)
        self.assertFalse(sv.looks_like_prepare(strip))

    def test_the_running_match_clock_is_not_the_prepare_banner(self):
        """The regression this module exists to not repeat. A full-video sweep found
        this check firing for an entire real match, not just the pre-match countdown,
        because the ongoing match clock sits in the identical spot once a round starts —
        `PREPARE_CLUTTER_REGION`'s own comment has the measured numbers. What tells them
        apart is what's drawn just underneath: nothing yet, before the match starts: an
        objective tracker, a fixed once it has. This fixture is that second case —
        the same countdown, plus a busy, saturated bar where the tracker goes."""
        strip = prepare_screen(scenery(5))
        x, y, w, h = sv.queuevision._fractional_box(sv.PREPARE_CLUTTER_REGION, *SCREEN)
        rng = np.random.default_rng(6)
        strip[y:y + h, x:x + w] = rng.integers(0, 255, size=(h, w, 3), dtype=np.uint8)
        self.assertFalse(sv.looks_like_prepare(strip))

    def test_several_minutes_remaining_is_not_the_prepare_banner_either(self):
        """A second, later-found regression: the same fixed box is the *live match
        clock* for the whole rest of the round, in the same amber, and a quiet moment
        under an uncontested objective can slip the clutter check above on its own — a
        full-video sweep found this happen for real. What a real pre-match countdown
        never shows and a several-minutes-remaining match clock always does is a
        non-zero leading digit; this fixture is "4:34", not "0:34"."""
        self.assertFalse(sv.looks_like_prepare(prepare_screen(scenery(7), seconds=274)))

    def test_a_label_bleeding_in_from_the_left_is_not_mistaken_for_the_minutes_digit(self):
        """A second real regression from the same sweep: on one real capture, a
        neighbouring label's own text bled into this box's fixed position from the left
        edge, close enough to count as a digit-height mark, while the real minutes digit
        wasn't in the crop at all — only the bleed and the two real seconds digits were.
        Treating whatever sits leftmost as the minutes digit read that bleed as if it
        were the missing zero, 137px from the seconds beside it against 20-63px for
        every real minutes digit measured through this path. This fixture is that exact
        shape: a bright mark hugging the crop's own left edge, then two digit-sized marks
        on the right and nothing real in between."""
        strip = scenery(8)
        x, y, w, h = sv.queuevision._fractional_box(sv.PREPARE_TIMER_REGION, *SCREEN)
        color = (40, 140, 230)
        cv2.rectangle(strip, (x, y + 2), (x + 8, y + h - 2), color, -1)
        cv2.rectangle(strip, (x + 70, y + 2), (x + 80, y + h - 2), color, -1)
        cv2.rectangle(strip, (x + 90, y + 2), (x + 100, y + h - 2), color, -1)
        self.assertFalse(sv.looks_like_prepare(strip))

    def test_a_white_countdown_in_the_same_spot_is_not_the_prepare_banner(self):
        """A third real regression: an unrelated white countdown ("Locks In") was found
        sharing this exact fixed position, bright and holed exactly like a real "0"
        wherever it happened to read one - value and shape both agreeing with a real
        countdown, which is what makes colour the only thing left to ask. Measured on
        five real captures of it, saturation was 4-13 against 178-198 for four real
        amber readings through the same path."""
        strip = scenery(9)
        x, y, w, h = sv.queuevision._fractional_box(sv.PREPARE_TIMER_REGION, *SCREEN)
        cv2.putText(strip, "0:30", (x, y + h - 2), cv2.FONT_HERSHEY_DUPLEX, h / 26.0,
                   (255, 255, 255), max(1, int(h / 16)), cv2.LINE_AA)
        self.assertFalse(sv.looks_like_prepare(strip))


# ---------------------------------------------------------------- fingerprinting

@unittest.skipUnless(HAVE_CV2, "vision extras aren't installed")
class FingerprintTests(unittest.TestCase):
    def test_the_same_frame_hashes_identically(self):
        frame = map_vote_screen(scenery(1))
        self.assertEqual(sv.fingerprint(frame), sv.fingerprint(frame.copy()))

    def test_a_soft_change_stays_within_tolerance(self):
        """The kind of noise an idle menu has — a small patch a little brighter — has to
        still count as "the same screen", or the cache this exists for would never hit
        one for longer than a single frame."""
        frame = map_vote_screen(scenery(2))
        nudged = frame.copy()
        nudged[100:120, 100:120] = np.clip(
            nudged[100:120, 100:120].astype(int) + 30, 0, 255).astype(np.uint8)
        self.assertLessEqual(
            sv._hamming(sv.fingerprint(frame), sv.fingerprint(nudged)),
            sv.FINGERPRINT_HAMMING_TOLERANCE)

    def test_a_different_screen_is_a_different_hash(self):
        """Two different noise fields stand in for two different game scenes — a
        different map, a different camera angle — which is the change this hash exists
        to catch. A small UI ornament added to the *same* scenery is a different, and
        much easier, question: `test_a_soft_change_stays_within_tolerance` already covers
        it landing inside tolerance, which is the correct answer for that one instead."""
        far_apart = sv._hamming(sv.fingerprint(scenery(3)), sv.fingerprint(scenery(4)))
        self.assertGreater(far_apart, sv.FINGERPRINT_HAMMING_TOLERANCE)


class StageCacheTests(unittest.TestCase):
    """No pixels here — the cache is exercised against hand-built hashes, the same way
    `RowShapeTests` exercises `queueroles.looks_like_role_select` against hand-built
    hits."""

    def test_an_empty_cache_answers_nothing(self):
        self.assertIsNone(sv.StageCache().get(12345))

    def test_a_stored_answer_comes_back_for_the_same_hash(self):
        cache = sv.StageCache()
        result = sv.StageResult(sv.HERO_SELECT, "roster")
        cache.put(0b1010, result)
        self.assertIs(cache.get(0b1010), result)

    def test_a_close_hash_still_hits(self):
        cache = sv.StageCache()
        result = sv.StageResult(sv.HERO_SELECT)
        cache.put(0b0000, result)
        close = (1 << 0) | (1 << 1)                # 2 bits different
        self.assertIs(cache.get(close), result)

    def test_a_far_hash_misses(self):
        cache = sv.StageCache()
        cache.put(0, sv.StageResult(sv.HERO_SELECT))
        far = (1 << 10) - 1                        # 10 bits different
        self.assertIsNone(cache.get(far))

    def test_capacity_drops_the_oldest_entry(self):
        # Chosen far enough apart, pairwise, that none is a tolerance-away neighbour of
        # another — the point under test is eviction, not a coincidental near-hash hit.
        first, second, third = 0, 0xFF, 0xFF00
        cache = sv.StageCache(capacity=2)
        cache.put(first, sv.StageResult(sv.HERO_SELECT, "first"))
        cache.put(second, sv.StageResult(sv.HERO_SELECT, "second"))
        cache.put(third, sv.StageResult(sv.HERO_SELECT, "third"))
        self.assertIsNone(cache.get(first))
        self.assertIsNotNone(cache.get(third))


# ---------------------------------------------------------------- the dispatcher

@unittest.skipUnless(HAVE_CV2, "vision extras aren't installed")
class ClassifyTests(unittest.TestCase):
    """The order the checks run in, since that's the part specific to this module — each
    individual check is already covered by its own test class here or in
    `test_queuevision.py` / `test_queueroles.py`."""

    def test_ordinary_scenery_is_unknown(self):
        result = sv.classify(scenery(1))
        self.assertEqual(result.stage, sv.UNKNOWN)
        self.assertFalse(result.recognised)

    def test_the_vote_screen_is_named_over_ordinary_scenery(self):
        result = sv.classify(map_vote_screen(scenery(2)))
        self.assertEqual(result.stage, sv.MAP_VOTE)

    def test_the_prepare_banner_is_named(self):
        result = sv.classify(prepare_screen(scenery(3)))
        self.assertEqual(result.stage, sv.PREPARE)

    def test_without_a_hero_store_the_roster_is_never_tried(self):
        """The expensive check has to be opt-in — passing nothing for it must not raise,
        and must fall through to unknown rather than guess."""
        result = sv.classify(scenery(4))
        self.assertEqual(result.stage, sv.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
