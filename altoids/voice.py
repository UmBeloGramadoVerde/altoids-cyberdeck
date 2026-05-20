from __future__ import annotations

import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .config import VoiceConfig


@dataclass(frozen=True, slots=True)
class VoiceResult:
    ok: bool
    message: str
    text: str = ""


class VoiceManager:
    def __init__(self, config: VoiceConfig, enabled: bool | None = None) -> None:
        self.config = config
        self.enabled = config.enabled if enabled is None else enabled
        self._arecord_path = shutil.which("arecord")
        self._record_process: subprocess.Popen[bytes] | None = None
        self._record_path: Path | None = None
        self._record_started_at = 0.0
        self._status = "disabled" if not self.enabled else "idle"
        self._results: queue.SimpleQueue[VoiceResult] = queue.SimpleQueue()
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def status(self) -> str:
        return self._status

    @property
    def recording(self) -> bool:
        return self._record_process is not None

    def start(self) -> VoiceResult:
        if not self.enabled:
            return VoiceResult(False, "voice requires Whisplay")
        ready = self._check_backend_ready()
        if not ready.ok:
            self._status = "error"
            return ready
        if self._arecord_path is None:
            self._status = "error"
            return VoiceResult(False, "arecord not found")
        with self._lock:
            if self._record_process is not None:
                return VoiceResult(True, "recording")
            if self._worker is not None and self._worker.is_alive():
                return VoiceResult(False, "voice busy")
            handle = tempfile.NamedTemporaryFile(prefix="altoids-voice-", suffix=".wav", delete=False)
            handle.close()
            path = Path(handle.name)
            args = [
                self._arecord_path,
                "-q",
                "-f",
                "S16_LE",
                "-r",
                str(max(8000, int(self.config.sample_rate))),
                "-c",
                "1",
            ]
            if self.config.audio_device:
                args.extend(["-D", self.config.audio_device])
            args.append(str(path))
            try:
                self._record_process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            except OSError as exc:
                path.unlink(missing_ok=True)
                self._status = "error"
                return VoiceResult(False, f"record failed: {exc}")
            self._record_path = path
            self._record_started_at = time.monotonic()
            self._status = "recording"
            return VoiceResult(True, "recording")

    def stop(self) -> VoiceResult:
        with self._lock:
            process = self._record_process
            path = self._record_path
            started_at = self._record_started_at
            self._record_process = None
            self._record_path = None
        if process is None or path is None:
            return VoiceResult(False, "not recording")
        duration = time.monotonic() - started_at
        self._stop_process(process)
        if duration < self.config.min_record_seconds:
            path.unlink(missing_ok=True)
            self._status = "idle"
            return VoiceResult(False, "recording too short")
        self._status = "transcribing"
        self._worker = threading.Thread(target=self._transcribe_worker, args=(path,), daemon=True)
        self._worker.start()
        return VoiceResult(True, "transcribing")

    def update(self) -> list[VoiceResult]:
        if self._record_process is not None and time.monotonic() - self._record_started_at >= self.config.max_record_seconds:
            self.stop()
        results: list[VoiceResult] = []
        while True:
            try:
                result = self._results.get_nowait()
            except queue.Empty:
                break
            results.append(result)
        return results

    def shutdown(self) -> None:
        process = self._record_process
        self._record_process = None
        self._record_path = None
        if process is not None:
            self._stop_process(process)

    def _transcribe_worker(self, path: Path) -> None:
        try:
            text = self._transcribe_openai(path)
            if self.config.insert_trailing_space and text and not text.endswith((" ", "\n")):
                text = f"{text} "
            self._status = "idle"
            self._results.put(VoiceResult(True, "transcribed", text=text))
        except Exception as exc:  # pragma: no cover - defensive thread boundary
            self._status = "error"
            self._results.put(VoiceResult(False, f"transcribe failed: {exc}"))
        finally:
            path.unlink(missing_ok=True)

    def _transcribe_openai(self, path: Path) -> str:
        if self.config.backend != "openai":
            raise RuntimeError(f"unsupported voice backend: {self.config.backend}")
        ready = self._check_backend_ready()
        if not ready.ok:
            raise RuntimeError(ready.message)
        from openai import OpenAI
        client = OpenAI(timeout=self.config.timeout_seconds)
        params: dict[str, object] = {"model": self.config.model}
        if self.config.language:
            params["language"] = self.config.language
        if self.config.prompt:
            params["prompt"] = self.config.prompt
        with path.open("rb") as audio_file:
            transcript = client.audio.transcriptions.create(file=audio_file, **params)
        text = getattr(transcript, "text", "")
        return str(text).strip()

    def _check_backend_ready(self) -> VoiceResult:
        if self.config.backend != "openai":
            return VoiceResult(False, f"unsupported voice backend: {self.config.backend}")
        if not os.environ.get("OPENAI_API_KEY"):
            return VoiceResult(False, "OPENAI_API_KEY is not set")
        try:
            import openai  # noqa: F401
        except ModuleNotFoundError:
            return VoiceResult(False, "openai package is not installed")
        return VoiceResult(True, "voice ready")

    @staticmethod
    def _stop_process(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1.0)
