from __future__ import annotations

from dataclasses import dataclass
import subprocess
import time


@dataclass(slots=True)
class BluetoothStatus:
    connected: bool = False
    device_name: str = ""


class BluetoothMonitor:
    def __init__(self, poll_interval_seconds: float = 0.5) -> None:
        self.status = BluetoothStatus()
        self.available = True
        self._last_poll_at = 0.0
        self.poll_interval_seconds = max(0.1, poll_interval_seconds)

    def poll(self) -> BluetoothStatus:
        now = time.monotonic()
        if now - self._last_poll_at < self.poll_interval_seconds:
            return self.status
        self._last_poll_at = now
        try:
            result = subprocess.run(
                ["bluetoothctl", "devices", "Connected"],
                check=False,
                capture_output=True,
                text=True,
                timeout=1.0,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            self.available = False
            self.status = BluetoothStatus()
            return self.status

        self.available = result.returncode == 0
        if not self.available:
            self.status = BluetoothStatus()
            return self.status

        devices = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line.startswith("Device "):
                continue
            parts = line.split(maxsplit=2)
            if len(parts) < 3:
                continue
            devices.append(parts[2])
        self.status = BluetoothStatus(
            connected=bool(devices),
            device_name=devices[0] if devices else "",
        )
        return self.status
