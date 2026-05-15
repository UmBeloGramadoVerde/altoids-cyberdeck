from __future__ import annotations

import json
from types import SimpleNamespace
import tempfile
import time
import unittest

from PIL import Image, ImageFont

from altoids.input_keyboard import KeyboardEvent
from altoids.ui.base import ScreenContext
from altoids.ui.emulation import EmulationScreen


class EmulationScreenTest(unittest.TestCase):
    def make_screen(self) -> EmulationScreen:
        app = SimpleNamespace(
            config=SimpleNamespace(display=SimpleNamespace(width=280, height=240)),
            font=ImageFont.load_default(),
            font_large=ImageFont.load_default(),
        )
        return EmulationScreen(ScreenContext(app=app))

    def test_builtin_smoke_cart_runs(self) -> None:
        screen = self.make_screen()

        screen._load_selection()
        screen.update(1 / 60)

        self.assertEqual(screen.mode, "run")
        self.assertEqual(screen.loaded_title, "SMOKE TEST")
        self.assertTrue(any(pixel for row in screen.chip8.framebuffer for pixel in row))

    def test_rom_directory_is_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            previous = EmulationScreen.rom_dir
            try:
                EmulationScreen.rom_dir = previous.__class__(temp_dir)
                (EmulationScreen.rom_dir / "pong.ch8").write_bytes(bytes.fromhex("00E0 1200"))
                (EmulationScreen.rom_dir / "pong.txt").write_text("Use keys 7 and 4.", encoding="utf-8")

                screen = self.make_screen()

                self.assertEqual([cart.title for cart in screen.cartridges], ["SMOKE TEST", "PONG"])
                self.assertEqual(screen.cartridges[1].notes, "Use keys 7 and 4.")
            finally:
                EmulationScreen.rom_dir = previous

    def test_nested_archive_metadata_is_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            previous = EmulationScreen.rom_dir
            try:
                EmulationScreen.rom_dir = previous.__class__(temp_dir)
                archive = EmulationScreen.rom_dir / "archive"
                roms = archive / "roms"
                roms.mkdir(parents=True)
                (roms / "garden.ch8").write_bytes(bytes.fromhex("00E0 1200"))
                src = archive / "src" / "garden"
                src.mkdir(parents=True)
                Image.new("RGB", (64, 32), "white").save(src / "garden.gif")
                (archive / "authors.json").write_text(
                    json.dumps({"JaneDev": {"url": "https://example.test/jane"}}),
                    encoding="utf-8",
                )
                (archive / "programs.json").write_text(
                    json.dumps(
                        {
                            "garden": {
                                "title": "Ordinary Idle Garden",
                                "authors": ["JaneDev"],
                                "images": ["garden.gif"],
                                "desc": "A calm plant simulation.",
                                "event": "Octojam",
                                "release": "2020-01-02",
                                "platform": "chip8",
                                "options": {"tickrate": 30},
                            }
                        }
                    ),
                    encoding="utf-8",
                )

                screen = self.make_screen()
                cart = screen.cartridges[1]
                lines = screen._detail_lines(cart)

                self.assertEqual(cart.title, "ORDINARY IDLE GARDEN")
                self.assertEqual(cart.notes, "A calm plant simulation.")
                self.assertEqual(cart.preview_path, src / "garden.gif")
                self.assertTrue(any("JaneDev" in line for line in lines))
                self.assertTrue(any("30hz" in line for line in lines))
            finally:
                EmulationScreen.rom_dir = previous

    def test_launch_configures_platform_from_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            previous = EmulationScreen.rom_dir
            try:
                EmulationScreen.rom_dir = previous.__class__(temp_dir)
                archive = EmulationScreen.rom_dir / "archive"
                roms = archive / "roms"
                roms.mkdir(parents=True)
                (roms / "wide.ch8").write_bytes(bytes.fromhex("00FF 1202"))
                (archive / "authors.json").write_text("{}", encoding="utf-8")
                (archive / "programs.json").write_text(
                    json.dumps({"wide": {"title": "Wide Mode", "authors": [], "desc": "", "platform": "schip"}}),
                    encoding="utf-8",
                )

                screen = self.make_screen()
                screen.selection = 1
                screen._load_selection()

                self.assertEqual(screen.chip8.platform, "schip")
                self.assertEqual((screen.chip8.width, screen.chip8.height), (128, 64))
            finally:
                EmulationScreen.rom_dir = previous

    def test_detail_mode_uses_matching_notes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            previous = EmulationScreen.rom_dir
            try:
                EmulationScreen.rom_dir = previous.__class__(temp_dir)
                (EmulationScreen.rom_dir / "tetris.ch8").write_bytes(bytes.fromhex("00E0 1200"))
                (EmulationScreen.rom_dir / "tetris.txt").write_text(
                    "The 4 key is left rotate, 5 is left move, and 6 is right move.",
                    encoding="utf-8",
                )
                screen = self.make_screen()
                screen.selection = 1

                screen._open_detail()
                lines = screen._detail_lines(screen.cartridges[screen.selection])

                self.assertEqual(screen.mode, "detail")
                self.assertTrue(any("left rotate" in line for line in lines))
            finally:
                EmulationScreen.rom_dir = previous

    def test_run_mode_uses_standard_qwerty_chip8_grid(self) -> None:
        screen = self.make_screen()
        screen._load_selection()

        expected = {
            "1": 0x1,
            "2": 0x2,
            "3": 0x3,
            "4": 0xC,
            "q": 0x4,
            "w": 0x5,
            "e": 0x6,
            "r": 0xD,
            "a": 0x7,
            "s": 0x8,
            "d": 0x9,
            "f": 0xE,
            "z": 0xA,
            "x": 0x0,
            "c": 0xB,
            "v": 0xF,
        }
        for key, chip8_key in expected.items():
            with self.subTest(key=key):
                screen._clear_keys()
                handled = screen.on_keyboard_event(KeyboardEvent(key=key, raw_key=f"KEY_{key.upper()}"))

                self.assertTrue(handled)
                self.assertEqual(screen.mode, "run")
                self.assertTrue(screen.chip8.keys[chip8_key])

    def test_run_mode_uses_shift_hex_fallback(self) -> None:
        screen = self.make_screen()
        screen._load_selection()

        screen.on_keyboard_event(KeyboardEvent(key="7", raw_key="KEY_7", shift=True))
        screen.on_keyboard_event(KeyboardEvent(key="a", raw_key="KEY_A", text="A", shift=True))
        screen.on_keyboard_event(KeyboardEvent(key="$", raw_key="Digit4", text="$", shift=True))

        self.assertTrue(screen.chip8.keys[0x7])
        self.assertTrue(screen.chip8.keys[0xA])
        self.assertTrue(screen.chip8.keys[0x4])

    def test_run_mode_uses_numpad_digits_as_direct_hex_fallback(self) -> None:
        screen = self.make_screen()
        screen._load_selection()

        screen.on_keyboard_event(KeyboardEvent(key="4", raw_key="KEY_KP4"))

        self.assertTrue(screen.chip8.keys[0x4])

    def test_space_maps_to_center_key(self) -> None:
        screen = self.make_screen()
        screen._load_selection()

        screen.on_keyboard_event(KeyboardEvent(key=" ", raw_key="KEY_SPACE", text=" "))
        screen.on_keyboard_event(KeyboardEvent(key="enter", raw_key="KEY_ENTER"))

        self.assertTrue(screen.chip8.keys[0x5])

    def test_arrow_keys_map_to_common_chip8_direction_cluster(self) -> None:
        screen = self.make_screen()
        screen._load_selection()

        expected = {"up": 0x2, "left": 0x4, "right": 0x6, "down": 0x8}
        for key, chip8_key in expected.items():
            with self.subTest(key=key):
                screen._clear_keys()
                screen.on_keyboard_event(KeyboardEvent(key=key, raw_key=f"KEY_{key.upper()}"))

                self.assertTrue(screen.chip8.keys[chip8_key])

    def test_run_mode_escape_requires_hold(self) -> None:
        screen = self.make_screen()
        screen._load_selection()

        screen.on_keyboard_event(KeyboardEvent(key="escape", raw_key="KEY_ESC"))
        screen.on_keyboard_event(KeyboardEvent(key="escape", raw_key="KEY_ESC", event_type="release"))
        screen.update(1.0)

        self.assertEqual(screen.mode, "run")
        self.assertIsNone(screen.escape_armed_at)

    def test_run_mode_escape_hold_clears_game_and_suppresses_held_keys(self) -> None:
        screen = self.make_screen()
        screen._load_selection()
        screen.on_keyboard_event(KeyboardEvent(key="w", raw_key="KEY_W"))

        screen.escape_armed_at = time.monotonic() - screen.escape_hold_seconds
        screen.update(0.01)

        self.assertEqual(screen.mode, "select")
        self.assertFalse(any(screen.chip8.keys))
        self.assertTrue(screen.on_keyboard_event(KeyboardEvent(key="w", raw_key="KEY_W")))
        self.assertEqual(screen.selection, 0)
        self.assertTrue(screen.on_keyboard_event(KeyboardEvent(key="w", raw_key="KEY_W", event_type="release")))
        self.assertFalse(screen.suppressed_runtime_key_ids)

    def test_run_mode_long_y_returns_to_picker(self) -> None:
        screen = self.make_screen()
        screen._load_selection()

        self.assertTrue(screen.on_button("Y", True))

        self.assertEqual(screen.mode, "select")

    def test_deactivate_stops_running_cart(self) -> None:
        screen = self.make_screen()
        screen._load_selection()
        screen.on_keyboard_event(KeyboardEvent(key="s", raw_key="KEY_S"))

        screen.on_deactivate()

        self.assertEqual(screen.mode, "select")
        self.assertFalse(any(screen.chip8.keys))


if __name__ == "__main__":
    unittest.main()
