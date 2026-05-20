from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import http.client
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import socket
import ssl
import threading
import time
from typing import Any

from PIL import ImageDraw

from ..colors import ACCENT, AUX, BG, COOL, DANGER, DIM, FG, INFO, SURFACE_ALT, SURFACE_GRID, SURFACE_INSET, WARN
from ..input_keyboard import KeyboardEvent
from .base import Screen, ScreenContext
from .widgets import draw_label, draw_panel, draw_scanlines, draw_segmented_bar, draw_status_dot


STATE_IDLE = "IDLE"
STATE_SURVEYING = "SURVEYING"
STATE_ANALYZING = "ANALYZING"
STATE_REQUEST = "REQUEST"
STATE_READY = "READY"
STATE_ERROR = "ERROR"

PAGES = ("AGENT", "MAP", "INBOX", "TARGETS", "ACTIONS", "TIMELINE", "SIGNAL")
ACTION_ITEMS = (
    ("QUICK SWEEP", "Run the baseline Network Field Kit mission."),
    ("DEEP SWEEP", "Request or run the wider local port sweep for known hosts."),
    ("CHECK ROUTER", "Inspect the inferred gateway with common web/service checks."),
    ("COMPARE LAST", "Compare the current snapshot against persisted memory."),
    ("EXPORT REPORT", "Rewrite the latest Markdown report for this network."),
)
BASELINE_PORTS = (22, 80, 443)
DEEP_PORTS = (21, 22, 25, 53, 80, 110, 143, 443, 445, 993, 995, 1883, 3000, 3306, 5000, 5432, 6379, 8000, 8080, 8443, 9000, 9200)
SECURITY_HEADERS = (
    "strict-transport-security",
    "content-security-policy",
    "x-frame-options",
    "x-content-type-options",
    "referrer-policy",
)


@dataclass(slots=True)
class FeedItem:
    id: str
    kind: str
    summary: str
    detail: str = ""
    icon: str = "[ ]"
    color: str = "normal"
    created_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class PendingRequest:
    id: str
    summary: str
    detail: str
    action: str


@dataclass(slots=True)
class HostSnapshot:
    host: str
    role: str = "host"
    ports: dict[str, bool] = field(default_factory=dict)
    http: dict[str, object] = field(default_factory=dict)
    tls: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class NetworkSnapshot:
    network_id: str = "unknown"
    ssid: str = ""
    local_ip: str = "offline"
    gateway: str = ""
    signal: int = 0
    hosts: list[HostSnapshot] = field(default_factory=list)
    started_at: float = 0.0
    completed_at: float = 0.0


class TinScopeScreen(Screen):
    name = "tinscope"

    def __init__(self, context: ScreenContext) -> None:
        super().__init__(context)
        self.state = STATE_IDLE
        self.status_line = "ENTER START"
        self.command_line = "NETWORK FIELD KIT"
        self.progress = 0.0
        self.feed: list[FeedItem] = []
        self.selected_index = 0
        self.feed_top = 0
        self.page_index = 0
        self.page_selection: dict[str, int] = {}
        self.reader_open = False
        self.reader_scroll = 0
        self.pending_request: PendingRequest | None = None
        self.snapshot = NetworkSnapshot()
        self.previous_snapshot: dict[str, Any] = {}
        self.report_path: Path | None = None
        self._worker: threading.Thread | None = None
        self._blink = 0.0
        self._next_item_id = 1
        self._state_dir = Path(os.environ.get("TINSCOPE_STATE_DIR", ".runtime/tinscope"))
        self._load_state()

    def update(self, dt: float) -> bool:
        self._blink = (self._blink + dt) % 1.0
        return self.state in {STATE_SURVEYING, STATE_ANALYZING, STATE_REQUEST} or self._blink < dt

    def render(self, draw: ImageDraw.ImageDraw, buffer) -> None:
        app = self.context.app
        width = app.config.display.width
        height = app.config.display.height
        footer_height = 24 if app.shows_button_bar else 0
        content_bottom = height - footer_height - 8
        try:
            signature = (width, height, footer_height)
            buffer.paste(self.cached_background(signature, buffer.size, self._paint_static_background))
            draw = ImageDraw.Draw(buffer)

            self._draw_status(draw, width)
            self._draw_page(draw, width, content_bottom)
            if self.reader_open:
                self._draw_reader(draw, width, height)
        except Exception as exc:  # pragma: no cover - defensive UI boundary
            self._record_action_error("TinScope render failed", exc)
            self._draw_failure_frame(ImageDraw.Draw(buffer), width, height)

    def _paint_static_background(self, draw: ImageDraw.ImageDraw, buffer) -> None:
        app = self.context.app
        width = app.config.display.width
        height = app.config.display.height
        footer_height = 24 if app.shows_button_bar else 0
        content_bottom = height - footer_height - 8

        draw_label(draw, 12, 8, "TINSCOPE // COMMAND OPERATOR", app.font, ACCENT)
        draw_label(draw, width - 64, 8, "VFD-IO", app.font, WARN)
        draw.line((12, 22, width - 12, 22), fill=SURFACE_INSET, width=1)
        draw_panel(draw, (12, 30, width - 12, 104), title="AGENT", title_font=app.font, outline=ACCENT, title_color=ACCENT, fill=SURFACE_ALT)
        draw_scanlines(draw, (12, 30, width - 12, 104), step=6, color=SURFACE_GRID)
        draw_panel(draw, (12, 112, width - 12, content_bottom), title="OPERATOR", title_font=app.font, outline=AUX, title_color=AUX)

    def _draw_status(self, draw: ImageDraw.ImageDraw, width: int) -> None:
        app = self.context.app
        color = self._state_color()
        pulse = self.state in {STATE_SURVEYING, STATE_ANALYZING, STATE_REQUEST} and self._blink < 0.55
        draw_status_dot(draw, 24, 50, self.state != STATE_IDLE, color if pulse or self.state not in {STATE_SURVEYING, STATE_ANALYZING} else DIM)
        draw_label(draw, 42, 43, self.state, app.font_large, color)
        draw_label(draw, width - 86, 45, self._trim(f"{self.page_name} {self.page_index + 1}/{len(PAGES)}", 12), app.font, DIM)
        draw_label(draw, 24, 70, self._trim(self.command_line.upper(), 31), app.font, FG)
        draw_segmented_bar(draw, 24, 90, width - 48, self.progress, segments=18, color=color)

    @property
    def page_name(self) -> str:
        return PAGES[self.page_index]

    def _draw_page(self, draw: ImageDraw.ImageDraw, width: int, content_bottom: int) -> None:
        if self.page_name in {"AGENT", "INBOX"}:
            self._draw_feed(draw, width, content_bottom)
        elif self.page_name == "MAP":
            self._draw_map(draw, width, content_bottom)
        elif self.page_name == "TARGETS":
            self._draw_targets(draw, width, content_bottom)
        elif self.page_name == "ACTIONS":
            self._draw_actions(draw, width, content_bottom)
        elif self.page_name == "TIMELINE":
            self._draw_timeline(draw, width, content_bottom)
        elif self.page_name == "SIGNAL":
            self._draw_signal(draw, width, content_bottom)

    def _draw_feed(self, draw: ImageDraw.ImageDraw, width: int, content_bottom: int) -> None:
        app = self.context.app
        visible_rows = max(1, (content_bottom - 132) // 18)
        self._clamp_selection(visible_rows)
        if not self.feed:
            draw_label(draw, 24, 134, "NO EVENTS. ENTER START.", app.font, DIM)
        else:
            for row, item in enumerate(self.feed[self.feed_top : self.feed_top + visible_rows]):
                index = self.feed_top + row
                y = 132 + row * 18
                selected = index == self.selected_index
                color = self._item_color(item)
                if selected:
                    draw.rectangle((20, y - 2, width - 20, y + 14), outline=color, fill=BG)
                draw_label(draw, 26, y, item.icon, app.font, color)
                draw_label(draw, 52, y, self._trim(item.summary.upper(), 28), app.font, FG if selected else DIM)
        hint = self._footer_hint()
        draw_label(draw, 22, content_bottom - 15, self._trim(hint, 33), app.font, ACCENT if self.state != STATE_ERROR else WARN)

    def _draw_map(self, draw: ImageDraw.ImageDraw, width: int, content_bottom: int) -> None:
        app = self.context.app
        lines = self._map_lines()
        for index, line in enumerate(lines[:7]):
            draw_label(draw, 24, 132 + index * 13, self._trim(line, 33), app.terminal_font, ACCENT if index == 0 else FG if "[" in line else DIM)
        draw_label(draw, 22, content_bottom - 15, "L/R PAGE  ENTER DETAIL", app.font, ACCENT)

    def _draw_targets(self, draw: ImageDraw.ImageDraw, width: int, content_bottom: int) -> None:
        app = self.context.app
        hosts = self.snapshot.hosts
        selected = self._page_selected("TARGETS", len(hosts))
        if not hosts:
            draw_label(draw, 24, 134, "NO TARGETS YET. ENTER RUN.", app.font, DIM)
        for index, host in enumerate(hosts[:5]):
            y = 132 + index * 18
            active = index == selected
            color = WARN if any(host.ports.values()) else DIM
            if active:
                draw.rectangle((20, y - 2, width - 20, y + 14), outline=color, fill=BG)
            open_ports = ",".join(port for port, is_open in host.ports.items() if is_open) or "-"
            draw_label(draw, 26, y, ">" if active else " ", app.font, color)
            draw_label(draw, 42, y, self._trim(f"{host.role.upper()} {host.host}", 18), app.font, FG if active else DIM)
            draw_label(draw, 182, y, self._trim(open_ports, 9), app.font, color)
        draw_label(draw, 22, content_bottom - 15, "UP/DN TARGET  ENTER DETAIL", app.font, ACCENT)

    def _draw_actions(self, draw: ImageDraw.ImageDraw, width: int, content_bottom: int) -> None:
        app = self.context.app
        selected = self._page_selected("ACTIONS", len(ACTION_ITEMS))
        for index, (title, _detail) in enumerate(ACTION_ITEMS):
            y = 130 + index * 18
            active = index == selected
            color = ACCENT if active else DIM
            if active:
                draw.rectangle((20, y - 2, width - 20, y + 14), outline=color, fill=BG)
            draw_label(draw, 28, y, ">" if active else " ", app.font, color)
            draw_label(draw, 44, y, title, app.font, FG if active else DIM)
        draw_label(draw, 22, content_bottom - 15, "UP/DN ACTION  ENTER RUN", app.font, ACCENT)

    def _draw_timeline(self, draw: ImageDraw.ImageDraw, width: int, content_bottom: int) -> None:
        app = self.context.app
        events = self._timeline_entries(limit=6)
        if not events:
            draw_label(draw, 24, 134, "NO TIMELINE EVENTS.", app.font, DIM)
        for index, line in enumerate(events):
            y = 132 + index * 16
            draw_label(draw, 24, y, self._trim(line, 32), app.font, FG if index == 0 else DIM)
        draw_label(draw, 22, content_bottom - 15, "TIMELINE MEMORY", app.font, ACCENT)

    def _draw_signal(self, draw: ImageDraw.ImageDraw, width: int, content_bottom: int) -> None:
        app = self.context.app
        rows = [
            ("NET", self.snapshot.network_id),
            ("SSID", self.snapshot.ssid or "unknown"),
            ("RSSI", f"{self.snapshot.signal}%"),
            ("IP", self.snapshot.local_ip),
            ("GW", self.snapshot.gateway or "unknown"),
        ]
        for index, (label, value) in enumerate(rows):
            y = 130 + index * 18
            draw_label(draw, 28, y, label, app.font, DIM)
            draw_label(draw, 74, y, self._trim(value, 22), app.font, FG)
        draw_segmented_bar(draw, 74, 166, 92, max(0.0, min(1.0, self.snapshot.signal / 100.0)), segments=10, color=INFO)
        draw_label(draw, 22, content_bottom - 15, "SIGNAL INSTRUMENT", app.font, ACCENT)

    def _draw_reader(self, draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
        app = self.context.app
        bounds = (8, 18, width - 8, height - 30)
        left, top, right, bottom = bounds
        draw.rounded_rectangle(bounds, radius=8, outline=ACCENT, fill=SURFACE_ALT)
        draw.rounded_rectangle((left + 5, top + 5, right - 5, bottom - 5), radius=6, outline=SURFACE_INSET, fill=None)
        title = f" ITEM {self.selected_index + 1}/{max(1, len(self.feed))} "
        draw.rectangle((left + 12, top - 1, left + 94, top + 11), fill=SURFACE_ALT)
        draw_label(draw, left + 14, top + 1, title, app.font, ACCENT)
        lines = self._reader_lines(33)
        content_height = max(1, (bottom - top - 24) // 13)
        max_scroll = max(0, len(lines) - content_height)
        self.reader_scroll = min(max(0, self.reader_scroll), max_scroll)
        for row, line in enumerate(lines[self.reader_scroll : self.reader_scroll + content_height]):
            draw_label(draw, left + 14, top + 20 + row * 13, self._trim(line, 33), app.font, FG if row == 0 and self.reader_scroll == 0 else DIM)

    def on_button(self, button: str, long_press: bool) -> bool:
        try:
            if self.reader_open:
                if button == "A":
                    self._move_reader_selection(-1)
                elif button == "B":
                    self._move_reader_selection(1)
                elif button in {"X", "Y"}:
                    self._close_reader()
                return True
            if button == "A":
                self._move_page_selection(-1)
                return True
            if button == "B":
                self._move_page_selection(1)
                return True
            if button == "X":
                self._handle_enter()
                return True
            if button == "Y":
                if long_press:
                    self.context.app.set_screen("home")
                else:
                    self._handle_space()
                return True
            return False
        except Exception as exc:  # pragma: no cover - defensive input boundary
            self._record_action_error("TinScope button failed", exc)
            return True

    def on_keyboard_event(self, event: KeyboardEvent) -> bool:
        try:
            if event.ctrl or event.alt:
                return False
            if self.reader_open:
                return self._handle_reader_key(event)
            if event.key in {"q", "escape"}:
                if self.pending_request is not None:
                    self._deny_request()
                else:
                    self.context.app.set_screen("home")
                return True
            if event.key == "enter":
                self._handle_enter()
                return True
            if event.key == " ":
                self._handle_space()
                return True
            if event.key in {"up", "down"}:
                self._move_page_selection(-1 if event.key == "up" else 1)
                return True
            if event.key in {"left", "right"}:
                self._cycle_page(-1 if event.key == "left" else 1)
                return True
            if event.key == "tab":
                self._open_reader()
                return True
            return False
        except Exception as exc:  # pragma: no cover - defensive input boundary
            self._record_action_error("TinScope key failed", exc)
            return True

    def get_button_hints(self) -> list[str]:
        if self.reader_open:
            return ["A prev", "B next", "X close", "Y close"]
        if self.pending_request is not None:
            return ["A up", "B down", "X yes", "Y info"]
        return ["A up", "B down", "X enter", "Y info"]

    def _handle_reader_key(self, event: KeyboardEvent) -> bool:
        if event.key in {"escape", "enter", "q"}:
            self._close_reader()
            return True
        if event.key == "up":
            self.reader_scroll = max(0, self.reader_scroll - 1)
            return True
        if event.key == "down":
            self.reader_scroll += 1
            return True
        if event.key == "left":
            self._move_reader_selection(-1)
            return True
        if event.key == "right":
            self._move_reader_selection(1)
            return True
        if event.key == "home":
            self.reader_scroll = 0
            return True
        if event.key == "end":
            self.reader_scroll = 9999
            return True
        return False

    def _handle_enter(self) -> None:
        if self.pending_request is not None:
            self._approve_request()
            return
        if self.page_name == "ACTIONS":
            self._run_selected_action()
            return
        if self.state in {STATE_IDLE, STATE_READY, STATE_ERROR} and not self.feed:
            self._start_baseline()
            return
        if self.state == STATE_IDLE:
            self._start_baseline()
            return
        if self.page_name == "TARGETS":
            self._inspect_selected_target()
            return
        if self.page_name in {"MAP", "SIGNAL", "TIMELINE"}:
            self._inspect_page()
            return
        self._open_reader()

    def _handle_space(self) -> None:
        if self.pending_request is not None:
            self._select_item_by_id(self.pending_request.id)
            self._open_reader()
            return
        if self.feed:
            self._open_reader()
        else:
            self.command_line = "ENTER STARTS NETWORK FIELD KIT"

    def _start_baseline(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            self.command_line = "MISSION ALREADY RUNNING"
            return
        self.state = STATE_SURVEYING
        self.progress = 0.05
        self.pending_request = None
        self.feed.clear()
        self.selected_index = 0
        self.feed_top = 0
        self.reader_open = False
        self.snapshot = NetworkSnapshot(started_at=time.time())
        self._add_item("mission", "Network field kit started", "Baseline survey started.", "[#]", "info", persist=False)
        self._save_state()
        self._worker = threading.Thread(target=self._run_baseline_mission, daemon=True)
        self._worker.start()

    def _run_baseline_mission(self) -> None:
        try:
            self.command_line = "READING LOCAL SIGNALS"
            context = self._network_context()
            self.snapshot.network_id = context["network_id"]
            self.snapshot.ssid = context["ssid"]
            self.snapshot.local_ip = context["local_ip"]
            self.snapshot.gateway = context["gateway"]
            self.snapshot.signal = context["signal"]
            self.previous_snapshot = self._load_latest_snapshot(self.snapshot.network_id)
            self._persist_event("mission_started", context)
            self.progress = 0.2
            self._add_item("signal", self._signal_summary(), json.dumps(context, indent=2), "[+]", "accent")

            self.command_line = "PROBING NEIGHBORHOOD"
            hosts = self._probe_hosts(BASELINE_PORTS, deep=False)
            self.snapshot.hosts = hosts
            self.progress = 0.65
            self._add_host_findings(hosts)

            self.state = STATE_ANALYZING
            self.command_line = "COMPARING MEMORY"
            self._add_diff_findings()
            self.progress = 0.82

            if hosts:
                request = PendingRequest(
                    id=self._make_id(),
                    summary="Approve deeper local sweep?",
                    detail="Runs a wider port set against the small host set already seen in this mission.",
                    action="deep_sweep",
                )
                self.pending_request = request
                self.state = STATE_REQUEST
                self.command_line = "REQUEST: DEEPER SWEEP?"
                self.progress = 0.86
                self._add_item("request", request.summary, request.detail, "[?]", "warn", item_id=request.id)
                self._save_all()
                return

            self._finish_mission("No local hosts answered baseline probes.")
        except Exception as exc:  # pragma: no cover - defensive runtime path
            self.state = STATE_ERROR
            self.command_line = "MISSION ERROR"
            self.status_line = str(exc)
            self._add_item("error", "Mission error", str(exc), "[!]", "danger")
            self._save_all()

    def _approve_request(self) -> None:
        request = self.pending_request
        if request is None:
            return
        self.pending_request = None
        self.state = STATE_ANALYZING
        self.command_line = "APPROVED: DEEPER SWEEP"
        self._add_item("approval", "Approved deeper sweep", request.detail, "[+]", "accent")
        self._save_state()
        if self._worker is not None and self._worker.is_alive():
            self.command_line = "WAITING FOR WORKER"
            return
        self._worker = threading.Thread(target=self._run_deep_sweep, daemon=True)
        self._worker.start()

    def _deny_request(self) -> None:
        request = self.pending_request
        self.pending_request = None
        self._add_item("approval", "Skipped deeper sweep", request.detail if request else "", "[-]", "dim")
        self._finish_mission("Report ready. Deeper sweep skipped.")

    def _run_deep_sweep(self) -> None:
        try:
            self.command_line = "RUNNING DEEPER SWEEP"
            self.progress = 0.9
            hosts = self._probe_hosts(DEEP_PORTS, deep=True)
            merged = {host.host: host for host in self.snapshot.hosts}
            for host in hosts:
                merged[host.host] = host
            self.snapshot.hosts = list(merged.values())
            self._add_host_findings(hosts, deep=True)
            self._finish_mission("Report ready. Deeper sweep complete.")
        except Exception as exc:  # pragma: no cover - defensive runtime path
            self._record_action_error("Deep sweep failed", exc)

    def _run_selected_action(self) -> None:
        try:
            selected = self._page_selected("ACTIONS", len(ACTION_ITEMS))
            action = ACTION_ITEMS[selected][0]
            if action == "QUICK SWEEP":
                self._start_baseline()
                return
            if action == "DEEP SWEEP":
                if not self.snapshot.hosts:
                    self._add_item("action", "Deep sweep needs targets", "Run QUICK SWEEP first so TinScope has a bounded host set.", "[!]", "warn")
                    return
                if self._worker is not None and self._worker.is_alive():
                    self.command_line = "MISSION ALREADY RUNNING"
                    return
                self.pending_request = None
                self.state = STATE_ANALYZING
                self._worker = threading.Thread(target=self._run_deep_sweep, daemon=True)
                self._worker.start()
                return
            if action == "CHECK ROUTER":
                self._check_router_action()
                return
            if action == "COMPARE LAST":
                self._add_diff_findings()
                self._save_state()
                return
            if action == "EXPORT REPORT":
                self.report_path = self._write_report()
                self._add_item("report", "Report exported", str(self.report_path), "[+]", "accent")
                self._save_state()
        except Exception as exc:  # pragma: no cover - defensive runtime path
            self._record_action_error("Action failed", exc)

    def _check_router_action(self) -> None:
        try:
            gateway = self.snapshot.gateway
            if not gateway:
                self._add_item("action", "No gateway inferred", "TinScope does not have a gateway address yet. Run QUICK SWEEP or check Signal.", "[!]", "warn")
                return
            host = HostSnapshot(
                host=gateway,
                role="gateway",
                ports={str(port): self._check_port(gateway, port) for port in BASELINE_PORTS},
            )
            if host.ports.get("80"):
                host.http["http"] = self._check_http(gateway, "http")
            if host.ports.get("443"):
                host.http["https"] = self._check_http(gateway, "https")
                host.tls = self._check_tls(gateway)
            hosts = {item.host: item for item in self.snapshot.hosts}
            hosts[gateway] = host
            self.snapshot.hosts = list(hosts.values())
            self._add_item("action", "Router check complete", self._host_detail(host), "[+]", "accent")
            self._save_all()
        except Exception as exc:  # pragma: no cover - defensive runtime path
            self._record_action_error("Router check failed", exc)

    def _record_action_error(self, summary: str, exc: Exception) -> None:
        self.state = STATE_ERROR
        self.command_line = summary
        self.status_line = exc.__class__.__name__
        try:
            self._add_item("error", summary, f"{exc.__class__.__name__}: {exc}", "[!]", "danger")
            self._save_state()
        except Exception:
            self.feed.append(
                FeedItem(
                    id=self._make_id(),
                    kind="error",
                    summary=summary,
                    detail=f"{exc.__class__.__name__}: {exc}",
                    icon="[!]",
                    color="danger",
                )
            )

    def _draw_failure_frame(self, draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
        app = self.context.app
        draw.rectangle((0, 0, width, height), fill=BG)
        draw.rounded_rectangle((10, 18, width - 10, height - 34), radius=8, outline=DANGER, fill=SURFACE_ALT)
        draw_label(draw, 22, 30, "TINSCOPE // ERROR", app.font, DANGER)
        draw_label(draw, 22, 58, self._trim(self.command_line.upper(), 30), app.font_large, WARN)
        draw_label(draw, 22, 88, self._trim(self.status_line, 32), app.font, FG)
        draw_label(draw, 22, height - 54, "ESC HOME  ENTER INSPECT", app.font, DIM)

    def _inspect_selected_target(self) -> None:
        if not self.snapshot.hosts:
            return
        selected = self._page_selected("TARGETS", len(self.snapshot.hosts))
        host = self.snapshot.hosts[selected]
        item = self._add_item("target", f"Target {host.host}", self._host_detail(host), "[ ]", "info", persist=False)
        self._select_item_by_id(item.id)
        self._open_reader()

    def _inspect_page(self) -> None:
        if self.page_name == "MAP":
            detail = "\n".join(self._map_lines())
        elif self.page_name == "SIGNAL":
            detail = "\n".join(f"{key}: {value}" for key, value in asdict(self.snapshot).items() if key != "hosts")
        else:
            detail = "\n".join(self._timeline_entries(limit=20)) or "No timeline events."
        item = self._add_item("view", f"{self.page_name} detail", detail, "[ ]", "info", persist=False)
        self._select_item_by_id(item.id)
        self._open_reader()

    def _finish_mission(self, message: str) -> None:
        self.snapshot.completed_at = time.time()
        self.state = STATE_READY
        self.progress = 1.0
        self.command_line = message
        self._write_latest_snapshot()
        self.report_path = self._write_report()
        self._add_item("report", "Report ready", str(self.report_path), "[+]", "accent")
        self._persist_event("mission_completed", asdict(self.snapshot))
        self._save_state()

    def _network_context(self) -> dict[str, Any]:
        app = self.context.app
        wifi = app.wifi.status(allow_refresh=not app.input_render_pending)
        stats = app.system_snapshot()
        local_ip = str(stats.get("ip_address") or "offline")
        if local_ip == "offline":
            local_ip = self._nmcli_ip_address(wifi.device) or local_ip
        gateway = self._infer_gateway(local_ip)
        if wifi.connected and wifi.ssid:
            network_id = self._slug(wifi.ssid)
        elif gateway:
            network_id = self._slug(f"{gateway}-lan")
        elif local_ip and local_ip != "offline":
            network_id = self._slug(local_ip)
        else:
            network_id = "offline"
        return {
            "network_id": network_id,
            "ssid": wifi.ssid,
            "signal": wifi.signal,
            "wifi_state": wifi.state,
            "local_ip": local_ip,
            "gateway": gateway,
        }

    def _probe_hosts(self, ports: tuple[int, ...], *, deep: bool) -> list[HostSnapshot]:
        candidates = self._candidate_hosts(deep=deep)
        hosts: list[HostSnapshot] = []
        for index, host in enumerate(candidates):
            role = "gateway" if host == self.snapshot.gateway else "self" if host == self.snapshot.local_ip else "host"
            ports_result = {str(port): self._check_port(host, port) for port in ports}
            if not any(ports_result.values()) and host not in {self.snapshot.gateway, self.snapshot.local_ip}:
                continue
            snapshot = HostSnapshot(host=host, role=role, ports=ports_result)
            if ports_result.get("80"):
                snapshot.http["http"] = self._check_http(host, "http")
            if ports_result.get("443"):
                snapshot.http["https"] = self._check_http(host, "https")
                snapshot.tls = self._check_tls(host)
            hosts.append(snapshot)
            self.progress = min(0.95, self.progress + (0.2 / max(1, len(candidates))))
            self.command_line = f"PROBED {index + 1}/{len(candidates)}"
        return hosts

    def _candidate_hosts(self, *, deep: bool) -> list[str]:
        local_ip = self.snapshot.local_ip
        gateway = self.snapshot.gateway
        candidates: list[str] = []
        for host in (gateway, local_ip):
            if host and host != "offline" and host not in candidates:
                candidates.append(host)
        try:
            interface = ipaddress.ip_interface(f"{local_ip}/24")
            network = interface.network
        except ValueError:
            return candidates or ["127.0.0.1"]
        suffixes = (1, 2, 10, 20, 50, 100, 200, 254) if not deep else (1, 2, 3, 4, 5, 10, 20, 30, 40, 50, 75, 100, 150, 200, 254)
        base = int(network.network_address)
        for suffix in suffixes:
            host = str(ipaddress.ip_address(base + suffix))
            if host != local_ip and host not in candidates:
                candidates.append(host)
        return candidates[:16 if deep else 8]

    def _add_host_findings(self, hosts: list[HostSnapshot], *, deep: bool = False) -> None:
        if not hosts:
            self._add_item("finding", "No local hosts answered", "No host responded on checked ports.", "[-]", "dim")
            return
        open_count = sum(sum(1 for is_open in host.ports.values() if is_open) for host in hosts)
        self._add_item(
            "finding",
            f"{len(hosts)} hosts seen, {open_count} ports open",
            self._hosts_detail(hosts),
            "[+]",
            "warn" if open_count else "accent",
        )
        for host in hosts[:4]:
            open_ports = [port for port, is_open in host.ports.items() if is_open]
            if open_ports:
                self._add_item(
                    "finding",
                    f"{host.host} exposes {','.join(open_ports[:4])}",
                    self._host_detail(host),
                    "[!]" if deep else "[+]",
                    "warn",
                )

    def _add_diff_findings(self) -> None:
        previous_hosts = {
            host.get("host")
            for host in self.previous_snapshot.get("hosts", [])
            if isinstance(host, dict)
        }
        current_hosts = {host.host for host in self.snapshot.hosts}
        new_hosts = sorted(current_hosts - previous_hosts)
        missing_hosts = sorted(previous_hosts - current_hosts)
        if not self.previous_snapshot:
            self._add_item("memory", "First snapshot for network", "No previous timeline entry was available.", "[ ]", "info")
            return
        if new_hosts:
            self._add_item("memory", f"{len(new_hosts)} new hosts", "\n".join(new_hosts), "[!]", "warn")
        if missing_hosts:
            self._add_item("memory", f"{len(missing_hosts)} hosts missing", "\n".join(missing_hosts), "[-]", "dim")
        if not new_hosts and not missing_hosts:
            self._add_item("memory", "Network unchanged", "No host count change from previous snapshot.", "[=]", "accent")

    def _check_port(self, host: str, port: int) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.35)
                return sock.connect_ex((host, port)) == 0
        except OSError:
            return False

    def _check_http(self, host: str, scheme: str) -> dict[str, object]:
        conn_cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
        port = 443 if scheme == "https" else 80
        result: dict[str, object] = {"scheme": scheme, "status": None, "missing_headers": [], "server": "", "error": ""}
        try:
            conn = conn_cls(host, port=port, timeout=0.8)
            conn.request("HEAD", "/", headers={"User-Agent": "TinScope/2.0"})
            response = conn.getresponse()
            headers = {key.lower() for key, _ in response.getheaders()}
            result["status"] = response.status
            result["server"] = response.getheader("server", "")
            result["missing_headers"] = [header for header in SECURITY_HEADERS if header not in headers]
            conn.close()
        except OSError as exc:
            result["error"] = exc.__class__.__name__
        return result

    def _check_tls(self, host: str) -> dict[str, object]:
        result: dict[str, object] = {"issuer": "", "expires": "", "days_left": None, "error": ""}
        try:
            context = ssl.create_default_context()
            with socket.create_connection((host, 443), timeout=0.8) as sock:
                with context.wrap_socket(sock, server_hostname=host) as tls_sock:
                    cert = tls_sock.getpeercert()
        except OSError as exc:
            result["error"] = exc.__class__.__name__
            return result
        for part in cert.get("issuer", ()):
            for key, value in part:
                if key == "organizationName":
                    result["issuer"] = value
                    break
        not_after = cert.get("notAfter", "")
        try:
            expires = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
            result["expires"] = expires.strftime("%Y-%m-%d")
            result["days_left"] = (expires - datetime.now(timezone.utc)).days
        except ValueError:
            result["expires"] = not_after[:10]
        return result

    def _add_item(
        self,
        kind: str,
        summary: str,
        detail: str = "",
        icon: str = "[ ]",
        color: str = "normal",
        *,
        item_id: str | None = None,
        persist: bool = True,
    ) -> FeedItem:
        item = FeedItem(
            id=item_id or self._make_id(),
            kind=kind,
            summary=summary,
            detail=detail,
            icon=icon,
            color=color,
        )
        self.feed.append(item)
        self.selected_index = len(self.feed) - 1
        if persist:
            self._persist_event("feed_item", asdict(item))
        return item

    def _make_id(self) -> str:
        value = f"ts-{self._next_item_id:04d}"
        self._next_item_id += 1
        return value

    def _open_reader(self) -> None:
        if not self.feed:
            return
        self.reader_open = True
        self.reader_scroll = 0

    def _close_reader(self) -> None:
        self.reader_open = False
        self.reader_scroll = 0

    def _reader_lines(self, width: int) -> list[str]:
        if not self.feed:
            return ["No item selected."]
        item = self.feed[self.selected_index]
        lines = [f"{item.icon} {item.kind.upper()}", ""]
        lines.extend(self._wrap(item.summary, width))
        if item.detail:
            lines.extend(["", "DETAIL", ""])
            lines.extend(self._wrap_block(item.detail, width))
        return lines

    def _move_selection(self, delta: int) -> None:
        if not self.feed:
            return
        self.selected_index = min(max(0, self.selected_index + delta), len(self.feed) - 1)

    def _move_page_selection(self, delta: int) -> None:
        if self.page_name in {"AGENT", "INBOX"}:
            self._move_selection(delta)
            return
        count = self._page_item_count()
        if count <= 0:
            return
        key = self.page_name
        self.page_selection[key] = min(max(0, self.page_selection.get(key, 0) + delta), count - 1)

    def _cycle_page(self, delta: int) -> None:
        self.page_index = (self.page_index + delta) % len(PAGES)
        self.command_line = f"{self.page_name} VIEW"

    def _page_item_count(self) -> int:
        if self.page_name == "TARGETS":
            return len(self.snapshot.hosts)
        if self.page_name == "ACTIONS":
            return len(ACTION_ITEMS)
        return 0

    def _page_selected(self, page: str, count: int) -> int:
        if count <= 0:
            self.page_selection[page] = 0
            return 0
        value = min(max(0, self.page_selection.get(page, 0)), count - 1)
        self.page_selection[page] = value
        return value

    def _move_reader_selection(self, delta: int) -> None:
        previous = self.selected_index
        self._move_selection(delta)
        if self.selected_index != previous:
            self.reader_scroll = 0

    def _select_item_by_id(self, item_id: str) -> None:
        for index, item in enumerate(self.feed):
            if item.id == item_id:
                self.selected_index = index
                return

    def _clamp_selection(self, visible_rows: int) -> None:
        if not self.feed:
            self.selected_index = 0
            self.feed_top = 0
            return
        self.selected_index = min(max(0, self.selected_index), len(self.feed) - 1)
        if self.selected_index < self.feed_top:
            self.feed_top = self.selected_index
        if self.selected_index >= self.feed_top + visible_rows:
            self.feed_top = self.selected_index - visible_rows + 1
        self.feed_top = min(max(0, self.feed_top), max(0, len(self.feed) - visible_rows))

    def _save_all(self) -> None:
        self._write_latest_snapshot()
        self._write_report()
        self._save_state()

    def _save_state(self) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "state": self.state,
            "status_line": self.status_line,
            "command_line": self.command_line,
            "progress": self.progress,
            "selected_index": self.selected_index,
            "network_id": self.snapshot.network_id,
            "report_path": str(self.report_path) if self.report_path else "",
            "pending_request": asdict(self.pending_request) if self.pending_request else None,
            "feed": [asdict(item) for item in self.feed[-20:]],
            "snapshot": asdict(self.snapshot),
        }
        self._atomic_write_json(self._state_dir / "state.json", payload)

    def _load_state(self) -> None:
        path = self._state_dir / "state.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.state = STATE_ERROR
            self.command_line = "STATE MEMORY CORRUPT"
            return
        self.state = data.get("state") if data.get("state") in {STATE_IDLE, STATE_READY, STATE_ERROR} else STATE_IDLE
        self.status_line = str(data.get("status_line") or self.status_line)
        self.command_line = str(data.get("command_line") or "RESUMED MEMORY")
        self.progress = float(data.get("progress") or 0.0)
        self.report_path = Path(data["report_path"]) if data.get("report_path") else None
        self.feed = [self._feed_item_from_dict(item) for item in data.get("feed", []) if isinstance(item, dict)]
        self.selected_index = min(int(data.get("selected_index") or 0), max(0, len(self.feed) - 1))
        pending = data.get("pending_request")
        if isinstance(pending, dict):
            self.pending_request = PendingRequest(
                id=str(pending.get("id") or "restored-request"),
                summary=str(pending.get("summary") or "Approval requested"),
                detail=str(pending.get("detail") or ""),
                action=str(pending.get("action") or ""),
            )
            self.state = STATE_REQUEST
        snapshot = data.get("snapshot")
        if isinstance(snapshot, dict):
            self.snapshot = self._snapshot_from_dict(snapshot)

    def _write_latest_snapshot(self) -> None:
        network_dir = self._network_dir(self.snapshot.network_id)
        network_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_write_json(network_dir / "latest.json", asdict(self.snapshot))

    def _write_report(self) -> Path:
        network_dir = self._network_dir(self.snapshot.network_id)
        network_dir.mkdir(parents=True, exist_ok=True)
        path = network_dir / "report.md"
        path.write_text(self._report_markdown(), encoding="utf-8")
        self.report_path = path
        return path

    def _persist_event(self, event_type: str, payload: dict[str, Any]) -> None:
        network_id = self.snapshot.network_id or "unknown"
        path = self._network_dir(network_id) / "timeline.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "type": event_type,
            "at": time.time(),
            "payload": payload,
        }
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, sort_keys=True) + "\n")

    def _load_latest_snapshot(self, network_id: str) -> dict[str, Any]:
        path = self._network_dir(network_id) / "latest.json"
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _network_dir(self, network_id: str) -> Path:
        return self._state_dir / "networks" / self._slug(network_id or "unknown")

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.tmp")
        temp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temp.replace(path)

    def _report_markdown(self) -> str:
        snapshot = self.snapshot
        lines = [
            "# TinScope Network Field Kit",
            "",
            f"- Network: `{snapshot.network_id}`",
            f"- SSID: `{snapshot.ssid or 'unknown'}`",
            f"- Local IP: `{snapshot.local_ip}`",
            f"- Gateway: `{snapshot.gateway or 'unknown'}`",
            f"- Completed: `{self._time_label(snapshot.completed_at or time.time())}`",
            "",
            "## Findings",
            "",
        ]
        if self.feed:
            lines.extend(f"- {item.icon} **{item.kind}**: {item.summary}" for item in self.feed)
        else:
            lines.append("- No findings recorded.")
        lines.extend(["", "## Hosts", ""])
        if not snapshot.hosts:
            lines.append("- No hosts recorded.")
        for host in snapshot.hosts:
            open_ports = [port for port, is_open in host.ports.items() if is_open]
            lines.append(f"- `{host.host}` `{host.role}` open: `{', '.join(open_ports) or 'none'}`")
        return "\n".join(lines) + "\n"

    def _hosts_detail(self, hosts: list[HostSnapshot]) -> str:
        lines: list[str] = []
        for host in hosts:
            open_ports = [port for port, is_open in host.ports.items() if is_open]
            lines.append(f"{host.host} ({host.role}) open: {', '.join(open_ports) or 'none'}")
        return "\n".join(lines)

    def _host_detail(self, host: HostSnapshot) -> str:
        lines = [f"host: {host.host}", f"role: {host.role}", "ports:"]
        lines.extend(f"  {port}: {'open' if is_open else 'closed'}" for port, is_open in host.ports.items())
        if host.http:
            lines.extend(["", "http:"])
            lines.extend(json.dumps(host.http, indent=2).splitlines())
        if host.tls:
            lines.extend(["", "tls:"])
            lines.extend(json.dumps(host.tls, indent=2).splitlines())
        return "\n".join(lines)

    def _signal_summary(self) -> str:
        if self.snapshot.ssid:
            return f"{self.snapshot.ssid} {self.snapshot.signal}%"
        if self.snapshot.local_ip != "offline":
            return f"LAN {self.snapshot.local_ip}"
        return "Offline signal"

    def _map_lines(self) -> list[str]:
        gateway = self.snapshot.gateway or "gateway ?"
        local_ip = self.snapshot.local_ip
        hosts = [host for host in self.snapshot.hosts if host.host not in {gateway, local_ip}]
        lines = [
            "     INTERNET ?",
            "         |",
            f"   [ROUTER] {self._host_tail(gateway)}",
            "     /   |   \\",
            f"[DECK] {self._host_tail(local_ip)}",
        ]
        for host in hosts[:4]:
            ports = [port for port, is_open in host.ports.items() if is_open]
            marker = "*" if ports else "-"
            label = f"[{host.role.upper()[:4]}] {self._host_tail(host.host)} {marker}{','.join(ports[:2])}"
            lines.append(label)
        if not hosts:
            lines.append("[HOST] none seen")
        return lines

    def _timeline_entries(self, *, limit: int) -> list[str]:
        path = self._network_dir(self.snapshot.network_id) / "timeline.jsonl"
        if not path.exists():
            return []
        try:
            raw_lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
        except OSError:
            return []
        entries: list[str] = []
        for raw_line in reversed(raw_lines):
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            label = str(event.get("type") or "event").replace("_", " ")
            at = self._clock_label(float(event.get("at") or 0.0))
            payload = event.get("payload")
            summary = ""
            if isinstance(payload, dict):
                summary = str(payload.get("summary") or payload.get("network_id") or payload.get("kind") or "")
            entries.append(self._trim(f"{at} {label} {summary}", 34))
        return entries

    @staticmethod
    def _host_tail(host: str) -> str:
        if not host:
            return "?"
        parts = host.split(".")
        if len(parts) == 4:
            return f".{parts[-1]}"
        return host[:8]

    @staticmethod
    def _clock_label(value: float) -> str:
        if value <= 0:
            return "--:--"
        return datetime.fromtimestamp(value).strftime("%H:%M")

    @staticmethod
    def _infer_gateway(local_ip: str) -> str:
        try:
            address = ipaddress.ip_address(local_ip)
        except ValueError:
            return ""
        if address.version != 4:
            return ""
        parts = local_ip.split(".")
        if len(parts) != 4:
            return ""
        return ".".join(parts[:3] + ["1"])

    @staticmethod
    def _nmcli_ip_address(device: str) -> str:
        try:
            proc = subprocess.run(
                ["nmcli", "-g", "IP4.ADDRESS", "device", "show", device],
                text=True,
                capture_output=True,
                timeout=0.8,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            return ""
        if proc.returncode != 0:
            return ""
        for line in proc.stdout.splitlines():
            address, _, _prefix = line.strip().partition("/")
            try:
                ipaddress.ip_address(address)
            except ValueError:
                continue
            if not address.startswith("127."):
                return address
        return ""

    @staticmethod
    def _slug(value: str) -> str:
        safe = "".join(char.lower() if char.isalnum() else "-" for char in value.strip())
        while "--" in safe:
            safe = safe.replace("--", "-")
        return safe.strip("-")[:48] or "unknown"

    @staticmethod
    def _time_label(value: float) -> str:
        if value <= 0:
            return "never"
        return datetime.fromtimestamp(value).isoformat(timespec="seconds")

    def _footer_hint(self) -> str:
        if self.reader_open:
            return "UP/DN SCROLL  L/R ITEM  ESC CLOSE"
        if self.pending_request is not None:
            return "ENTER APPROVE  ESC DENY  SPACE INFO"
        if self.state == STATE_IDLE:
            return "ENTER START  SPACE INFO"
        if self.state in {STATE_SURVEYING, STATE_ANALYZING}:
            return "SURVEY LIVE  ENTER INSPECT"
        return "UP/DN SELECT  ENTER INSPECT"

    def _state_color(self) -> str:
        return {
            STATE_IDLE: DIM,
            STATE_SURVEYING: INFO,
            STATE_ANALYZING: COOL,
            STATE_REQUEST: WARN,
            STATE_READY: ACCENT,
            STATE_ERROR: DANGER,
        }.get(self.state, FG)

    def _item_color(self, item: FeedItem) -> str:
        return {
            "accent": ACCENT,
            "warn": WARN,
            "danger": DANGER,
            "info": INFO,
            "dim": DIM,
        }.get(item.color, FG)

    @classmethod
    def _feed_item_from_dict(cls, data: dict[str, Any]) -> FeedItem:
        return FeedItem(
            id=str(data.get("id") or "restored"),
            kind=str(data.get("kind") or "event"),
            summary=str(data.get("summary") or ""),
            detail=str(data.get("detail") or ""),
            icon=str(data.get("icon") or "[ ]"),
            color=str(data.get("color") or "normal"),
            created_at=float(data.get("created_at") or time.time()),
        )

    @classmethod
    def _snapshot_from_dict(cls, data: dict[str, Any]) -> NetworkSnapshot:
        hosts = []
        for raw_host in data.get("hosts", []):
            if not isinstance(raw_host, dict):
                continue
            hosts.append(
                HostSnapshot(
                    host=str(raw_host.get("host") or ""),
                    role=str(raw_host.get("role") or "host"),
                    ports=dict(raw_host.get("ports") or {}),
                    http=dict(raw_host.get("http") or {}),
                    tls=dict(raw_host.get("tls") or {}),
                )
            )
        return NetworkSnapshot(
            network_id=str(data.get("network_id") or "unknown"),
            ssid=str(data.get("ssid") or ""),
            local_ip=str(data.get("local_ip") or "offline"),
            gateway=str(data.get("gateway") or ""),
            signal=int(data.get("signal") or 0),
            hosts=hosts,
            started_at=float(data.get("started_at") or 0.0),
            completed_at=float(data.get("completed_at") or 0.0),
        )

    @staticmethod
    def _wrap(text: str, width: int) -> list[str]:
        words = text.replace("\n", " ").split()
        if not words:
            return [""]
        lines: list[str] = []
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if len(candidate) <= width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
        return lines

    @classmethod
    def _wrap_block(cls, text: str, width: int) -> list[str]:
        lines: list[str] = []
        for raw_line in text.splitlines():
            lines.extend(cls._wrap(raw_line, width))
        return lines or [""]

    @staticmethod
    def _trim(text: object, limit: int) -> str:
        value = str(text)
        if len(value) <= limit:
            return value
        return f"{value[: max(0, limit - 1)]}>"
