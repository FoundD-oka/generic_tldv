"""Google Drive export for calendar-origin meetings."""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import attributes

from .models import Meeting, Transcription

logger = logging.getLogger("meeting_api.drive_export")

TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
DRIVE_EXPORT_MAX_ATTEMPTS = int(
    os.getenv("KABOSU_DRIVE_EXPORT_MAX_ATTEMPTS", os.getenv("DRIVE_EXPORT_MAX_ATTEMPTS", "24"))
)
DRIVE_EXPORT_SWEEP_LIMIT = int(
    os.getenv("KABOSU_DRIVE_EXPORT_SWEEP_LIMIT", os.getenv("DRIVE_EXPORT_SWEEP_LIMIT", "10"))
)
DRIVE_EXPORT_STATUSES = {"queued", "running", "done", "failed", "skipped"}


class DriveExportError(Exception):
    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


def _utcnow_iso() -> str:
    return datetime.utcnow().isoformat()


def _meeting_data(meeting: Meeting) -> Dict[str, Any]:
    return dict(meeting.data or {}) if isinstance(meeting.data, dict) else {}


def _calendar_only_mode() -> bool:
    """Restrict Drive export to Google Calendar-origin meetings when enabled."""
    return os.getenv("KABOSU_DRIVE_EXPORT_CALENDAR_ONLY", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _calendar_metadata(meeting: Meeting) -> Optional[Dict[str, Any]]:
    data = _meeting_data(meeting)
    calendar_event = data.get("calendar_event")
    if not isinstance(calendar_event, dict):
        return None
    if calendar_event.get("source") != "google_calendar":
        return None
    return dict(calendar_event)


def _set_drive_export_state(meeting: Meeting, **updates: Any) -> Dict[str, Any]:
    data = _meeting_data(meeting)
    current = dict(data.get("drive_export") or {})
    current.update(updates)
    status = current.get("status")
    if status and status not in DRIVE_EXPORT_STATUSES:
        raise ValueError(f"Invalid drive_export status: {status!r}")
    data["drive_export"] = current
    if status:
        data["drive_export_status"] = status
    meeting.data = data
    attributes.flag_modified(meeting, "data")
    return current


def queue_drive_export_if_needed(
    meeting: Meeting,
    *,
    triggered_by: str,
) -> bool:
    """Queue Drive export when this meeting was created from Google Calendar."""
    if os.getenv("KABOSU_DRIVE_EXPORT_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
        return False
    if _calendar_only_mode() and _calendar_metadata(meeting) is None:
        return False

    current = dict(_meeting_data(meeting).get("drive_export") or {})
    if current.get("status") in {"queued", "running", "done"}:
        return False
    if current.get("status") == "failed" and not current.get("retryable"):
        return False

    now = _utcnow_iso()
    _set_drive_export_state(
        meeting,
        status="queued",
        queued_at=current.get("queued_at") or now,
        updated_at=now,
        attempts=current.get("attempts") or 0,
        last_error=None,
        retryable=True,
        triggered_by=triggered_by,
    )
    return True


def requeue_drive_export(
    meeting: Meeting,
    *,
    triggered_by: str,
) -> bool:
    """Force a re-export after transcript content mutated (e.g. speaker rename).

    Unlike queue_drive_export_if_needed, a `done` (or terminally `failed`)
    export is re-queued; the existing Drive file_id is kept in state so the
    re-run updates the same document instead of creating a duplicate.
    """
    if os.getenv("KABOSU_DRIVE_EXPORT_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
        return False
    if _calendar_only_mode() and _calendar_metadata(meeting) is None:
        return False

    current = dict(_meeting_data(meeting).get("drive_export") or {})
    now = _utcnow_iso()
    if current.get("status") == "queued":
        # Not started yet; the pending run reads fresh rows at run time.
        return False
    if current.get("status") == "running":
        # An export is mid-flight with the OLD rows. Flag a re-run; the
        # exporter re-queues itself instead of finishing as `done`.
        _set_drive_export_state(
            meeting,
            rerun_requested=True,
            updated_at=now,
            triggered_by=triggered_by,
        )
        return True

    _set_drive_export_state(
        meeting,
        status="queued",
        queued_at=now,
        updated_at=now,
        attempts=0,
        last_error=None,
        retryable=True,
        triggered_by=triggered_by,
        requeued_from=current.get("status"),
    )
    return True


def drive_export_retry_eligible(job: Dict[str, Any], *, now: Optional[datetime] = None) -> bool:
    attempts = int(job.get("attempts") or 0)
    if attempts <= 0:
        return True
    last_at = job.get("updated_at") or job.get("failed_at") or job.get("started_at")
    if not last_at:
        return True
    try:
        last_dt = datetime.fromisoformat(str(last_at).replace("Z", "+00:00"))
        if last_dt.tzinfo is not None:
            last_dt = last_dt.replace(tzinfo=None)
    except (TypeError, ValueError):
        return True
    now = now or datetime.utcnow()
    backoff_seconds = min(60 * (2 ** min(attempts - 1, 10)), 86400)
    return now - last_dt >= timedelta(seconds=backoff_seconds)


async def run_drive_export(
    meeting_id: int,
    db: AsyncSession,
) -> Dict[str, Any]:
    """Export a queued calendar-origin meeting transcript to Google Drive."""
    meeting = (await db.execute(
        select(Meeting)
        .where(Meeting.id == meeting_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )).scalars().first()
    if meeting is None:
        raise DriveExportError("Meeting not found", retryable=False)

    calendar_event = _calendar_metadata(meeting)
    if calendar_event is None:
        if _calendar_only_mode():
            _set_drive_export_state(
                meeting,
                status="skipped",
                skipped_at=_utcnow_iso(),
                updated_at=_utcnow_iso(),
                skipped_reason="not_calendar_origin",
                retryable=False,
            )
            await db.commit()
            return {"status": "skipped", "reason": "not_calendar_origin"}
        # Non-calendar meeting: export with meeting-derived metadata only.
        calendar_event = {}

    data = _meeting_data(meeting)
    current = dict(data.get("drive_export") or {})
    attempts = int(current.get("attempts") or 0) + 1
    _set_drive_export_state(
        meeting,
        status="running",
        started_at=_utcnow_iso(),
        updated_at=_utcnow_iso(),
        attempts=attempts,
        last_error=None,
        retryable=True,
    )
    await db.commit()

    try:
        transcripts = (await db.execute(
            select(Transcription)
            .where(Transcription.meeting_id == meeting_id)
            .order_by(Transcription.start_time, Transcription.id)
        )).scalars().all()
        filename = drive_export_filename(meeting, calendar_event)
        content = build_drive_markdown(meeting, calendar_event, transcripts)
        # Re-exports (speaker rename etc.) update the existing Drive file
        # in place instead of creating a duplicate.
        upload = await upload_markdown_to_drive(filename, content, file_id=current.get("file_id"))
    except DriveExportError as exc:
        _set_drive_export_state(
            meeting,
            status="failed",
            failed_at=_utcnow_iso(),
            updated_at=_utcnow_iso(),
            attempts=attempts,
            last_error=str(exc),
            retryable=exc.retryable,
        )
        await db.commit()
        raise
    except Exception as exc:
        _set_drive_export_state(
            meeting,
            status="failed",
            failed_at=_utcnow_iso(),
            updated_at=_utcnow_iso(),
            attempts=attempts,
            last_error=str(exc),
            retryable=True,
        )
        await db.commit()
        raise DriveExportError(f"Drive export failed: {exc}") from exc

    # Re-read the meeting before the final state write: a speaker PATCH may
    # have committed corrections (and a rerun_requested flag) while the upload
    # was in flight. Writing the stale in-memory JSONB back would clobber it.
    refreshed = (await db.execute(
        select(Meeting)
        .where(Meeting.id == meeting_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )).scalars().first()
    if refreshed is not None:
        meeting = refreshed
    current = dict(_meeting_data(meeting).get("drive_export") or {})

    if current.get("rerun_requested"):
        # Transcript content changed mid-export: the uploaded file already has
        # stale names, so go straight back to queued for a fresh render.
        _set_drive_export_state(
            meeting,
            status="queued",
            queued_at=_utcnow_iso(),
            updated_at=_utcnow_iso(),
            attempts=0,
            rerun_requested=False,
            file_id=upload.get("id"),
            web_view_link=upload.get("webViewLink"),
            filename=filename,
            last_error=None,
            retryable=True,
        )
        await db.commit()
        logger.info(
            "Drive export for meeting %s re-queued (content changed mid-export) file=%s",
            meeting_id, upload.get("id"),
        )
        return {"status": "queued", "filename": filename, **upload}

    _set_drive_export_state(
        meeting,
        status="done",
        completed_at=_utcnow_iso(),
        updated_at=_utcnow_iso(),
        attempts=attempts,
        rerun_requested=False,
        file_id=upload.get("id"),
        web_view_link=upload.get("webViewLink"),
        filename=filename,
        last_error=None,
        retryable=False,
    )
    await db.commit()
    logger.info("Drive export succeeded for meeting %s file=%s", meeting_id, upload.get("id"))
    return {"status": "done", "filename": filename, **upload}


def drive_export_filename(meeting: Meeting, calendar_event: Dict[str, Any]) -> str:
    start_dt = _parse_datetime(calendar_event.get("start_time")) or meeting.start_time or meeting.created_at
    if isinstance(start_dt, datetime):
        prefix = start_dt.strftime("%Y-%m-%d_%H%M")
    else:
        prefix = datetime.utcnow().strftime("%Y-%m-%d_%H%M")
    title = str(calendar_event.get("title") or meeting.platform_specific_id or meeting.id)
    return f"{prefix}_{_safe_filename_component(title)}.md"


def build_drive_markdown(
    meeting: Meeting,
    calendar_event: Dict[str, Any],
    transcripts: Iterable[Transcription],
) -> str:
    title = str(calendar_event.get("title") or meeting.platform_specific_id or f"meeting-{meeting.id}")
    transcript_rows = list(transcripts)
    participants = _participants(calendar_event, transcript_rows)
    lines = [
        f"# {title}",
        "",
        "## 会議メタ",
        "",
        f"- 日時: {_display_datetime_range(calendar_event)}",
        f"- 会議ID: {meeting.platform}/{meeting.platform_specific_id}",
        f"- 会議URL: {calendar_event.get('meeting_url') or meeting.constructed_meeting_url or ''}",
        f"- 参加者: {', '.join(participants) if participants else '未取得'}",
        "",
        "## 文字起こし",
        "",
    ]

    if not transcript_rows:
        lines.append("_文字起こしはありません。_")
        lines.append("")
        return "\n".join(lines)

    for row in transcript_rows:
        speaker = (getattr(row, "speaker", None) or "Unknown").strip() or "Unknown"
        text = str(getattr(row, "text", "") or "").strip()
        if not text:
            continue
        start = _format_offset(getattr(row, "start_time", None))
        lines.append(f"- [{start}] **{speaker}**: {text}")

    lines.append("")
    return "\n".join(lines)


async def upload_markdown_to_drive(
    filename: str,
    content: str,
    *,
    file_id: Optional[str] = None,
) -> Dict[str, Any]:
    folder_id = os.getenv("KABOSU_DRIVE_FOLDER_ID", "").strip()
    if not folder_id:
        raise DriveExportError("KABOSU_DRIVE_FOLDER_ID is not configured", retryable=True)

    access_token = await refresh_google_access_token()
    metadata: Dict[str, Any] = {
        "name": filename,
        "mimeType": "text/markdown",
    }
    if not file_id:
        # Parents can only be set at creation time.
        metadata["parents"] = [folder_id]
    boundary = f"kabosu_{uuid.uuid4().hex}"
    body = _multipart_related_body(boundary, metadata, content)
    timeout = float(os.getenv("KABOSU_DRIVE_UPLOAD_TIMEOUT_SECONDS", "60"))

    params = {
        "uploadType": "multipart",
        "fields": "id,webViewLink",
        # Required when the parent folder lives in a Shared Drive; harmless for My Drive.
        "supportsAllDrives": "true",
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": f"multipart/related; boundary={boundary}",
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        if file_id:
            resp = await client.patch(
                f"{DRIVE_UPLOAD_URL}/{file_id}",
                params=params,
                content=body,
                headers=headers,
            )
            if resp.status_code == 404:
                # File was deleted from Drive; fall back to creating a new one.
                logger.warning("Drive file %s gone; creating a new export file", file_id)
                return await upload_markdown_to_drive(filename, content)
        else:
            resp = await client.post(
                DRIVE_UPLOAD_URL,
                params=params,
                content=body,
                headers=headers,
            )
    if resp.status_code >= 400:
        raise DriveExportError(
            f"Drive upload failed: {resp.status_code} {resp.text[:300]}",
            retryable=_retryable_google_status(resp.status_code),
        )
    return resp.json()


async def refresh_google_access_token() -> str:
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    refresh_token = (
        os.getenv("KABOSU_GOOGLE_REFRESH_TOKEN", "").strip()
        or os.getenv("KABOSU_CALENDAR_REFRESH_TOKEN", "").strip()
    )
    missing = [
        name for name, value in (
            ("GOOGLE_CLIENT_ID", client_id),
            ("GOOGLE_CLIENT_SECRET", client_secret),
            ("KABOSU_GOOGLE_REFRESH_TOKEN", refresh_token),
        )
        if not value
    ]
    if missing:
        raise DriveExportError(f"Missing Google OAuth env: {', '.join(missing)}", retryable=True)

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            },
        )
    if resp.status_code >= 400:
        raise DriveExportError(
            f"Google token refresh failed: {resp.status_code} {resp.text[:300]}",
            retryable=_retryable_google_status(resp.status_code),
        )
    return resp.json()["access_token"]


def _multipart_related_body(boundary: str, metadata: Dict[str, Any], content: str) -> bytes:
    metadata_json = json.dumps(metadata, ensure_ascii=False).encode("utf-8")
    content_bytes = content.encode("utf-8")
    return b"".join([
        f"--{boundary}\r\n".encode("ascii"),
        b"Content-Type: application/json; charset=UTF-8\r\n\r\n",
        metadata_json,
        b"\r\n",
        f"--{boundary}\r\n".encode("ascii"),
        b"Content-Type: text/markdown; charset=UTF-8\r\n\r\n",
        content_bytes,
        b"\r\n",
        f"--{boundary}--\r\n".encode("ascii"),
    ])


def _participants(calendar_event: Dict[str, Any], transcripts: Iterable[Transcription]) -> list[str]:
    attendees = calendar_event.get("attendees")
    if isinstance(attendees, list):
        names = [str(item).strip() for item in attendees if str(item).strip()]
        if names:
            return sorted(set(names))
    speakers = {
        str(getattr(row, "speaker", "") or "").strip()
        for row in transcripts
    }
    return sorted(name for name in speakers if name and name.lower() != "unknown")


def _display_datetime_range(calendar_event: Dict[str, Any]) -> str:
    start = calendar_event.get("start_time") or ""
    end = calendar_event.get("end_time") or ""
    if start and end:
        return f"{start} - {end}"
    return str(start or end or "未取得")


def _format_offset(value: Any) -> str:
    try:
        total = max(0, int(float(value)))
    except (TypeError, ValueError):
        total = 0
    minutes, seconds = divmod(total, 60)
    return f"{minutes:02d}:{seconds:02d}"


def _parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _safe_filename_component(value: str) -> str:
    value = re.sub(r"[\x00-\x1f/\\:*?\"<>|]+", "_", value).strip(" ._")
    value = re.sub(r"\s+", " ", value)
    return (value or "meeting")[:80]


def _retryable_google_status(status_code: int) -> bool:
    return status_code in {401, 403, 408, 409, 425, 429} or status_code >= 500
