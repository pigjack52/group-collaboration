"""Fetch-only discovery. Git objects are evidence; local receipts are disposable."""
import json
import os
from pathlib import Path

from group_events import EventError, parse_event
from group_git import GitError, _git, inspect_status, format_status


def refs(root):
    raw, _ = _git(root, 'for-each-ref', '--format=%(refname) %(objectname)',
                  'refs/remotes/origin/')
    return dict(line.split(' ', 1) for line in raw.splitlines()
                if not line.startswith('refs/remotes/origin/HEAD '))


def event_entries(root, tip):
    tree, _ = _git(root, '--no-replace-objects', 'ls-tree', '-r', '-z', tip,
                   '--', '.group/events/')
    for entry in filter(None, tree.split('\0')):
        meta, path = entry.split('\t', 1)
        if path.endswith('.json'):
            mode, kind, blob = meta.split()
            yield path, mode, kind, blob


def read_discovered_event(root, path, mode, kind, blob, group_id):
    if mode not in ('100644', '100755') or kind != 'blob':
        raise EventError('event must be a regular Git blob')
    raw, _ = _git(root, '--no-replace-objects', 'cat-file', 'blob', blob)
    event = parse_event(raw)
    if path != f'.group/events/{event["event_id"]}.json':
        raise EventError('event ID does not match filename')
    if event['group_id'] != group_id:
        raise EventError('group_id differs from group.json')
    return event


def discover(root, config):
    """Print progress before fetch; only acknowledge a complete scan atomically."""
    root = Path(root)
    before = inspect_status(root, config['canonical_branch'], allow_missing_upstream=True)
    print('SYNC discovery only; no code integrated; not the full SYNC procedure.')
    print('Before fetch:\n' + format_status(before), flush=True)
    cache = root / '.group/local/sync-discover.json'
    errors, risks = [], []
    try:
        if not isinstance(config.get('group_id'), str) or not config['group_id'].strip():
            raise GitError('Invalid group_id')
        if not before.origin_exists:
            raise GitError('No origin remote; discovery not completed')
        state = {'seen': {}, 'refs': {}}
        if cache.exists():
            state = json.loads(cache.read_text(encoding='utf-8'))
            if (not isinstance(state, dict) or set(state) != {'seen', 'refs'}
                    or any(not isinstance(state[k], dict) for k in state)
                    or any(not isinstance(v, str) for k in state for v in state[k].values())):
                raise ValueError('invalid discovery cache schema')
        old = refs(root)
        # Explicit destination prevents configured fetch refspecs writing local heads.
        # No tags/submodules/hooks, no worktree updates; prune remote tracking only.
        _git(root, '-c', 'core.hooksPath=' + os.devnull, 'fetch',
             '--no-tags', '--no-recurse-submodules', '--no-write-fetch-head',
             '--prune', '--no-prune-tags', '--refmap=', 'origin',
             '+refs/heads/*:refs/remotes/origin/*')
        print('Fetch completed; remote tracking information updated.')
        new = refs(root)
        canonical = 'refs/remotes/origin/' + config['canonical_branch']
        if canonical not in new:
            errors.append(f'Canonical remote branch unavailable: {canonical}')
        baseline = dict(state['refs'], **old)
        for ref in sorted(set(baseline) | set(new)):
            previous, current = baseline.get(ref), new.get(ref)
            if current is None:
                risks.append(f'Remote branch deleted: {ref}; previous={previous}')
                continue
            if previous == current:
                continue
            print(f'Branch: {ref}; before={previous or "(new)"}; after={current}')
            if previous:
                changes, _ = _git(root, 'diff', '--name-status', previous, current,
                                  '--', '.group/events/')
                for change in changes.splitlines():
                    if not change.startswith('A\t'):
                        risks.append(f'Published event modified/deleted [{ref}]: {change}')
                _, code = _git(root, 'merge-base', '--is-ancestor', previous, current,
                               allowed=(0, 1, 128))
                if code:
                    risks.append(f'History rewritten or ancestry unavailable: {ref} {previous} -> {current}')
            # Log exact incoming commits even if an earlier attempt fetched but failed scanning.
            known = state['refs'].get(ref)
            args = [current] + (['^' + known] if known else [])
            commits, _ = _git(root, 'log', '--format=%H %s', *args, '--')
            for line in commits.splitlines():
                print(f'Incoming commit [{ref}]: {line}')
        seen = dict(state['seen'])
        records = {}
        count = 0
        for ref, tip in sorted(new.items()):
            if ref != canonical and not ref.startswith('refs/remotes/origin/people/'):
                continue
            for path, mode, kind, blob in event_entries(root, tip):
                source = f'{ref}@{tip}:{path}'
                try:
                    event = read_discovered_event(root, path, mode, kind, blob, config['group_id'])
                    identifier = event['event_id']
                    fingerprint = json.dumps(event, sort_keys=True, ensure_ascii=True)
                    prior = records.get(identifier, seen.get(identifier))
                    if prior is not None and prior != fingerprint:
                        risks.append(f'Immutable event changed or conflicting event_id: {identifier} at {source}')
                    records[identifier] = fingerprint
                    if seen.get(identifier) != fingerprint:
                        count += 1
                        print(f'New event [{source}]: ' + fingerprint)
                        seen[identifier] = fingerprint
                except (EventError, UnicodeError) as exc:
                    errors.append(f'Invalid event {source}: {exc}')
        events = [json.loads(value) for value in records.values()]
        for field in ('change_id', 'question_id'):
            groups = {}
            for event in events:
                if field in event:
                    groups.setdefault(event[field], []).append(event)
            for key, values in groups.items():
                # Preserve all declarations; no timestamp-based winner is inferred.
                for kind in {e['event_type'] for e in values}:
                    same = [e for e in values if e['event_type'] == kind]
                    if len(same) > 1:
                        risks.append(f'Potentially conflicting {kind} records for {field}={key}: '
                                     + ', '.join(e['event_id'] for e in same))
        outcomes = {}
        for event in events:
            if event['event_type'] == 'test_result':
                key = ('test_result', event['commit'], event['scope'])
                outcomes.setdefault(key, set()).add(event['automated_result'])
            elif event['event_type'] == 'integration_result':
                key = ('integration_result', event['change_id'], event['target_branch'])
                outcomes.setdefault(key, set()).add(event['result'])
        for key, values in outcomes.items():
            if len(values) > 1:
                risks.append(f'Conflicting evidence for {key}: {sorted(values)}; no winner inferred')
        current = inspect_status(root, config['canonical_branch'], allow_missing_upstream=True)
        if (before.branch, before.head, before.staged, before.unstaged, before.untracked) != (
                current.branch, current.head, current.staged, current.unstaged, current.untracked):
            errors.append('Local branch/HEAD/worktree status changed during discovery; inspect concurrent activity')
        else:
            print('Local branch, HEAD and uncommitted path status preserved.')
        counts, _ = _git(root, 'rev-list', '--left-right', '--count',
                         before.head + '...' + canonical, '--') if canonical in new else ('unknown', 0)
        print(f'HEAD vs canonical ahead/behind: {counts}')
        print(f'New events: {count}; payloads are declarations, not proof. Review files_changed, '
              'interface_changes, known_issues and related_events against current work.')
        if not errors and not risks:
            cache.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache.with_suffix('.tmp')
            with temporary.open('x', encoding='utf-8') as stream:
                json.dump({'seen': seen, 'refs': new}, stream, sort_keys=True)
            temporary.replace(cache)
    except (GitError, OSError, ValueError) as exc:
        errors.append(str(exc))
    for risk in risks:
        print('RISK: ' + risk)
    for error in errors:
        print('ERROR: ' + error)
    if errors or risks:
        print('Discovery incomplete or requires review; receipts not advanced.')
        return 1
    print('Discovery completed; no merge/rebase/pull/checkout/commit/push performed.')
    return 0
