"""Reading the hero-select screen, and clicking on it.

This is the first piece of the server that looks at the real game rather than waiting for
someone to press a button. It answers two questions about the "Select a Hero" screen —
which heroes are on the roster and where each one sits, and what every player has picked
so far — and it can act on the first answer by double-clicking a hero into place.

How it recognises a hero: `cv2.matchTemplate` with `TM_CCOEFF_NORMED`, sliding each
hero's catalog portrait over the screenshot and scoring how well the pixels line up. That
score is brightness-independent, which matters on a screen this animated. Everything else
here is about making that primitive fast enough and honest enough to trust:

- Only the strip of screen holding the roster is searched. Matching the whole screen was
  measurably worse, not just slower — the big splash-art panel behind the grid produced
  confident matches on the wrong hero.
- The search runs against a half-resolution copy. On a 1440p screen that turned ~830ms of
  matching into ~200ms, and costs a couple of pixels of precision on an icon some sixty
  wide, which is nothing next to the icon's own size.
- Templates are tried at several scales, because the roster icon is far smaller than the
  catalog portrait and the ratio moves with resolution and UI scale.

The awkward part is the *selected* hero. Overwatch draws a glow border and the hero's name
across whichever icon is currently chosen, which distorts exactly the pixels the matcher
wants; a badly-lit icon can score below an unrelated one elsewhere in the grid, so the
failure isn't a low number, it's a confident match on the wrong hero. The fix is to press
right three times, which walks the highlight somewhere else, and look again — see
`scan_roster`. Only the heroes that scored poorly are re-matched, since only one hero is
ever obscured at a time and re-running all fifty-three would double the cost of the scan
to re-answer fifty-two questions that were already answered well.

None of this is importable on a Mac, which is where this project is developed. Every
dependency below is optional and `VISION_AVAILABLE` says whether they arrived; callers
are expected to check it and fall back to driving the queue by hand.
"""
from __future__ import annotations

import time

try:                                    # pragma: no cover - trivial import guard
    import cv2
    import numpy as np
    import mss
    import pyautogui
    import pygetwindow
    VISION_AVAILABLE = True
except ImportError:                     # pragma: no cover - the Mac dev path
    cv2 = np = mss = pyautogui = pygetwindow = None
    VISION_AVAILABLE = False

WINDOW_TITLE = "Overwatch"
FOCUS_SETTLE_SECONDS = 0.5

# Fractions of the monitor rather than pixels, so a different resolution doesn't need a
# different build. Calibrated against a 2560x1440 capture of the hero-select screen.
GRID_REGION_FRACTION = (0.06, 0.71, 0.88, 0.146)        # x, y, w, h of the roster strip
SLOT_CENTER_FRACTION = (0.247, 0.559)                   # centre of the first player slot
SLOT_SPACING_FRACTION = 0.127                           # gap between slot centres
SLOT_SIZE_FRACTION = (0.045, 0.076)                     # crop taken around each centre
SLOT_COUNT = 5

# The roster icon is a fraction of the catalog portrait's size, and which fraction depends
# on resolution and UI scale, so the template is tried at each of these.
SCALES = [0.14, 0.17, 0.20, 0.24, 0.28]
SLOT_SCALES = [0.35, 0.4, 0.45, 0.5, 0.55, 0.6]
DOWNSCALE = 0.5

# Below this a grid match is treated as possibly-obscured and looked at again after the
# nudge. Set from measurement: a clean icon scores 0.75-0.9, an obscured one well under.
RECHECK_THRESHOLD = 0.65
# Below this a hero isn't considered to be on the roster at all.
GRID_MIN_SCORE = 0.60

# Whether the roster is on screen at all, which is *not* answerable per hero.
#
# `matchTemplate` always reports its best guess however poor the fit, so every hero
# "matches" something on any screen. Measured against a live game: with the roster open,
# 49 of 53 heroes scored at or above the recheck threshold and they landed on 52 distinct
# positions. Pointed at ordinary scenery with no roster in sight, 23 still cleared that
# same score — the best of them at 0.777 — but they piled onto only 31 distinct spots,
# because a handful of scenery features attract many templates at once.
#
# So the honest signal is structural: a real roster is *many* heroes each matching
# *somewhere different*. Both numbers sit far from either measurement, so this
# distinguishes a hero-select screen from a killcam or a spawn room rather than merely
# from a black frame. Getting this wrong is not cosmetic — it's the difference between
# telling the phone about a roster and inventing one, and between nudging the selection in
# a menu and typing arrow keys into a live match.
POSITION_BUCKET = 30
MIN_CONFIDENT_HEROES = 35
MIN_DISTINCT_POSITIONS = 40
# An unfilled player slot is a flat placeholder; a real portrait has far more going on.
# Measured 69.6 for a filled slot against 38-46 for empty ones on the same screen.
EMPTY_STD_THRESHOLD = 50.0
# Below this a slot's best match isn't worth reporting as a pick at all.
SLOT_MIN_SCORE = 0.45

NUDGE_PRESSES = 3
NUDGE_SETTLE_SECONDS = 0.35
MENU_SETTLE_SECONDS = 0.6
CLICK_SETTLE_SECONDS = 0.4


class RosterHit:
    """One hero found in the roster grid, in absolute screen coordinates."""

    __slots__ = ("hero_key", "score", "x", "y", "w", "h")

    def __init__(self, hero_key: str, score: float, x: int, y: int, w: int, h: int):
        self.hero_key = hero_key
        self.score = float(score)
        self.x, self.y, self.w, self.h = int(x), int(y), int(w), int(h)

    @property
    def center(self) -> tuple:
        return self.x + self.w // 2, self.y + self.h // 2

    def __repr__(self):
        return "RosterHit(%s, %.2f, at %d,%d)" % (self.hero_key, self.score, self.x, self.y)


class SlotPick:
    """What one player slot is showing. `hero_key` is None for an empty slot."""

    __slots__ = ("slot", "hero_key", "score", "is_self")

    def __init__(self, slot: int, hero_key, score: float, is_self: bool = False):
        self.slot = int(slot)
        self.hero_key = hero_key
        self.score = float(score)
        self.is_self = bool(is_self)

    def as_wire(self) -> dict:
        return {"slot": self.slot, "heroKey": self.hero_key, "isSelf": self.is_self}

    def __repr__(self):
        return "SlotPick(%d, %s, %.2f, self=%s)" % (
            self.slot, self.hero_key, self.score, self.is_self)


class ScanResult:
    """Everything one look at the screen produced.

    `on_hero_select` is False when the roster wasn't there to read, in which case the
    other two are empty — an answer of "I looked and there's nothing", which is different
    from the caller never having looked at all.
    """

    def __init__(self, roster: dict, picks: list, on_hero_select: bool = True):
        self.roster = roster            # hero key -> RosterHit
        self.picks = picks              # list of SlotPick, occupied slots only
        self.on_hero_select = on_hero_select

    @property
    def available_hero_keys(self) -> list:
        return sorted(self.roster)

    def taken_hero_keys(self, my_hero_key=None) -> list:
        """The heroes other players are on — what the app greys out.

        Whose pick is "mine" comes from the slot flagged during the scan; when nothing
        could be flagged, fall back to the hero the server already believes is ours rather
        than guessing at a slot and crediting someone else's pick to the player.
        """
        mine = next((pick for pick in self.picks if pick.is_self), None)
        if mine is not None:
            return [p.hero_key for p in self.picks if p is not mine and p.hero_key]
        return [p.hero_key for p in self.picks
                if p.hero_key and p.hero_key != my_hero_key]


# ---------------------------------------------------------------- the window

def focus_game_window(log=None) -> bool:
    """Brings Overwatch to the front. Everything below reads the screen and sends real
    clicks and keystrokes, so they land wherever the focus is — which is the terminal or
    the control panel unless this has run first."""
    if not VISION_AVAILABLE:
        return False
    log = log or (lambda message: None)
    try:
        matches = pygetwindow.getWindowsWithTitle(WINDOW_TITLE)
    except Exception as problem:        # pragma: no cover - platform-specific failure
        log("Couldn't look for the Overwatch window: %s" % problem)
        return False
    if not matches:
        log("No Overwatch window is open.")
        return False

    window = matches[0]
    try:
        if window.isMinimized:
            window.restore()
        window.activate()
    except Exception as problem:        # pragma: no cover - platform-specific failure
        log("Couldn't focus the Overwatch window: %s" % problem)
        return False
    time.sleep(FOCUS_SETTLE_SECONDS)
    return True


def capture():
    """The whole primary monitor, as (colour BGR, grayscale)."""
    with mss.MSS() as sct:
        monitor = sct.monitors[1]
        shot = sct.grab(monitor)
        color = cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)
    return color, cv2.cvtColor(color, cv2.COLOR_BGR2GRAY)


# ---------------------------------------------------------------- matching

def _resized(gray, factor: float):
    return cv2.resize(gray, (int(gray.shape[1] * factor), int(gray.shape[0] * factor)),
                      interpolation=cv2.INTER_AREA)


def best_match(screen_gray, template_gray, scales=SCALES, downscale: float = DOWNSCALE):
    """Where `template_gray` best fits in `screen_gray`, as (score, x, y, w, h).

    Searched at half size and scaled back up — see the module docstring for why that
    trade is worth it. Returns None when the template can't fit at any scale.
    """
    small = _resized(screen_gray, downscale)
    best = None
    for scale in scales:
        w = int(template_gray.shape[1] * scale * downscale)
        h = int(template_gray.shape[0] * scale * downscale)
        if w < 6 or h < 6 or w > small.shape[1] or h > small.shape[0]:
            continue
        result = cv2.matchTemplate(small, cv2.resize(template_gray, (w, h),
                                                     interpolation=cv2.INTER_AREA),
                                   cv2.TM_CCOEFF_NORMED)
        _, score, _, location = cv2.minMaxLoc(result)
        if best is None or score > best[0]:
            best = (score, location[0], location[1], w, h)
    if best is None:
        return None
    score, x, y, w, h = best
    back = 1.0 / downscale
    return score, int(x * back), int(y * back), int(w * back), int(h * back)


def grid_region(gray) -> tuple:
    h, w = gray.shape
    fx, fy, fw, fh = GRID_REGION_FRACTION
    x0, y0 = int(w * fx), int(h * fy)
    return x0, y0, x0 + int(w * fw), y0 + int(h * fh)


def slot_region(index: int, gray) -> tuple:
    h, w = gray.shape
    cx = SLOT_CENTER_FRACTION[0] * w + index * SLOT_SPACING_FRACTION * w
    cy = SLOT_CENTER_FRACTION[1] * h
    half_w, half_h = SLOT_SIZE_FRACTION[0] * w / 2, SLOT_SIZE_FRACTION[1] * h / 2
    return (max(0, int(cx - half_w)), max(0, int(cy - half_h)),
            min(w, int(cx + half_w)), min(h, int(cy + half_h)))


def looks_like_roster(hits: dict) -> bool:
    """Whether `hits` came from a real hero-select roster or from ordinary scenery.

    See the constants above for the measurements behind the two thresholds. Judged on the
    unfiltered hits on purpose: how badly the *worst* matches are spread is exactly the
    signal, and filtering to the good ones first would throw it away.
    """
    confident = sum(1 for hit in hits.values() if hit.score >= RECHECK_THRESHOLD)
    spread = {(hit.x // POSITION_BUCKET, hit.y // POSITION_BUCKET) for hit in hits.values()}
    return confident >= MIN_CONFIDENT_HEROES and len(spread) >= MIN_DISTINCT_POSITIONS


def _match_grid(gray, templates, hero_keys) -> dict:
    x0, y0, x1, y1 = grid_region(gray)
    strip = gray[y0:y1, x0:x1]
    found = {}
    for key in hero_keys:
        template = templates.template(key)
        if template is None:
            continue
        hit = best_match(strip, template)
        if hit is None:
            continue
        score, x, y, w, h = hit
        found[key] = RosterHit(key, score, x0 + x, y0 + y, w, h)
    return found


# ---------------------------------------------------------------- scanning

def scan_player_slots(gray, templates, hero_keys) -> list:
    """What each player slot is showing, occupied slots only.

    These hexagons are the trustworthy source for who picked what: unlike the roster grid
    they're drawn plain, with no glow or name over them, so a straight match is reliable
    here in a way it isn't down in the grid.

    An empty slot is a flat placeholder, so its pixels barely vary; checking that first
    costs one standard deviation and skips fifty-three matches for a slot nobody is in.
    """
    picks = []
    for index in range(SLOT_COUNT):
        x0, y0, x1, y1 = slot_region(index, gray)
        patch = gray[y0:y1, x0:x1]
        if patch.size == 0 or float(patch.std()) < EMPTY_STD_THRESHOLD:
            continue

        best_key, best_score = None, -1.0
        for key in hero_keys:
            template = templates.template(key)
            if template is None:
                continue
            hit = best_match(patch, template, scales=SLOT_SCALES, downscale=1.0)
            if hit and hit[0] > best_score:
                best_key, best_score = key, hit[0]
        if best_key and best_score >= SLOT_MIN_SCORE:
            picks.append(SlotPick(index + 1, best_key, best_score))
    return picks


def resolve_self_slot(picks: list, my_hero_key=None) -> list:
    """Flags which slot is the player's own.

    The player is *not* reliably slot one — role queue orders the slots by role, so the
    position moves with the role queued for. Rather than assume, match against the hero
    the server already knows we picked: if exactly one occupied slot is on that hero, it's
    ours. Anything less certain leaves every slot unflagged, because the clients can live
    with no "you" marker but not with one pointing at a stranger.
    """
    for pick in picks:
        pick.is_self = False
    if not my_hero_key:
        return picks
    candidates = [pick for pick in picks if pick.hero_key == my_hero_key]
    if len(candidates) == 1:
        candidates[0].is_self = True
    return picks


def scan(templates, hero_keys, my_hero_key=None, nudge: bool = True, log=None) -> ScanResult:
    """One full look at the hero-select screen.

    The player slots are read from the *first* capture, before any nudge. Pressing right
    doesn't merely move a cursor in this UI — it moves the actual selection, and the
    player's own slot follows it. Reading the slots first means what's reported is what
    the player had chosen when asked, not what the scan itself just did to them.
    """
    log = log or (lambda message: None)
    hero_keys = list(hero_keys)

    _, gray = capture()
    hits = _match_grid(gray, templates, hero_keys)

    # Before anything else, and before touching a key: is the roster even there? The nudge
    # below presses arrow keys, and pressing those into a live match instead of a menu is
    # exactly the kind of thing this check exists to prevent.
    if not looks_like_roster(hits):
        log("Hero select isn't on screen")
        return ScanResult({}, [], on_hero_select=False)

    roster = {key: hit for key, hit in hits.items() if hit.score >= GRID_MIN_SCORE}
    picks = resolve_self_slot(scan_player_slots(gray, templates, hero_keys), my_hero_key)

    if nudge:
        unsure = [key for key in hero_keys
                  if key not in roster or roster[key].score < RECHECK_THRESHOLD]
        if unsure:
            log("Looking again at %d hero%s the highlight may have been sitting on"
                % (len(unsure), "" if len(unsure) == 1 else "es"))
            _nudge_selection()
            _, after = capture()
            for key, hit in _match_grid(after, templates, unsure).items():
                if hit.score < GRID_MIN_SCORE:
                    continue
                if key not in roster or hit.score > roster[key].score:
                    roster[key] = hit

    return ScanResult(roster, picks)


def _nudge_selection():
    for _ in range(NUDGE_PRESSES):
        pyautogui.press("right")
    time.sleep(NUDGE_SETTLE_SECONDS)


def reopen_hero_menu():
    """`H` is Overwatch's own shortcut back to the hero list. Once a hero is locked the
    roster isn't on screen to be matched against, so there's nothing to scan and nothing
    to click until this puts it back."""
    pyautogui.press("h")
    time.sleep(MENU_SETTLE_SECONDS)


# ---------------------------------------------------------------- acting

def double_click(point: tuple):
    pyautogui.moveTo(point[0], point[1])
    pyautogui.doubleClick()
    time.sleep(CLICK_SETTLE_SECONDS)


def select_hero(templates, hero_keys, hero_key: str, roster=None, log=None) -> bool:
    """Puts the player on `hero_key` by double-clicking its icon.

    `roster` is the last scan's positions, reused when it has what we need so a pick
    doesn't pay for a fresh scan. When it doesn't — most often because a hero is already
    locked in and the roster isn't on screen at all — `H` brings the list back and the
    screen is read again before clicking.
    """
    log = log or (lambda message: None)
    hit = (roster or {}).get(hero_key)

    if hit is None:
        log("No position for %s — reopening the hero list" % hero_key)
        reopen_hero_menu()
        _, gray = capture()
        # Matched across the whole catalog rather than just the hero we want, because a
        # single template always "matches" something: the only way to know the roster came
        # back is to look at how all of them landed.
        hits = _match_grid(gray, templates, hero_keys)
        if not looks_like_roster(hits):
            log("The hero list didn't come back — not clicking blind")
            return False
        hit = hits.get(hero_key)
        if hit is not None and hit.score < GRID_MIN_SCORE:
            hit = None

    if hit is None:
        log("Couldn't find %s on screen" % hero_key)
        return False

    double_click(hit.center)
    return True
