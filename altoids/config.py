from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib


@dataclass(slots=True)
class DisplayConfig:
    width: int = 320
    height: int = 240
    fps_active: int = 12
    fps_idle: int = 1
    backlight_brightness: float = 1.0


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


@dataclass(slots=True)
class SystemConfig:
    temperature_warn_c: float = 70.0


@dataclass(slots=True)
class WifiConfig:
    scan_cache_seconds: float = 15.0
    passwords: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class AltoidsConfig:
    root_dir: Path
    display: DisplayConfig = field(default_factory=DisplayConfig)
    sleep: SleepConfig = field(default_factory=SleepConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    terminal: TerminalConfig = field(default_factory=TerminalConfig)
    system: SystemConfig = field(default_factory=SystemConfig)
    wifi: WifiConfig = field(default_factory=WifiConfig)

    @property
    def font_path(self) -> Path:
        return self.root_dir / self.ui.font_path


def _merge_dataclass(instance: Any, values: dict[str, Any]) -> Any:
    for key, value in values.items():
        if hasattr(instance, key):
            setattr(instance, key, value)
    return instance


def load_config(path: str | Path | None = None) -> AltoidsConfig:
    root_dir = Path(__file__).resolve().parent.parent
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
    return config
