#!/usr/bin/env bash
# SubagentStop フック（.claude/settings.json、matcher: hw-builder）。
# Builder が完了を報告するとき、verify が通るまで差し戻す。判断を仰ぐ報告（先頭行が HW-ESCALATE:）は
# 止めない。無限に続けないよう、同じ Builder を3回差し戻したら止める。
set -uo pipefail
input="$(cat)"
IFS=$'\t' read -r cwd sid escalate < <(printf '%s' "$input" | python3 -c '
import json, sys
d = json.load(sys.stdin)
escalate = (d.get("last_assistant_message") or "").lstrip().startswith("HW-ESCALATE:")
print(d.get("cwd") or ".", d.get("agent_id") or d.get("session_id") or "unknown", "yes" if escalate else "no", sep="\t")' 2>/dev/null) || exit 0
[ "$escalate" = yes ] && exit 0
root="$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null)" || exit 0
[ -f "$root/.hw/runtime/hw.py" ] || exit 0
unconfigured="$(python3 - "$root" <<'PY' 2>/dev/null
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
verify = json.loads((root / '.hw/project.json').read_text()).get('verify')
script = root / '.hw/verify.sh'
print('yes' if verify == [['bash', '.hw/verify.sh']] and (not script.is_file() or 'HW_UNCONFIGURED' in script.read_text()) else 'no')
PY
)"
if [ "$unconfigured" = yes ]; then
  echo "hw: verify が未設定のため、完了前の自動検証を省略した。主担当へ未検証として報告する。" >&2
  exit 0
fi
state="$(git -C "$root" rev-parse --path-format=absolute --git-path hw-builder-stop)" || exit 0
mkdir -p "$state"
count_file="$state/$sid"
count="$(cat "$count_file" 2>/dev/null || echo 0)"
if out="$(python3 "$root/.hw/runtime/hw.py" --project "$root" verify 2>&1)"; then
  rm -f "$count_file"
  exit 0
fi
if [ "$count" -ge 3 ]; then
  rm -f "$count_file"
  echo "hw: verify が3回の差し戻し後も失敗している。完了とせず、失敗内容と .hw/evidence/ の記録を主担当へ報告する。" >&2
  exit 0
fi
echo $((count + 1)) > "$count_file"
{
  echo "hw: verify が失敗した。原因を直し、verify が通ってから完了する。テストや検証を弱めて通さない。"
  echo "要求の食い違いや権限など、主担当の判断が要って直せない場合は、報告の先頭行を"
  echo "'HW-ESCALATE: <理由>' にして終える。"
  printf '%s\n' "$out" | tail -40
} >&2
exit 2
