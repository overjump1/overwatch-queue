"""Runs a local Mosquitto broker as a managed subprocess.

Mosquitto is what actually accepts phone/watch connections and fans out `owq/snapshot`
with QoS 1 and a retained last value — this module's only job is to keep it running
alongside the Python server (config, auth files, lifecycle) so "run one program" stays
true for whoever starts this thing. No TLS, no persistence to disk: this is the same
LAN-only, no-transport-encryption stance the WebSocket server always had (see
`docs/PROTOCOL.md`), and a restart already implies every session reconnects fresh.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".overwatch-queue", "mosquitto")
CONF_PATH = os.path.join(CONFIG_DIR, "mosquitto.conf")
PASSWD_PATH = os.path.join(CONFIG_DIR, "passwd")
ACL_PATH = os.path.join(CONFIG_DIR, "acl")

# The phone/watch use this identity; the Python server uses the other one, with its own
# password that never goes in a QR code. Keeping them separate is what lets the ACL file
# grant the server write access to `owq/snapshot` without also handing that out to a
# paired device that has no business publishing state.
PHONE_USER = "owq"
SERVER_USER = "owqserver-internal"

_VENDOR_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "vendor", "mosquitto"))


def _find_binary(name: str) -> str:
    """A bundled copy next to the server wins, so installing this app is still one step;
    falling back to PATH is for anyone who'd rather manage their own Mosquitto install."""
    for candidate in (os.path.join(_VENDOR_DIR, name), os.path.join(_VENDOR_DIR, name + ".exe")):
        if os.path.isfile(candidate):
            return candidate
    found = shutil.which(name)
    if found:
        return found
    raise FileNotFoundError(
        "%s wasn't found. Install Mosquitto (https://mosquitto.org/download/) or drop "
        "its binaries into server/vendor/mosquitto/." % name)


class MQTTBroker:
    """Starts, stops and restarts the Mosquitto process this server talks to."""

    def __init__(self, port: int = 1883, log=None):
        self.port = port
        self.log = log or (lambda message: None)
        self._process = None

    def start(self, phone_password: str, server_password: str):
        self.stop()
        self._write_config(phone_password, server_password)
        binary = _find_binary("mosquitto")
        self._process = subprocess.Popen(
            [binary, "-c", CONF_PATH],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        time.sleep(0.3)          # a beat, so a bad config fails here rather than on connect
        if self._process.poll() is not None:
            self._process = None
            raise OSError("Mosquitto exited immediately — is port %d already in use?"
                         % self.port)
        self.log("Mosquitto listening on port %d" % self.port)

    def restart(self, phone_password: str, server_password: str):
        """Rewrites auth and bounces the broker — every connected client (right or wrong
        password) gets dropped and has to reconnect, which is exactly what a pairing-token
        regeneration or a port change needs."""
        self.start(phone_password, server_password)

    def stop(self):
        if self._process is None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._process.kill()
        self._process = None

    def _write_config(self, phone_password: str, server_password: str):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        _write_passwd_file(PASSWD_PATH, {PHONE_USER: phone_password,
                                         SERVER_USER: server_password})
        with open(ACL_PATH, "w") as handle:
            handle.write(
                "user %s\n"
                "topic read owq/snapshot\n"
                "topic read owq/reply/#\n"
                "topic write owq/command/#\n"
                "topic readwrite owq/presence/#\n"
                "\n"
                "user %s\n"
                "topic write owq/snapshot\n"
                "topic write owq/reply/#\n"
                "topic read owq/command/#\n"
                "topic readwrite owq/presence/#\n"
                % (PHONE_USER, SERVER_USER))
        with open(CONF_PATH, "w") as handle:
            handle.write(
                "listener %d 0.0.0.0\n"
                "allow_anonymous false\n"
                "password_file %s\n"
                "acl_file %s\n"
                "persistence false\n"
                "log_dest none\n"
                % (self.port, PASSWD_PATH, ACL_PATH))


def _write_passwd_file(path: str, users: dict):
    """Writes Mosquitto's passwd file via `mosquitto_passwd` — its hash format
    (PBKDF2-SHA512 with a random salt per entry) isn't worth re-implementing here."""
    binary = _find_binary("mosquitto_passwd")
    if os.path.exists(path):
        os.remove(path)
    first = True
    for username, password in users.items():
        args = [binary, "-b"] + (["-c"] if first else []) + [path, username, password]
        subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        first = False
