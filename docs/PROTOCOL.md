# Queue protocol v1

What the PC server and the phone say to each other. Every sample below is **copied from
the app's own encoder** — `WireFormatTests.testPrintProtocolSamples` prints them, and the
other tests in that file fail if the format ever drifts from this document.

## Transport

A WebSocket:

```
ws://<pc-ip>:8787/queue
```

Text or binary frames, both accepted. The phone connects, sends `hello` **carrying its
pairing token**, and the server replies with a `snapshot` — or refuses the connection.
See [Pairing](#pairing) below. The client reconnects on its own with
exponential backoff (capped at 30s) and treats 30 seconds of total silence as a dead
connection, so **send a `heartbeat` at least every ~15 seconds** even when nothing changes.

The iOS client is configured for cleartext on the local network
(`NSAllowsLocalNetworking`), so plain `ws://` to a LAN address works. The first connection
triggers iOS's local-network permission prompt.

## Envelope

Every frame, both directions:

```json
{"v": 1, "body": { ... }}
```

`v` is the protocol version. A client receiving a version it doesn't know reports the
mismatch rather than guessing. Dates are **ISO-8601 with a `Z` suffix**
(`2023-11-14T22:13:20Z`). Durations are **seconds**, as numbers.

`body` is a tagged object — `{"type": "...", "data": {...}}`, with `data` omitted for
types that carry nothing.

## Server → client

### `snapshot` — the full state

The only message that matters. Send it whenever anything changes; it is always safe to
send, and the client is built to accept it at any time.

```json
{"v":1,"body":{"type":"snapshot","data":{
  "sessionID":"3F2504E0-4F89-41D3-9A0C-0305E82C3301",
  "sequence":7,
  "serverTime":"2023-11-14T22:15:37Z",
  "phase":{"type":"searching","data":{
    "mode":"competitive","role":"tank",
    "startedAt":"2023-11-14T22:13:20Z",
    "estimatedWait":240,"groupSize":1}}}}}
```

| Field | Meaning |
|---|---|
| `sessionID` | One UUID per continuous queue session. Change it when a new queue starts. |
| `sequence` | Increment on every snapshot within a session. The client **discards anything not newer**, which is what stops out-of-order delivery from rewinding the UI. |
| `serverTime` | Your clock, right now. The client measures the offset against its own and corrects every timer, so a PC clock a few seconds off doesn't skew displayed wait times. |
| `phase` | See below. |

### `heartbeat` — liveness

```json
{"v":1,"body":{"type":"heartbeat","data":{"serverTime":"2023-11-14T22:13:20Z"}}}
```

### `error` — a command couldn't be honoured

```json
{"v":1,"body":{"type":"error","data":{"code":"no_game","message":"Overwatch isn't running"}}}
```

`message` is shown to the user verbatim, so write it for a human.

Codes the server in `server/` uses:

| Code | Meaning |
|---|---|
| `pairing_required` | The `hello` had no token, or the wrong one. The connection is closed straight after. |
| `unpaired` | A command arrived before any `hello`. |
| `no_cancel` | A `cancelQueue` that couldn't be honoured. The next `snapshot` still holds the true state. |
| `version` | The client speaks a protocol version this server doesn't. |

## Pairing

The queue is on the local network, and a LAN is not a private place — a flatmate on the
same Wi-Fi shouldn't be able to drive the screen on your wrist. So the PC generates one
random token, shows it as a QR code, and refuses every connection that doesn't present it.

1. The server generates a UUID token on first run and stores it (in
   `~/.overwatch-queue/pairing.json`). It survives restarts, so pairing is a one-time act.
2. It shows a QR code carrying everything the phone needs:

   ```
   owq://pair?host=192.168.1.14&port=8787&token=3f2504e0-4f89-41d3-9a0c-0305e82c3301
   ```

   `port` may be omitted, and defaults to 8787. The iOS app registers `owq` in
   `CFBundleURLTypes`, so the system camera can hand the code straight to it rather than
   decoding a string with nowhere to go.
3. The phone scans it, keeps it, and sends the token in every `hello`.
4. A `hello` with a missing or wrong token gets an `error` with code `pairing_required`,
   and then a close with status **1008**. No snapshots are ever sent to a connection that
   hasn't presented the token.
5. A connection that sends no `hello` within ten seconds is closed the same way.

The phone passes the token on to its watch, so one scan configures both; expect the same
token from more than one client, and `kind` is what tells them apart. Both may be
connected at once, and both should get every snapshot.

Generating a new token on the PC unpairs every device at once, which is the recovery path
if a token leaks. There is no transport encryption: this is a token on a LAN, not a
credential worth stealing, and adding TLS would mean certificates for a machine that has
no name.

## Phases

`phase.type` is one of `idle`, `searching`, `matchFound`, `mapVote`, `heroSelect`,
`inGame`, `cancelled`.

**Send absolute timestamps, never countdowns.** Deadlines are moments (`lockInAt`,
`deadline`), and queue starts are moments (`startedAt`). The clients tick locally from
these. This is what lets a Live Activity count for ten minutes on the Lock Screen off a
single message — sending a ticking "secondsRemaining" would burn the update budget and
still drift.

### `idle`

```json
{"type":"idle"}
```

### `searching`

```json
{"type":"searching","data":{"mode":"competitive","role":"tank",
 "startedAt":"2023-11-14T22:13:20Z","estimatedWait":240,"groupSize":1}}
```

`mode`: `quickPlay` · `competitive` · `arcade` · `stadium` · `mysteryHeroes` · `custom`
`role`: `tank` · `damage` · `support` · `flex` · `open`
`estimatedWait` may be omitted when you don't have one — the UI shows an indeterminate
shimmer instead of a fake progress bar. Once elapsed passes the estimate the UI stops
pretending and says "longer than usual", so a rough estimate is fine.

### `matchFound`

```json
{"type":"matchFound","data":{"mode":"competitive","role":"tank",
 "waited":137,"lockInAt":"2023-11-14T22:13:20Z"}}
```

`waited` is the final wait in seconds, frozen. `lockInAt` is when the game pulls the
player in — Overwatch has no accept prompt, so this is "how long you have to get back to
the PC", not a response deadline.

### `mapVote`

```json
{"type":"mapVote","data":{"deadline":"2023-11-14T22:13:20Z","myVote":"ilios",
 "options":[{"mapKey":"ilios","votes":2},{"mapKey":"havana","votes":1}]}}
```

`mapKey` values are OverFast map keys (`ilios`, `kings-row`, `circuit-royal`…). Any key
the client doesn't recognise still renders, just without art. `myVote` echoes the local
player's choice.

### `heroSelect`

```json
{"type":"heroSelect","data":{"mode":"competitive","role":"tank","mapKey":"havana",
 "deadline":"2023-11-14T22:13:20Z","takenHeroKeys":["orisa"],"myHeroKey":"reinhardt"}}
```

`takenHeroKeys` are removed from the grid. `mapKey` is optional.

### `inGame`

```json
{"type":"inGame","data":{"mode":"competitive","mapKey":"havana",
 "heroKey":"reinhardt","startedAt":"2023-11-14T22:13:20Z"}}
```

### `cancelled`

```json
{"type":"cancelled","data":{"reason":"matchCancelled"}}
```

`reason`: `userLeft` · `matchCancelled` · `timedOut` · `serverError`.
Optional `message` overrides the default text shown to the user.

### Legal transitions

The client refuses transitions outside this table, so a confused server can't wedge the
UI in a nonsensical state:

```
idle        → searching
searching   → matchFound
matchFound  → mapVote | heroSelect | inGame | searching   (last = match fell apart)
mapVote     → heroSelect | inGame
heroSelect  → inGame
cancelled   → searching
any         → idle | cancelled
```

Re-sending the *same* phase with an updated payload is always allowed — that's how you
update an estimate or a vote tally.

## Client → server

Sent when the player acts on the phone or the watch.

```json
{"v":1,"body":{"type":"hello","data":{"token":"3f2504e0-4f89-41d3-9a0c-0305e82c3301","client":{"kind":"phone","name":"Tomer's iPhone","appVersion":"1.0"}}}}
{"v":1,"body":{"type":"voteMap","data":{"mapKey":"ilios"}}}
{"v":1,"body":{"type":"selectHero","data":{"heroKey":"reinhardt"}}}
{"v":1,"body":{"type":"cancelQueue"}}
{"v":1,"body":{"type":"requestSnapshot"}}
{"v":1,"body":{"type":"registerPushToken","data":{"token":"5fceb98...","environment":"sandbox"}}}
{"v":1,"body":{"type":"registerActivityPushToken","data":{"sessionID":"3F2504E0-4F89-41D3-9A0C-0305E82C3301","token":"activity-token...","environment":"sandbox"}}}
{"v":1,"body":{"type":"registerActivityStartToken","data":{"token":"start-token...","environment":"sandbox"}}}
```

`kind` is `phone` or `watch`. `token` is the pairing token — see [Pairing](#pairing).
It is optional in the schema so a server can choose not to require one, but the server
in `server/` always does.

`cancelQueue` is **best effort** — it means "leave the queue, or bail out of the match if
you still can". Overwatch often won't let you, and the client expects that. If it doesn't
work, just keep sending the real state; don't fabricate a cancellation.

`requestSnapshot` is sent when the app returns to the foreground. Reply with a `snapshot`.

The client applies its own votes and hero picks locally for instant feedback, then sends
the command. **Your next snapshot is authoritative** — if it disagrees, the client accepts
the server's version.

`registerPushToken` hands over the APNs device token this client just got from
`didRegisterForRemoteNotificationsWithDeviceToken` (or the watchOS equivalent), hex-encoded.
`environment` is `sandbox` for a debug build, `production` for a release one — Apple runs
separate push hosts for each, and a token is only valid against the one it was issued for.
Sent once per launch, right after `hello`; re-sending overwrites whatever this `kind`
registered before.

`registerActivityPushToken` hands over the push token for one running Live Activity —
what `Activity.pushTokenUpdates` yields once the phone starts it with `pushType: .token`.
`sessionID` ties it to the queue session that activity is showing; a token for a session
that's no longer current is simply ignored, so a late-arriving registration from an
activity that already ended can't accidentally attach to a new one.

`registerActivityStartToken` hands over the app-level **push-to-start** token — what
`Activity<QueueActivityAttributes>.pushToStartTokenUpdates` yields. Independent of any one
session: it's what lets the PC create the *next* Live Activity from nothing, even on a
launch that's never opened this queue's activity itself. Sent once available, and again
whenever the system hands over a replacement.

## Push

Silent by default, loud when it matters. Every state change — the same one that triggers
a `snapshot` broadcast — also, if this client has a registered push token, becomes a
**background** APNs push (`content-available`, no banner, no sound): just enough for the
app to wake up, reconnect, and pull a fresh snapshot on its own. A `matchFound`, `mapVote`
or `heroSelect` additionally gets a visible **alert** push, since those are the moments
worth surfacing even with the app fully closed.

**Live Activity pushes** are a different, more specific mechanism layered on top of the
same relay: they update the Dynamic Island / Lock Screen card directly, without waking the
app at all. Three events, all under `apns-push-type: liveactivity`:

- **start** — creates the activity from nothing, using the push-to-start token. Sent once
  per session, the first time a phase actually starts and no per-activity token exists
  yet. Carries `attributes` (`sessionID`, `startedAt`) and `content-state`, under the topic
  `<bundle-id>.push-type.liveactivity.start`.
- **update** — pushed to the activity's own per-activity token on every subsequent change,
  once the phone has had a chance to register one (see `registerActivityPushToken` above).
  Carries `content-state`; an urgent phase adds an `alert`.
- **end** — sent instead of an update once the phase goes back to `idle`/`cancelled`, then
  the per-activity token is forgotten.

Between "start" and the phone registering a real per-activity token, further changes are
silently held — there's a real, expected gap here, since push-to-start creates the
activity entirely OS-side without running any app code; only the *next* ordinary
background wake-up (above) gives the app a chance to attach and register one.

The one thing worth knowing if you implement a server for this from scratch: a Live
Activity push's `content-state` is decoded with a plain `JSONDecoder`, not this protocol's
`.iso8601` one — every date inside it has to be a bare number of seconds since
2001-01-01T00:00:00Z (Swift's default `Date` encoding, `timeIntervalSinceReferenceDate`),
not an ISO-8601 string and not a 1970 Unix timestamp. `server/owqserver/protocol.py`'s
`content_state()` is the reference implementation.

This is unrelated to the WebSocket above — a push is how the PC reaches a device that
currently holds no socket open at all (screen locked, app killed, watch out of range).
The server in `server/` needs an Apple Push Notification Auth Key to send these, and by
default reaches Apple through [`relay/`](../relay/README.md) — a small hosted service
that holds that key so no installed copy of the server has to — rather than talking to
Apple directly; see [server/README.md](../server/README.md#push-notifications-optional)
for both that default and the advanced bring-your-own-key path. Nothing here is required
— a server with neither configured just never pushes, and every client still works
exactly as it does today.

## The server

`server/` implements all of this, and drives it from a control panel instead of from the
game — which is what makes every screen in the app testable before any game detection
exists. See [server/README.md](../server/README.md).

If you're writing another one, the smallest thing worth having: accept the WebSocket,
check the token in `hello`, keep a `sequence` counter and a `sessionID`, send a `snapshot`
on connect and on every state change, send a `heartbeat` every 10 seconds, and log the
commands you receive. That alone drives every screen in the app. Acting on `voteMap` /
`selectHero` inside the game can come later.
