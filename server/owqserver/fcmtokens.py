"""Where the phone's Firebase Cloud Messaging registration token is remembered.

Same "one small JSON file, best effort on disk" shape as `pushtokens.py`, and kept apart
from it for the same reason `activitytokens.py` is: this token means something different.
An APNs device token addresses a device from *our* side of Apple's door; an FCM token
addresses it from Firebase's, and carries no sandbox/production distinction — Firebase
holds both Auth Keys and picks per its own app configuration.

There is one phone per pairing, so a new token simply replaces the old one.
"""
from __future__ import annotations

import json
import os
import stat

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".overwatch-queue")
CONFIG_PATH = os.path.join(CONFIG_DIR, "fcm_tokens.json")


class FCMTokens:
    def __init__(self, path: str = CONFIG_PATH):
        self.path = path
        self._token = None
        self._load()

    def _load(self):
        try:
            with open(self.path) as handle:
                stored = json.load(handle)
        except (OSError, ValueError):
            return
        token = stored.get("phone")
        if token:
            self._token = str(token)

    def _save(self):
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.path, "w") as handle:
            json.dump({"phone": self._token}, handle, indent=2)
        try:
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)     # owner only
        except OSError:
            pass                                                  # best effort on Windows

    def register(self, token: str):
        if not token or token == self._token:
            return
        self._token = token
        self._save()

    def forget(self):
        if self._token is not None:
            self._token = None
            self._save()

    def get(self):
        """The phone's FCM token, or `None` if it has never registered one."""
        return self._token
