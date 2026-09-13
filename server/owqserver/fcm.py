"""Sends Live Activity pushes through Firebase Cloud Messaging instead of straight at
Apple.

Why the extra hop, when `apns.py` already talks to APNs directly and this ends up at the
same place? Because measured against a real device, the two do not behave the same:

- A **push-to-start** posted straight to APNs is accepted with a 200 and an `apns-id`,
  and then, over dozens of attempts across both sandbox and production, a fresh token,
  and every documented precondition satisfied, simply never arrives. The identical
  payload routed through Firebase arrives.
- An **alert on a Live Activity push** posted straight to APNs never carries its sound.
  `usernotificationsd` logs `hasSound: 0` against it no matter where the `sound` key is
  put — inside `alert`, beside it, as a string, as a dictionary, or left out entirely.
  Routed through Firebase, the same alert sounds and buzzes.

Neither difference is documented anywhere; both are reproducible. So Firebase is the
transport for anything Live-Activity-shaped, and `pushrelay.py` stays the transport for
plain notifications, which it has always delivered reliably.

Addressing takes two tokens at once, which is easy to trip over: `message.token` is the
*device's* FCM registration token (see `fcmtokens.py`), while `apns.live_activity_token`
is the ActivityKit token for the specific card — push-to-start for `start`, per-activity
for `update`/`end`. Both are required; neither substitutes for the other.
"""
from __future__ import annotations

import json
import os

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".overwatch-queue")
CONFIG_PATH = os.path.join(CONFIG_DIR, "fcm-service-account.json")

_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
_SEND_URL = "https://fcm.googleapis.com/v1/projects/%s/messages:send"

# Apple requires immediate priority for every Live Activity push.
_LIVE_ACTIVITY_HEADERS = {"apns-priority": "10", "apns-push-type": "liveactivity"}


class FCMConfig:
    """The service-account JSON downloaded from the Firebase console, plus the project it
    belongs to. Dropped in by hand next to `pairing.json`, exactly the way `apns.json` and
    the `.p8` are — and just as deliberately never committed."""

    def __init__(self, project_id: str, service_account_path: str):
        self.project_id = project_id
        self.service_account_path = service_account_path

    @classmethod
    def load(cls, path: str = CONFIG_PATH):
        """`None` when no service account is configured — Live Activity pushes are then
        skipped, the same way they are when no APNs key is."""
        try:
            with open(path) as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return None
        project_id = data.get("project_id")
        if not project_id:
            return None
        return cls(project_id, path)


class FCMClient:
    """Sends one push at a time over HTTPS. The OAuth access token is minted from the
    service account and cached by `google-auth`, which refreshes it as it expires."""

    def __init__(self, config: FCMConfig, log=None):
        self.config = config
        self.log = log or (lambda message: None)
        self._credentials = None

    def close(self):
        pass            # nothing held open; `requests` opens a connection per call

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
        import requests       # imported lazily so a server without the extras installed
                              # can still run everything that doesn't touch Firebase

        device_token = self.device_token
        if not device_token or not live_activity_token:
            return None
        if alert:
            aps["alert"] = alert

        body = {"message": {"token": device_token,
                            "apns": {"live_activity_token": live_activity_token,
                                     "headers": dict(_LIVE_ACTIVITY_HEADERS),
                                     "payload": {"aps": aps}}}}
        try:
            response = requests.post(
                _SEND_URL % self.config.project_id,
                headers={"Authorization": "Bearer %s" % self._access_token(),
                         "Content-Type": "application/json"},
                data=json.dumps(body), timeout=10)
        except Exception as error:              # noqa: BLE001 - any transport failure
            self.log("FCM push to %s failed: %s" % (label, error))
            return None

        if response.status_code >= 400:
            self.log("FCM push to %s refused: %s %s"
                     % (label, response.status_code, response.text.strip()))
        return response

    # The device's FCM registration token, set by `queueserver` as it registers. Kept as a
    # plain attribute rather than passed per call so this stays interface-compatible with
    # `apns.py`, whose activity methods take only ActivityKit tokens.
    device_token = None

    @staticmethod
    def token_is_invalid(response) -> bool:
        """Firebase's own way of saying a registration token is dead: `UNREGISTERED` (the
        app was removed) or `INVALID_ARGUMENT` against a malformed one. The caller treats
        it the same way it treats Apple's `BadDeviceToken`."""
        if response is None or response.status_code not in (400, 404):
            return False
        try:
            status = response.json().get("error", {}).get("status")
        except ValueError:
            return False
        return status in ("UNREGISTERED", "NOT_FOUND", "INVALID_ARGUMENT")

    # ------------------------------------------------------------ auth

    def _access_token(self) -> str:
        import google.auth.transport.requests      # imported lazily, as above
        from google.oauth2 import service_account

        if self._credentials is None:
            self._credentials = service_account.Credentials.from_service_account_file(
                self.config.service_account_path, scopes=[_SCOPE])
        if not self._credentials.valid:
            self._credentials.refresh(google.auth.transport.requests.Request())
        return self._credentials.token
