"""INT merge commit, post-merge verification, and integration_result publication."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory

from group_event_publish import (
    collect_known_events, commit_event_file, new_event_id, push_and_verify_event, utc_now,
)
from group_events import EventError, validate_event
from group_git import GitError, _git, inspect_status, repository_root
from group_int import (
    GIT, all_refs, ancestor, assess_pending_merge, candidates, check_git_environment,
    git_path, inspect_merge_snapshot, observe_target, personal_owner, ref_snapshot,
    resolve_work,
)
from group_pub_commit import HOOKS, commit_details
from group_pub_push import OBJECT_ID
from group_status import StatusError, read_members
from group_test import COUNT_FIELDS, TIMEOUT, classify_result, unique_fields


MERGE_CLEANUP_FILES = ('MERGE_HEAD', 'MERGE_MSG', 'MERGE_MODE', 'AUTO_MERGE')
VERIFICATION_VALUES = {'pass', 'fail', 'partial', 'not_available', 'not_applicable', 'needs_human'}


def _check_commit_merge_policies(root):
    hooks_dir, _ = _git(root, 'rev-parse', '--git-path', 'hooks')
    active = [name for name in HOOKS if (root / hooks_dir / name).is_file()
              and os.access(root / hooks_dir / name, os.X_OK)]
    if active:
        raise GitError('Active Git hooks require a separate commit workflow: ' + ', '.join(active))
    shared_index, _ = _git(root, 'rev-parse', '--shared-index-path')
    sparse, _ = _git(root, 'config', '--bool', '--get', 'core.sparseCheckout', allowed=(0, 1))
    if shared_index or sparse == 'true':
        raise GitError('Split indexes and sparse checkouts are unsupported for INT commit-merge.')
    gpg, _ = _git(root, 'config', '--bool', '--get', 'commit.gpgSign', allowed=(0, 1))
    if gpg == 'true':
        raise GitError('Configured commit.gpgSign=true requires a separate signed commit workflow.')


def commit_merge(directory, load_config, args):
    """Safely commit a reviewed pending merge with staged-tree binding and target-branch CAS."""
    report = dict(
        operation='int_commit_merge', status='blocked', attempted=False,
        change_id=args.change_id, person_id=args.person_id, target_branch=args.target_branch,
        blockers=[],
        validation='Build, tests and semantic review were not performed.',
        transaction_note=(
            'Git refs.c forbids combining symref-verify HEAD and target branch update in one '
            'ref transaction; guarded by groupctl-int.lock, index.lock, pre/post HEAD symref '
            'verification, and single-ref target branch CAS.'
        ),
    )
    try:
        if not args.message or not args.message.strip():
            raise GitError('A nonblank --message is required for --commit-merge.')
        if not args.expected_tree or not OBJECT_ID.fullmatch(args.expected_tree.lower()):
            raise GitError('A full lowercase --expected-tree OID is required.')
        check_git_environment(directory)
        root = repository_root(directory).resolve()
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
            _execute_commit_merge(root, load_config, args, report)
        finally:
            lock.rmdir()
    except (EventError, GitError, KeyError, OSError, StatusError, ValueError) as exc:
        if report['attempted']:
            report['status'] = 'write_unverified'
        report['blockers'].append(str(exc))
    return report


def _execute_commit_merge(root, load_config, args, report):
    _check_commit_merge_policies(root)
    facts, snapshot = inspect_merge_snapshot(root)
    # Our own INT lock is held; exclude it from the pending-merge assessment.
    facts_for_check = dict(facts, int_lock_present=False)
    inspect_report = dict(
        status='blocked', evidence_status='unverified', blockers=[],
        change_id=args.change_id, person_id=args.person_id, target_branch=args.target_branch,
    )
    assess_pending_merge(root, load_config, args, inspect_report, facts_for_check, snapshot)
    report['inspection'] = inspect_report
    if inspect_report['status'] != 'awaiting_human_confirmation' or inspect_report['blockers']:
        report['blockers'].extend(inspect_report['blockers'] or ['Pending merge is not ready to commit.'])
        return
    expected_tree = args.expected_tree.lower()
    if inspect_report.get('staged_tree_oid') != expected_tree:
        raise GitError(
            f'Staged tree OID {inspect_report.get("staged_tree_oid")} differs from '
            f'reviewed --expected-tree {expected_tree}.'
        )

    target = 'refs/heads/' + args.target_branch
    head = args.expected_head.lower()
    work = args.work_sha.lower()
    before_refs = snapshot['refs']
    canonical = load_config(root / '.group/group.json')['canonical_branch']

    index_file = git_path(root, 'index')
    index_lock = index_file.with_name(index_file.name + '.lock')
    with TemporaryDirectory(prefix='groupctl-int-hooks-') as temp_hooks:
        git_opts = (*GIT, '-c', 'core.fsmonitor=false', '-c', 'core.splitIndex=false',
                    '-c', f'core.hooksPath={Path(temp_hooks).as_posix()}')
        with index_lock.open('xb') as locked:
            try:
                cur_ref, _ = _git(root, 'symbolic-ref', '--no-recurse', '--quiet', 'HEAD')
                cur_head, _ = _git(root, *GIT, 'rev-parse', '--verify', 'HEAD^{commit}')
                if (cur_ref, cur_head) != (target, head):
                    raise GitError('HEAD or branch changed before committing merge.')
                marker = git_path(root, 'MERGE_HEAD').read_text(encoding='ascii').strip()
                if marker != work:
                    raise GitError('MERGE_HEAD changed before committing merge.')
                private_index = Path(temp_hooks) / 'index'
                import shutil
                shutil.copy2(index_file, private_index)
                tree, _ = _git(root, *git_opts, 'write-tree',
                               env_overrides={'GIT_INDEX_FILE': str(private_index)})
                if tree != expected_tree:
                    raise GitError('Live index tree OID differs from reviewed --expected-tree.')
                report['attempted'] = True
                merge_commit, _ = _git(
                    root, *git_opts, 'commit-tree', tree, '-p', head, '-p', work, '-F', '-',
                    input_bytes=(args.message.rstrip('\n') + '\n').encode('utf-8'),
                )
                report['candidate_commit'] = merge_commit
                if commit_details(root, merge_commit) != (tree, [head, work]):
                    raise GitError('Created merge commit tree or parents [T, A] could not be verified.')
                pre_ref, _ = _git(root, 'symbolic-ref', '--no-recurse', '--quiet', 'HEAD')
                if pre_ref != target:
                    raise GitError('HEAD symref changed immediately before ref CAS update.')
                _git(root, *git_opts, 'update-ref', '--no-deref', '-m',
                     f'groupctl INT commit-merge {args.change_id}', target, merge_commit, head)
                post_ref, _ = _git(root, 'symbolic-ref', '--no-recurse', '--quiet', 'HEAD')
                post_head, _ = _git(root, *GIT, 'rev-parse', '--verify', 'HEAD^{commit}')
                if (post_ref, post_head) != (target, merge_commit):
                    raise GitError('HEAD symref or tip mismatch immediately after ref CAS update.')
                for name in MERGE_CLEANUP_FILES:
                    git_path(root, name).unlink(missing_ok=True)
            finally:
                locked.close()
                index_lock.unlink(missing_ok=True)

    actual_refs = all_refs(root)
    expected_refs = dict(before_refs, **{target: merge_commit + '\t'})
    after, blockers = observe_target(root, canonical, snapshot['status_options'])
    report['after'] = after
    if (blockers or actual_refs != expected_refs
            or (after['head_ref'], after['head']) != (target, merge_commit)
            or not ancestor(root, head, merge_commit)
            or not ancestor(root, work, merge_commit)):
        raise GitError('Post-commit merge verification failed; inspect preserved Git state.')
    report.update(
        status='local_merge_committed',
        merge_commit=merge_commit,
        parents=[head, work],
        tree=tree,
    )


def _find_candidate_for_verify(root, config, change_id):
    known = collect_known_events(root, config['group_id'])
    matches = [e for e in known.values()
               if e['event_type'] == 'change_published'
               and e['change_id'].casefold() == change_id.casefold()]
    if not matches:
        raise EventError(f'No change_published event found for {change_id}.')
    if len(matches) > 1:
        raise EventError(f'Multiple conflicting change_published events found for {change_id}.')
    event = matches[0]
    head_tip, _ = _git(root, 'rev-parse', '--verify', 'HEAD^{commit}')
    work_sha = resolve_work(root, event['commit'], len(head_tip))
    return event, work_sha


def run_isolated_unittest(root, commit_sha):
    """Run fixed unittest runner at an exact commit in a disposable detached worktree."""
    import group_test
    root = repository_root(root)
    outcome = dict(
        commit=commit_sha, automated_result='not_available', passed=0, failed=0, skipped=0,
        ran=0, commands=[], summary='Validation not started.',
    )
    with TemporaryDirectory(prefix='groupctl-wt-') as wt_base:
        wt_dir = Path(wt_base) / 'worktree'
        result_file = Path(wt_base) / 'result.json'
        _git(root, 'worktree', 'add', '--detach', '--quiet', str(wt_dir), commit_sha)
        try:
            if not (wt_dir / 'tests').is_dir():
                outcome['summary'] = 'No tests/ directory at tested commit; not_available.'
                return outcome
            command = [sys.executable, '-B', str(Path(group_test.__file__).resolve()),
                       '--unittest-runner', str(result_file)]
            outcome['commands'] = ['python -B -m unittest discover -s tests -v (isolated worktree)']
            try:
                py_path = str(wt_dir)
                if os.environ.get('PYTHONPATH'):
                    py_path = py_path + os.pathsep + os.environ['PYTHONPATH']
                proc = subprocess.run(
                    command, cwd=wt_dir, capture_output=True, timeout=TIMEOUT, shell=False,
                    env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8',
                             PYTHONPATH=py_path),
                )
            except subprocess.TimeoutExpired:
                outcome.update(automated_result='partial', summary=f'Test timeout after {TIMEOUT}s.')
                return outcome
            if not result_file.exists():
                outcome.update(automated_result='partial', summary='Test runner did not emit result counts.')
                return outcome
            counts = json.loads(result_file.read_text(encoding='utf-8'), object_pairs_hook=unique_fields)
            if (any(type(counts.get(k)) is not int or counts[k] < 0 for k in COUNT_FIELDS)
                    or sum(counts[k] for k in ('passed', 'failed', 'skipped')) != counts['ran']):
                outcome.update(automated_result='partial', summary='Invalid runner counts.')
                return outcome
            res, expl = classify_result(counts, proc.returncode)
            # Detect if tests mutated the isolated worktree.
            dirty, _ = _git(wt_dir, 'status', '--porcelain=v1')
            if dirty and res == 'pass':
                res = 'fail'
                expl = 'Tests modified tracked/untracked files inside isolated worktree. ' + expl
            outcome.update(
                automated_result=res,
                passed=counts['passed'], failed=counts['failed'],
                skipped=counts['skipped'], ran=counts['ran'],
                summary=(expl + f'ran={counts["ran"]} passed={counts["passed"]} '
                         f'failed={counts["failed"]} skipped={counts["skipped"]}').strip(),
            )
            return outcome
        finally:
            _git(root, 'worktree', 'remove', '--force', str(wt_dir), allowed=(0, 1, 128))


def verify_integration(directory, load_config, args):
    """Run Level 1 (Git ancestry + conflict markers) and Level 2 (isolated tests) on an integration commit."""
    report = dict(
        operation='int_verify', status='failed', blockers=[],
        change_id=args.change_id, person_id=args.person_id, target_branch=args.target_branch,
        verification=dict(build='not_applicable', tests='not_available', semantic_review='not_performed'),
    )
    try:
        root = repository_root(directory)
        config = load_config(root / '.group/group.json')
        members = {m['person_id'] for m in read_members(root)}
        if args.person_id not in members:
            raise EventError(f'Unknown member person_id: {args.person_id}')
        event, work_sha = _find_candidate_for_verify(root, config, args.change_id)
        merge_sha = args.merge_commit
        if not merge_sha:
            merge_sha, _ = _git(root, *GIT, 'rev-parse', '--verify', f'refs/heads/{args.target_branch}^{{commit}}')
        else:
            merge_sha, _ = _git(root, *GIT, 'rev-parse', '--verify', merge_sha + '^{commit}')
        report.update(
            change_event_id=event['event_id'],
            candidate_work_sha=work_sha,
            merge_commit=merge_sha,
        )
        incorporated = ancestor(root, work_sha, merge_sha)
        report['work_incorporated'] = incorporated
        if not incorporated:
            report['blockers'].append('Candidate work commit is not an ancestor of merge_commit.')

        markers, code = _git(
            root, *GIT, 'grep', '-n', '-E', r'^(<{7}|={7}|>{7})( |$)', merge_sha,
            allowed=(0, 1),
        )
        marker_hits = markers.splitlines() if code == 0 and markers else []
        report['conflict_marker_hits'] = marker_hits
        if marker_hits:
            report['blockers'].append('Leftover conflict markers detected at merge_commit.')

        test_outcome = run_isolated_unittest(root, merge_sha)
        report['test_execution'] = test_outcome
        report['verification']['tests'] = test_outcome['automated_result']
        if test_outcome['automated_result'] != 'pass':
            report['blockers'].append(f'Automated tests did not pass: {test_outcome["automated_result"]}')

        if not report['blockers']:
            report['status'] = 'verified_mechanical_only'
    except (EventError, GitError, OSError, StatusError, ValueError) as exc:
        report['blockers'].append(str(exc))
    return report


def publish_integration_result(directory, load_config, args):
    """Create, commit, and optionally push+verify an integration_result event."""
    report = dict(
        operation='int_publish_result', status='blocked', verified=False,
        change_id=args.change_id, person_id=args.person_id, target_branch=args.target_branch,
        blockers=[],
    )
    try:
        root = repository_root(directory)
        config = load_config(root / '.group/group.json')
        members = {m['person_id'] for m in read_members(root)}
        if args.person_id not in members:
            raise EventError(f'Unknown member person_id: {args.person_id}')

        event_pub, work_sha = _find_candidate_for_verify(root, config, args.change_id)
        merge_sha, _ = _git(root, *GIT, 'rev-parse', '--verify', args.merge_commit + '^{commit}')
        build_status = args.build_status
        tests_status = args.tests_status
        semantic_status = args.semantic_review
        result_value = args.result

        for label, val in (('build', build_status), ('tests', tests_status), ('semantic_review', semantic_status)):
            if val not in VERIFICATION_VALUES:
                raise EventError(f'Invalid verification.{label}: {val}')
        if semantic_status not in ('pass', 'fail', 'needs_human'):
            raise EventError('semantic_review must be explicitly pass, fail, or needs_human.')

        incorporated = ancestor(root, work_sha, merge_sha)
        if result_value == 'success':
            if not incorporated:
                raise EventError('Cannot report result=success when work commit is not incorporated in merge_commit.')
            if build_status not in ('pass', 'not_applicable'):
                raise EventError('Cannot report result=success when build did not pass or is not_applicable.')
            if tests_status != 'pass':
                raise EventError('Cannot report result=success unless tests==pass (missing/partial tests require needs_human or failed).')
            if semantic_status != 'pass':
                raise EventError('Cannot report result=success unless semantic_review==pass.')
        if (semantic_status == 'fail' or tests_status == 'fail' or build_status == 'fail') and result_value == 'success':
            raise EventError('Failed verification cannot produce result=success.')

        now, created_at = utc_now()
        conflicts = list(args.conflict or [])
        resolution = args.conflict_resolution_summary or (
            'No textual conflicts.' if not conflicts else 'Conflicts resolved and reviewed.'
        )
        event = validate_event(dict(
            protocol_version='0.1',
            event_id=new_event_id(args.person_id, now),
            event_type='integration_result',
            group_id=config['group_id'],
            person_id=args.person_id,
            created_at=created_at,
            related_events=[event_pub['event_id']],
            change_id=args.change_id,
            target_branch=args.target_branch,
            result=result_value,
            merge_commit=merge_sha,
            conflicts=conflicts,
            conflict_resolution_summary=resolution,
            verification=dict(build=build_status, tests=tests_status, semantic_review=semantic_status),
            summary=args.summary,
        ))

        commit_rep = commit_event_file(
            root, load_config, args.person_id, event,
            message=f'Record integration_result for {args.change_id} ({result_value})',
            check_related=True,
        )
        report.update(
            event_id=event['event_id'],
            event_path=commit_rep['event_path'],
            event_commit=commit_rep['commit'],
            event_blob=commit_rep['event_blob'],
            publication='local_committed',
            status='local_event_committed',
        )
        if args.remote:
            if not (args.expected_url and args.confirm_reviewed):
                raise GitError('Pushing integration_result requires --remote, --expected-url, and --confirm-reviewed.')
            push_rep = push_and_verify_event(
                root, branch=commit_rep['branch'], commit=commit_rep['commit'],
                parent=commit_rep['parent'], event_id=event['event_id'],
                event_path=commit_rep['event_path'], event_blob=commit_rep['event_blob'],
                remote=args.remote, expected_url=args.expected_url,
                expected_remote_head=args.expected_remote_head,
            )
            report['push'] = push_rep
            report['verified'] = push_rep['verified']
            report['publication'] = push_rep['result']
            report['status'] = push_rep['result']
    except (EventError, GitError, OSError, StatusError, ValueError) as exc:
        report['blockers'].append(str(exc))
    return report
