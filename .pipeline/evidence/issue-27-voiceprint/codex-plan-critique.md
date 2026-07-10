<!-- 出所: Codex thread 019f4973-3eea-71a1-9361-2d7586ea5e88（task-mre7eznx-tpu77h / resume task-mre7om0v-mwwswb）。
     Codexサンドボックスがread-onlyでファイル書き込み不可だったため、最終メッセージ全文を
     オーケストレータ（Claude Code）が本ファイルへ転記した。内容は無改変。2026-07-10 -->

## Factual Corrections

1. Schema-sync の「新4テーブルは起動時に作られる」は、条件付きで成立。`services/meeting-api/meeting_api/main.py:261`-`267` の startup が `init_db()` を呼び、`services/meeting-api/meeting_api/database.py:90`-`97` が `schema_sync.ensure_schema(engine, Base, prerequisites=AdminBase)` を呼ぶ。`libs/schema-sync/schema_sync/sync.py:149`-`162` は `base.metadata.create_all(conn, checkfirst=True)` の後に column/index sync を走らせる。compose でも `deploy/compose/docker-compose.yml:254`-`358` の `meeting-api` がこのアプリを起動するので、モデルが `meeting_api.models.Base` に登録されていれば materialize される。

2. ただし cascade delete の主張は、モデル定義次第でまだ危ない。`create_all` は `ForeignKey(..., ondelete="CASCADE")` を書いた場合だけ DB-level cascade を作る。計画は `voiceprints.profile_id` には cascade と書く一方、`voiceprint_consents.subject_profile_id` は `FK` としか書いていない（`.pipeline/plans/issue-27-voiceprint/plan.md:68`-`78`）。`profile→voiceprints→consents` を DB FK で保証するなら、consents も `ondelete="CASCADE"` が必要。audit は計画通り `ON DELETE SET NULL` が妥当（`.pipeline/plans/issue-27-voiceprint/plan.md:75`-`78`）。

3. 新テーブルの index 作成に online migration は不要。ただしこの結論は「新規テーブル」限定。`libs/schema-sync/schema_sync/sync.py:121`-`146` は既存テーブル向けの index sync を同期実行し、`info={"online_only": True}` だけ skip する。`services/meeting-api/meeting_api/models.py:86`-`95` の `ix_transcription_meeting_cluster` はその実例。新4テーブルが空で作られるなら問題は小さいが、将来 voiceprints に大きい index を後追加するなら同じ online_only ルールが必要。

4. `run_deferred_transcription` の hook 位置は「クラスタ命名 + 保存済み訂正の後」で正しいが、正確には `services/meeting-api/meeting_api/final_transcription.py:1269`-`1274` の correction 再適用後、`Transcription` insert ループ `services/meeting-api/meeting_api/final_transcription.py:1277`-`1306` と `meeting.data` 更新 `services/meeting-api/meeting_api/final_transcription.py:1318`-`1354` の前後に設計判断が要る。ここなら `segments` は in-memory にある（`services/meeting-api/meeting_api/final_transcription.py:1158`-`1163`, `services/meeting-api/meeting_api/final_transcription.py:1216`-`1221`）。ただし `_derive_speaker_mapping_status` はこの関数内では呼ばれていないので、matching 側で同等条件を計算する必要がある。

5. all-or-nothing lane fallback との相互作用は、hook を `lane_used` が確定した後に置けば安全。lane は `_transcribe_lanes()` が一つでも失敗すると `LaneTranscriptionFallback` になり、mixed master に落ちる（`services/meeting-api/meeting_api/final_transcription.py:1151`-`1175`）。その前に声紋処理を入れると、捨てられる lane 結果に対して embedding を作る危険がある。hook は `services/meeting-api/meeting_api/final_transcription.py:1176` 以降、最終的な `segments` が確定してからに限定すべき。

6. offset shifting の見落としがある。lane path では `_shift_segment_times()` が segment start/end を mixed timeline に +offset する（`services/meeting-api/meeting_api/final_transcription.py:482`-`489`）。一方、lane 音声ファイル自体は lane-local timeline で、lane source は `start_offset_seconds` を保持している（`services/meeting-api/meeting_api/final_transcription.py:304`-`314`）。lane master から切るなら `start/end - lane.start_offset_seconds` が必要で、mixed master から切るなら同時発話や他者音声を含みやすい。

7. Phase 3 shared-mic の `needs_review` とは衝突し得るが、設計で避けられる。shared mic は `_apply_lane_identity()` が `speaker_cluster="lane:{laneKey}:{cluster}"` にし、`speaker=None` を強制する（`services/meeting-api/meeting_api/final_transcription.py:399`-`446`）。保存済み rename は後で勝つ（`services/meeting-api/meeting_api/final_transcription.py:1260`-`1274`）。したがって声紋 suggest は「`speaker` が空で lane sub-cluster 形式のものだけ」に後段 overlay すべきで、命名済み segment は触らないのが正解。

8. suggested-status merge は「関数を変えず caller 側で merge」というより、実装上は `_get_full_transcript_segments()` の PG/Redis merge 後に入れるのが安全。現在の PG path は `TranscriptionSegment(... speaker_mapping_status=_derive_speaker_mapping_status(...))`（`services/meeting-api/meeting_api/collector/endpoints.py:300`-`314`）、Redis path は `derived_status if ... else d.get("speaker_mapping_status")`（`services/meeting-api/meeting_api/collector/endpoints.py:365`-`390`）。Redis が同じ `segment_id` で PG に勝つ設計（`services/meeting-api/meeting_api/collector/endpoints.py:256`-`264`, `services/meeting-api/meeting_api/collector/endpoints.py:286`-`287`）なので、PG branch だけに suggested を足すと live cache がある時に消える。

9. `"suggested"` は型では落ちない。API schema は `speaker_mapping_status: Optional[str]`（`services/meeting-api/meeting_api/schemas.py:1044`-`1062`）、dashboard type も `speaker_mapping_status?: string`（`services/dashboard/src/types/vexa.ts:58`-`80`）、REST mapper も raw string を渡す（`services/dashboard/src/lib/api.ts:173`-`185`, `services/dashboard/src/lib/api.ts:222`-`240`）。ただし UI は `"needs_review"` だけを badge 表示している（`services/dashboard/src/components/transcript/transcript-segment.tsx:204`-`213`）。candidate name/score/profile_id を segment-level に追加しないと「候補: ○○ 87%」は表示できない。

10. dashboard は `meeting.data` を広く返す。transcript endpoint は `response_data["data"] = dict(meeting.data)` を返す（`services/meeting-api/meeting_api/collector/endpoints.py:481`-`488`）、`MeetingResponse` も `data` を返し、serializer は `webhook_secret` しか除外しない（`services/meeting-api/meeting_api/schemas.py:853`, `services/meeting-api/meeting_api/schemas.py:976`-`981`）。`meeting.data["speaker_suggestions"]` に profile_id/name/score を置くなら、必要な画面以外にも露出する前提で PII 判断が必要。

11. `SpeakerUpdateResponse.cluster_id` の単数追加は straightforward ではない。PATCH payload は rename/merge/reassign を同時に含められる（`services/meeting-api/meeting_api/meetings.py:1997`-`2021`）。rename by `from_cluster` は 1 cluster、rename by `from_name` は cluster 不明、merge は複数 cluster と representative（`services/meeting-api/meeting_api/meetings.py:2122`-`2155`）、reassign は segment ids と任意 `to_cluster`（`services/meeting-api/meeting_api/meetings.py:2157`-`2174`）。暗黙登録 UI のための echo は `cluster_id` 単数ではなく、`affected_clusters` または `enrollment_candidates[]` が必要。

12. ffmpeg slicing は実装可能。既存変換は temp file + `subprocess.run(["ffmpeg", "-i", ..., "-ar", "16000", "-ac", "1", "-f", "wav", ...])`（`services/meeting-api/meeting_api/final_transcription.py:751`-`781`）。storage は full download と byte range と stream-to-path を持つ（`services/meeting-api/meeting_api/storage.py:27`-`38`, `services/meeting-api/meeting_api/storage.py:199`-`227`, `services/meeting-api/meeting_api/storage.py:355`-`386`）。ただし byte range は時間 slice ではないので、webm/opus では直接使えない。full source を local file に落として ffmpeg concat するか、16k mono WAV 化後に PCM slice する必要がある。

13. ECAPA 入力として 30秒 cap は作業仮説として妥当寄りだが、根拠は「十分な可能性」止まり。plan は最大30秒・5秒未満 skip を提案している（`.pipeline/plans/issue-27-voiceprint/plan.md:56`-`62`）。既存 ffmpeg 変換は 16kHz mono WAV を作る（`services/meeting-api/meeting_api/final_transcription.py:763`-`765`）ので抽出器へ渡す形式としては自然。research/option matrix は ECAPA を 192次元・cosine score 前提とする（`.pipeline/plans/issue-27-voiceprint/research-brief.md:57`-`58`, `.pipeline/plans/issue-27-voiceprint/option-matrix.md:12`）。ただし非連続発話を無音なしで結合する前処理差、共有マイクの混入、cluster purity の方が精度を支配しそう。acceptance には 5/15/30秒の比較ログが必要。

14. meeting-api には `numpy` と `cryptography` がない。`services/meeting-api/requirements.txt:1`-`15` にどちらも未記載。repo 全体では `numpy` は transcription-service / tts-service / lite にあるだけで、`cryptography` / `Fernet` は requirements 検索で出ていない（検索結果: `services/transcription-service/requirements.txt:7`, `services/tts-service/requirements.txt:6`, `deploy/lite/requirements.txt:45`）。アプリ側 cosine と Fernet を meeting-api で行うなら、依存追加と image size / import startup の確認が必要。

15. Fernet key management は plan 自身も risk に書いているが、実装着手前の blocker に近い。plan は `VOICEPRINT_ENCRYPTION_KEY` の env 設定までしか定めないと書いている（`.pipeline/plans/issue-27-voiceprint/plan.md:83`-`89`, `.pipeline/plans/issue-27-voiceprint/plan.md:208`-`209`）。生体 PII なので、鍵が無い時の挙動、rotation、複数 key decrypt、key id / re-encrypt 手順が未定義のままでは危険。

16. voiceprint-service は deployable service としてまだ薄い。transcription-service は startup で model を load し（`services/transcription-service/main.py:211`-`247`）、health は model presence を返す（`services/transcription-service/main.py:250`-`260`）、Dockerfile は system deps と healthcheck を持つ（`services/transcription-service/Dockerfile:1`-`32`）、CPU compose は model cache volume を mount する（`services/transcription-service/docker-compose.cpu.yml:25`-`43`）。voiceprint-service plan は `/embed`,`/health` と compose 追加だけで（`.pipeline/plans/issue-27-voiceprint/plan.md:49`-`51`, `.pipeline/plans/issue-27-voiceprint/plan.md:222`-`223`）、model cache、build-time vs runtime download、healthcheck start period、auth token、concurrency/backpressure、memory limit、torch/torchaudio CPU wheel size、compose/meeting-api env wiring が未記述。

17. threshold の記述は軽い不整合あり。option-matrix は community 通例を 0.70-0.75 としつつ、提案初期値は 0.75-0.80（`.pipeline/plans/issue-27-voiceprint/option-matrix.md:36`-`55`）。plan の 0.78 は提案レンジ内なので採用自体は矛盾しないが、「community 通例の上限側」ではなく「option-matrix の提案初期レンジ内」と書くべき（`.pipeline/plans/issue-27-voiceprint/plan.md:96`-`101`）。

18. PII policy との最大差分は、保持期間と本人同意の実装面。policy は 24ヶ月保持と自動失効/削除 batch を要求する（`.pipeline/plans/issue-27-voiceprint/pii-policy-draft.md:52`-`64`, `.pipeline/plans/issue-27-voiceprint/pii-policy-draft.md:136`-`145`）が、plan の変更対象には retention job がない（`.pipeline/plans/issue-27-voiceprint/plan.md:148`-`161`, `.pipeline/plans/issue-27-voiceprint/plan.md:216`-`232`）。policy は本人以外の代理同意不可・本人不在なら別チャネル同意必須（`.pipeline/plans/issue-27-voiceprint/pii-policy-draft.md:29`-`50`）だが、plan は代理登録防止を out of scope にしている（`.pipeline/plans/issue-27-voiceprint/plan.md:121`-`126`, `.pipeline/plans/issue-27-voiceprint/plan.md:171`）。

19. 「同意なし保存不可」は DB schema だけではまだ保証できない。plan の `voiceprints` は consent_id を持たず、`voiceprint_consents` も profile_id だけなので（`.pipeline/plans/issue-27-voiceprint/plan.md:68`-`78`）、service transaction がバグれば voiceprint だけ insert できる。AC で「consent 無し insert 不可」を掲げるなら（`.pipeline/plans/issue-27-voiceprint/plan.md:184`）、`voiceprints.consent_id NOT NULL` + FK、または deferrable constraint / trigger / service-level invariant test のどれで保証するか明記が必要。

20. cost/latency impact は過小評価気味。現 deferred path は STT 完了後に DB insert へ進むが、voiceprint matching を同期で挟むと cluster 数ぶんの slice/ffmpeg、HTTP `/embed`、復号、cosine 計算が追加される。`services/meeting-api/meeting_api/final_transcription.py:1354` の commit まで遅れると transcript 完了自体が遅くなる。plan は「クラスタごとの複数回ffmpeg呼び出しはdeferred待ち時間を増やす」とは書くが、failure/timeout を transcript failure にしない設計まではない（`.pipeline/plans/issue-27-voiceprint/plan.md:210`-`211`）。

## Adopted-Recommended Changes

1. 新テーブル設計は採用してよいが、FK を明文化する。最低限、`voiceprints.profile_id -> speaker_profiles.id ON DELETE CASCADE`、`voiceprint_consents.subject_profile_id -> speaker_profiles.id ON DELETE CASCADE`、`voiceprint_audit_log.subject_profile_id -> speaker_profiles.id ON DELETE SET NULL`。これは plan の現行スキーマが consent 側 cascade を明記していないため（`.pipeline/plans/issue-27-voiceprint/plan.md:68`-`78`）、削除要件（`.pipeline/plans/issue-27-voiceprint/plan.md:128`-`133`）を DB-level で満たすために必要。

2. `run_deferred_transcription` の matching は、最終 `segments` が確定し、manual corrections が反映された後に限定する。実装目安は `services/meeting-api/meeting_api/final_transcription.py:1269`-`1274` の直後。失敗時は `final_transcription.status="failed"` にしない。`meeting.data["speaker_suggestions"]` は replace 時に stale clear し、今回 run の `source_recording_path` / `source` / `completed_at` と結びつける（`services/meeting-api/meeting_api/final_transcription.py:1318`-`1348`）。

3. 音声切り出しは mixed/lane で分岐する。mixed path は `source.storage_path` + segment start/end。lane path は `lane_sources` を `lane_key -> source` にし、`seg["_lane_key"]` と `lane.start_offset_seconds` で lane-local time に戻して切る。根拠は lane source が offset と storage path を持つこと（`services/meeting-api/meeting_api/final_transcription.py:304`-`314`）と、segment が mixed timeline へ shift されること（`services/meeting-api/meeting_api/final_transcription.py:482`-`489`）。

4. `_get_full_transcript_segments()` は suggestions を受け取るか内部で `Meeting.data` を読むようにし、PG/Redis を merge した後に `speaker_cluster` 単位で overlay する。条件は「derived/current status が needs_review 相当、speaker 空、suggestion.status == suggested」。PG/Redis それぞれの status 導出位置は `services/meeting-api/meeting_api/collector/endpoints.py:300`-`314` と `services/meeting-api/meeting_api/collector/endpoints.py:365`-`390`、Redis 優先 merge は `services/meeting-api/meeting_api/collector/endpoints.py:256`-`264`。

5. API schema / dashboard type には status だけでなく、候補 payload を追加する。例: `speaker_suggestion?: { profile_id, candidate_display_name, similarity, status }`。現状の backend schema は status しか持たず（`services/meeting-api/meeting_api/schemas.py:1044`-`1062`）、dashboard mapper も status だけを渡す（`services/dashboard/src/lib/api.ts:173`-`185`, `services/dashboard/src/lib/api.ts:222`-`240`）。UI は `"needs_review"` badge だけなので（`services/dashboard/src/components/transcript/transcript-segment.tsx:204`-`213`）、候補表示には追加 payload が必要。

6. PATCH response は `cluster_id` 単数ではなく、`affected_clusters: string[]` か `enrollment_candidates: [{ cluster_id, display_name, updated_count, operation }]` にする。理由は PATCH request が rename/merge/reassign を同時に受ける設計で（`services/meeting-api/meeting_api/meetings.py:2018`-`2021`）、merge は複数 cluster を代表 cluster に畳む（`services/meeting-api/meeting_api/meetings.py:2122`-`2155`）ため。implicit enroll は UI が元々選択していた cluster を明示して `POST /voiceprints/enroll-from-cluster` に渡す。

7. meeting-api には `numpy` / `cryptography` を追加するだけでなく、key validation と disabled mode を入れる。現 requirements には両方ない（`services/meeting-api/requirements.txt:1`-`15`）。`VOICEPRINT_ENCRYPTION_KEY` が無い場合、enroll は 503、matching は skip + audit、既存 voiceprints は復号しない。plan が key rotation を未整備 risk としているため（`.pipeline/plans/issue-27-voiceprint/plan.md:208`-`209`）、最初から key ring の設計余地を残すべき。

8. voiceprint-service は transcription-service 相当の運用要素を計画に足す。`API_TOKEN`、`MAX_ACTIVE_REQUESTS`、healthcheck、model cache volume、startup warmup、timeout、payload size cap、CPU wheel strategy、compose env (`VOICEPRINT_SERVICE_URL`, token) と `depends_on: condition: service_healthy` を明記する。比較元として transcription-service は startup model load と health を持つ（`services/transcription-service/main.py:211`-`260`）、Dockerfile healthcheck を持つ（`services/transcription-service/Dockerfile:26`-`32`）、CPU compose で model volume を持つ（`services/transcription-service/docker-compose.cpu.yml:25`-`43`）。

9. PII surface は「embedding を保存しない」だけでは足りない。`meeting.data["speaker_suggestions"]` は API で広く返るため、保存する項目を最小化し、profile_id を UI に出す必要がないなら segment response 側だけで返す。既存 `MeetingResponse` serializer は `webhook_secret` しか除外しない（`services/meeting-api/meeting_api/schemas.py:976`-`981`）、transcript endpoint は `meeting.data` を返す（`services/meeting-api/meeting_api/collector/endpoints.py:481`-`488`）。

10. acceptance criteria に latency と stale-suggestion 回帰を足す。例: voiceprint-service unavailable でも deferred transcript は succeeded、matching skipped が audit される。mode=replace 後に旧 suggestion が出ない。Redis が PG に勝つ状態でも suggested overlay が残る。lane offset ありの cluster slicing が正しい。根拠は replace が既存行を削除して再作成すること（`services/meeting-api/meeting_api/final_transcription.py:1253`-`1306`）、Redis が PG に勝つこと（`services/meeting-api/meeting_api/collector/endpoints.py:256`-`287`）、lane offset が segment time を変えること（`services/meeting-api/meeting_api/final_transcription.py:482`-`489`）。

## Rejected-Counterpoints

1. 「新4テーブルにもオンライン migration が必須」という反論は採らなくてよい。実コード上、startup schema-sync は `create_all(checkfirst=True)` を呼ぶので、新規空テーブルは作れる（`libs/schema-sync/schema_sync/sync.py:70`-`72`, `libs/schema-sync/schema_sync/sync.py:149`-`162`）。既存 507K 行の `transcriptions` と同列に扱う必要はない。ただし既存大規模テーブル向け index は `online_only` skip の仕組みがある（`services/meeting-api/meeting_api/models.py:86`-`95`）。

2. 「`speaker_mapping_status` が enum で suggested を弾く」という懸念は現状コードでは不成立。backend schema も dashboard type も string で、REST mapper も pass-through する（`services/meeting-api/meeting_api/schemas.py:1059`, `services/dashboard/src/types/vexa.ts:70`, `services/dashboard/src/lib/api.ts:237`-`239`）。

3. 「ffmpeg slicing は既存 storage API では不可能」という反論も採らなくてよい。時間 slice API は無いが、full download / stream-to-path と ffmpeg subprocess pattern はある（`services/meeting-api/meeting_api/storage.py:199`-`227`, `services/meeting-api/meeting_api/storage.py:355`-`386`, `services/meeting-api/meeting_api/final_transcription.py:751`-`781`）。問題は feasibility ではなく、lane offset と非連続 concat と latency budget。

4. 「pgvector を入れないと照合できない」という反論は現時点では弱い。plan は単一テナント・高々数百 profile・192次元を前提にアプリ側 cosine を選んでいる（`.pipeline/plans/issue-27-voiceprint/plan.md:83`-`89`）。ただし meeting-api に numpy が無いので（`services/meeting-api/requirements.txt:1`-`15`）、依存追加と O(clusters * voiceprints) の timeout は acceptance に入れるべき。

5. 「suggested は needs_review と競合するからやめるべき」ではなく、「needs_review の上に候補 payload を overlay する」と整理すればよい。Phase 3 の shared-mic safety は `speaker` を空のまま維持することで守れる（`services/meeting-api/meeting_api/final_transcription.py:399`-`446`）。read path は `speaker` が入ると needs_review を消す（`services/meeting-api/meeting_api/collector/endpoints.py:237`-`253`）ので、voiceprint suggest は `Transcription.speaker` を変えない設計が正しい。

## Blockers

1. Consent invariant が schema で保証されていない。`voiceprints` と `voiceprint_consents` が独立 profile FK のままだと、同意なし保存不可 AC を DB レベルで満たせない（`.pipeline/plans/issue-27-voiceprint/plan.md:68`-`78`, `.pipeline/plans/issue-27-voiceprint/plan.md:184`）。少なくとも plan に保証方式を追記するまで implementation 承認は危ない。

2. `VOICEPRINT_ENCRYPTION_KEY` の missing/rotation 方針が未定。生体 PII path なので、鍵が無い時に保存しないこと、旧鍵復号、新鍵再暗号化、漏洩時の操作が決まっていないまま進めるのはブロッカー。plan は `VOICEPRINT_ENCRYPTION_KEY` を env とするだけで（`.pipeline/plans/issue-27-voiceprint/plan.md:83`-`85`）、rotation は未整備 risk として残している（`.pipeline/plans/issue-27-voiceprint/plan.md:208`-`209`）。

3. retention 24ヶ月の削除 job が plan の変更対象に無い。policy は保持期間と自動失効/削除を要求している（`.pipeline/plans/issue-27-voiceprint/pii-policy-draft.md:52`-`64`, `.pipeline/plans/issue-27-voiceprint/pii-policy-draft.md:141`）。plan の実装順はデータモデル、service、切り出し、照合、API、dashboard、テストだが retention job がない（`.pipeline/plans/issue-27-voiceprint/plan.md:216`-`232`）。

4. 本人同意の扱いが policy と plan でズレている。policy は代理同意不可・本人不在なら別チャネル同意必須（`.pipeline/plans/issue-27-voiceprint/pii-policy-draft.md:29`-`50`）。plan は代理登録防止を技術的 out of scope にしている（`.pipeline/plans/issue-27-voiceprint/plan.md:121`-`126`, `.pipeline/plans/issue-27-voiceprint/plan.md:171`）。少なくとも human approval で明示的にリスク受容が必要。

5. `meeting.data["speaker_suggestions"]` の露出範囲が未承認。既存 API は `meeting.data` をほぼそのまま返すため、候補名/profile_id/score を JSONB に置くなら PII surface と redaction 方針が必要。根拠は transcript endpoint の `data` 返却（`services/meeting-api/meeting_api/collector/endpoints.py:481`-`488`）と `MeetingResponse` の serializer が `webhook_secret` しか除外しないこと（`services/meeting-api/meeting_api/schemas.py:976`-`981`）。

6. voiceprint-service の deploy plan が不足。model download/cache、healthcheck、auth、memory、torch CPU wheel、meeting-api env wiring が決まらないと compose で「起動はしたが cold start で落ちる/毎回 DL/無認証 PII endpoint」になり得る。比較元の transcription-service は startup load / health / Docker healthcheck / model volume を持つ（`services/transcription-service/main.py:211`-`260`, `services/transcription-service/Dockerfile:26`-`32`, `services/transcription-service/docker-compose.cpu.yml:25`-`43`）が、plan は `/embed`,`/health` と compose 追加程度に留まる（`.pipeline/plans/issue-27-voiceprint/plan.md:49`-`51`, `.pipeline/plans/issue-27-voiceprint/plan.md:222`-`223`）。

## Needs-Human

1. PII 方針 §7 の8項目承認を、plan hash と紐づけて実施する。policy の承認欄は未チェックのままで（`.pipeline/plans/issue-27-voiceprint/pii-policy-draft.md:107`-`118`）、plan も人間承認をブロッカーとしている（`.pipeline/plans/issue-27-voiceprint/plan.md:218`-`219`）。特に代理登録防止を技術実装するか、運用リスクとして明示受容するかを決める。

2. `speaker_suggestions` を `meeting.data` に保存して API 全体へ出す設計を許容するか、専用 endpoint / segment-level response のみに閉じるかを決める。plan は JSONB 保存を提案している（`.pipeline/plans/issue-27-voiceprint/plan.md:103`-`105`）が、既存 API は `meeting.data` を返す（`services/meeting-api/meeting_api/collector/endpoints.py:481`-`488`, `services/meeting-api/meeting_api/schemas.py:976`-`981`）。

3. `cluster_id` echo の UX semantics を決める。rename だけ登録オファー対象にするのか、merge は複数 cluster をまとめて登録するのか、reassign は登録対象外にするのか。現 PATCH は rename/merge/reassign を同時に受ける（`services/meeting-api/meeting_api/meetings.py:2018`-`2021`）ため、plan の単数 `cluster_id` 追加（`.pipeline/plans/issue-27-voiceprint/plan.md:121`-`124`）では意味が曖昧。

4. Key management を決める。単一 Fernet key で始めるのか、最初から key ring + key id + re-encrypt path を持つのか。missing-key 時に matching を skip する運用でよいか。plan は Fernet と env key を提案するが（`.pipeline/plans/issue-27-voiceprint/plan.md:83`-`89`）、rotation は別課題扱い（`.pipeline/plans/issue-27-voiceprint/plan.md:208`-`209`）。

5. しきい値 0.78 は `0.75-0.80` の作業仮説として採用してよいか。option-matrix は初期値 0.75-0.80 を提案している（`.pipeline/plans/issue-27-voiceprint/option-matrix.md:36`-`55`）。plan の 0.78 はその範囲内だが、説明は「community 通例の上限側」ではなく「提案初期レンジ内」に修正した方がよい（`.pipeline/plans/issue-27-voiceprint/plan.md:96`-`101`）。採用するなら auto 移行ではなく suggest-only に限定し、初期ログで FMR/FRR を見る件数と承認基準を決める。

6. Latency budget を決める。deferred transcript 完了に追加してよい上限秒数、voiceprint-service timeout、cluster 数上限、matching failure を transcript failure にしない方針を明文化する。現 deferred path は `meeting.data` 更新と commit が `services/meeting-api/meeting_api/final_transcription.py:1318`-`1354` にあり、ここへ同期 matching を入れると完了が遅れる。plan は latency risk を認識しているが（`.pipeline/plans/issue-27-voiceprint/plan.md:210`-`211`）、failure policy までは未定義。
