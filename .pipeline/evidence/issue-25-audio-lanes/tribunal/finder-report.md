findings:
- id: BUG-001
  file: services/vexa-bot/core/build-browser-utils.js
  line: 33
  category: type-error
  impact: critical
  title: BrowserLaneRecorderManager is not exported on window.VexaBrowserUtils — lane feature crashes at startup when enabled
  evidence: The bundle wrapper hardcodes the exposed keys — `window.VexaBrowserUtils = { BrowserAudioService: utils.BrowserAudioService, BrowserMediaRecorderPipeline: ..., BrowserWhisperLiveService: ..., generateBrowserUUID: ... }` — and was NOT updated for issue #25. The generated `dist/browser-utils.global.js` (lines 570-575) confirms the omission even though the inner CommonJS module does export the class (`exports.BrowserLaneRecorderManager` at dist line 564). In `services/vexa-bot/core/src/platforms/googlemeet/recording.ts:133`, `new u.BrowserLaneRecorderManager({...})` therefore throws `TypeError: u.BrowserLaneRecorderManager is not a constructor` inside `page.evaluate` whenever `recordLanes` is true. The throw happens AFTER the combined browser-side MediaRecorder was started (line 123) but BEFORE the #285 alone-cross-validation audio-processor initialization in the same evaluate, and the rejected evaluate propagates out of `startBrowserCapture` → `MediaRecorderCapture.start` (audio-pipeline.ts:759 has no try/catch) → `await pipeline.start()` (googlemeet/recording.ts:203) — so turning RECORD_PARTICIPANT_LANES on not only makes lanes dead-on-arrival, it aborts the Node-side recording startup path and skips the downstream hook init. "tsc + build clean" passed only because `u` is typed `any`; no test exercises the injected bundle.
  suggested_fix: Add `BrowserLaneRecorderManager: utils.BrowserLaneRecorderManager` to the window.VexaBrowserUtils object in build-browser-utils.js, rebuild the bundle, and add a smoke assertion (e.g. in startBrowserCapture, fail fast with a clear log if `typeof u.BrowserLaneRecorderManager !== "function"` while recordLanes is true instead of letting the evaluate reject mid-way). Also wrap the lane-manager construction in try/catch so a lane failure can never abort the mixed-master path (the commit message explicitly promises this).

- id: BUG-002
  file: services/meeting-api/meeting_api/final_transcription.py
  line: 297
  category: logic-error
  impact: critical
  title: Lane segment timestamps are lane-relative — no per-lane start offset exists anywhere, so merged transcripts are chronologically wrong
  evidence: Each lane's MediaRecorder starts when its track is first scanned (initial scan at recording start, or a 15s `rescan` for late joiners — browser.ts:612). A participant who joins at minute 30 gets a lane whose audio t=0 is minute 30 of the meeting. Nothing captures or transmits a lane start offset — `LaneAudioChunk` (audio-pipeline.ts), the upload metadata (recording.ts), the media_files `lane` object (recordings.py:549) and `LaneTranscriptionSource` all lack any start-time field. `_transcribe_lanes` then merges segments with `merged.sort(key=lambda s: (float(s.get("start", 0)), ...))` and `run_deferred_transcription` stores raw `start`/`end` into Transcription rows (line ~965), so a late joiner's first utterance is timestamped at 0.0 and interleaved at the top of the transcript, and dashboard audio-seek maps to the wrong position in the mixed master. The same misalignment corrupts DOM speaker attribution: `_parse_segments(tx, speaker_events=speaker_events, ...)` is called per-lane with the GLOBAL speaker_events timeline (final_transcription.py:337-343), so `name_clusters_by_dom_vote`/`map_speakers_to_segments` compare lane-relative segment times against master-relative DOM event times.
  suggested_fix: Record a per-lane start offset (e.g. bot captures `Date.now()` delta between the mixed recording's `started` event and each lane's recorder `onstart`, sends it as `lane_start_offset_seconds` in the first chunk's metadata, persisted on the media_files lane object). In `_transcribe_lanes`, add the offset to every segment start/end before merging, and to the window passed to the DOM-vote helpers. Until offsets exist, lane-first STT should be restricted to lanes created at recording start (or disabled).

- id: BUG-003
  file: services/meeting-api/meeting_api/recordings.py
  line: 585
  category: logic-error
  impact: medium
  title: A lane chunk with is_final=true marks the ENTIRE recording COMPLETED and fires the recording.completed webhook
  evidence: The status flip is media_type-agnostic — `if is_final: rec_payload["status"] = RecordingStatus.COMPLETED.value; ... status_transitioned_to_completed = not was_completed` (recordings.py:585-590), followed by `send_event_webhook(meeting.id, "recording.completed", ...)`. Lanes end whenever a participant leaves mid-meeting (track 'ended' → pipeline.stop() → isFinal chunk), so a mid-meeting participant departure would complete the recording and emit the external webhook while the mixed master is still uploading; the "terminal state is sticky" rule (line 592-595) then prevents subsequent audio chunks from downgrading it, so IN_PROGRESS semantics are lost for the rest of the meeting. This is currently masked only by BUG-004 (the empty final lane chunk is dropped Node-side and never reaches the server), i.e. it is a latent landmine that fires as soon as anyone fixes BUG-004 or any caller sends a data-bearing lane chunk with is_final=true (metadata `is_final` defaults to true for callers that omit it).
  suggested_fix: In internal_upload_recording, gate the recording-level COMPLETED transition and the recording.completed webhook on `not is_lane_media_type(media_type)`; lane is_final should only mark that lane's media_files entry final.

- id: BUG-004
  file: services/vexa-bot/core/src/services/audio-pipeline.ts
  line: 331
  category: logic-error
  impact: medium
  title: The lane final (is_final=true) chunk is always dropped — lane is_final never reaches the server, contradicting the flush contract
  evidence: BrowserMediaRecorderPipeline.onstop always emits the final chunk with `base64: ""` (browser.ts:479). `__vexaSaveLaneChunk` decodes that to a zero-length Buffer, and `_handleLaneChunk` starts with `if (!chunk.data || chunk.data.length === 0) { log("empty lane chunk dropped"); return; }` (audio-pipeline.ts:329-332). So no lane upload ever carries is_final=true: the lane media_files entry stays `is_final: false` until recording_finalizer or post_meeting_reconciler stamp it, the Pack U.7 `prior_is_final` guard never protects a lane pre-finalization, and the stopBrowserCapture comment "their final chunks must flush before ... teardown" is untrue for the is_final signal. The server-side chunk_seq/is_final contract that the code comments lean on ("the server's chunk_seq contract makes gaps detectable") is structurally broken for lanes.
  suggested_fix: In `_handleLaneChunk`, forward empty chunks when `chunk.isFinal` is true (upload a zero-byte body or a metadata-only final marker), or make the browser side set isFinal=true on the last data-bearing chunk instead of a separate empty one. Combine with the BUG-003 fix so the arrival of lane is_final cannot complete the recording.

- id: BUG-005
  file: services/vexa-bot/core/src/utils/browser.ts
  line: 479
  category: race-condition
  impact: medium
  title: Last lane data chunk can be lost at shutdown — onstop resolves stop() while the final ondataavailable handler is still encoding
  evidence: `recorder.ondataavailable` is async and awaits `event.data.arrayBuffer()` plus base64 encoding before invoking `chunkCallback` (browser.ts:405-470); `recorder.onstop` independently awaits the empty final callback and resolves `finalChunkPromise` (browser.ts:474-499). Since the last dataavailable's exposed-function call happens after several awaits, `pipeline.stop()` (and hence `BrowserLaneRecorderManager.stopAll()` and googlemeet's `stopBrowserCapture`) can resolve BEFORE the last data chunk has been delivered to Node. `UnifiedRecordingPipeline.stop()` then snapshots `Array.from(this.laneQueues.values())` (audio-pipeline.ts:308-310) before that chunk is enqueued, so the drain barrier misses it; if the bot process exits promptly, the tail ~30s of the lane's audio is silently lost, and the lane master is finalized without it. (The mixed path has the same shape pre-existing, but the lane path is new code and its comments claim the flush is guaranteed.)
  suggested_fix: Have ondataavailable register an in-flight promise that onstop awaits before emitting the final chunk (e.g. track pending chunk-callback promises and `await Promise.allSettled(pending)` in onstop), or delay resolveFinalChunk until the pending buffer is empty. On the Node side, re-await laneQueues in a loop until the map's promises are stable.

- id: BUG-006
  file: services/vexa-bot/core/src/utils/browser.ts
  line: 677
  category: race-condition
  impact: medium
  title: Overlapping scans can start two MediaRecorders for the same track/laneKey — chunk_seq collision corrupts the lane master
  evidence: `start()` schedules `this.scan(els)` from `setInterval` without awaiting the previous run (browser.ts:612-621), and `scan` awaits `startLane` per track. Inside `startLane`, `const laneKey = (await sha1Hex(track.id)).slice(0, 10)` (line 677) yields to the event loop BEFORE `this.lanes.set(track.id, ...)` — so two concurrent scans can both pass the `this.lanes.has(track.id)` check (line 651) and both construct a `BrowserMediaRecorderPipeline` for the same track. Both pipelines share the same laneKey (sha1 of the same track.id) but have independent `chunkSeq` counters starting at 0, so the server receives duplicate chunk_seq values under `.../lane-{key}/000000.webm` and each upload silently overwrites the other's object — the finalized lane master is interleaved garbage. The losing pipeline also leaks (map entry overwritten, recorder never stopped by stopAll).
  suggested_fix: Reserve the key synchronously before any await (e.g. `this.lanes.set(track.id, PLACEHOLDER)` or a `startingTracks` Set checked in scan), and/or serialize scans with a `scanning` flag so interval ticks skip while a scan is in flight.

- id: BUG-007
  file: services/vexa-bot/core/src/utils/browser.ts
  line: 645
  category: resource-leak
  impact: medium
  title: captureStream() is called on every 15s rescan and returns new tracks each time — lane explosion until maxLanes is exhausted
  evidence: `scan` computes `stream = (el.srcObject as MediaStream) || ((el as any).captureStream && (el as any).captureStream()) || null` for EVERY element on EVERY rescan. For any media element without srcObject (src-based playback, promo/self-view elements matched by the blanket `document.querySelectorAll("audio, video")` in the rescan), each `captureStream()` call creates a NEW MediaStream whose tracks have NEW ids, so `this.lanes.has(track.id)` never matches and a fresh lane (a whole MediaRecorder + upload stream) is started every 15 seconds for the same element until `maxLanes` is reached. Consequences: runaway CPU/bandwidth/storage cost, real participants permanently locked out of lane slots (`max lanes reached` warning at line 655), duplicate audio content transcribed as extra lanes (duplicated transcript text after the lane merge), and the abandoned MediaStreams are never closed.
  suggested_fix: Never call captureStream() during rescan for elements already known; key lanes by element identity (WeakSet of elements already captured) in addition to track.id, or restrict lane capture to elements with a MediaStream srcObject (the only case that represents a remote participant in Meet).

- id: BUG-008
  file: services/vexa-bot/core/src/utils/browser.ts
  line: 723
  category: race-condition
  impact: medium
  title: stopAll races an in-flight scan — lanes started after the map is cleared are never stopped and keep uploading during teardown
  evidence: `stopAll()` sets `this.stopped = true`, clears the interval, snapshots `Array.from(this.lanes.values())` and clears the map (browser.ts:723-731). But `this.stopped` is only checked at the top of the interval callback (line 613), not inside `scan`/`startLane`. A scan already in flight when stopAll runs will continue, pass its checks, `lanes.set(...)` and `await pipeline.start()` AFTER the snapshot — that recorder is never stopped by anyone. It keeps producing chunks through page teardown; late lane chunks arrive after recording_finalizer already built the lane master (the Pack U.7 guard preserves the master path, but the orphan chunks still land in storage and the lane master is missing that audio, or worse the chunks re-open `is_final` bookkeeping on the entry).
  suggested_fix: Check `this.stopped` inside `scan`'s loop and at the top of `startLane` (bail before constructing the pipeline), and make `stopAll` await any in-flight scan (store the scan promise and await it before snapshotting).

- id: BUG-009
  file: services/meeting-api/meeting_api/recordings.py
  line: 439
  category: injection
  impact: medium
  title: Client-supplied media_type (lane-*) is used unsanitized in the storage path and JSONB — no server-side validation of laneKey shape
  evidence: `media_type = meta.get("media_type", media_type)` (line 372) accepts any string from the upload body and interpolates it raw into `storage_path = f"recordings/{user_id}/{storage_id}/{session_uid}/{media_type}/{chunk_seq:06d}.{media_format}"` (line 439). Before this commit, non-audio/video types were inert (finalizer/sweeps ignored them); issue #25 makes every `lane-*` string load-bearing: it becomes a storage directory, a media_files JSONB key, a Pack U.7 endswith pattern (`f"/{media_type}/master.webm"`), a sweep-parse token (`parts[4]` in `_parse_recording_chunk_key` — a media_type containing "/" shifts every positional field), and `mf_type[len("lane-"):]` flows into `speaker_cluster = "lane:{laneKey}"` and Transcription rows. A compromised or buggy internal caller can send `media_type="lane-../../<other>/audio"` to write outside the recording's prefix or poison path-matching logic. The endpoint is JWT-scoped to one meeting, which bounds but does not eliminate the damage (the JWT does not constrain media_type).
  suggested_fix: Validate media_type server-side with a whitelist regex, e.g. `^(audio|video|screenshots|lane-[0-9a-f]{6,16})$`, and 422 anything else. The bot generates laneKey as 10 hex chars, so the tight pattern is compatible.

- id: BUG-010
  file: services/meeting-api/meeting_api/final_transcription.py
  line: 325
  category: memory-leak
  impact: medium
  title: _transcribe_lanes holds every lane's decoded WAV in memory simultaneously — multi-GB peak RSS before the budget check
  evidence: `prepared = await asyncio.gather(*(_prepare(lane) for lane in lane_sources), ...)` retains `(lane, audio, fmt, duration)` for ALL lanes at once; the semaphore (LANE_STT_CONCURRENCY=2) bounds concurrency of download/ffmpeg but not retention. `_convert_audio_to_wav` inflates compressed webm/opus to PCM WAV (~10-30x). With the default budget of `MAX_LANE_TOTAL_DURATION_SECONDS=14400` (4h of total lane audio is ALLOWED), 16kHz/16-bit mono WAV is ~115 MB/h → up to ~1.6 GB of lane WAV plus the base64/bytes intermediates held concurrently in the meeting-api process — and the budget check itself only runs AFTER all lanes are downloaded and converted (line ~340), so an over-budget meeting pays full download+ffmpeg cost and peak memory before falling back. OOM of meeting-api takes down all API traffic, not just this job.
  suggested_fix: Process lanes strictly sequentially or free each lane's audio after its STT call (pipeline prepare→transcribe per lane under the semaphore instead of two barriers); check the duration budget incrementally (abort as soon as the running total exceeds the cap, before converting remaining lanes); consider probing duration from storage metadata before download.

- id: BUG-011
  file: services/meeting-api/meeting_api/final_transcription.py
  line: 780
  category: logic-error
  impact: medium
  title: Lane presence bypasses the no-speaker-events replace guard, but the fallback path then destroys meaningful speaker labels anyway
  evidence: The skip guard now includes `and not _lane_master_sources(meeting)` — rationale: "Lane masters carry their own identity ... the protection is unnecessary when lanes exist." But the lane path is all-or-nothing: if `_transcribe_lanes` raises `LaneTranscriptionFallback` (any lane download/convert/STT failure, or duration budget exceeded), execution falls through to the UNCHANGED mixed-master path with `speaker_events == []` — precisely the scenario the guard exists to block. In replace mode this deletes all existing Transcription rows (`delete(Transcription)...`) that had meaningful speakers and rewrites them with "Unknown" speakers from `_parse_segments` with no DOM events. The guard's premise (lanes carry identity) no longer holds once the fallback fires, but the guard was already skipped.
  suggested_fix: Re-check the guard condition at fallback time: when `LaneTranscriptionFallback` is raised AND `not speaker_events` AND existing segments have meaningful speakers (and not force), abort with `skipped_no_speaker_events` instead of running the mixed path.

- id: BUG-012
  file: services/meeting-api/meeting_api/final_transcription.py
  line: 236
  category: logic-error
  impact: medium
  title: All-or-nothing covers STT failures only — not-yet-finalized lanes are silently excluded, producing a transcript missing participants
  evidence: `_lane_master_sources` selects only entries with `finalized_by == "recording_finalizer.master"` and a `/master.*` path. Lanes that exist but are not yet finalized (finalizer raised mid-run and a later sweep finalized only one recording/session; late lane chunks; reconciler-stamped `post_meeting_reconciler` entries awaiting the sweep) are simply absent from the list, so `_transcribe_lanes` happily succeeds on the subset and `lane_used=True` — the transcript then omits every segment of the missing participants with no fallback and no error (violating the stated all-or-nothing invariant "one failed lane means the whole lane path is abandoned"). Related: lanes are collected across ALL recordings/sessions of the meeting while `session_uid=source.session_uid` (line 972) stamps the MIXED source's session on every lane segment, and lane keys from different sessions are merged onto a single unaligned timeline (compounds BUG-002).
  suggested_fix: In run_deferred_transcription, compare the set of lane media_files present in JSONB (any `lane-*` entry) against the finalized set; if any lane exists but is unfinalized, treat it as a LaneTranscriptionFallback (or defer/retry) instead of transcribing the subset. Restrict lane sources to the same recording/session as `source`, and stamp each segment with its own lane's session_uid.

- id: BUG-013
  file: services/meeting-api/meeting_api/sweeps.py
  line: 365
  category: logic-error
  impact: medium
  title: _recording_has_unfinalized_lane keeps meetings in the sweep forever when a lane can never finalize — unbounded periodic re-finalization churn
  evidence: The unfinalized sweep now includes any recording where a lane media_files entry lacks `finalized_by == "recording_finalizer.master"` (line 539-545). `_finalize_one_media_file_sync` returns None when `list_objects_bounded` finds zero chunks ("No-fallback contract: do NOT fabricate an empty master") and the caller `continue`s without setting finalized_by. So a lane entry whose chunks are gone (retention lifecycle deleted chunks but JSONB remains — `delete_after` is set per entry; or a storage-side wipe) makes `_recording_has_unfinalized_lane` return True on EVERY sweep pass forever: each pass runs `finalize_recording_master`, which lists storage per media file, logs a warning, changes nothing, and the meeting stays in scope with no failure escape hatch or attempt cap. Multiply by many meetings with lanes and the sweep budget (UNFINALIZED_RECORDINGS_LIMIT slots) is permanently consumed by unfixable entries.
  suggested_fix: Give lane finalization a terminal failure state — e.g. after N sweep attempts (or when chunk listing is empty and the meeting is older than the retention window), stamp `finalized_by = "recording_finalizer.no_chunks"` (or `lane_finalize_failed: true`) on the entry so `_recording_has_unfinalized_lane` excludes it.

- id: BUG-014
  file: services/meeting-api/meeting_api/sweeps.py
  line: 597
  category: logic-error
  impact: low
  title: Sweep/inline JSONB recovery reconstructs lane media_files without the lane object — lane_label/lane_id permanently lost
  evidence: Both `recover_recordings_jsonb_from_storage` (line ~448) and `_sweep_unfinalized_recordings` (line ~593) rebuild media_files entries for lane types with only `type/format/storage_path/...` fields — no `"lane": {...}` object, because the label/id only ever existed in upload metadata. After recovery, `_lane_master_sources` yields `lane_label=None, lane_id_source=None`, so the solo-lane auto-confirm (`speaker = lane_label`) silently degrades: the cluster gets `lane:{key}` but the speaker name comes from the misaligned DOM vote or stays "Unknown". The feature's headline behavior disappears exactly in the recovery scenarios the sweep exists for, with no log or marker.
  suggested_fix: Persist lane identity somewhere recoverable (e.g. write a small `lane.json` sidecar object under the lane's storage prefix on first chunk, and have recovery read it), or at minimum log that lane identity was lost during recovery.

- id: BUG-015
  file: services/vexa-bot/core/src/platforms/googlemeet/recording.ts
  line: 56
  category: logic-error
  impact: low
  title: MAX_RECORDING_LANES=0 silently becomes 8 — the cap cannot express "no lanes" and 0/NaN fall back to maximum
  evidence: `Math.max(1, parseInt(process.env.MAX_RECORDING_LANES || "8", 10) || 8)` — `parseInt("0")` is 0, which is falsy, so `|| 8` turns an explicit 0 into 8 (the largest default), and any garbage value also becomes 8. An operator trying to minimize lane cost with 0 or 1 lane gets 8 or 1 respectively — the 0 case does the exact opposite of the intent.
  suggested_fix: Parse with an explicit NaN check (`const n = parseInt(...); Number.isFinite(n) ? Math.max(0, n) : 8`) and treat 0 as "lanes disabled".

- id: BUG-016
  file: deploy/compose/docker-compose.yml
  line: 308
  category: other
  impact: medium
  title: Three of the four documented lane env knobs are dead — LANE_STT_CONCURRENCY, MAX_LANE_TOTAL_DURATION_SECONDS and MAX_RECORDING_LANES are never delivered to the processes that read them
  evidence: env-example documents four knobs (RECORD_PARTICIPANT_LANES, MAX_RECORDING_LANES, LANE_STT_CONCURRENCY, MAX_LANE_TOTAL_DURATION_SECONDS), but the compose change adds only `RECORD_PARTICIPANT_LANES` to the meeting-api service environment. `LANE_STT_CONCURRENCY` and `MAX_LANE_TOTAL_DURATION_SECONDS` are read by meeting-api (final_transcription.py:44-47) yet absent from its compose `environment:` list, so operator values in `.env` are silently ignored and the hardcoded defaults always apply — these are the STT COST CAPS the plan mandates. `MAX_RECORDING_LANES` is read by the bot's Node process (`process.env.MAX_RECORDING_LANES`, googlemeet/recording.ts:56) inside dynamically launched bot containers, which receive `bot_config` (meetings.py:1108-1113) — recordParticipantLanes is in bot_config but MAX_RECORDING_LANES has no bot_config counterpart and is not wired into bot-container env anywhere, so the lane cap is always 8. Same for the bot-side `process.env.RECORD_PARTICIPANT_LANES` fallback (recording.ts:53) — never set in the bot container, dead code.
  suggested_fix: Add LANE_STT_CONCURRENCY and MAX_LANE_TOTAL_DURATION_SECONDS to the meeting-api service in docker-compose.yml; carry maxRecordingLanes through bot_config (meetings.py) instead of the bot-side env read, or inject the env var into bot containers at launch.

- id: BUG-017
  file: services/meeting-api/meeting_api/final_transcription.py
  line: 269
  category: logic-error
  impact: low
  title: _apply_lane_identity leaves unclustered segments un-namespaced in multi-cluster lanes, and keeps misattributed DOM-vote names on unlabeled solo lanes
  evidence: In the multi-cluster branch, `if cluster:` skips segments whose speaker_cluster is None — those keep cluster None while their siblings become `lane:{key}:{cluster}`; saved cluster corrections and Phase-3 sub-speaker UX can never target them, and across lanes the None-cluster segments are indistinguishable. In the solo branch, when `lane.lane_label` is None the code rewrites `speaker_cluster` to `lane:{laneKey}` but leaves `seg["speaker"]` at whatever `_parse_segments` voted from the GLOBAL (misaligned, see BUG-002) DOM timeline — i.e. a confidently wrong name attached to an auto-confirmed lane cluster, which then poisons `_saved_cluster_corrections`-style workflows keyed on that cluster.
  suggested_fix: In the multi-cluster branch, namespace None clusters too (e.g. `lane:{key}:unclustered`). In the solo branch with no lane_label, either clear speaker to None/"Unknown" or keep the vote but mark it low-confidence, rather than pairing an auto-confirmed cluster with an unverified name.

- id: BUG-018
  file: services/meeting-api/meeting_api/final_transcription.py
  line: 368
  category: logic-error
  impact: low
  title: detected_language is last-lane-wins across lanes
  evidence: The merge loop does `if detected and detected != "unknown": detected_language = detected` for each lane in order — the final value is whatever the last lane's STT detected, not a majority or the dominant-duration lane. One participant speaking another language flips the stored `language` on every Transcription row for the entire meeting (all rows share the single `detected_language`).
  suggested_fix: Pick the detected language by weighted vote (sum of segment durations per language across lanes) or keep per-lane language on each segment's rows.

- id: BUG-019
  file: services/vexa-bot/core/src/services/audio-pipeline.ts
  line: 366
  category: memory-leak
  impact: low
  title: laneQueues and skippedOverCap grow without bound under track churn
  evidence: `this.laneQueues.set(chunk.laneKey, next)` never deletes entries; Google Meet renegotiates tracks on layout/presentation changes, so long meetings can accumulate hundreds of laneKeys, each pinning a settled promise chain in the Map for the process lifetime. Similarly `skippedOverCap` (browser.ts:596/657) adds every over-cap track.id and never removes ended ones. Bounded per meeting in practice but unbounded in principle; combined with BUG-007 (new track ids every 15s) the growth becomes linear in meeting duration.
  suggested_fix: Delete the laneQueues entry when its promise settles and matches the current map value (`if (this.laneQueues.get(k) === next) this.laneQueues.delete(k)` in a finally); prune skippedOverCap entries whose tracks have ended.

- id: BUG-020
  file: services/vexa-bot/core/src/utils/browser.ts
  line: 625
  category: logic-error
  impact: medium
  title: laneLabelForElement's DOM assumption does not hold for Google Meet audio elements — lane labels will be null in the normal case, hollowing out the solo-lane auto-confirm feature
  evidence: `el.closest?.("[data-participant-id]")` assumes the audio element is a descendant of a participant tile. In Google Meet, remote audio is played through a small pool of detached `<audio>` elements attached near document level (they are not inside the video tiles — the existing combined pipeline finds them by scanning all media elements for srcObject, not via tiles). So `tile` is null, the label is null with source "stream" for essentially every lane, and the deferred stage's headline behavior — solo lane auto-confirms `speaker = lane_label` — never fires; every lane instead keeps the misaligned DOM-vote name (see BUG-017/BUG-002). Additionally the label is captured once at lane start and never refreshed, so even where a tile match exists, Meet's element/stream reuse (srcObject reassigned to a different participant) permanently attaches the FIRST participant's name to audio that later belongs to someone else — wrong speaker names asserted with auto-confirm confidence.
  suggested_fix: Correlate track → participant via Meet's data structures the existing speaker-detection code already uses (participant-id ↔ stream mapping), not via DOM ancestry of the audio element; re-resolve the label on each chunk (or on srcObject change) and end the lane when the element's srcObject is swapped.

- id: BUG-021
  file: services/vexa-bot/core/src/utils/browser.ts
  line: 667
  category: logic-error
  impact: low
  title: A throw from pipeline.start()/recorder.start() leaves a zombie lane entry and aborts the rest of the scan pass
  evidence: `startLane` registers the lane (`this.lanes.set(track.id, ...)`) BEFORE `await pipeline.start()`. `BrowserMediaRecorderPipeline.start` catches MediaRecorder construction errors but `recorder.start(this.opts.timesliceMs)` (browser.ts:499) is outside any try/catch — an InvalidStateError there propagates: (a) the remaining elements of that scan pass are skipped (`await this.startLane(...)` at line 667 is inside the loop; the rescan `.catch` only logs), and (b) the failed lane stays in `this.lanes` forever, so the track is never retried and permanently consumes a maxLanes slot while recording nothing.
  suggested_fix: try/catch around `await this.startLane(...)` in scan (log and continue), and delete the lanes entry on start failure so the track can be retried on the next rescan.

- id: BUG-022
  file: services/meeting-api/meeting_api/recordings.py
  line: 276
  category: other
  impact: low
  title: "Internal-only" lane hiding is response-boundary only — lane files remain fetchable via the per-id media endpoints and lane PII (participant display names) persists unfiltered in JSONB
  evidence: `_public_recording_view` strips lane entries from `/recordings` list/get responses, but `/recordings/{id}/media/{media_file_id}` and `/download` look up media_files by id on the RAW rec (`_find_meeting_data_recording` returns the unfiltered dict), so a lane master is served to anyone who learns its id (ids leak via the upload response `media_file_id`, logs, or webhook payloads — `send_event_webhook(..., {"recording": rec_payload})` ships the FULL rec_payload including lane entries and lane_label display names to external webhook consumers). The lane object also stores DOM-scraped participant names (`lane_label`) in meeting JSONB indefinitely with no deletion tie-in beyond the whole-recording delete path.
  suggested_fix: If lanes are truly internal-only, filter lane entries from webhook payloads and reject lane media_file ids on the user-facing media endpoints (or accept and document that owner-scoped access to lanes is fine — then remove the "internal-only" claim). Confirm the recording-delete path removes lane objects and prefixes (deletion reads raw JSONB per the code comment, so likely OK — verify with a test).

- id: BUG-023
  file: services/meeting-api/meeting_api/final_transcription.py
  line: 972
  category: logic-error
  impact: low
  title: Lane-derived transcripts record misleading provenance — session_uid and source_recording_path point at the mixed master
  evidence: When `lane_used` is true, every Transcription row is stored with `session_uid=source.session_uid` (the MIXED source's session, even for lanes from other recordings/sessions) and the success state records `"source_recording_path": source.storage_path` — the mixed master's path — alongside `"source": "deferred_lane_masters"`. Anyone auditing which audio produced the transcript (or re-running per-source) is pointed at a file that was never transcribed.
  suggested_fix: When lane_used, store the lane's own session_uid per segment and record the list of lane master storage_paths (e.g. `source_lane_paths`) in final_transcription state instead of (or alongside) the mixed master path.

score:
  critical: 2 x 10 = 20  (BUG-001, BUG-002)
  medium:  13 x 5 = 65  (BUG-003, BUG-004, BUG-005, BUG-006, BUG-007, BUG-008, BUG-009, BUG-010, BUG-011, BUG-012, BUG-013, BUG-016, BUG-020)
  low:      8 x 1 =  8  (BUG-014, BUG-015, BUG-017, BUG-018, BUG-019, BUG-021, BUG-022, BUG-023)
  total: 93
