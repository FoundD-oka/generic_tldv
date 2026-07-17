# Current State Report: transcript-seekbar-persistence-goal

## Known Facts
- scrollIntoViewが外側ページまで動かし、上部シークバーを画面外へ押し出す

## Issue Goal
- 発話再生時も会議全体のシークバーを表示したまま文字起こしだけを追従スクロールする

## Suggested Quality Checkpoint
- 追従スクロールを文字起こしコンテナ内に限定し、対象テストとDashboardビルドが通る

## Quality Conditions
- QC-01: 外側ページを動かさないコンテナ内スクロール -> 対象ユニットテストが通る
- QC-02: 変更ファイルの静的品質 -> 対象Lintが通る
- QC-03: 本番Dashboard互換性 -> production buildが通る
