---
generated_by: codex
task_id: app-loading-recording-refactor-20260905
planning_exception: user_explicitly_authorized_codex
source_commit: 67ea03210c2de4c8723780402d302948b138d939
status: plan_only
language: ja
---

# 読み込み・会議一覧・録音再生のリファクタリング計画

**目的：初期表示と終了済み会議一覧の待ちを増やす重複処理、履歴を欠落させるエラー処理、音声配信の全量buffer、非同期処理の競合を、小さな検証可能な変更で解消する。今回は計画のみで、アプリのコード・テスト・設定は変更していない。**

この文書は単独で実行仕様として読める。plan.mdだけを受け取った場合の必須補助ファイル復元手順も末尾付録に含む。構造マップや会話履歴を読まなくても、下記の対象・順序・期待値・中止条件に従える。補助資料は同じディレクトリの `structure-map.html` / `investigation.md` / `source-inventory.json` / `graph-evidence.json`。

## 1. 現状理解と実行範囲

### 基準と権限

- 調査HEADは `67ea03210c2de4c8723780402d302948b138d939`。対象行範囲はすべてこのHEADのもの。前項で行番号が動いた場合は、併記したシンボル・処理内容で同定する。旧行番号だけで編集しない。
- 元workspaceには `AGENTS.md` / `CLAUDE.md` の未コミット変更がある。調査では触れていない。実行者もそれをreset/stash/commitせず、R00で独立worktreeを作る。
- Fableは認証済みだが組織の月間利用上限で起動できなかった。ユーザーは後続メッセージ「codexで完成させて。」で**今回の計画作成者をCodexとする例外**を明示承認した。`generated_by: fable`とは偽記しない。
- この承認は `.hw` の機械ゲート改変や実装後のFableレビュー省略の承認ではない。現行 `pr-ready-gate.sh` はFable生成frontmatterを要求する。R00の引継ぎ手順でFableによる実際の再発行を行う。利用制限が続く場合は実装開始前に「計画は完成、実行環境の前提が未充足」と報告して中断する。ゲートの書換え・名前だけの付替えは禁止。
- 総計画はL（複数サービス・認証・streaming寿命を横断）。R軸はprime（12項目の変更と検証は数時間を超える見込み）。R00の準備は引継ぎ担当が行い、R01以降は下記の既存Prime起動手順を使う。一度に一項目だけ実施し、並列実装しない。Primeが未導入/利用不能なら勝手に基盤を変えず中断する。

### このコードが実現していること

Vexaは会議にBotを参加させ、音声・映像・文字起こし・会議状態を保存し、Dashboardから閲覧・再生・検索するアプリである。

|層|正本となるファイル|役割 / 次の依存先|
|---|---|---|
|画面入口|`services/dashboard/src/app/layout.tsx`、`components/layout/app-layout.tsx`|Next.js/Reactの共通shell。AuthProviderで保護画面の描画を制御。|
|認証|`components/auth/auth-provider.tsx`、`stores/auth-store.ts`、`app/api/auth/me/route.ts`|Cookieを使ってGateway `/auth/me`へ確認。必要時のみ既存shared-login / OAuthフロー。|
|一覧|`app/meetings/page.tsx` → `stores/meetings-store.ts` → `lib/api.ts`|一覧・フィルター・無限scroll。raw APIの数値ID/native_meeting_idをUIの文字列ID/platform_specific_idに変換。|
|Dashboard BFF|`app/api/vexa/[...path]/route.ts`|ブラウザと同じoriginのAPI。CookieからX-API-Keyを付ける。一覧はGateway `/bots`へ変換する。|
|Gateway|`services/api-gateway/main.py`|Admin APIで認証・scope確認後に内部APIへ転送。HTTPとWebSocketは別経路。|
|会議データ|`services/meeting-api/meeting_api/meetings.py`、`models.py`、`database.py`|PostgreSQLのMeetingが会議の正本。`data`はJSONB。`/bots`は全状態の履歴、`/bots/status`は稼働中コンテナ情報で同じ集合ではない。|
|会議実行|`services/runtime-api/runtime_api/`、`services/vexa-bot/core/src/`|コンテナを起動し、各会議サービスに参加、収録・STT・終了通知を実行。|
|録音生成|`meeting_api/recordings.py`、`callbacks.py`、`recording_finalizer.py`、`sweeps.py`|断片を保存しmasterを生成。完成後に`Meeting.data.recordings[].playback_url`を設定する。|
|録音閲覧|`use-meeting-playback.ts` → BFF `/master?proxy=1` → Gateway → `recordings.py` → `storage.py`|masterメタ情報を解決し、認証付きraw配信を中継。storageはlocal/MinIO-S3/GCS。MP3 downloadは別の変換経路。|
|文字起こし|Bot/STT → Redis → `meeting_api/collector/` → DB/WS → `packages/transcript-rendering`|RESTとWebSocketの情報をmanagerが正規化して画面に統合する。|
|補助サービス|Calendar、wake-stt/orchestrator、TTS、voiceprint、Agent API、MCP、Telegram|自動参加・呼びかけ・発話・話者推定・AI操作等。今回の変更対象外。|

Dashboard関連の相対パスはすべて `services/dashboard/src/` を起点としたもの。実際の変更対象は各項の完全パスを使う。

**実行者が保つデータ契約**

1. 会議の`completed`と録音masterの完成は別状態。master 404を壊れた会議・空の録音と断定しない。
2. 一覧は50件単位、`limit + 1`から`has_more`を算出。UIでredacted行を隠してもoffsetは元のpage単位で進む。ID重複除去と検索・状態・platform条件を維持する。
3. `/bots`は要約data、`/bots/id/{id}`は完全data。`?include=data`はバックエンドに既存の互換オプション。BFFに新しく露出させない。
4. 音声再生はcanonical masterを使用。rawはmasterの配信経路であり、未完成の任意chunkを勝手につなぐ機能を追加しない。lane音声は公開しない。
5. raw/MP3は200・206・416、Range/Content-Range/Content-Length/Accept-Rangesを保つ。rawの8MiB窓読み・to_thread、MP3の180秒timeout、GCS署名不能時のraw fallbackは既存機能。
6. APIキー・Cookie・署名URL・録音内容をログやテスト成果物に保存しない。ユーザー所有者条件とscope確認を省略しない。
7. wire/UIの名前・ID型の差は互換adapterであり、全体renameの対象ではない。

### 調査済みの事実と未検証事項

コード・テスト・設定928ファイル（190,734行）を機械走査し、重要な要求経路と既存テストを本文で確認した。全ファイル全行を意味解釈した完全監査ではない。GitNexus索引は基準HEADと一致。HTTP/動的dispatchはgraphだけでは欠けるため本文で補完した。

|根拠|事実|今回の対応|
|---|---|---|
|F01|一覧mountのeffectが2本で同じ取得を起動|R01|
|F02|一覧障害時に稼働中だけの集合を200で返す。fallbackにtimeoutなし|R02|
|F03|Gatewayが`resp.content`で音声全量を読んでから返す|R03|
|F04–05|master→downloadでDB検索重複。metadata用storage SDKがevent loopをblock|R04|
|F07|詳細の古い失敗・文字起こし・chat応答が新しい会議に反映されうる|R05|
|F06・18|詳細pollingの重なり、bootstrap/chatの重複、共通pollerのreject漏れ|R06|
|F08|録音配列の参照だけでURL再取得、audio/video errorの上書き|R07|
|F09|audio要素の1500ms自動retryに上限なし|R08|
|F10|GatewayとBFFで認証基盤の障害をinvalid tokenに変換。クライアントdeadlineなし|R09・R10|
|F11|一覧は全JSONB読取後に要約。DB projection・index効果は未計測|R00で診断方法固定、改修は別計画|
|F12|一時エラー判定重複|今回の変更で必要な箇所以外は保留|
|F13–17|命名差、旧helper候補、557/839/613/504行の巨大関数|削除や全面分割はしない|

本番HAR・エラー率・DB実行計画・実配備HEADは取得していない。したがって「何％高速化する」「今回で本番障害がすべて解消する」は合格条件にしない。リクエスト数、最初のbyteの返却順序、同時実行数、誤った状態遷移を再現可能なtestで保証する。

### 効果 × リスクと実行順

数値は実測スコアでなく相対評価。依存のないものも以下の順で実施する。

|順序/ID|狙う効果|変更リスク|先行ID|
|---|---|---|---|
|0 / R00|元に戻せる基準・正常動作の固定|低（コードはtestのみ）|なし|
|1 / R01|初回一覧の重複要求削減|低|R00|
|2 / R02|終了済み履歴の偽の空表示を防ぐ|中（BFFエラー契約）|R01|
|3 / R03|音声の全量待ちとサイズ比例メモリの除去|高（共通関数graph CRITICAL、61直接caller）|R02|
|4 / R04|master解決DB往復・SDK block削減|中|R03|
|5 / R05|旧会議の録音・text・error流入防止|中|R04|
|6 / R06|遅い取得の積み上がり防止|中|R05|
|7 / R07|同値録音の再取得抑止・音声/映像障害分離|中|R06|
|8 / R08|音声要素の無限retry停止|低〜中|R07|
|9 / R09|認証基盤停止と無効tokenの区別|高（resolverへの波及）|R08|
|10 / R10|初期認証待ちの有界化・再ログインloop防止|中|R09|
|11 / R11|検証結果とレビュー対象をcommitへ固定|低（証拠のみ）|R00–R10|

Graph補足：Next proxyRequestはMEDIUM/5直接caller、useMeetingPlaybackはLOW/1直接caller、download_media_fileはLOW/1直接caller。`_resolve_token`はMEDIUM判定だが73影響シンボル（直接6）なので保守的に高リスク扱い。fetchMeetings/checkAuth/fetchTranscriptsとFastAPI一覧handlerはUNKNOWNまたは名前解決不成立。ゼロ件を安全宣言に使わない。

## 2. 共通の実行・検証・戻し方

### コミットと実行場所

- 以後のコマンドは、特記以外は**R00で作る実行worktreeのroot**で実行する。
- 一項目の実装とその回帰testだけを一つの候補commitに含める。検証はそのcommitのclean treeで実行する。失敗時は次へ進まず、同項目内を修復し未pushの候補commitをamendして、clean treeで同じ検証を再実行する。採用されるcommitは一項目一つ。
- 各編集前に対象symbolへGitNexus upstream impact。HIGH/CRITICALは報告して範囲を限定する。今回の警告があるから次のHEADでの再確認を省略しない。UNKNOWNはテキスト参照・route・テストを照合する。
- commit直前に `node .gitnexus/run.cjs detect_changes --scope all --repo generic_tldv`。CLIがその表記を受け付けなければ`--help`で確認し、実在する`detect-changes --scope all`を使う。partial/truncated/解析失敗をpass扱いしない。最新索引が必要な場合は`node .gitnexus/run.cjs analyze --index-only`を使用し、コード・AGENTS等の書換えがないことを確認する。新worktreeでrepo名が異なる場合は`status`/`list`に表示された**実行worktreeの**識別子を使い、元workspaceのgraphを参照しない。
- test証拠はworktree外の`$EVIDENCE_DIR`に保存する。自動生成物のuntrackedが現れた場合はその実行原因を調べる。未知の生成物を消してcleanに見せない。必要な正式証拠ファイルのみR00で定義したpathへ最後にまとめる。

### コマンド束（名前はこの文書内の略号。shell関数を追加する指示ではない）

**D：Dashboard全体検証**

```bash
(cd services/dashboard && npm test)
(cd services/dashboard && npx --no-install tsc --noEmit)
(cd services/dashboard && VEXA_API_URL=http://127.0.0.1:8000 npm run build)
```

全test pass、型エラーなし、build exit 0。`prebuild`は既存local packageのdist同期とrelease-version生成を行う。trackedソース変更が発生したら止める。`VEXA_API_URL`はbuild時のダミー同一マシンURLで本番宛先ではない。build時に実backendへ接続しないことを確認。`.env*`に実credentialがある作業dirを使わない。

lintはCIと同じratchetを使用する（普通のeslint exit 1とcrashを区別）。

```bash
(cd services/dashboard
 set +e
 npx --no-install eslint . --format json --output-file "$EVIDENCE_DIR/eslint-report.json"
 ec=$?
 set -e
 if [ "$ec" -ge 2 ]; then exit "$ec"; fi
 node scripts/ci/lint-ratchet.mjs "$EVIDENCE_DIR/eslint-report.json" lint-baseline.json)
```

lint-baselineへの追加は禁止。既存失敗がR00から増えないこと。Dは各frontend項目の最終clean commitで実行。

**G：Gateway全体検証**

```bash
"$PLAN_PY" -m pytest services/api-gateway/tests/ -v
```

全test pass、exit 0。`.github/workflows/test-api-gateway.yml`の環境をR00で用意する。

**M：Meeting API検証**

```bash
"$PLAN_PY" -m pytest services/meeting-api/tests/ -v --ignore=services/meeting-api/tests/test_integration_live.py
```

既存CIの非live suite。既存の明示opt-in integration skip以外、新しいskip/xfailを認めない。R00で既存pass/fail/skipを記録し、failureがあれば実装を止める。`RUN_POSTGRES_INTEGRATION_TESTS`を有効化する追加DB検証はR00の分離DBでのみ実行。

**H：プロジェクト共通ゲート**

```bash
bash .hw/verify.sh
```

exit 0、既知baseline外の失敗ゼロ。`.hw/verify-baseline`は変更しない。rootの`make build`（publishを含む）、`make up`、deploy、`make full`、実会議へのsmokeは使用しない。

**各項目共通の完了条件**：指定の対象test + 適用束(D/G/M) + H + `git status --porcelain`空。現在のコードには存在しないtestは下記で「新設」と明記する。testファイル全体を指定したコマンドに加え、本文のtest名が収集・実行されることを確認し、0 testsをpass扱いしない。

### 回復手順（各項の「共通revert」はこれを指す）

```bash
git status --porcelain
# 空であることを確認。直前の失敗項目がHEADのときだけ実行する。
FAILED_COMMIT="$(git rev-parse HEAD)"
git show --stat --oneline "$FAILED_COMMIT"
git revert --no-edit "$FAILED_COMMIT"
```

作業を止めた時点の**直前一項目だけ**を対象に、clean・未公開branchで実行する。後続を実施していないため依存が壊れない。revert後にその項の検証束を再実行し、戻せたことを報告する。古い項目だけを後からrevertする操作は禁止。採用後の不具合なら後続依存項を逆順に戻す必要があるため、対象commit列と影響を報告して中断する。`reset --hard`・force push・元workspaceの変更破棄は禁止。

## 3. R00 — 安全網とベースライン（最初の1コミット）

**対象**：基準HEAD全体、`.hw/plans/app-loading-recording-refactor-20260905/`、新設する`services/dashboard/tests/test_loading_characterization.test.tsx`、`services/api-gateway/tests/test_media_contract_characterization.py`、`services/meeting-api/tests/test_read_contract_characterization.py`。既存sourceは変更しない。

**問題**：今の正常契約と遅延・競合の再現手段が一つにまとまっていない。元workspaceはdirtyであり、そこで変更を開始すると他作業を混ぜる。

**変更・手順**：

1. plan.mdだけが渡された場合は、付録9に従ってこの文書と必須補助ファイルを元rootの指定planディレクトリに保存する。HTML/調査索引は実装に必須ではなく、不在なら再生成しない。元rootで`git rev-parse HEAD`と`git status --short`を記録。HEADが上記SHAでなければ、差分をこの文書の対象と照合して計画を更新するまでは実装しない。
2. 新しいpath/branchでworktreeを作成する。既に存在したら再利用しない。

```bash
BASE=67ea03210c2de4c8723780402d302948b138d939
PLAN_SOURCE="$PWD/.hw/plans/app-loading-recording-refactor-20260905"
WORKTREE_DIR="$(dirname "$PWD")/generic_tldv-loading-refactor"
git worktree add -b refactor/app-loading-recording-20260905 "$WORKTREE_DIR" "$BASE"
mkdir -p "$WORKTREE_DIR/.hw/plans/app-loading-recording-refactor-20260905"
cp -R "$PLAN_SOURCE/." "$WORKTREE_DIR/.hw/plans/app-loading-recording-refactor-20260905/"
cd "$WORKTREE_DIR"
EVIDENCE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/vexa-loading-evidence.XXXXXX")"
export EVIDENCE_DIR
```

pathへの書込み権限が必要なら環境の承認手順を使う。既存AGENTS/CLAUDEのdirtyはcopy/commitしない。この計画内の現行ルールも実行指示として引き継ぐ。

3. **機械ゲートとの整合**：Fable復旧後に`claude auth status`（Keychain制限時は許可された制限外環境で再確認）を確認。Fable plannerへこの計画とコードをread-onlyで渡し、「How/契約/順序の再点検後、全内容を自分の責任で再発行。変更するなら勝手に実装せず差分を提示」と指示する。出力を実際に受け取れたときだけ、Codex原稿を`codex-plan.md`として保管し、Fable本文を`plan.md`に保存して`generated_by: fable`とする。`base-commit`はFable開始時HEADを記録する。生成できない/意味が変わる指摘が出たらここで停止し報告。フック・reviewer名・gateを偽装しない。
4. **環境**：Node 20（既存CI）、Python 3.11（既存CI）。rootの`.env`をsourceしない。既存lockfileからDashboardとtranscript-renderingを`npm ci --no-audit --no-fund`で用意する。順番は`packages/transcript-rendering`でci→build、`services/dashboard`でci→`npm run sync-packages`→`npm run generate-release-version`。npm lockfileの変更を許さない。
5. 一時venvを`$EVIDENCE_DIR/venv`に作り、`PLAN_PY="$EVIDENCE_DIR/venv/bin/python"`をexport。既存CI同様に`pip install -e libs/admin-models/ -e services/meeting-api/ -r services/api-gateway/requirements.txt`と`pytest pytest-asyncio httpx psycopg2-binary`をインストール。Pythonはlockされていないため既存CI/利用可能なbaseline環境のversionを優先し、`pip freeze`を保存。同じvenvを全項で使う。依存不整合が出たらrequirements更新で解決せず中断する。
6. test用のdummy環境をexportする：`DB_HOST=127.0.0.1 DB_PORT=5432 DB_NAME=test_db DB_USER=test_user DB_PASSWORD=test_pass DB_SSL_MODE=disable REDIS_URL=redis://127.0.0.1:6379 ADMIN_TOKEN=test-admin-secret ADMIN_API_URL=http://admin-api:8001 MEETING_API_URL=http://meeting-api:8080 TRANSCRIPTION_COLLECTOR_URL=http://meeting-api:8080 MCP_URL=http://mcp:18888`。通常testはmockを使用。実localhostサービスを前提とするtestはfixtureで接続先を確認する。これらの値で本番DBに接続しない。
7. docs copy前後のコード差分がゼロであることを確認後、R00の特性testを追加する。既存不具合を「期待仕様」として固定するtestは追加しない。失敗する改善後testは対応R項のcommitで追加する。
8. GitNexusの変更検査後、引継ぎ文書とR00のtestだけを明示pathでaddして、`test: 読み込み改修前の正常契約を固定`としてcommit。これが**作業前基準commit**。`git rev-parse HEAD`を外部証拠の`baseline-commit.txt`へ保存。以後このSHAを`BASELINE_COMMIT`として扱う。
9. clean treeでD/G/M/Hを実行。全体検証の既存failureや環境不足がある場合は、失敗test名・環境・stdout/stderrを報告し中断。テスト削除・skip・baseline追加で進めない。

R00の環境準備コマンド（上記4–6を実行する具体形。test実行前に設定する）：

```bash
(cd packages/transcript-rendering && npm ci --no-audit --no-fund && npm run build)
(cd services/dashboard && npm ci --no-audit --no-fund && npm run sync-packages && npm run generate-release-version)
python3.11 -m venv "$EVIDENCE_DIR/venv"
export PLAN_PY="$EVIDENCE_DIR/venv/bin/python"
"$PLAN_PY" -m pip install -e libs/admin-models/ -e services/meeting-api/ -r services/api-gateway/requirements.txt
"$PLAN_PY" -m pip install pytest pytest-asyncio httpx psycopg2-binary
"$PLAN_PY" -m pip freeze > "$EVIDENCE_DIR/pip-freeze.txt"
export DB_HOST=127.0.0.1 DB_PORT=5432 DB_NAME=test_db DB_USER=test_user DB_PASSWORD=test_pass DB_SSL_MODE=disable
export REDIS_URL=redis://127.0.0.1:6379 ADMIN_TOKEN=test-admin-secret
export ADMIN_API_URL=http://admin-api:8001 MEETING_API_URL=http://meeting-api:8080
export TRANSCRIPTION_COLLECTOR_URL=http://meeting-api:8080 MCP_URL=http://mcp:18888
```

R00をcommitして全条件を満たした後、同じ隔離worktree内で次を実行する。

```bash
export BASELINE_COMMIT="$(git rev-parse HEAD)"
printf '%s\n' "$BASELINE_COMMIT" > "$EVIDENCE_DIR/baseline-commit.txt"
HW_PRIME_WORKTREE=0 python3 .hw/prime_run.py app-loading-recording-refactor-20260905
```

`HW_PRIME_WORKTREE=0`は既存実装が持つ設定。**R00で既に隔離したworktree内だけ**で使い、二重の作業dirと環境コピーを避ける。停止gateは既定のpr-ready-gateを維持する。開始前にR00 commitとその検証証拠を実行者へ渡し、R00を繰り返さずR01から継続させる。再開時も最後に合格したcommitと証拠を確認して次項から進む。未完了項目はその項目で停止し、実行時間上限を理由に完了と扱わない。親担当がFableの採用/レビューを行い、実行役はREADYを自己宣言しない。`EVIDENCE_DIR`と`PLAN_PY`を同じshellから引き継ぐ。継続セッションでは記録した外部証拠pathを再exportする。

### R00で新設する特性test仕様

UIは既存vitest/jsdom/React DOMを使用。`// @vitest-environment jsdom`、`createRoot`、`act`、`IS_REACT_ACT_ENVIRONMENT=true`を使い、testing-libraryを新規導入しない。`next/navigation`、IntersectionObserver、HTMLMediaElement.play/load/pause、API呼出しをmock。storeのmodule状態は`vi.resetModules()`と再importで分離。unmount・fake timer解除・globals復元をafterEachに置く。

|test名（そのまま付ける）|入力|期待出力・観測点|
|---|---|---|
|`R00 list preserves page cursor after redaction`|API page50行。ID1–50、うちID1,2をdata.redacted=true。has_more=true。次pageID51–55。|表示48→53行。2回目のAPI offset=50。ID重複なし。完成会議は残る。|
|`R00 list maps wire identity without changing order`|raw id=42、native_meeting_id='abc-defg-hij'、status=completed。created_at異なる3会議。|UI id='42'、platform_specific_id一致、created_at降順。has_moreを維持。|
|`R00 master keeps same origin and not ready semantics`|master200にraw_urlとduration=12.5、別case404。|200→`/api/vexa/recordings/42/master?type=audio&proxy=1`・12.5、404→null。直接S3 URLをsrcにしない。|
|`R00 playback keeps fragment seek coordinates`|session-a=12秒、session-b=20秒。bのsegment start=3、session_uid=b、absolute時刻付き。|audio.seekToFragment(1,3)、video.seekTo(15)、virtual playbackTime=15。通常単一fragmentならseekTo(3)。|
|`R00 auth never trusts persisted credentials`|旧localStorageにuser/token/isAuthenticated=true。server401。|server検証前は認証済みにならず、401後user/token=null・isLoading=false。|
|`test_R00_raw_range_preserves_headers`|Gateway通常転送へfake backend status206/body=b'abc'、Content-Range='bytes 2-4/10'、Content-Length=3、Accept-Ranges=bytes。|status/header/body一致。最初のbyteのタイミングはここではassertしない。|
|`test_R00_recording_owner_boundary`|owner user5の録音42、他user6認証。SQLにuser_id=6条件があることを検査するDB spyは該当なしを返す。|404。storage.file_exists/download/get_presigned_urlは0回。owner5では同fixtureで取得可。mockが所有者条件を無視しない。|
|`test_R00_list_summary_and_include_data`|make_meetingにname='手動名'、calendar_event.title='定例'、participants=['A','B','C','D']、notes='あ'×121、status_transition配列2件、recordings1件。|通常はname/ calendar_title/participants先頭3/count4/notes_preview120文字/last_transition末尾/has_recording=true。include='data'時は元dataと完全一致。foreign user条件・limit+1・offsetがSQLに残る。|

Gateway fakeは`httpx.AsyncClient`にMockTransportを注入し、`_resolve_token`だけをfixtureでauth成功にする。API層の実forward関数を置換しない。R00はbody受信完了後の契約を固定するので基準実装でも通る。Meeting APIは既存conftestのmake_meeting/make_user/MockResultを使う。

**R00完了コマンド**：D/G/M/Hに加え、下記が収集され全pass（5 frontend cases、1 gateway case、2 meeting cases。parametrizeによる増加は可）。

```bash
(cd services/dashboard && npm test -- tests/test_loading_characterization.test.tsx)
"$PLAN_PY" -m pytest services/api-gateway/tests/test_media_contract_characterization.py -v
"$PLAN_PY" -m pytest services/meeting-api/tests/test_read_contract_characterization.py -v
```

注意：GatewayとMeeting APIはconftest/環境の干渉を避け、上記どおり**1サービスずつ別pytest processで実行**する（同一processで両サービスのappを混在させない）。

### ベースラインの性能診断方法

実装の合否は各項の決定的testで判定。次の実測は別途同じ端末・同じfixtureで行い、測っていない値を記入しない。

- 一覧：mock APIで50件completedを返し、遅延0/1000/6000msの3条件、cold browser contextで各5回。mountから最初のカード表示、/meetings要求数、0件とerror表示を区別して記録。開発StrictModeの再mountは別ラベルで記録し、productionの1 mountと混ぜない。
- 音声：R03のFakeByteStreamに64KiBのchunkを4,096回（256MiB相当）生成させ、bodyを保存せずhash/byte数だけ計測。全量を用意したbytes fixtureは禁止。baselineは全量bufferとなる事実を記録し、変更後はfirst chunkの即時返却とpeak memoryの傾向を比較。絶対ms/RSSはCIノイズがあるため合否には使わない。
- DB診断（任意、実装項目に追加しない）：使い捨てPostgreSQL16の専用DBに同じschemaを作り、user5の会議1,000件＋user6の100件をseed。user5は750 completed/200 active/50 failed、created_atを1秒刻みで固定、name='定例'+id、recordings等のJSONBを各64KiBにする。実handlerが生成したSQLに対し`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`を、default50・completed50・offset500・search='定例'の4条件で各5回取得。初回とwarm4回を分ける。出力を見ずにindex追加やJSONB SQL置換へ進まない。この測定は本計画の完了条件ではない。

**リスク**：test環境が本番値を継承すること、既存dirtyを混ぜること。**戻し方**：実行branchのR00を共通revert。元workspaceには一切戻し作業不要。**依存**：なし。

## 4. 作業項目（R01以降、必ずこの順）

### R01 — 一覧の初回取得を一つにする

**対象**：`services/dashboard/src/app/meetings/page.tsx:93–146`（applyFilters、初回load/filter effects、debounceRef）。新設`services/dashboard/tests/test_meetings_initial_load.test.tsx`。

**問題**：mountでfetchMeetingsが2回起動し、遅い最初の要求も実行されたままになる。unmount後の検索debounceも残る。

**変更**：独立した`// Initial load`のeffectを削除し、既存のdropdown filter effectを唯一の初回取得元として残す。applyFiltersの引数・300ms検索debounce・取得条件・page数は変更しない。unmount専用cleanupでdebounceRefのtimerをclearし、transcriptSearchGenerationRefを増やして旧検索結果を無効化する。検索中のdropdown動作の全面再設計はしない。

```tsx
// before: mount時に両方実行
useEffect(() => { fetchMeetings(); }, [fetchMeetings]);
useEffect(() => { applyFilters(searchQuery, statusFilter, platformFilter); }, [statusFilter, platformFilter]);
// after: 上の独立effectを削除。filter effectと既存handlerを維持
useEffect(() => () => {
  if (debounceRef.current) clearTimeout(debounceRef.current);
  transcriptSearchGenerationRef.current += 1;
}, []);
```

R00で作ったbaseline commitを起点に、Fable plannerへread-onlyで「計画本文を再確認し、実装対象をR01–R10、最終証拠をR11として採用」と渡す。再確認できた場合に限り`base-commit`をその開始時HEAD（R00）へ更新し、`adoption.json`へ実際の開始HEAD・採用日時・元原稿のgenerated_by=codex・ユーザー承認文を記録する。本文の意味変更が必要なら実装せず報告。これにより大きいHTML/全体索引は最終コードレビューの差分に入らない。R01のcommitにはこの小さい計画起点更新も含める。

**新設test**：

- `R01 one foreground fetch per production mount`：初期search=''・status/platform='all'で通常mount、effectをflush、getMeetings未解決のまま要求1回。resolve後も1回。StrictModeの開発用再mountはこの数字に含めない。
- `R01 filters and search retain request values`：初期resolve後status=completed→1回追加。検索'定例'を入力し299msは追加なし、300msでsearch='定例',status='completed'の1回追加。transcript検索も既存条件どおり。
- `R01 unmount cancels pending search`：入力後100msでunmount、1秒進めても一覧/横断検索の新しい要求は0回。開始済み検索の解決でstate更新しない。

**完了**：`(cd services/dashboard && npm test -- tests/test_meetings_initial_load.test.tsx tests/test_meetings_store_refresh_race.test.ts tests/test_loading_characterization.test.tsx)` →上記testと既存競合/offset契約pass。D/H。

**リスク / 戻し方**：初期表示が取得されなくなる危険はmount testで検出。共通revertでR01だけ戻す。元のfilterやstore関数は残る。**依存**：R00。

### R02 — 一覧障害を稼働中だけの履歴に置き換えない

**対象**：`services/dashboard/src/app/api/vexa/[...path]/route.ts:125–183`（/meetings GET branch）。新設`services/dashboard/tests/test_meetings_proxy_contract.test.ts`。

**問題**：/botsの失敗・401・402を/bots/statusの200へ変換するため、履歴が消えたように見える。fallbackが無期限に待つ。

**変更**：/meetings branch内だけを置換する。`GET /bots`を1回呼び、稼働中だけのfallback分岐を削除する。`MEETINGS_LIST_TIMEOUT_MS=5000`をbranch用定数にする。AbortControllerとtimerをfetch開始前に作り、JSON/text読了まで同じsignalを使い、finallyでclear。cache='no-store'。認証用の`userToken || VEXA_API_KEY`という既存の一覧限定規則を維持する。

|upstream結果|BFF結果|
|---|---|
|200・meetings配列、has_more boolean/省略|200 `{meetings, has_more: has_more ?? false}`。成功列の内容を改変しない。|
|200だが不正JSON / meetings非配列 / has_moreがboolean以外|502 `{error:'Invalid meetings response'}`。空成功へ落とさない。|
|401/403/402/429/5xxを含む非2xx|statusを維持。JSON bodyなら維持、非JSONなら`{error:'Meetings request failed', status: <status>}`。429のRetry-Afterがあれば通す。|
|5秒deadline（headers待ち/JSON body待ち双方）|504 `{error:'Request timeout'}`。|
|接続例外|502 `{error:'Failed to load meetings'}`。内部URL/鍵を返さない。|

すべてCache-Control=no-store。/meetings branchでCookie削除・shared-login・追加retryはしない。通常のauthenticated proxy branch、録音URL選択、声紋body制限には触れない。

```ts
// before
if (!botsResp.ok) { /* /bots/status → { meetings: runningOnly } */ }
// after (骨格)
try {
  const upstream = await fetch(botsUrl, { headers, signal: controller.signal, cache: 'no-store' });
  // 非2xxはstatusを維持。成功bodyを上表のとおり検証。
} catch (error) {
  // controller.signal.abortedで504、それ以外は502
} finally { clearTimeout(timer); }
```

**新設test**：

- `R02 completed history survives successful proxy`：completed/active各1件、has_more=true、limit50/offset50/search='定例'/status=completed/platform=google_meetが/botsへそのまま渡る。fetchは1回。
- `R02 failures never fall back to running bots`：401/402/403/429/503をparameterize。statusが維持され、URLに/bots/statusがない。Cookieは削除されない。
- `R02 headers and body hangs both time out`：fetch未解決とresponse.json未解決の2case。fake AbortSignalがabort時にrejectするようにする。4999ms未完了、5000msで504、timer残数0。
- `R02 malformed success is an error`：不正JSON、meetings={}、has_more='yes'で502。空配列正常200は200のまま。
- `R02 store retains rows after proxy failure`：storeにcompleted42をseedしBFF503相当のVexaAPIErrorを返す。meetings42を保持しerrorを設定、isLoadingMeetings=false。402はsubscriptionRequired=true。

**完了**：`(cd services/dashboard && npm test -- tests/test_meetings_proxy_contract.test.ts tests/test_vexa_sensitive_proxy_auth.test.ts tests/test_recording_master_proxy_route.test.ts)` →全pass。D/H。

**リスク / 戻し方**：以前隠していた障害がerrorとして表示されるのは意図した修正。API形状や成功時データは変えない。共通revert。**依存**：R01。

### R03 — 録音バイナリ3ルートだけGatewayでstreamingする

**対象**：`services/api-gateway/main.py:322–419,780–818`（forward_request、download_recording_master_mp3_proxy、download_media_raw_proxy、download_media_mp3_proxy）。新設`services/api-gateway/media_streaming.py`、`services/api-gateway/tests/test_media_streaming.py`。既存`services/api-gateway/tests/test_recording_routes.py`の対象3routeのmock配線。加えて`services/dashboard/src/app/api/vexa/[...path]/route.ts:379–399`と`services/dashboard/tests/test_recording_master_proxy_route.test.ts`（直接raw/MP3の416ヘッダーのみ）。

**問題**：共通forward_requestがresp.contentを使用し、upstreamの全byte受信までdownstreamへ返さない。全routeを一律streamingへ変えると61callerへ影響する。

**変更**：forward_requestにkeyword-only `stream_response: bool = False`を追加。認証/scope/identity header除去/query/body転送処理は共通の現位置を維持する。False分岐は既存`client.request`とResponseを維持。Trueだけ`client.build_request`→`client.send(...,stream=True)`→専用ClosingStreamingResponseへ渡す。

opt-inは**GET `/recordings/{id}/media/{media_id}/raw`、GET `/recordings/{id}/media/{media_id}/mp3`、GET `/recordings/{id}/master/mp3`の3つだけ**。metadata用`/master`、`/download`、`/recordings`、DELETE、一般JSON/Agent/MCPはFalseのまま。rawは既存client timeout30秒、MP3二つは180秒を**build_requestのtimeout引数**に反映する。sendに不正なtimeout引数を渡さない。時間はsocket待ちに適用され、正常にchunkが届く長時間bodyに絶対30秒の打切りを追加しない。

```py
# before
resp = await client.request(method, url, **request_kwargs)
return Response(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))
# after: auth済みの同じ位置
if not stream_response:
    # 既存の上記2行を保持
    ...
upstream_request = client.build_request(method, url, **request_kwargs)
upstream = await client.send(upstream_request, stream=True)
return ClosingStreamingResponse(upstream)
```

`media_streaming.py`の責務はheader転送とupstream寿命だけ。

- raw byteの`upstream.aiter_raw()`を渡し、gzip等を復号したbyteと元Content-Lengthを混在させない。Content-Encodingがあればraw byteとともに維持。
- status（404/416含む）、Content-Type、Content-Length、Content-Range、Accept-Ranges、Content-Dispositionを保持。hop-by-hop header（connection/keep-alive/proxy-authenticate/proxy-authorization/te/trailer/transfer-encoding/upgrade、およびConnectionで列挙された名前）は除く。下流のframingはASGIサーバーに任せる。
- `.content`/`.read`/`.aread`/chunk全蓄積は禁止。prefetch queueを作らない。
- ClosingStreamingResponseはASGI `__call__`を`try: await super().__call__(...) finally: ...`で包み、finally内は既存FastAPI依存のAnyIO `CancelScope(shield=True)`内で`await upstream.aclose()`。これにより正常終了・途中read失敗・下流切断（最初のbyte前を含む）でcloseする。iteratorのfinallyだけに寿命を任せない。例外を握りつぶして成功完了に見せない。
- headers返却前のhttpx.RequestErrorは既存503。headers返却後のreadエラーは接続を終了し、安全なevent名/statusだけをlog。200を500へ後から差し替えたりbodyにJSONを継ぎ足さない。
- Response構築中の例外でもcloseする。共有app.state.http_client自体はrequestごとにcloseしない。

この選択はHTTPXのmanual streamingでResponseをcloseする契約と、AnyIOのcancel中のfinalization仕様に基づく。参照：[HTTPX async streaming](https://www.python-httpx.org/async/)、[AnyIO cancellation](https://anyio.readthedocs.io/en/stable/cancellation.html)。依存を更新せず、R00で実際に入ったversionの挙動をtestで固定する。

**新設test**（通常ASGITransportはbodyを蓄積しうるためfirst-byte検証には使わない）：

1. `test_R03_first_chunk_does_not_wait_for_tail`：httpx.AsyncByteStreamを継承したFakeByteStreamがb'abc'をyield、次はasyncio.Eventを待ってb'def'をyield。実forward_requestから受けたResponseをASGI scope/receive/sendで実行し、sendで最初のhttp.response.bodyを観測する。tailのEventを未解放のままb'abc'が来ることをassert。その後解放して合計b'abcdef'を確認。未解放待ちの上限1秒はdeadlock検出用で性能SLAではない。
2. `test_R03_range_and_raw_encoding_are_preserved`：206、Content-Range='bytes 2-4/10'、length3、accept-ranges、content-dispositionが維持される。別case gzip raw byte fixtureのbyte数/Content-Encodingが一致。Content-Lengthを推測再計算しない。
3. `test_R03_closes_upstream_on_all_exit_paths`：正常完了、最初のchunk後OSError、最初のchunk前のdownstream disconnect、chunk後disconnect、ASGI sendがOSError、外側task cancellationをparameterize。FakeByteStream.acloseの実資源解放counter=1（Response.acloseの冪等な再呼出しは可）、残留taskなし、tailを待ち続けない。AnyIO cancel scope内でも確認する。
4. `test_R03_streaming_does_not_prefetch_tail`：64KiBを最大4096回生成。downstream sendを1chunkで止め、生成済みchunkが1を超えないことをassert。tail全体のsizeを変えても先頭応答の条件は変わらない。
5. `test_R03_only_binary_routes_opt_in`：3routeだけclient.send(stream=True)、metadata/list通常routeはclient.request。MP3 request.extensions['timeout']の各値180、raw30を検証。
6. `test_R03_auth_and_error_contracts_stay_closed`：APIキーなし401、scope不足403、偽x-user-id除去、ownerがないupstream404、Range416+Content-Range='bytes */10'、接続エラー503。認証拒否時はstream send=0。

BFFのmaster二段proxyは既に416のContent-Rangeを渡すが、直接raw/MP3はcontent-typeがJSONの416を通常JSON分岐へ落とし、Content-Rangeを失う。BFFのbinary passthrough条件に、**GETかつ上記3routeに一致しresponse.status===416**だけを加える。同じheader allowlist/bodyをそのまま返す。その他のJSON/authエラー分岐には触れない。新規test `R03 direct and master proxy preserve unsatisfied range` をtest_recording_master_proxy_route.test.tsへ追加し、直接raw・直接media/mp3・master/mp3・master?proxy=1それぞれが416/body/Content-Range='bytes */10'/Accept-Rangesを保持することを確認する。JSON401は従来の認証分岐を通ることもassertする。

既存test_recording_routesの3routeだけ、`client.request.call_args`を見るmockを`build_request/send`の実経路を観測するmock/MockTransportに変更する。URL/method/query/Range/timeout/statusのassertionは同じか強いまま維持し、test削除・期待値の緩和はしない。非opt-in testは変更しない。

**完了**：`"$PLAN_PY" -m pytest services/api-gateway/tests/test_media_streaming.py services/api-gateway/tests/test_recording_routes.py services/api-gateway/tests/test_header_injection.py services/api-gateway/tests/test_media_contract_characterization.py -v` →全pass。加えて`(cd services/dashboard && npm test -- tests/test_recording_master_proxy_route.test.ts tests/test_vexa_sensitive_proxy_auth.test.ts)`、D/G/H。R00の256MiB診断で全量bufferがないことを補足記録。

**リスク / 戻し方**：CRITICAL。connection leak・壊れたRange・認証迂回をtestで閉じる。共通revertでopt-inと新helperを同時に戻す。Meeting APIには変更を要求しない。BFFの416条件も同commitで戻し、後続項目がまだ無い状態へ戻す。**依存**：R02。

### R04 — masterメタ情報の二重検索と同期SDK待ちを除く

**対象**：`services/meeting-api/meeting_api/recordings.py:708–757,795–876`（get_recording_master/download_media_file）。新設`services/meeting-api/tests/test_recording_metadata_resolution.py`。

**問題**：master handlerが検索した同じrecをdownload handlerが再検索する。存在確認・署名URL発行はasync route内で同期実行される。

**変更**：同ファイルに非routeの`async _build_media_download_metadata(recording_id, mf)`を作る。download_media_fileのmf取得後（format/content-type/path/backend/size・存在確認・presign・response組立）をこのhelperへ移す。get_recording_masterは所有者条件付きrec検索→master選択→helperを直接呼び、media_file_id/raw_url/duration_secondsを付与。download_media_fileは従来の所有者検索と_find_media_fileの後helperへ委譲する。route関数同士の呼出しをなくす。

```py
# before master
_, rec = await _find_meeting_data_recording(db, user.id, recording_id)
master_mf = ...
response = await download_media_file(recording_id, media_file_id, auth, db)  # 再検索
# after master
_, rec = await _find_meeting_data_recording(db, user.id, recording_id)
master_mf = ...  # 選択条件・先頭優先を維持
response = await _build_media_download_metadata(recording_id, master_mf)
```

helper内のstorage client取得・file_exists・get_presigned_urlの同期SDK区間を**一つの同期local関数**にまとめ、`await asyncio.to_thread(...)`で実行する。SQLAlchemy session/Meeting ORM objectをthreadへ渡さない。渡すのはmfの必要scalar値とrecording_idだけ。threadの中でDBにアクセスしない。存在確認例外→既存404、署名URLが空→raw fallback、local→raw、filename/content_type/file_size_bytes/expires_in=3600/download_url aliasを維持する。例外分類をこの項目で変えない。

**新設test**：

- `test_R04_master_queries_owned_recording_once`：owner5/master audio1件、DB execute回数=1、raw_url/media_file_id/durationと従来metadata一致。master無し404ではstorage0回。
- `test_R04_download_and_master_share_metadata_contract`：local/MinIO/GCS、format webm/wav/mp4、presign空、exists falseをparameterize。download200 bodyは基準golden同一。masterの追加3キーだけ許容。
- `test_R04_slow_storage_does_not_block_event_loop`：fake SDK関数がthreading.Eventを待つ。並列heartbeat coroutineが実行されてからtest側でEventを解除できること。SDK開始thread IDがevent loop threadと異なる。wall clockだけで判定しない。終了後threadを必ず回収。
- `test_R04_owner_rejection_precedes_storage`：他owner検索なし→404、SDK0回。SQLのuser_id bindを検査。

**完了**：`"$PLAN_PY" -m pytest services/meeting-api/tests/test_recording_metadata_resolution.py services/meeting-api/tests/test_recording_download_fallback.py services/meeting-api/tests/test_recordings.py services/meeting-api/tests/test_recording_gcs_metadata.py -v` →全pass。M/H。R03 gateway suiteも再実行（rawの仕様は不変）。

**リスク / 戻し方**：threadでORMを使う、存在確認順を変える危険。共通revertでhelperと2callerを同時に戻す。storage.py・finalizer・DB schemaには変更しない。**依存**：R03。

### R05 — 詳細画面の非同期応答を会議と世代で隔離する

**対象**：`services/dashboard/src/stores/meetings-store.ts:72–143,261–380,431–440,511–539`（fetchMeeting/refreshMeeting/fetchTranscripts/fetchChatMessages/setCurrentMeeting/clearCurrentMeeting）。新設`services/dashboard/tests/test_meeting_detail_request_scope.test.ts`。

**問題**：旧要求の失敗やtranscript/chatの応答が現在の会議へ反映される。clearしても進行中要求が戻って状態を復活させる。

**変更**：一覧のgenerationはそのまま。詳細用にmodule内の`detailEpoch`、detail/transcript/chat別のrequest generation、`activeDetailId`を持つ。外へexportせず、新module/別storeへの全面分割はしない。

- `fetchMeeting(id)`：active IDと異なる場合はepochを増やしactive IDを設定、旧currentMeeting/transcripts/recordings/chatとmanagerをclearしてからforeground loading開始。同じIDの再取得はepochを変えずdetail世代を進める。
- `clearCurrentMeeting()`：epochと全詳細request世代を増やしactive ID=null、詳細データと詳細loadingだけclear。一覧stateは触らない。`setCurrentMeeting`はID切替時に同様のinvalidateを行い、そのmeeting/recordingsを設定。
- `refreshMeeting(id)`：active IDに合わなければAPI呼出しなしでnull。active ID未設定かつcurrentMeeting.idがidと一致する場合はそのIDを採用する（既存再文字起こしtestの直接state seedに対応）。currentMeetingもactive IDもない状態で勝手に会議を復活させない。
- transcript/chatは呼出し開始時にcurrentMeetingのID/platform/native IDと引数が一致することを確認。明示meetingIdがある場合はそれも一致必須。合わない場合は何も変更せずreturn。開始時のepoch/ID/そのchannelの世代をcaptureする。
- 各awaitの**成功とcatchの両方**で、capture値が現在と一致する場合だけset/bootstrap/log/route unavailable更新を許す。古いfinallyで新要求のloadingをfalseにしない。stale応答は何もしない。refreshのstaleはnullを返す。
- transcript/chatへの最新要求だけでなく録音書込みも調整する。detail/refresh/transcriptが開始するたび、共有`recordingReadGeneration`を一つ進めてcapture。録音配列を書けるのは最新のrecordingReadGenerationだけ。古いdetailが他のmetadataを更新する場合、`data.recordings`だけは現在のstore録音配列を保つ。最新transcriptがrecordingsを設定するときはcurrentMeeting.data.recordingsも同じ配列へ揃える。空配列も正本として適用する。旧API応答で完成録音を巻き戻さない。
- REST bootstrapのmanager処理、WS統合・テキスト正規化・既存402分類、録音signature比較は維持。WSのevent schemaは変更しない。

```ts
const epoch = detailEpoch;
const requestId = ++transcriptRequestGeneration;
const ownerId = currentMeeting.id;
const stillCurrent = () => epoch === detailEpoch
  && requestId === transcriptRequestGeneration
  && get().currentMeeting?.id === ownerId;
try {
  const result = await vexaAPI.getMeetingWithTranscripts(...);
  if (!stillCurrent()) return;
  get().bootstrapTranscripts(result.segments);
  // 録音は別途recordingReadGenerationも一致したときだけ適用
} catch (error) {
  if (!stillCurrent()) return;
  // 既存のcurrent要求向けerror処理
}
```

**新設test**：

- `R05 ignores stale detail success and failure`：A detail保留→B detail成功→A成功/404/402/503をそれぞれ解決。current=B、Bのerror/loading/recordingsが変わらない。
- `R05 clearing invalidates every channel`：Aに対するdetail/transcript/chatを保留→clear→順不同で全resolve。全詳細配列空、current=null、loading=false。manager bootstrap呼出し0。
- `R05 late transcript and chat cannot enter another meeting`：A bootstrap保留→BへswitchしBを成功→A応答。Bのtext/recordings/chatだけが残る。same native ID・異なるnumeric IDのcaseも含める。
- `R05 latest response owns loading and recording metadata`：同じ会議で古いforeground detail保留→新foreground保留→古い失敗。新loading=trueを維持。さらに古いtranscriptのrecordings=[]と新detailのcompleted masterを逆順で返し、完成録音が消えない。
- `R05 current empty recordings remain authoritative`：最新transcriptがrecordings=[]を返す→storeとcurrentMeeting.dataの録音が空になる。
- `R05 refresh accepts existing current meeting without new scope`：既存testと同じ`setState({currentMeeting: A})`からrefresh(A)で更新可能。clear後のrefresh(A)はnull・API0回。

**完了**：`(cd services/dashboard && npm test -- tests/test_meeting_detail_request_scope.test.ts tests/test_single_flight_polling.test.ts tests/test_recording_refresh_signature.test.ts tests/test_meetings_store_refresh_race.test.ts tests/test_loading_characterization.test.tsx)` →全pass。D/H。

**リスク / 戻し方**：別channelの世代を混ぜると正常なbootstrapが捨てられる。testでchannelごとの正常更新と録音だけの横断順序を分ける。共通revert。**依存**：R04。

### R06 — 詳細のpolling所有者を一つにする

**対象**：`services/dashboard/src/hooks/use-meeting-polling.ts:1–88`、`services/dashboard/src/hooks/use-meeting-live-data.ts:20–26`、`services/dashboard/src/lib/single-flight-polling.ts:1–29`。新設`services/dashboard/tests/test_meeting_polling_ownership.test.tsx`、既存`tests/test_single_flight_polling.test.ts`に追加。

**問題**：setIntervalで未完了でも処理が重なり、stoppingで二つのpollerとbootstrapが同じ取得を実行する。新しい共通pollerにも未処理rejectがある。

**変更**：

1. `startImmediateIntervalPolling`は既存exportと実装をそのまま残す。`test_meeting_polling.test.ts`の「重なりを保つ」旧helper特性testも変更しない。**本番のuseMeetingPollingだけ**既存startSingleFlightPollingへ移行する。
2. startSingleFlightPollingの3引数とinterval/shouldContinueの意味を維持し、runのrejectをcatchして安全な固定メッセージを出す。timerからのunhandled rejectionをなくし、finallyでinFlight解除。stop後はshouldContinueを呼ばない。shouldContinue=falseならtimer終了。
3. useMeetingPollingは一つのeffectとmode選択にする。`artifacts`が有効ならそちらを優先、そうでなくstatusが有効なら`status`、両方falseなら停止。artifactsはmeetingId/platform/native ID必須。intervalはartifacts2500ms、status5000msを維持。初回は即時実行。
4. artifacts taskは`await Promise.allSettled([refreshMeeting(...),fetchTranscripts(...),fetchChatMessages(...)])`をreturnする。各taskを`Promise.resolve().then(() => fn())`で包み、同期throwでも全体の管理を失わない。status taskはrefreshMeetingのPromiseをreturnする。
5. hook内のrefで`{meetingId, token}`のflightを保持する。mode変更のcleanup後も同じmeetingIdの旧taskが未完了なら新taskを開始しない。旧taskのfinallyは自身のtokenが一致する場合だけrefをclear。別meetingIdへ移る場合は新IDのflightを作れるが旧応答はR05で捨てられる。同じ会議にstatus/artifactが並行しない。
6. useMeetingLiveDataのstatus依存bootstrapは`pollArtifacts`がfalseのときだけ動くようにする（trueのときは即時artifact pollが担当）。現行の末尾にあるWS用chatだけの追加effectは削除し、通常bootstrapのchat1回に統合する。activeのWS購読自体は残す。

```ts
// before: callbackがundefinedを返すので外側で待てない
() => { refreshMeeting(id); fetchTranscripts(...); fetchChatMessages(...); }
// after
async () => {
  await Promise.allSettled([
    Promise.resolve().then(() => refreshMeeting(id)),
    Promise.resolve().then(() => fetchTranscripts(...)),
    Promise.resolve().then(() => fetchChatMessages(...)),
  ]);
}
```

**新設/追加test**：

- `R06 artifact polling has one owner`：stopping、status/artifact両方true、3taskを保留、7.5秒進めても各1回。3task中2つだけ完了しても追加0。全部完了後の次tickで各1回追加。
- `R06 switching mode does not overlap same meeting`：statusのrefresh保留→artifactsへ切替。同ID refresh追加0。旧refresh解決後の次artifact tickで3taskが始まる。
- `R06 bootstrap is not duplicated by polling`：active→通常transcript/chat各1回。stopping→artifact経由各1回、独立bootstrap0。completedかつ録音あり→artifact停止、状態変化のbootstrapだけ1回。
- `R06 cleanup and rejected tasks leave no unhandled work`：task reject/同期throw、unmount、fake timeを進める。unhandledRejection=0、unmount後新request=0。旧会議の遅着が新会議を変えない（実store併用）。
- `R06 single flight continues after rejection and stops on false`：初回reject後、次tickは実行される。次成功でshouldContinue=false→後のtickは0。既存再文字起こしqueued→running→succeeded停止testもpass。

**完了**：`(cd services/dashboard && npm test -- tests/test_meeting_polling_ownership.test.tsx tests/test_meeting_polling.test.ts tests/test_single_flight_polling.test.ts tests/test_transcript_reprocess_ui.test.ts)` →全pass。D/H。

**リスク / 戻し方**：Promiseをreturnし忘れると単なるhelper置換では改善しない。legacy testを通しつつ本番hookの最大同時実行1を別testで証明。共通revert。**依存**：R05。

### R07 — 再生URL解決の入力とerror状態を安定させる

**対象**：`services/dashboard/src/hooks/use-meeting-playback.ts:9–82,168`、`services/dashboard/src/lib/api.ts:565–594`、`services/dashboard/src/components/meetings/meeting-detail-page.tsx:111–116,153–159,268–304`、`services/dashboard/src/hooks/use-meeting-live-data.ts:8,23–24`。新設`services/dashboard/tests/test_playback_resolution.test.tsx`、`tests/test_recording_master_api.test.ts`の追加case。

**問題**：同じ録音内容を新配列で渡すたびにmaster APIを呼ぶ。音声と映像が同じerrorを消し合い、通信失敗後の回復が不明瞭。

**変更**：

- `useMeetingPlayback(meetingId, recordings, transcripts)`にする。唯一の本番caller MeetingDetailPageを同commitで更新。R00のhook testは呼出し引数だけ適応し、seekの期待値を維持する。
- audio/videoごとに**JSON.stringifyしたdescriptorキー**を作る。キーはmeetingIdと、選択対象recの`id/status/session_uid/created_at/playback_url[type]`、同typeのmaster media（typeが一致しfinalized_byがrecording_finalizer.masterの先頭要素）の`id/storage_path/file_size_bytes/duration_seconds/finalized_by/is_final`を含む。undefinedはnullに正規化。audioは既存created_at昇順（同値は元順）、videoは既存配列順を維持する。全文transcriptや配列object identityは含めない。
- effectは`[audioKey, retryGeneration]` / `[videoKey, retryGeneration]`を依存にする。JSON.parseしたdescriptorから処理するため、effect内で外側recordingsを参照せずexhaustive-depsを迂回しない。キーが同じ新配列は再fetch0回。masterの実体/長さが変われば再解決する。
- URL解決の状態はaudio/video別にする。`audioResolutionError`と`videoResolutionError`を保持し、片方成功で他方をclearしない。公開`playbackConnectionError`は互換用に`audioResolutionError ?? videoResolutionError`として残すが、画面表示とartifact poll停止には**audioResolutionErrorだけ**を使う。
- audio成功時は音声Playerを表示し、video失敗は別行の非fatal errorとして表示する。audio失敗時は従来の録音接続エラー枠を使う。各error枠に「再試行」ボタンを置き、hookの`retryPlayback()`が両channelの解決世代を増やす。新機能ではなく既存録音の接続失敗からの復旧操作としてこの範囲に限定する。
- `getRecordingMasterStreamUrl`にoptional第三引数`signal?: AbortSignal`を追加。指定されたときだけfetch optionsにsignalを渡す。404→null、同一origin master proxy URLの返却は維持。非2xxの失敗は`VexaAPIError`（statusを持つ）にする。正常時body型/URLキー/durationは変更しない。
- 一回のaudio batch/video探索ごとに10秒のAbortController deadline（fetchとJSON bodyを含む）を置く。audioは既存Promise.all、videoは従来順の探索。batch内の全fetchに同じsignalを渡す。cleanupでtimerをclearしてabortし、epoch/owner照合後のみsetする。
- 502/503/504・ネットワーク失敗・deadlineの場合だけ、自動再解決を1500/3000/6000msの最大3回（初回を含め最大4attempt）実行。自動retryで同じchannelのbatchを重ねない。401/403/その他4xx・不正200は自動retryなしでerror。手動retry・キー変更で回数をreset。自動retryはchannel内部のattempt counter/timerで進め、共用retryGenerationを増やさない（他channelのbudget resetと再取得を防ぐ）。共用retryGenerationは手動操作専用。自動retry中は終端audioResolutionErrorをまだ設定せず、budget終了時/非retryable失敗時だけ公開する。
- **404の既存null除外と並び順を維持**。audio対象batchがすべてnullなら同じ最大3回だけ再確認し、その後は「録音の準備を確認できませんでした。再試行してください」というaudio errorで自動pollを止める。キー変化または手動retryで再開できる。最初からplayback_urlが一つもない場合はhookで404探索を始めず、従来のfinalizing/artifact pollに任せる。video候補がすべて404なら有限retry後もvideoSrc=null・video側の準備確認errorだけとし、audio errorへ変換しない。video候補自体がない会議はvideoSrc=null・video error=null。
- 一件の非404失敗をallSettledで黙って除いて、残りを時間軸の詰まったplaylistとして表示しない。全体の非404失敗はbatch errorとする。未完成404の除外は基準実装の契約を維持するが、推測でduration/gapを補わない。
- meetingIdが変わったら旧playlist・download target・video/errorとpendingSeekTime/playbackTime/isPlaybackActiveをresetし、旧会議で予約したseekを新会議へ持ち越さない。descriptor変更時はそのchannelの解決結果だけclear。transcriptのvirtual time/seek計算式は変更しない。

```ts
// before
useEffect(resolveAudio, [audioMediaSignature, recordings]);
// after: 入力値をkeyで固定し、型付きのdescriptorへ戻す
useEffect(() => {
  const descriptors = JSON.parse(audioKey); // 上記固定fieldだけ
  const controller = new AbortController();
  // descriptorsで解決 → epoch確認 → audio状態のみ更新
  return () => { controller.abort(); /* deadline/retry timerをclear */ };
}, [audioKey, retryGeneration]);
```

**新設test**：

- `R07 equal descriptors do not refetch or reload`：録音1件をdeep-cloneして10回rerender、master APIはaudio1回/video対象分1回、AudioPlayer.src不変。transcript追加でも再解決なし。
- `R07 changed master and meeting invalidate old resolution`：同録音IDでmaster file_size/duration変更→再解決1回。A保留→B→A遅着ではBのsrc/errorを上書きしない。
- `R07 audio and video errors are independent`：audio503/video200とaudio200/video503の両順序。前者はaudio error維持、後者は音声play/seekが利用可能でvideo errorだけ表示。
- `R07 retries are finite and manually recoverable`：audio503を継続。各attemptは10秒以内に完了/timeout、1500/3000/6000msの間隔、4attemptで停止。さらに1分進めても追加0。手動retryで初回attemptが1回増え、その200でerrorが消える。
- `R07 no retry for authorization or invalid success`：401/403/422・URLのない200は1回でerror。404全件は有限retry後に準備確認error。playback_urlなしはAPI0回。
- `R07 preserves playlist order and seek contract`：audio recのcreated_at逆順入力を昇順にし、duration12/20、session-bの3秒→fragment1,3秒・virtual15秒。中間404の除外は基準挙動どおり。非404の1件失敗は他fragmentだけのplaylistを公開しない。
- `R07 aborts headers body and retries on cleanup`：fetch待ち・JSON body待ち・retry timer待ちの3caseでunmount。signal.aborted=true、残存timer0、state更新0。

**完了**：`(cd services/dashboard && npm test -- tests/test_playback_resolution.test.tsx tests/test_recording_master_api.test.ts tests/test_recording_master_proxy_route.test.ts tests/test_loading_characterization.test.tsx tests/test_meeting_polling_ownership.test.tsx)` →全pass。D/H。

**リスク / 戻し方**：不十分なkeyでmaster更新を見逃す、音声と映像のerrorを分離せずUIを非表示にする危険。上記field・表示条件を全て含める。共通revertでhookとcaller・signal対応をまとめて戻す。**依存**：R06。

### R08 — audio要素の自動再読込を有限にする

**対象**：`services/dashboard/src/components/recording/audio-player.tsx:55–68,166–179,220–247,272–288,309–322,445–470`（retry lifecycle）。新設`services/dashboard/tests/test_audio_retry_lifecycle.test.tsx`。

**問題**：errorのたび同じsrcを1500ms後にloadし、永続障害でも止まらない。旧srcのtimerが新srcに作用する余地がある。

**変更**：同ファイルに`AUDIO_RETRY_DELAY_MS=1500`と`AUDIO_MAX_AUTOMATIC_RETRIES=3`を定義。retry回数はrefで同期管理し、timer pending中の追加errorで回数を消費/重複予約しない。errorCountはUI表示用として維持。

error時、未予約かつ自動retry数<3なら1回予約。callback内でcountを増やしてaudio.load()。3回実行後の次errorはisLoading=falseの終端error状態にし、既存の手動「再試行」を表示する。読み込み状態を無期限spinnerにしない。HTMLMediaElementのerrorだけでHTTP401/404等を推定して認証stateを変更しない。

currentSrc変更時・canplay成功時・手動retry時にtimerをclear、回数0・errorCount0へ。timer callbackは予約時srcと現在srcが一致するときだけloadする。unmountでtimerをclear。手動retryは既存同様即load1回、その後に最大3回の自動retryを許す。src切替・元のfragment seek/wasPlayingRefの処理は維持する。R07のURL解決retryとHTML media retryは別状態機械で、片方をもう片方から自動的にresetしない。

**新設test**：

- `R08 retries three times then reaches terminal error`：error→1500ms→loadを3回繰返し、4回目errorから60秒進める。自動load合計3回、spinner終了、手動retry可。
- `R08 repeated error events reserve one timer`：1500ms以内にerror10回→timer1、load1。
- `R08 source change and unmount cancel old retry`：Aのerror予約→B srcへ変更、A timer時刻で追加loadなし（通常src切替のloadは別に数える）。unmount後追加0。
- `R08 manual retry and canplay reset budget`：終端error→手動retryで即load1、canplayでerror解消。次errorから再び最大3回。
- `R08 metadata and fragment seeking remain intact`：effect登録前readyState=HAVE_METADATAの再同期、同fragment seek、次fragmentへauto advance、duration更新をR00期待値で確認。

**完了**：`(cd services/dashboard && npm test -- tests/test_audio_retry_lifecycle.test.tsx tests/test_playback_resolution.test.tsx tests/test_loading_characterization.test.tsx)` →全pass。D/H。

**リスク / 戻し方**：retryを打ち切った後に手動復旧できなくなること。既存retryボタンを削除しない。共通revert。**依存**：R07。

### R09 — /auth/meで認証基盤の障害を401に変換しない

**対象**：`services/api-gateway/main.py:427–466,1848–1865`（_resolve_token/auth_me）。新設`services/api-gateway/tests/test_auth_availability.py`。

**問題**：Admin APIへの接続失敗・5xx・内部secret不一致もNoneになり、/auth/meが無効tokenとして401を返す。BFFだけ直しても区別できない。

**変更**：`_resolve_token(client, api_key, *, report_unavailable=False)`と内部例外`TokenValidationUnavailable`を追加。既存callerはdefault=Falseで従来挙動を維持し、auth_meだけTrueを指定する。

|strict（True）の結果|扱い|
|---|---|
|正常cache / Admin200の正常identity|従来のdict。user_idがありscopesが配列であることを確認。|
|cache不正JSON/不正形状|cacheを信用せずAdminへvalidate。削除必須ではない。|
|Admin401|無効/失効token→None→auth_me401。|
|Admin403|内部secret認証の不整合なのでTokenValidationUnavailable→503。ユーザーのCookie失効とは扱わない。|
|Admin429/5xx/不正200/その他予期しないstatus・httpx例外|TokenValidationUnavailable→503。|
|Redis障害|cacheを利用せずAdminへ確認。cache書込み失敗でも有効identityは返す。|

Admin `/internal/validate`の403は内部shared secret不一致、無効tokenは401であることを`services/admin-api/app/main.py:826–862`で確認済み。任意の403を「ユーザーが禁止された」と推測しない。

strictの場合だけRedis get/set各awaitを`asyncio.wait_for(...,1.0)`で囲み、timeout時はcache miss/write失敗扱い。auth_meではstrict resolver全体を`asyncio.wait_for(...,8.0)`で囲み、timeout/TokenValidationUnavailableを503 `{detail:'Authentication service unavailable'}`へ変換。APIキーなし401、正常200のresponse keysは維持する。鍵/secretをexception messageへ出さない。HTTPXの5秒validate timeoutとcache TTL60は維持。

```py
# default callerは無変更
async def _resolve_token(client, api_key, *, report_unavailable=False): ...
# auth_meだけ
try:
    user_data = await asyncio.wait_for(
        _resolve_token(app.state.http_client, api_key, report_unavailable=True), 8.0
    )
except (TokenValidationUnavailable, asyncio.TimeoutError):
    raise HTTPException(503, 'Authentication service unavailable')
if not user_data:
    raise HTTPException(401, 'Invalid API key')
```

strictの例外を関数内の既存broad exceptが再びNoneに変換しないよう、分岐はcatch後に明示する。default=Falseの他6直接caller、WebSocket/browser/forward_requestの認証意味を一緒に変更しない。

**新設test**：

- `test_R09_auth_me_distinguishes_invalid_and_unavailable`：Admin200/401/403/429/503/不正JSON/ConnectErrorをparameterize。200/401/503の対応表どおり。必ず実resolverを通し、resolver自体はmockしない。
- `test_R09_slow_cache_falls_back_within_budget`：Redis get保留→1秒でAdminへ進む。Admin成功後set保留→1秒で成功を返す。失敗中もcacheから適当なidentityを返さない。
- `test_R09_auth_me_has_eight_second_ceiling`：resolver本体を無期限待ちにしたcaseだけwait_forを制御し、8秒deadlineで503。これは境界test、前testで実resolverの分類を保証。
- `test_R09_default_resolver_and_scope_checks_are_unchanged`：report_unavailable未指定でAdmin障害→None。既存header injection、scope拒否、cache TTL60が維持される。tokenなし→Adminへのrequest0回。

**完了**：`"$PLAN_PY" -m pytest services/api-gateway/tests/test_auth_availability.py services/api-gateway/tests/test_header_injection.py services/api-gateway/tests/test_gate_g5_websocket.py services/api-gateway/tests/test_media_streaming.py -v` →全pass。G/H。

**リスク / 戻し方**：resolverは波及が広い。auth_meだけopt-inとしdefault互換をtest。共通revert。R10開始前なのでBFF側との不整合は生じない。**依存**：R08。

### R10 — 初期認証確認にdeadlineと正しい終端状態を置く

**対象**：`services/dashboard/src/app/api/auth/me/route.ts:10–56`、`services/dashboard/src/stores/auth-store.ts:6–13,153–220,225–305`、`services/dashboard/src/components/auth/auth-provider.tsx:55–100`。新設`services/dashboard/tests/test_auth_me_availability.test.ts`、`tests/test_auth_initialization.test.tsx`。

**問題**：upstream非2xxをすべて401へ変換してCookieを削除し、storeも5xxをunauthorized扱いにする。fetch/bodyのdeadlineがなく、shared-loginの障害からredirectが続く可能性がある。

**変更（サーバー側）**：/api/auth/meのGateway GETを10秒deadlineでfetch/JSON body読了まで管理しfinallyでtimer解除。401だけCookieを削除して401を返す。403・429・5xx・不正JSON・不正identity・ネットワーク失敗は503、deadlineは504として返し、Cookieは削除しない（Gatewayのstrict auth_meに通常403はない）。Cache-Control=no-store。正常時のuser/token/authenticated shapeは維持し、tokenをlogしない。

**変更（ブラウザ側）**：

1. checkAuthのGET `/api/auth/me`と必要なGET `/api/auth/oauth-callback`を一つの12秒deadlineで囲む。各fetchは同じAbortSignal。body解析までdeadline有効。401だけuser/tokenをclearしてunauthorized。5xx/429/JSONとして不正な200/ネットワーク/deadlineは`isAuthenticated=false,isLoading=false,authError='network',token=null`。userは表示用hintとして残してよいが認証済みと扱わない。
2. 現行の成功契約（200 user+token、OAuth callback成功、既にserver確認済みのin-memory identityを用いる既存分岐）を維持。JSONとして正常な200だがuser/token不足の場合は既存OAuth確認へ進み、その応答401はunauthorized、5xx/不正JSON/deadlineはnetwork。200でも必要なidentityが最後まで揃わずin-memory identityもない場合はnetworkとする。すべての枝を同じepochとdeadlineで囲む。永続化のtoken/isAuthenticated禁止を維持し、expiry不明の認証cache/TTL短絡を導入しない。
3. 同じcheckAuthが同時に呼ばれた場合はmodule内の同じin-flight Promiseを返す。完了後は解除し、次の明示checkは新しく検証する。`authEpoch`を持ち、logout/setAuth/shared-login開始で増加。旧check結果のsuccess/errorが新epochを上書きしない。logout後の旧200で再ログインさせない。stale finallyで新in-flightを解除しない。
4. 自動共有ログインは非冪等なPOSTなので自動retryを追加しない。signInSharedDashboardのブラウザPOSTは60秒deadline（既存serverの15秒admin操作が最大3段あるため）でbody読了まで管理。同時要求は1つのPromiseにまとめる。失敗5xx/429/ネットワーク/deadlineは`authError='network'`、LoginResultにoptional `reason:'network'`を返す。shared auth無効404/登録拒否403は従来のshared_login_failed分岐を維持。
5. AuthProviderの`await signInSharedDashboard()`直後で`sharedResult.reason==='network'`ならreturnし、その回のredirectを禁止する。React effect cleanupのタイミング任せにしない。既存network error枠と手動「再試行」を使う。ログイン成功・public route・明示logoutの既存経路は維持。
6. client abortはPOSTのserver側作用をrollbackしないことに注意。自動再送しない。manual retryは既存ユーザー操作として扱い、アカウント/キーの新しいcleanup処理は作らない。

```ts
// before: 非2xxすべてunauthorized
if (!response.ok) { clearCredentialsAndSetUnauthorized(); }
// after
if (response.status === 401) { clearCredentialsAndSetUnauthorized(); }
else if (!response.ok) { setNetworkTerminalState(); }
// shared-loginのawait直後
if (cancelled || sharedResult.success || sharedResult.reason === 'network') return;
```

**新設test**：

- `R10 auth route preserves cookie on outage`：Gateway401→Cookie delete、Gateway503/429/不正200→503+delete0、timeout→504+delete0、正常identity→旧shape200。fetch回数1。
- `R10 auth check reaches network terminal state`：503/504/ネットワーク/12秒timeout→authError network、loadingfalse、isAuthenticatedfalse、tokennull。AuthProviderにnetwork枠が表示され、router.push/外部redirect/shared POST追加0。
- `R10 shared outage does not redirect or retry`：GET401後にshared POST503/timeout。reason networkを受けてredirect0、POST1。さらにfake timerを120秒進めてもPOST追加0。shared disabled404は既存login遷移1回。
- `R10 auth checks share one request and ignore stale login`：checkAuthを同時2回→GET1。logout→旧200解決でisAuthenticatedfalse維持。setAuth(B)後の旧A失敗でBを消さない。
- `R10 oauth and healthy sessions retain behavior`：正常GET user+token、OAuth経由の正常結果、既存protected/public route、shared成功を確認。認証deadlineのtimerは成功/失敗/unmount後に残らない（storeの独立要求はunmountだけで取消さず完了/timeoutで終了、UIへの旧effect更新はしない）。

**完了**：`(cd services/dashboard && npm test -- tests/test_auth_me_availability.test.ts tests/test_auth_initialization.test.tsx tests/test_auth_redirect_loop.test.ts tests/test_login_shared_auth.test.tsx tests/test_loading_characterization.test.tsx)` →全pass。D/H。GのR09 suiteも再実行。

**リスク / 戻し方**：認証を速く見せるために検証を飛ばす変更は禁止。shared POSTへの自動retry禁止。同commitでBFF/store/providerを更新する。共通revertでR10だけ戻すとGatewayのR09は残るが、既存BFFが503を401に変換する旧挙動へ戻るだけで、正常契約は維持される。**依存**：R09。

### R11 — 最終レビュー証拠を一つのcommitで確定する

**対象**：`.hw/plans/app-loading-recording-refactor-20260905/review-verdict.json`（review toolが生成）。アプリsourceの変更なし。

**問題**：review後に実装commitをamendすると、verdictのreviewed_commitが祖先ではなくなる。review前に証拠を捏造したり、未commit verdictでclean gateを通したことにできない。

**手順**：

1. R10のclean commitでD/G/M/H全体とR00–R10の追加testを再実行。全pass、skip/xfail/zero testsが増えていないことを確認。`node .gitnexus/run.cjs detect_changes --scope compare --base_ref <R00のSHA> --repo <実行worktreeの索引名>`で影響を確認（表記は実CLI helpに合わせる）。partial/truncatedは再解析し完全な証拠を得るまで止める。
2. 本文末の実行順トレースと照合し、旧helperの本番参照、非opt-in route、audio/video error、認証deadlineが記載どおりであることをtest結果と差分で確認する。
3. R10のclean commitに対して`python3 .hw/fable_review.py app-loading-recording-refactor-20260905`。対象base-commitはR00、検証契約は同ディレクトリのverification-contract.md。plan/adoption/契約はR01以降に確定済みで、review直前に書き換えない。
4. promptの総chunk数が既定上限を超えた場合だけ、既存設定`HW_FABLE_MAX_CHUNKS=12`で同じ差分を再レビューする。単一ファイルがbudget超過した場合は見落とし扱いで中断。diffをtruncateしたり契約を削って通さない。利用枠・認証が不足した場合は実装検証済みとレビュー待ちを分けて報告し止める。
5. violationsがあれば次へ進まず、対応R項目IDと違反を報告する。R11でコードをこっそり修正しない。新たな修復項目が必要ならその契約を作ってから実行し、READYを自己宣言しない。advisoryはこの計画の自動修正範囲ではない。
6. READYならgenerated verdictだけをaddして`chore: 読み込み改修のレビュー証拠を確定`というR11 commitを作る。**R10をamendしない**。verdictのみはtarget_materialの除外対象なので、レビュー対象hashを変えずにreviewed_commit=R10が祖先として残る。
7. `python3 .hw/check_review_verdict.py app-loading-recording-refactor-20260905`と`bash .hw/hooks/pr-ready-gate.sh app-loading-recording-refactor-20260905`を実行。両exit0・clean treeで完了。gateが生成するstate/gatesは既存の扱いに従い、sourceを変えない。PR作成・merge・push・deployはこの計画には含まない。

**完了**：上記check/gate exit0、review-verdictのviolations空・対象hash一致、D/G/M/H最終記録あり。これが最後のコミットであり、R00–R11の**12項目＝12個の採用commit**となる（失敗修復時の未公開候補amendは採用数に数えない）。

**リスク / 戻し方**：verdict commit以後にsource/契約を変えるとREADY失効。R11だけの共通revertは実装を変えず、レビュー未確定状態へ戻る。失敗なら先の項目へ進まず報告する。**依存**：R00–R10すべて。

## 5. 作業順を頭からトレースした結果

実装した結果を確認したものではなく、**計画内の前提・依存・戻し方の静的レビュー**である。実行時は各testを省略できない。

|段階|前項の変更後に残る前提|次項との整合・検証|
|---|---|---|
|R00→R01|元sourceと同じHEAD、正常page/seek/auth/Rangeのtestあり。作業元のdirtyは隔離。|R01はその正常契約を変えずeffect一つだけ削除。Fable再採用時にbaseをR00へ移すので巨大索引がコードレビュー差分に入らない。|
|R01→R02|fetchMeetings/API形状/フィルターはそのまま。|R02はエラー時だけ非2xxを可視化。storeはVexaAPIErrorを扱う既存経路を利用し、後の項目のerror判定の前提が整う。|
|R02→R03|BFFの録音branchは無変更。|Gatewayのbinary3routeだけstreamingになり、BFFが既に渡すRangeとresponse.bodyを利用できる。直接raw/MP3のJSON416だけBFFでもheaderを保持する。metadata/listのclient.requestは維持。|
|R03→R04|binary streamはmetadata JSONと別route。|R04はmaster/downloadのJSON生成だけを共通化。raw/MP3のストレーム寿命やRangeは触らない。SDKをthreadへ移す際、DB/ORMは渡さない。|
|R04→R05|master JSON field・404・same-origin URLは維持。|R05はUIへの反映順序だけを制限。backend contractを変更しないので古い応答排除を独立にtest可能。|
|R05→R06|clear/切替後の応答は全channelで捨てられる。|poller cleanupで開始済みHTTPが物理的に止まらなくても汚染しない。mode切替中の同一会議は共通flight refで重複を止める。旧helper/testを削らない。|
|R06→R07|artifactの取得ownerは一つ。|R07のaudio errorでpoll停止、手動retryでerrorを解除すると同じownerが再開。video errorは停止条件から除外し、音声の正常表示も妨げない。keyだけに依存すると404/一時障害が再試行されなくなる穴は有限retryと明示retryで閉じる。|
|R07→R08|URL解決とHTML media読込は別状態。|R08はURL解決を呼ばずaudio.loadだけ有限にする。二つのretryが相互resetするloopを作らない。src変更/unmountで旧timer終了。|
|R08→R09|メディアroute3つ以外のGatewayは既存動作。|R09はauth_meだけstrict resolver。R03のforward_requestはdefault互換なので認証意味を一律変更しない。|
|R09→R10|無効token401と認証基盤障害503が区別される。|R10のBFF/storeが503をnetworkにする前提が成立。timeoutはGateway8s→BFF10s→GET client12sの順。shared POSTは60sで別管理し自動retryしない。|
|R10→R11|全項目のsource/契約がcommit済み、base=R00。|R10で全test/Fable review。R11はverdict除外ファイルだけなのでtarget hashとreviewed_commitの祖先条件を保持。|

**トレースで解消した衝突**：

- 旧pollerの「重なりを保つ」testを変更して通す案を捨て、callerを新pollerへ移す方式に固定した。
- Audio hookのkey安定化だけでは一時失敗の再試行が消えるため、有限retryと手動回復を同じR07に含めた。
- storeのchannel別世代だけではdetailとtranscriptが互いの録音を巻き戻せるため、録音書込みだけ横断世代を追加した。
- 音声/映像errorをhookだけ分けても画面の共通error分岐で音声Playerが消えるため、callerのrenderとpoll条件もR07で同時更新する。
- BFFだけの認証修正ではGatewayが503を401へ落とすため、R09→R10の順序にした。
- R11のverdictをR10へamendする案を禁止。12番目の独立commitとして祖先関係を保持する。

## 6. やらないこと

1. 全体アーキテクチャの置換、storeの全面分割、API client全面再生成、UI全面改装、SSR/auth cache導入、ライブラリ更新。
2. 新しいpagination/cursor、page size変更、検索/並び順変更、一覧response fieldの勝手な追加削除。
3. 未計測のDB index追加・JSONB→別table移行・一覧SQL projection書換え。F11はR00診断をもとに別計画とする。`select(Meeting)`の残存を今回の未完了扱いにしない。
4. 録音保持期限・削除・redaction・声紋同意・暗号・storage配置・CORS・IAM・署名TTL変更、全録音再生成、既存録音の書換え。
5. masterを介さない任意chunk再生、推測による断片間gap補完、コーデック自動変換の追加、非404失敗した断片を黙って除外する部分playlist化。
6. huge functionという理由だけでrequest_bot、Gemini結合、final_transcription、Bot収録・終了callbackを分割すること。今回の再生pathと生成pathを混ぜない。
7. 旧URL helper、wire/UIの命名差を未使用/不統一と決めつけて削除・一括renameすること。必要になればGitNexusで別途解析する。
8. エラーの握りつぶし、401/402を200空配列に変換、未検証userを認証済みにすること、成功のためtimeoutを無制限に延長すること。
9. 元workspaceのAGENTS/CLAUDEのdirtyを巻き込む、`.env`や本番配置資材を書き換える、本番データをfixtureにする、root make build/publish/deployを実行すること。
10. test削除/skip/xfail/期待値緩和・baseline追加・lint抑制追加によってpassさせること。mock配線やhook signatureの適応は必要範囲に限り、assertionの意味を維持する。
11. `generated_by: fable`、READY、benchmark値、test結果を実行せずに記入すること。ゲートの適用範囲/権威を変更すること。
12. R02の正しい失敗応答、R07の録音接続再試行、R08の有限retry、R09–R10の認証障害分類以外の新機能・仕様変更。

## 7. 実行者へのコピペ指示

```text
この計画書とリポジトリを唯一の実行仕様として、Vexaの読み込み・終了済み会議一覧・録音再生の改善を実施してください。

調査基準HEADは67ea03210c2de4c8723780402d302948b138d939です。変更箇所の行番号はこのHEAD基準なので、前項による移動はシンボルと本文で追跡してください。元workspaceのAGENTS.md/CLAUDE.mdにある未commit変更は触らず、R00で独立worktreeを作ってください。

今回の計画作成者Codexはユーザーが明示承認済みです。Fableと偽記しないでください。機械ゲートはその承認で変更されていません。R00/R01に書かれた実際のFable再採用と、R11のFableレビューを実施し、利用不可なら実装開始前または該当ゲートで停止して理由を報告してください。ゲートや署名を偽装しないでください。

R00の準備と基準commit後は、計画記載のprime起動を使ってR01から継続してください。Fable採用・レビューは親担当が実施し、実行役が自己署名してはいけません。

R00→R11を必ず一項目ずつ実施し、一項目ごとに一つの採用commitを作ってください。候補commit後のclean treeで、その項目の指定test・D/G/Mの適用束・Hを実行してください。完了条件を満たせなければ中断して報告してください。報告には項目ID、失敗test名/command、実際の結果、期待結果、対象commit、戻せるcommitを含めてください。次項への前倒し着手・並列実装は禁止です。

修復は同項目内の未公開候補commitをamendし、clean treeで再検証してください。成功済みの古い項目だけを途中でrevertしないでください。R11はreview-verdictだけを独立commitし、レビュー済みR10をamendしないでください。

編集前にGitNexus upstream impact、commit前に完全なdetect_changesを実行してください。HIGH/CRITICALは報告し、UNKNOWNはHTTP/動的参照をコードで照合してください。partial/truncatedをpass扱いしないでください。

Howと完了条件を合否基準とし、背景の説明を根拠に自己合格させないでください。テスト・assertion・baselineを弱めて通すこと、未使用推測による削除、DB移行、依存更新、本番操作、PR作成/push/merge/deployは範囲外です。

最後に全testとR11のhashが束縛されたreview/gateを通し、項目ごとのcommit・検証証拠・残る実測上の不明点を日本語で報告してください。本番の高速化率や障害完全解消は未計測なら主張しないでください。
```

## 8. 完成判定の境界

この計画の実装完了はR00–R11の契約を満たした状態。本番への適用・計測は別作業である。適用後も遅さが残る場合は、R00に定義した一覧DBの診断と実配備のHTTP時系列を照合し、F11やストレージ応答・CPU/メモリの追加計画に進む。未計測の原因まで今回の実行者に推測実装させない。


## 9. plan.mdだけで引き継ぐ場合の復元用付録

会話履歴や本ディレクトリの他ファイルを受け取れなくても実行仕様は欠けない。R00を始める前に、このplan.mdをリポジトリの`.hw/plans/app-loading-recording-refactor-20260905/plan.md`へ保存する。同じディレクトリに、以下3ブロックをそれぞれ記載ファイル名でそのまま保存する。`base-commit`には調査SHA `67ea03210c2de4c8723780402d302948b138d939`と末尾改行を入れる。これは文書の復元であり、Fable生成済みやレビュー済みの記録ではない。R00の実際の再採用手順は別途必須。HTML/索引/調査ノートは補助資料で、無い場合に実行を止める理由にはしない。

### sml-decision.json

```json
{
  "size": "L",
  "generated_by": "codex",
  "status": "planning_only",
  "axes": {
    "requirement_ambiguity": {
      "level": "M",
      "reason": "症状は明確だが本番計測未取得。改善対象を決定的な契約へ限定。"
    },
    "technical_unknown": {
      "level": "L",
      "reason": "ASGI切断、stream寿命、複数非同期channelと認証状態の相互作用。"
    },
    "blast_radius": {
      "level": "L",
      "reason": "Dashboard、Gateway、Meeting API。forward_requestの直接caller61、認証resolverへの波及。"
    },
    "verifiability": {
      "level": "M",
      "reason": "決定的テストで構造契約を検証。本番高速化率と障害完全解消は合否対象外。"
    }
  },
  "verdict_reason": "複数サービスと認証/streamingを横断するためL。実装前のFable再採用と実装後のFableレビューを省略しない。"
}
```

### runtime-decision.json

```json
{
  "runtime": "prime",
  "generated_by": "codex",
  "status": "planned_not_started",
  "axes": {
    "duration": true,
    "state_volume": true,
    "delegation": false,
    "recurrence": false
  },
  "reason": "12項目とサービス別全suiteの反復は数時間を超える見込み。全差分と非同期状態を一度に保持せず一項目ずつ検証・commitする。並列実装は不要。R00の独立worktreeで基準commit後、HW_PRIME_WORKTREE=0で既存prime_run.pyを起動し二重worktreeを避ける。停止条件は既存pr-ready-gateのまま。Fable再採用/レビューを実行役の自己署名で代替しない。"
}
```

### verification-contract.md

以下のmdブロック全体を保存する。後から自己判断で短縮・条件緩和しない。

```md
# 検証契約 — app-loading-recording-refactor-20260905

作成: Codex（ユーザー明示承認）。状態: 計画のみ。基準コード: `67ea03210c2de4c8723780402d302948b138d939`。
本書はplan.mdのHow/完了条件を短く固定したもの。入力fixture・test名・commandの完全仕様はplan.mdのR00–R11を適用する。本書だけを理由にtestを省略しない。実装前のFable再採用と実装後のFableレビューはplan.mdの手順どおり。

## C00 — 安全網と変更境界

- R00で独立worktreeを作成。元workspaceのAGENTS.md/CLAUDE.mdの未commit変更は触らない。
- R00で正常契約の特性test 8ケース以上を追加し、sourceを変えず基準commitを作る。5 Dashboard / 1 Gateway / 2 Meeting API。plan.mdに記載した全test名が収集されること。
- D（Dashboard test/typecheck/build/lint ratchet）・G（Gateway全suite）・M（Meeting API非live suite）・H（.hw/verify.sh）をclean commitで通す。環境不足と既存失敗は中断して報告。
- R01開始時のFable再確認でbase-commitをR00へ移す。大きいHTML/索引/原稿はR00に含め、実装レビューの差分へ混ぜない。
- R00–R11は直列・一項目一つの採用commit。テスト削除、skip追加、assertion緩和、lint/verify baseline追加、依存更新、本番操作は禁止。

## C01 — 一覧初回要求

- R01: productionの通常mountでforeground一覧要求1回。status/platform変更と300ms検索debounceの引数は従来どおり。unmount後の予約検索は0回。
- 50件単位offset、redacted表示除外、ID重複除去、並び順、has_moreを維持。
- 対象: test_meetings_initial_load.test.tsx、R00特性、既存store race。D/H。

## C02 — 履歴障害の可視化

- R02: /meetings→/botsを1回だけ要求し、/bots/status fallbackは0回。completedを含む成功bodyは維持。
- upstream401/402/403/429/5xxを200へ変換しない。malformed200は502。headers/body両方の待ちを5000ms以内で504に終端。接続例外502。
- Cache-Control=no-store、429 Retry-After保持。Cookieを削除しない。storeは旧一覧を残してerror/402を既存経路で表示する。
- 対象: test_meetings_proxy_contract.test.ts、既存sensitive proxy/master proxy。D/H。

## C03 — 音声byte転送

- R03: Gatewayのraw / media-mp3 / master-mp3のGET3routeだけstream_response=True。その他61callerを一律変更しない。
- tail未解放でもfirst chunkをASGI sendで観測。downstream停止中にtailをprefetchしない。.content/aread/全chunk蓄積なし。
- 正常・read例外・最初のchunk前後の切断・send失敗・cancelでupstream資源を回収。共有clientを閉じない。
- status200/206/404/416、Range関連header、Content-Type/Disposition/Encodingとraw bytesを保持。hop-by-hopは除去。raw30秒/MP3 180秒の既存socket timeoutを維持。
- missing token401・scope不足403・spoof header除去・owner404を維持。拒否時のupstream stream要求0回。
- BFF直接3routeのJSON416もContent-Rangeを保持。master?proxy=1の既存416と認証エラー分類を維持。
- 対象: test_media_streaming.py、test_recording_routes.py、test_header_injection.py、R00 gateway特性、test_recording_master_proxy_route.test.ts。D/G/H。

## C04 — masterメタ情報

- R04: masterの所有者付きDB検索1回。downloadも従来の所有者条件を維持。route同士の直接呼出しをhelperへ置換。
- 同期storage client取得/exists/presignはto_thread内、DB/ORMはthreadへ渡さない。SDKを止めてもevent loop heartbeatが進む。
- local/MinIO/GCS、presign空のraw fallback、metadata shape、master選択、404、TTL3600を維持。
- 対象: test_recording_metadata_resolution.py、既存recordings/download fallback/GCS。M/HとR03 Gateway回帰。

## C05 — 詳細の所有者と応答世代

- R05: detail/transcript/chatの成功・失敗・finallyをowner+epoch+channel世代で制御。clear後や他会議へ切替後の応答はstate/managerを変えない。
- detail/refresh/transcriptが共用する録音世代により古い録音を戻さない。最新の空配列は適用する。
- 既存currentMeetingだけをseedしたrefreshは成功し、clear後は復活しない。一覧generation/offset、WS、文字起こしの正規化を維持。
- 対象: test_meeting_detail_request_scope.test.ts、既存refresh signature/store race/single flight、R00特性。D/H。

## C06 — pollingの所有者

- R06: 同会議の最大同時poll batch=1。status/artifact併存時はartifact優先。初回即時・2500/5000msを維持。
- batchは3Promiseすべてのsettlementを待つ。同IDのmode切替でも旧taskが終わるまで追加0。
- stoppingのbootstrap/chat重複0。通常active bootstrapは各1回、WS購読維持。
- 旧startImmediateIntervalPollingと既存の重複特性testを変更しない。本番callerのみ移行。共通pollerのreject/同期throwはunhandledにならず次tickへ進める。
- 対象: test_meeting_polling_ownership.test.tsx、既存meeting polling/single flight/transcript reprocess。D/H。

## C07 — URL解決と音声/映像

- R07: 同値descriptorの10回rerenderで再取得0。masterの実体/長さ変更は再取得。meeting切替後に旧結果・旧seekを反映しない。
- audio/video errorは独立。video失敗でaudio Playerを隠さず、artifact停止判定もaudio側だけ。
- batch deadline10秒。通信/502/503/504/timeoutおよび全件404確認の再試行は1500/3000/6000ms、初回込み4attemptまで。401/403/その他4xx/不正200は自動再送しない。
- playback_url無しならmaster探索0。video無しならvideo error無し。404でない一件の失敗を黙って捨てて部分playlistを公開しない。
- 音声created_at順、映像既存順、404除外、同一origin src、duration/fragment/virtual seekを維持。手動再試行で回復可能。cleanupでabort/timer回収。
- 対象: test_playback_resolution.test.tsx、既存master API/proxy、R00特性、R06 hook。D/H。

## C08 — media要素の有限retry

- R08: HTML audio errorの自動loadは3回×1500msまで。連続error10回でも予約timer1つ。枠を使い切ったらspinner終了・既存手動retryを表示。
- src変更/canplay/手動retryでbudget reset、unmountでtimer0。旧srcのtimerは新srcをloadしない。
- R07のURL解決retryと相互resetしない。metadata再同期・fragment auto advance/seekを維持。
- 対象: test_audio_retry_lifecycle.test.tsx、R07、R00特性。D/H。

## C09 — 認証基盤の可用性

- R09: /auth/meだけstrict resolverを使用。Admin401のみ無効token、内部secret403/429/5xx/不正200/接続例外は503。
- Redis不良はcache miss、正常Admin結果をcache write失敗で失わせない。strict get/set各1秒、全体8秒。既存validate5秒/cache TTL60を維持。
- default=Falseの他callerとscope/identity注入は従来互換。欠けた/不正なidentityで許可しない。
- 対象: test_auth_availability.py、既存header injection/WS、R03 streaming。G/H。

## C10 — 認証初期化の終端

- R10: BFF GET10秒、browser GET群12秒、shared POST60秒でheaders/body両方を終端。401だけCookie削除/unauthorized。
- outage/不正応答はnetworkの非認証状態へ。AuthProviderはsharedResult.reason=networkならそのawait直後にreturn、redirect/POST自動再試行0。
- 並行checkAuthは1要求。logout/setAuth/shared開始後に旧checkがstateを戻さない。shared POSTも同時実行をまとめ、自動retryなし。
- 正常SSO/OAuth/public/protected/明示logoutを維持。persisted token/isAuthenticatedを信用・保存しない。
- 対象: test_auth_me_availability.test.ts、test_auth_initialization.test.tsx、既存auth redirect/login、R00特性。D/H、R09 Gateway。

## C11 — 完成証拠

- R10のclean commitで最終D/G/M/Hと全追加test、base=R00の完全なGitNexus compareを実施。partial/truncated/zero testsは不合格。
- Fable契約レビューのviolationsゼロ・READY。利用不可/契約違反なら停止し報告。実行役の自己合格は禁止。
- R11はreview-verdict.jsonだけをcommit。reviewed_commit=R10を祖先として残す。R10をamendしない。
- check_review_verdict.pyとpr-ready-gate.shがexit0、clean。12項目12採用commitと外部証拠を報告。PR/push/merge/deployは含めない。

## 報告項目

各項目ID・commit SHA・対象test名と収集数・実行command/exit code・外部証拠pathを記録。失敗時は期待/実際・再現入力・直前の戻せるcommitを報告し、次項を始めない。本番高速化率は未計測なら記載しない。
```
