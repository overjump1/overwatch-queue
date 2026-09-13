"""Sends Live Activity pushes through Firebase Cloud Messaging instead of straight at
Apple — by way of the push relay in `relay/`, which is the only thing holding the
Firebase credential.

Why Firebase at all, when a push could go straight to APNs and this ends up at the same
place? Because measured against a real device, the two do not behave the same:

- A **push-to-start** posted straight to APNs is accepted with a 200 and an `apns-id`,
  and then, over dozens of attempts across both sandbox and production, a fresh token,
  and every documented precondition satisfied, simply never arrives. The identical
  payload routed through Firebase arrives.
- An **alert on a Live Activity push** posted straight to APNs never carries its sound.
  `usernotificationsd` logs `hasSound: 0` against it no matter where the `sound` key is
  put — inside `alert`, beside it, as a string, as a dictionary, or left out entirely.
  Routed through Firebase, the same alert sounds and buzzes.

Neither difference is documented anywhere; both are reproducible. So Firebase is the
server's only push transport, and Live Activity pushes are the only thing it sends — no
plain notification goes to the phone or the watch at all.

Why the relay rather than calling Firebase from here: sending needs a service account,
and a service account is scoped to the whole Firebase project, not to one household.
Any copy of it on a customer's PC could push to every user of the app. The relay keeps
that credential in one place, and this server needs nothing configured at all.

Addressing takes two tokens at once, which is easy to trip over: `deviceToken` is the
*device's* FCM registration token (see `fcmtokens.py`), while `liveActivityToken` is the
ActivityKit token for the specific card — push-to-start for `start`, per-activity for
`update`/`end`. Both are required; neither substitutes for the other.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

RELAY_URL = "https://overwatch-queue-push-relay.tomerady.workers.dev"
# Points the server at another relay — a `wrangler dev` one, say. Set but empty, it turns
# pushing off entirely, which is how the test suite keeps a bare `QueueServer` from ever
# reaching the real one.
RELAY_URL_ENV = "OWQ_PUSH_RELAY_URL"


def relay_url():
    """The relay to push through, or `None` when pushing has been turned off."""
    url = os.environ.get(RELAY_URL_ENV, RELAY_URL)
    return url.rstrip("/") or None


class FCMClient:
    """Sends one push at a time to the relay over HTTPS."""

    def __init__(self, url: str, log=None):
        self.url = url.rstrip("/")
        self.log = log or (lambda message: None)

    def close(self):
        pass            # nothing held open; `urllib` opens a connection per call

    # ------------------------------------------------------------ sending

    def send_activity_start(self, push_to_start_token: str, attributes: dict,
                            content_state: dict, timestamp: int, alert=None):
        aps = {"timestamp": timestamp, "event": "start", "content-state": content_state,
               "attributes-type": "QueueActivityAttributes", "attributes": attributes}
        return self._send("activity-start", push_to_start_token, aps, alert)

    def send_activity_update(self, activity_token: str, content_state: dict,
                             timestamp: int, alert=None, stale_date=None):
        aps = {"timestamp": timestamp, "event": "update", "content-state": content_state}
        if stale_date is not None:
            aps["stale-date"] = stale_date
        return self._send("activity-update", activity_token, aps, alert)

    def send_activity_end(self, activity_token: str, content_state: dict, timestamp: int,
                          alert=None, dismissal_date=None):
        aps = {"timestamp": timestamp, "event": "end", "content-state": content_state}
        if dismissal_date is not None:
            aps["dismissal-date"] = dismissal_date
        return self._send("activity-end", activity_token, aps, alert)

    def _send(self, label: str, live_activity_token: str, aps: dict, alert):
        device_token = self.device_token
        if not device_token or not live_activity_token:
            return None
        if alert:
            aps["alert"] = alert
            # Lets the alert through a Focus that would otherwise hold it back — the app
            # has the time-sensitive entitlement for exactly this. A silent push has
            # nothing to break through with, so it never carries one.
            aps["interruption-level"] = "time-sensitive"

        # Every push is logged, loud or silent, so the panel shows exactly what the phone
        # was sent — a missing line is a push that never went, not one that went quietly.
        phase = aps["content-state"].get("phase")
        phase = phase.get("type", "?") if isinstance(phase, dict) else phase
        what = "%s %s (%s)" % (label, phase, "loud" if alert else "silent")
        body = {"deviceToken": device_token, "liveActivityToken": live_activity_token,
                "aps": aps}
        try:
            response = self._post("%s/v1/push" % self.url, body)
        except (urllib.error.URLError, OSError) as error:
            self.log("Push %s failed: %s" % (what, error))
            return None

        if response.status_code >= 400:
            self.log("Push %s refused: %s %s"
                     % (what, response.status_code, response.text.strip()))
        else:
            self.log("Pushed %s" % what)
        return response

    def _post(self, url: str, body: dict):
        request = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json",
                     # `urllib`'s default "Python-urllib/x.y" User-Agent gets caught by
                     # Cloudflare's automated bot protection (a 403 with error code 1010)
                     # before the request ever reaches the Worker.
                     "User-Agent": "OverwatchQueueServer/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return _Response(response.status, response.read())
        except urllib.error.HTTPError as error:
            # The relay proxies Firebase's own status and body through unchanged, so a
            # refusal here reads exactly as it would talking to Firebase directly.
            return _Response(error.code, error.read())

    # The device's FCM registration token, set by `queueserver` as it registers. Kept as a
    # plain attribute rather than passed per call, so the activity methods take only the
    # ActivityKit token they're addressing.
    device_token = None

    @staticmethod
    def token_is_invalid(response) -> bool:
        """Firebase's own way of saying a registration token is dead: `UNREGISTERED` (the
        app was removed) or `INVALID_ARGUMENT` against a malformed one. The caller treats
        it the same way it treats Apple's `BadDeviceToken`."""
        if response is None or response.status_code not in (400, 404):
            return False
        try:
            error = response.json().get("error", {})
        except (ValueError, AttributeError):
            return False
        # The relay's own 400s carry `error` as a plain string — a request it refused to
        # forward says nothing about whether the phone's token is still good.
        if not isinstance(error, dict):
            return False
        return error.get("status") in ("UNREGISTERED", "NOT_FOUND", "INVALID_ARGUMENT")


class _Response:
    """Just the parts of an HTTP response the server reads back."""

    def __init__(self, status_code: int, body: bytes):
        self.status_code = status_code
        self.text = body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.text)
