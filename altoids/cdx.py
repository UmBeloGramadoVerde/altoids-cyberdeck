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
INITIALIZE_TIMEOUT_SECONDS = 120.0
REQUEST_TIMEOUT_SECONDS = 30.0


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


@dataclass(frozen=True, slots=True)
class ComposerLine:
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class PasteText:
    text: str


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
    active_item_id: str = ""
    active_item_label: str = ""
    pending_approvals: list[ApprovalRequest] = field(default_factory=list)
    feed: list[FeedEntry] = field(default_factory=list)
    item_index: dict[str, int] = field(default_factory=dict)


class AppServerClient:
    def __init__(self, codex_bin: str, home_override: str | None = None, xdg_state_home: str | None = None) -> None:
        self.codex_bin = codex_bin
        env = os.environ.copy()
        if home_override:
            env["HOME"] = home_override
        if xdg_state_home:
            env["XDG_STATE_HOME"] = xdg_state_home
        self.process = subprocess.Popen(
            self._launch_command(codex_bin),
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
    def _launch_command(codex_bin: str) -> list[str]:
        if AppServerClient._requires_node(codex_bin):
            return [AppServerClient._resolve_node_bin(codex_bin), codex_bin, "app-server", "--listen", "stdio://"]
        return [codex_bin, "app-server", "--listen", "stdio://"]

    @staticmethod
    def _requires_node(codex_bin: str) -> bool:
        codex_path = Path(codex_bin)
        return codex_path.suffix == ".js" and not os.access(codex_path, os.X_OK)

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
            timeout=INITIALIZE_TIMEOUT_SECONDS,
        )
        self.notify("initialized")
        return response

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
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
        try:
            self.server_info = self.client.initialize()
            if args.thread_id:
                self._resume_thread(args.thread_id)
            elif args.new:
                self._start_new_thread()
            else:
                self._load_recent_threads()
        except Exception:
            self.close()
            raise

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
        self.state.active_item_id = ""
        self.state.active_item_label = ""
        self.state.pending_approvals.clear()
        self.state.reader_open = False
        self.state.reader_scroll = 0
        self.state.feed_top_entry = 0
        self.state.session_focus = "composer"
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
                self.state.active_item_id = ""
                self.state.active_item_label = ""
                self._clear_idle_notice()
            return
        if method == "turn/completed":
            if params.get("threadId") == self.state.thread_id:
                turn = params.get("turn", {})
                if turn.get("id") == self.state.active_turn_id:
                    self.state.active_turn_id = ""
                    self.state.active_item_id = ""
                    self.state.active_item_label = ""
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
                item = params.get("item", {})
                entry = self._entry_from_item(item)
                if entry is not None:
                    self._upsert_feed_entry(entry)
                    self.state.active_item_id = entry.id
                    self.state.active_item_label = self._active_item_label(entry)
                    self._clear_idle_notice()
            return
        if method == "item/completed":
            if params.get("threadId") == self.state.thread_id:
                item = params.get("item", {})
                entry = self._entry_from_item(item)
                if entry is not None:
                    self._upsert_feed_entry(entry)
                    if entry.id == self.state.active_item_id:
                        self.state.active_item_id = ""
                        self.state.active_item_label = ""
            return
        if method == "item/agentMessage/delta":
            if params.get("threadId") == self.state.thread_id:
                item_id = params.get("itemId", "")
                delta = params.get("delta", "")
                self.state.active_item_id = item_id
                self.state.active_item_label = "writing response"
                self._clear_idle_notice()
                self._append_agent_delta(item_id, delta)
            return
        if method == "item/commandExecution/outputDelta":
            if params.get("threadId") == self.state.thread_id:
                self.state.active_item_id = params.get("itemId", "")
                self.state.active_item_label = "running command"
                self._clear_idle_notice()
                self._append_output_delta(params.get("itemId", ""), params.get("delta", ""))
            return
        if method == "item/fileChange/outputDelta":
            if params.get("threadId") == self.state.thread_id:
                self.state.active_item_id = params.get("itemId", "")
                self.state.active_item_label = "editing files"
                self._clear_idle_notice()
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
        self._draw_line(stdscr, 1, 0, "=" * max(0, width - 1), "dim")
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
        pick = self._feed_pick_status()
        header_width = max(1, width - len(pick) - 3)
        self._draw_line(stdscr, 0, 0, self._fit(header, header_width), "accent")
        if pick:
            self._draw_line(stdscr, 0, max(0, width - len(pick) - 1), pick, "pick_status")
        self._draw_line(stdscr, 1, 0, "=" * max(0, width - 1), "dim")
        top = 2
        if pending is not None:
            preview = pending.command or pending.reason or pending.grant_root or "approval requested"
            self._draw_line(stdscr, top, 0, self._fit(f"[?] waiting: {preview}", width - 1), "warn")
            top += 1
        else:
            status = self._session_status()
            if status:
                self._draw_line(stdscr, top, 0, self._fit(f"[*] {status}", width - 1), "dim")
                top += 1
        composer_lines, cursor_y, cursor_x = self._composer_view(width - 1, max_lines=6)
        composer_height = len(composer_lines)
        feed_height = max(1, height - top - composer_height)
        self._render_feed(stdscr, top, width - 1, feed_height)
        composer_color = "accent" if self.state.session_focus == "composer" and not self.state.reader_open else "dim"
        composer_top = height - composer_height
        for offset, line in enumerate(composer_lines):
            self._draw_line(stdscr, composer_top + offset, 0, line, composer_color, fill=True)
        self._set_cursor(stdscr, self.state.session_focus == "composer" and not self.state.reader_open, composer_top + cursor_y, cursor_x)
        if self.state.reader_open:
            self._render_reader(stdscr)

    def _poll_input(self, stdscr) -> bool:
        while True:
            try:
                key = stdscr.get_wch()
            except curses.error:
                return False
            if key == "\x1b":
                key = self._read_escape_sequence(stdscr)
            if self.state.view == "startup":
                return self._handle_startup_key(key)
            return self._handle_session_key(key)

    def _handle_startup_key(self, key: object) -> bool:
        total = len(self.state.startup_threads) + 1
        if key == curses.KEY_UP or self._is_nav_prev(key):
            self.state.startup_index = (self.state.startup_index - 1) % total
            return False
        if key == curses.KEY_DOWN or self._is_nav_next(key):
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
        if key in {"q", "Q"}:
            return True
        if isinstance(key, str) and key.isprintable():
            self.state.session_focus = "composer"
            self._insert_composer_text(key)
            return False
        if key in {"\n", "\r"} or key == curses.KEY_ENTER:
            self._open_reader()
            return False
        if key in {27, "\x1b"}:
            return True
        if key == curses.KEY_UP or self._is_nav_prev(key):
            self._move_selection(-1)
            return False
        if key == curses.KEY_DOWN or self._is_nav_next(key):
            self._move_selection(1)
            return False
        if key == curses.KEY_PPAGE or self._is_nav_page_prev(key):
            self._move_selection(-6)
            return False
        if key == curses.KEY_NPAGE or self._is_nav_page_next(key):
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
        return False

    def _handle_composer_key(self, key: object) -> bool:
        if isinstance(key, PasteText):
            self._insert_composer_text(key.text)
            return False
        if key in {"\n", "\r"} or key == curses.KEY_ENTER:
            self._submit_composer()
            return False
        if self._is_shift_enter(key):
            self._insert_composer_text("\n")
            return False
        if key == curses.KEY_UP or self._is_arrow_up(key):
            self._move_composer_cursor_vertical(-1)
            return False
        if key == curses.KEY_DOWN or self._is_arrow_down(key):
            self._move_composer_cursor_vertical(1)
            return False
        if self._is_nav_prev(key):
            self._move_selection(-1)
            return False
        if self._is_nav_next(key):
            self._move_selection(1)
            return False
        if key == curses.KEY_PPAGE or self._is_nav_page_prev(key):
            self._move_selection(-6)
            return False
        if key == curses.KEY_NPAGE or self._is_nav_page_next(key):
            self._move_selection(6)
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
            self._clamp_composer_viewport()
            return False
        if key == curses.KEY_RIGHT:
            self.state.composer_cursor = min(len(self.state.composer), self.state.composer_cursor + 1)
            self._clamp_composer_viewport()
            return False
        if key == curses.KEY_HOME:
            self._move_composer_cursor_to_line_edge(start=True)
            return False
        if key == curses.KEY_END:
            self._move_composer_cursor_to_line_edge(start=False)
            return False
        if key == curses.KEY_RESIZE:
            self._clamp_composer_viewport()
            return False
        if self._is_ctrl_char(key, 12):
            self._load_recent_threads()
            self.state.notice = "Recent threads refreshed."
            return False
        if isinstance(key, str) and key:
            self._insert_composer_text(key)
            return False
        return False

    def _read_escape_sequence(self, stdscr) -> object:
        chars = ["\x1b"]
        deadline = time.monotonic() + 0.05
        while time.monotonic() < deadline:
            try:
                next_key = stdscr.get_wch()
            except curses.error:
                time.sleep(0.001)
                continue
            if not isinstance(next_key, str):
                return "\x1b"
            chars.append(next_key)
            sequence = "".join(chars)
            if sequence == "\x1b[200~":
                return self._read_bracketed_paste(stdscr)
            if sequence in {"\x1b[13;2u", "\x1b[27;2;13~"}:
                return sequence
            if len(sequence) >= 8:
                return sequence
        return "\x1b" if len(chars) == 1 else "".join(chars)

    def _read_bracketed_paste(self, stdscr) -> PasteText:
        chars: list[str] = []
        suffix = "\x1b[201~"
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                key = stdscr.get_wch()
            except curses.error:
                time.sleep(0.001)
                continue
            if not isinstance(key, str):
                continue
            chars.append(key)
            if "".join(chars[-len(suffix) :]) == suffix:
                return PasteText("".join(chars[:-len(suffix)]))
        return PasteText("".join(chars))

    def _handle_reader_key(self, key: object) -> bool:
        if key in {"\n", "\r"} or key == curses.KEY_ENTER or key in {27, "\x1b"}:
            self.state.reader_open = False
            self.state.reader_scroll = 0
            return False
        lines = self._reader_lines(max(1, self.state.screen_width - 4))
        viewport = max(1, self.state.screen_height - 3)
        max_scroll = max(0, len(lines) - viewport)
        if key == curses.KEY_UP:
            self._move_reader_selection(-1)
            return False
        if key == curses.KEY_DOWN:
            self._move_reader_selection(1)
            return False
        if key == curses.KEY_LEFT or self._is_nav_prev(key):
            self.state.reader_scroll = max(0, self.state.reader_scroll - 1)
            return False
        if key == curses.KEY_RIGHT or self._is_nav_next(key):
            self.state.reader_scroll = min(max_scroll, self.state.reader_scroll + 1)
            return False
        if key == curses.KEY_PPAGE or self._is_nav_page_prev(key):
            self.state.reader_scroll = max(0, self.state.reader_scroll - viewport)
            return False
        if key == curses.KEY_NPAGE or self._is_nav_page_next(key):
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
            if not self.state.active_turn_id:
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
            summary = self._file_change_summary(changes)
            detail = self._file_change_detail(status, changes)
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
            return FeedEntry(item_id, "file", summary, detail=detail, icon=icon, color=color)
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
        separator = "\n\noutput:\n" if current.kind == "file" and current.detail and "output:" not in current.detail else ""
        current.detail = f"{current.detail}{separator}{delta}".strip()

    def _file_change_summary(self, changes: object) -> str:
        change_list = changes if isinstance(changes, list) else []
        count = len(change_list)
        if count == 1 and isinstance(change_list[0], dict):
            change = change_list[0]
            path = self._change_path(change)
            action = self._change_action(change)
            stats = self._change_stat_label(change)
            parts = [part for part in (action, path, stats) if part]
            if parts:
                return " ".join(parts)
        return f"{count} file change{'s' if count != 1 else ''}"

    def _file_change_detail(self, status: str, changes: object) -> str:
        change_list = changes if isinstance(changes, list) else []
        lines = [
            "file change",
            f"status: {status or 'pending'}",
            f"files: {len(change_list)}",
        ]
        for index, raw_change in enumerate(change_list, start=1):
            if not isinstance(raw_change, dict):
                lines.extend(["", f"{index}. {raw_change}"])
                continue
            path = self._change_path(raw_change)
            action = self._change_action(raw_change)
            stats = self._change_stat_label(raw_change)
            heading = f"{index}. {path or '(unknown path)'}"
            if action:
                heading = f"{heading}  [{action}]"
            if stats:
                heading = f"{heading}  {stats}"
            lines.extend(["", heading])
            old_path = self._first_text(raw_change, ("oldPath", "fromPath", "previousPath", "sourcePath"))
            new_path = self._first_text(raw_change, ("newPath", "toPath", "targetPath", "destinationPath"))
            if old_path and new_path and old_path != new_path:
                lines.append(f"rename: {old_path} -> {new_path}")
            hunks = self._change_hunk_count(raw_change)
            if hunks is not None:
                lines.append(f"hunks: {hunks}")
        return "\n".join(lines)

    def _change_path(self, change: dict[str, Any]) -> str:
        return self._first_text(
            change,
            ("path", "filePath", "relativePath", "targetPath", "newPath", "toPath", "sourcePath", "oldPath", "fromPath"),
        )

    def _change_action(self, change: dict[str, Any]) -> str:
        value = self._first_text(change, ("action", "operation", "changeType", "kind", "type", "status"))
        return value.lower() if value else ""

    def _change_stat_label(self, change: dict[str, Any]) -> str:
        additions = self._first_int(change, ("additions", "added", "addedLines", "linesAdded", "insertions"))
        deletions = self._first_int(change, ("deletions", "deleted", "removedLines", "linesRemoved", "removals"))
        if additions is None or deletions is None:
            patch = self._first_text(change, ("patch", "diff", "unifiedDiff"))
            if patch:
                inferred_additions, inferred_deletions = self._diff_stats(patch)
                additions = inferred_additions if additions is None else additions
                deletions = inferred_deletions if deletions is None else deletions
        if additions is None and deletions is None:
            return ""
        return f"+{additions or 0} -{deletions or 0}"

    def _change_hunk_count(self, change: dict[str, Any]) -> int | None:
        hunks = change.get("hunks")
        if isinstance(hunks, list):
            return len(hunks)
        patch = self._first_text(change, ("patch", "diff", "unifiedDiff"))
        if patch:
            return sum(1 for line in patch.splitlines() if line.startswith("@@"))
        return None

    @staticmethod
    def _first_text(payload: dict[str, Any], keys: tuple[str, ...]) -> str:
        for key in keys:
            value = payload.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    @staticmethod
    def _first_int(payload: dict[str, Any], keys: tuple[str, ...]) -> int | None:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.strip().lstrip("-").isdigit():
                return int(value)
        return None

    @staticmethod
    def _diff_stats(patch: str) -> tuple[int, int]:
        additions = 0
        deletions = 0
        for line in patch.splitlines():
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("+"):
                additions += 1
            elif line.startswith("-"):
                deletions += 1
        return additions, deletions

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
            selected = index == self.state.selected_entry
            if selected:
                color = "selected_warn" if color == "warn" else "selected"
            for offset, line in enumerate(lines):
                if row >= top + height:
                    break
                if selected:
                    marker = ">" if offset == 0 else "|"
                    self._draw_line(stdscr, row, 0, " " * max(0, width - 1), "selected_bg")
                    self._draw_line(stdscr, row, 0, marker, "pick_status")
                    self._draw_line(stdscr, row, 2, self._fit(line, max(1, width - 2)), color)
                else:
                    line = f"  {line}"
                    self._draw_line(stdscr, row, 0, self._fit(line, width), color)
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

    def _feed_pick_status(self) -> str:
        if not self.state.feed:
            return ""
        return f"pick {self.state.selected_entry + 1}/{len(self.state.feed)}"

    def _session_status(self) -> str:
        if self.state.active_item_label:
            return self.state.active_item_label
        if self.state.active_turn_id:
            return "agent is working"
        if self.state.notice:
            return self.state.notice
        return ""

    def _clear_idle_notice(self) -> None:
        if self.state.notice in {"Composer is empty.", "Turn started.", "Steer sent."}:
            self.state.notice = ""

    @staticmethod
    def _active_item_label(entry: FeedEntry) -> str:
        if entry.kind == "agent":
            return "writing response"
        if entry.kind == "command":
            return "running command"
        if entry.kind == "file":
            return "editing files"
        if entry.kind == "tool":
            return f"using {entry.summary}"
        if entry.kind == "plan":
            return "updating plan"
        if entry.kind == "reasoning":
            return "thinking"
        return f"working on {entry.kind}"

    def _move_reader_selection(self, delta: int) -> None:
        previous = self.state.selected_entry
        self._move_selection(delta)
        if self.state.selected_entry != previous:
            self.state.reader_scroll = 0

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
            lines.extend(self._wrap_block(entry.detail, width))
        return lines

    def _render_reader(self, stdscr) -> None:
        height, width = stdscr.getmaxyx()
        box_top = 1
        box_left = 0
        box_height = max(4, height - 1)
        box_width = max(12, width - 1)
        content_width = max(1, box_width - 4)
        content_height = max(1, box_height - 2)
        lines = self._reader_lines(content_width)
        max_scroll = max(0, len(lines) - content_height)
        self.state.reader_scroll = min(max(0, self.state.reader_scroll), max_scroll)
        self._fill_rect(stdscr, box_top, box_left, box_height, box_width, "overlay")
        self._draw_box(stdscr, box_top, box_left, box_height, box_width, "overlay_border")
        title = f" message {self.state.selected_entry + 1}/{max(1, len(self.state.feed))} "
        self._draw_line(stdscr, box_top, box_left + 2, self._fit(title, box_width - 4), "overlay_title")
        visible = lines[self.state.reader_scroll : self.state.reader_scroll + content_height]
        for row, line in enumerate(visible, start=box_top + 1):
            if row >= box_top + box_height - 1:
                break
            self._draw_line(stdscr, row, box_left + 2, line, "overlay")
        self._set_cursor(stdscr, False, 0, 0)

    def _composer_view(self, width: int, max_lines: int) -> tuple[list[str], int, int]:
        layout = self._composer_layout(width)
        cursor_line = self._composer_cursor_line(layout)
        self._clamp_composer_viewport(cursor_line=cursor_line, max_lines=max_lines)
        top = self.state.composer_scroll
        visible_layout = layout[top : top + max_lines]
        rows: list[str] = []
        for index, line in enumerate(visible_layout, start=top):
            prefix = "> " if index == 0 else "  "
            rows.append(self._fit(f"{prefix}{self.state.composer[line.start:line.end]}", width))
        if not rows:
            rows = ["> "]
        cursor_line = self._composer_cursor_line(layout)
        visible_cursor_line = max(0, min(len(rows) - 1, cursor_line - self.state.composer_scroll))
        line = layout[cursor_line] if layout else ComposerLine(0, 0)
        cursor_x = 2 + self.state.composer_cursor - line.start
        return rows, visible_cursor_line, max(0, min(width - 1, cursor_x))

    def _composer_layout(self, width: int) -> list[ComposerLine]:
        viewport = max(1, width - 2)
        text = self.state.composer
        if not text:
            return [ComposerLine(0, 0)]
        lines: list[ComposerLine] = []
        line_start = 0
        index = 0
        column = 0
        while index < len(text):
            if text[index] == "\n":
                lines.append(ComposerLine(line_start, index))
                index += 1
                line_start = index
                column = 0
                continue
            if column >= viewport:
                lines.append(ComposerLine(line_start, index))
                line_start = index
                column = 0
                continue
            index += 1
            column += 1
        lines.append(ComposerLine(line_start, len(text)))
        return lines

    def _composer_cursor_line(self, layout: list[ComposerLine]) -> int:
        cursor = self.state.composer_cursor
        for index, line in enumerate(layout):
            if line.start <= cursor <= line.end:
                return index
        return max(0, len(layout) - 1)

    def _clamp_composer_viewport(self, cursor_line: int | None = None, max_lines: int = 6) -> None:
        self.state.composer_cursor = min(max(0, self.state.composer_cursor), len(self.state.composer))
        layout = self._composer_layout(max(1, self.state.screen_width - 1))
        if cursor_line is None:
            cursor_line = self._composer_cursor_line(layout)
        max_lines = max(1, max_lines)
        if cursor_line < self.state.composer_scroll:
            self.state.composer_scroll = cursor_line
        elif cursor_line >= self.state.composer_scroll + max_lines:
            self.state.composer_scroll = cursor_line - max_lines + 1
        max_scroll = max(0, len(layout) - max_lines)
        self.state.composer_scroll = min(max(0, self.state.composer_scroll), max_scroll)

    def _insert_composer_text(self, text: str) -> None:
        cursor = self.state.composer_cursor
        self.state.composer = f"{self.state.composer[:cursor]}{text}{self.state.composer[cursor:]}"
        self.state.composer_cursor = cursor + len(text)
        self._clamp_composer_viewport()
        self.state.session_focus = "composer"

    def _delete_composer_before_cursor(self) -> None:
        cursor = self.state.composer_cursor
        if cursor <= 0:
            return
        self.state.composer = f"{self.state.composer[:cursor - 1]}{self.state.composer[cursor:]}"
        self.state.composer_cursor = cursor - 1
        self._clamp_composer_viewport()

    def _delete_composer_at_cursor(self) -> None:
        cursor = self.state.composer_cursor
        if cursor >= len(self.state.composer):
            return
        self.state.composer = f"{self.state.composer[:cursor]}{self.state.composer[cursor + 1:]}"
        self._clamp_composer_viewport()

    def _move_composer_cursor_vertical(self, delta: int) -> None:
        layout = self._composer_layout(max(1, self.state.screen_width - 1))
        current_line = self._composer_cursor_line(layout)
        target_line = min(max(0, current_line + delta), len(layout) - 1)
        current = layout[current_line]
        target = layout[target_line]
        column = self.state.composer_cursor - current.start
        self.state.composer_cursor = min(target.end, target.start + max(0, column))
        self._clamp_composer_viewport(cursor_line=target_line)

    def _move_composer_cursor_to_line_edge(self, start: bool) -> None:
        layout = self._composer_layout(max(1, self.state.screen_width - 1))
        line_index = self._composer_cursor_line(layout)
        line = layout[line_index]
        self.state.composer_cursor = line.start if start else line.end
        self._clamp_composer_viewport(cursor_line=line_index)

    @staticmethod
    def _is_shift_enter(key: object) -> bool:
        return key in {"\x1b[13;2u", "\x1b[27;2;13~"}

    @staticmethod
    def _is_arrow_up(key: object) -> bool:
        return isinstance(key, str) and key in {"\x1b[A", "\x1bOA"}

    @staticmethod
    def _is_arrow_down(key: object) -> bool:
        return isinstance(key, str) and key in {"\x1b[B", "\x1bOB"}

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

    def _wrap_block(self, text: str, width: int) -> list[str]:
        lines: list[str] = []
        for raw_line in text.splitlines():
            if not raw_line:
                lines.append("")
                continue
            lines.extend(self._wrap(raw_line, width))
        return lines or [""]

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

    @classmethod
    def _is_nav_prev(cls, key: object) -> bool:
        return cls._is_ctrl_char(key, 16)

    @classmethod
    def _is_nav_next(cls, key: object) -> bool:
        return cls._is_ctrl_char(key, 14)

    @classmethod
    def _is_nav_page_prev(cls, key: object) -> bool:
        return cls._is_ctrl_char(key, 21)

    @classmethod
    def _is_nav_page_next(cls, key: object) -> bool:
        return cls._is_ctrl_char(key, 4)

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
            rendered = text.ljust(max(0, width - x - 1))
        try:
            stdscr.addnstr(y, x, rendered, max(0, width - x - 1), self._color_attr(color))
        except curses.error:
            return

    def _fill_rect(self, stdscr, top: int, left: int, height: int, width: int, color: str) -> None:
        for row in range(top, top + height):
            self._draw_line(stdscr, row, left, " " * max(0, width - 1), color)

    def _draw_box(self, stdscr, top: int, left: int, height: int, width: int, color: str) -> None:
        if height < 2 or width < 2:
            return
        horizontal = "-" * max(0, width - 2)
        self._draw_line(stdscr, top, left, f"+{horizontal}+", color)
        for row in range(top + 1, top + height - 1):
            self._draw_line(stdscr, row, left, "|", color)
            self._draw_line(stdscr, row, left + width - 1, "|", color)
        self._draw_line(stdscr, top + height - 1, left, f"+{horizontal}+", color)

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
        curses.init_pair(9, curses.COLOR_CYAN, curses.COLOR_WHITE)
        curses.init_pair(10, curses.COLOR_BLACK, curses.COLOR_GREEN)
        curses.init_pair(11, curses.COLOR_BLACK, curses.COLOR_MAGENTA)
        curses.init_pair(12, curses.COLOR_YELLOW, -1)

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
            "selected": curses.color_pair(10) | curses.A_BOLD,
            "selected_warn": curses.color_pair(7) | curses.A_BOLD,
            "selected_bg": curses.color_pair(10),
            "pick_status": curses.color_pair(12) | curses.A_BOLD,
            "overlay": curses.color_pair(8),
            "overlay_title": curses.color_pair(8) | curses.A_BOLD,
            "overlay_border": curses.color_pair(9) | curses.A_BOLD,
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
    startup = parser.add_mutually_exclusive_group()
    startup.add_argument("-n", "--new", action="store_true", help="Start a new thread immediately")
    startup.add_argument("--thread-id", default=None, help="Resume a specific thread id immediately")
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
