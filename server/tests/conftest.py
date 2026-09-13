"""Keeps the test run away from the real `~/.overwatch-queue`.

Every config module resolves its path from `os.path.expanduser("~")` at import time, and
several tests build a bare `QueueServer(...)` with no fakes. Pointed at a real home
directory, those servers loaded the real device tokens — and every phase change a test
drove went out as a real push to a real phone and watch. Redirecting the home directory here, before any test module imports `owqserver`,
makes that impossible rather than something each test has to remember.
"""
import os
import tempfile

_HOME = tempfile.mkdtemp(prefix="owq-test-home-")
os.environ["HOME"] = _HOME
os.environ["USERPROFILE"] = _HOME
os.environ.pop("HOMEDRIVE", None)
os.environ.pop("HOMEPATH", None)
# The push relay's URL is built into `fcm.py` rather than read from the home directory,
# so moving home isn't enough on its own: an empty URL turns pushing off outright.
os.environ["OWQ_PUSH_RELAY_URL"] = ""

assert os.path.expanduser("~") == _HOME, "tests must never see the real home directory"
