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
    match = PROMPT_RE.match(cleaned.strip())
    if not match:
        return text
    path_label = _short_path(match.group("path"))
    rest = match.group("rest") or ""
    compacted = f"{path_label}{match.group('sigil')}"
    if rest:
        compacted = f"{compacted} {rest}"
    return compacted


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
) -> None:
    x, y = origin
    width = cell_width(font)
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
