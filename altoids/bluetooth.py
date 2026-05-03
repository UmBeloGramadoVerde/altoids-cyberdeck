from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class BluetoothStatus:
    connected: bool = False
    device_name: str = ""


class BluetoothMonitor:
    def __init__(self) -> None:
        self.status = BluetoothStatus()
        try:
            import gi  # noqa: F401
        except ModuleNotFoundError:
            self.available = False
        else:
            self.available = True

    def poll(self) -> BluetoothStatus:
        return self.status
