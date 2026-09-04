# Overwatch Queue

Your Overwatch queue on your iPhone and Apple Watch: which mode you're in, how long you've
waited, and a loud, unmissable moment when a match lands — then map vote and hero pick from
the phone or the wrist.

The state comes from a server on your PC, which reads the queue off the screen: the banner
at the top of Overwatch says whether you're searching, in which mode, and for how long, and
the server watches it and moves the phase to match. It keeps up across the gap where the
banner disappears into a while-you-wait deathmatch, and it takes the wait straight off the
game's own clock rather than guessing at one.

The same server also hands you a control panel, which still drives every phase by hand —
map vote and hero select are not read off the screen yet, and the panel is how those get
exercised, over the real socket, into the real app.

## Running it

Start the server on your PC:

```bash
pip install -r server/requirements.txt
python3 server/run.py
```

Then the app:

```bash
open OverwatchQueue.xcodeproj
```

Pick the **OverwatchQueue** scheme and an iPhone 16 Pro simulator (any iPhone 14 Pro or
later, for the Dynamic Island). No Apple Developer account needed for the simulator.

From the command line:

```bash
xcodebuild -project OverwatchQueue.xcodeproj -scheme OverwatchQueue -destination 'platform=iOS Simulator,name=iPhone 16 Pro' CODE_SIGNING_ALLOWED=NO build
```

## Pairing

The server window shows a QR code. Point the app at it — that's the whole of setup. The
system Camera works too: the app registers the `owq://` scheme, so a scan offers
**Open in "OW Queue"** and pairs on the way in.

Scanning is the only way in. An address and a token typed by hand were a second route
that had to be kept working and could be got subtly wrong; the code carries both and
can't be mistyped. **Settings** has the other half: pair, or unpair.

A Simulator has no camera, so there is nothing to point at a code. Hand it the link
instead — iOS will ask once, and **Open** finishes the pairing:

```bash
xcrun simctl openurl booted "owq://pair?host=127.0.0.1&port=8787&token=<token>"
```

## Driving it by hand

Everything that used to be an in-app debug panel now lives in the server window, at the
end of the wire where the real thing will be: mode and role, estimated wait, *Skip ahead*
to see a long queue without waiting for one, jumps between phases, and scripted scenarios
that play out on a timer. Illegal steps are greyed out, using the same transition rules
the app enforces on the way in.

Everything you trigger reaches the phone, the watch, the Live Activity and the widgets
together. See **[server/README.md](server/README.md)**.

## The wire

The client connects to `ws://<host>:<port>/queue`, reconnects on its own with backoff, and
corrects every timer for clock drift between your PC and your phone.

**[docs/PROTOCOL.md](docs/PROTOCOL.md)** documents every message with samples taken
directly from the app's encoder. `Tests/WireFormatTests.swift` fails if the Swift side
ever drifts from that document, and `server/tests/test_protocol.py` fails if the Python
side does.

## Layout

```
Shared/       Model, wire protocol, transport, pairing, catalog, store, design components
              — compiled into every target
iOS/          The phone app, Live Activity controller, haptics, sound, pairing screen
Widgets/      Live Activity (Lock Screen + Dynamic Island), Home Screen / StandBy widget
Watch/        The watch app
WatchWidgets/ Smart Stack accessory
Tests/        Wire format, pairing codes, transition rules, clock sync, catalog schema
server/       The PC server and its control panel — Python
relay/        Optional: the hosted push relay that lets the server wake a backgrounded
              phone/watch via APNs without holding an Apple credential itself
tools/        Project generator, catalog refresh
docs/         PROTOCOL.md, ASSETS.md
```

**The `.xcodeproj` is generated, never hand-edited.** After adding or removing any file:

```bash
python3 tools/genproject.py
```

## Design notes

**Timers never cross the wire.** Every phase carries absolute timestamps, and each surface
ticks locally with `TimelineView` / `Text(timerInterval:)`. That's what lets a Live Activity
count for ten minutes on the Lock Screen off a single update instead of six hundred — and
ActivityKit throttles apps that push too often, which would bite exactly when a match lands.

**Clock drift is corrected once per connection.** Your PC being a few seconds off would
otherwise skew every displayed wait time, and a Live Activity would tick from the wrong
origin for the entire queue.

**Snapshots carry a sequence number.** WatchConnectivity makes no ordering promise and a
reconnecting socket can replay, so anything not strictly newer is dropped rather than
allowed to rewind the UI.

**Art is referenced, not bundled** — and cached in memory and on disk so it loads once.
See [docs/ASSETS.md](docs/ASSETS.md), including how to drop in your own images per hero or map.

**The watch is configured by the same scan.** The phone hands the pairing to the watch
over WatchConnectivity the moment it has one, so nothing is ever set up on the wrist. The
watch still listens to the phone by preference — that relay coalesces, arrives while the
watch app is asleep, and costs the watch no radio of its own — but when the phone is out
of range or switched off it now opens its own socket to the PC instead of going blank.
Snapshots carry a sequence number, so a handover mid-queue can't rewind the screen.

**One token, no transport security.** The socket carries which mode you queued for, and
it never leaves your LAN. A token in a QR code is enough to stop a flatmate driving your
watch; TLS would mean issuing certificates for a machine with no name, to protect
something nobody wants.

**There is no Accept button.** Overwatch 2 has no accept prompt — it just puts you in. The
only real action is a best-effort cancel, offered quietly rather than as a primary button
that implies control you don't have.
