"""The wire protocol from `docs/PROTOCOL.md`, spoken from the PC side.

Phases are plain dicts in exactly the shape that goes over the socket — there is no
second model to drift out of step with the JSON. The builders below exist so the GUI
can't misspell a key, and `QueueSession` enforces the same transition table the app
enforces on the way in, so this server can't push the phone into a state it will refuse.
"""
from __future__ import annotations

import datetime
import json
import uuid

VERSION = 1

MODES = ["quickPlay", "competitive", "arcade", "stadium", "mysteryHeroes", "custom"]
ROLES = ["tank", "damage", "support", "flex", "open"]
CANCEL_REASONS = ["userLeft", "matchCancelled", "timedOut", "serverError"]

# Modes where you pick a role before queueing; the others queue as `open`.
ROLE_QUEUE_MODES = {"quickPlay", "competitive", "stadium"}
# Mystery Heroes assigns your hero, so it goes straight from matchFound to inGame.
NO_HERO_SELECT_MODES = {"mysteryHeroes"}

# Mirrors `QueuePhase.isUrgent` — the phases that get a real alert (sound, haptic, a brief
# peek) on their Live Activity push, the way a delivery app announces a status change
# without a separate notification alongside it. Routine changes stay silent.
URGENT_KINDS = {"matchFound", "mapVote", "heroSelect"}

_NOTIFICATION_TITLES = {
    "matchFound": "Match Found",
    "mapVote": "Map Vote",
    "heroSelect": "Hero Select",
}
_NOTIFICATION_BODIES = {
    "matchFound": "You're being pulled into the game — get back to your PC.",
    "mapVote": "Pick where you want to play.",
    "heroSelect": "Choose your hero.",
}


def notification_copy(kind: str):
    """`(title, body)` for an urgent phase's Live Activity alert. Duplicated against the
    Swift side on purpose, the same way the phase builders below mirror `QueuePhase` —
    kept in step by eye rather than by a shared source, since the two live in different
    languages."""
    return _NOTIFICATION_TITLES.get(kind, "Overwatch Queue"), \
        _NOTIFICATION_BODIES.get(kind, "Status changed.")


def activity_fallback_copy():
    """`(title, body)` for the notification sent when a Live Activity never appeared.

    Not `notification_copy`: that copy announces an urgent phase, and this is only ever
    sent for a routine one (an urgent phase already got a real alert of its own). It has to
    earn the interruption on its own terms, so it says what happened and what tapping does.

    Unlike the copy above there's no Swift counterpart to keep in step — nothing on the
    device composes this text, it only ever arrives already written.
    """
    return "Queue started", "Tap to put it on your Lock Screen."


# ---------------------------------------------------------------- Live Activity pushes
#
# A Live Activity push's `content-state` is decoded on-device with a plain `JSONDecoder`
# — not this protocol's `.iso8601` one — so every `Date` inside it has to match Swift's
# *default* `Codable` encoding for `Date`, which is a bare number of seconds since
# 2001-01-01T00:00:00Z (`timeIntervalSinceReferenceDate`), not 1970 and not a string.
# Get this wrong and the Live Activity silently fails to update; nothing else on this
# wire uses this epoch, which is exactly why it's worth a comment.

REFERENCE_DATE_EPOCH_OFFSET = 978307200.0      # 2001-01-01T00:00:00Z, in Unix seconds

_ACTIVITY_DATE_KEYS = ("startedAt", "deadline", "lockInAt")


def reference_date_seconds(when: datetime.datetime) -> float:
    return when.timestamp() - REFERENCE_DATE_EPOCH_OFFSET


def _iso_to_reference_date_seconds(iso_string: str) -> float:
    parsed = datetime.datetime.strptime(iso_string, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=datetime.timezone.utc)
    return reference_date_seconds(parsed)


def content_state(phase: dict, sequence: int) -> dict:
    """The `QueueActivityAttributes.ContentState` shape for a Live Activity push —
    identical to the wire phase shape except every date is a reference-date float. Never
    mutates `phase`; the caller's own copy (typically `self.session.phase`) has to keep
    its ISO-8601 strings for the WebSocket and every other client."""
    data = phase.get("data")
    if data:
        data = dict(data)
        for key in _ACTIVITY_DATE_KEYS:
            if key in data:
                data[key] = _iso_to_reference_date_seconds(data[key])
        phase = {**phase, "data": data}
    return {"phase": phase, "sequence": sequence}


MODE_NAMES = {
    "quickPlay": "Quick Play",
    "competitive": "Competitive",
    "arcade": "Arcade",
    "stadium": "Stadium",
    "mysteryHeroes": "Mystery Heroes",
    "custom": "Custom Game",
}
ROLE_NAMES = {"tank": "Tank", "damage": "Damage", "support": "Support",
              "flex": "Flex", "open": "Open Queue"}


# ---------------------------------------------------------------- time

def now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def iso(when: datetime.datetime) -> str:
    """ISO-8601 with a Z suffix and whole seconds — what the app's decoder accepts."""
    return when.astimezone(datetime.timezone.utc).replace(microsecond=0) \
               .isoformat().replace("+00:00", "Z")


def in_seconds(seconds: float) -> str:
    return iso(now() + datetime.timedelta(seconds=seconds))


# ---------------------------------------------------------------- phases

def idle() -> dict:
    return {"type": "idle"}


def searching(mode: str, role: str, started_at: datetime.datetime,
              estimated_wait=None, group_size: int = 1) -> dict:
    data = {"mode": mode, "role": role, "startedAt": iso(started_at), "groupSize": group_size}
    if estimated_wait is not None:
        data["estimatedWait"] = round(float(estimated_wait), 3)
    return {"type": "searching", "data": data}


def match_found(mode: str, role: str, waited: float, lock_in_seconds: float = 15) -> dict:
    return {"type": "matchFound",
            "data": {"mode": mode, "role": role, "waited": round(float(waited), 3),
                     "lockInAt": in_seconds(lock_in_seconds)}}


def map_vote(map_keys, deadline_seconds: float = 25, votes=None, my_vote=None) -> dict:
    votes = votes or {}
    data = {"deadline": in_seconds(deadline_seconds),
            "options": [{"mapKey": key, "votes": int(votes.get(key, 0))} for key in map_keys]}
    if my_vote:
        data["myVote"] = my_vote
    return {"type": "mapVote", "data": data}


def hero_select(mode: str, role: str, map_key=None, deadline_seconds: float = 40,
                taken=None, my_hero=None) -> dict:
    data = {"mode": mode, "role": role, "deadline": in_seconds(deadline_seconds),
            "takenHeroKeys": list(taken or [])}
    if map_key:
        data["mapKey"] = map_key
    if my_hero:
        data["myHeroKey"] = my_hero
    return {"type": "heroSelect", "data": data}


def in_game(mode: str, map_key=None, hero_key=None, started_at=None) -> dict:
    data = {"mode": mode, "startedAt": iso(started_at or now())}
    if map_key:
        data["mapKey"] = map_key
    if hero_key:
        data["heroKey"] = hero_key
    return {"type": "inGame", "data": data}


def cancelled(reason: str = "matchCancelled", message=None) -> dict:
    data = {"reason": reason}
    if message:
        data["message"] = message
    return {"type": "cancelled", "data": data}


# ---------------------------------------------------------------- messages

def envelope(body: dict) -> str:
    return json.dumps({"v": VERSION, "body": body}, separators=(",", ":"))


def heartbeat() -> str:
    return envelope({"type": "heartbeat", "data": {"serverTime": iso(now())}})


def error(code: str, message: str) -> str:
    return envelope({"type": "error", "data": {"code": code, "message": message}})


# ---------------------------------------------------------------- the session

# Mirrors `QueuePhase.canTransition(to:)`. The app refuses anything outside this, so
# refusing it here too means the GUI can grey out a step instead of silently no-opping.
_TRANSITIONS = {
    "idle": {"searching"},
    "searching": {"matchFound"},
    "matchFound": {"mapVote", "heroSelect", "inGame", "searching"},
    "mapVote": {"heroSelect", "inGame"},
    "heroSelect": {"inGame"},
    "inGame": set(),
    "cancelled": {"searching"},
}


def can_transition(current: str, target: str) -> bool:
    if target in ("idle", "cancelled") or target == current:
        return True
    return target in _TRANSITIONS.get(current, set())


class QueueSession:
    """One continuous queue: a session id, a sequence counter, and the current phase."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.session_id = str(uuid.uuid4()).upper()
        self.sequence = 0
        self.phase = idle()

    @property
    def kind(self) -> str:
        return self.phase["type"]

    def can_go_to(self, target_kind: str) -> bool:
        return can_transition(self.kind, target_kind)

    def advance(self, phase: dict) -> bool:
        """Applies `phase` if the transition is legal. Returns whether it was."""
        if not self.can_go_to(phase["type"]):
            return False
        self.phase = phase
        self.sequence += 1
        return True

    def snapshot(self) -> str:
        return envelope({"type": "snapshot", "data": {
            "sessionID": self.session_id,
            "sequence": self.sequence,
            "serverTime": iso(now()),
            "phase": self.phase,
        }})

    # -- edits to the phase already showing, which are always legal ----------

    def patch(self, **fields) -> bool:
        """Updates fields of the current phase's payload and re-sends it."""
        if "data" not in self.phase:
            return False
        self.phase["data"].update(fields)
        self.sequence += 1
        return True

    def shift_start(self, seconds: float) -> bool:
        """Back-dates a search so a long wait can be seen without waiting for one."""
        if self.kind != "searching":
            return False
        started = datetime.datetime.strptime(
            self.phase["data"]["startedAt"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=datetime.timezone.utc)
        return self.patch(startedAt=iso(started - datetime.timedelta(seconds=seconds)))

    def elapsed(self) -> float:
        if self.kind != "searching":
            return 0.0
        started = datetime.datetime.strptime(
            self.phase["data"]["startedAt"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=datetime.timezone.utc)
        return max(0.0, (now() - started).total_seconds())
