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
        mode = self.mode

        if kind == "matchFound":
            waited = self.server.session.elapsed() or self.estimate
            return protocol.match_found(mode, self.effective_role, waited)
        if kind == "mapVote":
            return self.map_vote_phase()
        if kind == "heroSelect":
            return self.hero_select_phase()
        if kind == "inGame":
            return protocol.in_game(mode, self.map_key(), self.hero_key())
        if kind == "cancelled":
            return protocol.cancelled("matchCancelled")
        raise ValueError("no such phase: %s" % kind)

    def map_vote_phase(self) -> dict:
        """Map vote, read off the screen when the game is there to be read.

        `QueueServer.scan_map_vote` looks and returns `None` when it can't — no game, no
        window, fewer than two cards reading as a real map, or a machine without the OCR
        extra installed — in which case this falls back to the same plausible-catalog-
        options behaviour every phase here had before any of them could read a screen.
        """
        scan = self.server.scan_map_vote()
        if scan:
            return protocol.map_vote(scan.map_keys, 25)
        return protocol.map_vote(self.server.catalog.map_vote_options(self.mode), 25)

    def hero_select_phase(self) -> dict:
        """Hero select, read off the screen when the game is there to be read.

        This is the one phase that has a real answer available: the roster is on screen,
        and so is everyone's pick. `QueueServer.scan_hero_select` looks, and returns None
        when it can't — no game, no window, or a machine without the vision extras — in
        which case we fall back to the old behaviour of naming a few plausible heroes so
        the panel still demonstrates the phase.
        """
        mode, role = self.mode, self.effective_role
        scan = self.server.scan_hero_select()
        if scan:
            return protocol.hero_select(
                mode, role, self.map_key(), 40,
                taken=scan.taken_hero_keys(self.hero_key()),
                available=scan.available_hero_keys,
                team_picks=[pick.as_wire() for pick in scan.picks])

        heroes = [hero["key"] for hero in self.server.catalog.heroes_for(role, mode)]
        return protocol.hero_select(mode, role, self.map_key(), 40,
                                    random.sample(heroes, min(3, len(heroes))))

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
