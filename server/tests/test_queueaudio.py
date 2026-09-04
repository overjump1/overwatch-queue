"""Hearing a match land, tested without a recording.

Real captures are what found the numbers in `queueaudio`'s own docstring — a genuine
silence, a genuine tone, three matches minutes apart agreeing on both within measured
tolerances. None of that needs replaying here. What these tests build instead is the
shape those numbers describe: silence, then a tone, at levels chosen to sit clearly on
each side of the module's own thresholds, so what's actually under test is the state
machine deciding when that shape is complete — not whether a fixture sounds like Overwatch.
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "server"))

from owqserver import queueaudio as qa                            # noqa: E402

HAVE_NUMPY = qa.AUDIO_AVAILABLE
if HAVE_NUMPY:
    import numpy as np

SAMPLE_RATE = 48000


def silence(seconds, amplitude=0.001, seed=0):
    """Near-total quiet, with a whisper of noise — real captures never measured exact
    zero, just close to it."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SAMPLE_RATE)
    return (rng.standard_normal(n) * amplitude).astype(np.float32)


def tone(seconds, amplitude=0.03, seed=1):
    """A held loud stretch. What frequency it's at is not this detector's business —
    only loudness relative to what came before is — so noise stands in fine."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SAMPLE_RATE)
    return (rng.standard_normal(n) * amplitude).astype(np.float32)


@unittest.skipUnless(HAVE_NUMPY, "numpy not installed")
class AudioFoundDetectorTests(unittest.TestCase):
    def _detector(self):
        return qa.AudioFoundDetector(sample_rate=SAMPLE_RATE)

    def test_silence_then_a_held_tone_fires(self):
        detector = self._detector()
        self.assertFalse(detector.feed(silence(2.0)))
        self.assertTrue(detector.feed(tone(2.0)))

    def test_a_tone_with_no_silence_first_does_not_fire(self):
        # Ordinary gameplay's own ambient floor: loud more or less continuously, never
        # quiet first. See the module docstring for the measured floor this represents.
        detector = self._detector()
        fired = detector.feed(tone(3.0, amplitude=0.02))
        self.assertFalse(fired)

    def test_silence_too_short_does_not_arm_it(self):
        # Shorter than MIN_QUIET_SECONDS - a brief lull, not the real wait.
        detector = self._detector()
        detector.feed(silence(qa.MIN_QUIET_SECONDS * 0.5))
        self.assertFalse(detector.feed(tone(2.0)))

    def test_a_lone_loud_hop_does_not_fire(self):
        # Shorter than MIN_LOUD_SECONDS - a single loud sample is not yet a tone.
        detector = self._detector()
        detector.feed(silence(2.0))
        self.assertFalse(detector.feed(tone(qa.HOP_SECONDS)))

    def test_only_fires_once_per_tone(self):
        detector = self._detector()
        detector.feed(silence(2.0))
        self.assertTrue(detector.feed(tone(0.5)))
        # Same tone, still going: already confirmed, not confirmed again.
        self.assertFalse(detector.feed(tone(2.0)))

    def test_a_second_real_tone_after_a_second_real_silence_fires_again(self):
        detector = self._detector()
        detector.feed(silence(2.0))
        self.assertTrue(detector.feed(tone(0.5)))
        detector.feed(silence(2.0))
        self.assertTrue(detector.feed(tone(0.5)))

    def test_a_hop_between_the_two_thresholds_does_not_reset_a_loud_run_in_progress(self):
        # The bug a full-video sweep actually found: a real tone's own envelope dips
        # below `loud_rms_min` for individual 20ms hops well before it ends, and a first
        # version of this method mistook that dip for the tone ending, so total loud time
        # never reached `min_loud_seconds` and nothing ever fired. A wobble hop - neither
        # clearly loud nor clearly quiet - must leave an in-progress run alone.
        detector = self._detector()
        detector.feed(silence(2.0))
        wobble_rms = (qa.QUIET_RMS_MAX + qa.LOUD_RMS_MIN) / 2.0
        hop_samples = detector.hop_size
        loud_hop = tone(qa.HOP_SECONDS, amplitude=qa.LOUD_RMS_MIN * 1.5)
        wobble_hop = (np.random.default_rng(2).standard_normal(hop_samples) *
                     wobble_rms).astype(np.float32)
        fired = False
        # Enough loud hops to nearly confirm, one wobble hop, then enough to finish -
        # without the fix this never reaches MIN_LOUD_SECONDS because the wobble hop
        # keeps zeroing the accumulator.
        hops_needed = int(qa.MIN_LOUD_SECONDS / qa.HOP_SECONDS) + 2
        for _ in range(hops_needed // 2):
            fired = fired or detector.feed(loud_hop)
        fired = fired or detector.feed(wobble_hop)
        for _ in range(hops_needed // 2 + 1):
            fired = fired or detector.feed(loud_hop)
        self.assertTrue(fired)

    def test_chunk_size_does_not_change_the_answer(self):
        # Whether a caller hands over 20ms at a time or 2 seconds at a time, the hops
        # inside are identical - only the carry buffer's bookkeeping differs.
        big_chunks = self._detector()
        fired_big = big_chunks.feed(silence(2.0)) or big_chunks.feed(tone(2.0))

        tiny_chunks = self._detector()
        stream = np.concatenate([silence(2.0), tone(2.0)])
        hop = tiny_chunks.hop_size
        fired_tiny = False
        for start in range(0, len(stream), 137):        # an awkward, non-hop-aligned size
            fired_tiny = fired_tiny or tiny_chunks.feed(stream[start:start + 137])

        self.assertEqual(fired_big, fired_tiny)


@unittest.skipUnless(HAVE_NUMPY, "numpy not installed")
class FromInt16Tests(unittest.TestCase):
    def test_scales_full_range_to_unit_interval(self):
        samples = np.array([-32768, 0, 32767], dtype=np.int16)
        scaled = qa.from_int16(samples)
        self.assertAlmostEqual(float(scaled[0]), -1.0, places=3)
        self.assertAlmostEqual(float(scaled[1]), 0.0, places=3)
        self.assertAlmostEqual(float(scaled[2]), 1.0, places=3)


if __name__ == "__main__":
    unittest.main()
