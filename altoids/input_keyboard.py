from __future__ import annotations

from dataclasses import dataclass
import time


KEY_TEXT = {
    "KEY_A": "a",
    "KEY_B": "b",
    "KEY_C": "c",
    "KEY_D": "d",
    "KEY_E": "e",
    "KEY_F": "f",
    "KEY_G": "g",
    "KEY_H": "h",
    "KEY_I": "i",
    "KEY_J": "j",
    "KEY_K": "k",
    "KEY_L": "l",
    "KEY_M": "m",
    "KEY_N": "n",
    "KEY_O": "o",
    "KEY_P": "p",
    "KEY_Q": "q",
    "KEY_R": "r",
    "KEY_S": "s",
    "KEY_T": "t",
    "KEY_U": "u",
    "KEY_V": "v",
    "KEY_W": "w",
    "KEY_X": "x",
    "KEY_Y": "y",
    "KEY_Z": "z",
    "KEY_1": "1",
    "KEY_2": "2",
    "KEY_3": "3",
    "KEY_4": "4",
    "KEY_5": "5",
    "KEY_6": "6",
    "KEY_7": "7",
    "KEY_8": "8",
    "KEY_9": "9",
    "KEY_0": "0",
    "KEY_SPACE": " ",
    "KEY_MINUS": "-",
    "KEY_EQUAL": "=",
    "KEY_LEFTBRACE": "[",
    "KEY_RIGHTBRACE": "]",
    "KEY_BACKSLASH": "\\",
    "KEY_SEMICOLON": ";",
    "KEY_APOSTROPHE": "'",
    "KEY_GRAVE": "`",
    "KEY_COMMA": ",",
    "KEY_DOT": ".",
    "KEY_SLASH": "/",
}

SHIFTED_TEXT = {
    "1": "!",
    "2": "@",
    "3": "#",
    "4": "$",
    "5": "%",
    "6": "^",
    "7": "&",
    "8": "*",
    "9": "(",
    "0": ")",
    "-": "_",
    "=": "+",
    "[": "{",
    "]": "}",
    "\\": "|",
    ";": ":",
    "'": "\"",
    "`": "~",
    ",": "<",
    ".": ">",
    "/": "?",
}

SPECIAL_KEYS = {
    "KEY_ENTER": "enter",
    "KEY_KPENTER": "enter",
    "KEY_BACKSPACE": "backspace",
    "KEY_TAB": "tab",
    "KEY_ESC": "escape",
    "KEY_UP": "up",
    "KEY_DOWN": "down",
    "KEY_LEFT": "left",
    "KEY_RIGHT": "right",
    "KEY_HOME": "home",
    "KEY_END": "end",
    "KEY_PAGEUP": "pageup",
    "KEY_PAGEDOWN": "pagedown",
    "KEY_DELETE": "delete",
    "KEY_INSERT": "insert",
    "KEY_F1": "f1",
    "KEY_F2": "f2",
    "KEY_F3": "f3",
    "KEY_F4": "f4",
    "KEY_F5": "f5",
    "KEY_F6": "f6",
    "KEY_F7": "f7",
    "KEY_F8": "f8",
    "KEY_F9": "f9",
    "KEY_F10": "f10",
    "KEY_F11": "f11",
    "KEY_F12": "f12",
    "KEY_LEFTMETA": "meta",
    "KEY_RIGHTMETA": "meta",
}

MODIFIER_KEYS = {
    "KEY_LEFTSHIFT": "shift",
    "KEY_RIGHTSHIFT": "shift",
    "KEY_LEFTALT": "alt",
    "KEY_RIGHTALT": "alt",
    "KEY_LEFTCTRL": "ctrl",
    "KEY_RIGHTCTRL": "ctrl",
}


@dataclass(slots=True)
class KeyboardEvent:
    key: str
    text: str = ""
    ctrl: bool = False
    alt: bool = False
    shift: bool = False


class KeyboardInput:
    def __init__(self) -> None:
        try:
            import evdev
        except ModuleNotFoundError:
            self.available = False
            self.evdev = None
            self._devices = []
            self._modifier_state: set[str] = set()
            self._last_discovery_at = 0.0
        else:
            self.available = True
            self.evdev = evdev
            self._devices = []
            self._modifier_state: set[str] = set()
            self._last_discovery_at = 0.0
            self._discover_devices(force=True)

    def poll(self) -> list[KeyboardEvent]:
        if not self.available:
            return []

        self._discover_devices(force=False)
        events: list[KeyboardEvent] = []
        live_devices = []
        for device in self._devices:
            try:
                for raw_event in device.read():
                    if raw_event.type != self.evdev.ecodes.EV_KEY:
                        continue
                    key_event = self.evdev.categorize(raw_event)
                    keycode = key_event.keycode
                    if isinstance(keycode, list):
                        keycode = keycode[0]
                    if raw_event.value == 2:
                        continue
                    if keycode in MODIFIER_KEYS:
                        modifier = MODIFIER_KEYS[keycode]
                        if raw_event.value == 1:
                            self._modifier_state.add(modifier)
                        elif raw_event.value == 0:
                            self._modifier_state.discard(modifier)
                        continue
                    if raw_event.value != 1:
                        continue
                    event = self._to_keyboard_event(keycode)
                    if event is not None:
                        events.append(event)
                live_devices.append(device)
            except BlockingIOError:
                live_devices.append(device)
            except OSError:
                continue
        self._devices = live_devices
        return events

    def _discover_devices(self, force: bool) -> None:
        if not self.available:
            return
        now = time.monotonic()
        if not force and now - self._last_discovery_at < 5.0:
            return
        self._last_discovery_at = now
        known_paths = {device.path for device in self._devices}
        for path in self.evdev.list_devices():
            if path in known_paths:
                continue
            try:
                device = self.evdev.InputDevice(path)
                caps = device.capabilities()
            except OSError:
                continue
            key_codes = caps.get(self.evdev.ecodes.EV_KEY, [])
            if self.evdev.ecodes.KEY_A not in key_codes:
                continue
            self._devices.append(device)

    def _to_keyboard_event(self, keycode: str) -> KeyboardEvent | None:
        shift = "shift" in self._modifier_state
        ctrl = "ctrl" in self._modifier_state
        alt = "alt" in self._modifier_state
        if keycode in SPECIAL_KEYS:
            return KeyboardEvent(key=SPECIAL_KEYS[keycode], ctrl=ctrl, alt=alt, shift=shift)
        text = KEY_TEXT.get(keycode)
        if text is None:
            return None
        if shift:
            if text.isalpha():
                text = text.upper()
            else:
                text = SHIFTED_TEXT.get(text, text)
        return KeyboardEvent(key=text.lower() if len(text) == 1 and text.isalpha() else text, text=text, ctrl=ctrl, alt=alt, shift=shift)
