from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

try:
    import psutil
except ModuleNotFoundError:  # pragma: no cover
    psutil = None

from .accents import AccentManager
from .bluetooth import BluetoothMonitor
from .colors import ACCENT, BG, DIM, FG
from .config import AltoidsConfig, load_config
from .display import Display
from .input_buttons import ButtonEvent, ButtonInput
from .input_keyboard import KeyboardEvent, KeyboardInput
from .sleep import SleepManager
from .terminal import TmuxManager
from .wifi import WifiManager
from .webviewer import WebViewer
from .ui import HomeScreen, Screen, ScreenContext, SystemScreen, TerminalScreen
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
        self.wifi = WifiManager(
            passwords=dict(config.wifi.passwords),
            scan_cache_seconds=config.wifi.scan_cache_seconds,
        )
        self.button_input = ButtonInput(self.handle_button_event)
        self.keyboard_input = KeyboardInput()
        self.bluetooth_monitor = BluetoothMonitor()
        self.bluetooth_status = self.bluetooth_monitor.poll()
        self.sleep_manager = SleepManager(config.sleep.idle_seconds)
        self.accents = AccentManager(self.display, config.audio, config.led)
        self.screen_order = ["home", "term", "system"]
        context = ScreenContext(app=self)
        self.screens: dict[str, Screen] = {
            "home": HomeScreen(context),
            "term": TerminalScreen(context),
            "system": SystemScreen(context),
        }
        self.active_screen_name = "home"
        self.needs_redraw = True
        self._system_snapshot_cache: TimedValue | None = None
        self.command_mode_deadline = 0.0
        self.help_visible = False
        self.help_page_index = 0
        self.web_viewer = WebViewer(host=web_host, port=web_port) if web_viewer else None
        self._active_fps = max(config.display.fps_active, 20) if self.web_viewer is not None else config.display.fps_active
        self._boot_accent_fired = False

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
        return self.screens[self.active_screen_name]

    def set_screen(self, name: str) -> None:
        if name not in self.screens or self.active_screen_name == name:
            return
        self.active_screen_name = name
        self.needs_redraw = True
        self.accents.trigger("screen_change")

    def cycle_screen(self, delta: int = 1) -> None:
        index = self.screen_order.index(self.active_screen_name)
        self.active_screen_name = self.screen_order[(index + delta) % len(self.screen_order)]
        self.needs_redraw = True

    def handle_button_event(self, event: ButtonEvent) -> None:
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

    @property
    def command_mode_active(self) -> bool:
        return time.monotonic() < self.command_mode_deadline

    def _handle_command_mode_key(self, event: KeyboardEvent) -> bool:
        self.command_mode_deadline = 0.0
        if event.key == "h":
            self.toggle_help()
            return True
        if event.key.isdigit():
            target_window = 10 if event.key == "0" else int(event.key)
            self.tmux.select_window(target_window)
            return True
        if event.key == "q":
            self.set_screen("home")
            return True
        if event.key == "w":
            self.set_screen("term")
            return True
        if event.key == "e":
            self.set_screen("system")
            return True
        if event.key == "a":
            self.tmux.select_previous_window()
            return True
        if event.key == "s":
            self.tmux.select_next_window()
            return True
        if event.key == "d":
            self.tmux.create_window()
            return True
        if event.key == "f":
            self.tmux.close_active_window()
            return True
        if self.active_screen_name == "system":
            system_screen = self.screens["system"]
            if event.key == "j":
                return system_screen.on_button("A", False)
            if event.key == "k":
                return system_screen.on_button("B", False)
            if event.key == "r":
                return system_screen.on_button("X", False)
            if event.key == "c":
                return system_screen.on_button("Y", False)
        if event.key == "z":
            self.cycle_screen(-1)
            return True
        if event.key == "x":
            self.cycle_screen(1)
            return True
        return False

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
                    ("F1", "toggle help"),
                    ("Ctrl+H", "toggle help"),
                    ("Ctrl+/", "toggle help"),
                    ("Meta then H", "help"),
                    ("Meta then Q", "home"),
                    ("Meta then W", "terminal"),
                    ("Meta then E", "system"),
                    ("Meta then Z/X", "prev/next screen"),
                ],
            ),
            HelpPage(
                title="TMUX",
                rows=[
                    ("Meta then 1-0", "jump tmux window"),
                    ("Meta then A/S", "prev/next window"),
                    ("Meta then D", "new window"),
                    ("Meta then F", "close window"),
                    ("Ctrl+Up/Down", "scroll term"),
                    ("Ctrl+PgUp/Dn", "page scroll"),
                    ("Ctrl+Home/End", "top/live"),
                    ("Text keys", "send text in terminal"),
                    ("Ctrl+A-Z", "send ctrl chord"),
                ],
            ),
            HelpPage(
                title="SYSTEM",
                rows=[
                    ("Long A/B", "prev/next subpage"),
                    ("Meta then J/K", "wifi prev/next"),
                    ("Meta then R", "scan wifi"),
                    ("Meta then C", "connect wifi"),
                    ("Pass: text", "type password"),
                    ("Pass: Enter", "join"),
                    ("Pass: Backspace", "delete"),
                    ("Pass: Esc", "cancel"),
                    ("Help: A/B", "prev/next page"),
                    ("Help: Esc", "close help"),
                ],
            ),
            HelpPage(
                title="ACCENTS",
                rows=[
                    ("A / B", "volume - / +"),
                    ("X", "toggle mute"),
                    ("Y", "toggle led pulses"),
                    ("Whisplay only", "features gated"),
                    ("Sleep", "audio/led off"),
                    ("Wake", "restore + cue"),
                ],
            ),
        ]

    def toggle_help(self) -> None:
        self.help_visible = not self.help_visible
        if self.help_visible:
            self.command_mode_deadline = 0.0

    def _handle_help_button(self, event: ButtonEvent) -> bool:
        if event.long_press:
            self.toggle_help()
            return True
        if event.button == "A":
            self.help_page_index = (self.help_page_index - 1) % len(self.help_pages)
            return True
        if event.button == "B":
            self.help_page_index = (self.help_page_index + 1) % len(self.help_pages)
            return True
        if event.button in {"X", "Y"}:
            self.toggle_help()
            return True
        return False

    def _handle_help_key(self, event: KeyboardEvent) -> bool:
        if event.key in {"escape", "enter"} or self._is_help_shortcut(event):
            self.toggle_help()
            return True
        if event.key in {"left", "up"}:
            self.help_page_index = (self.help_page_index - 1) % len(self.help_pages)
            return True
        if event.key in {"right", "down", "tab"} or event.raw_key == "KEY_SPACE":
            self.help_page_index = (self.help_page_index + 1) % len(self.help_pages)
            return True
        if event.key == "h" and event.ctrl is False and event.alt is False:
            self.toggle_help()
            return True
        return False

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
        self.draw.rounded_rectangle((left, top, right, bottom), radius=8, outline=ACCENT, fill="#08100D")
        page = self.help_pages[self.help_page_index]
        draw_label(self.draw, left + 10, top + 8, f"HELP {self.help_page_index + 1}/{len(self.help_pages)}", self.font, ACCENT)
        draw_label(self.draw, right - 68, top + 8, page.title, self.font, FG)
        self.draw.line((left + 8, top + 28, right - 8, top + 28), fill=DIM, width=1)
        row_y = top + 36
        for shortcut, description in page.rows:
            draw_label(self.draw, left + 10, row_y, shortcut, self.font, FG)
            draw_label(self.draw, left + 122, row_y, description, self.font, DIM)
            row_y += 20

    def render(self) -> None:
        self.draw.rectangle((0, 0, self.config.display.width, self.config.display.height), fill=BG)
        self.active_screen.render(self.draw, self.buffer)
        if self.command_mode_active:
            self.draw.rounded_rectangle((256, 8, 312, 28), radius=4, outline=ACCENT, fill=BG)
            draw_label(self.draw, 268, 13, "CMD", self.font, FG)
        if self.help_visible:
            self._render_help_overlay()
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
        self.needs_redraw = False

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

                self.bluetooth_status = self.bluetooth_monitor.poll()
                if self.command_mode_deadline and not self.command_mode_active:
                    self.command_mode_deadline = 0.0
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
                time.sleep(max(0.01, 1.0 / max(1, target_fps)))
        finally:
            self.close()

    def close(self) -> None:
        self.accents.shutdown()
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
    app = AltoidsApp(config=config, web_viewer=args.web_viewer, web_host=args.web_host, web_port=args.web_port)
    health_reporter = HealthReporter(
        Path(args.health_file),
        os.environ.get("ALTOIDS_RELEASE_ID", "dev"),
    ) if args.health_file else None
    if app.web_viewer is not None:
        print(f"Web viewer available at {app.web_viewer.base_url}")
    max_frames = 1 if args.self_test and args.frames is None else args.frames
    return app.run(max_frames=max_frames, health_reporter=health_reporter)
