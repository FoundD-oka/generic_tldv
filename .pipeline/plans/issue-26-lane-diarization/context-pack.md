# Context Pack — Issue #26 (Phase 3: 同室共有マイク検出 + サブ話者命名)

> エピック: `.pipeline/plans/speaker-attribution-voiceprint/plan.md`（Phase 3節）
> 前提サブプラン: `.pipeline/plans/issue-25-audio-lanes/plan.md`（Phase 2、PR #33でmain merge済み）
> Issue: https://github.com/FoundD-oka/generic_tldv/issues/26

## 1. Phase 2マルチクラスタ分岐の現状動作（正確な読み）

`services/meeting-api/meeting_api/final_transcription.py`:

- `_transcribe_lanes`（:377-504）は各レーンの音声を丸ごと `_call_transcription_service` に渡し、
  `_parse_segments`（:710-750）を呼ぶ。`_parse_segments` は tx結果に `speaker`（Soniox acoustic
  cluster id）があれば `has_clusters=True` として **`name_clusters_by_dom_vote` を必ず実行し、
  クラスタ単位でDOM重複秒数の重み付き投票により `seg["speaker"]` を確定** する（レーンか
  mixed masterかを区別しない共通コード）。
- その後 `_apply_lane_identity`（:314-343）がレーン単位の後処理を行う:
  - `len(clusters) <= 1`（レーン内クラスタが1個以下）→ 単独レーン: `speaker_cluster` を
    `"lane:{laneKey}"` に上書きし、`lane_label` があれば `speaker` もそれで上書き。
  - `len(clusters) > 1` → **共有マイク相当**: 各クラスタ id を `"lane:{laneKey}:{cluster}"` に
    namespace するだけで、`speaker`（名前）は直前の DOM投票の結果のまま **変更しない**。
- つまり **今のコードは既にサブクラスタを `lane:{key}:{cluster}` として保存する構造を持つが、
  命名は DOM投票に委ねている** ＝ issue #26 の AC5（DOMをサブ話者の正解に使わない）に反する。
  ここが Phase 3 で変える必要のある「挙動変更点」。
- **安定性フィルタなし**: `clusters = {s.get("speaker_cluster") for s in segments if ...}` は
  最小継続時間・最小トークン数の足切りを一切行わない。1トークンの誤爆クラスタが1つあるだけで
  即 K>1（共有マイク判定）になる ＝ issue の AC3（false-split guard）が未実装。
- **既存テストなし**: `test_final_transcription_lanes.py` にマルチクラスタ分岐を検証するテストは
  存在しない（grep確認）。実装はあるが未検証の分岐。

## 2. Soniox adapter（`services/transcription-service/soniox_adapter.py`）の能力と限界

- 非同期API（`stt-async-v5`, `enable_speaker_diarization=true`）のみ。トークン単位の
  `speaker`（数値→文字列cluster id）を返し、`fold_tokens_to_segments` が「話者変化 or
  無音ギャップ > `SONIOX_SEGMENT_MAX_GAP_S`(既定1.0s)」でセグメント分割する。
- **クラスタ数上限15、クラスタidはファイル内限定の匿名id**（アダプタ契約
  `.pipeline/adapters/soniox-stt.adapter.json` の `forbidden_claims` に
  「cluster ids are stable across meetings」と明記——ファイル間で安定性の保証なし）。
- **区間制約API無し**: リクエストボディは `file_id`/`model`/`enable_speaker_diarization`/
  `language_hints` のみ。DOM発話区間で「この時間帯だけdiarizeして」と指示する仕組みはSoniox側
  に存在しない。区間限定をしたければ音声ファイル自体を事前に切り出すしかない。
- **トークン数はセグメントに残らない**: `fold_tokens_to_segments` はセグメントに
  `start/end/text/speaker` のみを残し、トークン件数を保持しない。AC3の「最小トークン数」足切りを
  厳密にやるなら、fold時に `token_count` フィールドを追加する小さな契約拡張が必要（既存の
  `speaker` 追加と同じ非破壊的パターンで対応可能）。

## 3. 訂正API（PATCH `/meetings/{id}/transcripts/speakers`）が今できること／できないこと

`services/meeting-api/meeting_api/meetings.py:2032-2216`:

- `rename`（`from_cluster` or `from_name` → `to_name`）、`merge`（複数cluster→1名+代表cluster）、
  `reassign`（`segment_ids` → 名前/cluster）はすべて `Transcription.speaker_cluster` の
  **文字列値**に対して汎用的に動作する。フォーマット（`lane:{key}:{cluster}` のようなコロン区切り）
  への依存は一切ない ＝ **Phase 3のサブクラスタ命名（AC2「1回命名→全反映」）は既存APIを
  1行も変えずに満たせる**。
- 補正は `meeting.data.speaker_corrections.clusters` に永続化され、`mode="replace"` 再実行後も
  `_saved_cluster_corrections`（:795-813）で再適用される——サブクラスタの命名もこの仕組みに
  そのまま乗る。
- ダッシュボード `services/dashboard/src/lib/speaker-edit.ts` の `buildSpeakerRename` /
  `buildSpeakerMerge` は既に `segment.speaker_cluster` を優先して使う実装——変更不要。
- **できないこと**: 「要確認」状態そのものの概念が無い。訂正APIは常に `speaker` を上書きするだけで、
  未命名状態のマーキング／解除のロジックを持たない（下記4節）。

## 4. 「要確認」状態 — 今あるもの / 必要になるもの

- DBスキーマ（`models.py` `Transcription`）に needs-review系のカラムは無い
  （`speaker`, `speaker_cluster`, `speaker_auto` のみ）。
- **しかし `speaker_mapping_status: Optional[str]` は既にAPIスキーマとダッシュボード型に
  存在する**（`schemas.py:1059` `TranscriptionSegment`、`dashboard/src/types/vexa.ts:70,148`）。
  現状はリアルタイム収集経路（`collector/processors.py:278,352`、値`"PRODUCER_LABELED"`）でのみ
  Redis生segmentに書かれ、**deferred/DB経路（`collector/endpoints.py:_get_full_transcript_segments`
  :264-285）ではPG由来segmentに一切セットされずNoneのまま**（読み出しコードに書き忘れではなく、
  そもそも用途が想定されていない）。
- → **マイグレーション不要の最小実装**: `_get_full_transcript_segments` のPG分岐で、
  `speaker_cluster` が `lane:{key}:{subcluster}` パターン（コロン2つ）かつ `speaker` が
  空/Unknownのときに `speaker_mapping_status="needs_review"` を**読み出し時に導出**する。
  人が命名すると `speaker` が非Noneになり条件が自動的に外れる＝状態遷移が追加コード無しで閉じる。
  新規カラムを追加する案（`Transcription.needs_review` bool）は507K行テーブルへのオンライン
  マイグレーションが必要になり、Phase 1の`speaker_cluster`追加と同種のコストがかかるため非推奨。
- **ダッシュボードのグルーピングに要注意な既存ギャップ**: `transcript-viewer.tsx:247`
  `key: seg.speaker || ""` — speakerが空文字/nullの segment は **speaker_cluster を無視して
  すべて同じ空キーにグルーピングされる**。2人分のサブクラスタがどちらも未命名（speaker=null）
  だと、UI上は1つの塊に混ざって表示され、クラスタ別に個別命名するUXが成立しない。
  （`group.key.startsWith("__unmapped_")` という分岐が1081/1111行にあるが、実際の
  grouping keyはそれを生成しておらず死んだ分岐——現状のgroupingは常に`seg.speaker || ""`）。
  Phase 3のUI変更で `key: seg.speaker || seg.speaker_cluster || ""` へのフォールバックが必要。

## 5. タイル↔レーン相関の選択肢と信頼性

- **実装済みのlaneKey生成**（`browser.ts:805`）は `sha1(track.id).slice(0,10)` —
  **MediaStreamTrack.id 由来**（issue-25 plan.md記載の「participantId由来」という設計意図とは
  実装が異なる：tile消失対策で意図的にtrack.id軸に変更されている）。
- **speaker_events**（`googlemeet/recording.ts:sendGoogleSpeakerEvent`、:392-408）の
  `participant_id` は `getGoogleParticipantId()`（data-participant-id → jsinstance →
  ランダム `gm-id-*`）——**参加者タイルDOM要素**由来。laneKeyとは全く別のid空間で、
  ハッシュ関係も一切ない。
- 唯一の橋渡しは `lane_label`（`browser.ts:laneLabelForElement`, :703-727）——キャプチャした
  **audio要素**から `el.closest('[data-participant-id]')` でタイル祖先を辿ってDOM表示名を読む
  best-effort文字列。**BUG-020（confirmed, medium, `.pipeline/evidence/issue-25-audio-lanes/
  tribunal-report.json`）**: Google MeetのリモートオーディオはタイルにネストされないDOM直下の
  `<audio>` プールが一般的で、`closest` は通常null——solo-lane自動命名の前提が実機で崩れやすい。
- **相関オプションの評価**:
  1. `laneKey` と `participant_id` の直接対応 — **不可**（ハッシュ元が違う、id空間が無関係）。
  2. `lane_label`（DOM名文字列）と `speaker_events[].participant_name`（DOM名文字列）の
     ファジーマッチ — **弱い**。両者ともDOMスクレイピングのbest-effortで、`lane_label`は
     BUG-020で頻繁にnullになり、命名ロジックの正規化経路も別。補助ヒントとしてのみ使え、
     必須の判定材料にはできない。
  3. レーンをDOM発話区間そのものに時間的にマスクする仕組み — **存在しない**。レーンが持つのは
     mixed timelineとの `start_offset_seconds`（BUG-002対応、レーン開始オフセットのみ）で、
     参加者ごとの発話区間との紐付けはコード上どこにも実装されていない。
- **結論**: issue本文が指示する「タイルのDOM発話区間に制約してdiarize」はサーバ側で信頼できる
  形で実装できない。正直な代替は「レーン全体を無制約でdiarize」（Phase 2が既にやっている）＋
  DOMはタイル単位ラベル（`lane_label`）としてのみ、単独レーンの自動命名にオプションで使う
  （Phase 2 solo分岐が既にそう）。サブ話者命名には一切使わない。
