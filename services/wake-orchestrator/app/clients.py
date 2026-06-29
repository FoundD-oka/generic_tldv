"""External API clients for Groq, Aivis Cloud, Vexa, and Vexa WebSocket."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

import httpx
import websockets

from .config import AudioFormat, Settings
from .text import add_safe_ssml_breaks, clean_for_tts

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TtsResult:
    audio: bytes
    format: AudioFormat
    sample_rate: int | None
    headers: dict[str, str]


@dataclass(frozen=True)
class SpeakAudioResponse:
    meeting_id: int | None
    request_id: str | None


@dataclass(frozen=True)
class MeetingRef:
    platform: str
    native_id: str
    meeting_id: int | None = None

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.native_id}"

    def as_subscribe_item(self) -> dict[str, str]:
        return {"platform": self.platform, "native_id": self.native_id}

    def as_message_value(self) -> dict[str, str | int]:
        value: dict[str, str | int] = {
            "platform": self.platform,
            "native_id": self.native_id,
        }
        if self.meeting_id is not None:
            value["meeting_id"] = self.meeting_id
        return value

    @classmethod
    def from_message(cls, value: Any) -> "MeetingRef | None":
        if not isinstance(value, dict):
            return None
        platform = value.get("platform")
        native_id = value.get("native_id") or value.get("native_meeting_id")
        if not platform or not native_id:
            return None
        meeting_id = _to_int(value.get("meeting_id"))
        return cls(platform=str(platform), native_id=str(native_id), meeting_id=meeting_id)


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ws_url(api_url: str, api_key: str) -> str:
    parsed = urlparse(api_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    query = urlencode({"api_key": api_key})
    return urlunparse((scheme, parsed.netloc, "/ws", "", query, ""))


def _wake_stt_ws_url(service_url: str, token: str | None = None) -> str:
    parsed = urlparse(service_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    query = urlencode({"token": token}) if token else ""
    return urlunparse((scheme, parsed.netloc, "/ws", "", query, ""))


class GroqClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        if not settings.groq_api_key:
            raise ValueError("GROQ_API_KEY is required")
        self._settings = settings
        self._client = client or httpx.AsyncClient(timeout=settings.groq_timeout_seconds)

    async def generate_reply(
        self,
        recent_context: str,
        wake_utterance: str,
        *,
        input_mode: str = "voice",
        output_mode: str = "voice",
    ) -> str:
        if output_mode == "voice":
            output_rule = (
                "回答は15〜25秒以内に読み上げられる長さにしてください。"
                "Markdown、表、長いURL、過剰な記号、SSMLタグは使わないでください。"
            )
        elif output_mode == "both":
            output_rule = (
                "チャットにも貼りやすく、口頭でも読める短い箇条書きにしてください。"
                "長いURLは必要な場合だけそのまま残してください。"
            )
        else:
            output_rule = (
                "Meetチャット欄で読みやすいように、短い段落か箇条書きで答えてください。"
                "長くても1000文字程度に収めてください。"
            )

        payload: dict[str, Any] = {
            "model": self._settings.groq_model,
            "temperature": 0.2,
            "max_completion_tokens": self._settings.groq_max_completion_tokens,
            "reasoning_format": "hidden",
            "stream": False,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "あなたはGoogle Meet内の音声・チャットアシスタント「カボス」です。"
                        "会議の音声文字起こし、Meetチャット欄、共有URL、抽出済みメモを使って、"
                        "現在のユーザー依頼に答えてください。"
                        "チャット欄は会議参加者が共有した正式な文脈です。"
                        "音声とチャットが矛盾する場合は、より新しい情報を優先してください。"
                        "URLや資料名は見えている範囲だけで扱ってください。"
                        "実際に中身を取得していないURLを読んだと言わないでください。"
                        "不明なことは断定せず、「現時点では」と言ってください。"
                        "現在の依頼を最優先し、回答後に不要な確認質問をしないでください。"
                        "日本語で短く、会議の流れを止めないように答えてください。"
                        f"{output_rule}"
                        "最後は必ず完結した一文で終えてください。"
                        "「ご質問があれば」「必要であれば」のような接続待ちの文末で終わらないでください。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "[現在の依頼]\n"
                        f"入力種別: {input_mode}\n"
                        f"出力先: {output_mode}\n"
                        f"{wake_utterance}\n\n"
                        "[会議コンテキスト]\n"
                        f"{recent_context}\n\n"
                        "[回答条件]\n"
                        "- 現在の依頼に直接答える\n"
                        "- チャット欄の情報を必要に応じて使う\n"
                        "- URLの中身を取得していない場合は、URLそのものと周辺文脈だけを根拠にする\n"
                        "- 回答後に不要な確認質問をしない\n\n"
                        "回答:"
                    ),
                },
            ],
        }
        response = await self._client.post(
            f"{self._settings.groq_api_base}/chat/completions",
            headers={
                "Authorization": f"Bearer {self._settings.groq_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        choice = data.get("choices", [{}])[0]
        text = choice.get("message", {}).get("content", "")
        finish_reason = choice.get("finish_reason")
        if finish_reason == "length" and (
            self._settings.groq_retry_max_completion_tokens
            > self._settings.groq_max_completion_tokens
        ):
            logger.warning(
                "Groq reply hit token limit; retrying with max_completion_tokens=%d",
                self._settings.groq_retry_max_completion_tokens,
            )
            payload["max_completion_tokens"] = self._settings.groq_retry_max_completion_tokens
            response = await self._client.post(
                f"{self._settings.groq_api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._settings.groq_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            choice = data.get("choices", [{}])[0]
            text = choice.get("message", {}).get("content", "")
            finish_reason = choice.get("finish_reason")

        if finish_reason == "length":
            logger.warning("Groq reply still ended by token limit after retry")
        if output_mode == "voice":
            return clean_for_tts(str(text), self._settings.max_speech_chars)
        return str(text).strip()


class AivisCloudClient:
    BILLING_HEADERS = (
        "content-disposition",
        "x-aivis-billing-mode",
        "x-aivis-character-count",
        "x-aivis-credits-used",
        "x-aivis-credits-remaining",
        "x-aivis-ratelimit-requests-limit",
        "x-aivis-ratelimit-requests-remaining",
        "x-aivis-ratelimit-requests-reset",
    )

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        if not settings.aivis_api_key:
            raise ValueError("AIVIS_API_KEY is required")
        self._settings = settings
        self._client = client or httpx.AsyncClient(timeout=settings.aivis_timeout_seconds)

    async def synthesize(self, text: str) -> TtsResult:
        output_format = self._settings.aivis_output_format
        speech_text = add_safe_ssml_breaks(text) if self._settings.aivis_use_ssml else text
        payload: dict[str, Any] = {
            "model_uuid": self._settings.aivis_model_uuid,
            "text": speech_text,
            "use_ssml": self._settings.aivis_use_ssml,
            "use_volume_normalizer": True,
            "language": "ja",
            "speaking_rate": 1.08,
            "emotional_intensity": 1.0,
            "tempo_dynamics": 1.0,
            "pitch": 0,
            "volume": 1.0,
            "leading_silence_seconds": self._settings.aivis_leading_silence_seconds,
            "trailing_silence_seconds": self._settings.aivis_trailing_silence_seconds,
            "line_break_silence_seconds": self._settings.aivis_line_break_silence_seconds,
            "output_format": output_format,
            "output_sampling_rate": self._settings.aivis_output_sampling_rate,
            "output_audio_channels": self._settings.aivis_output_audio_channels,
        }

        if output_format not in {"wav", "flac"}:
            payload["output_bitrate"] = self._settings.aivis_output_bitrate
        if self._settings.aivis_speaker_uuid:
            payload["speaker_uuid"] = self._settings.aivis_speaker_uuid
        if self._settings.aivis_style_name:
            payload["style_name"] = self._settings.aivis_style_name
        elif self._settings.aivis_style_id is not None:
            payload["style_id"] = self._settings.aivis_style_id
        if self._settings.aivis_user_dictionary_uuid:
            payload["user_dictionary_uuid"] = self._settings.aivis_user_dictionary_uuid

        response = await self._client.post(
            f"{self._settings.aivis_api_base}/tts/synthesize",
            headers={
                "Authorization": f"Bearer {self._settings.aivis_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()

        headers = {key.lower(): value for key, value in response.headers.items()}
        billing_headers = {key: headers[key] for key in self.BILLING_HEADERS if key in headers}
        if billing_headers:
            logger.info("Aivis response headers: %s", billing_headers)

        return TtsResult(
            audio=response.content,
            format=output_format,
            sample_rate=self._settings.aivis_output_sampling_rate,
            headers=headers,
        )


class VexaClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        if not settings.vexa_api_key:
            raise ValueError("VEXA_API_KEY is required")
        self._settings = settings
        self._client = client or httpx.AsyncClient(timeout=10.0)

    async def speak_audio(
        self,
        result: TtsResult,
        meeting: MeetingRef | None = None,
        request_id: str | None = None,
    ) -> SpeakAudioResponse:
        platform = meeting.platform if meeting else self._settings.vexa_platform
        native_id = meeting.native_id if meeting else self._settings.vexa_native_meeting_id
        if not native_id:
            raise ValueError("A native meeting id is required to send speech")

        payload: dict[str, Any] = {
            "audio_base64": base64.b64encode(result.audio).decode("ascii"),
            "format": result.format,
        }
        if result.sample_rate:
            payload["sample_rate"] = result.sample_rate
        if request_id:
            payload["request_id"] = request_id

        response = await self._client.post(
            (
                f"{self._settings.vexa_api_url}/bots/"
                f"{platform}/"
                f"{native_id}/speak"
            ),
            headers={
                "X-API-Key": self._settings.vexa_api_key,
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        meeting_id = meeting.meeting_id if meeting else None
        response_request_id = request_id
        try:
            data = response.json()
        except ValueError:
            data = {}
        if isinstance(data, dict):
            meeting_id = _to_int(data.get("meeting_id")) or meeting_id
            response_request_id = str(data.get("request_id") or request_id or "") or None
        return SpeakAudioResponse(meeting_id=meeting_id, request_id=response_request_id)

    async def voice_events(
        self,
        meeting: MeetingRef | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        platform = meeting.platform if meeting else self._settings.vexa_platform
        native_id = meeting.native_id if meeting else self._settings.vexa_native_meeting_id
        if not native_id:
            raise ValueError("A native meeting id is required to read voice events")

        response = await self._client.get(
            (
                f"{self._settings.vexa_api_url}/bots/"
                f"{platform}/"
                f"{native_id}/events"
            ),
            headers={"X-API-Key": self._settings.vexa_api_key},
            params={"limit": limit},
        )
        response.raise_for_status()
        data = response.json()
        events = data.get("events") if isinstance(data, dict) else []
        return [event for event in events if isinstance(event, dict)]

    async def chat_messages(self, meeting: MeetingRef | None = None) -> list[dict[str, Any]]:
        platform = meeting.platform if meeting else self._settings.vexa_platform
        native_id = meeting.native_id if meeting else self._settings.vexa_native_meeting_id
        if not native_id:
            raise ValueError("A native meeting id is required to read chat messages")

        response = await self._client.get(
            (
                f"{self._settings.vexa_api_url}/bots/"
                f"{platform}/"
                f"{native_id}/chat"
            ),
            headers={"X-API-Key": self._settings.vexa_api_key},
        )
        response.raise_for_status()
        data = response.json()
        messages = data.get("messages") if isinstance(data, dict) else []
        return [message for message in messages if isinstance(message, dict)]

    async def send_chat(self, text: str, meeting: MeetingRef | None = None) -> int | None:
        platform = meeting.platform if meeting else self._settings.vexa_platform
        native_id = meeting.native_id if meeting else self._settings.vexa_native_meeting_id
        if not native_id:
            raise ValueError("A native meeting id is required to send chat")

        response = await self._client.post(
            (
                f"{self._settings.vexa_api_url}/bots/"
                f"{platform}/"
                f"{native_id}/chat"
            ),
            headers={
                "X-API-Key": self._settings.vexa_api_key,
                "Content-Type": "application/json",
            },
            json={"text": text},
        )
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            data = {}
        if isinstance(data, dict):
            return _to_int(data.get("meeting_id")) or (meeting.meeting_id if meeting else None)
        return meeting.meeting_id if meeting else None

    async def wait_for_speech_to_finish(
        self,
        request_id: str,
        meeting: MeetingRef | None = None,
        timeout_seconds: float = 60.0,
        poll_interval_seconds: float = 0.25,
    ) -> str | None:
        deadline = time.monotonic() + max(timeout_seconds, 0.0)
        poll_interval_seconds = max(poll_interval_seconds, 0.05)
        terminal_events = {"speak.completed", "speak.error", "speak.interrupted"}
        saw_matching_start = False
        last_error: Exception | None = None

        while True:
            try:
                events = await self.voice_events(meeting, limit=100)
                last_error = None
                for event in events:
                    event_name = str(event.get("event") or "")
                    event_request_id = str(event.get("request_id") or "")
                    if event_request_id == request_id:
                        if event_name == "speak.started":
                            saw_matching_start = True
                            continue
                        if event_name in terminal_events:
                            return event_name
                    if event_name == "speak.interrupted" and saw_matching_start and not event_request_id:
                        return event_name
            except httpx.HTTPError as exc:
                last_error = exc

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if last_error is not None:
                    logger.warning(
                        "Timed out waiting for Vexa speech event request_id=%s last_error=%s",
                        request_id,
                        last_error,
                    )
                return None
            await asyncio.sleep(min(poll_interval_seconds, remaining))

    async def stop_speech(self, meeting: MeetingRef | None = None) -> None:
        platform = meeting.platform if meeting else self._settings.vexa_platform
        native_id = meeting.native_id if meeting else self._settings.vexa_native_meeting_id
        if not native_id:
            raise ValueError("A native meeting id is required to stop speech")

        response = await self._client.delete(
            (
                f"{self._settings.vexa_api_url}/bots/"
                f"{platform}/"
                f"{native_id}/speak"
            ),
            headers={"X-API-Key": self._settings.vexa_api_key},
        )
        response.raise_for_status()

    async def list_running_bots(self) -> list[MeetingRef]:
        response = await self._client.get(
            f"{self._settings.vexa_api_url}/bots/status",
            headers={"X-API-Key": self._settings.vexa_api_key or ""},
        )
        response.raise_for_status()
        data = response.json()
        refs: dict[str, MeetingRef] = {}
        for bot in data.get("running_bots") or []:
            if not isinstance(bot, dict):
                continue
            platform = bot.get("platform")
            native_id = bot.get("native_meeting_id") or bot.get("native_id")
            if not platform or not native_id:
                continue
            meeting_id = _to_int(bot.get("meeting_id") or bot.get("meeting_id_from_name"))
            ref = MeetingRef(str(platform), str(native_id), meeting_id=meeting_id)
            refs[ref.key] = ref
        return list(refs.values())


class VexaTranscriptSubscriber:
    def __init__(self, settings: Settings, vexa: VexaClient | None = None):
        if not settings.vexa_api_key:
            raise ValueError("VEXA_API_KEY is required")
        self._settings = settings
        self._vexa = vexa or VexaClient(settings)
        self._meeting_refs_by_id: dict[int, MeetingRef] = {}
        self._meeting_refs_by_key: dict[str, MeetingRef] = {}

    def _configured_meeting(self) -> MeetingRef | None:
        if not self._settings.vexa_native_meeting_id:
            return None
        return MeetingRef(
            platform=self._settings.vexa_platform,
            native_id=self._settings.vexa_native_meeting_id,
        )

    async def _discover_meetings(self) -> list[MeetingRef]:
        configured = self._configured_meeting()
        if configured:
            return [configured]
        if not self._settings.wake_auto_discover_bots:
            return []
        try:
            refs = await self._vexa.list_running_bots()
        except httpx.HTTPError as exc:
            logger.warning("Could not discover running Vexa bots: %s", exc)
            return []
        return refs

    async def _subscribe_new(self, ws: Any, subscribed_keys: set[str]) -> None:
        refs = await self._discover_meetings()
        stale_refs: list[MeetingRef] = []
        for ref in refs:
            previous = self._meeting_refs_by_key.get(ref.key)
            if (
                previous
                and previous.meeting_id is not None
                and ref.meeting_id is not None
                and previous.meeting_id != ref.meeting_id
            ):
                stale_refs.append(previous)
                subscribed_keys.discard(ref.key)
                self._meeting_refs_by_id.pop(previous.meeting_id, None)

        new_refs = [ref for ref in refs if ref.key not in subscribed_keys]
        if not new_refs and not stale_refs:
            return

        for ref in refs:
            self._meeting_refs_by_key[ref.key] = ref
            if ref.meeting_id is not None:
                self._meeting_refs_by_id[ref.meeting_id] = ref

        if stale_refs:
            await ws.send(
                json.dumps(
                    {
                        "action": "unsubscribe",
                        "meetings": [ref.as_subscribe_item() for ref in stale_refs],
                    },
                    ensure_ascii=False,
                )
            )
            logger.info(
                "Re-subscribing Vexa meetings after meeting id change: %s",
                [f"{old.key}:{old.meeting_id}" for old in stale_refs],
            )

        if not new_refs:
            return

        await ws.send(
            json.dumps(
                {"action": "subscribe", "meetings": [ref.as_subscribe_item() for ref in new_refs]},
                ensure_ascii=False,
            )
        )
        subscribed_keys.update(ref.key for ref in new_refs)
        logger.info("Subscribed to Vexa meetings: %s", [ref.key for ref in new_refs])

    def _enrich_message(self, message: dict[str, Any]) -> dict[str, Any]:
        if MeetingRef.from_message(message.get("_wake_meeting")):
            return message

        meeting = message.get("meeting")
        meeting_id = _to_int(message.get("meeting_id"))
        if meeting_id is None and isinstance(meeting, dict):
            meeting_id = _to_int(meeting.get("id"))
        ref = self._meeting_refs_by_id.get(meeting_id) if meeting_id is not None else None
        if not ref and isinstance(meeting, dict):
            ref = MeetingRef.from_message(meeting)
        if not ref:
            configured = self._configured_meeting()
            if configured:
                ref = configured
            elif len(self._meeting_refs_by_key) == 1:
                ref = next(iter(self._meeting_refs_by_key.values()))

        if not ref:
            return message
        enriched = dict(message)
        enriched["_wake_meeting"] = ref.as_message_value()
        return enriched

    async def messages(self):
        url = _ws_url(self._settings.vexa_api_url, self._settings.vexa_api_key or "")

        while True:
            try:
                logger.info("Connecting to Vexa transcript WebSocket: %s", url.split("api_key=")[0] + "api_key=***")
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    subscribed_keys: set[str] = set()
                    await self._subscribe_new(ws, subscribed_keys)

                    async def discovery_loop() -> None:
                        while True:
                            await asyncio.sleep(
                                max(self._settings.wake_discovery_interval_seconds, 1.0)
                            )
                            await self._subscribe_new(ws, subscribed_keys)

                    discovery_task = asyncio.create_task(discovery_loop())
                    try:
                        async for raw in ws:
                            try:
                                message = self._enrich_message(json.loads(raw))
                                message["_wake_websocket_received_ts_ms"] = int(time.time() * 1000)
                                yield message
                            except json.JSONDecodeError:
                                logger.warning("Ignoring invalid WebSocket JSON: %r", raw[:200])
                    finally:
                        discovery_task.cancel()
                        await asyncio.gather(discovery_task, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("WebSocket disconnected: %s; reconnecting", exc)
                await asyncio.sleep(max(self._settings.retry_delay_ms / 1000, 0.2))


class WakeSttTranscriptSubscriber:
    def __init__(self, settings: Settings):
        if not settings.wake_stt_url:
            raise ValueError("WAKE_STT_URL is required")
        self._settings = settings

    async def messages(self):
        url = _wake_stt_ws_url(self._settings.wake_stt_url or "", self._settings.wake_stt_token)

        while True:
            try:
                logger.info(
                    "Connecting to wake-stt WebSocket: %s",
                    url.split("token=")[0] + ("token=***" if "token=" in url else ""),
                )
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=20,
                ) as ws:
                    async for raw in ws:
                        try:
                            message = json.loads(raw)
                            message.setdefault("source", "wake-stt")
                            message["_wake_websocket_received_ts_ms"] = int(time.time() * 1000)
                            yield message
                        except json.JSONDecodeError:
                            logger.warning("Ignoring invalid wake-stt WebSocket JSON: %r", raw[:200])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("wake-stt WebSocket disconnected: %s; reconnecting", exc)
                await asyncio.sleep(max(self._settings.retry_delay_ms / 1000, 0.2))
