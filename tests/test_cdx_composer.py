from __future__ import annotations

import os
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

import altoids.cdx as cdx
from altoids.cdx import AppServerClient, CdxApp, CdxState


class FakeScreen:
    def __init__(self, height: int = 5, width: int = 20) -> None:
        self.height = height
        self.width = width
        self.writes: list[tuple[int, int, str, int, int]] = []

    def getmaxyx(self) -> tuple[int, int]:
        return self.height, self.width

    def addnstr(self, y: int, x: int, text: str, n: int, attr: int) -> None:
        self.writes.append((y, x, text[:n], n, attr))


class CdxComposerRenderingTest(unittest.TestCase):
    def test_fill_pads_composer_line_without_repeating_text(self) -> None:
        original_color_attr = CdxApp._color_attr
        CdxApp._color_attr = staticmethod(lambda color: 0)
        try:
            app = CdxApp.__new__(CdxApp)
            screen = FakeScreen(width=20)

            app._draw_line(screen, 0, 0, "> hello", "accent", fill=True)
        finally:
            CdxApp._color_attr = original_color_attr

        self.assertEqual(screen.writes, [(0, 0, "> hello" + " " * 12, 19, 0)])

    def test_raw_arrow_sequences_move_within_multiline_composer(self) -> None:
        app = CdxApp.__new__(CdxApp)
        app.state = CdxState(screen_width=40, session_focus="composer", composer="ab\ncd", composer_cursor=5)

        app._handle_composer_key("\x1b[A")
        self.assertEqual(app.state.composer, "ab\ncd")
        self.assertEqual(app.state.composer_cursor, 2)

        app._handle_composer_key("\x1b[B")
        self.assertEqual(app.state.composer, "ab\ncd")
        self.assertEqual(app.state.composer_cursor, 5)


class CdxStartupTest(unittest.TestCase):
    def test_launches_codex_executable_directly(self) -> None:
        self.assertEqual(
            AppServerClient._launch_command("/usr/local/bin/codex"),
            ["/usr/local/bin/codex", "app-server", "--listen", "stdio://"],
        )

    def test_uses_node_for_non_executable_javascript_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            node = root / "node"
            codex_js = root / "codex.js"
            node.write_text("#!/bin/sh\n")
            codex_js.write_text("console.log('codex')\n")
            os.chmod(node, 0o755)

            self.assertEqual(
                AppServerClient._launch_command(str(codex_js)),
                [str(node), str(codex_js), "app-server", "--listen", "stdio://"],
            )

    def test_new_flag_starts_thread_without_loading_recent_threads(self) -> None:
        original_client = cdx.AppServerClient
        original_resolver = CdxApp._resolve_codex_bin
        instances: list[FakeClient] = []

        class FakeClient:
            def __init__(self, *args: object, **kwargs: object) -> None:
                self.requests: list[str] = []
                instances.append(self)

            def initialize(self) -> dict[str, object]:
                return {}

            def request(self, method: str, params: dict[str, object], timeout: float = 30.0) -> dict[str, object]:
                self.requests.append(method)
                if method == "thread/start":
                    return {"thread": {"id": "thread-new", "turns": []}}
                if method == "thread/list":
                    raise AssertionError("thread/list should not be called for cdx -n")
                raise AssertionError(f"unexpected request: {method}")

            def close(self) -> None:
                pass

        cdx.AppServerClient = FakeClient
        CdxApp._resolve_codex_bin = staticmethod(lambda configured: "/usr/bin/codex")
        try:
            args = Namespace(
                cwd=None,
                codex_bin=None,
                home_override=None,
                xdg_state_home=None,
                thread_id=None,
                new=True,
            )
            app = CdxApp(args)
        finally:
            cdx.AppServerClient = original_client
            CdxApp._resolve_codex_bin = original_resolver

        self.assertEqual(instances[0].requests, ["thread/start"])
        self.assertEqual(app.state.view, "session")
        self.assertEqual(app.state.thread_id, "thread-new")

    def test_closes_app_server_when_initialize_fails(self) -> None:
        original_client = cdx.AppServerClient
        original_resolver = CdxApp._resolve_codex_bin
        instances: list[FakeFailingClient] = []

        class FakeFailingClient:
            def __init__(self, *args: object, **kwargs: object) -> None:
                self.closed = False
                instances.append(self)

            def initialize(self) -> dict[str, object]:
                raise RuntimeError("initialize timed out")

            def close(self) -> None:
                self.closed = True

        cdx.AppServerClient = FakeFailingClient
        CdxApp._resolve_codex_bin = staticmethod(lambda configured: "/usr/bin/codex")
        try:
            args = Namespace(cwd=None, codex_bin=None, home_override=None, xdg_state_home=None, thread_id=None, new=False)
            with self.assertRaisesRegex(RuntimeError, "initialize timed out"):
                CdxApp(args)
        finally:
            cdx.AppServerClient = original_client
            CdxApp._resolve_codex_bin = original_resolver

        self.assertEqual(len(instances), 1)
        self.assertTrue(instances[0].closed)


if __name__ == "__main__":
    unittest.main()
