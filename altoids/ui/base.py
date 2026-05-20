from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw

from ..colors import BG

if TYPE_CHECKING:
    from ..input_keyboard import KeyboardEvent


@dataclass(slots=True)
class ScreenContext:
    app: object


class Screen:
    name = "screen"

    def __init__(self, context: ScreenContext) -> None:
        self.context = context
        self._background_cache: dict[object, Image.Image] = {}

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

    def debug_state(self) -> dict[str, object]:
        return {}

    def invalidate_background(self) -> None:
        self._background_cache.clear()

    def cached_background(
        self,
        signature: object,
        size: tuple[int, int],
        painter,
    ) -> Image.Image:
        cached = self._background_cache.get(signature)
        if cached is not None and cached.size == size:
            return cached
        background = Image.new("RGB", size, BG)
        painter(ImageDraw.Draw(background), background)
        self._background_cache = {signature: background}
        return background
