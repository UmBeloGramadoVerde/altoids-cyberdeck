from __future__ import annotations

import time

from PIL import ImageDraw

from ..colors import ACCENT, AUX, COOL, DIM, FG, INFO, SURFACE_ALT, SURFACE_INSET, WARN
from ..messages import MESSAGES
from ..sprites import SpriteAnimator, load_mascot_frames
from .base import Screen, ScreenContext
from .widgets import draw_label, draw_panel, draw_scanlines, draw_segmented_bar, draw_status_dot


class HomeScreen(Screen):
    name = "home"

    def __init__(self, context: ScreenContext) -> None:
        super().__init__(context)
        frames = load_mascot_frames()
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
        height = app.config.display.height
        footer_height = 24 if app.shows_button_bar else 0
        content_bottom = height - footer_height - 8
        bluetooth_name = app.bluetooth_status.device_name or "SCAN"
        message_lines = self._message_lines(MESSAGES[self.message_index], 27)
        signature = (width, height, footer_height)
        buffer.paste(self.cached_background(signature, buffer.size, self._paint_static_background))
        draw = ImageDraw.Draw(buffer)

        mascot_bounds = (12, 24, 100, 124)
        clock_bounds = (108, 24, width - 12, 96)
        marquee_bounds = (12, 132, width - 12, 170)
        left_status_bounds = (12, 178, 94, content_bottom)
        mid_status_bounds = (100, 178, 182, content_bottom)
        right_status_bounds = (188, 178, width - 12, content_bottom)

        sprite_x = mascot_bounds[0] + (mascot_bounds[2] - mascot_bounds[0] - mascot.width) // 2
        sprite_y = mascot_bounds[1] + (mascot_bounds[3] - mascot_bounds[1] - mascot.height) // 2 + 4
        buffer.paste(mascot, (sprite_x, sprite_y))
        draw_label(draw, mascot_bounds[0] + 12, mascot_bounds[3] - 18, "< idle loop >", app.font, COOL)

        draw_label(draw, 122, 40, now, app.font_large, FG)
        draw_label(draw, 122, 68, f"UP {uptime}", app.font, FG)
        draw_segmented_bar(draw, 190, 72, 62, stats["cpu_pct"], segments=7, color=ACCENT)
        draw_label(draw, 190, 58, f"CPU {int(stats['cpu_pct'] * 100):>3}%", app.font, DIM)

        draw_label(draw, 24, 144, ">>", app.terminal_font, WARN)
        draw_label(draw, 48, 142, message_lines[0], app.font, FG)
        if len(message_lines) > 1:
            draw_label(draw, 48, 154, message_lines[1], app.font, WARN)

        draw_status_dot(draw, 22, 191, True, ACCENT)
        draw_label(draw, 36, 188, f"{stats['terminal_windows']:>2}", app.font_large, FG)
        draw_label(draw, 58, content_bottom - 20, "LIVE", app.font, DIM)

        draw_status_dot(draw, 110, 191, app.bluetooth_status.connected, INFO)
        draw_label(draw, 124, 188, "LINK" if app.bluetooth_status.connected else "IDLE", app.font, FG if app.bluetooth_status.connected else DIM)
        draw_label(draw, 108, content_bottom - 20, self._trim(bluetooth_name.upper(), 8), app.font, INFO if app.bluetooth_status.connected else DIM)

        temp_color = ACCENT if not stats["temperature_hot"] else WARN
        draw_label(draw, 198, 188, stats["temperature_label"], app.font_large, temp_color)
        draw_segmented_bar(draw, 198, content_bottom - 14, 54, stats["temperature_pct"], segments=6, color=temp_color)

    def _paint_static_background(self, draw: ImageDraw.ImageDraw, buffer) -> None:
        app = self.context.app
        width = app.config.display.width
        height = app.config.display.height
        footer_height = 24 if app.shows_button_bar else 0
        content_bottom = height - footer_height - 8

        draw_label(draw, 12, 8, "HOME // FIELD UNIT", app.font, ACCENT)
        draw_label(draw, width - 88, 8, "VFD READY", app.font, WARN)

        mascot_bounds = (12, 24, 100, 124)
        clock_bounds = (108, 24, width - 12, 96)
        marquee_bounds = (12, 132, width - 12, 170)
        left_status_bounds = (12, 178, 94, content_bottom)
        mid_status_bounds = (100, 178, 182, content_bottom)
        right_status_bounds = (188, 178, width - 12, content_bottom)

        draw_panel(draw, mascot_bounds, title="MASCOT", title_font=app.font, outline=AUX, title_color=AUX, fill=SURFACE_ALT, inner_outline=SURFACE_INSET)
        draw_scanlines(draw, mascot_bounds, step=6)
        draw_panel(draw, clock_bounds, title="LOCAL", title_font=app.font, outline=INFO, title_color=INFO)
        draw_scanlines(draw, clock_bounds, step=6)
        draw_panel(draw, marquee_bounds, title="MARQUEE", title_font=app.font, outline=WARN, title_color=WARN, fill=SURFACE_ALT, inner_outline=SURFACE_INSET)
        draw_scanlines(draw, marquee_bounds, step=5, color=SURFACE_INSET)
        draw_panel(draw, left_status_bounds, title="SHELLS", title_font=app.font, outline=ACCENT, title_color=ACCENT)
        draw_panel(draw, mid_status_bounds, title="BT", title_font=app.font, outline=INFO, title_color=INFO)
        draw_panel(draw, right_status_bounds, title="THERM", title_font=app.font, outline=ACCENT, title_color=ACCENT)

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

    @staticmethod
    def _trim(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return f"{text[: max(0, limit - 1)]}>"

    @classmethod
    def _message_lines(cls, text: str, limit: int) -> list[str]:
        words = text.replace('"', "").split()
        if not words:
            return [""]
        lines: list[str] = []
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if len(candidate) <= limit:
                current = candidate
                continue
            lines.append(cls._trim(current, limit))
            current = word
            if len(lines) == 1:
                continue
            break
        if len(lines) < 2:
            lines.append(cls._trim(current, limit))
        return lines[:2]

    def debug_state(self) -> dict[str, object]:
        return {
            "message_index": self.message_index,
            "message": MESSAGES[self.message_index],
        }
