from __future__ import annotations

import io
import json
import queue
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from PIL import Image

from .input_buttons import ButtonEvent
from .input_keyboard import KeyboardEvent


@dataclass(slots=True)
class WebViewerEvents:
    button_events: list[ButtonEvent]
    keyboard_events: list[KeyboardEvent]


HTML_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Altoids Web Viewer</title>
  <style>
    :root { color-scheme: dark; }
    body { margin: 0; padding: 16px; background: #111; color: #ddd; font-family: monospace; }
    .wrap { display: grid; gap: 16px; max-width: 980px; margin: 0 auto; }
    .screen { image-rendering: pixelated; width: min(96vw, 640px); border: 2px solid #333; background: #000; }
    .row { display: flex; gap: 8px; flex-wrap: wrap; }
    button { background: #1d1d1d; color: #ddd; border: 1px solid #444; border-radius: 6px; padding: 10px 12px; cursor: pointer; }
    button:hover { border-color: #00ffaa; }
    .hint { color: #9a9a9a; line-height: 1.5; }
    code { color: #00ffaa; }
  </style>
</head>
<body>
  <div class="wrap">
    <div><img id="screen" class="screen" src="/frame?ts=0" alt="Altoids frame"></div>
    <div class="row">
      <button data-button="A">A</button><button data-button="B">B</button><button data-button="X">X</button><button data-button="Y">Y</button>
      <button data-button="A" data-long="1">A long</button><button data-button="B" data-long="1">B long</button><button data-button="X" data-long="1">X long</button><button data-button="Y" data-long="1">Y long</button>
    </div>
    <div class="hint">
      Browser keyboard forwarding is enabled while this page is focused.<br>
      Useful mappings: <code>Meta</code> enters command mode. Letters type into tmux on the terminal screen.
    </div>
  </div>
  <script>
    const screen = document.getElementById("screen");
    const refresh = () => { screen.src = "/frame?ts=" + Date.now(); };
    const sendKey = async (event, eventType) => {
      const payload = {key: event.key, code: event.code, ctrl: event.ctrlKey, alt: event.altKey, shift: event.shiftKey, meta: event.metaKey, event_type: eventType};
      const response = await fetch("/key", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
      if (response.ok) {
        event.preventDefault();
        refresh();
      }
    };
    setInterval(refresh, 80);
    document.querySelectorAll("button[data-button]").forEach((button) => {
      button.addEventListener("click", async () => {
        await fetch("/button", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({button: button.dataset.button, long_press: button.dataset.long === "1"})});
        refresh();
      });
    });
    window.addEventListener("keydown", async (event) => { await sendKey(event, "press"); });
    window.addEventListener("keyup", async (event) => { await sendKey(event, "release"); });
  </script>
</body>
</html>
"""


def _normalize_web_key(payload: dict[str, object]) -> KeyboardEvent | None:
    key = str(payload.get("key", ""))
    code = str(payload.get("code", ""))
    ctrl = bool(payload.get("ctrl"))
    alt = bool(payload.get("alt"))
    shift = bool(payload.get("shift"))
    meta = bool(payload.get("meta"))
    event_type = "release" if payload.get("event_type") == "release" else "press"

    if key in {"Meta", "OS"} or code in {"MetaLeft", "MetaRight"}:
        return KeyboardEvent(key="meta", raw_key=code or key, ctrl=ctrl, alt=alt, shift=shift, event_type=event_type)

    named = {
        "Enter": "enter",
        "Backspace": "backspace",
        "Tab": "tab",
        "Escape": "escape",
        "ArrowUp": "up",
        "ArrowDown": "down",
        "ArrowLeft": "left",
        "ArrowRight": "right",
        "Home": "home",
        "End": "end",
        "PageUp": "pageup",
        "PageDown": "pagedown",
        "Delete": "delete",
        "Insert": "insert",
    }
    if key in named:
        return KeyboardEvent(key=named[key], raw_key=code or key, ctrl=ctrl, alt=alt, shift=shift, event_type=event_type)
    if key == " ":
        text = " " if event_type == "press" else ""
        return KeyboardEvent(key=" ", raw_key=code or "Space", text=text, ctrl=ctrl, alt=alt, shift=shift, event_type=event_type)
    if len(key) == 1 and key.isprintable():
        logical = key.lower() if key.isalpha() else key
        text = key if event_type == "press" else ""
        return KeyboardEvent(key=logical, raw_key=code or key, text=text, ctrl=ctrl, alt=alt, shift=shift, event_type=event_type)
    return None


class WebViewer:
    def __init__(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        self.host = host
        self.port = port
        self._lock = threading.Lock()
        self._frame_bytes = b""
        self._events: queue.SimpleQueue[object] = queue.SimpleQueue()
        self._server = ThreadingHTTPServer((host, port), self._handler_factory())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def update(self, image: Image.Image) -> None:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        with self._lock:
            self._frame_bytes = buffer.getvalue()

    def poll_events(self) -> WebViewerEvents:
        button_events: list[ButtonEvent] = []
        keyboard_events: list[KeyboardEvent] = []
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            if isinstance(event, ButtonEvent):
                button_events.append(event)
            else:
                keyboard_events.append(event)
        return WebViewerEvents(button_events=button_events, keyboard_events=keyboard_events)

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def _handler_factory(self) -> type[BaseHTTPRequestHandler]:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path == "/":
                    self._send_html(HTML_PAGE)
                    return
                if parsed.path == "/frame":
                    outer._send_frame(self)
                    return
                if parsed.path == "/healthz":
                    self._send_text("ok")
                    return
                self.send_error(HTTPStatus.NOT_FOUND)

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                body = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
                if parsed.path == "/button":
                    payload = json.loads(body or b"{}")
                    button = str(payload.get("button", ""))
                    if button in {"A", "B", "X", "Y"}:
                        outer._events.put(ButtonEvent(button=button, long_press=bool(payload.get("long_press"))))
                        self._send_text("ok")
                        return
                    self.send_error(HTTPStatus.BAD_REQUEST, "invalid button")
                    return
                if parsed.path == "/key":
                    payload = json.loads(body or b"{}")
                    event = _normalize_web_key(payload)
                    if event is None:
                        self.send_error(HTTPStatus.BAD_REQUEST, "unsupported key")
                        return
                    outer._events.put(event)
                    self._send_text("ok")
                    return
                self.send_error(HTTPStatus.NOT_FOUND)

            def log_message(self, format: str, *args) -> None:  # noqa: A003
                return

            def _send_html(self, body: str) -> None:
                data = body.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _send_text(self, body: str) -> None:
                data = body.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        return Handler

    def _send_frame(self, handler: BaseHTTPRequestHandler) -> None:
        with self._lock:
            frame = self._frame_bytes
        if not frame:
            placeholder = Image.new("RGB", (320, 240), "#0D0D0D")
            buffer = io.BytesIO()
            placeholder.save(buffer, format="PNG")
            frame = buffer.getvalue()
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "image/png")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Content-Length", str(len(frame)))
        handler.end_headers()
        handler.wfile.write(frame)
