#!/usr/bin/env python3
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Callable, Iterator

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from altoids.app import AltoidsApp
from altoids.config import load_config
from altoids.input_keyboard import KeyboardEvent


@contextmanager
def _profile_display_update(app: AltoidsApp) -> Iterator[dict[str, float | int]]:
    profile: dict[str, float | int] = {
        "display_update_ms": 0.0,
        "draw_calls": 0,
        "draw_bytes": 0,
        "snapshot_age_ms": -1.0,
    }
    original_update = app.display.update
    backend = getattr(app.display, "_backend", None)
    original_draw_image: Callable | None = getattr(backend, "draw_image", None) if backend is not None else None

    def profiled_update(*args, **kwargs):
        started = time.perf_counter()
        try:
            return original_update(*args, **kwargs)
        finally:
            profile["display_update_ms"] = (time.perf_counter() - started) * 1000.0

    def profiled_draw_image(*args, **kwargs):
        profile["draw_calls"] = int(profile["draw_calls"]) + 1
        if len(args) >= 5:
            profile["draw_bytes"] = int(profile["draw_bytes"]) + len(args[4])
        return original_draw_image(*args, **kwargs)

    app.display.update = profiled_update
    if backend is not None and original_draw_image is not None:
        setattr(backend, "draw_image", profiled_draw_image)
    try:
        cache = getattr(app, "_system_snapshot_cache", None)
        if cache is not None:
            profile["snapshot_age_ms"] = (time.monotonic() - cache.captured_at) * 1000.0
        yield profile
    finally:
        app.display.update = original_update
        if backend is not None and original_draw_image is not None:
            setattr(backend, "draw_image", original_draw_image)


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((pct / 100.0) * (len(ordered) - 1))))
    return ordered[index]


def _summary(values: list[float]) -> dict[str, float]:
    median = statistics.median(values)
    return {
        "median_ms": median,
        "mean_ms": statistics.fmean(values),
        "p95_ms": _percentile(values, 95),
        "best_ms": min(values),
        "worst_ms": max(values),
    }


def _print_summary(label: str, values: list[float]) -> None:
    summary = _summary(values)
    print(
        f"{label}: "
        f"median={summary['median_ms']:.2f} ms "
        f"mean={summary['mean_ms']:.2f} ms "
        f"p95={summary['p95_ms']:.2f} ms "
        f"best={summary['best_ms']:.2f} ms "
        f"worst={summary['worst_ms']:.2f} ms"
    )


def _loop_wait_samples(poll_interval_ms: float, samples: int) -> list[float]:
    if samples <= 1:
        return [poll_interval_ms]
    return [poll_interval_ms * index / (samples - 1) for index in range(samples)]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Measure synthetic keyboard-event to display-update latency.")
    parser.add_argument("--config", default=None, help="Path to altoids.toml")
    parser.add_argument("--backend", default=None, help="Override display backend, e.g. whisplay or mock")
    parser.add_argument("--iterations", type=int, default=50)
    parser.add_argument("--warmup", type=int, default=5, help="Discard this many initial samples")
    parser.add_argument("--settle", type=float, default=0.05, help="Pause between injected events")
    parser.add_argument("--json", dest="json_path", default=None, help="Optional path for machine-readable results")
    parser.add_argument("--loop-samples", type=int, default=101, help="Samples for modeled polling wait distribution")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    if args.backend is not None:
        config.display.backend = args.backend

    app = AltoidsApp(config=config)
    timings: list[float] = []
    samples: list[dict[str, float | int]] = []
    warmup_timings: list[float] = []
    try:
        app.render()
        event = KeyboardEvent(key="meta", raw_key="KEY_LEFTMETA")
        total_iterations = args.iterations + args.warmup
        for index in range(total_iterations):
            time.sleep(args.settle)
            started = time.perf_counter()
            app.handle_keyboard_event(event)
            if app.needs_redraw:
                with _profile_display_update(app) as profile:
                    app.render()
            else:
                profile = {"display_update_ms": 0.0, "draw_calls": 0, "draw_bytes": 0}
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            if index < args.warmup:
                warmup_timings.append(elapsed_ms)
            else:
                timings.append(elapsed_ms)
                samples.append(
                    {
                        "elapsed_ms": elapsed_ms,
                        "display_update_ms": float(profile["display_update_ms"]),
                        "draw_calls": int(profile["draw_calls"]),
                        "draw_bytes": int(profile["draw_bytes"]),
                        "snapshot_age_ms": float(profile["snapshot_age_ms"]),
                    }
                )
    finally:
        app.close()

    if warmup_timings:
        _print_summary("warmup discarded", warmup_timings)
    _print_summary("synthetic key-to-display", timings)
    worst_samples = sorted(samples, key=lambda sample: float(sample["elapsed_ms"]), reverse=True)[:5]
    if worst_samples:
        print("worst samples:")
        for sample in worst_samples:
            print(
                f"  elapsed={sample['elapsed_ms']:.2f} ms "
                f"display={sample['display_update_ms']:.2f} ms "
                f"draws={sample['draw_calls']} "
                f"bytes={sample['draw_bytes']}"
                f" snapshot_age={sample.get('snapshot_age_ms', -1.0):.1f} ms"
            )
    poll_ms = max(0.001, config.display.input_poll_interval) * 1000.0
    loop_waits = _loop_wait_samples(poll_ms, args.loop_samples)
    combined = [render + wait for render, wait in zip(timings, (loop_waits * ((len(timings) // len(loop_waits)) + 1))[: len(timings)])]
    print(f"configured input poll interval: {poll_ms:.2f} ms")
    _print_summary("modeled poll wait", loop_waits)
    _print_summary("modeled event-to-display", combined)

    if args.json_path:
        output = {
            "backend": app.display.backend_name,
            "input_poll_interval_ms": poll_ms,
            "warmup_timings_ms": warmup_timings,
            "timings_ms": timings,
            "samples": samples,
            "modeled_poll_wait_ms": loop_waits,
            "modeled_event_to_display_ms": combined,
            **_summary(timings),
        }
        output_path = Path(args.json_path).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(output, indent=2, sort_keys=True))
        print(f"wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
