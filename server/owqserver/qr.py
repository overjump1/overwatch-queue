"""A QR encoder, so the server needs nothing installed.

The pairing screen has to put a token in front of a phone camera, and the point of this
server is that it runs on a bare Python install — `python3 server/run.py`, no `pip
install` first, on whatever machine happens to have Overwatch on it. That rules out
`segno`/`qrcode`, so byte-mode QR lives here.

ISO/IEC 18004, versions 1-10 — 213 bytes at level M, against a pairing URL of about 80.
`server/tests/test_qr.py` checks every block's Reed-Solomon syndromes with an
independent field implementation, and compares module-for-module against `segno` when
that happens to be installed. That is what keeps the tables below honest.
"""
from __future__ import annotations

# ---------------------------------------------------------------- GF(256)

_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x = (_x << 1) ^ (0x11D if _x & 0x80 else 0)
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_divisor(degree: int) -> list:
    """Coefficients of the degree-`degree` generator polynomial, minus its leading 1."""
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _mul(root, 2)
    return result


def _rs_remainder(data, divisor) -> list:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, c in enumerate(divisor):
            result[i] ^= _mul(c, factor)
    return result


# ---------------------------------------------------------------- spec tables

# Error-correction level -> the two bits that go in the format information.
ECC_LEVELS = {"L": 0b01, "M": 0b00, "Q": 0b11, "H": 0b10}

# Indexed [level][version]; index 0 is unused so version numbers read directly.
_ECC_PER_BLOCK = {
    "L": (0, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18),
    "M": (0, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26),
    "Q": (0, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24),
    "H": (0, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28),
}
_NUM_BLOCKS = {
    "L": (0, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4),
    "M": (0, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5),
    "Q": (0, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8),
    "H": (0, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8),
}

MAX_VERSION = 10


def _raw_data_modules(version: int) -> int:
    """Modules available for data and ECC, i.e. everything but the function patterns."""
    result = (16 * version + 128) * version + 64
    if version >= 2:
        aligns = version // 7 + 2
        result -= (25 * aligns - 10) * aligns - 55
        if version >= 7:
            result -= 36                              # the two version-information blocks
    return result


def _capacity_bytes(version: int, ecl: str) -> int:
    """How many bytes of payload fit, after the mode indicator and length field."""
    codewords = _raw_data_modules(version) // 8
    codewords -= _ECC_PER_BLOCK[ecl][version] * _NUM_BLOCKS[ecl][version]
    header_bits = 4 + (8 if version <= 9 else 16)
    return codewords - (header_bits + 7) // 8


def _alignment_positions(version: int) -> list:
    if version == 1:
        return []
    count = version // 7 + 2
    step = (version * 4 + count * 2 + 1) // (count * 2 - 2) * 2
    positions = []
    pos = version * 4 + 10
    for _ in range(count - 1):
        positions.insert(0, pos)
        pos -= step
    positions.insert(0, 6)
    return positions


# ---------------------------------------------------------------- the code itself


class QrCode:
    """A rendered QR symbol. `modules[y][x]` is True where the module is dark."""

    def __init__(self, modules, version: int):
        self.modules = modules
        self.version = version
        self.size = len(modules)

    def __getitem__(self, y):
        return self.modules[y]

    def rows(self, quiet: int = 4) -> list:
        """Rows of booleans including the quiet zone, ready to draw."""
        width = self.size + quiet * 2
        blank = [False] * width
        out = [list(blank) for _ in range(quiet)]
        for row in self.modules:
            out.append([False] * quiet + list(row) + [False] * quiet)
        out.extend([list(blank) for _ in range(quiet)])
        return out

    def to_text(self) -> str:
        """Half-block rendering, for a terminal that has no GUI."""
        rows = self.rows()
        if len(rows) % 2:
            rows.append([False] * len(rows[0]))
        lines = []
        for y in range(0, len(rows), 2):
            line = ""
            for top, bottom in zip(rows[y], rows[y + 1]):
                line += {(0, 0): "█", (1, 1): " ",
                         (1, 0): "▄", (0, 1): "▀"}[(int(top), int(bottom))]
            lines.append(line)
        return "\n".join(lines)


def encode(text, ecl: str = "M", version: int = None, mask: int = None) -> QrCode:
    """Encode `text` in byte mode. Picks the smallest version that fits by default."""
    if ecl not in ECC_LEVELS:
        raise ValueError("error-correction level must be one of L, M, Q, H")
    data = text.encode("utf-8") if isinstance(text, str) else bytes(text)

    if version is None:
        for candidate in range(1, MAX_VERSION + 1):
            if len(data) <= _capacity_bytes(candidate, ecl):
                version = candidate
                break
        else:
            raise ValueError(
                "%d bytes is more than version %d level %s holds (%d)"
                % (len(data), MAX_VERSION, ecl, _capacity_bytes(MAX_VERSION, ecl)))
    elif len(data) > _capacity_bytes(version, ecl):
        raise ValueError("%d bytes does not fit in version %d level %s" % (len(data), version, ecl))

    codewords = _codewords(data, version, ecl)
    grid, reserved = _draw_function_patterns(version, ecl)
    _draw_codewords(grid, reserved, codewords)

    if mask is None:
        best, best_penalty = 0, None
        for candidate in range(8):
            _apply_mask(grid, reserved, candidate)
            _draw_format_bits(grid, ecl, candidate)
            penalty = _penalty(grid)
            if best_penalty is None or penalty < best_penalty:
                best, best_penalty = candidate, penalty
            _apply_mask(grid, reserved, candidate)   # XOR again to undo
        mask = best
    _apply_mask(grid, reserved, mask)
    _draw_format_bits(grid, ecl, mask)
    return QrCode(grid, version)


def _codewords(data: bytes, version: int, ecl: str) -> list:
    """Payload -> bit stream -> padded codewords -> ECC blocks, interleaved."""
    bits = []

    def push(value, width):
        for i in range(width - 1, -1, -1):
            bits.append((value >> i) & 1)

    push(0b0100, 4)                                  # byte mode
    push(len(data), 8 if version <= 9 else 16)
    for byte in data:
        push(byte, 8)

    total = (_raw_data_modules(version) // 8
             - _ECC_PER_BLOCK[ecl][version] * _NUM_BLOCKS[ecl][version])
    bits.extend([0] * min(4, total * 8 - len(bits)))
    bits.extend([0] * (-len(bits) % 8))
    for pad in _cycle_pad(total * 8 - len(bits)):
        push(pad, 8)

    codewords = [int("".join(str(b) for b in bits[i:i + 8]), 2) for i in range(0, len(bits), 8)]
    return _interleave(codewords, version, ecl)


def _cycle_pad(missing_bits: int) -> list:
    pads = []
    for i in range(missing_bits // 8):
        pads.append(0xEC if i % 2 == 0 else 0x11)
    return pads


def _interleave(data: list, version: int, ecl: str) -> list:
    """Split into blocks, append each block's ECC, then interleave as the spec requires."""
    blocks_total = _NUM_BLOCKS[ecl][version]
    ecc_len = _ECC_PER_BLOCK[ecl][version]
    raw = _raw_data_modules(version) // 8
    short_blocks = blocks_total - raw % blocks_total
    short_len = raw // blocks_total

    divisor = _rs_divisor(ecc_len)
    blocks, k = [], 0
    for i in range(blocks_total):
        length = short_len - ecc_len + (0 if i < short_blocks else 1)
        chunk = data[k:k + length]
        k += length
        ecc = _rs_remainder(chunk, divisor)
        padded = chunk + ([0] if i < short_blocks else [])   # keeps rows rectangular
        blocks.append(padded + ecc)

    result = []
    for i in range(len(blocks[0])):
        for j, block in enumerate(blocks):
            if i != short_len - ecc_len or j >= short_blocks:
                result.append(block[i])
    return result


# ---------------------------------------------------------------- module placement


def _draw_function_patterns(version: int, ecl: str):
    size = version * 4 + 17
    grid = [[False] * size for _ in range(size)]
    reserved = [[False] * size for _ in range(size)]

    def set_module(x, y, dark):
        if 0 <= x < size and 0 <= y < size:
            grid[y][x] = dark
            reserved[y][x] = True

    for i in range(size):                             # timing patterns
        set_module(6, i, i % 2 == 0)
        set_module(i, 6, i % 2 == 0)

    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                distance = max(abs(dx), abs(dy))
                set_module(cx + dx, cy + dy, distance != 2 and distance != 4)

    aligns = _alignment_positions(version)
    for i, cy in enumerate(aligns):
        for j, cx in enumerate(aligns):
            corner = (i, j) in ((0, 0), (0, len(aligns) - 1), (len(aligns) - 1, 0))
            if corner:
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    set_module(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)

    _draw_format_bits(grid, ecl, 0, reserved)         # reserve only; rewritten later
    set_module(8, size - 8, True)                     # the always-dark module

    if version >= 7:
        rem = version
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = version << 12 | rem
        for i in range(18):
            dark = (bits >> i) & 1 == 1
            a, b = size - 11 + i % 3, i // 3
            set_module(a, b, dark)
            set_module(b, a, dark)

    return grid, reserved


def _draw_format_bits(grid, ecl: str, mask: int, reserved=None):
    size = len(grid)
    data = ECC_LEVELS[ecl] << 3 | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    bits = (data << 10 | rem) ^ 0x5412

    def put(x, y, i):
        grid[y][x] = (bits >> i) & 1 == 1
        if reserved is not None:
            reserved[y][x] = True

    for i in range(6):
        put(8, i, i)
    put(8, 7, 6)
    put(8, 8, 7)
    put(7, 8, 8)
    for i in range(9, 15):
        put(14 - i, 8, i)

    for i in range(8):
        put(size - 1 - i, 8, i)
    for i in range(8, 15):
        put(8, size - 15 + i, i)


def _draw_codewords(grid, reserved, codewords):
    size = len(grid)
    bit = 0
    total_bits = len(codewords) * 8
    for right in range(size - 1, 0, -2):
        if right <= 6:                                # step over the vertical timing pattern
            right -= 1
        for vertical in range(size):
            for j in range(2):
                x = right - j
                upward = ((right + 1) & 2) == 0
                y = (size - 1 - vertical) if upward else vertical
                if not reserved[y][x] and bit < total_bits:
                    grid[y][x] = (codewords[bit >> 3] >> (7 - (bit & 7))) & 1 == 1
                    bit += 1
                # Any remaining modules stay light, which the spec permits.


def _apply_mask(grid, reserved, mask: int):
    size = len(grid)
    rule = (
        lambda x, y: (x + y) % 2 == 0,
        lambda x, y: y % 2 == 0,
        lambda x, y: x % 3 == 0,
        lambda x, y: (x + y) % 3 == 0,
        lambda x, y: (y // 2 + x // 3) % 2 == 0,
        lambda x, y: x * y % 2 + x * y % 3 == 0,
        lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
        lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
    )[mask]
    for y in range(size):
        for x in range(size):
            if not reserved[y][x] and rule(x, y):
                grid[y][x] = not grid[y][x]


_PENALTY_N1, _PENALTY_N2, _PENALTY_N3, _PENALTY_N4 = 3, 3, 40, 10
_FINDER_RATIO = [True, False, True, True, True, False, True]     # the 1:1:3:1:1 proportion


def _penalty(grid) -> int:
    """ISO/IEC 18004 table 11 — the mask with the lowest score is the one used."""
    size = len(grid)
    score = 0

    for line in list(grid) + [list(col) for col in zip(*grid)]:
        run = 1
        for i in range(1, size):
            if line[i] == line[i - 1]:
                run += 1
            else:
                if run >= 5:
                    score += _PENALTY_N1 + run - 5
                run = 1
        if run >= 5:
            score += _PENALTY_N1 + run - 5
        score += _finder_like(line, size) * _PENALTY_N3

    for y in range(size - 1):
        for x in range(size - 1):
            block = (grid[y][x], grid[y][x + 1], grid[y + 1][x], grid[y + 1][x + 1])
            if all(block) or not any(block):
                score += _PENALTY_N2

    dark = sum(row.count(True) for row in grid)
    total = size * size
    # |percent dark - 50|, in whole 5% steps.
    return score + _PENALTY_N4 * (abs(dark * 20 - total * 10) // total)


def _finder_like(line, size: int) -> int:
    """Runs that look like a finder pattern to a scanner: the 1:1:3:1:1 proportion with
    four light modules on one side of it. The quiet zone counts as that light area."""
    count = 0
    i = 0
    while i <= size - 7:
        if line[i:i + 7] != _FINDER_RATIO:
            i += 1
            continue
        after = i + 7
        if (i in (0, size - 7)
                or not any(line[max(i - 4, 0):i])
                or not any(line[after:after + 4])):
            count += 1
            i = after
        else:
            i += 4                                   # can't overlap sooner than this
    return count
