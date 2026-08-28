"""Where a phone or watch's APNs device token is remembered between runs.

One file, `~/.overwatch-queue/push_tokens.json`, keyed by `kind` (`phone`/`watch`) —
mirrors `pairing.json`'s "one small JSON file, best effort on disk" shape. There is at
most one phone and one watch per pairing (see `docs/PROTOCOL.md`), so a token simply
overwrites whatever that kind last registered.
"""
from __future__ import annotations

import json
import os
import stat

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".overwatch-queue")
CONFIG_PATH = os.path.join(CONFIG_DIR, "push_tokens.json")

KINDS = ("phone", "watch")
ENVIRONMENTS = ("sandbox", "production")


class PushTokens:
    """The device tokens currently registered, one per client kind."""

    def __init__(self, path: str = CONFIG_PATH):
        self.path = path
        self._tokens = {}          # kind -> {"token": str, "environment": str}
        self._load()

    def _load(self):
        try:
            with open(self.path) as handle:
                stored = json.load(handle)
        except (OSError, ValueError):
            return
        for kind in KINDS:
            entry = stored.get(kind)
            if isinstance(entry, dict) and entry.get("token") and \
                    entry.get("environment") in ENVIRONMENTS:
                self._tokens[kind] = {"token": str(entry["token"]),
                                      "environment": entry["environment"]}

    def _save(self):
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.path, "w") as handle:
            json.dump(self._tokens, handle, indent=2)
        try:
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)     # owner only
        except OSError:
            pass                                                  # best effort on Windows

    def register(self, kind: str, token: str, environment: str):
        if kind not in KINDS or not token or environment not in ENVIRONMENTS:
            return
        self._tokens[kind] = {"token": token, "environment": environment}
        self._save()

    def forget(self, kind: str):
        if self._tokens.pop(kind, None) is not None:
            self._save()

    def clear(self):
        """Every stored token is dropped — the pairing-recovery path calls this."""
        if self._tokens:
            self._tokens = {}
            self._save()

    def get(self, kind: str):
        """`(token, environment)` for `kind`, or `None` if it never registered one."""
        entry = self._tokens.get(kind)
        if entry is None:
            return None
        return entry["token"], entry["environment"]

    def items(self):
        """`(kind, token, environment)` for every kind with a registered token."""
        for kind, entry in self._tokens.items():
            yield kind, entry["token"], entry["environment"]
