from __future__ import annotations

import argparse
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

from .bluetooth import BluetoothMonitor
from .colors import ACCENT, BG, FG
from .config import AltoidsConfig, load_config
from .display import Display
from .input_buttons import ButtonEvent, ButtonInput
from .input_keyboard import KeyboardEvent, KeyboardInput
from .sleep import SleepManager
from .simulator import SimulatorDisplay
from .terminal import TmuxManager
from .wifi import WifiManager
from .ui import HomeScreen, Screen, ScreenContext, SystemScreen, TerminalScreen
from .ui.widgets import draw_label
from .ui.widgets import draw_button_bar


@dataclass(slots=True)
class TimedValue:
    value: dict[str, object]
    captured_at: float


class AltoidsApp:
    def __init__(self, config: AltoidsConfig, headless: bool = False, simulator: bool = False, simulator_scale: int = 3) -> None:
        self.config = config
        self.simulator = SimulatorDisplay(config.display.width, config.display.height, scale=simulator_scale) if simulator else None
        self.display = Display(
            config.display.width,
            config.display.height,
            config.display.backlight_brightness,
            simulator=self.simulator,
        )
        self.buffer = Image.new("RGB", (config.display.width, config.display.height), BG)
        self.draw = ImageDraw.Draw(self.buffer)
        self.font = self._load_font(config.font_path, config.ui.font_size)
        self.font_large = self._load_font(config.font_path, config.ui.font_size + 4)
        self.headless = headless
        self.tmux = TmuxManager(
            config.terminal.session_name,
            config.terminal.width_chars,
            config.terminal.height_chars,
            config.terminal.pane_history,
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

    def _load_font(self, path: Path, size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
        if path.exists():
            try:
                return ImageFont.load(path)
            except OSError:
                return ImageFont.truetype(str(path), size=size)
        return ImageFont.load_default()

    @property
    def active_screen(self) -> Screen:
        return self.screens[self.active_screen_name]

    def set_screen(self, name: str) -> None:
        if name not in self.screens or self.active_screen_name == name:
            return
        self.active_screen_name = name
        self.needs_redraw = True

    def cycle_screen(self, delta: int = 1) -> None:
        index = self.screen_order.index(self.active_screen_name)
        self.active_screen_name = self.screen_order[(index + delta) % len(self.screen_order)]
        self.needs_redraw = True

    def handle_button_event(self, event: ButtonEvent) -> None:
        if self.sleep_manager.sleeping:
            self.sleep_manager.bump()
            self.display.set_backlight(self.config.display.backlight_brightness)
            self.active_screen.on_wake()
            self.needs_redraw = True
            return
        self.sleep_manager.bump()
        self.needs_redraw |= self.active_screen.on_button(event.button, event.long_press)

    def handle_keyboard_event(self, event: KeyboardEvent) -> None:
        if self.sleep_manager.sleeping:
            self.sleep_manager.bump()
            self.display.set_backlight(self.config.display.backlight_brightness)
            self.active_screen.on_wake()
            self.needs_redraw = True
            return
        self.sleep_manager.bump()
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
        if self.active_screen_name == "term":
            if event.text and not event.ctrl and not event.alt:
                self.tmux.send_text(event.text)
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

    def render(self) -> None:
        self.draw.rectangle((0, 0, self.config.display.width, self.config.display.height), fill=BG)
        self.active_screen.render(self.draw, self.buffer)
        if self.command_mode_active:
            self.draw.rounded_rectangle((256, 8, 312, 28), radius=4, outline=ACCENT, fill=BG)
            draw_label(self.draw, 268, 13, "CMD", self.font, FG)
        draw_button_bar(
            self.draw,
            self.config.display.width,
            self.config.display.height,
            self.active_screen.get_button_hints(),
            self.font,
        )
        self.display.update(self.buffer)
        self.needs_redraw = False

    def run(self, max_frames: int | None = None) -> int:
        frame_count = 0
        last = time.monotonic()
        while True:
            now = time.monotonic()
            dt = now - last
            last = now

            for event in self.button_input.poll():
                self.handle_button_event(event)
            if self.simulator is not None:
                sim_events = self.simulator.poll_events()
                for event in sim_events.button_events:
                    self.handle_button_event(event)
                for event in sim_events.keyboard_events:
                    self.handle_keyboard_event(event)
            for event in self.keyboard_input.poll():
                self.handle_keyboard_event(event)

            self.bluetooth_status = self.bluetooth_monitor.poll()
            if self.command_mode_deadline and not self.command_mode_active:
                self.command_mode_deadline = 0.0
                self.needs_redraw = True
            self.needs_redraw |= self.active_screen.update(dt)
            if self.sleep_manager.update():
                self.display.set_backlight(0.0)

            if not self.sleep_manager.sleeping and self.needs_redraw:
                self.render()
                frame_count += 1

            if max_frames is not None and frame_count >= max_frames:
                return 0

            target_fps = self.config.display.fps_active if not self.sleep_manager.sleeping else self.config.display.fps_idle
            time.sleep(max(0.01, 1.0 / max(1, target_fps)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Altoids cyberdeck UI")
    parser.add_argument("--config", default=None, help="Path to altoids.toml")
    parser.add_argument("--frames", type=int, default=None, help="Render this many frames and exit")
    parser.add_argument("--simulator", action="store_true", help="Run with a desktop simulator window instead of hardware display")
    parser.add_argument("--sim-scale", type=int, default=3, help="Integer pixel scale for the simulator window")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    app = AltoidsApp(config=config, simulator=args.simulator, simulator_scale=args.sim_scale)
    return app.run(max_frames=args.frames)
