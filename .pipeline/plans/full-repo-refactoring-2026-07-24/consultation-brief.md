# Fable Consultation Brief: full-repo-refactoring-2026-07-24

Generated: 2026-07-24T03:22:53.291854+00:00
Provider target: claude-fable-cli
Mode: plan
Model: fable

## Safety And Boundaries

- You are an advisory reviewer, not the implementer.
- Use local file reads and read-only shell inspection only.
- Do not edit files, run write commands, commit, push, install dependencies, or change state.
- Keep the answer concise. Each finding should be at most two sentences plus evidence.
- Treat your answer as advisory review evidence. Local tests, source checks, and project evidence remain the source of truth.

## Required Context Summary

### 1. Original Task And Plan Step

Task id: full-repo-refactoring-2026-07-24
Plan step or checkpoint: 全体リファクタリング計画の実行順・安全性・独立検証性の監査

Relevant plan artifacts:

```text
## request.md

# 依頼要約: 全リポジトリ・リファクタリング計画

## 目的

`generic_tldv` 全体を読み、現在の構造、依存関係、重複、巨大関数、責務混在、
デッドコード、命名のずれ、エラー処理の穴、直値の散在を把握したうえで、
別の実行AIが迷わず安全に実施できるリファクタリング計画を作る。

## 制約

- この計画作成では実装コードを変更しない。
- 計画は日本語で書く。
- 実装は1項目ずつ実行し、1項目につき1コミットとする。
- 各項目へ、基準SHA時点の対象パスと行範囲、問題、具体的な変更、完了コマンドと
  期待結果、リスク、戻し方、依存項目を書く。
- 安全網を項目0として先に構築する。
- 機能追加、未承認の公開仕様変更、依存更新、テスト弱体化を混ぜない。
- 実行順を先頭からトレースし、後続の前提を前工程が壊さないことを確認する。

## 合格条件

この計画書と基準SHAのコードだけを渡された実行者が、追加の設計判断をせず、
項目ごとの停止条件とロールバック境界を守りながら完遂できること。


## plan.md

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

この計画は1本の巨大PRを意味しない。各フェーズを別PRにしてよい。ただし、コミット順と依存順は変えず、後述のフェーズゲートを越えるまで次フェーズへ進まない。

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
| Transcript UI | `components/transcript-viewer.tsx` | 検索、scroll、話者編集、voiceprint、再文字起こし | transcript manager、API |
| Transcript state | `lib/meetings-store.ts`, `packages/transcript-rendering/src/*` | API結果、confirmed/pending、dedup、timeline | Dashboard API、WebSocket |
| Browser session | Dashboard、`meetings.py`、`core/src/browser-session.ts` | VNC、workspace git、save storage | Gateway、Redis Pub/Sub |
| Bot core | `services/vexa-bot/core/src/index.ts` | platform起動、音声、Browser、command、shutdown | platform modulesとの逆import |
| Calendar | `services/calendar-service/app/main.py`, `sync.py` | OAuth、予定同期、自動投入 | Admin/Meetingのmodelを直接import |
| Agent | `services/agent-api/agent_api/*` | session、container、workspace、chat | Admin user data、Docker/K8s |
| Test registry | `tests3/test-registry.yaml`, `checks/registry.json`, `registry.yaml` | test列挙、dispatcher、集計 | Makefile、shell、Python |
| Deploy | `deploy/compose`, `deploy/lite`, `deploy/helm`, GCP workflows | local/prod起動、image publish、Cloud Run | Docker、Helm、GitHub Actions |
| Managed Harness | `scripts/harness`, `.claude/hooks`, `.pipeline`, `schemas` | worktree、build/evidence/gate/outcome | shell、embedded Python、Git |

### 1.3 構造上の重要事実

- 調査時の規模は約161,809 LOC。
- 特に大きい実装は `gemini_adapter.py` 3,936行、`vexa-bot/core/src/index.ts` 2,830行、`api-gateway/main.py` 2,667行、`meetings.py` 2,481行、Meeting detail page 2,452行、`final_transcription.py` 1,608行、`TranscriptViewer` 1,486行。
- `meeting-api` のlifecycle周辺は9モジュールの循環を関数内importで回避している。
- Calendar/AdminのDocker imageは共有契約のためにMeeting API packageを丸ごとinstallしている。
- `services/README.md` が示すservice separationと、実際のmodel/database直接importが一致していない。
- GitNexus indexは調査時点で `f9c3b36` を指し、現HEADより古い。FTS/embeddingも利用不能だったため、旧graphのimpact結果を最新ソースの直接読解とimport/AST解析で補完した。
- GitNexus上、`run_deferred_transcription` の上流影響はCRITICAL、`_plan_exact_boundary_stream_consumption` はMEDIUM。前者を一括編集してはいけない。
- BrowserでローカルDashboard `http://127.0.0.1:3002` の会議一覧・会議詳細を読み取り確認した。ただし稼働imageは調査HEADより約10日古く、現HEADの視覚的合否には使えない。現HEADでのスクリーンショットは項目0と最終gateで取得する。

### 1.4 問題一覧と優先度

| 優先度 | 問題 | 根拠となる対象 |
|---|---|---|
| P0 | Calendar/Agentが認証subjectではなくquery/body `user_id` を信頼する | Gateway `main.py:322-419`、Calendar `main.py:85-182`、Agent `main.py:243-475` |
| P0 | Ag

[truncated; verify against the source artifact]

## verification-contract.md

# Verification Contract: full-repo-refactoring-2026-07-24

## 判定原則

- size: L
- 1作業項目 = 1コミット。
- 各コミットは項目固有テストと、その項目が属するサービスのfull suiteを通す。
- 既存失敗を「今回無関係」と口頭で無視しない。項目0のmachine-readable baselineから
  件数・対象が増えていないことを証明する。
- `pytest` は `PYTHONDONTWRITEBYTECODE=1` と `-p no:cacheprovider` を付ける。
- build/test後に `git status --short` を確認し、生成差分をコミットへ混ぜない。
- live meeting、コンテナ再起動、GCP deployは項目固有の許可がある最終段階まで行わない。

## サービス別required command

```bash
# Dashboard
cd services/dashboard
npm test
npm run typecheck
npm run lint
VEXA_API_URL=http://localhost:8056 npm run build

# transcript-rendering
cd packages/transcript-rendering
npm test
npm run typecheck

# Vexa Bot
cd services/vexa-bot/core
npx tsc --noEmit --incremental false
npm test

# Meeting API
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider \
  services/meeting-api/tests \
  --ignore=services/meeting-api/tests/test_integration_live.py -q

# API Gateway / Admin / Agent / Calendar / Runtime
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider \
  services/api-gateway/tests services/admin-api/tests services/agent-api/tests \
  services/calendar-service/tests services/runtime-api/tests -q

# Transcription
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider \
  services/transcription-service/tests -q
```

実際のpackageに `typecheck` scriptがない場合、項目0でpackage.jsonを確認し、同じ処理を
`npx tsc --noEmit` で実行する。script追加はこの計画の目的ではない。

## 構造・gate

```bash
bash -n $(rg --files -g '*.sh')
"$RF_ENV_ROOT/backend/bin/python" tests3/docs/check.py
python3 -m unittest discover -s tests3/unit -v
make -C tests3 smoke
node .gitnexus/run.cjs detect_changes --scope compare --base_ref main
bash .claude/hooks/pr-ready-gate.sh full-repo-refactoring-2026-07-24
bash scripts/harness/outcome-judge.sh full-repo-refactoring-2026-07-24
```

GitNexus indexがstaleなら、実装コードを変更する前に専用コミット外で再解析する。
再解析がAGENTS等を変更する版なら、その生成差分を捨てずに中断し、runner更新を報告する。

## Browser最終確認

基準画面はカボスDashboard:

- 会議一覧
- 完了会議詳細
- 文字起こし検索（`[`、`(`、`\`、`.` をリテラルとして扱う）
- 会議A→Bの高速切替
- post-meeting polling
- Browser Session starting/active/terminal表示
- Audio/Video source切替

Playwrightでdesktop 1440×900とmobile 390×844を撮影する。スクリーンショット、
console error、network失敗をevidenceへ保存し、サービスコードのHEADとデプロイ対象SHAが
一致していることを先に記録する。

## 即時中断条件

- 公開request/response schema、status code、Redis key prefix、DB metadataに予定外差分。
- golden segmentのtext/speaker/timestamp順に1件でも差分。
- 認証mismatchが2xx、秘密値がresponse/log/URLへ出る。
- callback種別で同じTerminalSignalの結果が異なる。
- test 0件、全skip、missing scriptなのにgateが0。
- 項目外ファイルの変更、既存ユーザー成果物のstage、lockfile差分。
- 同じテストが2回連続で失敗した場合は実装を止め、Fable phase reviewを行う。

## Evidence

- `.pipeline/evidence/full-repo-refactoring-2026-07-24/evidence-manifest.json`
- `.pipeline/evidence/full-repo-refactoring-2026-07-24/evidence-pack.md`
- `.pipeline/evidence/full-repo-refactoring-2026-07-24/qa-judgment.json`
- `.pipeline/sessions/full-repo-refactoring-2026-07-24/events.jsonl`
- `.pipeline/outcomes/full-repo-refactoring-2026-07-24/outcome-card.json`


## option-matrix.md

# Option Matrix: リファクタリング戦略

評価は5点満点。安全性と実行者の判断量を最重視する。

| 選択肢 | 概要 | 安全性 | 価値到達 | 途中rollback | 実行者の判断量 | 結論 |
|---|---|---:|---:|---:|---:|---|
| A. 巨大ファイルから先に分割 | 行数の大きい順にmodule/componentへ移す | 1 | 2 | 2 | 4 | 不採用 |
| B. 将来MANIFESTへ一括移行 | `contracts/` と `packages/` を先に作り全サービスを移す | 2 | 3 | 1 | 5 | 不採用 |
| C. 境界修正→契約固定→move-only抽出 | P0バグを独立修正し、goldenを置いて責務を段階移動 | 5 | 5 | 5 | 1 | 採用 |
| D. 問題修正だけで分割しない | セキュリティ・競合だけ直し構造は維持 | 4 | 3 | 5 | 1 | 不採用 |

## 採用理由

選択肢Cなら、認証・状態遷移・非同期競合・検証の偽陽性を先に正せる。後半の抽出は、
固定済みの契約を保つmove-only変更になり、項目単位でrevertできる。

## 重要な設計選択

| 論点 | 採用 | 不採用 |
|---|---|---|
| 認証主体 | Gateway解決済みsubjectを下流の唯一の主体とする | body/queryの `user_id` を信頼 |
| 公開User JSON | allow-list化した公開DTO | 任意JSONに対する秘密名deny-listだけ |
| Agent scope | 既存 `browser` scopeへ割当。新scopeは作らない | 未承認の `agent` scope追加 |
| Calendar scope | `bot` | 新scope追加 |
| Recording scope | `tx` | `bot`とのOR許可 |
| Browser保存 | subscribe-before-publish + request別reply channel | 共有channelのplain `done` |
| URL parser | pure shared package +旧import re-export | サービスごとの正規表現追加 |
| ORM分離 | metadata snapshot後にshared model packageへmove | DB migrationとの同時実施 |
| `tests3` | fail-closed化し、役割を明記して段階縮小 | 一括削除または過去feature sidecarの復元 |
| UI分割 | race/polling/orderを修正後に抽出 | 2,000行componentを先に分割 |

## 採用しない拡張

- Redis Streamsへの全面置換
- token保存の暗号化方式変更
- 新しいAPI version、status、scope、DB schema
- React/Next.js/FastAPI/Redis等の依存更新
- 0.11モジュール全面移行


## research-brief.md

# Research Brief: 全リポジトリ・リファクタリング

## 問いの言い換え

元の問いは「巨大ファイルや重複をどう分割するか」ではない。実際に解くべき問いは、
「現在の外部契約と状態遷移を先に固定し、既知の境界バグを別ファイルへ温存せず、
検証基盤の偽陽性を除いたうえで、責務を段階的に移動するにはどの順番が安全か」である。

## 事前仮説

1. 最大のリスクはファイル長ではなく、認証主体、会議ライフサイクル、非同期状態、
   Pub/Sub応答の所有者が複数箇所へ分散していること。
2. 既存テスト数は多いが、ルート検証基盤には「未実行でも成功」に見える経路があり、
   全体の安全網としてはそのまま信用できない。
3. `MANIFEST.md` のモジュール化方針は参考になるが、別ブランチを前提とする将来仕様を
   現在の `main` へそのまま適用すると、現行サービスとデプロイ契約を壊す。

## 反証確認

- Dashboard 28ファイル・199テスト、`transcript-rendering` 83テスト、
  Vexa Bot型検査は成功しており、「テストが存在しない」は誤りだった。
- 一方、Dashboard lintは既存61 errors / 87 warnings、`tests3` は91登録中45スクリプトが
  不在、0 report / 0 featureでもgate成功可能であり、「既存gateが全体を証明する」は
  反証された。
- 起動済みカボスDashboardを `http://127.0.0.1:3002` で読み取り確認できたが、
  イメージは10日前で現HEADと同一ではない。実画面は補助証拠であり、HEADの静的確認と
  テストを置き換えない。
- `MANIFEST.md` は inventory を `feat/extension-in-tab-capture` 基準と明記し、
  statusも「merge後にbinding」である。現行 `main` の確定仕様としては扱わない。

## 現在の一次資料

- 基準コード: `main` / `b2bcae8e88f0e73fe95343ee3a694a3afc4e1028`
- リポジトリ内:
  - `AGENTS.md`
  - `docs/managed-agent-harness-architecture.md`
  - `docs/agent-coding-best-practices.md`
  - `services/README.md`
  - `MANIFEST.md`
  - `tests3/README.md`
  - 実装、テスト、Compose、CI、Harnessスクリプト
- Redis公式: [Pub/Subはat-most-onceで、購読中でない受信者はメッセージを失う](https://redis.io/docs/latest/develop/use-cases/pub-sub/)
- OWASP公式:
  - [API1 Broken Object Level Authorization](https://owasp.org/API-Security/editions/2019/en/0xa1-broken-object-level-authorization/)
  - [API3 Broken Object Property Level Authorization](https://owasp.org/API-Security/editions/2023/en/0xa3-broken-object-property-level-authorization/)
- FastAPI公式:
  - [`APIKeyHeader(auto_error=False)` はヘッダー欠落時に `None` を返す](https://fastapi.tiangolo.com/ja/reference/security/)

## 確定した示唆

1. `user_id` をquery/bodyから採用する経路は、Gatewayが注入する認証主体へ統一する。
2. 公開レスポンスは汎用JSONを後からdeny-listするのでなく、公開可能フィールドを
   allow-listする。
3. Browser保存の短い同期応答にはPub/Subを使い続けてもよいが、専用reply channelへ
   先にsubscribeし、`request_id` を照合する。永続再実行保証は今回追加しない。
4. 先に振る舞いとside-effect順をgolden/特性テスト化し、その後にmove-only抽出を行う。
5. `tests3` は即時全面廃止も全面復元もせず、偽陽性をfail-closed化し、現行Managed
   Harnessとサービス別テストへ権威を明示する。

## 信頼度と覆る条件

- 信頼度: 高（現行コード、テスト実行、import/AST解析、起動済み画面、公式一次資料で確認）。
- 次の場合は計画を止めて再調査する:
  - 実行開始時のHEADが基準SHAから変わり、対象シンボルまたは公開契約が変更済み。
  - `MANIFEST.md` の前提ブランチが `main` へ正式統合され、契約/gateが実装済み。
  - Agent APIのshipping方針またはtoken scope仕様が別の承認済み文書で確定。
  - 本番でBrowser保存に永続保証が必要と判明し、Pub/Sub継続が要件を満たさない。

## 時間で陳腐化する前提

- 起動済みDockerイメージの年齢、テスト成功数、lint件数、GitNexus index freshness。
- これらは実行項目0で必ず再計測する。

```

### 2. Approaches Tried And Failure Reasons

```text
[none recorded for this consultation]
```

### 3. Current Hypothesis

[not specified]

### 4. Questions To Decide

1. 別AIが追加判断なしで各項目を1コミットずつ安全に実行できるか
2. 先行項目が後続項目の前提を壊す順序矛盾や、rollback不能な項目がないか
3. 重要な問題の見落とし、曖昧な完了条件、禁止事項の不足がないか

## Decision Or Result Under Review

境界修正、検証基盤fail-closed化、契約固定、move-only抽出の順で実行する

## Context Coverage

```json
[
  {
    "source": "extra-source",
    "included_chars": 8000,
    "omitted_chars": 94728
  },
  {
    "source": "plan:plan.md",
    "included_chars": 5000,
    "omitted_chars": 97728
  }
]
```

## Extra Context

```text
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

この計画は1本の巨大PRを意味しない。各フェーズを別PRにしてよい。ただし、コミット順と依存順は変えず、後述のフェーズゲートを越えるまで次フェーズへ進まない。

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
| Transcript UI | `components/transcript-viewer.tsx` | 検索、scroll、話者編集、voiceprint、再文字起こし | transcript manager、API |
| Transcript state | `lib/meetings-store.ts`, `packages/transcript-rendering/src/*` | API結果、confirmed/pending、dedup、timeline | Dashboard API、WebSocket |
| Browser session | Dashboard、`meetings.py`、`core/src/browser-session.ts` | VNC、workspace git、save storage | Gateway、Redis Pub/Sub |
| Bot core | `services/vexa-bot/core/src/index.ts` | platform起動、音声、Browser、command、shutdown | platform modulesとの逆import |
| Calendar | `services/calendar-service/app/main.py`, `sync.py` | OAuth、予定同期、自動投入 | Admin/Meetingのmodelを直接import |
| Agent | `services/agent-api/agent_api/*` | session、container、workspace、chat | Admin user data、Docker/K8s |
| Test registry | `tests3/test-registry.yaml`, `checks/registry.json`, `registry.yaml` | test列挙、dispatcher、集計 | Makefile、shell、Python |
| Deploy | `deploy/compose`, `deploy/lite`, `deploy/helm`, GCP workflows | local/prod起動、image publish、Cloud Run | Docker、Helm、GitHub Actions |
| Managed Harness | `scripts/harness`, `.claude/hooks`, `.pipeline`, `schemas` | worktree、build/evidence/gate/outcome | shell、embedded Python、Git |

### 1.3 構造上の重要事実

- 調査時の規模は約161,809 LOC。
- 特に大きい実装は `gemini_adapter.py` 3,936行、`vexa-bot/core/src/index.ts` 2,830行、`api-gateway/main.py` 2,667行、`meetings.py` 2,481行、Meeting detail page 2,452行、`final_transcription.py` 1,608行、`TranscriptViewer` 1,486行。
- `meeting-api` のlifecycle周辺は9モジュールの循環を関数内importで回避している。
- Calendar/AdminのDocker imageは共有契約のためにMeeting API packageを丸ごとinstallしている。
- `services/README.md` が示すservice separationと、実際のmodel/database直接importが一致していない。
- GitNexus indexは調査時点で `f9c3b36` を指し、現HEADより古い。FTS/embeddingも利用不能だったため、旧graphのimpact結果を最新ソースの直接読解とimport/AST解析で補完した。
- GitNexus上、`run_deferred_transcription` の上流影響はCRITICAL、`_plan_exact_boundary_stream_consumption` はMEDIUM。前者を一括編集してはいけない。
- BrowserでローカルDashboard `http://127.0.0.1:3002` の会議一覧・会議詳細を読み取り確認した。ただし稼働imageは調査HEADより約10日古く、現HEADの視覚的合否には使えない。現HEADでのスクリーンショットは項目0と最終gateで取得する。

### 1.4 問題一覧と優先度

| 優先度 | 問題 | 根拠となる対象 |
|---|---|---|
| P0 | Calendar/Agentが認証subjectではなくquery/body `user_id` を信頼する | Gateway `main.py:322-419`、Calendar `main.py:85-182`、Agent `main.py:243-475` |
| P0 | Agent SSEがtoken/API key欠落時にfail closedしない。route scope表も不完全 | Gateway `main.py:88-100,1565-1645`、Agent `auth.py:1-29` |
| P0 | `workspace_git.token` が公開User response/analytics responseへ出る | Meeting `schemas.py:339-349`、Admin `main.py:224-261,339-360` |
| P0 | Dashboard `/api/config` が `VEXA_API_KEY` をbrowserへ返し得る | Dashboard config route `:59-80` |
| P0 | workspace git commandがshell interpolationし、tokenをURLへ含め、失敗を成功表示し得る | `core/src/browser-session.ts:14-116` |
| P0 | Browser保存はpublish後subscribe、相関IDなし、timeout逆転 | Meeting `meetings.py:1307-1334`、core `browser-session.ts:177-203`、Gateway `main.py:2363-2386` |
| P0 | Harness task-idから `.pipeline` 外へpath traversalできる | `codex-session-ledger.sh:19-24`、`sml-decision.sh:15-20,76-89`、`worktree.sh:17-23,75-84` |
| P0 | Admin DB再作成にMeeting側と同じ破壊防止guardがない | `libs/admin-models/database.py:118-144`、Admin recreate script `:29-59` |
| P0 | tests3は登録script 91件中45件が不存在でも成功する | `test-registry.yaml`、`run-matrix.sh:168-176,209-221` |
| P0 | report 0件、feature 0件、全skip、0 stepでも合格し得る | `aggregate.py:164-213,559-650`、`common.sh:130-146` |
| P0 | Compose/Lite readinessとDB初期化が失敗しても成功表示する | Compose Makefile `:298-340`、Lite Makefile `:128-200` |
| P1 | transcript検索に生文字列をRegExpとして渡し、`[` 等で例外になり得る | `transcript-segment.tsx:57-69` |
| P1 | dedupが直前要素しか見ず、A/B/A重複を残す | `dedup.ts:27-142` |
| P1 | 複数sessionを相対 `start_time` だけで並べ、絶対timelineを壊す | `manager.ts:61-67`、`dedup.ts:227-251`、Meeting page `:318-351` |
| P1 | meeting切替後に旧requestが新meeting stateを上書きする | `meetings-store.ts:347-379,435-456,510-538` |
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

##

[truncated; verify against the source artifact]
```

## Current Git Status

```text
?? .pipeline/evidence/internal-tldv-adoption-roadmap-2026/
?? .pipeline/gates/internal-tldv-adoption-roadmap-2026/
?? .pipeline/outcomes/internal-tldv-adoption-roadmap-2026/
?? .pipeline/plans/full-repo-refactoring-2026-07-24/
?? .pipeline/plans/internal-tldv-adoption-roadmap-2026/
?? .pipeline/sessions/internal-tldv-adoption-roadmap-2026/
?? docs/2026-07-17_社内tl-dv置き換えロードマップ.md

```

## Current Diff Stat

```text

```

## Current Diff Excerpt

```diff

```

## Required JSON Output

Return only JSON matching this shape:

```json
{
  "type": "object",
  "additionalProperties": true,
  "required": [
    "verdict",
    "summary",
    "findings",
    "confidence"
  ],
  "properties": {
    "verdict": {
      "type": "string",
      "enum": [
        "MUST_FIX",
        "SHOULD_FIX",
        "SHIP"
      ]
    },
    "summary": {
      "type": "string"
    },
    "confidence": {
      "type": "string",
      "enum": [
        "low",
        "medium",
        "high"
      ]
    },
    "findings": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": true,
        "required": [
          "id",
          "severity",
          "title",
          "evidence",
          "recommendation"
        ],
        "properties": {
          "id": {
            "type": "string"
          },
          "severity": {
            "type": "string",
            "enum": [
              "MUST_FIX",
              "SHOULD_FIX",
              "NOTE"
            ]
          },
          "title": {
            "type": "string"
          },
          "evidence": {
            "type": "string"
          },
          "recommendation": {
            "type": "string"
          }
        }
      }
    },
    "local_verification": {
      "type": "array",
      "items": {
        "type": "string"
      }
    }
  }
}
```
