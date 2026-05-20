from __future__ import annotations

from typing import Any

from PIL import ImageDraw

from ..colors import ACCENT, AUX, BG, COOL, DIM, FG, INFO, SURFACE_ALT, SURFACE_GRID, SURFACE_INSET, WARN
from ..input_keyboard import KeyboardEvent
from ..notes import QuickNote
from .base import Screen, ScreenContext
from .widgets import draw_label, draw_panel, draw_scanlines, draw_separator, draw_status_dot


class NotesScreen(Screen):
    name = "notes"

    def __init__(self, context: ScreenContext) -> None:
        super().__init__(context)
        self.draft = ""
        self.status_line = "READY FOR DROP"
        self.selected_index = 0
        self.source = "typed"

    def render(self, draw: ImageDraw.ImageDraw, buffer) -> None:
        app = self.context.app
        notes = self._notes()
        width = app.config.display.width
        height = app.config.display.height
        footer_height = 24 if app.shows_button_bar else 0
        signature = ("notes", width, height, footer_height)
        buffer.paste(self.cached_background(signature, buffer.size, self._paint_background))
        draw = ImageDraw.Draw(buffer)

        content_bottom = height - footer_height - 8
        notes_bounds = (10, 30, width - 10, 128)
        compose_bounds = (10, 136, width - 10, content_bottom)

        draw_label(draw, 82, 8, self._display_text(self._trim(self.status_line.upper(), 22)), app.font, DIM)
        self._draw_recent_notes(draw, notes, notes_bounds)
        self._draw_composer(draw, compose_bounds)

    def _paint_background(self, draw: ImageDraw.ImageDraw, buffer) -> None:
        app = self.context.app
        width = app.config.display.width
        height = app.config.display.height
        footer_height = 24 if app.shows_button_bar else 0
        content_bottom = height - footer_height - 8

        draw_label(draw, 12, 8, "NOTES", app.font, ACCENT)
        draw_label(draw, width - 76, 8, "CAPTURE", app.font, WARN)
        draw_separator(draw, 20, width)

        notes_bounds = (10, 30, width - 10, 128)
        compose_bounds = (10, 136, width - 10, content_bottom)
        draw_panel(draw, notes_bounds, title="RECENT DROPS", title_font=app.font, outline=COOL, title_color=COOL, fill=SURFACE_ALT, inner_outline=SURFACE_INSET)
        draw_scanlines(draw, notes_bounds, step=6, color=SURFACE_GRID)
        draw_panel(draw, compose_bounds, title="QUICK INPUT", title_font=app.font, outline=ACCENT, title_color=ACCENT, fill=BG, inner_outline=SURFACE_INSET)

    def _draw_recent_notes(
        self,
        draw: ImageDraw.ImageDraw,
        notes: list[QuickNote],
        bounds: tuple[int, int, int, int],
    ) -> None:
        app = self.context.app
        left, top, right, bottom = bounds
        if not notes:
            draw_status_dot(draw, left + 14, top + 34, False, DIM)
            draw_label(draw, left + 30, top + 31, "NO NOTES YET", app.font, DIM)
            draw_label(draw, left + 30, top + 47, "TYPE OR HOLD CMD+SPACE", app.font, INFO)
            return

        visible = notes[:4]
        for index, note in enumerate(visible):
            row_top = top + 24 + index * 17
            selected = index == self.selected_index
            color = FG if selected else DIM
            marker = ACCENT if selected else SURFACE_INSET
            draw.rectangle((left + 10, row_top - 1, right - 10, row_top + 14), outline=marker if selected else None, fill=BG if selected else None)
            draw_label(draw, left + 16, row_top, note.created_label, app.font, WARN if note.source == "voice" else COOL)
            draw_label(draw, left + 52, row_top, self._display_text(self._trim(note.text.upper(), 27)), app.font, color)

        count_label = f"{len(notes):>3} SAVED"
        draw_label(draw, right - 70, bottom - 18, count_label, app.font, INFO)

    def _draw_composer(self, draw: ImageDraw.ImageDraw, bounds: tuple[int, int, int, int]) -> None:
        app = self.context.app
        left, top, right, bottom = bounds
        source_color = WARN if self.source == "voice" else ACCENT
        draw_status_dot(draw, left + 14, top + 24, bool(self.draft), source_color)
        draw_label(draw, left + 30, top + 21, "VOICE" if self.source == "voice" else "TYPE", app.font, source_color)
        draw_label(draw, right - 78, top + 21, f"{len(self.draft):>3} CH", app.font, DIM)

        lines = self._wrap(self.draft or ">", 34, 4)
        y = top + 42
        for line in lines:
            draw_label(draw, left + 16, y, self._display_text(line), app.font, FG if self.draft else DIM)
            y += 15
        if self.draft:
            cursor_x = left + 16 + min(33, len(lines[-1])) * 7
            cursor_y = y - 13
            draw.rectangle((cursor_x, cursor_y, cursor_x + 5, cursor_y + 8), outline=AUX, fill=None)

        draw_label(draw, left + 16, bottom - 16, "ENTER SAVE  BKSP EDIT  CMD+SP", app.font, DIM)

    def on_button(self, button: str, long_press: bool) -> bool:
        if button == "A":
            self._select(-1)
            return True
        if button == "B":
            self._select(1)
            return True
        if button == "X":
            self._commit()
            return True
        if button == "Y":
            if long_press:
                self.context.app.set_screen("home")
            else:
                self.draft = ""
                self.source = "typed"
                self.status_line = "DRAFT CLEARED"
            return True
        return False

    def on_keyboard_event(self, event: KeyboardEvent) -> bool:
        if event.event_type != "press" or event.alt:
            return False
        if event.key == "enter":
            self._commit()
            return True
        if event.key == "backspace":
            self.draft = self.draft[:-1]
            self.status_line = "EDITING"
            return True
        if event.key == "escape":
            self.context.app.set_screen("home")
            return True
        if event.key == "up":
            self._select(-1)
            return True
        if event.key == "down":
            self._select(1)
            return True
        if event.ctrl and event.key == "l":
            self.draft = ""
            self.source = "typed"
            self.status_line = "DRAFT CLEARED"
            return True
        if event.text and not event.ctrl:
            self.draft = f"{self.draft}{event.text}"
            self.source = "typed"
            self.status_line = "CAPTURING"
            return True
        return False

    def insert_voice_text(self, text: str) -> bool:
        text = text.strip()
        if not text:
            return False
        if not self.draft.strip():
            note = self._save_note(text, source="voice")
            if note is None:
                self.draft = text
                self.source = "voice"
                self.status_line = self._save_error_status()
                return True
            self.selected_index = 0
            self.source = "voice"
            self.status_line = "VOICE SAVED"
            return True
        separator = "" if self.draft.endswith((" ", "\n")) else " "
        self.draft = f"{self.draft}{separator}{text}"
        self.source = "voice"
        self.status_line = "VOICE ADDED"
        return True

    def get_button_hints(self) -> list[str]:
        return ["A prev", "B next", "X save", "Y clear"]

    def debug_state(self) -> dict[str, object]:
        return {
            "draft": self.draft,
            "status_line": self.status_line,
            "selected_index": self.selected_index,
            "notes": len(self._notes()),
        }

    def _commit(self) -> None:
        note = self._save_note(self.draft, source=self.source)
        if note is None:
            self.status_line = self._save_error_status() if self.context.app.notes.last_error else "NOTHING TO SAVE"
            return
        self.draft = ""
        self.source = "typed"
        self.selected_index = 0
        self.status_line = "SAVED DROP"

    def _save_error_status(self) -> str:
        error = self.context.app.notes.last_error
        if not error:
            return "SAVE FAILED"
        return self._trim(f"SAVE FAILED {error}", 22)

    def _save_note(self, text: str, *, source: str) -> Any:
        try:
            return self.context.app.notes.add(text, source=source)
        except Exception as exc:
            self.context.app.notes.last_error = str(exc)
            return None

    def _notes(self) -> list[QuickNote]:
        return self.context.app.notes.list_notes()

    def _select(self, delta: int) -> None:
        notes = self._notes()
        if not notes:
            self.selected_index = 0
            self.status_line = "NO SAVED NOTES"
            return
        self.selected_index = min(max(0, self.selected_index + delta), min(3, len(notes) - 1))
        note = notes[self.selected_index]
        self.status_line = f"{note.date_label} {note.created_label}"

    @staticmethod
    def _trim(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return f"{text[: max(0, limit - 1)]}>"

    @staticmethod
    def _display_text(text: str) -> str:
        replacements = {
            "\u2018": "'",
            "\u2019": "'",
            "\u201c": '"',
            "\u201d": '"',
            "\u2013": "-",
            "\u2014": "-",
            "\u2026": "...",
        }
        for source, replacement in replacements.items():
            text = text.replace(source, replacement)
        return text.encode("ascii", "replace").decode("ascii")

    @classmethod
    def _wrap(cls, text: str, limit: int, max_lines: int) -> list[str]:
        if not text:
            return [""]
        words = text.split(" ")
        lines: list[str] = []
        current = ""
        for word in words:
            if not current:
                current = word
                continue
            candidate = f"{current} {word}"
            if len(candidate) <= limit:
                current = candidate
                continue
            lines.append(cls._trim(current, limit))
            current = word
            if len(lines) >= max_lines - 1:
                break
        if len(lines) < max_lines:
            lines.append(cls._trim(current, limit))
        return lines[:max_lines]
