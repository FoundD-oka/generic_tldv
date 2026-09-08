#!/usr/bin/env python3
"""hw v2: verification and context preparation, never production authorization."""
import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import urllib.request


def json_bytes(obj):
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE)


def local_path(root, name):
    path = Path(name)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise ValueError('path must be relative to the project: ' + name)
    p = root
    for part in path.parts:
        p /= part
        if p.is_symlink():
            raise ValueError('symlink is not a verified input: ' + name)
    return p


def load_policy(root, raw=None):
    raw = raw if raw is not None else local_path(root, '.hw/project.json').read_bytes()
    p = json.loads(raw)
    if p.get('schema') != 1:
        raise ValueError('unsupported project policy schema')
    commands = p.get('verify')
    if not isinstance(commands, list) or not commands:
        raise ValueError('verification commands are required')
    for command in commands:
        if not isinstance(command, list) or not command or not all(isinstance(a, str) and a for a in command):
            raise ValueError('each verification command must be a nonempty argv array')
    review = p.get('review')
    if not isinstance(review, dict) or not isinstance(review.get('paths'), list) or not all(isinstance(x, str) and x for x in review['paths']):
        raise ValueError('review.paths is required')
    if type(p.get('timeout_seconds')) is not int or not 1 <= p['timeout_seconds'] <= 7200:
        raise ValueError('timeout_seconds must be 1..7200')
    if 'intent_review' in p:
        config = p['intent_review']
        if not isinstance(config, dict) or not isinstance(config.get('paths', []), list) or not all(isinstance(v, str) and v for v in config.get('paths', [])):
            raise ValueError('project.intent_review.paths must be a string array')
        if not isinstance(config.get('model', 'fable'), str) or not config.get('model', 'fable').strip():
            raise ValueError('project.intent_review.model must be a nonempty string')
        if config.get('check_name') is not None and (not isinstance(config['check_name'], str) or not config['check_name'].strip()):
            raise ValueError('intent check_name must be nonempty or null')
        if config.get('app_id') is not None and (type(config['app_id']) is not int or config['app_id'] <= 0):
            raise ValueError('intent app_id must be positive or null')
    return p


def revision(root, ref):
    if not ref or ref.startswith('-'):
        raise ValueError('an explicit valid revision is required')
    return git(root, 'rev-parse', '--verify', ref + '^{commit}').decode().strip()


def snapshot(root):
    head = revision(root, 'HEAD')
    status = git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all')
    diff = git(root, 'diff', '--binary', 'HEAD', '--')
    extra = {}
    for item in git(root, 'ls-files', '--others', '--exclude-standard', '-z').split(b'\0'):
        if item:
            p = local_path(root, os.fsdecode(item))
            extra[os.fsdecode(item)] = sha(p.read_bytes())
    return {'head': head, 'dirty': bool(status), 'sha256': sha(head.encode() + status + diff + json_bytes(extra))}


def run_checks(root, policy, base=None):
    before = snapshot(root)
    evidence = local_path(root, '.hw/evidence/probe').parent
    evidence.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='verify-', dir=evidence))
    commands = []
    # Remove known GitHub token variables. This is not a sandbox or a complete secret scrubber.
    env = {k: v for k, v in os.environ.items() if k not in {'GITHUB_TOKEN', 'GH_TOKEN', 'ACTIONS_RUNTIME_TOKEN'}}
    env.update(HW_VERIFY_ROOT=str(root), HW_VERIFY_HEAD=before['head'], HW_VERIFY_BASE=base or '')
    for i, argv in enumerate(policy['verify']):
        log = folder / (str(i + 1) + '.log')
        with log.open('wb') as stream:
            try:
                process = subprocess.Popen(argv, cwd=root, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                           start_new_session=True)
                code = process.wait(timeout=policy['timeout_seconds'])
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                stream.write(b'\n[hw] verification timed out\n')
                code = 124
            except OSError as e:
                stream.write(str(e).encode())
                code = 127
        commands.append({'argv': argv, 'exit_code': code, 'log': str(log.relative_to(root)), 'log_sha256': sha(log.read_bytes())})
        if code:
            break
    after = snapshot(root)
    passed = all(c['exit_code'] == 0 for c in commands) and len(commands) == len(policy['verify']) and before == after
    record = {'schema': 1, 'status': 'passed' if passed else 'failed', 'root': str(root),
              'before': before, 'after': after, 'commands': commands,
              'authority': 'local_measurement_not_authorization'}
    (folder / 'result.json').write_bytes(json_bytes(record))
    return record


def task_data(root, name, raw=None):
    if name is None:
        return None
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', name):
        raise ValueError('invalid task id')
    obj = json.loads(raw if raw is not None else local_path(root, '.hw/tasks/' + name + '.json').read_bytes())
    if obj.get('schema') != 1 or not isinstance(obj.get('purpose'), str) or not obj['purpose'].strip():
        raise ValueError('task purpose is required')
    for key in ['acceptance', 'constraints', 'sources']:
        if not isinstance(obj.get(key), list) or not all(isinstance(v, str) and v.strip() for v in obj[key]):
            raise ValueError('task.' + key + ' must be a string array')
    if not obj['acceptance'] or type(obj.get('review_required')) is not bool:
        raise ValueError('task acceptance and explicit review_required are required')
    scopes = obj.get('review_paths', ['*'])
    if not isinstance(scopes, list) or not scopes or not all(isinstance(s, str) and s for s in scopes):
        raise ValueError('task.review_paths must be a nonempty string array')
    intent = intent_config(obj)
    # Deliberately do not copy builder narratives or arbitrary extra fields into QA request.
    return {**{k: obj[k] for k in ['schema', 'purpose', 'acceptance', 'constraints', 'sources', 'review_required']}, 'review_paths': scopes, **({'intent_review': intent} if intent is not None else {})}



def intent_config(task):
    if 'intent_review' not in task:
        return None
    config = task['intent_review']
    if not isinstance(config, dict) or type(config.get('required')) is not bool:
        raise ValueError('task.intent_review requires an explicit boolean required')
    for key in ['sources', 'design', 'evidence']:
        values = config.get(key, [])
        if not isinstance(values, list) or not all(isinstance(v, str) and v.strip() for v in values):
            raise ValueError('intent_review.' + key + ' must be a path array')
        if len(set(values)) != len(values):
            raise ValueError('duplicate intent input paths')
        for value in values:
            if Path(value).is_absolute() or '..' in Path(value).parts or not Path(value).parts:
                raise ValueError('intent input must be relative without traversal')
    baseline = config.get('baseline')
    if baseline is not None and (not isinstance(baseline, str) or not baseline.strip() or Path(baseline).is_absolute() or '..' in Path(baseline).parts):
        raise ValueError('intent baseline must be a relative path')
    if config['required'] and not config.get('sources'):
        raise ValueError('enabled intent review requires raw sources')
    return config


def tracked_input(root, name, head):
    path = local_path(root, name)
    mode = git(root, 'ls-tree', head, '--', name).split(b' ', 1)[0]
    if mode not in {b'100644', b'100755'} or not path.is_file():
        raise ValueError('intent input must be a tracked regular file: ' + name)
    raw = path.read_bytes()
    if raw != git(root, 'show', head + ':' + name):
        raise ValueError('intent input differs from committed bytes: ' + name)
    return raw


def intent_tasks(root, info, policy):
    def tasks_at(ref):
        result = {}
        for path in git(root, 'ls-tree', '-r', '--name-only', ref, '--', '.hw/tasks/').decode().splitlines():
            if path.endswith('.json'):
                if Path(path).parent != Path('.hw/tasks'):
                    raise ValueError('archive historical tasks outside .hw/tasks/')
                result[Path(path).stem] = task_data(root, Path(path).stem, git(root, 'show', ref + ':' + path))
        return result
    current, previous = tasks_at(info['head']), tasks_at(info['base'])
    def applies(task, name):
        return '.hw/tasks/' + name + '.json' in info['paths'] or any(fnmatch.fnmatchcase(path, pattern) for path in info['paths'] for pattern in task['review_paths'])
    def enabled(task):
        return bool(task.get('intent_review', {}).get('required'))
    def unchanged_archive(name, task):
        source, archive = '.hw/tasks/' + name + '.json', 'docs/hw-tasks/' + name + '.json'
        if name in current or not {source, archive}.issubset(info['paths']):
            return False
        if git(root, 'ls-tree', info['base'], '--', archive):
            return False  # Retirement requires adding an archive, not replacing an earlier one.
        if any(fnmatch.fnmatchcase(path, pattern) for path in info['paths'] if path not in {source, archive}
               for pattern in task['review_paths']):
            return False
        try:
            return tracked_input(root, archive, info['head']) == git(root, 'show', info['base'] + ':' + source)
        except (OSError, ValueError, subprocess.SubprocessError):
            return False
    required = {name: task for name, task in current.items() if enabled(task) and applies(task, name)}
    for name, task in previous.items():
        if enabled(task) and applies(task, name):
            if unchanged_archive(name, task):
                continue
            if name not in current or not enabled(current[name]) or not applies(current[name], name):
                raise ValueError('protected base intent requirement removed or downgraded: ' + name)
            if any(any(fnmatch.fnmatchcase(path, pattern) for pattern in task['review_paths']) and not any(fnmatch.fnmatchcase(path, pattern) for pattern in current[name]['review_paths']) for path in info['paths']):
                raise ValueError('protected base intent scope downgraded: ' + name)
            required[name] = current[name]
    for path in info['paths']:
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in policy.get('intent_review', {}).get('paths', [])):
            suitable = {name: task for name, task in current.items() if enabled(task) and any(fnmatch.fnmatchcase(path, pattern) for pattern in task['review_paths'])}
            if not suitable:
                raise ValueError('intent policy path has no enabled applicable task: ' + path)
            required.update(suitable)
    return required


def intent_packet(root, name, task, phase, info, model="fable"):
    if phase not in {'baseline', 'design', 'delivery'}:
        raise ValueError('invalid intent phase')
    config = task.get('intent_review')
    if not config or not config['required']:
        raise ValueError('task intent review is not enabled')
    # The documented role is the executed protocol, not a second abbreviated prompt.
    role = tracked_input(root, '.hw/roles/intent-reviewer.md', info['head'])
    if not role.strip():
        raise ValueError('intent reviewer protocol is empty')
    payload, inputs = {}, {}
    def add(group, paths):
        for i, path in enumerate(paths):
            raw = tracked_input(root, path, info['head'])
            target = 'inputs/' + group + '/' + str(i) + '/' + Path(path).name
            payload[target] = raw
            inputs.setdefault(group, []).append({'path': path, 'file': target, 'sha256': sha(raw)})
    add('sources', config['sources'])
    source_binding = sha(json_bytes({'task': name, 'sources': inputs['sources']}))
    manifest = {'schema': 1, 'axis': 'intent', 'role': 'intent-reviewer', 'phase': phase, 'task': name,
                'source_binding': source_binding, 'model': model, 'inputs': inputs,
                'review_protocol_sha256': sha(role),
                'isolation_enforced': False, 'authority': 'advisory_evidence_not_authorization'}
    if phase != 'baseline':
        if not config.get('baseline') or not config.get('design'):
            raise ValueError('intent design/delivery requires baseline and design inputs')
        add('baseline', [config['baseline']])
        baseline = json.loads(payload[inputs['baseline'][0]['file']])
        if not isinstance(baseline, dict):
            raise ValueError('baseline must be a JSON object')
        expectations = baseline.get('expectations')
        if baseline.get('source_binding') != source_binding:
            raise ValueError('baseline source_binding is stale')
        if not isinstance(expectations, list) or not expectations or not all((isinstance(v, str) and v.strip()) or (isinstance(v, dict) and bool(v)) for v in expectations):
            raise ValueError('baseline requires nonempty expectations')
        add('design', config['design'])
        if phase == 'delivery':
            if not config.get('evidence'):
                raise ValueError('intent delivery requires nonempty evidence')
            add('evidence', config['evidence'])
        manifest['change'] = {k: info[k] for k in ['base', 'merge_base', 'head', 'diff_sha256', 'policy_sha256', 'binding']}
        manifest['binding'] = sha(json_bytes({'axis': 'intent', 'phase': phase, 'task': name,
                                             'source_binding': source_binding, 'change': manifest['change'],
                                             'inputs': inputs, 'review_protocol_sha256': sha(role)}))
    payload['role.md'] = role
    manifest['files'] = {path: sha(raw) for path, raw in payload.items()}
    return manifest, payload


def check_intent_receipt(receipt, expected):
    if not isinstance(receipt, dict):
        raise ValueError('intent receipt must be a JSON object')
    for key, value in [('schema', 1), ('axis', 'intent'), ('phase', expected['phase']), ('task', expected['task']),
                       ('binding', expected['binding']), ('source_binding', expected['source_binding'])]:
        if receipt.get(key) != value:
            raise ValueError('missing, incorrect, or stale intent review ' + key)
    if receipt.get('unverified') != []:
        raise ValueError('intent review has missing or unverified evidence')
    if not isinstance(receipt.get('reviewer'), str) or not receipt['reviewer'].strip():
        raise ValueError('intent review must name a reviewer')
    if not isinstance(receipt.get('findings'), list) or any(not isinstance(f, dict) or not isinstance(f.get('scenario'), str) or not f['scenario'].strip() for f in receipt['findings']):
        raise ValueError('intent findings require a scenario string')
    # Reuse evidence validation without accepting intent receipts on the technical axis.
    check_receipt(dict(receipt, axis='technical'), expected['binding'])


def intent_ci_config(policy, name):
    config = policy.get('intent_review', {})
    if not config.get('check_name') or not config.get('app_id'):
        raise ValueError('independent intent review integration is not configured; no local JSON fallback in CI')
    if config['app_id'] == policy['review'].get('app_id') and policy['review'].get('check_name') in {config['check_name'], config['check_name'] + '/' + name}:
        raise ValueError('intent and technical review require separate check configurations')
    return dict(config, check_name=config['check_name'] + '/' + name)


def intent_gate(root, args):
    if snapshot(root)['dirty']:
        raise ValueError('intent gate requires committed changes')
    base, head, policy = local_inputs(root, args)
    info, _ = change(root, base, head, policy)
    if info['head'] != revision(root, 'HEAD'):
        raise ValueError('intent gate must run in the target checkout')
    intent_tasks(root, info, policy)
    expected, _ = intent_packet(root, args.task, task_data(root, args.task), args.phase, info)
    check_intent_receipt(json.loads(args.intent_review.read_bytes()), expected)
    return {'status': 'checks_passed', 'intent_review': 'passed', 'phase': args.phase,
            'task': args.task, 'binding': expected['binding'], 'authorization': 'not_granted'}

def change(root, base_ref, head_ref, policy, task=None):
    base, head = revision(root, base_ref), revision(root, head_ref)
    common = git(root, 'merge-base', base, head).decode().strip()
    paths = [os.fsdecode(p) for p in git(root, 'diff', '--no-renames', '--name-only', '-z', common, head, '--').split(b'\0') if p]
    patch = git(root, 'diff', '--no-ext-diff', '--binary', common, head, '--')
    binding = {'base': base, 'merge_base': common, 'head': head, 'diff_sha256': sha(patch),
               'policy_sha256': sha(json_bytes(policy))}
    # Unknown project domains are added in project.json; control-plane changes always require review.
    patterns = ['.hw/*', '.github/*', '.claude/*', '.codex/*', 'AGENTS.md', 'CLAUDE.md'] + policy['review']['paths']
    # All files in tasks/ are active requirements. CLI task selection cannot disable a requirement in CI.
    active = []
    tracked_tasks = git(root, 'ls-tree', '-r', '--name-only', head, '--', '.hw/tasks/').decode().splitlines()
    for p in local_path(root, '.hw/tasks').glob('*.json'):
        if str(p.relative_to(root)) not in tracked_tasks:
            raise ValueError('active task is not committed (check Git ignore rules): ' + p.name)
    for name in tracked_tasks:
        if name.endswith('.json'):
            if Path(name).parent != Path('.hw/tasks'):
                raise ValueError('archive historical tasks outside .hw/tasks/')
            active.append(task_data(root, Path(name).stem))
    required = any(fnmatch.fnmatchcase(p, pattern) for p in paths for pattern in patterns)
    required = required or any(t['review_required'] and any(fnmatch.fnmatchcase(p, pattern) for p in paths for pattern in t['review_paths']) for t in active)
    return {**binding, 'binding': sha(json_bytes(binding)), 'paths': paths, 'review_required': required}, patch


def check_receipt(receipt, binding):
    if not isinstance(receipt, dict):
        raise ValueError('review receipt must be a JSON object')
    if receipt.get('axis', 'technical') != 'technical':
        raise ValueError('technical review requires the technical axis')
    if receipt.get('schema') != 1 or receipt.get('binding') != binding:
        raise ValueError('missing or stale review binding')
    if receipt.get('verdict') != 'pass':
        raise ValueError('review did not pass')
    if receipt.get('context_mode') != 'fresh' or not isinstance(receipt.get('reviewer'), str) or not receipt['reviewer'].strip():
        raise ValueError('review must identify the fresh-context reviewer')
    # Older technical receipts omit this optional field. Explicit missing evidence must block.
    if receipt.get('unverified', []) != []:
        raise ValueError('review has missing or unverified evidence')
    findings = receipt.get('findings')
    if not isinstance(findings, list) or not all(isinstance(f, dict) and type(f.get('blocking')) is bool and f.get('scenario') for f in findings):
        raise ValueError('review findings must include blocking and scenario')
    if any(f['blocking'] for f in findings):
        raise ValueError('blocking finding; contract membership does not waive it')
    if not isinstance(receipt.get('checks'), list) or not receipt['checks'] or not all(isinstance(c, dict) and isinstance(c.get('method'), str) and c['method'].strip() and isinstance(c.get('result'), str) and c['result'].strip() for c in receipt['checks']):
        raise ValueError('review checks and observations are required')


def select_check(runs, config, head, binding):
    matches = [r for r in runs if r.get('name') == config['check_name'] and r.get('app', {}).get('id') == config['app_id']]
    if not matches:
        raise ValueError('required independent check is absent')
    last = max(matches, key=lambda r: r['id'])
    if last.get('head_sha') != head or last.get('external_id') != binding or last.get('status') != 'completed' or last.get('conclusion') != 'success':
        raise ValueError('independent check is stale, incomplete, or unsuccessful')


def external_review(head, binding, config):
    if not config.get('check_name') or type(config.get('app_id')) is not int or config['app_id'] <= 0:
        raise ValueError('independent review integration is not configured; no local JSON fallback in CI')
    repo = os.environ.get('GITHUB_REPOSITORY', '')
    token = os.environ.get('GITHUB_TOKEN', '')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo) or not token:
        raise ValueError('GitHub repository and read-only token are required')
    runs = []
    for page in range(1, 101):
        url = f'https://api.github.com/repos/{repo}/commits/{head}/check-runs?filter=all&per_page=100&page={page}'
        req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'})
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.load(response)
        runs.extend(data['check_runs'])
        if len(runs) >= data['total_count']:
            break
    else:
        raise ValueError('check pagination limit exceeded')
    select_check(runs, config, head, binding)


def ci_inputs(root):
    if os.environ.get('GITHUB_EVENT_NAME') != 'pull_request':
        raise ValueError('CI gate requires a pull_request event; other events need an explicit adapter')
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    pr = event['pull_request']
    base, head = pr['base']['sha'], pr['head']['sha']
    if not all(re.fullmatch(r'[0-9a-f]{40}', r) for r in [base, head]):
        raise ValueError('invalid event revisions')
    if revision(root, 'HEAD') != head:
        raise ValueError('checkout does not match PR head')
    raw = git(root, 'show', base + ':.hw/project.json')
    return base, head, load_policy(root, raw)


def local_inputs(root, args):
    current = load_policy(root)
    base = revision(root, args.base or current.get('base_ref'))
    # Match CI: project policy changes take effect after a separately reviewed merge.
    trusted = load_policy(root, git(root, 'show', base + ':.hw/project.json'))
    return base, args.head, trusted


def gate(root, args):
    if snapshot(root)['dirty']:
        raise ValueError('final gate requires committed changes; use verify during development')
    if args.ci:
        base, head, policy = ci_inputs(root)
    else:
        base, head, policy = local_inputs(root, args)
    task = task_data(root, args.task)
    info, _ = change(root, base, head, policy, task)
    if revision(root, 'HEAD') != info['head']:
        raise ValueError('run from the target checkout; do not copy .hw between worktrees')
    if info['review_required']:
        if args.ci:
            external_review(info['head'], info['binding'], policy['review'])
        else:
            if not args.review:
                raise ValueError('independent review required; generate context and obtain a fresh review')
            check_receipt(json.loads(args.review.read_text()), info['binding'])
    required_intent = intent_tasks(root, info, policy)
    receipts = []
    if not args.ci:
        receipts = [json.loads(Path(p).read_bytes()) for p in (getattr(args, 'intent_review', None) or [])]
    for name, intent_task in required_intent.items():
        expected, _ = intent_packet(root, name, intent_task, 'delivery', info)
        if args.ci:
            external_review(info['head'], expected['binding'], intent_ci_config(policy, name))
        else:
            candidates = [r for r in receipts if r.get('task') == name]
            if len(candidates) != 1:
                raise ValueError('exactly one independent intent review required for task: ' + name)
            check_intent_receipt(candidates[0], expected)
    measured = run_checks(root, policy, base=base)
    if measured['status'] != 'passed' or snapshot(root)['dirty'] or revision(root, 'HEAD') != info['head']:
        raise ValueError('verification failed or changed the checkout; inspect .hw/evidence/')
    return {'status': 'checks_passed', **info, 'verification': measured,
            'intent_review': {'status': 'passed' if required_intent else 'not_required', 'tasks': sorted(required_intent)},
            'authorization': 'not_granted', 'review_authority': 'external_check' if args.ci and (info['review_required'] or required_intent) else 'local_advisory'}


def context(root, args):
    if snapshot(root)['dirty']:
        raise ValueError('commit the requested inputs before preparing final review context')
    task = task_data(root, args.task)
    base, head, policy = local_inputs(root, args)
    info, patch = change(root, base, head, policy, task)
    if info['head'] != revision(root, 'HEAD'):
        raise ValueError('context must be prepared in the target checkout')
    output = args.output.resolve()
    if output == root or root in output.parents:
        raise ValueError('context bundle must be outside the project')
    if args.role == 'intent-reviewer':
        manifest, payload = intent_packet(root, args.task, task, getattr(args, 'phase', 'delivery'), info, policy.get('intent_review', {}).get('model', 'fable'))
        output.mkdir(parents=True, exist_ok=False)
        for name, raw in payload.items():
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        (output / 'manifest.json').write_bytes(json_bytes(manifest))
        return manifest
    output.mkdir(parents=True, exist_ok=False)
    (output / 'request.json').write_bytes(json_bytes(task))
    (output / 'changes.patch').write_bytes(patch)
    (output / 'role.md').write_bytes(local_path(root, '.hw/roles/' + args.role + '.md').read_bytes())
    manifest = {'schema': 1, 'role': args.role, **info, 'source_root': str(root),
                'not_imported_as_inputs': ['parent_conversation', 'extra_task_fields', 'other_reviewer_verdicts'],
                'caveat': 'The complete repository diff may itself contain narratives. This manifest does not enforce isolation.',
                'isolation_enforced': False,
                'files': {p.name: sha(p.read_bytes()) for p in sorted(output.iterdir())}}
    (output / 'manifest.json').write_bytes(json_bytes(manifest))
    return manifest


def doctor(root):
    policy = load_policy(root)
    problems = []
    path = local_path(root, '.hw/verify.sh')
    if policy['verify'] == [['bash', '.hw/verify.sh']] and (not path.is_file() or 'HW_UNCONFIGURED' in path.read_text()):
        problems.append('project verification is not configured')
    manifest = json.loads(local_path(root, '.hw/install.json').read_bytes())
    modified = [rel for rel, checksum in manifest['files'].items()
                if not local_path(root, rel).is_file() or sha(local_path(root, rel).read_bytes()) != checksum]
    tracked = set(git(root, 'ls-files', '-z').decode().split('\0'))
    required_files = set(manifest['files']) | {'.hw/project.json', '.hw/install.json'}
    if policy['verify'] == [['bash', '.hw/verify.sh']]:
        required_files.add('.hw/verify.sh')
    untracked = sorted(required_files - tracked)
    if untracked:
        problems.append('installation files are not tracked; CI and new checkouts cannot receive them')
    review = policy['review']
    if not review.get('check_name') or not review.get('app_id'):
        problems.append('independent GitHub check is not configured (required changes will block in CI)')
    intent = policy.get('intent_review', {})
    enabled = bool(intent.get('paths')) or any(task_data(root, p.stem).get('intent_review', {}).get('required')
                                             for p in local_path(root, '.hw/tasks').glob('*.json'))
    intent_status = 'optional_disabled'
    if enabled:
        try:
            intent_ci_config(policy, 'task')
            intent_status = 'configured_not_verified'
        except ValueError:
            intent_status = 'unconfigured'
            problems.append('independent intent GitHub check is not configured (enabled intent changes will block in CI)')
    return {'status': 'needs_setup' if problems else 'locally_configured', 'problems': problems,
            'intent_review': {'status': intent_status, 'model': intent.get('model', 'fable')},
            'modified_managed_files': modified, 'untracked_install_files': untracked, 'external_protection': 'not_verified',
            'production_permissions': 'not_provisioned', 'agent_context_isolation': 'runtime_configuration_required'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('verify')
    sub.add_parser('doctor')
    g = sub.add_parser('gate')
    g.add_argument('--base')
    g.add_argument('--head', default='HEAD')
    g.add_argument('--task')
    g.add_argument('--review', type=Path)
    g.add_argument('--ci', action='store_true')
    g.add_argument('--intent-review', type=Path, action='append', default=[])
    c = sub.add_parser('context')
    c.add_argument('--base')
    c.add_argument('--head', default='HEAD')
    c.add_argument('--task', required=True)
    c.add_argument('--role', choices=['researcher', 'builder', 'verifier', 'intent-reviewer'], required=True)
    c.add_argument('--output', type=Path, required=True)
    c.add_argument('--phase', choices=['baseline', 'design', 'delivery'], default='delivery')
    i = sub.add_parser('intent-gate')
    i.add_argument('--base')
    i.add_argument('--head', default='HEAD')
    i.add_argument('--task', required=True)
    i.add_argument('--phase', choices=['design', 'delivery'], required=True)
    i.add_argument('--intent-review', type=Path, required=True)
    args = parser.parse_args()
    root = args.project.resolve()
    try:
        if Path(git(root, 'rev-parse', '--show-toplevel').decode().strip()).resolve() != root:
            raise ValueError('--project must be the checkout root')
        if args.command == 'verify':
            result = run_checks(root, load_policy(root))
        elif args.command == 'gate':
            result = gate(root, args)
        elif args.command == 'intent-gate':
            result = intent_gate(root, args)
        elif args.command == 'context':
            result = context(root, args)
        else:
            result = doctor(root)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get('status') in {'failed', 'needs_setup'} else 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as e:
        print('[hw] ' + str(e), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
