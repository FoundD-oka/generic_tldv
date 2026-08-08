---
generated_by: fable
task_id: st9-numpy-dep-sync
base-commit: 5cae3a05550e8679b156e88652b0dfa2193d30f1
size: S
---

# st9: meeting-api 依存宣言乖離の解消(numpy / cryptography)

## ゴール
main の CI「Test Meeting API」の numpy 起因7失敗を解消する。依頼どおりで
reframe 不要。ただし同一欠陥クラス(requirements.txt にあり本番コードが直接
import するのに pyproject.toml に無い)の cryptography も同時に宣言する。

## How
`services/meeting-api/pyproject.toml` の `dependencies` に2行追加するのみ:
- `"numpy>=1.26",`
- `"cryptography>=42.0",`
(下限は requirements.txt:14-15 と一致させる)

## 制約
- 変更ファイルは pyproject.toml の1ファイルのみ。テスト・CI workflow・
  requirements.txt・他の依存行には触れない。
- メインリポジトリの作業ツリーに別セッション(Codex)の未コミット変更4ファイル
  あり(meeting-card.tsx / login/page.tsx / docker-compose.yml /
  test_meeting_cards_ui.test.ts)。ステージ・コミット禁止。
- st8 の verification-contract.md(既知失敗リスト)は完了済みタスクの記録
  なので改訂しない。
- 本タスク完了後も CI には postgres 起因の失敗が1件残る(次タスク
  st10 のスコープ)。これは本タスクの失敗ではない。

## 検証契約
verification-contract.md を参照(機械検証のみ、Fable レビュー不要)。

## Why(実装者に渡さない)
CI は `pip install -e services/meeting-api/` で依存を解決するため
requirements.txt を見ない。numpy は voiceprint_matching.py:742 の関数内
import で実行時に落ち、warning に握りつぶされてテスト7件が assert で落ちる。
cryptography は google-auth の推移的依存で偶然 CI に入っているだけで、
voiceprint_crypto.py の直接 import が上流の依存変更で無警告に壊れ得る。
