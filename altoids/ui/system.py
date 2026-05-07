from __future__ import annotations

from PIL import ImageDraw

from ..colors import ACCENT, AUX, COOL, DIM, FG, INFO, SURFACE_ALT, SURFACE_GRID, WARN
from ..input_keyboard import KeyboardEvent
from ..wifi import WifiNetwork
from .base import Screen, ScreenContext
from .widgets import draw_label, draw_panel, draw_scanlines, draw_segmented_bar, draw_separator, draw_status_dot


class SystemScreen(Screen):
    name = "system"
    _PAGES = ("system", "accents")

    def __init__(self, context: ScreenContext) -> None:
        super().__init__(context)
        self.selected_index = 0
        self.networks: list[WifiNetwork] = []
        self.status_line = "Y wifi setup"
        self.password_entry = ""
        self.password_target: WifiNetwork | None = None
        self._refresh_elapsed = 0.0
        self.page_index = 0
        self.wifi_config_active = False

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
        self.networks = self.context.app.wifi.scan(force=False, allow_refresh=False)
        if self.networks:
            self.selected_index = min(self.selected_index, len(self.networks) - 1)
        else:
            self.selected_index = 0
        if self.wifi_config_active:
            self.status_line = self.context.app.wifi.last_message
        return True

    def render(self, draw: ImageDraw.ImageDraw, buffer) -> None:
        if self.page_name == "accents":
            self._render_accents(buffer)
            return
        self._render_system(buffer)

    def _render_system(self, buffer) -> None:
        app = self.context.app
        stats = app.system_snapshot()
        wifi_status = app.wifi.status(allow_refresh=not app.input_render_pending)
        selected = self.networks[self.selected_index] if self.networks else None
        width = app.config.display.width
        height = app.config.display.height
        footer_height = 24 if app.shows_button_bar else 0
        content_bottom = height - footer_height - 8
        signature = ("system", width, height, footer_height)
        buffer.paste(self.cached_background(signature, buffer.size, self._paint_system_background))
        draw = ImageDraw.Draw(buffer)

        temp_color = WARN if stats["temperature_hot"] else ACCENT
        self._draw_status_row(draw, 24, 42, "CORE", "ONLINE", True, ACCENT)
        draw_label(draw, 24, 60, self._trim(f"UP {stats['uptime']}", 14), app.font, FG)
        draw_label(draw, 24, 76, self._trim(f"TMUX {stats['terminal_windows']} WIN", 14), app.font, DIM)

        self._draw_status_row(draw, 158, 42, "WIFI", "ON" if wifi_status.connected else "OFF", wifi_status.connected, INFO)
        draw_segmented_bar(draw, 158, 60, 72, wifi_status.signal / 100.0, segments=7, color=INFO if wifi_status.connected else DIM)
        self._draw_status_row(draw, 158, 76, "BT", "LIVE" if app.bluetooth_status.connected else "IDLE", app.bluetooth_status.connected, COOL)

        self._draw_meter_row(draw, 24, 116, "CPU", stats["cpu_pct"], f"{int(stats['cpu_pct'] * 100):>3}%")
        self._draw_meter_row(draw, 24, 132, "MEM", stats["mem_pct"], f"{int(stats['mem_pct'] * 100):>3}%")
        self._draw_meter_row(draw, 24, 148, "TMP", stats["temperature_pct"], stats["temperature_label"], color=temp_color)
        draw_label(draw, 190, 116, self._trim(f"DSK {stats['disk_label']}", 12), app.font, FG)
        draw_label(draw, 190, 132, self._trim(f"IP {stats['ip_address']}", 12), app.font, FG)
        draw_label(draw, 190, 148, self._trim(wifi_status.ssid or wifi_status.state.upper(), 12), app.font, INFO if wifi_status.connected else DIM)

        roster = self._network_roster(selected)
        wireless_y = (182, 194, 206) if footer_height else (182, 196, 210)
        draw_label(draw, 22, wireless_y[0], roster[0], app.font, FG)
        draw_label(draw, 22, wireless_y[1], roster[1], app.font, DIM)
        if self.entering_password and self.password_target is not None:
            masked = "*" * min(len(self.password_entry), 12)
            password_line = self._trim(f"PASS {self.password_target.ssid} {masked}", 30)
            draw_label(draw, 22, wireless_y[2], password_line, app.font, WARN)
        elif self.wifi_config_active:
            draw_label(draw, 22, wireless_y[2], roster[2], app.font, ACCENT)
        else:
            draw_label(draw, 22, wireless_y[2], self._trim(self.status_line.upper(), 30), app.font, ACCENT if wifi_status.connected else DIM)

    def _render_accents(self, buffer) -> None:
        app = self.context.app
        status = app.accents.status
        width = app.config.display.width
        height = app.config.display.height
        footer_height = 24 if app.shows_button_bar else 0
        content_bottom = height - footer_height - 8
        signature = ("accents", width, height, footer_height)
        buffer.paste(self.cached_background(signature, buffer.size, self._paint_accents_background))
        draw = ImageDraw.Draw(buffer)

        status_color = ACCENT if status.whisplay_available else WARN
        self._draw_status_row(draw, 24, 42, "RIG", "ONLINE" if status.whisplay_available else "MISSING", True, status_color)
        self._draw_status_row(draw, 24, 62, "SPK", status.audio_status.upper(), status.audio_available, INFO)
        self._draw_status_row(draw, 24, 78, "LED", "ARMED" if status.led_enabled else "DARK", status.led_enabled, AUX)

        draw_label(draw, 160, 42, f"{status.volume_percent:>3}%", app.font_large, FG)
        draw_label(draw, 206, 48, "VOL", app.font, DIM)
        draw_segmented_bar(draw, 160, 70, 84, status.volume_percent / 100.0, segments=8, color=ACCENT)
        self._draw_status_row(draw, 160, 82, "CUE", "MUTE" if status.muted else "LIVE", not status.muted, WARN if status.muted else ACCENT)

        draw_label(draw, 24, 116, self._trim(f"WHISPLAY {('ONLINE' if status.whisplay_available else 'MISSING')}", 20), app.font, status_color)
        draw_label(draw, 24, 132, self._trim(f"SPEAKER  {status.audio_status.upper()}", 20), app.font, ACCENT if status.audio_available else DIM)
        draw_label(draw, 24, 148, self._trim(f"LED PULSE {'ARMED' if status.led_enabled else 'DARK'}", 20), app.font, AUX if status.led_enabled else DIM)
        draw_label(draw, 170, 116, f"STBY {'YES' if status.sleeping else 'NO '}", app.font, WARN if status.sleeping else FG)
        draw_label(draw, 170, 132, f"MUTE {'YES' if status.muted else 'NO '}", app.font, WARN if status.muted else FG)
        draw_label(draw, 170, 148, self._trim(f"CUE {status.last_cue.upper()}", 12), app.font, DIM)

        message = self.status_line
        if not status.whisplay_available:
            message = "whisplay hardware required"
        elif status.audio_error:
            message = status.audio_error
        draw_label(draw, 22, 184, self._trim(message.upper(), 30), app.font, WARN if (not status.whisplay_available or status.audio_error) else FG)
        draw_label(draw, 22, 200, self._trim(f"AUDIO {status.audio_status.upper()}", 30), app.font, DIM)

    def _paint_system_background(self, draw: ImageDraw.ImageDraw, buffer) -> None:
        app = self.context.app
        width = app.config.display.width
        height = app.config.display.height
        footer_height = 24 if app.shows_button_bar else 0
        content_bottom = height - footer_height - 8

        draw_label(draw, 12, 8, "SYSTEM // CONTROL", app.font, ACCENT)
        draw_label(draw, width - 84, 8, "SYS  AUX", app.font, DIM)
        draw_separator(draw, 20, width)

        core_bounds = (12, 28, 138, 96)
        link_bounds = (146, 28, width - 12, 96)
        meter_bounds = (12, 104, width - 12, 160)
        log_bounds = (12, 168, width - 12, content_bottom)
        draw_panel(draw, core_bounds, title="CORE", title_font=app.font, outline=ACCENT, title_color=ACCENT)
        draw_scanlines(draw, core_bounds, step=6)
        draw_panel(draw, link_bounds, title="LINK", title_font=app.font, outline=INFO, title_color=INFO)
        draw_scanlines(draw, link_bounds, step=6)
        draw_panel(draw, meter_bounds, title="LOAD", title_font=app.font, fill=SURFACE_ALT)
        draw_panel(draw, log_bounds, title="WIRELESS", title_font=app.font, outline=COOL, title_color=COOL)
        draw_scanlines(draw, log_bounds, step=6, color=SURFACE_GRID)

    def _paint_accents_background(self, draw: ImageDraw.ImageDraw, buffer) -> None:
        app = self.context.app
        width = app.config.display.width
        height = app.config.display.height
        footer_height = 24 if app.shows_button_bar else 0
        content_bottom = height - footer_height - 8

        draw_label(draw, 12, 8, "ACCENTS // I/O", app.font, ACCENT)
        draw_label(draw, width - 84, 8, "SYS  AUX", app.font, DIM)
        draw_separator(draw, 20, width)

        rig_bounds = (12, 28, 138, 96)
        audio_bounds = (146, 28, width - 12, 96)
        controls_bounds = (12, 104, width - 12, 160)
        state_bounds = (12, 168, width - 12, content_bottom)
        draw_panel(draw, rig_bounds, title="RIG", title_font=app.font, outline=AUX, title_color=AUX)
        draw_scanlines(draw, rig_bounds, step=6)
        draw_panel(draw, audio_bounds, title="CUES", title_font=app.font, outline=ACCENT, title_color=ACCENT)
        draw_scanlines(draw, audio_bounds, step=6)
        draw_panel(draw, controls_bounds, title="CONTROL", title_font=app.font, fill=SURFACE_ALT)
        draw_panel(draw, state_bounds, title="STATUS", title_font=app.font, outline=INFO, title_color=INFO)
        draw_scanlines(draw, state_bounds, step=6, color=SURFACE_GRID)

    def on_button(self, button: str, long_press: bool) -> bool:
        if button == "A" and long_press and not self.entering_password and not self.wifi_config_active:
            self.page_index = (self.page_index - 1) % len(self._PAGES)
            self.status_line = self.page_name
            return True
        if button == "B" and long_press and not self.entering_password and not self.wifi_config_active:
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
        if self.wifi_config_active:
            if button == "X" and long_press:
                self._leave_wifi_config()
                return True
            if button == "Y" and long_press:
                self.context.app.set_screen("term")
                return True
            if button == "X":
                self._enter_wifi_config(force_scan=True)
                return True
            if button == "A":
                self._select_wifi_network(-1)
                return True
            if button == "B":
                self._select_wifi_network(1)
                return True
            if button == "Y":
                if self.networks:
                    self._connect_selected_network()
                else:
                    self.status_line = "no wifi networks"
                return True
            return False
        if button == "X" and long_press:
            self.context.app.set_screen("home")
            return True
        if button == "X":
            self.context.app.set_screen("home")
            return True
        if button == "A":
            self.page_index = (self.page_index - 1) % len(self._PAGES)
            self.status_line = self.page_name
            return True
        if button == "B":
            self.page_index = (self.page_index + 1) % len(self._PAGES)
            self.status_line = self.page_name
            return True
        if button == "Y":
            if long_press:
                self.context.app.set_screen("term")
                return True
            self._enter_wifi_config(force_scan=True)
            return True
        return False

    def on_keyboard_event(self, event: KeyboardEvent) -> bool:
        if self.wifi_config_active and not self.entering_password:
            if event.key == "escape":
                self._leave_wifi_config()
                return True
            if event.key in {"up", "left", "pageup", "k"}:
                self._select_wifi_network(-1)
                return True
            if event.key in {"down", "right", "pagedown", "j"}:
                self._select_wifi_network(1)
                return True
            if event.key == "r":
                self._enter_wifi_config(force_scan=True)
                return True
            if event.key == "enter":
                if self.networks:
                    self._connect_selected_network()
                else:
                    self.status_line = "no wifi networks"
                return True
            return False
        if not self.entering_password and event.key in {"1"}:
            self.page_index = 0
            self.status_line = self.page_name
            return True
        if not self.entering_password and event.key in {"2"}:
            self.page_index = 1
            self.status_line = self.page_name
            return True
        if not self.entering_password and event.key in {"left", "pageup", "[", "h", "a"}:
            self.page_index = (self.page_index - 1) % len(self._PAGES)
            self.status_line = self.page_name
            return True
        if not self.entering_password and event.key in {"right", "pagedown", "]", "tab", "l", "d"}:
            self.page_index = (self.page_index + 1) % len(self._PAGES)
            self.status_line = self.page_name
            return True
        if not self.entering_password and self.page_name == "accents":
            return self._on_accents_key(event)
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
        if self.wifi_config_active:
            return ["A prev", "B next", "X scan", "Y join"]
        if self.page_name == "accents":
            return ["A vol-", "B vol+", "X mute", "Y led"]
        return ["A page-", "B page+", "X home", "Y wifi"]

    def _enter_wifi_config(self, *, force_scan: bool = False) -> None:
        self.wifi_config_active = True
        self.networks = self.context.app.wifi.scan(force=force_scan)
        if self.networks:
            self.selected_index = min(self.selected_index, len(self.networks) - 1)
        else:
            self.selected_index = 0
        self.status_line = self.context.app.wifi.last_message
        self._refresh_elapsed = 0.0

    def _leave_wifi_config(self) -> None:
        self.wifi_config_active = False
        self.password_target = None
        self.password_entry = ""
        self.status_line = "wifi setup closed"

    def _select_wifi_network(self, delta: int) -> None:
        if not self.networks:
            self.status_line = "no wifi networks"
            return
        self.selected_index = (self.selected_index + delta) % len(self.networks)
        self.status_line = f"selected {self.networks[self.selected_index].ssid}"

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
            self.wifi_config_active = False

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

    def _on_accents_key(self, event: KeyboardEvent) -> bool:
        app = self.context.app
        status = app.accents.status
        if not status.whisplay_available:
            self.status_line = "whisplay hardware required"
            app.accents.trigger("error")
            return True
        if event.key in {"down", "-"}:
            if not status.audio_available:
                self.status_line = "speaker unavailable"
                app.accents.trigger("error")
                return True
            app.accents.adjust_volume(-10)
            self.status_line = f"volume {app.accents.status.volume_percent}%"
            return True
        if event.key in {"up", "+", "="}:
            if not status.audio_available:
                self.status_line = "speaker unavailable"
                app.accents.trigger("error")
                return True
            app.accents.adjust_volume(10)
            self.status_line = f"volume {app.accents.status.volume_percent}%"
            return True
        if event.key == "m":
            if not status.audio_available:
                self.status_line = "speaker unavailable"
                app.accents.trigger("error")
                return True
            app.accents.toggle_mute()
            self.status_line = "mute on" if app.accents.status.muted else "mute off"
            return True
        if event.key == "l":
            if not status.led_available:
                self.status_line = "led unavailable"
                app.accents.trigger("error")
                return True
            app.accents.toggle_led_enabled()
            self.status_line = "led pulses on" if app.accents.status.led_enabled else "led pulses off"
            return True
        return False

    def _draw_meter_row(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        label: str,
        pct: float,
        value: str,
        *,
        color: str = ACCENT,
    ) -> None:
        draw_label(draw, x, y, label, self.context.app.font, FG)
        draw_segmented_bar(draw, x + 34, y + 1, 86, pct, segments=10, color=color)
        draw_label(draw, x + 126, y, value, self.context.app.font, color if color != ACCENT else FG)

    def _draw_status_row(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        label: str,
        value: str,
        active: bool,
        color: str,
    ) -> None:
        app = self.context.app
        draw_status_dot(draw, x, y + 1, active, color)
        draw_label(draw, x + 14, y, label, app.font, DIM)
        draw_label(draw, x + 48, y, self._trim(value, 10), app.font, color if active else DIM)

    def _network_roster(self, selected: WifiNetwork | None) -> tuple[str, str, str]:
        wifi_status = self.context.app.wifi.status(allow_refresh=not self.context.app.input_render_pending)
        line_one = self._trim(
            f"NET {(wifi_status.ssid or wifi_status.state).upper()} {wifi_status.signal:>3}%",
            30,
        )
        if not self.wifi_config_active:
            return line_one, "WIFI SETUP: PRESS Y", "Y WIFI SETUP"
        if selected is None:
            return line_one, "PICK NONE", self._trim(self.status_line.upper(), 30)
        marker = "*" if selected.active else ">"
        security = "OPEN" if selected.open else "LOCK"
        line_two = self._trim(
            f"PICK {marker} {selected.ssid.upper()} {selected.signal:>3}% {security}",
            30,
        )
        detail = self._trim(f"{self.selected_index + 1}/{len(self.networks)} {self.status_line.upper()}", 30)
        return line_one, line_two, detail


def uptime_field(value: object) -> str:
    return str(value)[:6]
