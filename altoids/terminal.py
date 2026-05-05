from __future__ import annotations

import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(slots=True)
class TerminalSnapshot:
    lines: list[str]
    window_count: int
    active_window_position: int
    active_window: str
    pane_title: str
    pane_path: str
    pane_command: str
    cursor_x: int
    cursor_y: int
    cursor_visible: bool
    pane_in_mode: bool


@dataclass(slots=True)
class TmuxWindow:
    index: int
    name: str
    active: bool


class TmuxManager:
    def __init__(
        self,
        session_name: str,
        width_chars: int,
        height_chars: int,
        pane_history: int,
        shell_rc_path: Path,
    ) -> None:
        self.session_name = session_name
        self.width_chars = width_chars
        self.height_chars = height_chars
        self.pane_history = pane_history
        self.shell_rc_path = shell_rc_path
        self._last_size: tuple[int, int] | None = None
        self._last_resize_window: str | None = None
        self._last_ensure_at = 0.0
        self._session_configured = False
        self._last_snapshot: TerminalSnapshot | None = None
        self._control_process: subprocess.Popen[str] | None = None
        self._tmux_path = shutil.which("tmux")
        self._command_timeout = 0.5

    @property
    def available(self) -> bool:
        return self._tmux_path is not None

    def _run(self, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
        if self._tmux_path is None:
            return subprocess.CompletedProcess(["tmux", *args], 127, "", "tmux not found")
        try:
            return subprocess.run(
                [self._tmux_path, *args],
                check=check,
                text=True,
                capture_output=True,
                timeout=self._command_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            return subprocess.CompletedProcess(
                [self._tmux_path, *args],
                124,
                exc.stdout if isinstance(exc.stdout, str) else "",
                exc.stderr if isinstance(exc.stderr, str) else "tmux command timed out",
            )

    def _target(self) -> str:
        return self.session_name

    def _start_directory(self) -> str:
        return str(Path.home())

    def _current_directory(self) -> str:
        if not self.available:
            return self._start_directory()
        proc = self._run("display-message", "-p", "-t", self._target(), "#{pane_current_path}")
        path = proc.stdout.strip()
        if path:
            return path
        return self._start_directory()

    def ensure_session(self) -> None:
        if not self.available:
            return
        now = time.monotonic()
        if self._session_configured and now - self._last_ensure_at < 2.0:
            return
        has = self._run("has-session", "-t", self.session_name)
        if has.returncode == 0:
            if not self._session_configured:
                self._configure_session_shell()
                self._session_configured = True
            self._last_ensure_at = now
            return
        args = [
            "new-session",
            "-d",
            "-s",
            self.session_name,
            "-c",
            self._start_directory(),
            "-x",
            str(self.width_chars),
            "-y",
            str(self.height_chars),
        ]
        shell_command = self._shell_command()
        if shell_command:
            args.append(shell_command)
        self._run(*args)
        self._configure_session_shell()
        self._session_configured = True
        self._last_ensure_at = now

    def capture(self, scroll_offset: int = 0, height_rows: int | None = None, fast: bool = False) -> TerminalSnapshot:
        return self._capture_target(self._target(), scroll_offset=scroll_offset, height_rows=height_rows, fast=fast)

    def capture_window(self, index: int, scroll_offset: int = 0, height_rows: int | None = None) -> TerminalSnapshot:
        return self._capture_target(f"{self.session_name}:{index}", scroll_offset=scroll_offset, height_rows=height_rows)

    def _capture_target(
        self,
        target: str,
        scroll_offset: int = 0,
        height_rows: int | None = None,
        fast: bool = False,
    ) -> TerminalSnapshot:
        if not self.available:
            return TerminalSnapshot(
                lines=[
                    "tmux not installed",
                    "",
                    "Install tmux to enable persistent shells.",
                ],
                window_count=0,
                active_window_position=0,
                active_window="-",
                pane_title="-",
                pane_path="-",
                pane_command="-",
                cursor_x=0,
                cursor_y=0,
                cursor_visible=False,
                pane_in_mode=False,
            )
        self.ensure_session()
        row_count = height_rows or self.height_chars
        proc = self._run(*self._capture_pane_args(target, scroll_offset, row_count))
        if proc.returncode != 0 and self._last_snapshot is not None:
            return self._last_snapshot
        all_lines = proc.stdout.splitlines()
        lines = all_lines[-row_count:]
        if fast and self._last_snapshot is not None:
            previous = self._last_snapshot
            snapshot = TerminalSnapshot(
                lines=lines,
                window_count=previous.window_count,
                active_window_position=previous.active_window_position,
                active_window=previous.active_window,
                pane_title=previous.pane_title,
                pane_path=previous.pane_path,
                pane_command=previous.pane_command,
                cursor_x=previous.cursor_x,
                cursor_y=previous.cursor_y,
                cursor_visible=previous.cursor_visible,
                pane_in_mode=previous.pane_in_mode,
            )
            self._last_snapshot = snapshot
            return snapshot
        windows = self.list_window_details()
        active_window = next((window for window in windows if window.active), None)
        active = f"{active_window.index}:{active_window.name}" if active_window is not None else (f"{windows[0].index}:{windows[0].name}" if windows else "-")
        active_position = next((index for index, window in enumerate(windows, start=1) if window.active), 0)
        pane_title, pane_path, pane_command, cursor_x, cursor_y, cursor_visible, pane_in_mode = self._pane_metadata(target)
        snapshot = TerminalSnapshot(
            lines=lines,
            window_count=len(windows),
            active_window_position=active_position,
            active_window=active,
            pane_title=pane_title,
            pane_path=pane_path,
            pane_command=pane_command,
            cursor_x=cursor_x,
            cursor_y=cursor_y,
            cursor_visible=cursor_visible,
            pane_in_mode=pane_in_mode,
        )
        self._last_snapshot = snapshot
        return snapshot

    def list_windows(self) -> list[str]:
        return [
            f"{'*' if window.active else ' '}{window.index}:{window.name}"
            for window in self.list_window_details()
        ]

    def list_window_details(self) -> list[TmuxWindow]:
        if not self.available:
            return []
        self.ensure_session()
        proc = self._run("list-windows", "-F", "#{window_active}\t#{window_index}\t#{window_name}", "-t", self.session_name)
        if proc.returncode != 0:
            return []
        windows: list[TmuxWindow] = []
        for line in proc.stdout.splitlines():
            if not line:
                continue
            active, index, name = (line.split("\t", 2) + ["", ""])[:3]
            windows.append(
                TmuxWindow(
                    index=_parse_int(index),
                    name=name or "-",
                    active=active == "1",
                )
            )
        return windows

    def send_keys(self, keys: Iterable[str]) -> None:
        if not self.available:
            return
        self.ensure_session()
        key_list = list(keys)
        if self._control_command(
            "send-keys -t "
            + shlex_quote(self._target())
            + " "
            + " ".join(shlex_quote(key) for key in key_list)
        ):
            return
        self._run("send-keys", "-t", self._target(), *key_list)

    def send_enter(self) -> None:
        self.send_keys(["Enter"])

    def select_next_window(self) -> None:
        if not self.available:
            return
        self.ensure_session()
        self._run("next-window", "-t", self.session_name)
        self._invalidate_resize_cache()

    def select_previous_window(self) -> None:
        if not self.available:
            return
        self.ensure_session()
        self._run("previous-window", "-t", self.session_name)
        self._invalidate_resize_cache()

    def select_window(self, index: int) -> None:
        if not self.available or index < 1:
            return
        self.ensure_session()
        self._run("select-window", "-t", f"{self.session_name}:{index}")
        self._invalidate_resize_cache()

    def create_window(self, name: str | None = None) -> None:
        if not self.available:
            return
        self.ensure_session()
        args = ["new-window", "-t", self.session_name, "-c", self._current_directory()]
        if name:
            args.extend(["-n", name])
        shell_command = self._shell_command()
        if shell_command:
            args.append(shell_command)
        self._run(*args)
        self._invalidate_resize_cache()

    def close_active_window(self) -> None:
        if not self.available:
            return
        self.ensure_session()
        self._run("kill-window", "-t", self._target())
        self._invalidate_resize_cache()
        self.ensure_session()

    def send_text(self, text: str) -> None:
        if not text:
            return
        if not self.available:
            return
        self.ensure_session()
        if self._control_command(f"send-keys -t {shlex_quote(self._target())} -l {shlex_quote(text)}"):
            return
        self._run("send-keys", "-t", self._target(), text)

    def resize(self, width_chars: int, height_chars: int) -> None:
        if not self.available:
            return
        target_size = (max(1, width_chars), max(1, height_chars))
        self.ensure_session()
        if self._last_size == target_size:
            return
        active_window = self._active_window_id()
        if self._last_size == target_size and self._last_resize_window == active_window:
            return
        self._run(
            "resize-window",
            "-t",
            self.session_name,
            "-x",
            str(target_size[0]),
            "-y",
            str(target_size[1]),
        )
        self._last_size = target_size
        self._last_resize_window = active_window

    def debug_windows(self, max_lines: int = 80) -> list[dict[str, object]]:
        windows = self.list_window_details()
        captures: list[dict[str, object]] = []
        for window in windows:
            snapshot = self.capture_window(window.index, height_rows=max_lines)
            captures.append(
                {
                    "index": window.index,
                    "name": window.name,
                    "active": window.active,
                    "pane_title": snapshot.pane_title,
                    "pane_path": snapshot.pane_path,
                    "pane_command": snapshot.pane_command,
                    "cursor_x": snapshot.cursor_x,
                    "cursor_y": snapshot.cursor_y,
                    "cursor_visible": snapshot.cursor_visible,
                    "pane_in_mode": snapshot.pane_in_mode,
                    "line_count": len(snapshot.lines),
                    "lines": snapshot.lines[-max_lines:],
                }
            )
        return captures

    def _pane_metadata(self, target: str) -> tuple[str, str, str, int, int, bool, bool]:
        proc = self._run(
            "display-message",
            "-p",
            "-t",
            target,
            "#{pane_title}\t#{pane_current_path}\t#{pane_current_command}\t#{cursor_x}\t#{cursor_y}\t#{cursor_flag}\t#{pane_in_mode}",
        )
        if proc.returncode != 0:
            return "-", "-", "-", 0, 0, False, False
        parts = proc.stdout.rstrip("\n").split("\t")
        if len(parts) != 7:
            return "-", "-", "-", 0, 0, False, False
        pane_title, pane_path, pane_command, cursor_x, cursor_y, cursor_flag, pane_in_mode = parts
        return (
            pane_title or "-",
            pane_path or "-",
            pane_command or "-",
            _parse_int(cursor_x),
            _parse_int(cursor_y),
            cursor_flag == "1",
            pane_in_mode == "1",
        )

    def _shell_command(self) -> str:
        shell = shutil.which("bash") or shutil.which("sh")
        if shell is None:
            return ""
        if shell.endswith("bash") and self.shell_rc_path.exists():
            return f"{shell} --rcfile {shlex_quote(str(self.shell_rc_path))} -i"
        return shell

    def _configure_session_shell(self) -> None:
        shell = shutil.which("bash") or shutil.which("sh")
        if shell:
            self._run("set-option", "-t", self.session_name, "default-shell", shell)
        shell_command = self._shell_command()
        if shell_command:
            self._run("set-option", "-t", self.session_name, "default-command", shell_command)

    def _active_window_id(self) -> str:
        proc = self._run("display-message", "-p", "-t", self._target(), "#{window_id}")
        window_id = proc.stdout.strip()
        return window_id or self._target()

    def _invalidate_resize_cache(self) -> None:
        self._last_size = None
        self._last_resize_window = None

    def _control_command(self, command: str) -> bool:
        process = self._ensure_control_process()
        if process is None or process.stdin is None:
            return False
        try:
            process.stdin.write(command + "\n")
            process.stdin.flush()
            return True
        except (BrokenPipeError, OSError):
            self._control_process = None
            return False

    def _ensure_control_process(self) -> subprocess.Popen[str] | None:
        if self._control_process is not None and self._control_process.poll() is None:
            return self._control_process
        if self._tmux_path is None:
            return None
        try:
            self._control_process = subprocess.Popen(
                [self._tmux_path, "-C", "attach-session", "-t", self.session_name],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except OSError:
            self._control_process = None
        return self._control_process

    def shutdown(self) -> None:
        process = self._control_process
        self._control_process = None
        if process is None:
            return
        stdin = process.stdin
        try:
            if stdin is not None:
                stdin.write("detach-client\n")
                stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        try:
            if stdin is not None:
                stdin.close()
        except (BrokenPipeError, OSError):
            pass
        try:
            process.terminate()
        except OSError:
            pass
        try:
            process.wait(timeout=0.2)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def _capture_pane_args(self, target: str, scroll_offset: int, row_count: int) -> tuple[str, ...]:
        row_count = max(1, row_count)
        scroll_offset = max(0, scroll_offset)
        if scroll_offset == 0:
            return ("capture-pane", "-p", "-e", "-S", f"-{row_count}", "-t", target)
        start = min(self.pane_history, scroll_offset + row_count)
        return (
            "capture-pane",
            "-p",
            "-e",
            "-S",
            f"-{start}",
            "-E",
            f"-{scroll_offset}",
            "-t",
            target,
        )


def shlex_quote(value: str) -> str:
    return shlex.quote(value)


def _parse_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
