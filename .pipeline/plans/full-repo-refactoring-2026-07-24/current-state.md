# Current State Report: full-repo-refactoring-2026-07-24-goal

## Known Facts
- 調査HEAD b2bcae8e、既知問題とbaselineはplan.md 1.4〜1.5に固定

## Issue Goal
- 既存公開契約と利用者可視意味を維持し、認証境界・競合・検証偽陽性を直して巨大責務と循環を計画順に分解する

## Suggested Quality Checkpoint
- 全項目固有test、required suite、構造gate、fixture E2Eが同一最終HEADで成功する

## Quality Conditions
- QC-SEC: 認証・secret・outbound境界がfail closed -> security contract全件pass、公開secret 0、未認証副作用0
- QC-TEST: 検査の実行事実と合否が一致 -> 全item ready、失敗0、unexpected skip/xfail 0
- QC-STRUCTURE: 責務/循環/所有権budgetを満たす -> structure/import/ownership gate全件pass
- QC-E2E: 利用者可視契約を維持 -> source SHA一致、console/page/network error 0、承認外visual差0
