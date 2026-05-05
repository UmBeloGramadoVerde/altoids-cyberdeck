from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw

from .colors import ACCENT, AUX, BG, COOL, DIM, FG, SURFACE_ALT, WARN


def _placeholder_frames() -> list[Image.Image]:
    frames: list[Image.Image] = []
    variants = [
        ("SYS//00", "(o_o)", r"<|==|>", "`-..-'"),
        ("SYS//00", "(-_-)", r"<|==|>", "`-..-'"),
        ("SYS//00", "(o_o)", r"<|<>|>", "`-..-'"),
        ("SYNC/02", "(^_^)", r"<|==|>", "`.--.'"),
        ("SYNC/02", "(-_-)", r"<|==|>", "`.--.'"),
        ("PING/77", "(o_o)", r"<|/\|>", "`-..-'"),
    ]
    for label, face, torso, footer in variants:
        image = Image.new("RGB", (32, 32), BG)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((1, 1, 30, 30), radius=5, outline=ACCENT, fill=SURFACE_ALT)
        draw.rounded_rectangle((4, 4, 27, 27), radius=4, outline=DIM, fill=BG)
        draw.line((6, 10, 25, 10), fill=DIM, width=1)
        draw.line((6, 24, 25, 24), fill=DIM, width=1)
        draw.text((6, 5), label, fill=COOL, font=None)
        draw.text((7, 12), face, fill=FG, font=None)
        draw.text((6, 18), torso, fill=AUX, font=None)
        draw.text((7, 25), footer, fill=WARN if "SYNC" in label else ACCENT, font=None)
        frames.append(image)
    return frames


def load_mascot_frames() -> list[Image.Image]:
    return _placeholder_frames()


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
