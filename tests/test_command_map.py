from __future__ import annotations

from types import SimpleNamespace
import unittest

from altoids.app import AltoidsApp
from altoids.input_keyboard import KeyboardEvent


class FakeTmux:
    def __init__(self) -> None:
        self.actions: list[tuple[str, int | None]] = []

    def select_window(self, window: int) -> None:
        self.actions.append(("select", window))

    def select_previous_window(self) -> None:
        self.actions.append(("previous", None))

    def select_next_window(self) -> None:
        self.actions.append(("next", None))

    def create_window(self) -> None:
        self.actions.append(("create", None))

    def close_active_window(self) -> None:
        self.actions.append(("close", None))


class CommandMapTest(unittest.TestCase):
    def make_app(self, active_screen: str = "home") -> AltoidsApp:
        app = AltoidsApp.__new__(AltoidsApp)
        app.active_screen_name = active_screen
        app.screens = {
            "home": object(),
            "term": object(),
            "system": object(),
            "emu": object(),
            "tinscope": object(),
        }
        app.accents = SimpleNamespace(trigger=lambda cue: None)
        app.tmux = FakeTmux()
        app.command_mode_deadline = 0.0
        app.help_visible = False
        app.help_page_index = 0
        app.help_page_index_by_context = {}
        app.help_scroll_offsets = {}
        return app

    def command(self, app: AltoidsApp, key: str) -> bool:
        return app._handle_command_mode_key(KeyboardEvent(key=key, raw_key=f"KEY_{key.upper()}"))

    def test_global_command_keys_route_to_expected_pages(self) -> None:
        app = self.make_app()

        self.assertTrue(self.command(app, "t"))
        self.assertEqual(app.active_screen_name, "term")
        self.assertTrue(self.command(app, "s"))
        self.assertEqual(app.active_screen_name, "system")
        self.assertTrue(self.command(app, "g"))
        self.assertEqual(app.active_screen_name, "emu")

    def test_legacy_game_alias_is_removed(self) -> None:
        app = self.make_app()

        self.assertFalse(self.command(app, "v"))
        self.assertEqual(app.active_screen_name, "home")

    def test_tmux_window_digits_are_terminal_context_only(self) -> None:
        app = self.make_app()

        self.assertFalse(self.command(app, "1"))
        self.assertEqual(app.tmux.actions, [])

        app.active_screen_name = "term"
        self.assertTrue(self.command(app, "1"))
        self.assertEqual(app.tmux.actions, [("select", 1)])

    def test_command_preview_comes_from_available_specs(self) -> None:
        app = self.make_app(active_screen="term")

        hints = app._command_mode_hints()

        for expected in ["H", "Q", "T", "S", "G", "R", "[", "]", "N", "K", "0-9"]:
            self.assertIn(expected, hints)
        self.assertNotIn("V", hints)
        self.assertNotIn("W", hints)
        self.assertNotIn("E", hints)

    def test_help_rows_use_current_command_map(self) -> None:
        app = self.make_app()
        global_rows = dict(app._command_help_rows("GLOBAL"))
        emu_rows = dict(app._command_help_rows("EMU"))

        self.assertEqual(global_rows["terminal"], "CMD+T")
        self.assertEqual(global_rows["system/settings"], "CMD+S")
        self.assertEqual(emu_rows["games"], "CMD+G")
        self.assertNotIn("CMD+V", global_rows.values())
        self.assertNotIn("CMD+V", emu_rows.values())


if __name__ == "__main__":
    unittest.main()
