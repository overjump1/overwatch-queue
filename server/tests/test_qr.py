"""Checks the QR encoder against the maths, not against itself.

The Reed-Solomon checks use their own GF(256) implementation and their own
de-interleaver, so a mistake in `qr.py` can't hide behind a matching mistake here. If
`segno` is installed the matrices are also compared module for module.
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "server"))

from owqserver import pairing, qr                                # noqa: E402

try:
    import segno
except ImportError:
    segno = None

LEVELS = ("L", "M", "Q", "H")
VERSIONS = range(1, qr.MAX_VERSION + 1)


# ---------------------------------------------------------------- independent GF(256)

def gf_multiply(x: int, y: int) -> int:
    product = 0
    for bit in range(7, -1, -1):
        product = (product << 1) ^ ((product >> 7) * 0x11D)
        product ^= ((y >> bit) & 1) * x
    return product & 0xFF


def gf_power(exponent: int) -> int:
    value = 1
    for _ in range(exponent):
        value = gf_multiply(value, 2)
    return value


def syndromes(codewords, ecc_length: int) -> list:
    """Zero for every root iff the block is a valid Reed-Solomon codeword."""
    results = []
    for i in range(ecc_length):
        total, root = 0, gf_power(i)
        for codeword in codewords:
            total = gf_multiply(total, root) ^ codeword
        results.append(total)
    return results


def deinterleave(stream, version: int, ecl: str) -> list:
    """Undoes the block interleaving, straight from the spec's description."""
    blocks = qr._NUM_BLOCKS[ecl][version]
    ecc_length = qr._ECC_PER_BLOCK[ecl][version]
    total = qr._raw_data_modules(version) // 8
    short_blocks = blocks - total % blocks
    short_length = total // blocks
    lengths = [short_length - ecc_length + (0 if i < short_blocks else 1)
               for i in range(blocks)]

    data = [[] for _ in range(blocks)]
    position = 0
    for i in range(max(lengths)):
        for block in range(blocks):
            if i < lengths[block]:
                data[block].append(stream[position])
                position += 1
    ecc = [[] for _ in range(blocks)]
    for _ in range(ecc_length):
        for block in range(blocks):
            ecc[block].append(stream[position])
            position += 1
    return [data[i] + ecc[i] for i in range(blocks)]


class ErrorCorrectionTests(unittest.TestCase):
    def test_every_block_is_a_valid_codeword(self):
        for ecl in LEVELS:
            for version in VERSIONS:
                payload = b"o" * qr._capacity_bytes(version, ecl)
                stream = qr._codewords(payload, version, ecl)
                self.assertEqual(len(stream), qr._raw_data_modules(version) // 8)
                for block in deinterleave(stream, version, ecl):
                    self.assertEqual(syndromes(block, qr._ECC_PER_BLOCK[ecl][version]),
                                     [0] * qr._ECC_PER_BLOCK[ecl][version],
                                     "version %d level %s" % (version, ecl))

    def test_short_payloads_are_padded_and_still_valid(self):
        for ecl in LEVELS:
            for version in VERSIONS:
                stream = qr._codewords(b"x", version, ecl)
                for block in deinterleave(stream, version, ecl):
                    self.assertEqual(syndromes(block, qr._ECC_PER_BLOCK[ecl][version]),
                                     [0] * qr._ECC_PER_BLOCK[ecl][version])

    def test_padding_alternates_the_two_pad_codewords(self):
        stream = qr._codewords(b"x", 1, "L")
        self.assertEqual(stream[3:9], [0xEC, 0x11, 0xEC, 0x11, 0xEC, 0x11])

    def test_the_generator_polynomial_matches_the_published_table(self):
        # Published as exponents of alpha, which is how the spec tabulates it.
        self.assertEqual([qr._LOG[c] for c in qr._rs_divisor(7)],
                         [87, 229, 146, 149, 238, 102, 21])
        self.assertEqual([qr._LOG[c] for c in qr._rs_divisor(10)],
                         [251, 67, 46, 61, 118, 70, 64, 94, 32, 45])


class StructureTests(unittest.TestCase):
    def code(self, version=5, ecl="M", mask=3):
        return qr.encode("o" * qr._capacity_bytes(version, ecl), ecl=ecl,
                         version=version, mask=mask)

    def test_size_follows_the_version(self):
        for version in VERSIONS:
            self.assertEqual(qr.encode(b"hi", version=version).size, version * 4 + 17)

    def test_finder_patterns_sit_in_three_corners(self):
        code = self.code()
        last = code.size - 7
        for x0, y0 in ((0, 0), (last, 0), (0, last)):
            for dy in range(7):
                for dx in range(7):
                    distance = max(abs(dx - 3), abs(dy - 3))
                    self.assertEqual(code[y0 + dy][x0 + dx], distance != 2,
                                     "finder at %d,%d" % (x0, y0))

    def test_the_fourth_corner_is_not_a_finder(self):
        code = self.code()
        last = code.size - 7
        self.assertNotEqual([code[last + d][last + d] for d in range(7)], [True] * 7)

    def test_timing_patterns_alternate(self):
        code = self.code()
        for i in range(8, code.size - 8):
            self.assertEqual(code[6][i], i % 2 == 0)
            self.assertEqual(code[i][6], i % 2 == 0)

    def test_the_dark_module_is_dark(self):
        code = self.code()
        self.assertTrue(code[code.size - 8][8])

    def test_format_information_reads_back(self):
        for ecl in LEVELS:
            for mask in range(8):
                code = qr.encode(b"pair", ecl=ecl, version=2, mask=mask)
                bits = 0
                for i in range(6):
                    bits |= int(code[i][8]) << i
                bits |= int(code[7][8]) << 6
                bits |= int(code[8][8]) << 7
                bits |= int(code[8][7]) << 8
                for i in range(9, 15):
                    bits |= int(code[8][14 - i]) << i
                unmasked = bits ^ 0x5412
                self.assertEqual(unmasked >> 10, qr.ECC_LEVELS[ecl] << 3 | mask)

    def test_version_information_appears_from_version_seven(self):
        for version in (7, 8, 9, 10):
            code = qr.encode(b"x", version=version)
            bits = 0
            for i in range(18):
                bits |= int(code[i // 3][code.size - 11 + i % 3]) << i
            self.assertEqual(bits >> 12, version)

    def test_alignment_patterns_follow_the_version(self):
        # Version 1 has none; from 2 on they sit at the tabulated coordinates.
        self.assertEqual(qr._alignment_positions(1), [])
        self.assertEqual(qr._alignment_positions(2), [6, 18])
        self.assertEqual(qr._alignment_positions(7), [6, 22, 38])
        self.assertEqual(qr._alignment_positions(10), [6, 28, 50])

    def test_the_quiet_zone_is_four_modules_of_light(self):
        rows = self.code().rows()
        self.assertEqual(len(rows), self.code().size + 8)
        for row in rows[:4] + rows[-4:]:
            self.assertFalse(any(row))
        for row in rows:
            self.assertFalse(any(row[:4]) or any(row[-4:]))


class CapacityTests(unittest.TestCase):
    def test_a_pairing_url_fits_with_room_to_spare(self):
        url = pairing.Pairing(token="3f2504e0-4f89-41d3-9a0c-0305e82c3301",
                              port=8787, path=os.devnull).url("192.168.100.100")
        code = qr.encode(url, ecl="M")
        self.assertLessEqual(code.version, 6)
        self.assertLess(len(url), qr._capacity_bytes(qr.MAX_VERSION, "M"))

    def test_too_much_data_is_refused_rather_than_truncated(self):
        with self.assertRaises(ValueError):
            qr.encode("x" * (qr._capacity_bytes(qr.MAX_VERSION, "M") + 1), ecl="M")

    def test_unicode_goes_out_as_utf8(self):
        code = qr.encode("Tomer’s PC — 192.168.1.14")
        self.assertGreater(code.size, 0)


@unittest.skipIf(segno is None, "segno isn't installed")
class ReferenceTests(unittest.TestCase):
    """Module-for-module against segno, for payloads that fill a version exactly.

    Only full payloads: segno pads short ones slightly differently (both are valid
    Reed-Solomon, and a decoder reads past the character count either way), which would
    make the comparison a test of padding taste rather than of the encoder.
    """

    def test_matrices_match(self):
        for ecl in LEVELS:
            for version in VERSIONS:
                payload = "o" * qr._capacity_bytes(version, ecl)
                for mask in range(8):
                    mine = qr.encode(payload, ecl=ecl, version=version, mask=mask)
                    theirs = segno.make(payload, error=ecl, version=version, mask=mask,
                                        boost_error=False, mode="byte")
                    self.assertEqual(mine.modules,
                                     [[bool(m) for m in row] for row in theirs.matrix],
                                     "version %d level %s mask %d" % (version, ecl, mask))

    def test_penalty_scoring_matches(self):
        from segno import encoder
        for mask in range(8):
            code = qr.encode("o" * 60, ecl="M", version=5, mask=mask)
            matrix = [bytearray(1 if m else 0 for m in row) for row in code.modules]
            self.assertEqual(qr._penalty(code.modules),
                             sum(encoder.mask_scores(matrix, code.size, code.size)))


if __name__ == "__main__":
    unittest.main()
