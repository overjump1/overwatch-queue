"""End-to-end: a real socket, a real handshake, real frames."""
from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from owqserver import protocol                                   # noqa: E402
from owqserver.catalog import Catalog                            # noqa: E402
from owqserver.pairing import Pairing                            # noqa: E402
from owqserver.pushtokens import PushTokens                      # noqa: E402
from owqserver.queueserver import QueueServer                    # noqa: E402
from wsclient import TestClient                                  # noqa: E402

PORT = 8899
TOKEN = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
HELLO = {"kind": "phone", "name": "Test iPhone", "appVersion": "1.0"}


def hello(token=TOKEN):
    return {"v": 1, "body": {"type": "hello", "data": {"token": token, "client": HELLO}}}


class ServerTests(unittest.TestCase):
    def setUp(self):
        pairing = Pairing(token=TOKEN, port=PORT, path=os.devnull)
        self.server = QueueServer(pairing, Catalog(), push_tokens=PushTokens(os.devnull))
        self.server.start()
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.server.stop()
        time.sleep(0.05)

    def connect(self, path="/queue"):
        client = TestClient(port=PORT, path=path)
        self.clients.append(client)
        return client

    # ------------------------------------------------------------ handshake

    def test_upgrades_on_the_queue_path(self):
        self.assertTrue(self.connect().upgraded)

    def test_refuses_other_paths(self):
        self.assertIn("404", self.connect(path="/nope").response.splitlines()[0])

    # ------------------------------------------------------------ pairing

    def test_a_valid_token_gets_a_snapshot(self):
        client = self.connect()
        client.send(hello())
        message = client.receive()
        self.assertEqual(message["body"]["type"], "snapshot")
        self.assertEqual(message["v"], 1)
        self.assertEqual(message["body"]["data"]["phase"]["type"], "idle")

    def test_a_wrong_token_is_told_why_and_hung_up_on(self):
        client = self.connect()
        client.send(hello(token="00000000-0000-0000-0000-000000000000"))
        message = client.receive()
        self.assertEqual(message["body"]["type"], "error")
        self.assertEqual(message["body"]["data"]["code"], "pairing_required")
        self.assertIsNone(client.receive())          # socket closed behind the error

    def test_commands_before_hello_are_refused(self):
        client = self.connect()
        client.send({"v": 1, "body": {"type": "requestSnapshot"}})
        self.assertEqual(client.receive()["body"]["data"]["code"], "unpaired")

    def test_an_unpaired_client_gets_no_snapshots(self):
        listener = self.connect()
        listener.send(hello(token="nope"))
        listener.receive()                            # the error
        self.server.apply(protocol.searching("competitive", "tank", protocol.now(), 240))
        self.assertIsNone(listener.receive())

    # ------------------------------------------------------------ state

    def test_snapshots_follow_state_changes(self):
        client = self.connect()
        client.send(hello())
        client.receive()
        self.server.apply(protocol.searching("competitive", "tank", protocol.now(), 240))
        message = client.receive()
        phase = message["body"]["data"]["phase"]
        self.assertEqual(phase["type"], "searching")
        self.assertEqual(phase["data"]["role"], "tank")
        self.assertEqual(message["body"]["data"]["sequence"], 1)

    def test_sequence_climbs_and_the_session_id_holds(self):
        client = self.connect()
        client.send(hello())
        first = client.receive()["body"]["data"]
        self.server.apply(protocol.searching("quickPlay", "damage", protocol.now(), 30))
        self.server.apply(protocol.match_found("quickPlay", "damage", 30))
        second = client.receive()["body"]["data"]
        third = client.receive()["body"]["data"]
        self.assertEqual([first["sequence"], second["sequence"], third["sequence"]], [0, 1, 2])
        self.assertEqual({first["sessionID"], second["sessionID"], third["sessionID"]},
                         {first["sessionID"]})

    def test_illegal_transitions_are_refused(self):
        self.assertFalse(self.server.apply(protocol.in_game("competitive")))
        self.assertEqual(self.server.session.kind, "idle")

    def test_request_snapshot_is_answered(self):
        client = self.connect()
        client.send(hello())
        client.receive()
        client.send({"v": 1, "body": {"type": "requestSnapshot"}})
        self.assertEqual(client.receive()["body"]["type"], "snapshot")

    # ------------------------------------------------------------ commands

    def test_a_vote_comes_back_in_the_next_snapshot(self):
        client = self.connect()
        client.send(hello())
        client.receive()
        self.server.apply(protocol.searching("quickPlay", "damage", protocol.now(), 30))
        self.server.apply(protocol.match_found("quickPlay", "damage", 30))
        self.server.apply(protocol.map_vote(["ilios", "havana"], 25))
        for _ in range(3):
            client.receive()
        client.send({"v": 1, "body": {"type": "voteMap", "data": {"mapKey": "ilios"}}})
        phase = client.receive()["body"]["data"]["phase"]
        self.assertEqual(phase["data"]["myVote"], "ilios")
        self.assertEqual([o["votes"] for o in phase["data"]["options"]], [1, 0])

    def test_a_hero_pick_comes_back_in_the_next_snapshot(self):
        client = self.connect()
        client.send(hello())
        client.receive()
        self.server.apply(protocol.searching("competitive", "tank", protocol.now(), 30))
        self.server.apply(protocol.match_found("competitive", "tank", 30))
        self.server.apply(protocol.hero_select("competitive", "tank", "havana", 40))
        for _ in range(3):
            client.receive()
        client.send({"v": 1, "body": {"type": "selectHero", "data": {"heroKey": "orisa"}}})
        self.assertEqual(client.receive()["body"]["data"]["phase"]["data"]["myHeroKey"], "orisa")

    def test_a_refused_cancel_says_so_and_holds_the_state(self):
        self.server.honour_cancel = False
        client = self.connect()
        client.send(hello())
        client.receive()
        self.server.apply(protocol.searching("competitive", "tank", protocol.now(), 240))
        client.receive()
        client.send({"v": 1, "body": {"type": "cancelQueue"}})
        self.assertEqual(client.receive()["body"]["data"]["code"], "no_cancel")
        self.assertEqual(client.receive()["body"]["data"]["phase"]["type"], "searching")

    def test_an_honoured_cancel_moves_to_cancelled(self):
        client = self.connect()
        client.send(hello())
        client.receive()
        self.server.apply(protocol.searching("competitive", "tank", protocol.now(), 240))
        client.receive()
        client.send({"v": 1, "body": {"type": "cancelQueue"}})
        self.assertEqual(client.receive()["body"]["data"]["phase"]["type"], "cancelled")

    # ------------------------------------------------------------ two devices

    def test_both_devices_see_the_same_snapshot(self):
        phone, watch = self.connect(), self.connect()
        for client in (phone, watch):
            client.send(hello())
            client.receive()
        self.server.apply(protocol.searching("competitive", "support", protocol.now(), 90))
        self.assertEqual(phone.receive()["body"]["data"], watch.receive()["body"]["data"])

    # ------------------------------------------------------------ push tokens

    def test_registering_a_push_token_is_remembered(self):
        client = self.connect()
        client.send(hello())
        client.receive()
        client.send({"v": 1, "body": {"type": "registerPushToken",
                                      "data": {"token": "abc123", "environment": "sandbox"}}})
        time.sleep(0.05)
        self.assertEqual(self.server.push_tokens.get("phone"), ("abc123", "sandbox"))

    def test_registering_before_hello_is_ignored(self):
        client = self.connect()
        client.send({"v": 1, "body": {"type": "registerPushToken",
                                      "data": {"token": "abc123", "environment": "sandbox"}}})
        self.assertEqual(client.receive()["body"]["data"]["code"], "unpaired")
        self.assertIsNone(self.server.push_tokens.get("phone"))

    def test_a_malformed_registration_is_ignored(self):
        client = self.connect()
        client.send(hello())
        client.receive()
        client.send({"v": 1, "body": {"type": "registerPushToken",
                                      "data": {"token": "abc123", "environment": "staging"}}})
        time.sleep(0.05)
        self.assertIsNone(self.server.push_tokens.get("phone"))


class _FakeAPNs:
    """Records what would have gone to Apple, without a network in sight."""

    def __init__(self, invalid_kinds=()):
        self.background = []
        self.alerts = []
        self._invalid_kinds = set(invalid_kinds)

    def send_background(self, kind, token, environment, session_id, sequence):
        self.background.append((kind, token, environment, session_id, sequence))
        return "invalid" if kind in self._invalid_kinds else "ok"

    def send_alert(self, kind, token, environment, title, body, session_id, sequence):
        self.alerts.append((kind, token, environment, title, body, session_id, sequence))
        return "ok"

    @staticmethod
    def token_is_invalid(response):
        return response == "invalid"


class PushDispatchTests(unittest.TestCase):
    """`_push_apns` in isolation: given some registered tokens, what does it send?"""

    def setUp(self):
        pairing = Pairing(token=TOKEN, port=PORT + 1, path=os.devnull)
        self.server = QueueServer(pairing, Catalog(), push_tokens=PushTokens(os.devnull))
        self.fake = _FakeAPNs()
        self.server.apns = self.fake
        self.server.push_tokens.register("phone", "phone-token", "sandbox")
        self.server.push_tokens.register("watch", "watch-token", "production")

    def test_no_apns_configured_sends_nothing(self):
        self.server.apns = None
        self.server.apply(protocol.searching("quickPlay", "damage", protocol.now(), 30))
        self.assertEqual(self.fake.background, [])

    def test_a_routine_change_pushes_silently_to_every_registered_kind(self):
        self.server.apply(protocol.searching("quickPlay", "damage", protocol.now(), 30))
        kinds = {entry[0] for entry in self.fake.background}
        self.assertEqual(kinds, {"phone", "watch"})
        self.assertEqual(self.fake.alerts, [])

    def test_an_urgent_phase_also_gets_an_alert(self):
        self.server.apply(protocol.searching("quickPlay", "damage", protocol.now(), 30))
        self.server.apply(protocol.match_found("quickPlay", "damage", 30))
        kinds = {entry[0] for entry in self.fake.alerts}
        self.assertEqual(kinds, {"phone", "watch"})
        self.assertEqual(self.fake.alerts[0][3], "Match Found")

    def test_an_invalid_token_is_dropped_and_gets_no_alert(self):
        self.fake._invalid_kinds = {"phone"}
        self.server.apply(protocol.searching("quickPlay", "damage", protocol.now(), 30))
        self.server.apply(protocol.match_found("quickPlay", "damage", 30))
        self.assertIsNone(self.server.push_tokens.get("phone"))
        self.assertEqual(self.server.push_tokens.get("watch"), ("watch-token", "production"))
        self.assertNotIn("phone", {entry[0] for entry in self.fake.alerts})


if __name__ == "__main__":
    unittest.main()
