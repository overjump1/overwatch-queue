# Queue protocol v1

What the PC server and the phone say to each other. Every sample below is **copied from
the app's own encoder** — `WireFormatTests.testPrintProtocolSamples` prints them, and the
other tests in that file fail if the format ever drifts from this document.

## Transport

MQTT, against a local broker the PC server itself starts and manages (Eclipse Mosquitto —
see `server/owqserver/mqttbroker.py`). Previously a hand-rolled WebSocket server; that had
no redelivery for a client whose write failed (a backgrounded phone, a flaky Wi-Fi), so a
snapshot could be silently dropped with nothing to recover it short of the next unrelated
state change. MQTT's retained messages and QoS 1 close that gap at the transport level
instead of needing app-level acks.

Plain TCP, port 8787 by default, no transport encryption — the same LAN-only stance the
WebSocket server always had (see [Pairing](#pairing)).

### Topics

| Topic | Direction | QoS | Retained | Carries |
|---|---|---|---|---|
| `owq/snapshot` | server → all | 1 | yes | `snapshot` |
| `owq/heartbeat` | server → all | 0 | no | `heartbeat` |
| `owq/command/<clientID>` | client → server | 1 | no | every client→server message |
| `owq/reply/<clientID>` | server → one client | 0 | no | `pong`, `error` |
| `owq/presence/<clientID>` | either | 1 | yes | `{"online": bool}` |

`<clientID>` is the MQTT client ID a phone or watch connects with — stable across
relaunches (so a fresh install doesn't leave a stale `owq/presence/<old-id>` claiming a
dead device is online), unique per install. The server has no socket to read an identity
off any more, so it tracks each device purely by this ID; `owq/command/<clientID>` (rather
than one shared command topic) is what tells connections apart without needing the payload
itself to carry an ID.

`owq/snapshot` being **retained** is the core reliability property: any client that
(re)subscribes — after a dead Wi-Fi, a cold launch, the watch's direct-connect fallback
coming up — gets the current state instantly, with no round trip and no dependency on a
new state change happening to arrive. `owq/heartbeat` is **not** retained, deliberately: a
client trusts its freshness for clock re-anchoring (see `ping`/`pong` below), and a stale
retained heartbeat would defeat that.

### Auth

The pairing token is this connection's MQTT **password** (username `owq`) — see
[Pairing](#pairing). A wrong or missing one is refused by the broker at `CONNECT`, before
any application code or state is reachable; there is no more app-level accept-then-reject
handshake. Each device also sets a **Last Will** on its own `owq/presence/<clientID>`
(`{"online":false}`, retained) so the broker itself reports it gone the moment the
connection actually dies — this, plus the broker's own keepalive, is what replaced the old
socket-level heartbeat's liveness role. `owq/heartbeat` still exists, but purely for clock
sync (see `ping`/`pong`), not liveness.

The iOS client is configured for cleartext on the local network
(`NSAllowsLocalNetworking`), so a plain TCP connection to a LAN address works. The first
connection triggers iOS's local-network permission prompt.

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

### `heartbeat` — a fresh clock sample

```json
{"v":1,"body":{"type":"heartbeat","data":{"serverTime":"2023-11-14T22:13:20Z","presenceDegraded":false}}}
```

Published to `owq/heartbeat` every ~10 seconds while the server is running. No longer a
liveness signal (MQTT's own keepalive and each client's Last Will cover that) — its only
remaining job is giving a connected client a `serverTime` fresh enough to trust for clock
re-anchoring; see `ping`/`pong` below for why a snapshot's `serverTime` isn't used for this.

`presenceDegraded` is `true` whenever the server was asked to read Battle.net's own
presence and that link has gone quiet — Battle.net closed, crashed, or its debug port
stopped answering (see `owqserver.bnetpresence.PresenceWatcher.dead`). It rides along on
the heartbeat rather than a message of its own, since it changes at most as often as
Battle.net's connection does — there's nothing a dedicated topic buys over a field on an
already-periodic broadcast. `false` covers both "presence is fine" and "presence was
never asked for" — the app is simply running on the screen alone in either case, exactly
as it always did before presence was ever involved. **Not yet decoded by the Swift
client** — `Shared/Protocol/WireProtocol.swift`'s `.heartbeat` case and its `WireCoding`
would need the field added to actually surface a disconnect notice on the phone/watch;
this document and the server side are ahead of it.

### `pong` — the answer to `ping`, for verifying the clock

```json
{"v":1,"body":{"type":"pong","data":{"clientTime":1700000000.123,"serverTime":1700000000.456}}}
```

Reply to every `ping` immediately, and echo `clientTime` back **exactly as it arrived** —
the client needs its own send time to work out how long the round trip took, and anything
you do to it is charged to the answer. Any delay you add is indistinguishable, to the
client, from distance.

Both fields are **epoch seconds as numbers**, not the whole-second ISO-8601 strings used
everywhere else on this wire. Rounding to the second would be a half-second error in the
one message whose entire purpose is measuring time.

### `error` — a command couldn't be honoured

```json
{"v":1,"body":{"type":"error","data":{"code":"no_game","message":"Overwatch isn't running"}}}
```

`message` is shown to the user verbatim, so write it for a human.

Codes the server in `server/` uses:

| Code | Meaning |
|---|---|
| `no_cancel` | A `cancelQueue` that couldn't be honoured. The next `snapshot` still holds the true state. |
| `version` | The client speaks a protocol version this server doesn't. |

A bad or missing pairing token is no longer an app-level error: the broker refuses the
`CONNECT` outright (see [Transport](#transport)), so a connection that made it far enough
to receive any reply has already proven it holds the right token.

## Pairing

The queue is on the local network, and a LAN is not a private place — a flatmate on the
same Wi-Fi shouldn't be able to drive the screen on your wrist. So the PC generates one
random token, shows it as a QR code, and refuses every connection that doesn't present it.

1. The server generates a UUID token on first run and stores it (in
   `~/.overwatch-queue/pairing.json`, alongside a second, never-shown password the server
   itself uses to talk to its own broker — see `mqttbroker.py`). It survives restarts, so
   pairing is a one-time act.
2. It shows a QR code carrying everything the phone needs:

   ```
   owq://pair?host=192.168.1.14&port=8787&token=3f2504e0-4f89-41d3-9a0c-0305e82c3301
   ```

   `port` may be omitted, and defaults to 8787 — now the MQTT broker's port rather than a
   WebSocket's. The iOS app registers `owq` in `CFBundleURLTypes`, so the system camera can
   hand the code straight to it rather than decoding a string with nowhere to go.
3. The phone scans it, keeps it, and connects to the broker with it as its MQTT password
   (username `owq`).
4. A connection presenting a missing or wrong password is refused by the broker at
   `CONNECT` — no error message is possible at that point, since the connection never
   completes. There's nothing to distinguish "wrong token" from "broker unreachable" on
   this side beyond the connection simply never succeeding; a client should surface that
   as "not paired — re-scan the code" after a few failed attempts.

The phone passes the token on to its watch, so one scan configures both; both connect with
the same username/password but their own `owq/command/<clientID>` topic, and both should
get every `owq/snapshot`.

Generating a new token on the PC rewrites the broker's password file and bounces it,
dropping every connected device at once — the recovery path if a token leaks. There is no
transport encryption: this is a token on a LAN, not a credential worth stealing, and adding
TLS would mean certificates for a machine that has no name.

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
 "deadline":"2023-11-14T22:13:20Z","takenHeroKeys":["orisa"],"myHeroKey":"reinhardt",
 "availableHeroKeys":["reinhardt","orisa","sigma"],
 "teamPicks":[{"slot":2,"heroKey":"reinhardt","isSelf":true},
              {"slot":3,"heroKey":"orisa","isSelf":false}]}}
```

`takenHeroKeys` are removed from the grid. `mapKey` is optional.

`availableHeroKeys` and `teamPicks` are what the server read off the PC's screen, and are
**absent entirely when it didn't look** — no game running, or a server without the vision
extras. Absent and empty are different answers and clients must treat them differently:
absent means "nobody looked", so fall back to the shipped catalog; empty would mean the
roster is genuinely empty, which never happens. Both were added after v1 shipped, so a
client that has never heard of them decodes the phase exactly as before.

`slot` is a position in the on-screen row of player portraits. **It is not an identity and
not a role** — role queue orders the slots by role, so the local player is not reliably in
slot 1. `isSelf` is the only thing that marks the local player's pick; it is `false` on
every pick when the server couldn't tell which one was ours, and clients should show no
"you" marker in that case rather than assuming a position.

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
{"v":1,"body":{"type":"registerPushToken","data":{"token":"5fceb98...","environment":"sandbox","kind":"watch"}}}
{"v":1,"body":{"type":"registerActivityPushToken","data":{"sessionID":"3F2504E0-4F89-41D3-9A0C-0305E82C3301","token":"activity-token...","environment":"sandbox"}}}
{"v":1,"body":{"type":"registerActivityStartToken","data":{"token":"start-token...","environment":"sandbox"}}}
{"v":1,"body":{"type":"ping","data":{"clientTime":1700000000.123}}}
{"v":1,"body":{"type":"diagnostic","data":{"message":"Live Activity: started 24C67173-AADE"}}}
```

`kind` is `phone` or `watch`. `token` in `hello` is a holdover from the WebSocket-era
protocol and is no longer read by `server/` — the pairing token now gates the MQTT
connection itself (see [Pairing](#pairing)), so `hello` is purely an identity
announcement (which kind of device this is, for the device list and for filing push
tokens). Still accepted and still optional in the schema, for a server built differently.

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

`kind` says which device the token belongs to, and is **not** the same as the identity in
this connection's `hello`. A watch with no socket of its own registers through the paired
iPhone, so the arriving connection is the phone's — trust `kind` over it, or the watch's
token lands under `phone`, on top of the phone's own, and neither device can be reached.
Fall back to the connection's identity when `kind` is absent.

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

`ping` asks what time it is, carrying this device's own clock so the reply can be timed.
Answer with `pong`. This is the **only** message a client should measure a clock offset
from: `serverTime` on a `snapshot` or a `heartbeat` is stamped before the message travels
and read after it arrives, so it silently understates by however long delivery took — and
both surfaces here buffer, since iOS hands a resumed app its socket backlog and
WatchConnectivity coalesces. A round trip makes that delay *measurable* instead of
invisible, which is what lets a slow sample be rejected rather than believed.

`diagnostic` is a line the device wants written into the server's log, and the only
client command that isn't about the queue at all. Almost everything interesting about a
Live Activity happens where nobody can watch: the card is created by the system, updated
by push, and drawn by an extension in another process — so when one fails to appear there
is nothing to read anywhere. This is how the device says so out loud.

Debug builds only, and dropped rather than queued when the socket is down: a diagnostic
is worth something next to the moment it describes and nothing at all delivered late and
out of order. A server is free to ignore it entirely; `server/` prints it prefixed with
the client's name, so a line the phone wrote is never mistaken for something the server
observed.

## Push

Silent, deliberately. Every state change — the same one that triggers a `snapshot`
broadcast — becomes a **background** APNs push to any registered phone/watch token
(`content-available`, no banner, no sound): just enough for the app to wake up,
reconnect, and pull a fresh snapshot on its own. The **Live Activity pushes** below are
what's meant to actually surface a change on screen; `server/` never sends the standalone
visible **alert** push type on its own device-token, even for
`matchFound`/`mapVote`/`heroSelect` — that would be a second, separate notification for
the same event the Live Activity already announces (see below). (The `alert` push type
itself still exists and is a real, tested capability of both `apns.py`/`pushrelay.py` and
the relay — a server built differently is free to use it directly; this one's own policy
just routes the "make noise" moments through the Live Activity's own alert instead.)

**Live Activity pushes** are a different, more specific mechanism layered on top of the
same relay: they update the Dynamic Island / Lock Screen card directly, without waking the
app at all. Three events, all under `apns-push-type: liveactivity` and the **same topic**,
`<bundle-id>.push-type.liveactivity` — including the start push. A separate
`.push-type.liveactivity.start` topic is commonly described online for this, but it's
wrong, at least for real push-to-start tokens against this account: verified directly by
sending the same real token to both topics — `TopicDisallowed` on the `.start` one, `200`
on the plain one, unchanged otherwise.

- **start** — creates the activity from nothing, using the push-to-start token. Sent the
  first time a phase actually starts and no per-activity token exists yet, and *retried*
  on every subsequent change (throttled to once per `_activity_start_retry_seconds`) until
  one registers — a background push is best-effort, so the first attempt landing is never
  guaranteed. Carries `attributes` (`sessionID`, a `startedAt` fixed once per session and
  reused on every retry, so Apple recognises a retry as the same activity rather than a
  new one), `content-state`, and **always an `alert`** — verified directly, back to back,
  against a real push-to-start token: an otherwise-identical silent one reliably never
  showed up, one with an `alert` did, every time. There's nothing on screen yet for a
  silent push to land on, and Apple appears to treat a wholly silent push-to-start as
  low-priority best-effort in a way it doesn't one with an alert.
- **update** — pushed to the activity's own per-activity token on every subsequent change,
  once the phone has had a chance to register one (see `registerActivityPushToken` above).
  Carries `content-state`, plus an `alert` only for `matchFound`/`mapVote`/`heroSelect` —
  sound, haptic, a brief peek, the way a delivery app announces "your order is on the way"
  without a separate notification alongside it. Routine updates stay silent; the card
  changing is signal enough once it's already visible.
- **end** — sent instead of an update once the phase goes back to `idle`/`cancelled`, then
  the per-activity token is forgotten.

Between "start" and the phone registering a real per-activity token, further updates
have nothing to reach yet — there's a real, expected gap here, since push-to-start
creates the activity entirely OS-side without running any app code; only the *next*
ordinary background wake-up (above) gives the app a chance to attach and register one.
`start` itself keeps retrying in the meantime, so a delayed first attempt isn't fatal.

The one thing worth knowing if you implement a server for this from scratch: a Live
Activity push's `content-state` is decoded with a plain `JSONDecoder`, not this protocol's
`.iso8601` one — every date inside it has to be a bare number of seconds since
2001-01-01T00:00:00Z (Swift's default `Date` encoding, `timeIntervalSinceReferenceDate`),
not an ISO-8601 string and not a 1970 Unix timestamp. `server/owqserver/protocol.py`'s
`content_state()` is the reference implementation.

This is unrelated to the MQTT transport above — a push is how the PC reaches a device that
currently holds no connection open at all (screen locked, app killed, watch out of range).
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

If you're writing another one, the smallest thing worth having: run a local MQTT broker
with the pairing token as a connecting client's password, keep a `sequence` counter and a
`sessionID`, publish a retained `snapshot` to `owq/snapshot` on every state change (and once
at startup, so a client connecting before anything has happened yet still gets one), publish
a `heartbeat` to `owq/heartbeat` every 10 seconds, and log the commands you receive on
`owq/command/+`. That alone drives every screen in the app. Acting on `voteMap` /
`selectHero` inside the game can come later.
