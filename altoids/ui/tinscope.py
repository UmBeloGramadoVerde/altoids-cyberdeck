from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import http.client
from pathlib import Path
import socket
import ssl
import threading
import time

from PIL import ImageDraw

from ..colors import ACCENT, AUX, COOL, DANGER, DIM, FG, INFO, SURFACE_ALT, SURFACE_GRID, SURFACE_INSET, WARN
from ..input_keyboard import KeyboardEvent
from .base import Screen, ScreenContext
from .widgets import draw_label, draw_panel, draw_scanlines, draw_segmented_bar, draw_status_dot


PORT_PROFILES = {
    "QUICK": (22, 80, 443, 3000, 5000, 8000, 8080, 8443),
    "WEB": (80, 443, 8000, 8080, 8443, 8888, 9000, 9443),
    "DEV": (22, 80, 443, 3000, 3306, 5000, 5432, 6379, 8000, 8080, 9200),
    "WIDE": (21, 22, 25, 53, 80, 110, 143, 443, 445, 993, 995, 1883, 3000, 3306, 5000, 5432, 6379, 8000, 8080, 8443, 9000, 9200),
}
SECURITY_HEADERS = (
    "strict-transport-security",
    "content-security-policy",
    "x-frame-options",
    "x-content-type-options",
    "referrer-policy",
)


@dataclass(slots=True)
class PortFinding:
    port: int
    open: bool = False
    service: str = ""


@dataclass(slots=True)
class WebFinding:
    scheme: str
    status: int | None = None
    server: str = ""
    present_headers: set[str] = field(default_factory=set)
    error: str = ""


@dataclass(slots=True)
class TlsFinding:
    issuer: str = ""
    expires: str = ""
    days_left: int | None = None
    error: str = ""


@dataclass(slots=True)
class TinScopeReport:
    target: str = "127.0.0.1"
    profile: str = "QUICK"
    resolved_ip: str = ""
    ports: list[PortFinding] = field(default_factory=list)
    web: list[WebFinding] = field(default_factory=list)
    tls: TlsFinding = field(default_factory=TlsFinding)
    started_at: float = 0.0
    completed_at: float = 0.0
    error: str = ""

    @property
    def running(self) -> bool:
        return self.started_at > 0 and self.completed_at == 0 and not self.error


class TinScopeScreen(Screen):
    name = "tinscope"
    _TARGET_PRESETS = ("127.0.0.1", "localhost", "router.local", "example.com")
    _PAGES = ("TARGET", "PORTS", "WEB", "REPORT")
    _PROFILES = tuple(PORT_PROFILES.keys())

    def __init__(self, context: ScreenContext) -> None:
        super().__init__(context)
        self.target = "127.0.0.1"
        self.profile = "QUICK"
        self.page_index = 0
        self.status_line = "type target or enter scan"
        self.report = TinScopeReport(target=self.target, profile=self.profile)
        self._worker: threading.Thread | None = None
        self._blink = 0.0
        self._last_export_path: Path | None = None

    def update(self, dt: float) -> bool:
        self._blink = (self._blink + dt) % 1.0
        return self.report.running or self._blink < dt

    def render(self, draw: ImageDraw.ImageDraw, buffer) -> None:
        app = self.context.app
        width = app.config.display.width
        height = app.config.display.height
        footer_height = 24 if app.shows_button_bar else 0
        content_bottom = height - footer_height - 8
        signature = (width, height, footer_height)
        buffer.paste(self.cached_background(signature, buffer.size, self._paint_static_background))
        draw = ImageDraw.Draw(buffer)

        self._draw_scope_strip(draw, content_bottom)
        if self.page_index == 0:
            self._render_scope(draw, content_bottom)
        elif self.page_index == 1:
            self._render_ports(draw, content_bottom)
        elif self.page_index == 2:
            self._render_web(draw, content_bottom)
        else:
            self._render_report(draw, content_bottom)

    def _paint_static_background(self, draw: ImageDraw.ImageDraw, buffer) -> None:
        app = self.context.app
        width = app.config.display.width
        height = app.config.display.height
        footer_height = 24 if app.shows_button_bar else 0
        content_bottom = height - footer_height - 8

        draw_label(draw, 12, 8, "TINSCOPE // RESEARCH SURFACE", app.font, ACCENT)
        draw_label(draw, width - 65, 8, "VFD LAB", app.font, WARN)
        draw.line((12, 22, width - 12, 22), fill=SURFACE_INSET, width=1)

        draw_panel(draw, (12, 30, width - 12, 90), title="TARGET", title_font=app.font, outline=ACCENT, title_color=ACCENT)
        draw_scanlines(draw, (12, 30, width - 12, 90), step=6)
        draw_panel(draw, (12, 98, 132, content_bottom), title="SIGNAL", title_font=app.font, outline=INFO, title_color=INFO)
        draw_panel(draw, (140, 98, width - 12, content_bottom), title="DETAIL", title_font=app.font, outline=AUX, title_color=AUX, fill=SURFACE_ALT)
        draw_scanlines(draw, (140, 98, width - 12, content_bottom), step=6, color=SURFACE_GRID)

    def _draw_scope_strip(self, draw: ImageDraw.ImageDraw, content_bottom: int) -> None:
        app = self.context.app
        target = self._trim(self.target.upper(), 28)
        scan_color = ACCENT if self.report.running and self._blink < 0.55 else DIM
        draw_status_dot(draw, 24, 48, True, ACCENT)
        draw_label(draw, 40, 44, "RESEARCH MODE", app.font, ACCENT)
        draw_status_dot(draw, 134, 48, self.report.running, scan_color)
        draw_label(draw, 150, 44, "SCAN LIVE" if self.report.running else f"{self.profile} SET", app.font, scan_color if self.report.running else INFO)
        draw_label(draw, 24, 66, f"> {target}", app.terminal_font, FG)
        page_tabs = " ".join(f"[{i + 1}]{name}" if i == self.page_index else name for i, name in enumerate(self._PAGES))
        draw_label(draw, 22, content_bottom - 16, self._trim(page_tabs, 17), app.font, DIM)
        draw_label(draw, 150, content_bottom - 16, self._trim(self.status_line.upper(), 15), app.font, WARN if "error" in self.status_line else ACCENT)

    def _render_scope(self, draw: ImageDraw.ImageDraw, content_bottom: int) -> None:
        app = self.context.app
        age = self._scan_age()
        lines = [
            ("MODE", self.profile),
            ("INPUT", "HOST ONLY"),
            ("DNS", self.report.resolved_ip or "PENDING"),
            ("AGE", age),
        ]
        for index, (label, value) in enumerate(lines):
            draw_label(draw, 24, 116 + index * 18, label, app.font, DIM)
            draw_label(draw, 70, 116 + index * 18, self._trim(value, 8), app.font, FG if value != "PENDING" else DIM)
        self._draw_eva_ticks(draw, 152, 116, self._risk_score(), content_bottom)

    def _render_ports(self, draw: ImageDraw.ImageDraw, content_bottom: int) -> None:
        app = self.context.app
        ports = self.report.ports or [PortFinding(port) for port in PORT_PROFILES[self.profile][:6]]
        for index, finding in enumerate(ports[:6]):
            y = 116 + index * 16
            color = WARN if finding.open else DIM
            draw_status_dot(draw, 24, y + 1, finding.open, color)
            draw_label(draw, 40, y, f"{finding.port:>4}", app.font, FG)
            state = "OPEN" if finding.open else "CLOSED"
            draw_label(draw, 78, y, state, app.font, color)
        open_ports = [p for p in self.report.ports if p.open]
        draw_label(draw, 152, 116, "EXPOSURE", app.font, INFO)
        draw_label(draw, 152, 136, f"{len(open_ports):02d} OPEN", app.font_large, WARN if open_ports else ACCENT)
        draw_label(draw, 152, 164, self._trim(", ".join(str(p.port) for p in open_ports) or "NONE", 13), app.font, FG if open_ports else DIM)
        draw_label(draw, 152, 184, self._trim(f"{self.report.profile} {len(self.report.ports or PORT_PROFILES[self.profile])}P", 13), app.font, DIM)

    def _render_web(self, draw: ImageDraw.ImageDraw, content_bottom: int) -> None:
        app = self.context.app
        findings = self.report.web
        for index, scheme in enumerate(("http", "https")):
            finding = next((item for item in findings if item.scheme == scheme), WebFinding(scheme=scheme))
            y = 116 + index * 34
            active = finding.status is not None
            draw_status_dot(draw, 24, y + 2, active, ACCENT if active else DIM)
            draw_label(draw, 40, y, scheme.upper(), app.font, FG)
            status = str(finding.status) if finding.status is not None else self._trim(finding.error or "NO DATA", 10)
            draw_label(draw, 86, y, status, app.font, ACCENT if active else DIM)
            missing = [header for header in SECURITY_HEADERS if header not in finding.present_headers]
            draw_label(draw, 40, y + 14, self._trim(f"MISS {len(missing)} HDR", 14), app.font, WARN if active and missing else DIM)
        tls = self.report.tls
        tls_color = ACCENT if tls.days_left is not None and tls.days_left > 30 else WARN
        draw_label(draw, 152, 116, "TLS CERT", app.font, INFO)
        draw_label(draw, 152, 136, self._trim(tls.expires or tls.error or "NO DATA", 14), app.font, tls_color if tls.expires else DIM)
        if tls.days_left is not None:
            draw_label(draw, 152, 154, f"{tls.days_left:>4} DAYS", app.font_large, tls_color)
        draw_label(draw, 152, 184, self._trim(tls.issuer or "ISSUER PENDING", 14), app.font, DIM)

    def _render_report(self, draw: ImageDraw.ImageDraw, content_bottom: int) -> None:
        app = self.context.app
        risk = self._risk_score()
        open_count = len([p for p in self.report.ports if p.open])
        missing_headers = sum(len([h for h in SECURITY_HEADERS if h not in item.present_headers]) for item in self.report.web if item.status is not None)
        draw_label(draw, 24, 116, "RISK", app.font, DIM)
        draw_label(draw, 70, 110, f"{risk:02d}", app.font_large, WARN if risk else ACCENT)
        draw_segmented_bar(draw, 24, 144, 92, risk / 10.0, segments=10, color=WARN if risk > 4 else ACCENT)
        draw_label(draw, 24, 164, f"OPEN PORTS {open_count}", app.font, WARN if open_count else DIM)
        draw_label(draw, 24, 180, f"HEADER GAPS {missing_headers}", app.font, WARN if missing_headers else DIM)
        draw_label(draw, 152, 116, "NEXT ACTIONS", app.font, INFO)
        for index, line in enumerate(self._action_lines()[:5]):
            draw_label(draw, 152, 136 + index * 14, self._trim(line, 14), app.font, FG if index == 0 else DIM)

    def on_button(self, button: str, long_press: bool) -> bool:
        if button == "A":
            self._cycle_target(-1 if long_press else 1)
            return True
        if button == "B":
            self.page_index = (self.page_index + (-1 if long_press else 1)) % len(self._PAGES)
            return True
        if button == "X":
            if long_press:
                self._export_report()
            else:
                self._start_scan()
            return True
        if button == "Y":
            if long_press:
                self.context.app.set_screen("home")
            else:
                self._cycle_profile(1)
            return True
        return False

    def on_keyboard_event(self, event: KeyboardEvent) -> bool:
        if event.ctrl or event.alt:
            return False
        if event.key in {"q", "escape"}:
            self.context.app.set_screen("home")
            return True
        if self.report.running and (event.text or event.key in {"backspace", "delete"}):
            self.status_line = "scan live"
            return True
        if event.key in {"1", "2", "3", "4"}:
            self.page_index = int(event.key) - 1
            return True
        if event.key in {"left", "pageup"}:
            self.page_index = (self.page_index - 1) % len(self._PAGES)
            return True
        if event.key in {"right", "tab", "pagedown"}:
            self.page_index = (self.page_index + 1) % len(self._PAGES)
            return True
        if event.key in {"up", "down"}:
            self._cycle_target(-1 if event.key == "up" else 1)
            return True
        if event.key == "enter":
            self._start_scan()
            return True
        if event.key == "backspace":
            self.target = self.target[:-1] or "127.0.0.1"
            self._target_changed()
            return True
        if event.key == "delete":
            self.target = ""
            self._target_changed()
            return True
        if event.key == " ":
            if self.page_index == 3:
                self._export_report()
            else:
                self._cycle_profile(1)
            return True
        if event.text and event.text in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_:":
            if self.target == "127.0.0.1":
                self.target = ""
            self.target = (self.target + event.text.lower())[-42:]
            self._target_changed()
            return True
        return False

    def get_button_hints(self) -> list[str]:
        return ["A target", "B page", "X scan", "Y mode"]

    def _start_scan(self) -> None:
        target = self.target.strip()
        if not target or "/" in target:
            self.status_line = "host only"
            self.context.app.accents.trigger("error")
            return
        if self.report.running:
            self.status_line = "scan already live"
            return
        ports = PORT_PROFILES[self.profile]
        self.report = TinScopeReport(target=target, profile=self.profile, started_at=time.time())
        self.status_line = "scan live"
        self.page_index = 1
        self._worker = threading.Thread(target=self._scan_worker, args=(target, ports), daemon=True)
        self._worker.start()

    def _scan_worker(self, target: str, ports: tuple[int, ...]) -> None:
        report = self.report
        try:
            report.resolved_ip = socket.gethostbyname(target)
            report.ports = [self._check_port(target, port) for port in ports]
            report.web = [self._check_http(target, scheme) for scheme in ("http", "https")]
            report.tls = self._check_tls(target)
            report.completed_at = time.time()
            self.status_line = "scan complete"
        except OSError as exc:
            report.error = str(exc)
            report.completed_at = time.time()
            self.status_line = "scan error"

    def _check_port(self, target: str, port: int) -> PortFinding:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.7)
                open_port = sock.connect_ex((target, port)) == 0
        except OSError:
            open_port = False
        return PortFinding(port=port, open=open_port, service=self._service_name(port))

    def _check_http(self, target: str, scheme: str) -> WebFinding:
        conn_cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
        port = 443 if scheme == "https" else 80
        finding = WebFinding(scheme=scheme)
        try:
            conn = conn_cls(target, port=port, timeout=1.2)
            conn.request("HEAD", "/", headers={"User-Agent": "TinScope/1.0"})
            response = conn.getresponse()
            finding.status = response.status
            finding.server = response.getheader("server", "")
            finding.present_headers = {key.lower() for key, _ in response.getheaders()}
            conn.close()
        except OSError as exc:
            finding.error = exc.__class__.__name__
        return finding

    def _check_tls(self, target: str) -> TlsFinding:
        try:
            context = ssl.create_default_context()
            with socket.create_connection((target, 443), timeout=1.2) as sock:
                with context.wrap_socket(sock, server_hostname=target) as tls_sock:
                    cert = tls_sock.getpeercert()
        except OSError as exc:
            return TlsFinding(error=exc.__class__.__name__)
        issuer = "UNKNOWN"
        for part in cert.get("issuer", ()):
            for key, value in part:
                if key == "organizationName":
                    issuer = value
                    break
        not_after = cert.get("notAfter", "")
        try:
            expires = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
            days_left = (expires - datetime.now(timezone.utc)).days
            label = expires.strftime("%Y-%m-%d")
        except ValueError:
            days_left = None
            label = not_after[:10]
        return TlsFinding(issuer=issuer, expires=label, days_left=days_left)

    def _export_report(self) -> None:
        path = Path(".runtime/tinscope-report.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._report_markdown(), encoding="utf-8")
        self._last_export_path = path
        self.status_line = "report exported"

    def _report_markdown(self) -> str:
        open_ports = [p for p in self.report.ports if p.open]
        lines = [
            "# TinScope Report",
            "",
            f"- Target: `{self.report.target}`",
            f"- Profile: `{self.report.profile}`",
            f"- Resolved IP: `{self.report.resolved_ip or 'unresolved'}`",
            f"- Completed: `{datetime.fromtimestamp(self.report.completed_at or time.time()).isoformat(timespec='seconds')}`",
            "",
            "## Open Ports",
            "",
        ]
        lines.extend(f"- `{finding.port}` {finding.service}" for finding in open_ports)
        if not open_ports:
            lines.append("- None detected in common port set.")
        lines.extend(["", "## Web Checks", ""])
        for finding in self.report.web:
            missing = [header for header in SECURITY_HEADERS if header not in finding.present_headers]
            status = finding.status if finding.status is not None else finding.error or "unreachable"
            lines.append(f"- `{finding.scheme}` status: `{status}`, missing headers: `{', '.join(missing) or 'none'}`")
        lines.extend(["", "## TLS", ""])
        tls = self.report.tls
        lines.append(f"- Expires: `{tls.expires or 'unknown'}`")
        lines.append(f"- Days left: `{tls.days_left if tls.days_left is not None else 'unknown'}`")
        lines.append(f"- Issuer: `{tls.issuer or tls.error or 'unknown'}`")
        lines.extend(["", "## Suggested Actions", ""])
        lines.extend(f"- {line}" for line in self._action_lines())
        return "\n".join(lines) + "\n"

    def _action_lines(self) -> list[str]:
        actions: list[str] = []
        open_ports = [p for p in self.report.ports if p.open]
        if open_ports:
            actions.append("verify exposed services")
        if any(item.status is not None and any(header not in item.present_headers for header in SECURITY_HEADERS) for item in self.report.web):
            actions.append("harden web headers")
        if self.report.tls.days_left is not None and self.report.tls.days_left < 30:
            actions.append("renew TLS cert")
        if not actions:
            actions.append("baseline looks quiet")
        actions.append("rerun after changes")
        return actions

    def _risk_score(self) -> int:
        score = len([p for p in self.report.ports if p.open])
        score += sum(1 for item in self.report.web if item.status is not None)
        score += sum(len([h for h in SECURITY_HEADERS if h not in item.present_headers]) for item in self.report.web if item.status is not None) // 2
        if self.report.tls.days_left is not None and self.report.tls.days_left < 30:
            score += 2
        return min(score, 10)

    def _draw_eva_ticks(self, draw: ImageDraw.ImageDraw, x: int, y: int, risk: int, bottom: int) -> None:
        app = self.context.app
        draw_label(draw, x, y, "SYNC RISK", app.font, INFO)
        for index in range(10):
            top = y + 20 + index * 8
            color = WARN if index < risk else SURFACE_INSET
            draw.rectangle((x, top, x + 64, top + 4), outline=DIM, fill=color)
        draw_label(draw, x, min(bottom - 34, y + 110), "RESEARCH", app.font, ACCENT)

    def _cycle_profile(self, delta: int) -> None:
        if self.report.running:
            self.status_line = "scan live"
            return
        index = self._PROFILES.index(self.profile)
        self.profile = self._PROFILES[(index + delta) % len(self._PROFILES)]
        self.report = TinScopeReport(target=self.target, profile=self.profile)
        self.status_line = f"profile {self.profile.lower()}"

    def _cycle_target(self, delta: int) -> None:
        if self.report.running:
            self.status_line = "scan live"
            return
        try:
            index = self._TARGET_PRESETS.index(self.target)
        except ValueError:
            index = -1
        self.target = self._TARGET_PRESETS[(index + delta) % len(self._TARGET_PRESETS)]
        self._target_changed()

    def _target_changed(self) -> None:
        self.report = TinScopeReport(target=self.target, profile=self.profile)
        self.status_line = "target edited"

    def _scan_age(self) -> str:
        if self.report.completed_at:
            return f"{int(time.time() - self.report.completed_at)}S"
        if self.report.running:
            return f"T+{int(time.time() - self.report.started_at)}S"
        return "NONE"

    @staticmethod
    def _service_name(port: int) -> str:
        return {
            22: "ssh",
            80: "http",
            443: "https",
            3000: "dev",
            5000: "flask",
            8000: "http-alt",
            8080: "proxy",
            8443: "https-alt",
        }.get(port, "")

    @staticmethod
    def _trim(text: object, limit: int) -> str:
        value = str(text)
        if len(value) <= limit:
            return value
        return f"{value[: max(0, limit - 1)]}>"
