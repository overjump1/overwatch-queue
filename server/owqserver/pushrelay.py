"""Talks to the maintainer's push relay (see `relay/`) instead of to Apple directly.

This is the default push path: it needs no Apple Developer account, no `.p8` key, and no
extra dependency beyond the standard library — `apns.py`'s direct-to-Apple client is still
there for anyone who'd rather hold their own Auth Key (see `server/README.md`), but that's
the advanced path, not the one most installs use.

Same interface as `apns.APNsClient` on purpose (`send_background`, `send_alert`,
`token_is_invalid`) — `queueserver.py` doesn't need to know or care which one `self.apns`
actually is.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".overwatch-queue")
CONFIG_PATH = os.path.join(CONFIG_DIR, "push_relay.json")


class PushRelayConfig:
    def __init__(self, url: str, api_key: str):
        self.url = url.rstrip("/")
        self.api_key = api_key

    @classmethod
    def load(cls, path: str = CONFIG_PATH):
        """`None` if no relay is configured — a server with neither this nor `apns.json`
        simply never pushes, the same as any other optional feature here."""
        try:
            with open(path) as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return None
        url, api_key = data.get("url"), data.get("api_key")
        if not url or not api_key:
            return None
        return cls(url, api_key)


class PushRelayClient:
    def __init__(self, config: PushRelayConfig, log=None):
        self.config = config
        self.log = log or (lambda message: None)

    def close(self):
        pass        # nothing held open — `urllib` opens and closes a connection per call

    def send_background(self, kind: str, device_token: str, environment: str,
                        session_id: str, sequence: int):
        return self._send({"kind": kind, "token": device_token, "environment": environment,
                           "pushType": "background", "sessionId": session_id, "sequence": sequence})

    def send_alert(self, kind: str, device_token: str, environment: str,
                   title: str, body: str, session_id: str, sequence: int):
        return self._send({"kind": kind, "token": device_token, "environment": environment,
                           "pushType": "alert", "sessionId": session_id, "sequence": sequence,
                           "title": title, "body": body})

    # ------------------------------------------------------------ Live Activity pushes
    #
    # Same three shapes as `apns.APNsClient` — see its docstring — just carried as JSON
    # to the relay instead of straight to Apple. No `kind`: these always mean the iOS app.

    def send_activity_start(self, push_to_start_token: str, environment: str,
                            attributes: dict, content_state: dict, timestamp: int,
                            alert=None):
        payload = {"pushType": "liveActivityStart", "token": push_to_start_token,
                   "environment": environment, "timestamp": timestamp,
                   "attributes": attributes, "contentState": content_state}
        if alert:
            payload["alert"] = alert
        return self._send(payload)

    def send_activity_update(self, activity_token: str, environment: str,
                             content_state: dict, timestamp: int, alert=None,
                             stale_date=None):
        payload = {"pushType": "liveActivityUpdate", "token": activity_token,
                   "environment": environment, "timestamp": timestamp,
                   "contentState": content_state}
        if alert:
            payload["alert"] = alert
        if stale_date is not None:
            payload["staleDate"] = stale_date
        return self._send(payload)

    def send_activity_end(self, activity_token: str, environment: str,
                          content_state: dict, timestamp: int, dismissal_date=None):
        payload = {"pushType": "liveActivityEnd", "token": activity_token,
                   "environment": environment, "timestamp": timestamp,
                   "contentState": content_state}
        if dismissal_date is not None:
            payload["dismissalDate"] = dismissal_date
        return self._send(payload)

    def _send(self, payload: dict):
        request = urllib.request.Request(
            "%s/v1/push" % self.config.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                    "Authorization": "Bearer %s" % self.config.api_key,
                    # `urllib`'s default "Python-urllib/x.y" User-Agent gets caught by
                    # Cloudflare's automated bot protection (a 403 with error code 1010)
                    # before the request ever reaches the Worker — a real client name
                    # sails through the same check curl or a browser would pass.
                    "User-Agent": "OverwatchQueueServer/1.0"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return _Response(response.status, response.read())
        except urllib.error.HTTPError as error:
            # The relay proxies Apple's own error status/body through unchanged — a 400
            # or 410 here means exactly what it would mean talking to Apple directly.
            return _Response(error.code, error.read())
        except urllib.error.URLError as error:
            self.log("Push relay unreachable: %s" % error)
            return None

    @staticmethod
    def token_is_invalid(response) -> bool:
        if response is None or response.status_code not in (400, 410):
            return False
        try:
            reason = json.loads(response.text).get("reason")
        except ValueError:
            return False
        return reason in ("BadDeviceToken", "Unregistered")


class _Response:
    """Just enough of `httpx.Response`'s shape for `token_is_invalid` to read, from
    either `apns.APNsClient` or this relay client."""

    def __init__(self, status_code: int, body: bytes):
        self.status_code = status_code
        self.text = body.decode("utf-8", "replace")
