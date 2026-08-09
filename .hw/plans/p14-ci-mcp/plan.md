---
generated_by: fable
task_id: p14-ci-mcp
base-commit: 0c43bdff378a394857c3305be3186b6a1adb8d89
size: M
---

# ST-26(4/4): mcp のテスト収集を修理し単体 CI を追加する(P1-4 第4弾)

## ゴール

依頼の文字通りの内容: ST-26 の列挙は vexa-bot / transcription-service /
dashboard だが、調査で **mcp のテストが着手前から壊れている**(一度も収集
できた形跡がない)ことが判明した。

reframe(スコープ判断): mcp を P1-4 に**含める**。理由:
「テストは書かれたが CI が無いので壊れたまま誰も気づかない」は ST-26 の
問題意識(実効性のない検証)の最も進行した形であり、放置すればテスト資産が
腐り続ける。修理は小さく(conftest のパス1行 + 重複ファイル削除)、CI は
既存 test-api-gateway.yml の型がそのまま使える。ゴールは
**「mcp のテストが収集・実行され、壊れたら PR が赤くなる CI」**。

## 現状分析(現物確認済み、2026-08-09)

- `services/mcp/tests/` に test_health.py / test_parse_meeting_url.py の2本。
  いずれも fastapi_mcp / mcp.types を sys.modules スタブで差し替えてから
  main.py を import する設計(コンテナ外で pip 不能な fastapi-mcp への依存を
  回避済み)。設計は健全で、壊れているのは配線だけ。
- **バグ1**: `tests/conftest.py:8` が存在しない `packages/meeting-api` を
  sys.path に挿入(実体は `services/meeting-api`)。テストは
  `meeting_api.schemas` の Platform / MeetingCreate を import するため収集不能。
  なお sys.path 修正だけでは不十分: meeting_api.schemas は email-validator
  (meeting-api の `pydantic[email]` 依存)等を要求するため、**meeting-api を
  依存ごと install するのが正道**(sys.path ハックでは依存が入らない)。
- **バグ2**: `services/mcp/test_parse_meeting_url.py`(サービス直下)は tests/
  配下の同名ファイルの古い版(旧 sys.path ハック入り、スタブなし、341行 vs
  378行。diff で確認済み)。`pytest services/mcp/` と打つと古い版も収集され
  壊れる。上位互換の tests/ 版があるので削除する。
- mcp の CI は0。meeting-api の schemas に依存するサービスとして、
  test-api-gateway.yml と同じ path filter 流儀(push は schemas.py、PR は
  meeting-api 全体)が適用できる。
- **未知**: このテスト2本は一度も実行に成功した形跡がない。収集を直した結果
  fail する可能性がある(期待値の陳腐化 or 実バグの発見)。対応方針を §3 に定める。

## How

変更は4点: `services/mcp/tests/conftest.py`(修正)、
`services/mcp/test_parse_meeting_url.py`(削除)、
`.github/workflows/test-mcp.yml`(新規)、conftest 修正に伴う実行手順の
docstring 更新(conftest 内)。

### 1. `services/mcp/tests/conftest.py` の修理

- 存在しない `packages/meeting-api` への sys.path 挿入(7-9行目)を**削除**する
  (services/meeting-api へのパス修正ではなく削除。理由: meeting_api は依存
  [email-validator 等] ごと pip install しないと import できず、sys.path
  挿入は「動かない配線」を残すだけ)。
- SERVICE_ROOT の挿入(main.py import 用)は維持。
- docstring に実行前提を明記:
  `pip install -e libs/admin-models/ -e services/meeting-api/` 済みの venv で
  `pytest services/mcp/tests/ -v`。

### 2. `services/mcp/test_parse_meeting_url.py`(サービス直下)の削除

- tests/ 配下の新版に対する古い重複。`git log --follow` で由来を一応確認の上
  削除(削除理由を commit message に記載)。

### 3. テストを実際に実行し、結果に応じて修正

- fresh venv(python3.11)で
  `pip install -e libs/admin-models/ -e services/meeting-api/ pytest` →
  `python -m pytest services/mcp/tests/ -v`。
- fail した場合の分類と対処:
  - **期待値の陳腐化**(main.py の現挙動が正しく、テストが古い)→ テストを
    現挙動に合わせて修正。修正根拠(main.py のどの実装が正か)を PR に明記。
  - **実バグの発見**(テストの期待が仕様として正しく、main.py が誤り)→
    本タスクでは main.py を直さず、**停止して報告**(バグ修正は別タスク。
    CI 追加タスクに製品修正を混ぜない)。
  - skip / xfail での回避は禁止。

### 4. `.github/workflows/test-mcp.yml`(新規)

- `name: Test MCP`
- trigger(test-api-gateway.yml の流儀 + workflow 自身):
  - push: branches [main, feature/*], paths: `services/mcp/**`,
    `services/meeting-api/meeting_api/schemas.py`,
    `.github/workflows/test-mcp.yml`
  - pull_request: paths: `services/mcp/**`, `services/meeting-api/**`,
    `.github/workflows/test-mcp.yml`
- `permissions: contents: read`
- job `test`: ubuntu-latest, `timeout-minutes: 10`
- steps:
  1. checkout(既存 SHA ピン、persist-credentials: false)
  2. setup-python(既存 SHA ピン)`python-version: "3.11"` + `cache: 'pip'`,
     `cache-dependency-path: services/meeting-api/pyproject.toml`
  3. Install dependencies:
     `pip install -e libs/admin-models/ -e services/meeting-api/` +
     `pip install pytest`
     (fastapi-mcp / mcp はテストがスタブするため install しない。
     requirements.txt の fastapi-mcp はコンテナ内専用)
  4. Run tests: `python -m pytest services/mcp/tests/ -v`
- 禁止事項: `continue-on-error` / `|| true` を使わない。pytest の収集0は
  exit 5(非0)で自然に赤。

### 5. 検証手順(PR での実証)

1. ローカル fresh venv で `pytest services/mcp/tests/ -v` が全収集・全pass。
2. PR 作成 → `Test MCP` 緑を確認。
3. **sabotage 検証**: テスト期待値を壊す一時 commit → テストステップで赤 →
   revert → 緑。
4. revert 後 run で pip キャッシュ restore を確認。

## 変更しないもの

- `services/mcp/main.py`(実バグ発見時も本タスクでは触らない。§3 参照)。
- `services/mcp/requirements.txt` / Dockerfile(コンテナ内依存は現状のまま)。
- 既存 workflow。meeting-api 本体。

## Why(実装者に渡さない)

- mcp をスコープに足した判断はコーディネータへの報告事項(ST-26 の文言は
  「vexa-bot/transcription-service/dashboard/runtime-api等」で mcp は「等」側。
  ただし壊れたテストの放置は ST-26 の趣旨に最も反する)。却下されたら本タスク
  だけ落とせるよう独立タスクにしてある。
- conftest の sys.path 挿入を「修正」でなく「削除」にする理由: パスだけ直すと
  「venv に依存が無くても動きそうに見える」誤誘導が残る。依存解決は pip に
  一本化し、conftest は main.py の import 配線だけに責務を絞る。
- 実バグ発見時に停止する理由: CI 追加タスクの差分に製品修正が混ざると、
  レビュー範囲が膨らみ「CI を足しただけ」という検証契約が崩れる。
  バグは独立した再現手順付き issue にする方が監査追跡上も正しい。
- fastapi-mcp を CI に入れない理由: requirements.txt のコメントにある通り
  バージョン制約が繊細(mcp 2.0 で breaking)で、テストは既にスタブ設計。
  CI が fastapi-mcp のリリース事情で赤くなる依存を持ち込まない。
