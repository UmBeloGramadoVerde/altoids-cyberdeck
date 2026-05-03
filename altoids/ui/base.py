from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image, ImageDraw
    from ..input_keyboard import KeyboardEvent


@dataclass(slots=True)
class ScreenContext:
    app: object


class Screen:
    name = "screen"

    def __init__(self, context: ScreenContext) -> None:
        self.context = context

    def update(self, dt: float) -> bool:
        return False

    def render(self, draw: "ImageDraw.ImageDraw", buffer: "Image.Image") -> None:
        raise NotImplementedError

    def on_button(self, button: str, long_press: bool) -> bool:
        return False

    def on_keyboard_event(self, event: "KeyboardEvent") -> bool:
        return False

    def on_wake(self) -> None:
        return

    def get_button_hints(self) -> list[str]:
        return ["-", "-", "-", "-"]
