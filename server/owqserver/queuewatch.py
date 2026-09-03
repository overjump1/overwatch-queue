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

`QueueTracker` holds all of that and touches nothing: no screen, no clock, no server. It
is handed frames and a time and returns an observation, which is what lets the whole of
the reasoning above be tested against a made-up minute. `QueueWatcher` is the thin part
around it that captures, polls and calls `QueueServer.apply`.

Like `queuevision`, and unlike the hero-select half of `vision.py`, nothing here focuses a
window or touches the mouse. It only looks.
"""
from __future__ import annotations

import threading
import time

from . import protocol, queuedigits, queuevision, vision

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

IDLE_POLL_SECONDS = 1.5
SEARCHING_POLL_SECONDS = 0.25


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
        self.source = source                    # "timer" (read) or "self" (guessed)
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

    def _saw_banner(self, hit, when: float, timer):
        self.looking = True
        self._hidden_visible = self._hidden_total = 0.0
        self.confidence = hit.confidence
        self.kind = hit.kind
        if hit.mode:
            self.mode = hit.mode

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

        if self.state == GAME_FOUND and when - self._entered < GAME_FOUND_HOLD_SECONDS:
            # The banner lingers for a moment after the match lands. That is the same
            # match, not the next queue.
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
            if self.searching and self._hidden_total >= HIDDEN_BLIND_SECONDS:
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
        if self._hidden_visible >= HIDDEN_GRACE_SECONDS or \
                self._hidden_total >= HIDDEN_BLIND_SECONDS:
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


class QueueWatcher:
    """Polls the screen and moves the server's phase to match.

    Takes a `Controls` rather than a `QueueServer` because the queue's mode and role are
    only half readable: the banner's colour is the mode, but nothing on it says which role
    was queued, so the panel's selection stays the source for that. Going through
    `Controls` also means this gets `effective_role` — the open-queue rule for Arcade and
    Mystery Heroes — rather than reimplementing it.
    """

    def __init__(self, controls, log=None, reader=None, tracker=None):
        self.controls = controls
        self.server = controls.server
        self.log = log or (lambda message: None)
        self.tracker = tracker or QueueTracker()
        self.reader = reader if reader is not None else \
            queuedigits.DigitReader(log=lambda message: self.log(message))
        self.latest = None
        self.on_change = None           # called (on the poll thread) after a state moves

        self._modes = queuevision.load_modes()
        self._acted = IDLE
        self._thread = None
        self._stop = threading.Event()

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
        timer = None
        if hit is not None and hit.searching:
            timer = self.reader.read(hit.timer_patch(strip), when)
        return self.tracker.update(hit, when, visible=True, timer=timer)

    # ------------------------------------------------------------ the loop

    def _loop(self):
        while not self._stop.is_set():
            try:
                observation = self.poll()
                self.latest = observation
                self._act(observation)
            except Exception as problem:            # noqa: BLE001 - a poll must not kill
                # A frame that can't be read is a frame we skip. Losing the watcher to one
                # bad grab would silently stop the phone updating, with nothing to show
                # for it but a dead thread.
                self.log("Queue watch skipped a frame: %s" % problem)
                observation = None
            self._stop.wait(self._interval(observation))

    def _interval(self, observation) -> float:
        if observation is None:
            return IDLE_POLL_SECONDS
        if observation.in_queue or self.tracker.pending:
            # One promising frame is enough to speed up — the second frame that confirms
            # it then arrives a quarter-second later rather than a second and a half.
            return SEARCHING_POLL_SECONDS
        return IDLE_POLL_SECONDS

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
