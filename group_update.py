"""Conservative canonical update; never creates commits or claims semantic review."""
import os
from pathlib import Path
import subprocess
import sys

from group_git import GitError, _git, inspect_status, format_status


def update(root, config):
    root = Path(root)
    before = None
    try:
        canonical = config['canonical_branch']
        before = inspect_status(root, canonical, allow_missing_upstream=True)
        print('SYNC safe update stage only; semantic review: NOT PERFORMED.')
        print('Before fetch:\n' + format_status(before), flush=True)
        if not before.clean:
            raise GitError('Dirty worktree/index/untracked files; update refused')
        if (not before.branch or not before.branch.startswith('people/')
                or len(before.branch.split('/')) < 3 or before.branch == canonical):
            raise GitError('Requires a personal people/<person_id>/<topic> branch; detached/canonical/other branch refused')
        for marker in ('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'rebase-merge', 'rebase-apply', 'sequencer'):
            location, _ = _git(root, 'rev-parse', '--git-path', marker)
            if (root / location).exists():
                raise GitError('Unfinished Git operation: ' + marker)
        if not before.origin_exists:
            raise GitError('No origin remote')
        ref = 'refs/remotes/origin/' + canonical
        print(f'Cached canonical before fetch: {before.canonical_commit or "unknown"}')
        _git(root, '-c', 'core.hooksPath=' + os.devnull, 'fetch', '--no-tags',
             '--no-recurse-submodules', '--no-write-fetch-head', '--refmap=',
             'origin', '+refs/heads/' + canonical + ':' + ref)
        target, _ = _git(root, 'rev-parse', '--verify', ref + '^{commit}')
        print(f'Update source: {ref}@{target}; target branch: {before.branch}@{before.head}', flush=True)
        current = inspect_status(root, canonical, allow_missing_upstream=True)
        if current.branch != before.branch or current.head != before.head or not current.clean:
            raise GitError('Local state changed during fetch; update refused')
        _, incorporated = _git(root, 'merge-base', '--is-ancestor', target, before.head, allowed=(0, 1))
        if incorporated == 0:
            print('No update needed; canonical commit already incorporated. Tests: not rerun (no update).')
            return 0
        base, _ = _git(root, 'merge-base', before.head, target)
        files, _ = _git(root, 'diff', '--name-status', base, target, '--')
        summary, _ = _git(root, 'log', '--format=%H %s', before.head + '..' + target, '--')
        diff, _ = _git(root, 'diff', '--no-ext-diff', '--no-textconv', base, target, '--')
        print('AI review input: incoming commits\n' + summary + '\nAffected files:\n' + files + '\nIncoming diff:\n' + diff)
        print(f'Compare personal changes: git diff {base} {before.head} --')
        _, diverged = _git(root, 'merge-base', '--is-ancestor', before.head, target, allowed=(0, 1))
        print('Strategy: ' + ('merge --no-commit --no-ff; requires human commit after review' if diverged else 'fast-forward only'), flush=True)
        output, code = _git(root, '-c', 'core.hooksPath=' + os.devnull,
                            '-c', 'merge.autoStash=false', '-c', 'commit.gpgSign=false',
                            'merge', *(('--no-commit', '--no-ff') if diverged else ('--ff-only',)),
                            '--no-edit', target, allowed=(0, 1))
        print(output)
        if code:
            conflicts, _ = _git(root, 'diff', '--name-only', '--diff-filter=U')
            print('Merge failed/conflicted; original HEAD retained. Conflicts:\n' + conflicts)
            print('Recovery: git merge --abort (before making further edits); or resolve deliberately, test and review. No automatic resolution.')
            return 1
        after, _ = _git(root, 'rev-parse', 'HEAD')
        _, ancestry = _git(root, 'merge-base', '--is-ancestor', target, after, allowed=(0, 1))
        print(f'Before commit: {before.head}; after commit: {after}; canonical incorporated: {ancestry == 0}')
        combined, _ = _git(root, 'diff', '--no-ext-diff', '--no-textconv', before.head, '--')
        print('Resulting worktree diff for AI review:\n' + combined)
        checks, check_code = _git(root, 'diff', '--check', before.head, '--', allowed=(0, 2))
        print('Whitespace/conflict marker check: ' + ('fail' if check_code else 'pass') + '\n' + checks)
        # This project's supported quick validation; never silently run zero tests.
        tests = sorted((root / 'tests').glob('test*.py'))
        test_result = 'not_available'
        if tests:
            command = [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-v']
            print('Quick validation: ' + ' '.join(command), flush=True)
            result = subprocess.run(command, cwd=root, capture_output=True, timeout=120,
                                    env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
            print((result.stdout + result.stderr).decode('utf-8', errors='replace'))
            test_result = 'pass' if result.returncode == 0 else 'fail'
            if b'Ran 0 tests' in result.stderr:
                test_result = 'not_available'
        print(f'Tests: {test_result}; semantic review: NOT PERFORMED. AI/human must inspect interfaces, assumptions, test sufficiency and combined behavior.')
        if ancestry or check_code or test_result != 'pass':
            print('Update incomplete: pending merge commit, failed checks, or unavailable validation. No commit/rollback performed. Inspect git status; pending merge can be cancelled with git merge --abort.')
            return 1
        print('Git update and quick validation passed; semantic compatibility remains unverified.')
        return 0
    except (GitError, OSError, subprocess.TimeoutExpired) as exc:
        print('ERROR: ' + str(exc))
        print('Update not completed; inspect git status and HEAD. No automatic rollback or commit.')
        return 1
    finally:
        if before:
            try:
                head, _ = _git(root, 'rev-parse', 'HEAD')
            except GitError:
                head = 'unknown (final Git inspection failed)'
            print(f'Final record: branch before={before.branch}; HEAD before={before.head}; HEAD after={head}')
