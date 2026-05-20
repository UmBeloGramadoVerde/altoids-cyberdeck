from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

from PIL import ImageDraw

from ..colors import ACCENT, DIM, FG, SURFACE, SURFACE_ALT, SURFACE_GRID, SURFACE_INSET, SURFACE_PANEL
from ..input_keyboard import KeyboardEvent
from ..renderer import cell_width, render_terminal, strip_ansi, terminal_cell_advance
from .base import Screen, ScreenContext
from .widgets import BUTTON_BAR_HEIGHT
from .widgets import draw_label
from .widgets import draw_status_dot


@dataclass(slots=True)
class TerminalLayout:
    origin: tuple[int, int]
    body_left: int
    body_top: int
    body_right: int
    body_bottom: int
    visible_rows: int
    visible_cols: int
    minimal: bool


class TerminalScreen(Screen):
    name = "term"
    _origin = (12, 36)
    _header_height = 20
    _refresh_interval = 0.08

    def __init__(self, context: ScreenContext) -> None:
        super().__init__(context)
        self.scroll_offset = 0
        self._refresh_elapsed = 0.0
        self._last_visible_rows = context.app.config.terminal.height_chars
        self._last_layout_minimal = False

    def update(self, dt: float) -> bool:
        self._refresh_elapsed += dt
        if self._refresh_elapsed >= self._refresh_interval:
            self._refresh_elapsed = 0.0
            return True
        return False

    @property
    def _line_height(self) -> int:
        bbox = self.context.app.terminal_font.getbbox("Ag")
        return max(12, bbox[3] - bbox[1] + 1)

    def render(self, draw: ImageDraw.ImageDraw, buffer) -> None:
        app = self.context.app
        layout = self._layout_for_state(self._last_layout_minimal)
        app.tmux.resize(layout.visible_cols, layout.visible_rows)
        snapshot = app.tmux.capture(
            self.scroll_offset,
            height_rows=layout.visible_rows,
            fast=app.input_render_pending,
        )
        layout = self._layout_for_snapshot(snapshot)
        self._last_visible_rows = layout.visible_rows
        self._last_layout_minimal = layout.minimal
        width = app.config.display.width
        signature = (layout.minimal, width, app.config.display.height, app.shows_button_bar)
        buffer.paste(self.cached_background(signature, buffer.size, lambda bg_draw, bg_buffer: self._paint_static_background(bg_draw, bg_buffer, layout, width)))
        draw = ImageDraw.Draw(buffer)

        if not layout.minimal:
            self._draw_frame(draw, width, layout, snapshot)
        lines = snapshot.lines
        render_terminal(
            draw,
            lines[-layout.visible_rows :],
            app.terminal_font,
            layout.origin,
            line_height=self._line_height,
            max_rows=layout.visible_rows,
            max_cols=layout.visible_cols,
        )
        self._draw_cursor(draw, layout, snapshot)

    def _paint_static_background(self, draw: ImageDraw.ImageDraw, buffer, layout: TerminalLayout, width: int) -> None:
        if layout.minimal:
            self._draw_minimal_frame(draw, layout)
            return
        draw.rounded_rectangle(
            (layout.body_left, layout.body_top, layout.body_right, layout.body_bottom),
            radius=8,
            outline=DIM,
            fill=SURFACE,
        )
        body_height = layout.body_bottom - layout.body_top
        for y in range(layout.body_top + 1, layout.body_top + body_height, 4):
            draw.line((layout.body_left + 2, y, layout.body_right - 2, y), fill=SURFACE_GRID, width=1)

    def on_button(self, button: str, long_press: bool) -> bool:
        terminal_cfg = self.context.app.config.terminal
        if button == "A":
            if long_press:
                self.context.app.tmux.create_window()
                self.scroll_offset = 0
                return True
            self.scroll_offset += terminal_cfg.scroll_step * (10 if long_press else 1)
            return True
        if button == "B":
            if long_press:
                self.context.app.tmux.close_active_window()
                self.scroll_offset = 0
                return True
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
        return ["A up/new", "B dn/kill", "X win", "Y enter"]

    def on_keyboard_event(self, event: KeyboardEvent) -> bool:
        if event.alt:
            return False
        if event.key in {"up", "down", "pageup", "pagedown", "home", "end"} and self._active_pane_wants_navigation_keys():
            self.scroll_offset = 0
            self.context.app.tmux.send_keys([self._tmux_key_name(event.key)])
            return True
        # Treat plain navigation keys as viewport controls on the terminal
        # screen so they scroll the captured tmux buffer instead of being
        # forwarded into the shell.
        if event.key == "up":
            self._scroll_lines(self.context.app.config.terminal.scroll_step)
            return True
        if event.key == "down":
            self._scroll_lines(-self.context.app.config.terminal.scroll_step)
            return True
        if event.key == "pageup":
            self._scroll_lines(self._page_scroll_step())
            return True
        if event.key == "pagedown":
            self._scroll_lines(-self._page_scroll_step())
            return True
        if event.key == "home":
            self.scroll_offset = self.context.app.config.terminal.pane_history
            return True
        if event.key == "end":
            self.scroll_offset = 0
            return True
        return False

    def _draw_frame(
        self,
        draw: ImageDraw.ImageDraw,
        width: int,
        layout: TerminalLayout,
        snapshot,
    ) -> None:
        draw.rounded_rectangle((layout.body_left, 8, width - 8, 32), radius=6, outline=ACCENT, fill=SURFACE_PANEL)

        pane_name = snapshot.pane_title.strip() or snapshot.pane_command
        summary = self._window_summary(snapshot)
        self._draw_header_strip(draw, width, pane_name, summary)

    def _draw_header_strip(
        self,
        draw: ImageDraw.ImageDraw,
        width: int,
        pane_name: str,
        summary: str,
    ) -> None:
        top = 8
        bottom = top + self._header_height
        draw.rounded_rectangle((8, top, width - 8, bottom), radius=6, outline=ACCENT, fill=SURFACE_PANEL)
        draw_status_dot(draw, 14, top + 6, self.scroll_offset == 0, color=ACCENT)
        draw.rectangle((28, top + 7, 34, top + 13), outline=DIM, fill=None)
        if self.scroll_offset == 0:
            draw.line((40, top + 13, 44, top + 7), fill=ACCENT, width=1)
            draw.line((44, top + 7, 48, top + 13), fill=ACCENT, width=1)
        else:
            draw.line((40, top + 8, 44, top + 12), fill=DIM, width=1)
            draw.line((44, top + 12, 48, top + 8), fill=DIM, width=1)
        summary_width = len(summary) * cell_width(self.context.app.font)
        status_width = 12 if self.scroll_offset == 0 else len(str(self.scroll_offset)) * cell_width(self.context.app.font) + 6
        status_x = width - 16 - status_width
        count_x = status_x - 6 - summary_width
        title_limit = max(8, (count_x - 54) // max(1, cell_width(self.context.app.font)))
        draw_label(draw, 54, top + 5, self._truncate(pane_name, title_limit), self.context.app.font, FG)
        draw_label(draw, count_x, top + 5, summary, self.context.app.font, ACCENT)
        if self.scroll_offset == 0:
            draw_status_dot(draw, status_x, top + 6, True, color=ACCENT)
        else:
            draw_label(draw, status_x, top + 5, str(self.scroll_offset), self.context.app.font, DIM)

    def _draw_minimal_frame(self, draw: ImageDraw.ImageDraw, layout: TerminalLayout) -> None:
        draw.rounded_rectangle(
            (layout.body_left, layout.body_top, layout.body_right, layout.body_bottom),
            radius=4,
            outline=SURFACE_INSET,
            fill=SURFACE_ALT,
        )

    def _scroll_lines(self, delta: int) -> None:
        self.scroll_offset = max(0, min(self.context.app.config.terminal.pane_history, self.scroll_offset + delta))

    def _page_scroll_step(self) -> int:
        return max(self.context.app.config.terminal.scroll_step * 2, self._last_visible_rows - 1)

    @staticmethod
    def _tmux_key_name(key: str) -> str:
        return {
            "up": "Up",
            "down": "Down",
            "home": "Home",
            "end": "End",
            "pageup": "PageUp",
            "pagedown": "PageDown",
        }[key]

    def _layout_for_snapshot(self, snapshot) -> TerminalLayout:
        return self._layout_for_state(self._is_minimal_snapshot(snapshot))

    def _layout_for_state(self, minimal: bool) -> TerminalLayout:
        width = self.context.app.config.display.width
        height = self.context.app.config.display.height
        if minimal:
            origin = (4, 8)
            body_left = 2
            body_top = 2
            body_right = width - 2
            body_bottom = height - BUTTON_BAR_HEIGHT - 2
        else:
            origin = self._origin
            body_left = 8
            body_top = self._origin[1] - 6
            body_right = width - 8
            body_bottom = height - BUTTON_BAR_HEIGHT - 4
        visible_rows = max(1, (body_bottom - origin[1]) // self._line_height)
        visible_cols = max(1, int((body_right - origin[0]) // terminal_cell_advance(self.context.app.terminal_font)))
        return TerminalLayout(
            origin=origin,
            body_left=body_left,
            body_top=body_top,
            body_right=body_right,
            body_bottom=body_bottom,
            visible_rows=visible_rows,
            visible_cols=visible_cols,
            minimal=minimal,
        )

    def _is_minimal_snapshot(self, snapshot) -> bool:
        commands = {item.lower() for item in self.context.app.config.terminal.minimal_commands}
        pane_command = Path(snapshot.pane_command).name.lower()
        pane_title = snapshot.pane_title.strip().lower()
        if pane_command in commands:
            return True
        if any(pane_title == command or pane_title.startswith(f"{command} ") for command in commands):
            return True
        if pane_command != "node":
            return False
        return self._has_codex_tui_markers(snapshot.lines)

    def _active_pane_wants_navigation_keys(self) -> bool:
        snapshot = self.context.app.tmux.capture(
            0,
            height_rows=max(1, self._last_visible_rows),
            fast=True,
        )
        pane_command = Path(snapshot.pane_command).name.lower()
        pane_title = snapshot.pane_title.strip().lower()
        if pane_command == "cdx" or pane_title.startswith("cdx"):
            return True
        return self._has_cdx_markers(snapshot.lines)

    @staticmethod
    def _has_cdx_markers(lines: list[str]) -> bool:
        for raw_line in lines:
            line = strip_ansi(raw_line).strip()
            if line.startswith("cdx  cx:") or line.startswith("cdx startup"):
                return True
        return False

    @staticmethod
    def _has_codex_tui_markers(lines: list[str]) -> bool:
        markers = 0
        for raw_line in lines[-12:]:
            line = strip_ansi(raw_line).strip()
            if not line:
                continue
            if line.startswith("› "):
                markers += 1
            elif line.startswith("─ Worked for "):
                markers += 1
            elif "gpt-" in line and any(level in line for level in (" low", " medium", " high", " xhigh")):
                markers += 1
            elif "By continuing, you agree" in line:
                markers += 1
        return markers >= 2

    def _draw_cursor(self, draw: ImageDraw.ImageDraw, layout: TerminalLayout, snapshot) -> None:
        if not snapshot.cursor_visible or snapshot.pane_in_mode or self.scroll_offset != 0:
            return
        if int(time.monotonic() * 2) % 2:
            return
        if snapshot.cursor_y < 0 or snapshot.cursor_y >= layout.visible_rows:
            return
        if snapshot.cursor_x < 0 or snapshot.cursor_x >= layout.visible_cols:
            return
        width = terminal_cell_advance(self.context.app.terminal_font)
        x = int(round(layout.origin[0] + snapshot.cursor_x * width))
        y = layout.origin[1] + snapshot.cursor_y * self._line_height
        draw.rectangle((x, y, x + max(1, int(round(width)) - 2), y + self._line_height - 2), outline=ACCENT)

    @staticmethod
    def _window_summary(snapshot) -> str:
        current = snapshot.active_window_position or 1
        total = max(1, snapshot.window_count)
        return f"{current}/{total}"

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        cleaned = strip_ansi(text).replace("\n", " ").strip()
        if len(cleaned) <= limit:
            return cleaned
        return f"{cleaned[: max(0, limit - 1)]}>"

    @staticmethod
    def _short_path(raw_path: str, max_parts: int = 2) -> str:
        if not raw_path or raw_path == "-":
            return "-"
        path = Path(raw_path)
        parts = [part for part in path.parts if part not in {"/", ""}]
        if len(parts) <= max_parts:
            return str(path)
        return f"~/{'/'.join(parts[-max_parts:])}"

    def debug_state(self) -> dict[str, object]:
        snapshot = self.context.app.tmux.capture(self.scroll_offset, height_rows=1)
        return {
            "scroll_offset": self.scroll_offset,
            "last_visible_rows": self._last_visible_rows,
            "line_height": self._line_height,
            "codex_session_id": None,
            "codex_source_path": None,
        }
