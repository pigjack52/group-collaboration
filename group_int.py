"""Resolve a published change and inspect a local integration target."""
import os
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
        local_env, _ = _git(directory, 'rev-parse', '--local-env-vars')
        if set(local_env.splitlines()).intersection(os.environ) or 'GIT_NAMESPACE' in os.environ:
            raise GitError('Repository-local Git environment overrides are unsupported.')
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
