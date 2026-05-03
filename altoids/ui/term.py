from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import ImageDraw

from ..colors import ACCENT, DIM, FG
from ..renderer import cell_width, render_terminal, strip_ansi
from .base import Screen, ScreenContext
from .widgets import BUTTON_BAR_HEIGHT
from .widgets import draw_label


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
    _origin = (12, 42)
    _header_height = 28
    _refresh_interval = 0.08

    def __init__(self, context: ScreenContext) -> None:
        super().__init__(context)
        self.scroll_offset = 0
        self._refresh_elapsed = 0.0

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
        snapshot = app.tmux.capture(self.scroll_offset)
        layout = self._layout_for_snapshot(snapshot)
        app.tmux.resize(layout.visible_cols, layout.visible_rows)
        snapshot = app.tmux.capture(self.scroll_offset, height_rows=layout.visible_rows)
        layout = self._layout_for_snapshot(snapshot)
        width = app.config.display.width

        if layout.minimal:
            self._draw_minimal_frame(draw, layout)
        else:
            self._draw_frame(draw, width, layout, snapshot)
            body_height = layout.body_bottom - layout.body_top
            for y in range(layout.body_top + 1, layout.body_top + body_height, 4):
                draw.line((layout.body_left + 2, y, layout.body_right - 2, y), fill="#101010", width=1)
        render_terminal(
            draw,
            snapshot.lines[-layout.visible_rows :],
            app.terminal_font,
            layout.origin,
            line_height=self._line_height,
            max_rows=layout.visible_rows,
            max_cols=layout.visible_cols,
        )

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

    def _draw_frame(
        self,
        draw: ImageDraw.ImageDraw,
        width: int,
        layout: TerminalLayout,
        snapshot,
    ) -> None:
        draw.rounded_rectangle((layout.body_left, 8, width - 8, 32), radius=6, outline=ACCENT, fill="#101513")
        draw.rounded_rectangle(
            (layout.body_left, layout.body_top, layout.body_right, layout.body_bottom),
            radius=8,
            outline=DIM,
            fill="#080B0A",
        )

        active_window = snapshot.active_window.lstrip("* ").strip() or "-"
        pane_name = snapshot.pane_title.strip() or snapshot.pane_command
        pane_path = self._short_path(snapshot.pane_path, max_parts=2)
        scroll_label = "LIVE" if self.scroll_offset == 0 else f"SCROLL -{self.scroll_offset}"
        header_left = f"{active_window} {pane_name}".strip()
        draw_label(draw, 14, 14, self._truncate(header_left, 22), self.context.app.font, FG)
        draw_label(draw, 192, 14, f"{snapshot.window_count:02} WIN", self.context.app.font, ACCENT)
        draw_label(draw, 248, 14, self._truncate(scroll_label, 11), self.context.app.font, FG)
        draw_label(draw, 14, 30, self._truncate(pane_path, 28), self.context.app.font, DIM)

    def _draw_minimal_frame(self, draw: ImageDraw.ImageDraw, layout: TerminalLayout) -> None:
        draw.rounded_rectangle(
            (layout.body_left, layout.body_top, layout.body_right, layout.body_bottom),
            radius=4,
            outline="#1A2420",
            fill="#060806",
        )

    def _layout_for_snapshot(self, snapshot) -> TerminalLayout:
        width = self.context.app.config.display.width
        height = self.context.app.config.display.height
        minimal = self._is_minimal_snapshot(snapshot)
        if minimal:
            origin = (4, 8)
            body_left = 2
            body_top = 2
            body_right = width - 2
            body_bottom = height - BUTTON_BAR_HEIGHT - 2
        else:
            origin = self._origin
            body_left = 8
            body_top = self._origin[1] - 8
            body_right = width - 8
            body_bottom = height - BUTTON_BAR_HEIGHT - 4
        visible_rows = max(1, (body_bottom - origin[1]) // self._line_height)
        visible_cols = max(1, (body_right - origin[0] - 2) // cell_width(self.context.app.terminal_font))
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
        return any(pane_title == command or pane_title.startswith(f"{command} ") for command in commands)

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
