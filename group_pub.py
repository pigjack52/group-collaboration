"""Local Git and repository observations for PUB. Publication requires separate review."""
from group_git import _git, inspect_status
from group_status import StatusError, read_members
from group_test import MARKERS


def status_config(root):
    """Suppress hooks and external filters while inspecting the working tree."""
    keys, _ = _git(root, 'config', '-z', '--name-only', '--get-regexp',
                   r'^filter\..*\.(clean|smudge|process|required)$', allowed=(0, 1))
    drivers = sorted({key.rsplit('.', 1)[0] for key in keys.split('\0') if key})
    options = ['-c', 'core.fsmonitor=false']
    for driver in drivers:
        for key, value in (('clean', ''), ('smudge', ''), ('process', ''), ('required', 'false')):
            options.extend(('-c', f'{driver}.{key}={value}'))
    return options, bool(drivers)


def committed_range(root, state):
    ref = state.upstream or state.canonical_ref
    comparison = dict(ref=ref, source='local_upstream' if state.upstream else 'local_canonical',
                      ref_commit=None, merge_bases=[], diff_range=None, history_range=None,
                      net_changed_paths=None)
    if ref is None:
        comparison['source'] = None
        return comparison
    tip, _ = _git(root, 'rev-parse', '--verify', ref + '^{commit}')
    bases, _ = _git(root, 'merge-base', '--all', tip, state.head, allowed=(0, 1))
    comparison.update(ref_commit=tip, merge_bases=bases.splitlines(),
                      history_range=f'{tip}..{state.head}')
    if len(comparison['merge_bases']) == 1:
        base = comparison['merge_bases'][0]
        paths, _ = _git(root, 'diff', '--no-ext-diff', '--no-textconv', '--no-renames',
                        '--ignore-submodules=none', '--name-only', '-z', base, state.head, '--')
        comparison.update(diff_range=f'{base}..{state.head}',
                          net_changed_paths=list(filter(None, paths.split('\0'))))
    return comparison


def preflight(root, config, person_id):
    blockers = []
    group_id = config['group_id']
    canonical = config['canonical_branch']
    if not isinstance(group_id, str) or not group_id.strip() or config['protocol_version'] != '0.1':
        blockers.append('Invalid group_id or unsupported protocol_version in .group/group.json.')

    descriptor = None
    try:
        member = next((item for item in read_members(root) if item['person_id'] == person_id), None)
    except StatusError as exc:
        blockers.append(str(exc))
    else:
        if member is None:
            blockers.append('person_id has no registered member descriptor.')
        else:
            descriptor = f'.group/members/{member["person_id"]}.json'

    options, filters_disabled = status_config(root)
    state = inspect_status(root, canonical, allow_missing_upstream=True,
                           status_config=options, submodules='dirty')
    branch = state.branch
    parts = branch.split('/', 2) if branch else []
    personal = len(parts) == 3 and parts[0] == 'people' and bool(parts[2])
    owner_matches = personal and parts[1] == person_id
    if branch is None:
        blockers.append('Detached HEAD or HEAD outside refs/heads/.')
    elif branch == canonical:
        blockers.append('Current branch is the canonical branch.')
    elif not personal:
        blockers.append('Expected a personal branch named people/<person_id>/<topic>.')
    elif not owner_matches:
        blockers.append('Personal branch owner differs from person_id.')

    operations = []
    for marker in MARKERS:
        location, _ = _git(root, 'rev-parse', '--git-path', marker)
        if (root / location).exists():
            operations.append(marker)
    if operations:
        blockers.append('Unfinished Git operation detected.')
    index_path, _ = _git(root, 'rev-parse', '--git-path', 'index')
    index_lock_present = (root / (index_path + '.lock')).exists()
    if index_lock_present:
        blockers.append('Git index lock present.')
    entries, _ = _git(root, 'ls-files', '--stage', '-z')
    conflicts = set()
    submodules = set()
    for entry in filter(None, entries.split('\0')):
        metadata, path = entry.split('\t', 1)
        mode, _, stage = metadata.split()
        if stage != '0':
            conflicts.add(path)
        if mode == '160000':
            submodules.add(path)
    if conflicts:
        blockers.append('Unresolved Git conflicts detected.')
    flags, _ = _git(root, 'ls-files', '-v', '-z')
    hidden = [{'path': entry[2:], 'flag': entry[0]}
              for entry in filter(None, flags.split('\0'))
              if entry[0].islower() or entry[0] == 'S']
    if hidden:
        blockers.append('Index flags may hide working-tree changes.')

    upstream = state.upstream
    if branch and upstream is None:
        upstream, _ = _git(root, 'for-each-ref', '--format=%(upstream)', 'refs/heads/' + branch)
        upstream = upstream or None
    return {
        'operation': 'pub_preflight',
        'result': 'blocked' if blockers else 'review_required',
        'facts': {
            'group_id': group_id, 'person_id': person_id, 'member_descriptor': descriptor,
            'branch': branch, 'personal_branch': personal, 'branch_owner_matches': owner_matches,
            'canonical_branch': canonical, 'on_canonical': branch == canonical, 'head': state.head,
            'staged': state.staged, 'unstaged': state.unstaged, 'untracked': state.untracked,
            'unmerged_paths': sorted(conflicts), 'git_operations': operations,
            'hidden_index_flags': hidden, 'index_lock_present': index_lock_present,
            'upstream': upstream, 'upstream_ref_available': state.upstream is not None,
            'ahead': state.ahead, 'behind': state.behind,
            'comparison': committed_range(root, state),
            'external_filters_disabled': filters_disabled, 'submodule_worktrees_not_inspected': sorted(submodules),
        },
        'blockers': blockers,
        'review_required': [
            'Select the contribution. Review staged (HEAD to index), unstaged (index to worktree), and untracked content.',
            'Review the committed comparison and commit history, including paths changed and later reverted. Confirm the actual publication range independently.',
            'Review generated files, interface changes, and assumptions. Determine and run applicable checks on the selected content.',
        ],
        'unverified': {
            'remote_state': 'Local refs only; current remote refs and push destination unverified.',
            'permissions': 'Operator authentication and remote write permission unverified.',
            'secrets': 'No secret scan performed.',
            'tests': 'No tests run or evidence validated.',
            'publication': 'Mechanical preflight only; publication readiness and completion unverified.',
        },
    }
