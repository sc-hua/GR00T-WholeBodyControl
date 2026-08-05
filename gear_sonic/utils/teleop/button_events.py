"""Reusable edge and hold gesture helpers for teleoperation controls."""

import time


class LongPressTrigger:
    """Emit one event after a button remains pressed for a minimum duration."""

    def __init__(self, hold_seconds: float):
        if hold_seconds <= 0:
            raise ValueError("hold_seconds must be positive")
        self.hold_seconds = hold_seconds
        self._pressed_since = None
        self._triggered = False

    def update(self, pressed: bool, now: float | None = None) -> bool:
        """Return True once per press-and-hold gesture."""
        now = time.monotonic() if now is None else now
        if not pressed:
            self._pressed_since = None
            self._triggered = False
            return False

        if self._pressed_since is None:
            self._pressed_since = now
            return False

        if not self._triggered and now - self._pressed_since >= self.hold_seconds:
            self._triggered = True
            return True
        return False
