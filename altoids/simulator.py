from __future__ import annotations

import queue
from dataclasses import dataclass

from PIL import Image, ImageTk

from .input_buttons import ButtonEvent
from .input_keyboard import KeyboardEvent


@dataclass(slots=True)
class SimulatorEvents:
    button_events: list[ButtonEvent]
    keyboard_events: list[KeyboardEvent]


SIM_SPECIAL_KEYS = {
    "Return": ("enter", ""),
    "BackSpace": ("backspace", ""),
    "Tab": ("tab", ""),
    "Escape": ("escape", ""),
    "Up": ("up", ""),
    "Down": ("down", ""),
    "Left": ("left", ""),
    "Right": ("right", ""),
    "Home": ("home", ""),
    "End": ("end", ""),
    "Prior": ("pageup", ""),
    "Next": ("pagedown", ""),
    "Delete": ("delete", ""),
    "Insert": ("insert", ""),
    "space": (" ", " "),
}


class SimulatorDisplay:
    def __init__(self, width: int, height: int, scale: int = 3) -> None:
        import tkinter as tk

        self.width = width
        self.height = height
        self.scale = max(1, scale)
        self._queue: queue.SimpleQueue[object] = queue.SimpleQueue()
        self._root = tk.Tk()
        self._root.title("Altoids Simulator")
        self._root.geometry(f"{width * self.scale}x{height * self.scale}")
        self._canvas = tk.Label(self._root, bg="black")
        self._canvas.pack(fill="both", expand=True)
        self._photo = None
        self._root.bind("<KeyPress>", self._on_key_press)
        self._root.bind("<Alt-KeyPress-1>", lambda _event: self._enqueue_button("A", True))
        self._root.bind("<Alt-KeyPress-2>", lambda _event: self._enqueue_button("B", True))
        self._root.bind("<Alt-KeyPress-3>", lambda _event: self._enqueue_button("X", True))
        self._root.bind("<Alt-KeyPress-4>", lambda _event: self._enqueue_button("Y", True))
        self._root.focus_force()

    def update(self, image: Image.Image) -> None:
        preview = image.resize((self.width * self.scale, self.height * self.scale), Image.Resampling.NEAREST)
        self._photo = ImageTk.PhotoImage(preview)
        self._canvas.configure(image=self._photo)
        self._root.update_idletasks()
        self._root.update()

    def set_backlight(self, value: float) -> None:
        del value

    def poll_events(self) -> SimulatorEvents:
        button_events: list[ButtonEvent] = []
        keyboard_events: list[KeyboardEvent] = []
        while True:
            try:
                event = self._queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(event, ButtonEvent):
                button_events.append(event)
            else:
                keyboard_events.append(event)
        return SimulatorEvents(button_events=button_events, keyboard_events=keyboard_events)

    def _enqueue_button(self, button: str, long_press: bool) -> None:
        self._queue.put(ButtonEvent(button=button, long_press=long_press))

    def _on_key_press(self, event) -> None:
        if event.keysym in {"1", "2", "3", "4"} and not (event.state & 0x0008):
            self._enqueue_button({"1": "A", "2": "B", "3": "X", "4": "Y"}[event.keysym], False)
            return
        if event.keysym in {"Super_L", "Super_R", "Meta_L", "Meta_R"}:
            self._queue.put(KeyboardEvent(key="meta", raw_key=event.keysym))
            return
        special = SIM_SPECIAL_KEYS.get(event.keysym)
        if special is not None:
            key, text = special
            self._queue.put(
                KeyboardEvent(
                    key=key,
                    raw_key=event.keysym,
                    text=text,
                    ctrl=bool(event.state & 0x0004),
                    alt=bool(event.state & 0x0008),
                    shift=bool(event.state & 0x0001),
                )
            )
            return
        if len(event.char) == 1 and event.char.isprintable():
            char = event.char
            logical = char.lower() if char.isalpha() else char
            self._queue.put(
                KeyboardEvent(
                    key=logical,
                    raw_key=event.keysym,
                    text=char,
                    ctrl=bool(event.state & 0x0004),
                    alt=bool(event.state & 0x0008),
                    shift=bool(event.state & 0x0001),
                )
            )

