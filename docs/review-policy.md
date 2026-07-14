# Fable / GPT-5.6 Sol Ultra レビューポリシー

このポリシーは、Fableをユーザー意図と選択肢のアドバイザー、GPT-5.6
Sol Ultraを実装品質のガーディアンとして使い分ける。モデルの同意だけを
証拠にはせず、対象ハッシュ、テスト、Evidence Manifestを必ず併用する。
Fableのplan briefには、存在する場合は`request.md`、`issue.md`、
`task-brief.md`も含め、planが元のユーザー意図を落としていないか確認する。

## サイズ別ルーティング

| サイズ | 実装前 | 実装後 | 次へ進む条件 |
|---|---|---|---|
| S | Fableがplanをレビュー | 通常レビューなし | テスト成功、Fableのopen MUST_FIXが0 |
| M | Fableがplanをレビュー | Sol Ultraが最終diffをレビュー | テスト成功、Sol Ultraのopen MUST_FIXが0 |
| L | FableとSol Ultraが独立レビュー後に合意 | 両者が再レビュー後に合意 | 同一ハッシュへ双方が`AGREE`、blockerが空 |

全サイズで同じ失敗シグネチャが2回記録された場合、Sol Ultraの`stuck`
レビューが必須になる。失敗シグネチャは生ログではなく、
`test-api-timeout`のような安定した識別子を使う。

## コマンド

```bash
# 全サイズ: plan完成後
scripts/harness/external-consultation.sh run <task-id> --mode plan

# 同じ失敗のたびに記録。2回目でSol Ultraレビュー必須
scripts/harness/codex-review.sh failure <task-id> --signature <stable-id>
scripts/harness/codex-review.sh run <task-id> --mode stuck

# M/L: 実装・テスト完了後
scripts/harness/codex-review.sh run <task-id> --mode post

# L: plan時と実装後。事前に両モデルの同じstageの独立レビューが必要
scripts/harness/codex-review.sh run <task-id> --mode plan
scripts/harness/dual-review.sh run <task-id> --stage plan
scripts/harness/external-consultation.sh run <task-id> --mode post
scripts/harness/dual-review.sh run <task-id> --stage post
```

`dual-review.sh`はFableとSol Ultraの初回意見を同じbriefへ入れ、両者へ
再提示する。双方が`AGREE`し、双方の`blockers`が空のときだけ合意となる。
最大2ラウンドで未解決なら、会話を延長せずユーザー判断へエスカレートする。
`build.sh`と`codex-build.sh`は実装開始前に
`.claude/hooks/pre-implementation-review-gate.sh`を通すため、必要なplan
reviewやL合意が欠けた状態では実装コマンド自体が起動しない。
`worktree.sh create`はplanと実装前レビュー証跡をtask worktreeへ引き継ぐ。

## 証跡

```text
.pipeline/evidence/<task>/external-consultation/consultation-<stage>-summary.json
.pipeline/evidence/<task>/codex-review/review-<mode>-summary.json
.pipeline/evidence/<task>/codex-review/review-trigger.json
.pipeline/evidence/<task>/dual-review/consensus-<stage>-summary.json
.pipeline/gates/<task>/external-consultation.json
.pipeline/gates/<task>/codex-review.json
.pipeline/gates/<task>/dual-review.json
```

## 境界

- Sは通常の実装後レビューを行わない。
- M/LのSol Ultraレビューは実装セッションと分離し、read-onlyで実行する。
- FableもSol Ultraもリポジトリを編集しない。
- `SHIP`や`AGREE`はテスト成功の代わりにならない。
- MUST_FIXを修正した場合は、変更後の対象ハッシュでレビューをやり直す。
- post reviewはbuild開始時のSHAから現在状態までを対象にし、自動commit後も空diffをレビューしない。
