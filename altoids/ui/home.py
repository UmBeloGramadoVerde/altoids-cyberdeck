from __future__ import annotations

from dataclasses import dataclass
import time
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw

from ..buttons import LEFT_BOTTOM, LEFT_TOP, RIGHT_BOTTOM, RIGHT_TOP
from ..colors import ACCENT, AUX, COOL, DANGER, DIM, FG, INFO, SURFACE_ALT, SURFACE_INSET, WARN
from ..messages import MESSAGES
from ..sprites import SpriteAnimator, load_mascot_frames
from .base import Screen, ScreenContext
from .widgets import draw_label, draw_panel, draw_scanlines, draw_segmented_bar, draw_status_dot

if TYPE_CHECKING:
    from ..input_keyboard import KeyboardEvent


@dataclass(slots=True)
class PetState:
    snack: float = 0.68
    play: float = 0.58
    charge: float = 0.74
    action: str = "BOOT"
    action_elapsed: float = 2.0
    mood_elapsed: float = 0.0


class HomeScreen(Screen):
    name = "home"

    def __init__(self, context: ScreenContext) -> None:
        super().__init__(context)
        frames = load_mascot_frames()
        self.animator = SpriteAnimator(frames, self.context.app.config.ui.mascot_frame_seconds)
        self.pet = PetState()
        self.message_index = 0
        self.message_elapsed = 0.0

    def update(self, dt: float) -> bool:
        changed = self.animator.update(dt)
        self.pet.mood_elapsed += dt
        self.pet.snack = self._clamp01(self.pet.snack - dt * 0.004)
        self.pet.play = self._clamp01(self.pet.play - dt * 0.003)
        self.pet.charge = self._clamp01(self.pet.charge - dt * 0.002)
        if self.pet.action_elapsed > 0:
            self.pet.action_elapsed = max(0.0, self.pet.action_elapsed - dt)
            changed = True
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
        layout = self._layout(width, height, app.side_bar_width)
        bluetooth_name = app.bluetooth_status.device_name or "SCAN"
        message_limit = max(8, (layout["marquee_bounds"][2] - layout["marquee_bounds"][0] - 48) // 7)
        message_lines = self._message_lines(MESSAGES[self.message_index], message_limit)
        signature = (width, height, app.side_bar_width)
        buffer.paste(self.cached_background(signature, buffer.size, self._paint_static_background))
        draw = ImageDraw.Draw(buffer)

        mascot_bounds = layout["mascot_bounds"]
        clock_bounds = layout["clock_bounds"]
        care_bounds = layout["care_bounds"]
        marquee_bounds = layout["marquee_bounds"]
        left_status_bounds = layout["left_status_bounds"]
        mid_status_bounds = layout["mid_status_bounds"]
        right_status_bounds = layout["right_status_bounds"]
        content_bottom = layout["content_bottom"]
        clock_left = clock_bounds[0] + 14
        clock_bar_width = max(36, clock_bounds[2] - clock_bounds[0] - 72)

        self._draw_pet(draw, buffer, mascot, mascot_bounds)

        draw_label(draw, clock_left, 40, now, app.font_large, FG)
        draw_label(draw, clock_left, 68, f"UP {uptime}", app.font, FG)
        draw_label(draw, clock_bounds[2] - 66, 58, f"CPU {int(stats['cpu_pct'] * 100):>3}%", app.font, DIM)
        draw_segmented_bar(draw, clock_bounds[2] - clock_bar_width - 14, 72, clock_bar_width, stats["cpu_pct"], segments=5, color=ACCENT)

        care_step = max(36, (care_bounds[2] - care_bounds[0]) // 3)
        for index, (label, value, color) in enumerate(
            [("SNK", self.pet.snack, WARN), ("FUN", self.pet.play, AUX), ("PWR", self.pet.charge, ACCENT)]
        ):
            self._draw_care_meter(draw, care_bounds[0] + 10 + index * care_step, care_bounds[1] + 21, label, value, color)

        marquee_left = marquee_bounds[0] + 12
        draw_label(draw, marquee_left, marquee_bounds[1] + 17, ">>", app.terminal_font, WARN)
        draw_label(draw, marquee_left + 24, marquee_bounds[1] + 18, message_lines[0], app.font, FG)
        if len(message_lines) > 1:
            draw_label(draw, marquee_left + 24, marquee_bounds[1] + 27, message_lines[1], app.font, WARN)

        draw_status_dot(draw, left_status_bounds[0] + 10, left_status_bounds[1] + 13, True, ACCENT)
        draw_label(draw, left_status_bounds[0] + 24, left_status_bounds[1] + 10, f"{stats['terminal_windows']:>2}", app.font_large, FG)
        draw_label(draw, left_status_bounds[0] + 46, content_bottom - 20, "LIVE", app.font, DIM)

        draw_status_dot(draw, mid_status_bounds[0] + 10, mid_status_bounds[1] + 13, app.bluetooth_status.connected, INFO)
        draw_label(
            draw,
            mid_status_bounds[0] + 24,
            mid_status_bounds[1] + 10,
            "LINK" if app.bluetooth_status.connected else "IDLE",
            app.font,
            FG if app.bluetooth_status.connected else DIM,
        )
        draw_label(
            draw,
            mid_status_bounds[0] + 8,
            content_bottom - 20,
            self._trim(bluetooth_name.upper(), max(4, (mid_status_bounds[2] - mid_status_bounds[0] - 16) // 7)),
            app.font,
            INFO if app.bluetooth_status.connected else DIM,
        )

        temp_color = ACCENT if not stats["temperature_hot"] else WARN
        draw_label(draw, right_status_bounds[0] + 10, right_status_bounds[1] + 10, stats["temperature_label"], app.font_large, temp_color)
        draw_segmented_bar(
            draw,
            right_status_bounds[0] + 10,
            content_bottom - 14,
            max(40, right_status_bounds[2] - right_status_bounds[0] - 20),
            stats["temperature_pct"],
            segments=6,
            color=temp_color,
        )

    def _paint_static_background(self, draw: ImageDraw.ImageDraw, buffer) -> None:
        app = self.context.app
        width = app.config.display.width
        height = app.config.display.height
        layout = self._layout(width, height, app.side_bar_width)

        draw_label(draw, 12, 8, "HOME // FIELD UNIT", app.font, ACCENT)
        draw_label(draw, width - 88, 8, "VFD READY", app.font, WARN)

        mascot_bounds = layout["mascot_bounds"]
        clock_bounds = layout["clock_bounds"]
        care_bounds = layout["care_bounds"]
        marquee_bounds = layout["marquee_bounds"]
        left_status_bounds = layout["left_status_bounds"]
        mid_status_bounds = layout["mid_status_bounds"]
        right_status_bounds = layout["right_status_bounds"]

        draw_panel(draw, mascot_bounds, title="MASCOT", title_font=app.font, outline=AUX, title_color=AUX, fill=SURFACE_ALT, inner_outline=SURFACE_INSET)
        draw_scanlines(draw, mascot_bounds, step=6)
        draw_panel(draw, clock_bounds, title="LOCAL", title_font=app.font, outline=INFO, title_color=INFO)
        draw_scanlines(draw, clock_bounds, step=6)
        draw_panel(draw, care_bounds, title="CARE", title_font=app.font, outline=COOL, title_color=COOL, fill=SURFACE_ALT, inner_outline=SURFACE_INSET)
        draw_scanlines(draw, care_bounds, step=6, color=SURFACE_INSET)
        draw_panel(draw, marquee_bounds, title="MARQUEE", title_font=app.font, outline=WARN, title_color=WARN, fill=SURFACE_ALT, inner_outline=SURFACE_INSET)
        draw_scanlines(draw, marquee_bounds, step=5, color=SURFACE_INSET)
        draw_panel(draw, left_status_bounds, title="SHELLS", title_font=app.font, outline=ACCENT, title_color=ACCENT)
        draw_panel(draw, mid_status_bounds, title="BT", title_font=app.font, outline=INFO, title_color=INFO)
        draw_panel(draw, right_status_bounds, title="THERM", title_font=app.font, outline=ACCENT, title_color=ACCENT)

    @staticmethod
    def _layout(width: int, height: int, side_bar_width: int) -> dict[str, object]:
        panel_left = side_bar_width + 6
        panel_right = width - side_bar_width - 6
        content_bottom = height - 8
        mascot_bounds = (panel_left, 24, panel_left + 104, 136)
        clock_left = mascot_bounds[2] + 8
        clock_bounds = (clock_left, 24, panel_right, 94)
        care_bounds = (clock_left, 102, panel_right, 136)
        marquee_bounds = (panel_left, 144, panel_right, 176)
        bottom_inner = panel_right - panel_left
        gap = 6
        panel_width = (bottom_inner - gap * 2) // 3
        left_status_bounds = (panel_left, 184, panel_left + panel_width, content_bottom)
        mid_left = left_status_bounds[2] + gap
        mid_status_bounds = (mid_left, 184, mid_left + panel_width, content_bottom)
        right_status_bounds = (mid_status_bounds[2] + gap, 184, panel_right, content_bottom)
        return {
            "content_bottom": content_bottom,
            "mascot_bounds": mascot_bounds,
            "clock_bounds": clock_bounds,
            "care_bounds": care_bounds,
            "marquee_bounds": marquee_bounds,
            "left_status_bounds": left_status_bounds,
            "mid_status_bounds": mid_status_bounds,
            "right_status_bounds": right_status_bounds,
        }

    def on_button(self, slot: str, long_press: bool) -> bool:
        if slot == RIGHT_TOP:
            self.context.app.set_screen("term")
            return True
        if slot == RIGHT_BOTTOM:
            self.context.app.set_screen("system")
            return True
        if slot == LEFT_TOP:
            if long_press:
                self._cycle_message(-1)
                return True
            return self._feed_pet()
        if slot == LEFT_BOTTOM:
            if long_press:
                self._cycle_message(1)
                return True
            return self._play_with_pet()
        return False

    def on_keyboard_event(self, event: "KeyboardEvent") -> bool:
        if event.event_type != "press" or event.ctrl or event.alt:
            return False
        if event.key in {"a", "f"}:
            return self._feed_pet()
        if event.key in {"b", "p", " "}:
            return self._play_with_pet()
        if event.key == "left":
            self._cycle_message(-1)
            return True
        if event.key == "right":
            self._cycle_message(1)
            return True
        if event.key in {"x", "t"}:
            self.context.app.set_screen("term")
            return True
        if event.key in {"y", "s"}:
            self.context.app.set_screen("system")
            return True
        return False

    def get_button_hints(self) -> dict[str, str]:
        return {
            LEFT_TOP: "feed",
            LEFT_BOTTOM: "play",
            RIGHT_TOP: "term",
            RIGHT_BOTTOM: "sys",
        }

    def _draw_pet(
        self,
        draw: ImageDraw.ImageDraw,
        buffer: Image.Image,
        mascot: Image.Image,
        bounds: tuple[int, int, int, int],
    ) -> None:
        app = self.context.app
        mood = self._pet_mood()
        mood_color = self._pet_mood_color()
        left, top, right, bottom = bounds
        pulse = int(self.pet.mood_elapsed * 5) % 4
        sprite = mascot.resize((64, 64), Image.NEAREST)
        sprite_x = left + (right - left - sprite.width) // 2
        sprite_y = top + 30 + (2 if pulse in {1, 2} else 0)

        draw.rounded_rectangle((sprite_x - 7, sprite_y - 7, sprite_x + 70, sprite_y + 70), radius=10, outline=mood_color, fill=None)
        draw.line((sprite_x + 8, sprite_y - 12, sprite_x + 16, sprite_y - 4), fill=COOL, width=1)
        draw.line((sprite_x + 54, sprite_y - 12, sprite_x + 48, sprite_y - 4), fill=COOL, width=1)
        draw_status_dot(draw, sprite_x + 5, sprite_y - 18, pulse % 2 == 0, WARN)
        draw_status_dot(draw, sprite_x + 56, sprite_y - 18, pulse % 2 == 1, AUX)
        for index in range(4):
            x = left + 12 + index * 22
            y = top + 21 + ((index + pulse) % 3) * 2
            draw.point((x, y), fill=mood_color)
            draw.point((x + 1, y), fill=mood_color)

        buffer.paste(sprite, (sprite_x, sprite_y))
        draw_label(draw, left + 13, top + 18, f"< {mood} >", app.font, mood_color)
        draw_label(draw, left + 15, bottom - 16, self._pet_action_label(), app.font, COOL)

    def _draw_care_meter(self, draw: ImageDraw.ImageDraw, x: int, y: int, label: str, value: float, color: str) -> None:
        app = self.context.app
        draw_label(draw, x, y - 10, label, app.font, DIM)
        draw_segmented_bar(draw, x, y + 2, 34, value, segments=4, color=color)

    def _set_pet_action(self, action: str) -> None:
        self.pet.action = action
        self.pet.action_elapsed = 2.2

    def _feed_pet(self) -> bool:
        self.pet.snack = self._clamp01(self.pet.snack + 0.22)
        self.pet.play = self._clamp01(self.pet.play + 0.03)
        self.pet.charge = self._clamp01(self.pet.charge - 0.04)
        self._set_pet_action("SNACK++")
        return True

    def _play_with_pet(self) -> bool:
        self.pet.play = self._clamp01(self.pet.play + 0.20)
        self.pet.snack = self._clamp01(self.pet.snack - 0.04)
        self.pet.charge = self._clamp01(self.pet.charge - 0.07)
        self._set_pet_action("PLAY!!")
        return True

    def _cycle_message(self, delta: int) -> None:
        self.message_index = (self.message_index + delta) % len(MESSAGES)
        self.message_elapsed = 0.0

    def _pet_action_label(self) -> str:
        if self.pet.action_elapsed > 0:
            return self.pet.action
        return "READY"

    def _pet_mood(self) -> str:
        lowest = min(self.pet.snack, self.pet.play, self.pet.charge)
        if lowest < 0.22:
            if self.pet.snack == lowest:
                return "HUNGRY"
            if self.pet.play == lowest:
                return "BORED"
            return "TIRED"
        if min(self.pet.snack, self.pet.play, self.pet.charge) > 0.78:
            return "HYPER"
        return "CHILL"

    def _pet_mood_color(self) -> str:
        mood = self._pet_mood()
        if mood in {"HUNGRY", "TIRED"}:
            return WARN
        if mood == "BORED":
            return DANGER
        if mood == "HYPER":
            return AUX
        return ACCENT

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

    @staticmethod
    def _clamp01(value: float) -> float:
        return max(0.0, min(1.0, value))

    def debug_state(self) -> dict[str, object]:
        return {
            "message_index": self.message_index,
            "message": MESSAGES[self.message_index],
            "pet": {
                "snack": self.pet.snack,
                "play": self.pet.play,
                "charge": self.pet.charge,
                "mood": self._pet_mood(),
                "action": self._pet_action_label(),
            },
        }
