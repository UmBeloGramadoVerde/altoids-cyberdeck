from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Any

from PIL import Image, ImageDraw

from ..chip8 import Chip8, Chip8Error
from ..colors import ACCENT, BG, COOL, DANGER, DIM, FG, INFO, SURFACE_GRID, SURFACE_INSET, SURFACE_PANEL, WARN
from ..input_keyboard import KeyboardEvent
from .base import Screen, ScreenContext
from .widgets import draw_corner_ticks, draw_dot_grid, draw_label, draw_panel, draw_scanlines


SMOKE_CART = bytes.fromhex(
    "00E0"
    "6004"
    "6103"
    "A300"
    "D015"
    "120A"
)
SMOKE_SPRITE = bytes([0xF0, 0x90, 0x90, 0x90, 0xF0])


@dataclass(frozen=True, slots=True)
class Cartridge:
    title: str
    source: str
    data: bytes
    notes: str = ""
    metadata: dict[str, Any] | None = None
    preload: tuple[tuple[int, bytes], ...] = ()


class EmulationScreen(Screen):
    name = "emu"

    rom_dir = Path("roms/chip8")
    cycles_per_frame = 10
    key_hold_seconds = 0.18
    timer_hz = 60.0
    escape_hold_seconds = 0.7

    keyboard_map = {str(index): index for index in range(10)} | {
        "a": 0xA,
        "b": 0xB,
        "c": 0xC,
        "d": 0xD,
        "e": 0xE,
        "f": 0xF,
    }

    navigation_keys = {
        "escape",
        "enter",
        "tab",
        "up",
        "down",
        "left",
        "right",
        "home",
        "end",
        "pageup",
        "pagedown",
    }

    button_map = {
        "A": 0x4,
        "B": 0x6,
        "X": 0x5,
    }

    rom_key_overrides = {
        "chickenScratch": {" ": 0x5},
    }

    def __init__(self, context: ScreenContext) -> None:
        super().__init__(context)
        self.chip8 = Chip8()
        self.cartridges: list[Cartridge] = []
        self.selection = 0
        self.mode = "select"
        self.loaded_title = ""
        self.status = "scan roms/chip8"
        self.blink = 0.0
        self.timer_accumulator = 0.0
        self.held_keys: dict[int, float] = {}
        self.error_message = ""
        self.detail_scroll = 0
        self.run_notice = ""
        self.run_notice_until = 0.0
        self.escape_armed_at: float | None = None
        self.refresh_cartridges()

    def refresh_cartridges(self) -> None:
        metadata_by_key = self._load_archive_metadata()
        cartridges = [
            Cartridge(
                title="SMOKE TEST",
                source="BUILT-IN",
                data=SMOKE_CART,
                notes="Built-in validation cartridge. Draws a fixed sprite to confirm CHIP-8 display and opcode execution.",
                preload=((0x300, SMOKE_SPRITE),),
            )
        ]
        for path in sorted(self.rom_dir.rglob("*")):
            if not path.is_file() or path.name.startswith("."):
                continue
            if path.suffix.lower() not in {"", ".ch8", ".rom", ".bin"}:
                continue
            try:
                data = path.read_bytes()
            except OSError:
                continue
            if data:
                metadata = metadata_by_key.get(path.stem)
                cartridges.append(
                    Cartridge(
                        title=self._metadata_title(path, metadata),
                        source=str(path),
                        data=data,
                        notes=self._metadata_notes(path, metadata),
                        metadata=metadata,
                    )
                )
        self.cartridges = [cartridges[0], *sorted(cartridges[1:], key=lambda cart: cart.title.lower())]
        self.selection = min(self.selection, len(self.cartridges) - 1)
        self.status = f"{len(self.cartridges)} cart{'s' if len(self.cartridges) != 1 else ''}"

    def update(self, dt: float) -> bool:
        self.blink = (self.blink + dt) % 1.0
        self._release_expired_keys()
        if self.escape_armed_at is not None and time.monotonic() - self.escape_armed_at >= self.escape_hold_seconds:
            self.mode = "select"
            self._clear_keys()
            self.escape_armed_at = None
            self.run_notice = ""
            return True
        if self.mode != "run":
            return True
        try:
            for _ in range(self.cycles_per_frame):
                self.chip8.step()
            self.timer_accumulator += dt
            while self.timer_accumulator >= 1.0 / self.timer_hz:
                self.chip8.tick_timers()
                self.timer_accumulator -= 1.0 / self.timer_hz
        except Chip8Error as exc:
            self.mode = "error"
            self.error_message = str(exc)
        return True

    def render(self, draw: ImageDraw.ImageDraw, buffer: Image.Image) -> None:
        app = self.context.app
        width = app.config.display.width
        height = app.config.display.height
        buffer.paste(self.cached_background((width, height), buffer.size, self._paint_static_background))
        draw = ImageDraw.Draw(buffer)

        if self.mode == "run":
            self._render_running(draw, buffer)
        elif self.mode == "detail":
            self._render_detail(draw)
        else:
            self._render_selector(draw)

    def _paint_static_background(self, draw: ImageDraw.ImageDraw, buffer: Image.Image) -> None:
        app = self.context.app
        width = app.config.display.width
        height = app.config.display.height
        draw.rectangle((0, 0, width, height), fill=BG)
        draw.rectangle((8, 8, width - 8, height - 8), outline=SURFACE_INSET)
        draw.rectangle((12, 12, width - 12, height - 12), outline=SURFACE_GRID)
        draw_dot_grid(draw, (12, 12, width - 12, height - 12), step=8, color=SURFACE_GRID)
        draw_label(draw, 16, 13, "EMU BAY // CHIP-8", app.font, WARN)
        draw_label(draw, width - 68, 13, "VFD DIAG", app.font, DIM)
        draw.line((16, 32, width - 16, 32), fill=SURFACE_INSET)

    def _render_selector(self, draw: ImageDraw.ImageDraw) -> None:
        app = self.context.app
        width = app.config.display.width
        height = app.config.display.height
        if self.mode == "error":
            draw_label(draw, 24, 42, "EMU FAULT", app.font_large, DANGER)
            draw_label(draw, 24, 67, self._trim(self.error_message, 32), app.font, WARN)
            draw_label(draw, 24, 88, "ESC/Q RETURNS TO CARTS", app.font, DIM)

        row_top = 54 if self.mode != "error" else 116
        row_height = 29
        visible = self.cartridges[:5]
        if self.selection >= 5:
            start = self.selection - 4
            visible = self.cartridges[start : start + 5]
        else:
            start = 0
        for offset, cart in enumerate(visible):
            index = start + offset
            top = row_top + offset * (row_height + 4)
            selected = index == self.selection
            outline = ACCENT if selected else DIM
            fill = SURFACE_PANEL if selected else BG
            cart_bounds = (22, top, width - 22, top + row_height)
            draw.rounded_rectangle(cart_bounds, radius=4, outline=outline, fill=fill)
            if selected:
                draw.rounded_rectangle((24, top + 2, width - 24, top + row_height - 2), radius=3, outline=DIM, fill=None)
            if selected and self.blink < 0.55:
                # Segmented bar cursor instead of filled rect
                draw_scanlines(draw, (29, top + 9, 38, top + 16), step=3, color=ACCENT)
            draw_label(draw, 44, top + 5, self._trim(cart.title, 24), app.font, FG if selected else DIM)
            draw_label(draw, width - 63, top + 5, f"{index + 1:02}", app.font, outline)

        draw_label(draw, 18, height - 25, "UP/DOWN SELECT", app.font, WARN)
        draw_label(draw, 120, height - 25, "ENTER RUN", app.font, WARN)
        draw_label(draw, width - 64, height - 25, "Y INFO", app.font, WARN)

    def _render_detail(self, draw: ImageDraw.ImageDraw) -> None:
        app = self.context.app
        width = app.config.display.width
        height = app.config.display.height
        cart = self.cartridges[self.selection]
        notes = self._detail_lines(cart)
        visible_count = 9
        max_scroll = max(0, len(notes) - visible_count)
        self.detail_scroll = min(self.detail_scroll, max_scroll)

        detail_bounds = (18, 40, width - 18, height - 34)
        draw_panel(draw, detail_bounds, title="CART", title_font=app.font, outline=ACCENT, title_color=WARN)
        draw.rectangle((24, 48, width - 24, 78), outline=SURFACE_INSET, fill=BG)
        draw_label(draw, 30, 51, self._trim(cart.title, 29), app.font, WARN)
        platform = str((cart.metadata or {}).get("platform", "chip8")).upper()
        draw_label(draw, 30, 65, f"{len(cart.data)} bytes // {platform}", app.font, INFO)
        note_label = "JSON" if cart.metadata else ("TXT" if cart.notes else "NO NOTES")
        draw_label(draw, width - 96, 65, note_label, app.font, WARN if cart.notes else DIM)

        notes_bounds = (24, 82, width - 30, height - 42)
        draw_dot_grid(draw, notes_bounds, step=10, color=SURFACE_GRID)

        y = 89
        for line in notes[self.detail_scroll : self.detail_scroll + visible_count]:
            draw_label(draw, 26, y, line, app.font, FG if line else DIM)
            y += 12
        if max_scroll:
            bar_top = 90
            bar_bottom = height - 47
            thumb_height = max(12, (bar_bottom - bar_top) // max(1, len(notes) - visible_count + 1))
            thumb_top = bar_top + int((bar_bottom - bar_top - thumb_height) * (self.detail_scroll / max_scroll))
            draw.rectangle((width - 27, bar_top, width - 24, bar_bottom), outline=SURFACE_INSET)
            draw.rectangle((width - 27, thumb_top, width - 24, thumb_top + thumb_height), fill=ACCENT)

        draw_label(draw, 18, height - 25, "A/B SCROLL", app.font, WARN)
        draw_label(draw, 106, height - 25, "X RUN", app.font, WARN)
        draw_label(draw, width - 66, height - 25, "Y BACK", app.font, WARN)

    def _render_running(self, draw: ImageDraw.ImageDraw, buffer: Image.Image) -> None:
        app = self.context.app
        width = app.config.display.width
        height = app.config.display.height
        screen_left = 12
        screen_top = 40
        scale = min(256 // self.chip8.width, 128 // self.chip8.height)
        scale = max(1, scale)
        image = self.chip8.render_image(scale=scale, on=ACCENT, off="#030605")
        screen_width, screen_height = image.size
        screen_left = 12 + (256 - screen_width) // 2
        buffer.paste(image, (screen_left, screen_top))
        # Double outline for CRT depth
        inner_bounds = (screen_left - 1, screen_top - 1, screen_left + screen_width, screen_top + screen_height)
        outer_bounds = (screen_left - 3, screen_top - 3, screen_left + screen_width + 2, screen_top + screen_height + 2)
        draw.rectangle(outer_bounds, outline=DIM)
        draw.rectangle(inner_bounds, outline=COOL)
        draw_corner_ticks(draw, outer_bounds, color=COOL, length=6)
        draw_scanlines(draw, (screen_left, screen_top, screen_left + screen_width, screen_top + screen_height), step=8, color="#07120E")
        draw_label(draw, 18, 176, self._trim(self.loaded_title, 18), app.font, WARN)
        draw_label(draw, 18, 193, "HOLD ESC CARTS", app.font, DIM)
        draw_label(draw, 104, 193, "0-9 + A-F DIRECT", app.font, INFO)
        if self.run_notice and time.monotonic() < self.run_notice_until:
            draw_label(draw, 18, 211, self.run_notice, app.font, WARN)
        if self.chip8.sound_timer > 0:
            beep_color = WARN if self.blink < 0.5 else DIM
            draw_label(draw, width - 68, 176, "BEEP", app.font, beep_color)
        draw_label(draw, width - 78, height - 25, "LONG Y CARTS", app.font, DIM)

    def on_button(self, button: str, long_press: bool) -> bool:
        if self.mode == "run":
            if button == "Y" and long_press:
                self.mode = "select"
                self._clear_keys()
                return True
            if button == "Y":
                self._set_run_notice("hold Y for carts")
                return True
            key = self.button_map.get(button)
            if key is not None:
                self._tap_key(key)
                return True
            return False
        if self.mode == "detail":
            if button == "A":
                self._scroll_detail(-3)
                return True
            if button == "B":
                self._scroll_detail(3)
                return True
            if button == "X":
                self._load_selection()
                return True
            if button == "Y":
                self.mode = "select"
                return True
            return False
        if button == "A":
            self._move_selection(-1)
            return True
        if button == "B":
            self._move_selection(1)
            return True
        if button == "X":
            self._load_selection()
            return True
        if button == "Y":
            self._open_detail()
            return True
        return False

    def on_keyboard_event(self, event: KeyboardEvent) -> bool:
        if event.ctrl or event.alt:
            return False
        if self.mode == "run":
            if event.key == "escape":
                self._handle_run_escape(event)
                return True
            key = self._runtime_key_for_event(event)
            if key is not None:
                self._set_runtime_key(key, event.event_type != "release")
                return True
            if event.key in self.navigation_keys:
                return True
            if event.event_type == "press":
                self._set_run_notice("chip8 keys: 0-9 and A-F")
                return True
            return False
        if self.mode == "detail":
            if event.event_type != "press":
                return False
            if event.key in {"escape", "q"}:
                self.mode = "select"
                return True
            if event.key in {"up", "w", "k"}:
                self._scroll_detail(-1)
                return True
            if event.key in {"down", "s", "j"}:
                self._scroll_detail(1)
                return True
            if event.key == "pageup":
                self._scroll_detail(-6)
                return True
            if event.key == "pagedown":
                self._scroll_detail(6)
                return True
            if event.key == "home":
                self.detail_scroll = 0
                return True
            if event.key == "end":
                self.detail_scroll = self._max_detail_scroll()
                return True
            if event.key == "enter" or event.raw_key == "KEY_SPACE":
                self._load_selection()
                return True
            return False
        if event.event_type != "press":
            return False
        if event.key in {"q", "escape"}:
            self.context.app.set_screen("home")
            return True
        if event.key in {"up", "w", "k"}:
            self._move_selection(-1)
            return True
        if event.key in {"down", "s", "j"}:
            self._move_selection(1)
            return True
        if event.key == "r":
            self.refresh_cartridges()
            return True
        if event.key in {"tab", "i"}:
            self._open_detail()
            return True
        if event.key.isdigit() and event.key != "0":
            self.selection = min(len(self.cartridges) - 1, int(event.key) - 1)
            self._load_selection()
            return True
        if event.key == "enter":
            self._load_selection()
            return True
        if event.raw_key == "KEY_SPACE":
            self._open_detail()
            return True
        return False

    def get_button_hints(self) -> list[str]:
        if self.mode == "run":
            return ["A key4", "B key6", "X key5", "Y hold"]
        if self.mode == "detail":
            return ["A up", "B down", "X run", "Y back"]
        return ["A up", "B down", "X run", "Y info"]

    def _load_selection(self) -> None:
        if not self.cartridges:
            return
        cart = self.cartridges[self.selection]
        platform = str((cart.metadata or {}).get("platform", "chip8"))
        self.chip8.reset(platform=platform)
        try:
            self.chip8.load_rom(cart.data)
            for address, payload in cart.preload:
                self.chip8.memory[address : address + len(payload)] = payload
        except Chip8Error as exc:
            self.mode = "error"
            self.error_message = str(exc)
            return
        self.loaded_title = cart.title
        self.mode = "run"
        self.status = cart.source
        self.timer_accumulator = 0.0
        self.error_message = ""
        self.run_notice = ""
        self.run_notice_until = 0.0
        self.escape_armed_at = None
        self._clear_keys()

    def _open_detail(self) -> None:
        if not self.cartridges:
            return
        self.detail_scroll = 0
        self.mode = "detail"

    def _scroll_detail(self, delta: int) -> None:
        self.detail_scroll = min(max(0, self.detail_scroll + delta), self._max_detail_scroll())

    def _max_detail_scroll(self) -> int:
        if not self.cartridges:
            return 0
        return max(0, len(self._detail_lines(self.cartridges[self.selection])) - 9)

    def _detail_lines(self, cart: Cartridge) -> list[str]:
        metadata = cart.metadata or {}
        authors = metadata.get("authors")
        author_text = ", ".join(str(author) for author in authors) if isinstance(authors, list) else ""
        options = metadata.get("options") if isinstance(metadata.get("options"), dict) else {}
        option_text = self._option_summary(options)
        lines = [
            f"SOURCE {self._source_label(cart.source)}",
        ]
        if author_text:
            lines.extend(self._wrap(f"AUTHORS {author_text}", 34))
        if metadata.get("release"):
            lines.append(f"RELEASE {metadata['release']}")
        if metadata.get("event"):
            lines.append(f"EVENT {metadata['event']}")
        if metadata.get("platform"):
            lines.append(f"PLATFORM {str(metadata['platform']).upper()}")
        if option_text:
            lines.extend(self._wrap(f"OPTIONS {option_text}", 34))
        lines.append("")
        body = cart.notes.strip() or "No notes or JSON description were included for this cartridge."
        for raw_line in body.splitlines():
            text = " ".join(raw_line.strip().split())
            if not text:
                lines.append("")
                continue
            lines.extend(self._wrap(text, 34))
        return lines

    def _move_selection(self, delta: int) -> None:
        if not self.cartridges:
            return
        self.selection = (self.selection + delta) % len(self.cartridges)

    def _tap_key(self, key: int) -> None:
        self.chip8.set_key(key, True)
        self.held_keys[key] = time.monotonic() + self.key_hold_seconds

    def _set_runtime_key(self, key: int, pressed: bool) -> None:
        self.chip8.set_key(key, pressed)
        if pressed:
            self.held_keys[key] = time.monotonic() + self.key_hold_seconds
        else:
            self.held_keys.pop(key, None)

    def _runtime_key_for_event(self, event: KeyboardEvent) -> int | None:
        if not self.cartridges:
            return None
        cart = self.cartridges[self.selection]
        source_key = Path(cart.source).stem
        override = self.rom_key_overrides.get(source_key, {})
        if event.text in override:
            return override[event.text]
        if event.raw_key == "KEY_SPACE" and " " in override:
            return override[" "]
        return self.keyboard_map.get(event.key)

    def _handle_run_escape(self, event: KeyboardEvent) -> None:
        if event.event_type == "release":
            if self.escape_armed_at is not None:
                self.escape_armed_at = None
                self._set_run_notice("hold Esc to leave cart")
            return
        if self.escape_armed_at is None:
            self.escape_armed_at = time.monotonic()
            self._set_run_notice("hold Esc to leave cart")

    def _set_run_notice(self, message: str) -> None:
        self.run_notice = message.upper()
        self.run_notice_until = time.monotonic() + 1.2

    def _release_expired_keys(self) -> None:
        now = time.monotonic()
        expired = [key for key, until in self.held_keys.items() if until <= now]
        for key in expired:
            self.chip8.set_key(key, False)
            self.held_keys.pop(key, None)

    def _clear_keys(self) -> None:
        for key in range(16):
            self.chip8.set_key(key, False)
        self.held_keys.clear()
        self.escape_armed_at = None

    @staticmethod
    def _title_for_path(path: Path) -> str:
        title = path.stem if path.suffix else path.name
        return title.replace("_", " ").replace("-", " ").upper()

    @classmethod
    def _metadata_title(cls, path: Path, metadata: dict[str, Any] | None) -> str:
        if metadata is not None and isinstance(metadata.get("title"), str):
            return str(metadata["title"]).upper()
        return cls._title_for_path(path)

    @staticmethod
    def _metadata_notes(path: Path, metadata: dict[str, Any] | None) -> str:
        if metadata is not None and isinstance(metadata.get("desc"), str):
            return str(metadata["desc"]).strip()
        return EmulationScreen._read_notes(path)

    def _load_archive_metadata(self) -> dict[str, dict[str, Any]]:
        metadata: dict[str, dict[str, Any]] = {}
        for programs_path in self.rom_dir.rglob("programs.json"):
            try:
                programs = json.loads(programs_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(programs, dict):
                continue
            authors = self._load_archive_authors(programs_path.with_name("authors.json"))
            for key, value in programs.items():
                if not isinstance(value, dict):
                    continue
                entry = dict(value)
                entry["authors"] = self._author_labels(entry.get("authors"), authors)
                metadata[str(key)] = entry
        return metadata

    @staticmethod
    def _load_archive_authors(path: Path) -> dict[str, dict[str, Any]]:
        try:
            authors = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return authors if isinstance(authors, dict) else {}

    @staticmethod
    def _author_labels(value: object, authors: dict[str, dict[str, Any]]) -> list[str]:
        if not isinstance(value, list):
            return []
        labels: list[str] = []
        for item in value:
            key = str(item)
            info = authors.get(key, {})
            url = info.get("url") if isinstance(info, dict) else None
            labels.append(f"{key} ({url})" if isinstance(url, str) and url else key)
        return labels

    @staticmethod
    def _source_label(source: str) -> str:
        if source == "BUILT-IN":
            return source
        path = Path(source)
        try:
            return str(path.relative_to(EmulationScreen.rom_dir))
        except ValueError:
            return path.name

    @staticmethod
    def _option_summary(options: object) -> str:
        if not isinstance(options, dict):
            return ""
        pieces: list[str] = []
        tickrate = options.get("tickrate")
        if tickrate is not None:
            pieces.append(f"{tickrate}hz")
        enabled_quirks = [
            key.replace("Quirks", "")
            for key, value in options.items()
            if key.endswith("Quirks") and value is True
        ]
        if enabled_quirks:
            pieces.append("quirks " + ",".join(enabled_quirks[:3]))
        if options.get("enableXO"):
            pieces.append("XO")
        return " // ".join(pieces)

    @staticmethod
    def _read_notes(path: Path) -> str:
        notes_path = path.with_suffix(".txt")
        if not notes_path.exists():
            return ""
        try:
            return notes_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return ""

    @staticmethod
    def _wrap(text: str, limit: int) -> list[str]:
        words = text.split()
        if not words:
            return [""]
        lines: list[str] = []
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if len(candidate) <= limit:
                current = candidate
                continue
            lines.append(current)
            current = word[:limit]
        lines.append(current)
        return lines

    @staticmethod
    def _trim(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return f"{text[: max(0, limit - 1)]}>"
