## Factual Corrections

- `media_type = "lane-{laneKey}"` は upload 経路ではストレージ prefix と JSONB entry を分ける。`services/meeting-api/meeting_api/recordings.py:347-354` が metadata から `media_type` を受け、`recordings.py:417` が `.../{media_type}/{chunk_seq}.{format}` に保存し、`recordings.py:475-490` が `mf["type"] == media_type` 単位で既存 entry を差し替えるため。ただし「media_type は全体で自由形式」は不正確。読み出し schema は `MediaFileType` enum を `audio|video|screenshot` に固定しており、`RecordingResponse.model_validate()` は `/recordings` と `/recordings/{id}` で使われる（`services/meeting-api/meeting_api/schemas.py:1262-1269`, `recordings.py:563-587`）。lane entry を入れると response validation が壊れる可能性が高い。

- prefix 分離の helper 自体は plan の方向で合っている。`_chunk_prefix()` は storage_path の dirname を返し、`_finalize_one_media_file_sync()` は `prefix + "/"` だけを list する（`services/meeting-api/meeting_api/recording_finalizer.py:377-387`, `429-452`）。したがって `audio/` master の list に `lane-*/` が混ざる構造ではない。

- ただし「既存 finalizer が per-lane master.webm を無償で作る」は現状コードでは false。`finalize_recording_master()` は `mf_type not in ("audio", "video")` を skip する（`services/meeting-api/meeting_api/recording_finalizer.py:615-624`）。`lane-*` は master 作成対象にならない。

- `lane-*` の content type は現状だと audio ではなく video 扱いになる。`recordings.media_content_type()` と finalizer 側 `_media_content_type()` は `fmt == "webm"` で `typ == "audio"` 以外を `video/webm` にする（`services/meeting-api/meeting_api/recordings.py:146-150`, `services/meeting-api/meeting_api/recording_finalizer.py:401-405`）。

- sweep/recovery は lane を拾わない。`_parse_recording_chunk_key()` が `media_type not in {"audio", "video"}` を除外し、`_recording_has_playback_url()` も `audio|video` だけを見る（`services/meeting-api/meeting_api/sweeps.py:335-359`）。JSONB write が失われた lane chunk は recovery されず、audio playback_url がある recording では lane 未finalizeでも sweep 対象から外れ得る（`sweeps.py:519-525`）。

- playback は lane を master としては扱わない。backend master endpoint は query `type` を `audio|video` に制限し、MP3 master は `audio` のみ（`services/meeting-api/meeting_api/recordings.py:590-648`）。dashboard proxy も master proxy の `type` を `audio|video` に絞る（`services/dashboard/src/app/api/vexa/[...path]/route.ts:143-147`）。これは「lane を通常再生UIに出さない」意図なら整合するが、lane を API レスポンスに含めるには schema 更新が必要。

- `/recordings/{id}/media/{media_file_id}/mp3` は lane を audio と見なさない。`mf["type"] != "audio"` なら 400 になる（`services/meeting-api/meeting_api/recordings.py:821-825`）。lane の診断 download を MP3 でも欲しいなら追加仕様が必要。

- deletion は type 前提ではなく全 media_files を回るため lane entry 自体は削除対象になる（`services/meeting-api/meeting_api/recordings.py:855-873`）。ただし削除するのは `mf.storage_path` と mp3 sibling だけで、finalizer 後に残る chunk 群は既存 audio と同じく bucket lifecycle 任せ。GCS lifecycle は prefix 条件なしの age rule（`deploy/gcs/lifecycle.json:1-10`）。

- Drive export は media_files を直接使わず、`Transcription` rows を Markdown にする（`services/meeting-api/meeting_api/drive_export.py:221-230`, `320-355`）。lane media_files の存在そのものではなく、lane STT がどの speaker/cluster で transcript rows を作るかが影響点。

- bot 側の `BrowserMediaRecorderPipeline` はインスタンス状態を持つため多重化の余地はある（`services/vexa-bot/core/src/utils/browser.ts:329-356`）。ただし既存 `MediaRecorderCapture` は単一の `window.__vexaSaveRecordingChunk` を expose し、payload に lane identity がない（`services/vexa-bot/core/src/services/audio-pipeline.ts:572-594`）。`UnifiedRecordingPipeline` も単一 `RecordingService.uploadChunk()` へ流し、`uploadChunk()` metadata は `media_type` / lane metadata を持たない（`audio-pipeline.ts:295-302`, `services/vexa-bot/core/src/services/recording.ts:225-236`）。「既存 pipeline の単純 reuse」では lane upload はできない。

- combined master を壊さず per-element tap を並走させる feasibility はある。Google Meet の master は `createCombinedAudioStream(mediaElements)` から作った stream を `BrowserMediaRecorderPipeline` に渡す（`services/vexa-bot/core/src/platforms/googlemeet/recording.ts:90-113`）。一方、既存の live per-speaker capture は同じ browser page 内で各 `srcObject` を `AudioContext` に接続し、15秒 rescan で late joiner / element recycling を拾う（`services/vexa-bot/core/src/index.ts:2041-2164`）。ただしこれは PCM callback であり、lane MediaRecorder と lane upload registry は未実装。

- lane-id の安定性は plan より弱い。`getGoogleParticipantId()` は `data-participant-id` → `jsinstance` → element.dataset 上のランダム `gm-id-*` の順で返す（`services/vexa-bot/core/src/platforms/googlemeet/recording.ts:210-224`）。fallback id は DOM element に保存されるため、element replacement で同一人物でも別 id になる。

- screen share / presentation では participant tile が消えることを既存コードが前提にしている（`services/vexa-bot/core/src/platforms/googlemeet/recording.ts:498-501`, `605-620`）。tile 出現/消滅に lane recorder lifecycle を結びつけると、presentation 中に lane を止める、または再生成するリスクがある。participantId churn そのものはこのコードだけでは実測確認できないが、tile 消失は実装上の既知事象。

- 既存 live speaker mapping は participant count 変化で lock を含む mapping を invalidation する（`services/vexa-bot/core/src/index.ts:1724-1745`）。「新しい participantId = 新しい lane」は安全側ではあるが、audio track / stream reassignment と同一人物 rejoin の分断を吸収しない。

- `final_transcription.py` は現在、finalized な `type == "audio"` master を1本だけ選ぶ構造。JSONB 経路は `_is_master_audio_media_file()` で `mf["type"] == "audio"` を要求し、DB fallback も `MediaFile.type == "audio"` を要求する（`services/meeting-api/meeting_api/final_transcription.py:129-139`, `168-207`）。`run_deferred_transcription()` は単一 source を download して単一 STT call に渡す（`final_transcription.py:642-701`）。

- Phase 1 の cluster naming とはそのままでは衝突する。STT response の `speaker` があると `_parse_segments()` はそれを `speaker_cluster` にコピーし、DOM vote で名前を決める（`services/meeting-api/meeting_api/final_transcription.py:437-450`）。lane metadata / `lane_label` は現在使われない。単独 lane を `speaker_cluster = "lane:{laneKey}"` で自動確定するには、generic DOM vote の前後で lane 用の明示処理が必要。保存済み cluster corrections は `speaker_cluster` キーで再適用される（`final_transcription.py:740-747`）。

- Pack U.7 master-path preservation は lane では弱い。upload handler の master 判定は `/audio/master.webm|wav` または `prior_is_final` だけ（`services/meeting-api/meeting_api/recordings.py:492-501`）。finalizer は commit 時に `is_final=True` を付けるので完了後は守られるが、lane master path が入って `is_final` がまだ反映されていない race window では suffix 判定で守れない。post-meeting reconciler も `/audio/master.*` だけを finalizer 所有の signal として扱う（`services/meeting-api/meeting_api/post_meeting.py:319-341`）。

## Adopted-Recommended Changes

- plan に「upload は自由形式だが read schema / dashboard type / docs は自由形式ではない」を明記し、`MediaFileType` を `lane-*` 許容に変える。最低限、`services/meeting-api/meeting_api/schemas.py:1262-1269` と `services/dashboard/src/types/vexa.ts:495-500` を更新する acceptance criteria が必要。

- `is_audio_like_media_type(type)` などの共通 helper を meeting-api 側に置き、`audio` と `lane-*` を audio-like として扱う。対象は `recordings.media_content_type()`、`recording_finalizer._media_content_type()`、finalizer の media type allowlist、sweeps recovery、late-chunk master-path guard。

- finalizer は `lane-*` を連結対象にするが、`playback_url` は引き続き `audio/video` のみ生成する、という境界をテスト化する。根拠箇所は `recording_finalizer.py:615-624` と `692-704`。

- sweeps は `lane-*` chunk を recovery できるようにする。特に `_parse_recording_chunk_key()` の allowlist と `_recording_has_playback_url()` の「audio/video master があれば完了」判定を見直す（`services/meeting-api/meeting_api/sweeps.py:335-359`, `519-525`）。

- bot 側は master pipeline とは別に lane registry を作る。既存 `__vexaSaveRecordingChunk` / `UnifiedRecordingPipeline` を lane に流用するより、lane 用 callback payload に `media_type`, `lane_id`, `lane_label`, `lane_id_source` を含め、`RecordingService.uploadChunk()` に options 引数を追加する方が事故範囲を切りやすい（根拠: `audio-pipeline.ts:572-594`, `recording.ts:214-236`）。

- lane recorder の key は DOM tile だけでなく `MediaStream.id` / `MediaStreamTrack.id` も併用し、tile 消失中も track が alive なら lane を継続する設計にする。既存 per-speaker capture は stream.id で duplicate binding を避け、track `ended` で解除している（`services/vexa-bot/core/src/index.ts:2073-2107`）。

- `gm-id-*` fallback は自動確定の信頼度を下げる。`lane_id_source: "generated"` の場合は lane_label を保存しても、単独 tile 自動確定をそのまま通すかは人間判断に回す、または `speaker_auto_confidence` 相当を残す方が安全。

- deferred STT は「lane source list → lane STT results → merged transcript rows → no-lane fallback to mixed audio」という明示構造にする。lane STT 成功時は generic `_parse_segments()` だけに任せず、単独 lane では `speaker_cluster = "lane:{laneKey}"`, `speaker = lane_label`, `speaker_auto = lane_label` を明示してから saved corrections を適用する。segment_id には laneKey を含め、複数 lane の `idx/start` 衝突を避ける。

- acceptance criteria に以下を追加する: lane entry を含む `/recordings` response validation、`lane-*` content-type が `audio/webm` になること、lane finalizer が `lane-*/master.webm` を作ること、audio master の input key set が byte-identicalに不変なこと、sweep recovery が JSONB missing lane を復元すること、lane master 後 late chunk が storage_path を戻さないこと、feature flag off で bot の upload metadata と master bytes が現行と一致すること。

- cost control は env 名だけでなく実装 evidence を要求する。`RECORD_PARTICIPANT_LANES` default off、`MAX_RECORDING_LANES`、lane STT concurrency、per-meeting lane STT byte/duration cap、超過時の skipped metadata を acceptance criteria に入れる。調査時点の実装コードには、これらの env 読み取りや lane metadata 保存処理は見当たらない。

## Rejected-Counterpoints

- 「media_type が自由形式だから lane を載せれば既存全経路が通る」は退ける。upload/write path は通るが、finalizer allowlist、sweep recovery、response schema、content-type が未対応（`recordings.py:417-490`, `recording_finalizer.py:623-624`, `sweeps.py:344`, `schemas.py:1262-1269`）。

- 「per-media-file finalizer で lane master が無料」は退ける。per-media-file ループ自体はあるが、`lane-*` は `mf_type not in ("audio", "video")` で skip される（`services/meeting-api/meeting_api/recording_finalizer.py:615-624`）。

- 「gm-id fallback でも lane_label があるから十分」は退ける。fallback id は DOM element dataset に保持されるだけなので、element replacement で別 lane になり得る（`services/vexa-bot/core/src/platforms/googlemeet/recording.ts:218-223`）。同じ label の複数 lane を自動確定すると、後続の merge/correction 責務が増える。

- 「dashboard は完全に無変更でよい」は半分だけ退ける。再生 UI は `playback_url.audio/video` だけを見るので lane を再生対象にしない方針とは合う（`services/dashboard/src/app/meetings/[id]/page.tsx:210-247`）。ただし TypeScript の `MediaFileType` は `audio|video|screenshot` 固定で、lane を含む API response 契約とは合わない（`services/dashboard/src/types/vexa.ts:495-500`）。

- 「Drive export が lane media_files で壊れる」は現コード根拠では退ける。Drive export は media_files ではなく `Transcription` rows を読む（`services/meeting-api/meeting_api/drive_export.py:221-230`, `347-353`）。問題は lane STT が作る speaker/cluster rows の品質。

## Blockers

- `RecordingResponse` / `RecordingListResponse` が lane entry を返せない可能性が高い。`MediaFileResponse.type` が enum 固定で、recording endpoints は `RecordingResponse.model_validate()` を返す（`services/meeting-api/meeting_api/schemas.py:1262-1299`, `services/meeting-api/meeting_api/recordings.py:563-587`）。これは plan 承認前に必ず修正方針が必要。

- finalizer と sweep が `lane-*` を処理しない。`recording_finalizer.py:623-624` と `sweeps.py:344` が明示的な blocker。これを直さない限り「lane master.webm を deferred STT 入力にする」は成立しない。

- bot upload path に lane identity を運ぶ口がない。`RecordingService.uploadChunk()` metadata は `media_type`, `lane_id`, `lane_label`, `lane_id_source` を持たず（`services/vexa-bot/core/src/services/recording.ts:225-236`）、既存 `MediaRecorderCapture` callback payload も lane 情報を持たない（`services/vexa-bot/core/src/services/audio-pipeline.ts:572-594`）。

- deferred transcription は単一 audio master 前提。lane 優先・mixed fallback・複数 lane merge・partial lane failure の状態モデルが未定義のままでは、`run_deferred_transcription()` の成功/失敗/replace semantics と衝突する（`services/meeting-api/meeting_api/final_transcription.py:642-806`）。

- Phase 1 cluster naming と lane auto-label の統合が未設計。現行 `_parse_segments()` は STT diarization cluster を DOM vote で命名し、lane_label は見ない（`services/meeting-api/meeting_api/final_transcription.py:437-453`）。`speaker_cluster = "lane:{laneKey}"` を採用するなら、saved corrections の key 空間と merge API の扱いを明記する必要がある（`final_transcription.py:498-516`, `740-747`）。

- Pack U.7 late-chunk preservation の lane 対応が未明記。`/audio/master.*` 固定判定を generic master path または audio-like type に拡張しないと、lane master path race を audio と同等には守れない（`services/meeting-api/meeting_api/recordings.py:492-501`, `services/meeting-api/meeting_api/post_meeting.py:334-341`）。

- lane-id と recorder lifecycle の DOM 対応が不足している。Google Meet は presentation 中に participant tiles が消える前提の回避コードをすでに持つ（`services/vexa-bot/core/src/platforms/googlemeet/recording.ts:498-501`, `605-620`）。lane recorder を tile 出現/消滅で起動停止する設計は、この既知挙動に対する仕様が必要。

## Needs-Human

- lane STT の fallback policy を決める必要がある。候補は「lane が1本でもあれば mixed fallback しない」「全 lane 成功時だけ lane transcript、1本でも失敗なら mixed master に全面 fallback」「成功 lane と mixed を混ぜないで operator review」。ここを曖昧にすると重複 transcript や speaker correction の破壊が起きる。

- `gm-id-*` fallback lane を自動確定対象にするか、人間確認対象にするか決める必要がある。コード上は fallback id の安定性が element lifetime に依存するため（`services/vexa-bot/core/src/platforms/googlemeet/recording.ts:218-223`）、plan の「random fallback tolerated」は運用品質の判断が要る。

- lane media_files を public API に見せるか、内部 metadata として隠すか決める必要がある。見せるなら schema / dashboard type / MCP bundle / CLI 表示の契約更新が必要。隠すなら `/recordings` serializer で lane を除外しつつ deferred STT は内部的に参照する別設計が必要。

- cost cap の具体値を決める必要がある。plan の default 8 lanes は妥当そうだが、STT concurrency、最大 lane duration、最大 total lane bytes、calendar/export 再試行時の再課金防止までは実コードからは決められない。

- Google Meet 実機 PoC が必要。コードから tile 消失・fallback id の不安定化リスクは読めるが、`data-participant-id` / `jsinstance` が layout change、screen share、rejoin でどの程度 churn するかはこの repo だけでは検証できない。
