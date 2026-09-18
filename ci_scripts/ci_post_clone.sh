#!/bin/sh
# Xcode Cloud: add the Firebase config, which is kept out of git.
set -e
cd "$CI_PRIMARY_REPOSITORY_PATH"

if [ -z "$GOOGLE_SERVICE_INFO_PLIST" ]; then
  echo "error: add a secret GOOGLE_SERVICE_INFO_PLIST (base64 of ios/iOS/GoogleService-Info.plist) to the Xcode Cloud workflow"
  exit 1
fi
printf '%s' "$GOOGLE_SERVICE_INFO_PLIST" | base64 --decode > ios/iOS/GoogleService-Info.plist
