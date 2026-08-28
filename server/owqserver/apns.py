"""Talks to Apple Push Notification service, so a snapshot reaches a phone or watch that
isn't holding a WebSocket open — the screen is locked, the app is closed, or the watch is
out of range of both the phone and the PC.

Nothing here ever leaves the PC except the push itself: the Auth Key that authenticates
*to* Apple lives next to `pairing.json`, exactly the way the pairing token does, and a
client only ever hands over the one thing APNs needs from it — its own device token.
"""
from __future__ import annotations

import json
import os
import time

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".overwatch-queue")
CONFIG_PATH = os.path.join(CONFIG_DIR, "apns.json")

_HOSTS = {
    "production": "https://api.push.apple.com",
    "sandbox": "https://api.sandbox.push.apple.com",
}

# Apple accepts a token signed up to an hour ago; refresh well inside that so a clock
# that's a little off on either end never sends an expired one.
_TOKEN_LIFETIME_SECONDS = 50 * 60


class APNsConfig:
    """The one-time setup: a Key ID, a Team ID, the two bundle IDs, and the `.p8` itself.

    Loaded from `apns.json` plus `AuthKey_<KeyID>.p8` sitting next to it — both dropped in
    by hand after generating the key in the Apple Developer portal. See
    `server/README.md`.
    """

    def __init__(self, team_id: str, key_id: str, bundle_id_ios: str,
                 bundle_id_watch: str, private_key: str):
        self.team_id = team_id
        self.key_id = key_id
        self.bundle_id_ios = bundle_id_ios
        self.bundle_id_watch = bundle_id_watch
        self.private_key = private_key

    def bundle_id(self, kind: str) -> str:
        return self.bundle_id_ios if kind == "phone" else self.bundle_id_watch

    @classmethod
    def load(cls, path: str = CONFIG_PATH):
        """Returns `None` rather than raising — no key configured just means pushing is
        skipped, the same way an unpaired client just gets no snapshots."""
        try:
            with open(path) as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return None

        team_id = data.get("team_id")
        key_id = data.get("key_id")
        bundle_id_ios = data.get("bundle_id_ios")
        bundle_id_watch = data.get("bundle_id_watch")
        if not (team_id and key_id and bundle_id_ios and bundle_id_watch):
            return None

        key_path = os.path.join(os.path.dirname(path), "AuthKey_%s.p8" % key_id)
        try:
            with open(key_path) as handle:
                private_key = handle.read()
        except OSError:
            return None

        return cls(team_id, key_id, bundle_id_ios, bundle_id_watch, private_key)

    @classmethod
    def from_env(cls):
        """The same four fields plus the key itself, read from the environment instead of
        from files next to it — what a hosted relay runs on, where "drop a file in the
        home directory" isn't a thing. See `relay/README.md`."""
        team_id = os.environ.get("APNS_TEAM_ID")
        key_id = os.environ.get("APNS_KEY_ID")
        bundle_id_ios = os.environ.get("APNS_BUNDLE_ID_IOS")
        bundle_id_watch = os.environ.get("APNS_BUNDLE_ID_WATCH")
        private_key = os.environ.get("APNS_PRIVATE_KEY")
        key_path = os.environ.get("APNS_PRIVATE_KEY_PATH")

        if not (team_id and key_id and bundle_id_ios and bundle_id_watch):
            return None
        if not private_key and key_path:
            try:
                with open(key_path) as handle:
                    private_key = handle.read()
            except OSError:
                return None
        if not private_key:
            return None

        return cls(team_id, key_id, bundle_id_ios, bundle_id_watch, private_key)


class APNsClient:
    """Sends one push at a time over HTTP/2. Cheap to keep around: the auth token is
    cached and re-signed only every `_TOKEN_LIFETIME_SECONDS`, and the HTTP/2 connection
    is reused across pushes rather than reopened each time."""

    def __init__(self, config: APNsConfig, log=None):
        self.config = config
        self.log = log or (lambda message: None)
        self._auth_token = None
        self._auth_token_made_at = 0.0
        self._client = None

    def close(self):
        if self._client is not None:
            self._client.close()
            self._client = None

    # ------------------------------------------------------------ sending

    def send_background(self, kind: str, device_token: str, environment: str,
                        session_id: str, sequence: int):
        """A silent wake-up: no banner, no sound. Enough for the app to reconnect and
        pull a fresh snapshot on its own."""
        payload = {"aps": {"content-available": 1},
                   "sessionID": session_id, "sequence": sequence}
        return self._send(kind, self.config.bundle_id(kind), device_token, environment,
                          payload, push_type="background", priority="5")

    def send_alert(self, kind: str, device_token: str, environment: str,
                   title: str, body: str, session_id: str, sequence: int):
        """A real, visible notification — reserved for phases urgent enough that the
        player should be told even with the app fully closed."""
        payload = {"aps": {"alert": {"title": title, "body": body}, "sound": "default"},
                   "sessionID": session_id, "sequence": sequence}
        return self._send(kind, self.config.bundle_id(kind), device_token, environment,
                          payload, push_type="alert", priority="10")

    # ------------------------------------------------------------ Live Activity pushes
    #
    # A different beast from the two above: these target the Live Activity itself (its
    # own push-to-start or per-activity token, not a phone/watch device token), always
    # under the iOS bundle ID — the watch has no Live Activities — and always at
    # immediate priority, which Apple requires for this push type. See
    # `protocol.content_state` for the content-state shape these carry.

    def send_activity_start(self, push_to_start_token: str, environment: str,
                            attributes: dict, content_state: dict, timestamp: int,
                            alert=None):
        aps = {"timestamp": timestamp, "event": "start", "content-state": content_state,
               "attributes-type": "QueueActivityAttributes", "attributes": attributes}
        if alert:
            aps["alert"] = alert
        topic = "%s.push-type.liveactivity.start" % self.config.bundle_id_ios
        return self._send("activity-start", topic, push_to_start_token, environment,
                          {"aps": aps}, push_type="liveactivity", priority="10")

    def send_activity_update(self, activity_token: str, environment: str,
                             content_state: dict, timestamp: int, alert=None,
                             stale_date=None):
        aps = {"timestamp": timestamp, "event": "update", "content-state": content_state}
        if alert:
            aps["alert"] = alert
        if stale_date is not None:
            aps["stale-date"] = stale_date
        topic = "%s.push-type.liveactivity" % self.config.bundle_id_ios
        return self._send("activity-update", topic, activity_token, environment,
                          {"aps": aps}, push_type="liveactivity", priority="10")

    def send_activity_end(self, activity_token: str, environment: str,
                          content_state: dict, timestamp: int, dismissal_date=None):
        aps = {"timestamp": timestamp, "event": "end", "content-state": content_state}
        if dismissal_date is not None:
            aps["dismissal-date"] = dismissal_date
        topic = "%s.push-type.liveactivity" % self.config.bundle_id_ios
        return self._send("activity-end", topic, activity_token, environment,
                          {"aps": aps}, push_type="liveactivity", priority="10")

    def _send(self, label: str, topic: str, device_token: str, environment: str,
              payload: dict, push_type: str, priority: str):
        import httpx        # imported lazily so a server without the extra installed
                            # can still run everything that doesn't touch APNs

        if self._client is None:
            self._client = httpx.Client(http2=True, timeout=10.0)

        url = "%s/3/device/%s" % (_HOSTS[environment], device_token)
        headers = {
            "authorization": "bearer %s" % self._signed_auth_token(),
            "apns-topic": topic,
            "apns-push-type": push_type,
            "apns-priority": priority,
        }
        try:
            response = self._client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as error:
            self.log("APNs push to %s failed: %s" % (label, error))
            return None

        if response.status_code >= 400:
            self.log("APNs push to %s refused: %s %s"
                     % (label, response.status_code, response.text.strip()))
        return response

    @staticmethod
    def token_is_invalid(response) -> bool:
        """A 400 `BadDeviceToken` or 410 `Unregistered` means Apple will never accept
        this token again — the client needs to register a new one."""
        if response is None or response.status_code not in (400, 410):
            return False
        try:
            reason = response.json().get("reason")
        except ValueError:
            return False
        return reason in ("BadDeviceToken", "Unregistered")

    # ------------------------------------------------------------ auth

    def _signed_auth_token(self) -> str:
        import jwt          # imported lazily, same reasoning as httpx above

        now = time.time()
        if self._auth_token is None or now - self._auth_token_made_at > _TOKEN_LIFETIME_SECONDS:
            self._auth_token = jwt.encode(
                {"iss": self.config.team_id, "iat": int(now)},
                self.config.private_key,
                algorithm="ES256",
                headers={"kid": self.config.key_id},
            )
            self._auth_token_made_at = now
        return self._auth_token
