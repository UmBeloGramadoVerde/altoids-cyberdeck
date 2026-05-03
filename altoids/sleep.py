from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(slots=True)
class SleepManager:
    idle_seconds: float
    sleeping: bool = False
    _last_activity: float = 0.0

    def __post_init__(self) -> None:
        self.bump()

    def bump(self) -> None:
        self._last_activity = time.monotonic()
        self.sleeping = False

    def update(self) -> bool:
        if self.sleeping:
            return False
        if time.monotonic() - self._last_activity >= self.idle_seconds:
            self.sleeping = True
            return True
        return False
