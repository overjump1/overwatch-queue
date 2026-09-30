"""Which QueueFox this is: the real app, or QueueFox Dev.

The two are separate apps that sit side by side. Each has its own pairing, its own worker, its own
releases to update from and its own QR link, which only the matching phone app answers to -- so
nothing tested on dev can land on the real app, or the other way round.

Everything is QueueFox Dev -- running from source, the dev branch's builds, pull requests -- except
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
    # What this app was called before it was QueueFox, and the worker it paired through then. An
    # install upgraded from that one keeps its pairing, and keeps the phones paired back then
    # updated through that worker until the pairing is reset (app.adopt_old_data_dir,
    # relay.Mirrored). The phone apps from then only ever talk to that worker.
    old_name: str
    old_worker_url: str

    @property
    def data_dir(self):
        return os.path.join(_APPDATA, self.name)

    @property
    def old_data_dir(self):
        return os.path.join(_APPDATA, self.old_name)

    @property
    def pairing_file(self):
        return os.path.join(self.data_dir, "pairing.json")


REAL = Channel(
    name="QueueFox",
    worker_url="https://queuefox-push-relay.tomerady.workers.dev",
    release_api="https://api.github.com/repos/overjump1/queuefox/releases/latest",
    pair_scheme="queuefox",
    old_name="OverQueue",
    old_worker_url="https://overwatch-queue-push-relay.tomerady.workers.dev",
)
DEV = Channel(
    name="QueueFox Dev",
    worker_url="https://queuefox-push-relay-dev.tomerady.workers.dev",
    release_api="https://api.github.com/repos/overjump1/queuefox/releases/tags/dev-latest",
    pair_scheme="queuefox-dev",
    old_name="OverQueue Dev",
    old_worker_url="https://overwatch-queue-push-relay-dev.tomerady.workers.dev",
)

CURRENT = DEV if version.DEV else REAL
# Points this copy at another worker, e.g. `npx wrangler dev` on this machine.
WORKER_URL = (os.environ.get("QUEUEFOX_WORKER_URL") or CURRENT.worker_url).rstrip("/")
