# Push relay

A Cloudflare Worker that holds the one Firebase service account this whole app uses, so
that no copy of `server/` — installed by anyone, on any PC — ever needs (or can leak) it.
Every server posts its Live Activity pushes here; this is the only thing that talks to
Firebase.

See [`docs/PROTOCOL.md#push`](../docs/PROTOCOL.md#push) for what a push actually contains,
and `server/owqserver/fcm.py` for why pushes go through Firebase rather than straight to
Apple.

## Why this exists

A service account authorizes at the **Firebase project** level: the access token it mints
can send to *any* registration token in the project. There is no narrower credential that
only works for one person's phone. So if every installed copy of `server/` held that key,
anyone who pulled it off one household's PC could push to every user of the app — and,
depending on the account's roles, reach much more of the project than messaging. Routing
every push through one relay that only its maintainer controls keeps it in one place.

## What protects the relay itself

There's no API key. `server/` is open source, so any key it shipped with would be public;
checking one would only look like protection. The real defenses are:

- **It only sends one kind of push.** The push type (`liveactivity`) and priority are
  fixed in `src/fcm.ts`, and `src/validate.ts` allows only the `aps` keys a
  `QueueActivityAttributes` start/update/end uses, a short plain alert, and at most 4 KB.
  It can't be used to send a regular notification, a background wake-up, or another app's
  activity.
- **A push reaches only the device and card it names.** Both an FCM registration token and
  an ActivityKit token are required, and those only ever reach the one PC the phone paired
  with over its own LAN (see the pairing scheme in `docs/PROTOCOL.md`). Someone with only
  the relay's URL has nobody to push to.
- **`src/rateLimit.ts`** throttles each IP.

## Deploying it

Requires a Cloudflare account (the free plan is enough).

1. In the Google Cloud console for the Firebase project, create a service account for the
   relay and give it only the **Firebase Cloud Messaging API Admin** role. Prefer this to
   the default `firebase-adminsdk` account, which can do far more than send messages.
   Create a JSON key for it.
2. Add that JSON as the repository secret `FCM_SERVICE_ACCOUNT`, then delete the file:
   ```bash
   gh secret set FCM_SERVICE_ACCOUNT < path/to/key.json
   ```
   (In Windows PowerShell, which has no `<`:
   `Get-Content -Raw path\to\key.json | gh secret set FCM_SERVICE_ACCOUNT`.)

That's all. The `Push relay` workflow runs the tests on every pull request that touches
`relay/`, and on `main` (or when run by hand) it deploys the Worker, uploads the secret to
it, and checks the new version is answering. It refuses to deploy if the secret is missing
or isn't a service account. It uses the existing `CLOUDFLARE_API_TOKEN` and
`CLOUDFLARE_ACCOUNT_ID` secrets. To rotate the key, set the secret again and re-run the
workflow.

The Worker's URL is built into `server/owqserver/fcm.py` as `RELAY_URL`; change it there
if the Worker is ever renamed or moved.

## Developing

```bash
npm install
npm run dev     # wrangler dev — put FCM_SERVICE_ACCOUNT in .dev.vars (gitignored), then
                # OWQ_PUSH_RELAY_URL=http://127.0.0.1:8787 points a server at it
npm test        # vitest — OAuth signing, validation and rate limiting, no network
```

## Layout

```
wrangler.toml       config; the one secret is not here
src/
  fcm.ts            mints the OAuth token and sends one push — no Workers types
  validate.ts       what a PC is allowed to ask for
  rateLimit.ts      per-IP request throttling
  worker.ts         the HTTP handler that wires the above together
test/               vitest; no network, no deployed Worker required
```
