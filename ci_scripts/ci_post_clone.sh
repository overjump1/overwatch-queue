#!/bin/sh
# Xcode Cloud: add the Firebase configs, which are kept out of git, and stamp the build's version.
set -e
cd "$CI_PRIMARY_REPOSITORY_PATH"

# TestFlight is the real app, whichever branch it was built from. The project on its own is
# OverQueue Dev (project.yml).
sh ios/make-real.sh

write_config() {
  if [ -z "$1" ]; then
    echo "error: add the secret $2 (base64 of $3) to the Xcode Cloud workflow"
    exit 1
  fi
  printf '%s' "$1" | base64 --decode > "$3"
}

write_config "$GOOGLE_SERVICE_INFO_PLIST" GOOGLE_SERVICE_INFO_PLIST ios/iOS/GoogleService-Info.plist
write_config "$WATCH_GOOGLE_SERVICE_INFO_PLIST" WATCH_GOOGLE_SERVICE_INFO_PLIST ios/Watch/GoogleService-Info.plist

# The project ships on 0.0.0, so a build nobody stamped can't pass for a release (Updates.swift).
if [ -z "$CI_BUILD_NUMBER" ]; then
  echo "error: CI_BUILD_NUMBER isn't set; the build would go out as 0.0.0"
  exit 1
fi
version="2.0.$CI_BUILD_NUMBER"
sed -i '' \
  -e "s/MARKETING_VERSION = [^;]*;/MARKETING_VERSION = $version;/g" \
  -e "s/CURRENT_PROJECT_VERSION = [^;]*;/CURRENT_PROJECT_VERSION = $CI_BUILD_NUMBER;/g" \
  OverQueue.xcodeproj/project.pbxproj
grep -q "MARKETING_VERSION = $version;" OverQueue.xcodeproj/project.pbxproj \
  || { echo "error: couldn't stamp the version into the Xcode project"; exit 1; }
echo "Building $version ($CI_BUILD_NUMBER)"

# Xcode Cloud builds go to TestFlight and on to the App Store, and neither of those wants to hear
# about a GitHub release, so this script marks nothing: the update check is off unless
# .github/workflows/ios-app.yml turns it on (ios/iOS/Updates.swift).
