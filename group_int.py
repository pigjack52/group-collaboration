"""Resolve a published change and inspect a local integration target."""
import os
from pathlib import Path
import re

from group_events import EventError
from group_git import GitError, _git, inspect_status, repository_root
from group_pub import index_safety, status_config
from group_status import StatusError, read_members
from group_sync import event_entries, read_discovered_event


TRACKING = 'refs/remotes/origin/'
GIT = ('--no-replace-objects',)


def ref_snapshot(root, canonical, target):
    raw, _ = _git(root, 'for-each-ref', '--format=%(refname)%09%(objectname)%09%(symref)',
                  TRACKING, target)
    refs = {}
    for line in filter(None, raw.split('\n')):
        ref, oid, symbolic = line.split('\t')
        if ref in (target, TRACKING + canonical) or ref.startswith(TRACKING + 'people/'):
            refs[ref] = (oid, symbolic)
    return refs


def personal_owner(branch):
    parts = branch.split('/', 2)
    if len(parts) == 3 and parts[0] == 'people' and parts[2]:
        return parts[1]
    return None


def candidates(root, refs, group_id, change_id, report):
    records = {}
    for ref, (tip, symbolic) in sorted(refs.items()):
        if not ref.startswith(TRACKING):
            continue
        if symbolic:
            report['blockers'].append(f'Symbolic tracking ref cannot supply event evidence: {ref}')
            continue
        for path, mode, kind, blob in event_entries(root, tip):
            evidence = dict(ref=ref, tip_sha=tip, event_path=path, event_blob_sha=blob)
            try:
                event = read_discovered_event(root, path, mode, kind, blob, group_id)
            except (EventError, GitError) as exc:
                report['blockers'].append(f'Invalid event at {ref}@{tip}:{path}: {exc}')
                continue
            key = (event['event_id'].casefold(), blob)
            record = records.setdefault(key, dict(event=event, source_refs=[]))
            record['source_refs'].append(evidence)
    matches = [record for record in records.values()
               if record['event']['event_type'] == 'change_published'
               and record['event']['change_id'].casefold() == change_id.casefold()]
    ids = {record['event']['event_id'].casefold() for record in matches}
    related = [record for (identifier, _), record in records.items() if identifier in ids]
    if len(matches) > 1 or len(related) > 1:
        report['conflicting_candidates'] = related
        report['blockers'].append('Conflicting candidate events; operator must resolve the ambiguity.')
        report['status'] = 'candidate_conflict'
        return None
    if not matches:
        report['blockers'].append('No matching publication in the available origin tracking refs.')
        return None
    return matches[0]


def resolve_work(root, declared, width):
    if not re.fullmatch(r'[0-9a-fA-F]{4,' + str(width) + r'}', declared):
        raise EventError('Candidate commit must be a hexadecimal Git object ID.')
    objects, _ = _git(root, *GIT, 'rev-parse', '--disambiguate=' + declared.lower())
    matches = objects.splitlines()
    if len(matches) != 1:
        raise EventError('Candidate object is missing or its abbreviated SHA is ambiguous.')
    work = matches[0]
    kind, _ = _git(root, *GIT, 'cat-file', '-t', work)
    if kind != 'commit':
        raise EventError('Candidate object is not a Git commit.')
    return work


def ancestor(root, older, newer):
    _, code = _git(root, *GIT, 'merge-base', '--is-ancestor', older, newer, allowed=(0, 1))
    return code == 0


def inspect_candidate(root, record, refs, members, report):
    event, sources = record['event'], record['source_refs']
    report.update(event_id=event['event_id'], event_path=sources[0]['event_path'],
                  event_blob_sha=sources[0]['event_blob_sha'], source_refs=sources,
                  candidate_event=event)
    branch = event['branch']
    _git(root, 'check-ref-format', 'refs/heads/' + branch)
    if event['person_id'] not in members or personal_owner(branch) != event['person_id']:
        raise EventError('Publication author must be a registered member and own the declared source branch.')
    source = TRACKING + branch
    tip, symbolic = refs.get(source, (None, None))
    if not tip or symbolic:
        raise EventError('Declared source branch has no direct origin tracking ref.')
    kind, _ = _git(root, *GIT, 'cat-file', '-t', tip)
    if kind != 'commit':
        raise EventError('Declared source branch tip is not a Git commit.')
    work = resolve_work(root, event['commit'], len(tip))
    report['candidate_work_sha'] = work
    evidence = report['ancestry_evidence']
    evidence.update(source_ref=source, source_tip_sha=tip)
    evidence['work_is_ancestor_of_source'] = ancestor(root, work, tip)
    if not evidence['work_is_ancestor_of_source']:
        raise EventError('Candidate work commit is not reachable from the declared source branch.')
    if report['target_sha']:
        target = report['target_sha']
        evidence['work_is_ancestor_of_target'] = ancestor(root, work, target)
        bases, _ = _git(root, *GIT, 'merge-base', '--all', target, work, allowed=(0, 1))
        evidence['target_work_merge_bases'] = bases.splitlines()
        if not bases:
            raise GitError('Target and candidate have no available common ancestor.')
        report['incoming_commits'] = incoming_commits(root, target, work)


def incoming_commits(root, target, work):
    commits, _ = _git(root, *GIT, 'rev-list', target + '..' + work, '--')
    return sorted(commits.splitlines())


def check_git_environment(root):
    local_env, _ = _git(root, 'rev-parse', '--local-env-vars')
    if set(local_env.splitlines()).intersection(os.environ) or {
        'GIT_NAMESPACE', 'GIT_GRAFT_FILE', 'GIT_REPLACE_REF_BASE',
    }.intersection(os.environ):
        raise GitError('Repository-local Git environment overrides are unsupported.')


def preflight(directory, load_config, person_id, change_id, target_branch):
    report = dict(operation='int_preflight', status='insufficient_evidence',
                  change_id=change_id, person_id=person_id, target_branch=target_branch,
                  target_sha=None, target_kind=None, candidate_work_sha=None,
                  source_refs=[], event_id=None, event_path=None, event_blob_sha=None,
                  ancestry_evidence=dict(work_is_ancestor_of_source=None,
                                         work_is_ancestor_of_target=None),
                  blockers=[], review_required=[
                      'Review actual candidate history and diffs from an agreed contribution base.',
                      'Compare the declared files_changed, interfaces and assumptions with the target.',
                      'Plan build, tests, conflict checks and semantic verification for the combined work.',
                  ],
                  unverified=dict(remote_state='Local origin tracking refs only; may be stale.',
                                  contribution_scope='Declared files_changed is not verified by preflight.',
                                  semantic_integration='Not assessed; ancestry describes Git history only.',
                                  submodules='Nested submodule worktrees are not inspected.',
                                  validation='No test, build or merge was run.'))
    blockers = report['blockers']
    try:
        check_git_environment(directory)
        root = repository_root(directory)
        config = load_config(root / '.group/group.json')
        group_id, canonical = config['group_id'], config['canonical_branch']
        if not isinstance(group_id, str) or not group_id.strip() or config['protocol_version'] != '0.1':
            raise EventError('Invalid group_id or unsupported protocol_version.')
        members = {member['person_id'] for member in read_members(root)}
        if not change_id.strip():
            raise EventError('A nonblank change_id is required.')
        target = 'refs/heads/' + target_branch
        _git(root, 'check-ref-format', target)
        options, filters_disabled = status_config(root)
        before = inspect_status(root, canonical, allow_missing_upstream=True,
                                status_config=options, submodules='dirty')
        safety, unsafe = index_safety(root)
        report['worktree'] = dict(branch=before.branch, head=before.head, staged=before.staged,
                                  unstaged=before.unstaged, untracked=before.untracked, **safety)
        refs = ref_snapshot(root, canonical, target)
        target_sha, symbolic = refs.get(target, (None, None))
        if target_sha and not symbolic:
            kind, _ = _git(root, *GIT, 'cat-file', '-t', target_sha)
            if kind == 'commit':
                report['target_sha'] = target_sha
        report['target_ref'] = target
        report['target_upstream_ref'] = before.upstream
        tracking = refs.get(TRACKING + target_branch)
        report['target_tracking_sha'] = tracking[0] if tracking and not tracking[1] else None
        owner = personal_owner(target_branch)
        report['target_kind'] = 'canonical' if target_branch == canonical else 'personal' if owner else 'other'
        if person_id not in members:
            unsafe.append('Operator is not a registered member.')
        if target_branch != canonical and (not owner or owner != person_id):
            unsafe.append('Target must be the canonical branch or an operator-owned personal branch.')
        if not report['target_sha']:
            unsafe.append('Target is missing or is not a direct local branch at a commit.')
        if (before.branch, before.head) != (target_branch, report['target_sha']):
            unsafe.append('Target must be checked out in the inspected worktree to assess its readiness.')
        if not before.clean:
            unsafe.append('Worktree, index or untracked files are dirty.')
        if filters_disabled:
            unsafe.append('Configured external Git filters were disabled for inspection.')
        blockers.extend(unsafe)
        shallow, _ = _git(root, 'rev-parse', '--is-shallow-repository')
        grafts, _ = _git(root, 'rev-parse', '--git-path', 'info/grafts')
        if shallow == 'true' or 'GIT_GRAFT_FILE' in os.environ or (root / grafts).exists():
            raise GitError('Shallow or grafted history cannot establish INT ancestry.')
        record = candidates(root, refs, group_id, change_id, report)
        if record:
            try:
                inspect_candidate(root, record, refs, members, report)
            except (EventError, GitError) as exc:
                blockers.append(str(exc))
        after = inspect_status(root, canonical, allow_missing_upstream=True,
                               status_config=options, submodules='dirty')
        final_safety, _ = index_safety(root)
        if after != before or final_safety != safety or ref_snapshot(root, canonical, target) != refs:
            message = 'Git state changed during preflight; repeat after concurrent activity ends.'
            unsafe.append(message)
            blockers.append(message)
        if report['status'] != 'candidate_conflict':
            if unsafe:
                report['status'] = 'target_unsafe'
            elif not blockers:
                report['status'] = ('history_contains_candidate'
                                    if report['ancestry_evidence']['work_is_ancestor_of_target']
                                    else 'review_required')
    except (EventError, GitError, OSError, StatusError, ValueError) as exc:
        blockers.append(str(exc))
    return report


def all_refs(root):
    refs, _ = _git(root, 'for-each-ref', '--format=%(refname)%09%(objectname)%09%(symref)')
    return dict(line.split('\t', 1) for line in refs.splitlines())


def write_safety(root, work):
    options, filters_disabled = status_config(root)
    _, drivers = _git(root, 'config', '--get-regexp', r'^merge\..*\.driver$', allowed=(0, 1))
    if filters_disabled or drivers == 0:
        raise GitError('External filters or custom merge drivers are unsupported for INT writes.')
    tree, _ = _git(root, *GIT, 'ls-tree', '-r', '-z', work)
    if any(entry.startswith('160000 ') for entry in tree.split('\0')):
        raise GitError('Candidate contains submodules; INT write refused.')
    return options


def observe_target(root, canonical, options):
    direct, _ = _git(root, 'symbolic-ref', '--no-recurse', '--quiet', 'HEAD', allowed=(0, 1))
    state = inspect_status(root, canonical, allow_missing_upstream=True,
                           status_config=options, submodules='none')
    safety, blockers = index_safety(root)
    snapshot = dict(head=state.head, head_ref=direct, staged=state.staged,
                    unstaged=state.unstaged, untracked=state.untracked, **safety)
    if not state.clean:
        blockers.append('Worktree, index or untracked files are dirty.')
    if safety['submodule_worktrees_not_inspected']:
        blockers.append('Target index contains submodules.')
    return snapshot, blockers


def fast_forward(directory, load_config, args):
    report = dict(operation='int_ff_only', status='blocked', blockers=[], attempted=False,
                  validation='Build, tests and semantic review were not performed.',
                  remote_state='Local tracking refs may be stale; remote state was not verified.')
    try:
        check_git_environment(directory)
        root = repository_root(directory)
        git_dir, _ = _git(root, 'rev-parse', '--absolute-git-dir')
        common, _ = _git(root, 'rev-parse', '--path-format=absolute', '--git-common-dir')
        if Path(git_dir).resolve() != Path(common).resolve():
            raise GitError('Linked worktrees are unsupported for INT writes.')
        lock = Path(common) / 'groupctl-int.lock'
        try:
            lock.mkdir()
        except FileExistsError as exc:
            raise GitError('INT write lock exists; inspect it before retrying.') from exc
        try:
            apply_fast_forward(root, load_config, args, report)
        finally:
            lock.rmdir()
    except (EventError, GitError, OSError, StatusError, ValueError) as exc:
        if report['attempted']:
            report['status'] = 'write_unverified'
        report['blockers'].append(str(exc))
    return report


def apply_fast_forward(root, load_config, args, report):
    before_refs = all_refs(root)
    if any(ref.startswith('refs/replace/') for ref in before_refs):
        raise GitError('Replace objects are unsupported for INT writes.')
    checked = preflight(root, load_config, args.person_id, args.change_id, args.target_branch)
    report['preflight'] = checked
    if checked['blockers']:
        report['blockers'].extend(checked['blockers'])
        return
    if checked['worktree']['submodule_worktrees_not_inspected']:
        raise GitError('Target index contains submodules.')
    canonical = load_config(root / '.group/group.json')['canonical_branch']
    if (checked['target_kind'] != 'personal' or args.target_branch.casefold() in
            {canonical.casefold(), 'main'} or personal_owner(args.target_branch) != args.person_id):
        raise GitError('INT writes require the operator-owned personal branch; canonical and main are refused.')
    target, work = checked['target_ref'], checked['candidate_work_sha']
    head = checked['target_sha']
    evidence = checked['ancestry_evidence']
    event_path = f'.group/events/{args.expected_event_id}.json'
    expected = dict(change_id=args.change_id, event_id=args.expected_event_id, event_path=event_path,
                    event_blob_sha=args.expected_event_blob.lower(), candidate_work_sha=args.work_sha.lower(),
                    source_ref=args.expected_source_ref, source_tip_sha=args.expected_source_tip.lower())
    observed = dict(checked, **evidence, change_id=checked['candidate_event']['change_id'])
    mismatches = [field for field, value in expected.items() if observed[field] != value]
    if mismatches:
        raise GitError('Reviewed evidence changed: ' + ', '.join(mismatches))
    publication = dict(ref=expected['source_ref'], tip_sha=expected['source_tip_sha'],
                       event_path=event_path, event_blob_sha=expected['event_blob_sha'])
    if publication not in checked['source_refs']:
        raise GitError('Reviewed event must be present at the declared source tracking tip.')
    if not checked['candidate_event']['proposed_for_integration'] and not args.confirm_unproposed:
        raise GitError('Publication is not proposed for integration; --confirm-unproposed is required.')
    paths, _ = _git(root, *GIT, 'diff-tree', '--root', '-m', '-r', '--no-commit-id',
                    '--no-renames', '--name-only', '-z', work, '--')
    paths = list(filter(None, paths.split('\0')))
    if event_path in paths or (paths and all(path.startswith('.group/events/') for path in paths)):
        raise GitError('Candidate appears to be an event commit; expected a work commit.')
    options = write_safety(root, work)
    old = args.expected_head.lower()
    if len(old) != len(head) or not re.fullmatch(r'[0-9a-f]+', old):
        raise GitError('Expected HEAD must be a full hexadecimal object ID.')
    tracking = checked['target_tracking_sha']
    if tracking and not ancestor(root, tracking, head):
        raise GitError('Target is behind or diverges from its tracking ref; synchronize first.')
    upstream = checked['target_upstream_ref']
    if upstream and upstream != TRACKING + args.target_branch:
        oid, _, symbolic = before_refs.get(upstream, '').partition('\t')
        if not oid or symbolic or not ancestor(root, oid, head):
            raise GitError('Configured upstream is symbolic, ahead or diverged; synchronize first.')
    if head != old and not (head == work and ancestor(root, old, work)):
        raise GitError('Expected target HEAD is stale.')
    incoming = incoming_commits(root, old, work)
    report['incoming_commits'] = incoming
    reviewed = [] if args.expected_incoming == 'none' else args.expected_incoming.lower().split(',')
    if sorted(reviewed) != incoming:
        raise GitError('Incoming commit range differs from the reviewed T..A list.')
    before, blockers = observe_target(root, canonical, options)
    report['before'] = before
    if blockers or (before['head_ref'], before['head']) != (target, head) or all_refs(root) != before_refs:
        raise GitError('Git state is unsafe or changed before INT: ' + ' '.join(blockers))
    if evidence['work_is_ancestor_of_target']:
        report['status'] = 'already_in_history'
        return
    if not ancestor(root, head, work):
        raise GitError('Target and candidate diverge; non-fast-forward integration is required.')

    report['attempted'] = True
    try:
        output, code = _git(root, *GIT, *options, '-c', 'core.hooksPath=' + os.devnull,
                            '-c', 'rerere.enabled=false', '-c', 'maintenance.auto=false',
                            '-c', 'gc.auto=0', '-c', 'submodule.recurse=false',
                            '-c', f'branch.{args.target_branch}.mergeOptions=',
                            'merge', '--ff-only', '--no-commit', '--no-autostash',
                            '--no-overwrite-ignore', '--no-edit', '--', work)
        report.update(git_output=output, git_exit=code)
    except GitError as exc:
        report['blockers'].append(str(exc))
    finally:
        after, blockers = observe_target(root, canonical, options)
        report['after'] = after
        expected_refs = dict(before_refs, **{target: work + '\t'})
        actual_refs = all_refs(root)
        report['changed_refs'] = sorted(ref for ref in before_refs.keys() | actual_refs.keys()
                                        if before_refs.get(ref) != actual_refs.get(ref))
    if blockers or (after['head_ref'], after['head']) != (target, work) or actual_refs != expected_refs:
        raise GitError('Fast-forward postconditions could not be verified; inspect the preserved Git state.')
    report['status'] = 'write_unverified' if report['blockers'] else 'local_fast_forward_verified'
