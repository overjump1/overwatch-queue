"""Detects Overwatch's "Select a Role" screen by finding its four role icons in a row.

Only used while Battle.net says "In Queue" but the queue hasn't been confirmed yet, because
Battle.net reports "In Queue" from the moment that screen opens."""
from __future__ import annotations

import ctypes
import os
import re
import threading
import time
import urllib.request

try:
    import cv2
    import mss
    import numpy as np
    AVAILABLE = os.name == "nt"
except ImportError:
    cv2 = mss = np = None
    AVAILABLE = False

ROLE_ORDER = ["tank", "damage", "support", "flex"]
ICON_URLS = {
    "tank": "https://static.wikia.nocookie.net/overwatch_gamepedia/images/6/69/"
            "TankIcon.png/revision/latest?cb=20151109212047",
    "damage": "https://static.wikia.nocookie.net/overwatch_gamepedia/images/1/14/"
              "OffenseIcon.png/revision/latest?cb=20151109211953",
    "support": "https://static.wikia.nocookie.net/overwatch_gamepedia/images/5/5f/"
               "SupportIcon.png/revision/latest?cb=20151109212032",
    "flex": "https://static.wikia.nocookie.net/overwatch_gamepedia/images/d/da/"
            "Flex_Icon.svg/revision/latest?cb=20260217121748",
}
DOWNLOAD_RETRY_SECONDS = 300

ICON_VALUE_MIN = 170
ICON_SATURATION_MAX = 60
ROLE_ROW_REGION = (0.08, 0.38, 0.68, 0.53)
SCALES = [0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 1.0, 1.15]
ICON_MIN_SCORE = 0.5
MIN_ICONS_FOUND = 3
MIN_CARD_SPACING_RATIO = 1.5

_SVG_TOKEN = re.compile(r"([MLZ])|(-?[0-9]*\.?[0-9]+(?:[eE]-?[0-9]+)?)")


class RoleSelect:
    """`visible()` is True/False when it could look, None when it can't (Overwatch isn't the
    foreground window, icons unavailable, or no vision libraries)."""

    def __init__(self, cache_dir, log=print):
        self.cache_dir = cache_dir
        self.log = log
        self._templates = None
        self._last_attempt = 0.0
        self._lock = threading.Lock()

    def prefetch(self):
        if AVAILABLE:
            try:
                self._load()
            except Exception as problem:  # noqa: BLE001
                self.log("Role icon prefetch failed: %s" % problem)

    def visible(self):
        if not AVAILABLE:
            return None
        try:
            templates = self._load(blocking=False)
            if not templates:
                return None
            rect = overwatch_window()
            if rect is None:
                return None
            return looks_like_role_select(find_role_row(capture(rect), templates))
        except Exception as problem:  # noqa: BLE001
            self.log("Role select check failed: %s" % problem)
            return None

    def _load(self, blocking=True):
        if not self._lock.acquire(blocking=blocking):
            return None
        try:
            return self._load_locked()
        finally:
            self._lock.release()

    def _load_locked(self):
        if self._templates is not None:
            return self._templates
        if time.monotonic() - self._last_attempt < DOWNLOAD_RETRY_SECONDS and self._last_attempt:
            return None
        self._last_attempt = time.monotonic()
        os.makedirs(self.cache_dir, exist_ok=True)
        templates = {}
        for role in ROLE_ORDER:
            glyph = self._template(role)
            if glyph is not None:
                templates[role] = glyph
        if len(templates) < MIN_ICONS_FOUND:
            self.log("Role icons unavailable; role select detection is off for now")
            return None
        self._templates = templates
        return templates

    def _template(self, role):
        path = os.path.join(self.cache_dir, role + (".svg" if role == "flex" else ".png"))
        if not os.path.exists(path):
            try:
                request = urllib.request.Request(ICON_URLS[role], headers={"User-Agent": "OverQueue/2.0"})
                with urllib.request.urlopen(request, timeout=15) as response:
                    data = response.read()
                with open(path + ".part", "wb") as handle:
                    handle.write(data)
                os.replace(path + ".part", path)
            except Exception as problem:  # noqa: BLE001
                self.log("Couldn't download the %s icon: %s" % (role, problem))
                return None
        with open(path, "rb") as handle:
            data = handle.read()
        glyph = _rasterize_svg(data) if role == "flex" else _alpha_glyph(data)
        if glyph is None:
            os.remove(path)
            return None
        return _tight_crop(glyph)


def overwatch_window():
    """The Overwatch window's screen rectangle if it's the foreground window, else None."""
    user32 = ctypes.WinDLL("user32")
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user32.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.ClientToScreen.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    title = ctypes.create_unicode_buffer(64)
    user32.GetWindowTextW(hwnd, title, 64)
    if title.value != "Overwatch":
        return None

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    rect, origin = RECT(), POINT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)) or not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        return None
    if rect.right < 200 or rect.bottom < 200:
        return None
    return origin.x, origin.y, rect.right, rect.bottom


def capture(rect):
    left, top, width, height = rect
    with mss.mss() as sct:
        shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
    return cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)


def _alpha_glyph(data):
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None or image.ndim < 3 or image.shape[2] < 4:
        return None
    return image[:, :, 3]


def _tight_crop(mask):
    ys, xs = np.nonzero(mask)
    if ys.size == 0:
        return mask
    return mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def _svg_polygons(path_d):
    polygons, current, numbers = [], [], []
    for command, number in _SVG_TOKEN.findall(path_d):
        if command in ("M", "Z"):
            if current:
                polygons.append(current)
            current = []
        elif not command:
            numbers.append(float(number))
            if len(numbers) == 2:
                current.append(numbers)
                numbers = []
    if current:
        polygons.append(current)
    return polygons


def _rasterize_svg(data):
    """The flex icon is an SVG made only of straight-line triangles, so fillPoly is enough."""
    text = data.decode("utf-8", errors="ignore")
    view = re.search(r'viewBox="([^"]+)"', text)
    if view is None:
        return None
    try:
        _, _, width, height = (float(v) for v in view.group(1).split())
    except ValueError:
        return None
    if width <= 0 or height <= 0:
        return None
    canvas = np.zeros((int(height) + 1, int(width) + 1), np.uint8)
    for path_d in re.findall(r'd="([^"]*)"', text):
        for points in _svg_polygons(path_d):
            if len(points) >= 3:
                cv2.fillPoly(canvas, [np.array(points, dtype=np.float64).astype(np.int32)], 255)
    return canvas if canvas.any() else None


def _glyph_mask(bgr):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    keep = (hsv[:, :, 2] >= ICON_VALUE_MIN) & (hsv[:, :, 1] <= ICON_SATURATION_MAX)
    return keep.astype(np.uint8) * 255


def _best_match(screen_mask, template):
    best = None
    for scale in SCALES:
        w, h = int(template.shape[1] * scale), int(template.shape[0] * scale)
        if w < 6 or h < 6 or w > screen_mask.shape[1] or h > screen_mask.shape[0]:
            continue
        resized = cv2.resize(template, (w, h), interpolation=cv2.INTER_AREA)
        _, score, _, location = cv2.minMaxLoc(cv2.matchTemplate(screen_mask, resized, cv2.TM_CCOEFF_NORMED))
        if best is None or score > best[0]:
            best = (score, location[0], location[1], w, h)
    return best


def find_role_row(bgr, templates):
    """role -> (score, x, y, w, h) for each role icon found in the row region."""
    h, w = bgr.shape[:2]
    fx0, fy0, fx1, fy1 = ROLE_ROW_REGION
    x0, y0, x1, y1 = int(w * fx0), int(h * fy0), int(w * fx1), int(h * fy1)
    mask = _glyph_mask(bgr[y0:y1, x0:x1])
    hits = {}
    for role, template in templates.items():
        found = _best_match(mask, template)
        if found is not None:
            score, x, y, tw, th = found
            hits[role] = (score, x0 + x, y0 + y, tw, th)
    return hits


def looks_like_role_select(hits):
    """At least three confident icons, left to right in role order, on one row, and spaced
    like real cards rather than several templates landing on the same spot."""
    confident = {role: hit for role, hit in hits.items() if hit[0] >= ICON_MIN_SCORE}
    if len(confident) < MIN_ICONS_FOUND:
        return False
    by_x = sorted(confident, key=lambda role: confident[role][1])
    if by_x != [role for role in ROLE_ORDER if role in confident]:
        return False
    centers = [y + h / 2.0 for _, _, y, _, h in confident.values()]
    heights = [h for _, _, _, _, h in confident.values()]
    if max(centers) - min(centers) > 0.6 * (sum(heights) / len(heights)):
        return False
    x_centers = [confident[role][1] + confident[role][3] / 2.0 for role in by_x]
    average_width = sum(confident[role][3] for role in by_x) / len(by_x)
    return all(b - a >= MIN_CARD_SPACING_RATIO * average_width for a, b in zip(x_centers, x_centers[1:]))
