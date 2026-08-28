# Overwatch Queue Server

What the phone and the watch talk to. It runs on the PC, holds the queue state, and
pushes it to every paired device over a WebSocket.

The game side doesn't exist yet, so the state comes from a control panel you drive by
hand. That panel is not a mock: it pushes real snapshots through the real socket, and
what the phone does with them is exactly what it will do when this program is reading
Overwatch instead of reading a mouse click.

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
owqserver/
  gui.py          the PyQt6 window
  controls.py     what its buttons mean, with no Qt in sight — the testable half
  queueserver.py  queue state, command handling, scenarios
  wsserver.py     a small RFC 6455 WebSocket server
  protocol.py     the wire format from docs/PROTOCOL.md
  pairing.py      the token, where it's stored, and the addresses to offer
  pushtokens.py   registered APNs device tokens, one per phone/watch
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
