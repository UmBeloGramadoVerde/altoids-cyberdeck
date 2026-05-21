from __future__ import annotations

import json
from types import SimpleNamespace
import tempfile
import unittest

from PIL import ImageFont

from altoids.input_keyboard import KeyboardEvent
from altoids.ui.base import ScreenContext
from altoids.ui.emulation import EmulationScreen


class EmulationScreenTest(unittest.TestCase):
    def make_screen(self) -> EmulationScreen:
        app = SimpleNamespace(
            config=SimpleNamespace(display=SimpleNamespace(width=320, height=240)),
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

    def test_run_mode_q_is_ignored_not_exit_or_gameplay(self) -> None:
        screen = self.make_screen()
        screen._load_selection()

        handled = screen.on_keyboard_event(KeyboardEvent(key="q", raw_key="KEY_Q"))

        self.assertTrue(handled)
        self.assertEqual(screen.mode, "run")
        self.assertFalse(any(screen.chip8.keys))

    def test_run_mode_uses_direct_hex_keys(self) -> None:
        screen = self.make_screen()
        screen._load_selection()

        screen.on_keyboard_event(KeyboardEvent(key="7", raw_key="KEY_7"))
        screen.on_keyboard_event(KeyboardEvent(key="a", raw_key="KEY_A"))

        self.assertTrue(screen.chip8.keys[0x7])
        self.assertTrue(screen.chip8.keys[0xA])

    def test_chicken_scratch_space_maps_to_center_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            previous = EmulationScreen.rom_dir
            try:
                EmulationScreen.rom_dir = previous.__class__(temp_dir)
                archive = EmulationScreen.rom_dir / "archive"
                roms = archive / "roms"
                roms.mkdir(parents=True)
                (roms / "chickenScratch.ch8").write_bytes(bytes.fromhex("00E0 1200"))
                (archive / "authors.json").write_text("{}", encoding="utf-8")
                (archive / "programs.json").write_text(
                    json.dumps({"chickenScratch": {"title": "Chicken Scratch", "authors": [], "platform": "xochip"}}),
                    encoding="utf-8",
                )
                screen = self.make_screen()
                screen.selection = 1
                screen._load_selection()

                screen.on_keyboard_event(KeyboardEvent(key=" ", raw_key="KEY_SPACE", text=" "))

                self.assertTrue(screen.chip8.keys[0x5])
            finally:
                EmulationScreen.rom_dir = previous

    def test_space_is_not_global_gameplay_key(self) -> None:
        screen = self.make_screen()
        screen._load_selection()

        screen.on_keyboard_event(KeyboardEvent(key=" ", raw_key="KEY_SPACE", text=" "))

        self.assertFalse(any(screen.chip8.keys))

    def test_run_mode_escape_requires_hold(self) -> None:
        screen = self.make_screen()
        screen._load_selection()

        screen.on_keyboard_event(KeyboardEvent(key="escape", raw_key="KEY_ESC"))
        screen.on_keyboard_event(KeyboardEvent(key="escape", raw_key="KEY_ESC", event_type="release"))
        screen.update(1.0)

        self.assertEqual(screen.mode, "run")
        self.assertIsNone(screen.escape_armed_at)

    def test_run_mode_long_y_returns_to_picker(self) -> None:
        screen = self.make_screen()
        screen._load_selection()

        self.assertTrue(screen.on_button("Y", True))

        self.assertEqual(screen.mode, "select")


if __name__ == "__main__":
    unittest.main()
