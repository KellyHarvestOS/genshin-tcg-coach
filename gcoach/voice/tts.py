"""Text-to-speech for the spoken advice.

Engines (the voice id is prefixed with its engine):
  edge:   Microsoft neural voices of Edge "Read aloud" (edge-tts package) — free, no key;
  google: Google Cloud Text-to-Speech, Chirp 3 HD — needs an API key.
Only the advice phrase is sent to the service. If synthesis fails the UI falls back to the
Windows voice (browser speechSynthesis). Audio is cached on disk, so repeated phrases
("Завершайте раунд.") are instant.

Settings live in data/voice.json (edited from the UI); config.toml / GCOACH_* give defaults.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import threading
import urllib.error
import urllib.request
from typing import Any

from ..config import Config

log = logging.getLogger("gcoach.voice")

GOOGLE_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"
FEMALE_VOICES: list[tuple[str, str]] = [
    ("edge:ru-RU-SvetlanaNeural", "Светлана — нейросеть, родной русский"),
    ("edge:en-US-AvaMultilingualNeural", "Ава — мультиязычная нейросеть"),
    ("edge:en-US-EmmaMultilingualNeural", "Эмма — мультиязычная нейросеть"),
    ("edge:de-DE-SeraphinaMultilingualNeural", "Серафина — мультиязычная нейросеть"),
    ("edge:fr-FR-VivienneMultilingualNeural", "Вивьен — мультиязычная нейросеть"),
    ("google:ru-RU-Chirp3-HD-Aoede", "Google Aoede (нужен ключ)"),
    ("google:ru-RU-Chirp3-HD-Kore", "Google Kore (нужен ключ)"),
    ("google:ru-RU-Chirp3-HD-Leda", "Google Leda (нужен ключ)"),
    ("google:ru-RU-Chirp3-HD-Zephyr", "Google Zephyr (нужен ключ)"),
]
DEFAULT_VOICE = FEMALE_VOICES[0][0]
GOOGLE_FALLBACK = "ru-RU-Wavenet-E"  # if a Chirp voice is rejected for this language


class TTSError(Exception):
    def __init__(self, message: str, status: int = 503):
        super().__init__(message)
        self.status = status


def _normalise(voice: str | None) -> str:
    voice = voice or DEFAULT_VOICE
    return voice if ":" in voice else f"google:{voice}"  # ids saved before engines had prefixes


class VoiceService:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.file = cfg.path("data/voice.json")
        self.cache_dir = cfg.path("data/tts_cache")
        self._lock = threading.Lock()
        self.settings: dict[str, Any] = {"enabled": True, "voice": cfg.TTS_VOICE, "rate": cfg.TTS_RATE, "api_key": ""}
        try:
            self.settings.update(json.loads(self.file.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        self.settings["voice"] = _normalise(self.settings.get("voice"))

    # ---------------------------------------------------------------------------------
    @property
    def api_key(self) -> str:
        return (self.settings.get("api_key") or self.cfg.GOOGLE_TTS_API_KEY or "").strip()

    def _engine_voice(self) -> tuple[str, str]:
        engine, name = self.settings["voice"].split(":", 1)
        if engine == "google" and not self.api_key:
            engine, name = DEFAULT_VOICE.split(":", 1)  # no key: the free neural voice
        return engine, name

    def public(self) -> dict[str, Any]:
        """Settings for the UI (the key itself never leaves the server)."""
        return {"enabled": bool(self.settings.get("enabled", True)), "voice": self.settings["voice"],
                "rate": self.settings.get("rate"), "provider": self._engine_voice()[0],
                "has_key": bool(self.api_key), "voices": [{"id": v, "name": n} for v, n in FEMALE_VOICES]}

    def update(self, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if "enabled" in data:
                self.settings["enabled"] = bool(data["enabled"])
            if data.get("voice") in {v for v, _ in FEMALE_VOICES}:
                self.settings["voice"] = data["voice"]
            if "rate" in data:
                self.settings["rate"] = min(1.6, max(0.7, float(data["rate"])))
            if "api_key" in data:
                self.settings["api_key"] = str(data["api_key"] or "").strip()
            self.file.parent.mkdir(parents=True, exist_ok=True)
            self.file.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.public()

    # ---------------------------------------------------------------------------------
    def synthesize(self, text: str) -> bytes:
        text = " ".join(text.split())[:400]
        if not text:
            raise TTSError("Пустой текст", 400)
        engine, voice = self._engine_voice()
        rate = float(self.settings.get("rate") or 1.0)
        cached = self.cache_dir / (hashlib.md5(f"{engine}:{voice}|{rate}|{text}".encode("utf-8")).hexdigest() + ".mp3")
        if cached.exists():
            return cached.read_bytes()
        audio = self._google_synth(text, voice, rate) if engine == "google" else self._edge_synth(text, voice, rate)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(audio)
        return audio

    def _edge_synth(self, text: str, voice: str, rate: float) -> bytes:
        try:
            audio = self._edge(text, voice, rate)
        except ImportError as exc:
            raise TTSError("Пакет edge-tts не установлен: pip install edge-tts", 503) from exc
        except Exception as exc:  # network / service errors
            raise TTSError(f"Нейроголос недоступен: {exc}", 502) from exc
        if not audio:
            raise TTSError("Нейроголос вернул пустой звук", 502)
        return audio

    @staticmethod
    def _edge(text: str, voice: str, rate: float) -> bytes:
        import edge_tts

        async def run() -> bytes:
            buf = bytearray()
            async for chunk in edge_tts.Communicate(text, voice, rate=f"{round((rate - 1) * 100):+d}%").stream():
                if chunk["type"] == "audio":
                    buf += chunk["data"]
            return bytes(buf)

        return asyncio.run(run())  # called from a worker thread: no running event loop there

    def _google_synth(self, text: str, voice: str, rate: float) -> bytes:
        attempts = [(voice, rate), (voice, None)]  # retry without speakingRate if a voice rejects it
        if voice != GOOGLE_FALLBACK:
            attempts.append((GOOGLE_FALLBACK, rate))
        error = TTSError("Google TTS: неизвестная ошибка")
        for name, speed in attempts:
            try:
                return self._google(self.api_key, text, name, speed)
            except urllib.error.HTTPError as exc:
                error = TTSError(f"Google TTS: {_google_message(exc)}", 502)
                log.warning("tts %s failed: %s", name, error)
                if exc.code != 400:  # bad key / API disabled / quota: another voice will not help
                    break
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise TTSError(f"Нет связи с Google TTS: {exc}", 502) from exc
        raise error

    @staticmethod
    def _google(key: str, text: str, voice: str, rate: float | None) -> bytes:
        audio_cfg: dict[str, Any] = {"audioEncoding": "MP3"}
        if rate:
            audio_cfg["speakingRate"] = rate
        body = {"input": {"text": text}, "voice": {"languageCode": voice[:5], "name": voice}, "audioConfig": audio_cfg}
        req = urllib.request.Request(GOOGLE_URL, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json; charset=utf-8", "X-Goog-Api-Key": key})
        with urllib.request.urlopen(req, timeout=12) as resp:
            return base64.b64decode(json.loads(resp.read())["audioContent"])


def _google_message(exc: urllib.error.HTTPError) -> str:
    try:
        return json.loads(exc.read().decode("utf-8"))["error"]["message"]
    except Exception:
        return f"HTTP {exc.code}"
