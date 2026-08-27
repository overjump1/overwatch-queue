# Art and audio

## Hero and map art — fetched, not bundled

The app pulls its catalog from the [OverFast API](https://github.com/TeKrop/overfast-api)
at runtime:

| Endpoint | Used for |
|---|---|
| `GET https://overfast-api.tekrop.fr/heroes?locale=en-us` | `key`, `name`, `portrait`, `role`, `subrole`, `gamemodes` |
| `GET https://overfast-api.tekrop.fr/maps` | `key`, `name`, `screenshot`, `gamemodes`, `location`, `country_code` |

Hero portraits resolve to Blizzard's own CDN
(`https://d15f34w2p8l1cc.cloudfront.net/overwatch/<hash>.png`); map screenshots come from
the API's static host. Nothing is copied into the app — it references the official
hosting, which also means heroes and maps added to the game appear without a rebuild.

The catalog isn't only art: `role` drives the hero grid's filtering and `gamemodes`
decides which maps are eligible for a given queue's vote.

### Caching

Two tiers, so art loads once and then stays instant:

- **Memory** — decoded `UIImage`s in an `NSCache` (300 entries / 96 MB ceiling), so
  scrolling the hero grid never re-decodes and returning to a screen shows art with no
  placeholder flash. Purged automatically under memory pressure.
- **Disk** — a 256 MB `URLCache` that survives relaunches. Art URLs are content-addressed
  (the filename is a hash of the image), so a cached response can never be stale and the
  cache policy prefers it without revalidating.

Verified: a full tank roster caches 16 image blobs (~3.3 MB), and a relaunch fetches
none of them again. Current usage and a **Clear image cache** button are in the debug
panel under *Art cache*.

`AsyncImage` is deliberately not used — it routes through `URLSession.shared`, whose
default cache is far too small for a 53-hero roster, and it keeps no decoded-image cache.
`CachedImage` in `Shared/Catalog/ImageCache.swift` replaces it.

### Three-layer fallback

1. Network fetch (current)
2. On-disk cache in Application Support (offline, survives launches)
3. `Shared/Resources/catalog-fallback.json` in the bundle — a snapshot of both catalogs so
   a first launch with no network still has the full hero and map list (text only, no images)

Regenerate the bundled snapshot at any time:

```bash
python3 tools/refresh-catalog.py
```

## Using your own art instead

An image in the asset catalog **wins over the network fetch**, per entry. Add images to
`iOS/Assets.xcassets` (or `Watch/Assets.xcassets`) named:

```
Hero/<hero-key>     e.g. Hero/reinhardt, Hero/wrecking-ball
Map/<map-key>       e.g. Map/kings-row, Map/circuit-royal
```

The keys are exactly the `key` fields from the endpoints above. Hero images are drawn
square; map images fill a 156pt-tall card, so landscape crops work best. Anything you
don't supply keeps using the fetched art, and anything neither source has falls back to a
drawn placeholder — a role-tinted tile with the hero's initials, or an objective-type
glyph for a map. The grid never has holes.

Run `python3 tools/genproject.py` after adding files so the project picks them up.

## Audio

`Shared/Resources/match_found.caf` is a synthesized cue: a low thump, then two bell tones
a fifth apart with the second entering late so it rises rather than lands flat.

To use your own, replace that file — same name, same folder. Any format
`AVAudioPlayer` reads works if you also update the extension in
`iOS/SoundPlayer.swift`. It plays in the `.ambient` category with `.mixWithOthers`, so it
never pauses whatever music you're queueing to.

The haptic is generated in code (`iOS/Haptics.swift`) and needs no asset: a sharp
transient, a second one a beat later, then a rumble that decays over half a second.
