"""Reading the queue off the screen, tested without a screen.

Split the way the modules are, and for the same reason. The geometry, the digit
labelling and the whole state machine are arithmetic with no pixels in them, so they run
anywhere — including the Mac this project is developed on, where the vision extras aren't
installed. Only the tests that genuinely need an image to look at are skipped there, and
those build their own: a flat coloured box on noisy scenery, which is the one thing the
detector has to be able to tell apart.

The state-machine tests are the ones worth reading. They hand `QueueTracker` a made-up
minute in a millisecond, because every interesting case here is about *time* — a gap that
should not end a queue, a gap that should, and the difference between not seeing a banner
and not being able to see the screen at all.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "server"))

from owqserver import protocol, queuedigits, queuevision, queuewatch    # noqa: E402
from owqserver.catalog import Catalog                                   # noqa: E402
from owqserver.controls import Controls                                 # noqa: E402
from owqserver.pairing import Pairing                                   # noqa: E402
from owqserver.queueserver import QueueServer                           # noqa: E402

HAVE_CV2 = queuevision.QUEUE_VISION_AVAILABLE
SCREEN = (2560, 1440)

if HAVE_CV2:
    import cv2
    import numpy as np


# ---------------------------------------------------------------- image fixtures

def scenery(seed=0):
    """Textured, unsaturated background — a spawn room rather than a menu."""
    rng = np.random.default_rng(seed)
    height = queuevision.strip_height(SCREEN[1])
    noise = rng.integers(20, 90, size=(height, SCREEN[0], 3), dtype=np.uint8)
    return cv2.GaussianBlur(noise, (0, 0), 3)


def _fill(strip, box, hue):
    x, y, w, h = box
    patch = np.zeros((h, w, 3), np.uint8)
    patch[:, :] = (int(hue / 2), 200, 230)
    strip[y:y + h, x:x + w] = cv2.cvtColor(patch, cv2.COLOR_HSV2BGR)
    return strip


def _stamp(strip, seconds, at, height):
    """`M:SS` in white, sized to `height`, with its left edge at `at`."""
    scale = height / 26.0
    cv2.putText(strip, "%d:%02d" % (seconds // 60, seconds % 60), at,
                cv2.FONT_HERSHEY_DUPLEX, scale, (255, 255, 255),
                max(1, int(scale * 1.6)), cv2.LINE_AA)
    return strip


def banner(strip, box, hue, seconds=17):
    """The in-game bar: a flat saturated box, its label, and a timer at the right end.

    The timer is not decoration on this fixture. A box without one is no longer a banner
    as far as `find_banner` is concerned — the menu's row of mode tabs taught it that —
    so a fixture that omitted it would be testing a path the detector doesn't have.
    """
    x, y, w, h = box
    _fill(strip, box, hue)
    cv2.rectangle(strip, (x + int(w * 0.16), y + int(h * 0.36)),
                  (x + int(w * 0.60), y + int(h * 0.60)), (255, 255, 255), -1)
    return _stamp(strip, seconds, (x + int(w * 0.79), y + int(h * 0.72)), int(h * 0.42))


def menu_tab(strip, hue, seconds=17):
    """The menu's queue tab, filled and carrying its timer where the real one prints it."""
    box = queuevision._fractional_box(queuevision.MENU_TAB_REGION, *SCREEN)
    _fill(strip, box, hue)
    x0, y0, w, h = queuevision._fractional_box(queuevision.MENU_TIMER_REGION, *SCREEN)
    return _stamp(strip, seconds, (x0 + int(w * 0.10), y0 + int(h * 0.72)), int(h * 0.34))


def idle_tab(strip, hue):
    """The same tab with no timer on it — what the menu shows when nothing is queueing."""
    box = queuevision._fractional_box(queuevision.MENU_TAB_REGION, *SCREEN)
    _fill(strip, box, hue)
    x, y, w, h = box
    cv2.circle(strip, (x + w // 2, y + h // 2), int(h * 0.30), (255, 255, 255), -1)
    return strip


def check_circle(strip, box):
    x, y, _, h = box
    cv2.circle(strip, (x + h // 2, y + h // 2), int(h * 0.32), (60, 200, 60), -1)
    cv2.circle(strip, (x + h // 2, y + h // 2), int(h * 0.11), (255, 255, 255), -1)
    return strip


def timer_crop(seconds, hue=340):
    """The corner of a banner holding `M:SS` in white on flat colour."""
    patch = np.zeros((70, 110, 3), np.uint8)
    patch[:, :] = (int(hue / 2), 200, 230)
    patch = cv2.cvtColor(patch, cv2.COLOR_HSV2BGR)
    cv2.putText(patch, "%d:%02d" % (seconds // 60, seconds % 60), (6, 50),
                cv2.FONT_HERSHEY_DUPLEX, 1.3, (255, 255, 255), 2, cv2.LINE_AA)
    return patch


# Proportions taken from the real capture, scaled to this screen: the bar measured
# 446x76 on 1920x1080, which is 0.23 of the width and 0.070 of the height.
BAR_BOX = (12, 14, 596, 100)


# ---------------------------------------------------------------- geometry

class ShapeTests(unittest.TestCase):
    """Which banner a box is, decided from its proportions and where it sits."""

    def test_a_wide_bar_in_a_corner_is_the_in_game_one(self):
        self.assertEqual(queuevision.classify((12, 14, 600, 72), SCREEN[0]),
                         queuevision.IN_GAME_BAR)

    def test_the_in_game_bar_is_recognised_in_either_corner(self):
        right = (SCREEN[0] - 612, 14, 600, 72)
        self.assertEqual(queuevision.classify(right, SCREEN[0]), queuevision.IN_GAME_BAR)

    def test_a_wide_bar_adrift_in_the_middle_is_not_a_banner(self):
        """The in-game bar is pinned to an edge. A wide flat thing in the centre of the
        screen is some other piece of interface, and guessing costs a false queue."""
        self.assertIsNone(queuevision.classify((900, 14, 600, 72), SCREEN[0]))

    def test_the_bar_still_counts_when_it_stops_short_of_the_edge(self):
        """Measured on a real capture: the right-hand bar left a 136px gap on a 1920-wide
        screen, which is 0.071 of it. The first guess at this allowed 0.06 and missed
        every in-game frame of the queue."""
        box = (SCREEN[0] - 600 - int(SCREEN[0] * 0.071), 14, 600, 100)
        self.assertEqual(queuevision.classify(box, SCREEN[0]), queuevision.IN_GAME_BAR)

    def test_a_slightly_squarer_bar_still_counts(self):
        """The left-hand bar measured 450x82 — aspect 5.49, which the first guess of 5.5
        excluded by a hundredth."""
        self.assertEqual(queuevision.classify((0, 14, 549, 100), SCREEN[0]),
                         queuevision.IN_GAME_BAR)

    def test_a_compact_pill_is_not_classified_here_any_more(self):
        """The menu tab is found by `menu_tab`, at a fixed place, because its shape and
        colour are identical to the idle tab's. Nothing pill-shaped is a banner by
        proportions alone."""
        self.assertIsNone(queuevision.classify((SCREEN[0] // 2 - 120, 10, 240, 104),
                                               SCREEN[0]))

    def test_shapes_between_the_two_are_neither(self):
        self.assertIsNone(queuevision.classify((12, 14, 300, 72), SCREEN[0]))


class ModeHueTests(unittest.TestCase):
    def setUp(self):
        self.hues, self.tolerance = queuevision.load_modes()

    def test_the_shipped_table_names_the_two_measured_modes(self):
        self.assertEqual(sorted(self.hues), ["competitive", "quickPlay"])

    def test_blue_is_quick_play_and_pink_is_competitive(self):
        self.assertEqual(queuevision.match_mode(213.0, self.hues, self.tolerance),
                         "quickPlay")
        self.assertEqual(queuevision.match_mode(338.0, self.hues, self.tolerance),
                         "competitive")

    def test_an_unmeasured_hue_names_no_mode(self):
        """Stadium and Arcade have never been measured. Reporting the nearest of the two
        we do know would put the wrong mode on the watch; None lets the caller keep the
        mode the panel already has."""
        self.assertIsNone(queuevision.match_mode(270.0, self.hues, self.tolerance))

    def test_hues_are_compared_the_short_way_round_the_circle(self):
        self.assertAlmostEqual(queuevision.hue_distance(350.0, 10.0), 20.0)
        self.assertEqual(queuevision.match_mode(352.0, {"competitive": 4.0}, 12.0),
                         "competitive")


# ---------------------------------------------------------------- detection

@unittest.skipUnless(HAVE_CV2, "vision extras aren't installed")
class BannerDetectionTests(unittest.TestCase):
    """The in-game bar, which is the half still found by shape and colour."""

    def test_ordinary_scenery_holds_no_banner(self):
        self.assertIsNone(queuevision.find_banner(scenery(1), *SCREEN))

    def test_the_in_game_bar_is_found_with_its_mode(self):
        hit = queuevision.find_banner(banner(scenery(2), BAR_BOX, 212.0), *SCREEN)
        self.assertIsNotNone(hit)
        self.assertEqual(hit.kind, queuevision.IN_GAME_BAR)
        self.assertEqual(hit.mode, "quickPlay")
        self.assertTrue(hit.searching)

    def test_the_bar_is_found_in_the_right_corner_too(self):
        box = (SCREEN[0] - 596 - 90, 14, 596, 100)
        self.assertIsNotNone(queuevision.find_banner(banner(scenery(3), box, 212.0),
                                                     *SCREEN))

    def test_a_box_with_no_timer_on_it_is_not_a_banner(self):
        """The menu's row of mode tabs is a flat blue box of exactly these proportions
        against the left edge. Before the timer was required it read as an in-game queue
        bar in every single menu frame."""
        strip = _fill(scenery(4), BAR_BOX, 212.0)
        self.assertIsNone(queuevision.find_banner(strip, *SCREEN))

    def test_a_thin_wide_timer_elsewhere_is_not_a_banner(self):
        """A deathmatch draws its own match clock: pink, wide, thin, and with a colon in
        it, so it passes both the shape test and the timer test. It was being reported as
        a Competitive queue right through a Quick Play match. Height is what separates
        them — 0.030 of the screen against 0.054 and up for a real bar."""
        thin = (232, 173, 366, 43)
        strip = _stamp(_fill(scenery(5), thin, 343.0), 155,
                       (232 + 250, 173 + 32), 20)
        self.assertIsNone(queuevision.find_banner(strip, *SCREEN))

    def test_an_unmeasured_colour_is_still_a_banner(self):
        hit = queuevision.find_banner(banner(scenery(6), BAR_BOX, 270.0), *SCREEN)
        self.assertIsNotNone(hit)
        self.assertIsNone(hit.mode)
        self.assertEqual(hit.kind, queuevision.IN_GAME_BAR)

    def test_the_timer_crop_lands_on_the_timer(self):
        strip = banner(scenery(7), BAR_BOX, 212.0, seconds=95)
        hit = queuevision.find_banner(strip, *SCREEN)
        self.assertIsNotNone(queuedigits.segment(hit.timer_patch(strip)))


@unittest.skipUnless(HAVE_CV2, "vision extras aren't installed")
class MenuTabTests(unittest.TestCase):
    """The menu, where shape and colour are no help at all.

    The tab that says SEARCHING and the tab that sits there idle are the same trapezoid
    in the same place in the same blue. Everything here is really one assertion: the
    timer is the only thing that separates them, and it is enough.
    """

    def test_a_queueing_tab_is_found(self):
        hit = queuevision.find_banner(menu_tab(scenery(1), 216.0), *SCREEN)
        self.assertIsNotNone(hit)
        self.assertEqual(hit.kind, queuevision.MENU_PILL)
        self.assertEqual(hit.mode, "quickPlay")
        self.assertTrue(hit.searching)

    def test_the_idle_tab_is_not(self):
        """Identical box, identical colour, no timer. A detector that fires on the menu
        at rest would put every player permanently in a queue."""
        self.assertIsNone(queuevision.find_banner(idle_tab(scenery(2), 216.0), *SCREEN))

    def test_a_green_check_on_the_tab_means_the_game_was_found(self):
        box = queuevision._fractional_box(queuevision.MENU_TAB_REGION, *SCREEN)
        strip = check_circle(menu_tab(scenery(3), 216.0), box)
        hit = queuevision.find_banner(strip, *SCREEN)
        self.assertIsNotNone(hit)
        self.assertTrue(hit.game_found)

    def test_the_match_is_seen_even_though_it_takes_the_timer_away(self):
        """`GAME FOUND!` replaces the timer as well as the icon, so asking about the
        timer first would lose the one frame that matters most."""
        box = queuevision._fractional_box(queuevision.MENU_TAB_REGION, *SCREEN)
        strip = check_circle(_fill(scenery(4), box, 216.0), box)
        hit = queuevision.find_banner(strip, *SCREEN)
        self.assertIsNotNone(hit)
        self.assertTrue(hit.game_found)

    def test_a_check_circle_does_not_stop_the_tab_reading_as_one_colour(self):
        """Why the dominant hue is measured rather than the spread of every pixel: a tab
        that is mostly blue with a green disc on it has to still read as blue."""
        box = queuevision._fractional_box(queuevision.MENU_TAB_REGION, *SCREEN)
        hit = queuevision.find_banner(check_circle(menu_tab(scenery(5), 216.0), box),
                                      *SCREEN)
        self.assertEqual(hit.mode, "quickPlay")
        self.assertGreater(hit.coverage, queuevision.DOMINANT_COVERAGE_MIN)


# ---------------------------------------------------------------- the timer

@unittest.skipUnless(HAVE_CV2, "vision extras aren't installed")
class TimerSegmentationTests(unittest.TestCase):

    def test_a_short_timer_splits_into_one_minute_and_two_second_glyphs(self):
        glyphs = queuedigits.segment(timer_crop(29))
        self.assertIsNotNone(glyphs)
        self.assertEqual((len(glyphs.minutes), len(glyphs.seconds)), (1, 2))

    def test_a_long_timer_keeps_both_minute_glyphs(self):
        glyphs = queuedigits.segment(timer_crop(10 * 60 + 5))
        self.assertEqual((len(glyphs.minutes), len(glyphs.seconds)), (2, 2))

    def test_a_blank_crop_is_not_a_reading(self):
        self.assertIsNone(queuedigits.segment(timer_crop(0)[:, :4]))


@unittest.skipUnless(HAVE_CV2, "vision extras aren't installed")
class DigitLearningTests(unittest.TestCase):
    """The clock teaching this its own digits.

    There is no font here and nothing checked in, so the only thing that can label a
    glyph is the fact that a clock counts. These check that it does, and — more
    importantly — that a broken run of ticks refuses to label anything rather than
    labelling it off by one.
    """

    def setUp(self):
        self.cache = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.cache, ignore_errors=True)

    def _run(self, reader, start, seconds, step=0.5, skip=()):
        when = 0.0
        for tick in range(seconds):
            if tick in skip:
                continue
            for _ in range(int(1 / step)):
                when += step
                reader.read(timer_crop(start + tick), when)
        return reader

    def test_a_minute_of_queueing_teaches_it_every_digit(self):
        reader = queuedigits.DigitReader(cache_dir=self.cache)
        self.assertFalse(reader.ready)
        self._run(reader, 7, 60)
        self.assertTrue(reader.ready)
        self.assertEqual(len(reader.templates), 10)

    def test_the_learned_digits_read_the_clock_back(self):
        reader = self._run(queuedigits.DigitReader(cache_dir=self.cache), 7, 60)
        for value in (0, 9, 29, 59, 60, 95, 247, 599):
            reader.reset()
            self.assertEqual(reader.read(timer_crop(value), 1000.0), value,
                             "misread %d" % value)

    def test_the_digits_survive_into_a_fresh_reader(self):
        self._run(queuedigits.DigitReader(cache_dir=self.cache), 7, 60)
        self.assertTrue(queuedigits.DigitReader(cache_dir=self.cache).ready)

    def test_ticks_alone_are_not_enough_without_a_rollover(self):
        """Nine seconds of clock is nine glyphs and no anchor. Labelling them from
        whatever came first would be a guess, and a guess here misreads every timer
        afterwards by a fixed amount, quietly."""
        reader = self._run(queuedigits.DigitReader(cache_dir=self.cache), 0, 9)
        self.assertFalse(reader.ready)

    def test_a_missed_second_spoils_only_the_run_it_fell_in(self):
        """A dropped frame shifts every glyph after it by one place, which is the one way
        this could learn a whole set of confidently wrong digits. The run holding the gap
        comes out nine ticks long instead of ten and is thrown away for it, so what gets
        learned is learned from the clean runs either side."""
        reader = queuedigits.DigitReader(cache_dir=self.cache)
        self._run(reader, 0, 40, skip=(13,))
        self.assertTrue(reader.ready)
        for value in (7, 42, 60, 118):
            reader.reset()
            self.assertEqual(reader.read(timer_crop(value), 1000.0), value)

    def test_nothing_is_learned_when_every_run_is_broken(self):
        reader = queuedigits.DigitReader(cache_dir=self.cache)
        self._run(reader, 0, 36, skip=(13, 25))
        self.assertFalse(reader.ready)


@unittest.skipUnless(HAVE_CV2, "vision extras aren't installed")
class TimerPlausibilityTests(unittest.TestCase):

    def setUp(self):
        self.cache = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.cache, ignore_errors=True)
        self.reader = queuedigits.DigitReader(cache_dir=self.cache)
        when = 0.0
        for tick in range(60):
            for _ in range(2):
                when += 0.5
                self.reader.read(timer_crop(7 + tick), when)
        self.reader.reset()

    def test_a_reading_that_agrees_with_the_wall_clock_is_kept(self):
        self.assertEqual(self.reader.read(timer_crop(30), 0.0), 30)
        self.assertEqual(self.reader.read(timer_crop(31), 1.0), 31)

    def test_an_impossible_jump_is_thrown_away(self):
        self.reader.read(timer_crop(30), 0.0)
        self.assertIsNone(self.reader.read(timer_crop(247), 1.0))

    def test_it_re_anchors_rather_than_wedging_on_a_real_jump(self):
        """The guard exists to reject misreads, not to outlast the truth. A clock that
        keeps insisting is the clock, and this has to give way or it never reads again."""
        self.reader.read(timer_crop(30), 0.0)
        answers = [self.reader.read(timer_crop(247 + n), 1.0 + n) for n in range(6)]
        self.assertIsNone(answers[0])
        self.assertEqual(answers[-1], 252)


# ---------------------------------------------------------------- the state machine

def hit(kind=queuevision.MENU_PILL, mode="competitive", game_found=False):
    """A `BannerHit` built by hand — no image, no OpenCV, just the answer."""
    return queuevision.BannerHit(kind, (0, 0, 240, 104), mode, 340.0, game_found,
                                 0.9, 0.9)


BAR = queuevision.IN_GAME_BAR
PILL = queuevision.MENU_PILL


class TrackerEntryTests(unittest.TestCase):

    def setUp(self):
        self.tracker = queuewatch.QueueTracker()

    def test_one_frame_is_not_a_queue(self):
        self.assertEqual(self.tracker.update(hit(), 0.0).state, queuewatch.IDLE)

    def test_two_frames_in_a_row_are(self):
        self.tracker.update(hit(), 0.0)
        seen = self.tracker.update(hit(), 0.25)
        self.assertEqual(seen.state, queuewatch.SEARCHING_MENU)
        self.assertTrue(seen.searching)

    def test_a_lone_flash_of_colour_is_forgotten(self):
        self.tracker.update(hit(), 0.0)
        self.tracker.update(None, 0.25)
        self.assertEqual(self.tracker.update(hit(), 0.5).state, queuewatch.IDLE)

    def test_the_mode_comes_off_the_banner(self):
        self.tracker.update(hit(mode="quickPlay"), 0.0)
        self.assertEqual(self.tracker.update(hit(mode="quickPlay"), 0.25).mode,
                         "quickPlay")

    def test_an_unmeasured_colour_leaves_the_last_known_mode_alone(self):
        """A banner whose hue isn't in the table is still a queue. Blanking the mode
        because of it would replace a good answer with no answer."""
        for when in (0.0, 0.25):
            self.tracker.update(hit(mode="competitive"), when)
        seen = self.tracker.update(hit(mode=None), 0.5)
        self.assertEqual(seen.mode, "competitive")
        self.assertTrue(seen.searching)


class TrackerGapTests(unittest.TestCase):
    """The in-between state — the whole reason this class exists."""

    def setUp(self):
        self.tracker = queuewatch.QueueTracker()
        for when in (0.0, 0.25):
            self.tracker.update(hit(), when)

    def test_a_short_gap_is_still_a_queue(self):
        seen = self.tracker.update(None, 10.0)
        self.assertEqual(seen.state, queuewatch.SEARCHING_HIDDEN)
        self.assertTrue(seen.searching)

    def test_a_long_gap_ends_it(self):
        self.tracker.update(None, 10.0)
        self.assertEqual(self.tracker.update(None, 30.0).state, queuewatch.IDLE)

    def test_the_banner_coming_back_in_the_other_place_keeps_the_queue(self):
        """Menu pill, gap while a deathmatch loads, then the in-game bar. One queue."""
        self.tracker.update(None, 6.0)
        seen = self.tracker.update(hit(kind=BAR), 12.0)
        self.assertEqual(seen.state, queuewatch.SEARCHING_IN_GAME)
        self.assertAlmostEqual(seen.queue_elapsed, 12.0, places=3)

    def test_the_wait_is_not_reset_by_the_gap(self):
        self.tracker.update(None, 15.0)
        self.assertAlmostEqual(self.tracker.update(hit(kind=BAR), 18.0).queue_elapsed,
                               18.0, places=3)

    def test_the_grace_starts_again_after_the_banner_returns(self):
        self.tracker.update(None, 15.0)
        self.tracker.update(hit(kind=BAR), 16.0)
        self.tracker.update(None, 30.0)
        self.assertTrue(self.tracker.update(None, 34.0).searching)

    def test_being_alt_tabbed_holds_the_state_instead_of_replacing_it(self):
        """The bug this exists to stop. The panel showing the state is itself a window,
        so reading it is what puts Overwatch in the background — and reporting that as
        "banner out of sight" meant the readout said so every single time a human looked
        at it, no matter what the queue was doing."""
        seen = self.tracker.update(None, 5.0, visible=False)
        self.assertEqual(seen.state, queuewatch.SEARCHING_MENU)
        self.assertFalse(seen.looking)

    def test_the_banner_really_going_is_still_reported_as_such(self):
        """The other half of the same distinction: when we *can* see and there is no
        banner, that is a real gap and worth showing."""
        seen = self.tracker.update(None, 5.0, visible=True)
        self.assertEqual(seen.state, queuewatch.SEARCHING_HIDDEN)
        self.assertTrue(seen.looking)

    def test_looking_comes_back_the_moment_a_banner_is_seen(self):
        self.tracker.update(None, 5.0, visible=False)
        self.assertTrue(self.tracker.update(hit(), 6.0).looking)

    def test_time_spent_alt_tabbed_is_not_charged_to_the_grace(self):
        """Twenty seconds away, then the banner is genuinely gone for fifteen. If the
        blind time counted, that would add to thirty-five and end a live queue."""
        for when in range(2, 22, 2):
            self.tracker.update(None, float(when), visible=False)
        self.tracker.update(hit(), 22.0)
        self.assertTrue(self.tracker.update(None, 36.0, visible=True).searching)

    def test_being_alt_tabbed_does_not_count_against_the_grace(self):
        """A screenshot of the desktop is not evidence the queue ended. Counting it would
        end a queue for someone who tabbed out to read something for half a minute."""
        for when in range(5, 120, 5):
            seen = self.tracker.update(None, float(when), visible=False)
        self.assertTrue(seen.searching)

    def test_but_it_gives_up_eventually(self):
        for when in range(5, 400, 5):
            seen = self.tracker.update(None, float(when), visible=False)
        self.assertEqual(seen.state, queuewatch.IDLE)


class TrackerMatchTests(unittest.TestCase):

    def setUp(self):
        self.tracker = queuewatch.QueueTracker()
        for when in (0.0, 0.25):
            self.tracker.update(hit(), when)

    def test_one_frame_of_green_is_enough(self):
        seen = self.tracker.update(hit(game_found=True), 30.0)
        self.assertEqual(seen.state, queuewatch.GAME_FOUND)

    def test_the_wait_freezes_at_the_moment_the_match_landed(self):
        self.tracker.update(hit(game_found=True), 30.0)
        self.assertAlmostEqual(self.tracker.update(None, 36.0).queue_elapsed, 30.0,
                               places=3)

    def test_the_banner_lingering_afterwards_is_not_a_new_queue(self):
        self.tracker.update(hit(game_found=True), 30.0)
        self.assertEqual(self.tracker.update(hit(), 31.0).state, queuewatch.GAME_FOUND)

    def test_it_falls_back_to_idle_once_the_match_has_moved_on(self):
        self.tracker.update(hit(game_found=True), 30.0)
        self.assertEqual(self.tracker.update(None, 50.0).state, queuewatch.IDLE)


class TrackerTimingTests(unittest.TestCase):

    def setUp(self):
        self.tracker = queuewatch.QueueTracker()

    def test_without_a_readable_timer_it_counts_from_when_it_noticed(self):
        self.tracker.update(hit(), 0.0)
        self.tracker.update(hit(), 0.25)
        seen = self.tracker.update(hit(), 10.25)
        self.assertAlmostEqual(seen.queue_elapsed, 10.25, places=3)
        self.assertEqual(seen.source, "self")

    def test_the_screens_timer_wins_the_moment_it_can_be_read(self):
        """The point of reading it at all: a server that joined a queue already in
        progress is ninety seconds wrong until this happens, and right afterwards."""
        self.tracker.update(hit(), 0.0)
        self.tracker.update(hit(), 0.25)
        seen = self.tracker.update(hit(), 1.0, timer=95)
        self.assertAlmostEqual(seen.queue_elapsed, 95.0, places=3)
        self.assertEqual(seen.source, "timer")

    def test_a_reading_that_agrees_leaves_the_start_where_it_was(self):
        self.tracker.update(hit(), 0.0)
        self.tracker.update(hit(), 0.25, timer=0)
        before = self.tracker._started
        self.tracker.update(hit(), 5.25, timer=5)
        self.assertEqual(self.tracker._started, before)

    def test_the_wait_survives_a_gap_once_anchored(self):
        self.tracker.update(hit(), 0.0)
        self.tracker.update(hit(), 0.25, timer=40)
        self.tracker.update(None, 8.0)
        self.assertAlmostEqual(self.tracker.update(hit(kind=BAR), 14.0).queue_elapsed,
                               53.75, places=2)


# ---------------------------------------------------------------- the wiring

class _StubReader:
    def __init__(self):
        self.resets = 0
        self.ready = False

    def reset(self):
        self.resets += 1


class WatcherWiringTests(unittest.TestCase):
    """What each state does to the session everyone else is reading."""

    def setUp(self):
        self.server = QueueServer(Pairing(token="t" * 32, port=0), Catalog())
        self.controls = Controls(self.server)
        self.watcher = queuewatch.QueueWatcher(self.controls, reader=_StubReader())
        self.moves = []
        self.watcher.on_change = self.moves.append

    def _see(self, state, mode="competitive", elapsed=0.0):
        seen = queuewatch.QueueObservation(state, PILL, mode, elapsed, 0.0, "self", 0.9)
        self.watcher._act(seen)
        return seen

    def test_searching_starts_a_queue_on_the_session(self):
        self._see(queuewatch.SEARCHING_MENU)
        self.assertEqual(self.server.session.kind, "searching")

    def test_the_banners_colour_picks_the_mode(self):
        self._see(queuewatch.SEARCHING_MENU, mode="quickPlay")
        self.assertEqual(self.server.session.phase["data"]["mode"], "quickPlay")

    def test_an_unmeasured_colour_keeps_the_panels_mode(self):
        self.controls.mode = "arcade"
        self._see(queuewatch.SEARCHING_MENU, mode=None)
        self.assertEqual(self.server.session.phase["data"]["mode"], "arcade")

    def test_the_role_comes_from_the_panel_because_the_banner_cannot_say(self):
        """The bar reads "ROLE QUEUE: QUICK PLAY" — which names the mode, not the role.
        Nothing on screen says which role was queued, so the panel stays the source."""
        self.controls.role = "support"
        self._see(queuewatch.SEARCHING_MENU, mode="quickPlay")
        self.assertEqual(self.server.session.phase["data"]["role"], "support")

    def test_moving_between_the_two_banners_does_not_restart_the_queue(self):
        self._see(queuewatch.SEARCHING_MENU)
        session = self.server.session.session_id
        self._see(queuewatch.SEARCHING_HIDDEN)
        self._see(queuewatch.SEARCHING_IN_GAME)
        self.assertEqual(self.server.session.session_id, session)
        self.assertEqual(self.server.session.kind, "searching")

    def test_a_match_moves_the_session_on_with_the_wait_it_measured(self):
        self._see(queuewatch.SEARCHING_MENU)
        self._see(queuewatch.GAME_FOUND, elapsed=137.0)
        self.assertEqual(self.server.session.kind, "matchFound")
        self.assertEqual(self.server.session.phase["data"]["waited"], 137.0)

    def test_a_queue_that_just_stops_is_reported_as_cancelled(self):
        self._see(queuewatch.SEARCHING_MENU)
        self._see(queuewatch.IDLE)
        self.assertEqual(self.server.session.kind, "cancelled")

    def test_only_real_moves_are_announced(self):
        self._see(queuewatch.SEARCHING_MENU)
        self._see(queuewatch.SEARCHING_MENU)
        self._see(queuewatch.GAME_FOUND)
        self.assertEqual([seen.state for seen in self.moves],
                         [queuewatch.SEARCHING_MENU, queuewatch.GAME_FOUND])

    def test_a_read_timer_back_dates_the_start_the_phone_is_ticking_from(self):
        """`startedAt` is what every device counts from, so correcting it here is what
        makes a late-joined queue show the right number on the watch."""
        self._see(queuewatch.SEARCHING_MENU)
        seen = queuewatch.QueueObservation(queuewatch.SEARCHING_MENU, PILL,
                                           "competitive", 240.0, 0.0, "timer", 0.9)
        self.watcher._act(seen)
        self.assertGreater(self.server.session.elapsed(), 200.0)

    def test_a_guessed_wait_never_moves_the_start(self):
        self._see(queuewatch.SEARCHING_MENU)
        self._see(queuewatch.SEARCHING_MENU, elapsed=240.0)
        self.assertLess(self.server.session.elapsed(), 5.0)

    def test_a_mode_misread_on_the_first_frame_is_corrected_by_later_ones(self):
        """The frame a queue starts on is the banner mid-animation, part transparent and
        sliding in — the worst frame to take a colour from, and the only one we get
        before telling the phone. Every frame after it is another look."""
        self._see(queuewatch.SEARCHING_MENU, mode="competitive")
        self._see(queuewatch.SEARCHING_MENU, mode="quickPlay")
        self.assertEqual(self.server.session.phase["data"]["mode"], "quickPlay")
        self.assertEqual(self.controls.mode, "quickPlay")

    def test_correcting_the_mode_does_not_restart_the_queue(self):
        self._see(queuewatch.SEARCHING_MENU, mode="competitive")
        session = self.server.session.session_id
        self._see(queuewatch.SEARCHING_MENU, mode="quickPlay")
        self.assertEqual(self.server.session.session_id, session)

    def test_an_unmeasured_colour_never_blanks_a_mode_already_known(self):
        """None means "not measured", not "no longer competitive"."""
        self._see(queuewatch.SEARCHING_MENU, mode="competitive")
        self._see(queuewatch.SEARCHING_MENU, mode=None)
        self.assertEqual(self.server.session.phase["data"]["mode"], "competitive")

    def test_the_mode_is_left_alone_once_the_match_has_landed(self):
        self._see(queuewatch.SEARCHING_MENU, mode="competitive")
        self._see(queuewatch.GAME_FOUND, mode="competitive")
        self._see(queuewatch.GAME_FOUND, mode="quickPlay")
        self.assertEqual(self.server.session.phase["data"]["mode"], "competitive")


class WatcherPacingTests(unittest.TestCase):
    def setUp(self):
        server = QueueServer(Pairing(token="t" * 32, port=0), Catalog())
        self.watcher = queuewatch.QueueWatcher(Controls(server), reader=_StubReader())

    def _interval(self, state):
        return self.watcher._interval(
            queuewatch.QueueObservation(state, None, None, 0.0, 0.0, "self", 0.0))

    def test_it_looks_rarely_when_nothing_is_happening(self):
        self.assertEqual(self._interval(queuewatch.IDLE), queuewatch.IDLE_POLL_SECONDS)

    def test_it_looks_often_once_there_is_a_queue(self):
        self.assertEqual(self._interval(queuewatch.SEARCHING_MENU),
                         queuewatch.SEARCHING_POLL_SECONDS)

    def test_one_promising_frame_already_speeds_it_up(self):
        """So the frame that confirms a queue arrives a quarter-second later rather than
        a second and a half — which is most of what entering costs."""
        self.watcher.tracker.update(hit(), 0.0)
        self.assertEqual(self._interval(queuewatch.IDLE),
                         queuewatch.SEARCHING_POLL_SECONDS)


class ServerIntegrationTests(unittest.TestCase):
    def test_queue_vision_is_off_unless_it_is_asked_for(self):
        """Same rule as the hero-select vision it sits beside: merely building a server
        must never start something that reads the screen."""
        server = QueueServer(Pairing(token="t" * 32, port=0), Catalog())
        self.assertFalse(server.queue_vision_enabled)
        self.assertIsNone(server.queue_watcher)

    def test_the_phases_it_sends_are_ones_the_app_would_accept(self):
        for phase in (protocol.searching("quickPlay", "tank", protocol.now()),
                      protocol.match_found("quickPlay", "tank", 137.0),
                      protocol.cancelled("userLeft")):
            session = protocol.QueueSession()
            if phase["type"] != "searching":
                session.advance(protocol.searching("quickPlay", "tank", protocol.now()))
            self.assertTrue(session.advance(phase), phase["type"])


if __name__ == "__main__":
    unittest.main()
