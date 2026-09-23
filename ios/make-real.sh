#!/bin/sh
# Makes the Xcode project build the real OverQueue. Left alone, every build is OverQueue Dev
# (project.yml): Xcode on your Mac, pull requests, the dev branch's IPA. Only two builds are the
# real app, and both run this: Xcode Cloud's, for TestFlight (ci_scripts/ci_post_clone.sh), and
# main's release IPA (.github/workflows/ios-app.yml).
#
# It edits the generated project rather than project.yml because that's what gets built, and
# checks every setting landed: a sed that matches nothing succeeds anyway, and would ship the dev
# app to TestFlight without a word.
set -e
project="$(dirname "$0")/../OverQueue.xcodeproj/project.pbxproj"

real() {
  sed -i.bak "s|\([[:space:]]$1\) = [^;]*;|\1 = \"$2\";|g" "$project"
  rm -f "$project.bak"
  # Once for Debug, once for Release.
  if [ "$(grep -c "[[:space:]]$1 = \"$2\";" "$project")" -ne 2 ]; then
    echo "error: couldn't set $1 in the Xcode project"
    exit 1
  fi
}

real OWQ_BUNDLE_ID com.tomerady.OverwatchQueue
real OWQ_APP_NAME OverQueue
real OWQ_PAIR_SCHEME overqueue
real OWQ_LEGACY_PAIR_SCHEME owq
real OWQ_WORKER_URL https://overwatch-queue-push-relay.tomerady.workers.dev
real OWQ_RELEASE_API https://api.github.com/repos/overjump1/overwatch-queue/releases/latest
echo "Building the real OverQueue"
