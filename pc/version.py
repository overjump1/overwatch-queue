"""What CI stamps into a build (see .github/workflows/windows-app.yml). Running from source
leaves VERSION at 0.0.0, which turns the update check off (see updater.py), and the addresses
on the real worker and the real releases."""
VERSION = "0.0.0"
# A dev build talks to the dev worker and updates only from the dev prerelease (see release.yml).
WORKER_URL = "https://overwatch-queue-push-relay.tomerady.workers.dev"
RELEASE_API = "https://api.github.com/repos/overjump1/overwatch-queue/releases/latest"
