from __future__ import annotations

from PIL import ImageDraw

from ..colors import ACCENT, FG, WARN
from ..input_keyboard import KeyboardEvent
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
        self.password_entry = ""
        self.password_target: WifiNetwork | None = None
        self._refresh_elapsed = 0.0

    @property
    def entering_password(self) -> bool:
        return self.password_target is not None

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
        if self.entering_password and self.password_target is not None:
            masked = "*" * min(len(self.password_entry), 12)
            draw_label(draw, 8, 226, f"pass {self.password_target.ssid[:10]} {masked}", app.font, WARN)
        draw_label(draw, 176, 202, self.status_line[:16], app.font)

    def on_button(self, button: str, long_press: bool) -> bool:
        if self.entering_password:
            if button == "A" and self.password_entry:
                self.password_entry = self.password_entry[:-1]
                self.status_line = "deleted"
                return True
            if button == "B":
                self.password_entry += " "
                self.status_line = "space"
                return True
            if button == "X":
                self._cancel_password_entry()
                return True
            if button == "Y":
                self._submit_password_entry()
                return True
            return False
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
                self._connect_selected_network()
            return True
        return False

    def on_keyboard_event(self, event: KeyboardEvent) -> bool:
        if not self.entering_password:
            return False
        if event.key == "escape":
            self._cancel_password_entry()
            return True
        if event.key == "backspace":
            if self.password_entry:
                self.password_entry = self.password_entry[:-1]
            self.status_line = "password edit"
            return True
        if event.key == "enter":
            self._submit_password_entry()
            return True
        if event.text and not event.ctrl and not event.alt:
            self.password_entry += event.text
            self.status_line = f"password {len(self.password_entry)} chars"
            return True
        return False

    def get_button_hints(self) -> list[str]:
        if self.entering_password:
            return ["A del", "B spc", "X cancel", "Y join"]
        return ["A wifi-", "B wifi+", "X scan", "Y conn"]

    def _connect_selected_network(self) -> None:
        network = self.networks[self.selected_index]
        if network.open:
            _, message = self.context.app.wifi.connect(network)
            self.status_line = message
            return
        if network.ssid in self.context.app.wifi.passwords:
            connected, message = self.context.app.wifi.connect(network)
            self.status_line = message
            if connected:
                return
        self.password_target = network
        self.password_entry = ""
        self.status_line = f"password for {network.ssid[:8]}"

    def _submit_password_entry(self) -> None:
        network = self.password_target
        if network is None:
            return
        if not self.password_entry:
            self.status_line = "password required"
            return
        connected, message = self.context.app.wifi.connect(network, password=self.password_entry)
        self.status_line = message
        if connected:
            self.password_target = None
            self.password_entry = ""

    def _cancel_password_entry(self) -> None:
        self.password_target = None
        self.password_entry = ""
        self.status_line = "wifi connect canceled"
