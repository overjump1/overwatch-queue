"""A minimal MQTT client standing in for a phone or watch, so the tests can talk to the
real local broker the server manages — see `owqserver.mqttbroker`/`mqttclient`.

Requires `mosquitto`/`mosquitto_passwd` to be installed and reachable (bundled under
server/vendor/mosquitto/ or on PATH) — `QueueServer.start()` launches a real broker, the
same one a phone or watch would actually talk to.
"""
from __future__ import annotations

import queue
import uuid

import paho.mqtt.client as mqtt


class TestClient:
    """Connects as `client_id`, subscribes to its own reply/snapshot topics, and hands
    back received envelopes in arrival order through `receive()`."""

    def __init__(self, port, token, host="127.0.0.1", client_id=None, timeout=5):
        self.client_id = client_id or str(uuid.uuid4())
        self.timeout = timeout
        self._inbox: "queue.Queue" = queue.Queue()
        self._connected: "queue.Queue" = queue.Queue()

        self._mqtt = mqtt.Client(client_id=self.client_id, protocol=mqtt.MQTTv311)
        self._mqtt.username_pw_set("owq", token)
        self._mqtt.will_set("owq/presence/%s" % self.client_id, b'{"online":false}', retain=True)
        self._mqtt.on_connect = self._on_connect
        self._mqtt.on_message = self._on_message
        self._mqtt.connect(host, port, keepalive=15)
        self._mqtt.loop_start()

    @property
    def connected(self) -> bool:
        """Blocks briefly for the CONNACK; True only for rc == 0 (right credentials)."""
        try:
            return self._connected.get(timeout=self.timeout) == 0
        except queue.Empty:
            return False

    def _on_connect(self, client, userdata, flags, rc):
        self._connected.put(rc)
        if rc != 0:
            return
        client.subscribe("owq/snapshot", qos=1)
        client.subscribe("owq/reply/%s" % self.client_id, qos=0)
        client.publish("owq/presence/%s" % self.client_id, b'{"online":true}', retain=True)

    def _on_message(self, client, userdata, msg):
        import json
        self._inbox.put(json.loads(msg.payload.decode("utf-8")))

    def send(self, message: dict):
        import json
        self._mqtt.publish("owq/command/%s" % self.client_id,
                           json.dumps(message), qos=1)

    def receive(self):
        """Next message as a parsed dict, or None if nothing arrived within the timeout."""
        try:
            return self._inbox.get(timeout=self.timeout)
        except queue.Empty:
            return None

    def close(self):
        try:
            self._mqtt.loop_stop()
            self._mqtt.disconnect()
        except OSError:
            pass
