# 実行計画

## 目的

対象社内会議をカボスへ段階移行し、最終的に旧tl;dvの新規利用を終了できる判断基準を作る。

## 採用方針

- 部署単位のKPIゲート移行を採用する。
- 完了は機能数ではなく、対象会議成功カバレッジ、E2E成功率、利用率、旧ツール残存率で判断する。
- 録音不可・オプトアウト会議は正当な例外として母数から分離する。
- 日付は目安とし、Exit KPI未達なら次段階へ進めない。

## フェーズ

1. Phase 0: 対象会議、現行利用、KPIイベント、同意・保持・削除、パイロットを確定する。
2. Phase 1: 5〜10人・30会議以上で並行運用し、確定文字起こし、Drive、自動成果物、話者品質を安定させる。
3. Phase 2: 1部署・100会議以上でカレンダー既定参加、検索・共有、問い合わせ運用を定着させる。
4. Phase 3: 全社既定にし、冗長化、権限、監視、復元を全社SLOへ引き上げる。
5. Phase 4: 4週連続で最終KPIを満たして旧ツールを終了する。

## 成果物

- 正本ロードマップ: `docs/2026-07-17_社内tl-dv置き換えロードマップ.md`
- KPIバックキャスト: `.pipeline/plans/internal-tldv-adoption-roadmap-2026/kpi-backcast-roadmap.md`
- リサーチブリーフ: `.pipeline/plans/internal-tldv-adoption-roadmap-2026/research-brief.md`
- 選択肢比較: `.pipeline/plans/internal-tldv-adoption-roadmap-2026/option-matrix.md`
- 検証契約: `.pipeline/plans/internal-tldv-adoption-roadmap-2026/verification-contract.md`

## 未確定事項

- パイロット部署と対象者
- 現行tl;dvの利用者数、費用、利用会議時間、必須機能
- 対象会議と例外会議の正式定義
- 過去tl;dvデータの移行・アーカイブ・削除方針
- 会議後に必須とする自動成果物の詳細
