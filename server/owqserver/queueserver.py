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

from . import protocol, queuemapvote, queueroles, queuevision, queuewatch, vision
from .activitytokens import ActivityTokens
from .apns import APNsClient, APNsConfig
from .heroimages import TemplateStore
from .protocol import QueueSession
from .pushrelay import PushRelayClient, PushRelayConfig
from .pushtokens import PushTokens
from .wsserver import CLOSE_POLICY_VIOLATION, WebSocketServer

HEARTBEAT_SECONDS = 10
# A client that hasn't said a valid hello by then is not a phone of ours.
HELLO_TIMEOUT_SECONDS = 10


class QueueServer:
    def __init__(self, pairing, catalog, log=None, push_tokens=None, activity_tokens=None,
                 vision_enabled=False, queue_vision_enabled=False):
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

        # Reading the game is opt-in, and off by default even where it would work.
        #
        # Defaulting to "on wherever the libraries import" was tried and is a trap: the
        # test suite constructs a real server, and on a machine with the extras installed
        # a single `phase_for("heroSelect")` reached out, focused whatever Overwatch was
        # running and typed arrow keys into it. Nothing that merely *builds* a server
        # should be able to take the mouse. `run.py` turns it on for the actual app.
        self.vision_enabled = bool(vision_enabled) and vision.VISION_AVAILABLE
        # Watching for a queue is opt-in for the same reason, though the stakes are lower:
        # it only ever reads the top of the screen and never takes the mouse. What it does
        # do is *drive the session on its own*, so a test that built a server with it on
        # would find its phase moving underneath it.
        self.queue_vision_enabled = (bool(queue_vision_enabled) and
                                     queuevision.QUEUE_VISION_AVAILABLE)
        self.queue_watcher = None
        self.templates = TemplateStore(catalog, log=lambda message: self.log(message))
        self.role_templates = queueroles.RoleIconStore(log=lambda message: self.log(message))
        # Where each hero sat the last time we looked, so picking one doesn't have to pay
        # for a fresh scan. Cleared whenever the phase moves, since the roster is only on
        # screen during hero select and stale coordinates would click on nothing.
        self._roster = {}

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
        # A background push-to-start is best-effort like any other APNs push — it can be
        # delayed or dropped, and there's no acknowledgement. So this isn't a one-shot: a
        # session with no update token yet keeps retrying `start` on every subsequent
        # change, throttled by the cooldown below so a burst of fast changes (or repeated
        # manual testing) doesn't hammer it. `startedAt` is fixed the first time a session
        # sends one, not recomputed per retry — same attributes every time is what lets
        # Apple's system recognise a retry as the same activity rather than a new one.
        self._activity_session_id = None
        self._activity_started_at = None
        self._activity_start_last_sent_at = 0.0
        self._activity_start_retry_seconds = 20
        # ...and when the retries don't take either, `_check_activity_fallback` gives up on
        # the Live Activity and sends a plain notification instead. Apple accepts a
        # push-to-start with a 200 whether or not it ever delivers one, so the only thing
        # this side can observe is the app never coming back with a per-activity token.
        # Wait that out, then say it the one way that demonstrably arrives.
        self._activity_start_first_sent_at = None
        self._activity_fallback_sent_for = None
        self._activity_fallback_delay_seconds = 15
        # The phase an update alert was last sent for, so a patch to the *same* phase's
        # data — the estimate ticking, a mode correction — never earns one of its own.
        # See `_push_activity` for why every real phase change now does.
        self._last_activity_kind = None
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

    def watch_queue(self, controls):
        """Starts reading the queue off the screen, and returns the watcher or None.

        Takes the panel's `Controls` rather than building its own, because the mode and
        role a queue is in are only half readable — see `queuewatch.QueueWatcher`. Called
        from `run.py` and the panel; doing it here keeps the enabled-or-not decision in
        the one place that already makes it for hero select.
        """
        if not self.queue_vision_enabled or self.queue_watcher is not None:
            return self.queue_watcher
        self.queue_watcher = queuewatch.QueueWatcher(
            controls, log=lambda message: self.log(message),
            role_templates=self.role_templates,
            hero_templates=self.templates, hero_keys=self.hero_keys)
        self.queue_watcher.start()
        return self.queue_watcher

    def stop(self):
        self._running = False
        self.stop_scenario()
        if self.queue_watcher:
            self.queue_watcher.stop()
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
            if phase["type"] != "heroSelect":
                # The roster is only on screen during hero select; anywhere else those
                # coordinates point at whatever has replaced it.
                self._roster = {}
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
        current `sequence`.

        Silent for a routine change — the Live Activity below is meant to surface those on
        the phone without a redundant banner on top. An urgent phase is different: it gets
        a real, time-sensitive alert on both kinds, phone included, rather than trusting
        the Live Activity alone — push-to-start (what would otherwise be the phone's only
        route to a visible alert with the app fully closed) is best-effort in the same way
        the comment on `_push_activity` describes, and in practice unreliable enough that
        the alert can't be the only thing carrying an urgent phase to a closed app. The
        watch has no Live Activity of its own at all, so this alert is its only
        independent signal once out of the phone's WatchConnectivity range."""
        if not self.apns:
            return
        session_id, sequence, kind = self.session.session_id, self.session.sequence, self.session.kind
        urgent = kind in protocol.URGENT_KINDS
        for client_kind, device_token, environment in list(self.push_tokens.items()):
            response = self.apns.send_background(
                client_kind, device_token, environment, session_id, sequence)
            if self.apns.token_is_invalid(response):
                self.push_tokens.forget(client_kind)
                continue
            if urgent:
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
          activity from nothing. Retried on every subsequent change (throttled — see
          `_activity_start_retry_seconds`) until an update token registers, since a
          background push is best-effort and the first attempt landing is never
          guaranteed. Sent alongside — never instead of — the phone's own silent wake-up
          above, since only that wake-up gives the app a chance to attach to the activity
          and register a real per-activity token for every update after this one.

        Every real phase change carries a real alert — sound, haptic, a brief peek — the
        way a transit app's Live Activity buzzes at each stop rather than only the ones
        it judges important: the point is feeling the trip move, not just being able to
        check it. A *patch* to the phase already showing — the estimate ticking down, a
        mode correction — is not that: nothing about the queue actually moved, so it
        stays silent, which is what `_last_activity_kind` is for telling apart from a
        real `apply`.

        A *start* always carries one. A wholly silent push-to-start was never once seen
        to arrive, while an identical one with an alert sometimes does — which makes some
        sense, since there's nothing on screen yet for a silent content refresh to land
        on.

        "Sometimes" is the honest word. Push-to-start is accepted with a 200 and then, often
        enough to design around, simply never delivered — with nothing on this side to say
        so. `_check_activity_fallback` is what covers that case: when the retries here have
        had their chance and the app still hasn't come back with a per-activity token, it
        stops trying to conjure a card and just sends a notification the player can tap.
        """
        content_state = protocol.content_state(self.session.phase, sequence)
        timestamp = int(protocol.now().timestamp())
        title, body = protocol.notification_copy(kind)
        start_alert = {"title": title, "body": body}
        changed_kind = kind != self._last_activity_kind
        update_alert = start_alert if changed_kind else None

        activity = self.activity_tokens.update_token(session_id)
        if activity:
            # There's a live card and the app is attached to it: push-to-start took, so
            # there's nothing for the fallback to rescue.
            self._activity_start_first_sent_at = None
            token, environment = activity
            if kind in ("idle", "cancelled"):
                end_alert = start_alert if kind == "cancelled" and changed_kind else None
                self.apns.send_activity_end(token, environment, content_state, timestamp,
                                            alert=end_alert)
                self.activity_tokens.forget_update()
                self._activity_session_id = None
                self._last_activity_kind = None
            else:
                self._last_activity_kind = kind
                self.apns.send_activity_update(token, environment, content_state,
                                               timestamp, alert=update_alert)
            return

        if kind in ("idle", "cancelled"):
            self._activity_session_id = None
            self._activity_start_first_sent_at = None
            return
        start = self.activity_tokens.start_token()
        if not start:
            return

        if self._activity_session_id != session_id:
            self._activity_session_id = session_id
            self._activity_started_at = protocol.reference_date_seconds(protocol.now())
            self._activity_start_last_sent_at = 0.0            # a new session always retries immediately
            # A fresh queue gets a fresh chance at both the card and, failing that, the
            # one fallback notification it's allowed.
            self._activity_start_first_sent_at = None
            self._activity_fallback_sent_for = None

        now = time.time()
        if now - self._activity_start_last_sent_at < self._activity_start_retry_seconds:
            return
        self._activity_start_last_sent_at = now

        token, environment = start
        attributes = {"sessionID": session_id, "startedAt": self._activity_started_at}
        self._last_activity_kind = kind
        self.apns.send_activity_start(token, environment, attributes, content_state,
                                      timestamp, alert=start_alert)
        if self._activity_start_first_sent_at is None:
            # The clock the fallback measures against: when this session *first* asked for
            # a card, not when it last retried, so the retries all happen inside the wait
            # rather than pushing it further out each time.
            self._activity_start_first_sent_at = time.time()

    def _check_activity_fallback(self):
        """Once a session has asked for a Live Activity and not got one, say it plainly.

        Runs on the heartbeat rather than off a state change, because the thing it's
        waiting for is the *absence* of one: the app registering a per-activity token is
        what proves the card exists, and that arrives (or doesn't) some seconds after the
        push, with nothing else happening in between.

        Only the phone, and only a non-urgent phase. An urgent one already sent a real
        alert in `_push_apns`, and that alert is itself a way into the app — a second
        notification fifteen seconds later would be the same event twice. The watch has no
        Live Activity to be missing.
        """
        with self._lock:
            if not self.apns:
                return
            session_id = self.session.session_id
            sequence = self.session.sequence
            kind = self.session.kind
            if kind in ("idle", "cancelled") or kind in protocol.URGENT_KINDS:
                return
            if self._activity_start_first_sent_at is None:
                return                                  # never asked for a card
            if self._activity_fallback_sent_for == session_id:
                return                                  # one per queue, not one per tick
            if self.activity_tokens.update_token(session_id):
                return                                  # it arrived after all
            if time.time() - self._activity_start_first_sent_at < self._activity_fallback_delay_seconds:
                return                                  # still might
            phone = self.push_tokens.get("phone")
            if not phone:
                # Nothing to send to. Deliberately *not* latched: the phone may register a
                # moment from now, and this queue should still get its one notification.
                return
            self._activity_fallback_sent_for = session_id
            token, environment = phone

        # Outside the lock on purpose, unlike `_push_apns`. This runs once a second, and
        # `apply()` holds the same lock across every state change — waiting on Apple in
        # here would stall the whole server for as long as APNs felt like taking. The flag
        # above is set inside the lock, so a second tick can't double-send while this one
        # is still in flight.
        title, body = protocol.activity_fallback_copy()
        self.log("No Live Activity took for this queue — telling the phone the plain way")
        self.apns.send_alert("phone", token, environment, title, body, session_id, sequence)

    def _changed(self):
        if self.on_change:
            self.on_change()

    # ------------------------------------------------------------ reading the game

    @property
    def hero_keys(self) -> list:
        """Every hero the catalog knows, unfiltered by role — the roster on screen shows
        all three roles at once, so a scan restricted to the queued role would be looking
        for two-thirds of what's in front of it."""
        return [hero["key"] for hero in self.catalog.heroes if hero.get("key")]

    def scan_hero_select(self):
        """Looks at the hero-select screen. `None` when there's nothing to look at.

        Deliberately outside `self._lock`: this takes a couple of seconds, and every state
        change in the server waits on that same lock. Holding it across a scan would stall
        the heartbeat and every broadcast for the duration. Nothing here touches session
        state — the caller decides what to do with the answer.
        """
        if not self.vision_enabled:
            return None
        if not vision.focus_game_window(log=lambda message: self.log(message)):
            return None

        started = time.time()
        try:
            result = vision.scan(self.templates, self.hero_keys,
                                 my_hero_key=self._my_hero_key(),
                                 log=lambda message: self.log(message))
        except Exception as problem:     # pragma: no cover - depends on a live screen
            # A scan is a convenience over driving by hand, never a reason to take the
            # server down with it.
            self.log("Screen scan failed: %s" % problem)
            return None

        if not result.on_hero_select:
            # Looked, and the roster wasn't there. The caller falls back to building the
            # phase from the catalog rather than reporting a roster that isn't on screen.
            return None

        self._roster = result.roster
        self.log("Scanned in %.1fs — %d heroes on screen, %d slot%s filled"
                 % (time.time() - started, len(result.roster), len(result.picks),
                    "" if len(result.picks) == 1 else "s"))
        for pick in result.picks:
            self.log("  slot %d: %s%s" % (pick.slot, self.catalog.name_for_hero(pick.hero_key),
                                          " (you)" if pick.is_self else ""))
        return result

    def _my_hero_key(self):
        with self._lock:
            return (self.session.phase.get("data") or {}).get("myHeroKey")

    def scan_role_select(self):
        """Looks at the "Select a Role" screen. `None` when there's nothing to look at.

        On demand, for a person's own click on the panel's own button. `queuewatch.
        QueueWatcher._check_role_select` reads the same screen continuously and does not
        call this — same underlying `queueroles.scan`, but on its own idle poll rather
        than a focus grab this method still takes for a manual read's sake.
        """
        if not self.vision_enabled:
            return None
        if not vision.focus_game_window(log=lambda message: self.log(message)):
            return None

        try:
            result = queueroles.scan(self.role_templates,
                                     log=lambda message: self.log(message))
        except Exception as problem:      # pragma: no cover - depends on a live screen
            self.log("Role-select scan failed: %s" % problem)
            return None

        if not result.on_screen:
            return None

        self.log("Scanned — %s checked%s" % (
            ", ".join(sorted(result.roles)) or "nothing",
            " (%s)" % result.mode if result.mode else ""))
        return result

    def scan_map_vote(self):
        """Looks at the "Vote for a Map" screen and reads which real maps its cards
        name. `None` when there's nothing to look at, or when fewer than two cards read
        as a real map — see `queuemapvote.scan` for why that's treated the same as the
        screen not being up.

        No focus grab first, unlike hero select's own scan: reading a screenshot never
        needed the window foregrounded to begin with, the same reason `scan_role_select`
        and the queue banner's own continuous poll don't take one either, and nothing
        this reads back needs a real click to land anywhere.
        """
        if not self.vision_enabled:
            return None
        try:
            result = queuemapvote.scan(self.catalog, log=lambda message: self.log(message))
        except Exception as problem:      # pragma: no cover - depends on a live screen
            self.log("Map vote scan failed: %s" % problem)
            return None
        if not result.on_screen:
            return None
        self.log("Scanned the vote screen — %s" % ", ".join(
            self.catalog.name_for_map(key) for key in result.map_keys))
        return result

    def _drive_hero_pick(self, hero_key: str):
        """Puts the game on `hero_key`, then re-reads the slots to see if it took.

        Runs on its own thread — see `_select_hero`. The re-scan is the honest part: the
        click is a guess that the icon is where we last saw it, and looking again
        is what turns that into something worth telling the phone.
        """
        if not vision.focus_game_window(log=lambda message: self.log(message)):
            return
        try:
            clicked = vision.select_hero(self.templates, self.hero_keys, hero_key,
                                         roster=self._roster,
                                         log=lambda message: self.log(message))
            if not clicked:
                self.log("Pick %s wasn't made on screen — do it on the PC"
                         % self.catalog.name_for_hero(hero_key))
                return
            still_open = vision.looks_at_hero_select(self.templates, self.hero_keys)
            picks = []
            if still_open:
                _, gray = vision.capture()
                picks = vision.resolve_self_slot(
                    vision.scan_player_slots(gray, self.templates, self.hero_keys), hero_key)
        except Exception as problem:     # pragma: no cover - depends on a live screen
            self.log("Couldn't pick %s on screen: %s" % (hero_key, problem))
            return

        if not still_open:
            # Confirming a hero closes the menu, so the roster being gone is what success
            # looks like — not a failure to verify. Nothing to re-read, and nothing to
            # say about the slots: reporting an empty row here would tell the phone
            # everyone had un-picked.
            self.log("Picked %s" % self.catalog.name_for_hero(hero_key))
            return

        # The menu is still up, so the slot row is readable and worth checking against
        # what was asked for. The click is only ever a guess that an icon was where the
        # matcher said it was; this is the game's own answer.
        landed = next((pick for pick in picks if pick.is_self), None)
        if landed is not None and landed.hero_key != hero_key:
            self.log("Asked for %s but the game shows %s — the click went wide"
                     % (self.catalog.name_for_hero(hero_key),
                        self.catalog.name_for_hero(landed.hero_key)))

        with self._lock:
            if self.session.kind != "heroSelect":
                return                   # the phase moved on while we were clicking
            self.session.patch(teamPicks=[pick.as_wire() for pick in picks])
            self._broadcast_snapshot()
        self._changed()

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
            self._register_push_token(client, data.get("token"), data.get("environment"),
                                      data.get("kind"))
        elif kind == "registerActivityPushToken":
            self._register_activity_push_token(
                data.get("sessionID"), data.get("token"), data.get("environment"))
        elif kind == "registerActivityStartToken":
            self._register_activity_start_token(data.get("token"), data.get("environment"))
        elif kind == "ping":
            # Answered inline and immediately: anything this end waits on is time the
            # client can only read as distance to the server.
            client_time = data.get("clientTime")
            if isinstance(client_time, (int, float)):
                client.send(protocol.pong(float(client_time)))
        elif kind == "diagnostic":
            self._log_diagnostic(client, data.get("message"))
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

        # The phone hears back the moment the state changed, above; the mouse takes a
        # second or two longer. Doing that here would hold up the socket thread this
        # arrived on — and every other message on it — for the length of a click and a
        # re-scan, so it goes to a thread of its own.
        if self.vision_enabled:
            threading.Thread(target=self._drive_hero_pick, args=(hero_key,),
                             daemon=True, name="hero-pick").start()

    def _register_push_token(self, client, token, environment, kind=None):
        # The device says which it is, and that is believed over the identity of the
        # socket it arrived on. A watch with no connection of its own registers through
        # the paired iPhone, so the socket here is the phone's — reading the kind from it
        # would file the watch's token under `phone`, on top of the phone's own, and leave
        # both devices unreachable. `kind` is absent from older clients, which only ever
        # registered over their own socket, so falling back to the identity is right.
        if kind not in ("phone", "watch"):
            kind = (client.identity or {}).get("kind")
        if kind not in ("phone", "watch") or not token or environment not in ("sandbox", "production"):
            return
        self.push_tokens.register(kind, token, environment)
        self.log("%s registered %s for push notifications" % (client.name, kind))

    def _register_activity_push_token(self, session_id, token, environment):
        if not session_id or not token or environment not in ("sandbox", "production"):
            return
        self.activity_tokens.register_update(session_id, token, environment)
        # Proof a card exists and the app is attached to it — whether it was pushed into
        # being or started locally. Either way there's nothing left to fall back from.
        with self._lock:
            self._activity_start_first_sent_at = None
        self.log("Live Activity registered for push updates")

    def _register_activity_start_token(self, token, environment):
        if not token or environment not in ("sandbox", "production"):
            return
        self.activity_tokens.register_start(token, environment)
        self.log("Registered for Live Activity push-to-start")

    # A device is the only thing that can see whether its Live Activity actually appeared,
    # and until this existed the only way to find out was to pick the phone up and ask.
    # Truncated because it lands in a one-line log view, and marked so nobody mistakes a
    # line the phone wrote for something the server observed.
    _DIAGNOSTIC_LIMIT = 300

    def _log_diagnostic(self, client, message):
        if not isinstance(message, str) or not message.strip():
            return
        text = message.strip()[:self._DIAGNOSTIC_LIMIT].replace("\n", " ")
        self.log("%s says: %s" % (client.name, text))

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
            self._check_activity_fallback()
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
