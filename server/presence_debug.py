#!/usr/bin/env python3
"""Asks Battle.net what you're doing and says what it heard, one line per read.

    python3 server/presence_debug.py                    # auto: CDP, then memory
    python3 server/presence_debug.py --source cdp        # force the debug-port path
    python3 server/presence_debug.py --source memory     # force the process-memory path
    python3 server/presence_debug.py --raw               # print every raw record read

`queuepresence`'s vocabulary is small and was built from one account — this is how a new
string gets added to it: queue up with this running and read off the raw text next to
`?` for whatever it didn't recognise. A mode that reads as `?` is the string to paste
into `queuepresence.MODE_WORDS`; a whole reading that reads as `unknown` is the string to
paste into `ACTIVITY_SUFFIXES` or `STANDALONE_ACTIVITIES`.

Never presses anything and never touches Overwatch — the CDP path is a websocket to
Battle.net's own embedded browser, the memory path a plain `ReadProcessMemory` on
Battle.net's own process. It doesn't need the server: it drives `PresenceWatcher.poll`
directly, so this is the real state machine's own doorway, not a second implementation
that might disagree with it.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from owqserver import bnetpresence, queuepresence, queuewatch          # noqa: E402

MISSING = """This only runs on Windows, where Battle.net itself runs.
"""


def line(reading: "queuepresence.PresenceReading", raw: bool) -> str:
    detail = "%r" % reading.text if reading.text else "-"
    described = ("%-11s %-11s" % (reading.state, reading.mode or "-")
                if reading.known else "%-23s" % "unknown")
    tag = "" if reading.mode or not reading.known or reading.text.strip() in (
        "", "In Menus", "Practice Range", "Tutorial") else "  ?"
    out = "%s  %-24s %-8s %s%s" % (
        time.strftime("%H:%M:%S"), described, reading.battletag or "-", detail, tag)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", choices=("auto", "cdp", "memory"), default="auto",
                        help="which transport to use (default: try the debug port, "
                             "then fall back to process memory)")
    parser.add_argument("--port", type=int, default=bnetpresence.DEFAULT_CDP_PORT,
                        help="Battle.net's --remote-debugging-port (default: %d)"
                             % bnetpresence.DEFAULT_CDP_PORT)
    parser.add_argument("--battletag", help="override the auto-detected account")
    parser.add_argument("--interval", type=float,
                        help="seconds between reads (default: the source's own cadence)")
    parser.add_argument("--seconds", type=float,
                        help="stop after this long instead of on Ctrl-C")
    parser.add_argument("--raw", action="store_true",
                        help="print every read, not just the ones that changed")
    options = parser.parse_args()

    if not bnetpresence.PRESENCE_AVAILABLE:
        print(MISSING)
        return 1

    identity = None
    if options.battletag:
        identity = queuepresence.Identity(options.battletag)
    source = bnetpresence.open_source(prefer=options.source, port=options.port,
                                      identity=identity, log=lambda m: print("   %s" % m))
    if isinstance(source, bnetpresence.NullSource):
        print("Couldn't reach Battle.net over %s. Is it running%s?"
              % (options.source, "" if options.source != "cdp" else
                 " with --remote-debugging-port=%d" % options.port))
        return 1
    print("Reading via %s. Ctrl-C to stop.\n" % type(source).__name__)

    watcher = queuewatch.PresenceWatcher(source, lambda reading, when: None,
                                         log=lambda m: print("   %s" % m))
    interval = options.interval if options.interval is not None else source.poll_seconds

    started, last_text = time.monotonic(), None
    try:
        while options.seconds is None or time.monotonic() - started < options.seconds:
            reading = watcher.poll()
            if options.raw or reading.text != last_text:
                print(line(reading, options.raw))
                last_text = reading.text
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        source.close()
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
