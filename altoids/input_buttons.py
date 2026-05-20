from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(slots=True)
class ButtonEvent:
    button: str
    long_press: bool = False


class ButtonInput:
    def __init__(self, callback: Callable[[ButtonEvent], None]) -> None:
        self.callback = callback
        self.available = False
        try:
            import RPi.GPIO as GPIO  # noqa: F401
        except ModuleNotFoundError:
            return
        self.available = True
        # GPIO wiring is device-specific; keep a noop implementation until deployed.

    def poll(self) -> list[ButtonEvent]:
        return []
