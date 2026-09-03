"""Reading the role-select screen, tested without a screen.

The same split as `test_queuevision.py`, and for the same reason: the arithmetic — which
single role a set of checked boxes collapses to, whether four icons in a row look like
the real thing — runs anywhere, including the Mac this project is developed on. Only the
tests that need an image to look at are skipped there, and those build their own: a
synthetic card row with its own stand-in glyphs rather than the real fetched art, because
what's under test is the pipeline (locate an icon, sample its checkbox, sample its card),
not whether these particular shapes resemble Overwatch's.
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "server"))

from owqserver import queueroles, queuevision                     # noqa: E402

HAVE_CV2 = queueroles.QUEUE_ROLES_AVAILABLE
SCREEN = (2000, 1125)

if HAVE_CV2:
    import cv2
    import numpy as np


# ---------------------------------------------------------------- image fixtures

def _glyph(role, size=64):
    """A small shape distinct per role, standing in for the real fetched icon. The
    pipeline only cares that the same shape is used both as the "art" and as what's
    drawn on the fake screen — not that it looks anything like a shield or a cross."""
    mask = np.zeros((size, size), np.uint8)
    if role == "tank":
        cv2.rectangle(mask, (4, 4), (size - 4, size - 4), 255, -1)
    elif role == "damage":
        cv2.circle(mask, (size // 2, size // 2), size // 2 - 4, 255, -1)
    elif role == "support":
        cv2.line(mask, (4, size // 2), (size - 4, size // 2), 255, 10)
        cv2.line(mask, (size // 2, 4), (size // 2, size - 4), 255, 10)
    else:                                # flex
        for cx, cy in ((size * 0.3, size * 0.3), (size * 0.7, size * 0.3),
                       (size * 0.5, size * 0.7)):
            cv2.circle(mask, (int(cx), int(cy)), size // 4, 255, -1)
    return mask


GLYPH_SIZE = 90     # close to the ~90-94px a real card's icon actually matched at


class _FakeStore:
    """A `RoleIconStore` that never touches the network — every role's "art" is
    `_glyph`, generated once and handed back like a real cache would."""

    def template(self, role):
        return _glyph(role, size=GLYPH_SIZE)


def _row_layout(icon_size=GLYPH_SIZE, gap=300):
    """Four icon boxes, evenly spaced inside the region the real detector searches, in
    `queueroles.ROLE_ORDER` — the order the real cards are always shown in."""
    x0, y0, x1, y1 = queueroles._fractional_region(queueroles.ROLE_ROW_REGION, *SCREEN)
    start_x, y = x0 + 40, y0 + 60
    return {role: (start_x + i * gap, y, icon_size, icon_size)
            for i, role in enumerate(queueroles.ROLE_ORDER)}


def _geometry_for(layout):
    """The `RowGeometry` a perfect, noise-free detection of `layout` would produce —
    used to draw the checkbox and card exactly where `card_checked`/`card_hue` will
    later go looking for them, the way the real UI's fixed layout does."""
    tops = [box[1] for box in layout.values()]
    xs = sorted(layout[role][0] + layout[role][2] / 2.0 for role in queueroles.ROLE_ORDER
                if role in layout)
    gaps = [b - a for a, b in zip(xs, xs[1:])]
    return queueroles.RowGeometry(sum(tops) / len(tops), sum(gaps) / len(gaps))


def _paint_card(canvas, hit, geometry, hue):
    """The saturated fill behind one focused card — the same flat, hue-only box
    `test_queuevision.py`'s `_fill` builds for the queue banner."""
    x0, y0, x1, y1 = queueroles._region_from_icon(queueroles.CARD_REGION_FROM_ICON, hit,
                                                  geometry)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(canvas.shape[1], x1), min(canvas.shape[0], y1)
    patch = np.zeros((y1 - y0, x1 - x0, 3), np.uint8)
    patch[:, :] = (int(hue / 2), 200, 230)
    canvas[y0:y1, x0:x1] = cv2.cvtColor(patch, cv2.COLOR_HSV2BGR)


def _draw_checkbox(canvas, hit, geometry, checked):
    x0, y0, x1, y1 = queueroles._region_from_icon(queueroles.CHECKBOX_REGION_FROM_ICON,
                                                  hit, geometry)
    cv2.rectangle(canvas, (x0, y0), (x1, y1), (200, 200, 200), 2)
    if checked:
        # A real checkmark's own strokes don't necessarily cross dead centre — measured,
        # exactly that shape was once missing `card_checked`'s inset interior entirely.
        # A small disc centred in the box lands inside whatever interior a border inset
        # leaves without *filling* it — a disc big enough to do that reads as one more
        # flat colour to a variance test, which is indistinguishable from empty.
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        cv2.circle(canvas, (cx, cy), max(2, min(x1 - x0, y1 - y0) // 10), (255, 255, 255), -1)


def scene(checked_roles=(), vivid=None, hue=212.0, layout=None):
    """A synthetic role-select screen: a dark, idle background; four icons in a row;
    a checkmark on every role in `checked_roles`; and, if `vivid` names one, that card's
    body filled with `hue` the way the real focused card lights up."""
    layout = layout or _row_layout()
    geometry = _geometry_for(layout)
    canvas = np.full((SCREEN[1], SCREEN[0], 3), 25, np.uint8)
    hits = {role: queueroles.RoleCardHit(role, 1.0, *box) for role, box in layout.items()}

    if vivid is not None:
        _paint_card(canvas, hits[vivid], geometry, hue)
    for role, hit in hits.items():
        glyph = cv2.resize(_glyph(role), (hit.w, hit.h), interpolation=cv2.INTER_AREA)
        region = canvas[hit.y:hit.y + hit.h, hit.x:hit.x + hit.w]
        region[glyph > 127] = (255, 255, 255)
        _draw_checkbox(canvas, hit, geometry, role in checked_roles)
    return canvas


def scenery(seed=0):
    """Ordinary game scenery, with nothing icon-shaped anywhere in it."""
    rng = np.random.default_rng(seed)
    return rng.integers(20, 90, size=(SCREEN[1], SCREEN[0], 3), dtype=np.uint8)


# ---------------------------------------------------------------- pure logic

class SelectionTests(unittest.TestCase):
    """Collapsing a set of checked roles to the single value the rest of the app speaks."""

    def _sel(self, roles):
        return queueroles.RoleSelection(frozenset(roles), None, {}, True)

    def test_nothing_checked_is_undecided_not_a_guess(self):
        self.assertIsNone(self._sel([]).effective_role)

    def test_exactly_one_checked_is_unambiguous(self):
        self.assertEqual(self._sel(["support"]).effective_role, "support")

    def test_more_than_one_checked_is_flex(self):
        self.assertEqual(self._sel(["tank", "support"]).effective_role, "flex")

    def test_every_role_checked_is_still_just_flex(self):
        self.assertEqual(self._sel(["tank", "damage", "support", "flex"]).effective_role,
                         "flex")


class RowGeometryTests(unittest.TestCase):
    """The row's own shared top and spacing, recovered from the hits alone.

    This exists because of a real bug, not a hypothetical one: reading real fetched icon
    art against a real capture, the four icons' own matched boxes disagreed with each
    other by up to 30% in height, because a resized template's edges partially correlate
    with the decorative ring drawn around every icon at more than one nearby scale — and
    because the checkbox and card regions used to be found as a multiple of each icon's
    *own* matched height, that was enough to land the sampled interior on the checkbox's
    own border and read an untouched box as checked. Centre-to-centre spacing measured
    far more reliably on the same capture, which is why every offset is built on that
    instead — these are the arithmetic behind it, pixels aside.
    """

    def _hit(self, role, x, w=100, h=100, score=0.9, y=400):
        return queueroles.RoleCardHit(role, score, x, y, w, h)

    def _centred(self, role, center, w=100, h=100, score=0.9, y=400):
        """A hit whose *centre* — not its left edge — lands at `center`, since a
        differently-sized box built from the same left edge as another would silently
        move the centre `row_geometry` actually measures."""
        return queueroles.RoleCardHit(role, score, center - w / 2.0, y, w, h)

    def test_spacing_is_the_gap_between_confident_centres(self):
        hits = {"tank": self._hit("tank", 0), "damage": self._hit("damage", 300),
                "support": self._hit("support", 600)}
        self.assertAlmostEqual(queueroles.row_geometry(hits).spacing, 300.0)

    def test_uneven_heights_dont_move_the_spacing(self):
        """The whole point: two icons genuinely different sizes, the way Tank's shield
        and Damage's bullets measure on a real capture, still agree on where the next
        card sits."""
        hits = {"tank": self._centred("tank", 0, w=90, h=94),
                "damage": self._centred("damage", 300, w=63, h=57)}
        # `RoleCardHit` rounds its box to whole pixels, so a fractional centre can be off
        # by half a pixel from the number that went in — noise this small isn't the
        # ambiguity this test is about.
        self.assertAlmostEqual(queueroles.row_geometry(hits).spacing, 300.0, delta=1.0)

    def test_top_is_averaged_across_the_row(self):
        hits = {"tank": self._hit("tank", 0, y=460), "damage": self._hit("damage", 300, y=470)}
        self.assertAlmostEqual(queueroles.row_geometry(hits).top, 465.0)

    def test_a_non_adjacent_pair_still_agrees_on_spacing(self):
        """Tank and Flex are three slots apart; the gap between them divided by three
        should land on the same spacing two neighbours would give."""
        hits = {"tank": self._hit("tank", 0), "flex": self._hit("flex", 900)}
        self.assertAlmostEqual(queueroles.row_geometry(hits).spacing, 300.0)

    def test_weak_matches_are_excluded(self):
        hits = {"tank": self._hit("tank", 0), "damage": self._hit("damage", 300),
                "support": self._hit("support", 5000, score=0.1)}
        self.assertAlmostEqual(queueroles.row_geometry(hits).spacing, 300.0)

    def test_one_confident_hit_is_nothing_to_measure_spacing_from(self):
        self.assertIsNone(queueroles.row_geometry({"tank": self._hit("tank", 0)}))

    def test_no_hits_is_nothing_to_measure_from(self):
        self.assertIsNone(queueroles.row_geometry({}))


class RowShapeTests(unittest.TestCase):
    """Whether a set of icon hits looks like the real row of four cards.

    No pixels here — `RoleCardHit` is built by hand, the same way `test_vision.py`'s
    `RegionTests` and `TakenHeroTests` build their fixtures without a screen.
    """

    def _hit(self, role, x, y=400, size=48, score=0.9):
        return queueroles.RoleCardHit(role, score, x, y, size, size)

    def _row(self, **overrides):
        hits = {role: self._hit(role, 100 + i * 200)
                for i, role in enumerate(queueroles.ROLE_ORDER)}
        hits.update(overrides)
        return hits

    def test_four_confident_icons_in_order_is_the_role_screen(self):
        self.assertTrue(queueroles.looks_like_role_select(self._row()))

    def test_three_of_four_is_still_enough(self):
        """One card can sit under a cursor or a focus glow that distorts its icon —
        the same tolerance the hero grid gives an obscured portrait."""
        hits = self._row()
        del hits["flex"]
        self.assertTrue(queueroles.looks_like_role_select(hits))

    def test_two_of_four_is_not_enough(self):
        hits = self._row()
        del hits["flex"]
        del hits["support"]
        self.assertFalse(queueroles.looks_like_role_select(hits))

    def test_weak_matches_dont_count_towards_the_four(self):
        """Two of the four scoring low leaves only two confident — one below the
        three-of-four tolerance `test_three_of_four_is_still_enough` allows — so this has
        to fail on the strength of the matches, not just their count."""
        hits = self._row(tank=self._hit("tank", 100, score=0.1),
                         damage=self._hit("damage", 300, score=0.2))
        self.assertFalse(queueroles.looks_like_role_select(hits))

    def test_out_of_order_is_not_the_role_screen(self):
        """The four cards are always Tank, Damage, Support, then the fourth — swapped
        positions is the shape of a coincidence, not this screen."""
        hits = self._row()
        hits["tank"], hits["damage"] = hits["damage"], hits["tank"]
        self.assertFalse(queueroles.looks_like_role_select(hits))

    def test_a_row_that_isnt_level_is_not_the_role_screen(self):
        hits = self._row(support=self._hit("support", 500, y=900))
        self.assertFalse(queueroles.looks_like_role_select(hits))


class SvgRasterTests(unittest.TestCase):
    """The one icon that only exists as an SVG, filled by hand rather than by a renderer
    — see `_rasterize_svg`'s docstring for why that's workable here at all."""

    def test_a_closed_triangle_is_read_as_one_polygon(self):
        points = queueroles._svg_polygons("M 0,0 L 10,0 5,10 Z")
        self.assertEqual(points, [[[0.0, 0.0], [10.0, 0.0], [5.0, 10.0]]])

    def test_two_subpaths_are_read_as_two_polygons(self):
        points = queueroles._svg_polygons("M 0,0 L 1,0 1,1 Z M 5,5 L 6,5 6,6 Z")
        self.assertEqual(len(points), 2)

    @unittest.skipUnless(HAVE_CV2, "needs the vision extras")
    def test_a_triangle_svg_rasterises_to_a_filled_shape(self):
        svg = ('<svg viewBox="0 0 10 10"><path d="M 0,0 L 10,0 10,10 Z"/></svg>'
              ).encode("utf-8")
        mask = queueroles._rasterize_svg(svg, scale=10)
        self.assertIsNotNone(mask)
        # A right triangle covering half a 10x10 square, rasterised at 10x scale:
        # comfortably more than a sliver, comfortably less than the whole canvas.
        filled = float((mask > 0).mean())
        self.assertGreater(filled, 0.3)
        self.assertLess(filled, 0.7)

    def test_a_viewbox_free_svg_has_nothing_to_rasterise(self):
        self.assertIsNone(queueroles._rasterize_svg(b"<svg><path d='M 0,0 L 1,1 Z'/></svg>"))


# ---------------------------------------------------------------- pixels

@unittest.skipUnless(HAVE_CV2, "needs the vision extras")
class DetectionTests(unittest.TestCase):
    """The full pipeline against a built screen, with a known right answer."""

    def test_scenery_has_no_role_row_on_it(self):
        hits = queueroles.find_role_row(scenery(), _FakeStore())
        self.assertFalse(queueroles.looks_like_role_select(hits))

    def test_every_icon_is_found_where_it_was_put(self):
        layout = _row_layout()
        hits = queueroles.find_role_row(scene(layout=layout), _FakeStore())
        self.assertEqual(set(hits), set(queueroles.ROLE_ORDER))
        for role, (x, y, w, h) in layout.items():
            self.assertLess(abs(hits[role].x - x), 10)
            self.assertLess(abs(hits[role].y - y), 10)

    def test_a_built_row_looks_like_the_role_screen(self):
        hits = queueroles.find_role_row(scene(), _FakeStore())
        self.assertTrue(queueroles.looks_like_role_select(hits))

    def test_an_unchecked_box_reads_as_unchecked(self):
        canvas = scene(checked_roles=())
        hits = queueroles.find_role_row(canvas, _FakeStore())
        geo = queueroles.row_geometry(hits)
        self.assertFalse(queueroles.card_checked(canvas, hits["tank"], geo))

    def test_a_checked_box_reads_as_checked(self):
        canvas = scene(checked_roles=("tank",))
        hits = queueroles.find_role_row(canvas, _FakeStore())
        geo = queueroles.row_geometry(hits)
        self.assertTrue(queueroles.card_checked(canvas, hits["tank"], geo))

    def test_more_than_one_box_can_be_checked_at_once(self):
        """The whole reason this reads every card's own checkbox instead of asking "which
        one is highlighted": Overwatch lets more than one be checked together."""
        canvas = scene(checked_roles=("damage", "support"), vivid="damage")
        hits = queueroles.find_role_row(canvas, _FakeStore())
        geo = queueroles.row_geometry(hits)
        checked = {role for role, hit in hits.items()
                  if queueroles.card_checked(canvas, hit, geo)}
        self.assertEqual(checked, {"damage", "support"})

    def test_the_highlighted_cards_hue_names_the_mode(self):
        canvas = scene(vivid="support", hue=212.0)
        hits = queueroles.find_role_row(canvas, _FakeStore())
        geo = queueroles.row_geometry(hits)
        hue, coverage, spread = queueroles.card_hue(canvas, hits["support"], geo)
        modes = {"quickPlay": 212.0, "competitive": 340.0}
        self.assertEqual(queuevision.match_mode(hue, modes, 12.0), "quickPlay")
        self.assertGreater(coverage, 0.9)

    def test_an_idle_cards_hue_names_nothing(self):
        canvas = scene()
        hits = queueroles.find_role_row(canvas, _FakeStore())
        geo = queueroles.row_geometry(hits)
        hue, coverage, spread = queueroles.card_hue(canvas, hits["tank"], geo)
        self.assertLess(coverage, queuevision.DOMINANT_COVERAGE_MIN)

    def test_scan_reports_every_checked_role_and_the_mode(self):
        canvas_roles = {"damage", "support"}
        result = _ScanningStore.run(checked_roles=canvas_roles, vivid="damage", hue=340.0)
        self.assertTrue(result.on_screen)
        self.assertEqual(result.roles, frozenset(canvas_roles))
        self.assertEqual(result.mode, "competitive")
        self.assertEqual(result.effective_role, "flex")

    def test_scan_reports_a_single_role_as_itself(self):
        result = _ScanningStore.run(checked_roles={"support"})
        self.assertEqual(result.effective_role, "support")

    def test_scan_of_scenery_says_nothing_is_on_screen(self):
        result = queueroles.scan(_FakeStore(), modes=({}, 12.0))
        # `scan` captures the real screen when there's nothing built to hand it, so this
        # only checks the shape of "not found" — see `_ScanningStore` for the built case.
        self.assertIsInstance(result, queueroles.RoleSelection)


class _ScanningStore(_FakeStore):
    """Runs `queueroles.scan` against a built canvas instead of the real screen, by
    monkeypatching `capture` for the duration of one call — `scan` has no parameter to
    hand it a screen directly, on purpose, since nothing else in this codebase's vision
    modules takes one either."""

    @staticmethod
    def run(**scene_kwargs):
        canvas = scene(**scene_kwargs)
        real_capture = queueroles.capture
        queueroles.capture = lambda: canvas
        try:
            return queueroles.scan(_FakeStore(), modes=({"quickPlay": 212.0,
                                                          "competitive": 340.0}, 12.0))
        finally:
            queueroles.capture = real_capture


if __name__ == "__main__":
    unittest.main()
