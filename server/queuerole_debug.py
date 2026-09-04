#!/usr/bin/env python3
"""Looks at the Select a Role screen once and says what it found.

    python3 server/queuerole_debug.py                  # one look at the real screen
    python3 server/queuerole_debug.py --dump shot.png   # ...and save an annotated frame
    python3 server/queuerole_debug.py --image cap.png   # ...or re-run one saved frame
    python3 server/queuerole_debug.py --loop            # keep looking until Ctrl-C

The constants in `queueroles` started from two screenshots and were then corrected
against one real recording — one capture, one resolution, one UI scale, not a guarantee.
This is how they get checked against another: bring up Select a Role, run this, and read
off where each icon actually landed, how confident the match was, and whether the row's
own geometry (`top`, `spacing`) looks sane. A hue that isn't in `queuemodes.json` prints
as `?` next to the number to add there, same as `queuewatch_debug.py`.

It never presses anything.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from owqserver import queueroles, queuevision                     # noqa: E402

MISSING = """The vision extras aren't installed, so there's nothing to look with:

    pip install -r server/requirements.txt
"""


def describe(bgr, role, hit, geometry) -> str:
    checked = queueroles.card_checked(bgr, hit, geometry) if geometry else False
    hue, coverage, spread = queueroles.card_hue(bgr, hit, geometry) if geometry \
        else (None, 0.0, 360.0)
    hues, tolerance = queuevision.load_modes()
    mode = "-" if hue is None else (queuevision.match_mode(hue, hues, tolerance) or "?")
    return ("    %-8s score %.2f  at %d,%d %dx%d  checkbox %-9s  "
            "hue %-5.0f mode %-11s cover %.2f spread %.1f"
            % (role, hit.score, hit.x, hit.y, hit.w, hit.h,
               "checked" if checked else "unchecked", hue, mode, coverage, spread))


def annotate(bgr, hits, geometry):
    import cv2
    canvas = bgr.copy()
    for role, hit in hits.items():
        checked = geometry is not None and queueroles.card_checked(bgr, hit, geometry)
        colour = (0, 220, 0) if hit.score >= queueroles.ICON_MIN_SCORE else (0, 0, 220)
        cv2.rectangle(canvas, (hit.x, hit.y), (hit.x + hit.w, hit.y + hit.h), colour, 2)
        cv2.putText(canvas, "%s %s" % (role, "X" if checked else "-"),
                    (hit.x, max(12, hit.y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (255, 255, 255), 1, cv2.LINE_AA)
        if geometry is not None:
            for box, tint in ((queueroles.CHECKBOX_REGION_FROM_ICON, (255, 200, 0)),
                              (queueroles.CARD_REGION_FROM_ICON, (255, 0, 200))):
                x0, y0, x1, y1 = queueroles._region_from_icon(box, hit, geometry)
                cv2.rectangle(canvas, (x0, y0), (x1, y1), tint, 1)
    return canvas


def show_one(bgr, options) -> int:
    hits = queueroles.find_role_row(bgr, queueroles.RoleIconStore())
    geometry = queueroles.row_geometry(hits)
    print("%d icon%s found in a %dx%d frame — geometry %s"
          % (len(hits), "" if len(hits) == 1 else "s", bgr.shape[1], bgr.shape[0],
             geometry or "not enough confident icons to measure"))
    for role in queueroles.ROLE_ORDER:
        if role in hits:
            print(describe(bgr, role, hits[role], geometry))
        else:
            print("    %-8s not found" % role)

    on_screen = queueroles.looks_like_role_select(hits)
    print("\n-> %s" % ("on screen" if on_screen else "not the role-select screen"))
    if on_screen and geometry is not None:
        checked = sorted(role for role, hit in hits.items()
                         if queueroles.card_checked(bgr, hit, geometry))
        print("   checked: %s" % (", ".join(checked) or "nothing"))

    if options.dump:
        import cv2
        cv2.imwrite(options.dump, annotate(bgr, hits, geometry))
        print("   wrote %s" % options.dump)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--image", help="read one saved frame instead of the screen")
    parser.add_argument("--dump", metavar="FILE", help="write an annotated frame to FILE")
    parser.add_argument("--loop", action="store_true", help="keep looking until Ctrl-C")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="seconds between looks with --loop (default 1.0)")
    options = parser.parse_args()

    if not queueroles.QUEUE_ROLES_AVAILABLE:
        print(MISSING)
        return 1

    import cv2

    if options.image:
        bgr = cv2.imread(options.image, cv2.IMREAD_COLOR)
        if bgr is None:
            print("Couldn't read %s" % options.image)
            return 1
        return show_one(bgr, options)

    try:
        while True:
            print(time.strftime("%H:%M:%S"))
            show_one(queueroles.capture(), options)
            if not options.loop:
                return 0
            print()
            time.sleep(options.interval)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
