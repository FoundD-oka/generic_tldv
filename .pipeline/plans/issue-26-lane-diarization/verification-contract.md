# Verification Contract — issue-26-lane-diarization

- size: L（sml-decision.json 参照）
- external consultation: claude-fable-cli（optional）
- tribunal: required（L policy）
- Rev: v1（2026-07-10、plan v2＝Codex批判反映と同時に確定）

## Must Pass（deterministic）

### 0. 批判由来の追加基準
- golden fixture: `contracts/stt/v1/examples/golden-2-diarization.response.json`に
  `token_count`を追加した状態で`test_golden_fixture_replay_is_deterministic`が完全一致で
  pass（`build_verbose_json_response()`出力とgolden responseのバイト単位一致）。
- `contracts/stt/v1/README.md`が`token_count`をoptional additive fieldとして記載し、
  `.pipeline/adapters/soniox-stt.adapter.json`の`validation.checks`が更新後golden fixtureと
  整合すること（adapter-contract gateでの検証）。
- REST mapper: `services/dashboard/src/lib/api.ts`の`RawSegment`→`TranscriptSegment`
  マッピングが`speaker_mapping_status`を保持する（deferred/PG経由の会議データで
  `needs_review`がdashboard型まで届くfixtureテスト）。
- **生のcluster idが話者名として画面に出ないこと**: `needs_review`の全経路
  （grouping/header/speaker-list/badge/`transcript-segment.tsx`描画）で
  `lane:{key}:{cluster}`形式の文字列がそのまま表示されないことをスナップショット/DOM
  アサートで確認。
- K計数は安定クラスタのみ: `_stable_lane_clusters`が閾値未満のクラスタを`K`から除外し、
  かつ「最も時間重複の大きい安定クラスタへの吸収」を行わない（不安定クラスタが独自の
  sub-cluster idを保持し続けるfixtureテスト）。K_stable<=1（全クラスタ閾値未満を含む）は
  単独レーン扱い、K_stable>=2は不安定クラスタも`needs_review`対象として維持。
- mixed-master（レーン非使用）経路のDOM投票が無変更: multi-cluster lane分岐の
  `speaker=None`上書きロジックが`_apply_lane_identity`のlane専用コードパスにのみ効き、
  mixed-master diarization結果のfixtureはPhase 2から出力が変わらないことの回帰テスト。
- all-or-nothing＋offset順序の不変条件回帰: 1レーンSTT失敗fixtureで
  `LaneTranscriptionFallback`が発火し混合master経路に全面フォールバックすること、および
  安定性フィルタ挿入後もspeaker_eventsのlane-localシフト→`_parse_segments`→
  `_apply_lane_identity`→`_shift_segment_times`の順序・例外非伝播が保たれること
  （既存`test_final_transcription.py`／`test_final_transcription_lanes.py`suite全緑）。

### 1. P3-AC1（共有マイク検出）
- 2声混在の合成fixture（1レーンに2安定クラスタ、閾値以上の発話時間・トークン数）で
  K_stable=2と判定され、両クラスタの`speaker_cluster`が`lane:{laneKey}:{cluster}`に
  namespaceされ、`speaker`/`speaker_auto`が`None`、読み出し時に
  `speaker_mapping_status="needs_review"`が導出されること。

### 2. P3-AC2（命名の一括反映、renameに限定）
- `from_cluster="lane:{key}:A"`のrenameでクラスタAの全発話にのみ反映され、クラスタBは
  不変であること。merge/reassignの`mode=replace`再実行後semanticsはこの契約のアサート
  対象外（Out of Scopeとして明記済み、将来issue）。

### 3. P3-AC3（false-split guard）
- 短い相槌1発話だけの誤爆クラスタを含むfixtureでK_stable<=1に収束し、単独レーン判定
  （`lane_label`があれば全segmentがそれを`speaker`として持つ）を維持すること。誤爆クラスタが
  新規サブクラスタとして数えられないこと。

### 4. P3-AC4（境界のバウンドされた劣化、B-2解決の検証）
- `LANE_SHARED_MIC_MIN_CLUSTER_DURATION_S`/`_TOKENS`ちょうどの境界値fixtureで、
  speakerが推測名を出さず絶対に`None`以外にならないこと（否定的テスト）。
- 全クラスタが閾値未満（no-stable-cluster edge）のfixtureで単独レーン扱いに帰着すること。
- K_stable>=2かつ不安定クラスタが存在するfixtureで、不安定クラスタが安定クラスタへ
  吸収されず自分のsub-cluster idを保持し、`needs_review`対象になること。

### 5. P3-AC5（DOM非依存の回帰）
- マルチクラスタfixtureでDOM speaker_eventsの内容を変えても`speaker`/`speaker_auto`が
  変化しないこと。
- mixed-master（レーン非使用）fixtureではDOM投票結果がPhase 2と同一であること
  （lane専用分岐に閉じていることの回帰確認）。

## Required Commands

- `cd services/meeting-api && PYTHONPATH=. python -m pytest tests -q`（全緑、特に
  `test_final_transcription` / `test_final_transcription_lanes` / `test_speaker_clusters`）
- `cd services/transcription-service && python -m pytest -q`（`test_soniox_adapter`の
  golden fixture一致テスト含む）
- `cd services/dashboard && npx vitest run`（fallback key・identity/表示ラベル分離・
  REST mapperのstatus伝搬テスト）
- `cd services/dashboard && npx tsc --noEmit`（型変更の整合確認）
- `node .gitnexus/run.cjs detect-changes -r generic_tldv`（想定シンボルのみ:
  `_apply_lane_identity` / `_stable_lane_clusters` / `_get_full_transcript_segments` /
  `fold_tokens_to_segments` / dashboard mapper・viewer関連）
- GitNexus `impact`: `_apply_lane_identity` / `name_clusters_by_dom_vote` /
  `fold_tokens_to_segments` / `_get_full_transcript_segments`のupstream影響を実装前に記録

## Evidence Rule

- Evidence は `.pipeline/evidence/issue-26-lane-diarization/` に格納。
- L要件: tribunal report または sidechain synthesis 必須
  （AC3/AC4/AC5＋B-2不安定クラスタ非吸収セマンティクスを重点レビュー）。
- 人間承認（approvals.jsonl、diff hash束縛）なしにPR readyにしない
  （AC5挙動変更の説明を承認材料に含める）。
- 実装エージェントの自己申告をevidenceにしない（テストは独立再実行で記録）。
