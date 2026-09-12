#!/usr/bin/env python3
"""Starts the queue server and its control panel.

    pip install -r server/requirements.txt
    python3 server/run.py

PyQt6 is the only dependency, and only for the window — `--headless` runs the server
without it, printing the pairing code into the terminal instead.

Hero select reads the real screen where the optional vision extras are installed, which
also means it focuses Overwatch and drives the mouse. `--no-vision` turns that off and
leaves the panel driving the queue entirely by hand.

The queue itself is read the same way and by default: the banner at the top of the screen
says whether you are searching, in which mode, and for how long, and the server moves the
phase to match with nobody pressing anything. That half only ever looks — it never takes
the mouse — so it is on unless `--no-queue-vision` says otherwise.

Battle.net itself already knows whether you're queueing — Overwatch tells Blizzard's
presence service, which tells Battle.net — and that keeps working while Overwatch isn't
the foreground window, which is exactly the screen's blind spot. Once it's answered
once, it takes over deciding whether a queue is running, what mode it's in, and whether
it's just found a match; a role-select screen (which Battle.net also reports as "in
queue") holds that off until vision confirms it's actually closed, or falls back to
trusting Battle.net outright wherever vision can't see at all. The screen still keeps
what presence can't give at all — the queue timer, map vote, hero select — and its own
fast match-found check keeps running too, so whichever notices first wins. If Battle.net
closes or its debug port stops answering, it's relaunched automatically and the paired
phone/watch are told it disconnected. `--no-presence` turns all of this off; on by
default, it only ever reads (a websocket to Battle.net's own debug port, or a plain
read of Battle.net's own process memory — Overwatch itself is never touched).
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from owqserver import bnetpresence, qr                             # noqa: E402
from owqserver.catalog import Catalog                             # noqa: E402
from owqserver.controls import Controls                           # noqa: E402
from owqserver.pairing import Pairing, local_addresses            # noqa: E402
from owqserver.queueserver import QueueServer                     # noqa: E402

MISSING_QT = """PyQt6 isn't installed, so there's no window to show. Either:

    pip install -r server/requirements.txt        and run this again
    python3 server/run.py --headless              to drive it from the terminal
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, help="override the stored port")
    parser.add_argument("--headless", action="store_true",
                        help="no window; prints the pairing code in the terminal")
    parser.add_argument("--new-token", action="store_true",
                        help="generate a new pairing token, unpairing every device")
    parser.add_argument("--no-vision", action="store_true",
                        help="never read the game screen or move the mouse")
    parser.add_argument("--no-queue-vision", action="store_true",
                        help="don't watch the screen for a queue; drive it by hand")
    parser.add_argument("--no-presence", action="store_true",
                        help="don't ask Battle.net what you're doing")
    parser.add_argument("--presence-source", choices=("auto", "cdp", "memory", "off"),
                        default="auto",
                        help="how to read Battle.net's presence (default: try its debug "
                             "port, then fall back to reading its own process memory)")
    parser.add_argument("--battlenet-port", type=int,
                        default=bnetpresence.DEFAULT_CDP_PORT,
                        help="Battle.net's --remote-debugging-port, if you've enabled one "
                             "(default: %d)" % bnetpresence.DEFAULT_CDP_PORT)
    options = parser.parse_args()

    pairing = Pairing.load()
    if options.port:
        pairing.port = options.port
        pairing.save()
    if options.new_token:
        pairing.regenerate()
        print("New pairing token. Every device has to scan again.")

    server = QueueServer(pairing, Catalog(), vision_enabled=not options.no_vision,
                         queue_vision_enabled=not options.no_queue_vision,
                         presence_enabled=not options.no_presence,
                         presence_source=options.presence_source,
                         presence_port=options.battlenet_port)

    if options.headless:
        return run_headless(server)

    try:
        from owqserver.gui import run
    except ImportError:
        print(MISSING_QT)
        return 1
    return run(server)


def run_headless(server) -> int:
    """The server with no window: still pairs, still drives every screen in the app."""
    server.log = print
    server.start()
    # No panel to take the mode and role from, so the watcher gets a `Controls` of its
    # own with the defaults. The banner's colour still supplies the mode; only the role
    # is left at whatever `Controls` starts with, since nothing on screen names it.
    server.watch_queue(Controls(server))
    address = local_addresses()[0]
    url = server.pairing.url(address)

    print(qr.encode(url, ecl="M").to_text())
    print("\n%s\n" % url)
    print("Scan the code, or type the address into the app: %s:%d"
          % (address, server.pairing.port))
    print("Ctrl-C to stop.\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
