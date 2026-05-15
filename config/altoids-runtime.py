#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable


ROOT_DIR = Path(os.environ.get("ALTOIDS_RUNTIME_ROOT", "/opt/altoids"))
RUN_DIR = Path(os.environ.get("ALTOIDS_RUNTIME_RUN", "/run/altoids"))
STATE_DIR = Path(os.environ.get("ALTOIDS_RUNTIME_STATE", str(RUN_DIR)))
RELEASES_DIR = ROOT_DIR / "releases"
CURRENT_LINK = ROOT_DIR / "current"
PREVIOUS_LINK = ROOT_DIR / "previous"
STAGED_LINK = ROOT_DIR / "staged"
SHARED_VENV = ROOT_DIR / ".venv"
SHARED_VENDOR = ROOT_DIR / "vendor"
STATUS_FILE = STATE_DIR / "status.json"
MAX_LOG_CHARS = 240


def ensure_directories() -> None:
    RELEASES_DIR.mkdir(parents=True, exist_ok=True)
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def atomic_symlink(link_path: Path, target: Path) -> None:
    link_path.parent.mkdir(parents=True, exist_ok=True)
    temp_link = link_path.with_name(f".{link_path.name}.{os.getpid()}.tmp")
    if temp_link.exists() or temp_link.is_symlink():
        temp_link.unlink()
    temp_link.symlink_to(target)
    temp_link.replace(link_path)


def resolve_link(link_path: Path) -> Path | None:
    if not link_path.exists() and not link_path.is_symlink():
        return None
    try:
        return link_path.resolve(strict=True)
    except FileNotFoundError:
        return None


def release_id() -> str:
    return time.strftime("%Y%m%dT%H%M%S")


def ignore_copy(path: str, names: list[str]) -> set[str]:
    ignored = {
        ".git",
        ".venv",
        ".pytest_cache",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".DS_Store",
    }
    if Path(path).resolve() == ROOT_DIR.resolve():
        ignored.update({"current", "previous", "staged", "releases", "runtime"})
    if "artifacts" in names:
        ignored.add("artifacts")
    return {name for name in names if name in ignored}


def write_status(status: str, message: str, **details: object) -> None:
    ensure_directories()
    payload: dict[str, object] = {
        "status": status,
        "message": message,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    payload.update(details)
    STATUS_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True))


def read_status() -> dict[str, object] | None:
    if not STATUS_FILE.exists():
        return None
    return json.loads(STATUS_FILE.read_text())


def short_path(path: Path | None) -> str | None:
    if path is None:
        return None
    return str(path)


def validate_source_dir(source_dir: Path) -> None:
    if not (source_dir / "altoids" / "__main__.py").exists():
        raise SystemExit(f"{source_dir} does not look like an altoids repo checkout")


def create_release_from_source(source_dir: Path, requested_id: str | None = None) -> Path:
    ensure_directories()
    validate_source_dir(source_dir)
    source_dir = source_dir.resolve()
    identifier = requested_id or release_id()
    destination = RELEASES_DIR / identifier
    if destination.exists():
        raise SystemExit(f"release already exists: {destination}")
    shutil.copytree(source_dir, destination, symlinks=True, ignore=ignore_copy)
    release_venv = destination / ".venv"
    if not release_venv.exists() and SHARED_VENV.exists():
        release_venv.symlink_to(SHARED_VENV)
    release_vendor = destination / "vendor"
    if not release_vendor.exists() and SHARED_VENDOR.exists():
        release_vendor.symlink_to(SHARED_VENDOR)
    metadata = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_dir": str(source_dir),
        "release_id": identifier,
    }
    (destination / ".altoids-release.json").write_text(json.dumps(metadata, indent=2, sort_keys=True))
    return destination


def python_for_release(release_dir: Path) -> Path:
    release_python = release_dir / ".venv" / "bin" / "python"
    if release_python.exists():
        return release_python
    shared_python = SHARED_VENV / "bin" / "python"
    if shared_python.exists():
        return shared_python
    return Path(sys.executable)


def run_self_test(release_dir: Path) -> tuple[bool, str]:
    command = [str(python_for_release(release_dir)), "-m", "altoids", "--self-test"]
    result = subprocess.run(
        command,
        cwd=release_dir,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    output = (result.stderr or result.stdout or "").strip()
    if result.returncode == 0:
        return True, output
    return False, output or f"self-test exited with status {result.returncode}"


def cmd_stage(args: argparse.Namespace) -> int:
    source_dir = Path(args.source).resolve()
    release_dir = create_release_from_source(source_dir, requested_id=args.release_id)
    atomic_symlink(STAGED_LINK, release_dir)
    write_status("staged", f"staged {release_dir.name}", staged=short_path(release_dir), source=str(source_dir))
    print(release_dir)
    return 0


def cmd_bootstrap(args: argparse.Namespace) -> int:
    source_dir = Path(args.source).resolve()
    release_dir = create_release_from_source(source_dir, requested_id=args.release_id)
    atomic_symlink(CURRENT_LINK, release_dir)
    atomic_symlink(STAGED_LINK, release_dir)
    write_status("ok", f"bootstrapped {release_dir.name}", current=short_path(release_dir))
    print(release_dir)
    return 0


def cmd_activate(_args: argparse.Namespace) -> int:
    candidate = resolve_link(STAGED_LINK)
    if candidate is None:
        raise SystemExit("no staged release")
    current = resolve_link(CURRENT_LINK)
    if current is not None and current == candidate:
        write_status("ok", "staged release already active", current=short_path(current), staged=short_path(candidate))
        print("staged release already active")
        return 0
    healthy, output = run_self_test(candidate)
    if not healthy:
        write_status(
            "error",
            "staged release failed self-test",
            release=short_path(candidate),
            output=output[:MAX_LOG_CHARS],
        )
        raise SystemExit(output or "staged release failed self-test")
    if current is not None and current != candidate:
        atomic_symlink(PREVIOUS_LINK, current)
    atomic_symlink(CURRENT_LINK, candidate)
    write_status(
        "ok",
        f"activated {candidate.name}",
        current=short_path(candidate),
        previous=short_path(current),
        staged=short_path(candidate),
    )
    print(candidate)
    return 0


def cmd_rollback(_args: argparse.Namespace) -> int:
    candidate = resolve_link(PREVIOUS_LINK)
    if candidate is None:
        raise SystemExit("no previous release")
    current = resolve_link(CURRENT_LINK)
    if current is not None and current == candidate:
        write_status("ok", "previous release already active", current=short_path(current))
        print("previous release already active")
        return 0
    if current is not None:
        atomic_symlink(STAGED_LINK, current)
    atomic_symlink(CURRENT_LINK, candidate)
    write_status(
        "ok",
        f"rolled back to {candidate.name}",
        current=short_path(candidate),
        previous=short_path(current),
    )
    print(candidate)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    status = read_status() or {}
    status.setdefault("current", short_path(resolve_link(CURRENT_LINK)))
    status.setdefault("previous", short_path(resolve_link(PREVIOUS_LINK)))
    status.setdefault("staged", short_path(resolve_link(STAGED_LINK)))
    if args.json:
        print(json.dumps(status, indent=2, sort_keys=True))
        return 0
    for key in ("status", "message", "current", "previous", "staged", "timestamp"):
        value = status.get(key)
        if value:
            print(f"{key}: {value}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Altoids runtime control")
    subparsers = parser.add_subparsers(dest="command", required=True)

    stage = subparsers.add_parser("stage", help="Stage the current source tree as the next release")
    stage.add_argument("source", nargs="?", default=".", help="Source checkout to copy into a staged release")
    stage.add_argument("--release-id", default=None, help="Explicit release identifier")
    stage.set_defaults(func=cmd_stage)

    bootstrap = subparsers.add_parser("bootstrap", help="Create the initial current release")
    bootstrap.add_argument("--source", required=True, help="Source checkout to copy into the first release")
    bootstrap.add_argument("--release-id", default="bootstrap", help="Explicit release identifier")
    bootstrap.set_defaults(func=cmd_bootstrap)

    activate = subparsers.add_parser("activate", help="Promote the staged release to current after self-test")
    activate.set_defaults(func=cmd_activate)

    rollback = subparsers.add_parser("rollback", help="Switch current back to the previous release")
    rollback.set_defaults(func=cmd_rollback)

    status = subparsers.add_parser("status", help="Show current runtime state")
    status.add_argument("--json", action="store_true", help="Print full JSON status")
    status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    ensure_directories()
    parser = build_parser()
    args = parser.parse_args(argv)
    func: Callable[[argparse.Namespace], int] = args.func
    return func(args)


if __name__ == "__main__":
    raise SystemExit(main())
