"""Asking Battle.net itself what the player is doing, instead of the screen.

Two ways in:

`MemorySource`, the default, reads the presence record straight out of Battle.net's own
process memory with a plain `ReadProcessMemory`, the way `queuedigits` reads pixels
rather than calling an OCR engine - no injection, and Overwatch itself is never touched.
It needs nothing from Battle.net at all, however Battle.net was started, which is why it
is the default: the debug port below only exists if Battle.net was launched with a flag,
and Battle.net rewrites its own auto-start entry and shortcut, so there is no dependable
way to make sure it was. It reads the same presence object the Battle.net window is built
from, checked against update messages for which program is current, and hands it to the
same `queuepresence.parse_record` as `CDPSource` - confirmed live to read identically,
through Battle.net's own "In App" and Overwatch's "In Menus", and to know the status
~17 seconds after a fresh Battle.net start.

`CDPSource` talks to Battle.net's own embedded Chromium (CEF) over the Chrome DevTools
Protocol, if Battle.net was started with `--remote-debugging-port`. Once connected it
calls the exact same function Battle.net's own UI calls to show your presence to a
friend - `phoenix.socialService.subscribeSelfPresence` - which hands back a clean,
structured record (battletag, program, the rich-presence string) with nothing to scrape
and nothing that a re-skin of the app could quietly break. Confirmed live against a real
account: a queue starting and ending was seen through this path in real time.

Every source's `read` **never raises**. A source that cannot see is, to everything
downstream, indistinguishable from a source with no opinion - `queuepresence.UNKNOWN`,
never a guess. The one thing no source may ever do is manufacture a state.

Nothing here ever writes anything. Confirmed live against a real account, for the
record: setting the account's availability to "Appear Offline" does *not* blank
`rich_presence` in the very same account's own `subscribeSelfPresence` read - that
setting changes what a friend sees, not what this project reads about its own
account - so there is nothing for a player to need to change here even in principle.
"""
from __future__ import annotations

import ctypes
import json
import os
import sqlite3
import subprocess
import time

from . import queuepresence

try:                                    # pragma: no cover - the Mac/Linux dev path
    import websocket as _ws
    _CDP_AVAILABLE = True
except ImportError:                     # pragma: no cover - see requirements.txt
    _ws = None
    _CDP_AVAILABLE = False

# Reading a Windows process's own memory, or a Battle.net-specific config path, makes no
# sense anywhere else - this whole module is a Windows-only extra, the same shape as
# `queuevision.QUEUE_VISION_AVAILABLE`'s import guard.
PRESENCE_AVAILABLE = os.name == "nt"
CDP_AVAILABLE = PRESENCE_AVAILABLE and _CDP_AVAILABLE

DEFAULT_CDP_PORT = 9222
CDP_TIMEOUT_SECONDS = 2.0
CDP_POLL_SECONDS = 1.0
# A read is a tenth of a second (heap only, one process), so polling this often is cheap
# — and the faster it polls, the less often two status changes land in the same read.
MEMORY_POLL_SECONDS = 1.0

# The one call this project needs from Battle.net's own frontend to read. Wrapped in a
# Promise so a single `Runtime.evaluate` with `awaitPromise` gets exactly one settled
# answer back, and `JSON.stringify`d so CDP hands back a plain string rather than a
# remote object reference that would need a second round trip to read.
_SUBSCRIBE_SELF_PRESENCE_JS = """
new Promise((resolve) => {
  try {
    window.phoenix.socialService.subscribeSelfPresence({}, (err, data) => {
      resolve(err ? 'null' : JSON.stringify((data && data.self) || null));
    });
  } catch (e) {
    resolve('null');
  }
})
"""


class NullSource:
    """No opinion, ever. What `open_source` hands back when nothing else could start."""

    poll_seconds = 5.0

    def read(self, when):
        return queuepresence.PresenceReading(queuepresence.UNKNOWN, None, "", None, when)

    def close(self):
        pass


class CDPSource:
    """Reads presence over Battle.net's own CEF debug port.

    One target is picked once (the app shell page, `resources://home/` in every build
    seen so far - never the `webview` targets, which are just embedded content like the
    news carousel) and its WebSocket is kept open across polls; a failed read closes it
    so the next poll reconnects and re-picks a target rather than trusting a stale one.
    """

    poll_seconds = CDP_POLL_SECONDS

    def __init__(self, port=DEFAULT_CDP_PORT, identity=None, log=None):
        self.port = port
        self.identity = identity
        self.log = log or (lambda message: None)
        self._socket = None
        self._req_id = 0

    def read(self, when):
        try:
            if self._socket is None:
                self._connect()
            record = self._eval(_SUBSCRIBE_SELF_PRESENCE_JS)
        except Exception as problem:                # noqa: BLE001 - never raise from here
            self.log("Battle.net's debug port didn't answer: %s" % problem)
            self.close()
            return queuepresence.PresenceReading(queuepresence.UNKNOWN, None, "", None, when)

        reading = queuepresence.parse_record(record, when, identity=self.identity)
        if reading is None:
            return queuepresence.PresenceReading(queuepresence.UNKNOWN, None, "", None, when)
        return reading

    def close(self):
        if self._socket is not None:
            try:
                self._socket.close()
            except Exception:                        # noqa: BLE001 - already going away
                pass
            self._socket = None

    # ------------------------------------------------------------ the CDP plumbing

    def _connect(self):
        targets = _list_targets(self.port)
        target = _pick_app_shell(targets)
        if target is None:
            raise RuntimeError("no CEF target answered on port %d" % self.port)
        self._socket = _ws.create_connection(target["webSocketDebuggerUrl"],
                                             timeout=CDP_TIMEOUT_SECONDS)

    def _eval(self, expression):
        """Runs one JS expression — a `Promise` resolving to a JSON string, by
        convention every expression this module sends does — and returns the decoded
        value. Shared by every read and write this source makes."""
        self._req_id += 1
        request_id = self._req_id
        self._socket.send(json.dumps({
            "id": request_id,
            "method": "Runtime.evaluate",
            "params": {"expression": expression,
                      "returnByValue": True, "awaitPromise": True},
        }))
        deadline = time.monotonic() + CDP_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            raw = self._socket.recv()
            message = json.loads(raw)
            if message.get("id") != request_id:
                continue                              # an unrelated event; keep waiting
            result = message.get("result", {}).get("result", {})
            if result.get("subtype") == "error":
                raise RuntimeError(result.get("description", "presence eval error"))
            value = result.get("value")
            if not value or value == "null":
                return None
            return json.loads(value)
        raise TimeoutError("Battle.net's debug port didn't answer in time")


def _list_targets(port):
    import urllib.request
    with urllib.request.urlopen(
            "http://127.0.0.1:%d/json/list" % port, timeout=CDP_TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _pick_app_shell(targets):
    """The app shell over a content webview - see the class docstring for why."""
    for target in targets:
        if target.get("type") == "page" and "webSocketDebuggerUrl" in target:
            return target
    for target in targets:
        if "webSocketDebuggerUrl" in target:
            return target
    return None


class MemorySource:
    """Reads your own presence out of Battle.net's process memory.

    Where it lands depends on whether the Battle.net window is open, measured both ways:
    closed to the tray, each change arrives in the main process as protocol messages —
    field operations keyed by your account id; open, it arrives in a renderer instead, as
    records naming your account id and battletag, with nothing in the main process at all.
    `collect_presence` counts both, so which one is in play doesn't matter.

    Nothing inside either says which copy is current. Freed copies aren't erased, so
    leftovers of earlier values sit alongside the live one, and the one timestamp-shaped
    field was measured to be shared across different values. What does say so is arrival:
    each change shows up as new copies within a poll — and they're recycled fast, every
    copy gone within ~15 seconds of quitting Overwatch — so `PresenceTracker` takes whatever
    just arrived as current, polling once a second. Measured against a real sequence — leave
    queue, Practice Range, leave it, queue, quit Overwatch — every step arrived in order.

    Only private (heap) memory is read: every copy was found there, and it's a fraction of
    each process — DLL images and mapped files make up the rest and held none. Battle.net's
    GPU and utility processes are skipped, and the process list is re-checked every
    `_RESOLVE_EVERY_READS` since renderers come and go with the window.
    """

    poll_seconds = MEMORY_POLL_SECONDS

    _PROCESS_QUERY_INFORMATION = 0x0400
    _PROCESS_VM_READ = 0x0010
    _MEM_COMMIT = 0x1000
    _MEM_PRIVATE = 0x20000
    _READABLE_PROTECT = frozenset({0x02, 0x04, 0x08, 0x20, 0x40, 0x80})
    _MAX_REGION_BYTES = 200 * 1024 * 1024
    _RESCAN_AFTER_FAILURES = 3
    # The process list is a PowerShell call (~0.5s), so it isn't looked up every read —
    # just often enough to catch renderers appearing when the window opens.
    _RESOLVE_EVERY_READS = 10

    def __init__(self, identity, log=None):
        self.identity = identity
        self.log = log or (lambda message: None)
        self._main = None
        self._pids = []
        self._failures = 0
        self._reads = 0
        self._tracker = PresenceTracker(identity)
        # Whether the last read reached Battle.net at all, known status or not — see
        # `queuewatch.PresenceWatcher.poll` for why that's asked.
        self.alive = False

    def read(self, when):
        unknown = queuepresence.PresenceReading(queuepresence.UNKNOWN, None, "", None, when)
        self.alive = False
        if self.identity is None or not self.identity.account_id:
            return unknown
        try:
            self._reads += 1
            if (not self._pids or self._failures >= self._RESCAN_AFTER_FAILURES
                    or self._reads % self._RESOLVE_EVERY_READS == 0):
                self._failures = 0
                renderers, main = _battlenet_pids()
                if (main[0] if main else None) != self._main:
                    # A new Battle.net process is fresh memory: nothing carries over.
                    self._main = main[0] if main else None
                    self._tracker = PresenceTracker(self.identity)
                self._pids = main + renderers
            record = self._tracker.observe(self._observe(self._pids))
        except Exception as problem:                  # noqa: BLE001 - never raise
            self.log("Reading Battle.net's memory failed: %s" % problem)
            self._failures += 1
            return unknown

        if record is None:
            self._failures += 1
            return unknown
        self._failures = 0
        return queuepresence.parse_record(record, when, identity=self.identity) or unknown

    def close(self):
        self._pids = []

    def _observe(self, pids):
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p

        def chunks():
            for pid in pids:
                handle = k32.OpenProcess(
                    self._PROCESS_QUERY_INFORMATION | self._PROCESS_VM_READ, False, pid)
                if not handle:
                    continue
                # Reachable is what alive means, not "found copies": with nothing
                # changing, every copy was measured to be recycled within seconds while
                # Battle.net sat there fine — and the status last arrived still stands.
                self.alive = True
                try:
                    yield from _readable_regions(k32, handle, self._MAX_REGION_BYTES,
                                                 private_only=True)
                finally:
                    k32.CloseHandle(handle)

        return collect_presence(chunks(), self.identity)


# The program Battle.net files its own presence fields under — the fourcc "BN".
_PRESENCE_PROGRAM = 0x424E
# Which (group, field) carries what, measured live against a known sequence of changes.
_PROGRAM_FIELDS = {(2, 21)}               # "Pro" for Overwatch, "App"/"BSAp" for Battle.net
_STATUS_FIELDS = {(2, 22), (1, 25)}       # the rich-presence text; (1, 25) alone carried
#                                           "In App" when Overwatch was quit
_BATTLENET_STATUS = "In App"              # Battle.net's own, not a game's


def collect_presence(chunks, identity):
    """A `Counter` of `("program", text)` and `("status", text)` — every copy found for
    `identity` across `chunks` of raw memory, in either form it arrives in. Pure, no process
    access, so the decoding is testable against plain bytes.

    Field operations (window closed): the account id (field 1), then blocks (field 2) that
    each start with the account id again and carry one operation (field 3) — a key (program,
    group, field, index) and a value whose field 4 is the text.

    Records (window open): field 1 the account id and field 2 the battletag, both as text,
    then field 6 the program and field 13 the status — mapped against the debug port's own
    record for the same account. A record without field 13 carried only a program change and
    says nothing about the status.

    Anything keyed to another account — a friend, whose presence sits in the same memory in
    the same shapes — is skipped.
    """
    import re
    from collections import Counter

    observed = Counter()
    if identity is None or not identity.account_id:
        return observed
    account = int(identity.account_id)
    operation_start = re.compile(re.escape(b"\x08" + _varint_bytes(account) + b"\x12"))
    record_start = None
    if identity.battletag:
        account_text = str(identity.account_id).encode("ascii")
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


_RECORD_MAX_BYTES = 512


def _count_record(chunk, start, observed):
    fields, last = {}, 0
    for number, value in _repeated_fields(chunk[start:start + _RECORD_MAX_BYTES]):
        if number <= last:
            break                                  # where the next record begins
        fields[number], last = value, number
    program, status = fields.get(6), fields.get(13)
    if isinstance(program, bytes) and program:
        observed[("program", program.decode("utf-8", "replace"))] += 1
    if isinstance(status, bytes):
        observed[("status", status.decode("utf-8", "replace"))] += 1


def _account_blocks(chunk, pos, account):
    """Each block (field 2) of the message whose first block starts at `pos`, for as long
    as they keep starting with `account`'s id."""
    end = len(chunk)
    while pos < end and chunk[pos] == 0x12:
        length, body_at = _read_varint(chunk, pos + 1, end)
        if length is None or length > 4096 or body_at + length > end:
            return
        block = _repeated_fields(chunk[body_at:body_at + length])
        if not block or block[0] != (1, account):
            return
        yield block
        pos = body_at + length


def _count_operations(block, observed):
    for number, operation in block:
        if number != 3 or not isinstance(operation, bytes):
            continue
        parts = dict(_repeated_fields(operation))
        if not isinstance(parts.get(1), bytes):
            continue
        key = dict(_repeated_fields(parts[1]))
        if key.get(1) != _PRESENCE_PROGRAM:
            continue
        where = (key.get(2), key.get(3))
        if where not in _PROGRAM_FIELDS and where not in _STATUS_FIELDS:
            continue
        value = dict(_repeated_fields(parts[2])) if isinstance(parts.get(2), bytes) else {}
        text = value.get(4, b"")
        if not isinstance(text, bytes):
            continue
        kind = "program" if where in _PROGRAM_FIELDS else "status"
        observed[(kind, text.decode("utf-8", "replace"))] += 1


def _repeated_fields(buf):
    """Every top-level protobuf field in `buf`, in order, repeats kept — `(number, value)`
    pairs, stopping at the first thing that can't be a field."""
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


class PresenceTracker:
    """Which program and status are current, judged by what newly arrived.

    Fed each read's copy counts. A value whose copies grew since the last read just arrived,
    and becomes current; leftovers of earlier values don't grow, so they never win again on
    their own. When two values of the same kind grow in one read — leaving a queue was
    measured to bring the old status along with the new, 2 copies to 6 — a value never seen
    before beats one that merely grew, then the bigger arrival wins, then a change beats the
    value already current.

    Before anything has arrived there's nothing to compare against, and taking the most
    common value was measured to be wrong — "Practice Range" outnumbered the "In App" left
    after quitting Overwatch. So a value is only taken then if every program and status in
    memory would read as the same state; otherwise the reading is `UNKNOWN` until the next
    change arrives. Never a guess.
    """

    KINDS = ("program", "status")

    def __init__(self, identity):
        self.identity = identity
        self._previous = None
        self._seen = set()
        self._values = {}
        self._order = {}
        self._clock = 0

    def observe(self, observed):
        if self._previous is not None:
            grown = {key: count - self._previous.get(key, 0)
                     for key, count in observed.items()
                     if count > self._previous.get(key, 0)}
            for kind in self.KINDS:
                arrivals = {key: size for key, size in grown.items() if key[0] == kind}
                if arrivals:
                    current = self._values.get(kind)
                    # A real value beats an empty one arriving alongside it: Overwatch
                    # clears its status while loading and sets one moments later, close
                    # enough for both to land in one poll.
                    best = max(arrivals, key=lambda key: (bool(key[1]), key not in self._seen,
                                                          arrivals[key], key[1] != current))
                    self._clock += 1
                    self._values[kind], self._order[kind] = best[1], self._clock
        # Kept even when empty: every copy having been recycled is a baseline of zero, so the
        # next change counts as arriving rather than being taken for a first look.
        self._previous = observed
        self._seen.update(observed)
        if not self._values:
            if not observed:
                return None
            agreed = self._unambiguous(observed)
            if agreed is None:
                return self._as_record("", "", status_newer=True)     # reads as UNKNOWN
            self._values = agreed
            self._order = {kind: 0 for kind in agreed}
        return self._record()

    def _unambiguous(self, observed):
        """The most common program and status, but only if every combination of the
        programs and statuses in memory reads as the same state."""
        values = {kind: [key[1] for key, _ in observed.most_common() if key[0] == kind] or [""]
                  for kind in self.KINDS}
        states = set()
        for program in values["program"]:
            for status in values["status"]:
                reading = queuepresence.parse_record(
                    self._as_record(program, status, status_newer=True), 0.0,
                    identity=self.identity)
                states.add((reading.state, reading.mode) if reading else None)
        if len(states) != 1:
            return None
        return {kind: values[kind][0] for kind in self.KINDS}

    def _record(self):
        if not self._values:
            return None
        return self._as_record(self._values.get("program", ""), self._values.get("status", ""),
                               self._order.get("status", 0) >= self._order.get("program", 0))

    def _as_record(self, program, status, status_newer):
        if queuepresence.parse(status)[0] != queuepresence.UNKNOWN:
            program = queuepresence.OVERWATCH_PROGRAM_ID
        elif status == _BATTLENET_STATUS and status_newer:
            # Quitting Overwatch was measured to send only this — the program field kept
            # saying "Pro" — so it has to be what says Overwatch has gone.
            program = "App"
        return {"id": str(self.identity.account_id) if self.identity.account_id else None,
                "battle_tag": self.identity.battletag, "program_id": program,
                "program_name": "", "rich_presence": status}


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


class _MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", ctypes.c_ulong), ("RegionSize", ctypes.c_size_t),
        ("State", ctypes.c_ulong), ("Protect", ctypes.c_ulong), ("Type", ctypes.c_ulong),
    ]


def _readable_regions(k32, handle, max_region_bytes, private_only=False):
    mbi = _MEMORY_BASIC_INFORMATION()
    k32.VirtualQueryEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.POINTER(_MEMORY_BASIC_INFORMATION), ctypes.c_size_t]
    k32.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    address = 0
    while address < 0x7FFF0000:
        if k32.VirtualQueryEx(handle, ctypes.c_void_p(address), ctypes.byref(mbi),
                              ctypes.sizeof(mbi)) == 0:
            break
        size = mbi.RegionSize
        if (mbi.State == MemorySource._MEM_COMMIT and
                (not private_only or mbi.Type == MemorySource._MEM_PRIVATE) and
                (mbi.Protect & 0xFF) in MemorySource._READABLE_PROTECT and
                0 < size < max_region_bytes):
            buffer = ctypes.create_string_buffer(size)
            read = ctypes.c_size_t(0)
            if k32.ReadProcessMemory(handle, ctypes.c_void_p(address), buffer, size,
                                     ctypes.byref(read)) and read.value:
                yield buffer.raw[:read.value]
        address += size if size else 0x1000


def _battlenet_pids():
    """`(renderers, main)` — Battle.net's renderer process ids, and its main process's.
    Its GPU and utility processes never hold presence, so they aren't worth a scan.

    Shells out to PowerShell rather than walking every process by hand: this runs only
    when there's no known-good process to read, not every poll.
    """
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
            "Get-CimInstance Win32_Process -Filter \"Name='Battle.net.exe'\" | "
            "ForEach-Object { '{0} {1}' -f $_.ProcessId, $(if ($_.CommandLine -match "
            "'--type=renderer') { 'renderer' } elseif ($_.CommandLine -match '--type=') "
            "{ 'helper' } else { 'main' }) }"],
            capture_output=True, text=True, timeout=10)
    except Exception:                                 # noqa: BLE001 - best effort
        return []
    renderers, main = [], []
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit():
            if parts[1] == "renderer":
                renderers.append(int(parts[0]))
            elif parts[1] == "main":
                main.append(int(parts[0]))
    return renderers, main


def find_identity(log=None):
    """The battletag/account id to filter presence reads by, from Battle.net's own
    `CachedData.db` (`login_cache`). Read-only, and tolerant of the file being locked by
    Battle.net itself - a busy database just means "try again next time", not a crash."""
    log = log or (lambda message: None)
    path = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Battle.net", "CachedData.db")
    if not os.path.exists(path):
        return None
    try:
        connection = sqlite3.connect("file:%s?mode=ro&immutable=1" % path, uri=True,
                                     timeout=1.0)
        row = connection.execute(
            "SELECT battle_tag, account_id_lo FROM login_cache LIMIT 1").fetchone()
        connection.close()
    except sqlite3.Error as problem:
        log("Couldn't read Battle.net's own account cache: %s" % problem)
        return None
    if not row:
        return None
    return queuepresence.Identity(row[0], row[1])


def battlenet_alive(port=DEFAULT_CDP_PORT):
    """A liveness proxy independent of whatever a source's own reads say: rich presence
    genuinely doesn't change while someone just sits in a menu, so an unchanged reading
    never proves the source is still alive. Battle.net's local web server answers on
    every path even though none of them are real routes (a 404 still proves the process
    is serving), which is a cheap enough signal for that."""
    if not PRESENCE_AVAILABLE:
        return False
    import urllib.error
    import urllib.request
    try:
        urllib.request.urlopen("http://127.0.0.1:22885/", timeout=0.25)
        return True
    except urllib.error.HTTPError:
        return True                                    # a 404 still means it answered
    except Exception:                                  # noqa: BLE001 - unreachable
        return False


# How long after one launch attempt before another is allowed, regardless of how many
# times a caller asks — Battle.net takes a few seconds to come up, and re-invoking
# `Start-Process` every time a poll's backoff fires would spawn nothing worse than a
# wasted process launch, but there is no reason to risk even that.
LAUNCH_COOLDOWN_SECONDS = 15.0
_last_launch_attempt = [0.0]

_BATTLENET_EXE_CANDIDATES = (
    r"C:\Program Files (x86)\Battle.net\Battle.net.exe",
    r"C:\Program Files\Battle.net\Battle.net.exe",
)


def battlenet_running() -> bool:
    """Whether any `Battle.net.exe` process exists at all, regardless of whether its
    debug port answers. `launch_battlenet` only ever launches when this is False —
    never kills or restarts a live one, since that could interrupt an active queue,
    the one thing this project exists not to do."""
    if not PRESENCE_AVAILABLE:
        return False
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
            "(Get-Process -Name 'Battle.net' -ErrorAction SilentlyContinue | "
            "Select-Object -First 1).Id"],
            capture_output=True, text=True, timeout=10)
    except Exception:                                  # noqa: BLE001 - best effort
        return True                # can't tell — assume it's there rather than risk
                                   # launching a second one alongside it
    return bool(completed.stdout.strip())


def launch_battlenet(port=DEFAULT_CDP_PORT, log=None) -> bool:
    """Starts Battle.net with its debug port open, but only when it isn't running at
    all — this is the "restart the client" recovery path for when Battle.net itself has
    closed or crashed out from under an established connection, not a way to force a
    running one to pick up the flag. Returns whether it actually launched anything."""
    log = log or (lambda message: None)
    if not PRESENCE_AVAILABLE:
        return False
    now = time.monotonic()
    if now - _last_launch_attempt[0] < LAUNCH_COOLDOWN_SECONDS:
        return False
    if battlenet_running():
        return False
    _last_launch_attempt[0] = now

    exe = next((path for path in _BATTLENET_EXE_CANDIDATES if os.path.exists(path)), None)
    if exe is None:
        log("Battle.net isn't running and couldn't be found to relaunch it")
        return False
    try:
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | \
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen([exe, "--remote-debugging-port=%d" % port],
                         creationflags=creationflags, close_fds=True)
        log("Battle.net wasn't running — launching it with its debug port open")
        return True
    except Exception as problem:                        # noqa: BLE001 - best effort
        log("Couldn't launch Battle.net: %s" % problem)
        return False


def force_relaunch_battlenet(port=DEFAULT_CDP_PORT, log=None) -> bool:
    """Closes a running Battle.net and starts it again with its debug port open — the
    fix for the one case `launch_battlenet` deliberately won't touch: Battle.net already
    open, but started by hand (no `--remote-debugging-port`), so presence can never
    become authoritative no matter how long this waits.

    Never called on its own — a person has to ask for this, because closing Battle.net
    can end an active game exactly the way an accidental automatic relaunch was written
    never to. Ignores `LAUNCH_COOLDOWN_SECONDS`, the same way asking twice in a row for
    anything else this deliberate would still mean it twice.
    """
    log = log or (lambda message: None)
    if not PRESENCE_AVAILABLE:
        return False

    if battlenet_running():
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                "Stop-Process -Name 'Battle.net' -Force -ErrorAction SilentlyContinue"],
                capture_output=True, timeout=10)
        except Exception as problem:                    # noqa: BLE001 - best effort
            log("Couldn't close Battle.net: %s" % problem)
            return False

        deadline = time.monotonic() + 10.0
        while battlenet_running():
            if time.monotonic() > deadline:
                log("Battle.net wouldn't close — leaving it alone")
                return False
            time.sleep(0.5)

    exe = next((path for path in _BATTLENET_EXE_CANDIDATES if os.path.exists(path)), None)
    if exe is None:
        log("Battle.net closed, but couldn't be found to launch again")
        return False
    try:
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | \
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen([exe, "--remote-debugging-port=%d" % port],
                         creationflags=creationflags, close_fds=True)
        _last_launch_attempt[0] = time.monotonic()
        log("Relaunched Battle.net with its debug port open")
        return True
    except Exception as problem:                        # noqa: BLE001 - best effort
        log("Couldn't launch Battle.net: %s" % problem)
        return False


def open_source(prefer="auto", port=DEFAULT_CDP_PORT, identity=None, log=None,
                relaunch=False):
    """Tries CDP, falls back to memory, never fails outright - a `NullSource` is always
    a legal answer so a caller never has to special-case "nothing worked".

    `relaunch=True` additionally launches Battle.net when it isn't running at all and
    the CDP path is what's being tried — see `launch_battlenet`. Off by default: a
    fresh server start has no business launching someone's Battle.net for them; only
    the recovery path from an established connection dying opts into it (see
    `QueueServer.watch_queue`'s `reopen` callback)."""
    log = log or (lambda message: None)
    identity = identity if identity is not None else find_identity(log=log)

    def try_cdp():
        if not CDP_AVAILABLE:
            return None
        try:
            _list_targets(port)
        except Exception:                              # noqa: BLE001 - port not open
            if relaunch:
                launch_battlenet(port=port, log=log)
            return None
        return CDPSource(port=port, identity=identity, log=log)

    def try_memory():
        if not PRESENCE_AVAILABLE or identity is None:
            return None
        return MemorySource(identity, log=log)

    if prefer == "cdp":
        return try_cdp() or NullSource()
    if prefer == "memory":
        return try_memory() or NullSource()
    if prefer == "off":
        return NullSource()
    return try_cdp() or try_memory() or NullSource()
