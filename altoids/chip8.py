from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import random

from PIL import Image, ImageColor

from .colors import ACCENT, BG


DISPLAY_WIDTH = 64
DISPLAY_HEIGHT = 32
HIGH_DISPLAY_WIDTH = 128
HIGH_DISPLAY_HEIGHT = 64
PROGRAM_START = 0x200
FONT_START = 0x50
LARGE_FONT_START = 0xA0

FONTSET = bytes(
    [
        0xF0,
        0x90,
        0x90,
        0x90,
        0xF0,
        0x20,
        0x60,
        0x20,
        0x20,
        0x70,
        0xF0,
        0x10,
        0xF0,
        0x80,
        0xF0,
        0xF0,
        0x10,
        0xF0,
        0x10,
        0xF0,
        0x90,
        0x90,
        0xF0,
        0x10,
        0x10,
        0xF0,
        0x80,
        0xF0,
        0x10,
        0xF0,
        0xF0,
        0x80,
        0xF0,
        0x90,
        0xF0,
        0xF0,
        0x10,
        0x20,
        0x40,
        0x40,
        0xF0,
        0x90,
        0xF0,
        0x90,
        0xF0,
        0xF0,
        0x90,
        0xF0,
        0x10,
        0xF0,
        0xF0,
        0x90,
        0xF0,
        0x90,
        0x90,
        0xE0,
        0x90,
        0xE0,
        0x90,
        0xE0,
        0xF0,
        0x80,
        0x80,
        0x80,
        0xF0,
        0xE0,
        0x90,
        0x90,
        0x90,
        0xE0,
        0xF0,
        0x80,
        0xF0,
        0x80,
        0xF0,
        0xF0,
        0x80,
        0xF0,
        0x80,
        0x80,
    ]
)

LARGE_FONTSET = bytes(
    [
        0x3C,
        0x66,
        0xC3,
        0xC3,
        0xC3,
        0xC3,
        0xC3,
        0xC3,
        0x66,
        0x3C,
        0x18,
        0x38,
        0x78,
        0x18,
        0x18,
        0x18,
        0x18,
        0x18,
        0x18,
        0x7E,
        0x7E,
        0xC3,
        0x03,
        0x06,
        0x0C,
        0x18,
        0x30,
        0x60,
        0xC0,
        0xFF,
        0x7E,
        0xC3,
        0x03,
        0x06,
        0x1C,
        0x06,
        0x03,
        0x03,
        0xC3,
        0x7E,
        0x06,
        0x0E,
        0x1E,
        0x36,
        0x66,
        0xC6,
        0xFF,
        0x06,
        0x06,
        0x06,
        0xFF,
        0xC0,
        0xC0,
        0xFC,
        0x06,
        0x03,
        0x03,
        0x03,
        0xC6,
        0x7C,
        0x3E,
        0x60,
        0xC0,
        0xFC,
        0xC6,
        0xC3,
        0xC3,
        0xC3,
        0x66,
        0x3C,
        0xFF,
        0x03,
        0x06,
        0x0C,
        0x18,
        0x30,
        0x30,
        0x30,
        0x30,
        0x30,
        0x3C,
        0x66,
        0xC3,
        0xC3,
        0x66,
        0x3C,
        0x66,
        0xC3,
        0x66,
        0x3C,
        0x3C,
        0x66,
        0xC3,
        0xC3,
        0xC3,
        0x67,
        0x3F,
        0x03,
        0x06,
        0x7C,
        0x18,
        0x3C,
        0x66,
        0xC3,
        0xC3,
        0xFF,
        0xC3,
        0xC3,
        0xC3,
        0xC3,
        0xC3,
        0xFC,
        0xC6,
        0xC3,
        0xC6,
        0xFC,
        0xC6,
        0xC3,
        0xC3,
        0xC6,
        0xFC,
        0x3E,
        0x63,
        0xC0,
        0xC0,
        0xC0,
        0xC0,
        0xC0,
        0xC0,
        0x63,
        0x3E,
        0xFC,
        0xC6,
        0xC3,
        0xC3,
        0xC3,
        0xC3,
        0xC3,
        0xC3,
        0xC6,
        0xFC,
        0xFF,
        0xC0,
        0xC0,
        0xC0,
        0xFC,
        0xC0,
        0xC0,
        0xC0,
        0xC0,
        0xFF,
        0xFF,
        0xC0,
        0xC0,
        0xC0,
        0xFC,
        0xC0,
        0xC0,
        0xC0,
        0xC0,
        0xC0,
    ]
)


class Chip8Error(RuntimeError):
    pass


@dataclass(slots=True)
class Chip8:
    memory: bytearray = field(default_factory=lambda: bytearray(65536))
    v: list[int] = field(default_factory=lambda: [0] * 16)
    stack: list[int] = field(default_factory=list)
    keys: list[bool] = field(default_factory=lambda: [False] * 16)
    framebuffer: list[list[bool]] = field(
        default_factory=lambda: [[False] * DISPLAY_WIDTH for _ in range(DISPLAY_HEIGHT)]
    )
    rpl_flags: list[int] = field(default_factory=lambda: [0] * 16)
    rng: random.Random = field(default_factory=random.Random)
    platform: str = "chip8"
    width: int = DISPLAY_WIDTH
    height: int = DISPLAY_HEIGHT
    draw_planes: int = 1
    i: int = 0
    pc: int = PROGRAM_START
    delay_timer: int = 0
    sound_timer: int = 0
    waiting_for_key_register: int | None = None

    def __post_init__(self) -> None:
        self._load_fonts()

    def configure(self, platform: str = "chip8") -> None:
        self.platform = platform.lower()
        if self.platform in {"schip", "xochip"}:
            self._set_resolution(HIGH_DISPLAY_WIDTH, HIGH_DISPLAY_HEIGHT)
        else:
            self._set_resolution(DISPLAY_WIDTH, DISPLAY_HEIGHT)

    def reset(self, platform: str | None = None) -> None:
        if platform is not None:
            self.platform = platform.lower()
        self.memory = bytearray(65536)
        self._load_fonts()
        self.v = [0] * 16
        self.stack.clear()
        self.keys = [False] * 16
        if self.platform in {"schip", "xochip"}:
            self._set_resolution(HIGH_DISPLAY_WIDTH, HIGH_DISPLAY_HEIGHT)
        else:
            self._set_resolution(DISPLAY_WIDTH, DISPLAY_HEIGHT)
        self.i = 0
        self.pc = PROGRAM_START
        self.delay_timer = 0
        self.sound_timer = 0
        self.draw_planes = 1
        self.waiting_for_key_register = None

    def _load_fonts(self) -> None:
        self.memory[FONT_START : FONT_START + len(FONTSET)] = FONTSET
        self.memory[LARGE_FONT_START : LARGE_FONT_START + len(LARGE_FONTSET)] = LARGE_FONTSET

    def _set_resolution(self, width: int, height: int) -> None:
        old_framebuffer = self.framebuffer
        old_width = getattr(self, "width", DISPLAY_WIDTH)
        old_height = getattr(self, "height", DISPLAY_HEIGHT)
        self.width = width
        self.height = height
        self.framebuffer = [[False] * width for _ in range(height)]
        for y in range(min(old_height, height)):
            for x in range(min(old_width, width)):
                self.framebuffer[y][x] = old_framebuffer[y][x]

    def load_rom(self, data: bytes, start: int = PROGRAM_START) -> None:
        if start < 0 or start + len(data) > len(self.memory):
            raise Chip8Error("ROM does not fit in memory")
        self.memory[start : start + len(data)] = data
        self.pc = start

    def set_key(self, key: int, pressed: bool) -> None:
        if key < 0 or key >= len(self.keys):
            return
        self.keys[key] = pressed
        if pressed and self.waiting_for_key_register is not None:
            self.v[self.waiting_for_key_register] = key
            self.waiting_for_key_register = None

    def step(self) -> int:
        if self.waiting_for_key_register is not None:
            return 0
        if self.memory[self.pc] == 0xF0 and self.memory[self.pc + 1] == 0x00:
            opcode = (self.memory[self.pc] << 24) | (self.memory[self.pc + 1] << 16) | (self.memory[self.pc + 2] << 8) | self.memory[self.pc + 3]
            self.pc += 4
            self._execute_long(opcode)
            return opcode
        opcode = (self.memory[self.pc] << 8) | self.memory[self.pc + 1]
        self.pc += 2
        self._execute(opcode)
        return opcode

    def run_steps(self, count: int) -> list[int]:
        return [self.step() for _ in range(count)]

    def render_image(self, scale: int = 4, on: str = ACCENT, off: str = BG) -> Image.Image:
        image = Image.new("RGB", (self.width, self.height), off)
        pixels = image.load()
        on_rgb = ImageColor.getrgb(on)
        for y, row in enumerate(self.framebuffer):
            for x, lit in enumerate(row):
                if lit:
                    pixels[x, y] = on_rgb
        return image.resize((self.width * scale, self.height * scale), Image.Resampling.NEAREST)

    def save_image(self, path: Path, scale: int = 4) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.render_image(scale=scale).save(path)

    def tick_timers(self) -> None:
        if self.delay_timer > 0:
            self.delay_timer -= 1
        if self.sound_timer > 0:
            self.sound_timer -= 1

    def _execute_long(self, opcode: int) -> None:
        if opcode & 0xFFFF0000 == 0xF0000000:
            self.i = opcode & 0xFFFF
            return
        raise Chip8Error(f"unsupported opcode {opcode:08X}")

    def _execute(self, opcode: int) -> None:
        nnn = opcode & 0x0FFF
        nn = opcode & 0x00FF
        n = opcode & 0x000F
        x = (opcode & 0x0F00) >> 8
        y = (opcode & 0x00F0) >> 4

        match opcode & 0xF000:
            case 0x0000:
                if opcode == 0x00E0:
                    self._clear()
                elif opcode == 0x00EE:
                    if not self.stack:
                        raise Chip8Error("return with empty stack")
                    self.pc = self.stack.pop()
                elif opcode == 0x00FB:
                    self._scroll_right(4)
                elif opcode == 0x00FC:
                    self._scroll_left(4)
                elif opcode == 0x00FD:
                    raise Chip8Error("program exited")
                elif opcode == 0x00FE:
                    self._set_resolution(DISPLAY_WIDTH, DISPLAY_HEIGHT)
                elif opcode == 0x00FF:
                    self._set_resolution(HIGH_DISPLAY_WIDTH, HIGH_DISPLAY_HEIGHT)
                elif (opcode & 0xFFF0) == 0x00C0:
                    self._scroll_down(n)
                elif (opcode & 0xFFF0) == 0x00D0:
                    self._scroll_up(n)
                else:
                    raise Chip8Error(f"unsupported opcode {opcode:04X}")
            case 0x1000:
                self.pc = nnn
            case 0x2000:
                self.stack.append(self.pc)
                self.pc = nnn
            case 0x3000:
                if self.v[x] == nn:
                    self.pc += 2
            case 0x4000:
                if self.v[x] != nn:
                    self.pc += 2
            case 0x5000:
                if n == 0 and self.v[x] == self.v[y]:
                    self.pc += 2
                elif n == 2:
                    self._store_register_range(x, y)
                elif n == 3:
                    self._load_register_range(x, y)
                elif n != 0:
                    raise Chip8Error(f"unsupported opcode {opcode:04X}")
            case 0x6000:
                self.v[x] = nn
            case 0x7000:
                self.v[x] = (self.v[x] + nn) & 0xFF
            case 0x8000:
                self._execute_alu(opcode, x, y, n)
            case 0x9000:
                if n == 0 and self.v[x] != self.v[y]:
                    self.pc += 2
                elif n != 0:
                    raise Chip8Error(f"unsupported opcode {opcode:04X}")
            case 0xA000:
                self.i = nnn
            case 0xB000:
                self.pc = nnn + self.v[0]
            case 0xC000:
                self.v[x] = self.rng.randrange(0, 256) & nn
            case 0xD000:
                self._draw_sprite(self.v[x], self.v[y], n)
            case 0xE000:
                self._execute_key_skip(opcode, x, nn)
            case 0xF000:
                self._execute_misc(opcode, x, nn)
            case _:
                raise Chip8Error(f"unsupported opcode {opcode:04X}")

    def _execute_alu(self, opcode: int, x: int, y: int, n: int) -> None:
        match n:
            case 0x0:
                self.v[x] = self.v[y]
            case 0x1:
                self.v[x] |= self.v[y]
            case 0x2:
                self.v[x] &= self.v[y]
            case 0x3:
                self.v[x] ^= self.v[y]
            case 0x4:
                result = self.v[x] + self.v[y]
                self.v[x] = result & 0xFF
                self.v[0xF] = 1 if result > 0xFF else 0
            case 0x5:
                self.v[0xF] = 1 if self.v[x] >= self.v[y] else 0
                self.v[x] = (self.v[x] - self.v[y]) & 0xFF
            case 0x6:
                self.v[0xF] = self.v[x] & 0x1
                self.v[x] >>= 1
            case 0x7:
                self.v[0xF] = 1 if self.v[y] >= self.v[x] else 0
                self.v[x] = (self.v[y] - self.v[x]) & 0xFF
            case 0xE:
                self.v[0xF] = (self.v[x] >> 7) & 0x1
                self.v[x] = (self.v[x] << 1) & 0xFF
            case _:
                raise Chip8Error(f"unsupported opcode {opcode:04X}")

    def _execute_key_skip(self, opcode: int, x: int, nn: int) -> None:
        key = self.v[x] & 0xF
        if nn == 0x9E:
            if self.keys[key]:
                self.pc = (self.pc + 2) & 0xFFF
            return
        if nn == 0xA1:
            if not self.keys[key]:
                self.pc = (self.pc + 2) & 0xFFF
            return
        raise Chip8Error(f"unsupported opcode {opcode:04X}")

    def _execute_misc(self, opcode: int, x: int, nn: int) -> None:
        match nn:
            case 0x07:
                self.v[x] = self.delay_timer
            case 0x0A:
                pressed = next((key for key, is_pressed in enumerate(self.keys) if is_pressed), None)
                if pressed is None:
                    self.waiting_for_key_register = x
                else:
                    self.v[x] = pressed
            case 0x15:
                self.delay_timer = self.v[x]
            case 0x18:
                self.sound_timer = self.v[x]
            case 0x1E:
                self.i = (self.i + self.v[x]) & 0xFFFF
            case 0x29:
                self.i = FONT_START + (self.v[x] & 0xF) * 5
            case 0x30:
                self.i = LARGE_FONT_START + (self.v[x] & 0xF) * 10
            case 0x01:
                self.draw_planes = max(1, self.v[x] & 0x3)
            case 0x02:
                return
            case 0x33:
                value = self.v[x]
                self.memory[self.i] = value // 100
                self.memory[self.i + 1] = (value // 10) % 10
                self.memory[self.i + 2] = value % 10
            case 0x3A:
                return
            case 0x55:
                self.memory[self.i : self.i + x + 1] = bytes(self.v[: x + 1])
            case 0x65:
                self.v[: x + 1] = self.memory[self.i : self.i + x + 1]
            case 0x75:
                self.rpl_flags[: x + 1] = self.v[: x + 1]
            case 0x85:
                self.v[: x + 1] = self.rpl_flags[: x + 1]
            case _:
                raise Chip8Error(f"unsupported opcode {opcode:04X}")

    def _clear(self) -> None:
        for y in range(self.height):
            for x in range(self.width):
                self.framebuffer[y][x] = False

    def _draw_sprite(self, x_pos: int, y_pos: int, height: int) -> None:
        collision = False
        sprite_height = 16 if height == 0 else height
        sprite_width = 16 if height == 0 else 8
        for row in range(sprite_height):
            y = (y_pos + row) % self.height
            row_bytes = 2 if sprite_width == 16 else 1
            for byte_index in range(row_bytes):
                sprite_byte = self.memory[self.i + row * row_bytes + byte_index]
                for bit in range(8):
                    if sprite_byte & (0x80 >> bit) == 0:
                        continue
                    x = (x_pos + byte_index * 8 + bit) % self.width
                    if self.framebuffer[y][x]:
                        collision = True
                    self.framebuffer[y][x] = not self.framebuffer[y][x]
        self.v[0xF] = 1 if collision else 0

    def _scroll_down(self, lines: int) -> None:
        lines = min(max(0, lines), self.height)
        if lines == 0:
            return
        for y in range(self.height - 1, -1, -1):
            source_y = y - lines
            self.framebuffer[y] = list(self.framebuffer[source_y]) if source_y >= 0 else [False] * self.width

    def _scroll_up(self, lines: int) -> None:
        lines = min(max(0, lines), self.height)
        if lines == 0:
            return
        for y in range(self.height):
            source_y = y + lines
            self.framebuffer[y] = list(self.framebuffer[source_y]) if source_y < self.height else [False] * self.width

    def _scroll_right(self, columns: int) -> None:
        columns = min(max(0, columns), self.width)
        if columns == 0:
            return
        for y in range(self.height):
            self.framebuffer[y] = [False] * columns + self.framebuffer[y][: self.width - columns]

    def _scroll_left(self, columns: int) -> None:
        columns = min(max(0, columns), self.width)
        if columns == 0:
            return
        for y in range(self.height):
            self.framebuffer[y] = self.framebuffer[y][columns:] + [False] * columns

    def _store_register_range(self, x: int, y: int) -> None:
        registers = self._register_range(x, y)
        self.memory[self.i : self.i + len(registers)] = bytes(self.v[index] for index in registers)

    def _load_register_range(self, x: int, y: int) -> None:
        registers = self._register_range(x, y)
        for offset, index in enumerate(registers):
            self.v[index] = self.memory[self.i + offset]

    @staticmethod
    def _register_range(x: int, y: int) -> range:
        step = 1 if x <= y else -1
        return range(x, y + step, step)
