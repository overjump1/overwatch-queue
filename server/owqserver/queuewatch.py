"""Turning a stream of frames into a queue, and telling the server about it.

`queuevision` answers one frame at a time, and one frame at a time is not something to
put on someone's watch. Two things have to happen in between:

**A gap has to stop meaning "not queueing".** Taking a while-you-wait deathmatch hides the
banner for several seconds while the map loads; so does a killcam, a scoreboard, and
alt-tabbing to a browser. Every one of those is still a queue. So an absent banner starts
a grace period rather than ending the queue, and the grace is generous — twenty seconds —
because the two errors are not equal. Ending the queue late costs a stale line on a phone
for a few seconds. Ending it early tells someone their queue is over while they are still
in it, which is the one thing this project exists not to do.

Not being able to see is kept strictly apart from the banner being gone, which is a
distinction worth spelling out because collapsing the two was a real bug. While Overwatch
is behind another window there is no evidence about anything, so the grace is suspended
*and the state does not move*; the observation is flagged `looking = False` instead. The
reason this matters so much: the panel showing the state is itself a window, so reading
it is what puts Overwatch in the background. Treating that as "the banner disappeared"
meant the readout said so every single time a human looked at it, whatever the queue was
really doing. `HIDDEN_BLIND_SECONDS` is the backstop that eventually gives up anyway, so
a match that pops while the player is in a browser can't hold a queue open forever.

**A single frame has to stop being enough.** Entering a queue needs two frames in a row,
which costs a quarter of a second and buys immunity to one unlucky flash of colour.
Leaving for `GAME_FOUND` deliberately does *not* wait: the green check is unmistakable and
the moment is brief, and being a quarter-second late with the one alert anyone actually
cares about is worse than a rare false one that the next frames clear anyway.

The duration is kept as a *start time*, not a counter, so a gap can't drop seconds from
it. It begins as the moment we first noticed, and is rewritten the instant the on-screen
timer can be read — see `queuedigits` for why the game's own clock is the better answer.
`source` says which of the two the number came from, so nothing downstream has to guess.

**Polling while searching does not have to mean polling `find_banner` itself that often.**
A full look — shape, colour spread, the timer digits — costs real work, which is exactly
why `SEARCHING_POLL_SECONDS` throttles it to four times a second rather than every frame.
But a full-video test measured what that throttle actually costs: the real check circle
goes from nothing to thousands of matching pixels within two frames of appearing, so a
quarter-second gap is routinely most of the moment being waited for. `queuevision.
green_hint` is what closes that without paying for more full looks — a box already known,
scanned for nothing but "enough green pixels to be worth asking," measured at 0-5 on
every one of five real queues right up until the real thing, which put over 2000 in the
same box immediately. `_fast_wait` spends the same quarter-second `SEARCHING_POLL_SECONDS`
already budgeted for the next full look watching that box at 50Hz instead of sleeping
through it blind, and returns the instant it trips so the next full look happens right
then rather than however much of the budget was left.

`QueueTracker` holds all of that and touches nothing: no screen, no clock, no server. It
is handed frames and a time and returns an observation, which is what lets the whole of
the reasoning above be tested against a made-up minute. `QueueWatcher` is the thin part
around it that captures, polls and calls `QueueServer.apply`.

Like `queuevision`, and unlike the hero-select half of `vision.py`, nothing here focuses a
window or touches the mouse. It only looks.

**Which role is checked is read the same way, whenever there is no queue to watch
instead.** `queueroles.scan` was built alongside this module but, until a real session
using both live found it, stayed a button a person had to click — the "Select a Role"
screen a person actually looks at right before starting a queue, so it only matters while
`IDLE`, and is only ever a plain screenshot the same as everything else here, never a
focus grab the way reading a hero's own pick is. `_check_role_select` is that read, run on
the same idle cadence as everything else while there is no banner to explain why not.
"""
from __future__ import annotations

import threading
import time

try:                                    # pragma: no cover - trivial import guard
    import cv2
except ImportError:                     # pragma: no cover - the Mac dev path
    cv2 = None

from . import protocol, queuedigits, queuepresence, queueroles, queuevision, stagevision, vision

IDLE = "idle"
SEARCHING_MENU = "searchingMenu"
SEARCHING_IN_GAME = "searchingInGame"
SEARCHING_HIDDEN = "searchingHidden"
GAME_FOUND = "gameFound"

SEARCHING_STATES = frozenset({SEARCHING_MENU, SEARCHING_IN_GAME, SEARCHING_HIDDEN})

# Two frames to start a queue, none to end one at a match. See the module docstring.
ENTER_FRAMES = 2
HIDDEN_GRACE_SECONDS = 20.0
# The backstop for a queue that popped while nobody was looking at the screen.
HIDDEN_BLIND_SECONDS = 240.0
# A match found stays on the board this long even after its banner goes, so the phone
# isn't dragged back to idle in the second before hero select appears.
GAME_FOUND_HOLD_SECONDS = 12.0
# How far the read timer and our own clock may disagree before the read one wins.
TIMER_DISAGREE_SECONDS = 2.0

# Was 1.5s. A queue starting is only ever caught on the *next* idle poll after the
# player clicks Play, so this interval is a direct, visible part of "how long until my
# phone notices" — unlike `SEARCHING_POLL_SECONDS`, which a full-video test tuned against
# real footage, this one was just a default. A plain screenshot-and-box-check is cheap
# enough that running it three times as often costs nothing worth trading against that.
IDLE_POLL_SECONDS = 0.5
SEARCHING_POLL_SECONDS = 0.25
# How often the fast trip-wire actually looks, inside a `SEARCHING_POLL_SECONDS` budget.
# The real check circle's own ramp (0 to 2000+ matching pixels within two frames) has
# room to spare against even a slower tick than this; 50Hz is chosen for margin, not
# because anything measured needed it that tight.
FAST_TICK_SECONDS = 0.02
# How long a banner box is trusted after the last poll that actually confirmed one there.
# Past this, `_fast_wait` falls back to sleeping rather than watching: a box this stale
# is a poll or more behind, and a full-video test found exactly what a stale one can drift
# onto — a live match's own green score bar, once the real banner had already moved on to
# somewhere else. `green_check`'s shape tests would still reject that if asked, so a stale
# box only ever costs a wasted look rather than a wrong answer, but there is no reason to
# spend even that when the box backing it is this old.
BOX_FRESHNESS_SECONDS = 0.5

# --- Battle.net presence — see `QueueTracker.presence` for how these are used. ---
#
# Vision is never asked to arbitrate against a part Battle.net already answers. Once
# presence has proven itself alive (`presence_authoritative`, below), it alone decides
# whether a queue exists, what mode it's in, and — confirmed directly: Battle.net's
# `In Game` *is* the match being found, not merely "some Overwatch activity that isn't a
# queue" — whether a searching queue has just found its match. Vision gets no vote on any
# of that any more. It keeps doing exactly what presence structurally cannot: Battle.net's
# rich presence carries no elapsed time at all, so the on-screen timer stays vision's job
# regardless, and the fast green check still runs and can still land `GAME_FOUND` first —
# both paths reach the same state, so whichever is quicker simply wins, and the other is a
# harmless no-op once there. Map vote and hero select have no presence equivalent at all
# and are entirely unaffected.

# Battle.net's rich presence says "In Queue" from the moment the role-select screen
# opens, before a real search has even begun — the same string covers both, confirmed
# directly against a real account, so presence alone cannot tell "picking a role" apart
# from "actually searching". Vision positively confirming that screen is still up is the
# one thing that can, so a queueing reading arriving while it's on screen is held off
# rather than treated as a real start — see `QueueWatcher._role_select_confirmed_visible`
# and `_presence_queueing`. How long a "yes, it was up" reading is trusted before it
# decays back to "unknown, so trust presence" — a little more than `IDLE_POLL_SECONDS`
# (0.5s) so one slow poll doesn't wrongly let a real start through.
ROLE_SELECT_FRESHNESS_SECONDS = 1.5


class QueueObservation:
    """What the tracker believes right now."""

    __slots__ = ("state", "kind", "mode", "queue_elapsed", "state_elapsed", "source",
                 "confidence", "looking")

    def __init__(self, state, kind, mode, queue_elapsed, state_elapsed, source,
                 confidence, looking=True):
        self.state = state
        self.kind = kind                        # the banner shape, or None when hidden
        self.mode = mode                        # a protocol mode, or None if unmeasured
        self.queue_elapsed = float(queue_elapsed)
        self.state_elapsed = float(state_elapsed)
        self.source = source                    # "timer" (read), "self" (guessed), or
                                                 # "audio" (queueaudio's own tone, below)
        self.confidence = float(confidence)
        # Whether the last look was a look at all. False means Overwatch was behind
        # something, so `state` is the last thing actually seen rather than news.
        self.looking = bool(looking)

    @property
    def searching(self) -> bool:
        return self.state in SEARCHING_STATES

    @property
    def in_queue(self) -> bool:
        return self.state != IDLE

    def __repr__(self):
        return "<QueueObservation %s %s %s %ds by %s>" % (
            self.state, self.kind or "-", self.mode or "?", self.queue_elapsed,
            self.source)


class QueueTracker:
    """The state machine, with no screen and no clock of its own.

    Every method takes the time it should think it is, so a test can hand it a minute in
    a millisecond and so the poll loop's own jitter never becomes drift.
    """

    def __init__(self):
        self.state = IDLE
        self.kind = None
        self.mode = None
        self.confidence = 0.0
        self.source = "self"
        self.looking = True
        self._entered = 0.0
        self._started = None            # when the queue began, in the caller's clock
        self._last = None               # the previous update's time
        self._pending = None            # (state, frames so far, when it started)
        self._hidden_visible = 0.0      # grace spent while we could actually see
        self._hidden_total = 0.0        # ...and regardless of whether we could
        self._found_waited = 0.0
        # Battle.net presence — see `presence`/`presence_lost` below.
        self._presence = None           # the last coarse state presence claimed
        self._presence_at = None        # when, in the caller's own clock
        self._banner_at = None          # when a *searching* banner was last actually seen
        # Whether presence has proven itself alive and therefore owns queue-start,
        # queue-end and the mode outright. Starts False — vision is exactly as
        # authoritative as it always was until a first known reading says otherwise —
        # and reverts to False the moment presence goes quiet (`presence_lost`), so a
        # dead Battle.net hands full control back rather than freezing the tracker with
        # no one left driving it.
        self.presence_authoritative = False

    # ------------------------------------------------------------ reading it

    @property
    def searching(self) -> bool:
        return self.state in SEARCHING_STATES

    @property
    def pending(self) -> bool:
        """Whether a banner has been seen once and is waiting to be seen again."""
        return self._pending is not None

    def queue_elapsed(self, when: float) -> float:
        if self.state == GAME_FOUND:
            return self._found_waited
        if self._started is None:
            return 0.0
        return max(0.0, when - self._started)

    def observation(self, when: float) -> QueueObservation:
        return QueueObservation(self.state, self.kind, self.mode,
                                self.queue_elapsed(when), max(0.0, when - self._entered),
                                self.source, self.confidence, self.looking)

    # ------------------------------------------------------------ driving it

    def update(self, hit, when: float, visible: bool = True, timer=None):
        """One frame. `hit` is a `BannerHit` or None, `timer` the seconds read off it."""
        gap = 0.0 if self._last is None else max(0.0, when - self._last)
        self._last = when

        if hit is not None:
            self._saw_banner(hit, when, timer)
        else:
            self._saw_nothing(when, gap, visible)
        return self.observation(when)

    def audio_found(self, when: float):
        """A second, independent "match found" signal from `queueaudio` rather than a
        banner glimpsed on screen — see that module for what it actually hears and how
        it decided.

        Only trusted while actually searching, which a full-video test of that audio
        channel is what showed this gate was necessary rather than incidental: every
        other quiet-then-loud transition the same recording's audio turned up — entering
        hero select, "prepare to attack" starting, whatever plays between rounds — happens
        strictly after a real match already landed, by which point this tracker has
        already left every `SEARCHING_STATES` member behind. `self.searching` being false
        is what tells those apart from the real thing without this method needing to know
        what any of them individually were. It does not fully close the channel's own
        weaker spot — the same sweep found a couple of triggers that land *while still
        genuinely searching*, most likely voice chat resuming after a lull, which this
        gate cannot distinguish from the real tone by timing alone — so this stays a
        second opinion the way `queuevision`'s own is treated as the trustworthy one:
        useful for catching what a brief on-screen check missed, not yet trusted to
        overrule a screen that disagrees with it.
        """
        if not self.searching:
            return
        if self.state != GAME_FOUND:
            self._found_waited = self.queue_elapsed(when)
            self._enter(GAME_FOUND, when)
        self.looking = True
        self.source = "audio"

    def presence(self, reading, when: float, role_select_visible: bool = False):
        """Folds one look at Battle.net's own presence into the same state machine.

        A known reading is proof the source is alive, which is what turns
        `presence_authoritative` on — from that moment, presence alone decides whether
        a queue exists and what mode it's in, full stop, with no deferral to whatever
        vision happens to be seeing. That's a deliberate change from treating this like
        `audio_found`'s "second opinion": Battle.net's rich presence isn't a noisy
        channel to be weighed against the screen, it's the same information the screen
        was always a proxy for, arriving from Blizzard directly.

        `role_select_visible` is the one deliberate exception — see `_presence_queueing`.

        An `UNKNOWN` reading is recorded nowhere — not even its timestamp, and it never
        touches `presence_authoritative` — so "presence is quiet" and "presence keeps
        answering it doesn't know" stay indistinguishable, which they should be: neither
        is evidence of anything.
        """
        if reading is None or not reading.known:
            return
        self._presence, self._presence_at = reading.state, when
        self.presence_authoritative = True

        if reading.mode:
            # The only source that can ever name arcade/stadium/mysteryHeroes outright —
            # a hue is a measurement against two table entries; this is text. Never
            # blanked by a reading with no mode word, same as a hue's own "None means
            # not measured" rule.
            self.mode = reading.mode

        if reading.state == queuepresence.QUEUEING:
            self._presence_queueing(when, role_select_visible)
        elif reading.state in (queuepresence.MENUS, queuepresence.ELSEWHERE):
            self._presence_not_queueing(when)
        elif reading.state == queuepresence.IN_GAME:
            self._presence_in_game(when)
        # PLAYING_OTHER is positively observed and deliberately given no authority over
        # the queue state — the practice range is exactly where people wait out a queue.

    def _presence_queueing(self, when: float, role_select_visible: bool = False):
        if self.searching:
            # Confirmation, not a fresh sighting: treat it like `_saw_banner` does for
            # the grace clocks, but leave `state`/`_started`/`source` alone — which
            # banner is showing, and the precise clock, are vision's business, not
            # presence's, and stay so even now that presence owns queue-start/end.
            self._hidden_visible = self._hidden_total = 0.0
            return

        if self.state == GAME_FOUND:
            if self.source == "audio":
                # No on-screen confirmation ever existed for this one; presence still
                # saying "queueing" afterward is the missing look, same as the screen
                # itself disagreeing — see `_saw_banner`'s identical retraction.
                self._pending = None
                self._started = self._started if self._started is not None else when
                self.source = "self"
                self._enter(SEARCHING_HIDDEN, when)
            # A vision-confirmed match is left alone — the green check is still the
            # authoritative match-found signal (see the module note above), so a stale
            # "still queueing" arriving after it is expected, not a contradiction.
            return

        if role_select_visible:
            # Battle.net says "In Queue" from the moment the role-select screen opens —
            # the same string covers "picking a role" and "actually searching", so
            # presence alone can't tell them apart. Vision positively confirming that
            # screen is still up is the one thing that can, so the claim is held off
            # rather than started early. See `ROLE_SELECT_FRESHNESS_SECONDS` for how
            # this decays back to trusting presence the moment vision can no longer
            # confirm it — role-select closing, or Overwatch going out of sight.
            return

        # Idle: presence alone is enough to start a queue, with no `ENTER_FRAMES` wait —
        # a presence string isn't a noisy pixel measurement with an "unlucky flash" to
        # guard against, and two reads of the same cached record wouldn't be independent
        # evidence the way two separate frames are.
        self._pending = None
        self._started = when
        self.source = "presence"
        self._enter(SEARCHING_HIDDEN, when)

    def _presence_not_queueing(self, when: float):
        if self.state == GAME_FOUND:
            # Unverified whether presence distinguishes match found/map vote/hero
            # select from "in menus" — harmless either way, since leaving GAME_FOUND
            # for IDLE sends nothing to the server on its own (`_act` only emits
            # `cancelled` when leaving a *searching* state).
            if when - self._entered >= GAME_FOUND_HOLD_SECONDS:
                self._reset(when)
            return

        if not self.searching:
            return

        # No deferral to a recent banner sighting: presence owns this question outright
        # once it's proven itself alive, so it ends the queue the moment it says so.
        self._reset(when)

    def _presence_in_game(self, when: float):
        """`In Game` is the match being found — confirmed directly, not a guess — so a
        searching queue is promoted the moment presence says so, the same as vision's
        own green check does. Both paths land on the same state, so whichever gets
        there first wins and the other is a no-op; this is what lets presence's own
        several-second lag behind the instant a match lands cost nothing when vision's
        fast trip-wire gets there first, while still being a real, independent way to
        notice a match if vision ever doesn't (Overwatch backgrounded, a bad frame)."""
        if not self.searching:
            return
        if self.state != GAME_FOUND:
            self._found_waited = self.queue_elapsed(when)
            self._enter(GAME_FOUND, when)
        self.source = "presence"

    def presence_lost(self, when: float):
        """Called when a presence source dies, or its last reading has gone stale — the
        tracker gets no ticks of its own, so the loss has to be told to it explicitly.

        Hands full authority back to vision (`presence_authoritative = False`) rather
        than leaving the tracker frozen with no one left driving queue-start/end — a
        dead Battle.net must never mean a queue quietly stops being noticed. Separately,
        resets a queue only if presence itself started it and vision never corroborated
        it (`_banner_at is None`) — zero evidence remains behind it. A queue vision has
        seen is left strictly alone; vision's own rules govern it once it's back in
        charge, same as they always did before presence was ever involved.
        """
        self._presence = self._presence_at = None
        self.presence_authoritative = False
        if self.searching and self.source == "presence" and self._banner_at is None:
            self._reset(when)

    def _saw_banner(self, hit, when: float, timer):
        self.looking = True
        self._hidden_visible = self._hidden_total = 0.0
        self.confidence = hit.confidence
        self.kind = hit.kind
        # The mode is a part Battle.net's own presence already provides once it's
        # proven itself alive — a hue is a measurement against two table entries, worse
        # in every way than text naming the mode outright, so vision doesn't get a vote
        # on it any more once presence is authoritative.
        if hit.mode and not self.presence_authoritative:
            self.mode = hit.mode

        if hit.searching:
            self._banner_at = when

        if hit.game_found:
            if self.state != GAME_FOUND:
                self._found_waited = self.queue_elapsed(when)
                self._enter(GAME_FOUND, when)
            return

        # The in-game bar is the only one of these actually drawn over a live match; the
        # menu tab and the corner HUD pill are both "somewhere in the menus", whichever
        # screen that happens to be, so both read as the same state.
        wanted = SEARCHING_IN_GAME if hit.kind == queuevision.IN_GAME_BAR else SEARCHING_MENU
        if self.searching:
            # Already certain there's a queue on; which banner is showing is just where
            # the player happens to be, and swapping between them needs no convincing.
            self._anchor(when, timer)
            if self.state != wanted:
                self._enter(wanted, when)
            return

        if self.state == GAME_FOUND and self.source == "audio":
            # A GAME_FOUND that came from `audio_found` has no on-screen confirmation of
            # its own — nothing has actually looked yet. The screen showing "still
            # searching" straight afterward is exactly that missing look, and it says the
            # tone this came from was not a match landing after all: a real one clears
            # the banner within a few seconds, and a full-video test of this channel
            # found real false claims that a blind hold would have sat on for the full
            # twelve seconds, silently eating whatever the queue did in the meantime —
            # in one measured case, the real match landing three seconds later. Retracted
            # immediately in favour of what the screen is still plainly showing, rather
            # than held the way a vision-confirmed GAME_FOUND is.
            self._pending = None
            self._started = self._started if self._started is not None else when
            self.source = "self"
            self._enter(wanted, when)
            self._anchor(when, timer)
            return

        if self.state == GAME_FOUND and when - self._entered < GAME_FOUND_HOLD_SECONDS:
            # The banner lingers for a moment after the match lands. That is the same
            # match, not the next queue.
            return

        if self.presence_authoritative:
            # Starting a queue from idle is presence's call now, not a banner vision
            # happened to glimpse — see the module note above. A banner seen while
            # presence says nothing is running is not enough on its own any more.
            return

        if self._pending and self._pending[0] == wanted:
            self._pending = (wanted, self._pending[1] + 1, self._pending[2])
        else:
            self._pending = (wanted, 1, when)
        if self._pending[1] < ENTER_FRAMES:
            return

        # The queue is dated from the *first* of the confirming frames, not the one that
        # happened to convince us. Waiting for proof is our problem, not the player's,
        # and charging them the frames it took would make every measured wait short.
        first = self._pending[2]
        self._pending = None
        self._started = first
        self.source = "self"
        self._enter(wanted, when)
        self._anchor(when, timer)

    def _saw_nothing(self, when: float, gap: float, visible: bool):
        self._pending = None
        self.looking = visible

        if not visible:
            # Being unable to see is not a state the queue is in, and collapsing the two
            # was a real mistake: the panel is on the other monitor or the other alt-tab,
            # so *reading the panel is itself what puts Overwatch behind something*. The
            # readout therefore said "banner out of sight" every single time anybody
            # looked at it, no matter what the queue was doing. Holding the last state
            # actually seen, and flagging that we aren't looking, is what fixes that.
            self._hidden_total += gap
            if (not self.presence_authoritative and self.searching and
                    self._hidden_total >= HIDDEN_BLIND_SECONDS):
                # Ending a queue vision has lost sight of is exactly the "no one's
                # driving this any more" backstop presence now owns once it's alive —
                # see the module note above and `_presence_not_queueing`.
                self._reset(when)
            return

        if self.state == GAME_FOUND:
            if when - self._entered >= GAME_FOUND_HOLD_SECONDS:
                self._reset(when)
            return
        if not self.searching:
            return

        self.kind = None
        self._hidden_total += gap
        self._hidden_visible += gap
        if self.state != SEARCHING_HIDDEN:
            self._enter(SEARCHING_HIDDEN, when)
        if not self.presence_authoritative and (
                self._hidden_visible >= HIDDEN_GRACE_SECONDS or
                self._hidden_total >= HIDDEN_BLIND_SECONDS):
            self._reset(when)

    def _anchor(self, when: float, timer):
        """Moves the queue's start to agree with the timer on screen."""
        if timer is None:
            return
        started = when - timer
        if self._started is None or abs(started - self._started) > TIMER_DISAGREE_SECONDS:
            self._started = started
        self.source = "timer"

    def _enter(self, state: str, when: float):
        self.state = state
        self._entered = when

    def _reset(self, when: float):
        self._enter(IDLE, when)
        self.kind = None
        self.mode = None
        self.confidence = 0.0
        self.source = "self"
        self.looking = True
        self._started = None
        self._pending = None
        self._hidden_visible = self._hidden_total = 0.0
        self._banner_at = None


class QueueWatcher:
    """Polls the screen and moves the server's phase to match.

    Takes a `Controls` rather than a `QueueServer` because the queue's mode and role are
    only half readable: the banner's colour is the mode, but nothing on it says which role
    was queued while one is actually running. Before one starts, though, the role is
    exactly as readable as the banner is — see `_check_role_select` — so `Controls.role`
    is written here too, the same as `mode` and `estimate` already are by hand, whenever
    the real "Select a Role" screen says something different. Going through `Controls`
    also means this gets `effective_role` — the open-queue rule for Arcade and Mystery
    Heroes — rather than reimplementing it.
    """

    def __init__(self, controls, log=None, reader=None, tracker=None, role_templates=None,
                hero_templates=None, hero_keys=None):
        self.controls = controls
        self.server = controls.server
        self.log = log or (lambda message: None)
        self.tracker = tracker or QueueTracker()
        self.reader = reader if reader is not None else \
            queuedigits.DigitReader(log=lambda message: self.log(message))
        self.role_templates = role_templates
        self.hero_templates = hero_templates
        self.hero_keys = hero_keys
        self.latest = None
        self.on_change = None           # called (on the poll thread) after a state moves
        # called (on the poll thread) with a `queueroles.RoleSelection` whenever an idle
        # poll reads a role different from the one already selected — a GUI wires this to
        # whatever already keeps a picker widget in sync with a hand-triggered scan.
        self.on_role_detected = None

        self._modes = queuevision.load_modes()
        self._acted = IDLE
        self._thread = None
        self._stop = threading.Event()
        self._last_box = None           # a searching banner's own box, for `_fast_wait`
        self._last_box_at = None        # when that box was last actually confirmed
        # Whether the last role-select scan found the screen up, and when — see
        # `_role_select_confirmed_visible` and `QueueTracker._presence_queueing`.
        self._role_select_visible = False
        self._role_select_checked_at = None
        # Guards `_act`'s read-modify-write of `_acted` and the tracker itself, since a
        # `PresenceWatcher` can call `fold_presence` from its own thread at any moment.
        # Released before `_fast_wait` runs — see that method's own note — so a slow
        # presence read can never share a budget with the 50Hz trip-wire.
        self._tracker_lock = threading.RLock()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.running:
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="queuewatch")
        self._thread.start()
        self.log("Watching the screen for a queue")

    def stop(self):
        self._stop.set()

    # ------------------------------------------------------------ one frame

    def poll(self, when=None) -> QueueObservation:
        """Looks once and folds the answer into the tracker. Public so the debug tool can
        drive it a frame at a time without a thread."""
        when = time.monotonic() if when is None else when

        if vision._game_window() is None:
            # No game at all, which is not the same as not being able to see it: let the
            # grace run out and the queue end.
            return self.tracker.update(None, when, visible=True)
        if not vision._foreground_is_game():
            # A screenshot of somebody's browser says nothing about their queue.
            return self.tracker.update(None, when, visible=False)

        strip, screen_w, screen_h = queuevision.capture_strip()
        hit = queuevision.find_banner(strip, screen_w, screen_h, self._modes)
        if hit is not None:
            self._last_box = hit.box
            self._last_box_at = when
        timer = None
        if hit is not None and hit.searching:
            timer = self.reader.read(hit.timer_patch(strip), when)
        observation = self.tracker.update(hit, when, visible=True, timer=timer)

        if hit is None and self.role_templates is not None and observation.state == IDLE:
            self._check_role_select()
        if hit is None:
            self._check_match_progress()
        return observation

    def _check_match_progress(self):
        """Moves the session on through map vote, hero select and into the match itself,
        the same look `Controls.jump` already takes on a person's own click — this is
        only ever a fresh set of eyes on `self.server.session.kind`, never a tracker of
        its own the way the banner has one, because that phase is already the one true
        record of where the match actually is.

        Gated on that phase rather than anything read here, on purpose: `mapVote` and
        `heroSelect` are each looked for only while the phase hasn't already said we're
        there, so a screen that happens to look like one twice in a row costs one look
        and one skipped one, never two jumps. `looks_like_prepare` is the exception,
        since it is asked for *because* `heroSelect` is already current — a match that
        has actually started is what it exists to catch.
        """
        kind = self.server.session.kind
        if kind not in ("matchFound", "mapVote", "heroSelect"):
            return

        if kind == "heroSelect":
            if stagevision.looks_like_prepare(queueroles.capture()):
                self.controls.jump("inGame")
            return

        frame = queueroles.capture()
        if stagevision.looks_like_map_vote(frame):
            if kind != "mapVote":
                self.controls.jump("mapVote")
            return

        if not self.server.vision_enabled or self.hero_templates is None or \
                self.hero_keys is None:
            # Detecting the roster needs no mouse of its own, but the jump this would
            # make does — `Controls.hero_select_phase` reaches for a real scan, which
            # focuses the window the same as a person's own click would. Skipped
            # entirely with vision off, the same boundary `run.py` already draws.
            return
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        grid_hits = vision._match_grid(gray, self.hero_templates, self.hero_keys)
        if vision.looks_like_roster(grid_hits):
            self.controls.jump("heroSelect")

    def _check_role_select(self):
        """Reads the real "Select a Role" screen, the same look `QueueServer.
        scan_role_select` takes on request — but every idle poll rather than a person's
        click, since `queueroles.scan` needs nothing this module doesn't already have
        permission to do. It takes its own screenshot rather than reusing `strip`: the
        row of cards can sit anywhere down the middle of the screen depending on
        resolution and UI scale, unlike the banner, which is only ever near the top.
        """
        try:
            result = queueroles.scan(self.role_templates, modes=self._modes, log=self.log)
        except Exception as problem:              # noqa: BLE001 - see `_loop`
            self.log("Queue watch's role check skipped a frame: %s" % problem)
            return
        # Recorded regardless of whether the role changed — this is also the one
        # signal that can tell Battle.net's presence apart from "picking a role" and
        # "actually searching", which report identically. See
        # `_role_select_confirmed_visible`.
        self._role_select_visible = result.on_screen
        self._role_select_checked_at = time.monotonic()
        if not result.on_screen:
            return
        role = result.effective_role
        if role is None or role == self.controls.role:
            return
        self.controls.role = role
        if self.on_role_detected:
            self.on_role_detected(result)

    def _role_select_confirmed_visible(self, when=None) -> bool:
        """Whether vision positively saw the role-select screen recently enough to
        trust. Decays to `False` — meaning "unknown, so trust presence" — once the last
        check is more than `ROLE_SELECT_FRESHNESS_SECONDS` old, which covers every case
        vision can't currently confirm anything at all: it's disabled, Overwatch isn't
        foreground, or a presence-only server never runs this check in the first place
        (`_role_select_checked_at` simply stays `None` forever)."""
        when = time.monotonic() if when is None else when
        if self._role_select_checked_at is None:
            return False
        if when - self._role_select_checked_at > ROLE_SELECT_FRESHNESS_SECONDS:
            return False
        return self._role_select_visible

    # ------------------------------------------------------------ the loop

    def _loop(self):
        while not self._stop.is_set():
            try:
                with self._tracker_lock:
                    observation = self.poll()
                    self.latest = observation
                    self._act(observation)
            except Exception as problem:            # noqa: BLE001 - a poll must not kill
                # A frame that can't be read is a frame we skip. Losing the watcher to one
                # bad grab would silently stop the phone updating, with nothing to show
                # for it but a dead thread.
                self.log("Queue watch skipped a frame: %s" % problem)
                observation = None

            interval = self._interval(observation)
            if observation is not None and observation.searching:
                # Waiting specifically for the one moment `queuevision.green_hint` exists
                # to catch early - see the module docstring. Any other observation (idle,
                # already `GAME_FOUND`, or a poll that failed) just sleeps the interval out
                # exactly as before. Deliberately outside the lock above: this is the
                # 50Hz trip-wire a slow presence read must never be able to share a
                # budget with — see `_tracker_lock`'s own note.
                self._fast_wait(interval)
            else:
                self._stop.wait(interval)

    def fold_presence(self, reading, when=None) -> QueueObservation:
        """One look at Battle.net's own presence, folded into the same tracker and
        acted on the same way a screen poll is. Called from `PresenceWatcher`'s own
        thread — see `_tracker_lock` for why every access here is guarded."""
        when = time.monotonic() if when is None else when
        with self._tracker_lock:
            role_select_visible = self._role_select_confirmed_visible(when)
            self.tracker.presence(reading, when, role_select_visible=role_select_visible)
            observation = self.tracker.observation(when)
            self.latest = observation
            self._act(observation)
        return observation

    def fold_presence_lost(self, when=None):
        """Tells the tracker a presence source has died or gone stale — see
        `QueueTracker.presence_lost`."""
        when = time.monotonic() if when is None else when
        with self._tracker_lock:
            self.tracker.presence_lost(when)
            observation = self.tracker.observation(when)
            self.latest = observation
            self._act(observation)
        return observation

    def _interval(self, observation) -> float:
        if observation is None:
            return IDLE_POLL_SECONDS
        if observation.in_queue or self.tracker.pending:
            # One promising frame is enough to speed up — the second frame that confirms
            # it then arrives a quarter-second later rather than a second and a half.
            return SEARCHING_POLL_SECONDS
        if self.server.session.kind in ("matchFound", "mapVote", "heroSelect"):
            # The tracker's own state resets to IDLE once the found banner's hold expires
            # (`GAME_FOUND_HOLD_SECONDS`), well before the map vote and hero select screens
            # are done — but `_check_match_progress` is exactly what's watching for a vote
            # landing or a pick locking in during that stretch, on the same idle-poll
            # thread. Falling back to `IDLE_POLL_SECONDS` here would mean the phone finds
            # out about a teammate's pick up to a second and a half late, which is the
            # slowest the app ever feels precisely when it's being watched most closely.
            return SEARCHING_POLL_SECONDS
        return IDLE_POLL_SECONDS

    def _fast_wait(self, budget: float) -> bool:
        """Spends up to `budget` seconds watching the last confirmed banner box for
        `queuevision.green_hint`'s cheap trip-wire, instead of sleeping through it blind.
        Returns whether it tripped — true or false, the budget is always fully spent
        either watching or waiting, so the caller's own timing is unaffected either way.

        Falls back to an ordinary sleep whenever there is no box fresh enough to trust;
        see `BOX_FRESHNESS_SECONDS` for why a stale one is worse than none.
        """
        if (self._last_box is None or self._last_box_at is None or
                time.monotonic() - self._last_box_at > BOX_FRESHNESS_SECONDS):
            self._stop.wait(budget)
            return False

        box = self._last_box
        deadline = time.monotonic() + budget
        while not self._stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            try:
                strip, _, _ = queuevision.capture_strip()
                if queuevision.green_hint(strip, box):
                    return True
            except Exception as problem:          # noqa: BLE001 - see `_loop`
                self.log("Queue watch's fast check skipped a frame: %s" % problem)
            self._stop.wait(min(FAST_TICK_SECONDS, max(0.0, deadline - time.monotonic())))
        return False

    # ------------------------------------------------------------ telling the server

    def _act(self, observation: QueueObservation):
        was, self._acted = self._acted, observation.state

        if observation.searching and was not in SEARCHING_STATES:
            if observation.mode:
                self.controls.mode = observation.mode
            self.log("Queue started — %s" % (observation.mode or self.controls.mode))
            self.controls.start_queue()
            self.reader.reset()
        elif observation.state == GAME_FOUND and was != GAME_FOUND:
            self.log("Game found after %ds" % observation.queue_elapsed)
            self.server.apply(protocol.match_found(
                self.controls.mode, self.controls.effective_role,
                observation.queue_elapsed))
        elif observation.state == IDLE and was in SEARCHING_STATES:
            self.log("Queue ended without a match")
            self.server.apply(protocol.cancelled("userLeft"))
            self.reader.reset()
        elif observation.searching:
            self._correct_mode(observation)
            self._correct_start(observation)

        if was != observation.state and self.on_change:
            self.on_change(observation)

    def _correct_mode(self, observation: QueueObservation):
        """Follows the banner when its colour turns out to say a different mode.

        The mode is taken from the frame a queue starts on, and that frame is the one
        most likely to be wrong: the banner is mid-animation, sliding in and part
        transparent, which is exactly when its colour reads oddly. Every later frame is
        another look at the same question, so a settled disagreement is worth taking —
        otherwise one bad frame at the start mislabels the whole queue on the phone.

        Only ever *changes* a mode, never blanks one: a hue that isn't in the table is
        `None`, which means "not measured", not "no longer competitive".
        """
        if not observation.mode or observation.mode == self.controls.mode:
            return
        if self.server.session.kind != "searching":
            return
        self.log("Queue is %s, not %s" % (observation.mode, self.controls.mode))
        self.controls.mode = observation.mode
        self.server.patch(mode=observation.mode)

    def _correct_start(self, observation: QueueObservation):
        """Keeps the session's `startedAt` honest once the screen's timer can be read.

        The phone ticks its own clock from `startedAt`, so this is the whole benefit of
        reading the timer at all: a queue the server joined late, or one whose start it
        guessed, silently becomes right on every device at once.
        """
        if observation.source != "timer" or self.server.session.kind != "searching":
            return
        drift = observation.queue_elapsed - self.server.session.elapsed()
        if abs(drift) > TIMER_DISAGREE_SECONDS:
            self.server.shift_start(drift)


# How many consecutive failed/timed-out reads before a source is declared dead. Three
# rather than one: a lone dropped frame is routine for a socket to a whole other
# application, and shouldn't retract a queue on its first hiccup.
PRESENCE_FAILURES_MAX = 3
# Reconnect backoff after a source dies, doubling from here up to the cap below.
PRESENCE_RECONNECT_SECONDS = 1.0
PRESENCE_RECONNECT_MAX_SECONDS = 30.0


class PresenceWatcher:
    """Polls a `bnetpresence` source on its own thread and folds every reading into
    `QueueWatcher`'s tracker.

    Deliberately not folded into `QueueWatcher._loop`: that loop's own `_fast_wait`
    spends its whole budget watching for `queuevision.green_hint` at 50Hz, and the
    module docstring is explicit about how little slack that leaves — a slow read on a
    socket to a whole other application must never be able to share that budget. This
    also means presence has its own cadence, which is appropriate: its own floor is a
    round trip to Blizzard's presence service, and polling faster than that buys
    nothing. `fold` is `QueueWatcher.fold_presence` in the app, or a plain list's
    `append` in a test — same injection style as `QueueWatcher(reader=...)`.

    A source declared dead (`PRESENCE_FAILURES_MAX` reads in a row with no known
    answer) is reported two ways: `fold_lost` tells the tracker (see
    `QueueTracker.presence_lost`), and `dead` is read by `QueueServer` to push a
    "disconnected" flag to the phone/watch over the heartbeat — see
    `docs/PROTOCOL.md`. `reopen`, when given, is tried on every dead poll and may
    replace `source` outright — the app's own callable additionally relaunches
    Battle.net when it isn't running at all (see `bnetpresence.launch_battlenet`).
    """

    def __init__(self, source, fold, fold_lost=None, reopen=None, log=None,
                clock=time.monotonic):
        self.source = source
        self.fold = fold
        self.fold_lost = fold_lost or (lambda when: None)
        # Called once when the source is first declared dead, and again on every dead
        # poll after that (`bnetpresence.launch_battlenet`'s own cooldown is what keeps
        # that from spamming a relaunch). Returns a replacement source, or `None` to
        # keep retrying the one already held — the app's own callable additionally
        # relaunches Battle.net itself when it isn't running at all; a plain test never
        # needs to pass one.
        self.reopen = reopen
        self.log = log or (lambda message: None)
        self.clock = clock
        self.latest = None
        self._thread = None
        self._stop = threading.Event()
        self._failures = 0
        self._dead = False

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def dead(self) -> bool:
        """Whether the source has been declared dead — the "disconnected" signal
        `QueueServer` pushes to the phone/watch over the heartbeat."""
        return self._dead

    def start(self):
        if self.running:
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="presencewatch")
        self._thread.start()
        self.log("Asking Battle.net what you're doing")

    def stop(self):
        self._stop.set()

    def poll(self, when=None) -> "queuepresence.PresenceReading":
        """One read, folded into the tracker. Public so a debug tool can drive it a
        step at a time without a thread."""
        when = self.clock() if when is None else when
        reading = self.source.read(when)
        self.latest = reading
        if reading.known:
            self._failures = 0
            self._dead = False
        else:
            self._failures += 1
            if self._failures >= PRESENCE_FAILURES_MAX:
                if not self._dead:
                    self._dead = True
                    self.log("Battle.net presence went quiet")
                    self.fold_lost(when)
                self._try_reopen()
        self.fold(reading, when)
        return reading

    def _try_reopen(self):
        if self.reopen is None:
            return
        try:
            replacement = self.reopen()
        except Exception as problem:                    # noqa: BLE001 - best effort
            self.log("Couldn't reconnect to Battle.net: %s" % problem)
            return
        if replacement is not None:
            self.source.close()
            self.source = replacement

    def _loop(self):
        backoff = PRESENCE_RECONNECT_SECONDS
        while not self._stop.is_set():
            try:
                self.poll()
            except Exception as problem:            # noqa: BLE001 - a poll must not kill
                self.log("Presence watch skipped a read: %s" % problem)

            if not self._dead:
                # A lone dropped read is routine for a socket to a whole other
                # application, and polls again at the source's own ordinary cadence —
                # only a source declared dead (`PRESENCE_FAILURES_MAX` in a row) backs
                # off, so it isn't hammering a socket or a process handle that isn't
                # answering.
                self._stop.wait(self.source.poll_seconds)
                backoff = PRESENCE_RECONNECT_SECONDS
            else:
                self._stop.wait(min(backoff, PRESENCE_RECONNECT_MAX_SECONDS))
                backoff = min(backoff * 2, PRESENCE_RECONNECT_MAX_SECONDS)

    def close(self):
        self.stop()
        self.source.close()
