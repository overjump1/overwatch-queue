"""Reads the player's own Battle.net rich presence ("Competitive: In Queue") out of
Battle.net's process memory. Read-only: nothing is injected and Overwatch is never touched."""
from __future__ import annotations

import ctypes
import os
import re
import sqlite3
import subprocess
import time
from collections import Counter

QUEUEING = "queueing"
IN_GAME = "inGame"
GAME_ENDING = "gameEnding"
MENUS = "menus"
PLAYING_OTHER = "playingOther"
ELSEWHERE = "elsewhere"
UNKNOWN = "unknown"

ACTIVITY_SUFFIXES = (("In Queue", QUEUEING), ("Game Ending", GAME_ENDING), ("In Game", IN_GAME))
STANDALONE = {"In Menus": MENUS, "Practice Range": PLAYING_OTHER, "Tutorial": PLAYING_OTHER}
MODE_WORDS = {
    "Quick Play": "quickPlay",
    "Competitive": "competitive",
    "Custom Game": "custom",
    "Arcade": "arcade",
    "Stadium": "stadium",
    "Mystery Heroes": "mysteryHeroes",
}
OVERWATCH_PROGRAM = "Pro"
BATTLENET_STATUS = "In App"

AVAILABLE = os.name == "nt"


def parse(text):
    """`"Competitive: In Queue"` -> `(QUEUEING, "competitive")`. Unknown text is `(UNKNOWN, None)`."""
    text = (text or "").strip()
    if not text:
        return UNKNOWN, None
    if text in STANDALONE:
        return STANDALONE[text], None
    for suffix, state in ACTIVITY_SUFFIXES:
        if text.endswith(suffix):
            prefix = text[:-len(suffix)].rstrip().rstrip(":").rstrip()
            return state, MODE_WORDS.get(prefix)
    return UNKNOWN, None


def classify(program, status):
    if program and program != OVERWATCH_PROGRAM:
        return ELSEWHERE, None
    return parse(status)


class Identity:
    def __init__(self, battletag, account_id):
        self.battletag = battletag
        self.account_id = int(account_id)


def find_identity():
    """The logged-in account from Battle.net's own `CachedData.db`, or None."""
    path = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Battle.net", "CachedData.db")
    if not os.path.exists(path):
        return None
    try:
        connection = sqlite3.connect("file:%s?mode=ro&immutable=1" % path, uri=True, timeout=1.0)
        try:
            row = connection.execute("SELECT battle_tag, account_id_lo FROM login_cache LIMIT 1").fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    if not row or row[1] is None:
        return None
    return Identity(row[0], row[1])


PEAK_SHRINK_POLLS = 5


class Tracker:
    """Picks the current program/status by what newly arrived in memory.

    Old copies are never erased, so a value whose copy count grew past the most copies of it
    ever seen is the current one. That high-water mark, rather than the previous read, is what
    a scan is measured against: a scan that misses part of Battle.net's memory can then only
    fail to reach a peak, never fake one. Before the first change, a value is only trusted if
    every copy in memory reads as the same state."""

    KINDS = ("program", "status")

    def __init__(self, log=None):
        self.log = log or (lambda message: None)
        self._peak = None
        self._low = 0
        self._primed = False
        self._seen = set()
        self._values = {}
        self._order = {}
        self._clock = 0

    def rebaseline(self):
        """Forgets the copy counts but keeps the reading. For when the set of Battle.net
        processes changes, which makes the old counts meaningless without making the reading
        wrong."""
        self._peak, self._low, self._primed = None, 0, False

    def observe(self, observed, complete=True):
        """One scan of Battle.net's memory. `complete` says every process was read; a partial
        scan is still safe to feed, it just can't lower the peaks. An empty scan is refused
        outright, leaving the last reading standing -- bounded by the detector's presence-lost
        grace, which is what eventually calls the queue off."""
        if not observed:
            return UNKNOWN, None
        if self._peak is None:
            return self._prime(observed, complete)
        if not self._primed and complete:
            # The baseline came off a scan that missed processes, so it reads low. Take this
            # one instead, rather than mistaking the difference for a wave of arrivals.
            return self._prime(observed, complete)
        for kind in self.KINDS:
            arrivals = {key: count - self._peak.get(key, 0) for key, count in observed.items()
                        if key[0] == kind and count > self._peak.get(key, 0)}
            if arrivals:
                current = self._values.get(kind)
                best = max(arrivals, key=lambda key: (bool(key[1]), key not in self._seen,
                                                      arrivals[key], key[1] != current))
                self._clock += 1
                self._values[kind], self._order[kind] = best[1], self._clock
        self._update_peaks(observed, complete)
        self._seen.update(observed)
        if not self._values:
            self._adopt(self._unambiguous(observed))
        return self._current()

    def _prime(self, observed, complete):
        """Takes a scan as the baseline, learning nothing from it: with nothing to compare
        against, a count is just a count. A scan that missed processes can serve until a whole
        one comes along, so a Battle.net that's never fully readable still tracks."""
        self._peak, self._low, self._primed = dict(observed), 0, complete
        self._seen.update(observed)
        self._adopt(self._unambiguous(observed))
        return self._current()

    def _update_peaks(self, observed, complete):
        """Peaks only rise, so an unreadable process can never fake an arrival. They come back
        down only once a run of complete scans agrees Battle.net really did free the copies."""
        shrunk = any(observed.get(key, 0) < count for key, count in self._peak.items())
        self._low = self._low + 1 if complete and shrunk else 0
        if self._low >= PEAK_SHRINK_POLLS:
            self.log("Battle.net freed presence copies, re-baselining the counts")
            self._peak, self._low = dict(observed), 0
            return
        for key, count in observed.items():
            if count > self._peak.get(key, 0):
                self._peak[key] = count

    def _adopt(self, agreed):
        if agreed is not None:
            self._values, self._order = agreed, {kind: 0 for kind in agreed}

    def _current(self):
        return self._reading(self._values.get("program", ""), self._values.get("status", ""),
                             self._order.get("status", 0) >= self._order.get("program", 0))

    def _unambiguous(self, observed):
        values = {kind: [key[1] for key, _ in observed.most_common() if key[0] == kind] or [""]
                  for kind in self.KINDS}
        readings = {self._reading(program, status, True)
                    for program in values["program"] for status in values["status"]}
        if len(readings) != 1:
            return None
        return {kind: values[kind][0] for kind in self.KINDS}

    @staticmethod
    def _reading(program, status, status_newer):
        if parse(status)[0] != UNKNOWN:
            program = OVERWATCH_PROGRAM
        elif status == BATTLENET_STATUS and status_newer:
            program = "App"
        return classify(program, status)


# --- decoding presence out of raw memory -----------------------------------------------

_PRESENCE_PROGRAM = 0x424E            # "BN"
_PROGRAM_FIELDS = {(2, 21)}
_STATUS_FIELDS = {(2, 22), (1, 25)}
_RECORD_MAX_BYTES = 512


def collect(chunks, identity):
    """Counts every `("program", text)` / `("status", text)` copy for `identity` in `chunks`."""
    observed = Counter()
    account = identity.account_id
    operation_start = re.compile(re.escape(b"\x08" + _varint_bytes(account) + b"\x12"))
    record_start = None
    if identity.battletag:
        account_text = str(account).encode("ascii")
        tag = identity.battletag.encode("utf-8", "ignore")
        record_start = re.compile(re.escape(
            b"\x0a" + _varint_bytes(len(account_text)) + account_text +
            b"\x12" + _varint_bytes(len(tag)) + tag))
    for chunk in chunks:
        for match in operation_start.finditer(chunk):
            for block in _account_blocks(chunk, match.end() - 1, account):
                _count_operations(block, observed)
        if record_start is not None:
            for match in record_start.finditer(chunk):
                _count_record(chunk, match.start(), observed)
    return observed


def _count_record(chunk, start, observed):
    fields, last = {}, 0
    for number, value in _fields(chunk[start:start + _RECORD_MAX_BYTES]):
        if number <= last:
            break
        fields[number], last = value, number
    program, status = fields.get(6), fields.get(13)
    if isinstance(program, bytes) and program:
        observed[("program", program.decode("utf-8", "replace"))] += 1
    if isinstance(status, bytes):
        observed[("status", status.decode("utf-8", "replace"))] += 1


def _account_blocks(chunk, pos, account):
    end = len(chunk)
    while pos < end and chunk[pos] == 0x12:
        length, body_at = _read_varint(chunk, pos + 1, end)
        if length is None or length > 4096 or body_at + length > end:
            return
        block = _fields(chunk[body_at:body_at + length])
        if not block or block[0] != (1, account):
            return
        yield block
        pos = body_at + length


def _count_operations(block, observed):
    for number, operation in block:
        if number != 3 or not isinstance(operation, bytes):
            continue
        parts = dict(_fields(operation))
        if not isinstance(parts.get(1), bytes):
            continue
        key = dict(_fields(parts[1]))
        if key.get(1) != _PRESENCE_PROGRAM:
            continue
        where = (key.get(2), key.get(3))
        if where not in _PROGRAM_FIELDS and where not in _STATUS_FIELDS:
            continue
        value = dict(_fields(parts[2])) if isinstance(parts.get(2), bytes) else {}
        text = value.get(4, b"")
        if isinstance(text, bytes):
            kind = "program" if where in _PROGRAM_FIELDS else "status"
            observed[(kind, text.decode("utf-8", "replace"))] += 1


def _fields(buf):
    out, pos, end = [], 0, len(buf)
    while pos < end:
        key, pos = _read_varint(buf, pos, end)
        if key is None or key >> 3 == 0:
            break
        wire = key & 7
        if wire == 0:
            value, pos = _read_varint(buf, pos, end)
            if value is None:
                break
        elif wire == 2:
            length, pos = _read_varint(buf, pos, end)
            if length is None or pos + length > end:
                break
            value, pos = bytes(buf[pos:pos + length]), pos + length
        else:
            break
        out.append((key >> 3, value))
    return out


def _varint_bytes(value):
    out = bytearray()
    while True:
        byte, value = value & 0x7F, value >> 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _read_varint(buf, pos, end):
    value = shift = 0
    while pos < end and shift < 64:
        byte = buf[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7
    return None, pos


# --- process access (Windows) -----------------------------------------------------------

_PROCESS_QUERY_INFORMATION = 0x0400
_PROCESS_VM_READ = 0x0010
_MEM_COMMIT = 0x1000
_MEM_PRIVATE = 0x20000
_READABLE_PROTECT = frozenset({0x02, 0x04, 0x08, 0x20, 0x40, 0x80})
_MAX_REGION_BYTES = 200 * 1024 * 1024


class _MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", ctypes.c_ulong), ("RegionSize", ctypes.c_size_t),
        ("State", ctypes.c_ulong), ("Protect", ctypes.c_ulong), ("Type", ctypes.c_ulong),
    ]


def _kernel32():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    k32.VirtualQueryEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_MBI), ctypes.c_size_t]
    k32.VirtualQueryEx.restype = ctypes.c_size_t
    k32.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    return k32


def _regions(k32, handle):
    mbi = _MBI()
    address = 0
    while address < 0x7FFF0000:
        if k32.VirtualQueryEx(handle, ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
            break
        size = mbi.RegionSize or 0x1000
        if (mbi.State == _MEM_COMMIT and mbi.Type == _MEM_PRIVATE and
                (mbi.Protect & 0xFF) in _READABLE_PROTECT and size < _MAX_REGION_BYTES):
            buffer = ctypes.create_string_buffer(size)
            read = ctypes.c_size_t(0)
            if k32.ReadProcessMemory(handle, ctypes.c_void_p(address), buffer, size, ctypes.byref(read)) and read.value:
                yield buffer.raw[:read.value]
        address += size


class _PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_ulong), ("cntUsage", ctypes.c_ulong), ("th32ProcessID", ctypes.c_ulong),
        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", ctypes.c_ulong),
        ("cntThreads", ctypes.c_ulong), ("th32ParentProcessID", ctypes.c_ulong),
        ("pcPriClassBase", ctypes.c_long), ("dwFlags", ctypes.c_ulong), ("szExeFile", ctypes.c_wchar * 260),
    ]


def pids_named(name):
    """Every process id running `name` (lower-case). Cheap enough to run every read."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PROCESSENTRY32)]
    k32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PROCESSENTRY32)]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    snapshot = k32.CreateToolhelp32Snapshot(0x2, 0)
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        return frozenset()
    pids = set()
    try:
        entry = _PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(entry)
        more = k32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            if entry.szExeFile.lower() == name:
                pids.add(entry.th32ProcessID)
            more = k32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snapshot)
    return frozenset(pids)


def _battlenet_roles():
    """`(main, renderers)` process ids of Battle.net, from their command lines. GPU and
    utility helpers never hold presence and are skipped."""
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='Battle.net.exe'\" | "
         "ForEach-Object { '{0} {1}' -f $_.ProcessId, $(if ($_.CommandLine -match "
         "'--type=renderer') { 'renderer' } elseif ($_.CommandLine -match '--type=') "
         "{ 'helper' } else { 'main' }) }"],
        capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    main, renderers = [], []
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit():
            if parts[1] == "renderer":
                renderers.append(int(parts[0]))
            elif parts[1] == "main":
                main.append(int(parts[0]))
    return main, renderers


ROLE_RETRY_SECONDS = 5.0
CONNECTED_GRACE_SECONDS = 3.0


class Presence:
    """`read()` returns `(state, mode)` and never raises. `connected` says whether every
    Battle.net process was readable on a recent read."""

    def __init__(self, log=print):
        self.log = log
        self.connected = False
        self._reached = False
        self._reached_at = 0.0
        self._opened = 0
        self._blind = False
        self._identity = None
        self._known_pids = frozenset()
        self._main = None
        self._pids = []
        self._roles_at = 0.0
        self._roles_failed = False
        self._tracker = Tracker(log)

    def rebaseline(self):
        """Forgets the copy counts, keeping the reading. For a gap in the reads -- the app
        was reading over Battle.net's debug port instead -- where the counts on the far side
        of it say nothing about what arrived during it."""
        self._tracker.rebaseline()

    def read(self):
        self._reached = False
        try:
            return self._read()
        finally:
            now = time.monotonic()
            if self._reached:
                self._reached_at = now
            # A single missed process shouldn't flash "Battle.net not found" at the player.
            self.connected = now - self._reached_at <= CONNECTED_GRACE_SECONDS

    def _read(self):
        if not AVAILABLE:
            return UNKNOWN, None
        try:
            if self._identity is None:
                self._identity = find_identity()
                if self._identity is None:
                    return UNKNOWN, None
            pid_set = pids_named("battle.net.exe")
            if pid_set != self._known_pids or (pid_set and not self._pids):
                self._resolve_pids(pid_set)
            if not self._pids:
                return UNKNOWN, None
            self._opened = 0
            observed = collect(self._chunks(), self._identity)
            self._reached = self._opened == len(self._pids)
            self._note_scan()
            if not self._opened:
                return UNKNOWN, None
            return self._tracker.observe(observed, self._reached)
        except Exception as problem:  # noqa: BLE001
            self.log("Presence read failed: %s" % problem)
            self._known_pids = frozenset()
            self._roles_at = 0.0
            return UNKNOWN, None

    def _resolve_pids(self, pid_set):
        """Works out which Battle.net processes to scan. A role lookup that fails or comes back
        empty keeps the previous list and retries, rather than leaving the app with nothing to
        read until Battle.net's processes happen to change again."""
        if not pid_set:
            self._known_pids, self._pids, self._main = pid_set, [], None
            self._tracker.rebaseline()
            return
        # Only a lookup that came back empty is worth holding off on; a real change to
        # Battle.net's processes has to be picked up at once.
        if self._pids and self._roles_failed and time.monotonic() - self._roles_at < ROLE_RETRY_SECONDS:
            return
        try:
            main, renderers = _battlenet_roles()
        except Exception as problem:  # noqa: BLE001
            self.log("Battle.net role lookup failed: %s" % problem)
            main, renderers = [], []
        finally:
            # Timed from the end, so a lookup that sat on its 15s timeout doesn't retry at once.
            self._roles_at = time.monotonic()
        pids = main + renderers
        self._roles_failed = not pids
        if pids:
            self._known_pids = pid_set
        else:
            # Helpers hold no presence, so scanning them only costs time -- which beats
            # reading nothing at all. `_known_pids` stays put so the next read tries again.
            self.log("Battle.net role lookup found nothing, scanning all %d processes" % len(pid_set))
            pids = sorted(pid_set)
        if set(pids) != set(self._pids):
            self.log("Battle.net processes changed to %s" % ", ".join(str(pid) for pid in pids))
            self._pids = pids
            self._tracker.rebaseline()
        if main and main[0] != self._main:
            self._main = main[0]
            identity = find_identity()
            if identity is not None:
                if self._identity is None or identity.account_id != self._identity.account_id:
                    self.log("Battle.net account changed, starting over")
                    self._tracker = Tracker(self.log)
                self._identity = identity

    def _note_scan(self):
        """One line when the scan starts missing processes and one when it recovers, so a long
        outage leaves a pair of lines in the log rather than one per second."""
        if self._reached == (not self._blind):
            return
        self._blind = not self._reached
        if self._blind:
            self.log("Couldn't read all of Battle.net: %d of %d processes"
                     % (self._opened, len(self._pids)))
        else:
            self.log("Battle.net readable again: %d processes" % len(self._pids))

    def _chunks(self):
        k32 = _kernel32()
        for pid in self._pids:
            handle = k32.OpenProcess(_PROCESS_QUERY_INFORMATION | _PROCESS_VM_READ, False, pid)
            if not handle:
                continue
            try:
                # A handle that opens but yields nothing readable isn't a process we reached.
                read = False
                for chunk in _regions(k32, handle):
                    read = True
                    yield chunk
                if read:
                    self._opened += 1
            finally:
                k32.CloseHandle(handle)
