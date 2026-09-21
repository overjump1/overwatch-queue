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

# The project ships on 0.0.0, so a build nobody stamped can't pass for a release (Updates.swift).
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

# TestFlight hands out its own updates, so these builds don't check GitHub. PlistBuddy writes a
# file of its own when the path is wrong, hence the checks either side of the write.
plist=ios/iOS/Info.plist
[ -f "$plist" ] || { echo "error: $plist isn't there; has the app's Info.plist moved?"; exit 1; }
/usr/libexec/PlistBuddy -c "Delete :OWQTestFlightBuild" "$plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :OWQTestFlightBuild bool true" "$plist"
marked=$(/usr/libexec/PlistBuddy -c "Print :OWQTestFlightBuild" "$plist" 2>/dev/null || true)
[ "$marked" = true ] || { echo "error: couldn't mark the build as TestFlight's"; exit 1; }
