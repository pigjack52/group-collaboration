"""INT tests using temporary Git repositories and the real CLI."""
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

import group_int
import groupctl

from test_events import sample
import test_pub


TARGET = test_pub.BRANCH
SOURCE = 'people/P02/work'
SOURCE_REF = 'refs/remotes/origin/' + SOURCE


class IntPreflightTests(unittest.TestCase):
    git = test_pub.PubTests.git
    commit = test_pub.PubTests.commit
    snapshot = test_pub.PubTests.snapshot

    def setUp(self):
        test_pub.PubTests.setUp(self)
        member = json.loads(self.member.read_text(encoding='utf-8'))
        member.update(person_id='P02')
        self.member.with_name('P02.json').write_text(json.dumps(member), encoding='utf-8')
        (self.root / '.gitignore').write_bytes(b'.group/local/\n')
        self.base = self.commit('register publisher')
        self.git('update-ref', 'refs/heads/main', self.base)
        self.git('switch', '-c', SOURCE)
        self.file.write_bytes(b'published first change\n')
        self.commit('first work commit')
        (self.root / 'second.txt').write_bytes(b'published second change\n')
        self.work = self.commit('second work commit')
        self.event_dir = self.root / '.group/events'
        self.event_dir.mkdir()
        self.event = dict(sample('change_published'), event_id='E-int', group_id='G-test',
                          person_id='P02', change_id='C-int', branch=SOURCE,
                          commit=self.work, files_changed=['tracked.txt', 'second.txt'],
                          proposed_for_integration=True)
        self.tip = self.record(self.event)
        self.git('update-ref', SOURCE_REF, self.tip)
        self.git('switch', TARGET)

    def record(self, event, filename=None):
        path = self.event_dir / (filename or event['event_id'] + '.json')
        path.write_text(json.dumps(event), encoding='utf-8')
        return self.commit('publication fixture')

    def replace_publication(self, **fields):
        self.git('switch', SOURCE)
        self.tip = self.record(dict(self.event, **fields))
        self.git('update-ref', SOURCE_REF, self.tip)
        self.git('switch', TARGET)

    def run_preflight(self, status='review_required', *, target=TARGET, person='P01'):
        before = self.snapshot()
        result = subprocess.run(
            [sys.executable, '-B', str(test_pub.CLI), 'int', '--preflight', '--person-id', person,
             '--change-id', 'C-int', '--target-branch', target],
            cwd=self.root / '.group', env=self.env, capture_output=True, text=True,
            encoding='utf-8', timeout=45)
        self.assertEqual(self.snapshot(), before)
        expected = int(status not in ('review_required', 'history_contains_candidate'))
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], status, report)
        self.assertEqual(bool(report['blockers']), bool(expected), report)
        return report

    def test_unique_multi_commit_candidate_with_advanced_source_and_old_receipt(self):
        cache = self.root / '.group/local/sync-discover.json'
        cache.parent.mkdir()
        cache.write_text(json.dumps({'seen': {'E-int': json.dumps(self.event)},
                                     'refs': {SOURCE_REF: self.tip}}), encoding='utf-8')
        self.git('remote', 'add', 'origin', str(self.root / 'unavailable.git'))
        report = self.run_preflight()
        blob = self.git('rev-parse', f'{self.tip}:.group/events/E-int.json')
        self.assertEqual(report['candidate_event'], self.event)
        self.assertEqual(report['candidate_work_sha'], self.work)
        self.assertEqual(report['target_sha'], self.base)
        self.assertEqual(report['target_kind'], 'personal')
        self.assertEqual(report['source_refs'], [dict(ref=SOURCE_REF, tip_sha=self.tip,
                                                     event_path='.group/events/E-int.json',
                                                     event_blob_sha=blob)])
        self.assertEqual(report['event_blob_sha'], blob)
        ancestry = report['ancestry_evidence']
        self.assertTrue(ancestry['work_is_ancestor_of_source'])
        self.assertNotEqual(ancestry['source_tip_sha'], self.work)
        self.assertFalse(ancestry['work_is_ancestor_of_target'])
        self.assertEqual(ancestry['target_work_merge_bases'], [self.base])
        self.assertIn('may be stale', report['unverified']['remote_state'])
        self.assertIn('not verified', report['unverified']['contribution_scope'])

    def test_identical_event_on_multiple_refs_is_one_candidate(self):
        self.git('update-ref', 'refs/remotes/origin/main', self.tip)
        report = self.run_preflight()
        self.assertEqual(report['event_id'], 'E-int')
        self.assertEqual({item['ref'] for item in report['source_refs']},
                         {SOURCE_REF, 'refs/remotes/origin/main'})

    def test_same_change_id_has_conflicting_events(self):
        self.git('switch', SOURCE)
        tip = self.record(dict(self.event, event_id='E-other', commit=self.base))
        self.git('update-ref', SOURCE_REF, tip)
        self.git('switch', TARGET)
        report = self.run_preflight('candidate_conflict')
        self.assertEqual(len(report['conflicting_candidates']), 2)
        self.assertIsNone(report['candidate_work_sha'])

    def test_same_event_id_with_different_blob_is_conflicting(self):
        self.git('update-ref', 'refs/remotes/origin/main', self.tip)
        self.replace_publication(change_id='C-other')
        report = self.run_preflight('candidate_conflict')
        self.assertEqual(len(report['conflicting_candidates']), 2)

    def test_missing_candidate_commit(self):
        self.replace_publication(commit='0' * len(self.work))
        report = self.run_preflight('insufficient_evidence')
        self.assertIn('missing', ' '.join(report['blockers']))

    def test_candidate_object_is_not_commit(self):
        self.replace_publication(commit=self.git('rev-parse', f'{self.work}:second.txt'))
        report = self.run_preflight('insufficient_evidence')
        self.assertIn('not a Git commit', ' '.join(report['blockers']))

    def test_abbreviated_work_sha_resolves_to_full_commit(self):
        self.replace_publication(commit=self.work[:12])
        self.assertEqual(self.run_preflight()['candidate_work_sha'], self.work)

    def test_work_is_not_reachable_from_declared_source(self):
        self.file.write_bytes(b'local only\n')
        local = self.commit('unreachable work')
        self.replace_publication(commit=local)
        report = self.run_preflight('insufficient_evidence')
        self.assertFalse(report['ancestry_evidence']['work_is_ancestor_of_source'])

    def test_source_ref_must_exist_even_if_event_is_on_canonical(self):
        self.git('update-ref', 'refs/remotes/origin/main', self.tip)
        self.git('update-ref', '-d', SOURCE_REF)
        report = self.run_preflight('insufficient_evidence')
        self.assertIn('no direct origin tracking ref', ' '.join(report['blockers']))

    def test_event_path_group_and_source_author_are_checked(self):
        for fields, detail in (
            (dict(event_id='E-wrong'), 'event ID does not match filename'),
            (dict(group_id='G-other'), 'group_id differs'),
            (dict(person_id='P01'), 'own the declared source branch'),
        ):
            with self.subTest(fields=fields):
                self.git('switch', SOURCE)
                path = self.event_dir / 'E-int.json'
                path.write_text(json.dumps(dict(self.event, **fields)), encoding='utf-8')
                tip = self.commit('replace publication fixture')
                self.git('update-ref', SOURCE_REF, tip)
                self.git('switch', TARGET)
                report = self.run_preflight('insufficient_evidence')
                self.assertIn(detail, ' '.join(report['blockers']))

    def test_target_history_already_contains_work(self):
        self.git('merge', '--ff-only', self.work)
        report = self.run_preflight('history_contains_candidate')
        self.assertTrue(report['ancestry_evidence']['work_is_ancestor_of_target'])
        self.assertIn('Not assessed', report['unverified']['semantic_integration'])

    def test_missing_target_branch(self):
        report = self.run_preflight('target_unsafe', target='people/P01/missing')
        self.assertIsNone(report['target_sha'])

    def test_dirty_index_worktree_and_untracked(self):
        (self.root / 'staged.txt').write_bytes(b'staged\n')
        self.git('add', 'staged.txt')
        self.file.write_bytes(b'unstaged\n')
        (self.root / 'extra.txt').touch()
        report = self.run_preflight('target_unsafe')
        self.assertEqual(report['worktree']['staged'], ['staged.txt'])
        self.assertEqual(report['worktree']['unstaged'], ['tracked.txt'])
        self.assertEqual(report['worktree']['untracked'], ['extra.txt'])

    def test_active_merge_and_unmerged_index(self):
        self.file.write_bytes(b'conflicting local work\n')
        self.commit('conflicting local work')
        result = subprocess.run(['git', 'merge', '--no-commit', self.work], cwd=self.root,
                                env=self.env, capture_output=True)
        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        report = self.run_preflight('target_unsafe')
        self.assertIn('MERGE_HEAD', report['worktree']['git_operations'])
        self.assertEqual(report['worktree']['unmerged_paths'], ['tracked.txt'])

    def test_index_lock_and_hidden_flag(self):
        self.git('update-index', '--assume-unchanged', 'tracked.txt')
        (self.root / '.git/index.lock').touch()
        report = self.run_preflight('target_unsafe')
        self.assertTrue(report['worktree']['index_lock_present'])
        self.assertEqual(report['worktree']['hidden_index_flags'][0]['path'], 'tracked.txt')

    def test_canonical_target_reports_sha_and_preserves_refs(self):
        report = self.run_preflight('target_unsafe', target='main')
        self.assertEqual(report['target_sha'], self.base)
        self.assertEqual(report['target_kind'], 'canonical')
        self.git('switch', 'main')
        report = self.run_preflight(target='main')
        self.assertEqual(report['target_sha'], self.base)
        self.assertEqual(report['target_kind'], 'canonical')

    def test_personal_target_must_belong_to_operator(self):
        self.run_preflight('target_unsafe', person='P02')

    def ff_args(self, **overrides):
        expected = dict(person_id='P01', change_id='C-int', target_branch=TARGET,
                        expected_head=self.base, work_sha=self.work,
                        expected_event_id='E-int',
                        expected_event_blob=self.git('rev-parse', f'{self.tip}:.group/events/E-int.json'),
                        expected_source_ref=SOURCE_REF, expected_source_tip=self.tip,
                        expected_incoming=','.join(self.git('rev-list', f'{self.base}..{self.work}').splitlines()) or 'none')
        expected.update(overrides)
        return ['int', '--ff-only', '--confirm-reviewed', '--confirm-unproposed',
                *(arg for key, value in expected.items() for arg in
                  ('--' + key.replace('_', '-'), value))]

    def ff(self, args, status, blocker=None):
        before = self.snapshot()
        refs = group_int.all_refs(self.root)
        result = subprocess.run([sys.executable, '-B', str(test_pub.CLI), *args],
                                cwd=self.root, env=self.env, capture_output=True,
                                text=True, encoding='utf-8', timeout=45)
        self.assertEqual(result.returncode, int(status not in (
            'local_fast_forward_verified', 'already_in_history', 'pending_commit')),
            result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], status, report)
        if blocker:
            self.assertIn(blocker, ' '.join(report['blockers']))
        if status == 'local_fast_forward_verified':
            refs['refs/heads/' + TARGET] = self.work + '\t'
        elif not report['attempted']:
            self.assertEqual(self.snapshot(), before)
        self.assertEqual(group_int.all_refs(self.root), refs)
        return report

    def test_ff_advances_only_target_to_work_and_replay_is_noop(self):
        args = self.ff_args()
        report = self.ff(args, 'local_fast_forward_verified')
        self.assertEqual(report['changed_refs'], ['refs/heads/' + TARGET])
        self.assertEqual(report['after']['head'], self.work)
        self.assertFalse(report['after']['git_operations'])
        self.assertFalse(report['after']['unmerged_paths'])
        self.assertEqual(self.git('status', '--porcelain'), '')
        self.assertEqual(self.git('write-tree'), self.git('rev-parse', self.work + '^{tree}'))
        self.assertFalse(self.event_dir.exists())
        self.ff(args, 'already_in_history')
        self.ff(self.ff_args(expected_head=self.work, expected_incoming='none'), 'already_in_history')
        self.file.write_text('more', encoding='utf-8')
        tip = self.commit('after work')
        self.ff(self.ff_args(expected_head=tip, expected_incoming='none'), 'already_in_history')

    def test_ff_rejects_stale_or_unreviewed_evidence(self):
        for overrides, reason in (
            (dict(expected_head=self.work), 'stale'),
            (dict(expected_source_tip=self.work), 'source_tip_sha'),
            (dict(expected_source_ref='refs/remotes/origin/main'), 'source_ref'),
            (dict(expected_event_blob=self.work), 'event_blob_sha'),
            (dict(expected_event_id='E-other'), 'event_path'),
            (dict(change_id='c-int'), 'change_id'),
            (dict(work_sha=self.tip), 'candidate_work_sha'),
            (dict(expected_incoming=self.work), 'commit range'),
        ):
            with self.subTest(overrides=overrides):
                self.ff(self.ff_args(**overrides), 'blocked', reason)
        args = self.ff_args()
        self.replace_publication(summary='changed after review')
        self.ff(args, 'blocked', 'event_blob_sha')
        args = self.ff_args()
        self.git('switch', SOURCE)
        self.git('commit', '--allow-empty', '-m', 'advanced publisher tip with same event')
        self.git('update-ref', SOURCE_REF, 'HEAD')
        self.git('switch', TARGET)
        self.ff(args, 'blocked', 'source_tip_sha')

    def test_ff_requires_separate_confirmation_for_unproposed(self):
        self.replace_publication(proposed_for_integration=False)
        args = self.ff_args()
        args.remove('--confirm-unproposed')
        self.ff(args, 'blocked', '--confirm-unproposed')
        self.ff([*args, '--confirm-unproposed'], 'local_fast_forward_verified')

    def test_ff_refuses_event_only_candidate(self):
        self.replace_publication(commit=self.tip)
        work = self.git('rev-parse', 'refs/heads/' + SOURCE + '^')
        self.ff(self.ff_args(work_sha=work), 'blocked', 'event commit')

    def test_ff_rejects_divergence_and_unrelated_history(self):
        self.file.write_text('local', encoding='utf-8')
        local = self.commit('local work')
        self.ff(self.ff_args(expected_head=local), 'blocked', 'non-fast-forward')
        tree = self.git('rev-parse', local + '^{tree}')
        orphan = self.git('commit-tree', tree, '-m', 'unrelated')
        self.git('update-ref', 'refs/heads/' + TARGET, orphan)
        self.ff(self.ff_args(expected_head=orphan), 'blocked', 'common ancestor')

    def test_ff_refuses_tracking_ahead_or_diverged(self):
        self.git('branch', '--set-upstream-to=' + SOURCE, TARGET)
        self.ff(self.ff_args(), 'blocked', 'Configured upstream')
        self.git('branch', '--unset-upstream', TARGET)
        tracking = 'refs/remotes/origin/' + TARGET
        self.git('update-ref', tracking, self.work)
        self.ff(self.ff_args(), 'blocked', 'synchronize')
        self.file.write_text('local', encoding='utf-8')
        local = self.commit('local work')
        self.ff(self.ff_args(expected_head=local), 'blocked', 'synchronize')

    def test_ff_refuses_canonical_or_other_operator(self):
        self.ff(self.ff_args(person_id='P02'), 'blocked', 'operator-owned')
        self.git('switch', 'main')
        self.ff(self.ff_args(target_branch='main'), 'blocked', 'canonical and main')

    def test_ff_refuses_dirty_categories(self):
        args = self.ff_args()
        self.file.write_text('local', encoding='utf-8')
        self.ff(args, 'blocked', 'dirty')
        self.git('restore', '--', 'tracked.txt')
        (self.root / 'extra').write_text('local', encoding='utf-8')
        self.ff(args, 'blocked', 'dirty')
        self.git('add', 'extra')
        self.ff(args, 'blocked', 'dirty')

    def test_ff_refuses_operation_lock_and_hidden_flags(self):
        args = self.ff_args()
        for name, reason in (('MERGE_HEAD', 'Unfinished'), ('index.lock', 'index lock')):
            marker = self.root / '.git' / name
            marker.write_text(self.work + '\n', encoding='ascii')
            self.ff(args, 'blocked', reason)
            self.assertTrue(marker.exists())
            marker.unlink()
        for flag in ('assume-unchanged', 'skip-worktree'):
            self.git('update-index', '--' + flag, 'tracked.txt')
            self.ff(args, 'blocked', 'Index flags')
            self.git('update-index', '--no-' + flag, 'tracked.txt')

    def test_ff_refuses_replace_and_external_drivers(self):
        args = self.ff_args()
        self.git('replace', self.work, self.base)
        self.ff(args, 'blocked', 'Replace objects')
        self.git('replace', '-d', self.work)
        for key, reason in (('merge.external.driver', 'custom merge drivers'),
                            ('filter.external.smudge', 'external Git filters')):
            self.git('config', key, 'external')
            self.ff(args, 'blocked', reason)
            self.git('config', '--unset', key)

    def test_ff_refuses_candidate_submodule(self):
        self.git('switch', SOURCE)
        self.git('update-index', '--add', '--cacheinfo', f'160000,{self.work},nested')
        self.git('commit', '-m', 'candidate submodule')
        self.work = self.git('rev-parse', 'HEAD')
        self.tip = self.record(dict(self.event, commit=self.work))
        self.git('update-ref', SOURCE_REF, self.tip)
        self.git('switch', TARGET)
        self.ff(self.ff_args(), 'blocked', 'Candidate contains submodules')

    def test_ff_git_failure_preserves_ignored_collision(self):
        (self.root / '.git/info/exclude').write_text('second.txt\n', encoding='utf-8')
        path = self.root / 'second.txt'
        path.write_text('local ignored content', encoding='utf-8')
        report = self.ff(self.ff_args(), 'write_unverified', 'overwritten')
        self.assertEqual(report['after']['head'], self.base)
        self.assertEqual(report['changed_refs'], [])
        self.assertEqual(path.read_text(encoding='utf-8'), 'local ignored content')

    def test_ff_serializes_two_invocations(self):
        from argparse import Namespace
        args = self.ff_args()
        values = dict(zip((key[2:].replace('-', '_') for key in args[4::2]), args[5::2]))
        real_preflight = group_int.preflight

        def preflight_while_locked(*params):
            self.ff(args, 'blocked', 'INT write lock exists')
            return real_preflight(*params)

        with patch('group_int.preflight', side_effect=preflight_while_locked):
            report = group_int.fast_forward(self.root, groupctl.load_config,
                                             Namespace(confirm_unproposed=True, **values))
        self.assertEqual(report['status'], 'local_fast_forward_verified', report)
        self.assertFalse((self.root / '.git/groupctl-int.lock').exists())

    def prepare_args(self, **overrides):
        args = self.ff_args(**overrides)
        args[1] = '--prepare-merge'
        return args

    def diverge(self, conflict=False):
        path = self.file if conflict else self.root / 'local.txt'
        path.write_bytes(b'local change\n')
        return self.commit('diverge on target')

    def test_prepare_leaves_reviewable_index_and_blocks_replay(self):
        head = self.diverge()
        before_log = self.git('reflog', '--format=%H')
        args = self.prepare_args(expected_head=head)
        # Explicit command-line behavior overrides local preferences.
        self.git('config', 'merge.ff', 'only')
        self.git('config', f'branch.{TARGET}.mergeOptions', '--squash')
        report = self.ff(args, 'pending_commit')
        self.assertEqual(report['after']['head'], head)
        self.assertEqual(report['after']['merge_heads'], [self.work])
        self.assertEqual(report['after']['unmerged_paths'], [])
        self.assertEqual(report['changed_refs'], [])
        self.assertEqual(self.git('rev-parse', 'HEAD', 'refs/heads/' + TARGET, 'MERGE_HEAD'),
                         '\n'.join((head, head, self.work)))
        self.assertEqual(self.git('reflog', '--format=%H'), before_log)
        self.assertEqual(self.git('ls-files', '-u'), '')
        self.assertEqual(self.git('diff', '--cached', '--name-only'), 'second.txt\ntracked.txt')
        self.assertIn('+published first change', self.git('diff', '--cached', '--', 'tracked.txt'))
        self.assertEqual(self.git('show', ':local.txt'), 'local change')
        self.assertFalse(self.event_dir.exists())
        self.ff(args, 'blocked', 'Unfinished Git operation')

    def test_prepare_reports_conflict_and_preserves_index_stages(self):
        head = self.diverge(conflict=True)
        self.git('config', 'rerere.enabled', 'true')
        report = self.ff(self.prepare_args(expected_head=head), 'conflicted')
        self.assertEqual(report['git_exit'], 1)
        self.assertIn('CONFLICT', report['git_output'])
        self.assertEqual(report['after']['unmerged_paths'], ['tracked.txt'])
        self.assertEqual(self.git('rev-parse', 'HEAD', 'MERGE_HEAD'), head + '\n' + self.work)
        for stage, commit in ((1, self.base), (2, head), (3, self.work)):
            self.assertEqual(self.git('show', f':{stage}:tracked.txt'),
                             self.git('show', commit + ':tracked.txt'))
        self.assertIn(b'<<<<<<<', self.file.read_bytes())
        self.assertFalse((self.root / '.git/rr-cache').exists())

    def test_prepare_blocks_ignored_collision(self):
        head = self.diverge()
        (self.root / '.git/info/exclude').write_text('second.txt\n', encoding='utf-8')
        path = self.root / 'second.txt'
        path.write_bytes(b'local ignored content\n')
        self.ff(self.prepare_args(expected_head=head), 'blocked', 'Ignored untracked paths')
        self.assertEqual(path.read_bytes(), b'local ignored content\n')

    def test_prepare_git_failure_preserves_repository_state(self):
        head = self.diverge()
        self.git('config', 'merge.verifySignatures', 'true')
        report = self.ff(self.prepare_args(expected_head=head), 'write_unverified', 'signature')
        self.assertTrue(report['attempted'])
        self.assertEqual(report['after']['head'], head)
        self.assertEqual(report['after']['merge_heads'], [])
        self.assertEqual(self.git('status', '--porcelain'), '')

    def test_prepare_refuses_linear_and_reports_already_in_history(self):
        args = self.prepare_args()
        self.ff(args, 'blocked', '--ff-only')
        self.git('merge', '--ff-only', self.work)
        self.ff(args, 'blocked', 'stale')
        self.ff(self.prepare_args(expected_head=self.work, expected_incoming='none'),
                'already_in_history')

    def test_prepare_refuses_unrelated_history(self):
        tree = self.git('rev-parse', self.base + '^{tree}')
        orphan = self.git('commit-tree', tree, '-m', 'unrelated')
        self.git('update-ref', 'refs/heads/' + TARGET, orphan)
        self.ff(self.prepare_args(expected_head=orphan), 'blocked', 'common ancestor')

    def test_prepare_refuses_multiple_merge_bases(self):
        other = self.diverge()
        tree = self.git('rev-parse', other + '^{tree}')
        head = self.git('commit-tree', tree, '-p', other, '-p', self.work, '-m', 'target merge')
        self.git('update-ref', 'refs/heads/' + TARGET, head)
        self.git('switch', SOURCE)
        self.work = self.git('commit-tree', tree, '-p', self.work, '-p', other,
                             '-m', 'candidate merge')
        self.git('reset', '--hard', self.work)
        self.event_dir.mkdir(exist_ok=True)
        self.tip = self.record(dict(self.event, commit=self.work))
        self.git('update-ref', SOURCE_REF, self.tip)
        self.git('switch', TARGET)
        report = self.ff(self.prepare_args(expected_head=head, expected_incoming=self.work),
                         'blocked', 'exactly one')
        self.assertEqual(len(report['preflight']['ancestry_evidence']['target_work_merge_bases']), 2)

    def test_prepare_rechecks_review_and_target_tracking(self):
        head = self.diverge()
        for overrides, reason in (
            (dict(expected_head=self.base), 'stale'),
            (dict(expected_source_tip=self.work), 'source_tip_sha'),
            (dict(expected_event_blob=self.work), 'event_blob_sha'),
            (dict(expected_incoming='none'), 'commit range'),
        ):
            with self.subTest(reason=reason):
                self.ff(self.prepare_args(**(dict(expected_head=head) | overrides)),
                        'blocked', reason)
        self.git('update-ref', 'refs/remotes/origin/' + TARGET, self.work)
        self.ff(self.prepare_args(expected_head=head), 'blocked', 'synchronize')

    def test_prepare_refuses_dirty_state_and_markers(self):
        head = self.diverge()
        args = self.prepare_args(expected_head=head)
        self.file.write_bytes(b'unsaved\n')
        self.ff(args, 'blocked', 'dirty')
        self.git('restore', '--', 'tracked.txt')
        extra = self.root / 'extra.txt'
        extra.touch()
        self.ff(args, 'blocked', 'dirty')
        self.git('add', 'extra.txt')
        self.ff(args, 'blocked', 'dirty')
        self.git('rm', '-f', 'extra.txt')
        for name, reason in (('index.lock', 'index lock'), ('CHERRY_PICK_HEAD', 'Unfinished')):
            with self.subTest(marker=name):
                marker = self.root / '.git' / name
                marker.write_text(head + '\n', encoding='ascii')
                self.ff(args, 'blocked', reason)
                marker.unlink()

    def test_prepare_refuses_protected_and_other_owner_targets(self):
        self.ff(self.prepare_args(person_id='P02'), 'blocked', 'operator-owned')
        self.git('switch', 'main')
        self.ff(self.prepare_args(target_branch='main'), 'blocked', 'canonical and main')
        config = self.root / '.group/group.json'
        data = json.loads(config.read_text(encoding='utf-8'))
        data['canonical_branch'] = TARGET
        config.write_text(json.dumps(data), encoding='utf-8')
        self.commit('configure different canonical branch')
        self.ff(self.prepare_args(target_branch='main'), 'blocked', 'operator-owned')
        self.git('switch', TARGET)
        config.write_text(json.dumps(data), encoding='utf-8')
        self.commit('configure personal canonical branch')
        self.ff(self.prepare_args(), 'blocked', 'canonical and main')

    def test_prepare_cli_requires_review_and_exclusive_mode(self):
        args = self.prepare_args()
        for invalid in ([*args, '--preflight'], [*args, '--ff-only'],
                        [arg for arg in args if arg != '--confirm-reviewed'], args[:-2]):
            with self.subTest(args=invalid):
                result = subprocess.run([sys.executable, '-B', str(test_pub.CLI), *invalid],
                                        cwd=self.root, env=self.env, capture_output=True, timeout=45)
                self.assertEqual(result.returncode, 2, result.stderr)

    def test_prepare_requires_unproposed_confirmation(self):
        self.replace_publication(proposed_for_integration=False)
        head = self.diverge()
        args = self.prepare_args(expected_head=head)
        args.remove('--confirm-unproposed')
        self.ff(args, 'blocked', '--confirm-unproposed')
        self.ff([*args, '--confirm-unproposed'], 'pending_commit')

    def inspect_args(self, **overrides):
        args = self.ff_args(**overrides)
        args[1] = '--inspect-merge'
        return args

    def inspect_pending(self, args, status, evidence='reviewed_evidence_matches'):
        before = self.snapshot()
        result = subprocess.run([sys.executable, '-B', str(test_pub.CLI), *args],
                                cwd=self.root, env=self.env, capture_output=True,
                                text=True, encoding='utf-8', timeout=45)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(result.returncode, int(status != 'awaiting_human_confirmation'),
                         result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], status, report)
        self.assertEqual(report['evidence_status'], evidence, report)
        self.assertTrue(report['state_verified'], report)
        return report

    def test_inspect_conflict_partial_and_all_resolutions_are_read_only(self):
        unusual = '衝突 name #.txt'
        self.git('switch', SOURCE)
        (self.root / unusual).write_bytes(b'candidate\n')
        self.work = self.commit('add candidate file')
        self.tip = self.record(dict(self.event, commit=self.work))
        self.git('update-ref', SOURCE_REF, self.tip)
        self.git('switch', TARGET)
        self.file.write_bytes(b'target\n')
        (self.root / unusual).write_bytes(b'target added file\n')
        head = self.commit('diverge on both paths')
        args = self.inspect_args(expected_head=head)
        self.ff(self.prepare_args(expected_head=head), 'conflicted')
        facts = self.inspect_pending(args, 'conflicted')['git_state']
        self.assertEqual(facts['head'], head)
        self.assertEqual(facts['merge_heads'], [self.work])
        for path, sides in (('tracked.txt', [(1, self.base), (2, head), (3, self.work)]),
                            (unusual, [(2, head), (3, self.work)])):
            self.assertEqual(facts['conflicts'][path], [
                dict(stage=stage, mode='100644', object_id=self.git('rev-parse', f'{commit}:{path}'))
                for stage, commit in sides
            ])
        self.file.write_bytes(b'resolved first\n')
        self.git('add', '--', 'tracked.txt')
        facts = self.inspect_pending(args, 'conflicted')['git_state']
        self.assertEqual(facts['unmerged_paths'], [unusual])
        (self.root / unusual).write_bytes(b'resolved second\n')
        self.git('add', '--', unusual)
        report = self.inspect_pending(args, 'awaiting_human_confirmation')
        self.assertEqual(report['git_state']['unmerged_paths'], [])
        self.assertEqual(report['git_state']['merge_heads'], [self.work])
        self.assertEqual(report['git_state']['head'], head)

    def test_inspect_modify_delete_and_staged_marker_check(self):
        self.git('rm', '--', 'tracked.txt')
        head = self.commit('remove target file')
        self.ff(self.prepare_args(expected_head=head), 'conflicted')
        args = self.inspect_args(expected_head=head)
        facts = self.inspect_pending(args, 'conflicted')['git_state']
        self.assertEqual([item['stage'] for item in facts['conflicts']['tracked.txt']], [1, 3])
        self.file.write_bytes(b'<<<<<<< HEAD\ncontent\n=======\nother\n>>>>>>> work\n')
        self.git('add', '--', 'tracked.txt')
        report = self.inspect_pending(args, 'blocked')
        self.assertEqual(report['git_state']['unmerged_paths'], [])
        self.assertNotEqual(report['staged_diff_check']['exit_code'], 0)
        self.git('rm', '-f', '--', 'tracked.txt')
        self.inspect_pending(args, 'awaiting_human_confirmation')

    def test_inspect_missing_and_mismatched_evidence(self):
        head = self.diverge()
        self.ff(self.prepare_args(expected_head=head), 'pending_commit')
        basic = ['int', '--inspect-merge', '--person-id', 'P01',
                 '--change-id', 'C-int', '--target-branch', TARGET]
        self.inspect_pending(basic, 'blocked', 'insufficient_evidence')
        for overrides in (dict(expected_head=self.base), dict(work_sha=self.base),
                          dict(expected_event_id='E-wrong'), dict(expected_event_blob=self.work),
                          dict(expected_source_tip=self.work), dict(change_id='C-wrong'),
                          dict(expected_incoming='none')):
            with self.subTest(overrides=overrides):
                self.inspect_pending(self.inspect_args(**(dict(expected_head=head) | overrides)),
                                     'blocked', 'evidence_mismatch')

    def test_inspect_diagnostic_mode_following_conflict(self):
        head = self.diverge(conflict=True)
        self.ff(self.prepare_args(expected_head=head), 'conflicted')
        args = ['int', '--inspect-merge', '--person-id', 'P01',
                '--change-id', 'C-unverified', '--target-branch', TARGET]
        report = self.inspect_pending(args, 'conflicted', 'insufficient_evidence')
        self.assertEqual(report['git_state']['unmerged_paths'], ['tracked.txt'])
        self.assertNotIn('reviewed_evidence', report)

    def test_inspect_rejects_duplicate_event_for_same_work(self):
        head = self.diverge()
        self.git('switch', '-c', 'people/P02/alternative', SOURCE)
        tip = self.record(dict(self.event, event_id='E-duplicate'))
        self.git('switch', TARGET)
        self.ff(self.prepare_args(expected_head=head), 'pending_commit')
        args = self.inspect_args(expected_head=head)
        self.git('update-ref', 'refs/remotes/origin/people/P02/alternative', tip)
        report = self.inspect_pending(args, 'blocked', 'evidence_mismatch')
        self.assertIn('Conflicting candidate events', ' '.join(report['blockers']))

    def test_inspect_blocks_dirty_paths_and_hidden_flags_after_staging(self):
        head = self.diverge()
        args = self.inspect_args(expected_head=head)
        self.ff(self.prepare_args(expected_head=head), 'pending_commit')
        self.file.write_bytes(b'unstaged\n')
        extra = self.root / 'untracked.txt'
        extra.write_bytes(b'untracked\n')
        facts = self.inspect_pending(args, 'blocked')['git_state']
        self.assertEqual(facts['unstaged'], ['tracked.txt'])
        self.assertEqual(facts['untracked'], ['untracked.txt'])
        self.git('update-index', '--assume-unchanged', 'tracked.txt')
        facts = self.inspect_pending(args, 'blocked', 'unverified')['git_state']
        self.assertEqual(facts['hidden_index_flags'][0]['path'], 'tracked.txt')

    def test_inspect_refuses_protected_or_wrong_targets(self):
        for person, branch in (('P01', 'main'), ('P01', SOURCE), ('P02', TARGET)):
            self.inspect_pending(self.inspect_args(person_id=person, target_branch=branch),
                                 'blocked', 'unverified')
        config = self.root / '.group/group.json'
        group = json.loads(config.read_text(encoding='utf-8'))
        group['canonical_branch'] = TARGET
        config.write_text(json.dumps(group), encoding='utf-8')
        self.inspect_pending(self.inspect_args(), 'blocked', 'unverified')

    def test_inspect_no_pending_merge_and_inconsistent_markers(self):
        args = self.inspect_args()
        self.inspect_pending(args, 'no_pending_merge', 'unverified')
        marker = self.root / '.git/MERGE_HEAD'
        marker.write_text(f'{self.work}\n{self.base}\n', encoding='ascii')
        self.inspect_pending(args, 'blocked', 'unverified')
        marker.write_text(f'{self.work}\n', encoding='ascii')
        for name in ('index.lock', 'CHERRY_PICK_HEAD', 'MERGE_AUTOSTASH'):
            path = marker.with_name(name)
            path.write_text(self.work, encoding='ascii')
            self.inspect_pending(args, 'blocked', 'unverified')
            path.unlink()

    def test_inspect_detects_concurrent_index_ref_marker_and_worktree_changes(self):
        from argparse import Namespace

        head = self.diverge()
        self.ff(self.prepare_args(expected_head=head), 'pending_commit')
        extra = self.root / 'extra.txt'
        extra.write_bytes(b'first\n')
        original = group_int.inspect_merge_snapshot
        args = Namespace(confirm_reviewed=False, person_id='P01', change_id='C-int', target_branch=TARGET)
        for mutate in (
            lambda: extra.write_bytes(b'other\n'),
            lambda: self.git('add', '--', 'extra.txt'),
            lambda: self.git('update-ref', 'refs/heads/unrelated', head),
            lambda: (self.root / '.git/MERGE_HEAD').write_text(f'{self.base}\n', encoding='ascii'),
        ):
            def observe_then_mutate(root):
                result = original(root)
                if spy.call_count == 1:
                    mutate()
                return result

            with patch('group_int.inspect_merge_snapshot', side_effect=observe_then_mutate) as spy:
                report = group_int.inspect_merge(self.root, groupctl.load_config, args)
            self.assertEqual(report['status'], 'blocked', report)
            self.assertFalse(report['state_verified'], report)
            self.assertIn('Git state changed during inspection', ' '.join(report['blockers']))

    def test_inspect_cli_rejects_partial_malformed_and_nonexclusive_options(self):
        args = self.inspect_args()
        for invalid in ([*args, '--preflight'], [*args, '--ff-only'], [*args, '--prepare-merge'],
                        args[:-2], self.inspect_args(work_sha='abcd'),
                        [arg for arg in args if arg != '--confirm-reviewed']):
            with self.subTest(args=invalid):
                result = subprocess.run([sys.executable, '-B', str(test_pub.CLI), *invalid],
                                        cwd=self.root, env=self.env, capture_output=True, timeout=45)
                self.assertEqual(result.returncode, 2, result.stderr)
