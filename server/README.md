# Overwatch Queue Server

What the phone and the watch talk to. It runs on the PC, holds the queue state, and
pushes it to every paired device over a WebSocket.

Half of it reads the game. The queue banner at the top of the screen is watched
continuously, so *searching* and *match found* happen on their own; hero select is read
when the panel asks for it. Everything else still comes from a control panel you drive by
hand, and that panel is not a mock — it pushes real snapshots through the real socket, and
what the phone does with them is exactly what it does when the screen is the thing
driving them.

```bash
pip install -r server/requirements.txt
python3 server/run.py
```

PyQt6 is the only dependency, and only for the window — everything else is the standard
library, including the QR encoder. Python 3.8 or newer.

## Pairing

The window shows a QR code. Open the app on your phone and point it at the code; that's
the whole of setup. The phone's own Camera app works too — the app registers the `owq://`
scheme, so scanning offers **Open in "OW Queue"** and pairing happens on the way in.

Scanning is the only way in: the app has no address-and-token form, so there is nothing
to mistype. Settings offers pair and unpair, and nothing else.

Behind it is a random token, generated once and kept in `~/.overwatch-queue/pairing.json`.
Every device sends it back in its first message, and a connection that doesn't present it
is closed — a LAN is not a private place, and nobody else on your Wi-Fi should be able to
drive the screen on your wrist. **New token** invalidates every paired device at once.

If your PC has more than one address — a VPN, Docker, a second NIC — pick the one the
phone can reach from the dropdown; the code redraws for whichever you choose.

## Push notifications (optional)

The WebSocket only reaches a device that's holding it open — the moment the phone's
screen locks or the watch drifts out of range, that stops being true. Apple Push
Notification service is how the PC reaches them anyway: it pushes a snapshot to Apple's
servers, and Apple delivers it even to a fully backgrounded app. See
[docs/PROTOCOL.md](../docs/PROTOCOL.md#push) for what actually gets sent.

The phone and watch each register their own device token the moment they connect and
have push permission — nothing to do on that side beyond installing a build with the
Push Notifications capability. Nothing below is required either: a server with neither
of these configured just never pushes, and everything else works exactly as it does today.

### The easy way: the maintainer's relay

APNs authorizes at the Apple Developer Team level, not per device — the credential that
lets you push at all would let you push to *every* installed copy of this app, not just
your own, so it can't simply ship inside the open-source server. Instead, `relay/` is a
small Cloudflare Worker that holds that one credential; every PC talks to it over HTTPS
instead of to Apple directly. See [relay/README.md](../relay/README.md) for why that's
the shape of it and exactly what it does and doesn't protect against.

Point your server at a deployed relay with `~/.overwatch-queue/push_relay.json`:

```json
{
  "url": "https://overwatch-queue-push-relay.<your-subdomain>.workers.dev",
  "api_key": "<the RELAY_API_KEY the relay was deployed with>"
}
```

### The advanced way: your own Apple Auth Key

If you'd rather not depend on anyone's relay — including the maintainer's — the server
will use a real Apple Auth Key directly if it finds one, before ever looking for
`push_relay.json`. This only makes sense for a server you and nobody else installs: the
same key that lets you push to your own phone lets you push to *any* device registered
for this app, so it should never leave a machine you personally control.

1. In the [Apple Developer portal](https://developer.apple.com/account/resources/authkeys/list),
   create an APNs Auth Key and download the `.p8` it gives you (only once — Apple won't
   let you download it again). Note its **Key ID** and your account's **Team ID**.
2. Drop the file in as `~/.overwatch-queue/AuthKey_<KeyID>.p8`.
3. Create `~/.overwatch-queue/apns.json` next to it:

   ```json
   {
     "team_id": "ABCDE12345",
     "key_id": "F6G7H8J9K0",
     "bundle_id_ios": "com.tomerady.OverwatchQueue",
     "bundle_id_watch": "com.tomerady.OverwatchQueue.watchkitapp"
   }
   ```

4. Install the extra dependencies this path needs (already listed, commented, in
   `server/requirements.txt`) and restart the server.

## Watching for a queue

On by default wherever the vision extras import; `--no-queue-vision` turns it off, as does
the **Watch for a queue** checkbox in the panel. It captures the top 15% of the screen a
few times a second and looks for the queue banner — a flat, saturated box that the game
draws and the game world never does. What it can tell from that:

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
separate, on-demand look at that screen, the same way hero select is: click **Detect from
screen** beside the role dropdown in the panel, or call `QueueServer.scan_role_select()`.

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

## The controls

| | |
|---|---|
| **Queue** | Mode, role and estimated wait, then *Start queue*. *Skip ahead* back-dates the start so you can see a ten-minute wait without waiting ten minutes. |
| **Jump to phase** | Match Found → Map Vote → Hero Select → In Game. Steps that aren't reachable from the current phase are greyed out, using the same transition rules the app enforces on the way in. |
| **Scenarios** | Scripted timelines that play out on a timer, so you can watch the real transitions and animations end to end rather than jumping between them. |
| **Honour cancel** | Off is the honest case: Overwatch usually won't let you leave. The app is built to be told so, and this is how you rehearse it. |
| **Traffic** | Connections, pairings, and the votes and hero picks coming back from the phone. |

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
  wsserver.py     a small RFC 6455 WebSocket server
  protocol.py     the wire format from docs/PROTOCOL.md
  pairing.py      the token, where it's stored, and the addresses to offer
  pushtokens.py   registered APNs device tokens, one per phone/watch
  activitytokens.py  the Live Activity's own push tokens — push-to-start and per-activity
  apns.py         talks to Apple directly — the advanced, bring-your-own-key path
  pushrelay.py    talks to the maintainer's relay instead — see ../relay/
  qr.py           a QR encoder, so none of the above needs installing
  catalog.py      hero and map keys, read from the app's own catalog
tests/            unittest; no dependencies, and the GUI tests run offscreen
```

```bash
python3 -m unittest discover -s server/tests -t server/tests
```

The QR tests check every block's Reed-Solomon syndromes against an independent field
implementation, and compare module-for-module against `segno` when that happens to be
installed. The protocol tests mirror the transition table in `QueuePhase.canTransition`,
so if the app grows a rule they fail until the server grows it too.
