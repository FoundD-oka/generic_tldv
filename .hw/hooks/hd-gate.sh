#!/usr/bin/env bash
# HD (Harness Delta) 再発ゲート — 証拠ベース。
#
# 目的はループ防止ではなく「タスクを跨いだ学習の強制」。同カテゴリの指摘が
# 「同一タスクで2回以上」または「直近3つの別タスクのうち2つ以上」で再発したら、
# ルール改訂が記録されるまでブロックする。コード修正だけでは解除されない
# (症状を直しても、ルールを直さなければ次のタスクでまた踏むから)。
#
# 改訂は人間でもエージェントでも記録してよい。解除条件は署名ではなく証拠:
#   1. 最新の改訂より後に再発していない → 通す(経過観察)。自己改善を信用する。
#   2. 改訂の後にまた再発した → その改訂は効かなかった。ブロックする。
#   3. エージェントの改訂が2回失敗した → 自己改善の限界。人間の改訂を要求する。
# 空虚な改訂で解除しても再発は止まらないので、次の再発で必ず捕まる。
#
# ログは追記専用。ゲートを通すために過去行を消さない。
set -uo pipefail

TASK_ID="${1:-manual}"
LOG=".hw/rules/hd-log.tsv"
[ -f "$LOG" ] || exit 0

python3 - "$TASK_ID" "$LOG" <<'PY'
import json, pathlib, sys

# エージェントの改訂が何回失敗したら人間へエスカレートするか。
# 「同一失敗2回で独立診断を挟む」と同じ物差し。
MAX_FAILED_AGENT_REVISIONS = 2

task_id, log_path = sys.argv[1:3]
rows = []
for line in pathlib.Path(log_path).read_text(encoding="utf-8").splitlines():
    if not line.strip() or line.startswith("#") or line.startswith("ts\t"):
        continue
    parts = line.split("\t")
    if len(parts) >= 3:
        rows.append({"ts": parts[0], "task": parts[1], "category": parts[2]})

# 改訂はカテゴリごとに時系列で保持する(最新1件だけでは失敗回数を数えられない)。
# author 未指定は human 扱い(証拠ゲート導入前の記録との後方互換)。
resolutions = {}
res = pathlib.Path(".hw/rules/hd-resolutions.jsonl")
if res.exists():
    for line in res.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        category, ts = item.get("category", ""), item.get("ts", "")
        if not category or not ts:
            continue
        author = "agent" if item.get("author") == "agent" else "human"
        resolutions.setdefault(category, []).append({"ts": ts, "author": author})
for entries in resolutions.values():
    entries.sort(key=lambda entry: entry["ts"])

# ログ全体の出現順で直近3タスクを決める
distinct = []
for row in rows:
    if row["task"] not in distinct:
        distinct.append(row["task"])
last_three = distinct[-3:]

blocked, observed = [], []
for category in sorted({row["category"] for row in rows}):
    occurrences = [row for row in rows if row["category"] == category]
    latest = max(row["ts"] for row in occurrences)
    same_task = sum(1 for row in occurrences if row["task"] == task_id)
    cross_task = sum(
        1
        for task in last_three
        if any(row["task"] == task for row in occurrences)
    )
    if not (same_task >= 2 or cross_task >= 2):
        continue

    entries = resolutions.get(category, [])
    if not entries:
        blocked.append({"category": category, "reason": "no-revision", "latest": latest})
        continue

    # 各改訂について「その改訂の後、次の改訂までの間に再発したか」を数える。
    # 再発していれば、その改訂は効かなかったという証拠。
    failed_agent = 0
    for index, entry in enumerate(entries):
        following = entries[index + 1]["ts"] if index + 1 < len(entries) else None
        recurred = any(
            row["ts"] > entry["ts"] and (following is None or row["ts"] < following)
            for row in occurrences
        )
        if recurred and entry["author"] == "agent":
            failed_agent += 1

    newest = entries[-1]
    if newest["ts"] <= latest:
        # 改訂の後にまた再発した。その改訂は効かなかった。
        post = sum(1 for row in occurrences if row["ts"] > newest["ts"])
        blocked.append(
            {
                "category": category,
                "reason": "revision-ineffective",
                "latest": latest,
                "author": newest["author"],
                "post": post,
            }
        )
    elif failed_agent >= MAX_FAILED_AGENT_REVISIONS and newest["author"] != "human":
        # 自己改善が繰り返し外している。根本原因を掴めていない証拠なので人間へ。
        blocked.append(
            {
                "category": category,
                "reason": "agent-exhausted",
                "latest": latest,
                "failed": failed_agent,
            }
        )
    else:
        # 最新の改訂より後に再発していない。自己改善を信用して通す。
        observed.append(
            {"category": category, "ts": newest["ts"], "author": newest["author"]}
        )

for item in observed:
    print(
        f"[hw][hd] 経過観察: カテゴリ '{item['category']}' は "
        f"{item['ts']} の改訂({item['author']})以降まだ再発していない。"
        "次に再発したらこの改訂は無効と判定する。"
    )

if blocked:
    for item in blocked:
        category = item["category"]
        if item["reason"] == "no-revision":
            print(
                f"[hw][hd] block: カテゴリ '{category}' が再発"
                f"(最新: {item['latest']})。CLAUDE.md、AGENTS.md、または検証契約を"
                "改訂し、.hw/rules/hd-resolutions.jsonl に"
                f'{{"ts": "<ISO日時>", "category": "{category}", '
                '"rule_change": "<何を変えたか>", "author": "agent", '
                '"evidence": "<根拠のcommitか軌跡ID>"} を追記する。'
            )
        elif item["reason"] == "revision-ineffective":
            who = "自己改善" if item["author"] == "agent" else "人間の改訂"
            print(
                f"[hw][hd] block: カテゴリ '{category}' は{who}の後も "
                f"{item['post']} 件再発した(最新: {item['latest']})。その改訂は"
                "効いていない。同じ手を繰り返さず、独立診断を挟んで別の"
                "アプローチで改訂すること。"
            )
        else:
            print(
                f"[hw][hd] block: カテゴリ '{category}' はエージェントの改訂が "
                f"{item['failed']} 回失敗している(最新: {item['latest']})。"
                "自己改善では根本原因に届いていない。人間が改訂を記録すること"
                '(author: "human")。'
            )
    sys.exit(1)
PY
