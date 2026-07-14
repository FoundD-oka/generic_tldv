"""Defensive normalization for bot-observed meeting participant rosters."""

from __future__ import annotations

import hashlib
import math
import re
import time
from typing import Any, Dict, Iterable, List


MAX_ROSTER_ENTRIES = 250
MAX_ROSTER_INPUT_ENTRIES = 1000
MAX_PARTICIPANT_NAME_LENGTH = 120
MAX_PARTICIPANT_ID_LENGTH = 200
MAX_CLOCK_SKEW_MS = 5 * 60 * 1000
_OPAQUE_ID = re.compile(r"^participant:[a-f0-9]{16}$")
_JUNK_NAME_PATTERNS = (
    re.compile(r"^google participant \(", re.IGNORECASE),
    re.compile(r"spaces/", re.IGNORECASE),
    re.compile(r"devices/", re.IGNORECASE),
    re.compile(r"let participants", re.IGNORECASE),
    re.compile(r"send messages", re.IGNORECASE),
    re.compile(r"turn on captions", re.IGNORECASE),
)


def _clean_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = " ".join(value.split()).strip()
    return cleaned if 0 < len(cleaned) <= limit else ""


def _timestamp(value: Any, fallback: int = 0, max_value: int | None = None) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(number) or number < 0:
        return fallback
    if max_value is not None and number > max_value:
        return fallback
    return int(number)


def _opaque_id(raw_id: str, name: str, id_salt: str) -> str:
    if _OPAQUE_ID.fullmatch(raw_id):
        return raw_id
    identity = raw_id or f"name:{name.casefold()}"
    digest = hashlib.sha256(
        f"{id_salt}\0{identity}".encode("utf-8")
    ).hexdigest()[:16]
    return f"participant:{digest}"


def normalize_participant_roster(
    raw_entries: Any,
    *,
    id_salt: str,
) -> List[Dict[str, Any]]:
    """Return an upper-bounded, idempotent roster without exposing platform IDs."""

    if not isinstance(raw_entries, list):
        return []

    by_name: Dict[str, Dict[str, Any]] = {}
    max_timestamp = int(time.time() * 1000) + MAX_CLOCK_SKEW_MS
    for raw in raw_entries[:MAX_ROSTER_INPUT_ENTRIES]:
        if not isinstance(raw, dict):
            continue
        name = _clean_text(
            raw.get("participant_name", raw.get("name")),
            MAX_PARTICIPANT_NAME_LENGTH,
        )
        if not name or any(pattern.search(name) for pattern in _JUNK_NAME_PATTERNS):
            continue
        raw_id = _clean_text(
            raw.get("participant_id", raw.get("id")),
            MAX_PARTICIPANT_ID_LENGTH,
        )
        participant_id = _opaque_id(raw_id, name, id_salt)
        source = "people_panel" if raw.get("source") == "people_panel" else "participant_tile"
        first_seen = _timestamp(raw.get("first_seen_at_ms"), 0, max_timestamp)
        last_seen = _timestamp(raw.get("last_seen_at_ms"), first_seen, max_timestamp)
        if last_seen < first_seen:
            first_seen, last_seen = last_seen, first_seen

        name_key = name.casefold()
        existing = by_name.get(name_key)
        if existing is None:
            if len(by_name) >= MAX_ROSTER_ENTRIES:
                continue
            by_name[name_key] = {
                "participant_id": participant_id,
                "participant_name": name,
                "first_seen_at_ms": first_seen,
                "last_seen_at_ms": last_seen,
                "source": source,
            }
        else:
            existing["first_seen_at_ms"] = min(existing["first_seen_at_ms"], first_seen)
            existing["last_seen_at_ms"] = max(existing["last_seen_at_ms"], last_seen)
            if source == "people_panel":
                existing["source"] = source
                existing["participant_name"] = name

    return sorted(
        by_name.values(),
        key=lambda item: (item["first_seen_at_ms"], item["participant_name"].casefold()),
    )


def participant_names(roster: Iterable[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    seen = set()
    for entry in roster:
        name = _clean_text(entry.get("participant_name"), MAX_PARTICIPANT_NAME_LENGTH)
        key = name.casefold()
        if name and key not in seen:
            seen.add(key)
            names.append(name)
    return names


def merge_participant_roster_data(
    meeting_data: Any,
    incoming_roster: Any,
    *,
    id_salt: str,
) -> Dict[str, Any]:
    """Merge observed facts while preserving explicitly supplied participants."""

    data = dict(meeting_data) if isinstance(meeting_data, dict) else {}
    existing = data.get("participant_roster")
    normalized = normalize_participant_roster(
        [
            *(existing if isinstance(existing, list) else []),
            *(incoming_roster if isinstance(incoming_roster, list) else []),
        ],
        id_salt=id_salt,
    )
    if not normalized:
        return data

    names = participant_names(normalized)
    data["participant_roster"] = normalized
    data["observed_participants"] = names
    if "participants" not in data or data.get("participants_source") in {
        "participant_roster",
        "transcript_speakers",
    }:
        data["participants"] = names
        data["participants_source"] = "participant_roster"
    return data
