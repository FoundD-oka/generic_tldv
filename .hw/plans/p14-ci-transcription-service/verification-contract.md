# Verification Contract — p14-ci-transcription-service

対象: `base-commit..HEAD` の差分(base-commit はコーディネータが着手時に
plan.md frontmatter へ書き込んだ値)。検証は commit 済み clean tree に対して行う。

## ベースライン取得手順(転記値の使用禁止)

1. 実装着手前に base-commit の clean tree で fresh venv(python3.11、
   `~/.cache/hw-venvs/p14-ci-transcription-service` 等 /tmp 以外)を作成:
   `pip install -r services/transcription-service/requirements.txt pytest pytest-asyncio`
   → `HF_HUB_OFFLINE=1 python -m pytest services/transcription-service/tests/`
2. サマリ全文を `.hw/gates/p14-ci-transcription-service/pytest-baseline-<commit>.txt`
   に保存する。**この実測値が本契約のベースライン**。handoff・過去契約・本契約
   以外からの転記値は無効。
3. ベースラインに fail がある場合は着手前に報告(本タスクはテスト修正を含まない)。

## 検証の権威分担

- **ローカルで確認するもの**: workflow の構文・禁止パターン不在・テストが
  ローカル fresh venv で緑(ベースライン一致)。
- **CI 実行結果に委ねるもの(最終権威)**: Linux ランナーでの install 成功・
  テスト緑・sabotage 時の赤・キャッシュ効果・実行時間。PR 上の実測 run が証拠。

## Acceptance Tests

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-001 | workflow が有効な YAML で、trigger が push(branches [main, feature/*])+ pull_request、paths が `services/transcription-service/**` と workflow ファイル自身(push / pull_request 両方)に限定されている | venv に pyyaml を入れ `python -c "import yaml; yaml.safe_load(open('.github/workflows/test-transcription-service.yml'))"` + 目視レビュー | 出力 + レビュー verdict |
| AT-002 | **CI 実測緑(本質要求)**: PR 上で `Test Transcription Service` が実行され、テストステップまで到達して成功する(ベースラインと同数の passed / skipped) | `gh run list --workflow=test-transcription-service.yml` + `gh run view <id> --log` のテストサマリ行 | run URL とログ抜粋を `.hw/gates/p14-ci-transcription-service/ci-green.txt` に保存 |
| AT-003 | **sabotage 検証(常に緑でないことの実証)**: テスト期待値を壊す一時 commit を push すると workflow が「テストステップの失敗で」赤になり、revert 後に緑へ戻る | 一時 commit → `gh run view <id> --log-failed` → revert → 再実行確認 | 赤 run URL・log-failed 抜粋・revert 後の緑 run URL を `.hw/gates/.../ci-sabotage.txt` に保存 |
| AT-004 | pip キャッシュが効く: setup-python の `cache: 'pip'` が設定され、2回目以降の run ログに cache restore が記録される | AT-003 の revert 後 run のログで "Cache restored" 系行を確認 | ログ抜粋 |
| AT-005 | テストステップの env に `HF_HUB_OFFLINE=1` が設定されている | workflow ファイルの grep | grep 出力 |
| AT-006 | CI の job 実行時間が 15 分以内(timeout-minutes: 20 設定込み) | 緑 run の `gh run view <id> --json jobs` で所要時間確認 | JSON 抜粋 |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 | 握り潰しなし: workflow に `continue-on-error` / `\|\| true` / `\|\| echo` / `exit 0` の失敗マスクが存在しない | `grep -nE "continue-on-error\|\\\|\\\| *true\|\\\|\\\| *echo\|exit 0" .github/workflows/test-transcription-service.yml` が空 | grep 出力 |
| FP-002 | 変更ファイルが `.github/workflows/test-transcription-service.yml`(新規)のみ。requirements.txt・tests/ の変更が必要になった場合は理由を PR に明記し Fable レビュー対象とする | `git diff --name-only base-commit..HEAD` | diff 出力 |
| FP-003 | 既存 workflow 10本が無変更 | `git diff base-commit..HEAD -- .github/workflows/ ':!.github/workflows/test-transcription-service.yml'` が空 | diff 出力 |
| FP-004 | ローカル fresh venv でのテスト結果がベースラインと一致(本タスクはテストを変更しないので passed / skipped 同数) | ベースラインと同一 venv・同一コマンドで HEAD を再実行しサマリ比較 | 両サマリ全文 |

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | actions は既存 test-*.yml と同一の SHA ピン(checkout / setup-python)を使用し、新規の unpinned action を導入しない | workflow の uses: 行レビュー | レビュー verdict |
| NFT-002 | secrets を要求しない(permissions: contents: read のみ、env に secrets 参照なし) | workflow レビュー | レビュー verdict |

## KPI Checks

なし(kpi-backcast-roadmap.md 不使用)。

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(`.hw/gates/p14-ci-transcription-service/` へ。`.hw/plans/` に後 commit しない)
- hash-bound approval required: yes(M のため Fable 契約レビュー必須)
- research brief required: no
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no
- external consultation provider: not needed

## Research Freshness Checks

- actions の SHA ピンは既存 test-meeting-api.yml で稼働実績のあるものを流用
  (新規リサーチ不要)。ubuntu-latest のランナー像変更でテストが壊れる依存
  (apt パッケージ等)は使用していない。
