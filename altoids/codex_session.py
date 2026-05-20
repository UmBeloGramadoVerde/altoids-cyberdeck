from __future__ import annotations

import json
import os
import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any


EVENT_BADGES = {
    "agent-turn-complete": "OK",
    "assistant": "..",
    "assistant_message": "..",
    "agent_message": "..",
    "commentary": "..",
    "event_msg": "EV",
    "reasoning": "..",
    "response_item": "EV",
    "session_meta": "ID",
    "tool": "TL",
    "tool_call": "TL",
    "tool_result": "TL",
    "user": "IN",
}

STRING_KEYS = (
    "last-assistant-message",
    "last_assistant_message",
    "summary",
    "text",
    "message",
    "content",
    "description",
    "title",
)

APPROVAL_RE = re.compile(r"Do you want (?:me )?to (.+?)\??$", re.IGNORECASE)


@dataclass(slots=True)
class CodexEvent:
    event_type: str
    summary: str
    raw: dict[str, Any]


@dataclass(slots=True)
class CodexSessionView:
    session_id: str
    cwd: str
    source_path: Path
    updated_at: float
    lines: list[str]
    events: list[CodexEvent]
    last_assistant_message: str


@dataclass(slots=True)
class CodexTimelineEvent:
    index: int
    event_type: str
    kind: str
    badge: str
    summary: str
    detail: str
    raw: dict[str, Any]


@dataclass(slots=True)
class CodexSessionSnapshot:
    session_id: str
    cwd: str
    source_path: Path | None
    updated_at: float
    events: list[CodexTimelineEvent]
    last_assistant_message: str
    current_phase: str
    last_tool: str
    pending_approval: str


@dataclass(slots=True)
class _MetaCache:
    mtime_ns: int
    size: int
    cwd: str
    session_id: str


@dataclass(slots=True)
class _ViewCache:
    mtime_ns: int
    size: int
    view: CodexSessionView


class CodexSessionStore:
    def __init__(self, root: Path, scan_limit: int = 24, max_events: int = 120) -> None:
        self.root = root.expanduser()
        self.scan_limit = max(4, scan_limit)
        self.max_events = max(16, max_events)
        self._meta_cache: dict[Path, _MetaCache] = {}
        self._view_cache: dict[Path, _ViewCache] = {}
        self._candidate_cache: list[Path] = []
        self._candidate_cache_at = 0.0
        self._last_target = ""
        self._last_match: Path | None = None

    def view_for_cwd(self, cwd: str) -> CodexSessionView | None:
        target = _normalize_path(cwd)
        if target is None:
            return None
        target_text = str(target)
        candidate = self._last_match if target_text == self._last_target else None
        if candidate is None or not candidate.exists() or not self._matches_target(candidate, target):
            candidate = self._find_rollout(target)
            self._last_target = target_text
            self._last_match = candidate
        if candidate is None:
            return None
        return self._parse_rollout(candidate)

    def recent_view_for_cwd(self, cwd: str, freshness_seconds: float = 15.0) -> CodexSessionView | None:
        view = self.view_for_cwd(cwd)
        if view is None:
            return None
        if freshness_seconds <= 0:
            return view
        if os.times().elapsed - view.updated_at <= freshness_seconds:
            return view
        return None

    def rollout_for_cwd(self, cwd: str, started_after: float = 0.0) -> Path | None:
        target = _normalize_path(cwd)
        if target is None:
            return None
        for candidate in self._candidate_files():
            try:
                if started_after > 0 and candidate.stat().st_mtime < started_after:
                    continue
            except OSError:
                continue
            if self._matches_target(candidate, target):
                return candidate
        return None

    def rollout_candidates_for_cwd(self, cwd: str, started_after: float = 0.0) -> list[Path]:
        target = _normalize_path(cwd)
        if target is None:
            return []
        matches: list[Path] = []
        for candidate in self._candidate_files():
            try:
                if started_after > 0 and candidate.stat().st_mtime < started_after:
                    continue
            except OSError:
                continue
            if self._matches_target(candidate, target):
                matches.append(candidate)
        return matches

    def _find_rollout(self, target: Path) -> Path | None:
        for candidate in self._candidate_files():
            if self._matches_target(candidate, target):
                return candidate
        return None

    def _candidate_files(self) -> list[Path]:
        sessions_root = self.root / "sessions"
        if not sessions_root.exists():
            return []
        now = os.times().elapsed
        if self._candidate_cache and now - self._candidate_cache_at < 1.0:
            return self._candidate_cache

        candidates = sorted(
            (path for path in sessions_root.rglob("rollout-*.jsonl") if path.is_file()),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        self._candidate_cache = candidates[: self.scan_limit]
        self._candidate_cache_at = now
        return self._candidate_cache

    def _matches_target(self, path: Path, target: Path) -> bool:
        meta = self._session_meta(path)
        if meta is None or not meta.cwd:
            return False
        session_cwd = _normalize_path(meta.cwd)
        if session_cwd is None:
            return False
        target_text = str(target)
        session_text = str(session_cwd)
        return target_text == session_text or target_text.startswith(f"{session_text}{os.sep}") or session_text.startswith(f"{target_text}{os.sep}")

    def _session_meta(self, path: Path) -> _MetaCache | None:
        stat = path.stat()
        cached = self._meta_cache.get(path)
        if cached is not None and cached.mtime_ns == stat.st_mtime_ns and cached.size == stat.st_size:
            return cached

        cwd = ""
        session_id = ""
        try:
            with path.open("r", encoding="utf-8") as handle:
                for _ in range(12):
                    line = handle.readline()
                    if not line:
                        break
                    payload = _decode_line(line)
                    if payload is None:
                        continue
                    event_type = _event_type(payload)
                    data = _payload(payload)
                    if not cwd:
                        cwd = _string_value(data, "cwd") or _string_value(payload, "cwd")
                    if not session_id:
                        session_id = _string_value(data, "id") or _string_value(payload, "id")
                    if event_type == "session_meta" and cwd:
                        break
        except OSError:
            return None

        meta = _MetaCache(
            mtime_ns=stat.st_mtime_ns,
            size=stat.st_size,
            cwd=cwd,
            session_id=session_id,
        )
        self._meta_cache[path] = meta
        return meta

    def _parse_rollout(self, path: Path) -> CodexSessionView | None:
        stat = path.stat()
        cached = self._view_cache.get(path)
        if cached is not None and cached.mtime_ns == stat.st_mtime_ns and cached.size == stat.st_size:
            return cached.view

        meta = self._session_meta(path)
        session_id = meta.session_id if meta is not None else ""
        cwd = meta.cwd if meta is not None else ""
        events: list[CodexEvent] = []
        lines: list[str] = []
        last_assistant_message = ""

        try:
            with path.open("r", encoding="utf-8") as handle:
                for raw_line in handle:
                    payload = _decode_line(raw_line)
                    if payload is None:
                        continue
                    event_type = _event_type(payload)
                    data = _payload(payload)
                    if event_type == "session_meta":
                        session_id = session_id or _string_value(data, "id")
                        cwd = cwd or _string_value(data, "cwd")
                        continue
                    summary = _summarize_event(payload)
                    if not summary:
                        continue
                    if _is_assistant_message_event(payload, event_type) or event_type == "agent-turn-complete":
                        last_assistant_message = _extract_message(payload) or last_assistant_message
                    event = CodexEvent(event_type=event_type or "event", summary=summary, raw=payload)
                    if lines and lines[-1] == summary:
                        continue
                    events.append(event)
                    lines.append(summary)
        except OSError:
            return None

        if not lines:
            placeholder = self._starting_summary(session_id)
            lines = [placeholder]
            events = [CodexEvent(event_type="session_meta", summary=placeholder, raw={})]
        if len(events) > self.max_events:
            events = events[-self.max_events :]
            lines = lines[-self.max_events :]

        view = CodexSessionView(
            session_id=session_id or "-",
            cwd=cwd or "-",
            source_path=path,
            updated_at=stat.st_mtime,
            lines=lines,
            events=events,
            last_assistant_message=last_assistant_message,
        )
        self._view_cache[path] = _ViewCache(
            mtime_ns=stat.st_mtime_ns,
            size=stat.st_size,
            view=view,
        )
        return view

    @staticmethod
    def _starting_summary(session_id: str) -> str:
        if session_id and session_id != "-":
            return f"[ID] cx {session_id[-6:]} starting"
        return "[ID] codex starting"


class CodexSessionTail:
    def __init__(self, root: Path, scan_limit: int = 24, max_events: int = 120) -> None:
        self.store = CodexSessionStore(root, scan_limit=scan_limit, max_events=max_events)
        self.max_events = max(16, max_events)
        self._path: Path | None = None
        self._offset = 0
        self._event_index = 0
        self._events: deque[CodexTimelineEvent] = deque(maxlen=self.max_events)
        self._session_id = "-"
        self._cwd = "-"
        self._updated_at = 0.0
        self._last_assistant_message = ""
        self._current_phase = "starting"
        self._last_tool = ""
        self._pending_approval = ""

    def poll(self, cwd: str, started_after: float = 0.0) -> CodexSessionSnapshot:
        candidate = self.store.rollout_for_cwd(cwd, started_after=started_after)
        if candidate is None:
            return self.snapshot()
        return self.poll_bound(candidate)

    def bind(self, path: Path) -> CodexSessionSnapshot:
        self._bind(path)
        return self.snapshot()

    def poll_bound(self, path: Path) -> CodexSessionSnapshot:
        if self._path != path:
            self._bind(path)
            return self.snapshot()
        try:
            stat = path.stat()
        except OSError:
            return self.snapshot()
        if stat.st_size < self._offset:
            self._bind(path)
            return self.snapshot()
        if stat.st_size == self._offset:
            self._updated_at = stat.st_mtime
            return self.snapshot()
        self._read_from_offset(path, self._offset)
        self._updated_at = stat.st_mtime
        return self.snapshot()

    def snapshot(self) -> CodexSessionSnapshot:
        return CodexSessionSnapshot(
            session_id=self._session_id,
            cwd=self._cwd,
            source_path=self._path,
            updated_at=self._updated_at,
            events=list(self._events),
            last_assistant_message=self._last_assistant_message,
            current_phase=self._current_phase,
            last_tool=self._last_tool,
            pending_approval=self._pending_approval,
        )

    def clear_pending_approval(self) -> None:
        self._pending_approval = ""

    def set_pending_approval(self, detail: str) -> None:
        self._pending_approval = detail.strip()

    def _bind(self, path: Path) -> None:
        self._path = path
        self._offset = 0
        self._event_index = 0
        self._events.clear()
        self._session_id = "-"
        self._cwd = "-"
        self._updated_at = 0.0
        self._last_assistant_message = ""
        self._current_phase = "starting"
        self._last_tool = ""
        self._pending_approval = ""
        self._read_from_offset(path, 0)
        try:
            self._updated_at = path.stat().st_mtime
        except OSError:
            self._updated_at = 0.0

    def _read_from_offset(self, path: Path, offset: int) -> None:
        try:
            with path.open("r", encoding="utf-8") as handle:
                handle.seek(offset)
                for raw_line in handle:
                    payload = _decode_line(raw_line)
                    if payload is None:
                        continue
                    self._consume(payload)
                self._offset = handle.tell()
        except OSError:
            return

    def _consume(self, event: dict[str, Any]) -> None:
        event_type = _event_type(event)
        payload = _payload(event)
        if event_type == "session_meta":
            self._session_id = _string_value(payload, "id") or self._session_id
            self._cwd = _string_value(payload, "cwd") or self._cwd
            return
        normalized = _normalize_timeline_event(event, self._event_index)
        self._event_index += 1
        if normalized is None:
            return
        if normalized.kind in {"assistant_message", "commentary"} and normalized.detail:
            self._last_assistant_message = normalized.detail
        if normalized.kind in {"tool_call", "tool_result", "exec_result"}:
            self._last_tool = normalized.summary
        if normalized.kind == "approval_requested":
            self._pending_approval = normalized.detail or normalized.summary
        elif normalized.kind == "user_message":
            self._pending_approval = ""
        if normalized.kind not in {"user_message", "system"}:
            self._current_phase = normalized.kind
        self._events.append(normalized)


def _decode_line(line: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        return payload
    return None


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    if isinstance(payload, dict):
        return payload
    return event


def _event_type(event: dict[str, Any]) -> str:
    event_type = event.get("type")
    payload = event.get("payload")
    if isinstance(event_type, str):
        if event_type in {"event_msg", "response_item"} and isinstance(payload, dict):
            nested = payload.get("type")
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
        return event_type
    if isinstance(payload, dict):
        nested = payload.get("type")
        if isinstance(nested, str):
            return nested
    return ""


def _string_value(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) else ""


def _normalize_path(raw_path: str) -> Path | None:
    if not raw_path or raw_path == "-":
        return None
    return Path(raw_path).expanduser().resolve(strict=False)


def _summarize_event(event: dict[str, Any]) -> str:
    event_type = _event_type(event)
    if event_type in {"token_count", "function_call_output", "custom_tool_call_output", "patch_apply_end", "task_started", "turn_context"}:
        return ""
    if event_type == "agent-turn-complete":
        message = _extract_message(event)
        return f"[OK] {_shorten(message)}" if message else "[OK] turn complete"

    if event_type == "exec_command_end":
        status = _string_value(_payload(event), "status").lower()
        exit_code = _payload(event).get("exit_code")
        if status == "completed" and exit_code == 0:
            return "[OK] exec"
        if exit_code not in {None, 0}:
            return f"[!!] exec {exit_code}"
        return "[EX] exec"

    tool_name = _extract_tool_name(event)
    if tool_name:
        return f"[{_tool_badge(tool_name)}] {tool_name.split('.')[-1]}"

    message = _extract_message(event)
    if message:
        if _skip_message(message):
            return ""
        badge = ".." if _is_assistant_message_event(event, event_type) else EVENT_BADGES.get(event_type, "EV")
        return f"[{badge}] {_shorten(message)}"

    if not event_type or event_type == "session_meta":
        return ""
    badge = EVENT_BADGES.get(event_type, "EV")
    return f"[{badge}] {event_type.replace('-', ' ')}"


def _extract_message(event: dict[str, Any]) -> str:
    payload = _payload(event)
    for key in STRING_KEYS:
        message = _string_value(payload, key) or _string_value(event, key)
        if message:
            return message
    content = payload.get("content")
    if isinstance(content, list):
        extracted = _content_text(content)
        if extracted:
            return extracted
    inputs = payload.get("input-messages") or payload.get("input_messages") or event.get("input-messages") or event.get("input_messages")
    if isinstance(inputs, list):
        for item in reversed(inputs):
            if isinstance(item, str) and item.strip():
                return item.strip()
    nested = payload.get("message")
    if isinstance(nested, dict):
        for key in ("content", "text"):
            message = _string_value(nested, key)
            if message:
                return message
    return ""


def _extract_tool_name(event: dict[str, Any]) -> str:
    payload = _payload(event)
    candidates: list[Any] = [
        payload.get("tool_name"),
        payload.get("tool"),
        payload.get("name"),
        payload.get("recipient_name"),
        event.get("tool_name"),
        event.get("tool"),
        event.get("name"),
        event.get("recipient_name"),
    ]
    call = payload.get("tool_call")
    if isinstance(call, dict):
        candidates.extend([call.get("tool_name"), call.get("tool"), call.get("recipient_name"), call.get("name")])
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def _tool_badge(tool_name: str) -> str:
    label = tool_name.split(".")[-1]
    if "github" in tool_name.lower() or label.startswith("_fetch_pr") or label.startswith("_search_pr"):
        return "GH"
    if "search" in label or label in {"open", "click", "find"}:
        return "WB"
    if label == "exec_command":
        return "EX"
    if label == "write_stdin":
        return "IO"
    if label == "apply_patch":
        return "PT"
    return "TL"


def _content_text(content: list[Any]) -> str:
    parts: list[str] = []
    for item in content:
        if isinstance(item, str) and item.strip():
            parts.append(item.strip())
            continue
        if not isinstance(item, dict):
            continue
        text = _string_value(item, "text")
        if text:
            parts.append(text)
            continue
        nested = item.get("content")
        if isinstance(nested, list):
            nested_text = _content_text(nested)
            if nested_text:
                parts.append(nested_text)
    return " ".join(part for part in parts if part).strip()


def _is_assistant_message_event(event: dict[str, Any], event_type: str) -> bool:
    if event_type in {"assistant", "assistant_message", "agent_message", "commentary"}:
        return True
    payload = _payload(event)
    role = _string_value(payload, "role") or _string_value(event, "role")
    if role == "assistant":
        return True
    phase = _string_value(payload, "phase") or _string_value(event, "phase")
    return phase == "commentary"


def _shorten(text: str, limit: int = 44) -> str:
    cleaned = " ".join(text.replace("\n", " ").split())
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[: max(0, limit - 1)]}>"


def _skip_message(message: str) -> bool:
    stripped = message.strip()
    if not stripped:
        return True
    prefixes = (
        "<environment_context>",
        "<permissions instructions>",
        "<collaboration_mode>",
        "<apps_instructions>",
        "<skills_instructions>",
        "<plugins_instructions>",
    )
    return stripped.startswith(prefixes)


def _normalize_timeline_event(event: dict[str, Any], index: int) -> CodexTimelineEvent | None:
    event_type = _event_type(event)
    if event_type in {"token_count", "function_call_output", "custom_tool_call_output", "patch_apply_end", "task_started", "turn_context"}:
        return None

    if event_type == "agent-turn-complete":
        detail = _extract_message(event)
        summary = "Turn complete"
        if detail:
            summary = _shorten(detail, limit=28)
        return CodexTimelineEvent(
            index=index,
            event_type=event_type,
            kind="turn_complete",
            badge="OK",
            summary=summary,
            detail=detail,
            raw=event,
        )

    if event_type == "exec_command_end":
        payload = _payload(event)
        exit_code = payload.get("exit_code")
        command = _command_preview(payload)
        if exit_code == 0:
            badge = "OK"
            summary = "exec ok"
        elif exit_code in {None, ""}:
            badge = "EX"
            summary = "exec"
        else:
            badge = "!!"
            summary = f"exec {exit_code}"
        return CodexTimelineEvent(
            index=index,
            event_type=event_type,
            kind="exec_result",
            badge=badge,
            summary=summary,
            detail=command,
            raw=event,
        )

    tool_name = _extract_tool_name(event)
    if tool_name:
        label = tool_name.split(".")[-1]
        return CodexTimelineEvent(
            index=index,
            event_type=event_type or "tool",
            kind="tool_call" if event_type != "tool_result" else "tool_result",
            badge=_tool_badge(tool_name),
            summary=label,
            detail=label,
            raw=event,
        )

    message = _extract_message(event)
    if message:
        if _skip_message(message):
            return None
        approval = _approval_detail(message)
        if approval:
            return CodexTimelineEvent(
                index=index,
                event_type=event_type or "approval",
                kind="approval_requested",
                badge="ASK",
                summary="approval",
                detail=approval,
                raw=event,
            )
        if _is_assistant_message_event(event, event_type):
            kind = "commentary" if event_type == "commentary" else "assistant_message"
            badge = ".."
        elif event_type in {"user", "user_message"}:
            kind = "user_message"
            badge = "IN"
        else:
            kind = "system"
            badge = EVENT_BADGES.get(event_type, "EV")
        return CodexTimelineEvent(
            index=index,
            event_type=event_type or "message",
            kind=kind,
            badge=badge,
            summary=_shorten(message, limit=28),
            detail=message,
            raw=event,
        )

    if not event_type or event_type == "session_meta":
        return None
    return CodexTimelineEvent(
        index=index,
        event_type=event_type,
        kind="system",
        badge=EVENT_BADGES.get(event_type, "EV"),
        summary=event_type.replace("-", " "),
        detail="",
        raw=event,
    )


def _approval_detail(message: str) -> str:
    cleaned = " ".join(message.replace("\n", " ").split()).strip()
    match = APPROVAL_RE.search(cleaned)
    if not match:
        return ""
    return match.group(1).strip().rstrip(".?")


def _command_preview(payload: dict[str, Any], limit: int = 72) -> str:
    command = payload.get("command")
    if isinstance(command, list):
        rendered = " ".join(str(part) for part in command if str(part).strip())
    elif isinstance(command, str):
        rendered = command.strip()
    else:
        rendered = ""
    if not rendered:
        rendered = _string_value(payload, "cmd")
    return _shorten(rendered, limit=limit) if rendered else ""
