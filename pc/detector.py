"""The queue state machine: Battle.net presence (plus role-select vision) in, idle/queueing/found out."""
from __future__ import annotations

import presence as p

IDLE = "idle"
QUEUEING = "queueing"
FOUND = "found"

PRESENCE_LOST_SECONDS = 60.0
ROLE_SELECT_CLEAR_CHECKS = 2
ROLE_QUEUE_MODES = (None, "quickPlay", "competitive")


class Detector:
    def __init__(self, now):
        self.state = None
        self.mode = None
        self.started_at = None
        self.found_at = None
        self.holding_for_role_select = False
        self._born = now
        self._last_known = None
        self._clear_checks = 0
        self._cleared_at = None

    def wants_role_check(self, reading, mode):
        """Vision is only needed while Battle.net says "In Queue" but the queue isn't confirmed yet."""
        return reading == p.QUEUEING and self.state != QUEUEING and mode in ROLE_QUEUE_MODES

    def elapsed(self, now):
        if self.started_at is None:
            return 0.0
        end = self.found_at if self.state == FOUND and self.found_at is not None else now
        return max(0.0, end - self.started_at)

    def step(self, now, reading, mode, role_select=None):
        """Feeds one presence reading. `role_select` is the vision result if it was checked:
        True (role screen showing), False (not showing) or None (not checked / couldn't look)."""
        if reading == p.UNKNOWN:
            last = self._last_known if self._last_known is not None else self._born
            if now - last > PRESENCE_LOST_SECONDS and self.state != IDLE:
                self._go_idle()
            return
        self._last_known = now

        if reading == p.QUEUEING:
            if self.state == QUEUEING:
                self.mode = mode or self.mode
            elif role_select is True:
                self.holding_for_role_select = True
                self._clear_checks = 0
                self._cleared_at = None
                self.mode = mode or self.mode
                self.state = self.state or IDLE
            elif self.holding_for_role_select and role_select is None:
                pass
            elif self.holding_for_role_select and self._clear_checks + 1 < ROLE_SELECT_CLEAR_CHECKS:
                self._clear_checks += 1
                self._cleared_at = self._cleared_at or now
            else:
                self._enter_queue(self._cleared_at or now, mode)
        elif reading == p.IN_GAME:
            if self.state == QUEUEING and mode != "custom":
                self.state = FOUND
                self.found_at = now
                self.mode = mode or self.mode
            elif self.state is None:
                self.state = IDLE
        elif reading == p.GAME_ENDING:
            if self.state != QUEUEING:
                self._go_idle()
        elif reading in (p.MENUS, p.ELSEWHERE):
            self._go_idle()
        elif self.state is None:
            self.state = IDLE

    def _enter_queue(self, started_at, mode):
        self.state = QUEUEING
        self.mode = mode or self.mode
        self.started_at = started_at
        self.found_at = None
        self.holding_for_role_select = False
        self._clear_checks = 0
        self._cleared_at = None

    def _go_idle(self):
        self.state = IDLE
        self.mode = None
        self.started_at = None
        self.found_at = None
        self.holding_for_role_select = False
        self._clear_checks = 0
        self._cleared_at = None
