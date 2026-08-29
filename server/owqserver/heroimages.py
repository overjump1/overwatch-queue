"""Grayscale hero portraits for template matching, cached on disk.

There is no second hero list here and no second API to call. The app already ships a
catalog with a `portrait` URL for every hero (`Shared/Resources/catalog-fallback.json`,
refreshed by `tools/refresh-catalog.py`), so the art this matcher compares the screen
against is the same art the phone draws. Fetched once into a local cache, read back as
grayscale from then on — matching only ever needs luminance, and converting once at load
is cheaper than converting on every frame.

Missing art is not an error worth stopping for: a hero whose portrait won't download is
simply one the scan can't recognise, and `template()` returns None so the caller can skip
it and carry on with the other fifty-two.
"""
from __future__ import annotations

import os
import urllib.error
import urllib.request

try:                                    # pragma: no cover - trivial import guard
    import cv2
    import numpy as np
    IMAGES_AVAILABLE = True
except ImportError:                     # pragma: no cover - the Mac dev path
    cv2 = None
    np = None
    IMAGES_AVAILABLE = False

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_CACHE_DIR = os.path.join(REPO_ROOT, "server", ".cache", "hero-portraits")

DOWNLOAD_TIMEOUT_SECONDS = 15


class TemplateStore:
    """Lazily downloads and caches one grayscale template per hero key."""

    def __init__(self, catalog, cache_dir: str = DEFAULT_CACHE_DIR, log=None):
        self.catalog = catalog
        self.cache_dir = cache_dir
        self.log = log or (lambda message: None)
        self._templates = {}            # hero key -> grayscale array, or None if hopeless

    # ------------------------------------------------------------ lookups

    def portrait_url(self, hero_key: str):
        for entry in self.catalog.heroes:
            if entry.get("key") == hero_key:
                return entry.get("portrait")
        return None

    def path_for(self, hero_key: str) -> str:
        return os.path.join(self.cache_dir, "%s.png" % hero_key)

    # ------------------------------------------------------------ the cache

    def template(self, hero_key: str):
        """The hero's grayscale portrait, downloading it the first time. None if the art
        can't be had — a hero the scan will simply not look for."""
        if not IMAGES_AVAILABLE:
            return None
        if hero_key in self._templates:
            return self._templates[hero_key]

        path = self.path_for(hero_key)
        if not os.path.exists(path) and not self._download(hero_key, path):
            self._templates[hero_key] = None
            return None

        image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if image is None:
            # A truncated or half-written file from an interrupted run. Drop it so the
            # next call fetches it again rather than failing forever on the same bytes.
            self._forget_file(path)
            self._templates[hero_key] = None
            return None

        self._templates[hero_key] = image
        return image

    def _download(self, hero_key: str, path: str) -> bool:
        url = self.portrait_url(hero_key)
        if not url:
            return False
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Written to a neighbouring name and moved into place, so an interrupted download
        # can't leave a half-file that later looks cached.
        partial = path + ".part"
        try:
            with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
                payload = response.read()
            with open(partial, "wb") as handle:
                handle.write(payload)
            os.replace(partial, path)
            return True
        except (urllib.error.URLError, OSError, ValueError) as problem:
            self.log("Couldn't fetch %s's portrait: %s" % (hero_key, problem))
            self._forget_file(partial)
            return False

    @staticmethod
    def _forget_file(path: str):
        try:
            os.remove(path)
        except OSError:
            pass

    # ------------------------------------------------------------ warming

    def prefetch(self, hero_keys) -> int:
        """Pulls every template down up front. Worth calling once before the first scan:
        fifty-odd downloads inside a scan would read as the scan being slow."""
        got = 0
        for key in hero_keys:
            if self.template(key) is not None:
                got += 1
        return got
