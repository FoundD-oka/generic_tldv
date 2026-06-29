"""Low-latency STT bridge for wake detection."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import re
import struct
import time
import uuid
import wave
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Optional

import httpx
from fastapi import HTTPException, WebSocket
from pydantic import BaseModel, Field

from .config import Settings

logger = logging.getLogger(__name__)

_PUNCT_RE = re.compile(r"[\s、。,.!?！？「」『』（）()【】\[\]〈〉<>:：;；]+")
_KABOSU_VARIANT_RE = (
    r"(?:(?:カ|か|コ|こ)[ーｰ]?(?:ボ|ぼ|ポ|ぽ|ホ|ほ|バ|ば)[ーｰ]?(?:ス|す|ズ|ず|酢)"
    r"|株酢|ｶﾎﾞｽ|カボちゃん|かぼちゃん|カーブス|かーぶす|kabosu|kabos|kavos|koposu|kopos|qabo?s)"
    r"(?:さん|ちゃん)?"
)
_LEADING_COMMAND_PARTICLE_RE = re.compile(
    r"^[\s、。,.!?！？]*(?:は|を|に|へ|で|って|の|さん|ちゃん)?[\s、。,.!?！？]*"
)
_WEAK_COMMANDS = {"", "は", "を", "に", "へ", "で", "の", "って", "さん", "ちゃん"}
_WEAK_FAST_COMMANDS = _WEAK_COMMANDS | {"これ", "それ", "あれ", "えっと", "あの", "うーん"}
_FAST_COMMAND_KEYWORDS = (
    "教えて",
    "まとめ",
    "要約",
    "わかる",
    "分かる",
    "どう思う",
    "できる",
    "して",
    "お願い",
    "?",
    "？",
)


class AudioIngest(BaseModel):
    platform: str
    native_meeting_id: Optional[str] = None
    meeting_id: Optional[int] = None
    speaker_id: str
    speaker: str = "Unknown"
    sample_rate: int = 16000
    audio_format: str = "f32le"
    audio_base64: str
    captured_at_ms: int = Field(default_factory=lambda: int(time.time() * 1000))
    duration_ms: Optional[int] = None
    wake_trace_id: Optional[str] = None
    bot_audio_received_ts_ms: Optional[int] = None
    audio_chunk_sent_to_stt_ts_ms: Optional[int] = None


@dataclass
class AudioChunk:
    data: bytes
    samples: int
    captured_at_ms: int
    wake_trace_id: str | None = None
    bot_audio_received_ts_ms: int | None = None
    audio_chunk_sent_to_stt_ts_ms: int | None = None
    wake_stt_ingest_ts_ms: int = 0


@dataclass
class SpeakerState:
    platform: str
    native_meeting_id: str | None
    meeting_id: int | None
    speaker_id: str
    speaker: str
    chunks: Deque[AudioChunk] = field(default_factory=deque)
    utterance_chunks: Deque[AudioChunk] = field(default_factory=deque)
    total_samples: int = 0
    utterance_samples: int = 0
    first_captured_at_ms: int = 0
    wake_trace_id: str | None = None
    bot_audio_received_ts_ms: int | None = None
    audio_chunk_sent_to_stt_ts_ms: int | None = None
    wake_stt_ingest_ts_ms: int | None = None
    last_ingest_ms: int = 0
    last_submit_ms: int = 0
    sequence: int = 0
    in_flight: bool = False
    active_session_id: str | None = None
    active_wake: str | None = None
    active_started_ms: int = 0
    active_detected_ts_ms: int = 0
    active_finalizing: bool = False
    finalize_task: asyncio.Task[None] | None = None
    fast_command_task: asyncio.Task[None] | None = None
    fast_command_candidate: dict[str, Any] | None = None
    fast_command_norm: str | None = None
    fast_command_published: bool = False

    @property
    def meeting_key(self) -> str:
        native = self.native_meeting_id or str(self.meeting_id or "unknown")
        return f"{self.platform}:{native}"


class BroadcastHub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def add(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.add(websocket)

    async def remove(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(websocket)

    async def publish(self, message: dict[str, Any]) -> None:
        async with self._lock:
            clients = list(self._clients)
        if not clients:
            return
        stale: list[WebSocket] = []
        for websocket in clients:
            try:
                await websocket.send_json(message)
            except Exception:
                stale.append(websocket)
        if stale:
            async with self._lock:
                for websocket in stale:
                    self._clients.discard(websocket)


class WakeSttService:
    def __init__(
        self,
        settings: Settings,
        hub: BroadcastHub,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.hub = hub
        self.client = client or httpx.AsyncClient(timeout=settings.request_timeout_seconds)
        self.states: dict[str, SpeakerState] = {}
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        async with self._lock:
            tasks = []
            for state in self.states.values():
                for task in (state.finalize_task, state.fast_command_task):
                    if task and not task.done():
                        tasks.append(task)
            for task in tasks:
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.client.aclose()

    async def ingest(self, payload: AudioIngest) -> dict[str, Any]:
        if not self.settings.transcription_url:
            raise HTTPException(status_code=503, detail="WAKE_STT_TRANSCRIPTION_URL is not configured")
        if payload.audio_format != "f32le":
            raise HTTPException(status_code=415, detail="Only f32le audio is supported")
        if payload.sample_rate != self.settings.sample_rate:
            raise HTTPException(
                status_code=400,
                detail=f"Expected sample_rate={self.settings.sample_rate}, got {payload.sample_rate}",
            )

        try:
            audio = base64.b64decode(payload.audio_base64)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid audio_base64") from exc
        if len(audio) % 4 != 0:
            raise HTTPException(status_code=400, detail="f32le audio length must be divisible by 4")

        samples = len(audio) // 4
        now_ms = int(time.time() * 1000)
        state_key = self._state_key(payload)

        should_submit = False
        snapshot: tuple[SpeakerState, bytes, int, int, int] | None = None
        async with self._lock:
            state = self.states.get(state_key)
            if state is None:
                state = SpeakerState(
                    platform=payload.platform,
                    native_meeting_id=payload.native_meeting_id,
                    meeting_id=payload.meeting_id,
                    speaker_id=payload.speaker_id,
                    speaker=payload.speaker or "Unknown",
                    first_captured_at_ms=payload.captured_at_ms,
                    wake_trace_id=payload.wake_trace_id,
                    bot_audio_received_ts_ms=payload.bot_audio_received_ts_ms,
                    audio_chunk_sent_to_stt_ts_ms=payload.audio_chunk_sent_to_stt_ts_ms,
                    wake_stt_ingest_ts_ms=now_ms,
                )
                self.states[state_key] = state

            if state.last_ingest_ms and now_ms - state.last_ingest_ms > self.settings.idle_reset_ms:
                self._reset_for_new_utterance(state, payload, now_ms)

            state.platform = payload.platform
            state.native_meeting_id = payload.native_meeting_id
            state.meeting_id = payload.meeting_id
            state.speaker = payload.speaker or state.speaker
            if not state.first_captured_at_ms:
                state.first_captured_at_ms = payload.captured_at_ms
            if not state.wake_trace_id:
                state.wake_trace_id = payload.wake_trace_id
            if not state.bot_audio_received_ts_ms:
                state.bot_audio_received_ts_ms = payload.bot_audio_received_ts_ms
            if not state.audio_chunk_sent_to_stt_ts_ms:
                state.audio_chunk_sent_to_stt_ts_ms = payload.audio_chunk_sent_to_stt_ts_ms
            if not state.wake_stt_ingest_ts_ms:
                state.wake_stt_ingest_ts_ms = now_ms
            state.last_ingest_ms = now_ms
            chunk = AudioChunk(
                audio,
                samples,
                payload.captured_at_ms,
                wake_trace_id=payload.wake_trace_id,
                bot_audio_received_ts_ms=payload.bot_audio_received_ts_ms,
                audio_chunk_sent_to_stt_ts_ms=payload.audio_chunk_sent_to_stt_ts_ms,
                wake_stt_ingest_ts_ms=now_ms,
            )
            state.chunks.append(chunk)
            state.utterance_chunks.append(_copy_chunk(chunk))
            state.total_samples += samples
            state.utterance_samples += samples
            self._trim_to_max_window(state)
            self._trim_to_turn_window(state)
            if state.active_session_id:
                self._schedule_finalize_locked(state_key, state)

            window_ms = state.total_samples / self.settings.sample_rate * 1000
            since_submit_ms = now_ms - state.last_submit_ms
            if (
                not state.in_flight
                and window_ms >= self.settings.min_window_ms
                and since_submit_ms >= self.settings.submit_interval_ms
            ):
                state.in_flight = True
                state.last_submit_ms = now_ms
                should_submit = True
                snapshot = self._snapshot(state)

        logger.info(
            "wake-stt ingest speaker=%s trace=%s samples=%d duration_ms=%d queued=%s",
            payload.speaker or payload.speaker_id,
            payload.wake_trace_id or "-",
            samples,
            payload.duration_ms or int(samples / self.settings.sample_rate * 1000),
            should_submit,
        )
        if should_submit and snapshot:
            asyncio.create_task(self._transcribe_snapshot(state_key, snapshot))
        return {"ok": True, "queued": should_submit}

    def _state_key(self, payload: AudioIngest) -> str:
        native = payload.native_meeting_id or str(payload.meeting_id or "unknown")
        return f"{payload.platform}:{native}:{payload.speaker_id}"

    def _reset_for_new_utterance(self, state: SpeakerState, payload: AudioIngest, now_ms: int) -> None:
        state.chunks.clear()
        state.utterance_chunks.clear()
        state.total_samples = 0
        state.utterance_samples = 0
        state.first_captured_at_ms = payload.captured_at_ms
        state.wake_trace_id = payload.wake_trace_id
        state.bot_audio_received_ts_ms = payload.bot_audio_received_ts_ms
        state.audio_chunk_sent_to_stt_ts_ms = payload.audio_chunk_sent_to_stt_ts_ms
        state.wake_stt_ingest_ts_ms = now_ms
        state.sequence += 1
        state.last_submit_ms = 0
        state.in_flight = False
        state.active_session_id = None
        state.active_wake = None
        state.active_started_ms = 0
        state.active_detected_ts_ms = 0
        state.active_finalizing = False
        if state.finalize_task and not state.finalize_task.done():
            state.finalize_task.cancel()
        state.finalize_task = None
        self._clear_fast_command_locked(state)

    def _trim_to_max_window(self, state: SpeakerState) -> None:
        max_samples = int(self.settings.sample_rate * self.settings.max_window_ms / 1000)
        while state.total_samples > max_samples and state.chunks:
            chunk = state.chunks[0]
            overflow = state.total_samples - max_samples
            if overflow >= chunk.samples:
                state.chunks.popleft()
                state.total_samples -= chunk.samples
                if state.chunks:
                    self._sync_state_timing_from_first_chunk(state)
                continue

            bytes_to_drop = overflow * 4
            duration_ms = int(overflow / self.settings.sample_rate * 1000)
            chunk.data = chunk.data[bytes_to_drop:]
            chunk.samples -= overflow
            chunk.captured_at_ms += duration_ms
            state.total_samples -= overflow
            self._sync_state_timing_from_first_chunk(state)
            break

    def _trim_to_turn_window(self, state: SpeakerState) -> None:
        max_ms = max(self.settings.turn_max_ms + self.settings.turn_preroll_ms, self.settings.max_window_ms)
        max_samples = int(self.settings.sample_rate * max_ms / 1000)
        while state.utterance_samples > max_samples and state.utterance_chunks:
            chunk = state.utterance_chunks[0]
            overflow = state.utterance_samples - max_samples
            if overflow >= chunk.samples:
                state.utterance_chunks.popleft()
                state.utterance_samples -= chunk.samples
                continue

            bytes_to_drop = overflow * 4
            duration_ms = int(overflow / self.settings.sample_rate * 1000)
            chunk.data = chunk.data[bytes_to_drop:]
            chunk.samples -= overflow
            chunk.captured_at_ms += duration_ms
            state.utterance_samples -= overflow
            break

    def _sync_state_timing_from_first_chunk(self, state: SpeakerState) -> None:
        if not state.chunks:
            return
        chunk = state.chunks[0]
        state.first_captured_at_ms = chunk.captured_at_ms
        state.wake_trace_id = chunk.wake_trace_id
        state.bot_audio_received_ts_ms = chunk.bot_audio_received_ts_ms
        state.audio_chunk_sent_to_stt_ts_ms = chunk.audio_chunk_sent_to_stt_ts_ms
        state.wake_stt_ingest_ts_ms = chunk.wake_stt_ingest_ts_ms or state.wake_stt_ingest_ts_ms

    def _snapshot(self, state: SpeakerState) -> tuple[SpeakerState, bytes, int, int, int]:
        audio = b"".join(chunk.data for chunk in state.chunks)
        start_ms = state.first_captured_at_ms
        duration_ms = int(state.total_samples / self.settings.sample_rate * 1000)
        sequence = state.sequence
        meta = SpeakerState(
            platform=state.platform,
            native_meeting_id=state.native_meeting_id,
            meeting_id=state.meeting_id,
            speaker_id=state.speaker_id,
            speaker=state.speaker,
            first_captured_at_ms=state.first_captured_at_ms,
            wake_trace_id=state.wake_trace_id,
            bot_audio_received_ts_ms=state.bot_audio_received_ts_ms,
            audio_chunk_sent_to_stt_ts_ms=state.audio_chunk_sent_to_stt_ts_ms,
            wake_stt_ingest_ts_ms=state.wake_stt_ingest_ts_ms,
            sequence=state.sequence,
        )
        return (meta, audio, start_ms, duration_ms, sequence)

    async def _transcribe_snapshot(
        self,
        state_key: str,
        snapshot: tuple[SpeakerState, bytes, int, int, int],
    ) -> None:
        meta, audio, start_ms, duration_ms, sequence = snapshot
        try:
            stt_request_start_ts_ms = int(time.time() * 1000)
            logger.info(
                "wake-stt transcribe speaker=%s trace=%s sequence=%d duration_ms=%d bytes=%d",
                meta.speaker,
                meta.wake_trace_id or "-",
                sequence,
                duration_ms,
                len(audio),
            )
            text, segments, language = await self._transcribe(audio)
            stt_response_ts_ms = int(time.time() * 1000)
            clean_text = text.strip()
            if self.settings.log_transcripts:
                logger.info(
                    "wake-stt transcript speaker=%s trace=%s sequence=%d duration_ms=%d stt_request_ms=%d chars=%d text=%s",
                    meta.speaker,
                    meta.wake_trace_id or "-",
                    sequence,
                    duration_ms,
                    stt_response_ts_ms - stt_request_start_ts_ms,
                    len(clean_text),
                    _short_log_text(clean_text),
                )
            if clean_text:
                publish_ts_ms = int(time.time() * 1000)
                event = self._event_from_result(
                    meta,
                    clean_text,
                    segments,
                    language,
                    start_ms,
                    duration_ms,
                    sequence,
                    stt_request_start_ts_ms,
                    stt_response_ts_ms,
                    publish_ts_ms,
                )
                pending_text = " | ".join(
                    str(segment.get("text") or "").strip()
                    for segment in event.get("pending", [])
                    if str(segment.get("text") or "").strip()
                )
                logger.info(
                    "wake-stt publish speaker=%s trace=%s sequence=%d pending=%d text=%s",
                    meta.speaker,
                    meta.wake_trace_id or "-",
                    sequence,
                    len(event.get("pending", [])),
                    _short_log_text(pending_text),
                )
                await self.hub.publish(event)
                wake = _detect_wake(clean_text)
                if wake:
                    await self._arm_turn(state_key, meta, wake, stt_response_ts_ms)
                await self._maybe_schedule_fast_command(
                    state_key,
                    meta,
                    clean_text,
                    segments,
                    language,
                    start_ms,
                    duration_ms,
                    sequence,
                    stt_request_start_ts_ms,
                    stt_response_ts_ms,
                    publish_ts_ms,
                )
        except Exception as exc:
            logger.warning("wake-stt transcription failed speaker=%s: %s", meta.speaker, exc)
        finally:
            async with self._lock:
                state = self.states.get(state_key)
                if state:
                    state.in_flight = False

    async def _arm_turn(
        self,
        state_key: str,
        meta: SpeakerState,
        wake: str,
        detected_ts_ms: int,
    ) -> None:
        async with self._lock:
            state = self.states.get(state_key)
            if state is None:
                return
            if state.active_session_id:
                logger.info(
                    "wake-stt turn already active speaker=%s session=%s wake=%s text_wake=%s",
                    state.speaker,
                    state.active_session_id,
                    state.active_wake or "-",
                    wake,
                )
                return

            session_id = f"wake-turn-{uuid.uuid4().hex}"
            state.active_session_id = session_id
            state.active_wake = wake
            state.active_started_ms = int(time.time() * 1000)
            state.active_detected_ts_ms = detected_ts_ms
            state.active_finalizing = False
            self._clear_fast_command_locked(state)
            self._schedule_finalize_locked(state_key, state)

        logger.info(
            "wake-stt turn armed speaker=%s session=%s wake=%s trace=%s",
            meta.speaker,
            session_id,
            wake,
            meta.wake_trace_id or "-",
        )

    def _schedule_finalize_locked(self, state_key: str, state: SpeakerState) -> None:
        if not state.active_session_id or state.active_finalizing:
            return

        now_ms = int(time.time() * 1000)
        elapsed_ms = max(0, now_ms - state.active_started_ms)
        max_remaining_ms = max(0, self.settings.turn_max_ms - elapsed_ms)
        delay_ms = min(max(self.settings.turn_silence_ms, 0), max_remaining_ms)
        if state.finalize_task and not state.finalize_task.done():
            state.finalize_task.cancel()
        state.finalize_task = asyncio.create_task(
            self._finalize_turn_after_delay(state_key, state.active_session_id, delay_ms / 1000)
        )

    async def _maybe_schedule_fast_command(
        self,
        state_key: str,
        meta: SpeakerState,
        text: str,
        segments: list[dict[str, Any]],
        language: str,
        start_ms: int,
        duration_ms: int,
        sequence: int,
        stt_request_start_ts_ms: int,
        stt_response_ts_ms: int,
        publish_ts_ms: int,
    ) -> None:
        command_text = _command_text_from_wake_text(text)
        if not _is_fast_command_candidate(command_text):
            return

        async with self._lock:
            state = self.states.get(state_key)
            if (
                state is None
                or not state.active_session_id
                or state.active_finalizing
                or state.fast_command_published
            ):
                return

            norm = _normalize_command(command_text)
            state.fast_command_candidate = {
                "meta": meta,
                "session_id": state.active_session_id,
                "wake": state.active_wake or "カボス",
                "text": text,
                "command_text": command_text,
                "segments": segments,
                "language": language,
                "start_ms": start_ms,
                "duration_ms": duration_ms,
                "sequence": sequence,
                "wake_detected_ts_ms": state.active_detected_ts_ms,
                "stt_request_start_ts_ms": stt_request_start_ts_ms,
                "stt_response_ts_ms": stt_response_ts_ms,
                "publish_ts_ms": publish_ts_ms,
            }
            state.fast_command_norm = norm
            if state.fast_command_task and not state.fast_command_task.done():
                state.fast_command_task.cancel()
            delay_ms = max(0, self.settings.fast_command_stability_ms)
            state.fast_command_task = asyncio.create_task(
                self._publish_fast_command_after_delay(
                    state_key,
                    state.active_session_id,
                    norm,
                    delay_ms / 1000,
                )
            )

    async def _publish_fast_command_after_delay(
        self,
        state_key: str,
        session_id: str,
        command_norm: str,
        delay_seconds: float,
    ) -> None:
        try:
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)
            async with self._lock:
                state = self.states.get(state_key)
                if (
                    state is None
                    or state.active_session_id != session_id
                    or state.active_finalizing
                    or state.fast_command_published
                    or state.fast_command_norm != command_norm
                    or not state.fast_command_candidate
                ):
                    return
                candidate = dict(state.fast_command_candidate)
                state.fast_command_published = True

            event = self._command_event_from_result(
                candidate["meta"],
                candidate["session_id"],
                candidate["wake"],
                candidate["text"],
                candidate["command_text"],
                candidate["segments"],
                candidate["language"],
                candidate["start_ms"],
                candidate["duration_ms"],
                candidate["sequence"],
                candidate["wake_detected_ts_ms"],
                candidate["stt_request_start_ts_ms"],
                candidate["stt_response_ts_ms"],
                int(time.time() * 1000),
                event_name="command.partial",
            )
            logger.info(
                "wake-stt command partial speaker=%s session=%s wake=%s command_chars=%d command=%s stability_ms=%d",
                candidate["meta"].speaker,
                candidate["session_id"],
                candidate["wake"],
                len(event.get("command_text") or ""),
                _short_log_text(str(event.get("command_text") or "")),
                self.settings.fast_command_stability_ms,
            )
            await self.hub.publish(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("wake-stt fast command publish failed session=%s", session_id)

    async def _finalize_turn_after_delay(self, state_key: str, session_id: str, delay_seconds: float) -> None:
        try:
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)
            await self._finalize_turn(state_key, session_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("wake-stt turn finalize failed session=%s", session_id)

    async def _finalize_turn(self, state_key: str, session_id: str) -> None:
        async with self._lock:
            state = self.states.get(state_key)
            if state is None or state.active_session_id != session_id or state.active_finalizing:
                return
            state.active_finalizing = True
            if state.fast_command_task and not state.fast_command_task.done():
                state.fast_command_task.cancel()
            state.fast_command_task = None
            meta, audio, start_ms, duration_ms, sequence = self._turn_snapshot(state)
            wake = state.active_wake or "カボス"
            detected_ts_ms = state.active_detected_ts_ms

        stt_request_start_ts_ms = int(time.time() * 1000)
        logger.info(
            "wake-stt turn finalizing speaker=%s session=%s wake=%s duration_ms=%d bytes=%d",
            meta.speaker,
            session_id,
            wake,
            duration_ms,
            len(audio),
        )
        try:
            text, segments, language = await self._transcribe(audio)
            stt_response_ts_ms = int(time.time() * 1000)
            clean_text = text.strip()
            command_text = _command_text_from_wake_text(clean_text)
            publish_ts_ms = int(time.time() * 1000)
            event = self._command_event_from_result(
                meta,
                session_id,
                wake,
                clean_text,
                command_text,
                segments,
                language,
                start_ms,
                duration_ms,
                sequence,
                detected_ts_ms,
                stt_request_start_ts_ms,
                stt_response_ts_ms,
                publish_ts_ms,
            )
            logger.info(
                "wake-stt command final speaker=%s session=%s wake=%s chars=%d command_chars=%d text=%s command=%s",
                meta.speaker,
                session_id,
                wake,
                len(clean_text),
                len(command_text),
                _short_log_text(clean_text),
                _short_log_text(command_text),
            )
            await self.hub.publish(event)
        finally:
            async with self._lock:
                state = self.states.get(state_key)
                if state and state.active_session_id == session_id:
                    state.active_session_id = None
                    state.active_wake = None
                    state.active_started_ms = 0
                    state.active_detected_ts_ms = 0
                    state.active_finalizing = False
                    state.finalize_task = None
                    self._clear_fast_command_locked(state, cancel_task=False)
                    state.utterance_chunks.clear()
                    state.utterance_samples = 0

    def _clear_fast_command_locked(self, state: SpeakerState, *, cancel_task: bool = True) -> None:
        if cancel_task and state.fast_command_task and not state.fast_command_task.done():
            state.fast_command_task.cancel()
        state.fast_command_task = None
        state.fast_command_candidate = None
        state.fast_command_norm = None
        state.fast_command_published = False

    def _turn_snapshot(self, state: SpeakerState) -> tuple[SpeakerState, bytes, int, int, int]:
        audio = b"".join(chunk.data for chunk in state.utterance_chunks)
        start_ms = state.utterance_chunks[0].captured_at_ms if state.utterance_chunks else state.first_captured_at_ms
        duration_ms = int(state.utterance_samples / self.settings.sample_rate * 1000)
        meta = SpeakerState(
            platform=state.platform,
            native_meeting_id=state.native_meeting_id,
            meeting_id=state.meeting_id,
            speaker_id=state.speaker_id,
            speaker=state.speaker,
            first_captured_at_ms=start_ms,
            wake_trace_id=state.wake_trace_id,
            bot_audio_received_ts_ms=state.bot_audio_received_ts_ms,
            audio_chunk_sent_to_stt_ts_ms=state.audio_chunk_sent_to_stt_ts_ms,
            wake_stt_ingest_ts_ms=state.wake_stt_ingest_ts_ms,
            sequence=state.sequence,
        )
        return (meta, audio, start_ms, duration_ms, state.sequence)

    async def _transcribe(self, f32le_audio: bytes) -> tuple[str, list[dict[str, Any]], str]:
        wav = f32le_to_wav(f32le_audio, self.settings.sample_rate)
        headers: dict[str, str] = {"X-Transcription-Tier": "realtime"}
        if self.settings.transcription_token:
            headers["Authorization"] = f"Bearer {self.settings.transcription_token}"
        data: dict[str, str] = {
            "model": "whisper-1",
            "response_format": "verbose_json",
            "timestamp_granularities": "segment",
            "transcription_tier": "realtime",
            "max_speech_duration_s": str(self.settings.max_speech_duration_s),
            "min_silence_duration_ms": str(self.settings.min_silence_duration_ms),
        }
        if self.settings.language:
            data["language"] = self.settings.language

        response = await self.client.post(
            self.settings.transcription_url or "",
            headers=headers,
            data=data,
            files={"file": ("wake.wav", wav, "audio/wav")},
        )
        response.raise_for_status()
        body = response.json()
        return (
            str(body.get("text") or ""),
            list(body.get("segments") or []),
            str(body.get("language") or self.settings.language or "unknown"),
        )

    def _event_from_result(
        self,
        meta: SpeakerState,
        text: str,
        segments: list[dict[str, Any]],
        language: str,
        start_ms: int,
        duration_ms: int,
        sequence: int,
        stt_request_start_ts_ms: int,
        stt_response_ts_ms: int,
        publish_ts_ms: int,
    ) -> dict[str, Any]:
        end_ms = start_ms + duration_ms
        segment_id = f"wake-stt:{meta.meeting_key}:{meta.speaker_id}:{sequence}"
        pending = []
        timing = {
            "wake_trace_id": meta.wake_trace_id,
            "bot_audio_received_ts_ms": meta.bot_audio_received_ts_ms,
            "audio_chunk_sent_to_stt_ts_ms": meta.audio_chunk_sent_to_stt_ts_ms,
            "wake_stt_ingest_ts_ms": meta.wake_stt_ingest_ts_ms,
            "stt_request_start_ts_ms": stt_request_start_ts_ms,
            "stt_response_ts_ms": stt_response_ts_ms,
            "transcript_mutable_publish_ts_ms": publish_ts_ms,
        }
        source_segments = segments or [{"text": text, "start": 0.0, "end": duration_ms / 1000}]
        for index, segment in enumerate(source_segments):
            segment_text = str(segment.get("text") or "").strip()
            if not segment_text:
                continue
            seg_start_ms = start_ms + int(float(segment.get("start") or 0.0) * 1000)
            seg_end_ms = start_ms + int(float(segment.get("end") or duration_ms / 1000) * 1000)
            pending.append(
                {
                    "speaker": meta.speaker,
                    "text": segment_text,
                    "start": max(0.0, (seg_start_ms - start_ms) / 1000),
                    "end": max(0.0, (seg_end_ms - start_ms) / 1000),
                    "language": language,
                    "completed": False,
                    "segment_id": f"{segment_id}:{index}",
                    "absolute_start_time": _iso_ms(seg_start_ms),
                    "absolute_end_time": _iso_ms(seg_end_ms),
                    **timing,
                }
            )
        if not pending:
            pending.append(
                {
                    "speaker": meta.speaker,
                    "text": text,
                    "start": 0,
                    "end": duration_ms / 1000,
                    "language": language,
                    "completed": False,
                    "segment_id": f"{segment_id}:0",
                    "absolute_start_time": _iso_ms(start_ms),
                    "absolute_end_time": _iso_ms(end_ms),
                    **timing,
                }
            )
        return {
            "type": "transcript",
            "event": "transcript.partial",
            "source": "wake-stt",
            "speaker": meta.speaker,
            "confirmed": [],
            "pending": pending,
            **timing,
            "_wake_meeting": {
                "platform": meta.platform,
                "native_id": meta.native_meeting_id or str(meta.meeting_id or ""),
                "meeting_id": meta.meeting_id,
            },
        }

    def _command_event_from_result(
        self,
        meta: SpeakerState,
        session_id: str,
        wake: str,
        text: str,
        command_text: str,
        segments: list[dict[str, Any]],
        language: str,
        start_ms: int,
        duration_ms: int,
        sequence: int,
        wake_detected_ts_ms: int,
        stt_request_start_ts_ms: int,
        stt_response_ts_ms: int,
        publish_ts_ms: int,
        event_name: str = "command.final",
    ) -> dict[str, Any]:
        event = self._event_from_result(
            meta,
            text,
            segments,
            language,
            start_ms,
            duration_ms,
            sequence,
            stt_request_start_ts_ms,
            stt_response_ts_ms,
            publish_ts_ms,
        )
        event.update(
            {
                "type": event_name,
                "event": event_name,
                "session_id": session_id,
                "wake": wake,
                "text": text,
                "command_text": "" if _is_weak_command(command_text) else command_text,
                "wake_detected_ts_ms": wake_detected_ts_ms,
            }
        )
        return event


def f32le_to_wav(audio: bytes, sample_rate: int) -> bytes:
    sample_count = len(audio) // 4
    floats = struct.unpack(f"<{sample_count}f", audio) if sample_count else []
    pcm = bytearray(sample_count * 2)
    for index, sample in enumerate(floats):
        clamped = max(-1.0, min(1.0, float(sample)))
        value = int(clamped * 32767)
        struct.pack_into("<h", pcm, index * 2, value)

    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(bytes(pcm))
    return output.getvalue()


def _copy_chunk(chunk: AudioChunk) -> AudioChunk:
    return AudioChunk(
        data=chunk.data,
        samples=chunk.samples,
        captured_at_ms=chunk.captured_at_ms,
        wake_trace_id=chunk.wake_trace_id,
        bot_audio_received_ts_ms=chunk.bot_audio_received_ts_ms,
        audio_chunk_sent_to_stt_ts_ms=chunk.audio_chunk_sent_to_stt_ts_ms,
        wake_stt_ingest_ts_ms=chunk.wake_stt_ingest_ts_ms,
    )


def _normalize_command(text: str) -> str:
    normalized = text.lower().replace("ｶﾎﾞｽ", "カボス")
    normalized = re.sub(_KABOSU_VARIANT_RE, "kabosu", normalized, flags=re.IGNORECASE)
    return _PUNCT_RE.sub("", normalized)


def _detect_wake(text: str) -> str | None:
    match = list(re.finditer(_KABOSU_VARIANT_RE, text, flags=re.IGNORECASE))
    if not match:
        return None
    return match[-1].group(0)


def _command_text_from_wake_text(text: str) -> str:
    if not text.strip():
        return ""
    stripped = re.sub(_KABOSU_VARIANT_RE, "", text, flags=re.IGNORECASE)
    stripped = _LEADING_COMMAND_PARTICLE_RE.sub("", stripped)
    return stripped.strip(" \t\r\n、。,.")


def _is_weak_command(text: str) -> bool:
    normalized = _normalize_command(text)
    return normalized in _WEAK_COMMANDS or len(normalized) <= 1


def _is_fast_command_candidate(text: str) -> bool:
    normalized = _normalize_command(text)
    if normalized in _WEAK_FAST_COMMANDS or len(normalized) <= 1:
        return False
    if any(keyword in text for keyword in _FAST_COMMAND_KEYWORDS):
        return True
    return len(normalized) >= 4


def _iso_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _short_log_text(text: str, limit: int = 160) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit].rstrip() + "..."
