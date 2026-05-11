from __future__ import annotations

from pathlib import Path
import unittest

from altoids.chip8 import Chip8


class Chip8CoreTest(unittest.TestCase):
    def test_smoke_rom_draws_sprite(self) -> None:
        chip8 = Chip8()
        program = bytes.fromhex(
            "00E0"  # clear screen
            "6004"  # V0 = 4
            "6103"  # V1 = 3
            "A300"  # I = 0x300
            "D015"  # draw 5-byte sprite at V0,V1
            "120A"  # loop forever
        )
        sprite = bytes([0xF0, 0x90, 0x90, 0x90, 0xF0])

        chip8.load_rom(program)
        chip8.memory[0x300 : 0x300 + len(sprite)] = sprite
        chip8.run_steps(5)
        chip8.save_image(Path("artifacts/chip8-smoke.png"), scale=4)

        expected_lit = {
            (4, 3),
            (5, 3),
            (6, 3),
            (7, 3),
            (4, 4),
            (7, 4),
            (4, 5),
            (7, 5),
            (4, 6),
            (7, 6),
            (4, 7),
            (5, 7),
            (6, 7),
            (7, 7),
        }
        lit = {
            (x, y)
            for y, row in enumerate(chip8.framebuffer)
            for x, pixel in enumerate(row)
            if pixel
        }

        self.assertEqual(lit, expected_lit)
        self.assertEqual(chip8.v[0xF], 0)
        self.assertEqual(chip8.pc, 0x20A)

    def test_sprite_collision_sets_vf(self) -> None:
        chip8 = Chip8()
        program = bytes.fromhex(
            "6004"
            "6103"
            "A300"
            "D015"
            "D015"
        )
        sprite = bytes([0xF0, 0x90, 0x90, 0x90, 0xF0])

        chip8.load_rom(program)
        chip8.memory[0x300 : 0x300 + len(sprite)] = sprite
        chip8.run_steps(5)

        self.assertEqual(chip8.v[0xF], 1)
        self.assertFalse(any(pixel for row in chip8.framebuffer for pixel in row))

    def test_schip_high_resolution_and_scroll(self) -> None:
        chip8 = Chip8()
        chip8.reset(platform="schip")
        program = bytes.fromhex(
            "00FF"  # high resolution
            "6001"  # V0 = 1
            "6101"  # V1 = 1
            "A300"  # I = sprite
            "D010"  # 16x16 sprite
            "00C1"  # scroll down 1
            "00FB"  # scroll right 4
        )
        sprite = bytes([0x80, 0x00] + [0x00, 0x00] * 15)

        chip8.load_rom(program)
        chip8.memory[0x300 : 0x300 + len(sprite)] = sprite
        chip8.run_steps(7)

        self.assertEqual((chip8.width, chip8.height), (128, 64))
        self.assertTrue(chip8.framebuffer[2][5])

    def test_xochip_long_i_loads_16_bit_address(self) -> None:
        chip8 = Chip8()
        chip8.reset(platform="xochip")
        chip8.load_rom(bytes.fromhex("F0 00 40 00"))

        chip8.step()

        self.assertEqual(chip8.i, 0x4000)

    def test_schip_rpl_flags_round_trip(self) -> None:
        chip8 = Chip8()
        chip8.reset(platform="schip")
        chip8.v[0] = 11
        chip8.v[1] = 22
        chip8.load_rom(bytes.fromhex("F175 6000 6100 F185"))

        chip8.run_steps(4)

        self.assertEqual(chip8.v[:2], [11, 22])


if __name__ == "__main__":
    unittest.main()
