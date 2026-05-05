#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
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
HEALTH_FILE = RUN_DIR / "health.json"
PID_FILE = RUN_DIR / "supervisor.pid"
STATUS_FILE = STATE_DIR / "status.json"
STARTUP_TIMEOUT_SECONDS = 20.0
ACCEPTANCE_WINDOW_SECONDS = 5.0
MONITOR_INTERVAL_SECONDS = 0.5
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


def short_path(path: Path | None) -> str | None:
    if path is None:
        return None
    return str(path)


def read_health(pid: int) -> dict[str, object] | None:
    if not HEALTH_FILE.exists():
        return None
    try:
        payload = json.loads(HEALTH_FILE.read_text())
    except json.JSONDecodeError:
        return None
    if payload.get("pid") != pid:
        return None
    return payload


def python_for_release(release_dir: Path) -> Path:
    release_python = release_dir / ".venv" / "bin" / "python"
    if release_python.exists():
        return release_python
    shared_python = SHARED_VENV / "bin" / "python"
    if shared_python.exists():
        return shared_python
    return Path(sys.executable)


def run_self_test(release_dir: Path) -> tuple[bool, str]:
    env = os.environ.copy()
    env["ALTOIDS_RELEASE_ID"] = release_dir.name
    command = [str(python_for_release(release_dir)), "-m", "altoids", "--self-test"]
    result = subprocess.run(
        command,
        cwd=release_dir,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    output = (result.stderr or result.stdout or "").strip()
    if result.returncode == 0:
        return True, output
    return False, output or f"self-test exited with status {result.returncode}"


def pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def signal_supervisor(signum: int) -> None:
    if not PID_FILE.exists():
        raise SystemExit("supervisor is not running")
    pid = int(PID_FILE.read_text().strip())
    if not pid_is_running(pid):
        raise SystemExit(f"supervisor pid {pid} is stale")
    os.kill(pid, signum)


class Supervisor:
    def __init__(self) -> None:
        self.child: subprocess.Popen[str] | None = None
        self.child_release: Path | None = None
        self.pending_release: Path | None = None
        self.pending_reason: str | None = None
        self.pending_started_at = 0.0
        self.pending_ready_at = 0.0
        self.pending_fallback: Path | None = None
        self.stop_requested = False
        self.reload_requested = False
        self.rollback_requested = False

    def request_reload(self, *_args: object) -> None:
        self.reload_requested = True

    def request_rollback(self, *_args: object) -> None:
        self.rollback_requested = True

    def request_stop(self, *_args: object) -> None:
        self.stop_requested = True

    def run(self) -> int:
        ensure_directories()
        PID_FILE.write_text(f"{os.getpid()}\n")
        signal.signal(signal.SIGHUP, self.request_reload)
        signal.signal(signal.SIGUSR1, self.request_rollback)
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)
        try:
            current = resolve_link(CURRENT_LINK)
            previous = resolve_link(PREVIOUS_LINK)
            if current is None:
                current = previous or resolve_link(STAGED_LINK)
            if current is None:
                write_status("error", "no release configured")
                return 1
            self.launch_release(current, fallback=previous, reason="startup", require_acceptance=False)
            while not self.stop_requested:
                if self.reload_requested:
                    self.reload_requested = False
                    self.handle_reload_request()
                if self.rollback_requested:
                    self.rollback_requested = False
                    self.handle_rollback_request()
                if self.child is None:
                    break
                return_code = self.child.poll()
                if return_code is not None:
                    self.handle_child_exit(return_code)
                    time.sleep(MONITOR_INTERVAL_SECONDS)
                    continue
                self.handle_pending_acceptance()
                time.sleep(MONITOR_INTERVAL_SECONDS)
            self.stop_child()
            return 0
        finally:
            if PID_FILE.exists():
                PID_FILE.unlink()

    def handle_reload_request(self) -> None:
        candidate = resolve_link(STAGED_LINK)
        current = self.child_release
        if candidate is None:
            write_status("error", "reload requested with no staged release")
            return
        if current is not None and candidate == current:
            write_status("ok", "staged release already active", release=short_path(candidate))
            return
        healthy, output = run_self_test(candidate)
        if not healthy:
            write_status(
                "error",
                "staged release failed self-test",
                release=short_path(candidate),
                output=output[:MAX_LOG_CHARS],
            )
            return
        if current is None:
            self.launch_release(candidate, fallback=resolve_link(PREVIOUS_LINK), reason="reload")
            return
        self.stop_child()
        self.launch_release(candidate, fallback=current, reason="reload")

    def handle_rollback_request(self) -> None:
        candidate = resolve_link(PREVIOUS_LINK)
        if candidate is None:
            write_status("error", "rollback requested with no previous release")
            return
        current = self.child_release
        if current is not None and candidate == current:
            write_status("ok", "previous release already active", release=short_path(candidate))
            return
        self.stop_child()
        self.launch_release(candidate, fallback=current, reason="rollback")

    def launch_release(self, release_dir: Path, fallback: Path | None, reason: str, require_acceptance: bool = True) -> None:
        python_bin = python_for_release(release_dir)
        if HEALTH_FILE.exists():
            HEALTH_FILE.unlink()
        env = os.environ.copy()
        env["ALTOIDS_RELEASE_ID"] = release_dir.name
        command = [str(python_bin), "-m", "altoids", "--health-file", str(HEALTH_FILE)]
        self.child = subprocess.Popen(command, cwd=release_dir, env=env, text=True)
        self.child_release = release_dir
        if require_acceptance:
            self.pending_release = release_dir
            self.pending_reason = reason
            self.pending_started_at = time.monotonic()
            self.pending_ready_at = 0.0
            self.pending_fallback = fallback
        else:
            self.pending_release = None
            self.pending_reason = None
            self.pending_started_at = 0.0
            self.pending_ready_at = 0.0
            self.pending_fallback = None
        write_status(
            "starting" if require_acceptance else "ok",
            f"launching {release_dir.name}",
            release=short_path(release_dir),
            fallback=short_path(fallback),
            reason=reason,
            pid=self.child.pid,
        )

    def handle_pending_acceptance(self) -> None:
        if self.child is None or self.pending_release is None:
            return
        health = read_health(self.child.pid)
        if health and health.get("ready"):
            if not self.pending_ready_at:
                self.pending_ready_at = time.monotonic()
            if time.monotonic() - self.pending_ready_at >= ACCEPTANCE_WINDOW_SECONDS:
                try:
                    self.promote_pending_release()
                except OSError as exc:
                    self.fail_pending_release(f"promotion failed: {exc}")
                return
        if time.monotonic() - self.pending_started_at >= STARTUP_TIMEOUT_SECONDS:
            self.fail_pending_release("health timeout")

    def handle_child_exit(self, return_code: int) -> None:
        release = self.child_release
        if self.pending_release is not None:
            self.fail_pending_release(f"exited with status {return_code}")
            return
        write_status(
            "restarting",
            f"active release exited with status {return_code}",
            release=short_path(release),
        )
        if release is not None:
            self.launch_release(release, fallback=resolve_link(PREVIOUS_LINK), reason="restart", require_acceptance=False)

    def promote_pending_release(self) -> None:
        if self.pending_release is None:
            return
        old_current = resolve_link(CURRENT_LINK)
        candidate = self.pending_release
        if old_current is not None and old_current != candidate:
            atomic_symlink(PREVIOUS_LINK, old_current)
        atomic_symlink(CURRENT_LINK, candidate)
        if resolve_link(STAGED_LINK) == candidate:
            atomic_symlink(STAGED_LINK, candidate)
        write_status(
            "ok",
            f"{candidate.name} accepted",
            current=short_path(candidate),
            previous=short_path(old_current),
            reason=self.pending_reason,
            pid=self.child.pid if self.child is not None else None,
        )
        self.pending_release = None
        self.pending_reason = None
        self.pending_started_at = 0.0
        self.pending_ready_at = 0.0
        self.pending_fallback = None

    def fail_pending_release(self, reason: str) -> None:
        failed_release = self.pending_release
        fallback = self.pending_fallback
        self.stop_child()
        write_status(
            "rollback",
            f"{failed_release.name if failed_release else 'candidate'} failed: {reason[:MAX_LOG_CHARS]}",
            failed_release=short_path(failed_release),
            fallback=short_path(fallback),
            reason=self.pending_reason,
        )
        self.pending_release = None
        self.pending_reason = None
        self.pending_started_at = 0.0
        self.pending_ready_at = 0.0
        self.pending_fallback = None
        if fallback is not None:
            self.launch_release(fallback, fallback=resolve_link(PREVIOUS_LINK), reason="rollback-recovery", require_acceptance=False)

    def stop_child(self) -> None:
        if self.child is None:
            return
        if self.child.poll() is None:
            self.child.terminate()
            try:
                self.child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(timeout=5)
        self.child = None
        self.child_release = None


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


def cmd_reload(_args: argparse.Namespace) -> int:
    signal_supervisor(signal.SIGHUP)
    print("reload requested")
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
    signal_supervisor(signal.SIGUSR1)
    print("rollback requested")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    status = read_status() or {}
    status.setdefault("current", short_path(resolve_link(CURRENT_LINK)))
    status.setdefault("previous", short_path(resolve_link(PREVIOUS_LINK)))
    status.setdefault("staged", short_path(resolve_link(STAGED_LINK)))
    if args.json:
        print(json.dumps(status, indent=2, sort_keys=True))
        return 0
    for key in ("status", "message", "current", "previous", "staged", "timestamp", "reason"):
        value = status.get(key)
        if value:
            print(f"{key}: {value}")
    return 0


def cmd_supervisor(_args: argparse.Namespace) -> int:
    return Supervisor().run()


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

    reload_parser = subparsers.add_parser("reload", help="Ask the supervisor to switch to the staged release")
    reload_parser.set_defaults(func=cmd_reload)

    activate = subparsers.add_parser("activate", help="Promote the staged release to current after self-test")
    activate.set_defaults(func=cmd_activate)

    rollback = subparsers.add_parser("rollback", help="Ask the supervisor to switch back to the previous release")
    rollback.set_defaults(func=cmd_rollback)

    status = subparsers.add_parser("status", help="Show current runtime state")
    status.add_argument("--json", action="store_true", help="Print full JSON status")
    status.set_defaults(func=cmd_status)

    supervisor = subparsers.add_parser("supervisor", help="Run the stable release supervisor")
    supervisor.set_defaults(func=cmd_supervisor)
    return parser


def main(argv: list[str] | None = None) -> int:
    ensure_directories()
    parser = build_parser()
    args = parser.parse_args(argv)
    func: Callable[[argparse.Namespace], int] = args.func
    return func(args)


if __name__ == "__main__":
    raise SystemExit(main())
