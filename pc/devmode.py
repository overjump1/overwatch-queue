"""Battle.net's developer mode: started with `--remote-debugging-port`, Battle.net's own
embedded Chromium (CEF) answers the Chrome DevTools Protocol, and the exact call its UI
makes to show your presence to a friend -- `phoenix.socialService.subscribeSelfPresence` --
hands back a clean, structured record. Nothing to scrape, nothing a re-skin of the app
could quietly break, and the answer arrives the moment Battle.net changes it.

Battle.net only opens that port if it was launched with the flag, and it rewrites its own
`Run` auto-start entry and Start Menu shortcut on every start, so nothing persistent can
arrange for one; a flagged launch alongside a running unflagged copy just hands off to that
copy and exits. A flagged launch from a clean start is the one thing that works, so
`ensure` starts Battle.net with the flag when it isn't running, and closes and reopens it
when it is running without the port. That's skipped while Overwatch is open: nothing here
is worth interrupting a game for, and `Reader` falls back to reading Battle.net's memory
whenever the port isn't there, so the app keeps working either way.

Read-only, like `presence`: the only thing written anywhere is Battle.net's own command
line, and Overwatch is never touched.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.request

import presence

try:  # pragma: no cover - absent on the Mac/Linux dev path
    import websocket as _ws
except ImportError:  # pragma: no cover - see requirements.txt
    _ws = None

DEBUG_PORT = 9222
AVAILABLE = presence.AVAILABLE and _ws is not None

TIMEOUT_SECONDS = 2.0
# A closed port ought to refuse a connection instantly, but one that's dropped rather than
# refused would sit on the timeout above -- every read, on the thread the queue is read on.
PROBE_SECONDS = 0.3
# How long before a dropped connection is tried again. There's no point asking every
# second: Battle.net takes a while to come back up, and memory is covering us meanwhile.
RECONNECT_SECONDS = 5.0
# How long a running Battle.net gets for its port to start answering before it counts as
# started without one. It opens a few seconds into startup, so an app opened right after
# Battle.net itself mustn't mistake "still starting" for "started wrong" and close it.
GRACE_SECONDS = 15.0
CLOSE_TIMEOUT_SECONDS = 10.0

OVERWATCH_PROGRAM_NAME = "Overwatch"

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

# Wrapped in a Promise so one `Runtime.evaluate` with `awaitPromise` gets exactly one
# settled answer, and `JSON.stringify`d so CDP hands back a plain string rather than a
# remote object reference that would need a second round trip to read.
_SELF_PRESENCE_JS = """
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


def classify(record):
    """A `subscribeSelfPresence` record -> `(state, mode)`, in `presence`'s vocabulary.

    Total, and deliberately so: `DebugPort.read` promises never to raise, and what comes
    back over the wire is whatever Battle.net's frontend chose to hand over -- anything but
    a record it can read is `UNKNOWN`, never an exception.

    A program that isn't Overwatch is `ELSEWHERE` and never falls through to `parse`:
    another game's rich presence has nothing to do with this app's words. Finding no
    program named at all is `UNKNOWN`, not `ELSEWHERE` -- only a positively named *other*
    program is evidence there's no Overwatch queue.
    """
    if not isinstance(record, dict) or not record:
        return presence.UNKNOWN, None
    program_id = record.get("program_id")
    program_name = record.get("program_name")
    if program_id or program_name:
        if program_id != presence.OVERWATCH_PROGRAM and program_name != OVERWATCH_PROGRAM_NAME:
            return presence.ELSEWHERE, None
    return presence.parse(record.get("rich_presence") or "")


class DebugPort:
    """Reads presence over Battle.net's debug port. `read()` returns `(state, mode)` and
    never raises; `connected` says whether that read came off a real record.

    One target is picked per connection (the app shell page -- never the `webview`
    targets, which are embedded content like the news carousel) and its WebSocket is kept
    open across reads; a failed read closes it, so the next one reconnects and re-picks
    rather than trusting a stale target.

    The record is our own account's by construction -- `subscribeSelfPresence` is the
    logged-in account's own presence, not a lookup -- so unlike the memory reader there is
    no friend's record to tell it apart from.
    """

    def __init__(self, port=DEBUG_PORT, log=print):
        self.port = port
        self.log = log
        self.connected = False
        self._socket = None
        self._request = 0
        self._retry_at = 0.0
        self._quiet = False

    def read(self):
        if self._socket is None and time.monotonic() < self._retry_at:
            return presence.UNKNOWN, None
        try:
            if self._socket is None:
                self._connect()
            record = self._evaluate(_SELF_PRESENCE_JS)
            if not isinstance(record, dict) or not record:
                # The port is there but there's no record to read: Battle.net is still
                # signing in, its frontend hasn't finished loading, or what came back isn't
                # a record at all. Not connected, so `Reader` goes back to reading memory.
                self._note(False, "no presence record yet" if not record else
                           "unreadable presence record: %r" % (record,))
                return presence.UNKNOWN, None
            reading = classify(record)
        except Exception as problem:  # noqa: BLE001 - never raise from a read
            self._note(False, problem)
            self.close()
            self._retry_at = time.monotonic() + RECONNECT_SECONDS
            return presence.UNKNOWN, None
        self._note(True, None)
        return reading

    def close(self):
        self.connected = False
        if self._socket is not None:
            try:
                self._socket.close()
            except Exception:  # noqa: BLE001 - already going away
                pass
            self._socket = None

    def _note(self, connected, why):
        """One line when the port stops answering and one when it comes back, so a long
        outage leaves a pair of lines in the log rather than one a second."""
        self.connected = connected
        if connected == (not self._quiet):
            return
        self._quiet = not connected
        if connected:
            self.log("Battle.net's debug port is answering again")
        else:
            self.log("Battle.net's debug port didn't answer: %s" % why)

    def _connect(self):
        target = _app_shell(_targets(self.port))
        if target is None:
            raise RuntimeError("nothing answered on port %d" % self.port)
        # No `Origin` header: current Chromium rejects a DevTools WebSocket that carries one
        # unless it was started with `--remote-allow-origins`, and a client that isn't a web
        # page has no origin to claim anyway. Measured against Chromium 141 -- the handshake
        # is a 403 with the header and fine without it.
        self._socket = _ws.create_connection(target["webSocketDebuggerUrl"],
                                             timeout=TIMEOUT_SECONDS, suppress_origin=True)

    def _evaluate(self, expression):
        """Runs one JS expression -- a Promise resolving to a JSON string, by convention --
        and returns the decoded value, or None."""
        self._request += 1
        request = self._request
        self._socket.send(json.dumps({
            "id": request, "method": "Runtime.evaluate",
            "params": {"expression": expression, "returnByValue": True, "awaitPromise": True},
        }))
        deadline = time.monotonic() + TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            message = json.loads(self._socket.recv())
            if message.get("id") != request:
                continue  # an unrelated event; keep waiting for our answer
            result = message.get("result", {}).get("result", {})
            if result.get("subtype") == "error":
                raise RuntimeError(result.get("description", "presence eval failed"))
            value = result.get("value")
            return json.loads(value) if value and value != "null" else None
        raise TimeoutError("Battle.net's debug port didn't answer in time")


def _targets(port):
    if not _listening(port):
        raise ConnectionError("nothing is listening on port %d" % port)
    with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def _listening(port):
    """Whether anything holds the port at all, asked the cheap way -- see `PROBE_SECONDS`."""
    try:
        socket.create_connection(("127.0.0.1", port), PROBE_SECONDS).close()
        return True
    except OSError:
        return False


def _app_shell(targets):
    """The app shell over a content webview -- see `DebugPort` for why."""
    attached = [target for target in targets if "webSocketDebuggerUrl" in target]
    pages = [target for target in attached if target.get("type") == "page"]
    return (pages or attached or [None])[0]


# --- getting Battle.net into developer mode ---------------------------------------------


def find_exe():
    for root in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                 r"C:\Program Files (x86)", r"C:\Program Files"):
        path = os.path.join(root, "Battle.net", "Battle.net.exe") if root else None
        if path and os.path.exists(path):
            return path
    return None


def running():
    return bool(presence.pids_named("battle.net.exe"))


def port_open(port=DEBUG_PORT):
    try:
        _targets(port)
        return True
    except Exception:  # noqa: BLE001 - closed is the answer, not an error
        return False


def launch(port=DEBUG_PORT, log=print):
    exe = find_exe()
    if exe is None:
        log("Couldn't find Battle.net to start it in developer mode")
        return False
    try:
        subprocess.Popen([exe, "--remote-debugging-port=%d" % port],
                         creationflags=_DETACHED, close_fds=True)
    except OSError as problem:
        log("Couldn't start Battle.net: %s" % problem)
        return False
    log("Started Battle.net in developer mode")
    return True


def relaunch(port=DEBUG_PORT, log=print, stop=None):
    """Closes a Battle.net running without its debug port and starts it again with one.
    Refuses while Overwatch is open, because closing Battle.net under a running game can
    end it, and a queue read the slower way beats a queue dropped.

    `stop` is looked at once, before anything is closed. Past that the restart always runs
    to the end: a Battle.net this closed and never reopened is worse than a late one."""
    if presence.pids_named("overwatch.exe"):
        log("Battle.net is running without its debug port, but Overwatch is open — leaving it alone")
        return False
    if stop is not None and stop.is_set():
        return False
    log("Battle.net is running without its debug port — restarting it in developer mode")
    try:
        # By image name, never `/T`: Overwatch is Battle.net's own child, and killing the
        # tree would take the game with it. Every Battle.net helper is a Battle.net.exe.
        closing = subprocess.run(["taskkill", "/IM", "Battle.net.exe", "/F"], capture_output=True,
                                 text=True, timeout=CLOSE_TIMEOUT_SECONDS, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as problem:
        log("Couldn't close Battle.net: %s" % problem)
        return False
    deadline = time.monotonic() + CLOSE_TIMEOUT_SECONDS
    while running():
        if time.monotonic() >= deadline:
            # In taskkill's own words, so an elevated Battle.net says "Access is denied"
            # here instead of leaving the log with an unexplained refusal.
            log("Battle.net wouldn't close (%s) — leaving it alone" % _why(closing))
            return False
        time.sleep(0.5)
    return launch(port, log)


def _why(closing):
    """What a `taskkill` that didn't do the job had to say for itself."""
    if not closing.returncode:
        return "it's still there"
    return (closing.stderr or closing.stdout or "").strip() or "exit code %d" % closing.returncode


def ensure(port=DEBUG_PORT, log=print, grace_seconds=GRACE_SECONDS, stop=None):
    """Gets Battle.net running with its debug port open. Returns whether it started one.

    Blocks for up to `grace_seconds` waiting on a Battle.net that's still coming up, so run
    it off the GUI thread -- and hand it the app's `stop` event, so someone who quits in the
    meantime doesn't get their Battle.net closed on the way out the door."""
    if not AVAILABLE:
        return False
    if not running():
        return launch(port, log)
    deadline = time.monotonic() + grace_seconds
    while not port_open(port):
        if not running():
            return False  # closed while we waited; nothing to restart
        if time.monotonic() >= deadline:
            return relaunch(port, log, stop)
        if _waited(stop, 1.0):
            return False  # the app is quitting
    return False


def _waited(stop, seconds):
    """Waits, and says whether the app is on its way out."""
    if stop is None:
        time.sleep(seconds)
        return False
    return stop.wait(seconds)


# --- what the app reads through -----------------------------------------------------------


class Reader:
    """Battle.net presence, over its debug port when that's open and out of its memory when
    it isn't -- same `read()` and `connected` as `presence.Presence`, plus `source`, which
    names whichever of the two the last read came from.

    The fallback isn't a nicety: the port is gone the moment someone restarts Battle.net
    themselves, and reading memory needs nothing from Battle.net at all, however it was
    started. Developer mode is what's aimed for; memory is what's always there.
    """

    def __init__(self, mode="devmode", port=DEBUG_PORT, log=print):
        self.log = log
        self.port = port
        # No websocket module, or not Windows: there's no port to read, whatever was asked.
        self.mode = mode if AVAILABLE else "memory"
        self.memory = presence.Presence(log=log)
        self.debug = DebugPort(port, log=log) if self.mode != "memory" else None
        self.connected = False
        self.source = "memory"

    def start(self, stop=None):
        """Puts Battle.net into developer mode, once. Blocks -- run it on a thread, and pass
        the app's `stop` event so quitting cuts it short before it closes anything."""
        if self.mode == "devmode":
            ensure(self.port, self.log, stop=stop)

    def read(self):
        if self.debug is not None:
            state, mode = self.debug.read()
            if self.debug.connected:
                if self.source != "devmode":
                    self.log("Reading presence over Battle.net's debug port")
                    self.source = "devmode"
                self.connected = True
                return state, mode
            if self.source == "devmode":
                self.log("Reading presence out of Battle.net's memory instead")
                self.source = "memory"
                # The copy counts from before the gap say nothing about what arrived during
                # it, so the memory reader starts its counting over rather than reading the
                # catch-up as a wave of changes.
                self.memory.rebaseline()
        state, mode = self.memory.read()
        self.connected = self.memory.connected
        return state, mode
