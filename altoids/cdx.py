from __future__ import annotations

import argparse
import curses
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import textwrap
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SHORTCUT_APPROVE = "1"
SHORTCUT_SESSION = "2"
SHORTCUT_REJECT = "3"
SHORTCUT_REDIRECT = "4"


@dataclass(slots=True)
class FeedEntry:
    id: str
    kind: str
    summary: str
    detail: str = ""
    icon: str = "[ ]"
    color: str = "normal"


@dataclass(slots=True)
class ApprovalRequest:
    request_id: int | str
    method: str
    thread_id: str
    turn_id: str
    item_id: str
    reason: str = ""
    command: str = ""
    cwd: str = ""
    grant_root: str = ""


@dataclass(slots=True)
class RecentThread:
    thread_id: str
    preview: str
    cwd: str
    updated_at: float
    name: str | None = None


@dataclass(slots=True)
class CdxState:
    view: str = "startup"
    session_focus: str = "feed"
    screen_height: int = 0
    screen_width: int = 0
    composer: str = ""
    composer_cursor: int = 0
    composer_scroll: int = 0
    selected_entry: int = 0
    feed_top_entry: int = 0
    reader_open: bool = False
    reader_scroll: int = 0
    notice: str = ""
    awaiting_redirect_message: bool = False
    startup_index: int = 0
    startup_threads: list[RecentThread] = field(default_factory=list)
    thread_id: str = ""
    active_turn_id: str = ""
    pending_approvals: list[ApprovalRequest] = field(default_factory=list)
    feed: list[FeedEntry] = field(default_factory=list)
    item_index: dict[str, int] = field(default_factory=dict)


class AppServerClient:
    def __init__(self, codex_bin: str, home_override: str | None = None, xdg_state_home: str | None = None) -> None:
        self.codex_bin = codex_bin
        self.node_bin = self._resolve_node_bin(codex_bin)
        env = os.environ.copy()
        if home_override:
            env["HOME"] = home_override
        if xdg_state_home:
            env["XDG_STATE_HOME"] = xdg_state_home
        self.process = subprocess.Popen(
            [self.node_bin, self.codex_bin, "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        self._write_lock = threading.Lock()
        self._stderr_lock = threading.Lock()
        self._next_id = 1
        self._response_queues: dict[int, queue.Queue[dict[str, Any]]] = {}
        self.events: queue.SimpleQueue[tuple[str, dict[str, Any]]] = queue.SimpleQueue()
        self.stderr_lines: queue.SimpleQueue[str] = queue.SimpleQueue()
        self._recent_stderr: deque[str] = deque(maxlen=20)
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    @staticmethod
    def _resolve_node_bin(codex_bin: str) -> str:
        codex_path = Path(codex_bin)
        sibling = codex_path.with_name("node")
        if sibling.exists():
            return str(sibling)
        node_bin = shutil.which("node")
        if node_bin:
            return node_bin
        raise RuntimeError("Could not locate node binary for Codex")

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def initialize(self) -> dict[str, Any]:
        response = self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "altoids_cdx",
                    "title": "Altoids CDX",
                    "version": "0.1.0",
                },
                "capabilities": {
                    "experimentalApi": True,
                },
            },
        )
        self.notify("initialized")
        return response

    def request(self, method: str, params: dict[str, Any] | None = None, timeout: float = 15.0) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        response_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        self._response_queues[request_id] = response_queue
        payload: dict[str, Any] = {"id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._send(payload)
        try:
            response = response_queue.get(timeout=timeout)
        except queue.Empty as exc:
            raise RuntimeError(self._timeout_message(method, timeout)) from exc
        finally:
            self._response_queues.pop(request_id, None)
        if "error" in response:
            error = response["error"]
            message = self._format_error(error)
            raise RuntimeError(message)
        return response.get("result", {})

    def _timeout_message(self, method: str, timeout: float) -> str:
        message = f"Codex app-server did not respond to {method!r} within {timeout:g}s."
        returncode = self.process.poll()
        if returncode is not None:
            message = f"{message} It exited with status {returncode}."
        stderr = self.recent_stderr()
        if stderr:
            message = f"{message}\nRecent app-server stderr:\n" + "\n".join(stderr[-8:])
            if any("readonly database" in line or "read-only database" in line for line in stderr):
                message = (
                    f"{message}\nHint: Codex could not write its state DB. "
                    "Use --xdg-state-home or --home-override with a writable directory."
                )
        return message

    def recent_stderr(self) -> list[str]:
        with self._stderr_lock:
            return list(self._recent_stderr)

    @staticmethod
    def _format_error(error: dict[str, Any]) -> str:
        message = error.get("message", "Unknown app-server error")
        data = error.get("data")
        if isinstance(data, dict):
            code = data.get("code")
            if code == "activeTurnNotSteerable":
                turn_kind = ""
                details = data.get("details")
                if isinstance(details, dict):
                    active = details.get("activeTurnNotSteerable")
                    if isinstance(active, dict):
                        turn_kind = active.get("turnKind") or ""
                if turn_kind:
                    return f"Active turn is not steerable ({turn_kind})."
                return "Active turn is not steerable."
            if code and message:
                return f"{message} [{code}]"
        return message

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"method": method}
        if params is not None:
            payload["params"] = params
        self._send(payload)

    def respond(self, request_id: int | str, result: dict[str, Any]) -> None:
        self._send({"id": request_id, "result": result})

    def _send(self, payload: dict[str, Any]) -> None:
        if self.process.stdin is None:
            raise RuntimeError("app-server stdin is not available")
        encoded = json.dumps(payload, separators=(",", ":"))
        with self._write_lock:
            self.process.stdin.write(f"{encoded}\n")
            self.process.stdin.flush()

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        for raw_line in self.process.stdout:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                self.stderr_lines.put(f"non-json stdout: {line}")
                continue
            if "id" in payload and "method" not in payload:
                request_id = payload.get("id")
                response_queue = self._response_queues.get(request_id)
                if response_queue is not None:
                    response_queue.put(payload)
                else:
                    self.events.put(("response", payload))
                continue
            if "id" in payload and "method" in payload:
                self.events.put(("server_request", payload))
                continue
            if "method" in payload:
                self.events.put(("notification", payload))

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        for raw_line in self.process.stderr:
            line = raw_line.rstrip()
            if line:
                with self._stderr_lock:
                    self._recent_stderr.append(line)
                self.stderr_lines.put(line)


class CdxApp:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.cwd = Path(args.cwd or Path.cwd()).resolve()
        self.client = AppServerClient(
            self._resolve_codex_bin(args.codex_bin),
            home_override=args.home_override,
            xdg_state_home=args.xdg_state_home,
        )
        self.state = CdxState()
        self.server_info = self.client.initialize()
        self._load_recent_threads()
        if args.thread_id:
            self._resume_thread(args.thread_id)

    @staticmethod
    def _resolve_codex_bin(configured: str | None) -> str:
        if configured:
            return configured
        direct = shutil.which("codex")
        if direct:
            return direct
        proc = subprocess.run(
            ["/bin/bash", "-ic", "command -v codex"],
            capture_output=True,
            text=True,
            check=False,
        )
        candidate = proc.stdout.strip().splitlines()
        if candidate:
            return candidate[-1]
        raise RuntimeError("Could not locate codex binary")

    def close(self) -> None:
        self.client.close()

    def run(self) -> int:
        try:
            return curses.wrapper(self._run)
        finally:
            self.close()

    def _run(self, stdscr) -> int:
        curses.noecho()
        curses.cbreak()
        stdscr.nodelay(True)
        stdscr.keypad(True)
        self._init_colors()

        while True:
            self._drain_protocol_events()
            self._drain_stderr()
            self._render(stdscr)
            if self._poll_input(stdscr):
                return 0
            if self.client.process.poll() is not None:
                self.state.notice = "Codex app-server exited."
                self._render(stdscr)
                return int(self.client.process.returncode or 0)
            time.sleep(0.05)

    def _load_recent_threads(self) -> None:
        try:
            result = self.client.request(
                "thread/list",
                {
                    "cwd": str(self.cwd),
                    "limit": 8,
                    "sortKey": "updated_at",
                    "sortDirection": "desc",
                },
            )
        except RuntimeError as exc:
            self.state.notice = str(exc)
            self.state.startup_threads = []
            return
        threads = result.get("data", [])
        self.state.startup_threads = [
            RecentThread(
                thread_id=thread["id"],
                preview=thread.get("preview", "") or "(no preview)",
                cwd=thread.get("cwd", ""),
                updated_at=float(thread.get("updatedAt", 0) or 0),
                name=thread.get("name"),
            )
            for thread in threads
        ]

    def _start_new_thread(self) -> None:
        try:
            result = self.client.request(
                "thread/start",
                {
                    "cwd": str(self.cwd),
                    "approvalPolicy": "on-request",
                    "approvalsReviewer": "user",
                    "sessionStartSource": "startup",
                },
            )
        except RuntimeError as exc:
            self.state.notice = str(exc)
            return
        self._activate_thread(result["thread"], clear_feed=True)
        self.state.view = "session"
        self.state.notice = "New thread started."

    def _resume_thread(self, thread_id: str) -> None:
        try:
            result = self.client.request(
                "thread/resume",
                {
                    "threadId": thread_id,
                    "cwd": str(self.cwd),
                    "approvalPolicy": "on-request",
                    "approvalsReviewer": "user",
                },
            )
        except RuntimeError as exc:
            self.state.notice = str(exc)
            return
        self._activate_thread(result["thread"], clear_feed=True)
        self.state.view = "session"
        self.state.notice = "Thread resumed."

    def _activate_thread(self, thread: dict[str, Any], clear_feed: bool) -> None:
        self.state.thread_id = thread["id"]
        self.state.active_turn_id = ""
        self.state.pending_approvals.clear()
        self.state.reader_open = False
        self.state.reader_scroll = 0
        self.state.feed_top_entry = 0
        self.state.session_focus = "feed"
        if clear_feed:
            self.state.feed.clear()
            self.state.item_index.clear()
            self.state.selected_entry = 0
        for turn in thread.get("turns", []):
            for item in turn.get("items", []):
                self._hydrate_item(item)
            if turn.get("status") == "inProgress":
                self.state.active_turn_id = turn["id"]

    def _hydrate_item(self, item: dict[str, Any]) -> None:
        entry = self._entry_from_item(item)
        if entry is not None:
            self._upsert_feed_entry(entry)

    def _drain_protocol_events(self) -> None:
        while True:
            try:
                event_type, payload = self.client.events.get_nowait()
            except queue.Empty:
                break
            if event_type == "notification":
                self._handle_notification(payload["method"], payload.get("params", {}))
            elif event_type == "server_request":
                self._handle_server_request(payload)

    def _drain_stderr(self) -> None:
        while True:
            try:
                line = self.client.stderr_lines.get_nowait()
            except queue.Empty:
                break
            if "WARNING: proceeding, even though we could not update PATH" in line:
                continue
            self.state.notice = line

    def _handle_notification(self, method: str, params: dict[str, Any]) -> None:
        if method == "thread/started":
            thread = params.get("thread", {})
            if thread.get("id") == self.state.thread_id:
                self.state.notice = ""
            return
        if method == "turn/started":
            if params.get("threadId") == self.state.thread_id:
                turn = params.get("turn", {})
                self.state.active_turn_id = turn.get("id", "")
            return
        if method == "turn/completed":
            if params.get("threadId") == self.state.thread_id:
                turn = params.get("turn", {})
                if turn.get("id") == self.state.active_turn_id:
                    self.state.active_turn_id = ""
                status = turn.get("status", "completed")
                message = "Turn completed."
                if status == "failed":
                    error = (turn.get("error") or {}).get("message")
                    message = error or "Turn failed."
                self.state.notice = message
            return
        if method == "thread/status/changed":
            return
        if method == "item/started":
            if params.get("threadId") == self.state.thread_id:
                entry = self._entry_from_item(params.get("item", {}))
                if entry is not None:
                    self._upsert_feed_entry(entry)
            return
        if method == "item/completed":
            if params.get("threadId") == self.state.thread_id:
                entry = self._entry_from_item(params.get("item", {}))
                if entry is not None:
                    self._upsert_feed_entry(entry)
            return
        if method == "item/agentMessage/delta":
            if params.get("threadId") == self.state.thread_id:
                item_id = params.get("itemId", "")
                delta = params.get("delta", "")
                self._append_agent_delta(item_id, delta)
            return
        if method == "item/commandExecution/outputDelta":
            if params.get("threadId") == self.state.thread_id:
                self._append_output_delta(params.get("itemId", ""), params.get("delta", ""))
            return
        if method == "item/fileChange/outputDelta":
            if params.get("threadId") == self.state.thread_id:
                self._append_output_delta(params.get("itemId", ""), params.get("delta", ""))
            return
        if method == "serverRequest/resolved":
            if params.get("threadId") == self.state.thread_id:
                request_id = params.get("requestId")
                self.state.pending_approvals = [item for item in self.state.pending_approvals if item.request_id != request_id]
            return
        if method in {"warning", "guardianWarning", "deprecationNotice", "configWarning"}:
            message = params.get("message") or params.get("text") or "Warning"
            if method == "configWarning":
                message = params.get("summary") or message
            self.state.notice = message
            return
        if method == "error":
            message = params.get("message") or "Server error"
            self.state.notice = message
            return

    def _handle_server_request(self, payload: dict[str, Any]) -> None:
        method = payload["method"]
        params = payload.get("params", {})
        if params.get("threadId") and params.get("threadId") != self.state.thread_id:
            return
        if method == "item/commandExecution/requestApproval":
            self.state.pending_approvals.append(
                ApprovalRequest(
                    request_id=payload["id"],
                    method=method,
                    thread_id=params["threadId"],
                    turn_id=params["turnId"],
                    item_id=params["itemId"],
                    reason=params.get("reason") or "",
                    command=params.get("command") or "",
                    cwd=params.get("cwd") or "",
                )
            )
            self.state.notice = "Approval requested."
            return
        if method == "item/fileChange/requestApproval":
            self.state.pending_approvals.append(
                ApprovalRequest(
                    request_id=payload["id"],
                    method=method,
                    thread_id=params["threadId"],
                    turn_id=params["turnId"],
                    item_id=params["itemId"],
                    reason=params.get("reason") or "",
                    grant_root=params.get("grantRoot") or "",
                )
            )
            self.state.notice = "File change approval requested."
            return
        self.state.notice = f"Unsupported server request: {method}"

    def _render(self, stdscr) -> None:
        stdscr.erase()
        self.state.screen_height, self.state.screen_width = stdscr.getmaxyx()
        if self.state.view == "startup":
            self._render_startup(stdscr)
        else:
            self._render_session(stdscr)
        stdscr.refresh()

    def _render_startup(self, stdscr) -> None:
        height, width = stdscr.getmaxyx()
        self._draw_line(stdscr, 0, 0, self._fit(f"cdx startup  {self._short_path(str(self.cwd))}", width - 1), "accent")
        self._draw_line(stdscr, 1, 0, "=", "dim", fill=True)
        rows = ["new thread"] + [
            self._fit(
                f"{thread.name or thread.preview}  [{time.strftime('%H:%M', time.localtime(thread.updated_at))}]",
                width - 6,
            )
            for thread in self.state.startup_threads
        ]
        self._draw_line(stdscr, 3, 0, "Select a thread to resume or start a new one.", "normal")
        if self.state.notice:
            self._draw_line(stdscr, 4, 0, self._fit(f"[!] {self.state.notice}", width - 1), "warn")
        for index, row in enumerate(rows):
            marker = ">" if index == self.state.startup_index else " "
            color = "accent" if index == self.state.startup_index else "normal"
            self._draw_line(stdscr, 6 + index, 0, f"{marker} {row}", color)

    def _render_session(self, stdscr) -> None:
        height, width = stdscr.getmaxyx()
        pending = self.state.pending_approvals[0] if self.state.pending_approvals else None
        ask = " ask" if pending is not None else ""
        focus = "reader" if self.state.reader_open else self.state.session_focus
        header = f"cdx  cx:{self.state.thread_id[-8:] or 'new'}  {self._short_path(str(self.cwd))}{ask}  {focus}"
        self._draw_line(stdscr, 0, 0, self._fit(header, width - 1), "accent")
        self._draw_line(stdscr, 1, 0, "=", "dim", fill=True)
        top = 2
        if pending is not None:
            preview = pending.command or pending.reason or pending.grant_root or "approval requested"
            self._draw_line(stdscr, top, 0, self._fit(f"[?] waiting: {preview}", width - 1), "warn")
            top += 1
        elif self.state.notice:
            self._draw_line(stdscr, top, 0, self._fit(f"[*] {self.state.notice}", width - 1), "dim")
            top += 1
        feed_height = max(4, height - top - 2)
        self._render_feed(stdscr, top, width - 1, feed_height)
        composer_text, cursor_x = self._composer_view(width - 1)
        composer_color = "accent" if self.state.session_focus == "composer" and not self.state.reader_open else "dim"
        self._draw_line(stdscr, height - 1, 0, composer_text, composer_color)
        self._set_cursor(stdscr, self.state.session_focus == "composer" and not self.state.reader_open, height - 1, cursor_x)
        if self.state.reader_open:
            self._render_reader(stdscr)

    def _poll_input(self, stdscr) -> bool:
        while True:
            try:
                key = stdscr.get_wch()
            except curses.error:
                return False
            if self.state.view == "startup":
                return self._handle_startup_key(key)
            return self._handle_session_key(key)

    def _handle_startup_key(self, key: object) -> bool:
        total = len(self.state.startup_threads) + 1
        if key == curses.KEY_UP:
            self.state.startup_index = (self.state.startup_index - 1) % total
            return False
        if key == curses.KEY_DOWN:
            self.state.startup_index = (self.state.startup_index + 1) % total
            return False
        if key in {"\n", "\r"} or key == curses.KEY_ENTER:
            if self.state.startup_index == 0:
                self._start_new_thread()
            else:
                thread = self.state.startup_threads[self.state.startup_index - 1]
                self._resume_thread(thread.thread_id)
            return False
        if key in {"q", "Q"}:
            return True
        return False

    def _handle_session_key(self, key: object) -> bool:
        if self.state.reader_open:
            return self._handle_reader_key(key)
        if key == SHORTCUT_APPROVE and self.state.pending_approvals:
            self._reply_to_approval("accept")
            return False
        if key == SHORTCUT_SESSION and self.state.pending_approvals:
            self._reply_to_approval("acceptForSession")
            return False
        if key == SHORTCUT_REJECT and self.state.pending_approvals:
            self._reply_to_approval("decline")
            return False
        if key == SHORTCUT_REDIRECT and self.state.pending_approvals:
            self._reply_to_approval("decline", redirect=True)
            return False
        if key == "\t":
            self.state.session_focus = "composer" if self.state.session_focus == "feed" else "feed"
            return False
        if self.state.session_focus == "feed":
            return self._handle_feed_key(key)
        return self._handle_composer_key(key)

    def _handle_feed_key(self, key: object) -> bool:
        if key in {"\n", "\r"} or key == curses.KEY_ENTER:
            self._open_reader()
            return False
        if key in {27, "\x1b"}:
            return True
        if key == curses.KEY_UP:
            self._move_selection(-1)
            return False
        if key == curses.KEY_DOWN:
            self._move_selection(1)
            return False
        if key == curses.KEY_PPAGE:
            self._move_selection(-6)
            return False
        if key == curses.KEY_NPAGE:
            self._move_selection(6)
            return False
        if key == curses.KEY_HOME:
            self._set_selection(0)
            return False
        if key == curses.KEY_END:
            self._set_selection(len(self.state.feed) - 1)
            return False
        if key == curses.KEY_RESIZE:
            return False
        if self._is_ctrl_char(key, 12):
            self._load_recent_threads()
            self.state.notice = "Recent threads refreshed."
            return False
        if key in {"q", "Q"}:
            return True
        return False

    def _handle_composer_key(self, key: object) -> bool:
        if key in {"\n", "\r"} or key == curses.KEY_ENTER:
            self._submit_composer()
            return False
        if key in {27, "\x1b"}:
            if self.state.composer:
                self.state.composer = ""
                self.state.composer_cursor = 0
                self.state.composer_scroll = 0
                self.state.awaiting_redirect_message = False
                self.state.notice = "Composer cleared."
                return False
            self.state.session_focus = "feed"
            return False
        if key in {curses.KEY_BACKSPACE, "\b", "\x7f"}:
            self._delete_composer_before_cursor()
            return False
        if key == curses.KEY_DC:
            self._delete_composer_at_cursor()
            return False
        if key == curses.KEY_LEFT:
            self.state.composer_cursor = max(0, self.state.composer_cursor - 1)
            self._clamp_composer_view()
            return False
        if key == curses.KEY_RIGHT:
            self.state.composer_cursor = min(len(self.state.composer), self.state.composer_cursor + 1)
            self._clamp_composer_view()
            return False
        if key == curses.KEY_HOME:
            self.state.composer_cursor = 0
            self._clamp_composer_view()
            return False
        if key == curses.KEY_END:
            self.state.composer_cursor = len(self.state.composer)
            self._clamp_composer_view()
            return False
        if key == curses.KEY_RESIZE:
            self._clamp_composer_view()
            return False
        if self._is_ctrl_char(key, 12):
            self._load_recent_threads()
            self.state.notice = "Recent threads refreshed."
            return False
        if isinstance(key, str) and key.isprintable():
            self._insert_composer_text(key)
            return False
        return False

    def _handle_reader_key(self, key: object) -> bool:
        if key in {"\n", "\r"} or key == curses.KEY_ENTER or key in {27, "\x1b"}:
            self.state.reader_open = False
            self.state.reader_scroll = 0
            return False
        lines = self._reader_lines(max(1, self.state.screen_width - 8))
        viewport = max(1, self.state.screen_height - 8)
        max_scroll = max(0, len(lines) - viewport)
        if key == curses.KEY_UP:
            self.state.reader_scroll = max(0, self.state.reader_scroll - 1)
            return False
        if key == curses.KEY_DOWN:
            self.state.reader_scroll = min(max_scroll, self.state.reader_scroll + 1)
            return False
        if key == curses.KEY_PPAGE:
            self.state.reader_scroll = max(0, self.state.reader_scroll - viewport)
            return False
        if key == curses.KEY_NPAGE:
            self.state.reader_scroll = min(max_scroll, self.state.reader_scroll + viewport)
            return False
        if key == curses.KEY_HOME:
            self.state.reader_scroll = 0
            return False
        if key == curses.KEY_END:
            self.state.reader_scroll = max_scroll
            return False
        if key == curses.KEY_RESIZE:
            return False
        return False

    def _submit_composer(self) -> None:
        message = self.state.composer.strip()
        if not message:
            self.state.notice = "Composer is empty."
            return
        try:
            if self.state.active_turn_id:
                result = self.client.request(
                    "turn/steer",
                    {
                        "threadId": self.state.thread_id,
                        "expectedTurnId": self.state.active_turn_id,
                        "input": [{"type": "text", "text": message, "text_elements": []}],
                    },
                )
                self.state.active_turn_id = result.get("turnId", self.state.active_turn_id)
                self.state.notice = "Steer sent."
            else:
                result = self.client.request(
                    "turn/start",
                    {
                        "threadId": self.state.thread_id,
                        "cwd": str(self.cwd),
                        "input": [{"type": "text", "text": message, "text_elements": []}],
                    },
                )
                turn = result.get("turn", {})
                self.state.active_turn_id = turn.get("id", "")
                self.state.notice = "Turn started."
        except RuntimeError as exc:
            self.state.notice = str(exc)
            return
        self.state.awaiting_redirect_message = False
        self.state.composer = ""
        self.state.composer_cursor = 0
        self.state.composer_scroll = 0

    def _reply_to_approval(self, decision: str, redirect: bool = False) -> None:
        if not self.state.pending_approvals:
            self.state.notice = "No pending approval."
            return
        approval = self.state.pending_approvals[0]
        payload = {"decision": decision}
        self.client.respond(approval.request_id, payload)
        if redirect:
            self.state.awaiting_redirect_message = True
            self.state.notice = "Approval declined. Type redirect message and press Enter."
        else:
            self.state.notice = "Approval response sent."

    def _entry_from_item(self, item: dict[str, Any]) -> FeedEntry | None:
        item_type = item.get("type")
        item_id = item.get("id", f"item-{time.time_ns()}")
        if item_type == "userMessage":
            text = self._user_input_text(item.get("content", []))
            return FeedEntry(item_id, "user", text or "(empty message)", icon=":>", color="normal")
        if item_type == "agentMessage":
            text = item.get("text", "") or "(agent message)"
            phase = item.get("phase")
            icon = "<~" if phase == "commentary" else "<:"
            color = "dim" if phase == "commentary" else "normal"
            return FeedEntry(item_id, "agent", text, icon=icon, color=color)
        if item_type == "plan":
            return FeedEntry(item_id, "plan", item.get("text", "plan"), icon="<~", color="dim")
        if item_type == "commandExecution":
            command = item.get("command", "command")
            status = item.get("status", "")
            summary = command
            icon = "[#]"
            color = "normal"
            if status == "completed":
                icon = "[+]"
                color = "ok"
            elif status == "failed":
                icon = "[!]"
                color = "error"
            elif status == "inProgress":
                icon = "[#]"
            detail = item.get("aggregatedOutput") or ""
            return FeedEntry(item_id, "command", summary, detail=detail, icon=icon, color=color)
        if item_type == "fileChange":
            status = item.get("status", "")
            changes = item.get("changes", [])
            summary = f"{len(changes)} file change{'s' if len(changes) != 1 else ''}"
            icon = "[#]"
            color = "normal"
            if status == "completed":
                icon = "[+]"
                color = "ok"
            elif status == "failed":
                icon = "[!]"
                color = "error"
            elif status == "declined":
                icon = "[!]"
                color = "warn"
            return FeedEntry(item_id, "file", summary, icon=icon, color=color)
        if item_type in {"mcpToolCall", "dynamicToolCall"}:
            tool = item.get("tool", "tool")
            status = item.get("status", "")
            icon = "[#]"
            color = "normal"
            if status == "completed":
                icon = "[+]"
                color = "ok"
            elif status == "failed":
                icon = "[!]"
                color = "error"
            return FeedEntry(item_id, "tool", tool, icon=icon, color=color)
        if item_type == "reasoning":
            summary = " ".join(item.get("summary", [])) or "reasoning"
            return FeedEntry(item_id, "reasoning", summary, icon="<~", color="dim")
        return None

    @staticmethod
    def _user_input_text(content: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for item in content:
            if item.get("type") == "text" and item.get("text"):
                parts.append(str(item["text"]))
        return " ".join(parts).strip()

    def _upsert_feed_entry(self, entry: FeedEntry | None) -> None:
        if entry is None:
            return
        index = self.state.item_index.get(entry.id)
        if index is None:
            was_at_tail = not self.state.feed or self.state.selected_entry >= len(self.state.feed) - 1
            self.state.item_index[entry.id] = len(self.state.feed)
            self.state.feed.append(entry)
            if was_at_tail:
                self._set_selection(len(self.state.feed) - 1)
            return
        self.state.feed[index] = entry

    def _append_agent_delta(self, item_id: str, delta: str) -> None:
        index = self.state.item_index.get(item_id)
        if index is None:
            entry = FeedEntry(item_id, "agent", delta, icon="<:", color="normal")
            self._upsert_feed_entry(entry)
            return
        self.state.feed[index].summary += delta

    def _append_output_delta(self, item_id: str, delta: str) -> None:
        index = self.state.item_index.get(item_id)
        if index is None:
            return
        current = self.state.feed[index]
        current.detail = f"{current.detail}{delta}".strip()

    def _render_feed(self, stdscr, top: int, width: int, height: int) -> None:
        if not self.state.feed:
            self._draw_line(stdscr, top, 0, "<_> waiting for session activity", "dim")
            return
        self._ensure_feed_viewport(width, height)
        row = top
        index = self.state.feed_top_entry
        while index < len(self.state.feed) and row < top + height:
            entry = self.state.feed[index]
            lines = self._entry_preview_lines(entry, width)
            color = self._entry_color(entry)
            if index == self.state.selected_entry:
                color = "selected_warn" if color == "warn" else "selected"
            for line in lines:
                if row >= top + height:
                    break
                self._draw_line(stdscr, row, 0, line, color)
                row += 1
            index += 1
        while row < top + height:
            self._draw_line(stdscr, row, 0, "", "normal")
            row += 1

    def _entry_preview_lines(self, entry: FeedEntry, width: int) -> list[str]:
        summary = entry.summary.replace("\n", " ")
        summary_lines = self._wrap(f"{entry.icon} {summary}", width)
        detail = entry.detail.replace("\n", " ").strip()
        if entry.kind in {"user", "agent", "plan", "reasoning"}:
            return self._clamp_lines(summary_lines, 2, width)
        if entry.kind == "command":
            lines = self._clamp_lines(summary_lines, 1, width)
            if detail:
                lines.extend(self._clamp_lines(self._wrap(f"    {detail}", width), 1, width))
            return lines
        lines = summary_lines
        if detail:
            lines.extend(self._clamp_lines(self._wrap(f"    {detail}", width), 1, width))
        return self._clamp_lines(lines, 3, width)

    def _entry_total_rows(self, entry: FeedEntry, width: int) -> int:
        return len(self._entry_preview_lines(entry, width))

    def _ensure_feed_viewport(self, width: int, height: int) -> None:
        if not self.state.feed:
            self.state.feed_top_entry = 0
            self.state.selected_entry = 0
            return
        self.state.selected_entry = min(max(0, self.state.selected_entry), len(self.state.feed) - 1)
        self.state.feed_top_entry = min(max(0, self.state.feed_top_entry), len(self.state.feed) - 1)
        selected = self.state.selected_entry
        if selected < self.state.feed_top_entry:
            self.state.feed_top_entry = selected
        while True:
            visible_rows = 0
            index = self.state.feed_top_entry
            selected_visible = False
            while index < len(self.state.feed) and visible_rows < height:
                rows = self._entry_total_rows(self.state.feed[index], width)
                if index == selected:
                    selected_visible = visible_rows < height
                visible_rows += rows
                index += 1
            if selected_visible:
                break
            self.state.feed_top_entry += 1
            if self.state.feed_top_entry >= len(self.state.feed):
                self.state.feed_top_entry = max(0, len(self.state.feed) - 1)
                break

    def _set_selection(self, index: int) -> None:
        if not self.state.feed:
            self.state.selected_entry = 0
            self.state.feed_top_entry = 0
            return
        self.state.selected_entry = min(max(0, index), len(self.state.feed) - 1)

    def _move_selection(self, delta: int) -> None:
        if not self.state.feed:
            return
        self._set_selection(self.state.selected_entry + delta)

    def _open_reader(self) -> None:
        if not self.state.feed:
            return
        self.state.reader_open = True
        self.state.reader_scroll = 0

    def _reader_lines(self, width: int) -> list[str]:
        if not self.state.feed:
            return ["No message selected."]
        entry = self.state.feed[self.state.selected_entry]
        lines = [f"{entry.icon} {self._entry_label(entry)}", ""]
        lines.extend(self._wrap(entry.summary.replace("\n", " "), width))
        if entry.detail:
            lines.extend(["", "detail:", ""])
            lines.extend(self._wrap(entry.detail.replace("\n", " "), width))
        return lines

    def _render_reader(self, stdscr) -> None:
        height, width = stdscr.getmaxyx()
        box_top = 2
        box_left = 2
        box_height = max(6, height - 4)
        box_width = max(12, width - 4)
        content_width = max(1, box_width - 4)
        content_height = max(1, box_height - 4)
        lines = self._reader_lines(content_width)
        max_scroll = max(0, len(lines) - content_height)
        self.state.reader_scroll = min(max(0, self.state.reader_scroll), max_scroll)
        self._fill_rect(stdscr, box_top, box_left, box_height, box_width, "overlay")
        self._draw_line(stdscr, box_top, box_left + 2, self._fit("message", box_width - 4), "overlay_title")
        bottom_hint = "Enter/Esc close  Up/Down scroll"
        self._draw_line(stdscr, box_top + box_height - 1, box_left + 2, self._fit(bottom_hint, box_width - 4), "dim")
        visible = lines[self.state.reader_scroll : self.state.reader_scroll + content_height]
        for row, line in enumerate(visible, start=box_top + 2):
            if row >= box_top + box_height - 1:
                break
            self._draw_line(stdscr, row, box_left + 2, line, "overlay")
        self._set_cursor(stdscr, False, 0, 0)

    def _composer_view(self, width: int) -> tuple[str, int]:
        prompt = "> "
        viewport = max(1, width - len(prompt))
        self._clamp_composer_view(viewport)
        visible = self.state.composer[self.state.composer_scroll : self.state.composer_scroll + viewport]
        rendered = f"{prompt}{visible}"
        cursor_x = len(prompt) + self.state.composer_cursor - self.state.composer_scroll
        return self._fit(rendered, width), max(0, min(width - 1, cursor_x))

    def _clamp_composer_view(self, viewport: int | None = None) -> None:
        if viewport is None:
            viewport = 1
        self.state.composer_cursor = min(max(0, self.state.composer_cursor), len(self.state.composer))
        if self.state.composer_cursor < self.state.composer_scroll:
            self.state.composer_scroll = self.state.composer_cursor
        elif self.state.composer_cursor >= self.state.composer_scroll + viewport:
            self.state.composer_scroll = self.state.composer_cursor - viewport
        self.state.composer_scroll = min(max(0, self.state.composer_scroll), len(self.state.composer))

    def _insert_composer_text(self, text: str) -> None:
        cursor = self.state.composer_cursor
        self.state.composer = f"{self.state.composer[:cursor]}{text}{self.state.composer[cursor:]}"
        self.state.composer_cursor = cursor + len(text)
        self._clamp_composer_view()
        self.state.session_focus = "composer"

    def _delete_composer_before_cursor(self) -> None:
        cursor = self.state.composer_cursor
        if cursor <= 0:
            return
        self.state.composer = f"{self.state.composer[:cursor - 1]}{self.state.composer[cursor:]}"
        self.state.composer_cursor = cursor - 1
        self._clamp_composer_view()

    def _delete_composer_at_cursor(self) -> None:
        cursor = self.state.composer_cursor
        if cursor >= len(self.state.composer):
            return
        self.state.composer = f"{self.state.composer[:cursor]}{self.state.composer[cursor + 1:]}"
        self._clamp_composer_view()

    @staticmethod
    def _fit(text: str, width: int) -> str:
        if width <= 0:
            return ""
        cleaned = text.replace("\n", " ")
        if len(cleaned) <= width:
            return cleaned
        if width <= 1:
            return cleaned[:width]
        return f"{cleaned[: width - 1]}>"

    @staticmethod
    def _wrap(text: str, width: int) -> list[str]:
        if width <= 0:
            return [""]
        if width < 8:
            compact = text.replace("\n", " ")
            return [compact[i : i + width] for i in range(0, len(compact), width)] or [""]
        return textwrap.wrap(text.replace("\n", " "), width=width) or [""]

    @staticmethod
    def _clamp_lines(lines: list[str], limit: int, width: int) -> list[str]:
        if len(lines) <= limit:
            return lines
        trimmed = lines[:limit]
        if width > 1:
            base = trimmed[-1][: max(0, width - 2)].rstrip()
            trimmed[-1] = f"{base}>"
        return trimmed

    @staticmethod
    def _is_ctrl_char(value: object, codepoint: int) -> bool:
        return isinstance(value, str) and len(value) == 1 and ord(value) == codepoint

    @staticmethod
    def _short_path(raw_path: str, max_parts: int = 2) -> str:
        path = Path(raw_path)
        parts = [part for part in path.parts if part not in {"/", ""}]
        if len(parts) <= max_parts:
            return str(path)
        home = str(Path.home())
        if raw_path.startswith(home):
            return f"~/{'/'.join(parts[-max_parts:])}"
        return "/".join(parts[-max_parts:])

    def _draw_line(self, stdscr, y: int, x: int, text: str, color: str, fill: bool = False) -> None:
        height, width = stdscr.getmaxyx()
        if y < 0 or y >= height or x >= width:
            return
        rendered = text
        if fill:
            rendered = (text * max(1, width - x))[: max(0, width - x - 1)]
        try:
            stdscr.addnstr(y, x, rendered, max(0, width - x - 1), self._color_attr(color))
        except curses.error:
            return

    def _fill_rect(self, stdscr, top: int, left: int, height: int, width: int, color: str) -> None:
        for row in range(top, top + height):
            self._draw_line(stdscr, row, left, " " * max(0, width - 1), color)

    @staticmethod
    def _entry_label(entry: FeedEntry) -> str:
        mapping = {
            "user": "User",
            "agent": "Agent",
            "plan": "Plan",
            "command": "Command",
            "file": "File Change",
            "tool": "Tool",
            "reasoning": "Reasoning",
        }
        return mapping.get(entry.kind, entry.kind.title())

    @staticmethod
    def _entry_color(entry: FeedEntry) -> str:
        return entry.color

    @staticmethod
    def _set_cursor(stdscr, visible: bool, y: int, x: int) -> None:
        try:
            curses.curs_set(1 if visible else 0)
        except curses.error:
            pass
        if visible:
            try:
                stdscr.move(y, x)
            except curses.error:
                pass

    @staticmethod
    def _init_colors() -> None:
        if not curses.has_colors():
            return
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
        curses.init_pair(4, curses.COLOR_RED, -1)
        curses.init_pair(5, curses.COLOR_WHITE, -1)
        curses.init_pair(6, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(7, curses.COLOR_BLACK, curses.COLOR_YELLOW)
        curses.init_pair(8, curses.COLOR_BLACK, curses.COLOR_WHITE)

    @staticmethod
    def _color_attr(color: str) -> int:
        if not curses.has_colors():
            return curses.A_NORMAL
        mapping = {
            "accent": curses.color_pair(1) | curses.A_BOLD,
            "ok": curses.color_pair(2),
            "warn": curses.color_pair(3),
            "error": curses.color_pair(4),
            "dim": curses.color_pair(5) | curses.A_DIM,
            "normal": curses.color_pair(5),
            "selected": curses.color_pair(6) | curses.A_BOLD,
            "selected_warn": curses.color_pair(7) | curses.A_BOLD,
            "overlay": curses.color_pair(8),
            "overlay_title": curses.color_pair(8) | curses.A_BOLD,
        }
        return mapping.get(color, curses.A_NORMAL)

    @staticmethod
    def _flow_color(line: str) -> str:
        if line.startswith("[?]"):
            return "warn"
        if line.startswith("[!]"):
            return "error"
        if line.startswith("[+]") or line.startswith("[*]"):
            return "ok"
        if line.startswith("<~"):
            return "dim"
        return "normal"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Codex dashboard wrapper for the Altoids cyberdeck")
    parser.add_argument("--cwd", default=None, help="Working directory for the Codex thread")
    parser.add_argument("--thread-id", default=None, help="Resume a specific thread id immediately")
    parser.add_argument("--codex-bin", default=None, help="Path to the codex CLI binary")
    parser.add_argument("--home-override", default=None, help="Override HOME for the app-server subprocess")
    parser.add_argument("--xdg-state-home", default=None, help="Override XDG_STATE_HOME for the app-server subprocess")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        app = CdxApp(args)
        return app.run()
    except RuntimeError as exc:
        print(f"cdx: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
