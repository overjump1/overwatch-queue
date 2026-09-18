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

### What you get notified about

Only two things make a sound: a queue starting ("In queue") and a match being found ("Match found!"), each once. Everything after that updates the Live Activity quietly:

- A minute after the match is found it turns into **In a match** (good luck, have fun) and counts the match time.
- When the match ends it shows **Not in queue** for 10 minutes, then goes away.
- If you cancel the queue it shows **Not in queue** for a minute, then goes away.

## Windows app

Download `OWQueue-Setup-<version>.exe` from [Releases](https://github.com/overjump1/overwatch-queue/releases) and run it. It installs for your user only (no admin prompt) and can start the app when you sign in.

To run from source:

```
pip install -r pc/requirements.txt
python pc/app.py
```

Scan the QR code with the iPhone app. **Reset QR code** makes a new code; the old one stops working right away. Logs are in `%APPDATA%\OWQueue\owqueue.log`.

If Battle.net runs as administrator, the app has to run as administrator as well, or it can't read Battle.net's memory.

### Releasing

The `Windows app` workflow builds the app with PyInstaller (`pc/owqueue.spec`) and packs it into a setup exe with Inno Setup (`pc/installer.iss`). Pull requests and pushes to `main` upload the installer as a workflow artifact. To publish a release, push a version tag:

```
git tag v1.0.0 && git push origin v1.0.0
```

To build locally, install [Inno Setup 6](https://jrsoftware.org/isinfo.php), then from the repo root:

```
pip install -r pc/requirements.txt pyinstaller
pyinstaller --noconfirm pc/owqueue.spec
iscc /DAppVersion=1.0.0 pc\installer.iss
```

## Worker

```
cd worker && npm install
npx wrangler secret put FCM_SERVICE_ACCOUNT   # one time: Firebase service-account JSON
npx wrangler deploy
npm test
```

## iPhone and Watch apps

1. Put `GoogleService-Info.plist` in `ios/iOS/`.
2. Put the Watch app's `GoogleService-Info.plist` (Firebase app "OverwatchQueue Watch", bundle ID `com.tomerady.OverwatchQueue.watchkitapp`) in `ios/Watch/`.
3. Open `OverwatchQueue.xcodeproj`. It's generated from `project.yml`: after adding or removing files, run `xcodegen` in the repo root and commit the result.

### TestFlight (Xcode Cloud)

Pushes to `main` build and upload to TestFlight. `ci_scripts/ci_post_clone.sh` writes both Firebase configs from the workflow's secret environment variables: `GOOGLE_SERVICE_INFO_PLIST` holds `base64 -i ios/iOS/GoogleService-Info.plist` and `WATCH_GOOGLE_SERVICE_INFO_PLIST` holds `base64 -i ios/Watch/GoogleService-Info.plist`.

To test in the Simulator against a local worker (`npx wrangler dev`), run a Debug build with the launch argument `-workerURL http://127.0.0.1:8787`, then pair:

```
xcrun simctl openurl booted "owq://pair?id=<id from the PC app>"
```
