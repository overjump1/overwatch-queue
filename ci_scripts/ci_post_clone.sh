#!/bin/sh
# Xcode Cloud: add the Firebase configs, which are kept out of git.
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
