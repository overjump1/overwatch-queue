"""Checks GitHub Releases for a newer build and installs it.

Runs on its own thread like Relay; the GUI reads `snapshot()` on its refresh timer rather than
waiting on the network."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request

from version import RELEASE_API, VERSION

ASSET = re.compile(r"^OverQueue-Setup-(.+)\.exe$", re.IGNORECASE)
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "OverQueue/%s" % VERSION}
TIMEOUT_SECONDS = 20
CHECK_SECONDS = 6 * 3600
RETRY_SECONDS = 30 * 60

IDLE = "idle"
CHECKING = "checking"
UP_TO_DATE = "upToDate"
AVAILABLE = "available"
DOWNLOADING = "downloading"
INSTALLING = "installing"
FAILED = "failed"
BUSY = (CHECKING, DOWNLOADING, INSTALLING)


def parse_version(text):
    """`v2.0.42` becomes (2, 0, 42). A trailing non-number ends it, so `0.0.0-dev.7` is (0, 0, 0)."""
    parts = []
    for piece in re.split(r"[.\-+]", (text or "").strip().lstrip("vV")):
        if not piece.isdigit():
            break
        parts.append(int(piece))
    return tuple((parts + [0, 0, 0])[:3]) if parts else None


def is_newer(candidate, current):
    left, right = parse_version(candidate), parse_version(current)
    return left is not None and right is not None and left > right


def checks_for_updates():
    """False for builds from source and pull requests: they're 0.x, below every release, so they
    would offer an update forever."""
    here = parse_version(VERSION)
    return here is not None and here[0] > 0


def find_update(release):
    """The Windows installer in a `releases/latest` reply as (version, url, size), if it's newer
    than this build.

    The version is the one in the asset's filename, not the release tag: release.yml only rebuilds
    the apps that changed and copies the rest forward, so v2.0.3 holds OverQueue-Setup-2.0.2.exe and
    going by the tag would have this build reinstalling itself forever."""
    for asset in release.get("assets") or []:
        found = ASSET.match(asset.get("name") or "")
        if not found:
            continue
        url = asset.get("browser_download_url")
        if url and is_newer(found.group(1), VERSION):
            return found.group(1), url, asset.get("size") or 0
        return None
    return None


class Updater:
    """Checks on launch and every six hours. One `press()` does the whole update: it downloads the
    installer and starts it, and the GUI quits once the state reaches INSTALLING."""

    def __init__(self, data_dir, log=print):
        self.log = log
        self._dir = os.path.join(data_dir, "updates")
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._state, self._version, self._percent = IDLE, None, 0
        self._update = None  # (version, url, size) once one is on offer
        self._go = False
        self._next_check = 0.0

    def snapshot(self):
        with self._lock:
            return self._state, self._version, self._percent

    def press(self):
        """The footer button: install the update on offer, or go and look for one."""
        with self._lock:
            if self._state in BUSY:
                return
            if self._update is None:
                self._next_check = 0.0
            else:
                self._go = True
        self._wake.set()

    def run(self, stop):
        if not checks_for_updates():
            self.log("Update checks are off in this build (version %s)" % VERSION)
            return
        while not stop.is_set():
            try:
                if self._due():
                    self._check()
                if self._take_go():
                    self._install(stop)
            except Exception as problem:  # noqa: BLE001
                self._fail("Update failed: %s" % problem)
            self._wake.wait(timeout=30)
            self._wake.clear()

    def _due(self):
        with self._lock:
            return time.monotonic() >= self._next_check

    def _take_go(self):
        with self._lock:
            going = self._go and self._update is not None
            self._go = False
            if going:
                self._state, self._percent = DOWNLOADING, 0
            return going

    def _check(self):
        with self._lock:
            self._state = CHECKING
        try:
            request = urllib.request.Request(RELEASE_API, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                update = find_update(json.loads(response.read() or b"{}"))
        except (urllib.error.URLError, OSError, ValueError, AttributeError) as problem:
            with self._lock:
                self._next_check = time.monotonic() + RETRY_SECONDS
            self._fail("Couldn't reach GitHub: %s" % problem)
            return
        with self._lock:
            self._next_check = time.monotonic() + CHECK_SECONDS
            self._update, self._percent = update, 0
            self._state = AVAILABLE if update else UP_TO_DATE
            self._version = update[0] if update else None
        if update:
            self.log("Update available: %s" % update[0])

    def _install(self, stop):
        """Downloads the installer and starts it. /SILENT asks nothing (a per-user install needs no
        UAC) and installer.iss starts the app again once a silent install finishes."""
        with self._lock:
            version, url, size = self._update
        try:
            path = self._download(url, size, version, stop)
        except (urllib.error.URLError, OSError, ValueError) as problem:
            self._fail("Download failed: %s" % problem)
            return
        if path is None:
            return
        try:
            subprocess.Popen([path, "/SILENT", "/CLOSEAPPLICATIONS", "/FORCECLOSEAPPLICATIONS"],
                             creationflags=subprocess.DETACHED_PROCESS)
        except OSError as problem:
            self._fail("Couldn't start the installer: %s" % problem)
            return
        self.log("Installing %s" % version)
        with self._lock:
            self._state = INSTALLING

    def _download(self, url, size, version, stop):
        """The installer on disk, or None if `stop` was set part way through."""
        target = os.path.join(self._dir, "OverQueue-Setup-%s.exe" % version)
        if size and os.path.exists(target) and os.path.getsize(target) == size:
            return target
        # Whatever else is in there is an older release's installer, and 50MB apiece.
        shutil.rmtree(self._dir, ignore_errors=True)
        os.makedirs(self._dir, exist_ok=True)
        partial = target + ".part"
        written = 0
        request = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            total = size or int(response.headers.get("Content-Length") or 0)
            with open(partial, "wb") as handle:
                while not stop.is_set():
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    written += len(chunk)
                    if total:
                        with self._lock:
                            self._percent = min(99, written * 100 // total)
        if stop.is_set():
            return None
        if size and written != size:
            raise OSError("got %d bytes, expected %d" % (written, size))
        os.replace(partial, target)
        self.log("Downloaded the %s installer" % version)
        return target

    def _fail(self, message):
        """Leaves any update on offer in place, so pressing the button tries it again."""
        self.log(message)
        with self._lock:
            self._state, self._percent = FAILED, 0
