"""Sends state changes to the Cloudflare worker and checks whether a phone is paired.
Runs on its own thread so the GUI and detector never wait on the network."""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request

import version

WORKER_URL = os.environ.get("OVERQUEUE_WORKER_URL", version.WORKER_URL).rstrip("/")
USER_AGENT = "OverQueue/2.0"
TIMEOUT_SECONDS = 10
# How often to ask who's paired, while that's still worth asking -- see `run`.
UNPAIRED_CHECK_SECONDS = 5
PAIRED_KEYS = (("iPhone", "phonePaired"), ("Android", "androidPaired"))
MAX_BACKOFF_SECONDS = 30


def _request(method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(WORKER_URL + path, data=data, method=method, headers={
        "Content-Type": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, None


def _paired_names(body):
    """The phones the worker says are registered, e.g. ["iPhone"]."""
    body = body if isinstance(body, dict) else {}
    return [name for name, key in PAIRED_KEYS if body.get(key)]


def _says_paired(body):
    """Whether `body` answers the pairing question at all. A worker too old to put it in a
    report's reply must not read as "nobody's paired" -- that would leave the QR code up for
    good. Saying nothing just leaves the check to `_check_paired`, the way it used to be."""
    return isinstance(body, dict) and any(key in body for _, key in PAIRED_KEYS)


def _report(pending):
    """The body for `pending`, with its times aged to now: a report can sit in the queue
    (retries, a pairing reset) and the worker dates the queue and the match from them."""
    age = max(0.0, time.monotonic() - pending["at"])
    elapsed, since_found = pending["elapsed"], pending["sinceFound"]
    if pending["state"] == "queueing":
        elapsed += age
    elif pending["state"] == "found":
        since_found += age
    return {"state": pending["state"], "mode": pending["mode"],
            "elapsed": round(elapsed), "sinceFound": round(since_found)}


class Relay:
    def __init__(self, pair_id, log=print):
        self.log = log
        self.paired = []  # names of the phones that registered with this pairing, e.g. ["iPhone"]
        self.reachable = True
        self._pair_id = pair_id
        self._pending = None
        self._last_sent = None
        self._stale_ids = []
        self._next_check = 0.0
        self._expecting_phone = False
        self._lock = threading.Lock()
        self._wake = threading.Event()

    def publish(self, state, mode, elapsed, since_found=0.0):
        with self._lock:
            self._pending = {"state": state, "mode": mode, "elapsed": elapsed,
                             "sinceFound": since_found, "at": time.monotonic()}
        self._wake.set()

    def change_pair(self, pair_id):
        with self._lock:
            self._stale_ids.append(self._pair_id)
            self._pair_id = pair_id
            if self._pending is None:
                self._pending = self._last_sent
            self.paired = []
            self._next_check = 0.0
        self._wake.set()

    def expect_phone(self, expecting):
        """While the QR code is on screen, check often so a newly scanned phone shows up quickly."""
        with self._lock:
            changed = expecting != self._expecting_phone
            if expecting and changed:
                self._next_check = 0.0
            self._expecting_phone = expecting
        # Only on a change: the GUI calls this twice a second, and waking the loop each
        # time would spin it for nothing.
        if changed:
            self._wake.set()

    def run(self, stop):
        """Delivers the latest state and, while that's still worth asking, checks which phones
        are paired. The watcher only speaks up when something changes, so a PC sitting idle -- or
        sitting in a long queue -- makes no requests at all."""
        backoff = 1
        while not stop.is_set():
            try:
                self._delete_stale()
                if not self._send_pending():
                    raise ConnectionError("state not delivered")
                # Only worth asking while the QR code is up or nothing has paired yet: a
                # pairing the worker knows about never goes away on its own, and every report
                # brings the answer back with it anyway.
                with self._lock:
                    asking = self._expecting_phone or not self.paired
                    due = time.monotonic() >= self._next_check
                if asking and due:
                    self._check_paired()
                    with self._lock:
                        self._next_check = time.monotonic() + UNPAIRED_CHECK_SECONDS
                self.reachable = True
                backoff = 1
                self._wake.wait(timeout=1)
                self._wake.clear()
            except Exception as problem:  # noqa: BLE001
                self.reachable = False
                self.log("Worker unreachable: %s" % problem)
                stop.wait(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)

    def _send_pending(self):
        with self._lock:
            pending, pair_id = self._pending, self._pair_id
        if pending is None:
            return True
        try:
            status, body = _request("POST", "/v1/pair/%s/state" % pair_id, _report(pending))
        except Exception as problem:  # noqa: BLE001
            self.log("Sending state failed: %s" % problem)
            return False
        if status >= 500:
            return False
        if status != 200:
            self.log("Worker rejected state %s: HTTP %d" % (pending, status))
        elif _says_paired(body):
            # The reply carries who's paired, so a report doubles as the pairing check.
            self._set_paired(pair_id, _paired_names(body))
        with self._lock:
            if self._pending is pending:
                self._pending = None
            self._last_sent = pending
        return True

    def _check_paired(self):
        with self._lock:
            pair_id = self._pair_id
        status, body = _request("GET", "/v1/pair/%s/state" % pair_id)
        self._set_paired(pair_id, _paired_names(body if status == 200 else None))

    def _set_paired(self, pair_id, paired):
        """Drops an answer for a pairing that was reset while the request was in flight."""
        with self._lock:
            if pair_id == self._pair_id:
                self.paired = paired

    def _delete_stale(self):
        with self._lock:
            stale = list(self._stale_ids)
        for pair_id in stale:
            status, _ = _request("DELETE", "/v1/pair/%s" % pair_id)
            if status < 500:
                with self._lock:
                    self._stale_ids.remove(pair_id)
