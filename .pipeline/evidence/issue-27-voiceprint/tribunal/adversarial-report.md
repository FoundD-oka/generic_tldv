verdicts:
- id: BUG-001
  verdict: confirmed
  reasoning: |
    `_INTERNAL_DATA_KEYS` in `services/meeting-api/meeting_api/webhook_delivery.py:67-72`
    is `{"webhook_delivery", "webhook_deliveries", "webhook_secret", "webhook_secrets",
    "webhook_events", "webhook_url", "outbound_events", "bot_container_id",
    "container_name"}` — `speaker_suggestions` is absent. `clean_meeting_data()`
    (same file, line 90-94) is a plain key-exclusion filter over exactly that set,
    and `webhooks.py:137` (`_build_meeting_event_data`) does
    `"data": clean_meeting_data(meeting.data)` for BOTH `send_completion_webhook`
    (webhooks.py:143) and `send_status_webhook` (webhooks.py:195,225), i.e. every
    external webhook payload in the system. Meanwhile `schemas.py:33`
    (`MEETING_DATA_REDACTED_KEYS = frozenset({"webhook_secret", "speaker_suggestions"})`)
    shows the commit's authors were aware `speaker_suggestions` needed redaction and
    patched the API-response path (`schemas.py` field_serializer) but never touched
    webhook_delivery.py's independent key list — a second, unsynchronized filter for
    the same field.

    Timing: I traced the actual pipeline. `post_meeting.py`'s `run_all_tasks` queues
    final transcription (Task 1) then fires `send_completion_webhook` (Task 3,
    line 419-427) BEFORE the heavy work runs. The heavy work — actual final
    transcription + `run_voiceprint_matching_followup` (which writes
    `meeting.data["speaker_suggestions"]`, voiceprint_matching.py:504-511,522-527) —
    only executes later, out-of-band, via `sweeps.py::_sweep_final_transcription_jobs`
    (sweeps.py:683-779) picking up the "queued" job. So the *first* completion webhook
    genuinely predates matching and cannot leak anything.

    However, `callbacks.py:781-795` has a documented "late bot callback" branch: when
    `meeting.data.get("stop_requested")` is true and a NEW non-terminal status arrives
    for an already-processed meeting, the code explicitly skips the status transition
    but still calls `schedule_status_webhook_task` "so users subscribed to
    meeting.status_change ... don't miss events that legitimately happened on the bot
    side." This webhook still runs `_build_meeting_event_data(meeting)` →
    `clean_meeting_data(meeting.data)` on the meeting's current `meeting.data`, which by
    then may already contain `speaker_suggestions` written by the sweep's matching run
    (timing between the sweep and a delayed bot callback is not otherwise
    synchronized). This is a concrete, if narrow, path by which a `profile_id`-bearing
    suggestion reaches an external, user-configured webhook URL — precisely the "third
    unguarded exit path" the finder describes. The structural gap (webhook path never
    updated to match the API-response redaction) is unambiguous and real regardless of
    how many concrete triggers exist.
  confidence: high

- id: BUG-002
  verdict: confirmed
  reasoning: |
    `services/meeting-api/meeting_api/database.py:72-75` sets
    `sessionmaker(..., expire_on_commit=False)`. This is the load-bearing fact that
    makes the race real: after any `await db.commit()` inside `_run_matching`
    (voiceprint_matching.py, e.g. lines 409, 426, 434, 443), the `meeting` ORM
    object's `.data` attribute is NOT expired/refreshed from the DB — it keeps
    whatever Python value was last assigned to it in-process.

    `final_transcription.py:1057-1062` loads `meeting` with `.with_for_update()`, but
    that lock is released at the success commit (`final_transcription.py:1354`,
    right before `queue_drive_export_if_needed` and the voiceprint call at line 1373).
    `run_voiceprint_matching_followup` → `_run_matching` then does slicing/ffmpeg/HTTP
    work with no additional row lock, for up to `MATCH_TOTAL_BUDGET_S` (120s default),
    and finally (voiceprint_matching.py:521-527):
    ```
    if changed:
        data = dict(meeting.data or {}) if isinstance(meeting.data, dict) else {}
        data["speaker_suggestions"] = suggestions
        meeting.data = data
        attributes.flag_modified(meeting, "data")
    await db.commit()
    ```
    `meeting.data` here is the in-process Python value, not a fresh DB read — because
    of `expire_on_commit=False`, no implicit refresh happens. Meanwhile
    `update_meeting_speakers` (PATCH, meetings.py:2062-2067) and
    `reject_speaker_suggestion` (DELETE, meetings.py:2302-2307) each open a brand-new
    per-request session/ORM instance, `SELECT ... FOR UPDATE`, mutate
    `data["speaker_corrections"]` or `data["speaker_suggestions"]`, and commit
    independently. If either commits during the matching window, its change is
    invisible to `_run_matching`'s stale `data` dict, and the final
    `meeting.data = data` write is a FULL JSONB column replacement built from that
    stale base — silently discarding the concurrent PATCH's write (lost update) or
    resurrecting an already-rejected suggestion. The finder's phrase "fetched possibly
    minutes earlier" is a mild overstatement (the base is actually as fresh as
    `final_transcription.py`'s own commit, milliseconds before matching starts), but
    the core defect — no re-lock/re-fetch across an up-to-120s window, full-dict
    overwrite instead of a scoped merge — is exactly as described and is a genuine,
    exploitable lost-update race.
  confidence: high

- id: BUG-003
  verdict: confirmed
  reasoning: |
    `voiceprint_matching.py:41-42`:
    ```
    MATCH_TOTAL_BUDGET_S = float(os.getenv("MATCH_TOTAL_BUDGET_S", "120"))
    EMBED_TIMEOUT_S = float(os.getenv("EMBED_TIMEOUT_S", "15"))
    ```
    reads unprefixed names, while `deploy/compose/docker-compose.yml:358-359` and
    `deploy/env-example:222,224` set/document only
    `VOICEPRINT_MATCH_TOTAL_BUDGET_S` / `VOICEPRINT_EMBED_TIMEOUT_S`. I grepped both
    files for any unprefixed fallback and found none — the container's environment
    never contains `MATCH_TOTAL_BUDGET_S`/`EMBED_TIMEOUT_S`, so operator overrides via
    `.env` are silently ignored; the code always falls back to hardcoded 120/15. By
    contrast, `VOICEPRINT_SUGGEST_THRESHOLD` and `VOICEPRINT_RETENTION_MONTHS` (same
    file, lines 39-40) ARE correctly prefixed and DO match compose/env-example,
    confirming this is an inconsistency specific to these two vars, not a systemic
    naming convention. I also confirmed `VOICEPRINT_MIN_CLIP_SECONDS`,
    `VOICEPRINT_MAX_CLIP_SECONDS`, `VOICEPRINT_FFMPEG_TIMEOUT_SECONDS`
    (voiceprint_matching.py:44-46) have no corresponding entry in either
    docker-compose.yml or env-example — defaults-only, exactly as claimed.
  confidence: high

- id: BUG-004
  verdict: confirmed
  reasoning: |
    `handleAcceptSuggestion` (transcript-viewer.tsx:438-444) calls
    `applySpeakerUpdate(buildSpeakerRename({ speaker: "", speaker_cluster: clusterId },
    candidateName))` — an ordinary cluster rename payload, indistinguishable at the
    PATCH-payload level from a manual free-text rename. Backend-side,
    `update_meeting_speakers`'s rename loop (meetings.py:2138-2140) unconditionally
    appends `{"cluster_id": op.from_cluster, "display_name": op.to_name,
    "operation": "rename"}` to `affected_clusters` for EVERY rename BEFORE it even
    checks `pending_suggestions.get(op.from_cluster)` (line 2141) to decide whether to
    log a `confirm` audit event. There is no separate tag (no `confirm: true`, no
    `profile_id`, no distinct operation string) that would let the frontend tell
    "accepted an existing voiceprint match" apart from "typed a brand-new name."
    `getRenameEnrollCandidates` (speaker-edit.ts:101-107) filters purely on
    `cluster.operation === "rename"`, so `applySpeakerUpdate`'s success handler
    (transcript-viewer.tsx:411-425) fires the enroll-offer toast for both cases
    identically. This directly contradicts the commit message's stated scope
    ("enroll offer after manual rename only") and the finder's suggested fix (tag the
    `affected_clusters` entry when a pending suggestion matched) is the correct
    remediation.
  confidence: high

- id: BUG-005
  verdict: confirmed
  reasoning: |
    `sweeps.py:930-942`:
    ```
    now = datetime.utcnow()
    if (_voiceprint_retention_last_run is not None
        and (now - _voiceprint_retention_last_run).total_seconds()
        < VOICEPRINT_RETENTION_SWEEP_INTERVAL_SECONDS):
        return 0
    _voiceprint_retention_last_run = now
    ...
    async with db_session_factory() as db:
        rows = (await db.execute(...)).scalars().all()   # can raise
    ```
    The module-level guard is stamped at line 942, before the `async with` block that
    performs the actual (fallible) DB query. I confirmed the caller,
    `start_sweeps`'s loop (sweeps.py:1054-1062), wraps the call in
    `try: ... except Exception as e: logger.error(...)` with no `finally`/reset of
    `_voiceprint_retention_last_run` on failure. Since this is a plain Python module
    global (persists for the process lifetime, not per-request state), a single
    transient DB error on any invocation disables the 24-month PII retention sweep for
    a full `VOICEPRINT_RETENTION_SWEEP_INTERVAL_SECONDS` (86400s = 24h), and repeats
    indefinitely under a persistent failure, exactly as claimed.
  confidence: high

- id: BUG-006
  verdict: confirmed
  reasoning: |
    `services/voiceprint-service/main.py:282-306` (`/embed`): the Content-Length
    pre-check (lines 286-295) is best-effort only, then `audio_bytes =
    await _extract_audio_bytes(request)` runs unconditionally, and only AFTER that
    call returns does `len(audio_bytes) > VOICEPRINT_MAX_AUDIO_BYTES` get checked
    (lines 301-306). `_extract_audio_bytes` (main.py:222-255) calls
    `await request.body()` for the JSON path or `await request.form()` for
    multipart — both fully buffer the request body into memory with no incremental
    size ceiling; there is no ASGI-level max-body-size middleware/config visible in
    `main.py`, `Dockerfile`, or the compose service definition. A client omitting
    `Content-Length` (or lying under the cap, or using chunked transfer-encoding) can
    have its entire body read into memory before the real 413 check ever fires. I
    confirmed `.pipeline/adapters/voiceprint-embedder.adapter.json:49` explicitly lists
    "/embed rejects payloads larger than VOICEPRINT_MAX_AUDIO_BYTES with 413" as a
    validated contract item, so the documented safety boundary and the actual
    enforcement point genuinely diverge. (Practical risk is somewhat mitigated by this
    being an internal-compose-network-only service called by meeting-api with
    small, controlled clip sizes, but the code-level claim is accurate as stated.)
  confidence: high

- id: BUG-007
  verdict: confirmed
  reasoning: |
    `_download_master_to_tempfile` (voiceprint_matching.py:229-237):
    ```
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    await asyncio.to_thread(storage.download_file_to_path, storage_path, path)
    return path
    ```
    `tempfile.mkstemp` creates the file on disk immediately (line 234). I read all
    three `download_file_to_path` implementations in `storage.py` (default fallback
    lines 48-55, S3 lines 218-227, GCS lines 382-387) — every one can raise (network
    error, missing object, credential/API failure) while writing to or reading for
    `dest_file_path`. If it raises, `_download_master_to_tempfile` propagates the
    exception without returning `path`. Its only caller,
    `embed_clip_from_ranges` (voiceprint_matching.py:259-278), does
    `src_path = await _download_master_to_tempfile(...)` followed by a `try/finally:
    os.unlink(src_path)` that wraps only the CODE AFTER that assignment — so if the
    download call itself raises, `src_path` is never bound and the `finally` block
    is never entered. The empty temp file at `path` is leaked on disk for the
    container's lifetime, one per failed matching attempt per cluster, exactly as
    claimed.
  confidence: high

- id: BUG-008
  verdict: confirmed
  reasoning: |
    `services/voiceprint-service/main.py:201-203`:
    ```
    provided = auth_header[len("Bearer "):].strip()
    if provided != token:
        raise HTTPException(status_code=401, detail="invalid bearer token")
    ```
    This is a plain Python string `!=`, which short-circuits at the first differing
    byte — a textbook non-constant-time comparison on a bearer-token credential
    guarding a biometric-data endpoint. `secrets.compare_digest` is not imported or
    used anywhere in this file. The mechanical claim is exactly correct; severity is
    fairly rated "low" given the service sits on an internal compose network per the
    finder's own caveat.
  confidence: high

- id: BUG-009
  verdict: confirmed
  reasoning: |
    `services/meeting-api/meeting_api/voiceprints.py:114-123`:
    ```
    profile = (await db.execute(
        select(SpeakerProfile).where(
            SpeakerProfile.user_id == current_user.id,
            SpeakerProfile.display_name == req.display_name,
        )
    )).scalars().first()
    if profile is None:
        profile = SpeakerProfile(user_id=current_user.id, display_name=req.display_name)
        db.add(profile)
        await db.flush()
    ```
    No `.with_for_update()` on the SELECT. I read the `SpeakerProfile` model
    definition (`models.py:199-213`): `__table_args__` only has a non-unique index
    (`Index('ix_speaker_profile_user_id', 'user_id')`) — no unique constraint on
    `(user_id, display_name)` anywhere. Two concurrent enroll-from-cluster calls with
    the same `display_name` can both pass the SELECT before either commits, both
    insert a new `SpeakerProfile` row, and both succeed — a genuine TOCTOU race
    producing duplicate identity profiles, exactly as described.
  confidence: high

- id: BUG-010
  verdict: confirmed
  reasoning: |
    `rejectedSuggestionClusters` (transcript-viewer.tsx:144) is a
    `useState<Set<string>>` initialized empty. I grepped every reference in the file:
    it is only ever added-to on reject (`handleRejectSuggestion`,
    transcript-viewer.tsx:451) and only ever removed on the reject API call's own
    failure (lines 453-457, rollback of the optimistic update). There is no
    `useEffect` keyed on `meeting.id` or on a transcript refetch that clears or
    reconciles this set, and it gates the suggestion overlay at line 1179-1183
    (`speaker_suggestion: rejectedSuggestionClusters.has(group.key) ? undefined : ...`)
    purely by the raw `cluster_id` string. Since `mode="replace"` matching
    (voiceprint_matching.py:399-410) explicitly clears and recomputes
    `speaker_suggestions` and can reuse the same lane-derived `cluster_id` for a
    genuinely new run, a fresh suggestion for a previously-rejected cluster stays
    suppressed client-side for as long as the component instance is mounted with that
    `meeting.id` — no reload path exists in this file to invalidate it. The
    finder's characterization ("only grows... never expires") is accurate.
  confidence: high

- id: BUG-011
  verdict: confirmed
  reasoning: |
    `voiceprint_matching.py:293-301` (`_cosine_similarity`) guards the zero-norm case
    (`if denom == 0.0: return 0.0`) but performs no `math.isfinite`/NaN check. If
    either embedding vector contains a NaN component — plausible via `_embed_clip`'s
    `[float(x) for x in embedding]` (line 256) on a malformed/degenerate
    voiceprint-service response, since Python's `json.loads` accepts literal `NaN`
    tokens by default (`allow_nan=True`) — `np.linalg.norm`/`np.dot` propagate NaN and
    the function returns NaN rather than raising. `max(scored, key=lambda t: t[2])`
    (line 486) with no pre-filter: Python's `max` is a left-to-right fold using `>`
    comparisons, and any comparison against NaN is False. If the NaN-scored candidate
    happens to be first in `voiceprints`' (DB row) order, EVERY subsequent real
    comparison `real_score > NaN` evaluates False, so `max` incorrectly keeps the NaN
    entry as "best" instead of a legitimately higher-scoring real candidate — this is
    order-dependent, non-deterministic undefined behavior exactly as the finder
    describes. I'll flag one imprecision in the finder's writeup: because
    `NaN >= VOICEPRINT_SUGGEST_THRESHOLD` is also False, the practical failure mode
    when NaN wins the fold is a SILENTLY DROPPED/SKIPPED suggestion (and NaN pollution
    of the `match_attempt` audit log's `scores`/`top_similarity`), not literally "a
    wrong speaker gets suggested" as stated — but the underlying technical defect
    (missing `isfinite` filtering, undefined max-under-NaN behavior) is correct and
    real.
  confidence: medium

- id: BUG-012
  verdict: confirmed
  reasoning: |
    `update_meeting_speakers`'s rename loop (meetings.py:2141-2154) explicitly checks
    `pending_suggestions.get(op.from_cluster)` and does
    `del pending_suggestions[op.from_cluster]` plus a `confirm` audit event when a
    pending suggestion existed for that cluster. The merge loop
    (meetings.py:2161-2201) has no equivalent: it iterates `op.clusters`, updates
    `clusters_map`/`aliases`, and appends `operation: "merge"` entries to
    `affected_clusters`, but never reads or mutates `pending_suggestions` /
    `data["speaker_suggestions"]` for any of the merged-away source clusters. I
    confirmed the finder's mitigating claim too: `_overlay_speaker_suggestions`
    (collector/endpoints.py:257,272) only overlays a suggestion when
    `seg.speaker_mapping_status == "needs_review"`, and the merge's own
    `UPDATE Transcription SET speaker=op.to_name` clears that status for the merged
    rows — so today this produces a growing, un-audited orphaned key in the
    `speaker_suggestions` JSONB blob rather than a user-visible leak, exactly as
    characterized (latent correctness gap, asymmetric with the rename branch's
    handling, no `confirm`/reject-equivalent audit trail for the merge case).
  confidence: high

summary: |
  All 12 findings were independently re-traced against the actual commit aae86b5
  code (webhook_delivery.py, webhooks.py, schemas.py, voiceprint_matching.py,
  meetings.py, sweeps.py, voiceprints.py, models.py, database.py,
  voiceprint-service/main.py, transcript-viewer.tsx, speaker-edit.ts,
  collector/endpoints.py, docker-compose.yml, env-example). Every claim held up
  under adversarial scrutiny — including checking `expire_on_commit` session config
  for BUG-002, tracing the actual webhook-firing order relative to the sweep-based
  matching follow-up for BUG-001, verifying absence of unique constraints for
  BUG-009, and confirming no reconciliation/reset logic exists for BUG-005/BUG-010.
  No false positives were found. Given the scoring rule (correctly disproving a
  false positive earns its score; wrongly disproving a real bug costs DOUBLE), the
  correct action is to confirm all 12 rather than manufacture a disproof to score
  points on a genuine finding.

score:
  earned: 0
  rationale: >
    No findings were disproved. Zero false-positive-disproval points earned,
    but also zero double-penalty risk, since no confirmed bug was wrongly
    marked disproved. Reviewer's total claimed severity score of 46 stands
    unchallenged by this adversarial pass.
