# Plan — Issue #26: 同室共有マイクの検出とサブ話者命名（Phase 3）

> エピック: `.pipeline/plans/speaker-attribution-voiceprint/plan.md`（Phase 3節）
> 前提: Phase 2（issue #25, PR #33）がmainにmerge済み。本ブランチはそのmainから分岐。
> Codex Plan Critic: `.pipeline/evidence/issue-26-lane-diarization/codex-plan-critique.md`（v2で反映済み）
> Issue: https://github.com/FoundD-oka/generic_tldv/issues/26
> 状態: **DRAFT（計画のみ、製品コード未変更）**
> Rev: v2（2026-07-10、Codex批判反映）

## Request

- Source: GitHub issue #26（エピックPhase 3）
- 目的: レーン単位のdeferred STTが手に入った状態（Phase 2）を前提に、1レーン内に複数の
  声（=同室で1マイクを共有する複数人）が混在するケースを検出し、DOMでは分離できないその
  サブ話者を「要確認」として提示、1回の命名で全発話に反映できるようにする。

## Context Pack

詳細: `.pipeline/plans/issue-26-lane-diarization/context-pack.md`（批判のFC-1/3/5/11で事実確認済み）。

- `_apply_lane_identity`（`final_transcription.py:314-343`）は既にマルチクラスタレーンを
  `lane:{laneKey}:{cluster}` にnamespaceするが、命名は`_parse_segments`内の
  `name_clusters_by_dom_vote`（DOM重複秒投票、共通コード）に委ねたまま——issueのAC5（DOMを
  サブ話者の正解に使わない）と矛盾する、Phase 2からの意図的な挙動変更点。
- 安定性フィルタが皆無（1トークンの誤爆でK>1化）——AC3のfalse-split guardが未実装。
- 訂正API（PATCH `/transcripts/speakers`）のrenameは`speaker_cluster`の文字列値に汎用的に
  動作し、`lane:{key}:{cluster}`形式にも変更不要（AC2はrenameに限定すれば満たせる）。ただし
  merge/reassignの`mode=replace`再実行後semanticsは現行APIでは保証できない（後述）。
- `speaker_mapping_status`はAPIスキーマ・ダッシュボード型に既に存在するが、deferred/DB経路の
  読み出し（`collector/endpoints.py`）でもREST mapper（`dashboard/src/lib/api.ts`）でも
  伝搬していない——両方を直さないとUIに届かない。
- ダッシュボードのグルーピングは「複数segmentの結合」ではなく、`seg.speaker || ""`が空文字に
  縮退することで起きる**identityの潰れ**（filter/色/連続header判定/話者一覧が同じ空keyに集約）。
- DOM発話区間でのdiarization区間制約はサーバ側で信頼できる形で実装不可（tile↔レーンのid空間
  無関係、`lane_label`はBUG-020で頻繁にnull）——issue本文の実装指示①は不採用、無制約の
  全レーンdiarizeを継続する。

## 設計

### 方針: 挙動変更点を明示した最小差分

Phase 2の共通コード（`_parse_segments`, `name_clusters_by_dom_vote`, Soniox adapter呼び出し）は
変更しない。変更点は`_apply_lane_identity`前後のレーン後処理に閉じる。**mixed-master経路
（レーン非使用の混合音声diarization）のDOM投票は無変更**（FC-2）。

1. **DOM区間制約は不採用**（設計判断、TODOではない）。DOMは`lane_label`（Phase 2の単独レーン
   自動命名）としてのみ残す。サブ話者の正解には一切使わない（AC5）。
2. **安定性フィルタ（AC3 false-split guard）**: Soniox adapterに非破壊的フィールド
   `token_count`（fold時のトークン件数、`speaker`追加と同じ拡張パターン）を追加。契約変更面は
   golden fixture（`contracts/stt/v1/examples/golden-2-diarization.response.json`）、
   `test_golden_fixture_replay_is_deterministic`、`contracts/stt/v1/README.md`（optional
   additive field記載）、`.pipeline/adapters/soniox-stt.adapter.json`（`validation.checks`）の
   4点セット——これが変更面から欠けていた（批判FC-4/ARC-2）。`_stable_lane_clusters(segments,
   min_duration_s, min_tokens)`を新設し、クラスタごとの総発話時間・総トークン数
   （`token_count`欠損時は語数近似）を集計、閾値（`LANE_SHARED_MIC_MIN_CLUSTER_DURATION_S`
   既定2.0秒、`LANE_SHARED_MIC_MIN_CLUSTER_TOKENS`既定5）以上を「安定クラスタ」とする。
3. **不安定クラスタの扱い（B-2の明確化、AC4の境界劣化の解決）**: 「最も時間重複の大きい安定
   クラスタへ吸収」という未定義の吸収ロジックは採用しない。**K = 安定クラスタの数のみ**で数え、
   不安定クラスタは決して他クラスタへ黙って吸収しない：
   - K_stable <= 1（0または1個、全クラスタが閾値未満の場合を含む）: **単独レーン扱い**。
     不安定クラスタの発話も含め全segmentがレーンラベル（`speaker_cluster="lane:{laneKey}"`、
     `lane_label`があれば`speaker`もそれで上書き）を取る——Phase 2 solo挙動と一致、
     リスクは閾値でバウンドされる。
   - K_stable >= 2（共有マイク）: 各安定クラスタを`lane:{laneKey}:{cluster}`にnamespace。
     不安定クラスタも**吸収せず自分のサブクラスタidを維持**し、同様に`needs_review`対象とする
     （未定義の吸収先選択ロジックを排除）。
4. **命名破棄はK>1（共有マイク）分岐でのみ**（ARC-1、FC-2）: `_apply_lane_identity`の
   multi-cluster branchで`seg["speaker"]`を**保存処理に渡す前・`speaker_auto`キャプチャの前に**
   `None`へ上書きする。`speaker_auto = seg.get("speaker")`は`_apply_lane_identity`後に走る
   （`final_transcription.py:1122-1129`）ため、この順序でDOM投票名が`speaker_auto`にも
   残らない。**mixed-master path（レーン非使用時）のDOM投票結果は無変更**。単独レーン
   （K_stable<=1）分岐はDOM投票を破棄せず`lane_label`のみ使う（Phase 2と同じ）。
5. **要確認フラグ（マイグレーション無し、B-1で変更面を訂正）**: (a) `collector/endpoints.py:
   _get_full_transcript_segments`のPGセグメント構築分岐に、`speaker_cluster`が
   `^lane:[^:]+:.+$`（サブクラスタ形式）かつ`speaker`が空/Unknownの場合に
   `speaker_mapping_status="needs_review"`を読み出し時に導出。(b) **これだけではUIに届かない**
   （FC-8）——`services/dashboard/src/lib/api.ts`のREST mapper（`RawSegment`→
   `TranscriptSegment`）に`speaker_mapping_status`を追加で通す。websocket経路は既に伝搬済みの
   ため対象外。新規DBカラムは追加しない。
6. **命名反映（AC2はrenameに限定、B-3/FC-6）**: 既存のPhase 1b`PATCH /transcripts/speakers`の
   `rename`（`from_cluster=lane:{key}:{cluster}`）をそのまま使う。バックエンド変更ゼロ。
   **merge/reassignの`mode=replace`再実行後semantics（代表clusterへの再集約、segment単位の
   再適用）は現行APIの構造的限界でAC2の対象外**——将来issueで扱う（Out of Scope参照）。
7. **ダッシュボード（B-4/ARC-4で識別キーと表示ラベルを分離）**:
   - identity/groupingキー: `seg.speaker || seg.speaker_cluster || ""`にフォールバック
     （filter・色・連続speaker header判定・話者一覧の全箇所で同じキーを使い、identityの
     潰れ（FC-9/RC-4: segment結合ではなく空keyへの縮退）を解消）。
   - **表示ラベルはidentityキーと別**（B-4）: `needs_review`のサブクラスタは生の
     `lane:{key}:{cluster}`を絶対に表示しない。日本語ラベル「要確認の話者」＋クラスタ出現順の
     短い添字（A/B…）を表示名として使う。
   - `speaker_mapping_status === "needs_review"`のグループに「要確認」バッジを表示し、
     既存の話者リネームUI（`buildSpeakerRename`）をそのまま起動できるようにする。
8. **不変条件の維持（ARC-6）**: 既存のall-or-nothingフォールバック（`LaneTranscriptionFallback`）
   とBUG-002対応のoffset順序（speaker_eventsをlane-localにずらして`_parse_segments`→
   `_apply_lane_identity`→`_shift_segment_times`でmaster timelineへ戻す順）は変更しない。
   安定性フィルタはこの順序の**後**に挿入し、例外を`LaneTranscriptionFallback`へ伝播させない
   （フィルタ内部で例外を握り単独レーン扱いにフォールバックする）。
9. **AC4（短発話・同時発話のバウンドされた劣化）**: 安定性フィルタは**誤分割の抑制**であり、
   STTのコスト・レイテンシ削減策ではない（FC-12: レーン全体音声は既にSoniox呼び出し済みで
   課金対象は不変）。同時発話はSoniox側がトークンを時系列１本のストリームで返す構造的制約が
   あり、meeting-api層では解決できない——「絶対に黙って誤った名前を当てない」不変条件
   （K_stable>=2分岐で`speaker=None`固定、不安定クラスタも吸収しない）で境界を持たせる
   （上記3節参照、B-2の解決がAC4のバウンド劣化仕様）。

## 変更対象

- `services/transcription-service/soniox_adapter.py` — `fold_tokens_to_segments`に
  `token_count`追加（後方互換）。
- 契約セット: `contracts/stt/v1/examples/golden-2-diarization.response.json`（`token_count`
  追加）、`services/transcription-service/tests/test_soniox_adapter.py`
  （`test_golden_fixture_replay_is_deterministic`更新）、`contracts/stt/v1/README.md`
  （optional additive field記載）、`.pipeline/adapters/soniox-stt.adapter.json`
  （`validation.checks`更新）。
- `services/meeting-api/meeting_api/final_transcription.py` — `_stable_lane_clusters`新設、
  `_apply_lane_identity`のK_stable分岐（不安定クラスタの非吸収セマンティクス含む）、
  `speaker`/`speaker_auto`双方への`None`上書き順序、関連env変数。
- `services/meeting-api/meeting_api/collector/endpoints.py` —
  `_get_full_transcript_segments`に`speaker_mapping_status="needs_review"`の読み出し時導出。
- `services/dashboard/src/lib/api.ts` — `RawSegment`→`TranscriptSegment`マッピングに
  `speaker_mapping_status`を追加（B-1で追加、Phase 2 changeset漏れの修正）。
- `services/dashboard/src/components/transcript/transcript-viewer.tsx` /
  `transcript-segment.tsx` — grouping keyフォールバック、identityと表示ラベルの分離
  （「要確認の話者」＋添字、raw cluster idは描画しない）、要確認バッジ表示。
- テスト: `services/meeting-api/tests/test_final_transcription_lanes.py`（ARC-7の7項目、
  下記検証契約参照）、dashboard側のfallback key・表示ラベルテスト。

## Out of Scope

- DOM発話区間でのdiarization区間制約（設計上不採用、上記「設計」1節で根拠明記）。
- 声紋によるサブ話者の自動命名（Phase 4 / issue #27の責務）。
- `Transcription`テーブルへの新規カラム追加（`speaker_mapping_status`は導出のみ、永続化しない）。
- **merge/reassignの`mode=replace`再実行後semantics**（代表clusterへの再集約、segment単位の
  再適用）——AC2はrenameのみを対象とし、現行APIの構造的限界として将来issueに切り出す（B-3）。
- **exports/public shareへの`needs_review`反映**（Phase 3は内部UI限定、非目標として明記。NH-3）。
- Zoom/Teams対応、mixed master経路の変更。
- レーン↔DOM参加者idの構造的な相関の実装（BUG-020の恒久修正は別issue）。

## 検証契約要点

詳細は`.pipeline/plans/issue-26-lane-diarization/verification-contract.md`（本plan v2と同時に
確定）を参照。要点: P3-AC1（2声混在fixtureでK_stable=2・両クラスタが`needs_review`）、
P3-AC2（`from_cluster="lane:{key}:A"` renameが対象クラスタのみに反映、merge/reassignの
永続semanticsは対象外）、P3-AC3（誤爆1発話クラスタを含むfixtureでK_stable<=1に収束、
不安定クラスタも含め全segmentがレーンラベルを取る）、P3-AC4（閾値境界でspeakerが絶対に
None以外にならないこと・不安定クラスタが黙って吸収されないこと）、P3-AC5（DOM
speaker_eventsを変えても`speaker`/`speaker_auto`が変化しないこと、かつmixed-master
fixtureではDOM投票が不変であることの回帰テスト）。

## S/M/L

| Field | Value |
|---|---|
| size | **L**（エピック規定でPhase 3は独立L sub-planとして扱う） |
| reason | Soniox adapter契約拡張（golden fixture含む）、meeting-apiの話者命名ロジックにPhase 2からの意図的な挙動変更（DOM投票の破棄、K_stable判定）を入れる、dashboardのグルーピング・表示ラベル仕様変更（REST mapper含む）を伴う |
| human gate | yes（L policy、AC5の挙動変更は要承認、ハッシュ束縛承認） |
| tribunal | yes（`required_for_l`、特にAC3/AC4の「誤った推測を出さない」不変条件とB-2の不安定クラスタ非吸収セマンティクスの検証） |

## Risks

- Soniox診断の同時発話（オーバーラップ）耐性はmeeting-api層で改善不可。ACはこれを「黙って
  適用しない」でバウンドするのみで、精度自体は保証しない。
- `token_count`のfold追加はgolden fixture・contract README・adapter manifestの3点セット更新を
  要する契約拡張——preflightで整合を確認すること。
- AC5の挙動変更（Phase 2でDOM投票が命名していたマルチクラスタ発話が、Phase 3では
  未命名/要確認になる）は**ユーザー体験の後退に見える可能性**——リリースノートが必要
  （human gate項目）。mixed-master経路は無変更である旨も明記する。
- ダッシュボードのgrouping/表示ラベル変更は既存の単独話者ケースに影響しないことを既存
  スナップショット/テストで確認する必要あり。
- 閾値の初期値（NH-1、下記Codex Plan Critique節参照）は実運用有効化前に実会議PoCで再検討。
- タイル↔レーン相関を実装しない判断は、将来「DOM区間制約」を求める別issueの再提起リスクを
  残す——BUG-020の恒久修正が前提解消の唯一の経路であることをcontext-packに明記済み。

## 実装順

1. Soniox adapter: `token_count`追加＋golden fixture更新＋contract README/adapter manifest
   更新＋後方互換テスト。
2. `final_transcription.py`: `_stable_lane_clusters`＋K_stable分岐（不安定クラスタ非吸収）＋
   `speaker`/`speaker_auto`双方の破棄順序＋不変条件（all-or-nothing/offset順序）維持の確認。
3. `collector/endpoints.py`: `speaker_mapping_status="needs_review"`読み出し時導出。
4. `dashboard/src/lib/api.ts`: REST mapperに`speaker_mapping_status`を追加。
5. dashboard UI: grouping keyフォールバック＋表示ラベル分離（「要確認の話者」＋添字）＋
   要確認バッジ（既存rename UIの流用）。
6. 統合fixture（2声混在レーン／単独レーン誤分割なし／閾値境界／mixed-master不変）＋
   GitNexus impact/detect-changes＋tribunal（AC3/AC4/AC5＋B-2セマンティクス中心）＋
   evidence pack＋human承認（AC5挙動変更の説明を含む）。

## Codex Plan Critique

- 批判ファイル: `.pipeline/evidence/issue-26-lane-diarization/codex-plan-critique.md`
- 採用した指摘: ARC-1（K>1でDOM投票破棄、multi-cluster branch限定）、ARC-2（`token_count`を
  optional additive fieldとし、golden fixture/README/adapter manifest/contractテストを変更面に
  追加）、ARC-3（`speaker_mapping_status`はDBカラム追加ではなくread-time derivation）、
  ARC-4（dashboardはkey fallbackだけでなくREST mapper・表示ラベル・バッジ伝搬まで一括修正）、
  ARC-5（Correction APIはpayload変更なしで進める）、ARC-6（all-or-nothingフォールバックと
  BUG-002 offset順序を不変条件として維持）、ARC-7（テストを7項目に拡張）。B-1〜B-4は設計節に
  解決策を明記（B-1: api.ts追加、B-2: K_stable限定＋不安定クラスタ非吸収、B-3: AC2をrenameに
  限定、B-4: identityキーと表示ラベルの分離）。
- 訂正として採用したcounterpoint: RC-1（DB migration不要）、RC-2（PATCH payload拡張不要）、
  RC-3（DOM speaking interval constraintは現データ前提で却下）、RC-4（dashboard bugは
  segment結合ではなく空keyへのidentity collapse——本plan全体でこの表現に統一）。
- Needs-Human: NH-1（閾値2.0秒/5トークンは初期値、実会議PoCでチューニング、Risks参照）。
  NH-2（表示ラベルは「要確認の話者」＋添字A/Bで本v2暫定解決、ユーザー確認待ち）。
  NH-3（export/public shareへの`needs_review`反映はPhase 3非目標として確定、Out of Scope
  に明記）。NH-4（speaker_eventsへのtrack id/lane id付与はDOM区間制約を将来やる場合の前提、
  別issueで扱う——Out of Scopeの「レーン↔DOM参加者id相関」と同根として記録）。
