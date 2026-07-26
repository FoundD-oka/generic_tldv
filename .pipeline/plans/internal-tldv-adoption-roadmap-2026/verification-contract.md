# 検証契約: internal-tldv-adoption-roadmap-2026

## 受入テスト

| ID | 要件 | 方法 | 証跡 |
|---|---|---|---|
| AT-001 | 正本ロードマップが日本語で存在する | manual/command | `docs/2026-07-17_社内tl-dv置き換えロードマップ.md` |
| AT-002 | 現状の実装と稼働実績が分離される | manual | 現在地、research-brief |
| AT-003 | KPIに算式・母数・目標がある | command | K-01〜K-15 |
| AT-004 | Phase 0〜4に対象・作業・Exit KPIがある | command | 段階導入ロードマップ |
| AT-005 | Go/Hold/Rollbackと正当な例外がある | command | 正本ロードマップ |
| AT-006 | 現行tl;dvの未確認値を事実として捏造しない | manual | research-brief、K-15 |

## 回避する失敗パターン

| ID | 回帰させないこと | 方法 | 証跡 |
|---|---|---|---|
| FP-001 | 「全部」を例外なしの録音強制と定義しない | manual | 置き換え完了の定義 |
| FP-002 | 機能実装済みを全社運用品質の証明にしない | manual | 現在地 |
| FP-003 | 日付だけで次Phaseへ進めない | manual | Exit KPI |
| FP-004 | 開発・検証会議を正式な社内KPI母数にしない | manual | 暫定ベースライン注記 |

## KPI確認

| KPI ID | カテゴリ | 最低合格線 | 方法 | 証跡 |
|---|---|---|---|---|
| KPI-001 | 定義 | K-01〜K-15が存在する | `rg -n 'K-(0[1-9]|1[0-5])' docs/2026-07-17_社内tl-dv置き換えロードマップ.md` | コマンド出力 |
| KPI-002 | 段階 | Phase 0〜4が存在する | `rg -n 'Phase [0-4]' docs/2026-07-17_社内tl-dv置き換えロードマップ.md` | コマンド出力 |
| KPI-003 | 安全 | Go/Hold/Rollbackが存在する | `rg -n 'Go / Hold / Rollback|Rollback' docs/2026-07-17_社内tl-dv置き換えロードマップ.md` | コマンド出力 |

## ゲート要件

- preflight結果: 必須
- 証跡パック: 必須
- ハッシュ拘束承認: 不要
- リサーチブリーフ: 必須
- 選択肢比較: 必須
- KPIバックキャストロードマップ: 必須
- 外部相談: 不要
- 外部相談プロバイダ: 該当なし

## 調査鮮度の確認

| ID | 鮮度が落ちる判断 | 再確認方法 | 証跡 |
|---|---|---|---|
| RF-001 | 稼働会議数と成功状態 | Phase 0開始時にDB再集計 | `research-brief.md` |
| RF-002 | 話者PoCのopen/closed状態 | Phase 1前にGitHub Issue再確認 | `research-brief.md` |
| RF-003 | Cloud RunとDocker稼働構成 | 各Phase開始時に構成棚卸し | 運用証跡 |
