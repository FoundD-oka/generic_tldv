# Current State Report: dashboard-ux-status-polish-goal

## Known Facts
- 無効辞書の視認性が弱く、会議ナビ名が曖昧で、声紋プレビュー生成中に操作でき、再文字起こし中も完了表示になる

## Issue Goal
- 辞書・ナビ・声紋・再文字起こしの4つのUX状態を分かりやすく安全にする

## Suggested Quality Checkpoint
- 4つの状態が要求どおり表示・制御され、全体回帰テストで確認できる

## Quality Conditions
- qc-dictionary: 無効辞書行の背景と文字をグレー化する -> 辞書UIテストが通る
- qc-nav: 日本語サイドパネルを会議一覧へ変更する -> コピー単体テストが通る
- qc-voiceprint: 確認再生準備中は全操作を遮断し完了または失敗で解除する -> 声紋UIテストが通る
- qc-status: 再文字起こしqueued/runningを一覧・詳細で処理中と表示する -> 状態・再処理UIテストが通る
- qc-dashboard-regression: 既存Dashboardを壊さない -> Dashboard全テストとbuildが通る
- qc-api: 一覧APIが最終文字起こし状態を軽量応答に含める -> Meeting API対象テストが通る
- qc-scope: 変更影響が想定範囲内である -> GitNexus変更検査が通る
