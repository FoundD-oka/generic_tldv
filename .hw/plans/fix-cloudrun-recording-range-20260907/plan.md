---
generated_by: fable
task_id: fix-cloudrun-recording-range-20260907
base_commit: d7a4d729e95981f59c3653351e41089d0d523184
created: 2026-09-07
revised: 2026-09-07 (実装前改訂 r2)
---

# Cloud Run 経由の長尺録音再生を「対象メディア経路限定の上限付き Range 転送」で復旧する

## 依頼の文字通りの内容

終了ミーティングの再生で「音声の読み込みに失敗しました 再試行」になる不具合を修正する。原因調査は完了済み(Cloud Run の HTTP/1 応答 32MiB 上限に、dashboard proxy が `Range: bytes=0-` をそのまま転送して得た 52MB 応答が衝突し 500)。

## ゴール(reframe なし)

修正方針「1 応答を 8MiB に区切る Range 配信」はユーザー合意済みなので reframe しない。ゴールは次の 1 点:

> Cloud Run 公開 dashboard で、32MiB 超の録音(例: 録音 841188337344 / 52,676,203 bytes / audio/webm)を `<audio>` で先頭再生・途中 seek・終端付近再生できる。対象 2 経路(下記)への有効な先頭/途中開始の単一 byte range 要求に対する 1 応答本文は 8MiB 以下。

## 適用範囲(これだけに効かせる)

上限付き Range 転送を適用するのは GET の次の 2 経路のみ:

- 経路 A: canonical master proxy `recordings/{id}/master` で `proxy=1`(既存の media fetch 分岐。`mediaHeaders` を組み立てる箇所)
- 経路 B: 直接 raw `recordings/{id}/media/{mediaId}/raw`(既存の `headers` に Range を載せる分岐のうち、この path 形のみ)

それ以外(既存の mp3 取得経路、非メディア JSON 経路、GET 以外)は Range を無変更で転送する。

## 対象外(触らない)

- AudioPlayer の再試行ロジック、GCS 署名認証 warning、`lib/api.ts`(別 worktree で進行中)
- 既存の mp3 取得経路の Range 転送(8MiB 明示 chunk を含め無変更)
- Cloud Run の環境変数・`.env`・compose 構成・CI workflow
- suffix range(`bytes=-N`)、複数範囲、不正値、Range なし要求の挙動(既存互換で据え置く。Range なし要求で 32MiB 超が失敗する可能性は残るが、今回の再現トレースは Range 付き要求であり、Range なし要求の本番発生は未確認。発生したら別タスク)
- proxyRequest の全体リファクタ
- main へのマージ(ユーザー未明示。エージェントは実施しない)

## How

### 変更ファイル(これ以外に差分を出さない)

1. `services/dashboard/src/lib/media-proxy-range.ts`(新規。純関数と定数の export はここだけ)
2. `services/dashboard/src/app/api/vexa/[...path]/route.ts`(import と適用箇所のみ。新規 named export は追加しない。App Router の route.ts は HTTP メソッド以外の named export を build で拒否する)
3. `services/dashboard/tests/test_media_proxy_range_bound.test.ts`(新規。既存テストファイルは変更しない)
4. `.hw/plans/fix-cloudrun-recording-range-20260907/**`(plan.md、verification-contract.md、base-commit、sml-decision.json、runtime-decision.json、evidence/**、review-verdict.json)
5. `.hw/current/task-id`(git 管理されている場合のみ)、`.hw/gates/fix-cloudrun-recording-range-20260907/**`(git 管理されている場合のみ)

`.hw/state/**` は ignored のため差分に現れない(現れたら違反)。

### Step 1: `src/lib/media-proxy-range.ts`(新規)

```ts
// Cloud Run の HTTP/1 応答上限(32MiB)より十分小さい 1 応答あたりの最大バイト数。固定値。env で可変にしない。
export const MEDIA_PROXY_RANGE_MAX_BYTES = 8 * 1024 * 1024; // 8388608
export function boundMediaRangeHeader(rangeHeader: string | null): string | null
```

引数は `rangeHeader` のみ(maxBytes 引数は設けない。境界テストは定数を使う)。仕様(これ以外の独自規約を足さない。max = 8388608):

| 入力 Range(trim 後) | 返す値 | 備考 |
|---|---|---|
| null / 空 | null(転送しない) | 既存互換。応答は upstream の 200 をそのまま |
| `bytes=S-E` かつ `E-S+1 <= max` | 入力そのまま | 8MiB ちょうどは不変 |
| `bytes=S-E` かつ `E-S+1 > max` | `bytes=S-(S+max-1)` | 大きな明示範囲 |
| `bytes=S-`(open end) | `bytes=S-(S+max-1)` | ブラウザの典型要求。主因の経路 |
| `bytes=-N`(suffix) | 入力そのまま | 起点を変えると意味が変わるため触らない |
| `S+max-1` が safe integer を超える | 入力そのまま | 和の overflow guard |
| 上記以外(`bytes=-`、`bytes=abc`、`items=0-1`、`bytes=0-1,5-9`、`E<S`、S または E が safe integer 超過) | 入力そのまま | upstream が 416 等を判断(既存互換) |

判定手順:

1. null または trim 後が空 → null。
2. trim 後の値が `/^bytes=(\d*)-(\d*)$/` に一致しない → 入力をそのまま返す。
3. 第 1 群 s が空(suffix)→ 入力をそのまま返す。
4. `S = Number(s)`。`Number.isSafeInteger(S)` でなければ入力をそのまま返す。
5. 第 2 群 e が非空なら `E = Number(e)`。`Number.isSafeInteger(E)` でない、または `E < S` なら入力をそのまま返す。`E - S + 1 <= max` なら入力をそのまま返す。
6. `newEnd = S + max - 1`。`Number.isSafeInteger(newEnd)` でなければ入力をそのまま返す。
7. `bytes=${S}-${newEnd}` を返す。

「入力をそのまま返す」は trim 前の元文字列を返す(転送内容を変えない)。

### Step 2: route.ts の適用(import 1 行 + 適用 2 箇所)

- 先頭に `import { boundMediaRangeHeader } from '@/lib/media-proxy-range';`(既存 import の alias/相対パス規約に合わせる)。
- `const rangeHeader = request.headers.get('range');` は変更しない。
- 経路 A: `mediaHeaders['Range']` を設定している箇所で `rangeHeader` の代わりに `boundMediaRangeHeader(rangeHeader)` を使う。null なら既存どおり Range を付けない。分岐条件(既存の GET・`proxy=1` 判定)は変えない。
- 経路 B: `headers['Range']` を設定している箇所で、method が GET かつ path が `recordings/{id}/media/{mediaId}/raw`(セグメント 5 個、1 番目 `recordings`、3 番目 `media`、5 番目 `raw`)のときのみ `boundMediaRangeHeader(rangeHeader)`、それ以外は `rangeHeader` をそのまま使う。path 判定は route.ts 内の非 export 関数または定数で書く(named export にしない)。
- 応答側は変更しない: status、`Content-Type`/`Content-Length`/`Content-Range`/`Accept-Ranges`/`Content-Disposition` の passthrough、`Cache-Control: no-store`、ヘッダ待ちタイムアウト(`MEDIA_PROXY_HEADERS_TIMEOUT_MS`、本文は無期限)、認証(cookie なし 401、backend raw への `X-API-Key`、外部 presigned へは付けない)、raw URL 選択。upstream の 206/200/416 とヘッダはそのまま返す。

### Step 3: テスト(`tests/test_media_proxy_range_bound.test.ts`、vitest/node。既存テストの mock パターンを踏襲)

- import: `boundMediaRangeHeader`・`MEDIA_PROXY_RANGE_MAX_BYTES` は `src/lib/media-proxy-range` から、`GET` は route.ts から(既存 route テストと同じ方法)。
- 純関数テーブル: 上表の各行 + 境界(長さ == 8388608 不変、8388609 は切詰め)+ 前後空白 trim + overflow guard(`bytes=9007199254740000-` 不変、`bytes=9007199254740992-` 不変、`bytes=0-9007199254740992` 不変)。
- ルートテスト(fetch を `vi.stubGlobal` で mock):
  - 経路 A + `bytes=0-` → media fetch の Range が `bytes=0-8388607`、`X-API-Key` あり、upstream 206(`content-range: bytes 0-8388607/52676203`、`content-length: 8388608`)が status/ヘッダ/本文とも passthrough。
  - 経路 A + `bytes=0-65535` → Range 不変、206 passthrough。`bytes=0-99999999` → `bytes=0-8388607`。`bytes=-1024` → 不変。
  - 経路 A + Range なし → media fetch の headers に Range キーなし、upstream 200 passthrough。
  - 経路 A + upstream 416(`content-range: bytes */52676203`)→ 416 とヘッダを passthrough。
  - 経路 B `recordings/42/media/7/raw` + `bytes=0-` → upstream fetch の Range が `bytes=0-8388607`、`audio/webm` 206 passthrough。
  - 対象外経路(route.ts で `headers['Range']` を使う既存の mp3 取得経路。実パスは route.ts の分岐と `lib/api.ts` の呼出から確認し、テスト内コメントに根拠を記す)+ `bytes=0-` → Range 不変。同経路 + `bytes=0-8388607` → 不変。
  - cookie なし(`vi.mocked(cookies)` の `get` が undefined)+ 経路 A → 401、fetch 未呼出。
- 既存テストは削除・skip・期待値変更なし。

### Step 4: ローカル検証(commit 済み tree に対して実行。順序厳守: commit → 検証)

```
cd services/dashboard
npm ci --no-audit --no-fund   # 未インストール時のみ
npm run generate-release-version
npm test                       # 全緑
npx tsc --noEmit
npx eslint . --format json --output-file /tmp/eslint-report.json ; node scripts/ci/lint-ratchet.mjs /tmp/eslint-report.json lint-baseline.json
npm run build                  # route.ts の不正 export があればここで落ちる
cd ../.. && bash .hw/verify.sh
```

出力は `evidence/test-output.txt`、`evidence/tsc-lint-build.txt`、`evidence/verify-output.txt` に保存。`lint-baseline.json` は変更しない。既存 suite の基準(テスト数・期待値・lint 上限)を緩めない。

### Step 5: GitNexus

- 本 worktree の index は生成済み。編集前 `impact({target:'proxyRequest', direction:'upstream'})` は保存済み(`evidence/impact.txt`: MEDIUM、直接呼出 5 = GET/POST/PUT/PATCH/DELETE、1 module、processes 0)。
- index 構築は process 探索に truncation があるため、HTTP 越しの呼出と動的呼出は text で裏取りし同ファイルに追記する: route.ts の `rangeHeader` 使用箇所(2 箇所)、`mediaHeaders` の組立箇所、`lib/api.ts` で経路 A/B の URL を生成する箇所(調査時 549/602 付近の raw フォールバック src と master proxy src)、mp3 取得経路の URL 生成箇所。
- commit 前に `detect_changes()` を実行し `evidence/detect_changes.txt` に保存。結果が partial/truncated なら complete になるまで再実行し、complete の結果のみ採用する。想定外の symbol/flow が含まれたら commit せず原因を記録する。

### Step 6: フェーズ

| Phase | 実行役 | 内容 | 成果物 |
|---|---|---|---|
| A 実装・検証 | Opus 5 implementer | Step 1〜5。コード + テストを commit してから検証し evidence を commit | `evidence/**` |
| B レビュー | Fable(read-only) | clean tree で `python3 .hw/fable_review.py fix-cloudrun-recording-range-20260907`。審査対象は契約の「コード契約」表のみ。`claude auth status` が sandbox で false なら制限外で再確認。violations のみ修復 → 再 commit → 再レビュー | `review-verdict.json` |
| C レビュー後手続き | implementer / 人間 | READY 記録(差分 hash・契約 hash 束縛)、`bash .hw/hooks/pr-ready-gate.sh fix-cloudrun-recording-range-20260907` が `ready`、PR(日本語)作成。本文に契約 ID・evidence パス・反映予定 SHA・revert 先 revision を列挙。マージはしない | `.hw/gates/<task>/pr-ready.json`、PR |
| D リリース | implementer(人間承認下) / 人間 | Step 7。Phase C 完了後の別段階。反映後確認の未実行をコードレビュー違反にしない | `evidence/release/**` |

### Step 7: 本番反映(Phase D。Cloud Run image 限定反映。環境変数・.env・compose は一切触らない)

前提: AT-001〜013 緑、Fable READY、pr-ready-gate `ready`、PR 作成済み。反映する SHA は READY に束縛された commit SHA(以下 VERIFIED_SHA)。main 未マージのまま本番 image が本ブランチ SHA になる点を PR 本文に明記する。

1. ロールバック元を記録: `gcloud run services describe kabosu-dashboard --project pm-qe-mgmt-20260624 --region asia-northeast1 --format=json | jq '{revision: .status.latestReadyRevisionName, image: .spec.template.spec.containers[0].image, envNames: [.spec.template.spec.containers[0].env[].name], ports: .spec.template.spec.containers[0].ports}'` → `evidence/release/revision-before.json`(期待: `kabosu-dashboard-00069-7q8`、image tag `d7a4d729…`。異なれば実値を記録し、それを revert 先にする)。env の値は保存しない。`ports` に h2c があれば反映を止め人間へ(RF-001)。
2. 反映: VERIFIED_SHA の clean checkout(`git status --porcelain` 空、`git rev-parse HEAD` = VERIFIED_SHA)から、`deploy-dashboard-gcp.yml` の step と同一の `gcloud builds submit --config cloudbuild-dashboard.yaml …` を実行する。substitution は workflow と同じキーのみ(`_DEPLOY_SHA` = VERIFIED_SHA、`_VEXA_API_URL` は describe の値)。workflow に無い引数を足さない。`--set-env-vars`/`--update-env-vars`/`make -C deploy/compose up` は禁止。実行コマンド(秘密値を除く)と Cloud Build ID を `evidence/release/commands.txt` に記録。
3. 反映後: 同じ describe を `evidence/release/revision-after.json` に保存。image tag が VERIFIED_SHA、`envNames` と `ports` が before と一致。
4. HTTP 確認(ログイン cookie を使う。cookie 値は evidence に残さない): `curl -sS -o /dev/null -D - -H 'Range: bytes=0-' <dashboard>/api/vexa/recordings/841188337344/master?type=audio&proxy=1` → 206、`Content-Range: bytes 0-8388607/52676203`、`Content-Length: 8388608`。同様に `bytes=30000000-` → `bytes 30000000-38388607/52676203`、`bytes=52676000-` → `bytes 52676000-52676202/52676203`、`bytes=0-65535` → 206/65536 bytes で SHA256 が調査時の backend 値と一致 → `evidence/release/http-checks.txt`。
5. ブラウザ確認: 当該ミーティングを開き、先頭再生 10 秒以上 → 中央付近へ seek して 10 秒以上 → 終端 30 秒前へ seek して終端まで。エラー表示なし。DevTools Network の 206 連鎖(Content-Range の先頭値)を要約し、スクリーンショットと共に `evidence/release/browser-check.md`。同画面で WebM ダウンロードが 100% 完了することも確認。
6. ログ確認: `gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="kabosu-dashboard" AND resource.labels.revision_name="<新 revision>" AND textPayload:"Response size was too large"' --project pm-qe-mgmt-20260624 --freshness=2h` が 0 件 → `evidence/release/cloudrun-log.txt`。
7. 失敗時ロールバック: `gcloud run services update-traffic kabosu-dashboard --project pm-qe-mgmt-20260624 --region asia-northeast1 --to-revisions <before の revision>=100`。実行したら `commands.txt` に記録し人間へ報告。
8. release evidence を commit し PR 本文に追記。マージは人間の判断に委ねる。

## リサーチ記録(確信度と覆る条件)

- RF-001 Cloud Run 上限: 2026-09-07 に公式 quotas を確認。HTTP/1 応答は chunked/streaming を除き 32MiB 上限。確信度: 高。覆る条件: サービスが h2c ポートで HTTP/2 end-to-end になっている場合(Step 7-1 の `ports` で確認)。
- RF-002 ブラウザは要求より短い 206(正しい Content-Range 付き)を受け取ると続きを再要求する。Chrome は確信度 高、Safari/Firefox は 中。覆る条件: Step 7-5 で 206 連鎖は正しいのに seek が失敗する → 人間へ。代替は Why 節参照。
- RF-003 Next.js App Router の route.ts は HTTP メソッド等の許可された export 以外の named export を build エラーにする。確信度: 高。覆る条件なし(Step 4 の `npm run build` で機械確認)。

## Why(実装者に渡さない)

- 主因は route.ts が `Range: bytes=0-` をそのまま backend raw に転送し、backend が EOF まで(52MB、Content-Length 付き)返し、Next がそれをそのまま返すため Cloud Run の 32MiB 上限で 500 になること。Cloud Run システムログ「Response size was too large」と trace が一致。小さい明示 Range(0-65535)は 206 で成功し SHA も一致するので、backend・認証・GCS は主因でない。
- 純関数を別ファイルに置くのは、route.ts の named export が build で拒否されるため。テストが直接叩ける場所として lib に出す。
- 適用を経路 A/B に限定するのは、依頼が録音再生の修正であり、mp3 取得や非メディア API の Range に上限をかける根拠がないため。`rangeHeader` を一括で差し替えると mp3 経路にも効いてしまうので、代入元ではなく使用箇所で分岐させる。
- 8MiB を固定にするのは、設定要望がなく、env で可変にすると誤設定で 32MiB を突破する経路が増えるため。8MiB は backend の `RECORDING_STREAM_WINDOW_BYTES` 既定と `downloadRecordingInChunks` の chunk と同値で、既存の明示 8MiB Range を不変に保つ境界として自然。
- suffix・複数範囲・不正値を触らないのは、意味を変えずに切り詰める方法が無い(suffix は起点が変わる)ため。独自規約を足すより upstream の 416 判断に委ねる方が退行リスクが低い。
- 和の overflow guard を明示するのは、`S + max - 1` が safe integer を超えると文字列化で精度落ちした範囲を送ってしまうため。その場合は無変更で upstream に委ねる。
- 代替案「Content-Length を落として chunked 転送にし上限を回避する」は Cloud Run 仕様上は成立するが、Node/Next の転送符号化挙動に依存し単体テストで保証しにくい。Range 切り詰めは HTTP 意味論の範囲内で決定的に検証できるので採用。RF-002 が覆った場合の次善策として保持。
- 非 Range 要求の 32MiB 超 200 を据え置くのは、206 を Range 無し要求に返すのは RFC 違反、200 で本文を切るのは Content-Length との不整合になるため。全クライアントが必ず Range を送るとは断定しない。再現トレースが Range 付きであることだけを根拠にし、Range なし要求の失敗が観測されたら別タスクで扱う。
- マージを自動実施しないのはユーザーが明示していないため。反映は「修正依頼」に基づく image 限定反映として、検証済み SHA をタグにした既存 cloudbuild の同一手順に限定し、env 保持とロールバック先 revision の明確さを保つ。
- 「全ての Range 応答が 8MiB 以下」と約束しないのは、suffix・不正値・Range なしを据え置くため。契約は対象経路への有効な先頭/途中開始の単一 byte range に限定する。