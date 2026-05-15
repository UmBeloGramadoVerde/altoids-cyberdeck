#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import json
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any


def _measure(label: str, timings: list[tuple[str, float]], func: Callable[[], Any]) -> Any:
    started = time.perf_counter()
    try:
        return func()
    finally:
        timings.append((label, (time.perf_counter() - started) * 1000.0))


def benchmark_altoids(config_path: str | None, frames: int) -> list[tuple[str, float]]:
    timings: list[tuple[str, float]] = []
    app_module = _measure("import altoids.app", timings, lambda: importlib.import_module("altoids.app"))
    config = _measure("load config", timings, lambda: app_module.load_config(config_path))
    config.display.backend = "mock"
    app = _measure("construct AltoidsApp", timings, lambda: app_module.AltoidsApp(config=config))
    try:
        _measure(f"render {frames} frame{'s' if frames != 1 else ''}", timings, lambda: app.run(max_frames=frames))
    finally:
        app.close()
    return timings


def benchmark_cdx(args: argparse.Namespace) -> list[tuple[str, float]]:
    timings: list[tuple[str, float]] = []
    cdx = _measure("import altoids.cdx", timings, lambda: importlib.import_module("altoids.cdx"))
    codex_bin = _measure("resolve codex bin", timings, lambda: cdx.CdxApp._resolve_codex_bin(args.codex_bin))
    client = _measure(
        "start app-server process",
        timings,
        lambda: cdx.AppServerClient(
            codex_bin,
            home_override=args.home_override,
            xdg_state_home=args.xdg_state_home,
        ),
    )
    try:
        _measure("initialize app-server", timings, client.initialize)
        if args.cdx_mode == "list":
            _measure(
                "thread/list",
                timings,
                lambda: client.request(
                    "thread/list",
                    {
                        "cwd": str(Path(args.cwd or Path.cwd()).resolve()),
                        "limit": args.thread_limit,
                        "sortKey": "updated_at",
                        "sortDirection": "desc",
                    },
                ),
            )
        elif args.cdx_mode == "new":
            _measure(
                "thread/start",
                timings,
                lambda: client.request(
                    "thread/start",
                    {
                        "cwd": str(Path(args.cwd or Path.cwd()).resolve()),
                        "approvalPolicy": "on-request",
                        "approvalsReviewer": "user",
                        "sessionStartSource": "startup-benchmark",
                    },
                ),
            )
    finally:
        _measure("close app-server", timings, client.close)
    return timings


def _summarize(runs: list[list[tuple[str, float]]]) -> list[dict[str, float | str]]:
    labels = [label for label, _ in runs[0]]
    rows: list[dict[str, float | str]] = []
    for index, label in enumerate(labels):
        values = [run[index][1] for run in runs]
        rows.append(
            {
                "phase": label,
                "median_ms": statistics.median(values),
                "mean_ms": statistics.fmean(values),
                "min_ms": min(values),
                "max_ms": max(values),
            }
        )
    totals = [sum(value for _, value in run) for run in runs]
    rows.append(
        {
            "phase": "total",
            "median_ms": statistics.median(totals),
            "mean_ms": statistics.fmean(totals),
            "min_ms": min(totals),
            "max_ms": max(totals),
        }
    )
    return rows


def _print_table(rows: list[dict[str, float | str]]) -> None:
    phase_width = max(len(str(row["phase"])) for row in rows)
    print(f"{'phase':<{phase_width}}  median    mean     min     max")
    for row in rows:
        print(
            f"{str(row['phase']):<{phase_width}}  "
            f"{float(row['median_ms']):7.1f}  "
            f"{float(row['mean_ms']):7.1f}  "
            f"{float(row['min_ms']):6.1f}  "
            f"{float(row['max_ms']):6.1f}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark Altoids and cdx startup phases.")
    parser.add_argument("--target", choices=("altoids", "cdx"), default="altoids")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable summary")
    parser.add_argument("--config", default=None, help="Path to altoids.toml")
    parser.add_argument("--frames", type=int, default=1, help="Frames to render for Altoids startup")
    parser.add_argument("--cwd", default=None, help="Working directory to pass to cdx requests")
    parser.add_argument("--codex-bin", default=None, help="Path to codex executable")
    parser.add_argument("--home-override", default=None)
    parser.add_argument("--xdg-state-home", default=None)
    parser.add_argument("--cdx-mode", choices=("list", "new", "initialize"), default="list")
    parser.add_argument("--thread-limit", type=int, default=8)
    parser.add_argument("--in-process", action="store_true", help=argparse.SUPPRESS)
    return parser


def _subprocess_runs(args: argparse.Namespace) -> list[list[tuple[str, float]]]:
    base_command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--target",
        args.target,
        "--runs",
        "1",
        "--json",
        "--in-process",
        "--frames",
        str(args.frames),
        "--cdx-mode",
        args.cdx_mode,
        "--thread-limit",
        str(args.thread_limit),
    ]
    optional_args = {
        "--config": args.config,
        "--cwd": args.cwd,
        "--codex-bin": args.codex_bin,
        "--home-override": args.home_override,
        "--xdg-state-home": args.xdg_state_home,
    }
    for flag, value in optional_args.items():
        if value:
            base_command.extend([flag, value])

    runs: list[list[tuple[str, float]]] = []
    for _ in range(args.runs):
        completed = subprocess.run(base_command, check=True, capture_output=True, text=True)
        rows = json.loads(completed.stdout)
        runs.append([(str(row["phase"]), float(row["median_ms"])) for row in rows if row["phase"] != "total"])
    return runs


def main() -> int:
    args = build_parser().parse_args()
    if args.runs < 1:
        raise SystemExit("--runs must be at least 1")
    if args.runs > 1 and not args.in_process:
        runs = _subprocess_runs(args)
    else:
        runs = []
        for _ in range(args.runs):
            if args.target == "altoids":
                runs.append(benchmark_altoids(args.config, args.frames))
            else:
                runs.append(benchmark_cdx(args))
    rows = _summarize(runs)
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        _print_table(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
