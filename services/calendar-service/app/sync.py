"""Calendar sync loop — polls Google Calendar, upserts events, schedules bots."""

import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import attributes

from meeting_api.models import CalendarEvent, Meeting
from admin_models.models import User
from app.google_calendar import (
    refresh_access_token,
    list_events,
    extract_meeting_url,
    detect_platform,
    parse_event_time,
)

logger = logging.getLogger("calendar-service.sync")

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
MEETING_API_URL = os.getenv("MEETING_API_URL", "http://meeting-api:8080")
BOT_API_TOKEN = os.getenv("BOT_API_TOKEN", "")
DEFAULT_LEAD_TIME_MINUTES = int(os.getenv("DEFAULT_LEAD_TIME_MINUTES", "2"))
KABOSU_CALENDAR_MODE = os.getenv("KABOSU_CALENDAR_MODE", "per_user").strip().lower()
KABOSU_GOOGLE_REFRESH_TOKEN = (
    os.getenv("KABOSU_GOOGLE_REFRESH_TOKEN", "")
    or os.getenv("KABOSU_CALENDAR_REFRESH_TOKEN", "")
)
KABOSU_BOT_OWNER_USER_ID = (
    os.getenv("KABOSU_BOT_OWNER_USER_ID", "")
    or os.getenv("KABOSU_CALENDAR_USER_ID", "")
)
KABOSU_CALENDAR_ACCOUNT_EMAIL = os.getenv("KABOSU_CALENDAR_ACCOUNT_EMAIL", "").strip()
KABOSU_BOT_NAME = os.getenv("KABOSU_BOT_NAME", "カボス")
KABOSU_BOT_LANGUAGE = os.getenv("KABOSU_BOT_LANGUAGE", "ja")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


KABOSU_VOICE_AGENT_ENABLED = _env_bool("KABOSU_VOICE_AGENT_ENABLED", True)


def single_account_mode_enabled() -> bool:
    """Return true when the calendar service should sync only the Kabosu account."""
    return KABOSU_CALENDAR_MODE == "single_account"


def _kabosu_owner_user_id() -> Optional[int]:
    try:
        return int(KABOSU_BOT_OWNER_USER_ID)
    except (TypeError, ValueError):
        return None


def _bot_request_headers() -> dict[str, str]:
    headers = {"X-API-Key": BOT_API_TOKEN}
    owner_user_id = _kabosu_owner_user_id()
    if owner_user_id is not None:
        headers.update({
            "X-User-ID": str(owner_user_id),
            "X-User-Scopes": "bot,tx,browser",
            "X-User-Limits": os.getenv("KABOSU_BOT_OWNER_MAX_CONCURRENT", "999"),
        })
    return headers


async def sync_user_calendar(user_id: int, db: AsyncSession) -> int:
    """Sync a single user's Google Calendar events. Returns count of upserted events."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        logger.warning(f"User {user_id} not found")
        return 0

    user_data = user.data or {}
    gc_data = user_data.get("google_calendar", {})
    oauth = gc_data.get("oauth", {})
    refresh_token = oauth.get("refresh_token")
    if not refresh_token:
        logger.info(f"User {user_id} has no Google Calendar refresh token")
        return 0

    upserted, next_sync_token = await _sync_calendar_events(
        user_id,
        refresh_token,
        gc_data.get("sync_token"),
        db,
        sync_label=f"user {user_id}",
    )

    # Save new sync token
    if next_sync_token:
        gc_data["sync_token"] = next_sync_token
        user_data["google_calendar"] = gc_data
        await db.execute(
            update(User).where(User.id == user_id).values(data=user_data)
        )

    await db.commit()
    logger.info(f"Synced {upserted} events for user {user_id}")
    return upserted


async def sync_single_account_calendar(db: AsyncSession) -> int:
    """Sync the dedicated Kabosu calendar account into one owner user's events."""
    owner_user_id = _kabosu_owner_user_id()
    if owner_user_id is None:
        logger.warning("KABOSU_CALENDAR_MODE=single_account requires KABOSU_BOT_OWNER_USER_ID")
        return 0
    if not KABOSU_GOOGLE_REFRESH_TOKEN:
        logger.warning("KABOSU_CALENDAR_MODE=single_account requires KABOSU_GOOGLE_REFRESH_TOKEN")
        return 0
    if not KABOSU_CALENDAR_ACCOUNT_EMAIL:
        logger.warning("KABOSU_CALENDAR_MODE=single_account requires KABOSU_CALENDAR_ACCOUNT_EMAIL")
        return 0

    result = await db.execute(select(User).where(User.id == owner_user_id))
    user = result.scalar_one_or_none()
    if not user:
        logger.warning(f"Kabosu owner user {owner_user_id} not found")
        return 0

    user_data = dict(user.data or {})
    gc_data = dict(user_data.get("google_calendar") or {})
    single_state = dict(gc_data.get("single_account") or {})

    upserted, next_sync_token = await _sync_calendar_events(
        owner_user_id,
        KABOSU_GOOGLE_REFRESH_TOKEN,
        single_state.get("sync_token"),
        db,
        sync_label=f"kabosu account {KABOSU_CALENDAR_ACCOUNT_EMAIL}",
    )

    if next_sync_token:
        single_state["sync_token"] = next_sync_token
        single_state["account_email"] = KABOSU_CALENDAR_ACCOUNT_EMAIL
        single_state["last_synced_at"] = datetime.now(timezone.utc).isoformat()
        gc_data["single_account"] = single_state
        user_data["google_calendar"] = gc_data
        await db.execute(update(User).where(User.id == owner_user_id).values(data=user_data))

    await db.commit()
    logger.info(f"Synced {upserted} events for Kabosu calendar account")
    return upserted


async def _sync_calendar_events(
    user_id: int,
    refresh_token: str,
    existing_sync_token: Optional[str],
    db: AsyncSession,
    *,
    sync_label: str,
) -> tuple[int, Optional[str]]:
    access_token, _expires_in = await refresh_access_token(
        GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, refresh_token
    )

    time_min = datetime.now(timezone.utc)
    time_max = time_min + timedelta(days=7)

    api_response = await list_events(
        access_token,
        time_min=time_min,
        time_max=time_max,
        sync_token=existing_sync_token,
    )

    if api_response.get("fullSyncRequired"):
        logger.info(f"Full sync required for {sync_label}, clearing sync token")
        api_response = await list_events(
            access_token, time_min=time_min, time_max=time_max
        )

    events = api_response.get("items", [])
    next_sync_token = api_response.get("nextSyncToken")
    upserted = 0

    for event in events:
        event_id = event.get("id")
        if not event_id:
            continue

        if event.get("status") == "cancelled":
            await db.execute(
                update(CalendarEvent)
                .where(
                    CalendarEvent.user_id == user_id,
                    CalendarEvent.external_event_id == event_id,
                )
                .values(status="cancelled")
            )
            continue

        start_time = parse_event_time(event, "start")
        if not start_time:
            continue

        end_time = parse_event_time(event, "end")
        meeting_url = extract_meeting_url(event)
        platform = detect_platform(meeting_url) if meeting_url else None

        stmt = pg_insert(CalendarEvent).values(
            user_id=user_id,
            external_event_id=event_id,
            title=event.get("summary", ""),
            start_time=start_time,
            end_time=end_time,
            meeting_url=meeting_url,
            platform=platform,
            status="pending",
        ).on_conflict_do_update(
            constraint="uq_calendar_event_user_ext_id",
            set_={
                "title": event.get("summary", ""),
                "start_time": start_time,
                "end_time": end_time,
                "meeting_url": meeting_url,
                "platform": platform,
            },
        )
        await db.execute(stmt)
        upserted += 1

    return upserted, next_sync_token


async def schedule_upcoming_bots(db: AsyncSession) -> int:
    """Check for pending events within lead time and schedule bots. Returns count scheduled."""
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(minutes=DEFAULT_LEAD_TIME_MINUTES)

    result = await db.execute(
        select(CalendarEvent).where(
            CalendarEvent.status == "pending",
            CalendarEvent.start_time <= cutoff,
            CalendarEvent.end_time > now,
            CalendarEvent.meeting_url.isnot(None),
            CalendarEvent.platform.isnot(None),
        )
    )
    events = result.scalars().all()
    scheduled = 0

    for event in events:
        try:
            native_meeting_id = _extract_native_id(event.meeting_url, event.platform)
            payload: dict[str, Any] = {
                "platform": event.platform,
                "native_meeting_id": native_meeting_id,
                "meeting_url": event.meeting_url,
                "bot_name": KABOSU_BOT_NAME,
                "language": KABOSU_BOT_LANGUAGE,
                "voice_agent_enabled": KABOSU_VOICE_AGENT_ENABLED,
            }
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{MEETING_API_URL}/bots",
                    json=payload,
                    headers=_bot_request_headers(),
                    timeout=30,
                )

            if resp.status_code in (200, 201):
                resp_data = resp.json()
                meeting_id = resp_data.get("id")
                await _attach_calendar_metadata(db, event, meeting_id)
                await db.execute(
                    update(CalendarEvent)
                    .where(CalendarEvent.id == event.id)
                    .values(
                        status="scheduled",
                        meeting_id=meeting_id,
                    )
                )
                scheduled += 1
                logger.info(f"Scheduled bot for event {event.id}: {event.title}")
            else:
                logger.error(f"Bot request failed for event {event.id}: {resp.status_code} {resp.text}")
                await db.execute(
                    update(CalendarEvent)
                    .where(CalendarEvent.id == event.id)
                    .values(status="failed")
                )
        except Exception as e:
            logger.error(f"Failed to schedule bot for event {event.id}: {e}")

    await db.commit()
    return scheduled


async def _attach_calendar_metadata(
    db: AsyncSession,
    event: CalendarEvent,
    meeting_id: Optional[int],
) -> None:
    if not meeting_id:
        return
    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        logger.warning("Meeting %s not found after calendar bot creation", meeting_id)
        return

    data = dict(meeting.data or {}) if isinstance(meeting.data, dict) else {}
    data["calendar_event"] = _calendar_event_metadata(event)
    meeting.data = data
    attributes.flag_modified(meeting, "data")


def _calendar_event_metadata(event: CalendarEvent) -> dict[str, Any]:
    return {
        "source": "google_calendar",
        "calendar_event_id": event.id,
        "external_event_id": event.external_event_id,
        "title": event.title,
        "start_time": _dt_iso(event.start_time),
        "end_time": _dt_iso(event.end_time),
        "meeting_url": event.meeting_url,
        "platform": event.platform,
        "account_email": KABOSU_CALENDAR_ACCOUNT_EMAIL,
    }


def _dt_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _extract_native_id(url: str, platform: str) -> str:
    """Extract the native meeting ID from a URL for meeting-api."""
    if platform == "google_meet":
        # https://meet.google.com/abc-defg-hij -> abc-defg-hij
        return url.rsplit("/", 1)[-1].split("?")[0]
    if platform == "zoom":
        # https://zoom.us/j/123456?pwd=xxx -> 123456
        import re
        match = re.search(r"/j/(\d+)", url)
        return match.group(1) if match else url
    if platform == "teams":
        return url
    return url
