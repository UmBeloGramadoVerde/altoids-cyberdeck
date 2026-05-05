from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from PIL import ImageDraw, ImageFont

from .colors import ANSI_BASIC, DIM, FG

ANSI_RE = re.compile(r"\x1b\[([0-9;?]*)m")
CSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

PROMPT_RE = re.compile(
    r"^(?:\[[^\]]+\]\s+)?(?:(?P<user>[^@\s]+)@(?P<host>[^:\s]+):)?(?P<path>\S+?)(?P<sigil>[$#])(?:\s+(?P<rest>.*))?$"
)
TOOL_CALL_RE = re.compile(r"ToolCall:\s+([A-Za-z0-9_:.]+)(?:\s+(\{.*))?$")
COMMENTARY_PREFIX_RE = re.compile(r"^(I('| a)?m|I am|I’ll|I will|Next I’m|Now I’m)\b", re.IGNORECASE)
APPROVAL_RE = re.compile(r"Do you want me to (.+?)\??$", re.IGNORECASE)

TOOL_BADGES = {
    "exec_command": "EX",
    "write_stdin": "IO",
    "apply_patch": "PT",
    "search_query": "WB",
    "open": "WB",
    "click": "WB",
    "find": "WB",
    "weather": "WX",
    "finance": "FX",
    "sports": "SP",
    "_fetch_pr": "GH",
    "_fetch_pr_patch": "GH",
    "_fetch_pr_comments": "GH",
    "_fetch_issue": "GH",
    "_search_prs": "GH",
    "_search_issues": "GH",
    "_add_comment_to_issue": "GH",
    "_add_review_to_pr": "GH",
    "_request_pull_request_reviewers": "GH",
    "_reply_to_review_comment": "GH",
    "request_user_input": "UI",
}


def _ansi_color(code: int) -> str:
    if 30 <= code <= 37:
        return ANSI_BASIC.get(code - 30, FG)
    if 90 <= code <= 97:
        base = ANSI_BASIC.get(code - 90, FG)
        return _brighten(base)
    return FG


def _brighten(hex_color: str) -> str:
    red = min(255, int(hex_color[1:3], 16) + 0x44)
    green = min(255, int(hex_color[3:5], 16) + 0x44)
    blue = min(255, int(hex_color[5:7], 16) + 0x44)
    return f"#{red:02X}{green:02X}{blue:02X}"


def strip_ansi(text: str) -> str:
    return CSI_RE.sub("", text)


def _leading_whitespace(text: str) -> tuple[str, str]:
    body = text.lstrip(" ")
    return text[: len(text) - len(body)], body


def _line_segments(text: str) -> list[tuple[str, str]]:
    color = FG
    cursor = 0
    segments: list[tuple[str, str]] = []
    for match in ANSI_RE.finditer(text):
        if match.start() > cursor:
            segments.append((text[cursor:match.start()], color))
        params = [part for part in match.group(1).split(";") if part]
        if not params:
            color = FG
        for param in params:
            if param == "0":
                color = FG
            elif param == "2":
                color = DIM
            elif param == "39":
                color = FG
            elif param.isdigit():
                color = _ansi_color(int(param))
        cursor = match.end()
    if cursor < len(text):
        segments.append((text[cursor:], color))
    return segments


def _compact_prompt_line(text: str) -> str:
    cleaned = strip_ansi(text)
    indent, body = _leading_whitespace(cleaned)
    match = PROMPT_RE.match(body.rstrip())
    if not match:
        return text
    path_label = _short_path(match.group("path"))
    rest = match.group("rest") or ""
    compacted = f"{path_label}{match.group('sigil')}"
    if rest:
        compacted = f"{compacted} {rest}"
    return f"{indent}{compacted}"


def _tool_badge(tool_name: str) -> str:
    short_name = tool_name.split(".")[-1]
    return TOOL_BADGES.get(short_name, short_name[:2].upper())


def _compact_tool_call(text: str) -> str | None:
    cleaned = strip_ansi(text).strip()
    match = TOOL_CALL_RE.search(cleaned)
    if not match:
        return None
    tool_name = match.group(1)
    payload = match.group(2) or ""
    badge = _tool_badge(tool_name)
    label = tool_name.split(".")[-1]
    lower_payload = payload.lower()

    if "github" in tool_name.lower() or "pull_request" in label or label.startswith("_fetch_pr") or label.startswith("_search_pr"):
        if "comments" in label:
            return f"[GH] comments"
        if "issue" in label:
            return f"[GH] issue"
        if "patch" in label or "diff" in label:
            return f"[GH] patch"
        if "search" in label:
            return f"[GH] search"
        if "review" in label:
            return f"[GH] review"
        return "[GH] pr"

    if '"cmd":"' in payload or '"cmd": "' in payload:
        return f"[{badge}] cmd"
    if '"q":"' in payload or '"q": "' in payload:
        return f"[{badge}] query"
    if '"path":"' in payload or '"path": "' in payload:
        return f"[{badge}] path"
    if '"ticker":"' in payload or '"ticker": "' in payload:
        return f"[{badge}] ticker"
    if "search_query" in tool_name or '"search_query"' in lower_payload:
        return f"[{badge}] search"
    if any(key in lower_payload for key in ['"repo_full_name"', '"repo":"', '"repo": "', '"repository_full_name"']):
        return f"[{badge}] repo"
    if any(key in lower_payload for key in ['"pr_number"', '"pull_request"', '"issue_number"']):
        return f"[{badge}] item"
    return f"[{badge}] {label}"


def _compact_approval_line(text: str) -> str | None:
    cleaned = strip_ansi(text).strip()
    match = APPROVAL_RE.search(cleaned)
    if not match:
        return None
    action = match.group(1).strip().rstrip(".")
    words = action.split()
    preview = " ".join(words[:4]).lower()
    return f"[ASK] {preview}"


def _compact_commentary_line(cleaned: str) -> str:
    words = cleaned.split()
    if not words:
        return cleaned

    lower = cleaned.lower()
    if "checking" in lower or "confirming" in lower or "verifying" in lower:
        return "[..] checking"
    if "search" in lower or "looking" in lower or "locate" in lower:
        return "[..] searching"
    if "reading" in lower or "opening" in lower:
        return "[..] reading"
    if "patch" in lower or "edit" in lower or "updating" in lower or "writing" in lower:
        return "[..] editing"
    if "test" in lower or "self-test" in lower or "verify" in lower:
        return "[..] testing"
    if "plan" in lower:
        return "[..] planning"

    preview = " ".join(words[:3]).lower()
    return f"[..] {preview}"


def _compact_badged_codex_line(cleaned: str) -> str:
    lower = cleaned.lower()
    if lower.startswith("[..] reasoning"):
        return ""
    if lower.startswith("[ok] exec"):
        return "[OK] ok"
    if lower.startswith("[ex] exec_command"):
        return "[EX] run"
    if lower.startswith("[pt] apply_patch"):
        return "[PT] patch"
    if lower.startswith("[io] write_stdin"):
        return "[IO] in"
    if lower.startswith("[wb] "):
        return "[WB] web"
    if lower.startswith("[gh] "):
        return "[GH] git"
    if lower.startswith("[..] "):
        message = cleaned[5:].strip()
        return _compact_commentary_line(message)
    return cleaned


def _compact_codex_line(text: str) -> str:
    cleaned = strip_ansi(text).rstrip()
    indent, body = _leading_whitespace(cleaned)
    if not body:
        return text

    if body.startswith("[EV] response_item"):
        return ""
    if body.startswith("[EV] event_msg"):
        return ""
    if "WARNING: proceeding, even though we could not update PATH:" in body:
        return ""
    if body.startswith("Process running with session ID "):
        return f"{indent}[IO] running"
    if body.startswith("Process exited with code 0"):
        return f"{indent}[OK] done"
    if body.startswith("Process exited with code "):
        return f"{indent}[!!] exit"
    if body.startswith("Original token count:"):
        return ""
    if body.startswith("Wall time:"):
        return ""
    if body.startswith("Chunk ID:"):
        return ""
    if body == "Output:":
        return ""
    if body.startswith("Command:"):
        return f"{indent}[EX] cmd"
    if body.startswith("Wrote ") and " bytes to stdin" in body:
        return f"{indent}[IO] input"
    if body.startswith("Requesting approval") or body.startswith("approval required"):
        return f"{indent}[ASK] approval"
    if body.startswith("Top-level comment") or body.startswith("Reply text"):
        return f"{indent}[GH] comment"
    if "pull request" in body.lower():
        return f"{indent}[GH] pr"
    if "issue comment" in body.lower():
        return f"{indent}[GH] issue"
    if body.startswith("["):
        compact_badged = _compact_badged_codex_line(body)
        if compact_badged != body:
            return f"{indent}{compact_badged}" if compact_badged else ""

    compact_approval = _compact_approval_line(text)
    if compact_approval is not None:
        return f"{indent}{compact_approval}"
    compact_tool = _compact_tool_call(text)
    if compact_tool is not None:
        return f"{indent}{compact_tool}"

    if COMMENTARY_PREFIX_RE.match(body):
        return f"{indent}{_compact_commentary_line(body)}"

    return text


def _short_path(raw_path: str, max_parts: int = 2) -> str:
    if raw_path in {"~", "/"}:
        return raw_path
    path = Path(raw_path)
    parts = [part for part in path.parts if part not in {"/", ""}]
    if len(parts) <= max_parts:
        return raw_path
    if raw_path.startswith("~/"):
        return f"~/{'/'.join(parts[-max_parts:])}"
    return "/".join(parts[-max_parts:])


def cell_width(font: ImageFont.ImageFont | ImageFont.FreeTypeFont) -> int:
    bbox = font.getbbox("M")
    return max(1, bbox[2] - bbox[0])


def render_terminal(
    draw: ImageDraw.ImageDraw,
    lines: Iterable[str],
    font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
    origin: tuple[int, int],
    line_height: int = 12,
    max_rows: int = 18,
    max_cols: int | None = None,
    codex_compact: bool = False,
) -> None:
    x, y = origin
    width = cell_width(font)
    if not codex_compact:
        for row_index, raw in enumerate(lines):
            if row_index >= max_rows:
                break
            raw = _compact_prompt_line(raw)
            row_y = y + row_index * line_height
            cursor_x = x
            cols_used = 0
            for segment, color in _line_segments(raw):
                visible = segment.replace("\t", "    ")
                if max_cols is not None:
                    remaining = max_cols - cols_used
                    if remaining <= 0:
                        break
                    visible = visible[:remaining]
                if not visible:
                    continue
                draw.text((cursor_x, row_y), visible, font=font, fill=color)
                advance = len(visible) * width
                cursor_x += advance
                cols_used += len(visible)
        return

    rendered_rows = 0
    for raw in lines:
        raw = _compact_prompt_line(raw)
        raw = _compact_codex_line(raw)
        if not raw:
            continue
        segments = _line_segments(raw)
        pending: list[tuple[str, str]] = []
        for segment, color in segments:
            visible = segment.replace("\t", "    ")
            while visible:
                if max_cols is None:
                    pending.append((visible, color))
                    visible = ""
                    continue
                used = sum(len(chunk) for chunk, _ in pending)
                remaining = max_cols - used
                if remaining <= 0:
                    if rendered_rows >= max_rows:
                        return
                    _draw_terminal_row(draw, pending, font, x, y + rendered_rows * line_height, width)
                    rendered_rows += 1
                    pending = []
                    continue
                pending.append((visible[:remaining], color))
                visible = visible[remaining:]
        if rendered_rows >= max_rows:
            return
        _draw_terminal_row(draw, pending, font, x, y + rendered_rows * line_height, width)
        rendered_rows += 1


def _draw_terminal_row(
    draw: ImageDraw.ImageDraw,
    segments: list[tuple[str, str]],
    font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
    x: int,
    y: int,
    width: int,
) -> None:
    cursor_x = x
    for segment, color in segments:
        if not segment:
            continue
        draw.text((cursor_x, y), segment, font=font, fill=color)
        cursor_x += len(segment) * width
