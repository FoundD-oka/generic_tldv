# 調査要約

## 観測事実

- 失敗rawには`error_max_turns`と`error_max_budget_usd`が記録されている。
- 終了時の`stop_reason`はいずれも`tool_use`だった。
- stderrには現行CLIで認識されない`MultiEdit` deny rule警告がある。
- 現行Claude Code CLIは`--safe-mode`でcustomizationを無効化し、`--tools ""`でbuilt-in toolsを停止できる。
- Harnessが生成するbriefには計画、検証契約、差分、質問が既に含まれる。

## 仮説

自己完結briefにもかかわらずrepo探索toolを許可したため、Fableが回答前にtool turnを消費し、turnまたは費用上限へ到達した。

## 反証条件

context-only起動でも同じ上限エラーになる場合は、brief量、モデル単価、認証／quotaを別原因として再調査する。
