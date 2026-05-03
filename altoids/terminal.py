from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Iterable


@dataclass(slots=True)
class TerminalSnapshot:
    lines: list[str]
    window_count: int
    active_window: str


class TmuxManager:
    def __init__(self, session_name: str, width_chars: int, height_chars: int, pane_history: int) -> None:
        self.session_name = session_name
        self.width_chars = width_chars
        self.height_chars = height_chars
        self.pane_history = pane_history

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
            return
        self._run(
            "new-session",
            "-d",
            "-s",
            self.session_name,
            "-x",
            str(self.width_chars),
            "-y",
            str(self.height_chars),
        )

    def capture(self, scroll_offset: int = 0) -> TerminalSnapshot:
        if not self.available:
            return TerminalSnapshot(
                lines=[
                    "tmux not installed",
                    "",
                    "Install tmux to enable persistent shells.",
                ],
                window_count=0,
                active_window="-",
            )
        self.ensure_session()
        proc = self._run("capture-pane", "-p", "-J", "-S", f"-{self.pane_history}", "-t", self._target())
        all_lines = proc.stdout.splitlines()
        end = max(0, len(all_lines) - scroll_offset)
        start = max(0, end - self.height_chars)
        lines = all_lines[start:end]
        windows = self.list_windows()
        active = next((window for window in windows if window.startswith("*")), windows[0] if windows else "-")
        return TerminalSnapshot(lines=lines, window_count=len(windows), active_window=active)

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

    def send_text(self, text: str) -> None:
        if not text:
            return
        self.send_keys([text])
