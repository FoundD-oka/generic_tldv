# Verification Contract — fix-cloudrun-recording-range-20260907

証跡ルート: `.hw/plans/fix-cloudrun-recording-range-20260907/evidence/`
対象差分: `d7a4d729e95981f59c3653351e41089d0d523184..HEAD`

## フェーズと審査範囲

| Phase | 内容 | Fable レビュー対象 |
|---|---|---|
| A/B コード契約 | 下表「コード契約」「Failure Patterns(コード)」「NFT-002」。commit 済み clean tree の対象差分に対して審査 | 対象 |
| C レビュー後手続き | READY 記録、pr-ready-gate、PR 作成。レビュー後にゲートが機械判定する。未実行をレビュー違反にしない | 対象外 |
| D リリース | Cloud Run image 限定反映と反映後確認。Phase C 完了後の別段階。未実行をレビュー違反にしない | 対象外 |

## コード契約(Phase A/B: Fable レビュー対象。prod 反映前に全て通す)

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-001 | `boundMediaRangeHeader`(`src/lib/media-proxy-range.ts` から import)が仕様表どおり: null/空→null、`bytes=0-65535` 不変、長さ==8388608 不変、長さ==8388609 は `bytes=S-(S+8388607)`、`bytes=0-` → `bytes=0-8388607`、`bytes=30000000-` → `bytes=30000000-38388607`、`bytes=-1024` 不変、`bytes=-`/`bytes=abc`/`items=0-1`/`bytes=0-1,5-9`/`bytes=5-3` 不変、S または E が safe integer 超過は不変、`S+8388607` が safe integer 超過(`bytes=9007199254740000-`)は不変、前後空白 trim 後に判定し不変時は元文字列を返す | unit (vitest) | `evidence/test-output.txt` |
| AT-002 | 経路 A(GET `recordings/{id}/master?type=audio&proxy=1`)+ `Range: bytes=0-` で media fetch に `Range: bytes=0-8388607` と `X-API-Key` を送り、upstream 206(`content-range: bytes 0-8388607/52676203`、`content-length: 8388608`、`accept-ranges: bytes`、`content-type: audio/webm`)を status/ヘッダ/本文とも passthrough | unit (vitest) | `evidence/test-output.txt` |
| AT-003 | 経路 A で `bytes=0-65535` は Range 不変で 206 passthrough。`bytes=0-99999999` は `bytes=0-8388607` に切り詰め | unit (vitest) | `evidence/test-output.txt` |
| AT-004 | 経路 A で `bytes=-1024` は不変転送。Range なしは media fetch の headers に Range キーが無く upstream 200 を passthrough | unit (vitest) | `evidence/test-output.txt` |
| AT-005 | 経路 A で upstream 416(`content-range: bytes */52676203`)を 416 とヘッダごと passthrough | unit (vitest) | `evidence/test-output.txt` |
| AT-006 | 経路 B(GET `recordings/42/media/7/raw`)+ `bytes=0-` で upstream fetch の Range が `bytes=0-8388607`、`audio/webm` 206 を passthrough | unit (vitest) | `evidence/test-output.txt` |
| AT-007 | cookie なしで経路 A は 401、fetch 未呼出 | unit (vitest) | `evidence/test-output.txt` |
| AT-008 | 対象外経路(既存の mp3 取得経路。テスト内コメントに route.ts/api.ts の根拠を記す)で `bytes=0-` と `bytes=0-8388607` がいずれも不変転送 | unit (vitest) | `evidence/test-output.txt` |
| AT-009 | `npm test` 全緑(既存 `test_recording_master_proxy_route` / `test_vexa_sensitive_proxy_auth` を含む)、`npx tsc --noEmit` 成功、`npm run build` 成功 | command | `evidence/test-output.txt`, `evidence/tsc-lint-build.txt` |
| AT-010 | lint ratchet: errors ≤ 50、warnings ≤ 75、`lint-baseline.json` 無変更 | command | `evidence/tsc-lint-build.txt` |
| AT-011 | `bash .hw/verify.sh` が baseline 外の新規失敗ゼロ | command | `evidence/verify-output.txt` |
| AT-012 | 編集前 impact(proxyRequest: MEDIUM / 直接 5 = GET,POST,PUT,PATCH,DELETE / 1 module / processes 0)を記録済みで、truncation の補完として `rangeHeader` 使用 2 箇所・`mediaHeaders` 組立・api.ts の経路 A/B と mp3 の URL 生成箇所を Grep で裏取り。commit 前 `detect_changes()` が complete(partial/truncated は再実行し complete のみ採用)で、変更 symbol が route.ts の proxyRequest 系と新規 lib/テストに限られる | command/source | `evidence/impact.txt`, `evidence/detect_changes.txt` |
| AT-013 | `git diff --name-only base..HEAD` が次のみ: `services/dashboard/src/lib/media-proxy-range.ts`、`services/dashboard/src/app/api/vexa/[...path]/route.ts`、`services/dashboard/tests/test_media_proxy_range_bound.test.ts`、`.hw/plans/fix-cloudrun-recording-range-20260907/**`(plan.md、verification-contract.md、base-commit、sml-decision.json、runtime-decision.json、evidence/**、review-verdict.json)、`.hw/current/task-id`(git 管理時)、`.hw/gates/fix-cloudrun-recording-range-20260907/**`(git 管理時)。`.hw/state/**` は ignored で現れない | diff audit | `evidence/diff-files.txt` |

## レビュー後手続き(Phase C: レビュー対象外、ゲートが機械判定)

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| PR-001 | clean tree で Fable READY(対象差分 hash・契約 hash 束縛)。修復や契約変更で失効したら再レビュー | command | `review-verdict.json` |
| PR-002 | `bash .hw/hooks/pr-ready-gate.sh fix-cloudrun-recording-range-20260907` が `ready` | command | `.hw/gates/<task>/pr-ready.json` |
| PR-003 | 日本語 PR を作成。本文に契約 ID・evidence パス・反映予定 SHA(READY 束縛 commit)・revert 先 revision を列挙。エージェントはマージしない | manual audit | PR URL(`evidence/release/commands.txt` に記録) |

## 反映後確認(Phase D: Phase C 完了後の別段階。レビュー対象外)

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-101 | 反映前に revision 名・image・env 名一覧・ports を記録(期待 `kabosu-dashboard-00069-7q8` / tag `d7a4d729…`。異なれば実値を revert 先とする)。env 値は含めない。`ports` に h2c があれば反映を止め人間へ | command | `evidence/release/revision-before.json` |
| AT-102 | 反映は READY 束縛 commit SHA の clean checkout から、`deploy-dashboard-gcp.yml` と同一の `gcloud builds submit --config cloudbuild-dashboard.yaml` で `_DEPLOY_SHA` = 当該 SHA として実施(image 更新のみ)。反映後 image tag = 当該 SHA、env 名一覧と ports が before と一致 | command | `evidence/release/revision-after.json`, `evidence/release/commands.txt` |
| AT-103 | 録音 841188337344 の経路 A に `bytes=0-` → 206 `bytes 0-8388607/52676203` / `Content-Length: 8388608`、`bytes=30000000-` → `bytes 30000000-38388607/52676203`、`bytes=52676000-` → `bytes 52676000-52676202/52676203`、`bytes=0-65535` → 206/65536 bytes で SHA256 が調査時 backend 値と一致 | smoke (curl) | `evidence/release/http-checks.txt` |
| AT-104 | ブラウザで先頭 10 秒以上再生 → 中央 seek 10 秒以上 → 終端 30 秒前 seek → 終端到達。エラー UI 表示なし。Network に 206 連鎖 | manual (browser) | `evidence/release/browser-check.md` + screenshot |
| AT-105 | 新 revision で「Response size was too large」ログ 0 件(確認時間帯) | command | `evidence/release/cloudrun-log.txt` |
| AT-106 | 同画面の WebM ダウンロードが 100% 完了 | manual (browser) | `evidence/release/browser-check.md` |

## Failure Patterns

| ID | Phase | Must Not Regress | Method | Evidence |
|---|---|---|---|---|
| FP-001 | A/B | テスト削除・skip・期待値緩和なし(既存テストファイル無変更、既存 suite の基準を緩めない) | diff audit | `evidence/test-diff-audit.txt` |
| FP-002 | A/B | 8MiB ちょうどの明示 Range は対象経路で不変転送(AT-001 境界行)、mp3 経路は `bytes=0-` も含め不変(AT-008) | unit | `evidence/test-output.txt` |
| FP-003 | A/B | 認証・raw URL 選択・presigned 拒否・ヘッダ待ち 504・本文無期限の既存挙動が既存テストで緑のまま | unit (既存テスト) | `evidence/test-output.txt` |
| FP-004 | A/B | 非メディア JSON 経路と GET 以外は変更なし(`test_vexa_sensitive_proxy_auth` 緑、route の JSON 分岐に差分なし) | unit + diff | `evidence/test-output.txt`, `evidence/diff-files.txt` |
| FP-005 | A/B | route.ts に新規 named export なし(`git diff base..HEAD -- route.ts` の追加行に `export` が無く、`npm run build` 成功) | diff audit + command | `evidence/diff-files.txt`, `evidence/tsc-lint-build.txt` |
| FP-006 | A/B | AudioPlayer / `lib/api.ts` / GCS 認証 / backend / compose / CI workflow に差分なし | diff audit | `evidence/diff-files.txt` |
| FP-007 | A/B, D | cookie 値・API key・presigned URL の署名を evidence に残さない | grep audit(`X-Amz-Signature|vxa_|Cookie:` が 0 件) | `evidence/secret-scan.txt` |
| FP-008 | D | env 変更コマンド・`.env` 編集・`make -C deploy/compose up`・workflow に無い gcloud 引数を実行しない | command audit | `evidence/release/commands.txt` |
| FP-009 | D | 反映失敗時は AT-101 で記録した revision へ traffic を戻し記録 | command | `evidence/release/commands.txt` |

## Non-Functional Checks

| ID | Phase | Requirement | Method | Evidence |
|---|---|---|---|---|
| NFT-001 | D | 経路 A/B への有効な先頭/途中開始の単一 byte range(`bytes=S-` / `bytes=S-E`、S+8388607 が safe integer 内)に対する応答本文は 8388608 bytes 以下(AT-103 の Content-Length で実証)。suffix・不正値・Range なし要求は対象外 | smoke | `evidence/release/http-checks.txt` |
| NFT-002 | A/B | 新規 lint 負債ゼロ、baseline 引き上げなし | command | `evidence/tsc-lint-build.txt` |

## KPI Checks

該当なし(`kpi-backcast-roadmap.md` なし)。

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes
- hash-bound approval required: yes
- research brief required: no(plan.md のリサーチ記録節で代替)
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no
- external consultation provider: not needed

## Research Freshness Checks

| ID | Decision That Can Go Stale | Freshness Method | Evidence |
|---|---|---|---|
| RF-001 | Cloud Run HTTP/1 応答 32MiB 上限(chunked 除外)。h2c なら前提が変わる | 2026-09-07 公式 quotas 確認済み + AT-101 の `ports` に h2c が無いこと | `evidence/release/revision-before.json` |
| RF-002 | ブラウザが短縮 206 を受けて続きを再要求する | AT-104 の実ブラウザ確認。失敗時は人間へ(代替: chunked 転送) | `evidence/release/browser-check.md` |
| RF-003 | App Router route.ts の named export 制限 | AT-009 の `npm run build` | `evidence/tsc-lint-build.txt` |