"""Checks GitHub Releases for a newer build and installs it.

Runs on its own thread like Relay, and the GUI reads `snapshot()` on its refresh timer rather
than waiting on the network. The version compared against is the one in the asset's filename,
not the release tag: release.yml only rebuilds the apps that changed and copies the rest of the
assets forward, so v2.0.42 can still hold OWQueue-Setup-2.0.40.exe."""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request

from version import VERSION

RELEASE_API = "https://api.github.com/repos/overjump1/overwatch-queue/releases/latest"
ASSET_PATTERN = re.compile(r"^OWQueue-Setup-(.+)\.exe$", re.IGNORECASE)
USER_AGENT = "OWQueue/%s" % VERSION
TIMEOUT_SECONDS = 20
CHECK_SECONDS = 6 * 3600
RETRY_SECONDS = 30 * 60

# What the footer button is doing; `version` in the snapshot is the one being offered.
IDLE = "idle"
CHECKING = "checking"
UP_TO_DATE = "upToDate"
AVAILABLE = "available"
DOWNLOADING = "downloading"
INSTALLING = "installing"
FAILED = "failed"


def parse_version(text):
    """`v2.0.42` becomes (2, 0, 42). A trailing non-number ends it, so `0.0.0-dev.7` is (0, 0, 0)."""
    if not text:
        return None
    parts = []
    for piece in re.split(r"[.\-+]", text.strip().lstrip("vV")):
        if not piece.isdigit():
            break
        parts.append(int(piece))
    if not parts:
        return None
    return tuple((parts + [0, 0, 0])[:3])


def is_newer(candidate, current):
    left, right = parse_version(candidate), parse_version(current)
    return left is not None and right is not None and left > right


def checks_for_updates():
    """Builds from source and pull request artifacts are 0.x and never check: they're older than
    every release, so they'd offer an update forever."""
    here = parse_version(VERSION)
    return here is not None and here[0] > 0


def _pick_asset(release):
    """The Windows installer in a `releases/latest` reply, as (version, url, size)."""
    for asset in release.get("assets") or []:
        match = ASSET_PATTERN.match(asset.get("name") or "")
        if match:
            return match.group(1), asset.get("browser_download_url"), asset.get("size") or 0
    return None


def find_update(release):
    """The installer from `release` if it's newer than this build, else None."""
    asset = _pick_asset(release)
    if asset is None or not asset[1] or not is_newer(asset[0], VERSION):
        return None
    return asset


def _fetch_release():
    request = urllib.request.Request(RELEASE_API, headers={
        "Accept": "application/vnd.github+json", "User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read() or b"{}")


class Updater:
    """Checks on launch and every six hours; downloads only once `download()` is called."""

    def __init__(self, data_dir, log=print):
        self.log = log
        self._dir = os.path.join(data_dir, "updates")
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._state = IDLE
        self._version = None
        self._percent = 0
        self._url = None
        self._size = 0
        self._ready = None  # the downloaded installer, once it's complete
        self._next_check = 0.0
        self._wants_download = False

    def snapshot(self):
        with self._lock:
            return self._state, self._version, self._percent

    def check_now(self):
        """The footer button, in a state where checking is what it should do."""
        with self._lock:
            if self._state in (CHECKING, DOWNLOADING, INSTALLING):
                return
            self._next_check = 0.0
        self._wake.set()

    def download(self):
        """The footer button, once an update is on offer."""
        with self._lock:
            if self._state != AVAILABLE or self._ready is not None:
                return
            self._wants_download = True
        self._wake.set()

    def ready(self):
        """True once the installer is downloaded and `install()` will do something."""
        with self._lock:
            return self._ready is not None and self._state == AVAILABLE

    def install(self):
        """Starts the installer and returns True; the app quits right after.

        /SILENT installs without asking anything (a per-user install needs no UAC), and
        installer.iss starts the app again when the install was silent."""
        with self._lock:
            path = self._ready if self._state == AVAILABLE else None
        if path is None:
            return False
        try:
            subprocess.Popen([path, "/SILENT", "/CLOSEAPPLICATIONS", "/FORCECLOSEAPPLICATIONS"],
                             creationflags=subprocess.DETACHED_PROCESS)
        except OSError as problem:
            self._fail("Couldn't start the installer: %s" % problem)
            return False
        with self._lock:
            self._state = INSTALLING
        self.log("Installing %s" % self._version)
        return True

    def run(self, stop):
        if not checks_for_updates():
            self.log("Update checks are off in this build (version %s)" % VERSION)
            return
        while not stop.is_set():
            try:
                if time.monotonic() >= self._next_check:
                    self._check()
                if self._take_download_request():
                    self._download(stop)
            except Exception as problem:  # noqa: BLE001
                self._fail("Update check failed: %s" % problem)
            self._wake.wait(timeout=30)
            self._wake.clear()

    # Steps

    def _check(self):
        with self._lock:
            # A finished download stays on offer; checking again would only find the same release.
            if self._ready is not None:
                self._next_check = time.monotonic() + CHECK_SECONDS
                return
            self._state = CHECKING
        try:
            release = _fetch_release()
        except (urllib.error.URLError, OSError, ValueError) as problem:
            with self._lock:
                self._next_check = time.monotonic() + RETRY_SECONDS
            self._fail("Couldn't reach GitHub: %s" % problem)
            return
        update = find_update(release)
        with self._lock:
            self._next_check = time.monotonic() + CHECK_SECONDS
            self._percent = 0
            if update is None:
                self._state, self._version, self._url = UP_TO_DATE, None, None
            else:
                self._state = AVAILABLE
                self._version, self._url, self._size = update
        if update is not None:
            self.log("Update available: %s" % update[0])

    def _take_download_request(self):
        with self._lock:
            wanted = self._wants_download and self._state == AVAILABLE and self._url is not None
            self._wants_download = False
            if wanted:
                self._state, self._percent = DOWNLOADING, 0
            return wanted

    def _download(self, stop):
        with self._lock:
            url, size, version = self._url, self._size, self._version
        target = os.path.join(self._dir, "OWQueue-Setup-%s.exe" % version)
        partial = target + ".part"
        try:
            os.makedirs(self._dir, exist_ok=True)
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            written = 0
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
                self._discard(partial)
                return
            # A truncated download would be an installer that doesn't run.
            if size and written != size:
                raise OSError("got %d bytes, expected %d" % (written, size))
            os.replace(partial, target)
        except (urllib.error.URLError, OSError, ValueError) as problem:
            self._discard(partial)
            self._fail("Download failed: %s" % problem)
            return
        with self._lock:
            self._ready, self._state, self._percent = target, AVAILABLE, 100
        self.log("Downloaded the %s installer" % version)

    # Helpers

    def _fail(self, message):
        self.log(message)
        with self._lock:
            self._state, self._percent = FAILED, 0

    @staticmethod
    def _discard(path):
        try:
            os.remove(path)
        except OSError:
            pass
