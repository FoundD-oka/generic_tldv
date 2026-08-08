# 検証契約: st9-numpy-dep-sync

ベースライン(base-commit 5cae3a0、CI run 31236545693 実測):
`8 failed, 584 passed, 10 skipped`。失敗内訳 = numpy 起因7件 + postgres 1件。

## 最低合格ライン(すべて機械検証、すべて必須)

1. **依存宣言の存在**: 次が両方 exit 0。
   `grep -q '"numpy>=1.26"' services/meeting-api/pyproject.toml`
   `grep -q '"cryptography>=42.0"' services/meeting-api/pyproject.toml`

2. **CI 同等環境での再現検証**: リポジトリルートで fresh venv を作り
   CI と同一手順でインストールして実行する(python3.11 必須)。
   ```bash
   V=$(mktemp -d)/venv && python3.11 -m venv "$V"
   "$V/bin/pip" install -q -e libs/admin-models/ -e services/meeting-api/
   "$V/bin/pip" install -q pytest pytest-asyncio httpx
   "$V/bin/python" -c "import numpy, cryptography"   # exit 0 必須
   "$V/bin/pytest" services/meeting-api/tests/ \
     --ignore=services/meeting-api/tests/test_integration_live.py \
     --deselect "services/meeting-api/tests/integration/test_transcription_dictionary_postgres.py::test_real_postgres_advisory_lock_enforces_200_term_cap"
   ```
   最終 pytest が **exit 0**(deselect した postgres 1件以外の failed ゼロ)。

3. **voiceprint 7件の直接確認**: 同 venv で
   `"$V/bin/pytest" services/meeting-api/tests/test_voiceprint_matching.py -q`
   が exit 0(32件全パス)。

4. **差分の限定**: `git diff --name-only <base-commit>..HEAD` の出力が
   `.hw/plans/st9-numpy-dep-sync/` 配下と `services/meeting-api/pyproject.toml`
   のみ。pyproject.toml の変更は dependencies への2行追加のみ。

## アンチゲーミング条項
- tests/ 配下・CI workflow・pytest 設定の変更、skip/xfail/deselect の恒久
  追加は違反(合格ライン2の deselect は検証コマンド内のみで、postgres
  失敗が次タスク st10 のスコープであるため)。
- Codex 由来の未コミット4ファイルをコミットに含めたら違反。

## 証拠
上記コマンドの実行ログ(exit code 含む)を .hw/gates/st9-numpy-dep-sync/
(gitignore領域)へ保存する。push 後の CI は postgres 1件のみ failed
(`7 failed → 1 failed` 遷移)を確認できれば追加証拠とする。
