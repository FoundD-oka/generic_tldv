---
generated_by: fable
task_id: p14-ci-transcription-service
base-commit: 69d01b2f887a0765d2900f83545e1b58bcf8de0c
size: M
---

# ST-26(1/4): transcription-service の単体テスト CI を追加する(P1-4 第1弾)

## ゴール

依頼の文字通りの内容: 「transcription-service に CI が1本もない」(監査 ST-26)。
テストスイートは既に存在し(main 実測 208 passed / 1 skipped、handoff 記載)、
CI が無いだけ。

reframe は不要だが、達成基準を「workflow ファイルが存在する」ではなく
**「テストが壊れたら PR が赤くなることを実証済みの CI」**と定義する
(ST-27/28 の教訓 = 常に緑の CI は無いのと同じ)。合格条件に
「意図的に壊すと赤くなる」実証を含める。

## 現状分析(現物確認済み、2026-08-09)

- `.github/workflows/` は10本あるが、transcription-service を検証するものは0
  (test-meeting-api / test-admin-api / test-api-gateway / test-packages のみが
  テスト系。ST-26 は未解消)。
- テスト: `services/transcription-service/tests/`(test_gemini_adapter.py /
  test_soniox_adapter.py / test_config.py / test_quality_gate.py)。
  `tests/quality/` は test_*.py なしのスクリプト群で pytest 非収集。
  `test_hot.sh` / `test_stress.sh` も非収集。conftest.py は integration マーカー
  定義のみ(1 skipped はこれ由来)。
- 依存: `requirements.txt`(faster-whisper / soundfile / numpy / google-genai 等)。
  fresh venv での install はローカル実測約1分(handoff)。soundfile は wheel に
  libsndfile 同梱、faster-whisper 系も wheel 供給で apt 依存なしの見込み。
- fresh venv(HF キャッシュ空)でテストが通っている実績があるので、テストは
  モデルダウンロード・ネットワーク非依存のはず。CI では `HF_HUB_OFFLINE=1` を
  設定して隠れたネットワーク依存を fail-loud にする。
- 既存 test-*.yml の流儀: `name: Test <Service>` / push branches [main, feature/*]
  + paths / pull_request + paths / `permissions: contents: read` / actions を
  SHA ピン(checkout `34e11487...` / setup-python `a26af69b...`)。
- **既存流儀の盲点**: 既存 test-*.yml は pull_request の paths に workflow
  ファイル自身を含めていない。workflow だけを追加・変更する PR では CI が
  走らない。本タスクの PR は workflow 追加のみなので、この盲点を踏むと
  「PR で一度も実行されないまま merge」になる。pull_request paths にも
  workflow ファイルを含める(既存流儀からの意図的な逸脱)。

## How

変更は `.github/workflows/test-transcription-service.yml`(新規)のみ。
サービス本体・テスト・既存 workflow は無変更。

### 1. `.github/workflows/test-transcription-service.yml`

- `name: Test Transcription Service`
- trigger(paths は push / pull_request とも同一にする):
  - push: branches [main, feature/*], paths:
    `services/transcription-service/**`,
    `.github/workflows/test-transcription-service.yml`
  - pull_request: paths 同上(workflow 自身を含める。上記「盲点」参照)
- `permissions: contents: read`
- job `test`: `runs-on: ubuntu-latest`, `timeout-minutes: 20`
- steps:
  1. checkout(test-meeting-api.yml と同じ SHA ピン、`persist-credentials: false`)
  2. setup-python(同じ SHA ピン)`python-version: "3.11"` +
     **`cache: 'pip'`、`cache-dependency-path: services/transcription-service/requirements.txt`**
     (faster-whisper 系の install が重いためキャッシュ必須)
  3. Install dependencies:
     `pip install -r services/transcription-service/requirements.txt` と
     `pip install pytest pytest-asyncio`(handoff のローカル手順と同一構成)
  4. Run tests:
     `python -m pytest services/transcription-service/tests/ -v`
     env に `HF_HUB_OFFLINE: "1"` を設定(隠れたモデルダウンロードを禁止)。
- **禁止事項**: `continue-on-error`・`|| true`・`- run: ... || echo` の類は
  一切使わない。pytest は「収集0件」で exit code 5(非0)なので、テストが
  消えた場合も自然に赤になる(この性質を握り潰す細工をしない)。

### 2. CI 上で赤が出た場合の対応方針(実装者向け)

- install 失敗(wheel 不在で sdist ビルドに落ちる等)→ 該当パッケージの
  バージョン floor を requirements.txt で調整するのではなく、まず CI の
  Python 3.11 と wheel 供給状況を確認。requirements.txt の変更が必要になったら
  変更理由を PR に明記(契約 FP-002 の対象)。
- `HF_HUB_OFFLINE=1` 起因の失敗 → どのテストがネットワークを要求したかを
  特定し、当該テストのモック不足として扱う(env を外して逃げない)。
  修正が本タスクで重すぎる場合は当該テストを deselect せず、報告して判断を仰ぐ。

### 3. 検証手順(PR での実証)

1. PR を作成し、`Test Transcription Service` workflow が実行され緑になることを確認。
2. **sabotage 検証**: path filter 内のテスト1件の期待値を意図的に壊す一時
   commit を push → workflow が「テストステップで」赤になることを確認し、
   `gh run view --log-failed` の抜粋を保存 → `git revert` で戻して緑を再確認。
   (squash merge なので main 履歴は汚れない)
3. revert 後の再実行ログで pip キャッシュの restore を確認(2回目以降の
   install 時間短縮の証拠)。

## 変更しないもの

- サービス本体・tests/・requirements.txt(CI で赤が出て変更が必要になった
  場合のみ、理由を PR に明記して変更可)。
- 既存 workflow 10本。deploy 系・compose。

## Why(実装者に渡さない)

- ST-26〜29 の問題意識の本体は「CI が存在しないか、存在しても常に緑」。
  だから合格条件を workflow の存在ではなく「壊したら赤くなる実証」に置いた。
  rung.yml(continue-on-error で常に緑)と同じ轍を踏まないための sabotage 検証。
- `HF_HUB_OFFLINE=1` は hermeticity の保険。fresh venv 実績から不要のはずだが、
  将来誰かがモデル実ロードのテストを足したとき「CI がひっそり遅く・不安定に
  なる」のではなく即座に赤で気づける。
- pull_request paths に workflow 自身を含める逸脱は、既存4本の盲点の是正を
  新規分から始めるもの。既存4本への遡及適用は本タスクのスコープ外
  (advisory として報告)。
- runtime-api も ST-26 の「等」に含まれるが、クライアント指示のスコープは
  vexa-bot / transcription-service / dashboard の3つ。runtime-api CI は
  advisory として報告し、本タスク群には含めない。
