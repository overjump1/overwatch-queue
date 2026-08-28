#!/usr/bin/env python3
"""Starts the queue server and its control panel.

    pip install -r server/requirements.txt
    python3 server/run.py

PyQt6 is the only dependency, and only for the window — `--headless` runs the server
without it, printing the pairing code into the terminal instead.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from owqserver import qr                                          # noqa: E402
from owqserver.catalog import Catalog                             # noqa: E402
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
    options = parser.parse_args()

    pairing = Pairing.load()
    if options.port:
        pairing.port = options.port
        pairing.save()
    if options.new_token:
        pairing.regenerate()
        print("New pairing token. Every device has to scan again.")

    server = QueueServer(pairing, Catalog())

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
