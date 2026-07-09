# Adversarial Review — issue #25 audio lanes (commit 9aed38b)

Role: defense attorney. Every finding re-verified against the actual code.

verdicts:
- id: BUG-001
  verdict: confirmed
  reasoning: >
    build-browser-utils.js:32-37 hardcodes the exposed keys: `window.VexaBrowserUtils = {
    BrowserAudioService: ..., BrowserMediaRecorderPipeline: ..., BrowserWhisperLiveService: ...,
    generateBrowserUUID: ... }` — no BrowserLaneRecorderManager, and the generated
    dist/browser-utils.global.js (line 570) shows the same 4-key object even though the inner
    module does export the class (dist line 564 `exports.BrowserLaneRecorderManager`).
    The injected bundle IS this file (googlemeet/recording.ts:70/210 → ensureBrowserUtils with
    browser-utils.global.js; package.json build runs `node build-browser-utils.js`).
    googlemeet/recording.ts:132-134 does `new u.BrowserLaneRecorderManager({...})` where
    `u = (window as any).VexaBrowserUtils` — undefined property → TypeError. The claimed ordering
    also holds: the combined pipeline is started at line 123 (`await pipeline.start()`), the lane
    block runs at 132, and the #285 audio-processor init follows it. Propagation is unguarded:
    startBrowserCapture's evaluate rejects → MediaRecorderCapture.start (audio-pipeline.ts:760,
    no try/catch) → UnifiedRecordingPipeline.start (line 279) → `await pipeline.start()` at
    googlemeet/recording.ts:203. Enabling RECORD_PARTICIPANT_LANES crashes recording startup.
  confidence: high

- id: BUG-002
  verdict: confirmed
  reasoning: >
    No lane start offset exists anywhere in the pipeline: LaneAudioChunk
    (audio-pipeline.ts:135-145) has laneKey/laneId/laneLabel/laneIdSource/format/data/seq/isFinal
    only; upload metadata (recording.ts:249-256) adds media_type/lane_id/lane_label/lane_id_source;
    the media_files lane object (recordings.py:543-549) stores the same three lane fields. Lanes
    start mid-meeting by design — the 15s rescan (browser.ts start()) picks up late joiners, and
    "New participantId/track ⇒ new lane by design" (browser.ts comment) — so a lane's MediaRecorder
    t=0 is that lane's start time, not the meeting's. `_transcribe_lanes` merges raw times
    (`merged.sort(key=lambda s: (float(s.get("start", 0)), ...))`) and rows are stored with raw
    start/end (final_transcription.py:950-951, 965-966), so a late joiner's speech lands at the top
    of the transcript. It also passes the GLOBAL DOM timeline per lane:
    `_parse_segments(tx, ..., speaker_events=speaker_events, fallback_duration=duration)` inside
    `_transcribe` — lane-relative segment times voted against master-relative DOM events.
    Real design flaw affecting normal usage (late joiners are normal).
  confidence: high

- id: BUG-003
  verdict: disproved
  reasoning: >
    The claimed failure path cannot execute. The only producer of lane uploads is
    `_handleLaneChunk` (audio-pipeline.ts:320-368). Data-bearing lane chunks are always emitted
    with `isFinal: false` — browser.ts:442-447 hardcodes `isFinal: false` in ondataavailable — and
    the only isFinal=true lane chunk is the empty onstop marker (browser.ts:478-483,
    `base64: ""`), which `_handleLaneChunk` drops at audio-pipeline.ts:330-333 before any upload.
    `uploadChunk` therefore never sends a lane request with is_final=true, and the metadata always
    contains an explicit is_final (recording.ts:246), so the Form default at recordings.py:351 is
    never in play for lane traffic. No other caller of /internal/recordings/upload sends lane-*
    media types. The finder's own evidence concedes this ("currently masked ... never reaches the
    server") — i.e. the reported failure (mid-meeting COMPLETED flip + recording.completed webhook
    from a lane chunk) is a hypothetical about a future fix, not a bug in this commit. A
    defense-in-depth gate would be nice; no executing defect exists.
  confidence: high

- id: BUG-004
  verdict: disproved
  reasoning: >
    Factually the empty lane final chunk is dropped (audio-pipeline.ts:330-333), but this is
    byte-identical to the PRE-EXISTING mixed-master behavior: `_handleChunk` has the same
    unchanged guard (audio-pipeline.ts:370-374), and the mixed path's onstop marker is also
    `base64: ""` (browser.ts:478-483) — the mixed recording's is_final upload has never reliably
    reached the server either. The architecture explicitly compensates: post_meeting.py:277-288
    ("Bug B: ... recordings whose finalizer chunk never reached the server ... stayed IN_PROGRESS
    forever") stamps is_final via the reconciler, and lane finality that anything consumes is
    `finalized_by == "recording_finalizer.master"` (final_transcription.py `_lane_master_sources`),
    stamped by the finalizer at recording_finalizer.py:669 — not upload-time is_final. The data
    chunks (the thing the stopBrowserCapture comment's "final chunks must flush" protects) DO
    flush. No functional consequence was identified: this is the system's established finality
    design applied consistently to lanes, not a defect introduced by the commit.
  confidence: medium

- id: BUG-005
  verdict: confirmed
  reasoning: >
    Real unsynchronized race. `ondataavailable` performs `await event.data.arrayBuffer()` + base64
    encode + `await this.opts.chunkCallback(...)` (browser.ts:427-447) while `onstop`
    independently awaits only its own empty-marker callback and then resolves finalChunkPromise
    (browser.ts:472-496) — nothing makes onstop wait for in-flight ondataavailable work
    (MediaRecorder fires the last dataavailable task before stop, but the async handler suspends
    at its first await, letting onstop's handler run to completion). So `pipeline.stop()` →
    `BrowserLaneRecorderManager.stopAll()` (browser.ts:723-731) can resolve before the last data
    chunk was delivered to Node, and `UnifiedRecordingPipeline.stop()` snapshots
    `Array.from(this.laneQueues.values())` (audio-pipeline.ts:310) — a one-shot barrier that
    misses a chunk enqueued after the snapshot. In practice the combined p.stop() round-trips and
    the mixed uploadQueue drain usually give the lane chunk time to register, but when the mixed
    queue is empty/fast the window is real, and the code comment ("lane final chunks ... are
    already enqueued", audio-pipeline.ts:308-309) asserts a guarantee the code does not provide.
    Up to timesliceMs (30s) of lane tail audio can be silently lost.
  confidence: medium

- id: BUG-006
  verdict: disproved
  reasoning: >
    The double-start requires two `scan` passes to be concurrently in flight, which requires a
    single scan pass to still be running when the next 15s interval tick fires. `start()` awaits
    the initial scan BEFORE installing the interval (browser.ts: `await this.scan(initialElements);
    ... window.setInterval(...)`), so initial/rescan overlap is impossible; rescan/rescan overlap
    needs one pass to span the full 15,000ms interval. Every await in the pass is millisecond-scale:
    `sha1Hex(track.id)` is a crypto.subtle digest of a ~40-char string, and
    `BrowserMediaRecorderPipeline.start()` contains no awaited I/O at all (mime probe, constructor,
    handler assignment, synchronous `recorder.start()` — browser.ts:376-503). Within one pass the
    loop is sequential and `this.lanes.set(track.id, ...)` lands before the next element is
    examined, so duplicate tracks across elements are caught by `this.lanes.has(track.id)`
    (browser.ts:651). Even under main-thread jank, the suspended pass's promise-resolution job runs
    before the delayed timer macrotask, so the pass completes first. The TOCTOU shape exists on
    paper but no realistic execution reaches it; chunk_seq collision from this path is not a real
    outcome.
  confidence: medium

- id: BUG-007
  verdict: confirmed
  reasoning: >
    scan() computes `stream = (el.srcObject as MediaStream) || ((el as any).captureStream &&
    (el as any).captureStream()) || null` for every element on every pass (browser.ts:640-648),
    and the rescan feeds it the blanket `document.querySelectorAll("audio, video")`
    (browser.ts:614-616) — unlike the combined pipeline, which filters to srcObject-bearing
    elements only (browser.ts findMediaElements:44-57). Per spec, each
    HTMLMediaElement.captureStream() call returns a NEW MediaStream with NEW track objects/ids, so
    for any src-based media element (Meet chime/effect players, any non-srcObject element), every
    15s rescan yields tracks that fail `this.lanes.has(track.id)` and start a fresh
    MediaRecorder+upload lane until maxLanes is exhausted (browser.ts:653-661), locking real
    participants out and duplicating transcribed audio. Old captureStream lanes never end (their
    tracks stay live), so the duplicates all keep recording. The guard is keyed only on track.id;
    nothing keys on the element. Mechanism verified; the code pattern is defective whenever any
    non-srcObject media element exists.
  confidence: medium

- id: BUG-008
  verdict: confirmed
  reasoning: >
    `stopAll()` sets `this.stopped = true`, clears the interval, snapshots
    `Array.from(this.lanes.values())` and clears the map (browser.ts:723-731), but `this.stopped`
    is checked ONLY in the interval callback (browser.ts:613) — neither in `scan`'s loop nor in
    `startLane`. Unlike a scan/scan overlap, stopAll's timing is arbitrary (teardown), so it can
    land while a rescan is suspended inside `startLane` (at the sha1Hex or pipeline.start await);
    the resumed continuation then does `this.lanes.set(...)` after the map was cleared and starts
    a recorder no one will ever stop. That orphan MediaRecorder keeps invoking
    __vexaSaveLaneChunk through teardown; its chunks land in storage after finalize, re-opening
    the lane entry bookkeeping. Window is narrow (a NEW lane must be starting at the teardown
    instant) but element churn at meeting end makes it plausible, and the fix cost (check
    this.stopped in scan/startLane) shows a genuinely missing guard. Real race, low frequency.
  confidence: medium

- id: BUG-009
  verdict: disproved
  reasoning: >
    Not an executing defect and not introduced by this commit. The acceptance
    `media_type = meta.get("media_type", media_type)` (recordings.py:372) and the interpolation
    into storage_path (recordings.py:439) are PRE-EXISTING lines — the commit did not add them.
    The endpoint is internal-only (`include_in_schema=False`, recordings.py:341) behind
    require_recording_upload_token — an HS256 JWT pinned to one meeting (recordings.py:402-403) —
    and meeting-api is compose `expose:`-only (recordings.py:410-413 TODO documents exactly this
    threat model). No untrusted principal can supply media_type. The only in-tree lane caller
    generates laneKey as 10 lowercase hex chars from SHA-1 (browser.ts:677). "Escape the prefix"
    also fails on the shipped backends: MinIO/S3/GCS keys are flat strings — "lane-../../x" is a
    literal key, not a traversal. What remains is a defense-in-depth wish (whitelist regex) against
    a compromised internal component, which could already write arbitrary keys directly. Hardening
    suggestion, not a bug.
  confidence: medium

- id: BUG-010
  verdict: confirmed
  reasoning: >
    `prepared = await asyncio.gather(*(_prepare(lane) for lane in lane_sources), ...)` retains
    every lane's decoded WAV simultaneously; the semaphore (LANE_STT_CONCURRENCY, default 2)
    bounds concurrency of download/ffmpeg, not retention. `_convert_audio_to_wav` produces
    16kHz/mono/16-bit PCM (`ffmpeg -ar 16000 -ac 1 -f wav`, final_transcription.py:493) =
    ~115MB/hour, and the duration budget check runs only AFTER all lanes are downloaded and
    converted (`total_duration = sum(...)` after the gather), so an over-budget meeting pays full
    peak memory before falling back — the cap bounds STT cost, never RSS. Even within budget,
    4h of allowed lane audio ≈ 460MB of WAV plus webm intermediates, and docker-compose.yml gives
    meeting-api `mem_limit: 1g` — an 8-lane 2h meeting (16h ≈ 1.8GB WAV) OOM-kills the whole API
    container. Real unbounded-retention defect with a hard memory ceiling in the shipped deploy.
  confidence: medium

- id: BUG-011
  verdict: confirmed
  reasoning: >
    The skip guard (final_transcription.py:773-782) adds `and not _lane_master_sources(meeting)`
    on the premise that "Lane masters carry their own identity". But the guard is evaluated
    BEFORE knowing whether the lane path will be used: if `_transcribe_lanes` raises
    LaneTranscriptionFallback (any lane download/convert/STT failure or budget breach,
    final_transcription.py:869-875), execution falls into the unchanged mixed path (877-900) with
    `speaker_events == []` — exactly the state the guard exists to block — and then replace mode
    unconditionally runs `await db.execute(delete(Transcription).where(...))`
    (final_transcription.py:937) and rewrites rows whose speakers come from `_parse_segments`
    with no DOM events (Unknown). Meaningful existing speaker labels are destroyed in precisely
    the fallback scenario the bypass rationale does not cover. The guard condition is never
    re-checked at fallback time. Genuine logic error.
  confidence: high

- id: BUG-012
  verdict: confirmed
  reasoning: >
    `_lane_master_sources` (final_transcription.py:236-241) requires
    `finalized_by == "recording_finalizer.master"` and silently drops every other lane entry —
    there is no completeness check comparing finalized lanes against the full set of lane-*
    media_files, so `_transcribe_lanes` succeeds on the subset and `lane_used=True` with
    participants missing and no error, violating the stated all-or-nothing contract (the
    docstring's "one failed lane means the whole lane path is abandoned" only covers failures of
    KNOWN lanes). Reachable windows exist: a lane entry created/updated by a late or raced chunk
    after the exit-callback finalize commit (the upload handler creates unfinalized entries at any
    time, recordings.py:550-577); a lane whose chunk listing returned empty in a finalize pass
    (recording_finalizer.py:456-463 returns None, caller `continue`s at 660-662 while the same
    commit finalizes the audio master — asymmetric by construction); reconciler-stamped
    (`post_meeting_reconciler`) lane entries awaiting the next unfinalized sweep while the
    transcription sweep only needs the mixed master. The cross-session merge/mislabel sub-claim is
    also factual: lanes are collected across ALL recordings while rows get the mixed source's
    session_uid (line 972). Real gap versus the invariant the code claims.
  confidence: medium

- id: BUG-013
  verdict: disproved
  reasoning: >
    The two harms claimed — "keeps meetings in the sweep forever" and "sweep budget slots
    permanently consumed by unfixable entries" — both fail against the actual query. The sweep
    candidate set is `select(Meeting.id).where(status in terminal).where(created_at < cutoff)
    .order_by(Meeting.id.desc()).limit(UNFINALIZED_RECORDINGS_LIMIT)` (sweeps.py:514-520): it is
    the NEWEST N terminal meetings regardless of finalization state. An unfixable lane entry
    therefore (a) never crowds out other meetings — the window is recency-based, the same N
    meetings are scanned either way — and (b) ages OUT of scope as soon as N newer terminal
    meetings exist, so "forever" only holds in a fully idle deployment, where the residual cost is
    a handful of list_objects calls per sweep pass — negligible. The precondition is also exotic:
    a lane media_files entry only exists because a chunk upload succeeded (recordings.py:550), so
    zero-chunk-forever requires storage-side loss (lifecycle deletion/wipe) BEFORE any successful
    finalize — normally finalization happens minutes after the meeting, retention windows are
    days. A terminal failure stamp would be tidier, but the reported unbounded-churn/budget-
    exhaustion impact does not hold.
  confidence: medium

- id: BUG-014
  verdict: confirmed
  reasoning: >
    Both recovery sites rebuild lane media_files entries without the `lane` object: inline
    recovery constructs entries with only id/type/format/storage_path/... (sweeps.py:444-461) and
    the sweep does the same (sweeps.py:593-610) — `_parse_recording_chunk_key` now admits lane-*
    types (sweeps.py:345) so lane entries ARE recreated, but lane_label/lane_id exist only in
    upload metadata and are not recoverable from storage keys. After recovery + finalization,
    `_lane_master_sources` yields `lane_label=None` (final_transcription.py:255-256:
    `lane = mf.get("lane") or {}`), so the solo-lane auto-confirm sets the cluster but never the
    speaker name — the headline behavior silently disappears in exactly the recovery scenarios,
    with no log of the identity loss. Factually verified; impact appropriately low.
  confidence: high

- id: BUG-015
  verdict: confirmed
  reasoning: >
    googlemeet/recording.ts:54-57: `Math.max(1, parseInt(process.env.MAX_RECORDING_LANES || "8",
    10) || 8)`. `parseInt("0") === 0` is falsy, so `|| 8` maps an explicit 0 to 8 before the
    Math.max clamp ever sees it — the author's own `Math.max(1, ...)` shows intent to clamp small
    values to a floor, and the `|| 8` subverts it: an operator minimizing lane cost with 0 gets the
    maximum default instead. env-example explicitly documents MAX_RECORDING_LANES as a settable
    knob, and operators can inject env into bot containers via the editable runtime profiles, so
    the input is configuration-reachable. Classic `|| default`-on-numeric footgun; behavior
    verified, opposite-of-intent for a plausible input.
  confidence: medium

- id: BUG-016
  verdict: confirmed
  reasoning: >
    Verified all three dead knobs. (1) meeting-api's compose service uses an explicit
    `environment:` list (no env_file, docker-compose.yml:254-370); the commit added only
    `RECORD_PARTICIPANT_LANES` (line 308). `LANE_STT_CONCURRENCY` and
    `MAX_LANE_TOTAL_DURATION_SECONDS` are read by meeting-api at import
    (final_transcription.py:44-47) but appear nowhere in the compose file or helm chart —
    `.env` values are silently ignored and the hardcoded caps always apply. (2)
    `MAX_RECORDING_LANES` is read only via `process.env` in the bot container
    (googlemeet/recording.ts:56); bot containers are launched by runtime-api with
    `env: {"BOT_CONFIG": ..., "BOT_MODE": ...}` (meetings.py:825) plus the profile env
    (services/runtime-api/profiles.yaml — no lane vars), so the cap is always 8. Same for the
    bot-side `process.env.RECORD_PARTICIPANT_LANES` fallback (recording.ts:53) — dead; only the
    bot_config path (meetings.py:1113) works. Three of four documented knobs (deploy/env-example
    :106-111) are inert, including the plan-mandated STT cost caps.
  confidence: high

- id: BUG-017
  verdict: disproved
  reasoning: >
    Both complaints describe documented, defensible design, not incorrect behavior. The
    `_apply_lane_identity` docstring states the multi-cluster policy explicitly: "keep the
    diarization clusters but namespace them per lane so clusters from different lanes can never
    collide; naming stays with the DOM vote ... (Phase 3 owns real sub-speaker UX)". A segment
    with `speaker_cluster=None` is UNclustered — None is the absence of a cluster, cannot
    "collide" across lanes (and such segments remain distinguishable via the lane-keyed
    segment_id, final_transcription.py:958-959); leaving them un-namespaced matches the
    pre-existing mixed-path semantics where unclustered segments were never correction-targetable
    either. In the solo branch, keeping the `_parse_segments` DOM-vote name when lane_label is
    absent is a best-effort default, strictly no worse than the mixed path's behavior for the same
    segment, and human corrections keyed on the lane cluster still override it by design
    (final_transcription.py:941-946). Any wrongness of that name is BUG-002's misalignment,
    already reported separately — this finding adds no independent defect.
  confidence: medium

- id: BUG-018
  verdict: confirmed
  reasoning: >
    final_transcription.py merge loop: `for segments, detected in results: ... if detected and
    detected != "unknown": detected_language = detected` — the surviving value is whichever lane
    happens to be LAST in JSONB media_files order, and it is stamped onto every stored row
    (`language=detected_language`, line 971) and into final_transcription state. Unlike the mixed
    path (where detection ran over the full mix and naturally reflects the dominant language),
    lane order is arbitrary, so one minority-language participant flips the recorded language for
    the entire meeting. Order-dependent, arbitrary selection of a per-meeting value — real, low
    impact.
  confidence: medium

- id: BUG-019
  verdict: disproved
  reasoning: >
    Not a meaningful leak. `laneQueues` maps laneKey → the lane's latest upload promise
    (audio-pipeline.ts:334-366); once settled, a promise's reaction handlers are cleared per spec,
    so the chunk Buffer captured by the closure is released after the upload completes — the map
    retains only tiny settled Promise objects and 10-char key strings. `skippedOverCap` holds
    track-id strings. The bot process is per-meeting and exits at meeting end; even the finder's
    own compounding scenario (one new key per 15s rescan) yields ~240 entries/hour — bytes, not a
    resource problem. The finder concedes "bounded per meeting in practice." No plausible
    execution turns this into observable impact; classifying spec-level tidiness as a memory-leak
    bug is over-reporting.
  confidence: high

- id: BUG-020
  verdict: confirmed
  reasoning: >
    laneLabelForElement (browser.ts:625-636) resolves the label via
    `el.closest?.("[data-participant-id]")` — it requires the audio element to be a DOM descendant
    of a participant tile. Google Meet plays remote audio through a small pool of document-level
    <audio> elements not nested in tiles; this codebase's own audio discovery reflects that
    (findMediaElements scans ALL `audio, video` elements for srcObject with no tile association,
    browser.ts:37-57, and nothing in the existing Meet speaker-detection ties audio elements to
    tiles — tiles are used only for visual speaking indicators). Tile-nested VIDEO elements carry
    video-only streams (no audio tracks), so `stream.getAudioTracks()` excludes them from lanes.
    Net: lanes come from the pooled audio elements, `closest` returns null, label=null with
    source "stream" — the solo-lane auto-confirm (`speaker = lane_label`) has no label to confirm
    in the platform's normal case. The author's own comment hedges exactly this ("the audio
    element may live inside (or near) a participant tile. When it does not ... the deferred stage
    simply cannot auto-confirm"). Additionally the label is captured once at startLane and never
    refreshed on srcObject/track reuse. The feature's headline behavior is structurally hollow on
    the one platform it was wired to.
  confidence: medium

- id: BUG-021
  verdict: confirmed
  reasoning: >
    Verified, and there is an even easier zombie path than the one reported.
    `startLane` registers the lane BEFORE starting it (`this.lanes.set(track.id, ...)` precedes
    `await pipeline.start()`, browser.ts:704/718). (a) `recorder.start(this.opts.timesliceMs)`
    (browser.ts:499) is outside any try/catch — an InvalidStateError (e.g. the track ended between
    scan's readyState check and start, making the single-track stream inactive; the 'ended'
    listener registered just above never fires for an already-ended track) propagates out of
    startLane, aborts the remaining elements of that scan pass (the `await this.startLane` at
    browser.ts:667 is inside the loop; the rescan `.catch` only logs), and leaves the dead entry
    in `this.lanes` forever — permanently consuming a maxLanes slot with no retry. (b) Worse:
    when MediaRecorder CONSTRUCTION fails, pipeline.start() logs and `return`s silently
    (browser.ts:396-401) — no throw at all, so the lane entry stays registered around a pipeline
    with no recorder, recording nothing, also unretryable. Missing per-lane error isolation and
    missing cleanup on start failure are real defects, appropriately rated low.
  confidence: high

- id: BUG-022
  verdict: disproved
  reasoning: >
    No security boundary is crossed and the "gap" is explicitly documented design. Every cited
    endpoint requires the OWNING user's auth: /media/{id}/download and /raw both resolve via
    `_find_meeting_data_recording(db, user.id, recording_id)` (recordings.py:749, 825) — a lane
    master is only ever served to the user whose meeting it is; that is owner-scoped access to
    their own meeting audio, not a leak. The response-boundary-only scope of the filtering is not
    an oversight — `_public_recording_view`'s docstring states it verbatim: "Only the response
    boundary filters — deletion and finalizer paths read the raw JSONB". The recording.completed
    webhook (recordings.py:600) is delivered to the owner's own configured endpoint — the same
    trust domain that could fetch the data anyway — and per the lane upload flow it practically
    never fires from lane traffic (lane is_final never reaches the server; see BUG-003/004
    analysis). lane_label PII in JSONB follows the identical pre-existing pattern of
    speaker_events, which already stores DOM-scraped participant names in meeting JSONB with the
    same whole-meeting deletion lifecycle. A consistency polish at most; no defect with user
    impact.
  confidence: medium

- id: BUG-023
  verdict: confirmed
  reasoning: >
    Verified: every Transcription row is stored with `session_uid=source.session_uid`
    (final_transcription.py:972) — the MIXED source's session — even though
    `_lane_master_sources` collects lanes across ALL recordings/sessions of the meeting and each
    LaneTranscriptionSource carries its own session_uid that is then discarded. For multi-session
    meetings (bot rejoin) this writes factually wrong session attribution into DB rows. The
    success state likewise records `"source_recording_path": source.storage_path`
    (final_transcription.py:998) — the mixed master, which was never transcribed — alongside
    `"source": "deferred_lane_masters"`; lane_keys are listed but no lane paths, so per-source
    auditing points at the wrong file. Low impact (provenance/metadata), but the stored data is
    genuinely incorrect, not merely stylistic.
  confidence: medium

score:
  Disproved (earned):
    BUG-003 (medium, 5) — lane is_final=true with data is unreachable from any in-tree caller; finder concedes masking
    BUG-004 (medium, 5) — identical to pre-existing mixed-path design; finality is reconciler/finalizer-owned; no functional impact
    BUG-006 (medium, 5) — requires one scan pass to span the full 15s rescan interval; all awaits are ms-scale; interleaving unreachable
    BUG-009 (medium, 5) — acceptance line pre-existing; JWT-scoped internal endpoint; object-store keys make traversal inert; hardening wish
    BUG-013 (medium, 5) — sweep window is newest-N terminal meetings: entries age out and consume no one else's budget; precondition exotic
    BUG-017 (low, 1) — documented design (Phase 3 owns sub-speaker naming); None-cluster cannot collide; no independent defect beyond BUG-002
    BUG-019 (low, 1) — settled promises/strings only; per-meeting process; no observable impact possible
    BUG-022 (low, 1) — all paths owner-authenticated; filtering scope explicitly documented; PII pattern pre-existing (speaker_events)
  Earned score: 5+5+5+5+5+1+1+1 = 28
  Confirmed (no score, penalties avoided): BUG-001, BUG-002, BUG-005, BUG-007, BUG-008,
  BUG-010, BUG-011, BUG-012, BUG-014, BUG-015, BUG-016, BUG-018, BUG-020, BUG-021, BUG-023
