"""Asking Battle.net itself what the player is doing, instead of the screen.

Two ways in, tried in this order:

`CDPSource` talks to Battle.net's own embedded Chromium (CEF) over the Chrome DevTools
Protocol, if Battle.net was started with `--remote-debugging-port`. Once connected it
calls the exact same function Battle.net's own UI calls to show your presence to a
friend - `phoenix.socialService.subscribeSelfPresence` - which hands back a clean,
structured record (battletag, program, the rich-presence string) with nothing to scrape
and nothing that a re-skin of the app could quietly break. Confirmed live against a real
account: a queue starting and ending was seen through this path in real time.

`MemorySource` is the fallback for a Battle.net that wasn't launched with the debug
flag: reading the same presence record straight out of the memory of Battle.net's own
CEF renderer process with a plain `ReadProcessMemory`, the way `queuedigits` reads pixels
rather than calling an OCR engine - no injection, and Overwatch itself is never touched.
It is heuristic where `CDPSource` is exact (there is no schema to ask for; it is pattern-
matching a byte layout that could shift on a Battle.net update), which is why it is the
fallback and not the default whenever the debug port is available.

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
    """Reads the same presence record out of Battle.net's own renderer process memory.

    Heuristic by nature: there is no schema here, only a byte pattern - our own account
    id, immediately followed by our own battletag, immediately followed (a short, mostly
    stable gap later) by the rich-presence text - observed on a real account and not
    guaranteed to survive a Battle.net update. It is the documented fallback for exactly
    that reason: whenever the debug port is reachable, `open_source` prefers it.
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
        try:
            if self._pid is None or self._failures >= self._RESCAN_AFTER_FAILURES:
                self._pid = _find_renderer_pid()
                self._failures = 0
            if self._pid is None:
                return queuepresence.PresenceReading(queuepresence.UNKNOWN, None, "",
                                                     None, when)
            text = self._scan(self._pid)
        except Exception as problem:                  # noqa: BLE001 - never raise
            self.log("Reading Battle.net's memory failed: %s" % problem)
            self._failures += 1
            return queuepresence.PresenceReading(queuepresence.UNKNOWN, None, "", None, when)

        if text is None:
            self._failures += 1
            return queuepresence.PresenceReading(queuepresence.UNKNOWN, None, "", None, when)
        self._failures = 0
        state, mode = queuepresence.parse(text)
        return queuepresence.PresenceReading(state, mode, text, self.identity.battletag, when)

    def close(self):
        self._pid = None

    def _scan(self, pid):
        """Finds our own account id + battletag, then the first recognisable presence
        activity within a short distance after it. Returns the raw text, or `None`."""
        needle = None
        if self.identity.account_id:
            needle = (self.identity.account_id.encode("ascii") + b".{0,24}" +
                     self.identity.battletag.encode("utf-8", "ignore"))
        elif self.identity.battletag:
            needle = self.identity.battletag.encode("utf-8", "ignore")
        if needle is None:
            return None

        import re
        pattern = re.compile(needle + rb".{0,120}", re.DOTALL)

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = k32.OpenProcess(self._PROCESS_QUERY_INFORMATION | self._PROCESS_VM_READ,
                                 False, pid)
        if not handle:
            return None
        try:
            for chunk in _readable_regions(k32, handle, self._MAX_REGION_BYTES):
                match = pattern.search(chunk)
                if not match:
                    continue
                candidate = _first_known_activity(match.group())
                if candidate is not None:
                    return candidate
        finally:
            k32.CloseHandle(handle)
        return None


def _first_known_activity(blob):
    """The first printable run in `blob` that `queuepresence.parse` actually recognises.

    Scans every printable-ASCII run rather than trusting a fixed offset, because the
    exact byte distance from the battletag to the presence text isn't a stable contract
    - only the text itself, and only once `parse` has approved it, is trusted.
    """
    import re
    for run in re.finditer(rb"[ -~]{4,80}", blob):
        text = run.group().decode("ascii", "replace")
        state, _mode = queuepresence.parse(text)
        if state != queuepresence.UNKNOWN:
            return text
    return None


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


def _find_renderer_pid():
    """The PID of a Battle.net CEF renderer process - where the presence cache lives.

    Shells out to PowerShell rather than walking every process by hand: this runs at
    most once every few failed reads (`_RESCAN_AFTER_FAILURES`), not every poll, so the
    cost of one process spawn is traded for not hand-rolling a WMI/toolhelp walk.
    """
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
            "(Get-CimInstance Win32_Process -Filter \"Name='Battle.net.exe'\" | "
            "Where-Object { $_.CommandLine -match '--type=renderer' } | "
            "Select-Object -First 1 -ExpandProperty ProcessId)"],
            capture_output=True, text=True, timeout=10)
    except Exception:                                 # noqa: BLE001 - best effort
        return None
    out = completed.stdout.strip()
    return int(out) if out.isdigit() else None


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
