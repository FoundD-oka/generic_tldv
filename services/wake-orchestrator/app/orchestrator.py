"""State machine for Kabosu meeting assistant."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.resources
import io
import logging
import re
import time
import uuid
import wave
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Deque

import httpx

from .clients import MeetingRef, TtsResult, VexaClient
from .config import Settings
from .text import (
    clean_for_tts,
    contains_chat_wake,
    detect_wake,
    is_echo_of_bot,
    normalize_chat_text,
    normalize_ja,
    redact_secrets,
    strip_chat_wake,
)

logger = logging.getLogger(__name__)


class WakeState(str, Enum):
    IDLE = "IDLE"
    COLLECTING = "COLLECTING"
    THINKING = "THINKING"
    SYNTHESIZING = "SYNTHESIZING"
    SPEAKING = "SPEAKING"
    COOLDOWN = "COOLDOWN"


@dataclass
class TranscriptSegment:
    text: str
    speaker: str
    segment_id: str
    source: str = "vexa"
    completed: bool = False
    absolute_start_time: str | None = None
    absolute_end_time: str | None = None
    speaker_session_uid: str | None = None
    speaker_track_id: str | None = None
    speaker_mapping_status: str | None = None
    wake_trace_id: str | None = None
    bot_audio_received_ts_ms: int | None = None
    audio_chunk_sent_to_stt_ts_ms: int | None = None
    wake_stt_ingest_ts_ms: int | None = None
    stt_request_start_ts_ms: int | None = None
    stt_response_ts_ms: int | None = None
    transcript_mutable_publish_ts_ms: int | None = None
    websocket_received_ts_ms: int | None = None
    wake_detected_ts_ms: int | None = None
    session_id: str | None = None
    requires_command_final: bool = False


@dataclass
class ChatMessage:
    id: str
    sender: str
    normalized_sender: str
    text: str
    normalized_text: str
    timestamp_ms: int
    received_at_ms: int
    source: str
    is_from_bot: bool = False
    has_wake_word: bool = False
    consumed_turn_id: str | None = None


@dataclass
class PendingWake:
    wake: str
    speaker: str
    text: str
    segment: TranscriptSegment
    started_at: float
    updated_at: float
    requires_command_final: bool = False


class WakeOrchestrator:
    def __init__(
        self,
        settings: Settings,
        groq: Any | None,
        aivis: Any | None,
        vexa: VexaClient,
        meeting: MeetingRef | None = None,
    ):
        self.settings = settings
        self.groq = groq
        self.aivis = aivis
        self.vexa = vexa
        self.meeting = meeting
        self.state = WakeState.IDLE
        self._recent_transcript: Deque[tuple[float, str, str]] = deque(maxlen=800)
        self._recent_chat: Deque[ChatMessage] = deque(maxlen=max(settings.wake_chat_recent_limit, 1))
        self._chat_by_id: dict[str, ChatMessage] = {}
        self._recent_chat_texts: Deque[tuple[float, str, str]] = deque(maxlen=80)
        self._recent_bot_texts: Deque[tuple[float, str]] = deque(maxlen=20)
        self._recent_wake_segment_keys: Deque[tuple[float, str]] = deque(maxlen=80)
        self._recent_stabilized_wakes: Deque[tuple[float, str, str]] = deque(maxlen=30)
        self._answered_command_sessions: Deque[tuple[float, str]] = deque(maxlen=100)
        self._seen_segments: dict[str, str] = {}
        self._last_wake_by_speaker: dict[tuple[str, str], float] = {}
        self._cooldown_until = 0.0
        self._last_bot_speak_at = 0.0
        self._wake_ack_audio: TtsResult | None = None
        self._pending_wake: PendingWake | None = None
        self._pending_wake_task: asyncio.Task[None] | None = None
        self._chat_bootstrapped = False

    async def handle_message(self, message: dict[str, Any]) -> None:
        message_type = str(message.get("type") or message.get("event") or "")
        if message_type in {"chat.received", "chat.sent"}:
            await self.bootstrap_chat_context()
            await self._handle_chat_event(message, message_type)
            return
        if message_type == "chat.messages":
            self.ingest_chat_messages(message.get("messages") or [], source="ws_chat_messages")
            return
        await self.bootstrap_chat_context()
        if message_type in {"command.partial", "command.final"}:
            await self._handle_command_message(message)
            return
        if message_type != "transcript":
            return

        segments = self._segments_from_message(message)
        for index, segment in enumerate(segments):
            answered = await self.handle_segment(segment)
            if answered:
                if self.state == WakeState.COLLECTING:
                    for remaining in segments[index + 1 :]:
                        await self.handle_segment(remaining)
                else:
                    now = time.monotonic()
                    for remaining in segments[index + 1 :]:
                        if _has_wake(remaining.text, self.settings):
                            self._remember_wake_segment_keys(now, remaining)
                            logger.info(
                                "Ignoring remaining wake segment in handled transcript batch: speaker=%s segment=%s text=%s",
                                remaining.speaker,
                                remaining.segment_id,
                                _short_log_text(remaining.text),
                            )
                break

    async def bootstrap_chat_context(self) -> None:
        if self._chat_bootstrapped or not self.settings.wake_chat_bootstrap_enabled:
            return
        self._chat_bootstrapped = True
        try:
            messages = await self.vexa.chat_messages(self.meeting)
        except Exception as exc:
            logger.info("Chat bootstrap skipped: %s", exc)
            return
        stored = self.ingest_chat_messages(messages, source="rest_bootstrap")
        if stored:
            logger.info("Chat bootstrap stored messages=%d meeting=%s", stored, _meeting_log_key(self.meeting))

    def ingest_chat_messages(self, messages: list[Any], *, source: str) -> int:
        stored = 0
        for raw in messages:
            if not isinstance(raw, dict):
                continue
            message = self._chat_message_from_raw(raw, source=source)
            _, is_new = self._store_chat_message(message)
            if is_new:
                stored += 1
        return stored

    async def _handle_chat_event(self, raw: dict[str, Any], message_type: str) -> None:
        source = "ws_chat_sent" if message_type == "chat.sent" else "ws_chat_received"
        incoming = self._chat_message_from_raw(raw, source=source)
        message, is_new = self._store_chat_message(incoming)

        should_allow_bootstrap_replay = (
            not is_new
            and source == "ws_chat_received"
            and message.source == "rest_bootstrap"
            and not message.consumed_turn_id
        )
        if not is_new and not should_allow_bootstrap_replay:
            logger.info(
                "Ignoring duplicate chat message sender=%s source=%s id=%s",
                message.sender,
                source,
                message.id[:12],
            )
            return

        if source == "ws_chat_sent":
            return

        await self._maybe_answer_chat(message)

    def _chat_message_from_raw(self, raw: dict[str, Any], *, source: str) -> ChatMessage:
        received_at_ms = int(time.time() * 1000)
        text = str(raw.get("text") or raw.get("message") or "").strip()
        sender = str(raw.get("sender") or ("カボス" if source == "ws_chat_sent" else "Unknown")).strip()
        timestamp_ms = (
            _coerce_ms(raw.get("timestamp"))
            or _coerce_ms(raw.get("timestamp_ms"))
            or _coerce_ms(raw.get("ts"))
            or received_at_ms
        )
        normalized_sender = normalize_ja(sender) or normalize_chat_text(sender).lower()
        normalized_text = normalize_chat_text(text)
        is_from_bot = source == "ws_chat_sent" or _coerce_bool(raw.get("is_from_bot"))
        has_wake_word = contains_chat_wake(text)
        message_id = _chat_message_id(
            meeting_key=_meeting_log_key(self.meeting)
            if self.meeting
            else f"{self.settings.vexa_platform}:{self.settings.vexa_native_meeting_id or 'default'}",
            sender=normalized_sender,
            text=normalized_text,
            timestamp_ms=timestamp_ms,
        )
        return ChatMessage(
            id=message_id,
            sender=sender or "Unknown",
            normalized_sender=normalized_sender,
            text=text,
            normalized_text=normalized_text,
            timestamp_ms=timestamp_ms,
            received_at_ms=received_at_ms,
            source=source,
            is_from_bot=is_from_bot,
            has_wake_word=has_wake_word,
        )

    def _store_chat_message(self, message: ChatMessage) -> tuple[ChatMessage, bool]:
        existing = self._chat_by_id.get(message.id)
        if existing:
            existing.is_from_bot = existing.is_from_bot or message.is_from_bot
            existing.has_wake_word = existing.has_wake_word or message.has_wake_word
            return existing, False

        self._chat_by_id[message.id] = message
        self._recent_chat.append(message)
        self._trim_recent_chat()
        return message, True

    async def _maybe_answer_chat(self, message: ChatMessage) -> None:
        if not self.settings.wake_chat_enabled:
            return
        if message.consumed_turn_id:
            return
        if self._is_bot_chat_message(message):
            logger.info("Ignoring bot chat message sender=%s text=%s", message.sender, _short_log_text(message.text))
            return
        if not message.has_wake_word:
            return

        now = time.monotonic()
        if self._is_recent_chat_text_duplicate(now, message):
            logger.info(
                "Ignoring repeated chat wake sender=%s text=%s",
                message.sender,
                _short_log_text(message.text),
            )
            return
        if self.state != WakeState.IDLE:
            logger.info(
                "Ignoring chat wake while busy state=%s sender=%s text=%s",
                self.state,
                message.sender,
                _short_log_text(message.text),
            )
            return

        turn_id = f"chat-{uuid.uuid4().hex}"
        message.consumed_turn_id = turn_id
        self._remember_chat_text(now, message)
        utterance = strip_chat_wake(message.text) or self.settings.wake_chat_empty_prompt
        output_mode = _chat_output_mode(utterance)
        logger.info(
            "Chat wake accepted: turn=%s sender=%s output=%s text=%s",
            turn_id,
            message.sender,
            output_mode,
            _short_log_text(utterance),
        )
        await self._answer_chat(message, utterance, output_mode=output_mode)

    async def _answer_chat(self, message: ChatMessage, utterance: str, *, output_mode: str) -> None:
        if self.groq is None:
            logger.error("Chat wake detected but Groq client is not configured")
            await self._send_chat_reply("カボス:\n処理中に失敗しました。もう一度送ってください。")
            return
        if output_mode in {"voice", "both"} and self.aivis is None:
            logger.error("Chat wake requested voice output but Aivis client is not configured")
            await self._send_chat_reply("カボス:\n音声出力の準備ができていません。チャットで返します。")
            output_mode = "chat"

        try:
            self.state = WakeState.THINKING
            context = self.assistant_context_text(current_chat=message)
            reply = await self._with_one_retry(
                lambda: self.groq.generate_reply(
                    context,
                    utterance,
                    input_mode="chat",
                    output_mode=output_mode,
                ),
                label="Groq chat reply",
            )
            reply = reply.strip()
            if not reply:
                logger.warning("Groq returned an empty chat reply sender=%s", message.sender)
                return

            self.state = WakeState.SPEAKING if output_mode == "both" else WakeState.THINKING
            await self._send_chat_reply(_format_chat_reply(reply))

            if output_mode == "both":
                assert self.aivis is not None
                voice_reply = clean_for_tts(reply, self.settings.max_speech_chars)
                audio = await self._with_one_retry(
                    lambda: self.aivis.synthesize(voice_reply),
                    label="Aivis synthesize chat reply",
                )
                self._recent_bot_texts.append((time.monotonic(), voice_reply))
                self._last_bot_speak_at = time.monotonic()
                request_id = f"chat-reply-{uuid.uuid4().hex}"
                await self._with_one_retry(
                    lambda: self.vexa.speak_audio(audio, self.meeting, request_id=request_id),
                    label="Vexa chat reply speak",
                )
        except Exception:
            logger.exception("Chat answer failed")
            try:
                await self._send_chat_reply("カボス:\n処理中に失敗しました。もう一度送ってください。")
            except Exception:
                logger.exception("Chat failure notice could not be sent")
        finally:
            self.state = WakeState.COOLDOWN
            await self._cooldown()

    async def handle_segment(self, segment: TranscriptSegment) -> bool:
        now = time.monotonic()
        text = segment.text.strip()
        if not text:
            return False

        previous = self._seen_segments.get(segment.segment_id)
        if previous == text:
            return False
        delta_text = _new_segment_text(previous, text)
        self._seen_segments[segment.segment_id] = text
        if self.settings.wake_log_transcripts:
            logger.info(
                "Wake transcript received source=%s speaker=%s segment=%s completed=%s delta=%s text=%s",
                segment.source,
                segment.speaker,
                segment.segment_id,
                segment.completed,
                _short_log_text(delta_text),
                _short_log_text(text),
            )

        if self.state == WakeState.COLLECTING:
            if self._update_pending_wake(now, segment, text):
                return False
            if _has_wake(text, self.settings):
                self._remember_wake_segment_keys(now, segment)
                logger.info(
                    "Ignoring wake while collecting another input speaker=%s text=%s",
                    segment.speaker,
                    _short_log_text(text),
                )
            return False
        if self.state != WakeState.IDLE:
            if _has_wake(text, self.settings):
                self._remember_wake_segment_keys(now, segment)
                logger.info(
                    "Ignoring wake while busy state=%s speaker=%s text=%s",
                    self.state,
                    segment.speaker,
                    _short_log_text(text),
                )
            logger.debug(
                "Ignoring transcript while busy state=%s speaker=%s: %s",
                self.state,
                segment.speaker,
                text,
            )
            return False
        if now < self._cooldown_until:
            if _has_wake(text, self.settings):
                remaining_ms = int((self._cooldown_until - now) * 1000)
                self._remember_wake_segment_keys(now, segment)
                logger.info(
                    "Ignoring wake during cooldown: speaker=%s remaining_ms=%d text=%s",
                    segment.speaker,
                    remaining_ms,
                    _short_log_text(text),
                )
            return False

        self._recent_transcript.append((time.time(), _speaker_context_label(segment), delta_text or text))
        self._trim_recent()

        bot_texts = [bot_text for _, bot_text in self._recent_bot_texts]
        if self._bot_echo_window_active(now) and is_echo_of_bot(text, bot_texts):
            logger.info("Ignoring likely bot echo from %s: %s", segment.speaker, _short_log_text(text))
            return False

        candidate_text = delta_text or text
        match = detect_wake(
            candidate_text,
            self.settings.wake_words,
            self.settings.wake_negative_patterns,
        )
        if not match and candidate_text != text and not (previous and _has_wake(previous, self.settings)):
            match = detect_wake(
                text,
                self.settings.wake_words,
                self.settings.wake_negative_patterns,
            )
        if not match:
            return False

        dedupe_key = (_speaker_stream_key(segment), normalize_ja(match.wake))
        last = self._last_wake_by_speaker.get(dedupe_key, 0.0)
        dedupe_seconds = self.settings.wake_same_speaker_dedupe_ms / 1000
        if dedupe_seconds > 0 and last > 0 and now - last < dedupe_seconds:
            logger.info("Ignoring duplicate wake from %s: %s", segment.speaker, match.wake)
            return False
        if self._is_recent_stabilized_wake_duplicate(now, segment.speaker, text):
            logger.info("Ignoring recently stabilized wake from %s: %s", segment.speaker, _short_log_text(text))
            return False
        if self._is_duplicate_wake_segment(now, segment):
            logger.info(
                "Ignoring already handled wake segment from %s: segment=%s text=%s",
                segment.speaker,
                segment.segment_id,
                _short_log_text(text),
            )
            return False
        self._last_wake_by_speaker[dedupe_key] = now
        self._remember_wake_segment_keys(now, segment)
        segment.wake_detected_ts_ms = int(time.time() * 1000)
        self._log_wake_timing(segment)

        if not segment.completed and self.settings.wake_input_settle_ms > 0:
            self._start_pending_wake(now, match.wake, segment, text)
            return True

        utterance = clean_for_tts(match.seed or text, max_chars=1200)
        logger.info(
            "Wake detected: wake=%s speaker=%s utterance=%s",
            match.wake,
            segment.speaker,
            _short_log_text(utterance),
        )
        await self._answer(
            match.wake,
            segment.speaker,
            utterance,
            play_ack=self._should_play_wake_ack(match.wake, segment),
        )
        return True

    async def tick(self) -> None:
        return None

    def recent_transcript_text(self) -> str:
        self._trim_recent()
        return "\n".join(f"{speaker}: {text}" for _, speaker, text in self._recent_transcript)[-5000:]

    def recent_chat_text(self, current_chat: ChatMessage | None = None) -> str:
        self._trim_recent_chat()
        messages = list(self._recent_chat)
        if current_chat and all(message.id != current_chat.id for message in messages):
            messages.append(current_chat)
        lines = []
        for message in messages[-self.settings.wake_chat_recent_limit :]:
            sender = message.sender or "Unknown"
            source = "bot" if self._is_bot_chat_message(message) else "participant"
            text = redact_secrets(message.text)
            lines.append(f"{sender} ({source}, {message.source}): {text}")
        return "\n".join(lines)[-5000:]

    def assistant_context_text(self, current_chat: ChatMessage | None = None) -> str:
        transcript = self.recent_transcript_text()
        chat = self.recent_chat_text(current_chat)
        urls = _extract_urls("\n".join(message.text for message in self._recent_chat))
        sections = [
            "[直近の音声文字起こし]\n" + (redact_secrets(transcript) if transcript else "(なし)"),
            "[直近のチャット]\n" + (chat if chat else "(なし)"),
        ]
        if urls:
            sections.append(
                "[共有URL]\n"
                + "\n".join(f"- {redact_secrets(url)} (リンク先本文は未取得)" for url in urls[-10:])
            )
        return "\n\n".join(sections)[-9000:]

    async def _send_chat_reply(self, text: str) -> None:
        for chunk in _split_chat_text(text, self.settings.wake_chat_max_message_chars):
            await self._with_one_retry(
                lambda chunk=chunk: self.vexa.send_chat(chunk, self.meeting),
                label="Vexa chat send",
            )

    def _log_wake_timing(self, segment: TranscriptSegment) -> None:
        audio_end_ms = _timestamp_ms(segment.absolute_end_time)
        values: list[str] = []
        for label, value in (
            ("bot_to_ingest_ms", _delta_ms(segment.bot_audio_received_ts_ms, segment.wake_stt_ingest_ts_ms)),
            (
                "ingest_to_stt_request_ms",
                _delta_ms(segment.wake_stt_ingest_ts_ms, segment.stt_request_start_ts_ms),
            ),
            ("stt_request_ms", _delta_ms(segment.stt_request_start_ts_ms, segment.stt_response_ts_ms)),
            (
                "publish_to_ws_ms",
                _delta_ms(segment.transcript_mutable_publish_ts_ms, segment.websocket_received_ts_ms),
            ),
            ("audio_end_to_ws_ms", _delta_ms(audio_end_ms, segment.websocket_received_ts_ms)),
            ("ws_to_wake_ms", _delta_ms(segment.websocket_received_ts_ms, segment.wake_detected_ts_ms)),
            ("audio_end_to_wake_ms", _delta_ms(audio_end_ms, segment.wake_detected_ts_ms)),
        ):
            if value is not None:
                values.append(f"{label}={value}")
        if not values:
            return
        logger.info(
            "Wake timing source=%s trace=%s speaker=%s segment=%s %s",
            segment.source,
            segment.wake_trace_id or "-",
            segment.speaker,
            segment.segment_id,
            " ".join(values),
        )

    def _predicted_pending_answer_gap_ms(self, pending: PendingWake) -> int:
        now = time.monotonic()
        settle_seconds = max(self.settings.wake_input_settle_ms, 0) / 1000
        max_seconds = max(self.settings.wake_max_input_ms, self.settings.wake_input_settle_ms) / 1000
        settle_remaining = pending.updated_at + settle_seconds - now
        max_remaining = pending.started_at + max_seconds - now
        return max(0, int(min(settle_remaining, max_remaining) * 1000))

    def _should_play_wake_ack(
        self,
        wake: str,
        segment: TranscriptSegment,
        *,
        predicted_answer_gap_ms: int | None = None,
    ) -> bool:
        if not self.settings.wake_ack_enabled or not self.settings.wake_ack_text.strip():
            return False

        now_ms = int(time.time() * 1000)
        audio_end_ms = _timestamp_ms(segment.absolute_end_time)
        wake_lag_ms = _delta_ms(audio_end_ms, segment.wake_detected_ts_ms or now_ms)
        if (
            self.settings.wake_ack_max_lag_ms > 0
            and wake_lag_ms is not None
            and wake_lag_ms > self.settings.wake_ack_max_lag_ms
        ):
            logger.info(
                "Skipping wake ack because transcript is stale: wake=%s source=%s lag_ms=%d max_lag_ms=%d",
                wake,
                segment.source,
                wake_lag_ms,
                self.settings.wake_ack_max_lag_ms,
            )
            return False

        if (
            self.settings.wake_ack_min_answer_gap_ms > 0
            and predicted_answer_gap_ms is not None
            and predicted_answer_gap_ms < self.settings.wake_ack_min_answer_gap_ms
        ):
            logger.info(
                "Skipping wake ack because answer is expected soon: wake=%s answer_gap_ms=%d min_gap_ms=%d",
                wake,
                predicted_answer_gap_ms,
                self.settings.wake_ack_min_answer_gap_ms,
            )
            return False

        return True

    def _start_pending_wake(
        self,
        now: float,
        wake: str,
        segment: TranscriptSegment,
        text: str,
    ) -> None:
        self.state = WakeState.COLLECTING
        self._pending_wake = PendingWake(
            wake=wake,
            speaker=segment.speaker,
            text=text,
            segment=segment,
            started_at=now,
            updated_at=now,
            requires_command_final=segment.requires_command_final,
        )
        logger.info(
            "Wake candidate detected: wake=%s speaker=%s segment=%s text=%s",
            wake,
            segment.speaker,
            segment.segment_id,
            _short_log_text(text),
        )
        self._pending_wake_task = asyncio.create_task(self._finalize_pending_wake())

    def _update_pending_wake(self, now: float, segment: TranscriptSegment, text: str) -> bool:
        pending = self._pending_wake
        if pending is None or not _same_pending_wake_turn(pending, segment):
            return False

        match = detect_wake(text, self.settings.wake_words, self.settings.wake_negative_patterns)
        if not match:
            return False

        pending.wake = match.wake
        pending.text = text
        pending.segment = segment
        pending.updated_at = now
        pending.requires_command_final = pending.requires_command_final or segment.requires_command_final
        self._remember_wake_segment_keys(now, segment)
        logger.info(
            "Wake candidate updated: wake=%s speaker=%s segment=%s text=%s",
            match.wake,
            segment.speaker,
            segment.segment_id,
            _short_log_text(text),
        )
        return True

    async def _finalize_pending_wake(self) -> None:
        ack_handled = False
        pending = self._pending_wake
        if pending is not None:
            ack_handled = True
            answer_gap_ms = self._predicted_pending_answer_gap_ms(pending)
            if self._should_play_wake_ack(
                pending.wake,
                pending.segment,
                predicted_answer_gap_ms=answer_gap_ms,
            ):
                await self._play_wake_ack(pending.wake)

        while True:
            pending = self._pending_wake
            if pending is None:
                self.state = WakeState.IDLE
                return

            now = time.monotonic()
            settle_seconds = max(self.settings.wake_input_settle_ms, 0) / 1000
            max_seconds = max(self.settings.wake_max_input_ms, self.settings.wake_input_settle_ms) / 1000
            settle_remaining = pending.updated_at + settle_seconds - now
            max_remaining = pending.started_at + max_seconds - now
            wait_seconds = min(settle_remaining, max_remaining)
            if wait_seconds <= 0:
                break
            await asyncio.sleep(min(wait_seconds, 0.1))

        pending = self._pending_wake
        self._pending_wake = None
        if pending is None:
            self.state = WakeState.IDLE
            return

        match = detect_wake(pending.text, self.settings.wake_words, self.settings.wake_negative_patterns)
        if not match:
            logger.info("Wake candidate dropped before finalize: speaker=%s text=%s", pending.speaker, pending.text)
            self.state = WakeState.IDLE
            return

        if pending.requires_command_final:
            logger.info(
                "Wake candidate expired waiting for command.final: wake=%s speaker=%s text=%s",
                match.wake,
                pending.speaker,
                _short_log_text(pending.text),
            )
            self.state = WakeState.IDLE
            return

        utterance = clean_for_tts(match.seed or pending.text, max_chars=1200)
        self._recent_transcript.append(
            (time.time(), _speaker_context_label(pending.segment), pending.text)
        )
        self._trim_recent()
        self._remember_stabilized_wake(time.monotonic(), pending.speaker, pending.text)
        logger.info(
            "Wake detected: wake=%s speaker=%s utterance=%s source=stabilized_pending transcript_source=%s",
            match.wake,
            pending.speaker,
            _short_log_text(utterance),
            pending.segment.source,
        )
        await self._answer(match.wake, pending.speaker, utterance, play_ack=not ack_handled)

    def _segments_from_message(self, message: dict[str, Any]) -> list[TranscriptSegment]:
        speaker = str(message.get("speaker") or "Unknown")
        source = str(message.get("source") or "vexa")
        requires_command_final = (
            source == "wake-stt"
            and str(message.get("event") or "") == "transcript.partial"
        )
        raw_segments: list[dict[str, Any]] = []
        if self.settings.wake_use_pending_transcripts:
            raw_segments.extend(message.get("pending") or [])
        if self.settings.wake_use_confirmed_transcripts:
            raw_segments.extend(message.get("confirmed") or [])

        segments: list[TranscriptSegment] = []
        for index, raw in enumerate(raw_segments):
            text = str(raw.get("text") or "").strip()
            if not text:
                continue
            raw_speaker = str(raw.get("speaker") or speaker or "Unknown")
            segment_id = str(
                raw.get("segment_id")
                or raw.get("absolute_start_time")
                or f"{raw_speaker}:{index}:{text[:32]}"
            )
            if not raw.get("completed", False) and not raw.get("segment_id"):
                segment_id = (
                    raw.get("absolute_start_time")
                    or f"pending:{raw_speaker}:{index}:{normalize_ja(text)[:48]}"
                )
            segments.append(
                TranscriptSegment(
                    text=text,
                    speaker=raw_speaker,
                    segment_id=segment_id,
                    source=source,
                    completed=bool(raw.get("completed", False)),
                    absolute_start_time=raw.get("absolute_start_time"),
                    absolute_end_time=raw.get("absolute_end_time"),
                    speaker_session_uid=_first_str(
                        raw,
                        message,
                        "speaker_session_uid",
                        "session_uid",
                        "uid",
                    ),
                    speaker_track_id=_first_str(
                        raw,
                        message,
                        "speaker_track_id",
                        "track_id",
                        "trackId",
                    ),
                    speaker_mapping_status=_first_str(
                        raw,
                        message,
                        "speaker_mapping_status",
                        "speakerMappingStatus",
                    ),
                    wake_trace_id=_first_str(raw, message, "wake_trace_id"),
                    bot_audio_received_ts_ms=_first_ms(raw, message, "bot_audio_received_ts_ms"),
                    audio_chunk_sent_to_stt_ts_ms=_first_ms(
                        raw,
                        message,
                        "audio_chunk_sent_to_stt_ts_ms",
                    ),
                    wake_stt_ingest_ts_ms=_first_ms(raw, message, "wake_stt_ingest_ts_ms"),
                    stt_request_start_ts_ms=_first_ms(raw, message, "stt_request_start_ts_ms"),
                    stt_response_ts_ms=_first_ms(raw, message, "stt_response_ts_ms"),
                    transcript_mutable_publish_ts_ms=_first_ms(
                        raw,
                        message,
                        "transcript_mutable_publish_ts_ms",
                    ),
                    websocket_received_ts_ms=_first_ms(
                        raw,
                        message,
                        "websocket_received_ts_ms",
                        "_wake_websocket_received_ts_ms",
                    ),
                    session_id=_first_str(raw, message, "session_id"),
                    requires_command_final=requires_command_final,
                )
            )
        return segments

    async def _handle_command_message(self, message: dict[str, Any]) -> None:
        message_type = str(message.get("type") or "command.final")
        source = str(message.get("source") or "wake-stt")
        speaker = str(message.get("speaker") or "Unknown")
        session_id = str(message.get("session_id") or "")
        wake = str(message.get("wake") or "カボス")
        full_text = str(message.get("text") or "").strip()
        command_text = str(message.get("command_text") or "").strip()
        if session_id and self._has_answered_command_session(session_id):
            logger.info(
                "Ignoring duplicate %s source=%s speaker=%s session=%s text=%s",
                message_type,
                source,
                speaker,
                session_id,
                _short_log_text(full_text or command_text),
            )
            return
        if not command_text:
            match = detect_wake(full_text, self.settings.wake_words, self.settings.wake_negative_patterns)
            command_text = match.seed if match else full_text
        utterance = clean_for_tts(command_text, max_chars=1200)

        await self._cancel_pending_wake_for_command(session_id)

        if _is_weak_command_text(utterance):
            logger.info(
                "Ignoring empty command.final source=%s speaker=%s session=%s text=%s command=%s",
                source,
                speaker,
                session_id or "-",
                _short_log_text(full_text),
                _short_log_text(utterance),
            )
            self.state = WakeState.IDLE
            return

        if self.state not in {WakeState.IDLE, WakeState.COLLECTING}:
            logger.info(
                "Ignoring command.final while busy state=%s source=%s speaker=%s session=%s command=%s",
                self.state,
                source,
                speaker,
                session_id or "-",
                _short_log_text(utterance),
            )
            return

        self._recent_transcript.append((time.time(), speaker, full_text or utterance))
        self._trim_recent()
        self._remember_stabilized_wake(time.monotonic(), speaker, full_text or utterance)
        if session_id:
            self._remember_answered_command_session(time.monotonic(), session_id)
        logger.info(
            "Wake command accepted: event=%s wake=%s speaker=%s session=%s utterance=%s source=%s",
            message_type,
            wake,
            speaker,
            session_id or "-",
            _short_log_text(utterance),
            source,
        )
        await self._answer(wake, speaker, utterance, play_ack=False)

    async def _cancel_pending_wake_for_command(self, session_id: str) -> None:
        pending_task = self._pending_wake_task
        self._pending_wake = None
        self._pending_wake_task = None
        if pending_task and not pending_task.done() and pending_task is not asyncio.current_task():
            pending_task.cancel()
            await asyncio.gather(pending_task, return_exceptions=True)

    async def _answer(self, wake: str, speaker: str, utterance: str, *, play_ack: bool = True) -> None:
        if self.groq is None:
            logger.error("Wake detected but Groq client is not configured")
            self.state = WakeState.COOLDOWN
            await self._cooldown()
            return
        output_mode = _voice_output_mode(utterance)
        if output_mode in {"voice", "both"} and self.aivis is None:
            logger.error("Wake detected but Aivis client is not configured")
            self.state = WakeState.COOLDOWN
            await self._cooldown()
            return

        try:
            if play_ack:
                self.state = WakeState.SPEAKING
                await self._play_wake_ack(wake)

            self.state = WakeState.THINKING
            reply = await self._with_one_retry(
                lambda: self.groq.generate_reply(
                    self.assistant_context_text(),
                    utterance,
                    input_mode="voice",
                    output_mode=output_mode,
                ),
                label="Groq reply",
            )
            if not reply:
                logger.warning("Groq returned an empty reply for wake=%s speaker=%s", wake, speaker)
                return

            if output_mode in {"chat", "both"}:
                await self._send_chat_reply(_format_chat_reply(reply))
                if output_mode == "chat":
                    logger.info(
                        "Wake reply sent to chat: wake=%s speaker=%s chars=%d",
                        wake,
                        speaker,
                        len(reply),
                    )
                    return

            voice_reply = clean_for_tts(reply, self.settings.max_speech_chars)
            self.state = WakeState.SYNTHESIZING
            assert self.aivis is not None
            audio = await self._with_one_retry(
                lambda: self.aivis.synthesize(voice_reply),
                label="Aivis synthesize",
            )
            self._recent_bot_texts.append((time.monotonic(), voice_reply))

            self.state = WakeState.SPEAKING
            self._last_bot_speak_at = time.monotonic()
            request_id = f"wake-reply-{uuid.uuid4().hex}"
            await self._with_one_retry(
                lambda: self.vexa.speak_audio(audio, self.meeting, request_id=request_id),
                label="Vexa reply speak",
            )
            playback_seconds = _audio_duration_seconds(audio)
            guard_seconds = self.settings.wake_response_playback_guard_ms / 1000
            event_timeout_seconds = max(5.0, playback_seconds + max(guard_seconds, 3.0))
            logger.info(
                "Wake reply sent: wake=%s speaker=%s chars=%d audio_bytes=%d request_id=%s event_timeout_ms=%d",
                wake,
                speaker,
                len(voice_reply),
                len(audio.audio),
                request_id,
                int(event_timeout_seconds * 1000),
            )
            terminal_event = await self.vexa.wait_for_speech_to_finish(
                request_id=request_id,
                meeting=self.meeting,
                timeout_seconds=event_timeout_seconds,
                poll_interval_seconds=self.settings.wake_speech_event_poll_ms / 1000,
            )
            if terminal_event:
                logger.info(
                    "Wake reply playback finished: wake=%s speaker=%s request_id=%s event=%s",
                    wake,
                    speaker,
                    request_id,
                    terminal_event,
                )
            else:
                logger.warning(
                    "Wake reply playback event timed out: wake=%s speaker=%s request_id=%s",
                    wake,
                    speaker,
                    request_id,
                )
        except Exception:
            logger.exception("Wake answer failed")
        finally:
            self.state = WakeState.COOLDOWN
            await self._cooldown()

    async def _play_wake_ack(self, wake: str) -> None:
        ack_text = self.settings.wake_ack_text.strip()
        if not self.settings.wake_ack_enabled or not ack_text:
            return

        try:
            audio = self._load_wake_ack_audio()
            if audio is None:
                return
            self._recent_bot_texts.append((time.monotonic(), ack_text))
            self._last_bot_speak_at = time.monotonic()
            request_id = f"wake-ack-{uuid.uuid4().hex}"
            request_ts_ms = int(time.time() * 1000)
            await self._with_one_retry(
                lambda: self.vexa.speak_audio(audio, self.meeting, request_id=request_id),
                label="Vexa wake ack speak",
            )
            logger.info(
                "Wake ack sent: wake=%s source=recorded_wav bytes=%d request_id=%s ack_speak_request_ts_ms=%d",
                wake,
                len(audio.audio),
                request_id,
                request_ts_ms,
            )
        except Exception:
            logger.exception("Wake ack failed")

    def _load_wake_ack_audio(self) -> TtsResult | None:
        if self._wake_ack_audio is not None:
            return self._wake_ack_audio

        try:
            if self.settings.wake_ack_audio_path:
                audio = Path(self.settings.wake_ack_audio_path).read_bytes()
            else:
                audio = (
                    importlib.resources.files("app")
                    .joinpath("assets")
                    .joinpath("wake_ack_un_bang.wav")
                    .read_bytes()
                )
        except OSError:
            logger.exception("Wake ack asset could not be read")
            return None

        self._wake_ack_audio = TtsResult(
            audio=audio,
            format=self.settings.wake_ack_audio_format,
            sample_rate=self.settings.wake_ack_audio_sample_rate,
            headers={"purpose": "wake_ack", "source": "recorded_wav"},
        )
        return self._wake_ack_audio

    async def _with_one_retry(
        self,
        factory: Callable[[], Awaitable[Any]],
        label: str,
    ) -> Any:
        try:
            return await factory()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {500, 502, 503, 504}:
                logger.warning("%s HTTP %s; retrying once", label, exc.response.status_code)
                await asyncio.sleep(self.settings.retry_delay_ms / 1000)
                return await factory()
            raise
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            logger.warning("%s transport error %s; retrying once", label, exc)
            await asyncio.sleep(self.settings.retry_delay_ms / 1000)
            return await factory()

    async def _cooldown(self) -> None:
        cooldown_seconds = max(self.settings.wake_cooldown_ms, 0) / 1000
        self._cooldown_until = time.monotonic() + cooldown_seconds
        if cooldown_seconds > 0:
            await asyncio.sleep(cooldown_seconds)
        self.state = WakeState.IDLE

    def _bot_echo_window_active(self, now: float) -> bool:
        return (
            self._last_bot_speak_at > 0
            and now - self._last_bot_speak_at < self.settings.bot_echo_cooldown_ms / 1000
        )

    def _is_duplicate_wake_segment(self, now: float, segment: TranscriptSegment) -> bool:
        keys = _wake_segment_keys(segment)
        if not keys:
            return False

        cutoff = now - 180
        while self._recent_wake_segment_keys and self._recent_wake_segment_keys[0][0] < cutoff:
            self._recent_wake_segment_keys.popleft()
        recent_keys = {key for _, key in self._recent_wake_segment_keys}
        return any(key in recent_keys for key in keys)

    def _is_recent_stabilized_wake_duplicate(self, now: float, speaker: str, text: str) -> bool:
        window_seconds = max(self.settings.wake_stabilized_duplicate_ms, 0) / 1000
        if window_seconds <= 0:
            return False

        cutoff = now - window_seconds
        while self._recent_stabilized_wakes and self._recent_stabilized_wakes[0][0] < cutoff:
            self._recent_stabilized_wakes.popleft()

        text_norm = _wake_turn_fingerprint(text)
        if not text_norm:
            return False
        speaker_norm = normalize_ja(speaker) or speaker
        return any(
            recent_speaker == speaker_norm and _same_wake_turn_text(recent_text, text_norm)
            for _, recent_speaker, recent_text in self._recent_stabilized_wakes
        )

    def _remember_stabilized_wake(self, now: float, speaker: str, text: str) -> None:
        text_norm = _wake_turn_fingerprint(text)
        if not text_norm:
            return
        self._recent_stabilized_wakes.append((now, normalize_ja(speaker) or speaker, text_norm))

    def _has_answered_command_session(self, session_id: str) -> bool:
        now = time.monotonic()
        cutoff = now - 600
        while self._answered_command_sessions and self._answered_command_sessions[0][0] < cutoff:
            self._answered_command_sessions.popleft()
        return any(recent_session_id == session_id for _, recent_session_id in self._answered_command_sessions)

    def _remember_answered_command_session(self, now: float, session_id: str) -> None:
        if not session_id or self._has_answered_command_session(session_id):
            return
        self._answered_command_sessions.append((now, session_id))

    def _remember_wake_segment_keys(self, now: float, segment: TranscriptSegment) -> None:
        for key in _wake_segment_keys(segment):
            self._recent_wake_segment_keys.append((now, key))

    def _trim_recent(self) -> None:
        cutoff = time.time() - self.settings.recent_transcript_minutes * 60
        while self._recent_transcript and self._recent_transcript[0][0] < cutoff:
            self._recent_transcript.popleft()
        bot_cutoff = time.monotonic() - 120
        while self._recent_bot_texts and self._recent_bot_texts[0][0] < bot_cutoff:
            self._recent_bot_texts.popleft()

    def _trim_recent_chat(self) -> None:
        cutoff = int((time.time() - self.settings.wake_chat_recent_minutes * 60) * 1000)
        while self._recent_chat and self._recent_chat[0].received_at_ms < cutoff:
            old = self._recent_chat.popleft()
            self._chat_by_id.pop(old.id, None)
        while len(self._recent_chat) > max(self.settings.wake_chat_recent_limit, 1):
            old = self._recent_chat.popleft()
            self._chat_by_id.pop(old.id, None)

    def _is_bot_chat_message(self, message: ChatMessage) -> bool:
        if message.is_from_bot:
            return True
        bot_names = {normalize_ja(name) or normalize_chat_text(name).lower() for name in self.settings.wake_chat_bot_sender_names}
        return message.normalized_sender in bot_names

    def _is_recent_chat_text_duplicate(self, now: float, message: ChatMessage) -> bool:
        window_seconds = max(self.settings.wake_chat_same_text_dedupe_ms, 0) / 1000
        if window_seconds <= 0:
            return False
        cutoff = now - window_seconds
        while self._recent_chat_texts and self._recent_chat_texts[0][0] < cutoff:
            self._recent_chat_texts.popleft()
        return any(
            sender == message.normalized_sender and text == message.normalized_text
            for _, sender, text in self._recent_chat_texts
        )

    def _remember_chat_text(self, now: float, message: ChatMessage) -> None:
        self._recent_chat_texts.append((now, message.normalized_sender, message.normalized_text))


def _chat_message_id(*, meeting_key: str, sender: str, text: str, timestamp_ms: int) -> str:
    rounded_timestamp = int(timestamp_ms / 1000) * 1000
    raw = "|".join([meeting_key, sender, text, str(rounded_timestamp)])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _chat_output_mode(text: str) -> str:
    if re.search(r"(声で|口頭で|読み上げて|みんなに話して)", text):
        return "both"
    return "chat"


def _voice_output_mode(text: str) -> str:
    if re.search(r"チャットにも(?:貼って|送って|返して|共有して|書いて)?", text):
        return "both"
    if re.search(
        r"(チャットで(?:返して|回答して|送って|教えて)|(?:チャットに|チャットへ)(?:貼って|送って|共有して|書いて|返して)|テキストで返して)",
        text,
    ):
        return "chat"
    return "voice"


def _format_chat_reply(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return "カボス:\n現時点では、返答できる内容がありません。"
    if stripped.startswith("カボス:") or stripped.startswith("カボス："):
        return stripped
    return f"カボス:\n{stripped}"


def _split_chat_text(text: str, max_chars: int) -> list[str]:
    limit = max(max_chars, 200)
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text.strip()
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        boundary = max(
            remaining.rfind("\n", 0, limit),
            remaining.rfind("。", 0, limit),
            remaining.rfind("、", 0, limit),
            remaining.rfind(" ", 0, limit),
        )
        if boundary < int(limit * 0.5):
            boundary = limit
        chunks.append(remaining[:boundary].strip())
        remaining = remaining[boundary:].strip()
    return [chunk for chunk in chunks if chunk]


def _extract_urls(text: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for match in re.findall(r"https?://[^\s<>\]\)\"']+", text):
        url = match.rstrip("、。,.!?！？")
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _meeting_log_key(meeting: MeetingRef | None) -> str:
    if meeting:
        return meeting.key
    return "default"


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _new_segment_text(previous: str | None, current: str) -> str:
    if not previous:
        return current

    if current.startswith(previous):
        return current[len(previous) :].strip()

    previous_norm = normalize_ja(previous)
    current_norm = normalize_ja(current)
    if previous_norm and current_norm.startswith(previous_norm):
        return ""

    return current


def _has_wake(text: str, settings: Settings) -> bool:
    return bool(detect_wake(text, settings.wake_words, settings.wake_negative_patterns))


def _wake_segment_keys(segment: TranscriptSegment) -> list[str]:
    keys: list[str] = []
    speaker = _speaker_stream_key(segment)
    if segment.segment_id:
        keys.append(f"segment:{speaker}:{segment.segment_id}")
    if segment.absolute_start_time:
        keys.append(f"start:{speaker}:{segment.absolute_start_time}")
    return keys


def _same_pending_wake_turn(pending: PendingWake, segment: TranscriptSegment) -> bool:
    pending_speaker = _speaker_stream_key(pending.segment)
    segment_speaker = _speaker_stream_key(segment)
    if pending_speaker != segment_speaker:
        return False

    pending_keys = set(_wake_segment_keys(pending.segment))
    segment_keys = set(_wake_segment_keys(segment))
    if pending_keys and segment_keys and pending_keys.intersection(segment_keys):
        return True

    pending_text = _wake_turn_fingerprint(pending.text)
    segment_text = _wake_turn_fingerprint(segment.text)
    return _same_wake_turn_text(pending_text, segment_text)


def _speaker_stream_key(segment: TranscriptSegment) -> str:
    return (
        segment.speaker_track_id
        or segment.speaker_session_uid
        or normalize_ja(segment.speaker)
        or segment.speaker
        or "unknown"
    )


def _speaker_context_label(segment: TranscriptSegment) -> str:
    status = (segment.speaker_mapping_status or "").upper()
    low_confidence = status in {
        "UNKNOWN",
        "NO_SPEAKER_EVENTS",
        "ERROR_IN_MAPPING",
        "MULTIPLE_CONCURRENT_SPEAKERS",
    }
    speaker = segment.speaker or "Unknown"

    label = "[話者不明]" if low_confidence else speaker
    details: list[str] = []
    if low_confidence and speaker and speaker != "Unknown":
        details.append(f"display={speaker}")
    if segment.speaker_session_uid:
        details.append(f"session_uid={segment.speaker_session_uid}")
    if segment.speaker_track_id:
        details.append(f"track_id={segment.speaker_track_id}")
    if status:
        details.append(f"speaker_mapping_status={status}")
    if details:
        return f"{label} ({', '.join(details)})"
    return label


def _wake_turn_fingerprint(text: str) -> str:
    normalized = normalize_ja(text)
    return normalized.replace("kabosu", "") or normalized


def _same_wake_turn_text(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True

    min_len = min(len(left), len(right))
    if min_len >= 4 and (left in right or right in left):
        return True
    if min_len >= 6 and SequenceMatcher(None, left, right).ratio() >= 0.72:
        return True
    return False


def _is_weak_command_text(text: str) -> bool:
    normalized = normalize_ja(text).replace("kabosu", "")
    return normalized in {"", "は", "を", "に", "へ", "で", "の", "って", "さん", "ちゃん"} or len(normalized) <= 1


def _audio_duration_seconds(result: TtsResult) -> float:
    if not result.audio:
        return 0.0

    if result.format == "wav":
        try:
            with wave.open(io.BytesIO(result.audio), "rb") as wav:
                rate = wav.getframerate()
                frames = wav.getnframes()
                if rate > 0:
                    duration = frames / rate
                    if 0 <= duration <= 120:
                        return duration
        except (EOFError, wave.Error):
            logger.debug("Could not parse wav duration; falling back to byte estimate")

    if result.sample_rate:
        # The Aivis path uses 16-bit mono PCM WAV. This fallback keeps the busy
        # window useful even if a containerized encoder returns a bad WAV header.
        return min(len(result.audio) / max(result.sample_rate * 2, 1), 120.0)
    return 0.0


def _short_log_text(text: str, limit: int = 160) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit].rstrip() + "..."


def _coerce_ms(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            return int(text)
        return _timestamp_ms(text)
    return None


def _timestamp_ms(value: str | int | float | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    try:
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        return int(datetime.fromisoformat(normalized).timestamp() * 1000)
    except ValueError:
        return None


def _delta_ms(start_ms: int | None, end_ms: int | None) -> int | None:
    if start_ms is None or end_ms is None:
        return None
    return int(end_ms - start_ms)


def _first_ms(raw: dict[str, Any], message: dict[str, Any], *names: str) -> int | None:
    for name in names:
        value = _coerce_ms(raw.get(name))
        if value is not None:
            return value
        value = _coerce_ms(message.get(name))
        if value is not None:
            return value
    return None


def _first_str(raw: dict[str, Any], message: dict[str, Any], *names: str) -> str | None:
    for name in names:
        value = raw.get(name)
        if value is None:
            value = message.get(name)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None
