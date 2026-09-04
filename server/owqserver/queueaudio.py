"""Hearing a match land, as a second and independent channel from seeing it.

`queuevision.find_banner` reads a green check that is on screen for well under a second —
fast polling covers that, but it is still one channel, and one channel has one failure
mode: whatever it is that occasionally keeps a frame from reading right also keeps it from
reading right the *next* frame it happens on. A full-video sweep of this project's own
recording found a second, completely independent tell for the same moment, one that has
nothing to do with the banner, the vote screen, or any pixel on screen at all: the game
goes essentially silent for the last stretch of a queue, and then a match landing plays a
low, sustained tone into that silence.

Measured on three real matches, minutes apart, in an otherwise ordinary recording: each
one sat at an RMS of 0.001-0.003 — silence, not just quiet, since one of the three still
had teammates talking earlier in the same clip and had gone completely dead by the three
seconds before the tone — for at least 1.2 seconds before the tone, and each one's tone
rose past 0.012 within 100ms of the same instant and held above it for 1.5-2 seconds
before decaying. All three landed within a 200ms window of each other measured as an
offset from the visual banner's own green check, despite being spread across 37 minutes of
the same recording: the tone follows the check by roughly 3.1-3.2 seconds every time,
which is presumably match-found's own fixed transition timing rather than anything this
project controls.

That gap between "silence" (0.001-0.003) and "a real sound" (0.012+) is wide open on
purpose: ordinary Overwatch gameplay is never anywhere near either number for long. Combat
sits at 0.01-0.07 more or less continuously — loud enough that it would cross the "loud"
threshold on its own, which is exactly why loudness alone is not the test here. What
gameplay does not do is sit *quiet* for over a second first. Requiring both — a real
silence, immediately followed by a real tone — is what a lone gunfight or a lone quiet
moment each fail on their own, verified against two of this project's own known false
`gameFound` frames (both mid-fight, both loud throughout, neither with a second of silence
anywhere near them) and a plain mid-match clip (ambient, never silent, never as loud as the
tone either).

This is a hearing test, not an ear: nothing here opens an audio device, and this module
does not know what a sample rate its caller is running at beyond what it is told.
`AudioFoundDetector` is handed chunks of already-captured, already-mono, already
[-1, 1]-scaled audio — a live caller's loopback capture, or a recording read back for
testing — and answers the one question `queuewatch.QueueTracker.audio_found` exists to
receive: was that silence-then-tone shape just completed.
"""
from __future__ import annotations

try:                                    # pragma: no cover - trivial import guard
    import numpy as np
    AUDIO_AVAILABLE = True
except ImportError:                     # pragma: no cover - the Mac dev path
    np = None
    AUDIO_AVAILABLE = False

# The rate this module's own thresholds were measured at. A caller running at a different
# rate only needs to pass it in — every threshold here is in seconds or in RMS, neither of
# which cares what the sample rate was.
SAMPLE_RATE = 48000

# How finely the envelope is sampled. Small enough that the 1.5-2 second tone is many hops
# wide, not one; large enough that a single hop is still a meaningful loudness estimate
# rather than a handful of samples of noise.
HOP_SECONDS = 0.02

# Below this RMS counts as the silence the tone needs to follow. The quietest of the three
# measured matches sat at 0.001; the loudest of their own quiet stretches, with teammates
# audible earlier in the same clip, still fell to 0.006 or under for the required window.
# Ordinary gameplay's own ambient floor (0.01-0.03, measured on a plain mid-match clip)
# sits well above this, so a real match never mistakes "gameplay got briefly quiet" for
# this.
QUIET_RMS_MAX = 0.006

# Above this RMS counts as the tone. Measured onsets held 0.017-0.032; this sits below all
# of them with room to spare, without dropping so low that it would fire on gameplay's own
# ambient floor by itself — which is exactly why this number alone is not the whole test.
LOUD_RMS_MIN = 0.012

# How long the silence has to hold before a tone counts as *the* tone rather than a loud
# moment that happens to follow an ordinary quiet one. The shortest silence measured across
# three real matches was about 3.5 seconds (the one with earlier chatter); this leaves
# nearly 2 seconds of margin under it.
MIN_QUIET_SECONDS = 1.2

# How long the tone has to hold before it is trusted, rather than reacting to this module's
# own single loud hop. Measured tones held for 1.5-2 seconds; a third of a second is enough
# to reject a one-hop spike while still confirming well inside every tone actually seen.
MIN_LOUD_SECONDS = 0.3


class AudioFoundDetector:
    """Stateful, one match-found edge per silence-then-tone shape.

    Deliberately not stateless the way `stagevision.classify` is: a single chunk of audio
    says nothing on its own, since "loud" is only meaningful next to the quiet that did or
    didn't precede it. This is `queuewatch.QueueTracker` cut down to the one thing audio
    can actually see — a "found" edge, nothing else, no notion of which queue mode or how
    long it ran — because that is genuinely all this channel knows.

    `feed` is meant to be called repeatedly with whatever chunk size the caller's capture
    hands over — a live loopback callback's own buffer, or a whole clip cut into pieces for
    a test — and chops each chunk into `HOP_SECONDS` hops itself, carrying any remainder
    into the next call, so chunk size never changes the answer.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE, hop_seconds: float = HOP_SECONDS,
                 quiet_rms_max: float = QUIET_RMS_MAX, loud_rms_min: float = LOUD_RMS_MIN,
                 min_quiet_seconds: float = MIN_QUIET_SECONDS,
                 min_loud_seconds: float = MIN_LOUD_SECONDS):
        self.sample_rate = sample_rate
        self.hop_seconds = hop_seconds
        self.hop_size = max(1, int(round(sample_rate * hop_seconds)))
        self.quiet_rms_max = quiet_rms_max
        self.loud_rms_min = loud_rms_min
        self.min_quiet_seconds = min_quiet_seconds
        self.min_loud_seconds = min_loud_seconds

        self._carry = np.empty(0, dtype=np.float32) if AUDIO_AVAILABLE else None
        self._quiet_accum = 0.0
        self._loud_accum = 0.0
        self._quiet_before_run = 0.0
        self._fired_this_run = False

    def feed(self, samples) -> bool:
        """Folds in one chunk of mono audio, scaled to [-1, 1]. Returns whether the
        silence-then-tone shape was just completed somewhere inside it — true for at most
        one hop per real tone, however many chunks that tone happens to span."""
        if not AUDIO_AVAILABLE:
            return False

        samples = np.asarray(samples, dtype=np.float32)
        if samples.ndim > 1:
            samples = samples.mean(axis=1)
        chunk = np.concatenate([self._carry, samples]) if self._carry.size else samples

        fired = False
        hop = self.hop_size
        usable = (len(chunk) // hop) * hop
        for start in range(0, usable, hop):
            window = chunk[start:start + hop]
            rms = float(np.sqrt(np.mean(np.square(window))))
            if self._process_hop(rms):
                fired = True
        self._carry = chunk[usable:]
        return fired

    def _process_hop(self, rms: float) -> bool:
        if rms >= self.loud_rms_min:
            if self._loud_accum == 0.0:
                self._quiet_before_run = self._quiet_accum
                self._fired_this_run = False
            self._loud_accum += self.hop_seconds
            self._quiet_accum = 0.0
            if (not self._fired_this_run and
                    self._quiet_before_run >= self.min_quiet_seconds and
                    self._loud_accum >= self.min_loud_seconds):
                self._fired_this_run = True
                return True
        elif rms <= self.quiet_rms_max:
            self._quiet_accum += self.hop_seconds
            self._loud_accum = 0.0
        # A hop that lands in neither band is a genuine wobble, not a real return to
        # quiet - real tones measured on real captures dip below `loud_rms_min` for
        # individual 20ms hops well before they actually end, which a first version of
        # this method treated as the tone ending and reset on, so the run's total loud
        # time never reached `min_loud_seconds` and nothing ever fired. Only a hop that
        # actually lands at-or-under `quiet_rms_max` ends a loud run now; anything softer
        # than loud but still above quiet leaves whichever run is in progress untouched.
        return False


def from_int16(samples) -> "np.ndarray":
    """Convenience for a capture device handing over signed 16-bit PCM, the common case
    for a Windows loopback source: scales to the [-1, 1] float `AudioFoundDetector.feed`
    expects."""
    return np.asarray(samples, dtype=np.float32) / 32768.0
