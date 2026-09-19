"""Sends state changes to the Cloudflare worker and checks whether a phone is paired.
Runs on its own thread so the GUI and detector never wait on the network."""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request

WORKER_URL = os.environ.get("OWQ_WORKER_URL", "https://overwatch-queue-push-relay.tomerady.workers.dev").rstrip("/")
USER_AGENT = "OWQueue/2.0"
TIMEOUT_SECONDS = 10
UNPAIRED_CHECK_SECONDS = 5
PAIRED_CHECK_SECONDS = 60
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

    def run(self, stop):
        """Delivers the latest state and checks which phones are paired. The watcher
        re-publishes the state every minute so the worker can tell the PC is still there."""
        backoff = 1
        while not stop.is_set():
            try:
                self._delete_stale()
                if not self._send_pending():
                    raise ConnectionError("state not delivered")
                if time.monotonic() >= self._next_check:
                    self._check_paired()
                    with self._lock:
                        wait = PAIRED_CHECK_SECONDS if self.paired else UNPAIRED_CHECK_SECONDS
                        self._next_check = time.monotonic() + wait
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
            status, _ = _request("POST", "/v1/pair/%s/state" % pair_id, _report(pending))
        except Exception as problem:  # noqa: BLE001
            self.log("Sending state failed: %s" % problem)
            return False
        if status >= 500:
            return False
        if status != 200:
            self.log("Worker rejected state %s: HTTP %d" % (pending, status))
        with self._lock:
            if self._pending is pending:
                self._pending = None
            self._last_sent = pending
        return True

    def _check_paired(self):
        with self._lock:
            pair_id = self._pair_id
        status, body = _request("GET", "/v1/pair/%s/state" % pair_id)
        body = body if status == 200 and isinstance(body, dict) else {}
        paired = [name for name, key in (("iPhone", "phonePaired"), ("Android", "androidPaired")) if body.get(key)]
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
