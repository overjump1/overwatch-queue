"""The version this build reports. CI rewrites VERSION before building (see
.github/workflows/windows-app.yml); running from source leaves it 0.0.0, which turns the
update check off (see updater.py)."""
VERSION = "0.0.0"
