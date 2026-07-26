# KPI Backcast Roadmap: 全リポジトリ・リファクタリング

## 完成時KPI

| カテゴリ | 完成状態 | 機械的証拠 |
|---|---|---|
| 認証 | client指定ID、未署名cookie、cross-audience tokenで別ユーザー/別service資産を操作できない | subject/署名/audience mismatchで副作用0のmatrix |
| 秘密情報 | 公開API・Dashboard・log/URL/Redis job/generated workloadへglobal credential、platform共有provider credential、Zoom client secret、proxy user-infoを返さない | schema/route/core/deploy canary test |
| 非同期整合性 | 古い会議応答、重複poll、相関なし応答がない | race、single-flight、concurrencyテスト |
| 文字起こし | interleave・rejoin・境界mergeで順序と重複が安定 | golden/propertyテスト |
| 会議ライフサイクル | callback/sweep/stopが同一終端サービスを使う | transition matrix/idempotencyテスト |
| モジュール境界 | serviceからentrypointへの逆import、主要循環が0 | import graphテスト |
| 検証基盤 | 0件、全skip、missing active testがpassにならない | tests3 contract unit tests |
| デプロイ | health/readiness失敗が非0、deploy後probeとrollbackあり | fake command test + workflow validation |
| 可読性 | 巨大entrypoint/componentがcomposition中心になる | AST size/責務検査、lint/typecheck |
| 文書 | current mainの実態とshipping状態が一致 | docs check、staleness gate |

## 現在値

- ソース系ファイル総量: 約161,809行。
- 最大Python: `gemini_adapter.py` 3,936行。
- 最大TS/TSX:
  - `vexa-bot/core/src/index.ts` 2,830行
  - meeting detail page 2,452行
  - `TranscriptViewer` 1,486行
- Dashboard: 199 tests pass、lint 61 errors / 87 warnings。
- transcript-rendering: 83 pass / 5 skip、typecheck pass。
- tests3: 91登録中45 script不在、feature sidecarは意図的にOSSから削除済み。
- GitNexus: indexは2026-07-17時点でstale、FTSなし。direct readで補完。
- 起動済みDashboard: port 3001/3002、イメージは約10日前。

## チェックポイント

| Checkpoint | 到達条件 | 後続で解禁される作業 |
|---|---|---|
| CP0 Safety | baseline SHA、既存test結果、golden、fake infra fixtureが保存済み | 全変更 |
| CP1 Boundaries | Admin/user subject、audience別secret、storage/event/media/egress broker、subject-owned provider credential、server-side Zoom issuer、Browser save、URLのP0がgreen | 構造移動 |
| CP2 Trustworthy Gates | 0/missing/skipがfail-closed、health commandが非0 | required CI/deploy gate |
| CP3 Backend Shape | lifecycle/request/final transcription/Geminiがthin coordinator | shared package化 |
| CP4 Frontend Shape | WS/store/UI/coreの所有者が単一 | dead code/lint cleanup |
| CP5 Decoupled Services | pure contracts/modelsをサービス外へ移しDocker依存を縮小 | 文書同期、最終E2E |
| CP6 Closure | 全required command、fixture E2E、GitNexus diff、独立tribunal/post review/QA、outcome、人間承認が同一HEAD | PR Ready |

## 依存グラフ要約

```text
CP0
 ├─ security boundaries ─────────────┐
 ├─ frontend correctness ────────────┤
 └─ verification fail-closed ────────┘
                  ↓
       lifecycle / request / async owners
                  ↓
        pure contract + model extraction
                  ↓
        large component/entrypoint thinning
                  ↓
           deploy + docs + final gates
```

## Deliverable

- 実装計画: `.pipeline/plans/full-repo-refactoring-2026-07-24/plan.md`
- 検証契約: `.pipeline/plans/full-repo-refactoring-2026-07-24/verification-contract.md`
- 実装時の証拠: `.pipeline/evidence/full-repo-refactoring-2026-07-24/`
- 実装時のgate: `.pipeline/gates/full-repo-refactoring-2026-07-24/`
- 実装時のsession ledger:
  `.pipeline/sessions/full-repo-refactoring-2026-07-24/events.jsonl`
- 実装時のoutcome:
  `.pipeline/outcomes/full-repo-refactoring-2026-07-24/outcome-card.json`
