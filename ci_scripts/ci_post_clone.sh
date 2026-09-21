#!/bin/sh
# Xcode Cloud: add the Firebase configs, which are kept out of git, and stamp the build's version.
set -e
cd "$CI_PRIMARY_REPOSITORY_PATH"

write_config() {
  if [ -z "$1" ]; then
    echo "error: add the secret $2 (base64 of $3) to the Xcode Cloud workflow"
    exit 1
  fi
  printf '%s' "$1" | base64 --decode > "$3"
}

write_config "$GOOGLE_SERVICE_INFO_PLIST" GOOGLE_SERVICE_INFO_PLIST ios/iOS/GoogleService-Info.plist
write_config "$WATCH_GOOGLE_SERVICE_INFO_PLIST" WATCH_GOOGLE_SERVICE_INFO_PLIST ios/Watch/GoogleService-Info.plist

# The project ships on 0.0.0 so that a build nobody stamped can't claim to be a release (see
# ios/iOS/Updates.swift). Xcode Cloud numbers its builds itself, so TestFlight shows a version that
# moves: 2.0.<Xcode Cloud build>. That counter isn't GitHub's run number, so this version and a
# release's 2.0.<run> don't line up — which is fine, because these builds never look at GitHub.
if [ -z "$CI_BUILD_NUMBER" ]; then
  echo "error: CI_BUILD_NUMBER isn't set; the build would go out as 0.0.0"
  exit 1
fi
version="2.0.$CI_BUILD_NUMBER"
sed -i '' \
  -e "s/MARKETING_VERSION = [^;]*;/MARKETING_VERSION = $version;/g" \
  -e "s/CURRENT_PROJECT_VERSION = [^;]*;/CURRENT_PROJECT_VERSION = $CI_BUILD_NUMBER;/g" \
  OverwatchQueue.xcodeproj/project.pbxproj
grep -q "MARKETING_VERSION = $version;" OverwatchQueue.xcodeproj/project.pbxproj \
  || { echo "error: couldn't stamp the version into the Xcode project"; exit 1; }
echo "Building $version ($CI_BUILD_NUMBER)"

# TestFlight and the App Store hand out their own updates, so these builds don't check GitHub.
# A plist key rather than a guess at runtime: it's the one thing that says where the build came from.
/usr/libexec/PlistBuddy -c "Delete :OWQTestFlightBuild" ios/iOS/Info.plist 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :OWQTestFlightBuild bool true" ios/iOS/Info.plist
