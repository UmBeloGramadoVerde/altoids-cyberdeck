from __future__ import annotations

from pathlib import Path
import json
import os
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from altoids.input_keyboard import KeyboardEvent
from altoids.config import AltoidsConfig
from altoids.notes import NoteStore
from altoids.ui.notes import NotesScreen


class FailingNoteStore(NoteStore):
    def _save(self, notes):  # type: ignore[no-untyped-def]
        raise OSError("read-only storage")


class CrashingNoteStore(NoteStore):
    def add(self, text: str, *, source: str = "typed"):
        raise RuntimeError("boom")


class NotesTest(unittest.TestCase):
    def test_store_persists_notes_newest_first(self) -> None:
        with TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp))

            store.add(" first idea ")
            store.add("second   idea", source="voice")
            reloaded = NoteStore(Path(tmp)).list_notes()

        self.assertEqual([note.text for note in reloaded], ["second idea", "first idea"])
        self.assertEqual(reloaded[0].source, "voice")

    def test_store_tolerates_malformed_note_fields(self) -> None:
        with TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp))
            store.path.parent.mkdir(parents=True)
            store.path.write_text(json.dumps([
                {"text": "bad source", "created_at": "NaN", "source": ["voice"]},
                {"text": "good", "created_at": 1, "source": "voice"},
            ]))

            notes = store.list_notes()

        self.assertEqual([note.text for note in notes], ["bad source", "good"])
        self.assertEqual(notes[0].source, "typed")

    def test_store_recovers_from_non_utf8_notes_file_on_save(self) -> None:
        with TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp))
            store.path.parent.mkdir(parents=True)
            store.path.write_bytes(b"\xff\xfe not json")

            note = store.add("new note")
            reloaded = NoteStore(Path(tmp)).list_notes()

        self.assertIsNotNone(note)
        self.assertEqual([saved.text for saved in reloaded], ["new note"])

    def test_store_update_replaces_existing_note_in_place(self) -> None:
        with TemporaryDirectory() as tmp:
            store = NoteStore(Path(tmp))
            first = store.add("first")
            second = store.add("second", source="voice")

            updated = store.update(second, "second updated")
            reloaded = NoteStore(Path(tmp)).list_notes()

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIsNotNone(updated)
        self.assertEqual([note.text for note in reloaded], ["second updated", "first"])
        self.assertEqual(reloaded[0].created_at, second.created_at)
        self.assertEqual(reloaded[0].source, "voice")

    def test_typed_input_saves_draft(self) -> None:
        with TemporaryDirectory() as tmp:
            app = self._fake_app(Path(tmp))
            screen = NotesScreen(SimpleNamespace(app=app))

            for char in "ship it":
                screen.on_keyboard_event(KeyboardEvent(key=char, raw_key="", text=char))
            screen.on_keyboard_event(KeyboardEvent(key="enter", raw_key="KEY_ENTER"))

            notes = app.notes.list_notes()

        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].text, "ship it")
        self.assertEqual(screen.draft, "")

    def test_config_state_dir_defaults_to_runtime_folder_in_repo(self) -> None:
        with TemporaryDirectory() as tmp:
            config = AltoidsConfig(root_dir=Path(tmp))

        self.assertEqual(config.state_dir, Path(tmp) / ".runtime")

    def test_config_state_dir_prefers_runtime_state_env(self) -> None:
        with TemporaryDirectory() as tmp:
            original_state = os.environ.get("ALTOIDS_RUNTIME_STATE")
            original_root = os.environ.get("ALTOIDS_RUNTIME_ROOT")
            os.environ["ALTOIDS_RUNTIME_STATE"] = f"{tmp}/persistent-state"
            os.environ.pop("ALTOIDS_RUNTIME_ROOT", None)
            try:
                config = AltoidsConfig(root_dir=Path("/repo"))
                state_dir = config.state_dir
            finally:
                if original_state is None:
                    os.environ.pop("ALTOIDS_RUNTIME_STATE", None)
                else:
                    os.environ["ALTOIDS_RUNTIME_STATE"] = original_state
                if original_root is None:
                    os.environ.pop("ALTOIDS_RUNTIME_ROOT", None)
                else:
                    os.environ["ALTOIDS_RUNTIME_ROOT"] = original_root

        self.assertEqual(state_dir, Path(tmp) / "persistent-state")

    def test_voice_text_saves_immediately_when_draft_is_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            app = self._fake_app(Path(tmp))
            screen = NotesScreen(SimpleNamespace(app=app))

            self.assertTrue(screen.insert_voice_text("remember the antenna sketch"))
            notes = app.notes.list_notes()

        self.assertEqual(notes[0].text, "remember the antenna sketch")
        self.assertEqual(notes[0].source, "voice")
        self.assertEqual(screen.status_line, "VOICE SAVED")

    def test_voice_text_appends_to_existing_draft(self) -> None:
        with TemporaryDirectory() as tmp:
            app = self._fake_app(Path(tmp))
            screen = NotesScreen(SimpleNamespace(app=app))
            screen.draft = "idea:"

            self.assertTrue(screen.insert_voice_text("make it tactile"))

        self.assertEqual(screen.draft, "idea: make it tactile")
        self.assertEqual(app.notes.list_notes(), [])

    def test_typing_with_selected_note_edits_original_instead_of_creating_new_one(self) -> None:
        with TemporaryDirectory() as tmp:
            app = self._fake_app(Path(tmp))
            original = app.notes.add("ship it")
            screen = NotesScreen(SimpleNamespace(app=app))

            screen.on_keyboard_event(KeyboardEvent(key="backspace", raw_key="KEY_BACKSPACE"))
            for char in " now":
                screen.on_keyboard_event(KeyboardEvent(key=char, raw_key="", text=char))
            screen.on_keyboard_event(KeyboardEvent(key="enter", raw_key="KEY_ENTER"))
            notes = app.notes.list_notes()

        self.assertIsNotNone(original)
        self.assertEqual([note.text for note in notes], ["ship it now"])
        self.assertEqual(notes[0].created_at, original.created_at)
        self.assertEqual(screen.status_line, "UPDATED DROP")

    def test_save_failure_does_not_clear_typed_draft(self) -> None:
        app = SimpleNamespace(notes=FailingNoteStore(Path("/tmp")))
        screen = NotesScreen(SimpleNamespace(app=app))
        screen.draft = "keep this"

        screen.on_keyboard_event(KeyboardEvent(key="enter", raw_key="KEY_ENTER"))

        self.assertEqual(screen.draft, "keep this")
        self.assertIn("SAVE FAILED", screen.status_line)
        self.assertEqual(app.notes.list_notes(), [])

    def test_voice_save_failure_keeps_transcript_as_draft(self) -> None:
        app = SimpleNamespace(notes=FailingNoteStore(Path("/tmp")))
        screen = NotesScreen(SimpleNamespace(app=app))

        self.assertTrue(screen.insert_voice_text("keep voice text"))

        self.assertEqual(screen.draft, "keep voice text")
        self.assertEqual(screen.source, "voice")
        self.assertIn("SAVE FAILED", screen.status_line)
        self.assertEqual(app.notes.list_notes(), [])

    def test_unexpected_store_crash_does_not_clear_draft(self) -> None:
        app = SimpleNamespace(notes=CrashingNoteStore(Path("/tmp")))
        screen = NotesScreen(SimpleNamespace(app=app))
        screen.draft = "keep this too"

        screen.on_keyboard_event(KeyboardEvent(key="enter", raw_key="KEY_ENTER"))

        self.assertEqual(screen.draft, "keep this too")
        self.assertIn("SAVE FAILED", screen.status_line)
        self.assertEqual(app.notes.last_error, "boom")

    def test_display_text_is_ascii_safe(self) -> None:
        rendered = NotesScreen._display_text("curly’s note — emoji 😀")

        self.assertEqual(rendered, "curly's note - emoji ?")

    @staticmethod
    def _fake_app(root: Path):
        return SimpleNamespace(notes=NoteStore(root))


if __name__ == "__main__":
    unittest.main()
