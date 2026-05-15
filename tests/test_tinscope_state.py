from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from altoids.ui.tinscope import TinScopeScreen


class TinScopeStateTest(unittest.TestCase):
    def test_local_default_state_path_stays_in_repo_runtime(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(TinScopeScreen._resolve_state_file(), Path(".runtime/tinscope/state.json"))

    def test_runtime_state_env_uses_writable_runtime_state(self) -> None:
        with patch.dict(
            os.environ,
            {"ALTOIDS_RUNTIME_STATE": "/tmp/altoids-state"},
            clear=True,
        ):
            self.assertEqual(TinScopeScreen._resolve_state_file(), Path("/tmp/altoids-state/tinscope/state.json"))

    def test_file_like_state_dir_is_treated_as_parent_directory(self) -> None:
        with patch.dict(os.environ, {"TINSCOPE_STATE_DIR": "runtime/tinscope/.state.json"}, clear=True):
            self.assertEqual(TinScopeScreen._resolve_state_file(), Path("runtime/tinscope/state.json"))

    def test_atomic_write_uses_tmp_suffix_without_json_json_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "state.json"

            TinScopeScreen._atomic_write_json(state_file, {"ok": True})

            self.assertEqual(state_file.read_text(encoding="utf-8"), '{\n  "ok": true\n}')
            self.assertFalse((Path(temp_dir) / ".state.json.json").exists())


if __name__ == "__main__":
    unittest.main()
