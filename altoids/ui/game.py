from __future__ import annotations

from dataclasses import dataclass
import random

from PIL import ImageDraw

from ..colors import ACCENT, BG, COOL, DANGER, DIM, FG, INFO, SURFACE_ALT, SURFACE_GRID, SURFACE_INSET, WARN
from ..input_keyboard import KeyboardEvent
from .base import Screen, ScreenContext
from .widgets import draw_label, draw_scanlines, draw_segmented_bar, draw_status_dot


@dataclass(slots=True)
class Threat:
    lane: int
    x: float
    speed: float
    glyph: str
    phase: float = 0.0


class GameSelectScreen(Screen):
    name = "game"

    games = (
        ("SYNC/DEFLECT", "AT FIELD TIMING", "sync_deflect"),
        ("MAGI/ROUTE", "ROTATE SIGNAL PATH", "magi_route"),
    )

    def __init__(self, context: ScreenContext) -> None:
        super().__init__(context)
        self.selection = 0
        self.blink = 0.0

    def update(self, dt: float) -> bool:
        self.blink = (self.blink + dt) % 1.0
        return True

    def render(self, draw: ImageDraw.ImageDraw, buffer) -> None:
        app = self.context.app
        width = app.config.display.width
        height = app.config.display.height
        buffer.paste(self.cached_background((width, height), buffer.size, self._paint_static_background))
        draw = ImageDraw.Draw(buffer)

        row_top = 58
        row_height = 52
        for index, (title, subtitle, _) in enumerate(self.games):
            top = row_top + index * (row_height + 14)
            selected = index == self.selection
            outline = ACCENT if selected else SURFACE_INSET
            fill = "#0C1612" if selected else BG
            draw.rectangle((22, top, width - 22, top + row_height), outline=outline, fill=fill)
            draw.rectangle((28, top + 6, 44, top + row_height - 6), outline=outline, fill=BG)
            if selected and self.blink < 0.55:
                draw.rectangle((32, top + 18, 40, top + 26), fill=outline)
            draw_label(draw, 56, top + 11, title, app.font_large, FG if selected else DIM)
            draw_label(draw, 56, top + 32, subtitle, app.font, WARN if selected else DIM)
            draw_label(draw, width - 52, top + 11, f"0{index + 1}", app.font, outline)

    def _paint_static_background(self, draw: ImageDraw.ImageDraw, buffer) -> None:
        app = self.context.app
        width = app.config.display.width
        height = app.config.display.height
        draw.rectangle((0, 0, width, height), fill=BG)
        draw.rectangle((8, 8, width - 8, height - 8), outline=SURFACE_INSET, fill=None)
        draw.rectangle((12, 12, width - 12, height - 12), outline=SURFACE_GRID, fill=None)
        draw_scanlines(draw, (12, 12, width - 12, height - 12), step=7, color=SURFACE_GRID)
        draw_label(draw, 16, 13, "GAME SELECT", app.font, ACCENT)
        draw_label(draw, width - 72, 13, "2 CARTS", app.font, WARN)
        draw.line((16, 32, width - 16, 32), fill=SURFACE_INSET, width=1)
        draw_label(draw, 18, height - 25, "UP/DOWN SELECT", app.font, DIM)
        draw_label(draw, 126, height - 25, "SPACE LOAD", app.font, DIM)
        draw_label(draw, width - 56, height - 25, "Q HOME", app.font, DIM)

    def on_keyboard_event(self, event: KeyboardEvent) -> bool:
        if event.ctrl or event.alt:
            return False
        if event.key in {"q", "escape"}:
            self.context.app.set_screen("home")
            return True
        if event.key in {"up", "w", "k"}:
            self.selection = (self.selection - 1) % len(self.games)
            return True
        if event.key in {"down", "s", "j"}:
            self.selection = (self.selection + 1) % len(self.games)
            return True
        if event.key in {"1", "2"}:
            self.selection = min(len(self.games) - 1, int(event.key) - 1)
            self._launch_selection()
            return True
        if event.raw_key == "KEY_SPACE" or event.key in {"enter", "z", "x"}:
            self._launch_selection()
            return True
        return False

    def on_button(self, button: str, long_press: bool) -> bool:
        if button == "A":
            self.selection = (self.selection - 1) % len(self.games)
            return True
        if button == "B":
            self.selection = (self.selection + 1) % len(self.games)
            return True
        if button == "X":
            self._launch_selection()
            return True
        if button == "Y":
            self.context.app.set_screen("home")
            return True
        return False

    def get_button_hints(self) -> list[str]:
        return ["A up", "B down", "X load", "Y home"]

    def _launch_selection(self) -> None:
        self.context.app.set_screen(self.games[self.selection][2])


class SyncDeflectScreen(Screen):
    name = "sync_deflect"

    lane_count = 4
    shield_x = 52
    hit_window = 10

    def __init__(self, context: ScreenContext) -> None:
        super().__init__(context)
        self.rng = random.Random(814)
        self.cursor_lane = 1
        self.threats: list[Threat] = []
        self.spawn_timer = 0.8
        self.score = 0
        self.combo = 0
        self.best = 0
        self.sync = 1.0
        self.heat = 0.0
        self.elapsed = 0.0
        self.state = "ready"
        self.flash_timer = 0.0
        self.flash_text = "SYNC READY"

    def reset(self) -> None:
        self.cursor_lane = 1
        self.threats.clear()
        self.spawn_timer = 0.65
        self.score = 0
        self.combo = 0
        self.sync = 1.0
        self.heat = 0.0
        self.elapsed = 0.0
        self.state = "running"
        self.flash_timer = 0.6
        self.flash_text = "FIELD ONLINE"

    def update(self, dt: float) -> bool:
        if self.flash_timer > 0:
            self.flash_timer = max(0.0, self.flash_timer - dt)
        if self.state != "running":
            return self.flash_timer > 0

        self.elapsed += dt
        self.spawn_timer -= dt
        self.heat = max(0.0, self.heat - dt * 0.18)
        if self.spawn_timer <= 0:
            self._spawn_threat()

        missed: list[Threat] = []
        for threat in self.threats:
            threat.x -= threat.speed * dt
            threat.phase += dt
            if threat.x < self.shield_x - 18:
                missed.append(threat)

        for threat in missed:
            self.threats.remove(threat)
            self.combo = 0
            self.sync = max(0.0, self.sync - 0.22)
            self.flash_timer = 0.4
            self.flash_text = "BREACH"

        if self.sync <= 0:
            self.state = "lost"
            self.best = max(self.best, self.score)
            self.flash_timer = 1.2
            self.flash_text = "SYNC LOST"
        return True

    def render(self, draw: ImageDraw.ImageDraw, buffer) -> None:
        app = self.context.app
        width = app.config.display.width
        height = app.config.display.height
        signature = (width, height)
        buffer.paste(self.cached_background(signature, buffer.size, self._paint_static_background))
        draw = ImageDraw.Draw(buffer)

        lane_y = self._lane_y_positions(height)
        for index, y in enumerate(lane_y):
            color = ACCENT if index == self.cursor_lane else SURFACE_GRID
            draw.line((24, y, width - 18, y), fill=color, width=1)
            draw.rectangle((30, y - 3, 36, y + 3), outline=COOL if index == self.cursor_lane else DIM)

        self._draw_shield(draw, lane_y[self.cursor_lane])
        self._draw_threats(draw, lane_y)
        self._draw_hud(draw, width, height)

        if self.state != "running":
            self._draw_state_overlay(draw, width, height)
        elif self.flash_timer > 0:
            draw_label(draw, 95, 31, self.flash_text, app.font, WARN if self.flash_text == "BREACH" else ACCENT)

    def _paint_static_background(self, draw: ImageDraw.ImageDraw, buffer) -> None:
        app = self.context.app
        width = app.config.display.width
        height = app.config.display.height
        draw.rectangle((0, 0, width, height), fill=BG)
        draw.rectangle((8, 8, width - 8, height - 8), outline=SURFACE_INSET, fill=None)
        draw.rectangle((12, 12, width - 12, height - 12), outline=SURFACE_GRID, fill=None)
        draw_scanlines(draw, (12, 12, width - 12, height - 12), step=7, color=SURFACE_GRID)
        draw_label(draw, 16, 13, "SYNC/DEFLECT", app.font, ACCENT)
        draw_label(draw, width - 76, 13, "AT-FLD", app.font, WARN)
        draw.line((16, 30, width - 16, 30), fill=SURFACE_INSET, width=1)
        draw.line((16, height - 34, width - 16, height - 34), fill=SURFACE_INSET, width=1)
        for x in range(self.shield_x - self.hit_window, self.shield_x + self.hit_window + 1, 5):
            draw.line((x, 42, x, height - 46), fill=SURFACE_GRID, width=1)
        draw_label(draw, 18, height - 25, "W/S MOVE", app.font, DIM)
        draw_label(draw, 94, height - 25, "SPACE DEFLECT", app.font, DIM)
        draw_label(draw, width - 56, height - 25, "Q HOME", app.font, DIM)

    def _draw_shield(self, draw: ImageDraw.ImageDraw, y: int) -> None:
        color = WARN if self.heat > 0.7 else ACCENT
        draw.line((self.shield_x, y - 22, self.shield_x, y + 22), fill=color, width=2)
        draw.rectangle((self.shield_x - 8, y - 8, self.shield_x + 8, y + 8), outline=color, fill=BG)
        draw.rectangle((self.shield_x - 3, y - 3, self.shield_x + 3, y + 3), fill=color)

    def _draw_threats(self, draw: ImageDraw.ImageDraw, lane_y: list[int]) -> None:
        app = self.context.app
        for threat in self.threats:
            x = int(threat.x)
            y = lane_y[threat.lane]
            urgent = abs(x - self.shield_x) <= self.hit_window
            color = WARN if urgent else INFO
            draw.rectangle((x - 6, y - 6, x + 6, y + 6), outline=color, fill=BG)
            draw.line((x - 9, y, x - 3, y), fill=color, width=1)
            draw.line((x + 3, y, x + 9, y), fill=color, width=1)
            draw_label(draw, x - 3, y - 5, threat.glyph, app.font, color)

    def _draw_hud(self, draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
        app = self.context.app
        draw_label(draw, 18, 36, f"SCORE {self.score:04d}", app.font, FG)
        draw_label(draw, 18, 50, f"BEST  {self.best:04d}", app.font, DIM)
        draw_status_dot(draw, 103, 40, self.state == "running", ACCENT)
        draw_label(draw, 118, 36, f"COMBO {self.combo:02d}", app.font, COOL if self.combo else DIM)
        draw_label(draw, width - 92, 36, "SYNC", app.font, ACCENT)
        draw_segmented_bar(draw, width - 54, 39, 36, self.sync, segments=5, color=ACCENT, off_color=SURFACE_ALT)
        draw_label(draw, width - 92, 51, "HEAT", app.font, WARN if self.heat > 0.7 else DIM)
        draw_segmented_bar(draw, width - 54, 54, 36, self.heat, segments=5, color=WARN, off_color=SURFACE_ALT)
        if self.state == "running":
            draw_label(draw, width - 79, height - 50, f"T+{int(self.elapsed):03d}", app.font, DIM)

    def _draw_state_overlay(self, draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
        app = self.context.app
        left = 42
        top = 78
        right = width - 42
        bottom = 158
        outline = DANGER if self.state == "lost" else ACCENT
        draw.rectangle((left, top, right, bottom), outline=outline, fill=BG)
        draw.rectangle((left + 4, top + 4, right - 4, bottom - 4), outline=SURFACE_INSET, fill=None)
        title = "SYNC LOST" if self.state == "lost" else "SYNC READY"
        prompt = "R / SPACE RESTART" if self.state == "lost" else "SPACE START"
        draw_label(draw, left + 22, top + 17, title, app.font_large, outline)
        draw_label(draw, left + 22, top + 45, prompt, app.font, FG)
        draw_label(draw, left + 22, top + 60, "W/S OR ARROWS SELECT LANE", app.font, DIM)

    def on_keyboard_event(self, event: KeyboardEvent) -> bool:
        if event.ctrl or event.alt:
            return False
        if event.key in {"q", "escape"}:
            self.context.app.set_screen("game")
            return True
        if event.key in {"r"}:
            self.reset()
            return True
        if event.key in {"up", "w", "k"}:
            self.cursor_lane = max(0, self.cursor_lane - 1)
            return True
        if event.key in {"down", "s", "j"}:
            self.cursor_lane = min(self.lane_count - 1, self.cursor_lane + 1)
            return True
        if event.raw_key == "KEY_SPACE" or event.key in {"enter", "z", "x"}:
            if self.state != "running":
                self.reset()
            else:
                self._deflect()
            return True
        return False

    def on_button(self, button: str, long_press: bool) -> bool:
        if button == "A":
            self.cursor_lane = max(0, self.cursor_lane - 1)
            return True
        if button == "B":
            self.cursor_lane = min(self.lane_count - 1, self.cursor_lane + 1)
            return True
        if button == "X":
            if self.state != "running":
                self.reset()
            else:
                self._deflect()
            return True
        if button == "Y":
            self.context.app.set_screen("game")
            return True
        return False

    def get_button_hints(self) -> list[str]:
        return ["A up", "B down", "X hit", "Y menu"]

    def debug_state(self) -> dict[str, object]:
        return {
            "state": self.state,
            "score": self.score,
            "best": self.best,
            "combo": self.combo,
            "sync": round(self.sync, 2),
            "heat": round(self.heat, 2),
            "threats": len(self.threats),
        }

    def _deflect(self) -> None:
        if self.heat >= 1.0:
            self.sync = max(0.0, self.sync - 0.08)
            self.flash_timer = 0.25
            self.flash_text = "HEAT LOCK"
            return

        target = self._target_threat()
        self.heat = min(1.0, self.heat + 0.18)
        if target is None:
            self.combo = 0
            self.sync = max(0.0, self.sync - 0.08)
            self.flash_timer = 0.25
            self.flash_text = "NO LOCK"
            return

        distance = abs(target.x - self.shield_x)
        self.threats.remove(target)
        self.combo += 1
        bonus = 2 if distance <= 4 else 1
        self.score += 10 * bonus + min(90, self.combo * 3)
        self.sync = min(1.0, self.sync + 0.035)
        self.flash_timer = 0.18
        self.flash_text = "PERFECT" if bonus == 2 else "DEFLECT"

    def _target_threat(self) -> Threat | None:
        candidates = [
            threat
            for threat in self.threats
            if threat.lane == self.cursor_lane and abs(threat.x - self.shield_x) <= self.hit_window
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda threat: abs(threat.x - self.shield_x))

    def _spawn_threat(self) -> None:
        app = self.context.app
        width = app.config.display.width
        difficulty = min(1.0, self.elapsed / 90.0)
        speed = 62.0 + difficulty * 42.0 + self.rng.uniform(-4.0, 8.0)
        lane = self.rng.randrange(self.lane_count)
        glyph = self.rng.choice(("I", "O", "X", "+"))
        self.threats.append(Threat(lane=lane, x=width - 26, speed=speed, glyph=glyph))
        interval = 1.08 - difficulty * 0.42
        self.spawn_timer = max(0.42, interval + self.rng.uniform(-0.18, 0.14))

    def _lane_y_positions(self, height: int) -> list[int]:
        top = 78
        bottom = height - 64
        if self.context.app.shows_button_bar:
            bottom -= 20
        step = (bottom - top) // (self.lane_count - 1)
        return [top + index * step for index in range(self.lane_count)]


class MagiRouteScreen(Screen):
    name = "magi_route"

    north = 1
    east = 2
    south = 4
    west = 8
    opposite = {north: south, east: west, south: north, west: east}
    directions = {
        north: (0, -1),
        east: (1, 0),
        south: (0, 1),
        west: (-1, 0),
    }

    def __init__(self, context: ScreenContext) -> None:
        super().__init__(context)
        self.rng = random.Random(213)
        self.size = 5
        self.cursor_x = 0
        self.cursor_y = 2
        self.board: list[list[int]] = []
        self.moves = 0
        self.solved = 0
        self.pulse = 0.0
        self.flash_timer = 0.0
        self.flash_text = "ROUTE READY"
        self.reset()

    def reset(self) -> None:
        self.cursor_x = 0
        self.cursor_y = 2
        self.moves = 0
        self.board = self._build_board()
        self.flash_timer = 0.7
        self.flash_text = "ROUTE READY"

    def update(self, dt: float) -> bool:
        self.pulse = (self.pulse + dt) % 1.0
        if self.flash_timer > 0:
            self.flash_timer = max(0.0, self.flash_timer - dt)
        return True

    def render(self, draw: ImageDraw.ImageDraw, buffer) -> None:
        app = self.context.app
        width = app.config.display.width
        height = app.config.display.height
        buffer.paste(self.cached_background((width, height), buffer.size, self._paint_static_background))
        draw = ImageDraw.Draw(buffer)

        cell = 28
        grid_left = (width - self.size * cell) // 2
        grid_top = 58
        live_cells = self._live_cells()
        for y in range(self.size):
            for x in range(self.size):
                self._draw_cell(draw, grid_left + x * cell, grid_top + y * cell, cell, self.board[y][x], (x, y) in live_cells)

        cx = grid_left + self.cursor_x * cell
        cy = grid_top + self.cursor_y * cell
        draw.rectangle((cx + 1, cy + 1, cx + cell - 2, cy + cell - 2), outline=WARN, fill=None)
        self._draw_hud(draw, width, height)

    def _paint_static_background(self, draw: ImageDraw.ImageDraw, buffer) -> None:
        app = self.context.app
        width = app.config.display.width
        height = app.config.display.height
        draw.rectangle((0, 0, width, height), fill=BG)
        draw.rectangle((8, 8, width - 8, height - 8), outline=SURFACE_INSET, fill=None)
        draw.rectangle((12, 12, width - 12, height - 12), outline=SURFACE_GRID, fill=None)
        draw_scanlines(draw, (12, 12, width - 12, height - 12), step=7, color=SURFACE_GRID)
        draw_label(draw, 16, 13, "MAGI/ROUTE", app.font, ACCENT)
        draw_label(draw, width - 82, 13, "SIG PATCH", app.font, WARN)
        draw.line((16, 32, width - 16, 32), fill=SURFACE_INSET, width=1)
        draw_label(draw, 18, height - 25, "ARROWS MOVE", app.font, DIM)
        draw_label(draw, 112, height - 25, "SPACE ROTATE", app.font, DIM)
        draw_label(draw, width - 56, height - 25, "Q MENU", app.font, DIM)

    def _draw_cell(self, draw: ImageDraw.ImageDraw, left: int, top: int, cell: int, mask: int, live: bool) -> None:
        mid_x = left + cell // 2
        mid_y = top + cell // 2
        color = ACCENT if live else INFO
        outline = SURFACE_INSET if not live else ACCENT
        draw.rectangle((left + 3, top + 3, left + cell - 4, top + cell - 4), outline=outline, fill=BG)
        if mask & self.north:
            draw.line((mid_x, top + 3, mid_x, mid_y), fill=color, width=2)
        if mask & self.east:
            draw.line((mid_x, mid_y, left + cell - 4, mid_y), fill=color, width=2)
        if mask & self.south:
            draw.line((mid_x, mid_y, mid_x, top + cell - 4), fill=color, width=2)
        if mask & self.west:
            draw.line((left + 3, mid_y, mid_x, mid_y), fill=color, width=2)
        draw.rectangle((mid_x - 2, mid_y - 2, mid_x + 2, mid_y + 2), fill=color if live else SURFACE_INSET)

    def _draw_hud(self, draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
        app = self.context.app
        routed = self.is_solved()
        draw_label(draw, 18, 38, f"MOVES {self.moves:03d}", app.font, FG)
        draw_label(draw, 102, 38, f"SOLVED {self.solved:02d}", app.font, COOL if self.solved else DIM)
        draw_label(draw, width - 83, 38, "LINK", app.font, ACCENT if routed else DIM)
        draw_segmented_bar(draw, width - 47, 41, 28, 1.0 if routed else len(self._live_cells()) / 8.0, segments=4, color=ACCENT, off_color=SURFACE_ALT)
        if self.flash_timer > 0:
            draw_label(draw, 98, 207, self.flash_text, app.font, ACCENT if routed else WARN)

    def on_keyboard_event(self, event: KeyboardEvent) -> bool:
        if event.ctrl or event.alt:
            return False
        if event.key in {"q", "escape"}:
            self.context.app.set_screen("game")
            return True
        if event.key == "r":
            self.reset()
            return True
        if event.key in {"left", "a", "h"}:
            self.cursor_x = max(0, self.cursor_x - 1)
            return True
        if event.key in {"right", "d", "l"}:
            self.cursor_x = min(self.size - 1, self.cursor_x + 1)
            return True
        if event.key in {"up", "w", "k"}:
            self.cursor_y = max(0, self.cursor_y - 1)
            return True
        if event.key in {"down", "s", "j"}:
            self.cursor_y = min(self.size - 1, self.cursor_y + 1)
            return True
        if event.raw_key == "KEY_SPACE" or event.key in {"enter", "z", "x"}:
            self._rotate_selected()
            return True
        return False

    def on_button(self, button: str, long_press: bool) -> bool:
        if button == "A":
            self.cursor_y = max(0, self.cursor_y - 1)
            return True
        if button == "B":
            self.cursor_y = min(self.size - 1, self.cursor_y + 1)
            return True
        if button == "X":
            self._rotate_selected()
            return True
        if button == "Y":
            self.context.app.set_screen("game")
            return True
        return False

    def get_button_hints(self) -> list[str]:
        return ["A up", "B down", "X rot", "Y menu"]

    def debug_state(self) -> dict[str, object]:
        return {
            "moves": self.moves,
            "solved": self.solved,
            "routed": self.is_solved(),
            "cursor": (self.cursor_x, self.cursor_y),
        }

    def _rotate_selected(self) -> None:
        self.board[self.cursor_y][self.cursor_x] = self._rotate_mask(self.board[self.cursor_y][self.cursor_x])
        self.moves += 1
        if self.is_solved():
            self.solved += 1
            self.flash_timer = 1.0
            self.flash_text = "ROUTE ACCEPT"
            self.board = self._build_board()
            self.moves = 0
            self.cursor_x = 0
            self.cursor_y = 2
        else:
            self.flash_timer = 0.22
            self.flash_text = "PATCH"

    def is_solved(self) -> bool:
        return (self.size - 1, 2) in self._live_cells(require_sink=True)

    def _live_cells(self, require_sink: bool = False) -> set[tuple[int, int]]:
        live: set[tuple[int, int]] = set()
        stack = [(0, 2)]
        if not self.board or not (self.board[2][0] & self.west):
            return live
        while stack:
            x, y = stack.pop()
            if (x, y) in live:
                continue
            live.add((x, y))
            mask = self.board[y][x]
            if require_sink and x == self.size - 1 and y == 2 and mask & self.east:
                return live
            for direction, (dx, dy) in self.directions.items():
                if not (mask & direction):
                    continue
                nx = x + dx
                ny = y + dy
                if not (0 <= nx < self.size and 0 <= ny < self.size):
                    continue
                if self.board[ny][nx] & self.opposite[direction]:
                    stack.append((nx, ny))
        return live

    def _build_board(self) -> list[list[int]]:
        board = [[self.rng.choice((self.north | self.south, self.east | self.west, self.north | self.east, self.east | self.south, self.south | self.west, self.west | self.north)) for _ in range(self.size)] for _ in range(self.size)]
        path = [(0, 2), (1, 2), (1, 1), (2, 1), (3, 1), (3, 2), (4, 2)]
        for index, (x, y) in enumerate(path):
            mask = 0
            if index == 0:
                mask |= self.west
            else:
                px, py = path[index - 1]
                mask |= self._direction_to(x, y, px, py)
            if index == len(path) - 1:
                mask |= self.east
            else:
                nx, ny = path[index + 1]
                mask |= self._direction_to(x, y, nx, ny)
            board[y][x] = mask
        for y in range(self.size):
            for x in range(self.size):
                rotations = self.rng.randrange(4)
                for _ in range(rotations):
                    board[y][x] = self._rotate_mask(board[y][x])
        if self._board_is_solved(board):
            board[2][0] = self._rotate_mask(board[2][0])
        return board

    def _board_is_solved(self, board: list[list[int]]) -> bool:
        old_board = self.board
        self.board = board
        solved = self.is_solved()
        self.board = old_board
        return solved

    def _direction_to(self, x: int, y: int, nx: int, ny: int) -> int:
        dx = nx - x
        dy = ny - y
        if dx == 1:
            return self.east
        if dx == -1:
            return self.west
        if dy == 1:
            return self.south
        return self.north

    def _rotate_mask(self, mask: int) -> int:
        rotated = 0
        if mask & self.north:
            rotated |= self.east
        if mask & self.east:
            rotated |= self.south
        if mask & self.south:
            rotated |= self.west
        if mask & self.west:
            rotated |= self.north
        return rotated
