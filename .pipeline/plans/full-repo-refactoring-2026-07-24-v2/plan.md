# generic_tldv 全体リファクタリング実行計画 v2（縮約版）

- Task ID: `full-repo-refactoring-2026-07-24-v2`
- 基準ブランチ: `main`
- 調査時HEAD: `b2bcae8e88f0e73fe95343ee3a694a3afc4e1028`
- 目的: この計画とリポジトリだけを受け取った実行者が、追加の設計判断をせず、129項目を1項目1コミットで安全に完遂する
- 本計画作成時の制約: 製品コード、テスト、設定は変更しない。元のv1計画4ファイルも変更しない

## 0. 結論と実行方針

巨大ファイルの分割を先に行わない。認可、秘密値、破壊操作、非同期競合、検査の偽陽性を先に修正し、既存挙動をcharacterization testで固定してからmove-only抽出へ進む。

管理上は3フェーズ、4マイルストーンとする。

| フェーズ | 範囲 | 目的 | 管理マイルストーン |
|---|---|---|---|
| Phase 1 | RF-00A〜RF-30 | 安全網、認可・秘密値、破壊操作、正しさ、非同期競合 | M0=RF-00D安全網、M1=RF-30挙動修正完了 |
| Phase 2 | RF-31〜RF-51 | tests3、Deploy、Harnessを実行事実と一致させる | M2=RF-51検証基盤完了 |
| Phase 3 | RF-52〜RF-75F | Backend、Frontend、Bot Coreの契約維持型抽出とデッド資産整理 | M3=RF-75F最終完了 |

管理マイルストーンは承認儀式ではない。各地点で対象test、required suite、CI、tracked GitNexus wrapperの`detect-changes`、PR reviewを確認する通常の工程境界である。

本v2では新しい技術選定を行わない。v1の`research-brief.md`と`option-matrix.md`で確定済みの工学判断を保持し、運用protocolだけを縮約するため、両artifactを再生成・複製しない。実装に必要な決定前提は本計画へ転記済みであり、実行者へ外部文脈を要求しない。

### 0.1 実配備の7境界と6回の軽量drain

credential互換導入とlegacy削除の間には実トラフィック観測が必要なため、配備境界だけは安全上7波を維持する。各波は通常のPR、CI、人間mergeで進め、追加の承認レイヤーや専用の障害耐性protocolは設けない。

| Wave | 項目範囲 | merge後の停止条件 |
|---|---|---|
| D1 | RF-00A, RF-00N, RF-00E, RF-00B, RF-00C, RF-00D, RF-01〜RF-05B | OP-05A: 新Gateway identity経路が成功しlegacy direct利用が0 |
| D2 | RF-05C, RF-05D1, RF-05D1B | OP-05D: service principal成功が1件以上、shared credential利用が0 |
| D3 | RF-05C2, RF-05D2〜RF-06C1 | OP-06C: authenticated Redis経路成功、legacy Redis利用が0 |
| D4 | RF-05F2, RF-05G2, RF-06C2, RF-06D1 | OP-06D: capability経路成功、legacy workload token利用が0 |
| D5 | RF-06D2〜RF-06F | OP-06F: pre-signed Zoom SDK JWT成功、client-secret経路利用が0 |
| D6 | RF-06G〜RF-09A | OP-09: correlation channel成功、legacy Browser transport利用が0 |
| D7 | RF-09B〜RF-75F | 新たなdrainなし。M1〜M3の通常検証を完了 |

各OP gateに必要なのは、対象環境、観測開始・終了時刻、新経路の成功件数1以上、legacy経路0件、観測queryまたはdashboard URL、確認者だけである。未達なら次のlegacy削除項目へ進まず`blocked: drain pending`として止める。実行AIはproduction配備や数値の捏造をしない。

D1〜D6は互換cutoverの技術配備境界である。D7は単一PRではなくdrain不要の通常delivery laneで、M1/M2/M3または項目単位のPRへ分割してよい。ただしRF見出し順と1項目1commitは維持する。

## 1. 現状理解

`generic_tldv`はGoogle Meet、Microsoft Teams、Zoom等へBotまたはBrowser Sessionを参加させ、音声・映像を取得し、リアルタイムまたは会議後に文字起こしする。Dashboardは閲覧、検索、話者編集、再文字起こし、再生、エクスポートを提供し、同じrepoにCalendar、Telegram、MCP、Agent、Wake STT/TTS/Voiceprint、Deploy、Managed Harnessが含まれる。

主要経路は次のとおり。

```text
利用者 -> Dashboard / Telegram / MCP / API -> API Gateway
API Gateway -> Admin / Meeting / Calendar / Agent / Runtime
Bot / Browser -> Redis -> Meeting collector -> PostgreSQL / Object Storage
Meeting -> Transcription -> transcript確定 -> Dashboard / export / voiceprint
Wake STT -> Wake Orchestrator -> LLM -> TTS
```

### 1.1 構造マップ

| 領域 | 主要ファイル | 責務と依存 |
|---|---|---|
| Gateway | `services/api-gateway/main.py` | 認証、scope、HTTP/WS proxy、Agent SSE。全service境界 |
| Meeting | `meeting_api/meetings.py` | Bot要求、URL、runtime spec、Browser保存。Runtime/DB/Redisへ依存 |
| Lifecycle | `callbacks.py`, `sweeps.py`, `post_meeting.py` | 終端判定、復旧、副作用。関数内importを含む循環 |
| Deferred transcription | `final_transcription.py` | lease、provider、DB、cache、publish、export、voiceprint |
| Transcription | `services/transcription-service/main.py`, `gemini_adapter.py` | HTTP境界、音声前処理、chunk境界、speaker alignment |
| Runtime | `runtime_api/api.py`, `scheduler.py` | Docker/K8s/Process backend、予約job |
| Dashboard | `app/meetings/[id]/page.tsx`, `transcript-viewer.tsx` | polling、再生、Browser、検索、話者編集、responsive UI |
| Transcript state | `meetings-store.ts`, `packages/transcript-rendering/src/*` | confirmed/pending、dedup、timeline、WebSocket |
| Bot Core | `services/vexa-bot/core/src/index.ts` | platform起動、音声、Browser、command、shutdown |
| Agent/Calendar | `services/agent-api`, `services/calendar-service` | workspace/container、OAuth、予定同期 |
| Tests/Deploy/Harness | `tests3`, `deploy`, `scripts/harness` | registry、readiness、CI、evidence、worktree |

### 1.2 優先問題

- P0: query/bodyの`user_id`、未署名cookie/email、欠落secret fallbackにより認証subjectを取り違える。
- P0: browser、Bot、Agent containerへservice-wide credentialを渡し、別sessionへreplayできる。
- P0: Runtime create、workspace Git、webhook/scheduler、task IDにcommand injection、SSRF、path traversal、control-plane侵害の余地がある。
- P0: Browser保存のpublish/subscribe順序、相関ID、timeoutが競合する。
- P0: tests3、Compose、Lite、Helm、Harnessが0件、skip、不存在script、timeoutを成功扱いできる。
- P1: callback終端意味、transcript dedup/timeline、meeting切替、polling、WebSocket、schedulerに状態競合がある。
- P1: lifecycle、Bot要求、deferred transcription、Gemini、Gateway、Dashboard、Bot Coreが巨大かつ責務混在。
- P2: DTO/表示、UI state/API/JSX、ORM/database、registry、shell helper、docs資産が重複または不整合。

### 1.3 基準baseline

- Dashboard unit: 28 files / 199 tests成功。lintは61 errors / 87 warningsの既知失敗。
- `packages/transcript-rendering`: 83成功 / 5 skip。typecheck成功。
- `services/vexa-bot/core`: `tsc --noEmit --incremental false`成功。
- tests3 registry: 91件中45 script不存在。無言で削除せずRF-31〜37で状態を正規化する。
- `features/`は意図的にrepo外へ移動済み。削除済みsidecarを復元しない。
- 調査時GitNexus indexは古い。実装開始時に再解析し、最新sourceと直接照合する。
- 調査時に見えたDashboardは現HEADより古いため合否根拠にしない。RF-00Cで現HEADのfixture baselineを取得する。

## 2. 共通実行規約

### 2.1 対象、行番号、停止条件

- 対象行は調査時HEADの位置。先行変更でずれたら記載symbol、endpoint、test名で再同定する。
- `新規 path`はexactな作成先。rename/moveはsourceとdestinationを対象欄のとおり使う。
- 各itemのwrite unionは(a)対象欄のwrite path、(b)自ID matrixの`state` 1値、(c)完了条件にliteral `path::nodeid`で列挙したtest file、または既存test basenameがrepo内で一意に解決するexact test file、(d)2.3表のresolver itemだけownerのexact test file/parameter marker、の和集合とする。(c)はtestの変更/新規作成だけを許し、2.3の圧縮記法をfull pathへ展開後の`::`前を使う。曖昧basename、複数候補、production/configへのfallback、別test/別parameter、他ID matrix変更は停止。required suite全体とread-only inventoryはwrite targetへ昇格しない。union外pathが必要なら編集せず計画修正を求める。
- read-only inventoryは影響確認だけに使い、検索結果を自動で編集対象へ追加しない。
- 公開API、status code、JSON shape、Redis key、DB metadata、transaction境界、provider model/prompt/retry値は、項目が明示したもの以外変更しない。
- test不存在、0件収集、skip、xfail、必要infra不足をpassへ変換しない。
- dependency更新、lockfile再生成、format-only横断変更は項目が明示しない限り禁止。
- RF-00B以降は唯一の暗黙共通targetとして`scripts/test/refactor-item-matrix.json`の自ID `state`だけを`planned`から`active`へ変更し、同じitem commitへ含める。commands/nodeids/suites/他IDの変更は対象外変更として停止する。

### 2.2 1項目の標準手順

1. `git status --short`を確認し、ユーザー所有の既存差分を変更・stash・削除・stageしない。
2. `ITEM_BASE_SHA="$(git rev-parse HEAD)"`を記録する。
3. 既存production file/symbolごとに`bash scripts/test/run-gitnexus-refactor.sh impact --target "<symbol-or-file>" --direction upstream`を実行する。indexが古ければ`bash scripts/test/run-gitnexus-refactor.sh analyze`後に再実行する。
4. HIGH/CRITICALまたは計画外processが出たら編集せず、blast radiusを報告して承認または再計画を待つ。
5. バグ修正は再現testを先に追加して修正前failureを確認する。move-only項目は既存characterizationを先にpassさせる。
6. 2.1のwrite unionだけを変更し、項目固有commandとrequired suiteを実行する。
7. `git diff --check`後、write unionのexact pathだけをstageする。`git diff --cached --check`とcached path一覧を確認し、ユーザー所有path、union外path、欠落した新規fileが1件でもあればunstage/deleteせず停止する。
8. `bash scripts/test/run-gitnexus-refactor.sh analyze --force`後、`bash scripts/test/run-gitnexus-refactor.sh detect-changes --base-ref "$ITEM_BASE_SHA"`がexit 0・非空・partial/errorなしで、reported risk/processが計画内、symbolへmapされるcached codeは期待symbol/processを含むことを確認する。JSON/PNG/lockfile等symbolなしpathの表示は要求せずstep 7のcached write-union照合を正本とする。解析後にindex/worktreeを変えず指定subjectで1コミットする。
9. 完了条件未達、または解析後に差分変更が必要ならcommit/後続項目へ進まず、step 6から検証し直す。

RF-00Aではtracked GitNexus wrapperが未作成なのでGitNexus commandを要求せず、Gitのread-only確認とdirect assertionだけを行う。RF-00Nではwrapperのdirect testとdependency install後にwrapper自身の`analyze`と`detect-changes`を実行する。RF-00E作業中にmatrixをAだけactiveとしてRF-00A、次にN、次にEをactive化して各標準2 commandを順に実行し、A/N/E activeの同一RF-00E commitへ含める。

### 2.3 標準commandの意味

全項目の完了条件にある2 commandはliteralで実行する。

```bash
bash scripts/test/run-refactor-item.sh RF-XX
bash scripts/test/run-required-suites.sh RF-XX
```

合格はexit 0、test 1件以上収集、failed 0、下表の明示strict xfail以外のunexpected skip/xfail/xpass 0、本文に列挙したexact test名すべてnormal passである。RF-00B/Dのstrict xfailはresolver項目までexpectedとし、最終xfail 0にする。

matrixは`{item_id,state,commands,required_suites}`を129見出し順に持ち、stateは`planned|active`だけとする。plannedは未着手でargv schema/path containmentだけを検査し、test fileの存在・収集・実行を要求せずplaceholderも作らない。activeはliteral testの存在、1件以上収集、実行を必須にする。item runnerは自ID active、全先行ID active、全後続ID plannedを要求し、active集合が先頭からの連続prefixでなければexit 2。RF-00EではA→N→Eの順にactive化し、以後各item working diffが自IDのstate 1値だけをactive化してtests/targetと同じ1 commitへ含め、最終planned 0とする。phase/wave gateは過去itemを再実行せず、保存済みitem reportを検証してstable-unique full suiteだけを実行する。未知/重複ID、空command、active missing test、2.1のproduction fallback/別test/別parameter/他ID state変更はexit 2。

本文の`path::{test_a,test_b}`は`path::test_a`,`path::test_b`へ展開する文書圧縮記法である。完全な`path::test_a`の直後から連続する`::test_b`も、次のfull path/command/suite区切りまで直前pathを継承して`path::test_b`へ展開する。matrix argvは全てbraceなし/full pathのliteral要素とし、先行full pathなし、区切り越え、曖昧継承、`{`/`}`を含むargv、`::`始まりargvは実行前にexit 2とする。

| owner | exact parameter nodeid | resolver |
|---|---|---|
| RF-00B | `services/meeting-api/tests/test_lifecycle_characterization.py::test_terminal_matrix_snapshot[zero-exit-explicit-failure]` | RF-11 |
| RF-00B | `services/api-gateway/tests/test_route_inventory_characterization.py::test_every_current_route_is_observed[unclassified-policy]` | RF-05A |
| RF-00B | `services/runtime-api/tests/test_scheduler_characterization.py::test_job_and_terminal_history_schema_snapshot[non-atomic-retry]` | RF-24 |
| RF-00D | `tests3/unit/refactor/test_rf_00d.py::test_fail_open_baseline[missing-active-script]` | RF-32 |
| RF-00D | `tests3/unit/refactor/test_rf_00d.py::test_fail_open_baseline[empty-report]` | RF-31 |
| RF-00D | `tests3/unit/refactor/test_rf_00d.py::test_fail_open_baseline[all-skip]` | RF-31 |
| RF-00D | `tests3/unit/refactor/test_rf_00d.py::test_fail_open_baseline[zero-step]` | RF-33 |
| RF-00D | `tests3/unit/refactor/test_rf_00d.py::test_fail_open_baseline[compose-health-timeout]` | RF-38 |
| RF-00D | `tests3/unit/refactor/test_rf_00d.py::test_fail_open_baseline[lite-schema-init]` | RF-39 |
| RF-00D | `tests3/unit/refactor/test_rf_00d.py::test_fail_open_baseline[helm-missing]` | RF-42 |
| RF-00D | `tests3/unit/refactor/test_rf_00d.py::test_fail_open_baseline[task-traversal]` | RF-01 |

各resolver itemには表のowner test exact parameter行を暗黙の共通targetとして合成し、その行のxfail marker除去と期待値更新だけを許す。owner matrix entryと同fileの他parameterはbyte不変にする。

required suiteの実体は次に固定する。`RF_ENV_ROOT`はRF-00Eがrepo内task一時領域へ作る。

| 略号 | command |
|---|---|
| V-MEETING | `(cd services/meeting-api && "$RF_ENV_ROOT/backend/bin/python" -m pytest tests --ignore=tests/test_integration_live.py --ignore=tests/test_gate_g3.py --ignore=tests/integration -q)` |
| V-BACKEND | `(cd services/api-gateway && "$RF_ENV_ROOT/backend/bin/python" -m pytest tests --ignore=tests/test_gate_g5_websocket.py -q) && (cd services/admin-api && "$RF_ENV_ROOT/backend/bin/python" -m pytest tests --ignore=tests/test_g5_gate.py -q) && (cd services/agent-api && "$RF_ENV_ROOT/backend/bin/python" -m pytest tests --ignore=tests/test_g5_gate.py -q) && (cd services/calendar-service && "$RF_ENV_ROOT/backend/bin/python" -m pytest tests -q) && (cd services/runtime-api && "$RF_ENV_ROOT/backend/bin/python" -m pytest tests --ignore=tests/test_gate_g5.py --ignore=tests/test_integration.py --ignore=tests/test_integration_process.py --deselect=tests/test_backends.py::test_process_backend_stop_terminates -q)` |
| V-TRANSCRIPTION | `(cd services/transcription-service && "$RF_ENV_ROOT/transcription/bin/python" -m pytest tests --ignore=tests/test_quality_gate.py -q)` |
| V-DASH | `cd services/dashboard && npm test && ./node_modules/.bin/tsc --noEmit && node scripts/check-lint-baseline.mjs tests/fixtures/lint-baseline.json && VEXA_API_URL=http://localhost:8056 npm run build` |
| V-DASH-FINAL | `cd services/dashboard && npm test && ./node_modules/.bin/tsc --noEmit && npm run lint && VEXA_API_URL=http://localhost:8056 npm run build` |
| V-TRANSCRIPT | `cd packages/transcript-rendering && npm test && ./node_modules/.bin/tsc --noEmit` |
| V-CORE | `cd services/vexa-bot/core && ./node_modules/.bin/tsc --noEmit --incremental false && npm test` |
| V-INTEGRATIONS | `(cd services/mcp && "$RF_ENV_ROOT/integrations/bin/python" -m pytest tests -q) && (cd services/telegram-bot && "$RF_ENV_ROOT/integrations/bin/python" -m pytest tests -q)` |
| V-CLIENTS | `$RF_ENV_ROOT/integrations/bin/python -m pip check && $RF_ENV_ROOT/integrations/bin/vexa --help` |
| V-AUX | `(cd services/wake-stt && "$RF_ENV_ROOT/aux/bin/python" -m pytest tests -q) && (cd services/wake-orchestrator && "$RF_ENV_ROOT/aux/bin/python" -m pytest tests -q) && (cd services/tts-service && "$RF_ENV_ROOT/aux/bin/python" -m pytest tests -q) && (cd services/voiceprint-service && "$RF_ENV_ROOT/aux/bin/python" -m pytest tests -q)` |
| V-OPS | `$RF_ENV_ROOT/backend/bin/python -m pytest tests3/unit -q && find . -name "*.sh" -not -path "*/node_modules/*" -exec bash -n {} + && $RF_ENV_ROOT/backend/bin/python tests3/docs/check.py` |
| V-HARNESS-CONTRACT | `$RF_ENV_ROOT/backend/bin/python -m pytest tests3/unit -q -k "harness or adapter or worktree or consultation or workflow"` |

required suiteは実行前に次のintentional exclusionが全て実在し、追加・欠落0であることを検査する: Meeting=`test_integration_live.py`,`test_gate_g3.py`,`tests/integration/`; Backend=`admin-api/test_g5_gate.py`,`api-gateway/test_gate_g5_websocket.py`,`agent-api/test_g5_gate.py`,`runtime-api/{test_gate_g5.py,test_integration.py,test_integration_process.py}`; Transcription=`test_quality_gate.py`; Runtime deselect=`test_backends.py::test_process_backend_stop_terminates`。前3群はtests3 live/gate/quality lane所有、Runtime 1件はplatform timing由来の恒久baselineで本計画のresolverなし。収集対象のskipは0とし、Transcriptの既存5 skipはRF-00Cでnormal passへ変える。

### 2.4 戻し方

- R0（通常）: failure diffとログを保存して停止。完了済みcommitを変更せず、直前合格SHAから新規worktreeを作り同じ項目を再実行する。
- R1（互換導入）: 旧経路を残して停止し、新経路をdefaultへ昇格しない。
- R2（legacy削除）: drain未達なら削除commitを作らない。配備後異常なら直前の互換配備artifactへrollbackし、credentialを手作業で復活させない。
- R3（DB/schema）: destructive migrationを同じ項目へ含めない。失敗時はapplicationを直前versionへ戻しdataを変更しない。
- R4（契約維持型refactor / UI・visual）: 各項目で固定したDTO、route、byte、golden、DOM、画像契約のいずれかに意図外差分があれば不合格とし、前の合格SHAからR0の手順で同じ項目を再実行する。

### 2.5 Visual baseline

RF-00Cでは具象JSON fixtureをrepoへ追加し、9 scenarioをparameterizeしたPlaywright spec 1本でdesktop 1440x900とmobile 390x844の18枚を取得する。fixtureは具象ファイルだけにし、生成用の独自言語は作らない。baselineとfinalは同じfixture、route、viewport、locale、timezone、clock、browser versionを使う。各scenarioでDOM role/text、console error 0、page error 0、unexpected 4xx/5xx 0を検査し、UI変更対象だけselector単位の差分を許可する。実ユーザーcookie、token、live dataは使わない。

### 2.6 フェーズ・wave完了

- 各項目は1コミット。複数IDをまとめない。
- 各wave末尾で対象item command、stable-unique required suites、CI、`git diff --check`、tracked wrapperの`detect-changes`、PR reviewを通し、人間がmergeする。
- M0〜M3では同じ検証をphase全体へ実行する。実行済みtest reportの再承認や計画全文の反復reviewは不要。
- D1〜D6の次境界開始には対応drain記録が必要。D7と管理マイルストーンは通常のCI/PR工程でよい。

## 3. Phase 1: 安全網・境界・正しさ（RF-00A〜RF-30）

### M0へ向けた安全網

### RF-00A 作業前checkpointと隔離worktree
- 対象: read-only: Git repository全体、`scripts/harness/{backcast-checkpoint,worktree,build}.sh:1-末尾`; 新規
  `.pipeline/plans/full-repo-refactoring-2026-07-24-v2/{plan.md,verification-contract.md,planned-visual-changes.json,release-boundaries.json}:1-末尾`; 新規
  `.pipeline/evidence/full-repo-refactoring-2026-07-24-v2/baseline.json:1-末尾`
- 問題: 調査workspaceにはユーザー所有の未追跡成果物がある。これをbaseline commitへ混ぜる、stashする、削除する行為は不可。
- 変更: 現行`worktree.sh create`は元workspaceのbrokenなGit管理外symlinkを複製して失敗するため、RF-00A bootstrapだけはraw Gitで基準SHAのclean detached controlを作る。controlでは実装・commitせず、4 artifactと下記exact
  keyのbaselineだけを個別copy/生成してから、control上の既存helperで正式worktree/metadataを作る。以後controller commandはcontrol、実装とcommitは正式worktreeだけで行う。次をrepo rootからliteral実行し、path/branch衝突、source symlink、SHA差で停止する。

```bash
set -euo pipefail
RF_TASK=full-repo-refactoring-2026-07-24-v2; RF_BASE=b2bcae8e88f0e73fe95343ee3a694a3afc4e1028
RF_BOOTSTRAP_ATTEMPT="${RF_BOOTSTRAP_ATTEMPT:-initial}"; case "$RF_BOOTSTRAP_ATTEMPT" in initial) ;; retry-[1-9]*) RF_N="${RF_BOOTSTRAP_ATTEMPT#retry-}"; case "$RF_N" in *[!0-9]*) echo "blocked: retry-N requires a positive integer" >&2; exit 2;; esac;; *) echo "blocked: attempt must be initial or retry-N" >&2; exit 2;; esac
RF_SUFFIX=""; test "$RF_BOOTSTRAP_ATTEMPT" = initial || RF_SUFFIX="-$RF_BOOTSTRAP_ATTEMPT"; RF_BRANCH="codex/full-repo-refactoring-2026-07-24-v2-d1$RF_SUFFIX"
RF_ORIGINAL_ROOT="$(git rev-parse --show-toplevel)"; RF_CONTROL="$RF_ORIGINAL_ROOT/.pipeline/worktrees/full-repo-refactoring-2026-07-24-v2-bootstrap-control$RF_SUFFIX/checkout"
RF_EXPECTED_OFFICIAL="$RF_ORIGINAL_ROOT/.pipeline/worktrees/full-repo-refactoring-2026-07-24-v2$RF_SUFFIX/checkout"
RF_STATE="$RF_ORIGINAL_ROOT/.pipeline/worktrees/full-repo-refactoring-2026-07-24-v2-bootstrap-state$RF_SUFFIX"
test "$(git rev-parse HEAD)" = "$RF_BASE"; test -n "$(git branch --show-current)"; test ! -e "$RF_CONTROL"; test ! -L "$RF_CONTROL"; test ! -e "$RF_EXPECTED_OFFICIAL"; test ! -L "$RF_EXPECTED_OFFICIAL"; test ! -e "$RF_STATE"; test ! -L "$RF_STATE"
if git show-ref --verify --quiet "refs/heads/$RF_BRANCH"; then echo "blocked: branch exists" >&2; exit 1; fi
mkdir -p "$RF_STATE"; git status --porcelain=v1 -z >"$RF_STATE/original-status.before"
git worktree add --detach "$RF_CONTROL" "$RF_BASE"
for n in agents hooks rules skills; do test ! -e "$RF_CONTROL/.claude/$n"; test ! -L "$RF_CONTROL/.claude/$n"; done
install -d "$RF_CONTROL/.pipeline/plans/$RF_TASK" "$RF_CONTROL/.pipeline/evidence/$RF_TASK"
copy_exact(){ test -f "$1"; test ! -L "$1"; test ! -e "$2"; cp -p -- "$1" "$2"; cmp -- "$1" "$2"; }
for n in plan.md verification-contract.md planned-visual-changes.json release-boundaries.json; do copy_exact "$RF_ORIGINAL_ROOT/.pipeline/plans/$RF_TASK/$n" "$RF_CONTROL/.pipeline/plans/$RF_TASK/$n"; done
python3 - "$RF_ORIGINAL_ROOT" "$RF_CONTROL/.pipeline/evidence/$RF_TASK/baseline.json" "$RF_TASK" "$RF_BASE" "$RF_BOOTSTRAP_ATTEMPT" <<'PY'
import json, pathlib, subprocess, sys
from datetime import datetime, timezone
root,out,task,base,attempt=sys.argv[1:]
git=lambda *a: subprocess.check_output(["git","-C",root,*a],text=True).splitlines()
payload={"task_id":task,"attempt_id":attempt,"base_sha":base,"original_branch":git("branch","--show-current")[0],"original_status_lines":git("status","--short"),"original_untracked_paths":git("ls-files","--others","--exclude-standard"),"created_at":datetime.now(timezone.utc).isoformat().replace("+00:00","Z")}
pathlib.Path(out).write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
PY
(cd "$RF_CONTROL" && bash scripts/harness/worktree.sh create "$RF_TASK" --base "$RF_BASE" --path "$RF_EXPECTED_OFFICIAL" --branch "$RF_BRANCH")
RF_OFFICIAL="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["path"])' "$RF_CONTROL/.pipeline/worktrees/$RF_TASK/worktree.json")"; test "$RF_OFFICIAL" = "$RF_EXPECTED_OFFICIAL"; test "$(git -C "$RF_OFFICIAL" rev-parse HEAD)" = "$RF_BASE"; test "$(git -C "$RF_OFFICIAL" branch --show-current)" = "$RF_BRANCH"
cd "$RF_OFFICIAL"; git add -- ".pipeline/evidence/$RF_TASK/baseline.json" ".pipeline/plans/$RF_TASK/plan.md" ".pipeline/plans/$RF_TASK/planned-visual-changes.json" ".pipeline/plans/$RF_TASK/release-boundaries.json" ".pipeline/plans/$RF_TASK/verification-contract.md"
python3 -c 'import subprocess,sys; e=sys.argv[1:]; a=subprocess.check_output(["git","diff","--cached","--name-only"],text=True).splitlines(); assert a==sorted(e),(a,e)' ".pipeline/evidence/$RF_TASK/baseline.json" ".pipeline/plans/$RF_TASK/plan.md" ".pipeline/plans/$RF_TASK/planned-visual-changes.json" ".pipeline/plans/$RF_TASK/release-boundaries.json" ".pipeline/plans/$RF_TASK/verification-contract.md"
git diff --cached --check; git commit -m "RF-00A record approved plan and baseline"; test -z "$(git status --short)"
git -C "$RF_ORIGINAL_ROOT" status --porcelain=v1 -z >"$RF_STATE/original-status.after"; cmp -- "$RF_STATE/original-status.before" "$RF_STATE/original-status.after"
for n in plan.md verification-contract.md planned-visual-changes.json release-boundaries.json; do cmp -- "$RF_ORIGINAL_ROOT/.pipeline/plans/$RF_TASK/$n" "$RF_OFFICIAL/.pipeline/plans/$RF_TASK/$n"; done
```

  `baseline.json`は上記7 keyだけを持ち、`attempt_id`は`initial`または正整数Nの`retry-N`（`retry-1`を含み、`retry-0`/非数字を拒否）、`created_at`はUTC RFC 3339、status/untrackedは取得順の文字列配列とする。control/状態fileはtask-owned pathで最終完了まで保持し、symlink target作成、fallback、`worktree.sh`修正、元workspaceのstage/stash/deleteは禁止。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-00A`と`bash scripts/test/run-required-suites.sh RF-00A`がRF-00E作業中のA-only active段階でexit 0。bootstrap時はHEAD/branch/base、worktree分離、元workspaceの未追跡path不変をdirect
  assertionで確認。tests=`tests3/unit/test_refactor_item_runner.py::{test_rf_00a_checkpoint_schema_and_exact_staging,test_rf_00a_control_and_official_worktrees_bind_base_branch_and_metadata,test_rf_00a_original_workspace_status_and_artifact_bytes_are_unchanged,test_rf_00a_retry_1_suffix_is_unique_and_zero_or_nondigits_are_rejected}`; suite=なし。
- リスク/戻し方: worktree作成時のpath衝突、task artifact以外の混入、checkpoint引数のshell quoting。失敗時は元workspace status byte一致を確認し、このattemptが作ったexact control/official/state pathとbranchを列挙して保持・停止し、自動cleanup/force削除しない。原因修正後、人間が明示した`RF_BOOTSTRAP_ATTEMPT=retry-N`で同base/同4 artifact bytesから再実行する。旧attemptはread-only、path/branch再利用禁止。全review、新master task、archive/hash chainは要求しない。
- 依存: なし
- コミット: `RF-00A record approved plan and baseline`

### RF-00N Tracked GitNexus runtimeを先行固定
- 対象: 新規 `scripts/test/run-gitnexus-refactor.sh:1-末尾` 新規 `scripts/test/gitnexus-runtime/package.json:1-末尾` 新規 `scripts/test/gitnexus-runtime/package-lock.json:1-末尾` 新規 `scripts/test/gitnexus-runtime/run.mjs:1-末尾` 新規
  `scripts/test/test_gitnexus_refactor_bootstrap.py:1-末尾`
- 問題: 全項目で編集前impactを必須にする一方、従来runnerはgitignoredでclean cloneへ含まれず、versionも固定されない。RF-00Eで同時作成すると、RF-00E自身の既存symbol編集より前にwrapperが存在しない。
- 変更: `package.json`を`private=true`、Node major 22、npm major 10、dependency exact `gitnexus=1.6.9`にし、lockfileのtarball integrityを
  `sha512-Rq5LXFygx7jjMp/YFsIAcnnzuKvvCsb4rxHFILnu05ZOqk7xNXTUSMRa968EOCbxcKFxnhKYaGXoabOUeGZX6A==`へ固定する。shell評価を使わないtracked wrapperの公開CLIは`analyze [--force]`、`impact --target <symbol-or-file>
  --direction upstream`、`detect-changes --base-ref <sha-or-ref>`だけとする。wrapperはrepo rootとHEADをGitから推論し、upstream argvをanalyze=`analyze <repo-root> --skip-agents-md --skip-skills`、impact=target位置引数+upstream
  direction、detect=`--scope compare --base-ref`へexact変換する。impactはexit 0かつstdoutのJSON parse成功、analyze/detectはexit 0かつstdoutの非空textを必須とし、detectの正規なno-change textも許可する。通常logはstderrへpassthroughし、task/attempt/hash/evidence
  path引数やartifact生成を追加しない。`npm ci --prefix
  scripts/test/gitnexus-runtime`を実行し、前後でpackage-lock byte差0を確認する。本項目の新規lock作成だけは横断lock更新禁止の例外とし、package.json作成後に`test "$(node -p 'process.versions.node.split(".")[0]')" = 22 && test "$(npm
  --version | cut -d. -f1)" = 10 && npm install --package-lock-only --ignore-scripts --prefix scripts/test/gitnexus-runtime`を1回実行し、top dependency/integrity test合格後は`npm ci`だけを使う。exact installは`RF00A_SHA="$(git rev-parse
  HEAD)" && RF_GNX_LOCK_COPY="$(mktemp -t rf00n-lock.XXXXXX)" && cp --
  scripts/test/gitnexus-runtime/package-lock.json "$RF_GNX_LOCK_COPY" && npm ci --prefix scripts/test/gitnexus-runtime && cmp -- "$RF_GNX_LOCK_COPY" scripts/test/gitnexus-runtime/package-lock.json`とする。`node_modules`はstageしない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-00N`と`bash scripts/test/run-required-suites.sh RF-00N`がRF-00E作業中のA/N active段階でexit 0。bootstrap時は`python3 -m unittest -v
  scripts.test.test_gitnexus_refactor_bootstrap`を実行。tests=`test_manifest_is_private_and_requires_node_22_npm_10`,`test_lock_pins_gitnexus_1_6_9_and_exact_integrity`,`test_wrapper_rejects_unknown_duplicate_missing_ref_and_dash_prefixed_values`,`test_wrapper_uses_execfile_without_shell_and_preserves_command_specific_output`,`test_analyze_argv_uses_repo_root_optional_force_and_skips_agent_outputs_exactly`,`test_upstream_argv_maps_impact_target_direction_and_compare_base_exactly`,`test_impact_requires_json_but_analyze_and_detect_accept_nonempty_text`,`test_empty_text_and_invalid_impact_json_fail`,`test_staged_code_symbol_is_detected_while_symbol_less_files_use_cached_scope_check`,`test_wrapper_infers_repo_root_and_head`。
  Node/npm majorが22/10、`scripts/test/gitnexus-runtime/node_modules/.bin/gitnexus --version`がexact `1.6.9`、上記5 target pathだけをstageした後の`bash scripts/test/run-gitnexus-refactor.sh analyze --force`と`bash
  scripts/test/run-gitnexus-refactor.sh detect-changes --base-ref "$RF00A_SHA"`がexit 0かつstaged codeのsymbol/processを報告し、symbolなしfileはcached write-union照合で包含、package-lock差分0、tracked `node_modules` 0。suite=なし。
- リスク/戻し方: npm registry取得不能、upstream tarball差替え、wrapper自身のfalse-pass。取得不能やintegrity差はversionを緩めず中断する。commit後検証失敗はbranch/evidenceを保持し、RF-00A SHAから新worktreeで同じRF-00Nを再実装する。global installやcontrol helperで代用しない。 失敗時はR0。
- 依存: RF-00A
- コミット: `RF-00N pin tracked GitNexus runtime and wrapper`

### RF-00E 再現可能なtest environment bootstrap
- 対象: 新規 `scripts/test/bootstrap-refactor-env.sh:1-末尾`; 新規 `scripts/test/run-refactor-item.sh:1-末尾`; 新規 `scripts/test/run-refactor-item.py:1-末尾`; 新規 `scripts/test/run-required-suites.sh:1-末尾`; 新規
  `scripts/test/run-refactor-phase-gate.sh:1-末尾`; 新規 `scripts/test/run-refactor-release-gate.sh:1-末尾`; 新規 `scripts/test/run-full-refactor-verification.sh:1-末尾`; 新規
  `scripts/test/refactor-item-matrix.json:1-末尾`; 新規 `scripts/test/refactor-python-constraints.txt:1-末尾`; 新規 `tests3/unit/test_refactor_item_runner.py:1-末尾`; 新規 `tests3/unit/test_refactor_phase_gate.py:1-末尾`; read-only:
  `services/dashboard/package-lock.json:1-末尾`, `packages/transcript-rendering/package-lock.json:1-末尾`, `services/vexa-bot/package-lock.json:1-末尾`
- 問題: clean worktreeには`.venv`と`node_modules`がなく、既存計画のtest commandを実行できない。system Python 3.9はMeeting APIの`>=3.11`条件も満たさない。
- 変更: bootstrapは`RF_OFFICIAL="$(git rev-parse --show-toplevel)"`、`RF_ENV_ROOT="$RF_OFFICIAL/.pipeline/tmp/full-repo-refactoring-2026-07-24-v2/refactor-env"`、`RF_PYTHON311="$(command -v python3.11)"`へ固定し、executable不存在またはversion
  tupleが`(3,11)`以外なら`blocked: Python 3.11 required`でexit 2。resolver venvへ全groupと共通`pytest pytest-asyncio httpx`をinstallし、`pip freeze --all`からlocal editableとpip/setuptools/wheelを除くPEP 503正規名のsorted
  `name==version`を`refactor-python-constraints.txt`へ一度生成する。fresh backend/transcription/integrations/aux venvは必ず同じ`-c`で再installし`pip check`する。
- Python groupはexactに、backend=`-e libs/schema-sync -e libs/admin-models -e services/meeting-api -e services/runtime-api[dev] -e services/agent-api[all]`+Meeting/Agent/Gateway/Admin/Calendar requirements、transcription=Transcription requirements、integrations=`-e services/meeting-api -e packages/vexa-client -e packages/vexa-cli`+MCP/Telegram requirements、aux=Wake STT/Orchestrator/TTS/Voiceprint requirementsとする。constraints外解決、別requirements、system site packagesを禁止し、CLIは`$RF_ENV_ROOT/integrations/bin/vexa`だけを使う。
- Nodeは`npm ci --prefix packages/transcript-rendering`、`npm ci --prefix services/dashboard`、`npm ci --prefix services/vexa-bot`だけを使いlock差分0を確認する。Dashboard Playwrightは`services/dashboard/node_modules/.bin/playwright`、CoreのPlaywright/tsc/tsxは`services/vexa-bot/core/node_modules/.bin/playwright`、同`tsc`、同`tsx`へ固定し、各cwdでChromium installとlaunchを通す。matrixは2.1/2.3のwrite union、129 ID、2-state、両圧縮展開を実装し、shell評価、0件、active missing/skip/xfail、unknown ID、production fallback、別test/parameter、他ID state変更を失敗にする。phase/wave verifierは保存済みitem report、stable-unique suite、CI、tracked wrapperだけを確認する。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-00E`と`bash scripts/test/run-required-suites.sh RF-00E`がexit
  0。tests=`test_all_plan_ids_have_exact_matrix_entries`,`test_matrix_has_only_planned_active_contiguous_prefix`,`test_planned_allows_missing_but_active_requires_literal_tests`,`test_only_current_state_can_change`,`test_write_union_allows_exact_tests_and_rejects_production_fallback_other_tests_parameters_and_states`,`test_matrix_rejects_unexpanded_brace_nodeids`,`test_matrix_expands_inherited_path_nodeids_and_rejects_ambiguous_or_bare_suffix_argv`,`test_machine_reports_bind_argv_counts_required_names_and_head`,`test_python311_root_constraints_and_four_install_manifests_are_exact`,`test_core_local_playwright_tsc_tsx_paths_and_both_chromium_launches`,`test_missing_infrastructure_is_blocked_never_skipped_or_passed`,`test_each_phase_has_exact_nonempty_ordered_item_set`,`test_release_boundaries_expand_to_every_plan_item_exactly_once`,`test_commit_history_matches_subjects_and_state_activation`。4
  venv `pip check`、freezeとconstraints一致、3 lock差分0、両Chromium launch、Core-local V-CORE、`$RF_ENV_ROOT/integrations/bin/vexa --help`がpass。suite=V-CLIENTS,V-OPS。
- リスク/戻し方: dependency download失敗、重いTorch/Whisper package、宣言rangeによる解決差。network/install権限を得られなければ中断し、testをskipしない。ephemeral envは削除せず再利用または手動cleanup対象として報告する。失敗時はR0。
- 依存: RF-00N
- コミット: `RF-00E add reproducible refactor test bootstrap and item runner`

### RF-00B Backend契約の特性test
- 対象: 新規 `services/meeting-api/tests/test_lifecycle_characterization.py:1-末尾` 新規 `services/meeting-api/tests/test_request_bot_characterization.py:1-末尾` 新規
  `services/meeting-api/tests/test_final_transcription_characterization.py:1-末尾` 新規 `services/transcription-service/tests/test_gemini_boundary_golden.py:1-末尾` 新規 `services/runtime-api/tests/test_scheduler_characterization.py:1-末尾`
  新規 `services/api-gateway/tests/test_route_inventory_characterization.py:1-末尾` 新規 `tests3/unit/refactor/test_rf_00b.py:1-末尾`
- 問題: 後続抽出が守るべきpayload、状態遷移、副作用順序が複数testへ分散し、一部は未固定。
- 変更: Meeting lifecycle、Bot request、deferred transcription、Gemini境界、Runtime scheduler、Gateway route inventoryの現行契約をsecret-free fixtureへ固定する。既知bugはresolver項目を明記したstrict xfailとし、該当項目でだけ通常passへ変える。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-00B`と`bash scripts/test/run-required-suites.sh RF-00B`がexit 0。`services/meeting-api/tests/test_lifecycle_characterization.py::test_terminal_matrix_snapshot`
  `services/meeting-api/tests/test_request_bot_characterization.py::test_standard_browser_and_agent_runtime_specs_snapshot`
  `services/meeting-api/tests/test_final_transcription_characterization.py::test_deferred_transcription_side_effect_order_snapshot`
  `services/transcription-service/tests/test_gemini_boundary_golden.py::test_ascii_japanese_emoji_speaker_and_overlap_matrix_snapshot`
  `services/runtime-api/tests/test_scheduler_characterization.py::test_job_and_terminal_history_schema_snapshot` `services/api-gateway/tests/test_route_inventory_characterization.py::test_every_current_route_is_observed`
  `tests3/unit/refactor/test_rf_00b.py::test_known_xfail_inventory_has_exact_three_owner_cases_and_resolvers` 上記nodeidを個別実行し、2.3表でRF-11/RF-05A/RF-24へbindした3 strict xfail以外が全てpass。 `V-MEETING`、`V-BACKEND`、`V-TRANSCRIPTION`
  がbaseline以上。 goldenにtoken、password、実UUID、現在時刻が含まれない。 suite=V-BACKEND,V-MEETING,V-TRANSCRIPTION。
- リスク/戻し方: 現状バグをgoldenへ固定する危険。上記xfail対象以外の差異が出たら実装へ進まず、fixtureの観測方法を報告する。失敗時はR0。
- 依存: RF-00E
- コミット: `RF-00B add backend characterization safety net`

### RF-00C Frontend/Core契約の特性testと視覚baseline
- 対象: `services/dashboard/tests/**:1-末尾`; `packages/transcript-rendering/src/*.test.ts:1-末尾`; `services/vexa-bot/core/src/**/*.test.ts:1-末尾`; 新規 `services/vexa-bot/core/test-registry.json:1-末尾`; 新規
  `services/vexa-bot/core/scripts/run-tests.mjs:1-末尾`; 新規 `services/dashboard/tests/refactor/rf_00c.test.ts:1-末尾`; 新規 `services/dashboard/tests/test_transcript_baseline.test.ts:1-末尾`; 新規
  `services/dashboard/tests/fixtures/deferred-promise.ts:1-末尾`; 新規 `services/dashboard/scripts/check-lint-baseline.mjs:1-末尾`; 新規 `services/dashboard/tests/fixtures/lint-baseline.json:1-末尾`;
  `services/dashboard/src/app/meetings/page.tsx:1-末尾`; `services/dashboard/src/app/meetings/[id]/page.tsx:1-末尾`; `services/dashboard/src/components/layout/app-layout.tsx:1-末尾`;
  `services/dashboard/src/components/meetings/{meeting-card,browser-session-view}.tsx:1-末尾`; `services/dashboard/src/components/transcript/{transcript-viewer,transcript-segment}.tsx:1-末尾`;
  `services/dashboard/src/components/recording/{audio-player,video-player}.tsx:1-末尾`; 新規 `services/vexa-bot/core/src/refactor-tests/rf_00c.test.ts:1-末尾`; 新規
  `services/dashboard/tests/e2e/refactor-visual.spec.ts:1-末尾`; 新規 `services/dashboard/playwright.refactor.config.ts:1-末尾`; `services/dashboard/package.json:1-末尾`; 新規
  `services/dashboard/tests/e2e/refactor-visual.spec.ts-snapshots/**:1-末尾`; 新規 `services/dashboard/tests/fixtures/refactor-e2e/**:1-末尾`; 新規
  `packages/transcript-rendering/src/test-fixtures/panel20-sanitized.json:1-末尾`; `services/dashboard/next.config.ts:1-末尾`; read-only:
  `.pipeline/plans/full-repo-refactoring-2026-07-24-v2/planned-visual-changes.json:1-末尾`
- 問題: DOM test基盤が薄く、巨大component内の正しい挙動と既知bugが区別されていない。調査時のlive imageは現HEADと一致しない。
- 変更: `planned-visual-changes.json`をaction生成器として解釈せず、そこに列挙済みの匿名化された具象JSON/binary fixtureを`services/dashboard/tests/fixtures/refactor-e2e`へcommitする。9 scenarioを直接parameterizeした
  `refactor-visual.spec.ts` 1本でdesktop/mobile計18枚を撮影し、同じspecをbaseline/finalに使う。固定clock、locale、timezone、viewportを使い、console/page/network error 0、DOM role/textを検証する。意図したUI変更だけselector
  mask内の画像差分を許す。`panel20.test.ts`は`packages/transcript-rendering/src/test-fixtures/panel20-sanitized.json`を必須fixtureとして読み、`existsSync`と`describe.skipIf`を削除して既存5 caseを通常testへ変える。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-00C`と`bash scripts/test/run-required-suites.sh RF-00C`がexit
  0。tests=`test_transcript_correct_behavior_baseline`,`test_deferred_store_fixture_exposes_request_generation`,`test_fixture_routes_use_numeric_ids_native_ids_and_exact_raw_dtos_without_real_identity_or_token`,`test_exact_eighteen_shot_inventory_is_shared_by_baseline_and_final`,`test_runner_builds_and_starts_its_own_exact_tree_and_rejects_external_base_url`,`test_s09_fake_websocket_baseline_query_and_final_cookie_bff_contract`,`test_rgba_diff_counts_one_pixel_when_any_single_channel_differs`,`test_selector_mask_rejects_one_pixel_outside_union`。18
  PNG、個人情報/実token 0、console/page/network error 0。`panel20.test.ts`は5 pass/skip 0。`cd services/dashboard && REFRACTOR_VISUAL_MODE=baseline npm run test:e2e:refactor -- --update-snapshots`をRF-00Cで1回実行し18枚を同commitへ含める。suite=V-DASH,V-TRANSCRIPT,V-CORE。
- リスク/戻し方: build生成物混入とlive個人情報の保存。tracked差分を確認し、個人情報が1件でも映る場合は画像をcommitせず停止する。fixtureと通信遮断を直して18枚すべてを再取得し、必要枚数やassertionを減らさない。失敗時はR0とし、test/evidence差分を保持して再実行する。
- 依存: RF-00E
- コミット: `RF-00C add frontend characterization and visual baseline`

### RF-00D Ops/Harnessの偽陽性baseline
- 対象: `tests3/test-registry.yaml:1-793`; `tests3/checks/registry.json:1-1581`; `tests3/registry.yaml:1-3779`; `tests3/Makefile:1-338`; 新規 `tests3/unit/refactor/test_rf_00d.py:1-末尾`; 新規
  `tests3/unit/fixtures/fake-bin/{curl,docker,helm,git}:1-末尾`; 新規 `tests3/unit/fixtures/registry-baseline.json:1-末尾`; read-only: `deploy/compose/**:1-末尾`, `deploy/lite/**:1-末尾`, `deploy/helm/**:1-末尾`, `scripts/harness/**:1-末尾`,
  `.github/workflows/**:1-末尾`
- 問題: commandが0を返しても検査実体がない経路がある。実環境Docker/GCPを使わず再現できるfixtureが必要。
- 変更: PATH先頭へdelegateしないfake curl/docker/helm/gitを置くpytest fixtureを追加する。registry 91件と不存在45件、0 report、all skip、0 step、timeout、schema init失敗を再現し、後続RF-31〜42で一件ずつ通常passへ変える。実Docker、Kubernetes、GCPへ接続しない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-00D`と`bash scripts/test/run-required-suites.sh RF-00D`がexit 0。strict xfailのcollection/result確認は`run-refactor-item.sh RF-00D`と`V-OPS`のmachine report内assertionとして行い、本文から別のraw
  pytestを実行しない。 `tests3/unit/refactor/test_rf_00d.py::test_fail_open_baseline`の項目固有runnerに登録した8 parameter IDと、`tests3/unit/refactor/test_rf_00d.py::test_fake_binaries_never_delegate_to_real_tools` 実Docker daemon、Kubernetes
  cluster、GCPへ接続していない。 registry 91件、不在45件がbaseline JSONと一致するか、差異理由が記録される。 Required suite: `V-OPS`。 suite=V-OPS。
- リスク/戻し方: fake binaryが実commandを呼ぶ危険。fixture内で絶対にdelegateせず、受けたargvと指定exitだけ返す。失敗時はR0。
- 依存: RF-00E
- コミット: `RF-00D add fail-open infrastructure characterization`

### 認可・秘密値・破壊操作

### RF-01 Harness task-idのpath containment
- 対象: `scripts/harness/backcast-approval.sh:1-末尾` `scripts/harness/backcast-checkpoint.sh:1-末尾` `scripts/harness/backcast-current.sh:1-末尾` `scripts/harness/backcast-evidence-pack.sh:1-末尾`
  `scripts/harness/backcast-manifest.sh:1-末尾` `scripts/harness/backcast-next-checkpoint.sh:1-末尾` `scripts/harness/backcast-state.sh:1-末尾` `scripts/harness/build.sh:1-末尾` `scripts/harness/codex-build.sh:1-末尾`
  `scripts/harness/codex-review.sh:1-末尾` `scripts/harness/codex-session-ledger.sh:1-末尾` `scripts/harness/delivery-integrity-smoke.sh:1-末尾` `scripts/harness/external-consultation.sh:1-末尾` `scripts/harness/full-loop-smoke.sh:1-末尾`
  `scripts/harness/outcome-judge.sh:1-末尾` `scripts/harness/review-policy-smoke.sh:1-末尾` `scripts/harness/sml-decision.sh:1-末尾` `scripts/harness/task-set.sh:1-末尾` `scripts/harness/validate-runtime-profile.sh:1-末尾`
  `scripts/harness/worktree.sh:1-末尾` `schemas/checkpoint-contract.schema.json:26-29` 新規 `scripts/harness/lib/task-path.sh:1-末尾` read-only inventory: `git grep -n -F -e '$TASK' -e '${TASK}' -- scripts/harness`。上記20 shell
  file以外にtask-idをpathへ結合するproduction callerが1件でもあれば変更せずplan reviewへ戻る
- 問題: `/abs`、`../x`、`.`、`..` 等が `.pipeline` 外のpathを作り得る。
- 変更: `validate_task_id` は `^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$` のみ許可する。全pathはresolve後にrepo root配下かつ指定 `.pipeline/<kind>/` 配下であることを共通helperで再検証する。schemaのpatternも同一にする。

変更前:

```text
task_id="$1"
target=".pipeline/sessions/$task_id"
```

変更後:

```text
task_id="$(validate_task_id "$1")" || exit 2
target="$(pipeline_path sessions "$task_id")" || exit 2
```
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-01`と`bash scripts/test/run-required-suites.sh RF-01`がexit 0。`test_task_id_validation.py::test_rejects_dot_dot_absolute_separator_control_and_overlong`
  `::test_rejected_id_writes_nothing_outside_pipeline` `::test_valid_existing_task_ids_pass` `V-OPS` suite=V-OPS。
- リスク/戻し方: 過去の特殊文字付きtask-idが拒否される。項目0の現存ID監査で不適合があればrenameせず中断。失敗時はR0。
- 依存: RF-00D
- コミット: `RF-01 contain harness task paths`

### RF-02 Admin DB再作成の破壊防止
- 対象: `libs/admin-models/admin_models/database.py:118-144` `services/admin-api/app/scripts/recreate_db.py:29-59` `services/meeting-api/meeting_api/database.py:1-末尾`
- 問題: Admin側だけ誤接続したDBのschemaを確認なしでdrop/recreateできる。
- 変更: Meeting側のguardを共通関数`require_destructive_schema_permission()`として`libs/admin-models`へ移す。`VEXA_ENV`はliteral
  `development|test`だけ、`ALLOW_DROP_SCHEMA=true`、`ALLOW_DROP_SCHEMA_DB_NAME=DB_NAME`を接続前の最低条件とし、production/unset/unknown、どれか未設定、不一致、文字列`true`以外はconnection/SQL実行前にexit 2。
  同名DBを別serverへ誤接続しても通らないよう、接続後かつDDL前にread-onlyで`current_database(),inet_server_addr(),inet_server_port(),current_user`と、初回安全なDB
  bootstrapで作成済みの管理tableから`database_sentinel_uuid`を取得する。operator入力`ALLOW_DROP_SCHEMA_TARGET_FINGERPRINT`はcanonical JSON `{"database","host_ip","port","user","sentinel_uuid"}`のSHA-256 lowercase
  hexで、取得値から再計算したfingerprintとconstant-time exact一致しなければDDL 0でexit 2。sentinel不存在・重複・型不正も自動作成せず拒否する。 CLI対話は固定語`recreate`ではなく、取得したfingerprintの先頭12桁を含む`recreate
  <12hex>`の完全一致を1回だけ要求する。stdinがTTYでない、余分な空白、case違い、EOFは拒否する。`--dry-run`はsecretを含まないdatabase/host IP/port/user/sentinel hash/fingerprintと各guardのpass/failだけ表示し、DDLを実行しない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-02`と`bash scripts/test/run-required-suites.sh RF-02`がexit 0。`test_database_guard.py::test_recreate_rejected_without_explicit_allow`
  `::test_recreate_rejected_for_production_environment` `::test_recreate_allowed_only_for_exact_environment_and_database_fingerprint` `::test_same_database_name_on_wrong_host_performs_zero_ddl`
  `::test_missing_or_wrong_sentinel_and_wrong_confirmation_perform_zero_ddl` testではfake connectionを使い、実DBへ接続しない。 `V-BACKEND` suite=V-BACKEND。
- リスク/戻し方: 開発者の既存操作が止まる。guardを迂回せず、新しい明示envをREADMEへ記載する。drop実行後のデータは戻らないため本項目testはmock限定。失敗時はR3。
- 依存: RF-00B
- コミット: `RF-02 guard destructive admin schema recreation`

### RF-03A Agent専用runtime config契約を追加してconsumerを先に移行
- 対象: `services/admin-api/app/main.py:821-883` `services/meeting-api/meeting_api/schemas.py:329-381` `services/agent-api/agent_api/config.py:12-27` `services/agent-api/agent_api/container_manager.py:30-105,138-176`
  `services/agent-api/agent_api/chat.py:116-130` `deploy/compose/docker-compose.yml:1-末尾` `deploy/lite/{entrypoint.sh,supervisord.conf}:1-末尾`
  `deploy/helm/charts/vexa/{values.yaml,templates/secret.yaml,templates/deployment-admin-api.yaml}:1-末尾` 対象外（変更禁止）: Helm deploymentが存在しないAgent service向けHelm object 新規 `services/admin-api/tests/test_agent_runtime_config.py:1-末尾` 新規
  `services/agent-api/tests/test_agent_runtime_config.py:1-末尾`
- 問題: Agentは汎用`/admin/users/{id}`から`data.env`と`workspace_git.token`を取る。公開DTOだけ先にallow-list化するとcontainer/env/git cloneを壊し、Agentへ全権Admin tokenを持たせ続ける。
- 変更: Adminへ`GET /internal/users/{user_id}/agent-runtime-config`を`include_in_schema=False`で追加する。AdminとAgentだけに配る専用`AGENT_RUNTIME_CONFIG_SECRET`を`X-Agent-Runtime-Config-Secret`でconstant-time比較してからDBへ触り、未設定はproduction startup
  failure、欠落/不一致403。既存`INTERNAL_API_SECRET`、Admin key、Gateway identity secretはこのendpointで拒否する。 responseは `env: dict[str,str]` と `workspace_git: {repo, branch, token}|null`だけ。未知keyを返さず、型不正422、userなし404、設定なしは空/null。
  Agentの`get_user_data()`を`get_agent_runtime_config()`へ置換し、`ADMIN_API_TOKEN`/`X-Admin-API-Key`を削除する。Compose/Lite/Helmは`AGENT_RUNTIME_CONFIG_SECRET`をAdminとAgentへだけ配り、Meeting/Runtime/Gateway/Bot/Browserへ配らない。cache
  key/TTL/invalidationは維持する。 値をlog/exception/URLへ出さず、`env_count`と`has_workspace_git`だけ記録する。公開User DTOは本項目ではまだ変えない。
- 判断固定: 境界: Agent用runtime configはservice内部型だけを返し、公開User DTOを再利用しない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-03A`と`bash scripts/test/run-required-suites.sh RF-03A`がexit
  0。`services/admin-api/tests/test_agent_runtime_config.py::{test_internal_config_rejects_missing_wrong_legacy_and_cross_audience_secret,test_internal_config_returns_only_env_and_workspace_git}`
  `services/agent-api/tests/test_agent_runtime_config.py::{test_agent_uses_internal_endpoint_without_admin_token,test_runtime_config_secret_never_appears_in_logs}` canary
  secretはresponse/caplog/exceptionに0件。render済みdeployではAdmin/Agent以外のenv/Secret refに0件。 `rg -n 'ADMIN_API_TOKEN|X-Admin-API-Key' services/agent-api`はnegative assertion以外0件。 `V-BACKEND`。 suite=V-BACKEND。
- リスク/戻し方: Adminだけ戻すと新Agentがconfigを取れない。rolloutはAdmin→Agent、rollbackはAgent→Admin。DB保存形式は変えず、失敗branchを保持して前SHAから再実行。 失敗時はR0。
- 依存: RF-00B
- コミット: `RF-03A add a least-privilege agent runtime config contract`

### RF-03B Scoped user APIへconsumerを移して公開User DTOをallow-list化
- 対象: `services/meeting-api/meeting_api/schemas.py:329-381,1262-1267` `services/admin-api/app/main.py:161-285,339-470,724-819` 新規 `services/admin-api/app/webhook_delivery_broker.py:1-末尾` `services/api-gateway/main.py:1405-1433`
  `services/api-gateway/main.py:321-466` `services/meeting-api/meeting_api/meetings.py:990-1001` `services/meeting-api/meeting_api/webhooks.py:100-289` `services/meeting-api/meeting_api/webhook_delivery.py:99-263`
  `services/meeting-api/meeting_api/webhook_retry_worker.py:115-258` 新規 `scripts/migrations/migrate_webhook_credentials.py:1-末尾` `services/dashboard/src/app/api/webhooks/{config,rotate-secret,test,deliveries}/**:1-末尾`
  `services/dashboard/src/app/api/calendar/oauth/start/route.ts:1-107` `services/dashboard/src/app/api/calendar/oauth/complete/route.ts:1-198` `services/dashboard/src/app/api/zoom/oauth/start/route.ts:1-104`
  `services/dashboard/src/app/api/zoom/oauth/complete/route.ts:1-205` 新規 `services/admin-api/tests/test_public_user_contract.py:1-末尾` 新規 `services/admin-api/tests/test_user_webhook_contract.py:1-末尾` 新規
  `services/admin-api/tests/test_webhook_delivery_broker.py:1-末尾` 新規 `services/meeting-api/tests/test_webhook_credential_refs.py:1-末尾` 新規 `services/api-gateway/tests/test_user_config_proxy.py:1-末尾` 新規
  `services/api-gateway/tests/test_oauth_state_registry.py:1-末尾` 新規 `services/dashboard/tests/test_user_scoped_webhook_routes.test.ts:1-末尾` 新規 `services/dashboard/tests/test_oauth_partial_write.test.ts:1-末尾` 新規
  `services/dashboard/tests/test_oauth_subject_binding.test.ts:1-末尾`
- 問題: deny-listはnested git/env/OAuth/webhook secretを公開User/analyticsへ露出し得る。Dashboard webhook BFFも全権Admin keyで汎用User JSONをread-modify-writeする。
- 変更: 認証tokenのsubjectだけを使う `GET/PUT /user/webhook`、`POST /user/webhook/rotate-secret`、`POST /user/webhook/test`、`GET /user/webhook/deliveries`、`GET /user/workspace-git`をAdmin/Gatewayへ追加する。requestに`user_id`を受けない。Admin-owned
  `user.data.webhook_profiles`はversioned mapとし、各versionを`{credential_ref,endpoint_url,webhook_secret,events,status,created_at}`へ固定する。`credential_ref`はCSPRNG 128-bit以上、subject/versionへ一意。raw secretを許すのはAdmin DBのこのrecord、rotate
  response 1回、Admin delivery brokerのrequest-local memoryだけ。 GETはmasked secretとactive `credential_ref`、rotateだけ新secretを1回返す。PUTでsecret省略時は既存versionを維持し、URL/events/secretのどれかを変える場合は新version/refを作る。testは保存済みrefだけを使い、body
  `url,secret,headers`を422、redirectを追わない。完全なoutbound policyはRF-05Eで行う。 Gateway token validation response/cache/envelope/headerから`webhook_url,webhook_secret,webhook_events`を削除し、Meeting createにはsubject-bound
  `webhook_credential_ref`だけを渡す。Meeting row/data、delivery record、Redis retry jobは`credential_ref,event_type,payload,attempt,next_retry_at,created_at,metadata`だけを保存し、URL、secret、Authorization/HMAC headerを0にする。version
  refがURL/events/secretのsnapshotをAdmin側で束縛するため、会議作成時設定を固定する既存挙動は維持する。 Adminへ固定`POST /internal/webhook-deliveries`を追加する。RF-05A後のMeeting
  identityは`sub,meeting_id,credential_ref,event_type,payload_sha256,attempt_id,iat,exp<=30,jti`をbindする。Admin brokerはref owner/status/versionを確認し、dispatch直前だけDBからURL/secretを解決してHMAC/Bearer headerをrequest-local
  memoryで生成し、response/log/queueへ返さない。wrong subject/ref/audience/body/replayはHTTP transport 0。同じ業務`attempt_id`はdelivery idempotency、identity `jti`はrequestごとに新規とし混用しない。 migrationは新規webhook enqueueをfreezeし、Meeting
  JSONBと`webhook:retry_queue`をbounded inventoryする。raw値はmemory内だけでAdmin profile versionへupsertし、Meeting row/queueをrefへ置換する。値をstdout/log/evidence/tmpへ出さず、`migrated+revoked+expired=inventory`と全legacy raw field
  0を確認してfreezeを解除する。crash/retryは既にref化済みrecordをbyte不変でskipする。 Dashboard webhook routeをcookie user tokenでGatewayへproxyするだけにし、`VEXA_ADMIN_API_KEY`、汎用`/admin/users`、raw URL、Dashboard内HMACを削除する。 Calendar/Zoom OAuth
  startはbodyの`userEmail`を認可に使わない。既存HttpOnly user tokenをGatewayへ送り、Gatewayが解決した認証subjectだけを採用する。bodyに`userEmail,userId,redirectUri,provider`があれば400、表示用cookie/emailと不一致でも認証token subjectを変えない。`returnTo`はsingle leading
  `/`、backslash/authority/encoded authority/controlなしのsame-origin relative pathだけを許し、不正値は`/meetings`へ黙って丸めず400にする。 Gatewayへ認証subject専用`POST /user/oauth/state/{calendar|zoom}`と`POST
  /user/oauth/state/{calendar|zoom}/consume`を追加する。startはCSPRNG 256-bitのopaque stateとRFC 7636 verifierを発行し、Redis
  `oauth-state:<sha256(state)>`へ`provider,subject_id,redirect_uri,return_to,pkce_verifier_ciphertext,nonce,key_id,iat,exp`だけを`SET NX EX 600`で保存する。verifierはGatewayだけに配る32-byte
  `OAUTH_STATE_ENCRYPTION_SECRET`でAES-256-GCM暗号化し、AADへprovider/subject/state hash/redirect/expをbindする。create responseはopaque stateとS256 challengeだけ、consume成功responseはDashboard serverへverifierを1回だけ返す。raw API/provider
  token、email、raw state、raw verifierをRedis/log/evidence/browser responseへ保存しない。redirect URIはprovider別server configのexact値だけとする。 completeは現在のHttpOnly user token subject、provider、server-config redirect URI、opaque stateをGateway
  consume endpointへ渡す。Gatewayは`WATCH`→`GET`→provider/subject/redirect/expiry照合→`MULTI DEL`→`EXEC`を最大5回行い、1 requestだけがconsume成功する。Lua/GET後単独DELを使わない。wrong provider/subject/redirect/expired/tampered、100並列replayは99件以上がtoken
  exchange/Admin PATCHより前に拒否され、失敗requestは正規stateを削除しない。成功requestはprovider exchangeより前にstateを消費し、exchange失敗でも同じstateを再利用できない。 Calendar/Zoom completionは汎用User dataをGET/mergeせず、consume responseのsubjectに対するprovider top-level
  keyだけを目的別Admin endpointへPATCHし、tokenをlog/responseへ出さない。state payloadへuser ID/email/provider credentialを埋める旧HMAC blobと`NEXTAUTH_SECRET`/Admin key fallbackを削除する。 consumer移行後、公開`PublicUserData`を
  `workspace_git:{repo,branch,has_token}`だけのallow-listへする。`env`、webhook/OAuth、未知keyを落とし、User detail/analyticsも同serializerを通す。 Admin PATCH logは`user_id`と変更key名だけにし、旧/new dataの値を削除する。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-03B`と`bash scripts/test/run-required-suites.sh RF-03B`がexit 0。`services/admin-api/tests/test_public_user_contract.py::test_all_user_and_analytics_responses_use_allow_list`
  `services/admin-api/tests/test_user_webhook_contract.py::{test_rotate_returns_secret_once_only,test_webhook_test_rejects_client_url_and_uses_stored_url}`
  `services/admin-api/tests/test_webhook_delivery_broker.py::{test_ref_is_subject_bound_and_raw_secret_never_leaves_admin_broker,test_wrong_subject_ref_audience_body_and_replay_make_zero_http_calls,test_rotate_returns_secret_once_and_creates_new_version}`
  `services/meeting-api/tests/test_webhook_credential_refs.py::{test_meeting_and_retry_queue_store_ref_but_no_url_header_or_secret,test_retry_resolves_ref_just_in_time_and_preserves_payload_and_backoff,test_legacy_rows_and_queue_are_migrated_without_value_logging}`
  `services/api-gateway/tests/test_user_config_proxy.py::test_validate_cache_and_meeting_proxy_never_contain_webhook_secret`
  `services/dashboard/tests/test_user_scoped_webhook_routes.test.ts::test_dashboard_never_calls_admin_users_for_webhooks`
  `services/dashboard/tests/test_oauth_partial_write.test.ts::{calendar_completion_updates_only_calendar_key,zoom_completion_updates_only_zoom_key}`
  `services/dashboard/tests/test_oauth_subject_binding.test.ts::{test_start_uses_authenticated_subject_and_rejects_user_email_user_id_and_redirect_override,test_return_to_rejects_authority_backslash_encoded_authority_and_control,test_complete_consumes_state_before_provider_exchange_and_never_uses_state_subject_from_client}`
  `services/api-gateway/tests/test_oauth_state_registry.py::{test_state_record_contains_only_hashes_and_nonsecret_subject_metadata,test_wrong_provider_subject_redirect_and_expiry_have_zero_consume_or_upstream_side_effects,test_hundred_parallel_consumers_produce_exactly_one_success,test_provider_exchange_failure_cannot_reuse_consumed_state,test_failed_mismatch_does_not_burn_valid_state,test_state_and_pkce_values_never_enter_log_redis_dump_or_response}`
  git/env/OAuth/webhook canaryは全public/Admin-user/analytics responseとcaplogに0件。RF-03A internal endpointとrotate直後だけexact例外。 `rg -n 'VEXA_ADMIN_API_KEY|/admin/users' services/dashboard/src/app/api/webhooks`、`rg -n
  'Current:.*data|New:.*data' services/admin-api/app`、`rg -n 'findUserByEmail|userEmail|NEXTAUTH_SECRET|signStatePayload' services/dashboard/src/app/api/{calendar,zoom}/oauth`はnegative assertion以外0件。 `V-BACKEND`, `V-DASH`。
  suite=V-BACKEND,V-DASH。
- リスク/戻し方: undocumented clientが汎用`user.data`を読む可能性、OAuth callback中の旧stateが無効になる可能性。汎用dataやstateless stateへ戻さず、配備前に新規startを一時停止し最大旧state TTL 600秒+clock skewを待ってからGateway→Dashboardの順に切り替える。失敗branchを保持しRF-03A直後の合格SHAから再実行する。 失敗時はR0。
- 依存: RF-03A, RF-00C
- コミット: `RF-03B replace generic user data reads with scoped contracts`

### RF-03C API token管理を認証subjectへ束縛しtoken一覧のraw再表示を止める
- 対象: `services/dashboard/src/lib/auth-utils.ts:13-58` `services/dashboard/src/app/api/profile/keys/route.ts:1-109` `services/dashboard/src/app/api/profile/keys/[id]/route.ts:1-50`
  `services/dashboard/src/app/profile/page.tsx:110-190,290-330` `services/admin-api/app/main.py:391-566,724-818` `services/meeting-api/meeting_api/schemas.py:360-373`
  `services/api-gateway/main.py:1-末尾`（既存`/auth/me`と新規`/user/tokens` routeの追加位置を含む） 新規 `services/admin-api/tests/test_user_token_contract.py:1-末尾` 新規 `services/api-gateway/tests/test_user_token_routes.py:1-末尾` 新規
  `services/dashboard/tests/test_profile_token_ownership.test.ts:1-末尾` 新規 `services/dashboard/tests/test_cookie_only_auth_state.test.ts:1-末尾`
- 問題: 有効なA tokenと未署名`vexa-user-info` cookieのB emailを組み合わせるとBのuser IDを採用し、全権Admin BFF経由でBのraw API token一覧・発行・任意ID削除へ到達できる。token一覧も既存raw値を再表示する。
- 変更: `getAuthenticatedUserId()`は表示用`vexa-user-info`を一切読まず、`vexa-token`をGateway `GET /auth/me`へ送り、そのresponseの`user_id`だけを認可subjectとして採用する。`/auth/me`が非200、`user_id`が正の整数でない、bodyが不正なら`null`。email/name cookieは表示専用で、認可・DB
  lookup・object keyに使わない。 Admin/Gatewayへ認証subject専用`GET /user/tokens`、`POST /user/tokens`、`DELETE /user/tokens/{token_id}`を追加する。clientから`user_id`を受けず、current
  tokenから解決したuserだけを対象にする。DELETEはtoken行の`user_id`一致をSQL条件へ含め、他人/不存在は同じ404、DELETE件数0。RF-05A後はtrusted identityへ差し替えるがpath/DTOは維持する。 Dashboard profile BFFはcookie user tokenをGatewayへproxyするだけにし、`VEXA_ADMIN_API_KEY`、Admin
  URL、`/admin/users`、`/admin/tokens`を削除する。 一覧DTOは`id,name,scopes,created_at,last_used_at,expires_at,masked_suffix`だけ。raw `token`はPOST作成responseで1回だけ返し、DB再読込/list/log/exceptionには返さない。既存token値を復号・再表示するfallbackは作らない。 profile
  pageは一覧行で`masked_suffix`だけを表示しcopy buttonを出さない。作成成功modalだけresponseのraw tokenをmemory stateへ保持し、modal close/unmountで破棄する。Git workspace token UIは別purpose contractなのでこの項目で変更しない。 Dashboardの既存auth response/store
  tokenはRF-04Aのcookie-only consumer移行と同時に削除する。RF-03C単体では認証response shapeを変えず、Phase 1完了前に途中deployしない。

変更前:

```ts
const email = JSON.parse(userInfoCookie).email;
return adminLookupByEmail(email).id;
```

変更後:

```ts
const me = await gatewayFetch("/auth/me", authCookie);
return isPositiveInteger(me.user_id) ? String(me.user_id) : null;
```

- 判断固定: token一覧DTOはexact `id,name,scopes,created_at,last_used_at,expires_at,masked_suffix`だけ。raw `token`は作成responseで一度だけ返す。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-03C`と`bash scripts/test/run-required-suites.sh RF-03C`がexit
  0。`services/dashboard/tests/test_profile_token_ownership.test.ts::{a_token_with_forged_b_user_info_still_resolves_a,normal_profile_routes_never_use_admin_credentials,token_list_and_profile_rows_never_contain_raw_token,created_token_is_visible_once_and_cleared_on_close}`
  `services/admin-api/tests/test_user_token_contract.py::{test_list_never_returns_raw_token,test_create_returns_raw_token_once,test_delete_requires_current_owner}`
  `services/api-gateway/tests/test_user_token_routes.py::{test_subject_bound_token_crud,test_cross_user_token_id_is_not_found}` AからBのlist/create/deleteは不可でB行更新0。list/profile row/caplogにtoken canary 0、create response/modalだけexact
  1件。既存auth responseの一時互換はこの項目のcanary対象外だがRF-04Aで必ず0にする。 `rg -n 'VEXA_ADMIN_API_KEY|/admin/users|/admin/tokens' services/dashboard/src/app/api/profile services/dashboard/src/lib/auth-utils.ts` は0件。 `V-BACKEND`, `V-DASH`。
  suite=V-BACKEND,V-DASH。
- リスク/戻し方: token値を一覧からcopyしていた利用者は作成時以外に再取得できなくなる。raw再表示を戻さず、UIへ「作成時のみ表示」を明記する。失敗branchを保持し、RF-03BのSHAから新worktreeで再実行する。 失敗時はR0。
- 依存: RF-03B, RF-00C
- コミット: `RF-03C bind token management to the authenticated subject`

### RF-03D Agent workspace Git cloneからshell・credential URL・任意originを除去
- 対象: `services/agent-api/agent_api/workspace.py:1-38,327-361` `services/agent-api/agent_api/chat.py:97-130` `services/agent-api/agent_api/config.py:1-末尾` 新規 `services/agent-api/agent_api/git_workspace.py:1-末尾` 新規
  `services/agent-api/tests/test_git_workspace_security.py:1-末尾` 新規 `services/vexa-agent/system/bin/vexa-git-bootstrap:1-末尾` `services/vexa-agent/Dockerfile:9-14` `deploy/compose/docker-compose.yml:175-209`
  `deploy/lite/entrypoint.sh:98-104` `deploy/lite/supervisord.conf:148-160`
- 問題: Agentのworkspace初期化もtokenを`https://<token>@host/...`へ埋め、shell文字列で`git clone && cp && rm`を実行する。remote config、process output、例外へtokenが残り、任意HTTPS originへのcredential送信とoutbound接続も可能である。
- 変更: `git_clone_init()`のshell
  commandを削除し、host側の`asyncio.create_subprocess_exec("docker","exec","-i",container,"/system/bin/vexa-git-bootstrap","--repo",credential_free_url,"--branch",validated_branch)`だけを使う`git_workspace.py`へ置換する。repo/branch/target/tokenをshell文字列へ連結しない。workspaceはhelper内constant
  `/workspace`で、hostからpathを受けない。tokenはDocker execのstdinへ`uint32 big-endian byte length + exact UTF-8 bytes + EOF`の1 frameで1回だけ送り、0 byte/64KiB超/invalid UTF-8/trailing byteをhelperがclone前に拒否する。host/containerのargv/env、clone URL、remote
  URL、logへtokenを入れない。 `vexa-git-bootstrap`をAgent imageの`/system/bin/`へ0755でCOPYする。helperはstdin tokenをmemoryへ読み、session-private tmpfs `/run/vexa-git/<128-bit random>/`へ0700 directory、0600 token fileと0500
  `GIT_ASKPASS`を作る。askpassはUsername promptへserver固定`x-access-token`、Password promptへtoken file内容だけを返し、その他のpromptを非0で拒否する。Git子process終了後`finally`でtoken/askpass/temp
  cloneを削除する。`GIT_TERMINAL_PROMPT=0`、`GIT_CONFIG_NOSYSTEM=1`、`http.followRedirects=false`、`protocol.allow=never`、`protocol.https.allow=always`を固定し、Git
  argvを`["git","clone","--branch",validated_branch,"--single-branch","--",credential_free_url,temp_dir]`へ固定する。成功後の`origin`もcredential-free URL exact。 URLは`https`、userinfo/fragmentなし、default port 443、server-side
  `AGENT_GIT_ALLOWED_HOSTS`のASCII lowercase exact hostだけを許す。allow-list未設定でworkspace git configが存在する場合はcontainer/Docker/Git副作用前にworkspace初期化を失敗させる。IP literal、localhost、single-label、解決した全A/AAAAのうち1件でもnon-global
  addressをclone前に拒否する。接続は検証済みIP集合へpinし、TLS SNI/証明書検証とHTTP Hostは元のallow-listed hostnameのままにする。retryは再解決・再検証・再pinし、redirectを全面禁止する。RF-06I2のhost-side egress policyも同じhost/IP/CIDR外をdenyする。
  既存workspaceをclone済みとして再利用する前に`.git/config`、worktree-local config、submodule configをregular file・no-symlinkでinventoryする。remote URLにuserinfo、credential
  helper/path、`http.*.extraHeader`、禁止scheme/hostが1件でもあればnetwork/Git前にworkspaceを`credential_quarantine`へ移し、値を表示せずkey名・remote host hashだけを返す。自動で安全化した体にせず、過去にURLへ入ったtokenはoperator rotation対象としてoperation
  evidenceへ件数だけ記録する。新規clone成功後も同じscrubberがcredential-free originと禁止config 0を確認してからworkspaceを公開する。 ComposeのNO-SHIP agent例とLite supervisorへ`AGENT_GIT_ALLOWED_HOSTS`を値変更なしで渡す。Lite entrypointは未設定時に空文字をexportするがdefault
  hostを追加しない。HelmにはAgent workload templateが存在しないため本項目で架空のHelm設定を追加せず、RF-51のownership catalogへ「Agent Helm wiringなし」を記録する。 branchはGit check-ref-format相当をpure validationし、先頭`-`、`..`、control/NUL、空、64 KiB超を拒否する。workspace/temp
  pathはserver constantだけで、user入力pathを受けない。clone/copy/cleanupのどれかが非0なら`False`、workspaceへpartial content 0、safe error codeだけを返す。 RF-03Aのinternal runtime configはtokenをAgent service memoryまで渡せるが、生成Agent
  containerの常駐env/config/inspectへは渡さない。本項目の短命stdin bootstrapだけを例外とし、RF-06E/06H後も同じsubject/egress契約を維持する。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-03D`と`bash scripts/test/run-required-suites.sh RF-03D`がexit
  0。`services/agent-api/tests/test_git_workspace_security.py::{test_clone_uses_installed_fixed_helper_argv_and_length_prefixed_stdin_without_shell,test_token_is_absent_from_url_argv_env_remote_log_and_exception,test_only_allowlisted_global_https_origin_and_valid_branch_reach_docker,test_connection_is_pinned_to_verified_ip_while_host_and_sni_remain_original,test_redirect_ip_literal_private_dns_userinfo_and_protocol_smuggling_are_rejected,test_empty_oversize_invalid_utf8_and_trailing_stdin_frames_are_rejected_before_git,test_failure_and_timeout_remove_temp_credentials_clone_and_partial_workspace,test_existing_credential_remote_extraheader_helper_and_submodule_are_quarantined_before_network,test_subject_workspace_config_cannot_select_another_subject_token,test_compose_and_lite_forward_allowlist_without_default_and_helm_adds_no_phantom_agent_workload}`
  fake Docker/Git fixtureでmetacharacterを含むrepo/branch/tokenから追加process 0、token canaryはstdin frameだけexact 1件、全captured argv/env/url/remote/log/exceptionに0。 valid fixtureはcredential-free origin、branch、workspace content
  hash一致。invalid origin/branch/DNSはDocker/Git process 0。 `V-BACKEND`, `V-OPS`。 suite=V-BACKEND,V-OPS。
- リスク/戻し方: self-hosted Git originやredirect依存cloneが止まる。allow-listやredirectを広げてその場で救済せず、必要originとglobal addressを別の承認済みconfig変更として報告する。失敗branchを保持しRF-03CのSHAから再実行し、URLへ露出済みの可能性があるtokenはGit外でrotateする。 失敗時はR0。
- 依存: RF-03A, RF-03C
- コミット: `RF-03D isolate agent git credentials from shell urls and arbitrary origins`

### RF-04A Dashboard browser-facing routeからservice tokenを除外
- 対象: `services/dashboard/src/app/api/config/route.ts:1-末尾` `services/dashboard/src/app/api/vexa/[...path]/route.ts:99-178` `services/dashboard/src/app/api/auth/me/route.ts:1-56`
  `services/dashboard/src/app/api/auth/verify/route.ts:185-217` `services/dashboard/src/app/api/auth/[...nextauth]/route.ts:1-末尾` `services/dashboard/src/app/api/auth/oauth-callback/route.ts:1-末尾`
  `services/dashboard/src/app/api/auth/send-magic-link/route.ts:1-末尾` `services/dashboard/src/app/api/auth/shared-login/route.ts:1-末尾` `services/dashboard/src/stores/auth-store.ts:1-240`
  `services/dashboard/src/app/auth/verify/page.tsx:36-101` `services/dashboard/src/hooks/use-runtime-config.ts:1-末尾` `services/dashboard/src/hooks/use-vexa-websocket.ts:1-末尾`
  `services/dashboard/src/hooks/use-live-transcripts.ts:60-90` `services/dashboard/src/app/meetings/[id]/page.tsx:118-140` `services/dashboard/src/lib/direct-login.ts:1-末尾` `services/dashboard/src/app/mcp/page.tsx:1-末尾`
  `services/dashboard/src/components/mcp/mcp-config-button.tsx:1-末尾` read-only既知一致: `services/dashboard/src/app/profile/page.tsx:171,179-180`のAPI key作成response一回表示だけ。RF-03C後の一覧・通常renderにraw token一致が残れば停止 read-only inventory: `git
  grep -n -E -e 'useAuthStore\(.*token|state\.token|data\.token|meData\.token|authToken' -- services/dashboard/src`。上記write対象と作成一回表示のread-only既知一致以外が1件でもあれば変更せずplan reviewへ戻る
- 問題: server-to-serverの`VEXA_API_KEY`をbrowser-readable JSONへ含め得るうえ、未認証`GET /api/vexa/meetings`が同service keyでprivate meeting一覧を代理取得する。
- 変更: browser公開schemaのtop-level keyをexact `wsUrl,apiUrl,publicApiUrl,decisionListenerUrl,defaultBotName,brand,sharedAuth,hostedMode,webappUrl`だけに固定し、追加keyをschema testで拒否する。`wsUrl`はrequest
  originと同一originの`ws(s)://<request-host><basePath>/ws`、`apiUrl`は同一origin/basePath、`publicApiUrl`はRF-20の外部CDP表示だけに使う公開URLとする。`authToken`を含むcredential/token/key fieldとservice credential fallback
  `process.env.VEXA_API_KEY`を除き、空文字fieldとして残さない。 `/api/auth/me`、verify、shared/direct/OAuth login responseから`token`
  fieldを削除する。`AuthState`から`token/setToken`を除去し、`setAuth(user)`、`isAuthenticated=Boolean(user)`へ固定する。persist対象は`user,isAuthenticated,didLogout`だけで、localStorage/sessionStorageへtokenを一度も書かない。 Zustand persist versionを1つ上げ、React store
  hydrateや`checkAuth()`より前にserver-rendered bootstrap scriptがversioned/idempotent migrationを実行する。exact legacy keys`vexa-auth`と既知旧aliasをlocalStorage/sessionStorageから削除し、token/authToken fieldを含むIndexedDB database/object store、Cache
  Storage、service-worker cacheをallow-list inventory後に削除する。未知origin-wide dataを全消去せず、symlink相当のないbrowser storage APIだけを使う。migration成功markerはtokenを含まないversionだけで、失敗時はprotected UI/networkを開始せずlogoutへfail closedする。local
  user/isAuthenticatedを引継がず強制server re-authし、legacy tokenは別operator rolloutでrevokeする。 `authToken`も公開JSONから削除し、DashboardのHTTP/SSE/WebSocket consumerをcookieを読むsame-origin BFFへ切り替える。BFFだけがserver-sideでcookie tokenをGatewayへ付ける。MCP/API
  key管理等の外部client token契約は変更しない。 login/verify pageはresponseの公開userだけで`setAuth(user)`し、`checkAuth()`は`{authenticated:true,user}`だけで成功する。cookie 200でもuser不正ならlogout、network error時はlocal userを認証証拠にせず`isAuthenticated=false`。 `GET
  /api/vexa/meetings`も他routeと同じくcookie user token必須にし、欠落時401・upstream fetch 0。pre-login browsingの匿名公開DTO/tenantは本計画では新設しない。 responseへ `Cache-Control: private, no-store` と `Vary: Cookie` を付け、共有cacheへ認証状態を保存させない。
  Dashboardの全browser-reachable routeでservice credentialを利用者credential fallbackに使わない。server-only deploy/admin automationは対象外だがbrowser requestから到達できないことをtestする。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-04A`と`bash scripts/test/run-required-suites.sh RF-04A`がexit
  0。`services/dashboard/tests/test_config_route.test.ts::{never_serializes_service_vexa_api_key,never_serializes_cookie_user_token,sets_private_no_store_and_vary_cookie,returns_only_documented_public_runtime_config_fields,ws_and_api_urls_are_same_origin_and_public_api_url_is_display_only}`
  `services/dashboard/tests/test_vexa_proxy_auth.test.ts::{meetings_requires_cookie_user_token,missing_cookie_makes_zero_upstream_calls,service_key_is_never_a_browser_fallback}`
  `services/dashboard/tests/test_cookie_only_auth_state.test.ts::{auth_responses_never_return_cookie_token,auth_store_never_persists_token,pre_hydration_migration_removes_every_known_legacy_local_session_indexeddb_cache_and_service_worker_token_shape,migration_failure_starts_no_protected_network_and_forces_logout,verify_and_shared_login_use_public_user_only,network_failure_does_not_trust_local_user,live_http_sse_and_websocket_use_same_origin_routes}`
  service canaryを設定してcookieなしで全BFFを叩き、upstream call 0。clean browserへ全legacy storage shapeをseedし、migration後のlocal/session/IndexedDB/Cache/DOM/logにcanary 0、server auth前のprotected request 0。 `rg -n
  'userToken\s*\|\|\s*process\.env\.VEXA_API_KEY|authToken|state\.token|meData\.token|data\.token' services/dashboard/src` はAPI key作成一回表示fixture以外0件。 `V-DASH`。lintはbaseline以下、新規0。 suite=V-DASH。
- リスク/戻し方: pre-login一覧と直接WebSocket接続が止まる。service key fallbackやraw token JSONを戻さず、同一origin proxyのmethod/status/stream互換をcharacterization testへ追加する。失敗branchを保持しRF-03CのSHAから再実行し、露出した可能性のあるservice/user tokenは別運用でrotateする。 失敗時はR0。
- 依存: RF-03C, RF-00C
- コミット: `RF-04A keep browser and service credentials separated`

### RF-04B Admin session cookieを単一の署名検証実装へ統合
- 対象: `services/dashboard/src/app/api/auth/admin-verify/route.ts:1-136` `services/dashboard/src/app/api/admin/[...path]/route.ts:1-149` 新規 `services/dashboard/src/lib/server/admin-session.ts:1-末尾` 新規
  `services/dashboard/tests/test_admin_session_auth.test.ts:1-末尾`
- 問題: admin verify GETはHMACを検証する一方、全権Admin proxyはcookie全体をbase64 decodeするだけ。攻撃者がunsigned JSONを自作して`VEXA_ADMIN_API_KEY`付きproxyを利用できる。
- 変更: server-only `admin-session.ts`へ`signAdminSession()`と`verifyAdminSession()`を1実装だけ置く。cookie wire formatを`v1.<base64url-payload>.<64桁hex HMAC-SHA256>`へ固定し、payloadは `{v:1,authenticated:true,iat:number,exp:number}` 以外をrejectする。
  verify順は`segment数/文字集合/長さ -> HMAC再計算 -> signature Buffer長一致 -> timingSafeEqual -> JSON/schema -> authenticated -> iat<=now+60s -> exp>now -> exp-iat<=24h`。比較前に長さ不一致をrejectし、例外を認証成功へ変換しない。 POSTのAdmin
  token比較も同長Bufferだけconstant-time比較する。missing secretは503でcookie/fetch 0、secretやcookieをlogしない。 admin verify GETとadmin proxyの全methodが同helperだけを呼ぶ。invalid/tampered/unsigned/expired/future-issued/wrong-secret/malformed
  cookieは401、Admin upstream fetch 0。valid cookieは既存method/path/query/body/status/content-typeを維持する。 cookie属性は`HttpOnly`、production/HTTPSで`Secure`、`SameSite=Strict`、`Path=/`、`Max-Age=86400`。全認証responseは`Cache-Control: no-store`。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-04B`と`bash scripts/test/run-required-suites.sh RF-04B`がexit
  0。`services/dashboard/tests/test_admin_session_auth.test.ts::{rejects_unsigned_forged_cookie,rejects_tampered_expired_future_wrong_secret_and_bad_length,accepts_valid_signed_cookie_for_all_proxy_methods,invalid_cookie_never_fetches_admin,uses_strict_cookie_attributes}`
  unsigned forged fixtureは現実装でproxy到達することをfix前red testで確認し、fix後401/Administrative fetch 0。 `rg -n 'Buffer\.from\(sessionCookie\.value,\s*"base64"\)|function verifyAdminSession' services/dashboard/src/app/api/admin
  services/dashboard/src/app/api/auth/admin-verify` は0件。 `V-DASH`。 suite=V-DASH。
- リスク/戻し方: 既存admin session cookieはwire format変更で一度ログアウトになる。旧unsigned decoderをdual acceptせず、401後の再ログインだけを許す。失敗branchを保持しRF-04AのSHAから再実行する。 失敗時はR0。
- 依存: RF-04A
- コミット: `RF-04B verify every admin session before privileged proxying`

### RF-05A Gateway route policyとtrusted identity envelopeをfail-closed化
- 対象: `services/api-gateway/main.py:88-100,321-419,691-1710,2432-2520` `services/meeting-api/meeting_api/meetings.py:1-末尾` `services/meeting-api/meeting_api/callbacks.py:1094-1165` `services/meeting-api/meeting_api/auth.py:1-54`
  `services/calendar-service/app/main.py:85-182` `services/calendar-service/app/sync.py:70-79` `services/agent-api/agent_api/auth.py:1-29` `services/agent-api/agent_api/main.py:243-475` `services/admin-api/app/main.py:1-末尾`
  `services/runtime-api/runtime_api/config.py:1-末尾` `services/mcp/main.py:1-末尾` `services/transcription-service/main.py:1-末尾` `services/tts-service/main.py:1-末尾` `services/voiceprint-service/main.py:1-末尾`
  `services/wake-stt/app/main.py:1-末尾` `services/wake-orchestrator/app/main.py:1-末尾` `deploy/compose/docker-compose.yml:87-164,216-300,412-429` `deploy/lite/entrypoint.sh:1-398` `deploy/lite/supervisord.conf:95-211`
  `deploy/helm/charts/vexa/values.yaml:1-584` `deploy/helm/charts/vexa/templates/secret.yaml:1-30` `deploy/helm/charts/vexa/templates/deployment-{api-gateway,admin-api,meeting-api,mcp}.yaml:1-末尾`
  `deploy/helm/charts/vexa-lite/values.yaml:1-末尾` `deploy/helm/charts/vexa-lite/templates/secret.yaml:1-末尾` `deploy/helm/charts/vexa-lite/templates/deployment.yaml:1-末尾`
  `deploy/helm/charts/vexa-lite/templates/dashboard-deployment.yaml:1-末尾` 新規 `services/api-gateway/tests/test_route_policy.py:1-末尾` 新規 `services/calendar-service/tests/test_scheduler_gateway_assertion.py:1-末尾` 新規
  `services/api-gateway/tests/test_admin_mcp_identity.py:1-末尾` 新規 `services/admin-api/tests/test_trusted_identity.py:1-末尾` 新規 `services/mcp/tests/test_trusted_identity.py:1-末尾` 新規
  `services/api-gateway/tests/test_synthetic_route_registration.py:1-末尾` 新規 `services/api-gateway/tests/test_transcript_share_identity.py:1-末尾` 新規 `packages/security-contracts/identity-envelope-v1.json:1-末尾` 新規
  `scripts/migrations/migrate_transcript_shares.py:1-末尾` 新規 `tests3/unit/refactor/test_vexa_env_modes.py:1-末尾` 新規 `tests3/unit/refactor/test_rf_05a.py:1-末尾`
- 問題: route policyがCalendar/recording/Agent/MCPを網羅せずoptional credentialでupstreamへ到達できる。下流も未署名`X-User-ID`やbody/query主体を信頼し、secret未設定時にopenになる。
- 変更: `RoutePolicy(method,path/prefix,auth_mode,scopes,downstream)`を単一tableへ固定し、exact/長いprefix優先で一意解決する。全HTTP/WS routeが1件だけに分類され、未分類/複数一致はstartup/test failure。 scopeは
  `/user,/calendar=bot`、`/bots=bot|browser`、meeting/transcript/recording/speaker/voiceprint=`tx`、Agent chat/session/workspace/schedule/container=`browser`、`/mcp`はvalid user token + tool-level scope、`/b/{token}`はbrowser-session
  tokenとする。新scopeを作らない。 clientの`x-user-*`/`x-internal-secret`を除去する。Gateway→Admin token検証には専用`GATEWAY_ADMIN_VALIDATE_SECRET`、Gateway→Meeting/Calendar/Agent/Admin/MCP
  identityには下流別`GATEWAY_MEETING_IDENTITY_SECRET`、`GATEWAY_CALENDAR_IDENTITY_SECRET`、`GATEWAY_AGENT_IDENTITY_SECRET`、`GATEWAY_ADMIN_IDENTITY_SECRET`、`GATEWAY_MCP_IDENTITY_SECRET`を使う。MCP→Gateway tool
  dispatchは別の`MCP_GATEWAY_ASSERTION_SECRET`、Meeting→Admin webhook deliveryは`MEETING_ADMIN_WEBHOOK_IDENTITY_SECRET`を使い、全secretの相互非同値をstartupで強制する。既存`INTERNAL_API_SECRET`や別audienceのsecretを使い回さない。
  Adminでtokenを解決後、対象下流へだけ`X-Vexa-Trusted-Identity`のcanonical
  JSONと`X-Vexa-Identity-Signature`を注入する。envelopeは`v=1,sub,scopes,limits,aud,method,route_policy_id,route_template,canonical_path_params,normalized_path,canonical_query_sha256,body_sha256,content_length,iat,exp,jti`を持ち、audは下流service
  exact、TTL 30秒、clock skew 5秒。pathはASGI `raw_path` byteを1回だけpercent decodeし、invalid/truncated percent、NUL/control、invalid UTF-8、backslash、encoded slash/backslash、dot segment、double-encoding候補をroute解決前に400でrejectする。許可byteだけをRFC3986
  uppercase percent-encodingへ戻し、route tableが解決したpolicy ID/templateとtyped path parameterのcanonical JSONも署名する。Gateway・各下流・upstream adapterは同じ`packages/security-contracts/identity-envelope-v1.json` adversarial vectorを使い、downstream
  actual route ID/template/path params/normalized pathとexact照合する。queryはraw queryを左から1回だけparseし、`+`をspaceへ暗黙変換せずRFC3986 percent decodeした**順序付き(name,value) pair列**をその順番のままcanonical percent-encodingする。値sort、同名pair
  sort、dict化をしない。route policyは各query名のcardinalityを`zero-or-one|repeatable`で宣言し、`api_key,token,user_id,meeting_id,session_id,container_id`等のcredential/subject/resource選択paramは全て`zero-or-one`、重複時は値が同じでも400・upstream 0。empty
  name/value、`+`対`%20`、percent文字大小、mixed encoding、repeatable pair順をcross-language vectorで固定する。bodyなしは空byte SHA-256、content length不一致はreject。WSはGET handshake path/queryへbindする。 各下流にimmutable `TrustedIdentity`
  dependencyを作り、自service向けsecretで署名検証した後、actual method/path/query/body digest/lengthとconstant-time照合した場合だけidentityを受理する。state-changing methodは副作用前に`SET identity-jti:<aud>:<jti> 1 NX EX
  <remaining-ttl>`を原子的実行し、重複/100並列replayは409・handler/DB call 0。Gateway retryはattemptごとにnew jtiをmintし、業務idempotency keyとは混ぜない。GET/WSもroute/digest bindは必須。wrong audience/expired/future/duplicate headerを拒否し、legacy body/query
  `user_id`は一致時だけ受理して捨て、不一致403。 `/user/*`の通常Admin proxyと`/mcp`/`/mcp/*`はAdmin token introspection後にraw `Authorization,X-API-Key`を除去し、それぞれ`aud=admin-api|mcp` envelopeだけを送る。Admin/MCPはraw client tokenをrequest context、tool
  state、logへ保持しない。唯一のraw token trusted-boundary例外はGateway→Admin固定`/internal/validate`であり、response/cache/logへ値を残さず通常proxyへ流用しない。 MCP
  toolがGatewayへ戻る経路は`MCP_GATEWAY_ASSERTION_SECRET`で`sub,tool_name,operation,method,route_policy_id,normalized_path,canonical_query_sha256,body_sha256,iat,exp<=30,jti`を署名し、Gatewayの固定internal MCP dispatch tableへだけ送る。unknown
  tool/path、subject/method/path/body/audience mismatch、100並列replayはGateway upstream/DB call 0。MCPへraw user tokenを渡すfallbackや全route catch-allを作らない。 Meetingのwebhook deliveryは`MEETING_ADMIN_WEBHOOK_IDENTITY_SECRET`でRF-03Bのexact
  claimを署名し、Admin brokerだけが受ける。Gateway/Admin/MCP/他serviceへこのsecretを配らず、Admin brokerからMeetingへraw secretを返さない。 Calendar background syncはMeetingへ直結しない。`CALENDAR_SCHEDULER_ASSERTION_SECRET`をCalendar issuer+Gateway
  verifierだけへ配り、固定`POST /internal/calendar/bots`へ`sub=user_id,event_id,operation=bots.create,method,path,body_sha256,iat,exp<=30s,jti`を送る。Gatewayがrequest bind/one-time jtiを検証後に通常のGateway→Meeting
  identityをmintする。Calendarから`BOT_API_TOKEN,X-User-*`とMeeting URL直結を削除し、single-account/per-userの既存bot request goldenを維持する。 public transcript share recordからraw利用者API keyを除去する。Redis
  valueは`share_id_hash,resolved_user_id,creator_token_id,meeting_id,platform,native_id,exp,status`のexact allow-listだけで、`share-by-token:<creator_token_id>` indexを持つ。token DELETE
  transactionはindex内shareを全revokeしてからtokenを無効化する。公開取得routeはshare hash/active/expiryを確認後、`operation=transcript.share.read`のGateway→Meeting trusted identityをmintし、Meetingがrecord subject/meeting ownership一致を確認してtranscriptを返す。creator
  token revoke、share revoke、expiry後はMeeting/DB call 0。recordのmeeting/user改ざんは403、raw API keyをRedis/log/job/responseへ0。 既存raw-token shareはreverse index/DB inventoryを持たないため、架空indexを前提にしない。D1 deploy前に旧share作成経路をfreezeし、一時Redis
  principal `migrate-transcript-shares`をCSPRNG passwordで作る。権限はkey `~share:transcript:* ~share-by-token:*`、commands `SCAN,GET,SET,DEL,PTTL,EXPIRE,SADD,SMEMBERS,WATCH,UNWATCH,MULTI,EXEC`だけ。`KEYS`、broad
  key/category、他namespaceを常時禁止する。 migrationは`SCAN MATCH share:transcript:* COUNT 100`をcursor=0まで実行し、最大100万key/1万cursor/15分を超えたら旧recordを削除せず中断する。最初のkey inventoryは0700 operator temp directoryの0600
  fileへだけ保存し、evidence/stdoutへ出さずfinally削除する。各recordのraw tokenはmemory内だけでAdminへresolveし、owner/token ID/meeting一致ならremaining PTTLを保持してWATCH/MULTIでsanitized record+reverse indexを書いてraw
  fieldを削除する。invalid/ambiguous/revoked/conflictはshare revoke、scan後に消えたkeyは`expired_during_migration`へ分類する。retryはsanitized recordをbyte不変でskipし、second passでlegacy raw field 0を確認する。
  `migrated+revoked+expired_during_migration+already_sanitized=inventory`を値なしで記録した後、一時principal/password/Secret ref/SCAN grantを削除する。この削除とold-value canary rejectが完了するまでOP-05Aをpassさせない。露出した旧tokenはOP-05Aでrotate/revokeする。 runtime
  modeは`VEXA_ENV=production|development|test`の3値を必須にし、unset/空/unknownは全service startup failureにする。Compose/Lite/Helmの通常profileはliteral `production`、test overlayだけliteral `test`を設定する。synthetic
  routeは`VEXA_ENV=test`かつ`ENABLE_SYNTHETIC_RIG=true`かつ32-byte以上の専用`SYNTHETIC_RIG_SECRET`が揃う場合だけ条件登録し、developmentでは登録しない。test時もloopback-only fixture bindingと専用header secretを要求し、missing/wrongは403、Meeting/downstream/DB/state call
  0。production/unset/unknownではGateway `/bots/internal/test/*`, `/bots/internal/callback/*`とMeeting synthetic lifecycle/state callbackのroute inventory自体0または404で、副作用0。 このRF-05A commit自身で通常Compose/Lite/Helm `vexa`/Helm
  `vexa-lite`の全server processへliteral `VEXA_ENV=production`、test overlayだけ`test`を配る。上記Gateway identity、Calendar/Telegram assertion、OAuth registry encryption、Webhook broker、MCP reverse assertionのcurrent/optional previous Secret
  refをissuer/verifier exact pairへ配線し、RF-05Fへ先送りしない。RF-05Fは値のdefault廃止・preflight・rotation hardeningを担当する。render manifestへ`verifier→issuer→canary`順を保存し、OP-05Aの各success counterが実render refへbindしない限り進まない。 `vexa-lite`もD1 secret
  inventoryに含め、全Secret `envFrom`で偶然受け取らせない。各processは明示`secretKeyRef`またはservice別projected fileだけを受け、Dashboard deploymentへAdmin token/identity signing secret 0。この項目では既存securityContextを変更せず、RF-05F/06I3の対象から漏らさない。 Meeting standalone
  `API_KEYS`とAgent direct `API_KEY`はこのcommit中だけ互換で残すが、空ならstartup failure。standaloneはclient `X-User-ID`を信頼しない。RF-05CでAgent direct static authを閉じる。 rolling順は downstream verifierを先にdual-accept配置→Gateway signer配置→signed request
  canary→legacy unsigned identity close。Gatewayを先に配備しない。rollbackはlegacy close前ならGateway signer→downstream verifierの逆順、close後にunsigned trustを復活させずtaskを未完で停止する。
- 判断固定: 優先順位: 検証済みtrusted envelope > 検証済みuser token。identity header単独、欠落secret、unknown audienceは拒否する。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-05A`と`bash scripts/test/run-required-suites.sh RF-05A`がexit
  0。`services/api-gateway/tests/test_route_policy.py::{test_every_route_resolves_exactly_one_policy,test_missing_invalid_and_scope_failure_never_calls_upstream,test_cross_audience_route_body_query_and_method_replay_is_rejected,test_raw_path_canonicalization_rejects_encoded_slash_backslash_dot_double_decode_nul_and_invalid_utf8_before_upstream,test_route_policy_template_and_canonical_path_params_are_cross_language_exact,test_query_canonicalization_preserves_order_plus_empty_and_percent_encoding,test_duplicate_singleton_security_subject_and_resource_params_are_rejected_before_upstream,test_state_change_jti_parallel_replay_has_exactly_one_side_effect}`
  `services/calendar-service/tests/test_scheduler_gateway_assertion.py::{test_single_account_and_per_user_sync_use_fixed_gateway_route,test_wrong_user_event_audience_route_and_replay_never_call_meeting,test_calendar_never_receives_gateway_identity_or_meeting_token_secret}`
  `tests3/unit/refactor/test_rf_05a.py::{test_direct_service_rejects_spoofed_user_and_cross_subject_body,test_identity_secret_and_calendar_assertion_secret_distribution_are_exact,test_missing_production_secret_fails_startup}`
  `services/api-gateway/tests/test_admin_mcp_identity.py::{test_user_admin_proxy_strips_raw_token_and_signs_admin_identity,test_mcp_proxy_strips_authorization_and_x_api_key_and_signs_mcp_identity,test_mcp_reverse_dispatch_is_tool_method_path_body_subject_bound_and_one_time}`
  `services/admin-api/tests/test_trusted_identity.py::test_admin_audience_and_jti_are_verified_before_db`
  `services/mcp/tests/test_trusted_identity.py::{test_mcp_requires_mcp_audience_and_never_receives_raw_token,test_tool_calls_use_reverse_assertion_without_client_token}`
  `tests3/unit/refactor/test_rf_05a.py::{test_admin_mcp_webhook_and_reverse_assertion_secret_distribution_are_exact,test_r1_compose_lite_vexa_and_vexa_lite_render_all_active_identity_refs,test_every_r1_server_has_literal_production_mode_and_only_test_overlay_has_test,test_op05a_paths_are_backed_by_rendered_secrets_not_future_rf05f_wiring}`
  `services/api-gateway/tests/test_synthetic_route_registration.py::{test_production_development_unset_and_unknown_modes_never_register_synthetic_routes,test_test_mode_requires_enable_flag_dedicated_secret_and_loopback_binding,test_wrong_rig_secret_has_zero_downstream_or_state_side_effects,test_test_overlay_serves_existing_synthetic_fixture_contract}`
  `tests3/unit/refactor/test_vexa_env_modes.py::{test_every_server_process_rejects_unset_empty_and_unknown_vexa_env_before_listen,test_production_and_development_never_enable_test_or_docs_routes,test_compose_lite_helm_set_production_and_only_test_overlay_sets_test}`
  `services/api-gateway/tests/test_transcript_share_identity.py::{test_share_record_never_contains_raw_user_api_key,test_temporary_scan_principal_migrates_real_unindexed_legacy_namespace,test_migration_preserves_remaining_ttl_and_reverse_index,test_crash_retry_parallel_and_expiry_classification_are_exact,test_temporary_principal_and_scan_grant_are_removed_before_gate,test_keys_command_and_raw_value_logging_are_never_used,test_public_share_mints_only_share_read_identity_for_record_owner_and_meeting,test_revoked_expired_or_tampered_share_has_zero_meeting_and_database_calls,test_fresh_share_preserves_public_transcript_contract}`
  route inventory全件一意、Calendar=`bot`、recordings=`tx`、Agent=`browser`。 spoof header除去、missing/invalid/scope不足でupstream 0。Meeting identityをCalendar/Agentへreplay、Gateway-Admin secretを下流へ使用、既存internal
  secretをidentityへ使用するfixtureは全て403・副作用0。 直接serviceへ偽`X-User-ID`は403、A token+B body/queryは403かつ副作用0、一致legacy入力は成功。 RF-00Bのstrict xfail
  `services/api-gateway/tests/test_route_inventory_characterization.py::test_every_current_route_is_observed[unclassified-policy]` だけをmarker削除して通常passへ変更し、RF-00B matrix entryは変更しない。
  Admin/Gateway/Meeting/Runtime/Calendar/Agent/MCP/Transcription/TTS/Voiceprint/Wake STT/Wake Orchestratorのparameterized subprocess/import fixtureで`VEXA_ENV` unset/empty/unknownはlisten/background task/provider
  load前にnon-zero、`production|development|test`だけmode parse成功。productionではsynthetic/docs/debug route inventory 0。 production相当envでinternal secret/standalone key欠落はstartup failure。 通常Compose/Lite/Helmは`VEXA_ENV=production`、test
  overlayだけ`VEXA_ENV=test`+enable flag+専用secret。production/development/unset/unknownのsynthetic route count 0。 `V-BACKEND`。 suite=V-BACKEND。
- リスク/戻し方: 未分類routeや旧direct clientが止まる。default allowを追加せずroute/scope不明なら中断。rolloutはdownstream verifier→Gateway signer→legacy close、rollbackはclose前だけ逆順。 失敗時はR0。
- 依存: RF-03B, RF-00B
- コミット: `RF-05A enforce explicit route policy and trusted identities`

### RF-05B Agent公開routeを完成させ外部consumerをGatewayへ移行
- 対象: `services/agent-api/agent_api/main.py:243-510` `services/api-gateway/main.py:1565-1710` `services/dashboard/src/app/api/agent/[...path]/route.ts:1-95` `services/telegram-bot/bot.py:45-46,270-300,452-796` 新規
  `scripts/migrations/migrate_telegram_mappings.py:1-末尾` 新規 `services/telegram-bot/tests/test_linking.py:1-末尾` 新規 `services/api-gateway/tests/test_telegram_linking.py:1-末尾` `packages/vexa-cli/vexa_cli/{client,config,main}.py:1-末尾`
  新規 `packages/vexa-cli/tests/test_public_routes.py:1-末尾` 新規 `services/agent-api/tests/test_public_agent_routes.py:1-末尾` 新規 `services/api-gateway/tests/test_agent_route_inventory.py:1-末尾` 新規
  `services/telegram-bot/tests/test_agent_gateway_routing.py:1-末尾` 新規 `services/telegram-bot/tests/test_gateway_assertion.py:1-末尾` 新規 `services/dashboard/tests/test_agent_gateway_route.test.ts:1-末尾`
- 問題: workspace/schedule/containerはdirect Agent/Runtimeまたは無認証internal routeへ依存し、Dashboard/Telegramは全利用者共通Agent tokenを使う。
- 変更: subject-bound public routeとしてAgent health、workspace save/status/files、workspaces、schedule、container list/get/delete/CDPを追加し、resolved subject所有containerだけを扱う。 Gatewayへ上記exact method/pathを`browser` scopeで列挙し、unknown `/api/*`
  catch-allを作らない。SSEだけstream proxy。 Dashboard BFFをcookie user token→Gatewayに変更し、`AGENT_API_URL/TOKEN`、hard-coded token、存在しない`body.bot_token`注入を削除する。 Telegram全Agent操作をGatewayへ統一し、次項のservice assertionからresolved
  subjectを得る。利用者mapping不明時はAdmin user/tokenを自動作成せず、network前に日本語の連携案内を返す。 Telegramはfull user tokenを`telegram:{tg_id}`へ保存しない。Redisには`telegram_user_id -> resolved_user_id`だけを保存し、`TELEGRAM_GATEWAY_ASSERTION_SECRET`をTelegram
  issuer+Gateway verifierだけへ配る。各requestは`sub=resolved_user_id,tg_id,operation,method,normalized_path,body_sha256,iat,exp<=30s,jti`を署名し、Gatewayがroute bind/one-time jtiを検証した後だけ通常のsubject identityへ変換する。A assertion+B user/path/body
  replayは403・upstream 0。raw user tokenをRedis/log/responseへ0。 新規linkは認証subject専用`POST /user/telegram/link-codes`で256-bit one-time codeを1回だけ返す。GatewayはRedis `telegram-link:<sha256(code)>`へsubject/iat/expを`SET NX EX 600`し、raw
  code/tokenを保存しない。Telegram `/link <code>`はTelegram update由来tg_idとcodeをmethod/path/body/jti-bound
  assertionでGatewayへ送り、GatewayがWATCH/MULTI/EXECでcodeを一度だけconsumeして`telegram-user-map:<tg_id>=resolved_user_id`を作る。別subjectの既存mappingは上書きせず409。通常requestはmapping subjectとassertion subをexact照合し、Telegram permanent principalはmapping
  GETだけ、link/map writeはGatewayだけが所有する。 existing Telegram mappingにはbounded known-user indexが存在しないため、一時principal `migrate-telegram-map`をkey `~telegram:* ~telegram-user-map:*`、commands
  `SCAN,GET,SET,DEL,PTTL,EXPIRE,WATCH,UNWATCH,MULTI,EXEC`だけで作る。旧writer freeze後、`SCAN MATCH telegram:* COUNT 100`をcursor=0まで実行し、最大100万key/1万cursor/15分を超えたら削除せず中断する。raw `user_id:token`はmemory内だけでAdmin resolveしprefix user ID一致時だけnew
  mappingを書いてold keyを削除、不正/revoked/conflictはmapping revokeする。second passでlegacy/raw value 0を確認後、一時principal/password/Secret ref/SCAN grantを削除する。migration/retry/parallel中もnew requestはsanitized mappingだけを使い、raw
  tokenを再保存しない。露出tokenはOP-05Aでrotate/revokeする。 `packages/vexa-cli` defaultを`http://localhost:8056` Gatewayへし、chat/session/workspace/statusをpublic `/api/*`へ向け、`/internal/`を使わない。 旧internal routeはRF-05Cまで残す。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-05B`と`bash scripts/test/run-required-suites.sh RF-05B`がexit
  0。`services/agent-api/tests/test_public_agent_routes.py::{test_subject_owned_workspace_and_container_routes,test_cross_subject_container_is_not_found}`
  `services/api-gateway/tests/test_agent_route_inventory.py::{test_agent_route_method_scope_inventory,test_sse_headers_and_status_preserved}`
  `services/telegram-bot/tests/test_agent_gateway_routing.py::test_agent_calls_use_gateway_and_chat_user_token`
  `services/telegram-bot/tests/test_gateway_assertion.py::{test_redis_mapping_contains_user_id_but_never_raw_token,test_temporary_scan_migration_is_bounded_value_redacted_and_retry_safe,test_every_request_uses_short_lived_method_path_body_bound_assertion,test_cross_user_route_body_and_replay_are_rejected_before_upstream,test_existing_telegram_command_golden_is_preserved}`
  `services/telegram-bot/tests/test_linking.py::{test_unlinked_user_gets_link_instructions_without_admin_or_gateway_business_call,test_one_time_link_creates_id_only_mapping,test_link_code_and_user_token_never_enter_log_or_redis_value}`
  `services/api-gateway/tests/test_telegram_linking.py::{test_link_code_is_subject_bound_one_time_and_mapping_cannot_be_overwritten,test_mapping_and_assertion_subject_must_match_before_upstream,test_temporary_scan_principal_migrates_all_legacy_keys_then_is_revoked_and_is_retry_safe}`
  `packages/vexa-cli/tests/test_public_routes.py::test_all_agent_commands_use_public_gateway_routes` `services/dashboard/tests/test_agent_gateway_route.test.ts::test_agent_bff_uses_cookie_user_token_only` A token+B
  user/containerで403/404、side effect 0。全consumerのhostはGatewayだけ。 `rg -n 'AGENT_API_TOKEN|body\.bot_token|/internal/' services/dashboard/src/app/api/agent services/telegram-bot/bot.py packages/vexa-cli`はfixture以外0。`rg -n
  'create.*user|create.*token|/admin/users|/admin/tokens' services/telegram-bot`はnegative test以外0。 `V-BACKEND`, `V-DASH`, `V-INTEGRATIONS`, `V-CLIENTS`。 suite=V-BACKEND,V-CLIENTS,V-DASH,V-INTEGRATIONS。
- リスク/戻し方: proxy path/method/SSE header差。既存snapshotを先に固定し、rollout Agent→Gateway→consumer、rollback逆順。 失敗時はR0。
- 依存: RF-05A, RF-03A, RF-00C
- コミット: `RF-05B publish subject-bound agent routes before closing internals`

### RF-05C Workload CLIをGateway優先へ互換移行
- 対象: `services/vexa-agent/system/bin/vexa:1-870` `services/vexa-agent/system/README.md:1-末尾` `services/vexa-agent/Dockerfile:1-26` `services/agent-api/agent_api/main.py:1-末尾` `services/agent-api/agent_api/auth.py:1-末尾`
  `services/agent-api/agent_api/config.py:1-末尾` `services/agent-api/agent_api/container_manager.py:1-末尾` `services/agent-api/agent_api/workspace.py:293-323` `services/agent-api/agent_api/chat.py:127-130`
  `services/meeting-api/meeting_api/config.py:1-末尾` `services/meeting-api/meeting_api/meetings.py:790-835` `services/runtime-api/profiles.yaml:48-78` read-only inventory: `git grep -n -E -e
  '/internal/(agent|runtime)|AGENT_API_URL|RUNTIME_API_URL' -- services/meeting-api services/runtime-api`。write対象外production/config callerが1件でもあれば停止してplan reviewへ戻る 新規 `services/vexa-agent/tests/test_vexa_public_routing.sh:1-末尾`
  新規 `services/agent-api/tests/test_internal_auth.py:1-末尾` 新規 `services/agent-api/tests/test_no_direct_static_auth.py:1-末尾` 新規 `services/meeting-api/tests/test_managed_standalone_auth_modes.py:1-末尾`
  `services/runtime-api/tests/test_profiles.py:1-末尾` 新規 `services/agent-api/tests/test_workspace_initialization.py:1-末尾`
- 問題: workload CLIがAgent/Runtime/collectorのinternal routeへ直結し、利用者操作可能containerへglobal internal secretが入り得る。Agent internal authもfail-open。
- 変更: CLI baseを`VEXA_API_URL`（default `http://api-gateway:8000`）へ統一し、既存user-scoped `VEXA_BOT_API_TOKEN`を全requestの`X-API-Key`へ付ける。new configがあるworkloadはGatewayだけを使い、失敗時にinternalへfallbackしない。旧image/sessionだけは従来internal route/static
  credentialをD2 compatibilityとして使い、caller/path別legacy counterを値なしで記録する。 workspace/schedule/container/meeting/recordingはRF-05B/既存Gateway公開routeへ、transcriptはまずowned meetingを取得して公開transcript routeへ送る。`VEXA_AGENT_API`,
  `VEXA_RUNTIME_API`, `VEXA_MEETING_API`, `VEXA_TC`, `INTERNAL_API_SECRET`参照をCLIから削除する。 service-wide Admin/Internal/Runtime secretをcontainerへ渡さない。利用者token provisionが無ければfail closed。 browser-session
  `BOT_CONFIG.internalSecret`を削除する。exit callback用service secretはRuntime service processの`callback_headers`だけに残し、browser container env/configへ入れない。 Agent internal chat/workspace/webhook
  endpointへ共通`require_internal_caller`を付け、secret未設定startup failure、missing/wrong 403。D2では正規旧Meeting/Runtime callerだけservice process secretを付けて一時受理しcounter化する。new consumerはpublic Gateway routeを優先する。 Agent public `/api/*`はRF-05A
  identityを優先し、旧image/session用static direct authをcounter付きcompatibilityとして残す。browser/agent profileのnew generationからglobal internal/runtime/admin tokenを除くが、旧generation rollback Secret refはOP-05D-DRAINまで保持する。 Meeting
  authを`DEPLOYMENT_MODE=managed|standalone`のdiscriminated configへする。managedはRF-05A signed identityを優先し、D2だけ旧managed static keyをstrict counter付きfallbackとして受理する。standaloneは明示mode+32-byte以上の専用standalone keyだけを受理し、Gateway identity
  headerとのdual modeを拒否する。new managed profileはstatic ref 0、旧generation refはOP-05Dまでのcompatibility inventoryへ限定する。 Agent Dockerfileの不存在`features/knowledge-workspace/templates/knowledge/`
  COPYを削除し、削除済み`features/`を復元しない。repo設定がなくlegacy templateも存在しない初回workspaceは空directoryを`git init`し、結果を`source="empty_git_repository", template_applied=false`として返す。template適用成功と表示せず、chat側も同結果を正しく案内する。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-05C`と`bash scripts/test/run-required-suites.sh RF-05C`がexit 0。`bash -n services/vexa-agent/system/bin/vexa` `bash services/vexa-agent/tests/test_vexa_public_routing.sh`
  `services/agent-api/tests/test_internal_auth.py::{test_missing_and_wrong_secret_are_rejected_before_side_effects,test_only_meeting_and_runtime_internal_callers_preserve_response_shape}`
  `services/agent-api/tests/test_no_direct_static_auth.py::{test_new_client_prefers_signed_gateway_identity_without_static_fallback,test_legacy_direct_static_auth_is_counted_compatibility_only}`
  `services/meeting-api/tests/test_managed_standalone_auth_modes.py::{test_new_managed_profile_uses_only_signed_identity,test_legacy_managed_static_key_is_counted_compatibility_only,test_standalone_requires_strong_key_and_rejects_gateway_identity,test_mixed_mode_fails_startup}`
  `services/runtime-api/tests/test_profiles.py::test_agent_and_browser_profiles_have_no_global_service_secrets`
  `services/agent-api/tests/test_workspace_initialization.py::test_first_time_workspace_without_legacy_template_initializes_empty_repo` fake curl全host=Gateway、user tokenのみ、internal/Runtime/collector直結0、token欠落network 0/exit 2。
  Agent internal endpointはmissing/wrong 403・side effect 0、正しい旧callerだけ既存shape+legacy counter。new callerはinternal call 0。 agent/browser container envにInternal/Runtime/Admin global secret 0。 browser-session generated
  specは`BOT_CONFIG.internalSecret` 0、Runtime callback headerだけがservice-side secretを持つ。 `docker build -f services/vexa-agent/Dockerfile -t rf05c-vexa-agent .` と `docker run --rm rf05c-vexa-agent sh -lc 'command -v vexa && vexa
  --help >/dev/null'` がexit 0。 `git grep 'features/knowledge-workspace/templates/knowledge' -- services/vexa-agent services/agent-api`は0件。 `V-BACKEND`, `V-CLIENTS`。 suite=V-BACKEND,V-CLIENTS。
- リスク/戻し方: Gateway/token provision不備でnew CLI停止。new clientにinternal fallbackを足さず、旧generationだけでrollbackする。rollout Gateway/Agent確認→new image→legacy counter観測。 失敗時はR1。
- 依存: RF-05B, RF-05A, OP-05A-DRAIN
- コミット: `RF-05C prefer gateway routes while counting legacy agent paths`

### RF-05D1 Runtime service principalをlegacy互換のまま先行配備
- 対象: `services/runtime-api/runtime_api/config.py:1-末尾` `services/runtime-api/runtime_api/main.py:1-末尾` `services/runtime-api/runtime_api/api.py:1-末尾` `services/runtime-api/runtime_api/scheduler_api.py:1-69`
  `services/runtime-api/runtime_api/backends/__init__.py:1-末尾` `services/runtime-api/runtime_api/backends/docker.py:1-末尾` `services/runtime-api/runtime_api/backends/kubernetes.py:1-末尾` `services/agent-api/agent_api/config.py:1-末尾`
  `services/agent-api/agent_api/container_manager.py:1-末尾` `services/agent-api/agent_api/main.py:1-末尾` `services/api-gateway/main.py:1-末尾` `services/meeting-api/config/profiles.yaml:1-末尾`
  `services/meeting-api/meeting_api/config.py:1-末尾` `services/meeting-api/meeting_api/main.py:1-末尾` `services/meeting-api/meeting_api/meetings.py:1-末尾` read-only既知一致:
  `services/meeting-api/meeting_api/callbacks.py:1-末尾`、`services/meeting-api/meeting_api/container_stop_outbox.py:1-末尾`、`services/meeting-api/meeting_api/recording_finalizer.py:1-末尾`、`services/meeting-api/meeting_api/schemas.py:1-末尾`、`services/meeting-api/meeting_api/sweeps.py:1-末尾`、`services/meeting-api/meeting_api/webhook_url.py:1-末尾`のcomment/docstring一致
  read-only inventory: `git grep -n -E -e 'RUNTIME_API|runtime-api|RuntimeClient' -- services/meeting-api services/agent-api services/api-gateway`。上記write対象またはread-only既知一致以外が1件でもあれば停止してplan reviewへ戻る
  `deploy/compose/docker-compose.yml:140-160,270-280` `deploy/lite/supervisord.conf:120-150,190-200` `deploy/helm/charts/vexa/{values.yaml,templates/deployment-runtime-api.yaml,templates/rbac-runtime-api.yaml}:1-末尾` 新規
  `deploy/helm/charts/vexa/templates/namespace-workloads.yaml:1-末尾` 新規 `deploy/helm/charts/vexa/templates/admission-runtime-workloads.yaml:1-末尾` 新規 `deploy/helm/charts/vexa/templates/networkpolicy-workloads.yaml:1-末尾` 対象外（変更禁止）:
  Helm chartにdeploymentが存在しないAgent/Calendar/Telegram/Wake/Voiceprint/Transcription向けHelm object 新規 `services/runtime-api/tests/test_auth_compatibility.py:1-末尾` 新規 `tests3/unit/refactor/test_rf_05d1.py:1-末尾`
- 問題: Runtime middlewareは`API_KEYS`空なら全APIがopenだが、shared keyを同時に削除すると旧Runtime/旧callerのrolling deployとrollbackが成立しない。
- 変更: `RUNTIME_MEETING_API_TOKEN`, `RUNTIME_AGENT_API_TOKEN`, `RUNTIME_GATEWAY_API_TOKEN`を相互に別値で追加し、constant-time照合後`RuntimePrincipal(name,scopes,allowed_profiles)`をrequest stateへ置く。新tokenのmissing/placeholder/相互同値はproduction
  startup failure。 operator gate前にcreate bodyの危険な自由度を閉じる。profile別Pydantic DTOを`extra="forbid"`とし、callerが指定できるのはprofile selector、trusted subjectにbindしたmeeting/session ID、typed provider/capability
  refだけ。`name,image,command,entrypoint,raw env,mounts,volumes,network,network_mode,devices,privileged,cap_add,hostNetwork,hostPID,hostIPC,serviceAccount,k8s_overrides`は422でbackend/state call 0。server profile
  resolverだけが実値を構築し、Docker backendもroot/docker.sock/containerd/device/host network/privileged/cap-addをdefense-in-depth拒否する。 nameは`profile+owner/session hash+128-bit server random`だけ、Redis reservationはSET NX、backend
  409は既存resourceをstart/reuseせず409にする。Kubernetesはcontrol/data serviceとSecretsを`vexa-control` namespace、generated workloadだけを`vexa-workloads` namespaceへ分離する。Runtime control-plane
  SAのRole/RoleBindingは`vexa-workloads`のPod/exec/log/deleteと必要なNetworkPolicyだけにnamespace-scopeし、cluster-wide Role、`vexa-control` Pod/Secret、他namespaceへのverb 0。generated PodはRoleBinding 0の`vexa-workload`
  SA+`automountServiceAccountToken:false`固定、request/profileから上書き不可。ValidatingAdmissionPolicyまたは同等のcluster admissionは`runtime.managed=true` workloadについて許可image digest/profile label/SA/namespace/non-root
  securityContext/volume/networkを固定し、Runtime SA compromise fixtureでもcontrol serviceへexec/get/list/delete/secret read 0にする。これらをoperator gate後のRF-06I1まで延期しない。 Meeting principalはmeeting/browser-session
  profileのcreate/get/delete/waitとmeeting-owned scheduler、Agent principalはagent profileのcreate/get/delete/touch/exec/archiveとAgent scheduler、Gateway principalはbrowser-sessionのget/touchだけ。new principal
  pathはoperation前にscope/owner/profileを検査し、禁止操作はbackend/Redis call前403/404。 Runtime verifierを先に配備できるよう、このcommitだけ既存`API_KEYS`/legacy
  headerを受理し、`RuntimePrincipal(name="legacy-compat")`へ写像して現行route意味を維持する。legacy使用はcredential値なしのcounterだけを記録する。空`API_KEYS`による認証middleware未装着は廃止し、legacyもnewも設定されないproductionはstartup failure。 verifier配備後、Meeting/Agent/Gateway client
  helperを各自のnew tokenへ切り替える。Compose/Lite/HelmはRuntimeへ3 new tokenとlegacy key、各callerへ自principal tokenと一時legacy rollback refを配る。new clientはnew tokenを優先し、両方あるときlegacyを送らない。 `GET /profiles`のnew principal routeはoperator-onlyとしresolved
  env/secret値を返さない。lifecycle callbackはraw headerを永続化せず、`callback_ref={service:"meeting",operation:"bot_callback"}`だけをjobへ保存する。 component rollout順を`Runtime dual verifier -> Meeting/Agent/Gateway callers -> new-token
  canary`に固定する。legacy rejection/removalはRF-05D2まで行わない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-05D1`と`bash scripts/test/run-required-suites.sh RF-05D1`がexit
  0。`services/runtime-api/tests/test_auth_compatibility.py::{test_new_principals_enforce_operation_profile_matrix,test_legacy_key_is_temporary_compatibility_only,test_new_token_wins_when_both_are_present,test_no_config_never_disables_auth_middleware,test_profiles_redact_resolved_secrets}`
  `services/runtime-api/tests/test_auth_compatibility.py::{test_create_rejects_client_image_command_env_mount_network_name_and_k8s_overrides_before_backend,test_backend_rejects_root_socket_device_host_network_and_privileged_even_for_server_spec}`
  `services/runtime-api/tests/test_auth_compatibility.py::{test_server_generated_name_collision_never_reuses_existing_backend,test_generated_workload_uses_unbound_service_account_with_automount_false_before_runtime_rollout,test_runtime_role_is_namespace_scoped_and_cannot_read_exec_or_delete_control_plane_resources,test_admission_rejects_workload_label_profile_service_account_security_or_volume_override}`
  `tests3/unit/refactor/test_rf_05d1.py::{test_each_caller_prefers_only_own_runtime_principal,test_dual_verifier_is_deployed_before_caller_cutover,test_callback_job_stores_typed_ref_not_headers}` new principal cross-matrix:
  Gateway tokenでexec/delete/scheduler/archive=403、Meeting tokenでexec/agent profile=403、Agent tokenでmeeting profile=403、正規operationだけ成功。 legacy fixtureは現行route成功、counter exact 1、credential/log/response/Redisへの値0。new+legacy
  fixtureはnew path counter 0。 generated containerにRuntime token 0、profile response/Redis job/backend metadataに全token/callback canary 0。 `V-BACKEND`, `V-OPS`。 suite=V-BACKEND,V-OPS。
- リスク/戻し方: Runtimeよりcallerを先に配備すると401になる。必ずdual verifier先行とし、失敗時はこのcompatibility commitのRuntimeへ戻す。認証なし状態へ戻さず、RF-05CのSHAから新worktreeで再実行する。 失敗時はR1。
- 依存: RF-05C, RF-00B, RF-00D
- コミット: `RF-05D1 deploy runtime service principals with legacy compatibility`

### RF-05D1B Workload imageとRuntime profileをrootless sandboxへ固定
- 対象: `services/vexa-bot/Dockerfile:1-149` `services/vexa-bot/core/Dockerfile:1-97` `services/vexa-agent/Dockerfile:1-26` `services/vexa-bot/core/src/docker.ts:1-151` `services/runtime-api/profiles.yaml:1-96`
  `services/meeting-api/config/profiles.yaml:1-53` `deploy/compose/docker-compose.yml:1-末尾` `deploy/lite/{entrypoint.sh,supervisord.conf}:1-末尾` `services/runtime-api/runtime_api/backends/{__init__,docker,kubernetes}.py:1-末尾`
  `deploy/helm/charts/vexa/{values.yaml,templates/configmap-runtime-profiles.yaml,templates/deployment-runtime-api.yaml}:1-末尾` 新規 `services/runtime-api/tests/test_workload_security_profiles.py:1-末尾` 新規
  `tests3/unit/refactor/test_rf_05d1b.py:1-末尾`
- 問題: RF-05D1の認証cutoverとimage/rootless移行はfailure domainもrollback順も異なる。同一commitにすると、401障害とChromium/filesystem障害を独立に切り戻せない。
- 変更: Meeting、Browser、Agent imageへ用途別の固定non-root UID/GIDを作り、build時に必要な所有権だけを付与する。runtimeで`chown`、root shell、sudo、SSH daemonを起動しない。 Coreの全Chromium launchから`--no-sandbox`を削除し、user
  namespace/seccompを有効にしたsandbox起動だけを許す。sandboxが使えないhostではflagを戻さずstartupを失敗させる。 Docker server profileは`User=<profile uid>,CapDrop=ALL,SecurityOpt=no-new-privileges,ReadonlyRootfs=true`、pinned seccomp、private `/tmp,/run,<session
  writable dir>` tmpfsだけ。Agentの`/run`は固定UID所有の0700で、RF-03Dの`/run/vexa-git/<random>`以外へcredential
  fileを作らせず、終了後emptyを検査する。Kubernetesは`runAsNonRoot,runAsUser,runAsGroup,allowPrivilegeEscalation:false,capabilities.drop=["ALL"],seccompProfile.type=RuntimeDefault,readOnlyRootFilesystem:true`固定。request/profile
  overrideをRF-05D1のDTO/backend guardが拒否することを再確認する。 GPU/VAAPI profileだけexact render deviceと固定render groupを許す。`privileged`、root、host namespace、追加capabilityはGPUでも許さない。 rolloutは新image単体smoke→Runtime profile dual deploy→新規session
  canaryの順。既存sessionをin-place変更しない。失敗時は新規session受付を止め、Runtime principalはRF-05D1のまま維持し、image/profileだけ直前digestへ戻す。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-05D1B`と`bash scripts/test/run-required-suites.sh RF-05D1B`がexit
  0。`services/runtime-api/tests/test_workload_security_profiles.py::{test_all_generated_profiles_run_nonroot_with_zero_caps_no_new_privileges_readonly_root_and_runtime_default_seccomp,test_agent_private_run_tmpfs_supports_git_bootstrap_and_is_empty_afterward,test_chromium_launches_with_sandbox_and_no_no_sandbox_flag,test_only_gpu_profile_has_exact_render_device_and_group_exception,test_client_cannot_override_any_security_context}`
  `tests3/unit/refactor/test_rf_05d1b.py::{test_meeting_browser_agent_images_define_distinct_nonroot_users,test_compose_and_helm_profiles_match_runtime_security_contract,test_rollout_keeps_runtime_principal_configuration_unchanged}`
  Meeting/Browser/Agentのlocal imageをbuildして各1 container smokeを実行し、`id -u != 0`、`CapEff/CapPrm/CapBnd=0`、rootfs write=EROFS、allow-listed tmpfsだけwrite成功、SA token 0、Chromium sandbox/meeting join fixture/Agent command fixture成功。
  `V-BACKEND`, `V-CORE`, `V-OPS`。 suite=V-BACKEND,V-CORE,V-OPS。
- リスク/戻し方: Chromium sandbox、writable path、GPU groupのhost差。認証commitと同時rollbackしない。image/profileだけ直前digestへ戻し、失敗branchを保持してRF-05D1 SHAからこの項目を再実行する。 失敗時はR0。
- 依存: RF-05D1, RF-03D
- コミット: `RF-05D1B harden workload images and runtime profiles`

### RF-05C2 Agent internal/directとMeeting managed static互換をdrain後に閉鎖
- 対象: `services/vexa-agent/system/bin/vexa:1-870` `services/agent-api/agent_api/{main,auth,workspace,chat,config,container_manager}.py:1-末尾` `services/api-gateway/main.py:1-末尾`
  `services/meeting-api/meeting_api/{auth,meetings}.py:1-末尾` `services/runtime-api/profiles.yaml:1-末尾` `deploy/compose/docker-compose.yml:1-末尾` `deploy/lite/{entrypoint.sh,supervisord.conf}:1-末尾`
  `deploy/helm/charts/vexa/{values.yaml,templates/secret.yaml,templates/deployment-meeting-api.yaml}:1-末尾` 既存（RF-05Cで追加済み）`services/agent-api/tests/test_no_direct_static_auth.py:1-末尾`
  既存（RF-05Cで追加済み）`services/meeting-api/tests/test_managed_standalone_auth_modes.py:1-末尾` 新規 `tests3/unit/refactor/test_rf_05c2.py:1-末尾`
- 問題: RF-05Cはrolling deploy用にAgent internal/direct static authとMeeting managed static keyを残す。drain後も残せばGateway trusted identityを迂回できる。
- 変更: OP-05D-DRAINのpath別counter、active old workload/client、観測時間、old-value canaryを検証後、Agent internal chat/workspace/webhook legacy route/auth、public direct static auth、Meeting managed static key fallbackと全rollback Secret refを削除する。
  Agent public routeとmanaged MeetingはRF-05A trusted identityだけを受理する。standalone Meetingの専用strong keyは別modeとして維持し、managed/standalone mixed configをstartup failureのままにする。 new workload CLI/Core/profileはGateway user
  tokenだけを持ち、Agent/Runtime/Meeting/internal direct URLまたはglobal Internal/Admin/Runtime credentialをenv/config/mountへ0にする。旧credentialを新compat keyへrenameして残さない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-05C2`と`bash scripts/test/run-required-suites.sh RF-05C2`がexit
  0。`services/agent-api/tests/test_no_direct_static_auth.py::{test_public_routes_accept_only_signed_gateway_identity,test_old_direct_and_internal_credentials_have_zero_side_effects}`
  `services/meeting-api/tests/test_managed_standalone_auth_modes.py::{test_managed_mode_accepts_only_signed_identity_and_has_no_static_ref,test_standalone_keeps_only_its_dedicated_key,test_old_managed_key_has_zero_side_effects}`
  `tests3/unit/refactor/test_rf_05c2.py::{test_workloads_have_no_internal_admin_runtime_or_managed_meeting_secret,test_agent_legacy_routes_auth_and_deploy_refs_are_absent,test_gateway_user_token_is_the_only_workload_cli_credential}`
  `V-BACKEND`, `V-MEETING`, `V-CORE`, `V-OPS`。 suite=V-BACKEND,V-CORE,V-MEETING,V-OPS。
- リスク/戻し方: stale旧image/clientは停止する。fresh cutover再検証が不合格ならD2 compatibility deploymentを維持しD3を配備せず中断する。旧static/internal authを新commitで戻さない。失敗時はR2。
- 依存: RF-05C, RF-05D1, OP-05D-DRAIN
- コミット: `RF-05C2 remove drained agent and managed meeting legacy auth`

### RF-05D2 Runtime shared credentialをdrain後に閉鎖
- 対象: RF-05D1後の `services/runtime-api/runtime_api/{config,main,api,scheduler_api}.py:1-末尾` `services/meeting-api/meeting_api/meetings.py:750-1210` `services/agent-api/agent_api/{main,container_manager}.py:1-末尾`
  `services/api-gateway/main.py:1-末尾` `deploy/{compose,lite,helm}/**:1-末尾` のRuntime credential wiring 新規 `services/runtime-api/tests/test_auth_fail_closed.py:1-末尾` 新規 `tests3/unit/refactor/test_rf_05d2.py:1-末尾`
- 問題: RF-05D1はrolling互換のためshared `API_KEYS`とcaller legacy refを意図的に残しており、侵害callerの権限分離はまだ完了していない。
- 変更: new principal canaryとlegacy counter=0を最低「最大request timeout+scheduler poll interval+5分」のdrain windowで確認した後、Runtimeから`API_KEYS`/legacy header/`legacy-compat` principalを削除する。Meeting/Agent/Gatewayのlegacy
  ref/fallback、Compose/Lite/Helm legacy secret keyも同じcommitで削除する。 healthだけを無認証200とし、全他routeはmissing/wrong/old shared tokenで403、side effect 0。scope/owner/profile matrixとtyped callback refをRF-05D1のまま維持する。
  principal別rotationは`*_PREVIOUS_TOKEN`1世代だけを許す。順序は「Runtimeへnew current+old previous -> 当該callerをnew currentへ切替 -> drain中old counter 0 -> previous refを全profileから削除」。新installとPhase 1完了時は全`*_PREVIOUS_TOKEN`を空/未設定にする。
  rollbackはpreviousが残るdrain中だけ旧callerへ戻せる。previous削除後にshared `API_KEYS`を復活させず、taskを未完で停止してRF-05D1のSHAからRF-05D2をやり直す。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-05D2`と`bash scripts/test/run-required-suites.sh RF-05D2`がexit
  0。`services/runtime-api/tests/test_auth_fail_closed.py::{test_missing_wrong_placeholder_and_legacy_shared_tokens_fail_closed,test_principal_operation_profile_cross_matrix,test_profiles_redact_resolved_secrets,test_current_previous_rotation_accepts_only_declared_principal}`
  `tests3/unit/refactor/test_rf_05d2.py::{test_legacy_api_keys_and_caller_fallbacks_are_absent,test_each_caller_has_only_own_runtime_secret_ref,test_previous_tokens_are_empty_in_final_render,test_callback_job_stores_typed_ref_not_headers}`
  health以外missing/wrong/old shared token 403・backend/Redis side effect 0。正規principalだけ既存response shape。 Compose/Lite/Helm renderで各callerは自principal current refだけ、Runtimeは3 currentを参照、legacy/previous ref 0、生成container token 0。 `rg
  -n 'API_KEYS|legacy-compat|RUNTIME_.*_PREVIOUS_TOKEN' services/runtime-api services/meeting-api services/agent-api services/api-gateway deploy` はnegative/rotation test以外0。 `V-BACKEND`, `V-OPS`。 suite=V-BACKEND,V-OPS。
- リスク/戻し方: 未inventory callerがold shared tokenを使うと停止する。RF-05D1 counterが0でなければ本項目を開始しない。失敗branchを保持し、RF-05D1のSHAから新worktreeで再実行する。 失敗時はR2。
- 依存: RF-05C2, RF-05D1, OP-05D-DRAIN
- コミット: `RF-05D2 remove runtime shared credentials after caller drain`

### RF-05E Scheduler・webhook outbound HTTP policyを統一
- 対象: `services/agent-api/agent_api/main.py:359-425` `services/runtime-api/runtime_api/api.py:1-末尾` `services/runtime-api/runtime_api/lifecycle.py:1-末尾` `services/runtime-api/runtime_api/scheduler_api.py:1-69`
  `services/runtime-api/runtime_api/scheduler.py:204-280` `services/meeting-api/meeting_api/webhook_url.py:1-末尾` `services/meeting-api/meeting_api/webhook_delivery.py:1-末尾`
  `services/meeting-api/meeting_api/webhook_retry_worker.py:1-末尾` `services/admin-api/app/webhook_delivery_broker.py:1-末尾`（RF-03B作成物） `services/dashboard/src/app/api/webhooks/test/route.ts:1-末尾` read-only既知一致:
  `services/dashboard/src/app/api/webhooks/deliveries/route.ts:1-末尾`のdelivery表示route read-only inventory: `git grep -n -E -e 'callback_url|callbackUrl|lifecycle.*callback' -- services/runtime-api`と`git grep -n -E -e
  'test.*webhook|webhook.*test' -- services/admin-api services/dashboard/src/app/api/webhooks`。上記write対象・read-only既知一致・category read-only以外のproduction実装一致が1件でもあれば停止してplan reviewへ戻る 新規
  `packages/security-contracts/outbound-http-policy-v1.json:1-末尾`
- 問題: prefix検査と保存時検査だけではIPv6、非global IP、DNS再解決、redirect、任意header/callbackを防げず、schedulerとwebhookがSSRF/secret転送口になる。
- 変更: 言語間vectorを唯一の判定fixtureとし、Admin/Meeting/Runtimeのservice-local validatorが同じallow/deny結果を返す。 external URLはhttp/https、長さ2048、userinfo/fragmentなし、hostnameあり、port
  1..65535。literal/DNS全A/AAAAを`ipaddress`へ通し、1件でも`is_global=False`、single-label、unresolved、IPv4-mapped IPv6、metadata等ならreject。 保存時、各attempt、durable retry直前に再解決する。transportは検証済みA/AAAAの1つへ直接connectし、TLS SNI/証明書検証とHTTP
  Hostは元のhostnameを維持する。socket peer IPが検証集合外ならrequest body/credential送信前にcloseする。retryは必ず再解決・再検証・再pinし、HTTP client既定poolがhostnameを再解決する経路を使わない。redirectは追わず3xxを成功扱いしない。methodはGET/POST/PUT/PATCH/DELETE、body 256KiB、timeout
  1..30秒、response 1MiB、preview 200文字。 hop-by-hop、credential、cookie、`X-API-Key/X-Internal-Secret`等のclient指定headerとCR/LFを422。logはscheme/host/portだけ。 scheduler requestを `external_http` と `internal_agent_chat` のdiscriminated
  modelへする。永続化するのはmethod/body上限、sanitized headers、destination policy、`credential_ref`だけ。internal URL/secretはdispatch時にserver configから構築し、raw credential/headerはrequest body、Redis job、result、logへ保存しない。legacy kindなしjobはexact internal
  contractだけadapterし、それ以外はexternalとして再検査、不明なら`failed: unsafe_legacy_request`。 lifecycle callbackは設定済みMeeting origin + 固定internal pathだけ。success/failure callback、通常webhook、retry、Admin testも同policy。RF-03BのAdmin webhook
  brokerはcredential refをsubject/versionへ照合した後、各attempt直前に保存済みendpointを再検証・再解決・IP pinし、request-local memoryでだけHMAC/Bearer headerを生成する。Meeting/retry/GatewayへURL/secret/headerを返さず、Dashboardは保存済みrefへのtest要求だけを行う。 application
  pinに加えRF-06I2のhost egress policyでprivate/link-local/metadataをdenyする。HTTP/1.1、HTTP/2のどちらもverified peer IPをevidenceへhost hash付きで残し、proxy/environment variableによる別経路を無効化する。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-05E`と`bash scripts/test/run-required-suites.sh RF-05E`がexit
  0。`tests3/unit/refactor/test_rf_05e.py::{test_all_services_match_outbound_policy_vectors,test_reject_has_zero_transport_calls,test_connection_is_pinned_to_verified_ip_with_original_host_and_sni,test_public_validation_then_private_connect_race_sends_zero_body_or_credentials,test_dns_rebinding_and_redirect_to_private_are_terminal,test_redis_jobs_logs_and_results_have_no_raw_credential}`
  `tests3/unit/refactor/test_rf_05e.py::test_admin_webhook_broker_uses_same_dns_pin_redirect_header_and_size_policy`
  `services/admin-api/tests/test_webhook_delivery_broker.py::{test_dispatch_resolves_versioned_ref_just_in_time_and_pins_verified_peer,test_retry_revalidates_dns_and_never_returns_url_secret_or_header}`
  `services/dashboard/tests/test_user_scoped_webhook_routes.test.ts::test_webhook_test_uses_stored_endpoint_without_client_url`
  vectorはIPv4/IPv6/mapped/link-local/metadata/CGNAT/single-label/unresolved/credential/redirect-to-privateを含み全service同結果。 reject時transport call 0、redirect Location call 0、retryでpublic→privateへ変化したfixture送信0/terminal failure。
  internal Agent chatはserver設定だけ、external request/log/Redis job/resultへservice token 0。 mock resolver/transportだけを使い実Internet/localhost接続0。 `V-BACKEND`, `V-DASH`, `V-INTEGRATIONS`。 suite=V-BACKEND,V-DASH,V-INTEGRATIONS。
- リスク/戻し方: private/redirect/custom auth webhookとunsafe legacy jobが止まる。allow-listを広げて救済せず、host/method/件数だけ報告してpublic non-redirect endpointへ移行。旧prefix/follow_redirectsへ戻さない。 失敗時はR0。
- 依存: RF-03B, RF-05D2, RF-00B, RF-00C
- コミット: `RF-05E enforce one outbound HTTP security contract`

### RF-05F Default/empty secretとdirect-login fallbackを全profileで廃止
- 対象: `deploy/compose/docker-compose.yml:20-160,270-300,440-500,690-710` `deploy/compose/{Makefile,README.md}:1-末尾` read-only inventory: `git grep -n -E -e 'changeme|lite-|vexa-|DIRECT_LOGIN|JWT_SECRET' --
  deploy/compose`。期待pathは上記3 fileだけで、別pathが1件でもあれば停止してplan reviewへ戻る `deploy/lite/entrypoint.sh:1-末尾` `deploy/lite/supervisord.conf:1-末尾` `deploy/lite/Makefile:1-末尾` `deploy/helm/charts/vexa/values.yaml:1-末尾`
  `deploy/helm/charts/vexa/templates/_helpers.tpl:1-末尾` `deploy/helm/charts/vexa/templates/secret.yaml:1-末尾` `deploy/helm/charts/vexa/templates/deployment-admin-api.yaml:1-末尾`
  `deploy/helm/charts/vexa/templates/deployment-api-gateway.yaml:1-末尾` `deploy/helm/charts/vexa/templates/deployment-dashboard.yaml:1-末尾` `deploy/helm/charts/vexa/templates/deployment-mcp.yaml:1-末尾`
  `deploy/helm/charts/vexa/templates/deployment-meeting-api.yaml:1-末尾` `deploy/helm/charts/vexa/templates/deployment-minio.yaml:1-末尾` `deploy/helm/charts/vexa/templates/deployment-pgbouncer.yaml:1-末尾`
  `deploy/helm/charts/vexa/templates/deployment-redis.yaml:1-末尾` `deploy/helm/charts/vexa/templates/deployment-runtime-api.yaml:1-末尾` `deploy/helm/charts/vexa/templates/deployment-tts-service.yaml:1-末尾`
  `deploy/helm/charts/vexa/templates/job-migrations.yaml:1-末尾` `deploy/helm/charts/vexa/templates/job-minio-init.yaml:1-末尾` `deploy/helm/charts/vexa/templates/statefulset-postgres.yaml:1-末尾`
  `deploy/helm/charts/vexa-lite/values.yaml:1-末尾` `deploy/helm/charts/vexa-lite/templates/secret.yaml:1-末尾` `deploy/helm/charts/vexa-lite/templates/deployment.yaml:1-末尾`
  `deploy/helm/charts/vexa-lite/templates/dashboard-deployment.yaml:1-末尾` `scripts/bot-debug.sh:1-52` 新規 `tests3/unit/refactor/test_bot_debug_secret_transport.py:1-末尾`
  `services/dashboard/src/app/api/auth/[...nextauth]/route.ts:1-末尾` `services/dashboard/src/app/api/auth/send-magic-link/route.ts:1-末尾` `services/dashboard/src/app/api/auth/verify/route.ts:1-末尾`
  `services/dashboard/src/lib/direct-login.ts:1-末尾` `services/dashboard/src/app/api/health/route.ts:1-末尾` read-only既知一致: `services/dashboard/src/app/api/admin/[...path]/route.ts:1-末尾`
  `services/dashboard/src/app/api/auth/admin-verify/route.ts:1-末尾` `services/dashboard/src/app/api/auth/me/route.ts:1-末尾` `services/dashboard/src/app/api/auth/oauth-callback/route.ts:1-末尾`
  `services/dashboard/src/app/api/auth/shared-login/route.ts:1-末尾` `services/dashboard/src/app/api/calendar/oauth/start/route.ts:1-末尾` `services/dashboard/src/app/api/calendar/oauth/complete/route.ts:1-末尾`
  `services/dashboard/src/app/api/config/route.ts:1-末尾` `services/dashboard/src/app/api/zoom/oauth/start/route.ts:1-末尾` `services/dashboard/src/app/api/zoom/oauth/complete/route.ts:1-末尾`
  `services/dashboard/src/lib/auth-utils.ts:1-末尾` `services/dashboard/src/lib/dashboard-copy.ts:1-末尾` `services/dashboard/src/lib/email.ts:1-末尾` `services/dashboard/src/lib/zoom-oauth-client.ts:1-末尾` read-only inventory: `git grep
  -n -E -e 'magic.?link|direct.?login|NEXTAUTH|OAuth|verify' -- services/dashboard/src/app/api services/dashboard/src/lib`。上記write対象またはread-only既知一致以外の認証実装が1件でもあれば停止してplan reviewへ戻る
  `services/meeting-api/meeting_api/storage.py:100-125` `services/admin-api/app/main.py:1-末尾` `services/api-gateway/main.py:1-末尾` `services/meeting-api/config/profiles.yaml:1-末尾` `services/meeting-api/meeting_api/config.py:1-末尾`
  `services/meeting-api/meeting_api/main.py:1-末尾` `services/meeting-api/meeting_api/meetings.py:1-末尾` `services/runtime-api/profiles.yaml:1-末尾` `services/runtime-api/runtime_api/config.py:1-末尾`
  `services/runtime-api/runtime_api/main.py:1-末尾` `services/voiceprint-service/main.py:1-末尾` read-only既知一致: `services/meeting-api/meeting_api/auth.py:1-末尾` `services/meeting-api/meeting_api/callbacks.py:1-末尾`
  `services/meeting-api/meeting_api/collector/auth.py:1-末尾` `services/meeting-api/meeting_api/collector/config.py:1-末尾` `services/meeting-api/meeting_api/collector/processors.py:1-末尾`
  `services/meeting-api/meeting_api/dispatch_check.py:1-末尾` `services/meeting-api/meeting_api/drive_export.py:1-末尾` `services/meeting-api/meeting_api/final_transcription.py:1-末尾`
  `services/meeting-api/meeting_api/post_meeting.py:1-末尾` `services/meeting-api/meeting_api/redaction.py:1-末尾` `services/meeting-api/meeting_api/voiceprint_matching.py:1-末尾`
  `services/runtime-api/runtime_api/backends/kubernetes.py:1-末尾` read-only inventory: `git grep -n -E -e 'SECRET|API_KEY|TOKEN' -- services/admin-api services/api-gateway services/meeting-api services/runtime-api
  services/voiceprint-service`。上記write対象・read-only既知一致・category read-only以外のproduction/config一致が1件でもあれば停止してplan reviewへ戻る 新規 `scripts/deploy/secret_preflight.py:1-末尾`
- 問題: `postgres/changeme/vexa-*/lite-*`等の既知default、Admin keyへのJWT fallback、direct login default有効により設定漏れが安全側に倒れない。
- 変更: production preflightでAdmin、legacy Internal、`AGENT_RUNTIME_CONFIG_SECRET`、`GATEWAY_ADMIN_VALIDATE_SECRET`、6種のGateway identity secret（Meeting/Calendar/Agent/Admin/MCP/Workload
  Access）、`MCP_GATEWAY_ASSERTION_SECRET`、`MEETING_ADMIN_WEBHOOK_IDENTITY_SECRET`、`OAUTH_STATE_ENCRYPTION_SECRET`、`CALENDAR_SCHEDULER_ASSERTION_SECRET`、`TELEGRAM_GATEWAY_ASSERTION_SECRET`、`TRANSCRIPTION_SERVICE_TOKEN`、`VOICEPRINT_SERVICE_TOKEN`、`WAKE_ORCHESTRATOR_WS_SERVICE_TOKEN`、3種のRuntime
  principal
  token、`MEETING_TOKEN_SIGNING_SECRET`、`BOT_CALLBACK_CAPABILITY_HASH_SECRET`、`BROWSER_STORAGE_CAPABILITY_SIGNING_SECRET`、`BOT_EVENT_CAPABILITY_SECRET`、`BOT_TRANSCRIPTION_CAPABILITY_SECRET`、`BOT_WAKE_CAPABILITY_SECRET`、`BOT_TTS_CAPABILITY_SECRET`、`RECORDING_UPLOAD_CAPABILITY_SECRET`、`BOT_PROXY_CAPABILITY_SECRET`、7種の`CAPABILITY_INTROSPECTION_{EVENT,TRANSCRIPTION,WAKE,TTS,PROXY,ACCESS_MEETING,ACCESS_AGENT}_TOKEN`、`WORKLOAD_ACCESS_REGISTRATION_SECRET`、`RUNTIME_MEETING_ACCESS_STATE_SECRET`、`RUNTIME_AGENT_ACCESS_STATE_SECRET`、`RUNTIME_NETWORK_POLICY_MAC_SECRET`、JWT/NextAuthは32
  byte以上かつ全組合せ相互非同値、DB/MinIO/Redis credentialは16 byte以上かつusername非同値を要求し、既知placeholder集合をcase-insensitive拒否する。`SYNTHETIC_RIG_SECRET`はtest overlayかつenable flag=trueの場合だけ32 byte以上・他secretと非同値を必須にし、production/developmentでは未設定かつSecret
  ref 0を要求する。errorは変数名/理由だけ。 signing/static service secret配布先をexact matrixへ固定する。`GATEWAY_{MEETING,CALENDAR,AGENT,ADMIN,MCP,WORKLOAD_ACCESS}_IDENTITY_SECRET`はGateway issuer+名前どおりの唯一verifierだけ。`MCP_GATEWAY_ASSERTION_SECRET`はMCP
  issuer+Gateway verifier、`MEETING_ADMIN_WEBHOOK_IDENTITY_SECRET`はMeeting issuer+Admin webhook broker verifier、`OAUTH_STATE_ENCRYPTION_SECRET`はGateway processだけ。`CALENDAR_SCHEDULER_ASSERTION_SECRET`はCalendar issuer+Gateway
  verifier、`TELEGRAM_GATEWAY_ASSERTION_SECRET`はTelegram issuer+Gateway verifierだけ。`TRANSCRIPTION_SERVICE_TOKEN`はMeeting deferred caller+Transcription verifier、`VOICEPRINT_SERVICE_TOKEN`はMeeting voiceprint client+Voiceprint
  verifier、`WAKE_ORCHESTRATOR_WS_SERVICE_TOKEN`はWake Orchestrator client+Wake STT verifierだけ。Voiceprintの`ALLOW_UNAUTHENTICATED`はproductionで未設定/falseだけを許し、trueはpreflight failure。`SYNTHETIC_RIG_SECRET`はtest
  overlayのGateway/Meetingだけで通常profileはref
  0。`BOT_CALLBACK_CAPABILITY_HASH_SECRET`,`BROWSER_STORAGE_CAPABILITY_SIGNING_SECRET`,`BOT_EVENT_CAPABILITY_SECRET`,`RECORDING_UPLOAD_CAPABILITY_SECRET`はMeetingだけ、`BOT_TRANSCRIPTION_CAPABILITY_SECRET`はMeeting issuer+Transcription
  verifier、`BOT_WAKE_CAPABILITY_SECRET`はMeeting+Wake、`BOT_TTS_CAPABILITY_SECRET`はMeeting+TTS、`BOT_PROXY_CAPABILITY_SECRET`はMeeting issuer+非権限`workload-broker` verifier、`MEETING_TOKEN_SIGNING_SECRET`はMeeting API/collector
  processだけ。`RUNTIME_MEETING_ACCESS_STATE_SECRET`はRuntime issuer+Meeting verifier、`RUNTIME_AGENT_ACCESS_STATE_SECRET`はRuntime issuer+Agent verifier、`RUNTIME_NETWORK_POLICY_MAC_SECRET`はRuntime+host network-policy
  agentだけ。生成workload、許可以外のserviceへは0。signing secretではなく署名済み短命tokenまたはRF-06I2のper-session Ed25519 private keyだけを生成workloadへ渡す。 introspection auth tokenは`EVENT=Meeting内loopback
  brokerのみ`、`TRANSCRIPTION=Meeting+Transcription`、`WAKE=Meeting+Wake`、`TTS=Meeting+TTS`、`PROXY=Meeting+workload-broker`、`ACCESS_MEETING=Meeting+workload-access-broker`、`ACCESS_AGENT=Agent+workload-access-broker`のexact
  pairだけへ配る。`WORKLOAD_ACCESS_REGISTRATION_SECRET`はRuntime issuer+workload-access-broker verifierだけ。Recording/Browser storage/CallbackはMeeting process内でregistryを直接検査する。introspection/registration/state/network-policy
  secretは生成workload、Gateway、Admin、Redis/log/job、相互brokerへ0。 workload access registration mTLSはregistry entryを`planned|active`で管理する。RF-05Fの`planned`時点はCA/server/client certificate値、期限、Secret refを要求せず、schema、SAN、TLS
  1.3、配布先ruleだけを検査し、全profile render ref 0を必須にする。RF-06I2が同じentryを`active`へ変えるcommitから、production既存Secret、CA certificate=Runtime+workload-access-broker、server certificate/private key=brokerだけ、Runtime client certificate/private
  key=Runtimeだけを必須にする。server SAN exact `workload-access-broker`、client SAN exact `runtime-api`、相互verify、期限がrelease window+30日未満ならpreflight failure。plaintext listener、`verify=false`、self-signed自動生成、private
  keyの生成workload/Gateway/Meeting配布は0。Compose/Lite test fixtureだけtracked test CA/certを使い、production値を生成しない。 RF-05F時点では未作成のTranscription/Wake/TTS capability consumer、workload-broker、workload-access-brokerへSecret
  refを先行配布しない。preflight registryへ型/長さ/相互非同値/distribution ruleだけを`planned`登録し、未導入consumerの値/cert期限検査なし・render ref 0をRF-05F完了条件にする。各consumerを作るRF-06D1/RF-06H/RF-06I2が自身のentryだけを`planned ->
  active`にし、そのcommit以後は値/certificate/expiry/exact pairを必須にする。Phase 1 gateで全entry active、全active値条件pass、許可以外ref 0、previous ref 0をまとめて検証する。 RF-05AのGateway identity/Calendar assertion、RF-05GのMeetingToken、RF-05H/06A〜06Hのworkload
  capability signing keyだけをcurrent keyとoptional previous keyの2-key ringに統一する。RF-04Bの`v1.<payload>.<sig>` admin cookie、NextAuth/OAuth wireはこの変更対象外。対象keyは既存secret名をcurrent値として保ち、同prefixの`_KID`を必須、optional
  previousは`_PREVIOUS_SECRET`+`_PREVIOUS_KID`の両方が揃う場合だけ許す。`kid`はASCII `[A-Za-z0-9._-]{1,64}`、current/previous ID・値は相互非同値、algorithmはHS256固定。issuerはcurrentだけ、verifierはheader/claimの`kid`でexact keyを選び、unknown kidをrejectして全鍵を総当たりしない。
  RF-05Aで既に発行中のno-`kid` identity/assertionをrolling中に即時401へしないため、RF-05Fのverifierだけは「headerに`kid` field自体が存在しない・HS256・strict audience/route/body/TTL claim・署名がcurrent secret
  exact」の場合に限り`legacy_no_kid`として一時acceptし、credential値なしのaudience/issuer別counterを記録する。`kid=null|""`、unknown kid、previous keyによるno-kid、claim緩和はrejectする。全issuerをcurrent
  `kid`付与へ切替え、OP-06C-DRAINが最大30秒TTL+5秒skew+最大retry+300秒以上でaudience別no-kid count 0を証明するまでcompat branchを消さない。RF-05F2がbranch/refを削除し、Phase 1最終renderはprevious ref/no-kid accept 0を要求する。callbackのserver-side hash recordにも`key_id`を保存する。
  Composeは`${VAR:?required}`。`make bootstrap-secrets`は48-byte randomをgitignored `.env`へtemp→0600→atomic renameし、既存file非上書き/値非表示。direct login default false。 Liteは外部指定のないservice
  secretだけを`/data/vexa/secrets.env`へ初回生成し0600/再利用。DB/MinIOは明示入力。credential URLをlogしない。 Helmは`existingSecretName`またはrequired values。template default生成を禁止し、Deploymentは固定key refだけを使う。 Helm `vexa-lite`も全Secret
  `envFrom`を削除し固定`secretKeyRef`/projected fileだけを使う。DashboardからAdmin tokenを除去する。Dashboard
  containerは`runAsNonRoot=true,allowPrivilegeEscalation=false,capabilities.drop=[ALL],seccompProfile.type=RuntimeDefault,readOnlyRootFilesystem=true`。main Lite
  containerだけはRF-06I3の短命UID分離bootstrapのため`runAsUser=0,allowPrivilegeEscalation=false,capabilities.drop=[ALL],capabilities.add=[SETUID,SETGID,KILL],seccompProfile.type=RuntimeDefault,readOnlyRootFilesystem=true`をexact例外とし、他capability/root
  shell/package manager/network bootstrapを禁止する。RF-06I3でreadiness前のpermanent UID/cap
  dropを証明できなければ起動失敗とする。`deploymentMode=single-tenant-development`、replica=1、autoscaling/ingress/service-account-token/hostPID/hostNetwork/hostPath/Docker socketなしをrender時に強制し、managed/production値はfailする。RF-06I3がchild UID/process
  isolationを完成させるまで「managed production対応」と表示しない。 `scripts/bot-debug.sh`はpredictable `/tmp/.${PROJ}_token`と`BOT_DEBUG_TOKEN` env cacheを削除し、`mktemp -d "${TMPDIR:-/tmp}/vexa-bot-debug.XXXXXXXX"`→mode0700→success/failure/SIGINT共通trap
  cleanupを使う。必要な一時fileは0600。Admin/user tokenはargv、env、persistent fileへ置かず、stdin-backed `curl --config /dev/fd/<fd>`のheaderとしてだけ渡す。`set -x`を明示解除し、curl failure body/stdout/stderr、`/proc/*/cmdline`、tmpへcanary 0。
  JWT/NextAuth/OAuthからAdmin key/固定fallbackを削除。欠落時503でsign/verify 0。direct loginは`VEXA_ENV=development`かつliteral `true`だけ、production trueはpreflight failure。 MinIO/S3 backend時だけstorage credentialを必須化。local/gcsには要求しない。 secret
  rotationは各release OP gateの必須sub-stepとする。new path成功→legacy admission/deployment generation freeze→intended service pairだけへnew値発行→old値revoke→old canary reject→旧Secret ref/active session
  0の順で、旧値をrepo/evidenceへ保存しない。storage/provider/proxy/Zoomを含め、codeから参照を消しただけで旧credentialを有効なまま残さない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-05F`と`bash scripts/test/run-required-suites.sh RF-05F`がexit
  0。`tests3/unit/test_secret_preflight.py::{test_all_profiles_reject_empty_placeholder_and_equal_secrets,test_rendered_profiles_contain_no_literal_default_secret,test_direct_login_is_disabled_without_explicit_test_flag,test_secret_preflight_reports_names_not_values}`
  `tests3/unit/refactor/test_rf_05f.py::{test_storage_credentials_are_required_only_for_selected_backend,test_capability_signing_secret_distribution_matches_exact_service_matrix,test_generated_workloads_receive_tokens_but_never_signing_secrets,test_lite_secret_file_is_reused_with_mode_0600_and_no_log_leak}`
  `tests3/unit/refactor/test_rf_05f.py::{test_vexa_lite_uses_explicit_secret_refs_and_dashboard_has_no_admin_token,test_vexa_lite_has_nonempty_security_context_and_rejects_managed_production,test_admin_mcp_oauth_webhook_secret_distribution_matches_exact_matrix}`
  `tests3/unit/refactor/test_bot_debug_secret_transport.py::{test_uses_private_mktemp_and_cleans_on_success_failure_and_signal,test_admin_and_user_tokens_exist_only_in_curl_config_fd_not_argv_env_or_log,test_predictable_tmp_cache_and_bot_debug_token_env_are_absent}`
  `tests3/unit/refactor/test_rf_05f.py::{test_calendar_scheduler_secret_exists_only_in_calendar_and_gateway,test_voiceprint_token_exists_only_in_meeting_and_voiceprint_and_production_never_allows_unauthenticated,test_key_ring_uses_exact_kid_and_hs256_without_key_scanning,test_no_kid_compat_accepts_only_exact_current_strict_token_and_counts_by_audience,test_previous_key_verifies_only_during_declared_drain_window,test_phase_one_final_render_has_zero_previous_key_refs_and_no_kid_accept,test_callback_hash_record_contains_key_id}`
  secretなしCompose config non-zero、valid fixture preflight/Helm render exit 0。 empty/placeholder/同値/production direct-login=trueは失敗。Lite再起動2回でsecret再利用・mode0600・log canary 0。 deny-list/test fixture以外に既知default文字列0、実secretのcommand
  line/git/evidence出力0。 `V-BACKEND`, `V-DASH`, `V-OPS`。 suite=V-BACKEND,V-DASH,V-OPS。
- リスク/戻し方: default依存環境が起動しなくなる。先にbootstrap/preflightを配布し値を用意してからrolloutし、rollbackでdefaultを復活せず不足Secretを補う。 失敗時はR0。
- 依存: RF-05A, RF-05D2, RF-05E, RF-03A, RF-00C, RF-00D
- コミット: `RF-05F remove insecure deployment credential defaults`

### RF-05G MeetingToken署名鍵をAdmin全権keyから分離
- 対象: `services/meeting-api/meeting_api/meetings.py:79-116` `services/meeting-api/meeting_api/collector/processors.py:25-65` `services/meeting-api/README.md:66,90-115` `deploy/compose/docker-compose.yml:90-140,260-285`
  `deploy/lite/supervisord.conf:100-150` `deploy/helm/charts/vexa/{values.yaml,templates/secret.yaml,templates/deployment-meeting-api.yaml}:1-末尾` 新規 `services/meeting-api/tests/test_meeting_token_signing.py:1-末尾`
- 問題: MeetingTokenをAdmin API全権keyで署名・検証し、Meeting serviceへAdmin keyを配る。Meeting侵害がAdmin全権侵害へ拡大する。
- 変更: `MEETING_TOKEN_SIGNING_SECRET`,`MEETING_TOKEN_SIGNING_KID`と任意の`MEETING_TOKEN_SIGNING_PREVIOUS_SECRET`,`MEETING_TOKEN_SIGNING_PREVIOUS_KID`を導入する。current/previousは32
  byte以上、ID/値は相互非同値、Admin/Internal/JWT/Runtime/Gateway各secretとも非同値。未設定/同値はproduction startup failure。new issuerはHS256 header `kid=current`を必須で付け、verifierはknown `kid`で1鍵だけを選ぶ。
  claimとalgorithmは既存の`HS256`、`iss=meeting-api`、`aud=transcription-collector`、`scope=transcribe:write`、`meeting_id,user_id,platform,native_meeting_id,iat,exp,jti`を維持する。new verifierをcollectorへ先行配備し、known `kid`
  tokenに加えて、RF-05G配備前にAdmin keyで発行済みのno-kid tokenだけをstrict claim/audience/最大2時間TTLで`legacy_admin_signed`として一時acceptする。legacy branchは専用Admin verification key refをcollector verifierだけにread-only配布し、Meeting
  issuerは一切読まず、credential値なしcounterを記録する。`kid`付きAdmin署名、missing claim、TTL超過、unknown kidはrejectする。 Compose/Lite/HelmはMeeting API/collector processへnew signing key ringを配り、collectorだけへ一時legacy Admin verification refを配る。Meeting
  issuerから`ADMIN_TOKEN`/`ADMIN_API_TOKEN`を削除し、new issuerへ切替える。Admin API tokenはAdmin API、RF-04Bのadmin BFF、上記read-only legacy verifier以外へ配らない。 OP-06C-DRAINは`max_legacy_meeting_token_ttl_seconds=7200`、clock skew/retry/active old
  collector session lifetimeを含む観測期間、`legacy_admin_signed_count=0`,`active_old_collector_sessions=0`を証明する。RF-05G2がlegacy verifier/Admin refを削除するまでAdmin-signed tokenを即時rejectしない。new key rotationはknown previous/current
  verifier先行→issuer current切替→最大TTL+clock skew+retry経過→previous ref除去。値はlog/evidence/command lineへ出さない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-05G`と`bash scripts/test/run-required-suites.sh RF-05G`がexit
  0。`services/meeting-api/tests/test_meeting_token_signing.py::{test_current_and_previous_kid_select_exact_signing_secret,test_legacy_no_kid_admin_token_is_strictly_accepted_and_counted_only_during_compatibility,test_kid_admin_unknown_missing_claim_and_overlong_legacy_tokens_are_rejected,test_claims_and_wire_contract_are_unchanged,test_missing_equal_or_placeholder_secret_fails_closed}`
  Compose/Lite/Helm render testでMeeting issuer envにAdmin token 0、Admin envにsigning secret 0、collector/Meetingだけnew signing ref、collectorだけtemporary legacy verification refあり。 new current/previous tokenは既存collector pathで成功。strict
  legacy canaryだけcompat counter exact 1、他Admin署名fixtureはcollector write 0。 `rg -n 'ADMIN_TOKEN|ADMIN_API_TOKEN' services/meeting-api/meeting_api/meetings.py services/meeting-api/meeting_api/collector
  deploy/helm/charts/vexa/templates/deployment-meeting-api.yaml` はnegative fixture以外0件。 `V-MEETING`, `V-BACKEND`, `V-OPS`。 suite=V-BACKEND,V-MEETING,V-OPS。
- リスク/戻し方: deploy順違いでcollectorが新tokenを拒否する。collector compat verifier→new issuerの順に配布し、legacy verifierはOP-06C-DRAIN前に削除しない。失敗branchを保持しRF-05FのSHAから再実行する。 失敗時はR0。
- 依存: RF-05F
- コミット: `RF-05G separate meeting token signing from admin authority`

### RF-05H Meeting Botへglobal internal secretを渡さずsession capabilityへ置換
- 対象: `services/meeting-api/meeting_api/callbacks.py:1-末尾` `services/meeting-api/meeting_api/collector/auth.py:1-末尾` `services/meeting-api/meeting_api/collector/endpoints.py:1-末尾` `services/meeting-api/meeting_api/config.py:1-末尾`
  `services/meeting-api/meeting_api/container_stop_outbox.py:1-末尾` `services/meeting-api/meeting_api/main.py:1-末尾` `services/meeting-api/meeting_api/meetings.py:750,819-828,1104-1147,1210`
  `services/meeting-api/meeting_api/post_meeting.py:1-末尾` `services/meeting-api/meeting_api/recording_finalizer.py:1-末尾` `services/meeting-api/meeting_api/recordings.py:1-末尾` `services/meeting-api/meeting_api/schemas.py:1-末尾`
  `services/meeting-api/meeting_api/sweeps.py:1-末尾` `services/meeting-api/meeting_api/webhooks.py:1-末尾` `services/vexa-bot/core/src/services/unified-callback.ts:177-178` `services/vexa-bot/core/src/docker.ts:98`
  `services/runtime-api/profiles.yaml:77` `deploy/compose/docker-compose.yml:1-末尾` `deploy/lite/supervisord.conf:1-末尾` `deploy/helm/charts/vexa/templates/deployment-admin-api.yaml:1-末尾`
  `deploy/helm/charts/vexa/templates/deployment-api-gateway.yaml:1-末尾` `deploy/helm/charts/vexa/templates/deployment-meeting-api.yaml:1-末尾` `deploy/helm/charts/vexa/templates/secret.yaml:1-末尾` read-only inventory: `git grep -n -E
  -e 'callback|INTERNAL_API_SECRET' -- services/meeting-api/meeting_api`と`git grep -n -E -e 'INTERNAL_API_SECRET|BOT_CONFIG' -- deploy/compose deploy/lite deploy/helm
  services/runtime-api/profiles.yaml`。期待pathは上記20既存fileだけで、別pathが1件でもあれば停止してplan reviewへ戻る 新規 `services/meeting-api/tests/test_callback_capability.py:1-末尾` 新規 `services/vexa-bot/core/src/meeting-proxy-options.test.ts:1-末尾`
- 問題: Meeting Bot config/envへglobal `INTERNAL_API_SECRET`を渡すため、1 container侵害で全service internal surfaceのcredentialが漏れる。
- 変更: Bot起動ごとに32-byte random callback capabilityを作る。Meeting serviceだけが持つ`BOT_CALLBACK_CAPABILITY_HASH_SECRET`でcanonical length-prefixed
  `v=1,aud="bot-callback",meeting_id,user_id,session_uid,bot_instance_id,operations={"callback"},iat,exp,raw_token`全体をHMAC-SHA256し、Redisへ `bot-callback-capability:<meeting_id>:<bot_instance_id>`
  のdigest/audience/subject/operation/expだけを保存する。`exp=min(resolved_bot_deadline+3600,iat+28800)`、clock skew 5秒とし、deadline欠落/過去/8時間超過は上限へ丸めず発行前422。plain SHA-256やtokenだけのHMACを禁止し、raw token/hash secretをDB/Redis/log/evidenceへ保存しない。
  runtime specのbot configへ`callbackToken`だけを渡し、Botはcallback routeへ`X-Bot-Callback-Token`として送る。route path/bodyのmeeting/bot identityとRedis keyが一致する場合だけ、constant-time比較後にそのcallback操作だけを許す。 Meeting自身/他serviceの内部callは従来のstrong
  `X-Internal-Secret`を利用できる。callback dependencyだけが2 credential typeを明示的に扱い、capabilityを他internal endpointへ使えない。 Bot config/env、runtime meeting profile、Docker helperからglobal `INTERNAL_API_SECRET`注入/fallbackを削除する。rolling
  deployはMeeting dual-accept→Bot callbackToken対応→token発行有効→Bot env削除の順。 terminal callback後にcapability keyを削除し、duplicate callbackは既存idempotency契約の範囲だけ処理する。Redis障害時はglobal fallbackへ戻らず503。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-05H`と`bash scripts/test/run-required-suites.sh RF-05H`がexit
  0。`services/meeting-api/tests/test_callback_capability.py::{test_wrong_audience_user_meeting_session_bot_replay_expiry_and_redis_failure_have_zero_side_effects,test_deadline_ttl_is_capped_at_eight_hours_and_invalid_deadline_is_rejected,test_terminal_callback_revokes_capability,test_redis_record_copy_or_digest_tamper_cannot_forge_another_meeting_capability}`
  `services/vexa-bot/core/src/services/unified-callback.test.ts::{test_callback_uses_session_token_only,test_global_internal_fallback_is_absent}` generated Meeting Bot/runtime spec/envに`INTERNAL_API_SECRET`
  0、Agent/browserもRF-05Cの0を維持。 capability raw canaryはRedis hash値、DB、log、exceptionに0件。 `V-MEETING`, `V-CORE`, `V-BACKEND`。 suite=V-BACKEND,V-CORE,V-MEETING。
- リスク/戻し方: deploy順不一致でcallback停止。dual-accept期間のMeetingを先に配置し、global secretをBotへ戻して復旧しない。順序を満たせない場合は中断。 失敗時はR0。
- 依存: RF-05A, RF-05G
- コミット: `RF-05H scope bot callbacks with per-session capabilities`

### RF-06A Agent workspace storageをservice-side brokerへ移す
- 対象: `services/agent-api/agent_api/container_manager.py:151-158` `services/agent-api/agent_api/main.py:1-末尾` `services/agent-api/agent_api/workspace.py:1-120` `services/runtime-api/runtime_api/api.py:1-末尾`
  `services/runtime-api/runtime_api/backends/__init__.py:1-末尾` `services/runtime-api/runtime_api/backends/docker.py:1-末尾` `services/runtime-api/runtime_api/backends/kubernetes.py:1-末尾`
  `services/runtime-api/runtime_api/backends/process.py:1-末尾` `services/agent-api/README.md:1-末尾` read-only inventory: `git grep -n -E -e 'archive|workspace.*(upload|download)|s3 sync' -- services/agent-api
  services/runtime-api`。期待pathは上記write対象だけで、third-party license等の非実装一致はpath/lineをevidenceへ保存する。別production callerが1件でもあれば停止してplan reviewへ戻る 新規 `services/agent-api/tests/test_workspace_archive_broker.py:1-末尾` 新規
  `services/runtime-api/tests/test_archive_adapters.py:1-末尾`
- 問題: generated Agent containerへbucket-wide AWS/MinIO credentialを入れ、container内`aws s3 sync`を実行するため、利用者/AI shellから他user prefixへ到達できる。
- 変更: S3/MinIO credentialはAgent API service processだけが持つ。container specからAWS/S3 access/secret/session tokenを全削除する。 RuntimeへRF-05D2 service-auth済みarchive transfer APIを追加し、Agentのtrusted subjectからAgent
  serviceが決めたowner/containerと固定workspace rootだけをtar streamでupload/downloadする。Runtime request bodyの`user_id`やcontainer名だけを権限根拠にせず、clientは任意host/path/commandを指定できない。 restoreはAgent APIが自user
  prefixからobjectをstreamし、saveはRuntimeからtarをstreamして同じvalidator後に自user prefixへmultipart uploadする。directoryとregular fileだけを許し、absolute/`..`/NUL/drive prefix、symlink/hardlink、device/FIFO/socket、GNU
  sparse、PAX/global-PAXのpath/linkpath/size override、duplicate canonical path、case/Unicode normalization collisionをextract前にrejectする。`extractall()`を使わず、事前にregular directoryとして作ったworkspace
  rootのdirfdから`openat`/`mkdirat`（利用可能なら`openat2 RESOLVE_BENEATH|NO_SYMLINKS`）で各componentを`O_NOFOLLOW`照合し、pre-existing symlinkとextract中のrename/symlink raceをfail closedにする。fileはtemp siblingへ`O_EXCL`でstream→size/hash verify→same-dir
  atomic renameし、ownership/modeはserver allow-listからだけ設定する。上限はuncompressed合計2 GiB、単一file 512 MiB、entry 100,000件、path UTF-8 4,096 byte、stream chunk 1 MiB、multipart part 16 MiB、全体900秒、無通信60秒に固定する。超過は413/408、partial object/temp
  archiveを必ず削除する。 bucket list/deleteはAgent serviceが`users/<resolved_user_id>/workspace/` prefixへ明示制限し、container name/body user IDを権限根拠にしない。 local backendも同じbroker interfaceを使い、container内credential fallbackを作らない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-06A`と`bash scripts/test/run-required-suites.sh RF-06A`がexit
  0。`services/agent-api/tests/test_workspace_storage_broker.py::{test_cross_user_prefix_is_rejected_before_storage,test_round_trip_preserves_path_mode_and_content_hash,test_total_file_entry_path_and_idle_limits_cleanup_partial_upload}`
  `services/runtime-api/tests/test_workspace_archive_api.py::{test_body_user_id_cannot_override_service_bound_owner,test_malicious_tar_never_writes_outside_workspace,test_links_pax_sparse_devices_duplicates_preexisting_symlink_and_races_are_rejected,test_cancel_removes_temp_archive_and_multipart_upload}`
  A container/tokenからB prefixのlist/get/put/deleteは全て403/404・storage call 0。 malicious tar（`../`、absolute、全link種、PAX/sparse、device/FIFO/socket、duplicate、pre-existing symlink、rename/symlink race、oversize）をrejectしworkspace外write 0。
  container inspect/env、runtime spec、log、exceptionにstorage credential canary 0。 round-trip fixtureのfile path/mode/content hash一致。 `V-BACKEND`。 suite=V-BACKEND。
- リスク/戻し方: 大容量workspaceのmemory/timeout差。全処理をbounded streamingにし、bytes全読込を禁止。credentialをcontainerへ戻して救済せず、失敗時はworkspace save機能を停止して前SHAから再実行。 失敗時はR0。
- 依存: RF-03A, RF-05D2
- コミット: `RF-06A broker agent workspace storage outside generated containers`

### RF-06B Browser userdata storageをsession-scoped brokerへ移す
- 対象: `services/meeting-api/config/profiles.yaml:1-末尾` `services/meeting-api/meeting_api/meetings.py:797-812,1131-1140,1283-1360` 新規 `services/meeting-api/meeting_api/browser_storage_broker.py:1-末尾` 新規
  `services/meeting-api/meeting_api/browser_storage_repository.py:1-末尾` `services/meeting-api/tests/test_meetings.py:1-末尾` `services/vexa-bot/README.md:1-末尾` `services/vexa-bot/core/entrypoint.sh:1-末尾`
  `services/vexa-bot/core/src/BROWSER-SESSION.md:1-末尾` `services/vexa-bot/core/src/browser-session.ts:1-末尾` `services/vexa-bot/core/src/docker.ts:1-末尾` `services/vexa-bot/core/src/index.ts:1-末尾`
  `services/vexa-bot/core/src/s3-sync.ts:1-116` 新規 `services/vexa-bot/core/src/browser-storage-client.ts:1-末尾` read-only既知一致:
  `services/vexa-bot/{Dockerfile.experiment,core/entrypoint-experiment.sh,core/src/platforms/hot-debug.sh,hot-run.sh,run-zoom-bot.sh}:1-末尾`の一般BOT_CONFIG/debug処理、`services/vexa-bot/docs/recording-pipeline.md:1-末尾`のrecording記述
  read-only inventory: `git grep -n -F -e 'BOT_CONFIG' -e 's3-sync' -- services/meeting-api services/vexa-bot`。上記write対象またはread-only既知一致以外のproduction/config callerが1件でもあれば停止してplan reviewへ戻る 新規
  `services/meeting-api/tests/test_browser_storage_broker.py:1-末尾` 新規 `services/vexa-bot/core/src/browser-storage-client.test.ts:1-末尾`
- 問題: browser-session containerへbucket-wide MinIO access/secretを渡し、prefixは規約だけで強制されない。
- 変更: RF-05Hのcallback tokenとは別に、Meeting serviceだけが持つ`BROWSER_STORAGE_CAPABILITY_SIGNING_SECRET`でHS256署名したopaque browser-storage
  capabilityを発行する。claimは`v=1,iss=meeting-api,aud=browser-storage-broker,operations=["browser_storage"],sub=browser:<session_uid>,resolved_user_id,prefix,meeting_id,session_uid,iat,nbf,exp,jti`のexact allow-listとし、unknown
  claim/algorithmをrejectする。`exp=min(session_absolute_expiry,iat+86400)`、clock skew 5秒とし、session terminal、idle timeout、明示logoutのいずれでもjtiを即時revokeする。Redisにはjti revocation/expiryだけを保存し、client入力のdigest/metadataを権限根拠にしない。callback
  tokenは`operations={"callback"}`だけ。Meeting APIへdownload/upload broker routeを追加し、2 tokenを相互利用できない。 CoreのS3 syncをbroker HTTP clientへ置換し、browser userdataをbounded tar streamで取得/保存する。上限はuncompressed合計2 GiB、単一file 512 MiB、entry
  100,000件、path 4,096 byte、chunk 1 MiB、全体300秒、無通信30秒。RF-06Aと同じdirectory/regular-file-only、全link/PAX/sparse/device/FIFO/socket/duplicate/path normalization/pre-existing symlink/race reject、dirfd `openat`/`O_NOFOLLOW`
  extractionを共通vectorで必須にし、`extractall()`を使わない。coreへbucket endpoint/access/secretを渡さない。 Meeting serviceだけがMinIO/S3 credentialを持ち、`users/<resolved_user_id>/browser-userdata/`以外のlist/get/put/deleteを組み立てられないtyped repositoryを使う。
  browser-session `BOT_CONFIG`から`s3AccessKey/s3SecretKey`とglobal internal secretを削除し、broker URL + session capabilityだけを渡す。終了時capability失効後のuploadはreject。 既存save UI/Redis request-responseはRF-09Bまで維持し、Core内部の保存先transportだけを変える。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-06B`と`bash scripts/test/run-required-suites.sh RF-06B`がexit
  0。`services/meeting-api/tests/test_browser_storage_broker.py::{test_capability_is_bound_to_user_meeting_and_session,test_ttl_uses_session_absolute_expiry_and_one_day_cap,test_terminal_idle_and_logout_revoke_immediately,test_callback_and_storage_capabilities_are_not_interchangeable,test_archive_rejects_links_pax_sparse_devices_duplicates_preexisting_symlink_and_races_before_storage,test_archive_limits_and_path_rules_fail_before_storage,test_round_trip_preserves_userdata_hash_and_mode,test_tampered_redis_metadata_or_forged_digest_never_authorizes_storage}`
  `services/vexa-bot/core/src/browser-storage-client.test.ts::{test_uses_broker_without_s3_credentials,test_timeout_and_cancel_cleanup_partial_stream}` A session capabilityからB user/meeting prefixの全operation拒否・storage call 0。
  callback tokenをstorage routeへ、storage tokenをcallback routeへ送るfixtureは403・副作用0。 container env/BOT_CONFIG/inspect/logにMinIO/S3/internal credential canary 0。 userdata round-trip hash/mode一致、oversize/path traversal/symlink escape拒否。
  `V-MEETING`, `V-CORE`, `V-BACKEND`。 suite=V-BACKEND,V-CORE,V-MEETING。
- リスク/戻し方: browser profile復元/保存がbroker到達性へ依存する。dual transportをcontainer credential付きで残さず、Meeting先行→Core image→config切替の順。失敗時は新session作成を停止し前SHAから再実行。 失敗時はR0。
- 依存: RF-05H, RF-06A
- コミット: `RF-06B broker browser storage with session-scoped capabilities`

### RF-06C1 Redisをauthenticated service principalへ互換移行
- 対象: `deploy/compose/docker-compose.yml:1-末尾` `deploy/lite/{entrypoint.sh,supervisord.conf}:1-末尾`
  `deploy/helm/charts/vexa/{values.yaml,templates/_helpers.tpl,templates/secret.yaml,templates/deployment-redis.yaml,templates/service-redis.yaml}:1-末尾` 新規 `deploy/helm/charts/vexa/templates/configmap-redis-acl.yaml:1-末尾` RF-05D1の
  `deploy/helm/charts/vexa/templates/networkpolicy-workloads.yaml:1-末尾` `services/agent-api/agent_api/config.py:1-末尾` `services/agent-api/agent_api/main.py:1-末尾` `services/api-gateway/main.py:1-末尾`
  `services/meeting-api/config/profiles.yaml:1-末尾` `services/meeting-api/meeting_api/config.py:1-末尾` `services/meeting-api/meeting_api/main.py:1-末尾` `services/meeting-api/meeting_api/meetings.py:1-末尾`
  `services/runtime-api/profiles.yaml:1-末尾` `services/runtime-api/runtime_api/config.py:1-末尾` `services/runtime-api/runtime_api/main.py:1-末尾` `services/telegram-bot/bot.py:1-末尾` `services/agent-api/tests/test_g5_gate.py:1-末尾`
  `services/meeting-api/tests/collector/conftest.py:1-末尾` `services/meeting-api/tests/conftest.py:1-末尾` `services/runtime-api/tests/test_api.py:1-末尾` `services/runtime-api/tests/test_backends.py:1-末尾`
  `services/runtime-api/tests/test_integration_process.py:1-末尾` `services/runtime-api/tests/test_lifecycle.py:1-末尾` `services/runtime-api/tests/test_scheduler_api.py:1-末尾` `services/runtime-api/tests/test_state.py:1-末尾`
  `services/telegram-bot/tests/conftest.py:1-末尾` `services/runtime-api/runtime_api/backends/docker.py:1-末尾` `services/runtime-api/runtime_api/backends/kubernetes.py:1-末尾` read-only既知一致:
  `services/{agent-api,api-gateway,meeting-api,runtime-api}/README.md:1-末尾`と`services/runtime-api/.github/workflows/ci.yml:1-末尾` read-only inventory: `git grep -n -E -e 'REDIS_URL|redis://|Redis\(' -- services/meeting-api
  services/runtime-api services/api-gateway services/agent-api services/telegram-bot`。上記write対象またはread-only既知一致以外が1件でもあれば停止してplan reviewへ戻る 新規 `tests3/unit/refactor/test_rf_06c1.py:1-末尾` 新規
  `services/meeting-api/tests/test_collector_redis_acl.py:1-末尾`
- 問題: 現Compose/Helm Redisは匿名接続でき、全workloadと同一network/namespaceにいる。envからURLを消すだけでは侵害containerが`redis:6379`を推測してbrokerを迂回できる。
- 変更: permanent Redis ACL userを`meeting-api,runtime-api,api-gateway,agent-api,admin-api,mcp,calendar-service,telegram-bot,meeting-bot-legacy,browser-session-legacy`へ固定し、各passwordを別Secret
  keyにする。D1の`migrate-transcript-shares,migrate-telegram-map` principal/password/Secret refはOP-05Aで削除済みであることをbootstrap時に検査し、このmatrixへ再作成しない。全userは`resetkeys resetchannels
  -@all`から構築し、接続に実traceで必要な`AUTH,HELLO,PING,QUIT,CLIENT|SETINFO`と、DB番号が0以外の場合だけ`SELECT`を加える。`COMMAND,KEYS,SCAN,EVAL,EVALSHA,FUNCTION,FCALL,PSUBSCRIBE`とbroad `CLIENT`は全permanent userで禁止し、`~*`,`&*`,`+@all`をrenderへ1件も残さない。 exact ACL
  matrixを次に固定する。 `meeting-api`: keys
  `active_meetings,transcription_segments,speaker_events_relative,meeting:*,meeting_session:*,speaker_events:*,browser_session:*,va:meeting:*,webhook:retry_queue,meeting-api:container-stops,meeting-api:container-stop-dlq,identity-jti:meeting-api:*,capability:*,capability-session:*`;
  channels `bm:meeting:*:status,tc:meeting:*:mutable,bot_commands:meeting:*,browser_session:*`、RF-06C2でchannels `meeting:*:segments,va:meeting:*`を追加。現行`voice_agent.py`の`LRANGE va:meeting:{id}:event_log`に必要なkey
  grantはC1から含める。commands
  `GET,SET,DEL,EXPIRE,HGET,HGETALL,HSET,HDEL,SADD,SREM,SMEMBERS,ZADD,ZRANGEBYSCORE,LRANGE,LLEN,LPOP,RPUSH,LTRIM,XADD,XRANGE,XDEL,XGROUP|CREATE,XREADGROUP,XACK,XPENDING,XCLAIM,XINFO|GROUPS,XINFO|CONSUMERS,PUBLISH,SUBSCRIBE,UNSUBSCRIBE,WATCH,UNWATCH,MULTI,EXEC`。broad
  `XGROUP`,`XINFO`や他subcommandはgrantしない。 `runtime-api`: keys
  `runtime:container:*,runtime:callback:*,runtime:process:*,runtime:reservation:*,runtime:index:*,scheduler:jobs,scheduler:executing,scheduler:history,scheduler:idem:*,browser_session:*`; commands
  `GET,SET,DEL,SADD,SREM,SMEMBERS,ZADD,ZRANGE,ZRANGEBYSCORE,ZREM,HGET,HGETALL,HSET,HDEL,WATCH,UNWATCH,MULTI,EXEC`。現`scan_iter`はC1内で`runtime:index:{containers,callbacks,processes}`をbackfillし件数一致後に削除する。`browser_session:*`はRF-09Bまでの一時cleanup例外で、最終はMeeting
  typed cleanupへ移してACLから外す。 `api-gateway`: keys
  `ratelimit:*,gateway:token:*,share:transcript:*,share-by-token:*,browser_session:*,agent:sessions:*,identity-jti:api-gateway:*,mcp-assertion-jti:*,calendar-assertion-jti:*,telegram-assertion-jti:*,telegram-link:*,telegram-user-map:*,oauth-state:*`;
  read-only channels `tc:meeting:*:mutable,bm:meeting:*:status,va:meeting:*:chat`; commands `GET,SET,DEL,HGET,ZREMRANGEBYSCORE,ZADD,ZCARD,EXPIRE,SUBSCRIBE,UNSUBSCRIBE,WATCH,UNWATCH,MULTI,EXEC`。`PUBLISH`なし。 `agent-api`: keys
  `agent:session:*,agent:sessions:*,identity-jti:agent-api:*`; commands `GET,SET,DEL,EXPIRE,HGET,HGETALL,HSET,HDEL`。 `admin-api`: key `identity-jti:admin-api:*`; command `SET`だけ。GatewayのAdmin identityとMeeting webhook
  identityのjtiを同audience namespaceでconsumeし、他key/command 0。 `mcp`: key `identity-jti:mcp:*`; command `SET`だけ。 `calendar-service`: key `identity-jti:calendar-service:*`; command `SET`だけ。 `telegram-bot`: key `telegram-user-map:*`;
  command `GET`だけ。link/map write、旧`telegram:*`、`SCAN`、raw user token 0。 `meeting-bot-legacy`（RF-06C2まで）: keys `transcription_segments,speaker_events_relative,meeting:*`; channels
  `meeting:*:segments,tc:meeting:*:mutable,va:meeting:*,bot_commands:meeting:*`; commands `XADD,SET,DEL,RPUSH,LTRIM,EXPIRE,PUBLISH,SUBSCRIBE,UNSUBSCRIBE`。 `browser-session-legacy`（RF-09Bまで）: key `meeting:*:chat_messages`; channels
  `browser_session:*,bot_commands:meeting:*,va:meeting:*:chat`; commands `RPUSH,EXPIRE,PUBLISH,SUBSCRIBE,UNSUBSCRIBE`。 この互換commitではRedis `default` userを一時的に`on nopass`のまま残し、ACL userを先行作成する。service clientを各principal
  URLへ切り替え、generated Meeting/Browserだけは後続cutover用legacy userを優先する。credential値でなくprincipal別connection counterを記録する。 Runtime backendはgenerated workloadへ`runtime.managed=true`と`runtime.profile=meeting|browser-session|agent`
  labelをDocker/Kubernetes双方で付ける。Compose/Lite/Helmへ`infra`/`workload` network/NetworkPolicyの定義を先行追加するが、このcommitではlegacy workload到達をまだdenyしない。 rolloutはRedis ACL定義→service principal client→legacy workload principalの順。anonymous
  counterと各principal counterを`.pipeline/evidence/$TASK/operations/redis-c1-drain.json`へ値なしで保存する。RF-06C2のoperator gateまで`default off`やlegacy revokeを行わない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-06C1`と`bash scripts/test/run-required-suites.sh RF-06C1`がexit
  0。`tests3/unit/refactor/test_rf_06c1.py::{test_exact_redis_acl_principals_and_dangerous_command_denies,test_each_service_receives_only_own_redis_secret_ref,test_runtime_assigns_exact_managed_profile_labels,test_compatibility_keeps_default_and_two_legacy_users_only_until_next_gate,test_redis_secret_values_never_enter_render_logs_or_workload_args}`
  `tests3/unit/refactor/test_rf_06c1.py::{test_acl_dryrun_exact_positive_vectors_and_cross_principal_negative_matrix,test_meeting_voice_event_log_lrange_is_allowed_only_for_meeting_principal,test_meeting_consumer_pending_claim_and_xinfo_subcommands_are_exactly_allowed,test_render_contains_no_broad_key_channel_category_eval_keys_scan_or_command_grant,test_runtime_backfills_explicit_indexes_then_removes_scan,test_calendar_jti_oauth_state_admin_mcp_and_telegram_nonsecret_mapping_have_minimal_acl,test_admin_mcp_and_gateway_jti_keys_are_cross_principal_isolated,test_permanent_telegram_acl_has_get_only_and_no_scan,test_r1_temporary_migration_principals_secrets_and_scan_grants_are_absent}`
  `services/meeting-api/tests/test_collector_redis_acl.py::{test_stale_pending_entry_is_claimed_and_acked_with_meeting_principal,test_collector_health_reads_group_and_consumer_info_with_exact_subcommands,test_cross_principal_and_unlisted_xinfo_subcommands_are_noperm}`
  ACL-enabled local Redis fixtureで`ACL DRYRUN <user> <exact command...>`の正規vector全成功、各principalから他principalの全key/channel canaryをGET/SET/PUBLISH/SUBSCRIBEして全NOPERM・副作用0。production-like
  traceの実command/key/channel集合が宣言matrixのsubset、unknown 0。wrong principal/passwordはNOAUTH、secret値log 0。 Compose/Helm renderでserviceは自principal secret refだけ、Meeting/Browser legacy workload以外のgenerated workloadはRedis credential 0。
  `V-BACKEND`, `V-INTEGRATIONS`, `V-OPS`。 suite=V-BACKEND,V-INTEGRATIONS,V-OPS。
- リスク/戻し方: client inventory漏れでRedis依存serviceが停止する。default userを閉じる前のcompatibility itemなので、counterが残ればRF-06C2へ進まずclient inventoryをこのcommit内で直す。失敗branchを保持しRF-06BのSHAから再実行する。 失敗時はR1。
- 依存: RF-05F, RF-06B
- コミット: `RF-06C1 introduce authenticated redis principals before closing anonymous access`

### RF-05F2 no-kid identity互換をdrain後に削除
- 対象: `services/api-gateway/main.py:1-末尾` `services/calendar-service/app/{main,sync}.py:1-末尾` `services/agent-api/agent_api/auth.py:1-末尾` `services/meeting-api/meeting_api/auth.py:1-末尾` `services/admin-api/app/main.py:1-末尾`
  `services/mcp/main.py:1-末尾` `services/{transcription-service,tts-service,voiceprint-service}/main.py:1-末尾` `services/{wake-stt,wake-orchestrator}/app/main.py:1-末尾` `deploy/compose/docker-compose.yml:1-末尾`
  `deploy/lite/{entrypoint.sh,supervisord.conf}:1-末尾` `deploy/helm/charts/vexa/{values.yaml,templates/secret.yaml}:1-末尾` 新規 `tests3/unit/refactor/test_rf_05f2.py:1-末尾`
- 問題: RF-05Fはrolling deployのためno-`kid` current-key tokenを一時acceptする。drain後も残せば、key identifierによる選択を迂回する旧wireが恒久化する。
- 変更: OP-06C-DRAINのaudience別counter/hash/観測時間を検証後、`legacy_no_kid` verifier branch、counter、compat configを全audienceから削除する。 issuer/verifierはHS256かつknown current/optional previous `kid`だけを許し、missing/null/empty/unknown `kid`はsignature
  scan前にrejectする。current→previous総当たりを作らない。 RF-05F key ringの通常rotation contractは維持し、この項目でcurrent/previous値をrotateしない。previous refが必要な別rotation中なら本項目を開始せず、そのaudienceのoperator drainを完了する。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-05F2`と`bash scripts/test/run-required-suites.sh RF-05F2`がexit
  0。`tests3/unit/refactor/test_rf_05f2.py::{test_all_identity_audiences_require_known_kid_without_key_scanning,test_no_kid_null_empty_unknown_and_cross_audience_tokens_have_zero_upstream_or_database_calls,test_render_and_source_have_no_no_kid_compatibility_branch_or_counter}`
  `V-BACKEND`, `V-OPS`。 suite=V-BACKEND,V-OPS。
- リスク/戻し方: stale issuerが残れば401になる。OP-06C-DRAINをfresh cutover再検証し、失敗時はD3 compat componentを維持してD4を配備せず中断する。no-kid branchを新commitで戻さない。失敗時はR2。
- 依存: RF-05F, OP-06C-DRAIN
- コミット: `RF-05F2 remove drained no kid identity compatibility`

### RF-05G2 Admin署名MeetingToken互換をdrain後に削除
- 対象: `services/meeting-api/meeting_api/meetings.py:79-116` `services/meeting-api/meeting_api/collector/processors.py:25-65` `services/meeting-api/README.md:66-115` `deploy/compose/docker-compose.yml:90-140,260-285`
  `deploy/lite/supervisord.conf:100-150` `deploy/helm/charts/vexa/{values.yaml,templates/secret.yaml,templates/deployment-meeting-api.yaml}:1-末尾` `services/meeting-api/tests/test_meeting_token_signing.py:1-末尾`（RF-05G作成物） 新規
  `tests3/unit/refactor/test_rf_05g2.py:1-末尾`
- 問題: RF-05Gは既発行tokenを切らないためcollectorへAdmin-key legacy verifierを一時残す。drain後も残せばMeeting侵害からAdmin authorityへ至る旧境界が閉じない。
- 変更: OP-06C-DRAINのlegacy count、active old collector session、最大TTL/skew/retry観測を検証後、legacy Admin verification branch/ref/counterをcollector、Compose/Lite/Helmから削除する。 mint/verifyはknown
  `MEETING_TOKEN_SIGNING_{CURRENT,PREVIOUS}_KID`のexact keyだけを読み、Admin/Internal/JWT key ref/importを0にする。missing/unknown/no-kid tokenはcollector write前401/403。 Admin key自体のrotation/revocationはOP-06C証拠で完了済みであることをold-value canary
  hashで再検証し、値を証拠へ保存しない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-05G2`と`bash scripts/test/run-required-suites.sh RF-05G2`がexit
  0。`services/meeting-api/tests/test_meeting_token_signing.py::{test_known_current_and_previous_kid_only,test_admin_signed_no_kid_unknown_and_missing_kid_tokens_have_zero_collector_writes}`
  `tests3/unit/refactor/test_rf_05g2.py::{test_collector_and_meeting_have_no_admin_key_ref_or_legacy_verifier,test_only_meeting_and_collector_receive_meeting_signing_key_ring}` `V-MEETING`, `V-BACKEND`, `V-OPS`。
  suite=V-BACKEND,V-MEETING,V-OPS。
- リスク/戻し方: old collector/issuerが残ればtranscription停止。fresh cutover再検証が不合格ならD3 compat deploymentを維持しD4を配備せず中断する。Admin key fallbackを戻さない。失敗時はR2。
- 依存: RF-05G, RF-05F2, OP-06C-DRAIN
- コミット: `RF-05G2 remove drained admin signed meeting tokens`

### RF-06C2 Meeting Bot event brokerをlegacy互換で先行配備
- 対象: 新規 `services/meeting-api/meeting_api/workload_capabilities.py:1-末尾` 新規 `services/meeting-api/meeting_api/session_capability_registry.py:1-末尾` 新規 `services/meeting-api/meeting_api/bot_event_broker.py:1-末尾`
  `services/meeting-api/meeting_api/meetings.py:1021-1111` `services/vexa-bot/core/src/services/{segment-publisher,chat}.ts:1-末尾` `services/vexa-bot/core/src/index.ts:534-687,1060-1079,1347-1393,2414-2445`
  `deploy/compose/docker-compose.yml:1-末尾` `deploy/lite/{entrypoint.sh,supervisord.conf}:1-末尾` `deploy/helm/charts/vexa/{values.yaml,templates/configmap-redis-acl.yaml,templates/networkpolicy-workloads.yaml}:1-末尾`
  `services/runtime-api/profiles.yaml:1-末尾` 新規 `services/meeting-api/tests/test_bot_event_broker.py:1-末尾` 新規 `services/meeting-api/tests/test_session_capability_registry.py:1-末尾`
  既存（RF-06C1で追加済み）`services/meeting-api/tests/test_collector_redis_acl.py:1-末尾` 新規 `services/vexa-bot/core/src/services/event-broker-client.test.ts:1-末尾`
- 問題: RF-06C1は認証移行のためanonymous userとMeeting Bot legacy Redis credentialを残す。broker導入とlegacy閉鎖を同commitにすると、新Botの実trafficを観測する前にrollback経路まで失う。
- 変更: `BOT_EVENT_CAPABILITY_SECRET`から`iss=meeting-api,aud=meeting-event-broker,sub=bot:<bot_instance_id>,meeting_id,user_id,session_uid,operations,iat,nbf,exp,jti`の短命tokenを発行する。operationsは`transcript.write,speaker.write,pending.write,realtime.publish,chat.write,voice_event.write,command.subscribe`だけ、`exp=min(bot
  deadline+1h,iat+8h)`、clock skew 5秒、同一jti connection 1本。 `SessionCapabilityRegistry`をMeetingのserver-owned Redis
  repositoryとして追加する。発行transactionは`capability:<session_uid>:<aud>:<jti>`へ`owner,meeting_id,bot_instance_id,aud,operations,exp,status=active`をTTL付きSET NXし、session indexへjtiを追加できた場合だけtokenを返す。event
  brokerは署名検証後・Redis/副作用前にregistryのexact active recordを照合する。RF-06D1/RF-06Hのremote verifierは固定`POST /internal/workload-capabilities/introspect`へaudience別introspection
  tokenと`jti,aud,session_uid,operation`だけを送り、Meetingが同じ照合を行う。registry/Meeting unavailable・timeout・malformed responseは503、署名だけでfallbackしない。 normal terminal、explicit delete/logout、idle timeout、failed、cancelled、sweep
  cleanupの全経路はbounded audience indexを`WATCH -> active/index再読 -> MULTI -> 全record revoked + terminal marker -> EXEC`で`active ->
  revoked`へ変更し、全audience/jtiを副作用前に一括失効する。Lua/EVAL/EVALSHAは使わない。WatchErrorはjitter付き最大5回、超過時503・後続副作用0。terminal requestとcapability requestの100並列raceは、terminal linearization point後の副作用0、active
  token発行0。終了処理再試行は冪等で、index/recordは元expまで監査用に残すがactiveへ戻さない。 BotはMeeting semantic brokerだけを呼ぶ。HTTP batchは型付きDTO、command受信はclaim meeting専用SSE/WS。1 event 256 KiB、1 batch 100件/4 MiB、chat 64 KiB、pending speaker 256 byte、session 200
  event/秒・burst 400、heartbeat 30秒。claim/path/body不一致、unknown op、oversize/rate超過はRedis call前reject。 Brokerが表現できるRedis操作を`XADD transcription_segments`、`XADD speaker_events_relative`、derived pending
  `SET/DEL`、既存meeting/transcript/chat/voice channelへの`PUBLISH`、chat/event listの`RPUSH/LTRIM/EXPIRE`、claim meeting exactの`SUBSCRIBE bot_commands:meeting:{id}`だけへ固定する。任意key/channel、`PSUBSCRIBE`、管理commandをAPIへ出さない。 Coreへbroker
  clientを追加し、new Meeting Botはbroker URL+capabilityを優先する。D4では旧image/sessionのrollback用Redis client/URL/credential、Redis `default on nopass`、`meeting-bot-legacy`、Meeting profileのRedis到達をcompatibilityとして残すが、new broker
  fieldが存在するBotはRedisへfallbackせずbroker failureを返す。principal別`event_broker_success`と`meeting_bot_legacy` counterを値なしで記録する。Redis/default/legacy/networkの閉鎖はOP-06D-DRAIN後のRF-06D2だけが行う。 `chat.ts`の到達不能`if(false)` transcript
  XADDは削除し、chatはchat channel/listだけ。secret/URLをlogしない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-06C2`と`bash scripts/test/run-required-suites.sh RF-06C2`がexit
  0。`services/meeting-api/tests/test_bot_event_broker.py::{test_operation_route_matrix,test_wrong_meeting_session_bot_audience_and_expiry_make_zero_redis_calls,test_only_fixed_derived_redis_operations_are_possible,test_oversize_and_rate_limits_fail_before_redis}`
  `services/meeting-api/tests/test_session_capability_registry.py::{test_issue_registers_exact_session_audience_jti_before_returning_token,test_every_terminal_delete_timeout_failure_cancel_path_revokes_all_audiences,test_registry_failure_returns_503_without_signature_only_fallback,test_terminal_and_issue_hundred_way_race_has_no_post_terminal_active_token_or_side_effect,test_introspection_requires_exact_audience_service_token}`
  `services/vexa-bot/core/src/services/event-broker-client.test.ts::{preserves_existing_payload_and_order,chat_never_writes_transcript_stream,contains_no_raw_redis_transport}`
  `tests3/unit/refactor/test_rf_06c2.py::{test_new_meeting_bot_prefers_broker_and_never_falls_back_to_redis,test_default_and_meeting_legacy_users_remain_compatibility_only_until_rf_06d2,test_new_broker_field_has_no_redis_secret,test_old_session_fixture_keeps_declared_legacy_path_only}`
  new image/BOT_CONFIGのbroker-enabled fixtureはRedis call 0で既存payload/order一致。旧image/session fixtureだけlegacy counter exact 1。 generated new Meeting Bot env/BOT_CONFIG/inspect/logにRedis URL/password 0。旧session rollback
  profileとBrowser legacy以外のgenerated workload credential 0。 `V-MEETING`, `V-CORE`, `V-BACKEND`, `V-OPS`。 suite=V-BACKEND,V-CORE,V-MEETING,V-OPS。
- リスク/戻し方: broker差でlive transcriptが止まる。OP-06C-DRAINなしで開始しない。失敗時はnew session受付を停止してD3 compatibility componentへ戻し、legacy閉鎖を先行しない。失敗時はR1。
- 依存: RF-06C1, RF-05F2, RF-05G2, OP-06C-DRAIN
- コミット: `RF-06C2 deploy meeting event broker with legacy compatibility`

### RF-06D1 Workload capability validatorとissuerをlegacy互換で先行配備
- 対象: `services/meeting-api/meeting_api/{meetings,recordings}.py:1-末尾` `services/transcription-service/main.py:123-152,273-288` `services/wake-stt/app/main.py:36-45,68-77`
  `services/wake-orchestrator/app/{config,clients,orchestrator}.py:1-末尾` `services/tts-service/main.py:390-410,447-551` `services/vexa-bot/core/src/{index,types,docker}.ts:1-末尾`
  `services/vexa-bot/core/src/services/{transcription-client,wake-stt-client,recording,video-recording,tts-playback}.ts:1-末尾` `services/vexa-bot/core/src/platforms/googlemeet/recording.ts:1-末尾`
  `services/vexa-bot/core/src/platforms/msteams/recording.ts:1-末尾` `services/vexa-bot/core/src/platforms/zoom/web/recording.ts:1-末尾` 新規 `services/meeting-api/tests/test_workload_capability_audiences.py:1-末尾` 新規
  `services/transcription-service/tests/test_workload_auth.py:1-末尾` 新規 `services/wake-stt/tests/test_workload_auth.py:1-末尾` 新規 `services/tts-service/tests/test_workload_auth.py:1-末尾`
- 問題: Transcription/Wake/TTS static tokenとRecording用MeetingTokenを一度に削除すると、validator/issuer/Coreのrolling version差で音声処理全体が停止する。
- 変更: audience別相互非同値secretでHS256固定validatorを追加し、`iss=meeting-api`、exact `aud,operations,meeting_id,user_id,bot_instance_id,session_uid,iat,nbf,exp,jti`と上限claimを検査する。このcommitでは各routeがnew capabilityとlegacy credentialをdual
  acceptし、new pathを優先する。 new capability pathは署名/claim検査後、RF-06C2のintrospection endpointへ自audience専用tokenで問い合わせ、active record一致を確認してからprovider/storage/stream side effectへ進む。introspection 503/timeout/invalid JSON、record
  missing/revoked/mismatchはfail closedで副作用0。legacy compatibility pathだけはcounterを記録してregistryなしの現契約をRF-06D2まで維持する。 exact route/authを固定する。 Transcription workload: 新規`POST /v1/workload/audio/transcriptions`、`Authorization: Bearer
  <aud=transcription-service,op=audio.transcribe>`。既存`POST /v1/audio/transcriptions`+`TRANSCRIPTION_SERVICE_TOKEN`はdeferred service caller専用で維持。 Wake workload: 既存`POST /v1/audio/ingest`、Bearer capability優先、legacy
  `X-API-Key/WAKE_STT_TOKEN`はこのcommitだけfallback。WebSocket authは本項目対象外で現契約維持。 TTS workload: 既存`POST /v1/audio/speech`、Bearer capability優先、legacy `X-API-Key/TTS_API_TOKEN`はこのcommitだけfallback。`/health`,`/voices`は現契約維持。 Recording:
  既存`POST /internal/recordings/upload`、Bearer capability優先。`is_final=false`は`recording.chunk.upload`、`is_final=true`は`recording.final.upload`。legacy MeetingTokenはこのcommitだけfallback。 deferred専用`TRANSCRIPTION_SERVICE_TOKEN`は32
  byte以上・placeholder不可・production missing/emptyでstartup failure、constant-time比較、Meeting deferred callerとTranscription verifierのexact pairだけへ配る。空値で認証をskipする分岐を削除し、wrong/missingはprovider call前401。 Wake Orchestrator→Wake STT
  WebSocketにはworkload capabilityと別の`WAKE_ORCHESTRATOR_WS_SERVICE_TOKEN`を32 byte以上で追加し、Orchestrator issuer/clientとWake verifierだけへ配る。`Authorization: Bearer`または固定Sec-WebSocket-Protocol entryでhandshakeし、query string
  tokenを禁止する。Wake側missing/empty configはproduction startup failure、missing/wrong/query tokenはupgrade前401/403・audio/provider call 0。token/query/headerをaccess logへ残さない。 Transcriptionは25 MiB/request・1,200/min、Wakeは1
  MiB/frame・6,000/min・8時間累積、TTSは8,000文字・30/min・concurrency 2、Recordingはchunk 1 GiB/final 32 GiB/session 64 GiB。token TTLは`min(bot deadline+5m,iat+8h)`。同じjtiの正規session内再利用を許し、idempotencyはTranscription/Wake `request_id`、Recording
  `upload_id+chunk_seq`とpayload hashで別管理する。 Meetingはlegacy fieldsに加えて`collectorCapability,transcriptionCapability,wakeCapability,ttsCapability,recordingCapability`を発行する。Coreはnew fieldを優先するが、このcommitだけlegacy
  `botConfig.token`/static envへfallbackする。両方ある場合legacyを送らない。 audio/video final uploadを全体read/Buffer.concatから`stat`+`createReadStream` multipartへ変更する。12 GiB limit testはfake `stat.size=12GiB`と少量chunkのlogical counting
  ReadableでContent-Length/limitだけ検査する。実RSS/fd/retry testはtracked 256 MiB fixtureをstreamし、RSS増加128 MiB未満、retryごと新stream、abort後fd 0を検査する。12 GiBを実際に生成・読込しない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-06D1`と`bash scripts/test/run-required-suites.sh RF-06D1`がexit
  0。`services/meeting-api/tests/test_workload_capability_audiences.py::{test_exact_route_method_header_and_operation_inventory,test_cross_audience_matrix_is_rejected,test_claim_identity_mismatch_has_zero_side_effects,test_new_capability_wins_when_legacy_is_also_present,test_legacy_credentials_remain_temporary_fallback}`
  `services/meeting-api/tests/test_workload_capability_audiences.py::{test_new_capability_requires_active_registry_record_before_every_side_effect,test_registry_unavailable_never_falls_back_to_signature_only_or_legacy}`
  `services/transcription-service/tests/test_workload_auth.py::{test_new_workload_route_requires_transcription_audience,test_deferred_route_keeps_service_token}`
  `services/transcription-service/tests/test_workload_auth.py::{test_deferred_token_missing_empty_placeholder_fails_startup,test_deferred_wrong_or_missing_token_has_zero_provider_calls,test_only_meeting_deferred_caller_receives_service_token}`
  `services/wake-stt/tests/test_workload_auth.py::{test_ingest_prefers_bearer_capability,test_legacy_key_is_temporary_fallback,test_orchestrator_websocket_requires_dedicated_header_or_subprotocol_token,test_websocket_query_token_is_rejected_and_never_logged,test_missing_websocket_token_config_fails_startup}`
  `services/tts-service/tests/test_workload_auth.py::{test_speech_prefers_bearer_capability,test_legacy_key_is_temporary_fallback}` Coreの各clientでnew valid token response golden一致、new+legacy時legacy header/call 0。12 GiB logical
  testの実読込64 MiB未満、256 MiB streamのRSS/fd条件成功。 Transcription deferred static tokenとWake Orchestrator WS tokenのmissing/empty/wrong/cross-use全matrixは401/403またはstartup failure、副作用0。request URL/query/logにraw token 0。 `V-MEETING`,
  `V-TRANSCRIPTION`, `V-CORE`, `V-AUX`, `V-BACKEND`, `V-OPS`。 suite=V-AUX,V-BACKEND,V-CORE,V-MEETING,V-OPS,V-TRANSCRIPTION。
- リスク/戻し方: route/header/clock差でaudio停止。validator service→Meeting issuer→Core imageの順でcomponent deployし、legacy fallbackはRF-06D2まで残す。失敗branchを保持しRF-06C2のSHAから再実行する。 失敗時はR1。
- 依存: RF-05G, RF-06C2
- コミット: `RF-06D1 deploy workload capabilities with legacy compatibility`

### RF-06D2 Workload legacy tokenと汎用Bot tokenをdrain後に除去
- 対象: `services/meeting-api/meeting_api/{meetings,recordings}.py:1-末尾` `services/transcription-service/main.py:1-末尾` `services/wake-stt/app/main.py:1-末尾` `services/wake-orchestrator/app/{config,clients,orchestrator}.py:1-末尾`
  `services/tts-service/main.py:1-末尾` `services/vexa-bot/core/src/{index,types,docker}.ts:1-末尾` `services/vexa-bot/core/src/services/{transcription-client,wake-stt-client,recording,video-recording,tts-playback}.ts:1-末尾`
  `services/runtime-api/profiles.yaml:1-末尾` `deploy/{compose,lite,helm}/**:1-末尾` `services/meeting-api/meeting_api/collector/processors.py:29-65,156-176` 新規 `tests3/unit/refactor/test_rf_06d2.py:1-末尾`
- 問題: RF-06D1はrolling互換用にstatic workload credentialと汎用`botConfig.token`を残しており、cross-service replay余地がまだある。
- 変更: OP-06D-DRAINを検証後、Wake/TTS/Recording workload routeのlegacy fallbackを削除し、Transcription Botはnew workload routeだけを使う。deferred service callerの`POST /v1/audio/transcriptions`+service tokenだけはservice process内例外として残す。 RF-06C2のevent
  broker cutoverも同じdrain証拠へbindする。CoreのMeeting Redis client/URL/credential、`meeting-bot-legacy` ACL userを削除し、Redis `default off`、Meeting profileをworkload networkだけへ切り替える。Kubernetes NetworkPolicyは`runtime.profile=meeting`からRedis
  ingressをdenyする。Browser legacy user/profileだけはRF-09Bまで残す。new broker unavailable時にRedisへfallbackしない。 generated meeting/browser/agent
  containerのenv/BOT_CONFIG/profileから`TRANSCRIPTION_SERVICE_TOKEN`,`WAKE_STT_TOKEN`,`WAKE_STT_API_TOKEN`,`TTS_API_TOKEN`とlegacy MeetingTokenを削除する。signing secretはRF-05F exact service matrix外へ出さない。
  `botConfig.token/currentBotConfig.token`を削除し、用途別5 capabilityだけにする。collector envelopeは`aud=transcription-collector,op=segment.write|speaker.write`だけをRF-06C2 brokerがserver-side付加し、BotへMeetingTokenを渡さない。 全audience×全routeのcross
  matrix、revocation、request idempotency、normal load/limitをfinal authとして固定する。旧static/MeetingTokenは401/403・provider/storage/Redis/TTS call 0。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-06D2`と`bash scripts/test/run-required-suites.sh RF-06D2`がexit
  0。`services/meeting-api/tests/test_workload_capability_audiences.py::{test_cross_audience_matrix_is_rejected,test_recording_rejects_collector_and_legacy_meeting_tokens,test_claim_identity_mismatch_has_zero_side_effects,test_collector_contract_is_preserved_via_broker}`
  `tests3/unit/refactor/test_rf_06d2.py::{test_generated_workloads_have_no_static_media_or_tts_token,test_generic_bot_token_and_legacy_fallbacks_are_absent,test_default_and_meeting_legacy_redis_users_are_off,test_meeting_profile_has_no_redis_network_or_secret,test_only_browser_legacy_remains_until_rf_09b,test_only_deferred_transcription_service_keeps_static_token,test_signing_secret_distribution_matches_rf_05f_matrix}`
  8-speaker/500ms Wake/Transcription retry/30秒chunk/12 GiB logical final/TTS normal loadは成功、上限+1だけ429/413。cross audience/expired/revoked/bad signatureは副作用0。 `rg -n 'botConfig\.token|currentBotConfig\.token|token: botConfig\.token'
  services/vexa-bot/core/src` 0。generated workload env/config/mount/job/logのstatic token/signing secret canary 0。 `V-MEETING`, `V-TRANSCRIPTION`, `V-CORE`, `V-AUX`, `V-BACKEND`, `V-OPS`。
  suite=V-AUX,V-BACKEND,V-CORE,V-MEETING,V-OPS,V-TRANSCRIPTION。
- リスク/戻し方: legacy caller drainが虚偽ならaudio/recording停止。OP-06D-DRAINなしで開始しない。失敗時は新規Bot作成を止め、legacy secretを新commitへ戻さずRF-06D1 componentへrollbackしてtask未完とする。 失敗時はR2。
- 依存: RF-06C2, RF-06D1, OP-06D-DRAIN
- コミット: `RF-06D2 remove workload legacy tokens after capability drain`

### RF-06E Agent containerのprovider credentialをsubject所有へ限定
- 対象: `deploy/compose/docker-compose.yml:1-末尾` `services/agent-api/agent_api/container_manager.py:140-176` `services/agent-api/agent_api/config.py:32-37` `services/agent-api/agent_api/main.py:1-末尾`
  `services/runtime-api/profiles.yaml:81-96` `services/meeting-api/config/profiles.yaml:45-53` read-only inventory: `git grep -n -E -e 'ANTHROPIC_API_KEY|CLAUDE|provider.*credential' -- services/agent-api services/runtime-api
  deploy/compose deploy/lite deploy/helm`。期待pathは`deploy/compose/docker-compose.yml`、上記Agent 3 file、`services/runtime-api/profiles.yaml`だけで、別pathが1件でもあれば停止してplan reviewへ戻る 新規
  `services/agent-api/tests/test_provider_credential_ownership.py:1-末尾`
- 問題: Agent service全体の`ANTHROPIC_API_KEY`とhost上のClaude OAuth credential fileを全user containerへ渡す。1 user/AI shellがplatform共有provider権限を持ち出せる。
- 変更: Agent service global configの`ANTHROPIC_API_KEY`、`CLAUDE_CREDENTIALS_PATH`、`CLAUDE_JSON_PATH`と、Runtime agent profileのglobal provider env/mountを削除する。host credential fileをcontainerへbind mountする経路を残さない。 RF-03Aの任意dict
  `AgentRuntimeConfig.env`をprovider用途に使わず、subject dataから読むtyped `provider_credentials={anthropic_api_key}`だけを許可する。server側mapperがこれをexact `ANTHROPIC_API_KEY`へ変換し、未知provider
  fieldと`VEXA_*`,`*_URL`,`HTTP_PROXY`,`HTTPS_PROXY`,`PATH`,`LD_*`,`DYLD_*`,`PYTHONPATH`,`NODE_OPTIONS`等のreserved keyは保存/Runtime call前422にする。 container envは「固定system allow-listを構築→subject-owned provider fieldをexact provider
  env名へ追加」の順で新dictを作り、user入力dictのmergeやserver global provider credential fallbackをしない。user A configはresolved subject Aのcontainerだけへ渡し、body/query user IDで上書きできない。 provider credentialのkey/valueをresponse/log/evidenceへ出さず、Runtime
  create bodyをcaplogするtestでもvalueをredactする。Runtimeのprofile/list/build summaryへ解決値を保存しない。 既存Claude OAuth fileはcontainerへ移行・copyしない。OAuth file利用者は本項目のrollout前にsubject-owned Anthropic API keyを設定し、未設定なら既存の認証不足errorで停止する。 provider
  credentialがない場合、containerは作成できるがClaude実行要求はnetwork/process開始前に既存の認証不足errorを返す。platform共有credentialを復活させるfallbackを作らない。 将来platform-managed provider accessが必要なら、利用量/subjectをbindするprovider brokerを別taskで設計する。本項目で汎用LLM proxyを追加しない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-06E`と`bash scripts/test/run-required-suites.sh RF-06E`がexit
  0。`services/agent-api/tests/test_provider_credential_ownership.py::{test_global_provider_key_and_host_oauth_mount_are_never_propagated,test_only_resolved_subject_provider_field_reaches_own_container,test_reserved_and_unknown_env_keys_are_rejected_before_runtime_call,test_cross_user_body_cannot_select_credentials,test_missing_user_credential_fails_before_provider_process,test_provider_values_are_absent_from_caplog_response_and_exception}`
  Compose/Lite/Helm renderとRuntime profile responseでgenerated Agent env/mountにglobal `ANTHROPIC_API_KEY`、Claude credential path/file canary 0。 user-owned canaryはA container create callだけexact 1件、B
  container/log/exception/evidenceに0。 `rg -n 'CLAUDE_CREDENTIALS_PATH|CLAUDE_JSON_PATH' services/agent-api services/runtime-api/profiles.yaml` はmigration test以外0。 `V-BACKEND`, `V-OPS`。 suite=V-BACKEND,V-OPS。
- リスク/戻し方: platform共有Claude credentialに依存する既存環境ではAgent実行が認証不足になる。共有secretを戻さず、各userへsubject-owned credential設定を案内する。失敗branchを保持しRF-06D2のSHAから再実行する。 失敗時はR0。
- 依存: RF-03A, RF-05D2, RF-06A
- コミット: `RF-06E keep agent provider credentials subject owned`

### RF-06F Zoom pre-signed SDK JWT受理をsecret除去より先に配備する
- 対象: `services/meeting-api/meeting_api/meetings.py:1158-1175` 新規 `services/meeting-api/meeting_api/zoom_sdk_tokens.py:1-末尾` 新規 `services/meeting-api/tests/test_zoom_sdk_tokens.py:1-末尾` `services/vexa-bot/core/src/index.ts:1-末尾`
  `services/vexa-bot/core/src/platforms/zoom/index.ts:1-末尾` `services/vexa-bot/core/src/platforms/zoom/strategies/join.ts:14-33` `services/vexa-bot/core/src/platforms/zoom/sdk-manager.ts:203-223` 新規
  `services/vexa-bot/core/src/platforms/zoom/sdk-manager.test.ts:1-末尾` `services/vexa-bot/run-zoom-bot.sh:1-40` 新規 `services/vexa-bot/tests/test_zoom_runner_auth.sh:1-末尾` `services/vexa-bot/README.md:101-123` read-only既知一致: Zoom
  meeting URL password fixtureの`services/meeting-api/tests/test_url_parser_and_dry_run.py:1-末尾`と`services/vexa-bot/core/src/platforms/zoom/web/join.test.ts:1-末尾`、別purpose HMAC
  testの`services/vexa-bot/core/src/services/production-replay.test.ts:1-末尾`、provider SDK headerの`services/vexa-bot/core/src/platforms/zoom/native/zoom_meeting_sdk/h/auth_service_interface.h:162,165` read-only inventory: `git grep
  -n -E -e 'ZOOM_(CLIENT|SDK).*(ID|SECRET)|zoom.*secret|createHmac|tokenExp|appKey' -- deploy/compose deploy/lite deploy/helm services/runtime-api services/meeting-api
  services/vexa-bot`。上記write対象またはread-only既知一致以外のproduction/config wiringが1件でもあれば停止してplan reviewへ戻る
- 問題: Zoom native SDK用client secretを生成Bot envへ渡し、Bot側で24時間のapp-level JWTを何度でも生成できる。JWT受理とsecret除去を1コミットにすると、旧Core rollbackができない。
- 変更: Meeting側`zoom_sdk_tokens.py`でHS256 JWTを生成し、Botへ`ZOOM_SDK_JWT`とpublic client IDを追加する。このコミットでは旧Core rollback用`ZOOM_CLIENT_SECRET`配線を残し、RF-06GまでPhase 1を完了扱いにしない。 token payloadはZoom
  SDKが現在使う`appKey,iat,exp,tokenExp`だけ。`iat=now-5s`、`exp=tokenExp=min(max(iat+1800s, bot_deadline+300s), iat+7200s)`へ固定し、Zoom最小30分と最大2時間を両立する。token/secretをDB/Redis/log/evidenceへ保存しない。
  Coreの`ZoomSDKManager`は`ZOOM_SDK_JWT`がある場合それだけをSDK authenticateへ渡し、client secret HMACを呼ばない。JWT欠落時だけ既存secret pathを互換利用するが、両方あるのにlegacyへfallbackしてはいけない。
  `run-zoom-bot.sh`も`ZOOM_SDK_JWT`優先、secretはこのコミットだけfallbackとし、READMEへRF-06G後にJWT必須になることを明記する。 `test_zoom_runner_auth.sh`は`--report-json <task-owned path> --case <exact case>`を1回以上受け、未知/重複case、任意output、extra
  argvを拒否する。RF-06F時点ではcompletionに列挙した2 caseをこの順で実装・実行し、RF-06Gが3件目`test_runner_rejects_client_secret_and_requires_presigned_jwt`を追加する。各item reportの`required_test_names`と`cases[]`は、そのitem completionに存在するexact case列とbyte一致させる。fake
  Dockerだけを使い、`collected,passed,failed,skipped,required_test_names,cases[]`をJSONへatomic writeする。 rolling順はMeeting JWT発行→Core pre-signed対応image→manual runnerのJWT smoke。全環境でJWT path成功後だけRF-06Gへ進む。 Zoom SDK
  JWTはprovider仕様上meeting-boundでない残余リスクがあるため、最大2時間とnetwork policyで縮小し、完全なmeeting bindingとは主張しない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-06F`と`bash scripts/test/run-required-suites.sh RF-06F`がexit
  0。`services/meeting-api/tests/test_zoom_sdk_tokens.py::{test_token_uses_existing_claim_contract_thirty_minute_floor_and_two_hour_cap,test_short_bot_deadline_still_meets_zoom_minimum,test_secret_and_token_are_never_persisted_or_logged}`
  `services/vexa-bot/core/src/platforms/zoom/sdk-manager.test.ts::{uses_presigned_jwt_without_calling_legacy_hmac,presigned_jwt_wins_when_both_credentials_exist,legacy_secret_still_supports_old_image_rollback,rejects_missing_or_expired_token_before_sdk_auth}`
  generated Zoom BotはJWTとlegacy secretをこの互換commitだけ受ける。JWTありfixtureでHMAC call 0、JWT/secretのDB/Redis/log/evidence保存0。
  `services/vexa-bot/tests/test_zoom_runner_auth.sh::{test_runner_prefers_presigned_jwt,test_legacy_secret_is_only_temporary_fallback}` `V-MEETING`, `V-CORE`, `V-BACKEND`, `V-OPS`。 suite=V-BACKEND,V-CORE,V-MEETING,V-OPS。
- リスク/戻し方: provider clock skew/JWT TTLでnative joinが止まる。このcommitは旧pathを残すため旧Coreへ戻せるが、RF-06Gを開始せずtask未完として報告する。fixture tokenとMeeting/Core rollout versionを直し、失敗branchを保持してRF-06EのSHAから再実行する。 失敗時はR1。
- 依存: RF-05F, RF-06D2
- コミット: `RF-06F accept server signed zoom sdk tokens before secret removal`

### RF-06G Zoom SDK client secretを全workloadから除去する
- 対象: `services/meeting-api/meeting_api/meetings.py:1158-1175` `services/meeting-api/meeting_api/zoom_sdk_tokens.py:1-末尾`（RF-06F作成物） `services/vexa-bot/core/src/index.ts:1-末尾`
  `services/vexa-bot/core/src/platforms/zoom/index.ts:1-末尾` `services/vexa-bot/core/src/platforms/zoom/strategies/join.ts:14-33` `services/vexa-bot/core/src/platforms/zoom/sdk-manager.ts:203-223`
  `services/vexa-bot/core/src/platforms/zoom/sdk-manager.test.ts:1-末尾`（RF-06F作成物） `services/vexa-bot/run-zoom-bot.sh:1-40` `services/vexa-bot/tests/test_zoom_runner_auth.sh:1-末尾`（RF-06F作成物） `services/vexa-bot/README.md:101-123`
  read-only既知一致: Zoom meeting URL password fixtureの`services/meeting-api/tests/test_url_parser_and_dry_run.py:1-末尾`と`services/vexa-bot/core/src/platforms/zoom/web/join.test.ts:1-末尾`、別purpose HMAC
  testの`services/vexa-bot/core/src/services/production-replay.test.ts:1-末尾`、provider SDK headerの`services/vexa-bot/core/src/platforms/zoom/native/zoom_meeting_sdk/h/auth_service_interface.h:162,165` read-only inventory:
  RF-06Fと同じZoom検索。上記write対象またはread-only既知一致以外のproduction/config wiringが1件でもあれば停止してplan reviewへ戻る
- 問題: RF-06Fは安全なJWT pathを先行配備したが、rollback用client secretとBot側HMACがまだ残る。
- 変更: `ZOOM_CLIENT_SECRET`はMeeting service processだけが持つ。Bot env/BOT_CONFIG、Runtime profile、Core config/typeからclient secretを削除する。 CoreのHMAC生成、`crypto.createHmac`、client secret引数、legacy fallbackを削除し、pre-signed JWT欠落/期限切れ/不正形式はSDK
  call前にfailする。 `run-zoom-bot.sh`は`ZOOM_SDK_JWT`とpublic client IDだけを必須にし、secret入力/`-e`注入を削除する。READMEのBot必須env表からsecretを削除し、短期JWTはMeeting serviceの認証済みbot作成フローから得ること、直接secretでmintしないことを明記する。 rolloutはRF-06FのJWT path
  evidence確認→新Core/manual runner→runtime spec secret削除の順。RF-06F互換commitへ戻す必要が出た場合はtaskを未完で停止し、RF-06G合格を主張しない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-06G`と`bash scripts/test/run-required-suites.sh RF-06G`がexit
  0。`services/meeting-api/tests/test_zoom_sdk_tokens.py::{test_missing_server_secret_fails_before_runtime_create,test_only_meeting_service_holds_client_secret_ref}`
  `services/vexa-bot/core/src/platforms/zoom/sdk-manager.test.ts::{uses_presigned_jwt_without_client_secret,rejects_missing_or_expired_token_before_sdk_auth}`
  `services/vexa-bot/tests/test_zoom_runner_auth.sh::test_runner_rejects_client_secret_and_requires_presigned_jwt` generated Zoom Bot env/BOT_CONFIG/inspect/log/Redisに`ZOOM_CLIENT_SECRET` canary 0。Meeting service secret refだけexact
  1。 `rg -n 'ZOOM_CLIENT_SECRET|createHmac' services/vexa-bot services/runtime-api/profiles.yaml deploy/compose deploy/lite deploy/helm` はMeeting server wiring/negative test以外0。 `V-MEETING`, `V-CORE`, `V-BACKEND`, `V-OPS`。
  suite=V-BACKEND,V-CORE,V-MEETING,V-OPS。
- リスク/戻し方: RF-06Fで未検出のJWT互換差があるとjoin停止。secretを同commitへ戻さず、失敗branchを保持しRF-06FのSHAから新worktreeでRF-06Gを再実行する。運用rollbackでRF-06F imageへ戻した場合はtask statusを未完にする。 失敗時はR2。
- 依存: RF-06F, OP-06F-DRAIN
- コミット: `RF-06G remove zoom client secrets from every workload`

### RF-06H upstream proxy credentialをsession-scoped egress brokerへ移す
- 対象: `services/meeting-api/meeting_api/meetings.py:1084-1175` 新規 `services/meeting-api/meeting_api/workload_proxy_capabilities.py:1-末尾` 新規 `services/meeting-api/tests/test_workload_proxy_capability.py:1-末尾`
  `services/runtime-api/runtime_api/api.py:1-末尾` 新規 `services/workload-broker/{pyproject.toml,Dockerfile}:1-末尾` 新規 `services/workload-broker/workload_broker/{__init__,main}.py:1-末尾` 新規
  `services/workload-broker/tests/test_egress_proxy.py:1-末尾` 新規 `deploy/helm/charts/vexa/templates/{deployment-workload-broker,service-workload-broker}.yaml:1-末尾`
  `deploy/helm/charts/vexa/{values.yaml,templates/secret.yaml,templates/configmap-runtime-profiles.yaml}:1-末尾` `deploy/compose/docker-compose.yml:1-末尾` `deploy/lite/{entrypoint.sh,supervisord.conf}:1-末尾`
  `services/vexa-bot/core/src/index.ts:2479-2485,2523-2535,2607-2610` `services/runtime-api/profiles.yaml:1-末尾` のmeeting proxy env read-only inventory: `git grep -n -E -e 'HTTP_PROXY|HTTPS_PROXY|proxy.*credential' --
  services/meeting-api services/runtime-api deploy/compose deploy/lite deploy/helm`。baseでservices側一致0、deploy側既存proxy wiring 1件以上を期待し、結果をitem evidenceへ保存する。services側一致が増えていればtargetを自動拡張せずplan reviewへ戻る 新規
  `services/runtime-api/tests/test_proxy_capability.py:1-末尾`
- 問題: `HTTP_PROXY/HTTPS_PROXY`へ`user:password@host`形式のupstream credentialを入れると、生成Bot/Browserがplatform共有proxy credentialを読める。proxy自体もprivate networkへの迂回経路になり得る。
- 変更: upstream proxy scheme/host/portは非secret config、username/passwordまたはcredential全体はCompose/Liteの0600 secret file、Helm `existingSecret`固定keyの`secretKeyRef`だけに置く。values/ConfigMap/profile/evidence/process argsへ平文0。proxy有効なのにSecret
  ref欠落はstartup failure。 credentialと`BOT_PROXY_CAPABILITY_SECRET`は新規`workload-broker`だけが保持する。brokerはnon-root、read-only rootfs、all capabilities drop、Docker socket/Kubernetes token/Runtime principal/DB/Redis credential 0。Runtime
  control planeはsession network attach/detachだけを行い、CONNECT/HTTP bytesをparseしない。generated meeting workloadへはbroker URLと`aud=workload-egress-proxy`のsession capabilityだけを渡し、upstream host/userinfoを渡さない。現HEADでproxy wiringがないBrowser
  Sessionへ新規適用しない。 Meetingはplatformとserver-side provider registryから`allowed_host_rules`（ASCII lowercase exact hostまたは承認済みsuffix
  boundary）と`allowed_provider_cidr_ids`を解決し、capabilityへ`meeting_id/session_uid/container_name,platform,operations=["http.forward","https.connect"],allowed_host_rules,allowed_provider_cidr_ids,allowed_ports=[80,443],max_connections=128,max_bytes=21474836480,iat,nbf,exp,jti`を署名する。workload
  request/BOT_CONFIGはallow-listを指定・追加できない。`exp=min(bot deadline+300s,iat+28800s)`、clock skew 5秒、同一jtiの同時connection上限128。別session/container/platform/audience/revoked jtiはconnect前403。 brokerは署名検証後、各CONNECT/forward開始前にRF-06C2
  registryを`CAPABILITY_INTROSPECTION_PROXY_TOKEN`で照合する。registry unavailable/timeout/revokedは503/403でupstream socket 0、署名だけのoffline fallback 0。terminal revocationで既存connectionもgrace内にcloseする。 brokerはHTTP
  forwardとCONNECTの最小実装だけをinternal port `8091`で提供し、host portへbindしない。header上限32 KiB、connect timeout 15秒、idle timeout 300秒、session総転送20 GiB、graceful shutdown 30秒に固定する。 Meeting Botの3 Playwright launch pathは同じpure
  `meetingProxyOptions(config)`を使い、`proxy={server:"http://workload-broker:8091",username:"vexa-session",password:<session capability>}`を渡す。capabilityをURL/BOT_CONFIG log/process argsへ出さず、407時にproxyなしで再launchしない。Browser Session
  launch pathは変更しない。 RF-05EのDNS/IP policyで**destination target**の元hostnameを各request直前にbroker自身が解決し、capabilityのexact/suffix host ruleとprovider registryのCIDR双方に一致する全A/AAAAだけを許す。別に、固定configの**upstream proxy endpoint**をexact
  allow-list、TLS verify-full、固定CA、元proxy hostname/SNIで検証し、DNS検証済みproxy IP literalへpinしてTLS接続する。brokerはそのTLS tunnel内だけで`CONNECT <validated destination IP literal>:<port>`を送り、upstreamへdestination hostnameを再解決させない。tunnel成立後のbrowser
  TLS/HTTPは元destination hostnameをHost/SNI/証明書検証へ使う。`Proxy-Authorization`はupstream TLS内のCONNECT requestにだけ付与し、destination tunnel bytes、redirect先、packet/logへ1 byteも出さない。upstreamがliteral
  CONNECTをsupportしない場合は起動preflightでfailし、hostname CONNECTへfallbackしない。port 80のcredential-bearing plaintext upstreamは常に禁止し、TLS upstream内のIP-literal CONNECT後にraw HTTPをtunnelするmodeをupstreamがsupportしない場合はHTTP
  forwardをdisableする。destinationまたはproxy socket peer IPが各検証集合外ならrequest body/Proxy-Authorization送信前にcloseし、retryは両endpointを再解決・再検証・再pinする。private/non-global/metadata/single-label/IP literal、任意public
  host、redirect先の未許可host/CIDRをdenyする。workloadから任意header credential、proxy chaining設定、SOCKS、UDPを受けない。実装前preflightでupstream schemeをinventoryし、`socks4/socks5`またはTLS非対応proxyが1件でも設定済みならcredential除去前に停止してoperatorへTLS対応HTTP CONNECT
  proxyへの移行を要求する。`workload-broker /health`はlistener/introspection dependencyとupstream literal-CONNECT/TLS capabilityを検査し、設定なし時は`disabled`、設定ありでlistener failureは503。Runtime `/health`はdata-plane secret・broker
  task・listener状態を保持または報告しない。 upstream `Proxy-Authorization`はworkload-brokerがdispatch時に追加し、request/Redis/backend metadata/logへ保存しない。logはtarget public host/port、session hash、resultだけ。 proxy未設定profileではbroker/capabilityを作らずdirect
  egressの現仕様を維持する。proxy設定時にbroker unavailableならdirect fallbackせずBot作成/connectionをfailする。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-06H`と`bash scripts/test/run-required-suites.sh RF-06H`がexit
  0。`services/workload-broker/tests/test_egress_proxy.py::{test_upstream_userinfo_never_reaches_workload_configmap_profile_or_logs,test_capability_is_bound_to_session_container_platform_server_host_rules_and_provider_cidrs,test_workload_cannot_choose_or_expand_allowlist,test_private_metadata_arbitrary_public_and_dns_rebinding_targets_are_rejected,test_two_hop_proxy_pins_verified_upstream_and_destination_ips_with_original_sni,test_connect_uses_destination_ip_literal_and_upstream_cannot_reresolve_public_to_private,test_proxy_authorization_exists_only_inside_verified_upstream_tls_and_never_in_destination_packet_or_log,test_plaintext_credential_upstream_and_unsupported_literal_connect_fail_closed,test_proxy_configured_never_falls_back_to_direct,test_fixed_connection_byte_timeout_and_header_limits,test_listener_health_and_graceful_shutdown,test_socks_configuration_blocks_before_workload_change,test_broker_has_no_control_plane_database_or_redis_reachability}`
  `services/workload-broker/tests/test_egress_proxy.py::{test_registry_unavailable_or_revoked_has_zero_upstream_sockets,test_runtime_health_has_no_broker_listener_or_data_plane_secret_state}`
  `services/meeting-api/tests/test_workload_proxy_capability.py::{test_meeting_issuer_derives_allowlist_server_side_and_binds_session_container_platform,test_request_cannot_supply_proxy_host_rules_cidrs_or_credential,test_capability_and_upstream_secret_never_enter_bot_config_log_redis_or_evidence}`
  `services/vexa-bot/core/src/meeting-proxy-options.test.ts::{test_all_three_meeting_launch_paths_use_fixed_proxy_auth,test_407_never_relaunches_without_proxy,test_browser_session_launch_is_unchanged}` mock
  transport/resolverだけを使い実Internet/localhost接続0。wrong capability時upstream connect 0。 generated Meeting Bot env/BOT_CONFIG/inspect/log/Redisにupstream proxy URL/user/password canary 0。session capabilityは自sessionだけで成功。Browser
  Sessionはproxy field/behavior追加0。 bounded mockで64並列connectionと5 GiB相当stream counterが上限未達、129接続/20 GiB+1 byteだけ429/413。capability/tokenはprocess argsとURLに0。 Helm render canaryはSecret dataを表示せず、workload-brokerの`secretKeyRef` exact
  1、Runtime/Meeting/Browser/Agent/Gateway 0。broker侵害fixtureからDocker socket/Kubernetes API/Runtime control port/DB/Redisは全connection denied。 `V-BACKEND`, `V-MEETING`, `V-OPS`。 suite=V-BACKEND,V-MEETING,V-OPS。
- リスク/戻し方: Chromium CONNECT互換、throughput、stream cleanupで会議参加が止まる。bounded fake proxyとmeeting smokeで確認し、userinfo env/direct fallbackを戻さない。失敗branchを保持しRF-06GのSHAから再実行する。 失敗時はR0。
- 依存: RF-05D2, RF-05E, RF-05F, RF-06D2, RF-06G
- コミット: `RF-06H broker upstream proxy credentials per session`

### RF-06I1 既存Runtime resourceをserver-owned labelへbackfillし操作時に再照合
- 対象: `services/runtime-api/runtime_api/api.py:178-272` `services/runtime-api/runtime_api/backends/docker.py:143-202` `services/runtime-api/runtime_api/backends/kubernetes.py:150-227` 新規
  `services/runtime-api/runtime_api/models.py:1-末尾` `deploy/helm/charts/vexa/templates/{deployment-runtime-api,rbac-runtime-api}.yaml:1-末尾` 新規 `services/runtime-api/tests/test_resource_ownership_backfill.py:1-末尾`
- 問題: RF-05D1で新規createを安全化しても、既存container/PodとRedis stateにはserver-owned label/owner/profile/backend identityが欠ける。get/exec/deleteがRedis recordだけを信頼すると、旧resourceや改ざんstateへ越権できる。
- 変更: RF-05D1以後のnew resource recordをimmutable `backend_id,owner_subject_hash,session_uid,profile,server_nonce,expected_labels,created_at`へ統一する。RF-03A/06Eのprovider/workspace refはserver-side resolverで解決し、record/envへraw credential 0。
  既存resource inventoryをread-only走査し、Runtimeが作成したことを既存label+state+name+creation time全一致で証明できるものだけmaintenance window中にserver-owned labelへbackfillする。曖昧、owner不明、label
  conflict、外部作成resourceは`quarantined_legacy`として操作禁止にし、自動adopt/deleteしない。件数/ID hashだけをoperator reportへ保存する。 get/touch/exec/archive/delete/stop/reaperはRedis recordとbackend実体のID、owner、profile、server nonce、全expected
  label一致を毎回再検査する。不一致は409/404、backend operation 0、stateを実体へ合わせて書換えない。Kubernetes SA/automountとDocker bind/network/capabilityもRF-05D1 profile contractから逸脱していないことをinspectする。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-06I1`と`bash scripts/test/run-required-suites.sh RF-06I1`がexit
  0。`services/runtime-api/tests/test_resource_ownership_backfill.py::{test_only_provably_runtime_owned_legacy_resources_are_backfilled,test_ambiguous_external_and_conflicting_resources_are_quarantined_not_adopted_or_deleted,test_every_operation_rechecks_backend_id_owner_profile_nonce_labels_and_security_profile,test_state_tamper_never_relabels_or_operates_backend,test_provider_and_workspace_credentials_are_absent_from_immutable_record}`
  A principal+B backend/state fixtureはstart/touch/exec/archive/delete/stop 0。正規resourceだけ既存response契約で成功し、backfill件数+quarantine件数=inventory件数。 `V-BACKEND`, `V-OPS`。 suite=V-BACKEND,V-OPS。
- リスク/戻し方: 旧resourceがquarantineされ操作不能になる。自動adopt/deleteで救済せず、operatorへhashed ID/理由を渡して個別再作成する。失敗branchを保持しRF-06HのSHAから再実行する。 失敗時はR0。
- 依存: RF-05D1B, RF-05D2, RF-06A, RF-06E
- コミット: `RF-06I1 backfill and recheck runtime resource ownership`

### RF-06I2 Workload間VNC・CDP ingressをsession単位で隔離
- 対象: `services/vexa-bot/core/entrypoint.sh:73-89` `services/vexa-bot/core/src/index.ts:2303-2331` `services/runtime-api/runtime_api/backends/{docker,kubernetes}.py:1-末尾` 新規
  `services/workload-access-broker/{pyproject.toml,Dockerfile}:1-末尾` 新規 `services/workload-access-broker/workload_access_broker/{__init__,main}.py:1-末尾` 新規 `services/workload-access-broker/tests/test_access.py:1-末尾` 新規
  `services/runtime-network-policy-agent/{pyproject.toml,Dockerfile}:1-末尾` 新規 `services/runtime-network-policy-agent/runtime_network_policy_agent/{__init__,main}.py:1-末尾` 新規
  `services/runtime-network-policy-agent/tests/test_policy.py:1-末尾` 新規 `deploy/runtime-network-policy-agent/runtime-network-policy-agent.service:1-末尾` 新規 `deploy/runtime-network-policy-agent/policy.json:1-末尾` runtime output:
  `/var/lib/vexa/runtime-network-policy-agent/state.json:1-末尾` `deploy/lite/{bot-slot-wrapper.sh,entrypoint.sh,supervisord.conf,Dockerfile.lite}:1-末尾` `services/api-gateway/main.py:1886-2338`
  `services/agent-api/agent_api/{main,container_manager}.py:1-末尾` `services/meeting-api/meeting_api/session_capability_registry.py:1-末尾`（RF-06C2作成物） 新規
  `deploy/helm/charts/vexa/templates/{deployment-workload-access-broker,service-workload-access-broker,networkpolicy-workload-access}.yaml:1-末尾` RF-05D1の `deploy/helm/charts/vexa/templates/networkpolicy-workloads.yaml:1-末尾`
  `deploy/helm/charts/vexa/{values.yaml,templates/secret.yaml,templates/rbac-runtime-api.yaml}:1-末尾` `deploy/compose/docker-compose.yml:1-末尾`
- 問題: generated workloadsがflat network上にあり、x11vnc `-nopw`、websockify 6080、CDP relay 9223へ他sessionから直結できる。Gateway subject認証を迂回して画面/cookieを奪取できる。
- 変更: Chrome 9222とx11vnc 5900はworkload loopbackだけへbindする。Runtimeはbackend ID/label検証後、audienceごとのEd25519 keypair+jtiをmemory内で生成する。private keyは直後のbootstrap以外に渡さず、public keyだけをowner serviceへ登録する。 Runtime create DTO/HTTP
  requestはprivate keyを含めず、container/Podをkeyなし・readiness falseで作成する。固定bootstrap operationだけが`uint32 length + PKCS8 key bytes + EOF`のframed stdinでper-session 0600 tmpfs fileまたはsealed Linux memfdへ渡す。任意exec
  APIはこのoperationを表現できない。relayがread後unlink/locked memory化する。RuntimeはRedis/registry credentialを持たず、profileから解決したowner serviceの固定`POST
  /internal/workload-access/{bind,activate,fail}`だけを呼ぶ。Meeting/browserには`RUNTIME_MEETING_ACCESS_STATE_SECRET`、Agentには`RUNTIME_AGENT_ACCESS_STATE_SECRET`を使い、相互利用不可。body-bound
  assertionは`iss=runtime-api,aud=<owner-service>,operation,relay_jti,public_key,public_key_sha256,owner_subject_hash,session_uid,profile,backend_id,registration_jti,broker_ack_sha256,method,path,body_sha256,iat,exp<=30s,state_jti`を持つ。owner
  serviceは自身のcanonical session/container recordを照合し、bindでregistry recordを不存在から`bound`へSET NX、activate/failでexact current recordをCASする。wrong owner/profile/backend/key/caller/replayはregistry write 0。`bound`はuser connect用active
  introspectionをdenyする。 RuntimeのmTLS+HS256 registration bodyは`owner_service,profile,relay_jti,public_key,public_key_sha256,session_uid,backend_id,registration_jti`を含む。access brokerはowner別の固定registration-check
  endpointへ`ACCESS_MEETING|ACCESS_AGENT` introspection tokenで問い合わせ、`bound` recordのpublic key/hash/backend/profile一致だけを確認する。このregistration-only checkはuser connectを許可しない。続いてbrokerは256-bit nonce+audience/session/backend/jti
  transcriptをrelayへ送り、relayはEd25519 private keyで署名する。brokerはregistration bodyのpublic keyで検証し、private key/共通raw tokenをbroker↔relay networkへ1 byteも送らない。成功ackをRuntimeが同じowner serviceのactivate endpointへbindしてからreadiness
  trueへする。bind/registration/check/challenge/ack/activationのどれかが失敗したらowner fail endpointで`failed|revoked`へ遷移し、作成resourceをcleanup、broker memory record/connectionとprivate key
  memory/tmpfsを破棄する。一度もactiveにせず、retryでfailed/revokedを再利用しない。Docker inspect、Kubernetes rendered Pod、Runtime DB/Redis/backend metadata/job/log、packet capture、`/proc/*/{cmdline,environ}`へprivate-key canary
  0。ProcessBackendはRF-06I3の`pass_fds`または0600 private tmpfsだけを使う。 user-visible経路をGateway→subject auth→別service `workload-access-broker`→session relayへ統一する。RF-06Hのegress
  `workload-broker`とprocess/container/Deployment/ServiceAccount/secretを共有しない。access brokerはupstream proxy credential、`BOT_PROXY_CAPABILITY_SECRET`、`CAPABILITY_INTROSPECTION_PROXY_TOKEN` 0、egress brokerはVNC/CDP private
  key、`CAPABILITY_INTROSPECTION_ACCESS_{MEETING,AGENT}_TOKEN`、registration/state secret 0。両方non-root/read-only/capabilities 0で、Docker socket/Kubernetes API/Runtime principal/DB/Redis secret 0。 Runtime control planeはnetwork
  attach/detach後、`WORKLOAD_ACCESS_REGISTRATION_SECRET`でHS256署名した`iss=runtime-api,aud=workload-access-broker,session_uid,backend_id,server_generated_dns_name,ports={vnc:6080,cdp:9223},method="POST",normalized_path="/internal/registrations",canonical_body_sha256,content_length,iat,exp<=300s,registration_jti`を固定routeへ送る。DNS名はRF-06I1
  server-generated backend labelとexact一致、portsはenumで任意値不可。brokerは同じsession networkへattach済みの自身からDNS/label一致をprobeし、registration_jtiをone-time消費して登録する。body差替/replay、任意IP/host/headerはrecord/relay call 0。 access brokerはpublic
  key/context/connectionだけをmemory recordへ持ち、private keyを一度も受けない。broker process/pod restart時はkeyを復元・reissue・再登録せず全registrationをfail closedにし、owner service/Runtimeへrecreate-required health eventを返して該当sessionをterminal化する。旧relay_jti
  revoke、connection close、endpoint/network cleanup後にsession再作成だけが新keypairを配布する。 registration listenerはRF-05FのmTLS Secretだけを使いTLS 1.3、server SAN=`workload-access-broker`、client SAN=`runtime-api`を両側で検証する。plaintext port/listener
  0、HTTP downgrade/redirect 0、`verify=false` 0。wrong/expired CA/SAN/client certはbody parse前reject・memory record 0。cert rotationはnew CA bundle+new server/client certを先行配置→mutual canary→旧cert connection 0観測→旧CA/cert ref除去の順で、private
  keyをnetworkへ1 byteも送らない。 `session_handle`は新しいsecretやmappingではなく、既存server-generated canonical `session_uid`（lowercase UUID文字列）を使う。owner lookupの唯一sourceはprofile別の既存canonical record（meeting/browser=`Meeting`、agent=`Agent`）で、lookup
  keyは`owner_service+profile+session_uid`とし別profile/ownerの同文字列を同一視しない。新しいowner mapping DBを作らない。 D6では旧Gateway `/b/{token}`とCDP `?api_key=`を即時削除せず、新しい非secret `/b/{session_handle}` routeとdual serveする。new sessionは旧route/tokenを発行せず、old
  sessionだけlegacy counter付き旧routeを使う。OP-09-DRAINが`active_old_browser_sessions=0`と`active_old_agent_cdp_sessions=0`を証明した後、RF-09Bが旧route/query verifierを削除する。既存sessionをsilent
  disconnectして継続扱いにせず、maintenance時に新session受付をfreezeし明示terminal/recreate_requiredとする。 authorize/consumeをprofile-awareにする。meeting/browser profileはMeetingのserver-owned session owner recordと固定Meeting authorize/consume
  endpoint、standalone Agent CDPはAgentのserver-owned user/session/container recordと固定Agent authorize/consume endpointを唯一sourceにする。Gatewayは256-bit proposed `access_jti`を生成し、trusted
  identityで`sub,owner_service,profile,session_uid,session_handle,aud,operation,method,path,access_jti,exp<=30s`をowner authorize endpointへbody-bound送信する。ownerがsubject/recordを照合し`authorized` recordをSET
  NX/TTL保存した応答後だけ、Gatewayは同claimを`GATEWAY_WORKLOAD_ACCESS_IDENTITY_SECRET`で署名してbrokerへ送る。brokerは同じowner serviceのconsume endpointで既存recordをatomic `authorized -> consumed`にし、owner別`ACCESS_MEETING|ACCESS_AGENT`
  introspectionでrelay_jti active/public keyを照合してから署名challengeを行う。未登録、claim差、forged/replayed jtiはrelay challenge 0、Gateway crash recordはTTLで失効する。Meeting tokenをAgentへ、Agent tokenをMeeting/Browserへcross-replayした場合はowner lookup/relay
  connect 0。registration/state/access jtiを同値比較・共用しない。URL/history/Referer/access logにAPI key/private key 0。A subject+B handleはHTTP 403/WS 4403、broker/relay connect 0。 Gateway/BrowserへRuntime control-plane tokenやprivate
  keyを返さない。access broker APIはverified+consumed identityから得たowner/profile/session/audienceだけを使い、任意host/port/headerを表現できず、terminal時にowner registry revoke→connection close→relay key破棄を行う。一方のowner registry outage時に他ownerへfallbackしない。
  Dockerはsessionごとのinternal bridgeを作り、対象workloadと`workload-access-broker`だけをattachする。Runtimeはcontrol call時だけDocker APIでnetwork lifecycleを管理しdata networkへattachしない。Docker bridge自体はegress
  denyにならないため、root-owned別process`runtime-network-policy-agent`だけに`CAP_NET_ADMIN`とhost network namespaceを与える。agentはnetwork listener、Docker socket、Kubernetes token、DB/Redis/service credentialを持たない。Unix socket
  directoryは`root:vexa-runtime` 0710、socketは0660で、agentは`SO_PEERCRED`のfixed Runtime UID/GIDと`RUNTIME_NETWORK_POLICY_MAC_SECRET`のone-time body MACを両方検証する。 Runtimeはnetwork-policy
  agentへcontainer/veth/ifindex/cgroupを指定しない。agent自身がsession作成時にopaque `network_handle`、専用managed bridge、root-owned cgroup subtree、one-time owner nonceを生成し、handleだけをRuntimeへ返す。Runtimeはbackend
  create時にそのhandleを参照できるが既存`lo,eth0,docker0`、control/infra bridge、host/root cgroup、他session handleを表現できない。apply/cleanup時にagent自身がDocker root-owned inspect stateと`bridge master == managed bridge`、veth peer、cgroup ancestry、session
  UID、owner nonceを再照合し、全一致した対象だけを操作する。agent入力は`network_handle,session_uid,profile,provider-registry ID,owner nonce`だけで、container ID/veth/ifindex/cgroup/host/CIDR/port/rule/digestを受けない。任意・stale・別session target、cleanup
  replay、handle改ざんはnft/cgroup/network change 0。agent自身がroot-owned immutable profile policy+provider/Git host registryからDNS検証済みIP/portを導出し、Runtime compromiseでも任意target/allow ruleを表現不能にする。host boot unitはDockerより前にpersistent base
  default-deny `inet` table/`DOCKER-USER` chainをrestoreし、agentがroot-owned MAC付きstate manifestとkernel managed bridge/cgroup/nft ruleをreconcileするまで全managed bridge packetをdenyする。process kill時はkernel rulesが残り、restart/reboot中もpacket
  0。missing/reused handle/ifindex/cgroup、manifest/rule差、agent unavailableはworkload readiness false。atomic apply後だけreadiness ack、session終了時はowner/backend照合後rule+manifest cleanup。workloadへNET_ADMIN/host namespaceを与えない。
  Kubernetesは`vexa-workloads` namespaceの`runtime.managed=true` Podをdefault-deny ingress/egress、`app=workload-access-broker`から6080/9223だけallow、workload相互traffic 0とする。Runtime Roleは同namespaceだけ、control
  namespace/Secrets/APIへのreachability 0をRF-05D1 contractどおり再検証する。 egress matrixをexact固定する。全profileはcontrolled DNS resolverのUDP/TCP 53だけ共通許可し、169.254.169.254、100.100.100.200、link-local、RFC1918、cluster/control namespace
  CIDR、Kubernetes API、Redis/Postgres/MinIO、Runtime control port、他workload CIDRをdenyする。meeting profileはMeeting event/media brokerとserver-side provider registryのexact host+resolved public CIDR/80/443、browser profileはMeeting browser
  brokerと明示public host/CIDR 80/443、agent profileはGateway/Agent brokerとRF-03Dのallow-listed Git host/pinned public CIDR 80/443だけ。workload/client入力はhost/CIDRを追加できない。proxy-enabled meetingはpublic 80/443
  directをdenyしworkload-brokerだけ。WebRTC UDPは調査時fixtureで使用するprovider CIDR+3478/19302-19309だけをallowし、unknown provider CIDRが必要ならpolicyを広げず停止する。 LiteのVNC/CDP
  portは`bot-slot-wrapper.sh`を唯一のallocatorとし、`DISPLAY,VNC_PORT,WEBSOCKIFY_PORT,CDP_PORT,CDP_RELAY_PORT`を予約recordからentrypoint/supervisorへ渡す。entrypoint内の`:99/5900/6080/9222/9223` hard-codeを削除し、2 session
  fixtureで全port非重複、終了後予約0。Xvfbの`-ac`を削除してsession 0600 `XAUTHORITY`+MIT cookieを必須にし、x11vncはlocalhost+password file、PulseAudioはsession private socket/source/sinkだけを使う。root password fallback、sshd、openssh-server/sshpass、SSH
  port/UI表示を全profileから削除し、remote commandは認証済みbroker/Runtime execだけに限定する。 rolling順はauthenticated relay対応image→workload-access-broker→owner別state/introspection→new Gateway route→runtime-network-policy-agent/Kubernetes deny
  policy。deny policy前に正規Gateway goldenを通し、new sessionへdirect endpointをpublic発行しない。旧routeはOP-09までold session専用compatibilityとしてだけ残す。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-06I2`と`bash scripts/test/run-required-suites.sh RF-06I2`がexit
  0。`services/workload-access-broker/tests/test_access.py::{test_capability_is_bound_to_owner_service_profile_session_container_audience_and_registry,test_arbitrary_host_port_and_header_are_unrepresentable,test_terminal_revocation_closes_connections,test_access_and_egress_brokers_have_disjoint_secrets_and_processes,test_broker_has_no_control_plane_database_or_redis_credentials,test_registration_requires_runtime_mtls_signature_public_key_server_generated_dns_fixed_ports_and_backend_label,test_gateway_route_preserves_existing_vnc_and_cdp_contract}`
  `services/workload-access-broker/tests/test_access.py::{test_runtime_generates_keypair_bootstraps_private_and_binds_public_key_to_exact_owner,test_bound_registration_check_does_not_enable_user_connect,test_ed25519_nonce_challenge_matches_registered_public_key_before_activation,test_private_key_never_crosses_service_network_or_persists,test_owner_specific_bind_activate_fail_revoke_and_introspection_never_cross_fallback,test_registration_body_is_digest_bound_and_registration_jti_is_one_time,test_restart_revokes_relay_jti_and_requires_session_recreation_without_key_restore,test_gateway_preregisters_access_jti_before_signing_and_consume_requires_exact_existing_record,test_unregistered_forged_mismatched_and_replayed_access_jti_make_zero_relay_challenges,test_access_jti_and_relay_jti_have_separate_one_time_and_lifetime_semantics,test_subject_a_cannot_open_subject_b_http_ws_vnc_or_cdp_session,test_session_handle_is_namespaced_by_owner_and_profile,test_old_route_cutover_requires_zero_old_browser_and_agent_sessions,test_url_query_referer_history_and_access_log_contain_no_api_key_or_private_key}`
  `services/runtime-network-policy-agent/tests/test_policy.py::{test_socket_requires_runtime_peer_uid_and_one_time_mac,test_agent_creates_opaque_handle_managed_bridge_and_cgroup_and_runtime_cannot_choose_target,test_runtime_input_cannot_supply_container_veth_ifindex_cgroup_host_cidr_port_or_rule,test_control_infra_host_and_other_session_targets_change_zero_rules,test_agent_derives_exact_root_owned_profile_policy,test_wrong_reused_handle_ifindex_cgroup_owner_nonce_and_manifest_are_fail_closed,test_cleanup_replay_or_foreign_handle_changes_zero_network_state,test_process_kill_restart_and_host_reboot_keep_managed_packets_denied_until_reconcile,test_atomic_apply_readiness_and_cleanup}`
  `tests3/unit/refactor/test_rf_06i2.py::{test_docker_uses_one_internal_network_per_session_with_workload_access_broker_only,test_kubernetes_default_denies_workload_ingress_and_allows_workload_access_broker_only,test_runtime_role_cannot_touch_control_namespace_or_secrets,test_exact_profile_egress_matrix_denies_metadata_control_plane_datastores_and_cross_workload,test_proxy_enabled_meeting_has_no_direct_public_egress,test_loopback_chrome_and_vnc_have_no_public_direct_listener,test_lite_allocates_unique_display_vnc_websocket_and_cdp_ports_and_cleans_reservations,test_lite_xauthority_and_audio_are_session_private,test_ssh_root_password_packages_ports_and_ui_are_absent}`
  session A workloadからBの6080/9223へTCP/HTTP/WS/CDP全拒否、B page/cookie/keyboard side effect 0。wrong private-key proof/owner/profile時relay connect 0。正規Gateway pathだけdesktop/mobile VNC/CDP golden一致。 generated workloadにRuntime
  control-plane credential/global relay secret/K8s SA token 0。cleanup後network/key/connection/policy rule 0。 `V-BACKEND`, `V-MEETING`, `V-DASH`, `V-CORE`, `V-OPS`。 suite=V-BACKEND,V-CORE,V-DASH,V-MEETING,V-OPS。
- リスク/戻し方: VNC/CDP tunnel、dynamic network cleanupでBrowser Session表示が止まる。deny policyを最後に適用し、正規Gateway smoke不合格ならpolicyを広げずtask未完で停止する。失敗branchを保持しRF-06I1のSHAから再実行する。 失敗時はR0。
- 依存: RF-06I1, RF-06H, RF-06C2, RF-05D1B, RF-05A
- コミット: `RF-06I2 isolate workload vnc and cdp ingress by session`

### RF-06I3 Lite ProcessBackendをsecret分離しsingle-tenant限定へ固定
- 対象: `services/runtime-api/runtime_api/backends/process.py:1-末尾` `services/runtime-api/runtime_api/api.py:178-272` `deploy/lite/Dockerfile.lite:1-末尾` `deploy/lite/{entrypoint.sh,supervisord.conf,bot-slot-wrapper.sh}:1-末尾`
  `deploy/helm/charts/vexa-lite/values.yaml:1-末尾` `deploy/helm/charts/vexa-lite/templates/secret.yaml:1-末尾` `deploy/helm/charts/vexa-lite/templates/deployment.yaml:1-末尾`
  `deploy/helm/charts/vexa-lite/templates/dashboard-deployment.yaml:1-末尾` 新規 `deploy/lite/lite-init.py:1-末尾` 新規 `deploy/lite/lite-services.json:1-末尾` 新規 `services/runtime-api/tests/test_process_backend_isolation.py:1-末尾` 新規
  `tests3/unit/refactor/test_rf_06i3.py:1-末尾`
- 問題: ProcessBackendは`os.environ.copy()`とhost subprocess execを使い、Liteはroot/same UID、Supervisor親env、共有filesystem/X11/audioを全processへ継承する。container/Kubernetes隔離だけ直しても別session・別service secretへ到達できる。
- 変更: ProcessBackend createは空dictからprofile別exact allow-listを構築し、親envをcopy/mergeしない。allow-list外、`*_SECRET,*_TOKEN,*_KEY,DATABASE_URL,REDIS_URL,AWS_*,MINIO_*,KUBECONFIG,DOCKER_HOST`は子env 0。Bot childへ渡すのはserver生成session ID、RF-06C2
  capability、broker URL、予約済みdisplay/port、localeだけ。ProcessBackend `exec`は生成時と同じsession UID/namespaceへ入れる実装がない限り403/501でhost subprocessを1回も起動しない。 Liteは汎用Supervisorのroot常駐をやめ、固定manifestだけを読む最小`lite-init` PID
  1へ置換する。`lite-init`は起動時だけUID 0かつ`SETUID,SETGID,KILL`の3 capabilityを持ち、network listener、shell、任意command/config interpolationを持たない。service別0600 secret fileをopenし、各childへ必要なFDだけを渡して`env
  -i`、`setgroups([])→setgid→setuid→PR_SET_NO_NEW_PRIVS`、capability 0、umask 077で起動する。全childは起動barrier pipeで待機し、親が自身を`lite-monitor`非root UID/GIDへ変更して全capability/securebitsをpermanent
  dropし、`/proc/self/status`の`Uid/Gid/CapEff/CapPrm/CapBnd`を検証した後だけbarrierをrelease/readiness trueにする。drop失敗時はlistenerを開かず全child/FDを終了してcontainer failure。drop後はchild restart/host execを行わず、1 child終了でPID 1も終了してcontainer
  runtimeに全cgroup cleanupを委ねる。 service別secret FDとexact allow-listだけを読み、単一`secrets.env`を全programへsourceしない。Meeting/Admin/Gateway/Runtime/Broker/Botは別UID/GID、supplementary group 0、`no_new_privs`, capabilities 0、umask 077。session
  rootは0700、file 0600、private `TMPDIR/XDG_RUNTIME_DIR/XAUTHORITY`、終了時recursive cleanup。`/proc`はhidepid相当または別UIDから`environ/cmdline/fd` EACCESを必須にする。唯一のUID0/capability例外はreadiness前の`lite-init`で、shell/HTTP/debug routeから到達不能とする。
  process recordは`pid,proc_starttime_ticks,pgid,uid,session_uid,exe_inode,command_digest`をcreate直後にimmutable保存する。inspect/exec/stop/remove/reaperはsignalまたはstatus判断前に`/proc/<pid>/{stat,status,exe}`を再読し全field一致、PGIDがserver-generated
  session group一致の場合だけ操作する。PID再利用、UID/session/exe/starttime/PGID不一致はstale tombstoneへ移してsignal 0、recordを別processへ付け替えない。100 create/stop/PID-reuse simulationで別service/session process kill 0。
  ProcessBackend/Liteを`DEPLOYMENT_MODE=single-tenant-development`かつ同時session総数1専用とし、production、複数subject、Agent/browser multi-tenant profile、ownerを問わず2 session目はpreflight/create
  failure。sessionごとに未使用UID/GIDを割当て、終了時に全process/file/socket cleanupとidentity照合が完了するまでUIDを再利用しない。Docker/Kubernetes backendだけをmanaged production対応とする。single sessionでもRF-06I2のXAUTHORITY/audio/SSH廃止を必須にし、root shell/root
  passwordへfallbackしない。 Helm `vexa-lite`はRF-05Fのmain-container bootstrap例外をexact維持し、Dashboardはnonroot/capability 0のままにする。全Secret `envFrom`、service account token、host
  namespace/mount/socket、空securityContext、replica>1、autoscaling/ingressをrenderで拒否する。readiness probeは`lite-init` permanent drop proofと全child UID/FD allow-listを照合し、単にport openだけで成功しない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-06I3`と`bash scripts/test/run-required-suites.sh RF-06I3`がexit
  0。`services/runtime-api/tests/test_process_backend_isolation.py::{test_child_environment_starts_empty_and_contains_only_profile_allowlist,test_parent_database_redis_provider_and_signing_canaries_are_absent_from_proc_environ,test_exec_without_same_session_sandbox_returns_403_without_subprocess,test_session_directory_modes_unique_uid_owner_cleanup_and_safe_uid_reuse,test_any_second_session_and_managed_production_process_backend_fail_before_spawn}`
  `services/runtime-api/tests/test_process_backend_isolation.py::{test_stop_remove_inspect_and_reaper_verify_pid_starttime_pgid_uid_session_exe_and_command,test_stale_pid_reused_by_another_uid_or_service_is_tombstoned_and_never_signaled,test_hundred_create_stop_pid_reuse_races_never_kill_unowned_process}`
  `tests3/unit/refactor/test_rf_06i3.py::{test_every_lite_program_uses_env_i_separate_uid_and_separate_secret_file,test_no_shared_secret_file_or_parent_environment_inheritance,test_cross_uid_proc_secret_file_tmp_x11_and_audio_access_is_eacces,test_no_root_user_password_sshd_sshpass_or_host_exec_path,test_docker_and_kubernetes_remain_only_managed_production_backends}`
  `tests3/unit/refactor/test_rf_06i3.py::{test_lite_init_has_only_setuid_setgid_kill_before_barrier_and_permanently_drops_all_before_readiness,test_lite_child_crash_exits_container_without_privileged_restart,test_vexa_lite_chart_has_exact_bootstrap_exception_and_nonroot_dashboard,test_vexa_lite_rejects_production_multiple_sessions_envfrom_service_account_token_and_host_mount}`
  実process fixtureでservice AからBの`/proc/<pid>/environ`, secret file, session dir, X11 socket/cookie, PulseAudio socketが全EACCES。readiness後の全processはroot UID/group/capability 0、cleanup後process/file/port
  0。readiness前`lite-init`だけはexact 3 capability、0.0.0.0 listener 0。 suite=なし（項目固有testのみ）。
- リスク/戻し方: 既存Lite multi-session利用は停止する。隔離を弱めず、単一利用者developmentへ明示移行するかDocker/Kubernetes backendへ切り替える。失敗branchを保持しRF-06I2のSHAから再実行する。 失敗時はR0。
- 依存: RF-06I2, RF-05F
- コミット: `RF-06I3 isolate lite processes and restrict process backend to single tenant`

### RF-08 Browser workspace git操作をargv実行へ変更
- 対象: `services/vexa-bot/core/src/browser-session.ts:14-116`
- 問題: shell文字列へのrepo/ref/path埋め込み、credential入りURL、終了code無視によりcommand injection、token leak、false successがある。
- 変更: RF-03DでAgent imageへ固定した`/system/bin/vexa-git-bootstrap`をBrowser profileにも同一hashでCOPYし、Browser側からは`spawn(helper,["--repo",credentialFreeUrl,"--branch",validatedRef],{stdio:["pipe",...]})`だけを使う。tokenは同じuint32
  big-endian framed stdin、session-private `/run/vexa-git`、AskPass、credential-free origin、redirect禁止、allow-listed HTTPS host、DNS global判定+verified IP pin、Host/SNI維持、ref/path containment契約を再利用し、別実装のcredential helperを作らない。exit
  code非0は`{ok:false,code,safeMessage}`。 操作前に既存workspaceの`.git/config`、worktree/submodule configをRF-03D scrubberでinventoryし、userinfo remote、extraHeader、credential helper、禁止host/schemeがあればnetwork前にquarantineしてtoken
  rotationを要求する。clone/fetch/checkout成功後もremote/configを再検査し、log/exception/argv/env/remoteへtoken 0を確認してから`{ok:true,operation}`を返す。

変更前:

```ts
exec(`git clone https://${token}@${repo} ${target}`);
return { ok: true };
```

変更後:

```ts
await execFileAsync("git", ["clone", "--", credentialFreeUrl, target], {
  env: gitCredentialEnvironment(token),
});
return { ok: true, operation: "clone" };
```
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-08`と`bash scripts/test/run-required-suites.sh RF-08`がexit 0。`services/vexa-bot/core/src/refactor-tests/rf_08.test.ts::uses_the_exact_rf_03d_fixed_helper_and_framed_stdin`
  `::passes_untrusted_values_as_argv` `::never_puts_token_in_url_log_error_env_or_remote` `::pins_verified_ip_while_preserving_original_host_and_sni`
  `::quarantines_existing_credential_remote_extraheader_helper_and_submodule_before_network` `::rejects_target_outside_workspace_root` `::returns_failure_when_git_exits_nonzero` `::supports_valid_clone_fetch_checkout` `V-CORE`
  suite=V-CORE。
- リスク/戻し方: credential helper差異でprivate repo cloneが失敗する。fake gitでargv/envを固定し、実private repo tokenをtestへ使わない。漏洩済みtokenはGit rollbackと別にrotation対象として報告する。失敗時はR0。
- 依存: RF-03B, RF-03D, RF-06B, RF-00C
- コミット: `RF-08 execute browser git commands without a shell`

### RF-09A Browser保存の相関session channelをRedis互換のまま先行配備
- 対象: `services/meeting-api/meeting_api/meetings.py:1307-1334` `services/vexa-bot/core/src/browser-session.ts:150-277` `services/api-gateway/main.py:2363-2386` `services/runtime-api/profiles.yaml:58-79`
  `services/meeting-api/meeting_api/bot_event_broker.py:1-末尾`（RF-06C2作成物） `services/meeting-api/meeting_api/session_capability_registry.py:1-末尾`（RF-06C2作成物） 新規 `services/meeting-api/tests/test_browser_session_save.py:1-末尾` 新規
  `services/vexa-bot/core/src/browser-session-save.test.ts:1-末尾`
- 問題: Redis Pub/Subはat-most-onceで、publish後subscribeすると高速応答を失う。共有`done`に相関IDがなく並列要求を取り違える。新WSへの移行とRedis credential除去を同時に行うと旧Browser Sessionをdrainできない。
- 変更: RF-06Bのbrowser-storage capabilityを流用せず、別の`aud=browser-session-control`
  capabilityを発行する。claimはuser/meeting/session/container、`operations=["save_storage","chat.send","command.receive"]`、iat/nbf/exp/jtiをbindし、callback/storage/event tokenを相互利用できない。 Browser SessionはMeeting event
  brokerへoutboundで1本の認証済みWSを確立する。Brokerはconnection registryをclaimのsession/containerへbindし、同一sessionの重複接続は新connectionを拒否する。client requestからbroker接続先、Redis key/channel、任意operationを指定できない。 Meeting
  APIは`request_id`ごとのwaiterをregistryへ登録してから同じsession WSへcommandを送信し、同一`request_id`の `{request_id,ok,code,safe_message}`だけを受理する。timeout/cancel/WS close時はregistryから削除する。unknown/duplicate/別session responseは他requestを完了させない。 Coreはnew
  WSでstructured responseを返す。new WSがある場合はRedisへ二重publishせずnewだけを使い、WS未提供の旧sessionだけ既存plain Redis responseをこのcommit中のfallbackとして維持する。Broker/Meeting→Core image→新Browser sessionの順で配備し、Redis credential/ACL userはRF-09Bまで残す。
  browser-sessionのchat/commandもRF-06C2のsemantic brokerへ移し、`MeetingChatService`へraw Redis URLを渡さない。`chat_send`成功通知をcommand channelへ自己publishする現挙動はRF-00C characterizationでconsumer
  0が証明された場合に削除し、consumerが1件でもあれば`voice_event.write`の`chat.sent`へ固定変換する。任意channelを残さない。 Gateway timeoutをMeeting timeoutより15秒長くする。new Browser SessionはWSを優先するが、旧instance rollback用にBrowser profileのRedis
  credentialと`browser-session-legacy` ACL userをこのcommitでは削除しない。
- 判断固定: 順序: correlation ID生成 → response channel subscribe確認 → request publish → 同ID response待機 → finally unsubscribe。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-09A`と`bash scripts/test/run-required-suites.sh RF-09A`がexit
  0。`services/meeting-api/tests/test_browser_session_save.py::{test_registers_request_before_sending_command,test_two_concurrent_requests_accept_only_own_response,test_timeout_cancel_and_disconnect_remove_waiter,test_gateway_timeout_exceeds_meeting_timeout,test_wrong_session_duplicate_and_unknown_response_never_complete_request}`
  `services/vexa-bot/core/src/browser-session-save.test.ts::{supports_correlated_session_channel,returns_structured_git_failure}` 100並列fixtureで誤配送/重複/欠落0、timeout/close後waiter・WS registry 0。 new WS fixtureではRedis publish/subscribe
  0。WSなしlegacy fixtureだけRedis path成功し、`browser-session-legacy` counter exact 1。 `V-MEETING`, `V-BACKEND`, `V-CORE`, `V-OPS` suite=V-BACKEND,V-CORE,V-MEETING,V-OPS。
- リスク/戻し方: deploy順の不一致とWS切断で保存が止まる。Broker/Meeting→Core image→新session切替の順にする。このcompatibility commit内だけ旧instanceへ戻せる。失敗branchを保持しRF-08のSHAから再実行する。 失敗時はR1。
- 依存: RF-08, RF-06C2
- コミット: `RF-09A deploy correlated browser session commands with redis compatibility`

### RF-09B Browser legacy Redis transportとcredentialをdrain後に除去
- 対象: `services/meeting-api/meeting_api/meetings.py:1-末尾` `services/meeting-api/meeting_api/bot_event_broker.py:1-末尾`（RF-06C2作成物、RF-09A変更済み） `services/meeting-api/meeting_api/session_capability_registry.py:1-末尾`（RF-06C2作成物）
  RF-09A後の `services/vexa-bot/core/src/browser-session.ts:1-末尾` RF-09A後の `services/runtime-api/profiles.yaml:1-末尾` RF-06C1後の `deploy/{compose,lite,helm}/**:1-末尾` にあるRedis ACL/NetworkPolicy RF-06I2後の
  `services/{api-gateway,dashboard,agent-api}/**:1-末尾` にあるlegacy VNC/CDP route/query verifier。Meeting API側は上記3 exact fileだけ 新規 `tests3/unit/refactor/test_rf_09b.py:1-末尾`
- 問題: RF-09Aはold session互換のRedis transport/credentialを残すため、Browser侵害からRedisへの直接到達がまだ可能。
- 変更: OP-09-DRAINを検証後、Coreのplain Redis fallback、Browser profile/env/BOT_CONFIGの`REDIS_URL`/credential、`browser-session-legacy` ACL userを削除する。 Gateway/Dashboard/Meetingの旧`/b/{token}` routeとAgent/Browser CDP `?api_key=`発行・検証・URL
  assemblyを削除し、owner/profile namespaced `/b/{session_handle}`+preauthorized one-time access identityだけを残す。legacy route/queryは404/401かつowner lookup/broker/relay call 0、URL/history/Referer/access logにAPI key/private key 0。
  Kubernetes NetworkPolicyを全`runtime.managed=true` PodからRedis ingress denyへ閉じ、Docker session networkからRedisを外す。Meeting/Runtime/Gateway等のservice principalだけinfra networkへ残す。 new WS
  unavailable時はRedisへfallbackせず、save/chat/commandを安全な503/connection errorで停止する。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-09B`と`bash scripts/test/run-required-suites.sh RF-09B`がexit
  0。`tests3/unit/refactor/test_rf_09b.py::{test_browser_legacy_acl_user_and_profile_secret_are_absent,test_legacy_vnc_token_route_and_cdp_query_token_are_absent,test_only_profile_aware_session_handle_and_preauthorized_access_identity_remain,test_all_managed_workloads_are_denied_direct_redis_ingress,test_ws_unavailable_never_falls_back_to_redis,test_service_principals_keep_required_infra_access}`
  Browser/Meeting/Agent image内から既知Redis DNS/IPへ`PING/AUTH/GET/XADD/PUBLISH/SUBSCRIBE`はdenied/NOAUTH、side effect 0。Meeting service broker経由だけ成功。 generated Meeting Bot/Browser/Agent env/BOT_CONFIG/inspect/logのRedis URL/password 0。
  `rg -n 'createClient|REDIS_URL|redisUrl|browser_session:' services/vexa-bot/core/src/browser-session.ts services/runtime-api/profiles.yaml` はnegative fixture以外0。`rg -n '/b/\{token\}|api_key=.*cdp|cdp.*api_key'
  services/api-gateway services/dashboard services/agent-api services/meeting-api`はnegative test以外0。 `V-MEETING`, `V-BACKEND`, `V-CORE`, `V-OPS`。 suite=V-BACKEND,V-CORE,V-MEETING,V-OPS。
- リスク/戻し方: old sessionが残っていれば保存が止まる。OP-09-DRAINなしで開始しない。Redis credentialを新commitで戻さずRF-09A componentへrollbackしてtask未完とする。 失敗時はR2。
- 依存: RF-09A, RF-06I2, OP-09-DRAIN
- コミット: `RF-09B remove browser redis access after session drain`

### RF-10 Meeting URL parserの単一契約化
- 対象: `services/meeting-api/meeting_api/schemas.py:442-536,748-758` `services/mcp/main.py:231-360` `services/telegram-bot/bot.py:646-691` 新規 `libs/meeting-contracts/pyproject.toml:1-末尾` 新規
  `libs/meeting-contracts/meeting_contracts/{__init__,url}.py:1-末尾` 新規 `libs/meeting-contracts/tests/test_meeting_url_contract.py:1-末尾` `services/{meeting-api,mcp,telegram-bot}/Dockerfile:1-末尾` `deploy/lite/Dockerfile.lite:1-末尾`
  重複test `services/mcp/test_parse_meeting_url.py:1-末尾` と `services/mcp/tests/test_parse_meeting_url.py:1-末尾`
- 問題: parserが3実装へ分岐し、TelegramはTeamsを `microsoft_teams` + full URLで送りMeeting APIの `teams` + native ID契約に違反する。
- 変更: install可能なpure package `libs/meeting-contracts`を作り、`ParsedMeetingUrl(platform, native_meeting_id, normalized_url, original_url, passcode, teams_base_host, warnings)` を実装する。 Google standard/nickname/lookup、Teams
  personal/enterprise/deep/msteams/legacy、Zoom j/w/wc/events/myを共有parameter fixtureへ置く。 Meeting API/MCP/Telegramは共有parserを呼ぶ薄いadapterにし、旧import名はre-exportする。 Telegramは `platform="teams"` と抽出済みnative IDをMeetingCreateへ送る。
  MeetingCreateへは既存のpasscode、Teams base host、元meeting URLを落とさず渡す。MCPのwarning文面/条件も現在値をfixture化し、共有parserの`warnings`から同じresponseを作る。 `services/mcp/test_parse_meeting_url.py`のcaseをcanonical
  `services/mcp/tests/test_parse_meeting_url.py`と共有fixtureへ移し、旧fileを削除する。case削減は禁止。 RF-00E bootstrapへeditable installを追加し、Meeting/MCP/Telegram/Lite
  imageが同一commitでpackageをCOPY/installできるようDockerfileを更新する。sourceだけ先行してimport不能な中間commitを作らない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-10`と`bash scripts/test/run-required-suites.sh RF-10`がexit 0。`libs/meeting-contracts/tests/test_meeting_url_contract.py::test_provider_url_matrix`
  `::test_rejects_lookalike_hosts_and_invalid_ids` `::test_preserves_original_url_passcode_teams_host_and_warnings` `services/telegram-bot/tests/test_meeting_url.py::test_teams_request_matches_meeting_create_contract`
  `services/mcp/tests/test_parse_meeting_url.py::test_canonical_cases_include_legacy_file_without_loss` exact local Docker commands: `docker build -f services/meeting-api/Dockerfile -t rf10-meeting .` `docker build -f
  services/mcp/Dockerfile -t rf10-mcp .` `docker build -f services/telegram-bot/Dockerfile -t rf10-telegram .` `docker build -f deploy/lite/Dockerfile.lite -t rf10-lite .` 各imageへ `docker run --rm <image> python -c 'from
  meeting_contracts.url import ParsedMeetingUrl'` を実行しexit 0。 `V-MEETING`, `V-INTEGRATIONS`。 suite=V-INTEGRATIONS,V-MEETING。
- リスク/戻し方: 稀なURL variantのnormalization差、Docker contextへのpackage COPY漏れ。共有fixtureに現行全caseを先に移し、fixture差0と全clean image buildを確認する。Telegram Teamsの422修正だけが意図したbehavior差。失敗branchを保持し、前SHAから再実行する。 失敗時はR0。
- 依存: RF-00B
- コミット: `RF-10 share one meeting URL contract`

### 正しさ・非同期競合・状態管理

### RF-11 callback種別間の終端意味を一致させる
- 対象: `services/meeting-api/meeting_api/callbacks.py:115-124,364-396,887-912`
- 問題: `exit_code == 0` のexit callbackは明示的な失敗理由を無視して`COMPLETED`にし、status callbackは同じ理由を`FAILED`にする。callback到着種別で同一会議の最終状態が変わる。
- 変更: 失敗理由集合を1か所へ集約する。 純粋関数 `classify_terminal_signal(exit_code, completion_reason, failure_stage)` を作り、exit/status callbackの両方が呼ぶ。 優先順位は `明示的失敗理由 > 非0 exit code > 正常完了理由`。したがって `exit_code=0 + awaiting_admission_rejected`
  は`FAILED`。 DB commit、publish、post-meeting enqueueの順序は変更しない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-11`と`bash scripts/test/run-required-suites.sh RF-11`がexit 0。RF-00Bのstrict xfail
  `services/meeting-api/tests/test_lifecycle_characterization.py::test_terminal_matrix_snapshot[zero-exit-explicit-failure]` だけをmarker削除して通常passへ変更し、RF-00B matrix entryは変更しない。
  `services/meeting-api/tests/test_callbacks.py::{test_exit_and_status_change_equivalent_for_every_completion_reason,test_duplicate_terminal_callback_is_idempotent}` `V-MEETING` suite=V-MEETING。
- リスク/戻し方: これまでcompleted扱いだった一部履歴が今後failedになる。過去行はmigrationしない。unexpected reasonの期待値が不明なら追加推測せず中断。 失敗時はR0。
- 依存: RF-00B
- コミット: `RF-11 align terminal callback semantics`

### RF-12 transcript検索をリテラル一致へ固定
- 対象: `services/dashboard/src/components/transcript/transcript-segment.tsx:57-69` `services/dashboard/src/components/transcript/transcript-viewer.tsx:86` の既存escape実装 新規 `services/dashboard/src/lib/text-search.ts:1-末尾` 新規
  `services/dashboard/tests/refactor/rf_12.test.ts:1-末尾`
- 問題: 入力をそのまま`RegExp`へ渡すため、`[`、`(`、`\`等で例外になり、`.`等がワイルドカードになる。
- 変更: 共通pure helper `escapeRegExpLiteral` と `splitByLiteralQuery(text, query)` を `src/lib/text-search.ts` に置く。空queryは元textを1要素で返す。Transcript segmentと既存chat highlightの双方を同じhelperへ切り替える。大文字小文字非区別の現行仕様を維持する。

変更前:

```ts
text.split(new RegExp(`(${query})`, "gi"))
```

変更後:

```ts
splitByLiteralQuery(text, query)
```
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-12`と`bash scripts/test/run-required-suites.sh RF-12`がexit 0。`test_text_search.test.ts::treats_bracket_paren_backslash_and_dot_as_literals`
  `::returns_original_text_for_empty_query` `::preserves_case_insensitive_highlight` Browser E2Eで `[`、`(`、`\`、`.` を順に検索しconsole error 0。 `V-DASH` suite=V-DASH。
- リスク/戻し方: 正規表現検索を期待する利用者がいた可能性。ただしUIはregex modeを示していないためリテラルを契約とする。 失敗時はR4。
- 依存: RF-00C
- コミット: `RF-12 make transcript search literal-safe`

### RF-13 transcript dedupの直前候補をspeaker/stream単位へ分離
- 対象: `packages/transcript-rendering/src/dedup.ts:27-142` `packages/transcript-rendering/src/dedup.test.ts:131-141`
- 問題: 既存のoverlap heuristicが入力全体の「最後にacceptした1件」だけを比較するため、speaker A/B/Aの最後のAを最初のAと比較できない。既存identity/key契約を作り直す必要はない。
- 変更: 現在のtext/time overlap判定、許容幅、pending/confirmed処理、`identity.ts`のsegment keyは1文字も変えない。 `dedup.ts` private `heuristicScopeKey`を追加し、meeting=`meeting_id ?? meetingInstanceId ?? "unknown"`、stream=`track_id ?? speaker_track_id ??
  speakerTrackId ?? speakerSessionUid ?? session_uid ?? "unknown"`、speaker=`speaker ?? ""` の順に固定する。`identity.ts`のpublic export/APIは変えない。 最後にacceptした出力indexを `heuristicScopeKey`
  ごとの`Map`へ保持する。既存heuristicがreplaceを返した場合は同じindexを維持、keep-both時だけ新indexへ更新、drop時はMapを更新しない。 新候補は同じscopeの直前accept済みsegmentだけと既存heuristicで比較する。異speaker、異stream/sessionの同文発話は比較対象にせず保持する。 IDのないsegmentへ新しいrounded stable
  keyを発明しない。scopeに必要なidentityが欠ける場合は既存fallbackを使い、そのfallbackをtestへ固定する。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-13`と`bash scripts/test/run-required-suites.sh RF-13`がexit 0。`packages/transcript-rendering/src/dedup.test.ts::removes_overlapping_interleaved_a_b_a_within_same_stream`
  `::keeps_same_text_from_different_speakers` `::keeps_identical_text_from_different_streams_or_sessions` `::keeps_repeated_utterance_at_distinct_non_overlapping_times` `::preserves_existing_pending_confirmed_behavior`
  `V-TRANSCRIPT`, `V-DASH` suite=V-DASH,V-TRANSCRIPT。
- リスク/戻し方: scope keyが粗いと正当な反復発話を消す。新しいidentity設計を入れず既存identity helperを使い、cross-speaker/cross-session fixtureが1件でも減ったら中断。失敗branchを保持し、前SHAから再実行する。 失敗時はR4。
- 依存: RF-00C
- コミット: `RF-13 scope transcript overlap deduplication by speaker and stream`

### RF-14 複数sessionを絶対timelineへ正規化
- 対象: `packages/transcript-rendering/src/manager.ts:61-67` `packages/transcript-rendering/src/dedup.ts:227-251` 新規 `packages/transcript-rendering/src/timeline.ts:1-末尾` `packages/transcript-rendering/src/index.ts:1-末尾`
  `services/dashboard/src/app/meetings/[id]/page.tsx:318-351`
- 問題: segmentには既に`absolute_start_time`がありPageもsession wallclockを導出するが、manager finalizeの末尾が再び相対`start_time`でsortし、再接続sessionの0秒発話を会議冒頭へ戻す。
- 変更: `packages/transcript-rendering`へpure `sortByAbsoluteTimeline`を追加し、primary keyを既存`absolute_start_time`とする。 同値tie-breakは既存session UID、segment ID、元入力indexの順で決定し、同一入力に常に同一順を返す。 `manager.finalize`、dedup後の最終整列、Dashboard
  live-store/viewerのcross-session表示をこのhelperへ切り替える。最終段で`sortByStartTime`を再適用しない。 相対`start_time`/`end_time`はmedia seek用として一切変更しない。`SessionAnchor`、legacy anchor推定、API field/schemaを新設しない。 既存`sortByStartTime`
  exportは同一session用途の互換APIとして残し、この項目で削除・意味変更しない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-14`と`bash scripts/test/run-required-suites.sh RF-14`がexit 0。`packages/transcript-rendering/src/manager.test.ts::finalize_keeps_reconnected_session_after_earlier_absolute_session`
  `packages/transcript-rendering/src/dedup.test.ts::sorts_cross_session_segments_by_absolute_start_time` `::preserves_relative_start_and_end_for_playback` `::uses_session_segment_and_input_order_as_deterministic_tie_break`
  `V-TRANSCRIPT`, `V-DASH` suite=V-DASH,V-TRANSCRIPT。
- リスク/戻し方: `absolute_start_time`欠落fixtureの順序差。欠落時は既存入力順fallbackを明示し、相対timeへcross-session fallbackしない。API/model新設はせず、失敗時は前SHAから再実行。 失敗時はR4。
- 依存: RF-13
- コミット: `RF-14 order transcript sessions on one absolute timeline`

### RF-15 Meeting切替requestへgeneration guardを付ける
- 対象: `services/dashboard/src/stores/meetings-store.ts:347-379,435-456,510-538` `services/dashboard/src/app/meetings/[id]/page.tsx:805-815`
- 問題: Meeting Aの遅いtranscript/chat/artifact responseが、A→B切替後にBのstateへ書き込む。
- 変更: storeに `{meetingId, generation}` のrequest tokenを発行する。 list/detailだけでなくtranscript、chat、artifact、recordingsの全async write前に現在token一致を確認する。 meeting切替・logout・store resetでgenerationをincrementし、AbortControllerをcancelする。 stale
  responseはerror stateも更新しない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-15`と`bash scripts/test/run-required-suites.sh RF-15`がexit 0。`test_meetings_store_requests.test.ts::late_transcript_from_a_cannot_overwrite_b`
  `::late_chat_and_artifact_from_a_are_ignored` `::switch_aborts_all_inflight_meeting_requests` `::current_request_updates_state_normally` `V-DASH` suite=V-DASH。
- リスク/戻し方: 正当なresponseまで捨てる可能性。token発行をmeeting-scoped action入口へ限定する。 失敗時はR4。
- 依存: RF-00C
- コミット: `RF-15 guard meeting-scoped async writes by generation`

### RF-16 再文字起こしpollingを単一controllerへ集約
- 対象: `services/dashboard/src/components/transcript/transcript-viewer.tsx:406-443,1050-1094` `services/dashboard/src/app/meetings/[id]/page.tsx:882-885` 新規 `services/dashboard/src/hooks/use-retranscription-job.ts:1-末尾`
- 問題: ViewerとPageが同じstatusをpollし、Viewerの長時間loopに明確なcleanupがない。
- 変更: `useRetranscriptionJob(meetingId)` を唯一のownerにする。 controllerは `idle|starting|polling|succeeded|failed|timed_out|cancelled` を持つ。poll間隔は現行2,500ms、上限は現行`1,050`回、すなわち43分45秒。transport
  errorはそのloopを`failed`で停止し、既存toastへ同じmessageを渡す。 同時requestは1、unmount/meeting切替でAbort。再mount時にserver statusが`queued|running`なら新しい最大1,050回のloopを開始する。 Viewer/Pageは同じhook結果をprops/contextで共有し、独自`setInterval`/loopを削除する。 terminal
  statusで必ずtimer 0。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-16`と`bash scripts/test/run-required-suites.sh RF-16`がexit 0。`test_retranscription_controller.test.ts::starts_only_one_poll_loop` `::cancels_on_meeting_change_and_unmount`
  `::stops_on_terminal_status` `::times_out_after_configured_budget` fake timer実行後pending timer 0。 `V-DASH` suite=V-DASH。
- リスク/戻し方: loop owner変更でtoast/refresh時点がずれる可能性。2,500ms、1,050回、terminal mapping、messageをcharacterization testで固定する。失敗時はR4。
- 依存: RF-15
- コミット: `RF-16 own retranscription polling in one controller`

### RF-17 post-meeting pollingをsingle-flight化
- 対象: `services/dashboard/src/app/meetings/[id]/page.tsx:865-945` 新規 `services/dashboard/src/hooks/use-post-meeting-progress.ts:1-末尾`
- 問題: 複数effect/intervalがoverlapし、前回fetch完了前に次回fetchを起動し得る。
- 変更: `usePostMeetingProgress(meetingId, initialStatus)` にownerを集約する。 request中は次tickを起動しない。完了後に`setTimeout`で次回をscheduleする。 status terminal、meeting切替、unmountでcancel。 recordings、artifacts、meeting detailのrefresh順を固定し、同一generation
  guardを使う。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-17`と`bash scripts/test/run-required-suites.sh RF-17`がexit 0。`test_post_meeting_polling.test.ts::never_overlaps_requests` `::refreshes_recording_artifact_and_meeting_in_order`
  `::stops_after_terminal_or_dispose` `::ignores_late_response_after_meeting_switch` `V-DASH` suite=V-DASH。
- リスク/戻し方: 更新間隔が伸びる。現行interval値をそのまま使用し、schedule方式だけ変更。 失敗時はR4。
- 依存: RF-15, RF-16
- コミット: `RF-17 make post-meeting polling single-flight`

### RF-18 live follow scrollをtranscript container内へ限定
- 対象: `services/dashboard/src/components/transcript/transcript-viewer.tsx:650-681` `services/dashboard/src/lib/transcript-scroll.ts:1-51` `services/dashboard/tests/test_transcript_scroll.test.ts:7-70`
- 問題: live followだけ`scrollIntoView()`を使い、外側pageまで移動させ得る。
- 変更: `scrollTranscriptContainerToBottom(container)` を追加し、`container.scrollTo({top: max(0, scrollHeight-clientHeight)})` のみ使う。sentinelへの`scrollIntoView`を削除する。userがfollowを解除した状態では呼ばない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-18`と`bash scripts/test/run-required-suites.sh RF-18`がexit
  0。`services/dashboard/tests/test_transcript_scroll.test.ts::{scrolls_only_transcript_container_to_bottom,clamps_bottom_when_content_is_short,does_not_scroll_when_follow_is_disabled}` E2Eでouter page `scrollY`不変、inner scrollだけ増加。
  `V-DASH` suite=V-DASH。
- リスク/戻し方: virtualized containerならscroll target差。現DOM fixtureでcontainer refを固定。 失敗時はR4。
- 依存: RF-00C
- コミット: `RF-18 keep live transcript scrolling inside its container`

### RF-19 WebSocket transcript sessionを1実装へ統合
- 対象: `services/dashboard/src/hooks/use-live-transcripts.ts:160-270` `services/dashboard/src/hooks/use-vexa-websocket.ts:56-168,285-322` `services/dashboard/src/stores/live-store.ts:1-88` `services/dashboard/next.config.ts:48-59`
  `services/dashboard/src/app/docs/ws/page.tsx:1-末尾` `services/dashboard/src/app/docs/ws/subscribe/page.tsx:1-末尾` `services/dashboard/src/app/docs/auth/page.tsx:1-末尾`
  `services/dashboard/src/app/docs/cookbook/track-meeting-status/page.tsx:1-末尾` `services/dashboard/src/app/docs/cookbook/get-transcripts/page.tsx:1-末尾` `packages/transcript-rendering/src/manager.ts:1-末尾`
  `services/api-gateway/main.py:2487-2665` `services/api-gateway/tests/{test_websocket.py,test_gate_g5_websocket.py}:1-末尾` `services/wake-orchestrator/app/clients.py:116-127,744-751`
  `services/wake-orchestrator/tests/test_clients.py:430-490` `tests3/checks/run:1390-1420` 新規 `services/dashboard/src/lib/live-transcript-session.ts:1-末尾`
- 問題: 会議詳細と`/join`でconfirmed/pending、token、retry、cleanupの意味が異なる。さらにGateway/Wake/docsはraw API keyをWebSocket URL queryへ載せ、URL履歴・proxy access log・例外へcredentialが残り得る。
- 変更: transport/controller `LiveTranscriptSession` を作り、URL解決、接続、parse、backoff、disposeだけを担当させる。 両画面が`TranscriptManager.handleMessage()`へ同じmessageを渡す。 pending空tickでdraftを消す。 browser credentialはstore、Cookie API、runtime
  configのどこからもJavaScriptで読まない。常にsame-origin/base-pathのqueryless `/ws`へ接続し、Next rewriteが既存HttpOnly `vexa-token` cookieとexact `Origin`をGatewayへ渡す。Gatewayはproduction allow-listに完全一致する`Origin`、Secure/HttpOnly/SameSite
  cookie、RF-05Aと同じtoken resolverによるactive subject/scope解決が全て成功した場合だけhandshakeをacceptする。Origin欠落はnon-browser header modeだけで許し、mismatch/null Origin、cookie+header subject不一致、無効tokenはRedis subscribe/task生成前に拒否する。 non-browser
  clientはqueryless `/ws`と`Authorization: Bearer <API key>`だけを使う。Wake Orchestrator、integration test、CLI/check、Python/Nodeのdocs例を同じcommitでheader modeへ移す。browser WebSocket APIは任意headerを設定できないため、public docsのbrowser JavaScript例はraw
  keyを埋め込まず、「認証済みsame-origin Dashboard `/ws`を使う」または「server-side bridgeでAuthorizationを付ける」例へ置換する。 Gatewayは`api_key`,`token`,`access_token` queryを値の有無にかかわらずhandshake前400/4400で拒否し、`X-API-Key`の旧WebSocket
  fallbackも削除する。credential候補がquery/subprotocol/subscribe frameへあればresolver/Redis/downstream call 0。認証subjectはhandshake時に1回だけimmutable化し、subscribe payloadのuser IDを受けず、RF-05A trusted identityでTranscription authorizationへ渡す。invalid
  tokenをacceptして後段へ委譲する現挙動を残さない。 WebSocket URL、access/error log、exception、close reason、metrics labelへraw credential 0。logは`transport=cookie|authorization`、route、status、nonsecret subject hashだけ。`rg`で`/ws?api_key=`とWebSocket
  URLを組み立てる`api_key=`をproduction/docs/checkから0にする。 reconnectは指数backoff + jitter、最大試行回数を既存UXに合わせて定数化し、dispose後は0回。 旧hooksは薄いadapterとして1段階残し、callsiteを同一commitで移行する。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-19`と`bash scripts/test/run-required-suites.sh RF-19`がexit 0。`test_live_transcript_session.test.ts::replaces_pending_for_same_speaker` `::empty_pending_tick_clears_draft`
  `::never_reads_browser_credentials_or_puts_tokens_in_url_subprotocol_or_subscribe_frame` `::does_not_reconnect_after_cleanup` `::routes_join_and_meeting_messages_through_same_manager`
  `services/api-gateway/tests/test_websocket.py::{test_same_origin_secure_http_only_cookie_authenticates_before_accept,test_authorization_bearer_authenticates_non_browser_client,test_query_x_api_key_subprotocol_and_frame_credentials_are_rejected_before_resolver_redis_or_downstream,test_cookie_header_subject_mismatch_and_wrong_origin_fail_closed,test_subscribe_uses_handshake_subject_and_trusted_identity_only}`
  `services/wake-orchestrator/tests/test_clients.py::test_vexa_websocket_uses_queryless_url_and_authorization_header_without_log_leak` `rg -n '/ws\?api_key=|api_key=.*[/]ws|[/]ws.*api_key=' services packages tests3 --glob
  '!**/fixtures/**'`は0件。 `V-TRANSCRIPT`, `V-DASH`, `V-BACKEND`, `V-INTEGRATIONS`。 suite=V-BACKEND,V-DASH,V-INTEGRATIONS,V-TRANSCRIPT。
- リスク/戻し方: URL queryで接続する未管理clientは接続不能になる。実装前にrepo内client/docsを上記`rg`で全inventoryし同commitで移行する。外部利用者にはrelease noteでAuthorization headerまたはsame-origin bridgeを先行告知し、raw query token fallbackを復活させない。失敗branchを保持しRF-18直後の合格SHAから再実行する。
  失敗時はR4。
- 依存: RF-13, RF-14, RF-15, RF-05A
- コミット: `RF-19 unify live transcript websocket semantics`

### RF-20 Browser Session UIのsame-origin経路と状態分類を統一
- 対象: `services/dashboard/README.md:74-85` `services/dashboard/src/app/meetings/[id]/page.tsx:124-134,1150-1177,1890-1939` `services/dashboard/src/components/meetings/browser-session-view.tsx:61-103`
  `services/dashboard/src/hooks/use-runtime-config.ts:8-110` `tests3/tests/static/dashboard-config-ssot.sh:48-64` `tests3/tests/dashboard-browser-view.sh:2-9` `tests3/tests/dashboard-browser-view.mjs:14-17,69-78`
  `tests3/test-registry.yaml:653-656,709-712` 新規 `services/dashboard/src/lib/browser-session-view-model.ts:1-末尾` 新規 `services/dashboard/tests/refactor/rf_20.test.ts:1-末尾`
- 問題: VNC/saveがruntime `apiUrl`へ直接向き、READMEのsame-origin規約と違う。platform/data.mode判定が不統一で、開始中statusを終了表示し得る。
- 変更: `browserSessionRoutes(meeting,runtimeConfig)` をpure helper化し、VNCとmutationは常に `withBasePath("/b/...")`。外部CDP表示だけ`runtimeConfig.publicApiUrl`を使う。`RuntimeConfig`へ`publicApiUrl:string`を追加し、`BrowserSessionView`独自の`GET
  /api/config`とlocal `apiUrl` stateを削除して共有`useRuntimeConfig()`の1 fetchへ統合する。 `isBrowserSessionMeeting = platform==="browser_session" || data.mode==="browser_session"` の1実装に統一し、存在しない`browser` literalは使わない。 statusを
  `starting=requested|awaiting_admission|joining`、`active=active|recording`、`terminal=completed|failed|stopped` に分類。 `services/dashboard/src/lib/browser-session-view-model.ts`へ
  `BrowserSessionViewModel`とbuilderを置き、Pageと既存`browser-session-view.tsx`は同じmodelを使う。この項目では新しいPanel componentを作らない。 tests3の旧「runtime API URLへ向くことを正とする」逆向きassertを、新しいsame-origin契約へ同じcommitで更新し、registryの説明も同期する。test削除やdisableで通さない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-20`と`bash scripts/test/run-required-suites.sh RF-20`がexit 0。`test_browser_session_routes.test.ts::uses_same_origin_for_vnc_and_mutations` `::uses_public_url_only_for_external_cdp`
  `::recognizes_platform_only_browser_session` `::uses_shared_runtime_config_without_component_local_config_fetch` `::classifies_requested_and_awaiting_as_starting` mobile/desktop E2E screenshotで同じstatus文言。 `V-DASH`, `V-OPS`
  suite=V-DASH,V-OPS。
- リスク/戻し方: base path deploymentの二重prefix。`withBasePath`のinput/output fixtureをroot/subpath両方に置く。 失敗時はR4。
- 依存: RF-04A, RF-09B
- コミット: `RF-20 normalize browser session routes and lifecycle UI`

### RF-21 AudioPlayerのfragment/retry状態を有限化
- 対象: `services/dashboard/src/components/recording/audio-player.tsx:64-110,112-179,237-247`
- 問題: fragment数減少時にindexが範囲外となり、audio errorが1.5秒ごと無制限retryし、play rejectionを握り潰す。
- 変更: pure `normalizeFragmentIndex(index, length)` と `nextMediaRetry(attempt)` を追加。 source list変更時にindex clamp、source identity変更時にretry budget reset。 自動retry最大3回、1.5/3/6秒。以降は明示エラーとmanual retry。 `play()`
  rejectionをstateへ反映し、unmount後state更新を防ぐ。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-21`と`bash scripts/test/run-required-suites.sh RF-21`がexit 0。`test_audio_playback_logic.test.ts::clamps_index_after_fragment_list_shrinks`
  `::stops_automatic_retry_after_three_failures` `::resets_retry_budget_for_new_source` `::surfaces_play_rejection` `V-DASH` suite=V-DASH。
- リスク/戻し方: transient障害から自動復帰しなくなる可能性。manual retryを必須で残す。 失敗時はR4。
- 依存: RF-15
- コミット: `RF-21 bound audio playback retries`

### RF-22 VideoPlayerのstate/imperative APIを整合させる
- 対象: `services/dashboard/src/components/recording/video-player.tsx:18-36,81-88`
- 問題: setter宣言前にimperative handleから参照しlint errorとなり、play rejectionもUIへ届かない。
- 変更: state宣言をimperative handleより前へ移す。`play/pause/seek`を同じcontrollerへ集約し、Promise rejectionを`playbackError`へ保存。source変更でerrorをresetし、unmount済みrefを操作しない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-22`と`bash scripts/test/run-required-suites.sh RF-22`がexit 0。`test_video_playback_logic.test.ts::imperative_play_surfaces_rejection` `::source_change_resets_error`
  `::dispose_prevents_late_state_update` 対象fileのlint error 0。 `V-DASH` suite=V-DASH。
- リスク/戻し方: imperative refの公開shapeを変えない。type snapshotで固定。 失敗時はR4。
- 依存: RF-00C
- コミット: `RF-22 make video playback state deterministic`

### RF-23 Decisions SSEのretry lifecycleを閉じる
- 対象: `services/dashboard/src/components/decisions/decisions-panel.tsx:364-477`
- 問題: reconnect timeoutをcleanupせず、unmount後にcaptured stateで再接続し得る。URL dependencyも不足。
- 変更: `DecisionSseController`へEventSource、retry timer、active flagを所有させる。`dispose()`はEventSource closeとtimer clearを必ず行う。URL変更時は旧controllerをdispose後に新規作成。指数backoffは最大30秒。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-23`と`bash scripts/test/run-required-suites.sh RF-23`がexit 0。`test_decision_sse_controller.test.ts::cancels_retry_on_dispose` `::reconnects_once_after_error`
  `::uses_new_url_after_runtime_config_change` `::never_opens_after_dispose` `V-DASH` suite=V-DASH。
- リスク/戻し方: reconnect頻度の差。fake timerで回数を固定。 失敗時はR4。
- 依存: RF-04A
- コミット: `RF-23 close decision SSE retry lifecycle`

### RF-24 Runtime schedulerを原子的かつ整合的にする
- 対象: `services/runtime-api/runtime_api/scheduler.py:92-155,290-315`
- 問題: `GET -> SET -> ZADD`が非原子的。SET後crashでidempotency keyが存在しないjobを指せる。retry `execute_at`未更新、pending jobをhistoryへ保存、lookup順も不整合。
- 変更: RF-06C1のACLが`EVAL/EVALSHA`を禁止するためLuaを使わない。exact `scheduler:idem:<key>`を`WATCH`→`GET`し、既存job IDがあれば`executing/pending/history`のtyped repositoryで実在を確認して返す。不存在ならserver-generated job ID/payloadをmemoryで作り、`MULTI`内でjob payload
  `SET`、`ZADD scheduler:jobs score job_id`、idempotency `SET EX`をqueueして`EXEC`する。WatchError/EXEC nullはbounded jitter付き最大5回、毎回全keyを再読する。超過時503でpartial write 0。 idempotency keyがmissing jobを指す、jobはあるが`ZSCORE scheduler:jobs`とpayload
  `execute_at`が不一致、queue memberだけ orphanの各状態を検出したら新jobを黙って作らず`SchedulerConsistencyError`としてside effect 0で隔離する。通常requestはrepairせずstructured errorを返す。RF-24ではrepair CLIを実装しない。既存orphan修復は別taskとする。
  retry時は `execute_at=next_time` をpayloadとsorted-set scoreの両方へ反映。 historyへ保存するのはterminalのみ。 lookup順は `executing -> pending -> history`。 既存Redis key prefixとjob JSON fieldは維持する。
- 判断固定: 原子性: idempotency reserveとjob createを同一WATCH/MULTI/EXEC transactionで行い、terminal historyは一度だけappendする。Luaは使わない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-24`と`bash scripts/test/run-required-suites.sh RF-24`がexit 0。`services/runtime-api/tests/test_scheduler.py::test_concurrent_idempotent_schedule_enqueues_exactly_once`
  `services/runtime-api/tests/test_scheduler.py::test_idempotency_never_points_to_missing_job` `services/runtime-api/tests/test_scheduler.py::test_watch_conflict_retries_with_bounded_jitter_and_no_partial_write`
  `services/runtime-api/tests/test_scheduler.py::test_zscore_payload_and_idempotency_orphans_fail_closed_until_explicit_repair`（fixtureがorphan状態を直接修復した後にだけ再scheduleが成功すること）
  `services/runtime-api/tests/test_scheduler.py::{test_retry_updates_next_execute_at,test_retrying_job_is_not_terminal_history,test_get_job_prefers_live_state_over_history}` RF-00Bのstrict xfail
  `services/runtime-api/tests/test_scheduler_characterization.py::test_job_and_terminal_history_schema_snapshot[non-atomic-retry]` だけをmarker削除して通常passへ変更し、RF-00B matrix entryは変更しない。 `V-BACKEND` suite=V-BACKEND。
- リスク/戻し方: Redis WATCH競合と既存orphan検出でscheduleが503になり得る。使用中Redis versionでACL付きWATCH/MULTI/EXECをintegration fixture実行し、競合時に上限を広げない。orphanを自動削除せず別途repair対象として報告する。失敗時はR0。
- 依存: RF-00B, RF-06C1
- コミット: `RF-24 make runtime scheduling atomic`

### RF-25 Agent container生成をkeyed lock化
- 対象: `services/agent-api/agent_api/container_manager.py:35-40,122-186` `services/agent-api/agent_api/chat.py:161-185`
- 問題: global `_new_container`が並列request間で共有され、同一user/sessionの二重spawnと別requestへのcreated flag漏れがある。
- 変更: `(user_id, session_id)`ごとのasync lockを弱参照または完了時削除するregistryで管理。 `ensure_container()` は `EnsureContainerResult(name, created)` をrequest-localに返す。 global `_new_container` を削除し、chatは戻り値だけを見る。 spawn失敗時はlockを解放し、partial
  mappingを残さない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-25`と`bash scripts/test/run-required-suites.sh RF-25`がexit 0。`test_container_manager.py::test_two_concurrent_ensure_calls_spawn_once` `::test_created_flag_is_request_local`
  `::test_failed_spawn_leaves_no_mapping_or_lock` `V-BACKEND` suite=V-BACKEND。
- リスク/戻し方: lock registry leak。完了後size 0のtestを追加。 失敗時はR0。
- 依存: RF-05A, RF-05B
- コミット: `RF-25 serialize agent container creation per session`

### RF-26 Calendar workerをlifespan管理し、eventをclaimする
- 対象: `services/calendar-service/app/main.py:39-73` `services/calendar-service/app/sync.py:244-308`
- 問題: startup taskを保持/cancelせず、複数workerが同じpending eventを処理でき、eventごとにHTTP clientを作る。
- 変更: FastAPI lifespanでworker taskと共有`httpx.AsyncClient`を作り、shutdownでcancel/await/close。 `schedule_upcoming_bots`は、dueな`pending` eventを `ORDER BY start_time, id FOR UPDATE SKIP LOCKED LIMIT 1` で1件取得し、row
  lockを保持した同一transaction内でMeeting API呼出しと`scheduled|failed`更新を行ってcommitする。次のeventは新transactionで取得する。 process crash/exception時はtransactionをrollbackし、rowは`pending`のまま次回対象にする。HTTP non-2xxは現行どおり`failed`。新status/column/leaseは追加しない。
  共有clientは`sync_loop`から引数で渡し、eventごとの`AsyncClient`生成を削除する。
- 判断固定: row lockを保持した同一transaction内で外部callとstatus更新を行い、成功/失敗statusとclaimを同じcommitで確定する。HTTP中最大30秒のlockを許容する。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-26`と`bash scripts/test/run-required-suites.sh RF-26`がexit 0。`test_sync_worker.py::test_worker_is_cancelled_on_shutdown` `::test_two_workers_claim_event_once`
  `::test_shared_http_client_closed_once` `::test_crash_rolls_back_and_leaves_event_pending` `::test_http_non_2xx_preserves_existing_failed_status` `V-BACKEND` suite=V-BACKEND。
- リスク/戻し方: HTTP呼出し中に最大30秒row lockを保持する。対象は同じeventの重複投入防止に限定し、eventを1件ずつ処理する。PostgreSQL以外のproduction dialectが見つかったら中断し、migrationや別claim方式を推測しない。 失敗時はR0。
- 依存: RF-05A
- コミット: `RF-26 manage calendar sync worker lifecycle`

### RF-27 Wake STTのspeaker stateとtaskを回収する
- 対象: `services/wake-stt/app/service.py:157-171,197-212,270-272`
- 問題: speaker stateを削除せず、transcription taskを追跡しないため長時間稼働でmemory/taskが増える。
- 変更: `SpeakerState.last_ingest_ms`を使い、`state.in_flight == false`、active sessionなし、finalize/fast-command taskなしのstateだけを、最終ingestから `max(60_000, settings.idle_reset_ms * 10)` ms後に削除する。 evictionは60秒ごとのmaintenance taskで行い、設定field
  `WAKE_STT_STATE_TTL_MS` があればその正整数を使う。defaultは上記式。default未満の値は起動時validation errorにして、utterance途中を削除できないようにする。 全create_taskをsetへ登録し、done callbackで削除。 service `close()` で受付停止、task cancel/await、state clear。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-27`と`bash scripts/test/run-required-suites.sh RF-27`がexit 0。`test_service_lifecycle.py::test_idle_speaker_state_is_evicted` `::test_active_speaker_is_not_evicted`
  `::test_close_cancels_inflight_transcription_tasks` `::test_task_registry_returns_to_zero` `V-AUX`。 suite=V-AUX。
- リスク/戻し方: 長い無音後のspeaker continuity。TTLを既存session timeout以上に設定しfixtureで境界確認。 失敗時はR0。
- 依存: RF-00B
- コミット: `RF-27 bound wake STT state and tasks`

### RF-28 Wake Orchestratorのmeeting/cacheを上限付きにする
- 対象: `services/wake-orchestrator/app/main.py:68-103` `services/wake-orchestrator/app/orchestrator.py:115-130,360-461`
- 問題: meeting orchestrator、`_seen_segments`、speaker dedupe mapが無制限に増える。
- 変更: `WakeOrchestrator.last_activity_monotonic`を全messageで更新し、stateが`IDLE`、pending wake/taskなしのinstanceだけを30分無活動後に`close()`してregistryから削除する。tickerは60秒ごとにeviction判定する。 `_seen_segments`は`OrderedDict`で最大5,000 ID、各entry TTL
  30分。read/write時にLRU更新し、上限超過時は最古を削除する。 `_last_wake_by_speaker`は`wake_same_speaker_dedupe_ms <= 0`なら保存しない。正数ならTTLを`max(60秒, dedupe値)`、最大1,000 keyとする。 `WAKE_ORCHESTRATOR_IDLE_TTL_SECONDS` default
  1800、`WAKE_SEEN_SEGMENT_TTL_SECONDS` default 1800、`WAKE_SEEN_SEGMENT_MAX` default 5000としてvalidateし、0/負数は起動時error。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-28`と`bash scripts/test/run-required-suites.sh RF-28`がexit 0。`test_orchestrator_cache.py::test_idle_meeting_orchestrator_is_evicted` `::test_active_meeting_is_retained`
  `::test_seen_segment_cache_is_bounded` `::test_duplicate_inside_window_is_suppressed` `V-AUX`。 suite=V-AUX。
- リスク/戻し方: TTL外の再送を再処理する可能性。既存最大再送時間より長いTTLを選ぶ。 失敗時はR0。
- 依存: RF-00B
- コミット: `RF-28 bound wake orchestrator state`

### RF-29 TTS model loadを排他し、WAV合成をevent loop外へ出す
- 対象: `services/tts-service/main.py:331-349,447-551`
- 問題: 同一voice初回loadが競合し、async endpoint内の同期WAV処理とbytes連結がevent loopを止める。
- 変更: voiceごとのasync lockでdouble-check loadする。 model/providerのthread safetyを実行者判断にしない。同じvoiceのloadとsynthesisは同じper-voice `asyncio.Lock`内で直列化し、異なるvoiceだけ並列を許す。lockを保持したままCPU/同期I/OのWAV合成を`await asyncio.to_thread(...)`で実行する。
  chunkはlist/`bytearray`へ蓄積し最後に1回結合。 request cancel時も共有model cacheを壊さない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-29`と`bash scripts/test/run-required-suites.sh RF-29`がexit 0。`test_tts_concurrency.py::test_concurrent_first_load_only_loads_once`
  `::test_health_remains_responsive_during_wav_synthesis` `::test_same_voice_synthesis_max_concurrency_is_one` `::test_different_voices_can_synthesize_in_parallel` `::test_cancelled_request_does_not_remove_loaded_voice`
  `::test_output_wav_bytes_match_baseline` `V-AUX`。 suite=V-AUX。
- リスク/戻し方: 同一voice throughputが直列化される。これはthread safetyを推測しないための固定trade-offであり、lockを外して最適化しない。health responsiveness、different-voice並列、output byte goldenのどれかが崩れたら中断し前SHAから再実行する。 失敗時はR0。
- 依存: RF-00B
- コミット: `RF-29 isolate TTS loading and synthesis`

### RF-30 Voiceprint model load失敗をhealthへ反映
- 対象: `services/voiceprint-service/main.py:146-172,214-220`
- 問題: model load task失敗後もhealthが永久に`loading`を返し、運用が失敗を判定できない。
- 変更: `ModelLoadState(status="loading|ready|failed", safe_error, task)` を単一sourceにする。done callbackでexceptionを取得し`failed`へ遷移。healthはfailedで503と安全なerror codeを返す。retryは既存の明示reload endpointがなければ追加しない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-30`と`bash scripts/test/run-required-suites.sh RF-30`がexit 0。`test_health.py::test_health_reports_failed_after_model_load_error` `::test_health_reports_ready_after_success`
  `::test_loader_exception_is_retrieved` `::test_health_does_not_expose_secret_or_stack` `V-AUX`。 suite=V-AUX。
- リスク/戻し方: readiness probeが503でrestart loopになる。これは現状の隠れたfailureを可視化する意図した変更で、deployment probeのrestart policyを変更しない。 失敗時はR0。
- 依存: RF-00B
- コミット: `RF-30 expose voiceprint load failure in health`

## 4. Phase 2: tests3・Deploy・Harness（RF-31〜RF-51）

### RF-31 report statusをpass/skip/invalid/failへ正規化
- 対象: `tests3/checks/run:1575-1606,1676-1694` `tests3/lib/{aggregate.py,common.sh,run-matrix.sh}:1-末尾` 新規 `tests3/lib/report_status.py:1-末尾`（status normalizationとreader契約の唯一の実装先）
- 問題: 全step skipとstep 0件がpassになり、「検査済み」と誤解される。
- 変更: 判定を1つのPython helperへ置き、shell runnerはその結果を使う。既存JSON fieldを削除せず、`status` enumを拡張し、aggregateも同じ意味で読む。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-31`と`bash scripts/test/run-required-suites.sh RF-31`がexit 0。`test_report_status.py::test_all_skip_is_skip` `::test_zero_steps_is_invalid` `::test_pass_plus_skip_is_pass`
  `::test_any_failure_is_fail` RF-00Dの該当xfailを通常passへ変更。 `V-OPS` suite=V-OPS。
- リスク/戻し方: downstreamが新statusを知らない。reader/schema/runnerを同一commitで対応し、unknown statusはinvalid。 失敗時はR0。
- 依存: RF-00D
- コミット: `RF-31 make test report status truthful`

### RF-32 active registry script不存在をfatalにする
- 対象: `tests3/test-registry.yaml:1-末尾` `tests3/lib/run-matrix.sh:168-176,209-221` read-only input: `tests3/unit/fixtures/registry-baseline.json:1-末尾`（RF-00D作成物）
- 問題: active登録91件中45件のscriptが存在しないのにmatrixが成功する。
- 変更: registry entryへ `status: active|disabled|retired`、非activeには必須 `reason`、`source_commit`、`review_after` を定義する。 baselineで不存在の45件は、削除commit `a51b952...` で実行体が消えた事実を理由に`disabled`へ明示分類する。機能coverageが代替されたとは書かない。 `active + missing`
  は実行前integrity errorでexit 2。 `disabled/retired`でもreason/source/review_after欠落、期限超過はexit 2。 実行体を復元しない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-32`と`bash scripts/test/run-required-suites.sh RF-32`がexit 0。`test_matrix_contract.py::test_missing_active_script_is_fatal`
  `::test_disabled_script_requires_reason_source_and_review_date` `::test_expired_disabled_entry_is_fatal` `::test_baseline_missing_entries_are_explicitly_classified` matrix summaryにactive/disabled/retired件数が表示される。 `V-OPS`
  suite=V-OPS。
- リスク/戻し方: matrixが即時赤化する。分類漏れを無言で削除せず修正。 失敗時はR0。
- 依存: RF-31
- コミット: `RF-32 fail on missing active test scripts`

### RF-33 aggregateを非空・適用可能性の明示契約へ変更
- 対象: `tests3/lib/aggregate.py:164-213,559-565,596-650` `tests3/Makefile:164-175` 新規 `tests3/gate-applicability.json:1-末尾`
- 問題: report 0件、feature 0件でgateが0。`validate-all`は`report-gate`でなく`report`へ依存する。
- 変更: gateは`reports >= 1`を必須。 feature contractは、現在OSS外へ移した事実を示すversioned `gate-applicability.json` に `applicable:false`, `reason`, `source_commit=2d93eca...`, `owner`, `review_after` がある場合だけnot-applicableを許可する。
  policy欠落/期限超過/feature 0かつapplicableはfail。 `validate-all`を`report-gate`へ接続。 0件をWARNで通すbranchを削除。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-33`と`bash scripts/test/run-required-suites.sh RF-33`がexit 0。`test_aggregate_gate.py::test_gate_fails_without_reports`
  `::test_gate_fails_without_feature_catalog_or_not_applicable_policy` `::test_expired_not_applicable_policy_fails` `::test_minimal_complete_fixture_passes` `test_make_contract.py::test_validate_all_invokes_report_gate` `V-OPS`
  suite=V-OPS。
- リスク/戻し方: 現行all-modeがredになる。RF-31/32後にのみ実施。過去feature sidecarを復元しない。 失敗時はR0。
- 依存: RF-31, RF-32
- コミット: `RF-33 require non-empty applicable test evidence`

### RF-34 smoke stampを入力fingerprintへ結び付ける
- 対象: `tests3/Makefile:39-42,63-73,329-331` runtime output: `tests3/.state/.smoke-passed:1-末尾`
- 問題: source/registry/config変更後も古いstampでsmokeを省略できる。
- 変更: stamp JSONへHEAD、dirty tracked diff hash、registry hash、checks registry hash、runner/config hash、実行command versionを保存。 利用時に全field一致を検証し、1つでも違えばsmoke再実行。 simple timestamp fileは廃止するが、既存stampを移行せずcache missとして扱う。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-34`と`bash scripts/test/run-required-suites.sh RF-34`がexit 0。`test_smoke_stamp.py::test_source_change_invalidates_stamp` `::test_registry_change_invalidates_stamp`
  `::test_unchanged_inputs_reuse_stamp` `::test_legacy_stamp_is_cache_miss` `V-OPS` suite=V-OPS。
- リスク/戻し方: smoke実行回数増加。正しい安全側であり、失敗時も旧stampを復元しない。失敗時はR0。
- 依存: RF-33
- コミット: `RF-34 bind smoke cache to verified inputs`

### RF-35 三つのregistryへ役割とparity検査を与える
- 対象: `tests3/test-registry.yaml:1-793` `tests3/checks/registry.json:1-1581` `tests3/registry.yaml:1-3779` `tests3/Makefile:1-338` `tests3/lib/run-matrix.sh:1-223` `tests3/lib/aggregate.py:1-654` 新規
  `tests3/tools/registry_parity.py:1-末尾` 新規 `tests3/unit/refactor/test_rf_35.py:1-末尾`
- 問題: 三つのregistryのcanonical/derived/legacy区分がなく、`tests3/registry.yaml`は実行経路から参照されていない。
- 変更: `test-registry.yaml`を実行testのcanonical source、`checks/registry.json`をcheck implementation catalog、`registry.yaml`をlegacy metadata sourceと明記する。 parity toolでID重複、orphan、同一IDのmode/tier/path不一致、未消化legacy metadataをJSON出力する。
  この項目ではregistryを削除・統合しない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-35`と`bash scripts/test/run-required-suites.sh RF-35`がexit 0。`test_registry_parity.py::test_duplicate_ids_fail` `::test_orphan_check_fails`
  `::test_conflicting_execution_metadata_fails` `::test_current_registry_snapshot_has_documented_exceptions_only`
  `tests3/unit/refactor/test_rf_35.py::test_current_registry_snapshot_has_documented_exceptions_only`が`sys.executable,tests3/tools/registry_parity.py,--check`を`shell=False`で実行し、exit 0とmachine JSONをassertする。別direct
  commandとしては実行しない。 `V-OPS` suite=V-OPS。
- リスク/戻し方: 現在の不整合が多数出る。例外はID、理由、owner、review_afterを必須とし無制限allowlistにしない。 失敗時はR0。
- 依存: RF-32
- コミット: `RF-35 define registry roles and enforce parity`

### RF-36 legacy registry metadataをcanonicalへ移し、参照不能registryを削除
- 対象: `tests3/registry.yaml:1-3779` `tests3/test-registry.yaml:1-793` `tests3/checks/registry.json:1-1581` 既存（RF-35で追加済み） `tests3/tools/registry_parity.py:1-末尾` `tests3/Makefile:1-338` `tests3/lib/run-matrix.sh:1-223`
  `tests3/lib/aggregate.py:1-654` 新規 `tests3/registry-migration.json:1-末尾` 新規 `tests3/unit/refactor/test_rf_36.py:1-末尾`
- 問題: 未参照registryが将来の実行対象に見え、更新先を誤らせる。
- 変更: RF-35 parity outputが列挙するlegacy固有metadataを、実行testなら`test-registry.yaml`、check implementationなら`checks/registry.json`へfield単位で移す。 全fieldのdestination mappingを `registry-migration.json` に残す。 parityでlegacy固有情報0、runtime reference
  0を確認後、`tests3/registry.yaml`を削除。 reader fallbackを追加せず、docsを二registry構成へ更新。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-36`と`bash scripts/test/run-required-suites.sh RF-36`がexit 0。`test_registry_parity.py::test_no_legacy_metadata_is_lost` `::test_runtime_reads_only_canonical_registries` `rg -n
  "tests3/registry.yaml|registry.yaml" tests3 Makefile .github` がmigration record/docsの過去説明以外0。 `V-OPS` suite=V-OPS。
- リスク/戻し方: 隠れconsumer。repo全体`rg`とCI workflow解析で0確認する。失敗時はR0。
- 依存: RF-35
- コミット: `RF-36 retire the unused legacy test registry`

### RF-37 changed-file resolverをcanonical path mapへ置換
- 対象: `tests3/resolve.py:8-15,25-47,153-173` `tests3/Makefile:42-50` `.github/workflows/rung.yml:22-32`
- 問題: deprecated resolverが`packages/`、`libs/`、`contracts/`、`schemas/`、`scripts/`、workflow、Harness変更を拾えずsmokeへ縮退する。
- 変更: canonical registry entryへpath globとrequired tierを持たせ、resolverはそこからmapを構築。 shared pathは依存する全suiteを選ぶ。`.github/`、`scripts/harness/`、`.claude/hooks/`、`schemas/`、`tests3/`は最低`smoke + ops/harness`。 unknown tracked
  pathは成功縮退せず、明示`full` fallbackまたはexit 2。CIは`full` fallbackを採用。 deprecated warningと旧hard-coded tableを削除。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-37`と`bash scripts/test/run-required-suites.sh RF-37`がexit 0。`test_resolve.py::test_packages_and_libs_select_consumers` `::test_harness_and_schema_changes_select_harness_gates`
  `::test_workflow_change_selects_full_ci_validation` `::test_unknown_path_falls_back_to_full` `::test_docs_only_uses_documented_lightweight_set` `V-OPS` suite=V-OPS。
- リスク/戻し方: CI時間増加。false negativeを避けるため安全側。path mappingを緩めず、必要なら別承認で性能最適化。 失敗時はR0。
- 依存: RF-35
- コミット: `RF-37 resolve changed files from canonical test ownership`

### RF-38 Compose readinessをfail-closedにする
- 対象: `deploy/compose/Makefile:298-340`
- 問題: Dashboard待機timeout、API/Admin/Dashboard health失敗をechoするだけで成功終了する。
- 変更: bounded retry helperが各必須endpointの試行回数、最終status、elapsedを返す。 必須endpointを全て検査し、失敗一覧を表示後に1件でも失敗ならnon-zero。 optional serviceはcatalogの`optional`に限りskip可。endpoint名のhard-coded善意除外をしない。 実Docker testより先にRF-00D fake curl/dockerを使う。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-38`と`bash scripts/test/run-required-suites.sh RF-38`がexit 0。`test_compose_readiness.py::test_all_required_endpoints_must_succeed` `::test_partial_failure_returns_nonzero`
  `::test_timeout_returns_nonzero` `::test_optional_catalog_service_may_be_absent` fake test後に実environmentが利用可能なCIでCompose smoke pass。 `V-OPS` suite=V-OPS。
- リスク/戻し方: 起動が遅い環境でred化。既存timeoutを維持し、必要なら設定値で上げるが失敗をpassにしない。 失敗時はR0。
- 依存: RF-31, RF-35
- コミット: `RF-38 fail compose readiness on unhealthy services`

### RF-39 Lite readinessとschema initをfail-closedにする
- 対象: `deploy/lite/Makefile:128-200` `deploy/lite/entrypoint.sh:1-末尾`
- 問題: Postgres/API timeout後もready表示し、`init-db`が全errorを`|| true`で捨てる。
- 変更: Postgres/API readinessはRF-38と同じ結果契約を使い、timeoutでnon-zero。 schema syncは既知の冪等状態だけexit 0として明示分類し、それ以外をfatal。 success文言はcommand exit 0確認後だけ表示。 fake command fixtureでstderr/exitを固定。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-39`と`bash scripts/test/run-required-suites.sh RF-39`がexit 0。`test_lite_readiness.py::test_postgres_timeout_is_fatal` `::test_api_timeout_is_fatal`
  `::test_unknown_schema_error_is_fatal` `::test_known_idempotent_schema_state_passes` `::test_success_message_only_after_success` `V-OPS` suite=V-OPS。
- リスク/戻し方: 過去に無視していたschema errorで起動停止。正しいfail-closed。既知エラー追加はerror code/fixture必須。 失敗時はR0。
- 依存: RF-38
- コミット: `RF-39 fail lite startup on readiness or schema errors`

### RF-40 Runtime profile guardをcheckとrepairへ分離
- 対象: `deploy/compose/scripts/guard-runtime-profiles.sh:103-126,140-195` `deploy/compose/Makefile:1-末尾`
- 問題: 「検査」が既定で5serviceをforce-recreateし、内部失敗を`|| true`で隠す。
- 変更: `check-runtime-profiles` はread-onlyで期待/実際のimage/env/profile差を列挙し、差異でnon-zero。 `repair-runtime-profiles` だけが明示confirmation flag付きでrecreateし、各失敗を伝播。 `make test`/CIはcheck-only。自動repairを呼ばない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-40`と`bash scripts/test/run-required-suites.sh RF-40`がexit 0。`test_runtime_profile_guard.py::test_check_never_calls_mutating_docker_commands` `::test_check_fails_on_drift`
  `::test_repair_requires_explicit_flag` `::test_repair_propagates_recreate_failure` `V-OPS` suite=V-OPS。
- リスク/戻し方: 開発者が手動repairを必要とする。commandをREADMEへ明記。 失敗時はR0。
- 依存: RF-00D
- コミット: `RF-40 separate runtime profile checks from repair`

### RF-41 Shell portability helperへ非互換commandを集約
- 対象: `tests3/lib/common.sh:347-375` `deploy/compose/Makefile:1-末尾` `deploy/helm/tests/test_template.sh:1-末尾` `deploy/lite/Dockerfile.lite:1-末尾` `tests3/lib/reset/redeploy-compose.sh:1-末尾` `tests3/lib/reset/redeploy-lite.sh:1-末尾`
  `tests3/lib/reset/reset-compose.sh:1-末尾` `tests3/lib/reset/reset-lite.sh:1-末尾` `tests3/lib/vm-setup-compose.sh:1-末尾` `tests3/lib/vm-setup-lite.sh:1-末尾` `tests3/tests/collect.sh:1-末尾` `tests3/tests/meeting-tts-teams.sh:1-末尾`
  `tests3/tests/meeting-tts.sh:1-末尾` `tests3/tests/static/dashboard-config-ssot.sh:1-末尾` `tests3/tests/transcribe.sh:1-末尾` `tests3/tests/transcription-replay.sh:1-末尾` `tests3/tests/v0.10.6.1-tts-auto-lang.sh:1-末尾`
  `tests3/tests/webhooks.sh:1-末尾` 新規 `tests3/lib/portability.py:1-末尾` 新規 `.github/workflows/shell-portability.yml:1-末尾` read-only inventory: `git grep -n -E -e 'head -n -1|sed -i|date -Iseconds|stat -c%s' -- deploy/compose
  deploy/lite deploy/helm scripts/harness tests3`。期待pathは上記18既存fileだけで、別pathが1件でもあれば停止してplan reviewへ戻る
- 問題: macOS/BSDとUbuntu/GNUで挙動が違い、現在macOSで`head -n -1`が失敗する。
- 変更: line除去、file size、ISO timestamp、in-place editは標準Python helperへ集約。 shellからOS判定して別commandを組むbranchを増やさない。 `shell-portability` GitHub Actions matrixをmacOS/Ubuntuで実行する。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-41`と`bash scripts/test/run-required-suites.sh RF-41`がexit 0。`test_portability_helpers.py::test_drop_last_line` `::test_file_size` `::test_iso_timestamp_is_timezone_aware`
  `::test_atomic_text_replacement` GitHub Actions workflow syntax check。 `V-OPS` suite=V-OPS。
- リスク/戻し方: Python不在環境。リポジトリHarness/tests3はPythonを既に前提としていることをCI imageで確認。 失敗時はR0。
- 依存: RF-31
- コミット: `RF-41 make test and deploy helpers cross-platform`

### RF-42 Helm検査を本当に実行しrelease前requiredにする
- 対象: `deploy/helm/tests/test_helm_lint.sh:33-49` `deploy/helm/tests/test_template.sh:14-23,27,49,74-78` `.github/workflows/chart-release.yml:19-58`
- 問題: Helm不在・lint失敗をpass扱いし、`set -e`下の`((PASS++))`で最初の成功時に終了し得る。個人kubeconfig参照もある。
- 変更: Helm不在はCIでinvalid/fail、local明示skipはRF-31のskip。 lint/template failureをそのままnon-zero。 arithmetic incrementを`PASS=$((PASS + 1))`等へ変更。 kubeconfigをCI secret/inputから受け、個人pathを削除。 release jobはlint/template/schema
  validation成功を`needs`で必須化。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-42`と`bash scripts/test/run-required-suites.sh RF-42`がexit 0。`test_helm_scripts.py::test_missing_helm_is_not_pass` `::test_lint_failure_propagates` `::test_template_runs_all_cases`
  `::test_no_personal_kubeconfig_path` fake helmとCI workflow static test。 `V-OPS` suite=V-OPS。
- リスク/戻し方: chart release停止。release前にcurrent chartでlint/templateを実行し、既存chart問題を別項目として報告。testを弱めない。 失敗時はR0。
- 依存: RF-31, RF-41
- コミット: `RF-42 require real Helm validation before release`

### RF-43 Managed Harnessをmain PRのrequired workflowへ接続
- 対象: `.github/workflows/rung.yml:5-8,17-39,63-75` `.github/workflows/{gates,labeler}.yml:1-末尾` `.harness/target.yaml:6-20,37` `scripts/harness/{outcome-judge,validate-runtime-profile}.sh:1-末尾` `tests3/Makefile:1-末尾` 新規
  `.github/workflows/managed-harness-gate.yml:1-末尾` 新規 `tests3/unit/refactor/test_rf_43.py:1-末尾`（workflow security checkerもこのtest内へ固定）
- 問題: 旧`tests3-stateless`検査は`continue-on-error`で最終jobが常に0、main PRにManaged Harness gateがない。
- 変更: 新workflow `managed-harness-gate.yml`でcontext/residency/preflight/hd-gate/adapter validation/tests3 report-gate/outcome judgeを順に実行。 required jobは各exit codeを伝播し、artifact/evidenceがない場合fail。
  triggerは`pull_request`だけで`pull_request_target`を使わない。workflow top-level `permissions: contents: read`、必要な追加scopeは該当jobだけへ明示し、fork/untrusted PRにenvironment secret、repository write token、OIDC
  `id-token`を渡さない。`actions/checkout`は`persist-credentials:false`、全`uses:`は検証済みfull 40桁commit SHAへpinしtag/branch参照を禁止する。download artifactを実行可能fileやPATHへ置かず、PR codeが書くoutputをshell/evalへ再解釈しない。 旧rungはlegacy
  fixture専用の手動workflowへ移し、main PR triggerを外す。削除しない。 同じworkflow security checkerを`.github/workflows/rung.yml`, `gates.yml`, `labeler.yml`へ適用する。write permissionまたは`pull_request_target`が業務上必要なworkflowはuntrusted checkout/code
  executionを同jobで行わず、pin済みactionとmetadata-only入力だけに限定する。満たせなければRF-43を中断しrequired化しない。 branch protection自体の変更はrepo外操作なので、必要なrequired check名をdelivery noteへ記載し、管理者設定完了まで「CI接続済み、branch protection未確認」と区別。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-43`と`bash scripts/test/run-required-suites.sh RF-43`がexit 0。`test_workflow_contract.py::test_managed_harness_gate_has_no_continue_on_error`
  `::test_required_job_depends_on_all_gates` `::test_legacy_rung_does_not_run_on_main_pull_request` `::test_pr_workflows_use_pull_request_read_only_permissions_pinned_actions_and_checkout_without_credentials`
  `::test_pull_request_target_write_jobs_never_checkout_or_execute_untrusted_code_and_receive_no_oidc_or_environment_secrets` local action syntax/lint。 `V-OPS`, `V-HARNESS-CONTRACT` suite=V-HARNESS-CONTRACT,V-OPS。
- リスク/戻し方: 壊れたgateをrequired化すると全PR停止。RF-31〜42後、local full gate greenを証拠化してから実施。 失敗時はR0。
- 依存: RF-31, RF-32, RF-33, RF-34, RF-35, RF-36, RF-37, RF-38, RF-39, RF-40, RF-41, RF-42
- コミット: `RF-43 require the managed harness on main pull requests`

### RF-44 Dashboard deployへpretest・path filter・probe・rollbackを追加
- 対象: `.github/workflows/deploy-dashboard-gcp.yml:3-6,32-48` `deploy/gcp/cloudbuild-dashboard.yaml:17-38`
- 問題: mainの全pushで直接build/deployし、事前test、対象path限定、deploy後probe、前revision復帰がない。
- 変更: triggerをDashboard、transcript package、関連deploy configのpathへ限定。 deploy前に`V-DASH`相当とtranscript suiteをrequired jobで実行。 PR/test jobは`permissions: contents: read`、secret/id-token 0、checkout `persist-credentials:false`。main
  push後のdeploy jobだけ`id-token: write,contents: read`を持ち、environment approvalとprotected branch条件を必須にする。`actions/checkout`,`google-github-actions/auth`,`setup-gcloud`を含む全`uses:`はfull commit SHAへpinしtag参照を禁止する。untrusted PR
  code、PR生成artifact、PR-controlled action pathをdeploy/OIDC jobで実行・source・PATH追加しない。 imageはdigestでCloud Runへ反映。 deploy前revisionを保存し、health/API/static asset probe失敗時は前revisionへtrafficを戻してjob fail。 source commit labelをrevisionへ付け、UI
  evidenceのSHA照合に使う。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-44`と`bash scripts/test/run-required-suites.sh RF-44`がexit 0。`test_dashboard_deploy_workflow.py::test_unrelated_change_does_not_deploy` `::test_deploy_requires_tests`
  `::test_uses_image_digest` `::test_failed_probe_rolls_back_and_fails_job` `::test_revision_records_source_sha` `::test_deploy_oidc_job_runs_only_after_protected_main_and_environment_approval_with_pinned_actions`
  `::test_pull_requests_receive_no_gcp_oidc_secret_or_write_token_and_cannot_feed_executable_artifacts_to_deploy` Cloud Build config dry-run/static validation。 `V-OPS`, `V-DASH`。 suite=V-DASH,V-OPS。
- リスク/戻し方: rollback command自体の誤動作。fake gcloudでargvを固定し、production実行は管理者承認後。workflow commitは。 失敗時はR0。
- 依存: RF-43
- コミット: `RF-44 make dashboard deployment verifiable and reversible`

### RF-45A image catalogを単一sourceにする
- 対象: `deploy/compose/Makefile:390-440` `deploy/compose/docker-compose.yml:645-715` `deploy/README.md:53-58` 新規 `deploy/images.yaml:1-末尾` 新規 `tests3/unit/refactor/test_rf_45a.py:1-末尾`
- 問題: publish対象、Compose service、docsのimage一覧が一致せず、voiceprint/calendar等が漏れる。
- 変更: `deploy/images.yaml`へ `id/context/dockerfile/status=shipped|optional|no-ship/publish_name/owners` を定義。 publish matrix、inventory check、docs tableをcatalogから生成または検証。 `no-ship`は理由必須。既存imageのship
  statusを推測せず、Composeで使うがpublishしないものは明示`no-ship`としてreview対象にする。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-45A`と`bash scripts/test/run-required-suites.sh RF-45A`がexit 0。`test_image_catalog.py::test_every_compose_build_has_catalog_entry` `::test_every_publish_target_is_shipped`
  `::test_no_ship_requires_reason` `::test_docs_inventory_matches_catalog` catalog generation後tracked unexpected diff 0。 `V-OPS` suite=V-OPS。
- リスク/戻し方: 誤ってimageを公開する危険。既存publish対象だけを`shipped`で初期化し、追加は明示承認まで`no-ship`。 失敗時はR0。
- 依存: RF-35
- コミット: `RF-45A define one deploy image catalog`

### RF-45B vexa-client notebook helperをimport-safeなexample moduleへ変更
- 対象: `packages/vexa-client/vexa_client/test_funcs.py:1-67` `packages/vexa-client/tests/admin_tutorial.ipynb:1-末尾` の先頭import cell `packages/vexa-client/pyproject.toml:40-45` 新規
  `packages/vexa-client/vexa_client/notebook_helpers.py:1-末尾` 新規 `packages/vexa-client/tests/test_notebook_helpers.py:1-末尾`
- 問題: production package内にpytest収集と誤解される`test_funcs.py`があり、import時に`sys.path`を書換え、標準library名と衝突する`import test`を実行し、localhost URLを直値化する。notebookだけのhelperがpackage importを不安定にする。
- 変更: `test_funcs.py`を`notebook_helpers.py`へrenameする。compatibility re-export fileは残さず、repo内の唯一のconsumerである`admin_tutorial.ipynb`のJSON cell sourceを`from vexa_client.notebook_helpers import ...`へ更新する。 `sys.path.append`、`import
  test`、相対`from vexa import`を削除し、`from vexa_client.vexa import VexaClient`へ固定する。`parse_url`を使っていない場合はimportしない。 module import時にenv読込、network、client生成、print/displayを実行しない。`create_user_client(admin_client, user_api_key=None, *,
  base_url=None)`とし、`base_url`は明示引数→`VEXA_API_URL`→library既定値の順。`ADMIN_API_TOKEN`をmodule globalへ読まない。
  `get_transcript`は例外を握り潰してprintせず、`poll_count`と`interval_seconds`をkeyword-only引数にし、最終exceptionをcallerへraiseする。Notebook側が必要ならcellで表示用try/exceptを書く。 notebookはexample artifactでありpytest対象にしない。testはmodule import副作用0、明示base
  URL、client注入、poll回数/raise、notebook import cellだけを検証し、実networkへ接続しない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-45B`と`bash scripts/test/run-required-suites.sh RF-45B`がexit
  0。`packages/vexa-client/tests/test_notebook_helpers.py::{test_import_has_no_path_env_network_or_display_side_effect,test_base_url_precedence_is_explicit,test_polling_raises_last_error,test_admin_notebook_imports_packaged_helper}`
  `test_import_has_no_path_env_network_or_display_side_effect`が同じintegrations interpreterのsubprocessで`from vexa_client.notebook_helpers import create_user_client, request_bot, get_transcript`を実行しexit 0をassertする。
  `test_admin_notebook_imports_packaged_helper`が旧`packages/vexa-client/vexa_client/test_funcs.py`不存在と、`sys.path|import test|from test_funcs|localhost:18056`のproduction/notebook source一致0をassertする。これらを未登録direct commandとして再実行しない。
  `V-CLIENTS`, `V-INTEGRATIONS`。 suite=V-CLIENTS,V-INTEGRATIONS。
- リスク/戻し方: 外部利用者が非公開`test_funcs`をimportしている可能性はあるが、package contract/READMEに公開されていない。互換shimで危険importを残さず、必要なら別のdeprecation計画として停止報告する。失敗branchを保持しRF-45AのSHAから再実行する。 失敗時はR0。
- 依存: RF-45A
- コミット: `RF-45B make vexa client notebook helpers import safe`

### RF-46 Harness adapter/runtime schema validationを統一
- 対象: `scripts/harness/validate-runtime-profile.sh:68-90` `schemas/{harness-adapter,harness-agent,harness-environment}.schema.json:1-末尾` 新規 `scripts/harness/lib/contracts.py:1-末尾` 新規
  `tests3/unit/refactor/test_rf_46.py:1-末尾`
- 問題: tracked runtime validatorはJSON parseしかせず、tracked schemaにある`name`、ID長、kind enumを検証しない。
- 変更: 標準Pythonのみの `scripts/harness/lib/contracts.py` にschema loader/validatorを実装する。既存jsonschema dependencyを追加しない。 tracked `validate-runtime-profile.sh`は同じvalidator CLIを呼ぶ。schemaをtracked側の唯一のenum/pattern sourceにし、duplicate shell
  regexを削除し、invalid field pathと理由を同じJSON error形式で返す。external hook parityはこの項目の前提・合格条件にせず、projectのhook修復後に別の保守taskまたは最終PR gateで確認する。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-46`と`bash scripts/test/run-required-suites.sh RF-46`がexit 0。`test_harness_contracts.py::test_tracked_cli_accepts_valid_fixture`
  `::test_tracked_cli_rejects_invalid_fixtures` `::test_name_id_length_and_kind_match_schema` `::test_invalid_json_is_not_only_validation_performed` `git diff --name-only "$ITEM_BASE_SHA"` に `.claude/` が0件。
  `V-OPS`, `V-HARNESS-CONTRACT` suite=V-HARNESS-CONTRACT,V-OPS。
- リスク/戻し方: 既存tracked adapterがschema不適合。fixture一覧を先に走らせ、不適合は無言でschemaを緩めず中断する。external symlink targetは編集しない。失敗時はR0。
- 依存: RF-01
- コミット: `RF-46 validate harness contracts from one schema`

### RF-47 新規Harness worktreeを `.pipeline` 外へ置く
- 対象: `scripts/harness/worktree.sh:75-84` `scripts/harness/{build,codex-build,full-loop-smoke,delivery-integrity-smoke}.sh:1-末尾` runtime checkout: `.worktrees/<task-id>/**:1-末尾` runtime metadata:
  `.pipeline/worktrees/<task-id>/worktree.json:1-末尾`
- 問題: checkoutを`.pipeline/worktrees/.../checkout`へ作り、`.pipeline`配下が33万file超となりevidence scanやcontext収集を肥大化させる。
- 変更: 新規worktree rootをrepo内の既存ignored path `.worktrees/<task-id>`へ固定する。repo siblingとの実行時選択を残さない。 metadata pathは現行callerとRF-00Aが読む`.pipeline/worktrees/<task-id>/worktree.json`のまま変えず、そこへ実checkout
  pathを保存する。`.worktrees/<task-id>/worktree.json`や`.pipeline/worktrees/<task-id>/metadata.json`という第二の正本を作らない。 既存worktreeは移動・削除しない。lookupはmetadataにpathがあれば新旧両方を読める。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-47`と`bash scripts/test/run-required-suites.sh RF-47`がexit 0。`test_worktree_paths.py::test_new_checkout_is_outside_pipeline` `::test_metadata_stays_inside_pipeline`
  `::test_existing_legacy_worktree_is_still_discoverable` `::test_no_existing_worktree_is_moved_or_deleted` temporary Git repo integration test。 `V-OPS`, `V-HARNESS-CONTRACT` suite=V-HARNESS-CONTRACT,V-OPS。
- リスク/戻し方: filesystem permission/path長。`mktemp` fixtureで両OS確認。既存worktreeに破壊操作をしない。 失敗時はR0。
- 依存: RF-01, RF-46
- コミット: `RF-47 keep harness checkouts out of evidence storage`

### RF-48 Harness共通I/O libraryを導入する
- 対象: read-only input: `scripts/harness/external-consultation.sh:57-90,185-220,850-953` 新規 `scripts/harness/lib/pipeline_io.py:1-末尾` 新規 `tests3/unit/refactor/test_rf_48.py:1-末尾`
- 問題: task path、atomic JSON、hash、event appendのembedded Pythonが複数scriptに重複する。
- 変更: `scripts/harness/lib/pipeline_io.py`に `resolve_task_path`、`read_json`、`atomic_write_json`、`atomic_write_text`、`append_event`、`sha256_file`、`canonical_json_hash` を追加。 RF-01のpath containmentを必ず内部で再利用。 この項目ではproduction
  callsiteを移行しない。現行scriptと同じfixture出力をgolden testで固定する。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-48`と`bash scripts/test/run-required-suites.sh RF-48`がexit 0。`test_pipeline_io.py::test_atomic_write_never_leaves_partial_json` `::test_atomic_text_write_never_leaves_partial_file`
  `::test_event_append_is_valid_jsonl_under_concurrency` `::test_canonical_hash_is_stable` `::test_all_paths_are_task_contained` library dependencyは標準Pythonのみ。 `V-OPS`, `V-HARNESS-CONTRACT` suite=V-HARNESS-CONTRACT,V-OPS。
- リスク/戻し方: 未使用libraryのdrift。次項目で必ずconsumer移行し、APIをtest固定。 失敗時はR0。
- 依存: RF-01, RF-46
- コミット: `RF-48 add shared harness pipeline I/O primitives`

### RF-49A Harness decision/gate JSONを共通I/Oへ移行
- 対象: `scripts/harness/sml-decision.sh:60-154` `scripts/harness/outcome-judge.sh:22-183` 新規 `tests3/unit/test_harness_cli_compatibility.py:1-末尾`
- 問題: decision/gateのtask path、JSON読書き、error形式がembedded Pythonへ重複する。
- 変更: 2 scriptのpath resolve/read/atomic JSON writeだけをRF-48 `pipeline_io.py`へ移す。判定ロジック、CLI引数、field、exit code、stdout/stderr順は変更しない。 S/M/L、invalid size、既存verification contract非上書き、checkpoint ID保持、outcomeのledger/L sidechain/task
  mismatchをgolden化する。 `backcast-state.sh`等、下記後続IDのscriptへ触れない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-49A`と`bash scripts/test/run-required-suites.sh RF-49A`がexit
  0。`tests3/unit/test_harness_cli_compatibility.py::{test_sml_decision_json_exit_and_output_match_golden,test_outcome_judge_json_exit_and_output_match_golden,test_invalid_size_and_task_mismatch_fail_closed}` 上記exact
  nodeidは`run-refactor-item.sh RF-49A`のmatrix `pytest` commandだけが実行し、全件pass、unexpected skip/xfail 0をmachine reportで確認する。 before/afterのJSON field、exit code、stdout/stderr順がgolden一致。 `V-OPS`, `V-HARNESS-CONTRACT`。
  suite=V-HARNESS-CONTRACT,V-OPS。
- リスク/戻し方: judgeの1 fieldでも変わると既存gateを壊す。bytes差があればcommitせず中断し、最後の良好SHAから再実行する。 失敗時はR0。
- 依存: RF-48
- コミット: `RF-49A migrate harness decision and outcome I/O`

### RF-49B Harness session/state JSONLを共通I/Oへ移行
- 対象: `scripts/harness/codex-session-ledger.sh:55-147,183-256` `scripts/harness/backcast-state.sh:61-136` `scripts/harness/backcast-current.sh:59-208` `tests3/unit/test_harness_cli_compatibility.py:1-末尾`（RF-49A作成物）
- 問題: session/state event appendとcheckpoint JSON更新が別々の非atomic実装を持つ。
- 変更: task path、atomic JSON、JSONL appendだけをRF-48へ移す。transition legality、sequence、run ID、timestamp field、CLI outputは維持。 legal/illegal/allow-same、concurrent append、current updateのapproval/manifest前後をgolden比較する。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-49B`と`bash scripts/test/run-required-suites.sh RF-49B`がexit
  0。`tests3/unit/test_harness_cli_compatibility.py::{test_session_ledger_legal_and_illegal_transitions_match_golden,test_twenty_concurrent_writers_produce_unique_parseable_sequence,test_backcast_current_and_state_match_golden}`
  上記exact nodeidは`run-refactor-item.sh RF-49B`のmatrix `pytest` commandだけが実行し、全件passをmachine reportで確認する。 並列20 writer後のJSONLが全行parse可能、sequence重複0。 `V-OPS`, `V-HARNESS-CONTRACT`。 suite=V-HARNESS-CONTRACT,V-OPS。
- リスク/戻し方: event順やstate transition差。golden差1件でも中断。失敗branchを保持し前SHAから再実行。 失敗時はR0。
- 依存: RF-49A
- コミット: `RF-49B migrate harness session and state I/O`

### RF-49C Harness evidence/approval writerを共通I/Oへ移行
- 対象: `scripts/harness/backcast-evidence-pack.sh:28-197` `scripts/harness/backcast-approval.sh:78-205` `scripts/harness/backcast-next-checkpoint.sh:114-148,185-208` `tests3/unit/test_harness_cli_compatibility.py:1-末尾`（RF-49A作成物）
- 問題: evidence text、approval JSON、next-checkpoint更新がtask containment/atomic writeを各自実装する。
- 変更: RF-48の`atomic_write_text`/`atomic_write_json`/task pathへ移行し、pack内容、approval HEAD binding、state/exit/outputをgolden一致させる。 approval済みHEAD不一致、manifest欠落、next checkpoint不正遷移を従来どおりfail closedにする。legacy
  flagなしmodeの既存state挙動は他task互換としてgolden固定するが、本計画では呼ばない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-49C`と`bash scripts/test/run-required-suites.sh RF-49C`がexit
  0。`tests3/unit/test_harness_cli_compatibility.py::{test_evidence_pack_text_matches_golden,test_approval_head_binding_and_next_checkpoint_match_golden,test_immutable_approval_binds_manifest_pack_and_target_hash_without_rewriting_reviewed_files,test_interrupted_writes_leave_no_partial_artifact}`
  上記exact nodeidは`run-refactor-item.sh RF-49C`のmatrix `pytest` commandだけが実行し、全件passをmachine reportで確認する。 interrupted write fixtureでpartial JSON/text 0。 `V-OPS`, `V-HARNESS-CONTRACT`。 suite=V-HARNESS-CONTRACT,V-OPS。
- リスク/戻し方: approval hashの意味変更はPR gateを無効化する。exact bytes/hash golden差で中断し、前SHAから再実行。 失敗時はR0。
- 依存: RF-49B
- コミット: `RF-49C migrate harness evidence and approval I/O`

### RF-49D Harness worktree/build metadata I/Oを共通化
- 対象: `scripts/harness/worktree.sh:66-224` `scripts/harness/build.sh:170-207` `tests3/unit/test_harness_cli_compatibility.py:1-末尾`（RF-49A作成物）
- 問題: operational metadataのpath/JSON writeだけが共通containment/atomicityを迂回する。
- 変更: worktree metadataとbuild summaryのpath resolve/read/writeだけをRF-48へ移す。git worktree操作、build command、auto-commit、state transition、manifest/pack起動は変更しない。 legacy/new worktree discoveryと成功/失敗build summaryをgolden比較する。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-49D`と`bash scripts/test/run-required-suites.sh RF-49D`がexit
  0。`tests3/unit/test_harness_cli_compatibility.py::{test_legacy_and_new_worktree_discovery_match_golden,test_success_and_failure_build_summaries_match_golden}` 上記exact nodeidは`run-refactor-item.sh RF-49D`のmatrix `pytest`
  commandだけが実行し、全件passをmachine reportで確認する。 `V-OPS`, `V-HARNESS-CONTRACT`。 suite=V-HARNESS-CONTRACT,V-OPS。
- リスク/戻し方: git操作まで巻き込む危険。diffにmetadata I/O以外のworktree/build control flow差があれば中断。 失敗時はR0。
- 依存: RF-47, RF-49C
- コミット: `RF-49D migrate harness operational metadata I/O`

### RF-49E Review差分のimmutable base SHAを固定
- 対象: `scripts/harness/build.sh:170-207` 新規 `tests3/unit/test_harness_review_base.py:1-末尾`
- 問題: tracked review scriptsはbuild summaryのlegacy `head_sha`をreview baseとして読む。build summaryを最終HEADで再生成するとこれが上書きされ、post reviewの差分が空になる。
- 変更: 互換field `head_sha`をimmutable review baseとして残す。旧summaryがあれば旧`head_sha`、なければ現在HEADを初回値にし、以後上書きしない。 新field `implementation_head_sha`へbuild実行時の現在HEADを書く。補助field `base_sha`もimmutable `head_sha`と同じ値にするが、外部symlink validatorを変更しない。
  tracked review scriptsも現状どおりlegacy `head_sha`を読むため、callsite変更は不要。`head_sha`が`implementation_head_sha`のancestorでない、またはpost modeで差分空ならtracked
  review script側がfailする契約testを追加する。 RF-00Aのbaseline build summaryをmigration fixtureに使う。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-49E`と`bash scripts/test/run-required-suites.sh RF-49E`がexit
  0。`tests3/unit/test_harness_review_base.py::{test_three_builds_preserve_immutable_review_base,test_implementation_head_only_tracks_current_head,test_post_review_rejects_empty_nonancestor_and_unknown_base}`
  上記exact nodeidは`run-refactor-item.sh RF-49E`のmatrix `pytest` commandだけが実行し、全件passをmachine reportで確認する。 3回build summaryを更新してもlegacy `head_sha`と`base_sha`はRF-00A SHAのまま、`implementation_head_sha`だけ現在HEADへ更新。 post review
  fixtureのdiffがRF-00A後の実装commitを1件以上含み、空diff/非ancestor/未知SHAをreject。 `V-OPS`, `V-HARNESS-CONTRACT`。 suite=V-HARNESS-CONTRACT,V-OPS。
- リスク/戻し方: legacy field名と意味の互換を壊す危険。tracked consumerのgolden差が出たら中断し、外部hook変更はこの項目へ含めない。失敗時はR0。
- 依存: RF-49D
- コミット: `RF-49E preserve an immutable review base SHA`

### RF-50 External consultationを薄いCLIへ段階移行
- 対象: `scripts/harness/external-consultation.sh:1-953` 新規 `scripts/harness/lib/external_consultation.py:1-末尾` 新規 `tests3/unit/refactor/test_rf_50.py:1-末尾`
- 問題: 953行のshell + embedded Pythonにpath、JSON、hash、event、provider呼出し、fallbackが混在する。
- 変更: `scripts/harness/lib/external_consultation.py`へpure plan validation、request manifest、response parsing、evidence writingを移す。 shellはargument/env validation、provider process起動、Python CLI呼出しだけにする。 provider command、timeout、max
  call、fallback、artifact path、event順、hash fieldを変更しない。 一括書換えせず、既存golden fixtureの各mode `plan|review`、success/failure/max-call fallbackを比較してからembedded blockを削除。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-50`と`bash scripts/test/run-required-suites.sh RF-50`がexit 0。`test_external_consultation.py::test_plan_mode_matches_legacy_golden` `::test_review_mode_matches_legacy_golden`
  `::test_provider_failure_records_advisory_failure` `::test_max_call_fallback_is_preserved` `::test_evidence_hash_binds_exact_request_and_response` shell fileがargument/exec orchestrationだけになり、embedded Python 0。 `V-OPS`,
  `V-HARNESS-CONTRACT` suite=V-HARNESS-CONTRACT,V-OPS。
- リスク/戻し方: L-task gateを壊す。legacy goldenを同一fixtureで比較し、差があれば削除段階へ進まない。失敗時はR0。
- 依存: RF-49E
- コミット: `RF-50 extract external consultation orchestration`

### RF-51 Docs ownership検査でREADME実在を必須化
- 対象: `tests3/docs/check.py:119-178` `tests3/docs/manifest.json:19-23,44-48` `docs/README.md:25,30` 新規 `tests3/unit/refactor/test_rf_51.py:1-末尾`
- 問題: ownership manifestが存在しないREADMEを指しても4 checks passになる。
- 変更: `README_EXISTS` checkを追加し、各ownership targetはregular file、repo内、case-sensitive一致を必須とする。 ghost owner `transcription-collector`を削除し、`api/transcripts`, `api/meetings`, `api/recordings`を既存`meeting-api` ownerへ明示remapする。 ghost
  owner `shared-models`を削除し、webhook契約を`meeting-api`へremapする。token scopingは`api-gateway`のまま変更しない。 `docs/README.md`の同じowner表をmanifestと同期する。存在しないREADMEを新規作成する選択肢は取らない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-51`と`bash scripts/test/run-required-suites.sh RF-51`がexit
  0。`tests3/unit/refactor/test_rf_51.py::{test_missing_owned_readme_fails,test_path_outside_repo_fails,test_current_manifest_targets_exist,test_docs_check_subprocess_reports_readme_exists_pass}` `V-OPS` suite=V-OPS。
- リスク/戻し方: ownershipを誤配分する危険。現在のproduction route/importとservice責務へ上記mappingを固定し、実行時に別ownerを選ばない。失敗時は前SHAから再実行。 失敗時はR0。
- 依存: RF-35
- コミット: `RF-51 verify owned documentation exists`

## 5. Phase 3: 契約維持型リファクタリング（RF-52〜RF-75F）

### Backend

### RF-52 Meeting lifecycleの型と分類を依存なしmoduleへ移す
- 対象: `services/meeting-api/meeting_api/callbacks.py:29-36,115-124,364-396,887-912` `services/meeting-api/meeting_api/collector/endpoints.py:1-末尾` `services/meeting-api/meeting_api/final_transcription.py:1-末尾`
  `services/meeting-api/meeting_api/meetings.py:1-末尾` `services/meeting-api/meeting_api/outbound_events.py:1-末尾` `services/meeting-api/meeting_api/schemas.py:1-末尾` `services/meeting-api/meeting_api/sweeps.py:1-末尾`
  `services/meeting-api/meeting_api/voice_agent.py:1-末尾` read-only inventory: `git grep -n -E -e 'MeetingStatus|CompletionReason|FailureReason|is_terminal' --
  services/meeting-api/meeting_api`。期待pathは上記8既存fileだけで、別pathが1件でもあれば停止してplan reviewへ戻る 新規 `services/meeting-api/meeting_api/lifecycle/__init__.py:1-末尾` 新規 `services/meeting-api/meeting_api/lifecycle/types.py:1-末尾` 新規
  `services/meeting-api/meeting_api/lifecycle/classification.py:1-末尾` 新規 `services/meeting-api/tests/refactor/test_rf_52.py:1-末尾`
- 問題: status enum、失敗理由、終端分類がcallbacks/schemasへ分散し、循環依存の根になる。
- 変更: `meeting_api/lifecycle/types.py`へ `MeetingStatus`、`MeetingCompletionReason`、`MeetingFailureStage`、`TerminalSignal`、`TerminalDecision`を移す。 `lifecycle/classification.py`へRF-11のpure classifierを移す。
  旧`meeting_api.schemas`と`callbacks`から同名をre-exportし、外部importを壊さない。 新moduleはstdlib/enum/dataclassだけに依存し、DB/FastAPI/routerをimportしない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-52`と`bash scripts/test/run-required-suites.sh RF-52`がexit 0。`test_lifecycle_imports.py::test_types_and_classification_have_no_service_dependencies`
  `::test_legacy_imports_are_identity_compatible` `test_lifecycle_characterization.py::test_terminal_matrix_snapshot` `V-MEETING` suite=V-MEETING。
- リスク/戻し方: enum class identityが二重になる危険。定義は新module1か所、旧moduleはalias re-exportのみ。 失敗時はR0。
- 依存: RF-11
- コミット: `RF-52 extract dependency-free meeting lifecycle types`

### RF-53 Meeting status transitionを単一serviceへ移す
- 対象: `services/meeting-api/meeting_api/meetings.py:1-末尾` のstatus update `services/meeting-api/meeting_api/callbacks.py:1-末尾` `services/meeting-api/meeting_api/sweeps.py:1-末尾`
  `services/meeting-api/meeting_api/post_meeting.py:1-末尾` `services/meeting-api/meeting_api/recording_finalizer.py:1-末尾` 新規 `services/meeting-api/meeting_api/lifecycle/transitions.py:1-末尾` 新規
  `services/meeting-api/tests/refactor/test_rf_53.py:1-末尾`
- 問題: status update、terminal guard、timestamp、publish条件が複数moduleに分散する。
- 変更: `meeting_api/lifecycle/transitions.py` に `MeetingTransitionService.transition(meeting_id, expected_from, decision, session)` を作る。 既存transaction/sessionを引数で受け、service自身が新transactionを開始しない。 idempotent
  terminal再入、timestamp、completion reason、failure stageの更新規則をRF-00B goldenどおり実装。 callbacks/sweeps等は旧function wrapperを経て新serviceを呼ぶ。call orderは維持。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-53`と`bash scripts/test/run-required-suites.sh RF-53`がexit 0。`test_transitions.py::test_transition_matrix_matches_characterization` `::test_duplicate_terminal_transition_is_noop`
  `::test_uses_callers_existing_transaction` `::test_publish_is_not_inside_transition_service` `V-MEETING` suite=V-MEETING。
- リスク/戻し方: transaction境界の暗黙変更。commit/rollbackをservice内で呼んだらtest failure。wrapperを残すため。 失敗時はR0。
- 依存: RF-52
- コミット: `RF-53 centralize meeting status transitions`

### RF-54 終端副作用をTerminalMeetingServiceへ集約
- 対象: `services/meeting-api/meeting_api/callbacks.py:1-末尾` `services/meeting-api/meeting_api/sweeps.py:1-末尾` `services/meeting-api/meeting_api/post_meeting.py:1-末尾` `services/meeting-api/meeting_api/recording_finalizer.py:1-末尾`
  `services/meeting-api/meeting_api/final_transcription.py:992,1059` 新規 `services/meeting-api/meeting_api/lifecycle/terminal_service.py:1-末尾` 新規 `services/meeting-api/tests/refactor/test_rf_54.py:1-末尾`
- 問題: recording finalize、transition、publish、post-meeting enqueueがcallback/sweepごとに組み替えられ、重複や意味差を生む。
- 変更: 依存はconstructor ports `RecordingFinalizer`, `TransitionService`, `Publisher`, `PostMeetingEnqueuer`として注入する。各callback/sweepはsignal変換だけ行い同serviceを呼ぶ。副作用順序、回数、error isolationはRF-00B goldenに一致させる。
- 判断固定: 順序: recording finalize → transition commit → publish → post-meeting enqueue。callerのtransactionを勝手に開始しない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-54`と`bash scripts/test/run-required-suites.sh RF-54`がexit 0。`test_terminal_service.py::test_side_effect_order_matches_characterization`
  `::test_duplicate_callbacks_finalize_and_enqueue_once` `::test_exit_status_and_sweep_signals_reach_same_terminal_result` `test_post_meeting_idempotency.py::test_terminal_signals_enqueue_post_meeting_once`
  `test_sweeps_stopping.py::test_sweep_terminal_signal_uses_terminal_service_once` `V-MEETING` suite=V-MEETING。
- リスク/戻し方: CRITICAL範囲。GitNexus impactを必ず保存し、callsite漏れが1件あれば中断し、旧wrappersを残す。失敗時はR0。
- 依存: RF-53
- コミット: `RF-54 centralize terminal meeting orchestration`

### RF-55 Meeting lifecycleの循環importを除去
- 対象: `services/meeting-api/meeting_api/main.py:29-33,332` `services/meeting-api/meeting_api/meetings.py:53,333,1683,1715,1949,1991,2044,2396` `services/meeting-api/meeting_api/callbacks.py:29-36`
  `services/meeting-api/meeting_api/post_meeting.py:17` `services/meeting-api/meeting_api/final_transcription.py:992,1059` `services/meeting-api/meeting_api/sweeps.py:99,159,243,322,520,694`
  `services/meeting-api/meeting_api/recording_finalizer.py:585` `services/meeting-api/meeting_api/voice_agent.py:18` `services/meeting-api/meeting_api/voiceprints.py:41` 新規 `services/meeting-api/tests/refactor/test_rf_55.py:1-末尾`
- 問題: 9-module cycleを関数内importで回避し、import時の構造と実行時依存が一致しない。
- 変更: RF-52〜54のtypes/service/portsを依存方向の中心にする。 router/mainがcomposition rootでconcrete portsを組み立てる。 production codeの関数内importを列挙し、循環回避目的のものだけ通常import/DIへ置換する。 optional dependency/lazy heavy SDKのimportは理由commentとtestを残し、この項目で無理に移さない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-55`と`bash scripts/test/run-required-suites.sh RF-55`がexit 0。`test_import_graph.py::test_meeting_api_production_import_graph_is_acyclic`
  `::test_all_meeting_modules_import_in_clean_process` RF-00B lifecycle/finalization golden差0。 `V-MEETING` suite=V-MEETING。
- リスク/戻し方: startup時にoptional SDKを要求する可能性。clean process testはproduction dependency setとminimal test setの両方で実行。 失敗時はR0。
- 依存: RF-52, RF-53, RF-54
- コミット: `RF-55 remove meeting lifecycle import cycles`

### RF-56 `request_bot`からpure builderを抽出
- 対象: `services/meeting-api/meeting_api/meetings.py:722-1278` 新規 `services/meeting-api/meeting_api/bot_request/__init__.py:1-末尾` 新規 `services/meeting-api/meeting_api/bot_request/builders.py:1-末尾` 新規
  `services/meeting-api/tests/refactor/test_rf_56.py:1-末尾`
- 問題: 557行にmode判定、URL、重複制限、DB作成、token、timeout、config/env、dry-run、spawn、Redis、schedulerが混在する。
- 変更: 副作用を持たない次の関数を `meeting_api/bot_request/builders.py` へ抽出する。 `resolve_meeting_identity` `resolve_bot_timeouts` `build_meeting_data` `build_bot_config` `build_runtime_spec` 入力/戻り値はfrozen dataclass/Pydantic modelにし、global
  env読取はcallerで値を渡す。 field名、default、env文字列、secret redaction、JSON順非依存の内容をRF-00B goldenと一致させる。 元functionは新builderを呼ぶが、副作用順序はまだ変えない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-56`と`bash scripts/test/run-required-suites.sh RF-56`がexit 0。`test_bot_request_builders.py::test_standard_runtime_spec_golden` `::test_browser_runtime_spec_golden`
  `::test_agent_only_runtime_spec_golden` `::test_builders_do_not_access_db_redis_or_environment` RF-00B request golden差0。 `V-MEETING` suite=V-MEETING。
- リスク/戻し方: default評価時点の変化。env/clock/UUIDを明示引数にし、golden差で中断。 失敗時はR0。
- 依存: RF-10, RF-55
- コミット: `RF-56 extract pure bot request builders`

### RF-57 `request_bot`をmode strategyとcoordinatorへ分ける
- 対象: `services/meeting-api/meeting_api/meetings.py:722-1278` RF-56の `services/meeting-api/meeting_api/bot_request/{__init__,builders}.py:1-末尾` 新規 `services/meeting-api/meeting_api/bot_request/strategies.py:1-末尾` 新規
  `services/meeting-api/meeting_api/bot_request/coordinator.py:1-末尾` 新規 `services/meeting-api/tests/refactor/test_rf_57.py:1-末尾`
- 問題: standard、browser、agent-onlyの分岐と共有副作用が一関数に残る。
- 変更: `StandardBotStrategy`、`BrowserSessionStrategy`、`AgentOnlyStrategy`を作り、mode固有validate/specだけ担当。 `BotRequestCoordinator`は順序を `validate -> duplicate/limit check -> Meeting create -> token/config -> dry-run or runtime spawn ->
  Redis/scheduler -> response` に固定。 DB commit回数、spawn失敗時のMeeting行/status、Redis key、scheduler payloadを変更しない。 endpoint `request_bot`はrequest parse、strategy選択、coordinator call、responseだけにする。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-57`と`bash scripts/test/run-required-suites.sh RF-57`がexit 0。`test_bot_request_coordinator.py::test_standard_browser_agent_side_effect_order`
  `::test_runtime_spawn_failure_preserves_existing_failed_meeting_behavior` `::test_dry_run_has_no_runtime_redis_or_scheduler_side_effect` `::test_each_mode_selects_exactly_one_strategy` `request_bot`本体は150行以下、mode固有config生成0。
  `V-MEETING` suite=V-MEETING。
- リスク/戻し方: partial failure仕様が変わる。RF-00B call-order/golden差で中断。strategyを旧functionへ戻せる1commit。 失敗時はR0。
- 依存: RF-56
- コミット: `RF-57 split bot request strategies from orchestration`

### RF-58 Deferred transcriptionの外部依存をportsへ包む
- 対象: `services/meeting-api/meeting_api/final_transcription.py:1131-1590` 新規 `services/meeting-api/meeting_api/transcription_flow/__init__.py:1-末尾` 新規 `services/meeting-api/meeting_api/transcription_flow/ports.py:1-末尾` 新規
  `services/meeting-api/meeting_api/transcription_flow/adapters.py:1-末尾` 新規 `services/meeting-api/tests/refactor/test_rf_58.py:1-末尾`
- 問題: lease、録画、provider HTTP、DB、cache、Pub/Sub、Drive、voiceprintが460行の一関数に直結する。GitNexus impactはCRITICAL。
- 変更: 次のprotocolと現行実装adapterを追加し、元functionのcallsiteは変えず内部呼出しだけ置換する。 `FinalTranscriptionLease` `RecordingSourceResolver` `TranscriptionProviderClient` `TranscriptPersistence` `TranscriptPublisher` `PostCommitHook` portは既存key/HTTP
  payload/DB modelを変換せず包む。 Drive/voiceprintは`PostCommitHook`だが、現行のfailure isolationを個別に維持。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-58`と`bash scripts/test/run-required-suites.sh RF-58`がexit 0。`test_final_transcription_ports.py::test_each_external_dependency_is_called_through_one_port`
  `::test_adapter_payloads_match_characterization` `::test_lease_key_and_ttl_are_unchanged` RF-00B call-order/golden差0。 `V-MEETING` suite=V-MEETING。
- リスク/戻し方: CRITICAL blast radius。port追加以外の順序変更禁止。GitNexus direct caller全件をevidenceへ列挙。 失敗時はR0。
- 依存: RF-55
- コミット: `RF-58 wrap deferred transcription dependencies in ports`

### RF-59 Deferred transcriptionをcoordinatorとpost-commit hooksへ分ける
- 対象: `services/meeting-api/meeting_api/final_transcription.py:1131-1590` RF-58の `services/meeting-api/meeting_api/transcription_flow/{__init__,ports,adapters}.py:1-末尾` 新規
  `services/meeting-api/meeting_api/transcription_flow/coordinator.py:1-末尾` 新規 `services/meeting-api/tests/refactor/test_rf_59.py:1-末尾`
- 問題: 正常系とcleanup/error handlingが同じ関数に絡み、DB確定前後の副作用境界が読めない。
- 変更: `FinalTranscriptionCoordinator.run()`へ次の状態機械を実装する。 replace modeの旧segment削除と新segment insertは同一transaction。 lease喪失後はpersistenceしない。 cache/publish失敗とDrive/voiceprint失敗の扱いはRF-00B観測どおり分離。
  旧`run_deferred_transcription`署名は薄いcompatibility wrapperとして残す。
- 判断固定: DB commit成功後だけpost-commit hookを実行し、rollback時はpublish/export/voiceprintを呼ばない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-59`と`bash scripts/test/run-required-suites.sh RF-59`がexit 0。`test_final_transcription_coordinator.py::test_lease_loss_prevents_persistence`
  `::test_replace_mode_rollback_preserves_old_rows` `::test_commit_precedes_cache_publish_and_hooks` `::test_post_commit_hook_failure_does_not_revert_transcript` `::test_legacy_entrypoint_signature_is_preserved` `V-MEETING`
  suite=V-MEETING。
- リスク/戻し方: 最重要データ経路。segment text/speaker/timestamp/count、DB transaction、Redis eventに1差でもあれば中断する。失敗時はR0。
- 依存: RF-58
- コミット: `RF-59 extract deferred transcription coordinator`

### RF-60 Gemini境界処理のimmutable型とpure alignmentを抽出
- 対象: `services/transcription-service/gemini_adapter.py:1775-2613` 新規 `services/transcription-service/gemini_boundary/__init__.py:1-末尾` 新規 `services/transcription-service/gemini_boundary/types.py:1-末尾` 新規
  `services/transcription-service/gemini_boundary/alignment.py:1-末尾` 新規 `services/transcription-service/tests/refactor/test_rf_60.py:1-末尾`
- 問題: boundary token、overlap、speaker対応、fallback、logがmutable dict/listへ混在する。
- 変更: `gemini_boundary/types.py`へfrozen `BoundaryUnit`、`OverlapEdge`、`ConsumptionPlan`。 `gemini_boundary/alignment.py`へtokenization、normalized comparison、candidate scoring、speaker-compatible alignmentのpure helper。 provider
  call/prompt/logは元fileに残し、新型へ変換して既存plannerを呼ぶ。 normalization、timestamp rounding、Unicode処理をRF-00B goldenどおり維持。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-60`と`bash scripts/test/run-required-suites.sh RF-60`がexit 0。`test_gemini_boundary_types.py::test_models_are_immutable`
  `test_gemini_alignment.py::test_ascii_japanese_emoji_and_speaker_cases` `::test_alignment_is_deterministic` `::test_alignment_does_not_mutate_inputs` RF-00B Gemini golden差0。 `V-TRANSCRIPTION` suite=V-TRANSCRIPTION。
- リスク/戻し方: Unicode normalization差。入力/出力byte相当fixtureで差0を必須。 失敗時はR0。
- 依存: RF-00B
- コミット: `RF-60 extract immutable Gemini boundary primitives`

### RF-61 exact-boundary plannerを専用moduleへ移す
- 対象: `services/transcription-service/gemini_adapter.py:1775-2613` の `_plan_exact_boundary_stream_consumption` とhelper RF-60の `services/transcription-service/gemini_boundary/{__init__,types,alignment}.py:1-末尾` 新規
  `services/transcription-service/gemini_boundary/planner.py:1-末尾` 新規 `services/transcription-service/tests/refactor/test_rf_61.py:1-末尾`
- 問題: 839行のplannerがadapter内部状態・logging・fallbackと結合する。GitNexus impactはMEDIUMで85 symbol。
- 変更: `gemini_boundary/planner.py`へpure `plan_exact_boundary_stream_consumption(inputs, policy) -> ConsumptionPlan` を移す。 clock/log/provider configは引数のimmutable policyへ変換。 旧関数名は同じsignatureのwrapperとして残し、全callsiteを一度に変えない。 fallback
  branchとreason codeを全てgolden fixtureへ列挙する。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-61`と`bash scripts/test/run-required-suites.sh RF-61`がexit 0。`test_gemini_boundary_planner.py::test_boundary_plan_golden_matrix` `::test_every_fallback_reason_has_fixture`
  `::test_plan_is_deterministic` `::test_legacy_wrapper_matches_new_planner` `V-TRANSCRIPTION` suite=V-TRANSCRIPTION。
- リスク/戻し方: 85 symbol影響。wrapper identity/golden差0を確認。 失敗時はR0。
- 依存: RF-60
- コミット: `RF-61 extract the exact-boundary planner`

### RF-62 Gemini chunk mergeを専用moduleへ移す
- 対象: `services/transcription-service/gemini_adapter.py:2616-3228` RF-60/RF-61の `services/transcription-service/gemini_boundary/{__init__,types,alignment,planner}.py:1-末尾` 新規
  `services/transcription-service/gemini_boundary/merge.py:1-末尾` 新規 `services/transcription-service/tests/refactor/test_rf_62.py:1-末尾`
- 問題: 613行のmergeがtimestamp、speaker、text overlap、fallback、loggingを一体化する。
- 変更: `gemini_boundary/merge.py`へ `merge_chunk_segments(chunks, plan, policy)` を移す。 output segment modelは既存型を使用し、text、speaker、start/end、順序を変えない。 旧functionはwrapperとして残す。 invariant testとしてtimestamps
  nondecreasing、境界text非重複、同入力idempotentを追加。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-62`と`bash scripts/test/run-required-suites.sh RF-62`がexit 0。`test_gemini_merge.py::test_merge_golden_matrix` `::test_timestamps_are_monotonic`
  `::test_boundary_text_is_not_duplicated` `::test_merge_is_idempotent` `::test_legacy_wrapper_matches_new_merge` `V-TRANSCRIPTION` suite=V-TRANSCRIPTION。
- リスク/戻し方: silent transcript corruption。golden 1件でも差があれば中断。 失敗時はR0。
- 依存: RF-61
- コミット: `RF-62 extract Gemini chunk merging`

### RF-63 Transcription HTTP endpointを4責務へ分ける
- 対象: `services/transcription-service/main.py:274-599` 新規 `services/transcription-service/transcription_http/__init__.py:1-末尾` 新規 `services/transcription-service/transcription_http/request_validation.py:1-末尾` 新規
  `services/transcription-service/transcription_http/audio_preparation.py:1-末尾` 新規 `services/transcription-service/transcription_http/provider_dispatch.py:1-末尾` 新規
  `services/transcription-service/transcription_http/response_mapping.py:1-末尾` 新規 `services/transcription-service/tests/refactor/test_rf_63.py:1-末尾`
- 問題: request validation、audio decode/convert、semaphore、provider選択、response mappingが1endpointに混在。
- 変更: `request_validation.py`: multipart/parameter validationと既存HTTP error mapping。 `audio_preparation.py`: decode/format conversion、temp resource lifecycle。 `provider_dispatch.py`: semaphoreとprovider adapter選択。
  `response_mapping.py`: OpenAI互換responseへ変換。 endpointは4段階を呼ぶだけにし、multipart field、status code、error body、model名、retryを維持。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-63`と`bash scripts/test/run-required-suites.sh RF-63`がexit 0。`test_transcription_endpoint_contract.py::test_request_response_and_error_golden`
  `::test_temp_resources_are_cleaned_on_every_failure_stage` `::test_semaphore_wraps_provider_call_only` `::test_provider_selection_matches_baseline` endpoint本体150行以下。 `V-TRANSCRIPTION` suite=V-TRANSCRIPTION。
- リスク/戻し方: exception mappingとcleanupの変化。failure stage全fixtureでHTTP status/bodyを比較。 失敗時はR0。
- 依存: RF-60, RF-61, RF-62
- コミット: `RF-63 separate transcription endpoint responsibilities`

### RF-64 API Gatewayをpolicy・HTTP proxy・SSE・WS routerへ分ける
- 対象: `services/api-gateway/main.py:1-2667` 新規 `services/api-gateway/gateway/__init__.py:1-末尾` 新規 `services/api-gateway/gateway/policy.py:1-末尾` 新規 `services/api-gateway/gateway/http_proxy.py:1-末尾` 新規
  `services/api-gateway/gateway/sse_proxy.py:1-末尾` 新規 `services/api-gateway/gateway/ws_proxy.py:1-末尾` 新規 `services/api-gateway/gateway/routers/__init__.py:1-末尾` 新規
  `services/api-gateway/gateway/routers/{admin,meeting,calendar,agent,recordings,browser}.py:1-末尾` 新規 `tests3/unit/refactor/test_rf_64.py:1-末尾`
- 問題: 認証、route policy、HTTP proxy、SSE、WS、shared URLが単一moduleで、動的routeのimpactがgraphに出にくい。
- 変更: `gateway/policy.py`: RF-05Aのroute policy/identity。 `gateway/http_proxy.py`: header sanitization、timeout、HTTP streaming。 `gateway/sse_proxy.py`: Agent SSE。 `gateway/ws_proxy.py`: WebSocket。 `gateway/routers/*.py`:
  Admin/Meeting/Calendar/Agent/recordings/browser route登録。 `main.py`はapp/lifespan/router includeのみ。 dynamic path、methods、status、headers、timeout、body streamingをroute inventory goldenと一致させる。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-64`と`bash scripts/test/run-required-suites.sh RF-64`がexit 0。`test_gateway_route_inventory.py::test_method_path_policy_upstream_golden`
  `::test_no_authenticated_route_bypasses_policy` `test_gateway_imports.py::test_main_is_composition_only`
  `test_proxy_contract.py::{test_http_proxy_preserves_method_path_query_headers_body_and_status,test_sse_proxy_preserves_stream_events_and_disconnect_cleanup,test_ws_proxy_preserves_bidirectional_frames_close_code_and_auth_policy}`
  `main.py` 400行以下。 `V-BACKEND` suite=V-BACKEND。
- リスク/戻し方: dynamic route漏れ。OpenAPI + manual WS/SSE inventoryをbefore/after比較し、差1件で中断。 失敗時はR0。
- 依存: RF-05A, RF-05B, RF-05C, RF-05D2, RF-05E, RF-09B
- コミット: `RF-64 split gateway policy and transport routers`

### RF-65 Meeting ORM modelをshared packageへmoveしre-exportする
- 対象: `services/meeting-api/meeting_api/models.py:1-末尾` 新規 `libs/meeting-models/pyproject.toml:1-末尾` 新規 `libs/meeting-models/meeting_models/{__init__,models}.py:1-末尾` 新規
  `services/meeting-api/tests/fixtures/meeting-model-metadata.json:1-末尾` `libs/admin-models/admin_models/{__init__,models}.py:1-末尾` `services/admin-api/app/main.py:1-末尾` `services/calendar-service/app/{main,models,sync}.py:1-末尾`
  `services/{meeting-api,admin-api,calendar-service}/Dockerfile:1-末尾` `deploy/lite/Dockerfile.lite:1-末尾`
- 問題: model/Base/databaseが重複し、サービス分離と実依存が一致しない。先に移すとDB metadata driftが危険。
- 変更: 現在のtable、column、type、nullable、default、FK、constraint、index、naming conventionをsorted JSON snapshotへ固定。 `libs/meeting-models/meeting_models/models.py`へ定義をmove。 `meeting_api.models`は同一class objectをre-exportする。duplicate
  declarative definitionを残さない。 `libs/meeting-models/pyproject.toml`を同じcommitで作り、現時点で`meeting_api.models`を利用するMeeting/Admin/Calendar/Lite imageへCOPY/installする。次項目を待たずRF-65単体でclean build可能にする。 Alembic/schema migrationは作らない。
- 判断固定: 新packageを唯一の定義元とし、旧moduleは同じclass objectのre-exportだけにする。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-65`と`bash scripts/test/run-required-suites.sh RF-65`がexit 0。`test_meeting_model_metadata.py::test_metadata_snapshot_is_unchanged`
  `::test_legacy_imports_reference_same_classes_and_base` `::test_no_duplicate_table_definitions` `::test_schema_sync_is_noop_for_current_schema` Meeting/Admin/Calendar/Liteをclean buildし、各containerでlegacy/new importが同一class
  identityを返す。 `V-MEETING`, `V-BACKEND` suite=V-BACKEND,V-MEETING。
- リスク/戻し方: DB metadata driftは重大。snapshot差が1fieldでもあれば中断。migrationを追加しない。re-exportがあるため。 失敗時はR0。
- 依存: RF-55, RF-59, RF-63
- コミット: `RF-65 move meeting ORM models behind compatibility exports`

### RF-66A DB infrastructureをshared model packageへ移す
- 対象: `services/meeting-api/meeting_api/database.py:1-末尾` `libs/admin-models/admin_models/database.py:1-末尾` `services/calendar-service/app/main.py:12-14` 新規 `libs/meeting-models/meeting_models/database.py:1-末尾`
- 問題: pool/URL/session/destructive guardがMeeting/Adminへ分岐し、consumerのDB構成がservice package依存を生む。
- 変更: URL/pool/session/metadataとRF-02の`require_destructive_schema_permission()`を`meeting_models.database`へmoveする。 `meeting_api.database`と`admin_models.database`のlegacy public symbolは同一objectをre-exportする。 Meeting/Admin/Calendar
  consumerをshared importへ切り替え、URL組立、pool option、transaction境界をgolden一致させる。 RF-02の全破壊entrypointがmove後もguardを必ず通る。DB schema/migrationを変更しない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-66A`と`bash scripts/test/run-required-suites.sh RF-66A`がexit 0。`tests3/unit/test_service_boundaries.py::test_shared_database_configuration_matches_existing_urls_and_pool_settings`
  `services/admin-api/tests/test_database_guard.py::{test_destructive_entrypoints_require_explicit_permission,test_legacy_and_shared_guard_are_same_object}`
  `services/meeting-api/tests/test_meeting_model_metadata.py::test_metadata_snapshot_is_unchanged` `V-MEETING`, `V-BACKEND` suite=V-BACKEND,V-MEETING。
- リスク/戻し方: DB metadata/pool/guard drift。snapshotまたはguard testが1件でも違えば中断し、migrationで合わせない。前SHAから再実行。 失敗時はR0。
- 依存: RF-02, RF-65
- コミット: `RF-66A share database infrastructure without schema drift`

### RF-66B Pure schema/security utilityをmeeting-contractsへ移す
- 対象: `services/meeting-api/meeting_api/schemas.py:1-1364` `services/meeting-api/meeting_api/security_headers.py:1-52` `services/meeting-api/meeting_api/redaction.py:1-35` `services/meeting-api/meeting_api/webhook_url.py:1-138`
  `libs/meeting-contracts/meeting_contracts/__init__.py:1-末尾`（RF-10作成物） 新規 `libs/meeting-contracts/meeting_contracts/{schemas,security_headers,redaction,webhook_url}.py:1-末尾` `services/admin-api/app/main.py:1-末尾`
  `services/api-gateway/main.py:1-末尾`
- 問題: Admin/Gatewayがpure contract/security helperのためMeeting API application package全体をinstallする。
- 変更: DB/FastAPI routerに依存しないschema、security header、redaction、webhook URL validatorだけを`meeting_contracts`へmoveする。 旧Meeting moduleは同じclass/function objectをre-exportする。公開schema field/default/validation error/JSON schemaを変更しない。
  Admin/Gatewayを新package importへ切替え、RF-03B/RF-05Eのsecret/URL contractを再実行する。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-66B`と`bash scripts/test/run-required-suites.sh RF-66B`がexit 0。`tests3/unit/test_service_boundaries.py::test_pure_contract_modules_do_not_import_meeting_application`
  `::test_admin_gateway_use_meeting_contracts_directly` Meeting OpenAPI/schema snapshot差はRF-03A〜03C/RF-05A〜05H/RF-06A〜06H明示差だけ。 `V-MEETING`, `V-BACKEND`。 suite=V-BACKEND,V-MEETING。
- リスク/戻し方: Pydantic class identity/error文差。legacy/new import identityとJSON schemaをsnapshotし、差で中断。前SHAから再実行。 失敗時はR0。
- 依存: RF-10, RF-66A
- コミット: `RF-66B move pure meeting contracts behind compatibility exports`

### RF-66C Cross-service Meeting API package installを除去
- 対象: `services/calendar-service/Dockerfile:13-15` `services/admin-api/Dockerfile:15-17` `services/api-gateway/Dockerfile:1-末尾` `services/admin-api/app/main.py:1-末尾` `services/calendar-service/app/{main,models,sync}.py:1-末尾`
  `services/api-gateway/main.py:1-末尾` `deploy/lite/Dockerfile.lite:1-末尾`
- 問題: RF-65/66A/66B後もDockerfileがMeeting applicationを丸ごとCOPY/installすればservice境界とimageサイズ問題が残る。
- 変更: repo-wide static import testでAdmin/Calendar/Gatewayのproduction `meeting_api` importを0にする。 各DockerfileからMeeting API sourceの丸ごとCOPY/installだけを削除し、`meeting-models`/`meeting-contracts`を明示COPY/installする。 deploy/Liteのbuild
  contextも同じpackageを含める。lockfile/依存versionは変更しない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-66C`と`bash scripts/test/run-required-suites.sh RF-66C`がexit 0。`tests3/unit/test_service_boundaries.py::test_admin_calendar_gateway_do_not_import_or_install_meeting_api_package`
  exact command: `docker build -f services/meeting-api/Dockerfile -t rf66-meeting .` `docker build -f services/admin-api/Dockerfile -t rf66-admin .` `docker build -f services/calendar-service/Dockerfile -t rf66-calendar .` `docker
  build -f services/api-gateway/Dockerfile -t rf66-gateway .` 各image内`python -c`でshared import成功、`import meeting_api`はMeeting image以外失敗。 `V-MEETING`, `V-BACKEND`。 suite=V-BACKEND,V-MEETING。
- リスク/戻し方: clean build context/package metadata漏れ。4 imageのどれかが失敗したらcommitせず、前SHAから再実行。DB migrationなし。 失敗時はR0。
- 依存: RF-66B
- コミット: `RF-66C remove cross-service meeting application installs`

### Frontend・Bot Core・重複・文書

### RF-67 Dashboard API契約・mapper・表示statusを分離
- 対象: `services/dashboard/src/lib/api.ts:265-368` `services/dashboard/src/types/vexa.ts:1-4,350-430` `services/dashboard/src/lib/retranscription-status.ts:1-17` 新規 `services/dashboard/src/lib/api/contracts.ts:1-末尾` 新規
  `services/dashboard/src/lib/api/meeting-mapper.ts:1-末尾` 新規 `services/dashboard/src/lib/meeting-status.ts:1-末尾` 新規 `services/dashboard/tests/refactor/rf_67.test.ts:1-末尾`
- 問題: DTO、runtime mapping、表示設定が混在し、型fileがruntime helperをimportする逆依存がある。
- 変更: `src/lib/api/contracts.ts`: wire DTO型だけ。 `src/lib/api/meeting-mapper.ts`: DTOからdomain `Meeting`へのpure mapping。 `src/lib/meeting-status.ts`: status分類と表示model。 `types/vexa.ts`: type-only export。runtime import 0。 `vexaAPI`
  facadeとpublic call signatureは維持し、callsiteへ新module pathを直接広げない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-67`と`bash scripts/test/run-required-suites.sh RF-67`がexit 0。`test_meeting_mapper.test.ts::test_api_fixture_maps_to_domain_golden`
  `test_meeting_status.test.ts::test_status_and_retranscription_display_matrix` `test_import_boundaries.test.ts::test_type_modules_have_no_runtime_imports` `V-DASH` suite=V-DASH。
- リスク/戻し方: default/null normalization差。RF-00C fixtureのdeep equality差0。 失敗時はR4。
- 依存: RF-15, RF-16, RF-20
- コミット: `RF-67 separate dashboard API contracts and mapping`

### RF-68 TranscriptViewerのpure view modelを抽出し到達不能codeを削除
- 対象: `services/dashboard/src/components/transcript/transcript-viewer.tsx:121-389,752-896` 新規 `services/dashboard/src/lib/transcript-view-model.ts:1-末尾` 新規 `services/dashboard/tests/refactor/rf_68.test.ts:1-末尾`
- 問題: 表示計算と未使用AI/export処理が混在し、lint errorと変更影響を増やす。
- 変更: `buildTranscriptViewModel(input)` を `src/lib/transcript-view-model.ts` へ抽出し、speaker filter、literal search、active playback、confirmed/pending、timeline groupingをpure計算。 TypeScript
  compiler/eslint/`rg`で参照0のhandler/state/importだけ削除する。到達可能性が不明なcodeは削除しない。 UI markup、className、文言はこの項目で変更しない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-68`と`bash scripts/test/run-required-suites.sh RF-68`がexit 0。`test_transcript_view_model.test.ts::test_speaker_filter_literal_search_and_active_segment`
  `::test_confirmed_and_pending_view_model` `::test_multi_session_absolute_timeline` 削除symbolのrepo参照0。 RF-00C desktop/mobile screenshotのDOM role/text差0。 `V-DASH` suite=V-DASH。
- リスク/戻し方: callback経由の間接参照を見落とす。TypeScript/lint/build/E2E全通過を必須。 失敗時はR4。
- 依存: RF-12, RF-13, RF-14, RF-18, RF-19, RF-67
- コミット: `RF-68 extract transcript view modeling and remove dead paths`

### RF-69 TranscriptViewerの編集hookと表示componentを分割
- 対象: `services/dashboard/src/components/transcript/transcript-viewer.tsx:406-750,897-1486` RF-68の `services/dashboard/src/lib/transcript-view-model.ts:1-末尾` 新規 `services/dashboard/src/hooks/use-speaker-editing.ts:1-末尾` 新規
  `services/dashboard/src/hooks/use-voiceprint-selection.ts:1-末尾` 新規 `services/dashboard/src/components/transcript/transcript-toolbar.tsx:1-末尾` 新規 `services/dashboard/src/components/transcript/transcript-timeline.tsx:1-末尾` 新規
  `services/dashboard/tests/refactor/rf_69.test.ts:1-末尾`
- 問題: speaker編集、voiceprint、scroll、selection、toolbar、timelineが1,486行componentに集中する。
- 変更: `useSpeakerEditing(meetingId)`へspeaker rename/merge requestとgeneration guard。 `useVoiceprintSelection(meetingId)`へselection/API/error。 `TranscriptToolbar`へ検索/filter/action表示。 `TranscriptTimeline`へsegment
  list/pending/renderとcontainer scroll。 `TranscriptViewer`はhook接続、view model生成、compositionだけにし、独自polling loop/API URL生成を持たない。 props/event callback typeを明示し、既存DOM role/aria/text/classNameを維持。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-69`と`bash scripts/test/run-required-suites.sh RF-69`がexit 0。`test_speaker_editing.test.ts::test_rename_merge_success_error_and_stale_response`
  `test_voiceprint_selection.test.ts::test_selection_is_meeting_scoped` `test_transcript_components.test.ts::test_toolbar_and_timeline_contract` Viewer本体700行以下、`setInterval`/raw fetch 0。 visual regressionで意図しない差0。 `V-DASH`
  suite=V-DASH。
- リスク/戻し方: UI event propagation/focus差。DOM roleとkeyboard fixture、visual diffを必須にする。失敗時はR4。
- 依存: RF-16, RF-18, RF-68
- コミット: `RF-69 split transcript editing and presentation`

### RF-70 Meeting detailのaction modelとresponsive headerを共通化
- 対象: `services/dashboard/src/app/meetings/[id]/page.tsx:98-779,1032-1724` 新規 `services/dashboard/src/hooks/use-meeting-actions.ts:1-末尾` 新規 `services/dashboard/src/components/meetings/meeting-header.tsx:1-末尾` 新規
  `services/dashboard/tests/refactor/rf_70.test.ts:1-末尾`
- 問題: desktop/mobileでtitle更新・export actionが重複し、同じ操作の条件/feedbackがずれる。
- 変更: `useMeetingActions(meetingId)`へ `saveTitle`, `exportTranscript`, `retryPostMeeting`, `openProvider` を集約。 `MeetingActionModel`をdesktop/mobile両方の`MeetingHeader`へ渡す。 title API call implementationは1つ、同時saveはsingle-flight、late
  responseはRF-15 generation guard。 responsive markup差はheader component内のCSS/layoutだけとし、action callbacksは同一。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-70`と`bash scripts/test/run-required-suites.sh RF-70`がexit 0。`test_meeting_actions.test.ts::test_title_save_has_one_api_call` `::test_duplicate_save_is_single_flight`
  `::test_export_action_is_shared_by_desktop_and_mobile` `test_meeting_header.test.ts::test_desktop_and_mobile_receive_same_action_model` title更新API callsiteが1実装。 `V-DASH` suite=V-DASH。
- リスク/戻し方: mobile/desktop固有UIを失う。視覚baseline両viewportで比較し、actionだけ共通化。 失敗時はR4。
- 依存: RF-15, RF-17, RF-67
- コミット: `RF-70 unify meeting detail actions across layouts`

### RF-71 Meeting detailのplayback・Browser・TTS compositionを分割
- 対象: `services/dashboard/src/app/meetings/[id]/page.tsx:318-351,781-948,1725-2452` 既存（RF-20で追加済み） `services/dashboard/src/lib/browser-session-view-model.ts:1-末尾` 既存
  `services/dashboard/src/components/meetings/browser-session-view.tsx:1-末尾` 新規 `services/dashboard/src/hooks/use-meeting-playback.ts:1-末尾` 新規 `services/dashboard/src/components/meetings/meeting-playback-panel.tsx:1-末尾` 新規
  `services/dashboard/src/components/meetings/meeting-browser-session-panel.tsx:1-末尾` 新規 `services/dashboard/src/hooks/use-meeting-tts.ts:1-末尾` 新規 `services/dashboard/tests/refactor/rf_71.test.ts:1-末尾`
- 問題: recording取得、fragment mapping、post-meeting lifecycle、Browser view、TTS、JSXがroute componentへ集中する。
- 変更: `useMeetingPlayback(meetingId)`へrecording取得、fragment/session mapping、selected source。 `MeetingPlaybackPanel`へAudio/Video切替。 RF-20の`BrowserSessionViewModel`と既存`browser-session-view.tsx`をcompositionし、新規
  `src/components/meetings/meeting-browser-session-panel.tsx` に `MeetingBrowserSessionPanel`を作る。propsは `{model: BrowserSessionViewModel, onSave(): Promise<void>, onRetry(): void}` に固定し、desktop/mobileが同じcomponentを使う。
  `useMeetingTts`へTTS request/play/cancel。 Pageはroute param、store selector、hook接続、tab compositionだけにし、raw fetch、`setInterval`、VNC URL組立を持たない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-71`と`bash scripts/test/run-required-suites.sh RF-71`がexit 0。`test_meeting_playback.test.ts::test_recording_fragments_map_to_sessions` `::test_source_switch_resets_media_state`
  `test_meeting_tts.test.ts::test_cancel_and_meeting_switch_cleanup` `test_browser_session_routes.test.ts::meeting_browser_session_panel_uses_rf20_view_model_for_both_layouts` Page本体1,200行以下、`setInterval` 0、VNC URL builder 0、title
  API call 0。 desktop/mobile visual regressionの意図しない差0。 `V-DASH` suite=V-DASH。
- リスク/戻し方: composition時のmount/unmountでmediaがresetする。tab切替fixtureとE2Eで固定。 失敗時はR4。
- 依存: RF-17, RF-20, RF-21, RF-22, RF-23, RF-69, RF-70
- コミット: `RF-71 split meeting playback and session composition`

### RF-72 Vexa Bot coreのruntime stateをplatformから切り離す
- 対象: `services/vexa-bot/core/src/index.ts:1-80` `services/vexa-bot/core/src/platforms/shared/meetingFlow.ts:1-5` `services/vexa-bot/core/src/services/audio-pipeline.ts:62`
  `services/vexa-bot/core/src/platforms/googlemeet/recording.ts:1-末尾` `services/vexa-bot/core/src/platforms/msteams/recording.ts:1-末尾` `services/vexa-bot/core/src/platforms/zoom/strategies/recording.ts:1-末尾`
  `services/vexa-bot/core/src/platforms/zoom/web/recording.ts:1-末尾` 新規 `services/vexa-bot/core/src/runtime/runtime-state.ts:1-末尾` 新規 `services/vexa-bot/core/src/refactor-tests/rf_72.test.ts:1-末尾`
- 問題: `index.ts`がplatformをimportし、platform/serviceがindexのglobal getterをimportする循環。
- 変更: `src/runtime/runtime-state.ts`へcurrent page/browser/context/stop signal等のstate/getter/setterを移す。 stateは明示`createRuntimeState()`でinstance化し、test間global leakをなくす。 legacy getterはindexからre-exportするが定義を持たない。 platform/service
  importを`runtime-state`へ切替。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-72`と`bash scripts/test/run-required-suites.sh RF-72`がexit 0。`runtime-state.test.ts::isolates_two_runtime_instances` `::legacy_getters_reference_the_same_state`
  `import-graph.test.ts::platforms_do_not_import_core_index` clean processで各platform module単独import成功。 `V-CORE` suite=V-CORE。
- リスク/戻し方: singleton identity差。production compositionは1 instanceを共有し、testで確認。 失敗時はR0。
- 依存: RF-08, RF-09B
- コミット: `RF-72 separate bot runtime state from the entrypoint`

### RF-73 Vexa Bot coreをportsとlifecycle moduleへ分割
- 対象: `services/vexa-bot/core/src/index.ts:1-2830` `services/vexa-bot/core/src/platforms/shared/meetingFlow.ts:1-末尾` 新規 `services/vexa-bot/core/src/runtime/browser-launch.ts:1-末尾` 新規
  `services/vexa-bot/core/src/runtime/command-handler.ts:1-末尾` 新規 `services/vexa-bot/core/src/runtime/diagnostics.ts:1-末尾` 新規 `services/vexa-bot/core/src/runtime/shutdown.ts:1-末尾` 新規
  `services/vexa-bot/core/src/refactor-tests/rf_73.test.ts:1-末尾`
- 問題: platform起動、音声、Browser、command、diagnostics、shutdownがentrypointへ混在する。
- 変更: `meetingFlow`は `{hasStopSignal, triggerCamera, triggerChat, startVideo, enterFullscreen}` portsを引数で受ける。 `runtime/browser-launch.ts` `runtime/command-handler.ts` `runtime/diagnostics.ts` `runtime/shutdown.ts`
  `index.ts`はenv/config parse、composition、start/awaitのみ。 signal order、browser options、callback payload、shutdown orderをRF-00C/core goldenに一致。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-73`と`bash scripts/test/run-required-suites.sh RF-73`がexit 0。`meeting-flow-ports.test.ts::test_flow_uses_injected_ports` `shutdown.test.ts::test_shutdown_order_matches_baseline`
  `command-handler.test.ts::test_browser_save_protocol_and_legacy_compatibility` `import-graph.test.ts::test_core_production_graph_is_acyclic` `index.ts`500行以下。 `V-CORE` suite=V-CORE。
- リスク/戻し方: process signalとresource cleanupの順序差。fake browser/audio/runtimeでgolden固定。 失敗時はR0。
- 依存: RF-72
- コミット: `RF-73 split bot runtime lifecycle from the entrypoint`

### RF-74A Agent MessageBubbleを共通presentationへ移す
- 対象: `services/dashboard/src/components/agent/agent-chat.tsx:27-58` `services/dashboard/src/components/agent/meeting-agent-panel.tsx:33-64` 新規 `services/dashboard/src/components/agent/message-bubble.tsx:1-末尾` 新規
  `services/dashboard/tests/test_agent_message_bubble.test.ts:1-末尾`
- 問題: 同じbubble markupが2実装あり、message型とspacing差だけが局所的に混在する。
- 変更: 共通propsを `{role, content, timestamp?, pending?, density: "chat"|"panel"}` に固定する。 各callerの既存型はcaller内mapperで共通propsへ変換し、共通componentはAPI/domain型をimportしない。 `density`で現在のclassName/余白差を保持し、文言/role/ariaを変えない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-74A`と`bash scripts/test/run-required-suites.sh RF-74A`がexit
  0。`services/dashboard/tests/test_agent_message_bubble.test.ts::{test_chat_variant_matches_before_snapshot,test_panel_variant_matches_before_snapshot}` 旧local `MessageBubble`定義0、共通定義1。 `V-DASH`。 suite=V-DASH。
- リスク/戻し方: visual density差。両画面のsnapshot/E2E差があれば中断し、前SHAから再実行。 失敗時はR4。
- 依存: RF-69, RF-71
- コミット: `RF-74A share agent message bubble presentation`

### RF-74B OAuth state registry clientを共通化
- 対象: `services/dashboard/src/app/api/calendar/oauth/start/route.ts:1-末尾` `services/dashboard/src/app/api/calendar/oauth/complete/route.ts:1-末尾` `services/dashboard/src/app/api/zoom/oauth/start/route.ts:1-末尾`
  `services/dashboard/src/app/api/zoom/oauth/complete/route.ts:1-末尾` 新規 `services/dashboard/src/lib/server/oauth-state-registry.ts:1-末尾` 新規 `services/dashboard/tests/test_oauth_state.test.ts:1-末尾`
- 問題: RF-03B後も、認証cookieの転送、provider固定、state create/consume、PKCE challenge/verifier処理、error mappingがCalendar/Zoomの4 routeへ重複し、片側だけのsecurity修正を生む。
- 変更: server-only `createOAuthFlow(provider, request)`と`consumeOAuthFlow(provider, request, state)`へ、HttpOnly user token転送、Gateway registry request、response schema検証、PKCE S256、timeout、no-store、secret
  redaction、共通error分類をmoveする。providerはliteral union `calendar|zoom`で、caller body/stateから選ばない。 Calendar/Zoom固有authorize/token endpoint、client ID/secret、scope、purpose-specific Admin PATCHだけを各route adapterに残す。static signed
  state、email/user ID payload、共通secret fallbackを再導入しない。 helperはcreate responseの`state,code_challenge`とconsume responseの`subject_id,redirect_uri,return_to,pkce_verifier`をexact
  allow-listでparseし、provider/redirectがadapter定数と不一致ならprovider exchange前に失敗する。verifierはtoken exchange request構築後に上書き可能なBufferへ移し、response/log/errorへ出さない。 client bundleからserver helper importをimport-boundary testで禁止する。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-74B`と`bash scripts/test/run-required-suites.sh RF-74B`がexit
  0。`services/dashboard/tests/test_oauth_state.test.ts::{test_calendar_and_zoom_use_same_registry_client_with_provider_literal,test_tampered_expired_cross_subject_and_replayed_state_are_rejected_before_exchange,test_provider_redirect_and_pkce_mismatch_preserve_route_error_contract,test_registry_verifier_never_enters_browser_response_log_or_exception,test_server_helper_is_absent_from_client_bundle}`
  registry create/consume client実装1、旧signature parser/sign function 0、server-only boundary pass。 `V-DASH`。 suite=V-DASH。
- リスク/戻し方: provider固有validation消失。共通helperへauthorize/token endpointやscopeを入れずroute fixture差で中断し、失敗branchを保持してRF-74A直後の合格SHAから再実行する。 失敗時はR4。
- 依存: RF-67, RF-03B
- コミット: `RF-74B share OAuth state registry client`

### RF-74C Audio resampling helperを共通化
- 対象: `services/vexa-bot/core/src/services/audio.ts:223-251` `services/vexa-bot/core/src/utils/browser.ts:245-270` 新規 `services/vexa-bot/core/src/audio/resample.ts:1-末尾` 新規 `services/vexa-bot/core/src/audio/resample.test.ts:1-末尾`
- 問題: 同じFloat32 resampling algorithmが2実装へ分岐する。
- 変更: 既存の丸め、output length、sample interpolationを1文字単位でpure functionへmoveし、2 callerは同じhelperを呼ぶ。algorithm/qualityを変更しない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-74C`と`bash scripts/test/run-required-suites.sh RF-74C`がexit
  0。`services/vexa-bot/core/src/audio/resample.test.ts::{test_rate_matrix_matches_before_bytes,test_empty_single_sample_and_clipping_match_before_bytes}` `resampleAudioData` algorithm定義1。 `V-CORE`。 suite=V-CORE。
- リスク/戻し方: sample rounding差。byte差1個で中断し前SHAから再実行。 失敗時はR4。
- 依存: RF-73
- コミット: `RF-74C share bot audio resampling`

### RF-74D Float32-to-PCM変換を共通化
- 対象: `services/vexa-bot/core/src/services/raw-capture.ts:154,198-207` `services/vexa-bot/core/src/services/recording.ts:71,453-462` 新規 `services/vexa-bot/core/src/audio/pcm.ts:1-末尾` 新規
  `services/vexa-bot/core/src/audio/pcm.test.ts:1-末尾`
- 問題: clamp/scaling/endian変換が2実装へ重複する。
- 変更: 現行clamp `[-1,1]`、negative/positive scaling、little-endian writeをpure helperへmoveし2 callerを切替える。Buffer ownership/copy回数以外のbyte列を変えない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-74D`と`bash scripts/test/run-required-suites.sh RF-74D`がexit
  0。`services/vexa-bot/core/src/audio/pcm.test.ts::{test_edge_float_matrix_matches_before_bytes,test_output_is_little_endian}` PCM conversion定義1、`V-CORE`。 suite=V-CORE。
- リスク/戻し方: overflow/endian差。全byte golden差で中断。 失敗時はR4。
- 依存: RF-73
- コミット: `RF-74D share bot PCM conversion`

### RF-74E Meeting duration表示を共通化
- 対象: `services/dashboard/src/app/meetings/[id]/page.tsx:1142` `services/dashboard/src/components/meetings/meeting-card.tsx:108` 新規 `services/dashboard/src/lib/meeting-duration.ts:1-末尾` 新規
  `services/dashboard/tests/test_meeting_duration.test.ts:1-末尾`
- 問題: 分単位durationの同一表示がPage/Cardに重複する。`src/lib/export.ts`は開始/終了時刻入力で別契約のため対象外。
- 変更: Page/Cardの現在の0/分/時間境界と日本語文言をpure helperへmoveする。`export.ts`は変更しない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-74E`と`bash scripts/test/run-required-suites.sh RF-74E`がexit
  0。`services/dashboard/tests/test_meeting_duration.test.ts::{test_page_and_card_duration_matrix_match_before_strings,test_export_duration_contract_is_unchanged}` 分単位`formatDuration`定義1、export用定義は意図的に残る。 `V-DASH`。 suite=V-DASH。
- リスク/戻し方: 別signatureのexport helperを誤統合する危険。対象外file差分があれば中断。 失敗時はR4。
- 依存: RF-70
- コミット: `RF-74E share meeting duration display`

### RF-74F Meeting fetchのtransient判定を共通化
- 対象: `services/dashboard/src/lib/api.ts:66-78` `services/dashboard/src/stores/meetings-store.ts:29-41` 新規 `services/dashboard/src/lib/transient-meeting-error.ts:1-末尾` 新規
  `services/dashboard/tests/test_transient_meeting_error.test.ts:1-末尾`
- 問題: retry可否predicateがAPI/storeへ重複し、status/network errorの片側driftを生む。
- 変更: 両実装へ同じfixtureを先に当て完全一致を確認し、同じ場合だけ共通pure predicateへmoveする。1caseでも差があれば統合せず本項目を停止して計画修正を求める。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-74F`と`bash scripts/test/run-required-suites.sh RF-74F`がexit
  0。`services/dashboard/tests/test_transient_meeting_error.test.ts::{test_api_and_store_matrix_match_before_results,test_shared_predicate_preserves_all_statuses}` transient meeting fetch predicate定義1、`V-DASH`。 suite=V-DASH。
- リスク/戻し方: retry増減。fixture差を「改善」として更新せず中断。 失敗時はR4。
- 依存: RF-15
- コミット: `RF-74F share transient meeting fetch classification`

### RF-74G Meeting status正規化validatorを共通化
- 対象: `services/meeting-api/meeting_api/schemas.py:932,1198` 新規 `services/meeting-api/meeting_api/status_normalization.py:1-末尾` 新規 `services/meeting-api/tests/test_status_normalization.py:1-末尾`
- 問題: Pydantic validator内の同一status normalizationが2定義へ重複する。
- 変更: pure `normalize_status_value`へ現行case/alias/null/type/errorをmoveし、両validatorはdelegateする。field名/error locationは各validator側で維持する。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-74G`と`bash scripts/test/run-required-suites.sh RF-74G`がexit
  0。`services/meeting-api/tests/test_status_normalization.py::{test_all_status_inputs_match_before_values,test_validation_error_json_matches_before}` normalization algorithm定義1、`V-MEETING`。 suite=V-MEETING。
- リスク/戻し方: validation error path差。値だけでなくerror JSON snapshot差で中断。 失敗時はR4。
- 依存: RF-63, RF-66B
- コミット: `RF-74G share meeting status normalization`

### RF-74H 参照されないtranscript package archiveを削除
- 対象: `services/dashboard/vexaai-transcript-rendering-0.2.0.tgz:1-末尾`（binary全体）
- 問題: Dashboardが使用するpackageは0.4.1系なのに0.2.0 archiveが残り、手動install対象に見える。
- 変更: `git grep -n "vexaai-transcript-rendering-0.2.0.tgz"`、package manifests、Dockerfile、workflow、docsで参照0を確認する。 `packages/transcript-rendering/package.json`とDashboardのresolved package versionが0.2.0ではないことを証拠化する。 archive
  1fileだけを削除する。他のpackage/cache/lockfileは変更しない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-74H`と`bash scripts/test/run-required-suites.sh RF-74H`がexit 0。削除前のrepo参照0。 削除後 `test ! -e services/dashboard/vexaai-transcript-rendering-0.2.0.tgz`。 commit前 `git diff --cached
  --name-only` が当該1fileだけ、commit後 `git show --pretty= --name-only HEAD` が当該1fileだけ。 `V-TRANSCRIPT`, `V-DASH`。 suite=V-DASH,V-TRANSCRIPT。
- リスク/戻し方: undocumented manual installが使う可能性。repo内参照0とrelease docsを確認し、外部配布の証拠があれば削除せず中断。失敗branchは保持し、前SHAから新worktreeで再実行する。 失敗時はR4。
- 依存: RF-71
- コミット: `RF-74H remove the unreferenced transcript package archive`

### RF-74I 恒久redirect配下のDashboard docs sourceを削除
- 対象: `services/dashboard/next.config.ts:61-80` `services/dashboard/src/app/docs/**:1-末尾`
- 問題: `/docs*`は外部へ恒久redirectする一方、同routeのsource treeが残り、どちらが正規docsか誤解させる。
- 変更: `next.config.ts`のredirect対象を全docs routeへ展開したfixtureを作る。 repo内link/import/navigationで`src/app/docs/**`のcomponentを直接参照していないことを確認する。 desktop/mobile E2Eで各代表route `/docs`, `/docs/auth`, `/docs/rest/meetings`, `/docs/ws/events`
  が期待する外部host/pathへ308 redirectすることを確認する。 `src/app/docs/**`だけを削除し、redirect設定は維持する。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-74I`と`bash scripts/test/run-required-suites.sh RF-74I`がexit 0。`test_docs_redirects.test.ts::test_all_removed_docs_routes_are_permanently_redirected`
  `::test_redirect_target_preserves_required_subpath` source削除後にrepo内direct import/link 0。 `V-DASH`。 suite=V-DASH。
- リスク/戻し方: redirect対象外の隠れroute削除。route inventoryに1件でもredirectなしがあれば削除せず中断。失敗branchは保持し、前SHAから再実行する。 失敗時はR4。
- 依存: RF-71
- コミット: `RF-74I remove dashboard docs sources behind permanent redirects`

### RF-75A CommonJS検証scriptと未escape JSXをlint準拠へする
- 対象: `services/dashboard/{agent-flow,agent-inspect,auth-validate-final,auth-validate,auth-validate2,auth-validate3,check-pages,deliver-validate,feature-validate}.js:1-末尾` 新規
  `services/dashboard/{agent-flow,agent-inspect,auth-validate-final,auth-validate,auth-validate2,auth-validate3,check-pages,deliver-validate,feature-validate}.mjs:1-末尾` RF-74I後に残る`react/no-unescaped-entities`違反は、RF-00C lint
  baselineが示す各`relative_file:line-end_line` 新規 `services/dashboard/scripts/check-lint-cluster.mjs:1-末尾` 新規 `.pipeline/evidence/$TASK/lint/rf-75a.json:1-末尾`
- 問題: baselineの`@typescript-eslint/no-require-imports` 9件と`react/no-unescaped-entities` 26件が、ad-hoc script形式とJSX source表現に集中する。
- 変更: 9 scriptを同名`.mjs`へ`git mv`し、`require("playwright")`をstatic `import { chromium } from "playwright"`へ置換する。CLI argv/exit/output/Playwright操作は変更せず、repo内参照を新pathへ同期する。 RF-74Iで消えずに残ったJSXは、表示文字列を変えない`{"'"}`/entity表現だけで修正する。 cluster
  checkerはESLint JSONを読み、対象2 ruleが0、他rule signature/件数がRF-00C baselineより増えていないことを検査する。さらにsorted `entries[{relative_file,line,end_line,rule_id,message,severity}]`, `source_files_sha256[{relative_file,sha256}]`,
  `eslint_config_sha256`, error/warning総数を`.pipeline/evidence/$TASK/lint/rf-75a.json`へatomic writeする。eslint config/ignoreは変更しない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-75A`と`bash scripts/test/run-required-suites.sh RF-75A`がexit
  0。`services/dashboard/tests/refactor/rf_75a.test.ts::{test_nine_mjs_cli_contracts_match_before,test_lint_cluster_checker_rejects_target_rules_and_preserves_others}` `node services/dashboard/scripts/check-lint-cluster.mjs --rules
  @typescript-eslint/no-require-imports,react/no-unescaped-entities --expect 0` がexit 0。 `rf-75a.json`の全`source_files_sha256`がcommit後fileと一致し、entryはfile/line/rule順、対象2 rule entry 0。 9 `.mjs --help`またはmock Playwright
  startupのbefore/after exit/output一致。 `V-DASH`。 suite=V-DASH。
- リスク/戻し方: ad-hoc script invocation path変更。repo参照とREADMEを同commitで更新し、外部利用証拠があれば中断。前SHAから再実行。 失敗時はR4。
- 依存: RF-74I
- コミット: `RF-75A close dashboard module and JSX lint errors`

### RF-75B Dashboardのunsafe type lintを解消
- 対象: `.pipeline/evidence/$TASK/lint/rf-75a.json:1-末尾`内の `@typescript-eslint/no-explicit-any`, `@typescript-eslint/no-empty-object-type`, `prefer-const`
  entryが示す`relative_file:line-end_line`だけ。実装前に全`source_files_sha256`と現在fileの一致を検証し、件数はRF-75A実測値を使う。対象外file/ruleを同commitで変更しない。
- 問題: transport/UI境界が`any`と空object型で型検査を迂回する。
- 変更: 外部入力は`unknown`で受け、type guard/Zod既存schema/判別unionでnarrowする。`as any`、eslint disable、広いindex signatureへ置換しない。 空object型は実意図が「追加propsなし」ならtype aliasを削除し親型を直接使用、辞書なら具体key/value型を定義する。
  `prefer-const`は再代入がないbindingだけ`const`へする。runtime挙動変更を混ぜない。 同じcheckerで修正後inventoryを`.pipeline/evidence/$TASK/lint/rf-75b.json`へ書き、次項目の唯一の対象表にする。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-75B`と`bash scripts/test/run-required-suites.sh RF-75B`がexit
  0。`services/dashboard/tests/refactor/rf_75b.test.ts::{test_unknown_inputs_are_narrowed_without_runtime_default_changes,test_lint_cluster_has_zero_unsafe_type_rules}` cluster checkerで3 rule件数0、他rule増加0。
  `rf-75b.json`の全`source_files_sha256`がcommit後fileと一致し、対象3 rule entry 0。 対象mapper/API/component testはitem runner、TypeScript検査とDashboard回帰は`V-DASH`だけが実行し、全command exit 0。本文から`npx`やraw test commandを追加実行しない。 suite=V-DASH。
- リスク/戻し方: 型を満たすためruntime defaultを変える危険。cast削除だけで済まない挙動差が必要なら中断し新IDを起票。 失敗時はR4。
- 依存: RF-67, RF-75A
- コミット: `RF-75B replace unsafe dashboard lint types`

### RF-75C React hook lifecycle lintを挙動修正で閉じる
- 対象: `.pipeline/evidence/$TASK/lint/rf-75b.json:1-末尾`内の `react-hooks/set-state-in-effect`, `react-hooks/immutability`, `react-hooks/exhaustive-deps`
  entryが示す`relative_file:line-end_line`だけ。実装前に全`source_files_sha256`と現在fileの一致を検証し、対象外file/ruleを同commitで変更しない。
- 問題: effect内同期state、mutation、不完全dependencyがstale stateや再接続漏れを隠す。disable追加では閉じられない。
- 変更: derived stateはrender時pure計算、外部subscription stateはcontroller callback、prop変更resetはkey/reducer eventへ移す。 mutable valueはcopy-on-writeまたはrefへ移し、既存objectを直接変更しない。
  dependencyは全参照を列挙し、無限loopになるcallbackは`useCallback`/controllerへ安定化する。eslint disableを増やさず、既存disableも対象scopeで0にする。 修正後inventoryを`.pipeline/evidence/$TASK/lint/rf-75c.json`へ書き、次項目の唯一の対象表にする。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-75C`と`bash scripts/test/run-required-suites.sh RF-75C`がexit
  0。`services/dashboard/tests/refactor/rf_75c.test.ts::{test_verify_page_strict_mode_has_no_duplicate_transition,test_decisions_reconnect_cleans_listener_and_timer,test_video_imperative_api_preserves_contract,test_meeting_switch_discards_stale_effect}`
  cluster checkerで3 rule件数0。 `rf-75c.json`の全`source_files_sha256`がcommit後fileと一致し、対象3 rule entry 0。 verify page、Decisions reconnect、Video imperative API、Meeting switchのfake timer/DOM testがpass。 `V-DASH`。 suite=V-DASH。
- リスク/戻し方: effect timing/再接続差。StrictMode二重mount fixtureとtimer 0を確認し、差があれば中断。 失敗時はR4。
- 依存: RF-15, RF-22, RF-23, RF-75B
- コミット: `RF-75C make dashboard hook lifecycles explicit`

### RF-75D Unused/no-img warningをcode側で0にする
- 対象: `.pipeline/evidence/$TASK/lint/rf-75c.json:1-末尾`内の `@typescript-eslint/no-unused-vars`, `@next/next/no-img-element` entryが示す`relative_file:line-end_line`だけ。実装前に全`source_files_sha256`と現在fileの一致を検証し、対象外file/ruleを同commitで変更しない。
- 問題: 大量のunused symbolがdead code判定を妨げ、残るraw imageがNext image policyを迂回する。
- 変更: symbolごとにTypeScript/`rg`/buildで参照0を確認し、import、local、引数を削除する。public export、side-effect import、reflection/string参照は削除しない。 unused parameterをinterface互換で残す必要がある場合だけ`_name`へ変更し、ruleが許容する既存設定に合うことを確認する。 raw
  imageは既存layout/sizeを維持して`next/image`へ切替える。data/blob等で非対応なら当該1箇所だけ、理由/test付き既存例外へ寄せるがglobal ruleを無効化しない。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-75D`と`bash scripts/test/run-required-suites.sh RF-75D`がexit 0。`node services/dashboard/scripts/check-lint-cluster.mjs --all --expect-errors 0 --expect-warnings 0` がexit
  0、stdoutに`errors=0 warnings=0`。第三者plugin理由でwarningが残る選択肢は認めない。 `V-DASH-FINAL`。 suite=V-DASH-FINAL。
- リスク/戻し方: dynamic/side-effect参照削除。参照0とbuild/E2Eが揃わないsymbolは削除せず中断。 失敗時はR4。
- 依存: RF-68, RF-74A, RF-75C
- コミット: `RF-75D close remaining dashboard lint warnings`

### RF-75E Compatibility/structure budget gateを追加
- 対象: 新規 `tests3/unit/test_structure_budget.py:1-末尾` 新規 `tests3/structure-budget.json:1-末尾` 新規 `tests3/compatibility-contracts.json:1-末尾`
- 問題: 分割後の循環、runtime type import、missing test、無期限compat wrapperが再発してもfinal suiteに単一gateがない。
- 変更: production import cycle 0、type module runtime import 0、active missing test 0、Dashboard lint error/warning 0、README ownership missing 0をmachine checkする。 compatibility wrapper/re-exportは `owner`, `introduced_in`,
  `remove_after`, `removal_condition` metadataを必須にし、期限は日付ではなく全consumer移行等の検証可能条件にする。 TranscriptViewer 700行、Meeting page 1,200行、core index 500行の上限を構造budgetへ固定し、空白圧縮/複数statement 1行化を禁止するformat checkを付ける。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-75E`と`bash scripts/test/run-required-suites.sh RF-75E`がexit
  0。`tests3/unit/test_structure_budget.py::{test_production_import_cycles_are_zero,test_compatibility_metadata_is_complete,test_file_size_budgets_and_format_rules_pass,test_active_tests_and_docs_owners_exist}` `V-OPS`,
  `V-DASH-FINAL`, `V-TRANSCRIPT`, `V-CORE`, `V-MEETING`, `V-BACKEND`, `V-TRANSCRIPTION`。 suite=V-BACKEND,V-CORE,V-DASH-FINAL,V-MEETING,V-OPS,V-TRANSCRIPT,V-TRANSCRIPTION。
- リスク/戻し方: 過度に脆い行数/graph gate。既存最終構造を測定したうえで計画値を緩めず、達成不能なら中断し計画reviewへ戻す。 失敗時はR4。
- 依存: RF-51, RF-64, RF-66C, RF-69, RF-71, RF-73, RF-74G, RF-75D
- コミット: `RF-75E enforce final compatibility and structure budgets`

### RF-75F Human-facing文書を実装済み構造へ同期
- 対象: `services/README.md:1-141` `docs/refactoring-execution-plan.md:1-334` `MANIFEST.md:1-227` `docs/README.md:1-45` `tests3/README.md:1-末尾` `deploy/README.md:1-末尾` `deploy/compose/README.md:1-末尾` `deploy/helm/README.md:1-末尾`
  `deploy/lite/README.md:1-末尾` `docs/harness-guide.md:1-末尾` `docs/managed-agent-harness-architecture.md:1-末尾`
- 問題: 旧bot-manager、future branch、消えたtest、修正前のservice依存を現行仕様のように読める。
- 変更: `services/README.md`をRF-65/66後の実import/image dependencyへ更新する。 旧実行計画へ「履歴資料・現行実行指示ではない」と対象commit/期間を追記する。 `MANIFEST.md`へ「future targetでcurrent mainのbinding contractではない」と明記し、未来内容を実装済みへ書き換えない。 tests3/deploy/Harness
  docsをRF-31〜51の実command/status/registry/catalogへ、docs owner表をRF-51へ同期する。
- 完了条件: `bash scripts/test/run-refactor-item.sh RF-75F`と`bash scripts/test/run-required-suites.sh RF-75F`がexit
  0。`tests3/unit/refactor/test_rf_75f.py::{test_human_docs_reference_only_existing_current_files_and_commands,test_planned_and_implemented_language_is_not_conflated,test_docs_check_and_structure_metadata_pass}`
  `tests3/unit/test_structure_budget.py::{test_compatibility_metadata_is_complete,test_active_tests_and_docs_owners_exist}` `V-OPS`, `V-HARNESS-CONTRACT`。 suite=V-HARNESS-CONTRACT,V-OPS。
- リスク/戻し方: 文書が先行/過剰主張する危険。最終HEADで実在するfile/command/testだけ記載し、plannedとimplementedを分ける。前SHAから再実行。 失敗時はR4。
- 依存: RF-51, RF-66C, RF-74I, RF-75E
- コミット: `RF-75F synchronize final architecture and operations documentation`

## 6. マイルストーンと最終検証

### M0: RF-00D

- RF-00A〜RF-00Dのitem commandがすべてpass。
- baseline test、18 screenshot、既知lint、registry 91件/不存在45件を再現可能なfixtureへ固定。
- ユーザー所有の既存差分が変更されていない。

### M1: RF-30

- RF-01〜RF-30のitem commandとstable-unique required suiteがpass。
- D1〜D6のmerge/drainを順番どおり完了してからlegacy削除項目をmergeしている。
- 認証subject、credential、Runtime resource、Browser transport、scheduler、polling、WebSocketの固有testがpass。
- `M0_SHA`をM0完了commitへ設定し、`bash scripts/test/run-gitnexus-refactor.sh analyze --force && bash scripts/test/run-gitnexus-refactor.sh detect-changes --base-ref "$M0_SHA"`が計画内processだけを報告。

### M2: RF-51

- RF-31〜RF-51のitem command、V-OPS、V-HARNESS-CONTRACT、CIがpass。
- registry active entry不存在0、0件/all-skip/timeoutの偽陽性0。
- Compose/Lite/Helm/Deploy/Harnessの検査が実際にcommandを実行し、失敗を非0で返す。
- tracked wrapperの`detect-changes`とPR reviewでPhase 2範囲外変更0。

### M3: RF-75F

次をrepo rootで実行し、すべてexit 0にする。

```bash
bash scripts/test/run-full-refactor-verification.sh
git diff --check
bash scripts/test/run-gitnexus-refactor.sh analyze --force
bash scripts/test/run-gitnexus-refactor.sh detect-changes --base-ref main
```

さらに以下を満たす。

- 129項目が見出し順どおり1項目1コミットで存在し、subjectが各項目の`コミット`欄と一致。
- 全item command、required suite、CI、build、lintがpass。0件、skip、xfail、missing testなし。
- RF-00Cと同じ具象fixture/Playwright specでfinal 18枚を取得。意図外pixel差、console/page/network error、個人情報、実tokenは0。
- D1〜D6境界とD7 laneを構成する各PRのreview/人間mergeが完了し、D1〜D6のdrain記録が存在。
- 公開API、status code、JSON、Redis key、DB metadata、transaction、provider設定の意図外差分0。
- `pr-ready-gate.sh`が利用可能なら既存通常gateとして実行する。不存在またはbrokenなら実装結果を保持して`blocked: current project PR-ready hook is unavailable`と報告し、PR-readyまたは完遂を主張しない。実装AIが外部symlinkを修復したり別pathへfallbackしたりしない。

## 7. やらないこと

- 新機能、UX刷新、DB destructive migration、provider/model/prompt変更。
- 依存library、lockfile、base imageの善意更新。
- 削除済み`features/`、過去sidecar、古いdocs実装の復元。
- 複数RF項目の1コミット化、squashで項目境界を消すこと。
- testの削除、assertion緩和、skip/xfail追加で合格させること。
- 対象外production/config fileの便乗修正、repo全体format。
- 実credential、個人情報、production dataをfixture/screenshot/logへ保存すること。
- production配備、credential revoke、drain結果を実行AIが自己申告すること。
- visual fixture生成用の独自言語、JSONの独自正規化規約、画像ごとの証明protocolを追加すること。
- 通常のCI/PR review以外に、追加の承認・証跡・process crash対策protocolを導入すること。
- brokenな外部`.claude` symlinkを実装AIがswitch/resetし、別pathへ暗黙fallbackすること。

## 8. 実行者への指示文

以下をそのまま実行者へ渡す。

> この計画を上から順に実行してください。1項目ずつ実施し、1項目ごとに指定subjectでコミットしてください。各項目は2.1のwrite union外へ変更を広げないでください。RF-00A/RF-00Nだけは2.2のbootstrap例外に従い、それ以外は変更前にtracked GitNexus wrapperの`impact`を確認し、HIGH/CRITICALまたは計画外processなら編集前に報告してください。項目固有test、required suite、`git diff --check`、wrapperの`detect-changes`を通してからコミットしてください。完了条件を一つでも満たせなければ後続項目へ進まず、失敗diffとログを保持して報告してください。D1〜D6のdrain待ちは正常な停止です。production配備、credential revoke、証跡の捏造は行わないでください。全項目後はM3の最終検証、PR review、人間mergeまでを完了条件としてください。

## 9. 計画整合性の確認

計画更新時は次を実行する。

```bash
test "$(rg -c '^### RF-' .pipeline/plans/full-repo-refactoring-2026-07-24-v2/plan.md)" -eq 129
diff -u \
  <(rg '^### RF-' .pipeline/plans/full-repo-refactoring-2026-07-24/plan.md | sed 's/^### //') \
  <(rg '^### RF-' .pipeline/plans/full-repo-refactoring-2026-07-24-v2/plan.md | sed 's/^### //')
```

各RF blockに`対象`、`問題`、`変更`、`完了条件`、`リスク/戻し方`、`依存`、`コミット`が各1件あること、依存先が先行項目であること、commit subject重複0も確認する。
