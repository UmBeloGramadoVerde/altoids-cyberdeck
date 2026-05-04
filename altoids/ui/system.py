from __future__ import annotations

from PIL import ImageDraw

from ..colors import ACCENT, FG, WARN
from ..input_keyboard import KeyboardEvent
from ..wifi import WifiNetwork
from .base import Screen, ScreenContext
from .widgets import draw_label, draw_progress_bar, draw_separator


class SystemScreen(Screen):
    name = "system"
    _PAGES = ("system", "accents")

    def __init__(self, context: ScreenContext) -> None:
        super().__init__(context)
        self.selected_index = 0
        self.networks: list[WifiNetwork] = []
        self.status_line = "A/B browse  X scan  Y connect"
        self.password_entry = ""
        self.password_target: WifiNetwork | None = None
        self._refresh_elapsed = 0.0
        self.page_index = 0

    @property
    def entering_password(self) -> bool:
        return self.password_target is not None

    @property
    def page_name(self) -> str:
        return self._PAGES[self.page_index]

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
        if self.page_name == "accents":
            self._render_accents(draw)
            return
        self._render_system(draw)

    def _render_system(self, draw: ImageDraw.ImageDraw) -> None:
        app = self.context.app
        stats = app.system_snapshot()
        wifi_status = app.wifi.status()
        selected = self.networks[self.selected_index] if self.networks else None
        width = app.config.display.width
        draw_label(draw, 8, 8, "SYSTEM", app.font_large, FG)
        draw_separator(draw, 28, width)

        bar_width = max(96, width - 132)
        draw_label(draw, 8, 40, f"CPU: {int(stats['cpu_pct'] * 100):>3}%", app.font)
        draw_progress_bar(draw, 88, 42, bar_width, stats["cpu_pct"], ACCENT)
        draw_label(draw, 8, 60, f"MEM: {int(stats['mem_pct'] * 100):>3}%", app.font)
        draw_progress_bar(draw, 88, 62, bar_width, stats["mem_pct"], ACCENT)
        draw_label(draw, 8, 80, f"TEMP: {stats['temperature_label']}", app.font, WARN if stats["temperature_hot"] else FG)
        draw_progress_bar(draw, 88, 82, bar_width, stats["temperature_pct"], WARN if stats["temperature_hot"] else ACCENT)
        draw_label(draw, 8, 104, f"DISK: {stats['disk_label']}", app.font)
        draw_label(draw, 8, 124, f"IP: {self._trim(stats['ip_address'], 22)}", app.font)
        draw_label(draw, 8, 144, f"BT: {'connected' if app.bluetooth_status.connected else 'disconnected'}", app.font)
        draw_label(draw, 8, 164, f"tmux: {stats['terminal_windows']} windows", app.font)

        wifi_line = f"wifi: {wifi_status.ssid or wifi_status.state} {wifi_status.signal}%"
        draw_label(draw, 8, 184, self._trim(wifi_line, 30), app.font, ACCENT if wifi_status.connected else FG)
        if selected:
            marker = "*" if selected.active else ">"
            security = "open" if selected.open else "lock"
            selected_line = f"{marker} {selected.ssid} {selected.signal:>3}% {security}"
            draw_label(draw, 8, 198, self._trim(selected_line, 30), app.font)
        else:
            draw_label(draw, 8, 198, "no wifi networks cached", app.font)
        if self.entering_password and self.password_target is not None:
            masked = "*" * min(len(self.password_entry), 12)
            draw_label(draw, 8, 210, self._trim(f"pass {self.password_target.ssid} {masked}", 30), app.font, WARN)
        else:
            draw_label(draw, 8, 210, self._trim(self.status_line, 30), app.font)

    def _render_accents(self, draw: ImageDraw.ImageDraw) -> None:
        app = self.context.app
        status = app.accents.status
        width = app.config.display.width
        draw_label(draw, 8, 8, "ACCENTS", app.font_large, FG)
        draw_separator(draw, 28, width)

        draw_label(draw, 8, 40, f"whisplay: {'available' if status.whisplay_available else 'not available'}", app.font, ACCENT if status.whisplay_available else FG)
        draw_label(draw, 8, 64, f"speaker: {status.audio_status}", app.font, ACCENT if status.audio_available else FG)
        draw_label(draw, 8, 88, f"volume: {status.volume_percent:>3}%", app.font)
        draw_progress_bar(draw, 88, 90, max(96, width - 132), status.volume_percent / 100.0, ACCENT)
        draw_label(draw, 8, 112, f"mute: {'on' if status.muted else 'off'}", app.font)
        draw_label(draw, 8, 136, f"led pulses: {'on' if status.led_enabled else 'off'}", app.font)
        draw_label(draw, 8, 160, f"standby: {'sleeping' if status.sleeping else 'awake'}", app.font)
        draw_label(draw, 8, 184, self._trim(f"last cue: {status.last_cue}", 30), app.font)
        message = self.status_line
        if not status.whisplay_available:
            message = "whisplay hardware required"
        elif status.audio_error:
            message = status.audio_error
        draw_label(draw, 8, 210, self._trim(message, 30), app.font, WARN if not status.whisplay_available else FG)

    def on_button(self, button: str, long_press: bool) -> bool:
        if button == "A" and long_press:
            self.page_index = (self.page_index - 1) % len(self._PAGES)
            self.status_line = self.page_name
            return True
        if button == "B" and long_press:
            self.page_index = (self.page_index + 1) % len(self._PAGES)
            self.status_line = self.page_name
            return True
        if self.page_name == "accents":
            return self._on_accents_button(button, long_press)
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
        if self.page_name == "accents":
            return ["A vol-", "B vol+", "X mute", "Y led"]
        return ["A wifi-", "B wifi+", "X scan", "Y conn"]

    def _connect_selected_network(self) -> None:
        network = self.networks[self.selected_index]
        if network.open:
            connected, message = self.context.app.wifi.connect(network)
            self.status_line = message
            self.context.app.accents.trigger("wifi_success" if connected else "wifi_error")
            return
        if network.ssid in self.context.app.wifi.passwords:
            connected, message = self.context.app.wifi.connect(network)
            self.status_line = message
            self.context.app.accents.trigger("wifi_success" if connected else "wifi_error")
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
            self.context.app.accents.trigger("error")
            return
        connected, message = self.context.app.wifi.connect(network, password=self.password_entry)
        self.status_line = message
        self.context.app.accents.trigger("wifi_success" if connected else "wifi_error")
        if connected:
            self.password_target = None
            self.password_entry = ""

    def _cancel_password_entry(self) -> None:
        self.password_target = None
        self.password_entry = ""
        self.status_line = "wifi connect canceled"

    @staticmethod
    def _trim(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return f"{text[: max(0, limit - 1)]}>"

    def _on_accents_button(self, button: str, long_press: bool) -> bool:
        app = self.context.app
        status = app.accents.status
        if button == "X" and long_press:
            app.set_screen("home")
            return True
        if button == "Y" and long_press:
            app.set_screen("term")
            return True
        if not status.whisplay_available:
            self.status_line = "whisplay hardware required"
            app.accents.trigger("error")
            return True
        if button == "A":
            if not status.audio_available:
                self.status_line = "speaker unavailable"
                app.accents.trigger("error")
                return True
            app.accents.adjust_volume(-10)
            self.status_line = f"volume {app.accents.status.volume_percent}%"
            return True
        if button == "B":
            if not status.audio_available:
                self.status_line = "speaker unavailable"
                app.accents.trigger("error")
                return True
            app.accents.adjust_volume(10)
            self.status_line = f"volume {app.accents.status.volume_percent}%"
            return True
        if button == "X":
            if not status.audio_available:
                self.status_line = "speaker unavailable"
                app.accents.trigger("error")
                return True
            app.accents.toggle_mute()
            self.status_line = "mute on" if app.accents.status.muted else "mute off"
            return True
        if button == "Y":
            if not status.led_available:
                self.status_line = "led unavailable"
                app.accents.trigger("error")
                return True
            app.accents.toggle_led_enabled()
            self.status_line = "led pulses on" if app.accents.status.led_enabled else "led pulses off"
            return True
        return False
