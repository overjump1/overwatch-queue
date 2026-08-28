"""What the panel's controls mean, with no Tk anywhere near it.

The window in `gui.py` is a thin layer over this: it mirrors its widgets into `mode`,
`role` and `estimate`, and calls the methods below. Keeping the two apart means the part
worth testing — which phase a button produces, which role a mode queues as, what a jump
does to a running scenario — can be tested without opening a window.
"""
from __future__ import annotations

import random

from . import protocol


class Controls:
    def __init__(self, server):
        self.server = server
        self.mode = "competitive"
        self.role = "tank"
        self.estimate = 240

    # ------------------------------------------------------------ selections

    @property
    def queues_by_role(self) -> bool:
        return self.mode in protocol.ROLE_QUEUE_MODES

    @property
    def effective_role(self) -> str:
        """Arcade, Mystery Heroes and custom games have no role queue."""
        return self.role if self.queues_by_role else "open"

    def set_estimate(self, seconds: int):
        self.estimate = max(0, int(seconds))
        # A live queue gets the new estimate straight away, the way a real server would
        # revise one it had got wrong.
        if self.server.session.kind == "searching":
            self.server.patch(estimatedWait=self.estimate)

    # ------------------------------------------------------------ actions

    def start_queue(self) -> bool:
        self.server.stop_scenario()
        self.server.reset()
        return self.server.apply(protocol.searching(
            self.mode, self.effective_role, protocol.now(), self.estimate))

    def jump(self, kind: str) -> bool:
        self.server.stop_scenario()
        return self.server.apply(self.phase_for(kind))

    def reset(self):
        self.server.stop_scenario()
        self.server.reset()

    def phase_for(self, kind: str) -> dict:
        """The phase a jump button sends. Payloads are filled from the real catalog, so
        the phone gets keys it has art for."""
        mode, role = self.mode, self.effective_role
        catalog = self.server.catalog

        if kind == "matchFound":
            waited = self.server.session.elapsed() or self.estimate
            return protocol.match_found(mode, role, waited)
        if kind == "mapVote":
            return protocol.map_vote(catalog.map_vote_options(mode), 25)
        if kind == "heroSelect":
            heroes = [hero["key"] for hero in catalog.heroes_for(role, mode)]
            taken = random.sample(heroes, min(3, len(heroes)))
            return protocol.hero_select(mode, role, self.map_key(), 40, taken)
        if kind == "inGame":
            return protocol.in_game(mode, self.map_key(), self.hero_key())
        if kind == "cancelled":
            return protocol.cancelled("matchCancelled")
        raise ValueError("no such phase: %s" % kind)

    # ------------------------------------------------------------ carry-over

    def map_key(self):
        """Whatever map the flow has already settled on, or a fresh one.

        After a vote, that's the option with the most votes — the same map the game
        would send you to, so hero select and the match agree with what the phone just
        showed rather than naming somewhere nobody voted for.
        """
        data = self.server.session.phase.get("data") or {}
        if data.get("mapKey"):
            return data["mapKey"]
        if data.get("options"):
            return max(data["options"], key=lambda option: option["votes"])["mapKey"]
        return self.server.catalog.map_vote_options(self.mode, 1)[0]

    def hero_key(self):
        return (self.server.session.phase.get("data") or {}).get("myHeroKey")
