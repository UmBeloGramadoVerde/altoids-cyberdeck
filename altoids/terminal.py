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
    active_window: str
    pane_title: str
    pane_path: str
    pane_command: str


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
                active_window="-",
                pane_title="-",
                pane_path="-",
                pane_command="-",
            )
        self.ensure_session()
        proc = self._run("capture-pane", "-p", "-e", "-J", "-S", f"-{self.pane_history}", "-t", self._target())
        all_lines = proc.stdout.splitlines()
        end = max(0, len(all_lines) - scroll_offset)
        row_count = height_rows or self.height_chars
        start = max(0, end - row_count)
        lines = all_lines[start:end]
        windows = self.list_windows()
        active = next((window for window in windows if window.startswith("*")), windows[0] if windows else "-")
        pane_title, pane_path, pane_command = self._pane_metadata()
        return TerminalSnapshot(
            lines=lines,
            window_count=len(windows),
            active_window=active,
            pane_title=pane_title,
            pane_path=pane_path,
            pane_command=pane_command,
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

    def create_window(self, name: str | None = None) -> None:
        if not self.available:
            return
        self.ensure_session()
        args = ["new-window", "-t", self.session_name]
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

    def _pane_metadata(self) -> tuple[str, str, str]:
        proc = self._run(
            "display-message",
            "-p",
            "-t",
            self._target(),
            "#{pane_title}\t#{pane_current_path}\t#{pane_current_command}",
        )
        if proc.returncode != 0:
            return "-", "-", "-"
        parts = proc.stdout.rstrip("\n").split("\t", 2)
        if len(parts) != 3:
            return "-", "-", "-"
        return tuple(part or "-" for part in parts)  # type: ignore[return-value]

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
