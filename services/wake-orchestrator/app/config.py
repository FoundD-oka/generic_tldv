"""Runtime configuration for the Kabosu Wake Orchestrator."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal


AudioFormat = Literal["mp3", "wav", "flac", "aac", "opus"]


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return int(value)


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return float(value)


def _get_list(name: str, default: list[str]) -> list[str]:
    value = os.getenv(name)
    if not value:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


def _get_optional(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


@dataclass(frozen=True)
class Settings:
    """Environment-driven settings.

    Defaults intentionally present the product as Kabosu even though the
    underlying meeting substrate is Vexa.
    """

    vexa_api_url: str = "http://localhost:8056"
    vexa_api_key: str | None = None
    vexa_platform: str = "google_meet"
    vexa_native_meeting_id: str | None = None
    wake_auto_discover_bots: bool = True
    wake_discovery_interval_seconds: float = 5.0
    wake_stt_url: str | None = None
    wake_stt_token: str | None = None

    groq_api_key: str | None = None
    groq_api_base: str = "https://api.groq.com/openai/v1"
    groq_model: str = "openai/gpt-oss-20b"
    groq_timeout_seconds: float = 5.0
    groq_max_completion_tokens: int = 768
    groq_retry_max_completion_tokens: int = 1536

    aivis_api_base: str = "https://api.aivis-project.com/v1"
    aivis_api_key: str | None = None
    aivis_model_uuid: str = "18972473-ca36-4e06-a33a-5cc14adba0c4"
    aivis_speaker_uuid: str | None = None
    aivis_style_id: int | None = 0
    aivis_style_name: str | None = None
    aivis_user_dictionary_uuid: str | None = None
    aivis_output_format: AudioFormat = "wav"
    aivis_output_bitrate: int = 192
    aivis_output_sampling_rate: int = 24000
    aivis_output_audio_channels: str = "mono"
    aivis_timeout_seconds: float = 10.0
    aivis_use_ssml: bool = True
    aivis_leading_silence_seconds: float = 0.05
    aivis_trailing_silence_seconds: float = 0.7
    aivis_line_break_silence_seconds: float = 0.2

    wake_words: list[str] = field(
        default_factory=lambda: ["カボス", "ねえカボス", "カボスさん", "かぼす", "カボちゃん", "カーブス"]
    )
    wake_negative_patterns: list[str] = field(
        default_factory=lambda: [
            "カボスって",
            "カボスの",
            "カボスを使って",
            "カボスという",
            "AIみたいな",
            "AIツール",
        ]
    )
    wake_cooldown_ms: int = 0
    wake_same_speaker_dedupe_ms: int = 0
    wake_input_settle_ms: int = 800
    wake_max_input_ms: int = 2500
    wake_stabilized_duplicate_ms: int = 20000
    wake_ack_enabled: bool = True
    wake_ack_text: str = "うん！"
    wake_ack_audio_path: str | None = None
    wake_ack_audio_format: AudioFormat = "wav"
    wake_ack_audio_sample_rate: int = 24000
    wake_ack_max_lag_ms: int = 3000
    wake_ack_min_answer_gap_ms: int = 700
    wake_response_playback_guard_ms: int = 1000
    wake_speech_event_poll_ms: int = 1000
    wake_use_pending_transcripts: bool = True
    wake_use_confirmed_transcripts: bool = False
    recent_transcript_minutes: int = 5
    wake_chat_enabled: bool = True
    wake_chat_bootstrap_enabled: bool = True
    wake_chat_recent_limit: int = 30
    wake_chat_recent_minutes: int = 15
    wake_chat_same_text_dedupe_ms: int = 3000
    wake_chat_max_message_chars: int = 1000
    wake_chat_bot_sender_names: list[str] = field(
        default_factory=lambda: ["カボス", "Kabosu", "kabosu", "Vexa Bot"]
    )
    wake_chat_empty_prompt: str = (
        "呼ばれたよ。要約、整理、チャット内容の確認、次アクション出しができます。"
    )

    max_speech_chars: int = 220
    bot_echo_cooldown_ms: int = 2000
    wake_log_transcripts: bool = False
    retry_delay_ms: int = 700

    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        output_format = os.getenv("AIVIS_OUTPUT_FORMAT", cls.aivis_output_format).strip().lower()
        if output_format not in {"mp3", "wav", "flac", "aac", "opus"}:
            raise ValueError("AIVIS_OUTPUT_FORMAT must be one of: mp3, wav, flac, aac, opus")

        style_name = _get_optional("AIVIS_STYLE_NAME")
        style_id: int | None
        if style_name:
            style_id = None
        else:
            style_raw = os.getenv("AIVIS_STYLE_ID", "0").strip()
            style_id = int(style_raw) if style_raw != "" else None

        return cls(
            vexa_api_url=os.getenv("VEXA_API_URL", cls.vexa_api_url).rstrip("/"),
            vexa_api_key=_get_optional("VEXA_API_KEY"),
            vexa_platform=os.getenv("VEXA_PLATFORM", cls.vexa_platform),
            vexa_native_meeting_id=_get_optional("VEXA_NATIVE_MEETING_ID"),
            wake_auto_discover_bots=_get_bool(
                "WAKE_AUTO_DISCOVER_BOTS", cls.wake_auto_discover_bots
            ),
            wake_discovery_interval_seconds=_get_float(
                "WAKE_DISCOVERY_INTERVAL_SECONDS", cls.wake_discovery_interval_seconds
            ),
            wake_stt_url=_get_optional("WAKE_STT_URL"),
            wake_stt_token=_get_optional("WAKE_STT_TOKEN") or _get_optional("WAKE_STT_API_TOKEN"),
            groq_api_key=_get_optional("GROQ_API_KEY"),
            groq_api_base=os.getenv("GROQ_API_BASE", cls.groq_api_base).rstrip("/"),
            groq_model=os.getenv("GROQ_MODEL", cls.groq_model),
            groq_timeout_seconds=_get_float("GROQ_TIMEOUT_SECONDS", cls.groq_timeout_seconds),
            groq_max_completion_tokens=_get_int(
                "GROQ_MAX_COMPLETION_TOKENS", cls.groq_max_completion_tokens
            ),
            groq_retry_max_completion_tokens=_get_int(
                "GROQ_RETRY_MAX_COMPLETION_TOKENS", cls.groq_retry_max_completion_tokens
            ),
            aivis_api_base=os.getenv("AIVIS_API_BASE", cls.aivis_api_base).rstrip("/"),
            aivis_api_key=_get_optional("AIVIS_API_KEY"),
            aivis_model_uuid=os.getenv("AIVIS_MODEL_UUID", cls.aivis_model_uuid),
            aivis_speaker_uuid=_get_optional("AIVIS_SPEAKER_UUID"),
            aivis_style_id=style_id,
            aivis_style_name=style_name,
            aivis_user_dictionary_uuid=_get_optional("AIVIS_USER_DICTIONARY_UUID"),
            aivis_output_format=output_format,  # type: ignore[arg-type]
            aivis_output_bitrate=_get_int("AIVIS_OUTPUT_BITRATE", cls.aivis_output_bitrate),
            aivis_output_sampling_rate=_get_int(
                "AIVIS_OUTPUT_SAMPLING_RATE", cls.aivis_output_sampling_rate
            ),
            aivis_output_audio_channels=os.getenv(
                "AIVIS_OUTPUT_AUDIO_CHANNELS", cls.aivis_output_audio_channels
            ),
            aivis_timeout_seconds=_get_float("AIVIS_TIMEOUT_SECONDS", cls.aivis_timeout_seconds),
            aivis_use_ssml=_get_bool("AIVIS_USE_SSML", cls.aivis_use_ssml),
            aivis_leading_silence_seconds=_get_float(
                "AIVIS_LEADING_SILENCE_SECONDS", cls.aivis_leading_silence_seconds
            ),
            aivis_trailing_silence_seconds=_get_float(
                "AIVIS_TRAILING_SILENCE_SECONDS", cls.aivis_trailing_silence_seconds
            ),
            aivis_line_break_silence_seconds=_get_float(
                "AIVIS_LINE_BREAK_SILENCE_SECONDS", cls.aivis_line_break_silence_seconds
            ),
            wake_words=_get_list("WAKE_WORDS", cls().wake_words),
            wake_negative_patterns=_get_list(
                "WAKE_NEGATIVE_PATTERNS", cls().wake_negative_patterns
            ),
            wake_cooldown_ms=_get_int("WAKE_COOLDOWN_MS", cls.wake_cooldown_ms),
            wake_same_speaker_dedupe_ms=_get_int(
                "WAKE_SAME_SPEAKER_DEDUPE_MS", cls.wake_same_speaker_dedupe_ms
            ),
            wake_input_settle_ms=_get_int("WAKE_INPUT_SETTLE_MS", cls.wake_input_settle_ms),
            wake_max_input_ms=_get_int("WAKE_MAX_INPUT_MS", cls.wake_max_input_ms),
            wake_stabilized_duplicate_ms=_get_int(
                "WAKE_STABILIZED_DUPLICATE_MS", cls.wake_stabilized_duplicate_ms
            ),
            wake_ack_enabled=_get_bool("WAKE_ACK_ENABLED", cls.wake_ack_enabled),
            wake_ack_text=os.getenv("WAKE_ACK_TEXT", cls.wake_ack_text),
            wake_ack_audio_path=_get_optional("WAKE_ACK_AUDIO_PATH"),
            wake_ack_audio_format=os.getenv(
                "WAKE_ACK_AUDIO_FORMAT", cls.wake_ack_audio_format
            ).strip().lower(),  # type: ignore[arg-type]
            wake_ack_audio_sample_rate=_get_int(
                "WAKE_ACK_AUDIO_SAMPLE_RATE", cls.wake_ack_audio_sample_rate
            ),
            wake_ack_max_lag_ms=_get_int("WAKE_ACK_MAX_LAG_MS", cls.wake_ack_max_lag_ms),
            wake_ack_min_answer_gap_ms=_get_int(
                "WAKE_ACK_MIN_ANSWER_GAP_MS", cls.wake_ack_min_answer_gap_ms
            ),
            wake_response_playback_guard_ms=_get_int(
                "WAKE_RESPONSE_PLAYBACK_GUARD_MS",
                cls.wake_response_playback_guard_ms,
            ),
            wake_speech_event_poll_ms=_get_int(
                "WAKE_SPEECH_EVENT_POLL_MS", cls.wake_speech_event_poll_ms
            ),
            wake_use_pending_transcripts=_get_bool(
                "WAKE_USE_PENDING_TRANSCRIPTS", cls.wake_use_pending_transcripts
            ),
            wake_use_confirmed_transcripts=_get_bool(
                "WAKE_USE_CONFIRMED_TRANSCRIPTS", cls.wake_use_confirmed_transcripts
            ),
            recent_transcript_minutes=_get_int(
                "RECENT_TRANSCRIPT_MINUTES", cls.recent_transcript_minutes
            ),
            wake_chat_enabled=_get_bool("WAKE_CHAT_ENABLED", cls.wake_chat_enabled),
            wake_chat_bootstrap_enabled=_get_bool(
                "WAKE_CHAT_BOOTSTRAP_ENABLED", cls.wake_chat_bootstrap_enabled
            ),
            wake_chat_recent_limit=_get_int(
                "WAKE_CHAT_RECENT_LIMIT", cls.wake_chat_recent_limit
            ),
            wake_chat_recent_minutes=_get_int(
                "WAKE_CHAT_RECENT_MINUTES", cls.wake_chat_recent_minutes
            ),
            wake_chat_same_text_dedupe_ms=_get_int(
                "WAKE_CHAT_SAME_TEXT_DEDUPE_MS",
                cls.wake_chat_same_text_dedupe_ms,
            ),
            wake_chat_max_message_chars=_get_int(
                "WAKE_CHAT_MAX_MESSAGE_CHARS", cls.wake_chat_max_message_chars
            ),
            wake_chat_bot_sender_names=_get_list(
                "WAKE_CHAT_BOT_SENDER_NAMES", cls().wake_chat_bot_sender_names
            ),
            wake_chat_empty_prompt=os.getenv(
                "WAKE_CHAT_EMPTY_PROMPT", cls.wake_chat_empty_prompt
            ),
            max_speech_chars=_get_int("MAX_SPEECH_CHARS", cls.max_speech_chars),
            bot_echo_cooldown_ms=_get_int("BOT_ECHO_COOLDOWN_MS", cls.bot_echo_cooldown_ms),
            wake_log_transcripts=_get_bool(
                "WAKE_LOG_TRANSCRIPTS", cls.wake_log_transcripts
            ),
            retry_delay_ms=_get_int("WAKE_RETRY_DELAY_MS", cls.retry_delay_ms),
            log_level=os.getenv("LOG_LEVEL", cls.log_level),
        )

    def missing_required(self) -> list[str]:
        missing: list[str] = []
        if not self.vexa_api_key:
            missing.append("VEXA_API_KEY")
        if not self.groq_api_key:
            missing.append("GROQ_API_KEY")
        if not self.aivis_api_key:
            missing.append("AIVIS_API_KEY")
        if not self.aivis_model_uuid:
            missing.append("AIVIS_MODEL_UUID")
        if not self.vexa_native_meeting_id and not self.wake_auto_discover_bots:
            missing.append("VEXA_NATIVE_MEETING_ID")
        return missing
