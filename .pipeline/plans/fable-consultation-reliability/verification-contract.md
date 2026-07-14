# 検証契約

- Fable起動引数に`--safe-mode`、空の`--tools`、`--strict-mcp-config`が含まれる。
- `MultiEdit`を含む存在しないtool deny指定がない。
- 正常な構造化応答から`completed` summaryが生成される。
- `error_max_turns`または`error_max_budget_usd`時、原因・turn数・費用がeventsへ記録される。
- dual-reviewのFable起動も同じcontext-only契約を使う。
- review-policy smoke、runtime profile、adapter validationが成功する。
- 実CLIのFable probeが1回で構造化応答を返す。
