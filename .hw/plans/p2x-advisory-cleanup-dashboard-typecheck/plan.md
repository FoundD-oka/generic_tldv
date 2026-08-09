---
generated_by: fable
task_id: p2x-advisory-cleanup-dashboard-typecheck
base-commit: 6fe1b2328b49c7fe9d9a2459c386b93477895212
size: S
---

# dashboard の型チェックを常時緑にして CI に載せる(advisory 18)

## How

1. 実測: `cd services/dashboard && npm ci --no-audit --no-fund && npx tsc --noEmit`。
   エラー一覧を記録(引き継ぎ時点は1件:
   `tests/test_voiceprint_recording_ui.test.ts` の `children` 必須プロパティ欠落)。
2. エラーを全件修正して `npx tsc --noEmit` exit 0 にする。想定は該当テストへの
   `children` 追加のみ。**実測で6件以上の未知エラーが出たら着手を止めて報告**
   (S の前提が崩れるためプラン層へ差し戻し)。
3. `.github/workflows/test-dashboard.yml` の「Run tests」の後に
   `- name: Typecheck` / `run: npx tsc --noEmit` を追加。
4. 禁止: `tsconfig.json` を緩めてエラーを消すこと(strict 系フラグ・include の
   変更不可)。`src/` の実行時挙動を変えること(型注釈の追加のみ許容)。
5. 検証は verification-contract.md のとおり。

## Why(実装者に渡さない)

- 価値は「型チェックが常時緑でない状態を CI が証明できないこと」の解消。
  dashboard は既に test + lint ラチェットが CI にあり(#63)、typecheck だけが
  穴だった。エラー1件のうちは exit 0 必須にでき、ラチェット機構が不要。
- ci-triggers タスクと同じ workflow ファイルを触るため、順序は ci-triggers の後。
