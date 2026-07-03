# harness-init クイックスタート

> **最初にこれだけ読めば動く。** 詳細は各ステージのリンク先へ。

---

## Stage 1 — Day 1: とにかく動かす

### インストール

```
/harness-init
```

### S タスクを1件流す

```bash
# 1. タスクIDを設定（これだけ。以降のコマンドはプレフィックス不要）
bash scripts/harness/task-set.sh issue-1

# 2. コレが「S」かどうかを確認（全部 YES なら S fast-path）
#    - ≤ 2ファイル・≤30行の変更？
#    - 新しい依存関係を追加しない？
#    - schema/migration/auth/payment/PII を触らない？
#    - 実装方針がコードを読めば自明？
#    - 1文で説明できる？

# 3. plan.md を書く（S なら ≤10行で OK）
mkdir -p .pipeline/plans/issue-1
cat > .pipeline/plans/issue-1/plan.md << 'EOF'
# Plan: issue-1
intent: <変更の目的を1文で>
approach: <どのファイルを、どう変えるか>
EOF

# 4. サイズを記録
bash scripts/harness/sml-decision.sh issue-1 --size S --reason "single file, unambiguous"

# 5. 実装

# 6. gh pr create（preflight が自動チェック）
gh pr create
```

**つまずいたら:** `bash .claude/hooks/pr-ready-gate.sh` を手動実行して `[NG]` 行を確認。

---

## Stage 2 — Week 1: 判断できるようになる

| トピック | 読む場所 |
|---------|---------|
| S/M/L の判断基準 | [user-guide.md § 7](user-guide.md#sml) |
| Plan Relay の読み方 | [user-guide.md § 6](user-guide.md#plan-relay) |
| HD ゲートがブロックしたとき | [user-guide.md § 9](user-guide.md#hd-gate) |
| Residency でブロックされたとき | [user-guide.md § Residency](user-guide.md#residency) |

---

## Stage 3 — Month 1: 運用できるようになる

| トピック | 読む場所 |
|---------|---------|
| KPI Backcast / 受託案件型 | [user-guide.md § 6 — KPI Backcast](user-guide.md#kpi-backcast) |
| Bug Tribunal / Sidechain Review | [user-guide.md § 10](user-guide.md#tribunal) |
| Feedback Ledger / ハーネス規則の管理 | [user-guide.md § 8 — feedback-prune](user-guide.md#feedback-prune) |
| Adapter Contract | [user-guide.md § Adapter](user-guide.md#adapter) |

---

## よくある詰まりポイント

| 状況 | 確認コマンド |
|------|------------|
| pr-ready が通らない | `bash .claude/hooks/pr-ready-gate.sh` |
| 今のタスクIDを確認したい | `bash scripts/harness/task-set.sh` |
| HD ゲートがブロックする | `bash .claude/hooks/hd-gate.sh` |
| harness-doctor でエラー | `bash .ci/harness-doctor.sh` |
