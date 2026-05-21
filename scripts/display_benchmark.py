#!/usr/bin/env python3
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from PIL import Image, ImageDraw

from altoids.config import load_config
from altoids.display import Display


@dataclass(slots=True)
class ProfileStats:
    draw_image: list[float] = field(default_factory=list)
    set_window: list[float] = field(default_factory=list)
    send_data: list[float] = field(default_factory=list)
    hat_display: list[float] = field(default_factory=list)
    draw_bytes: int = 0
    send_data_bytes: int = 0


@dataclass(slots=True)
class BenchmarkResult:
    label: str
    timings: list[float]
    profile: ProfileStats


def _ms(values: list[float]) -> list[float]:
    return [value * 1000.0 for value in values]


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((pct / 100.0) * (len(ordered) - 1))))
    return ordered[index]


def _timing_summary(timings: list[float]) -> dict[str, float]:
    milliseconds = _ms(timings)
    median = statistics.median(milliseconds) if milliseconds else 0.0
    return {
        "median_ms": median,
        "mean_ms": statistics.fmean(milliseconds) if milliseconds else 0.0,
        "p95_ms": _percentile(milliseconds, 95),
        "best_ms": min(milliseconds) if milliseconds else 0.0,
        "worst_ms": max(milliseconds) if milliseconds else 0.0,
        "fps": 1000.0 / median if median > 0 else 0.0,
    }


def _profile_summary(profile: ProfileStats) -> dict[str, float | int]:
    return {
        "draw_calls": len(profile.draw_image),
        "draw_bytes": profile.draw_bytes,
        "draw_median_ms": statistics.median(_ms(profile.draw_image)) if profile.draw_image else 0.0,
        "draw_mean_ms": statistics.fmean(_ms(profile.draw_image)) if profile.draw_image else 0.0,
        "set_window_calls": len(profile.set_window),
        "set_window_median_ms": statistics.median(_ms(profile.set_window)) if profile.set_window else 0.0,
        "send_data_calls": len(profile.send_data),
        "send_data_bytes": profile.send_data_bytes,
        "send_data_median_ms": statistics.median(_ms(profile.send_data)) if profile.send_data else 0.0,
        "send_data_mean_ms": statistics.fmean(_ms(profile.send_data)) if profile.send_data else 0.0,
        "hat_display_calls": len(profile.hat_display),
        "hat_display_median_ms": statistics.median(_ms(profile.hat_display)) if profile.hat_display else 0.0,
        "hat_display_mean_ms": statistics.fmean(_ms(profile.hat_display)) if profile.hat_display else 0.0,
    }


def _print_result(result: BenchmarkResult) -> None:
    timing = _timing_summary(result.timings)
    profile = _profile_summary(result.profile)
    print(
        f"{result.label:<24} "
        f"median={timing['median_ms']:>6.2f} ms "
        f"mean={timing['mean_ms']:>6.2f} ms "
        f"p95={timing['p95_ms']:>6.2f} ms "
        f"best={timing['best_ms']:>6.2f} ms "
        f"fps~{timing['fps']:>6.1f} "
        f"draws={profile['draw_calls']:>3} "
        f"bytes={profile['draw_bytes']:>8}"
    )
    if profile["draw_calls"]:
        print(
            f"{'':<24} "
            f"draw_med={profile['draw_median_ms']:>6.2f} ms "
            f"setwin_med={profile['set_window_median_ms']:>5.2f} ms "
            f"send_med={profile['send_data_median_ms']:>5.2f} ms "
            f"send_mean={profile['send_data_mean_ms']:>5.2f} ms "
            f"send_calls={profile['send_data_calls']:>3}"
        )
    if profile["hat_display_calls"]:
        spi_ms = profile["hat_display_median_ms"]
        prep_ms = max(0.0, timing["median_ms"] - spi_ms)
        print(
            f"{'':<24} "
            f"spi_med={spi_ms:>6.2f} ms "
            f"spi_mean={profile['hat_display_mean_ms']:>6.2f} ms "
            f"prep~{prep_ms:>5.2f} ms "
            f"spi_pct={spi_ms / timing['median_ms'] * 100.0 if timing['median_ms'] > 0 else 0:.0f}%"
        )


@contextmanager
def _profile_backend(display: Display) -> Iterator[ProfileStats]:
    backend = getattr(display, "_backend", None)
    profile = ProfileStats()
    if backend is None:
        yield profile
        return

    originals: dict[str, Callable] = {}

    def wrap(name: str, timings: list[float]) -> None:
        original = getattr(backend, name, None)
        if original is None:
            return
        originals[name] = original

        def profiled(*args, **kwargs):
            if name == "draw_image" and len(args) >= 5:
                profile.draw_bytes += len(args[4])
            if name == "_send_data" and args:
                profile.send_data_bytes += len(args[0])
            started = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                timings.append(time.perf_counter() - started)

        setattr(backend, name, profiled)

    wrap("set_window", profile.set_window)
    wrap("_send_data", profile.send_data)
    wrap("draw_image", profile.draw_image)
    wrap("display", profile.hat_display)
    try:
        yield profile
    finally:
        for name, original in originals.items():
            setattr(backend, name, original)


def _time_updates(display: Display, frames: list[Image.Image], iterations: int) -> BenchmarkResult:
    timings: list[float] = []
    with _profile_backend(display) as profile:
        for index in range(iterations):
            frame = frames[index % len(frames)]
            started = time.perf_counter()
            display.update(frame)
            timings.append(time.perf_counter() - started)
    return BenchmarkResult("", timings, profile)


def _solid_frames(width: int, height: int) -> list[Image.Image]:
    return [
        Image.new("RGB", (width, height), "#000000"),
        Image.new("RGB", (width, height), "#FFFFFF"),
    ]


def _single_region_frames(width: int, height: int, region_size: int) -> list[Image.Image]:
    base = Image.new("RGB", (width, height), "#050505")
    frames: list[Image.Image] = []
    for color in ("#F0D15A", "#2EC4B6"):
        frame = base.copy()
        draw = ImageDraw.Draw(frame)
        draw.rectangle((8, 8, 8 + region_size - 1, 8 + region_size - 1), fill=color)
        frames.append(frame)
    return frames


def _moving_region_frames(width: int, height: int, region_size: int) -> list[Image.Image]:
    base = Image.new("RGB", (width, height), "#050505")
    max_x = max(0, width - region_size - 8)
    max_y = max(0, height - region_size - 8)
    positions = [(8, 8), (max_x, 8), (max_x, max_y), (8, max_y)]
    frames: list[Image.Image] = []
    for index, (x, y) in enumerate(positions):
        frame = base.copy()
        draw = ImageDraw.Draw(frame)
        draw.rectangle((x, y, x + region_size - 1, y + region_size - 1), fill=("#F0D15A", "#2EC4B6", "#FF6B6B", "#9BC53D")[index])
        frames.append(frame)
    return frames


def _sparse_corner_frames(width: int, height: int, region_size: int) -> list[Image.Image]:
    base = Image.new("RGB", (width, height), "#050505")
    frames: list[Image.Image] = []
    for color in ("#F0D15A", "#2EC4B6"):
        frame = base.copy()
        draw = ImageDraw.Draw(frame)
        draw.rectangle((8, 8, 8 + region_size - 1, 8 + region_size - 1), fill=color)
        draw.rectangle(
            (width - region_size - 8, height - region_size - 8, width - 9, height - 9),
            fill=color,
        )
        frames.append(frame)
    return frames


def _noop_frames(width: int, height: int) -> list[Image.Image]:
    frame = Image.new("RGB", (width, height), "#050505")
    return [frame, frame]


def _checker_frames(width: int, height: int, block: int = 8) -> list[Image.Image]:
    frames: list[Image.Image] = []
    palettes = [
        ("#050505", "#202820"),
        ("#050505", "#F0D15A"),
    ]
    for color_a, color_b in palettes:
        frame = Image.new("RGB", (width, height), color_a)
        draw = ImageDraw.Draw(frame)
        for y in range(0, height, block):
            for x in range(0, width, block):
                if ((x // block) + (y // block)) % 2:
                    draw.rectangle((x, y, min(width - 1, x + block - 1), min(height - 1, y + block - 1)), fill=color_b)
        frames.append(frame)
    return frames


def _parse_int_list(value: str) -> list[int]:
    return [int(part.strip()) for part in value.split(",") if part.strip()]


def _parse_float_list(value: str) -> list[float]:
    return [float(part.strip()) for part in value.split(",") if part.strip()]


def _parse_optional_int_list(value: str) -> list[int | None]:
    speeds: list[int | None] = []
    for part in value.split(","):
        token = part.strip().lower()
        if not token:
            continue
        speeds.append(None if token in {"default", "none"} else int(token))
    return speeds


def _read_spi_speed(display: Display) -> int | None:
    backend = getattr(display, "_backend", None)
    if backend is None:
        return None
    # Try common SPI attribute paths across backends
    candidates = [
        getattr(backend, "spi", None),
        getattr(backend, "_spi", None),
    ]
    # displayhatmini: backend.st7789._spi
    st = getattr(backend, "st7789", None)
    if st is not None:
        candidates.append(getattr(st, "_spi", None))
    for spi in candidates:
        if spi is not None and hasattr(spi, "max_speed_hz"):
            return spi.max_speed_hz
    return None


def _set_spi_speed(display: Display, speed_hz: int) -> bool:
    backend = getattr(display, "_backend", None)
    if backend is None:
        return False
    candidates = [
        getattr(backend, "spi", None),
        getattr(backend, "_spi", None),
    ]
    st = getattr(backend, "st7789", None)
    if st is not None:
        candidates.append(getattr(st, "_spi", None))
    for spi in candidates:
        if spi is not None and hasattr(spi, "max_speed_hz"):
            spi.max_speed_hz = speed_hz
            return True
    return False


def _run_backlight_sweep(
    display: Display, levels: list[float], hold_seconds: float, width: int, height: int,
) -> None:
    # Test pattern: left half black, right half white, thin gradient strip in the middle
    pattern = Image.new("RGB", (width, height), "#000000")
    draw = ImageDraw.Draw(pattern)
    mid = width // 2
    strip_w = max(4, width // 10)
    draw.rectangle((mid + strip_w, 0, width - 1, height - 1), fill="#FFFFFF")
    for x in range(strip_w):
        gray = int(255 * x / strip_w)
        draw.line([(mid + x, 0), (mid + x, height - 1)], fill=(gray, gray, gray))
    # Add colored patches at the bottom
    patch_h = height // 5
    patch_w = width // 4
    colors = ["#FF0000", "#00FF00", "#0000FF", "#F0D15A"]
    for i, color in enumerate(colors):
        x0 = i * patch_w
        draw.rectangle((x0, height - patch_h, x0 + patch_w - 1, height - 1), fill=color)

    display.update(pattern)
    original_brightness = display.brightness

    print(f"\nbacklight sweep ({len(levels)} levels, {hold_seconds}s each):")
    for level in levels:
        display.set_backlight(level)
        print(f"  brightness={level:.2f}")
        time.sleep(hold_seconds)

    display.set_backlight(original_brightness)
    print(f"  restored brightness={original_brightness:.2f}")


def _build_display(config, spi_speed_hz: int | None) -> Display:
    return Display(
        config.display.width,
        config.display.height,
        config.display.backlight_brightness,
        backend=config.display.backend,
        rotation=config.display.rotation,
        driver_path=config.display_driver_path,
        transfer_quantization=config.display.transfer_quantization,
        spi_speed_hz=spi_speed_hz,
        split_dirty_regions=config.display.split_dirty_regions,
    )


def _run_case(display: Display, label: str, frames: list[Image.Image], iterations: int) -> BenchmarkResult:
    display.update(frames[-1])
    result = _time_updates(display, frames, iterations)
    result.label = label
    return result


def _run_suite_for_speed(config, spi_speed_hz: int | None, args) -> list[BenchmarkResult]:
    display = _build_display(config, spi_speed_hz)
    actual_spi = _read_spi_speed(display)
    print(
        f"\nbackend={display.backend_name} size={config.display.width}x{config.display.height}"
        f" spi_speed_hz={spi_speed_hz} actual_spi_hz={actual_spi}"
    )
    if display.backend_name == "mock":
        print("warning: mock backend does not measure SPI transfer performance")
        for backend, error in display.backend_init_errors.items():
            print(f"{backend} init failed: {error}")
        display.shutdown()
        if config.display.backend != "mock":
            raise SystemExit(2)

    width = config.display.width
    height = config.display.height
    results: list[BenchmarkResult] = []
    try:
        cases: list[tuple[str, list[Image.Image]]] = [
            ("noop", _noop_frames(width, height)),
            ("full-screen", _solid_frames(width, height)),
            ("checker-full", _checker_frames(width, height)),
        ]
        for size in args.region_sizes:
            cases.append((f"rect-{size}x{size}", _single_region_frames(width, height, size)))
        for size in args.moving_region_sizes:
            cases.append((f"moving-{size}x{size}", _moving_region_frames(width, height, size)))
        for size in args.sparse_region_sizes:
            cases.append((f"sparse-2x{size}", _sparse_corner_frames(width, height, size)))

        for label, frames in cases:
            result = _run_case(display, label, frames, args.iterations)
            results.append(result)
            _print_result(result)
            time.sleep(args.cooldown)
    finally:
        display.shutdown()
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark Altoids display transfer timings.")
    parser.add_argument("--config", default=None, help="Path to altoids.toml")
    parser.add_argument("--backend", default=None, help="Override display backend, e.g. whisplay, displayhatmini, mock")
    parser.add_argument("--iterations", type=int, default=30, help="Updates per test case")
    parser.add_argument("--cooldown", type=float, default=0.05, help="Pause between cases")
    parser.add_argument("--region-size", type=int, default=50, help="Dirty rectangle size for legacy single-case mode")
    parser.add_argument("--region-sizes", type=_parse_int_list, default=[1, 10, 25, 50, 100, 160], help="Comma-separated static rectangle sizes")
    parser.add_argument("--moving-region-sizes", type=_parse_int_list, default=[25, 50], help="Comma-separated moving rectangle sizes")
    parser.add_argument("--sparse-region-sizes", type=_parse_int_list, default=[10, 25], help="Comma-separated sparse two-corner rectangle sizes")
    parser.add_argument("--spi-speed-hz", type=int, default=None, help="Override configured SPI speed when the backend exposes it")
    parser.add_argument("--spi-speeds", type=_parse_optional_int_list, default=None, help="Comma-separated speed suite, e.g. default,62500000,80000000,100000000")
    parser.add_argument("--split-dirty-regions", action="store_true", help="Try splitting sparse dirty bboxes into multiple rectangles")
    parser.add_argument("--json", dest="json_path", default=None, help="Optional path to write machine-readable benchmark results")
    parser.add_argument("--width", type=int, default=None, help="Override config width (e.g. 320 for native HAT Mini)")
    parser.add_argument("--height", type=int, default=None, help="Override config height")
    parser.add_argument("--backlight-sweep", nargs="?", const="0.2,0.4,0.6,0.8,0.9,1.0", default=None,
                        help="Sweep backlight levels with test pattern (default levels: 0.2,0.4,0.6,0.8,0.9,1.0)")
    parser.add_argument("--backlight-hold", type=float, default=3.0, help="Seconds to hold each backlight level (default: 3)")
    parser.add_argument("--quick", action="store_true", help="Run only legacy full-screen and one rectangle case")
    parser.add_argument("--profile", action="store_true", help="Accepted for compatibility; profiling is always collected")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    if args.backend is not None:
        config.display.backend = args.backend
    if args.width is not None:
        config.display.width = args.width
    if args.height is not None:
        config.display.height = args.height
    if args.split_dirty_regions:
        config.display.split_dirty_regions = True
    if args.quick:
        args.region_sizes = [args.region_size]
        args.moving_region_sizes = []
        args.sparse_region_sizes = []

    speed_values = args.spi_speeds if args.spi_speeds is not None else [args.spi_speed_hz]
    json_results: list[dict[str, object]] = []
    for speed in speed_values:
        config.display.spi_speed_hz = speed
        results = _run_suite_for_speed(config, speed, args)
        json_results.extend(
            {
                "spi_speed_hz": speed,
                "label": result.label,
                **_timing_summary(result.timings),
                **_profile_summary(result.profile),
            }
            for result in results
        )

    if args.backlight_sweep is not None:
        levels = _parse_float_list(args.backlight_sweep)
        sweep_display = _build_display(config, speed_values[0] if speed_values else None)
        try:
            _run_backlight_sweep(
                sweep_display, levels, args.backlight_hold,
                config.display.width, config.display.height,
            )
        finally:
            sweep_display.shutdown()

    if args.json_path:
        output_path = Path(args.json_path).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(json_results, indent=2, sort_keys=True))
        print(f"\nwrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
