"""The server's own connection to the local Mosquitto broker — replaces `wsserver.py`.

Nothing here accepts a socket directly any more; the broker does that. This is just a
`paho-mqtt` client wrapper with the same shape `queueserver.py` used to get from
`WebSocketServer` (`start`/`stop`/`clients`/`on_open`/`on_message`/`on_close`), so the
switch stays mostly in this file and in how a "connection" is broadcast to.

A paired phone or watch is tracked by the `clientID` it puts in its own topics
(`owq/command/<clientID>`, `owq/presence/<clientID>`) rather than by a `socket.socket` —
that ID is the one thing MQTT hands us for free that a raw TCP connection also did.
Authorization itself has already happened by the time any message from a device reaches
here: the broker's password file and ACL (see `mqttbroker.py`) refuse the CONNECT outright
for a bad or missing pairing token, so nothing on this side re-checks a token — the
`hello` message a client still sends is just an identity announcement now, not a gate.
"""
from __future__ import annotations

import json
import threading
import time

import paho.mqtt.client as mqtt

TOPIC_SNAPSHOT = "owq/snapshot"
TOPIC_HEARTBEAT = "owq/heartbeat"
TOPIC_COMMAND_WILDCARD = "owq/command/+"
TOPIC_PRESENCE_WILDCARD = "owq/presence/+"


def reply_topic(client_id: str) -> str:
    return "owq/reply/%s" % client_id


class Client:
    """One paired phone or watch, tracked by its MQTT clientID."""

    def __init__(self, client_id: str):
        self.client_id = client_id
        self.host = client_id
        self.identity = None
        self.authorized = False
        self.connected_at = time.time()

    @property
    def name(self) -> str:
        if self.identity:
            return "%s (%s)" % (self.identity.get("name", "?"), self.identity.get("kind", "?"))
        return self.client_id


class MQTTClient:
    """Connects to the local broker as the privileged `owqserver-internal` user."""

    def __init__(self, host="127.0.0.1", port=1883, username="owqserver-internal",
                 password="", on_open=None, on_message=None, on_close=None, log=None):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.on_open = on_open
        self.on_message = on_message
        self.on_close = on_close
        self.log = log or (lambda message: None)

        self._clients: dict[str, Client] = {}
        self._clients_lock = threading.Lock()
        self._pending_subscribe_mids: set = set()
        self._subscribed_event = threading.Event()
        self._mqtt = self._build_client()

    def _build_client(self) -> mqtt.Client:
        client = mqtt.Client(client_id="owqserver", protocol=mqtt.MQTTv311)
        client.username_pw_set(self.username, self.password)
        client.on_connect = self._on_connect
        client.on_subscribe = self._on_subscribe
        client.on_message = self._on_message
        client.on_disconnect = lambda *a: self.log("Lost the connection to the MQTT broker")
        return client

    # ------------------------------------------------------------ lifecycle

    def start(self):
        self._pending_subscribe_mids = set()
        self._subscribed_event = threading.Event()
        self._mqtt = self._build_client()          # picks up any changed host/port/password
        self._mqtt.connect(self.host, self.port, keepalive=15)
        self._mqtt.loop_start()
        # A client that connects and publishes immediately after this call returns
        # (right after pairing, or right after a token rotation forces every device to
        # reconnect) needs the broker to already know about our subscriptions — MQTT
        # only delivers to subscribers present at publish time, and the command topic
        # isn't retained, so a subscribe that's still in flight silently drops the
        # message with no error on either side. Block until the broker has actually
        # acknowledged both subscriptions (or give up after a few seconds and let the
        # normal on-connect retry path take over) instead of returning the moment the
        # TCP connect attempt was merely *started*.
        if not self._subscribed_event.wait(timeout=5):
            self.log("Timed out waiting for the MQTT broker to confirm our subscriptions")

    def stop(self):
        self._mqtt.loop_stop()
        try:
            self._mqtt.disconnect()
        except OSError:
            pass

    @property
    def clients(self) -> list:
        with self._clients_lock:
            return list(self._clients.values())

    def publish_snapshot(self, text: str):
        self._mqtt.publish(TOPIC_SNAPSHOT, text, qos=1, retain=True)

    def publish_heartbeat(self, text: str):
        # Not retained: a stale heartbeat is worse than none, since a client trusts its
        # `serverTime` as fresh by construction (see `protocol.heartbeat`'s docstring).
        self._mqtt.publish(TOPIC_HEARTBEAT, text, qos=0, retain=False)

    def reply(self, client_id: str, text: str):
        self._mqtt.publish(reply_topic(client_id), text, qos=0, retain=False)

    # ------------------------------------------------------------ internals

    def _on_connect(self, client, userdata, flags, rc):
        if rc != 0:
            self.log("Couldn't connect to the MQTT broker (rc=%s)" % rc)
            return
        _, command_mid = client.subscribe(TOPIC_COMMAND_WILDCARD, qos=1)
        _, presence_mid = client.subscribe(TOPIC_PRESENCE_WILDCARD, qos=1)
        self._pending_subscribe_mids = {command_mid, presence_mid}

    def _on_subscribe(self, client, userdata, mid, granted_qos):
        self._pending_subscribe_mids.discard(mid)
        if not self._pending_subscribe_mids:
            self._subscribed_event.set()

    def _on_message(self, client, userdata, msg):
        parts = msg.topic.split("/", 2)
        if len(parts) != 3:
            return
        _, kind, client_id = parts
        if kind == "presence":
            self._handle_presence(client_id, msg.payload)
        elif kind == "command":
            self._handle_command(client_id, msg.payload.decode("utf-8", "replace"))

    def _handle_presence(self, client_id: str, payload: bytes):
        try:
            online = bool(json.loads(payload.decode("utf-8")).get("online", False))
        except ValueError:
            online = False
        if online:
            with self._clients_lock:
                is_new = client_id not in self._clients
                found = self._clients.setdefault(client_id, Client(client_id))
            if is_new and self.on_open:
                self.on_open(found)
        else:
            with self._clients_lock:
                found = self._clients.pop(client_id, None)
            if found and self.on_close:
                self.on_close(found)

    def _handle_command(self, client_id: str, raw: str):
        with self._clients_lock:
            found = self._clients.setdefault(client_id, Client(client_id))
        if self.on_message:
            self.on_message(found, raw)
