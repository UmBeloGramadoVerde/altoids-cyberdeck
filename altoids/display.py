from __future__ import annotations

from pathlib import Path
import sys
import threading
import time

from PIL import Image, ImageChops

try:
    import numpy as np
except ModuleNotFoundError:  # pragma: no cover
    np = None


class Display:
    def __init__(
        self,
        width: int,
        height: int,
        brightness: float = 1.0,
        backend: str = "auto",
        rotation: int = 0,
        driver_path: Path | None = None,
    ) -> None:
        self.width = width
        self.height = height
        self.brightness = brightness
        self.rotation = rotation % 360
        self.driver_path = driver_path
        self._backend = None
        self._backend_name = "mock"
        self._mock_output_dir = Path("artifacts")
        self._last_mock_save_at = 0.0
        self._driver = None
        self._last_whisplay_frame: Image.Image | None = None
        self._last_whisplay_array = None
        self._standby = False
        self._led_generation = 0
        self._led_lock = threading.Lock()

        backend_name = backend.lower()
        candidates = [backend_name] if backend_name != "auto" else ["whisplay", "displayhatmini"]
        for candidate in candidates:
            if candidate == "whisplay" and self._init_whisplay():
                break
            if candidate == "displayhatmini" and self._init_displayhatmini():
                break

    def _init_displayhatmini(self) -> bool:
        try:
            import displayhatmini
        except ModuleNotFoundError:
            return False
        self._driver = displayhatmini
        self._backend = displayhatmini.DisplayHATMini()
        self._backend_name = "displayhatmini"
        self._backend.set_backlight(self.brightness)
        return True

    def _init_whisplay(self) -> bool:
        if self.driver_path is not None and self.driver_path.exists():
            driver_root = str(self.driver_path)
            if driver_root not in sys.path:
                sys.path.insert(0, driver_root)
        try:
            from WhisPlay import WhisPlayBoard
        except ModuleNotFoundError:
            return False
        self._driver = WhisPlayBoard
        self._backend = WhisPlayBoard()
        self._backend_name = "whisplay"
        self._backend.set_backlight(int(self.brightness * 100))
        return True

    def update(self, image: Image.Image) -> None:
        if self._standby:
            return
        if self._backend_name == "whisplay":
            frame = self._prepare_whisplay_frame(image)
            bbox = self._dirty_bbox(frame)
            if bbox is None:
                return
            left, top, right, bottom = bbox
            region = frame.crop(bbox)
            self._backend.draw_image(
                left,
                top,
                right - left,
                bottom - top,
                self._rgb565_bytes(region),
            )
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
        if self._backend_name == "whisplay":
            self._backend.set_backlight(int(self.brightness * 100))
        elif self._backend is not None:
            self._backend.set_backlight(self.brightness)

    @property
    def is_whisplay(self) -> bool:
        return self._backend_name == "whisplay"

    @property
    def supports_led(self) -> bool:
        return self.is_whisplay and self._backend is not None and hasattr(self._backend, "set_rgb")

    @property
    def supports_audio(self) -> bool:
        return self.is_whisplay

    def pulse_led(self, color: tuple[int, int, int], duration_ms: int = 200, brightness: float = 1.0) -> None:
        if not self.supports_led or self._standby:
            return
        scaled = tuple(max(0, min(255, int(channel * max(0.0, min(1.0, brightness))))) for channel in color)
        with self._led_lock:
            self._led_generation += 1
            generation = self._led_generation

        def worker() -> None:
            try:
                self._backend.set_rgb(*scaled)
                time.sleep(max(0.02, duration_ms / 1000.0))
                with self._led_lock:
                    if generation != self._led_generation:
                        return
                self._backend.set_rgb(0, 0, 0)
            except Exception:
                return

        threading.Thread(target=worker, daemon=True).start()

    def clear_led(self) -> None:
        if not self.supports_led:
            return
        with self._led_lock:
            self._led_generation += 1
        try:
            self._backend.set_rgb(0, 0, 0)
        except Exception:
            return

    def enter_standby(self) -> None:
        self._standby = True
        self.clear_led()
        self.set_backlight(0.0)

    def exit_standby(self, backlight_brightness: float | None = None) -> None:
        self._standby = False
        if backlight_brightness is not None:
            self.set_backlight(backlight_brightness)

    def shutdown(self) -> None:
        self.clear_led()
        if self._backend is not None and hasattr(self._backend, "cleanup"):
            try:
                self._backend.cleanup()
            except Exception:
                return

    @staticmethod
    def _rgb565_bytes(image: Image.Image) -> bytes:
        if np is not None:
            rgb = np.asarray(image, dtype=np.uint8)
            rgb565 = (
                ((rgb[..., 0].astype(np.uint16) & 0xF8) << 8)
                | ((rgb[..., 1].astype(np.uint16) & 0xFC) << 3)
                | (rgb[..., 2].astype(np.uint16) >> 3)
            )
            return rgb565.byteswap().tobytes()
        data = bytearray()
        for red, green, blue in image.getdata():
            rgb565 = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
            data.extend(((rgb565 >> 8) & 0xFF, rgb565 & 0xFF))
        return bytes(data)

    def _apply_rotation(self, image: Image.Image) -> Image.Image:
        if self.rotation == 90:
            return image.transpose(Image.Transpose.ROTATE_90)
        if self.rotation == 180:
            return image.transpose(Image.Transpose.ROTATE_180)
        if self.rotation == 270:
            return image.transpose(Image.Transpose.ROTATE_270)
        if self.rotation:
            return image.rotate(self.rotation, expand=True)
        return image

    def _prepare_whisplay_frame(self, image: Image.Image) -> Image.Image:
        frame = image if image.mode == "RGB" else image.convert("RGB")
        frame = self._apply_rotation(frame)
        size = (self._backend.LCD_WIDTH, self._backend.LCD_HEIGHT)
        if frame.size != size:
            frame = frame.resize(size)
        return frame

    def _dirty_bbox(self, frame: Image.Image) -> tuple[int, int, int, int] | None:
        full_bounds = (0, 0, frame.width, frame.height)
        if np is not None:
            current = np.asarray(frame, dtype=np.uint8)
            previous = self._last_whisplay_array
            if previous is None or previous.shape != current.shape:
                self._last_whisplay_array = current.copy()
                self._last_whisplay_frame = None
                return full_bounds
            changed = np.any(current != previous, axis=2)
            if not changed.any():
                return None
            ys, xs = np.nonzero(changed)
            self._last_whisplay_array = current.copy()
            self._last_whisplay_frame = None
            return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)

        previous_frame = self._last_whisplay_frame
        if previous_frame is None or previous_frame.size != frame.size:
            self._last_whisplay_frame = frame.copy()
            return full_bounds
        bbox = ImageChops.difference(frame, previous_frame).getbbox()
        self._last_whisplay_frame = frame.copy()
        return bbox
