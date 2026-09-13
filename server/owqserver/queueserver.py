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

from . import bnetpresence, protocol, queuemapvote, queueroles, queuevision, queuewatch, vision
from .activitytokens import ActivityTokens
from . import fcm
from .fcm import FCMClient
from .fcmtokens import FCMTokens
from .heroimages import TemplateStore
from .protocol import QueueSession
from .pushtokens import PushTokens
from .mqttbroker import MQTTBroker, SERVER_USER
from .mqttclient import MQTTClient

# Not a liveness check any more (MQTT's own keepalive/LWT covers that) — just how often
# a connected client gets a fresh clock sample to re-anchor against. See `_tick_loop`.
HEARTBEAT_SECONDS = 10


class QueueServer:
    def __init__(self, pairing, catalog, log=None, push_tokens=None, activity_tokens=None,
                 fcm_tokens=None,
                 vision_enabled=False, queue_vision_enabled=False, presence_enabled=False,
                 presence_source="memory", presence_port=bnetpresence.DEFAULT_CDP_PORT):
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
        # Asking Battle.net is opt-in for the same reason as the two above: it only ever
        # reads (a debug-port websocket, or a plain `ReadProcessMemory` on Battle.net's
        # own process — Overwatch itself is never touched), but it *drives the session on
        # its own*, so a test that built a server with it on would find its phase moving
        # underneath it, and worse, would open a process handle on whatever Battle.net
        # happened to be running on the machine the tests execute on.
        self.presence_enabled = (bool(presence_enabled) and
                                 bnetpresence.PRESENCE_AVAILABLE)
        self.presence_source = presence_source
        self.presence_port = presence_port
        self.queue_watcher = None
        self.presence_watcher = None
        self.templates = TemplateStore(catalog, log=lambda message: self.log(message))
        self.role_templates = queueroles.RoleIconStore(log=lambda message: self.log(message))
        # Where each hero sat the last time we looked, so picking one doesn't have to pay
        # for a fresh scan. Cleared whenever the phase moves, since the roster is only on
        # screen during hero select and stale coordinates would click on nothing.
        self._roster = {}

        # Pushing is Live Activity pushes only, all over Firebase by way of the push relay
        # — see `fcm.py` for what Firebase delivers that a direct APNs push measurably
        # doesn't, and why this server never holds the Firebase credential itself.
        #
        # The phone/watch device tokens are still recorded as they register (the panel
        # uses them to tell whether a device has ever been paired), but nothing sends a
        # plain notification to either any more: the Live Activity's own alert is the
        # only way this server interrupts anyone, and the watch mirrors that card on its
        # own.
        self.push_tokens = push_tokens if push_tokens is not None else PushTokens()
        # A Live Activity's own push tokens — see `_push_activity` for how they're used.
        self.activity_tokens = activity_tokens if activity_tokens is not None else ActivityTokens()
        # How Firebase addresses this phone — see `fcmtokens.py`.
        self.fcm_tokens = fcm_tokens if fcm_tokens is not None else FCMTokens()
        # A background push-to-start is best-effort like any other push — it can be
        # delayed or dropped, and there's no acknowledgement. So this isn't a one-shot: a
        # session with no update token yet keeps retrying `start` (see
        # `_retry_activity_start`), throttled by the cooldown below so a burst of fast
        # changes doesn't hammer it. `startedAt` is fixed the first time a session sends
        # one, not recomputed per retry — same attributes every time is what lets Apple's
        # system recognise a retry as the same activity rather than a new one.
        self._activity_session_id = None
        self._activity_started_at = None
        self._activity_start_last_sent_at = 0.0
        self._activity_start_retry_seconds = 4
        # Set when a session first needs a card and cleared once the app registers a
        # per-activity token for it — while set, `_retry_activity_start` keeps trying.
        self._activity_start_first_sent_at = None
        # The phase an update alert was last sent for, so a patch to the *same* phase's
        # data — the estimate ticking, a mode correction — never earns one of its own.
        # See `_push_activity` for why every real phase change does.
        self._last_activity_kind = None
        self.fcm = self._make_fcm_client()
        self._warned_no_fcm = False

        self.broker = MQTTBroker(port=pairing.port, log=lambda message: self.log(message))
        self.mqtt = MQTTClient(host="127.0.0.1", port=pairing.port,
                               username=SERVER_USER, password=pairing.server_password,
                               on_open=self._client_connected,
                               on_message=self._client_said,
                               on_close=self._client_gone,
                               # Resolved late: the GUI replaces `log` after the
                               # server is built, and broker-level messages should
                               # follow it there rather than into the original.
                               log=lambda message: self.log(message))
        self._activity_timer = None
        self._running = False

    def _make_fcm_client(self):
        """The one push transport — see `fcm.py` for why Live Activity pushes go through
        Firebase rather than straight at Apple. `None` only when pushing has been turned
        off, which is what the test suite does."""
        url = fcm.relay_url()
        if not url:
            return None
        client = FCMClient(url, log=lambda message: self.log(message))
        client.device_token = self.fcm_tokens.get()
        return client

    # ------------------------------------------------------------ lifecycle

    def start(self):
        self.broker.port = self.pairing.port
        self.broker.start(self.pairing.token, self.pairing.server_password)
        self.mqtt.port = self.pairing.port
        self.mqtt.password = self.pairing.server_password
        self.mqtt.start()
        # Seeds the retained topic with the current state immediately, so a phone that
        # connects before anything has actually changed yet — the common case, right
        # after pairing — still gets a snapshot the instant it subscribes, rather than
        # waiting on the first real state change to populate `owq/snapshot` at all.
        self.mqtt.publish_snapshot(self.session.snapshot())
        self._running = True
        self._activity_timer = threading.Thread(target=self._tick_loop,
                                                 daemon=True, name="server-tick")
        self._activity_timer.start()
        self.log("Listening on port %d" % self.pairing.port)

    def restart_broker(self):
        """Rotates auth and bounces the broker — used after the pairing token changes,
        since that's the only way to force every previously-paired device off."""
        self.broker.restart(self.pairing.token, self.pairing.server_password)

    def watch_queue(self, controls):
        """Starts reading the queue — off the screen, off Battle.net's own presence, or
        both — and returns the `QueueWatcher` (which holds the shared tracker either way)
        or `None` if neither is enabled.

        Takes the panel's `Controls` rather than building its own, because the mode and
        role a queue is in are only half readable — see `queuewatch.QueueWatcher`. Called
        from `run.py` and the panel; doing it here keeps the enabled-or-not decision in
        the one place that already makes it for hero select.

        The two channels share one tracker on purpose: a presence-only server (no
        `opencv`/`mss` needed at all) still gets a fully working `QueueWatcher` with its
        screen thread simply never started, and enabling vision later just starts that
        thread against the tracker presence has already been talking to.
        """
        if not self.queue_vision_enabled and not self.presence_enabled:
            return self.queue_watcher
        if self.queue_watcher is None:
            self.queue_watcher = queuewatch.QueueWatcher(
                controls, log=lambda message: self.log(message),
                role_templates=self.role_templates,
                hero_templates=self.templates, hero_keys=self.hero_keys)
        if self.queue_vision_enabled and not self.queue_watcher.running:
            self.queue_watcher.start()
        if self.presence_enabled and self.presence_watcher is None:
            source = bnetpresence.open_source(prefer=self.presence_source,
                                              port=self.presence_port,
                                              log=lambda message: self.log(message))
            self.presence_watcher = queuewatch.PresenceWatcher(
                source, self.queue_watcher.fold_presence,
                fold_lost=self.queue_watcher.fold_presence_lost,
                reopen=self._reopen_presence,
                log=lambda message: self.log(message))
            self.presence_watcher.start()
        return self.queue_watcher

    def _reopen_presence(self):
        """Tried by `PresenceWatcher` on every poll while its source is declared dead.

        `relaunch=True` here — and only here — is what lets Battle.net having closed
        or crashed out from under an already-established connection recover on its
        own: `bnetpresence.launch_battlenet` starts it back up, but only when it isn't
        running at all, never interrupting one that's merely lost its debug port or is
        mid-queue. A fresh `watch_queue()` call never opts into this — see
        `bnetpresence.open_source`'s own docstring.
        """
        source = bnetpresence.open_source(prefer=self.presence_source,
                                          port=self.presence_port,
                                          log=lambda message: self.log(message),
                                          relaunch=True)
        return None if isinstance(source, bnetpresence.NullSource) else source

    def stop_presence(self):
        if self.presence_watcher:
            self.presence_watcher.close()
            self.presence_watcher = None

    def relaunch_battlenet(self) -> bool:
        """A person's own "it's open but not working" button — closes Battle.net and
        starts it again with its debug port open.

        `_reopen_presence`'s automatic path deliberately never does this: a Battle.net
        that's merely lost its debug port, or was simply started by hand without it, is
        left running rather than closed out from under an active game. This is the same
        recovery with that one safety removed, because a person watching the panel
        pressed a button asking for exactly that. Once Battle.net is back up, the
        watcher already retrying `_reopen_presence` on every dead poll is what actually
        reconnects — nothing further to do here."""
        return bnetpresence.force_relaunch_battlenet(
            port=self.presence_port, log=lambda message: self.log(message))

    def stop(self):
        self._running = False
        self.stop_scenario()
        if self.queue_watcher:
            self.queue_watcher.stop()
        self.stop_presence()
        self.mqtt.stop()
        self.broker.stop()

    @property
    def clients(self) -> list:
        return [c for c in self.mqtt.clients if c.authorized]

    @property
    def presence_degraded(self) -> bool:
        """Whether Battle.net's presence link is down — pushed to the phone/watch on
        every heartbeat (see `protocol.heartbeat`) so a disconnect is visible rather
        than a silent stall while the queue waits for it. `False` whenever presence was
        never enabled in the first place, same as it always was."""
        return bool(self.presence_watcher and self.presence_watcher.dead)

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
        # Retained + QoS 1: the broker itself now guarantees delivery to every connected
        # subscriber and hands the latest snapshot straight to anyone who (re)subscribes,
        # including a phone that just reconnected after a dead Wi-Fi — no more silent
        # drops the way the old socket `broadcast()` had on a write failure.
        self.mqtt.publish_snapshot(self.session.snapshot())
        # Off-thread: `_push_live_activity` makes blocking HTTP calls (10s timeout apiece),
        # and this runs from inside the same lock that every vote, pick and phase change
        # goes through. Left inline, a slow push doesn't just delay itself — it stalls the
        # scan loop's next frame *and* the next client message waiting on this lock. The
        # push itself already tolerates running late or twice (a client discards anything
        # not newer than its current sequence), so nothing here needs the lock still held.
        threading.Thread(target=self._push_live_activity, daemon=True,
                         name="live-activity-push").start()

    def _push_live_activity(self):
        """Keeps the Live Activity current over Firebase, whether or not the phone's
        process is even running. This is the only push this server sends: no plain
        notification goes to the phone or the watch — the card's own alert is the one
        interruption, and the watch mirrors the card by itself.

        Skipped while a phone is live over MQTT: that phone's own `LiveActivityController`
        reacts to the very snapshot this broadcast just published, driving the same
        start/update/end — pushing here too would fire a second alert for the one event.
        See `_phone_is_live_connected`.

        An idle/cancelled push goes out regardless. It's the terminal event for this
        queue, and nothing else will ever trigger another push for it: if the phone's own
        `end()` doesn't land (the socket looking live a moment longer than the phone
        actually is, or the process suspended mid-`await` as the screen locks) the card
        would be stuck showing its last phase forever. Ending an already-ended activity is
        a no-op, so sending it twice costs nothing."""
        if not self.fcm:
            if not self._warned_no_fcm:
                self._warned_no_fcm = True
                self.log("Pushing is turned off — the Live Activity won't be pushed")
            return
        session_id, sequence, kind = self.session.session_id, self.session.sequence, self.session.kind
        if not self._phone_is_live_connected() or kind in ("idle", "cancelled"):
            self._push_activity(session_id, sequence, kind)

    def _phone_is_live_connected(self) -> bool:
        """Whether a phone is currently subscribed over MQTT — the one signal both sides
        can check without coordinating a flag over the network, and the reason a phone
        that's actually looking at the app doesn't also need a pushed alert for the same
        phase change its own live connection just delivered."""
        return any((client.identity or {}).get("kind") == "phone" for client in self.clients)

    def _push_activity(self, session_id: str, sequence: int, kind: str):
        """Keeps the Live Activity itself current, independent of whether the phone's
        process is even running:

        - A token left over from an *earlier* session → that card is ended first. A
          requeue straight after a match mints a new session; without this the old card
          would sit beside the new one, still saying "In game".
        - Already has a per-activity token for *this* session → push an update (or, on
          idle/cancelled, an end — the activity's own equivalent of `LiveActivityController
          .end()`, reachable even when nothing local is around to call that).
        - No token yet, but a phase has actually started → push-to-start creates the
          activity from nothing, retried (throttled — see `_activity_start_retry_seconds`)
          until an update token registers.

        Every real phase change carries a real alert — sound, haptic, a brief peek. A
        *patch* to the phase already showing — the estimate ticking, a mode correction —
        stays silent, which is what `_last_activity_kind` is for telling apart. A *start*
        always carries one: a wholly silent push-to-start was never once seen to arrive,
        which makes some sense, since there's nothing on screen yet for a silent content
        refresh to land on.
        """
        content_state = protocol.content_state(self.session.phase, sequence)
        timestamp = int(protocol.now().timestamp())
        title, body = protocol.notification_copy(kind)
        start_alert = {"title": title, "body": body}

        stale = self.activity_tokens.stale_update_token(session_id)
        if stale:
            stale_token, _environment = stale
            self.fcm.send_activity_end(stale_token, content_state, timestamp)
            self.activity_tokens.forget_update()
            self._last_activity_kind = None
            self.log("Ended the previous queue's Live Activity")

        changed_kind = kind != self._last_activity_kind
        update_alert = start_alert if changed_kind else None

        activity = self.activity_tokens.update_token(session_id)
        if activity:
            self._activity_start_first_sent_at = None
            token, _environment = activity
            if kind in ("idle", "cancelled"):
                end_alert = start_alert if kind == "cancelled" and changed_kind else None
                self.fcm.send_activity_end(token, content_state, timestamp, alert=end_alert)
                self.activity_tokens.forget_update()
                self._activity_session_id = None
                self._last_activity_kind = None
            else:
                self._last_activity_kind = kind
                response = self.fcm.send_activity_update(token, content_state, timestamp,
                                                         alert=update_alert)
                if self.fcm.token_is_invalid(response):
                    # Firebase will never accept this token again (reinstall, or the token
                    # was rotated/revoked mid-session) — forgetting it lets push-to-start
                    # have another go at putting a card up.
                    self.activity_tokens.forget_update()
            return

        if kind in ("idle", "cancelled"):
            self._activity_session_id = None
            self._activity_start_first_sent_at = None
            return

        if self._activity_session_id != session_id:
            self._activity_session_id = session_id
            self._activity_started_at = protocol.reference_date_seconds(protocol.now())
            self._activity_start_last_sent_at = 0.0            # a new session always retries immediately
            # Arms the retry the moment the queue needs a card, even with no start token
            # on file yet — one may register a moment from now.
            self._activity_start_first_sent_at = time.time()

        start = self.activity_tokens.start_token()
        if not start:
            return

        now = time.time()
        if now - self._activity_start_last_sent_at < self._activity_start_retry_seconds:
            return
        self._activity_start_last_sent_at = now

        token, _environment = start
        attributes = {"sessionID": session_id, "startedAt": self._activity_started_at}
        self._last_activity_kind = kind
        response = self.fcm.send_activity_start(token, attributes, content_state,
                                                timestamp, alert=start_alert)
        if self.fcm.token_is_invalid(response):
            # A dead push-to-start token would otherwise keep "succeeding" into the void
            # every retry, forever.
            self.activity_tokens.forget_start()

    def _retry_activity_start(self):
        """Gives push-to-start a real second (and third...) try instead of just the one.

        `_push_activity` otherwise only runs off an actual phase change, and a queue that
        sits in `searching` for a while — the common case, and the one push-to-start most
        needs retried for — has no such change to ride. Running the same retry off the
        once-a-second tick gives it every chance the throttle allows.

        `_push_activity` already no-ops instantly once a per-activity token has
        registered; checked here too, first, so a session with nothing left to retry
        doesn't pay for the lock and push machinery on every tick."""
        if not self.fcm:
            return
        with self._lock:
            session_id, sequence, kind = self.session.session_id, self.session.sequence, self.session.kind
            if kind in ("idle", "cancelled"):
                return
            if self._activity_start_first_sent_at is None:
                return                                  # never asked for a card
            if self.activity_tokens.update_token(session_id):
                return                                  # already has one
        self._push_activity(session_id, sequence, kind)

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
            self.mqtt.reply(client.client_id, protocol.error(
                "version", "This server speaks protocol v%d." % protocol.VERSION))
            return

        if kind == "hello":
            self._handle_hello(client, body.get("data") or {})
            return

        # No token check here any more: the broker already refused the connection at
        # CONNECT time if `owq/command/<clientID>`'s credentials didn't match the current
        # pairing token (see `mqttbroker.py`'s ACL). Anything reaching this point already
        # passed that gate.
        data = body.get("data") or {}
        if kind == "requestSnapshot":
            self.mqtt.reply(client.client_id, self.session.snapshot())
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
        elif kind == "registerFCMToken":
            self._register_fcm_token(data.get("token"))
        elif kind == "ping":
            # Answered inline and immediately: anything this end waits on is time the
            # client can only read as distance to the server.
            client_time = data.get("clientTime")
            if isinstance(client_time, (int, float)):
                self.mqtt.reply(client.client_id, protocol.pong(float(client_time)))
        elif kind == "diagnostic":
            self._log_diagnostic(client, data.get("message"))
        else:
            self.log("%s sent an unknown command: %s" % (client.name, kind))

    def _handle_hello(self, client, data: dict):
        # An identity announcement now, not a gate — the broker already required the
        # right pairing token as this client's MQTT password before it could publish
        # here at all. `client.authorized` just means "has said hello", for the GUI's
        # device list.
        client.identity = data.get("client") or {}
        client.authorized = True
        self.log("%s paired" % client.name)
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

    def _register_fcm_token(self, token):
        if not token:
            return
        self.fcm_tokens.register(token)
        # The client is built once at startup, before any device has said hello, so it
        # starts out addressing nobody. This is where it learns who to talk to.
        if self.fcm:
            self.fcm.device_token = token
        self.log("Registered for Firebase push")

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
            self.mqtt.reply(client.client_id, protocol.error("no_cancel", "Too late to leave this one."))
            self.mqtt.reply(client.client_id, self.session.snapshot())
            return
        self.log("%s cancelled the queue" % client.name)
        self.stop_scenario()
        self.apply(protocol.cancelled("userLeft"))

    # ------------------------------------------------------------ once-a-second work

    def _tick_loop(self):
        # This used to be a "heartbeat loop" that also kept sockets alive and dropped
        # clients that never said hello — both jobs MQTT itself absorbs now (broker
        # keepalive/LWT for liveness, the ACL for auth). What's left: retrying a
        # Live Activity push-to-start, and a lightweight heartbeat publish — not for liveness,
        # just so a connected client's clock stays freshly re-anchored (see
        # `protocol.heartbeat`).
        last_heartbeat = 0.0
        while self._running:
            time.sleep(1)
            self._retry_activity_start()
            now = time.time()
            if now - last_heartbeat >= HEARTBEAT_SECONDS:
                last_heartbeat = now
                self.mqtt.publish_heartbeat(
                    protocol.heartbeat(presence_degraded=self.presence_degraded))

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
                                   {key: random.randint(0, 3) for key in maps},
                                   mode="quickPlay", roles=["damage"]), 20),
        (lambda: protocol.hero_select("quickPlay", "damage", maps[0], 30,
                                      random.sample(heroes, min(2, len(heroes)))), 30),
        (lambda: protocol.in_game("quickPlay", maps[0], random.choice(heroes),
                                  roles=["damage"]), 60),
    ]


def _long_comp_tank(catalog):
    maps = catalog.map_vote_options("competitive")
    return [
        (lambda: protocol.searching("competitive", "flex", protocol.now() - _minutes(6), 240,
                                    roles=["tank", "support"]), 25),
        (lambda: protocol.match_found("competitive", "flex", 385, 10,
                                      roles=["tank", "support"]), 5),
        (lambda: protocol.hero_select("competitive", "flex", maps[0], 40,
                                      roles=["tank", "support"]), 40),
        (lambda: protocol.in_game("competitive", maps[0], roles=["tank", "support"]), 60),
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
