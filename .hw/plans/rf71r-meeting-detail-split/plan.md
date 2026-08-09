---
generated_by: fable
task_id: rf71r-meeting-detail-split
size: M
runtime: prime
base-commit: de926e386c97ec4aea5e3bb5bb741c8465fe86cb
---

# RF-71R: meetings/[id]/page.tsx の分割(RF-70/71 の縮約・自己完結版)

## Why(実装者に渡さない)

このセクションは prime_run.py が実装者プロンプトから除去する。コーディネータと
レビュー層だけが読む。

### 依頼の文字通りと reframe

- 文字通りの依頼: 「リファクタリングv2で(Prime Agent を)動かしてみて」。
- 本当のゴール: **Prime Agent(prime ハーネス)の性能評価実験**。特に
  「想定外の未知の課題発見・解決能力」を測る。そのために成功条件は
  **機械判定可能**でなければならず(ユーザー明示指示)、同時に How を固めすぎると
  発見能力が測れない。
- reframe の内容: v2 の RF-70/71 をそのまま実行するのではなく、
  (1) 依存 RF 項目(RF-15/17/20/21/22/23/67/69/70)が main に一切ない、
  (2) v2 の完了条件コマンド `run-refactor-item.sh` / `run-required-suites.sh` が
  リポジトリに存在しない、(3) 視覚回帰基盤がなく「visual regression 差0」は
  機械判定不能、という3つの実測事実に基づき、**RF-70+RF-71 の機械判定可能な
  最終状態だけを抜き出した自己完結タスク「RF-71R」として再設計**した。
  依存項目の成果物(hook/component)は本タスク内で新設する。分割の仕方は
  実装者の裁量(発見能力の観測対象)。

### スコープ選定の根拠

- RF-71 の完了条件は「Page本体1,200行以下 / setInterval 0 / VNC URL builder 0 /
  title API call 0」という数値目標を含み、実験の成功条件として最適。
- RF-70 の「title保存の1実装化」はトースト文言の出現数(C08)で機械化できる。
- 「visual regression 差0」は基盤がないため契約から**落とし**、代替として
  (a) 日本語文言の全量残存検査(C14、base版page.tsxの111文字列)、
  (b) ソース文字列に結合した既存テスト3本の assertion 維持(C09b)、
  (c) 全既存テスト非退行(C11b)を機械化した。
- Phase 3 の p3-triage も UI-20(page.tsx分割)を「リファクタv2 RF-70/71 へ委譲」
  としており、本タスクはその委譲先の実体になる。

### 停止条件問題(prime が M/L で pr-ready-gate を満たせない)の解決

問題: `.hw/prime_run.py` は停止条件に `pr-ready-gate.sh` をハードコードするが、
M/L では現在差分hashに束縛された Fable READY が必要で、read-only レビュー工程の
産物である READY を実行役は構造的に作れない。prime は M/L で必ず予算切れ
→ escalate になり、「prime の性能」ではなく「ハーネス統合の欠陥」を測ってしまう。

採用した解: **`HW_PRIME_GATE` 環境変数による停止条件の上書き**
(prep/prime_run_gate_override.patch.md)。既定は現行のまま fail-closed。
本タスクでは `bash scripts/test/run-refactor-item.sh RF-71R` を停止条件に渡す。
根拠:

1. 権威の序列を壊さない。停止条件は「実行役がいつ手を止めてよいか」の判定であり
   PR の権威ではない。PR 前には従来どおりコーディネータが Fable レビュー →
   pr-ready-gate(全項目)を実行し、PR 作成コマンドは共通 PreToolUse フックが
   インターセプトする。CI が最終権威として再実行する。
2. 自己申告排除の原則を保つ。代替ゲートは **commit 済みの検証スクリプト**で、
   実行役はこれを書き換えられない(C02 の allowlist が scripts/ と .hw/ の変更を
   機械的に拒否し、書き換えれば自分のゲートが落ちる)。
3. 上書きは prime-run.json の `gate_command` に記録され、証跡が残る。
4. 変更が最小(3行+コメント)で、既定動作は不変。

棄却した別解:
- **S と申告してゲートの Fable READY 要求を外す**: S/M/L は不確定性の記録であり、
  実験のために偽ると fail-closed 原則とレビュー義務が壊れる。棄却。
- **pr-ready-gate に「prime 走行中モード」を足す**: 全タスク共通のゲート面を
  広げ、恒久的な抜け穴になり得る。実行1回のスコープで済む env 上書きより
  リスクが大きい。棄却。
- **Fable READY の事前発行**: READY は差分hashに束縛され実装前に発行不能
  (設計どおり)。棄却。

### 観測プローブ(How に書かず、prime が自力発見するか観測する)

以下は既知の落とし穴だが、意図的に How から伏せてある。ゲートが赤くなる形で
表面化するので、発見と解決の過程が transcript に残る:

1. 既存テスト3本が page.tsx の**ソース文字列に直接結合**しており、コード移動で
   壊れる → 参照先の付け替えという解決を自力導出できるか(C09a/C09b が枠を固定)。
2. **tsc を緑にする修正が eslint ラチェットを赤にする**相互作用(PR #73 実例)。
3. vitest の include は `tests/**/*.test.ts` のみで **.tsx テストは実行されない**
   (C10 が `.test.ts` を数えるため、.tsx で書くとゲートが通らない)。
4. worktree には node_modules がない(ゲートスクリプトが自前で npm ci するので
   ゲート実行は自己完結だが、実装中のローカル検証には自分で環境構築が要る)。
5. メディア要素(audio/video)の JSX を子コンポーネント化する際の
   mount/unmount リセット。**これは機械検査がない**(視覚/E2E基盤なし)。
   Fable レビューと人間の実機確認で見る。prime が自発的にこのリスクを指摘するかは
   発見能力の重要な観測点。

### コーディネータ準備作業(prime 起動前。順番厳守)

1. 本プラン一式を commit(`plan(hw): rf71r-meeting-detail-split プラン`)。
2. prep の内容をリポジトリへ配置して commit:
   - `prep/run-refactor-item.sh` → `scripts/test/run-refactor-item.sh`
   - `prep/RF-71R.sh` → `scripts/test/refactor-checks/RF-71R.sh`
   - `prep/prime_run_gate_override.patch.md` の内容を `.hw/prime_run.py` へ適用
   - 確認: `python3 -c "import ast;ast.parse(open('.hw/prime_run.py').read())"`
3. `git rev-parse HEAD`(=手順2のコミット)を
   `.hw/plans/rf71r-meeting-detail-split/base-commit` に上書きして commit。
   これで C02 allowlist と Fable レビューの対象が「prime の差分だけ」になる。
4. 動作確認: `bash scripts/test/run-refactor-item.sh RF-71R` を実行し、
   **C04・C05・C06・C07a・C08・C10・C11b のみが FAIL**、他が ok であることを
   確認する(2026-08-10 のプレ検証で確認済みのパターン。これらの FAIL は
   未実装ゆえの期待値)。
5. 起動:
   ```
   HW_PRIME_GATE="bash scripts/test/run-refactor-item.sh RF-71R" \
   HW_PRIME_GATE_TIMEOUT_MS=900000 \
   python3 .hw/prime_run.py rf71r-meeting-detail-split
   ```
   (GATE_TIMEOUT を15分に上げるのは、worktree 初回ゲートの npm ci があるため)

### prime 終了後(コーディネータ)

1. worktree(`../generic_tldv.hw-worktrees/rf71r-meeting-detail-split`)で
   `bash scripts/test/run-refactor-item.sh RF-71R` を自分で再実行(自己申告排除)。
2. Fable 契約レビュー `python3 .hw/fable_review.py rf71r-meeting-detail-split`
   (M のため READY 必須)。violation は worktree 上の実行役へ差し戻し。
3. verdict commit 後 `bash .hw/hooks/pr-ready-gate.sh rf71r-meeting-detail-split`
   → `.hw/current/task-id` 書き込み(PR 作成とは別の Bash 呼び出しで)→ PR。
4. 実験の評価: `.hw/state/prime-rf71r-meeting-detail-split.jsonl` と
   `.hw/gates/rf71r-meeting-detail-split/prime-run.json` から
   ターン数・継続回数・トークン・ゲート試行回数を集計。観測プローブ1〜5の
   発見有無と解決手順を transcript から抽出する。

### v2 プランへの advisory(本タスクの副産物)

- `scripts/test/run-refactor-item.sh` を新設した。v2 の各 RF 項目は
  `scripts/test/refactor-checks/<RF-xx>.sh` を追加すれば同じ入口で判定できる。
  `run-required-suites.sh` は新設しない(suite 実行は各 checks スクリプトに
  内包する方が二重管理にならない)。v2 プランの完了条件文言は要改訂。
- 本タスク完了後、v2 の RF-70/71 は「RF-71R で達成済み(縮約)」として
  改訂が必要(視覚回帰条件の扱い含む。人間の承認事項)。

## ゴール

`services/dashboard/src/app/meetings/[id]/page.tsx`(2,502行)を分割し、
route コンポーネントを「hook 接続・store 参照・タブ/レイアウト composition」に
限定する。数値目標:

- Page 本体 **1,200行以下**
- Page 内 `setInterval` **0**(現在2。ポーリングは hook へ)
- Page 内 raw `fetch(` **0**(現在5。API 呼び出しは hook / lib へ)
- Page 内 VNC URL 組立(`vnc/vnc.html` 文字列)**0**(現在2。組立実装は
  src 全体で高々2ファイル=共通 builder + 既存 browser-session-view)
- タイトル保存の実装 **1本化**(現在 desktop/mobile で4箇所重複。
  成功/失敗トースト文言の出現が src 全体で各1箇所になる。
  会議一覧側 `meeting-card.tsx` は対象外)
- 抽出した hook / ロジックの**新規ユニットテストを2ファイル以上・8テスト以上**追加

ユーザーから見た挙動・画面・文言は一切変えない(下の不変条件)。

## 現状(着手時実測、main eb7a37d、2026-08-10)

- `page.tsx`: 2,502行。`setInterval` 2(L908 状態リコンサイル5s / L967
  post-meeting 成果物2.5s)。`fetch(` 5(L625/L646 録音プローブ、L1974
  ブラウザ状態保存、L2449/L2465 TTS speak)。`vnc/vnc.html` 2(L1191/L1944、
  desktop/mobile で重複)。タイトル保存 `updateMeetingData(...{name:...})` +
  トーストが4箇所(L1273/L1298 desktop、L1603/L1627 mobile)。
- 既存テスト: 33ファイル 263件 全緑(vitest 4.1.0)。うち
  `test_meeting_detail_wiring` / `test_meeting_detail_title` /
  `test_transcript_reprocess_ui` の3本は page.tsx の**ソーステキストを読んで
  文字列を検証**する形式。
- `npx tsc --noEmit` 緑。lint は `lint-baseline.json`(errors=50 / warnings=84)
  とのラチェット比較(`scripts/ci/lint-ratchet.mjs`)。
- 既存資産: `src/hooks/`(use-live-transcripts 等4本)、`src/lib/`
  (single-flight-polling、browser-api-url、export 等)、
  `src/components/meetings/`(browser-session-view 等)。

## 不変条件(厳密。違反はレビューで差し戻す)

1. **挙動不変**: ユーザー操作に対する反応、ポーリング間隔(5s / 2.5s)、
   API 呼び出しの回数・順序・ペイロード、トースト表示条件を変えない。
2. **文言不変**: 日本語文言は一字も変えない・消さない(C14 が base 版 page.tsx の
   全日本語文字列111件の残存を機械検査する。移動は自由)。
3. **DOM 構造の実質不変**: role / aria-* / className / 要素の入れ子とタブ構造を
   保つ。audio / video 要素が再レンダーやタブ切替で意図せず unmount されない
   構造にする。
4. **公開シグネチャ不変**: route(`/meetings/[id]`)と page の default export を
   維持。既存の exported 関数・コンポーネントのシグネチャを変えない。
5. **テスト非退行**: 既存テストの削除禁止。改変はソース結合3ファイル
   (`test_meeting_detail_wiring` / `test_meeting_detail_title` /
   `test_transcript_reprocess_ui`)の**参照先パス付け替えのみ**許可。
   assertion の削除・期待値の緩和・`.skip`/`.only` 禁止(C09a/b/c)。
6. **変更範囲**: `services/dashboard/` 配下のみ(C02)。`scripts/`・`.hw/`・
   `.github/` に触れない。`lint-baseline.json` は下げるのみ可・上げるの禁止。
7. **新規モジュールの型を明示**: 抽出する hook / component の props・戻り値に
   明示的な型を付ける(tsc 緑 + eslint ラチェット非増加の両立。C12/C13)。

## How(何を満たすかは厳密に、どう分割するかは緩く)

- 分割の単位・命名・配置は実装者の裁量。参考として v2 プラン(RF-70/71)は
  `useMeetingActions` / `MeetingHeader` / `useMeetingPlayback` /
  `MeetingPlaybackPanel` / ブラウザセッション panel / `useMeetingTts` という
  分割を想定していたが、**これに従う義務はない**。数値目標と不変条件を満たす
  最善の分割を選ぶこと。
- 既存の `src/hooks/` `src/lib/` `src/components/` の資産・慣例
  (`@/` alias、named export、テストは `tests/test_*.test.ts`)に合わせる。
- 抽出したポーリング・タイトル保存・TTS 等のロジックには挙動を固定する
  ユニットテストを書く(新規 `.test.ts` 2ファイル以上、合計テスト数 271件以上)。
- 大きな移動は「移動だけのコミット」と「接続変更のコミット」を分けるなど、
  レビュー可能な粒度で commit する。コミットメッセージは日本語で
  `refactor(dashboard): ...` 形式。
- 進捗の各段階で `bash scripts/test/run-refactor-item.sh RF-71R` を実行して
  現在地を確認できる(これが完了条件そのもの)。

## 完了条件

commit 済み clean tree で `bash scripts/test/run-refactor-item.sh RF-71R` が
**exit 0**(C00〜C15 全通過)。個々の検査の意味と基準値は
`verification-contract.md` に定義する。テストの削除・skip・期待値の緩和・
検証スクリプトの変更で通すことは禁止(C02/C09 が機械的に検出する)。
