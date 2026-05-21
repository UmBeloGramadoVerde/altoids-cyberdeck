from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFont

try:
    import psutil
except ModuleNotFoundError:  # pragma: no cover
    psutil = None

from .accents import AccentManager
from .bluetooth import BluetoothMonitor
from .colors import ACCENT, BG, DIM, FG, SURFACE_ALT
from .config import AltoidsConfig, load_config
from .codex_session import CodexSessionStore
from .display import Display
from .input_buttons import ButtonEvent, ButtonInput
from .input_keyboard import KeyboardEvent, KeyboardInput
from .notes import NoteStore
from .sleep import SleepManager
from .terminal import TmuxManager
from .voice import VoiceManager, VoiceResult
from .wifi import WifiManager
from .webviewer import WebViewer
from .ui import EmulationScreen, HomeScreen, Screen, ScreenContext, SystemScreen, TerminalScreen, TinScopeScreen
from .ui.widgets import draw_label
from .ui.widgets import draw_button_bar


@dataclass(slots=True)
class TimedValue:
    value: dict[str, object]
    captured_at: float


@dataclass(frozen=True, slots=True)
class HelpPage:
    title: str
    rows: list[tuple[str, str]]


class HealthReporter:
    def __init__(self, path: Path, release: str, interval_seconds: float = 1.0) -> None:
        self.path = path
        self.release = release
        self.interval_seconds = interval_seconds
        self.pid = os.getpid()
        self.started_at = time.time()
        self.ready_at: float | None = None
        self._last_write_at = 0.0

    def mark_ready(self) -> None:
        if self.ready_at is None:
            self.ready_at = time.time()
        self._write(force=True)

    def beat(self) -> None:
        self._write(force=False)

    def _write(self, force: bool) -> None:
        now = time.monotonic()
        if not force and now - self._last_write_at < self.interval_seconds:
            return
        self._last_write_at = now
        payload: dict[str, object] = {
            "pid": self.pid,
            "release": self.release,
            "ready": self.ready_at is not None,
            "started_at": self.started_at,
            "heartbeat_at": time.time(),
        }
        if self.ready_at is not None:
            payload["ready_at"] = self.ready_at
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(f".{self.path.name}.{self.pid}.tmp")
        temp_path.write_text(json.dumps(payload, sort_keys=True))
        temp_path.replace(self.path)


@dataclass(frozen=True, slots=True)
class CommandSpec:
    key: str
    action: str
    combo: str
    kind: str
    target: str = ""
    contexts: tuple[str, ...] = ()
    hint: str = ""
    help_page: str = "GLOBAL"


COMMAND_SPECS: tuple[CommandSpec, ...] = (
    CommandSpec("h", "help", "CMD+H", "help", hint="H"),
    CommandSpec("q", "home", "CMD+Q", "screen", target="home", hint="Q"),
    CommandSpec("t", "terminal", "CMD+T", "screen", target="term", hint="T"),
    CommandSpec("s", "system/settings", "CMD+S", "screen", target="system", hint="S"),
    CommandSpec("i", "notes", "CMD+I", "screen", target="notes", hint="I"),
    CommandSpec("g", "games", "CMD+G", "screen", target="emu", hint="G"),
    CommandSpec("r", "tinscope", "CMD+R", "screen", target="tinscope", hint="R"),
    CommandSpec("[", "previous tmux window", "CMD+[", "tmux_previous", contexts=("term",), hint="[", help_page="TMUX"),
    CommandSpec("]", "next tmux window", "CMD+]", "tmux_next", contexts=("term",), hint="]", help_page="TMUX"),
    CommandSpec("n", "new tmux window", "CMD+N", "tmux_new", contexts=("term",), hint="N", help_page="TMUX"),
    CommandSpec("k", "close tmux window", "CMD+K", "tmux_close", contexts=("term",), hint="K", help_page="TMUX"),
)


class AltoidsApp:
    def __init__(self, config: AltoidsConfig, headless: bool = False, web_viewer: bool = False, web_host: str = "127.0.0.1", web_port: int = 8765) -> None:
        self.config = config
        self.display = Display(
            config.display.width,
            config.display.height,
            config.display.backlight_brightness,
            backend=config.display.backend,
            rotation=config.display.rotation,
            driver_path=config.display_driver_path,
            transfer_quantization=config.display.transfer_quantization,
            spi_speed_hz=config.display.spi_speed_hz,
            split_dirty_regions=config.display.split_dirty_regions,
        )
        self.buffer = Image.new("RGB", (config.display.width, config.display.height), BG)
        self.draw = ImageDraw.Draw(self.buffer)
        self.font = self._load_font(config.font_path, config.ui.font_size)
        self.font_large = self._load_font(config.font_path, config.ui.font_size + 4)
        self.terminal_font = self._load_font(
            config.terminal_font_path,
            config.terminal.font_size,
            fallback_paths=self._terminal_font_fallbacks(),
        )
        self.headless = headless
        self.tmux = TmuxManager(
            config.terminal.session_name,
            config.terminal.width_chars,
            config.terminal.height_chars,
            config.terminal.pane_history,
            config.shell_rc_path,
        )
        self.codex_sessions = CodexSessionStore(
            config.codex_home_path,
            scan_limit=config.terminal.codex_scan_limit,
        )
        self.wifi = WifiManager(
            passwords=dict(config.wifi.passwords),
            scan_cache_seconds=config.wifi.scan_cache_seconds,
        )
        self.notes = NoteStore(config.root_dir)
        self.button_input = ButtonInput(self.handle_button_event, display=self.display)
        self.keyboard_input = KeyboardInput()
        self.bluetooth_monitor = BluetoothMonitor()
        self.bluetooth_status = self.bluetooth_monitor.poll()
        self.sleep_manager = SleepManager(config.sleep.idle_seconds)
        self.accents = AccentManager(self.display, config.audio, config.led)
        self.voice = VoiceManager(config.voice, enabled=config.voice.enabled and self.display.is_whisplay)
        self.screen_order = ["home", "notes", "tinscope", "emu", "term", "system"]
        context = ScreenContext(app=self)
        self._screen_context = context
        self.screens: dict[str, Screen] = {
            "home": HomeScreen(context),
        }
        self._screen_factories: dict[str, Callable[[], Screen]] = {
            "tinscope": self._create_tinscope_screen,
            "emu": self._create_emulation_screen,
            "term": self._create_terminal_screen,
            "system": self._create_system_screen,
            "notes": self._create_notes_screen,
        }
        self.active_screen_name = "home"
        self.needs_redraw = True
        self._system_snapshot_cache: TimedValue | None = None
        self.command_mode_deadline = 0.0
        self.help_visible = False
        self.help_page_index = 0
        self.help_page_index_by_context: dict[str, int] = {}
        self.help_scroll_offsets: dict[int, int] = {}
        self.web_viewer = WebViewer(host=web_host, port=web_port) if web_viewer else None
        self._active_fps = max(config.display.fps_active, 20) if self.web_viewer is not None else config.display.fps_active
        self._boot_accent_fired = False
        self._last_input_event_at: float | None = None
        self._last_input_to_render_ms: float | None = None
        self._voice_meta_held = False
        self._voice_trigger_active = False
        self._voice_notice = ""
        self._voice_notice_until = 0.0

    def _load_font(
        self,
        path: Path,
        size: int,
        fallback_paths: list[Path] | None = None,
    ) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
        candidates = [path]
        if fallback_paths:
            candidates.extend(fallback_paths)
        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                return ImageFont.load(candidate)
            except OSError:
                try:
                    return ImageFont.truetype(str(candidate), size=size)
                except OSError:
                    continue
        return ImageFont.load_default()

    @staticmethod
    def _terminal_font_fallbacks() -> list[Path]:
        return [
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"),
            Path("/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf"),
            Path("/usr/share/fonts/truetype/freefont/FreeMono.ttf"),
        ]

    @property
    def active_screen(self) -> Screen:
        return self._screen(self.active_screen_name)

    @property
    def shows_button_bar(self) -> bool:
        return not self.display.is_whisplay

    def set_screen(self, name: str) -> None:
        known_screens = getattr(self, "screen_order", tuple(self.screens.keys()))
        if name not in known_screens or self.active_screen_name == name:
            return
        getattr(self.active_screen, "on_deactivate", lambda: None)()
        self._screen(name)
        self.active_screen_name = name
        self.needs_redraw = True
        self.accents.trigger("screen_change")

    def _screen(self, name: str) -> Screen:
        screen = self.screens.get(name)
        if screen is not None:
            return screen
        factory = self._screen_factories.get(name)
        if factory is None:
            raise KeyError(name)
        screen = factory()
        self.screens[name] = screen
        return screen

    def _create_emulation_screen(self) -> Screen:
        from .ui.emulation import EmulationScreen
        return EmulationScreen(self._screen_context)

    def _create_tinscope_screen(self) -> Screen:
        from .ui.tinscope import TinScopeScreen
        return TinScopeScreen(self._screen_context)

    def _create_terminal_screen(self) -> Screen:
        from .ui.term import TerminalScreen
        return TerminalScreen(self._screen_context)

    def _create_system_screen(self) -> Screen:
        from .ui.system import SystemScreen
        return SystemScreen(self._screen_context)

    def _create_notes_screen(self) -> Screen:
        from .ui.notes import NotesScreen
        return NotesScreen(self._screen_context)

    @staticmethod
    def _create_web_viewer(host: str, port: int) -> WebViewer:
        from .webviewer import WebViewer
        return WebViewer(host=host, port=port)

    def handle_button_event(self, event: ButtonEvent) -> None:
        self._mark_input_event()
        if self.sleep_manager.sleeping:
            self.sleep_manager.bump()
            self.display.exit_standby(self.config.display.backlight_brightness)
            self.accents.exit_standby()
            self.active_screen.on_wake()
            self.needs_redraw = True
            self.accents.trigger("wake")
            return
        self.sleep_manager.bump()
        if self.help_visible:
            self.needs_redraw |= self._handle_help_button(event)
            return
        self.needs_redraw |= self.active_screen.on_button(event.button, event.long_press)

    def handle_keyboard_event(self, event: KeyboardEvent) -> None:
        self._mark_input_event()
        if self._handle_voice_key(event):
            self.needs_redraw = True
            return
        if event.event_type != "press" and not self._active_screen_accepts_key_release():
            return
        if self.sleep_manager.sleeping:
            self.sleep_manager.bump()
            self.display.exit_standby(self.config.display.backlight_brightness)
            self.accents.exit_standby()
            self.active_screen.on_wake()
            self.needs_redraw = True
            self.accents.trigger("wake")
            return
        self.sleep_manager.bump()
        if self.help_visible:
            if self._handle_help_key(event):
                self.needs_redraw = True
            return
        if self._is_help_shortcut(event):
            self.toggle_help()
            self.needs_redraw = True
            return
        if event.key == "meta":
            self.command_mode_deadline = time.monotonic() + 1.5
            self.needs_redraw = True
            return
        if self.command_mode_active and self._handle_command_mode_key(event):
            self.needs_redraw = True
            return
        if self.command_mode_active:
            self.command_mode_deadline = 0.0
            self.needs_redraw = True
        if self.active_screen.on_keyboard_event(event):
            self.needs_redraw = True
            return
        if self.active_screen_name == "term":
            if event.text and not event.ctrl and not event.alt:
                self.tmux.send_text(event.text)
                self.needs_redraw = True
                return
            if event.ctrl and not event.alt:
                tmux_key = self._tmux_ctrl_key(event)
                if tmux_key is not None:
                    self.tmux.send_keys([tmux_key])
                    self.needs_redraw = True
                    return
            if event.key == "enter":
                self.tmux.send_enter()
                self.needs_redraw = True
                return
            if event.key in {"backspace", "tab", "escape", "up", "down", "left", "right", "home", "end", "pageup", "pagedown", "delete", "insert"}:
                self.tmux.send_keys([self._tmux_key_name(event.key)])
                self.needs_redraw = True

    def _handle_voice_key(self, event: KeyboardEvent) -> bool:
        if self.config.voice.trigger != "meta+space":
            return False
        if event.key == "meta":
            self._voice_meta_held = event.event_type == "press"
            if event.event_type == "release" and self._voice_trigger_active:
                self._stop_voice_recording()
                return True
            return False
        if event.key != " " or not self._voice_meta_held:
            return False
        if event.event_type == "press":
            if not self._voice_trigger_active:
                result = self.voice.start()
                self._set_voice_notice(result.message)
                self._voice_trigger_active = result.ok and result.message == "recording"
            self.command_mode_deadline = 0.0
            return True
        if event.event_type == "release" and self._voice_trigger_active:
            self._stop_voice_recording()
            return True
        return True

    def _stop_voice_recording(self) -> None:
        result = self.voice.stop()
        self._voice_trigger_active = False
        self._set_voice_notice(result.message)

    def _handle_voice_result(self, result: VoiceResult) -> None:
        if not result.ok:
            self._set_voice_notice(result.message)
            return
        if result.text and self._insert_voice_text(result.text):
            self._set_voice_notice("voice inserted")
            return
        self._set_voice_notice("no focused text target")

    def _insert_voice_text(self, text: str) -> bool:
        if self.active_screen_name == "term":
            self.tmux.paste_text(text)
            return True
        inserter = getattr(self.active_screen, "insert_voice_text", None)
        if callable(inserter):
            return bool(inserter(text))
        return False

    def _set_voice_notice(self, message: str) -> None:
        self._voice_notice = message
        self._voice_notice_until = time.monotonic() + 2.0
        self.needs_redraw = True

    @property
    def command_mode_active(self) -> bool:
        return time.monotonic() < self.command_mode_deadline

    def _command_mode_hints(self) -> list[str]:
        hints = [spec.hint or spec.key.upper() for spec in self._available_command_specs()]
        if self.active_screen_name == "term":
            hints.append("0-9")
        return hints

    def _active_screen_accepts_key_release(self) -> bool:
        if self.active_screen_name != "emu":
            return False
        return getattr(self.active_screen, "accepts_key_release", lambda: False)()

    def _handle_command_mode_key(self, event: KeyboardEvent) -> bool:
        self.command_mode_deadline = 0.0
        if self.active_screen_name == "term" and event.key.isdigit():
            target_window = 10 if event.key == "0" else int(event.key)
            self.tmux.select_window(target_window)
            return True
        spec = self._command_spec_for_key(event.key)
        if spec is None:
            return False
        if spec.kind == "help":
            self.toggle_help()
            return True
        if spec.kind == "screen":
            self.set_screen(spec.target)
            return True
        if spec.kind == "tmux_previous":
            self.tmux.select_previous_window()
            return True
        if spec.kind == "tmux_next":
            self.tmux.select_next_window()
            return True
        if spec.kind == "tmux_new":
            self.tmux.create_window()
            return True
        if spec.kind == "tmux_close":
            self.tmux.close_active_window()
            return True
        return False

    def _available_command_specs(self) -> list[CommandSpec]:
        return [
            spec for spec in COMMAND_SPECS
            if not spec.contexts or self.active_screen_name in spec.contexts
        ]

    def _command_spec_for_key(self, key: str) -> CommandSpec | None:
        for spec in self._available_command_specs():
            if spec.key == key:
                return spec
        return None

    @staticmethod
    def _tmux_key_name(key: str) -> str:
        return {
            "backspace": "BSpace",
            "tab": "Tab",
            "escape": "Escape",
            "up": "Up",
            "down": "Down",
            "left": "Left",
            "right": "Right",
            "home": "Home",
            "end": "End",
            "pageup": "PageUp",
            "pagedown": "PageDown",
            "delete": "DC",
            "insert": "IC",
        }[key]

    @staticmethod
    def _tmux_ctrl_key(event: KeyboardEvent) -> str | None:
        if len(event.key) == 1 and event.key.isalpha():
            return f"C-{event.key.lower()}"
        if event.raw_key == "KEY_SPACE":
            return "C-Space"
        if event.key == "[":
            return "C-["
        if event.key == "]":
            return "C-]"
        if event.key == "\\":
            return "C-\\"
        if event.key == "/":
            return "C-/"
        return None

    def system_snapshot(self) -> dict[str, object]:
        now = time.monotonic()
        if self._system_snapshot_cache is not None and now - self._system_snapshot_cache.captured_at < 1.0:
            return self._system_snapshot_cache.value
        if self._last_input_event_at is not None and self._system_snapshot_cache is not None:
            return self._system_snapshot_cache.value
        cpu_pct = psutil.cpu_percent() / 100.0 if psutil else 0.0
        mem_pct = psutil.virtual_memory().percent / 100.0 if psutil else 0.0
        disk = shutil.disk_usage("/")
        uptime_seconds = int(time.time() - psutil.boot_time()) if psutil else 0
        temperature_c = self._temperature_c()
        snapshot = {
            "cpu_pct": cpu_pct,
            "mem_pct": mem_pct,
            "temperature_c": temperature_c,
            "temperature_pct": min(max(temperature_c / 100.0, 0.0), 1.0),
            "temperature_label": f"{temperature_c:.0f}C",
            "temperature_hot": temperature_c >= self.config.system.temperature_warn_c,
            "disk_label": f"{self._fmt_gb(disk.used)} / {self._fmt_gb(disk.total)}",
            "uptime": self._fmt_uptime(uptime_seconds),
            "ip_address": self._ip_address(),
            "terminal_windows": len(self.tmux.list_windows()),
        }
        self._system_snapshot_cache = TimedValue(snapshot, now)
        return snapshot

    def _temperature_c(self) -> float:
        if not psutil:
            return 0.0
        temps = psutil.sensors_temperatures(fahrenheit=False)
        for key in ("cpu_thermal", "coretemp"):
            entries = temps.get(key)
            if entries:
                return float(entries[0].current)
        return 0.0

    def _ip_address(self) -> str:
        if psutil:
            preferred: list[str] = []
            fallback: list[str] = []
            try:
                interfaces = psutil.net_if_addrs()
            except OSError:
                interfaces = {}
            for name, entries in interfaces.items():
                for entry in entries:
                    if entry.family != socket.AF_INET:
                        continue
                    address = entry.address
                    if not address or address.startswith("127."):
                        continue
                    if name.startswith(("wlan", "wifi", "wl", "eth", "en")):
                        preferred.append(address)
                    else:
                        fallback.append(address)
            if preferred:
                return preferred[0]
            if fallback:
                return fallback[0]
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))
                return sock.getsockname()[0]
        except OSError:
            return "offline"

    @staticmethod
    def _fmt_gb(value: int) -> str:
        return f"{value / (1024 ** 3):.1f}G"

    @staticmethod
    def _fmt_uptime(total_seconds: int) -> str:
        days, rem = divmod(total_seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, _ = divmod(rem, 60)
        if days:
            return f"{days}d {hours}h"
        if hours:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"

    @property
    def help_pages(self) -> list[HelpPage]:
        return [
            HelpPage(
                title="GLOBAL",
                rows=[
                    ("toggle help", "F1 / Ctrl+H / Ctrl+/"),
                    ("jump to help page", "Help: 1-6"),
                    ("prev/next help page", "Help: Left/Right"),
                    ("scroll help page", "Help: Up/Down"),
                    ("voice dictation", "Hold CMD+Space"),
                    *self._command_help_rows("GLOBAL"),
                ],
            ),
            HelpPage(
                title="TMUX",
                rows=[
                    ("jump tmux window", "CMD+1-0"),
                    *self._command_help_rows("TMUX"),
                    ("scroll terminal", "Ctrl+Up/Down"),
                    ("scrollback", "Up/Down"),
                    ("top/live", "Ctrl+Home/End"),
                    ("send text in terminal", "Text keys"),
                    ("send ctrl chord", "Ctrl+A-Z"),
                ],
            ),
            HelpPage(
                title="EMU",
                rows=[
                    *self._command_help_rows("EMU"),
                    ("select cart", "Up/Down"),
                    ("run cart", "Enter / X"),
                    ("cart details", "Space / Tab / Y"),
                    ("scroll details", "Details: A/B"),
                    ("details back", "Details: Q/Esc/Y"),
                    ("refresh rom list", "R"),
                    ("chip8 keys", "Run: 0-9 and A-F"),
                    ("button controls", "Run: A/B/X"),
                    ("return to carts", "Run: hold Esc / long Y"),
                    ("home", "Picker: Q / Esc / Y"),
                ],
            ),
            HelpPage(
                title="TINSCOPE",
                rows=[
                    *self._command_help_rows("TINSCOPE"),
                    ("start mission", "Enter / X"),
                    ("approve request", "Enter / X"),
                    ("deny request", "Esc"),
                    ("inspect item", "Enter / Tab"),
                    ("context", "Space / Y"),
                    ("select item", "Up/Down"),
                    ("switch page", "Left/Right"),
                    ("run action", "Actions: Enter"),
                    ("overlay scroll", "Up/Down"),
                    ("overlay item", "Left/Right"),
                    ("close overlay", "Enter/Esc"),
                    ("home", "Esc / Q"),
                ],
            ),
            HelpPage(
                title="NOTES",
                rows=[
                    *self._command_help_rows("NOTES"),
                    ("type into draft", "Text keys"),
                    ("save draft", "Enter / X"),
                    ("voice quick save", "Hold CMD+Space"),
                    ("edit draft", "Backspace"),
                    ("clear draft", "Ctrl+L / Y"),
                    ("select recent", "Up/Down / A/B"),
                    ("home", "Esc / long Y"),
                ],
            ),
            HelpPage(
                title="SYSTEM",
                rows=[
                    ("expand core panel", "C"),
                    ("expand load panel", "O"),
                    ("expand link panel", "L"),
                    ("expand wireless", "W"),
                    ("expand rig panel", "R"),
                    ("expand cues panel", "U"),
                    ("back to overview", "Detail: Esc"),
                    ("wifi pick network", "Wireless: Up/Down"),
                    ("wifi scan", "Wireless: R"),
                    ("wifi join", "Wireless: Enter"),
                    ("wifi password", "Pass: text keys"),
                    ("password join", "Pass: Enter"),
                    ("password cancel", "Pass: Esc"),
                    ("volume +/-", "Cues: Up/Down +/-"),
                    ("toggle mute", "Cues: M"),
                    ("toggle led", "Cues: L"),
                ],
            ),
        ]

    def _command_help_rows(self, page_title: str) -> list[tuple[str, str]]:
        if page_title == "EMU":
            return [(spec.action, spec.combo) for spec in COMMAND_SPECS if spec.target == "emu"]
        if page_title == "TINSCOPE":
            return [(spec.action, spec.combo) for spec in COMMAND_SPECS if spec.target == "tinscope"]
        if page_title == "NOTES":
            return [(spec.action, spec.combo) for spec in COMMAND_SPECS if spec.target == "notes"]
        return [
            (spec.action, spec.combo)
            for spec in COMMAND_SPECS
            if spec.help_page == page_title
        ]

    def toggle_help(self) -> None:
        self.help_visible = not self.help_visible
        if self.help_visible:
            self.command_mode_deadline = 0.0
            self.help_page_index = self._help_page_index_for_current_context()

    def _handle_help_button(self, event: ButtonEvent) -> bool:
        if event.long_press:
            self.toggle_help()
            return True
        if event.button == "A":
            self._set_help_page_index((self.help_page_index - 1) % len(self.help_pages))
            return True
        if event.button == "B":
            self._set_help_page_index((self.help_page_index + 1) % len(self.help_pages))
            return True
        if event.button in {"X", "Y"}:
            self.toggle_help()
            return True
        return False

    def _handle_help_key(self, event: KeyboardEvent) -> bool:
        if event.key in {"escape", "enter"} or self._is_help_shortcut(event):
            self.toggle_help()
            return True
        if self._select_help_page_by_key(event.key):
            return True
        if event.key == "left":
            self._set_help_page_index((self.help_page_index - 1) % len(self.help_pages))
            return True
        if event.key in {"right", "tab"} or event.raw_key == "KEY_SPACE":
            self._set_help_page_index((self.help_page_index + 1) % len(self.help_pages))
            return True
        if event.key == "up":
            self._scroll_help(-1)
            return True
        if event.key == "down":
            self._scroll_help(1)
            return True
        if event.key == "pageup":
            self._scroll_help(-5)
            return True
        if event.key == "pagedown":
            self._scroll_help(5)
            return True
        if event.key == "home":
            self._set_help_scroll(0)
            return True
        if event.key == "end":
            self._set_help_scroll(self._max_help_scroll())
            return True
        if event.key == "h" and event.ctrl is False and event.alt is False:
            self.toggle_help()
            return True
        return False

    def _select_help_page_by_key(self, key: str) -> bool:
        if not key.isdigit():
            return False
        page_number = int(key)
        if page_number == 0 or page_number > len(self.help_pages):
            return False
        self._set_help_page_index(page_number - 1)
        return True

    def _set_help_page_index(self, index: int) -> None:
        self.help_page_index = index
        self.help_page_index_by_context[self._help_context_key()] = index

    def _help_page_index_for_current_context(self) -> int:
        context_key = self._help_context_key()
        if context_key in self.help_page_index_by_context:
            return self.help_page_index_by_context[context_key]
        return self._contextual_help_page_index()

    def _help_context_key(self) -> str:
        return self.active_screen_name

    def _contextual_help_page_index(self) -> int:
        page_title = {
            "home": "GLOBAL",
            "term": "TMUX",
            "notes": "NOTES",
            "emu": "EMU",
            "tinscope": "TINSCOPE",
            "system": "SYSTEM",
        }.get(self._help_context_key(), "GLOBAL")
        for index, page in enumerate(self.help_pages):
            if page.title == page_title:
                return index
        return 0

    def _scroll_help(self, delta: int) -> None:
        self._set_help_scroll(self._current_help_scroll() + delta)

    def _set_help_scroll(self, offset: int) -> None:
        self.help_scroll_offsets[self.help_page_index] = min(max(0, offset), self._max_help_scroll())

    def _current_help_scroll(self) -> int:
        return self.help_scroll_offsets.get(self.help_page_index, 0)

    def _max_help_scroll(self) -> int:
        page = self.help_pages[self.help_page_index]
        return max(0, len(page.rows) - self._help_visible_row_count())

    def _help_visible_row_count(self) -> int:
        height = self.config.display.height
        top = 12
        bottom = height - 30
        first_row_y = top + 36
        return max(1, (bottom - first_row_y - 8) // 20)

    @staticmethod
    def _is_help_shortcut(event: KeyboardEvent) -> bool:
        if event.key == "f1":
            return True
        if event.ctrl and not event.alt and event.key == "h":
            return True
        if event.ctrl and not event.alt and event.key in {"/", "?"}:
            return True
        return False

    def _render_help_overlay(self) -> None:
        width = self.config.display.width
        height = self.config.display.height
        top = 12
        left = 10
        right = width - 10
        bottom = height - 30
        self.draw.rounded_rectangle((left, top, right, bottom), radius=8, outline=ACCENT, fill=SURFACE_ALT)
        page = self.help_pages[self.help_page_index]
        self._set_help_scroll(self._current_help_scroll())
        scroll = self._current_help_scroll()
        visible_rows = self._help_visible_row_count()
        draw_label(self.draw, left + 10, top + 8, f"HELP {self.help_page_index + 1}/{len(self.help_pages)}", self.font, ACCENT)
        scroll_label = f"{page.title} {scroll + 1}-{min(len(page.rows), scroll + visible_rows)}/{len(page.rows)}"
        draw_label(self.draw, right - 112, top + 8, scroll_label, self.font, FG)
        self.draw.line((left + 8, top + 28, right - 8, top + 28), fill=DIM, width=1)
        row_y = top + 36
        for action, combo in page.rows[scroll : scroll + visible_rows]:
            draw_label(self.draw, left + 10, row_y, action, self.font, FG)
            draw_label(self.draw, left + 188, row_y, combo, self.font, DIM)
            row_y += 20

    def _render_voice_overlay(self) -> None:
        now = time.monotonic()
        status = self.voice.status
        if status in {"recording", "transcribing"}:
            label = f"VOICE {status.upper()}"
        elif self._voice_notice and now < self._voice_notice_until:
            label = f"VOICE {self._voice_notice.upper()}"
        else:
            return
        width = self.config.display.width
        x0 = 10
        y0 = 10 if not self.command_mode_active else 34
        x1 = min(width - 10, x0 + 10 + len(label) * 7)
        self.draw.rounded_rectangle((x0, y0, x1, y0 + 20), radius=4, outline=ACCENT, fill=BG)
        draw_label(self.draw, x0 + 5, y0 + 5, label, self.font, FG)

    def render(self) -> None:
        self.draw.rectangle((0, 0, self.config.display.width, self.config.display.height), fill=BG)
        self.active_screen.render(self.draw, self.buffer)
        if self.command_mode_active:
            hints = self._command_mode_hints()
            hint_text = " ".join(hints)
            hint_width = min(len(hint_text) * 7 + 10, 220)
            self.draw.rounded_rectangle((6, 6, hint_width, 26), radius=4, outline=DIM, fill=BG)
            draw_label(self.draw, 12, 11, hint_text, self.font, DIM)
            cmd_left = hint_width + 4
            self.draw.rounded_rectangle((cmd_left, 6, cmd_left + 48, 26), radius=4, outline=ACCENT, fill=BG)
            draw_label(self.draw, cmd_left + 8, 11, "CMD", self.font, FG)
        if self.help_visible:
            self._render_help_overlay()
        self._render_voice_overlay()
        if self.shows_button_bar:
            draw_button_bar(
                self.draw,
                self.config.display.width,
                self.config.display.height,
                ["A prev", "B next", "X close", "Y close"] if self.help_visible else self.active_screen.get_button_hints(),
                self.font,
            )
        self.display.update(self.buffer)
        if self.web_viewer is not None:
            self.web_viewer.update(self.buffer)
        if self._last_input_event_at is not None:
            self._last_input_to_render_ms = (time.monotonic() - self._last_input_event_at) * 1000.0
            self._last_input_event_at = None
        self.needs_redraw = False

    def _mark_input_event(self) -> None:
        if self._last_input_event_at is None:
            self._last_input_event_at = time.monotonic()

    @property
    def input_render_pending(self) -> bool:
        return self._last_input_event_at is not None

    def run(self, max_frames: int | None = None, health_reporter: HealthReporter | None = None) -> int:
        frame_count = 0
        last = time.monotonic()
        try:
            while True:
                now = time.monotonic()
                dt = now - last
                last = now

                for event in self.button_input.poll():
                    self.handle_button_event(event)
                for event in self.keyboard_input.poll():
                    self.handle_keyboard_event(event)
                if self.web_viewer is not None:
                    web_events = self.web_viewer.poll_events()
                    for event in web_events.button_events:
                        self.handle_button_event(event)
                    for event in web_events.keyboard_events:
                        self.handle_keyboard_event(event)
                for result in self.voice.update():
                    self._handle_voice_result(result)

                if not self.input_render_pending:
                    self.bluetooth_status = self.bluetooth_monitor.poll()
                    if self.display.backend_name == "displayhatmini":
                        flip = not self.bluetooth_status.connected
                        if flip != self.display._flip_180:
                            self.display.set_flip_180(flip)
                            self.needs_redraw = True
                if self.command_mode_deadline and not self.command_mode_active:
                    self.command_mode_deadline = 0.0
                    self.needs_redraw = True
                if self._voice_notice and time.monotonic() >= self._voice_notice_until:
                    self._voice_notice = ""
                    self.needs_redraw = True
                self.needs_redraw |= self.active_screen.update(dt)
                if self.sleep_manager.update():
                    self.accents.enter_standby()
                    self.display.enter_standby()

                if not self.sleep_manager.sleeping and self.needs_redraw:
                    self.render()
                    frame_count += 1
                    if health_reporter is not None and frame_count == 1:
                        health_reporter.mark_ready()
                    if not self._boot_accent_fired:
                        self.accents.trigger("boot_complete")
                        self._boot_accent_fired = True

                if max_frames is not None and frame_count >= max_frames:
                    return 0

                if health_reporter is not None:
                    health_reporter.beat()
                target_fps = self._active_fps if not self.sleep_manager.sleeping else self.config.display.fps_idle
                frame_interval = 1.0 / max(1, target_fps)
                input_poll_interval = max(0.001, self.config.display.input_poll_interval)
                time.sleep(min(frame_interval, input_poll_interval))
        finally:
            self.close()

    def close(self) -> None:
        self.voice.shutdown()
        self.accents.shutdown()
        self.tmux.shutdown()
        self.display.shutdown()
        if self.web_viewer is not None:
            self.web_viewer.shutdown()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Altoids cyberdeck UI")
    parser.add_argument("--config", default=None, help="Path to altoids.toml")
    parser.add_argument("--frames", type=int, default=None, help="Render this many frames and exit")
    parser.add_argument("--self-test", action="store_true", help="Initialize the app, render one frame, and exit")
    parser.add_argument("--health-file", default=None, help="Path to a JSON health/heartbeat file for supervisor use")
    parser.add_argument("--web-viewer", action="store_true", help="Serve the UI over HTTP for browser-based viewing and control")
    parser.add_argument("--web-host", default="127.0.0.1", help="Host/interface for the web viewer")
    parser.add_argument("--web-port", type=int, default=8765, help="Port for the web viewer")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    if args.self_test:
        # Self-tests may run while the active release still owns the display GPIO lines.
        # Force the mock backend so staged validation doesn't contend with the live app.
        config.display.backend = "mock"
    app = AltoidsApp(config=config, web_viewer=args.web_viewer, web_host=args.web_host, web_port=args.web_port)
    health_reporter = HealthReporter(
        Path(args.health_file),
        os.environ.get("ALTOIDS_RELEASE_ID", "dev"),
    ) if args.health_file else None
    if app.web_viewer is not None:
        print(f"Web viewer available at {app.web_viewer.base_url}")
    max_frames = 1 if args.self_test and args.frames is None else args.frames
    return app.run(max_frames=max_frames, health_reporter=health_reporter)
