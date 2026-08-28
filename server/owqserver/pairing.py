"""Pairing: one secret the PC keeps, shown as a QR code for the phone to scan.

The token is a random UUID generated on the PC and stored in the user's home directory.
The phone scans it once, keeps it, and sends it in every `hello`; the server hangs up on
a connection that doesn't present it. That's all the security this needs — the socket
never leaves the LAN — but it does mean a flatmate on the same Wi-Fi can't drive your
queue screen, and "unpair" is a button rather than a reinstall.
"""
from __future__ import annotations

import json
import os
import socket
import stat
import uuid

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".overwatch-queue")
CONFIG_PATH = os.path.join(CONFIG_DIR, "pairing.json")

DEFAULT_PORT = 8787
SCHEME = "owq"


class Pairing:
    """The stored token and port, and the URL that goes in the QR code."""

    def __init__(self, token: str = None, port: int = DEFAULT_PORT, path: str = CONFIG_PATH):
        self.path = path
        self.token = token or new_token()
        self.port = port

    @classmethod
    def load(cls, path: str = CONFIG_PATH) -> "Pairing":
        """Reads the stored pairing, generating and saving a new one on first run."""
        try:
            with open(path) as handle:
                stored = json.load(handle)
            pairing = cls(token=str(stored["token"]),
                          port=int(stored.get("port", DEFAULT_PORT)), path=path)
        except (OSError, ValueError, KeyError):
            pairing = cls(path=path)
            pairing.save()
        return pairing

    def save(self):
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.path, "w") as handle:
            json.dump({"token": self.token, "port": self.port}, handle, indent=2)
        try:
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)     # owner only
        except OSError:
            pass                                                  # best effort on Windows

    def regenerate(self):
        """New token — every paired phone stops being paired."""
        self.token = new_token()
        self.save()

    def matches(self, candidate) -> bool:
        return isinstance(candidate, str) and _constant_time_equal(candidate, self.token)

    def url(self, host: str) -> str:
        """What the QR code carries. Short on purpose: a smaller QR scans faster."""
        return "%s://pair?host=%s&port=%d&token=%s" % (SCHEME, host, self.port, self.token)

    @property
    def short_token(self) -> str:
        return "%s…%s" % (self.token[:8], self.token[-4:])


def new_token() -> str:
    """A random UUID. CPython draws uuid4 from the OS entropy source."""
    return str(uuid.uuid4())


def _constant_time_equal(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    difference = 0
    for x, y in zip(a.encode(), b.encode()):
        difference |= x ^ y
    return difference == 0


# ---------------------------------------------------------------- addresses

def local_addresses() -> list:
    """LAN addresses this PC might be reachable at, best guess first.

    A PC with a VPN, Docker or a second NIC has several, and only one of them is the one
    the phone can reach — so the GUI offers the list rather than picking for you.
    """
    found = []

    def add(address):
        if address and address not in found and not address.startswith("127."):
            found.append(address)

    # Which source address the routing table would pick for an off-machine destination.
    # Connecting a UDP socket sends nothing; it just resolves the route.
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))          # TEST-NET-1: reserved, never routed
        add(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        probe.close()

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(info[4][0])
    except OSError:
        pass

    found.sort(key=_address_rank)
    return found or ["127.0.0.1"]


def _address_rank(address: str) -> int:
    """Home-network ranges first — that's where the phone is."""
    if address.startswith("192.168."):
        return 0
    if address.startswith("10."):
        return 1
    if address.startswith("172."):
        return 2
    if address.startswith("169.254."):     # link-local: assigned when DHCP failed
        return 9
    return 5
