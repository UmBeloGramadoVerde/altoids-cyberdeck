from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .display import Display


@dataclass(slots=True)
class ButtonEvent:
    button: str
    long_press: bool = False


class ButtonInput:
    LONG_PRESS_SECONDS = 0.5

    def __init__(self, callback: Callable[[ButtonEvent], None], display: Display | None = None) -> None:
        self.callback = callback
        self.display = display
        self.available = display is not None
        self._pressed: dict[str, float] = {}

    def poll(self) -> list[ButtonEvent]:
        if self.display is None:
            return []
        states = self.display.read_buttons()
        if not states:
            return []
        events: list[ButtonEvent] = []
        now = time.monotonic()
        for button, pressed in states.items():
            was_pressed = button in self._pressed
            if pressed and not was_pressed:
                self._pressed[button] = now
            elif not pressed and was_pressed:
                press_start = self._pressed.pop(button)
                long = (now - press_start) >= self.LONG_PRESS_SECONDS
                events.append(ButtonEvent(button=button, long_press=long))
        return events
