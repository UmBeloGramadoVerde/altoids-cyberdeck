from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from .colors import ACCENT, BG, FG


def _placeholder_frames() -> list[Image.Image]:
    frames: list[Image.Image] = []
    eye_patterns = [(10, 11), (10, 11), (10, 11), (10, 11), (10, 10), (10, 11)]
    for left_eye_y, right_eye_y in eye_patterns:
        image = Image.new("RGB", (32, 32), BG)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((4, 6, 28, 28), radius=6, fill=ACCENT)
        draw.rectangle((10, left_eye_y, 12, left_eye_y + 1), fill=BG)
        draw.rectangle((20, right_eye_y, 22, right_eye_y + 1), fill=BG)
        draw.rectangle((11, 20, 21, 21), fill=FG)
        frames.append(image)
    return frames


def load_sprite_sheet(path: Path, frame_width: int = 32, frame_height: int = 32) -> list[Image.Image]:
    if not path.exists():
        return _placeholder_frames()

    sprite_sheet = Image.open(path).convert("RGB")
    frames: list[Image.Image] = []
    for top in range(0, sprite_sheet.height, frame_height):
        for left in range(0, sprite_sheet.width, frame_width):
            box = (left, top, left + frame_width, top + frame_height)
            if box[2] <= sprite_sheet.width and box[3] <= sprite_sheet.height:
                frames.append(sprite_sheet.crop(box))
    return frames or _placeholder_frames()


@dataclass(slots=True)
class SpriteAnimator:
    frames: list[Image.Image]
    frame_seconds: float = 0.5
    index: int = 0
    elapsed: float = 0.0

    def update(self, dt: float) -> bool:
        self.elapsed += dt
        advanced = False
        while self.elapsed >= self.frame_seconds:
            self.elapsed -= self.frame_seconds
            self.index = (self.index + 1) % len(self.frames)
            advanced = True
        return advanced

    def current(self) -> Image.Image:
        return self.frames[self.index]
