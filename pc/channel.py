"""Which OverQueue this is: the real app, or OverQueue Dev.

The two are separate apps that sit side by side. Each has its own pairing, its own worker, its own
releases to update from and its own QR link, which only the matching phone app answers to -- so
nothing tested on dev can land on the real app, or the other way round.

Everything is OverQueue Dev -- running from source, the dev branch's builds, pull requests -- except
main's release, which CI stamps as the real app in version.py. So working on the app never touches
the real app's pairing.
"""
from __future__ import annotations

import os
from typing import NamedTuple

import version

_APPDATA = os.environ.get("APPDATA") or os.path.expanduser("~")


class Channel(NamedTuple):
    name: str
    worker_url: str
    release_api: str
    # The QR code's link. Each phone app claims only its own, so the camera opens the right one.
    pair_scheme: str

    @property
    def data_dir(self):
        return os.path.join(_APPDATA, self.name)

    @property
    def pairing_file(self):
        return os.path.join(self.data_dir, "pairing.json")


REAL = Channel(
    name="OverQueue",
    worker_url="https://overwatch-queue-push-relay.tomerady.workers.dev",
    release_api="https://api.github.com/repos/overjump1/overwatch-queue/releases/latest",
    pair_scheme="overqueue",
)
DEV = Channel(
    name="OverQueue Dev",
    worker_url="https://overwatch-queue-push-relay-dev.tomerady.workers.dev",
    release_api="https://api.github.com/repos/overjump1/overwatch-queue/releases/tags/dev-latest",
    pair_scheme="overqueue-dev",
)

CURRENT = DEV if version.DEV else REAL
# Points this copy at another worker, e.g. `npx wrangler dev` on this machine.
WORKER_URL = (os.environ.get("OVERQUEUE_WORKER_URL") or CURRENT.worker_url).rstrip("/")
