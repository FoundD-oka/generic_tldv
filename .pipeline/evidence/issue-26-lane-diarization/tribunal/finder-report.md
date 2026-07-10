findings:
- id: BUG-001
  file: services/meeting-api/meeting_api/final_transcription.py
  line: 427-432
  category: logic-error
  impact: medium
  title: Shared-mic lane forces speaker=None even on segments that never had a speaker_cluster, silently collapsing them into a shared blank identity
  evidence: |
    In `_apply_lane_identity`, the shared-mic branch is:
        if shared_mic:
            for seg in segments:
                cluster = seg.get("speaker_cluster")
                if cluster:
                    seg["speaker_cluster"] = f"lane:{lane.lane_key}:{cluster}"
                seg["speaker"] = None
    `seg["speaker"] = None` runs unconditionally for every segment in the lane,
    not only for the ones whose `cluster` was truthy. A segment that Soniox
    could not diarize (no `speaker` token tag) comes out of `_parse_segments`
    with `speaker_cluster=None` and `speaker="Unknown"` (see
    `_parse_segments`, `seg["speaker"] = cluster_names.get(cluster, "Unknown")
    if cluster else "Unknown"`). Once that segment lands in a lane that is
    otherwise shared-mic (K_stable>=2), this loop wipes its "Unknown" label to
    None while leaving `speaker_cluster` at None (the `if cluster:` guard
    skips renaming it, since `cluster` is falsy). Downstream,
    `_derive_speaker_mapping_status(None, None)` returns None (not
    "needs_review", since it requires a lane sub-cluster shape), and the
    dashboard's `getSpeakerIdentityKey` (`segment.speaker || segment.speaker_cluster
    || ""`) resolves this segment's identity to the empty string "" — the same
    identity key used meeting-wide for any other completely unattributed
    segment. Before this diff (the old multi-cluster branch never touched
    `seg["speaker"]`), such a segment kept the literal "Unknown" label and was
    at least visibly distinct/filterable; now it silently disappears into a
    blank, badge-less bucket that can visually merge with any other blank-key
    segment in the transcript, with no needs_review affordance to fix it since
    it has no lane sub-cluster id to rename.
  suggested_fix: |
    Only null out `seg["speaker"]` when the segment actually carries a lane
    sub-cluster id (i.e. inside the `if cluster:` branch), and leave
    clusterless segments with whatever `_parse_segments` already assigned
    ("Unknown"/DOM name) — or explicitly namespace them too (e.g.
    `lane:{lane.lane_key}:unclustered`) so they still get a stable,
    reviewable identity instead of collapsing to "".

- id: BUG-002
  file: services/dashboard/src/lib/speaker-edit.ts
  line: 43-79
  category: logic-error
  impact: medium
  title: buildSpeakerMerge still matches by seg.speaker only, so multi-select merge silently drops or mis-handles needs_review lane sub-speakers introduced by this commit
  evidence: |
    `transcript-viewer.tsx` now feeds `selectedSpeakers` from `speakerOrder`,
    which is built via `getSpeakerIdentityKey` (issue #26): for an unnamed
    shared-mic sub-cluster the identity key is the raw `speaker_cluster`
    (e.g. "lane:hash:spk0"), not `speaker` (which is empty/None for those
    segments). `buildSpeakerMerge` was not updated for this and still filters
    by name only:
        for (const seg of segments) {
          if (!seg.speaker || !selected.has(seg.speaker)) continue;
          ...
        }
    For a needs_review sub-speaker segment, `seg.speaker` is falsy, so
    `!seg.speaker` short-circuits to `continue` — the segment is skipped
    regardless of whether its `speaker_cluster` (its actual identity key) is
    in `selected`. Two concrete failure modes:
    1. User selects two needs_review badges ("要確認の話者A" and "…B") in the
       speaker filter and clicks merge: every segment is skipped, `clusters`
       and `clusterlessNames` both stay empty, the function returns `null`,
       and `applySpeakerUpdate(null)` in transcript-viewer.tsx
       (`if (!payload || !meeting?.id) return;`) no-ops with **no toast, no
       error** — the user believes they merged two speakers and nothing
       happened.
    2. User selects one needs_review sub-speaker plus one named speaker (a
       very natural "attach this stray voice to a known person" action):
       only the named speaker's cluster is picked up, producing
       `rename: [{ from_cluster: <named speaker's cluster>, to_name }]` —
       the needs_review sub-speaker is silently dropped from the operation
       even though the UI made it look selected/included.
    The existing test file `services/dashboard/tests/test_speaker_edit.test.ts`
    was not updated in this commit and still only exercises name-keyed
    `selectedSpeakers` (e.g. ["Unknown", "Unknown 2"]), confirming this
    integration path was never adapted for the new cluster-id identity keys.
    Single-segment rename (`buildSpeakerRename`, used by the per-segment
    "one-click naming" edit) DOES check `speaker_cluster` first and works
    correctly — only the multi-select merge path is broken.
  suggested_fix: |
    Change `buildSpeakerMerge` to match segments by identity key (mirroring
    `getSpeakerIdentityKey`: `seg.speaker || seg.speaker_cluster`) instead of
    `seg.speaker` alone, and add a regression test with a needs_review
    lane-sub-cluster segment mixed into the selection.

- id: BUG-003
  file: services/dashboard/src/lib/export.ts
  line: 86,111,130,142
  category: logic-error
  impact: low
  title: TXT/JSON/SRT/VTT exports read the raw (pre-label-resolution) segment.speaker, so two distinct needs_review sub-speakers export as identical blank names
  evidence: |
    `handleExport` in transcript-viewer.tsx calls `exportToTxt/Json/Srt/Vtt`
    with the raw `segments` prop, not `groupedSegments`/the
    `speakerDisplayLabels`-resolved synthetic segments used for on-screen
    rendering. `export.ts` then does `${segment.speaker}:` /
    `speaker: s.speaker` directly. For a shared-mic lane, `segment.speaker`
    is empty/undefined for every needs_review sub-cluster segment (server
    forces `speaker=None`), so exported transcripts render e.g.
    `[00:00] : こんにちは` and `[00:07] : 別の発言です` with no way to tell
    the two distinct speakers apart (both blank) — unlike the dashboard,
    which resolves them to "要確認の話者A"/"B" via `speaker-label.ts`. This
    does not leak the raw lane cluster id (the stated invariant "no raw lane
    id ever rendered" still holds), but it is a real fidelity regression for
    any shared-mic meeting exported before a human renames the sub-speakers.
  suggested_fix: |
    Resolve display labels (via `getSpeakerDisplayLabel`/
    `buildSpeakerDisplayLabels`) before handing segments to the export
    functions, exactly as the on-screen renderer does, so exports match what
    the user sees.

- id: BUG-004
  file: services/meeting-api/meeting_api/final_transcription.py
  line: 56-58
  category: other
  impact: low
  title: LANE_SHARED_MIC_MIN_CLUSTER_TOKENS uses int(os.getenv(...)) at import time — a plausible-looking float value crashes meeting-api on startup
  evidence: |
    LANE_SHARED_MIC_MIN_CLUSTER_TOKENS = int(
        os.getenv("LANE_SHARED_MIC_MIN_CLUSTER_TOKENS", "5")
    )
    Its sibling threshold, `LANE_SHARED_MIC_MIN_CLUSTER_DURATION_S`, is parsed
    with `float(...)` two lines above it, and both are documented together in
    deploy/env-example / docker-compose.yml with parallel `X.0`-looking
    defaults ("2.0" / "5"). An operator who (reasonably, given the sibling
    var) sets `LANE_SHARED_MIC_MIN_CLUSTER_TOKENS=5.0` in the environment
    causes `int("5.0")` to raise `ValueError` at module import time, which
    takes down the whole meeting-api process on startup, not just this
    feature — a disproportionate blast radius for a tunable threshold that
    the rest of the module (`_stable_lane_clusters`) already treats
    defensively (documented as "never raises... degrades toward unstable").
  suggested_fix: |
    Parse with `int(float(os.getenv(...)))` (accepts both "5" and "5.0") or
    wrap the module-level parse in a try/except that logs and falls back to
    the default, matching the defensive posture already documented for the
    rest of this feature.

- id: BUG-005
  file: services/meeting-api/meeting_api/collector/endpoints.py
  line: 365-378
  category: logic-error
  impact: low
  title: The Redis (live) segment merge path never calls _derive_speaker_mapping_status — it only trusts a field nothing currently writes, an asymmetry with the PG path that is easy to silently break
  evidence: |
    `_get_full_transcript_segments` derives `speaker_mapping_status` for
    Postgres-sourced segments via `_derive_speaker_mapping_status(pg_speaker_cluster,
    seg.speaker)` (line ~311), but for Redis-sourced (live) segments it does:
        speaker_mapping_status=d.get("speaker_mapping_status"),
    i.e. it trusts whatever key happens to be in the Redis JSON blob, with no
    derivation fallback. Today this is safe only because nothing in the
    realtime collector pipeline (`processors.py`, `db_writer.py`) ever writes
    a `speaker_cluster` in the `lane:{key}:{cluster}` shape into Redis (lane
    diarization is deferred-only), so `d.get("speaker_mapping_status")` is
    always None in practice. But this is an implicit, undocumented
    cross-module invariant rather than an enforced one: Redis segments (live)
    take precedence over Postgres on key collision per this function's own
    docstring, so if a future change ever writes a lane sub-cluster id into
    the live Redis segment hash (e.g. a live preview of lane diarization, or
    a bug that copies a `speaker_cluster` value verbatim), those segments
    would silently bypass needs_review derivation and show up as ordinary
    (non-flagged) segments even though they carry an unnamed lane sub-cluster
    id — the exact "raw lane id leak" scenario the rest of this feature goes
    out of its way to prevent, just reached through the other merge branch.
  suggested_fix: |
    Route the Redis branch through the same `_derive_speaker_mapping_status`
    call (falling back to the JSON field only if the derivation returns None
    and the field is explicitly set), so both merge branches share one source
    of truth instead of one being "derive" and the other being "trust the
    wire".

- id: BUG-006
  file: services/meeting-api/meeting_api/final_transcription.py
  line: 572,597-598
  category: logic-error
  impact: low
  title: shared_mic_lane_keys threading treats an empty-string lane_key the same as "not shared-mic", so a shared-mic lane with a falsy lane_key would silently vanish from shared_mic_lanes
  evidence: |
    `_transcribe` returns `(lane.lane_key if shared_mic else None)`, and the
    merge loop does `if shared_mic_lane_key: shared_mic_lane_keys.append(...)`.
    Both the ternary and the `if` treat any falsy `lane.lane_key` (not just
    `None`) as "no shared-mic lane to record". `lane_key` is derived as
    `mf_type[len("lane-"):]` in `_lane_master_sources` — normally a 10-char
    sha1-hex slug from the bot (`browser.ts: (await sha1Hex(track.id)).slice(0,10)`),
    so it is never empty in the current bot implementation, but nothing in
    `final_transcription.py` enforces or validates that. If `mf_type` were
    ever exactly `"lane-"` (a malformed/degenerate media_file type — e.g. a
    corrupted or hand-crafted `meeting.data.recordings` entry), a genuinely
    shared-mic lane would be silently dropped from
    `final_transcription.shared_mic_lanes` while its segments still get the
    `lane::{cluster}` sub-cluster ids and `speaker=None` treatment — an
    inconsistent state where the segments look shared-mic but the recorded
    state says they aren't.
  suggested_fix: |
    Use an explicit sentinel (e.g. return `(lane.lane_key, shared_mic)` as a
    tuple, or check `shared_mic_lane_key is not None`) instead of relying on
    Python truthiness for a string that is only guaranteed non-empty by an
    upstream convention this module cannot see.

Total score: 5 + 5 + 1 + 1 + 1 + 1 = 14
