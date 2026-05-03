from __future__ import annotations

from PIL import ImageDraw

from ..colors import ACCENT, FG, WARN
from ..wifi import WifiNetwork
from .base import Screen, ScreenContext
from .widgets import draw_label, draw_progress_bar, draw_separator


class SystemScreen(Screen):
    name = "system"

    def __init__(self, context: ScreenContext) -> None:
        super().__init__(context)
        self.selected_index = 0
        self.networks: list[WifiNetwork] = []
        self.status_line = "A/B browse  X scan  Y connect"
        self._refresh_elapsed = 0.0

    def update(self, dt: float) -> bool:
        self._refresh_elapsed += dt
        if self._refresh_elapsed < 1.0:
            return False
        self._refresh_elapsed = 0.0
        self.networks = self.context.app.wifi.scan(force=False)
        if self.networks:
            self.selected_index = min(self.selected_index, len(self.networks) - 1)
        else:
            self.selected_index = 0
        self.status_line = self.context.app.wifi.last_message
        return True

    def render(self, draw: ImageDraw.ImageDraw, buffer) -> None:
        app = self.context.app
        stats = app.system_snapshot()
        wifi_status = app.wifi.status()
        selected = self.networks[self.selected_index] if self.networks else None
        draw_label(draw, 8, 8, "SYSTEM", app.font_large, FG)
        draw_separator(draw, 28, app.config.display.width)

        draw_label(draw, 8, 40, f"CPU: {int(stats['cpu_pct'] * 100):>3}%", app.font)
        draw_progress_bar(draw, 88, 42, 140, stats["cpu_pct"], ACCENT)
        draw_label(draw, 8, 64, f"MEM: {int(stats['mem_pct'] * 100):>3}%", app.font)
        draw_progress_bar(draw, 88, 66, 140, stats["mem_pct"], ACCENT)
        draw_label(draw, 8, 88, f"TEMP: {stats['temperature_label']}", app.font, WARN if stats["temperature_hot"] else FG)
        draw_progress_bar(draw, 88, 90, 140, stats["temperature_pct"], WARN if stats["temperature_hot"] else ACCENT)
        draw_label(draw, 8, 116, f"DISK: {stats['disk_label']}", app.font)
        draw_label(draw, 8, 140, f"IP:  {stats['ip_address']}", app.font)
        draw_label(draw, 8, 164, f"BT:  {'connected' if app.bluetooth_status.connected else 'disconnected'}", app.font)
        draw_label(draw, 8, 188, f"tmux: {stats['terminal_windows']} windows", app.font)

        draw_label(
            draw,
            8,
            202,
            f"wifi: {wifi_status.ssid or wifi_status.state} {wifi_status.signal}%",
            app.font,
            ACCENT if wifi_status.connected else FG,
        )
        if selected:
            marker = "*" if selected.active else ">"
            security = "open" if selected.open else "lock"
            draw_label(draw, 8, 214, f"{marker} {selected.ssid[:14]} {selected.signal:>3}% {security}", app.font)
        else:
            draw_label(draw, 8, 214, "no wifi networks cached", app.font)
        draw_label(draw, 176, 202, self.status_line[:16], app.font)

    def on_button(self, button: str, long_press: bool) -> bool:
        if button == "X" and long_press:
            self.context.app.set_screen("home")
            return True
        if button == "X":
            self.networks = self.context.app.wifi.scan(force=True)
            self.status_line = self.context.app.wifi.last_message
            self.selected_index = 0
            self._refresh_elapsed = 0.0
            return True
        if button == "A":
            if self.networks:
                self.selected_index = (self.selected_index - 1) % len(self.networks)
                self.status_line = f"selected {self.networks[self.selected_index].ssid}"
            return True
        if button == "B":
            if self.networks:
                self.selected_index = (self.selected_index + 1) % len(self.networks)
                self.status_line = f"selected {self.networks[self.selected_index].ssid}"
            return True
        if button == "Y":
            if long_press:
                self.context.app.set_screen("term")
                return True
            if self.networks:
                _, message = self.context.app.wifi.connect(self.networks[self.selected_index])
                self.status_line = message
            return True
        return False

    def get_button_hints(self) -> list[str]:
        return ["A wifi-", "B wifi+", "X scan", "Y conn"]
