# Overwatch Queue

Your Overwatch queue on your iPhone and Apple Watch: which mode you're in, how long you've
waited, and a loud, unmissable moment when a match lands — then map vote and hero pick from
the phone or the wrist.

A server on your Windows PC will eventually report the real state. It doesn't exist yet, so
the app ships with a **mock driver you control by hand**, and a WebSocket driver already
wired behind the same interface for when the PC side is ready.

## Running it

```bash
open OverwatchQueue.xcodeproj
```

Pick the **OverwatchQueue** scheme and an iPhone 16 Pro simulator (any iPhone 14 Pro or
later, for the Dynamic Island). No Apple Developer account needed for the simulator.

From the command line:

```bash
xcodebuild -project OverwatchQueue.xcodeproj -scheme OverwatchQueue -destination 'platform=iOS Simulator,name=iPhone 16 Pro' CODE_SIGNING_ALLOWED=NO build
```

## Driving it by hand

Tap the slider icon, top right. The debug panel pushes real events through the real
transport into the real store — what you see is exactly what the app will do when those
events arrive from a socket instead.

- **Queue** — mode, role, estimated wait, and *Skip ahead 1/5 minutes* to see a long wait
  without waiting for one
- **Jump to phase** — Match Found → Map Vote → Hero Select → In Game. Steps that aren't
  legal from the current phase are greyed out, using the same transition rules the real
  server has to follow
- **Scenarios** — scripted timelines that play out on a timer, so you can watch the real
  transitions and animations end to end
- **Sent upstream** — the commands your PC server would have received
- **Art cache** — how much art is cached, and a button to clear it

Everything you trigger mirrors to the watch and to the Live Activity.

There's also a launch argument for jumping straight to a phase, on both platforms:

```bash
xcrun simctl launch booted com.tomerady.OverwatchQueue -demoPhase heroSelect
```

`idle` · `searching` · `matchFound` · `mapVote` · `heroSelect` · `inGame` · `cancelled`.

## Connecting it to your PC

Switch **State from** to *PC Server* in the debug panel and enter your PC's LAN address.
The client connects to `ws://<host>:<port>/queue`, reconnects on its own with backoff, and
corrects every timer for clock drift between your PC and your phone.

Write the server against **[docs/PROTOCOL.md](docs/PROTOCOL.md)** — it documents every
message with samples taken directly from the app's encoder, and `Tests/WireFormatTests.swift`
fails if the format ever drifts from that document.

## Layout

```
Shared/       Model, wire protocol, transports, catalog, store, design components
              — compiled into every target
iOS/          The phone app, Live Activity controller, haptics, sound, debug panel
Widgets/      Live Activity (Lock Screen + Dynamic Island), Home Screen / StandBy widget
Watch/        The watch app
WatchWidgets/ Smart Stack accessory
Tests/        Wire format, transition rules, clock sync, catalog schema
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

**There is no Accept button.** Overwatch 2 has no accept prompt — it just puts you in. The
only real action is a best-effort cancel, offered quietly rather than as a primary button
that implies control you don't have.
