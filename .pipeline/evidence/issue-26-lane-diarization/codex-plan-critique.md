## Factual Corrections

- FC-1: Phase 2 の `lane:{laneKey}:{cluster}` namespace と DOM-vote 命名の現状は実コードで確認できる。`_parse_segments()` は STT segment の `speaker` を `speaker_cluster` にコピーし、`name_clusters_by_dom_vote()` の結果を `seg["speaker"]` に入れる（`services/meeting-api/meeting_api/final_transcription.py:734-747`）。その後 `_apply_lane_identity()` は multi-cluster lane で `speaker_cluster` を `lane:{lane_key}:{cluster}` にするだけで、DOM vote 済みの `speaker` は消していない（`services/meeting-api/meeting_api/final_transcription.py:314-338`）。この挙動は既存テストでも明示されており、multi-cluster lane の先頭 segment が DOM vote で `"山森"` になることを期待している（`services/meeting-api/tests/test_final_transcription_lanes.py:245-295`）。

- FC-2: DOM 名を sub-speaker に残さないための backend 変更面は、lane path では `_apply_lane_identity()` 後の上書きでほぼ閉じる。保存前に `speaker_auto = seg.get("speaker")` が入るため、K>1 分岐で `speaker` を `None` にしてから保存処理へ渡せば DOM vote 名は `speaker_auto` にも残らない（`services/meeting-api/meeting_api/final_transcription.py:1122-1129`）。ただし mixed-master の diarization path は引き続き `_parse_segments()` の DOM vote を使うので、変更対象は「lane の multi-cluster」に限定して書くべき（`services/meeting-api/meeting_api/final_transcription.py:1078-1083`）。

- FC-3: Soniox adapter への `token_count` 追加は実装面では clean。`fold_tokens_to_segments()` は token run を `current` dict に畳み込み、最後に segment dict を作る構造なので、run に count を持たせて segment に出すだけで足りる（`services/transcription-service/soniox_adapter.py:56-119`）。meeting-api 側も `_parse_segments()` が `dict(segment)` で未知フィールドを保持するため、`token_count` は lane 後処理まで届く（`services/meeting-api/meeting_api/final_transcription.py:717-722`）。

- FC-4: ただし `token_count` は golden/contract test をそのままでは壊す。`test_golden_fixture_replay_is_deterministic()` は `build_verbose_json_response()` の出力と golden response の完全一致を要求している（`services/transcription-service/tests/test_soniox_adapter.py:99-107`）。現 golden response の segment には `token_count` が無い（`contracts/stt/v1/examples/golden-2-diarization.response.json:7-20`）。契約 README と adapter manifest も現在は optional `speaker` だけを diarization extension としている（`contracts/stt/v1/README.md:16-30`, `.pipeline/adapters/soniox-stt.adapter.json:22-29`）。

- FC-5: Correction API は `lane:{key}:{cluster}` のような colon 入り cluster key でも即時 rename/merge/reassign は動く。rename は `Transcription.speaker_cluster == op.from_cluster` の文字列一致、merge は `.in_(op.clusters)`、reassign は `segment_id` 対象で `to_cluster` を任意文字列として保存するだけで、cluster 文字列を split していない（`services/meeting-api/meeting_api/meetings.py:2097-2169`）。保存済み rename も `speaker_corrections.clusters` の文字列 key をそのまま読む（`services/meeting-api/meeting_api/final_transcription.py:795-813`）。

- FC-6: ただし「merge/reassign も replace 後に完全復元される」とまでは言えない。replace 時の再適用は `cluster -> name` だけで、merge の代表 `speaker_cluster` への再集約は行わない（`services/meeting-api/meeting_api/final_transcription.py:1124-1129`）。reassign は history に残るだけで、segment_id 単位の再適用ロジックが無い（`services/meeting-api/meeting_api/meetings.py:2157-2179`）。AC2 の「1回命名で全発話反映」は rename で満たせるが、計画では merge/reassign の永続 semantics を盛らない方がいい。

- FC-7: `speaker_mapping_status` は API schema と dashboard type には存在するが、deferred/PG read path では現状セットされない。schema は `speaker_mapping_status` を持つ（`services/meeting-api/meeting_api/schemas.py:1044-1062`）、DB model には対応 column が無い（`services/meeting-api/meeting_api/models.py:63-96`）、PG segment 構築では `speaker_cluster` と `speaker_auto` だけを入れている（`services/meeting-api/meeting_api/collector/endpoints.py:264-285`）。Redis segment では status を読む（`services/meeting-api/meeting_api/collector/endpoints.py:336-349`）。

- FC-8: 「読み出し時導出」だけでは dashboard に届かない。REST mapper の `RawSegment` に `speaker_mapping_status` が無く、`TranscriptSegment` への mapping でも渡していない（`services/dashboard/src/lib/api.ts:173-185`, `services/dashboard/src/lib/api.ts:221-238`）。一方 websocket hooks は status を渡しているので、欠落は REST/deferred 表示に集中している（`services/dashboard/src/hooks/use-vexa-websocket.ts:147-165`, `services/dashboard/src/hooks/use-live-transcripts.ts:81-100`）。

- FC-9: dashboard grouping bug の表現は補正が必要。現コードは「1 segment を 1 group に wrap」しており、複数 segment をデータ構造上 1 block に結合してはいない（`services/dashboard/src/components/transcript/transcript-viewer.tsx:239-255`）。実害は、group key が `seg.speaker || ""` なので未命名 sub-cluster が同じ空 key 扱いになり、speaker filter、色、連続 speaker header 判定、speaker list が潰れること（`services/dashboard/src/components/transcript/transcript-viewer.tsx:227-235`, `services/dashboard/src/components/transcript/transcript-viewer.tsx:261-264`, `services/dashboard/src/components/transcript/transcript-viewer.tsx:1111-1123`）。

- FC-10: UI 変更リストは不足。group key を `seg.speaker || seg.speaker_cluster || ""` にするだけだと、synthetic segment の `speaker` が raw cluster id 表示になり得る（`services/dashboard/src/components/transcript/transcript-viewer.tsx:1072-1088`）、実際の表示は `segment.speaker` をそのまま出す（`services/dashboard/src/components/transcript/transcript-segment.tsx:187-201`）。また `speakerOrder` は今も `segment.speaker` だけを見るため、filter/list 側は cluster fallback されない（`services/dashboard/src/components/transcript/transcript-viewer.tsx:227-237`）。

- FC-11: DOM speaking interval で diarization を constrain しない判断は、現コード前提では正直で妥当。laneKey は `sha1(track.id).slice(0, 10)` で、lane registry も `track.id -> lane`（`services/vexa-bot/core/src/utils/browser.ts:632-643`, `services/vexa-bot/core/src/utils/browser.ts:798-838`）。保存 metadata には `lane_id` として track.id が入る（`services/vexa-bot/core/src/services/recording.ts:8-17`, `services/meeting-api/meeting_api/recordings.py:363-383`, `services/meeting-api/meeting_api/recordings.py:550-562`）。一方 Google Meet speaker event は DOM participant id/name だけで、track id を持たない（`services/vexa-bot/core/src/platforms/googlemeet/recording.ts:287-301`, `services/vexa-bot/core/src/platforms/googlemeet/recording.ts:392-407`）。`lane_label` も `closest("[data-participant-id]")` に依存し、null を許す best-effort 実装（`services/vexa-bot/core/src/utils/browser.ts:703-727`）。

- FC-12: 安定性フィルタは cost/latency 削減策ではない。lane path は `_call_transcription_service()` 後に `_parse_segments()`、`_apply_lane_identity()`、`_shift_segment_times()` を実行する順番なので、filter を入れても Soniox 呼び出し数・音声長・課金対象は変わらない（`services/meeting-api/meeting_api/final_transcription.py:451-463`）。Soniox adapter も file upload/create/poll/transcript fetch を完了してから token を fold する（`services/transcription-service/soniox_adapter.py:179-231`）。追加 CPU は segment 集計だけなので小さいが、計画では「誤分割抑制」であり「STT cost 抑制」ではないと明記した方がいい。

## Adopted-Recommended Changes

- ARC-1: K>1 lane で DOM vote 名を破棄する方針は採用でよい。現状の DOM vote は `_parse_segments()` の共通処理で必ず先に入るため、Phase 3 の責務として `_apply_lane_identity()` の multi-cluster branch で `speaker` を `None` に戻すのが最小差分（`services/meeting-api/meeting_api/final_transcription.py:734-747`, `services/meeting-api/meeting_api/final_transcription.py:327-338`）。

- ARC-2: `token_count` は adapter contract の optional additive field として追加する。実装は `fold_tokens_to_segments()` の run 作成・継続箇所で count を持つのが自然（`services/transcription-service/soniox_adapter.py:83-95`）。あわせて golden response、`test_golden_fixture_replay_is_deterministic()`、contract README、adapter manifest を更新する（`services/transcription-service/tests/test_soniox_adapter.py:99-107`, `contracts/stt/v1/README.md:16-30`, `.pipeline/adapters/soniox-stt.adapter.json:44-53`）。

- ARC-3: `speaker_mapping_status="needs_review"` は DB column 追加ではなく read-time derivation が妥当。`Transcription` model は既に large-table で speaker_cluster index も online_only とされており、status column を足すより PG read branch で `speaker_cluster` と `speaker` から導出する方が局所的（`services/meeting-api/meeting_api/models.py:75-96`, `services/meeting-api/meeting_api/collector/endpoints.py:264-285`）。

- ARC-4: dashboard は key fallback だけでなく、REST mapper、speaker identity helper、表示 label、badge の伝搬を一括で直すべき。`vexaAPI.getMeetingDetails()` が status を落としているため、ここを直さないと deferred の needs_review は UI に届かない（`services/dashboard/src/lib/api.ts:173-238`）。`TranscriptSegment` component は現在 status prop を見ていないので、badge を出すなら synthetic segment に status を含めるか component props を拡張する必要がある（`services/dashboard/src/components/transcript/transcript-viewer.tsx:1072-1088`, `services/dashboard/src/components/transcript/transcript-segment.tsx:143-221`）。

- ARC-5: Correction API は payload 変更なしで進める。`buildSpeakerRename()` は `speaker_cluster` を優先して `from_cluster` を作るため、未命名でも cluster があれば一括 rename payload を作れる（`services/dashboard/src/lib/speaker-edit.ts:8-22`）。API 側も cluster 文字列に形式制約が無い（`services/meeting-api/meeting_api/meetings.py:1997-2021`, `services/meeting-api/meeting_api/meetings.py:2097-2119`）。

- ARC-6: 既存 lane path の all-or-nothing と BUG-002 offset 補正は維持する。lane failure は `LaneTranscriptionFallback` で lane path 全体を捨てる設計（`services/meeting-api/meeting_api/final_transcription.py:233-238`, `services/meeting-api/meeting_api/final_transcription.py:1013-1037`）。offset は speaker_events を lane-local にずらしてから parse し、segment times を master timeline に戻している（`services/meeting-api/meeting_api/final_transcription.py:346-374`, `services/meeting-api/meeting_api/final_transcription.py:451-463`）。安定性フィルタはこの順序を壊さず、例外で mixed fallback を誘発しない実装にする。

- ARC-7: 追加テストは計画より少し広げる。最低限、K>1 で `speaker is None`、DOM events を変えても `speaker` が変わらない、短 token/短 duration cluster が K>1 を作らない、colon cluster key rename が API と saved correction で通る、REST mapper が `speaker_mapping_status` を保持する、dashboard の fallback key が filter/header/display を壊さない、golden fixture が `token_count` 付きで一致する、を入れるべき（根拠: current tests は DOM vote を期待している `services/meeting-api/tests/test_final_transcription_lanes.py:245-295`、Soniox golden は完全一致 `services/transcription-service/tests/test_soniox_adapter.py:99-107`、REST mapper は status 欠落 `services/dashboard/src/lib/api.ts:173-238`）。

## Rejected-Counterpoints

- RC-1: `speaker_mapping_status` のための DB migration は不要。schema/type には既に field があり、DB model には column が無い（`services/meeting-api/meeting_api/schemas.py:1059`, `services/dashboard/src/types/vexa.ts:58-80`, `services/meeting-api/meeting_api/models.py:63-96`）。Phase 3 の needs_review は `speaker_cluster` と空/Unknown speaker から導出できるため、large-table migration を増やす理由が弱い。

- RC-2: PATCH `/transcripts/speakers` の payload 拡張は不要。既存の `rename` は `from_cluster`、`merge` は `clusters`、`reassign` は `segment_ids` と optional `to_cluster` で動く（`services/meeting-api/meeting_api/meetings.py:1997-2021`）。dashboard helper も cluster 優先 rename を既に作る（`services/dashboard/src/lib/speaker-edit.ts:8-22`）。

- RC-3: 現データだけで DOM speaking interval constraint を実装する案は却下でよい。lane side は track id、speaker event side は DOM participant id/name で、両者をつなぐサーバ保存済み stable key が無い（`services/vexa-bot/core/src/utils/browser.ts:805-838`, `services/vexa-bot/core/src/platforms/googlemeet/recording.ts:392-407`, `services/meeting-api/meeting_api/recordings.py:550-562`）。`lane_label` と `participant_name` の文字列一致は補助ヒントにはなるが、AC5 の「DOMを sub-speaker 正解にしない」と衝突する。

- RC-4: 「dashboard で複数 unnamed sub-cluster が 1 group に merge される」という表現は退ける。現コードは 1 segment = 1 group で、実際の問題は同じ空 key による identity collapse（filter/header/color/list）である（`services/dashboard/src/components/transcript/transcript-viewer.tsx:239-255`, `services/dashboard/src/components/transcript/transcript-viewer.tsx:227-235`, `services/dashboard/src/components/transcript/transcript-viewer.tsx:1111-1123`）。

## Blockers

- B-1: 計画の dashboard 変更対象に `services/dashboard/src/lib/api.ts` が入っていないのは AC の blocker。backend で `speaker_mapping_status` を導出しても、REST mapper が field を捨てるため deferred transcript 画面に `needs_review` が届かない（`services/dashboard/src/lib/api.ts:173-238`）。Phase 3 の UI バッジ要件を満たすにはここを変更対象に追加する必要がある。

- B-2: 安定性フィルタの「不安定 cluster を最も時間重複の大きい安定 cluster へ吸収」が未定義。Soniox fold は speaker change/gap で別 run を作るため、通常の turn-taking では不安定 segment と安定 segment が時間重複しない可能性が高い（`services/transcription-service/soniox_adapter.py:83-95`）。全 cluster が閾値未満の場合、吸収先も存在しない。zero-overlap tie、no-stable-cluster、unclustered segment の扱いを実装前に決める必要がある。

- B-3: merge/reassign の replace 後 semantics を AC に含めるなら現行 API では不足。merge は即時には代表 cluster へ更新するが、replace 再適用は name だけで `speaker_cluster` を代表へ戻さない（`services/meeting-api/meeting_api/meetings.py:2122-2155`, `services/meeting-api/meeting_api/final_transcription.py:1124-1129`）。reassign は saved corrections に再適用可能な segment map を保存していない（`services/meeting-api/meeting_api/meetings.py:2157-2179`）。AC2 を rename に限定するなら blocker ではないが、merge/reassign まで保証する文面は修正が必要。

- B-4: UI key fallback だけでは raw cluster id が話者名として表示される恐れがある。`syntheticSegment.speaker` は group key をそのまま使い、`TranscriptSegment` は `segment.speaker` をそのまま描画する（`services/dashboard/src/components/transcript/transcript-viewer.tsx:1072-1088`, `services/dashboard/src/components/transcript/transcript-segment.tsx:187-201`）。identity key と display label を分ける設計が必要。

## Needs-Human

- NH-1: `LANE_SHARED_MIC_MIN_CLUSTER_DURATION_S=2.0`、`LANE_SHARED_MIC_MIN_CLUSTER_TOKENS=5` の初期値は実データでのチューニングが必要。既存 code には lane-level cluster duration/token 統計が無く、Soniox adapter も今は token_count を出していない（`services/transcription-service/soniox_adapter.py:56-119`, `services/meeting-api/meeting_api/final_transcription.py:327-338`）。

- NH-2: needs_review の表示名を決める必要がある。`speaker=None` を dashboard では空表示にするのか、「要確認」「未命名」などの display label にするのか、raw `lane:{key}:{cluster}` を出すのかで UX が変わる。現 UI は `segment.speaker || ""` を表示するだけ（`services/dashboard/src/components/transcript/transcript-segment.tsx:187-201`）。

- NH-3: export/share に needs_review を含めるかは product 判断が必要。dashboard JSON export は `speaker_mapping_status` と `speaker_cluster` を出していない（`services/dashboard/src/lib/export.ts:98-122`）。API Gateway public share は `[timestamp] speaker: text` の plain text だけを生成する（`services/api-gateway/main.py:945-964`, `services/api-gateway/main.py:1056-1074`）。

- NH-4: 将来 DOM interval constraint を本当にやるなら、bot 側で speaker_events に track id / lane id 相当を載せる設計が必要。live transcript segment 側には track identity を保持する経路があるが（`services/vexa-bot/core/src/index.ts:216-223`, `services/vexa-bot/core/src/index.ts:2073-2096`）、meeting.data の terminal `speaker_events` には現状それが入っていない（`services/vexa-bot/core/src/platforms/googlemeet/recording.ts:392-407`, `services/meeting-api/meeting_api/callbacks.py:860-863`）。
