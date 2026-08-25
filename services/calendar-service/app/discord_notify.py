"""Drive export 完了 → Discord チャンネル自動選択通知。

meeting-api が `drive_export.completed` フックを内部エンドポイントへ送り、この
モジュールが Guild のチャンネル一覧を毎回取得してモデルに投稿先を選ばせる。
モデルが使えない・確信度が低い・候補外 id を返した場合は default チャンネルへ
フォールバックする。設定が空のときは何も呼ばず skipped を返す。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import attributes

from meeting_api.models import Meeting

logger = logging.getLogger("calendar-service.discord_notify")

# Discord channel types that accept a normal message post.
GUILD_TEXT = 0
GUILD_ANNOUNCEMENT = 5
GUILD_CATEGORY = 4
POSTABLE_CHANNEL_TYPES = {GUILD_TEXT, GUILD_ANNOUNCEMENT}

DISCORD_MESSAGE_LIMIT = 2000


class DiscordHTTPError(Exception):
    """Non-2xx response from the Discord API."""

    def __init__(self, status_code: int, body: str = ""):
        super().__init__(f"Discord API returned {status_code}: {body[:300]}")
        self.status_code = status_code
        self.body = body


class DiscordDeliveryError(Exception):
    """The notification could not be delivered anywhere (caller should retry)."""


# ---------------------------------------------------------------------------
# Inbound webhook verification (same algorithm as meeting_api.webhook_delivery)
# ---------------------------------------------------------------------------


def verify_webhook_signature(
    raw_body: bytes,
    signature_header: Optional[str],
    timestamp_header: Optional[str],
    secret: str,
    *,
    now: Optional[float] = None,
    tolerance: int,
) -> bool:
    if not signature_header or not timestamp_header or not secret:
        return False
    try:
        ts = int(str(timestamp_header).strip())
    except (TypeError, ValueError):
        return False
    current = time.time() if now is None else now
    if abs(current - ts) > tolerance:
        return False
    signed_content = f"{ts}.".encode() + raw_body
    digest = hmac.new(secret.encode(), signed_content, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={digest}", signature_header.strip())


# ---------------------------------------------------------------------------
# Channel candidates / model selection (pure functions)
# ---------------------------------------------------------------------------


def candidate_channels(channels: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep only postable text channels, resolving the category name."""
    category_names = {
        str(ch.get("id")): str(ch.get("name") or "")
        for ch in channels
        if isinstance(ch, dict) and ch.get("type") == GUILD_CATEGORY
    }
    candidates = []
    for ch in channels:
        if not isinstance(ch, dict) or ch.get("type") not in POSTABLE_CHANNEL_TYPES:
            continue
        parent_id = ch.get("parent_id")
        try:
            position = int(ch.get("position") or 0)
        except (TypeError, ValueError):
            position = 0
        candidates.append({
            "id": str(ch.get("id")),
            "name": str(ch.get("name") or ""),
            "topic": str(ch.get("topic") or ""),
            "category": category_names.get(str(parent_id), "") if parent_id else "",
            "_position": position,
        })
    candidates.sort(key=lambda item: item["_position"])
    for item in candidates:
        item.pop("_position", None)
    return candidates


def parse_model_selection(text: str) -> Optional[Dict[str, Any]]:
    """Strictly parse the router response into {channel_id, confidence, reason}."""
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None

    channel_id = parsed.get("channel_id")
    if isinstance(channel_id, bool) or channel_id is None:
        return None
    if isinstance(channel_id, int):
        channel_id = str(channel_id)
    if not isinstance(channel_id, str) or not channel_id.strip():
        return None

    confidence = parsed.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return None
    confidence = float(confidence)
    if confidence < 0.0 or confidence > 1.0:
        return None

    reason = parsed.get("reason")
    if not isinstance(reason, str):
        return None

    return {"channel_id": channel_id.strip(), "confidence": confidence, "reason": reason}


def resolve_target(
    selection: Optional[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
    default_channel_id: str,
    threshold: float,
) -> Tuple[str, Optional[str]]:
    if not selection:
        return default_channel_id, "model_unavailable"
    if float(selection.get("confidence") or 0.0) < threshold:
        return default_channel_id, "low_confidence"
    known = {str(item.get("id")) for item in candidates}
    if str(selection.get("channel_id")) not in known:
        return default_channel_id, "unknown_channel"
    return str(selection["channel_id"]), None


def build_message(
    title: str,
    web_view_link: str,
    calendar_event: Dict[str, Any],
    reason: Optional[str],
) -> Dict[str, Any]:
    calendar_event = calendar_event or {}
    start = str(calendar_event.get("start_time") or "")
    end = str(calendar_event.get("end_time") or "")
    if start and end:
        when = f"{start} - {end}"
    else:
        when = start or end or "不明"

    lines = [
        f"カボス議事録: {title}",
        f"日時: {when}",
        f"{web_view_link}",
    ]
    if reason:
        lines.append(f"(既定チャンネルへ通知: {reason})")
    content = "\n".join(lines)[:DISCORD_MESSAGE_LIMIT]
    return {"content": content, "allowed_mentions": {"parse": []}}


# ---------------------------------------------------------------------------
# Model router (Groq OpenAI-compatible API)
# ---------------------------------------------------------------------------


_ROUTER_SYSTEM_PROMPT = (
    "あなたは社内議事録の投稿先チャンネルを選ぶルーターです。"
    "与えられた候補チャンネルから最も適切な1つを選び、"
    '{"channel_id": "...", "confidence": 0.0-1.0, "reason": "..."} '
    "の JSON のみを返してください。候補に無い channel_id は返さないこと。"
)


async def select_channel_with_model(
    title: str,
    context: str,
    candidates: List[Dict[str, Any]],
    *,
    client: httpx.AsyncClient,
) -> Optional[Dict[str, Any]]:
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key or not candidates:
        return None
    api_base = os.getenv("GROQ_API_BASE", "https://api.groq.com/openai/v1").rstrip("/")
    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip() or "openai/gpt-oss-20b"
    timeout = float(os.getenv("KABOSU_DISCORD_ROUTER_TIMEOUT_SECONDS", "8"))
    context_chars = int(os.getenv("KABOSU_DISCORD_ROUTER_CONTEXT_CHARS", "3000"))

    user_prompt = json.dumps(
        {
            "title": title,
            "context": (context or "")[:context_chars],
            "candidates": candidates,
        },
        ensure_ascii=False,
    )
    body = {
        "model": model,
        "temperature": 0,
        "stream": False,
        "reasoning_format": "hidden",
        # response_format=json_object は openai/gpt-oss-20b + max_completion_tokens=256
        # では reasoning がトークンを食い切って空生成になり 400
        # (json_validate_failed) を返す(2026-08-26 実測)。JSON は system prompt で
        # 要求し、崩れた出力は parse_model_selection が None にして default へ倒す。
        "max_completion_tokens": 256,
        "messages": [
            {"role": "system", "content": _ROUTER_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }

    try:
        resp = await client.post(
            f"{api_base}/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    except httpx.TimeoutException as exc:
        logger.warning("Discord router model timed out: %s", exc)
        return None
    except httpx.HTTPError as exc:
        logger.warning("Discord router model request failed: %s", exc)
        return None
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        logger.warning("Discord router model returned an unexpected shape: %s", exc)
        return None

    return parse_model_selection(content)


# ---------------------------------------------------------------------------
# Discord API client
# ---------------------------------------------------------------------------


class DiscordClient:
    def __init__(self, client: httpx.AsyncClient, *, token: str, api_base: Optional[str] = None):
        self._client = client
        self._headers = {"Authorization": f"Bot {token}"}
        self._api_base = (
            api_base or os.getenv("KABOSU_DISCORD_API_BASE", "https://discord.com/api/v10")
        ).rstrip("/")

    async def list_guild_channels(self, guild_id: str) -> List[Dict[str, Any]]:
        resp = await self._client.get(
            f"{self._api_base}/guilds/{guild_id}/channels", headers=self._headers
        )
        if resp.status_code < 200 or resp.status_code >= 300:
            raise DiscordHTTPError(resp.status_code, resp.text)
        return resp.json()

    async def create_message(self, channel_id: str, message: Dict[str, Any]) -> Dict[str, Any]:
        resp = await self._client.post(
            f"{self._api_base}/channels/{channel_id}/messages",
            json=message,
            headers=self._headers,
        )
        if resp.status_code < 200 or resp.status_code >= 300:
            raise DiscordHTTPError(resp.status_code, resp.text)
        return resp.json()


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


async def handle_drive_export_completed(
    db: AsyncSession,
    envelope: Dict[str, Any],
    *,
    http_client_factory=httpx.AsyncClient,
) -> Dict[str, Any]:
    token = os.getenv("KABOSU_DISCORD_BOT_TOKEN", "").strip()
    guild_id = os.getenv("KABOSU_DISCORD_GUILD_ID", "").strip()
    default_channel_id = os.getenv("KABOSU_DISCORD_DEFAULT_CHANNEL_ID", "").strip()
    if not token or not guild_id or not default_channel_id:
        return {"status": "skipped", "reason": "discord_not_configured"}

    event_id = envelope.get("event_id")
    data = envelope.get("data") or {}
    meeting_info = data.get("meeting") or {}
    meeting_id = meeting_info.get("id")

    meeting = (await db.execute(
        select(Meeting).where(Meeting.id == meeting_id).with_for_update()
    )).scalars().first()
    if meeting is None:
        return {"status": "skipped", "reason": "meeting_not_found"}

    meeting_data = dict(meeting.data or {}) if isinstance(meeting.data, dict) else {}
    notify = dict(meeting_data.get("discord_notify") or {})
    if notify.get("event_id") == event_id and notify.get("status") == "posted":
        return {"status": "duplicate", "channel_id": notify.get("channel_id")}

    drive_export = data.get("drive_export") or {}
    calendar_event = data.get("calendar_event") or {}
    title = str(data.get("title") or f"meeting-{meeting_id}")
    web_view_link = str(drive_export.get("web_view_link") or "")
    context_excerpt = str(data.get("context_excerpt") or "")

    threshold = float(os.getenv("KABOSU_DISCORD_ROUTER_CONFIDENCE_THRESHOLD", "0.85"))
    timeout = float(os.getenv("KABOSU_DISCORD_TIMEOUT_SECONDS", "10"))

    async with http_client_factory(timeout=timeout) as client:
        discord = DiscordClient(client, token=token)
        channels = await discord.list_guild_channels(guild_id)
        candidates = candidate_channels(channels)
        selection = await select_channel_with_model(
            title, context_excerpt, candidates, client=client
        )
        target, fallback_reason = resolve_target(
            selection, candidates, default_channel_id, threshold
        )
        message = build_message(title, web_view_link, calendar_event, fallback_reason)

        try:
            response = await discord.create_message(target, message)
        except DiscordHTTPError as exc:
            if exc.status_code in {403, 404} and target != default_channel_id:
                fallback_reason = f"selected_{exc.status_code}"
                target = default_channel_id
                message = build_message(title, web_view_link, calendar_event, fallback_reason)
                try:
                    response = await discord.create_message(target, message)
                except Exception as fallback_exc:
                    raise DiscordDeliveryError(
                        f"default channel post failed: {fallback_exc}"
                    ) from fallback_exc
            else:
                raise DiscordDeliveryError(f"discord post failed: {exc}") from exc
        except Exception as exc:
            raise DiscordDeliveryError(f"discord post failed: {exc}") from exc

    meeting_data["discord_notify"] = {
        "event_id": event_id,
        "status": "posted",
        "channel_id": target,
        "message_id": response.get("id") if isinstance(response, dict) else None,
        "selected_channel_id": selection["channel_id"] if selection else None,
        "confidence": selection["confidence"] if selection else None,
        "model_reason": selection["reason"] if selection else None,
        "fallback_reason": fallback_reason,
        "posted_at": _utcnow_iso(),
    }
    meeting.data = meeting_data
    attributes.flag_modified(meeting, "data")
    await db.commit()

    return {"status": "posted", "channel_id": target, "fallback_reason": fallback_reason}


def _utcnow_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
