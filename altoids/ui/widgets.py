from __future__ import annotations

from PIL import ImageDraw, ImageFont

from ..colors import ACCENT, BG, DIM, FG

BUTTON_BAR_HEIGHT = 24


def draw_progress_bar(draw: ImageDraw.ImageDraw, x: int, y: int, width: int, pct: float, color: str = ACCENT) -> None:
    pct = max(0.0, min(1.0, pct))
    draw.rectangle((x, y, x + width, y + 7), outline=DIM, fill=None)
    fill_width = max(1, int((width - 2) * pct)) if pct > 0 else 0
    if fill_width:
        draw.rectangle((x + 1, y + 1, x + fill_width, y + 6), fill=color)


def draw_status_dot(draw: ImageDraw.ImageDraw, x: int, y: int, active: bool, color: str = ACCENT) -> None:
    draw.ellipse((x, y, x + 8, y + 8), outline=color if active else DIM, fill=color if active else BG)


def draw_label(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, font: ImageFont.ImageFont, color: str = FG) -> None:
    draw.text((x, y), text, font=font, fill=color)


def draw_separator(draw: ImageDraw.ImageDraw, y: int, width: int) -> None:
    for x in range(0, width, 6):
        draw.line((x, y, x + 2, y), fill=DIM, width=1)


def draw_button_bar(draw: ImageDraw.ImageDraw, width: int, height: int, hints: list[str], font: ImageFont.ImageFont) -> None:
    top = height - BUTTON_BAR_HEIGHT
    draw.rectangle((0, top, width, height), fill=BG)
    draw_separator(draw, top, width)
    labels = hints[:4] + ["-"] * max(0, 4 - len(hints))
    segment_width = max(1, width // 4)
    for index, text in enumerate(labels[:4]):
        draw.text((8 + index * segment_width, top + 7), text, font=font, fill=FG)
