from __future__ import annotations

import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field


@dataclass(slots=True)
class WifiNetwork:
    ssid: str
    signal: int          # 0-100
    security: str        # raw nmcli string e.g. "WPA2", "WPA3 SAE", ""
    active: bool = False
    known: bool = False

    @property
    def open(self) -> bool:
        return self.security in {"", "--"}


@dataclass(slots=True)
class WifiStatus:
    connected: bool = False
    ssid: str = ""
    signal: int = 0
    ip: str = ""
    state: str = "unknown"


@dataclass(slots=True)
class ConnectResult:
    state: str = "idle"   # "idle" | "connecting" | "success" | "failed"
    message: str = ""
    ssid: str = ""


class WifiManager:
    def __init__(self, scan_cache_seconds: float = 30.0) -> None:
        self._nmcli_path = shutil.which("nmcli")
        self._scan_cache_seconds = scan_cache_seconds
        self._status_cache: WifiStatus | None = None
        self._status_at = 0.0
        self._scan_cache: list[WifiNetwork] = []
        self._scan_at = 0.0
        self._connect_result = ConnectResult()
        self._connect_lock = threading.Lock()
        self._connect_thread: threading.Thread | None = None

    @property
    def available(self) -> bool:
        return self._nmcli_path is not None

    def status(self, allow_refresh: bool = True) -> WifiStatus:
        now = time.monotonic()
        if self._status_cache is not None and now - self._status_at < 2.0:
            return self._status_cache
        if not allow_refresh or not self.available:
            return self._status_cache or WifiStatus()
        self._status_cache = self._active_connection()
        self._status_at = now
        return self._status_cache

    def scan(self, force: bool = False) -> list[WifiNetwork]:
        if not self.available:
            return []
        now = time.monotonic()
        if not force and self._scan_cache and now - self._scan_at < self._scan_cache_seconds:
            return self._scan_cache
        if force:
            try:
                subprocess.run(
                    [self._nmcli_path, "device", "wifi", "rescan"],
                    check=False, capture_output=True, timeout=5.0,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        try:
            result = subprocess.run(
                [self._nmcli_path, "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY",
                 "device", "wifi", "list", "--rescan", "no"],
                check=False, capture_output=True, text=True, timeout=5.0,
            )
        except (OSError, subprocess.TimeoutExpired):
            return self._scan_cache
        if result.returncode != 0:
            return self._scan_cache
        known = self._known_ssids()
        self._scan_cache = self._parse_scan(result.stdout, known)
        self._scan_at = now
        return self._scan_cache

    def connect_async(self, ssid: str, password: str = "") -> None:
        with self._connect_lock:
            self._connect_result = ConnectResult(state="connecting", ssid=ssid)
        thread = threading.Thread(
            target=self._connect_worker, args=(ssid, password), daemon=True,
        )
        self._connect_thread = thread
        thread.start()

    def poll_connect(self) -> ConnectResult:
        with self._connect_lock:
            return ConnectResult(
                state=self._connect_result.state,
                message=self._connect_result.message,
                ssid=self._connect_result.ssid,
            )

    def reset_connect(self) -> None:
        with self._connect_lock:
            self._connect_result = ConnectResult()

    def poll(self) -> None:
        """Lightweight tick for the main loop — refreshes status cache."""
        self.status(allow_refresh=True)

    def _connect_worker(self, ssid: str, password: str) -> None:
        if not self.available:
            with self._connect_lock:
                self._connect_result = ConnectResult(
                    state="failed", message="nmcli not available", ssid=ssid,
                )
            return
        known = self._known_ssids()
        if ssid in known and not password:
            cmd = [self._nmcli_path, "connection", "up", ssid]
        else:
            cmd = [self._nmcli_path, "device", "wifi", "connect", ssid]
            if password:
                cmd += ["password", password]
        try:
            result = subprocess.run(
                cmd, check=False, capture_output=True, text=True, timeout=30.0,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            with self._connect_lock:
                self._connect_result = ConnectResult(
                    state="failed", message=str(exc), ssid=ssid,
                )
            return
        if result.returncode == 0:
            # Invalidate caches so next poll picks up the new state.
            self._status_at = 0.0
            self._scan_at = 0.0
            status = self._active_connection()
            with self._connect_lock:
                self._connect_result = ConnectResult(
                    state="success",
                    message=status.ip or "connected",
                    ssid=ssid,
                )
        else:
            error = (result.stderr or result.stdout or "connection failed").strip()
            with self._connect_lock:
                self._connect_result = ConnectResult(
                    state="failed", message=error, ssid=ssid,
                )

    def _parse_scan(self, output: str, known: set[str]) -> list[WifiNetwork]:
        best: dict[str, WifiNetwork] = {}
        for line in output.strip().splitlines():
            if not line:
                continue
            # nmcli terse mode uses colon delimiter; escaped colons = \:
            parts = self._split_terse(line)
            if len(parts) < 4:
                continue
            in_use = parts[0].strip() == "*"
            ssid = parts[1].strip()
            if not ssid:
                continue
            try:
                signal = int(parts[2].strip())
            except ValueError:
                signal = 0
            security = parts[3].strip()
            network = WifiNetwork(
                ssid=ssid, signal=signal, security=security,
                active=in_use, known=ssid in known,
            )
            existing = best.get(ssid)
            if existing is None or network.signal > existing.signal:
                best[ssid] = network
        # Sort: active first, then by signal descending.
        return sorted(best.values(), key=lambda n: (not n.active, -n.signal))

    @staticmethod
    def _split_terse(line: str) -> list[str]:
        """Split an nmcli terse-mode line on unescaped colons."""
        parts: list[str] = []
        current: list[str] = []
        i = 0
        while i < len(line):
            if line[i] == "\\" and i + 1 < len(line) and line[i + 1] == ":":
                current.append(":")
                i += 2
            elif line[i] == ":":
                parts.append("".join(current))
                current = []
                i += 1
            else:
                current.append(line[i])
                i += 1
        parts.append("".join(current))
        return parts

    def _known_ssids(self) -> set[str]:
        if not self.available:
            return set()
        try:
            result = subprocess.run(
                [self._nmcli_path, "-t", "-f", "NAME", "connection", "show"],
                check=False, capture_output=True, text=True, timeout=3.0,
            )
        except (OSError, subprocess.TimeoutExpired):
            return set()
        if result.returncode != 0:
            return set()
        return {line.strip() for line in result.stdout.splitlines() if line.strip()}

    def _active_connection(self) -> WifiStatus:
        if not self.available:
            return WifiStatus()
        try:
            result = subprocess.run(
                [self._nmcli_path, "-t", "-f",
                 "DEVICE,TYPE,STATE,CONNECTION",
                 "device", "status"],
                check=False, capture_output=True, text=True, timeout=3.0,
            )
        except (OSError, subprocess.TimeoutExpired):
            return WifiStatus()
        if result.returncode != 0:
            return WifiStatus(state="error")
        for line in result.stdout.strip().splitlines():
            parts = self._split_terse(line)
            if len(parts) < 4:
                continue
            if parts[1].strip() != "wifi":
                continue
            state = parts[2].strip()
            ssid = parts[3].strip()
            connected = state == "connected"
            if not connected:
                return WifiStatus(state=state)
            ip = self._device_ip(parts[0].strip())
            signal = self._active_signal(ssid)
            return WifiStatus(
                connected=True, ssid=ssid, signal=signal, ip=ip, state="connected",
            )
        return WifiStatus(state="no-wifi-device")

    def _device_ip(self, device: str) -> str:
        try:
            result = subprocess.run(
                [self._nmcli_path, "-t", "-f", "IP4.ADDRESS", "device", "show", device],
                check=False, capture_output=True, text=True, timeout=3.0,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        for line in result.stdout.strip().splitlines():
            parts = self._split_terse(line)
            if len(parts) >= 2 and parts[1].strip():
                addr = parts[1].strip()
                # Strip CIDR prefix if present.
                return addr.split("/")[0]
        return ""

    def _active_signal(self, ssid: str) -> int:
        try:
            result = subprocess.run(
                [self._nmcli_path, "-t", "-f", "SSID,SIGNAL",
                 "device", "wifi", "list", "--rescan", "no"],
                check=False, capture_output=True, text=True, timeout=3.0,
            )
        except (OSError, subprocess.TimeoutExpired):
            return 0
        for line in result.stdout.strip().splitlines():
            parts = self._split_terse(line)
            if len(parts) >= 2 and parts[0].strip() == ssid:
                try:
                    return int(parts[1].strip())
                except ValueError:
                    pass
        return 0
