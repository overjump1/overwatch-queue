"""What CI stamps into a build (see .github/workflows/windows-app.yml). Running from source
leaves VERSION at 0.0.0, which turns the update check off (see updater.py)."""
VERSION = "0.0.0"
# True in a build of OverQueue Dev, the separate app dev builds are (see channel.py).
DEV = False
