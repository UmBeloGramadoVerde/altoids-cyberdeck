from __future__ import annotations

import shlex
import shutil
import subprocess
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

    @property
    def available(self) -> bool:
        return shutil.which("tmux") is not None

    def _run(self, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["tmux", *args],
            check=check,
            text=True,
            capture_output=True,
        )

    def _target(self) -> str:
        return self.session_name

    def _start_directory(self) -> str:
        return str(Path.home())

    def ensure_session(self) -> None:
        if not self.available:
            return
        has = self._run("has-session", "-t", self.session_name)
        if has.returncode == 0:
            self._configure_session_shell()
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

    def capture(self, scroll_offset: int = 0, height_rows: int | None = None) -> TerminalSnapshot:
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
        proc = self._run("capture-pane", "-p", "-e", "-J", "-S", f"-{self.pane_history}", "-t", self._target())
        all_lines = proc.stdout.splitlines()
        end = max(0, len(all_lines) - scroll_offset)
        row_count = height_rows or self.height_chars
        start = max(0, end - row_count)
        lines = all_lines[start:end]
        windows = self.list_windows()
        active_index = next((index for index, window in enumerate(windows) if window.startswith("*")), -1)
        active = windows[active_index] if active_index >= 0 else (windows[0] if windows else "-")
        pane_title, pane_path, pane_command, cursor_x, cursor_y, cursor_visible, pane_in_mode = self._pane_metadata()
        return TerminalSnapshot(
            lines=lines,
            window_count=len(windows),
            active_window_position=active_index + 1 if active_index >= 0 else 0,
            active_window=active,
            pane_title=pane_title,
            pane_path=pane_path,
            pane_command=pane_command,
            cursor_x=cursor_x,
            cursor_y=cursor_y,
            cursor_visible=cursor_visible,
            pane_in_mode=pane_in_mode,
        )

    def list_windows(self) -> list[str]:
        if not self.available:
            return []
        self.ensure_session()
        proc = self._run("list-windows", "-F", "#{?window_active,*, }#{window_index}:#{window_name}", "-t", self.session_name)
        if proc.returncode != 0:
            return []
        return [line for line in proc.stdout.splitlines() if line]

    def send_keys(self, keys: Iterable[str]) -> None:
        if not self.available:
            return
        self.ensure_session()
        self._run("send-keys", "-t", self._target(), *list(keys))

    def send_enter(self) -> None:
        self.send_keys(["Enter"])

    def select_next_window(self) -> None:
        if not self.available:
            return
        self.ensure_session()
        self._run("next-window", "-t", self.session_name)

    def select_previous_window(self) -> None:
        if not self.available:
            return
        self.ensure_session()
        self._run("previous-window", "-t", self.session_name)

    def select_window(self, index: int) -> None:
        if not self.available or index < 1:
            return
        self.ensure_session()
        self._run("select-window", "-t", f"{self.session_name}:{index}")

    def create_window(self, name: str | None = None) -> None:
        if not self.available:
            return
        self.ensure_session()
        args = ["new-window", "-t", self.session_name, "-c", self._start_directory()]
        if name:
            args.extend(["-n", name])
        shell_command = self._shell_command()
        if shell_command:
            args.append(shell_command)
        self._run(*args)

    def close_active_window(self) -> None:
        if not self.available:
            return
        self.ensure_session()
        self._run("kill-window", "-t", self._target())
        self.ensure_session()

    def send_text(self, text: str) -> None:
        if not text:
            return
        self.send_keys([text])

    def resize(self, width_chars: int, height_chars: int) -> None:
        if not self.available:
            return
        target_size = (max(1, width_chars), max(1, height_chars))
        if self._last_size == target_size:
            return
        self.ensure_session()
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

    def _pane_metadata(self) -> tuple[str, str, str, int, int, bool, bool]:
        proc = self._run(
            "display-message",
            "-p",
            "-t",
            self._target(),
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


def shlex_quote(value: str) -> str:
    return shlex.quote(value)


def _parse_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
