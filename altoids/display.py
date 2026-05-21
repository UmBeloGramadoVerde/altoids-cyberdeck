from __future__ import annotations

import os
from pathlib import Path
import sys
import threading
import time
import traceback

from PIL import Image, ImageChops

try:
    import numpy as np
except ModuleNotFoundError:  # pragma: no cover
    np = None


class Display:
    _DIRTY_TILE_SIZE = 16
    _MAX_DIRTY_REGIONS = 8
    _SPLIT_DIRTY_MIN_AREA = 4096
    _SPLIT_DIRTY_MIN_SAVINGS = 0.25

    def __init__(
        self,
        width: int,
        height: int,
        brightness: float = 1.0,
        backend: str = "auto",
        rotation: int = 0,
        driver_path: Path | None = None,
        transfer_quantization: str = "rgb565",
        spi_speed_hz: int | None = None,
        split_dirty_regions: bool = False,
    ) -> None:
        self.width = width
        self.height = height
        self.brightness = brightness
        self.rotation = rotation % 360
        self.driver_path = driver_path
        self.transfer_quantization = transfer_quantization.lower()
        self.spi_speed_hz = spi_speed_hz
        self.split_dirty_regions = split_dirty_regions
        self._backend = None
        self._backend_name = "mock"
        self._backend_init_errors: dict[str, str] = {}
        self._mock_output_dir = self._resolve_mock_output_dir()
        self._last_mock_save_at = 0.0
        self._driver = None
        self._last_frame: Image.Image | None = None
        self._flip_180 = True
        self._standby = False
        self._led_generation = 0
        self._led_lock = threading.Lock()

        backend_name = backend.lower()
        candidates = [backend_name] if backend_name != "auto" else ["displayhatmini", "whisplay"]
        for candidate in candidates:
            if candidate == "whisplay" and self._init_whisplay():
                break
            if candidate == "displayhatmini" and self._init_displayhatmini():
                break

    def _init_displayhatmini(self) -> bool:
        try:
            import displayhatmini
        except ModuleNotFoundError as exc:
            self._backend_init_errors["displayhatmini"] = f"{type(exc).__name__}: {exc}"
            return False
        try:
            self._driver = displayhatmini
            hat_w = displayhatmini.DisplayHATMini.WIDTH
            hat_h = displayhatmini.DisplayHATMini.HEIGHT
            self._displayhatmini_buffer = Image.new("RGB", (hat_w, hat_h))
            self._backend = displayhatmini.DisplayHATMini(self._displayhatmini_buffer)
            self._backend_name = "displayhatmini"
            self._apply_spi_speed()
            self._backend.set_backlight(self.brightness)
            return True
        except Exception as exc:
            self._backend_init_errors["displayhatmini"] = self._format_init_error(exc)
            self._backend = None
            self._backend_name = "mock"
            return False

    def _init_whisplay(self) -> bool:
        driver_path = self._resolve_whisplay_driver_path()
        if driver_path is not None:
            driver_root = str(driver_path)
            if driver_root not in sys.path:
                sys.path.insert(0, driver_root)
        WhisPlayBoard = None
        # Try new layout first (runtime/whisplay.py -> WhisplayBoard)
        try:
            from whisplay import WhisplayBoard as WhisPlayBoard
        except ModuleNotFoundError:
            pass
        # Fall back to old layout (Driver/WhisPlay.py -> WhisPlayBoard)
        if WhisPlayBoard is None:
            try:
                from WhisPlay import WhisPlayBoard
            except ModuleNotFoundError as exc:
                searched = str(driver_path) if driver_path is not None else "no existing driver_path"
                self._backend_init_errors["whisplay"] = f"{type(exc).__name__}: {exc}; searched {searched}"
                return False
        try:
            self._driver = WhisPlayBoard
            self._backend = WhisPlayBoard()
            self._backend_name = "whisplay"
            self._apply_spi_speed()
            self._backend.set_backlight(int(self.brightness * 100))
            return True
        except Exception as exc:
            self._backend_init_errors["whisplay"] = self._format_init_error(exc)
            self._backend = None
            self._backend_name = "mock"
            return False

    def update(self, image: Image.Image) -> None:
        if self._standby:
            return
        if self._backend_name == "whisplay":
            frame = self._prepare_whisplay_frame(image)
            regions = self._dirty_regions(frame)
            if not regions:
                return
            for left, top, right, bottom in regions:
                payload = self._rgb565_bytes(frame.crop((left, top, right, bottom)))
                self._backend.draw_image(left, top, right - left, bottom - top, payload)
            return
        if self._backend is not None:
            if self._backend_name == "displayhatmini":
                frame = image if image.mode == "RGB" else image.convert("RGB")
                if self._flip_180:
                    frame = frame.transpose(Image.Transpose.ROTATE_180)
                hat_w = self._displayhatmini_buffer.width
                hat_h = self._displayhatmini_buffer.height
                if frame.size != (hat_w, hat_h):
                    frame = frame.resize((hat_w, hat_h))
                regions = self._dirty_regions(frame)
                if not regions:
                    return
                st = self._backend.st7789
                for left, top, right, bottom in regions:
                    payload = self._rgb565_bytes(frame.crop((left, top, right, bottom)))
                    st.set_window(left, top, right - 1, bottom - 1)
                    st.data(payload)
            else:
                self._backend.display(image)
            return
        now = time.monotonic()
        if now - self._last_mock_save_at < 0.5:
            return
        self._last_mock_save_at = now
        self._mock_output_dir.mkdir(parents=True, exist_ok=True)
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
    def backend_name(self) -> str:
        return self._backend_name

    @property
    def backend_init_errors(self) -> dict[str, str]:
        return dict(self._backend_init_errors)

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

    def set_flip_180(self, flip: bool) -> None:
        if flip == self._flip_180:
            return
        self._flip_180 = flip
        self._last_frame = None

    def read_buttons(self) -> dict[str, bool]:
        """Return current state of hardware buttons (True = pressed)."""
        if self._backend_name != "displayhatmini" or self._backend is None:
            return {}
        try:
            cls = self._driver.DisplayHATMini
            return {
                "A": self._backend.read_button(cls.BUTTON_A),
                "B": self._backend.read_button(cls.BUTTON_B),
                "X": self._backend.read_button(cls.BUTTON_X),
                "Y": self._backend.read_button(cls.BUTTON_Y),
            }
        except Exception:
            return {}

    def shutdown(self) -> None:
        self.clear_led()
        if self._backend is not None and hasattr(self._backend, "cleanup"):
            try:
                self._backend.cleanup()
            except Exception:
                return

    def _resolve_mock_output_dir(self) -> Path:
        explicit = os.environ.get("ALTOIDS_ARTIFACTS_DIR")
        if explicit:
            return Path(explicit).expanduser()
        repo_relative = Path.cwd() / "artifacts"
        if os.access(Path.cwd(), os.W_OK):
            return repo_relative
        xdg_state_home = os.environ.get("XDG_STATE_HOME")
        if xdg_state_home:
            return Path(xdg_state_home).expanduser() / "altoids" / "artifacts"
        return Path.home() / ".local" / "state" / "altoids" / "artifacts"

    def _resolve_whisplay_driver_path(self) -> Path | None:
        candidates: list[Path] = []
        if self.driver_path is not None:
            candidates.append(self.driver_path)
        # New repo layout: runtime/whisplay.py
        candidates.append(Path("/opt/altoids/vendor/Whisplay/runtime"))
        # Old repo layout: Driver/WhisPlay.py
        candidates.append(Path("/opt/altoids/vendor/Whisplay/Driver"))
        for candidate in candidates:
            if (candidate / "whisplay.py").exists() or (candidate / "WhisPlay.py").exists():
                return candidate
        return None

    @staticmethod
    def _format_init_error(exc: Exception) -> str:
        trace = traceback.format_exception(type(exc), exc, exc.__traceback__)
        return "".join(trace[-8:]).strip() or type(exc).__name__

    def _rgb565_bytes(self, image: Image.Image) -> bytes:
        if np is not None:
            return self._rgb565_region_bytes(self._rgb_array(image))
        data = bytearray()
        for red, green, blue in image.getdata():
            red, green, blue = self._quantize_rgb_triplet(red, green, blue)
            rgb565 = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
            data.extend(((rgb565 >> 8) & 0xFF, rgb565 & 0xFF))
        return bytes(data)

    def _rgb_array(self, image: Image.Image):
        rgb = np.asarray(image, dtype=np.uint8)
        return self._quantize_rgb_array(rgb)

    def _rgb565_array(self, image: Image.Image):
        return self._rgb565_from_rgb_region(self._rgb_array(image))

    @staticmethod
    def _rgb565_from_rgb_region(region):
        return (
            ((region[..., 0].astype(np.uint16) & 0xF8) << 8)
            | ((region[..., 1].astype(np.uint16) & 0xFC) << 3)
            | (region[..., 2].astype(np.uint16) >> 3)
        )

    @staticmethod
    def _rgb565_region_bytes(region) -> bytes:
        return Display._rgb565_from_rgb_region(region).byteswap().tobytes()

    def _quantize_rgb_array(self, rgb):
        if self.transfer_quantization != "rgb332":
            return rgb
        quantized = rgb.copy()
        quantized[..., 0] &= 0xE0
        quantized[..., 1] &= 0xE0
        quantized[..., 2] &= 0xC0
        return quantized

    def _quantize_rgb_triplet(self, red: int, green: int, blue: int) -> tuple[int, int, int]:
        if self.transfer_quantization != "rgb332":
            return red, green, blue
        return red & 0xE0, green & 0xE0, blue & 0xC0

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

    def _dirty_regions(self, frame: Image.Image) -> list[tuple[int, int, int, int]]:
        full_bounds = (0, 0, frame.width, frame.height)
        previous_frame = self._last_frame
        if previous_frame is None or previous_frame.size != frame.size:
            self._last_frame = frame.copy()
            return [full_bounds]
        difference = ImageChops.difference(frame, previous_frame)
        bbox = difference.getbbox()
        self._last_frame = frame.copy()
        if bbox is None:
            return []
        if self.split_dirty_regions:
            split_regions = self._split_dirty_bbox(difference, bbox)
            if split_regions is not None:
                return split_regions
        return [bbox]

    def _split_dirty_bbox(
        self,
        difference: Image.Image,
        bbox: tuple[int, int, int, int],
    ) -> list[tuple[int, int, int, int]] | None:
        if np is None:
            return None
        left, top, right, bottom = bbox
        bbox_area = (right - left) * (bottom - top)
        if bbox_area < self._SPLIT_DIRTY_MIN_AREA:
            return None

        diff_region = np.asarray(difference.crop(bbox), dtype=np.uint8)
        changed = np.any(diff_region != 0, axis=2)
        regions = self._coalesce_dirty_regions(changed, offset=(left, top))
        if not regions or len(regions) > self._MAX_DIRTY_REGIONS:
            return None

        split_area = sum((region[2] - region[0]) * (region[3] - region[1]) for region in regions)
        if split_area >= bbox_area * (1.0 - self._SPLIT_DIRTY_MIN_SAVINGS):
            return None
        return regions

    def _coalesce_dirty_regions(self, changed, offset: tuple[int, int] = (0, 0)) -> list[tuple[int, int, int, int]]:
        tile = self._DIRTY_TILE_SIZE
        offset_x, offset_y = offset
        height, width = changed.shape
        tile_rows = (height + tile - 1) // tile
        tile_cols = (width + tile - 1) // tile
        spans_by_row: list[list[tuple[int, int]]] = []
        for tile_y in range(tile_rows):
            row_spans: list[tuple[int, int]] = []
            run_start: int | None = None
            y0 = tile_y * tile
            y1 = min(height, y0 + tile)
            for tile_x in range(tile_cols):
                x0 = tile_x * tile
                x1 = min(width, x0 + tile)
                active = bool(changed[y0:y1, x0:x1].any())
                if active and run_start is None:
                    run_start = tile_x
                elif not active and run_start is not None:
                    row_spans.append((run_start, tile_x))
                    run_start = None
            if run_start is not None:
                row_spans.append((run_start, tile_cols))
            spans_by_row.append(row_spans)

        open_regions: dict[tuple[int, int], list[int]] = {}
        merged: list[tuple[int, int, int, int]] = []
        for tile_y, spans in enumerate(spans_by_row):
            next_regions: dict[tuple[int, int], list[int]] = {}
            for span in spans:
                existing = open_regions.pop(span, None)
                if existing is None:
                    next_regions[span] = [span[0], tile_y, span[1], tile_y + 1]
                else:
                    existing[3] = tile_y + 1
                    next_regions[span] = existing
            merged.extend(
                (
                    offset_x + left * tile,
                    offset_y + top * tile,
                    offset_x + min(width, right * tile),
                    offset_y + min(height, bottom * tile),
                )
                for left, top, right, bottom in open_regions.values()
            )
            open_regions = next_regions

        merged.extend(
            (
                offset_x + left * tile,
                offset_y + top * tile,
                offset_x + min(width, right * tile),
                offset_y + min(height, bottom * tile),
            )
            for left, top, right, bottom in open_regions.values()
        )
        if len(merged) <= self._MAX_DIRTY_REGIONS:
            return merged

        ys, xs = np.nonzero(changed)
        return [
            (
                offset_x + int(xs.min()),
                offset_y + int(ys.min()),
                offset_x + int(xs.max()) + 1,
                offset_y + int(ys.max()) + 1,
            )
        ]

    def _apply_spi_speed(self) -> None:
        if self.spi_speed_hz is None or self._backend is None:
            return
        candidates = [getattr(self._backend, "spi", None)]
        st = getattr(self._backend, "st7789", None)
        if st is not None:
            candidates.append(getattr(st, "_spi", None))
        for spi in candidates:
            if spi is not None and hasattr(spi, "max_speed_hz"):
                spi.max_speed_hz = int(self.spi_speed_hz)
                return
