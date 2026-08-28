# Push relay

A Cloudflare Worker that holds the one Apple Push Notification Auth Key this whole app
uses, so that no copy of `server/` — installed by anyone, on any PC — ever needs (or can
leak) the real Apple credential. Every server just talks to this Worker over HTTPS
instead of to Apple directly; this is the only thing that talks to Apple.

See [`docs/PROTOCOL.md#push`](../docs/PROTOCOL.md#push) for what a push actually contains,
and [`server/README.md`](../server/README.md#push-notifications) for how a PC points
itself at a relay.

## Why this exists

APNs authorizes at the **Apple Developer Team** level, not per device: the Auth Key signs
a JWT that says "I am Team X", and Apple then lets that JWT push to *any* device token
registered for *any* app under Team X. There is no way to mint a narrower credential that
only works for one person's phone. So if every installed copy of `server/` held that key
directly, anyone who extracted it from any one household's PC could push arbitrary
notifications to every user of this app — not just their own. Routing every push through
one relay that only its maintainer controls is what keeps the real key in exactly one
place.

## What the API key here does — and doesn't — protect

`RELAY_API_KEY` is checked on every request, but it's baked into an open-source client
that anyone can read the source of (or extract from a built binary). It is **not** a
secret in the sense the Apple Auth Key is. What it's actually for:

- A cheap first filter against requests that aren't from this app at all.
- Something you can rotate (ship an app update with a new key) if the relay is ever
  abused, without touching the Apple credential.

The real backstop is that **a push can only ever reach the one device token it names**,
and a device token is only ever handed to the one PC its owner paired with over their own
LAN (see the pairing scheme in `docs/PROTOCOL.md`) — a stranger who only has the relay's
URL and API key still has no device tokens to push to. `src/validate.ts` and
`src/rateLimit.ts` are the actual defenses against the relay being turned into a generic
"send anything anywhere" proxy; read the comments there before changing either.

## Deploying it

Requires Node and a Cloudflare account (the free plan is enough — see the size note
below).

1. Generate an APNs Auth Key in the
   [Apple Developer portal](https://developer.apple.com/account/resources/authkeys/list)
   and note its **Key ID** and your account's **Team ID**.
2. `cd relay && npm install`
3. Fill in `wrangler.toml`'s `[vars]` — `APNS_TEAM_ID`, `APNS_KEY_ID`, and the two bundle
   IDs (already correct for this app's real bundle IDs; only change these if you've
   forked it under your own).
4. Set the two secrets (never put these in `wrangler.toml`):
   ```bash
   wrangler secret put APNS_PRIVATE_KEY     # paste the whole .p8 file, including the
                                             # BEGIN/END lines
   wrangler secret put RELAY_API_KEY        # any long random string — this is what
                                             # server/ will need to configure per PC
   ```
5. `npm run deploy`. Wrangler prints the Worker's URL — that plus the `RELAY_API_KEY`
   from step 4 is everything a PC needs (see `server/README.md`).

## Developing

```bash
npm install
npm run dev     # wrangler dev — a local server backed by the real Workers runtime
npm test        # vitest — the pure logic (JWT signing, validation, rate limiting) needs
                 # no network and no deployed Worker to test
```

## Scale

This relay's entire job is forwarding one small JSON push per state change, for however
many households run this app — nowhere close to Cloudflare Workers' free-tier limit
(100,000 requests/day). If this project ever outgrows that, the Worker itself doesn't
need to change; only the billing does.

## Layout

```
wrangler.toml       config: bundle IDs and Team/Key ID (vars), the two secrets (not here)
src/
  apns.ts           signs the auth JWT and sends one push — no Workers types, unit-testable
  validate.ts       what a PC is allowed to ask for, and the constant-time key comparison
  rateLimit.ts       per-IP request throttling
  worker.ts          the HTTP handler that wires the above together
test/                vitest; no network, no deployed Worker required
```
