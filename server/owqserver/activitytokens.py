"""The two Live Activity push tokens, kept separately from `pushtokens.py`'s
phone/watch device tokens because they mean something different and don't fit the same
shape:

- The **push-to-start** token is app-level, like a device token — one per install — and
  lets the PC create a Live Activity via push even if the app was never opened this
  launch, before any queue has started.
- The **update** token belongs to one specific *instance* of a running Live Activity, so
  it's tagged with the session it was issued for; a session that's ended or been replaced
  makes the old one useless even if it's technically still in the file.
"""
from __future__ import annotations

import json
import os
import stat

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".overwatch-queue")
CONFIG_PATH = os.path.join(CONFIG_DIR, "activity_tokens.json")

ENVIRONMENTS = ("sandbox", "production")


class ActivityTokens:
    def __init__(self, path: str = CONFIG_PATH):
        self.path = path
        self._start = None                     # {"token", "environment"}
        self._update = None                     # {"token", "environment", "session_id"}
        self._load()

    def _load(self):
        try:
            with open(self.path) as handle:
                stored = json.load(handle)
        except (OSError, ValueError):
            return
        start = stored.get("start")
        if isinstance(start, dict) and start.get("token") and start.get("environment") in ENVIRONMENTS:
            self._start = {"token": str(start["token"]), "environment": start["environment"]}
        update = stored.get("update")
        if isinstance(update, dict) and update.get("token") and update.get("environment") in ENVIRONMENTS \
                and update.get("session_id"):
            self._update = {"token": str(update["token"]), "environment": update["environment"],
                            "session_id": str(update["session_id"])}

    def _save(self):
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.path, "w") as handle:
            json.dump({"start": self._start, "update": self._update}, handle, indent=2)
        try:
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    # ---------------------------------------------------------------- push-to-start

    def register_start(self, token: str, environment: str):
        if not token or environment not in ENVIRONMENTS:
            return
        start = {"token": token, "environment": environment}
        # Unchanged means nothing to write. Worth the branch for one reason: it makes this
        # file's mtime mean "the last time the app actually registered a *new* token",
        # which is the one signal that tells you a reinstall has invalidated the old one
        # — a push-to-start against a stale token is accepted by Apple and simply never
        # delivered, with nothing on this side to show for it.
        if self._start == start:
            return
        self._start = start
        self._save()

    def start_token(self):
        """`(token, environment)`, or `None` if the app has never registered one."""
        if self._start is None:
            return None
        return self._start["token"], self._start["environment"]

    def forget_start(self):
        if self._start is not None:
            self._start = None
            self._save()

    # ---------------------------------------------------------------- per-activity update

    def register_update(self, session_id: str, token: str, environment: str):
        if not token or environment not in ENVIRONMENTS or not session_id:
            return
        update = {"token": token, "environment": environment, "session_id": session_id}
        if self._update == update:                     # same reasoning as `register_start`
            return
        self._update = update
        self._save()

    def update_token(self, session_id: str):
        """`(token, environment)` for `session_id` specifically — `None` if this session
        never registered one (including a stale token left over from a previous
        session), which is what tells the caller to fall back to push-to-start instead."""
        if self._update is None or self._update["session_id"] != session_id:
            return None
        return self._update["token"], self._update["environment"]

    def forget_update(self):
        if self._update is not None:
            self._update = None
            self._save()

    # ---------------------------------------------------------------- recovery

    def clear(self):
        """Both tokens dropped — the pairing-recovery path calls this, same as
        `PushTokens.clear()`."""
        if self._start is not None or self._update is not None:
            self._start = None
            self._update = None
            self._save()
