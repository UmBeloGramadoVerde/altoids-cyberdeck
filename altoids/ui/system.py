from __future__ import annotations

from PIL import ImageDraw

from ..colors import ACCENT, AUX, BG, COOL, DIM, FG, INFO, SURFACE_ALT, SURFACE_GRID, SURFACE_PANEL, WARN
from ..input_keyboard import KeyboardEvent
from ..wifi import WifiNetwork
from .base import Screen, ScreenContext
from .widgets import (
    draw_corner_ticks,
    draw_detail_frame,
    draw_dot_grid,
    draw_label,
    draw_panel,
    draw_scanlines,
    draw_segmented_bar,
    draw_separator,
    draw_status_dot,
)


class SystemScreen(Screen):
    name = "system"

    # Panel key bindings for detail expansion
    _PANEL_KEYS = {
        "c": "core",
        "o": "load",
        "l": "link",
        "w": "wireless",
        "r": "rig",
        "u": "cues",
    }

    def __init__(self, context: ScreenContext) -> None:
        super().__init__(context)
        self.selected_index = 0
        self.networks: list[WifiNetwork] = []
        self.status_line = "W wireless  C core  L link"
        self.password_entry = ""
        self.password_target: WifiNetwork | None = None
        self._refresh_elapsed = 0.0
        self.detail_active: str | None = None
        self.detail_scroll = 0

    @property
    def entering_password(self) -> bool:
        return self.password_target is not None

    def update(self, dt: float) -> bool:
        self._refresh_elapsed += dt
        if self._refresh_elapsed < 1.0:
            return False
        self._refresh_elapsed = 0.0
        selected_network = self._selected_network()
        selected_ssid = selected_network.ssid if selected_network is not None else None
        self.networks = self.context.app.wifi.scan(force=False, allow_refresh=False)
        self._sync_wifi_selection(preferred_ssid=selected_ssid, prefer_active=False)
        return True

    # ── Rendering ──────────────────────────────────────────────

    def render(self, draw: ImageDraw.ImageDraw, buffer) -> None:
        if self.detail_active is not None:
            self._render_detail(buffer)
            return
        self._render_overview(buffer)

    def _render_overview(self, buffer) -> None:
        app = self.context.app
        stats = app.system_snapshot()
        wifi_status = app.wifi.status(allow_refresh=not app.input_render_pending)
        accent_status = app.accents.status
        width = app.config.display.width
        height = app.config.display.height
        footer_height = 24 if app.shows_button_bar else 0
        content_bottom = height - footer_height - 8
        layout = self._overview_layout(width)
        signature = ("system_unified", width, height, footer_height)
        buffer.paste(self.cached_background(signature, buffer.size, self._paint_overview_background))
        draw = ImageDraw.Draw(buffer)

        # Derive wifi connected state with IP fallback
        ip_addr = str(stats.get("ip_address", "offline"))
        wifi_connected = wifi_status.connected or (ip_addr not in ("offline", ""))
        wifi_sig = wifi_status.signal if wifi_status.connected else (75 if wifi_connected else 0)
        wifi_ssid = wifi_status.ssid or (ip_addr if wifi_connected and not wifi_status.ssid else wifi_status.state)

        temp_color = WARN if stats["temperature_hot"] else ACCENT
        core_bounds = layout["core_bounds"]
        load_bounds = layout["load_bounds"]
        link_bounds = layout["link_bounds"]
        wireless_bounds = layout["wireless_bounds"]
        rig_bounds = layout["rig_bounds"]
        cues_bounds = layout["cues_bounds"]

        # ── CORE panel content (background, left top) ──
        draw_status_dot(draw, core_bounds[0] + 12, 44, True, ACCENT)
        draw_label(draw, core_bounds[0] + 24, 42, "ONLINE", app.font, ACCENT)
        core_limit = max(10, (core_bounds[2] - core_bounds[0] - 12) // 7)
        draw_label(draw, core_bounds[0] + 12, 58, self._trim(f"UP {stats['uptime']}", core_limit), app.font, FG)
        draw_label(draw, core_bounds[0] + 12, 72, self._trim(f"TMUX {stats['terminal_windows']}W", core_limit), app.font, DIM)

        # ── LOAD panel content (foreground, right top) ──
        self._draw_meter_row(draw, load_bounds[0] + 16, 42, "CPU", stats["cpu_pct"], f"{int(stats['cpu_pct'] * 100):>3}%")
        self._draw_meter_row(draw, load_bounds[0] + 16, 58, "MEM", stats["mem_pct"], f"{int(stats['mem_pct'] * 100):>3}%")
        self._draw_meter_row(draw, load_bounds[0] + 16, 74, "TMP", stats["temperature_pct"], stats["temperature_label"], color=temp_color)

        # ── LINK panel content (background, left middle) ──
        draw_status_dot(draw, link_bounds[0] + 12, 108, wifi_connected, INFO)
        draw_label(draw, link_bounds[0] + 24, 106, "WIFI", app.font, DIM)
        draw_label(draw, link_bounds[0] + 54, 106, "ON" if wifi_connected else "OFF", app.font, INFO if wifi_connected else DIM)
        draw_status_dot(draw, link_bounds[0] + 12, 124, app.bluetooth_status.connected, COOL)
        draw_label(draw, link_bounds[0] + 24, 122, "BT", app.font, DIM)
        draw_label(draw, link_bounds[0] + 46, 122, "LIVE" if app.bluetooth_status.connected else "IDLE", app.font, COOL if app.bluetooth_status.connected else DIM)
        draw_label(draw, link_bounds[0] + 12, 138, self._trim(f"DSK {stats['disk_label']}", core_limit), app.font, DIM)

        # ── WIRELESS panel content (foreground, right middle) ──
        wireless_limit = max(12, (wireless_bounds[2] - wireless_bounds[0] - 20) // 7)
        wireless_bar_width = max(56, wireless_bounds[2] - wireless_bounds[0] - 84)
        wireless_left = wireless_bounds[0] + 16
        draw_label(draw, wireless_left, 106, self._trim(f"NET {wifi_ssid.upper()}", wireless_limit), app.font, FG)
        draw_segmented_bar(draw, wireless_left, 124, wireless_bar_width, wifi_sig / 100.0, segments=7, color=INFO if wifi_connected else DIM)
        draw_label(draw, wireless_left + wireless_bar_width + 8, 122, f"{wifi_sig:>3}%", app.font, INFO if wifi_connected else DIM)
        draw_label(draw, wireless_left, 138, self._trim(f"IP {ip_addr}", wireless_limit), app.font, FG)

        # ── RIG panel content (background, left bottom) ──
        rig_online = accent_status.whisplay_available
        rig_limit = max(10, (rig_bounds[2] - rig_bounds[0] - 12) // 7)
        draw_status_dot(draw, rig_bounds[0] + 12, 168, rig_online, AUX)
        draw_label(draw, rig_bounds[0] + 24, 166, "WHSP", app.font, DIM)
        draw_label(draw, rig_bounds[0] + 58, 166, "LIVE" if rig_online else "OFF", app.font, AUX if rig_online else DIM)
        draw_label(draw, rig_bounds[0] + 12, 182, self._trim(f"LED {'ARM' if accent_status.led_enabled else 'OFF'}", rig_limit), app.font, DIM)

        # ── CUES panel content (foreground, right bottom) ──
        cues_left = cues_bounds[0] + 12
        cues_bar_x = cues_left + 62
        cues_bar_width = max(36, cues_bounds[2] - cues_bar_x - 12)
        draw_label(draw, cues_left, 166, f"VOL {accent_status.volume_percent:>3}%", app.font, FG)
        draw_segmented_bar(draw, cues_bar_x, 168, cues_bar_width, accent_status.volume_percent / 100.0, segments=6, color=ACCENT)
        mute_label = "MUTE" if accent_status.muted else "LIVE"
        draw_status_dot(draw, cues_left, 184, not accent_status.muted, WARN if accent_status.muted else ACCENT)
        draw_label(draw, cues_left + 14, 182, "CUE", app.font, DIM)
        draw_label(draw, cues_left + 44, 182, mute_label, app.font, WARN if accent_status.muted else ACCENT)

        # ── Status line ──
        status_y = content_bottom - 10 if footer_height else height - 18
        draw_label(draw, 14, status_y, self._trim(self.status_line.upper(), max(20, (width - 28) // 7)), app.font, DIM)

    def _paint_overview_background(self, draw: ImageDraw.ImageDraw, buffer) -> None:
        app = self.context.app
        width = app.config.display.width
        height = app.config.display.height
        footer_height = 24 if app.shows_button_bar else 0

        # Header
        draw_label(draw, 12, 8, "SYSTEM // MAGI-03", app.font, ACCENT)
        draw_label(draw, width - 68, 8, "VFD DIAG", app.font, DIM)
        draw_separator(draw, 20, width)

        layout = self._overview_layout(width)
        core_bounds = layout["core_bounds"]
        load_bounds = layout["load_bounds"]
        link_bounds = layout["link_bounds"]
        wireless_bounds = layout["wireless_bounds"]
        rig_bounds = layout["rig_bounds"]
        cues_bounds = layout["cues_bounds"]

        # Background panels (drawn first — recede visually)
        draw_panel(draw, core_bounds, title="CORE", title_font=app.font, outline=ACCENT, title_color=ACCENT)
        draw_scanlines(draw, core_bounds, step=6)
        draw_panel(draw, link_bounds, title="LINK", title_font=app.font, outline=INFO, title_color=INFO)
        draw_scanlines(draw, link_bounds, step=6)
        draw_panel(draw, rig_bounds, title="RIG", title_font=app.font, outline=AUX, title_color=AUX)
        draw_scanlines(draw, rig_bounds, step=6)

        # Foreground panels (drawn last — actionable data, full opacity)
        draw_panel(draw, load_bounds, title="LOAD", title_font=app.font, outline=WARN, title_color=WARN)
        draw_panel(draw, wireless_bounds, title="WIRELESS", title_font=app.font, outline=COOL, title_color=COOL)
        draw_panel(draw, cues_bounds, title="CUES", title_font=app.font, outline=ACCENT, title_color=ACCENT)

    @staticmethod
    def _overview_layout(width: int) -> dict[str, tuple[int, int, int, int]]:
        total = width - 20
        col_split = 10 + total * 35 // 100
        core_bounds = (10, 28, col_split, 90)
        load_bounds = (col_split - 4, 28, width - 10, 90)
        link_bounds = (10, 96, col_split, 156)
        wireless_bounds = (col_split - 4, 96, width - 10, 156)
        rig_split = 10 + total * 45 // 100
        rig_bounds = (10, 160, rig_split, 198)
        cues_bounds = (rig_split + 2, 160, width - 10, 198)
        return {
            "core_bounds": core_bounds,
            "load_bounds": load_bounds,
            "link_bounds": link_bounds,
            "wireless_bounds": wireless_bounds,
            "rig_bounds": rig_bounds,
            "cues_bounds": cues_bounds,
        }

    # ── Detail Views ──────────────────────────────────────────

    def _render_detail(self, buffer) -> None:
        app = self.context.app
        width = app.config.display.width
        height = app.config.display.height
        draw = ImageDraw.Draw(buffer)
        draw.rectangle((0, 0, width, height), fill=BG)

        panel = self.detail_active
        if panel == "core":
            self._render_core_detail(draw, width, height)
        elif panel == "load":
            self._render_load_detail(draw, width, height)
        elif panel == "link":
            self._render_link_detail(draw, width, height)
        elif panel == "wireless":
            self._render_wireless_detail(draw, width, height)
        elif panel == "rig":
            self._render_rig_detail(draw, width, height)
        elif panel == "cues":
            self._render_cues_detail(draw, width, height)

    def _render_core_detail(self, draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
        app = self.context.app
        stats = app.system_snapshot()
        footer_h = 24 if app.shows_button_bar else 0
        bounds = draw_detail_frame(draw, width, height, title="CORE", font=app.font, color=ACCENT, footer_height=footer_h)
        left, top, right, bottom = bounds

        draw_label(draw, left, top, "STATUS", app.font, ACCENT)
        draw_status_dot(draw, left, top + 20, True, ACCENT)
        draw_label(draw, left + 14, top + 18, "SYSTEM ONLINE", app.font, ACCENT)
        draw_label(draw, left, top + 38, f"UPTIME  {stats['uptime']}", app.font, FG)
        draw_label(draw, left, top + 56, f"TMUX    {stats['terminal_windows']} WINDOWS", app.font, FG)
        draw_label(draw, left, top + 74, f"IP      {stats['ip_address']}", app.font, FG)
        draw_label(draw, left, top + 92, f"DISK    {stats['disk_label']}", app.font, FG)

        draw_label(draw, left, bottom - 14, "ESC BACK", app.font, DIM)

    def _render_load_detail(self, draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
        app = self.context.app
        stats = app.system_snapshot()
        footer_h = 24 if app.shows_button_bar else 0
        bounds = draw_detail_frame(draw, width, height, title="LOAD", font=app.font, color=WARN, footer_height=footer_h)
        left, top, right, bottom = bounds

        temp_color = WARN if stats["temperature_hot"] else ACCENT

        draw_label(draw, left, top, "RESOURCE MONITOR", app.font, WARN)
        y = top + 22
        for label, pct, value, color in [
            ("CPU", stats["cpu_pct"], f"{int(stats['cpu_pct'] * 100):>3}%", ACCENT),
            ("MEM", stats["mem_pct"], f"{int(stats['mem_pct'] * 100):>3}%", ACCENT),
            ("TMP", stats["temperature_pct"], stats["temperature_label"], temp_color),
        ]:
            draw_label(draw, left, y, label, app.font, FG)
            draw_segmented_bar(draw, left + 34, y + 1, 120, pct, segments=12, color=color)
            draw_label(draw, left + 162, y, value, app.font, color if color != ACCENT else FG)
            y += 22

        draw_label(draw, left, y + 8, f"DISK  {stats['disk_label']}", app.font, FG)

        draw_label(draw, left, bottom - 14, "ESC BACK", app.font, DIM)

    def _render_link_detail(self, draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
        app = self.context.app
        stats = app.system_snapshot()
        wifi_status = app.wifi.status(allow_refresh=not app.input_render_pending)
        ip_addr = str(stats.get("ip_address", "offline"))
        wifi_connected = wifi_status.connected or (ip_addr not in ("offline", ""))
        footer_h = 24 if app.shows_button_bar else 0
        bounds = draw_detail_frame(draw, width, height, title="LINK", font=app.font, color=INFO, footer_height=footer_h)
        left, top, right, bottom = bounds

        draw_label(draw, left, top, "CONNECTIVITY", app.font, INFO)
        y = top + 22
        draw_status_dot(draw, left, y + 1, wifi_connected, INFO)
        draw_label(draw, left + 14, y, "WIFI", app.font, DIM)
        draw_label(draw, left + 52, y, "ON" if wifi_connected else "OFF", app.font, INFO if wifi_connected else DIM)
        y += 18
        draw_status_dot(draw, left, y + 1, app.bluetooth_status.connected, COOL)
        draw_label(draw, left + 14, y, "BT", app.font, DIM)
        draw_label(draw, left + 42, y, "LIVE" if app.bluetooth_status.connected else "IDLE", app.font, COOL if app.bluetooth_status.connected else DIM)
        y += 22
        draw_label(draw, left, y, f"SSID    {(wifi_status.ssid or 'NONE').upper()}", app.font, FG)
        y += 16
        draw_label(draw, left, y, f"SIGNAL  {wifi_status.signal}%", app.font, FG)
        y += 16
        draw_label(draw, left, y, f"IP      {ip_addr}", app.font, FG)
        y += 16
        draw_label(draw, left, y, f"DISK    {stats['disk_label']}", app.font, FG)

        draw_label(draw, left, bottom - 14, "ESC BACK  W WIRELESS", app.font, DIM)

    def _render_wireless_detail(self, draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
        app = self.context.app
        wifi_status = app.wifi.status(allow_refresh=not app.input_render_pending)
        stats = app.system_snapshot()
        ip_addr = str(stats.get("ip_address", "offline"))
        wifi_connected = wifi_status.connected or (ip_addr not in ("offline", ""))
        wifi_ssid = wifi_status.ssid or (ip_addr if wifi_connected else wifi_status.state)
        wifi_sig = wifi_status.signal if wifi_status.connected else (75 if wifi_connected else 0)
        footer_h = 24 if app.shows_button_bar else 0
        bounds = draw_detail_frame(draw, width, height, title="WIRELESS", font=app.font, color=COOL, footer_height=footer_h)
        left, top, right, bottom = bounds

        # Current connection status
        ssid_text = wifi_ssid.upper()
        draw_label(draw, left, top, self._trim(f"NET {ssid_text}", 28), app.font, FG)
        draw_segmented_bar(draw, left, top + 16, 72, wifi_sig / 100.0, segments=7, color=INFO if wifi_connected else DIM)
        draw_label(draw, left + 80, top + 14, f"{wifi_sig:>3}%", app.font, INFO if wifi_connected else DIM)
        state_label = "CONNECTED" if wifi_connected else wifi_status.state.upper()
        draw_label(draw, left + 126, top + 14, self._trim(state_label, 12), app.font, ACCENT if wifi_connected else DIM)

        # Password entry mode
        if self.entering_password and self.password_target is not None:
            draw_label(draw, left, top + 36, f"PASSWORD FOR {self._trim(self.password_target.ssid.upper(), 16)}", app.font, WARN)
            masked = "*" * min(len(self.password_entry), 20)
            draw_label(draw, left, top + 54, f"> {masked}_", app.font, FG)
            draw_label(draw, left, bottom - 14, "ENTER JOIN  BACKSPACE DEL  ESC CANCEL", app.font, DIM)
            return

        # Network roster (scrollable)
        roster_top = top + 36
        roster_bottom = bottom - 18
        visible_lines = max(1, (roster_bottom - roster_top) // 14)

        if not self.networks:
            draw_label(draw, left, roster_top, "NO NETWORKS  PRESS R TO SCAN", app.font, DIM)
        else:
            scroll_offset = max(0, self.selected_index - visible_lines + 2)
            scroll_offset = min(scroll_offset, max(0, len(self.networks) - visible_lines))
            for i, network in enumerate(self.networks[scroll_offset:scroll_offset + visible_lines]):
                actual_index = scroll_offset + i
                y = roster_top + i * 14
                selected = actual_index == self.selected_index
                marker = ">" if selected else " "
                active = "*" if network.active else " "
                security = "OPEN" if network.open else "LOCK"
                line = f"{marker}{active}{self._trim(network.ssid.upper(), 16):16s} {network.signal:>3}% {security}"
                color = FG if selected else DIM
                draw_label(draw, left, y, line, app.font, color)
            # Scroll indicator
            if len(self.networks) > visible_lines:
                draw_label(draw, right - 40, roster_top, f"{self.selected_index + 1}/{len(self.networks)}", app.font, DIM)

        # Footer hints
        draw_label(draw, left, bottom - 14, "UP/DN NAV  R SCAN  ENTER JOIN  ESC BACK", app.font, DIM)

    def _render_rig_detail(self, draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
        app = self.context.app
        status = app.accents.status
        footer_h = 24 if app.shows_button_bar else 0
        bounds = draw_detail_frame(draw, width, height, title="RIG", font=app.font, color=AUX, footer_height=footer_h)
        left, top, right, bottom = bounds

        status_color = ACCENT if status.whisplay_available else WARN
        draw_label(draw, left, top, "HARDWARE STATUS", app.font, AUX)
        y = top + 22
        draw_status_dot(draw, left, y + 1, status.whisplay_available, status_color)
        draw_label(draw, left + 14, y, "WHISPLAY", app.font, DIM)
        draw_label(draw, left + 82, y, "ONLINE" if status.whisplay_available else "MISSING", app.font, status_color)
        y += 18
        draw_status_dot(draw, left, y + 1, status.audio_available, INFO)
        draw_label(draw, left + 14, y, "SPEAKER", app.font, DIM)
        draw_label(draw, left + 76, y, status.audio_status.upper(), app.font, INFO if status.audio_available else DIM)
        y += 18
        draw_status_dot(draw, left, y + 1, status.led_enabled, AUX)
        draw_label(draw, left + 14, y, "LED", app.font, DIM)
        draw_label(draw, left + 44, y, "ARMED" if status.led_enabled else "DARK", app.font, AUX if status.led_enabled else DIM)
        y += 22
        draw_label(draw, left, y, f"STANDBY  {'YES' if status.sleeping else 'NO'}", app.font, WARN if status.sleeping else FG)
        y += 16
        draw_label(draw, left, y, f"LAST CUE {self._trim(status.last_cue.upper(), 16)}", app.font, DIM)

        if status.audio_error:
            y += 20
            draw_label(draw, left, y, self._trim(f"ERR {status.audio_error.upper()}", 30), app.font, WARN)

        draw_label(draw, left, bottom - 14, "ESC BACK", app.font, DIM)

    def _render_cues_detail(self, draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
        app = self.context.app
        status = app.accents.status
        footer_h = 24 if app.shows_button_bar else 0
        bounds = draw_detail_frame(draw, width, height, title="CUES", font=app.font, color=ACCENT, footer_height=footer_h)
        left, top, right, bottom = bounds

        draw_label(draw, left, top, "AUDIO CONTROL", app.font, ACCENT)
        y = top + 24
        draw_label(draw, left, y, f"VOLUME  {status.volume_percent:>3}%", app.font, FG)
        draw_segmented_bar(draw, left, y + 16, 160, status.volume_percent / 100.0, segments=16, color=ACCENT)
        y += 40
        mute_label = "MUTED" if status.muted else "LIVE"
        draw_status_dot(draw, left, y + 1, not status.muted, WARN if status.muted else ACCENT)
        draw_label(draw, left + 14, y, "CUE", app.font, DIM)
        draw_label(draw, left + 44, y, mute_label, app.font, WARN if status.muted else ACCENT)
        y += 20
        draw_status_dot(draw, left, y + 1, status.led_enabled, AUX)
        draw_label(draw, left + 14, y, "LED", app.font, DIM)
        draw_label(draw, left + 44, y, "ARMED" if status.led_enabled else "DARK", app.font, AUX if status.led_enabled else DIM)

        if not status.whisplay_available:
            y += 24
            draw_label(draw, left, y, "WHISPLAY HARDWARE REQUIRED", app.font, WARN)

        draw_label(draw, left, bottom - 14, "+/- VOL  M MUTE  L LED  ESC BACK", app.font, DIM)

    # ── Input Handling ─────────────────────────────────────────

    def on_button(self, button: str, long_press: bool) -> bool:
        if self.detail_active is not None:
            return self._on_detail_button(button, long_press)

        if button == "X" and long_press:
            self.context.app.set_screen("home")
            return True
        if button == "X":
            self.context.app.set_screen("home")
            return True
        if button == "Y":
            if long_press:
                self.context.app.set_screen("term")
                return True
            # Open first detail panel
            self.detail_active = "core"
            self.detail_scroll = 0
            return True
        if button == "A":
            return True
        if button == "B":
            return True
        return False

    def _on_detail_button(self, button: str, long_press: bool) -> bool:
        if self.detail_active == "wireless":
            return self._on_wireless_detail_button(button, long_press)
        if self.detail_active == "cues":
            return self._on_cues_detail_button(button, long_press)
        if button == "Y":
            self._leave_detail()
            return True
        return False

    def _on_wireless_detail_button(self, button: str, long_press: bool) -> bool:
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
        if button == "Y":
            self._leave_detail()
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
        return False

    def _on_cues_detail_button(self, button: str, long_press: bool) -> bool:
        app = self.context.app
        status = app.accents.status
        if button == "Y":
            self._leave_detail()
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
        return False

    def on_keyboard_event(self, event: KeyboardEvent) -> bool:
        if self.detail_active is not None:
            return self._on_detail_key(event)
        # Overview mode: panel expansion keys
        panel = self._PANEL_KEYS.get(event.key)
        if panel is not None:
            self.detail_active = panel
            self.detail_scroll = 0
            if panel == "wireless":
                self._enter_wifi_config(force_scan=True)
            return True
        return False

    def _on_detail_key(self, event: KeyboardEvent) -> bool:
        if self.detail_active == "wireless":
            return self._on_wireless_detail_key(event)
        if self.detail_active == "cues":
            return self._on_cues_detail_key(event)
        if event.key == "escape":
            self._leave_detail()
            return True
        return False

    def _on_wireless_detail_key(self, event: KeyboardEvent) -> bool:
        if self.entering_password:
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
        if event.key == "escape":
            self._leave_detail()
            return True
        if event.key in {"up", "k"}:
            self._select_wifi_network(-1)
            return True
        if event.key in {"down", "j"}:
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

    def _on_cues_detail_key(self, event: KeyboardEvent) -> bool:
        app = self.context.app
        status = app.accents.status
        if event.key == "escape":
            self._leave_detail()
            return True
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

    def _leave_detail(self) -> None:
        self.detail_active = None
        self.detail_scroll = 0
        self.password_target = None
        self.password_entry = ""
        self.status_line = "W wireless  C core  L link"
        self.invalidate_background()

    def get_button_hints(self) -> list[str]:
        if self.detail_active == "wireless":
            if self.entering_password:
                return ["A del", "B spc", "X cancel", "Y join"]
            return ["A prev", "B next", "X scan", "Y back"]
        if self.detail_active == "cues":
            return ["A vol-", "B vol+", "X mute", "Y back"]
        if self.detail_active is not None:
            return ["-", "-", "-", "Y back"]
        return ["-", "-", "X home", "Y detail"]

    # ── WiFi Helpers ───────────────────────────────────────────

    def _enter_wifi_config(self, *, force_scan: bool = False) -> None:
        selected_network = self._selected_network()
        selected_ssid = selected_network.ssid if selected_network is not None else None
        self.networks = self.context.app.wifi.scan(force=force_scan)
        self._sync_wifi_selection(preferred_ssid=selected_ssid, prefer_active=force_scan)
        self.status_line = self.context.app.wifi.last_message
        self._refresh_elapsed = 0.0

    def _select_wifi_network(self, delta: int) -> None:
        if not self.networks:
            self.status_line = "no wifi networks"
            return
        self.selected_index = (self.selected_index + delta) % len(self.networks)
        network = self.networks[self.selected_index]
        self.status_line = f"{self.selected_index + 1}/{len(self.networks)} selected {network.ssid}"

    def _connect_selected_network(self) -> None:
        self._sync_wifi_selection(prefer_active=False)
        if not self.networks:
            self.status_line = "no wifi networks"
            return
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

    def _selected_network(self) -> WifiNetwork | None:
        if not self.networks:
            return None
        if self.selected_index < 0 or self.selected_index >= len(self.networks):
            return None
        return self.networks[self.selected_index]

    def _sync_wifi_selection(self, *, preferred_ssid: str | None = None, prefer_active: bool = True) -> None:
        if not self.networks:
            self.selected_index = 0
            return
        if prefer_active:
            for index, network in enumerate(self.networks):
                if network.active:
                    self.selected_index = index
                    return
        if preferred_ssid:
            for index, network in enumerate(self.networks):
                if network.ssid == preferred_ssid:
                    self.selected_index = index
                    return
        self.selected_index = min(max(self.selected_index, 0), len(self.networks) - 1)

    # ── Drawing Helpers ────────────────────────────────────────

    @staticmethod
    def _trim(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return f"{text[: max(0, limit - 1)]}>"

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
