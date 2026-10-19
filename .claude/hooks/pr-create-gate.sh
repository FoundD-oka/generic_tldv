#!/usr/bin/env bash
# PreToolUse hook (Bash, if: "Bash(gh *)").
# Blocks PR creation unless the last local hw gate passed on the current, clean HEAD.
# Local convenience only; CI and the merge decision remain the authority.
set -uo pipefail
# The `if` filter is best-effort: Claude Code runs the hook for any command it cannot parse
# (heredocs with braces, $(...), backticks, ...), and `if` holds a single rule, so it is set to
# every gh command. Re-check that `gh pr create` or `gh pr new` is actually run at a command
# position, and let every other command through.
# The target checkout is the session cwd, or the directory of a leading `cd <dir> &&` / `cd <dir>;`
# (common with git worktrees, where the session cwd is a different checkout).
# Read the script with `read`, not `$(cat <<'PY' ...)`: bash 3.2 (macOS /bin/bash) misparses
# backticks in a heredoc inside a command substitution.
IFS= read -r -d '' detect <<'PY'
import json, os, re, shlex, sys

SHELLS = {'sh', 'bash', 'zsh', 'dash', 'ksh'}
WRAPPERS = {'command', 'builtin', 'exec', 'nohup', 'time', 'sudo', 'env', 'xargs', 'nice', 'timeout'}
KEYWORDS = {'!', '{', '}', 'then', 'do', 'else', 'elif', 'if', 'while', 'until'}
HEREDOC = re.compile(r"(?<!<)<<(?!<)-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
SUBST = re.compile(r"\$\(|`")


def strip_heredocs(text):
    """Drop heredoc bodies (data, e.g. commit messages); keep them when a shell reads them."""
    out, lines, i = [], text.split('\n'), 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        for m in HEREDOC.finditer(line):
            body = []
            while i < len(lines) and lines[i].strip() != m.group(2):
                body.append(lines[i])
                i += 1
            i += 1
            words = set(re.findall(r'[A-Za-z]+', line[:m.start()]))
            if words & SHELLS:
                out.extend(body)
    return '\n'.join(out)


def tokens(text):
    lex = shlex.shlex(text, posix=True, punctuation_chars=';&|()<>`\n')
    lex.whitespace = ' \t\r'
    lex.commenters = ''
    lex.whitespace_split = True
    return list(lex)


def is_gh_pr_create(args):
    if not args or os.path.basename(args[0]) != 'gh':
        return False
    # `gh pr new` is an alias of create; -R/--repo takes a value and may come before or after `pr`.
    rest, i = [], 1
    while i < len(args) and len(rest) < 2:
        if args[i] in {'-R', '--repo'}:
            i += 2
            continue
        if not args[i].startswith('-'):
            rest.append(args[i])
        i += 1
    return rest[:1] == ['pr'] and rest[1:] in (['create'], ['new'])


def runs_pr_create(text, depth=0):
    if depth > 5:
        return False
    text = strip_heredocs(text.replace('\\\n', ' '))
    toks = tokens(text)
    # Command substitutions keep their text inside quoted tokens; scan them as commands too.
    for tok in toks:
        if SUBST.search(tok):
            inner = SUBST.sub('\n', tok).replace(')', '\n').replace('`', '\n')
            if runs_pr_create(inner, depth + 1):
                return True
    cmd, at_start = [], True
    for tok in toks + [';']:
        punct = tok and all(c in ';&|()<>`\n' for c in tok)
        if punct and not set(tok) & set('<>'):
            if check(cmd, depth):
                return True
            cmd, at_start = [], True
            continue
        if at_start and (tok in KEYWORDS or re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', tok)):
            continue
        at_start = False
        cmd.append(tok)
    return False


def check(cmd, depth):
    while cmd and os.path.basename(cmd[0]) in WRAPPERS:
        # Skip wrapper options, their arguments, assignments and durations (env -u X, timeout 60).
        i, takes_arg = 1, False
        while i < len(cmd) and os.path.basename(cmd[i]) not in SHELLS | {'gh'} and (takes_arg or cmd[i].startswith('-') or '=' in cmd[i] or cmd[i][:1].isdigit()):
            takes_arg = cmd[i].startswith('-') and '=' not in cmd[i] and len(cmd[i]) == 2
            i += 1
        cmd = cmd[i:]
    if is_gh_pr_create(cmd):
        return True
    if cmd and os.path.basename(cmd[0]) in SHELLS:
        for i, arg in enumerate(cmd[1:-1], 1):
            if re.match(r'^-[A-Za-z]*c[A-Za-z]*$', arg):
                return runs_pr_create(cmd[i + 1], depth + 1)
    if cmd and cmd[0] == 'eval':
        return runs_pr_create(' '.join(cmd[1:]), depth + 1)
    return False


d = json.load(sys.stdin)
cwd = d.get('cwd') or '.'
command = (d.get('tool_input') or {}).get('command') or ''
try:
    gated = runs_pr_create(command)
except ValueError:
    # Unbalanced quotes: fall back to a plain search so PR creation is never waved through.
    repo = r'(\s+(-R|--repo)(\s+|=)\S+)?'
    gated = re.search(r'(^|[\s;&|(){}`/])gh' + repo + r'\s+pr' + repo + r'\s+(create|new)\b', command) is not None
if not gated:
    print('skip')
    sys.exit()
m = re.match(r'\s*cd\s+(.+?)\s*(?:&&|;)', command)
if m:
    try:
        print(os.path.join(cwd, os.path.expanduser(shlex.split(m.group(1))[0])))
        sys.exit()
    except (ValueError, IndexError):
        pass
print(cwd)
PY
cwd="$(python3 -c "$detect" 2>/dev/null)" || cwd="."
[ "$cwd" = "skip" ] && exit 0
root="$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null)" || exit 0
[ -f "$root/.hw/runtime/hw.py" ] || exit 0
if ! out="$(python3 "$root/.hw/runtime/hw.py" --project "$root" pr-check 2>&1)"; then
  printf '%s\n' "$out" >&2
  echo "hw: この commit で 'python3 .hw/runtime/hw.py gate --base <比較先>'（必要なら --task/--review）を通してから PR を作成する。" >&2
  exit 2
fi
exit 0
