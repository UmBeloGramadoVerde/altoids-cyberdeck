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
def _profile_terminal(app: AltoidsApp) -> Iterator[dict[str, object]]:
    profile: dict[str, object] = {
        "display_update_ms": 0.0,
        "draw_calls": 0,
        "draw_bytes": 0,
        "tmux_calls": [],
    }
    original_display_update = app.display.update
    original_tmux_run = app.tmux._run
    original_control_command = app.tmux._control_command
    backend = getattr(app.display, "_backend", None)
    original_draw_image: Callable | None = getattr(backend, "draw_image", None) if backend is not None else None

    def profiled_display_update(*args, **kwargs):
        started = time.perf_counter()
        try:
            return original_display_update(*args, **kwargs)
        finally:
            profile["display_update_ms"] = (time.perf_counter() - started) * 1000.0

    def profiled_draw_image(*args, **kwargs):
        profile["draw_calls"] = int(profile["draw_calls"]) + 1
        if len(args) >= 5:
            profile["draw_bytes"] = int(profile["draw_bytes"]) + len(args[4])
        return original_draw_image(*args, **kwargs)

    def profiled_tmux_run(*args, **kwargs):
        started = time.perf_counter()
        try:
            return original_tmux_run(*args, **kwargs)
        finally:
            calls = profile["tmux_calls"]
            assert isinstance(calls, list)
            calls.append(
                {
                    "cmd": " ".join(args[:2]) if args else "",
                    "ms": (time.perf_counter() - started) * 1000.0,
                }
            )

    def profiled_control_command(command: str):
        started = time.perf_counter()
        try:
            return original_control_command(command)
        finally:
            calls = profile["tmux_calls"]
            assert isinstance(calls, list)
            calls.append(
                {
                    "cmd": "control",
                    "ms": (time.perf_counter() - started) * 1000.0,
                }
            )

    app.display.update = profiled_display_update
    app.tmux._run = profiled_tmux_run
    app.tmux._control_command = profiled_control_command
    if backend is not None and original_draw_image is not None:
        setattr(backend, "draw_image", profiled_draw_image)
    try:
        yield profile
    finally:
        app.display.update = original_display_update
        app.tmux._run = original_tmux_run
        app.tmux._control_command = original_control_command
        if backend is not None and original_draw_image is not None:
            setattr(backend, "draw_image", original_draw_image)


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((pct / 100.0) * (len(ordered) - 1))))
    return ordered[index]


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "median_ms": statistics.median(values),
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


def _tmux_totals(samples: list[dict[str, object]]) -> list[float]:
    totals: list[float] = []
    for sample in samples:
        calls = sample["tmux_calls"]
        assert isinstance(calls, list)
        totals.append(sum(float(call["ms"]) for call in calls))
    return totals


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Measure typed-key to terminal-display latency.")
    parser.add_argument("--config", default=None, help="Path to altoids.toml")
    parser.add_argument("--backend", default=None, help="Override display backend, e.g. whisplay or mock")
    parser.add_argument("--iterations", type=int, default=80)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--settle", type=float, default=0.05)
    parser.add_argument("--text", default="a", help="Single text key to inject repeatedly")
    parser.add_argument("--json", dest="json_path", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    if args.backend is not None:
        config.display.backend = args.backend

    app = AltoidsApp(config=config)
    app.active_screen_name = "term"
    app.needs_redraw = True
    timings: list[float] = []
    warmup_timings: list[float] = []
    samples: list[dict[str, object]] = []
    try:
        app.tmux.ensure_session()
        app.render()
        key_text = args.text[:1] or "a"
        event = KeyboardEvent(key=key_text.lower(), raw_key=f"KEY_{key_text.upper()}", text=key_text)
        for index in range(args.iterations + args.warmup):
            time.sleep(args.settle)
            started = time.perf_counter()
            with _profile_terminal(app) as profile:
                app.handle_keyboard_event(event)
                if app.needs_redraw:
                    app.render()
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            if index < args.warmup:
                warmup_timings.append(elapsed_ms)
            else:
                timings.append(elapsed_ms)
                calls = profile["tmux_calls"]
                assert isinstance(calls, list)
                samples.append(
                    {
                        "elapsed_ms": elapsed_ms,
                        "display_update_ms": float(profile["display_update_ms"]),
                        "draw_calls": int(profile["draw_calls"]),
                        "draw_bytes": int(profile["draw_bytes"]),
                        "tmux_call_count": len(calls),
                        "tmux_total_ms": sum(float(call["ms"]) for call in calls),
                        "tmux_calls": calls,
                    }
                )
    finally:
        app.close()

    if warmup_timings:
        _print_summary("warmup discarded", warmup_timings)
    _print_summary("terminal key-to-display", timings)
    display_values = [float(sample["display_update_ms"]) for sample in samples]
    tmux_values = _tmux_totals(samples)
    _print_summary("display update", display_values)
    _print_summary("tmux subprocess total", tmux_values)
    print("worst samples:")
    for sample in sorted(samples, key=lambda row: float(row["elapsed_ms"]), reverse=True)[:8]:
        print(
            f"  elapsed={sample['elapsed_ms']:.2f} ms "
            f"tmux={sample['tmux_total_ms']:.2f} ms "
            f"tmux_calls={sample['tmux_call_count']} "
            f"display={sample['display_update_ms']:.2f} ms "
            f"bytes={sample['draw_bytes']}"
        )

    if args.json_path:
        output = {
            "backend": app.display.backend_name,
            "timings_ms": timings,
            "warmup_timings_ms": warmup_timings,
            "samples": samples,
            **_summary(timings),
        }
        output_path = Path(args.json_path).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(output, indent=2, sort_keys=True))
        print(f"wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
