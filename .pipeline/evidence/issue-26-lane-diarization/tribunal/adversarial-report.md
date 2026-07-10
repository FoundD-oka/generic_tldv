verdicts:
- id: BUG-001
  verdict: confirmed
  reasoning: |
    Verified against services/meeting-api/meeting_api/final_transcription.py:427-432.
    In the shared_mic branch of `_apply_lane_identity`:
        if shared_mic:
            for seg in segments:
                cluster = seg.get("speaker_cluster")
                if cluster:
                    seg["speaker_cluster"] = f"lane:{lane.lane_key}:{cluster}"
                seg["speaker"] = None
    `seg["speaker"] = None` sits OUTSIDE the `if cluster:` guard, so it runs for
    every segment in a shared-mic lane, including ones that never had a
    speaker_cluster. Traced `_parse_segments` (line 851-859): when
    `has_clusters` is True for the lane's tx_result (true whenever ANY segment
    carries a diarization tag), every segment gets
    `seg["speaker_cluster"] = str(cluster) if cluster not in (None, "") else None`
    and `seg["speaker"] = cluster_names.get(cluster, "Unknown") if cluster else
    "Unknown"` — so a segment Soniox didn't tag legitimately comes out with
    speaker_cluster=None, speaker="Unknown". If that segment sits in an
    otherwise-shared-mic lane, the unconditional `seg["speaker"] = None`
    overwrites "Unknown" while `if cluster:` (falsy) skips namespacing it, so it
    keeps speaker_cluster=None. Downstream, `_derive_speaker_mapping_status`
    (collector/endpoints.py:249) returns None for a None speaker_cluster (regex
    never matches), so the segment is NOT flagged needs_review, and
    `getSpeakerIdentityKey` (speaker-label.ts:27, `segment.speaker ||
    segment.speaker_cluster || ""`) resolves it to "" — the same bucket as any
    other unattributed segment, with no rename affordance. I confirmed prior
    behavior (git show 58200e2, old `else` branch) never touched `seg["speaker"]`
    for multi-cluster lanes at all, so this "Unknown"→silently-blank collapse is
    a genuine regression introduced by this commit, and confirmed no test in
    test_final_transcription_lanes.py exercises a mixed clustered/clusterless
    segment set inside a K_stable>=2 lane — the gap is real and untested.
  confidence: high

- id: BUG-002
  verdict: confirmed
  reasoning: |
    Traced the runtime path end to end. transcript-viewer.tsx:240-249 builds
    `speakerOrder` via `getSpeakerIdentityKey(segment)` (speaker-label.ts:27,
    `segment.speaker || segment.speaker_cluster || ""`); for a needs_review
    sub-cluster, `speaker` is empty so the identity key IS the raw
    speaker_cluster string (e.g. "lane:abc123:spk0"). The speaker-filter
    dropdown (transcript-viewer.tsx:770-785) populates `selectedSpeakers` from
    exactly these `speakerOrder` entries via `toggleSpeaker`. `handleMergeSelectedSpeakers`
    (line 416-420) calls `buildSpeakerMerge(selectedSpeakers, segments,
    mergeTargetName)` with the RAW `segments` prop (not display-relabeled).
    speaker-edit.ts:54-55:
        for (const seg of segments) {
          if (!seg.speaker || !selected.has(seg.speaker)) continue;
    For a needs_review segment, `seg.speaker` is falsy, so `!seg.speaker` is
    true and the segment is skipped regardless of whether its
    `speaker_cluster` is in `selected` — confirmed exactly as claimed. Verified
    `buildSpeakerRename` (used for single-segment edit, lines 9-22) DOES check
    `speaker_cluster` first and is unaffected. Verified
    services/dashboard/tests/test_speaker_edit.test.ts only exercises
    name-keyed selections (["Unknown","Unknown 2"], "レガシー") — no
    needs_review/lane-sub-cluster case exists, confirming this path shipped
    untested. This is a real, reachable UI bug: selecting 2 needs_review
    badges and merging silently no-ops (buildSpeakerMerge returns null →
    applySpeakerUpdate short-circuits with no toast).
  confidence: high

- id: BUG-003
  verdict: disproved
  reasoning: |
    export.ts is not in the commit's changed-file list at all (git show
    58200e2 --stat confirms only 19 files changed, export.ts absent), and the
    commit message explicitly states "Invariants preserved: ... exports
    unchanged." This is not an oversight — it is a documented, reviewed
    decision. .pipeline/plans/issue-26-lane-diarization/plan.md:127-134 has an
    explicit "Out of Scope" section: "exports/public shareへのneeds_review反映
    （Phase 3は内部UI限定、非目標として明記。NH-3）" and line 203 restates
    "NH-3（export/export/public shareへのneeds_review反映はPhase 3非目標として確定、
    Out of Scope...)". The Codex plan critique
    (.pipeline/evidence/issue-26-lane-diarization/codex-plan-critique.md:69)
    independently raised the exact same question ("NH-3: export/shareに
    needs_reviewを含めるかはproduct判断が必要") and it was resolved as
    deliberately out of scope before implementation. The finder's technical
    description of the code (raw segment.speaker used in exports) is accurate,
    but characterizing an explicitly reviewed, documented non-goal as an
    unintended "bug"/"regression" the commit introduced is over-reporting —
    this is a scoped product decision, not a defect of this diff.
  confidence: high

- id: BUG-004
  verdict: confirmed
  reasoning: |
    Verified final_transcription.py:56-58:
        LANE_SHARED_MIC_MIN_CLUSTER_TOKENS = int(
            os.getenv("LANE_SHARED_MIC_MIN_CLUSTER_TOKENS", "5")
        )
    while its sibling two lines above (line 53-55) uses `float(...)`. Ran
    `python3 -c "print(int('5.0'))"` — confirmed it raises
    `ValueError: invalid literal for int() with base 10: '5.0'`. Both are
    module-level statements executed at import time, so a malformed env value
    takes down the whole meeting-api process before any request is served —
    disproportionate for a tunable threshold, and inconsistent with the
    defensive posture `_stable_lane_clusters` documents for itself ("Never
    raises... degrades toward unstable", final_transcription.py:341-346). The
    finder's core technical claim is fully correct and the risk (operator
    plausibly typing "5.0" given the neighboring float-typed var uses decimal
    defaults) is real, even though deploy/env-example and docker-compose.yml
    currently ship correct non-decimal values for this var.
  confidence: high

- id: BUG-005
  verdict: confirmed
  reasoning: |
    Verified collector/endpoints.py: the PG branch (lines 300-314) computes
    `speaker_mapping_status=_derive_speaker_mapping_status(pg_speaker_cluster,
    seg.speaker)`, while the Redis branch (line 374) does
    `speaker_mapping_status=d.get("speaker_mapping_status")` — trusting
    whatever key is in the JSON blob, with zero derivation fallback. Checked
    the function's own docstring (line 261-265): "Redis segments (live) take
    precedence over Postgres (persisted)" — confirms Redis wins on key
    collision, exactly the collision risk the finder describes. Checked
    .pipeline/plans/issue-26-lane-diarization/plan.md for any mention of this
    asymmetry or an explicit decision to trust-not-derive on the Redis path —
    found none; the plan and the code's own inline comments discuss ARC-3 (no
    DB migration) and the PG-side derivation rationale but never address why
    the Redis branch differs. The claim of "implicit, undocumented cross-module
    invariant" is accurate. Currently safe only because lane diarization is
    deferred-only (RECORD_PARTICIPANT_LANES default off, and nothing in the
    realtime collector writes lane-shaped speaker_cluster into Redis) — a real,
    if currently dormant, latent-bug risk exactly as described, not a fabricated
    concern.
  confidence: high

- id: BUG-006
  verdict: confirmed
  reasoning: |
    Verified final_transcription.py:572 (`return segments, detected, (lane.lane_key
    if shared_mic else None)`) and line 597 (`if shared_mic_lane_key:
    shared_mic_lane_keys.append(...)`) both rely on Python truthiness, so an
    empty-string lane_key is indistinguishable from "not shared-mic". Delegated
    a sub-investigation to trace whether `mf_type == "lane-"` (empty lane_key)
    is reachable: the legitimate bot path (vexa-bot/core/src/utils/browser.ts,
    sha1Hex(...).slice(0,10)) always yields a non-empty 10-char key, so it
    cannot happen through normal bot operation — but
    services/meeting-api/meeting_api/recordings.py's internal upload endpoint
    accepts `media_type` as an unvalidated free-form string, and
    `is_lane_media_type()` (media_types.py:12-14, `.startswith("lane-")`) would
    accept the literal string "lane-" itself. `_lane_master_sources`
    (final_transcription.py:307, `lane_key=mf_type[len("lane-"):]`) has no
    guard rejecting an empty resulting key before constructing
    LaneTranscriptionSource. So this is not purely theoretical scaremongering —
    there genuinely is no schema-level validation preventing a malformed
    internal caller from producing lane_key="", which would then silently drop
    a shared-mic lane from `shared_mic_lanes` while its segments still carry
    `lane::{cluster}` sub-ids and speaker=None. The finder correctly scoped this
    as low impact/likelihood while still being an accurate description of a
    real gap in defensive validation.
  confidence: medium

Score: 5 (BUG-001) + 5 (BUG-002) + 1 (BUG-004) + 1 (BUG-005) + 1 (BUG-006) = 13, plus BUG-003 correctly disproved (+1) = 14 earned.
No wrongful disprovals — all confirmed findings verified against real code paths and cross-checked against test coverage; the one disproved finding (BUG-003) is backed by explicit, reviewed plan documentation (NH-3 Out of Scope) rather than a judgment call.
