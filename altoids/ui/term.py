from __future__ import annotations

from PIL import ImageDraw

from ..renderer import render_terminal
from .base import Screen, ScreenContext


class TerminalScreen(Screen):
    name = "term"

    def __init__(self, context: ScreenContext) -> None:
        super().__init__(context)
        self.scroll_offset = 0
        self._refresh_elapsed = 0.0

    def update(self, dt: float) -> bool:
        self._refresh_elapsed += dt
        if self._refresh_elapsed >= 0.25:
            self._refresh_elapsed = 0.0
            return True
        return False

    def render(self, draw: ImageDraw.ImageDraw, buffer) -> None:
        snapshot = self.context.app.tmux.capture(self.scroll_offset)
        render_terminal(draw, snapshot.lines, self.context.app.font, (8, 8))

    def on_button(self, button: str, long_press: bool) -> bool:
        terminal_cfg = self.context.app.config.terminal
        if button == "A":
            self.scroll_offset += terminal_cfg.scroll_step * (10 if long_press else 1)
            return True
        if button == "B":
            self.scroll_offset = max(0, self.scroll_offset - terminal_cfg.scroll_step * (10 if long_press else 1))
            return True
        if button == "X":
            if long_press:
                self.context.app.tmux.select_previous_window()
            else:
                self.context.app.tmux.select_next_window()
            self.scroll_offset = 0
            return True
        if button == "Y":
            if long_press:
                self.context.app.set_screen("home")
            else:
                self.context.app.tmux.send_enter()
            return True
        return False

    def get_button_hints(self) -> list[str]:
        return ["A scrl", "B scrl", "X win", "Y enter"]
