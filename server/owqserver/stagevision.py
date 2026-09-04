"""Naming which screen Overwatch is showing, from one frame, with no memory of the last.

Everything else in this package answers one narrow question — is there a queue banner,
is the role screen up, is the hero grid on screen — because each of those is genuinely a
different kind of look at the screen, calibrated and tested on its own. This module does
not replace any of them. It is the dispatcher a caller reaches for when the actual
question is broader: "what, of everything this package knows how to recognise, is on
screen right now" — asked once, cheaply, with no assumption about what came before.

That statelessness is the point and the constraint at once. `queuewatch.QueueTracker`
gets to trust a gap because it remembers the frame before it; a hidden-blind grace period
only makes sense across time. Nothing here keeps that memory, so nothing here can lean on
it — every stage below has to stand on what a single frame actually shows.

Ordered cheapest and most self-certain first, because most frames of a real session are
just gameplay, and the honest, resource-efficient answer for most of them has to be
reached without doing much work at all:

1. The queue banner (`queuevision`) — a strip capture, already as cheap as this project
   gets.
2. `looks_like_map_vote` and `looks_like_prepare` — a couple of small fixed crops each,
   no template matching, described below.
3. The role-select row (`queueroles`) — four icon templates over one bounded region.
4. The hero-select roster (`vision`) — fifty-odd templates over the whole grid strip,
   the most expensive thing this package does. Opt-in only: pass `templates` and
   `hero_keys` to `classify` to have it tried, and it is tried last, after everything
   cheaper has already said no.

**The map vote screen** is found by two ornaments, not by its text. "VOTE FOR A MAP <N>"
sits at the top centre with the map count in a colour and a font this project has no
license to ship a template for, but the two diagonal amber light-beams flanking the title
are a plain, saturated, fixed-position shape - and specifically their *inner* ends, which
stay put at 0.365-0.391 and 0.609-0.635 of the width no matter how many digits the count
needs, while a title in a different language pushes each beam's *outer* end further out.
Measured across three real captures, both beams' inner segments landed in the same 10px
band pixel-for-pixel every time.

**Prepare to attack** is found by its timer, not its label. The label changes with the
mode and the side - "Prepare to Attack", presumably "Prepare to Defend" and others never
captured - but the countdown next to it is drawn in the same amber as the vote screen's
count, in the same fixed place, regardless of which label is showing or how long the map
takes to say. Two real captures with different remaining times agreed on that box within
a few pixels. Digits are not parsed here the way `queuedigits` parses the queue's — only
whether digit-shaped marks are sitting where the countdown always sits — which answers
"is this the screen" without yet answering "how many seconds are left"; teaching this
timer its own digit set the way `queuedigits` taught itself the queue's is future work,
not done here.

That timer alone is not enough, and a full-video sweep is the only reason this module
knows it: the *ongoing match clock* — "Escort the Payload 6:27", counting down a live
round, not the twenty seconds before one — is drawn in the exact same amber, in the exact
same place, for the entire match. Left unchecked this reads a real game in progress as
"prepare" for as long as the round lasts, not just the setup before it. What actually
separates them is the objective tracker underneath — a payload's progress bar, a point's
capture ring — which the pre-match screen hasn't drawn yet and the live match always has;
`PREPARE_CLUTTER_REGION` is a coarse "is something busy sitting there" test built on
that. It closes most of the gap, verified by that same sweep, but not all of it: an
escort bar is colourful enough to push well clear of the pre-match range, while a
control-point's tracker is compact enough that a quiet, uncontested moment can still slip
under it — measured on one real capture, for tens of seconds at a stretch. Neither
reading the amber digits' actual value nor recognising the label's own text would have
this problem, and either is real future work; what stands here is deliberately the
cheaper, imperfect answer, with its own failure mode measured and written down rather
than assumed away. A caller that cannot tolerate the residue should require this to
follow a `HERO_SELECT` reading within the pre-match window's own short duration before
trusting it — real context this module intentionally doesn't keep for itself.

Nothing here focuses a window, moves the mouse or presses a key. It only looks.
"""
from __future__ import annotations

try:                                    # pragma: no cover - trivial import guard
    import cv2
    import numpy as np
    import mss
    STAGE_VISION_AVAILABLE = True
except ImportError:                     # pragma: no cover - the Mac dev path
    cv2 = np = mss = None
    STAGE_VISION_AVAILABLE = False

from . import queuevision

# ---------------------------------------------------------------- stage names

SEARCHING = "searching"          # a queue banner, not yet a match
GAME_FOUND = "gameFound"         # a queue banner's green check
ROLE_SELECT = "roleSelect"
MAP_VOTE = "mapVote"
PREPARE = "prepare"
HERO_SELECT = "heroSelect"
UNKNOWN = "unknown"              # nothing recognised — ordinary gameplay, most of the time

# ---------------------------------------------------------------- map vote

# The inner end of each amber beam flanking "VOTE FOR A MAP", as (x0, y0, x1, y1)
# fractions of the screen. Measured across three real captures: the outer end moves with
# how many digits the vote count needs, but the *inner* end — the one closest to the
# count, which is what these regions cover — sat at x=750 (left) and x=1172 (right) on
# every one of them, on a 1920-wide capture, with the bar itself exactly 10px tall,
# y=168-177. Only this stable inner slice is used; the beam's own length is not.
#
# The beam is drawn on a slight diagonal, not measured at first: a first guess at the
# vertical bounds padded a few pixels above the bar for safety, and those pixels turned
# out to hold none of it at all — coverage fell from a clean 1.0 at the bar's own top row
# to 0.70 by the time three empty rows were averaged in. y0 sits on the bar's first row
# for exactly that reason, not a row above it.
MAP_VOTE_LEFT_REGION = (0.365, 0.1556, 0.391, 0.1648)
MAP_VOTE_RIGHT_REGION = (1.0 - 0.391, 0.1556, 1.0 - 0.365, 0.1648)

# The beam's own measured hue was 10-25 (OpenCV's halved 0-179 scale) at saturation and
# value well above ordinary scenery — the same "interface, not scenery" reasoning
# `queuevision.SATURATION_MIN`/`VALUE_MIN` make, tightened because this only ever has to
# separate one specific ornament from everything else, not a whole banner from a room.
AMBER_HUE_RANGE = (8, 26)
AMBER_SATURATION_MIN = 130
AMBER_VALUE_MIN = 130
# How much of each small region has to be that amber for it to be the beam and not a
# coincidence. Both real captures measured at or near 1.0; this leaves considerable room
# without opening the door to a stray warm-coloured pixel or two.
MAP_VOTE_COVERAGE_MIN = 0.7

# How much of that same amber is allowed in a strip just above and just below each
# beam — nearly none, which is what tells an actual beam from an amber-toned *scene*.
# A full-video sweep found this necessary, not this module's first instinct: an orange
# VFX flash and a gold-toned room each lit up both beam regions at once, coverage 0.5-1.0
# in each, because the *entire screen* was that colour, symmetry included — the very
# check meant to rule out coincidence doesn't, once the coincidence is screen-wide rather
# than confined to one spot. On every real vote screen, the strip immediately outside the
# beam's own 10px band is the menu's plain dark background: 0.0-0.046 coverage. On both
# amber-toned false frames, the strip right next to the "beam" was just as amber as the
# beam itself: 0.54-1.0. That gap, not the beam's own coverage, is what actually
# distinguishes a beam from a colour wash.
MAP_VOTE_ISOLATION_MAX = 0.25


def _amber_coverage(bgr) -> float:
    if bgr is None or bgr.size == 0:
        return 0.0
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    keep = ((hsv[:, :, 0] >= AMBER_HUE_RANGE[0]) & (hsv[:, :, 0] <= AMBER_HUE_RANGE[1]) &
            (hsv[:, :, 1] >= AMBER_SATURATION_MIN) & (hsv[:, :, 2] >= AMBER_VALUE_MIN))
    return float(keep.mean())


def _looks_like_a_beam(frame_bgr, region) -> bool:
    h, w = frame_bgr.shape[:2]
    x, y, bw, bh = queuevision._fractional_box(region, w, h)
    body = frame_bgr[y:y + bh, x:x + bw]
    if _amber_coverage(body) < MAP_VOTE_COVERAGE_MIN:
        return False

    # A real beam is a thin, isolated bar — checked by looking just outside its own
    # band, above and below, for the plain dark menu background a real one always sits
    # against. Neither strip is allowed to be nearly as amber as the beam itself.
    above = frame_bgr[max(0, y - bh * 2):y, x:x + bw]
    below = frame_bgr[y + bh:y + bh * 3, x:x + bw]
    return (_amber_coverage(above) <= MAP_VOTE_ISOLATION_MAX and
            _amber_coverage(below) <= MAP_VOTE_ISOLATION_MAX)


def looks_like_map_vote(frame_bgr) -> bool:
    """Whether the map-vote screen's title ornament is on screen.

    Both beams have to show, symmetric about the screen's own centre, because a lone
    warm-coloured object sitting in only one of the two positions is exactly the kind of
    coincidence one fixed crop alone cannot rule out — the same reasoning
    `queueroles.looks_like_role_select` applies to its own row of four. Each beam also
    has to actually be a beam — see `_looks_like_a_beam` and `MAP_VOTE_ISOLATION_MAX` for
    the real screens that made that a separate check rather than an assumption.
    """
    return (_looks_like_a_beam(frame_bgr, MAP_VOTE_LEFT_REGION) and
            _looks_like_a_beam(frame_bgr, MAP_VOTE_RIGHT_REGION))


# ---------------------------------------------------------------- prepare to attack

# Where the countdown next to "Prepare to Attack" (or whatever the label says) sits, as a
# fraction of the screen — fixed regardless of the label's own length or language, unlike
# the map vote's title. Measured across two real captures showing different times
# remaining (0:32 and 0:40): both landed within a few pixels of x=1035-1069, y=43-55 on a
# 1920x1080 capture. Widened generously past both.
PREPARE_TIMER_REGION = (0.52, 0.032, 0.575, 0.058)

# The digits are the same amber as the vote screen's count — not the white
# `queuevision`/`queuedigits` read everywhere else — so this needs its own bright-mark
# test rather than reusing `queuedigits.TEXT_SATURATION_MAX`, which that amber fails by a
# wide margin (measured saturation 150-160 against a ceiling of 60).
PREPARE_VALUE_MIN = 150
# A digit stroke has real area; a compression artefact or a stray pixel does not.
PREPARE_MIN_MARK_AREA = 4
# Two digits and a colon is the least a running clock ever shows here (the countdown
# never reaches ten minutes) — fewer marks than that is not a timer, it's noise.
PREPARE_MIN_MARKS = 3

# The objective tracker — the payload's progress bar, a control point's capture ring,
# whichever the mode draws — sits just under the label for the rest of the match, in the
# same fixed place, once the match itself actually starts. A first version of this check
# had no way to tell that tracker's own "M:SS" — the *match* clock counting down a round,
# not the pre-match countdown — from the real thing, and a full-video sweep caught it
# firing for the entire length of a real match because of it, not just the twenty-odd
# seconds before one. This region and the threshold below it are what that sweep taught:
# measured across four real "Prepare to Attack" captures the busy tracker hasn't been
# drawn under yet, coverage sat at 0.013-0.064; measured across six real in-match
# captures where it had, 0.124-0.351. The gap between those, not any single number in it,
# is the whole reason this check exists as a second, separate test rather than a tighter
# version of the first one.
PREPARE_CLUTTER_REGION = (0.354, 0.069, 0.651, 0.157)
PREPARE_CLUTTER_MAX = 0.09


def looks_like_prepare(frame_bgr) -> bool:
    """Whether a countdown is sitting where "Prepare to Attack" always puts one, and the
    match itself hasn't actually started yet.

    The first half answers *is the screen showing that banner*, not *how many seconds
    are left* — it looks for digit-shaped marks in the timer's own fixed spot rather than
    reading them, the way `queuedigits.segment` reads the queue's. Teaching this timer
    its own digits the same way is future work; see the module docstring for why.

    The second half is the one a real sweep, not a guess, put here — see
    `PREPARE_CLUTTER_REGION`'s own comment for the failure it was measured against.
    """
    h, w = frame_bgr.shape[:2]
    x, y, tw, th = queuevision._fractional_box(PREPARE_TIMER_REGION, w, h)
    body = frame_bgr[y:y + th, x:x + tw]
    if body.size == 0:
        return False

    hsv = cv2.cvtColor(body, cv2.COLOR_BGR2HSV)
    mask = (hsv[:, :, 2] >= PREPARE_VALUE_MIN).astype(np.uint8)
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    marks = sum(1 for index in range(1, count) if stats[index][4] >= PREPARE_MIN_MARK_AREA)
    if marks < PREPARE_MIN_MARKS:
        return False

    cx, cy, cw, ch = queuevision._fractional_box(PREPARE_CLUTTER_REGION, w, h)
    clutter = frame_bgr[cy:cy + ch, cx:cx + cw]
    if clutter.size == 0:
        return True
    clutter_hsv = cv2.cvtColor(clutter, cv2.COLOR_BGR2HSV)
    busy = ((clutter_hsv[:, :, 1] >= 80) & (clutter_hsv[:, :, 2] >= 80)).mean()
    return busy <= PREPARE_CLUTTER_MAX


# ---------------------------------------------------------------- fingerprinting

# A coarse average-hash of the whole frame, in pixels, before it's packed into bits —
# small enough that computing it costs nothing next to the fifty-odd templates it exists
# to let a caller skip. 96 bits is generous for a hash whose only job is telling "the
# same static menu" apart from "gameplay, which moves every frame" — see `fingerprint`.
FINGERPRINT_SIZE = (12, 8)
# How many of those 96 bits may differ and still count as "the same screen". Wide enough
# to absorb a soft idle animation or a particle drifting past; not so wide that it would
# blur two actually-different screens of similar overall brightness into one hash.
FINGERPRINT_HAMMING_TOLERANCE = 3


def fingerprint(frame_bgr) -> int:
    """A cheap, tolerant hash of `frame_bgr`'s overall look, as a single int.

    Not a content hash — an *average* hash, which asks only whether each patch of a
    heavily downsampled copy of the frame is lighter or darker than that copy's own mean.
    That is exactly tolerant to the kind of change a static menu has from one poll to the
    next — an idle character sway, a soft glow pulsing — and exactly intolerant to the
    kind of change gameplay has, because the camera moves on almost every frame of it.
    A menu screen keeps producing close to the same hash for as long as it sits there;
    ordinary gameplay essentially never repeats one.
    """
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, FINGERPRINT_SIZE, interpolation=cv2.INTER_AREA)
    bits = small > small.mean()
    value = 0
    for bit in bits.flatten():
        value = (value << 1) | int(bit)
    return int(value)


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


class StageCache:
    """Remembers which stage a screen's *look* was last confirmed to be, so a caller
    polling continuously — `queuewatch.QueueWatcher` is the shape of caller this is for —
    doesn't pay for `classify`'s one expensive check, the hero grid's fifty-odd templates,
    on every single frame of a hero-select screen nobody has touched in ten seconds.

    Deliberately narrow: this caches *which stage a look like this one turned out to be*,
    never a stage's own metadata. A cached hero-select hit is still the roster
    `vision.looks_like_roster` actually confirmed — which heroes are on it does not
    change while the screen is up — but `vision.scan_player_slots` is run fresh by
    whatever calls this every time regardless, because a pick changing mid-selection is
    real information a stale answer would get wrong, in exactly the way barely-changed
    pixels this hash is built to shrug off are not.
    """

    def __init__(self, capacity: int = 32):
        self.capacity = capacity
        self._entries = []          # [(fingerprint, StageResult), ...], oldest first

    def get(self, print_: int):
        for stored, result in reversed(self._entries):
            if _hamming(print_, stored) <= FINGERPRINT_HAMMING_TOLERANCE:
                return result
        return None

    def put(self, print_: int, result: "StageResult"):
        self._entries.append((print_, result))
        if len(self._entries) > self.capacity:
            self._entries.pop(0)


# ---------------------------------------------------------------- the dispatcher

class StageResult:
    """One classifier's answer for one frame."""

    __slots__ = ("stage", "detail")

    def __init__(self, stage: str, detail=None):
        self.stage = stage
        self.detail = detail          # whatever that stage's own detector returned

    @property
    def recognised(self) -> bool:
        return self.stage != UNKNOWN

    def __repr__(self):
        return "<StageResult %s%s>" % (self.stage, " %r" % (self.detail,) if self.detail
                                       is not None else "")


def classify(frame_bgr, modes=None, role_templates=None, hero_templates=None,
            hero_keys=None, cache: "StageCache | None" = None) -> StageResult:
    """Which of this package's known stages `frame_bgr` shows, cheapest first.

    `modes` is `queuevision.load_modes()`'s pair. `role_templates` is a
    `queueroles.RoleIconStore`; passing it tries the role-select row. `hero_templates`
    and `hero_keys` are `vision.py`'s template store and catalogue keys; passing both
    tries the hero-select roster — the one check here expensive enough that it is never
    tried unless asked for by name, and the one `cache` (a `StageCache`) is for: pass one
    and a hero-select confirmation is skipped in favour of the cached answer whenever
    this frame's `fingerprint` is close enough to one already confirmed.

    Statelessness cuts both ways. This will happily call two consecutive frames of the
    same real moment two different things — a queue banner mid-animation might be
    `UNKNOWN` for a frame and `SEARCHING` the next, the way `queuewatch.ENTER_FRAMES`
    already expects and absorbs. A caller that wants that smoothed over needs a tracker
    with memory, which is what `queuewatch` already is for the queue banner specifically;
    this is the part with none, on purpose — `cache` included, which only ever skips
    *redoing* a check, never changes what the check itself would have answered.
    """
    if not STAGE_VISION_AVAILABLE:
        return StageResult(UNKNOWN)

    h, w = frame_bgr.shape[:2]

    strip = frame_bgr[:queuevision.strip_height(h), :]
    hit = queuevision.find_banner(strip, w, h, modes)
    if hit is not None:
        return StageResult(GAME_FOUND if hit.game_found else SEARCHING, hit)

    if looks_like_map_vote(frame_bgr):
        return StageResult(MAP_VOTE)

    if looks_like_prepare(frame_bgr):
        return StageResult(PREPARE)

    if role_templates is not None:
        from . import queueroles
        hits = queueroles.find_role_row(frame_bgr, role_templates)
        if queueroles.looks_like_role_select(hits):
            geometry = queueroles.row_geometry(hits)
            hues, tolerance = modes if modes is not None else queuevision.load_modes()
            roles = {role for role, h in hits.items() if h.score >= queueroles.ICON_MIN_SCORE
                    and queueroles.card_checked(frame_bgr, h, geometry)}
            return StageResult(ROLE_SELECT, queueroles.RoleSelection(
                frozenset(roles), None, hits, True))

    if hero_templates is not None and hero_keys is not None:
        print_ = fingerprint(frame_bgr) if cache is not None else None
        cached = cache.get(print_) if cache is not None else None
        if cached is not None and cached.stage == HERO_SELECT:
            return cached

        from . import vision
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        grid_hits = vision._match_grid(gray, hero_templates, hero_keys)
        if vision.looks_like_roster(grid_hits):
            result = StageResult(HERO_SELECT, grid_hits)
            if cache is not None:
                cache.put(print_, result)
            return result

    return StageResult(UNKNOWN)
