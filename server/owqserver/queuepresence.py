"""What Battle.net's own rich presence says, turned into this project's vocabulary.

Nothing here opens a socket, a process handle or a file — that is `bnetpresence`'s job.
This module is arithmetic on a string, the same separation `queuevision`'s pixel tests
keep from `queuewatch`'s state machine, and for the same reason: it is what lets the
whole vocabulary be tested against made-up strings on any machine, Battle.net installed
or not.

Battle.net's rich presence for Overwatch is a short line built from two parts:

    [<Mode>: ]<Activity>

`In Menus` on its own, `Quick Play: In Queue`, `Competitive: In Game`, `Custom Game:
In Game`, and standalone words like `Practice Range` and `Tutorial` have all been seen
verbatim on a real account. The two are pulled apart by finding the longest *trailing*
activity the text ends with, not by splitting on the colon — a mode is free to contain
one of its own (a group marker, a map name) and the activity never has trailing text of
its own in anything observed, so anchoring on its end is the more conservative read.

Two degradation rules, deliberately asymmetric, mirror the one `queuevision.match_mode`
already makes for a hue outside every tabled hue's tolerance — "unmeasured" is not "not
there":

  * an unrecognised **mode** in front of a recognised activity still names the coarse
    state, with `mode=None` for "not measured" — `"Stadium: In Queue"` is still a queue,
    even before Stadium's own string is ever added to the table below.
  * an unrecognised **activity** is always `UNKNOWN`, whatever the mode says — a
    familiar mode word never rescues a claim the activity itself doesn't make.
"""
from __future__ import annotations

# ------------------------------------------------------------ the coarse vocabulary

# A real, positively-read queue or match.
QUEUEING = "queueing"
IN_GAME = "inGame"
# Battle.net says the player is somewhere in Overwatch's menus - not queueing, not in a
# match. Ends a queue the same way `MENUS` below does.
MENUS = "menus"
# Positively observed, and deliberately given no authority over the queue state: the
# practice range and the tutorial are exactly where people wait out a queue, so treating
# either as "not queueing" would end a live queue every time someone did the single most
# common while-you-wait thing there is.
PLAYING_OTHER = "playingOther"
# A record was read for our own account and it names a program that is not Overwatch.
# Real evidence there's no Overwatch queue - never inferred from merely finding nothing.
ELSEWHERE = "elsewhere"
# No claim could be read at all - a string outside every known word, an empty reading,
# or no record found. Carries no authority in either direction.
UNKNOWN = "unknown"

_KNOWN_STATES = frozenset({QUEUEING, IN_GAME, MENUS, PLAYING_OTHER, ELSEWHERE, UNKNOWN})

# Longest-first so a shorter activity that happens to be a suffix of a longer one (none
# currently are) could never shadow it.
ACTIVITY_SUFFIXES = (
    ("In Queue", QUEUEING),
    ("In Game", IN_GAME),
)

# Text that is the *entire* activity on its own - nothing precedes it, so there is no
# mode to parse out of it.
STANDALONE_ACTIVITIES = {
    "In Menus": MENUS,
    "Practice Range": PLAYING_OTHER,
    "Tutorial": PLAYING_OTHER,
}

# The mode word Battle.net prints in front of an activity, mapped to `protocol.MODES`.
# Deliberately incomplete: Stadium's and Mystery Heroes' own strings have never been
# observed, and Arcade's sub-modes may present as their own mode word rather than
# "Arcade" itself. Guessing either would put a wrong mode on the watch; add the real
# string here once `presence_debug.py` has printed it.
MODE_WORDS = {
    "Quick Play": "quickPlay",
    "Competitive": "competitive",
    "Custom Game": "custom",
    "Arcade": "arcade",
}

# The program this project cares about. Confirmed from a live `subscribeSelfPresence`
# record: `program_id: "Pro"`, `program_name: "Overwatch"`.
OVERWATCH_PROGRAM_ID = "Pro"
OVERWATCH_PROGRAM_NAME = "Overwatch"


class PresenceReading:
    """What one look at Battle.net's presence says, in this project's own words.

    Mirrors `queuewatch.QueueObservation`'s shape on purpose: a `state`, a `mode` that is
    `None` when unmeasured rather than a guess, and the raw text kept verbatim so an
    unrecognised string is visible in the log and the debug tool rather than silently
    dropped - the same promise `queuewatch_debug.py` already makes about an unmeasured
    hue.
    """

    __slots__ = ("state", "mode", "text", "battletag", "at")

    def __init__(self, state, mode, text, battletag, at):
        assert state in _KNOWN_STATES, state
        self.state = state
        self.mode = mode              # a `protocol.MODES` member, or None: not measured
        self.text = text              # the raw rich-presence string, verbatim
        self.battletag = battletag    # whose reading this is, or None if not carried
        self.at = float(at)           # the caller's own clock, when this was read

    @property
    def known(self) -> bool:
        return self.state != UNKNOWN

    @property
    def says_queueing(self) -> bool:
        return self.state == QUEUEING

    def __repr__(self):
        return "<PresenceReading %s %s %r by %s>" % (
            self.state, self.mode or "-", self.text, self.battletag or "?")


class Identity:
    """The account this project should be reading presence *for*.

    Every source is handed one so a friend's record read alongside our own can never be
    mistaken for it - a `MemorySource` scanning a heap of everyone Battle.net has ever
    cached presence for has no other way to tell the two apart.
    """

    __slots__ = ("battletag", "account_id")

    def __init__(self, battletag, account_id=None):
        self.battletag = battletag
        self.account_id = str(account_id) if account_id is not None else None

    def matches(self, battletag=None, account_id=None) -> bool:
        if account_id is not None and self.account_id is not None:
            return str(account_id) == self.account_id
        if battletag is not None and self.battletag is not None:
            return battletag == self.battletag
        return False

    def __repr__(self):
        return "<Identity %s (%s)>" % (self.battletag, self.account_id or "?")


def parse(text, *, mode_words=None, activity_suffixes=None, standalone=None):
    """The core rule: a rich-presence string to `(state, mode)`.

    `mode_words`/`activity_suffixes`/`standalone` are injectable so a test can hand in a
    table containing a word the shipped one doesn't know, without the shipped table
    having to pretend it recognises something never actually observed - the same reason
    `queuevision.load_modes` takes its table from a file rather than hard-coding it.
    """
    mode_words = MODE_WORDS if mode_words is None else mode_words
    activity_suffixes = ACTIVITY_SUFFIXES if activity_suffixes is None else activity_suffixes
    standalone = STANDALONE_ACTIVITIES if standalone is None else standalone

    if text is None:
        return UNKNOWN, None
    text = text.strip()
    if not text:
        return UNKNOWN, None

    if text in standalone:
        return standalone[text], None

    for suffix, state in activity_suffixes:
        if text == suffix:
            return state, None
        if text.endswith(suffix):
            prefix = text[:-len(suffix)].rstrip()
            if prefix.endswith(":"):
                prefix = prefix[:-1].rstrip()
            if not prefix:
                # No mode text at all in front of a recognised activity - still known,
                # just unmeasured, e.g. a variant this table hasn't seen a mode for.
                return state, None
            return state, mode_words.get(prefix)

    return UNKNOWN, None


def parse_record(record, when, identity=None):
    """A structured `subscribeSelfPresence`-shaped record to a `PresenceReading`.

    `record` is the `self` object Battle.net's own `phoenix.socialService.
    subscribeSelfPresence` callback hands back: `battle_tag`, `program_id`,
    `program_name`, `rich_presence`, `id` (an account id). Returns `None` (not a
    reading at all) when the record plainly isn't ours, or when there's nothing there
    to read.

    A program that isn't Overwatch is `ELSEWHERE` and never falls through to `parse` -
    another game's rich presence has nothing to do with this project's vocabulary. But
    finding no program on the record at all is `UNKNOWN`, not `ELSEWHERE`: no program
    named is no evidence of anything, only a positively named *other* one is.
    """
    if not record:
        return None
    battletag = record.get("battle_tag")
    account_id = record.get("id")
    if identity is not None and not identity.matches(battletag=battletag,
                                                       account_id=account_id):
        return None

    program_id = record.get("program_id")
    program_name = record.get("program_name")
    text = record.get("rich_presence") or ""

    if program_id or program_name:
        is_overwatch = (program_id == OVERWATCH_PROGRAM_ID or
                        program_name == OVERWATCH_PROGRAM_NAME)
        if not is_overwatch:
            return PresenceReading(ELSEWHERE, None, text, battletag, when)

    state, mode = parse(text)
    return PresenceReading(state, mode, text, battletag, when)
