"""A minimal WebSocket client, so the tests can talk to the server for real."""
from __future__ import annotations

import base64
import json
import os
import socket
import struct


class TestClient:
    def __init__(self, host="127.0.0.1", port=8787, path="/queue", timeout=5):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n"
                           "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
                           "Sec-WebSocket-Version: 13\r\n\r\n"
                           % (path, host, port, key)).encode())
        self.response = self._read_headers()

    @property
    def upgraded(self) -> bool:
        return self.response.startswith("HTTP/1.1 101")

    def _read_headers(self) -> str:
        buffer = b""
        while b"\r\n\r\n" not in buffer:
            chunk = self.sock.recv(1024)
            if not chunk:
                break
            buffer += chunk
        return buffer.decode("latin-1")

    def send(self, message: dict):
        payload = json.dumps(message).encode()
        mask = os.urandom(4)
        header = bytearray([0x81])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        else:
            header.append(0x80 | 126)
            header += struct.pack("!H", length)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + mask + masked)

    def receive(self):
        """Next text frame as a parsed dict, or None if the socket closed."""
        while True:
            header = self._exactly(2)
            if header is None:
                return None
            opcode = header[0] & 0x0F
            length = header[1] & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._exactly(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._exactly(8))[0]
            payload = self._exactly(length) if length else b""
            if opcode == 0x8:
                return None
            if opcode == 0x1:
                return json.loads(payload.decode())

    def _exactly(self, count):
        buffer = b""
        while len(buffer) < count:
            try:
                chunk = self.sock.recv(count - len(buffer))
            except socket.timeout:
                return None
            if not chunk:
                return None
            buffer += chunk
        return buffer

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass
