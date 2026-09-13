# Overwatch Queue Server

What the phone and the watch talk to. It runs on the PC, holds the queue state, and
pushes it to every paired device over MQTT, via a local Mosquitto broker this server
starts and manages on your behalf — no config file to write, no service to set up by
hand, just [Mosquitto itself](https://mosquitto.org/download/) installed and on `PATH`
(or its binaries dropped into `server/vendor/mosquitto/`).

Half of it reads the game. The queue banner at the top of the screen is watched
continuously, so *searching* and *match found* happen on their own; whichever role is
checked on the "Select a Role" screen is read the same way, whenever there's no queue to
watch instead. Past that, one watcher thread follows the whole rest of a match on its
own too: the vote screen, the hero roster, and the "Prepare to Attack" countdown that
means the match has actually started, each moving the phase along the moment it's seen —
map vote's own options read as the real maps on screen where the OCR extra is installed,
hero select's as the real roster and picks. The control panel underneath all of that is
not a mock, though — it pushes real snapshots through the real socket, and what the phone
does with them is exactly what it does when the screen is the thing driving them, which is
also what makes it possible to drive any of this by hand instead, or override it, at any
point.

```bash
pip install -r server/requirements.txt
python3 server/run.py
```

PyQt6 (the window) and `paho-mqtt` (talking to the local broker) are the only Python
dependencies — everything else is the standard library, including the QR encoder. Python
3.8 or newer. Mosquitto itself is a separate, non-Python install — see above.

## Pairing

The window shows a QR code. Open the app on your phone and point it at the code; that's
the whole of setup. The phone's own Camera app works too — the app registers the `owq://`
scheme, so scanning offers **Open in "OW Queue"** and pairing happens on the way in.

Scanning is the only way in: the app has no address-and-token form, so there is nothing
to mistype. Settings offers pair and unpair, and nothing else.

Behind it is a random token, generated once and kept in `~/.overwatch-queue/pairing.json`.
It doubles as the password every device's MQTT connection has to present, and a
connection with the wrong one is refused by the broker itself — a LAN is not a private
place, and nobody else on your Wi-Fi should be able to drive the screen on your wrist.
**New token** rewrites the broker's credentials and bounces it, invalidating every paired
device at once.

If your PC has more than one address — a VPN, Docker, a second NIC — pick the one the
phone can reach from the dropdown; the code redraws for whichever you choose.

## Push notifications

MQTT only reaches a device that's holding a connection open — the moment the phone's
screen locks, that stops being true. The Live Activity is how the queue stays on the Lock
Screen, Dynamic Island and (mirrored by the watch itself) the Apple Watch anyway, and this
server keeps it current by pushing to it through **Firebase Cloud Messaging**. That's the
only push it sends: no plain notification ever goes to the phone or the watch — the Live
Activity's own alert is the one interruption. See [docs/PROTOCOL.md](../docs/PROTOCOL.md#push)
for what actually gets sent, and `owqserver/fcm.py` for why Firebase rather than a direct
APNs push.

There is nothing to set up. The server never talks to Firebase itself: it posts each push
to the hosted relay in [`../relay/`](../relay/), which holds the one Firebase credential.
A service account can push to every user of the app, so it never belongs on anyone's PC.
The app registers its own Firebase and Live Activity tokens the moment it connects.

`OWQ_PUSH_RELAY_URL` points the server at a different relay (a local `wrangler dev`, say);
set but empty, it turns pushing off, and the panel says so.

## Watching for a queue

On by default wherever the vision extras import; `--no-queue-vision` turns it off. It
captures the top 15% of the screen a few times a second and looks for the queue banner —
a flat, saturated box that the game draws and the game world never does. What it can tell
from that:

| | |
|---|---|
| **Which state** | The tall pill at top centre is the Play menu; the wide bar in a top corner is a while-you-wait game; the small pill at top right is anywhere else — career profile, hero gallery, settings, any other screen. A green check circle on any of the three is *game found*. |
| **Which mode** | The banner's colour. Blue is Quick Play, pink is Competitive — see `owqserver/queuemodes.json`, and add to it rather than widening its tolerance. A colour that isn't listed is still a queue; the panel's mode is kept. |
| **How long** | Read off the timer printed on the banner, which is the game's own clock and so survives the server starting mid-queue. Until it can be read the wait is timed from here instead, and the panel says which of the two you're looking at. |

The seconds where the banner isn't drawn at all — loading into a deathmatch, a killcam, a
scoreboard — do not end the queue: an absent banner starts a twenty-second grace period
instead, and alt-tabbing suspends even that, because a screenshot of your browser is not
evidence about your queue.

Nothing in this half focuses a window or touches the mouse. It only looks; the hero-select
scanner above is the part that clicks.

### Its digits are learned, not shipped

There are no digit images in the repository and no font to render them from. The timer is
a clock, and a clock counts, which is enough for it to label its own digits: the tick
where the tens place rolls over is the tick where the units place is zero, and one run of
exactly ten ticks between two rollovers both labels ten glyphs and proves itself. A single
unbroken minute of queueing teaches it the set, which is then cached in
`server/.cache/queue-digits/` and never learned again.

### Retuning it

The constants in `owqserver/queuevision.py` were measured off captures. To check them
against your own screen:

```bash
python3 server/queuewatch_debug.py --all
```

One line per frame, plus every box it considered and the test that rejected each one. A
mode whose colour isn't in `queuemodes.json` prints as `?` beside the hue it actually is,
which is the number to add. `--dump DIR` saves annotated frames and `--image FILE` re-runs
a saved one.

## Reading which role is checked

Role-queue modes show a "Select a Role" screen before searching starts — Tank, Damage,
Support and a fourth, each with its own checkbox, and more than one can be checked at
once. Nothing about the queue banner says which of those was chosen, so this is a
separate look at that screen — but unlike hero select, reading it is nothing more than a
screenshot, so `queuewatch.QueueWatcher` reads it on every idle poll and keeps the role
it queues by in step automatically, with no button to press. `QueueServer.scan_role_select()`
is still there for a manual, one-off read from code.

It finds each card by its icon — the same shield, bullets, cross and three-circle glyphs
the rest of the game uses — rather than by position, then reads each one's own checkbox
for whether it's checked. The vivid colour behind whichever card is focused (the same
blue-for-Quick-Play, pink-for-Competitive hue the queue banner reads) is picked up too,
as a bonus this screen happens to also reveal — it marks keyboard/controller focus, not
which roles are selected, so it names the mode rather than deciding anything about roles.

Unlike everything else in `owqserver/queuevision.py`, the constants in
`owqserver/queueroles.py` were never measured against a live capture — they were read off
two screenshots handed over for this feature, which is a starting point rather than a
calibration. Check them against your own screen with:

```bash
python3 server/queuerole_debug.py --dump annotated.png
```

It prints where each icon was found, its confidence, its checkbox and card regions, and
whether the screen was recognised at all.

## Reading which maps are on the vote screen

The vote screen's own cards were tried two other ways before this one, and both failed
against real captures: the card's own thumbnail is a different render of the map
entirely from the catalog's promotional screenshot, so matching one against the other
means nothing, and rendering a candidate map's name in a stand-in font to compare against
the real text on screen doesn't survive either the wrong font or the game's own tight
letter-kerning — even switching to the game's real font (Overwatch's UI runs on Big
Noodle Too) didn't fix it, since a whole word squashed to another word's size throws away
the shape that told them apart. What actually reads the card cleanly is a real OCR model,
`rapidocr-onnxruntime` — an optional, meaningfully large extra (see
`server/requirements.txt`), not something this needs a system OCR binary installed
alongside the way `pytesseract` would. Every name it reads is checked against
`Catalog.key_for_map_name`, which tolerates a clipped crop losing a trailing letter but
refuses anything not close enough to a real map to trust — the same "win clearly or say
nothing" the rest of this project already holds every match to.

Without the extra installed, or with fewer than two of the (up to three) cards reading as
a real map, map vote falls back to the same plausible-but-not-real catalog options this
project has always shown — `Controls.map_vote_phase` is where that fallback lives.

## The controls

The window has two pages: **Pair** (a QR code, shown until a phone connects) and
**Status** (the live phase, what vision/presence currently see, and who's paired). Vision
and Battle.net presence run automatically whenever their extras are installed — there's no
on/off switch in the window; `--no-vision`, `--no-queue-vision` and `--no-presence` are the
way to turn them off (see `python3 server/run.py --help`).

The status page also has a **Manual override** to hand-drive the queue past whatever
vision/presence are seeing — useful with nothing installed, or for testing the Live
Activity and its alerts without waiting for the real thing. *Start* begins a fresh
queue, *Next* moves it one step through searching → match found → map vote → hero select →
in game → end (its label says which), and *Cancel* cancels it. Every step is one the app
accepts, so none is refused.

Maps and heroes come from `Shared/Resources/catalog-fallback.json`, the same catalog the
app ships, so the vote and hero-select screens get keys the app has art for.

## Without a window

```bash
python3 server/run.py --headless
```

Prints the QR code into the terminal and serves the same protocol — useful over SSH, or
when you want the thing out of the way. `--port` overrides the stored port and `--new-token`
unpairs everything.

## Pairing a Simulator

A Simulator has no camera, so there is no code to point it at. Press **Copy pairing
link** and hand the link over directly — iOS asks once, and **Open** finishes it:

```bash
xcrun simctl openurl booted "owq://pair?host=127.0.0.1&port=8787&token=<token>"
```

Use `127.0.0.1` rather than the LAN address the window shows: a Simulator shares the
Mac's network stack, so the server is already local to it.

The watch app takes `-server <host:port> -token <uuid>` at launch, because
WatchConnectivity between paired *simulators* is unreliable and pointing the watch
straight at the PC is the only way to test it without hardware.

## Layout

```
run.py            entry point
queuewatch_debug.py  prints what the queue watcher sees, for retuning it
owqserver/
  gui.py          the PyQt6 window
  controls.py     what its buttons mean, with no Qt in sight — the testable half
  queueserver.py  queue state, command handling, scenarios
  queuevision.py  finds the queue banner by its shape and colour
  queuedigits.py  reads the timer on it, and learns its digits from it
  queuewatch.py   turns a stream of frames into a queue, and tells the server
  vision.py       reads the hero-select screen, and clicks on it
  mqttbroker.py   starts, stops and configures the local Mosquitto broker
  mqttclient.py   the server's own connection to that broker
  protocol.py     the wire format from docs/PROTOCOL.md
  pairing.py      the token, where it's stored, and the addresses to offer
  pushtokens.py   registered device tokens — only used to tell a device has paired
  activitytokens.py  the Live Activity's own push tokens — push-to-start and per-activity
  fcmtokens.py    the phone's Firebase registration token
  fcm.py          sends Live Activity pushes through Firebase, via ../relay/
  qr.py           a QR encoder, so none of the above needs installing
  catalog.py      hero and map keys, read from the app's own catalog
tests/            unittest, run under pytest; the GUI tests run offscreen. test_server.py is the one real
                  dependency: it starts an actual Mosquitto broker, so it needs
                  `mosquitto`/`mosquitto_passwd` installed and reachable to run.
```

```bash
python3 -m pytest server/tests
```

Under pytest, not plain `unittest discover`: `tests/conftest.py` is what moves the home
directory somewhere temporary and turns pushing off before anything imports `owqserver`,
and only pytest loads it. Without it, a test that drives a bare `QueueServer` reads your
real device tokens and pushes to your real phone.

The QR tests check every block's Reed-Solomon syndromes against an independent field
implementation, and compare module-for-module against `segno` when that happens to be
installed. The protocol tests mirror the transition table in `QueuePhase.canTransition`,
so if the app grows a rule they fail until the server grows it too.
