from __future__ import annotations

from PIL import ImageDraw, ImageFont

from ..colors import ACCENT, BG, DIM, FG, SURFACE_ALT, SURFACE_GRID, SURFACE_INSET, SURFACE_OFF, SURFACE_PANEL

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


def draw_panel(
    draw: ImageDraw.ImageDraw,
    bounds: tuple[int, int, int, int],
    *,
    title: str | None = None,
    title_font: ImageFont.ImageFont | None = None,
    title_color: str = ACCENT,
    outline: str = ACCENT,
    fill: str = SURFACE_PANEL,
    inner_outline: str | None = SURFACE_INSET,
) -> None:
    left, top, right, bottom = bounds
    draw.rounded_rectangle(bounds, radius=8, outline=outline, fill=fill)
    if inner_outline is not None and right - left > 12 and bottom - top > 12:
        draw.rounded_rectangle((left + 5, top + 5, right - 5, bottom - 5), radius=6, outline=inner_outline, fill=None)
    draw.line((left + 8, top + 16, right - 8, top + 16), fill=inner_outline or DIM, width=1)
    draw_corner_ticks(draw, bounds, color=outline)
    if title and title_font is not None:
        draw.rectangle((left + 10, top - 1, min(right - 10, left + 10 + len(title) * 8), top + 11), fill=fill)
        draw.text((left + 12, top + 1), title, font=title_font, fill=title_color)


def draw_corner_ticks(
    draw: ImageDraw.ImageDraw,
    bounds: tuple[int, int, int, int],
    *,
    color: str = ACCENT,
    length: int = 8,
) -> None:
    left, top, right, bottom = bounds
    for x0, x1, y in (
        (left + 3, left + 3 + length, top + 3),
        (right - 3 - length, right - 3, top + 3),
        (left + 3, left + 3 + length, bottom - 3),
        (right - 3 - length, right - 3, bottom - 3),
    ):
        draw.line((x0, y, x1, y), fill=color, width=1)
    for x, y0, y1 in (
        (left + 3, top + 3, top + 3 + length),
        (right - 3, top + 3, top + 3 + length),
        (left + 3, bottom - 3 - length, bottom - 3),
        (right - 3, bottom - 3 - length, bottom - 3),
    ):
        draw.line((x, y0, x, y1), fill=color, width=1)


def draw_scanlines(draw: ImageDraw.ImageDraw, bounds: tuple[int, int, int, int], *, step: int = 5, color: str = SURFACE_GRID) -> None:
    left, top, right, bottom = bounds
    for y in range(top + 3, bottom - 2, step):
        draw.line((left + 3, y, right - 3, y), fill=color, width=1)


def draw_segmented_bar(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    width: int,
    pct: float,
    *,
    segments: int = 10,
    color: str = ACCENT,
    off_color: str = SURFACE_OFF,
) -> None:
    pct = max(0.0, min(1.0, pct))
    segments = max(1, segments)
    gap = 2
    segment_width = max(2, (width - gap * (segments - 1)) // segments)
    lit_segments = int(round(pct * segments))
    for index in range(segments):
        left = x + index * (segment_width + gap)
        right = left + segment_width
        fill = color if index < lit_segments else off_color
        draw.rectangle((left, y, right, y + 6), outline=DIM, fill=fill)


def draw_button_bar(draw: ImageDraw.ImageDraw, width: int, height: int, hints: list[str], font: ImageFont.ImageFont) -> None:
    top = height - BUTTON_BAR_HEIGHT
    draw.rectangle((0, top, width, height), fill=SURFACE_ALT)
    draw_separator(draw, top, width)
    labels = hints[:4] + ["-"] * max(0, 4 - len(hints))
    segment_width = max(1, width // 4)
    for index, text in enumerate(labels[:4]):
        draw.text((8 + index * segment_width, top + 7), text, font=font, fill=FG if text != "-" else DIM)
