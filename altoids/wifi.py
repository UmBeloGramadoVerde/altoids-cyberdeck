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
    bssid: str = ""
    channel: str = ""

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
    _last_error: str = ""

    @property
    def available(self) -> bool:
        return shutil.which("nmcli") is not None

    @property
    def last_message(self) -> str:
        return self._last_message

    @property
    def last_error(self) -> str:
        return self._last_error

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        command = ["nmcli", *args]
        try:
            return subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return subprocess.CompletedProcess(command, 1, "", str(exc))

    def status(self, allow_refresh: bool = True) -> WifiStatus:
        if not self.available:
            return WifiStatus(device=self.device, state="nmcli missing")

        now = time.monotonic()
        if self._cached_status is not None and now - self._last_status_at < 2.0:
            return self._cached_status
        if not allow_refresh and self._cached_status is not None:
            return self._cached_status

        proc = self._run("-m", "multiline", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "show")
        if proc.returncode != 0:
            self._last_error = self._command_error(proc, "wifi status failed")
            return WifiStatus(device=self.device, state="status error")

        for record in self._parse_multiline_records(proc.stdout):
            dev_type = record.get("GENERAL.TYPE", "")
            if dev_type != "802-11-wireless":
                continue
            state = record.get("GENERAL.STATE", "")
            connection = record.get("GENERAL.CONNECTION", "")
            connected = "connected" in state
            active_network = self._active_network_from_nmcli() if connected else None
            status = WifiStatus(
                connected=connected,
                ssid=active_network.ssid if active_network is not None else self._clean_network_name(connection),
                signal=active_network.signal if active_network is not None else 0,
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

    def scan(self, force: bool = False, allow_refresh: bool = True) -> list[WifiNetwork]:
        if not self.available:
            self._cached_networks = []
            self._last_message = "nmcli not installed"
            self._last_error = self._last_message
            return []

        now = time.monotonic()
        if not force and self._cached_networks and now - self._last_scan_at < self.scan_cache_seconds:
            return self._cached_networks
        if not allow_refresh:
            return self._cached_networks

        self._run("radio", "wifi", "on")
        proc = self._list_wifi(rescan=force)
        if proc.returncode != 0:
            self._last_error = self._command_error(proc, "wifi scan failed")
            self._last_message = self._last_error
            return self._cached_networks

        networks: list[WifiNetwork] = []
        for record in self._parse_multiline_records(proc.stdout):
            in_use = record.get("IN-USE", "")
            ssid = self._clean_network_name(record.get("SSID", ""))
            signal = record.get("SIGNAL", "0")
            security = record.get("SECURITY", "")
            if not ssid:
                continue
            networks.append(
                WifiNetwork(
                    ssid=ssid,
                    signal=self._parse_int(signal),
                    security=security,
                    active=in_use.strip() == "*",
                    bssid=record.get("BSSID", ""),
                    channel=record.get("CHAN", ""),
                )
            )
        networks.sort(key=lambda item: (not item.active, -item.signal, item.ssid.lower(), item.bssid))
        self._cached_networks = networks
        self._last_scan_at = now
        self._last_message = f"scan ok {len(networks)} nets"
        self._last_error = ""
        return networks

    def connect(self, network: WifiNetwork, password: str | None = None) -> tuple[bool, str]:
        if not self.available:
            self._last_message = "nmcli not installed"
            self._last_error = self._last_message
            return False, self._last_message

        self._run("radio", "wifi", "on")
        args = ["device", "wifi", "connect", network.ssid]
        chosen_password = password if password is not None else self.passwords.get(network.ssid)
        if chosen_password:
            args.extend(["password", chosen_password])
        elif not network.open:
            self._last_message = f"missing password for {network.ssid}"
            self._last_error = self._last_message
            return False, self._last_message

        proc = self._run(*args)
        if proc.returncode == 0:
            if chosen_password:
                self.passwords[network.ssid] = chosen_password
            self._last_message = f"connected {network.ssid}"
            self._last_error = ""
            self._cached_status = None
            self.scan(force=True)
            return True, self._last_message

        stderr = self._command_error(proc, "wifi connect failed")
        self._last_message = stderr
        self._last_error = stderr
        return False, stderr

    def _active_network_from_nmcli(self) -> WifiNetwork | None:
        proc = self._run("-m", "multiline", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "no")
        if proc.returncode != 0:
            return None
        for record in self._parse_multiline_records(proc.stdout):
            if record.get("IN-USE", "").strip() != "*":
                continue
            ssid = self._clean_network_name(record.get("SSID", ""))
            if not ssid:
                continue
            return WifiNetwork(
                ssid=ssid,
                signal=self._parse_int(record.get("SIGNAL", "0")),
                security=record.get("SECURITY", ""),
                active=True,
            )
        return None

    def _list_wifi(self, *, rescan: bool) -> subprocess.CompletedProcess[str]:
        args = [
            "-m",
            "multiline",
            "-f",
            "IN-USE,BSSID,SSID,CHAN,SIGNAL,SECURITY",
            "device",
            "wifi",
            "list",
            "--rescan",
            "yes" if rescan else "no",
        ]
        return self._run(*args)

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

    @staticmethod
    def _clean_network_name(value: str) -> str:
        value = value.strip()
        return "" if value in {"", "--"} else value

    @staticmethod
    def _command_error(proc: subprocess.CompletedProcess[str], fallback: str) -> str:
        message = proc.stderr.strip() or proc.stdout.strip() or fallback
        return " ".join(message.split())
