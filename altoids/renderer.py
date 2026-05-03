from __future__ import annotations

import re
from typing import Iterable

from PIL import ImageDraw, ImageFont

from .colors import FG

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def render_terminal(
    draw: ImageDraw.ImageDraw,
    lines: Iterable[str],
    font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
    origin: tuple[int, int],
    line_height: int = 12,
    max_rows: int = 18,
) -> None:
    x, y = origin
    for row_index, raw in enumerate(lines):
        if row_index >= max_rows:
            break
        draw.text((x, y + row_index * line_height), strip_ansi(raw), font=font, fill=FG)
