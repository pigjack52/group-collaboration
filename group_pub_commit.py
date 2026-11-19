"""Create one explicitly scoped local PUB commit."""
import json
import os
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory

from group_git import GitError, _git, inspect_status, repository_root
from group_pub import preflight, status_config


DIFF = ('diff', '--no-ext-diff', '--no-textconv', '--no-renames',
        '--ignore-submodules=none', '--name-only', '-z')
HOOKS = ('pre-commit', 'prepare-commit-msg', 'commit-msg', 'post-commit',
         'reference-transaction')


def paths(output):
    return set(filter(None, output.split('\0')))


def check_state(root, load_config, args, *, own_lock=False):
    config = load_config(root / '.group/group.json')
    report = preflight(root, config, args.person_id)
    blockers = report['blockers']
    if own_lock:
        blockers = [item for item in blockers if item != 'Git index lock present.']
    facts = report['facts']
    if facts['head'] != args.expected_head or facts['branch'] != args.expected_branch:
        blockers.append('HEAD or branch differs from the explicitly reviewed target.')
    if blockers:
        raise GitError(' '.join(blockers))
    for name in ('.group/group.json', facts['member_descriptor']):
        committed, _ = _git(root, '--no-replace-objects', 'cat-file', 'blob',
                            f'{args.expected_head}:{name}')
        current = config if name == '.group/group.json' else json.loads(
            (root / name).read_text(encoding='utf-8-sig'))
        if current != json.loads(committed.removeprefix('\ufeff')):
            raise GitError(f'Group or member metadata differs from reviewed HEAD: {name}')
    return facts


def check_write_settings(root, facts):
    if facts['external_filters_disabled']:
        raise GitError('Configured external Git filters require a separate commit workflow.')
    hooks, _ = _git(root, 'rev-parse', '--git-path', 'hooks')
    active = [name for name in HOOKS if os.access(root / hooks / name, os.X_OK)
              and (root / hooks / name).is_file()]
    if active:
        raise GitError('Active Git hooks require a separate commit workflow: ' + ', '.join(active))
    shared_index, _ = _git(root, 'rev-parse', '--shared-index-path')
    sparse, _ = _git(root, 'config', '--bool', '--get', 'core.sparseCheckout', allowed=(0, 1))
    if shared_index or sparse == 'true':
        raise GitError('Split indexes and sparse checkouts require a separate commit workflow.')


def selection(root, head, selected):
    staged = paths(_git(root, *DIFF, '--cached', '--ita-visible-in-index', head, '--')[0])
    if staged - selected:
        raise GitError('Unselected staged paths: ' + repr(sorted(staged - selected)))
    unstaged = paths(_git(root, '-c', 'core.fsmonitor=false', *DIFF, '--')[0])
    untracked = paths(_git(root, 'ls-files', '--others', '--exclude-standard', '-z')[0])
    working = unstaged | untracked
    if selected - (staged | working):
        raise GitError('Selected paths must be exact changed or untracked files: '
                       + repr(sorted(selected - (staged | working))))
    if staged & working:
        raise GitError('Selected paths have both staged and working-tree changes: '
                       + repr(sorted(staged & working)))
    return working & selected


def commit_details(root, commit):
    raw, _ = _git(root, '--no-replace-objects', 'cat-file', 'commit', commit)
    headers = raw.split('\n\n', 1)[0].splitlines()
    tree = next(line[5:] for line in headers if line.startswith('tree '))
    parents = [line[7:] for line in headers if line.startswith('parent ')]
    return tree, parents


def write_commit(root, load_config, args, report):
    head, branch = args.expected_head, args.expected_branch
    selected = set(args.file)
    if len(selected) != len(args.file):
        raise GitError('Specify each file once.')
    facts = check_state(root, load_config, args)
    check_write_settings(root, facts)
    working = selection(root, head, selected)
    if selected.intersection(facts['submodule_worktrees_not_inspected']):
        raise GitError('Submodule changes require a separate commit workflow.')
    location, _ = _git(root, 'rev-parse', '--git-path', 'index')
    index = root / location
    lock = index.with_name(index.name + '.lock')
    with TemporaryDirectory(prefix='groupctl-pub-') as temporary:
        private_index = Path(temporary) / 'index'
        env = {'GIT_INDEX_FILE': str(private_index)}
        hooks = Path(temporary) / 'empty-hooks'
        hooks.mkdir()
        options = ('--no-replace-objects', '-c', 'core.fsmonitor=false', '-c', 'core.splitIndex=false',
                   '-c', f'core.hooksPath={hooks.as_posix()}')
        # Hold Git's normal index lock until the ref transaction and index installation finish.
        with lock.open('xb') as locked_index:
            try:
                check_write_settings(root, check_state(root, load_config, args, own_lock=True))
                if selection(root, head, selected) != working:
                    raise GitError('Staging or working-tree selection changed during validation.')
                if index.exists():
                    # Git uses index mtime to detect racy same-size worktree edits.
                    shutil.copy2(index, private_index)
                else:
                    _git(root, *options, 'read-tree', head, env_overrides=env)
                report['phase'] = 'isolated_index'
                if working:
                    _git(root, *options, '--literal-pathspecs', 'add', '--', *sorted(working),
                         env_overrides=env)
                tree, _ = _git(root, *options, 'write-tree', env_overrides=env)
                actual = paths(_git(root, *options, *DIFF, head, tree, '--')[0])
                if actual != selected:
                    raise GitError('Candidate tree differs from the exact selected file set: '
                                   + repr(sorted(actual)))
                entries, _ = _git(root, *options, 'ls-tree', '-r', '-z', tree)
                if any(entry.startswith('160000 ') and entry.split('\t', 1)[1] in selected
                       for entry in paths(entries)):
                    raise GitError('Embedded repositories and submodule changes require separate review.')
                dirty, _ = _git(root, *options, *DIFF, '--', env_overrides=env)
                if paths(dirty) & selected:
                    raise GitError('Selected working-tree content changed after staging.')
                with private_index.open('rb') as contents:
                    shutil.copyfileobj(contents, locked_index)
                locked_index.flush()
                os.fsync(locked_index.fileno())
                stat = private_index.stat()
                os.utime(lock, ns=(stat.st_atime_ns, stat.st_mtime_ns))
                report['phase'] = 'creating_object'
                commit, _ = _git(root, *options, 'commit-tree', tree, '-p', head, '-F', '-',
                                 input_bytes=args.message.encode('utf-8'))
                report.update(phase='commit_object', candidate_commit=commit)
                if commit_details(root, commit) != (tree, [head]):
                    raise GitError('Candidate commit tree or parent could not be verified.')
                check_write_settings(root, check_state(root, load_config, args, own_lock=True))
                dirty, _ = _git(root, *options, *DIFF, '--', env_overrides=env)
                if paths(dirty) & selected:
                    raise GitError('Selected working-tree content changed before the ref update.')
                ref = 'refs/heads/' + branch
                report['phase'] = 'updating_ref'
                _git(root, *options, 'update-ref', '--no-deref', '-m', 'groupctl PUB work commit',
                     ref, commit, head)
                report.update(phase='ref_updated', commit=commit, parents=[head], files=sorted(actual))
                current_ref, _ = _git(root, 'symbolic-ref', '--quiet', 'HEAD')
                current_head, _ = _git(root, 'rev-parse', '--verify', 'HEAD^{commit}')
                if (current_ref, current_head) != (ref, commit):
                    raise GitError('HEAD or branch changed around the ref update; index installation skipped.')
                locked_index.close()
                os.replace(lock, index)
                report['phase'] = 'index_installed'
            finally:
                locked_index.close()
                if report['phase'] != 'index_installed':
                    lock.unlink(missing_ok=True)


def work_commit(directory, load_config, args):
    report = dict(operation='pub_work_commit', result='refused', phase='validation',
                  publication='not_performed', expected_head=args.expected_head,
                  expected_branch=args.expected_branch)
    root = None
    try:
        if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', args.expected_head):
            raise GitError('Expected HEAD must be a full lowercase Git object ID.')
        local_env, _ = _git(directory, 'rev-parse', '--local-env-vars')
        if set(local_env.splitlines()).intersection(os.environ) or 'GIT_NAMESPACE' in os.environ:
            raise GitError('Repository-local Git environment overrides are unsupported.')
        root = repository_root(directory)
        if args.use_existing:
            facts = check_state(root, load_config, args)
            head = facts['head']
            _, parents = commit_details(root, head)
            if len(parents) != 1:
                raise GitError('Existing work must be a single-parent commit.')
            changed = paths(_git(root, '--no-replace-objects', *DIFF, parents[0], head, '--')[0])
            # Preflight may prefer upstream; use canonical independently to reject already accepted work.
            state = inspect_status(root, facts['canonical_branch'], allow_missing_upstream=True,
                                   status_config=status_config(root)[0], submodules='dirty')
            if not changed or not state.canonical_commit:
                raise GitError('Existing work requires a nonempty commit and a known local canonical ref.')
            _, incorporated = _git(root, '--no-replace-objects', 'merge-base', '--is-ancestor',
                                   head, state.canonical_commit, allowed=(0, 1))
            if incorporated == 0:
                raise GitError('The selected HEAD is already incorporated in the local canonical ref.')
            report.update(commit=head, parents=parents, files=sorted(changed),
                          reason='Explicitly selected the existing personal HEAD; no new commit needed.')
        else:
            write_commit(root, load_config, args, report)
        after = observe(root, load_config, args)
        report['git_status'] = after['facts']
        if args.work_commit:
            check_write_settings(root, after['facts'])
            staged = paths(_git(root, *DIFF, '--cached', '--ita-visible-in-index',
                                report['commit'], '--')[0])
            unstaged = paths(_git(root, '-c', 'core.fsmonitor=false', *DIFF, '--')[0])
            if staged or set(report['files']).intersection(unstaged | set(after['facts']['untracked'])):
                raise GitError('Final index or selected worktree state could not be confirmed.')
        if (after['blockers'] or after['facts']['head'] != report['commit']
                or after['facts']['branch'] != args.expected_branch):
            raise GitError('Final branch, HEAD, index or worktree state could not be confirmed.')
        ref, _ = _git(root, 'symbolic-ref', '--quiet', 'HEAD')
        head, _ = _git(root, 'rev-parse', '--verify', 'HEAD^{commit}')
        if (ref, head) != ('refs/heads/' + args.expected_branch, report['commit']):
            raise GitError('HEAD or branch changed during final verification.')
        report.update(result='existing' if args.use_existing else 'created', phase='verified')
        if args.work_commit:
            report['publication'] = 'unpublished'
    except (GitError, OSError, ValueError) as exc:
        if report['phase'] != 'validation':
            report['result'] = 'partial'
        report['error'] = str(exc)
        if root is not None:
            try:
                report['git_status'] = observe(root, load_config, args)['facts']
            except (GitError, OSError, ValueError) as status_error:
                report['status_error'] = str(status_error)
    return report


def observe(root, load_config, args):
    return preflight(root, load_config(root / '.group/group.json'), args.person_id)
