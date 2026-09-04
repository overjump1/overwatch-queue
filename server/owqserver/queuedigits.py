"""Reading the queue timer off the banner, and learning its digits from the clock itself.

The number in the banner is the honest answer to "how long have you been queueing". A
clock we start ourselves can only measure from the moment we first *noticed*, which is
wrong every time the server starts mid-queue, and drifts across the seconds where the
banner isn't drawn at all. The game's own timer has none of those problems, so where it
can be read it wins, and `queuewatch` back-dates its start to agree with it.

Reading it means classifying three or four glyphs of a fixed interface font, which is a
much smaller problem than OCR and is solved here with the same `TM_CCOEFF_NORMED` and the
same "win clearly or say nothing" rule as the hero matcher in `vision.py`.

That leaves the digit images, and this is the part worth explaining. There are none in
the repository and there is no font to render them from — but the thing being read is a
clock, and a clock counts. That is enough to label its own digits:

- Each tick, the units-of-seconds glyph changes. Collect them in order and they cycle
  with period ten.
- The tick where the *tens* glyph changes is, by definition, the tick where the units
  glyph is zero. One anchor fixes the whole cycle.
- A run between two anchors exactly ten ticks apart both labels ten glyphs and proves
  itself: any dropped frame makes the run the wrong length and it is thrown away.

So one uninterrupted minute of queueing teaches this module all ten digits, with no
labelling, no font, and nothing to check in. They are cached next to the hero portraits
and never learned again. Until then `read` returns None and the caller times the queue
itself — a worse answer, but the right shape of worse: late, not wrong.

The most valuable line in the file is the plausibility guard in `read`. A timer that has
been counting for a minute cannot suddenly read 4:07, so a reading that disagrees with
the wall clock is a misread digit and is thrown away. That turns the failure mode from
"the watch says you have waited four minutes" into "the watch says nothing new", which is
the difference between a bug someone notices and a bug someone acts on.
"""
from __future__ import annotations

import os

try:                                    # pragma: no cover - trivial import guard
    import cv2
    import numpy as np
    DIGITS_AVAILABLE = True
except ImportError:                     # pragma: no cover - the Mac dev path
    cv2 = np = None
    DIGITS_AVAILABLE = False

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_CACHE_DIR = os.path.join(REPO_ROOT, "server", ".cache", "queue-digits")

# The crop is a few dozen pixels tall. Matching likes more than that, and cubic upscaling
# of hard-edged interface text costs nothing in fidelity.
UPSCALE = 3
# White text on a saturated banner: the one thing on it with no colour at all.
TEXT_SATURATION_MAX = 60
TEXT_VALUE_MIN = 180

# Every glyph is squashed into this box before matching. The font is fixed, so the same
# digit is always squashed the same way and a narrow "1" stays as distinguishable from an
# "8" as it was — which is not true of the aspect ratio, and is why this ignores it.
GLYPH_SIZE = (24, 32)                   # width, height
MIN_GLYPH_AREA = 8
# A digit is a full-height mark; a colon is two small ones. The gap between these two
# fractions is deliberate: anything landing in it is neither, and makes the read fail
# rather than pick.
DIGIT_HEIGHT_RATIO = 0.60
COLON_HEIGHT_RATIO = 0.45

DIGIT_MIN_SCORE = 0.70
DIGIT_MARGIN = 0.08

# How far a reading may sit from where the wall clock says it should be. Generous enough
# for a slow poll and a second of rounding, far tighter than any single-digit misread.
PLAUSIBLE_DRIFT_SECONDS = 3.0
# ...but never wedged. If the guard rejects this many in a row the guard is the thing
# that is wrong — the queue restarted, or the clock was re-anchored — so it lets go.
REJECT_LIMIT = 5

# Two glyphs this alike are the same digit still on screen, not the next tick.
TICK_DIFFERENT_BELOW = 0.90
SAME_GLYPH_ABOVE = 0.90
BOOTSTRAP_MIN_TICKS = 30


class TimerGlyphs:
    """The glyphs of one `M:SS` reading, already split by the colon."""

    __slots__ = ("minutes", "seconds")

    def __init__(self, minutes, seconds):
        self.minutes = list(minutes)
        self.seconds = list(seconds)

    @property
    def units(self):
        """The units-of-seconds glyph — the one that changes every tick."""
        return self.seconds[-1]

    @property
    def tens(self):
        """The tens-of-seconds glyph, whose change marks a units of zero."""
        return self.seconds[0]


# ---------------------------------------------------------------- glyph handling

def _normalised(mask, box):
    x, y, w, h = box
    return cv2.resize(mask[y:y + h, x:x + w], GLYPH_SIZE, interpolation=cv2.INTER_AREA)


def correlation(a, b) -> float:
    """How alike two normalised glyphs are, on the same scale as every other score here."""
    return float(cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED)[0][0])


def segment(patch):
    """The `M:SS` glyphs in `patch` (BGR), or None if it doesn't hold a clean reading.

    Digits are taken from the *right*, because the timer is right-aligned in both banners
    and anything that bled into the crop bled in from the left. The colon then has to sit
    exactly where a colon belongs, which is what stops a stray mark from being read as a
    digit and shifting every place value.
    """
    if patch is None or patch.size == 0:
        return None

    big = cv2.resize(patch, (patch.shape[1] * UPSCALE, patch.shape[0] * UPSCALE),
                     interpolation=cv2.INTER_CUBIC)
    hsv = cv2.cvtColor(big, cv2.COLOR_BGR2HSV)
    mask = (((hsv[:, :, 1] <= TEXT_SATURATION_MAX) &
             (hsv[:, :, 2] >= TEXT_VALUE_MIN)) * 255).astype(np.uint8)

    count, _, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), 8)
    marks = [tuple(int(value) for value in stats[index][:5]) for index in range(1, count)
             if stats[index][4] >= MIN_GLYPH_AREA and stats[index][3] >= 3]
    if not marks:
        return None

    tallest = max(mark[3] for mark in marks)
    digits = sorted((m for m in marks if m[3] >= DIGIT_HEIGHT_RATIO * tallest),
                    key=lambda m: m[0])
    smalls = [m for m in marks if m[3] < COLON_HEIGHT_RATIO * tallest]
    if len(digits) < 3:
        return None

    seconds = digits[-2:]
    minutes = digits[:-2][-2:]
    if not minutes:
        return None

    # The colon lives between the two halves. Where there is one — and there always is,
    # unless the crop clipped it — it has to agree, or the reading is not a reading.
    if smalls:
        colon_x = sum(mark[0] + mark[2] / 2.0 for mark in smalls) / len(smalls)
        if not minutes[-1][0] + minutes[-1][2] <= colon_x <= seconds[0][0]:
            return None

    return TimerGlyphs([_normalised(mask, mark[:4]) for mark in minutes],
                       [_normalised(mask, mark[:4]) for mark in seconds])


def classify(glyph, templates):
    """Which digit `glyph` is, or None when nothing wins clearly.

    Same shape of judgement as `vision.SLOT_MARGIN`: a real identification beats the
    runner-up by a distance, and a pile of near-ties means nothing matched.
    """
    best, best_score, runner_up = None, -1.0, -1.0
    for digit, template in enumerate(templates):
        score = correlation(glyph, template)
        if score > best_score:
            best, best_score, runner_up = digit, score, best_score
        elif score > runner_up:
            runner_up = score
    if best_score < DIGIT_MIN_SCORE or best_score - runner_up < DIGIT_MARGIN:
        return None
    return best


# ---------------------------------------------------------------- learning the digits

class DigitBootstrap:
    """Collects ticks until the clock has shown all ten of its own digits.

    Held separately from `DigitReader` so the labelling — the part with the reasoning in
    it — can be tested against a made-up sequence of ticks with no images involved.
    """

    def __init__(self):
        self.ticks = []                 # (units glyph, tens glyph), one per second seen

    def observe(self, glyphs: TimerGlyphs):
        """Records a tick if the units digit has changed since the last one."""
        if self.ticks and correlation(glyphs.units, self.ticks[-1][0]) >= TICK_DIFFERENT_BELOW:
            return False
        self.ticks.append((glyphs.units, glyphs.tens))
        return True

    def anchors(self):
        """Tick indices where the units digit must be zero — where the tens digit rolled."""
        return [index for index in range(1, len(self.ticks))
                if correlation(self.ticks[index][1],
                               self.ticks[index - 1][1]) < SAME_GLYPH_ABOVE]

    def learn(self):
        """The ten digit templates, or None while the evidence is short.

        Only runs bracketed by two anchors exactly ten ticks apart are used. That length
        is the proof: ten ticks between two rollovers is a complete, unbroken cycle, and
        a run of any other length means a tick was missed and its labels would be off by
        however many.
        """
        if len(self.ticks) < BOOTSTRAP_MIN_TICKS:
            return None

        samples = {}
        marks = self.anchors()
        for start, stop in zip(marks, marks[1:]):
            if stop - start != 10:
                continue
            for offset in range(10):
                samples.setdefault(offset, []).append(self.ticks[start + offset][0])

        if len(samples) < 10:
            return None
        return [np.mean(np.stack(samples[digit]).astype(np.float32), axis=0)
                .astype(np.uint8) for digit in range(10)]


# ---------------------------------------------------------------- the reader

class DigitReader:
    """Reads the timer, learning the digits from it on the way if it has to."""

    def __init__(self, cache_dir: str = DEFAULT_CACHE_DIR, log=None):
        self.cache_dir = cache_dir
        self.log = log or (lambda message: None)
        self.templates = self._load()
        self._bootstrap = None if self.templates else DigitBootstrap()
        self._last = None               # (monotonic when, seconds read)
        self._rejected = 0

    @property
    def ready(self) -> bool:
        return self.templates is not None

    def reset(self):
        """Forgets the running reading. Called when a queue ends, so the next one is not
        measured against the last one's clock."""
        self._last = None
        self._rejected = 0

    def read(self, patch, when: float):
        """The timer in `patch` as whole seconds, or None.

        None covers every way this can decline — no glyphs, an unreadable one, or a
        reading the wall clock says is impossible — because to the caller they are the
        same thing: keep the number you already had.
        """
        glyphs = segment(patch)
        if glyphs is None:
            return None

        if not self.ready:
            self._learn_from(glyphs)
            return None

        places = [classify(glyph, self.templates)
                  for glyph in glyphs.minutes + glyphs.seconds]
        if any(digit is None for digit in places):
            return None

        minutes = int("".join(str(digit) for digit in places[:-2]))
        seconds = int("".join(str(digit) for digit in places[-2:]))
        if seconds > 59:
            return None
        return self._plausible(minutes * 60 + seconds, when)

    def _plausible(self, seconds: int, when: float):
        if self._last is not None:
            then, before = self._last
            if abs(seconds - (before + (when - then))) > PLAUSIBLE_DRIFT_SECONDS:
                self._rejected += 1
                if self._rejected < REJECT_LIMIT:
                    return None
                # Rejecting this many in a row means the clock moved under us rather than
                # the reading being wrong. Believe the screen and re-anchor to it.
                self.log("Queue timer jumped to %ds — re-anchoring" % seconds)
        self._rejected = 0
        self._last = (when, seconds)
        return seconds

    def _learn_from(self, glyphs: TimerGlyphs):
        if not self._bootstrap.observe(glyphs):
            return
        learned = self._bootstrap.learn()
        if learned is None:
            return
        ticks = len(self._bootstrap.ticks)
        self.templates = learned
        self._bootstrap = None
        self._save(learned)
        self.log("Learned the queue timer's digits from %d ticks of the clock" % ticks)

    # ------------------------------------------------------------ the cache

    def _path(self, digit: int) -> str:
        return os.path.join(self.cache_dir, "digit-%d.png" % digit)

    def _load(self):
        images = []
        for digit in range(10):
            image = cv2.imread(self._path(digit), cv2.IMREAD_GRAYSCALE) \
                if os.path.exists(self._path(digit)) else None
            if image is None or image.shape[:2] != (GLYPH_SIZE[1], GLYPH_SIZE[0]):
                return None
            images.append(image)
        return images

    def _save(self, templates):
        """Written the way `TemplateStore` writes portraits — through a `.part` file, so a
        half-finished set is never read back as a real one.

        Encoded by hand rather than with `imwrite`, which chooses its codec from the file
        extension and has never heard of `.png.part`.
        """
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            for digit, image in enumerate(templates):
                encoded, buffer = cv2.imencode(".png", image)
                if not encoded:
                    raise OSError("couldn't encode digit %d" % digit)
                partial = self._path(digit) + ".part"
                with open(partial, "wb") as handle:
                    handle.write(buffer.tobytes())
                os.replace(partial, self._path(digit))
        except (OSError, cv2.error) as problem:
            self.log("Couldn't cache the queue digits: %s" % problem)
