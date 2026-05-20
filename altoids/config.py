from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib


@dataclass(slots=True)
class DisplayConfig:
    backend: str = "auto"
    width: int = 280
    height: int = 240
    fps_active: int = 12
    fps_idle: int = 1
    backlight_brightness: float = 1.0
    rotation: int = 270
    driver_path: str = "vendor/Whisplay/runtime"
    transfer_quantization: str = "rgb565"
    spi_speed_hz: int | None = None
    input_poll_interval: float = 0.005
    split_dirty_regions: bool = False


@dataclass(slots=True)
class SleepConfig:
    idle_seconds: int = 60


@dataclass(slots=True)
class UIConfig:
    font_path: str = "fonts/terminus.pil"
    font_size: int = 12
    message_interval: float = 12.0
    mascot_frame_seconds: float = 0.5
    button_labels: list[str] = field(default_factory=lambda: ["A", "B", "X", "Y"])


@dataclass(slots=True)
class TerminalConfig:
    session_name: str = "altoids"
    pane_history: int = 200
    width_chars: int = 53
    height_chars: int = 20
    scroll_step: int = 3
    shell_rc_path: str = "config/cyberdeck-shell.sh"
    font_path: str = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
    font_size: int = 11
    minimal_commands: list[str] = field(default_factory=lambda: ["codx", "codex"])
    codex_compact: bool = False
    codex_home: str = "~/.codex"
    codex_scan_limit: int = 24


@dataclass(slots=True)
class SystemConfig:
    temperature_warn_c: float = 70.0


@dataclass(slots=True)
class WifiConfig:
    scan_cache_seconds: float = 15.0
    passwords: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class AudioConfig:
    enabled: bool = True
    card: str = "wm8960soundcard"
    volume_percent: int = 70
    muted: bool = False
    cue_screen_change: bool = True
    cue_wake: bool = True
    cue_boot: bool = True
    cue_wifi: bool = True
    cue_error: bool = True


@dataclass(slots=True)
class LedConfig:
    enabled: bool = True
    pulses: bool = True
    brightness: float = 0.35


@dataclass(slots=True)
class VoiceConfig:
    enabled: bool = True
    backend: str = "openai"
    model: str = "gpt-4o-mini-transcribe"
    trigger: str = "meta+space"
    max_record_seconds: float = 30.0
    min_record_seconds: float = 0.25
    timeout_seconds: float = 30.0
    insert_trailing_space: bool = False
    language: str = ""
    prompt: str = ""
    audio_device: str = ""
    sample_rate: int = 16000


@dataclass(slots=True)
class AltoidsConfig:
    root_dir: Path
    display: DisplayConfig = field(default_factory=DisplayConfig)
    sleep: SleepConfig = field(default_factory=SleepConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    terminal: TerminalConfig = field(default_factory=TerminalConfig)
    system: SystemConfig = field(default_factory=SystemConfig)
    wifi: WifiConfig = field(default_factory=WifiConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    led: LedConfig = field(default_factory=LedConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)

    @property
    def font_path(self) -> Path:
        return self.root_dir / self.ui.font_path

    @property
    def shell_rc_path(self) -> Path:
        return self.root_dir / self.terminal.shell_rc_path

    @property
    def terminal_font_path(self) -> Path:
        path = Path(self.terminal.font_path)
        if path.is_absolute():
            return path
        return self.root_dir / path

    @property
    def codex_home_path(self) -> Path:
        return Path(self.terminal.codex_home).expanduser()

    @property
    def display_driver_path(self) -> Path:
        path = Path(self.display.driver_path)
        if path.is_absolute():
            return path
        return self.root_dir / path


def _merge_dataclass(instance: Any, values: dict[str, Any]) -> Any:
    for key, value in values.items():
        if hasattr(instance, key):
            setattr(instance, key, value)
    return instance


def load_config(path: str | Path | None = None) -> AltoidsConfig:
    root_dir = Path(__file__).resolve().parent.parent
    _load_dotenv(root_dir / ".env")
    config = AltoidsConfig(root_dir=root_dir)
    config_path = Path(path) if path else root_dir / "config" / "altoids.toml"
    if not config_path.exists():
        return config

    payload = tomllib.loads(config_path.read_text())
    if "display" in payload:
        config.display = _merge_dataclass(config.display, payload["display"])
    if "sleep" in payload:
        config.sleep = _merge_dataclass(config.sleep, payload["sleep"])
    if "ui" in payload:
        config.ui = _merge_dataclass(config.ui, payload["ui"])
    if "terminal" in payload:
        config.terminal = _merge_dataclass(config.terminal, payload["terminal"])
    if "system" in payload:
        config.system = _merge_dataclass(config.system, payload["system"])
    if "wifi" in payload:
        config.wifi = _merge_dataclass(config.wifi, payload["wifi"])
    if "audio" in payload:
        config.audio = _merge_dataclass(config.audio, payload["audio"])
    if "led" in payload:
        config.led = _merge_dataclass(config.led, payload["led"])
    if "voice" in payload:
        config.voice = _merge_dataclass(config.voice, payload["voice"])
    return config


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value
