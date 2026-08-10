# Verification Contract — rf71r-meeting-detail-split(第2版)

判定の単一実行点は `bash scripts/test/run-refactor-item.sh RF-71R`
(実体: `scripts/test/refactor-checks/RF-71R.sh`。exit 0 = 合格ライン到達)。
正の判定は**コーディネータによる再実行のみ**。再実行時は既知の base を
`RF71R_BASE=<base> bash scripts/test/run-refactor-item.sh RF-71R` として外部ピンする
(base-commit ファイルと不一致なら C00 で fail)。実行役のゲート出力は自己申告として
扱う。`RF71R_SKIP_NODE=1` は FAIL パターン確認専用で、必ず exit 1(合格判定不可)。

第2版の要旨: 第1回試行が「page.tsx の本体を隣のファイルへ移動するだけ」で
第1版の合格ラインに到達したため、指標を「page.tsx 単体」から「差分で触れた
全ファイル+配置規則+純増上限」へ拡張した。目的は行数の削減ではなく
**責務の分解**であり、以下の検査はそれを機械判定に近似する。

基準値は 2026-08-10 に `00d960b`(page.tsx 2,502行 / 109,537B)で実測。
`BASE` は `.hw/plans/rf71r-meeting-detail-split/base-commit` の値。
`PAGE=services/dashboard/src/app/meetings/[id]/page.tsx`。

## Acceptance Tests

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-001 (C04) | Page 本体 600 行以下かつ 32,000B 以下(着手時 2,502行/109,537B)。行数偽装(minify)はバイト上限で無効 | `wc -l` / `wc -c` | RF-71R.sh 出力 |
| AT-002 (C03, C17) | Page は自ファイル内で `export default function` を定義する。re-export だけの page・import/re-export のみの passthrough ファイルは差分全体で 0 | grep + python 検査 | 同上 |
| AT-003 (C16) | 差分で触れた(A/M)全コードファイル(`services/dashboard` 全体、tests 含む、page 以外)が 600 行以下かつ 32,000B 以下。既存ファイルの改変は base 実測 +80行 / +4,000B まで許容 | RF-71R.sh 内 python 検査 | 同上 |
| AT-004 (C05a/b) | Page 内 `setInterval` 0(着手時 2)。差分で触れた src ファイルのうち `src/hooks/`・`src/lib/` 以外で `setInterval` を base より増やさない | grep + python 検査 | 同上 |
| AT-005 (C06a/b) | Page 内 raw `fetch(` 0(着手時 5。`fetch.call/apply/bind` も検出)。差分で触れた src ファイルのうち `src/hooks/`・`src/lib/`・`src/app/api/` 以外で `fetch` を base より増やさない | 同上 | 同上 |
| AT-006 (C07a/b) | Page 内 VNC 文字列(`vnc.html`/`websockify`)0(着手時 2)。src 全体で `vnc/vnc.html` 出現 ≤2・`vnc/websockify` 出現 ≤2・含有ファイル ≤2(着手時 3/3/2。組立の共通化で減る方向のみ) | grep -rEo 集計 | 同上 |
| AT-007 (C08) | タイトル保存の実装1本化。成功/失敗トースト文言の出現が src 全体(meeting-card.tsx 除く)で各1(着手時 各4) | grep 集計 | 同上 |
| AT-008 (C10a/b, C11b) | 新規 `.test.ts` 2ファイル以上。各新規テストは BASE..HEAD で追加/変更された src モジュールを import し `expect(` ≥3。テスト総数 271 件以上(着手時 263)・全緑 | git diff + python + vitest JSON | 同上 |
| AT-009 (C18) | `services/dashboard/src` の純増が +500 行以下かつ +25,000B 以下(コピー・重複・水増しの禁止。移動は純増ゼロ、抽出の界面コストのみ許容) | numstat + blob サイズ差 | 同上 |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 (C11b) | 既存テスト 263 件が1件も落ちない | vitest JSON で failed=0 かつ passed≥271 | RF-71R.sh 出力 |
| FP-002 (C09a) | 既存テストファイルの削除0。改変はソース結合3ファイル(`test_meeting_detail_wiring` / `test_meeting_detail_title` / `test_transcript_reprocess_ui`)のみ | git diff --diff-filter | 同上 |
| FP-003 (C09b) | 上記3ファイルの `expect(` 数が着手時値(21/5/20)を下回らない | grep -c | 同上 |
| FP-004 (C09c) | テストに `.only(` / `.skip(` を入れない | grep | 同上 |
| FP-005 (C14) | base 版 page.tsx の日本語文字列(2文字以上の連続、111件)が **page.tsx の import 閉包内**にすべて残存(未参照ファイルへの文言退避は不合格。移動は許容。日本語コメントも対象なので削除しない) | RF-71R.sh 内の閉包 BFS 検査 | 同上 |
| FP-006 (C12) | `npx tsc --noEmit` 緑 | 同コマンド exit 0 | 同上 |
| FP-007 (C13a/b) | eslint ラチェット非増加(着手時 errors=50 / warnings=84)。`lint-baseline.json` を上げるのは禁止・下げるのは可 | lint-ratchet.mjs + BASE 比較 | 同上 |
| FP-008 (C02) | 差分は `services/dashboard/` と本タスクの `base-commit`・`review-verdict*` のみ。**plan.md・本契約・`scripts/`・`.hw/`本体・`.github/` への変更は違反**(第1版は `.hw/plans` を丸ごと除外しており契約改変が素通りだった。是正) | git diff --name-only の allowlist | 同上 |
| FP-009 (C19) | `services/dashboard` 内の設定・CI補助・依存ファイル(package.json / package-lock.json / vitest.config* / tsconfig* / eslint* / next.config* / postcss* / tailwind* / components.json / .npmrc / .gitignore / scripts/)は不変(ゲート無効化経路の遮断) | git diff --name-only | 同上 |
| FP-010 (C00) | base-commit の差し替え禁止。コーディネータ再実行時は `RF71R_BASE` 外部ピンと一致し、BASE は HEAD の祖先であること | RF-71R.sh C00 | 同上 |
| FP-011 (C01) | 検証は commit 済み clean tree に対して行う | git status --porcelain 空 | 同上 |
| FP-012 (C15) | リポジトリ横断の静的検証に新規回帰なし | `bash .hw/verify.sh` exit 0 | 同上 |

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | ポーリング間隔の不変(状態リコンサイル 5000ms / post-meeting 成果物 2500ms) | 実装レビュー + 新規テストでの間隔 assert(推奨)。機械最低ラインは FP-001 | Fable レビュー |
| NFT-002 | audio/video 要素がタブ切替・再レンダーで意図せず unmount されない構造 | Fable レビュー + マージ後の人間実機確認 | review-verdict / 人間確認 |
| NFT-003 | **検査回避を目的とした難読化の禁止**: 文字列の分割・連結・エンコード、識別子の間接参照(`window["set"+"Interval"]` 等)、無意味な props 素通し分割など、検査文字列・閾値の回避だけを目的とする構造は violation(機械検査を通っていても不合格)。判定は Fable レビューと人間 | BASE..HEAD 差分レビュー | review-verdict |

NFT-002 は機械化できない残余リスクとして明示する。NFT-003 は本実験の趣旨
(検査の裏の意図=責務の分解)を契約本文に固定するもので、violation 扱い。

## Gate Requirements

- preflight result required: yes。コーディネータが prime 起動前(commit 済み・
  base-commit 更新済みの状態)に RF-71R.sh を実行し、FAIL が
  **C04 / C05a / C06a / C07a / C07b / C08 / C10a / C10b / C11b のみ**である
  パターンを確認してから起動する(これらは未実装ゆえの期待 FAIL)。
- evidence pack required: yes(`.hw/state/prime-rf71r-meeting-detail-split.jsonl`
  と `.hw/gates/rf71r-meeting-detail-split/prime-run.json`。第1回分は
  `*-run1` へ退避済みであること)。
- hash-bound approval required: yes(M のため Fable READY 必須。READY は
  BASE..HEAD 差分 hash と本契約 hash に束縛)。
- 停止条件(HW_PRIME_GATE)= `bash scripts/test/run-refactor-item.sh RF-71R`。
  実行役の停止判定にすぎず、正の判定はコーディネータが `RF71R_BASE` ピン付きで
  再実行したもののみ。PR 前に pr-ready-gate 全項目、CI が最終権威。
- research brief / option matrix / kpi backcast / external consultation: 不要。

## Research Freshness Checks

該当なし(対象はリポジトリ内コードの構造分解のみ)。

## 改訂履歴

- 2026-08-10 初版(Fable planner)。基準値は main `eb7a37d` 実測。
- 2026-08-10 第2版(Fable planner)。第1回試行が「本体の別ファイル移動」で
  第1版に合格した(Goodhart)ため、検査対象を page 単体から差分全体へ拡張:
  C16(触れた全ファイルの行数/バイト上限)・C17(passthrough 禁止)・
  C18(純増上限)・C19(dashboard 内設定の凍結)・C10b(テストの結合強制)を
  新設。C02 の `.hw/plans` 丸ごと除外を廃止(契約・plan 改変の穴)。C04 を
  600行+バイト上限へ、C05/C06 を配置規則へ、C07 を出現数上限へ、C14 を
  import 閉包制限へ強化。C00 に `RF71R_BASE` 外部ピンを追加。迂回シナリオと
  遮断根拠の全対応表は plan.md の Why 追記(実装者非公開)を参照。
