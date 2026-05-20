from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import time
from typing import Any


@dataclass(frozen=True, slots=True)
class QuickNote:
    created_at: float
    text: str
    source: str = "typed"

    @property
    def created_label(self) -> str:
        return time.strftime("%H:%M", time.localtime(self.created_at))

    @property
    def date_label(self) -> str:
        return time.strftime("%b %d", time.localtime(self.created_at)).upper()


class NoteStore:
    def __init__(self, root_dir: Path, max_notes: int = 200) -> None:
        self.path = root_dir / ".runtime" / "notes" / "quick-notes.json"
        self.max_notes = max(1, max_notes)
        self._notes: list[QuickNote] | None = None
        self.last_error = ""

    def list_notes(self) -> list[QuickNote]:
        if self._notes is None:
            self._notes = self._load()
        return list(self._notes)

    def add(self, text: str, *, source: str = "typed") -> QuickNote | None:
        normalized = " ".join(text.strip().split())
        if not normalized:
            return None
        note = QuickNote(created_at=time.time(), text=normalized, source=source)
        current_notes = self.list_notes()
        notes = [note, *current_notes][: self.max_notes]
        self.last_error = ""
        try:
            self._save(notes)
        except Exception as exc:
            self.last_error = str(exc)
            self._notes = current_notes
            return None
        self._notes = notes
        return note

    def _load(self) -> list[QuickNote]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return []
        if not isinstance(payload, list):
            return []
        notes: list[QuickNote] = []
        for item in payload:
            note = self._note_from_payload(item)
            if note is not None:
                notes.append(note)
        return notes[: self.max_notes]

    def _save(self, notes: list[QuickNote]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [
            {
                "created_at": note.created_at,
                "text": note.text,
                "source": note.source,
            }
            for note in notes[: self.max_notes]
        ]
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temp_path, self.path)

    @staticmethod
    def _note_from_payload(item: Any) -> QuickNote | None:
        if not isinstance(item, dict):
            return None
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            return None
        created_at = item.get("created_at", time.time())
        try:
            timestamp = float(created_at)
        except (TypeError, ValueError):
            timestamp = time.time()
        if not math.isfinite(timestamp):
            timestamp = time.time()
        source = item.get("source", "typed")
        if not isinstance(source, str):
            source = "typed"
        if source not in {"typed", "voice"}:
            source = "typed"
        return QuickNote(created_at=timestamp, text=text.strip(), source=source)
