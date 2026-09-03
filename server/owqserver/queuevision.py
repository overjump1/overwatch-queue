"""Finding the queue banner on screen.

Overwatch shows a queue in two places, and they need two different methods — which was
not the original plan, and is the main thing a real capture taught this module.

- **In a while-you-wait game**, a wide bar pinned to a top corner: mode icon, "ROLE
  QUEUE: QUICK PLAY", a timer. Found by shape and colour, because a game world contains
  nothing like it. Measured at 446x76 pinned right and 450x82 pinned left.
- **In the menu**, a tab at the top centre. *Not* found by shape and colour, because the
  idle tab is the same tab — same trapezoid, same blue, same place, differing only in
  what is printed on it. `menu_tab` explains what is used instead and why nothing
  cheaper works.

There is a third thing the queue does, which is show neither of those for a few seconds
while one turns into the other. That gap is deliberately not this module's problem: here
it is simply None, and `queuewatch` is what decides a gap still means "queueing".

When the match lands, whichever of the two is showing gets a saturated green check circle
where the mode icon was, which is `game_found` below.

Both kinds are made to show a timer before they count. That started as the menu's
discriminator and turned out to be worth applying to the bar too: the menu's own row of
mode tabs is a flat blue box of exactly bar-like proportions sitting against the left
edge, and it reads as an in-game queue bar in every menu frame without it. Finding the
timer needs no digit templates — `queuedigits.segment` asks only whether glyphs of the
right heights sit in a row with a colon between them — so this stays as free of
checked-in art as it always was.

The dominant-hue test is deliberately not "is the whole box one colour". It finds the
busiest ten-degree bucket and measures the spread of only what falls near it, because a
box that is 90% flat pink and 10% green check circle has to pass — that box is the single
most important frame this module will ever see.

The hue also *is* the mode: blue for Quick Play, pink for Competitive, per
`queuemodes.json`. An unrecognised hue is still a banner, reported with `mode` None.
Stadium and Arcade have never been in front of this code, and refusing to notice a queue
because its colour is new would be much the worse failure — the caller keeps whichever
mode the panel already has and carries on.

Hue is handled in degrees throughout and converted at the OpenCV boundary, which packs a
360-degree circle into a byte by halving it. That is exactly the sort of factor of two
that survives review and then reads a pink banner as green.

Nothing here focuses a window, moves the mouse or presses a key. It only looks.
"""
from __future__ import annotations

import json
import os

try:                                    # pragma: no cover - trivial import guard
    import cv2
    import numpy as np
    import mss
    QUEUE_VISION_AVAILABLE = True
except ImportError:                     # pragma: no cover - the Mac dev path
    cv2 = np = mss = None
    QUEUE_VISION_AVAILABLE = False

from . import queuedigits

MODES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "queuemodes.json")

MENU_PILL = "menuPill"
IN_GAME_BAR = "inGameBar"

# Only the top of the screen is ever captured. One strip covers the centre pill and both
# corners, so nothing here depends on which corner the in-game bar is pinned to — and on
# a 1440p screen it is a 2560x216 grab, which mss does in a millisecond or two.
STRIP_FRACTION = 0.15
# The mask work runs on a half-size copy, as in `vision.best_match`. A banner is hundreds
# of pixels wide; halving it costs nothing that matters and quarters the pixel count.
DOWNSCALE = 0.5

# Saturated and bright: interface, not scenery.
SATURATION_MIN = 100
VALUE_MIN = 90

# Closing has to swallow the white text and the icon, which are holes punched through the
# middle of an otherwise solid box. Sized off the banner's own height rather than a fixed
# pixel count, so it survives a change of resolution.
CLOSE_KERNEL_FRACTION = 0.024

# All as fractions of the whole screen, not of the captured strip.
BOX_WIDTH_RANGE = (0.06, 0.45)
# The floor here is doing real work. A deathmatch draws its own match timer — pink, wide,
# thin, and with a colon in it, so it passes both the shape test and the timer test — and
# it was being read as a Competitive queue bar throughout a Quick Play match. Measured, a
# real bar is 0.054-0.076 of the screen's height and that impostor was 0.030, so the floor
# sits between them rather than at the bottom of what a box could conceivably be.
BOX_HEIGHT_RANGE = (0.045, 0.12)
# A rounded rectangle nearly fills its bounding box; a scenery blob of the right size
# generally does not.
FILL_RATIO_MIN = 0.70
# ...and most of it is one colour, held tightly.
DOMINANT_COVERAGE_MIN = 0.60
HUE_STD_MAX = 8.0
HUE_BIN_DEGREES = 10.0
HUE_WINDOW_DEGREES = 20.0

# Measured off a real 1920x1080 capture of a Quick Play queue: the in-game bar came out
# 446x76 (aspect 5.9) pinned right and 450x82 (aspect 5.5) pinned left. The first guesses
# here were 5.5 and 0.06, and both were a hair too tight to catch either of them - 5.49
# fell under the floor, and the right-hand bar stopped 136px short of the edge, which is
# 0.071 of the width. Widened to where the measurements sit comfortably inside.
BAR_ASPECT_RANGE = (4.5, 12.0)
EDGE_FRACTION = 0.11

# Where the menu's tab sits. It does not move, which is the whole reason the menu is
# handled by looking at a fixed place rather than by hunting for a blob - see
# `menu_tab` for why hunting cannot work there.
MENU_TAB_REGION = (0.42, 0.000, 0.58, 0.080)
# ...and where the timer is printed inside it. Measured at 0.549-0.573; kept generous.
MENU_TIMER_REGION = (0.515, 0.000, 0.600, 0.055)

GREEN_HUE_RANGE = (85.0, 165.0)
GREEN_SATURATION_MIN = 110
GREEN_VALUE_MIN = 110
GREEN_DIAMETER_RANGE = (0.35, 1.05)
GREEN_ASPECT_RANGE = (0.7, 1.4)
# A disc fills 0.785 of its bounding box, and the white tick carved out of the middle of
# this one takes a bite out of that.
GREEN_ROUNDNESS_MIN = 0.40
# A check circle is an ornament on the banner. Something green covering half the box is
# the banner itself, in a mode whose colour nobody has measured yet.
GREEN_MAX_SHARE = 0.35

# Where the timer sits inside each kind of box, as (x0, y0, x1, y1) fractions of the box.
# The in-game bar right-aligns it; the pill puts it beside the icon on the upper row.
TIMER_REGION = {
    IN_GAME_BAR: (0.76, 0.05, 1.00, 0.95),
    MENU_PILL: (0.00, 0.00, 1.00, 1.00),      # the crop is already only the timer
}


class BannerHit:
    """One queue banner found in the strip, in strip pixel coordinates.

    Strip coordinates are screen coordinates — the strip starts at the top-left of the
    monitor — but nothing downstream needs that, and saying "strip" keeps the crop in
    `timer_patch` honest about what it is indexing into.
    """

    __slots__ = ("kind", "box", "mode", "hue", "game_found", "coverage", "fill")

    def __init__(self, kind, box, mode, hue, game_found, coverage, fill):
        self.kind = kind
        self.box = tuple(int(v) for v in box)       # x, y, w, h
        self.mode = mode                            # a protocol mode, or None if new
        self.hue = float(hue)
        self.game_found = bool(game_found)
        self.coverage = float(coverage)
        self.fill = float(fill)

    @property
    def searching(self) -> bool:
        return not self.game_found

    @property
    def confidence(self) -> float:
        """How much of the box agreed with itself. Not a probability — an ordering, used
        to choose between two candidates in the same frame."""
        return self.coverage * self.fill

    def timer_patch(self, strip):
        """The crop the timer digits live in, or None if the box is too small for one."""
        x, y, w, h = self.box
        fx0, fy0, fx1, fy1 = TIMER_REGION[self.kind]
        x0, y0 = max(0, x + int(w * fx0)), max(0, y + int(h * fy0))
        x1 = min(strip.shape[1], x + int(w * fx1))
        y1 = min(strip.shape[0], y + int(h * fy1))
        if x1 - x0 < 8 or y1 - y0 < 6:
            return None
        return strip[y0:y1, x0:x1]

    def __repr__(self):
        return "<BannerHit %s %s hue=%.0f%s box=%s conf=%.2f>" % (
            self.kind, self.mode or "?", self.hue,
            " GAME FOUND" if self.game_found else "", self.box, self.confidence)


# ---------------------------------------------------------------- modes

def load_modes(path: str = MODES_PATH):
    """The hue table as (hues, tolerance). A missing or broken file is not fatal: every
    banner then reports mode None, which every caller already has to handle."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        hues = {key: float(value) for key, value in (data.get("hues") or {}).items()}
        return hues, float(data.get("tolerance", 12.0))
    except (OSError, ValueError, TypeError):
        return {}, 12.0


def hue_distance(a: float, b: float) -> float:
    """Degrees apart the short way round the circle."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


def match_mode(hue: float, hues: dict, tolerance: float):
    """The nearest measured mode within `tolerance`, or None. Nearest rather than first,
    so adding a mode close to an existing one narrows both rather than shadowing one."""
    best, best_gap = None, tolerance
    for mode, known in hues.items():
        gap = hue_distance(hue, known)
        if gap <= best_gap:
            best, best_gap = mode, gap
    return best


# ---------------------------------------------------------------- capture

def strip_height(screen_h: int) -> int:
    return max(1, int(screen_h * STRIP_FRACTION))


def capture_strip():
    """The top of the primary monitor, as (BGR strip, screen width, screen height)."""
    with mss.MSS() as sct:
        monitor = sct.monitors[1]
        shot = sct.grab({"left": monitor["left"], "top": monitor["top"],
                         "width": monitor["width"],
                         "height": strip_height(monitor["height"])})
        strip = cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)
    return strip, monitor["width"], monitor["height"]


# ---------------------------------------------------------------- hue statistics

def dominant_hue(hues):
    """(hue, coverage, spread) for a bag of hues in degrees.

    Coverage is the share sitting within `HUE_WINDOW_DEGREES` of the busiest bucket, and
    spread is the circular standard deviation of only those — so a flat pink box with a
    green circle on it reads as flat pink at 90% coverage rather than as a wild mixture.
    """
    if hues.size == 0:
        return 0.0, 0.0, 360.0

    bin_count = int(round(360.0 / HUE_BIN_DEGREES))
    buckets = np.bincount((hues / HUE_BIN_DEGREES).astype(np.int32) % bin_count,
                          minlength=bin_count)
    centre = (int(buckets.argmax()) + 0.5) * HUE_BIN_DEGREES

    near = hues[np.abs((hues - centre + 180.0) % 360.0 - 180.0) <= HUE_WINDOW_DEGREES]
    if near.size == 0:
        return centre, 0.0, 360.0

    radians = np.deg2rad(near.astype(np.float64))
    x, y = float(np.cos(radians).mean()), float(np.sin(radians).mean())
    resultant = min(1.0, (x * x + y * y) ** 0.5)
    spread = float(np.degrees((-2.0 * np.log(max(resultant, 1e-9))) ** 0.5))
    return float(np.degrees(np.arctan2(y, x)) % 360.0), near.size / float(hues.size), spread


def green_check(patch) -> bool:
    """Whether a green check circle sits inside `patch` (BGR)."""
    if patch is None or patch.size == 0:
        return False
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0].astype(np.float32) * 2.0
    green = ((hue >= GREEN_HUE_RANGE[0]) & (hue <= GREEN_HUE_RANGE[1]) &
             (hsv[:, :, 1] >= GREEN_SATURATION_MIN) &
             (hsv[:, :, 2] >= GREEN_VALUE_MIN)).astype(np.uint8)

    count, _, stats, _ = cv2.connectedComponentsWithStats(green, 8)
    box_h, box_w = patch.shape[:2]
    for index in range(1, count):
        _, _, w, h, area = (int(value) for value in stats[index])
        if w <= 0 or h <= 0:
            continue
        diameter = max(w, h)
        if not GREEN_DIAMETER_RANGE[0] * box_h <= diameter <= GREEN_DIAMETER_RANGE[1] * box_h:
            continue
        if not GREEN_ASPECT_RANGE[0] <= w / float(h) <= GREEN_ASPECT_RANGE[1]:
            continue
        if area < GREEN_ROUNDNESS_MIN * w * h:
            continue
        if area > GREEN_MAX_SHARE * box_w * box_h:
            continue
        return True
    return False


# ---------------------------------------------------------------- detection

def classify(box, screen_w: int):
    """Which banner a box of this shape and position is, or None for neither."""
    x, _, w, h = box
    if h <= 0:
        return None
    aspect = w / float(h)
    if BAR_ASPECT_RANGE[0] <= aspect <= BAR_ASPECT_RANGE[1]:
        hugs_edge = (x <= EDGE_FRACTION * screen_w or
                     x + w >= (1.0 - EDGE_FRACTION) * screen_w)
        return IN_GAME_BAR if hugs_edge else None
    return None


def measure(strip, screen_w: int = 0, screen_h: int = 0):
    """Every saturated blob in `strip`, with all its numbers and why it was turned down.

    `reason` is None for the ones that survived, and names the test that rejected the
    rest. `candidates` is a filter over this rather than a second copy of it, so what the
    debug tool explains is exactly what the detector did — which is the whole point of a
    tool whose job is retuning these constants against a real screen.
    """
    if strip is None or strip.size == 0:
        return []
    screen_w = screen_w or strip.shape[1]
    screen_h = screen_h or int(strip.shape[0] / STRIP_FRACTION)

    small = cv2.resize(strip, (max(1, int(strip.shape[1] * DOWNSCALE)),
                               max(1, int(strip.shape[0] * DOWNSCALE))),
                       interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    mask = ((hsv[:, :, 1] >= SATURATION_MIN) &
            (hsv[:, :, 2] >= VALUE_MIN)).astype(np.uint8)

    size = max(3, int(screen_h * CLOSE_KERNEL_FRACTION * DOWNSCALE) | 1)
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((size, size), np.uint8))

    count, labels, stats, _ = cv2.connectedComponentsWithStats(closed, 8)
    back = 1.0 / DOWNSCALE
    measured = []
    for index in range(1, count):
        sx, sy, sw, sh, area = (int(value) for value in stats[index])
        if sw <= 0 or sh <= 0:
            continue
        box = (int(sx * back), int(sy * back), int(sw * back), int(sh * back))
        found = {"box": box, "aspect": box[2] / float(box[3]) if box[3] else 0.0,
                 "fill": area / float(sw * sh), "kind": None, "hue": None,
                 "coverage": None, "spread": None, "reason": None}
        measured.append(found)

        if not BOX_WIDTH_RANGE[0] * screen_w <= box[2] <= BOX_WIDTH_RANGE[1] * screen_w:
            found["reason"] = "width"
            continue
        if not BOX_HEIGHT_RANGE[0] * screen_h <= box[3] <= BOX_HEIGHT_RANGE[1] * screen_h:
            found["reason"] = "height"
            continue
        if found["fill"] < FILL_RATIO_MIN:
            found["reason"] = "fill"
            continue

        # The saturated pixels only. `closed` includes the text and icon it filled in,
        # whose colours are not the banner's and would drag the spread out. Measured
        # before the shape test purely so the tool can report the hue of a box that was
        # the wrong shape — the detector doesn't care about the order.
        hues = hsv[:, :, 0][(labels == index) & (mask > 0)].astype(np.float32) * 2.0
        found["hue"], found["coverage"], found["spread"] = dominant_hue(hues)

        found["kind"] = classify(box, screen_w)
        if found["kind"] is None:
            found["reason"] = "shape"
        elif found["coverage"] < DOMINANT_COVERAGE_MIN:
            found["reason"] = "mottled"
        elif found["spread"] > HUE_STD_MAX:
            found["reason"] = "not one colour"
    return measured


def candidates(strip, screen_w: int = 0, screen_h: int = 0):
    """Every box that passed every test, as (kind, box, hue, coverage, fill)."""
    return [(found["kind"], found["box"], found["hue"], found["coverage"], found["fill"])
            for found in measure(strip, screen_w, screen_h) if found["reason"] is None]


def _fractional_box(fractions, screen_w: int, screen_h: int):
    fx0, fy0, fx1, fy1 = fractions
    x0, y0 = int(screen_w * fx0), int(screen_h * fy0)
    return x0, y0, int(screen_w * fx1) - x0, int(screen_h * fy1) - y0


def menu_tab(strip, screen_w: int, screen_h: int, modes=None):
    """The menu's queue tab, or None when the menu isn't queueing.

    This does not hunt for a blob, and that is a finding rather than a shortcut. Two
    things about the real menu defeat blob-hunting outright:

    - The tab that says SEARCHING and the tab that sits there idle are *the same tab* —
      same trapezoid, same place, same blue. Only the contents differ: a bolt, the word
      SEARCHING and a timer, against the Overwatch logo. Nothing about the box's shape or
      colour separates them, so anything that finds one finds the other, and a detector
      that fires in the menu at rest is worse than no detector.
    - The menu around it is *also* saturated interface. Measured on a real capture, the
      close that fills the tab's own text bridges it to the nav bar above and the mode
      tabs below into one 1616x162 blob, and the tab stops existing as a component at all.

    What does separate them is the timer, because an idle tab has no timer. Finding
    `M:SS` needs no digit templates — `queuedigits.segment` asks only whether three or
    four glyphs of the right heights sit in a row with a colon in the right place — so
    this stays as free of checked-in art as the rest of the module. Measured over a real
    queue it caught 8 of the 9 seconds of menu searching with no false positive on any
    idle or in-game frame; the miss is the first second, while the tab animates in, which
    is what `queuewatch`'s two-frame entry is for anyway.
    """
    hues, tolerance = modes if modes is not None else load_modes()
    tab = _fractional_box(MENU_TAB_REGION, screen_w, screen_h)
    x, y, w, h = tab
    body = strip[max(0, y):y + h, max(0, x):x + w]
    if body.size == 0:
        return None

    hsv = cv2.cvtColor(body, cv2.COLOR_BGR2HSV)
    keep = (hsv[:, :, 1] >= SATURATION_MIN) & (hsv[:, :, 2] >= VALUE_MIN)
    hue, coverage, spread = dominant_hue(hsv[:, :, 0][keep].astype(np.float32) * 2.0)
    mode = match_mode(hue, hues, tolerance)

    # A match landing replaces the bolt with a green check — and takes the timer away
    # with it, so this has to be asked before the timer question, not after.
    if green_check(body):
        return BannerHit(MENU_PILL, tab, mode, hue, True, coverage, float(keep.mean()))

    timer = _fractional_box(MENU_TIMER_REGION, screen_w, screen_h)
    tx, ty, tw, th = timer
    if queuedigits.segment(strip[max(0, ty):ty + th, max(0, tx):tx + tw]) is None:
        return None
    return BannerHit(MENU_PILL, tab, mode, hue, False, coverage, float(keep.mean()))


def find_banner(strip, screen_w: int = 0, screen_h: int = 0, modes=None):
    """The queue banner in `strip`, or None if there isn't one.

    `modes` is `load_modes()`'s pair, passed in so a poll loop reads the file once rather
    than once a frame.

    The menu is asked first and separately — see `menu_tab`. What is left is the in-game
    bar, which *is* found by shape and colour, because a game world contains nothing
    remotely like it. It is still made to show a timer before it counts: measured on a
    real capture, the menu's own row of mode tabs is a flat blue box of exactly the right
    proportions sitting against the left edge, and without that check it reads as an
    in-game queue bar every time the menu is on screen.
    """
    screen_w = screen_w or strip.shape[1]
    screen_h = screen_h or int(strip.shape[0] / STRIP_FRACTION)

    menu = menu_tab(strip, screen_w, screen_h, modes)
    if menu is not None:
        return menu

    hues, tolerance = modes if modes is not None else load_modes()
    for kind, box, hue, coverage, fill in sorted(
            candidates(strip, screen_w, screen_h), key=lambda c: -c[3] * c[4]):
        x, y, w, h = box
        patch = strip[max(0, y):y + h, max(0, x):x + w]
        hit = BannerHit(kind, box, match_mode(hue, hues, tolerance), hue,
                        green_check(patch), coverage, fill)
        if hit.game_found or queuedigits.segment(hit.timer_patch(strip)) is not None:
            return hit
    return None
