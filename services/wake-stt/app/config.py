"""Configuration for the Kabosu wake STT bridge."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_optional(name: str, fallback: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return fallback
    return value.strip()


@dataclass(frozen=True)
class Settings:
    api_token: str | None = None
    transcription_url: str | None = None
    transcription_token: str | None = None
    language: str | None = "ja"
    sample_rate: int = 16000
    min_window_ms: int = 600
    max_window_ms: int = 2200
    submit_interval_ms: int = 650
    idle_reset_ms: int = 1400
    turn_silence_ms: int = 900
    turn_max_ms: int = 4500
    turn_preroll_ms: int = 900
    fast_command_stability_ms: int = 500
    min_silence_duration_ms: int = 80
    max_speech_duration_s: float = 2.0
    request_timeout_seconds: float = 8.0
    log_transcripts: bool = False
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        tx_url = _get_optional("WAKE_STT_TRANSCRIPTION_URL", _get_optional("TRANSCRIPTION_SERVICE_URL"))
        tx_token = _get_optional(
            "WAKE_STT_TRANSCRIPTION_TOKEN", _get_optional("TRANSCRIPTION_SERVICE_TOKEN")
        )
        language = _get_optional("WAKE_STT_LANGUAGE", cls.language)
        if language == "auto":
            language = None
        return cls(
            api_token=_get_optional("WAKE_STT_API_TOKEN"),
            transcription_url=tx_url.rstrip("/") if tx_url else None,
            transcription_token=tx_token,
            language=language,
            sample_rate=_get_int("WAKE_STT_SAMPLE_RATE", cls.sample_rate),
            min_window_ms=_get_int("WAKE_STT_MIN_WINDOW_MS", cls.min_window_ms),
            max_window_ms=_get_int("WAKE_STT_MAX_WINDOW_MS", cls.max_window_ms),
            submit_interval_ms=_get_int(
                "WAKE_STT_SUBMIT_INTERVAL_MS", cls.submit_interval_ms
            ),
            idle_reset_ms=_get_int("WAKE_STT_IDLE_RESET_MS", cls.idle_reset_ms),
            turn_silence_ms=_get_int("WAKE_STT_TURN_SILENCE_MS", cls.turn_silence_ms),
            turn_max_ms=_get_int("WAKE_STT_TURN_MAX_MS", cls.turn_max_ms),
            turn_preroll_ms=_get_int("WAKE_STT_TURN_PREROLL_MS", cls.turn_preroll_ms),
            fast_command_stability_ms=_get_int(
                "WAKE_STT_FAST_COMMAND_STABILITY_MS",
                cls.fast_command_stability_ms,
            ),
            min_silence_duration_ms=_get_int(
                "WAKE_STT_MIN_SILENCE_DURATION_MS", cls.min_silence_duration_ms
            ),
            max_speech_duration_s=float(
                os.getenv("WAKE_STT_MAX_SPEECH_DURATION_S", str(cls.max_speech_duration_s))
            ),
            request_timeout_seconds=float(
                os.getenv("WAKE_STT_REQUEST_TIMEOUT_SECONDS", str(cls.request_timeout_seconds))
            ),
            log_transcripts=_get_bool("WAKE_STT_LOG_TRANSCRIPTS", cls.log_transcripts),
            log_level=os.getenv("LOG_LEVEL", cls.log_level),
        )
