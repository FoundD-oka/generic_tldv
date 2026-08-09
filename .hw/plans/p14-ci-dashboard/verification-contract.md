# Verification Contract — p14-ci-dashboard

対象: `base-commit..HEAD` の差分(base-commit はコーディネータが着手時に
plan.md frontmatter へ書き込んだ値)。検証は commit 済み clean tree に対して行う。

## ベースライン取得手順(転記値の使用禁止)

1. 実装着手前に base-commit の clean tree で:
   `cd services/dashboard && npm ci --no-audit --no-fund`
   - `npm test` → サマリ(files / tests 数)を
     `.hw/gates/p14-ci-dashboard/vitest-baseline-<commit>.txt` に保存。
   - `npx eslint . --format json --output-file /tmp/eslint-base.json`(exit 1 許容)
     → errorCount / warningCount 合計を集計し
     `.hw/gates/p14-ci-dashboard/eslint-baseline-<commit>.txt` に保存。
2. **この実測値が本契約のベースライン**であり、`lint-baseline.json` に commit
   する値もこの実測値。handoff の概数(61/87)の転記は無効。
3. ベースラインで vitest に fail がある場合は着手前に報告。

## 検証の権威分担(3層。各 AT/FP に判定主体を明記)

- **Fable 差分レビューで判定するもの**: 契約と `base-commit..HEAD` の差分だけで
  完結する項目(workflow 構文・禁止パターン不在・変更ファイル限定・
  lint-baseline.json と契約記載の独立実測値の突合)。
  **Fable は `.hw/gates/`(gitignore 領域)に到達できない**ため、合否判定を
  evidence pack との突合に依存する AT/FP を置いてはならない。
- **CI 実行結果に委ねるもの(最終権威)**: Linux での npm ci 成功・テスト緑・
  ラチェット緑(= baseline 過小でないことの機械検証)・2種 sabotage の赤・
  キャッシュ効果・実行時間。
- **ゲート(pr-ready-gate / コーディネータ)が evidence pack で確認するもの**:
  `.hw/gates/p14-ci-dashboard/` の実測ログ・run URL の保存と内容確認。
  evidence はレビューの合否根拠ではなく、ゲート通過の証跡。

## Acceptance Tests

| ID | Requirement | Method | Evidence / 判定主体 |
|---|---|---|---|
| AT-001 | workflow が有効な YAML で、trigger paths が `services/dashboard/**` + `packages/transcript-rendering/**` + workflow 自身(push / pull_request 両方)に限定されている | pyyaml でのパース + 目視レビュー | 判定主体: Fable(差分)。パース出力は evidence へ |
| AT-002 | **CI 実測緑(本質要求)**: PR 上で `Test Dashboard` が npm ci → vitest → lint ラチェットまで全ステップ成功 | `gh run list --workflow=test-dashboard.yml` + `gh run view <id> --log` | 判定主体: CI。run URL とログ抜粋を `.hw/gates/p14-ci-dashboard/ci-green.txt` に保存(ゲート確認)。vitest テスト数のベースライン突合はゲートが evidence で行う(レビュー層は FP-002/FP-003 のテスト無変更で代替判定)。**ランナーは UTC のため、この緑は AT-009 の TZ 固定が効いていることの機械検証を兼ねる** |
| AT-003 | **sabotage 検証(テスト)**: テスト期待値を壊す一時 commit で「テストステップの失敗で」赤 → revert で緑 | 一時 commit → `gh run view --log-failed` → revert | 判定主体: CI。赤/緑 run URL・ログ抜粋を `.hw/gates/.../ci-sabotage-test.txt` に保存(ゲート確認) |
| AT-004 | **sabotage 検証(lint ラチェット)**: 新規 lint error を1件足す一時 commit で「ラチェットステップの失敗で」赤(現在値>基準値の出力を含む)→ revert で緑 | 同上 | 判定主体: CI。`.hw/gates/.../ci-sabotage-lint.txt` に保存(ゲート確認) |
| AT-005 | lint-ratchet.mjs の判定境界: 合成 report で (a) errors/warnings がベースライン同数 → exit 0、(b) errors +1 → exit 1、(c) warnings +1 → exit 1、(d) fatalErrorCount ≥1 → exit 1 | ローカルで合成 JSON を与えて実行し exit code 確認。レビュー層は差分内の lint-ratchet.mjs ソースの判定式で確認 | 判定主体: Fable(ソース)+ CI(AT-004 が増加→赤の実証)。ローカル実行ログは `.hw/gates/.../ratchet-boundary.txt` へ(ゲート確認) |
| AT-006a | **baseline の過小記載防止**: `lint-baseline.json` が実測より小さければ AT-002 の緑は成立しない(ラチェットが current > baseline で赤になる)。よって CI 緑 run のラチェットステップが current / baseline 両値を出力して pass していることをもって「baseline ≥ 実測」を機械検証とする | AT-002 の緑 run のラチェットステップ出力(current= / baseline= の行) | 判定主体: CI(最終権威)。ログ抜粋を `.hw/gates/.../ci-green.txt` に含める(ゲート確認) |
| AT-006b | **baseline の過大記載防止(水増しによる新規違反の隠蔽余地の排除)**: commit された `lint-baseline.json` の errors / warnings が、本契約の改訂履歴に記録された**コーディネータ独立実測値(errors=61 / warnings=87、2026-08-09、node v22.23.0 / eslint v9.39.1)**と一致する | 差分内の `lint-baseline.json` と本契約記載値の突合(差分と契約だけで完結) | 判定主体: Fable(差分)。**「減少したら通る」性質は不変**: この一致要求は baseline を導入する本 PR に限る。以後の運用は NFT-004 による |
| AT-007 | eslint クラッシュ(exit ≥2)がラチェットに吸収されず job fail になる分岐が workflow に存在する | workflow の該当ステップのレビュー | 判定主体: Fable(差分) |
| AT-008 | npm キャッシュが効く: 2回目以降の run ログに cache restore が記録され、job 実行時間が 10 分以内 | revert 後 run のログ + `gh run view --json jobs` | 判定主体: CI。ログ・JSON 抜粋を evidence へ(ゲート確認) |
| AT-009 | **テストの TZ 非依存(改訂2で追加)**: (a) `vitest.config.ts` にテストプロセスの TZ を `Asia/Tokyo` へ固定する設定(`test.env.TZ` または setupFiles 冒頭の `process.env.TZ`)が存在する。(b) workflow に `TZ` の設定が**ない**(CI=UTC のままにして固定の実効性を CI 緑で検証するため)。(c) ローカルで `TZ=UTC npm test` と `TZ=Asia/Tokyo npm test` の両方が同数で全pass | (a)(b) は差分レビュー + `grep -n "TZ" .github/workflows/test-dashboard.yml` が空であること。(c) は両コマンドのサマリ | 判定主体: (a)(b) Fable(差分)、UTC 側の最終権威は CI(AT-002 の緑)。(c) はゲート(両サマリを `.hw/gates/p14-ci-dashboard/tz-both.txt` に保存) |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence / 判定主体 |
|---|---|---|---|
| FP-001 | 握り潰しなし: workflow に `continue-on-error` / `\|\| true` がない。`set +e` は eslint 呼び出し1行の exit code 捕捉のみで、直後に `set -e` 復帰と ec 判定がある | grep + 該当ステップのレビュー | 判定主体: Fable(差分) |
| FP-002 | 変更ファイルが `.github/workflows/test-dashboard.yml`・`services/dashboard/lint-baseline.json`・`services/dashboard/scripts/ci/lint-ratchet.mjs`・`services/dashboard/vitest.config.ts`(TZ 固定のみ。改訂2)の4つのみ(src / tests / package.json / eslint.config.mjs は無変更。TZ 固定を setupFiles 方式にする場合のみ setup ファイル1つの新規追加を許容) | `git diff --name-only base-commit..HEAD` | 判定主体: Fable(差分) |
| FP-003 | vitest 設定の弱化なし: `vitest.config.ts` の差分が TZ 固定(env / setupFiles)の追加ハンク**のみ**で、passWithNoTests の有効化・include の縮小・その他テスト判定に関わる変更がない | `git diff base-commit..HEAD -- services/dashboard/vitest.config.ts` のハンクレビュー | 判定主体: Fable(差分) |
| FP-004 | ローカル `npm test` の結果がベースラインと同数(本タスクはテストを変更しない) | 同一コマンドで HEAD を再実行しサマリ比較 | 判定主体: ゲート(evidence pack の両サマリ突合)。レビュー層は FP-002/FP-003(テスト・設定無変更)で代替判定 |
| FP-005 | 依存追加なし: package.json / package-lock.json 無変更(lint-ratchet.mjs は Node 標準モジュールのみ) | FP-002 の diff + import レビュー | 判定主体: Fable(差分) |
| FP-006 | 既存 workflow 10本が無変更 | `git diff base-commit..HEAD -- .github/workflows/ ':!.github/workflows/test-dashboard.yml'` が空 | 判定主体: Fable(差分) |

## Non-Functional Checks

| ID | Requirement | Method | Evidence / 判定主体 |
|---|---|---|---|
| NFT-001 | actions は既存 test-*.yml と同一の SHA ピンを使用 | uses: 行レビュー | 判定主体: Fable(差分) |
| NFT-002 | secrets を要求しない(contents: read のみ) | workflow レビュー | 判定主体: Fable(差分) |
| NFT-003 | lint-ratchet.mjs に baseline を「上げる」抜け道の自動化がない(基準値変更は人間の commit のみ) | ソースレビュー | 判定主体: Fable(差分) |
| NFT-004 | **baseline 変更の由来追跡(将来 PR の運用ルール)**: `lint-baseline.json` を変更する PR は、commit message または PR 本文に測定コマンド・node / eslint バージョン・実測合計値を記録すること。過小方向はラチェットの「増加で赤」により CI が検出し、過大方向はこの記録をその PR のレビューで突合する。このルールを lint-ratchet.mjs 冒頭コメントの運用ルールに含める | lint-ratchet.mjs 冒頭コメントのレビュー | 判定主体: Fable(差分) |

## KPI Checks

なし(kpi-backcast-roadmap.md 不使用)。

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(`.hw/gates/p14-ci-dashboard/` へ。ゲート確認用であり、Fable レビューの合否根拠には使わない)
- hash-bound approval required: yes(M のため Fable 契約レビュー必須)
- research brief required: no
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no
- external consultation provider: not needed

## Research Freshness Checks

- eslint v9 flat config の `--format json` 出力スキーマ(results[].errorCount /
  warningCount / fatalErrorCount)は安定 API。vitest の「テスト0件で非0 exit」
  既定は v4 系で不変。いずれも実装時にローカル実行で確認できるため、
  外部リサーチへの依存なし。

## 改訂履歴

- **2026-08-09 改訂1**(Fable。コーディネータからの差し戻しによる契約改訂。
  実装コードの変更なし)
  - **原因**: 旧 AT-006 が合否判定を `.hw/gates/`(gitignore 領域)の evidence
    との突合だけに依存させていた。Fable レビュー層は read-only で
    `base-commit..HEAD` の差分と契約しか見ないため、この条件はレビュー層では
    原理的に判定不能であり、NEEDS_HUMAN(violations=0 / advisory=5)を誘発した。
  - **改訂前**: 「AT-006 | `lint-baseline.json` の値がベースライン取得手順の
    実測値と一致する | evidence の eslint-baseline と commit された JSON の突合 |
    突合結果」
  - **改訂後**: AT-006a(過小方向 = CI ラチェット緑が機械検証。baseline が
    実測未満なら AT-002 の緑が成立しない)と AT-006b(過大方向 = 差分内の
    baseline JSON を本改訂履歴記載のコーディネータ独立実測値と突合。レビュー層
    で完結)に分離。将来の baseline 変更 PR の由来記録ルールを NFT-004 として
    追加。「減少したら通る」ラチェット性質は不変。
  - **コーディネータ独立実測(AT-006b の基準値の根拠)**: 2026-08-09、本タスク
    ブランチで `npx eslint . --format json` を独立再実行 → **errors=61 /
    warnings=87** / fatal=0 / filesWithProblems=51 / resultEntries=237。
    環境: node v22.23.0 / eslint v9.39.1。resultEntries が実装者の base-commit
    実測(236)より1多いのは本ブランチで追加された lint-clean な
    `scripts/ci/lint-ratchet.mjs` の分で説明がつく。実装者 evidence
    (`.hw/gates/p14-ci-dashboard/eslint-baseline-69d01b2.txt`、測定日時
    2026-08-09T08:02:03Z)とも一致。
  - **併せて実施**: 「検証の権威分担」を3層(Fable差分 / CI / ゲート)に明確化
    し、全 AT/FP に判定主体を明記。AT-002(vitest 数の突合)・FP-004(ローカル
    サマリ突合)は evidence 依存部分をゲート判定へ移し、レビュー層の判定は
    FP-002/FP-003(差分の無変更確認)で代替する形に整理。

- **2026-08-09 改訂2**(Fable。PR #63 の CI 赤に対するコーディネータ差し戻し
  による。実装コードの変更なし)
  - **発見の経緯**: PR #63 で `Test Dashboard` が `Run tests` ステップで赤
    (`Tests 1 failed | 242 passed (243)`)。失敗は
    `tests/test_export_and_bot_defaults.test.ts > exportToTxt > uses a
    Japanese text export template` の `日時: 2026年6月25日 19:00` 期待
    (19:00 JST = 10:00 UTC、CI ランナーは UTC)。CI 導入によって初めて露見
    した既存テストの環境依存であり、ST-26 の狙いどおりの検出。
  - **実測値**: コーディネータ実測 — 当該ファイル `TZ=UTC` で 1 failed /
    `TZ=Asia/Tokyo` で 6 passed。Fable 実測(スイート全体、node v22.23.0)—
    `TZ=UTC` / `TZ=America/New_York` / `TZ=Pacific/Kiritimati` の3ゾーン
    すべてで **1 failed | 242 passed (243)**、失敗は全ゾーン当該1件のみ。
    **他テストに同種の TZ 依存なし**(advisory 不要)。別途、Node v22 が
    実行中の `process.env.TZ` 変更を即時反映すること(10時→19時)も実測済み。
  - **製品バグではないと判断した根拠**: `exportToTxt`(`src/lib/export.ts`)は
    date-fns + ja ロケールで整形し、呼び出し元 `src/app/meetings/[id]/page.tsx`
    は `"use client"` = ブラウザ実行。閲覧者ローカル TZ での整形は意図どおり。
    欠陥はテスト側の暗黙の JST 依存。
  - **方針決定**: (B) の変種「vitest 設定でテストプロセスの TZ を Asia/Tokyo
    に固定」を採用(誰の環境でも同一結果 + 日本語限定プロダクトの実運用表示と
    期待値リテラルの一致を両立)。(A) workflow への TZ 設定は「JST でしか
    通らない欠陥の温存 + CI からの隠蔽」のため不採用。CI ランナーは UTC の
    ままにし、AT-002 の緑が固定の実効性の機械検証を兼ねる。
  - **契約変更**: AT-009 新設(TZ 固定の存在 / workflow に TZ なし / 両 TZ で
    ローカル全pass)。FP-002 を4ファイルへ(vitest.config.ts の TZ 固定のみ
    許容)、FP-003 を「TZ 追加ハンクのみ」のハンクレビューへ改訂。
    テスト修理は CI 整備タスクのスコープ内と判断(テストの環境依存の修理で
    あって製品挙動の変更ではない。製品コード不変は FP-002 で機械検証)。
