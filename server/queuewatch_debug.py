#!/usr/bin/env python3
"""Watches the queue banner and says what it sees, one line per frame.

    python3 server/queuewatch_debug.py                 # live, off the real screen
    python3 server/queuewatch_debug.py --dump shots/   # ...and save annotated frames
    python3 server/queuewatch_debug.py --image cap.png # ...or re-run one saved frame
    python3 server/queuewatch_debug.py --all           # show the boxes it turned down

The constants in `queuevision` were derived from downscaled screenshots, which is enough
to build against and not enough to trust. This is how they get measured on a real screen:
queue up with `--all` running and read off the aspect, the hue and the reason each box
was rejected. A mode whose colour isn't in `queuemodes.json` shows as `?` next to the hue
it actually is — which is the number to paste into that file.

It never presses anything, and it doesn't need the server: it drives `QueueWatcher.poll`
straight, so the state machine, the grace period and the timer reading are all the real
ones rather than a second implementation that might disagree with them.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from owqserver import queuevision, queuewatch                     # noqa: E402

MISSING = """The vision extras aren't installed, so there's nothing to look with:

    pip install -r server/requirements.txt
"""


def describe(found: dict, hues, tolerance) -> str:
    mode = "-"
    if found["hue"] is not None:
        mode = queuevision.match_mode(found["hue"], hues, tolerance) or "?"
    return ("    %-11s %-18s aspect %5.1f  fill %.2f  hue %-5s %-11s cover %-5s "
            "spread %-5s  %s" % (
                found["kind"] or "-", "%d,%d %dx%d" % found["box"], found["aspect"],
                found["fill"],
                "-" if found["hue"] is None else "%.0f" % found["hue"], mode,
                "-" if found["coverage"] is None else "%.2f" % found["coverage"],
                "-" if found["spread"] is None else "%.1f" % found["spread"],
                "kept" if found["reason"] is None else "dropped: " + found["reason"]))


def annotate(strip, measured):
    import cv2
    canvas = strip.copy()
    for found in measured:
        x, y, w, h = found["box"]
        kept = found["reason"] is None
        cv2.rectangle(canvas, (x, y), (x + w, y + h),
                      (0, 220, 0) if kept else (0, 0, 220), 2)
        cv2.putText(canvas, found["reason"] or found["kind"] or "?", (x, max(12, y - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return canvas


def line(seen: queuewatch.QueueObservation) -> str:
    return ("%s  %-18s %-12s %-13s %6s waited (%s)  conf %.2f"
            % (time.strftime("%H:%M:%S"), seen.state, seen.kind or "-",
               seen.mode or "mode not measured",
               "%d:%02d" % (int(seen.queue_elapsed) // 60, int(seen.queue_elapsed) % 60),
               seen.source, seen.confidence))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--image", help="read one saved strip instead of the screen")
    parser.add_argument("--dump", metavar="DIR",
                        help="write an annotated strip per frame into DIR")
    parser.add_argument("--all", action="store_true",
                        help="list every box considered, not just the banner")
    parser.add_argument("--interval", type=float, default=0.25,
                        help="seconds between looks (default 0.25)")
    parser.add_argument("--seconds", type=float,
                        help="stop after this long instead of on Ctrl-C")
    options = parser.parse_args()

    if not queuevision.QUEUE_VISION_AVAILABLE:
        print(MISSING)
        return 1

    import cv2
    modes = queuevision.load_modes()
    if options.dump:
        os.makedirs(options.dump, exist_ok=True)

    if options.image:
        strip = cv2.imread(options.image, cv2.IMREAD_COLOR)
        if strip is None:
            print("Couldn't read %s" % options.image)
            return 1
        return show_one(strip, modes, options)

    return watch(modes, options)


def show_one(strip, modes, options) -> int:
    """One saved frame, in as much detail as there is."""
    measured = queuevision.measure(strip)
    print("%d box%s considered in a %dx%d strip"
          % (len(measured), "" if len(measured) == 1 else "es", strip.shape[1],
             strip.shape[0]))
    for found in sorted(measured, key=lambda f: f["reason"] is not None):
        if options.all or found["reason"] is None:
            print(describe(found, *modes))

    hit = queuevision.find_banner(strip, modes=modes)
    print("\n-> %s" % (hit or "no banner"))
    if hit is not None:
        from owqserver import queuedigits
        reader = queuedigits.DigitReader()
        patch = hit.timer_patch(strip)
        glyphs = queuedigits.segment(patch)
        print("   timer crop %s, %s"
              % (None if patch is None else "%dx%d" % (patch.shape[1], patch.shape[0]),
                 "no readable glyphs" if glyphs is None else
                 "%d + %d glyphs" % (len(glyphs.minutes), len(glyphs.seconds))))
        print("   digits %s" % ("learned" if reader.ready else
                                "not learned yet — queue for a minute with this running"))
    if options.dump:
        import cv2
        path = os.path.join(options.dump, "annotated.png")
        cv2.imwrite(path, annotate(strip, measured))
        print("   wrote %s" % path)
    return 0


def watch(modes, options) -> int:
    """The live loop, driving the real watcher one frame at a time."""
    watcher = queuewatch.QueueWatcher(_Bare(), log=lambda message: print("   %s" % message))
    print("Watching. Ctrl-C to stop.\n")
    started, frame, last = time.monotonic(), 0, None
    try:
        while options.seconds is None or time.monotonic() - started < options.seconds:
            seen = watcher.poll()
            # Every frame while something is happening, but only on a change while
            # nothing is — otherwise an idle screen scrolls the interesting part away.
            if seen.in_queue or last is None or seen.state != last:
                print(line(seen))
            last = seen.state

            if options.all or options.dump:
                strip, screen_w, screen_h = queuevision.capture_strip()
                measured = queuevision.measure(strip, screen_w, screen_h)
                if options.all:
                    for found in measured:
                        print(describe(found, *modes))
                if options.dump:
                    import cv2
                    frame += 1
                    cv2.imwrite(os.path.join(options.dump, "frame-%04d.png" % frame),
                                annotate(strip, measured))
            time.sleep(options.interval)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


class _Bare:
    """Just enough of `Controls` for the watcher to hold on to.

    This tool calls `poll` rather than running the watcher's loop, so nothing here is
    ever reached — `poll` looks at the screen and folds the answer into the tracker, and
    it is `_act` that talks to a server. The session below is a real one anyway, so that
    stays true by construction rather than by nobody having tried it.
    """

    class _Server:
        def __init__(self):
            from owqserver import protocol
            self.session = protocol.QueueSession()

        def apply(self, phase):
            return self.session.advance(phase)

        def shift_start(self, seconds):
            return self.session.shift_start(seconds)

    def __init__(self):
        self.server = self._Server()
        self.mode = "quickPlay"
        self.role = "flex"
        self.effective_role = "flex"

    def start_queue(self):
        from owqserver import protocol
        self.server.session.reset()
        return self.server.apply(
            protocol.searching(self.mode, self.effective_role, protocol.now()))


if __name__ == "__main__":
    sys.exit(main() or 0)
