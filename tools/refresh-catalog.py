#!/usr/bin/env python3
"""Refreshes the bundled cold-start catalog snapshot from the OverFast API.

The app fetches this data at runtime; this snapshot only covers a first launch with no
network. Re-run it occasionally so that first launch isn't badly out of date:

    python3 tools/refresh-catalog.py
"""
import datetime
import json
import os
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "Shared", "Resources", "catalog-fallback.json")

HEROES = "https://overfast-api.tekrop.fr/heroes?locale=en-us"
MAPS = "https://overfast-api.tekrop.fr/maps"


def get(url):
    request = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "OverwatchQueue/1.0",
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def main():
    heroes, maps = get(HEROES), get(MAPS)
    if not heroes or not maps:
        raise SystemExit("refusing to write an empty catalog")

    # MapType in Shared/Model/OverwatchMap.swift must cover every gamemode string, or
    # those maps decode as .unknown and drop out of the vote pool.
    known = {"escort", "hybrid", "control", "push", "flashpoint", "clash", "assault",
             "elimination", "deathmatch", "team-deathmatch", "capture-the-flag",
             "payload-race", "practice-range", "workshop"}
    seen = {g for m in maps for g in (m.get("gamemodes") or [])}
    if unknown := seen - known:
        print(f"WARNING: MapType is missing {sorted(unknown)} — add them to OverwatchMap.swift")

    payload = {
        "heroes": heroes,
        "maps": maps,
        "fetchedAt": datetime.datetime.now(datetime.timezone.utc)
                      .replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    with open(OUT, "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"{len(heroes)} heroes, {len(maps)} maps -> {OUT}")


if __name__ == "__main__":
    main()
