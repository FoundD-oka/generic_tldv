---
generated_by: fable
task_id: p3-dead-code-removal
base-commit: 8bfb4476bcbc25b935004e6c555ea3d0425b1470
size: S
---

# dashboard の参照ゼロ・デッドコード削除(FT-2 / UI-19)

## How

1. 着手時に対象13ファイルの参照0を再実測(Codex 並行編集対策)。対象ごとに
   `git grep -l -F "<ファイル名(拡張子除くベース名)>" -- . ':(exclude)services/dashboard/node_modules'`
   を実行し、自ファイル以外からの参照が1件でも見つかったものは**削除せず残して報告**。
2. `git rm` で削除(対象は下記のみ。他ファイルの編集禁止):
   - `services/dashboard/src/components/decisions/decisions-panel.tsx`(637行・import 0 実測済み)
   - dashboard 直下の検証スクリプト12本: `agent-flow.js` `agent-inspect.js`
     `auth-validate.js` `auth-validate2.js` `auth-validate3.js` `auth-validate-final.js`
     `check-pages.js` `deliver-validate.js` `deliver-validate.ts` `feature-validate.js`
     `test-agent-panel.mjs` `validate.sh`
   ※ `docker-entrypoint.sh` は Dockerfile の ENTRYPOINT。削除禁止。
3. 削除で lint 対象が減るため `lint-baseline.json` の errors/warnings を実測値へ
   **下げる**(上げるのは禁止)。tsc と eslint の両方を確認する(片方の修正が
   他方を赤にする前科あり)。
4. 検証は verification-contract.md のとおり。参照が想定外に見つかり削除対象が
   10本未満になったらプラン層へ差し戻す。

## Why(実装者に渡さない)

- decisions-panel は FT-2(AI 層スコープ外)の未配線 UI で、ユーザー指示により
  デッドコード扱いが確定。スクリプト12本は package.json/CI/tests3/README/Dockerfile
  参照0を 2026-08-10 実測済み。リファクタv2 RF-75A(9本を .mjs 化して残す)とは
  衝突するが、v2 は未着手であり、削除が価値(lint 負債9件削減・出荷物の清浄化)で勝る。
  RF-75A の縮退は p3-triage/plan.md に記録済み。
- 先頭に置くのは後続 Phase 3 タスクの diff と lint baseline を先に軽くするため。
