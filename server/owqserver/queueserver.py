"""The server proper: holds the queue state, speaks to the phones, plays scenarios.

Everything that changes state goes through `apply`, which checks the transition table,
bumps the sequence and pushes a snapshot to every paired client — so the phone, the
watch, the Live Activity and the widgets all move together, exactly as they will when
this is reading the real game instead of a person clicking buttons.
"""
from __future__ import annotations

import datetime
import json
import random
import threading
import time

from . import protocol
from .activitytokens import ActivityTokens
from .apns import APNsClient, APNsConfig
from .protocol import QueueSession
from .pushrelay import PushRelayClient, PushRelayConfig
from .pushtokens import PushTokens
from .wsserver import CLOSE_POLICY_VIOLATION, WebSocketServer

HEARTBEAT_SECONDS = 10
# A client that hasn't said a valid hello by then is not a phone of ours.
HELLO_TIMEOUT_SECONDS = 10


class QueueServer:
    def __init__(self, pairing, catalog, log=None, push_tokens=None, activity_tokens=None):
        self.pairing = pairing
        self.catalog = catalog
        self.session = QueueSession()
        self.log = log or (lambda message: None)

        # Whether a cancel from the phone is honoured. Overwatch usually won't let you
        # out, and the app is built to accept being told so — this is how you rehearse it.
        self.honour_cancel = True

        self._lock = threading.RLock()
        self._scenario = None
        self._scenario_stop = threading.Event()
        self.on_change = None            # called (on any thread) after state moves

        # Pushing is entirely optional: with neither of these configured, a backgrounded
        # phone or watch just won't be woken until it reconnects on its own.
        #
        # `apns.json` (a real Apple Auth Key, held locally) wins if it's there — that's
        # the advanced path for someone who'd rather not depend on anyone else's relay.
        # Otherwise, `push_relay.json` points at the maintainer's Cloudflare Worker (see
        # `relay/`), which is what lets this server push without an Apple Developer
        # account of its own. Same interface either way — nothing below this cares which.
        self.push_tokens = push_tokens if push_tokens is not None else PushTokens()
        # A Live Activity's own push tokens — see `_push_activity` for how the two get
        # used differently from the phone/watch tokens above.
        self.activity_tokens = activity_tokens if activity_tokens is not None else ActivityTokens()
        # Guards against re-sending push-to-start for a session that's already been
        # started that way — Apple has no "start, but only if it isn't already running"
        # semantics, so a second `start` event for the same activity is just a bug here.
        self._activity_start_sent_for = None
        self.apns = self._make_push_client()

        self.ws = WebSocketServer(port=pairing.port,
                                  on_open=self._client_connected,
                                  on_message=self._client_said,
                                  on_close=self._client_gone,
                                  # Resolved late: the GUI replaces `log` after the
                                  # server is built, and socket-level messages should
                                  # follow it there rather than into the original.
                                  log=lambda message: self.log(message))
        self._heartbeat = None
        self._running = False

    def _make_push_client(self):
        log = lambda message: self.log(message)                          # noqa: E731
        apns_config = APNsConfig.load()
        if apns_config:
            return APNsClient(apns_config, log=log)
        relay_config = PushRelayConfig.load()
        if relay_config:
            return PushRelayClient(relay_config, log=log)
        return None

    # ------------------------------------------------------------ lifecycle

    def start(self):
        self.ws.port = self.pairing.port
        self.ws.start()
        self._running = True
        self._heartbeat = threading.Thread(target=self._heartbeat_loop, daemon=True,
                                           name="heartbeat")
        self._heartbeat.start()
        self.log("Listening on port %d" % self.pairing.port)

    def stop(self):
        self._running = False
        self.stop_scenario()
        self.ws.stop()
        if self.apns:
            self.apns.close()

    @property
    def clients(self) -> list:
        return [c for c in self.ws.clients if c.authorized]

    # ------------------------------------------------------------ state

    def apply(self, phase: dict) -> bool:
        """Moves to `phase` if the transition is legal, and tells everyone."""
        with self._lock:
            if not self.session.advance(phase):
                self.log("Refused %s → %s (the app would refuse it too)"
                         % (self.session.kind, phase["type"]))
                return False
            self._broadcast_snapshot()
        self._changed()
        return True

    def patch(self, **fields) -> bool:
        with self._lock:
            if not self.session.patch(**fields):
                return False
            self._broadcast_snapshot()
        self._changed()
        return True

    def shift_start(self, seconds: float) -> bool:
        with self._lock:
            if not self.session.shift_start(seconds):
                return False
            self._broadcast_snapshot()
        self._changed()
        return True

    def reset(self):
        """Starts a fresh session — new id, sequence back to zero."""
        self.stop_scenario()
        with self._lock:
            self.session.reset()
            self._broadcast_snapshot()
        self.log("New session")
        self._changed()

    def _broadcast_snapshot(self):
        self.ws.broadcast(self.session.snapshot())
        self._push_apns()

    def _push_apns(self):
        """Reaches whichever paired kinds have registered a device token — regardless of
        whether they're also connected over the socket right now, since a client that's
        about to go stale benefits from the wake-up landing just before it notices.
        A duplicate delivery is harmless: the client discards anything not newer than its
        current `sequence`."""
        if not self.apns:
            return
        session_id, sequence, kind = self.session.session_id, self.session.sequence, self.session.kind
        for client_kind, device_token, environment in list(self.push_tokens.items()):
            response = self.apns.send_background(
                client_kind, device_token, environment, session_id, sequence)
            if self.apns.token_is_invalid(response):
                self.push_tokens.forget(client_kind)
                continue
            if kind in protocol.URGENT_KINDS:
                title, body = protocol.notification_copy(kind)
                self.apns.send_alert(client_kind, device_token, environment,
                                     title, body, session_id, sequence)
        self._push_activity(session_id, sequence, kind)

    def _push_activity(self, session_id: str, sequence: int, kind: str):
        """Keeps the Live Activity itself current, independent of whether the phone's
        process is even running:

        - Already has a per-activity token for *this* session → push an update (or, on
          idle/cancelled, an end — the activity's own equivalent of `LiveActivityController
          .end()`, reachable even when nothing local is around to call that).
        - No token yet, but a phase has actually started → push-to-start creates the
          activity from nothing, once per session. Sent alongside — never instead of —
          the phone's own silent wake-up above, since only that wake-up gives the app a
          chance to attach to the newly-created activity and register a real per-activity
          token for every update after this one.
        """
        content_state = protocol.content_state(self.session.phase, sequence)
        timestamp = int(protocol.now().timestamp())
        alert = None
        if kind in protocol.URGENT_KINDS:
            title, body = protocol.notification_copy(kind)
            alert = {"title": title, "body": body}

        activity = self.activity_tokens.update_token(session_id)
        if activity:
            token, environment = activity
            if kind in ("idle", "cancelled"):
                self.apns.send_activity_end(token, environment, content_state, timestamp)
                self.activity_tokens.forget_update()
            else:
                self.apns.send_activity_update(token, environment, content_state,
                                               timestamp, alert=alert)
            return

        if kind in ("idle", "cancelled") or self._activity_start_sent_for == session_id:
            return
        start = self.activity_tokens.start_token()
        if not start:
            return
        token, environment = start
        attributes = {"sessionID": session_id,
                     "startedAt": protocol.reference_date_seconds(protocol.now())}
        self.apns.send_activity_start(token, environment, attributes, content_state,
                                      timestamp, alert=alert)
        self._activity_start_sent_for = session_id

    def _changed(self):
        if self.on_change:
            self.on_change()

    # ------------------------------------------------------------ clients

    def _client_connected(self, client):
        client.connected_at = time.time()
        self.log("%s connected" % client.host)

    def _client_gone(self, client):
        self.log("%s disconnected" % client.name)
        self._changed()

    def _client_said(self, client, raw: str):
        try:
            message = json.loads(raw)
            body = message["body"]
            kind = body["type"]
        except (ValueError, KeyError, TypeError):
            self.log("%s sent something that isn't a command: %.60s" % (client.host, raw))
            return

        if message.get("v") != protocol.VERSION:
            client.send(protocol.error(
                "version", "This server speaks protocol v%d." % protocol.VERSION))
            return

        if kind == "hello":
            self._handle_hello(client, body.get("data") or {})
            return

        if not client.authorized:
            client.send(protocol.error("unpaired", "Say hello with a pairing token first."))
            client.close(CLOSE_POLICY_VIOLATION, "unpaired")
            return

        data = body.get("data") or {}
        if kind == "requestSnapshot":
            client.send(self.session.snapshot())
        elif kind == "voteMap":
            self._vote(client, data.get("mapKey"))
        elif kind == "selectHero":
            self._select_hero(client, data.get("heroKey"))
        elif kind == "cancelQueue":
            self._cancel(client)
        elif kind == "registerPushToken":
            self._register_push_token(client, data.get("token"), data.get("environment"))
        elif kind == "registerActivityPushToken":
            self._register_activity_push_token(
                data.get("sessionID"), data.get("token"), data.get("environment"))
        elif kind == "registerActivityStartToken":
            self._register_activity_start_token(data.get("token"), data.get("environment"))
        else:
            self.log("%s sent an unknown command: %s" % (client.name, kind))

    def _handle_hello(self, client, data: dict):
        identity = data.get("client") or {}
        client.identity = identity
        if not self.pairing.matches(data.get("token")):
            self.log("%s tried to connect without a valid token" % client.host)
            client.send(protocol.error(
                "pairing_required",
                "This phone isn't paired. Scan the QR code in the server window."))
            client.close(CLOSE_POLICY_VIOLATION, "pairing required")
            return
        client.authorized = True
        self.log("%s paired" % client.name)
        client.send(self.session.snapshot())
        self._changed()

    # -- commands from the phone --------------------------------------------

    def _vote(self, client, map_key):
        with self._lock:
            if self.session.kind != "mapVote" or not map_key:
                return
            options = self.session.phase["data"]["options"]
            for option in options:
                if option["mapKey"] == map_key:
                    option["votes"] += 1
            self.session.patch(options=options, myVote=map_key)
            self._broadcast_snapshot()
        self.log("%s voted for %s" % (client.name, self.catalog.name_for_map(map_key)))
        self._changed()

    def _select_hero(self, client, hero_key):
        with self._lock:
            if self.session.kind != "heroSelect" or not hero_key:
                return
            self.session.patch(myHeroKey=hero_key)
            self._broadcast_snapshot()
        self.log("%s picked %s" % (client.name, self.catalog.name_for_hero(hero_key)))
        self._changed()

    def _register_push_token(self, client, token, environment):
        kind = (client.identity or {}).get("kind")
        if kind not in ("phone", "watch") or not token or environment not in ("sandbox", "production"):
            return
        self.push_tokens.register(kind, token, environment)
        self.log("%s registered for push notifications" % client.name)

    def _register_activity_push_token(self, session_id, token, environment):
        if not session_id or not token or environment not in ("sandbox", "production"):
            return
        self.activity_tokens.register_update(session_id, token, environment)
        self.log("Live Activity registered for push updates")

    def _register_activity_start_token(self, token, environment):
        if not token or environment not in ("sandbox", "production"):
            return
        self.activity_tokens.register_start(token, environment)
        self.log("Registered for Live Activity push-to-start")

    def _cancel(self, client):
        if not self.honour_cancel:
            # What the real thing does when the game won't let go: say so, and keep
            # sending the true state rather than faking a cancellation.
            self.log("%s asked to cancel — refused" % client.name)
            client.send(protocol.error("no_cancel", "Too late to leave this one."))
            client.send(self.session.snapshot())
            return
        self.log("%s cancelled the queue" % client.name)
        self.stop_scenario()
        self.apply(protocol.cancelled("userLeft"))

    # ------------------------------------------------------------ heartbeat

    def _heartbeat_loop(self):
        while self._running:
            time.sleep(1)
            beat = protocol.heartbeat()
            for client in self.ws.clients:
                if client.authorized:
                    if time.time() - getattr(client, "last_beat", 0) >= HEARTBEAT_SECONDS:
                        client.last_beat = time.time()
                        try:
                            client.send(beat)
                        except OSError:
                            pass
                elif time.time() - getattr(client, "connected_at", 0) > HELLO_TIMEOUT_SECONDS:
                    self.log("%s never said hello — dropping" % client.host)
                    client.close(CLOSE_POLICY_VIOLATION, "no hello")

    # ------------------------------------------------------------ scenarios

    @property
    def scenario_running(self) -> bool:
        return self._scenario is not None and self._scenario.is_alive()

    def play(self, scenario):
        """Runs a scripted timeline on a background thread, so the real transitions and
        their animations can be watched end to end rather than jumped between."""
        self.stop_scenario()
        self.reset()
        self._scenario_stop = threading.Event()
        stop = self._scenario_stop

        def run():
            for build, hold in scenario.steps(self.catalog):
                if stop.is_set():
                    return
                self.apply(build())
                if stop.wait(hold):
                    return
            self._changed()

        self.log("Scenario: %s" % scenario.name)
        self._scenario = threading.Thread(target=run, daemon=True, name="scenario")
        self._scenario.start()
        self._changed()

    def stop_scenario(self):
        if self._scenario_stop:
            self._scenario_stop.set()
        self._scenario = None


class Scenario:
    """A scripted timeline. Steps build their phase when they run, so the timestamps
    inside are always relative to the moment the step actually starts."""

    def __init__(self, key, name, detail, build_steps):
        self.key = key
        self.name = name
        self.detail = detail
        self._build_steps = build_steps

    def steps(self, catalog) -> list:
        return self._build_steps(catalog)


def _fast_quick_play(catalog):
    maps = catalog.map_vote_options("quickPlay")
    heroes = [h["key"] for h in catalog.heroes_for("damage", "quickPlay")]
    return [
        (lambda: protocol.searching("quickPlay", "damage", protocol.now(), 45), 12),
        (lambda: protocol.match_found("quickPlay", "damage", 12, 10), 4),
        (lambda: protocol.map_vote(maps, 20,
                                   {key: random.randint(0, 3) for key in maps}), 20),
        (lambda: protocol.hero_select("quickPlay", "damage", maps[0], 30,
                                      random.sample(heroes, min(2, len(heroes)))), 30),
        (lambda: protocol.in_game("quickPlay", maps[0], random.choice(heroes)), 60),
    ]


def _long_comp_tank(catalog):
    maps = catalog.map_vote_options("competitive")
    return [
        (lambda: protocol.searching("competitive", "tank",
                                    protocol.now() - _minutes(6), 240), 25),
        (lambda: protocol.match_found("competitive", "tank", 385, 10), 5),
        (lambda: protocol.hero_select("competitive", "tank", maps[0], 40), 40),
        (lambda: protocol.in_game("competitive", maps[0]), 60),
    ]


def _match_fell_apart(catalog):
    return [
        (lambda: protocol.searching("quickPlay", "support", protocol.now(), 30), 8),
        (lambda: protocol.match_found("quickPlay", "support", 8, 10), 6),
        (lambda: protocol.cancelled("matchCancelled"), 3),
        (lambda: protocol.searching("quickPlay", "support", protocol.now(), 40), 30),
    ]


def _mystery_heroes(catalog):
    maps = catalog.map_vote_options("mysteryHeroes")
    heroes = [h["key"] for h in catalog.heroes_for("open", "mysteryHeroes")]
    return [
        (lambda: protocol.searching("mysteryHeroes", "open", protocol.now(), 20), 10),
        (lambda: protocol.match_found("mysteryHeroes", "open", 10, 10), 4),
        (lambda: protocol.in_game("mysteryHeroes", maps[0], random.choice(heroes)), 60),
    ]


def _minutes(count):
    return datetime.timedelta(minutes=count)


SCENARIOS = [
    Scenario("fastQuickPlay", "Fast Quick Play",
             "12s search → found → vote → heroes → game", _fast_quick_play),
    Scenario("longCompTank", "Long Comp Tank",
             "Starts 6 minutes deep, past its estimate", _long_comp_tank),
    Scenario("matchFellApart", "Match Falls Apart",
             "Match found, then cancelled — back to searching", _match_fell_apart),
    Scenario("mysteryHeroes", "Mystery Heroes",
             "No role queue, no hero select", _mystery_heroes),
]
