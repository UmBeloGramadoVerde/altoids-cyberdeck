from __future__ import annotations

import time

from PIL import ImageDraw

from ..colors import ACCENT, FG
from ..messages import MESSAGES
from ..sprites import SpriteAnimator, load_sprite_sheet
from .base import Screen, ScreenContext
from .widgets import draw_label, draw_progress_bar, draw_status_dot


class HomeScreen(Screen):
    name = "home"

    def __init__(self, context: ScreenContext) -> None:
        super().__init__(context)
        sprite_path = self.context.app.config.root_dir / "assets" / "mascot.png"
        frames = load_sprite_sheet(sprite_path)
        self.animator = SpriteAnimator(frames, self.context.app.config.ui.mascot_frame_seconds)
        self.message_index = 0
        self.message_elapsed = 0.0

    def update(self, dt: float) -> bool:
        changed = self.animator.update(dt)
        self.message_elapsed += dt
        if self.message_elapsed >= self.context.app.config.ui.message_interval:
            self.message_elapsed = 0.0
            self.message_index = (self.message_index + 1) % len(MESSAGES)
            changed = True
        return changed

    def render(self, draw: ImageDraw.ImageDraw, buffer) -> None:
        app = self.context.app
        stats = app.system_snapshot()
        now = time.strftime("%H:%M")
        uptime = stats["uptime"]
        mascot = self.animator.current()
        width = app.config.display.width
        buffer.paste(mascot, (16, 28))

        draw_label(draw, 84, 48, f"{now}", app.font_large, FG)
        draw_label(draw, 84, 70, f"uptime {uptime}", app.font, FG)
        draw_label(draw, 20, 118, f"\"{MESSAGES[self.message_index][:28]}\"", app.font, ACCENT)

        draw_status_dot(draw, 20, 164, True, ACCENT)
        draw_label(draw, 36, 160, f"{stats['terminal_windows']} shells", app.font)
        draw_status_dot(draw, 136, 164, app.bluetooth_status.connected, ACCENT)
        draw_label(draw, 152, 160, "BT", app.font)
        temp_x = width - 76
        draw_label(draw, temp_x, 160, stats["temperature_label"], app.font)
        draw_progress_bar(draw, temp_x, 178, 56, stats["temperature_pct"], ACCENT)

    def on_button(self, button: str, long_press: bool) -> bool:
        if button == "X":
            self.context.app.set_screen("term")
            return True
        if button == "Y":
            self.context.app.set_screen("system")
            return True
        if button == "A":
            self.message_index = (self.message_index - 1) % len(MESSAGES)
            return True
        if button == "B":
            self.message_index = (self.message_index + 1) % len(MESSAGES)
            return True
        return False

    def get_button_hints(self) -> list[str]:
        return ["A prev", "B next", "X term", "Y sys"]
