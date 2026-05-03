from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass, field


@dataclass(slots=True)
class WifiNetwork:
    ssid: str
    signal: int
    security: str
    active: bool = False

    @property
    def open(self) -> bool:
        return self.security in {"", "--"}


@dataclass(slots=True)
class WifiStatus:
    connected: bool = False
    ssid: str = ""
    signal: int = 0
    state: str = "offline"
    device: str = "wlan0"


@dataclass(slots=True)
class WifiManager:
    passwords: dict[str, str] = field(default_factory=dict)
    scan_cache_seconds: float = 15.0
    device: str = "wlan0"
    _cached_networks: list[WifiNetwork] = field(default_factory=list)
    _cached_status: WifiStatus | None = None
    _last_scan_at: float = 0.0
    _last_status_at: float = 0.0
    _last_message: str = "wifi idle"

    @property
    def available(self) -> bool:
        return shutil.which("nmcli") is not None

    @property
    def last_message(self) -> str:
        return self._last_message

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["nmcli", *args],
            text=True,
            capture_output=True,
        )

    def status(self) -> WifiStatus:
        if not self.available:
            return WifiStatus(device=self.device, state="nmcli missing")

        now = time.monotonic()
        if self._cached_status is not None and now - self._last_status_at < 2.0:
            return self._cached_status

        proc = self._run("-m", "multiline", "-f", "DEVICE,TYPE,STATE,CONNECTION,SIGNAL", "device", "show")
        if proc.returncode != 0:
            return WifiStatus(device=self.device, state="status error")

        for record in self._parse_multiline_records(proc.stdout):
            dev_type = record.get("GENERAL.TYPE", "")
            if dev_type != "802-11-wireless":
                continue
            state = record.get("GENERAL.STATE", "")
            connection = record.get("GENERAL.CONNECTION", "")
            signal = self._parse_int(record.get("AP1.SIGNAL", "0"))
            connected = "connected" in state
            status = WifiStatus(
                connected=connected,
                ssid="" if connection in {"--", ""} else connection,
                signal=signal,
                state=state.split(" ", 1)[-1] if " " in state else state,
                device=record.get("GENERAL.DEVICE", self.device),
            )
            self._cached_status = status
            self._last_status_at = now
            return status
        status = WifiStatus(device=self.device, state="wifi unavailable")
        self._cached_status = status
        self._last_status_at = now
        return status

    def scan(self, force: bool = False) -> list[WifiNetwork]:
        if not self.available:
            self._cached_networks = []
            self._last_message = "nmcli not installed"
            return []

        now = time.monotonic()
        if not force and self._cached_networks and now - self._last_scan_at < self.scan_cache_seconds:
            return self._cached_networks

        self._run("device", "wifi", "rescan")
        proc = self._run("-m", "multiline", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list")
        if proc.returncode != 0:
            self._last_message = "wifi scan failed"
            return self._cached_networks

        networks: list[WifiNetwork] = []
        seen: set[str] = set()
        for record in self._parse_multiline_records(proc.stdout):
            in_use = record.get("IN-USE", "")
            ssid = record.get("SSID", "")
            signal = record.get("SIGNAL", "0")
            security = record.get("SECURITY", "")
            if not ssid or ssid in seen:
                continue
            seen.add(ssid)
            networks.append(
                WifiNetwork(
                    ssid=ssid,
                    signal=self._parse_int(signal),
                    security=security,
                    active=in_use.strip() == "*",
                )
            )
        networks.sort(key=lambda item: (not item.active, -item.signal, item.ssid.lower()))
        self._cached_networks = networks
        self._last_scan_at = now
        self._last_message = f"scan ok {len(networks)} nets"
        return networks

    def connect(self, network: WifiNetwork) -> tuple[bool, str]:
        if not self.available:
            self._last_message = "nmcli not installed"
            return False, self._last_message

        args = ["device", "wifi", "connect", network.ssid]
        password = self.passwords.get(network.ssid)
        if password:
            args.extend(["password", password])
        elif not network.open:
            self._last_message = f"missing password for {network.ssid}"
            return False, self._last_message

        proc = self._run(*args)
        if proc.returncode == 0:
            self._last_message = f"connected {network.ssid}"
            self._cached_status = None
            self.scan(force=True)
            return True, self._last_message

        stderr = proc.stderr.strip() or proc.stdout.strip() or "wifi connect failed"
        self._last_message = stderr
        return False, stderr

    @staticmethod
    def _parse_multiline_records(output: str) -> list[dict[str, str]]:
        records: list[dict[str, str]] = []
        current: dict[str, str] = {}
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line:
                if current:
                    records.append(current)
                    current = {}
                continue
            key, _, value = line.partition(":")
            current[key.strip()] = value.strip()
        if current:
            records.append(current)
        return records

    @staticmethod
    def _parse_int(value: str) -> int:
        digits = "".join(char for char in value if char.isdigit())
        return int(digits or "0")
