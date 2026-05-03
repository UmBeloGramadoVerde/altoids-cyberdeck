from __future__ import annotations

from pathlib import Path
import time

from PIL import Image


class Display:
    def __init__(self, width: int, height: int, brightness: float = 1.0, simulator: object | None = None) -> None:
        self.width = width
        self.height = height
        self.brightness = brightness
        self._backend = None
        self._simulator = simulator
        self._mock_output_dir = Path("artifacts")
        self._last_mock_save_at = 0.0
        if self._simulator is not None:
            return
        try:
            import displayhatmini
        except ModuleNotFoundError:
            self._driver = None
        else:
            self._driver = displayhatmini
            self._backend = displayhatmini.DisplayHATMini()
            self._backend.set_backlight(brightness)

    def update(self, image: Image.Image) -> None:
        if self._simulator is not None:
            self._simulator.update(image)
            return
        if self._backend is not None:
            self._backend.display(image)
            return
        now = time.monotonic()
        if now - self._last_mock_save_at < 0.5:
            return
        self._last_mock_save_at = now
        self._mock_output_dir.mkdir(exist_ok=True)
        image.save(self._mock_output_dir / "last-frame.png")

    def set_backlight(self, value: float) -> None:
        self.brightness = max(0.0, min(1.0, value))
        if self._simulator is not None:
            self._simulator.set_backlight(self.brightness)
            return
        if self._backend is not None:
            self._backend.set_backlight(self.brightness)
