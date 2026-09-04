"""Reading which role or roles are checked on the "Select a Role" screen.

Overwatch shows this screen before a role-queue mode starts searching: four cards in a
row — Tank, Damage, Support, and a fourth ("All", queuing for whichever of the three is
short) — each with its own checkbox, so more than one can be checked at once. Nothing
about the queue banner in `queuevision` says which of these was chosen; this module is
what looks at the screen behind it, on demand, the same way `vision.scan` reads the
hero-select roster on demand rather than being polled continuously.

Two things identify a card, and this uses both for two different questions:

- **The icon.** Each card carries the same glyph the rest of the game uses for that role
  — a shield, three bullets, a cross, three overlapping circles for "all". Matched by
  shape rather than by position, because position is what tells `looks_like_role_select`
  this is really the role screen and not four coincidentally similar shapes elsewhere.
- **The checkbox underneath it.** This is the actual answer to "which roles are checked"
  — an empty square versus a square with a checkmark in it. The vivid, saturated colour
  behind whichever card is focused (blue for Quick Play, pink for Competitive — the same
  hues `queuevision` reads off the queue banner itself) is what a person's eye is drawn
  to, but it marks keyboard/controller focus, not the checkbox state: only one card gets
  it, yet more than one can genuinely be checked. So the colour is read here only for
  which *mode* it names, as a bonus this screen happens to also reveal — not for which
  roles are selected.

Matching is done on a mask of "bright, low-saturation" pixels rather than on raw colour
or grayscale, for the same reason the icons themselves are fetched as their alpha channel
rather than their RGB: a plain white glyph is drawn on both a near-black idle card and a
vivid highlighted one, so the pixels that make it a shield are its brightness and shape,
never its background. Building the same kind of mask from the screen crop means one
template matches the icon on either kind of card without needing to know which.

The region and checkbox/card constants below started from two screenshots handed over
for this feature, and were then corrected against a real 1920x1080 recording of an actual
queue: the first guess had the row's search region clipping the Tank icon on one edge and
reaching into the group panel's own player list on the other, and derived the checkbox
and card positions as fractions of *each icon's own* box — which real footage showed to
be the wrong unit, since Tank's shield and Support's cross both measure a genuinely
different height from Damage's bullets or Flex's circles. See `reference_size` for the
fix. One capture is still one screen, one resolution and one UI scale, not a guarantee —
`queuerole_debug.py` exists to check these against a different one and correct them
further.

Nothing here focuses a window, moves the mouse or presses a key. It only looks.
"""
from __future__ import annotations

import os
import re
import urllib.error
import urllib.request

try:                                    # pragma: no cover - trivial import guard
    import cv2
    import numpy as np
    import mss
    QUEUE_ROLES_AVAILABLE = True
except ImportError:                     # pragma: no cover - the Mac dev path
    cv2 = np = mss = None
    QUEUE_ROLES_AVAILABLE = False

from . import queuevision

ROLE_ORDER = ["tank", "damage", "support", "flex"]

# The same glyphs Overwatch itself uses for each role, fetched once and cached rather
# than checked in — see `heroimages.py` for the same trade made the same way. Three are
# ordinary raster art; "flex" only exists as an SVG, handled by `_rasterize_svg` below.
ROLE_ICON_URLS = {
    "tank": "https://static.wikia.nocookie.net/overwatch_gamepedia/images/6/69/"
            "TankIcon.png/revision/latest?cb=20151109212047",
    "damage": "https://static.wikia.nocookie.net/overwatch_gamepedia/images/1/14/"
              "OffenseIcon.png/revision/latest?cb=20151109211953",
    "support": "https://static.wikia.nocookie.net/overwatch_gamepedia/images/5/5f/"
               "SupportIcon.png/revision/latest?cb=20151109212032",
    "flex": "https://static.wikia.nocookie.net/overwatch_gamepedia/images/d/da/"
            "Flex_Icon.svg/revision/latest?cb=20260217121748",
}

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_CACHE_DIR = os.path.join(REPO_ROOT, "server", ".cache", "role-icons")
DOWNLOAD_TIMEOUT_SECONDS = 15

# A card's icon is a plain white-to-light-grey glyph regardless of whether its card is
# idle (near-black) or lit up in the mode's colour — so "bright and barely saturated"
# is what the glyph is, on either background, and is applied to the screen crop and to
# the fetched art alike (there via its own alpha channel, not this test).
ICON_VALUE_MIN = 170
ICON_SATURATION_MAX = 60

# Where the row of four cards sits, as fractions of the whole screen. Measured off a real
# 1920x1080 capture of the Unranked queue screen: the four icons sat between x=215 and
# x=1180, y=477 and y=549. The first guess at this, made from a still screenshot rather
# than a real capture, put the left edge at 0.12 (230px) — 15px inside the real Tank icon
# — and the right edge at 0.88 (1690px), which reached past the row into the group
# panel's own player names and flags, close enough to real UI to pass this module's own
# brightness test and confuse the match. Widened well past both measured edges.
ROLE_ROW_REGION = (0.08, 0.38, 0.68, 0.53)

# Tried as fractions of each icon template's own native size, the same idea as
# `vision.SCALES`. Wider than that range because nothing here has yet measured how big
# the real icon renders relative to the fetched art, the way the hero grid icon's ratio
# to its catalog portrait has been.
SCALES = [0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 1.0, 1.15]

# A confident icon match, below which a card isn't trusted to be there at all.
ICON_MIN_SCORE = 0.5
# Four cards are always shown together; three found confidently (one perhaps sitting
# under a cursor or a controller-focus glow) is still enough to call this the role
# screen, the same tolerance `looks_like_roster` gives a hero grid.
MIN_ICONS_FOUND = 3
# How far apart two confidently-matched icons have to sit, as a multiple of their own
# average width, to count as two different cards rather than one coincidence several
# templates all landed on. Real cards measured about 2.5-3x apart on a real capture;
# this sits well under that floor rather than at it, on purpose — see
# `looks_like_role_select`'s own docstring for the three real screens that made this
# check necessary at all.
MIN_CARD_SPACING_RATIO = 1.5

# Where the checkbox and the card's own coloured body sit: (dx0, dy0, dx1, dy1) offsets
# from an icon's own centre-x and `row_geometry`'s own top-y, in units of its spacing —
# not of any icon's own matched width or height, and that distinction is load-bearing.
# The four icons are different shapes at different sizes — measured off a real capture,
# their own tight glyphs bounded at 58x72 (Tank), 63x57 (Damage), 72x72 (Support) and
# 72x68 (Flex) — so no per-icon size is "the" icon size to build another offset out of.
# Worse, `find_role_row` doesn't even recover any of those cleanly: on the same capture
# the matched boxes came back 90-94px tall for three of the four regardless of that
# 57-72px true range, because a resized template's edges partially correlate with the
# decorative ring drawn around every icon at more than one nearby scale, and the match is
# free to prefer whichever scores a fraction higher. Centre-to-centre spacing has no such
# ambiguity — matched centres landed within 14px of true on that capture, and consecutive
# gaps agreed with each other to better than 2px.
#
# The offsets below are measured from `row_geometry`'s own top, not from the icons'
# *true* top: those two disagree by 8-12px, because `row_geometry` averages whatever top
# `find_role_row` actually recovers per icon rather than the tight glyph's true edge, and
# it's the recovered number every one of these offsets is added to at runtime. Building
# the fraction from the true top and only meeting the recovered one at runtime was
# measured to land the checkbox interior on its own border on a second real frame from
# the same recording — close enough to work most of the time, and wrong in exactly the
# way this project's own docstrings keep warning that kind of gap is.
CHECKBOX_REGION_FROM_ICON = (-0.062, 0.707, 0.062, 0.831)
# The row's own cards measured 298-300px wide on centres 300px apart — right up against
# each other, separated by only their own borders — so this stays well inside one card's
# width rather than reaching for its measured edges: sampling a sliver of a lit-up
# neighbour wouldn't change which mode this reads (any card sampling that neighbour's hue
# still names the same mode) but would misattribute *which* card the colour is on, if
# that's ever asked of it later.
CARD_REGION_FROM_ICON = (-0.47, -0.37, 0.47, 0.94)

# An empty checkbox's *interior* — inside its own drawn border, which is trimmed off
# before this is measured, since the border is bright against either kind of card and
# would otherwise look like content on every card, checked or not — is a single flat
# colour with nothing else in it, the same way an empty hero-select slot is. A checkmark
# breaks that flatness real variance, the same test `EMPTY_STD_THRESHOLD` makes of a
# hero-select slot. Measured against a synthetic checkbox of the same proportions: 0.0
# for an empty interior regardless of the card's own brightness, 11.6-107 for a checked
# one depending how much the checkmark's colour happens to contrast with the card behind
# it — so this sits well clear of the empty case even at that weakest measured margin.
CHECKBOX_STD_MIN = 6.0
# How far in from the checkbox's own edges to look, as a fraction of its size, to stay
# clear of the border drawn around it. Wider than the border alone needs, on purpose: a
# card that has the keyboard/controller focus glow drawn around it — a real thing seen on
# a real capture, not a hypothetical — carries a bright highlighted edge close enough to
# an end card's own checkbox that 0.30 still let it in and read an untouched box as
# checked; 0.40 was measured clean on the same frame with the real checkmark still well
# inside it.
CHECKBOX_BORDER_INSET = 0.40

_SVG_TOKEN = re.compile(r"([MLZ])|(-?[0-9]*\.?[0-9]+(?:[eE]-?[0-9]+)?)")


class RoleCardHit:
    """One role's icon, found in the row, in screen coordinates."""

    __slots__ = ("role", "score", "x", "y", "w", "h")

    def __init__(self, role: str, score: float, x: int, y: int, w: int, h: int):
        self.role = role
        self.score = float(score)
        self.x, self.y, self.w, self.h = int(x), int(y), int(w), int(h)

    def __repr__(self):
        return "RoleCardHit(%s, %.2f, at %d,%d %dx%d)" % (
            self.role, self.score, self.x, self.y, self.w, self.h)


class RoleSelection:
    """Everything one look at the role-select screen produced.

    `on_screen` is False when the screen wasn't there to read, in which case `roles` is
    empty and `mode` is None — the same "looked, and there was nothing" shape
    `ScanResult.on_hero_select` uses for the other screen this project reads on demand.
    """

    __slots__ = ("roles", "mode", "hits", "on_screen")

    def __init__(self, roles: frozenset, mode, hits: dict, on_screen: bool):
        self.roles = frozenset(roles)
        self.mode = mode
        self.hits = hits                # role -> RoleCardHit, whether or not checked
        self.on_screen = bool(on_screen)

    @property
    def effective_role(self):
        """The single value the rest of this app speaks in.

        Exactly one checked role is unambiguous. Checking none is a screen caught
        mid-change, not a decision, so it comes back as None rather than a guess.
        Checking more than one is queueing flexibly across them, which is what
        Overwatch itself calls "flex" — this app has no way to say "tank or support
        but not damage" and doesn't invent one here.
        """
        if not self.roles:
            return None
        if len(self.roles) == 1:
            return next(iter(self.roles))
        return "flex"

    def __repr__(self):
        return "<RoleSelection %s mode=%s%s>" % (
            sorted(self.roles), self.mode or "?", "" if self.on_screen else " NOT ON SCREEN")


# ---------------------------------------------------------------- role art

class RoleIconStore:
    """Lazily downloads and caches one glyph mask per role.

    A glyph mask, not the art itself: matching needs only where the icon is light and
    where it isn't, so the raster icons are reduced to their own alpha channel and the
    SVG one is rasterised straight to a mask — see `_rasterize_svg`. That is also the
    one format every role can be compared in without deciding whether the fetched art
    is a photo, a WebP, or an SVG.
    """

    def __init__(self, cache_dir: str = DEFAULT_CACHE_DIR, log=None):
        self.cache_dir = cache_dir
        self.log = log or (lambda message: None)
        self._templates = {}            # role -> glyph mask, or None if hopeless

    def path_for(self, role: str) -> str:
        return os.path.join(self.cache_dir, role + (".svg" if role == "flex" else ".art"))

    def template(self, role: str):
        """The role's glyph mask, downloading and rasterising it the first time. None if
        the art can't be had — a role this scan will simply not look for."""
        if not QUEUE_ROLES_AVAILABLE:
            return None
        if role in self._templates:
            return self._templates[role]

        path = self.path_for(role)
        if not os.path.exists(path) and not self._download(role, path):
            self._templates[role] = None
            return None

        glyph = self._decode(role, path)
        self._templates[role] = glyph
        return glyph

    def _decode(self, role: str, path: str):
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError:
            return None
        glyph = _rasterize_svg(data) if role == "flex" else _alpha_glyph(data)
        if glyph is None:
            # A truncated download, or art wikia has since replaced with something this
            # can't decode. Drop it so the next call fetches fresh rather than failing
            # on the same bytes forever.
            self._forget_file(path)
            return None
        return _tight_crop(glyph)

    def _download(self, role: str, path: str) -> bool:
        url = ROLE_ICON_URLS.get(role)
        if not url:
            return False
        os.makedirs(os.path.dirname(path), exist_ok=True)
        partial = path + ".part"
        try:
            with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
                payload = response.read()
            with open(partial, "wb") as handle:
                handle.write(payload)
            os.replace(partial, path)
            return True
        except (urllib.error.URLError, OSError, ValueError) as problem:
            self.log("Couldn't fetch %s's icon: %s" % (role, problem))
            self._forget_file(partial)
            return False

    @staticmethod
    def _forget_file(path: str):
        try:
            os.remove(path)
        except OSError:
            pass

    def prefetch(self, roles=ROLE_ORDER) -> int:
        return sum(1 for role in roles if self.template(role) is not None)


def _alpha_glyph(data: bytes):
    """A raster icon's own alpha channel, which is already exactly the mask wanted —
    the glyph opaque, everything else transparent."""
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None or image.ndim < 3 or image.shape[2] < 4:
        return None
    return image[:, :, 3]


def _tight_crop(mask):
    """`mask` cropped to its own opaque pixels, with no surrounding margin left in.

    The four fetched icons don't carry the same amount of padding around their own
    glyph: Wikia's raster art leaves a wide margin inside its canvas, while the SVG this
    module rasterises itself does not, which was measured to matter a great deal — the
    same real card's Tank and Support icons matched a good 30-40% *taller* than their own
    real on-screen glyph, because the matched template was the glyph plus its margin, not
    the glyph itself, while Flex's tighter art matched close to true size. `reference_size`
    and every offset built on it assume a matched box's height means the same thing for
    every role; cropping the margin off here, once, at the source, is what makes that
    assumption hold rather than working around it separately for each inconsistency it
    would otherwise cause downstream.
    """
    ys, xs = np.nonzero(mask)
    if ys.size == 0:
        return mask
    return mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def _svg_polygons(path_d: str):
    """Every closed subpath in one SVG `d` string, as a list of (x, y) points.

    Only `M` and `Z` start a new polygon; `L` merely switches to drawing lines within the
    one already open; and each of these paths is one `M` followed by two `L`-ed points and
    a `Z`, so mistaking `L` for a break here would cut every triangle into two useless
    single- and double-point scraps — which is exactly the bug this once was.
    """
    polygons, current, numbers = [], [], []
    for command, number in _SVG_TOKEN.findall(path_d):
        if command in ("M", "Z"):
            if current:
                polygons.append(current)
            current = []
        elif not command:
            numbers.append(float(number))
            if len(numbers) == 2:
                current.append(numbers)
                numbers = []
    if current:
        polygons.append(current)
    return polygons


def _rasterize_svg(data: bytes, scale: float = 1.0):
    """Filling the one icon that only exists as an SVG, without pulling in a renderer.

    Wikia's Flex icon has no curves in it anywhere: every path is a chain of
    `M x,y L x,y x,y Z` triangles, which is what a tool that flattens Bezier curves to
    line segments leaves behind. That happens to be exactly what `cv2.fillPoly` wants,
    so this is the whole rasteriser — no dependency and no native library brought in for
    the one icon that needs it, at the cost of only ever working on straight-line SVGs.

    The default scale matters more than it looks: `SCALES` searches every template as a
    *fraction of its own native size*, on the assumption that the four templates' native
    sizes are all roughly comparable — true of the three raster icons, which arrive
    around 150-175px. Flex's viewBox is 146.5x138.2 *units*, not pixels, and rasterising
    it at a scale chosen for a clean debug picture (6, once) rather than for this made
    its native size ~880x830 — five times the others' — so the same `SCALES` list no
    longer meant the same real-world icon size for it as for the rest. Scale 1 keeps it
    in the same ballpark as the art it stands beside.
    """
    text = data.decode("utf-8", errors="ignore")
    view = re.search(r'viewBox="([^"]+)"', text)
    if view is None:
        return None
    try:
        _, _, width, height = (float(v) for v in view.group(1).split())
    except ValueError:
        return None
    if width <= 0 or height <= 0:
        return None

    canvas = np.zeros((int(height * scale) + 1, int(width * scale) + 1), np.uint8)
    for path_d in re.findall(r'd="([^"]*)"', text):
        for points in _svg_polygons(path_d):
            if len(points) < 3:
                continue
            pts = (np.array(points, dtype=np.float64) * scale).astype(np.int32)
            cv2.fillPoly(canvas, [pts], 255)
    return canvas if canvas.any() else None


# ---------------------------------------------------------------- capture

def capture():
    """The whole primary monitor, in colour. The row of cards can sit anywhere down the
    middle of the screen depending on resolution and UI scale, unlike the queue banner
    which is only ever found near the top — so unlike `queuevision.capture_strip`, this
    grabs the lot."""
    with mss.MSS() as sct:
        monitor = sct.monitors[1]
        shot = sct.grab(monitor)
        return cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)


def _fractional_region(fractions, w: int, h: int):
    fx0, fy0, fx1, fy1 = fractions
    return int(w * fx0), int(h * fy0), int(w * fx1), int(h * fy1)


def _region_from_icon(fractions, hit: RoleCardHit, geometry: "RowGeometry"):
    """A box near `hit`, as (dx0, dy0, dx1, dy1) offsets from its own centre-x and the
    row's own top-y, in units of `geometry.spacing` — see `row_geometry` for why the
    row's shared numbers, and not this one icon's own matched width or height, are the
    right ones to measure an offset in."""
    cx = hit.x + hit.w / 2.0
    dx0, dy0, dx1, dy1 = fractions
    return (int(cx + dx0 * geometry.spacing), int(geometry.top + dy0 * geometry.spacing),
            int(cx + dx1 * geometry.spacing), int(geometry.top + dy1 * geometry.spacing))


def _crop(bgr, box):
    x0, y0, x1, y1 = box
    h, w = bgr.shape[:2]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return None
    return bgr[y0:y1, x0:x1]


def glyph_mask(bgr):
    """"Bright and barely saturated" as a 0/255 mask — see the module docstring for why
    that, and not colour, is what a card's icon is made of on either kind of card."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    keep = (hsv[:, :, 2] >= ICON_VALUE_MIN) & (hsv[:, :, 1] <= ICON_SATURATION_MAX)
    return (keep.astype(np.uint8)) * 255


def _best_match(screen_mask, template_mask, scales=SCALES):
    """Where `template_mask` best fits in `screen_mask`, as (score, x, y, w, h). The same
    idea as `vision.best_match`, kept separate rather than imported so this module's own
    availability never depends on `vision`'s mouse-and-keyboard extras."""
    best = None
    for scale in scales:
        w = int(template_mask.shape[1] * scale)
        h = int(template_mask.shape[0] * scale)
        if w < 6 or h < 6 or w > screen_mask.shape[1] or h > screen_mask.shape[0]:
            continue
        resized = cv2.resize(template_mask, (w, h), interpolation=cv2.INTER_AREA)
        result = cv2.matchTemplate(screen_mask, resized, cv2.TM_CCOEFF_NORMED)
        _, score, _, location = cv2.minMaxLoc(result)
        if best is None or score > best[0]:
            best = (score, location[0], location[1], w, h)
    return best


# ---------------------------------------------------------------- detection

def find_role_row(bgr, templates: RoleIconStore) -> dict:
    """Every role whose icon was found in the row, as role -> `RoleCardHit`."""
    h, w = bgr.shape[:2]
    x0, y0, x1, y1 = _fractional_region(ROLE_ROW_REGION, w, h)
    mask = glyph_mask(bgr[y0:y1, x0:x1])

    hits = {}
    for role in ROLE_ORDER:
        template = templates.template(role)
        if template is None:
            continue
        found = _best_match(mask, template)
        if found is None:
            continue
        score, x, y, tw, th = found
        hits[role] = RoleCardHit(role, score, x0 + x, y0 + y, tw, th)
    return hits


def looks_like_role_select(hits: dict) -> bool:
    """Whether `hits` came from the real role-select row rather than four coincidences.

    Confidence alone isn't enough — see `vision.looks_like_roster` for the same
    argument made about the hero grid. What a lone confident icon can't fake is the
    *arrangement*: the four cards sit in one row, left to right, always in the same
    order, each a real distance from the next.

    That last clause was added after a full-video sweep, not guessed in advance: a
    victory screen's own art, a spawn room's wall monitors, and a control point's
    contest meter each fooled an earlier version of this into believing they were the
    role row. In every one of those, three or four templates had all landed on
    virtually the same small patch of the image — a generic bright shape weakly
    resembling a shield, a cross and a set of bullets all at once — rather than on four
    separate cards. Sorting them by x still came back in Tank-Damage-Support-Flex order
    purely because that is the order they were inserted in and their x's were all but
    tied, which the order check alone cannot tell apart from four cards genuinely read
    left to right. Real cards are never that close together: measured on a real
    capture, centre-to-centre spacing ran about 2.5-3x an icon's own width. Requiring at
    least a real fraction of that is what a coincidence landing on one spot cannot fake.
    """
    confident = {role: hit for role, hit in hits.items() if hit.score >= ICON_MIN_SCORE}
    if len(confident) < MIN_ICONS_FOUND:
        return False

    by_x = sorted(confident, key=lambda role: confident[role].x)
    if by_x != [role for role in ROLE_ORDER if role in confident]:
        return False

    centers = [hit.y + hit.h / 2.0 for hit in confident.values()]
    heights = [hit.h for hit in confident.values()]
    tolerance = 0.6 * (sum(heights) / len(heights))
    if (max(centers) - min(centers)) > tolerance:
        return False

    x_centers = [confident[role].x + confident[role].w / 2.0 for role in by_x]
    gaps = [b - a for a, b in zip(x_centers, x_centers[1:])]
    average_width = sum(confident[role].w for role in by_x) / len(by_x)
    return all(gap >= MIN_CARD_SPACING_RATIO * average_width for gap in gaps)


def card_checked(bgr, hit: RoleCardHit, geometry: "RowGeometry") -> bool:
    """Whether this card's checkbox has a checkmark in it.

    Sampled from *inside* the box's own border, not the whole box: the border itself is
    bright against either kind of card and would otherwise read as "content" whether or
    not anything is checked, which was measured to swing the plain std of the whole box
    from 13 to 79 on backgrounds alone, before a checkmark enters into it at all. Inside
    the border, an empty box is one flat colour and a checked one has a real glyph
    breaking that flatness — regardless of whether the card behind it is the idle card's
    near-black or the focused card's saturated fill, since the test is relative to
    itself rather than to a fixed colour.
    """
    x0, y0, x1, y1 = _region_from_icon(CHECKBOX_REGION_FROM_ICON, hit, geometry)
    inset_x = max(1, int((x1 - x0) * CHECKBOX_BORDER_INSET))
    inset_y = max(1, int((y1 - y0) * CHECKBOX_BORDER_INSET))
    patch = _crop(bgr, (x0 + inset_x, y0 + inset_y, x1 - inset_x, y1 - inset_y))
    if patch is None or patch.size == 0:
        return False
    return float(cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY).std()) >= CHECKBOX_STD_MIN


def card_hue(bgr, hit: RoleCardHit, geometry: "RowGeometry"):
    """(hue, coverage, spread) of this card's own body, in the same shape
    `queuevision.dominant_hue` reports for the queue banner — reused as-is, since a
    focused role card and a queue banner are both "a flat, saturated box" at heart."""
    patch = _crop(bgr, _region_from_icon(CARD_REGION_FROM_ICON, hit, geometry))
    if patch is None or patch.size == 0:
        return 0.0, 0.0, 360.0
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    keep = ((hsv[:, :, 1] >= queuevision.SATURATION_MIN) &
            (hsv[:, :, 2] >= queuevision.VALUE_MIN))
    hues = hsv[:, :, 0][keep].astype(np.float32) * 2.0
    return queuevision.dominant_hue(hues)


class RowGeometry:
    """The row's own stable numbers, shared by every card rather than read off any one
    of them — see `row_geometry` for why those are the numbers this measures."""

    __slots__ = ("top", "spacing")

    def __init__(self, top: float, spacing: float):
        self.top = float(top)
        self.spacing = float(spacing)

    def __repr__(self):
        return "RowGeometry(top=%.1f, spacing=%.1f)" % (self.top, self.spacing)


def row_geometry(hits: dict):
    """The row's own top-y and centre-to-centre spacing, or None with too little of the
    row confidently found to measure either.

    Not any one icon's own matched box: measured off a real capture, the four came back
    58x72 (Tank), 63x57 (Damage), 72x72 (Support) and 72x68 (Flex) as *true* tight
    glyphs — already different shapes at different sizes inside one shared slot, not four
    measurements of the same thing — and `find_role_row` didn't even recover those
    cleanly. On that same capture it matched three of the four 90-94px tall regardless of
    that 57-72px true spread, because a resized template's edges partially correlate with
    the decorative ring drawn around every icon at more than one nearby scale, and the
    match is free to prefer whichever scores a fraction higher — measured there as a
    0.014 margin between the true scale and the one actually chosen. An offset built on
    a number that unreliable would land almost anywhere.

    Spacing has no such ambiguity, on the same capture: matched centres landed within
    14px of true, and consecutive gaps agreed with each other to better than 2px, because
    *where* a template's best match sits is far better determined than *how big* the
    best-scoring resize of it turned out to be. Top is likewise taken across the row
    rather than from any one icon, since a real difference in glyph shape can shift where
    a bounding box starts as well as how tall it is.
    """
    confident = {role: hit for role, hit in hits.items() if hit.score >= ICON_MIN_SCORE}
    if len(confident) < 2:
        return None

    index = {role: i for i, role in enumerate(ROLE_ORDER)}
    ordered = sorted(confident, key=lambda role: index[role])
    centers = [(index[role], confident[role].x + confident[role].w / 2.0)
               for role in ordered]
    gaps = [(c2 - c1) / (i2 - i1) for (i1, c1), (i2, c2) in zip(centers, centers[1:])
            if i2 != i1]
    if not gaps:
        return None

    spacing = sum(gaps) / len(gaps)
    top = sum(hit.y for hit in confident.values()) / len(confident)
    return RowGeometry(top, spacing)


def scan(templates: RoleIconStore, modes=None, log=None) -> RoleSelection:
    """One full look at the role-select screen. See the module docstring for what each
    half of a card contributes to the answer."""
    log = log or (lambda message: None)
    if not QUEUE_ROLES_AVAILABLE:
        return RoleSelection(frozenset(), None, {}, False)

    bgr = capture()
    hits = find_role_row(bgr, templates)
    if not looks_like_role_select(hits):
        log("Role select isn't on screen")
        return RoleSelection(frozenset(), None, hits, False)

    geometry = row_geometry(hits)
    hues, tolerance = modes if modes is not None else queuevision.load_modes()
    roles, mode, best_coverage = set(), None, -1.0
    for role, hit in hits.items():
        if hit.score < ICON_MIN_SCORE:
            continue
        if card_checked(bgr, hit, geometry):
            roles.add(role)

        hue, coverage, spread = card_hue(bgr, hit, geometry)
        if (coverage > best_coverage and coverage >= queuevision.DOMINANT_COVERAGE_MIN
                and spread <= queuevision.HUE_STD_MAX):
            matched = queuevision.match_mode(hue, hues, tolerance)
            if matched:
                mode, best_coverage = matched, coverage

    return RoleSelection(frozenset(roles), mode, hits, True)
