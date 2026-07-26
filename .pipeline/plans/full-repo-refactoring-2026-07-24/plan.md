# generic_tldv 全体リファクタリング実行計画

- Task ID: `full-repo-refactoring-2026-07-24`
- 基準ブランチ: `main`
- 調査時 HEAD: `b2bcae8e88f0e73fe95343ee3a694a3afc4e1028`
- 規模判定: `L`
- この文書の目的: この文書とリポジトリだけを渡された実行者が、追加の設計判断をせず、1項目1コミットで安全に完遂できるようにする
- 対象外: この計画作成ターンではソース、テスト、設定を1文字も変更しない

## 0. 結論と実行方針

本件の本丸は「大きいファイルが多い」ことだけではない。先に直すべきなのは、認証主体の取り違え、秘密値の露出、Redis Pub/Sub応答の取り逃がし、非同期競合、そして「テストを実行していないのに合格する」検証基盤である。

したがって、実装順は次に固定する。

```text
安全網
  -> 認可・秘密値・破壊操作・path containment
  -> 非同期競合・順序・誤成功
  -> tests3 / deploy / Harness の fail-closed 化
  -> 契約を維持した backend move-only 抽出
  -> frontend / bot core の move-only 抽出
  -> デッド資産・重複・lint・文書の閉鎖
```

巨大関数や巨大コンポーネントを先に分割してはいけない。現状のバグを複数ファイルへ拡散し、修正時の責任境界をさらに曖昧にするためである。

互換導入とcredential削除を1本の未merge branchへ積み、途中commitをproductionへ直接出してdrainしてはいけない。本計画は次の7 releaseを順番に実行するmaster planである。各releaseの初回attemptはその時点の`origin/main`から新しい`codex/full-repo-refactoring-2026-07-24-rN` branchを作り、表のRF見出しだけを1項目1コミットで実装する。失敗後の共通retry protocolだけは`codex/<release-task-id>-retry-<failed-id>-<token>`を使い、hash-bound state-transfer manifestをrelease manifestへ記録する。このretry branchも同じreleaseとしてfirst-parent直線履歴、cherry-pick/rebase/merge commit/revertは禁止する。

| Release | `release-boundaries.json`のstart/end（間の`### RF-*`を見出し順に機械展開） | merge後の必須停止点 |
|---|---|---|
| R1 | RF-00A, RF-00N, RF-00E, RF-00B, RF-00C, RF-00D, RF-01〜RF-05B | hash-bound QA・人間承認・PR merge・認可済み配備後、OP-05A-DRAIN |
| R2 | RF-05C, RF-05D1, RF-05D1B | 同じrelease closure後、OP-05D-DRAIN |
| R3 | RF-05C2, RF-05D2〜RF-06C1 | 同じrelease closure後、OP-06C-DRAIN |
| R4 | RF-05F2, RF-05G2, RF-06C2, RF-06D1 | 同じrelease closure後、OP-06D-DRAIN |
| R5 | RF-06D2〜RF-06F | 同じrelease closure後、OP-06F-DRAIN |
| R6 | RF-06G〜RF-09A | 同じrelease closure後、OP-09-DRAIN |
| R7 | RF-09B〜RF-75F | 全フェーズgateと最終closure。新たなcompatibility drainなし |

各R1〜R6は表の末尾commitで9章のtest/evidence/tribunal/post-review/独立QA/outcomeをそのreleaseの`RELEASE_BASE_SHA..RELEASE_HEAD_SHA`へ実行し、人間承認とPR mergeが完了するまで次へ進まない。認可済みoperatorはmerge済みexact `merged_main_sha`だけを、approval/merge/operationで一致した`target_environment_id`とnonsecret deployment fingerprintへ配備し、drain artifactの`component_source_sha`をそのSHA、`expected_commit`を`release-boundaries.json.operator_expected_item`のitem commit SHAへbindする。R3〜R7のdecommission deploy直前は前release gateを流用せず、current merged SHAへbindした15分以内のfresh cutover gateとcontinuous freezeを再検証する。`origin/main`がmerge結果を指し、operator gateがpassした後にだけ次releaseをbranchする。R2以降はplan external review hashを再利用できるが、`origin/main`差分と前release evidence/operation gateを新しいrelease baselineへ記録する。final commit history verifierは1本の旧baseを期待せず、7個の`release-manifest.json`それぞれでbase/head/item subject順を検査し、その連結item集合が全RF見出しとexact一致することを要求する。

実装完遂にはコードだけでは満たせない運用前提がある。6つのOP gateはmerge済み互換実装を認可済み人間operatorが配備・観測した証拠を必須とする。実行AIはproductionへ配備せず、証拠を捏造せず、各停止点で待つ。証拠が提供されない場合の正しい終了状態は、merge済み互換releaseとlocal検証を保全した`blocked: operator evidence pending`である。decommission itemを飛ばして完遂扱いにしてはいけない。

`release-boundaries.json`がrelease境界のauthoritative sourceである。各releaseは別task/checkpoint/stateを使い、static master planは共有する。

```bash
set -euo pipefail
export MASTER_TASK=full-repo-refactoring-2026-07-24
export RELEASE_ID=rN
export TASK="${MASTER_TASK}-${RELEASE_ID}"
export STATIC_PLAN_ROOT=".pipeline/plans/${MASTER_TASK}"
export RELEASE_PLAN_ROOT=".pipeline/plans/${TASK}"
export RELEASE_EVIDENCE_ROOT=".pipeline/evidence/${TASK}"
```

path ownershipは次に固定する。

| 種別 | path | 更新可否 |
|---|---|---|
| static master | `.pipeline/plans/$MASTER_TASK/{plan.md,verification-contract.md,planned-visual-changes.json,release-boundaries.json,request.md,research-brief.md,option-matrix.md,kpi-backcast-roadmap.md}` | review対象4 fileは4-file review確定時からimmutable、8 file全体はRF-00A commit後immutable。RF-00A commit前でも1 byte変更したら4-file review、coverage、pre-implementation gateを全て再実行 |
| shared immutable visual baseline | `.pipeline/evidence/$MASTER_TASK/{baseline-ui.json,baseline-integrity.json,screenshots/baseline/**}` | RF-00C commit後immutable |
| release-local approved copy | `.pipeline/plans/$TASK/{request.md,plan.md,verification-contract.md,planned-visual-changes.json,release-boundaries.json}` | masterとbyte一致するread-only runtime artifact。R1はRF-00Aでtracked、R2〜R7はcommitせずpremerge archiveまで毎回hash検証 |
| release-local lifecycle | `.pipeline/plans/$TASK/{goal-contract.json,current-state.md,checkpoint-contract.json,sml-decision.json}`、`.pipeline/{evidence,gates,sessions,outcomes,approvals}/$TASK/**` | 当releaseだけ |
| previous release | 上記の`$MASTER_TASK-r1`〜直前release | read-only |

clean status、build summary、phase/review/QA/outcome/approval/currentは常に現在の`TASK`を使う。plan/visual/release-boundaryとbaseline comparisonだけ`MASTER_TASK`を使う。hard-codeされたmaster task pathとこの表が衝突する箇所は実行せず、RF-00Eのpath parity testを直してexternal plan reviewへ戻る。

release worktree内の`.pipeline` runtime artifactはGitへcommitせず、別worktreeからも自動では見えない。したがって、R1 worktree作成前のplanning checkoutを唯一のcontrol checkoutとし、その絶対realpathを`CONTROL_ROOT`としてR1 `baseline.json`、R2〜R7 `bootstrap.json`へ固定する。各releaseの人間approvalとPR-ready完了後、PR作成前にRF-00Eのread-only-source archiverで次の**新規・一度限り**のarchiveへregular fileだけをcopyする。

```text
$CONTROL_ROOT/.pipeline/release-archives/<release-task-id>/
  premerge/
    archive-manifest.json
    files/
      .pipeline/plans/<release-task-id>/**
      .pipeline/evidence/<release-task-id>/**
      .pipeline/gates/<release-task-id>/**
      .pipeline/sessions/<release-task-id>/**
      .pipeline/outcomes/<release-task-id>/**
      .pipeline/approvals/<release-task-id>/**
    control/
      approval-stage/
        prearchive-ready.json
    trusted-launchers/
      <repo-relative-path>
  postmerge/
    merge-record.json
    operator-gates/<gate-id>.json
```

repo-relative memberのcanonical namespaceはexact `premerge/files/<repo-relative-path>`であり、`premerge/.pipeline/**`直下へ同じmemberを複製しない。control checkout外部のprearchive receiptはexact `premerge/control/approval-stage/prearchive-ready.json`、release HEADのGit objectから抽出するpostmerge runner/helperはexact `premerge/trusted-launchers/<repo-relative-path>`だけへ置く。`premerge/`はdestination不存在時だけtemp siblingへcopy→全file SHA-256検査→atomic renameし、以後1 byteも更新・削除しない。source symlink/FIFO/socket、task外path、secret-like filename、task/checkpoint/head不一致を拒否する。`postmerge/merge-record.json`と各operator gateもdestination不存在時だけatomic createする。R2〜R7 bootstrapとR7 final aggregatorは前release worktreeの相対pathを読まず、このarchive rootだけを読む。release worktreeを消さなくてもよいが、worktreeの残存を証拠永続化の代用にしない。

R1だけがRF-00Aでstatic plan/baselineを記録する。R2〜R7は、直前releaseのPR mergeとoperator gate後の`main`を`RELEASE_BASE_SHA`として、新しい`TASK`でcheckpoint→S/M/L decision→worktree→state `building`を開始する。前releaseのstate/outcome/approvalを再open・上書きしない。各releaseの`.pipeline/evidence/$TASK/release-manifest.json`は次を必須とする。

```json
{
  "schema_version": "1.0",
  "master_task_id": "full-repo-refactoring-2026-07-24",
  "release_id": "rN",
  "release_task_id": "full-repo-refactoring-2026-07-24-rN",
  "release_base_sha": "<branch作成時main>",
  "release_head_sha": "<表の末尾RF commit>",
  "release_head_tree_sha": "<git rev-parse release_head^{tree}>",
  "target_environment_id": "<認可済みoperatorが指定したstable environment ID>",
  "deployment_fingerprint_sha256": "<secretを含まないcluster/account/region/namespace fingerprintのSHA-256>",
  "implementation_branch": "codex/<release-task-id>|codex/<release-task-id>-retry-<failed-id>-<token>",
  "retry_state_transfer_sha256": "<retry時のstate-transfer.json SHA-256|null>",
  "item_ids": ["<release-boundariesを見出し順に展開したexact IDs>"],
  "item_commits": [{"item_id": "RF-...", "sha": "...", "subject": "..."}],
  "static_sha256": {
    "request.md": "...",
    "plan.md": "...",
    "verification-contract.md": "...",
    "planned-visual-changes.json": "...",
    "release-boundaries.json": "..."
  },
  "operator_gate": "op-...|null",
  "operator_expected_item": "RF-...|null"
}
```

この計画で`reviewed static`は独立reviewerが全byte確認する`plan.md,verification-contract.md,planned-visual-changes.json,release-boundaries.json`の4 file、`approved copy`はそれらに承認済み依頼原文`request.md`を加えた5 fileを意味する。request本文はreview対象4 fileの代用ではないが、release manifest、approval target、premerge archive、retry transfer、final aggregatorでは5 file全てのpath/hashを固定する。requestだけの差替えもapproval後改変として失敗させる。

この`release-manifest.json`はapproval hash対象で、pre-mergeに固定後は変更しない。approval完了後にcontrol checkoutのimmutable premerge archiveへ格納する。merge後は別の`$CONTROL_ROOT/.pipeline/release-archives/$TASK/postmerge/merge-record.json`へ`release_manifest_sha256,premerge_archive_manifest_sha256,merge_permit_sha256,merge_attestation_sha256,release_head_sha,merged_main_sha,merge_method,release_head_tree_sha,merged_main_tree_sha,tree_matches,merged_at,merged_by_stable_id,target_environment_id,deployment_fingerprint_sha256,approval_stage_attempt_id,approval_stage_selection_sha256,parent_closure_attempt_id,prearchive_ready_sha256,approval_completion_sha256,archive_manifest_sha256,postmerge_stage_attempt_id,postmerge_stage_selection_sha256,lifecycle_tip_sha256`を一度だけ記録する。値は認可済みGitHub/operator経路の既存immutable merge attestation、control-root final approval completion、canonical stage selection/lifecycleから照合して取り込み、caller環境変数による自己申告を受けない。operation artifactは`postmerge/operator-gates/<gate-id>.json`へ置き、さらに`merge_record_sha256,expected_commit,component_source_sha=merged_main_sha,target_environment_id,deployment_fingerprint_sha256,stage_attempt_id,stage_selection_sha256,lifecycle_parent_sha256,lifecycle_tip_sha256`を持つ。final aggregatorは7 releaseそれぞれのimmutable premerge archive→全closure artifact→final approval completion→全postmerge stage selection/receipt→merge permit/attestation/record→operation/cutover gate→lifecycle tipのhash chainを検証する。

release local closureのoperator唯一入口は9.0のtrusted bootstrap blockである。operatorは9.0表の`CLOSURE_STAGE`と`CLOSURE_ACTION`だけを設定してblock全体を実行する。bootstrapがGit blobからrunnerと全helperをrepo外tempへ展開・検証し、fresh `/usr/bin/env -i /bin/bash --noprofile --norc`内でextracted `run-refactor-closure-stage.sh`へ対応する`--resume-or-new|--verify-existing`を内部選択して渡す。operatorがextracted runnerや9.1〜9.7のcommand列を直接呼ぶことは禁止する。`run-refactor-release-gate.sh`もoperatorが直接呼ばず、9.2の`release-execution` stageが`--execute --attempt-id <selected closure attempt>`を、続く`release-finalize` stageが`--verify-execution`、final GitNexus/full verification、`--finalize --attempt-id <same selected closure attempt>`を固定順で呼ぶ内部helperである。ack喪失時は対応stage wrapperが`--verify-execution`または`--verify-existing`だけを呼び、`--execute`/`--finalize`を再実行しない。

このgateはfuture releaseのmatrix entryが`planned`でも失敗させず、(1)過去release entryはactive/commit-manifest一致、(2)R1先頭から当release末尾までの全active prefixの`replayable` itemを見出し順に一度だけ実行、(3)RF-00A/RF-00N/RF-00Cは各commit/archiveに固定した証拠をread-only verify、(4)active prefixが触れたrequired suiteをstable-uniqueで一度だけ実行、(5)release内first-parent subject exact一致、(6)source diffが当release項目対象のunion内、(7)GitNexus compare、(8)approved copy 5 file/release-boundary hash一致を検査する。R7だけは全entry activeに加え、既存5 phase gate JSONと既存item/suite reportをread-only集約する`run-full-refactor-verification.sh`を要求し、phase/item/suiteを再実行・上書きしない。final E2E以降はrelease gateの後に9.2〜9.8で作る。

local gate後、9.3〜9.7と同じschemaを現在の`TASK`、`head_sha=$RELEASE_HEAD_SHA`で実行し、独立QA、人間approval、`pr-ready-gate.sh "$TASK"`までpassしてからPRを作る。PRはsquash/rebase merge禁止。fast-forwardまたはitem commitsを保持するno-ff mergeだけを許し、no-ff時はmerge treeがrelease head treeとbyte一致することを検査する。merge後にoperatorが配備するartifactのsource labelは`merged_main_sha`、OP verifierの`expected_commit`は表末尾のcompatibility item commit SHAとし、両方をoperation artifactへ保存する。operator gate後、次releaseのbaseは`merged_main_sha` exactでなければならない。

R7最終aggregatorは7 manifestのrelease ID/task/base/headを順に読み、`r1.release_base_sha=b2bcae8e...`、各`rN.release_base_sha=前release.merged_main_sha`、各merge tree=head tree、全item IDの連結=全`### RF-*`見出し、subject/件数一致、R1〜R6 operation gate pass、R7 full closure passを要求する。単一`IMPLEMENTATION_BASE_SHA..FINAL_HEAD`のfirst-parent直線履歴を要求する旧記述より、このrelease protocolを優先する。

## 1. 現状理解

### 1.1 このシステムが実現していること

`generic_tldv` は、Google Meet、Microsoft Teams、Zoom等へBotまたはBrowser Sessionを参加させ、音声・映像を取得し、リアルタイムまたは会議後に文字起こしし、Dashboardから閲覧・検索・話者編集・再文字起こし・エクスポートを行うシステムである。Calendar連携、Telegram、MCP、音声Agent、Git workspace付きAgent sessionも同じリポジトリに含む。

主要なデータ経路は次のとおり。

```text
利用者
  -> Dashboard / Telegram / MCP / API
  -> API Gateway
       -> Admin API       ユーザー、API token、workspace設定
       -> Meeting API     Bot要求、会議状態、録画、後処理
       -> Calendar        予定同期、自動Bot投入
       -> Agent API       container、workspace、SSE chat
       -> Runtime API     container起動、停止、予約実行

Vexa Bot
  -> 会議へ参加
  -> 音声chunk / transcript
  -> Redis
  -> collector / Meeting API
  -> PostgreSQL / Object Storage
  -> Dashboard

会議後処理
  Meeting API
  -> recording取得
  -> Transcription Service
  -> transcript確定
  -> Redis publish / cache
  -> Voiceprint / Drive export

音声Agent
  Wake STT -> Wake Orchestrator -> LLM -> TTS
```

### 1.2 主要ファイルと依存関係

| 領域 | 主要ファイル | 現在の役割 | 主な依存 |
|---|---|---|---|
| Gateway | `services/api-gateway/main.py` | 認証、scope、HTTP/WS proxy、共有URL、Agent SSE | Admin、Meeting、Calendar、Agent |
| Meeting | `services/meeting-api/meeting_api/meetings.py` | Bot要求、URL/native ID、runtime spec、browser保存 | Runtime、DB、Redis、schemas |
| Meeting lifecycle | `callbacks.py`, `sweeps.py`, `post_meeting.py`, `recording_finalizer.py` | callback、終端判定、復旧、録画確定、後処理 | 相互に関数内importを含む循環 |
| Deferred transcription | `final_transcription.py` | lease、録画解決、provider、DB、cache、publish、Drive、voiceprint | Meeting DB、Redis、Transcription |
| Transcription | `services/transcription-service/main.py` | OpenAI互換endpoint、音声前処理、provider dispatch | Gemini/Soniox adapters |
| Gemini | `gemini_adapter.py` | chunk境界計画、speaker対応、segment統合 | provider SDK、設定 |
| Runtime | `runtime_api/api.py`, `scheduler.py` | backend経由container操作、Redis予約job | Redis、Docker/K8s backend |
| Dashboard page | `services/dashboard/src/app/meetings/[id]/page.tsx` | 会議詳細の取得、polling、再生、Browser、responsive UI | store、API、viewer |
| Transcript UI | `services/dashboard/src/components/transcript/transcript-viewer.tsx` | 検索、scroll、話者編集、voiceprint、再文字起こし | transcript manager、API |
| Transcript state | `services/dashboard/src/stores/meetings-store.ts`, `packages/transcript-rendering/src/*` | API結果、confirmed/pending、dedup、timeline | Dashboard API、WebSocket |
| Browser session | Dashboard、`meetings.py`、`core/src/browser-session.ts` | VNC、workspace git、save storage | Gateway、Redis Pub/Sub |
| Bot core | `services/vexa-bot/core/src/index.ts` | platform起動、音声、Browser、command、shutdown | platform modulesとの逆import |
| Calendar | `services/calendar-service/app/main.py`, `sync.py` | OAuth、予定同期、自動投入 | Admin/Meetingのmodelを直接import |
| Agent | `services/agent-api/agent_api/*` | session、container、workspace、chat | Admin user data、Docker/K8s |
| Agent runtime image | `services/vexa-agent/Dockerfile`, `system/bin/vexa` | Agent container内CLI、workspace保存/status、予約実行 | Agent API、Gateway、Runtime API |
| Public clients | `packages/vexa-cli`, `packages/vexa-client` | 利用者向けCLI/SDK、meeting/workspace API呼出し | Gateway公開API |
| Shared test/persona | `packages/redaction-tests`, `packages/kabosu-persona` | secret redaction fixture、Kabosu persona資産 | Admin/Meeting/Agent、bot integration |
| Test registry | `tests3/test-registry.yaml`, `checks/registry.json`, `registry.yaml` | test列挙、dispatcher、集計 | Makefile、shell、Python |
| Deploy | `deploy/compose`, `deploy/lite`, `deploy/helm`, GCP workflows | local/prod起動、image publish、Cloud Run | Docker、Helm、GitHub Actions |
| Managed Harness | tracked `scripts/harness`, `.pipeline`, `schemas`; read-only external `.claude/hooks` | worktree、build/evidence/gate/outcome | shell、embedded Python、Git |

### 1.3 構造上の重要事実

- 調査時の規模は約161,809 LOC。
- 特に大きい実装は `gemini_adapter.py` 3,936行、`vexa-bot/core/src/index.ts` 2,830行、`api-gateway/main.py` 2,667行、`meetings.py` 2,481行、Meeting detail page 2,452行、`final_transcription.py` 1,608行、`TranscriptViewer` 1,486行。
- `meeting-api` のlifecycle周辺は9モジュールの循環を関数内importで回避している。
- Calendar/AdminのDocker imageは共有契約のためにMeeting API packageを丸ごとinstallしている。
- `services/README.md` が示すservice separationと、実際のmodel/database直接importが一致していない。
- GitNexus indexは調査時点で `f9c3b36` を指し、現HEADより古い。FTS/embeddingも利用不能だったため、旧graphのimpact結果を最新ソースの直接読解とimport/AST解析で補完した。
- GitNexus上、`run_deferred_transcription` の上流影響はCRITICAL、`_plan_exact_boundary_stream_consumption` はMEDIUM。前者を一括編集してはいけない。
- BrowserでローカルDashboard `http://127.0.0.1:3002` の会議一覧・会議詳細を読み取り確認した。ただし稼働imageは調査HEADより約10日古く、現HEADの視覚的合否には使えない。現HEADでのスクリーンショットは項目0と最終gateで取得する。
- `services/vexa-agent/Dockerfile:17` は現HEADに存在しない `features/knowledge-workspace/templates/knowledge/` をCOPYする。Agent API/Runtime/Meeting profileはこのimageを参照するため、clean checkoutではAgent runtimeがbuild不能である。削除済み`features/`を復元せず、未使用COPYを除く必要がある。
- `packages/vexa-client/vexa_client/test_funcs.py:1-16` はnotebook用helperであり、`sys.path`変更、存在しない`import test`、localhost固定を含む。pytest testとして扱ってはいけない。
- `.claude/hooks`, `.claude/agents`, `.claude/rules`, `.claude/skills` はこのrepoのtracked sourceではなく、別repo `claude-dotfiles` への絶対symlinkである。この計画はhookをread-onlyで実行するが変更しない。hook側修正が必要ならgeneric_tldv実装を中断し、canonical repo用の別task/別planへ切り出す。
- 計画作成hostでは外部`claude-dotfiles` checkoutが`70d84688a33815c892cd3da3900b80c19a159ae7`へ進み、Harnessが`closed/harness-init/`へ移動した一方、repo外の4 symlinkは旧`skills/harness-init/`を指したままで現在brokenである。調査hostの外部checkoutやsymlinkは変更していない。本計画がreviewしたhookはGit object `claude-dotfiles@fb9bd5f0d2a20a52d9d48dc56d4080a65c5c3233:skills/harness-init/shared/.claude/**`のbytesであり、実装開始には3章のexact external packageを事前配置した隔離環境が必要である。現在hostの`closed/`へ暗黙fallbackしてはいけない。

### 1.4 問題一覧と優先度

| 優先度 | 問題 | 根拠となる対象 |
|---|---|---|
| P0 | Calendar/Agentが認証subjectではなくquery/body `user_id` を信頼する | Gateway `main.py:322-419`、Calendar `main.py:85-182`、Agent `main.py:243-475` |
| P0 | Agent SSEがtoken/API key欠落時にfail closedしない。route scope表も不完全 | Gateway `main.py:88-100,1565-1645`、Agent `auth.py:1-29` |
| P0 | Meeting/Runtime APIもsecret未設定時にfail openし、内部identity headerを単独で信頼する | Meeting `auth.py:24-54`、Runtime `main.py:57-62,145-159` |
| P0 | `workspace_git.token` が公開User response/analytics responseへ出る | Meeting `schemas.py:339-349`、Admin `main.py:224-261,339-360` |
| P0 | Dashboard `/api/config` が `VEXA_API_KEY` をbrowserへ返し得る | Dashboard config route `:59-80` |
| P0 | Admin proxyがadmin session cookieのHMACを検証せず、unsigned base64 JSONで全権proxyへ到達できる | Dashboard `api/auth/admin-verify/route.ts:22-37,101-130`、`api/admin/[...path]/route.ts:10-52` |
| P0 | `vexa-user-info`の未署名emailを認可subjectへ使い、A token+B emailでBのraw API token管理へ到達できる | Dashboard `lib/auth-utils.ts:13-58`、`api/profile/keys/**`、Admin token delete |
| P0 | 未認証meeting一覧BFFがservice keyをfallbackにし、private会議を代理取得する | Dashboard `api/vexa/[...path]/route.ts:111-178` |
| P0 | MeetingToken署名にAdmin API全権keyを再利用し、collector以外のRecording uploadにも同tokenを使う | Meeting `meetings.py:83-116`、`collector/processors.py:29-65`、`recordings.py:112-119` |
| P0 | Bot/Browserへglobal Redis、Transcription、Wake、Internal、Storage credentialを渡し、別会議/別audienceへreplayできる | Runtime `profiles.yaml:36-77`、Meeting `meetings.py:797-835,1084-1111`、Core Redis/media clients |
| P0 | Agentへplatform共有Anthropic/Claude credential、Zoom Botへclient secret、Bot/Browserへuserinfo入りupstream proxy URLを渡す | Agent `container_manager.py:140-169`、Meeting `meetings.py:1158-1175`、Helm `values.yaml:506-515` |
| P0 | webhook testとschedulerが任意HTTP送信を許し、redirect/private IP/DNS再解決を共通policyで塞いでいない | Dashboard webhook route、Runtime `scheduler.py:204-258`、Agent `main.py:382-398` |
| P0 | Compose/Helm/Liteに既知の弱いdefault secretと直接login fallbackがあり、設定漏れが安全側に倒れない | deploy compose/helm/lite、Dashboard magic-link/direct-login routes |
| P0 | `VEXA_ENV`未設定をdevelopment扱いし、公開Gateway/Meetingでsynthetic test lifecycle/callback routeが有効になり得る | Gateway `main.py:2432`、Meeting `meetings.py:1188`、`callbacks.py:1094,1142`、deploy全profile |
| P0 | Runtime create bodyがimage/command/env/mount/network/nameをbackendへ通し、既存resource再利用やcontrol-plane侵害が可能 | Runtime `api.py:178-272`、Docker/Kubernetes backend |
| P0 | Lite ProcessBackendが親environment/root UID/shared X11/audio/filesystemを子processへ継承し、session間秘密値・画面へ到達できる | `backends/process.py:61-72,219-233`、`deploy/lite/bot-slot-wrapper.sh:33`、Lite Dockerfile |
| P0 | Agent/Browserのworkspace git commandがshell interpolationし、tokenをURLへ含め、任意origin送信や誤成功を起こし得る | Agent `workspace.py:327-361`、Core `browser-session.ts:14-116` |
| P0 | Browser保存はpublish後subscribe、相関IDなし、timeout逆転 | Meeting `meetings.py:1307-1334`、core `browser-session.ts:177-203`、Gateway `main.py:2363-2386` |
| P0 | Harness task-idから `.pipeline` 外へpath traversalできる | `codex-session-ledger.sh:19-24`、`sml-decision.sh:15-20,76-89`、`worktree.sh:17-23,75-84` |
| P0 | Admin DB再作成にMeeting側と同じ破壊防止guardがない | `libs/admin-models/admin_models/database.py:118-144`、`services/admin-api/app/scripts/recreate_db.py:29-59` |
| P0 | tests3は登録script 91件中45件が不存在でも成功する | `test-registry.yaml`、`run-matrix.sh:168-176,209-221` |
| P0 | report 0件、feature 0件、全skip、0 stepでも合格し得る | `aggregate.py:164-213,559-650`、`common.sh:130-146` |
| P0 | Compose/Lite readinessとDB初期化が失敗しても成功表示する | Compose Makefile `:298-340`、Lite Makefile `:128-200` |
| P0 | Agent runtime imageが不存在`features/`をCOPYし、clean build不能 | `services/vexa-agent/Dockerfile:17`、Agent/Runtime/Meeting profile |
| P1 | transcript検索に生文字列をRegExpとして渡し、`[` 等で例外になり得る | `services/dashboard/src/components/transcript/transcript-segment.tsx:57-69` |
| P1 | dedupが直前要素しか見ず、A/B/A重複を残す | `dedup.ts:27-142` |
| P1 | 複数sessionを相対 `start_time` だけで並べ、絶対timelineを壊す | `manager.ts:61-67`、`dedup.ts:227-251`、Meeting page `:318-351` |
| P1 | meeting切替後に旧requestが新meeting stateを上書きする | `services/dashboard/src/stores/meetings-store.ts:347-379,435-456,510-538` |
| P1 | 再文字起こしとpost-meeting pollingが二重・重複実行される | Viewer `:406-443,1050-1094`、Page `:865-945` |
| P1 | WebSocketが2系統あり、pending消去、token更新、reconnect意味論が異なる | `use-live-transcripts.ts`、`use-vexa-websocket.ts`、`live-store.ts` |
| P1 | Browser UIがsame-origin規約と状態分類に違反 | Dashboard README `:74-85`、Page `:124-134,1150-1177,1890-1939` |
| P1 | runtime schedulerのidempotencyが非原子的でretry/historyも不整合 | `scheduler.py:92-155,290-315` |
| P1 | exit callbackとstatus callbackで同じ明示的失敗理由の終端結果が違う | `callbacks.py:115-124,364-396,887-912` |
| P1 | Meeting URL parserが3実装へ分岐しTelegram Teams requestが422になる | `schemas.py:442-536,748-758`、MCP `main.py:231-360`、Telegram `bot.py:646-691` |
| P1 | lifecycle、Bot要求、deferred文字起こし、Gemini、Gatewayが巨大かつ責務混在 | 前記主要ファイル |
| P1 | core `index.ts` とplatform moduleが循環する | `core/src/index.ts`、`platforms/shared/meetingFlow.ts`、recording modules |
| P1 | Agent/Calendar/Wake/TTS/Voiceprintに競合、task leak、無制限cache、health不整合 | 各補助service |
| P1 | Helm/CI/deployが失敗を成功扱い、またはrequired gateへ未接続 | Helm tests、`rung.yml`、dashboard deploy workflow |
| P2 | API DTO/表示ロジック、UI state/API/JSX、player制御が混在 | Dashboard API/types/viewer/page/player |
| P2 | ORM/database codeが重複し、service imageが別service packageへ依存 | `libs/admin-models`、Meeting models/database、各Dockerfile |
| P2 | shell portability、registry三重化、古いresolver、Harness embedded Python重複 | tests3/deploy/Harness |
| P2 | 参照されないtgz、redirect済みdocs source、古い実行計画、README不整合 | Dashboard/docs/tests3 docs |

### 1.5 既知のbaseline

- Dashboard unit: 28 files / 199 tests 成功。
- Dashboard lint: 61 errors / 87 warningsで既存失敗。
- `packages/transcript-rendering`: 83成功 / 5 skip、typecheck成功。
- `services/vexa-bot/core`: `tsc --noEmit --incremental false` 成功。
- `tests3`: registry 91件 / script不存在45件。
- `features/`: 現在存在しない。`2d93eca2c94695b11ae631adacf4f38c4e721255` でOSS外へ出す意図が明記されているため、過去sidecarを善意で復元しない。
- 不存在testの多くは `a51b952076f0f88455f1da5ce10a4d10a73bb835` で削除されている。registryから無言で消さず、状態と理由をmachine-readableにする。

### 1.6 効果×リスクによる実行優先度

| 順位 | 項目群 | 効果 | 変更リスク | 順序の理由 |
|---|---|---:|---:|---|
| 1 | RF-00A→RF-00N→RF-00E→RF-00B→RF-00C→RF-00D 安全網 | 最大 | 低 | 後続の誤成功と観測不能を先に止める |
| 2 | RF-01〜10 境界/secret/SSRF/破壊操作 | 最大 | 高 | 漏洩・越権・任意送信・build不能は構造整理より先 |
| 3 | RF-11〜30 正しさ/競合 | 高 | 中〜高 | 現行bugを巨大関数分割後の複数moduleへ固定しない |
| 4 | RF-31〜51 検証/Deploy/Harness | 高 | 中 | 「実行していないのにpass」を除去して後半の証拠を有効化 |
| 5 | RF-52〜66 Backend抽出 | 中〜高 | 高 | characterizationと境界修正後にmove-onlyで実施 |
| 6 | RF-67〜75 UI/Core/重複/文書 | 中 | 中 | 契約とstate意味論を先に固定し、最後にcompositionを薄くする |

同順位内でも本文の依存順を変えない。高リスク項目は小さく分割してあるため、複数IDを1コミットへまとめてリスクを「吸収」してはいけない。

## 2. 共通実行規約

### 2.1 行番号の扱い

各項目の行範囲は調査時HEAD `b2bcae8e...` の基準位置である。先行項目で行番号がずれた場合は、記載したsymbol、endpoint、test名で再同定する。名前が見つからない、または責務が先行項目で想定外に消えている場合は中断し、計画逸脱として報告する。`1-末尾`は調査時の末尾行を越えてよい唯一の表記であり、数値rangeのendが実fileの末尾を越えていたら誤記として実装前に計画修正を要求する。

対象欄の表記は次のliteral規則で解釈し、実行者の裁量で対象を広げない。

- 既存file pathに行範囲がない場合は、そのfileの`1-末尾`だけを対象とする。`path:r1,r2-r3,...`は各point/rangeを同じpathの別rangeへ展開する正式表記であり、`path:r1`、`path:r2-r3`の列へ正規化する。point/rangeは正整数、開始≤終了、調査時file末尾以内、相互非重複でなければexit 2とする。
- 既存`directory/`または`**`/`*` globは、項目開始時の`git ls-files -- <pattern>`結果をexact target inventoryとしてevidenceへ保存する。結果0件なら中断する。`新規`globは原則禁止し、(a)RF-00Aのliteral local review 3 file＋canonical Fable/Codex/dual summary 3 file、(b)RF-00Cのfixture/screenshotだけを例外とする。RF-00Aはfilesystem globやcanonical gateの不存在fieldから再探索しない。RF-00C fixtureは`sorted(Object.values(fixture_contracts).map(x => x.path))`のunique exact path、screenshotは`scenarios[*].scenario_id × sorted(Object.keys(viewports))`を`filename_template`へ代入した18 exact pathから開始前inventoryを作る。JSONにない追加file、0件、重複、path escapeは中断する。
- repo外`.claude/{hooks,agents,rules,skills}` symlinkは上のtracked glob規則へ入れない。外部inputは3章で事前配置された`claude-dotfiles@fb9bd5f0d2a20a52d9d48dc56d4080a65c5c3233`のGit root、symlink raw target、`realpath`、regular file、Git blob、SHA-256を照合し、各項目に列挙したexact fileだけを`read_only_input`へ入れる。broken symlink、別commit、現在checkoutの`closed/harness-init/`へのfallback、実行者による外部checkoutのswitch、symlink変更は全て`blocked: canonical hook package missing`であり、write targetの0件globとして再解釈しない。
- `{a,b}`はbraceを含む名前ではなく、同じdirectory配下の`a`と`b`へliteral展開する。
- `新規`と書いたpathは、その項目で作成するexact pathであり、別名へ変更しない。
- 各項目の`read-only inventory`は、exact path対象欄の**完全性を検査するread-only discovery**であり、検索結果を`write_target_allowlist`へ自動追加しない。同じ対象欄に列挙したsymbol、secret、route、module名をliteralなら`git grep -n -F -e <term> -- <roots>`、フェーズ1に記録した正規表現なら`git grep -n -E -e <pattern> -- <roots>`し、commandと全一致行をitem開始evidenceへ保存する。対象欄で「read-only既知一致」としてexact pathを列挙したfileは変更せず、path/一致行を検査する。`README.md`、`LICENSE`、`docs/`、path componentが`tests|test|fixtures|__fixtures__`のfile、`.github/workflows/`だけはcategoryがpathから機械判定できるread-only inventoryとして追加列挙を不要とし、全path/lineをevidenceへ保存してwrite対象へ追加しない。これらcategoryのfileを修正する必要が生じた場合は、対象欄にexact write pathがなければ停止する。それ以外のexact target path外production/config一致、動的参照、検索語を確定できない箇所が1件でもあれば、そのfileを善意で編集対象へ広げずplan reviewへ戻る。production source内のcomment/docstringだけの一致もcategory例外にせず、対象欄のexact read-only path列挙がなければ停止する。
- rename/move項目は移動元と移動先の両方を`write_target_allowlist`へ入れる。移動先のexact pathが対象欄にないrenameは中断する。`git mv`後もsource deletionとdestination additionを別々にscope照合する。
- `.pipeline/**`、`<release-task-id>`、`$TASK`を含むruntime pathは、検証済み`TASK="${MASTER_TASK}-${RELEASE_ID}"`をliteral置換した後にrepo containmentを検査するruntime専用表記であり、`git ls-files`対象にしない。production/test/docs pathへplaceholderを使うことは禁止する。
- RF-75A〜RF-75Dのlint JSONはruntime inputである。各JSONをschema/hash/task/head/rule ID/sorted unique/repo containment/line boundsで検証し、明記されたrule allow-listに一致する`relative_file:start_line-end_line`だけを`artifact_source_targets`として`write_target_allowlist`へ加える。JSON自身はstageしない。空、重複、対象欄のrule外、生成HEAD不一致なら中断する。
- フェーズ末尾の`rg` inventoryはread-onlyな残存違反検査でありtarget解決ではない。対象解決には上記`git ls-files`/`git grep`だけを使い、`rg`結果を編集対象へ自動追加しない。
- 以下の各項目に書いたfull pathが、この共通規則より優先する。似た名前の別fileを「同じ責務」と判断して追加しない。

各項目は対象を次の3 scopeへ機械分離する。

- `write_target_allowlist`: (a)項目の`対象`にexact file path/globとしてある既存/新規production・test・docs file（logical search command/result自体は除外）、(b)Appendix A.1 rule 2で解決した完了条件/new-test candidate、(c)A.2 fallback exact path、(d)A.6 resolverが変更する所有元test exact path、(e)`scripts/test/refactor-item-matrix.json`の和。directory/globは上記literal展開後のregular fileだけ。A.1/A.2/A.6 resolverはmatrix linterと同じ実装・同じJSON出力をimpact、stage、release scopeで再利用し、別々に再推測しない。
- `required_changed_targets`: `write_target_allowlist`内の`新規`production/test/docs file、削除対象、rename/moveのsourceとdestination、matrix entryを持つ項目の`scripts/test/refactor-item-matrix.json`。runtime/read-only fileは`新規`でも含めない。RF-00A/RF-00N/RF-00E/RF-00Cのbootstrap例外は各本文のexact staged setを使う。通常項目はこれらに加え、matrix以外のallowlist pathが1件以上cached差分へ含まれなければならない。対象欄の全fileを変更必須とはせず、観測・比較だけした既存fileをstage集合へ要求しない。
- `read_only_input`: `Git repository全体`、外部canonical hook、static reviewed artifact、既存inventory、比較base/archive等。項目本文で`read-only`と明記したものだけで、write/stage unionへ入れない。absolute symlink先やrepo外canonical hookは`git ls-files`へ渡さず、`realpath`、regular file、期待commit/hash、allowlisted canonical rootを照合したread-only inventoryへ分離する。
- `runtime_output`: `.pipeline/evidence|gates|sessions|outcomes|approvals|tmp`、venv、node_modules、worktree metadata等。項目本文のschema/allow-list先へだけ書き、source commitへstageしない。ただしRF-00Aの明示baseline/review artifactとRF-00Cの明示immutable visual baselineは各項目の例外どおりstageする。

`write_target_allowlist`が空、同じpathがread-only/runtimeと重複、解決不能token、対象外pathを含む場合は編集前にexit 2。production helper/moduleの新規destinationは対象欄へexact pathがなければA.1 test resolverでは補えないため中断する。item開始evidenceへ3 scope、resolver source、allowlist/required_changed_targetsの全path/range/SHAを保存する。

### 2.2 各項目で必ず行う共通手順

RF-00AとRF-00Nだけはbootstrap例外である。RF-00A時点ではitem runner/matrix/GitNexus wrapperが存在しないため、review済みartifactと新規bootstrap receipt helper/testのexact staged set、本文のstandalone test/verifier、worktree/checkpoint検査だけを実行する。RF-00Nは新規GitNexus runtime/wrapper/testだけを作り既存production symbolを変更しないため、同項目のstandalone bootstrap testをcommit前に通し、commit直後に初めてtracked wrapperで自身のcommitをRF-00A SHAと比較する。RF-00A/RF-00N以外は次の手順を省略しない。

本文の全`bash` code fenceは、明示済みshebang付きscriptを除き、同じblock冒頭で`set -euo pipefail`を有効にした非対話shellとして実行する。前提`test`、copy、hash検査、importの1つでも失敗したら後続行へ進まない。意図的なexit code採取で`set +e`を使うblockは直後にstatusを保存して`set -e`へ戻し、未復帰を`bash -n`に加えるstatic testで拒否する。9.0と9.8.0のoperator入口fenceだけはambient shellで`set`する前にfresh shellへ入ること自体が契約なので、first commandをliteral `/usr/bin/env -i ... /bin/bash --noprofile --norc`とし、heredoc内のfirst commandが`set -Eeuo pipefail`でなければならない。この2 fence以外の例外を追加しない。

唯一のmulti-fence例外はRF-00A initial手順1〜11である。同項目に明記した`RF00A_INITIAL_TRANSACTION`の開始fenceから終了fenceまでを、表示順のまま**同じ1回の非対話`bash -s` process**へ連結して実行する。途中fenceを別process、別terminal、別sessionで直接実行してはいけない。開始fenceが`set -Eeuo pipefail`とERR handlerを最初の副作用前に設置し、終了fenceだけがtrapを解除する。pre/post commitを問わずactual failure、またはcanonical `rf-00a-postcommit` receiptが存在しないprocess喪失はRF-00Aのterminal failureであり、同じmaster task/branch/pathで続行しない。唯一のrecoverable caseはcaptureがsingle-write完了した後のstdout/process ack喪失で、別sessionのstandalone `verify-only`がexisting receipt/head/hashを全てpassする場合だけである。失敗artifactを保持してblockedを報告し、調査基準SHAとreview対象4 fileから別master task IDの計画を作り、外部/独立reviewを最初から通す。これ以外のfenceは前sessionの変数、cwd、trapを継承しないstandalone blockである。

1. 次のcommandで、task-owned runtime artifactを除く実装treeがcleanであることを確認する。項目0で記録した既存ユーザー成果物が同じ作業treeに見える場合は、専用worktreeの作成に失敗しているため中断する。

```bash
set -euo pipefail
test -n "${MASTER_TASK:-}"
test -n "${RELEASE_ID:-}"
test -n "${TASK:-}"
test "$TASK" = "${MASTER_TASK}-${RELEASE_ID}"
git status --short --untracked-files=all -- . \
  ":(exclude).pipeline/evidence/$TASK/**" \
  ":(exclude).pipeline/gates/$TASK/**" \
  ":(exclude).pipeline/outcomes/$TASK/**" \
  ":(exclude).pipeline/sessions/$TASK/**" \
  ":(exclude).pipeline/approvals/$TASK/**" \
  ":(exclude).pipeline/plans/$TASK/checkpoint-contract.json" \
  ":(exclude).pipeline/plans/$TASK/goal-contract.json" \
  ":(exclude).pipeline/plans/$TASK/current-state.md" \
  ":(exclude).pipeline/plans/$TASK/sml-decision.json" \
  ":(exclude).pipeline/plans/$TASK/request.md" \
  ":(exclude).pipeline/plans/$TASK/plan.md" \
  ":(exclude).pipeline/plans/$TASK/verification-contract.md" \
  ":(exclude).pipeline/plans/$TASK/planned-visual-changes.json" \
  ":(exclude).pipeline/plans/$TASK/release-boundaries.json" \
  ":(exclude).pipeline/plans/$TASK/consultation-*.md"
```

出力は空でなければならない。task-owned artifactは別途raw `git status --short`で一覧を保存し、上記allow-list外へ1件も出ていないことを確認する。このstatusより先に、release-local approved copy 5 fileはmaster staticの同名fileとbyte/hash一致、lifecycle 4 fileはtask/checkpoint ID一致をRF-00E verifierで確認する。master側の`plan.md`、`request.md`、`research-brief.md`、`option-matrix.md`、`kpi-backcast-roadmap.md`、`verification-contract.md`は除外しない。release-local copy/lifecycleの内容不一致を「除外pathだから許可」と扱わず中断する。

2. `ITEM_ID`を現在の`### RF-*`見出しIDとbyte一致で設定し、`ITEM_BASE_SHA="$(git rev-parse HEAD)"` を項目evidenceへ保存する。initial branchでは`ATTEMPT_ID=initial`、retry branchでは検証済み`state-transfer.json.retry_token`を設定し、他の値を拒否する。次のliteral `analyze`を1回実行後、`write_target_allowlist`の各**既存**production fileと、変更する既存関数/class/method名をそれぞれ`SYMBOL`へ1つずつ設定してliteral `impact`を実行する。file全体が対象ならrepo-relative file pathを`SYMBOL`にする。新規production file/symbolは編集前indexに存在しないためpre-impactへ渡さず、同項目の既存production target全件を`impact_anchor`とする。新規production targetがあるのに既存production anchorが0件ならmatrix linterをfailさせ、実行者が類似symbolを推測しない。新規test/docsだけは検証対象production anchorを使う。変更後は新規symbol/file自身にもpost context/impactを実行し、anchorのblast radiusと計画範囲内であることを照合する。HIGH/CRITICALなら結果とblast radiusをユーザーへ提示し、**明示的な続行承認を受けるまで編集しない**。承認記録、対象process、直接/間接caller、計画範囲内である根拠をitem evidenceへ保存する。計画対象外processへ到達した場合は、承認があってもこの計画では進めず再計画する。

```bash
set -euo pipefail
test "$ITEM_ID" = "<現在の見出しID>"
bash scripts/test/run-gitnexus-refactor.sh analyze \
  --worktree "$PWD" --head "$ITEM_BASE_SHA" \
  --task "$TASK" --evidence-scope "item:$ITEM_ID" \
  --analysis-stage pre --attempt-id "$ATTEMPT_ID"
bash scripts/test/run-gitnexus-refactor.sh impact \
  --worktree "$PWD" --head "$ITEM_BASE_SHA" \
  --task "$TASK" --evidence-scope "item:$ITEM_ID" \
  --analysis-stage pre --attempt-id "$ATTEMPT_ID" \
  --target "$SYMBOL" --direction upstream
```
3. 対象testを先に追加または確認する。既存バグ修正項目では、修正前に新testが意図した理由で失敗することを記録する。
4. 本体を変更する。
5. 項目固有testと下表のrequired suiteを通す。
6. `git diff --check`と手順1の除外付きstatusを通した後、実差分のある`write_target_allowlist` pathだけを明示`git add -- <exact paths>`する。cached path集合がnon-empty、`cached_paths ⊆ write_target_allowlist`、`required_changed_targets ⊆ cached_paths`、通常項目ではmatrix以外のcached pathが1件以上、の全条件をmachine checkする。これによりGitNexus compareへ新規fileも含めつつ、観測だけしたallowlist fileを無理に差分化しない。runtime/read-only/user file、allowlist外pathがcachedに1件でもあればcommitせず中断する。その後、次のtracked wrapperを実行する。post analyzeは`--force`でindex+working treeを再解析し、既存変更symbolと新規production symbol/fileのcontext/impactを取得してから、staged diffを`ITEM_BASE_SHA`と`--scope compare`で比較する。`main`比較はフェーズgateと最終検証だけで行う。

```bash
set -euo pipefail
bash scripts/test/run-gitnexus-refactor.sh analyze \
  --worktree "$PWD" --head "$(git rev-parse HEAD)" \
  --task "$TASK" --evidence-scope "item:$ITEM_ID" \
  --analysis-stage post --attempt-id "$ATTEMPT_ID"
# 既存変更symbolと新規production symbol/fileを1件ずつPOST_SYMBOLへ設定して繰り返す。
bash scripts/test/run-gitnexus-refactor.sh impact \
  --worktree "$PWD" --head "$(git rev-parse HEAD)" \
  --task "$TASK" --evidence-scope "item:$ITEM_ID" \
  --analysis-stage post --attempt-id "$ATTEMPT_ID" \
  --target "$POST_SYMBOL" --direction upstream
bash scripts/test/run-gitnexus-refactor.sh detect-changes \
  --worktree "$PWD" --head "$(git rev-parse HEAD)" \
  --task "$TASK" --evidence-scope "item:$ITEM_ID" \
  --analysis-stage post --attempt-id "$ATTEMPT_ID" \
  --base-ref "$ITEM_BASE_SHA"
```
7. 手順6でstage済みのexact `cached_paths`とallowlist/required subsetを再照合して項目IDをprefixにした1コミットを作る。task-owned `.pipeline` runtime artifactを追加stageしない。RF-00A/RF-00Nはmatrix作成前なので例外、RF-00Eはmatrix新規作成を自身の対象欄どおりstageする。RF-00Cだけは不変baselineとして`.pipeline/evidence/$MASTER_TASK/baseline-ui.json`、`baseline-integrity.json`、`screenshots/baseline/**`のexact 3 pathも同commitへstageしてよい。他のruntime artifactをRF-00Cへ混ぜない。RF-00B以降は自身のentryを`planned`から`active`へ変える差分を同じcommitへ必ず含める。matrixの他ID、runner schema、required suiteを同時変更してはいけない。
8. commit後にcommit SHAをsession/evidenceへ記録する。これによりtask-owned artifactはdirtyになってよいが、手順1の除外付きstatusは空でなければならない。

GitNexus indexがstaleの場合、対象symbol編集前に再解析する。再解析が `AGENTS.md`、`CLAUDE.md` 等を変更する実装なら、その生成差分を勝手に破棄・commitせず中断して報告する。

各項目の「戻し方」は本節を優先する。本計画の共通rollbackは「失敗branchとevidenceを保持し、最後に合格したcommit SHAから新しいworktree/branchを作り、修正済みの同一項目を再実行する」である。source commit後に問題が判明した場合も、同じbranchで打消しcommitを作らない。項目rollback欄に`revert`を含む計画はmatrix linterで拒否し、漏洩可能性のあるcredential rotationや外部資源cleanupだけをGit rollbackと別の運用作業として報告する。

retryは失敗項目によって入口を分ける。RF-00Aのinitial transactionがcommit後の`rf-00a-postcommit` capture/verifyまで到達できなかった場合は**再試行しない**。そのbranch、worktree、stdout/stderr、review artifact、作成済みruntime artifactを変更せず保全し、master taskをblockedとして報告する。再開には、元の調査基準SHAとreview対象4 fileを入力に別master task IDを発行し、そのtask用のstatic artifact、外部review、独立review、pre-implementation gateをすべて作り直した新計画が必要である。未commit helper、途中生成artifact、旧taskのapproval/review gateを新taskへcopyしてはならない。

RF-00N/RF-00EとR2〜R7 bootstrapは、RF-00Aでtracked固定する`refactor-bootstrap-receipt.py`がcontrol rootへsingle-writeした直前合格stage receiptからだけtask artifactをrestoreする。receipt pathは`$CONTROL_ROOT/.pipeline/bootstrap-archives/$TASK/attempts/<attempt-id>/<rf-00a-postcommit|rf-00n-postcommit|release-bootstrap>/`、attempt IDは`initial`またはhelperがCSPRNGで発行しreceiptへ固定した`retry-<failed-stage>-<32 lowercase hex>`である。過去attemptを上書き・削除せず、branch/path/last-good SHA、approved copy 5 hash、task lifecycle/build/state/session/GitNexus report全file hashを照合する。`node_modules,.venv,.pipeline/tmp`はcopyせず、restore後にNode 22/npm 10とlock/integrity前後一致を確認して`npm ci --prefix scripts/test/gitnexus-runtime`を再実行し、`gitnexus --version=1.6.9`を確認してからpre-impactへ進む。network/cache不足はblockedで、global toolへfallbackしない。RF-00B以降は、**最後に合格したcommitのtracked transfer helper**を使う。失敗worktree内の未commit helperを実行してはいけない。

RF-00N/RF-00Eのretry、または成功済みR2〜R7 bootstrap worktreeの消失からの復元は次のliteral手順だけを使う。`FAILED_BOOTSTRAP_STAGE=rf-00n|rf-00e|release-worktree-recovery`とし、source stageは順に`rf-00a-postcommit|rf-00n-postcommit|release-bootstrap`、`LAST_GOOD_SHA`はsource receiptの値とexact一致させる。R2〜R7 bootstrapが`release-bootstrap` capture前に失敗した場合、partial task stateをrestoreせず、直前release archiveを正本に`bootstrap-refactor-release.sh --attempt-id <new CSPRNG token>`で全bootstrapを新branch/pathへ最初から再実行する。旧branch/pathは保持する。

```bash
set -Eeuo pipefail
BOOTSTRAP_RESTORE_STAGE=validate-source
bootstrap_restore_failure() {
  local rc=$?
  trap - ERR
  printf 'bootstrap restore failed at %s (exit=%s); preserve source and partial destination\n' \
    "$BOOTSTRAP_RESTORE_STAGE" "$rc" >&2
  exit "$rc"
}
trap bootstrap_restore_failure ERR
test -n "${MASTER_TASK:-}"
test -n "${RELEASE_ID:-}"
case "$RELEASE_ID" in r1|r2|r3|r4|r5|r6|r7) ;; *) exit 2 ;; esac
test -n "${TASK:-}"
test "$TASK" = "${MASTER_TASK}-${RELEASE_ID}"
test -n "${FAILED_BOOTSTRAP_STAGE:-}"
test -n "${SOURCE_ATTEMPT_ID:-}"
test -n "${SOURCE_STAGE:-}"
test -n "${LAST_GOOD_SHA:-}"
case "$FAILED_BOOTSTRAP_STAGE:$SOURCE_STAGE" in
  rf-00n:rf-00a-postcommit|rf-00e:rf-00n-postcommit|release-worktree-recovery:release-bootstrap) ;;
  *) exit 2 ;;
esac
CONTROL_ROOT="$(
  git rev-parse --path-format=absolute --git-common-dir | \
    python3 -I -S -c 'import pathlib,sys; print(pathlib.Path(sys.stdin.read().strip()).resolve().parent)'
)"
BOOTSTRAP_TOOL_DIR="$(mktemp -d)"
git show "$LAST_GOOD_SHA:scripts/test/refactor-bootstrap-receipt.py" > \
  "$BOOTSTRAP_TOOL_DIR/refactor-bootstrap-receipt.py"
test "$(git hash-object "$BOOTSTRAP_TOOL_DIR/refactor-bootstrap-receipt.py")" = \
     "$(git rev-parse "$LAST_GOOD_SHA:scripts/test/refactor-bootstrap-receipt.py")"
TRUSTED_PYTHON="$(
  env -i PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin \
    sh -c 'command -v python3.12 || command -v python3.11 || command -v python3'
)"
case "$TRUSTED_PYTHON" in "$CONTROL_ROOT"/*) exit 2 ;; esac
BOOTSTRAP_ATTEMPT_ID="$(
  env -i PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin \
    "$TRUSTED_PYTHON" -I -S \
    "$BOOTSTRAP_TOOL_DIR/refactor-bootstrap-receipt.py" new-attempt \
      --control-root "$CONTROL_ROOT" \
      --task "$TASK" \
      --failed-stage "$FAILED_BOOTSTRAP_STAGE" \
      --from-attempt "$SOURCE_ATTEMPT_ID" \
      --from-stage "$SOURCE_STAGE" \
      --expected-last-good-sha "$LAST_GOOD_SHA"
)"
env -i ATTEMPT="$BOOTSTRAP_ATTEMPT_ID" \
  "$TRUSTED_PYTHON" -I -S -c \
  'import os,re; assert re.fullmatch(r"retry-[a-z0-9-]+-[0-9a-f]{32}", os.environ["ATTEMPT"])'
BOOTSTRAP_RETRY_PATH="$CONTROL_ROOT/.pipeline/worktrees/$TASK-$BOOTSTRAP_ATTEMPT_ID/checkout"
BOOTSTRAP_RETRY_BRANCH="codex/$TASK-$BOOTSTRAP_ATTEMPT_ID"
test ! -e "$BOOTSTRAP_RETRY_PATH"
if git show-ref --verify --quiet "refs/heads/$BOOTSTRAP_RETRY_BRANCH"; then
  exit 2
fi
BOOTSTRAP_RESTORE_STAGE=restore-new-destination
env -i PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin \
  "$TRUSTED_PYTHON" -I -S \
  "$BOOTSTRAP_TOOL_DIR/refactor-bootstrap-receipt.py" restore \
    --control-root "$CONTROL_ROOT" \
    --task "$TASK" \
    --from-attempt "$SOURCE_ATTEMPT_ID" \
    --from-stage "$SOURCE_STAGE" \
    --to-attempt "$BOOTSTRAP_ATTEMPT_ID" \
    --expected-last-good-sha "$LAST_GOOD_SHA" \
    --destination "$BOOTSTRAP_RETRY_PATH" \
    --branch "$BOOTSTRAP_RETRY_BRANCH"
cd "$BOOTSTRAP_RETRY_PATH"
test "$(git rev-parse HEAD)" = "$LAST_GOOD_SHA"
test "$(git branch --show-current)" = "$BOOTSTRAP_RETRY_BRANCH"
env -i PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin \
  "$TRUSTED_PYTHON" -I -S \
  "$BOOTSTRAP_TOOL_DIR/refactor-bootstrap-receipt.py" verify-only \
    --control-root "$CONTROL_ROOT" \
    --task "$TASK" \
    --attempt-id "$BOOTSTRAP_ATTEMPT_ID" \
    --stage restore \
    --expected-head "$LAST_GOOD_SHA"
npm ci --prefix scripts/test/gitnexus-runtime
test "$(scripts/test/gitnexus-runtime/node_modules/.bin/gitnexus --version)" = "1.6.9"
trap - ERR
```

R2〜R7の`release-bootstrap` capture前失敗だけは上のrestoreを使わない。直前releaseのimmutable archiveを入力に、次のliteral commandで新tokenを発行し、新branch/pathへbootstrapを全文再実行する。`PREVIOUS_TASK`と`RELEASE_BASE_SHA`は`release-boundaries.json`および直前merge recordから導出した値とexact一致させる。current taskのpartial plan/evidence/gate/worktreeをcopy sourceにせず、既存pathを削除しない。

```bash
set -Eeuo pipefail
test -n "${MASTER_TASK:-}"
test -n "${RELEASE_ID:-}"
case "$RELEASE_ID" in r2|r3|r4|r5|r6|r7) ;; *) exit 2 ;; esac
test -n "${TASK:-}"
test -n "${PREVIOUS_TASK:-}"
test -n "${RELEASE_BASE_SHA:-}"
test -n "${TARGET_ENVIRONMENT_ID:-}"
test -n "${DEPLOYMENT_FINGERPRINT_SHA256:-}"
CONTROL_ROOT="$(
  git rev-parse --path-format=absolute --git-common-dir | \
    python3 -I -S -c 'import pathlib,sys; print(pathlib.Path(sys.stdin.read().strip()).resolve().parent)'
)"
TRUSTED_PYTHON="$(
  env -i PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin \
    sh -c 'command -v python3.12 || command -v python3.11 || command -v python3'
)"
case "$TRUSTED_PYTHON" in "$CONTROL_ROOT"/*) exit 2 ;; esac
BOOTSTRAP_TOOL_DIR="$(mktemp -d)"
git -C "$CONTROL_ROOT" show \
  "$RELEASE_BASE_SHA:scripts/test/refactor-bootstrap-receipt.py" > \
  "$BOOTSTRAP_TOOL_DIR/refactor-bootstrap-receipt.py"
test "$(git -C "$CONTROL_ROOT" hash-object "$BOOTSTRAP_TOOL_DIR/refactor-bootstrap-receipt.py")" = \
     "$(git -C "$CONTROL_ROOT" rev-parse "$RELEASE_BASE_SHA:scripts/test/refactor-bootstrap-receipt.py")"
ARCHIVE_ROOT="$CONTROL_ROOT/.pipeline/release-archives"
BOOTSTRAP_ATTEMPT_ID="$(
  env -i PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin \
    "$TRUSTED_PYTHON" -I -S \
    "$BOOTSTRAP_TOOL_DIR/refactor-bootstrap-receipt.py" new-release-attempt \
      --control-root "$CONTROL_ROOT" \
      --archive-root "$ARCHIVE_ROOT" \
      --task "$TASK" --release "$RELEASE_ID" \
      --previous-task "$PREVIOUS_TASK" \
      --expected-base "$RELEASE_BASE_SHA"
)"
env -i ATTEMPT="$BOOTSTRAP_ATTEMPT_ID" "$TRUSTED_PYTHON" -I -S -c \
  'import os,re; assert re.fullmatch(r"retry-release-bootstrap-[0-9a-f]{32}", os.environ["ATTEMPT"])'
BOOTSTRAP_HOLDER="$(mktemp -d)"
BOOTSTRAP_DETACHED_ROOT="$BOOTSTRAP_HOLDER/checkout"
git -C "$CONTROL_ROOT" worktree add --detach \
  "$BOOTSTRAP_DETACHED_ROOT" "$RELEASE_BASE_SHA"
test "$(git -C "$BOOTSTRAP_DETACHED_ROOT" rev-parse HEAD)" = "$RELEASE_BASE_SHA"
bash "$BOOTSTRAP_DETACHED_ROOT/scripts/test/bootstrap-refactor-release.sh" \
  --master-task "$MASTER_TASK" --release "$RELEASE_ID" \
  --base "$RELEASE_BASE_SHA" --control-root "$CONTROL_ROOT" \
  --attempt-id "$BOOTSTRAP_ATTEMPT_ID" \
  --target-environment-id "$TARGET_ENVIRONMENT_ID" \
  --deployment-fingerprint-sha256 "$DEPLOYMENT_FINGERPRINT_SHA256"
```

RF-00B以降の**item実装中**retry worktreeは次のexact手順で作る。失敗worktree/branchをremove、force、renameしない。`LAST_GOOD_SHA`は失敗項目の`ITEM_BASE_SHA`、`FAILED_ID`は見出しIDである。生成token衝突時は自動でexit 2し、既存pathを再利用しない。新worktree内でもevidence task IDは元の`TASK`を使い、worktree metadata用`RETRY_TASK`だけを別名にする。

tracked transfer helperはcopy前に失敗元のtask-owned artifact全体をcontrol rootのappend-only `.pipeline/retry-source-archives/$TASK/$RETRY_TASK/`へregular-file inventory、SHA-256、source HEAD/statusとともにsingle-write保全する。destinationへ移せるのは次のcutoff allow-listだけである。

- masterとbyte一致するrelease-local approved copy 5 file、task固有lifecycle 4 file。ただしcheckpoint stateは`building`のみ。
- baseline/bootstrap provenance、dependency/GitNexus lock、既に完了したitem/phase evidenceのうち、artifactの`head_sha`が`LAST_GOOD_SHA`と同一またはancestorで、item順が`FAILED_ID`より前のもの。
- sessionは元`events.jsonl`を丸ごとcopyせず、各eventのcanonical hash chainを先頭から検証し、head-bound eventが`LAST_GOOD_SHA`以前かつ失敗item開始前までのprefixだけを`.pipeline/sessions/$TASK/retries/$RETRY_TASK/source-prefix.jsonl`へ書く。retry開始eventは別の`.pipeline/sessions/$TASK/retries/$RETRY_TASK/events.jsonl`から新hash chainとして開始し、元ledgerを変更しない。

次は常にdestination copy対象外とする: `FAILED_ID`以降のitem/phase report、release gate/manifest、full verification、evidence manifest/pack、external/Codex/dual review、tribunal、final UI、QA、outcome、approval/approval-target、closure stage、archive/merge/operator/cutover/postmerge artifact、およびfailed receipt。helperは`.pipeline/evidence/$TASK/retries/$RETRY_TASK/cutoff-manifest.json`へ`schema_version,task_id,retry_task_id,retry_token,failed_id,last_good_sha,source_head_sha,source_archive_path,source_archive_sha256,approved_copy_sha256,lifecycle_sha256,selected_files[{path,sha256,head_sha,kind}],excluded_files[{path,reason}],session_source_prefix_sha256,retry_session_path`をsorted/single-writeし、各selected `head_sha`のancestor関係とexcluded category 0-copyを`--verify-only`で再確認する。symlink/FIFO/socket/secret filename、分類不能なtask artifact、headを証明できないderived evidenceはcopyせずに成功扱いせずexit 2とする。RF-47 commitが`LAST_GOOD_SHA`のancestorならcontrol checkoutの`.worktrees/<retry-task>`、そうでなければcontrol checkoutの`.pipeline/worktrees/<retry-task>/checkout`を使う。

```bash
set -Eeuo pipefail
RETRY_STAGE=validate-source
retry_failure() {
  local rc=$?
  trap - ERR
  printf 'item retry failed at %s (exit=%s); preserve source and partial destination\n' \
    "$RETRY_STAGE" "$rc" >&2
  exit "$rc"
}
trap retry_failure ERR
test -n "${MASTER_TASK:-}"
test -n "${RELEASE_ID:-}"
test -n "${TASK:-}"
test -n "$LAST_GOOD_SHA"
test -n "$FAILED_ID"
FAILED_WORKTREE="$(git rev-parse --show-toplevel)"
git cat-file -e "$LAST_GOOD_SHA:scripts/test/transfer-refactor-retry-state.py"
FAILED_COMMON_DIR="$(
  git -C "$FAILED_WORKTREE" rev-parse --path-format=absolute --git-common-dir
)"
test "$(basename "$FAILED_COMMON_DIR")" = ".git"
CONTROL_ROOT="$(dirname "$FAILED_COMMON_DIR")"
test "$(git -C "$CONTROL_ROOT" rev-parse --show-toplevel)" = "$CONTROL_ROOT"
test "$(
  git -C "$CONTROL_ROOT" rev-parse --path-format=absolute --git-common-dir
)" = "$FAILED_COMMON_DIR"
test "$(git -C "$CONTROL_ROOT" remote get-url origin)" = \
     "$(git -C "$FAILED_WORKTREE" remote get-url origin)"
git -C "$CONTROL_ROOT" cat-file -e "$LAST_GOOD_SHA^{commit}"
RETRY_TOKEN="$(date -u '+%Y%m%dT%H%M%SZ')-$$"
RETRY_TASK="${TASK}-retry-${FAILED_ID#RF-}-${RETRY_TOKEN}"
RETRY_BRANCH="codex/${RETRY_TASK}"
if git log --format=%s "$LAST_GOOD_SHA" | \
  grep -Fxq 'RF-47 keep harness checkouts out of evidence storage'; then
  RETRY_PATH="$CONTROL_ROOT/.worktrees/$RETRY_TASK"
else
  RETRY_PATH="$CONTROL_ROOT/.pipeline/worktrees/$RETRY_TASK/checkout"
fi
test ! -e "$RETRY_PATH"
if git show-ref --verify --quiet "refs/heads/$RETRY_BRANCH"; then
  exit 2
fi
RETRY_TOOL_DIR="$(mktemp -d)"
trap 'rm -rf -- "$RETRY_TOOL_DIR"' EXIT
git show "$LAST_GOOD_SHA:scripts/harness/worktree.sh" > \
  "$RETRY_TOOL_DIR/worktree.sh"
git show "$LAST_GOOD_SHA:scripts/test/transfer-refactor-retry-state.py" > \
  "$RETRY_TOOL_DIR/transfer-refactor-retry-state.py"
chmod 0500 "$RETRY_TOOL_DIR/worktree.sh"
chmod 0400 "$RETRY_TOOL_DIR/transfer-refactor-retry-state.py"
test "$(git hash-object "$RETRY_TOOL_DIR/worktree.sh")" = \
     "$(git rev-parse "$LAST_GOOD_SHA:scripts/harness/worktree.sh")"
test "$(git hash-object "$RETRY_TOOL_DIR/transfer-refactor-retry-state.py")" = \
     "$(git rev-parse "$LAST_GOOD_SHA:scripts/test/transfer-refactor-retry-state.py")"
TRUSTED_PYTHON="$(
  env -i PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin \
    sh -c 'command -v python3.12 || command -v python3.11 || command -v python3'
)"
case "$TRUSTED_PYTHON" in
  "$FAILED_WORKTREE"/*|"$CONTROL_ROOT"/*|"$RETRY_TOOL_DIR"/*) exit 2 ;;
esac
test -x "$TRUSTED_PYTHON"
(
  cd "$RETRY_TOOL_DIR"
  env -i PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin \
    "$TRUSTED_PYTHON" -I -S transfer-refactor-retry-state.py \
      --preflight-source \
      --source "$FAILED_WORKTREE" \
      --control-root "$CONTROL_ROOT" \
      --task "$TASK" \
      --retry-task "$RETRY_TASK" \
      --last-good-sha "$LAST_GOOD_SHA" \
      --failed-id "$FAILED_ID"
)
(
  cd "$CONTROL_ROOT"
  RETRY_STAGE=create-worktree
  env -i PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin \
    bash "$RETRY_TOOL_DIR/worktree.sh" create "$RETRY_TASK" \
      --base "$LAST_GOOD_SHA" \
      --path "$RETRY_PATH" \
      --branch "$RETRY_BRANCH"
)
(
  cd "$RETRY_TOOL_DIR"
  env -i PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin \
    "$TRUSTED_PYTHON" -I -S transfer-refactor-retry-state.py \
      --source "$FAILED_WORKTREE" \
      --destination "$RETRY_PATH" \
      --control-root "$CONTROL_ROOT" \
      --task "$TASK" \
      --retry-task "$RETRY_TASK" \
      --last-good-sha "$LAST_GOOD_SHA" \
      --failed-id "$FAILED_ID"
)
cd "$RETRY_PATH"
test "$(git rev-parse HEAD)" = "$LAST_GOOD_SHA"
cmp -s ".pipeline/plans/$TASK/plan.md" ".pipeline/plans/$MASTER_TASK/plan.md"
test -f ".pipeline/evidence/$TASK/retries/$RETRY_TASK/state-transfer.json"
export REPO_ROOT="$RETRY_PATH"
export RF_ENV_ROOT="$REPO_ROOT/.pipeline/tmp/$TASK/env"
test "$(node --version)" = "v22.$(node --version | cut -d. -f2-)"
test "$(npm --version | cut -d. -f1)" = "10"
GITNEXUS_LOCK_BEFORE="$(
  python3 -I -S -c 'import hashlib,pathlib; print(hashlib.sha256(pathlib.Path("scripts/test/gitnexus-runtime/package-lock.json").read_bytes()).hexdigest())'
)"
npm ci --prefix scripts/test/gitnexus-runtime
GITNEXUS_LOCK_AFTER="$(
  python3 -I -S -c 'import hashlib,pathlib; print(hashlib.sha256(pathlib.Path("scripts/test/gitnexus-runtime/package-lock.json").read_bytes()).hexdigest())'
)"
test "$GITNEXUS_LOCK_BEFORE" = "$GITNEXUS_LOCK_AFTER"
test "$(
  scripts/test/gitnexus-runtime/node_modules/.bin/gitnexus --version
)" = "1.6.9"
bash scripts/test/bootstrap-refactor-env.sh --reuse-constraints
"$RF_ENV_ROOT/backend/bin/python" scripts/test/transfer-refactor-retry-state.py \
  --verify-only \
  --destination "$RETRY_PATH" \
  --control-root "$CONTROL_ROOT" \
  --task "$TASK" \
  --retry-task "$RETRY_TASK" \
  --last-good-sha "$LAST_GOOD_SHA" \
  --failed-id "$FAILED_ID"
test "$(git branch --show-current)" = "$RETRY_BRANCH"
bash scripts/harness/backcast-state.sh "$TASK" building \
  --allow-same \
  --reason "retry $FAILED_ID from immutable last-good SHA $LAST_GOOD_SHA"
trap - ERR
```

共通retry protocolを使えるのはcheckpoint state=`building`で単一itemのcommit前検証に失敗した場合だけである。phase/release/closure gateのack喪失は同じcanonical selection/stage attemptの`--verify-existing`でdestination/hashを再確認し、writerやtestを再実行しない。実failure時の遷移はentry stateに合わせ、phase gateは`building -> blocked`、9.1で`built`到達後かつ`verifying`前なら`built -> verifying -> verification_failed -> blocked`、release/closure gateの`verifying`中は`verifying -> verification_failed -> blocked`とする。既に`awaiting_approval|approved|merged|completed`のstageで実failureが出た場合は過去stateを変更せず、failed selection/receiptをappend-onlyで残してterminal停止する。どのcaseもsource/static/matrix/既存commitを同taskで変更しない。失敗reportから見出し順最初のitem/suiteと最後の変更itemを診断情報として算出しても、後続commitのcherry-pick/rebase/再実装をこのtaskで開始してはならない。修復は、失敗taskのimmutable evidenceと元planを入力に別task IDのremediation planを独立reviewしてから行う。`test_item_retry_requires_building_state_and_cutoff_manifest`、`test_phase_failure_uses_legal_building_to_blocked_transition`、`test_release_and_closure_failure_use_legal_state_specific_terminal_transition`、`test_gate_ack_loss_uses_verify_existing_without_reexecution`で固定する。

### 2.3 Required suiteの略号

各項目の「完了条件」に記載する略号は、repo rootで次を実行することを意味する。各shellの先頭で必ず次を設定する。

```bash
set -Eeuo pipefail
export REPO_ROOT="$(git rev-parse --show-toplevel)"
test -n "${TASK:-}"
export RF_ENV_ROOT="$REPO_ROOT/.pipeline/tmp/$TASK/env"
cd "$REPO_ROOT"
RF_SUITE_TMP="$(mktemp -d)"
trap 'rm -rf -- "$RF_SUITE_TMP"' EXIT

validate_local_node_bin() {
  local package_root="$1"
  local binary="$2"
  test -x "$binary"
  "$RF_ENV_ROOT/backend/bin/python" -I -S - "$package_root" "$binary" <<'PY'
import pathlib, stat, sys
root = (pathlib.Path(sys.argv[1]) / "node_modules").resolve(strict=True)
binary = pathlib.Path(sys.argv[2]).resolve(strict=True)
assert binary.is_relative_to(root)
assert stat.S_ISREG(binary.stat().st_mode)
PY
}

# V-MEETING
PYTHONDONTWRITEBYTECODE=1 "$RF_ENV_ROOT/backend/bin/python" -m pytest -p no:cacheprovider \
  services/meeting-api/tests \
  --ignore=services/meeting-api/tests/test_integration_live.py -q

# V-BACKEND
PYTHONDONTWRITEBYTECODE=1 "$RF_ENV_ROOT/backend/bin/python" -m pytest -p no:cacheprovider \
  services/api-gateway/tests services/admin-api/tests services/agent-api/tests \
  services/calendar-service/tests services/runtime-api/tests -q

# V-TRANSCRIPTION
PYTHONDONTWRITEBYTECODE=1 "$RF_ENV_ROOT/transcription/bin/python" -m pytest -p no:cacheprovider \
  services/transcription-service/tests -q

# V-DASH
validate_local_node_bin "$REPO_ROOT/services/dashboard" \
  "$REPO_ROOT/services/dashboard/node_modules/.bin/tsc"
(cd "$REPO_ROOT/services/dashboard" && \
  npm test && \
  ./node_modules/.bin/tsc --noEmit && \
  node scripts/check-lint-baseline.mjs tests/fixtures/lint-baseline.json && \
  VEXA_API_URL=http://localhost:8056 npm run build)

# V-DASH-FINAL
validate_local_node_bin "$REPO_ROOT/services/dashboard" \
  "$REPO_ROOT/services/dashboard/node_modules/.bin/tsc"
(cd "$REPO_ROOT/services/dashboard" && \
  npm test && \
  ./node_modules/.bin/tsc --noEmit && \
  npm run lint && \
  VEXA_API_URL=http://localhost:8056 npm run build)

# V-TRANSCRIPT
validate_local_node_bin "$REPO_ROOT/packages/transcript-rendering" \
  "$REPO_ROOT/packages/transcript-rendering/node_modules/.bin/tsc"
(cd "$REPO_ROOT/packages/transcript-rendering" && \
  npm test && ./node_modules/.bin/tsc --noEmit)

# V-CORE
validate_local_node_bin "$REPO_ROOT/services/vexa-bot/core" \
  "$REPO_ROOT/services/vexa-bot/core/node_modules/.bin/tsc"
(cd "$REPO_ROOT/services/vexa-bot/core" && \
  ./node_modules/.bin/tsc --noEmit --incremental false && npm test)

# V-INTEGRATIONS
PYTHONDONTWRITEBYTECODE=1 "$RF_ENV_ROOT/integrations/bin/python" -m pytest -p no:cacheprovider \
  services/mcp/tests \
  services/telegram-bot/tests -q

# V-CLIENTS
"$RF_ENV_ROOT/integrations/bin/python" -m pip check
"$RF_ENV_ROOT/integrations/bin/python" -c \
  'import vexa_cli, vexa_client; print("client imports: ok")'
"$RF_ENV_ROOT/integrations/bin/vexa" --help >/dev/null
"$RF_ENV_ROOT/integrations/bin/python" -m json.tool \
  packages/redaction-tests/secret-redaction-cases.json >/dev/null
test -s packages/kabosu-persona/persona.ja.md

# V-AUX
PYTHONDONTWRITEBYTECODE=1 "$RF_ENV_ROOT/aux/bin/python" -m pytest -p no:cacheprovider \
  services/wake-stt/tests services/wake-orchestrator/tests \
  services/tts-service/tests services/voiceprint-service/tests -q

# V-OPS
"$RF_ENV_ROOT/backend/bin/python" -m pytest -p no:cacheprovider tests3/unit -q
OPS_SHELL_LIST="$RF_SUITE_TMP/v-ops-shells.txt"
rg --files -g '*.sh' >"$OPS_SHELL_LIST"
test -s "$OPS_SHELL_LIST"
while IFS= read -r file; do /bin/bash -n "$file"; done <"$OPS_SHELL_LIST"
"$RF_ENV_ROOT/backend/bin/python" tests3/docs/check.py
PATH="$RF_ENV_ROOT/backend/bin:$PATH" make -C tests3 smoke

# V-HARNESS-CONTRACT
"$RF_ENV_ROOT/backend/bin/python" -m pytest -p no:cacheprovider \
  tests3/unit -q -k 'harness or adapter or worktree or consultation or workflow'
HARNESS_SHELL_LIST="$RF_SUITE_TMP/v-harness-shells.txt"
rg --files scripts/harness -g '*.sh' >"$HARNESS_SHELL_LIST"
for hook in \
  .claude/hooks/adapter-validate.sh \
  .claude/hooks/approval-hash-check.sh \
  .claude/hooks/backcast-validate.sh \
  .claude/hooks/codex-review-validate.sh \
  .claude/hooks/dual-review-validate.sh \
  .claude/hooks/external-consultation-validate.sh \
  .claude/hooks/feedback-prune.sh \
  .claude/hooks/pr-ready-gate.sh \
  .claude/hooks/pre-implementation-review-gate.sh; do
  test -f "$hook"
  "$RF_ENV_ROOT/backend/bin/python" -I -S - "$hook" >>"$HARNESS_SHELL_LIST" <<'PY'
import pathlib, sys
print(pathlib.Path(sys.argv[1]).resolve(strict=True))
PY
done
test "$(wc -l <"$HARNESS_SHELL_LIST" | tr -d ' ')" -ge 12
while IFS= read -r file; do /bin/bash -n "$file"; done <"$HARNESS_SHELL_LIST"

# V-ITEM: 各項目の項目固有test。RF-00Eで全IDを明示登録する
bash scripts/test/run-refactor-item.sh RF-XX
```

`run-required-suites.sh`はNode suite開始前に、対象packageの`package-lock.json` SHA-256がRF-00E bootstrapの`dependency-locks.json`に記録された同path hashと一致し、`node_modules/.bin/tsc`の解決先がそのpackageの`node_modules`配下のregular executableであることを上記helperどおり検証する。lock entry欠落、hash差、binary欠落/escapeはfallbackせずexit 2。`npx`、global binary、downloadは使わない。

`V-DASH` の`check-lint-baseline.mjs`はESLint JSONの `{relative_file, ruleId, message, severity}` multisetを項目0のbaselineと比較し、新規signature 0、各signature件数増加0、総error/warning増加0だけを合格にする。ESLint自身の非0を握り潰すのではなく、既知集合に限定して暫定合格させる。RF-75では`V-DASH-FINAL`を使い、raw lint exit 0/error 0を必須とする。buildがversion fileや`.next`を生成する場合、tracked差分0を確認し、生成物をcommitしない。

各項目に列挙した `file::test` や `test.ts::case` は仕様名であり、それ単独をshellへ貼らない。次の`RF-XX`を現在の見出しIDへ1回だけ置換して`ITEM_ID`を設定し、実行commandは必ず次の2本にする。

```bash
set -Eeuo pipefail
ITEM_ID=RF-XX
test -n "${ITEM_ID:-}"
case "$ITEM_ID" in RF-*) ;; *) exit 2 ;; esac
bash scripts/test/run-refactor-item.sh "$ITEM_ID"
bash scripts/test/run-required-suites.sh "$ITEM_ID"
```

RF-00Eで作る `scripts/test/refactor-item-matrix.json` は1項目を1 objectとして、1項目内の複数言語・複数cwdを `commands[]` で表現する。shell文字列は保存せず、argv配列だけを subprocessへ渡す。schemaは次に固定する。

```json
{
  "schema_version": "1.0",
  "task_id": "full-repo-refactoring-2026-07-24",
  "items": {
    "RF-XX": {
      "commands": [
        {
          "id": "unique-within-item",
          "status": "planned|active",
          "replay_policy": "replayable|once",
          "runner": "pytest|vitest|core-registry|shell-json|argv",
          "cwd": ".",
          "venv": "backend|transcription|integrations|aux|null",
          "argv": ["literal", "arguments", "only"],
          "planned_paths": ["future/test/path"],
          "expected": {
            "exit_code": 0,
            "collected_min": 1,
            "skipped": 0,
            "xfailed": 0,
            "required_test_names": ["test_exact_contract_name"]
          }
        }
      ],
      "known_xfails": [
        {
          "nodeid": "repo/relative/test.py::test_exact_case[param]",
          "resolved_by": "RF-YY"
        }
      ],
      "required_suites": ["V-MEETING"]
    }
  }
}
```

`expected`はrunner別schemaとする。`pytest|vitest|core-registry|shell-json`は上例の`exit_code,collected_min,skipped,xfailed,required_test_names`を必須にし、実行時0件収集または必須test名未収集をexit 2にする。`required_test_names`は各項目の完了条件に列挙したtest関数/case名をbrace省略せず1件ずつ入れ、空配列を禁止する。`argv`は`exit_code`と任意の`stdout_exact`/`stdout_regex`/`stderr_regex`だけを許可し、存在しないcollection fieldを要求しない。`replay_policy="once"`を許すのはRF-00Nのcommit時bootstrap reportとA.5のRF-00C baseline captureだけで、その他は全て`replayable`とする。RF-00Aは`commands=[]`の専用inline-before-runner modeでありreplay policyを持たない。

`pytest`は選択venvのPythonへ `-m pytest -p no:cacheprovider -vv -rA` とargvを渡し、collection nodeidとsummaryをparseする。`vitest`はmatrixのcwdから`<cwd>/node_modules/.bin/vitest`を解決し、realpathが`<cwd>/node_modules/`配下、実行可能、Dashboard lockfileで解決したVitest packageのbinと一致することを確認してから`run --reporter=json --outputFile=<task tmp>`を直接argv実行する。global package runner、PATH上の同名command、download fallbackは禁止する。`core-registry`はRF-00Cで実装する `run-tests.mjs --file <path> --report-json <task tmp>` を直接呼ぶ。`shell-json`はA.1aのZoom runner 1 pathだけを`bash <path> --report-json <task tmp> --case <literal case>...`で直接実行し、出力JSONのcollection/count/test名を同じtest schemaへ正規化する。`argv`はAppendix A.3/A.5にliteral登録した`test,bash,node,docker,/usr/bin/python3`だけをshell評価なしで実行する。`bash` argvはrepo内の固定scriptまたは`-n`だけ、`node` argvはrepo内の固定scriptだけ、`docker` argvはA.5とのbyte一致だけを許す。`/usr/bin/python3`はA.3のRF-00N exact argv 1件だけに許可し、absolute pathをPATH探索せず使う。runnerは実行前にregular executable、root所有、group/world非writable、Python 3.9以上を確認し、argv位置1〜2がexact `-I,-S`、位置3がcurrent HEADでtrackedされたrepo内regular file `scripts/test/test_gitnexus_refactor_bootstrap.py`、位置4がexact `--verify-committed-artifacts`であることを照合する。別interpreter、symlink、`-c|-m|-`、追加引数、別script、PATH fallbackはcommand 0でexit 2にする。Docker commandの直前にはrunner自身が`docker context show`のstdout=`default`、`DOCKER_HOST`未設定、`docker info` exit 0を確認し、1条件でも外れればDockerを呼ばずexit 2にする。現Coreの`npm test`へ存在しない`--file/--filter`を渡してはいけない。unknown runner/venv/ID、duplicate JSON key、空commands、空argv、test runnerの実行時0件収集、required test name不足、unexpected skip/xfailをexit 2にする。

`known_xfails`はRF-00B/RF-00Dだけに許し、Appendix A.6のexact nodeidと`resolved_by`をそのまま登録する。runnerは`resolved_by` itemが`planned`の間だけそのnodeidをstrict xfailとして期待し、resolver itemが`active`になった実行では、resolver自身のcommandに加えて所有元RF-00B/RF-00Dの該当commandを自動再実行し、そのnodeidがmarkerなしのnormal pass、xfail 0になった場合だけ成功とする。resolver commitは所有元test fileの該当caseを変更してよいが、所有元matrix entryを変更してはいけない。最終時は全resolverがactiveなのでknown xfail 0、skip 0を要求する。

全item/suite runnerはstdoutの成功文言やprocess exit codeだけを証拠にしない。各実行を`.pipeline/evidence/$TASK/test-reports/<item-or-suite>/<run-id>.json`へatomic writeし、共通field `schema_version,task_id,head_sha,runner_id,command_id,argv,cwd,started_at,finished_at,exit_code,status`と、`pytest|vitest|core-registry|shell-json`では`collected,passed,failed,skipped,xfailed,xpassed,required_test_names,required_test_status`、`argv`では`assertion_count,assertion_results`を必須にする。test runnerはnative JSON/JUnit reporterをparseし、`exit_code=0,collected>=expected.collected_min,failed=0,skipped=0,xfailed=0,xpassed=0`かつ全required testがnormal passのときだけ`status=pass`。argv runnerはAppendix A.3/A.5で固定した各commandにつきexact 1 assertionを作り、`assertion_count=そのreportのcommand数`、各resultを`{name,operator:"argv_expectation",expected,actual,passed}`とする。`expected`は`exit_code`と表にある`stdout_exact|stdout_regex|stderr_regex`だけ、`actual`は実exit codeと捕捉したstdout/stderr、`passed`は全指定field一致時だけtrue。assertion 0、unknown/missing key、regex compile失敗をfailにする。V-OPSのDocker/Make等で必要infraがない場合のskip/早期returnはpassへ変換せず`status=blocked,exit_code=2`。release/phase/full verifierは当該HEADのreport全件を再parseし、欠落・重複・unknown field・command argv差・mtimeだけ新しい旧report流用を拒否する。

RF-00E時点では後続test fileがまだ存在しないため、`--lint`はschema、task ID、見出しとのID parity、未知suite、空commandsだけを検査し、future file実在を要求しない。各後続項目は自身のtestを追加した同じcommitで該当commandを`planned`から`active`へ変える。`--check-ready <ID>`と通常実行時だけactive file/nodeid/cwd実在を検査し、最終`--check-all-ready`はRF-00A以外の全commandがactiveであることを要求する。RF-00Aだけは`mode=inline-before-runner,commands=[],required_suites=[]`として本文のexact commandを使う。`run-required-suites.sh`はIDのsuite列だけを上記定義どおり実行する。以後、項目固有test名を追加・変更する項目は同一コミットでmatrixも更新する。

### 対象欄の行範囲表記

全RF項目の`対象`欄は次の機械規則で解釈し、実行者の裁量で範囲を広げない。

- `path:開始-終了`はその行範囲、`path:1-末尾`はfile全体である。
- `新規 path`または行範囲のないbare file pathは、その項目で作る`path:1-末尾`を意味する。既存fileなのにbare pathなら同じく`path:1-末尾`であり、一部だけと推測しない。
- `{a,b}`は各literal fileへ展開し、末尾の行範囲を全fileへ適用する。`*`/`**`またはdirectoryは、既存tracked fileなら項目開始時のliteral `git ls-files -- <pattern>`、新規RF-00A reviewなら上記literal 6 file、新規RF-00C fixture/screenshotなら2.1のJSON resolverだけで得たrepo-relative regular fileをUTF-8 byte順に固定し、各fileの`1-末尾`を対象にする。`rg`結果をglob展開へ混ぜない。生成物、symlink先、untracked user fileは含めない。
- prefix table内の`**/`はdirectory階層0個以上を意味し、たとえば`libs/meeting-contracts/tests/**/*.py`は`libs/meeting-contracts/tests/test_x.py`にも一致する。実装は`PurePosixPath` parts比較で行い、host shell globの設定へ依存しない。
- endpoint、test名、lint rule、shell commandなどfile pathでないbacktickは行範囲対象ではなく、直前に列挙したfile内のsymbol/検証条件である。
- RF-00Eのmatrix linterは各項目の対象tokenをこの規則で正規化し、2.1の`write_target_allowlist/required_changed_targets/read_only_input/runtime_output`を同じresolverで生成する。0 fileへ展開する既存glob、repo外path、`..`、scope重複、同項目inventory外のfileを拒否する。論理symbol/caller検索は2.1のliteral `git grep -F`または記録済み`git grep -E`だけを使い、`rg`や独自globへ置換しない。展開後のexact `path:start-end`一覧をitem evidenceへ保存する。

## 3. 項目0: 安全網の構築

### 実装開始前の外部レビュー承認ゲート

これは実装項目ではなく、RF-00Aより前の停止条件である。このリポジトリではL作業の実装前に、exact plan hashへbindされたFable、Codex ultra、dual consensusが必須。

計画作成時のFable試行は `Not logged in` で失敗し、repository由来の計画を外部サービスへ送る権限も安全審査で承認されなかった。したがって、現時点ではpre-implementation gateは未合格であり、実行者は次を満たすまでRF-00Aを含む実装作業を開始しない。

0. このrepoの`.claude/{hooks,agents,rules,skills}`はtracked codeではなくcanonical `claude-dotfiles`へのabsolute symlinkである。環境所有者は実行AIへ渡す**隔離済みpackageの作成前**に、4 symlinkのraw targetから共通Git rootを導出し、そのrootへ`https://github.com/FoundD-oka/claude-dotfiles.git`のcommit `fb9bd5f0d2a20a52d9d48dc56d4080a65c5c3233`をdetached checkoutした状態で配置する。実行AIは既存の別commit checkoutをswitch/resetせず、symlinkを変更せず、現在の`closed/harness-init/`へfallbackしない。計画作成hostはこの条件を満たさず4 symlinkがbrokenなので、同hostのままでは正しい状態は`blocked: canonical hook package missing`である。別machineでraw absolute targetとpinned checkoutを事前再現できない場合もportable handoffではないため停止する。似たscriptの再実装、vendor、skipは禁止する。事前配置後に次をそのまま実行する。

```bash
set -euo pipefail
test -L .claude/hooks
test -x .claude/hooks/pre-implementation-review-gate.sh
test -x .claude/hooks/pr-ready-gate.sh
HOOK_REPO="$(git -C .claude/hooks rev-parse --show-toplevel)"
test "$(git -C "$HOOK_REPO" rev-parse HEAD)" = \
  "fb9bd5f0d2a20a52d9d48dc56d4080a65c5c3233"
python3 -I -S - "$HOOK_REPO" <<'PY'
import os, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve(strict=True)
for name in ("hooks", "agents", "rules", "skills"):
    link = pathlib.Path(".claude") / name
    assert link.is_symlink()
    raw = os.readlink(link)
    assert os.path.isabs(raw)
    expected = root / "skills/harness-init/shared/.claude" / name
    assert pathlib.Path(raw) == expected
    assert link.resolve(strict=True) == expected.resolve(strict=True)
PY
python3 -I -S - <<'PY'
import hashlib, pathlib
expected = {
    ".claude/hooks/adapter-validate.sh": "fc27f0398bde656d0e08af3971b84287a872a0050854bd26ce7493ac40b031db",
    ".claude/hooks/approval-hash-check.sh": "45a427ac65ecca99621a5f972ff5a981732e7963eca64fe78d790ed2f4a21197",
    ".claude/hooks/backcast-validate.sh": "d19e591955f67251ddbf09f0a94ff694772a71df17223d10970e52d7ce3cf174",
    ".claude/hooks/codex-review-validate.sh": "fb074be510fa146006dff81f8ca55cc8aeed6cd7f4091f15d3efb0966f29a6fe",
    ".claude/hooks/dual-review-validate.sh": "fccf291d3cf7cd9042ad1d2ef18832458933de76b3ead46cff57f8560826fbd1",
    ".claude/hooks/external-consultation-validate.sh": "2935f07bf82de2e0714dc88dd21b9a880afe3a5713f0c949136baf030c092ea6",
    ".claude/hooks/pre-implementation-review-gate.sh": "dfbce98f5be13a6b0b797d5dee75aecafbbe7ca8e2a557d9e05d37ee8a2aab0c",
    ".claude/hooks/pr-ready-gate.sh": "19bdf3c6271d6f754eb1ff8b07af06ffa708fd133fc9fae1751dd6deb392a2e6",
    ".claude/hooks/feedback-prune.sh": "d5156730b3fda276265a0485756b187f90467a783f2b2caeaaff612bc39ee8d6",
}
for name, digest in expected.items():
    assert hashlib.sha256(pathlib.Path(name).read_bytes()).hexdigest() == digest
PY
```

1. 外部reviewより先に、現在の計画checkoutで次をそのまま実行してcheckpointとS/M/L decisionを同じcheckpoint IDへ結び付ける。これはplanning artifact生成であり、RF-00Aの実装開始には数えない。

```bash
set -euo pipefail
export TASK=full-repo-refactoring-2026-07-24
bash scripts/harness/backcast-checkpoint.sh "$TASK" \
  --goal "既存公開契約と利用者可視意味を維持し、認証境界・競合・検証偽陽性を直して巨大責務と循環を計画順に分解する" \
  --current "調査HEAD b2bcae8e、既知問題とbaselineはplan.md 1.4〜1.5に固定" \
  --target "全項目固有test、required suite、構造gate、fixture E2Eが同一最終HEADで成功する" \
  --type validation \
  --approval-required \
  --max-files 500 \
  --condition "QC-SEC::認証・secret・outbound境界がfail closed::security contract全件pass、公開secret 0、未認証副作用0::final-tests" \
  --condition "QC-TEST::検査の実行事実と合否が一致::全item ready、失敗0、unexpected skip/xfail 0::final-tests" \
  --condition "QC-STRUCTURE::責務/循環/所有権budgetを満たす::structure/import/ownership gate全件pass::final-tests" \
  --condition "QC-E2E::利用者可視契約を維持::source SHA一致、console/page/network error 0、承認外visual差0::final-e2e" \
  --command "final-tests::bash scripts/test/run-full-refactor-verification.sh" \
  --command "final-e2e::bash scripts/test/verify-refactor-e2e-evidence.sh --mode final" \
  --allowed "services/**" \
  --allowed "packages/**" \
  --allowed "libs/**" \
  --allowed "deploy/**" \
  --allowed "tests3/**" \
  --allowed "scripts/**" \
  --allowed ".github/**" \
  --allowed ".harness/**" \
  --allowed "schemas/**" \
  --allowed "docs/**" \
  --allowed "MANIFEST.md" \
  --allowed ".pipeline/**" \
  --forbidden ".env*" \
  --forbidden "**/*.pem" \
  --forbidden "**/*.key" \
  --forbidden "**/node_modules/**" \
  --forbidden "**/.venv/**" \
  --forbidden "**/migrations/**" \
  --forbidden ".claude/**" \
  --forbidden "features/**"
bash scripts/harness/sml-decision.sh "$TASK" \
  --size L \
  --reason "複数service、auth、DB、UI、CI、Harnessを横断する" \
  --external-consultation required_for_l
```

checkpoint commandはdeterministic test/structure/E2Eだけを判定する。独立QA、sidechain、post review、outcome、人間承認はmanifest生成後の最終closureで別に判定し、未承認なのにcheckpoint conditionをpassさせない。

2. リポジトリ所有者から、redact済みplan/review materialをFable/Codex review providerへ送信する明示承認を得る。
3. providerへ正規loginする。現行wrapperはplan/briefを5,000文字でtruncateするため、Fable/Codex summaryの`brief_truncated=true`は補助相談の既知制約として許すが、全文reviewとは呼ばない。planning handoffには、別sessionのread-only reviewer 2名が4 static file全文を読んだ次のartifactを同梱する。実装者は自作・補完しない。

- `.pipeline/evidence/full-repo-refactoring-2026-07-24/plan-review/local-review-1.json`
- `.pipeline/evidence/full-repo-refactoring-2026-07-24/plan-review/local-review-2.json`
- `.pipeline/evidence/full-repo-refactoring-2026-07-24/plan-review/coverage-manifest.json`

各local reviewのtop-level schemaはexact `schema_version="1.0",reviewer_id,run_id,independent=true,verdict=pass|block,findings[],reviewed_plan_sha256,reviewed_contract_sha256,reviewed_visual_sha256,reviewed_release_boundaries_sha256,reviewed_byte_ranges`。`findings[]`の各要素はexact `finding_id,severity=info|low|medium|high|critical,action=advisory|must_fix,summary`。`reviewed_byte_ranges`は`plan.md,verification-contract.md,planned-visual-changes.json,release-boundaries.json`の順にexact 4件、それぞれexact `path,start_byte=0,end_byte=file_size,sha256`を持つ。4つのtop-level reviewed SHA fieldも対応fileのSHA-256と一致させる。coverage manifestのtop-level schemaはexact `schema_version="1.0",master_task_id,generated_from_review_ids,files,status`、files要素は同じ4 file順でexact `path,size,sha256,covered_ranges`、covered rangeは`local-review-1`,`local-review-2`のreviewer順にexact 2件の`reviewer_id,start_byte=0,end_byte=file_size`を持つ。`start_byte,end_byte,size`はJSON integerかつPythonで`type(value) is int`（`bool`不可）でなければならない。全JSONでduplicate key、unknown/missing key、配列のduplicate/extra/reorder、空ID、型違い、boolean-as-integerを拒否する。2 reviewer ID/run IDは相互に異なり、4 fileの全byteを両者がcoverし、全hash一致、must_fix/critical/high 0、`status=pass`でなければ失敗とする。RF-00A開始前に、計画文書そのものに含まれる次の標準Pythonだけのread-only verifierを実行し、stdout `local-plan-review-coverage: pass`を要求する。別fileや未tracked helperを前提にしない。

```bash
set -euo pipefail
python3 -I -S - <<'PY'
import hashlib, json, pathlib, re

def require(condition, message):
    if not condition:
        raise SystemExit(f"local-plan-review-coverage: {message}")

def strict_load(path):
    require(path.is_file() and not path.is_symlink(), f"not a regular file: {path}")
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"duplicate JSON key {key!r}: {path}")
            result[key] = value
        return result
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicates)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"local-plan-review-coverage: invalid JSON {path}: {exc}") from exc

root = pathlib.Path(".pipeline/plans/full-repo-refactoring-2026-07-24")
evidence = pathlib.Path(".pipeline/evidence/full-repo-refactoring-2026-07-24/plan-review")
names = ("plan.md", "verification-contract.md", "planned-visual-changes.json", "release-boundaries.json")
paths = tuple(str(root / name) for name in names)
raw_by_path = {path: pathlib.Path(path).read_bytes() for path in paths}
sha_by_path = {path: hashlib.sha256(raw).hexdigest() for path, raw in raw_by_path.items()}
sha_fields = (
    "reviewed_plan_sha256",
    "reviewed_contract_sha256",
    "reviewed_visual_sha256",
    "reviewed_release_boundaries_sha256",
)
review_keys = {
    "schema_version", "reviewer_id", "run_id", "independent", "verdict", "findings",
    *sha_fields, "reviewed_byte_ranges",
}
finding_keys = {"finding_id", "severity", "action", "summary"}
range_keys = {"path", "start_byte", "end_byte", "sha256"}
id_pattern = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*")
reviews = []
for number in (1, 2):
    review = strict_load(evidence / f"local-review-{number}.json")
    require(isinstance(review, dict) and set(review) == review_keys, f"review {number} schema")
    require(review["schema_version"] == "1.0", f"review {number} schema_version")
    for field in ("reviewer_id", "run_id"):
        require(isinstance(review[field], str) and id_pattern.fullmatch(review[field]), f"review {number} {field}")
    require(review["independent"] is True and review["verdict"] == "pass", f"review {number} verdict")
    require(isinstance(review["findings"], list), f"review {number} findings type")
    finding_ids = []
    for finding in review["findings"]:
        require(isinstance(finding, dict) and set(finding) == finding_keys, f"review {number} finding schema")
        require(isinstance(finding["finding_id"], str) and id_pattern.fullmatch(finding["finding_id"]), f"review {number} finding_id")
        require(finding["severity"] in {"info", "low", "medium", "high", "critical"}, f"review {number} finding severity")
        require(finding["action"] in {"advisory", "must_fix"}, f"review {number} finding action")
        require(isinstance(finding["summary"], str) and finding["summary"].strip(), f"review {number} finding summary")
        require(finding["action"] != "must_fix" and finding["severity"] not in {"high", "critical"}, f"review {number} blocking finding")
        finding_ids.append(finding["finding_id"])
    require(len(finding_ids) == len(set(finding_ids)), f"review {number} duplicate finding_id")
    for field, path in zip(sha_fields, paths):
        require(review[field] == sha_by_path[path], f"review {number} {field}")
    ranges = review["reviewed_byte_ranges"]
    require(isinstance(ranges, list) and len(ranges) == len(paths), f"review {number} range count")
    require([item.get("path") if isinstance(item, dict) else None for item in ranges] == list(paths), f"review {number} range order")
    for item, path in zip(ranges, paths):
        require(set(item) == range_keys, f"review {number} range schema {path}")
        require(type(item["start_byte"]) is int and type(item["end_byte"]) is int, f"review {number} range integer type {path}")
        require(item == {
            "path": path,
            "start_byte": 0,
            "end_byte": len(raw_by_path[path]),
            "sha256": sha_by_path[path],
        }, f"review {number} range value {path}")
    reviews.append(review)

reviewer_ids = [review["reviewer_id"] for review in reviews]
run_ids = [review["run_id"] for review in reviews]
require(len(set(reviewer_ids)) == len(set(run_ids)) == 2, "reviewer/run independence")
coverage = strict_load(evidence / "coverage-manifest.json")
coverage_keys = {"schema_version", "master_task_id", "generated_from_review_ids", "files", "status"}
file_keys = {"path", "size", "sha256", "covered_ranges"}
covered_range_keys = {"reviewer_id", "start_byte", "end_byte"}
require(isinstance(coverage, dict) and set(coverage) == coverage_keys, "coverage schema")
require(coverage["schema_version"] == "1.0", "coverage schema_version")
require(coverage["master_task_id"] == "full-repo-refactoring-2026-07-24", "coverage task")
require(coverage["status"] == "pass", "coverage status")
require(coverage["generated_from_review_ids"] == reviewer_ids, "coverage reviewer IDs/order")
items = coverage["files"]
require(isinstance(items, list) and len(items) == len(paths), "coverage file count")
require([item.get("path") if isinstance(item, dict) else None for item in items] == list(paths), "coverage file order")
for item, path in zip(items, paths):
    require(set(item) == file_keys, f"coverage file schema {path}")
    require(type(item["size"]) is int, f"coverage file size integer type {path}")
    require(item["size"] == len(raw_by_path[path]) and item["sha256"] == sha_by_path[path], f"coverage file value {path}")
    ranges = item["covered_ranges"]
    require(isinstance(ranges, list) and len(ranges) == 2, f"coverage range count {path}")
    require([entry.get("reviewer_id") if isinstance(entry, dict) else None for entry in ranges] == reviewer_ids, f"coverage reviewer order {path}")
    for entry, reviewer_id in zip(ranges, reviewer_ids):
        require(set(entry) == covered_range_keys, f"coverage range schema {path}")
        require(type(entry["start_byte"]) is int and type(entry["end_byte"]) is int, f"coverage range integer type {path}")
        require(entry == {
            "reviewer_id": reviewer_id,
            "start_byte": 0,
            "end_byte": len(raw_by_path[path]),
        }, f"coverage range value {path}")
print("local-plan-review-coverage: pass")
PY
```

RF-00Eは上の`strict_load`、exact key/type/order/cardinality、4 top-level SHA、4 full-byte range、2 reviewer independence assertionをbyte-equivalentなtracked `scripts/test/verify-local-plan-review-coverage.py`へ固定し、positive fixtureに加えてduplicate JSON key/path/reviewer/range、unknown/missing key、top-level SHA差、extra/reordered file/range、空ID、型違い、`start_byte/end_byte/size`へ`false|true`を入れるboolean-as-integerの各negative fixtureをtestする。canonical pre-implementation hookは外部Fable/Codex/dualを検証し、local reviewer coverageはこの独立verifierが検証する。片方をもう片方の代用にしない。

4. 外部wrapperへ次を実行する。

```bash
set -euo pipefail
bash scripts/harness/external-consultation.sh run \
  full-repo-refactoring-2026-07-24 --mode plan \
  --source .pipeline/plans/full-repo-refactoring-2026-07-24/plan.md
bash scripts/harness/codex-review.sh run \
  full-repo-refactoring-2026-07-24 --mode plan \
  --source .pipeline/plans/full-repo-refactoring-2026-07-24/plan.md
bash scripts/harness/dual-review.sh run \
  full-repo-refactoring-2026-07-24 --stage plan
bash .claude/hooks/pre-implementation-review-gate.sh \
  full-repo-refactoring-2026-07-24
```

5. MUST_FIX/不一致、local reviewer coverage 100%未満があればplanだけを直し、plan hashが変わるためexternal/local reviewを先頭から再実行する。
6. `pre-implementation-review.json`が`status=pass`、Fable/Codexのopen MUST_FIX 0、dual consensus `agreed=true`、coverage manifestがplan/contract/visual-plan/release-boundariesのexact SHAを持つ計画だけをRF-00Aでcommitする。4 static fileのどれか1 byteでも変わった場合、2名の全文reviewからやり直す。

外部送信を承認されない場合は、このrepoのHarness done definitionを満たせないため「計画は完成、実装開始は権限待ち」と報告して停止する。max-call fallbackを権限回避に使わない。

### RF-00A 作業前checkpointと隔離worktree

- 対象:
  - read-only input: Git repository全体、`scripts/harness/{backcast-checkpoint,worktree,build}.sh:1-末尾`、3章で事前配置したcanonical `claude-dotfiles@fb9bd5f0d2a20a52d9d48dc56d4080a65c5c3233`の`.claude/hooks/{adapter-validate,approval-hash-check,backcast-validate,codex-review-validate,dual-review-validate,external-consultation-validate,feedback-prune,pr-ready-gate,pre-implementation-review-gate}.sh:1-末尾`
  - 新規 `.pipeline/plans/full-repo-refactoring-2026-07-24/{request.md,plan.md,verification-contract.md,planned-visual-changes.json,release-boundaries.json,research-brief.md,option-matrix.md,kpi-backcast-roadmap.md}:1-末尾`
  - 新規 `.pipeline/evidence/full-repo-refactoring-2026-07-24/plan-review/{local-review-1.json,local-review-2.json,coverage-manifest.json}:1-末尾`
  - 新規 `.pipeline/evidence/full-repo-refactoring-2026-07-24/external-consultation/consultation-plan-summary.json:1-末尾`
  - 新規 `.pipeline/evidence/full-repo-refactoring-2026-07-24/codex-review/review-plan-summary.json:1-末尾`
  - 新規 `.pipeline/evidence/full-repo-refactoring-2026-07-24/dual-review/consensus-plan-summary.json:1-末尾`
  - 新規 `.pipeline/gates/full-repo-refactoring-2026-07-24/pre-implementation-review.json:1-末尾`
  - 新規 `.pipeline/plans/full-repo-refactoring-2026-07-24-r1/{goal-contract.json,current-state.md,checkpoint-contract.json,sml-decision.json,request.md,plan.md,verification-contract.md,planned-visual-changes.json,release-boundaries.json}:1-末尾`
  - 新規 `.pipeline/evidence/full-repo-refactoring-2026-07-24-r1/baseline.json:1-末尾`
  - 新規 `.pipeline/evidence/full-repo-refactoring-2026-07-24-r1/plan-review/{local-review-1.json,local-review-2.json,coverage-manifest.json}:1-末尾`
  - 新規 `.pipeline/evidence/full-repo-refactoring-2026-07-24-r1/external-consultation/consultation-plan-summary.json:1-末尾`
  - 新規 `.pipeline/evidence/full-repo-refactoring-2026-07-24-r1/codex-review/review-plan-summary.json:1-末尾`
  - 新規 `.pipeline/evidence/full-repo-refactoring-2026-07-24-r1/dual-review/consensus-plan-summary.json:1-末尾`
  - 新規 `.pipeline/gates/full-repo-refactoring-2026-07-24-r1/pre-implementation-review.json:1-末尾`
  - 新規 `scripts/test/refactor-bootstrap-receipt.py:1-末尾`
  - 新規 `scripts/test/test_refactor_bootstrap_receipt.py:1-末尾`
- 問題: 調査workspaceにはユーザー所有の未追跡成果物がある。これをbaseline commitへ混ぜる、stashする、削除する行為は不可。
- 変更:
  - `refactor-bootstrap-receipt.py`は標準ライブラリだけを使うtracked bootstrap helperとし、`new-attempt,new-release-attempt,capture,restore,verify-only`だけを持つ。control rootはGit common-dirから導出してcaller値とexact照合し、archive先を`.pipeline/bootstrap-archives/<release-task>/attempts/<attempt-id>/<stage>/`へcontainする。`initial`以外のattempt IDは`secrets.token_hex(16)`で`retry-<failed-stage>-<32hex>`としてsingle-write発行し、既存branch/path/receiptと衝突したら再利用せず失敗する。`new-release-attempt`はcurrent R2〜R7 task/release、直前release task、absolute archive root、expected baseを必須入力とし、直前release premerge archive/merge record/operator gateのhashと`merged_main_sha=expected base`を検証して`retry-release-bootstrap-<32hex>`だけを発行する。current partial task artifactは一切読まない。
  - capture stageは`rf-00a-postcommit|rf-00n-postcommit|release-bootstrap`だけ。approved copy 5、lifecycle 4、task exactのevidence/gates/sessions/outcomes/approvals、branch/base/head/last-good/remote/environmentをregular-file allow-listでcopyし、sorted path/hash manifestをtemp sibling→atomic renameで一度だけ作る。symlink/FIFO/socket、secret-like filename、node_modules/venv/tmp、task外pathを拒否する。restoreは新branch/worktree不存在を確認し、manifest hash/ancestor/remote/environmentを照合してtask-owned fileだけをcopyする。既存同内容receiptは`verify-only`だけ成功、異内容/partial destinationは失敗。source/失敗worktreeを変更・削除しない。
  - RF-00A時点ではGitNexus/item runnerがないため、repo-local package importへ依存しない直接実行 `python3 -I -S scripts/test/test_refactor_bootstrap_receipt.py -v`をcommit前に使う。testはpath escape、symlink、ack喪失後verify-only、異内容衝突、CSPRNG retry token、rf-00a→rf-00n restore chainをtemporary Git fixtureで固定する。
  - 以下の`RF00A_INITIAL_TRANSACTION_BEGIN/END`間は、Markdownから`bash` fence本文だけを表示順に連結した1本を`bash -s`へ渡す。prose、JSON fence、完了条件fenceを混ぜない。RF-00Eのstatic testはmarkerがexact 1組、先頭statementが`set -Eeuo pipefail`、最終statementが`trap - ERR`、途中に別`set +e`未復帰0であることを検証する。
  1. 元workspaceで次の変数を設定し、`main`とHEADが調査SHAに一致することを確認する。異なる場合は実装を開始せず、baseline、GitNexus impact、line/symbol、research、plan external reviewを新HEADで再作成する。旧planの再照合だけで続行しない。

<!-- RF00A_INITIAL_TRANSACTION_BEGIN -->
```bash
set -Eeuo pipefail
RF00A_INITIAL_STAGE=preflight
rf00a_initial_failure() {
  local rc=$?
  trap - ERR
  printf 'RF-00A initial transaction failed at %s (exit=%s); preserve every path and stop for a new reviewed master task\n' \
    "$RF00A_INITIAL_STAGE" "$rc" >&2
  exit "$rc"
}
trap rf00a_initial_failure ERR
export MASTER_TASK=full-repo-refactoring-2026-07-24
export RELEASE_ID=r1
export TASK="${MASTER_TASK}-${RELEASE_ID}"
export RELEASE_BASE_SHA=b2bcae8e88f0e73fe95343ee3a694a3afc4e1028
export TARGET_ENVIRONMENT_ID="<認可済みoperatorが与えるstable environment ID>"
export DEPLOYMENT_FINGERPRINT_SHA256="<secretを含まないcluster/account/region/namespace fingerprintのSHA-256>"
export CONTROL_ROOT="$(git rev-parse --show-toplevel)"
test -n "$TARGET_ENVIRONMENT_ID"
case "$TARGET_ENVIRONMENT_ID" in
  *[!A-Za-z0-9._:/-]*|'') exit 2 ;;
esac
case "$DEPLOYMENT_FINGERPRINT_SHA256" in
  [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;;
  *) exit 2 ;;
esac
git fetch origin main
test "$(git rev-parse HEAD)" = "$RELEASE_BASE_SHA"
test "$(git rev-parse main)" = "$RELEASE_BASE_SHA"
test "$(git rev-parse origin/main)" = "$RELEASE_BASE_SHA"
test "$TASK" = full-repo-refactoring-2026-07-24-r1
```

  2. 元workspaceの `git status --short` を保存し、既存未追跡物を一覧化する。これらをstageしない。
  3. planning checkoutでmasterのstatic 5 fileと承認済みreview artifactをR1 taskへbyte-copyし、R1 task IDでcheckpoint/SMLを生成する。checkpointのquality condition/allowed/forbidden/commandは下のliteral commandから変えない。copy後にmaster/R1 static fileのSHA-256が全件一致し、local coverage verifierとcanonical pre-gateがR1 taskでもpassすることを確認する。

```bash
mkdir -p ".pipeline/plans/$TASK" \
  ".pipeline/evidence/$TASK/plan-review" ".pipeline/gates/$TASK"
for name in request.md plan.md verification-contract.md \
  planned-visual-changes.json release-boundaries.json; do
  cp -p ".pipeline/plans/$MASTER_TASK/$name" ".pipeline/plans/$TASK/$name"
  cmp -s ".pipeline/plans/$MASTER_TASK/$name" ".pipeline/plans/$TASK/$name"
done
for name in local-review-1.json local-review-2.json coverage-manifest.json; do
  cp -p ".pipeline/evidence/$MASTER_TASK/plan-review/$name" \
    ".pipeline/evidence/$TASK/plan-review/$name"
done
for pair in \
  "external-consultation/consultation-plan-summary.json" \
  "codex-review/review-plan-summary.json" \
  "dual-review/consensus-plan-summary.json"; do
  mkdir -p ".pipeline/evidence/$TASK/${pair%/*}"
  cp -p ".pipeline/evidence/$MASTER_TASK/$pair" \
    ".pipeline/evidence/$TASK/$pair"
done
cp -p ".pipeline/gates/$MASTER_TASK/pre-implementation-review.json" \
  ".pipeline/gates/$TASK/pre-implementation-review.json"
bash scripts/harness/backcast-checkpoint.sh "$TASK" \
  --goal "master plan $MASTER_TASK のR1を承認済み順序で実装する" \
  --current "release base $RELEASE_BASE_SHA" \
  --target "R1 release gate、独立QA、人間承認、PR readinessを同一HEADで満たす" \
  --type validation --approval-required --max-files 500 \
  --condition "QC-SEC::R1認証境界がfail closed::R1 security contract全件pass::release-tests" \
  --condition "QC-TEST::検査の実行事実と合否が一致::current prefix全item pass::release-tests" \
  --condition "QC-STRUCTURE::R1 scope/履歴がplan一致::release manifest pass::release-tests" \
  --condition "QC-E2E::baselineをimmutable固定::18 screenshotとmanifest hash固定::baseline-e2e" \
  --command "release-tests::bash scripts/test/run-refactor-release-gate.sh --verify-existing --master-task $MASTER_TASK --release $RELEASE_ID --task $TASK --base $RELEASE_BASE_SHA --head HEAD --attempt-id @SELECTED_CLOSURE_ATTEMPT@" \
  --command "baseline-e2e::bash scripts/test/verify-refactor-e2e-evidence.sh --mode baseline" \
  --allowed "services/**" --allowed "packages/**" --allowed "libs/**" \
  --allowed "deploy/**" --allowed "tests3/**" --allowed "scripts/**" \
  --allowed ".github/**" --allowed ".harness/**" --allowed "schemas/**" \
  --allowed "docs/**" --allowed "MANIFEST.md" --allowed ".pipeline/**" \
  --forbidden ".env*" --forbidden "**/*.pem" --forbidden "**/*.key" \
  --forbidden "**/node_modules/**" --forbidden "**/.venv/**" \
  --forbidden "**/migrations/**" --forbidden ".claude/**" --forbidden "features/**"
bash scripts/harness/sml-decision.sh "$TASK" --size L \
  --reason "R1も認証・複数service・Harnessを横断する" \
  --external-consultation required_for_l
# この直前に3節のinline local-review verifierを再実行し、passを確認する。
bash .claude/hooks/pre-implementation-review-gate.sh "$TASK"
```

  4. 次をそのまま実行する。既存task/branch/pathと衝突したら`--force`を使わず停止する。

```bash
bash scripts/harness/worktree.sh create "$TASK" \
  --base "$RELEASE_BASE_SHA" \
  --branch "codex/$TASK"
WORKTREE_PATH="$(
  python3 -I -S - "$CONTROL_ROOT" "$TASK" <<'PY'
import json, pathlib, sys
root, task = pathlib.Path(sys.argv[1]), sys.argv[2]
d = json.loads((root / ".pipeline/worktrees" / task / "worktree.json").read_text())
assert d["task_id"] == task and d["status"] in {"created", "bootstrapped"}
print(pathlib.Path(d["path"]).resolve(strict=True))
PY
)"
cd "$WORKTREE_PATH"
test "$(git rev-parse HEAD)" = "$RELEASE_BASE_SHA"
test "$(git branch --show-current)" = "codex/$TASK"
```

  5. 上のliteral blockで新worktreeへ移動済みであることを再確認する。worktree scriptはR1 taskの`plans/`と`evidence/`をcopyするため、`git status --short`はtask-owned artifactだけなら非空でよい。それ以外のpathが1件でもあれば中断する。生成済み4 lifecycle fileがcopyされ、checkpoint IDが一致することを次で確認する。

```bash
python3 -I -S - "$TASK" <<'PY'
import json, pathlib, sys
task = sys.argv[1]
p = pathlib.Path(".pipeline/plans") / task
c = json.loads((p / "checkpoint-contract.json").read_text())
s = json.loads((p / "sml-decision.json").read_text())
assert (p / "goal-contract.json").is_file() and (p / "current-state.md").is_file()
assert c["task_id"] == task == s["task_id"]
assert s["checkpoint_id"] == c["checkpoint_id"] and s["size"] == "L"
assert c["execution_bounds"]["require_human_approval_before_pr"] is True
assert c["execution_bounds"]["max_changed_files"] == 500
assert len(c["quality_conditions"]) == 4
assert {x["id"] for x in c["verification_commands"]} == {"release-tests", "baseline-e2e"}
PY
```

  5a. `worktree.sh`はmaster plan/evidenceと`gates/`をcopyしないため、次をそのまま実行する。source/destination hash不一致なら停止する。新worktree内でlocal coverage verifierとcanonical pre-gateを別々に再実行する。reviewed static 4 fileのexact SHA/coverage 100%とapproved copy 5 fileのbyte一致が同じくpassしなければRF-00Aをcommitしない。copy元pathはbaseline evidenceへ記録し、元fileを移動・削除しない。

```bash
test "$PWD" = "$WORKTREE_PATH"
mkdir -p ".pipeline/plans/$MASTER_TASK" \
  ".pipeline/evidence/$MASTER_TASK/plan-review" \
  ".pipeline/evidence/$MASTER_TASK/external-consultation" \
  ".pipeline/evidence/$MASTER_TASK/codex-review" \
  ".pipeline/evidence/$MASTER_TASK/dual-review"
for name in request.md plan.md verification-contract.md \
  planned-visual-changes.json release-boundaries.json \
  research-brief.md option-matrix.md kpi-backcast-roadmap.md; do
  cp -p "$CONTROL_ROOT/.pipeline/plans/$MASTER_TASK/$name" \
    ".pipeline/plans/$MASTER_TASK/$name"
done
for name in local-review-1.json local-review-2.json coverage-manifest.json; do
  cp -p "$CONTROL_ROOT/.pipeline/evidence/$MASTER_TASK/plan-review/$name" \
    ".pipeline/evidence/$MASTER_TASK/plan-review/$name"
done
for pair in \
  "external-consultation/consultation-plan-summary.json" \
  "codex-review/review-plan-summary.json" \
  "dual-review/consensus-plan-summary.json"; do
  cp -p "$CONTROL_ROOT/.pipeline/evidence/$MASTER_TASK/$pair" \
    ".pipeline/evidence/$MASTER_TASK/$pair"
done
mkdir -p ".pipeline/gates/$TASK"
mkdir -p ".pipeline/gates/$MASTER_TASK"
cp -p \
  "$CONTROL_ROOT/.pipeline/gates/$MASTER_TASK/pre-implementation-review.json" \
  ".pipeline/gates/$MASTER_TASK/pre-implementation-review.json"
cp -p \
  "$CONTROL_ROOT/.pipeline/gates/$TASK/pre-implementation-review.json" \
  ".pipeline/gates/$TASK/pre-implementation-review.json"
python3 -I -S - "$CONTROL_ROOT" "$PWD" "$TASK" <<'PY'
import hashlib, pathlib, sys
source = pathlib.Path(sys.argv[1])
destination = pathlib.Path(sys.argv[2])
task = sys.argv[3]
rel = pathlib.Path(".pipeline/gates") / task / "pre-implementation-review.json"
assert hashlib.sha256((source / rel).read_bytes()).digest() == \
       hashlib.sha256((destination / rel).read_bytes()).digest()
PY
# 3節に掲載した標準Python verifier blockをここで再実行し、
# stdout exact `local-plan-review-coverage: pass`を確認する。
bash .claude/hooks/pre-implementation-review-gate.sh "$TASK"
```

  6. `.pipeline/evidence/$TASK/baseline.json` にmaster task、release ID、base SHA、branch、worktree絶対path、`control_root_realpath="$CONTROL_ROOT"`、control checkoutのremote URL、元workspaceのstatus一覧、approved copy 5 fileのSHA-256、reviewed static 4 fileのcoverage manifest SHA、`target_environment_id="$TARGET_ENVIRONMENT_ID"`、`deployment_fingerprint_sha256="$DEPLOYMENT_FINGERPRINT_SHA256"`を書く。control rootは既存directoryのrealpathで、現在のplanning checkoutとexact一致しなければならない。環境2値は以後のrelease manifest、approval target、merge attestation、operation/cutover artifactとexact一致させ、途中変更しない。`checkpoint_sha`は自己参照になるためfileへ書かず、commit後のcommand outputとsession eventへ記録する。同じworktreeで対象欄のbootstrap receipt helper/testを実装し、`python3 -I -S scripts/test/test_refactor_bootstrap_receipt.py -v`を実行して全列挙testがnormal passすることを確認する。
  7. 次のresolverが出力するexact pathだけを明示stageする。canonical gateはpass判定専用であり、実在しない`reviewed_evidence` fieldを仮定しない。review inventoryはlocal coverage manifest 3 fileとcanonical Fable/Codex/dual summary 3 fileのliteral集合である。各summaryがreviewed static 4 hashと同じplan material hashへbindし、R1 copyがmasterと同一byteであることをcanonical pre-gateとinline verifierの両方で確認する。directory単位の`git add`、filesystem glob、目視だけのallow-list判定は禁止する。

```bash
RF00A_STAGE_LIST="$(mktemp)"
RF00A_CACHED_LIST="$(mktemp)"
python3 -I -S - "$MASTER_TASK" "$TASK" >"$RF00A_STAGE_LIST" <<'PY'
import hashlib, json, pathlib, sys
master, task = sys.argv[1:]
review_suffixes = (
    "plan-review/local-review-1.json",
    "plan-review/local-review-2.json",
    "plan-review/coverage-manifest.json",
    "external-consultation/consultation-plan-summary.json",
    "codex-review/review-plan-summary.json",
    "dual-review/consensus-plan-summary.json",
)
literal = [
    *(f".pipeline/plans/{master}/{name}" for name in (
        "request.md", "plan.md", "verification-contract.md",
        "planned-visual-changes.json", "release-boundaries.json",
        "research-brief.md", "option-matrix.md", "kpi-backcast-roadmap.md")),
    *(f".pipeline/plans/{task}/{name}" for name in (
        "goal-contract.json", "current-state.md", "checkpoint-contract.json",
        "sml-decision.json", "request.md", "plan.md",
        "verification-contract.md", "planned-visual-changes.json",
        "release-boundaries.json")),
    f".pipeline/gates/{master}/pre-implementation-review.json",
    f".pipeline/gates/{task}/pre-implementation-review.json",
    f".pipeline/evidence/{task}/baseline.json",
    *(f".pipeline/evidence/{master}/{suffix}" for suffix in review_suffixes),
    *(f".pipeline/evidence/{task}/{suffix}" for suffix in review_suffixes),
    "scripts/test/refactor-bootstrap-receipt.py",
    "scripts/test/test_refactor_bootstrap_receipt.py",
]
for suffix in review_suffixes:
    p = pathlib.Path(f".pipeline/evidence/{master}/{suffix}")
    q = pathlib.Path(f".pipeline/evidence/{task}/{suffix}")
    assert p.is_file() and q.is_file() and not p.is_symlink() and not q.is_symlink()
    assert hashlib.sha256(p.read_bytes()).digest() == hashlib.sha256(q.read_bytes()).digest()
paths = sorted(set(literal))
assert len(paths) == len(literal)
for raw in paths:
    p = pathlib.Path(raw)
    assert not p.is_absolute() and ".." not in p.parts
    assert p.is_file() and not p.is_symlink()
print("\n".join(paths))
PY
while IFS= read -r path; do
  git add -- "$path"
done <"$RF00A_STAGE_LIST"
git diff --cached --name-only | LC_ALL=C sort >"$RF00A_CACHED_LIST"
cmp -s "$RF00A_STAGE_LIST" "$RF00A_CACHED_LIST"
git diff --cached --check
```

  8. staged一覧がresolverのexact setであることをmachine checkし、`git commit -m "RF-00A record approved plan and baseline"` を作る。commit後のtreeから同resolverを再実行し、`git show --format= --name-only HEAD`のsorted unique pathと一致させる。
  9. commit直後に次を実行し、pre-review済みcheckpointとbuild runnerを結ぶ。これはsourceを変更せず、最初の`build-summary.json.head_sha`を後のRF-49Eがimmutable review baseとして維持するためのbaselineである。

```bash
bash scripts/harness/build.sh "$TASK" \
  --worktree "$PWD" --no-commit --no-verify --no-pack --no-state -- true
```

  10. build summary確認後、stateをlegal transitionで`building`まで進める。各実装項目中は`building`を維持し、`built`へ進めるのは最終節で全実装commit後だけとする。

```bash
bash scripts/harness/backcast-state.sh "$TASK" planned \
  --reason "approved plan recorded"
bash scripts/harness/backcast-state.sh "$TASK" build_authorized \
  --reason "pre-implementation review gate passed"
bash scripts/harness/backcast-state.sh "$TASK" worktree_created \
  --reason "isolated implementation worktree created"
bash scripts/harness/backcast-state.sh "$TASK" building \
  --reason "RF-00N bootstrap begins"
```

  11. `git rev-parse HEAD`とbuild summaryをsession eventへ記録する。実装treeの除外付きstatusは空、task artifact差分はtask allow-list内でなければならない。その記録後に次を実行し、RF-00A直後のtask stateをcontrol rootへsingle-write captureする。ack喪失時は同じcommandを再実行せず`verify-only`だけを使う。

```bash
BOOTSTRAP_ATTEMPT_ID=initial
RF00A_HEAD="$(git rev-parse HEAD)"
python3 -I -S scripts/test/refactor-bootstrap-receipt.py capture \
  --control-root "$CONTROL_ROOT" \
  --source "$PWD" \
  --task "$TASK" \
  --attempt-id "$BOOTSTRAP_ATTEMPT_ID" \
  --stage rf-00a-postcommit \
  --last-good-sha "$RF00A_HEAD"
python3 -I -S scripts/test/refactor-bootstrap-receipt.py verify-only \
  --control-root "$CONTROL_ROOT" \
  --task "$TASK" \
  --attempt-id "$BOOTSTRAP_ATTEMPT_ID" \
  --stage rf-00a-postcommit \
  --expected-head "$RF00A_HEAD"
trap - ERR
```
<!-- RF00A_INITIAL_TRANSACTION_END -->
- 完了条件:
  - 次のstandalone verifierをそのまま実行し、stdout exact `rf-00a-bootstrap-verifier: pass`、exit 0を確認する。assertion名は`rf_00a_bootstrap_verifier::{test_task_checkpoint_and_environment_binding,test_worktree_branch_base_and_build_summary,test_static_and_review_hashes_match_control_copy}`とする。

```bash
set -Eeuo pipefail
export MASTER_TASK=full-repo-refactoring-2026-07-24
export RELEASE_ID=r1
export TASK="${MASTER_TASK}-${RELEASE_ID}"
export RELEASE_BASE_SHA=b2bcae8e88f0e73fe95343ee3a694a3afc4e1028
test -n "${TARGET_ENVIRONMENT_ID:-}"
case "$TARGET_ENVIRONMENT_ID" in *[!A-Za-z0-9._:/-]*|'') exit 2 ;; esac
env -i VALUE="${DEPLOYMENT_FINGERPRINT_SHA256:-}" PATH=/usr/bin:/bin \
  python3 -I -S -c \
  'import os,re; assert re.fullmatch(r"[0-9a-f]{64}", os.environ["VALUE"])'
python3 -I -S - "$MASTER_TASK" "$TASK" "$RELEASE_BASE_SHA" \
  "$TARGET_ENVIRONMENT_ID" "$DEPLOYMENT_FINGERPRINT_SHA256" <<'PY'
import hashlib, json, pathlib, subprocess, sys
master, task, base, environment, fingerprint = sys.argv[1:]
plan_root = pathlib.Path(".pipeline/plans")
evidence_root = pathlib.Path(".pipeline/evidence")
release_plan = plan_root / task
checkpoint = json.loads((release_plan / "checkpoint-contract.json").read_text())
sml = json.loads((release_plan / "sml-decision.json").read_text())
baseline = json.loads((evidence_root / task / "baseline.json").read_text())
build = json.loads((evidence_root / task / "build/build-summary.json").read_text())
assert checkpoint["task_id"] == task == sml["task_id"] == baseline["task_id"]
assert checkpoint["checkpoint_id"] == sml["checkpoint_id"]
assert sml["size"] == "L"
assert baseline["release_id"] == "r1" and baseline["release_base_sha"] == base
assert baseline["target_environment_id"] == environment
assert baseline["deployment_fingerprint_sha256"] == fingerprint
head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
branch = subprocess.check_output(["git", "branch", "--show-current"], text=True).strip()
assert branch == f"codex/{task}"
assert subprocess.run(["git", "merge-base", "--is-ancestor", base, head]).returncode == 0
assert build["task_id"] == task and build["head_sha"] == head and build["exit_code"] == 0
for name in ("request.md", "plan.md", "verification-contract.md",
             "planned-visual-changes.json", "release-boundaries.json"):
    a = (plan_root / master / name).read_bytes()
    b = (release_plan / name).read_bytes()
    assert hashlib.sha256(a).digest() == hashlib.sha256(b).digest()
reviews = sorted((evidence_root / master / "plan-review").glob("*.json"))
release_reviews = sorted((evidence_root / task / "plan-review").glob("*.json"))
assert len(reviews) == len(release_reviews) >= 3
assert [hashlib.sha256(x.read_bytes()).hexdigest() for x in reviews] == \
       [hashlib.sha256(x.read_bytes()).hexdigest() for x in release_reviews]
print("rf-00a-bootstrap-verifier: pass")
PY
```

RF-00A capture後のstdout/process ack喪失だけは、上記verifierの前に次のstandalone recoveryを1回実行できる。receipt不存在、unexpected head、hash不一致、別branch/taskならterminal failureとして別master taskへ戻る。

```bash
set -Eeuo pipefail
export MASTER_TASK=full-repo-refactoring-2026-07-24
export RELEASE_ID=r1
export TASK="${MASTER_TASK}-${RELEASE_ID}"
export RELEASE_BASE_SHA=b2bcae8e88f0e73fe95343ee3a694a3afc4e1028
CONTROL_ROOT="$(
  git rev-parse --path-format=absolute --git-common-dir |
    python3 -I -S -c 'import pathlib,sys; print(pathlib.Path(sys.stdin.read().strip()).resolve().parent)'
)"
RF00A_HEAD="$(git rev-parse HEAD)"
test "$(git branch --show-current)" = "codex/$TASK"
git merge-base --is-ancestor "$RELEASE_BASE_SHA" "$RF00A_HEAD"
python3 -I -S scripts/test/refactor-bootstrap-receipt.py verify-only \
  --control-root "$CONTROL_ROOT" \
  --task "$TASK" \
  --attempt-id initial \
  --stage rf-00a-postcommit \
  --expected-head "$RF00A_HEAD"
```

  - 標準Pythonのread-only assertionで`goal-contract.json`, `current-state.md`, `checkpoint-contract.json`, `sml-decision.json`が存在し、R1 task/checkpoint ID一致、size L、approval required、`max_files=500`、4 condition、`release-tests/baseline-e2e` commandを確認してexit 0。
  - baseline `build-summary.json`がexit code 0、mode `command`、head SHA=RF-00A commitを持つ。
  - checkpoint stateが`building`で、state historyの末尾4遷移が`planned -> build_authorized -> worktree_created -> building`。
  - `scripts.test.test_refactor_bootstrap_receipt`の`test_path_escape_and_non_regular_files_are_rejected`,`test_capture_is_single_write_and_ack_loss_uses_verify_only`,`test_existing_different_or_partial_destination_is_rejected`,`test_retry_attempt_token_is_csprng_and_single_write`,`test_rf00a_rf00n_release_restore_chain_preserves_hashes`がnormal pass。
  - control rootの`attempts/initial/rf-00a-postcommit/receipt.json`がRF-00A HEAD、branch、task、approved copy、lifecycle/build/state/session全file hashへbindし、`verify-only`がexit 0。
  - 新worktreeの実装tree除外付きstatusが空。task artifactは許可path内だけ。
  - `git show --stat --oneline HEAD` がresolverのmaster static/review/gate、R1 task plan/evidence/gate、bootstrap receipt helper/testだけを含む。
  - 元workspaceの既存未追跡pathに変更・削除・stageがない。
- リスクと戻し方: worktree作成時のpath衝突、task artifact以外の混入、checkpoint引数のshell quoting。既存worktreeを削除・移動せず中断する。RF-00A initial transactionにactual failureが出た、または`rf-00a-postcommit` canonical receiptをstandalone verifyできない場合は、commit前後を問わず同master task内でstage解除、修正、再試行をしない。captureが完了済みでstdout/processだけ失われた場合は上記verify-onlyだけを許す。それ以外はbranch/worktree/runtime artifact/stdout/stderrを証拠として保持してblockedを報告し、元base SHAとreview対象4 fileから**別master task ID**のplan/static artifactを作り、external/independent 4-file reviewとpre-implementation gateを最初から通す。旧taskの未commit helper、review gate、approval、途中artifactを流用しない。
- 依存: なし
- コミット: `RF-00A record approved plan and baseline`

### RF-00N Tracked GitNexus runtimeを先行固定

- 対象:
  - 新規 `scripts/test/run-gitnexus-refactor.sh:1-末尾`
  - 新規 `scripts/test/gitnexus-runtime/package.json:1-末尾`
  - 新規 `scripts/test/gitnexus-runtime/package-lock.json:1-末尾`
  - 新規 `scripts/test/gitnexus-runtime/run.mjs:1-末尾`
  - 新規 `scripts/test/test_gitnexus_refactor_bootstrap.py:1-末尾`
- 問題: 全項目で編集前impactを必須にする一方、従来の`.gitnexus/run.cjs`はgitignoredでcloneへ含まれず、version・integrity・evidence pathも固定されない。RF-00Eで同時作成すると、RF-00E自身の既存symbol編集より前にwrapperが存在しないtime-travelになる。
- 変更:
  - `scripts/test/gitnexus-runtime/package.json`は`private=true`、Node 22/npm 10、dependency `gitnexus="1.6.9"`だけを持つ。`package-lock.json`はlockfile v3でtop packageとtransitive dependencyを固定し、GitNexus tarball integrityを`sha512-Rq5LXFygx7jjMp/YFsIAcnnzuKvvCsb4rxHFILnu05ZOqk7xNXTUSMRa968EOCbxcKFxnhKYaGXoabOUeGZX6A==`へ固定する。`npm install`、global/latest、RF-00N後のlock変更を禁止する。
  - shell wrapperはargumentを加工してshell評価しない。`run.mjs`へ固定argvで渡し、`run.mjs`が`node:child_process.execFile`で`node_modules/.bin/gitnexus`のrealpathを実行する。gitignored control helper、PATH上のglobal binary、`npx`、shell、`eval`へfallbackしない。upstream cwdは`--worktree` exactで、argv mappingは次以外を許さない。
    - pre analyze: `["analyze", worktree, "--skip-agents-md", "--skip-skills"]`
    - post/final analyze: `["analyze", worktree, "--force", "--skip-agents-md", "--skip-skills"]`
    - impact: `["impact", target, "--direction", direction]`。targetは位置引数であり`--target`をupstreamへ渡さない。
    - detect: `["detect-changes", "--scope", "compare", "--base-ref", baseRef]`。default `unstaged`を使わない。
  - wrapperの唯一のCLI、stage分離、evidence filename/path、stale/HEAD/target/base-ref検証は2.2節のliteral契約を実装する。`--worktree`はcurrent repo realpath、`--task`はcurrent release task、`--evidence-scope`は`item:<planに存在するexact RF ID>`または`release:<r1..r7>`だけ。全commandで`--attempt-id`を必須にし、initial branchはliteral `initial`、retry branchはhash検証済み`state-transfer.json.retry_token`、closure再実行はsingle-write closure run tokenだけを許す。task外・symlink escape・既存異内容artifact・invalid JSON・0 resultはfail closed。
  - evidence canonical pathはitemで`.pipeline/evidence/$TASK/gitnexus/items/<RF-ID>/attempts/<attempt-id>/<head>/<pre|post>/`、release/finalで`.pipeline/evidence/$TASK/gitnexus/releases/<rN>/attempts/<attempt-id>/<head>/final/`。command種別とtarget hashをfilenameに含め、各directoryの`manifest.json`がsorted report path/hash/argvを列挙する。同一pathは一度だけatomic createし、過去attemptを上書き/copy/削除しない。wrapperのinternal `verify-existing` modeはupstream executableを呼ばず、指定scope/stage/attempt/head/baseのmanifest/report/meta/lock hashを再計算するだけである。release manifestはcurrent branchのstate-transfer hashからcurrent attemptを一意選択し、全過去attemptのmanifest/hash chainもread-only検証する。
  - `analyze`後に`.gitnexus/meta.json.lastCommit == --head == git rev-parse HEAD`を検査する。post/finalは`--force`実行済みであること、tracked `AGENTS.md/CLAUDE.md/.claude/**`差分0、index tree SHA=`git write-tree`、worktree/cached status hashをreportへbindする。`impact`/`detect-changes`は同scope/stage/head/attemptの成功済みanalysis hashへbindする。detect reportはscope=`compare`、base ref/merge-base、changed tracked+intent-to-add file集合がcached path集合と一致し、`cached_paths ⊆ write_target_allowlist`、`required_changed_targets ⊆ cached_paths`を検証する。実差がある項目でresult 0を拒否する。stdout/stderr/argv、tool version、package/lock/meta hash、worktree/head/task/scope/stage/attemptをcanonical JSONへ保存する。
  - `test_gitnexus_refactor_bootstrap.py`は標準ライブラリだけを使い、通常のunittest modeではtemporary Git fixtureとfake child executableでparser/containment/immutable evidenceを検証する。fakeは上記4 upstream argvをbyte一致で記録し、post/finalの`--force`欠落、detectの`--scope compare`欠落、impactの誤った`--target`、新規staged fileをreportから落とす実装を失敗させる。`--verify-committed-artifacts` modeはR1ではcommit直後report、R2〜R7ではR1 immutable premerge archive内report+archive manifestをread-only参照し、network/analysisを再実行せず、tracked 5 file、package/lock/integrity、RF-00N commit subject、reportのsource/head/attempt/hashを照合してstdout exact `gitnexus-bootstrap-committed: pass`を出す。current releaseへRF-00N reportをcopy/再生成しない。実packageの`--version`とcurrent repo `analyze`は別のcompletion commandで確認し、fakeだけで合格にしない。
  - RF-00Nは既存production symbolを編集しないbootstrap commitである。commit前はstandalone testだけを使い、exact 5 pathだけをcommitする。commit直後、そのtracked wrapperでRF-00N commitをRF-00A parentと比較する。initial attemptは`BOOTSTRAP_ATTEMPT_ID=initial`、RF-00N retryはRF-00A receiptから`new-attempt --failed-stage rf-00n`が発行しrestore receiptへ固定したtokenだけを使う。検証失敗時はcommitを打ち消さずRF-00A receiptの`last_good_sha`から新worktreeでRF-00Nを再実行する。
- 完了条件:
  - `node --version`が`v22.*`、`npm --version`が`10.*`。違えばexit 2で中断。
  - `npm ci --prefix scripts/test/gitnexus-runtime`がexit 0で、前後の`package-lock.json` SHA-256一致。
  - `python3 -m unittest -v scripts.test.test_gitnexus_refactor_bootstrap`がexit 0。test名は`test_lock_pins_gitnexus_1_6_9_and_exact_integrity`,`test_wrapper_rejects_unknown_duplicate_missing_ref_and_dash_prefixed_values`,`test_wrapper_uses_execfile_without_shell_and_contains_evidence`,`test_upstream_argv_maps_force_positional_impact_and_compare_scope_exactly`,`test_staged_new_file_is_present_in_compare_report`,`test_pre_post_final_attempt_paths_are_separate_and_immutable`,`test_retry_attempt_namespaces_do_not_overwrite_prior_evidence`,`test_stale_meta_wrong_worktree_head_and_unparseable_json_fail_closed`。
  - commit前 `git diff --cached --name-only`は上記5 pathだけ、subjectはliteralどおり。commit直後に次をそのまま実行する。

```bash
set -Eeuo pipefail
: "${BOOTSTRAP_ATTEMPT_ID:=initial}"
RF00N_HEAD="$(git rev-parse HEAD)"
RF00A_HEAD="$(git rev-parse HEAD^)"
test "$(git show -s --format=%s "$RF00A_HEAD")" = \
  "RF-00A record approved plan and baseline"
test "$(git show -s --format=%s "$RF00N_HEAD")" = \
  "RF-00N pin tracked GitNexus runtime and wrapper"
bash scripts/test/run-gitnexus-refactor.sh analyze \
  --worktree "$PWD" --head "$RF00N_HEAD" \
  --task "$TASK" --evidence-scope item:RF-00N \
  --analysis-stage post --attempt-id "$BOOTSTRAP_ATTEMPT_ID"
bash scripts/test/run-gitnexus-refactor.sh detect-changes \
  --worktree "$PWD" --head "$RF00N_HEAD" \
  --task "$TASK" --evidence-scope item:RF-00N \
  --analysis-stage post --attempt-id "$BOOTSTRAP_ATTEMPT_ID" \
  --base-ref "$RF00A_HEAD"
python3 -I -S scripts/test/refactor-bootstrap-receipt.py capture \
  --control-root "$CONTROL_ROOT" \
  --source "$PWD" \
  --task "$TASK" \
  --attempt-id "$BOOTSTRAP_ATTEMPT_ID" \
  --stage rf-00n-postcommit \
  --last-good-sha "$RF00N_HEAD"
python3 -I -S scripts/test/refactor-bootstrap-receipt.py verify-only \
  --control-root "$CONTROL_ROOT" \
  --task "$TASK" \
  --attempt-id "$BOOTSTRAP_ATTEMPT_ID" \
  --stage rf-00n-postcommit \
  --expected-head "$RF00N_HEAD"
```

  - wrapper reportのtool version=`1.6.9`、head/meta/current HEAD一致、detect対象が上記5 pathだけ、対象外process/HIGH/CRITICAL 0。control rootの選択attempt `rf-00n-postcommit/receipt.json`はこのreport全file hash、RF-00A parent、RF-00N HEAD、tracked runtime 5 fileへbindし`verify-only`がexit 0。
  - RF-00N時点ではmatrix/item runner未作成のため`run-refactor-item.sh`を要求しない。RF-00Eでmatrixへ`status=active,replay_policy=once`として登録し、以後はlock/wrapper/test/report hashのread-only再検証だけを行う。
- リスクと戻し方: npm registry取得不能、upstream tarball差替え、wrapper自身のfalse-pass。取得不能やintegrity差はversionを緩めず中断する。commit後検証失敗はbranch/evidenceを保持し、RF-00A SHAから新worktreeで同じRF-00Nを再実装する。global installやcontrol helperで代用しない。
- 依存: RF-00A
- コミット: `RF-00N pin tracked GitNexus runtime and wrapper`

### RF-00E 再現可能なtest environment bootstrap

- 対象:
  - 新規 `scripts/test/bootstrap-refactor-env.sh:1-末尾`
  - 新規 `scripts/test/bootstrap-refactor-release.sh:1-末尾`
  - 新規 `scripts/test/run-refactor-item.sh:1-末尾`
  - 新規 `scripts/test/run-refactor-item.py:1-末尾`
  - 新規 `scripts/test/run-required-suites.sh:1-末尾`
  - 新規 `scripts/test/run-refactor-phase-stage.sh:1-末尾`
  - 新規 `scripts/test/run-refactor-phase-gate.sh:1-末尾`
  - 新規 `scripts/test/run-refactor-release-gate.sh:1-末尾`
  - 新規 `scripts/test/run-refactor-closure-stage.sh:1-末尾`
  - 新規 `scripts/test/refactor-preapproval-bootstrap.py:1-末尾`
  - 新規 `scripts/test/refactor-preapproval-trusted-sources.json:1-末尾`
  - 新規 `scripts/test/run-full-refactor-verification.sh:1-末尾`
  - 新規 `scripts/test/verify-refactor-e2e-evidence.sh:1-末尾`
  - 新規 `scripts/test/verify-local-plan-review-coverage.py:1-末尾`
  - 新規 `scripts/test/verify-refactor-closure-artifacts.py:1-末尾`
  - 新規 `scripts/test/verify-refactor-commit-history.py:1-末尾`
  - 新規 `scripts/test/verify-refactor-release-manifests.py:1-末尾`
  - 新規 `scripts/test/freeze-refactor-approval-target.py:1-末尾`
  - 新規 `scripts/test/refactor-closure-attempt.py:1-末尾`
  - 新規 `scripts/test/write-refactor-outcome-card.py:1-末尾`
  - 新規 `scripts/test/finalize-refactor-blocked-review.py:1-末尾`
  - 新規 `scripts/test/verify-operator-cutover-gate.py:1-末尾`
  - 新規 `scripts/test/run-refactor-operator-gate.sh:1-末尾`
  - 新規 `scripts/test/run-refactor-cutover-gate.sh:1-末尾`
  - 新規 `scripts/test/run-refactor-postmerge-stage.sh:1-末尾`
  - 新規 `scripts/test/verify-refactor-merge-lease.py:1-末尾`
  - 新規 `scripts/test/run-refactor-build.sh:1-末尾`
  - 新規 `scripts/test/archive-refactor-release.py:1-末尾`
  - 新規 `scripts/test/write-refactor-merge-record.py:1-末尾`
  - 新規 `scripts/test/import-refactor-operation-artifact.py:1-末尾`
  - 新規 `scripts/test/install-refactor-local-packages.sh:1-末尾`
  - 新規 `scripts/test/transfer-refactor-retry-state.py:1-末尾`
  - 新規 `scripts/test/refactor-item-matrix.json:1-末尾`
  - 新規 `scripts/test/refactor-python-constraints.txt:1-末尾`
  - 新規 `tests3/unit/test_refactor_item_runner.py:1-末尾`
  - 新規 `tests3/unit/test_refactor_phase_gate.py:1-末尾`
  - 新規 `tests3/unit/test_refactor_closure.py:1-末尾`
  - `scripts/harness/backcast-manifest.sh:1-末尾`
  - `scripts/harness/backcast-approval.sh:1-末尾`
  - `scripts/harness/codex-session-ledger.sh:1-末尾`
  - runtime output: `.pipeline/tmp/<release-task-id>/env/**:1-末尾`
  - read-only input: `services/dashboard/package-lock.json:1-末尾`
  - read-only input: `packages/transcript-rendering/package-lock.json:1-末尾`
  - read-only input: `services/vexa-bot/package-lock.json:1-末尾`
- 問題: clean worktreeには`.venv`と`node_modules`がなく、既存計画のtest commandを実行できない。system Python 3.9はMeeting APIの`>=3.11`条件も満たさない。
- 変更:
  - bootstrap scriptは`PYTHON_BIN`指定を優先し、未指定なら`python3.12`、次に`python3.11`を探す。どちらもなければexit 2。Python 3.11/3.12以外、Node major 22以外、npm major 10以外はversionを表示してexit 2。
  - `bootstrap-refactor-env.sh --reuse-constraints`を追加する。R2〜R7では既存constraints、3 application lockfile、GitNexus runtime lockのR1 commit blob/hashを検証し、constraints生成/書換えをせず4 venvを同じ`-c`で再作成する。3 application directoryと`scripts/test/gitnexus-runtime`で`npm ci`を実行する。各release evidenceへversion/lock/constraints/pip freezeを保存し、R1とのthird-party version差1件でもexit 2。
  - `bootstrap-refactor-release.sh --master-task "$MASTER_TASK" --release r2..r7 --base "$RELEASE_BASE_SHA" --control-root "$CONTROL_ROOT" --attempt-id "$BOOTSTRAP_ATTEMPT_ID" --target-environment-id "$TARGET_ENVIRONMENT_ID" --deployment-fingerprint-sha256 "$DEPLOYMENT_FINGERPRINT_SHA256"`は`release-boundaries.json`からtaskを解決する。`BOOTSTRAP_ATTEMPT_ID=initial`ならbranch/pathは`codex/$TASK`と`$CONTROL_ROOT/.worktrees/$TASK`、`retry-release-bootstrap-<32hex>`なら`codex/$TASK-$BOOTSTRAP_ATTEMPT_ID`と`$CONTROL_ROOT/.worktrees/$TASK-$BOOTSTRAP_ATTEMPT_ID`へ機械導出し、caller指定path/branch flagを受けない。retry tokenは直前release archiveを検証した`refactor-bootstrap-receipt.py new-release-attempt`出力だけを許し、attempt receipt hashをbootstrap provenanceへ固定する。scriptは`set -Eeuo pipefail`とbootstrap failed receipt handlerを最初の副作用より前に設置し、`RELEASE_BASE_SHA`のdetached bootstrap checkoutからだけ実行して自身のsource commitがbaseと一致することを検査する。callerは直前にcontrol checkoutで`git fetch origin main`を実行し、baseが`origin/main`かつ前release archiveのmerge-record/approval/operation gateの`merged_main_sha`と一致しなければならない。環境2値はR1 baselineと前releaseのmanifest/approval/merge/operation artifactを全てexact照合し、欠落・形式不正・差異なら新worktree作成前にexit 2とする。local `main`を暗黙に更新・switchしない。masterの`request.md,plan.md,verification-contract.md,planned-visual-changes.json,release-boundaries.json`をdetached checkoutのrelease plan directoryへbyte-copyし、R1 plan review summary 3件とcoverage manifestをrelease evidenceへcopyしてhash/provenance manifestを作る。task固有checkpoint/SMLを生成し、canonical hookが計算する`plan_review_hash`とcopied summary `target_hash`一致を検査してpre-implementation gateを再実行する。その後、base commitに含まれる`worktree.sh`を`cd "$CONTROL_ROOT"`で呼び、metadataはcontrol checkout、checkoutはderived exact pathへ作る。detached checkoutからrelease approved copy/lifecycle/evidence/gatesを新worktreeへregular-file copyしてhash検証する。続いて`run-refactor-build.sh --mode baseline --task "$TASK" --release-base "$RELEASE_BASE_SHA" --worktree <new-worktree>`、state `planned -> build_authorized -> worktree_created -> building`、`bootstrap-refactor-env.sh --reuse-constraints`を順に行う。`--verify-export-only`は副作用を持たず、detached側のtask-owned dirty inventoryとfinal側copyのpath/hash完全一致を検査する。derived path以外、既存branch/worktree/path、base/source/environment mismatchはforceせずexit 2。`release-bootstrap` capture前に失敗したretryはpartial current task artifactをrestoreせず、別token/branch/pathで直前release archiveから全文を再実行する。
  - `RF_ENV_ROOT="$repo/.pipeline/tmp/$TASK/env"`配下に`backend`, `transcription`, `integrations`, `aux`の4 venvを作る。master taskへruntime envを作らない。
  - 最初にresolver用一時venvへ、backend/transcription/integrations/auxで使う全requirementsと`pytest pytest-asyncio httpx`をinstallし、`pip freeze --all`のpackage名をPEP 503正規化して重複version 0を確認する。local editable/path package、`pip/setuptools/wheel`を除く全third-partyを`name==version`でsortedした `scripts/test/refactor-python-constraints.txt` へ書く。
  - 4 venvのinstallは必ず `python -m pip install -c scripts/test/refactor-python-constraints.txt ...` とし、backendへ`libs/schema-sync`、`libs/admin-models`、`services/meeting-api`、`services/runtime-api[dev]`、`services/agent-api[all]`、API Gateway/Admin/Calendar requirements、transcriptionへTranscription requirements、integrationsへMCP/Telegram requirementsとeditable Vexa clients、auxへWake STT/Orchestrator/TTS/Voiceprint requirementsを入れる。
  - 4 venv完成後、各`pip freeze --all`のthird-party `name==version`がconstraintsと矛盾0であることを検査する。さらに空のprobe venvを1つ作り、全service requirementsを同じconstraintsでdry installして`pip check`を通す。constraints生成後にindex再解決してversionを変えず、RF-00E commit以後はconstraintsを変更しない。
  - `install-refactor-local-packages.sh`は任意引数を受けず、存在する場合だけ`libs/meeting-contracts`と`libs/meeting-models`をbackend/integrations venvへ`--no-deps -e`で冪等installし、各venvでimport identityを検査する。RF-10/RF-65はpackage作成後にこのscriptを最初に実行し、同じ項目内のpytest/Dockerへ進む。third-party constraints/lockfileは変更しない。
  - Nodeは次の順でlockfile厳守の`npm ci`を実行する。

```bash
set -Eeuo pipefail
(gitnexus_lock_before="$(
   python3 -I -S -c 'import hashlib,pathlib; print(hashlib.sha256(pathlib.Path("scripts/test/gitnexus-runtime/package-lock.json").read_bytes()).hexdigest())'
 )" && \
 npm ci --prefix scripts/test/gitnexus-runtime && \
 gitnexus_lock_after="$(
   python3 -I -S -c 'import hashlib,pathlib; print(hashlib.sha256(pathlib.Path("scripts/test/gitnexus-runtime/package-lock.json").read_bytes()).hexdigest())'
 )" && \
 test "$gitnexus_lock_before" = "$gitnexus_lock_after" && \
 test "$(scripts/test/gitnexus-runtime/node_modules/.bin/gitnexus --version)" = "1.6.9")
(cd packages/transcript-rendering && npm ci && npm run build)
(cd services/dashboard && npm ci)
(cd services/vexa-bot && npm ci)
```

  - 実行前後の4 lockfile SHA-256、Python/Node/npm/GitNexus version、各venvの`pip freeze --all`をtask evidenceへ保存する。全ての新規/retry worktreeでGitNexus lockのtop dependency/integrityも再検証する。`npm install`、lockfile rewrite、global installはしない。
  - RF-00Nでtracked固定済みのGitNexus 1.6.9 runtime/wrapper/lockを変更しない。RF-00E開始前に2.2節のliteral pre-analyze/impactをこのwrapperで実行し、control checkoutの`.gitnexus/run.cjs`（調査時hash `f6986b2c8e65fa53956d714cea251575420aa920b6dd2159d0b6d41ee7c8f718`）は比較資料にだけ使う。RF-00Eのrunnerはwrapper reportをmachine reportへ参照するだけで、global/latest、`npx`、gitignored helperへfallbackしない。
  - 2.3のJSON schemaどおり全RF IDを `refactor-item-matrix.json` へ登録し、`run-refactor-item.sh`はshell evaluationなしで`commands[]`を実行する。将来testは`status=planned`、過去commitのRF-00A/RF-00NとRF-00E自身だけ`active`にする。RF-00Aはinline verifier hash、RF-00Nは`replay_policy=once`でcommit時test/report/lock hashのread-only照合、RF-00Eは通常commandを登録する。通常の実装項目はcommit前に自身のcommandを`active`へしない限りrunnerがexit 2となる。
  - `run-required-suites.sh`はsuite名をhard-coded `case`で2.3のcommandへ対応させ、matrixが未知suiteを参照したらexit 2。cwdは全て`REPO_ROOT`からsubshellで切り替える。item/suite共通report writer/parserは2.3のmachine-readable report schemaを実装し、native reporter結果とrequired test名を照合する。missing Docker/DB/registry等をskip/成功へせずblocked exit 2にする。
  - `run-refactor-phase-stage.sh --phase <phase-1|...|phase-5> --expect-items <exact IDs>`をphase fenceの唯一のoperator入口にする。wrapperは`set -Eeuo pipefail`とERR handlerを最初の副作用前に設置し、current worktreeからtask/release/head/control root/environmentを再導出する。entry stateは`building|blocked`だけを許す。`blocked`では同phase/headのcanonical selection、failed receipt、`building -> blocked` event/current/checkpoint hashが全て一致する場合だけread-only verifyしてexit 3し、gate/transitionを再実行しない。`building`では`resume-or-new-phase` selectionを解決し、canonical phase pass JSONがあれば同selectionの`run-refactor-phase-gate.sh --verify-existing`だけを呼ぶ。不在ならselected attemptでgateを一度実行する。actual gate failureはappend-only failed receiptを検証後、合法な`building -> blocked`を一度だけ実行し、source/static/matrix/commitを変更せずexit 3。failed receipt後またはblocked event後のack喪失は同selectionの既済suffixをverifyする。
  - `run-refactor-phase-gate.sh <phase-1|...|phase-5> --expect-items <exact IDs> --attempt-id "$ATTEMPT_ID"`は、(1)列挙順のitem、(2)stable-unique suite、(3)phase assertionを一度だけ実行する。失敗時は`.pipeline/evidence/$TASK/phase-gates/attempts/<phase>/<attempt-id>/failed.json`をdestination不存在時だけ作りcanonical pass JSONを作らない。成功時は`.pipeline/evidence/$TASK/phase-gates/<phase>.json`をdestination不存在時だけatomic createし、`task_id,phase,head_sha,attempt_id,selection_sha256,item_ids,commands,assertions,status=passed`を持たせる。既存canonical destinationへは同内容でも書かず、ack喪失時は`--verify-existing`で全source/hashをread-only再計算する。異内容、unknown phase、順序差、0 command/assertionはexit 2。
  - `refactor-closure-attempt.py resume-or-new-closure`はtask/release/head/environmentでcontrol rootの`.pipeline/closure-selections/$TASK/<head>.json`を一意に解決する。selectionありならreceipt/hashをverify、なし＋matching closure attempt 0件ならCSPRNG `closure-<32hex>` receipt後にselectionをO_EXCL、attempt receipt作成後selection/stdout喪失でmatching exact 1件なら採用、複数/failed/inconsistentならblockedとする。`resume-or-new-phase`もtask/phase/head/environmentをkeyに`.pipeline/phase-selections/$TASK/<phase>/<head>.json`へ同じ0/1/複数規則で`phase-N-<32hex>`を固定する。mtime/latest、caller環境token、別token自動発行は禁止する。
  - stage allocationのoperator入口は必ず`resume-or-new-stage --parent-attempt <selected closure> --stage <literal enum> --generation <positive integer>`を使う。helperはtask/stage/head/parent/environment/generationで`.pipeline/closure-stage-selections/$TASK/<stage>/<head>/<parent>/generation-<N>.json`を一意に解決する。通常stageはgeneration=1固定。期限付き`premerge-lease|cutover-predeploy`だけ、selected attemptが`expired` completionで明示closeされ、前attestation/permitが期限切れまたはgeneration driftで未使用、canonical merge/deploy未実施であることを`expire-stage`がfresh remote/operator evidenceから検証した場合に限りgeneration=N+1を発行できる。実failure、validation failure、ack喪失ではrollover不可。selectionがあればselected attempt receipt/hashをverifyして再利用する。selectionがなくmatching attemptが0件ならCSPRNG `<stage>-<32hex>`のattempt receiptをO_EXCL作成後、同token/hashをselectionへO_EXCLで固定する。attempt作成後selection書込前のack喪失でmatching attemptがexact 1件ならそれをselectionへ採用する。matching attemptが複数、selection/receipt不一致、failed receipt済みなら推測・mtime/latest選択・新token発行をせずblocked。selection作成競合はwinnerをread-only verifyする。発行/再選択した値を`STAGE_ATTEMPT_ID`として同processへ渡し、内部algorithmは`new-stage`を再度呼ばない。`--stage-attempt`は`verify-stage`で既存selection/attemptのtask/release/head/parent/stage/environment/generationをread-only照合して同じIDを再利用する。全stage共通`complete-stage --output-sha256 <literal-name>=<hex>`はsorted unique output名/hash、selection hash、trusted cwd/source hashesを`completion.json`へO_EXCLで固定し、既存時は`verify-stage --expect-complete`へ同じordered `--output-sha256`を渡すだけを許す。`reject-input --field human-approved-target-sha256|human-approver --value-sha256 <hash>`はcaller入力不一致をstage directoryの`input-rejections/<field>-<value-sha256>.json`へO_EXCLで記録し、同一入力の再送は既存receiptをverifyして成功する。rejectionはattemptをfailed/completedにせず、state/target/decision/selectionを変更しないため、同じselectionへ正しい入力で再開できる。`checkpoint-stage --checkpoint prearchive-ready`はapproval target/decision、approved state event、PR-ready、stage/parent/headのhashをstage directoryの`prearchive-ready.json`へsingle-writeし、既存時は`verify-checkpoint --checkpoint prearchive-ready`だけを許す。approval stageの`complete-stage`はpremerge archive作成・自己hash検証後だけ許し、prearchive receipt hashと`archive_manifest_sha256`もcompletionへ固定する。completionはarchiveへ含めず、merge record/final aggregatorが独立にbindする。`run-refactor-phase-stage.sh`、`run-refactor-closure-stage.sh`、`run-refactor-postmerge-stage.sh`は同じselection algorithm/enumをimportし、独自定義しない。ack喪失時はselection tokenとcanonical artifactのverify-existingを呼ぶ。actual failureはstate-aware terminal failureであり、自動で新attemptを発行しない。
  - `run-refactor-closure-stage.sh`は各呼出しで`set -Eeuo pipefail`とstage固有ERR handlerを最初の副作用より前に設置し、`--master-task --release --release-worktree --stage`とoperator用`--resume-or-new`または内部resume用`--stage-attempt`を受ける。stageは`build-and-state,release-execution,release-finalize,manifest,tribunal,post-review,approval-target,approval`。`approval`だけは追加で`--human-approver`と`--human-approved-target-sha256`を必須にし、値を環境から暗黙取得しない。absolute worktree、task/base/head/control root/environment/parent closure attemptをbaseline/bootstrap、release boundaries、既存attempt receiptから再導出し、前shellの変数/cwd/trapを参照しない。`--resume-or-new`は上記canonical selectionを解決してから対応algorithmを実行するため、token stdout喪失後も同じselectionを再利用する。各stageは本文9.1〜9.7の対応algorithmだけを実行し、成功receiptをsingle-writeする。既存canonical outputは`--verify-existing`でsource/hash/state eventまで再検証し、writerや外部reviewを再実行しない。外部review待ちを跨いだ次stageは必ず新processで再水和する。`approval-target` stageのstate resume表は`verifying -> verified -> evidence_ready -> awaiting_approval`の未完suffixだけを実行し、既済prefixはtarget/hashとstate eventをread-only検証する。current stateが`verified|evidence_ready|awaiting_approval`なら先頭transitionを再実行せず、その他のstateはexit 2。approval target writerはdestination不存在かつstate=`verifying`のときだけcreateし、既存targetでは必ず`--verify-only`を使う。ack喪失でself-transition、evidence_missingへの誤遷移、同一target再生成を禁止する。`approval` stageはdecision/validator/approved/PR-ready/prearchive-ready/archive/final approval completionの順を変えず、wrapperが選択した1個のstage attemptだけを全suffixで使う。途中ack喪失では現在state、prearchive/archive/completionのhashをverifyして未完suffixだけを実行する。
  - `refactor-preapproval-bootstrap.py`は9.0のinline loaderだけから実行する。`refactor-preapproval-trusted-sources.json`は`run-refactor-closure-stage.sh`と、その8 stageが実行し得る全shell/Python helperのrepo-relative pathをsorted uniqueで列挙する。bootstrapはsystem Gitでrelease worktreeとcontrol checkoutのGit common-dir/origin/object formatを一致確認し、clean committed `HEAD`を確定してから、manifest自身と全列挙sourceをそのHEADのGit blobからrepo外temporary directoryへ`O_EXCL|O_NOFOLLOW`で全量抽出する。Git blob OID、`hash-object`、抽出SHA-256を一致させ、全shellへ`/bin/bash --noprofile --norc -n`、全Pythonへbuilt-in `compile(..., mode="exec")`を通した後だけ実行する。runner/helperは`RF_TRUSTED_TOOL_ROOT`配下のabsolute pathだけを呼び、release worktreeは検証対象data/cwdとしてのみ使う。PATH探索、worktree/archive内scriptの直接実行、`git show | bash`、stdin/FIFO実行、ambient `BASH_ENV,ENV,PYTHONPATH,GIT_*`は禁止する。bootstrapはroot-ownedかつgroup/world非writableな`/usr/bin/env,/usr/bin/python3,/usr/bin/git,/bin/bash`をowner/mode/hash（env以外はversionも）で検査し、clean `env -i`、`cwd=<release-worktree realpath>`でrunnerを起動する。system toolchain、control repository、trusted sourceのpath/blob/hash、exact cwdをbuild summary、各stage receipt、approval targetへ固定する。
  - closure wrapperのparentは、各processで最初に`resume-or-new-closure`をtask/release/head/environmentへ適用して得たexact 1件だけとする。内部algorithmは`new`/`new-stage`を呼ばず、bootstrapから渡された`CLOSURE_ATTEMPT_ID,STAGE_ATTEMPT_ID`を`verify-existing`/`verify-stage`して使う。共通failure classifierは、(a) canonical completionがありhash検証できるack喪失ならsuccessとしてverify-only終了、(b)認可/login/外部artifact待ちは`waiting_external`, exit 2でselection/state不変、(c)caller入力拒否はidempotent rejection receipt, exit 2、(d)実command/schema/verdict failureだけはstage failed receiptとsession failure eventを各exact 1件作り合法なstate transition後exit 3、(e)矛盾/複数候補はblockedとして別reviewed remediationを要求、の順で分類する。failed receiptを作ったstage/parentを同taskでresumeしたり、新attemptへ自動rolloverしてはいけない。
  - `build-and-state`は`build-summary.json`不存在かつstate=`building`のときだけ`run-refactor-build.sh --mode complete`を実行し、既存時は新設`--verify-existing`だけを呼ぶ。state suffixは`building`ならsummary検証後`built -> verifying`、`built`なら既存`building -> built` eventを検証後`verifying`、`verifying`なら両eventをverify-onlyし、その他はstage completionのverify以外exit 2とする。`manifest`は`evidence-manifest.json`と`evidence-pack.md`を別suffixとして、各destination不存在時だけcreate、既存時は新設`backcast-manifest.sh --verify-existing-output`または`backcast-evidence-pack.sh --verify-existing`だけを呼び、両hashをstage completionへ固定する。
  - `tribunal`と独立QAは`--prepare-external`でinput manifestをsingle-writeして`waiting_external`, exit 2、`--import-external`でrepo/control/archive外absolute regular fileを全量read・schema/hash/reviewer independence検証後にcanonicalへ`O_EXCL` importする二相方式とする。post Fable/Codex/dual reviewはpost-review input manifestを先にsingle-writeし、各summaryを独立suffixとして`exists -> provider validator only / absent -> provider run once`にする。login/authorization/provider session不足はfailed receiptを作らずwaiting、通信断後は既存summaryをverifyして未作成providerだけを呼ぶ。独立QA reviewer IDはimplementer、finder、adversarial、judge、synthesizer、post Codex reviewerの全IDと異なることを検証する。
  - `approval-target`の後半suffixは全てcreate-or-verifyにする。`feedback-prune.json`は不存在時だけhookを実行し、既存時は`verify-refactor-closure-artifacts.py --stage feedback-prune`でsource/hashをread-only検証する。session success eventは`sha256(task,head,parent-attempt,stage-attempt,"preapproval-success")`をidempotency keyとする`codex-session-ledger.sh record-once`でexact 1件をappendし、既存時は`verify-event`。RF-49Bはこの2 modeとidempotency fieldをgolden互換対象へ含める。outcome cardは不存在時だけwriter、既存時は`--verify-existing`、outcome judgeはread-only。approval targetはstate=`verifying`かつ不存在時だけcreateし、既存時はverify-onlyする。target作成後はsession/feedback/outcome/review/sourceを一切書かない。各suffix直後のack喪失では既存hash/eventを検証し、failure/session eventを追加しない。
  - `approval`は最初にcanonical final completionの有無を調べ、存在する場合はapproval target/decision/prearchive/archive/completionをverify-onlyしてexit 0する。このcompleted-ack recoveryでは既にmerge済みになり得るため`origin/main=release_base`を再要求しない。completion不存在時だけcaller hash/approver、target、decisionを検証し、fresh baseはdecision新規作成直前とprearchive新規作成直前に要求する。caller hash/approver不一致はreject-inputでselection/state/decision不変。実failureはfailed receiptでterminalにし、「fail-stage後に同attempt再開」は禁止する。PR-readyは不存在時だけcanonical hookを実行し、既存時は`pr-ready-gate.sh "$TASK" --verify-existing`で全source/hashを再計算する。
  - `run-refactor-release-gate.sh`は`--execute|--verify-execution|--finalize|--verify-existing`の排他的modeを必須とする。`--execute --attempt-id`だけがR1先頭からcurrent release末尾までのactive prefix全`replayable` itemとstable-unique suite/release assertionを一度ずつ実行し、`.pipeline/evidence/$TASK/release-gates/$RELEASE_ID/attempts/<attempt-id>/execution.json`へsingle-writeする。この段階でrelease manifestを作らない。RF-00A/RF-00N/RF-00Cは再実行せずimmutable sourceを検証する。R1のRF-00A/RF-00Nはcontrol rootのselected `rf-00a-postcommit`/`rf-00n-postcommit` receiptとmanifestを検証してrelease-local `bootstrap-receipts/`へdestination不存在copyし、R2〜R7はR1 premerge archive内の同copyを検証する。各R2〜R7はcurrent `release-bootstrap` receiptも同様にimportする。`--verify-execution`は既存execution/report/receipt importをread-only再検証する。`--finalize --attempt-id`だけが成功済みexecution、同attemptのfinal GitNexus、R7だけfull-verification、commit/static/scope/receipt chainをread-only検証し、canonical `release-manifest.json`をdestination不存在時だけ作る。`--verify-existing`はcanonical manifestと全source/hash/argvを再計算するだけでitem/suite/GitNexus/fullを呼ばない。finalize前失敗はcanonical manifest 0、ack喪失は各verify mode、異内容既存destinationはexit 2。
  - `run-full-refactor-verification.sh --attempt-id "$CLOSURE_ATTEMPT_ID"`はR7のrelease-gate execution後かつfinal GitNexus後に一度だけ実行し、同attempt既存machine reportをverify-only集約して`release-gates/r7/attempts/<attempt-id>/full-verification.json`をsingle-writeする。base/headはcaller環境へ依存せず`.pipeline/evidence/$TASK/build/build-summary.json`の`release_base_sha`と`implementation_head_sha`から読み、legacy `head_sha/base_sha=review_base_sha`をimplementation baseへ誤用しない。task ID、base ancestor、non-empty implementation diff、current HEAD=headを検査する。全matrix itemがactive、全`replayable` item/suite report、5 canonical phase gate JSON、V-DASH-FINAL/V-CLIENTS/V-OPS/V-HARNESS-CONTRACT、commit history、同attempt final GitNexusがpassであることを再parseする。phase/item/suite/GitNexusを呼ばない。既存destinationは`--verify-existing`だけを許す。
  - `verify-refactor-commit-history.py --task "$TASK" --base "$RELEASE_BASE_SHA" --head "$RELEASE_HEAD_SHA" --release "$RELEASE_ID"`は、release-boundariesから展開した当releaseの`### RF-*`順と各項目の`- コミット:` literalをparseし、release branchのfirst-parent commit件数・順序・subjectとexact一致させる。merge/余分/欠落/重複/Revertを拒否する。各commitのmatrix before/afterを比較し、RF-00E初期作成、RF-00A/RF-00N commit時のmatrix不存在を除き、当該IDだけが`planned -> active`になり、他ID entryがbyte不変であることを検査する。Appendix A.6 resolver commitは所有元test fileを変更できるが所有元matrix entryは不変でなければならない。
  - `verify-refactor-release-manifests.py --master-task "$MASTER_TASK" --archive-root "$CONTROL_ROOT/.pipeline/release-archives" --through rN`は、current worktreeの相対evidenceをfallbackにせずimmutable premerge archiveとpostmerge artifactだけを読む。release/task ID、archive manifest hash、base→merged main連鎖、release head/merge tree一致、item集合/順序/subject、static hash、全releaseのtribunal/post/QA/outcome/approval、operator expected commit/component source SHA、closure/postmerge selection/attempt/completion/lifecycle parent hashを検査する。`--through r1..r6`は指定済みreleaseまで、R7 merge後の`--through r7 --require-final-ui --output <canonical path>`だけ全matrix active、全phase/final evidenceを追加要求する。R7 aggregateは`aggregate-body.json`→completed event/completion→`final-aggregate.json`の二相でdestination不存在の場合だけatomic createする。outputはarchive root内R7 `postmerge/final-aggregate.json` exact以外を拒否する。
  - `verify-refactor-closure-artifacts.py`は最終節に固定したphase gate/tribunal/QA/outcomeのJSON schema、task ID、3 reviewerの独立ID、review対象evidence path実在を検査する。`--stage tribunal`はschemaと独立性を検証し、`verdict=pass|block`のどちらもvalidation自体はexit 0にする。`--stage post-review`はFable/Codex/dualのschema/hashを検証し、open MUST_FIX、MUST_FIX/ESCALATE、tribunal blocking/closure actionがあればexit 1、schema/path不備はexit 2。`--stage pre-approval`はtribunal/post/QAのblocking finding 0を要求する。phase gateは各phase末尾commitのSHAにbindし、そのSHAが`FINAL_HEAD`のancestorであることを検査する。tribunal/QA/review/outcomeだけは`head_sha=FINAL_HEAD`を必須とする。approval前はapproval fileを要求せず、`--stage after-approval`時だけapproval/manifest/currentのhash一致を要求する。`--stage blocked-review`はblocking finding 1件以上、outcome `status=blocked`、同一head、next actionを必須にしてexit 0とし、pass扱いにはしない。未知stageはexit 2。
  - `freeze-refactor-approval-target.py --task "$TASK" --head "$FINAL_HEAD"`はpre-approval検証、feedback prune、session ledger、outcome judge後、checkpointがまだ`verifying`の間に一度だけ実行する。`.pipeline/evidence/$TASK/approval-target.json`へ`schema_version,task_id,release_id,checkpoint_id,target_environment_id,deployment_fingerprint_sha256,base_sha,head_sha,closure_attempt_id,closure_attempt_receipt_sha256,static_sha256,evidence_manifest_sha256,evidence_pack_sha256,release_manifest_sha256,bootstrap_receipt_sha256,tribunal_sha256,post_review_sha256,qa_sha256,outcome_sha256,feedback_prune_sha256,session_ledger_sha256,phase_gate_sha256,final_ui_sha256|null,control_repository,system_toolchain,postmerge_trusted_sources,created_at`をatomic createし、既存pathを上書きしない。`control_repository`は`repository_id,origin_remote_name="origin",origin_url_literal,origin_url_canonical,control_root_realpath,git_common_dir_realpath,git_object_format`を持つ。canonical URLはuserinfo/query/fragmentを拒否し、scheme/hostをlowercase、path末尾`/`と`.git`を除く。`repository_id=sha256(origin_url_canonical+"\0"+git_object_format)`とする。`system_toolchain`は`env,python,git,bash`のexact 4 keyで、各entryがabsolute fixed path（順に`/usr/bin/env,/usr/bin/python3,/usr/bin/git,/bin/bash`）、`uid=0,mode,sha256,version`を持つ。envだけ`version=null`、他3件はexact version文字列。`postmerge_trusted_sources`はrunnerと全transitive tracked helperのsorted unique `repo_path,blob_sha,sha256,mode`を持ち、modeは`100644|100755`だけを許す。system pathがhostにない場合はapproval targetを別pathで作らずblockedにする。`post_review_sha256`はFable/Codex/dual 3 summaryのsorted path/hash aggregate、`phase_gate_sha256`は当releaseで存在するphase gateのsorted aggregate。各hashは実fileとhead/task/environment一致を検査してから計算する。`--verify-only --expect-state-prefix verifying|verified|evidence_ready|awaiting_approval`はtarget/source hashに加え、指定stateまでのexact state event prefixとcheckpoint/current-state一致をread-only検証する。作成・verifyがpassした後だけ、wrapperは未完の`verifying -> verified -> evidence_ready -> awaiting_approval` suffixを実行し、ack喪失後は既済prefixをverifyしてskipする。人間へ提示後は列挙sourceとtargetを1 byteも書換えない。
  - `finalize-refactor-blocked-review.py --task "$TASK" --head "$FINAL_HEAD" --source tribunal|post-review|qa`は、該当sourceのblocking finding実在とhead一致を検査し、`.pipeline/outcomes/$TASK/outcome-card.json`を`status=blocked,next_action="create a separately reviewed remediation task"`でatomic writeする。その後`verify-refactor-closure-artifacts.py --stage blocked-review`、`backcast-state verifying -> verification_failed -> blocked`をこの順で実行し、最終checkpoint state=`blocked`を再読する。source/plan/matrixを変更せずexit 3で終了する。合法遷移、findingなし、wrong head、既存pass outcome上書きをunit testで拒否する。
  - `write-refactor-outcome-card.py --task "$TASK" --head "$FINAL_HEAD" --attempt-id "$CLOSURE_ATTEMPT_ID"`は手入力booleanを受けず、release manifest、evidence manifest/pack、tribunal/post review/QA、feedback prune、session ledger、R7だけfinal UIを再parseし、全passかつ同一task/head/attemptの場合だけ本文9.6のpass outcome schemaをdestination不存在時にatomic createする。path/hash/booleanを証拠から導出し、missing/blocked/unknown/0 reportはexit 1/2でcardを作らない。既存pathは`--verify-existing`だけを許し、`outcome-judge.sh`はこのwriter成功後にread-only判定する。
  - `run-refactor-build.sh --mode baseline|complete --task "$TASK" --release-base "$RELEASE_BASE_SHA" --worktree "$PWD"`はcanonical `scripts/harness/build.sh`を呼ぶ薄いwrapperである。baseline modeは現在HEADをimmutable `review_base_sha`として別manifestにも保存する。complete modeは既存baseline hashを先に検証してからcanonical buildを実行し、`build-summary.json`をatomic rewriteしてlegacy `head_sha/base_sha=review_base_sha`、`release_base_sha=<branch作成base>`、`implementation_head_sha=<現在HEAD>`を持たせる。review base/release baseは以後不変、implementation headだけ更新可。ancestor/non-empty diff、task/branch一致を必須にする。branchは初回`codex/$TASK`、R2〜R7 bootstrap provenanceの`new-release-attempt` receipt/hashが一致する`codex/$TASK-retry-release-bootstrap-<32hex>`、または`.pipeline/evidence/$TASK/retries/<current-retry>/state-transfer.json`のhash/task/last-good/current branchが一致する`codex/${TASK}-retry-*`だけを許す。RF-00A retry branchは許可しない。release manifestへbranchと該当attempt/transfer hashを転記する。canonical buildが一時的にsummaryを上書きしてからwrapper検証が終わるまで外部reviewを呼ばない。R1はRF-00A summaryをbaselineとしてimportし、R2〜R7 bootstrapはbaseline modeを使う。
  - `archive-refactor-release.py --source "$REPO_ROOT" --control-root "$CONTROL_ROOT" --task "$TASK" --head "$FINAL_HEAD" --approval-stage-attempt "$STAGE_ATTEMPT_ID" --prearchive-ready "$PREARCHIVE_RECEIPT"`は9.7のapproval decision、validator、approved state、PR-ready、同stage attemptのprearchive-ready receiptが全てpassした後にだけ実行する。final approval completionはarchive後に作るため入力/memberへ要求しない。task-owned allow-listのregular file、task/checkpoint/head、source realpath、approval targetのcontrol repository identityを検査し、`release-archives/$TASK/premerge`不存在時だけtemp sibling→sorted path/hash manifest→atomic renameする。repo-relative memberは全て`premerge/files/<repo-relative-path>`へ保存する。control-root external prearchive receiptだけはexact `premerge/control/approval-stage/prearchive-ready.json`へcopyし、manifest top-levelへ`approval_stage_attempt_id,parent_closure_attempt_id,approval_target_sha256,prearchive_ready_path,prearchive_ready_sha256`を固定する。他の別pathを許さない。manifest memberは元repo-relativeまたはcontrol receipt source pathとarchive-relative pathを両方持つ。manifestは自己hashをmemberへ含めず、copied regular file全件だけを列挙する。manifest自身のSHA-256は後続final approval completion、merge-record、final aggregatorが保持する。archiveはapproval decision、approved state/event、PR-ready gate、prearchive-ready receiptを必須memberとして列挙する。さらにapproval target `postmerge_trusted_sources`とexact同集合のrunner/transitive tracked helperをrelease HEADのGit objectから`trusted-launchers/<repo-path>`へ全量抽出し、`trusted_launchers[repo_path]={repo_path,blob_sha,archive_path,sha256,mode,archive_mode}`を固定する。`mode`はGit mode、`archive_mode`は抽出regular fileの4桁POSIX permissionで、bootstrapが両方を再検証する。existing worktree fileをcopy sourceにしない。既存destination、symlink/FIFO/socket、secret-like filename、absolute/`..` member、task外pathはexit 2。sourceを削除・変更しない。`--verify-existing`はcopy/renameせず全member/source/blob/hash/modeとmanifest自己hashを再計算する。
  - `verify-refactor-merge-lease.py`はPR作成後・merge直前に、認可済みGitHub app/merge queueが別経路で作った`MERGE_LEASE_ATTESTATION`を検証する。schemaは`repository,pr_url,pr_number,base_sha,head_sha,head_tree_sha,required_checks,branch_protection,merge_queue_entry_id,lease_id,issued_at,expires_at,max_seconds=300,target_environment_id,deployment_fingerprint_sha256,attested_by_trusted_provider=true`。fresh remote API/refで`base_sha=origin/main=release_base_sha`、PR head/tree=release head/tree、required checks success、up-to-date/merge queue enforcement、未期限切れを確認し、`postmerge/merge-permits/<attempt-id>.json`へsingle-write permitを出す。実mergeはこのpermitを条件に同じqueue entryで行い、base/head/treeが変化したらGitHub側でatomic rejectする。事後検知だけに依存しない。
  - `write-refactor-merge-record.py --control-root "$CONTROL_ROOT" --task "$TASK" --release "$RELEASE_ID" --merge-attestation "$MERGE_ATTESTATION"`はpremerge archive、approval、merge permit、control-root canonical final approval completionと、認可済みGitHub/operator経路から既に存在するimmutable attestation JSONを読む。attestationは`repository,pr_url,pr_number,base_sha,head_sha,merged_commit_sha,merge_method,actor_stable_id,branch_protection,required_checks,merged_at,merge_permit_sha256,target_environment_id,deployment_fingerprint_sha256`を持ち、remote refs/tree、release head、permit、approval target環境、required checks successとexact照合する。final completionの`archive_manifest_sha256`を現在manifest hashへ照合し、`approval_stage_attempt_id,completion_sha256,parent_closure_attempt_id,prearchive_ready_sha256,archive_manifest_sha256`をmerge recordへ固定する。completionはarchive内にあると仮定せず、stage attempt pathとprearchive receiptからID/parent/headを再導出する。caller自己申告のmerge method/actor/timestampを受けない。全一致時だけattestation canonical bytesを`postmerge/merge-attestation.json`へdestination不存在copyし、そのhashを含む`postmerge/merge-record.json`をsingle-writeする。2 fileのpartial writeは失敗receiptとして保持し、`--verify-existing`/resumeが同一hashだけを完了する。`import-refactor-operation-artifact.py`も既存human artifactだけをcanonical attempt pathへcopyし、`--verify-existing`を持つ。
  - `run-refactor-postmerge-stage.sh`はcaller worktree/archive member/stdinから直接実行しない。9.8.0 trusted bootstrapが別経路のapproval target SHAをroot anchorにsystem toolchain/control repository/final approval completionを検証し、target/manifest/archive/Gitの4 hashを照合したrepo外temporary regular fileだけを実行する。runnerは各呼出しで`set -Eeuo pipefail`とstage固有ERR handlerを最初の副作用より前に設置する。`--master-task --release --archive-root --control-root --source-head --approval-target-sha256 --stage --generation --trusted-cwd`、`--resume-or-new|--expire-stage|--verify-existing`、stage固有の`--merge-lease-attestation|--merge-attestation|--cutover-attestation|--cutover-completion-attestation|--operator-attestation|--expiry-attestation`、外部入力がある場合の`--input-attestation-sha256`、aggregateの7 ordered `--release-approval-target-sha256`だけを受ける。`--verify-existing --expect-expired`はexpire completionだけを検証する。generic `--attestation`、複数/別stage flag、前shellのcwd/PYTHON_BIN/CONTROL_ROOT/TASKは拒否する。process cwdと`--trusted-cwd`は検証済みcontrol root realpathへexact一致し、selection/attempt/completionへbindする。外部attestationはbootstrapが全量secure-copyした`RF_TRUSTED_TOOL_ROOT/external-input/<sha>.json`だけを受け、pathではなく入力SHA-256をselection keyへ固定する。archive/boundaries/merge recordから全値を再導出し、release worktree欠落時もcanonical postmerge lifecycle storeから同selectionを再開する。`--resume-or-new`はstage key selectionを作成または再利用し、attempt receipt作成後stdout喪失でもexact 1件だけを回収する。canonical outputが既にあればwriterを呼ばず対応verify-existingだけを行う。bootstrapはrun exit 0後に必ず`--verify-existing`を別processで呼び、selection/completion/output hash/state suffixを再検証してからtemporary rootを削除する。期限stageの`--expire-stage`だけは未使用・期限切れを証明してgeneration rolloverを許す。merge-record成功時は`approved -> merged`、operator/aggregate成功時は`merged -> completed`を一度だけ進める。postmerge actual failureはappend-only failed receiptを残しterminal停止する。
  - `run-refactor-operator-gate.sh --master-task "$MASTER_TASK" --archive-root "$CONTROL_ROOT/.pipeline/release-archives" --release rN --gate <gate>`はrelease-boundariesからrelease task、operator expected item/subjectを解決し、premerge archive内のimmutable `release-manifest.json`とcanonical `postmerge/merge-record.json`、`postmerge/operator-gates/<gate-id>.json`を読む。checkoutの現在HEADを使わず、`verify-operator-cutover-gate.py --task <release-task> --gate <gate> --expected-subject <subject> --head <release_head_sha> --merge-record <exact path> --artifact <exact path>`を呼ぶ。
  - `verify-operator-cutover-gate.py`はexpected subjectのitem commit SHAがmanifestと一致し、release headのancestorであり、operation artifactの`expected_commit=<item commit SHA>,component_source_sha=<merged_main_sha>,release_manifest_sha256,merge_record_sha256,target_environment_id,deployment_fingerprint_sha256`がapproval/merge attestationとexact一致することをread-only検査する。共通schemaは`schema_version="operation-gate-v1",task_id,gate_id,target_environment_id,deployment_fingerprint_sha256,expected_commit,component_source_sha,release_manifest_sha256,merge_record_sha256,component_versions,freeze_lease_id,freeze_started_at,freeze_expires_at,admission_generation,deployment_generation,observation_started_at,observation_ended_at,measurements,operator_id,attested_by_human=true,attestation_method="interactive-operator-confirmation",status="pass",contains_secret_values=false`。各gate固有measurement名/閾値は当該OP節とexact一致、時刻はUTC ISO-8601、duration下限、freeze leaseが観測開始前から終了後まで連続して有効でgeneration不変を検査する。このschemaは人間性を暗号学的には証明しないため、認可済みhuman operatorから別経路で提供されたことをhuman process checkとして必須にする。AI実行者はartifactを自作しない。欠落時はexit 2で停止する。
  - R3〜R7のdecommission/removal codeを実環境へ配備する直前は、前releaseの保存済みOP gateだけを認可に使わない。認可済みoperatorが同じmeasurementを再観測し、current releaseの`merged_main_sha,target_environment_id,deployment_fingerprint_sha256`へbindした`gate_id="<source-gate>-cutover"` attestationを作る。`run-refactor-cutover-gate.sh --stage predeploy`はcurrent merge record、source OP artifact、cutover artifactを比較し、`attested_at`から15分以内、freeze leaseがsource観測開始前からcurrent deploy終了予定まで連続、admission/deployment generation不変、旧path count 0/new path success、source/current environment exact一致の場合だけdeploy permitを一度発行する。配備後の`--stage postdeploy`は別経路のcompletion attestationからpermit hash、開始/終了時刻、exact component versions、lease/generation不変を検証してsingle-write completionを作る。predeployだけを完了証拠にしない。R3=`op-05d-drain-cutover`、R4=`op-06c-drain-cutover`、R5=`op-06d-drain-cutover`、R6=`op-06f-drain-cutover`、R7=`op-09-drain-cutover`。期限切れ、別environment、freeze gap、generation変化では再観測し、古いartifactのtimestamp書換えを禁止する。
  - `backcast-manifest.sh`は本taskのcheckpoint `verification_commands`を任意shellとして実行しない。RF-00Eでtask/release別fixed registryの`id,cwd,argv[]`を定義し、closureの`release-tests`は必ず`run-refactor-release-gate.sh --verify-existing ... --attempt-id <selected>`だけに解決する。9.3の`--verify-existing-commands` modeは9.2のcanonical release manifest/reportのargv/head/hash/statusをread-only importし、`--execute`、item/suite/full/GitNexus/E2Eを呼ばない。checkpointの既存command文字列はregistry表示とbyte一致する場合だけ受理し、実行はrepo-contained cwd、`env -i` allow-list、`subprocess.run(argv,shell=False)`だけを使う。duplicate/unknown、metachar/newline/substitution、cwd escape、registry差はcommand 0でexit 2。悪性checkpointと二重実行spyをtestする。
  - `backcast-approval.sh --immutable-target`はRF-00Eで実装する。target fileと全列挙sourceのhash、task/head/environmentを再計算し、人間が渡したhashとexact一致した場合だけ`.pipeline/approvals/$TASK/approval-decision.json`をdestination不存在時にatomic createする。immutable modeは`evidence-manifest.json`、evidence pack、approval target、列挙source、checkpoint stateを変更せず、state transitionを呼ばず、失敗を`|| true`で握り潰さない。decisionは`reviewed_evidence.target_path,target_sha256,manifest_sha256,pack_sha256,closure_attempt_id`を持つ。既存decisionには`--verify-existing`だけを許し、approver/role/target/hashの1 byte差でexit 1。RF-49Cはこの既存modeをcommon I/Oへ移すだけで、R1〜R6で未実装のfuture optionを呼ばない。
  - `transfer-refactor-retry-state.py`は`--preflight-source`、archive-and-copy、`--verify-only`の3 modeを持つ標準ライブラリだけのPython 3.9+ scriptとする。preflightはdestination/branchを作る前に、derived control rootとartifact control root、Git common-dir、origin URL、task/checkpoint/static hash、last-good commit/ancestor、source regular-file inventoryをread-only検査する。checkpoint stateは`building`だけを許し、phase/release/closure failureではexit 2。archive-and-copyは最初にsource task artifact全件をcontrol rootのappend-only `.pipeline/retry-source-archives/$TASK/$RETRY_TASK/`へhash inventory付きで保全し、その後だけdestinationへcutoff allow-listをcopyする。approved copy 5 fileとlifecycle 4 fileに加え、baseline/bootstrap provenance、および`head_sha`が`LAST_GOOD_SHA`以前かつ`FAILED_ID`より前のcompleted item/phase evidenceだけを選ぶ。sessionは検証済みpre-failure prefixをretry専用`source-prefix.jsonl`へ固定し、新しい`events.jsonl`から別hash chainを開始する。failed ID以降、release/full/evidence pack/review/tribunal/final UI/QA/outcome/approval/closure/archive/merge/operator/cutover/postmerge/failed receiptは0-copy。分類不能またはhead-boundでないderived artifactはexit 2。symlink/FIFO/socket、`.env*`,`*.pem`,`*.key`,`node_modules`,`.venv`,`.pipeline/tmp`を拒否し、source/destination/control-root realpath containment、task/checkpoint ID、last-good SHAの存在/ancestorを検証する。`cutoff-manifest.json`は本文2.2のexact schemaでselected/excluded file、source archive hash、approved/lifecycle/session hashをsingle-writeし、verify-onlyでbyte一致、ancestor、0-copy categoryを再確認する。preflight/archive-and-copyはLAST_GOOD_SHAからhash照合してrepo外tempへ抽出したscriptを`env -i`かつ`python -I -S`で実行し、failed worktreeのvenv/PYTHONPATH/sitecustomizeを使わない。
  - matrix parserはJSON duplicate keyを`object_pairs_hook`で拒否し、cwd/pathをrepo配下へcontainし、`argv`をAppendix A.3/A.5のliteral allow-listへbyte一致させる。Dockerはlocal preflight成功後だけ実行する。`shell=True`、`eval`、文字列commandは禁止。
- 完了条件:
  - `bash scripts/test/bootstrap-refactor-env.sh` がexit 0。
  - 4 venvで `python -m pytest --version` がexit 0。
  - Dashboard/transcript/coreで `npm --version` と必要binary解決が成功。
  - `V-CLIENTS`がexit 0。
  - 4 lockfile hashが実行前後で一致し、GitNexus CLI versionはexact `1.6.9`。
  - 直後に列挙する3 test fileのexact nodeidは`run-refactor-item.sh RF-00E`のmatrix `pytest` commandだけが実行し、全件pass、全見出しID=matrix ID、duplicate/unknown/empty command fixture rejectをmachine reportで確認する。本文から別のraw pytestを実行しない。
  - `tests3/unit/test_refactor_item_runner.py::{test_all_plan_ids_have_exact_matrix_entries,test_every_target_token_normalizes_to_disjoint_write_read_runtime_scopes,test_completion_fallback_and_resolver_owner_paths_join_write_union,test_new_production_target_requires_existing_impact_anchor,test_test_runners_require_literal_nonempty_nodeids,test_machine_reports_bind_argv_counts_required_names_and_head,test_missing_infrastructure_is_blocked_never_skipped_or_passed,test_direct_completion_commands_are_registered,test_matrix_activation_changes_only_current_item,test_rf00a_rf00n_rf00c_verify_only_contracts_are_not_replayed,test_rf00n_python_argv_is_absolute_isolated_and_only_exact_literal_is_allowed,test_known_xfail_is_derived_from_resolver_state_without_mutating_owner_entry,test_docker_commands_require_default_local_context_and_unset_docker_host,test_retry_state_transfer_archives_full_source_but_copies_only_pre_failure_cutoff,test_retry_state_transfer_rejects_postfailure_derived_evidence_and_starts_new_session_chain,test_gitnexus_runtime_maps_force_positional_impact_compare_scope_and_attempt_namespace}`
  - `tests3/unit/test_refactor_phase_gate.py::{test_each_phase_has_exact_nonempty_ordered_item_set,test_phase_gate_runs_each_item_then_unique_suites_then_assertions,test_phase_gate_rejects_inactive_or_failed_item,test_phase_gate_writes_head_bound_atomic_evidence,test_phase_attempt_stdout_loss_recovers_exact_one_selection,test_phase_failure_uses_legal_building_to_blocked_transition_and_blocked_resume_is_verify_only}`
  - `tests3/unit/test_refactor_phase_gate.py::{test_release_boundaries_expand_to_every_plan_item_exactly_once,test_release_gate_allows_only_past_active_current_active_future_planned,test_release_gate_executes_every_replayable_prefix_item_and_unique_suite_once,test_release_gate_verifies_rf00a_rf00n_rf00c_from_immutable_sources,test_r7_release_and_full_verification_never_rerun_or_overwrite_phase_item_or_suite_artifacts,test_release_gate_checks_current_first_parent_subjects_and_unique_suites,test_release_manifest_base_head_and_tree_chain_is_atomic,test_final_aggregator_requires_all_seven_merges_and_six_operator_gates}`
  - `tests3/unit/test_refactor_closure.py::{test_commit_history_must_match_every_plan_subject_in_first_parent_order,test_commit_history_rejects_merge_extra_duplicate_and_revert,test_operator_gate_requires_exact_attestation_fields_commit_window_and_measurements,test_op_06c_requires_zero_meeting_legacy_before_revoke,test_operator_gate_rejects_secret_bearing_or_structurally_invalid_artifact,test_blocked_review_uses_verification_failed_then_blocked,test_tribunal_block_is_schema_valid_but_never_passes_preapproval}`
  - `tests3/unit/test_refactor_closure.py::{test_release_gate_does_not_require_future_e2e_qa_approval_merge_or_aggregator,test_build_wrapper_preserves_review_and_release_base_while_updating_implementation_head,test_build_and_manifest_accept_only_canonical_or_hash_bound_retry_branch,test_premerge_archive_is_single_write_regular_file_only_and_hash_complete,test_next_release_reads_only_control_archive_not_previous_worktree,test_next_release_bootstrap_runs_from_exact_origin_main_detached_checkout,test_merge_record_requires_immutable_remote_attestation_and_tree_environment_binding,test_operation_import_requires_existing_human_artifact_and_canonical_path,test_manifest_chain_verification_runs_after_current_operator_gate,test_cutover_gate_requires_fresh_current_sha_environment_and_continuous_freeze}`
  - `tests3/unit/test_refactor_closure.py::{test_local_plan_review_verifier_requires_two_full_byte_ranges_four_hashes_and_distinct_reviewers,test_local_plan_review_verifier_rejects_duplicate_json_keys_paths_ids_ranges_and_unknown_schema_fields,test_local_plan_review_verifier_rejects_boolean_as_integer_in_ranges_and_sizes,test_local_plan_review_verifier_checks_four_top_level_hashes_and_exact_ordered_coverage,test_approval_target_freezes_feedback_ledger_phase_and_post_review_hashes_and_rejects_postapproval_rewrite,test_backcast_manifest_rejects_shell_string_metacharacters_and_executes_fixed_argv_only,test_immutable_approval_mode_exists_from_rf_00e_for_every_release}`
  - `tests3/unit/test_refactor_closure.py::{test_closure_parent_stdout_loss_recovers_exact_one_selection,test_closure_stage_wrapper_rehydrates_every_stage_and_installs_failure_trap_before_first_side_effect,test_stage_attempt_allocation_ack_loss_recovers_exact_one_and_never_uses_latest,test_stage_attempt_selection_rejects_multiple_unselected_or_failed_attempts,test_every_closure_stage_partial_success_resumes_by_verifying_completed_suffix,test_approval_target_resume_verifies_completed_state_prefix_without_self_transition,test_wrong_supplied_approval_hash_or_approver_records_idempotent_rejection_without_expiring_or_failing_attempt,test_source_drift_expires_only_awaiting_approval_and_never_mutates_approved,test_merge_lease_rejects_base_head_tree_drift_before_merge_atomically,test_timed_stage_rollover_requires_expired_unused_generation_and_rejects_actual_failure,test_preapproval_launcher_rejects_dirty_worktree_script_bash_env_and_ambient_git_python,test_postmerge_bootstrap_requires_independently_supplied_target_sha,test_postmerge_bootstrap_rejects_ambient_or_repo_owned_tools,test_postmerge_bootstrap_extracts_full_regular_file_before_execution_and_rejects_stream_pipe,test_postmerge_bootstrap_checks_target_manifest_archive_blob_completion_and_control_repo_hashes,test_postmerge_stage_rejects_wrong_or_generic_attestation_flag,test_postmerge_stage_resumes_from_archive_when_release_worktree_is_absent,test_closure_and_postmerge_stage_enums_match_attempt_helper_exactly,test_merge_record_binds_approval_stage_attempt_completion_and_parent_closure,test_approved_merged_completed_ack_loss_is_idempotent_and_hash_verified}`
  - `tests3/unit/test_refactor_closure.py::{test_rf00a_actual_failure_blocks_and_only_completed_capture_ack_loss_can_verify_resume,test_release_bootstrap_retry_derives_new_branch_and_path_from_attempt_id,test_release_bootstrap_retry_never_restores_partial_precapture_state,test_item_retry_requires_building_state_and_cutoff_manifest,test_gate_ack_loss_uses_verify_existing_without_reexecution,test_real_phase_release_or_closure_failure_blocks_and_requires_new_reviewed_task,test_tribunal_and_qa_external_bundle_imports_are_hash_bound_single_write_and_reviewer_independent,test_external_review_pending_is_waiting_not_failed_selection,test_postrelease_lifecycle_store_restores_exact_final_head_and_hash_chain,test_final_aggregate_requires_seven_ordered_full_release_chains,test_final_aggregate_rejects_missing_duplicate_reordered_or_failed_receipts,test_final_aggregate_requires_all_release_review_approval_and_lifecycle_artifacts,test_final_aggregate_two_phase_completion_has_no_hash_cycle}`
  - constraints生成直後とprobe install後でthird-party version差0、4 venv全て`pip check`成功。
  - `bash scripts/test/run-refactor-item.sh RF-00E` と `bash scripts/test/run-required-suites.sh RF-00E` がexit 0。unknown IDとplanned-only IDはexit 2。
  - 除外付きstatusはbootstrap script以外clean。venv/node_modulesはGit対象外。
- リスクと戻し方: dependency download失敗、重いTorch/Whisper package、宣言rangeによる解決差。network/install権限を得られなければ中断し、testをskipしない。script commitは共通retry protocol、ephemeral envは削除せず再利用または手動cleanup対象として報告する。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-00N
- コミット: `RF-00E add reproducible refactor test bootstrap and item runner`

R2〜R7の開始は、直前releaseのPR mergeと該当OP gateがpassしたplanning checkoutで次のliteral手順を使う。`RELEASE_ID`だけを`r2`から順に1つ設定し、飛び越しや並列開始をしない。scriptはrelease/task/baseをstdoutで返すだけでなく`.pipeline/evidence/$TASK/bootstrap.json`へatomic writeする。そこからworktree pathを読み、移動後にbase/task/branch/stateを再照合する。

```bash
set -Eeuo pipefail
export MASTER_TASK=full-repo-refactoring-2026-07-24
export RELEASE_ID=r2  # r3,r4,r5,r6,r7も直前release完了後に同じ手順
export TASK="${MASTER_TASK}-${RELEASE_ID}"
export BOOTSTRAP_ATTEMPT_ID=initial
export TARGET_ENVIRONMENT_ID="<R1 baselineと同一のstable environment ID>"
export DEPLOYMENT_FINGERPRINT_SHA256="<R1 baselineと同一のlowercase SHA-256>"
export CONTROL_ROOT="$(git rev-parse --show-toplevel)"
BOOTSTRAP_PARENT=""
CALLER_TRUSTED_PYTHON="$(
  env -i PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin \
    sh -c 'command -v python3.12 || command -v python3.11 || command -v python3'
)"
case "$CALLER_TRUSTED_PYTHON" in "$CONTROL_ROOT"/*) exit 2 ;; esac
release_bootstrap_caller_failure() {
  local rc=$?
  trap - ERR
  set +e
  env -i PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin \
    "$CALLER_TRUSTED_PYTHON" -I -S - \
      "$CONTROL_ROOT" "$TASK" "$RELEASE_ID" "$BOOTSTRAP_ATTEMPT_ID" \
      "${RELEASE_BASE_SHA:-unresolved}" "$rc" <<'PY'
import datetime, json, os, pathlib, sys
control, task, release, attempt, base, rc = sys.argv[1:]
root = pathlib.Path(control).resolve(strict=True) / ".pipeline" / "bootstrap-attempts" / task / attempt
root.mkdir(parents=True, exist_ok=True)
path = root / "release-bootstrap-caller-failed.json"
payload = {
    "schema_version": "release-bootstrap-caller-failure-v1",
    "task_id": task,
    "release_id": release,
    "attempt_id": attempt,
    "release_base_sha": base,
    "exit_code": int(rc),
    "status": "failed",
    "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
}
if not path.exists():
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, sort_keys=True, separators=(",", ":"))
        f.write("\n")
PY
  printf 'release bootstrap caller failed (exit=%s); preserve detached/final paths and retry with a new release-bootstrap attempt\n' \
    "$rc" >&2
  exit "$rc"
}
trap release_bootstrap_caller_failure ERR
test -n "$TARGET_ENVIRONMENT_ID"
case "$TARGET_ENVIRONMENT_ID" in
  *[!A-Za-z0-9._:/-]*|'') exit 2 ;;
esac
case "$DEPLOYMENT_FINGERPRINT_SHA256" in
  [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;;
  *) exit 2 ;;
esac
git fetch origin main
export RELEASE_BASE_SHA="$(git rev-parse origin/main)"
BOOTSTRAP_PARENT="$(mktemp -d)"
BOOTSTRAP_CHECKOUT="$BOOTSTRAP_PARENT/checkout"
EXPECTED_WORKTREE_PATH="$CONTROL_ROOT/.worktrees/$TASK"
test ! -e "$EXPECTED_WORKTREE_PATH"
git -C "$CONTROL_ROOT" worktree add --detach \
  "$BOOTSTRAP_CHECKOUT" "$RELEASE_BASE_SHA"
test "$(git -C "$BOOTSTRAP_CHECKOUT" rev-parse HEAD)" = "$RELEASE_BASE_SHA"
(
  cd "$BOOTSTRAP_CHECKOUT"
  bash scripts/test/bootstrap-refactor-release.sh \
    --master-task "$MASTER_TASK" \
    --release "$RELEASE_ID" \
    --base "$RELEASE_BASE_SHA" \
    --control-root "$CONTROL_ROOT" \
    --attempt-id "$BOOTSTRAP_ATTEMPT_ID" \
    --target-environment-id "$TARGET_ENVIRONMENT_ID" \
    --deployment-fingerprint-sha256 "$DEPLOYMENT_FINGERPRINT_SHA256"
)
WORKTREE_PATH="$(
  python3 -I -S - "$BOOTSTRAP_CHECKOUT" "$TASK" <<'PY'
import json, pathlib, sys
bootstrap, task = sys.argv[1:]
d = json.loads((pathlib.Path(bootstrap) / ".pipeline/evidence" / task / "bootstrap.json").read_text())
assert d["task_id"] == task and d["status"] == "building"
print(d["worktree_path"])
PY
)"
test "$WORKTREE_PATH" = "$EXPECTED_WORKTREE_PATH"
cd "$WORKTREE_PATH"
test "$(git rev-parse HEAD)" = "$RELEASE_BASE_SHA"
test "$(git branch --show-current)" = "codex/$TASK"
test -x ".pipeline/tmp/$TASK/env/backend/bin/python"
test -f ".pipeline/evidence/$TASK/bootstrap.json"
(
  cd "$BOOTSTRAP_CHECKOUT"
  bash scripts/test/bootstrap-refactor-release.sh \
    --verify-export-only \
    --master-task "$MASTER_TASK" \
    --release "$RELEASE_ID" \
    --base "$RELEASE_BASE_SHA" \
    --control-root "$CONTROL_ROOT" \
    --attempt-id "$BOOTSTRAP_ATTEMPT_ID" \
    --target-environment-id "$TARGET_ENVIRONMENT_ID" \
    --deployment-fingerprint-sha256 "$DEPLOYMENT_FINGERPRINT_SHA256"
)
python3 -I -S scripts/test/refactor-bootstrap-receipt.py capture \
  --control-root "$CONTROL_ROOT" \
  --source "$PWD" \
  --task "$TASK" \
  --attempt-id "$BOOTSTRAP_ATTEMPT_ID" \
  --stage release-bootstrap \
  --last-good-sha "$RELEASE_BASE_SHA"
python3 -I -S scripts/test/refactor-bootstrap-receipt.py verify-only \
  --control-root "$CONTROL_ROOT" \
  --task "$TASK" \
  --attempt-id "$BOOTSTRAP_ATTEMPT_ID" \
  --stage release-bootstrap \
  --expected-head "$RELEASE_BASE_SHA"
case "$BOOTSTRAP_CHECKOUT" in
  "$BOOTSTRAP_PARENT"/checkout) ;;
  *) exit 2 ;;
esac
git -C "$CONTROL_ROOT" worktree remove --force "$BOOTSTRAP_CHECKOUT"
rmdir "$BOOTSTRAP_PARENT"
trap - ERR
```

期待結果は、直前releaseのimmutable premerge archive/approval/merge-record/operation artifact hash chainがpass、`RELEASE_BASE_SHA=origin/main=detached bootstrap HEAD=前release.merged_main_sha`、static 5 fileとplan-review 3件がmasterとbyte一致、checkpoint ID一意、state=`building`、constraints/4 lock/versionがR1と一致すること。`--verify-export-only`はdetached checkoutのdirty pathが当該taskのplans/evidence/gatesだけ、全regular fileがfinal worktreeへ同じrelative path/hashでcopy済み、bootstrap manifestがsource/destination双方で一致する場合だけexit 0にする。control rootの`attempts/initial/release-bootstrap/receipt.json`はbootstrap/build/state/session/provenance全file hashとrelease base/environmentへbindし、release manifestがこのreceipt path/hash/attempt IDを選択する。`--force` removalは`mktemp -d`直下のこの検証済みdetached checkoutだけに限定し、失敗時は診断用に残す。1件でも満たさなければ新releaseを実装しない。

### RF-00B Backend契約の特性test

- 対象:
  - 新規 `services/meeting-api/tests/test_lifecycle_characterization.py:1-末尾`
  - 新規 `services/meeting-api/tests/test_request_bot_characterization.py:1-末尾`
  - 新規 `services/meeting-api/tests/test_final_transcription_characterization.py:1-末尾`
  - 新規 `services/transcription-service/tests/test_gemini_boundary_golden.py:1-末尾`
  - 新規 `services/runtime-api/tests/test_scheduler_characterization.py:1-末尾`
  - 新規 `services/api-gateway/tests/test_route_inventory_characterization.py:1-末尾`
  - 新規 `tests3/unit/refactor/test_rf_00b.py:1-末尾`
- 問題: 後続抽出が守るべきpayload、状態遷移、副作用順序が複数testへ分散し、一部は未固定。
- 変更:
  - `test_lifecycle_characterization.py::test_terminal_matrix_snapshot` に callback種別、exit code、completion reason、期待status/failure stageを表形式fixtureで固定する。ただしRF-11で直す `exit_code=0 + explicit failure reason` は `xfail(strict=True)` とし、脆弱な挙動を正解化しない。
  - `test_request_bot_characterization.py` にstandard/browser/agent-onlyのMeeting response、Runtime request JSON、Redis key、scheduler payloadをJSON goldenとして固定する。secret、UUID、timestampはplaceholderへ正規化する。
  - `test_final_transcription_characterization.py` に `lease -> source -> provider -> DB commit -> cache/publish -> voiceprint/Drive` のcall orderをspyで固定する。
  - `test_gemini_boundary_golden.py` にASCII、日本語、絵文字、speaker交代、overlapなし/完全overlap/部分overlapのsegment text/speaker/start/endを固定する。
  - `test_scheduler_characterization.py` に現行job JSON schema、terminal history schemaを固定する。RF-24で直す非原子性とretry不整合は正解化しない。
  - Gateway route inventoryをfixture化し、未登録policyは `xfail(strict=True)` にしてRF-05Aで解消する。
  - known xfailはAppendix A.6の3 exact parameterized nodeidだけにする。関数全体をxfailせず、他parameterはnormal passにする。
  - `tests3/unit/refactor/test_rf_00b.py::test_known_xfail_inventory_has_exact_three_owner_cases_and_resolvers`で、3 nodeid、owner、`resolved_by`、strict marker、通常parameter数をAppendix A.6とbyte一致させる。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-00B`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-00B`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/meeting-api/tests/test_lifecycle_characterization.py::test_terminal_matrix_snapshot`
  - `services/meeting-api/tests/test_request_bot_characterization.py::test_standard_browser_and_agent_runtime_specs_snapshot`
  - `services/meeting-api/tests/test_final_transcription_characterization.py::test_deferred_transcription_side_effect_order_snapshot`
  - `services/transcription-service/tests/test_gemini_boundary_golden.py::test_ascii_japanese_emoji_speaker_and_overlap_matrix_snapshot`
  - `services/runtime-api/tests/test_scheduler_characterization.py::test_job_and_terminal_history_schema_snapshot`
  - `services/api-gateway/tests/test_route_inventory_characterization.py::test_every_current_route_is_observed`
  - `tests3/unit/refactor/test_rf_00b.py::test_known_xfail_inventory_has_exact_three_owner_cases_and_resolvers`
  - 上記nodeidを個別実行し、Appendix A.6でRF-11/RF-05A/RF-24へbindした3 strict xfail以外が全てpass。
  - `V-MEETING`、`V-BACKEND`、`V-TRANSCRIPTION` がbaseline以上。
  - goldenにtoken、password、実UUID、現在時刻が含まれない。
- リスクと戻し方: 現状バグをgoldenへ固定する危険。上記xfail対象以外の差異が出たら実装へ進まず、fixtureの観測方法を報告する。戻す場合はこのtest-only commitを共通retry protocol。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-00E
- コミット: `RF-00B add backend characterization safety net`

### RF-00C Frontend/Core契約の特性testと視覚baseline

- 対象:
  - `services/dashboard/tests/**:1-末尾`
  - `packages/transcript-rendering/src/*.test.ts:1-末尾`
  - `services/vexa-bot/core/src/**/*.test.ts:1-末尾`
  - 新規 `services/vexa-bot/core/test-registry.json:1-末尾`
  - 新規 `services/vexa-bot/core/scripts/run-tests.mjs:1-末尾`
  - 新規 `services/dashboard/tests/refactor/rf_00c.test.ts:1-末尾`
  - 新規 `services/dashboard/tests/test_transcript_baseline.test.ts:1-末尾`
  - 新規 `services/dashboard/tests/fixtures/deferred-promise.ts:1-末尾`
  - 新規 `services/dashboard/scripts/check-lint-baseline.mjs:1-末尾`
  - 新規 `services/dashboard/tests/fixtures/lint-baseline.json:1-末尾`
  - `services/dashboard/src/app/meetings/page.tsx:1-末尾`
  - `services/dashboard/src/app/meetings/[id]/page.tsx:1-末尾`
  - `services/dashboard/src/components/layout/app-layout.tsx:1-末尾`
  - `services/dashboard/src/components/meetings/{meeting-card,browser-session-view}.tsx:1-末尾`
  - `services/dashboard/src/components/transcript/{transcript-viewer,transcript-segment}.tsx:1-末尾`
  - `services/dashboard/src/components/recording/{audio-player,video-player}.tsx:1-末尾`
  - 新規 `services/vexa-bot/core/src/refactor-tests/rf_00c.test.ts:1-末尾`
  - 新規 `services/dashboard/scripts/refactor-e2e.mjs:1-末尾`
  - 新規 `services/dashboard/scripts/run-refactor-e2e.sh:1-末尾`
  - 新規 `services/dashboard/scripts/generate-refactor-media-fixtures.mjs:1-末尾`
  - 新規 `services/dashboard/scripts/capture-refactor-baseline.sh:1-末尾`
  - 新規 `services/dashboard/tests/fixtures/refactor-e2e/**:1-末尾`。exact file集合は`planned-visual-changes.json`の`sorted(Object.values(fixture_contracts).map(x => x.path))`のunique値だけ
  - 新規 `packages/transcript-rendering/src/test-fixtures/panel20-sanitized.json:1-末尾`
  - `services/dashboard/next.config.ts:1-末尾`
  - RF-00Eで作成済みの `scripts/test/verify-refactor-e2e-evidence.sh:1-末尾`
  - read-only input: `.pipeline/plans/full-repo-refactoring-2026-07-24/planned-visual-changes.json:1-末尾`
  - read-only input: `.pipeline/plans/full-repo-refactoring-2026-07-24/release-boundaries.json:1-末尾`
  - 新規 `.pipeline/evidence/full-repo-refactoring-2026-07-24/screenshots/baseline/**:1-末尾`
  - 新規 `.pipeline/evidence/full-repo-refactoring-2026-07-24/baseline-ui.json:1-末尾`
  - 新規 `.pipeline/evidence/full-repo-refactoring-2026-07-24/baseline-integrity.json:1-末尾`
  - runtime output（R7 closureで生成）: `.pipeline/evidence/full-repo-refactoring-2026-07-24-r7/final-ui.json:1-末尾`
- 問題: DOM test基盤が薄く、巨大component内の正しい挙動と既知bugが区別されていない。調査時のlive imageは現HEADと一致しない。
- 変更:
  - pure fixtureとしてnumeric Meeting `1001/1002`、native ID `fixture-native-a/b`、single/multi-session、confirmed/pending、fragment追加/削除を追加する。detail APIは`/api/vexa/meetings/1001|1002`、transcriptは`/api/vexa/transcripts/google_meet/fixture-native-a|b`を使い、route IDとnative IDを混同しない。
  - `planned-visual-changes.json`の計画時SHA-256は`2e20cd69a5db1346d5dccca05b9e91132833d76b82e6ea4cfb281efe1fd59f80`、`release-boundaries.json`は`a01acb4ab693838d5f9109b6ea68a8118cb97453c1e7da7bc7b2eb185e43bbc5`へliteral固定する。RF-00Aでplanと一緒にcommitし、RF-00Eのlinter、RF-00C capture、最終verifierは各literalと実file hashの一致を確認する。どちらかのhashが変わった場合は外部/local plan reviewを最初からやり直す。
  - fixture materializationは`planned-visual-changes.json.fixture_materialization_contract`を唯一の生成規則とする。top-level `path,transport`等はtransport別metadata allow-listとしてbody projection前に除外し、残るbody authoring keyだけへprojection ruleを適用する。DTO既定値→fixture固有既定値→authoring projection→exact配列/recipeの順で、`exact_identity|meeting_identity -> root`、`data_exact -> data`、`segments_exact|segments_exact_order|segments_recipe -> segments`、`recordings_exact|recordings_exact_order -> recordings`をJSON記載順どおり適用する。JSONのstrict fixture DTO contractが宣言するexact root/nested keyとclosed type unionを機械検証し、projection先の衝突、unknown metadata/authoring/DTO key、duplicate key、type/required key差、booleanをintegerとして受理すること、非finite numberはfatal。`segments_recipe`はJSONのclosed operation DSLだけを使ってRF-00Cでconcrete tracked arrayへ展開し、自然言語式やruntime生成を禁止する。`http-json-template`だけはtracked authoring templateを不変のまま保持し、loopback port/basePath固定後かつNext.js起動・route install前にallow-list `originHttp|originWs|basePath`をJSON string value内でsingle pass置換して`.pipeline/tmp/<validated-task-id>/e2e/<canonical-attempt-id>/materialized-fixtures/`へO_EXCL作成する。未知/残存placeholder、非loopback origin、basePath grammar違反はfatal。receiptには非secretのexact `placeholder_values={basePath,originHttp,originWs}`を保持する。JSON記載のrecursive UTF-8 key sort＋空白/改行なしserializationでplaceholder map、materialized body、receiptのhash inputを固定し、tracked Git blobとreceiptだけからtemp削除後も3 hashを再構築できなければ失敗とする。tracked blob/worktree hash、materialized body、receipt hashをsnapshotへ固定し、capture前後でtracked template不変を検証する。`http-json*`は明記がなければstatus 200、content-type `application/json; charset=utf-8`。同じfixtureを2 pathで使う場合も、`scenario_id,mode,method,raw_path_query,fixture,status,content_type,tracked_authoring_sha256,materialized_body_sha256,materialization_receipt_sha256`のbinding tupleを別々にsnapshotし、fixture名だけの照合を禁止する。receipt hashは`http-json-template`だけ64hex、concrete `http-json|http-binary|fake-websocket`はJSON nullとし、後者はtracked/body hashを必須かつ同値にする。
  - Dashboardの新testは全てVitestの既存includeに合う `services/dashboard/tests/**/*.test.ts` へ置く。`src/**/__tests__`へ置かない。
  - Coreの全既存`src/**/*.test.ts`をinventoryし、`test-registry.json`へ `path`, `runner=tsx|dist-node`, `status=active|disabled`, 非activeなら`reason/review_after`を持たせる。未登録test fileとactive missing fileはfatal。`run-tests.mjs`へ`--file <repo-relative-test>`と`--report-json <path>`を追加し、登録済みactive 1 fileだけのtest名/件数/statusをJSON出力できるようにする。`npm test`はbuild後にactive全件を実行し、新規testもregistryへ追加しなければ合格しない。
  - Dashboardへ `scripts/check-lint-baseline.mjs` と `tests/fixtures/lint-baseline.json` を追加する。baselineはESLint JSONから `{relative_file, ruleId, message, severity}` のsorted multisetとerror/warning総数を保存する。checkerは新規signature、既存signature件数増加、総数増加のどれかでexit 1。
  - 視覚diff領域と操作locatorsを実装者が推測しないよう、RF-00Cで既存DOMへ非表示文言を変えないstable hookを付ける。既存Meeting list root=`meeting-list`、各card=`meeting-card-1001|meeting-card-1002`、detail root=`meeting-detail`に`data-meeting-id,data-native-id,data-status`、既存一覧へ戻るLink=`meeting-detail-back`、app outer scroll element=`app-main-scroll`、検索form wrapper/input/results=`transcript-search-panel|transcript-search-input|transcript-search-results`、segment list/scroll container=`transcript-segment-list|transcript-scroll-container`、既存status表示=`meeting-status`、既存Browser wrapper=`browser-session-panel`、既存audio wrapper/play button=`audio-player|audio-play`、既存video wrapper/play button=`video-player|video-play`を`data-testid`としてexact 1個ずつ付ける。segment rowへfixture `data-segment-id`と`data-absolute-start-time`、ready後のrootへ`data-ready="true"`を付ける。browser panelはraw API値を`data-raw-status`へ、現行表示分類を`data-state`へ別々に出し、RF-20が後者だけを修正する。DOM testでdesktop/mobileとも各selector 1件、属性値、hook追加前後pixel差0をbaseline撮影前に保証する。新しいvisible controlは追加しない。
  - `test_transcript_baseline.test.ts` に通常文字列検索、単一session順序、speaker filter、pending replacementの現行正しい挙動を固定する。特殊記号検索、A/B/A dedup、絶対timelineは後続bug修正testとし、現状を正解化しない。
  - `panel20.test.ts`がclean checkoutで5件skipする原因は、gitignore済み`features/realtime-transcription/data/**`への依存である。実dataを復元・copyせず、PIIを含まない43 segment/7 speakerのsynthetic fixtureを`packages/transcript-rendering/src/test-fixtures/panel20-sanitized.json`へtracked追加し、testはそのrelative pathを無条件readする。`existsSync`と`describe.skipIf`を削除し、5件をnormal passへする。fixtureは`text="fixture-segment-001"`形式、speaker=`speaker-1..7`、単調なstart/end、固定completedで、実名・会議ID・token 0。
  - store requestのgenerationを観測できるdeferred Promise fixtureを追加する。
  - `generate-refactor-media-fixtures.mjs`はDashboard lockfileで固定されたPlaywright/Chromiumだけを使い、fixture不存在時だけtemp siblingへ生成・検証してatomic renameする。既存fileを上書きしない。`silence-1s.wav`は8,000Hz/mono/16-bit PCM、RIFF headerと8,000個のzero sampleからNodeで生成する。`black-1s.webm`は16×16 black canvasの`captureStream(10)`をChromium `MediaRecorder`の`video/webm;codecs=vp8`で1,000ms収録する。生成後に同じlocked Chromiumの`<audio>/<video>`へloadし、`error=null`、`readyState>=1`、finite duration 0.5〜2.0秒、videoは16×16を確認する。codec unsupported、空blob、既存fixture不正はfallback生成せずexit 1とする。生成物はRF-00C source stagingより前に作成し、同commitへ含め、baseline後は再生成しない。
  - media detailの`data.recordings`は必ず空配列とし、transcript responseだけがS07ではrecording 701 audio、S08では701 audio→702 videoの順で返す。同じrecording arrayをdetailとtranscriptへ二重配置してeffectを再発火させない。master responseは各exact 1回、binaryはbody-bearing GETを各1回以上必須とする。`http-binary` fixtureは`planned-visual-changes.json.binary_range_contracts.media-range.state_table`をbyte単位で実装する。RangeなしGET/HEAD、`bytes=N-M|N-|-S`のGET/HEAD、end clamp、size超過suffix、malformed/multi/`-0`/`N>M`/`N>=L`を表どおり200/206/416へし、`Accept-Ranges`,`Content-Range`,`Content-Length`とbody slice/body 0をexact照合する。同一scenario/pathはHEADを含め最大4 request、HEADはbody 0でlogical required GETを消費せず、最初のbody-bearing GETだけがrequired consumptionを満たす。5件目、別method/pathはfatalで、全request/method/range/status/header/body byte countをmanifestへ残す。
  - `refactor-e2e.mjs`はJSONのcommon profileとscenario request bindingsを最初のactionより前に全てinstallする。各scenario/mode/viewportでnew BrowserContextを作り、cookie/storage/Cache/Service Worker/fake clock/counter/WSを共有しない。contextは`id,mode,scenario_id,viewport,created_at,installed_before_goto=true,locale=ja-JP,timezone=Asia/Tokyo,color_scheme=light,reduced_motion=reduce,clock_time=2026-01-15T12:00:00.000Z`をmanifestへ残し、action/request/consumption/barrier/screenshot/assertionの各recordへ同じ`context_id`を必須にする。許可するpass-throughはexact main/RSC document、`/_next/static/**`、favicon、既存brand/icon assetだけ。`/api/**`、WS/SSE、blogを含む外部通信はexact binding外なら即失敗する。fake WebSocketはbaselineだけsame-origin `/ws?api_key=fixture-browser-token`、finalはqueryなし`/ws`を期待し、subscribe frameをexact照合してからack/emissionする。real socket、URL query、subprotocol、subscribe frame、consoleへのfinal token occurrenceは各0でなければならない。
  - screenshot直前の共通barrierは、(1)全normal fixture exact count消費、(2)required binary body GET消費、(3)S09 subscribe+emit処理完了、(4)in-flight fixture 0が連続500ms、(5)`document.fonts.ready`、(6)2 rAFの順。unused required fixtureを撮影後にだけ検出してはならない。optional media HEAD/rangeだけ0回を許す。
  - `refactor-e2e.mjs`は実ユーザーcookie/token/live dataを読まない。`run-refactor-e2e.sh`以外からの起動、`--base-url`/`--port`、外部server、listen済みportの再利用を拒否する。Node自身が127.0.0.1のport 0で空きportを予約してからbounded 3回以内でNext childを起動し、ready document、child PID、child cwd=`services/dashboard`、`.next/BUILD_ID`を検査する。Appendix Bの9 scenario×1440×900/390×844=18枚をexact filenameで撮影し、`finally`でbrowserと自分が起動したchildだけへTERM→5秒→KILLを行い、終了後PID不在を確認する。baseline/finalのscenario ID・viewport・filename集合はbyte一致し、extra/missing shotをrejectする。両modeはJSONの対応action列をそのまま実行する。baselineはpost-fix semantic assertionを合否判定せず観測値を記録し、finalだけが全semantic assertionを必須にする。S03だけはbaselineで安全な`fixture`検索、finalで生`[`検索を行う。S09はRF-18のscroll assertionと`websocket-token-surfaces-zero`を持ち、scrollをreset後に撮影するためpixel差0を必須にする。
  - `run-refactor-e2e.sh`はCLIを`--mode baseline|final --task <validated task id> --attempt-id <canonical attempt> --expected-source-sha <40/64 lowercase hex> --evidence <task-owned path> --manifest <task-owned path> --visual-plan <static exact path>`と、finalだけの`--compare <master baseline-ui.json>`へ限定する。baselineのtaskはliteral master task `full-repo-refactoring-2026-07-24`、attemptは項目共通手順で検証済みの`ATTEMPT_ID=initial|<state-transfer retry_token>`とexact一致させる。finalのtaskは`release-boundaries.json`から解決したcurrent R7 release task、attemptは9.1で選択したcanonical `CLOSURE_ATTEMPT_ID`とexact一致させる。evidence pathからtask/attemptを逆推測せず、nonce、`latest`、mtimeによる選択を禁止する。unknown/duplicate option、symlink、repo外realpath、inherited `NEXT_PUBLIC_*`,`DASHBOARD_*`,`VEXA_*`,`NODE_OPTIONS`,`NPM_CONFIG_*`,`HTTP_PROXY`,`HTTPS_PROXY`,`ALL_PROXY`,`NO_PROXY`、caller指定URL/portを拒否する。baselineではDashboard build input（`services/dashboard/{src,public,tests,scripts,next.config.ts,package.json,package-lock.json,tsconfig.json,postcss.config.mjs}`とtranscript/core fixture dependency）のindex↔worktree byte差0、untracked non-ignored input 0、statusのworktree column空を確認してから`ACTUAL_SOURCE_SHA="$(git write-tree)"`を計算する。finalではtracked implementation scopeのindex/worktree差0を確認して`ACTUAL_SOURCE_SHA="$(git rev-parse HEAD)"`とし、引数とexact一致しなければbuild前にexit 1する。finalのuntracked pathは当該releaseの`.pipeline/{plans,evidence,gates,sessions,outcomes,approvals,tmp}/$TASK/**`だけを許す。buildとNext childはともに`env -i`から起動し、`PATH=<RF-00Eで記録したNode/npmを含む固定PATH>`,`HOME=$TASK_TMP/home`,`TMPDIR=$TASK_TMP/tmp`,`CI=1`,`NODE_ENV=production`,`NEXT_TELEMETRY_DISABLED=1`,`TZ=Asia/Tokyo`,`LANG=C.UTF-8`,`LC_ALL=C.UTF-8`,`NEXT_PUBLIC_BASE_PATH=""`,`NEXT_PUBLIC_BLOG_URL=https://blog.vexa.ai`、内部専用`VEXA_REFRACTOR_E2E=1`,`VEXA_REFRACTOR_SOURCE_SHA=$ACTUAL_SOURCE_SHA`だけを設定する。HOME/TMPDIRは0700で事前作成し、`.env*`から上記keyを上書きさせない。`.next/BUILD_ID`一致後に固定nonce付きでNode runnerを1回だけ呼ぶ。
  - `next.config.ts`は`VEXA_REFRACTOR_E2E=1`のbuildだけ`generateBuildId()`を内部`VEXA_REFRACTOR_SOURCE_SHA`へ固定し、main document responseの`X-Vexa-Source-Sha`にも同値をliteral固定する。E2E flag時に値が40/64 lowercase hexでなければbuildを失敗させる。flag未設定の通常`npm run build`、V-DASH/V-DASH-FINAL、production buildは既存build ID/header挙動をbyte同等で維持し、source headerを追加しない。production profileに`VEXA_REFRACTOR_E2E`/`VEXA_REFRACTOR_SOURCE_SHA`を配らない。Playwrightはcaller引数だけをruntime SHAとして信用せず、`.next/BUILD_ID`と各scenarioのmain document response headerを読んで全18枚がwrapper算出値と一致するか検査する。baselineはobserved header=`BASELINE_TREE_SHA`、finalはobserved header=`RELEASE_HEAD_SHA`でなければ撮影前にexit 1。
  - `console.error`、`pageerror`、`requestfailed`、unexpected 4xx/5xxを1件でも検知したらexit 1。各PNGへroute/viewport/source commit/tree/build IDをJSON manifestで紐づける。
  - RF-00Cだけはsource/test/matrixを明示stageした後、commit前に`BASELINE_TREE_SHA="$(git write-tree)"`を採取し、そのtreeから撮影したことをbindする。dirty worktreeの`git rev-parse HEAD`をsource SHAとして記録しない。撮影後、baseline manifest/PNG/integrityを追加stageして同じRF-00C commitへ含めるため、最終commit treeと`BASELINE_TREE_SHA`が異なるのは正常である。
  - `baseline-ui.json`は `schema_version,mode="baseline",source_commit_sha=null,source_tree_sha,expected_source_sha,observed_runtime_sha,next_build_id,server_pid,server_cwd,runner_nonce,planned_visual_changes_sha256,scenario_ids,viewports,browser_contexts[],action_results[],fixture_materialization_receipts[],fixture_requests[],fixture_consumption[],screenshot_barrier_results[],screenshots[{scenario_id,path,sha256,width,height,route,viewport,captured_at,context_id}],console_errors,page_errors,request_failures,http_errors,child_processes_after,contains_real_data=false,playwright_version,chromium_version,render_settings` を持つ。`fixture_materialization_receipts`は`http-json-template` fixtureの件数とexact一致（現在2件）、fixture ID UTF-8 byte順で、他transportのreceiptは0件とする。各receiptはJSON契約のexact fieldとhashを持ち、全receiptの`created_before_server_start=true,tracked_unchanged_after_capture=true`を要求する。`action_results`はJSON action列と同数・同順で、各要素はaction結果固有値と`observations`を持ち、assertion専用の`observed_field/operator/expected/actual`を要求しない。`fixture_requests`はHTTP/WSの要求順で`scenario_id,viewport,mode,context_id,method,raw_path_query,range,fixture,status,content_type,body_bytes,tracked_authoring_sha256,materialized_body_sha256,materialization_receipt_sha256,consumption_index`を持ち、全expected countとcontext IDの一意性を検証する。`captured_at`はUTC ISO-8601、配列はpath順、error/process配列は空、全PNG hashは実fileと一致する。
  - `baseline-integrity.json`は`task_id,rf_id="RF-00C",source_tree_sha,baseline_manifest_sha256,baseline_png_aggregate_sha256,planned_visual_changes_sha256,scenario_ids,created_at`をatomic writeする。aggregateは全18 PNGのrepo-relative pathをUTF-8 byte順でsortした`path + NUL + sha256 + LF`のSHA-256。baseline manifest、integrity、18 PNGはRF-00C commitへ含め、後続commitで1 byteも変更しない。final runnerはRF-00C commit blobを`git show`で読み、working evidenceとhash一致を検査するため、integrity fileとPNGをまとめて改ざんしても合格しない。
  - final modeは`final-ui.json`へ `schema_version,mode="final",head_sha,source_commit_sha,source_tree_sha,expected_source_sha,observed_runtime_sha,next_build_id,server_pid,server_cwd,runner_nonce,baseline_manifest_sha256,baseline_png_aggregate_sha256,planned_visual_changes_sha256,scenario_ids,viewports,browser_contexts[],action_results[],fixture_materialization_receipts[],fixture_requests[],fixture_consumption[],screenshot_barrier_results[],assertion_results[{scenario_id,viewport,context_id,name,observed_field,operator,expected,actual,status}],screenshots[{scenario_id,path,sha256,width,height,route,viewport,captured_at,context_id}],console_errors,page_errors,request_failures,http_errors,visual_diffs[{scenario_id,viewport,baseline_sha256,final_sha256,pixel_diff_ratio,rf_ids,behavioral_rf_ids,allowed_selectors,outside_region_pixel_diff_ratio,semantic_assertions,status}],child_processes_after,contains_real_data=false,playwright_version,chromium_version,render_settings`をatomic writeする。template receipt/requestはbaselineと同じauthoring path/hash、mode固有のmaterialized body/hashを持ち、tracked template不変を再検証する。`head_sha=source_commit_sha=expected_source_sha=observed_runtime_sha=next_build_id=RELEASE_HEAD_SHA`、`source_tree_sha=git rev-parse RELEASE_HEAD_SHA^{tree}`、`server_cwd=services/dashboardのrealpath`を要求する。stable scenarioとS09は全pixel同一、visual change scenarioはJSONのexact RF/selector/semantic assertionに一致しselector union外pixel差0だけを合格にする。S09は`app-main-scroll`不変・inner scroll増加、final WSのURL query/subprotocol/subscribe frame/console token各0をassertし、画像maskは持たない。allow-list外差、missing assertion、unknown RFは失敗。
  - pixel maskは同じviewportのbaseline/final DOMでallowed selectorが各1件であることを確認し、両bounding boxのleft/topはfloor、right/bottomはceilして得たpadding 0の整数pixel矩形のunionだけを許可領域にする。selector不存在/複数、viewport外box、union外1 pixel以上、unknown action/assertion/fixtureはfatal。action resultは`observations`、assertion resultだけは`observed_field,operator,expected,actual,status`をmanifestへ残す。
  - 新しいdiff依存は追加しない。Dashboard lockfileで解決済みの`sharp`をNodeからimportできることをRF-00Eでpreflightし、PNGを8-bit RGBAへdecodeして幅/高さ/各channel（alpha含む）のbyte equalityを比較する。import不能ならlockfileを更新せず停止する。Playwright/Chromiumはlockfile解決versionをmanifestへ記録し、`deviceScaleFactor=1,locale=ja-JP,timezoneId=Asia/Tokyo,colorScheme=light,reducedMotion=reduce`、固定clock `2026-01-15T12:00:00.000Z`、固定fixture IDを使う。各撮影前に`document.fonts.ready`を待ち、CSS animation/transition/caretを無効化し、`screenshot({animations:"disabled"})`とする。browser/version/render setting差はpixel差として許可せず再現環境不成立で停止する。
  - `capture-refactor-baseline.sh`は次の固定手順だけを持ち、`run-refactor-item.sh RF-00C`のA.5 `replay_policy=once` argv commandからRF-00C実行時に1回だけ呼ぶ。任意引数、任意path、任意shell文字列を受けない。最終all-runやretry transfer先で再実行してはいけない。生成済みbaseline manifest/integrity/PNGが1個でも存在する場合は上書きせずexit 1する。

```bash
#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"
node services/dashboard/scripts/generate-refactor-media-fixtures.mjs
git add -- \
  services/dashboard/tests \
  services/dashboard/scripts \
  services/dashboard/src \
  services/dashboard/next.config.ts \
  packages/transcript-rendering/src \
  services/vexa-bot/core \
  scripts/test/refactor-item-matrix.json
BASELINE_TREE_SHA="$(git write-tree)"
test -n "${ATTEMPT_ID:-}"
bash services/dashboard/scripts/run-refactor-e2e.sh \
  --mode baseline \
  --task full-repo-refactoring-2026-07-24 \
  --attempt-id "$ATTEMPT_ID" \
  --expected-source-sha "$BASELINE_TREE_SHA" \
  --evidence .pipeline/evidence/full-repo-refactoring-2026-07-24/screenshots/baseline \
  --manifest .pipeline/evidence/full-repo-refactoring-2026-07-24/baseline-ui.json \
  --visual-plan .pipeline/plans/full-repo-refactoring-2026-07-24/planned-visual-changes.json
bash scripts/test/verify-refactor-e2e-evidence.sh --mode baseline
git add -- \
  .pipeline/evidence/full-repo-refactoring-2026-07-24/baseline-ui.json \
  .pipeline/evidence/full-repo-refactoring-2026-07-24/baseline-integrity.json
# repo-wide *.png ignoreを、このtaskのexact baseline directoryだけ明示解除する。
git add -f -- \
  .pipeline/evidence/full-repo-refactoring-2026-07-24/screenshots/baseline
test "$(
  git diff --cached --name-only -- \
    .pipeline/evidence/full-repo-refactoring-2026-07-24/screenshots/baseline |
    awk '/\.png$/ {n++} END {print n+0}'
)" -eq 18
```
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-00C`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-00C`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - Dashboard 28 files / 199 tests以上、transcript 88 pass / 0 skip、core typecheckがpass。
  - A.5登録済み`capture-refactor-baseline.sh`が内部の固定argvとして`node services/dashboard/scripts/check-lint-baseline.mjs services/dashboard/tests/fixtures/lint-baseline.json`を実行しexit 0。checkerを別の未登録direct commandとして再実行しない。baseline件数をmachine-readableに記録し、調査時61 errors / 87 warningsとの差を説明する。
  - `services/dashboard/tests/refactor/rf_00c.test.ts::{test_transcript_correct_behavior_baseline,test_deferred_store_fixture_exposes_request_generation,test_fixture_routes_use_numeric_ids_native_ids_and_exact_raw_dtos_without_real_identity_or_token,test_authoring_projection_maps_identity_data_segments_and_recordings_without_default_leak,test_template_materialization_is_prestart_hash_bound_and_never_rewrites_tracked_authoring,test_template_materialization_rejects_unknown_remaining_nonloopback_and_invalid_base_path,test_action_dsl_rejects_unknown_missing_extra_and_nonunique_locator,test_new_context_per_mode_scenario_viewport_and_exact_common_request_counts,test_unbound_api_ws_external_and_rsc_shape_are_fatal,test_every_http_request_is_prebound_consumed_and_barrier_precedes_screenshot,test_media_head_open_ended_suffix_and_bounded_range_contract,test_s07_s08_recordings_exist_only_in_transcript_response,test_s09_fake_websocket_baseline_query_and_final_cookie_bff_contract,test_exact_eighteen_shot_inventory_is_shared_by_baseline_and_final,test_baseline_build_inputs_are_staged_and_worktree_matches_index,test_runner_builds_and_starts_its_own_exact_tree_and_rejects_external_base_url,test_runtime_sha_is_observed_from_build_id_and_served_response_not_caller_value,test_non_e2e_build_retains_existing_build_id_and_header_behavior,test_production_profiles_never_set_refactor_e2e_variables,test_final_manifest_schema_binds_actions_assertions_head_baseline_visual_plan_and_process_cleanup,test_rgba_diff_counts_one_pixel_when_any_single_channel_differs,test_selector_mask_rejects_one_pixel_outside_union}`
  - `services/vexa-bot/core/src/refactor-tests/rf_00c.test.ts::{test_registry_rejects_unregistered_and_missing_active_tests,test_single_file_report_contains_literal_nodeids_and_counts}`
  - `bash services/dashboard/scripts/capture-refactor-baseline.sh` がexit 0。
  - Core `npm test`がregistryの全active testを実行し、unregistered test 0、active missing 0。
  - screenshotにsource commit/tree SHA、Next build ID、observed runtime SHA、server PID/cwd、viewport、route、UTC撮影時刻が紐づく。RF-00C commitから`git show`したbaseline manifest/PNG/integrity hashがworking evidenceと一致し、後続commitのbaseline path変更0。
  - 個人情報/実token 0、console/page/network error 0、子process残存0。
  - Required suites: `V-DASH`, `V-TRANSCRIPT`, `V-CORE`。
- リスクと戻し方: build生成物混入とlive個人情報の保存。tracked差分を確認し、個人情報が映る場合は画像を即保存対象外にしてfixture E2Eのみ残す。test/evidence commitを共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-00E
- コミット: `RF-00C add frontend characterization and visual baseline`

### RF-00D Ops/Harnessの偽陽性baseline

- 対象:
  - `tests3/test-registry.yaml:1-793`
  - `tests3/checks/registry.json:1-1581`
  - `tests3/registry.yaml:1-3779`
  - `tests3/Makefile:1-338`
  - 新規 `tests3/unit/refactor/test_rf_00d.py:1-末尾`
  - 新規 `tests3/unit/fixtures/fake-bin/curl:1-末尾`
  - 新規 `tests3/unit/fixtures/fake-bin/docker:1-末尾`
  - 新規 `tests3/unit/fixtures/fake-bin/helm:1-末尾`
  - 新規 `tests3/unit/fixtures/fake-bin/git:1-末尾`
  - 新規 `tests3/unit/fixtures/registry-baseline.json:1-末尾`
  - fixtureから観測するread-only surface: `deploy/compose/**:1-末尾`, `deploy/lite/**:1-末尾`, `deploy/helm/**:1-末尾`, `scripts/harness/**:1-末尾`, 3章で事前配置したcanonical `claude-dotfiles@fb9bd5f0d2a20a52d9d48dc56d4080a65c5c3233`の`.claude/hooks/{adapter-validate,approval-hash-check,backcast-validate,codex-review-validate,dual-review-validate,external-consultation-validate,feedback-prune,pr-ready-gate,pre-implementation-review-gate}.sh:1-末尾`, `.github/workflows/**:1-末尾`
- 問題: commandが0を返しても検査実体がない経路がある。実環境Docker/GCPを使わず再現できるfixtureが必要。
- 変更:
  - PATH先頭にfake `curl`、`docker`、`docker compose`、`helm`、`git` を置く一時fixture helperを `tests3/unit/fixtures/fake-bin/` に追加する。
  - `tests3/unit`はpytest collection対象とし、全testは`test_*.py`のpytest関数/fixtureで書く。既知bugは`@pytest.mark.xfail(strict=True)`を使い、RF-31以降で通常passへ変更する。
  - registry全entryについて `id/status/script_exists/mode/source_commit` を `registry-baseline.json` へ出す監査testを追加する。不在45件を自動削除しない。
  - report 0件、全skip、0 step、health timeout、schema init失敗、Helm不在、task-id traversalを再現するtestを追加し、後続項目まで `xfail(strict=True)` にする。
  - 1関数に複数caseがあるものはAppendix A.6のparameter IDをliteral指定し、解消項目が異なるcaseをまとめてxfail解除しない。
  - shell portability baselineをmacOS/BSD互換commandで採取する。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-00D`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-00D`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - strict xfailのcollection/result確認は`run-refactor-item.sh RF-00D`と`V-OPS`のmachine report内assertionとして行い、本文から別のraw pytestを実行しない。
  - `tests3/unit/refactor/test_rf_00d.py::test_fail_open_baseline`のAppendix A.6に列挙した8 parameter IDと、`tests3/unit/refactor/test_rf_00d.py::test_fake_binaries_never_delegate_to_real_tools`
  - 実Docker daemon、Kubernetes cluster、GCPへ接続していない。
  - registry 91件、不在45件がbaseline JSONと一致するか、差異理由が記録される。
  - Required suite: `V-OPS`。
- リスクと戻し方: fake binaryが実commandを呼ぶ危険。fixture内で絶対にdelegateせず、受けたargvと指定exitだけ返す。戻す場合はtest-only commitを共通retry protocol。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-00E
- コミット: `RF-00D add fail-open infrastructure characterization`

## 4. フェーズ1: 境界・認可・秘密値

### フェーズ1のexact target inventory

RF-03A〜RF-06I3の次のcommandは、各項目に列挙済みのexact path inventoryが全production/config callerを覆うか確認するread-only completeness checkである。検索結果をwrite対象へ展開しない。各commandを項目開始時に実行し、全行をitem evidenceへ保存する。`node_modules`、build output、`.pipeline`を除いて、対象欄のexact path外にproduction/config一致が1件でもあれば編集せずplan reviewへ戻る。README/test/fixture/CI一致はread-only分類し、exact pathとして対象欄に別記されない限り変更しない。commandがexit 1かつ一致0件で、個別項目が0件を明示許可していない場合も中断する。

```bash
set -euo pipefail
# RF-03A
git grep -n -E -e 'ADMIN_API_TOKEN|X-Admin-API-Key|AgentRuntimeConfig|workspace_git' \
  services/admin-api services/agent-api services/meeting-api services/runtime-api deploy
# RF-03B
git grep -n -E -e 'VEXA_ADMIN_API_KEY|/admin/users|workspace_git|webhook_secret|calendar.*oauth|zoom.*oauth' \
  services/admin-api services/api-gateway services/dashboard services/meeting-api
# RF-03C
git grep -n -E -e 'vexa-user-info|/admin/tokens|/user/tokens|masked_suffix|createUserToken|getAuthenticatedUserId' \
  services/admin-api services/api-gateway services/dashboard services/meeting-api
# RF-03D
git grep -n -E -e 'git_clone_init|workspace_git|git clone|GIT_ASKPASS|AGENT_GIT_ALLOWED_HOSTS|create_subprocess.*docker' \
  services/agent-api deploy/compose deploy/lite deploy/helm
# RF-04A
git grep -n -E -e 'VEXA_API_KEY|authToken|state\.token|meData\.token|data\.token|useAuthStore' \
  services/dashboard/src
# RF-04B
git grep -n -E -e 'vexa-admin-session|admin-session|VEXA_ADMIN_API_KEY|timingSafeEqual' \
  services/dashboard/src
# RF-05A
git grep -n -E -e 'X-User-ID|X-Internal-Secret|TrustedIdentity|RoutePolicy|API_KEYS|API_KEY' \
  services/api-gateway services/admin-api services/meeting-api services/calendar-service services/agent-api \
  services/mcp services/transcription-service services/tts-service services/voiceprint-service \
  services/wake-stt services/wake-orchestrator services/runtime-api deploy
# RF-05B
git grep -n -E -e '/agent|/internal/chat|AGENT_API|VEXA_AGENT|agent_api' \
  services/api-gateway services/agent-api services/dashboard services/telegram-bot packages/vexa-cli
# RF-05C
git grep -n -E -e 'X-Internal-Secret|INTERNAL_API_SECRET|AGENT_API|RUNTIME_API|workspace/status|schedule' \
  services/vexa-agent services/agent-api services/meeting-api services/runtime-api deploy
# RF-05D1/RF-05D1B/RF-05D2
git grep -n -E -e 'RUNTIME_API|X-Internal-Secret|INTERNAL_API_SECRET|/profiles|scheduler' \
  services/runtime-api services/meeting-api services/agent-api services/api-gateway \
  services/vexa-bot services/vexa-agent deploy
# RF-05E
git grep -n -E -e 'webhook|callback_url|follow_redirects|httpx\.|requests\.|Authorization|webhook_secret' \
  services/admin-api services/agent-api services/meeting-api services/runtime-api services/dashboard
# RF-05F
git grep -n -E -e 'changeme|default.*secret|DIRECT_LOGIN|MAGIC_LINK|NEXTAUTH|ADMIN_TOKEN|INTERNAL_API_SECRET' \
  deploy services/admin-api services/api-gateway services/meeting-api services/runtime-api services/dashboard
# RF-05G
git grep -n -E -e 'MeetingToken|ADMIN_TOKEN|ADMIN_API_TOKEN|transcription-collector|MEETING_TOKEN' \
  services/meeting-api deploy
# RF-05H
git grep -n -E -e 'INTERNAL_API_SECRET|X-Internal-Secret|unified-callback|callbackToken|BOT_CONFIG' \
  services/meeting-api services/vexa-bot services/runtime-api deploy
# RF-06A
git grep -n -E -e 'AWS_|S3_|MINIO|workspace.*archive|archive.*workspace|aws s3' \
  services/agent-api services/runtime-api deploy
# RF-06B
git grep -n -E -e 's3AccessKey|s3SecretKey|MINIO|browser-userdata|s3-sync|BOT_CONFIG' \
  services/meeting-api services/vexa-bot services/runtime-api deploy
# RF-06C1/RF-06C2
git grep -n -E -e 'REDIS_URL|redisUrl|createClient|transcription_segments|speaker_events_relative|bot_commands:' \
  services/meeting-api services/vexa-bot services/runtime-api deploy
# RF-06D1/RF-06D2
git grep -n -E -e 'TRANSCRIPTION_SERVICE_TOKEN|WAKE_STT|TTS_API_TOKEN|TTS_SERVICE_URL|transcribe:write|audio.synthesize|recording.upload|MeetingToken|botConfig\.token|currentBotConfig\.token' \
  services/meeting-api services/transcription-service services/wake-stt services/tts-service services/vexa-bot services/runtime-api deploy
# RF-06E
git grep -n -E -e 'ANTHROPIC_API_KEY|CLAUDE_CREDENTIALS_PATH|CLAUDE_JSON_PATH|provider.*credential' \
  services/agent-api services/runtime-api services/meeting-api deploy
# RF-06F
git grep -n -E -e 'ZOOM_CLIENT_SECRET|ZOOM_SDK_JWT|createHmac|tokenExp|appKey' \
  services/meeting-api services/vexa-bot services/runtime-api deploy
# RF-06G
git grep -n -E -e 'ZOOM_CLIENT_SECRET|ZOOM_SDK_JWT|createHmac|run-zoom-bot|tokenExp|appKey' \
  services/meeting-api services/vexa-bot services/runtime-api deploy
# RF-06H
git grep -n -E -e 'HTTP_PROXY|HTTPS_PROXY|Proxy-Authorization|UPSTREAM_PROXY|BOT_PROXY' \
  services/meeting-api services/runtime-api services/vexa-bot deploy
# RF-06I1/RF-06I2/RF-06I3
git grep -n -E -e 'backend_id|runtime\.managed|session_uid|VNC|x11vnc|websockify|CDP|9222|9223|ProcessBackend|subprocess|pass_fds|sshd|XAUTHORITY' \
  services/runtime-api services/meeting-api services/api-gateway services/vexa-bot \
  deploy
```

### RF-01 Harness task-idのpath containment

- 対象:
  - `scripts/harness/backcast-approval.sh:1-末尾`
  - `scripts/harness/backcast-checkpoint.sh:1-末尾`
  - `scripts/harness/backcast-current.sh:1-末尾`
  - `scripts/harness/backcast-evidence-pack.sh:1-末尾`
  - `scripts/harness/backcast-manifest.sh:1-末尾`
  - `scripts/harness/backcast-next-checkpoint.sh:1-末尾`
  - `scripts/harness/backcast-state.sh:1-末尾`
  - `scripts/harness/build.sh:1-末尾`
  - `scripts/harness/codex-build.sh:1-末尾`
  - `scripts/harness/codex-review.sh:1-末尾`
  - `scripts/harness/codex-session-ledger.sh:1-末尾`
  - `scripts/harness/delivery-integrity-smoke.sh:1-末尾`
  - `scripts/harness/external-consultation.sh:1-末尾`
  - `scripts/harness/full-loop-smoke.sh:1-末尾`
  - `scripts/harness/outcome-judge.sh:1-末尾`
  - `scripts/harness/review-policy-smoke.sh:1-末尾`
  - `scripts/harness/sml-decision.sh:1-末尾`
  - `scripts/harness/task-set.sh:1-末尾`
  - `scripts/harness/validate-runtime-profile.sh:1-末尾`
  - `scripts/harness/worktree.sh:1-末尾`
  - `schemas/checkpoint-contract.schema.json:26-29`
  - 新規 `scripts/harness/lib/task-path.sh:1-末尾`
  - read-only inventory: `git grep -n -F -e '$TASK' -e '${TASK}' -- scripts/harness`。上記20 shell file以外にtask-idをpathへ結合するproduction callerが1件でもあれば変更せずplan reviewへ戻る
- 問題: `/abs`、`../x`、`.`、`..` 等が `.pipeline` 外のpathを作り得る。
- 変更:

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

`validate_task_id` は `^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$` のみ許可する。全pathはresolve後にrepo root配下かつ指定 `.pipeline/<kind>/` 配下であることを共通helperで再検証する。schemaのpatternも同一にする。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-01`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-01`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_task_id_validation.py::test_rejects_dot_dot_absolute_separator_control_and_overlong`
  - `::test_rejected_id_writes_nothing_outside_pipeline`
  - `::test_valid_existing_task_ids_pass`
  - `V-OPS`
- リスクと戻し方: 過去の特殊文字付きtask-idが拒否される。項目0の現存ID監査で不適合があればrenameせず中断。DBなしのためcommit 共通retry protocolで戻せる。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-00D
- コミット: `RF-01 contain harness task paths`

### RF-02 Admin DB再作成の破壊防止

- 対象:
  - `libs/admin-models/admin_models/database.py:118-144`
  - `services/admin-api/app/scripts/recreate_db.py:29-59`
  - `services/meeting-api/meeting_api/database.py:1-末尾`
- 問題: Admin側だけ誤接続したDBのschemaを確認なしでdrop/recreateできる。
- 変更:
  - Meeting側のguardを共通関数`require_destructive_schema_permission()`として`libs/admin-models`へ移す。`VEXA_ENV`はliteral `development|test`だけ、`ALLOW_DROP_SCHEMA=true`、`ALLOW_DROP_SCHEMA_DB_NAME=DB_NAME`を接続前の最低条件とし、production/unset/unknown、どれか未設定、不一致、文字列`true`以外はconnection/SQL実行前にexit 2。
  - 同名DBを別serverへ誤接続しても通らないよう、接続後かつDDL前にread-onlyで`current_database(),inet_server_addr(),inet_server_port(),current_user`と、初回安全なDB bootstrapで作成済みの管理tableから`database_sentinel_uuid`を取得する。operator入力`ALLOW_DROP_SCHEMA_TARGET_FINGERPRINT`はcanonical JSON `{"database","host_ip","port","user","sentinel_uuid"}`のSHA-256 lowercase hexで、取得値から再計算したfingerprintとconstant-time exact一致しなければDDL 0でexit 2。sentinel不存在・重複・型不正も自動作成せず拒否する。
  - CLI対話は固定語`recreate`ではなく、取得したfingerprintの先頭12桁を含む`recreate <12hex>`の完全一致を1回だけ要求する。stdinがTTYでない、余分な空白、case違い、EOFは拒否する。`--dry-run`はsecretを含まないdatabase/host IP/port/user/sentinel hash/fingerprintと各guardのpass/failだけ表示し、DDLを実行しない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-02`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-02`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_database_guard.py::test_recreate_rejected_without_explicit_allow`
  - `::test_recreate_rejected_for_production_environment`
  - `::test_recreate_allowed_only_for_exact_environment_and_database_fingerprint`
  - `::test_same_database_name_on_wrong_host_performs_zero_ddl`
  - `::test_missing_or_wrong_sentinel_and_wrong_confirmation_perform_zero_ddl`
  - testではfake connectionを使い、実DBへ接続しない。
  - `V-BACKEND`
- リスクと戻し方: 開発者の既存操作が止まる。guardを迂回せず、新しい明示envをREADMEへ記載する。共通retry protocolで戻せるが、drop実行後のデータは戻らないため本項目testはmock限定。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-00B
- コミット: `RF-02 guard destructive admin schema recreation`

### RF-03A Agent専用runtime config契約を追加してconsumerを先に移行

- 対象:
  - `services/admin-api/app/main.py:821-883`
  - `services/meeting-api/meeting_api/schemas.py:329-381`
  - `services/agent-api/agent_api/config.py:12-27`
  - `services/agent-api/agent_api/container_manager.py:30-105,138-176`
  - `services/agent-api/agent_api/chat.py:116-130`
  - `deploy/compose/docker-compose.yml:1-末尾`
  - `deploy/lite/{entrypoint.sh,supervisord.conf}:1-末尾`
  - `deploy/helm/charts/vexa/{values.yaml,templates/secret.yaml,templates/deployment-admin-api.yaml}:1-末尾`
  - 対象外（変更禁止）: Helm deploymentが存在しないAgent service向けHelm object
  - 新規 `services/admin-api/tests/test_agent_runtime_config.py:1-末尾`
  - 新規 `services/agent-api/tests/test_agent_runtime_config.py:1-末尾`
- 問題: Agentは汎用`/admin/users/{id}`から`data.env`と`workspace_git.token`を取る。公開DTOだけ先にallow-list化するとcontainer/env/git cloneを壊し、Agentへ全権Admin tokenを持たせ続ける。
- 変更:
  - Adminへ`GET /internal/users/{user_id}/agent-runtime-config`を`include_in_schema=False`で追加する。AdminとAgentだけに配る専用`AGENT_RUNTIME_CONFIG_SECRET`を`X-Agent-Runtime-Config-Secret`でconstant-time比較してからDBへ触り、未設定はproduction startup failure、欠落/不一致403。既存`INTERNAL_API_SECRET`、Admin key、Gateway identity secretはこのendpointで拒否する。
  - responseは `env: dict[str,str]` と `workspace_git: {repo, branch, token}|null`だけ。未知keyを返さず、型不正422、userなし404、設定なしは空/null。
  - Agentの`get_user_data()`を`get_agent_runtime_config()`へ置換し、`ADMIN_API_TOKEN`/`X-Admin-API-Key`を削除する。Compose/Lite/Helmは`AGENT_RUNTIME_CONFIG_SECRET`をAdminとAgentへだけ配り、Meeting/Runtime/Gateway/Bot/Browserへ配らない。cache key/TTL/invalidationは維持する。
  - 値をlog/exception/URLへ出さず、`env_count`と`has_workspace_git`だけ記録する。公開User DTOは本項目ではまだ変えない。

```python
class AgentRuntimeConfig(BaseModel):
    env: dict[str, str] = Field(default_factory=dict)
    workspace_git: AgentWorkspaceGitConfig | None = None
```

- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-03A`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-03A`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/admin-api/tests/test_agent_runtime_config.py::{test_internal_config_rejects_missing_wrong_legacy_and_cross_audience_secret,test_internal_config_returns_only_env_and_workspace_git}`
  - `services/agent-api/tests/test_agent_runtime_config.py::{test_agent_uses_internal_endpoint_without_admin_token,test_runtime_config_secret_never_appears_in_logs}`
  - canary secretはresponse/caplog/exceptionに0件。render済みdeployではAdmin/Agent以外のenv/Secret refに0件。
  - `rg -n 'ADMIN_API_TOKEN|X-Admin-API-Key' services/agent-api`はnegative assertion以外0件。
  - `V-BACKEND`。
- リスクと戻し方: Adminだけ戻すと新Agentがconfigを取れない。rolloutはAdmin→Agent、rollbackはAgent→Admin。DB保存形式は変えず、失敗branchを保持して前SHAから再実行。
- 依存: RF-00B
- コミット: `RF-03A add a least-privilege agent runtime config contract`

### RF-03B Scoped user APIへconsumerを移して公開User DTOをallow-list化

- 対象:
  - `services/meeting-api/meeting_api/schemas.py:329-381,1262-1267`
  - `services/admin-api/app/main.py:161-285,339-470,724-819`
  - 新規 `services/admin-api/app/webhook_delivery_broker.py:1-末尾`
  - `services/api-gateway/main.py:1405-1433`
  - `services/api-gateway/main.py:321-466`
  - `services/meeting-api/meeting_api/meetings.py:990-1001`
  - `services/meeting-api/meeting_api/webhooks.py:100-289`
  - `services/meeting-api/meeting_api/webhook_delivery.py:99-263`
  - `services/meeting-api/meeting_api/webhook_retry_worker.py:115-258`
  - 新規 `scripts/migrations/migrate_webhook_credentials.py:1-末尾`
  - `services/dashboard/src/app/api/webhooks/{config,rotate-secret,test,deliveries}/**:1-末尾`
  - `services/dashboard/src/app/api/calendar/oauth/start/route.ts:1-107`
  - `services/dashboard/src/app/api/calendar/oauth/complete/route.ts:1-198`
  - `services/dashboard/src/app/api/zoom/oauth/start/route.ts:1-104`
  - `services/dashboard/src/app/api/zoom/oauth/complete/route.ts:1-205`
  - 新規 `services/admin-api/tests/test_public_user_contract.py:1-末尾`
  - 新規 `services/admin-api/tests/test_user_webhook_contract.py:1-末尾`
  - 新規 `services/admin-api/tests/test_webhook_delivery_broker.py:1-末尾`
  - 新規 `services/meeting-api/tests/test_webhook_credential_refs.py:1-末尾`
  - 新規 `services/api-gateway/tests/test_user_config_proxy.py:1-末尾`
  - 新規 `services/api-gateway/tests/test_oauth_state_registry.py:1-末尾`
  - 新規 `services/dashboard/tests/test_user_scoped_webhook_routes.test.ts:1-末尾`
  - 新規 `services/dashboard/tests/test_oauth_partial_write.test.ts:1-末尾`
  - 新規 `services/dashboard/tests/test_oauth_subject_binding.test.ts:1-末尾`
- 問題: deny-listはnested git/env/OAuth/webhook secretを公開User/analyticsへ露出し得る。Dashboard webhook BFFも全権Admin keyで汎用User JSONをread-modify-writeする。
- 変更:
  - 認証tokenのsubjectだけを使う `GET/PUT /user/webhook`、`POST /user/webhook/rotate-secret`、`POST /user/webhook/test`、`GET /user/webhook/deliveries`、`GET /user/workspace-git`をAdmin/Gatewayへ追加する。requestに`user_id`を受けない。Admin-owned `user.data.webhook_profiles`はversioned mapとし、各versionを`{credential_ref,endpoint_url,webhook_secret,events,status,created_at}`へ固定する。`credential_ref`はCSPRNG 128-bit以上、subject/versionへ一意。raw secretを許すのはAdmin DBのこのrecord、rotate response 1回、Admin delivery brokerのrequest-local memoryだけ。
  - GETはmasked secretとactive `credential_ref`、rotateだけ新secretを1回返す。PUTでsecret省略時は既存versionを維持し、URL/events/secretのどれかを変える場合は新version/refを作る。testは保存済みrefだけを使い、body `url,secret,headers`を422、redirectを追わない。完全なoutbound policyはRF-05Eで行う。
  - Gateway token validation response/cache/envelope/headerから`webhook_url,webhook_secret,webhook_events`を削除し、Meeting createにはsubject-bound `webhook_credential_ref`だけを渡す。Meeting row/data、delivery record、Redis retry jobは`credential_ref,event_type,payload,attempt,next_retry_at,created_at,metadata`だけを保存し、URL、secret、Authorization/HMAC headerを0にする。version refがURL/events/secretのsnapshotをAdmin側で束縛するため、会議作成時設定を固定する既存挙動は維持する。
  - Adminへ固定`POST /internal/webhook-deliveries`を追加する。RF-05A後のMeeting identityは`sub,meeting_id,credential_ref,event_type,payload_sha256,attempt_id,iat,exp<=30,jti`をbindする。Admin brokerはref owner/status/versionを確認し、dispatch直前だけDBからURL/secretを解決してHMAC/Bearer headerをrequest-local memoryで生成し、response/log/queueへ返さない。wrong subject/ref/audience/body/replayはHTTP transport 0。同じ業務`attempt_id`はdelivery idempotency、identity `jti`はrequestごとに新規とし混用しない。
  - migrationは新規webhook enqueueをfreezeし、Meeting JSONBと`webhook:retry_queue`をbounded inventoryする。raw値はmemory内だけでAdmin profile versionへupsertし、Meeting row/queueをrefへ置換する。値をstdout/log/evidence/tmpへ出さず、`migrated+revoked+expired=inventory`と全legacy raw field 0を確認してfreezeを解除する。crash/retryは既にref化済みrecordをbyte不変でskipする。
  - Dashboard webhook routeをcookie user tokenでGatewayへproxyするだけにし、`VEXA_ADMIN_API_KEY`、汎用`/admin/users`、raw URL、Dashboard内HMACを削除する。
  - Calendar/Zoom OAuth startはbodyの`userEmail`を認可に使わない。既存HttpOnly user tokenをGatewayへ送り、Gatewayが解決した認証subjectだけを採用する。bodyに`userEmail,userId,redirectUri,provider`があれば400、表示用cookie/emailと不一致でも認証token subjectを変えない。`returnTo`はsingle leading `/`、backslash/authority/encoded authority/controlなしのsame-origin relative pathだけを許し、不正値は`/meetings`へ黙って丸めず400にする。
  - Gatewayへ認証subject専用`POST /user/oauth/state/{calendar|zoom}`と`POST /user/oauth/state/{calendar|zoom}/consume`を追加する。startはCSPRNG 256-bitのopaque stateとRFC 7636 verifierを発行し、Redis `oauth-state:<sha256(state)>`へ`provider,subject_id,redirect_uri,return_to,pkce_verifier_ciphertext,nonce,key_id,iat,exp`だけを`SET NX EX 600`で保存する。verifierはGatewayだけに配る32-byte `OAUTH_STATE_ENCRYPTION_SECRET`でAES-256-GCM暗号化し、AADへprovider/subject/state hash/redirect/expをbindする。create responseはopaque stateとS256 challengeだけ、consume成功responseはDashboard serverへverifierを1回だけ返す。raw API/provider token、email、raw state、raw verifierをRedis/log/evidence/browser responseへ保存しない。redirect URIはprovider別server configのexact値だけとする。
  - completeは現在のHttpOnly user token subject、provider、server-config redirect URI、opaque stateをGateway consume endpointへ渡す。Gatewayは`WATCH`→`GET`→provider/subject/redirect/expiry照合→`MULTI DEL`→`EXEC`を最大5回行い、1 requestだけがconsume成功する。Lua/GET後単独DELを使わない。wrong provider/subject/redirect/expired/tampered、100並列replayは99件以上がtoken exchange/Admin PATCHより前に拒否され、失敗requestは正規stateを削除しない。成功requestはprovider exchangeより前にstateを消費し、exchange失敗でも同じstateを再利用できない。
  - Calendar/Zoom completionは汎用User dataをGET/mergeせず、consume responseのsubjectに対するprovider top-level keyだけを目的別Admin endpointへPATCHし、tokenをlog/responseへ出さない。state payloadへuser ID/email/provider credentialを埋める旧HMAC blobと`NEXTAUTH_SECRET`/Admin key fallbackを削除する。
  - consumer移行後、公開`PublicUserData`を `workspace_git:{repo,branch,has_token}`だけのallow-listへする。`env`、webhook/OAuth、未知keyを落とし、User detail/analyticsも同serializerを通す。
  - Admin PATCH logは`user_id`と変更key名だけにし、旧/new dataの値を削除する。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-03B`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-03B`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/admin-api/tests/test_public_user_contract.py::test_all_user_and_analytics_responses_use_allow_list`
  - `services/admin-api/tests/test_user_webhook_contract.py::{test_rotate_returns_secret_once_only,test_webhook_test_rejects_client_url_and_uses_stored_url}`
  - `services/admin-api/tests/test_webhook_delivery_broker.py::{test_ref_is_subject_bound_and_raw_secret_never_leaves_admin_broker,test_wrong_subject_ref_audience_body_and_replay_make_zero_http_calls,test_rotate_returns_secret_once_and_creates_new_version}`
  - `services/meeting-api/tests/test_webhook_credential_refs.py::{test_meeting_and_retry_queue_store_ref_but_no_url_header_or_secret,test_retry_resolves_ref_just_in_time_and_preserves_payload_and_backoff,test_legacy_rows_and_queue_are_migrated_without_value_logging}`
  - `services/api-gateway/tests/test_user_config_proxy.py::test_validate_cache_and_meeting_proxy_never_contain_webhook_secret`
  - `services/dashboard/tests/test_user_scoped_webhook_routes.test.ts::test_dashboard_never_calls_admin_users_for_webhooks`
  - `services/dashboard/tests/test_oauth_partial_write.test.ts::{calendar_completion_updates_only_calendar_key,zoom_completion_updates_only_zoom_key}`
  - `services/dashboard/tests/test_oauth_subject_binding.test.ts::{test_start_uses_authenticated_subject_and_rejects_user_email_user_id_and_redirect_override,test_return_to_rejects_authority_backslash_encoded_authority_and_control,test_complete_consumes_state_before_provider_exchange_and_never_uses_state_subject_from_client}`
  - `services/api-gateway/tests/test_oauth_state_registry.py::{test_state_record_contains_only_hashes_and_nonsecret_subject_metadata,test_wrong_provider_subject_redirect_and_expiry_have_zero_consume_or_upstream_side_effects,test_hundred_parallel_consumers_produce_exactly_one_success,test_provider_exchange_failure_cannot_reuse_consumed_state,test_failed_mismatch_does_not_burn_valid_state,test_state_and_pkce_values_never_enter_log_redis_dump_or_response}`
  - git/env/OAuth/webhook canaryは全public/Admin-user/analytics responseとcaplogに0件。RF-03A internal endpointとrotate直後だけexact例外。
  - `rg -n 'VEXA_ADMIN_API_KEY|/admin/users' services/dashboard/src/app/api/webhooks`、`rg -n 'Current:.*data|New:.*data' services/admin-api/app`、`rg -n 'findUserByEmail|userEmail|NEXTAUTH_SECRET|signStatePayload' services/dashboard/src/app/api/{calendar,zoom}/oauth`はnegative assertion以外0件。
  - `V-BACKEND`, `V-DASH`。
- リスクと戻し方: undocumented clientが汎用`user.data`を読む可能性、OAuth callback中の旧stateが無効になる可能性。汎用dataやstateless stateへ戻さず、配備前に新規startを一時停止し最大旧state TTL 600秒+clock skewを待ってからGateway→Dashboardの順に切り替える。失敗branchを保持しRF-03A直後の合格SHAから再実行する。
- 依存: RF-03A, RF-00C
- コミット: `RF-03B replace generic user data reads with scoped contracts`

### RF-03C API token管理を認証subjectへ束縛しtoken一覧のraw再表示を止める

- 対象:
  - `services/dashboard/src/lib/auth-utils.ts:13-58`
  - `services/dashboard/src/app/api/profile/keys/route.ts:1-109`
  - `services/dashboard/src/app/api/profile/keys/[id]/route.ts:1-50`
  - `services/dashboard/src/app/profile/page.tsx:110-190,290-330`
  - `services/admin-api/app/main.py:391-566,724-818`
  - `services/meeting-api/meeting_api/schemas.py:360-373`
  - `services/api-gateway/main.py:1-末尾`（既存`/auth/me`と新規`/user/tokens` routeの追加位置を含む）
  - 新規 `services/admin-api/tests/test_user_token_contract.py:1-末尾`
  - 新規 `services/api-gateway/tests/test_user_token_routes.py:1-末尾`
  - 新規 `services/dashboard/tests/test_profile_token_ownership.test.ts:1-末尾`
  - 新規 `services/dashboard/tests/test_cookie_only_auth_state.test.ts:1-末尾`
- 問題: 有効なA tokenと未署名`vexa-user-info` cookieのB emailを組み合わせるとBのuser IDを採用し、全権Admin BFF経由でBのraw API token一覧・発行・任意ID削除へ到達できる。token一覧も既存raw値を再表示する。
- 変更:
  - `getAuthenticatedUserId()`は表示用`vexa-user-info`を一切読まず、`vexa-token`をGateway `GET /auth/me`へ送り、そのresponseの`user_id`だけを認可subjectとして採用する。`/auth/me`が非200、`user_id`が正の整数でない、bodyが不正なら`null`。email/name cookieは表示専用で、認可・DB lookup・object keyに使わない。
  - Admin/Gatewayへ認証subject専用`GET /user/tokens`、`POST /user/tokens`、`DELETE /user/tokens/{token_id}`を追加する。clientから`user_id`を受けず、current tokenから解決したuserだけを対象にする。DELETEはtoken行の`user_id`一致をSQL条件へ含め、他人/不存在は同じ404、DELETE件数0。RF-05A後はtrusted identityへ差し替えるがpath/DTOは維持する。
  - Dashboard profile BFFはcookie user tokenをGatewayへproxyするだけにし、`VEXA_ADMIN_API_KEY`、Admin URL、`/admin/users`、`/admin/tokens`を削除する。
  - 一覧DTOは`id,name,scopes,created_at,last_used_at,expires_at,masked_suffix`だけ。raw `token`はPOST作成responseで1回だけ返し、DB再読込/list/log/exceptionには返さない。既存token値を復号・再表示するfallbackは作らない。
  - profile pageは一覧行で`masked_suffix`だけを表示しcopy buttonを出さない。作成成功modalだけresponseのraw tokenをmemory stateへ保持し、modal close/unmountで破棄する。Git workspace token UIは別purpose contractなのでこの項目で変更しない。
  - Dashboardの既存auth response/store tokenはRF-04Aのcookie-only consumer移行と同時に削除する。RF-03C単体では認証response shapeを変えず、Phase 1完了前に途中deployしない。

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

- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-03C`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-03C`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/dashboard/tests/test_profile_token_ownership.test.ts::{a_token_with_forged_b_user_info_still_resolves_a,normal_profile_routes_never_use_admin_credentials,token_list_and_profile_rows_never_contain_raw_token,created_token_is_visible_once_and_cleared_on_close}`
  - `services/admin-api/tests/test_user_token_contract.py::{test_list_never_returns_raw_token,test_create_returns_raw_token_once,test_delete_requires_current_owner}`
  - `services/api-gateway/tests/test_user_token_routes.py::{test_subject_bound_token_crud,test_cross_user_token_id_is_not_found}`
  - AからBのlist/create/deleteは不可でB行更新0。list/profile row/caplogにtoken canary 0、create response/modalだけexact 1件。既存auth responseの一時互換はこの項目のcanary対象外だがRF-04Aで必ず0にする。
  - `rg -n 'VEXA_ADMIN_API_KEY|/admin/users|/admin/tokens' services/dashboard/src/app/api/profile services/dashboard/src/lib/auth-utils.ts` は0件。
  - `V-BACKEND`, `V-DASH`。
- リスクと戻し方: token値を一覧からcopyしていた利用者は作成時以外に再取得できなくなる。raw再表示を戻さず、UIへ「作成時のみ表示」を明記する。失敗branchを保持し、RF-03BのSHAから新worktreeで再実行する。
- 依存: RF-03B, RF-00C
- コミット: `RF-03C bind token management to the authenticated subject`

### RF-03D Agent workspace Git cloneからshell・credential URL・任意originを除去

- 対象:
  - `services/agent-api/agent_api/workspace.py:1-38,327-361`
  - `services/agent-api/agent_api/chat.py:97-130`
  - `services/agent-api/agent_api/config.py:1-末尾`
  - 新規 `services/agent-api/agent_api/git_workspace.py:1-末尾`
  - 新規 `services/agent-api/tests/test_git_workspace_security.py:1-末尾`
  - 新規 `services/vexa-agent/system/bin/vexa-git-bootstrap:1-末尾`
  - `services/vexa-agent/Dockerfile:9-14`
  - `deploy/compose/docker-compose.yml:175-209`
  - `deploy/lite/entrypoint.sh:98-104`
  - `deploy/lite/supervisord.conf:148-160`
- 問題: Agentのworkspace初期化もtokenを`https://<token>@host/...`へ埋め、shell文字列で`git clone && cp && rm`を実行する。remote config、process output、例外へtokenが残り、任意HTTPS originへのcredential送信とoutbound接続も可能である。
- 変更:
  - `git_clone_init()`のshell commandを削除し、host側の`asyncio.create_subprocess_exec("docker","exec","-i",container,"/system/bin/vexa-git-bootstrap","--repo",credential_free_url,"--branch",validated_branch)`だけを使う`git_workspace.py`へ置換する。repo/branch/target/tokenをshell文字列へ連結しない。workspaceはhelper内constant `/workspace`で、hostからpathを受けない。tokenはDocker execのstdinへ`uint32 big-endian byte length + exact UTF-8 bytes + EOF`の1 frameで1回だけ送り、0 byte/64KiB超/invalid UTF-8/trailing byteをhelperがclone前に拒否する。host/containerのargv/env、clone URL、remote URL、logへtokenを入れない。
  - `vexa-git-bootstrap`をAgent imageの`/system/bin/`へ0755でCOPYする。helperはstdin tokenをmemoryへ読み、session-private tmpfs `/run/vexa-git/<128-bit random>/`へ0700 directory、0600 token fileと0500 `GIT_ASKPASS`を作る。askpassはUsername promptへserver固定`x-access-token`、Password promptへtoken file内容だけを返し、その他のpromptを非0で拒否する。Git子process終了後`finally`でtoken/askpass/temp cloneを削除する。`GIT_TERMINAL_PROMPT=0`、`GIT_CONFIG_NOSYSTEM=1`、`http.followRedirects=false`、`protocol.allow=never`、`protocol.https.allow=always`を固定し、Git argvを`["git","clone","--branch",validated_branch,"--single-branch","--",credential_free_url,temp_dir]`へ固定する。成功後の`origin`もcredential-free URL exact。
  - URLは`https`、userinfo/fragmentなし、default port 443、server-side `AGENT_GIT_ALLOWED_HOSTS`のASCII lowercase exact hostだけを許す。allow-list未設定でworkspace git configが存在する場合はcontainer/Docker/Git副作用前にworkspace初期化を失敗させる。IP literal、localhost、single-label、解決した全A/AAAAのうち1件でもnon-global addressをclone前に拒否する。接続は検証済みIP集合へpinし、TLS SNI/証明書検証とHTTP Hostは元のallow-listed hostnameのままにする。retryは再解決・再検証・再pinし、redirectを全面禁止する。RF-06I2のhost-side egress policyも同じhost/IP/CIDR外をdenyする。
  - 既存workspaceをclone済みとして再利用する前に`.git/config`、worktree-local config、submodule configをregular file・no-symlinkでinventoryする。remote URLにuserinfo、credential helper/path、`http.*.extraHeader`、禁止scheme/hostが1件でもあればnetwork/Git前にworkspaceを`credential_quarantine`へ移し、値を表示せずkey名・remote host hashだけを返す。自動で安全化した体にせず、過去にURLへ入ったtokenはoperator rotation対象としてoperation evidenceへ件数だけ記録する。新規clone成功後も同じscrubberがcredential-free originと禁止config 0を確認してからworkspaceを公開する。
  - ComposeのNO-SHIP agent例とLite supervisorへ`AGENT_GIT_ALLOWED_HOSTS`を値変更なしで渡す。Lite entrypointは未設定時に空文字をexportするがdefault hostを追加しない。HelmにはAgent workload templateが存在しないため本項目で架空のHelm設定を追加せず、RF-51のownership catalogへ「Agent Helm wiringなし」を記録する。
  - branchはGit check-ref-format相当をpure validationし、先頭`-`、`..`、control/NUL、空、64 KiB超を拒否する。workspace/temp pathはserver constantだけで、user入力pathを受けない。clone/copy/cleanupのどれかが非0なら`False`、workspaceへpartial content 0、safe error codeだけを返す。
  - RF-03Aのinternal runtime configはtokenをAgent service memoryまで渡せるが、生成Agent containerの常駐env/config/inspectへは渡さない。本項目の短命stdin bootstrapだけを例外とし、RF-06E/06H後も同じsubject/egress契約を維持する。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-03D`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-03D`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。
  - `services/agent-api/tests/test_git_workspace_security.py::{test_clone_uses_installed_fixed_helper_argv_and_length_prefixed_stdin_without_shell,test_token_is_absent_from_url_argv_env_remote_log_and_exception,test_only_allowlisted_global_https_origin_and_valid_branch_reach_docker,test_connection_is_pinned_to_verified_ip_while_host_and_sni_remain_original,test_redirect_ip_literal_private_dns_userinfo_and_protocol_smuggling_are_rejected,test_empty_oversize_invalid_utf8_and_trailing_stdin_frames_are_rejected_before_git,test_failure_and_timeout_remove_temp_credentials_clone_and_partial_workspace,test_existing_credential_remote_extraheader_helper_and_submodule_are_quarantined_before_network,test_subject_workspace_config_cannot_select_another_subject_token,test_compose_and_lite_forward_allowlist_without_default_and_helm_adds_no_phantom_agent_workload}`
  - fake Docker/Git fixtureでmetacharacterを含むrepo/branch/tokenから追加process 0、token canaryはstdin frameだけexact 1件、全captured argv/env/url/remote/log/exceptionに0。
  - valid fixtureはcredential-free origin、branch、workspace content hash一致。invalid origin/branch/DNSはDocker/Git process 0。
  - `V-BACKEND`, `V-OPS`。
- リスクと戻し方: self-hosted Git originやredirect依存cloneが止まる。allow-listやredirectを広げてその場で救済せず、必要originとglobal addressを別の承認済みconfig変更として報告する。失敗branchを保持しRF-03CのSHAから再実行し、URLへ露出済みの可能性があるtokenはGit外でrotateする。
- 依存: RF-03A, RF-03C
- コミット: `RF-03D isolate agent git credentials from shell urls and arbitrary origins`

### RF-04A Dashboard browser-facing routeからservice tokenを除外

- 対象:
  - `services/dashboard/src/app/api/config/route.ts:1-末尾`
  - `services/dashboard/src/app/api/vexa/[...path]/route.ts:99-178`
  - `services/dashboard/src/app/api/auth/me/route.ts:1-56`
  - `services/dashboard/src/app/api/auth/verify/route.ts:185-217`
  - `services/dashboard/src/app/api/auth/[...nextauth]/route.ts:1-末尾`
  - `services/dashboard/src/app/api/auth/oauth-callback/route.ts:1-末尾`
  - `services/dashboard/src/app/api/auth/send-magic-link/route.ts:1-末尾`
  - `services/dashboard/src/app/api/auth/shared-login/route.ts:1-末尾`
  - `services/dashboard/src/stores/auth-store.ts:1-240`
  - `services/dashboard/src/app/auth/verify/page.tsx:36-101`
  - `services/dashboard/src/hooks/use-runtime-config.ts:1-末尾`
  - `services/dashboard/src/hooks/use-vexa-websocket.ts:1-末尾`
  - `services/dashboard/src/hooks/use-live-transcripts.ts:60-90`
  - `services/dashboard/src/app/meetings/[id]/page.tsx:118-140`
  - `services/dashboard/src/lib/direct-login.ts:1-末尾`
  - `services/dashboard/src/app/mcp/page.tsx:1-末尾`
  - `services/dashboard/src/components/mcp/mcp-config-button.tsx:1-末尾`
  - read-only既知一致: `services/dashboard/src/app/profile/page.tsx:171,179-180`のAPI key作成response一回表示だけ。RF-03C後の一覧・通常renderにraw token一致が残れば停止
  - read-only inventory: `git grep -n -E -e 'useAuthStore\(.*token|state\.token|data\.token|meData\.token|authToken' -- services/dashboard/src`。上記write対象と作成一回表示のread-only既知一致以外が1件でもあれば変更せずplan reviewへ戻る
- 問題: server-to-serverの`VEXA_API_KEY`をbrowser-readable JSONへ含め得るうえ、未認証`GET /api/vexa/meetings`が同service keyでprivate meeting一覧を代理取得する。
- 変更:
  - browser公開schemaのtop-level keyをexact `wsUrl,apiUrl,publicApiUrl,decisionListenerUrl,defaultBotName,brand,sharedAuth,hostedMode,webappUrl`だけに固定し、追加keyをschema testで拒否する。`wsUrl`はrequest originと同一originの`ws(s)://<request-host><basePath>/ws`、`apiUrl`は同一origin/basePath、`publicApiUrl`はRF-20の外部CDP表示だけに使う公開URLとする。`authToken`を含むcredential/token/key fieldとservice credential fallback `process.env.VEXA_API_KEY`を除き、空文字fieldとして残さない。
  - `/api/auth/me`、verify、shared/direct/OAuth login responseから`token` fieldを削除する。`AuthState`から`token/setToken`を除去し、`setAuth(user)`、`isAuthenticated=Boolean(user)`へ固定する。persist対象は`user,isAuthenticated,didLogout`だけで、localStorage/sessionStorageへtokenを一度も書かない。
  - Zustand persist versionを1つ上げ、React store hydrateや`checkAuth()`より前にserver-rendered bootstrap scriptがversioned/idempotent migrationを実行する。exact legacy keys`vexa-auth`と既知旧aliasをlocalStorage/sessionStorageから削除し、token/authToken fieldを含むIndexedDB database/object store、Cache Storage、service-worker cacheをallow-list inventory後に削除する。未知origin-wide dataを全消去せず、symlink相当のないbrowser storage APIだけを使う。migration成功markerはtokenを含まないversionだけで、失敗時はprotected UI/networkを開始せずlogoutへfail closedする。local user/isAuthenticatedを引継がず強制server re-authし、legacy tokenは別operator rolloutでrevokeする。
  - `authToken`も公開JSONから削除し、DashboardのHTTP/SSE/WebSocket consumerをcookieを読むsame-origin BFFへ切り替える。BFFだけがserver-sideでcookie tokenをGatewayへ付ける。MCP/API key管理等の外部client token契約は変更しない。
  - login/verify pageはresponseの公開userだけで`setAuth(user)`し、`checkAuth()`は`{authenticated:true,user}`だけで成功する。cookie 200でもuser不正ならlogout、network error時はlocal userを認証証拠にせず`isAuthenticated=false`。
  - `GET /api/vexa/meetings`も他routeと同じくcookie user token必須にし、欠落時401・upstream fetch 0。pre-login browsingの匿名公開DTO/tenantは本計画では新設しない。
  - responseへ `Cache-Control: private, no-store` と `Vary: Cookie` を付け、共有cacheへ認証状態を保存させない。
  - Dashboardの全browser-reachable routeでservice credentialを利用者credential fallbackに使わない。server-only deploy/admin automationは対象外だがbrowser requestから到達できないことをtestする。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-04A`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-04A`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/dashboard/tests/test_config_route.test.ts::{never_serializes_service_vexa_api_key,never_serializes_cookie_user_token,sets_private_no_store_and_vary_cookie,returns_only_documented_public_runtime_config_fields,ws_and_api_urls_are_same_origin_and_public_api_url_is_display_only}`
  - `services/dashboard/tests/test_vexa_proxy_auth.test.ts::{meetings_requires_cookie_user_token,missing_cookie_makes_zero_upstream_calls,service_key_is_never_a_browser_fallback}`
  - `services/dashboard/tests/test_cookie_only_auth_state.test.ts::{auth_responses_never_return_cookie_token,auth_store_never_persists_token,pre_hydration_migration_removes_every_known_legacy_local_session_indexeddb_cache_and_service_worker_token_shape,migration_failure_starts_no_protected_network_and_forces_logout,verify_and_shared_login_use_public_user_only,network_failure_does_not_trust_local_user,live_http_sse_and_websocket_use_same_origin_routes}`
  - service canaryを設定してcookieなしで全BFFを叩き、upstream call 0。clean browserへ全legacy storage shapeをseedし、migration後のlocal/session/IndexedDB/Cache/DOM/logにcanary 0、server auth前のprotected request 0。
  - `rg -n 'userToken\s*\|\|\s*process\.env\.VEXA_API_KEY|authToken|state\.token|meData\.token|data\.token' services/dashboard/src` はAPI key作成一回表示fixture以外0件。
  - `V-DASH`。lintはbaseline以下、新規0。
- リスクと戻し方: pre-login一覧と直接WebSocket接続が止まる。service key fallbackやraw token JSONを戻さず、同一origin proxyのmethod/status/stream互換をcharacterization testへ追加する。失敗branchを保持しRF-03CのSHAから再実行し、露出した可能性のあるservice/user tokenは別運用でrotateする。
- 依存: RF-03C, RF-00C
- コミット: `RF-04A keep browser and service credentials separated`

### RF-04B Admin session cookieを単一の署名検証実装へ統合

- 対象:
  - `services/dashboard/src/app/api/auth/admin-verify/route.ts:1-136`
  - `services/dashboard/src/app/api/admin/[...path]/route.ts:1-149`
  - 新規 `services/dashboard/src/lib/server/admin-session.ts:1-末尾`
  - 新規 `services/dashboard/tests/test_admin_session_auth.test.ts:1-末尾`
- 問題: admin verify GETはHMACを検証する一方、全権Admin proxyはcookie全体をbase64 decodeするだけ。攻撃者がunsigned JSONを自作して`VEXA_ADMIN_API_KEY`付きproxyを利用できる。
- 変更:
  - server-only `admin-session.ts`へ`signAdminSession()`と`verifyAdminSession()`を1実装だけ置く。cookie wire formatを`v1.<base64url-payload>.<64桁hex HMAC-SHA256>`へ固定し、payloadは `{v:1,authenticated:true,iat:number,exp:number}` 以外をrejectする。
  - verify順は`segment数/文字集合/長さ -> HMAC再計算 -> signature Buffer長一致 -> timingSafeEqual -> JSON/schema -> authenticated -> iat<=now+60s -> exp>now -> exp-iat<=24h`。比較前に長さ不一致をrejectし、例外を認証成功へ変換しない。
  - POSTのAdmin token比較も同長Bufferだけconstant-time比較する。missing secretは503でcookie/fetch 0、secretやcookieをlogしない。
  - admin verify GETとadmin proxyの全methodが同helperだけを呼ぶ。invalid/tampered/unsigned/expired/future-issued/wrong-secret/malformed cookieは401、Admin upstream fetch 0。valid cookieは既存method/path/query/body/status/content-typeを維持する。
  - cookie属性は`HttpOnly`、production/HTTPSで`Secure`、`SameSite=Strict`、`Path=/`、`Max-Age=86400`。全認証responseは`Cache-Control: no-store`。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-04B`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-04B`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/dashboard/tests/test_admin_session_auth.test.ts::{rejects_unsigned_forged_cookie,rejects_tampered_expired_future_wrong_secret_and_bad_length,accepts_valid_signed_cookie_for_all_proxy_methods,invalid_cookie_never_fetches_admin,uses_strict_cookie_attributes}`
  - unsigned forged fixtureは現実装でproxy到達することをfix前red testで確認し、fix後401/Administrative fetch 0。
  - `rg -n 'Buffer\.from\(sessionCookie\.value,\s*"base64"\)|function verifyAdminSession' services/dashboard/src/app/api/admin services/dashboard/src/app/api/auth/admin-verify` は0件。
  - `V-DASH`。
- リスクと戻し方: 既存admin session cookieはwire format変更で一度ログアウトになる。旧unsigned decoderをdual acceptせず、401後の再ログインだけを許す。失敗branchを保持しRF-04AのSHAから再実行する。
- 依存: RF-04A
- コミット: `RF-04B verify every admin session before privileged proxying`

### RF-05A Gateway route policyとtrusted identity envelopeをfail-closed化

- 対象:
  - `services/api-gateway/main.py:88-100,321-419,691-1710,2432-2520`
  - `services/meeting-api/meeting_api/meetings.py:1-末尾`
  - `services/meeting-api/meeting_api/callbacks.py:1094-1165`
  - `services/meeting-api/meeting_api/auth.py:1-54`
  - `services/calendar-service/app/main.py:85-182`
  - `services/calendar-service/app/sync.py:70-79`
  - `services/agent-api/agent_api/auth.py:1-29`
  - `services/agent-api/agent_api/main.py:243-475`
  - `services/admin-api/app/main.py:1-末尾`
  - `services/runtime-api/runtime_api/config.py:1-末尾`
  - `services/mcp/main.py:1-末尾`
  - `services/transcription-service/main.py:1-末尾`
  - `services/tts-service/main.py:1-末尾`
  - `services/voiceprint-service/main.py:1-末尾`
  - `services/wake-stt/app/main.py:1-末尾`
  - `services/wake-orchestrator/app/main.py:1-末尾`
  - `deploy/compose/docker-compose.yml:87-164,216-300,412-429`
  - `deploy/lite/entrypoint.sh:1-398`
  - `deploy/lite/supervisord.conf:95-211`
  - `deploy/helm/charts/vexa/values.yaml:1-584`
  - `deploy/helm/charts/vexa/templates/secret.yaml:1-30`
  - `deploy/helm/charts/vexa/templates/deployment-{api-gateway,admin-api,meeting-api,mcp}.yaml:1-末尾`
  - `deploy/helm/charts/vexa-lite/values.yaml:1-末尾`
  - `deploy/helm/charts/vexa-lite/templates/secret.yaml:1-末尾`
  - `deploy/helm/charts/vexa-lite/templates/deployment.yaml:1-末尾`
  - `deploy/helm/charts/vexa-lite/templates/dashboard-deployment.yaml:1-末尾`
  - 新規 `services/api-gateway/tests/test_route_policy.py:1-末尾`
  - 新規 `services/calendar-service/tests/test_scheduler_gateway_assertion.py:1-末尾`
  - 新規 `services/api-gateway/tests/test_admin_mcp_identity.py:1-末尾`
  - 新規 `services/admin-api/tests/test_trusted_identity.py:1-末尾`
  - 新規 `services/mcp/tests/test_trusted_identity.py:1-末尾`
  - 新規 `services/api-gateway/tests/test_synthetic_route_registration.py:1-末尾`
  - 新規 `services/api-gateway/tests/test_transcript_share_identity.py:1-末尾`
  - 新規 `packages/security-contracts/identity-envelope-v1.json:1-末尾`
  - 新規 `scripts/migrations/migrate_transcript_shares.py:1-末尾`
  - 新規 `tests3/unit/refactor/test_vexa_env_modes.py:1-末尾`
  - 新規 `tests3/unit/refactor/test_rf_05a.py:1-末尾`
- 問題: route policyがCalendar/recording/Agent/MCPを網羅せずoptional credentialでupstreamへ到達できる。下流も未署名`X-User-ID`やbody/query主体を信頼し、secret未設定時にopenになる。
- 変更:
  - `RoutePolicy(method,path/prefix,auth_mode,scopes,downstream)`を単一tableへ固定し、exact/長いprefix優先で一意解決する。全HTTP/WS routeが1件だけに分類され、未分類/複数一致はstartup/test failure。
  - scopeは `/user,/calendar=bot`、`/bots=bot|browser`、meeting/transcript/recording/speaker/voiceprint=`tx`、Agent chat/session/workspace/schedule/container=`browser`、`/mcp`はvalid user token + tool-level scope、`/b/{token}`はbrowser-session tokenとする。新scopeを作らない。
  - clientの`x-user-*`/`x-internal-secret`を除去する。Gateway→Admin token検証には専用`GATEWAY_ADMIN_VALIDATE_SECRET`、Gateway→Meeting/Calendar/Agent/Admin/MCP identityには下流別`GATEWAY_MEETING_IDENTITY_SECRET`、`GATEWAY_CALENDAR_IDENTITY_SECRET`、`GATEWAY_AGENT_IDENTITY_SECRET`、`GATEWAY_ADMIN_IDENTITY_SECRET`、`GATEWAY_MCP_IDENTITY_SECRET`を使う。MCP→Gateway tool dispatchは別の`MCP_GATEWAY_ASSERTION_SECRET`、Meeting→Admin webhook deliveryは`MEETING_ADMIN_WEBHOOK_IDENTITY_SECRET`を使い、全secretの相互非同値をstartupで強制する。既存`INTERNAL_API_SECRET`や別audienceのsecretを使い回さない。
  - Adminでtokenを解決後、対象下流へだけ`X-Vexa-Trusted-Identity`のcanonical JSONと`X-Vexa-Identity-Signature`を注入する。envelopeは`v=1,sub,scopes,limits,aud,method,route_policy_id,route_template,canonical_path_params,normalized_path,canonical_query_sha256,body_sha256,content_length,iat,exp,jti`を持ち、audは下流service exact、TTL 30秒、clock skew 5秒。pathはASGI `raw_path` byteを1回だけpercent decodeし、invalid/truncated percent、NUL/control、invalid UTF-8、backslash、encoded slash/backslash、dot segment、double-encoding候補をroute解決前に400でrejectする。許可byteだけをRFC3986 uppercase percent-encodingへ戻し、route tableが解決したpolicy ID/templateとtyped path parameterのcanonical JSONも署名する。Gateway・各下流・upstream adapterは同じ`packages/security-contracts/identity-envelope-v1.json` adversarial vectorを使い、downstream actual route ID/template/path params/normalized pathとexact照合する。queryはraw queryを左から1回だけparseし、`+`をspaceへ暗黙変換せずRFC3986 percent decodeした**順序付き(name,value) pair列**をその順番のままcanonical percent-encodingする。値sort、同名pair sort、dict化をしない。route policyは各query名のcardinalityを`zero-or-one|repeatable`で宣言し、`api_key,token,user_id,meeting_id,session_id,container_id`等のcredential/subject/resource選択paramは全て`zero-or-one`、重複時は値が同じでも400・upstream 0。empty name/value、`+`対`%20`、percent文字大小、mixed encoding、repeatable pair順をcross-language vectorで固定する。bodyなしは空byte SHA-256、content length不一致はreject。WSはGET handshake path/queryへbindする。
  - 各下流にimmutable `TrustedIdentity` dependencyを作り、自service向けsecretで署名検証した後、actual method/path/query/body digest/lengthとconstant-time照合した場合だけidentityを受理する。state-changing methodは副作用前に`SET identity-jti:<aud>:<jti> 1 NX EX <remaining-ttl>`を原子的実行し、重複/100並列replayは409・handler/DB call 0。Gateway retryはattemptごとにnew jtiをmintし、業務idempotency keyとは混ぜない。GET/WSもroute/digest bindは必須。wrong audience/expired/future/duplicate headerを拒否し、legacy body/query `user_id`は一致時だけ受理して捨て、不一致403。
  - `/user/*`の通常Admin proxyと`/mcp`/`/mcp/*`はAdmin token introspection後にraw `Authorization,X-API-Key`を除去し、それぞれ`aud=admin-api|mcp` envelopeだけを送る。Admin/MCPはraw client tokenをrequest context、tool state、logへ保持しない。唯一のraw token trusted-boundary例外はGateway→Admin固定`/internal/validate`であり、response/cache/logへ値を残さず通常proxyへ流用しない。
  - MCP toolがGatewayへ戻る経路は`MCP_GATEWAY_ASSERTION_SECRET`で`sub,tool_name,operation,method,route_policy_id,normalized_path,canonical_query_sha256,body_sha256,iat,exp<=30,jti`を署名し、Gatewayの固定internal MCP dispatch tableへだけ送る。unknown tool/path、subject/method/path/body/audience mismatch、100並列replayはGateway upstream/DB call 0。MCPへraw user tokenを渡すfallbackや全route catch-allを作らない。
  - Meetingのwebhook deliveryは`MEETING_ADMIN_WEBHOOK_IDENTITY_SECRET`でRF-03Bのexact claimを署名し、Admin brokerだけが受ける。Gateway/Admin/MCP/他serviceへこのsecretを配らず、Admin brokerからMeetingへraw secretを返さない。
  - Calendar background syncはMeetingへ直結しない。`CALENDAR_SCHEDULER_ASSERTION_SECRET`をCalendar issuer+Gateway verifierだけへ配り、固定`POST /internal/calendar/bots`へ`sub=user_id,event_id,operation=bots.create,method,path,body_sha256,iat,exp<=30s,jti`を送る。Gatewayがrequest bind/one-time jtiを検証後に通常のGateway→Meeting identityをmintする。Calendarから`BOT_API_TOKEN,X-User-*`とMeeting URL直結を削除し、single-account/per-userの既存bot request goldenを維持する。
  - public transcript share recordからraw利用者API keyを除去する。Redis valueは`share_id_hash,resolved_user_id,creator_token_id,meeting_id,platform,native_id,exp,status`のexact allow-listだけで、`share-by-token:<creator_token_id>` indexを持つ。token DELETE transactionはindex内shareを全revokeしてからtokenを無効化する。公開取得routeはshare hash/active/expiryを確認後、`operation=transcript.share.read`のGateway→Meeting trusted identityをmintし、Meetingがrecord subject/meeting ownership一致を確認してtranscriptを返す。creator token revoke、share revoke、expiry後はMeeting/DB call 0。recordのmeeting/user改ざんは403、raw API keyをRedis/log/job/responseへ0。
  - 既存raw-token shareはreverse index/DB inventoryを持たないため、架空indexを前提にしない。R1 deploy前に旧share作成経路をfreezeし、一時Redis principal `migrate-transcript-shares`をCSPRNG passwordで作る。権限はkey `~share:transcript:* ~share-by-token:*`、commands `SCAN,GET,SET,DEL,PTTL,EXPIRE,SADD,SMEMBERS,WATCH,UNWATCH,MULTI,EXEC`だけ。`KEYS`、broad key/category、他namespaceを常時禁止する。
  - migrationは`SCAN MATCH share:transcript:* COUNT 100`をcursor=0まで実行し、最大100万key/1万cursor/15分を超えたら旧recordを削除せず中断する。最初のkey inventoryは0700 operator temp directoryの0600 fileへだけ保存し、evidence/stdoutへ出さずfinally削除する。各recordのraw tokenはmemory内だけでAdminへresolveし、owner/token ID/meeting一致ならremaining PTTLを保持してWATCH/MULTIでsanitized record+reverse indexを書いてraw fieldを削除する。invalid/ambiguous/revoked/conflictはshare revoke、scan後に消えたkeyは`expired_during_migration`へ分類する。retryはsanitized recordをbyte不変でskipし、second passでlegacy raw field 0を確認する。
  - `migrated+revoked+expired_during_migration+already_sanitized=inventory`を値なしで記録した後、一時principal/password/Secret ref/SCAN grantを削除する。この削除とold-value canary rejectが完了するまでOP-05Aをpassさせない。露出した旧tokenはOP-05Aでrotate/revokeする。
  - runtime modeは`VEXA_ENV=production|development|test`の3値を必須にし、unset/空/unknownは全service startup failureにする。Compose/Lite/Helmの通常profileはliteral `production`、test overlayだけliteral `test`を設定する。synthetic routeは`VEXA_ENV=test`かつ`ENABLE_SYNTHETIC_RIG=true`かつ32-byte以上の専用`SYNTHETIC_RIG_SECRET`が揃う場合だけ条件登録し、developmentでは登録しない。test時もloopback-only fixture bindingと専用header secretを要求し、missing/wrongは403、Meeting/downstream/DB/state call 0。production/unset/unknownではGateway `/bots/internal/test/*`, `/bots/internal/callback/*`とMeeting synthetic lifecycle/state callbackのroute inventory自体0または404で、副作用0。
  - このRF-05A commit自身で通常Compose/Lite/Helm `vexa`/Helm `vexa-lite`の全server processへliteral `VEXA_ENV=production`、test overlayだけ`test`を配る。上記Gateway identity、Calendar/Telegram assertion、OAuth registry encryption、Webhook broker、MCP reverse assertionのcurrent/optional previous Secret refをissuer/verifier exact pairへ配線し、RF-05Fへ先送りしない。RF-05Fは値のdefault廃止・preflight・rotation hardeningを担当する。render manifestへ`verifier→issuer→canary`順を保存し、OP-05Aの各success counterが実render refへbindしない限り進まない。
  - `vexa-lite`もR1 secret inventoryに含め、全Secret `envFrom`で偶然受け取らせない。各processは明示`secretKeyRef`またはservice別projected fileだけを受け、Dashboard deploymentへAdmin token/identity signing secret 0。この項目では既存securityContextを変更せず、RF-05F/06I3の対象から漏らさない。
  - Meeting standalone `API_KEYS`とAgent direct `API_KEY`はこのcommit中だけ互換で残すが、空ならstartup failure。standaloneはclient `X-User-ID`を信頼しない。RF-05CでAgent direct static authを閉じる。
  - rolling順は downstream verifierを先にdual-accept配置→Gateway signer配置→signed request canary→legacy unsigned identity close。Gatewayを先に配備しない。rollbackはlegacy close前ならGateway signer→downstream verifierの逆順、close後にunsigned trustを復活させずtaskを未完で停止する。

```python
identity = await require_route_identity(request, policy)
return await proxy(identity=identity, sanitized_headers=strip_identity_headers(request))
```

- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-05A`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-05A`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/api-gateway/tests/test_route_policy.py::{test_every_route_resolves_exactly_one_policy,test_missing_invalid_and_scope_failure_never_calls_upstream,test_cross_audience_route_body_query_and_method_replay_is_rejected,test_raw_path_canonicalization_rejects_encoded_slash_backslash_dot_double_decode_nul_and_invalid_utf8_before_upstream,test_route_policy_template_and_canonical_path_params_are_cross_language_exact,test_query_canonicalization_preserves_order_plus_empty_and_percent_encoding,test_duplicate_singleton_security_subject_and_resource_params_are_rejected_before_upstream,test_state_change_jti_parallel_replay_has_exactly_one_side_effect}`
  - `services/calendar-service/tests/test_scheduler_gateway_assertion.py::{test_single_account_and_per_user_sync_use_fixed_gateway_route,test_wrong_user_event_audience_route_and_replay_never_call_meeting,test_calendar_never_receives_gateway_identity_or_meeting_token_secret}`
  - `tests3/unit/refactor/test_rf_05a.py::{test_direct_service_rejects_spoofed_user_and_cross_subject_body,test_identity_secret_and_calendar_assertion_secret_distribution_are_exact,test_missing_production_secret_fails_startup}`
  - `services/api-gateway/tests/test_admin_mcp_identity.py::{test_user_admin_proxy_strips_raw_token_and_signs_admin_identity,test_mcp_proxy_strips_authorization_and_x_api_key_and_signs_mcp_identity,test_mcp_reverse_dispatch_is_tool_method_path_body_subject_bound_and_one_time}`
  - `services/admin-api/tests/test_trusted_identity.py::test_admin_audience_and_jti_are_verified_before_db`
  - `services/mcp/tests/test_trusted_identity.py::{test_mcp_requires_mcp_audience_and_never_receives_raw_token,test_tool_calls_use_reverse_assertion_without_client_token}`
  - `tests3/unit/refactor/test_rf_05a.py::{test_admin_mcp_webhook_and_reverse_assertion_secret_distribution_are_exact,test_r1_compose_lite_vexa_and_vexa_lite_render_all_active_identity_refs,test_every_r1_server_has_literal_production_mode_and_only_test_overlay_has_test,test_op05a_paths_are_backed_by_rendered_secrets_not_future_rf05f_wiring}`
  - `services/api-gateway/tests/test_synthetic_route_registration.py::{test_production_development_unset_and_unknown_modes_never_register_synthetic_routes,test_test_mode_requires_enable_flag_dedicated_secret_and_loopback_binding,test_wrong_rig_secret_has_zero_downstream_or_state_side_effects,test_test_overlay_serves_existing_synthetic_fixture_contract}`
  - `tests3/unit/refactor/test_vexa_env_modes.py::{test_every_server_process_rejects_unset_empty_and_unknown_vexa_env_before_listen,test_production_and_development_never_enable_test_or_docs_routes,test_compose_lite_helm_set_production_and_only_test_overlay_sets_test}`
  - `services/api-gateway/tests/test_transcript_share_identity.py::{test_share_record_never_contains_raw_user_api_key,test_temporary_scan_principal_migrates_real_unindexed_legacy_namespace,test_migration_preserves_remaining_ttl_and_reverse_index,test_crash_retry_parallel_and_expiry_classification_are_exact,test_temporary_principal_and_scan_grant_are_removed_before_gate,test_keys_command_and_raw_value_logging_are_never_used,test_public_share_mints_only_share_read_identity_for_record_owner_and_meeting,test_revoked_expired_or_tampered_share_has_zero_meeting_and_database_calls,test_fresh_share_preserves_public_transcript_contract}`
  - route inventory全件一意、Calendar=`bot`、recordings=`tx`、Agent=`browser`。
  - spoof header除去、missing/invalid/scope不足でupstream 0。Meeting identityをCalendar/Agentへreplay、Gateway-Admin secretを下流へ使用、既存internal secretをidentityへ使用するfixtureは全て403・副作用0。
  - 直接serviceへ偽`X-User-ID`は403、A token+B body/queryは403かつ副作用0、一致legacy入力は成功。
  - RF-00Bのstrict xfail `services/api-gateway/tests/test_route_inventory_characterization.py::test_every_current_route_is_observed[unclassified-policy]` だけをmarker削除して通常passへ変更し、RF-00B matrix entryは変更しない。
  - Admin/Gateway/Meeting/Runtime/Calendar/Agent/MCP/Transcription/TTS/Voiceprint/Wake STT/Wake Orchestratorのparameterized subprocess/import fixtureで`VEXA_ENV` unset/empty/unknownはlisten/background task/provider load前にnon-zero、`production|development|test`だけmode parse成功。productionではsynthetic/docs/debug route inventory 0。
  - production相当envでinternal secret/standalone key欠落はstartup failure。
  - 通常Compose/Lite/Helmは`VEXA_ENV=production`、test overlayだけ`VEXA_ENV=test`+enable flag+専用secret。production/development/unset/unknownのsynthetic route count 0。
  - `V-BACKEND`。
- リスクと戻し方: 未分類routeや旧direct clientが止まる。default allowを追加せずroute/scope不明なら中断。rolloutはdownstream verifier→Gateway signer→legacy close、rollbackはclose前だけ逆順。
- 依存: RF-03B, RF-00B
- コミット: `RF-05A enforce explicit route policy and trusted identities`

### RF-05B Agent公開routeを完成させ外部consumerをGatewayへ移行

- 対象:
  - `services/agent-api/agent_api/main.py:243-510`
  - `services/api-gateway/main.py:1565-1710`
  - `services/dashboard/src/app/api/agent/[...path]/route.ts:1-95`
  - `services/telegram-bot/bot.py:45-46,270-300,452-796`
  - 新規 `scripts/migrations/migrate_telegram_mappings.py:1-末尾`
  - 新規 `services/telegram-bot/tests/test_linking.py:1-末尾`
  - 新規 `services/api-gateway/tests/test_telegram_linking.py:1-末尾`
  - `packages/vexa-cli/vexa_cli/{client,config,main}.py:1-末尾`
  - 新規 `packages/vexa-cli/tests/test_public_routes.py:1-末尾`
  - 新規 `services/agent-api/tests/test_public_agent_routes.py:1-末尾`
  - 新規 `services/api-gateway/tests/test_agent_route_inventory.py:1-末尾`
  - 新規 `services/telegram-bot/tests/test_agent_gateway_routing.py:1-末尾`
  - 新規 `services/telegram-bot/tests/test_gateway_assertion.py:1-末尾`
  - 新規 `services/dashboard/tests/test_agent_gateway_route.test.ts:1-末尾`
- 問題: workspace/schedule/containerはdirect Agent/Runtimeまたは無認証internal routeへ依存し、Dashboard/Telegramは全利用者共通Agent tokenを使う。
- 変更:
  - subject-bound public routeとしてAgent health、workspace save/status/files、workspaces、schedule、container list/get/delete/CDPを追加し、resolved subject所有containerだけを扱う。
  - Gatewayへ上記exact method/pathを`browser` scopeで列挙し、unknown `/api/*` catch-allを作らない。SSEだけstream proxy。
  - Dashboard BFFをcookie user token→Gatewayに変更し、`AGENT_API_URL/TOKEN`、hard-coded token、存在しない`body.bot_token`注入を削除する。
  - Telegram全Agent操作をGatewayへ統一し、次項のservice assertionからresolved subjectを得る。利用者mapping不明時はAdmin user/tokenを自動作成せず、network前に日本語の連携案内を返す。
  - Telegramはfull user tokenを`telegram:{tg_id}`へ保存しない。Redisには`telegram_user_id -> resolved_user_id`だけを保存し、`TELEGRAM_GATEWAY_ASSERTION_SECRET`をTelegram issuer+Gateway verifierだけへ配る。各requestは`sub=resolved_user_id,tg_id,operation,method,normalized_path,body_sha256,iat,exp<=30s,jti`を署名し、Gatewayがroute bind/one-time jtiを検証した後だけ通常のsubject identityへ変換する。A assertion+B user/path/body replayは403・upstream 0。raw user tokenをRedis/log/responseへ0。
  - 新規linkは認証subject専用`POST /user/telegram/link-codes`で256-bit one-time codeを1回だけ返す。GatewayはRedis `telegram-link:<sha256(code)>`へsubject/iat/expを`SET NX EX 600`し、raw code/tokenを保存しない。Telegram `/link <code>`はTelegram update由来tg_idとcodeをmethod/path/body/jti-bound assertionでGatewayへ送り、GatewayがWATCH/MULTI/EXECでcodeを一度だけconsumeして`telegram-user-map:<tg_id>=resolved_user_id`を作る。別subjectの既存mappingは上書きせず409。通常requestはmapping subjectとassertion subをexact照合し、Telegram permanent principalはmapping GETだけ、link/map writeはGatewayだけが所有する。
  - existing Telegram mappingにはbounded known-user indexが存在しないため、一時principal `migrate-telegram-map`をkey `~telegram:* ~telegram-user-map:*`、commands `SCAN,GET,SET,DEL,PTTL,EXPIRE,WATCH,UNWATCH,MULTI,EXEC`だけで作る。旧writer freeze後、`SCAN MATCH telegram:* COUNT 100`をcursor=0まで実行し、最大100万key/1万cursor/15分を超えたら削除せず中断する。raw `user_id:token`はmemory内だけでAdmin resolveしprefix user ID一致時だけnew mappingを書いてold keyを削除、不正/revoked/conflictはmapping revokeする。second passでlegacy/raw value 0を確認後、一時principal/password/Secret ref/SCAN grantを削除する。migration/retry/parallel中もnew requestはsanitized mappingだけを使い、raw tokenを再保存しない。露出tokenはOP-05Aでrotate/revokeする。
  - `packages/vexa-cli` defaultを`http://localhost:8056` Gatewayへし、chat/session/workspace/statusをpublic `/api/*`へ向け、`/internal/`を使わない。
  - 旧internal routeはRF-05Cまで残す。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-05B`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-05B`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/agent-api/tests/test_public_agent_routes.py::{test_subject_owned_workspace_and_container_routes,test_cross_subject_container_is_not_found}`
  - `services/api-gateway/tests/test_agent_route_inventory.py::{test_agent_route_method_scope_inventory,test_sse_headers_and_status_preserved}`
  - `services/telegram-bot/tests/test_agent_gateway_routing.py::test_agent_calls_use_gateway_and_chat_user_token`
  - `services/telegram-bot/tests/test_gateway_assertion.py::{test_redis_mapping_contains_user_id_but_never_raw_token,test_temporary_scan_migration_is_bounded_value_redacted_and_retry_safe,test_every_request_uses_short_lived_method_path_body_bound_assertion,test_cross_user_route_body_and_replay_are_rejected_before_upstream,test_existing_telegram_command_golden_is_preserved}`
  - `services/telegram-bot/tests/test_linking.py::{test_unlinked_user_gets_link_instructions_without_admin_or_gateway_business_call,test_one_time_link_creates_id_only_mapping,test_link_code_and_user_token_never_enter_log_or_redis_value}`
  - `services/api-gateway/tests/test_telegram_linking.py::{test_link_code_is_subject_bound_one_time_and_mapping_cannot_be_overwritten,test_mapping_and_assertion_subject_must_match_before_upstream,test_temporary_scan_principal_migrates_all_legacy_keys_then_is_revoked_and_is_retry_safe}`
  - `packages/vexa-cli/tests/test_public_routes.py::test_all_agent_commands_use_public_gateway_routes`
  - `services/dashboard/tests/test_agent_gateway_route.test.ts::test_agent_bff_uses_cookie_user_token_only`
  - A token+B user/containerで403/404、side effect 0。全consumerのhostはGatewayだけ。
  - `rg -n 'AGENT_API_TOKEN|body\.bot_token|/internal/' services/dashboard/src/app/api/agent services/telegram-bot/bot.py packages/vexa-cli`はfixture以外0。`rg -n 'create.*user|create.*token|/admin/users|/admin/tokens' services/telegram-bot`はnegative test以外0。
  - `V-BACKEND`, `V-DASH`, `V-INTEGRATIONS`, `V-CLIENTS`。
- リスクと戻し方: proxy path/method/SSE header差。既存snapshotを先に固定し、rollout Agent→Gateway→consumer、rollback逆順。
- 依存: RF-05A, RF-03A, RF-00C
- コミット: `RF-05B publish subject-bound agent routes before closing internals`

#### OP-05A-DRAIN（非commit・人間operator停止点）

RF-05A/RF-05Bをdownstream verifier→Gateway signer/routes→既に移行済みconsumerの順で実配備できる認可済みoperatorが、共通`operation-gate-v1`で`measurements={max_identity_ttl_seconds:30,max_client_retry_window_seconds,legacy_unsigned_identity_count:0,signed_identity_success_by_path:{meeting:>0,agent:>0,calendar:>0,admin:>0,mcp:>0},calendar_gateway_assertion_success_count:>0,transcript_share_new_mapping_success_count:>0,telegram_new_mapping_success_count:>0,webhook_ref_delivery_success_count:>0,legacy_raw_share_record_count:0,legacy_raw_telegram_mapping_count:0,meeting_raw_webhook_secret_field_count:0,webhook_retry_raw_secret_count:0,gateway_webhook_secret_cache_count:0,share_inventory_count,share_migration_count,share_revoke_count,share_expired_during_migration_count,share_already_sanitized_count,telegram_inventory_count,telegram_migration_count,telegram_revoke_count,telegram_expired_during_migration_count,webhook_legacy_inventory_count,webhook_migrated_count,webhook_revoked_count,webhook_expired_count,temporary_migration_principal_count:0,temporary_scan_secret_ref_count:0,temporary_scan_grant_count:0,dashboard_minimum_source_sha,active_old_dashboard_client_count:0}`を提供する。enabled pathは各path個別に成功`>0`で、aggregate値を代用しない。`share_migration_count+share_revoke_count+share_expired_during_migration_count+share_already_sanitized_count=share_inventory_count`、`telegram_migration_count+telegram_revoke_count+telegram_expired_during_migration_count=telegram_inventory_count`、`webhook_migrated_count+webhook_revoked_count+webhook_expired_count=webhook_legacy_inventory_count`をexact一致させ、露出token/旧Dashboard local tokenをrotate/revokeしてold-value canary reject、legacy storage/ref 0を記録する。`KEYS`使用回数は常時0で、一時`SCAN`は2 migration principalのbounded runだけに限定しgate前にprincipal/password/ref/grantを全削除する。RF-05C未配備のworkload CLI/Agent direct/Meeting managed static pathはこのgateの0条件やcredential revoke対象に含めない。観測時間は`max(max_identity_ttl_seconds,max_client_retry_window_seconds,active client cache TTL)+300`秒以上。`bash scripts/test/run-refactor-operator-gate.sh --master-task full-repo-refactoring-2026-07-24 --archive-root "$CONTROL_ROOT/.pipeline/release-archives" --release r1 --gate op-05a-drain`がexit 0になるまでRF-05Cを開始しない。

### RF-05C Workload CLIをGateway優先へ互換移行

- 対象:
  - `services/vexa-agent/system/bin/vexa:1-870`
  - `services/vexa-agent/system/README.md:1-末尾`
  - `services/vexa-agent/Dockerfile:1-26`
  - `services/agent-api/agent_api/main.py:1-末尾`
  - `services/agent-api/agent_api/auth.py:1-末尾`
  - `services/agent-api/agent_api/config.py:1-末尾`
  - `services/agent-api/agent_api/container_manager.py:1-末尾`
  - `services/agent-api/agent_api/workspace.py:293-323`
  - `services/agent-api/agent_api/chat.py:127-130`
  - `services/meeting-api/meeting_api/config.py:1-末尾`
  - `services/meeting-api/meeting_api/meetings.py:790-835`
  - `services/runtime-api/profiles.yaml:48-78`
  - read-only inventory: `git grep -n -E -e '/internal/(agent|runtime)|AGENT_API_URL|RUNTIME_API_URL' -- services/meeting-api services/runtime-api`。write対象外production/config callerが1件でもあれば停止してplan reviewへ戻る
  - 新規 `services/vexa-agent/tests/test_vexa_public_routing.sh:1-末尾`
  - 新規 `services/agent-api/tests/test_internal_auth.py:1-末尾`
  - 新規 `services/agent-api/tests/test_no_direct_static_auth.py:1-末尾`
  - 新規 `services/meeting-api/tests/test_managed_standalone_auth_modes.py:1-末尾`
  - `services/runtime-api/tests/test_profiles.py:1-末尾`
  - 新規 `services/agent-api/tests/test_workspace_initialization.py:1-末尾`
- 問題: workload CLIがAgent/Runtime/collectorのinternal routeへ直結し、利用者操作可能containerへglobal internal secretが入り得る。Agent internal authもfail-open。
- 変更:
  - CLI baseを`VEXA_API_URL`（default `http://api-gateway:8000`）へ統一し、既存user-scoped `VEXA_BOT_API_TOKEN`を全requestの`X-API-Key`へ付ける。new configがあるworkloadはGatewayだけを使い、失敗時にinternalへfallbackしない。旧image/sessionだけは従来internal route/static credentialをR2 compatibilityとして使い、caller/path別legacy counterを値なしで記録する。
  - workspace/schedule/container/meeting/recordingはRF-05B/既存Gateway公開routeへ、transcriptはまずowned meetingを取得して公開transcript routeへ送る。`VEXA_AGENT_API`, `VEXA_RUNTIME_API`, `VEXA_MEETING_API`, `VEXA_TC`, `INTERNAL_API_SECRET`参照をCLIから削除する。
  - service-wide Admin/Internal/Runtime secretをcontainerへ渡さない。利用者token provisionが無ければfail closed。
  - browser-session `BOT_CONFIG.internalSecret`を削除する。exit callback用service secretはRuntime service processの`callback_headers`だけに残し、browser container env/configへ入れない。
  - Agent internal chat/workspace/webhook endpointへ共通`require_internal_caller`を付け、secret未設定startup failure、missing/wrong 403。R2では正規旧Meeting/Runtime callerだけservice process secretを付けて一時受理しcounter化する。new consumerはpublic Gateway routeを優先する。
  - Agent public `/api/*`はRF-05A identityを優先し、旧image/session用static direct authをcounter付きcompatibilityとして残す。browser/agent profileのnew generationからglobal internal/runtime/admin tokenを除くが、旧generation rollback Secret refはOP-05D-DRAINまで保持する。
  - Meeting authを`DEPLOYMENT_MODE=managed|standalone`のdiscriminated configへする。managedはRF-05A signed identityを優先し、R2だけ旧managed static keyをstrict counter付きfallbackとして受理する。standaloneは明示mode+32-byte以上の専用standalone keyだけを受理し、Gateway identity headerとのdual modeを拒否する。new managed profileはstatic ref 0、旧generation refはOP-05Dまでのcompatibility inventoryへ限定する。
  - Agent Dockerfileの不存在`features/knowledge-workspace/templates/knowledge/` COPYを削除し、削除済み`features/`を復元しない。repo設定がなくlegacy templateも存在しない初回workspaceは空directoryを`git init`し、結果を`source="empty_git_repository", template_applied=false`として返す。template適用成功と表示せず、chat側も同結果を正しく案内する。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-05C`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-05C`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `bash -n services/vexa-agent/system/bin/vexa`
  - `bash services/vexa-agent/tests/test_vexa_public_routing.sh`
  - `services/agent-api/tests/test_internal_auth.py::{test_missing_and_wrong_secret_are_rejected_before_side_effects,test_only_meeting_and_runtime_internal_callers_preserve_response_shape}`
  - `services/agent-api/tests/test_no_direct_static_auth.py::{test_new_client_prefers_signed_gateway_identity_without_static_fallback,test_legacy_direct_static_auth_is_counted_compatibility_only}`
  - `services/meeting-api/tests/test_managed_standalone_auth_modes.py::{test_new_managed_profile_uses_only_signed_identity,test_legacy_managed_static_key_is_counted_compatibility_only,test_standalone_requires_strong_key_and_rejects_gateway_identity,test_mixed_mode_fails_startup}`
  - `services/runtime-api/tests/test_profiles.py::test_agent_and_browser_profiles_have_no_global_service_secrets`
  - `services/agent-api/tests/test_workspace_initialization.py::test_first_time_workspace_without_legacy_template_initializes_empty_repo`
  - fake curl全host=Gateway、user tokenのみ、internal/Runtime/collector直結0、token欠落network 0/exit 2。
  - Agent internal endpointはmissing/wrong 403・side effect 0、正しい旧callerだけ既存shape+legacy counter。new callerはinternal call 0。
  - agent/browser container envにInternal/Runtime/Admin global secret 0。
  - browser-session generated specは`BOT_CONFIG.internalSecret` 0、Runtime callback headerだけがservice-side secretを持つ。
  - `docker build -f services/vexa-agent/Dockerfile -t rf05c-vexa-agent .` と `docker run --rm rf05c-vexa-agent sh -lc 'command -v vexa && vexa --help >/dev/null'` がexit 0。
  - `git grep 'features/knowledge-workspace/templates/knowledge' -- services/vexa-agent services/agent-api`は0件。
  - `V-BACKEND`, `V-CLIENTS`。
- リスクと戻し方: Gateway/token provision不備でnew CLI停止。new clientにinternal fallbackを足さず、旧generationだけでrollbackする。rollout Gateway/Agent確認→new image→legacy counter観測。
- 依存: RF-05B, RF-05A, OP-05A-DRAIN
- コミット: `RF-05C prefer gateway routes while counting legacy agent paths`

### RF-05D1 Runtime service principalをlegacy互換のまま先行配備

- 対象:
  - `services/runtime-api/runtime_api/config.py:1-末尾`
  - `services/runtime-api/runtime_api/main.py:1-末尾`
  - `services/runtime-api/runtime_api/api.py:1-末尾`
  - `services/runtime-api/runtime_api/scheduler_api.py:1-69`
  - `services/runtime-api/runtime_api/backends/__init__.py:1-末尾`
  - `services/runtime-api/runtime_api/backends/docker.py:1-末尾`
  - `services/runtime-api/runtime_api/backends/kubernetes.py:1-末尾`
  - `services/agent-api/agent_api/config.py:1-末尾`
  - `services/agent-api/agent_api/container_manager.py:1-末尾`
  - `services/agent-api/agent_api/main.py:1-末尾`
  - `services/api-gateway/main.py:1-末尾`
  - `services/meeting-api/config/profiles.yaml:1-末尾`
  - `services/meeting-api/meeting_api/config.py:1-末尾`
  - `services/meeting-api/meeting_api/main.py:1-末尾`
  - `services/meeting-api/meeting_api/meetings.py:1-末尾`
  - read-only既知一致: `services/meeting-api/meeting_api/callbacks.py:1-末尾`、`services/meeting-api/meeting_api/container_stop_outbox.py:1-末尾`、`services/meeting-api/meeting_api/recording_finalizer.py:1-末尾`、`services/meeting-api/meeting_api/schemas.py:1-末尾`、`services/meeting-api/meeting_api/sweeps.py:1-末尾`、`services/meeting-api/meeting_api/webhook_url.py:1-末尾`のcomment/docstring一致
  - read-only inventory: `git grep -n -E -e 'RUNTIME_API|runtime-api|RuntimeClient' -- services/meeting-api services/agent-api services/api-gateway`。上記write対象またはread-only既知一致以外が1件でもあれば停止してplan reviewへ戻る
  - `deploy/compose/docker-compose.yml:140-160,270-280`
  - `deploy/lite/supervisord.conf:120-150,190-200`
  - `deploy/helm/charts/vexa/{values.yaml,templates/deployment-runtime-api.yaml,templates/rbac-runtime-api.yaml}:1-末尾`
  - 新規 `deploy/helm/charts/vexa/templates/namespace-workloads.yaml:1-末尾`
  - 新規 `deploy/helm/charts/vexa/templates/admission-runtime-workloads.yaml:1-末尾`
  - 新規 `deploy/helm/charts/vexa/templates/networkpolicy-workloads.yaml:1-末尾`
  - 対象外（変更禁止）: Helm chartにdeploymentが存在しないAgent/Calendar/Telegram/Wake/Voiceprint/Transcription向けHelm object
  - 新規 `services/runtime-api/tests/test_auth_compatibility.py:1-末尾`
  - 新規 `tests3/unit/refactor/test_rf_05d1.py:1-末尾`
- 問題: Runtime middlewareは`API_KEYS`空なら全APIがopenだが、shared keyを同時に削除すると旧Runtime/旧callerのrolling deployとrollbackが成立しない。
- 変更:
  - `RUNTIME_MEETING_API_TOKEN`, `RUNTIME_AGENT_API_TOKEN`, `RUNTIME_GATEWAY_API_TOKEN`を相互に別値で追加し、constant-time照合後`RuntimePrincipal(name,scopes,allowed_profiles)`をrequest stateへ置く。新tokenのmissing/placeholder/相互同値はproduction startup failure。
  - operator gate前にcreate bodyの危険な自由度を閉じる。profile別Pydantic DTOを`extra="forbid"`とし、callerが指定できるのはprofile selector、trusted subjectにbindしたmeeting/session ID、typed provider/capability refだけ。`name,image,command,entrypoint,raw env,mounts,volumes,network,network_mode,devices,privileged,cap_add,hostNetwork,hostPID,hostIPC,serviceAccount,k8s_overrides`は422でbackend/state call 0。server profile resolverだけが実値を構築し、Docker backendもroot/docker.sock/containerd/device/host network/privileged/cap-addをdefense-in-depth拒否する。
  - nameは`profile+owner/session hash+128-bit server random`だけ、Redis reservationはSET NX、backend 409は既存resourceをstart/reuseせず409にする。Kubernetesはcontrol/data serviceとSecretsを`vexa-control` namespace、generated workloadだけを`vexa-workloads` namespaceへ分離する。Runtime control-plane SAのRole/RoleBindingは`vexa-workloads`のPod/exec/log/deleteと必要なNetworkPolicyだけにnamespace-scopeし、cluster-wide Role、`vexa-control` Pod/Secret、他namespaceへのverb 0。generated PodはRoleBinding 0の`vexa-workload` SA+`automountServiceAccountToken:false`固定、request/profileから上書き不可。ValidatingAdmissionPolicyまたは同等のcluster admissionは`runtime.managed=true` workloadについて許可image digest/profile label/SA/namespace/non-root securityContext/volume/networkを固定し、Runtime SA compromise fixtureでもcontrol serviceへexec/get/list/delete/secret read 0にする。これらをoperator gate後のRF-06I1まで延期しない。
  - Meeting principalはmeeting/browser-session profileのcreate/get/delete/waitとmeeting-owned scheduler、Agent principalはagent profileのcreate/get/delete/touch/exec/archiveとAgent scheduler、Gateway principalはbrowser-sessionのget/touchだけ。new principal pathはoperation前にscope/owner/profileを検査し、禁止操作はbackend/Redis call前403/404。
  - Runtime verifierを先に配備できるよう、このcommitだけ既存`API_KEYS`/legacy headerを受理し、`RuntimePrincipal(name="legacy-compat")`へ写像して現行route意味を維持する。legacy使用はcredential値なしのcounterだけを記録する。空`API_KEYS`による認証middleware未装着は廃止し、legacyもnewも設定されないproductionはstartup failure。
  - verifier配備後、Meeting/Agent/Gateway client helperを各自のnew tokenへ切り替える。Compose/Lite/HelmはRuntimeへ3 new tokenとlegacy key、各callerへ自principal tokenと一時legacy rollback refを配る。new clientはnew tokenを優先し、両方あるときlegacyを送らない。
  - `GET /profiles`のnew principal routeはoperator-onlyとしresolved env/secret値を返さない。lifecycle callbackはraw headerを永続化せず、`callback_ref={service:"meeting",operation:"bot_callback"}`だけをjobへ保存する。
  - component rollout順を`Runtime dual verifier -> Meeting/Agent/Gateway callers -> new-token canary`に固定する。legacy rejection/removalはRF-05D2まで行わない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-05D1`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-05D1`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/runtime-api/tests/test_auth_compatibility.py::{test_new_principals_enforce_operation_profile_matrix,test_legacy_key_is_temporary_compatibility_only,test_new_token_wins_when_both_are_present,test_no_config_never_disables_auth_middleware,test_profiles_redact_resolved_secrets}`
  - `services/runtime-api/tests/test_auth_compatibility.py::{test_create_rejects_client_image_command_env_mount_network_name_and_k8s_overrides_before_backend,test_backend_rejects_root_socket_device_host_network_and_privileged_even_for_server_spec}`
  - `services/runtime-api/tests/test_auth_compatibility.py::{test_server_generated_name_collision_never_reuses_existing_backend,test_generated_workload_uses_unbound_service_account_with_automount_false_before_runtime_rollout,test_runtime_role_is_namespace_scoped_and_cannot_read_exec_or_delete_control_plane_resources,test_admission_rejects_workload_label_profile_service_account_security_or_volume_override}`
  - `tests3/unit/refactor/test_rf_05d1.py::{test_each_caller_prefers_only_own_runtime_principal,test_dual_verifier_is_deployed_before_caller_cutover,test_callback_job_stores_typed_ref_not_headers}`
  - new principal cross-matrix: Gateway tokenでexec/delete/scheduler/archive=403、Meeting tokenでexec/agent profile=403、Agent tokenでmeeting profile=403、正規operationだけ成功。
  - legacy fixtureは現行route成功、counter exact 1、credential/log/response/Redisへの値0。new+legacy fixtureはnew path counter 0。
  - generated containerにRuntime token 0、profile response/Redis job/backend metadataに全token/callback canary 0。
  - `V-BACKEND`, `V-OPS`。
- リスクと戻し方: Runtimeよりcallerを先に配備すると401になる。必ずdual verifier先行とし、失敗時はこのcompatibility commitのRuntimeへ戻す。認証なし状態へ戻さず、RF-05CのSHAから新worktreeで再実行する。
- 依存: RF-05C, RF-00B, RF-00D
- コミット: `RF-05D1 deploy runtime service principals with legacy compatibility`

### RF-05D1B Workload imageとRuntime profileをrootless sandboxへ固定

- 対象:
  - `services/vexa-bot/Dockerfile:1-149`
  - `services/vexa-bot/core/Dockerfile:1-97`
  - `services/vexa-agent/Dockerfile:1-26`
  - `services/vexa-bot/core/src/docker.ts:1-151`
  - `services/runtime-api/profiles.yaml:1-96`
  - `services/meeting-api/config/profiles.yaml:1-53`
  - `deploy/compose/docker-compose.yml:1-末尾`
  - `deploy/lite/{entrypoint.sh,supervisord.conf}:1-末尾`
  - `services/runtime-api/runtime_api/backends/{__init__,docker,kubernetes}.py:1-末尾`
  - `deploy/helm/charts/vexa/{values.yaml,templates/configmap-runtime-profiles.yaml,templates/deployment-runtime-api.yaml}:1-末尾`
  - 新規 `services/runtime-api/tests/test_workload_security_profiles.py:1-末尾`
  - 新規 `tests3/unit/refactor/test_rf_05d1b.py:1-末尾`
- 問題: RF-05D1の認証cutoverとimage/rootless移行はfailure domainもrollback順も異なる。同一commitにすると、401障害とChromium/filesystem障害を独立に切り戻せない。
- 変更:
  - Meeting、Browser、Agent imageへ用途別の固定non-root UID/GIDを作り、build時に必要な所有権だけを付与する。runtimeで`chown`、root shell、sudo、SSH daemonを起動しない。
  - Coreの全Chromium launchから`--no-sandbox`を削除し、user namespace/seccompを有効にしたsandbox起動だけを許す。sandboxが使えないhostではflagを戻さずstartupを失敗させる。
  - Docker server profileは`User=<profile uid>,CapDrop=ALL,SecurityOpt=no-new-privileges,ReadonlyRootfs=true`、pinned seccomp、private `/tmp,/run,<session writable dir>` tmpfsだけ。Agentの`/run`は固定UID所有の0700で、RF-03Dの`/run/vexa-git/<random>`以外へcredential fileを作らせず、終了後emptyを検査する。Kubernetesは`runAsNonRoot,runAsUser,runAsGroup,allowPrivilegeEscalation:false,capabilities.drop=["ALL"],seccompProfile.type=RuntimeDefault,readOnlyRootFilesystem:true`固定。request/profile overrideをRF-05D1のDTO/backend guardが拒否することを再確認する。
  - GPU/VAAPI profileだけexact render deviceと固定render groupを許す。`privileged`、root、host namespace、追加capabilityはGPUでも許さない。
  - rolloutは新image単体smoke→Runtime profile dual deploy→新規session canaryの順。既存sessionをin-place変更しない。失敗時は新規session受付を止め、Runtime principalはRF-05D1のまま維持し、image/profileだけ直前digestへ戻す。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-05D1B`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-05D1B`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。
  - `services/runtime-api/tests/test_workload_security_profiles.py::{test_all_generated_profiles_run_nonroot_with_zero_caps_no_new_privileges_readonly_root_and_runtime_default_seccomp,test_agent_private_run_tmpfs_supports_git_bootstrap_and_is_empty_afterward,test_chromium_launches_with_sandbox_and_no_no_sandbox_flag,test_only_gpu_profile_has_exact_render_device_and_group_exception,test_client_cannot_override_any_security_context}`
  - `tests3/unit/refactor/test_rf_05d1b.py::{test_meeting_browser_agent_images_define_distinct_nonroot_users,test_compose_and_helm_profiles_match_runtime_security_contract,test_rollout_keeps_runtime_principal_configuration_unchanged}`
  - Meeting/Browser/Agentのlocal imageをbuildして各1 container smokeを実行し、`id -u != 0`、`CapEff/CapPrm/CapBnd=0`、rootfs write=EROFS、allow-listed tmpfsだけwrite成功、SA token 0、Chromium sandbox/meeting join fixture/Agent command fixture成功。
  - `V-BACKEND`, `V-CORE`, `V-OPS`。
- リスクと戻し方: Chromium sandbox、writable path、GPU groupのhost差。認証commitと同時rollbackしない。image/profileだけ直前digestへ戻し、失敗branchを保持してRF-05D1 SHAからこの項目を再実行する。
- 依存: RF-05D1, RF-03D
- コミット: `RF-05D1B harden workload images and runtime profiles`

#### OP-05D-DRAIN（非commit・人間operator停止点）

RF-05C/D1 commitを実環境へGateway/Agent/Meeting compatibility→Runtime verifier→Meeting/Agent/Gateway callerの順で配備する権限がない実行者はここで停止する。RF-05D1B image hardeningを含むR2 merge後に配備するが、drain対象commitはrelease-boundariesの`operator_expected_item=RF-05D1`である。fixture counterを実drainの代用にしてRF-05C2へ進んではいけない。認可済みoperatorが共通`operation-gate-v1`で`measurements={runtime_legacy_request_count:0,meeting_principal_success_count:>0,agent_principal_success_count:>0,gateway_principal_success_count:>0,agent_internal_legacy_count_by_path:{chat:0,workspace:0,webhook:0},agent_direct_static_auth_count:0,meeting_managed_static_auth_count:0,gateway_cli_success_count:>0,active_old_agent_workload_count:0,active_old_meeting_managed_client_count:0,max_request_timeout_seconds,scheduler_poll_interval_seconds,max_workload_session_lifetime_seconds}`を提供する。new pathは各principal/path個別`>0`、旧pathは各0でaggregateを代用しない。観測時間は`max(max_request_timeout_seconds+scheduler_poll_interval_seconds,max_workload_session_lifetime_seconds)+300`以上。旧Agent internal/direct、Meeting managed static、Runtime shared credentialはlegacy admission/deployment generationをfreeze後、new intended pairへrotateし、old canary reject/旧ref 0を記録する。`bash scripts/test/run-refactor-operator-gate.sh --master-task full-repo-refactoring-2026-07-24 --archive-root "$CONTROL_ROOT/.pipeline/release-archives" --release r2 --gate op-05d-drain`がexit 0になるまで進まない。

### RF-05C2 Agent internal/directとMeeting managed static互換をdrain後に閉鎖

- 対象:
  - `services/vexa-agent/system/bin/vexa:1-870`
  - `services/agent-api/agent_api/{main,auth,workspace,chat,config,container_manager}.py:1-末尾`
  - `services/api-gateway/main.py:1-末尾`
  - `services/meeting-api/meeting_api/{auth,meetings}.py:1-末尾`
  - `services/runtime-api/profiles.yaml:1-末尾`
  - `deploy/compose/docker-compose.yml:1-末尾`
  - `deploy/lite/{entrypoint.sh,supervisord.conf}:1-末尾`
  - `deploy/helm/charts/vexa/{values.yaml,templates/secret.yaml,templates/deployment-meeting-api.yaml}:1-末尾`
  - 既存（RF-05Cで追加済み）`services/agent-api/tests/test_no_direct_static_auth.py:1-末尾`
  - 既存（RF-05Cで追加済み）`services/meeting-api/tests/test_managed_standalone_auth_modes.py:1-末尾`
  - 新規 `tests3/unit/refactor/test_rf_05c2.py:1-末尾`
- 問題: RF-05Cはrolling deploy用にAgent internal/direct static authとMeeting managed static keyを残す。drain後も残せばGateway trusted identityを迂回できる。
- 変更:
  - OP-05D-DRAINのpath別counter、active old workload/client、観測時間、old-value canaryを検証後、Agent internal chat/workspace/webhook legacy route/auth、public direct static auth、Meeting managed static key fallbackと全rollback Secret refを削除する。
  - Agent public routeとmanaged MeetingはRF-05A trusted identityだけを受理する。standalone Meetingの専用strong keyは別modeとして維持し、managed/standalone mixed configをstartup failureのままにする。
  - new workload CLI/Core/profileはGateway user tokenだけを持ち、Agent/Runtime/Meeting/internal direct URLまたはglobal Internal/Admin/Runtime credentialをenv/config/mountへ0にする。旧credentialを新compat keyへrenameして残さない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-05C2`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-05C2`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。
  - `services/agent-api/tests/test_no_direct_static_auth.py::{test_public_routes_accept_only_signed_gateway_identity,test_old_direct_and_internal_credentials_have_zero_side_effects}`
  - `services/meeting-api/tests/test_managed_standalone_auth_modes.py::{test_managed_mode_accepts_only_signed_identity_and_has_no_static_ref,test_standalone_keeps_only_its_dedicated_key,test_old_managed_key_has_zero_side_effects}`
  - `tests3/unit/refactor/test_rf_05c2.py::{test_workloads_have_no_internal_admin_runtime_or_managed_meeting_secret,test_agent_legacy_routes_auth_and_deploy_refs_are_absent,test_gateway_user_token_is_the_only_workload_cli_credential}`
  - `V-BACKEND`, `V-MEETING`, `V-CORE`, `V-OPS`。
- リスクと戻し方: stale旧image/clientは停止する。fresh cutover再検証が不合格ならR2 compatibility deploymentを維持しR3を配備せず中断する。旧static/internal authを新commitで戻さない。
- 依存: RF-05C, RF-05D1, OP-05D-DRAIN
- コミット: `RF-05C2 remove drained agent and managed meeting legacy auth`

### RF-05D2 Runtime shared credentialをdrain後に閉鎖

- 対象:
  - RF-05D1後の `services/runtime-api/runtime_api/{config,main,api,scheduler_api}.py:1-末尾`
  - `services/meeting-api/meeting_api/meetings.py:750-1210`
  - `services/agent-api/agent_api/{main,container_manager}.py:1-末尾`
  - `services/api-gateway/main.py:1-末尾`
  - `deploy/{compose,lite,helm}/**:1-末尾` のRuntime credential wiring
  - 新規 `services/runtime-api/tests/test_auth_fail_closed.py:1-末尾`
  - 新規 `tests3/unit/refactor/test_rf_05d2.py:1-末尾`
- 問題: RF-05D1はrolling互換のためshared `API_KEYS`とcaller legacy refを意図的に残しており、侵害callerの権限分離はまだ完了していない。
- 変更:
  - new principal canaryとlegacy counter=0を最低「最大request timeout+scheduler poll interval+5分」のdrain windowで確認した後、Runtimeから`API_KEYS`/legacy header/`legacy-compat` principalを削除する。Meeting/Agent/Gatewayのlegacy ref/fallback、Compose/Lite/Helm legacy secret keyも同じcommitで削除する。
  - healthだけを無認証200とし、全他routeはmissing/wrong/old shared tokenで403、side effect 0。scope/owner/profile matrixとtyped callback refをRF-05D1のまま維持する。
  - principal別rotationは`*_PREVIOUS_TOKEN`1世代だけを許す。順序は「Runtimeへnew current+old previous -> 当該callerをnew currentへ切替 -> drain中old counter 0 -> previous refを全profileから削除」。新installとPhase 1完了時は全`*_PREVIOUS_TOKEN`を空/未設定にする。
  - rollbackはpreviousが残るdrain中だけ旧callerへ戻せる。previous削除後にshared `API_KEYS`を復活させず、taskを未完で停止してRF-05D1のSHAからRF-05D2をやり直す。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-05D2`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-05D2`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/runtime-api/tests/test_auth_fail_closed.py::{test_missing_wrong_placeholder_and_legacy_shared_tokens_fail_closed,test_principal_operation_profile_cross_matrix,test_profiles_redact_resolved_secrets,test_current_previous_rotation_accepts_only_declared_principal}`
  - `tests3/unit/refactor/test_rf_05d2.py::{test_legacy_api_keys_and_caller_fallbacks_are_absent,test_each_caller_has_only_own_runtime_secret_ref,test_previous_tokens_are_empty_in_final_render,test_callback_job_stores_typed_ref_not_headers}`
  - health以外missing/wrong/old shared token 403・backend/Redis side effect 0。正規principalだけ既存response shape。
  - Compose/Lite/Helm renderで各callerは自principal current refだけ、Runtimeは3 currentを参照、legacy/previous ref 0、生成container token 0。
  - `rg -n 'API_KEYS|legacy-compat|RUNTIME_.*_PREVIOUS_TOKEN' services/runtime-api services/meeting-api services/agent-api services/api-gateway deploy` はnegative/rotation test以外0。
  - `V-BACKEND`, `V-OPS`。
- リスクと戻し方: 未inventory callerがold shared tokenを使うと停止する。RF-05D1 counterが0でなければ本項目を開始しない。失敗branchを保持し、RF-05D1のSHAから新worktreeで再実行する。
- 依存: RF-05C2, RF-05D1, OP-05D-DRAIN
- コミット: `RF-05D2 remove runtime shared credentials after caller drain`

### RF-05E Scheduler・webhook outbound HTTP policyを統一

- 対象:
  - `services/agent-api/agent_api/main.py:359-425`
  - `services/runtime-api/runtime_api/api.py:1-末尾`
  - `services/runtime-api/runtime_api/lifecycle.py:1-末尾`
  - `services/runtime-api/runtime_api/scheduler_api.py:1-69`
  - `services/runtime-api/runtime_api/scheduler.py:204-280`
  - `services/meeting-api/meeting_api/webhook_url.py:1-末尾`
  - `services/meeting-api/meeting_api/webhook_delivery.py:1-末尾`
  - `services/meeting-api/meeting_api/webhook_retry_worker.py:1-末尾`
  - `services/admin-api/app/webhook_delivery_broker.py:1-末尾`（RF-03B作成物）
  - `services/dashboard/src/app/api/webhooks/test/route.ts:1-末尾`
  - read-only既知一致: `services/dashboard/src/app/api/webhooks/deliveries/route.ts:1-末尾`のdelivery表示route
  - read-only inventory: `git grep -n -E -e 'callback_url|callbackUrl|lifecycle.*callback' -- services/runtime-api`と`git grep -n -E -e 'test.*webhook|webhook.*test' -- services/admin-api services/dashboard/src/app/api/webhooks`。上記write対象・read-only既知一致・category read-only以外のproduction実装一致が1件でもあれば停止してplan reviewへ戻る
  - 新規 `packages/security-contracts/outbound-http-policy-v1.json:1-末尾`
- 問題: prefix検査と保存時検査だけではIPv6、非global IP、DNS再解決、redirect、任意header/callbackを防げず、schedulerとwebhookがSSRF/secret転送口になる。
- 変更:
  - 言語間vectorを唯一の判定fixtureとし、Admin/Meeting/Runtimeのservice-local validatorが同じallow/deny結果を返す。
  - external URLはhttp/https、長さ2048、userinfo/fragmentなし、hostnameあり、port 1..65535。literal/DNS全A/AAAAを`ipaddress`へ通し、1件でも`is_global=False`、single-label、unresolved、IPv4-mapped IPv6、metadata等ならreject。
  - 保存時、各attempt、durable retry直前に再解決する。transportは検証済みA/AAAAの1つへ直接connectし、TLS SNI/証明書検証とHTTP Hostは元のhostnameを維持する。socket peer IPが検証集合外ならrequest body/credential送信前にcloseする。retryは必ず再解決・再検証・再pinし、HTTP client既定poolがhostnameを再解決する経路を使わない。redirectは追わず3xxを成功扱いしない。methodはGET/POST/PUT/PATCH/DELETE、body 256KiB、timeout 1..30秒、response 1MiB、preview 200文字。
  - hop-by-hop、credential、cookie、`X-API-Key/X-Internal-Secret`等のclient指定headerとCR/LFを422。logはscheme/host/portだけ。
  - scheduler requestを `external_http` と `internal_agent_chat` のdiscriminated modelへする。永続化するのはmethod/body上限、sanitized headers、destination policy、`credential_ref`だけ。internal URL/secretはdispatch時にserver configから構築し、raw credential/headerはrequest body、Redis job、result、logへ保存しない。legacy kindなしjobはexact internal contractだけadapterし、それ以外はexternalとして再検査、不明なら`failed: unsafe_legacy_request`。
  - lifecycle callbackは設定済みMeeting origin + 固定internal pathだけ。success/failure callback、通常webhook、retry、Admin testも同policy。RF-03BのAdmin webhook brokerはcredential refをsubject/versionへ照合した後、各attempt直前に保存済みendpointを再検証・再解決・IP pinし、request-local memoryでだけHMAC/Bearer headerを生成する。Meeting/retry/GatewayへURL/secret/headerを返さず、Dashboardは保存済みrefへのtest要求だけを行う。
  - application pinに加えRF-06I2のhost egress policyでprivate/link-local/metadataをdenyする。HTTP/1.1、HTTP/2のどちらもverified peer IPをevidenceへhost hash付きで残し、proxy/environment variableによる別経路を無効化する。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-05E`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-05E`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `tests3/unit/refactor/test_rf_05e.py::{test_all_services_match_outbound_policy_vectors,test_reject_has_zero_transport_calls,test_connection_is_pinned_to_verified_ip_with_original_host_and_sni,test_public_validation_then_private_connect_race_sends_zero_body_or_credentials,test_dns_rebinding_and_redirect_to_private_are_terminal,test_redis_jobs_logs_and_results_have_no_raw_credential}`
  - `tests3/unit/refactor/test_rf_05e.py::test_admin_webhook_broker_uses_same_dns_pin_redirect_header_and_size_policy`
  - `services/admin-api/tests/test_webhook_delivery_broker.py::{test_dispatch_resolves_versioned_ref_just_in_time_and_pins_verified_peer,test_retry_revalidates_dns_and_never_returns_url_secret_or_header}`
  - `services/dashboard/tests/test_user_scoped_webhook_routes.test.ts::test_webhook_test_uses_stored_endpoint_without_client_url`
  - vectorはIPv4/IPv6/mapped/link-local/metadata/CGNAT/single-label/unresolved/credential/redirect-to-privateを含み全service同結果。
  - reject時transport call 0、redirect Location call 0、retryでpublic→privateへ変化したfixture送信0/terminal failure。
  - internal Agent chatはserver設定だけ、external request/log/Redis job/resultへservice token 0。
  - mock resolver/transportだけを使い実Internet/localhost接続0。
  - `V-BACKEND`, `V-DASH`, `V-INTEGRATIONS`。
- リスクと戻し方: private/redirect/custom auth webhookとunsafe legacy jobが止まる。allow-listを広げて救済せず、host/method/件数だけ報告してpublic non-redirect endpointへ移行。旧prefix/follow_redirectsへ戻さない。
- 依存: RF-03B, RF-05D2, RF-00B, RF-00C
- コミット: `RF-05E enforce one outbound HTTP security contract`

### RF-05F Default/empty secretとdirect-login fallbackを全profileで廃止

- 対象:
  - `deploy/compose/docker-compose.yml:20-160,270-300,440-500,690-710`
  - `deploy/compose/{Makefile,README.md}:1-末尾`
  - read-only inventory: `git grep -n -E -e 'changeme|lite-|vexa-|DIRECT_LOGIN|JWT_SECRET' -- deploy/compose`。期待pathは上記3 fileだけで、別pathが1件でもあれば停止してplan reviewへ戻る
  - `deploy/lite/entrypoint.sh:1-末尾`
  - `deploy/lite/supervisord.conf:1-末尾`
  - `deploy/lite/Makefile:1-末尾`
  - `deploy/helm/charts/vexa/values.yaml:1-末尾`
  - `deploy/helm/charts/vexa/templates/_helpers.tpl:1-末尾`
  - `deploy/helm/charts/vexa/templates/secret.yaml:1-末尾`
  - `deploy/helm/charts/vexa/templates/deployment-admin-api.yaml:1-末尾`
  - `deploy/helm/charts/vexa/templates/deployment-api-gateway.yaml:1-末尾`
  - `deploy/helm/charts/vexa/templates/deployment-dashboard.yaml:1-末尾`
  - `deploy/helm/charts/vexa/templates/deployment-mcp.yaml:1-末尾`
  - `deploy/helm/charts/vexa/templates/deployment-meeting-api.yaml:1-末尾`
  - `deploy/helm/charts/vexa/templates/deployment-minio.yaml:1-末尾`
  - `deploy/helm/charts/vexa/templates/deployment-pgbouncer.yaml:1-末尾`
  - `deploy/helm/charts/vexa/templates/deployment-redis.yaml:1-末尾`
  - `deploy/helm/charts/vexa/templates/deployment-runtime-api.yaml:1-末尾`
  - `deploy/helm/charts/vexa/templates/deployment-tts-service.yaml:1-末尾`
  - `deploy/helm/charts/vexa/templates/job-migrations.yaml:1-末尾`
  - `deploy/helm/charts/vexa/templates/job-minio-init.yaml:1-末尾`
  - `deploy/helm/charts/vexa/templates/statefulset-postgres.yaml:1-末尾`
  - `deploy/helm/charts/vexa-lite/values.yaml:1-末尾`
  - `deploy/helm/charts/vexa-lite/templates/secret.yaml:1-末尾`
  - `deploy/helm/charts/vexa-lite/templates/deployment.yaml:1-末尾`
  - `deploy/helm/charts/vexa-lite/templates/dashboard-deployment.yaml:1-末尾`
  - `scripts/bot-debug.sh:1-52`
  - 新規 `tests3/unit/refactor/test_bot_debug_secret_transport.py:1-末尾`
  - `services/dashboard/src/app/api/auth/[...nextauth]/route.ts:1-末尾`
  - `services/dashboard/src/app/api/auth/send-magic-link/route.ts:1-末尾`
  - `services/dashboard/src/app/api/auth/verify/route.ts:1-末尾`
  - `services/dashboard/src/lib/direct-login.ts:1-末尾`
  - `services/dashboard/src/app/api/health/route.ts:1-末尾`
  - read-only既知一致:
    - `services/dashboard/src/app/api/admin/[...path]/route.ts:1-末尾`
    - `services/dashboard/src/app/api/auth/admin-verify/route.ts:1-末尾`
    - `services/dashboard/src/app/api/auth/me/route.ts:1-末尾`
    - `services/dashboard/src/app/api/auth/oauth-callback/route.ts:1-末尾`
    - `services/dashboard/src/app/api/auth/shared-login/route.ts:1-末尾`
    - `services/dashboard/src/app/api/calendar/oauth/start/route.ts:1-末尾`
    - `services/dashboard/src/app/api/calendar/oauth/complete/route.ts:1-末尾`
    - `services/dashboard/src/app/api/config/route.ts:1-末尾`
    - `services/dashboard/src/app/api/zoom/oauth/start/route.ts:1-末尾`
    - `services/dashboard/src/app/api/zoom/oauth/complete/route.ts:1-末尾`
    - `services/dashboard/src/lib/auth-utils.ts:1-末尾`
    - `services/dashboard/src/lib/dashboard-copy.ts:1-末尾`
    - `services/dashboard/src/lib/email.ts:1-末尾`
    - `services/dashboard/src/lib/zoom-oauth-client.ts:1-末尾`
  - read-only inventory: `git grep -n -E -e 'magic.?link|direct.?login|NEXTAUTH|OAuth|verify' -- services/dashboard/src/app/api services/dashboard/src/lib`。上記write対象またはread-only既知一致以外の認証実装が1件でもあれば停止してplan reviewへ戻る
  - `services/meeting-api/meeting_api/storage.py:100-125`
  - `services/admin-api/app/main.py:1-末尾`
  - `services/api-gateway/main.py:1-末尾`
  - `services/meeting-api/config/profiles.yaml:1-末尾`
  - `services/meeting-api/meeting_api/config.py:1-末尾`
  - `services/meeting-api/meeting_api/main.py:1-末尾`
  - `services/meeting-api/meeting_api/meetings.py:1-末尾`
  - `services/runtime-api/profiles.yaml:1-末尾`
  - `services/runtime-api/runtime_api/config.py:1-末尾`
  - `services/runtime-api/runtime_api/main.py:1-末尾`
  - `services/voiceprint-service/main.py:1-末尾`
  - read-only既知一致:
    - `services/meeting-api/meeting_api/auth.py:1-末尾`
    - `services/meeting-api/meeting_api/callbacks.py:1-末尾`
    - `services/meeting-api/meeting_api/collector/auth.py:1-末尾`
    - `services/meeting-api/meeting_api/collector/config.py:1-末尾`
    - `services/meeting-api/meeting_api/collector/processors.py:1-末尾`
    - `services/meeting-api/meeting_api/dispatch_check.py:1-末尾`
    - `services/meeting-api/meeting_api/drive_export.py:1-末尾`
    - `services/meeting-api/meeting_api/final_transcription.py:1-末尾`
    - `services/meeting-api/meeting_api/post_meeting.py:1-末尾`
    - `services/meeting-api/meeting_api/redaction.py:1-末尾`
    - `services/meeting-api/meeting_api/voiceprint_matching.py:1-末尾`
    - `services/runtime-api/runtime_api/backends/kubernetes.py:1-末尾`
  - read-only inventory: `git grep -n -E -e 'SECRET|API_KEY|TOKEN' -- services/admin-api services/api-gateway services/meeting-api services/runtime-api services/voiceprint-service`。上記write対象・read-only既知一致・category read-only以外のproduction/config一致が1件でもあれば停止してplan reviewへ戻る
  - 新規 `scripts/deploy/secret_preflight.py:1-末尾`
- 問題: `postgres/changeme/vexa-*/lite-*`等の既知default、Admin keyへのJWT fallback、direct login default有効により設定漏れが安全側に倒れない。
- 変更:
  - production preflightでAdmin、legacy Internal、`AGENT_RUNTIME_CONFIG_SECRET`、`GATEWAY_ADMIN_VALIDATE_SECRET`、6種のGateway identity secret（Meeting/Calendar/Agent/Admin/MCP/Workload Access）、`MCP_GATEWAY_ASSERTION_SECRET`、`MEETING_ADMIN_WEBHOOK_IDENTITY_SECRET`、`OAUTH_STATE_ENCRYPTION_SECRET`、`CALENDAR_SCHEDULER_ASSERTION_SECRET`、`TELEGRAM_GATEWAY_ASSERTION_SECRET`、`TRANSCRIPTION_SERVICE_TOKEN`、`VOICEPRINT_SERVICE_TOKEN`、`WAKE_ORCHESTRATOR_WS_SERVICE_TOKEN`、3種のRuntime principal token、`MEETING_TOKEN_SIGNING_SECRET`、`BOT_CALLBACK_CAPABILITY_HASH_SECRET`、`BROWSER_STORAGE_CAPABILITY_SIGNING_SECRET`、`BOT_EVENT_CAPABILITY_SECRET`、`BOT_TRANSCRIPTION_CAPABILITY_SECRET`、`BOT_WAKE_CAPABILITY_SECRET`、`BOT_TTS_CAPABILITY_SECRET`、`RECORDING_UPLOAD_CAPABILITY_SECRET`、`BOT_PROXY_CAPABILITY_SECRET`、7種の`CAPABILITY_INTROSPECTION_{EVENT,TRANSCRIPTION,WAKE,TTS,PROXY,ACCESS_MEETING,ACCESS_AGENT}_TOKEN`、`WORKLOAD_ACCESS_REGISTRATION_SECRET`、`RUNTIME_MEETING_ACCESS_STATE_SECRET`、`RUNTIME_AGENT_ACCESS_STATE_SECRET`、`RUNTIME_NETWORK_POLICY_MAC_SECRET`、JWT/NextAuthは32 byte以上かつ全組合せ相互非同値、DB/MinIO/Redis credentialは16 byte以上かつusername非同値を要求し、既知placeholder集合をcase-insensitive拒否する。`SYNTHETIC_RIG_SECRET`はtest overlayかつenable flag=trueの場合だけ32 byte以上・他secretと非同値を必須にし、production/developmentでは未設定かつSecret ref 0を要求する。errorは変数名/理由だけ。
  - signing/static service secret配布先をexact matrixへ固定する。`GATEWAY_{MEETING,CALENDAR,AGENT,ADMIN,MCP,WORKLOAD_ACCESS}_IDENTITY_SECRET`はGateway issuer+名前どおりの唯一verifierだけ。`MCP_GATEWAY_ASSERTION_SECRET`はMCP issuer+Gateway verifier、`MEETING_ADMIN_WEBHOOK_IDENTITY_SECRET`はMeeting issuer+Admin webhook broker verifier、`OAUTH_STATE_ENCRYPTION_SECRET`はGateway processだけ。`CALENDAR_SCHEDULER_ASSERTION_SECRET`はCalendar issuer+Gateway verifier、`TELEGRAM_GATEWAY_ASSERTION_SECRET`はTelegram issuer+Gateway verifierだけ。`TRANSCRIPTION_SERVICE_TOKEN`はMeeting deferred caller+Transcription verifier、`VOICEPRINT_SERVICE_TOKEN`はMeeting voiceprint client+Voiceprint verifier、`WAKE_ORCHESTRATOR_WS_SERVICE_TOKEN`はWake Orchestrator client+Wake STT verifierだけ。Voiceprintの`ALLOW_UNAUTHENTICATED`はproductionで未設定/falseだけを許し、trueはpreflight failure。`SYNTHETIC_RIG_SECRET`はtest overlayのGateway/Meetingだけで通常profileはref 0。`BOT_CALLBACK_CAPABILITY_HASH_SECRET`,`BROWSER_STORAGE_CAPABILITY_SIGNING_SECRET`,`BOT_EVENT_CAPABILITY_SECRET`,`RECORDING_UPLOAD_CAPABILITY_SECRET`はMeetingだけ、`BOT_TRANSCRIPTION_CAPABILITY_SECRET`はMeeting issuer+Transcription verifier、`BOT_WAKE_CAPABILITY_SECRET`はMeeting+Wake、`BOT_TTS_CAPABILITY_SECRET`はMeeting+TTS、`BOT_PROXY_CAPABILITY_SECRET`はMeeting issuer+非権限`workload-broker` verifier、`MEETING_TOKEN_SIGNING_SECRET`はMeeting API/collector processだけ。`RUNTIME_MEETING_ACCESS_STATE_SECRET`はRuntime issuer+Meeting verifier、`RUNTIME_AGENT_ACCESS_STATE_SECRET`はRuntime issuer+Agent verifier、`RUNTIME_NETWORK_POLICY_MAC_SECRET`はRuntime+host network-policy agentだけ。生成workload、許可以外のserviceへは0。signing secretではなく署名済み短命tokenまたはRF-06I2のper-session Ed25519 private keyだけを生成workloadへ渡す。
  - introspection auth tokenは`EVENT=Meeting内loopback brokerのみ`、`TRANSCRIPTION=Meeting+Transcription`、`WAKE=Meeting+Wake`、`TTS=Meeting+TTS`、`PROXY=Meeting+workload-broker`、`ACCESS_MEETING=Meeting+workload-access-broker`、`ACCESS_AGENT=Agent+workload-access-broker`のexact pairだけへ配る。`WORKLOAD_ACCESS_REGISTRATION_SECRET`はRuntime issuer+workload-access-broker verifierだけ。Recording/Browser storage/CallbackはMeeting process内でregistryを直接検査する。introspection/registration/state/network-policy secretは生成workload、Gateway、Admin、Redis/log/job、相互brokerへ0。
  - workload access registration mTLSはregistry entryを`planned|active`で管理する。RF-05Fの`planned`時点はCA/server/client certificate値、期限、Secret refを要求せず、schema、SAN、TLS 1.3、配布先ruleだけを検査し、全profile render ref 0を必須にする。RF-06I2が同じentryを`active`へ変えるcommitから、production既存Secret、CA certificate=Runtime+workload-access-broker、server certificate/private key=brokerだけ、Runtime client certificate/private key=Runtimeだけを必須にする。server SAN exact `workload-access-broker`、client SAN exact `runtime-api`、相互verify、期限がrelease window+30日未満ならpreflight failure。plaintext listener、`verify=false`、self-signed自動生成、private keyの生成workload/Gateway/Meeting配布は0。Compose/Lite test fixtureだけtracked test CA/certを使い、production値を生成しない。
  - RF-05F時点では未作成のTranscription/Wake/TTS capability consumer、workload-broker、workload-access-brokerへSecret refを先行配布しない。preflight registryへ型/長さ/相互非同値/distribution ruleだけを`planned`登録し、未導入consumerの値/cert期限検査なし・render ref 0をRF-05F完了条件にする。各consumerを作るRF-06D1/RF-06H/RF-06I2が自身のentryだけを`planned -> active`にし、そのcommit以後は値/certificate/expiry/exact pairを必須にする。Phase 1 gateで全entry active、全active値条件pass、許可以外ref 0、previous ref 0をまとめて検証する。
  - RF-05AのGateway identity/Calendar assertion、RF-05GのMeetingToken、RF-05H/06A〜06Hのworkload capability signing keyだけをcurrent keyとoptional previous keyの2-key ringに統一する。RF-04Bの`v1.<payload>.<sig>` admin cookie、NextAuth/OAuth wireはこの変更対象外。対象keyは既存secret名をcurrent値として保ち、同prefixの`_KID`を必須、optional previousは`_PREVIOUS_SECRET`+`_PREVIOUS_KID`の両方が揃う場合だけ許す。`kid`はASCII `[A-Za-z0-9._-]{1,64}`、current/previous ID・値は相互非同値、algorithmはHS256固定。issuerはcurrentだけ、verifierはheader/claimの`kid`でexact keyを選び、unknown kidをrejectして全鍵を総当たりしない。
  - RF-05Aで既に発行中のno-`kid` identity/assertionをrolling中に即時401へしないため、RF-05Fのverifierだけは「headerに`kid` field自体が存在しない・HS256・strict audience/route/body/TTL claim・署名がcurrent secret exact」の場合に限り`legacy_no_kid`として一時acceptし、credential値なしのaudience/issuer別counterを記録する。`kid=null|""`、unknown kid、previous keyによるno-kid、claim緩和はrejectする。全issuerをcurrent `kid`付与へ切替え、OP-06C-DRAINが最大30秒TTL+5秒skew+最大retry+300秒以上でaudience別no-kid count 0を証明するまでcompat branchを消さない。RF-05F2がbranch/refを削除し、Phase 1最終renderはprevious ref/no-kid accept 0を要求する。callbackのserver-side hash recordにも`key_id`を保存する。
  - Composeは`${VAR:?required}`。`make bootstrap-secrets`は48-byte randomをgitignored `.env`へtemp→0600→atomic renameし、既存file非上書き/値非表示。direct login default false。
  - Liteは外部指定のないservice secretだけを`/data/vexa/secrets.env`へ初回生成し0600/再利用。DB/MinIOは明示入力。credential URLをlogしない。
  - Helmは`existingSecretName`またはrequired values。template default生成を禁止し、Deploymentは固定key refだけを使う。
  - Helm `vexa-lite`も全Secret `envFrom`を削除し固定`secretKeyRef`/projected fileだけを使う。DashboardからAdmin tokenを除去する。Dashboard containerは`runAsNonRoot=true,allowPrivilegeEscalation=false,capabilities.drop=[ALL],seccompProfile.type=RuntimeDefault,readOnlyRootFilesystem=true`。main Lite containerだけはRF-06I3の短命UID分離bootstrapのため`runAsUser=0,allowPrivilegeEscalation=false,capabilities.drop=[ALL],capabilities.add=[SETUID,SETGID,KILL],seccompProfile.type=RuntimeDefault,readOnlyRootFilesystem=true`をexact例外とし、他capability/root shell/package manager/network bootstrapを禁止する。RF-06I3でreadiness前のpermanent UID/cap dropを証明できなければ起動失敗とする。`deploymentMode=single-tenant-development`、replica=1、autoscaling/ingress/service-account-token/hostPID/hostNetwork/hostPath/Docker socketなしをrender時に強制し、managed/production値はfailする。RF-06I3がchild UID/process isolationを完成させるまで「managed production対応」と表示しない。
  - `scripts/bot-debug.sh`はpredictable `/tmp/.${PROJ}_token`と`BOT_DEBUG_TOKEN` env cacheを削除し、`mktemp -d "${TMPDIR:-/tmp}/vexa-bot-debug.XXXXXXXX"`→mode0700→success/failure/SIGINT共通trap cleanupを使う。必要な一時fileは0600。Admin/user tokenはargv、env、persistent fileへ置かず、stdin-backed `curl --config /dev/fd/<fd>`のheaderとしてだけ渡す。`set -x`を明示解除し、curl failure body/stdout/stderr、`/proc/*/cmdline`、tmpへcanary 0。
  - JWT/NextAuth/OAuthからAdmin key/固定fallbackを削除。欠落時503でsign/verify 0。direct loginは`VEXA_ENV=development`かつliteral `true`だけ、production trueはpreflight failure。
  - MinIO/S3 backend時だけstorage credentialを必須化。local/gcsには要求しない。
  - secret rotationは各release OP gateの必須sub-stepとする。new path成功→legacy admission/deployment generation freeze→intended service pairだけへnew値発行→old値revoke→old canary reject→旧Secret ref/active session 0の順で、旧値をrepo/evidenceへ保存しない。storage/provider/proxy/Zoomを含め、codeから参照を消しただけで旧credentialを有効なまま残さない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-05F`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-05F`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `tests3/unit/test_secret_preflight.py::{test_all_profiles_reject_empty_placeholder_and_equal_secrets,test_rendered_profiles_contain_no_literal_default_secret,test_direct_login_is_disabled_without_explicit_test_flag,test_secret_preflight_reports_names_not_values}`
  - `tests3/unit/refactor/test_rf_05f.py::{test_storage_credentials_are_required_only_for_selected_backend,test_capability_signing_secret_distribution_matches_exact_service_matrix,test_generated_workloads_receive_tokens_but_never_signing_secrets,test_lite_secret_file_is_reused_with_mode_0600_and_no_log_leak}`
  - `tests3/unit/refactor/test_rf_05f.py::{test_vexa_lite_uses_explicit_secret_refs_and_dashboard_has_no_admin_token,test_vexa_lite_has_nonempty_security_context_and_rejects_managed_production,test_admin_mcp_oauth_webhook_secret_distribution_matches_exact_matrix}`
  - `tests3/unit/refactor/test_bot_debug_secret_transport.py::{test_uses_private_mktemp_and_cleans_on_success_failure_and_signal,test_admin_and_user_tokens_exist_only_in_curl_config_fd_not_argv_env_or_log,test_predictable_tmp_cache_and_bot_debug_token_env_are_absent}`
  - `tests3/unit/refactor/test_rf_05f.py::{test_calendar_scheduler_secret_exists_only_in_calendar_and_gateway,test_voiceprint_token_exists_only_in_meeting_and_voiceprint_and_production_never_allows_unauthenticated,test_key_ring_uses_exact_kid_and_hs256_without_key_scanning,test_no_kid_compat_accepts_only_exact_current_strict_token_and_counts_by_audience,test_previous_key_verifies_only_during_declared_drain_window,test_phase_one_final_render_has_zero_previous_key_refs_and_no_kid_accept,test_callback_hash_record_contains_key_id}`
  - secretなしCompose config non-zero、valid fixture preflight/Helm render exit 0。
  - empty/placeholder/同値/production direct-login=trueは失敗。Lite再起動2回でsecret再利用・mode0600・log canary 0。
  - deny-list/test fixture以外に既知default文字列0、実secretのcommand line/git/evidence出力0。
  - `V-BACKEND`, `V-DASH`, `V-OPS`。
- リスクと戻し方: default依存環境が起動しなくなる。先にbootstrap/preflightを配布し値を用意してからrolloutし、rollbackでdefaultを復活せず不足Secretを補う。
- 依存: RF-05A, RF-05D2, RF-05E, RF-03A, RF-00C, RF-00D
- コミット: `RF-05F remove insecure deployment credential defaults`

### RF-05G MeetingToken署名鍵をAdmin全権keyから分離

- 対象:
  - `services/meeting-api/meeting_api/meetings.py:79-116`
  - `services/meeting-api/meeting_api/collector/processors.py:25-65`
  - `services/meeting-api/README.md:66,90-115`
  - `deploy/compose/docker-compose.yml:90-140,260-285`
  - `deploy/lite/supervisord.conf:100-150`
  - `deploy/helm/charts/vexa/{values.yaml,templates/secret.yaml,templates/deployment-meeting-api.yaml}:1-末尾`
  - 新規 `services/meeting-api/tests/test_meeting_token_signing.py:1-末尾`
- 問題: MeetingTokenをAdmin API全権keyで署名・検証し、Meeting serviceへAdmin keyを配る。Meeting侵害がAdmin全権侵害へ拡大する。
- 変更:
  - `MEETING_TOKEN_SIGNING_SECRET`,`MEETING_TOKEN_SIGNING_KID`と任意の`MEETING_TOKEN_SIGNING_PREVIOUS_SECRET`,`MEETING_TOKEN_SIGNING_PREVIOUS_KID`を導入する。current/previousは32 byte以上、ID/値は相互非同値、Admin/Internal/JWT/Runtime/Gateway各secretとも非同値。未設定/同値はproduction startup failure。new issuerはHS256 header `kid=current`を必須で付け、verifierはknown `kid`で1鍵だけを選ぶ。
  - claimとalgorithmは既存の`HS256`、`iss=meeting-api`、`aud=transcription-collector`、`scope=transcribe:write`、`meeting_id,user_id,platform,native_meeting_id,iat,exp,jti`を維持する。new verifierをcollectorへ先行配備し、known `kid` tokenに加えて、RF-05G配備前にAdmin keyで発行済みのno-kid tokenだけをstrict claim/audience/最大2時間TTLで`legacy_admin_signed`として一時acceptする。legacy branchは専用Admin verification key refをcollector verifierだけにread-only配布し、Meeting issuerは一切読まず、credential値なしcounterを記録する。`kid`付きAdmin署名、missing claim、TTL超過、unknown kidはrejectする。
  - Compose/Lite/HelmはMeeting API/collector processへnew signing key ringを配り、collectorだけへ一時legacy Admin verification refを配る。Meeting issuerから`ADMIN_TOKEN`/`ADMIN_API_TOKEN`を削除し、new issuerへ切替える。Admin API tokenはAdmin API、RF-04Bのadmin BFF、上記read-only legacy verifier以外へ配らない。
  - OP-06C-DRAINは`max_legacy_meeting_token_ttl_seconds=7200`、clock skew/retry/active old collector session lifetimeを含む観測期間、`legacy_admin_signed_count=0`,`active_old_collector_sessions=0`を証明する。RF-05G2がlegacy verifier/Admin refを削除するまでAdmin-signed tokenを即時rejectしない。new key rotationはknown previous/current verifier先行→issuer current切替→最大TTL+clock skew+retry経過→previous ref除去。値はlog/evidence/command lineへ出さない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-05G`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-05G`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/meeting-api/tests/test_meeting_token_signing.py::{test_current_and_previous_kid_select_exact_signing_secret,test_legacy_no_kid_admin_token_is_strictly_accepted_and_counted_only_during_compatibility,test_kid_admin_unknown_missing_claim_and_overlong_legacy_tokens_are_rejected,test_claims_and_wire_contract_are_unchanged,test_missing_equal_or_placeholder_secret_fails_closed}`
  - Compose/Lite/Helm render testでMeeting issuer envにAdmin token 0、Admin envにsigning secret 0、collector/Meetingだけnew signing ref、collectorだけtemporary legacy verification refあり。
  - new current/previous tokenは既存collector pathで成功。strict legacy canaryだけcompat counter exact 1、他Admin署名fixtureはcollector write 0。
  - `rg -n 'ADMIN_TOKEN|ADMIN_API_TOKEN' services/meeting-api/meeting_api/meetings.py services/meeting-api/meeting_api/collector deploy/helm/charts/vexa/templates/deployment-meeting-api.yaml` はnegative fixture以外0件。
  - `V-MEETING`, `V-BACKEND`, `V-OPS`。
- リスクと戻し方: deploy順違いでcollectorが新tokenを拒否する。collector compat verifier→new issuerの順に配布し、legacy verifierはOP-06C-DRAIN前に削除しない。失敗branchを保持しRF-05FのSHAから再実行する。
- 依存: RF-05F
- コミット: `RF-05G separate meeting token signing from admin authority`

### RF-05H Meeting Botへglobal internal secretを渡さずsession capabilityへ置換

- 対象:
  - `services/meeting-api/meeting_api/callbacks.py:1-末尾`
  - `services/meeting-api/meeting_api/collector/auth.py:1-末尾`
  - `services/meeting-api/meeting_api/collector/endpoints.py:1-末尾`
  - `services/meeting-api/meeting_api/config.py:1-末尾`
  - `services/meeting-api/meeting_api/container_stop_outbox.py:1-末尾`
  - `services/meeting-api/meeting_api/main.py:1-末尾`
  - `services/meeting-api/meeting_api/meetings.py:750,819-828,1104-1147,1210`
  - `services/meeting-api/meeting_api/post_meeting.py:1-末尾`
  - `services/meeting-api/meeting_api/recording_finalizer.py:1-末尾`
  - `services/meeting-api/meeting_api/recordings.py:1-末尾`
  - `services/meeting-api/meeting_api/schemas.py:1-末尾`
  - `services/meeting-api/meeting_api/sweeps.py:1-末尾`
  - `services/meeting-api/meeting_api/webhooks.py:1-末尾`
  - `services/vexa-bot/core/src/services/unified-callback.ts:177-178`
  - `services/vexa-bot/core/src/docker.ts:98`
  - `services/runtime-api/profiles.yaml:77`
  - `deploy/compose/docker-compose.yml:1-末尾`
  - `deploy/lite/supervisord.conf:1-末尾`
  - `deploy/helm/charts/vexa/templates/deployment-admin-api.yaml:1-末尾`
  - `deploy/helm/charts/vexa/templates/deployment-api-gateway.yaml:1-末尾`
  - `deploy/helm/charts/vexa/templates/deployment-meeting-api.yaml:1-末尾`
  - `deploy/helm/charts/vexa/templates/secret.yaml:1-末尾`
  - read-only inventory: `git grep -n -E -e 'callback|INTERNAL_API_SECRET' -- services/meeting-api/meeting_api`と`git grep -n -E -e 'INTERNAL_API_SECRET|BOT_CONFIG' -- deploy/compose deploy/lite deploy/helm services/runtime-api/profiles.yaml`。期待pathは上記20既存fileだけで、別pathが1件でもあれば停止してplan reviewへ戻る
  - 新規 `services/meeting-api/tests/test_callback_capability.py:1-末尾`
  - 新規 `services/vexa-bot/core/src/meeting-proxy-options.test.ts:1-末尾`
- 問題: Meeting Bot config/envへglobal `INTERNAL_API_SECRET`を渡すため、1 container侵害で全service internal surfaceのcredentialが漏れる。
- 変更:
  - Bot起動ごとに32-byte random callback capabilityを作る。Meeting serviceだけが持つ`BOT_CALLBACK_CAPABILITY_HASH_SECRET`でcanonical length-prefixed `v=1,aud="bot-callback",meeting_id,user_id,session_uid,bot_instance_id,operations={"callback"},iat,exp,raw_token`全体をHMAC-SHA256し、Redisへ `bot-callback-capability:<meeting_id>:<bot_instance_id>` のdigest/audience/subject/operation/expだけを保存する。`exp=min(resolved_bot_deadline+3600,iat+28800)`、clock skew 5秒とし、deadline欠落/過去/8時間超過は上限へ丸めず発行前422。plain SHA-256やtokenだけのHMACを禁止し、raw token/hash secretをDB/Redis/log/evidenceへ保存しない。
  - runtime specのbot configへ`callbackToken`だけを渡し、Botはcallback routeへ`X-Bot-Callback-Token`として送る。route path/bodyのmeeting/bot identityとRedis keyが一致する場合だけ、constant-time比較後にそのcallback操作だけを許す。
  - Meeting自身/他serviceの内部callは従来のstrong `X-Internal-Secret`を利用できる。callback dependencyだけが2 credential typeを明示的に扱い、capabilityを他internal endpointへ使えない。
  - Bot config/env、runtime meeting profile、Docker helperからglobal `INTERNAL_API_SECRET`注入/fallbackを削除する。rolling deployはMeeting dual-accept→Bot callbackToken対応→token発行有効→Bot env削除の順。
  - terminal callback後にcapability keyを削除し、duplicate callbackは既存idempotency契約の範囲だけ処理する。Redis障害時はglobal fallbackへ戻らず503。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-05H`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-05H`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/meeting-api/tests/test_callback_capability.py::{test_wrong_audience_user_meeting_session_bot_replay_expiry_and_redis_failure_have_zero_side_effects,test_deadline_ttl_is_capped_at_eight_hours_and_invalid_deadline_is_rejected,test_terminal_callback_revokes_capability,test_redis_record_copy_or_digest_tamper_cannot_forge_another_meeting_capability}`
  - `services/vexa-bot/core/src/services/unified-callback.test.ts::{test_callback_uses_session_token_only,test_global_internal_fallback_is_absent}`
  - generated Meeting Bot/runtime spec/envに`INTERNAL_API_SECRET` 0、Agent/browserもRF-05Cの0を維持。
  - capability raw canaryはRedis hash値、DB、log、exceptionに0件。
  - `V-MEETING`, `V-CORE`, `V-BACKEND`。
- リスクと戻し方: deploy順不一致でcallback停止。dual-accept期間のMeetingを先に配置し、global secretをBotへ戻して復旧しない。順序を満たせない場合は中断。
- 依存: RF-05A, RF-05G
- コミット: `RF-05H scope bot callbacks with per-session capabilities`

### RF-06A Agent workspace storageをservice-side brokerへ移す

- 対象:
  - `services/agent-api/agent_api/container_manager.py:151-158`
  - `services/agent-api/agent_api/main.py:1-末尾`
  - `services/agent-api/agent_api/workspace.py:1-120`
  - `services/runtime-api/runtime_api/api.py:1-末尾`
  - `services/runtime-api/runtime_api/backends/__init__.py:1-末尾`
  - `services/runtime-api/runtime_api/backends/docker.py:1-末尾`
  - `services/runtime-api/runtime_api/backends/kubernetes.py:1-末尾`
  - `services/runtime-api/runtime_api/backends/process.py:1-末尾`
  - `services/agent-api/README.md:1-末尾`
  - read-only inventory: `git grep -n -E -e 'archive|workspace.*(upload|download)|s3 sync' -- services/agent-api services/runtime-api`。期待pathは上記write対象だけで、third-party license等の非実装一致はpath/lineをevidenceへ保存する。別production callerが1件でもあれば停止してplan reviewへ戻る
  - 新規 `services/agent-api/tests/test_workspace_archive_broker.py:1-末尾`
  - 新規 `services/runtime-api/tests/test_archive_adapters.py:1-末尾`
- 問題: generated Agent containerへbucket-wide AWS/MinIO credentialを入れ、container内`aws s3 sync`を実行するため、利用者/AI shellから他user prefixへ到達できる。
- 変更:
  - S3/MinIO credentialはAgent API service processだけが持つ。container specからAWS/S3 access/secret/session tokenを全削除する。
  - RuntimeへRF-05D2 service-auth済みarchive transfer APIを追加し、Agentのtrusted subjectからAgent serviceが決めたowner/containerと固定workspace rootだけをtar streamでupload/downloadする。Runtime request bodyの`user_id`やcontainer名だけを権限根拠にせず、clientは任意host/path/commandを指定できない。
  - restoreはAgent APIが自user prefixからobjectをstreamし、saveはRuntimeからtarをstreamして同じvalidator後に自user prefixへmultipart uploadする。directoryとregular fileだけを許し、absolute/`..`/NUL/drive prefix、symlink/hardlink、device/FIFO/socket、GNU sparse、PAX/global-PAXのpath/linkpath/size override、duplicate canonical path、case/Unicode normalization collisionをextract前にrejectする。`extractall()`を使わず、事前にregular directoryとして作ったworkspace rootのdirfdから`openat`/`mkdirat`（利用可能なら`openat2 RESOLVE_BENEATH|NO_SYMLINKS`）で各componentを`O_NOFOLLOW`照合し、pre-existing symlinkとextract中のrename/symlink raceをfail closedにする。fileはtemp siblingへ`O_EXCL`でstream→size/hash verify→same-dir atomic renameし、ownership/modeはserver allow-listからだけ設定する。上限はuncompressed合計2 GiB、単一file 512 MiB、entry 100,000件、path UTF-8 4,096 byte、stream chunk 1 MiB、multipart part 16 MiB、全体900秒、無通信60秒に固定する。超過は413/408、partial object/temp archiveを必ず削除する。
  - bucket list/deleteはAgent serviceが`users/<resolved_user_id>/workspace/` prefixへ明示制限し、container name/body user IDを権限根拠にしない。
  - local backendも同じbroker interfaceを使い、container内credential fallbackを作らない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-06A`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-06A`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/agent-api/tests/test_workspace_storage_broker.py::{test_cross_user_prefix_is_rejected_before_storage,test_round_trip_preserves_path_mode_and_content_hash,test_total_file_entry_path_and_idle_limits_cleanup_partial_upload}`
  - `services/runtime-api/tests/test_workspace_archive_api.py::{test_body_user_id_cannot_override_service_bound_owner,test_malicious_tar_never_writes_outside_workspace,test_links_pax_sparse_devices_duplicates_preexisting_symlink_and_races_are_rejected,test_cancel_removes_temp_archive_and_multipart_upload}`
  - A container/tokenからB prefixのlist/get/put/deleteは全て403/404・storage call 0。
  - malicious tar（`../`、absolute、全link種、PAX/sparse、device/FIFO/socket、duplicate、pre-existing symlink、rename/symlink race、oversize）をrejectしworkspace外write 0。
  - container inspect/env、runtime spec、log、exceptionにstorage credential canary 0。
  - round-trip fixtureのfile path/mode/content hash一致。
  - `V-BACKEND`。
- リスクと戻し方: 大容量workspaceのmemory/timeout差。全処理をbounded streamingにし、bytes全読込を禁止。credentialをcontainerへ戻して救済せず、失敗時はworkspace save機能を停止して前SHAから再実行。
- 依存: RF-03A, RF-05D2
- コミット: `RF-06A broker agent workspace storage outside generated containers`

### RF-06B Browser userdata storageをsession-scoped brokerへ移す

- 対象:
  - `services/meeting-api/config/profiles.yaml:1-末尾`
  - `services/meeting-api/meeting_api/meetings.py:797-812,1131-1140,1283-1360`
  - 新規 `services/meeting-api/meeting_api/browser_storage_broker.py:1-末尾`
  - 新規 `services/meeting-api/meeting_api/browser_storage_repository.py:1-末尾`
  - `services/meeting-api/tests/test_meetings.py:1-末尾`
  - `services/vexa-bot/README.md:1-末尾`
  - `services/vexa-bot/core/entrypoint.sh:1-末尾`
  - `services/vexa-bot/core/src/BROWSER-SESSION.md:1-末尾`
  - `services/vexa-bot/core/src/browser-session.ts:1-末尾`
  - `services/vexa-bot/core/src/docker.ts:1-末尾`
  - `services/vexa-bot/core/src/index.ts:1-末尾`
  - `services/vexa-bot/core/src/s3-sync.ts:1-116`
  - 新規 `services/vexa-bot/core/src/browser-storage-client.ts:1-末尾`
  - read-only既知一致: `services/vexa-bot/{Dockerfile.experiment,core/entrypoint-experiment.sh,core/src/platforms/hot-debug.sh,hot-run.sh,run-zoom-bot.sh}:1-末尾`の一般BOT_CONFIG/debug処理、`services/vexa-bot/docs/recording-pipeline.md:1-末尾`のrecording記述
  - read-only inventory: `git grep -n -F -e 'BOT_CONFIG' -e 's3-sync' -- services/meeting-api services/vexa-bot`。上記write対象またはread-only既知一致以外のproduction/config callerが1件でもあれば停止してplan reviewへ戻る
  - 新規 `services/meeting-api/tests/test_browser_storage_broker.py:1-末尾`
  - 新規 `services/vexa-bot/core/src/browser-storage-client.test.ts:1-末尾`
- 問題: browser-session containerへbucket-wide MinIO access/secretを渡し、prefixは規約だけで強制されない。
- 変更:
  - RF-05Hのcallback tokenとは別に、Meeting serviceだけが持つ`BROWSER_STORAGE_CAPABILITY_SIGNING_SECRET`でHS256署名したopaque browser-storage capabilityを発行する。claimは`v=1,iss=meeting-api,aud=browser-storage-broker,operations=["browser_storage"],sub=browser:<session_uid>,resolved_user_id,prefix,meeting_id,session_uid,iat,nbf,exp,jti`のexact allow-listとし、unknown claim/algorithmをrejectする。`exp=min(session_absolute_expiry,iat+86400)`、clock skew 5秒とし、session terminal、idle timeout、明示logoutのいずれでもjtiを即時revokeする。Redisにはjti revocation/expiryだけを保存し、client入力のdigest/metadataを権限根拠にしない。callback tokenは`operations={"callback"}`だけ。Meeting APIへdownload/upload broker routeを追加し、2 tokenを相互利用できない。
  - CoreのS3 syncをbroker HTTP clientへ置換し、browser userdataをbounded tar streamで取得/保存する。上限はuncompressed合計2 GiB、単一file 512 MiB、entry 100,000件、path 4,096 byte、chunk 1 MiB、全体300秒、無通信30秒。RF-06Aと同じdirectory/regular-file-only、全link/PAX/sparse/device/FIFO/socket/duplicate/path normalization/pre-existing symlink/race reject、dirfd `openat`/`O_NOFOLLOW` extractionを共通vectorで必須にし、`extractall()`を使わない。coreへbucket endpoint/access/secretを渡さない。
  - Meeting serviceだけがMinIO/S3 credentialを持ち、`users/<resolved_user_id>/browser-userdata/`以外のlist/get/put/deleteを組み立てられないtyped repositoryを使う。
  - browser-session `BOT_CONFIG`から`s3AccessKey/s3SecretKey`とglobal internal secretを削除し、broker URL + session capabilityだけを渡す。終了時capability失効後のuploadはreject。
  - 既存save UI/Redis request-responseはRF-09Bまで維持し、Core内部の保存先transportだけを変える。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-06B`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-06B`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/meeting-api/tests/test_browser_storage_broker.py::{test_capability_is_bound_to_user_meeting_and_session,test_ttl_uses_session_absolute_expiry_and_one_day_cap,test_terminal_idle_and_logout_revoke_immediately,test_callback_and_storage_capabilities_are_not_interchangeable,test_archive_rejects_links_pax_sparse_devices_duplicates_preexisting_symlink_and_races_before_storage,test_archive_limits_and_path_rules_fail_before_storage,test_round_trip_preserves_userdata_hash_and_mode,test_tampered_redis_metadata_or_forged_digest_never_authorizes_storage}`
  - `services/vexa-bot/core/src/browser-storage-client.test.ts::{test_uses_broker_without_s3_credentials,test_timeout_and_cancel_cleanup_partial_stream}`
  - A session capabilityからB user/meeting prefixの全operation拒否・storage call 0。
  - callback tokenをstorage routeへ、storage tokenをcallback routeへ送るfixtureは403・副作用0。
  - container env/BOT_CONFIG/inspect/logにMinIO/S3/internal credential canary 0。
  - userdata round-trip hash/mode一致、oversize/path traversal/symlink escape拒否。
  - `V-MEETING`, `V-CORE`, `V-BACKEND`。
- リスクと戻し方: browser profile復元/保存がbroker到達性へ依存する。dual transportをcontainer credential付きで残さず、Meeting先行→Core image→config切替の順。失敗時は新session作成を停止し前SHAから再実行。
- 依存: RF-05H, RF-06A
- コミット: `RF-06B broker browser storage with session-scoped capabilities`

### RF-06C1 Redisをauthenticated service principalへ互換移行

- 対象:
  - `deploy/compose/docker-compose.yml:1-末尾`
  - `deploy/lite/{entrypoint.sh,supervisord.conf}:1-末尾`
  - `deploy/helm/charts/vexa/{values.yaml,templates/_helpers.tpl,templates/secret.yaml,templates/deployment-redis.yaml,templates/service-redis.yaml}:1-末尾`
  - 新規 `deploy/helm/charts/vexa/templates/configmap-redis-acl.yaml:1-末尾`
  - RF-05D1の `deploy/helm/charts/vexa/templates/networkpolicy-workloads.yaml:1-末尾`
  - `services/agent-api/agent_api/config.py:1-末尾`
  - `services/agent-api/agent_api/main.py:1-末尾`
  - `services/api-gateway/main.py:1-末尾`
  - `services/meeting-api/config/profiles.yaml:1-末尾`
  - `services/meeting-api/meeting_api/config.py:1-末尾`
  - `services/meeting-api/meeting_api/main.py:1-末尾`
  - `services/meeting-api/meeting_api/meetings.py:1-末尾`
  - `services/runtime-api/profiles.yaml:1-末尾`
  - `services/runtime-api/runtime_api/config.py:1-末尾`
  - `services/runtime-api/runtime_api/main.py:1-末尾`
  - `services/telegram-bot/bot.py:1-末尾`
  - `services/agent-api/tests/test_g5_gate.py:1-末尾`
  - `services/meeting-api/tests/collector/conftest.py:1-末尾`
  - `services/meeting-api/tests/conftest.py:1-末尾`
  - `services/runtime-api/tests/test_api.py:1-末尾`
  - `services/runtime-api/tests/test_backends.py:1-末尾`
  - `services/runtime-api/tests/test_integration_process.py:1-末尾`
  - `services/runtime-api/tests/test_lifecycle.py:1-末尾`
  - `services/runtime-api/tests/test_scheduler_api.py:1-末尾`
  - `services/runtime-api/tests/test_state.py:1-末尾`
  - `services/telegram-bot/tests/conftest.py:1-末尾`
  - `services/runtime-api/runtime_api/backends/docker.py:1-末尾`
  - `services/runtime-api/runtime_api/backends/kubernetes.py:1-末尾`
  - read-only既知一致: `services/{agent-api,api-gateway,meeting-api,runtime-api}/README.md:1-末尾`と`services/runtime-api/.github/workflows/ci.yml:1-末尾`
  - read-only inventory: `git grep -n -E -e 'REDIS_URL|redis://|Redis\(' -- services/meeting-api services/runtime-api services/api-gateway services/agent-api services/telegram-bot`。上記write対象またはread-only既知一致以外が1件でもあれば停止してplan reviewへ戻る
  - 新規 `tests3/unit/refactor/test_rf_06c1.py:1-末尾`
  - 新規 `services/meeting-api/tests/test_collector_redis_acl.py:1-末尾`
- 問題: 現Compose/Helm Redisは匿名接続でき、全workloadと同一network/namespaceにいる。envからURLを消すだけでは侵害containerが`redis:6379`を推測してbrokerを迂回できる。
- 変更:
  - permanent Redis ACL userを`meeting-api,runtime-api,api-gateway,agent-api,admin-api,mcp,calendar-service,telegram-bot,meeting-bot-legacy,browser-session-legacy`へ固定し、各passwordを別Secret keyにする。R1の`migrate-transcript-shares,migrate-telegram-map` principal/password/Secret refはOP-05Aで削除済みであることをbootstrap時に検査し、このmatrixへ再作成しない。全userは`resetkeys resetchannels -@all`から構築し、接続に実traceで必要な`AUTH,HELLO,PING,QUIT,CLIENT|SETINFO`と、DB番号が0以外の場合だけ`SELECT`を加える。`COMMAND,KEYS,SCAN,EVAL,EVALSHA,FUNCTION,FCALL,PSUBSCRIBE`とbroad `CLIENT`は全permanent userで禁止し、`~*`,`&*`,`+@all`をrenderへ1件も残さない。
  - exact ACL matrixを次に固定する。
    - `meeting-api`: keys `active_meetings,transcription_segments,speaker_events_relative,meeting:*,meeting_session:*,speaker_events:*,browser_session:*,va:meeting:*,webhook:retry_queue,meeting-api:container-stops,meeting-api:container-stop-dlq,identity-jti:meeting-api:*,capability:*,capability-session:*`; channels `bm:meeting:*:status,tc:meeting:*:mutable,bot_commands:meeting:*,browser_session:*`、RF-06C2でchannels `meeting:*:segments,va:meeting:*`を追加。現行`voice_agent.py`の`LRANGE va:meeting:{id}:event_log`に必要なkey grantはC1から含める。commands `GET,SET,DEL,EXPIRE,HGET,HGETALL,HSET,HDEL,SADD,SREM,SMEMBERS,ZADD,ZRANGEBYSCORE,LRANGE,LLEN,LPOP,RPUSH,LTRIM,XADD,XRANGE,XDEL,XGROUP|CREATE,XREADGROUP,XACK,XPENDING,XCLAIM,XINFO|GROUPS,XINFO|CONSUMERS,PUBLISH,SUBSCRIBE,UNSUBSCRIBE,WATCH,UNWATCH,MULTI,EXEC`。broad `XGROUP`,`XINFO`や他subcommandはgrantしない。
    - `runtime-api`: keys `runtime:container:*,runtime:callback:*,runtime:process:*,runtime:reservation:*,runtime:index:*,scheduler:jobs,scheduler:executing,scheduler:history,scheduler:idem:*,browser_session:*`; commands `GET,SET,DEL,SADD,SREM,SMEMBERS,ZADD,ZRANGE,ZRANGEBYSCORE,ZREM,HGET,HGETALL,HSET,HDEL,WATCH,UNWATCH,MULTI,EXEC`。現`scan_iter`はC1内で`runtime:index:{containers,callbacks,processes}`をbackfillし件数一致後に削除する。`browser_session:*`はRF-09Bまでの一時cleanup例外で、最終はMeeting typed cleanupへ移してACLから外す。
    - `api-gateway`: keys `ratelimit:*,gateway:token:*,share:transcript:*,share-by-token:*,browser_session:*,agent:sessions:*,identity-jti:api-gateway:*,mcp-assertion-jti:*,calendar-assertion-jti:*,telegram-assertion-jti:*,telegram-link:*,telegram-user-map:*,oauth-state:*`; read-only channels `tc:meeting:*:mutable,bm:meeting:*:status,va:meeting:*:chat`; commands `GET,SET,DEL,HGET,ZREMRANGEBYSCORE,ZADD,ZCARD,EXPIRE,SUBSCRIBE,UNSUBSCRIBE,WATCH,UNWATCH,MULTI,EXEC`。`PUBLISH`なし。
    - `agent-api`: keys `agent:session:*,agent:sessions:*,identity-jti:agent-api:*`; commands `GET,SET,DEL,EXPIRE,HGET,HGETALL,HSET,HDEL`。
    - `admin-api`: key `identity-jti:admin-api:*`; command `SET`だけ。GatewayのAdmin identityとMeeting webhook identityのjtiを同audience namespaceでconsumeし、他key/command 0。
    - `mcp`: key `identity-jti:mcp:*`; command `SET`だけ。
    - `calendar-service`: key `identity-jti:calendar-service:*`; command `SET`だけ。
    - `telegram-bot`: key `telegram-user-map:*`; command `GET`だけ。link/map write、旧`telegram:*`、`SCAN`、raw user token 0。
    - `meeting-bot-legacy`（RF-06C2まで）: keys `transcription_segments,speaker_events_relative,meeting:*`; channels `meeting:*:segments,tc:meeting:*:mutable,va:meeting:*,bot_commands:meeting:*`; commands `XADD,SET,DEL,RPUSH,LTRIM,EXPIRE,PUBLISH,SUBSCRIBE,UNSUBSCRIBE`。
    - `browser-session-legacy`（RF-09Bまで）: key `meeting:*:chat_messages`; channels `browser_session:*,bot_commands:meeting:*,va:meeting:*:chat`; commands `RPUSH,EXPIRE,PUBLISH,SUBSCRIBE,UNSUBSCRIBE`。
  - この互換commitではRedis `default` userを一時的に`on nopass`のまま残し、ACL userを先行作成する。service clientを各principal URLへ切り替え、generated Meeting/Browserだけは後続cutover用legacy userを優先する。credential値でなくprincipal別connection counterを記録する。
  - Runtime backendはgenerated workloadへ`runtime.managed=true`と`runtime.profile=meeting|browser-session|agent` labelをDocker/Kubernetes双方で付ける。Compose/Lite/Helmへ`infra`/`workload` network/NetworkPolicyの定義を先行追加するが、このcommitではlegacy workload到達をまだdenyしない。
  - rolloutはRedis ACL定義→service principal client→legacy workload principalの順。anonymous counterと各principal counterを`.pipeline/evidence/$TASK/operations/redis-c1-drain.json`へ値なしで保存する。RF-06C2のoperator gateまで`default off`やlegacy revokeを行わない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-06C1`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-06C1`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `tests3/unit/refactor/test_rf_06c1.py::{test_exact_redis_acl_principals_and_dangerous_command_denies,test_each_service_receives_only_own_redis_secret_ref,test_runtime_assigns_exact_managed_profile_labels,test_compatibility_keeps_default_and_two_legacy_users_only_until_next_gate,test_redis_secret_values_never_enter_render_logs_or_workload_args}`
  - `tests3/unit/refactor/test_rf_06c1.py::{test_acl_dryrun_exact_positive_vectors_and_cross_principal_negative_matrix,test_meeting_voice_event_log_lrange_is_allowed_only_for_meeting_principal,test_meeting_consumer_pending_claim_and_xinfo_subcommands_are_exactly_allowed,test_render_contains_no_broad_key_channel_category_eval_keys_scan_or_command_grant,test_runtime_backfills_explicit_indexes_then_removes_scan,test_calendar_jti_oauth_state_admin_mcp_and_telegram_nonsecret_mapping_have_minimal_acl,test_admin_mcp_and_gateway_jti_keys_are_cross_principal_isolated,test_permanent_telegram_acl_has_get_only_and_no_scan,test_r1_temporary_migration_principals_secrets_and_scan_grants_are_absent}`
  - `services/meeting-api/tests/test_collector_redis_acl.py::{test_stale_pending_entry_is_claimed_and_acked_with_meeting_principal,test_collector_health_reads_group_and_consumer_info_with_exact_subcommands,test_cross_principal_and_unlisted_xinfo_subcommands_are_noperm}`
  - ACL-enabled local Redis fixtureで`ACL DRYRUN <user> <exact command...>`の正規vector全成功、各principalから他principalの全key/channel canaryをGET/SET/PUBLISH/SUBSCRIBEして全NOPERM・副作用0。production-like traceの実command/key/channel集合が宣言matrixのsubset、unknown 0。wrong principal/passwordはNOAUTH、secret値log 0。
  - Compose/Helm renderでserviceは自principal secret refだけ、Meeting/Browser legacy workload以外のgenerated workloadはRedis credential 0。
  - `V-BACKEND`, `V-INTEGRATIONS`, `V-OPS`。
- リスクと戻し方: client inventory漏れでRedis依存serviceが停止する。default userを閉じる前のcompatibility itemなので、counterが残ればRF-06C2へ進まずclient inventoryをこのcommit内で直す。失敗branchを保持しRF-06BのSHAから再実行する。
- 依存: RF-05F, RF-06B
- コミット: `RF-06C1 introduce authenticated redis principals before closing anonymous access`

#### OP-06C-DRAIN（非commit・人間operator停止点）

RF-06C1 commitを実環境へcomponent単位で配備する権限がない実行者はここで停止する。fixture counterを実drainの代用にしてRF-05F2へ進んではいけない。認可済みoperatorが共通`operation-gate-v1`で次を提供する。

- Redis: `anonymous_connection_count=0`、`service_principal_success_by_principal={meeting-api:>0,runtime-api:>0,api-gateway:>0,agent-api:>0,calendar-service:>0,telegram-bot:>0}`、`meeting_bot_legacy_count>=0`,`browser_session_legacy_count>=0`,`max_redis_reconnect_interval_seconds`。Meeting/Browser workload legacyは後続brokerをまだ配備していないため、このgateで0を要求しない。
- identity key ring: RF-05Fの全audienceについて`legacy_no_kid_count_by_audience`が各0、`kid_current_success_by_audience`が各`>0`、観測時間が`max(30秒TTL,clock skew,max retry)+300`秒以上。
- MeetingToken/Admin key: `legacy_admin_signed_count=0`,`current_kid_success_count>0`,`active_old_collector_sessions=0`、観測時間が`max(7200秒legacy TTL,clock skew,retry window,old collector session lifetime)+300`秒以上。collectorからlegacy Admin refを外す前にAdmin current keyを新値へrotateし、全正規Admin consumer別`admin_current_key_success_by_consumer`が各`>0`、旧値canaryは全consumerでreject、`admin_previous_or_old_ref_count=0`,`active_admin_session_signed_by_old_key=0`を同じenvironment/freeze leaseで証明する。aggregate successを個別consumerの代用にせず、key値はartifactへ保存しない。
- storage/Git: RF-06A/B broker path successがAgent/Browser各`>0`、旧S3/MinIO credentialをnew broker専用credentialへrotate/revoke済み、old-value canary reject、生成workloadのold/new storage ref 0、RF-03Dで検出したcredential URL tokenのrotation完了件数=quarantine件数。

aggregate successを個別principal/audienceの代用にしない。`bash scripts/test/run-refactor-operator-gate.sh --master-task full-repo-refactoring-2026-07-24 --archive-root "$CONTROL_ROOT/.pipeline/release-archives" --release r3 --gate op-06c-drain`がexit 0になるまで進まない。

### RF-05F2 no-kid identity互換をdrain後に削除

- 対象:
  - `services/api-gateway/main.py:1-末尾`
  - `services/calendar-service/app/{main,sync}.py:1-末尾`
  - `services/agent-api/agent_api/auth.py:1-末尾`
  - `services/meeting-api/meeting_api/auth.py:1-末尾`
  - `services/admin-api/app/main.py:1-末尾`
  - `services/mcp/main.py:1-末尾`
  - `services/{transcription-service,tts-service,voiceprint-service}/main.py:1-末尾`
  - `services/{wake-stt,wake-orchestrator}/app/main.py:1-末尾`
  - `deploy/compose/docker-compose.yml:1-末尾`
  - `deploy/lite/{entrypoint.sh,supervisord.conf}:1-末尾`
  - `deploy/helm/charts/vexa/{values.yaml,templates/secret.yaml}:1-末尾`
  - 新規 `tests3/unit/refactor/test_rf_05f2.py:1-末尾`
- 問題: RF-05Fはrolling deployのためno-`kid` current-key tokenを一時acceptする。drain後も残せば、key identifierによる選択を迂回する旧wireが恒久化する。
- 変更:
  - OP-06C-DRAINのaudience別counter/hash/観測時間を検証後、`legacy_no_kid` verifier branch、counter、compat configを全audienceから削除する。
  - issuer/verifierはHS256かつknown current/optional previous `kid`だけを許し、missing/null/empty/unknown `kid`はsignature scan前にrejectする。current→previous総当たりを作らない。
  - RF-05F key ringの通常rotation contractは維持し、この項目でcurrent/previous値をrotateしない。previous refが必要な別rotation中なら本項目を開始せず、そのaudienceのoperator drainを完了する。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-05F2`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-05F2`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。
  - `tests3/unit/refactor/test_rf_05f2.py::{test_all_identity_audiences_require_known_kid_without_key_scanning,test_no_kid_null_empty_unknown_and_cross_audience_tokens_have_zero_upstream_or_database_calls,test_render_and_source_have_no_no_kid_compatibility_branch_or_counter}`
  - `V-BACKEND`, `V-OPS`。
- リスクと戻し方: stale issuerが残れば401になる。OP-06C-DRAINをfresh cutover再検証し、失敗時はR3 compat componentを維持してR4を配備せず中断する。no-kid branchを新commitで戻さない。
- 依存: RF-05F, OP-06C-DRAIN
- コミット: `RF-05F2 remove drained no kid identity compatibility`

### RF-05G2 Admin署名MeetingToken互換をdrain後に削除

- 対象:
  - `services/meeting-api/meeting_api/meetings.py:79-116`
  - `services/meeting-api/meeting_api/collector/processors.py:25-65`
  - `services/meeting-api/README.md:66-115`
  - `deploy/compose/docker-compose.yml:90-140,260-285`
  - `deploy/lite/supervisord.conf:100-150`
  - `deploy/helm/charts/vexa/{values.yaml,templates/secret.yaml,templates/deployment-meeting-api.yaml}:1-末尾`
  - `services/meeting-api/tests/test_meeting_token_signing.py:1-末尾`（RF-05G作成物）
  - 新規 `tests3/unit/refactor/test_rf_05g2.py:1-末尾`
- 問題: RF-05Gは既発行tokenを切らないためcollectorへAdmin-key legacy verifierを一時残す。drain後も残せばMeeting侵害からAdmin authorityへ至る旧境界が閉じない。
- 変更:
  - OP-06C-DRAINのlegacy count、active old collector session、最大TTL/skew/retry観測を検証後、legacy Admin verification branch/ref/counterをcollector、Compose/Lite/Helmから削除する。
  - mint/verifyはknown `MEETING_TOKEN_SIGNING_{CURRENT,PREVIOUS}_KID`のexact keyだけを読み、Admin/Internal/JWT key ref/importを0にする。missing/unknown/no-kid tokenはcollector write前401/403。
  - Admin key自体のrotation/revocationはOP-06C証拠で完了済みであることをold-value canary hashで再検証し、値を証拠へ保存しない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-05G2`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-05G2`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。
  - `services/meeting-api/tests/test_meeting_token_signing.py::{test_known_current_and_previous_kid_only,test_admin_signed_no_kid_unknown_and_missing_kid_tokens_have_zero_collector_writes}`
  - `tests3/unit/refactor/test_rf_05g2.py::{test_collector_and_meeting_have_no_admin_key_ref_or_legacy_verifier,test_only_meeting_and_collector_receive_meeting_signing_key_ring}`
  - `V-MEETING`, `V-BACKEND`, `V-OPS`。
- リスクと戻し方: old collector/issuerが残ればtranscription停止。fresh cutover再検証が不合格ならR3 compat deploymentを維持しR4を配備せず中断する。Admin key fallbackを戻さない。
- 依存: RF-05G, RF-05F2, OP-06C-DRAIN
- コミット: `RF-05G2 remove drained admin signed meeting tokens`

### RF-06C2 Meeting Bot event brokerをlegacy互換で先行配備

- 対象:
  - 新規 `services/meeting-api/meeting_api/workload_capabilities.py:1-末尾`
  - 新規 `services/meeting-api/meeting_api/session_capability_registry.py:1-末尾`
  - 新規 `services/meeting-api/meeting_api/bot_event_broker.py:1-末尾`
  - `services/meeting-api/meeting_api/meetings.py:1021-1111`
  - `services/vexa-bot/core/src/services/{segment-publisher,chat}.ts:1-末尾`
  - `services/vexa-bot/core/src/index.ts:534-687,1060-1079,1347-1393,2414-2445`
  - `deploy/compose/docker-compose.yml:1-末尾`
  - `deploy/lite/{entrypoint.sh,supervisord.conf}:1-末尾`
  - `deploy/helm/charts/vexa/{values.yaml,templates/configmap-redis-acl.yaml,templates/networkpolicy-workloads.yaml}:1-末尾`
  - `services/runtime-api/profiles.yaml:1-末尾`
  - 新規 `services/meeting-api/tests/test_bot_event_broker.py:1-末尾`
  - 新規 `services/meeting-api/tests/test_session_capability_registry.py:1-末尾`
  - 既存（RF-06C1で追加済み）`services/meeting-api/tests/test_collector_redis_acl.py:1-末尾`
  - 新規 `services/vexa-bot/core/src/services/event-broker-client.test.ts:1-末尾`
- 問題: RF-06C1は認証移行のためanonymous userとMeeting Bot legacy Redis credentialを残す。broker導入とlegacy閉鎖を同commitにすると、新Botの実trafficを観測する前にrollback経路まで失う。
- 変更:
  - `BOT_EVENT_CAPABILITY_SECRET`から`iss=meeting-api,aud=meeting-event-broker,sub=bot:<bot_instance_id>,meeting_id,user_id,session_uid,operations,iat,nbf,exp,jti`の短命tokenを発行する。operationsは`transcript.write,speaker.write,pending.write,realtime.publish,chat.write,voice_event.write,command.subscribe`だけ、`exp=min(bot deadline+1h,iat+8h)`、clock skew 5秒、同一jti connection 1本。
  - `SessionCapabilityRegistry`をMeetingのserver-owned Redis repositoryとして追加する。発行transactionは`capability:<session_uid>:<aud>:<jti>`へ`owner,meeting_id,bot_instance_id,aud,operations,exp,status=active`をTTL付きSET NXし、session indexへjtiを追加できた場合だけtokenを返す。event brokerは署名検証後・Redis/副作用前にregistryのexact active recordを照合する。RF-06D1/RF-06Hのremote verifierは固定`POST /internal/workload-capabilities/introspect`へaudience別introspection tokenと`jti,aud,session_uid,operation`だけを送り、Meetingが同じ照合を行う。registry/Meeting unavailable・timeout・malformed responseは503、署名だけでfallbackしない。
  - normal terminal、explicit delete/logout、idle timeout、failed、cancelled、sweep cleanupの全経路はbounded audience indexを`WATCH -> active/index再読 -> MULTI -> 全record revoked + terminal marker -> EXEC`で`active -> revoked`へ変更し、全audience/jtiを副作用前に一括失効する。Lua/EVAL/EVALSHAは使わない。WatchErrorはjitter付き最大5回、超過時503・後続副作用0。terminal requestとcapability requestの100並列raceは、terminal linearization point後の副作用0、active token発行0。終了処理再試行は冪等で、index/recordは元expまで監査用に残すがactiveへ戻さない。
  - BotはMeeting semantic brokerだけを呼ぶ。HTTP batchは型付きDTO、command受信はclaim meeting専用SSE/WS。1 event 256 KiB、1 batch 100件/4 MiB、chat 64 KiB、pending speaker 256 byte、session 200 event/秒・burst 400、heartbeat 30秒。claim/path/body不一致、unknown op、oversize/rate超過はRedis call前reject。
  - Brokerが表現できるRedis操作を`XADD transcription_segments`、`XADD speaker_events_relative`、derived pending `SET/DEL`、既存meeting/transcript/chat/voice channelへの`PUBLISH`、chat/event listの`RPUSH/LTRIM/EXPIRE`、claim meeting exactの`SUBSCRIBE bot_commands:meeting:{id}`だけへ固定する。任意key/channel、`PSUBSCRIBE`、管理commandをAPIへ出さない。
  - Coreへbroker clientを追加し、new Meeting Botはbroker URL+capabilityを優先する。R4では旧image/sessionのrollback用Redis client/URL/credential、Redis `default on nopass`、`meeting-bot-legacy`、Meeting profileのRedis到達をcompatibilityとして残すが、new broker fieldが存在するBotはRedisへfallbackせずbroker failureを返す。principal別`event_broker_success`と`meeting_bot_legacy` counterを値なしで記録する。Redis/default/legacy/networkの閉鎖はOP-06D-DRAIN後のRF-06D2だけが行う。
  - `chat.ts`の到達不能`if(false)` transcript XADDは削除し、chatはchat channel/listだけ。secret/URLをlogしない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-06C2`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-06C2`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/meeting-api/tests/test_bot_event_broker.py::{test_operation_route_matrix,test_wrong_meeting_session_bot_audience_and_expiry_make_zero_redis_calls,test_only_fixed_derived_redis_operations_are_possible,test_oversize_and_rate_limits_fail_before_redis}`
  - `services/meeting-api/tests/test_session_capability_registry.py::{test_issue_registers_exact_session_audience_jti_before_returning_token,test_every_terminal_delete_timeout_failure_cancel_path_revokes_all_audiences,test_registry_failure_returns_503_without_signature_only_fallback,test_terminal_and_issue_hundred_way_race_has_no_post_terminal_active_token_or_side_effect,test_introspection_requires_exact_audience_service_token}`
  - `services/vexa-bot/core/src/services/event-broker-client.test.ts::{preserves_existing_payload_and_order,chat_never_writes_transcript_stream,contains_no_raw_redis_transport}`
  - `tests3/unit/refactor/test_rf_06c2.py::{test_new_meeting_bot_prefers_broker_and_never_falls_back_to_redis,test_default_and_meeting_legacy_users_remain_compatibility_only_until_rf_06d2,test_new_broker_field_has_no_redis_secret,test_old_session_fixture_keeps_declared_legacy_path_only}`
  - new image/BOT_CONFIGのbroker-enabled fixtureはRedis call 0で既存payload/order一致。旧image/session fixtureだけlegacy counter exact 1。
  - generated new Meeting Bot env/BOT_CONFIG/inspect/logにRedis URL/password 0。旧session rollback profileとBrowser legacy以外のgenerated workload credential 0。
  - `V-MEETING`, `V-CORE`, `V-BACKEND`, `V-OPS`。
- リスクと戻し方: broker差でlive transcriptが止まる。OP-06C-DRAINなしで開始しない。失敗時はnew session受付を停止してR3 compatibility componentへ戻し、legacy閉鎖を先行しない。
- 依存: RF-06C1, RF-05F2, RF-05G2, OP-06C-DRAIN
- コミット: `RF-06C2 deploy meeting event broker with legacy compatibility`

### RF-06D1 Workload capability validatorとissuerをlegacy互換で先行配備

- 対象:
  - `services/meeting-api/meeting_api/{meetings,recordings}.py:1-末尾`
  - `services/transcription-service/main.py:123-152,273-288`
  - `services/wake-stt/app/main.py:36-45,68-77`
  - `services/wake-orchestrator/app/{config,clients,orchestrator}.py:1-末尾`
  - `services/tts-service/main.py:390-410,447-551`
  - `services/vexa-bot/core/src/{index,types,docker}.ts:1-末尾`
  - `services/vexa-bot/core/src/services/{transcription-client,wake-stt-client,recording,video-recording,tts-playback}.ts:1-末尾`
  - `services/vexa-bot/core/src/platforms/googlemeet/recording.ts:1-末尾`
  - `services/vexa-bot/core/src/platforms/msteams/recording.ts:1-末尾`
  - `services/vexa-bot/core/src/platforms/zoom/web/recording.ts:1-末尾`
  - 新規 `services/meeting-api/tests/test_workload_capability_audiences.py:1-末尾`
  - 新規 `services/transcription-service/tests/test_workload_auth.py:1-末尾`
  - 新規 `services/wake-stt/tests/test_workload_auth.py:1-末尾`
  - 新規 `services/tts-service/tests/test_workload_auth.py:1-末尾`
- 問題: Transcription/Wake/TTS static tokenとRecording用MeetingTokenを一度に削除すると、validator/issuer/Coreのrolling version差で音声処理全体が停止する。
- 変更:
  - audience別相互非同値secretでHS256固定validatorを追加し、`iss=meeting-api`、exact `aud,operations,meeting_id,user_id,bot_instance_id,session_uid,iat,nbf,exp,jti`と上限claimを検査する。このcommitでは各routeがnew capabilityとlegacy credentialをdual acceptし、new pathを優先する。
  - new capability pathは署名/claim検査後、RF-06C2のintrospection endpointへ自audience専用tokenで問い合わせ、active record一致を確認してからprovider/storage/stream side effectへ進む。introspection 503/timeout/invalid JSON、record missing/revoked/mismatchはfail closedで副作用0。legacy compatibility pathだけはcounterを記録してregistryなしの現契約をRF-06D2まで維持する。
  - exact route/authを固定する。
    - Transcription workload: 新規`POST /v1/workload/audio/transcriptions`、`Authorization: Bearer <aud=transcription-service,op=audio.transcribe>`。既存`POST /v1/audio/transcriptions`+`TRANSCRIPTION_SERVICE_TOKEN`はdeferred service caller専用で維持。
    - Wake workload: 既存`POST /v1/audio/ingest`、Bearer capability優先、legacy `X-API-Key/WAKE_STT_TOKEN`はこのcommitだけfallback。WebSocket authは本項目対象外で現契約維持。
    - TTS workload: 既存`POST /v1/audio/speech`、Bearer capability優先、legacy `X-API-Key/TTS_API_TOKEN`はこのcommitだけfallback。`/health`,`/voices`は現契約維持。
    - Recording: 既存`POST /internal/recordings/upload`、Bearer capability優先。`is_final=false`は`recording.chunk.upload`、`is_final=true`は`recording.final.upload`。legacy MeetingTokenはこのcommitだけfallback。
  - deferred専用`TRANSCRIPTION_SERVICE_TOKEN`は32 byte以上・placeholder不可・production missing/emptyでstartup failure、constant-time比較、Meeting deferred callerとTranscription verifierのexact pairだけへ配る。空値で認証をskipする分岐を削除し、wrong/missingはprovider call前401。
  - Wake Orchestrator→Wake STT WebSocketにはworkload capabilityと別の`WAKE_ORCHESTRATOR_WS_SERVICE_TOKEN`を32 byte以上で追加し、Orchestrator issuer/clientとWake verifierだけへ配る。`Authorization: Bearer`または固定Sec-WebSocket-Protocol entryでhandshakeし、query string tokenを禁止する。Wake側missing/empty configはproduction startup failure、missing/wrong/query tokenはupgrade前401/403・audio/provider call 0。token/query/headerをaccess logへ残さない。
  - Transcriptionは25 MiB/request・1,200/min、Wakeは1 MiB/frame・6,000/min・8時間累積、TTSは8,000文字・30/min・concurrency 2、Recordingはchunk 1 GiB/final 32 GiB/session 64 GiB。token TTLは`min(bot deadline+5m,iat+8h)`。同じjtiの正規session内再利用を許し、idempotencyはTranscription/Wake `request_id`、Recording `upload_id+chunk_seq`とpayload hashで別管理する。
  - Meetingはlegacy fieldsに加えて`collectorCapability,transcriptionCapability,wakeCapability,ttsCapability,recordingCapability`を発行する。Coreはnew fieldを優先するが、このcommitだけlegacy `botConfig.token`/static envへfallbackする。両方ある場合legacyを送らない。
  - audio/video final uploadを全体read/Buffer.concatから`stat`+`createReadStream` multipartへ変更する。12 GiB limit testはfake `stat.size=12GiB`と少量chunkのlogical counting ReadableでContent-Length/limitだけ検査する。実RSS/fd/retry testはtracked 256 MiB fixtureをstreamし、RSS増加128 MiB未満、retryごと新stream、abort後fd 0を検査する。12 GiBを実際に生成・読込しない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-06D1`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-06D1`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/meeting-api/tests/test_workload_capability_audiences.py::{test_exact_route_method_header_and_operation_inventory,test_cross_audience_matrix_is_rejected,test_claim_identity_mismatch_has_zero_side_effects,test_new_capability_wins_when_legacy_is_also_present,test_legacy_credentials_remain_temporary_fallback}`
  - `services/meeting-api/tests/test_workload_capability_audiences.py::{test_new_capability_requires_active_registry_record_before_every_side_effect,test_registry_unavailable_never_falls_back_to_signature_only_or_legacy}`
  - `services/transcription-service/tests/test_workload_auth.py::{test_new_workload_route_requires_transcription_audience,test_deferred_route_keeps_service_token}`
  - `services/transcription-service/tests/test_workload_auth.py::{test_deferred_token_missing_empty_placeholder_fails_startup,test_deferred_wrong_or_missing_token_has_zero_provider_calls,test_only_meeting_deferred_caller_receives_service_token}`
  - `services/wake-stt/tests/test_workload_auth.py::{test_ingest_prefers_bearer_capability,test_legacy_key_is_temporary_fallback,test_orchestrator_websocket_requires_dedicated_header_or_subprotocol_token,test_websocket_query_token_is_rejected_and_never_logged,test_missing_websocket_token_config_fails_startup}`
  - `services/tts-service/tests/test_workload_auth.py::{test_speech_prefers_bearer_capability,test_legacy_key_is_temporary_fallback}`
  - Coreの各clientでnew valid token response golden一致、new+legacy時legacy header/call 0。12 GiB logical testの実読込64 MiB未満、256 MiB streamのRSS/fd条件成功。
  - Transcription deferred static tokenとWake Orchestrator WS tokenのmissing/empty/wrong/cross-use全matrixは401/403またはstartup failure、副作用0。request URL/query/logにraw token 0。
  - `V-MEETING`, `V-TRANSCRIPTION`, `V-CORE`, `V-AUX`, `V-BACKEND`, `V-OPS`。
- リスクと戻し方: route/header/clock差でaudio停止。validator service→Meeting issuer→Core imageの順でcomponent deployし、legacy fallbackはRF-06D2まで残す。失敗branchを保持しRF-06C2のSHAから再実行する。
- 依存: RF-05G, RF-06C2
- コミット: `RF-06D1 deploy workload capabilities with legacy compatibility`

#### OP-06D-DRAIN（非commit・人間operator停止点）

RF-06C2/D1のbroker/validator、Meeting issuer、Core imageを順に実配備できる認可済みoperatorだけが共通`operation-gate-v1`を提供する。`measurements={max_token_ttl_seconds,max_client_retry_window_seconds,max_active_session_lifetime_seconds,event_broker_success_count:>0,transcription_capability_success_count:>0,wake_capability_success_count:>0,tts_capability_success_count:>0,recording_chunk_capability_success_count:>0,recording_final_capability_success_count:>0,meeting_bot_legacy_redis_count:0,legacy_transcription_count:0,legacy_wake_count:0,legacy_tts_count:0,legacy_recording_chunk_count:0,legacy_recording_final_count:0,cross_audience_reject_count:>0,disabled_path_configured_instances:0,disabled_path_active_sessions:0,disabled_path_secret_refs:0}`を提供する。5 media pathとevent brokerは各path個別のsuccessを必須とし、aggregate countを代用しない。観測時間は`max(max_token_ttl_seconds,max_client_retry_window_seconds,max_active_session_lifetime_seconds)+300`秒以上。旧global internal、media static、MeetingToken、meeting-bot Redis credentialはnew path success→legacy admission freeze→old credential revoke→old canary reject→ref/session 0の順で、値を保存せずhash/countだけを残す。`bash scripts/test/run-refactor-operator-gate.sh --master-task full-repo-refactoring-2026-07-24 --archive-root "$CONTROL_ROOT/.pipeline/release-archives" --release r4 --gate op-06d-drain`がexit 0になるまでRF-06D2を開始しない。

### RF-06D2 Workload legacy tokenと汎用Bot tokenをdrain後に除去

- 対象:
  - `services/meeting-api/meeting_api/{meetings,recordings}.py:1-末尾`
  - `services/transcription-service/main.py:1-末尾`
  - `services/wake-stt/app/main.py:1-末尾`
  - `services/wake-orchestrator/app/{config,clients,orchestrator}.py:1-末尾`
  - `services/tts-service/main.py:1-末尾`
  - `services/vexa-bot/core/src/{index,types,docker}.ts:1-末尾`
  - `services/vexa-bot/core/src/services/{transcription-client,wake-stt-client,recording,video-recording,tts-playback}.ts:1-末尾`
  - `services/runtime-api/profiles.yaml:1-末尾`
  - `deploy/{compose,lite,helm}/**:1-末尾`
  - `services/meeting-api/meeting_api/collector/processors.py:29-65,156-176`
  - 新規 `tests3/unit/refactor/test_rf_06d2.py:1-末尾`
- 問題: RF-06D1はrolling互換用にstatic workload credentialと汎用`botConfig.token`を残しており、cross-service replay余地がまだある。
- 変更:
  - OP-06D-DRAINを検証後、Wake/TTS/Recording workload routeのlegacy fallbackを削除し、Transcription Botはnew workload routeだけを使う。deferred service callerの`POST /v1/audio/transcriptions`+service tokenだけはservice process内例外として残す。
  - RF-06C2のevent broker cutoverも同じdrain証拠へbindする。CoreのMeeting Redis client/URL/credential、`meeting-bot-legacy` ACL userを削除し、Redis `default off`、Meeting profileをworkload networkだけへ切り替える。Kubernetes NetworkPolicyは`runtime.profile=meeting`からRedis ingressをdenyする。Browser legacy user/profileだけはRF-09Bまで残す。new broker unavailable時にRedisへfallbackしない。
  - generated meeting/browser/agent containerのenv/BOT_CONFIG/profileから`TRANSCRIPTION_SERVICE_TOKEN`,`WAKE_STT_TOKEN`,`WAKE_STT_API_TOKEN`,`TTS_API_TOKEN`とlegacy MeetingTokenを削除する。signing secretはRF-05F exact service matrix外へ出さない。
  - `botConfig.token/currentBotConfig.token`を削除し、用途別5 capabilityだけにする。collector envelopeは`aud=transcription-collector,op=segment.write|speaker.write`だけをRF-06C2 brokerがserver-side付加し、BotへMeetingTokenを渡さない。
  - 全audience×全routeのcross matrix、revocation、request idempotency、normal load/limitをfinal authとして固定する。旧static/MeetingTokenは401/403・provider/storage/Redis/TTS call 0。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-06D2`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-06D2`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/meeting-api/tests/test_workload_capability_audiences.py::{test_cross_audience_matrix_is_rejected,test_recording_rejects_collector_and_legacy_meeting_tokens,test_claim_identity_mismatch_has_zero_side_effects,test_collector_contract_is_preserved_via_broker}`
  - `tests3/unit/refactor/test_rf_06d2.py::{test_generated_workloads_have_no_static_media_or_tts_token,test_generic_bot_token_and_legacy_fallbacks_are_absent,test_default_and_meeting_legacy_redis_users_are_off,test_meeting_profile_has_no_redis_network_or_secret,test_only_browser_legacy_remains_until_rf_09b,test_only_deferred_transcription_service_keeps_static_token,test_signing_secret_distribution_matches_rf_05f_matrix}`
  - 8-speaker/500ms Wake/Transcription retry/30秒chunk/12 GiB logical final/TTS normal loadは成功、上限+1だけ429/413。cross audience/expired/revoked/bad signatureは副作用0。
  - `rg -n 'botConfig\.token|currentBotConfig\.token|token: botConfig\.token' services/vexa-bot/core/src` 0。generated workload env/config/mount/job/logのstatic token/signing secret canary 0。
  - `V-MEETING`, `V-TRANSCRIPTION`, `V-CORE`, `V-AUX`, `V-BACKEND`, `V-OPS`。
- リスクと戻し方: legacy caller drainが虚偽ならaudio/recording停止。OP-06D-DRAINなしで開始しない。失敗時は新規Bot作成を止め、legacy secretを新commitへ戻さずRF-06D1 componentへrollbackしてtask未完とする。
- 依存: RF-06C2, RF-06D1, OP-06D-DRAIN
- コミット: `RF-06D2 remove workload legacy tokens after capability drain`

### RF-06E Agent containerのprovider credentialをsubject所有へ限定

- 対象:
  - `deploy/compose/docker-compose.yml:1-末尾`
  - `services/agent-api/agent_api/container_manager.py:140-176`
  - `services/agent-api/agent_api/config.py:32-37`
  - `services/agent-api/agent_api/main.py:1-末尾`
  - `services/runtime-api/profiles.yaml:81-96`
  - `services/meeting-api/config/profiles.yaml:45-53`
  - read-only inventory: `git grep -n -E -e 'ANTHROPIC_API_KEY|CLAUDE|provider.*credential' -- services/agent-api services/runtime-api deploy/compose deploy/lite deploy/helm`。期待pathは`deploy/compose/docker-compose.yml`、上記Agent 3 file、`services/runtime-api/profiles.yaml`だけで、別pathが1件でもあれば停止してplan reviewへ戻る
  - 新規 `services/agent-api/tests/test_provider_credential_ownership.py:1-末尾`
- 問題: Agent service全体の`ANTHROPIC_API_KEY`とhost上のClaude OAuth credential fileを全user containerへ渡す。1 user/AI shellがplatform共有provider権限を持ち出せる。
- 変更:
  - Agent service global configの`ANTHROPIC_API_KEY`、`CLAUDE_CREDENTIALS_PATH`、`CLAUDE_JSON_PATH`と、Runtime agent profileのglobal provider env/mountを削除する。host credential fileをcontainerへbind mountする経路を残さない。
  - RF-03Aの任意dict `AgentRuntimeConfig.env`をprovider用途に使わず、subject dataから読むtyped `provider_credentials={anthropic_api_key}`だけを許可する。server側mapperがこれをexact `ANTHROPIC_API_KEY`へ変換し、未知provider fieldと`VEXA_*`,`*_URL`,`HTTP_PROXY`,`HTTPS_PROXY`,`PATH`,`LD_*`,`DYLD_*`,`PYTHONPATH`,`NODE_OPTIONS`等のreserved keyは保存/Runtime call前422にする。
  - container envは「固定system allow-listを構築→subject-owned provider fieldをexact provider env名へ追加」の順で新dictを作り、user入力dictのmergeやserver global provider credential fallbackをしない。user A configはresolved subject Aのcontainerだけへ渡し、body/query user IDで上書きできない。
  - provider credentialのkey/valueをresponse/log/evidenceへ出さず、Runtime create bodyをcaplogするtestでもvalueをredactする。Runtimeのprofile/list/build summaryへ解決値を保存しない。
  - 既存Claude OAuth fileはcontainerへ移行・copyしない。OAuth file利用者は本項目のrollout前にsubject-owned Anthropic API keyを設定し、未設定なら既存の認証不足errorで停止する。
  - provider credentialがない場合、containerは作成できるがClaude実行要求はnetwork/process開始前に既存の認証不足errorを返す。platform共有credentialを復活させるfallbackを作らない。
  - 将来platform-managed provider accessが必要なら、利用量/subjectをbindするprovider brokerを別taskで設計する。本項目で汎用LLM proxyを追加しない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-06E`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-06E`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/agent-api/tests/test_provider_credential_ownership.py::{test_global_provider_key_and_host_oauth_mount_are_never_propagated,test_only_resolved_subject_provider_field_reaches_own_container,test_reserved_and_unknown_env_keys_are_rejected_before_runtime_call,test_cross_user_body_cannot_select_credentials,test_missing_user_credential_fails_before_provider_process,test_provider_values_are_absent_from_caplog_response_and_exception}`
  - Compose/Lite/Helm renderとRuntime profile responseでgenerated Agent env/mountにglobal `ANTHROPIC_API_KEY`、Claude credential path/file canary 0。
  - user-owned canaryはA container create callだけexact 1件、B container/log/exception/evidenceに0。
  - `rg -n 'CLAUDE_CREDENTIALS_PATH|CLAUDE_JSON_PATH' services/agent-api services/runtime-api/profiles.yaml` はmigration test以外0。
  - `V-BACKEND`, `V-OPS`。
- リスクと戻し方: platform共有Claude credentialに依存する既存環境ではAgent実行が認証不足になる。共有secretを戻さず、各userへsubject-owned credential設定を案内する。失敗branchを保持しRF-06D2のSHAから再実行する。
- 依存: RF-03A, RF-05D2, RF-06A
- コミット: `RF-06E keep agent provider credentials subject owned`

### RF-06F Zoom pre-signed SDK JWT受理をsecret除去より先に配備する

- 対象:
  - `services/meeting-api/meeting_api/meetings.py:1158-1175`
  - 新規 `services/meeting-api/meeting_api/zoom_sdk_tokens.py:1-末尾`
  - 新規 `services/meeting-api/tests/test_zoom_sdk_tokens.py:1-末尾`
  - `services/vexa-bot/core/src/index.ts:1-末尾`
  - `services/vexa-bot/core/src/platforms/zoom/index.ts:1-末尾`
  - `services/vexa-bot/core/src/platforms/zoom/strategies/join.ts:14-33`
  - `services/vexa-bot/core/src/platforms/zoom/sdk-manager.ts:203-223`
  - 新規 `services/vexa-bot/core/src/platforms/zoom/sdk-manager.test.ts:1-末尾`
  - `services/vexa-bot/run-zoom-bot.sh:1-40`
  - 新規 `services/vexa-bot/tests/test_zoom_runner_auth.sh:1-末尾`
  - `services/vexa-bot/README.md:101-123`
  - read-only既知一致: Zoom meeting URL password fixtureの`services/meeting-api/tests/test_url_parser_and_dry_run.py:1-末尾`と`services/vexa-bot/core/src/platforms/zoom/web/join.test.ts:1-末尾`、別purpose HMAC testの`services/vexa-bot/core/src/services/production-replay.test.ts:1-末尾`、provider SDK headerの`services/vexa-bot/core/src/platforms/zoom/native/zoom_meeting_sdk/h/auth_service_interface.h:162,165`
  - read-only inventory: `git grep -n -E -e 'ZOOM_(CLIENT|SDK).*(ID|SECRET)|zoom.*secret|createHmac|tokenExp|appKey' -- deploy/compose deploy/lite deploy/helm services/runtime-api services/meeting-api services/vexa-bot`。上記write対象またはread-only既知一致以外のproduction/config wiringが1件でもあれば停止してplan reviewへ戻る
- 問題: Zoom native SDK用client secretを生成Bot envへ渡し、Bot側で24時間のapp-level JWTを何度でも生成できる。JWT受理とsecret除去を1コミットにすると、旧Core rollbackができない。
- 変更:
  - Meeting側`zoom_sdk_tokens.py`でHS256 JWTを生成し、Botへ`ZOOM_SDK_JWT`とpublic client IDを追加する。このコミットでは旧Core rollback用`ZOOM_CLIENT_SECRET`配線を残し、RF-06GまでPhase 1を完了扱いにしない。
  - token payloadはZoom SDKが現在使う`appKey,iat,exp,tokenExp`だけ。`iat=now-5s`、`exp=tokenExp=min(max(iat+1800s, bot_deadline+300s), iat+7200s)`へ固定し、Zoom最小30分と最大2時間を両立する。token/secretをDB/Redis/log/evidenceへ保存しない。
  - Coreの`ZoomSDKManager`は`ZOOM_SDK_JWT`がある場合それだけをSDK authenticateへ渡し、client secret HMACを呼ばない。JWT欠落時だけ既存secret pathを互換利用するが、両方あるのにlegacyへfallbackしてはいけない。
  - `run-zoom-bot.sh`も`ZOOM_SDK_JWT`優先、secretはこのコミットだけfallbackとし、READMEへRF-06G後にJWT必須になることを明記する。
  - `test_zoom_runner_auth.sh`は`--report-json <task-owned path> --case <exact case>`を1回以上受け、未知/重複case、任意output、extra argvを拒否する。RF-06F時点ではcompletionに列挙した2 caseをこの順で実装・実行し、RF-06Gが3件目`test_runner_rejects_client_secret_and_requires_presigned_jwt`を追加する。各item reportの`required_test_names`と`cases[]`は、そのitem completionに存在するexact case列とbyte一致させる。fake Dockerだけを使い、`collected,passed,failed,skipped,required_test_names,cases[]`をJSONへatomic writeする。
  - rolling順はMeeting JWT発行→Core pre-signed対応image→manual runnerのJWT smoke。全環境でJWT path成功後だけRF-06Gへ進む。
  - Zoom SDK JWTはprovider仕様上meeting-boundでない残余リスクがあるため、最大2時間とnetwork policyで縮小し、完全なmeeting bindingとは主張しない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-06F`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-06F`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/meeting-api/tests/test_zoom_sdk_tokens.py::{test_token_uses_existing_claim_contract_thirty_minute_floor_and_two_hour_cap,test_short_bot_deadline_still_meets_zoom_minimum,test_secret_and_token_are_never_persisted_or_logged}`
  - `services/vexa-bot/core/src/platforms/zoom/sdk-manager.test.ts::{uses_presigned_jwt_without_calling_legacy_hmac,presigned_jwt_wins_when_both_credentials_exist,legacy_secret_still_supports_old_image_rollback,rejects_missing_or_expired_token_before_sdk_auth}`
  - generated Zoom BotはJWTとlegacy secretをこの互換commitだけ受ける。JWTありfixtureでHMAC call 0、JWT/secretのDB/Redis/log/evidence保存0。
  - `services/vexa-bot/tests/test_zoom_runner_auth.sh::{test_runner_prefers_presigned_jwt,test_legacy_secret_is_only_temporary_fallback}`
  - `V-MEETING`, `V-CORE`, `V-BACKEND`, `V-OPS`。
- リスクと戻し方: provider clock skew/JWT TTLでnative joinが止まる。このcommitは旧pathを残すため旧Coreへ戻せるが、RF-06Gを開始せずtask未完として報告する。fixture tokenとMeeting/Core rollout versionを直し、失敗branchを保持してRF-06EのSHAから再実行する。
- 依存: RF-05F, RF-06D2
- コミット: `RF-06F accept server signed zoom sdk tokens before secret removal`

#### OP-06F-DRAIN（非commit・人間operator停止点）

RF-06FをMeeting issuer→Core image→manual runnerの順で実配備できる認可済みoperatorだけが共通`operation-gate-v1`を提供する。`measurements={max_zoom_sdk_token_ttl_seconds:7200,presigned_zoom_join_success_count:>0,legacy_zoom_hmac_count:0,active_old_zoom_bot_count:0}`、観測時間は`max_zoom_sdk_token_ttl_seconds+300`秒以上。`bash scripts/test/run-refactor-operator-gate.sh --master-task full-repo-refactoring-2026-07-24 --archive-root "$CONTROL_ROOT/.pipeline/release-archives" --release r5 --gate op-06f-drain`がexit 0になるまでRF-06Gを開始せず、fixture joinやsource grepを実drainの代用にしない。

### RF-06G Zoom SDK client secretを全workloadから除去する

- 対象:
  - `services/meeting-api/meeting_api/meetings.py:1158-1175`
  - `services/meeting-api/meeting_api/zoom_sdk_tokens.py:1-末尾`（RF-06F作成物）
  - `services/vexa-bot/core/src/index.ts:1-末尾`
  - `services/vexa-bot/core/src/platforms/zoom/index.ts:1-末尾`
  - `services/vexa-bot/core/src/platforms/zoom/strategies/join.ts:14-33`
  - `services/vexa-bot/core/src/platforms/zoom/sdk-manager.ts:203-223`
  - `services/vexa-bot/core/src/platforms/zoom/sdk-manager.test.ts:1-末尾`（RF-06F作成物）
  - `services/vexa-bot/run-zoom-bot.sh:1-40`
  - `services/vexa-bot/tests/test_zoom_runner_auth.sh:1-末尾`（RF-06F作成物）
  - `services/vexa-bot/README.md:101-123`
  - read-only既知一致: Zoom meeting URL password fixtureの`services/meeting-api/tests/test_url_parser_and_dry_run.py:1-末尾`と`services/vexa-bot/core/src/platforms/zoom/web/join.test.ts:1-末尾`、別purpose HMAC testの`services/vexa-bot/core/src/services/production-replay.test.ts:1-末尾`、provider SDK headerの`services/vexa-bot/core/src/platforms/zoom/native/zoom_meeting_sdk/h/auth_service_interface.h:162,165`
  - read-only inventory: RF-06Fと同じZoom検索。上記write対象またはread-only既知一致以外のproduction/config wiringが1件でもあれば停止してplan reviewへ戻る
- 問題: RF-06Fは安全なJWT pathを先行配備したが、rollback用client secretとBot側HMACがまだ残る。
- 変更:
  - `ZOOM_CLIENT_SECRET`はMeeting service processだけが持つ。Bot env/BOT_CONFIG、Runtime profile、Core config/typeからclient secretを削除する。
  - CoreのHMAC生成、`crypto.createHmac`、client secret引数、legacy fallbackを削除し、pre-signed JWT欠落/期限切れ/不正形式はSDK call前にfailする。
  - `run-zoom-bot.sh`は`ZOOM_SDK_JWT`とpublic client IDだけを必須にし、secret入力/`-e`注入を削除する。READMEのBot必須env表からsecretを削除し、短期JWTはMeeting serviceの認証済みbot作成フローから得ること、直接secretでmintしないことを明記する。
  - rolloutはRF-06FのJWT path evidence確認→新Core/manual runner→runtime spec secret削除の順。RF-06F互換commitへ戻す必要が出た場合はtaskを未完で停止し、RF-06G合格を主張しない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-06G`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-06G`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/meeting-api/tests/test_zoom_sdk_tokens.py::{test_missing_server_secret_fails_before_runtime_create,test_only_meeting_service_holds_client_secret_ref}`
  - `services/vexa-bot/core/src/platforms/zoom/sdk-manager.test.ts::{uses_presigned_jwt_without_client_secret,rejects_missing_or_expired_token_before_sdk_auth}`
  - `services/vexa-bot/tests/test_zoom_runner_auth.sh::test_runner_rejects_client_secret_and_requires_presigned_jwt`
  - generated Zoom Bot env/BOT_CONFIG/inspect/log/Redisに`ZOOM_CLIENT_SECRET` canary 0。Meeting service secret refだけexact 1。
  - `rg -n 'ZOOM_CLIENT_SECRET|createHmac' services/vexa-bot services/runtime-api/profiles.yaml deploy/compose deploy/lite deploy/helm` はMeeting server wiring/negative test以外0。
  - `V-MEETING`, `V-CORE`, `V-BACKEND`, `V-OPS`。
- リスクと戻し方: RF-06Fで未検出のJWT互換差があるとjoin停止。secretを同commitへ戻さず、失敗branchを保持しRF-06FのSHAから新worktreeでRF-06Gを再実行する。運用rollbackでRF-06F imageへ戻した場合はtask statusを未完にする。
- 依存: RF-06F, OP-06F-DRAIN
- コミット: `RF-06G remove zoom client secrets from every workload`

### RF-06H upstream proxy credentialをsession-scoped egress brokerへ移す

- 対象:
  - `services/meeting-api/meeting_api/meetings.py:1084-1175`
  - 新規 `services/meeting-api/meeting_api/workload_proxy_capabilities.py:1-末尾`
  - 新規 `services/meeting-api/tests/test_workload_proxy_capability.py:1-末尾`
  - `services/runtime-api/runtime_api/api.py:1-末尾`
  - 新規 `services/workload-broker/{pyproject.toml,Dockerfile}:1-末尾`
  - 新規 `services/workload-broker/workload_broker/{__init__,main}.py:1-末尾`
  - 新規 `services/workload-broker/tests/test_egress_proxy.py:1-末尾`
  - 新規 `deploy/helm/charts/vexa/templates/{deployment-workload-broker,service-workload-broker}.yaml:1-末尾`
  - `deploy/helm/charts/vexa/{values.yaml,templates/secret.yaml,templates/configmap-runtime-profiles.yaml}:1-末尾`
  - `deploy/compose/docker-compose.yml:1-末尾`
  - `deploy/lite/{entrypoint.sh,supervisord.conf}:1-末尾`
  - `services/vexa-bot/core/src/index.ts:2479-2485,2523-2535,2607-2610`
  - `services/runtime-api/profiles.yaml:1-末尾` のmeeting proxy env
  - read-only inventory: `git grep -n -E -e 'HTTP_PROXY|HTTPS_PROXY|proxy.*credential' -- services/meeting-api services/runtime-api deploy/compose deploy/lite deploy/helm`。baseでservices側一致0、deploy側既存proxy wiring 1件以上を期待し、結果をitem evidenceへ保存する。services側一致が増えていればtargetを自動拡張せずplan reviewへ戻る
  - 新規 `services/runtime-api/tests/test_proxy_capability.py:1-末尾`
- 問題: `HTTP_PROXY/HTTPS_PROXY`へ`user:password@host`形式のupstream credentialを入れると、生成Bot/Browserがplatform共有proxy credentialを読める。proxy自体もprivate networkへの迂回経路になり得る。
- 変更:
  - upstream proxy scheme/host/portは非secret config、username/passwordまたはcredential全体はCompose/Liteの0600 secret file、Helm `existingSecret`固定keyの`secretKeyRef`だけに置く。values/ConfigMap/profile/evidence/process argsへ平文0。proxy有効なのにSecret ref欠落はstartup failure。
  - credentialと`BOT_PROXY_CAPABILITY_SECRET`は新規`workload-broker`だけが保持する。brokerはnon-root、read-only rootfs、all capabilities drop、Docker socket/Kubernetes token/Runtime principal/DB/Redis credential 0。Runtime control planeはsession network attach/detachだけを行い、CONNECT/HTTP bytesをparseしない。generated meeting workloadへはbroker URLと`aud=workload-egress-proxy`のsession capabilityだけを渡し、upstream host/userinfoを渡さない。現HEADでproxy wiringがないBrowser Sessionへ新規適用しない。
  - Meetingはplatformとserver-side provider registryから`allowed_host_rules`（ASCII lowercase exact hostまたは承認済みsuffix boundary）と`allowed_provider_cidr_ids`を解決し、capabilityへ`meeting_id/session_uid/container_name,platform,operations=["http.forward","https.connect"],allowed_host_rules,allowed_provider_cidr_ids,allowed_ports=[80,443],max_connections=128,max_bytes=21474836480,iat,nbf,exp,jti`を署名する。workload request/BOT_CONFIGはallow-listを指定・追加できない。`exp=min(bot deadline+300s,iat+28800s)`、clock skew 5秒、同一jtiの同時connection上限128。別session/container/platform/audience/revoked jtiはconnect前403。
  - brokerは署名検証後、各CONNECT/forward開始前にRF-06C2 registryを`CAPABILITY_INTROSPECTION_PROXY_TOKEN`で照合する。registry unavailable/timeout/revokedは503/403でupstream socket 0、署名だけのoffline fallback 0。terminal revocationで既存connectionもgrace内にcloseする。
  - brokerはHTTP forwardとCONNECTの最小実装だけをinternal port `8091`で提供し、host portへbindしない。header上限32 KiB、connect timeout 15秒、idle timeout 300秒、session総転送20 GiB、graceful shutdown 30秒に固定する。
  - Meeting Botの3 Playwright launch pathは同じpure `meetingProxyOptions(config)`を使い、`proxy={server:"http://workload-broker:8091",username:"vexa-session",password:<session capability>}`を渡す。capabilityをURL/BOT_CONFIG log/process argsへ出さず、407時にproxyなしで再launchしない。Browser Session launch pathは変更しない。
  - RF-05EのDNS/IP policyで**destination target**の元hostnameを各request直前にbroker自身が解決し、capabilityのexact/suffix host ruleとprovider registryのCIDR双方に一致する全A/AAAAだけを許す。別に、固定configの**upstream proxy endpoint**をexact allow-list、TLS verify-full、固定CA、元proxy hostname/SNIで検証し、DNS検証済みproxy IP literalへpinしてTLS接続する。brokerはそのTLS tunnel内だけで`CONNECT <validated destination IP literal>:<port>`を送り、upstreamへdestination hostnameを再解決させない。tunnel成立後のbrowser TLS/HTTPは元destination hostnameをHost/SNI/証明書検証へ使う。`Proxy-Authorization`はupstream TLS内のCONNECT requestにだけ付与し、destination tunnel bytes、redirect先、packet/logへ1 byteも出さない。upstreamがliteral CONNECTをsupportしない場合は起動preflightでfailし、hostname CONNECTへfallbackしない。port 80のcredential-bearing plaintext upstreamは常に禁止し、TLS upstream内のIP-literal CONNECT後にraw HTTPをtunnelするmodeをupstreamがsupportしない場合はHTTP forwardをdisableする。destinationまたはproxy socket peer IPが各検証集合外ならrequest body/Proxy-Authorization送信前にcloseし、retryは両endpointを再解決・再検証・再pinする。private/non-global/metadata/single-label/IP literal、任意public host、redirect先の未許可host/CIDRをdenyする。workloadから任意header credential、proxy chaining設定、SOCKS、UDPを受けない。実装前preflightでupstream schemeをinventoryし、`socks4/socks5`またはTLS非対応proxyが1件でも設定済みならcredential除去前に停止してoperatorへTLS対応HTTP CONNECT proxyへの移行を要求する。`workload-broker /health`はlistener/introspection dependencyとupstream literal-CONNECT/TLS capabilityを検査し、設定なし時は`disabled`、設定ありでlistener failureは503。Runtime `/health`はdata-plane secret・broker task・listener状態を保持または報告しない。
  - upstream `Proxy-Authorization`はworkload-brokerがdispatch時に追加し、request/Redis/backend metadata/logへ保存しない。logはtarget public host/port、session hash、resultだけ。
  - proxy未設定profileではbroker/capabilityを作らずdirect egressの現仕様を維持する。proxy設定時にbroker unavailableならdirect fallbackせずBot作成/connectionをfailする。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-06H`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-06H`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/workload-broker/tests/test_egress_proxy.py::{test_upstream_userinfo_never_reaches_workload_configmap_profile_or_logs,test_capability_is_bound_to_session_container_platform_server_host_rules_and_provider_cidrs,test_workload_cannot_choose_or_expand_allowlist,test_private_metadata_arbitrary_public_and_dns_rebinding_targets_are_rejected,test_two_hop_proxy_pins_verified_upstream_and_destination_ips_with_original_sni,test_connect_uses_destination_ip_literal_and_upstream_cannot_reresolve_public_to_private,test_proxy_authorization_exists_only_inside_verified_upstream_tls_and_never_in_destination_packet_or_log,test_plaintext_credential_upstream_and_unsupported_literal_connect_fail_closed,test_proxy_configured_never_falls_back_to_direct,test_fixed_connection_byte_timeout_and_header_limits,test_listener_health_and_graceful_shutdown,test_socks_configuration_blocks_before_workload_change,test_broker_has_no_control_plane_database_or_redis_reachability}`
  - `services/workload-broker/tests/test_egress_proxy.py::{test_registry_unavailable_or_revoked_has_zero_upstream_sockets,test_runtime_health_has_no_broker_listener_or_data_plane_secret_state}`
  - `services/meeting-api/tests/test_workload_proxy_capability.py::{test_meeting_issuer_derives_allowlist_server_side_and_binds_session_container_platform,test_request_cannot_supply_proxy_host_rules_cidrs_or_credential,test_capability_and_upstream_secret_never_enter_bot_config_log_redis_or_evidence}`
  - `services/vexa-bot/core/src/meeting-proxy-options.test.ts::{test_all_three_meeting_launch_paths_use_fixed_proxy_auth,test_407_never_relaunches_without_proxy,test_browser_session_launch_is_unchanged}`
  - mock transport/resolverだけを使い実Internet/localhost接続0。wrong capability時upstream connect 0。
  - generated Meeting Bot env/BOT_CONFIG/inspect/log/Redisにupstream proxy URL/user/password canary 0。session capabilityは自sessionだけで成功。Browser Sessionはproxy field/behavior追加0。
  - bounded mockで64並列connectionと5 GiB相当stream counterが上限未達、129接続/20 GiB+1 byteだけ429/413。capability/tokenはprocess argsとURLに0。
  - Helm render canaryはSecret dataを表示せず、workload-brokerの`secretKeyRef` exact 1、Runtime/Meeting/Browser/Agent/Gateway 0。broker侵害fixtureからDocker socket/Kubernetes API/Runtime control port/DB/Redisは全connection denied。
  - `V-BACKEND`, `V-MEETING`, `V-OPS`。
- リスクと戻し方: Chromium CONNECT互換、throughput、stream cleanupで会議参加が止まる。bounded fake proxyとmeeting smokeで確認し、userinfo env/direct fallbackを戻さない。失敗branchを保持しRF-06GのSHAから再実行する。
- 依存: RF-05D2, RF-05E, RF-05F, RF-06D2, RF-06G
- コミット: `RF-06H broker upstream proxy credentials per session`

### RF-06I1 既存Runtime resourceをserver-owned labelへbackfillし操作時に再照合

- 対象:
  - `services/runtime-api/runtime_api/api.py:178-272`
  - `services/runtime-api/runtime_api/backends/docker.py:143-202`
  - `services/runtime-api/runtime_api/backends/kubernetes.py:150-227`
  - 新規 `services/runtime-api/runtime_api/models.py:1-末尾`
  - `deploy/helm/charts/vexa/templates/{deployment-runtime-api,rbac-runtime-api}.yaml:1-末尾`
  - 新規 `services/runtime-api/tests/test_resource_ownership_backfill.py:1-末尾`
- 問題: RF-05D1で新規createを安全化しても、既存container/PodとRedis stateにはserver-owned label/owner/profile/backend identityが欠ける。get/exec/deleteがRedis recordだけを信頼すると、旧resourceや改ざんstateへ越権できる。
- 変更:
  - RF-05D1以後のnew resource recordをimmutable `backend_id,owner_subject_hash,session_uid,profile,server_nonce,expected_labels,created_at`へ統一する。RF-03A/06Eのprovider/workspace refはserver-side resolverで解決し、record/envへraw credential 0。
  - 既存resource inventoryをread-only走査し、Runtimeが作成したことを既存label+state+name+creation time全一致で証明できるものだけmaintenance window中にserver-owned labelへbackfillする。曖昧、owner不明、label conflict、外部作成resourceは`quarantined_legacy`として操作禁止にし、自動adopt/deleteしない。件数/ID hashだけをoperator reportへ保存する。
  - get/touch/exec/archive/delete/stop/reaperはRedis recordとbackend実体のID、owner、profile、server nonce、全expected label一致を毎回再検査する。不一致は409/404、backend operation 0、stateを実体へ合わせて書換えない。Kubernetes SA/automountとDocker bind/network/capabilityもRF-05D1 profile contractから逸脱していないことをinspectする。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-06I1`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-06I1`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/runtime-api/tests/test_resource_ownership_backfill.py::{test_only_provably_runtime_owned_legacy_resources_are_backfilled,test_ambiguous_external_and_conflicting_resources_are_quarantined_not_adopted_or_deleted,test_every_operation_rechecks_backend_id_owner_profile_nonce_labels_and_security_profile,test_state_tamper_never_relabels_or_operates_backend,test_provider_and_workspace_credentials_are_absent_from_immutable_record}`
  - A principal+B backend/state fixtureはstart/touch/exec/archive/delete/stop 0。正規resourceだけ既存response契約で成功し、backfill件数+quarantine件数=inventory件数。
  - `V-BACKEND`, `V-OPS`。
- リスクと戻し方: 旧resourceがquarantineされ操作不能になる。自動adopt/deleteで救済せず、operatorへhashed ID/理由を渡して個別再作成する。失敗branchを保持しRF-06HのSHAから再実行する。
- 依存: RF-05D1B, RF-05D2, RF-06A, RF-06E
- コミット: `RF-06I1 backfill and recheck runtime resource ownership`

### RF-06I2 Workload間VNC・CDP ingressをsession単位で隔離

- 対象:
  - `services/vexa-bot/core/entrypoint.sh:73-89`
  - `services/vexa-bot/core/src/index.ts:2303-2331`
  - `services/runtime-api/runtime_api/backends/{docker,kubernetes}.py:1-末尾`
  - 新規 `services/workload-access-broker/{pyproject.toml,Dockerfile}:1-末尾`
  - 新規 `services/workload-access-broker/workload_access_broker/{__init__,main}.py:1-末尾`
  - 新規 `services/workload-access-broker/tests/test_access.py:1-末尾`
  - 新規 `services/runtime-network-policy-agent/{pyproject.toml,Dockerfile}:1-末尾`
  - 新規 `services/runtime-network-policy-agent/runtime_network_policy_agent/{__init__,main}.py:1-末尾`
  - 新規 `services/runtime-network-policy-agent/tests/test_policy.py:1-末尾`
  - 新規 `deploy/runtime-network-policy-agent/runtime-network-policy-agent.service:1-末尾`
  - 新規 `deploy/runtime-network-policy-agent/policy.json:1-末尾`
  - runtime output: `/var/lib/vexa/runtime-network-policy-agent/state.json:1-末尾`
  - `deploy/lite/{bot-slot-wrapper.sh,entrypoint.sh,supervisord.conf,Dockerfile.lite}:1-末尾`
  - `services/api-gateway/main.py:1886-2338`
  - `services/agent-api/agent_api/{main,container_manager}.py:1-末尾`
  - `services/meeting-api/meeting_api/session_capability_registry.py:1-末尾`（RF-06C2作成物）
  - 新規 `deploy/helm/charts/vexa/templates/{deployment-workload-access-broker,service-workload-access-broker,networkpolicy-workload-access}.yaml:1-末尾`
  - RF-05D1の `deploy/helm/charts/vexa/templates/networkpolicy-workloads.yaml:1-末尾`
  - `deploy/helm/charts/vexa/{values.yaml,templates/secret.yaml,templates/rbac-runtime-api.yaml}:1-末尾`
  - `deploy/compose/docker-compose.yml:1-末尾`
- 問題: generated workloadsがflat network上にあり、x11vnc `-nopw`、websockify 6080、CDP relay 9223へ他sessionから直結できる。Gateway subject認証を迂回して画面/cookieを奪取できる。
- 変更:
  - Chrome 9222とx11vnc 5900はworkload loopbackだけへbindする。Runtimeはbackend ID/label検証後、audienceごとのEd25519 keypair+jtiをmemory内で生成する。private keyは直後のbootstrap以外に渡さず、public keyだけをowner serviceへ登録する。
  - Runtime create DTO/HTTP requestはprivate keyを含めず、container/Podをkeyなし・readiness falseで作成する。固定bootstrap operationだけが`uint32 length + PKCS8 key bytes + EOF`のframed stdinでper-session 0600 tmpfs fileまたはsealed Linux memfdへ渡す。任意exec APIはこのoperationを表現できない。relayがread後unlink/locked memory化する。RuntimeはRedis/registry credentialを持たず、profileから解決したowner serviceの固定`POST /internal/workload-access/{bind,activate,fail}`だけを呼ぶ。Meeting/browserには`RUNTIME_MEETING_ACCESS_STATE_SECRET`、Agentには`RUNTIME_AGENT_ACCESS_STATE_SECRET`を使い、相互利用不可。body-bound assertionは`iss=runtime-api,aud=<owner-service>,operation,relay_jti,public_key,public_key_sha256,owner_subject_hash,session_uid,profile,backend_id,registration_jti,broker_ack_sha256,method,path,body_sha256,iat,exp<=30s,state_jti`を持つ。owner serviceは自身のcanonical session/container recordを照合し、bindでregistry recordを不存在から`bound`へSET NX、activate/failでexact current recordをCASする。wrong owner/profile/backend/key/caller/replayはregistry write 0。`bound`はuser connect用active introspectionをdenyする。
  - RuntimeのmTLS+HS256 registration bodyは`owner_service,profile,relay_jti,public_key,public_key_sha256,session_uid,backend_id,registration_jti`を含む。access brokerはowner別の固定registration-check endpointへ`ACCESS_MEETING|ACCESS_AGENT` introspection tokenで問い合わせ、`bound` recordのpublic key/hash/backend/profile一致だけを確認する。このregistration-only checkはuser connectを許可しない。続いてbrokerは256-bit nonce+audience/session/backend/jti transcriptをrelayへ送り、relayはEd25519 private keyで署名する。brokerはregistration bodyのpublic keyで検証し、private key/共通raw tokenをbroker↔relay networkへ1 byteも送らない。成功ackをRuntimeが同じowner serviceのactivate endpointへbindしてからreadiness trueへする。bind/registration/check/challenge/ack/activationのどれかが失敗したらowner fail endpointで`failed|revoked`へ遷移し、作成resourceをcleanup、broker memory record/connectionとprivate key memory/tmpfsを破棄する。一度もactiveにせず、retryでfailed/revokedを再利用しない。Docker inspect、Kubernetes rendered Pod、Runtime DB/Redis/backend metadata/job/log、packet capture、`/proc/*/{cmdline,environ}`へprivate-key canary 0。ProcessBackendはRF-06I3の`pass_fds`または0600 private tmpfsだけを使う。
  - user-visible経路をGateway→subject auth→別service `workload-access-broker`→session relayへ統一する。RF-06Hのegress `workload-broker`とprocess/container/Deployment/ServiceAccount/secretを共有しない。access brokerはupstream proxy credential、`BOT_PROXY_CAPABILITY_SECRET`、`CAPABILITY_INTROSPECTION_PROXY_TOKEN` 0、egress brokerはVNC/CDP private key、`CAPABILITY_INTROSPECTION_ACCESS_{MEETING,AGENT}_TOKEN`、registration/state secret 0。両方non-root/read-only/capabilities 0で、Docker socket/Kubernetes API/Runtime principal/DB/Redis secret 0。
  - Runtime control planeはnetwork attach/detach後、`WORKLOAD_ACCESS_REGISTRATION_SECRET`でHS256署名した`iss=runtime-api,aud=workload-access-broker,session_uid,backend_id,server_generated_dns_name,ports={vnc:6080,cdp:9223},method="POST",normalized_path="/internal/registrations",canonical_body_sha256,content_length,iat,exp<=300s,registration_jti`を固定routeへ送る。DNS名はRF-06I1 server-generated backend labelとexact一致、portsはenumで任意値不可。brokerは同じsession networkへattach済みの自身からDNS/label一致をprobeし、registration_jtiをone-time消費して登録する。body差替/replay、任意IP/host/headerはrecord/relay call 0。
  - access brokerはpublic key/context/connectionだけをmemory recordへ持ち、private keyを一度も受けない。broker process/pod restart時はkeyを復元・reissue・再登録せず全registrationをfail closedにし、owner service/Runtimeへrecreate-required health eventを返して該当sessionをterminal化する。旧relay_jti revoke、connection close、endpoint/network cleanup後にsession再作成だけが新keypairを配布する。
  - registration listenerはRF-05FのmTLS Secretだけを使いTLS 1.3、server SAN=`workload-access-broker`、client SAN=`runtime-api`を両側で検証する。plaintext port/listener 0、HTTP downgrade/redirect 0、`verify=false` 0。wrong/expired CA/SAN/client certはbody parse前reject・memory record 0。cert rotationはnew CA bundle+new server/client certを先行配置→mutual canary→旧cert connection 0観測→旧CA/cert ref除去の順で、private keyをnetworkへ1 byteも送らない。
  - `session_handle`は新しいsecretやmappingではなく、既存server-generated canonical `session_uid`（lowercase UUID文字列）を使う。owner lookupの唯一sourceはprofile別の既存canonical record（meeting/browser=`Meeting`、agent=`Agent`）で、lookup keyは`owner_service+profile+session_uid`とし別profile/ownerの同文字列を同一視しない。新しいowner mapping DBを作らない。
  - R6では旧Gateway `/b/{token}`とCDP `?api_key=`を即時削除せず、新しい非secret `/b/{session_handle}` routeとdual serveする。new sessionは旧route/tokenを発行せず、old sessionだけlegacy counter付き旧routeを使う。OP-09-DRAINが`active_old_browser_sessions=0`と`active_old_agent_cdp_sessions=0`を証明した後、RF-09Bが旧route/query verifierを削除する。既存sessionをsilent disconnectして継続扱いにせず、maintenance時に新session受付をfreezeし明示terminal/recreate_requiredとする。
  - authorize/consumeをprofile-awareにする。meeting/browser profileはMeetingのserver-owned session owner recordと固定Meeting authorize/consume endpoint、standalone Agent CDPはAgentのserver-owned user/session/container recordと固定Agent authorize/consume endpointを唯一sourceにする。Gatewayは256-bit proposed `access_jti`を生成し、trusted identityで`sub,owner_service,profile,session_uid,session_handle,aud,operation,method,path,access_jti,exp<=30s`をowner authorize endpointへbody-bound送信する。ownerがsubject/recordを照合し`authorized` recordをSET NX/TTL保存した応答後だけ、Gatewayは同claimを`GATEWAY_WORKLOAD_ACCESS_IDENTITY_SECRET`で署名してbrokerへ送る。brokerは同じowner serviceのconsume endpointで既存recordをatomic `authorized -> consumed`にし、owner別`ACCESS_MEETING|ACCESS_AGENT` introspectionでrelay_jti active/public keyを照合してから署名challengeを行う。未登録、claim差、forged/replayed jtiはrelay challenge 0、Gateway crash recordはTTLで失効する。Meeting tokenをAgentへ、Agent tokenをMeeting/Browserへcross-replayした場合はowner lookup/relay connect 0。registration/state/access jtiを同値比較・共用しない。URL/history/Referer/access logにAPI key/private key 0。A subject+B handleはHTTP 403/WS 4403、broker/relay connect 0。
  - Gateway/BrowserへRuntime control-plane tokenやprivate keyを返さない。access broker APIはverified+consumed identityから得たowner/profile/session/audienceだけを使い、任意host/port/headerを表現できず、terminal時にowner registry revoke→connection close→relay key破棄を行う。一方のowner registry outage時に他ownerへfallbackしない。
  - Dockerはsessionごとのinternal bridgeを作り、対象workloadと`workload-access-broker`だけをattachする。Runtimeはcontrol call時だけDocker APIでnetwork lifecycleを管理しdata networkへattachしない。Docker bridge自体はegress denyにならないため、root-owned別process`runtime-network-policy-agent`だけに`CAP_NET_ADMIN`とhost network namespaceを与える。agentはnetwork listener、Docker socket、Kubernetes token、DB/Redis/service credentialを持たない。Unix socket directoryは`root:vexa-runtime` 0710、socketは0660で、agentは`SO_PEERCRED`のfixed Runtime UID/GIDと`RUNTIME_NETWORK_POLICY_MAC_SECRET`のone-time body MACを両方検証する。
  - Runtimeはnetwork-policy agentへcontainer/veth/ifindex/cgroupを指定しない。agent自身がsession作成時にopaque `network_handle`、専用managed bridge、root-owned cgroup subtree、one-time owner nonceを生成し、handleだけをRuntimeへ返す。Runtimeはbackend create時にそのhandleを参照できるが既存`lo,eth0,docker0`、control/infra bridge、host/root cgroup、他session handleを表現できない。apply/cleanup時にagent自身がDocker root-owned inspect stateと`bridge master == managed bridge`、veth peer、cgroup ancestry、session UID、owner nonceを再照合し、全一致した対象だけを操作する。agent入力は`network_handle,session_uid,profile,provider-registry ID,owner nonce`だけで、container ID/veth/ifindex/cgroup/host/CIDR/port/rule/digestを受けない。任意・stale・別session target、cleanup replay、handle改ざんはnft/cgroup/network change 0。agent自身がroot-owned immutable profile policy+provider/Git host registryからDNS検証済みIP/portを導出し、Runtime compromiseでも任意target/allow ruleを表現不能にする。host boot unitはDockerより前にpersistent base default-deny `inet` table/`DOCKER-USER` chainをrestoreし、agentがroot-owned MAC付きstate manifestとkernel managed bridge/cgroup/nft ruleをreconcileするまで全managed bridge packetをdenyする。process kill時はkernel rulesが残り、restart/reboot中もpacket 0。missing/reused handle/ifindex/cgroup、manifest/rule差、agent unavailableはworkload readiness false。atomic apply後だけreadiness ack、session終了時はowner/backend照合後rule+manifest cleanup。workloadへNET_ADMIN/host namespaceを与えない。
  - Kubernetesは`vexa-workloads` namespaceの`runtime.managed=true` Podをdefault-deny ingress/egress、`app=workload-access-broker`から6080/9223だけallow、workload相互traffic 0とする。Runtime Roleは同namespaceだけ、control namespace/Secrets/APIへのreachability 0をRF-05D1 contractどおり再検証する。
  - egress matrixをexact固定する。全profileはcontrolled DNS resolverのUDP/TCP 53だけ共通許可し、169.254.169.254、100.100.100.200、link-local、RFC1918、cluster/control namespace CIDR、Kubernetes API、Redis/Postgres/MinIO、Runtime control port、他workload CIDRをdenyする。meeting profileはMeeting event/media brokerとserver-side provider registryのexact host+resolved public CIDR/80/443、browser profileはMeeting browser brokerと明示public host/CIDR 80/443、agent profileはGateway/Agent brokerとRF-03Dのallow-listed Git host/pinned public CIDR 80/443だけ。workload/client入力はhost/CIDRを追加できない。proxy-enabled meetingはpublic 80/443 directをdenyしworkload-brokerだけ。WebRTC UDPは調査時fixtureで使用するprovider CIDR+3478/19302-19309だけをallowし、unknown provider CIDRが必要ならpolicyを広げず停止する。
  - LiteのVNC/CDP portは`bot-slot-wrapper.sh`を唯一のallocatorとし、`DISPLAY,VNC_PORT,WEBSOCKIFY_PORT,CDP_PORT,CDP_RELAY_PORT`を予約recordからentrypoint/supervisorへ渡す。entrypoint内の`:99/5900/6080/9222/9223` hard-codeを削除し、2 session fixtureで全port非重複、終了後予約0。Xvfbの`-ac`を削除してsession 0600 `XAUTHORITY`+MIT cookieを必須にし、x11vncはlocalhost+password file、PulseAudioはsession private socket/source/sinkだけを使う。root password fallback、sshd、openssh-server/sshpass、SSH port/UI表示を全profileから削除し、remote commandは認証済みbroker/Runtime execだけに限定する。
  - rolling順はauthenticated relay対応image→workload-access-broker→owner別state/introspection→new Gateway route→runtime-network-policy-agent/Kubernetes deny policy。deny policy前に正規Gateway goldenを通し、new sessionへdirect endpointをpublic発行しない。旧routeはOP-09までold session専用compatibilityとしてだけ残す。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-06I2`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-06I2`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/workload-access-broker/tests/test_access.py::{test_capability_is_bound_to_owner_service_profile_session_container_audience_and_registry,test_arbitrary_host_port_and_header_are_unrepresentable,test_terminal_revocation_closes_connections,test_access_and_egress_brokers_have_disjoint_secrets_and_processes,test_broker_has_no_control_plane_database_or_redis_credentials,test_registration_requires_runtime_mtls_signature_public_key_server_generated_dns_fixed_ports_and_backend_label,test_gateway_route_preserves_existing_vnc_and_cdp_contract}`
  - `services/workload-access-broker/tests/test_access.py::{test_runtime_generates_keypair_bootstraps_private_and_binds_public_key_to_exact_owner,test_bound_registration_check_does_not_enable_user_connect,test_ed25519_nonce_challenge_matches_registered_public_key_before_activation,test_private_key_never_crosses_service_network_or_persists,test_owner_specific_bind_activate_fail_revoke_and_introspection_never_cross_fallback,test_registration_body_is_digest_bound_and_registration_jti_is_one_time,test_restart_revokes_relay_jti_and_requires_session_recreation_without_key_restore,test_gateway_preregisters_access_jti_before_signing_and_consume_requires_exact_existing_record,test_unregistered_forged_mismatched_and_replayed_access_jti_make_zero_relay_challenges,test_access_jti_and_relay_jti_have_separate_one_time_and_lifetime_semantics,test_subject_a_cannot_open_subject_b_http_ws_vnc_or_cdp_session,test_session_handle_is_namespaced_by_owner_and_profile,test_old_route_cutover_requires_zero_old_browser_and_agent_sessions,test_url_query_referer_history_and_access_log_contain_no_api_key_or_private_key}`
  - `services/runtime-network-policy-agent/tests/test_policy.py::{test_socket_requires_runtime_peer_uid_and_one_time_mac,test_agent_creates_opaque_handle_managed_bridge_and_cgroup_and_runtime_cannot_choose_target,test_runtime_input_cannot_supply_container_veth_ifindex_cgroup_host_cidr_port_or_rule,test_control_infra_host_and_other_session_targets_change_zero_rules,test_agent_derives_exact_root_owned_profile_policy,test_wrong_reused_handle_ifindex_cgroup_owner_nonce_and_manifest_are_fail_closed,test_cleanup_replay_or_foreign_handle_changes_zero_network_state,test_process_kill_restart_and_host_reboot_keep_managed_packets_denied_until_reconcile,test_atomic_apply_readiness_and_cleanup}`
  - `tests3/unit/refactor/test_rf_06i2.py::{test_docker_uses_one_internal_network_per_session_with_workload_access_broker_only,test_kubernetes_default_denies_workload_ingress_and_allows_workload_access_broker_only,test_runtime_role_cannot_touch_control_namespace_or_secrets,test_exact_profile_egress_matrix_denies_metadata_control_plane_datastores_and_cross_workload,test_proxy_enabled_meeting_has_no_direct_public_egress,test_loopback_chrome_and_vnc_have_no_public_direct_listener,test_lite_allocates_unique_display_vnc_websocket_and_cdp_ports_and_cleans_reservations,test_lite_xauthority_and_audio_are_session_private,test_ssh_root_password_packages_ports_and_ui_are_absent}`
  - session A workloadからBの6080/9223へTCP/HTTP/WS/CDP全拒否、B page/cookie/keyboard side effect 0。wrong private-key proof/owner/profile時relay connect 0。正規Gateway pathだけdesktop/mobile VNC/CDP golden一致。
  - generated workloadにRuntime control-plane credential/global relay secret/K8s SA token 0。cleanup後network/key/connection/policy rule 0。
  - `V-BACKEND`, `V-MEETING`, `V-DASH`, `V-CORE`, `V-OPS`。
- リスクと戻し方: VNC/CDP tunnel、dynamic network cleanupでBrowser Session表示が止まる。deny policyを最後に適用し、正規Gateway smoke不合格ならpolicyを広げずtask未完で停止する。失敗branchを保持しRF-06I1のSHAから再実行する。
- 依存: RF-06I1, RF-06H, RF-06C2, RF-05D1B, RF-05A
- コミット: `RF-06I2 isolate workload vnc and cdp ingress by session`

### RF-06I3 Lite ProcessBackendをsecret分離しsingle-tenant限定へ固定

- 対象:
  - `services/runtime-api/runtime_api/backends/process.py:1-末尾`
  - `services/runtime-api/runtime_api/api.py:178-272`
  - `deploy/lite/Dockerfile.lite:1-末尾`
  - `deploy/lite/{entrypoint.sh,supervisord.conf,bot-slot-wrapper.sh}:1-末尾`
  - `deploy/helm/charts/vexa-lite/values.yaml:1-末尾`
  - `deploy/helm/charts/vexa-lite/templates/secret.yaml:1-末尾`
  - `deploy/helm/charts/vexa-lite/templates/deployment.yaml:1-末尾`
  - `deploy/helm/charts/vexa-lite/templates/dashboard-deployment.yaml:1-末尾`
  - 新規 `deploy/lite/lite-init.py:1-末尾`
  - 新規 `deploy/lite/lite-services.json:1-末尾`
  - 新規 `services/runtime-api/tests/test_process_backend_isolation.py:1-末尾`
  - 新規 `tests3/unit/refactor/test_rf_06i3.py:1-末尾`
- 問題: ProcessBackendは`os.environ.copy()`とhost subprocess execを使い、Liteはroot/same UID、Supervisor親env、共有filesystem/X11/audioを全processへ継承する。container/Kubernetes隔離だけ直しても別session・別service secretへ到達できる。
- 変更:
  - ProcessBackend createは空dictからprofile別exact allow-listを構築し、親envをcopy/mergeしない。allow-list外、`*_SECRET,*_TOKEN,*_KEY,DATABASE_URL,REDIS_URL,AWS_*,MINIO_*,KUBECONFIG,DOCKER_HOST`は子env 0。Bot childへ渡すのはserver生成session ID、RF-06C2 capability、broker URL、予約済みdisplay/port、localeだけ。ProcessBackend `exec`は生成時と同じsession UID/namespaceへ入れる実装がない限り403/501でhost subprocessを1回も起動しない。
  - Liteは汎用Supervisorのroot常駐をやめ、固定manifestだけを読む最小`lite-init` PID 1へ置換する。`lite-init`は起動時だけUID 0かつ`SETUID,SETGID,KILL`の3 capabilityを持ち、network listener、shell、任意command/config interpolationを持たない。service別0600 secret fileをopenし、各childへ必要なFDだけを渡して`env -i`、`setgroups([])→setgid→setuid→PR_SET_NO_NEW_PRIVS`、capability 0、umask 077で起動する。全childは起動barrier pipeで待機し、親が自身を`lite-monitor`非root UID/GIDへ変更して全capability/securebitsをpermanent dropし、`/proc/self/status`の`Uid/Gid/CapEff/CapPrm/CapBnd`を検証した後だけbarrierをrelease/readiness trueにする。drop失敗時はlistenerを開かず全child/FDを終了してcontainer failure。drop後はchild restart/host execを行わず、1 child終了でPID 1も終了してcontainer runtimeに全cgroup cleanupを委ねる。
  - service別secret FDとexact allow-listだけを読み、単一`secrets.env`を全programへsourceしない。Meeting/Admin/Gateway/Runtime/Broker/Botは別UID/GID、supplementary group 0、`no_new_privs`, capabilities 0、umask 077。session rootは0700、file 0600、private `TMPDIR/XDG_RUNTIME_DIR/XAUTHORITY`、終了時recursive cleanup。`/proc`はhidepid相当または別UIDから`environ/cmdline/fd` EACCESを必須にする。唯一のUID0/capability例外はreadiness前の`lite-init`で、shell/HTTP/debug routeから到達不能とする。
  - process recordは`pid,proc_starttime_ticks,pgid,uid,session_uid,exe_inode,command_digest`をcreate直後にimmutable保存する。inspect/exec/stop/remove/reaperはsignalまたはstatus判断前に`/proc/<pid>/{stat,status,exe}`を再読し全field一致、PGIDがserver-generated session group一致の場合だけ操作する。PID再利用、UID/session/exe/starttime/PGID不一致はstale tombstoneへ移してsignal 0、recordを別processへ付け替えない。100 create/stop/PID-reuse simulationで別service/session process kill 0。
  - ProcessBackend/Liteを`DEPLOYMENT_MODE=single-tenant-development`かつ同時session総数1専用とし、production、複数subject、Agent/browser multi-tenant profile、ownerを問わず2 session目はpreflight/create failure。sessionごとに未使用UID/GIDを割当て、終了時に全process/file/socket cleanupとidentity照合が完了するまでUIDを再利用しない。Docker/Kubernetes backendだけをmanaged production対応とする。single sessionでもRF-06I2のXAUTHORITY/audio/SSH廃止を必須にし、root shell/root passwordへfallbackしない。
  - Helm `vexa-lite`はRF-05Fのmain-container bootstrap例外をexact維持し、Dashboardはnonroot/capability 0のままにする。全Secret `envFrom`、service account token、host namespace/mount/socket、空securityContext、replica>1、autoscaling/ingressをrenderで拒否する。readiness probeは`lite-init` permanent drop proofと全child UID/FD allow-listを照合し、単にport openだけで成功しない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-06I3`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-06I3`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。
  - `services/runtime-api/tests/test_process_backend_isolation.py::{test_child_environment_starts_empty_and_contains_only_profile_allowlist,test_parent_database_redis_provider_and_signing_canaries_are_absent_from_proc_environ,test_exec_without_same_session_sandbox_returns_403_without_subprocess,test_session_directory_modes_unique_uid_owner_cleanup_and_safe_uid_reuse,test_any_second_session_and_managed_production_process_backend_fail_before_spawn}`
  - `services/runtime-api/tests/test_process_backend_isolation.py::{test_stop_remove_inspect_and_reaper_verify_pid_starttime_pgid_uid_session_exe_and_command,test_stale_pid_reused_by_another_uid_or_service_is_tombstoned_and_never_signaled,test_hundred_create_stop_pid_reuse_races_never_kill_unowned_process}`
  - `tests3/unit/refactor/test_rf_06i3.py::{test_every_lite_program_uses_env_i_separate_uid_and_separate_secret_file,test_no_shared_secret_file_or_parent_environment_inheritance,test_cross_uid_proc_secret_file_tmp_x11_and_audio_access_is_eacces,test_no_root_user_password_sshd_sshpass_or_host_exec_path,test_docker_and_kubernetes_remain_only_managed_production_backends}`
  - `tests3/unit/refactor/test_rf_06i3.py::{test_lite_init_has_only_setuid_setgid_kill_before_barrier_and_permanently_drops_all_before_readiness,test_lite_child_crash_exits_container_without_privileged_restart,test_vexa_lite_chart_has_exact_bootstrap_exception_and_nonroot_dashboard,test_vexa_lite_rejects_production_multiple_sessions_envfrom_service_account_token_and_host_mount}`
  - 実process fixtureでservice AからBの`/proc/<pid>/environ`, secret file, session dir, X11 socket/cookie, PulseAudio socketが全EACCES。readiness後の全processはroot UID/group/capability 0、cleanup後process/file/port 0。readiness前`lite-init`だけはexact 3 capability、0.0.0.0 listener 0。
- リスクと戻し方: 既存Lite multi-session利用は停止する。隔離を弱めず、単一利用者developmentへ明示移行するかDocker/Kubernetes backendへ切り替える。失敗branchを保持しRF-06I2のSHAから再実行する。
- 依存: RF-06I2, RF-05F
- コミット: `RF-06I3 isolate lite processes and restrict process backend to single tenant`

### RF-08 Browser workspace git操作をargv実行へ変更

- 対象: `services/vexa-bot/core/src/browser-session.ts:14-116`
- 問題: shell文字列へのrepo/ref/path埋め込み、credential入りURL、終了code無視によりcommand injection、token leak、false successがある。
- 変更:

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

RF-03DでAgent imageへ固定した`/system/bin/vexa-git-bootstrap`をBrowser profileにも同一hashでCOPYし、Browser側からは`spawn(helper,["--repo",credentialFreeUrl,"--branch",validatedRef],{stdio:["pipe",...]})`だけを使う。tokenは同じuint32 big-endian framed stdin、session-private `/run/vexa-git`、AskPass、credential-free origin、redirect禁止、allow-listed HTTPS host、DNS global判定+verified IP pin、Host/SNI維持、ref/path containment契約を再利用し、別実装のcredential helperを作らない。exit code非0は`{ok:false,code,safeMessage}`。

操作前に既存workspaceの`.git/config`、worktree/submodule configをRF-03D scrubberでinventoryし、userinfo remote、extraHeader、credential helper、禁止host/schemeがあればnetwork前にquarantineしてtoken rotationを要求する。clone/fetch/checkout成功後もremote/configを再検査し、log/exception/argv/env/remoteへtoken 0を確認してから`{ok:true,operation}`を返す。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-08`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-08`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/vexa-bot/core/src/refactor-tests/rf_08.test.ts::uses_the_exact_rf_03d_fixed_helper_and_framed_stdin`
  - `::passes_untrusted_values_as_argv`
  - `::never_puts_token_in_url_log_error_env_or_remote`
  - `::pins_verified_ip_while_preserving_original_host_and_sni`
  - `::quarantines_existing_credential_remote_extraheader_helper_and_submodule_before_network`
  - `::rejects_target_outside_workspace_root`
  - `::returns_failure_when_git_exits_nonzero`
  - `::supports_valid_clone_fetch_checkout`
  - `V-CORE`
- リスクと戻し方: credential helper差異でprivate repo cloneが失敗する。fake gitでargv/envを固定し、実private repo tokenをtestへ使わない。共通retry protocol時にも漏洩済みtokenはrotation対象として報告。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-03B, RF-03D, RF-06B, RF-00C
- コミット: `RF-08 execute browser git commands without a shell`

### RF-09A Browser保存の相関session channelをRedis互換のまま先行配備

- 対象:
  - `services/meeting-api/meeting_api/meetings.py:1307-1334`
  - `services/vexa-bot/core/src/browser-session.ts:150-277`
  - `services/api-gateway/main.py:2363-2386`
  - `services/runtime-api/profiles.yaml:58-79`
  - `services/meeting-api/meeting_api/bot_event_broker.py:1-末尾`（RF-06C2作成物）
  - `services/meeting-api/meeting_api/session_capability_registry.py:1-末尾`（RF-06C2作成物）
  - 新規 `services/meeting-api/tests/test_browser_session_save.py:1-末尾`
  - 新規 `services/vexa-bot/core/src/browser-session-save.test.ts:1-末尾`
- 問題: Redis Pub/Subはat-most-onceで、publish後subscribeすると高速応答を失う。共有`done`に相関IDがなく並列要求を取り違える。新WSへの移行とRedis credential除去を同時に行うと旧Browser Sessionをdrainできない。
- 変更:

```json
{
  "action": "save_storage",
  "request_id": "uuid",
  "session_uid": "claim-bound-session"
}
```

  - RF-06Bのbrowser-storage capabilityを流用せず、別の`aud=browser-session-control` capabilityを発行する。claimはuser/meeting/session/container、`operations=["save_storage","chat.send","command.receive"]`、iat/nbf/exp/jtiをbindし、callback/storage/event tokenを相互利用できない。
  - Browser SessionはMeeting event brokerへoutboundで1本の認証済みWSを確立する。Brokerはconnection registryをclaimのsession/containerへbindし、同一sessionの重複接続は新connectionを拒否する。client requestからbroker接続先、Redis key/channel、任意operationを指定できない。
  - Meeting APIは`request_id`ごとのwaiterをregistryへ登録してから同じsession WSへcommandを送信し、同一`request_id`の `{request_id,ok,code,safe_message}`だけを受理する。timeout/cancel/WS close時はregistryから削除する。unknown/duplicate/別session responseは他requestを完了させない。
  - Coreはnew WSでstructured responseを返す。new WSがある場合はRedisへ二重publishせずnewだけを使い、WS未提供の旧sessionだけ既存plain Redis responseをこのcommit中のfallbackとして維持する。Broker/Meeting→Core image→新Browser sessionの順で配備し、Redis credential/ACL userはRF-09Bまで残す。
  - browser-sessionのchat/commandもRF-06C2のsemantic brokerへ移し、`MeetingChatService`へraw Redis URLを渡さない。`chat_send`成功通知をcommand channelへ自己publishする現挙動はRF-00C characterizationでconsumer 0が証明された場合に削除し、consumerが1件でもあれば`voice_event.write`の`chat.sent`へ固定変換する。任意channelを残さない。
  - Gateway timeoutをMeeting timeoutより15秒長くする。new Browser SessionはWSを優先するが、旧instance rollback用にBrowser profileのRedis credentialと`browser-session-legacy` ACL userをこのcommitでは削除しない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-09A`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-09A`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/meeting-api/tests/test_browser_session_save.py::{test_registers_request_before_sending_command,test_two_concurrent_requests_accept_only_own_response,test_timeout_cancel_and_disconnect_remove_waiter,test_gateway_timeout_exceeds_meeting_timeout,test_wrong_session_duplicate_and_unknown_response_never_complete_request}`
  - `services/vexa-bot/core/src/browser-session-save.test.ts::{supports_correlated_session_channel,returns_structured_git_failure}`
  - 100並列fixtureで誤配送/重複/欠落0、timeout/close後waiter・WS registry 0。
  - new WS fixtureではRedis publish/subscribe 0。WSなしlegacy fixtureだけRedis path成功し、`browser-session-legacy` counter exact 1。
  - `V-MEETING`, `V-BACKEND`, `V-CORE`, `V-OPS`
- リスクと戻し方: deploy順の不一致とWS切断で保存が止まる。Broker/Meeting→Core image→新session切替の順にする。このcompatibility commit内だけ旧instanceへ戻せる。失敗branchを保持しRF-08のSHAから再実行する。
- 依存: RF-08, RF-06C2
- コミット: `RF-09A deploy correlated browser session commands with redis compatibility`

#### OP-09-DRAIN（非commit・人間operator停止点）

RF-06G〜09Aを実環境へZoom/egress/access broker/owner state/network policy→Gateway/Agent/Meeting→Core→new Browser/Agent Sessionの順で配備し、旧sessionをdrainできる認可済みoperatorだけが共通`operation-gate-v1`を提供する。`measurements={max_browser_session_lifetime_seconds,max_agent_session_lifetime_seconds,max_proxy_capability_ttl_seconds,ws_success_count:>0,new_browser_vnc_success_count:>0,new_browser_cdp_success_count:>0,new_agent_cdp_success_count:>0,browser_legacy_redis_count:0,legacy_vnc_token_route_count:0,legacy_cdp_query_token_count:0,active_old_browser_sessions:0,active_old_agent_cdp_sessions:0,proxy_broker_success_count:>0,legacy_proxy_credential_in_workload_count:0}`を各path個別に提供し、観測時間は`max(max_browser_session_lifetime_seconds,max_agent_session_lifetime_seconds,max_proxy_capability_ttl_seconds)+300`秒以上。new session admissionとold deployment generationをfreezeし、freeze lease ID/開始/有効期限/target environment/source SHAをartifactへbindする。旧proxy/Zoom/Browser Redis/VNC/CDP credentialはnew path成功→old値revoke→old canary reject→active session/ref 0を記録する。全旧sessionの明示終了だけで観測を短縮する例外は作らない。`bash scripts/test/run-refactor-operator-gate.sh --master-task full-repo-refactoring-2026-07-24 --archive-root "$CONTROL_ROOT/.pipeline/release-archives" --release r6 --gate op-09-drain`がexit 0になるまで進まず、fixture/source grepを代用しない。

### RF-09B Browser legacy Redis transportとcredentialをdrain後に除去

- 対象:
  - `services/meeting-api/meeting_api/meetings.py:1-末尾`
  - `services/meeting-api/meeting_api/bot_event_broker.py:1-末尾`（RF-06C2作成物、RF-09A変更済み）
  - `services/meeting-api/meeting_api/session_capability_registry.py:1-末尾`（RF-06C2作成物）
  - RF-09A後の `services/vexa-bot/core/src/browser-session.ts:1-末尾`
  - RF-09A後の `services/runtime-api/profiles.yaml:1-末尾`
  - RF-06C1後の `deploy/{compose,lite,helm}/**:1-末尾` にあるRedis ACL/NetworkPolicy
  - RF-06I2後の `services/{api-gateway,dashboard,agent-api}/**:1-末尾` にあるlegacy VNC/CDP route/query verifier。Meeting API側は上記3 exact fileだけ
  - 新規 `tests3/unit/refactor/test_rf_09b.py:1-末尾`
- 問題: RF-09Aはold session互換のRedis transport/credentialを残すため、Browser侵害からRedisへの直接到達がまだ可能。
- 変更:
  - OP-09-DRAINを検証後、Coreのplain Redis fallback、Browser profile/env/BOT_CONFIGの`REDIS_URL`/credential、`browser-session-legacy` ACL userを削除する。
  - Gateway/Dashboard/Meetingの旧`/b/{token}` routeとAgent/Browser CDP `?api_key=`発行・検証・URL assemblyを削除し、owner/profile namespaced `/b/{session_handle}`+preauthorized one-time access identityだけを残す。legacy route/queryは404/401かつowner lookup/broker/relay call 0、URL/history/Referer/access logにAPI key/private key 0。
  - Kubernetes NetworkPolicyを全`runtime.managed=true` PodからRedis ingress denyへ閉じ、Docker session networkからRedisを外す。Meeting/Runtime/Gateway等のservice principalだけinfra networkへ残す。
  - new WS unavailable時はRedisへfallbackせず、save/chat/commandを安全な503/connection errorで停止する。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-09B`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-09B`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `tests3/unit/refactor/test_rf_09b.py::{test_browser_legacy_acl_user_and_profile_secret_are_absent,test_legacy_vnc_token_route_and_cdp_query_token_are_absent,test_only_profile_aware_session_handle_and_preauthorized_access_identity_remain,test_all_managed_workloads_are_denied_direct_redis_ingress,test_ws_unavailable_never_falls_back_to_redis,test_service_principals_keep_required_infra_access}`
  - Browser/Meeting/Agent image内から既知Redis DNS/IPへ`PING/AUTH/GET/XADD/PUBLISH/SUBSCRIBE`はdenied/NOAUTH、side effect 0。Meeting service broker経由だけ成功。
  - generated Meeting Bot/Browser/Agent env/BOT_CONFIG/inspect/logのRedis URL/password 0。
  - `rg -n 'createClient|REDIS_URL|redisUrl|browser_session:' services/vexa-bot/core/src/browser-session.ts services/runtime-api/profiles.yaml` はnegative fixture以外0。`rg -n '/b/\{token\}|api_key=.*cdp|cdp.*api_key' services/api-gateway services/dashboard services/agent-api services/meeting-api`はnegative test以外0。
  - `V-MEETING`, `V-BACKEND`, `V-CORE`, `V-OPS`。
- リスクと戻し方: old sessionが残っていれば保存が止まる。OP-09-DRAINなしで開始しない。Redis credentialを新commitで戻さずRF-09A componentへrollbackしてtask未完とする。
- 依存: RF-09A, RF-06I2, OP-09-DRAIN
- コミット: `RF-09B remove browser redis access after session drain`

### RF-10 Meeting URL parserの単一契約化

- 対象:
  - `services/meeting-api/meeting_api/schemas.py:442-536,748-758`
  - `services/mcp/main.py:231-360`
  - `services/telegram-bot/bot.py:646-691`
  - 新規 `libs/meeting-contracts/pyproject.toml:1-末尾`
  - 新規 `libs/meeting-contracts/meeting_contracts/{__init__,url}.py:1-末尾`
  - 新規 `libs/meeting-contracts/tests/test_meeting_url_contract.py:1-末尾`
  - `services/{meeting-api,mcp,telegram-bot}/Dockerfile:1-末尾`
  - `deploy/lite/Dockerfile.lite:1-末尾`
  - 重複test `services/mcp/test_parse_meeting_url.py:1-末尾` と `services/mcp/tests/test_parse_meeting_url.py:1-末尾`
- 問題: parserが3実装へ分岐し、TelegramはTeamsを `microsoft_teams` + full URLで送りMeeting APIの `teams` + native ID契約に違反する。
- 変更:
  - install可能なpure package `libs/meeting-contracts`を作り、`ParsedMeetingUrl(platform, native_meeting_id, normalized_url, original_url, passcode, teams_base_host, warnings)` を実装する。
  - Google standard/nickname/lookup、Teams personal/enterprise/deep/msteams/legacy、Zoom j/w/wc/events/myを共有parameter fixtureへ置く。
  - Meeting API/MCP/Telegramは共有parserを呼ぶ薄いadapterにし、旧import名はre-exportする。
  - Telegramは `platform="teams"` と抽出済みnative IDをMeetingCreateへ送る。
  - MeetingCreateへは既存のpasscode、Teams base host、元meeting URLを落とさず渡す。MCPのwarning文面/条件も現在値をfixture化し、共有parserの`warnings`から同じresponseを作る。
  - `services/mcp/test_parse_meeting_url.py`のcaseをcanonical `services/mcp/tests/test_parse_meeting_url.py`と共有fixtureへ移し、旧fileを削除する。case削減は禁止。
  - RF-00E bootstrapへeditable installを追加し、Meeting/MCP/Telegram/Lite imageが同一commitでpackageをCOPY/installできるようDockerfileを更新する。sourceだけ先行してimport不能な中間commitを作らない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-10`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-10`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `libs/meeting-contracts/tests/test_meeting_url_contract.py::test_provider_url_matrix`
  - `::test_rejects_lookalike_hosts_and_invalid_ids`
  - `::test_preserves_original_url_passcode_teams_host_and_warnings`
  - `services/telegram-bot/tests/test_meeting_url.py::test_teams_request_matches_meeting_create_contract`
  - `services/mcp/tests/test_parse_meeting_url.py::test_canonical_cases_include_legacy_file_without_loss`
  - exact local Docker commands:
    - `docker build -f services/meeting-api/Dockerfile -t rf10-meeting .`
    - `docker build -f services/mcp/Dockerfile -t rf10-mcp .`
    - `docker build -f services/telegram-bot/Dockerfile -t rf10-telegram .`
    - `docker build -f deploy/lite/Dockerfile.lite -t rf10-lite .`
    - 各imageへ `docker run --rm <image> python -c 'from meeting_contracts.url import ParsedMeetingUrl'` を実行しexit 0。
  - `V-MEETING`, `V-INTEGRATIONS`。
- リスクと戻し方: 稀なURL variantのnormalization差、Docker contextへのpackage COPY漏れ。共有fixtureに現行全caseを先に移し、fixture差0と全clean image buildを確認する。Telegram Teamsの422修正だけが意図したbehavior差。失敗branchを保持し、前SHAから再実行する。
- 依存: RF-00B
- コミット: `RF-10 share one meeting URL contract`

### フェーズ1ゲート

RF-01、RF-02、RF-03A〜03D、RF-04A〜04B、RF-05A〜05H（RF-05C2/05F2/05G2を含む）、RF-06A〜06I3、RF-08〜10完了後、次を満たさなければフェーズ2へ進まない。

- 認証mismatchは全て401/403で、upstream side effect 0。
- fixture secretはresponse、log、exception、URLのいずれにも0件。
- generated Agent/Browser/Meeting Bot containerのenv/config/inspect/mount/Redis job/logにAdmin/Internal/Runtime/Storage/Redis/Transcription/Wake/Recording署名鍵/Provider/Zoom client secret/upstream proxy userinfoのglobal credential 0件。
- outbound security vectorは全serviceで同じ結果、redirect/private/unresolved attempt 0。
- Agent imageのclean buildと`vexa --help`が成功し、不存在`features/`参照0。
- task-id不正入力でrepo外書込0件。
- Browser保存100並列fixtureで誤配送0、timeout後subscriber 0。
- `V-MEETING`、`V-BACKEND`、`V-DASH`、`V-CORE`、`V-INTEGRATIONS`、`V-CLIENTS`、`V-OPS` のbaseline退行0。

```bash
set -Eeuo pipefail
test -n "${MASTER_TASK:-}"
test -n "${RELEASE_ID:-}"
test "${TASK:-}" = "${MASTER_TASK}-${RELEASE_ID}"
PHASE_ITEMS='RF-01,RF-02,RF-03A,RF-03B,RF-03C,RF-03D,RF-04A,RF-04B,RF-05A,RF-05B,RF-05C,RF-05D1,RF-05D1B,RF-05C2,RF-05D2,RF-05E,RF-05F,RF-05G,RF-05H,RF-06A,RF-06B,RF-06C1,RF-05F2,RF-05G2,RF-06C2,RF-06D1,RF-06D2,RF-06E,RF-06F,RF-06G,RF-06H,RF-06I1,RF-06I2,RF-06I3,RF-08,RF-09A,RF-09B,RF-10'
bash scripts/test/run-refactor-phase-stage.sh \
  --phase phase-1 --expect-items "$PHASE_ITEMS"
```

exit 0、`.pipeline/evidence/$TASK/phase-gates/phase-1.json`が`status=passed`、item ID byte一致、command/assertion 1件以上、`head_sha`=RF-10 commitであること。

## 5. フェーズ2: 正しさ・非同期競合・状態管理

### RF-11 callback種別間の終端意味を一致させる

- 対象: `services/meeting-api/meeting_api/callbacks.py:115-124,364-396,887-912`
- 問題: `exit_code == 0` のexit callbackは明示的な失敗理由を無視して`COMPLETED`にし、status callbackは同じ理由を`FAILED`にする。callback到着種別で同一会議の最終状態が変わる。
- 変更:
  - 失敗理由集合を1か所へ集約する。
  - 純粋関数 `classify_terminal_signal(exit_code, completion_reason, failure_stage)` を作り、exit/status callbackの両方が呼ぶ。
  - 優先順位は `明示的失敗理由 > 非0 exit code > 正常完了理由`。したがって `exit_code=0 + awaiting_admission_rejected` は`FAILED`。
  - DB commit、publish、post-meeting enqueueの順序は変更しない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-11`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-11`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - RF-00Bのstrict xfail `services/meeting-api/tests/test_lifecycle_characterization.py::test_terminal_matrix_snapshot[zero-exit-explicit-failure]` だけをmarker削除して通常passへ変更し、RF-00B matrix entryは変更しない。
  - `services/meeting-api/tests/test_callbacks.py::{test_exit_and_status_change_equivalent_for_every_completion_reason,test_duplicate_terminal_callback_is_idempotent}`
  - `V-MEETING`
- リスクと戻し方: これまでcompleted扱いだった一部履歴が今後failedになる。過去行はmigrationしない。unexpected reasonの期待値が不明なら追加推測せず中断。commit 共通retry protocolで新規挙動を戻せる。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-00B
- コミット: `RF-11 align terminal callback semantics`

### RF-12 transcript検索をリテラル一致へ固定

- 対象:
  - `services/dashboard/src/components/transcript/transcript-segment.tsx:57-69`
  - `services/dashboard/src/components/transcript/transcript-viewer.tsx:86` の既存escape実装
  - 新規 `services/dashboard/src/lib/text-search.ts:1-末尾`
  - 新規 `services/dashboard/tests/refactor/rf_12.test.ts:1-末尾`
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

- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-12`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-12`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_text_search.test.ts::treats_bracket_paren_backslash_and_dot_as_literals`
  - `::returns_original_text_for_empty_query`
  - `::preserves_case_insensitive_highlight`
  - Browser E2Eで `[`、`(`、`\`、`.` を順に検索しconsole error 0。
  - `V-DASH`
- リスクと戻し方: 正規表現検索を期待する利用者がいた可能性。ただしUIはregex modeを示していないためリテラルを契約とする。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-00C
- コミット: `RF-12 make transcript search literal-safe`

### RF-13 transcript dedupの直前候補をspeaker/stream単位へ分離

- 対象:
  - `packages/transcript-rendering/src/dedup.ts:27-142`
  - `packages/transcript-rendering/src/dedup.test.ts:131-141`
- 問題: 既存のoverlap heuristicが入力全体の「最後にacceptした1件」だけを比較するため、speaker A/B/Aの最後のAを最初のAと比較できない。既存identity/key契約を作り直す必要はない。
- 変更:
  - 現在のtext/time overlap判定、許容幅、pending/confirmed処理、`identity.ts`のsegment keyは1文字も変えない。
  - `dedup.ts` private `heuristicScopeKey`を追加し、meeting=`meeting_id ?? meetingInstanceId ?? "unknown"`、stream=`track_id ?? speaker_track_id ?? speakerTrackId ?? speakerSessionUid ?? session_uid ?? "unknown"`、speaker=`speaker ?? ""` の順に固定する。`identity.ts`のpublic export/APIは変えない。
  - 最後にacceptした出力indexを `heuristicScopeKey` ごとの`Map`へ保持する。既存heuristicがreplaceを返した場合は同じindexを維持、keep-both時だけ新indexへ更新、drop時はMapを更新しない。
  - 新候補は同じscopeの直前accept済みsegmentだけと既存heuristicで比較する。異speaker、異stream/sessionの同文発話は比較対象にせず保持する。
  - IDのないsegmentへ新しいrounded stable keyを発明しない。scopeに必要なidentityが欠ける場合は既存fallbackを使い、そのfallbackをtestへ固定する。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-13`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-13`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `packages/transcript-rendering/src/dedup.test.ts::removes_overlapping_interleaved_a_b_a_within_same_stream`
  - `::keeps_same_text_from_different_speakers`
  - `::keeps_identical_text_from_different_streams_or_sessions`
  - `::keeps_repeated_utterance_at_distinct_non_overlapping_times`
  - `::preserves_existing_pending_confirmed_behavior`
  - `V-TRANSCRIPT`, `V-DASH`
- リスクと戻し方: scope keyが粗いと正当な反復発話を消す。新しいidentity設計を入れず既存identity helperを使い、cross-speaker/cross-session fixtureが1件でも減ったら中断。失敗branchを保持し、前SHAから再実行する。
- 依存: RF-00C
- コミット: `RF-13 scope transcript overlap deduplication by speaker and stream`

### RF-14 複数sessionを絶対timelineへ正規化

- 対象:
  - `packages/transcript-rendering/src/manager.ts:61-67`
  - `packages/transcript-rendering/src/dedup.ts:227-251`
  - 新規 `packages/transcript-rendering/src/timeline.ts:1-末尾`
  - `packages/transcript-rendering/src/index.ts:1-末尾`
  - `services/dashboard/src/app/meetings/[id]/page.tsx:318-351`
- 問題: segmentには既に`absolute_start_time`がありPageもsession wallclockを導出するが、manager finalizeの末尾が再び相対`start_time`でsortし、再接続sessionの0秒発話を会議冒頭へ戻す。
- 変更:
  - `packages/transcript-rendering`へpure `sortByAbsoluteTimeline`を追加し、primary keyを既存`absolute_start_time`とする。
  - 同値tie-breakは既存session UID、segment ID、元入力indexの順で決定し、同一入力に常に同一順を返す。
  - `manager.finalize`、dedup後の最終整列、Dashboard live-store/viewerのcross-session表示をこのhelperへ切り替える。最終段で`sortByStartTime`を再適用しない。
  - 相対`start_time`/`end_time`はmedia seek用として一切変更しない。`SessionAnchor`、legacy anchor推定、API field/schemaを新設しない。
  - 既存`sortByStartTime` exportは同一session用途の互換APIとして残し、この項目で削除・意味変更しない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-14`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-14`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `packages/transcript-rendering/src/manager.test.ts::finalize_keeps_reconnected_session_after_earlier_absolute_session`
  - `packages/transcript-rendering/src/dedup.test.ts::sorts_cross_session_segments_by_absolute_start_time`
  - `::preserves_relative_start_and_end_for_playback`
  - `::uses_session_segment_and_input_order_as_deterministic_tie_break`
  - `V-TRANSCRIPT`, `V-DASH`
- リスクと戻し方: `absolute_start_time`欠落fixtureの順序差。欠落時は既存入力順fallbackを明示し、相対timeへcross-session fallbackしない。API/model新設はせず、失敗時は前SHAから再実行。
- 依存: RF-13
- コミット: `RF-14 order transcript sessions on one absolute timeline`

### RF-15 Meeting切替requestへgeneration guardを付ける

- 対象:
  - `services/dashboard/src/stores/meetings-store.ts:347-379,435-456,510-538`
  - `services/dashboard/src/app/meetings/[id]/page.tsx:805-815`
- 問題: Meeting Aの遅いtranscript/chat/artifact responseが、A→B切替後にBのstateへ書き込む。
- 変更:
  - storeに `{meetingId, generation}` のrequest tokenを発行する。
  - list/detailだけでなくtranscript、chat、artifact、recordingsの全async write前に現在token一致を確認する。
  - meeting切替・logout・store resetでgenerationをincrementし、AbortControllerをcancelする。
  - stale responseはerror stateも更新しない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-15`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-15`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_meetings_store_requests.test.ts::late_transcript_from_a_cannot_overwrite_b`
  - `::late_chat_and_artifact_from_a_are_ignored`
  - `::switch_aborts_all_inflight_meeting_requests`
  - `::current_request_updates_state_normally`
  - `V-DASH`
- リスクと戻し方: 正当なresponseまで捨てる可能性。token発行をmeeting-scoped action入口へ限定する。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-00C
- コミット: `RF-15 guard meeting-scoped async writes by generation`

### RF-16 再文字起こしpollingを単一controllerへ集約

- 対象:
  - `services/dashboard/src/components/transcript/transcript-viewer.tsx:406-443,1050-1094`
  - `services/dashboard/src/app/meetings/[id]/page.tsx:882-885`
  - 新規 `services/dashboard/src/hooks/use-retranscription-job.ts:1-末尾`
- 問題: ViewerとPageが同じstatusをpollし、Viewerの長時間loopに明確なcleanupがない。
- 変更:
  - `useRetranscriptionJob(meetingId)` を唯一のownerにする。
  - controllerは `idle|starting|polling|succeeded|failed|timed_out|cancelled` を持つ。poll間隔は現行2,500ms、上限は現行`1,050`回、すなわち43分45秒。transport errorはそのloopを`failed`で停止し、既存toastへ同じmessageを渡す。
  - 同時requestは1、unmount/meeting切替でAbort。再mount時にserver statusが`queued|running`なら新しい最大1,050回のloopを開始する。
  - Viewer/Pageは同じhook結果をprops/contextで共有し、独自`setInterval`/loopを削除する。
  - terminal statusで必ずtimer 0。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-16`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-16`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_retranscription_controller.test.ts::starts_only_one_poll_loop`
  - `::cancels_on_meeting_change_and_unmount`
  - `::stops_on_terminal_status`
  - `::times_out_after_configured_budget`
  - fake timer実行後pending timer 0。
  - `V-DASH`
- リスクと戻し方: loop owner変更でtoast/refresh時点がずれる可能性。2,500ms、1,050回、terminal mapping、messageをcharacterization testで固定し、差が出たら共通retry protocol。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-15
- コミット: `RF-16 own retranscription polling in one controller`

### RF-17 post-meeting pollingをsingle-flight化

- 対象:
  - `services/dashboard/src/app/meetings/[id]/page.tsx:865-945`
  - 新規 `services/dashboard/src/hooks/use-post-meeting-progress.ts:1-末尾`
- 問題: 複数effect/intervalがoverlapし、前回fetch完了前に次回fetchを起動し得る。
- 変更:
  - `usePostMeetingProgress(meetingId, initialStatus)` にownerを集約する。
  - request中は次tickを起動しない。完了後に`setTimeout`で次回をscheduleする。
  - status terminal、meeting切替、unmountでcancel。
  - recordings、artifacts、meeting detailのrefresh順を固定し、同一generation guardを使う。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-17`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-17`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_post_meeting_polling.test.ts::never_overlaps_requests`
  - `::refreshes_recording_artifact_and_meeting_in_order`
  - `::stops_after_terminal_or_dispose`
  - `::ignores_late_response_after_meeting_switch`
  - `V-DASH`
- リスクと戻し方: 更新間隔が伸びる。現行interval値をそのまま使用し、schedule方式だけ変更。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-15, RF-16
- コミット: `RF-17 make post-meeting polling single-flight`

### RF-18 live follow scrollをtranscript container内へ限定

- 対象:
  - `services/dashboard/src/components/transcript/transcript-viewer.tsx:650-681`
  - `services/dashboard/src/lib/transcript-scroll.ts:1-51`
  - `services/dashboard/tests/test_transcript_scroll.test.ts:7-70`
- 問題: live followだけ`scrollIntoView()`を使い、外側pageまで移動させ得る。
- 変更: `scrollTranscriptContainerToBottom(container)` を追加し、`container.scrollTo({top: max(0, scrollHeight-clientHeight)})` のみ使う。sentinelへの`scrollIntoView`を削除する。userがfollowを解除した状態では呼ばない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-18`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-18`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/dashboard/tests/test_transcript_scroll.test.ts::{scrolls_only_transcript_container_to_bottom,clamps_bottom_when_content_is_short,does_not_scroll_when_follow_is_disabled}`
  - E2Eでouter page `scrollY`不変、inner scrollだけ増加。
  - `V-DASH`
- リスクと戻し方: virtualized containerならscroll target差。現DOM fixtureでcontainer refを固定。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-00C
- コミット: `RF-18 keep live transcript scrolling inside its container`

### RF-19 WebSocket transcript sessionを1実装へ統合

- 対象:
  - `services/dashboard/src/hooks/use-live-transcripts.ts:160-270`
  - `services/dashboard/src/hooks/use-vexa-websocket.ts:56-168,285-322`
  - `services/dashboard/src/stores/live-store.ts:1-88`
  - `services/dashboard/next.config.ts:48-59`
  - `services/dashboard/src/app/docs/ws/page.tsx:1-末尾`
  - `services/dashboard/src/app/docs/ws/subscribe/page.tsx:1-末尾`
  - `services/dashboard/src/app/docs/auth/page.tsx:1-末尾`
  - `services/dashboard/src/app/docs/cookbook/track-meeting-status/page.tsx:1-末尾`
  - `services/dashboard/src/app/docs/cookbook/get-transcripts/page.tsx:1-末尾`
  - `packages/transcript-rendering/src/manager.ts:1-末尾`
  - `services/api-gateway/main.py:2487-2665`
  - `services/api-gateway/tests/{test_websocket.py,test_gate_g5_websocket.py}:1-末尾`
  - `services/wake-orchestrator/app/clients.py:116-127,744-751`
  - `services/wake-orchestrator/tests/test_clients.py:430-490`
  - `tests3/checks/run:1390-1420`
  - 新規 `services/dashboard/src/lib/live-transcript-session.ts:1-末尾`
- 問題: 会議詳細と`/join`でconfirmed/pending、token、retry、cleanupの意味が異なる。さらにGateway/Wake/docsはraw API keyをWebSocket URL queryへ載せ、URL履歴・proxy access log・例外へcredentialが残り得る。
- 変更:
  - transport/controller `LiveTranscriptSession` を作り、URL解決、接続、parse、backoff、disposeだけを担当させる。
  - 両画面が`TranscriptManager.handleMessage()`へ同じmessageを渡す。
  - pending空tickでdraftを消す。
  - browser credentialはstore、Cookie API、runtime configのどこからもJavaScriptで読まない。常にsame-origin/base-pathのqueryless `/ws`へ接続し、Next rewriteが既存HttpOnly `vexa-token` cookieとexact `Origin`をGatewayへ渡す。Gatewayはproduction allow-listに完全一致する`Origin`、Secure/HttpOnly/SameSite cookie、RF-05Aと同じtoken resolverによるactive subject/scope解決が全て成功した場合だけhandshakeをacceptする。Origin欠落はnon-browser header modeだけで許し、mismatch/null Origin、cookie+header subject不一致、無効tokenはRedis subscribe/task生成前に拒否する。
  - non-browser clientはqueryless `/ws`と`Authorization: Bearer <API key>`だけを使う。Wake Orchestrator、integration test、CLI/check、Python/Nodeのdocs例を同じcommitでheader modeへ移す。browser WebSocket APIは任意headerを設定できないため、public docsのbrowser JavaScript例はraw keyを埋め込まず、「認証済みsame-origin Dashboard `/ws`を使う」または「server-side bridgeでAuthorizationを付ける」例へ置換する。
  - Gatewayは`api_key`,`token`,`access_token` queryを値の有無にかかわらずhandshake前400/4400で拒否し、`X-API-Key`の旧WebSocket fallbackも削除する。credential候補がquery/subprotocol/subscribe frameへあればresolver/Redis/downstream call 0。認証subjectはhandshake時に1回だけimmutable化し、subscribe payloadのuser IDを受けず、RF-05A trusted identityでTranscription authorizationへ渡す。invalid tokenをacceptして後段へ委譲する現挙動を残さない。
  - WebSocket URL、access/error log、exception、close reason、metrics labelへraw credential 0。logは`transport=cookie|authorization`、route、status、nonsecret subject hashだけ。`rg`で`/ws?api_key=`とWebSocket URLを組み立てる`api_key=`をproduction/docs/checkから0にする。
  - reconnectは指数backoff + jitter、最大試行回数を既存UXに合わせて定数化し、dispose後は0回。
  - 旧hooksは薄いadapterとして1段階残し、callsiteを同一commitで移行する。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-19`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-19`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_live_transcript_session.test.ts::replaces_pending_for_same_speaker`
  - `::empty_pending_tick_clears_draft`
  - `::never_reads_browser_credentials_or_puts_tokens_in_url_subprotocol_or_subscribe_frame`
  - `::does_not_reconnect_after_cleanup`
  - `::routes_join_and_meeting_messages_through_same_manager`
  - `services/api-gateway/tests/test_websocket.py::{test_same_origin_secure_http_only_cookie_authenticates_before_accept,test_authorization_bearer_authenticates_non_browser_client,test_query_x_api_key_subprotocol_and_frame_credentials_are_rejected_before_resolver_redis_or_downstream,test_cookie_header_subject_mismatch_and_wrong_origin_fail_closed,test_subscribe_uses_handshake_subject_and_trusted_identity_only}`
  - `services/wake-orchestrator/tests/test_clients.py::test_vexa_websocket_uses_queryless_url_and_authorization_header_without_log_leak`
  - `rg -n '/ws\?api_key=|api_key=.*[/]ws|[/]ws.*api_key=' services packages tests3 --glob '!**/fixtures/**'`は0件。
  - `V-TRANSCRIPT`, `V-DASH`, `V-BACKEND`, `V-INTEGRATIONS`。
- リスクと戻し方: URL queryで接続する未管理clientは接続不能になる。実装前にrepo内client/docsを上記`rg`で全inventoryし同commitで移行する。外部利用者にはrelease noteでAuthorization headerまたはsame-origin bridgeを先行告知し、raw query token fallbackを復活させない。失敗branchを保持しRF-18直後の合格SHAから再実行する。
- 依存: RF-13, RF-14, RF-15, RF-05A
- コミット: `RF-19 unify live transcript websocket semantics`

### RF-20 Browser Session UIのsame-origin経路と状態分類を統一

- 対象:
  - `services/dashboard/README.md:74-85`
  - `services/dashboard/src/app/meetings/[id]/page.tsx:124-134,1150-1177,1890-1939`
  - `services/dashboard/src/components/meetings/browser-session-view.tsx:61-103`
  - `services/dashboard/src/hooks/use-runtime-config.ts:8-110`
  - `tests3/tests/static/dashboard-config-ssot.sh:48-64`
  - `tests3/tests/dashboard-browser-view.sh:2-9`
  - `tests3/tests/dashboard-browser-view.mjs:14-17,69-78`
  - `tests3/test-registry.yaml:653-656,709-712`
  - 新規 `services/dashboard/src/lib/browser-session-view-model.ts:1-末尾`
  - 新規 `services/dashboard/tests/refactor/rf_20.test.ts:1-末尾`
- 問題: VNC/saveがruntime `apiUrl`へ直接向き、READMEのsame-origin規約と違う。platform/data.mode判定が不統一で、開始中statusを終了表示し得る。
- 変更:
  - `browserSessionRoutes(meeting,runtimeConfig)` をpure helper化し、VNCとmutationは常に `withBasePath("/b/...")`。外部CDP表示だけ`runtimeConfig.publicApiUrl`を使う。`RuntimeConfig`へ`publicApiUrl:string`を追加し、`BrowserSessionView`独自の`GET /api/config`とlocal `apiUrl` stateを削除して共有`useRuntimeConfig()`の1 fetchへ統合する。
  - `isBrowserSessionMeeting = platform==="browser_session" || data.mode==="browser_session"` の1実装に統一し、存在しない`browser` literalは使わない。
  - statusを `starting=requested|awaiting_admission|joining`、`active=active|recording`、`terminal=completed|failed|stopped` に分類。
  - `services/dashboard/src/lib/browser-session-view-model.ts`へ `BrowserSessionViewModel`とbuilderを置き、Pageと既存`browser-session-view.tsx`は同じmodelを使う。この項目では新しいPanel componentを作らない。
  - tests3の旧「runtime API URLへ向くことを正とする」逆向きassertを、新しいsame-origin契約へ同じcommitで更新し、registryの説明も同期する。test削除やdisableで通さない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-20`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-20`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_browser_session_routes.test.ts::uses_same_origin_for_vnc_and_mutations`
  - `::uses_public_url_only_for_external_cdp`
  - `::recognizes_platform_only_browser_session`
  - `::uses_shared_runtime_config_without_component_local_config_fetch`
  - `::classifies_requested_and_awaiting_as_starting`
  - mobile/desktop E2E screenshotで同じstatus文言。
  - `V-DASH`, `V-OPS`
- リスクと戻し方: base path deploymentの二重prefix。`withBasePath`のinput/output fixtureをroot/subpath両方に置く。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-04A, RF-09B
- コミット: `RF-20 normalize browser session routes and lifecycle UI`

### RF-21 AudioPlayerのfragment/retry状態を有限化

- 対象: `services/dashboard/src/components/recording/audio-player.tsx:64-110,112-179,237-247`
- 問題: fragment数減少時にindexが範囲外となり、audio errorが1.5秒ごと無制限retryし、play rejectionを握り潰す。
- 変更:
  - pure `normalizeFragmentIndex(index, length)` と `nextMediaRetry(attempt)` を追加。
  - source list変更時にindex clamp、source identity変更時にretry budget reset。
  - 自動retry最大3回、1.5/3/6秒。以降は明示エラーとmanual retry。
  - `play()` rejectionをstateへ反映し、unmount後state更新を防ぐ。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-21`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-21`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_audio_playback_logic.test.ts::clamps_index_after_fragment_list_shrinks`
  - `::stops_automatic_retry_after_three_failures`
  - `::resets_retry_budget_for_new_source`
  - `::surfaces_play_rejection`
  - `V-DASH`
- リスクと戻し方: transient障害から自動復帰しなくなる可能性。manual retryを必須で残す。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-15
- コミット: `RF-21 bound audio playback retries`

### RF-22 VideoPlayerのstate/imperative APIを整合させる

- 対象: `services/dashboard/src/components/recording/video-player.tsx:18-36,81-88`
- 問題: setter宣言前にimperative handleから参照しlint errorとなり、play rejectionもUIへ届かない。
- 変更: state宣言をimperative handleより前へ移す。`play/pause/seek`を同じcontrollerへ集約し、Promise rejectionを`playbackError`へ保存。source変更でerrorをresetし、unmount済みrefを操作しない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-22`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-22`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_video_playback_logic.test.ts::imperative_play_surfaces_rejection`
  - `::source_change_resets_error`
  - `::dispose_prevents_late_state_update`
  - 対象fileのlint error 0。
  - `V-DASH`
- リスクと戻し方: imperative refの公開shapeを変えない。type snapshotで固定。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-00C
- コミット: `RF-22 make video playback state deterministic`

### RF-23 Decisions SSEのretry lifecycleを閉じる

- 対象: `services/dashboard/src/components/decisions/decisions-panel.tsx:364-477`
- 問題: reconnect timeoutをcleanupせず、unmount後にcaptured stateで再接続し得る。URL dependencyも不足。
- 変更: `DecisionSseController`へEventSource、retry timer、active flagを所有させる。`dispose()`はEventSource closeとtimer clearを必ず行う。URL変更時は旧controllerをdispose後に新規作成。指数backoffは最大30秒。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-23`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-23`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_decision_sse_controller.test.ts::cancels_retry_on_dispose`
  - `::reconnects_once_after_error`
  - `::uses_new_url_after_runtime_config_change`
  - `::never_opens_after_dispose`
  - `V-DASH`
- リスクと戻し方: reconnect頻度の差。fake timerで回数を固定。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-04A
- コミット: `RF-23 close decision SSE retry lifecycle`

### RF-24 Runtime schedulerを原子的かつ整合的にする

- 対象: `services/runtime-api/runtime_api/scheduler.py:92-155,290-315`
- 問題: `GET -> SET -> ZADD`が非原子的。SET後crashでidempotency keyが存在しないjobを指せる。retry `execute_at`未更新、pending jobをhistoryへ保存、lookup順も不整合。
- 変更:
  - RF-06C1のACLが`EVAL/EVALSHA`を禁止するためLuaを使わない。exact `scheduler:idem:<key>`を`WATCH`→`GET`し、既存job IDがあれば`executing/pending/history`のtyped repositoryで実在を確認して返す。不存在ならserver-generated job ID/payloadをmemoryで作り、`MULTI`内でjob payload `SET`、`ZADD scheduler:jobs score job_id`、idempotency `SET EX`をqueueして`EXEC`する。WatchError/EXEC nullはbounded jitter付き最大5回、毎回全keyを再読する。超過時503でpartial write 0。
  - idempotency keyがmissing jobを指す、jobはあるが`ZSCORE scheduler:jobs`とpayload `execute_at`が不一致、queue memberだけ orphanの各状態を検出したら新jobを黙って作らず`SchedulerConsistencyError`としてside effect 0で隔離する。明示maintenance `repair_scheduler_orphans --dry-run/--apply <signed inventory>`だけがexact keyを修復し、通常requestはrepairしない。
  - retry時は `execute_at=next_time` をpayloadとsorted-set scoreの両方へ反映。
  - historyへ保存するのはterminalのみ。
  - lookup順は `executing -> pending -> history`。
  - 既存Redis key prefixとjob JSON fieldは維持する。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-24`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-24`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/runtime-api/tests/test_scheduler.py::test_concurrent_idempotent_schedule_enqueues_exactly_once`
  - `services/runtime-api/tests/test_scheduler.py::test_idempotency_never_points_to_missing_job`
  - `services/runtime-api/tests/test_scheduler.py::test_watch_conflict_retries_with_bounded_jitter_and_no_partial_write`
  - `services/runtime-api/tests/test_scheduler.py::test_zscore_payload_and_idempotency_orphans_fail_closed_until_explicit_repair`
  - `services/runtime-api/tests/test_scheduler.py::{test_retry_updates_next_execute_at,test_retrying_job_is_not_terminal_history,test_get_job_prefers_live_state_over_history}`
  - RF-00Bのstrict xfail `services/runtime-api/tests/test_scheduler_characterization.py::test_job_and_terminal_history_schema_snapshot[non-atomic-retry]` だけをmarker削除して通常passへ変更し、RF-00B matrix entryは変更しない。
  - `V-BACKEND`
- リスクと戻し方: Redis WATCH競合と既存orphan検出でscheduleが503になり得る。使用中Redis versionでACL付きWATCH/MULTI/EXECをintegration fixture実行し、競合時に上限を広げない。key/schemaを変えないのでcommit 共通retry protocol可能だが、orphanを自動削除せずsigned inventoryで別途repairする。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-00B, RF-06C1
- コミット: `RF-24 make runtime scheduling atomic`

### RF-25 Agent container生成をkeyed lock化

- 対象:
  - `services/agent-api/agent_api/container_manager.py:35-40,122-186`
  - `services/agent-api/agent_api/chat.py:161-185`
- 問題: global `_new_container`が並列request間で共有され、同一user/sessionの二重spawnと別requestへのcreated flag漏れがある。
- 変更:
  - `(user_id, session_id)`ごとのasync lockを弱参照または完了時削除するregistryで管理。
  - `ensure_container()` は `EnsureContainerResult(name, created)` をrequest-localに返す。
  - global `_new_container` を削除し、chatは戻り値だけを見る。
  - spawn失敗時はlockを解放し、partial mappingを残さない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-25`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-25`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_container_manager.py::test_two_concurrent_ensure_calls_spawn_once`
  - `::test_created_flag_is_request_local`
  - `::test_failed_spawn_leaves_no_mapping_or_lock`
  - `V-BACKEND`
- リスクと戻し方: lock registry leak。完了後size 0のtestを追加。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-05A, RF-05B
- コミット: `RF-25 serialize agent container creation per session`

### RF-26 Calendar workerをlifespan管理し、eventをclaimする

- 対象:
  - `services/calendar-service/app/main.py:39-73`
  - `services/calendar-service/app/sync.py:244-308`
- 問題: startup taskを保持/cancelせず、複数workerが同じpending eventを処理でき、eventごとにHTTP clientを作る。
- 変更:
  - FastAPI lifespanでworker taskと共有`httpx.AsyncClient`を作り、shutdownでcancel/await/close。
  - `schedule_upcoming_bots`は、dueな`pending` eventを `ORDER BY start_time, id FOR UPDATE SKIP LOCKED LIMIT 1` で1件取得し、row lockを保持した同一transaction内でMeeting API呼出しと`scheduled|failed`更新を行ってcommitする。次のeventは新transactionで取得する。
  - process crash/exception時はtransactionをrollbackし、rowは`pending`のまま次回対象にする。HTTP non-2xxは現行どおり`failed`。新status/column/leaseは追加しない。
  - 共有clientは`sync_loop`から引数で渡し、eventごとの`AsyncClient`生成を削除する。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-26`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-26`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_sync_worker.py::test_worker_is_cancelled_on_shutdown`
  - `::test_two_workers_claim_event_once`
  - `::test_shared_http_client_closed_once`
  - `::test_crash_rolls_back_and_leaves_event_pending`
  - `::test_http_non_2xx_preserves_existing_failed_status`
  - `V-BACKEND`
- リスクと戻し方: HTTP呼出し中に最大30秒row lockを保持する。対象は同じeventの重複投入防止に限定し、eventを1件ずつ処理する。PostgreSQL以外のproduction dialectが見つかったら中断し、migrationや別claim方式を推測しない。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-05A
- コミット: `RF-26 manage calendar sync worker lifecycle`

### RF-27 Wake STTのspeaker stateとtaskを回収する

- 対象: `services/wake-stt/app/service.py:157-171,197-212,270-272`
- 問題: speaker stateを削除せず、transcription taskを追跡しないため長時間稼働でmemory/taskが増える。
- 変更:
  - `SpeakerState.last_ingest_ms`を使い、`state.in_flight == false`、active sessionなし、finalize/fast-command taskなしのstateだけを、最終ingestから `max(60_000, settings.idle_reset_ms * 10)` ms後に削除する。
  - evictionは60秒ごとのmaintenance taskで行い、設定field `WAKE_STT_STATE_TTL_MS` があればその正整数を使う。defaultは上記式。default未満の値は起動時validation errorにして、utterance途中を削除できないようにする。
  - 全create_taskをsetへ登録し、done callbackで削除。
  - service `close()` で受付停止、task cancel/await、state clear。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-27`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-27`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_service_lifecycle.py::test_idle_speaker_state_is_evicted`
  - `::test_active_speaker_is_not_evicted`
  - `::test_close_cancels_inflight_transcription_tasks`
  - `::test_task_registry_returns_to_zero`
  - `V-AUX`。
- リスクと戻し方: 長い無音後のspeaker continuity。TTLを既存session timeout以上に設定しfixtureで境界確認。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-00B
- コミット: `RF-27 bound wake STT state and tasks`

### RF-28 Wake Orchestratorのmeeting/cacheを上限付きにする

- 対象:
  - `services/wake-orchestrator/app/main.py:68-103`
  - `services/wake-orchestrator/app/orchestrator.py:115-130,360-461`
- 問題: meeting orchestrator、`_seen_segments`、speaker dedupe mapが無制限に増える。
- 変更:
  - `WakeOrchestrator.last_activity_monotonic`を全messageで更新し、stateが`IDLE`、pending wake/taskなしのinstanceだけを30分無活動後に`close()`してregistryから削除する。tickerは60秒ごとにeviction判定する。
  - `_seen_segments`は`OrderedDict`で最大5,000 ID、各entry TTL 30分。read/write時にLRU更新し、上限超過時は最古を削除する。
  - `_last_wake_by_speaker`は`wake_same_speaker_dedupe_ms <= 0`なら保存しない。正数ならTTLを`max(60秒, dedupe値)`、最大1,000 keyとする。
  - `WAKE_ORCHESTRATOR_IDLE_TTL_SECONDS` default 1800、`WAKE_SEEN_SEGMENT_TTL_SECONDS` default 1800、`WAKE_SEEN_SEGMENT_MAX` default 5000としてvalidateし、0/負数は起動時error。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-28`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-28`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_orchestrator_cache.py::test_idle_meeting_orchestrator_is_evicted`
  - `::test_active_meeting_is_retained`
  - `::test_seen_segment_cache_is_bounded`
  - `::test_duplicate_inside_window_is_suppressed`
  - `V-AUX`。
- リスクと戻し方: TTL外の再送を再処理する可能性。既存最大再送時間より長いTTLを選ぶ。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-00B
- コミット: `RF-28 bound wake orchestrator state`

### RF-29 TTS model loadを排他し、WAV合成をevent loop外へ出す

- 対象: `services/tts-service/main.py:331-349,447-551`
- 問題: 同一voice初回loadが競合し、async endpoint内の同期WAV処理とbytes連結がevent loopを止める。
- 変更:
  - voiceごとのasync lockでdouble-check loadする。
  - model/providerのthread safetyを実行者判断にしない。同じvoiceのloadとsynthesisは同じper-voice `asyncio.Lock`内で直列化し、異なるvoiceだけ並列を許す。lockを保持したままCPU/同期I/OのWAV合成を`await asyncio.to_thread(...)`で実行する。
  - chunkはlist/`bytearray`へ蓄積し最後に1回結合。
  - request cancel時も共有model cacheを壊さない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-29`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-29`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_tts_concurrency.py::test_concurrent_first_load_only_loads_once`
  - `::test_health_remains_responsive_during_wav_synthesis`
  - `::test_same_voice_synthesis_max_concurrency_is_one`
  - `::test_different_voices_can_synthesize_in_parallel`
  - `::test_cancelled_request_does_not_remove_loaded_voice`
  - `::test_output_wav_bytes_match_baseline`
  - `V-AUX`。
- リスクと戻し方: 同一voice throughputが直列化される。これはthread safetyを推測しないための固定trade-offであり、lockを外して最適化しない。health responsiveness、different-voice並列、output byte goldenのどれかが崩れたら中断し前SHAから再実行する。
- 依存: RF-00B
- コミット: `RF-29 isolate TTS loading and synthesis`

### RF-30 Voiceprint model load失敗をhealthへ反映

- 対象: `services/voiceprint-service/main.py:146-172,214-220`
- 問題: model load task失敗後もhealthが永久に`loading`を返し、運用が失敗を判定できない。
- 変更: `ModelLoadState(status="loading|ready|failed", safe_error, task)` を単一sourceにする。done callbackでexceptionを取得し`failed`へ遷移。healthはfailedで503と安全なerror codeを返す。retryは既存の明示reload endpointがなければ追加しない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-30`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-30`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_health.py::test_health_reports_failed_after_model_load_error`
  - `::test_health_reports_ready_after_success`
  - `::test_loader_exception_is_retrieved`
  - `::test_health_does_not_expose_secret_or_stack`
  - `V-AUX`。
- リスクと戻し方: readiness probeが503でrestart loopになる。これは現状の隠れたfailureを可視化する意図した変更で、deployment probeのrestart policyを変更しない。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-00B
- コミット: `RF-30 expose voiceprint load failure in health`

### フェーズ2ゲート

- RF-00B/00Cのgolden差は、RF-11、RF-12、RF-13、RF-14、RF-24で明示した差だけ。
- fake timerを全て進めた後、Dashboardのpoll/reconnect timerとlistenerが0。
- Meeting A→B高速切替100回fixtureでAのdataがBへ0件。
- scheduler 100並列fixtureでjob 1件、idempotency dangling 0件。
- 全補助service lifecycle testでtask/cache件数が終了時0または設定上限以下。
- `V-MEETING`、`V-BACKEND`、`V-DASH`、`V-TRANSCRIPT`、対象service suiteがpass。

```bash
set -Eeuo pipefail
test -n "${MASTER_TASK:-}"
test -n "${RELEASE_ID:-}"
test "${TASK:-}" = "${MASTER_TASK}-${RELEASE_ID}"
PHASE_ITEMS='RF-11,RF-12,RF-13,RF-14,RF-15,RF-16,RF-17,RF-18,RF-19,RF-20,RF-21,RF-22,RF-23,RF-24,RF-25,RF-26,RF-27,RF-28,RF-29,RF-30'
bash scripts/test/run-refactor-phase-stage.sh \
  --phase phase-2 --expect-items "$PHASE_ITEMS"
```

exit 0、`.pipeline/evidence/$TASK/phase-gates/phase-2.json`が`status=passed`、item ID byte一致、command/assertion 1件以上、`head_sha`=RF-30 commitであること。

## 6. フェーズ3: tests3・Deploy・Harnessを「実行した事実」に一致させる

### RF-31 report statusをpass/skip/invalid/failへ正規化

- 対象:
  - `tests3/checks/run:1575-1606,1676-1694`
  - `tests3/lib/{aggregate.py,common.sh,run-matrix.sh}:1-末尾`
  - 新規 `tests3/lib/report_status.py:1-末尾`（status normalizationとreader契約の唯一の実装先）
- 問題: 全step skipとstep 0件がpassになり、「検査済み」と誤解される。
- 変更:

```text
fail step >= 1               -> fail
実行済みpass >= 1, fail 0    -> pass
step >= 1, 全step skip        -> skip
step == 0                     -> invalid
schema/runner error           -> invalid
```

判定を1つのPython helperへ置き、shell runnerはその結果を使う。既存JSON fieldを削除せず、`status` enumを拡張し、aggregateも同じ意味で読む。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-31`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-31`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_report_status.py::test_all_skip_is_skip`
  - `::test_zero_steps_is_invalid`
  - `::test_pass_plus_skip_is_pass`
  - `::test_any_failure_is_fail`
  - RF-00Dの該当xfailを通常passへ変更。
  - `V-OPS`
- リスクと戻し方: downstreamが新statusを知らない。reader/schema/runnerを同一commitで対応し、unknown statusはinvalid。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-00D
- コミット: `RF-31 make test report status truthful`

### RF-32 active registry script不存在をfatalにする

- 対象:
  - `tests3/test-registry.yaml:1-末尾`
  - `tests3/lib/run-matrix.sh:168-176,209-221`
  - read-only input: `tests3/unit/fixtures/registry-baseline.json:1-末尾`（RF-00D作成物）
- 問題: active登録91件中45件のscriptが存在しないのにmatrixが成功する。
- 変更:
  - registry entryへ `status: active|disabled|retired`、非activeには必須 `reason`、`source_commit`、`review_after` を定義する。
  - baselineで不存在の45件は、削除commit `a51b952...` で実行体が消えた事実を理由に`disabled`へ明示分類する。機能coverageが代替されたとは書かない。
  - `active + missing` は実行前integrity errorでexit 2。
  - `disabled/retired`でもreason/source/review_after欠落、期限超過はexit 2。
  - 実行体を復元しない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-32`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-32`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_matrix_contract.py::test_missing_active_script_is_fatal`
  - `::test_disabled_script_requires_reason_source_and_review_date`
  - `::test_expired_disabled_entry_is_fatal`
  - `::test_baseline_missing_entries_are_explicitly_classified`
  - matrix summaryにactive/disabled/retired件数が表示される。
  - `V-OPS`
- リスクと戻し方: matrixが即時赤化する。分類漏れを無言で削除せず修正。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-31
- コミット: `RF-32 fail on missing active test scripts`

### RF-33 aggregateを非空・適用可能性の明示契約へ変更

- 対象:
  - `tests3/lib/aggregate.py:164-213,559-565,596-650`
  - `tests3/Makefile:164-175`
  - 新規 `tests3/gate-applicability.json:1-末尾`
- 問題: report 0件、feature 0件でgateが0。`validate-all`は`report-gate`でなく`report`へ依存する。
- 変更:
  - gateは`reports >= 1`を必須。
  - feature contractは、現在OSS外へ移した事実を示すversioned `gate-applicability.json` に `applicable:false`, `reason`, `source_commit=2d93eca...`, `owner`, `review_after` がある場合だけnot-applicableを許可する。
  - policy欠落/期限超過/feature 0かつapplicableはfail。
  - `validate-all`を`report-gate`へ接続。
  - 0件をWARNで通すbranchを削除。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-33`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-33`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_aggregate_gate.py::test_gate_fails_without_reports`
  - `::test_gate_fails_without_feature_catalog_or_not_applicable_policy`
  - `::test_expired_not_applicable_policy_fails`
  - `::test_minimal_complete_fixture_passes`
  - `test_make_contract.py::test_validate_all_invokes_report_gate`
  - `V-OPS`
- リスクと戻し方: 現行all-modeがredになる。RF-31/32後にのみ実施。過去feature sidecarを復元しない。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-31, RF-32
- コミット: `RF-33 require non-empty applicable test evidence`

### RF-34 smoke stampを入力fingerprintへ結び付ける

- 対象:
  - `tests3/Makefile:39-42,63-73,329-331`
  - runtime output: `tests3/.state/.smoke-passed:1-末尾`
- 問題: source/registry/config変更後も古いstampでsmokeを省略できる。
- 変更:
  - stamp JSONへHEAD、dirty tracked diff hash、registry hash、checks registry hash、runner/config hash、実行command versionを保存。
  - 利用時に全field一致を検証し、1つでも違えばsmoke再実行。
  - simple timestamp fileは廃止するが、既存stampを移行せずcache missとして扱う。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-34`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-34`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_smoke_stamp.py::test_source_change_invalidates_stamp`
  - `::test_registry_change_invalidates_stamp`
  - `::test_unchanged_inputs_reuse_stamp`
  - `::test_legacy_stamp_is_cache_miss`
  - `V-OPS`
- リスクと戻し方: smoke実行回数増加。正しい安全側。共通retry protocol時は旧stampを復元しない。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-33
- コミット: `RF-34 bind smoke cache to verified inputs`

### RF-35 三つのregistryへ役割とparity検査を与える

- 対象:
  - `tests3/test-registry.yaml:1-793`
  - `tests3/checks/registry.json:1-1581`
  - `tests3/registry.yaml:1-3779`
  - `tests3/Makefile:1-338`
  - `tests3/lib/run-matrix.sh:1-223`
  - `tests3/lib/aggregate.py:1-654`
  - 新規 `tests3/tools/registry_parity.py:1-末尾`
  - 新規 `tests3/unit/refactor/test_rf_35.py:1-末尾`
- 問題: 三つのregistryのcanonical/derived/legacy区分がなく、`tests3/registry.yaml`は実行経路から参照されていない。
- 変更:
  - `test-registry.yaml`を実行testのcanonical source、`checks/registry.json`をcheck implementation catalog、`registry.yaml`をlegacy metadata sourceと明記する。
  - parity toolでID重複、orphan、同一IDのmode/tier/path不一致、未消化legacy metadataをJSON出力する。
  - この項目ではregistryを削除・統合しない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-35`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-35`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_registry_parity.py::test_duplicate_ids_fail`
  - `::test_orphan_check_fails`
  - `::test_conflicting_execution_metadata_fails`
  - `::test_current_registry_snapshot_has_documented_exceptions_only`
  - `tests3/unit/refactor/test_rf_35.py::test_current_registry_snapshot_has_documented_exceptions_only`が`sys.executable,tests3/tools/registry_parity.py,--check`を`shell=False`で実行し、exit 0とmachine JSONをassertする。別direct commandとしては実行しない。
  - `V-OPS`
- リスクと戻し方: 現在の不整合が多数出る。例外はID、理由、owner、review_afterを必須とし無制限allowlistにしない。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-32
- コミット: `RF-35 define registry roles and enforce parity`

### RF-36 legacy registry metadataをcanonicalへ移し、参照不能registryを削除

- 対象:
  - `tests3/registry.yaml:1-3779`
  - `tests3/test-registry.yaml:1-793`
  - `tests3/checks/registry.json:1-1581`
  - 既存（RF-35で追加済み） `tests3/tools/registry_parity.py:1-末尾`
  - `tests3/Makefile:1-338`
  - `tests3/lib/run-matrix.sh:1-223`
  - `tests3/lib/aggregate.py:1-654`
  - 新規 `tests3/registry-migration.json:1-末尾`
  - 新規 `tests3/unit/refactor/test_rf_36.py:1-末尾`
- 問題: 未参照registryが将来の実行対象に見え、更新先を誤らせる。
- 変更:
  - RF-35 parity outputが列挙するlegacy固有metadataを、実行testなら`test-registry.yaml`、check implementationなら`checks/registry.json`へfield単位で移す。
  - 全fieldのdestination mappingを `registry-migration.json` に残す。
  - parityでlegacy固有情報0、runtime reference 0を確認後、`tests3/registry.yaml`を削除。
  - reader fallbackを追加せず、docsを二registry構成へ更新。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-36`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-36`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_registry_parity.py::test_no_legacy_metadata_is_lost`
  - `::test_runtime_reads_only_canonical_registries`
  - `rg -n "tests3/registry.yaml|registry.yaml" tests3 Makefile .github` がmigration record/docsの過去説明以外0。
  - `V-OPS`
- リスクと戻し方: 隠れconsumer。repo全体`rg`とCI workflow解析で0確認。migration commitを共通retry protocolすればfileも戻る。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-35
- コミット: `RF-36 retire the unused legacy test registry`

### RF-37 changed-file resolverをcanonical path mapへ置換

- 対象:
  - `tests3/resolve.py:8-15,25-47,153-173`
  - `tests3/Makefile:42-50`
  - `.github/workflows/rung.yml:22-32`
- 問題: deprecated resolverが`packages/`、`libs/`、`contracts/`、`schemas/`、`scripts/`、workflow、Harness変更を拾えずsmokeへ縮退する。
- 変更:
  - canonical registry entryへpath globとrequired tierを持たせ、resolverはそこからmapを構築。
  - shared pathは依存する全suiteを選ぶ。`.github/`、`scripts/harness/`、`.claude/hooks/`、`schemas/`、`tests3/`は最低`smoke + ops/harness`。
  - unknown tracked pathは成功縮退せず、明示`full` fallbackまたはexit 2。CIは`full` fallbackを採用。
  - deprecated warningと旧hard-coded tableを削除。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-37`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-37`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_resolve.py::test_packages_and_libs_select_consumers`
  - `::test_harness_and_schema_changes_select_harness_gates`
  - `::test_workflow_change_selects_full_ci_validation`
  - `::test_unknown_path_falls_back_to_full`
  - `::test_docs_only_uses_documented_lightweight_set`
  - `V-OPS`
- リスクと戻し方: CI時間増加。false negativeを避けるため安全側。path mappingを緩めず、必要なら別承認で性能最適化。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-35
- コミット: `RF-37 resolve changed files from canonical test ownership`

### RF-38 Compose readinessをfail-closedにする

- 対象: `deploy/compose/Makefile:298-340`
- 問題: Dashboard待機timeout、API/Admin/Dashboard health失敗をechoするだけで成功終了する。
- 変更:
  - bounded retry helperが各必須endpointの試行回数、最終status、elapsedを返す。
  - 必須endpointを全て検査し、失敗一覧を表示後に1件でも失敗ならnon-zero。
  - optional serviceはcatalogの`optional`に限りskip可。endpoint名のhard-coded善意除外をしない。
  - 実Docker testより先にRF-00D fake curl/dockerを使う。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-38`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-38`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_compose_readiness.py::test_all_required_endpoints_must_succeed`
  - `::test_partial_failure_returns_nonzero`
  - `::test_timeout_returns_nonzero`
  - `::test_optional_catalog_service_may_be_absent`
  - fake test後に実environmentが利用可能なCIでCompose smoke pass。
  - `V-OPS`
- リスクと戻し方: 起動が遅い環境でred化。既存timeoutを維持し、必要なら設定値で上げるが失敗をpassにしない。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-31, RF-35
- コミット: `RF-38 fail compose readiness on unhealthy services`

### RF-39 Lite readinessとschema initをfail-closedにする

- 対象:
  - `deploy/lite/Makefile:128-200`
  - `deploy/lite/entrypoint.sh:1-末尾`
- 問題: Postgres/API timeout後もready表示し、`init-db`が全errorを`|| true`で捨てる。
- 変更:
  - Postgres/API readinessはRF-38と同じ結果契約を使い、timeoutでnon-zero。
  - schema syncは既知の冪等状態だけexit 0として明示分類し、それ以外をfatal。
  - success文言はcommand exit 0確認後だけ表示。
  - fake command fixtureでstderr/exitを固定。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-39`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-39`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_lite_readiness.py::test_postgres_timeout_is_fatal`
  - `::test_api_timeout_is_fatal`
  - `::test_unknown_schema_error_is_fatal`
  - `::test_known_idempotent_schema_state_passes`
  - `::test_success_message_only_after_success`
  - `V-OPS`
- リスクと戻し方: 過去に無視していたschema errorで起動停止。正しいfail-closed。既知エラー追加はerror code/fixture必須。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-38
- コミット: `RF-39 fail lite startup on readiness or schema errors`

### RF-40 Runtime profile guardをcheckとrepairへ分離

- 対象:
  - `deploy/compose/scripts/guard-runtime-profiles.sh:103-126,140-195`
  - `deploy/compose/Makefile:1-末尾`
- 問題: 「検査」が既定で5serviceをforce-recreateし、内部失敗を`|| true`で隠す。
- 変更:
  - `check-runtime-profiles` はread-onlyで期待/実際のimage/env/profile差を列挙し、差異でnon-zero。
  - `repair-runtime-profiles` だけが明示confirmation flag付きでrecreateし、各失敗を伝播。
  - `make test`/CIはcheck-only。自動repairを呼ばない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-40`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-40`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_runtime_profile_guard.py::test_check_never_calls_mutating_docker_commands`
  - `::test_check_fails_on_drift`
  - `::test_repair_requires_explicit_flag`
  - `::test_repair_propagates_recreate_failure`
  - `V-OPS`
- リスクと戻し方: 開発者が手動repairを必要とする。commandをREADMEへ明記。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-00D
- コミット: `RF-40 separate runtime profile checks from repair`

### RF-41 Shell portability helperへ非互換commandを集約

- 対象:
  - `tests3/lib/common.sh:347-375`
  - `deploy/compose/Makefile:1-末尾`
  - `deploy/helm/tests/test_template.sh:1-末尾`
  - `deploy/lite/Dockerfile.lite:1-末尾`
  - `tests3/lib/reset/redeploy-compose.sh:1-末尾`
  - `tests3/lib/reset/redeploy-lite.sh:1-末尾`
  - `tests3/lib/reset/reset-compose.sh:1-末尾`
  - `tests3/lib/reset/reset-lite.sh:1-末尾`
  - `tests3/lib/vm-setup-compose.sh:1-末尾`
  - `tests3/lib/vm-setup-lite.sh:1-末尾`
  - `tests3/tests/collect.sh:1-末尾`
  - `tests3/tests/meeting-tts-teams.sh:1-末尾`
  - `tests3/tests/meeting-tts.sh:1-末尾`
  - `tests3/tests/static/dashboard-config-ssot.sh:1-末尾`
  - `tests3/tests/transcribe.sh:1-末尾`
  - `tests3/tests/transcription-replay.sh:1-末尾`
  - `tests3/tests/v0.10.6.1-tts-auto-lang.sh:1-末尾`
  - `tests3/tests/webhooks.sh:1-末尾`
  - 新規 `tests3/lib/portability.py:1-末尾`
  - 新規 `.github/workflows/shell-portability.yml:1-末尾`
  - read-only inventory: `git grep -n -E -e 'head -n -1|sed -i|date -Iseconds|stat -c%s' -- deploy/compose deploy/lite deploy/helm scripts/harness tests3`。期待pathは上記18既存fileだけで、別pathが1件でもあれば停止してplan reviewへ戻る
- 問題: macOS/BSDとUbuntu/GNUで挙動が違い、現在macOSで`head -n -1`が失敗する。
- 変更:
  - line除去、file size、ISO timestamp、in-place editは標準Python helperへ集約。
  - shellからOS判定して別commandを組むbranchを増やさない。
  - `shell-portability` GitHub Actions matrixをmacOS/Ubuntuで実行する。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-41`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-41`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_portability_helpers.py::test_drop_last_line`
  - `::test_file_size`
  - `::test_iso_timestamp_is_timezone_aware`
  - `::test_atomic_text_replacement`
  - GitHub Actions workflow syntax check。
  - `V-OPS`
- リスクと戻し方: Python不在環境。リポジトリHarness/tests3はPythonを既に前提としていることをCI imageで確認。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-31
- コミット: `RF-41 make test and deploy helpers cross-platform`

### RF-42 Helm検査を本当に実行しrelease前requiredにする

- 対象:
  - `deploy/helm/tests/test_helm_lint.sh:33-49`
  - `deploy/helm/tests/test_template.sh:14-23,27,49,74-78`
  - `.github/workflows/chart-release.yml:19-58`
- 問題: Helm不在・lint失敗をpass扱いし、`set -e`下の`((PASS++))`で最初の成功時に終了し得る。個人kubeconfig参照もある。
- 変更:
  - Helm不在はCIでinvalid/fail、local明示skipはRF-31のskip。
  - lint/template failureをそのままnon-zero。
  - arithmetic incrementを`PASS=$((PASS + 1))`等へ変更。
  - kubeconfigをCI secret/inputから受け、個人pathを削除。
  - release jobはlint/template/schema validation成功を`needs`で必須化。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-42`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-42`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_helm_scripts.py::test_missing_helm_is_not_pass`
  - `::test_lint_failure_propagates`
  - `::test_template_runs_all_cases`
  - `::test_no_personal_kubeconfig_path`
  - fake helmとCI workflow static test。
  - `V-OPS`
- リスクと戻し方: chart release停止。release前にcurrent chartでlint/templateを実行し、既存chart問題を別項目として報告。testを弱めない。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-31, RF-41
- コミット: `RF-42 require real Helm validation before release`

### RF-43 Managed Harnessをmain PRのrequired workflowへ接続

- 対象:
  - `.github/workflows/rung.yml:5-8,17-39,63-75`
  - `.github/workflows/{gates,labeler}.yml:1-末尾`
  - `.harness/target.yaml:6-20,37`
  - `scripts/harness/{outcome-judge,validate-runtime-profile}.sh:1-末尾`
  - `tests3/Makefile:1-末尾`
  - 新規 `.github/workflows/managed-harness-gate.yml:1-末尾`
  - 新規 `tests3/unit/refactor/test_rf_43.py:1-末尾`（workflow security checkerもこのtest内へ固定）
- 問題: 旧`tests3-stateless`検査は`continue-on-error`で最終jobが常に0、main PRにManaged Harness gateがない。
- 変更:
  - 新workflow `managed-harness-gate.yml`でcontext/residency/preflight/hd-gate/adapter validation/tests3 report-gate/outcome judgeを順に実行。
  - required jobは各exit codeを伝播し、artifact/evidenceがない場合fail。
  - triggerは`pull_request`だけで`pull_request_target`を使わない。workflow top-level `permissions: contents: read`、必要な追加scopeは該当jobだけへ明示し、fork/untrusted PRにenvironment secret、repository write token、OIDC `id-token`を渡さない。`actions/checkout`は`persist-credentials:false`、全`uses:`は検証済みfull 40桁commit SHAへpinしtag/branch参照を禁止する。download artifactを実行可能fileやPATHへ置かず、PR codeが書くoutputをshell/evalへ再解釈しない。
  - 旧rungはlegacy fixture専用の手動workflowへ移し、main PR triggerを外す。削除しない。
  - 同じworkflow security checkerを`.github/workflows/rung.yml`, `gates.yml`, `labeler.yml`へ適用する。write permissionまたは`pull_request_target`が業務上必要なworkflowはuntrusted checkout/code executionを同jobで行わず、pin済みactionとmetadata-only入力だけに限定する。満たせなければRF-43を中断しrequired化しない。
  - branch protection自体の変更はrepo外操作なので、必要なrequired check名をdelivery noteへ記載し、管理者設定完了まで「CI接続済み、branch protection未確認」と区別。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-43`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-43`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_workflow_contract.py::test_managed_harness_gate_has_no_continue_on_error`
  - `::test_required_job_depends_on_all_gates`
  - `::test_legacy_rung_does_not_run_on_main_pull_request`
  - `::test_pr_workflows_use_pull_request_read_only_permissions_pinned_actions_and_checkout_without_credentials`
  - `::test_pull_request_target_write_jobs_never_checkout_or_execute_untrusted_code_and_receive_no_oidc_or_environment_secrets`
  - local action syntax/lint。
  - `V-OPS`, `V-HARNESS-CONTRACT`
- リスクと戻し方: 壊れたgateをrequired化すると全PR停止。RF-31〜42後、local full gate greenを証拠化してから実施。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-31, RF-32, RF-33, RF-34, RF-35, RF-36, RF-37, RF-38, RF-39, RF-40, RF-41, RF-42
- コミット: `RF-43 require the managed harness on main pull requests`

### RF-44 Dashboard deployへpretest・path filter・probe・rollbackを追加

- 対象:
  - `.github/workflows/deploy-dashboard-gcp.yml:3-6,32-48`
  - `deploy/gcp/cloudbuild-dashboard.yaml:17-38`
- 問題: mainの全pushで直接build/deployし、事前test、対象path限定、deploy後probe、前revision復帰がない。
- 変更:
  - triggerをDashboard、transcript package、関連deploy configのpathへ限定。
  - deploy前に`V-DASH`相当とtranscript suiteをrequired jobで実行。
  - PR/test jobは`permissions: contents: read`、secret/id-token 0、checkout `persist-credentials:false`。main push後のdeploy jobだけ`id-token: write,contents: read`を持ち、environment approvalとprotected branch条件を必須にする。`actions/checkout`,`google-github-actions/auth`,`setup-gcloud`を含む全`uses:`はfull commit SHAへpinしtag参照を禁止する。untrusted PR code、PR生成artifact、PR-controlled action pathをdeploy/OIDC jobで実行・source・PATH追加しない。
  - imageはdigestでCloud Runへ反映。
  - deploy前revisionを保存し、health/API/static asset probe失敗時は前revisionへtrafficを戻してjob fail。
  - source commit labelをrevisionへ付け、UI evidenceのSHA照合に使う。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-44`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-44`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_dashboard_deploy_workflow.py::test_unrelated_change_does_not_deploy`
  - `::test_deploy_requires_tests`
  - `::test_uses_image_digest`
  - `::test_failed_probe_rolls_back_and_fails_job`
  - `::test_revision_records_source_sha`
  - `::test_deploy_oidc_job_runs_only_after_protected_main_and_environment_approval_with_pinned_actions`
  - `::test_pull_requests_receive_no_gcp_oidc_secret_or_write_token_and_cannot_feed_executable_artifacts_to_deploy`
  - Cloud Build config dry-run/static validation。
  - `V-OPS`, `V-DASH`。
- リスクと戻し方: rollback command自体の誤動作。fake gcloudでargvを固定し、production実行は管理者承認後。workflow commitは共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-43
- コミット: `RF-44 make dashboard deployment verifiable and reversible`

### RF-45A image catalogを単一sourceにする

- 対象:
  - `deploy/compose/Makefile:390-440`
  - `deploy/compose/docker-compose.yml:645-715`
  - `deploy/README.md:53-58`
  - 新規 `deploy/images.yaml:1-末尾`
  - 新規 `tests3/unit/refactor/test_rf_45a.py:1-末尾`
- 問題: publish対象、Compose service、docsのimage一覧が一致せず、voiceprint/calendar等が漏れる。
- 変更:
  - `deploy/images.yaml`へ `id/context/dockerfile/status=shipped|optional|no-ship/publish_name/owners` を定義。
  - publish matrix、inventory check、docs tableをcatalogから生成または検証。
  - `no-ship`は理由必須。既存imageのship statusを推測せず、Composeで使うがpublishしないものは明示`no-ship`としてreview対象にする。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-45A`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-45A`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_image_catalog.py::test_every_compose_build_has_catalog_entry`
  - `::test_every_publish_target_is_shipped`
  - `::test_no_ship_requires_reason`
  - `::test_docs_inventory_matches_catalog`
  - catalog generation後tracked unexpected diff 0。
  - `V-OPS`
- リスクと戻し方: 誤ってimageを公開する危険。既存publish対象だけを`shipped`で初期化し、追加は明示承認まで`no-ship`。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-35
- コミット: `RF-45A define one deploy image catalog`

### RF-45B vexa-client notebook helperをimport-safeなexample moduleへ変更

- 対象:
  - `packages/vexa-client/vexa_client/test_funcs.py:1-67`
  - `packages/vexa-client/tests/admin_tutorial.ipynb:1-末尾` の先頭import cell
  - `packages/vexa-client/pyproject.toml:40-45`
  - 新規 `packages/vexa-client/vexa_client/notebook_helpers.py:1-末尾`
  - 新規 `packages/vexa-client/tests/test_notebook_helpers.py:1-末尾`
- 問題: production package内にpytest収集と誤解される`test_funcs.py`があり、import時に`sys.path`を書換え、標準library名と衝突する`import test`を実行し、localhost URLを直値化する。notebookだけのhelperがpackage importを不安定にする。
- 変更:
  - `test_funcs.py`を`notebook_helpers.py`へrenameする。compatibility re-export fileは残さず、repo内の唯一のconsumerである`admin_tutorial.ipynb`のJSON cell sourceを`from vexa_client.notebook_helpers import ...`へ更新する。
  - `sys.path.append`、`import test`、相対`from vexa import`を削除し、`from vexa_client.vexa import VexaClient`へ固定する。`parse_url`を使っていない場合はimportしない。
  - module import時にenv読込、network、client生成、print/displayを実行しない。`create_user_client(admin_client, user_api_key=None, *, base_url=None)`とし、`base_url`は明示引数→`VEXA_API_URL`→library既定値の順。`ADMIN_API_TOKEN`をmodule globalへ読まない。
  - `get_transcript`は例外を握り潰してprintせず、`poll_count`と`interval_seconds`をkeyword-only引数にし、最終exceptionをcallerへraiseする。Notebook側が必要ならcellで表示用try/exceptを書く。
  - notebookはexample artifactでありpytest対象にしない。testはmodule import副作用0、明示base URL、client注入、poll回数/raise、notebook import cellだけを検証し、実networkへ接続しない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-45B`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-45B`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `packages/vexa-client/tests/test_notebook_helpers.py::{test_import_has_no_path_env_network_or_display_side_effect,test_base_url_precedence_is_explicit,test_polling_raises_last_error,test_admin_notebook_imports_packaged_helper}`
  - `test_import_has_no_path_env_network_or_display_side_effect`が同じintegrations interpreterのsubprocessで`from vexa_client.notebook_helpers import create_user_client, request_bot, get_transcript`を実行しexit 0をassertする。
  - `test_admin_notebook_imports_packaged_helper`が旧`packages/vexa-client/vexa_client/test_funcs.py`不存在と、`sys.path|import test|from test_funcs|localhost:18056`のproduction/notebook source一致0をassertする。これらを未登録direct commandとして再実行しない。
  - `V-CLIENTS`, `V-INTEGRATIONS`。
- リスクと戻し方: 外部利用者が非公開`test_funcs`をimportしている可能性はあるが、package contract/READMEに公開されていない。互換shimで危険importを残さず、必要なら別のdeprecation計画として停止報告する。失敗branchを保持しRF-45AのSHAから再実行する。
- 依存: RF-45A
- コミット: `RF-45B make vexa client notebook helpers import safe`

### RF-46 Harness adapter/runtime schema validationを統一

- 対象:
  - `scripts/harness/validate-runtime-profile.sh:68-90`
  - `schemas/{harness-adapter,harness-agent,harness-environment}.schema.json:1-末尾`
  - read-only input: 3章で事前配置したcanonical `claude-dotfiles@fb9bd5f0d2a20a52d9d48dc56d4080a65c5c3233`の`.claude/hooks/adapter-validate.sh:1-末尾`（計画作成hostのbroken symlinkやcurrent `closed/` fileを代用しない）
  - 新規 `scripts/harness/lib/contracts.py:1-末尾`
  - 新規 `tests3/unit/refactor/test_rf_46.py:1-末尾`
- 問題: tracked runtime validatorはJSON parseしかせず、read-only external hookとschemaで`name`、ID長、kind enumがずれる。ただしexternal hookはこのrepoのコミット対象ではない。
- 変更:
  - 標準Pythonのみの `scripts/harness/lib/contracts.py` にschema loader/validatorを実装する。既存jsonschema dependencyを追加しない。
  - tracked `validate-runtime-profile.sh`は同じvalidator CLIを呼ぶ。schemaをtracked側の唯一のenum/pattern sourceにし、duplicate shell regexを削除。
  - read-only `.claude/hooks/adapter-validate.sh`を有効/無効fixtureへ実行し、tracked validatorとのaccept/reject parityを検査する。parityをtracked側だけで達成できない場合はgeneric_tldvのschemaを無理に緩めず中断し、`claude-dotfiles`側変更を別taskとして報告する。
  - invalid field pathと理由を同じJSON error形式で返す。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-46`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-46`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_harness_contracts.py::test_external_hook_and_tracked_cli_accept_same_valid_fixture`
  - `::test_external_hook_and_tracked_cli_reject_same_invalid_fixtures`
  - `::test_name_id_length_and_kind_match_schema`
  - `::test_invalid_json_is_not_only_validation_performed`
  - `git diff --name-only "$ITEM_BASE_SHA"` に `.claude/` が0件。
  - `V-OPS`, `V-HARNESS-CONTRACT`
- リスクと戻し方: 既存adapterまたはexternal hookがschema不適合。fixture一覧を先に走らせ、不適合は無言でschemaを緩めず、外部symlink targetを編集せず中断。打消しcommitを作らず、共通retry protocolで最後の良好SHAから新branch・新attemptを作って再実行する。
- 依存: RF-01
- コミット: `RF-46 validate harness contracts from one schema`

### RF-47 新規Harness worktreeを `.pipeline` 外へ置く

- 対象:
  - `scripts/harness/worktree.sh:75-84`
  - `scripts/harness/{build,codex-build,full-loop-smoke,delivery-integrity-smoke}.sh:1-末尾`
  - runtime checkout: `.worktrees/<task-id>/**:1-末尾`
  - runtime metadata: `.pipeline/worktrees/<task-id>/worktree.json:1-末尾`
- 問題: checkoutを`.pipeline/worktrees/.../checkout`へ作り、`.pipeline`配下が33万file超となりevidence scanやcontext収集を肥大化させる。
- 変更:
  - 新規worktree rootをrepo内の既存ignored path `.worktrees/<task-id>`へ固定する。repo siblingとの実行時選択を残さない。
  - metadata pathは現行callerとRF-00Aが読む`.pipeline/worktrees/<task-id>/worktree.json`のまま変えず、そこへ実checkout pathを保存する。`.worktrees/<task-id>/worktree.json`や`.pipeline/worktrees/<task-id>/metadata.json`という第二の正本を作らない。
  - 既存worktreeは移動・削除しない。lookupはmetadataにpathがあれば新旧両方を読める。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-47`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-47`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_worktree_paths.py::test_new_checkout_is_outside_pipeline`
  - `::test_metadata_stays_inside_pipeline`
  - `::test_existing_legacy_worktree_is_still_discoverable`
  - `::test_no_existing_worktree_is_moved_or_deleted`
  - temporary Git repo integration test。
  - `V-OPS`, `V-HARNESS-CONTRACT`
- リスクと戻し方: filesystem permission/path長。`mktemp` fixtureで両OS確認。既存worktreeに破壊操作をしない。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-01, RF-46
- コミット: `RF-47 keep harness checkouts out of evidence storage`

### RF-48 Harness共通I/O libraryを導入する

- 対象:
  - read-only input: `scripts/harness/external-consultation.sh:57-90,185-220,850-953`
  - 新規 `scripts/harness/lib/pipeline_io.py:1-末尾`
  - 新規 `tests3/unit/refactor/test_rf_48.py:1-末尾`
- 問題: task path、atomic JSON、hash、event appendのembedded Pythonが複数scriptに重複する。
- 変更:
  - `scripts/harness/lib/pipeline_io.py`に `resolve_task_path`、`read_json`、`atomic_write_json`、`atomic_write_text`、`append_event`、`sha256_file`、`canonical_json_hash` を追加。
  - RF-01のpath containmentを必ず内部で再利用。
  - この項目ではproduction callsiteを移行しない。現行scriptと同じfixture出力をgolden testで固定する。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-48`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-48`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_pipeline_io.py::test_atomic_write_never_leaves_partial_json`
  - `::test_atomic_text_write_never_leaves_partial_file`
  - `::test_event_append_is_valid_jsonl_under_concurrency`
  - `::test_canonical_hash_is_stable`
  - `::test_all_paths_are_task_contained`
  - library dependencyは標準Pythonのみ。
  - `V-OPS`, `V-HARNESS-CONTRACT`
- リスクと戻し方: 未使用libraryのdrift。次項目で必ずconsumer移行し、APIをtest固定。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-01, RF-46
- コミット: `RF-48 add shared harness pipeline I/O primitives`

### RF-49A Harness decision/gate JSONを共通I/Oへ移行

- 対象:
  - `scripts/harness/sml-decision.sh:60-154`
  - `scripts/harness/outcome-judge.sh:22-183`
  - 新規 `tests3/unit/test_harness_cli_compatibility.py:1-末尾`
- 問題: decision/gateのtask path、JSON読書き、error形式がembedded Pythonへ重複する。
- 変更:
  - 2 scriptのpath resolve/read/atomic JSON writeだけをRF-48 `pipeline_io.py`へ移す。判定ロジック、CLI引数、field、exit code、stdout/stderr順は変更しない。
  - S/M/L、invalid size、既存verification contract非上書き、checkpoint ID保持、outcomeのledger/L sidechain/task mismatchをgolden化する。
  - `backcast-state.sh`等、下記後続IDのscriptへ触れない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-49A`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-49A`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `tests3/unit/test_harness_cli_compatibility.py::{test_sml_decision_json_exit_and_output_match_golden,test_outcome_judge_json_exit_and_output_match_golden,test_invalid_size_and_task_mismatch_fail_closed}`
  - 上記exact nodeidは`run-refactor-item.sh RF-49A`のmatrix `pytest` commandだけが実行し、全件pass、unexpected skip/xfail 0をmachine reportで確認する。
  - before/afterのJSON field、exit code、stdout/stderr順がgolden一致。
  - `V-OPS`, `V-HARNESS-CONTRACT`。
- リスクと戻し方: judgeの1 fieldでも変わると既存gateを壊す。bytes差があればcommitせず中断し、最後の良好SHAから再実行する。
- 依存: RF-48
- コミット: `RF-49A migrate harness decision and outcome I/O`

### RF-49B Harness session/state JSONLを共通I/Oへ移行

- 対象:
  - `scripts/harness/codex-session-ledger.sh:55-147,183-256`
  - `scripts/harness/backcast-state.sh:61-136`
  - `scripts/harness/backcast-current.sh:59-208`
  - `tests3/unit/test_harness_cli_compatibility.py:1-末尾`（RF-49A作成物）
- 問題: session/state event appendとcheckpoint JSON更新が別々の非atomic実装を持つ。
- 変更:
  - task path、atomic JSON、JSONL appendだけをRF-48へ移す。transition legality、sequence、run ID、timestamp field、CLI outputは維持。
  - legal/illegal/allow-same、concurrent append、current updateのapproval/manifest前後をgolden比較する。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-49B`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-49B`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `tests3/unit/test_harness_cli_compatibility.py::{test_session_ledger_legal_and_illegal_transitions_match_golden,test_twenty_concurrent_writers_produce_unique_parseable_sequence,test_backcast_current_and_state_match_golden}`
  - 上記exact nodeidは`run-refactor-item.sh RF-49B`のmatrix `pytest` commandだけが実行し、全件passをmachine reportで確認する。
  - 並列20 writer後のJSONLが全行parse可能、sequence重複0。
  - `V-OPS`, `V-HARNESS-CONTRACT`。
- リスクと戻し方: event順やstate transition差。golden差1件でも中断。失敗branchを保持し前SHAから再実行。
- 依存: RF-49A
- コミット: `RF-49B migrate harness session and state I/O`

### RF-49C Harness evidence/approval writerを共通I/Oへ移行

- 対象:
  - `scripts/harness/backcast-evidence-pack.sh:28-197`
  - `scripts/harness/backcast-approval.sh:78-205`
  - `scripts/harness/backcast-next-checkpoint.sh:114-148,185-208`
  - `tests3/unit/test_harness_cli_compatibility.py:1-末尾`（RF-49A作成物）
- 問題: evidence text、approval JSON、next-checkpoint更新がtask containment/atomic writeを各自実装する。
- 変更:
  - RF-48の`atomic_write_text`/`atomic_write_json`/task pathへ移行し、pack内容、approval HEAD binding、state/exit/outputをgolden一致させる。
  - approval済みHEAD不一致、manifest欠落、next checkpoint不正遷移を従来どおりfail closedにする。legacy flagなしmodeの既存state挙動は他task互換としてgolden固定するが、本計画では呼ばない。
  - RF-00Eで既に全releaseへ提供済みのoptional `--immutable-target <approval-target.json> --target-sha256 <sha256>` modeを共通I/Oへ移す。target fileと列挙sourceのhash、task/head/environmentを再計算し、人間が渡したhashとexact一致した場合だけ`.pipeline/approvals/$TASK/approval-decision.json`へ`reviewed_evidence.target_path,target_sha256,manifest_sha256,pack_sha256,closure_attempt_id`を保存する既存挙動をgolden byte一致させる。新しいflag/fieldをこの項目で初導入しない。immutable modeは既存`evidence-manifest.json`、`evidence-pack.md`、approval target、列挙source、checkpoint stateを変更せず、state transitionを呼ばない。既存decisionは`--verify-existing`だけを許す。flagなしの既存CLI/bytesは他task互換のため維持する。本計画の9.7はimmutable modeだけを許す。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-49C`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-49C`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `tests3/unit/test_harness_cli_compatibility.py::{test_evidence_pack_text_matches_golden,test_approval_head_binding_and_next_checkpoint_match_golden,test_immutable_approval_binds_manifest_pack_and_target_hash_without_rewriting_reviewed_files,test_interrupted_writes_leave_no_partial_artifact}`
  - 上記exact nodeidは`run-refactor-item.sh RF-49C`のmatrix `pytest` commandだけが実行し、全件passをmachine reportで確認する。
  - interrupted write fixtureでpartial JSON/text 0。
  - `V-OPS`, `V-HARNESS-CONTRACT`。
- リスクと戻し方: approval hashの意味変更はPR gateを無効化する。exact bytes/hash golden差で中断し、前SHAから再実行。
- 依存: RF-49B
- コミット: `RF-49C migrate harness evidence and approval I/O`

### RF-49D Harness worktree/build metadata I/Oを共通化

- 対象:
  - `scripts/harness/worktree.sh:66-224`
  - `scripts/harness/build.sh:170-207`
  - `tests3/unit/test_harness_cli_compatibility.py:1-末尾`（RF-49A作成物）
- 問題: operational metadataのpath/JSON writeだけが共通containment/atomicityを迂回する。
- 変更:
  - worktree metadataとbuild summaryのpath resolve/read/writeだけをRF-48へ移す。git worktree操作、build command、auto-commit、state transition、manifest/pack起動は変更しない。
  - legacy/new worktree discoveryと成功/失敗build summaryをgolden比較する。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-49D`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-49D`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `tests3/unit/test_harness_cli_compatibility.py::{test_legacy_and_new_worktree_discovery_match_golden,test_success_and_failure_build_summaries_match_golden}`
  - 上記exact nodeidは`run-refactor-item.sh RF-49D`のmatrix `pytest` commandだけが実行し、全件passをmachine reportで確認する。
  - `V-OPS`, `V-HARNESS-CONTRACT`。
- リスクと戻し方: git操作まで巻き込む危険。diffにmetadata I/O以外のworktree/build control flow差があれば中断。
- 依存: RF-47, RF-49C
- コミット: `RF-49D migrate harness operational metadata I/O`

### RF-49E Review差分のimmutable base SHAを固定

- 対象:
  - `scripts/harness/build.sh:170-207`
  - 新規 `tests3/unit/test_harness_review_base.py:1-末尾`
- 問題: tracked/external review scriptsとread-only validatorはbuild summaryのlegacy `head_sha`をreview baseとして読む。build summaryを最終HEADで再生成するとこれが上書きされ、post reviewの差分が空になる。
- 変更:
  - 互換field `head_sha`をimmutable review baseとして残す。旧summaryがあれば旧`head_sha`、なければ現在HEADを初回値にし、以後上書きしない。
  - 新field `implementation_head_sha`へbuild実行時の現在HEADを書く。補助field `base_sha`もimmutable `head_sha`と同じ値にするが、外部symlink validatorを変更しない。
  - tracked external/Codex/dual review scriptsも現状どおりlegacy `head_sha`を読むため、callsite変更は不要。read-only external validatorとの互換testを実行するだけで、`.claude/**`を編集しない。
  - `head_sha`が`implementation_head_sha`のancestorでない、またはpost modeで差分空ならtracked review script側がfailする契約testを追加する。
  - RF-00Aのbaseline build summaryをmigration fixtureに使う。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-49E`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-49E`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `tests3/unit/test_harness_review_base.py::{test_three_builds_preserve_immutable_review_base,test_implementation_head_only_tracks_current_head,test_post_review_rejects_empty_nonancestor_and_unknown_base,test_read_only_external_validators_accept_compatible_summary}`
  - 上記exact nodeidは`run-refactor-item.sh RF-49E`のmatrix `pytest` commandだけが実行し、全件passをmachine reportで確認する。
  - 3回build summaryを更新してもlegacy `head_sha`と`base_sha`はRF-00A SHAのまま、`implementation_head_sha`だけ現在HEADへ更新。
  - post review fixtureのdiffがRF-00A後の実装commitを1件以上含み、空diff/非ancestor/未知SHAをreject。
  - read-only `.claude/hooks/{external-consultation,codex-review,dual-review}-validate.sh`がfixtureをpassし、`git diff --name-only "$ITEM_BASE_SHA"` に`.claude/` 0件。
  - `V-OPS`, `V-HARNESS-CONTRACT`。
- リスクと戻し方: legacy field名と意味の互換を壊す危険。external hook変更が必要になった時点で中断し、canonical `claude-dotfiles`の別taskへ切り出す。前SHAから再実行。
- 依存: RF-49D
- コミット: `RF-49E preserve an immutable review base SHA`

### RF-50 External consultationを薄いCLIへ段階移行

- 対象:
  - `scripts/harness/external-consultation.sh:1-953`
  - 新規 `scripts/harness/lib/external_consultation.py:1-末尾`
  - 新規 `tests3/unit/refactor/test_rf_50.py:1-末尾`
- 問題: 953行のshell + embedded Pythonにpath、JSON、hash、event、provider呼出し、fallbackが混在する。
- 変更:
  - `scripts/harness/lib/external_consultation.py`へpure plan validation、request manifest、response parsing、evidence writingを移す。
  - shellはargument/env validation、provider process起動、Python CLI呼出しだけにする。
  - provider command、timeout、max call、fallback、artifact path、event順、hash fieldを変更しない。
  - 一括書換えせず、既存golden fixtureの各mode `plan|review`、success/failure/max-call fallbackを比較してからembedded blockを削除。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-50`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-50`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_external_consultation.py::test_plan_mode_matches_legacy_golden`
  - `::test_review_mode_matches_legacy_golden`
  - `::test_provider_failure_records_advisory_failure`
  - `::test_max_call_fallback_is_preserved`
  - `::test_evidence_hash_binds_exact_request_and_response`
  - shell fileがargument/exec orchestrationだけになり、embedded Python 0。
  - `V-OPS`, `V-HARNESS-CONTRACT`
- リスクと戻し方: L-task gateを壊す。legacy goldenを同一fixtureで比較し、差があれば削除段階へ進まない。commit 共通retry protocol。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-49E
- コミット: `RF-50 extract external consultation orchestration`

### RF-51 Docs ownership検査でREADME実在を必須化

- 対象:
  - `tests3/docs/check.py:119-178`
  - `tests3/docs/manifest.json:19-23,44-48`
  - `docs/README.md:25,30`
  - 新規 `tests3/unit/refactor/test_rf_51.py:1-末尾`
- 問題: ownership manifestが存在しないREADMEを指しても4 checks passになる。
- 変更:
  - `README_EXISTS` checkを追加し、各ownership targetはregular file、repo内、case-sensitive一致を必須とする。
  - ghost owner `transcription-collector`を削除し、`api/transcripts`, `api/meetings`, `api/recordings`を既存`meeting-api` ownerへ明示remapする。
  - ghost owner `shared-models`を削除し、webhook契約を`meeting-api`へremapする。token scopingは`api-gateway`のまま変更しない。
  - `docs/README.md`の同じowner表をmanifestと同期する。存在しないREADMEを新規作成する選択肢は取らない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-51`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-51`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `tests3/unit/refactor/test_rf_51.py::{test_missing_owned_readme_fails,test_path_outside_repo_fails,test_current_manifest_targets_exist,test_docs_check_subprocess_reports_readme_exists_pass}`
  - `V-OPS`
- リスクと戻し方: ownershipを誤配分する危険。現在のproduction route/importとservice責務へ上記mappingを固定し、実行時に別ownerを選ばない。失敗時は前SHAから再実行。
- 依存: RF-35
- コミット: `RF-51 verify owned documentation exists`

### フェーズ3ゲート

- active test missing、report 0、feature policy不明、全skip、0 step、health timeout、Helm不在/失敗の各fixtureが全てnon-zero。
- `make -C tests3 validate-all` が最低1 reportを読み、report-gateまで実行する。
- macOS/Ubuntuのportability CIがgreen。
- Managed Harness local gateとmain PR workflowがgreen。branch protectionのrepo外設定は確認済み/未確認を区別。
- Dashboard deploy dry-runはunrelated changeでdeployせず、probe failureでrollback commandを発行してjob fail。
- `V-OPS`、`V-HARNESS-CONTRACT`がpass。

```bash
set -Eeuo pipefail
test -n "${MASTER_TASK:-}"
test -n "${RELEASE_ID:-}"
test "${TASK:-}" = "${MASTER_TASK}-${RELEASE_ID}"
PHASE_ITEMS='RF-31,RF-32,RF-33,RF-34,RF-35,RF-36,RF-37,RF-38,RF-39,RF-40,RF-41,RF-42,RF-43,RF-44,RF-45A,RF-45B,RF-46,RF-47,RF-48,RF-49A,RF-49B,RF-49C,RF-49D,RF-49E,RF-50,RF-51'
bash scripts/test/run-refactor-phase-stage.sh \
  --phase phase-3 --expect-items "$PHASE_ITEMS"
```

exit 0、`.pipeline/evidence/$TASK/phase-gates/phase-3.json`が`status=passed`、item ID byte一致、command/assertion 1件以上、`head_sha`=RF-51 commitであること。

## 7. フェーズ4: Backendの契約維持型リファクタリング

このフェーズでは、RF-01〜51で修正済みの明示的bug以外の外部挙動を変えない。endpoint path、status code、request/response JSON、Redis key、DB metadata、transaction境界、provider prompt/model/retry/chunk幅をgoldenへ固定したまま、責務を移動する。

### RF-52 Meeting lifecycleの型と分類を依存なしmoduleへ移す

- 対象:
  - `services/meeting-api/meeting_api/callbacks.py:29-36,115-124,364-396,887-912`
  - `services/meeting-api/meeting_api/collector/endpoints.py:1-末尾`
  - `services/meeting-api/meeting_api/final_transcription.py:1-末尾`
  - `services/meeting-api/meeting_api/meetings.py:1-末尾`
  - `services/meeting-api/meeting_api/outbound_events.py:1-末尾`
  - `services/meeting-api/meeting_api/schemas.py:1-末尾`
  - `services/meeting-api/meeting_api/sweeps.py:1-末尾`
  - `services/meeting-api/meeting_api/voice_agent.py:1-末尾`
  - read-only inventory: `git grep -n -E -e 'MeetingStatus|CompletionReason|FailureReason|is_terminal' -- services/meeting-api/meeting_api`。期待pathは上記8既存fileだけで、別pathが1件でもあれば停止してplan reviewへ戻る
  - 新規 `services/meeting-api/meeting_api/lifecycle/__init__.py:1-末尾`
  - 新規 `services/meeting-api/meeting_api/lifecycle/types.py:1-末尾`
  - 新規 `services/meeting-api/meeting_api/lifecycle/classification.py:1-末尾`
  - 新規 `services/meeting-api/tests/refactor/test_rf_52.py:1-末尾`
- 問題: status enum、失敗理由、終端分類がcallbacks/schemasへ分散し、循環依存の根になる。
- 変更:
  - `meeting_api/lifecycle/types.py`へ `MeetingStatus`、`MeetingCompletionReason`、`MeetingFailureStage`、`TerminalSignal`、`TerminalDecision`を移す。
  - `lifecycle/classification.py`へRF-11のpure classifierを移す。
  - 旧`meeting_api.schemas`と`callbacks`から同名をre-exportし、外部importを壊さない。
  - 新moduleはstdlib/enum/dataclassだけに依存し、DB/FastAPI/routerをimportしない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-52`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-52`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_lifecycle_imports.py::test_types_and_classification_have_no_service_dependencies`
  - `::test_legacy_imports_are_identity_compatible`
  - `test_lifecycle_characterization.py::test_terminal_matrix_snapshot`
  - `V-MEETING`
- リスクと戻し方: enum class identityが二重になる危険。定義は新module1か所、旧moduleはalias re-exportのみ。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-11
- コミット: `RF-52 extract dependency-free meeting lifecycle types`

### RF-53 Meeting status transitionを単一serviceへ移す

- 対象:
  - `services/meeting-api/meeting_api/meetings.py:1-末尾` のstatus update
  - `services/meeting-api/meeting_api/callbacks.py:1-末尾`
  - `services/meeting-api/meeting_api/sweeps.py:1-末尾`
  - `services/meeting-api/meeting_api/post_meeting.py:1-末尾`
  - `services/meeting-api/meeting_api/recording_finalizer.py:1-末尾`
  - 新規 `services/meeting-api/meeting_api/lifecycle/transitions.py:1-末尾`
  - 新規 `services/meeting-api/tests/refactor/test_rf_53.py:1-末尾`
- 問題: status update、terminal guard、timestamp、publish条件が複数moduleに分散する。
- 変更:
  - `meeting_api/lifecycle/transitions.py` に `MeetingTransitionService.transition(meeting_id, expected_from, decision, session)` を作る。
  - 既存transaction/sessionを引数で受け、service自身が新transactionを開始しない。
  - idempotent terminal再入、timestamp、completion reason、failure stageの更新規則をRF-00B goldenどおり実装。
  - callbacks/sweeps等は旧function wrapperを経て新serviceを呼ぶ。call orderは維持。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-53`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-53`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_transitions.py::test_transition_matrix_matches_characterization`
  - `::test_duplicate_terminal_transition_is_noop`
  - `::test_uses_callers_existing_transaction`
  - `::test_publish_is_not_inside_transition_service`
  - `V-MEETING`
- リスクと戻し方: transaction境界の暗黙変更。commit/rollbackをservice内で呼んだらtest failure。wrapperを残すため共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-52
- コミット: `RF-53 centralize meeting status transitions`

### RF-54 終端副作用をTerminalMeetingServiceへ集約

- 対象:
  - `services/meeting-api/meeting_api/callbacks.py:1-末尾`
  - `services/meeting-api/meeting_api/sweeps.py:1-末尾`
  - `services/meeting-api/meeting_api/post_meeting.py:1-末尾`
  - `services/meeting-api/meeting_api/recording_finalizer.py:1-末尾`
  - `services/meeting-api/meeting_api/final_transcription.py:992,1059`
  - 新規 `services/meeting-api/meeting_api/lifecycle/terminal_service.py:1-末尾`
  - 新規 `services/meeting-api/tests/refactor/test_rf_54.py:1-末尾`
- 問題: recording finalize、transition、publish、post-meeting enqueueがcallback/sweepごとに組み替えられ、重複や意味差を生む。
- 変更:

```python
class TerminalMeetingService:
    async def terminalize(
        self,
        meeting_id: int,
        signal: TerminalSignal,
        session: AsyncSession,
    ) -> TerminalResult:
        # lock -> classify -> recording finalize -> transition
        # -> commitは既存callerの契約に従う
        # -> publish -> post-meeting enqueue
```

依存はconstructor ports `RecordingFinalizer`, `TransitionService`, `Publisher`, `PostMeetingEnqueuer`として注入する。各callback/sweepはsignal変換だけ行い同serviceを呼ぶ。副作用順序、回数、error isolationはRF-00B goldenに一致させる。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-54`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-54`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_terminal_service.py::test_side_effect_order_matches_characterization`
  - `::test_duplicate_callbacks_finalize_and_enqueue_once`
  - `::test_exit_status_and_sweep_signals_reach_same_terminal_result`
  - `test_post_meeting_idempotency.py::test_terminal_signals_enqueue_post_meeting_once`
  - `test_sweeps_stopping.py::test_sweep_terminal_signal_uses_terminal_service_once`
  - `V-MEETING`
- リスクと戻し方: CRITICAL範囲。GitNexus impactを必ず保存し、callsite漏れが1件あれば中断。旧wrappersを残し、commit 共通retry protocol。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-53
- コミット: `RF-54 centralize terminal meeting orchestration`

### RF-55 Meeting lifecycleの循環importを除去

- 対象:
  - `services/meeting-api/meeting_api/main.py:29-33,332`
  - `services/meeting-api/meeting_api/meetings.py:53,333,1683,1715,1949,1991,2044,2396`
  - `services/meeting-api/meeting_api/callbacks.py:29-36`
  - `services/meeting-api/meeting_api/post_meeting.py:17`
  - `services/meeting-api/meeting_api/final_transcription.py:992,1059`
  - `services/meeting-api/meeting_api/sweeps.py:99,159,243,322,520,694`
  - `services/meeting-api/meeting_api/recording_finalizer.py:585`
  - `services/meeting-api/meeting_api/voice_agent.py:18`
  - `services/meeting-api/meeting_api/voiceprints.py:41`
  - 新規 `services/meeting-api/tests/refactor/test_rf_55.py:1-末尾`
- 問題: 9-module cycleを関数内importで回避し、import時の構造と実行時依存が一致しない。
- 変更:
  - RF-52〜54のtypes/service/portsを依存方向の中心にする。
  - router/mainがcomposition rootでconcrete portsを組み立てる。
  - production codeの関数内importを列挙し、循環回避目的のものだけ通常import/DIへ置換する。
  - optional dependency/lazy heavy SDKのimportは理由commentとtestを残し、この項目で無理に移さない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-55`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-55`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_import_graph.py::test_meeting_api_production_import_graph_is_acyclic`
  - `::test_all_meeting_modules_import_in_clean_process`
  - RF-00B lifecycle/finalization golden差0。
  - `V-MEETING`
- リスクと戻し方: startup時にoptional SDKを要求する可能性。clean process testはproduction dependency setとminimal test setの両方で実行。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-52, RF-53, RF-54
- コミット: `RF-55 remove meeting lifecycle import cycles`

### RF-56 `request_bot`からpure builderを抽出

- 対象:
  - `services/meeting-api/meeting_api/meetings.py:722-1278`
  - 新規 `services/meeting-api/meeting_api/bot_request/__init__.py:1-末尾`
  - 新規 `services/meeting-api/meeting_api/bot_request/builders.py:1-末尾`
  - 新規 `services/meeting-api/tests/refactor/test_rf_56.py:1-末尾`
- 問題: 557行にmode判定、URL、重複制限、DB作成、token、timeout、config/env、dry-run、spawn、Redis、schedulerが混在する。
- 変更:
  - 副作用を持たない次の関数を `meeting_api/bot_request/builders.py` へ抽出する。
    - `resolve_meeting_identity`
    - `resolve_bot_timeouts`
    - `build_meeting_data`
    - `build_bot_config`
    - `build_runtime_spec`
  - 入力/戻り値はfrozen dataclass/Pydantic modelにし、global env読取はcallerで値を渡す。
  - field名、default、env文字列、secret redaction、JSON順非依存の内容をRF-00B goldenと一致させる。
  - 元functionは新builderを呼ぶが、副作用順序はまだ変えない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-56`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-56`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_bot_request_builders.py::test_standard_runtime_spec_golden`
  - `::test_browser_runtime_spec_golden`
  - `::test_agent_only_runtime_spec_golden`
  - `::test_builders_do_not_access_db_redis_or_environment`
  - RF-00B request golden差0。
  - `V-MEETING`
- リスクと戻し方: default評価時点の変化。env/clock/UUIDを明示引数にし、golden差で中断。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-10, RF-55
- コミット: `RF-56 extract pure bot request builders`

### RF-57 `request_bot`をmode strategyとcoordinatorへ分ける

- 対象:
  - `services/meeting-api/meeting_api/meetings.py:722-1278`
  - RF-56の `services/meeting-api/meeting_api/bot_request/{__init__,builders}.py:1-末尾`
  - 新規 `services/meeting-api/meeting_api/bot_request/strategies.py:1-末尾`
  - 新規 `services/meeting-api/meeting_api/bot_request/coordinator.py:1-末尾`
  - 新規 `services/meeting-api/tests/refactor/test_rf_57.py:1-末尾`
- 問題: standard、browser、agent-onlyの分岐と共有副作用が一関数に残る。
- 変更:
  - `StandardBotStrategy`、`BrowserSessionStrategy`、`AgentOnlyStrategy`を作り、mode固有validate/specだけ担当。
  - `BotRequestCoordinator`は順序を `validate -> duplicate/limit check -> Meeting create -> token/config -> dry-run or runtime spawn -> Redis/scheduler -> response` に固定。
  - DB commit回数、spawn失敗時のMeeting行/status、Redis key、scheduler payloadを変更しない。
  - endpoint `request_bot`はrequest parse、strategy選択、coordinator call、responseだけにする。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-57`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-57`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_bot_request_coordinator.py::test_standard_browser_agent_side_effect_order`
  - `::test_runtime_spawn_failure_preserves_existing_failed_meeting_behavior`
  - `::test_dry_run_has_no_runtime_redis_or_scheduler_side_effect`
  - `::test_each_mode_selects_exactly_one_strategy`
  - `request_bot`本体は150行以下、mode固有config生成0。
  - `V-MEETING`
- リスクと戻し方: partial failure仕様が変わる。RF-00B call-order/golden差で中断。strategyを旧functionへ戻せる1commit。
- 依存: RF-56
- コミット: `RF-57 split bot request strategies from orchestration`

### RF-58 Deferred transcriptionの外部依存をportsへ包む

- 対象:
  - `services/meeting-api/meeting_api/final_transcription.py:1131-1590`
  - 新規 `services/meeting-api/meeting_api/transcription_flow/__init__.py:1-末尾`
  - 新規 `services/meeting-api/meeting_api/transcription_flow/ports.py:1-末尾`
  - 新規 `services/meeting-api/meeting_api/transcription_flow/adapters.py:1-末尾`
  - 新規 `services/meeting-api/tests/refactor/test_rf_58.py:1-末尾`
- 問題: lease、録画、provider HTTP、DB、cache、Pub/Sub、Drive、voiceprintが460行の一関数に直結する。GitNexus impactはCRITICAL。
- 変更:
  - 次のprotocolと現行実装adapterを追加し、元functionのcallsiteは変えず内部呼出しだけ置換する。
    - `FinalTranscriptionLease`
    - `RecordingSourceResolver`
    - `TranscriptionProviderClient`
    - `TranscriptPersistence`
    - `TranscriptPublisher`
    - `PostCommitHook`
  - portは既存key/HTTP payload/DB modelを変換せず包む。
  - Drive/voiceprintは`PostCommitHook`だが、現行のfailure isolationを個別に維持。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-58`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-58`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_final_transcription_ports.py::test_each_external_dependency_is_called_through_one_port`
  - `::test_adapter_payloads_match_characterization`
  - `::test_lease_key_and_ttl_are_unchanged`
  - RF-00B call-order/golden差0。
  - `V-MEETING`
- リスクと戻し方: CRITICAL blast radius。port追加以外の順序変更禁止。GitNexus direct caller全件をevidenceへ列挙。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-55
- コミット: `RF-58 wrap deferred transcription dependencies in ports`

### RF-59 Deferred transcriptionをcoordinatorとpost-commit hooksへ分ける

- 対象:
  - `services/meeting-api/meeting_api/final_transcription.py:1131-1590`
  - RF-58の `services/meeting-api/meeting_api/transcription_flow/{__init__,ports,adapters}.py:1-末尾`
  - 新規 `services/meeting-api/meeting_api/transcription_flow/coordinator.py:1-末尾`
  - 新規 `services/meeting-api/tests/refactor/test_rf_59.py:1-末尾`
- 問題: 正常系とcleanup/error handlingが同じ関数に絡み、DB確定前後の副作用境界が読めない。
- 変更:
  - `FinalTranscriptionCoordinator.run()`へ次の状態機械を実装する。

```text
lease取得
 -> source解決
 -> provider実行
 -> DB transaction内でsegment確定
 -> transaction成功後cache/publish
 -> voiceprint/Drive hooks
 -> lease解放
```

  - replace modeの旧segment削除と新segment insertは同一transaction。
  - lease喪失後はpersistenceしない。
  - cache/publish失敗とDrive/voiceprint失敗の扱いはRF-00B観測どおり分離。
  - 旧`run_deferred_transcription`署名は薄いcompatibility wrapperとして残す。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-59`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-59`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_final_transcription_coordinator.py::test_lease_loss_prevents_persistence`
  - `::test_replace_mode_rollback_preserves_old_rows`
  - `::test_commit_precedes_cache_publish_and_hooks`
  - `::test_post_commit_hook_failure_does_not_revert_transcript`
  - `::test_legacy_entrypoint_signature_is_preserved`
  - `V-MEETING`
- リスクと戻し方: 最重要データ経路。segment text/speaker/timestamp/count、DB transaction、Redis eventに1差でもあれば共通retry protocolし中断。commit 共通retry protocol。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-58
- コミット: `RF-59 extract deferred transcription coordinator`

### RF-60 Gemini境界処理のimmutable型とpure alignmentを抽出

- 対象:
  - `services/transcription-service/gemini_adapter.py:1775-2613`
  - 新規 `services/transcription-service/gemini_boundary/__init__.py:1-末尾`
  - 新規 `services/transcription-service/gemini_boundary/types.py:1-末尾`
  - 新規 `services/transcription-service/gemini_boundary/alignment.py:1-末尾`
  - 新規 `services/transcription-service/tests/refactor/test_rf_60.py:1-末尾`
- 問題: boundary token、overlap、speaker対応、fallback、logがmutable dict/listへ混在する。
- 変更:
  - `gemini_boundary/types.py`へfrozen `BoundaryUnit`、`OverlapEdge`、`ConsumptionPlan`。
  - `gemini_boundary/alignment.py`へtokenization、normalized comparison、candidate scoring、speaker-compatible alignmentのpure helper。
  - provider call/prompt/logは元fileに残し、新型へ変換して既存plannerを呼ぶ。
  - normalization、timestamp rounding、Unicode処理をRF-00B goldenどおり維持。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-60`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-60`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_gemini_boundary_types.py::test_models_are_immutable`
  - `test_gemini_alignment.py::test_ascii_japanese_emoji_and_speaker_cases`
  - `::test_alignment_is_deterministic`
  - `::test_alignment_does_not_mutate_inputs`
  - RF-00B Gemini golden差0。
  - `V-TRANSCRIPTION`
- リスクと戻し方: Unicode normalization差。入力/出力byte相当fixtureで差0を必須。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-00B
- コミット: `RF-60 extract immutable Gemini boundary primitives`

### RF-61 exact-boundary plannerを専用moduleへ移す

- 対象:
  - `services/transcription-service/gemini_adapter.py:1775-2613` の `_plan_exact_boundary_stream_consumption` とhelper
  - RF-60の `services/transcription-service/gemini_boundary/{__init__,types,alignment}.py:1-末尾`
  - 新規 `services/transcription-service/gemini_boundary/planner.py:1-末尾`
  - 新規 `services/transcription-service/tests/refactor/test_rf_61.py:1-末尾`
- 問題: 839行のplannerがadapter内部状態・logging・fallbackと結合する。GitNexus impactはMEDIUMで85 symbol。
- 変更:
  - `gemini_boundary/planner.py`へpure `plan_exact_boundary_stream_consumption(inputs, policy) -> ConsumptionPlan` を移す。
  - clock/log/provider configは引数のimmutable policyへ変換。
  - 旧関数名は同じsignatureのwrapperとして残し、全callsiteを一度に変えない。
  - fallback branchとreason codeを全てgolden fixtureへ列挙する。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-61`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-61`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_gemini_boundary_planner.py::test_boundary_plan_golden_matrix`
  - `::test_every_fallback_reason_has_fixture`
  - `::test_plan_is_deterministic`
  - `::test_legacy_wrapper_matches_new_planner`
  - `V-TRANSCRIPTION`
- リスクと戻し方: 85 symbol影響。wrapper identity/golden差0を確認。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-60
- コミット: `RF-61 extract the exact-boundary planner`

### RF-62 Gemini chunk mergeを専用moduleへ移す

- 対象:
  - `services/transcription-service/gemini_adapter.py:2616-3228`
  - RF-60/RF-61の `services/transcription-service/gemini_boundary/{__init__,types,alignment,planner}.py:1-末尾`
  - 新規 `services/transcription-service/gemini_boundary/merge.py:1-末尾`
  - 新規 `services/transcription-service/tests/refactor/test_rf_62.py:1-末尾`
- 問題: 613行のmergeがtimestamp、speaker、text overlap、fallback、loggingを一体化する。
- 変更:
  - `gemini_boundary/merge.py`へ `merge_chunk_segments(chunks, plan, policy)` を移す。
  - output segment modelは既存型を使用し、text、speaker、start/end、順序を変えない。
  - 旧functionはwrapperとして残す。
  - invariant testとしてtimestamps nondecreasing、境界text非重複、同入力idempotentを追加。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-62`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-62`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_gemini_merge.py::test_merge_golden_matrix`
  - `::test_timestamps_are_monotonic`
  - `::test_boundary_text_is_not_duplicated`
  - `::test_merge_is_idempotent`
  - `::test_legacy_wrapper_matches_new_merge`
  - `V-TRANSCRIPTION`
- リスクと戻し方: silent transcript corruption。golden 1件でも差があれば中断。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-61
- コミット: `RF-62 extract Gemini chunk merging`

### RF-63 Transcription HTTP endpointを4責務へ分ける

- 対象:
  - `services/transcription-service/main.py:274-599`
  - 新規 `services/transcription-service/transcription_http/__init__.py:1-末尾`
  - 新規 `services/transcription-service/transcription_http/request_validation.py:1-末尾`
  - 新規 `services/transcription-service/transcription_http/audio_preparation.py:1-末尾`
  - 新規 `services/transcription-service/transcription_http/provider_dispatch.py:1-末尾`
  - 新規 `services/transcription-service/transcription_http/response_mapping.py:1-末尾`
  - 新規 `services/transcription-service/tests/refactor/test_rf_63.py:1-末尾`
- 問題: request validation、audio decode/convert、semaphore、provider選択、response mappingが1endpointに混在。
- 変更:
  - `request_validation.py`: multipart/parameter validationと既存HTTP error mapping。
  - `audio_preparation.py`: decode/format conversion、temp resource lifecycle。
  - `provider_dispatch.py`: semaphoreとprovider adapter選択。
  - `response_mapping.py`: OpenAI互換responseへ変換。
  - endpointは4段階を呼ぶだけにし、multipart field、status code、error body、model名、retryを維持。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-63`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-63`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_transcription_endpoint_contract.py::test_request_response_and_error_golden`
  - `::test_temp_resources_are_cleaned_on_every_failure_stage`
  - `::test_semaphore_wraps_provider_call_only`
  - `::test_provider_selection_matches_baseline`
  - endpoint本体150行以下。
  - `V-TRANSCRIPTION`
- リスクと戻し方: exception mappingとcleanupの変化。failure stage全fixtureでHTTP status/bodyを比較。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-60, RF-61, RF-62
- コミット: `RF-63 separate transcription endpoint responsibilities`

### RF-64 API Gatewayをpolicy・HTTP proxy・SSE・WS routerへ分ける

- 対象:
  - `services/api-gateway/main.py:1-2667`
  - 新規 `services/api-gateway/gateway/__init__.py:1-末尾`
  - 新規 `services/api-gateway/gateway/policy.py:1-末尾`
  - 新規 `services/api-gateway/gateway/http_proxy.py:1-末尾`
  - 新規 `services/api-gateway/gateway/sse_proxy.py:1-末尾`
  - 新規 `services/api-gateway/gateway/ws_proxy.py:1-末尾`
  - 新規 `services/api-gateway/gateway/routers/__init__.py:1-末尾`
  - 新規 `services/api-gateway/gateway/routers/{admin,meeting,calendar,agent,recordings,browser}.py:1-末尾`
  - 新規 `tests3/unit/refactor/test_rf_64.py:1-末尾`
- 問題: 認証、route policy、HTTP proxy、SSE、WS、shared URLが単一moduleで、動的routeのimpactがgraphに出にくい。
- 変更:
  - `gateway/policy.py`: RF-05Aのroute policy/identity。
  - `gateway/http_proxy.py`: header sanitization、timeout、HTTP streaming。
  - `gateway/sse_proxy.py`: Agent SSE。
  - `gateway/ws_proxy.py`: WebSocket。
  - `gateway/routers/*.py`: Admin/Meeting/Calendar/Agent/recordings/browser route登録。
  - `main.py`はapp/lifespan/router includeのみ。
  - dynamic path、methods、status、headers、timeout、body streamingをroute inventory goldenと一致させる。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-64`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-64`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_gateway_route_inventory.py::test_method_path_policy_upstream_golden`
  - `::test_no_authenticated_route_bypasses_policy`
  - `test_gateway_imports.py::test_main_is_composition_only`
  - `test_proxy_contract.py::{test_http_proxy_preserves_method_path_query_headers_body_and_status,test_sse_proxy_preserves_stream_events_and_disconnect_cleanup,test_ws_proxy_preserves_bidirectional_frames_close_code_and_auth_policy}`
  - `main.py` 400行以下。
  - `V-BACKEND`
- リスクと戻し方: dynamic route漏れ。OpenAPI + manual WS/SSE inventoryをbefore/after比較し、差1件で中断。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-05A, RF-05B, RF-05C, RF-05D2, RF-05E, RF-09B
- コミット: `RF-64 split gateway policy and transport routers`

### RF-65 Meeting ORM modelをshared packageへmoveしre-exportする

- 対象:
  - `services/meeting-api/meeting_api/models.py:1-末尾`
  - 新規 `libs/meeting-models/pyproject.toml:1-末尾`
  - 新規 `libs/meeting-models/meeting_models/{__init__,models}.py:1-末尾`
  - 新規 `services/meeting-api/tests/fixtures/meeting-model-metadata.json:1-末尾`
  - `libs/admin-models/admin_models/{__init__,models}.py:1-末尾`
  - `services/admin-api/app/main.py:1-末尾`
  - `services/calendar-service/app/{main,models,sync}.py:1-末尾`
  - `services/{meeting-api,admin-api,calendar-service}/Dockerfile:1-末尾`
  - `deploy/lite/Dockerfile.lite:1-末尾`
- 問題: model/Base/databaseが重複し、サービス分離と実依存が一致しない。先に移すとDB metadata driftが危険。
- 変更:
  - 現在のtable、column、type、nullable、default、FK、constraint、index、naming conventionをsorted JSON snapshotへ固定。
  - `libs/meeting-models/meeting_models/models.py`へ定義をmove。
  - `meeting_api.models`は同一class objectをre-exportする。duplicate declarative definitionを残さない。
  - `libs/meeting-models/pyproject.toml`を同じcommitで作り、現時点で`meeting_api.models`を利用するMeeting/Admin/Calendar/Lite imageへCOPY/installする。次項目を待たずRF-65単体でclean build可能にする。
  - Alembic/schema migrationは作らない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-65`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-65`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_meeting_model_metadata.py::test_metadata_snapshot_is_unchanged`
  - `::test_legacy_imports_reference_same_classes_and_base`
  - `::test_no_duplicate_table_definitions`
  - `::test_schema_sync_is_noop_for_current_schema`
  - Meeting/Admin/Calendar/Liteをclean buildし、各containerでlegacy/new importが同一class identityを返す。
  - `V-MEETING`, `V-BACKEND`
- リスクと戻し方: DB metadata driftは重大。snapshot差が1fieldでもあれば中断。migrationを追加しない。re-exportがあるため共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-55, RF-59, RF-63
- コミット: `RF-65 move meeting ORM models behind compatibility exports`

### RF-66A DB infrastructureをshared model packageへ移す

- 対象:
  - `services/meeting-api/meeting_api/database.py:1-末尾`
  - `libs/admin-models/admin_models/database.py:1-末尾`
  - `services/calendar-service/app/main.py:12-14`
  - 新規 `libs/meeting-models/meeting_models/database.py:1-末尾`
- 問題: pool/URL/session/destructive guardがMeeting/Adminへ分岐し、consumerのDB構成がservice package依存を生む。
- 変更:
  - URL/pool/session/metadataとRF-02の`require_destructive_schema_permission()`を`meeting_models.database`へmoveする。
  - `meeting_api.database`と`admin_models.database`のlegacy public symbolは同一objectをre-exportする。
  - Meeting/Admin/Calendar consumerをshared importへ切り替え、URL組立、pool option、transaction境界をgolden一致させる。
  - RF-02の全破壊entrypointがmove後もguardを必ず通る。DB schema/migrationを変更しない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-66A`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-66A`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `tests3/unit/test_service_boundaries.py::test_shared_database_configuration_matches_existing_urls_and_pool_settings`
  - `services/admin-api/tests/test_database_guard.py::{test_destructive_entrypoints_require_explicit_permission,test_legacy_and_shared_guard_are_same_object}`
  - `services/meeting-api/tests/test_meeting_model_metadata.py::test_metadata_snapshot_is_unchanged`
  - `V-MEETING`, `V-BACKEND`
- リスクと戻し方: DB metadata/pool/guard drift。snapshotまたはguard testが1件でも違えば中断し、migrationで合わせない。前SHAから再実行。
- 依存: RF-02, RF-65
- コミット: `RF-66A share database infrastructure without schema drift`

### RF-66B Pure schema/security utilityをmeeting-contractsへ移す

- 対象:
  - `services/meeting-api/meeting_api/schemas.py:1-1364`
  - `services/meeting-api/meeting_api/security_headers.py:1-52`
  - `services/meeting-api/meeting_api/redaction.py:1-35`
  - `services/meeting-api/meeting_api/webhook_url.py:1-138`
  - `libs/meeting-contracts/meeting_contracts/__init__.py:1-末尾`（RF-10作成物）
  - 新規 `libs/meeting-contracts/meeting_contracts/{schemas,security_headers,redaction,webhook_url}.py:1-末尾`
  - `services/admin-api/app/main.py:1-末尾`
  - `services/api-gateway/main.py:1-末尾`
- 問題: Admin/Gatewayがpure contract/security helperのためMeeting API application package全体をinstallする。
- 変更:
  - DB/FastAPI routerに依存しないschema、security header、redaction、webhook URL validatorだけを`meeting_contracts`へmoveする。
  - 旧Meeting moduleは同じclass/function objectをre-exportする。公開schema field/default/validation error/JSON schemaを変更しない。
  - Admin/Gatewayを新package importへ切替え、RF-03B/RF-05Eのsecret/URL contractを再実行する。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-66B`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-66B`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `tests3/unit/test_service_boundaries.py::test_pure_contract_modules_do_not_import_meeting_application`
  - `::test_admin_gateway_use_meeting_contracts_directly`
  - Meeting OpenAPI/schema snapshot差はRF-03A〜03C/RF-05A〜05H/RF-06A〜06H明示差だけ。
  - `V-MEETING`, `V-BACKEND`。
- リスクと戻し方: Pydantic class identity/error文差。legacy/new import identityとJSON schemaをsnapshotし、差で中断。前SHAから再実行。
- 依存: RF-10, RF-66A
- コミット: `RF-66B move pure meeting contracts behind compatibility exports`

### RF-66C Cross-service Meeting API package installを除去

- 対象:
  - `services/calendar-service/Dockerfile:13-15`
  - `services/admin-api/Dockerfile:15-17`
  - `services/api-gateway/Dockerfile:1-末尾`
  - `services/admin-api/app/main.py:1-末尾`
  - `services/calendar-service/app/{main,models,sync}.py:1-末尾`
  - `services/api-gateway/main.py:1-末尾`
  - `deploy/lite/Dockerfile.lite:1-末尾`
- 問題: RF-65/66A/66B後もDockerfileがMeeting applicationを丸ごとCOPY/installすればservice境界とimageサイズ問題が残る。
- 変更:
  - repo-wide static import testでAdmin/Calendar/Gatewayのproduction `meeting_api` importを0にする。
  - 各DockerfileからMeeting API sourceの丸ごとCOPY/installだけを削除し、`meeting-models`/`meeting-contracts`を明示COPY/installする。
  - deploy/Liteのbuild contextも同じpackageを含める。lockfile/依存versionは変更しない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-66C`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-66C`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `tests3/unit/test_service_boundaries.py::test_admin_calendar_gateway_do_not_import_or_install_meeting_api_package`
  - exact command:
    - `docker build -f services/meeting-api/Dockerfile -t rf66-meeting .`
    - `docker build -f services/admin-api/Dockerfile -t rf66-admin .`
    - `docker build -f services/calendar-service/Dockerfile -t rf66-calendar .`
    - `docker build -f services/api-gateway/Dockerfile -t rf66-gateway .`
  - 各image内`python -c`でshared import成功、`import meeting_api`はMeeting image以外失敗。
  - `V-MEETING`, `V-BACKEND`。
- リスクと戻し方: clean build context/package metadata漏れ。4 imageのどれかが失敗したらcommitせず、前SHAから再実行。DB migrationなし。
- 依存: RF-66B
- コミット: `RF-66C remove cross-service meeting application installs`

### フェーズ4ゲート

- Meeting lifecycle production import graphにcycle 0。
- RF-00BのMeeting/Runtime/Gemini/Transcription goldenはRF-11/24の明示差以外0。
- `request_bot` endpoint、deferred entrypoint、Gemini旧functionはcompatibility wrapperを維持。
- DB metadata snapshot差0、schema migration 0、Redis key prefix差0。
- Gateway OpenAPI route/method/status/header/timeout差はRF-03A〜03C、RF-04A〜04B、RF-05A〜05H、RF-06A〜06H、RF-08〜09の明示差だけ。
- Admin/Calendar/Gateway imageからMeeting API package全体への依存0。
- `V-MEETING`、`V-BACKEND`、`V-TRANSCRIPTION`がpass。

```bash
set -Eeuo pipefail
test -n "${MASTER_TASK:-}"
test -n "${RELEASE_ID:-}"
test "${TASK:-}" = "${MASTER_TASK}-${RELEASE_ID}"
PHASE_ITEMS='RF-52,RF-53,RF-54,RF-55,RF-56,RF-57,RF-58,RF-59,RF-60,RF-61,RF-62,RF-63,RF-64,RF-65,RF-66A,RF-66B,RF-66C'
bash scripts/test/run-refactor-phase-stage.sh \
  --phase phase-4 --expect-items "$PHASE_ITEMS"
```

exit 0、`.pipeline/evidence/$TASK/phase-gates/phase-4.json`が`status=passed`、item ID byte一致、command/assertion 1件以上、`head_sha`=RF-66C commitであること。

## 8. フェーズ5: Frontend・Bot Coreの契約維持型リファクタリング

### RF-67 Dashboard API契約・mapper・表示statusを分離

- 対象:
  - `services/dashboard/src/lib/api.ts:265-368`
  - `services/dashboard/src/types/vexa.ts:1-4,350-430`
  - `services/dashboard/src/lib/retranscription-status.ts:1-17`
  - 新規 `services/dashboard/src/lib/api/contracts.ts:1-末尾`
  - 新規 `services/dashboard/src/lib/api/meeting-mapper.ts:1-末尾`
  - 新規 `services/dashboard/src/lib/meeting-status.ts:1-末尾`
  - 新規 `services/dashboard/tests/refactor/rf_67.test.ts:1-末尾`
- 問題: DTO、runtime mapping、表示設定が混在し、型fileがruntime helperをimportする逆依存がある。
- 変更:
  - `src/lib/api/contracts.ts`: wire DTO型だけ。
  - `src/lib/api/meeting-mapper.ts`: DTOからdomain `Meeting`へのpure mapping。
  - `src/lib/meeting-status.ts`: status分類と表示model。
  - `types/vexa.ts`: type-only export。runtime import 0。
  - `vexaAPI` facadeとpublic call signatureは維持し、callsiteへ新module pathを直接広げない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-67`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-67`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_meeting_mapper.test.ts::test_api_fixture_maps_to_domain_golden`
  - `test_meeting_status.test.ts::test_status_and_retranscription_display_matrix`
  - `test_import_boundaries.test.ts::test_type_modules_have_no_runtime_imports`
  - `V-DASH`
- リスクと戻し方: default/null normalization差。RF-00C fixtureのdeep equality差0。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-15, RF-16, RF-20
- コミット: `RF-67 separate dashboard API contracts and mapping`

### RF-68 TranscriptViewerのpure view modelを抽出し到達不能codeを削除

- 対象:
  - `services/dashboard/src/components/transcript/transcript-viewer.tsx:121-389,752-896`
  - 新規 `services/dashboard/src/lib/transcript-view-model.ts:1-末尾`
  - 新規 `services/dashboard/tests/refactor/rf_68.test.ts:1-末尾`
- 問題: 表示計算と未使用AI/export処理が混在し、lint errorと変更影響を増やす。
- 変更:
  - `buildTranscriptViewModel(input)` を `src/lib/transcript-view-model.ts` へ抽出し、speaker filter、literal search、active playback、confirmed/pending、timeline groupingをpure計算。
  - TypeScript compiler/eslint/`rg`で参照0のhandler/state/importだけ削除する。到達可能性が不明なcodeは削除しない。
  - UI markup、className、文言はこの項目で変更しない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-68`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-68`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_transcript_view_model.test.ts::test_speaker_filter_literal_search_and_active_segment`
  - `::test_confirmed_and_pending_view_model`
  - `::test_multi_session_absolute_timeline`
  - 削除symbolのrepo参照0。
  - RF-00C desktop/mobile screenshotのDOM role/text差0。
  - `V-DASH`
- リスクと戻し方: callback経由の間接参照を見落とす。TypeScript/lint/build/E2E全通過を必須。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-12, RF-13, RF-14, RF-18, RF-19, RF-67
- コミット: `RF-68 extract transcript view modeling and remove dead paths`

### RF-69 TranscriptViewerの編集hookと表示componentを分割

- 対象:
  - `services/dashboard/src/components/transcript/transcript-viewer.tsx:406-750,897-1486`
  - RF-68の `services/dashboard/src/lib/transcript-view-model.ts:1-末尾`
  - 新規 `services/dashboard/src/hooks/use-speaker-editing.ts:1-末尾`
  - 新規 `services/dashboard/src/hooks/use-voiceprint-selection.ts:1-末尾`
  - 新規 `services/dashboard/src/components/transcript/transcript-toolbar.tsx:1-末尾`
  - 新規 `services/dashboard/src/components/transcript/transcript-timeline.tsx:1-末尾`
  - 新規 `services/dashboard/tests/refactor/rf_69.test.ts:1-末尾`
- 問題: speaker編集、voiceprint、scroll、selection、toolbar、timelineが1,486行componentに集中する。
- 変更:
  - `useSpeakerEditing(meetingId)`へspeaker rename/merge requestとgeneration guard。
  - `useVoiceprintSelection(meetingId)`へselection/API/error。
  - `TranscriptToolbar`へ検索/filter/action表示。
  - `TranscriptTimeline`へsegment list/pending/renderとcontainer scroll。
  - `TranscriptViewer`はhook接続、view model生成、compositionだけにし、独自polling loop/API URL生成を持たない。
  - props/event callback typeを明示し、既存DOM role/aria/text/classNameを維持。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-69`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-69`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_speaker_editing.test.ts::test_rename_merge_success_error_and_stale_response`
  - `test_voiceprint_selection.test.ts::test_selection_is_meeting_scoped`
  - `test_transcript_components.test.ts::test_toolbar_and_timeline_contract`
  - Viewer本体700行以下、`setInterval`/raw fetch 0。
  - visual regressionで意図しない差0。
  - `V-DASH`
- リスクと戻し方: UI event propagation/focus差。DOM roleとkeyboard fixture、visual diffを必須。commit 共通retry protocol。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-16, RF-18, RF-68
- コミット: `RF-69 split transcript editing and presentation`

### RF-70 Meeting detailのaction modelとresponsive headerを共通化

- 対象:
  - `services/dashboard/src/app/meetings/[id]/page.tsx:98-779,1032-1724`
  - 新規 `services/dashboard/src/hooks/use-meeting-actions.ts:1-末尾`
  - 新規 `services/dashboard/src/components/meetings/meeting-header.tsx:1-末尾`
  - 新規 `services/dashboard/tests/refactor/rf_70.test.ts:1-末尾`
- 問題: desktop/mobileでtitle更新・export actionが重複し、同じ操作の条件/feedbackがずれる。
- 変更:
  - `useMeetingActions(meetingId)`へ `saveTitle`, `exportTranscript`, `retryPostMeeting`, `openProvider` を集約。
  - `MeetingActionModel`をdesktop/mobile両方の`MeetingHeader`へ渡す。
  - title API call implementationは1つ、同時saveはsingle-flight、late responseはRF-15 generation guard。
  - responsive markup差はheader component内のCSS/layoutだけとし、action callbacksは同一。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-70`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-70`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_meeting_actions.test.ts::test_title_save_has_one_api_call`
  - `::test_duplicate_save_is_single_flight`
  - `::test_export_action_is_shared_by_desktop_and_mobile`
  - `test_meeting_header.test.ts::test_desktop_and_mobile_receive_same_action_model`
  - title更新API callsiteが1実装。
  - `V-DASH`
- リスクと戻し方: mobile/desktop固有UIを失う。視覚baseline両viewportで比較し、actionだけ共通化。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-15, RF-17, RF-67
- コミット: `RF-70 unify meeting detail actions across layouts`

### RF-71 Meeting detailのplayback・Browser・TTS compositionを分割

- 対象:
  - `services/dashboard/src/app/meetings/[id]/page.tsx:318-351,781-948,1725-2452`
  - 既存（RF-20で追加済み） `services/dashboard/src/lib/browser-session-view-model.ts:1-末尾`
  - 既存 `services/dashboard/src/components/meetings/browser-session-view.tsx:1-末尾`
  - 新規 `services/dashboard/src/hooks/use-meeting-playback.ts:1-末尾`
  - 新規 `services/dashboard/src/components/meetings/meeting-playback-panel.tsx:1-末尾`
  - 新規 `services/dashboard/src/components/meetings/meeting-browser-session-panel.tsx:1-末尾`
  - 新規 `services/dashboard/src/hooks/use-meeting-tts.ts:1-末尾`
  - 新規 `services/dashboard/tests/refactor/rf_71.test.ts:1-末尾`
- 問題: recording取得、fragment mapping、post-meeting lifecycle、Browser view、TTS、JSXがroute componentへ集中する。
- 変更:
  - `useMeetingPlayback(meetingId)`へrecording取得、fragment/session mapping、selected source。
  - `MeetingPlaybackPanel`へAudio/Video切替。
  - RF-20の`BrowserSessionViewModel`と既存`browser-session-view.tsx`をcompositionし、新規 `src/components/meetings/meeting-browser-session-panel.tsx` に `MeetingBrowserSessionPanel`を作る。propsは `{model: BrowserSessionViewModel, onSave(): Promise<void>, onRetry(): void}` に固定し、desktop/mobileが同じcomponentを使う。
  - `useMeetingTts`へTTS request/play/cancel。
  - Pageはroute param、store selector、hook接続、tab compositionだけにし、raw fetch、`setInterval`、VNC URL組立を持たない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-71`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-71`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_meeting_playback.test.ts::test_recording_fragments_map_to_sessions`
  - `::test_source_switch_resets_media_state`
  - `test_meeting_tts.test.ts::test_cancel_and_meeting_switch_cleanup`
  - `test_browser_session_routes.test.ts::meeting_browser_session_panel_uses_rf20_view_model_for_both_layouts`
  - Page本体1,200行以下、`setInterval` 0、VNC URL builder 0、title API call 0。
  - desktop/mobile visual regressionの意図しない差0。
  - `V-DASH`
- リスクと戻し方: composition時のmount/unmountでmediaがresetする。tab切替fixtureとE2Eで固定。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-17, RF-20, RF-21, RF-22, RF-23, RF-69, RF-70
- コミット: `RF-71 split meeting playback and session composition`

### RF-72 Vexa Bot coreのruntime stateをplatformから切り離す

- 対象:
  - `services/vexa-bot/core/src/index.ts:1-80`
  - `services/vexa-bot/core/src/platforms/shared/meetingFlow.ts:1-5`
  - `services/vexa-bot/core/src/services/audio-pipeline.ts:62`
  - `services/vexa-bot/core/src/platforms/googlemeet/recording.ts:1-末尾`
  - `services/vexa-bot/core/src/platforms/msteams/recording.ts:1-末尾`
  - `services/vexa-bot/core/src/platforms/zoom/strategies/recording.ts:1-末尾`
  - `services/vexa-bot/core/src/platforms/zoom/web/recording.ts:1-末尾`
  - 新規 `services/vexa-bot/core/src/runtime/runtime-state.ts:1-末尾`
  - 新規 `services/vexa-bot/core/src/refactor-tests/rf_72.test.ts:1-末尾`
- 問題: `index.ts`がplatformをimportし、platform/serviceがindexのglobal getterをimportする循環。
- 変更:
  - `src/runtime/runtime-state.ts`へcurrent page/browser/context/stop signal等のstate/getter/setterを移す。
  - stateは明示`createRuntimeState()`でinstance化し、test間global leakをなくす。
  - legacy getterはindexからre-exportするが定義を持たない。
  - platform/service importを`runtime-state`へ切替。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-72`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-72`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `runtime-state.test.ts::isolates_two_runtime_instances`
  - `::legacy_getters_reference_the_same_state`
  - `import-graph.test.ts::platforms_do_not_import_core_index`
  - clean processで各platform module単独import成功。
  - `V-CORE`
- リスクと戻し方: singleton identity差。production compositionは1 instanceを共有し、testで確認。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-08, RF-09B
- コミット: `RF-72 separate bot runtime state from the entrypoint`

### RF-73 Vexa Bot coreをportsとlifecycle moduleへ分割

- 対象:
  - `services/vexa-bot/core/src/index.ts:1-2830`
  - `services/vexa-bot/core/src/platforms/shared/meetingFlow.ts:1-末尾`
  - 新規 `services/vexa-bot/core/src/runtime/browser-launch.ts:1-末尾`
  - 新規 `services/vexa-bot/core/src/runtime/command-handler.ts:1-末尾`
  - 新規 `services/vexa-bot/core/src/runtime/diagnostics.ts:1-末尾`
  - 新規 `services/vexa-bot/core/src/runtime/shutdown.ts:1-末尾`
  - 新規 `services/vexa-bot/core/src/refactor-tests/rf_73.test.ts:1-末尾`
- 問題: platform起動、音声、Browser、command、diagnostics、shutdownがentrypointへ混在する。
- 変更:
  - `meetingFlow`は `{hasStopSignal, triggerCamera, triggerChat, startVideo, enterFullscreen}` portsを引数で受ける。
  - `runtime/browser-launch.ts`
  - `runtime/command-handler.ts`
  - `runtime/diagnostics.ts`
  - `runtime/shutdown.ts`
  - `index.ts`はenv/config parse、composition、start/awaitのみ。
  - signal order、browser options、callback payload、shutdown orderをRF-00C/core goldenに一致。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-73`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-73`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `meeting-flow-ports.test.ts::test_flow_uses_injected_ports`
  - `shutdown.test.ts::test_shutdown_order_matches_baseline`
  - `command-handler.test.ts::test_browser_save_protocol_and_legacy_compatibility`
  - `import-graph.test.ts::test_core_production_graph_is_acyclic`
  - `index.ts`500行以下。
  - `V-CORE`
- リスクと戻し方: process signalとresource cleanupの順序差。fake browser/audio/runtimeでgolden固定。共通retry protocol可能。 失敗branchと証拠を保持し、直前合格SHAから新attemptで当該項目を再実行する。
- 依存: RF-72
- コミット: `RF-73 split bot runtime lifecycle from the entrypoint`

### RF-74A Agent MessageBubbleを共通presentationへ移す

- 対象:
  - `services/dashboard/src/components/agent/agent-chat.tsx:27-58`
  - `services/dashboard/src/components/agent/meeting-agent-panel.tsx:33-64`
  - 新規 `services/dashboard/src/components/agent/message-bubble.tsx:1-末尾`
  - 新規 `services/dashboard/tests/test_agent_message_bubble.test.ts:1-末尾`
- 問題: 同じbubble markupが2実装あり、message型とspacing差だけが局所的に混在する。
- 変更:
  - 共通propsを `{role, content, timestamp?, pending?, density: "chat"|"panel"}` に固定する。
  - 各callerの既存型はcaller内mapperで共通propsへ変換し、共通componentはAPI/domain型をimportしない。
  - `density`で現在のclassName/余白差を保持し、文言/role/ariaを変えない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-74A`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-74A`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/dashboard/tests/test_agent_message_bubble.test.ts::{test_chat_variant_matches_before_snapshot,test_panel_variant_matches_before_snapshot}`
  - 旧local `MessageBubble`定義0、共通定義1。
  - `V-DASH`。
- リスクと戻し方: visual density差。両画面のsnapshot/E2E差があれば中断し、前SHAから再実行。
- 依存: RF-69, RF-71
- コミット: `RF-74A share agent message bubble presentation`

### RF-74B OAuth state registry clientを共通化

- 対象:
  - `services/dashboard/src/app/api/calendar/oauth/start/route.ts:1-末尾`
  - `services/dashboard/src/app/api/calendar/oauth/complete/route.ts:1-末尾`
  - `services/dashboard/src/app/api/zoom/oauth/start/route.ts:1-末尾`
  - `services/dashboard/src/app/api/zoom/oauth/complete/route.ts:1-末尾`
  - 新規 `services/dashboard/src/lib/server/oauth-state-registry.ts:1-末尾`
  - 新規 `services/dashboard/tests/test_oauth_state.test.ts:1-末尾`
- 問題: RF-03B後も、認証cookieの転送、provider固定、state create/consume、PKCE challenge/verifier処理、error mappingがCalendar/Zoomの4 routeへ重複し、片側だけのsecurity修正を生む。
- 変更:
  - server-only `createOAuthFlow(provider, request)`と`consumeOAuthFlow(provider, request, state)`へ、HttpOnly user token転送、Gateway registry request、response schema検証、PKCE S256、timeout、no-store、secret redaction、共通error分類をmoveする。providerはliteral union `calendar|zoom`で、caller body/stateから選ばない。
  - Calendar/Zoom固有authorize/token endpoint、client ID/secret、scope、purpose-specific Admin PATCHだけを各route adapterに残す。static signed state、email/user ID payload、共通secret fallbackを再導入しない。
  - helperはcreate responseの`state,code_challenge`とconsume responseの`subject_id,redirect_uri,return_to,pkce_verifier`をexact allow-listでparseし、provider/redirectがadapter定数と不一致ならprovider exchange前に失敗する。verifierはtoken exchange request構築後に上書き可能なBufferへ移し、response/log/errorへ出さない。
  - client bundleからserver helper importをimport-boundary testで禁止する。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-74B`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-74B`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/dashboard/tests/test_oauth_state.test.ts::{test_calendar_and_zoom_use_same_registry_client_with_provider_literal,test_tampered_expired_cross_subject_and_replayed_state_are_rejected_before_exchange,test_provider_redirect_and_pkce_mismatch_preserve_route_error_contract,test_registry_verifier_never_enters_browser_response_log_or_exception,test_server_helper_is_absent_from_client_bundle}`
  - registry create/consume client実装1、旧signature parser/sign function 0、server-only boundary pass。
  - `V-DASH`。
- リスクと戻し方: provider固有validation消失。共通helperへauthorize/token endpointやscopeを入れずroute fixture差で中断し、失敗branchを保持してRF-74A直後の合格SHAから再実行する。
- 依存: RF-67, RF-03B
- コミット: `RF-74B share OAuth state registry client`

### RF-74C Audio resampling helperを共通化

- 対象:
  - `services/vexa-bot/core/src/services/audio.ts:223-251`
  - `services/vexa-bot/core/src/utils/browser.ts:245-270`
  - 新規 `services/vexa-bot/core/src/audio/resample.ts:1-末尾`
  - 新規 `services/vexa-bot/core/src/audio/resample.test.ts:1-末尾`
- 問題: 同じFloat32 resampling algorithmが2実装へ分岐する。
- 変更: 既存の丸め、output length、sample interpolationを1文字単位でpure functionへmoveし、2 callerは同じhelperを呼ぶ。algorithm/qualityを変更しない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-74C`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-74C`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/vexa-bot/core/src/audio/resample.test.ts::{test_rate_matrix_matches_before_bytes,test_empty_single_sample_and_clipping_match_before_bytes}`
  - `resampleAudioData` algorithm定義1。
  - `V-CORE`。
- リスクと戻し方: sample rounding差。byte差1個で中断し前SHAから再実行。
- 依存: RF-73
- コミット: `RF-74C share bot audio resampling`

### RF-74D Float32-to-PCM変換を共通化

- 対象:
  - `services/vexa-bot/core/src/services/raw-capture.ts:154,198-207`
  - `services/vexa-bot/core/src/services/recording.ts:71,453-462`
  - 新規 `services/vexa-bot/core/src/audio/pcm.ts:1-末尾`
  - 新規 `services/vexa-bot/core/src/audio/pcm.test.ts:1-末尾`
- 問題: clamp/scaling/endian変換が2実装へ重複する。
- 変更: 現行clamp `[-1,1]`、negative/positive scaling、little-endian writeをpure helperへmoveし2 callerを切替える。Buffer ownership/copy回数以外のbyte列を変えない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-74D`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-74D`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/vexa-bot/core/src/audio/pcm.test.ts::{test_edge_float_matrix_matches_before_bytes,test_output_is_little_endian}`
  - PCM conversion定義1、`V-CORE`。
- リスクと戻し方: overflow/endian差。全byte golden差で中断。
- 依存: RF-73
- コミット: `RF-74D share bot PCM conversion`

### RF-74E Meeting duration表示を共通化

- 対象:
  - `services/dashboard/src/app/meetings/[id]/page.tsx:1142`
  - `services/dashboard/src/components/meetings/meeting-card.tsx:108`
  - 新規 `services/dashboard/src/lib/meeting-duration.ts:1-末尾`
  - 新規 `services/dashboard/tests/test_meeting_duration.test.ts:1-末尾`
- 問題: 分単位durationの同一表示がPage/Cardに重複する。`src/lib/export.ts`は開始/終了時刻入力で別契約のため対象外。
- 変更: Page/Cardの現在の0/分/時間境界と日本語文言をpure helperへmoveする。`export.ts`は変更しない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-74E`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-74E`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/dashboard/tests/test_meeting_duration.test.ts::{test_page_and_card_duration_matrix_match_before_strings,test_export_duration_contract_is_unchanged}`
  - 分単位`formatDuration`定義1、export用定義は意図的に残る。
  - `V-DASH`。
- リスクと戻し方: 別signatureのexport helperを誤統合する危険。対象外file差分があれば中断。
- 依存: RF-70
- コミット: `RF-74E share meeting duration display`

### RF-74F Meeting fetchのtransient判定を共通化

- 対象:
  - `services/dashboard/src/lib/api.ts:66-78`
  - `services/dashboard/src/stores/meetings-store.ts:29-41`
  - 新規 `services/dashboard/src/lib/transient-meeting-error.ts:1-末尾`
  - 新規 `services/dashboard/tests/test_transient_meeting_error.test.ts:1-末尾`
- 問題: retry可否predicateがAPI/storeへ重複し、status/network errorの片側driftを生む。
- 変更: 両実装へ同じfixtureを先に当て完全一致を確認し、同じ場合だけ共通pure predicateへmoveする。1caseでも差があれば統合せず本項目を停止して計画修正を求める。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-74F`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-74F`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/dashboard/tests/test_transient_meeting_error.test.ts::{test_api_and_store_matrix_match_before_results,test_shared_predicate_preserves_all_statuses}`
  - transient meeting fetch predicate定義1、`V-DASH`。
- リスクと戻し方: retry増減。fixture差を「改善」として更新せず中断。
- 依存: RF-15
- コミット: `RF-74F share transient meeting fetch classification`

### RF-74G Meeting status正規化validatorを共通化

- 対象:
  - `services/meeting-api/meeting_api/schemas.py:932,1198`
  - 新規 `services/meeting-api/meeting_api/status_normalization.py:1-末尾`
  - 新規 `services/meeting-api/tests/test_status_normalization.py:1-末尾`
- 問題: Pydantic validator内の同一status normalizationが2定義へ重複する。
- 変更: pure `normalize_status_value`へ現行case/alias/null/type/errorをmoveし、両validatorはdelegateする。field名/error locationは各validator側で維持する。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-74G`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-74G`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/meeting-api/tests/test_status_normalization.py::{test_all_status_inputs_match_before_values,test_validation_error_json_matches_before}`
  - normalization algorithm定義1、`V-MEETING`。
- リスクと戻し方: validation error path差。値だけでなくerror JSON snapshot差で中断。
- 依存: RF-63, RF-66B
- コミット: `RF-74G share meeting status normalization`

### RF-74H 参照されないtranscript package archiveを削除

- 対象: `services/dashboard/vexaai-transcript-rendering-0.2.0.tgz:1-末尾`（binary全体）
- 問題: Dashboardが使用するpackageは0.4.1系なのに0.2.0 archiveが残り、手動install対象に見える。
- 変更:
  - `git grep -n "vexaai-transcript-rendering-0.2.0.tgz"`、package manifests、Dockerfile、workflow、docsで参照0を確認する。
  - `packages/transcript-rendering/package.json`とDashboardのresolved package versionが0.2.0ではないことを証拠化する。
  - archive 1fileだけを削除する。他のpackage/cache/lockfileは変更しない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-74H`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-74H`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - 削除前のrepo参照0。
  - 削除後 `test ! -e services/dashboard/vexaai-transcript-rendering-0.2.0.tgz`。
  - commit前 `git diff --cached --name-only` が当該1fileだけ、commit後 `git show --pretty= --name-only HEAD` が当該1fileだけ。
  - `V-TRANSCRIPT`, `V-DASH`。
- リスクと戻し方: undocumented manual installが使う可能性。repo内参照0とrelease docsを確認し、外部配布の証拠があれば削除せず中断。失敗branchは保持し、前SHAから新worktreeで再実行する。
- 依存: RF-71
- コミット: `RF-74H remove the unreferenced transcript package archive`

### RF-74I 恒久redirect配下のDashboard docs sourceを削除

- 対象:
  - `services/dashboard/next.config.ts:61-80`
  - `services/dashboard/src/app/docs/**:1-末尾`
- 問題: `/docs*`は外部へ恒久redirectする一方、同routeのsource treeが残り、どちらが正規docsか誤解させる。
- 変更:
  - `next.config.ts`のredirect対象を全docs routeへ展開したfixtureを作る。
  - repo内link/import/navigationで`src/app/docs/**`のcomponentを直接参照していないことを確認する。
  - desktop/mobile E2Eで各代表route `/docs`, `/docs/auth`, `/docs/rest/meetings`, `/docs/ws/events` が期待する外部host/pathへ308 redirectすることを確認する。
  - `src/app/docs/**`だけを削除し、redirect設定は維持する。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-74I`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-74I`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `test_docs_redirects.test.ts::test_all_removed_docs_routes_are_permanently_redirected`
  - `::test_redirect_target_preserves_required_subpath`
  - source削除後にrepo内direct import/link 0。
  - `V-DASH`。
- リスクと戻し方: redirect対象外の隠れroute削除。route inventoryに1件でもredirectなしがあれば削除せず中断。失敗branchは保持し、前SHAから再実行する。
- 依存: RF-71
- コミット: `RF-74I remove dashboard docs sources behind permanent redirects`

### RF-75A CommonJS検証scriptと未escape JSXをlint準拠へする

- 対象:
  - `services/dashboard/{agent-flow,agent-inspect,auth-validate-final,auth-validate,auth-validate2,auth-validate3,check-pages,deliver-validate,feature-validate}.js:1-末尾`
  - 新規 `services/dashboard/{agent-flow,agent-inspect,auth-validate-final,auth-validate,auth-validate2,auth-validate3,check-pages,deliver-validate,feature-validate}.mjs:1-末尾`
  - RF-74I後に残る`react/no-unescaped-entities`違反は、RF-00C lint baselineが示す各`relative_file:line-end_line`
  - 新規 `services/dashboard/scripts/check-lint-cluster.mjs:1-末尾`
  - 新規 `.pipeline/evidence/$TASK/lint/rf-75a.json:1-末尾`
- 問題: baselineの`@typescript-eslint/no-require-imports` 9件と`react/no-unescaped-entities` 26件が、ad-hoc script形式とJSX source表現に集中する。
- 変更:
  - 9 scriptを同名`.mjs`へ`git mv`し、`require("playwright")`をstatic `import { chromium } from "playwright"`へ置換する。CLI argv/exit/output/Playwright操作は変更せず、repo内参照を新pathへ同期する。
  - RF-74Iで消えずに残ったJSXは、表示文字列を変えない`{"'"}`/entity表現だけで修正する。
  - cluster checkerはESLint JSONを読み、対象2 ruleが0、他rule signature/件数がRF-00C baselineより増えていないことを検査する。さらにsorted `entries[{relative_file,line,end_line,rule_id,message,severity}]`, `source_files_sha256[{relative_file,sha256}]`, `eslint_config_sha256`, error/warning総数を`.pipeline/evidence/$TASK/lint/rf-75a.json`へatomic writeする。eslint config/ignoreは変更しない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-75A`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-75A`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/dashboard/tests/refactor/rf_75a.test.ts::{test_nine_mjs_cli_contracts_match_before,test_lint_cluster_checker_rejects_target_rules_and_preserves_others}`
  - `node services/dashboard/scripts/check-lint-cluster.mjs --rules @typescript-eslint/no-require-imports,react/no-unescaped-entities --expect 0` がexit 0。
  - `rf-75a.json`の全`source_files_sha256`がcommit後fileと一致し、entryはfile/line/rule順、対象2 rule entry 0。
  - 9 `.mjs --help`またはmock Playwright startupのbefore/after exit/output一致。
  - `V-DASH`。
- リスクと戻し方: ad-hoc script invocation path変更。repo参照とREADMEを同commitで更新し、外部利用証拠があれば中断。前SHAから再実行。
- 依存: RF-74I
- コミット: `RF-75A close dashboard module and JSX lint errors`

### RF-75B Dashboardのunsafe type lintを解消

- 対象: `.pipeline/evidence/$TASK/lint/rf-75a.json:1-末尾`内の `@typescript-eslint/no-explicit-any`, `@typescript-eslint/no-empty-object-type`, `prefer-const` entryが示す`relative_file:line-end_line`だけ。実装前に全`source_files_sha256`と現在fileの一致を検証し、件数はRF-75A実測値を使う。対象外file/ruleを同commitで変更しない。
- 問題: transport/UI境界が`any`と空object型で型検査を迂回する。
- 変更:
  - 外部入力は`unknown`で受け、type guard/Zod既存schema/判別unionでnarrowする。`as any`、eslint disable、広いindex signatureへ置換しない。
  - 空object型は実意図が「追加propsなし」ならtype aliasを削除し親型を直接使用、辞書なら具体key/value型を定義する。
  - `prefer-const`は再代入がないbindingだけ`const`へする。runtime挙動変更を混ぜない。
  - 同じcheckerで修正後inventoryを`.pipeline/evidence/$TASK/lint/rf-75b.json`へ書き、次項目の唯一の対象表にする。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-75B`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-75B`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/dashboard/tests/refactor/rf_75b.test.ts::{test_unknown_inputs_are_narrowed_without_runtime_default_changes,test_lint_cluster_has_zero_unsafe_type_rules}`
  - cluster checkerで3 rule件数0、他rule増加0。
  - `rf-75b.json`の全`source_files_sha256`がcommit後fileと一致し、対象3 rule entry 0。
  - 対象mapper/API/component testはitem runner、TypeScript検査とDashboard回帰は`V-DASH`だけが実行し、全command exit 0。本文から`npx`やraw test commandを追加実行しない。
- リスクと戻し方: 型を満たすためruntime defaultを変える危険。cast削除だけで済まない挙動差が必要なら中断し新IDを起票。
- 依存: RF-67, RF-75A
- コミット: `RF-75B replace unsafe dashboard lint types`

### RF-75C React hook lifecycle lintを挙動修正で閉じる

- 対象: `.pipeline/evidence/$TASK/lint/rf-75b.json:1-末尾`内の `react-hooks/set-state-in-effect`, `react-hooks/immutability`, `react-hooks/exhaustive-deps` entryが示す`relative_file:line-end_line`だけ。実装前に全`source_files_sha256`と現在fileの一致を検証し、対象外file/ruleを同commitで変更しない。
- 問題: effect内同期state、mutation、不完全dependencyがstale stateや再接続漏れを隠す。disable追加では閉じられない。
- 変更:
  - derived stateはrender時pure計算、外部subscription stateはcontroller callback、prop変更resetはkey/reducer eventへ移す。
  - mutable valueはcopy-on-writeまたはrefへ移し、既存objectを直接変更しない。
  - dependencyは全参照を列挙し、無限loopになるcallbackは`useCallback`/controllerへ安定化する。eslint disableを増やさず、既存disableも対象scopeで0にする。
  - 修正後inventoryを`.pipeline/evidence/$TASK/lint/rf-75c.json`へ書き、次項目の唯一の対象表にする。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-75C`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-75C`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `services/dashboard/tests/refactor/rf_75c.test.ts::{test_verify_page_strict_mode_has_no_duplicate_transition,test_decisions_reconnect_cleans_listener_and_timer,test_video_imperative_api_preserves_contract,test_meeting_switch_discards_stale_effect}`
  - cluster checkerで3 rule件数0。
  - `rf-75c.json`の全`source_files_sha256`がcommit後fileと一致し、対象3 rule entry 0。
  - verify page、Decisions reconnect、Video imperative API、Meeting switchのfake timer/DOM testがpass。
  - `V-DASH`。
- リスクと戻し方: effect timing/再接続差。StrictMode二重mount fixtureとtimer 0を確認し、差があれば中断。
- 依存: RF-15, RF-22, RF-23, RF-75B
- コミット: `RF-75C make dashboard hook lifecycles explicit`

### RF-75D Unused/no-img warningをcode側で0にする

- 対象: `.pipeline/evidence/$TASK/lint/rf-75c.json:1-末尾`内の `@typescript-eslint/no-unused-vars`, `@next/next/no-img-element` entryが示す`relative_file:line-end_line`だけ。実装前に全`source_files_sha256`と現在fileの一致を検証し、対象外file/ruleを同commitで変更しない。
- 問題: 大量のunused symbolがdead code判定を妨げ、残るraw imageがNext image policyを迂回する。
- 変更:
  - symbolごとにTypeScript/`rg`/buildで参照0を確認し、import、local、引数を削除する。public export、side-effect import、reflection/string参照は削除しない。
  - unused parameterをinterface互換で残す必要がある場合だけ`_name`へ変更し、ruleが許容する既存設定に合うことを確認する。
  - raw imageは既存layout/sizeを維持して`next/image`へ切替える。data/blob等で非対応なら当該1箇所だけ、理由/test付き既存例外へ寄せるがglobal ruleを無効化しない。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-75D`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-75D`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `node services/dashboard/scripts/check-lint-cluster.mjs --all --expect-errors 0 --expect-warnings 0` がexit 0、stdoutに`errors=0 warnings=0`。第三者plugin理由でwarningが残る選択肢は認めない。
  - `V-DASH-FINAL`。
- リスクと戻し方: dynamic/side-effect参照削除。参照0とbuild/E2Eが揃わないsymbolは削除せず中断。
- 依存: RF-68, RF-74A, RF-75C
- コミット: `RF-75D close remaining dashboard lint warnings`

### RF-75E Compatibility/structure budget gateを追加

- 対象:
  - 新規 `tests3/unit/test_structure_budget.py:1-末尾`
  - 新規 `tests3/structure-budget.json:1-末尾`
  - 新規 `tests3/compatibility-contracts.json:1-末尾`
- 問題: 分割後の循環、runtime type import、missing test、無期限compat wrapperが再発してもfinal suiteに単一gateがない。
- 変更:
  - production import cycle 0、type module runtime import 0、active missing test 0、Dashboard lint error/warning 0、README ownership missing 0をmachine checkする。
  - compatibility wrapper/re-exportは `owner`, `introduced_in`, `remove_after`, `removal_condition` metadataを必須にし、期限は日付ではなく全consumer移行等の検証可能条件にする。
  - TranscriptViewer 700行、Meeting page 1,200行、core index 500行の上限を構造budgetへ固定し、空白圧縮/複数statement 1行化を禁止するformat checkを付ける。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-75E`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-75E`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `tests3/unit/test_structure_budget.py::{test_production_import_cycles_are_zero,test_compatibility_metadata_is_complete,test_file_size_budgets_and_format_rules_pass,test_active_tests_and_docs_owners_exist}`
  - `V-OPS`, `V-DASH-FINAL`, `V-TRANSCRIPT`, `V-CORE`, `V-MEETING`, `V-BACKEND`, `V-TRANSCRIPTION`。
- リスクと戻し方: 過度に脆い行数/graph gate。既存最終構造を測定したうえで計画値を緩めず、達成不能なら中断し計画reviewへ戻す。
- 依存: RF-51, RF-64, RF-66C, RF-69, RF-71, RF-73, RF-74G, RF-75D
- コミット: `RF-75E enforce final compatibility and structure budgets`

### RF-75F Human-facing文書を実装済み構造へ同期

- 対象:
  - `services/README.md:1-141`
  - `docs/refactoring-execution-plan.md:1-334`
  - `MANIFEST.md:1-227`
  - `docs/README.md:1-45`
  - `tests3/README.md:1-末尾`
  - `deploy/README.md:1-末尾`
  - `deploy/compose/README.md:1-末尾`
  - `deploy/helm/README.md:1-末尾`
  - `deploy/lite/README.md:1-末尾`
  - `docs/harness-guide.md:1-末尾`
  - `docs/managed-agent-harness-architecture.md:1-末尾`
- 問題: 旧bot-manager、future branch、消えたtest、修正前のservice依存を現行仕様のように読める。
- 変更:
  - `services/README.md`をRF-65/66後の実import/image dependencyへ更新する。
  - 旧実行計画へ「履歴資料・現行実行指示ではない」と対象commit/期間を追記する。
  - `MANIFEST.md`へ「future targetでcurrent mainのbinding contractではない」と明記し、未来内容を実装済みへ書き換えない。
  - tests3/deploy/Harness docsをRF-31〜51の実command/status/registry/catalogへ、docs owner表をRF-51へ同期する。
- 完了条件:
  - 項目固有検証 command: `bash scripts/test/run-refactor-item.sh RF-75F`。
  - required suite検証 command: `bash scripts/test/run-required-suites.sh RF-75F`。
  - 期待結果: 登録済みcommandが全てexit 0。test runnerはcollected 1件以上、unexpected skip/xfail 0。argv runnerはmatrixのstdout/stderr条件一致。
  - `tests3/unit/refactor/test_rf_75f.py::{test_human_docs_reference_only_existing_current_files_and_commands,test_planned_and_implemented_language_is_not_conflated,test_docs_check_and_structure_metadata_pass}`
  - `tests3/unit/test_structure_budget.py::{test_compatibility_metadata_is_complete,test_active_tests_and_docs_owners_exist}`
  - `V-OPS`, `V-HARNESS-CONTRACT`。
- リスクと戻し方: 文書が先行/過剰主張する危険。最終HEADで実在するfile/command/testだけ記載し、plannedとimplementedを分ける。前SHAから再実行。
- 依存: RF-51, RF-66C, RF-74I, RF-75E
- コミット: `RF-75F synchronize final architecture and operations documentation`

### フェーズ5ゲート

- Dashboard desktop 1440×900 / mobile 390×844の会議一覧、完了会議詳細、検索、会議切替、post-meeting、Browser Session、Audio/Videoを現HEADで撮影。
- screenshotの意図しない視覚差0、console error 0、unexpected 4xx/5xx 0。
- Dashboard source SHA、稼働revision SHA、screenshot evidence SHAが一致。
- TranscriptViewer 700行以下、Meeting page 1,200行以下、core index 500行以下。ただし行数達成のための空白圧縮・不自然な1行化は禁止。
- production import cycle 0、Dashboard lint error 0、active missing test 0。
- 全required suiteとManaged Harness gateがpass。

```bash
set -Eeuo pipefail
test -n "${MASTER_TASK:-}"
test -n "${RELEASE_ID:-}"
test "${TASK:-}" = "${MASTER_TASK}-${RELEASE_ID}"
PHASE_ITEMS='RF-67,RF-68,RF-69,RF-70,RF-71,RF-72,RF-73,RF-74A,RF-74B,RF-74C,RF-74D,RF-74E,RF-74F,RF-74G,RF-74H,RF-74I,RF-75A,RF-75B,RF-75C,RF-75D,RF-75E,RF-75F'
bash scripts/test/run-refactor-phase-stage.sh \
  --phase phase-5 --expect-items "$PHASE_ITEMS"
```

exit 0、`.pipeline/evidence/$TASK/phase-gates/phase-5.json`が`status=passed`、item ID byte一致、command/assertion 1件以上、`head_sha`=RF-75F commitであること。

## 9. 最終検証と証拠

各releaseの末尾RF commit後に、現在のrelease taskで下記を順番どおり実行する。R7だけがfull suite、全phase、final fixture E2E、7 release aggregatorを追加実行する。外部review、QA、approvalはsourceを変更しないclosure artifactである。MUST_FIXが出た場合は同branchで修正せず、当release taskを`blocked`として終了し、findingと`RELEASE_HEAD_SHA`を入力に別task/別planを作る。

9.1〜9.7は外部review待ちや別sessionを跨ぐ。operatorが直接実行する唯一の入口は9.0のtrusted bootstrapであり、release worktree内のshell/Pythonを直接実行してはいけない。9.1〜9.7の長いcode fenceはwrapper実装者が固定する内部algorithm sketchで、operator commandではない。bootstrapとwrapperはtask/stage/head/parent/environmentへbindしたcanonical selectionを作成または再利用する。attempt receipt作成後stdout受領前に切断してもmatching exact 1件だけを採用し、複数・failed・不一致はblocked。mtime/latest推測や別token自動発行は禁止する。

### 9.0 9.1〜9.7共通preapproval trusted bootstrap

各operator callはfresh terminal processで、次の共通入力を明示設定してから下のblockを最初から実行する。`CONTROL_ROOT`はRF-00A baselineまたは前release completionで固定したabsolute control checkout、`RELEASE_WORKTREE`は当releaseのderived absolute worktreeである。`CLOSURE_STAGE`と`CLOSURE_ACTION`の許可組合せは下表だけで、未使用の外部入力は空にする。

| 呼出し | `CLOSURE_STAGE` | `CLOSURE_ACTION` | 追加入力 |
|---|---|---|---|
| 9.1 | `build-and-state` | `run` | なし |
| 9.2 command 1 | `release-execution` | `run` | なし |
| 9.2 command 2 | `release-finalize` | `run` | なし |
| 9.3 | `manifest` | `run` | なし |
| 9.4 command 1 | `tribunal` | `prepare-external` | なし |
| 9.4 command 2 | `tribunal` | `import-external` | `TRIBUNAL_FINDER,TRIBUNAL_ADVERSARIAL,TRIBUNAL_JUDGE,TRIBUNAL_SYNTHESIS,TRIBUNAL_REPORT` absolute regular files |
| 9.5 | `post-review` | `run` | なし |
| 9.6 command 1 | `approval-target` | `prepare-external` | なし |
| 9.6 command 2 | `approval-target` | `import-external` | `QA_JUDGMENT` absolute regular file |
| 9.7 | `approval` | `run` | `HUMAN_APPROVER,HUMAN_APPROVED_TARGET_SHA256` |

```bash
/usr/bin/env -i \
  HOME=/var/empty PATH=/usr/bin:/bin LANG=C LC_ALL=C \
  CONTROL_ROOT="${CONTROL_ROOT:-}" RELEASE_WORKTREE="${RELEASE_WORKTREE:-}" \
  RELEASE_ID="${RELEASE_ID:-}" CLOSURE_STAGE="${CLOSURE_STAGE:-}" \
  CLOSURE_ACTION="${CLOSURE_ACTION:-}" \
  TRIBUNAL_FINDER="${TRIBUNAL_FINDER:-}" \
  TRIBUNAL_ADVERSARIAL="${TRIBUNAL_ADVERSARIAL:-}" \
  TRIBUNAL_JUDGE="${TRIBUNAL_JUDGE:-}" \
  TRIBUNAL_SYNTHESIS="${TRIBUNAL_SYNTHESIS:-}" \
  TRIBUNAL_REPORT="${TRIBUNAL_REPORT:-}" \
  QA_JUDGMENT="${QA_JUDGMENT:-}" \
  HUMAN_APPROVER="${HUMAN_APPROVER:-}" \
  HUMAN_APPROVED_TARGET_SHA256="${HUMAN_APPROVED_TARGET_SHA256:-}" \
  /bin/bash --noprofile --norc <<'PREAPPROVAL_BOOTSTRAP'
set -Eeuo pipefail
umask 077
readonly SYSTEM_ENV=/usr/bin/env
readonly SYSTEM_PYTHON=/usr/bin/python3
readonly SYSTEM_GIT=/usr/bin/git
readonly SYSTEM_BASH=/bin/bash
readonly MASTER_TASK=full-repo-refactoring-2026-07-24
readonly BOOTSTRAP_PATH=scripts/test/refactor-preapproval-bootstrap.py
test -x "$SYSTEM_ENV" -a -x "$SYSTEM_PYTHON" -a -x "$SYSTEM_GIT" -a -x "$SYSTEM_BASH"
case "$CONTROL_ROOT" in /*) ;; *) exit 2 ;; esac
case "$RELEASE_WORKTREE" in /*) ;; *) exit 2 ;; esac
case "$RELEASE_ID" in r1|r2|r3|r4|r5|r6|r7) ;; *) exit 2 ;; esac
case "$CLOSURE_STAGE:$CLOSURE_ACTION" in
  build-and-state:run|release-execution:run|release-finalize:run|manifest:run|\
  tribunal:prepare-external|tribunal:import-external|post-review:run|\
  approval-target:prepare-external|approval-target:import-external|approval:run) ;;
  *) exit 2 ;;
esac
readonly CONTROL_REAL="$(
  "$SYSTEM_PYTHON" -I -S -c \
    'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve(strict=True))' \
    "$CONTROL_ROOT"
)"
readonly WORKTREE_REAL="$(
  "$SYSTEM_PYTHON" -I -S -c \
    'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve(strict=True))' \
    "$RELEASE_WORKTREE"
)"
test "$("$SYSTEM_GIT" -C "$CONTROL_REAL" rev-parse --show-toplevel)" = "$CONTROL_REAL"
readonly CONTROL_COMMON="$(
  "$SYSTEM_GIT" -C "$CONTROL_REAL" rev-parse --path-format=absolute --git-common-dir
)"
readonly WORKTREE_COMMON="$(
  "$SYSTEM_GIT" -C "$WORKTREE_REAL" rev-parse --path-format=absolute --git-common-dir
)"
test "$("$SYSTEM_PYTHON" -I -S -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve(strict=True))' "$CONTROL_COMMON")" = \
     "$("$SYSTEM_PYTHON" -I -S -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve(strict=True))' "$WORKTREE_COMMON")"
test "$("$SYSTEM_GIT" -C "$CONTROL_REAL" config --local --get-all remote.origin.url)" = \
     "$("$SYSTEM_GIT" -C "$WORKTREE_REAL" config --local --get-all remote.origin.url)"
readonly RELEASE_HEAD_SHA="$("$SYSTEM_GIT" -C "$WORKTREE_REAL" rev-parse --verify 'HEAD^{commit}')"
readonly BOOTSTRAP_OID="$(
  "$SYSTEM_GIT" -C "$CONTROL_REAL" rev-parse --verify "$RELEASE_HEAD_SHA:$BOOTSTRAP_PATH"
)"
readonly TEMP_ROOT="$(
  "$SYSTEM_PYTHON" -I -S -c \
    'import pathlib,tempfile; p="/private/tmp" if pathlib.Path("/private/tmp").is_dir() else "/tmp"; print(tempfile.mkdtemp(prefix="rf-preapproval-",dir=p))'
)"
readonly BOOTSTRAP_FILE="$TEMP_ROOT/bootstrap.py"
set -o noclobber
"$SYSTEM_GIT" -C "$CONTROL_REAL" cat-file blob "$BOOTSTRAP_OID" > "$BOOTSTRAP_FILE"
set +o noclobber
chmod 0500 "$BOOTSTRAP_FILE"
test "$("$SYSTEM_GIT" -C "$CONTROL_REAL" hash-object "$BOOTSTRAP_FILE")" = "$BOOTSTRAP_OID"
"$SYSTEM_PYTHON" -I -S -c \
  'import pathlib,sys; compile(pathlib.Path(sys.argv[1]).read_bytes(),sys.argv[1],"exec")' \
  "$BOOTSTRAP_FILE"

ARGS=(
  --master-task "$MASTER_TASK" --release "$RELEASE_ID"
  --control-root "$CONTROL_REAL" --release-worktree "$WORKTREE_REAL"
  --expected-head "$RELEASE_HEAD_SHA" --stage "$CLOSURE_STAGE"
  --action "$CLOSURE_ACTION" --loader-temp-root "$TEMP_ROOT"
)
if [[ "$CLOSURE_STAGE:$CLOSURE_ACTION" == tribunal:import-external ]]; then
  for pair in \
    "finder=$TRIBUNAL_FINDER" "adversarial=$TRIBUNAL_ADVERSARIAL" \
    "judge=$TRIBUNAL_JUDGE" "synthesis=$TRIBUNAL_SYNTHESIS" \
    "report=$TRIBUNAL_REPORT"; do
    case "${pair#*=}" in /*) ;; *) exit 2 ;; esac
    ARGS+=(--external-artifact "$pair")
  done
elif [[ "$CLOSURE_STAGE:$CLOSURE_ACTION" == approval-target:import-external ]]; then
  case "$QA_JUDGMENT" in /*) ;; *) exit 2 ;; esac
  ARGS+=(--external-artifact "qa=$QA_JUDGMENT")
elif [[ "$CLOSURE_STAGE" == approval ]]; then
  test -n "$HUMAN_APPROVER"
  "$SYSTEM_ENV" -i VALUE="$HUMAN_APPROVED_TARGET_SHA256" PATH=/usr/bin:/bin \
    "$SYSTEM_PYTHON" -I -S -c \
    'import os,re; assert re.fullmatch(r"[0-9a-f]{64}",os.environ["VALUE"])'
  ARGS+=(--human-approver "$HUMAN_APPROVER"
         --human-approved-target-sha256 "$HUMAN_APPROVED_TARGET_SHA256")
fi

set +e
"$SYSTEM_ENV" -i HOME=/var/empty PATH=/usr/bin:/bin LANG=C LC_ALL=C \
  GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
  GIT_OPTIONAL_LOCKS=0 GIT_NO_REPLACE_OBJECTS=1 \
  "$SYSTEM_PYTHON" -I -S "$BOOTSTRAP_FILE" "${ARGS[@]}"
rc=$?
set -e
if [[ "$rc" -eq 0 ]]; then
  "$SYSTEM_PYTHON" -I -S "$BOOTSTRAP_FILE" "${ARGS[@]}" --verify-existing
  rm -rf "$TEMP_ROOT"
  exit 0
fi
echo "trusted preapproval loader preserved after non-success: $TEMP_ROOT" >&2
exit "$rc"
PREAPPROVAL_BOOTSTRAP
```

inline loaderはrepo codeを実行する前にfresh `env -i /bin/bash --noprofile --norc`へ入り、control/worktree identityとGit objectを検証してbootstrap全量をrepo外regular fileへ固定する。抽出bootstrapはtrusted source manifestの全sourceと外部inputを一度だけ全量readし、外部inputは`O_EXCL|O_NOFOLLOW`でtemporary canonical copyしてsource SHA-256をstage input receiptへ固定する。runner success後は同bootstrapの`--verify-existing`がselection、completion、output hash、exact cwdをcontrol storeから再検証し、その成功後だけtemporary rootを削除する。exit 2のwaiting/rejectionとexit 3のterminal failureではrootを証拠として保持しpath/hashをreceiptへ記録する。

### 9.1 最終HEAD固定とbuild summary

operator commandは9.0へ`CLOSURE_STAGE=build-and-state,CLOSURE_ACTION=run`を渡す。9.0以外からwrapperを直接呼ばない。

wrapper内部algorithm（直接実行禁止）:

```bash
set -Eeuo pipefail
test -n "${MASTER_TASK:-}"
test -n "${RELEASE_ID:-}"
test -n "${TASK:-}"
test -n "${RELEASE_BASE_SHA:-}"
test "$TASK" = "${MASTER_TASK}-${RELEASE_ID}"
export REPO_ROOT="$(git rev-parse --show-toplevel)"
export RF_ENV_ROOT="$REPO_ROOT/.pipeline/tmp/$TASK/env"
cd "$REPO_ROOT"
CONTROL_ROOT="$(
  python3 -I -S - "$TASK" "$RELEASE_ID" <<'PY'
import json, pathlib, sys
task, release = sys.argv[1:]
name = "baseline.json" if release == "r1" else "bootstrap.json"
p = pathlib.Path(".pipeline/evidence") / task / name
d = json.loads(p.read_text())
raw = d.get("control_root_realpath") or d.get("control_root")
assert raw
root = pathlib.Path(raw).resolve(strict=True)
assert (root / ".git").exists()
print(root)
PY
)"
export CONTROL_ROOT

test -x "$RF_ENV_ROOT/backend/bin/python"
test -x "$RF_ENV_ROOT/transcription/bin/python"
test -x "$RF_ENV_ROOT/integrations/bin/python"
test -x "$RF_ENV_ROOT/aux/bin/python"

IMPLEMENTATION_STATUS="$(
  git status --porcelain --untracked-files=all -- \
    . ':(exclude).pipeline' ':(exclude).gitnexus'
)"
test -z "$IMPLEMENTATION_STATUS"
git diff --check

git fetch origin main
test "$(git rev-parse origin/main)" = "$RELEASE_BASE_SHA"
git merge-base --is-ancestor "$RELEASE_BASE_SHA" HEAD
test -n "$(git diff --name-only "$RELEASE_BASE_SHA"..HEAD -- . ':(exclude).pipeline')"
RELEASE_HEAD_SHA="$(git rev-parse HEAD)"
FINAL_HEAD="$RELEASE_HEAD_SHA"
IMPLEMENTATION_BASE_SHA="$RELEASE_BASE_SHA"
test -n "${CLOSURE_ATTEMPT_ID:-}"
test -n "${STAGE_ATTEMPT_ID:-}"
"$RF_ENV_ROOT/backend/bin/python" -I -S \
  scripts/test/refactor-closure-attempt.py verify-existing \
    --control-root "$CONTROL_ROOT" --task "$TASK" \
    --release "$RELEASE_ID" --head "$RELEASE_HEAD_SHA" \
    --attempt-id "$CLOSURE_ATTEMPT_ID"
"$RF_ENV_ROOT/backend/bin/python" -I -S \
  scripts/test/refactor-closure-attempt.py verify-stage \
    --control-root "$CONTROL_ROOT" --task "$TASK" \
    --release "$RELEASE_ID" --head "$RELEASE_HEAD_SHA" \
    --parent-attempt "$CLOSURE_ATTEMPT_ID" \
    --stage-attempt "$STAGE_ATTEMPT_ID" --stage build-and-state

BUILD_SUMMARY=".pipeline/evidence/$TASK/build/build-summary.json"
CURRENT_STATE="$(
  "$RF_ENV_ROOT/backend/bin/python" -I -S -c \
    'import json,sys; print(json.load(open(sys.argv[1]))["state"])' \
    ".pipeline/plans/$TASK/checkpoint-contract.json"
)"
case "$CURRENT_STATE" in building|built|verifying) ;; *) exit 2 ;; esac
if [[ -f "$BUILD_SUMMARY" ]]; then
  bash scripts/test/run-refactor-build.sh \
    --verify-existing --mode complete --task "$TASK" \
    --release-base "$RELEASE_BASE_SHA" --worktree "$PWD"
else
  test "$CURRENT_STATE" = building
  bash scripts/test/run-refactor-build.sh \
    --mode complete --task "$TASK" \
    --release-base "$RELEASE_BASE_SHA" --worktree "$PWD"
fi

"$RF_ENV_ROOT/backend/bin/python" -I -S - "$TASK" "$RELEASE_BASE_SHA" "$RELEASE_HEAD_SHA" <<'PY'
import json, pathlib, sys
task, base, head = sys.argv[1:]
p = pathlib.Path(".pipeline/evidence") / task / "build/build-summary.json"
d = json.loads(p.read_text())
assert d["task_id"] == task
assert d["mode"] == "implementation_complete"
assert d["exit_code"] == 0 and d["status"] == "passed"
assert d["release_base_sha"] == base
assert d["implementation_head_sha"] == head
PY

case "$CURRENT_STATE" in
  building)
    bash scripts/harness/backcast-state.sh "$TASK" built \
      --reason "all RF implementation commits completed"
    bash scripts/harness/backcast-state.sh "$TASK" verifying \
      --reason "final immutable HEAD verification begins" ;;
  built)
    "$RF_ENV_ROOT/backend/bin/python" scripts/test/verify-refactor-closure-artifacts.py \
      --task "$TASK" --head "$FINAL_HEAD" --stage build-state-prefix \
      --expect-state built
    bash scripts/harness/backcast-state.sh "$TASK" verifying \
      --reason "final immutable HEAD verification begins" ;;
  verifying)
    "$RF_ENV_ROOT/backend/bin/python" scripts/test/verify-refactor-closure-artifacts.py \
      --task "$TASK" --head "$FINAL_HEAD" --stage build-state-prefix \
      --expect-state verifying ;;
esac
```

RF-00Eのwrapper適用直後から`build-summary.json`はrelease-local `release_base_sha`と`implementation_head_sha`を持つ。legacy `head_sha/base_sha`はR1ではRF-00A commit、R2〜R7では当release branch作成時HEADというimmutable review baseであり、final HEADで上書きしない。RF-49Eはこの既存契約をcanonical buildへ内蔵するだけで、field意味を変更しない。この時点から承認完了まで`git rev-parse HEAD`は`RELEASE_HEAD_SHA`と一致し続けなければならない。

### 9.2 全test・構造gate・fixture E2E

operator command 1/2は9.0へ`CLOSURE_STAGE=release-execution,CLOSURE_ACTION=run`、command 2/2はfresh processの9.0へ`CLOSURE_STAGE=release-finalize,CLOSURE_ACTION=run`を渡す。順序を逆転・同process継続しない。

wrapper内部algorithm（2 stageへ本文順に分割し、直接実行禁止）:

```bash
set -Eeuo pipefail
test -n "${CLOSURE_ATTEMPT_ID:-}"
test -n "${STAGE_ATTEMPT_ID:-}"
"$RF_ENV_ROOT/backend/bin/python" -I -S \
  scripts/test/refactor-closure-attempt.py verify-existing \
    --control-root "$CONTROL_ROOT" \
    --task "$TASK" \
    --release "$RELEASE_ID" \
    --head "$RELEASE_HEAD_SHA" \
    --attempt-id "$CLOSURE_ATTEMPT_ID"
CURRENT_CLOSURE_STAGE=release-gate-execution
export CURRENT_CLOSURE_STAGE

RELEASE_EXECUTION_REPORT=".pipeline/evidence/$TASK/release-gates/$RELEASE_ID/attempts/$CLOSURE_ATTEMPT_ID/execution.json"
if [[ -f "$RELEASE_EXECUTION_REPORT" ]]; then
  bash scripts/test/run-refactor-release-gate.sh \
    --verify-execution \
    --master-task "$MASTER_TASK" --release "$RELEASE_ID" \
    --task "$TASK" --base "$RELEASE_BASE_SHA" --head "$RELEASE_HEAD_SHA" \
    --attempt-id "$CLOSURE_ATTEMPT_ID"
else
  bash scripts/test/run-refactor-release-gate.sh \
    --execute \
    --master-task "$MASTER_TASK" --release "$RELEASE_ID" \
    --task "$TASK" --base "$RELEASE_BASE_SHA" --head "$RELEASE_HEAD_SHA" \
    --attempt-id "$CLOSURE_ATTEMPT_ID"
fi

CURRENT_CLOSURE_STAGE=final-gitnexus
GITNEXUS_FINAL_MANIFEST=".pipeline/evidence/$TASK/gitnexus/releases/$RELEASE_ID/attempts/$CLOSURE_ATTEMPT_ID/$RELEASE_HEAD_SHA/final/manifest.json"
if [[ -f "$GITNEXUS_FINAL_MANIFEST" ]]; then
  bash scripts/test/run-gitnexus-refactor.sh verify-existing \
    --worktree "$PWD" --head "$RELEASE_HEAD_SHA" \
    --task "$TASK" --evidence-scope "release:$RELEASE_ID" \
    --analysis-stage final --attempt-id "$CLOSURE_ATTEMPT_ID" \
    --base-ref "$RELEASE_BASE_SHA"
else
  bash scripts/test/run-gitnexus-refactor.sh analyze \
    --worktree "$PWD" --head "$RELEASE_HEAD_SHA" \
    --task "$TASK" --evidence-scope "release:$RELEASE_ID" \
    --analysis-stage final --attempt-id "$CLOSURE_ATTEMPT_ID"
  bash scripts/test/run-gitnexus-refactor.sh detect-changes \
    --worktree "$PWD" --head "$RELEASE_HEAD_SHA" \
    --task "$TASK" --evidence-scope "release:$RELEASE_ID" \
    --analysis-stage final --attempt-id "$CLOSURE_ATTEMPT_ID" \
    --base-ref "$RELEASE_BASE_SHA"
fi
git fetch origin main
CURRENT_UPSTREAM_MAIN="$(git rev-parse origin/main)"
if [[ "$CURRENT_UPSTREAM_MAIN" != "$RELEASE_BASE_SHA" ]]; then
  echo "origin/main moved from this release base; create a newly reviewed release task" >&2
  exit 1
fi

if [[ "$RELEASE_ID" == "r7" ]]; then
  CURRENT_CLOSURE_STAGE=full-verification
  FULL_REPORT=".pipeline/evidence/$TASK/release-gates/r7/attempts/$CLOSURE_ATTEMPT_ID/full-verification.json"
  if [[ -f "$FULL_REPORT" ]]; then
    bash scripts/test/run-full-refactor-verification.sh \
      --verify-existing --master-task "$MASTER_TASK" \
      --task "$TASK" --head "$RELEASE_HEAD_SHA" \
      --attempt-id "$CLOSURE_ATTEMPT_ID"
  else
    bash scripts/test/run-full-refactor-verification.sh \
      --master-task "$MASTER_TASK" \
      --task "$TASK" --head "$RELEASE_HEAD_SHA" \
      --attempt-id "$CLOSURE_ATTEMPT_ID"
  fi
fi

CURRENT_CLOSURE_STAGE=release-gate-finalize
if [[ ! -f ".pipeline/evidence/$TASK/release-manifest.json" ]]; then
  bash scripts/test/run-refactor-release-gate.sh \
    --finalize \
    --master-task "$MASTER_TASK" --release "$RELEASE_ID" \
    --task "$TASK" --base "$RELEASE_BASE_SHA" --head "$RELEASE_HEAD_SHA" \
    --attempt-id "$CLOSURE_ATTEMPT_ID"
fi
bash scripts/test/run-refactor-release-gate.sh \
  --verify-existing \
  --master-task "$MASTER_TASK" \
  --release "$RELEASE_ID" \
  --task "$TASK" \
  --base "$RELEASE_BASE_SHA" \
  --head "$RELEASE_HEAD_SHA" \
  --attempt-id "$CLOSURE_ATTEMPT_ID"

if [[ "$RELEASE_ID" == "r7" ]]; then
  CURRENT_CLOSURE_STAGE=final-e2e
  E2E_ATTEMPT_ROOT=".pipeline/evidence/$TASK/e2e/attempts/$CLOSURE_ATTEMPT_ID"
  E2E_ATTEMPT_MANIFEST="$E2E_ATTEMPT_ROOT/final-ui.json"
  if [[ ! -f "$E2E_ATTEMPT_MANIFEST" ]]; then
    bash services/dashboard/scripts/run-refactor-e2e.sh \
      --mode final \
      --task "$TASK" \
      --attempt-id "$CLOSURE_ATTEMPT_ID" \
      --expected-source-sha "$RELEASE_HEAD_SHA" \
      --evidence "$E2E_ATTEMPT_ROOT/screenshots/final" \
      --compare ".pipeline/evidence/$MASTER_TASK/baseline-ui.json" \
      --visual-plan ".pipeline/plans/$MASTER_TASK/planned-visual-changes.json" \
      --manifest "$E2E_ATTEMPT_MANIFEST"
  fi
  bash scripts/test/verify-refactor-e2e-evidence.sh \
    --mode final \
    --task "$TASK" \
    --master-task "$MASTER_TASK" \
    --attempt-id "$CLOSURE_ATTEMPT_ID" \
    --expected-source-sha "$RELEASE_HEAD_SHA" \
    --manifest "$E2E_ATTEMPT_MANIFEST" \
    --promote-or-verify-canonical ".pipeline/evidence/$TASK/final-ui.json"
fi
test "$(git rev-parse HEAD)" = "$RELEASE_HEAD_SHA"
```

期待結果:

- CSPRNG closure attempt receipt、execution report、final GitNexus、R7 full-verification、finalized release manifestのproducer順がこの順であり、同じitem/suite/phase/GitNexusを二度実行していない。ack喪失時は選択attemptの`verify-existing`だけが成功する。
- R1〜R6はR1先頭からcurrent release末尾までのmatrix prefixだけが`active`、futureは`planned`。R7は全IDが`active`。active prefix全item commandとrequired suiteがexact 1回、exit 0。
- test runnerは0件収集なし、unexpected skip/xfail 0。RF-00B/00Dのstrict xfailは解消対象RF完了後0件。
- R7ではDashboard raw lint error/warning 0、production build成功。desktop 1440×900 / mobile 390×844の全fixture routeが撮影され、source SHA=`RELEASE_HEAD_SHA`、console/page/request error 0、unexpected 4xx/5xx 0、未承認visual差0。R1〜R6はfinal screenshotを作らない。
- GitNexus compareが計画対象外processへのHIGH/CRITICALを出さない。出た場合は完了を主張しない。
- DockerはRF-05C/RF-05D1B/RF-10/RF-65/RF-66Cに列挙したlocal image build/runだけを使用し、remote registry push、production Docker context、Kubernetes/GCP、production DB、live meetingへ接続していない。`docker context show`が`default`以外、`DOCKER_HOST`が設定済み、または`docker info`が失敗するならDocker item開始前に停止する。

### 9.3 Manifestとevidence pack

operator commandは9.0へ`CLOSURE_STAGE=manifest,CLOSURE_ACTION=run`を渡す。

checkpoint commandを再実行せず、9.2のselected closure attemptとcanonical reportをread-only machine-recordする。

wrapper内部algorithm（直接実行禁止）:

```bash
set -Eeuo pipefail
test -n "${TASK:-}"
test -n "${STAGE_ATTEMPT_ID:-}"
CURRENT_CLOSURE_STAGE=evidence-manifest-pack
EVIDENCE_MANIFEST=".pipeline/evidence/$TASK/evidence-manifest.json"
EVIDENCE_PACK=".pipeline/evidence/$TASK/evidence-pack.md"
if [[ -f "$EVIDENCE_MANIFEST" ]]; then
  bash scripts/harness/backcast-manifest.sh "$TASK" \
    --verify-existing-output --base "$IMPLEMENTATION_BASE_SHA" \
    --verify-existing-commands --closure-attempt "$CLOSURE_ATTEMPT_ID"
else
  bash scripts/harness/backcast-manifest.sh "$TASK" \
    --base "$IMPLEMENTATION_BASE_SHA" \
    --verify-existing-commands --closure-attempt "$CLOSURE_ATTEMPT_ID"
fi
if [[ -f "$EVIDENCE_PACK" ]]; then
  bash scripts/harness/backcast-evidence-pack.sh "$TASK" --verify-existing
else
  bash scripts/harness/backcast-evidence-pack.sh "$TASK"
fi

"$RF_ENV_ROOT/backend/bin/python" -I -S - "$TASK" "$IMPLEMENTATION_BASE_SHA" "$FINAL_HEAD" <<'PY'
import json, pathlib, sys
task, base, head = sys.argv[1:]
p = pathlib.Path(".pipeline/evidence") / task / "evidence-manifest.json"
d = json.loads(p.read_text())
assert d["task_id"] == task
assert d["repo"]["base_sha"] == base
assert d["repo"]["head_sha"] == head
assert not d["missing_evidence"]
assert all(x["status"] == "passed" for x in d["quality_conditions"])
assert all(x["exit_code"] == 0 for x in d["commands"] if x["required"])
assert not d["scope"]["forbidden_paths_changed"]
assert not d["scope"]["allowed_paths_outside_with_justification"]
phase_dir = pathlib.Path(".pipeline/evidence") / task / "phase-gates"
expected_phase_subject = {
    "phase-1": "RF-10 share one meeting URL contract",
    "phase-2": "RF-30 expose voiceprint load failure in health",
    "phase-3": "RF-51 verify owned documentation exists",
    "phase-4": "RF-66C remove cross-service meeting application installs",
    "phase-5": "RF-75F synchronize final architecture and operations documentation",
}
import subprocess
if task.endswith("-r7"):
    for phase, expected_subject in expected_phase_subject.items():
        gate = json.loads((phase_dir / f"{phase}.json").read_text())
        assert gate["task_id"] == task and gate["phase"] == phase
        assert gate["status"] == "passed"
        assert gate["commands"] and gate["assertions"]
        subprocess.run(["git", "merge-base", "--is-ancestor", gate["head_sha"], head], check=True)
        subject = subprocess.check_output(
            ["git", "show", "-s", "--format=%s", gate["head_sha"]], text=True
        ).strip()
        assert subject == expected_subject
else:
    assert not phase_dir.exists() or not list(phase_dir.glob("phase-*.json"))
PY

read -r MANIFEST_SHA256 PACK_SHA256 < <(
  "$RF_ENV_ROOT/backend/bin/python" -I -S - "$EVIDENCE_MANIFEST" "$EVIDENCE_PACK" <<'PY'
import hashlib, pathlib, sys
print(*(hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() for p in sys.argv[1:]))
PY
)
MANIFEST_COMPLETION="$CONTROL_ROOT/.pipeline/closure-stage-attempts/$TASK/manifest/$STAGE_ATTEMPT_ID/completion.json"
if [[ -f "$MANIFEST_COMPLETION" ]]; then
  "$RF_ENV_ROOT/backend/bin/python" -I -S scripts/test/refactor-closure-attempt.py \
    verify-stage --control-root "$CONTROL_ROOT" --task "$TASK" \
    --release "$RELEASE_ID" --head "$FINAL_HEAD" \
    --parent-attempt "$CLOSURE_ATTEMPT_ID" --stage-attempt "$STAGE_ATTEMPT_ID" \
    --stage manifest --expect-complete \
    --output-sha256 "evidence-manifest=$MANIFEST_SHA256" \
    --output-sha256 "evidence-pack=$PACK_SHA256"
else
  "$RF_ENV_ROOT/backend/bin/python" -I -S scripts/test/refactor-closure-attempt.py \
    complete-stage --control-root "$CONTROL_ROOT" --task "$TASK" \
    --release "$RELEASE_ID" --head "$FINAL_HEAD" \
    --parent-attempt "$CLOSURE_ATTEMPT_ID" --stage-attempt "$STAGE_ATTEMPT_ID" \
    --stage manifest \
    --output-sha256 "evidence-manifest=$MANIFEST_SHA256" \
    --output-sha256 "evidence-pack=$PACK_SHA256"
fi
```

### 9.4 L作業の独立tribunal

operator command 1/2は9.0へ`CLOSURE_STAGE=tribunal,CLOSURE_ACTION=prepare-external`を渡し、input manifest固定後の`waiting_external`, exit 2を期待する。

wrapperは`.pipeline/evidence/$TASK/external-inputs/tribunal/$STAGE_ATTEMPT_ID/input-manifest.json`をdestination不存在時だけ作り、`task_id,head_sha,parent_stage_completion_sha256,evidence_pack_sha256,release_manifest_sha256,diff_base_sha,diff_head_sha,diff_sha256,test_e2e_gitnexus_aggregate_sha256,allowed_output_schemas,created_at`へ固定する。既存時は全source/hashをverifyする。外部artifactがまだ無い、provider sessionが切断した、login/送信承認がないことはactual verification failureではない。append-only `waiting-external.json`を同attemptへcreate/verifyし、selection/stateをfailed/blockedへ変えずexit 2にする。

同一implementerの自己レビューを役割名だけ変えてコピーしてはいけない。finder/adversarial/judge/synthesizerの4つの独立したread-only agent/sessionへ、上記input manifestが列挙したartifactだけを渡す。各reviewerは相互に異なる`reviewer_id`と`run_id`を持ち、repo/control/archive外の作業directoryへ出力する。

operator command 2/2は5 external pathを設定したfresh processの9.0へ`CLOSURE_STAGE=tribunal,CLOSURE_ACTION=import-external`を渡す。9.0/bootstrapがabsolute regular/non-symlinkと全量hashを検証するため、operatorが別copyを作らない。

importerは各inputを一度だけ全量readし、regular/non-symlink、input manifest SHA、task/head/role/schema、4 reviewer/run IDの相互差、finding参照path/rangeを検証してから次へO_EXCL copyする。既存canonical pathはinput hashとreceiptが同一の場合だけverifyし、部分import後のack喪失は既存prefixを再copyせず残りだけimportする。外部source pathをcanonical evidenceへ参照として残さず、import receiptへsource SHA-256、canonical SHA-256、input manifest SHAを固定する。

- `.pipeline/evidence/$TASK/tribunal/finder.json`
- `.pipeline/evidence/$TASK/tribunal/adversarial.json`
- `.pipeline/evidence/$TASK/tribunal/judge.json`
- `.pipeline/evidence/$TASK/tribunal/synthesis.json`
- `.pipeline/evidence/$TASK/tribunal-report.md`

各review JSON schema:

```json
{
  "schema_version": "1.0",
  "task_id": "<release-task-id>",
  "head_sha": "<RELEASE_HEAD_SHA>",
  "role": "finder|adversarial|judge",
  "reviewer_id": "<independent-id>",
  "run_id": "<independent-run-id>",
  "verdict": "pass|block",
  "findings": [
    {
      "id": "TRI-001",
      "severity": "critical|high|medium|low",
      "file": "repo/relative/path",
      "line": 1,
      "evidence": "再現可能な根拠",
      "action": "must_fix|accept_risk"
    }
  ]
}
```

`synthesis.json`は独立synthesizer自身のreviewer/run ID、3 source reviewer ID/run ID、input manifest SHA、全findingの採否理由、`blocking_findings`配列、`closure_actions`配列、`verdict=pass|block`を持つ。critical/highまたは`must_fix`は必ずblocking。medium/lowのaccept riskは具体的残余リスクとownerを持つ。次がexit 0になるまで進まない。

```bash
set -Eeuo pipefail
CURRENT_CLOSURE_STAGE=tribunal
"$RF_ENV_ROOT/backend/bin/python" \
  scripts/test/verify-refactor-closure-artifacts.py \
  --task "$TASK" --head "$FINAL_HEAD" --stage tribunal
TRIBUNAL_VERDICT="$(
  "$RF_ENV_ROOT/backend/bin/python" -I -S -c \
    'import json,sys; print(json.load(open(sys.argv[1]))["verdict"])' \
    ".pipeline/evidence/$TASK/tribunal/synthesis.json"
)"
if [[ "$TRIBUNAL_VERDICT" == "block" ]]; then
  set +e
  "$RF_ENV_ROOT/backend/bin/python" scripts/test/finalize-refactor-blocked-review.py \
    --task "$TASK" --head "$FINAL_HEAD" --source tribunal
  rc=$?
  set -e
  test "$rc" -eq 3
  exit 3
fi
test "$TRIBUNAL_VERDICT" = "pass"
```

### 9.5 Fable、Codex ultra、dual post review

operator commandは9.0へ`CLOSURE_STAGE=post-review,CLOSURE_ACTION=run`を渡す。

外部へrepo由来artifactを送るため、事前承認済みproviderだけを使う。実装前承認が取り消された、loginがない、送信範囲を拡大する必要がある場合は停止する。

wrapper内部algorithm（直接実行禁止）:

```bash
set -Eeuo pipefail
test -n "${TASK:-}"
test -n "${STAGE_ATTEMPT_ID:-}"
CURRENT_CLOSURE_STAGE=post-review
POST_INPUT=".pipeline/evidence/$TASK/external-inputs/post-review/$STAGE_ATTEMPT_ID/input-manifest.json"
FABLE_SUMMARY=".pipeline/evidence/$TASK/external-consultation/consultation-post-summary.json"
CODEX_SUMMARY=".pipeline/evidence/$TASK/codex-review/review-post-summary.json"
DUAL_SUMMARY=".pipeline/evidence/$TASK/dual-review/consensus-post-summary.json"
"$RF_ENV_ROOT/backend/bin/python" scripts/test/verify-refactor-closure-artifacts.py \
  --task "$TASK" --head "$FINAL_HEAD" --stage post-review-input \
  --input-manifest "$POST_INPUT"

run_external_suffix() {
  local provider="$1"
  shift
  set +e
  "$@"
  local rc=$?
  set -e
  if [[ "$rc" -eq 2 ]]; then
    trap - ERR
    "$RF_ENV_ROOT/backend/bin/python" -I -S \
      scripts/test/refactor-closure-attempt.py waiting-external \
        --control-root "$CONTROL_ROOT" --task "$TASK" \
        --release "$RELEASE_ID" --head "$FINAL_HEAD" \
        --parent-attempt "$CLOSURE_ATTEMPT_ID" \
        --stage-attempt "$STAGE_ATTEMPT_ID" --stage post-review \
        --provider "$provider" --input-manifest "$POST_INPUT"
    exit 2
  fi
  return "$rc"
}

if [[ -f "$FABLE_SUMMARY" ]]; then
  bash .claude/hooks/external-consultation-validate.sh "$TASK"
else
  run_external_suffix fable \
    bash scripts/harness/external-consultation.sh run "$TASK" \
      --mode post --source ".pipeline/evidence/$TASK/evidence-pack.md"
  bash .claude/hooks/external-consultation-validate.sh "$TASK"
fi
if [[ -f "$CODEX_SUMMARY" ]]; then
  bash .claude/hooks/codex-review-validate.sh "$TASK"
else
  run_external_suffix codex \
    bash scripts/harness/codex-review.sh run "$TASK" \
      --mode post --source ".pipeline/evidence/$TASK/evidence-pack.md"
  bash .claude/hooks/codex-review-validate.sh "$TASK"
fi
if [[ -f "$DUAL_SUMMARY" ]]; then
  bash .claude/hooks/dual-review-validate.sh "$TASK"
else
  bash scripts/harness/dual-review.sh run "$TASK" --stage post
  bash .claude/hooks/dual-review-validate.sh "$TASK"
fi
```

Fable/Codexのopen MUST_FIX、dualの`MUST_FIX|ESCALATE`、tribunalのblocking/closure actionが1件でもあればsource、static plan、matrix、既存RF commitを変更せず、次を実行する。旧manifest/E2E/reviewを別taskの合格証拠へ流用しない。

```bash
set -Eeuo pipefail
if ! "$RF_ENV_ROOT/backend/bin/python" scripts/test/verify-refactor-closure-artifacts.py \
  --task "$TASK" --head "$FINAL_HEAD" --stage post-review; then
  set +e
  "$RF_ENV_ROOT/backend/bin/python" scripts/test/finalize-refactor-blocked-review.py \
    --task "$TASK" --head "$FINAL_HEAD" --source post-review
  rc=$?
  set -e
  test "$rc" -eq 3
  exit 3
fi
read -r FABLE_SHA256 CODEX_SHA256 DUAL_SHA256 < <(
  "$RF_ENV_ROOT/backend/bin/python" -I -S - \
    "$FABLE_SUMMARY" "$CODEX_SUMMARY" "$DUAL_SUMMARY" <<'PY'
import hashlib, pathlib, sys
print(*(hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() for p in sys.argv[1:]))
PY
)
POST_REVIEW_COMPLETION="$CONTROL_ROOT/.pipeline/closure-stage-attempts/$TASK/post-review/$STAGE_ATTEMPT_ID/completion.json"
POST_REVIEW_OUTPUTS=(
  --output-sha256 "fable=$FABLE_SHA256"
  --output-sha256 "codex=$CODEX_SHA256"
  --output-sha256 "dual=$DUAL_SHA256"
)
if [[ -f "$POST_REVIEW_COMPLETION" ]]; then
  "$RF_ENV_ROOT/backend/bin/python" -I -S scripts/test/refactor-closure-attempt.py \
    verify-stage --control-root "$CONTROL_ROOT" --task "$TASK" \
    --release "$RELEASE_ID" --head "$FINAL_HEAD" \
    --parent-attempt "$CLOSURE_ATTEMPT_ID" --stage-attempt "$STAGE_ATTEMPT_ID" \
    --stage post-review --expect-complete "${POST_REVIEW_OUTPUTS[@]}"
else
  "$RF_ENV_ROOT/backend/bin/python" -I -S scripts/test/refactor-closure-attempt.py \
    complete-stage --control-root "$CONTROL_ROOT" --task "$TASK" \
    --release "$RELEASE_ID" --head "$FINAL_HEAD" \
    --parent-attempt "$CLOSURE_ATTEMPT_ID" --stage-attempt "$STAGE_ATTEMPT_ID" \
    --stage post-review "${POST_REVIEW_OUTPUTS[@]}"
fi
```

### 9.6 独立QA、session ledger、outcome

operator command 1/2は9.0へ`CLOSURE_STAGE=approval-target,CLOSURE_ACTION=prepare-external`を渡し、QA input manifest固定後の`waiting_external`, exit 2を期待する。独立QAはrepo/control/archive外へJSONを出力する。command 2/2は`QA_JUDGMENT=<absolute regular file>`を設定したfresh processの9.0へ`CLOSURE_STAGE=approval-target,CLOSURE_ACTION=import-external`を渡し、QA import、feedback/session/outcome/target/state suffixを完了する。

独立QAはimplementerとtribunal 3 reviewerのいずれとも異なる`reviewer_id`で、次のexact schemaを`.pipeline/evidence/$TASK/qa-judgment.json`へ書く。

```json
{
  "schema_version": "1.0",
  "task_id": "<release-task-id>",
  "head_sha": "<RELEASE_HEAD_SHA>",
  "reviewer_id": "<independent-qa-id>",
  "run_id": "<independent-qa-run-id>",
  "independent": true,
  "input_manifest_sha256": "<64hex>",
  "verdict": "pass|block",
  "reviewed_artifacts": [
    ".pipeline/evidence/<release-task-id>/evidence-manifest.json",
    ".pipeline/evidence/<release-task-id>/evidence-pack.md",
    ".pipeline/evidence/<release-task-id>/release-manifest.json",
    ".pipeline/evidence/<release-task-id>/tribunal/synthesis.json",
    ".pipeline/evidence/<release-task-id>/dual-review/consensus-post-summary.json"
  ],
  "blocking_findings": [],
  "residual_risks": []
}
```

R7だけ`reviewed_artifacts`へ`.pipeline/evidence/<release-task-id>/final-ui.json`を追加する。`prepare-external`は`.pipeline/evidence/$TASK/external-inputs/qa/$STAGE_ATTEMPT_ID/input-manifest.json`をdestination不存在時だけ作り、task/head、tribunal/post-review/manifest/pack/release manifestのpath/hash、許可QA schema、implementerと既存全reviewer ID/run IDを固定する。`import-external`は9.0がsecure-copyしたQA JSONを一度だけparseし、input manifest SHA、task/head、reviewed artifact集合/hash、`independent=true`を照合し、QA reviewer/run IDがimplementer、finder、adversarial、judge、synthesizer、post Codex reviewerの全ID/run IDと異なる場合だけ`.pipeline/evidence/$TASK/qa-judgment.json`と`external-inputs/qa/$STAGE_ATTEMPT_ID/import-receipt.json`へO_EXCL importする。既存prefixはsource/canonical/receipt hash一致ならverify-only、部分importのack喪失は残りsuffixだけを作る。`verdict=block`または`blocking_findings`非空なら次を実行してexit 3で終了する。

```bash
set -Eeuo pipefail
QA_INPUT=".pipeline/evidence/$TASK/external-inputs/qa/$STAGE_ATTEMPT_ID/input-manifest.json"
QA_JUDGMENT_CANONICAL=".pipeline/evidence/$TASK/qa-judgment.json"
QA_IMPORT_RECEIPT=".pipeline/evidence/$TASK/external-inputs/qa/$STAGE_ATTEMPT_ID/import-receipt.json"
test -f "$QA_INPUT" -a -f "$QA_JUDGMENT_CANONICAL" -a -f "$QA_IMPORT_RECEIPT"
"$RF_ENV_ROOT/backend/bin/python" scripts/test/verify-refactor-closure-artifacts.py \
  --task "$TASK" --head "$FINAL_HEAD" --stage qa-import \
  --input-manifest "$QA_INPUT" --import-receipt "$QA_IMPORT_RECEIPT"
CURRENT_CLOSURE_STAGE=independent-qa
QA_VERDICT="$(
  "$RF_ENV_ROOT/backend/bin/python" -I -S -c \
    'import json,sys; d=json.load(open(sys.argv[1])); print(d["verdict"])' \
    ".pipeline/evidence/$TASK/qa-judgment.json"
)"
if [[ "$QA_VERDICT" == "block" ]]; then
  set +e
  "$RF_ENV_ROOT/backend/bin/python" scripts/test/finalize-refactor-blocked-review.py \
    --task "$TASK" --head "$FINAL_HEAD" --source qa
  rc=$?
  set -e
  test "$rc" -eq 3
  exit 3
fi
test "$QA_VERDICT" = "pass"
```

`.pipeline/outcomes/$TASK/outcome-card.json`を次のschemaで作る。全booleanは証拠がある場合だけtrueにし、pathを推測しない。

```json
{
  "schema_version": "1.0",
  "task_id": "<release-task-id>",
  "head_sha": "<RELEASE_HEAD_SHA>",
  "runtime_profile": "codex-cli",
  "size": "L",
  "result": {
    "at_pass": true,
    "fp_pass": true,
    "nft_pass": true,
    "hd_resolved": true,
    "blocking_findings": 0
  },
  "evidence": {
    "session_ledger": ".pipeline/sessions/<release-task-id>/events.jsonl",
    "verification": [
      ".pipeline/evidence/<release-task-id>/evidence-manifest.json",
      ".pipeline/evidence/<release-task-id>/evidence-pack.md",
      ".pipeline/evidence/<release-task-id>/release-manifest.json",
      ".pipeline/evidence/<release-task-id>/qa-judgment.json"
    ],
    "sidechain_synthesis": ".pipeline/evidence/<release-task-id>/tribunal/synthesis.json",
    "tribunal_report": ".pipeline/evidence/<release-task-id>/tribunal-report.md",
    "review": [
      ".pipeline/evidence/<release-task-id>/external-consultation/consultation-post-summary.json",
      ".pipeline/evidence/<release-task-id>/codex-review/review-post-summary.json",
      ".pipeline/evidence/<release-task-id>/dual-review/consensus-post-summary.json"
    ]
  },
  "notes": []
}
```

```bash
set -Eeuo pipefail
test -n "${TASK:-}"
test -n "${FINAL_HEAD:-}"
test -n "${CLOSURE_ATTEMPT_ID:-}"
test -n "${STAGE_ATTEMPT_ID:-}"
CURRENT_CLOSURE_STAGE=preapproval-finalization
"$RF_ENV_ROOT/backend/bin/python" \
  scripts/test/verify-refactor-closure-artifacts.py \
  --task "$TASK" --head "$FINAL_HEAD" --stage pre-approval

FEEDBACK_PRUNE=".pipeline/evidence/$TASK/feedback-prune.json"
if [[ -f "$FEEDBACK_PRUNE" ]]; then
  "$RF_ENV_ROOT/backend/bin/python" scripts/test/verify-refactor-closure-artifacts.py \
    --task "$TASK" --head "$FINAL_HEAD" --stage feedback-prune
else
  PATH="$RF_ENV_ROOT/backend/bin:$PATH" \
  HARNESS_TASK_ID="$TASK" \
  HARNESS_FEEDBACK_REQUIRED=1 \
  bash .claude/hooks/feedback-prune.sh \
    --required \
    --ledger .pipeline/feedback/ledger.jsonl \
    --output "$FEEDBACK_PRUNE"
  "$RF_ENV_ROOT/backend/bin/python" scripts/test/verify-refactor-closure-artifacts.py \
    --task "$TASK" --head "$FINAL_HEAD" --stage feedback-prune
fi

SESSION_EVENT_KEY="$(
  "$RF_ENV_ROOT/backend/bin/python" -I -S - \
    "$TASK" "$FINAL_HEAD" "$CLOSURE_ATTEMPT_ID" "$STAGE_ATTEMPT_ID" <<'PY'
import hashlib, sys
print(hashlib.sha256("\0".join([*sys.argv[1:], "preapproval-success"]).encode()).hexdigest())
PY
)"
bash scripts/harness/codex-session-ledger.sh record-once "$TASK" \
  --idempotency-key "$SESSION_EVENT_KEY" \
  --profile codex-cli --status succeeded \
  --summary "release $RELEASE_ID verification, reviews and independent QA passed at $FINAL_HEAD"
bash scripts/harness/codex-session-ledger.sh verify-event "$TASK" \
  --idempotency-key "$SESSION_EVENT_KEY" \
  --profile codex-cli --status succeeded \
  --summary "release $RELEASE_ID verification, reviews and independent QA passed at $FINAL_HEAD"

OUTCOME_CARD=".pipeline/outcomes/$TASK/outcome-card.json"
if [[ -f "$OUTCOME_CARD" ]]; then
  "$RF_ENV_ROOT/backend/bin/python" -I -S \
    scripts/test/write-refactor-outcome-card.py --verify-existing \
      --task "$TASK" --head "$FINAL_HEAD" --attempt-id "$CLOSURE_ATTEMPT_ID"
else
  "$RF_ENV_ROOT/backend/bin/python" -I -S \
    scripts/test/write-refactor-outcome-card.py \
      --task "$TASK" --head "$FINAL_HEAD" --attempt-id "$CLOSURE_ATTEMPT_ID"
fi
"$RF_ENV_ROOT/backend/bin/python" -I -S \
  scripts/test/write-refactor-outcome-card.py \
  --verify-existing \
  --task "$TASK" \
  --head "$FINAL_HEAD" \
  --attempt-id "$CLOSURE_ATTEMPT_ID"
bash scripts/harness/outcome-judge.sh "$TASK"
CURRENT_STATE="$(
  "$RF_ENV_ROOT/backend/bin/python" -I -S -c \
    'import json,sys; print(json.load(open(sys.argv[1]))["state"])' \
    ".pipeline/plans/$TASK/checkpoint-contract.json"
)"
case "$CURRENT_STATE" in
  verifying|verified|evidence_ready|awaiting_approval) ;;
  *) exit 2 ;;
esac
APPROVAL_TARGET=".pipeline/evidence/$TASK/approval-target.json"
if [[ -f "$APPROVAL_TARGET" ]]; then
  "$RF_ENV_ROOT/backend/bin/python" \
    scripts/test/freeze-refactor-approval-target.py \
    --task "$TASK" --head "$FINAL_HEAD" --verify-only \
    --expect-state-prefix "$CURRENT_STATE"
else
  test "$CURRENT_STATE" = "verifying"
  "$RF_ENV_ROOT/backend/bin/python" \
    scripts/test/freeze-refactor-approval-target.py \
    --task "$TASK" --head "$FINAL_HEAD"
fi
"$RF_ENV_ROOT/backend/bin/python" scripts/test/freeze-refactor-approval-target.py \
  --task "$TASK" --head "$FINAL_HEAD" --verify-only \
  --expect-state-prefix "$CURRENT_STATE"
if [[ "$CURRENT_STATE" == "verifying" ]]; then
  bash scripts/harness/backcast-state.sh "$TASK" verified \
    --reason "verification, reviews, QA and outcome passed"
  CURRENT_STATE=verified
fi
"$RF_ENV_ROOT/backend/bin/python" scripts/test/freeze-refactor-approval-target.py \
  --task "$TASK" --head "$FINAL_HEAD" --verify-only \
  --expect-state-prefix "$CURRENT_STATE"
if [[ "$CURRENT_STATE" == "verified" ]]; then
  bash scripts/harness/backcast-state.sh "$TASK" evidence_ready \
    --reason "hash-bound evidence pack is complete"
  CURRENT_STATE=evidence_ready
fi
"$RF_ENV_ROOT/backend/bin/python" scripts/test/freeze-refactor-approval-target.py \
  --task "$TASK" --head "$FINAL_HEAD" --verify-only \
  --expect-state-prefix "$CURRENT_STATE"
if [[ "$CURRENT_STATE" == "evidence_ready" ]]; then
  bash scripts/harness/backcast-state.sh "$TASK" awaiting_approval \
    --reason "human approval is required before PR readiness"
  CURRENT_STATE=awaiting_approval
fi
"$RF_ENV_ROOT/backend/bin/python" scripts/test/freeze-refactor-approval-target.py \
  --task "$TASK" --head "$FINAL_HEAD" --verify-only \
  --expect-state-prefix awaiting_approval

read -r QA_SHA256 FEEDBACK_SHA256 OUTCOME_SHA256 TARGET_SHA256 < <(
  "$RF_ENV_ROOT/backend/bin/python" -I -S - \
    "$QA_JUDGMENT_CANONICAL" "$FEEDBACK_PRUNE" "$OUTCOME_CARD" "$APPROVAL_TARGET" <<'PY'
import hashlib, pathlib, sys
print(*(hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() for p in sys.argv[1:]))
PY
)
APPROVAL_TARGET_COMPLETION="$CONTROL_ROOT/.pipeline/closure-stage-attempts/$TASK/approval-target/$STAGE_ATTEMPT_ID/completion.json"
APPROVAL_TARGET_OUTPUTS=(
  --output-sha256 "qa=$QA_SHA256"
  --output-sha256 "feedback-prune=$FEEDBACK_SHA256"
  --output-sha256 "outcome=$OUTCOME_SHA256"
  --output-sha256 "approval-target=$TARGET_SHA256"
  --output-sha256 "session-event-key=$SESSION_EVENT_KEY"
)
if [[ -f "$APPROVAL_TARGET_COMPLETION" ]]; then
  "$RF_ENV_ROOT/backend/bin/python" -I -S scripts/test/refactor-closure-attempt.py \
    verify-stage --control-root "$CONTROL_ROOT" --task "$TASK" \
    --release "$RELEASE_ID" --head "$FINAL_HEAD" \
    --parent-attempt "$CLOSURE_ATTEMPT_ID" --stage-attempt "$STAGE_ATTEMPT_ID" \
    --stage approval-target --expect-complete "${APPROVAL_TARGET_OUTPUTS[@]}"
else
  "$RF_ENV_ROOT/backend/bin/python" -I -S scripts/test/refactor-closure-attempt.py \
    complete-stage --control-root "$CONTROL_ROOT" --task "$TASK" \
    --release "$RELEASE_ID" --head "$FINAL_HEAD" \
    --parent-attempt "$CLOSURE_ATTEMPT_ID" --stage-attempt "$STAGE_ATTEMPT_ID" \
    --stage approval-target "${APPROVAL_TARGET_OUTPUTS[@]}"
fi
```

ここで必ず停止し、人間へ`RELEASE_HEAD_SHA`、release base SHA、evidence pack、tribunal、残余リスク、`approval-target.json`とそのSHA-256を提示する。AI自身をapproverにしてはいけない。提示後はtargetが列挙したfileとtarget自身を変更・再生成しない。target作成前の実証failureだけを`verifying -> verification_failed -> blocked`へ進める。target作成後のstate transition ack喪失は新findingではないためblocked/evidence_missingへ誤遷移させず、同stage attemptで上記prefix検証後に未完suffixだけを再開する。

### 9.7 人間承認後だけ実行するclosure

明示的な承認を受けた後だけ実行する独立stageである。前節のshell state/cwdを引き継がない。入力は`RELEASE_ID`、absolute `RELEASE_WORKTREE`、`HUMAN_APPROVER`、`HUMAN_APPROVED_TARGET_SHA256`だけとし、task/base/head/attempt/control rootはimmutable artifactから再導出する。

operator commandは人間承認値を受け取った後だけ、`HUMAN_APPROVER,HUMAN_APPROVED_TARGET_SHA256`を設定したfresh processの9.0へ`CLOSURE_STAGE=approval,CLOSURE_ACTION=run`を渡す。

wrapper内部algorithm（直接実行禁止）:

```bash
set -Eeuo pipefail
export MASTER_TASK=full-repo-refactoring-2026-07-24
test -n "${RELEASE_ID:-}"
case "$RELEASE_ID" in r1|r2|r3|r4|r5|r6|r7) ;; *) exit 2 ;; esac
test -n "${RELEASE_WORKTREE:-}"
case "$RELEASE_WORKTREE" in /*) ;; *) exit 2 ;; esac
export REPO_ROOT="$(
  python3 -I -S -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve(strict=True))' \
    "$RELEASE_WORKTREE"
)"
cd "$REPO_ROOT"
test "$(git rev-parse --show-toplevel)" = "$REPO_ROOT"
export TASK="${MASTER_TASK}-${RELEASE_ID}"
export RF_ENV_ROOT="$REPO_ROOT/.pipeline/tmp/$TASK/env"
CONTROL_ROOT="$(
  python3 -I -S - "$TASK" "$RELEASE_ID" <<'PY'
import json, pathlib, sys
task, release = sys.argv[1:]
name = "baseline.json" if release == "r1" else "bootstrap.json"
d = json.loads((pathlib.Path(".pipeline/evidence") / task / name).read_text())
print(pathlib.Path(d.get("control_root_realpath") or d["control_root"]).resolve(strict=True))
PY
)"
export CONTROL_ROOT
test -x "$RF_ENV_ROOT/backend/bin/python"
test -f ".pipeline/evidence/$TASK/evidence-manifest.json"
read -r IMPLEMENTATION_BASE_SHA FINAL_HEAD CLOSURE_ATTEMPT_ID < <(
  "$RF_ENV_ROOT/backend/bin/python" -I -S - "$TASK" <<'PY'
import json, pathlib, sys
task = sys.argv[1]
root = pathlib.Path(".pipeline/evidence") / task
d = json.loads((root / "evidence-manifest.json").read_text())
r = json.loads((root / "release-manifest.json").read_text())
a = json.loads((root / "approval-target.json").read_text())
assert d["task_id"] == task == r["task_id"] == a["task_id"]
assert d["repo"]["base_sha"] == r["release_base_sha"]
assert d["repo"]["head_sha"] == r["release_head_sha"] == a["head_sha"]
assert r["closure_attempt_id"] == a["closure_attempt_id"]
print(d["repo"]["base_sha"], d["repo"]["head_sha"], r["closure_attempt_id"])
PY
)
test -n "${STAGE_ATTEMPT_ID:-}"
POSTAPPROVAL_ATTEMPT_ID="$STAGE_ATTEMPT_ID"
env -i VALUE="$POSTAPPROVAL_ATTEMPT_ID" PATH=/usr/bin:/bin \
  python3 -I -S -c \
  'import os,re; assert re.fullmatch(r"approval-[0-9a-f]{32}", os.environ["VALUE"])'
"$RF_ENV_ROOT/backend/bin/python" -I -S \
  scripts/test/refactor-closure-attempt.py verify-stage \
    --control-root "$CONTROL_ROOT" --task "$TASK" \
    --release "$RELEASE_ID" --head "$FINAL_HEAD" \
    --parent-attempt "$CLOSURE_ATTEMPT_ID" \
    --stage-attempt "$POSTAPPROVAL_ATTEMPT_ID" --stage approval
CURRENT_CLOSURE_STAGE=fresh-base-and-target
reject_approval_input() {
  local field="$1"
  local value="$2"
  local value_sha256
  trap - ERR
  value_sha256="$(
    env -i VALUE="$value" PATH=/usr/bin:/bin \
      python3 -I -S -c \
      'import hashlib,os; print(hashlib.sha256(os.environ["VALUE"].encode()).hexdigest())'
  )"
  "$RF_ENV_ROOT/backend/bin/python" -I -S \
    scripts/test/refactor-closure-attempt.py reject-input \
      --control-root "$CONTROL_ROOT" --task "$TASK" \
      --release "$RELEASE_ID" --head "$FINAL_HEAD" \
      --parent-attempt "$CLOSURE_ATTEMPT_ID" \
      --stage-attempt "$POSTAPPROVAL_ATTEMPT_ID" --stage approval \
      --field "$field" --value-sha256 "$value_sha256"
  echo "caller approval input '$field' differs; state/target/decision/selection unchanged" >&2
  exit 2
}
test -n "${HUMAN_APPROVER:-}"
test "$(git rev-parse HEAD)" = "$FINAL_HEAD"
APPROVAL_ENTRY_STATE="$(
  "$RF_ENV_ROOT/backend/bin/python" -I -S -c \
    'import json,sys; print(json.load(open(sys.argv[1]))["state"])' \
    ".pipeline/plans/$TASK/checkpoint-contract.json"
)"
case "$APPROVAL_ENTRY_STATE" in awaiting_approval|approved|merged|completed) ;; *) exit 2 ;; esac
git merge-base --is-ancestor "$IMPLEMENTATION_BASE_SHA" "$FINAL_HEAD"
APPROVAL_TARGET=".pipeline/evidence/$TASK/approval-target.json"
test -f "$APPROVAL_TARGET"
APPROVAL_TARGET_SHA256="$(
  python3 -I -S - "$APPROVAL_TARGET" <<'PY'
import hashlib, pathlib, sys
print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)"
test -n "${HUMAN_APPROVED_TARGET_SHA256:-}"
if [[ "$HUMAN_APPROVED_TARGET_SHA256" != "$APPROVAL_TARGET_SHA256" ]]; then
  reject_approval_input human-approved-target-sha256 "$HUMAN_APPROVED_TARGET_SHA256"
fi

if ! "$RF_ENV_ROOT/backend/bin/python" \
  scripts/test/freeze-refactor-approval-target.py \
  --task "$TASK" --head "$FINAL_HEAD" --verify-only; then
  if [[ "$APPROVAL_ENTRY_STATE" == "awaiting_approval" ]]; then
    trap - ERR
    bash scripts/harness/backcast-state.sh "$TASK" expired_approval \
      --reason "immutable approval target or source changed before approved state"
    exit 3
  fi
  echo "approved target/source verification failed after state=approved; keep approved immutable and fail" >&2
  false
fi

APPROVAL_DECISION=".pipeline/approvals/$TASK/approval-decision.json"
PREARCHIVE_RECEIPT="$CONTROL_ROOT/.pipeline/closure-stage-attempts/$TASK/approval/$POSTAPPROVAL_ATTEMPT_ID/prearchive-ready.json"
PREMERGE_MANIFEST="$CONTROL_ROOT/.pipeline/release-archives/$TASK/premerge/archive-manifest.json"
APPROVAL_COMPLETION="$CONTROL_ROOT/.pipeline/closure-stage-attempts/$TASK/approval/$POSTAPPROVAL_ATTEMPT_ID/completion.json"
if [[ -f "$APPROVAL_DECISION" ]]; then
  CANONICAL_APPROVER="$(
    "$RF_ENV_ROOT/backend/bin/python" -I -S - "$APPROVAL_DECISION" <<'PY'
import json, pathlib, sys
d = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert d["status"] == "approved" and d["role"] == "client_owner"
print(d["approver"])
PY
  )"
  if [[ "$HUMAN_APPROVER" != "$CANONICAL_APPROVER" ]]; then
    reject_approval_input human-approver "$HUMAN_APPROVER"
  fi
fi

if [[ -f "$APPROVAL_COMPLETION" ]]; then
  test -f "$APPROVAL_DECISION" -a -f "$PREARCHIVE_RECEIPT" -a -f "$PREMERGE_MANIFEST"
  bash scripts/harness/backcast-approval.sh "$TASK" approved \
    --verify-existing \
    --approver "$HUMAN_APPROVER" --role client_owner \
    --immutable-target "$APPROVAL_TARGET" \
    --target-sha256 "$HUMAN_APPROVED_TARGET_SHA256"
  "$RF_ENV_ROOT/backend/bin/python" -I -S \
    scripts/test/refactor-closure-attempt.py verify-checkpoint \
      --control-root "$CONTROL_ROOT" --task "$TASK" \
      --release "$RELEASE_ID" --head "$FINAL_HEAD" \
      --parent-attempt "$CLOSURE_ATTEMPT_ID" \
      --stage-attempt "$POSTAPPROVAL_ATTEMPT_ID" --stage approval \
      --checkpoint prearchive-ready \
      --approval-target "$APPROVAL_TARGET" \
      --approval-decision "$APPROVAL_DECISION" \
      --pr-ready ".pipeline/gates/$TASK/pr-ready.json"
  "$RF_ENV_ROOT/backend/bin/python" scripts/test/archive-refactor-release.py \
    --verify-existing --source "$REPO_ROOT" --control-root "$CONTROL_ROOT" \
    --task "$TASK" --head "$FINAL_HEAD" \
    --approval-stage-attempt "$POSTAPPROVAL_ATTEMPT_ID" \
    --prearchive-ready "$PREARCHIVE_RECEIPT"
  PREMERGE_MANIFEST_SHA256="$(
    "$RF_ENV_ROOT/backend/bin/python" -I -S - "$PREMERGE_MANIFEST" <<'PY'
import hashlib, pathlib, sys
print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())
PY
  )"
  "$RF_ENV_ROOT/backend/bin/python" -I -S \
    scripts/test/refactor-closure-attempt.py verify-stage \
      --control-root "$CONTROL_ROOT" --task "$TASK" \
      --release "$RELEASE_ID" --head "$FINAL_HEAD" \
      --parent-attempt "$CLOSURE_ATTEMPT_ID" \
      --stage-attempt "$POSTAPPROVAL_ATTEMPT_ID" --stage approval \
      --expect-complete --archive-manifest-sha256 "$PREMERGE_MANIFEST_SHA256"
  exit 0
fi

case "$APPROVAL_ENTRY_STATE" in awaiting_approval|approved) ;; *) exit 2 ;; esac
if [[ ! -f "$APPROVAL_DECISION" ]]; then
  git fetch origin main
  if [[ "$(git rev-parse origin/main)" != "$IMPLEMENTATION_BASE_SHA" ]]; then
    trap - ERR
    bash scripts/harness/backcast-state.sh "$TASK" expired_approval \
      --reason "origin/main moved before approval decision"
    exit 3
  fi
  bash scripts/harness/backcast-approval.sh "$TASK" approved \
    --approver "$HUMAN_APPROVER" --role client_owner \
    --immutable-target "$APPROVAL_TARGET" \
    --target-sha256 "$HUMAN_APPROVED_TARGET_SHA256"
else
  bash scripts/harness/backcast-approval.sh "$TASK" approved \
    --verify-existing \
    --approver "$HUMAN_APPROVER" --role client_owner \
    --immutable-target "$APPROVAL_TARGET" \
    --target-sha256 "$HUMAN_APPROVED_TARGET_SHA256"
fi

CURRENT_CLOSURE_STAGE=approval-validators
bash .claude/hooks/approval-hash-check.sh "$TASK"
bash .claude/hooks/backcast-validate.sh "$TASK"
bash .claude/hooks/external-consultation-validate.sh "$TASK"
bash .claude/hooks/codex-review-validate.sh "$TASK"
bash .claude/hooks/dual-review-validate.sh "$TASK"

"$RF_ENV_ROOT/backend/bin/python" \
  scripts/test/verify-refactor-closure-artifacts.py \
  --task "$TASK" --head "$FINAL_HEAD" --stage after-approval
"$RF_ENV_ROOT/backend/bin/python" \
  scripts/test/freeze-refactor-approval-target.py \
  --task "$TASK" --head "$FINAL_HEAD" --verify-only

CURRENT_CLOSURE_STAGE=approved-state
CURRENT_STATE="$(
  "$RF_ENV_ROOT/backend/bin/python" -I -S -c \
    'import json,sys; print(json.load(open(sys.argv[1]))["state"])' \
    ".pipeline/plans/$TASK/checkpoint-contract.json"
)"
if [[ "$CURRENT_STATE" == "awaiting_approval" ]]; then
  bash scripts/harness/backcast-state.sh "$TASK" approved \
    --reason "human decision and immutable validators passed"
else
  test "$CURRENT_STATE" = "approved"
fi
CURRENT_CLOSURE_STAGE=pr-ready
if [[ -f ".pipeline/gates/$TASK/pr-ready.json" ]]; then
  bash .claude/hooks/pr-ready-gate.sh "$TASK" --verify-existing
else
  bash .claude/hooks/pr-ready-gate.sh "$TASK"
fi
"$RF_ENV_ROOT/backend/bin/python" -I -S - "$TASK" <<'PY'
import json, pathlib, sys
d = json.loads((pathlib.Path(".pipeline/plans") / sys.argv[1] / "checkpoint-contract.json").read_text())
assert d["state"] == "approved"
PY
CURRENT_CLOSURE_STAGE=prearchive-ready
if [[ -f "$PREARCHIVE_RECEIPT" ]]; then
  "$RF_ENV_ROOT/backend/bin/python" -I -S \
    scripts/test/refactor-closure-attempt.py verify-checkpoint \
      --control-root "$CONTROL_ROOT" --task "$TASK" \
      --release "$RELEASE_ID" --head "$FINAL_HEAD" \
      --parent-attempt "$CLOSURE_ATTEMPT_ID" \
      --stage-attempt "$POSTAPPROVAL_ATTEMPT_ID" --stage approval \
      --checkpoint prearchive-ready \
      --approval-target "$APPROVAL_TARGET" \
      --approval-decision "$APPROVAL_DECISION" \
      --pr-ready ".pipeline/gates/$TASK/pr-ready.json"
else
  git fetch origin main
  if [[ "$(git rev-parse origin/main)" != "$IMPLEMENTATION_BASE_SHA" ]]; then
    echo "origin/main moved after approval decision; preserve approved state and stop before prearchive" >&2
    false
  fi
  "$RF_ENV_ROOT/backend/bin/python" -I -S \
    scripts/test/refactor-closure-attempt.py checkpoint-stage \
      --control-root "$CONTROL_ROOT" --task "$TASK" \
      --release "$RELEASE_ID" --head "$FINAL_HEAD" \
      --parent-attempt "$CLOSURE_ATTEMPT_ID" \
      --stage-attempt "$POSTAPPROVAL_ATTEMPT_ID" --stage approval \
      --checkpoint prearchive-ready \
      --approval-target "$APPROVAL_TARGET" \
      --approval-decision "$APPROVAL_DECISION" \
      --pr-ready ".pipeline/gates/$TASK/pr-ready.json"
fi
test -f "$PREARCHIVE_RECEIPT"
CURRENT_CLOSURE_STAGE=premerge-archive
if [[ -f "$PREMERGE_MANIFEST" ]]; then
  "$RF_ENV_ROOT/backend/bin/python" scripts/test/archive-refactor-release.py \
    --verify-existing \
    --source "$REPO_ROOT" --control-root "$CONTROL_ROOT" \
    --task "$TASK" --head "$FINAL_HEAD" \
    --approval-stage-attempt "$POSTAPPROVAL_ATTEMPT_ID" \
    --prearchive-ready "$PREARCHIVE_RECEIPT"
else
  git fetch origin main
  if [[ "$(git rev-parse origin/main)" != "$IMPLEMENTATION_BASE_SHA" ]]; then
    echo "origin/main moved after state=approved; keep approved immutable and stop before archive" >&2
    false
  fi
  "$RF_ENV_ROOT/backend/bin/python" scripts/test/archive-refactor-release.py \
    --source "$REPO_ROOT" --control-root "$CONTROL_ROOT" \
    --task "$TASK" --head "$FINAL_HEAD" \
    --approval-stage-attempt "$POSTAPPROVAL_ATTEMPT_ID" \
    --prearchive-ready "$PREARCHIVE_RECEIPT"
fi
test -f "$PREMERGE_MANIFEST"
PREMERGE_MANIFEST_SHA256="$(
  "$RF_ENV_ROOT/backend/bin/python" -I -S - "$PREMERGE_MANIFEST" <<'PY'
import hashlib, pathlib, sys
print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)"
CURRENT_CLOSURE_STAGE=approval-completion
if [[ -f "$APPROVAL_COMPLETION" ]]; then
  "$RF_ENV_ROOT/backend/bin/python" -I -S \
    scripts/test/refactor-closure-attempt.py verify-stage \
      --control-root "$CONTROL_ROOT" --task "$TASK" \
      --release "$RELEASE_ID" --head "$FINAL_HEAD" \
      --parent-attempt "$CLOSURE_ATTEMPT_ID" \
      --stage-attempt "$POSTAPPROVAL_ATTEMPT_ID" --stage approval \
      --expect-complete \
      --archive-manifest-sha256 "$PREMERGE_MANIFEST_SHA256"
else
  "$RF_ENV_ROOT/backend/bin/python" -I -S \
    scripts/test/refactor-closure-attempt.py complete-stage \
      --control-root "$CONTROL_ROOT" --task "$TASK" \
      --release "$RELEASE_ID" --head "$FINAL_HEAD" \
      --parent-attempt "$CLOSURE_ATTEMPT_ID" \
      --stage-attempt "$POSTAPPROVAL_ATTEMPT_ID" --stage approval \
      --prearchive-ready "$PREARCHIVE_RECEIPT" \
      --archive-manifest "$PREMERGE_MANIFEST" \
      --archive-manifest-sha256 "$PREMERGE_MANIFEST_SHA256"
fi
"$RF_ENV_ROOT/backend/bin/python" -I -S \
  scripts/test/refactor-closure-attempt.py verify-stage \
    --control-root "$CONTROL_ROOT" --task "$TASK" \
    --release "$RELEASE_ID" --head "$FINAL_HEAD" \
    --parent-attempt "$CLOSURE_ATTEMPT_ID" \
    --stage-attempt "$POSTAPPROVAL_ATTEMPT_ID" --stage approval \
    --expect-complete \
    --archive-manifest-sha256 "$PREMERGE_MANIFEST_SHA256"
```

最終期待結果:

- implementation treeのstatusは空。`.pipeline`はtask-owned evidence/gates/outcome/session/approval/currentの生成差分を許すため、raw `git status --short`空を要求しない。
- static `plan.md`、`verification-contract.md`、review target hash、`FINAL_HEAD`に未承認変更0。
- control checkoutのpremerge archiveは新規一度だけ作成され、manifestの全relative path/hashがsourceと一致。archive内prearchive-ready receiptはapproval target/decision、approved state/event、PR-ready、同じstage attemptへbindする。archive外のfinal approval completion receiptはarchive manifest SHA-256とprearchive-ready receipt SHA-256へbindし、sourceと既存ユーザー成果物は不変。
- commit一覧は各RF ID 1件ずつ。失敗itemを打ち消すrevert commit 0。
- public API/schema/status/既存DB metadataの予定外差0。RedisはRF-06C1/RF-06C2/RF-09A/RF-09BのACL・broker移行とRF-24の内部原子化、RF-09A/RF-09Bのsession protocol以外に予定外差0。
- raw provider/client secretを持てる実行時例外は、(1) RF-05Cのsubject-owned Agent workloadへ渡す`VEXA_BOT_API_TOKEN`、(2) RF-06Eでsubject解決済みのAgent containerへ渡す`ANTHROPIC_API_KEY`、(3) RF-06F/RF-06GのZoom Botだけが持つ有効期限2時間以下のSDK JWT、の3契約だけ。いずれも対象subject/profile/workload/audienceへexact bindし、response、URL、log、Redis、job、evidence、別subject/profile/workloadではraw値0。これ以外のprovider/client secretを生成workloadへ配る件数0。`V-SECURITY-CONTRACT`と該当item test reportが例外名・配布先・TTL・zero-leak assertionをmachine-readableに列挙し、未列挙例外はfailとする。

### 9.8 条件付きmerge、operator gate、R7 aggregator

9.7がpassし、premerge archiveとstate=approved、PR-ready gate、prearchive-ready receipt、archive外final approval-stage completion receiptが揃ったreleaseだけを対象にする。final completionの`archive_manifest_sha256`は現在のarchive manifest SHA-256とexact一致しなければならず、9.8の最初のstageと全resumeはcontrol-root canonical completion receiptを独立anchorとして先に検証する。ここからの各code blockは別sessionで実行できるstandalone stageであり、前blockのcwd、変数、Python、task/gate IDを引き継がない。全blockの共通入力は`RELEASE_ID`とabsolute `ARCHIVE_ROOT="$CONTROL_ROOT/.pipeline/release-archives"`だけで、release worktreeは不要である。archive内repo-relative memberは`$ARCHIVE_ROOT/$TASK/premerge/files/<repo-relative-path>`へ固定する。各blockはapproval target/decision/archive manifest/final completionをclean environmentのtrusted launcherで照合し、そのapproved release HEADのGit objectを全量抽出・hash検証してからrunnerを実行する。caller worktreeやarchive内script、stream途中のblobを最初の実行sourceにしない。

#### 9.8.0 全stage共通のtrusted bootstrap（唯一のoperator entry）

9.8.1〜9.8.6は全て次のblockを**別processで最初から**実行する。`HUMAN_APPROVED_TARGET_SHA256`は9.7で人間へ提示し承認された値をarchiveとは別経路からoperatorが入力する。aggregateではR1〜R7の7値も同じ別経路で入力する。archiveやdecisionから読み取った値を独立anchorの代用にしない。host contractはroot所有かつgroup/world非writableなabsolute `/usr/bin/python3`,`/usr/bin/git`,`/bin/bash`,`/usr/bin/env`である。1つでも不存在またはapproval target固定hash/versionと不一致ならblockedで、PATH探索や別binaryへfallbackしない。

```bash
/usr/bin/env -i \
  HOME=/var/empty PATH=/usr/bin:/bin LANG=C LC_ALL=C \
  RELEASE_ID="${RELEASE_ID:-}" POSTMERGE_STAGE="${POSTMERGE_STAGE:-}" \
  POSTMERGE_ACTION="${POSTMERGE_ACTION:-run}" \
  POSTMERGE_GENERATION="${POSTMERGE_GENERATION:-1}" \
  ARCHIVE_ROOT="${ARCHIVE_ROOT:-}" \
  HUMAN_APPROVED_TARGET_SHA256="${HUMAN_APPROVED_TARGET_SHA256:-}" \
  MERGE_LEASE_ATTESTATION="${MERGE_LEASE_ATTESTATION:-}" \
  MERGE_ATTESTATION="${MERGE_ATTESTATION:-}" \
  CUTOVER_ATTESTATION="${CUTOVER_ATTESTATION:-}" \
  CUTOVER_COMPLETION_ATTESTATION="${CUTOVER_COMPLETION_ATTESTATION:-}" \
  OPERATOR_ATTESTATION="${OPERATOR_ATTESTATION:-}" \
  EXPIRY_ATTESTATION="${EXPIRY_ATTESTATION:-}" \
  APPROVAL_TARGET_SHA256_R1="${APPROVAL_TARGET_SHA256_R1:-}" \
  APPROVAL_TARGET_SHA256_R2="${APPROVAL_TARGET_SHA256_R2:-}" \
  APPROVAL_TARGET_SHA256_R3="${APPROVAL_TARGET_SHA256_R3:-}" \
  APPROVAL_TARGET_SHA256_R4="${APPROVAL_TARGET_SHA256_R4:-}" \
  APPROVAL_TARGET_SHA256_R5="${APPROVAL_TARGET_SHA256_R5:-}" \
  APPROVAL_TARGET_SHA256_R6="${APPROVAL_TARGET_SHA256_R6:-}" \
  APPROVAL_TARGET_SHA256_R7="${APPROVAL_TARGET_SHA256_R7:-}" \
  /bin/bash --noprofile --norc <<'POSTMERGE_BOOTSTRAP'
set -Eeuo pipefail
umask 077
readonly SYSTEM_ENV=/usr/bin/env
readonly SYSTEM_PYTHON=/usr/bin/python3
readonly SYSTEM_GIT=/usr/bin/git
readonly SYSTEM_BASH=/bin/bash
readonly MASTER_TASK=full-repo-refactoring-2026-07-24
test -x "$SYSTEM_ENV" -a -x "$SYSTEM_PYTHON" -a -x "$SYSTEM_GIT" -a -x "$SYSTEM_BASH"
case "${RELEASE_ID:-}" in r1|r2|r3|r4|r5|r6|r7) ;; *) exit 2 ;; esac
case "${POSTMERGE_STAGE:-}" in
  premerge-lease|merge-record|cutover-predeploy|cutover-postdeploy|operator|aggregate) ;;
  *) exit 2 ;;
esac
case "${POSTMERGE_ACTION:-run}" in run|expire) ;; *) exit 2 ;; esac
case "${ARCHIVE_ROOT:-}" in /*) ;; *) exit 2 ;; esac
case "${POSTMERGE_GENERATION:-1}" in *[!0-9]*|0|'') exit 2 ;; esac
"$SYSTEM_ENV" -i VALUE="${HUMAN_APPROVED_TARGET_SHA256:-}" PATH=/usr/bin:/bin \
  "$SYSTEM_PYTHON" -I -S -c \
  'import os,re; assert re.fullmatch(r"[0-9a-f]{64}",os.environ["VALUE"])'

INPUT_ATTESTATION=
INPUT_FLAG=
if [[ "${POSTMERGE_ACTION:-run}" == "expire" ]]; then
  case "$POSTMERGE_STAGE" in premerge-lease|cutover-predeploy) ;; *) exit 2 ;; esac
  INPUT_ATTESTATION="${EXPIRY_ATTESTATION:-}"
  INPUT_FLAG=--expiry-attestation
else
  case "$POSTMERGE_STAGE:$RELEASE_ID" in
    premerge-lease:r1|premerge-lease:r2|premerge-lease:r3|premerge-lease:r4|premerge-lease:r5|premerge-lease:r6|premerge-lease:r7)
      INPUT_ATTESTATION="${MERGE_LEASE_ATTESTATION:-}"; INPUT_FLAG=--merge-lease-attestation ;;
    merge-record:r1|merge-record:r2|merge-record:r3|merge-record:r4|merge-record:r5|merge-record:r6|merge-record:r7)
      INPUT_ATTESTATION="${MERGE_ATTESTATION:-}"; INPUT_FLAG=--merge-attestation ;;
    cutover-predeploy:r3|cutover-predeploy:r4|cutover-predeploy:r5|cutover-predeploy:r6|cutover-predeploy:r7)
      INPUT_ATTESTATION="${CUTOVER_ATTESTATION:-}"; INPUT_FLAG=--cutover-attestation ;;
    cutover-postdeploy:r3|cutover-postdeploy:r4|cutover-postdeploy:r5|cutover-postdeploy:r6|cutover-postdeploy:r7)
      INPUT_ATTESTATION="${CUTOVER_COMPLETION_ATTESTATION:-}"; INPUT_FLAG=--cutover-completion-attestation ;;
    operator:r1|operator:r2|operator:r3|operator:r4|operator:r5|operator:r6)
      INPUT_ATTESTATION="${OPERATOR_ATTESTATION:-}"; INPUT_FLAG=--operator-attestation ;;
    aggregate:r7)
      test "${POSTMERGE_ACTION:-run}" = run ;;
    *) exit 2 ;;
  esac
fi
if [[ "$POSTMERGE_STAGE" != aggregate || "${POSTMERGE_ACTION:-run}" == expire ]]; then
  case "$INPUT_ATTESTATION" in /*) ;; *) exit 2 ;; esac
  test -f "$INPUT_ATTESTATION" -a ! -L "$INPUT_ATTESTATION"
fi

ANCHORS=("$RELEASE_ID=$HUMAN_APPROVED_TARGET_SHA256")
if [[ "$POSTMERGE_STAGE" == aggregate ]]; then
  ANCHORS=()
  for id in r1 r2 r3 r4 r5 r6 r7; do
    case "$id" in
      r1) value="$APPROVAL_TARGET_SHA256_R1" ;;
      r2) value="$APPROVAL_TARGET_SHA256_R2" ;;
      r3) value="$APPROVAL_TARGET_SHA256_R3" ;;
      r4) value="$APPROVAL_TARGET_SHA256_R4" ;;
      r5) value="$APPROVAL_TARGET_SHA256_R5" ;;
      r6) value="$APPROVAL_TARGET_SHA256_R6" ;;
      r7) value="$APPROVAL_TARGET_SHA256_R7" ;;
    esac
    "$SYSTEM_ENV" -i VALUE="$value" PATH=/usr/bin:/bin \
      "$SYSTEM_PYTHON" -I -S -c \
      'import os,re; assert re.fullmatch(r"[0-9a-f]{64}",os.environ["VALUE"])'
    ANCHORS+=("$id=$value")
  done
  test "${APPROVAL_TARGET_SHA256_R7}" = "$HUMAN_APPROVED_TARGET_SHA256"
fi

"$SYSTEM_ENV" -i HOME=/var/empty PATH=/usr/bin:/bin LANG=C LC_ALL=C \
  GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
  GIT_OPTIONAL_LOCKS=0 GIT_NO_REPLACE_OBJECTS=1 \
  "$SYSTEM_PYTHON" -I -S - \
    "$SYSTEM_ENV" "$SYSTEM_PYTHON" "$SYSTEM_GIT" "$SYSTEM_BASH" \
    "$MASTER_TASK" "$RELEASE_ID" "$POSTMERGE_STAGE" \
    "${POSTMERGE_ACTION:-run}" "$POSTMERGE_GENERATION" \
    "$ARCHIVE_ROOT" "$HUMAN_APPROVED_TARGET_SHA256" \
    "$INPUT_FLAG" "$INPUT_ATTESTATION" "${ANCHORS[@]}" <<'PY'
import hashlib, json, os, pathlib, re, shutil, stat, subprocess, sys, tempfile, urllib.parse

(env_bin, python_bin, git_bin, bash_bin, master, release, stage, action, generation,
 archive_raw, approved_target_sha, input_flag, input_path, *anchor_args) = sys.argv[1:]
sha256 = lambda b: hashlib.sha256(b).hexdigest()
hex64 = re.compile(r"[0-9a-f]{64}\Z")
attempt_re = re.compile(r"[a-z0-9-]+-[0-9a-f]{32}\Z")

def no_dups(pairs):
    out = {}
    for key, value in pairs:
        assert key not in out
        out[key] = value
    return out

def read_regular(path):
    path = pathlib.Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        assert stat.S_ISREG(info.st_mode)
        chunks = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(fd)

clean_env = {
    "HOME": "/var/empty", "PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_REPLACE_OBJECTS": "1",
}
def run_bytes(argv):
    return subprocess.run(argv, env=clean_env, check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE).stdout
def run_text(argv):
    return run_bytes(argv).decode("utf-8").strip()

archive_root = pathlib.Path(archive_raw).resolve(strict=True)
assert archive_root.name == "release-archives" and archive_root.parent.name == ".pipeline"
control_root = archive_root.parent.parent.resolve(strict=True)
task = f"{master}-{release}"
premerge = archive_root / task / "premerge"
target_path = premerge / "files/.pipeline/evidence" / task / "approval-target.json"
target_raw = read_regular(target_path)
assert sha256(target_raw) == approved_target_sha and hex64.fullmatch(approved_target_sha)
target = json.loads(target_raw, object_pairs_hook=no_dups)
assert target["task_id"] == task and target["release_id"] == release

for name, path in {
    "env": env_bin, "python": python_bin, "git": git_bin, "bash": bash_bin
}.items():
    p = pathlib.Path(path)
    info = p.resolve(strict=True).stat()
    assert p.is_absolute() and stat.S_ISREG(info.st_mode)
    assert info.st_uid == 0 and info.st_mode & 0o022 == 0
    assert control_root not in p.resolve().parents and archive_root not in p.resolve().parents
    expected = target["system_toolchain"][name]
    assert expected["path"] == path
    assert expected["uid"] == 0
    assert expected["mode"] == f"{stat.S_IMODE(info.st_mode):04o}"
    assert sha256(read_regular(p.resolve())) == expected["sha256"]
versions = {
    "python": run_text([python_bin, "--version"]),
    "git": run_text([git_bin, "--version"]),
    "bash": run_text([bash_bin, "--version"]).splitlines()[0],
}
for name, value in versions.items():
    assert value == target["system_toolchain"][name]["version"]
assert target["system_toolchain"]["env"]["version"] is None

repo = target["control_repository"]
assert repo["origin_remote_name"] == "origin"
assert run_text([git_bin, "-C", str(control_root), "rev-parse", "--show-toplevel"]) == str(control_root)
common_raw = run_text([git_bin, "-C", str(control_root), "rev-parse",
                       "--path-format=absolute", "--git-common-dir"])
assert str(pathlib.Path(common_raw).resolve(strict=True)) == repo["git_common_dir_realpath"]
origins = run_text([git_bin, "-C", str(control_root), "config", "--local",
                    "--get-all", "remote.origin.url"]).splitlines()
assert origins == [repo["origin_url_literal"]]
def canonical_origin(raw):
    parsed = urllib.parse.urlsplit(raw)
    assert parsed.scheme in {"https", "ssh"} and parsed.hostname
    assert not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
    host = parsed.hostname.lower()
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    assert path.startswith("/") and ".." not in pathlib.PurePosixPath(path).parts
    return f"{parsed.scheme.lower()}://{host}{port}{path}"
origin_canonical = canonical_origin(origins[0])
assert origin_canonical == repo["origin_url_canonical"]
assert repo["control_root_realpath"] == str(control_root)
object_format = run_text([git_bin, "-C", str(control_root), "rev-parse",
                          "--show-object-format"])
assert object_format == repo["git_object_format"]
assert repo["repository_id"] == sha256(
    (origin_canonical + "\0" + object_format).encode()
)

manifest_path = premerge / "archive-manifest.json"
manifest_raw = read_regular(manifest_path)
manifest = json.loads(manifest_raw, object_pairs_hook=no_dups)
assert manifest["task_id"] == task == target["task_id"]
assert manifest["head_sha"] == target["head_sha"]
prearchive_path = premerge / "control/approval-stage/prearchive-ready.json"
prearchive_raw = read_regular(prearchive_path)
prearchive = json.loads(prearchive_raw, object_pairs_hook=no_dups)
attempt = prearchive["stage_attempt_id"]
parent = prearchive["parent_closure_attempt_id"]
assert attempt_re.fullmatch(attempt) and attempt.startswith("approval-")
assert prearchive["task_id"] == task and prearchive["head_sha"] == target["head_sha"]
assert prearchive["approval_target_sha256"] == approved_target_sha
completion_path = (control_root / ".pipeline/closure-stage-attempts" / task /
                   "approval" / attempt / "completion.json")
completion_raw = read_regular(completion_path)
completion = json.loads(completion_raw, object_pairs_hook=no_dups)
assert completion["task_id"] == task and completion["head_sha"] == target["head_sha"]
assert completion["stage_attempt_id"] == attempt
assert completion["parent_closure_attempt_id"] == parent
assert completion["approval_target_sha256"] == approved_target_sha
assert completion["prearchive_ready_path"] == "premerge/control/approval-stage/prearchive-ready.json"
assert completion["prearchive_ready_sha256"] == sha256(prearchive_raw)
assert completion["archive_manifest_sha256"] == sha256(manifest_raw)

anchors = {}
for item in anchor_args:
    rid, digest = item.split("=", 1)
    assert rid not in anchors and rid in {f"r{i}" for i in range(1, 8)}
    assert hex64.fullmatch(digest)
    anchors[rid] = digest
if stage == "aggregate":
    assert list(anchors) == [f"r{i}" for i in range(1, 8)]
else:
    assert anchors == {release: approved_target_sha}

sources = target["postmerge_trusted_sources"]
assert sources == sorted(sources, key=lambda x: x["repo_path"])
assert len({x["repo_path"] for x in sources}) == len(sources) > 0
launcher_map = manifest["trusted_launchers"]
assert set(launcher_map) == {x["repo_path"] for x in sources}
temp_parent = pathlib.Path("/private/tmp" if pathlib.Path("/private/tmp").is_dir() else "/tmp")
tool_root = pathlib.Path(tempfile.mkdtemp(prefix=f"{task}-{stage}-", dir=temp_parent))
os.chmod(tool_root, 0o700)
try:
    extracted = {}
    for source in sources:
        repo_path = source["repo_path"]
        assert not pathlib.PurePosixPath(repo_path).is_absolute()
        assert ".." not in pathlib.PurePosixPath(repo_path).parts
        entry = launcher_map[repo_path]
        assert entry["repo_path"] == repo_path
        assert source["mode"] == entry["mode"] and source["mode"] in {"100644", "100755"}
        tree_line = run_text([git_bin, "-C", str(control_root), "ls-tree",
                              target["head_sha"], "--", repo_path])
        tree_meta, tree_path = tree_line.split("\t", 1)
        tree_mode, tree_type, tree_oid = tree_meta.split()
        assert tree_path == repo_path and tree_type == "blob"
        assert tree_mode == source["mode"] == entry["mode"]
        blob_oid = run_text([git_bin, "-C", str(control_root), "rev-parse",
                             f'{target["head_sha"]}:{repo_path}'])
        blob = run_bytes([git_bin, "-C", str(control_root), "cat-file", "blob", blob_oid])
        assert blob_oid == tree_oid == source["blob_sha"] == entry["blob_sha"]
        assert sha256(blob) == source["sha256"] == entry["sha256"]
        archive_member = premerge / entry["archive_path"]
        assert archive_member.resolve(strict=True).is_relative_to(premerge.resolve(strict=True))
        assert sha256(read_regular(archive_member)) == entry["sha256"]
        assert entry["archive_mode"] == f"{stat.S_IMODE(archive_member.stat().st_mode):04o}"
        destination = tool_root / repo_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        extracted_mode = 0o500 if source["mode"] == "100755" else 0o400
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     extracted_mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(blob); stream.flush(); os.fsync(stream.fileno())
        assert stat.S_IMODE(destination.stat().st_mode) == extracted_mode
        assert run_text([git_bin, "-C", str(control_root), "hash-object",
                         str(destination)]) == blob_oid
        extracted[repo_path] = destination
    trusted_input = ""
    trusted_input_sha = ""
    if input_path:
        input_raw = read_regular(input_path)
        trusted_input_sha = sha256(input_raw)
        input_root = tool_root / "external-input"
        input_root.mkdir(mode=0o700)
        input_copy = input_root / f"{trusted_input_sha}.json"
        fd = os.open(input_copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
        with os.fdopen(fd, "wb") as stream:
            stream.write(input_raw); stream.flush(); os.fsync(stream.fileno())
        assert sha256(read_regular(input_copy)) == trusted_input_sha
        trusted_input = str(input_copy)
    runner = extracted["scripts/test/run-refactor-postmerge-stage.sh"]
    subprocess.run([bash_bin, "--noprofile", "--norc", "-n", str(runner)],
                   env=clean_env, cwd=str(control_root), check=True)
    common_argv = [
        bash_bin, "--noprofile", "--norc", str(runner),
        "--master-task", master, "--release", release,
        "--archive-root", str(archive_root), "--control-root", str(control_root),
        "--source-head", target["head_sha"], "--approval-target-sha256",
        approved_target_sha, "--stage", stage, "--generation", generation,
        "--trusted-cwd", str(control_root),
    ]
    if trusted_input:
        common_argv += ["--input-attestation-sha256", trusted_input_sha]
    runner_argv = list(common_argv)
    if action == "expire":
        runner_argv += ["--expire-stage", input_flag, trusted_input]
    else:
        runner_argv += ["--resume-or-new"]
        if stage == "aggregate":
            for rid, digest in anchors.items():
                runner_argv += ["--release-approval-target-sha256", f"{rid}={digest}"]
        else:
            runner_argv += [input_flag, trusted_input]
    runner_env = dict(clean_env)
    runner_env.update({
        "RF_TRUSTED_TOOL_ROOT": str(tool_root),
        "RF_TRUSTED_ENV": env_bin,
        "RF_TRUSTED_PYTHON": python_bin,
        "RF_TRUSTED_GIT": git_bin,
        "RF_TRUSTED_BASH": bash_bin,
    })
    result = subprocess.run(runner_argv, env=runner_env, cwd=str(control_root))
    if result.returncode != 0:
        print(f"trusted tool root preserved after failure: {tool_root}", file=sys.stderr)
        raise SystemExit(result.returncode)
    verify_argv = list(common_argv) + ["--verify-existing"]
    if action == "expire":
        verify_argv += ["--expect-expired", input_flag, trusted_input]
    elif stage == "aggregate":
        for rid, digest in anchors.items():
            verify_argv += ["--release-approval-target-sha256", f"{rid}={digest}"]
    else:
        verify_argv += [input_flag, trusted_input]
    verified = subprocess.run(verify_argv, env=runner_env, cwd=str(control_root))
    if verified.returncode != 0:
        print(f"trusted tool root preserved after completion verification: {tool_root}",
              file=sys.stderr)
        raise SystemExit(verified.returncode)
    shutil.rmtree(tool_root)
except BaseException:
    raise
PY
POSTMERGE_BOOTSTRAP
```

このbootstrapはGit blobを全量取得してから`O_EXCL|O_NOFOLLOW`のrepo外regular fileへ書き、approval target、archive manifest、archive member、Git blobのOID/SHA-256を全件照合し、`bash -n`後だけclean `env -i`で実行する。`git show | bash`、stdin/FIFO/`/dev/stdin`実行、caller worktree/archive memberの直接実行、ambient `BASH_ENV,ENV,PYTHONPATH,GIT_*`、PATH探索は禁止する。失敗時temporary rootは削除せずpath/hashをfailed receiptへ残し、成功receipt verify後だけ削除する。

#### 9.8.1〜9.8.6 stage入力と期待結果

| stage | release | run時のexact入力 | parent/期待結果 |
|---|---|---|---|
| `premerge-lease` | R1〜R7 | `MERGE_LEASE_ATTESTATION` | final approval completionをparentに、base/head/tree/check/environmentへbindした5分permit。merge前に期限切れなら下記rollover |
| `merge-record` | R1〜R7 | `MERGE_ATTESTATION` | premerge-lease completionをparentに、permit/remote/tree/environment照合、`approved -> merged` |
| `cutover-predeploy` | R3〜R7 | `CUTOVER_ATTESTATION` | merge-record completionをparentに15分permit。deploy前期限切れ/generation driftはrollover |
| `cutover-postdeploy` | R3〜R7 | `CUTOVER_COMPLETION_ATTESTATION` | cutover-predeploy completion、permit/lease/version/generation不変。predeploy単独は完了扱いしない |
| `operator` | R1〜R6 | `OPERATOR_ATTESTATION` | R1/R2はmerge-record、R3〜R6はcutover-postdeploy completionをparentにexact drain、`merged -> completed` |
| `aggregate` | R7 | external attestation 0、`APPROVAL_TARGET_SHA256_R1`〜`R7` | cutover-postdeploy completionをparentに7 release chainを検証し、二相aggregate後`merged -> completed` |

期限付きstageの正常な再発行だけは、同bootstrapへ`POSTMERGE_ACTION=expire`、現在の`POSTMERGE_GENERATION=N`、認可済み`EXPIRY_ATTESTATION`を渡す。runnerは旧permitが未使用、merge/deploy未実施、期限切れまたはgeneration drift、remote/environment同一をfresh evidenceで確認し、generation N selectionへ`status=expired` completionをsingle-writeする。その後、新しい観測attestationを用意して`POSTMERGE_ACTION=run,POSTMERGE_GENERATION=N+1`を実行する。実failure、schema failure、ack喪失、既にmerge/deploy済みではrolloverを拒否する。selection keyは`task,release,head,stage,generation,approval_target_sha256,archive_manifest_sha256,final_approval_completion_sha256,parent_completion_sha256,input_attestation_sha256,environment`のcanonical SHA-256であり、0件=create、exact 1件=recover、複数/failed/inconsistent=blocked、mtime/latest推測0。

aggregateのcanonical schemaはparallel hash配列を廃止し、`schema_version="refactor-final-aggregate/v2",master_task_id,status,validation_body_sha256,aggregate_stage_completion_sha256,release_chains[7],item_ids,assertions[]`とする。`release_chains`は`sequence=1..7,release_id=r1..r7` exact順で、各entryが`task_id,release_base_sha,release_head_sha,merged_main_sha,previous_release_completion_sha256,bootstrap_receipt_sha256,trust,closure_stage_receipts,postmerge_stage_receipts,lifecycle_events,review_artifacts`を持つ。

- `trust`: 別経路入力のapproval target、decision、prearchive-ready、archive manifest、archive外final approval completionの各SHA-256。
- `review_artifacts`: **全7 release**のtribunal 4 artifact+synthesis、post Fable/Codex/dual、独立QA、outcome、approval target/decisionをparseしてhead/task/reviewer independence/verdict/hash chainを検証する。R7だけ5 phase gate、final UI、全itemを追加する。
- `closure_stage_receipts`: 全releaseで`build-and-state,release-execution,release-finalize,manifest,tribunal,post-review,approval-target,approval`のexact 8件。各entryは`sequence,stage,attempt_id,selection_sha256,attempt_receipt_sha256,completion_sha256,parent_completion_sha256`。
- `postmerge_stage_receipts`: R1/R2は`premerge-lease,merge-record,operator` exact 3、R3〜R6は`premerge-lease,merge-record,cutover-predeploy,cutover-postdeploy,operator` exact 5、R7は`premerge-lease,merge-record,cutover-predeploy,cutover-postdeploy,aggregate` exact 5。期限rolloverがあれば同stage内`generations[]`を1..N順で、1..N-1=`expired`、N=`pass`、欠落/重複なしに固定する。
- `lifecycle_events`: `planned -> build_authorized -> worktree_created -> building -> built -> verifying -> verified -> evidence_ready -> awaiting_approval -> approved -> merged -> completed`の実際のevent列とparent hashを固定する。R2〜R7の`previous_release_completion_sha256`は直前release terminal receiptへ一致。

R7自己参照を避けるため、(1)`aggregate-body.json`へR7 mergedまでとaggregate selection/attemptをsingle-write、(2)body SHAへbindして`merged -> completed`とaggregate completionをsingle-write、(3)両hashとcompleted eventを持つ`final-aggregate.json`をsingle-writeする。ack喪失時は同selectionで既済prefixをverifyし未完suffixだけを実行する。missing/duplicate/reordered stage、failed selection、parent/lifecycle hash差、R1〜R6 review artifact欠落を全てfailさせる。

全postmerge stageで既存canonical artifactがあればwriterを再実行せずverify-existingする。実failureはappend-only failed receiptを作ってterminal停止し、premerge archive、approval、merge済み履歴、過去selection/attemptを変更しない。local `main`や削除可能なworktreeを証拠の正本にしない。

## 10. 実行順のトレース検証

### 10.1 前提が壊れないことの確認

| 先行項目 | 後続で使う前提 | 壊さないための仕組み |
|---|---|---|
| RF-00B/00C/00D | 全後続の現在契約 | bugはstrict xfail、正しい挙動だけgolden化 |
| RF-01 | 全Harness項目の安全なpath | 共通path helperをRF-46/48でも再利用 |
| RF-03A→03B→03C→03D | RF-04A/05B/06E/08のuser・workspace secret | audience専用Agent config→目的別user API→subject-bound token管理→Agent Gitのstdin/allowlisted origin隔離の順で全権Admin BFFとcredential URLを外す |
| RF-04A→04B | 全Dashboard BFF | browser JSON/service-key fallbackを除去し、署名済みadmin sessionだけを全権proxyへ通す |
| RF-05A→05B→05C→05D1→05D1B→OP-05D→05C2→05D2→05E→05F→05G→05H→06A→06B→06C1→OP-06C→05F2→05G2 | RF-06C2〜06I3/64のidentity/secret/outbound policy | identity→consumer compatibility→Runtime principal/rootless→fresh drain後legacy閉鎖→outbound/storage→key ring互換→Redis principal→fresh drain後no-kid/Admin verifier閉鎖の順 |
| RF-06A→06B→06C1→06C2→06D1→06D2→06E→06F→06G→06H→06I1→06I2→06I3 | RF-08/09A/09Bとworkload隔離 | storage→Redis principal→event broker→media capability→provider→Zoom→egress broker→resource ownership→ingress隔離→Lite process隔離の順 |
| RF-08→09A→09B | Browser save結果 | structured git resultを先に作り、相関WSを互換配備・drainし、最後にBrowser Redis credentialを除去する |
| RF-10 | RF-56/57のmeeting identity | URL契約を先に単一化し、巨大function内から抽出 |
| RF-11 | RF-52〜55 lifecycle | 意味差を先に修正し、誤挙動を新architectureへ固定しない |
| RF-13/14 | RF-19/68/69 | dedup/timeline意味を先に固定し、WS/UIは同じmanagerを使用 |
| RF-15 | RF-16/17/67〜71 | 全async UI writeがgeneration guardを利用 |
| RF-31〜33 | RF-43 required CI | gateの偽陽性を直してからrequired化 |
| RF-35/36 | RF-37/45/51 | canonical ownershipを決めてからresolver/catalog/docsを接続 |
| RF-46→47→48→49A→49B→49C→49D→49E | RF-50/最終closure | schema/path/I/Oを固定し、immutable base互換を作ってから953行scriptを薄くする |
| RF-52〜55 | RF-56〜59/65 | lifecycle cycle除去後にBot/deferred/model移動 |
| RF-60〜62 | RF-63 | provider内部pure処理を分けてからHTTP endpointを分割 |
| RF-65→66A→66B→66C | DB/package consumer | metadata同一class re-export→DB infrastructure→pure contract→cross-service install削除の順 |
| RF-67 | RF-68〜71 | DTO/domain/display境界を固定してからcomponent分割 |
| RF-72 | RF-73 | global state逆importを先に除去してからentrypoint分割 |
| RF-68/70 | RF-69/71 | pure view/action modelを先に作り、UI分割をprops配線へ限定 |
| RF-74A〜74I | RF-75A〜75F | duplicate/dead sourceを1論点ずつ閉じてからlint、structure、文書の最終gate |

### 10.2 実行順シミュレーションで見つけた禁止順序

- RF-64 Gateway分割をRF-03C〜06Gより前にしてはいけない。認証bypass、service-key confused deputy、cross-audience token、provider/Zoom/proxy credentialを複数routerへ拡散する。
- RF-65/66 ORM移動をRF-52〜59より前にしてはいけない。循環importとDB class identityを同時に変える。
- RF-57 `request_bot` strategy化をRF-10/55/56より前にしてはいけない。URL/lifecycle/configの三つの変数を同時に動かす。
- RF-59 deferred coordinator化をRF-58より前にしてはいけない。副作用のmock点がなく、順序差を検出できない。
- RF-63 endpoint分割をRF-60〜62より前にしてはいけない。provider internalとHTTP contractを同時に動かす。
- RF-69/71 UI分割をRF-12〜23、RF-67/68/70より前にしてはいけない。race/polling/検索bugを新hookへ移すだけになる。
- RF-43 required CIをRF-31〜42より前にしてはいけない。false passまたは全PR停止のどちらかになる。
- RF-74H/74I dead code削除をbuild/typecheck/route inventoryなしでしてはいけない。間接参照を見落とす。

### 10.3 フェーズ間rollback

- 全フェーズ共通: 失敗branchを証拠として保持し、最後に合格したitem SHAから新worktree/branchを作る。完了branchへrevert commitを積まない。
- フェーズ1失敗: 後続を開始せず、該当security itemのred/green evidenceを保全する。漏洩可能性のあるtokenはGitとは別にrotateし、新旧credentialをevidenceへ書かない。
- フェーズ2失敗: 対象項目だけを修正して新worktreeで再実行する。DB migrationや既存data cleanupを追加しない。
- フェーズ3失敗: required workflow/deployが未接続なら最後の良好SHAから再実行。RF-43/44で外部設定変更を実施していた場合は本計画で勝手に変更せず、人間承認を得た運用rollbackとして別記録する。
- フェーズ4失敗: compatibility wrapper/re-exportを最後の良好SHAに残した状態から再実行する。DB metadata差が出た時点で停止しmigrationを作らない。
- フェーズ5失敗: 失敗したA〜I/Fの見出し単位で新worktreeから再実行する。削除fileを別commitで戻すのではなく、元SHAを起点にする。

## 11. やらないこと

実行者は、善意でも次を行ってはいけない。

- この計画にない機能追加、UI redesign、文言変更、API version追加。
- React、Next.js、FastAPI、Redis、DB driver、provider SDK、Helm等の依存version更新、lockfile更新。
- endpoint path、method、request/response field、HTTP status、WebSocket/SSE payloadの変更。ただしRF-03A〜03C、RF-04A〜04B、RF-05A〜05H、RF-06A〜06I3、RF-08〜10に明記したsecurity/compatibility変更だけ例外。
- 新しいOAuth/API scopeの追加。Agentは既存`browser`、Calendarは`bot`、recordingsは`tx`を使う。
- token保存方式の暗号化設計変更、既存token一括migration。公開responseからの除外とinternal取得分離だけ行う。
- DB table/column/index/constraint/defaultの変更、Alembic migration、過去Meeting statusの書換え。
- Redis key prefix、TTL、job JSON、Pub/Sub channelの既存契約変更。RF-06C1/RF-06C2のACL/server-side broker allow-list、RF-09A/RF-09Bの相関session channel、RF-24の同一schema原子化だけが例外。生成workloadへraw Redis fallbackを残さない。
- provider model、prompt、temperature、retry、timeout、chunk幅、timestamp rounding、speaker heuristicの調整。
- 文字起こしgolden差を「改善」とみなして期待値を書換えること。
- `tests3`の不存在45scriptを理由なしに削除、復元、active扱いすること。
- `features/` sidecarを過去commitから復元すること。現在の非適用方針をmachine-readableにする。
- testを削除、skip追加、assert緩和、lint rule無効化、`continue-on-error`追加でgreenにすること。
- check command内で自動repairし、それを検査成功と扱うこと。
- 既存 `.pipeline` 成果物/worktree、ユーザーの未追跡fileを削除・移動・stash・baseline commitへ混入すること。
- `git reset --hard`、広範な`git checkout --`、一括find/replace、symbol renameの文字列置換。
- live meeting参加、実利用者へのBot投入、production DB操作、GCP deploy、branch protection変更を、項目に明記した承認なしで行うこと。
- 調査時より古い稼働imageのスクリーンショットを現HEADの合格証拠にすること。
- `MANIFEST.md`のfuture targetを、current mainの実装済み仕様として扱うこと。
- 行数目標のための空白削除、複数statementの1行化、意味のないwrapper分散。
- 1コミットへ複数見出しをまとめること。A/B等のsuffixを持つ見出しも、それぞれ独立した1項目・1コミットとして扱う。

## 12. 実行者へ渡す指示文

以下を、そのまま実行AIへ渡す。

```text
あなたは generic_tldv のリファクタリング実行者です。

唯一の実行計画は
.pipeline/plans/full-repo-refactoring-2026-07-24/plan.md
です。まず全文を読み、request.md、research-brief.md、option-matrix.md、
kpi-backcast-roadmap.md、verification-contract.mdも読んでください。

作業規則:
1. 実装前外部レビュー承認ゲートを満たした後、7 releaseを本文0章の境界どおり順番に実施してください。各release内は見出し順（R1はRF-00A→RF-00N→RF-00E→RF-00B→RF-00C→RF-00D→RF-01…）で、必ず1項目ずつ実施し、番号を独自に並べ替えないでください。
2. `### RF-...`見出し1つ=1コミットです。A/B等のsuffix付き見出しも別コミットです。
3. 各項目の開始前にclean worktreeを確認し、対象symbolのGitNexus upstream impactを実行してください。
4. HIGH/CRITICALならblast radiusをユーザーへ提示し、明示的な続行承認を受けるまで編集しないでください。計画に書かれた範囲を超える場合は承認があっても中断してください。
5. bug修正は、先に項目指定testが意図した理由で失敗することを確認してから本体を直してください。
6. 各項目の「完了条件」にある対象test、required suite、git diff check、GitNexus detect_changesを全て通してください。
7. 完了条件を1つでも満たせない場合、test削除・skip・assert緩和・lint無効化・依存更新で回避せず、そこで中断して報告してください。
8. 同じtestが2回連続で失敗した場合も中断し、外部送信の明示承認範囲内ならFable phase reviewを実行してください。承認/loginがなければ送信せず、そのblockerと失敗証拠を報告してください。
9. 項目外file、既存ユーザー成果物、lockfile、DB schema、Redis key、公開APIに予定外差分が出たらcommitせず中断してください。
10. 各項目完了後に指定messageでcommitし、commit SHA、変更file、test command、結果、残リスクをevents/evidenceへ記録してください。
11. 各release末尾でrelease closure、人間承認、PR merge、必要なoperator gateを満たすまで次releaseへ進まないでください。R7内ではフェーズゲートも満たすまで次フェーズへ進まないでください。
12. production deploy、live meeting、branch protection変更は、この計画だけを根拠に実行しないでください。OP節は認可済み人間operatorの実測artifactを待ち、AIが捏造しないでください。

判断に迷う場合:
- 「やらないこと」に当たるなら実施しません。
- 既存契約と計画が衝突するなら、契約を推測で変えず中断します。
- 行番号がずれたらsymbol/test名で探します。symbolが消えていたら中断します。
- より良い設計を思いついても、計画外なら提案として報告するだけにします。

完了報告には、全コミット一覧、全test/gate結果、GitNexus最終impact、
fixture E2E screenshot、source SHA照合、tribunal/post review/独立QA、
outcome-card、人間承認、未解決事項を含めてください。
```

## 13. 計画作成時の制約と未確定事項

- GitNexus indexは調査時にstaleで、FTS/embeddingも利用不能だった。旧graphだけに依存せず最新sourceを直接読んだが、実行者は編集直前に最新indexでimpactを再取得する。
- Fairy Tale skillが案内するTypeScript similarity adapterとresidency Python scriptはこの環境に存在しなかった。重複検出はTypeScript/Python ASTと直接比較で代替した。実行時に正式adapterが利用可能なら、RF-74前に追加のclone reportを証拠へ入れる。ただし計画外の統合対象を自動追加しない。
- 調査時のDashboard live imageは現HEADより古かった。視覚baseline/最終E2Eは、実行対象HEADから起動した環境で取り直す。
- Calendar RF-26はPostgreSQLの`FOR UPDATE SKIP LOCKED`を前提とする。production dialectが異なる場合は中断し、勝手なDB migrationや新statusを作らない。
- Dashboard lintの正確なbaselineは項目0で再採取する。調査時は61 errors / 87 warnings。
- branch protectionのrequired check設定はrepo外stateである。workflow実装と管理者設定確認を別々に報告する。
- 本計画は公開response、cross-subject access、生成workloadへのsecret配布を狭めるが、DB/backup内の`APIToken.token`、`user.data.workspace_git.token`、subject provider key、Google refresh token、webhook credential等をenvelope encryption/HMAC digestへ移行しない。DB/backup侵害時のreplay/exfiltrationは残る。別taskでKMS envelope encryption、token digest lookup、backfill、dual-read drain、backup rotationを設計する。
- RF-19でDashboardはsame-origin HttpOnly cookie、Wake Orchestratorと全non-browser client/docs/checkは`Authorization: Bearer`へ移し、Gatewayの`?api_key=`/`X-API-Key` WebSocket fallbackを削除する。browserは任意headerを付けられないため、public raw-key browser sampleをserver-side bridgeまたは認証済みsame-origin Dashboardの例へ置換する。本計画完了時はDashboard/VNC/CDP/workload/public WebSocketを含むURL queryのraw API/service/relay/provider tokenを0とし、pre-signed non-raw opaque capabilityだけを本文で明記したexact surfaceに許す。
- `workload-access-broker`はworkload間を分離するtrusted central componentで、process memoryには全active sessionのpublic key、connection、consumed access identityがある。Ed25519 private keyは各relayだけに置くためbroker侵害だけで署名proofは作れないが、active broker processの完全侵害に対する接続metadata可視性は残る。別task候補はper-session sidecar/brokerとhardware-backed key isolation。
- Gateway→Admin raw user-token validation、service bearer/introspection、Redis/Postgres/MinIOの一部transportはtrusted cluster/networkを前提とし、全service mTLS/TLS化は対象外。CNI/node/network侵害時のcredential/meeting data盗聴は未解消。別taskでservice mTLS、Redis TLS、Postgres `sslmode=verify-full`、MinIO TLSを一括移行する。
- `.claude`のcanonical absolute symlinkと外部review providerはhandoff環境外依存である。pinned canonical checkout、送信承認、loginがなければ実装開始gateはblockedになる。local substituteやmax-call fallbackで完遂扱いにしない。

## Appendix A. `refactor-item-matrix.json` のliteral作成規則

RF-00E実行者はtest pathやrunnerを推測してはいけない。次の規則と表がmatrixのauthoritative sourceである。

### A.1 見出しからmatrix entryを作る規則

1. 1 entryは`### RF-...`見出し開始から同項目の`- コミット:`行までだけを読む。フェーズgateや次項目のV-*を取り込まない。
2. test candidate pathは、(a)`完了条件`の`repo/relative/test/path::{test_a,test_b}`、(b)`対象`で`新規`と明記し、かつ次のliteral basename規則を満たすtest file、(c)rule 3で適用されるA.2 fallback、の順に作る。test file判定はPython=`test_*.py`、TypeScript/JavaScript=`*.test.ts|*.test.tsx|*.test.js|*.test.mjs|*.spec.ts|*.spec.tsx`、shell=`*/tests/test_*.sh`だけである。`tests3/**/*.py`等のrunner prefixに一致しても、このbasename規則外の`report_status.py`,`registry_parity.py`,`portability.py`等production helperをtest candidateへ入れない。単に変更・削除対象として列挙した既存test、archive、fixture、source pathは自動登録しない。特にRF-10の削除対象`services/mcp/test_parse_meeting_url.py`はcommandへ入れない。completionのbare `basename::test_name`は、同じbasenameのcandidateがexact 1件ならそのpathへbindする。同名candidate 0件なら、拡張子/runnerが一致するcandidateがexact 1件の場合だけその唯一pathへbindする。0件または複数ならlinterをfailさせる。これにより、例えばRF-35のlogical `test_registry_parity.py::*`は唯一のpytest candidate `tests3/unit/refactor/test_rf_35.py`へbindされる。`path::{...}`のcommand argvはpathだけ、`expected.required_test_names`へ各fully-qualified nodeidを別要素で登録する。後続の`::test_b`は直前に一意解決したpathに属する。test名をbasenameのfileへ勝手に新規作成しない。
3. A.2は**fallback mapping**である。rule 2(a)(b)からそのrunner/cwdのcandidateを1件以上取得できるIDにはA.2 pathを追加しない。取得できないrunner/cwdだけA.2のliteral pathを1件作る。`RF-00C`の2行だけは`always`で、Dashboard/Core双方を必ず追加する。本文でfile未確定のcross-service/fake-timer/static assertionはfallback fileへ本文のexact test名で実装する。空の`required_test_names`は全test runnerで禁止する。
4. 同一itemにPython/TypeScript/Core等が複数ある場合は`commands[]`を分ける。1 commandへcwd/venvを混在させない。
5. `required_suites`は同項目の`完了条件`内にliteralで書かれたV-*だけ。RF-00Aは`mode=inline-before-runner,commands=[],required_suites=[]`。RF-00Nは`mode=bootstrap-once,status=active,replay_policy=once,required_suites=[]`とし、A.3のverify-only commandだけを登録する。
6. test/suite以外の直接実行commandはA.3/A.5だけを`argv` commandへ登録する。本文の`rg`、件数、canary、schema説明は対応testまたはrequired suite内のassertionであり、任意shell commandとして実行しない。本文に`exact command`と明記したargvがA.3/A.5にない場合はmatrix linterをfailさせる。
7. normalization `N(ID)` はIDをASCII lowercase化し、`-`を`_`へ変える。例: `N(RF-05A)=rf_05a`。
8. Appendix未掲載IDは本文にfull test pathがある。full pathもsupplemental mappingもargv-only mappingもないIDをmatrix linterが見つけたらRF-00Eをfailさせ、実装へ進まない。

### A.1a test pathからrunnerを決めるprefix table

candidate pathは次の上から最初に一致する1行へbyte一致で割り当てる。brace/glob/`<N(ID)>`を展開してから照合し、複数一致、0一致、path escapeはfail。pytestの`argv`は`[repo-relative-path]`、Vitestは表のcwdからのrelative path 1件、Coreは`["--file",core-relative-path]`で、nodeidはargvへ渡さずmachine reportのrequired name照合へ使う。

| exact path prefix / suffix | runner | cwd | venv |
|---|---|---|---|
| `services/transcription-service/tests/**/*.py` | `pytest` | `.` | `transcription` |
| `services/{wake-stt,wake-orchestrator,tts-service,voiceprint-service}/tests/**/*.py` | `pytest` | `.` | `aux` |
| `libs/meeting-contracts/tests/**/*.py` | `pytest` | `.` | `integrations` |
| `services/{workload-broker,workload-access-broker,runtime-network-policy-agent}/tests/**/*.py` | `pytest` | `.` | `backend` |
| `services/{mcp,telegram-bot}/**/test*.py`, `packages/vexa-{client,cli}/**/test*.py` | `pytest` | `.` | `integrations` |
| `tests3/**/*.py`, `services/{meeting-api,runtime-api,admin-api,agent-api,api-gateway,calendar-service}/**/test*.py` | `pytest` | `.` | `backend` |
| `services/dashboard/**/*.test.ts` | `vitest` | `services/dashboard` | `null` |
| `packages/transcript-rendering/**/*.test.ts` | `vitest` | `packages/transcript-rendering` | `null` |
| `services/vexa-bot/core/**/*.test.ts` | `core-registry` | `services/vexa-bot/core` | `null` |
| `services/vexa-bot/tests/test_zoom_runner_auth.sh` | `shell-json` | `.` | `null` |

`shell-json`は`["bash","services/vexa-bot/tests/test_zoom_runner_auth.sh","--report-json",<task-owned report path>,"--case",<case>...]`だけを許し、case列はRF-06F/RF-06Gのcompletionに列挙した順、stdout JSONの`required_test_names`とexact一致させる。その他のshell/direct/Docker commandはA.3/A.5にだけ登録する。

### A.2 fallback supplemental mapping

| IDs | 作成するexact path | runner / cwd / venv / argv |
|---|---|---|
| `RF-00C` (`always`) | `services/dashboard/tests/refactor/rf_00c.test.ts` | `vitest` / `services/dashboard` / `null` / `["tests/refactor/rf_00c.test.ts"]` |
| `RF-00C` (`always`) | `services/vexa-bot/core/src/refactor-tests/rf_00c.test.ts` | `core-registry` / `services/vexa-bot/core` / `null` / `["--file","src/refactor-tests/rf_00c.test.ts"]` |
| `RF-00B, RF-02, RF-03B, RF-05A, RF-05D1, RF-05D1B, RF-05D2, RF-05E, RF-05F, RF-06C1, RF-06C2, RF-06D1, RF-06D2, RF-06E, RF-06H, RF-06I1, RF-06I2, RF-06I3, RF-24, RF-25, RF-26, RF-64` | `tests3/unit/refactor/test_<N(ID)>.py` | `pytest` / `.` / `backend` / `[path]` |
| `RF-00D, RF-01, RF-31, RF-32, RF-33, RF-34, RF-35, RF-36, RF-37, RF-38, RF-39, RF-40, RF-41, RF-42, RF-43, RF-44, RF-45A, RF-46, RF-47, RF-48, RF-50, RF-51, RF-75F` | `tests3/unit/refactor/test_<N(ID)>.py` | `pytest` / `.` / `backend` / `[path]` |
| `RF-11, RF-52, RF-53, RF-54, RF-55, RF-56, RF-57, RF-58, RF-59, RF-65, RF-74G` | `services/meeting-api/tests/refactor/test_<N(ID)>.py` | `pytest` / `.` / `backend` / `[path]` |
| `RF-60, RF-61, RF-62, RF-63` | `services/transcription-service/tests/refactor/test_<N(ID)>.py` | `pytest` / `.` / `transcription` / `[path]` |
| `RF-12, RF-15, RF-16, RF-17, RF-19, RF-20, RF-21, RF-22, RF-23, RF-67, RF-68, RF-69, RF-70, RF-71, RF-74E, RF-74F, RF-74I, RF-75A, RF-75B, RF-75C` | `services/dashboard/tests/refactor/<N(ID)>.test.ts` | `vitest` / `services/dashboard` / `null` / `["tests/refactor/<N(ID)>.test.ts"]` |
| `RF-08, RF-09A, RF-09B, RF-72, RF-73, RF-74C, RF-74D` | `services/vexa-bot/core/src/refactor-tests/<N(ID)>.test.ts` | `core-registry` / `services/vexa-bot/core` / `null` / `["--file","src/refactor-tests/<N(ID)>.test.ts"]` |
| `RF-27` | `services/wake-stt/tests/refactor/test_rf_27.py` | `pytest` / `.` / `aux` / `[path]` |
| `RF-28` | `services/wake-orchestrator/tests/refactor/test_rf_28.py` | `pytest` / `.` / `aux` / `[path]` |
| `RF-29` | `services/tts-service/tests/refactor/test_rf_29.py` | `pytest` / `.` / `aux` / `[path]` |
| `RF-30` | `services/voiceprint-service/tests/refactor/test_rf_30.py` | `pytest` / `.` / `aux` / `[path]` |

`<N(ID)>`はplaceholder文字列としてfile名へ残さず、上のnormalizationを適用したliteralへ展開する。例えばRF-64のpathは`tests3/unit/refactor/test_rf_64.py`、RF-75Cは`services/dashboard/tests/refactor/rf_75c.test.ts`。

### A.3 test fileを作らないargv-only item

| ID | runner | cwd | argv | assertion name | expected object |
|---|---|---|---|---|---|
| `RF-00N` | `argv` | `.` | `["/usr/bin/python3","-I","-S","scripts/test/test_gitnexus_refactor_bootstrap.py","--verify-committed-artifacts"]` | `rf_00n_committed_artifacts` | `{"exit_code":0,"stdout_exact":"gitnexus-bootstrap-committed: pass\n"}` |
| `RF-74H` | `argv` | `.` | `["test","!","-e","services/dashboard/vexaai-transcript-rendering-0.2.0.tgz"]` | `rf_74h_archive_absent` | `{"exit_code":0}` |
| `RF-75D` | `argv` | `.` | `["node","services/dashboard/scripts/check-lint-cluster.mjs","--all","--expect-errors","0","--expect-warnings","0"]` | `rf_75d_lint_zero_errors_warnings` | `{"exit_code":0,"stdout_regex":"(^|\\s)errors=0 warnings=0(\\s|$)"}` |

これらも`commands[]`は空にしない。表のassertion name/operator/expected objectを1 byteも変えずmachine reportへ入れる。RF-74Hの参照検索はrequired suite/補助test、RF-75Dのraw ESLintは`V-DASH-FINAL`で追加実行する。

### A.4 runnerとrequired suiteのparity検査

RF-00Eの`test_refactor_item_runner.py`は次を固定する。

- 全見出しID集合=matrix ID集合。現在の見出し数をhard-codeせず、planから取得する。
- A.2掲載IDは、rule 2から同runner/cwdのfull test pathを取得できない場合だけ、展開後supplemental pathとrunner/cwd/venv/argvがbyte一致。`RF-00C (always)`の2行だけはrule 2の有無にかかわらず必須。
- A.3はargvがbyte一致。
- A.5は1 argv配列=1 commandとして、順序を含めbyte一致。Docker commandより前にlocal-context preflightが実行されたことをrunner reportへ残す。
- 本文のfull test pathと`required_test_names`がmatrixから欠落していない。
- brace省略文字列をargvへ含めない。
- 削除対象・archive・source pathをtest commandとして登録しない。
- 本文で`exact command`とした直接command集合=A.3/A.5のargv command集合。
- `required_suites`は項目の`完了条件`と集合一致。
- item commit時にmatrixの`planned -> active`と、そのcommandが指すtest pathの追加または変更が同じdiffへ存在する。新規testはfile作成、既存testは少なくとも本文で要求したcase追加/更新のdiffを必須とする。pure argv-onlyのRF-74H/RF-75Dと、characterizationを既存test変更なしで再利用すると本文に明記したitemだけはtest file差分を要求しない。

### A.5 test以外のexact argv mapping

各JSON配列を独立した`runner="argv"` commandへし、表の順序で実行する。同じIDの前commandが非0なら後続を実行しない。host側で文字列をshell評価してはいけない。`docker run`内の`sh -lc`はimage内部の固定smoke commandであり、host commandは配列のまま実行する。

A.5の各commandのassertion nameは、IDをA.1 rule 7で正規化した値と、そのID内で上から0始まり・2桁zero padしたcommand indexを結合した`<N(ID)>_argv_<NN>`とする（例: RF-05C先頭は`rf_05c_argv_00`）。各expected objectは表の「exit 0」または「各exit 0」を当該commandごとの`{"exit_code":0}`へ機械展開する。stdout/stderr条件が表にないcommandへ追加条件を推測しない。A.3は同表のsemantic assertion nameを優先する。全A.3/A.5 commandでoperatorはliteral `argv_expectation`、assertion resultはexact 1件である。

| ID | cwd | argv（上から順） | expected |
|---|---|---|---|
| `RF-00C` | `.` | `["bash","services/dashboard/scripts/capture-refactor-baseline.sh"]` | exit 0 |
| `RF-05C` | `.` | `["bash","-n","services/vexa-agent/system/bin/vexa"]`<br>`["bash","services/vexa-agent/tests/test_vexa_public_routing.sh"]`<br>`["docker","build","-f","services/vexa-agent/Dockerfile","-t","rf05c-vexa-agent","."]`<br>`["docker","run","--rm","rf05c-vexa-agent","sh","-lc","command -v vexa && vexa --help >/dev/null"]` | 各exit 0 |
| `RF-05D1B` | `.` | `["docker","build","-f","services/vexa-bot/Dockerfile","-t","rf05d1b-meeting","services/vexa-bot"]`<br>`["docker","build","-f","services/vexa-bot/core/Dockerfile","-t","rf05d1b-browser","services/vexa-bot/core"]`<br>`["docker","build","-f","services/vexa-agent/Dockerfile","-t","rf05d1b-agent","."]`<br>`["docker","run","--rm","--entrypoint","sh","--read-only","--tmpfs","/tmp","--tmpfs","/run","--cap-drop","ALL","--security-opt","no-new-privileges","rf05d1b-meeting","-lc","test \"$(id -u)\" -ne 0 && test ! -w / && test -w /tmp"]`<br>`["docker","run","--rm","--entrypoint","sh","--read-only","--tmpfs","/tmp","--tmpfs","/run","--cap-drop","ALL","--security-opt","no-new-privileges","rf05d1b-browser","-lc","test \"$(id -u)\" -ne 0 && test ! -w / && test -w /tmp"]`<br>`["docker","run","--rm","--entrypoint","sh","--read-only","--tmpfs","/tmp","--tmpfs","/run","--cap-drop","ALL","--security-opt","no-new-privileges","rf05d1b-agent","-lc","test \"$(id -u)\" -ne 0 && test ! -w / && test -w /tmp"]` | 各exit 0。`$(...)`はcontainer内固定`sh -lc`だけで評価 |
| `RF-10` | `.` | `["bash","scripts/test/install-refactor-local-packages.sh"]`<br>`["docker","build","-f","services/meeting-api/Dockerfile","-t","rf10-meeting","."]`<br>`["docker","build","-f","services/mcp/Dockerfile","-t","rf10-mcp","."]`<br>`["docker","build","-f","services/telegram-bot/Dockerfile","-t","rf10-telegram","."]`<br>`["docker","build","-f","deploy/lite/Dockerfile.lite","-t","rf10-lite","."]`<br>`["docker","run","--rm","rf10-meeting","python","-c","from meeting_contracts.url import ParsedMeetingUrl"]`<br>`["docker","run","--rm","rf10-mcp","python","-c","from meeting_contracts.url import ParsedMeetingUrl"]`<br>`["docker","run","--rm","rf10-telegram","python","-c","from meeting_contracts.url import ParsedMeetingUrl"]`<br>`["docker","run","--rm","--entrypoint","python","rf10-lite","-c","from meeting_contracts.url import ParsedMeetingUrl"]` | 各exit 0 |
| `RF-65` | `.` | `["bash","scripts/test/install-refactor-local-packages.sh"]`<br>`["docker","build","-f","services/meeting-api/Dockerfile","-t","rf65-meeting","."]`<br>`["docker","build","-f","services/admin-api/Dockerfile","-t","rf65-admin","."]`<br>`["docker","build","-f","services/calendar-service/Dockerfile","-t","rf65-calendar","."]`<br>`["docker","build","-f","deploy/lite/Dockerfile.lite","-t","rf65-lite","."]`<br>`["docker","run","--rm","rf65-meeting","python","-c","import meeting_api.models as old; import meeting_models.models as new; assert old.Meeting is new.Meeting"]`<br>`["docker","run","--rm","rf65-admin","python","-c","import meeting_models.models as new; from meeting_api import models as old; assert old.Meeting is new.Meeting"]`<br>`["docker","run","--rm","rf65-calendar","python","-c","import meeting_models.models as new; from meeting_api import models as old; assert old.Meeting is new.Meeting"]`<br>`["docker","run","--rm","--entrypoint","python","rf65-lite","-c","import meeting_models.models as new; from meeting_api import models as old; assert old.Meeting is new.Meeting"]` | 各exit 0 |
| `RF-66C` | `.` | `["docker","build","-f","services/meeting-api/Dockerfile","-t","rf66-meeting","."]`<br>`["docker","build","-f","services/admin-api/Dockerfile","-t","rf66-admin","."]`<br>`["docker","build","-f","services/calendar-service/Dockerfile","-t","rf66-calendar","."]`<br>`["docker","build","-f","services/api-gateway/Dockerfile","-t","rf66-gateway","."]`<br>`["docker","run","--rm","rf66-meeting","python","-c","import meeting_api, meeting_contracts, meeting_models"]`<br>`["docker","run","--rm","rf66-admin","python","-c","import importlib.util, meeting_contracts, meeting_models; assert importlib.util.find_spec('meeting_api') is None"]`<br>`["docker","run","--rm","rf66-calendar","python","-c","import importlib.util, meeting_contracts, meeting_models; assert importlib.util.find_spec('meeting_api') is None"]`<br>`["docker","run","--rm","rf66-gateway","python","-c","import importlib.util, meeting_contracts, meeting_models; assert importlib.util.find_spec('meeting_api') is None"]` | 各exit 0 |
| `RF-75A` | `.` | `["node","services/dashboard/scripts/check-lint-cluster.mjs","--rules","@typescript-eslint/no-require-imports,react/no-unescaped-entities","--expect","0"]` | exit 0 |

### A.6 strict xfail ownership

次のnodeidだけが`known_xfails`へ入る。角括弧内parameter IDを省略せず、owner test file全体をxfailしない。`resolved_by` itemがactiveになるcommitで該当caseだけmarkerを除去し、通常passを確認する。

| owner | exact nodeid | resolved_by |
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

フェーズinventoryを採取する`rg/git grep/git ls-files`、GitNexus impact、status、hash、manifest read-only assertionはscope discoveryでありitem completion commandではないためA.3/A.5登録外を許す。ただし実装/testを実行する直接commandはA.3/A.5またはmatrix runner以外から呼ばない。

## Appendix B. fixture E2E visual contract

authoritative machine-readable sourceは`planned-visual-changes.json`である。本文だけを読んでも操作を取り違えないよう、9 scenarioを次に固定する。各scenarioをdesktop `1440x900`とmobile `390x844`で1枚ずつ、合計18枚撮る。

| ID | route / 主操作 | visual差 | 関連RF / 許可領域 | final semantic assertion |
|---|---|---|---|---|
| S01 | `/meetings`; numeric ID `1001/1002`のlist fixtureを2回取得して表示 | なし | なし | `list-ready`、cards 1001/1002可視 |
| S02 | `/meetings/1001`; native ID=`fixture-native-a`のcompleted detail | あり | RF-13,14 / `[data-testid="transcript-segment-list"]` | detail A、戻るボタン、speaker/stream重複0、absolute順序 |
| S03 | `/meetings/1001?fixture=literal-search`; baseline=`fixture`、final=`[` | あり | RF-12 / search panel・results・segment listの3 selector | page/console error 0、`[` match 1 |
| S04 | `/meetings/1001?fixture=fast-switch`; Aから一覧へ戻りcard 1002を選ぶ | なし | なし | detail B、A segment 0。RF-15 raceはunit deferred-promise testが所有 |
| S05 | `/meetings/1001?fixture=post-meeting`; `needs-human`を2回確認後、fake clock 5000ms、3回目で`completed` | なし | なし | completed表示。RF-17 single-flightはunit testが所有 |
| S06 | `/meetings/1001?fixture=browser-starting`; detailを2回取得しbrowser raw status=`requested` | あり | RF-20 / `[data-testid="browser-session-panel"]` | display state=`starting` |
| S07 | `/meetings/1001?fixture=audio-player`; recording一覧は空、transcript内audio ID 701からmaster/mediaを取得 | なし | なし | audio player可視。再生retry有限性はRF-21 unit testが所有 |
| S08 | `/meetings/1001?fixture=video-player`; recording一覧は空、transcript内audio 701/video 702から両master/mediaを取得 | なし | なし | audio/video player可視。imperative再生APIはRF-22 unit testが所有 |
| S09 | `/meetings/1001?fixture=live-follow`; fake WS subscribe後にsegment `live-044`をemitし、計測後scrollを0へ戻す | なし | behavioral RF-18、visual selectorなし | outer app scroll不変、inner transcript scroll増加、final WS token 0 |

action DSLはJSONに列挙した`goto,wait-request-count,wait-locator,fill,click,advance-fake-clock,set-scroll,record-scroll,wait-animation-frames,wait-fixture-websocket,emit-fixture-websocket`だけ。unknown opcode、field不足/余分、locator 0/複数、負のcount、fixture placeholder未解決はfatal。`wait-request-count`はopcode区切り後の**最後の`=`**だけをcount区切りにし、query内の`=`をpathの一部として保持する。各mode/scenario/viewportでnew BrowserContextを作り、最初のactionより前にcommon profile、scenario binding、fake WebSocketを全てinstallする。HTTPはmethod・origin class・raw path・raw queryをbyte一致させ、query順も変えない。`/api/**`、未宣言WS/SSE、外部通信はunboundなら即失敗し、Next main/RSC/staticと既存brand/icon assetだけを限定pass-throughする。fixture contractはmaterialize後の各bindingについて`scenario_id,mode,method,raw_path_query,fixture,status,content_type,tracked_authoring_sha256,materialized_body_sha256,materialization_receipt_sha256`をRF-00C test内snapshotへ固定し、pathやfixture名だけを見て任意bodyを作らない。binary mediaだけはJSONのfull state tableとHEAD/body-bearing GET/max 4 request契約を許し、それ以外の追加requestを拒否する。

撮影barrierは、全non-binary fixtureがexact count、binaryはbody-bearing GETが1回以上かつ上限内、S09はsubscribe受理・emit処理済み、in-flight=0が500ms継続、`document.fonts.ready`、2回の`requestAnimationFrame`の順に全て満たす。manifestはbaseline/finalとも`browser_contexts[]`と、同じ`context_id`を持つ`action_results[]`をaction順に保持し、action結果の詳細は`observations`へ入れる。finalの`assertion_results[{context_id,name,observed_field,operator,expected,actual,status}]`だけがassertion fieldを持つ。pixel diffは8-bit RGBAのいずれか1 channelが異なるpixelを1差分pixelとし、`pixel_diff_ratio=diff_pixel_count/(width*height)`。stable scenarioとS09は0、planned scenarioはselector矩形外0を要求する。
