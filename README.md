# OW Queue

See how long you've been in an Overwatch queue on your iPhone and Apple Watch. When a match is found, both of them notify you.

```
PC app (Windows)  ──state──▶  Cloudflare worker  ──FCM──▶  Live Activity (iPhone lock screen, Dynamic Island, Watch Smart Stack)
                                     ▲                     + a "Match found" alert to the Watch
                    iPhone and Watch apps read the state here while they're open
```

- **pc/** reads your Battle.net presence ("Competitive: In Queue") straight out of Battle.net's memory. It only uses screen vision in one case: Battle.net says "In Queue" but the queue hasn't been confirmed yet, which is the role-select screen. The queue counts as started once that screen closes. The match counts as found when Battle.net says you're in game.
- **worker/** keeps the latest state for each pairing and sends the Live Activity pushes.
- **ios/** holds the iPhone app, the Live Activity, and the Watch app.

## Windows app

```
pip install -r pc/requirements.txt
python pc/app.py
```

Scan the QR code with the iPhone app. **Reset QR code** makes a new code; the old one stops working right away. Logs are in `%APPDATA%\OWQueue\owqueue.log`.

If Battle.net runs as administrator, the app has to run as administrator as well, or it can't read Battle.net's memory.

## Worker

```
cd worker && npm install
npx wrangler secret put FCM_SERVICE_ACCOUNT   # one time: Firebase service-account JSON
npx wrangler deploy
npm test
```

## iPhone and Watch apps

1. Put `GoogleService-Info.plist` in `ios/iOS/`.
2. In Firebase, add a second Apple app with the bundle ID `com.tomerady.OverwatchQueue.watchkitapp`, and put its plist in `ios/Watch/`. The Watch app works without it, but then it won't get its own "Match found" alert.
3. Open `OverwatchQueue.xcodeproj`. It's generated from `project.yml`: after adding or removing files, run `xcodegen` in the repo root and commit the result.

### TestFlight (Xcode Cloud)

Pushes to `main` build and upload to TestFlight. `ci_scripts/ci_post_clone.sh` writes `GoogleService-Info.plist` from the workflow's secret environment variable `GOOGLE_SERVICE_INFO_PLIST`, which holds the output of `base64 -i ios/iOS/GoogleService-Info.plist`.

To test in the Simulator against a local worker (`npx wrangler dev`), run a Debug build with the launch argument `-workerURL http://127.0.0.1:8787`, then pair:

```
xcrun simctl openurl booted "owq://pair?id=<id from the PC app>"
```
