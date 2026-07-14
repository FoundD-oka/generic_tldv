# 実装計画

1. 既存のFable失敗証跡と現行Claude Code CLI契約を照合する。
2. 外部相談とL向け合意レビューのFable起動を、自己完結briefだけを読むcontext-only実行へ変更する。
3. 廃止済みtool名を除去し、失敗時にsubtype・turn数・費用上限を証跡へ残す。
4. fake CLIによる成功・失敗回帰テストと、実CLIによる1回の応答確認を行う。
5. 正規ソースとgeneric_tldvの配布コピーを同期する。

レビュー回数、S/M/Lのレビュー段数、max-call数は変更しない。
