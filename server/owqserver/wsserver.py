"""A small RFC 6455 WebSocket server — enough of one to talk to the app.

Threaded rather than asyncio, because the other half of this program is a Tk event
loop: one thread accepts, one thread per phone reads, and everything that touches the
GUI is handed back through a callback the GUI drains on its own timer. Nothing here
knows about queues or pairing tokens; that lives in `queueserver.py`.
"""
from __future__ import annotations

import base64
import hashlib
import socket
import struct
import threading

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# A frame this large is either a bug or someone probing; hang up rather than buffer it.
MAX_FRAME = 1 << 20

CLOSE_NORMAL = 1000
CLOSE_GOING_AWAY = 1001
CLOSE_PROTOCOL_ERROR = 1002
CLOSE_TOO_BIG = 1009
CLOSE_POLICY_VIOLATION = 1008          # what we send when a token doesn't match


class Client:
    """One connected phone or watch."""

    def __init__(self, sock: socket.socket, address):
        self.sock = sock
        self.address = address
        self.host = address[0]
        self._send_lock = threading.Lock()
        self._closed = False
        # Filled in by the layer above once the client says hello.
        self.identity = None
        self.authorized = False

    @property
    def name(self) -> str:
        if self.identity:
            return "%s (%s)" % (self.identity.get("name", "?"), self.identity.get("kind", "?"))
        return self.host

    def send(self, text: str):
        self._send_frame(0x1, text.encode("utf-8"))

    def ping(self):
        self._send_frame(0x9, b"")

    def close(self, code: int = CLOSE_NORMAL, reason: str = ""):
        if self._closed:
            return
        try:
            self._send_frame(0x8, struct.pack("!H", code) + reason.encode("utf-8")[:123])
        except OSError:
            pass
        self._shutdown()

    def _shutdown(self):
        self._closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def _send_frame(self, opcode: int, payload: bytes):
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(length)
        elif length < (1 << 16):
            header.append(126)
            header += struct.pack("!H", length)
        else:
            header.append(127)
            header += struct.pack("!Q", length)
        with self._send_lock:
            if self._closed:
                return
            try:
                self.sock.sendall(bytes(header) + payload)
            except OSError:
                self._closed = True
                raise


class WebSocketServer:
    """Accepts connections on `path` and calls back on the reading thread.

    `on_open`, `on_message` and `on_close` all run on a connection's own thread, so
    whatever they touch has to be safe to touch from there.
    """

    def __init__(self, host="0.0.0.0", port=8787, path="/queue",
                 on_open=None, on_message=None, on_close=None, log=None):
        self.host = host
        self.port = port
        self.path = path
        self.on_open = on_open
        self.on_message = on_message
        self.on_close = on_close
        self.log = log or (lambda message: None)

        self._server_sock = None
        self._accept_thread = None
        self._running = False
        self._clients = []
        self._clients_lock = threading.Lock()

    # ------------------------------------------------------------ lifecycle

    def start(self):
        if self._running:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.listen(8)
        sock.settimeout(0.5)               # so stop() doesn't have to wait on accept()
        self._server_sock = sock
        self._running = True
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True,
                                               name="ws-accept")
        self._accept_thread.start()

    def stop(self):
        self._running = False
        for client in self.clients:
            client.close(CLOSE_GOING_AWAY, "server stopping")
        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass
            self._server_sock = None

    @property
    def clients(self) -> list:
        with self._clients_lock:
            return list(self._clients)

    def broadcast(self, text: str, only_authorized: bool = True):
        for client in self.clients:
            if only_authorized and not client.authorized:
                continue
            try:
                client.send(text)
            except OSError:
                self._drop(client)

    # ------------------------------------------------------------ internals

    def _accept_loop(self):
        while self._running:
            try:
                sock, address = self._server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._serve, args=(sock, address), daemon=True,
                             name="ws-client-%s" % address[0]).start()

    def _serve(self, sock: socket.socket, address):
        client = Client(sock, address)
        try:
            if not self._handshake(client):
                return
        except OSError:
            client._shutdown()
            return

        with self._clients_lock:
            self._clients.append(client)
        if self.on_open:
            self.on_open(client)

        try:
            self._read_loop(client)
        except (OSError, ValueError) as error:
            self.log("%s dropped: %s" % (client.host, error))
        finally:
            self._drop(client)

    def _drop(self, client: Client):
        with self._clients_lock:
            if client not in self._clients:
                return
            self._clients.remove(client)
        client._shutdown()
        if self.on_close:
            self.on_close(client)

    def _handshake(self, client: Client) -> bool:
        raw = self._read_request(client.sock)
        if raw is None:
            client._shutdown()
            return False
        request_line, _, rest = raw.partition("\r\n")
        headers = {}
        for line in rest.split("\r\n"):
            name, _, value = line.partition(":")
            if name:
                headers[name.strip().lower()] = value.strip()

        parts = request_line.split(" ")
        path = parts[1].split("?")[0] if len(parts) > 1 else ""
        key = headers.get("sec-websocket-key")

        if headers.get("upgrade", "").lower() != "websocket" or not key:
            self._refuse(client.sock, "400 Bad Request",
                         "This is the Overwatch queue server. It speaks WebSocket.\n")
            return False
        if path != self.path:
            self._refuse(client.sock, "404 Not Found",
                         "Nothing here. The queue lives at %s.\n" % self.path)
            return False

        accept = base64.b64encode(hashlib.sha1((key + _GUID).encode()).digest()).decode()
        client.sock.sendall(("HTTP/1.1 101 Switching Protocols\r\n"
                             "Upgrade: websocket\r\n"
                             "Connection: Upgrade\r\n"
                             "Sec-WebSocket-Accept: %s\r\n\r\n" % accept).encode())
        return True

    @staticmethod
    def _refuse(sock: socket.socket, status: str, body: str):
        payload = body.encode()
        try:
            sock.sendall(("HTTP/1.1 %s\r\n"
                          "Content-Type: text/plain; charset=utf-8\r\n"
                          "Content-Length: %d\r\n"
                          "Connection: close\r\n\r\n" % (status, len(payload))).encode() + payload)
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        finally:
            sock.close()

    @staticmethod
    def _read_request(sock: socket.socket):
        """Reads up to the blank line that ends the HTTP headers."""
        sock.settimeout(10)
        buffer = b""
        while b"\r\n\r\n" not in buffer:
            chunk = sock.recv(1024)
            if not chunk:
                return None
            buffer += chunk
            if len(buffer) > 16384:
                return None
        sock.settimeout(None)
        return buffer.split(b"\r\n\r\n")[0].decode("latin-1")

    def _read_loop(self, client: Client):
        pending_opcode = None
        pending = bytearray()
        while self._running:
            frame = self._read_frame(client.sock)
            if frame is None:
                return
            fin, opcode, payload = frame

            if opcode == 0x8:                                   # close
                client.close(CLOSE_NORMAL)
                return
            if opcode == 0x9:                                   # ping
                client._send_frame(0xA, payload)
                continue
            if opcode == 0xA:                                   # pong
                continue

            if opcode in (0x1, 0x2):
                pending_opcode, pending = opcode, bytearray(payload)
            elif opcode == 0x0:
                if pending_opcode is None:
                    raise ValueError("continuation frame with nothing to continue")
                pending += payload
            else:
                raise ValueError("unknown opcode 0x%x" % opcode)

            if not fin:
                continue
            message = bytes(pending)
            pending_opcode, pending = None, bytearray()
            if self.on_message:
                self.on_message(client, message.decode("utf-8", "replace"))

    @staticmethod
    def _read_frame(sock: socket.socket):
        header = _recv_exactly(sock, 2)
        if header is None:
            return None
        fin = bool(header[0] & 0x80)
        opcode = header[0] & 0x0F
        masked = bool(header[1] & 0x80)
        length = header[1] & 0x7F

        if length == 126:
            extended = _recv_exactly(sock, 2)
            if extended is None:
                return None
            length = struct.unpack("!H", extended)[0]
        elif length == 127:
            extended = _recv_exactly(sock, 8)
            if extended is None:
                return None
            length = struct.unpack("!Q", extended)[0]
        if length > MAX_FRAME:
            raise ValueError("frame of %d bytes is too large" % length)

        # Every frame from a client must be masked; an unmasked one is a broken client.
        if not masked:
            raise ValueError("client sent an unmasked frame")
        mask = _recv_exactly(sock, 4)
        if mask is None:
            return None
        payload = _recv_exactly(sock, length) if length else b""
        if payload is None:
            return None
        unmasked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        return fin, opcode, unmasked


def _recv_exactly(sock: socket.socket, count: int):
    buffer = bytearray()
    while len(buffer) < count:
        chunk = sock.recv(count - len(buffer))
        if not chunk:
            return None
        buffer += chunk
    return bytes(buffer)
