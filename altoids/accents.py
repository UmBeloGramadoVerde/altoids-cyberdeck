from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import wave

from .config import AudioConfig, LedConfig
from .display import Display


@dataclass(slots=True)
class AccentStatus:
    whisplay_available: bool
    audio_available: bool
    led_available: bool
    audio_enabled: bool
    led_enabled: bool
    muted: bool
    volume_percent: int
    sleeping: bool
    audio_status: str
    audio_error: str
    last_cue: str = "idle"


class AccentManager:
    _MIN_INTERVALS = {
        "boot_complete": 1.0,
        "wake": 0.5,
        "screen_change": 0.15,
        "wifi_success": 0.5,
        "wifi_error": 0.5,
        "error": 0.4,
    }
    _LED_COLORS = {
        "boot_complete": (0, 255, 200),
        "wake": (220, 255, 255),
        "screen_change": (0, 255, 170),
        "wifi_success": (0, 255, 96),
        "wifi_error": (255, 64, 64),
        "error": (255, 64, 64),
    }

    def __init__(self, display: Display, audio_config: AudioConfig, led_config: LedConfig) -> None:
        self.display = display
        self.audio_config = audio_config
        self.led_config = led_config
        self._sleeping = False
        self._last_cue_at: dict[str, float] = {}
        self._last_cue_name = "idle"
        self._audio_process: subprocess.Popen[bytes] | None = None
        self._audio_lock = threading.Lock()
        self._audio_cache_dir = Path(tempfile.gettempdir()) / "altoids-audio"
        self._tone_files: dict[str, Path] = {}
        self._aplay_path = shutil.which("aplay")
        self._amixer_path = shutil.which("amixer")
        self._audio_available = False
        self._audio_status = "not available"
        self._audio_error = ""
        if self.display.is_whisplay:
            self._audio_available, self._audio_status, self._audio_error = self._probe_audio(audio_config.card)
        if self._audio_available:
            self._audio_cache_dir.mkdir(parents=True, exist_ok=True)
            self._prepare_tone_files()
            self._apply_volume_settings()

    @property
    def whisplay_available(self) -> bool:
        return self.display.is_whisplay

    @property
    def audio_available(self) -> bool:
        return self._audio_available

    @property
    def led_available(self) -> bool:
        return self.display.supports_led

    @property
    def status(self) -> AccentStatus:
        return AccentStatus(
            whisplay_available=self.whisplay_available,
            audio_available=self.audio_available,
            led_available=self.led_available,
            audio_enabled=self.audio_config.enabled,
            led_enabled=self.led_config.enabled and self.led_config.pulses,
            muted=self.audio_config.muted,
            volume_percent=self.audio_config.volume_percent,
            sleeping=self._sleeping,
            audio_status=self._audio_status,
            audio_error=self._audio_error,
            last_cue=self._last_cue_name,
        )

    def trigger(self, cue: str) -> None:
        if not self.whisplay_available or self._sleeping:
            return
        if not self._cue_enabled(cue):
            return
        now = time.monotonic()
        if now - self._last_cue_at.get(cue, 0.0) < self._MIN_INTERVALS.get(cue, 0.2):
            return
        self._last_cue_at[cue] = now
        self._last_cue_name = cue
        if self.audio_available and self.audio_config.enabled and not self.audio_config.muted:
            self._play_audio(cue)
        if self.led_available and self.led_config.enabled and self.led_config.pulses:
            self.display.pulse_led(
                self._LED_COLORS.get(cue, (0, 255, 170)),
                duration_ms=160 if cue == "screen_change" else 280,
                brightness=self.led_config.brightness,
            )

    def enter_standby(self) -> None:
        self._sleeping = True
        self.stop_audio()
        self.display.clear_led()

    def exit_standby(self) -> None:
        self._sleeping = False

    def set_volume_percent(self, value: int) -> None:
        self.audio_config.volume_percent = max(0, min(100, value))
        self._apply_volume_settings()

    def adjust_volume(self, delta: int) -> None:
        self.set_volume_percent(self.audio_config.volume_percent + delta)

    def toggle_mute(self) -> None:
        self.audio_config.muted = not self.audio_config.muted
        self._apply_volume_settings()

    def toggle_led_enabled(self) -> None:
        enabled = self.led_config.enabled and self.led_config.pulses
        self.led_config.enabled = not enabled
        self.led_config.pulses = self.led_config.enabled
        if not self.led_config.enabled:
            self.display.clear_led()

    def stop_audio(self) -> None:
        with self._audio_lock:
            process = self._audio_process
            self._audio_process = None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()

    def shutdown(self) -> None:
        self.stop_audio()
        self.display.clear_led()

    def _cue_enabled(self, cue: str) -> bool:
        return {
            "boot_complete": self.audio_config.cue_boot,
            "wake": self.audio_config.cue_wake,
            "screen_change": self.audio_config.cue_screen_change,
            "wifi_success": self.audio_config.cue_wifi,
            "wifi_error": self.audio_config.cue_wifi,
            "error": self.audio_config.cue_error,
        }.get(cue, True)

    def _probe_audio(self, card: str) -> tuple[bool, str, str]:
        if self._aplay_path is None:
            return False, "aplay missing", "aplay not installed"
        if self._amixer_path is None:
            return False, "amixer missing", "alsa-utils not installed"

        sources: list[str] = []
        try:
            sources.append(Path("/proc/asound/cards").read_text())
        except OSError:
            pass
        for args in ((self._aplay_path, "-l"), (self._aplay_path, "-L")):
            try:
                result = subprocess.run(args, check=False, capture_output=True, text=True, timeout=1.5)
            except (OSError, subprocess.TimeoutExpired):
                continue
            sources.append((result.stdout or "") + "\n" + (result.stderr or ""))

        if not any(card.lower() in source.lower() for source in sources):
            return False, "no wm8960", f"card {card} not listed"

        try:
            result = subprocess.run(
                [self._amixer_path, "-D", f"hw:{card}", "scontrols"],
                check=False,
                capture_output=True,
                text=True,
                timeout=1.5,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, "probe failed", str(exc)
        if result.returncode != 0:
            error = result.stderr.strip() or result.stdout.strip() or "amixer open failed"
            return False, "device open failed", error
        return True, "ok", ""

    def _prepare_tone_files(self) -> None:
        tones = {
            "boot_complete": [(620, 90, 0.35), (0, 30, 0.0), (880, 120, 0.38)],
            "wake": [(880, 70, 0.25)],
            "screen_change": [(740, 28, 0.12)],
            "wifi_success": [(660, 70, 0.28), (820, 90, 0.30)],
            "wifi_error": [(520, 80, 0.28), (360, 120, 0.22)],
            "error": [(480, 120, 0.26)],
        }
        for cue, segments in tones.items():
            path = self._audio_cache_dir / f"{cue}.wav"
            self._write_tone_file(path, segments)
            self._tone_files[cue] = path

    def _write_tone_file(self, path: Path, segments: list[tuple[int, int, float]]) -> None:
        sample_rate = 16000
        frames = bytearray()
        for frequency, duration_ms, amplitude in segments:
            total_samples = max(1, int(sample_rate * duration_ms / 1000))
            for index in range(total_samples):
                if frequency <= 0 or amplitude <= 0:
                    sample = 0
                else:
                    angle = 2.0 * math.pi * frequency * index / sample_rate
                    sample = int(math.sin(angle) * amplitude * 32767)
                frames.extend(struct.pack("<h", sample))
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            handle.writeframes(bytes(frames))

    def _play_audio(self, cue: str) -> None:
        path = self._tone_files.get(cue)
        if path is None or not path.exists() or self._aplay_path is None:
            return
        self.stop_audio()
        try:
            process = subprocess.Popen(
                [self._aplay_path, "-q", "-D", f"plughw:{self.audio_config.card}", str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            self._audio_available = False
            self._audio_status = "playback failed"
            self._audio_error = "failed to spawn aplay"
            return
        with self._audio_lock:
            self._audio_process = process
        threading.Thread(target=self._watch_audio_process, args=(process,), daemon=True).start()

    def _apply_volume_settings(self) -> None:
        if not self.audio_available or self._amixer_path is None:
            return
        try:
            subprocess.run(
                [
                    self._amixer_path,
                    "-D",
                    f"hw:{self.audio_config.card}",
                    "sset",
                    "Speaker",
                    f"{self.audio_config.volume_percent}%",
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                [
                    self._amixer_path,
                    "-D",
                    f"hw:{self.audio_config.card}",
                    "sset",
                    "Speaker",
                    "mute" if self.audio_config.muted else "unmute",
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._audio_status = "ok"
            self._audio_error = ""
        except OSError:
            self._audio_available = False
            self._audio_status = "mixer failed"
            self._audio_error = "amixer invocation failed"
            return

    def _watch_audio_process(self, process: subprocess.Popen[bytes]) -> None:
        try:
            returncode = process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            return
        with self._audio_lock:
            if self._audio_process is process:
                self._audio_process = None
        if returncode == 0:
            self._audio_status = "ok"
            self._audio_error = ""
            return
        self._audio_available = False
        self._audio_status = "playback failed"
        self._audio_error = f"aplay exited {returncode}"
