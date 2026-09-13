"""Asking Battle.net itself what the player is doing, instead of the screen.

Two ways in:

`MemorySource`, the default, reads the presence record straight out of Battle.net's own
process memory with a plain `ReadProcessMemory`, the way `queuedigits` reads pixels
rather than calling an OCR engine - no injection, and Overwatch itself is never touched.
It needs nothing from Battle.net at all, however Battle.net was started, which is why it
is the default: the debug port below only exists if Battle.net was launched with a flag,
and Battle.net rewrites its own auto-start entry and shortcut, so there is no dependable
way to make sure it was. It decodes the record's protobuf fields and hands them to the
same `queuepresence.parse_record` as `CDPSource` - confirmed live to read identically,
through Battle.net's own "In App" and Overwatch's "In Menus".

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
MEMORY_POLL_SECONDS = 3.0

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
    """Reads the same presence record out of Battle.net's own process memory.

    Battle.net keeps each presence record as the protobuf message it arrived in, so this
    decodes it rather than pattern-matching bytes: field 1 is the account id, 2 the
    battletag, 6 `program_id`, 7 `program_name` and 13 `rich_presence` — mapped field by
    field against the very record `CDPSource` reads for the same account, and handed to
    the same `queuepresence.parse_record`, so both sources mean exactly the same thing.
    Field numbers are a protobuf schema, which Battle.net can't renumber without breaking
    its own clients, so this survives far more than a byte-offset match would; if they
    ever do change, a record just stops decoding and reads as `UNKNOWN`, never a guess.

    Memory also holds stale copies of earlier records — freed, not zeroed — so every
    copy found is decoded and the newest by its own millisecond timestamps wins, rather
    than whichever happened to sit at the lowest address. And every Battle.net process
    is considered, not just the first renderer: the record lives in the main process and
    in only some of the renderers.
    """

    poll_seconds = MEMORY_POLL_SECONDS

    _PROCESS_QUERY_INFORMATION = 0x0400
    _PROCESS_VM_READ = 0x0010
    _MEM_COMMIT = 0x1000
    _READABLE_PROTECT = frozenset({0x02, 0x04, 0x08, 0x20, 0x40, 0x80})
    _MAX_REGION_BYTES = 200 * 1024 * 1024
    _RESCAN_AFTER_FAILURES = 3

    def __init__(self, identity, log=None):
        self.identity = identity
        self.log = log or (lambda message: None)
        self._pid = None
        self._failures = 0

    def read(self, when):
        unknown = queuepresence.PresenceReading(queuepresence.UNKNOWN, None, "", None, when)
        try:
            record = None
            if self._pid is not None and self._failures < self._RESCAN_AFTER_FAILURES:
                record = self._newest_record(self._pid)
            else:
                self._pid, self._failures = None, 0
                for pid in _battlenet_pids():
                    record = self._newest_record(pid)
                    if record is not None:
                        self._pid = pid
                        break
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
        self._pid = None

    def _newest_record(self, pid):
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        handle = k32.OpenProcess(self._PROCESS_QUERY_INFORMATION | self._PROCESS_VM_READ,
                                 False, pid)
        if not handle:
            return None
        try:
            return newest_presence_record(
                _readable_regions(k32, handle, self._MAX_REGION_BYTES), self.identity)
        finally:
            k32.CloseHandle(handle)


# Presence-record protobuf fields, by number, to the names `parse_record` reads — mapped
# against a live `subscribeSelfPresence` record for the same account (see `MemorySource`).
_PRESENCE_FIELDS = {1: "id", 2: "battle_tag", 6: "program_id", 7: "program_name",
                    13: "rich_presence"}
_RECORD_MAX_BYTES = 512
_EPOCH_MS_FLOOR = 10 ** 12            # a varint this big is a millisecond timestamp


def newest_presence_record(chunks, identity):
    """The newest presence record for `identity` across `chunks` of raw memory, as a
    `parse_record`-shaped dict, or `None` if no copy decodes. Pure — no process access —
    so the decoding itself is testable against plain bytes."""
    import re
    anchor = _record_anchor(identity)
    if anchor is None:
        return None
    pattern = re.compile(re.escape(anchor))
    best, best_time = None, -1
    for chunk in chunks:
        for match in pattern.finditer(chunk):
            fields = _decode_fields(chunk, match.start())
            record = {name: fields[number].decode("utf-8", "replace")
                      for number, name in _PRESENCE_FIELDS.items()
                      if isinstance(fields.get(number), bytes)}
            if "battle_tag" not in record:
                continue
            stamp = max((value for value in fields.values()
                         if isinstance(value, int) and value >= _EPOCH_MS_FLOOR), default=0)
            if stamp >= best_time:
                best, best_time = record, stamp
    return best


def _record_anchor(identity):
    """The bytes a record for this account starts with: field 1 (the account id) then
    field 2 (the battletag), each length-prefixed — or just field 2 without an id."""
    if identity is None or not identity.battletag:
        return None
    tag = identity.battletag.encode("utf-8", "ignore")
    tag_field = b"\x12" + _varint_bytes(len(tag)) + tag
    if not identity.account_id:
        return tag_field
    account = str(identity.account_id).encode("ascii")
    return b"\x0a" + _varint_bytes(len(account)) + account + tag_field


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


def _decode_fields(buf, pos, limit=_RECORD_MAX_BYTES):
    """Top-level protobuf fields from `pos`, stopping at the first thing that can't be
    one of this record's: a wire type it doesn't use, a length running off the end, or a
    field number that doesn't climb — which is where the next record begins."""
    end = min(len(buf), pos + limit)
    fields, last = {}, 0
    while pos < end:
        key, pos = _read_varint(buf, pos, end)
        if key is None:
            break
        number, wire = key >> 3, key & 7
        if number <= last:
            break
        last = number
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
        fields[number] = value
    return fields


class _MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", ctypes.c_ulong), ("RegionSize", ctypes.c_size_t),
        ("State", ctypes.c_ulong), ("Protect", ctypes.c_ulong), ("Type", ctypes.c_ulong),
    ]


def _readable_regions(k32, handle, max_region_bytes):
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
                (mbi.Protect & 0xFF) in MemorySource._READABLE_PROTECT and
                0 < size < max_region_bytes):
            buffer = ctypes.create_string_buffer(size)
            read = ctypes.c_size_t(0)
            if k32.ReadProcessMemory(handle, ctypes.c_void_p(address), buffer, size,
                                     ctypes.byref(read)) and read.value:
                yield buffer.raw[:read.value]
        address += size if size else 0x1000


def _battlenet_pids():
    """Every Battle.net process, renderers first — the presence record has been found in
    the main process and in only some renderers, never all of them.

    Shells out to PowerShell rather than walking every process by hand: this runs only
    when there's no known-good process to read, not every poll.
    """
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
            "Get-CimInstance Win32_Process -Filter \"Name='Battle.net.exe'\" | "
            "ForEach-Object { '{0} {1}' -f $_.ProcessId, "
            "[int]($_.CommandLine -match '--type=renderer') }"],
            capture_output=True, text=True, timeout=10)
    except Exception:                                 # noqa: BLE001 - best effort
        return []
    renderers, others = [], []
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit():
            (renderers if parts[1] == "1" else others).append(int(parts[0]))
    return renderers + others


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
