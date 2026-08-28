"""Hero and map keys, read from the snapshot the app already ships.

`Shared/Resources/catalog-fallback.json` is the app's cold-start catalog, refreshed by
`tools/refresh-catalog.py`. Reading it here means the vote and hero-select screens get
keys the app can actually draw art for, and there's one list to keep up to date rather
than two.
"""
from __future__ import annotations

import json
import os
import random

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CATALOG_PATH = os.path.join(REPO_ROOT, "Shared", "Resources", "catalog-fallback.json")

# Mirrors `MapType.pool(for:)` — the maps a given queue can actually put you on.
_ROTATION = ["escort", "hybrid", "control", "push", "flashpoint", "clash"]
_ARCADE_EXTRAS = ["assault", "elimination", "deathmatch", "team-deathmatch",
                  "capture-the-flag", "payload-race"]
MAP_POOLS = {
    "quickPlay": _ROTATION,
    "competitive": _ROTATION,
    "stadium": _ROTATION,
    "arcade": _ROTATION + _ARCADE_EXTRAS,
    "mysteryHeroes": _ROTATION + _ARCADE_EXTRAS,
    "custom": _ROTATION + _ARCADE_EXTRAS,
}

# Mirrors `QueueMode.catalogKey` — which hero roster a queue draws from.
HERO_POOLS = {"stadium": "stadium"}
DEFAULT_HERO_POOL = "quickplay"

# Used when the catalog file is missing, so the GUI still works from a bare checkout.
FALLBACK_MAPS = ["kings-row", "circuit-royal", "ilios", "havana", "busan", "new-junk-city"]
FALLBACK_HEROES = ["reinhardt", "orisa", "tracer", "ashe", "ana", "lucio"]


class Catalog:
    def __init__(self, path: str = CATALOG_PATH):
        self.path = path
        self.heroes = []
        self.maps = []
        self.error = None
        self.load()

    def load(self):
        try:
            with open(self.path) as handle:
                payload = json.load(handle)
            self.heroes = payload.get("heroes") or []
            self.maps = payload.get("maps") or []
            self.error = None
        except (OSError, ValueError) as problem:
            self.heroes, self.maps = [], []
            self.error = str(problem)

    @property
    def source(self) -> str:
        if self.error:
            return "built-in keys (%s)" % self.error
        return "%d heroes, %d maps" % (len(self.heroes), len(self.maps))

    # ------------------------------------------------------------ lookups

    def maps_for(self, mode: str) -> list:
        pool = set(MAP_POOLS.get(mode, _ROTATION))
        found = [m for m in self.maps if pool.intersection(m.get("gamemodes") or [])]
        return found or [{"key": key, "name": key, "gamemodes": []} for key in FALLBACK_MAPS]

    def heroes_for(self, role: str, mode: str) -> list:
        wanted = HERO_POOLS.get(mode, DEFAULT_HERO_POOL)
        found = [h for h in self.heroes
                 if wanted in (h.get("gamemodes") or [wanted])
                 and (role in ("flex", "open") or h.get("role") == role)]
        return found or [{"key": key, "name": key, "role": role} for key in FALLBACK_HEROES]

    def map_vote_options(self, mode: str, count: int = 3) -> list:
        """Three map keys, biased to distinct objective types the way the app does it,
        so a vote isn't three Escort maps."""
        pool = self.maps_for(mode)
        random.shuffle(pool)
        chosen, seen = [], set()
        for entry in pool:
            kind = (entry.get("gamemodes") or ["unknown"])[0]
            if kind not in seen:
                chosen.append(entry)
                seen.add(kind)
            if len(chosen) == count:
                break
        for entry in pool:
            if len(chosen) >= count:
                break
            if entry not in chosen:
                chosen.append(entry)
        return [entry["key"] for entry in chosen[:count]]

    def name_for_map(self, key: str) -> str:
        for entry in self.maps:
            if entry.get("key") == key:
                return entry.get("name", key)
        return key

    def name_for_hero(self, key: str) -> str:
        for entry in self.heroes:
            if entry.get("key") == key:
                return entry.get("name", key)
        return key
