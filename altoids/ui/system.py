from __future__ import annotations

from PIL import ImageDraw

from ..buttons import LEFT_BOTTOM, LEFT_TOP, RIGHT_BOTTOM, RIGHT_TOP
from ..colors import ACCENT, AUX, BG, COOL, DIM, FG, INFO, SURFACE_ALT, SURFACE_GRID, SURFACE_PANEL, WARN
from ..input_keyboard import KeyboardEvent
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
        "w": "wifi",
        "r": "rig",
        "u": "cues",
    }

    def __init__(self, context: ScreenContext) -> None:
        super().__init__(context)
        self.status_line = "C core  W wifi  R rig"
        self._refresh_elapsed = 0.0
        self.detail_active: str | None = None
        self.detail_scroll = 0
        # WiFi detail state
        self.wifi_state = "roster"
        self.wifi_selection = 0
        self.wifi_networks: list = []
        self.wifi_password = ""
        self.wifi_password_visible = False

    def update(self, dt: float) -> bool:
        dirty = False
        if self.detail_active == "wifi" and self.wifi_state == "connecting":
            result = self.context.app.wifi.poll_connect()
            if result.state in {"success", "failed"}:
                self.wifi_state = "result"
                self._wifi_result_message = result.message
                self._wifi_result_success = result.state == "success"
                cue = "wifi_success" if self._wifi_result_success else "wifi_error"
                self.context.app.accents.trigger(cue)
                dirty = True
            else:
                dirty = True  # animate dots
        self._refresh_elapsed += dt
        if self._refresh_elapsed < 1.0:
            return dirty
        self._refresh_elapsed = 0.0
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
        accent_status = app.accents.status
        width = app.config.display.width
        height = app.config.display.height
        content_bottom = height - 8
        layout = self._overview_layout(width, app.side_bar_width)
        signature = ("system_unified", width, height, app.side_bar_width)
        buffer.paste(self.cached_background(signature, buffer.size, self._paint_overview_background))
        draw = ImageDraw.Draw(buffer)

        ip_addr = str(stats.get("ip_address", "offline"))

        temp_color = WARN if stats["temperature_hot"] else ACCENT
        core_bounds = layout["core_bounds"]
        load_bounds = layout["load_bounds"]
        link_bounds = layout["link_bounds"]
        wifi_bounds = layout["wifi_bounds"]
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
        link_limit = max(10, (link_bounds[2] - link_bounds[0] - 12) // 7)
        draw_status_dot(draw, link_bounds[0] + 12, 108, app.bluetooth_status.connected, COOL)
        draw_label(draw, link_bounds[0] + 24, 106, "BT", app.font, DIM)
        draw_label(draw, link_bounds[0] + 46, 106, "LIVE" if app.bluetooth_status.connected else "IDLE", app.font, COOL if app.bluetooth_status.connected else DIM)
        draw_label(draw, link_bounds[0] + 12, 124, self._trim(f"IP {ip_addr}", link_limit), app.font, FG)
        draw_label(draw, link_bounds[0] + 12, 138, self._trim(f"DSK {stats['disk_label']}", link_limit), app.font, DIM)

        # ── WIFI panel content (foreground, right middle) ──
        wifi_status = app.wifi.status(allow_refresh=False)
        wifi_left = wifi_bounds[0] + 16
        wifi_limit = max(10, (wifi_bounds[2] - wifi_left - 8) // 7)
        draw_status_dot(draw, wifi_left, 108, wifi_status.connected, INFO)
        draw_label(draw, wifi_left + 14, 106, "WIFI", app.font, DIM)
        if wifi_status.connected:
            draw_label(draw, wifi_left, 124, self._trim(wifi_status.ssid, wifi_limit), app.font, FG)
            bar_segs = 5
            draw_segmented_bar(draw, wifi_left, 140, 50, wifi_status.signal / 100.0, segments=bar_segs, color=INFO)
            draw_label(draw, wifi_left + 56, 138, f"{wifi_status.signal}%", app.font, DIM)
        else:
            draw_label(draw, wifi_left, 124, "OFFLINE", app.font, DIM)
            net_count = len(self.wifi_networks)
            if net_count:
                draw_label(draw, wifi_left, 138, f"{net_count} FOUND", app.font, DIM)

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
        status_y = height - 18
        draw_label(draw, layout["status_left"], status_y, self._trim(self.status_line.upper(), max(20, (layout["status_width"]) // 7)), app.font, DIM)

    def _paint_overview_background(self, draw: ImageDraw.ImageDraw, buffer) -> None:
        app = self.context.app
        width = app.config.display.width
        height = app.config.display.height
        # Header
        draw_label(draw, 12, 8, "SYSTEM // MAGI-03", app.font, ACCENT)
        draw_label(draw, width - 68, 8, "VFD DIAG", app.font, DIM)
        draw_separator(draw, 20, width)

        layout = self._overview_layout(width, app.side_bar_width)
        core_bounds = layout["core_bounds"]
        load_bounds = layout["load_bounds"]
        link_bounds = layout["link_bounds"]
        wifi_bounds = layout["wifi_bounds"]
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
        draw_panel(draw, wifi_bounds, title="WIFI", title_font=app.font, outline=INFO, title_color=INFO)
        draw_panel(draw, cues_bounds, title="CUES", title_font=app.font, outline=ACCENT, title_color=ACCENT)

    @staticmethod
    def _overview_layout(width: int, side_bar_width: int) -> dict[str, tuple[int, int, int, int] | int]:
        content_left = side_bar_width + 2
        content_right = width - side_bar_width - 2
        total = content_right - content_left
        col_split = content_left + total * 35 // 100
        core_bounds = (content_left, 28, col_split, 90)
        load_bounds = (col_split - 4, 28, content_right, 90)
        mid_split = content_left + total * 35 // 100
        link_bounds = (content_left, 96, mid_split, 156)
        wifi_bounds = (mid_split - 4, 96, content_right, 156)
        rig_split = content_left + total * 45 // 100
        rig_bounds = (content_left, 160, rig_split, 198)
        cues_bounds = (rig_split + 2, 160, content_right, 198)
        return {
            "core_bounds": core_bounds,
            "load_bounds": load_bounds,
            "link_bounds": link_bounds,
            "wifi_bounds": wifi_bounds,
            "rig_bounds": rig_bounds,
            "cues_bounds": cues_bounds,
            "status_left": content_left + 4,
            "status_width": max(1, content_right - content_left - 8),
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
        elif panel == "wifi":
            self._render_wifi_detail(draw, width, height)
        elif panel == "rig":
            self._render_rig_detail(draw, width, height)
        elif panel == "cues":
            self._render_cues_detail(draw, width, height)

    def _render_core_detail(self, draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
        app = self.context.app
        stats = app.system_snapshot()
        bounds = draw_detail_frame(draw, width, height, title="CORE", font=app.font, color=ACCENT, side_bar_width=app.side_bar_width)
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
        bounds = draw_detail_frame(draw, width, height, title="LOAD", font=app.font, color=WARN, side_bar_width=app.side_bar_width)
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
        ip_addr = str(stats.get("ip_address", "offline"))
        bounds = draw_detail_frame(draw, width, height, title="LINK", font=app.font, color=INFO, side_bar_width=app.side_bar_width)
        left, top, right, bottom = bounds

        draw_label(draw, left, top, "CONNECTIVITY", app.font, INFO)
        y = top + 22
        draw_status_dot(draw, left, y + 1, app.bluetooth_status.connected, COOL)
        draw_label(draw, left + 14, y, "BT", app.font, DIM)
        draw_label(draw, left + 42, y, "LIVE" if app.bluetooth_status.connected else "IDLE", app.font, COOL if app.bluetooth_status.connected else DIM)
        y += 22
        draw_label(draw, left, y, f"IP      {ip_addr}", app.font, FG)
        y += 16
        draw_label(draw, left, y, f"DISK    {stats['disk_label']}", app.font, FG)

        draw_label(draw, left, bottom - 14, "ESC BACK", app.font, DIM)

    def _render_rig_detail(self, draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
        app = self.context.app
        status = app.accents.status
        bounds = draw_detail_frame(draw, width, height, title="RIG", font=app.font, color=AUX, side_bar_width=app.side_bar_width)
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
        bounds = draw_detail_frame(draw, width, height, title="CUES", font=app.font, color=ACCENT, side_bar_width=app.side_bar_width)
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

    def _render_wifi_detail(self, draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
        app = self.context.app
        bounds = draw_detail_frame(draw, width, height, title="WIFI", font=app.font, color=INFO, side_bar_width=app.side_bar_width)
        left, top, right, bottom = bounds
        if self.wifi_state == "roster":
            self._render_wifi_roster(draw, left, top, right, bottom)
        elif self.wifi_state == "password":
            self._render_wifi_password(draw, left, top, right, bottom)
        elif self.wifi_state == "connecting":
            self._render_wifi_connecting(draw, left, top, right, bottom)
        elif self.wifi_state == "result":
            self._render_wifi_result(draw, left, top, right, bottom)

    def _render_wifi_roster(self, draw: ImageDraw.ImageDraw, left: int, top: int, right: int, bottom: int) -> None:
        app = self.context.app
        networks = self.wifi_networks
        count = len(networks)
        draw_label(draw, left, top, f"NETWORKS ({count})", app.font, INFO)
        visible = 7
        row_h = 18
        list_top = top + 20
        max_ssid = max(1, (right - left - 80) // 7)
        if count == 0:
            draw_label(draw, left, list_top + 10, "NO NETWORKS FOUND", app.font, DIM)
            draw_label(draw, left, list_top + 28, "R TO RESCAN", app.font, DIM)
        else:
            scroll = max(0, min(self.wifi_selection - visible // 2, count - visible))
            for i in range(visible):
                idx = scroll + i
                if idx >= count:
                    break
                net = networks[idx]
                y = list_top + i * row_h
                selected = idx == self.wifi_selection
                if selected:
                    draw.rectangle((left - 2, y - 1, right + 2, y + row_h - 3), outline=INFO)
                # Signal bar (4 segments)
                sig_pct = net.signal / 100.0
                draw_segmented_bar(draw, left, y + 2, 28, sig_pct, segments=4, color=INFO)
                # SSID
                ssid_display = self._trim(net.ssid, max_ssid)
                draw_label(draw, left + 34, y, ssid_display, app.font, FG if selected else DIM)
                # Known indicator
                if net.known:
                    draw_status_dot(draw, right - 30, y + 3, True, ACCENT)
                # Lock indicator for secured networks
                if not net.open:
                    draw_label(draw, right - 16, y, "L", app.font, DIM)
            # Scroll arrows
            if scroll > 0:
                draw_label(draw, right - 8, list_top - 2, "^", app.font, DIM)
            if scroll + visible < count:
                draw_label(draw, right - 8, list_top + visible * row_h - 4, "v", app.font, DIM)

        draw_label(draw, left, bottom - 14, "UP/DN SEL  ENTER JOIN  R SCAN  ESC", app.font, DIM)

    def _render_wifi_password(self, draw: ImageDraw.ImageDraw, left: int, top: int, right: int, bottom: int) -> None:
        app = self.context.app
        net = self.wifi_networks[self.wifi_selection] if self.wifi_selection < len(self.wifi_networks) else None
        ssid = net.ssid if net else "?"
        draw_label(draw, left, top, "AUTHENTICATE", app.font, INFO)
        draw_label(draw, left, top + 22, self._trim(ssid, 24), app.font, FG)
        # Password field
        y = top + 48
        if self.wifi_password_visible:
            display = self.wifi_password
        else:
            display = "*" * len(self.wifi_password)
        field_width = right - left
        draw.rectangle((left - 2, y - 2, left + field_width + 2, y + 16), outline=INFO)
        draw_label(draw, left + 2, y + 1, self._trim(display or " ", max(1, field_width // 7 - 1)), app.font, FG)
        draw_label(draw, left, y + 22, f"{len(self.wifi_password)} CHARS", app.font, DIM)
        vis_label = "VISIBLE" if self.wifi_password_visible else "HIDDEN"
        draw_label(draw, left + 80, y + 22, vis_label, app.font, DIM)

        draw_label(draw, left, bottom - 14, "TAB VIS  ENTER OK  ESC CANCEL", app.font, DIM)

    def _render_wifi_connecting(self, draw: ImageDraw.ImageDraw, left: int, top: int, right: int, bottom: int) -> None:
        import time as _time
        app = self.context.app
        dots = "." * (int(_time.monotonic() * 3) % 4)
        net = self.wifi_networks[self.wifi_selection] if self.wifi_selection < len(self.wifi_networks) else None
        ssid = net.ssid if net else "?"
        draw_label(draw, left, top, f"CONNECTING{dots}", app.font, INFO)
        draw_label(draw, left, top + 24, self._trim(ssid, 24), app.font, FG)

        draw_label(draw, left, bottom - 14, "ESC CANCEL", app.font, DIM)

    def _render_wifi_result(self, draw: ImageDraw.ImageDraw, left: int, top: int, right: int, bottom: int) -> None:
        app = self.context.app
        success = getattr(self, "_wifi_result_success", False)
        message = getattr(self, "_wifi_result_message", "")
        net = self.wifi_networks[self.wifi_selection] if self.wifi_selection < len(self.wifi_networks) else None
        ssid = net.ssid if net else "?"
        if success:
            draw_label(draw, left, top, "CONNECTED", app.font, ACCENT)
            draw_status_dot(draw, left, top + 22, True, ACCENT)
            draw_label(draw, left + 14, top + 20, self._trim(ssid, 20), app.font, FG)
            if message:
                draw_label(draw, left, top + 42, self._trim(f"IP {message}", 28), app.font, FG)
        else:
            draw_label(draw, left, top, "FAILED", app.font, WARN)
            draw_status_dot(draw, left, top + 22, False, WARN)
            draw_label(draw, left + 14, top + 20, self._trim(ssid, 20), app.font, FG)
            if message:
                draw_label(draw, left, top + 42, self._trim(message.upper(), 28), app.font, WARN)

        draw_label(draw, left, bottom - 14, "ANY KEY TO CONTINUE", app.font, DIM)

    # ── Input Handling ─────────────────────────────────────────

    def on_button(self, slot: str, long_press: bool) -> bool:
        if self.detail_active is not None:
            return self._on_detail_button(slot, long_press)

        if slot == RIGHT_TOP and long_press:
            self.context.app.set_screen("home")
            return True
        if slot == RIGHT_TOP:
            self.context.app.set_screen("home")
            return True
        if slot == RIGHT_BOTTOM:
            if long_press:
                self.context.app.set_screen("term")
                return True
            # Open first detail panel
            self.detail_active = "core"
            self.detail_scroll = 0
            return True
        if slot == LEFT_TOP:
            return True
        if slot == LEFT_BOTTOM:
            return True
        return False

    def _on_detail_button(self, slot: str, long_press: bool) -> bool:
        if self.detail_active == "cues":
            return self._on_cues_detail_button(slot, long_press)
        if self.detail_active == "wifi":
            return self._on_wifi_detail_button(slot, long_press)
        if slot == RIGHT_BOTTOM:
            self._leave_detail()
            return True
        return False

    def _on_cues_detail_button(self, slot: str, long_press: bool) -> bool:
        app = self.context.app
        status = app.accents.status
        if slot == RIGHT_BOTTOM:
            self._leave_detail()
            return True
        if not status.whisplay_available:
            self.status_line = "whisplay hardware required"
            app.accents.trigger("error")
            return True
        if slot == LEFT_TOP:
            if not status.audio_available:
                self.status_line = "speaker unavailable"
                app.accents.trigger("error")
                return True
            app.accents.adjust_volume(-10)
            self.status_line = f"volume {app.accents.status.volume_percent}%"
            return True
        if slot == LEFT_BOTTOM:
            if not status.audio_available:
                self.status_line = "speaker unavailable"
                app.accents.trigger("error")
                return True
            app.accents.adjust_volume(10)
            self.status_line = f"volume {app.accents.status.volume_percent}%"
            return True
        if slot == RIGHT_TOP:
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
            if panel == "wifi":
                self._open_wifi_detail()
            return True
        return False

    def _on_detail_key(self, event: KeyboardEvent) -> bool:
        if self.detail_active == "cues":
            return self._on_cues_detail_key(event)
        if self.detail_active == "wifi":
            return self._on_wifi_detail_key(event)
        if event.key == "escape":
            self._leave_detail()
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

    def _open_wifi_detail(self) -> None:
        self.wifi_state = "roster"
        self.wifi_selection = 0
        self.wifi_password = ""
        self.wifi_password_visible = False
        app = self.context.app
        self.wifi_networks = app.wifi.scan(force=True)

    def _on_wifi_detail_button(self, slot: str, long_press: bool) -> bool:
        if self.wifi_state == "roster":
            if slot == LEFT_TOP:
                self._wifi_move_selection(-1)
                return True
            if slot == LEFT_BOTTOM:
                self._wifi_move_selection(1)
                return True
            if slot == RIGHT_TOP:
                self._wifi_activate_selected()
                return True
            if slot == RIGHT_BOTTOM:
                self._leave_detail()
                return True
        elif self.wifi_state == "password":
            if slot == LEFT_TOP:
                self.wifi_password = self.wifi_password[:-1]
                return True
            if slot == LEFT_BOTTOM:
                self.wifi_password += " "
                return True
            if slot == RIGHT_TOP:
                self._wifi_submit_password()
                return True
            if slot == RIGHT_BOTTOM:
                self.wifi_state = "roster"
                self.wifi_password = ""
                return True
        elif self.wifi_state == "connecting":
            if slot == RIGHT_BOTTOM:
                self.wifi_state = "roster"
                self.context.app.wifi.reset_connect()
                return True
        elif self.wifi_state == "result":
            self.wifi_state = "roster"
            self.context.app.wifi.reset_connect()
            return True
        return False

    def _on_wifi_detail_key(self, event: KeyboardEvent) -> bool:
        if self.wifi_state == "roster":
            return self._on_wifi_roster_key(event)
        if self.wifi_state == "password":
            return self._on_wifi_password_key(event)
        if self.wifi_state == "connecting":
            if event.key == "escape":
                self.wifi_state = "roster"
                self.context.app.wifi.reset_connect()
                return True
            return True
        if self.wifi_state == "result":
            self.wifi_state = "roster"
            self.context.app.wifi.reset_connect()
            return True
        return False

    def _on_wifi_roster_key(self, event: KeyboardEvent) -> bool:
        if event.key == "escape":
            self._leave_detail()
            return True
        if event.key == "up":
            self._wifi_move_selection(-1)
            return True
        if event.key == "down":
            self._wifi_move_selection(1)
            return True
        if event.key == "enter":
            self._wifi_activate_selected()
            return True
        if event.key == "r":
            self.wifi_networks = self.context.app.wifi.scan(force=True)
            self.wifi_selection = 0
            return True
        return False

    def _on_wifi_password_key(self, event: KeyboardEvent) -> bool:
        if event.key == "escape":
            self.wifi_state = "roster"
            self.wifi_password = ""
            return True
        if event.key == "tab":
            self.wifi_password_visible = not self.wifi_password_visible
            return True
        if event.key == "enter":
            self._wifi_submit_password()
            return True
        if event.key == "backspace":
            self.wifi_password = self.wifi_password[:-1]
            return True
        if event.text and not event.ctrl and not event.alt:
            self.wifi_password += event.text
            return True
        return True  # consume all keys in password mode

    def _wifi_move_selection(self, delta: int) -> None:
        count = len(self.wifi_networks)
        if count == 0:
            return
        self.wifi_selection = max(0, min(count - 1, self.wifi_selection + delta))

    def _wifi_activate_selected(self) -> None:
        if not self.wifi_networks:
            return
        if self.wifi_selection >= len(self.wifi_networks):
            return
        net = self.wifi_networks[self.wifi_selection]
        if net.known or net.open:
            self.wifi_state = "connecting"
            self.context.app.wifi.connect_async(net.ssid)
        else:
            self.wifi_state = "password"
            self.wifi_password = ""
            self.wifi_password_visible = False

    def _wifi_submit_password(self) -> None:
        if not self.wifi_password:
            return
        if self.wifi_selection >= len(self.wifi_networks):
            return
        net = self.wifi_networks[self.wifi_selection]
        self.wifi_state = "connecting"
        self.context.app.wifi.connect_async(net.ssid, self.wifi_password)

    def _leave_detail(self) -> None:
        self.detail_active = None
        self.detail_scroll = 0
        self.wifi_state = "roster"
        self.wifi_selection = 0
        self.wifi_password = ""
        self.wifi_password_visible = False
        self.status_line = "C core  W wifi  R rig"
        self.invalidate_background()

    def get_button_hints(self) -> dict[str, str]:
        if self.detail_active == "cues":
            return {
                LEFT_TOP: "vol-",
                LEFT_BOTTOM: "vol+",
                RIGHT_TOP: "mute",
                RIGHT_BOTTOM: "back",
            }
        if self.detail_active == "wifi":
            if self.wifi_state == "roster":
                return {LEFT_TOP: "up", LEFT_BOTTOM: "down", RIGHT_TOP: "join", RIGHT_BOTTOM: "back"}
            if self.wifi_state == "password":
                return {LEFT_TOP: "del", LEFT_BOTTOM: "spc", RIGHT_TOP: "ok", RIGHT_BOTTOM: "cancel"}
            if self.wifi_state == "connecting":
                return {LEFT_TOP: "-", LEFT_BOTTOM: "-", RIGHT_TOP: "-", RIGHT_BOTTOM: "cancel"}
            if self.wifi_state == "result":
                return {LEFT_TOP: "-", LEFT_BOTTOM: "-", RIGHT_TOP: "-", RIGHT_BOTTOM: "ok"}
        if self.detail_active is not None:
            return {
                LEFT_TOP: "-",
                LEFT_BOTTOM: "-",
                RIGHT_TOP: "-",
                RIGHT_BOTTOM: "back",
            }
        return {
            LEFT_TOP: "-",
            LEFT_BOTTOM: "-",
            RIGHT_TOP: "home",
            RIGHT_BOTTOM: "detail",
        }

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
