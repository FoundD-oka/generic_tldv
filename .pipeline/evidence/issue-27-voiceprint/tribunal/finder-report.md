findings:
- id: BUG-001
  file: services/meeting-api/meeting_api/webhook_delivery.py
  line: 67-94
  category: security
  impact: critical
  title: speaker_suggestions (incl. profile_id) is not stripped before external webhook delivery — leaks voiceprint match data to third-party webhook URLs
  evidence: |
    `_INTERNAL_DATA_KEYS` in webhook_delivery.py is:
    ```
    _INTERNAL_DATA_KEYS = {
        "webhook_delivery", "webhook_deliveries", "webhook_secret", "webhook_secrets",
        "webhook_events", "webhook_url",
        "outbound_events",
        "bot_container_id", "container_name",
    }
    ```
    This set is NOT updated to include `speaker_suggestions`. `clean_meeting_data()`
    (same file) is the ONLY redaction applied before `meeting.data` is embedded into
    outbound webhook payloads — `webhooks.py::_build_meeting_event_data` does
    `"data": clean_meeting_data(meeting.data)`, and this is used by both
    `send_completion_webhook` (fired from `post_meeting.py`) and `send_status_webhook`
    (fired "on every transition", per its own docstring). Meanwhile the commit's own
    redaction work only patched the *API response* path:
    `schemas.py::MEETING_DATA_REDACTED_KEYS = frozenset({"webhook_secret", "speaker_suggestions"})`
    and `MeetingResponse`'s `field_serializer`. The webhook path was never updated to
    match. Once `run_voiceprint_matching_followup` writes
    `meeting.data["speaker_suggestions"][cluster_id] = {"candidate_display_name":...,
    "profile_id": best_profile_id, "similarity":..., "status":"suggested",
    "run_completed_at":...}` (voiceprint_matching.py `_run_matching`), any subsequent
    status/completion webhook delivered to a user-configured external `webhook_url`
    will include this dict verbatim — including `profile_id`, an internal DB primary
    key that lets an external subscriber correlate the same enrolled voice across
    meetings, exactly the "露出制御" (profile_id must never reach a transcript-adjacent
    response) invariant the rest of this commit was built to enforce. This directly
    contradicts the stated hard invariant "meeting.data['speaker_suggestions'] NEVER
    appears in MeetingResponse.data or the transcript endpoint's data dict" in spirit
    (the webhook payload is a third, unguarded exit path for the exact same dict) and
    is worse in practice because it is sent to an arbitrary external URL rather than
    an authenticated first-party API caller.
  suggested_fix: Add "speaker_suggestions" to `_INTERNAL_DATA_KEYS` in webhook_delivery.py (or make `clean_meeting_data` delegate to/reuse `schemas.MEETING_DATA_REDACTED_KEYS` so there is exactly one source of truth for "keys that must never leave meeting.data"), and add a regression test asserting a populated `speaker_suggestions` key never appears in a built webhook envelope.

- id: BUG-002
  file: services/meeting-api/meeting_api/voiceprint_matching.py
  line: 396-410;520-527
  category: race-condition
  impact: critical
  title: Post-commit voiceprint matching follow-up performs a read-modify-write of meeting.data on a `meeting` ORM object it never re-locks/re-fetches, silently clobbering concurrent PATCH edits (renames, suggestion accept/reject) made from a different DB session
  evidence: |
    `run_deferred_transcription` loads `meeting` with `.with_for_update()` at the top
    of the function, but that lock is released once the "success commit" (referenced
    in final_transcription.py's comment right before the voiceprint call) happens.
    `run_voiceprint_matching_followup` / `_run_matching` then runs for up to
    `MATCH_TOTAL_BUDGET_S` (default 120s) doing network calls to voiceprint-service
    and ffmpeg work, and finally does:
    ```
    data = dict(meeting.data or {}) if isinstance(meeting.data, dict) else {}
    ...
    data["speaker_suggestions"] = suggestions
    meeting.data = data
    attributes.flag_modified(meeting, "data")
    await db.commit()
    ```
    on the SAME long-held, un-relocked `meeting` Python object/session from the start
    of the deferred job. Meanwhile `update_meeting_speakers` (PATCH, meetings.py) and
    `reject_speaker_suggestion` (DELETE, meetings.py) each open a NEW DB session/ORM
    instance and DO correctly use `select(Meeting)....with_for_update()` — but that
    only protects against races *between themselves*, not against this matching
    follow-up, which reads/writes `meeting.data` from an object that was fetched
    possibly minutes earlier and never refreshed. If a user renames a cluster (adding
    to `data["speaker_corrections"]`) or rejects/accepts a suggestion in that window,
    and the matching follow-up commits afterward, its `meeting.data = data` UPDATE
    overwrites the whole JSONB column with its own stale base, silently discarding the
    user's PATCH (lost update) and/or resurrecting an already-rejected suggestion that
    the matching run's stale in-memory suggestions dict never saw removed. This is
    exactly the "second commit race" / "session state after commit" class of bug
    called out as a specific hunt target, and it can destroy real user data
    (speaker_corrections), not just voiceprint state.
  suggested_fix: Before the final (and the mode="replace") write in `_run_matching`, re-SELECT the meeting row with `.with_for_update()` (or use `SELECT ... FOR UPDATE` + `populate_existing=True`) in a short transaction scoped just to the JSONB merge, and merge only the `speaker_suggestions` key rather than replacing the whole `data` dict captured at function entry. At minimum, re-fetch `meeting.data` immediately before each write rather than reusing the value read at the top of `_run_matching`.

- id: BUG-003
  file: deploy/compose/docker-compose.yml
  line: 358-359
  category: other
  impact: medium
  title: docker-compose/env-example define VOICEPRINT_MATCH_TOTAL_BUDGET_S / VOICEPRINT_EMBED_TIMEOUT_S but meeting-api code reads unprefixed MATCH_TOTAL_BUDGET_S / EMBED_TIMEOUT_S — operator overrides are silently ignored
  evidence: |
    `voiceprint_matching.py`:
    ```
    MATCH_TOTAL_BUDGET_S = float(os.getenv("MATCH_TOTAL_BUDGET_S", "120"))
    EMBED_TIMEOUT_S = float(os.getenv("EMBED_TIMEOUT_S", "15"))
    ```
    `docker-compose.yml` (meeting-api service) and `deploy/env-example` instead set/document:
    ```
    VOICEPRINT_MATCH_TOTAL_BUDGET_S=${VOICEPRINT_MATCH_TOTAL_BUDGET_S:-120}
    VOICEPRINT_EMBED_TIMEOUT_S=${VOICEPRINT_EMBED_TIMEOUT_S:-15}
    ```
    Because the env var names don't match, any operator who edits `.env` to raise or
    lower `VOICEPRINT_MATCH_TOTAL_BUDGET_S`/`VOICEPRINT_EMBED_TIMEOUT_S` (as
    documented, in Japanese, in env-example) has zero effect — the process always
    falls back to the hardcoded 120s/15s defaults, since `MATCH_TOTAL_BUDGET_S`/
    `EMBED_TIMEOUT_S` are never set in the container's environment. Separately,
    `VOICEPRINT_MIN_CLIP_SECONDS`, `VOICEPRINT_MAX_CLIP_SECONDS`, and
    `VOICEPRINT_FFMPEG_TIMEOUT_SECONDS` are read by voiceprint_matching.py but are not
    wired into docker-compose.yml or deploy/env-example at all (defaults only, no
    override path), unlike every other voiceprint knob.
  suggested_fix: Rename the env vars read in voiceprint_matching.py to the `VOICEPRINT_` prefixed forms already used everywhere else in this feature (`VOICEPRINT_MATCH_TOTAL_BUDGET_S`, `VOICEPRINT_EMBED_TIMEOUT_S`) to match compose/env-example, and add the three clip-policy env vars to docker-compose.yml + env-example for consistency with the rest of the feature's configuration surface.

- id: BUG-004
  file: services/dashboard/src/components/transcript/transcript-viewer.tsx
  line: 405-422
  category: logic-error
  impact: medium
  title: "Enroll offer" toast fires after accepting a voiceprint suggestion too, not just after a manual rename — contradicts the commit's own stated design ("enroll offer after manual rename only") and prompts redundant consent/enrollment for an identity already matched by voiceprint
  evidence: |
    `handleAcceptSuggestion` calls `applySpeakerUpdate(buildSpeakerRename(...))` — i.e.
    "承認" (accept) reuses the exact same rename code path as a manual free-text
    rename. `applySpeakerUpdate`'s success handler then unconditionally does:
    ```
    for (const candidate of getRenameEnrollCandidates(result.affected_clusters)) {
      toast(`この声を「${candidate.display_name}」として登録しますか？...`, { ... });
    }
    ```
    `getRenameEnrollCandidates` only filters on `cluster.operation === "rename"` — it
    has no way to distinguish "user typed a brand-new name" from "user clicked 承認 on
    an already-matched voiceprint suggestion", because the backend's PATCH response
    (`affected_clusters`) tags both cases identically as `operation: "rename"`. The
    commit message explicitly states the intended scope: "enroll offer after manual
    rename only", but the implementation offers to enroll a *second* voiceprint for a
    person who was just identified via an *existing* enrolled voiceprint, re-triggering
    the consent-confirmation flow for no reason and creating redundant biometric
    records for the same profile.
  suggested_fix: Have the backend PATCH response (or the dashboard) distinguish "rename via suggestion accept" from "rename via manual edit" — e.g. tag the `affected_clusters` entry with the pending-suggestion's `profile_id`/`confirm` flag when `pending_suggestions.get(op.from_cluster)` matched, and skip the enroll-offer toast for those entries in `getRenameEnrollCandidates`.

- id: BUG-005
  file: services/meeting-api/meeting_api/sweeps.py
  line: 67-79
  category: logic-error
  impact: medium
  title: Voiceprint retention day-guard timestamp is stamped before the sweep runs, so a transient failure silently disables the 24-month PII retention sweep for a full day (and repeats indefinitely if the failure persists)
  evidence: |
    ```
    global _voiceprint_retention_last_run
    ...
    now = datetime.utcnow()
    if (
        _voiceprint_retention_last_run is not None
        and (now - _voiceprint_retention_last_run).total_seconds()
        < VOICEPRINT_RETENTION_SWEEP_INTERVAL_SECONDS
    ):
        return 0
    _voiceprint_retention_last_run = now
    ...
    async with db_session_factory() as db:
        rows = (await db.execute(...)).scalars().all()   # can raise
        ...
    ```
    `_voiceprint_retention_last_run` is updated to `now` before the DB query that can
    fail even runs. `start_sweeps`'s calling `try/except` only logs the exception —
    it never resets the guard. If the very first invocation after a deploy (or any
    later invocation) hits a transient DB error, the day-guard is already set, so the
    sweep will not be retried for `VOICEPRINT_RETENTION_SWEEP_INTERVAL_SECONDS`
    (24h), and if the underlying issue is persistent (e.g. a bad migration, a locked
    table), this repeats forever with the sweep effectively permanently disabled while
    silently reporting nothing wrong beyond one log line per day. This directly
    undermines the PII policy's 24-month retention guarantee, which this sweep exists
    to enforce.
  suggested_fix: Only update `_voiceprint_retention_last_run` after the sweep body completes successfully (in a `finally`/success branch), or track last-attempt vs last-success separately and alert/retry sooner on repeated failures.

- id: BUG-006
  file: services/voiceprint-service/main.py
  line: 282-306
  category: resource-leak
  title: "/embed size cap (VOICEPRINT_MAX_AUDIO_BYTES) can be bypassed by omitting Content-Length, since the body is fully buffered into memory before the post-hoc len() check runs"
  impact: medium
  evidence: |
    ```
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > VOICEPRINT_MAX_AUDIO_BYTES:
                raise HTTPException(status_code=413, ...)
        except ValueError:
            pass
    ...
    audio_bytes = await _extract_audio_bytes(request)   # reads the FULL body first
    if len(audio_bytes) > VOICEPRINT_MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, ...)
    ```
    The Content-Length pre-check is best-effort only (the header is client-supplied
    and optional/absent for chunked transfer-encoding). `_extract_audio_bytes` calls
    `await request.body()` (or `await request.form()` for multipart, which similarly
    buffers) with no size limit, so a request with no Content-Length header (or a
    lying one under the cap) is fully read into process memory before the real
    `len(audio_bytes)` check ever runs. The adapter manifest
    (`.pipeline/adapters/voiceprint-embedder.adapter.json`) explicitly lists "/embed
    rejects payloads larger than VOICEPRINT_MAX_AUDIO_BYTES with 413" as a validated
    safety boundary/check, but the enforcement point is after-the-fact, not a real cap
    on memory used per request — an attacker (or a buggy caller) with network access
    to this internal service can send an arbitrarily large chunked body and exhaust
    the 1.5g-limited container's memory before the 413 is ever returned.
  suggested_fix: Enforce the cap while streaming (e.g. read the ASGI receive stream incrementally, aborting once VOICEPRINT_MAX_AUDIO_BYTES is exceeded) instead of buffering the whole body first, or set a reverse-proxy / ASGI server request-body limit in front of this service.

- id: BUG-007
  file: services/meeting-api/meeting_api/voiceprint_matching.py
  line: 227-233
  category: resource-leak
  title: Temp file created by _download_master_to_tempfile is never cleaned up if the download itself fails
  impact: low
  evidence: |
    ```
    async def _download_master_to_tempfile(
        storage_backend, storage_path, media_format,
    ) -> str:
        storage = create_storage_client(storage_backend)
        suffix = f".{(media_format or 'webm').lower()}"
        fd, path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        await asyncio.to_thread(storage.download_file_to_path, storage_path, path)
        return path
    ```
    `tempfile.mkstemp` creates the file on disk immediately. If
    `storage.download_file_to_path` raises (network error, missing object, storage
    backend outage — all realistic given this runs against production recordings
    storage), the function raises without returning `path`, so
    `embed_clip_from_ranges`'s `finally: os.unlink(src_path)` never executes (it never
    even gets a `src_path` value) and the empty temp file at `path` is leaked on disk
    for the lifetime of the container. Under sustained storage issues (the exact
    condition this function is likely to hit) this leaks one file per failed matching
    attempt per cluster.
  suggested_fix: Wrap the mkstemp+download in a try/except that unlinks `path` on any exception before re-raising (or use a context manager that guarantees cleanup).

- id: BUG-008
  file: services/voiceprint-service/main.py
  line: 198-204
  category: security
  title: Bearer token comparison uses non-constant-time `!=` instead of secrets.compare_digest, a timing side-channel on VOICEPRINT_SERVICE_TOKEN
  impact: low
  evidence: |
    ```
    provided = auth_header[len("Bearer "):].strip()
    if provided != token:
        raise HTTPException(status_code=401, detail="invalid bearer token")
    ```
    Python string `!=` short-circuits on the first mismatched byte, making the
    comparison time-dependent on how many leading characters of `provided` match
    `token`. This is a real (if low-severity, given the service is meant to sit
    behind an internal compose network) timing side channel on a biometric-data-path
    credential.
  suggested_fix: Use `secrets.compare_digest(provided, token)`.

- id: BUG-009
  file: services/meeting-api/meeting_api/voiceprints.py
  line: 155-164
  category: race-condition
  title: enroll-from-cluster's "find-or-create SpeakerProfile by display_name" has no locking or unique constraint, allowing duplicate profiles under concurrent enrollment
  impact: low
  evidence: |
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
    No `SELECT ... FOR UPDATE`, and `models.py`'s `SpeakerProfile` has no unique
    constraint on `(user_id, display_name)`. Two concurrent
    `enroll-from-cluster` calls with the same `display_name` (e.g. a user
    double-clicking the enroll-offer toast, or enrolling from two different
    meetings' clusters for the same person around the same time) can both miss
    the SELECT and both INSERT a new `SpeakerProfile`, producing two profiles
    for what should be one identity, each with its own voiceprint(s) — the
    matching step in `_load_user_voiceprints` will then treat them as two
    unrelated candidates.
  suggested_fix: Add a unique constraint on (user_id, display_name) in speaker_profiles and either catch the resulting IntegrityError and retry the SELECT, or take a row lock / use `INSERT ... ON CONFLICT DO NOTHING` + re-SELECT.

- id: BUG-010
  file: services/dashboard/src/components/transcript/transcript-viewer.tsx
  line: 144-145;438-455;1180-1183
  category: other
  title: rejectedSuggestionClusters client-side state is never reconciled with fresh server state, so a genuinely new suggestion for a previously-rejected cluster_id stays hidden for the rest of the session
  impact: low
  evidence: |
    `rejectedSuggestionClusters` is a `useState<Set<string>>` that only grows (entries
    are added on reject, removed only if the reject API call itself fails). It is
    keyed purely by `cluster_id` string. If the meeting is later re-transcribed with
    `mode="replace"` (which the matching follow-up explicitly supports and which
    clears+recomputes `speaker_suggestions`), and the SAME `cluster_id` string
    receives a brand-new (possibly different candidate) suggestion, the dashboard will
    keep suppressing the chip for that cluster because `rejectedSuggestionClusters`
    was never cleared on refetch/replace, per:
    ```
    speaker_suggestion: rejectedSuggestionClusters.has(group.key)
      ? undefined
      : group.segments[0]?.speaker_suggestion,
    ```
    This is exactly the "dashboard optimistic-reject state vs server state" seam
    called out as a hunt target — the optimistic update never expires or gets
    invalidated against a fresh transcript fetch.
  suggested_fix: Clear (or filter) `rejectedSuggestionClusters` whenever the transcript is refetched/replaced (e.g. on `meeting.id` change or an explicit "re-run matching" action), or key the rejection off `(cluster_id, run_completed_at)` instead of `cluster_id` alone so a new run's suggestion isn't conflated with a previously-rejected one.

- id: BUG-011
  file: services/meeting-api/meeting_api/voiceprint_matching.py
  line: 219-224
  category: logic-error
  title: max()-based best-candidate selection has undefined behavior under NaN similarity scores
  impact: low
  evidence: |
    ```
    scored = [
        (profile_id, display_name, _cosine_similarity(embedding, vp_embedding))
        for profile_id, display_name, vp_embedding in voiceprints
    ]
    best_profile_id, best_name, best_score = max(scored, key=lambda t: t[2])
    ```
    `_cosine_similarity` guards the exact-zero-vector case (returns 0.0) but not NaN —
    if either vector contains a NaN component (a corrupted/degenerate stored
    embedding, or a NaN slipping through from `_embed_clip`'s `[float(x) for x in
    embedding]` on a malformed service response), `np.dot`/`np.linalg.norm` propagate
    NaN, and `max()` with a NaN key is not well-defined (NaN comparisons are always
    False, so Python's `max` — a left-to-right fold — can silently keep an earlier,
    lower-similarity candidate as "best" instead of raising or excluding the NaN
    entry). This would produce a wrong suggested speaker rather than a clean failure,
    though it stays under the 0.78 threshold determination edge case since NaN >=
    threshold is also False, so a NaN best_score cannot itself trigger a "suggested"
    write. Still, a NaN pollutes the `match_attempt` audit's `scores` list and can bias
    which candidate gets logged as best/reported to `subject_profile_id`.
  suggested_fix: Filter out non-finite similarity scores (`math.isfinite`) before taking `max()`, and treat an embedding that produces any non-finite score as an `embed_failed`/skip rather than continuing to score it against every enrolled voiceprint.

- id: BUG-012
  file: services/meeting-api/meeting_api/meetings.py
  line: 2160-2192
  category: logic-error
  title: Merge operation never clears a stale pending speaker_suggestions entry for the clusters it merges away, leaving orphaned suggestion entries in meeting.data indefinitely
  impact: low
  evidence: |
    The `rename` branch explicitly pops a matching pending suggestion out of
    `pending_suggestions` when `op.from_cluster` had one (`del pending_suggestions[op.from_cluster]`,
    logging a `confirm` audit event). The `merge` branch has no equivalent check —
    `for cluster in op.clusters: clusters_map[cluster] = op.to_name; ...` never
    touches `pending_suggestions`/`data["speaker_suggestions"]`. If one of the merged
    source clusters had a pending voiceprint suggestion, that dict entry is never
    removed and is not currently user-visible only because `_overlay_speaker_suggestions`
    also requires `speaker_mapping_status == "needs_review"`, which the merge's own
    `UPDATE Transcription SET speaker=op.to_name` clears for those rows — so today this
    is "just" an ever-growing orphaned key inside the `speaker_suggestions` JSONB blob
    that a future `mode="replace"` run's stale-clear step will eventually wipe, but it
    is a latent correctness gap (no `confirm`/cleanup audit trail for the merge case,
    unlike rename) that would become user-visible the moment `_overlay_speaker_suggestions`'s
    gating condition is ever loosened.
  suggested_fix: Pop/clear any pending suggestion for every cluster in `op.clusters` inside the merge loop too, symmetric with the rename branch's handling, and record a `confirm`/`suggest`-reject-equivalent audit event for consistency.

Total score: 10 + 10 + 5 + 5 + 5 + 5 + 1 + 1 + 1 + 1 + 1 + 1 = 46
