# OW Queue

See how long you've been in an Overwatch queue on your iPhone, Apple Watch or Android phone. When a match is found, they notify you.

```
PC app (Windows)  ──state──▶  Cloudflare worker  ──FCM──▶  Live Activity (iPhone lock screen, Dynamic Island, Watch Smart Stack)
                                     │  ▲                  + a "Match found" alert to the Watch
                                     │  │
                                     │  └── iPhone, Watch and Android apps read the state here while they're open
                                     └──FCM──▶  Android: ongoing notification with a timer
```

- **pc/** reads your Battle.net presence ("Competitive: In Queue") by asking Battle.net itself, over the debug port it opens in developer mode — and out of Battle.net's own memory whenever that port isn't there. It only uses screen vision in one case: Battle.net says "In Queue" but the queue hasn't been confirmed yet, which is the role-select screen. The queue counts as started once that screen closes. The match counts as found when Battle.net says you're in game.
- **worker/** keeps the latest state for each pairing and sends the Live Activity and Android pushes.
- **ios/** holds the iPhone app, the Live Activity, and the Watch app.
- **android/** holds the Android app (Kotlin, Jetpack Compose).

### What you get notified about

Only two things make a sound: a queue starting ("In queue") and a match being found ("Match found!"), each once. Everything after that updates the Live Activity (on Android, the ongoing notification) quietly:

- A minute after the match is found it turns into **In a match** (good luck, have fun) and counts the match time.
- When the match ends it shows **Not in queue** for 10 minutes, then goes away.
- If you cancel the queue it shows **Not in queue** for a minute, then goes away.

## Updating

Each app checks the [latest release](https://github.com/overjump1/overwatch-queue/releases) when it opens and then every six hours, and there's a **Check for updates** item in each app's menu (the Windows app has the button in its footer, next to its version).

- **Windows**: one click downloads the installer and runs it silently, then the app starts again on the new version. No UAC prompt for a per-user install.
- **Android**: one tap downloads the APK and hands it to the system installer, which asks you to confirm. The first time, Android sends you to *Install unknown apps* to allow it for OW Queue. The new APK only installs over the old one if both were signed with the same key — see `ANDROID_DEBUG_KEYSTORE` under [Building](#building).
- **iPhone**: only says a newer build is out. iOS can't install an app on itself, so the IPA still goes on through AltStore or Sideloadly. TestFlight builds don't check at all, because TestFlight already tells you.

Each app compares itself against **the version in its own asset's filename**, not the release tag. `Release` copies unchanged apps forward, so `v2.0.42` can hold `OWQueue-Setup-2.0.40.exe`; going by the tag would have the Windows app reinstalling its own build forever.

Builds that aren't from a release never check: they're on a `0.x` version (`0.0.0` running `pc/app.py` from source, `0.0.0-dev` for a local Gradle build, `0.0.0-dev.<run>` for a pull request artifact), which is below every release and would claim an update forever.

## Windows app

Download `OWQueue-Setup-<version>.exe` from [Releases](https://github.com/overjump1/overwatch-queue/releases) and run it. It installs for your user only (no admin prompt) and can start the app when you sign in.

To run from source:

```
pip install -r pc/requirements.txt
python pc/app.py
```

On startup it puts Battle.net into developer mode: Battle.net only answers about your presence if it was started with `--remote-debugging-port`, and it rewrites its own auto-start entry and Start Menu shortcut every time it runs, so no persistent setting can arrange for that. If Battle.net isn't running, the app starts it with the flag; if it's running without it, the app closes it and starts it again with it. That happens with Overwatch open too — restarting Battle.net leaves a running game alone, and the app kills Battle.net by name rather than by process tree, so the game is never in the tree that goes. **Restart Battle.net** does the same thing on demand, for a Battle.net you restarted yourself since.

Whenever the port isn't there the app reads Battle.net's process memory instead, so it keeps working either way; the app's Battle.net line says which of the two it's using.

`--presence-source memory` leaves Battle.net alone entirely and only ever reads its memory. `--presence-source auto` uses a debug port if Battle.net already has one open, but never starts or restarts Battle.net. `--battlenet-port` changes the port (default 9222).

Scan the QR code with the iPhone or Android app. Once a phone is paired the code is hidden; **Show QR code** brings it back so you can pair another phone (it hides again once that phone pairs). **Reset QR code** makes a new code; the old one stops working right away and every paired phone has to scan again. Logs are in `%APPDATA%\OWQueue\owqueue.log`.

If Battle.net runs as administrator, the app has to run as administrator as well, or it can't read Battle.net's memory or restart it.

### Releasing

Every push to `main` that changes an app publishes a [release](https://github.com/overjump1/overwatch-queue/releases) (`v2.0.<run>`) with the Android APK, the Windows installer and the iPhone IPA. The `Release` workflow only rebuilds the apps whose files changed since the last release (`android/`, `pc/`, or `ios/` + `project.yml` + the Xcode project, plus each app's workflow) and copies the others from that release unchanged. Pushes that don't touch an app (the worker, the README) don't make a release. To rebuild everything, run the `Release` workflow by hand with **Rebuild every app** ticked.

The `Windows app` workflow builds the app with PyInstaller (`pc/owqueue.spec`) and packs it into a setup exe with Inno Setup (`pc/installer.iss`). Pull requests upload the installer as a workflow artifact.

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

### IPA (GitHub releases)

The `iOS app` workflow builds an unsigned IPA for the GitHub release, for sideloading with AltStore or Sideloadly (they sign it with your Apple ID). TestFlight is still the normal way to install. For the IPA to get pushes, add the same two Firebase configs as GitHub repository secrets `GOOGLE_SERVICE_INFO_PLIST` and `WATCH_GOOGLE_SERVICE_INFO_PLIST`; without them it builds with placeholders.

## Android app

The Android app is a phone app with the same screens as the iPhone app. The Live Activity becomes an ongoing notification with a running timer; on Android 16 it's shown as a Live Update. The notification uses three channels you can tune in the system settings: *Queue status* (quiet updates), *Queue started* and *Match found* (plays the match sound). A paired Wear OS watch gets these notifications too.

An iPhone and an Android phone can both be paired to the same PC.

### Setup

1. In the Firebase console, add an Android app with package name `com.tomerady.overwatchqueue` to the same Firebase project the worker uses. Download its `google-services.json`.
2. For local builds, put it in `android/app/`. It's git-ignored.
3. For CI, add it as the repository secret `GOOGLE_SERVICES_JSON`, holding `base64 -w0 google-services.json`. Without it, CI builds the APK with a placeholder config that can't get pushes.
4. Deploy the worker (`npx wrangler deploy`) so it knows how to push to Android.

### Building

The `Android app` workflow runs the unit tests and builds a debug APK. Pull requests upload it as a workflow artifact, and the `Release` workflow attaches it to the GitHub release. Install it by opening the APK on your phone (allow installs from unknown sources when asked).

For a new APK to install over the old one, every build has to be signed with the same key. Add your debug keystore as the repository secret `ANDROID_DEBUG_KEYSTORE`, holding `base64 -w0 ~/.android/debug.keystore` (Android Studio creates that file; its password is `android`). Without it, each CI build is signed with a new key and has to be uninstalled before the next one installs.

To build locally, open `android/` in Android Studio, or with JDK 17, the Android SDK and Gradle 8.11:

```
cd android
gradle wrapper         # one time: creates ./gradlew
./gradlew :shared:testDebugUnitTest :app:assembleDebug
```

To test against a local worker (`npx wrangler dev`) from the emulator, build with `-PworkerUrl=http://10.0.2.2:8787`, then pair:

```
adb shell am start -a android.intent.action.VIEW -d "owq://pair?id=<id from the PC app>"
```

### Adding a Wear OS app later

Everything that isn't phone UI lives in the `:shared` module: the queue model, the worker client, pairing, the notification and the match alert. A Wear OS app would be a new `:wear` module next to `:app` that depends on `:shared` and registers with `kind: "wearos"`. On the worker, add `"wearos"` to `ANDROID_KINDS` in `worker/src/index.ts`, and it gets the same pushes. Watch out for doubled alerts, because the phone's notifications are already bridged to the watch.
