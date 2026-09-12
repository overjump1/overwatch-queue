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
from owqserver.activitytokens import ActivityTokens              # noqa: E402
from owqserver.pushtokens import PushTokens                      # noqa: E402
from owqserver.queueserver import QueueServer                    # noqa: E402
from mqtttestclient import TestClient                             # noqa: E402

PORT = 8899
TOKEN = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
HELLO = {"kind": "phone", "name": "Test iPhone", "appVersion": "1.0"}


def hello():
    # No token here any more — the broker already required it as this connection's MQTT
    # password before a single message could be published. `hello` is now just identity.
    return {"v": 1, "body": {"type": "hello", "data": {"client": HELLO}}}


class ServerTests(unittest.TestCase):
    def setUp(self):
        pairing = Pairing(token=TOKEN, port=PORT, path=os.devnull)
        self.server = QueueServer(pairing, Catalog(), push_tokens=PushTokens(os.devnull),
                        activity_tokens=ActivityTokens(os.devnull))
        self.server.start()
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.server.stop()
        time.sleep(0.05)

    def connect(self, token=TOKEN):
        client = TestClient(port=PORT, token=token)
        self.clients.append(client)
        return client

    # ------------------------------------------------------------ pairing

    def test_a_valid_token_gets_a_snapshot(self):
        # No `hello` needed for this any more: `owq/snapshot` is retained, so subscribing
        # with the right credentials is what hands back the current state instantly —
        # the same thing that makes a reconnect after a dead Wi-Fi recover with no gap.
        client = self.connect()
        message = client.receive()
        self.assertEqual(message["body"]["type"], "snapshot")
        self.assertEqual(message["v"], 1)
        self.assertEqual(message["body"]["data"]["phase"]["type"], "idle")

    def test_a_wrong_token_cannot_even_connect(self):
        # The pairing token is the MQTT password now — a bad one is refused by the
        # broker at CONNECT time, before any application code sees the connection.
        client = TestClient(port=PORT, token="00000000-0000-0000-0000-000000000000")
        try:
            self.assertFalse(client.connected)
        finally:
            client.close()

    def test_commands_are_answered_without_a_hello(self):
        # `hello` is an identity announcement now (for the device list, and for filing a
        # push token under the right kind), not an authorization gate — the broker
        # already gated the connection itself.
        client = self.connect()
        client.receive()                                        # the retained snapshot
        client.send({"v": 1, "body": {"type": "requestSnapshot"}})
        self.assertEqual(client.receive()["body"]["type"], "snapshot")

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

    def test_registering_without_a_kind_or_hello_is_ignored(self):
        # No `unpaired` gate any more — the broker already required the pairing token to
        # connect at all — but a registration still needs to know which kind of device
        # it's for, and that comes from `hello`'s identity or an explicit `kind` field.
        client = self.connect()
        client.receive()                                        # the retained snapshot
        client.send({"v": 1, "body": {"type": "registerPushToken",
                                      "data": {"token": "abc123", "environment": "sandbox"}}})
        time.sleep(0.05)
        self.assertIsNone(self.server.push_tokens.get("phone"))

    def test_a_relayed_watch_token_is_not_filed_under_the_phone(self):
        # The watch has no socket of its own, so this arrives over the iPhone's. Reading
        # the kind from the connection would put the watch's token on top of the phone's
        # and leave both unreachable.
        client = self.connect()
        client.send(hello())
        client.receive()
        client.send({"v": 1, "body": {"type": "registerPushToken",
                                      "data": {"token": "phone-token",
                                               "environment": "sandbox", "kind": "phone"}}})
        client.send({"v": 1, "body": {"type": "registerPushToken",
                                      "data": {"token": "watch-token",
                                               "environment": "sandbox", "kind": "watch"}}})
        time.sleep(0.05)
        self.assertEqual(self.server.push_tokens.get("phone"), ("phone-token", "sandbox"))
        self.assertEqual(self.server.push_tokens.get("watch"), ("watch-token", "sandbox"))

    def test_a_token_without_a_kind_falls_back_to_the_connection(self):
        # Older clients only ever registered over their own socket, so the identity there
        # is the right answer for them.
        client = self.connect()
        client.send(hello())
        client.receive()
        client.send({"v": 1, "body": {"type": "registerPushToken",
                                      "data": {"token": "abc123", "environment": "sandbox"}}})
        time.sleep(0.05)
        self.assertEqual(self.server.push_tokens.get("phone"), ("abc123", "sandbox"))

    def test_ping_is_answered_with_the_clients_own_time_untouched(self):
        client = self.connect()
        client.send(hello())
        client.receive()
        sent = 1700000000.123
        client.send({"v": 1, "body": {"type": "ping", "data": {"clientTime": sent}}})
        reply = client.receive()
        self.assertEqual(reply["body"]["type"], "pong")
        # Echoed exactly: the client subtracts this from its own clock to get the round
        # trip, so any change here would be charged to the offset it derives.
        self.assertEqual(reply["body"]["data"]["clientTime"], sent)
        # Sub-second precision, unlike every other time on this wire.
        self.assertIsInstance(reply["body"]["data"]["serverTime"], float)

    def test_a_ping_without_a_usable_time_is_ignored(self):
        client = self.connect()
        client.send(hello())
        client.receive()
        client.send({"v": 1, "body": {"type": "ping", "data": {"clientTime": "soon"}}})
        client.send({"v": 1, "body": {"type": "requestSnapshot"}})
        # The next thing back is the snapshot, so nothing was sent for the bad ping.
        self.assertEqual(client.receive()["body"]["type"], "snapshot")

    def test_a_malformed_registration_is_ignored(self):
        client = self.connect()
        client.send(hello())
        client.receive()
        client.send({"v": 1, "body": {"type": "registerPushToken",
                                      "data": {"token": "abc123", "environment": "staging"}}})
        time.sleep(0.05)
        self.assertIsNone(self.server.push_tokens.get("phone"))


class DiagnosticTests(unittest.TestCase):
    """A line the device asks the server to write down — see `ClientCommand.diagnostic`."""

    def setUp(self):
        pairing = Pairing(token=TOKEN, port=PORT + 4, path=os.devnull)
        self.server = QueueServer(pairing, Catalog(), push_tokens=PushTokens(os.devnull),
                                  activity_tokens=ActivityTokens(os.devnull))
        self.lines = []
        self.server.log = self.lines.append
        self.client = _FakeClient()

    def test_a_message_is_logged_against_the_client(self):
        self.server._log_diagnostic(self.client, "Live Activity: started 24C67173")
        self.assertEqual(self.lines, ["iPhone says: Live Activity: started 24C67173"])

    def test_newlines_are_flattened_so_one_report_stays_one_line(self):
        self.server._log_diagnostic(self.client, "first\nsecond")
        self.assertEqual(self.lines, ["iPhone says: first second"])

    def test_a_long_message_is_truncated(self):
        self.server._log_diagnostic(self.client, "x" * 5000)
        self.assertEqual(len(self.lines), 1)
        self.assertTrue(self.lines[0].endswith("x" * QueueServer._DIAGNOSTIC_LIMIT))

    def test_nothing_useful_is_ignored(self):
        for junk in (None, "", "   ", 42, {"message": "nested"}):
            self.server._log_diagnostic(self.client, junk)
        self.assertEqual(self.lines, [])


class _FakeClient:
    name = "iPhone"


class _FakeAPNs:
    """Records what would have gone to Apple, without a network in sight."""

    def __init__(self, invalid_kinds=()):
        self.background = []
        self.alerts = []
        self.activity_starts = []
        self.activity_updates = []
        self.activity_ends = []
        self._invalid_kinds = set(invalid_kinds)

    def send_background(self, kind, token, environment, session_id, sequence):
        self.background.append((kind, token, environment, session_id, sequence))
        return "invalid" if kind in self._invalid_kinds else "ok"

    def send_alert(self, kind, token, environment, title, body, session_id, sequence):
        self.alerts.append((kind, token, environment, title, body, session_id, sequence))
        return "invalid" if kind in self._invalid_kinds else "ok"

    def send_activity_start(self, token, environment, attributes, content_state, timestamp, alert=None):
        self.activity_starts.append((token, environment, attributes, content_state, timestamp, alert))
        return "ok"

    def send_activity_update(self, token, environment, content_state, timestamp, alert=None, stale_date=None):
        self.activity_updates.append((token, environment, content_state, timestamp, alert, stale_date))
        return "ok"

    def send_activity_end(self, token, environment, content_state, timestamp, alert=None,
                          dismissal_date=None):
        self.activity_ends.append((token, environment, content_state, timestamp, alert,
                                   dismissal_date))
        return "ok"

    @staticmethod
    def token_is_invalid(response):
        return response == "invalid"


class PushDispatchTests(unittest.TestCase):
    """`_push_apns` in isolation: given some registered tokens, what does it send?"""

    def setUp(self):
        pairing = Pairing(token=TOKEN, port=PORT + 1, path=os.devnull)
        self.server = QueueServer(pairing, Catalog(), push_tokens=PushTokens(os.devnull),
                        activity_tokens=ActivityTokens(os.devnull))
        self.fake = _FakeAPNs()
        self.server.apns = self.fake
        self.server.push_tokens.register("phone", "phone-token", "sandbox")
        self.server.push_tokens.register("watch", "watch-token", "production")

    def test_no_apns_configured_sends_nothing(self):
        self.server.apns = None
        self.server.apply(protocol.searching("quickPlay", "damage", protocol.now(), 30))
        self.assertEqual(self.fake.alerts, [])

    def test_an_invalid_token_is_dropped(self):
        self.fake._invalid_kinds = {"phone"}
        self.server.apply(protocol.searching("quickPlay", "damage", protocol.now(), 30))
        self.assertIsNone(self.server.push_tokens.get("phone"))
        self.assertEqual(self.server.push_tokens.get("watch"), ("watch-token", "production"))

    def test_a_routine_change_alerts_every_registered_kind(self):
        # A background push's silent, low-priority delivery is exactly the failure mode
        # this replaces — Apple can delay or batch it at will. Every phase change, urgent
        # or not, now gets a real, time-sensitive alert instead.
        self.server.apply(protocol.searching("quickPlay", "damage", protocol.now(), 30))
        kinds = {entry[0] for entry in self.fake.alerts}
        self.assertEqual(kinds, {"phone", "watch"})
        for kind, token, environment, title, body, session_id, sequence in self.fake.alerts:
            self.assertEqual((title, body), protocol.notification_copy("searching"))
            self.assertEqual(session_id, self.server.session.session_id)

    def test_an_urgent_change_alerts_both_kinds(self):
        # Push-to-start (the phone's other route to a visible alert with the app fully
        # closed) is best-effort and unreliable in practice, so the phone gets this real
        # alert too rather than depending on the Live Activity alone. The watch has no
        # Live Activity of its own at all, so this is its only independent signal.
        self.server.apply(protocol.searching("quickPlay", "damage", protocol.now(), 30))
        self.server.apply(protocol.match_found("quickPlay", "damage", 30))
        kinds = {entry[0] for entry in self.fake.alerts if entry[3] == "Match Found"}
        self.assertEqual(kinds, {"phone", "watch"})

    def test_an_invalid_token_skips_only_that_kind_s_alert(self):
        self.fake._invalid_kinds = {"watch"}
        # The watch's token is invalid from the start, so it gets forgotten off the very
        # first alert — the one for this routine phase itself, since every phase now sends
        # one. A second, later phase change should find nothing left to alert it with.
        self.server.apply(protocol.searching("quickPlay", "damage", protocol.now(), 30))
        self.server.apply(protocol.match_found("quickPlay", "damage", 30))
        kinds = {entry[0] for entry in self.fake.alerts if entry[3] == "Match Found"}
        self.assertEqual(kinds, {"phone"})
        self.assertIsNone(self.server.push_tokens.get("watch"))

    def test_a_phone_with_an_attached_live_activity_gets_no_direct_alert(self):
        # Once the activity has attached, its own push already carries the alert the
        # player sees (see `_push_activity`) — a second, plain one here would just show up
        # as a duplicate banner in Notification Center on top of the Live Activity update.
        self.server.apply(protocol.searching("quickPlay", "damage", protocol.now(), 30))
        self.server.activity_tokens.register_update(
            self.server.session.session_id, "activity-token", "sandbox")
        self.server.apply(protocol.match_found("quickPlay", "damage", 30))
        kinds = {entry[0] for entry in self.fake.alerts if entry[3] == "Match Found"}
        self.assertEqual(kinds, {"watch"})

    def test_a_phone_falls_back_to_a_direct_alert_before_the_activity_attaches(self):
        # No per-activity token registered yet for this session: push-to-start may not
        # have taken, so the phone still needs its own alert rather than depending on a
        # card that might never show up.
        self.server.apply(protocol.searching("quickPlay", "damage", protocol.now(), 30))
        kinds = {entry[0] for entry in self.fake.alerts if entry[3] == "Queue Started"}
        self.assertEqual(kinds, {"phone", "watch"})


class LiveActivityPushDispatchTests(unittest.TestCase):
    """`_push_activity` in isolation: start, update, end, and the no-duplicate-start
    guard — none of this touches the phone/watch device-token push above."""

    def setUp(self):
        pairing = Pairing(token=TOKEN, port=PORT + 2, path=os.devnull)
        self.server = QueueServer(pairing, Catalog(), push_tokens=PushTokens(os.devnull),
                                  activity_tokens=ActivityTokens(os.devnull))
        self.fake = _FakeAPNs()
        self.server.apns = self.fake

    def test_no_start_token_means_nothing_is_sent(self):
        self.server.apply(protocol.searching("quickPlay", "damage", protocol.now(), 30))
        self.assertEqual(self.fake.activity_starts, [])

    def test_a_fresh_session_with_only_a_start_token_gets_a_start_push(self):
        self.server.activity_tokens.register_start("start-token", "sandbox")
        self.server.apply(protocol.searching("quickPlay", "damage", protocol.now(), 30))
        self.assertEqual(len(self.fake.activity_starts), 1)
        token, environment, attributes, content_state, _timestamp, alert = self.fake.activity_starts[0]
        self.assertEqual(token, "start-token")
        self.assertEqual(environment, "sandbox")
        self.assertEqual(attributes["sessionID"], self.server.session.session_id)
        self.assertEqual(content_state["phase"]["type"], "searching")
        # Every start carries an alert, even a routine phase — verified directly
        # against a real device that a silent push-to-start just doesn't reliably land.
        self.assertEqual(alert, {"title": "Queue Started", "body": "Watching your queue."})

    def test_start_is_not_resent_within_the_retry_cooldown(self):
        self.server.activity_tokens.register_start("start-token", "sandbox")
        self.server.apply(protocol.searching("quickPlay", "damage", protocol.now(), 30))
        self.server.apply(protocol.match_found("quickPlay", "damage", 30))
        self.assertEqual(len(self.fake.activity_starts), 1)

    def test_start_is_retried_once_the_cooldown_elapses(self):
        # A background push-to-start is best-effort — the first one landing is never
        # guaranteed, so a session with no update token yet keeps retrying rather than
        # giving up after one attempt.
        self.server.activity_tokens.register_start("start-token", "sandbox")
        self.server.apply(protocol.searching("quickPlay", "damage", protocol.now(), 30))
        self.server._activity_start_last_sent_at -= self.server._activity_start_retry_seconds + 1
        self.server.apply(protocol.match_found("quickPlay", "damage", 30))
        self.assertEqual(len(self.fake.activity_starts), 2)

    def test_retried_attributes_keep_the_same_startedAt(self):
        # Apple recognises a retry as the same activity by matching attributes — a
        # `startedAt` that drifted between attempts would make each retry look like a
        # brand new activity instead.
        self.server.activity_tokens.register_start("start-token", "sandbox")
        self.server.apply(protocol.searching("quickPlay", "damage", protocol.now(), 30))
        first_started_at = self.fake.activity_starts[0][2]["startedAt"]
        self.server._activity_start_last_sent_at -= self.server._activity_start_retry_seconds + 1
        self.server.apply(protocol.match_found("quickPlay", "damage", 30))
        second_started_at = self.fake.activity_starts[1][2]["startedAt"]
        self.assertEqual(first_started_at, second_started_at)

    def test_a_start_token_registered_mid_session_still_gets_the_current_phase(self):
        # The start token can arrive late (the app only just got its first chance to run
        # since installing) — the very first push-to-start should carry whatever phase
        # is current by then, not the one from when the session began.
        self.server.apply(protocol.searching("quickPlay", "damage", protocol.now(), 30))
        self.server.activity_tokens.register_start("start-token", "sandbox")
        self.server.apply(protocol.match_found("quickPlay", "damage", 30))
        self.assertEqual(len(self.fake.activity_starts), 1)
        content_state, alert = self.fake.activity_starts[0][3], self.fake.activity_starts[0][5]
        self.assertEqual(content_state["phase"]["type"], "matchFound")
        self.assertEqual(alert, {"title": "Match Found",
                                 "body": "You're being pulled into the game — get back to your PC."})

    def test_a_patch_to_the_same_phase_carries_no_alert(self):
        # The estimate ticking down, a mode correction — nothing about the queue itself
        # moved, so this is the one case that stays silent even though the phase kind it
        # patches (`searching`, just below) got a real alert of its own a moment earlier.
        self.server.activity_tokens.register_update(
            self.server.session.session_id, "activity-token", "sandbox")
        self.server.apply(protocol.searching("quickPlay", "damage", protocol.now(), 30))
        self.server.patch(estimatedWait=99)
        alert = self.fake.activity_updates[-1][4]
        self.assertIsNone(alert)

    def test_every_real_phase_change_carries_an_alert_now(self):
        # Not just the three that used to be `URGENT_KINDS` — a transit app buzzes at
        # every stop, not only the ones it judges important, and this is now the same.
        self.server.activity_tokens.register_update(
            self.server.session.session_id, "activity-token", "sandbox")
        self.server.apply(protocol.searching("quickPlay", "damage", protocol.now(), 30))
        self.server.apply(protocol.match_found("quickPlay", "damage", 30))
        self.server.apply(protocol.map_vote(["kings-row"]))
        self.server.apply(protocol.hero_select("quickPlay", "damage"))
        self.server.apply(protocol.in_game("quickPlay"))
        alerts = [update[4] for update in self.fake.activity_updates]
        self.assertEqual(alerts, [
            {"title": "Queue Started", "body": "Watching your queue."},
            {"title": "Match Found",
             "body": "You're being pulled into the game — get back to your PC."},
            {"title": "Map Vote", "body": "Pick where you want to play."},
            {"title": "Hero Select", "body": "Choose your hero."},
            {"title": "Match Started", "body": "Good luck, have fun."},
        ])

    def test_a_cancellation_carries_an_alert_on_its_end_push(self):
        self.server.activity_tokens.register_update(
            self.server.session.session_id, "activity-token", "sandbox")
        self.server.apply(protocol.searching("quickPlay", "damage", protocol.now(), 30))
        self.server.apply(protocol.cancelled("userLeft"))
        alert = self.fake.activity_ends[-1][4]
        self.assertEqual(alert, {"title": "Queue Cancelled",
                                 "body": "The queue ended without a match."})

    def test_idle_reached_directly_stays_silent(self):
        # `idle` is bookkeeping, not a real trip ending — `cancelled` is the event
        # actually worth an alert for. (`reset()` always mints a fresh session id, which
        # would never match a token registered under the old one; `apply(idle())`
        # directly is what lets this compare against the same registered token above.)
        self.server.activity_tokens.register_update(
            self.server.session.session_id, "activity-token", "sandbox")
        self.server.apply(protocol.searching("quickPlay", "damage", protocol.now(), 30))
        self.server.apply(protocol.idle())
        alert = self.fake.activity_ends[-1][4]
        self.assertIsNone(alert)

    def test_a_registered_update_token_is_used_instead_of_starting_again(self):
        self.server.activity_tokens.register_start("start-token", "sandbox")
        self.server.apply(protocol.searching("quickPlay", "damage", protocol.now(), 30))
        self.server.activity_tokens.register_update(
            self.server.session.session_id, "activity-token", "sandbox")
        self.server.apply(protocol.match_found("quickPlay", "damage", 30))
        self.assertEqual(len(self.fake.activity_starts), 1)     # the earlier one only
        self.assertEqual(len(self.fake.activity_updates), 1)
        token, environment, content_state, _timestamp, alert, _stale = self.fake.activity_updates[0]
        self.assertEqual(token, "activity-token")
        self.assertEqual(content_state["phase"]["type"], "matchFound")

    def test_an_update_token_from_a_previous_session_is_not_reused(self):
        self.server.activity_tokens.register_update("some-other-session", "stale-token", "sandbox")
        self.server.apply(protocol.searching("quickPlay", "damage", protocol.now(), 30))
        self.assertEqual(self.fake.activity_updates, [])

    def test_idle_with_a_registered_update_token_sends_end_and_forgets_it(self):
        self.server.activity_tokens.register_update(
            self.server.session.session_id, "activity-token", "sandbox")
        self.server.apply(protocol.searching("quickPlay", "damage", protocol.now(), 30))
        self.server.apply(protocol.cancelled("userLeft"))
        self.assertEqual(len(self.fake.activity_ends), 1)
        self.assertIsNone(self.server.activity_tokens.update_token(self.server.session.session_id))

    def test_idle_with_no_update_token_sends_nothing_and_does_not_start(self):
        self.server.activity_tokens.register_start("start-token", "sandbox")
        # `apply(idle())` isn't a real transition anywhere in play, but `reset()` is the
        # equivalent path back to idle and should behave the same way: no start, no end.
        self.server.reset()
        self.assertEqual(self.fake.activity_starts, [])
        self.assertEqual(self.fake.activity_ends, [])

    def test_a_new_session_can_start_a_new_activity_after_the_old_one_ended(self):
        self.server.activity_tokens.register_start("start-token", "sandbox")
        self.server.apply(protocol.searching("quickPlay", "damage", protocol.now(), 30))
        self.server.apply(protocol.cancelled("userLeft"))
        self.server.reset()
        self.server.apply(protocol.searching("quickPlay", "damage", protocol.now(), 30))
        self.assertEqual(len(self.fake.activity_starts), 2)


if __name__ == "__main__":
    unittest.main()
