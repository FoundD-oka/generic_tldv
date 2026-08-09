# Verification Contract — rf71r-meeting-detail-split

判定の単一実行点は `bash scripts/test/run-refactor-item.sh RF-71R`
(実体: `scripts/test/refactor-checks/RF-71R.sh`。exit 0 = 合格ライン到達)。
以下の表は各検査IDの意味・基準値・単体判定コマンドを定義する。基準値は
2026-08-10 に main `eb7a37d` で実測(転記ではない)。`BASE` は
`.hw/plans/rf71r-meeting-detail-split/base-commit` の値。
`PAGE=services/dashboard/src/app/meetings/[id]/page.tsx`。

## Acceptance Tests

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-001 (C04) | Page 本体 1,200 行以下(着手時 2,502) | `wc -l < "$PAGE"` ≤ 1200 | RF-71R.sh 出力 |
| AT-002 (C05) | Page 内 `setInterval` 0(着手時 2) | `grep -c 'setInterval' "$PAGE"` = 0 | 同上 |
| AT-003 (C06) | Page 内 raw `fetch(` 0(着手時 5) | `grep -c 'fetch(' "$PAGE"` = 0 | 同上 |
| AT-004 (C07a/b) | Page 内 VNC URL 組立 0(着手時 2)。`vnc/vnc.html` を含む src ファイルは高々2(着手時 2: page と browser-session-view) | `grep -c 'vnc/vnc.html' "$PAGE"` = 0 かつ `grep -rl 'vnc/vnc.html' services/dashboard/src \| wc -l` ≤ 2 | 同上 |
| AT-005 (C08) | タイトル保存の実装1本化(着手時: 成功/失敗トースト各4箇所)。`タイトルを更新しました` / `タイトルの更新に失敗しました` の出現が src 全体(meeting-card.tsx 除く)で各1 | `grep -ro '<文言>' services/dashboard/src \| grep -v meeting-card.tsx \| wc -l` = 1 | 同上 |
| AT-006 (C10, C11b) | 抽出ロジックの新規ユニットテスト: `.test.ts` 追加2ファイル以上、テスト総数 271 件以上(着手時 263)・全緑 | `git diff --diff-filter=A --name-only BASE..HEAD -- services/dashboard/tests` と vitest JSON reporter の numPassedTests | 同上 |
| AT-007 (C03) | route と default export の維持 | `grep -q 'export default' "$PAGE"` | 同上 |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 (C11b) | 既存テスト 263 件が1件も落ちない(着手時 33ファイル263件全緑) | `npx vitest run --reporter=json` で failed=0 かつ passed≥271 | RF-71R.sh 出力 |
| FP-002 (C09a) | 既存テストファイルの削除0。改変はソース結合3ファイル(`test_meeting_detail_wiring` / `test_meeting_detail_title` / `test_transcript_reprocess_ui`)のみ | `git diff --diff-filter=D/M --name-only BASE..HEAD -- services/dashboard/tests` | 同上 |
| FP-003 (C09b) | 上記3ファイルの `expect(` 数が着手時値(21/5/20)を下回らない(参照先付け替えは可、assertion 削除は不可) | `grep -c 'expect(' <file>` | 同上 |
| FP-004 (C09c) | テストに `.only(` / `.skip(` を入れない(着手時 0) | `grep -rn -E '\.(only\|skip)\(' services/dashboard/tests` = 0件 | 同上 |
| FP-005 (C14) | base 版 page.tsx の日本語文字列(2文字以上の連続、111件)が現在の `services/dashboard/src/**/*.ts(x)` にすべて残存(文言の消失・書き換えの機械検出。移動は許容) | RF-71R.sh 内の python 検査 | 同上 |
| FP-006 (C12) | `npx tsc --noEmit` 緑(着手時 緑) | 同コマンド exit 0 | 同上 |
| FP-007 (C13a/b) | eslint ラチェット非増加(着手時 errors=50 / warnings=84)。`lint-baseline.json` を上げるのは禁止・下げるのは可 | `node scripts/ci/lint-ratchet.mjs` + BASE 版 baseline との比較 | 同上 |
| FP-008 (C02) | 差分は `services/dashboard/` 配下のみ(`.hw/plans` はハーネス規約上のメタデータとして除外)。`scripts/`・`.hw/`(plans以外)・`.github/` の変更は違反=ゲート・検証スクリプト改変の防止 | `git diff --name-only BASE..HEAD -- ':(exclude).hw/plans'` が全て `^services/dashboard/` | 同上 |
| FP-009 (C01) | 検証は commit 済み clean tree に対して行う | `git status --porcelain` 空 | 同上 |
| FP-010 (C15) | リポジトリ横断の静的検証(`make smoke` 97 locks + docs)に新規回帰なし | `bash .hw/verify.sh` exit 0 | 同上 |

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | ポーリング間隔の不変(状態リコンサイル 5000ms / post-meeting 成果物 2500ms)。抽出後の hook/テストに間隔値が明示されること | 実装レビュー + 新規テストでの間隔 assert(推奨)。機械最低ラインは FP-001(既存挙動テスト非退行) | Fable レビュー |
| NFT-002 | audio/video 要素がタブ切替・再レンダーで意図せず unmount されない構造(視覚/E2E 基盤がないため機械判定外) | Fable レビュー(JSX 構造の差分確認)+ マージ後の人間実機確認 | review-verdict / 人間確認 |

NFT-002 は機械化できない残余リスクとして明示する(sml-decision の
verifiability=M の根拠)。violation 判定は機械検査(AT/FP)にのみ適用し、
NFT-002 の指摘は advisory として扱う。

## Gate Requirements

- preflight result required: yes(コーディネータが RF-71R.sh のプレ検証で
  C04/C05/C06/C07a/C08/C10/C11b のみ FAIL のパターンを確認してから prime 起動)
- evidence pack required: yes(`.hw/state/prime-rf71r-meeting-detail-split.jsonl`
  と `.hw/gates/rf71r-meeting-detail-split/prime-run.json` を保存)
- hash-bound approval required: yes(M のため Fable READY 必須。READY は
  BASE..HEAD 差分 hash と本契約 hash に束縛)
- research brief required: no
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: not needed

停止条件(HW_PRIME_GATE)= `bash scripts/test/run-refactor-item.sh RF-71R`。
これは実行役の停止判定であり PR の権威ではない。PR 前に pr-ready-gate 全項目
(Fable READY 含む)を通し、CI が最終権威として再実行する。

## Research Freshness Checks

該当なし(外部 API・ライブラリの現行仕様に依存する判断はない。対象は
リポジトリ内コードの構造移動のみ)。

## 改訂履歴

- 2026-08-10 初版(Fable planner)。基準値は main `eb7a37d` 実測。
