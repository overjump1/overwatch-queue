"""What CI stamps into a build (see .github/workflows/windows-app.yml). Running from source
leaves VERSION at 0.0.0, which turns the update check off (see updater.py)."""
VERSION = "0.0.0"
# OverQueue Dev (see channel.py): running from source, the dev branch's builds, pull requests.
# Only main's release stamps this False, making the real app.
DEV = True
