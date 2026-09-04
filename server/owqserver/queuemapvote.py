"""Reading which real maps are actually on the vote screen.

`stagevision.looks_like_map_vote` only ever answers "is this screen up" — it was built to
be cheap enough to poll constantly, from two small ornaments that say nothing about which
maps are actually being offered. This module is the other half, meant to be asked once,
the moment that screen is first seen: which three (or two — the option count itself is
never assumed) cards are showing, and which real map each one names.

Two more direct routes were tried first and both failed on real captures before this one
was written. Matching each card's own thumbnail against the catalog's promotional
screenshot doesn't work — measured directly, they are different renders of the map
entirely, not a crop of the same source art, so pixel correlation between them means
nothing. Rendering each candidate map's name in a stand-in font and correlating that
against the real name text doesn't work either, even once the *right* font (Overwatch's
own, Big Noodle Too) was found and used: a whole word squashed to fit another word's
bounding box loses exactly the shape that would have told them apart, and the game's own
tight kerning fuses adjacent letters together often enough that segmenting one glyph at a
time before comparing isn't reliable either. What actually reads the text cleanly, no
font-matching or hand-built segmentation required, is a real OCR model — verified directly
against all four map-vote screens in this project's own recording, every card read
correctly, one card's crop clipped a trailing letter and the catalog match below is what a
clipped read like that is for tolerating.

`rapidocr-onnxruntime` is the one dependency this costs, and it is a real one — no system
binary to install alongside it (unlike `pytesseract`, which wants a separate Tesseract
install), but a meaningfully larger download than anything else optional here. It only
matters once per real vote screen, not on any polling cadence, so the ~1s it takes per
card is not something a caller needs to budget for the way `vision`'s hero-grid match is.
"""
from __future__ import annotations

try:                                    # pragma: no cover - trivial import guard
    import cv2
    import numpy as np
    import mss
    from rapidocr_onnxruntime import RapidOCR
    MAP_VOTE_READING_AVAILABLE = True
except ImportError:                     # pragma: no cover - the extra isn't installed
    cv2 = np = mss = RapidOCR = None
    MAP_VOTE_READING_AVAILABLE = False

# Up to three cards, evenly spaced, in a fixed row — measured on four real vote screens,
# two and three options both seen, always in this same band regardless of count. A card
# with nothing legible in it (a vote offering fewer than three) is simply skipped rather
# than assumed absent from a count read anywhere else, since nothing on this screen states
# the count outside the cards themselves.
CARD_Y_RANGE = (0.2519, 0.7389)
CARD_X_RANGES = [
    (0.1911, 0.3833),
    (0.4115, 0.6094),
    (0.6365, 0.8292),
]

# Where the map's own name sits inside a card, as a fraction of the *screen* — not the
# card — matching every other region in this project. Measured on a real capture: the
# name's own bright pixels sat at y=576-590 of 1080, the mode label directly under it at
# 603-614, a clean gap between; the lower edge here is padded past the name without
# reaching into the mode label, which the crop needs to exclude on its own rather than
# leaning on OCR to ignore it.
NAME_Y_RANGE = (0.525, 0.556)

# A tight crop that's exactly the text and nothing else is what a person would draw by
# hand — and the one thing the OCR model's own text-detection stage measurably struggled
# with. A real capture of "Lijiang Tower" found the difference directly: cropped to just
# `NAME_Y_RANGE`, detection found nothing at all; padded a little above, where a card's
# own thumbnail art already fills the space, it read cleanly. Padded below by anywhere
# near as much reaches into the mode label measured 13px under the name's own last pixel,
# so the two paddings are deliberately not the same.
NAME_CROP_PAD_ABOVE = 15
NAME_CROP_PAD_BELOW = 5

MIN_OCR_CONFIDENCE = 0.5

_engine = None


def _ocr_engine():
    """The OCR model, built once — measured at a little over a second the first time,
    long enough that paying it again on every card would be its own reason not to."""
    global _engine
    if _engine is None:
        _engine = RapidOCR()
    return _engine


def _capture():
    with mss.MSS() as sct:
        monitor = sct.monitors[1]
        shot = sct.grab(monitor)
        return cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)


def _fractional_box(fractions, w: int, h: int):
    fx0, fy0, fx1, fy1 = fractions
    x0, y0 = int(w * fx0), int(h * fy0)
    return x0, y0, int(w * fx1) - x0, int(h * fy1) - y0


def _name_crop(frame_bgr, card_x_range):
    h, w = frame_bgr.shape[:2]
    x0, y0, cw, ch = _fractional_box(
        (card_x_range[0], NAME_Y_RANGE[0], card_x_range[1], NAME_Y_RANGE[1]), w, h)
    y0, y1 = max(0, y0 - NAME_CROP_PAD_ABOVE), min(h, y0 + ch + NAME_CROP_PAD_BELOW)
    return frame_bgr[y0:y1, x0:x0 + cw]


def _read_name(crop) -> str:
    """Every line of text OCR finds in `crop`, read top to bottom and joined with a
    space, or "" if it finds nothing worth trusting.

    More than one *is* expected here, not a sign the crop caught something it shouldn't
    have: a real capture of "Lijiang Tower" split across two separate lines by the OCR
    model's own text detector despite being one line on screen, joining back into the
    right name-shaped name and no other real one, since only one card in four real
    captures ever needed padding wide enough to risk catching the mode label below, and
    that label was measured never to appear in it."""
    if crop is None or crop.size == 0:
        return ""
    result, _ = _ocr_engine()(crop)
    if not result:
        return ""
    # Reading order: top to bottom, then left to right within a line — never just top
    # edge alone. A real capture of "New Queen Street" came back as three same-line boxes
    # a pixel apart in y (21, 22, 21), enough for a pure y-sort to shuffle the words; the
    # coarse line bucket below is what keeps ties like that from mattering while still
    # telling two genuinely different lines ("Lijiang" / "Tower", stacked in-game as one
    # line the model itself splits vertically) apart.
    lines = sorted(result, key=lambda hit: (round(hit[0][0][1] / 12.0), hit[0][0][0]))
    kept = [line[1].strip() for line in lines if float(line[2]) >= MIN_OCR_CONFIDENCE]
    return " ".join(word for word in kept if word)


class MapVoteSelection:
    """One look at the vote screen: which real maps its cards actually name."""

    __slots__ = ("map_keys", "on_screen")

    def __init__(self, map_keys: list, on_screen: bool):
        self.map_keys = list(map_keys)
        self.on_screen = bool(on_screen)


def scan(catalog, log=None) -> MapVoteSelection:
    """One full look at the map-vote screen. `catalog` is whatever `key_for_map_name`
    should be asked — the same `Catalog` the rest of the server already uses, so a
    detected name only ever becomes a key the app already has art for.

    Fewer than two cards reading a real map is treated the same as the screen not being
    up at all: a single misread name is exactly the shape of noise this is built to
    refuse rather than act on, the same "win clearly or say nothing" every other match in
    this project already holds itself to.
    """
    log = log or (lambda message: None)
    if not MAP_VOTE_READING_AVAILABLE:
        return MapVoteSelection([], False)

    frame = _capture()
    keys = []
    for x_range in CARD_X_RANGES:
        name = _read_name(_name_crop(frame, x_range))
        if not name:
            continue
        key = catalog.key_for_map_name(name)
        if key:
            keys.append(key)
        else:
            log("Map vote read %r, nothing in the catalog is close enough" % name)

    if len(keys) < 2:
        return MapVoteSelection([], False)
    log("Read the vote screen — %s" % ", ".join(keys))
    return MapVoteSelection(keys, True)
