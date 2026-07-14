# Current State Report: status-display-completed-goal

## Known Facts
- completedの終了理由ごとに停止済み・終了・退出済みなど異なるラベルを表示している

## Issue Goal
- 終了した会議のステータス表示を完了へ統一する

## Suggested Quality Checkpoint
- completedは常に完了と表示し、stoppingは停止中のまま維持される

## Quality Conditions
- completed-label: completedの終了理由に依存せず完了と表示する -> 代表的な終了理由すべてで完了になる
- stopping-label: 最終化中と完了を混同しない -> stoppingは停止中を維持する
