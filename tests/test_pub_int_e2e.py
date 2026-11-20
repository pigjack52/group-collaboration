"""Publish and integrate through real Git repositories with a file-only remote."""
from contextlib import chdir
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import group_int
import groupctl
from group_events import parse_event
from test_pub import BRANCH, CLI
import test_int
import test_pub
import test_pub_e2e


TARGET = 'people/P02/integration'
TARGET_REF = f'refs/heads/{TARGET}'
SOURCE = f'refs/heads/{BRANCH}'
TRACKING = f'refs/remotes/origin/{BRANCH}'
PROTECTED = ('refs/heads/main', 'refs/heads/canonical', 'refs/heads/people/P03/other')
PATHS = ('tracked.txt', 'second.txt')
CHANGE = 'C-pub-int'


class PubIntEndToEndTests(TestCase):
    git = test_pub.PubTests.git
    commit = test_pub.PubTests.commit
    snapshot = test_pub.PubTests.snapshot
    run_cli = test_pub_e2e.PubEndToEndTests.run_cli
    inspect_pending = test_int.IntPreflightTests.inspect_pending

    def setUp(self):
        test_pub.PubTests.setUp(self)
        self.env.update(GIT_ALLOW_PROTOCOL='file', GIT_OPTIONAL_LOCKS='0')
        self.enterContext(patch.dict('os.environ', self.env, clear=True))
        self.publisher = self.root
        fixture = Path(self.enterContext(TemporaryDirectory(prefix='groupctl-pub-int-')))
        self.receiver = fixture / 'receiver'
        self.url = (fixture / 'shared.git').as_posix()
        self.review_file = fixture / 'review.json'
        self.git('init', '--bare', '--initial-branch=main', self.url)
        member_path = self.root / '.group/members/P01.json'
        member = json.loads(member_path.read_text(encoding='utf-8'))
        member['person_id'] = 'P02'
        member_path.with_name('P02.json').write_text(json.dumps(member), encoding='utf-8')
        config_path = self.root / '.group/group.json'
        config = json.loads(config_path.read_text(encoding='utf-8'))
        config.update(canonical_branch='canonical', repository=self.url)
        config_path.write_text(json.dumps(config), encoding='utf-8')
        (self.root / PATHS[1]).write_bytes(b'base\n')
        self.base = self.commit('Register integration fixture')
        self.git('remote', 'add', 'origin', self.url)
        self.git('push', 'origin', *(f'{self.base}:{ref}' for ref in (*PROTECTED, SOURCE)))
        self.git('clone', '--branch', 'main', self.url, str(self.receiver))
        self.publish()
        self.root = self.receiver
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.test')
        self.git('config', 'core.autocrlf', 'false')
        self.git('branch', 'canonical', 'origin/canonical')
        self.git('branch', 'people/P03/other', 'origin/people/P03/other')
        self.git('switch', '-c', TARGET)
        self.git('fetch', 'origin')
        self.assertEqual(self.git('rev-parse', TRACKING), self.tip)
        self.assertEqual(self.bare_refs(), self.published_refs)
        self.addCleanup(self.assert_remote_unchanged)

    def bare_refs(self):
        return group_int.all_refs(self.url)

    def assert_remote_unchanged(self):
        self.assertEqual(self.bare_refs(), self.published_refs)
        self.assertEqual(group_int.all_refs(self.publisher), self.publisher_refs)

    def publish(self):
        def pub(mode, *args):
            return json.loads(self.run_cli(self.publisher, 'pub', mode, '--person-id', 'P01',
                                           '--expected-branch', BRANCH, '--confirm-reviewed', *args))

        remote = ('--remote', 'origin', '--expected-url', self.url)
        (self.root / 'prerequisite.txt').write_bytes(b'first work commit\n')
        self.first = self.commit('Add prerequisite')
        for path in PATHS:
            (self.root / path).write_bytes(b'published content\n')
        work = pub('--work-commit', '--expected-head', self.first, '--message', 'Update fixtures',
                   '--file', PATHS[0], '--file', PATHS[1])
        self.assertEqual(work['result'], 'created', work)
        self.work = work['commit']
        self.assertEqual(self.git('rev-parse', 'HEAD', 'HEAD^').splitlines(), [self.work, self.first])
        pushed = pub('--push-work', *remote, '--expected-head', self.work,
                     '--expected-remote-head', self.base)
        self.assertEqual(pushed['result'], 'verified', pushed)
        before_event = self.bare_refs()
        self.assertEqual(before_event, {**dict.fromkeys(PROTECTED, self.base + '\t'),
                                        SOURCE: self.work + '\t'})

        self.review_file.write_text(json.dumps(dict(summary='Update the fixture files.',
                                                    files_changed=list(PATHS), interface_changes=[],
                                                    tests_run=[], known_issues=[],
                                                    proposed_for_integration=True)), encoding='utf-8')
        contribution = (*remote, '--change-id', CHANGE, '--work-sha', self.work)
        recorded = pub('--event-commit', *contribution, '--expected-head', self.work,
                       '--review-file', str(self.review_file))
        self.assertEqual(recorded['result'], 'local_event_committed', recorded)
        self.tip, self.event_path = recorded['commit'], recorded['event_path']
        sent = pub('--push-event', *contribution, '--expected-head', self.tip,
                   '--expected-remote-head', self.work)
        self.assertEqual(sent['result'], 'published_verified', sent)
        self.assertEqual(self.git('rev-parse', f'{self.tip}^'), self.work)
        self.blob = self.git('rev-parse', f'{self.tip}:{self.event_path}')
        self.assertEqual(sent['remote_blob_verified'], self.blob)
        self.event = parse_event(self.git('show', f'{self.tip}:{self.event_path}'))
        self.assertEqual((self.event['event_type'], self.event['event_id'], self.event['change_id'],
                          self.event['branch'], self.event['commit'], self.event['person_id']),
                         ('change_published', recorded['event_id'], CHANGE, BRANCH, self.work, 'P01'))
        self.published_refs = {**before_event, SOURCE: self.tip + '\t'}
        self.publisher_refs = group_int.all_refs(self.publisher)

    def int_args(self, mode, **evidence):
        return ['int', mode, '--person-id', 'P02', '--change-id', CHANGE,
                '--target-branch', TARGET,
                *(arg for key, value in evidence.items() for arg in
                  ('--' + key.replace('_', '-'), value)),
                *(['--confirm-reviewed'] if evidence else [])]

    def review(self, head):
        before = self.snapshot()
        report = json.loads(self.run_cli(self.root, *self.int_args('--preflight')))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(report['status'], 'review_required', report)
        self.assertEqual(report['target_sha'], head)
        self.assertEqual(report['candidate_work_sha'], self.work)
        self.assertEqual(report['candidate_event'], self.event)
        self.assertEqual(report['event_blob_sha'], self.blob)
        self.assertEqual(report['source_refs'], [dict(ref=TRACKING, tip_sha=self.tip,
                                                     event_path=self.event_path,
                                                     event_blob_sha=self.blob)])
        self.assertEqual(report['incoming_commits'], sorted((self.first, self.work)))
        self.assertEqual(report['unverified']['validation'], 'No test, build or merge was run.')
        return dict(expected_head=report['target_sha'], work_sha=report['candidate_work_sha'],
                    expected_event_id=report['candidate_event']['event_id'],
                    expected_event_blob=report['event_blob_sha'],
                    expected_source_ref=report['source_refs'][0]['ref'],
                    expected_source_tip=report['source_refs'][0]['tip_sha'],
                    expected_incoming=','.join(report['incoming_commits']))

    def integrate(self, mode, evidence, status, blocker=None):
        before = self.snapshot()
        refs = group_int.all_refs(self.root)
        result = subprocess.run(
            [sys.executable, '-B', str(CLI), *self.int_args(mode, **evidence)],
            cwd=self.root, env=self.env, capture_output=True, text=True,
            encoding='utf-8', timeout=45)
        self.assertEqual(result.returncode, int(status not in (
            'local_fast_forward_verified', 'pending_commit')), result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], status, report)
        self.assert_validation_pending(report)
        if blocker:
            self.assertIn(blocker, ' '.join(report['blockers']))
        if status == 'blocked':
            self.assertFalse(report['attempted'])
            self.assertEqual(self.snapshot(), before)
        if status == 'local_fast_forward_verified':
            refs[TARGET_REF] = self.work + '\t'
        self.assertEqual(group_int.all_refs(self.root), refs)
        return report

    def assert_validation_pending(self, report):
        self.assertEqual(report['validation'], 'Build, tests and semantic review were not performed.')
        self.assertFalse((self.root / '.group/events').exists())
        self.assertEqual(self.git('log', '--all', '--format=', '--name-only', '--', '.group/events'),
                         self.event_path)

    def diverge(self, conflict=False):
        for path in PATHS if conflict else ('local.txt',):
            (self.root / path).write_bytes(b'target content\n')
        return self.commit('Diverge integration target')

    def assert_pending(self, head):
        self.assertEqual(self.git('rev-parse', 'HEAD', TARGET_REF, 'MERGE_HEAD').splitlines(),
                         [head, head, self.work])
        self.assertEqual(self.git('symbolic-ref', 'HEAD'), TARGET_REF)

    def inspect(self, evidence, head, status, paths):
        report = self.inspect_pending(self.int_args('--inspect-merge', **evidence), status)
        self.assert_validation_pending(report)
        self.assertEqual(report['git_state']['unmerged_paths'], paths)
        self.assert_pending(head)

    def test_published_work_fast_forwards_only_the_target(self):
        self.integrate('--ff-only', self.review(self.base), 'local_fast_forward_verified')
        self.assertEqual(self.git('symbolic-ref', 'HEAD'), TARGET_REF)
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.work)
        self.assertEqual(self.git('status', '--short'), '')

    def test_clean_pending_merge_can_be_inspected_but_not_replaced(self):
        head = self.diverge()
        evidence = self.review(head)
        self.integrate('--prepare-merge', evidence, 'pending_commit')
        self.assert_pending(head)
        self.assertEqual(self.git('diff', '--cached', '--name-only').splitlines(),
                         ['prerequisite.txt', 'second.txt', 'tracked.txt'])
        self.inspect(evidence, head, 'awaiting_human_confirmation', [])
        self.integrate('--prepare-merge', evidence, 'blocked', 'Unfinished Git operation')
        self.integrate('--ff-only', evidence, 'blocked', 'Unfinished Git operation')

    def test_published_conflicts_remain_pending_through_partial_and_full_resolution(self):
        head = self.diverge(conflict=True)
        evidence = self.review(head)
        self.integrate('--prepare-merge', evidence, 'conflicted')
        self.inspect(evidence, head, 'conflicted', sorted(PATHS))
        for path, remaining in zip(PATHS, ([PATHS[1]], []), strict=True):
            self.assertIn(b'<<<<<<<', (self.root / path).read_bytes())
            (self.root / path).write_bytes(b'manually combined content\n')
            self.git('add', '--', path)
            status = 'conflicted' if remaining else 'awaiting_human_confirmation'
            self.inspect(evidence, head, status, remaining)

    def test_mutations_reject_invalid_review_and_drifted_tracking_tip(self):
        evidence = self.review(self.base)
        for key, value, reason in (
            ('expected_event_id', 'E-wrong', 'event_path'),
            ('expected_event_blob', self.work, 'event_blob_sha'),
            ('expected_head', self.first, 'stale'),
            ('expected_incoming', self.work, 'commit range'),
        ):
            for mode in ('--ff-only', '--prepare-merge'):
                with self.subTest(key=key, mode=mode):
                    self.integrate(mode, dict(evidence, **{key: value}), 'blocked', reason)
        # Simulate a newer source tip observed by fetch, with the same event blob.
        advanced = self.git('commit-tree', f'{self.tip}^{{tree}}', '-p', self.tip,
                            '-m', 'Publisher advanced after review')
        self.git('update-ref', TRACKING, advanced, self.tip)
        for mode in ('--ff-only', '--prepare-merge'):
            with self.subTest(mode=mode):
                self.integrate(mode, evidence, 'blocked', 'source_tip_sha')

    def test_git_failure_leaves_the_target_and_worktree_intact(self):
        head = self.diverge()
        evidence = self.review(head)
        self.git('config', 'merge.verifySignatures', 'true')
        report = self.integrate('--prepare-merge', evidence, 'write_unverified', 'signature')
        self.assertTrue(report['attempted'])
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)
        self.assertEqual(self.git('status', '--short'), '')
        self.assertFalse((self.root / '.git/MERGE_HEAD').exists())

    def test_interruption_after_git_merge_preserves_the_pending_state(self):
        head = self.diverge()
        args = self.int_args('--prepare-merge', **self.review(head))
        refs = group_int.all_refs(self.root)
        git = group_int._git

        def interrupt_after_merge(root, *arguments, **options):
            result = git(root, *arguments, **options)
            if 'merge' in arguments:
                self.assert_pending(head)
                raise KeyboardInterrupt
            return result

        with chdir(self.root), patch.object(group_int, '_git', side_effect=interrupt_after_merge):
            with self.assertRaises(KeyboardInterrupt):
                groupctl.main(args)
        self.assert_pending(head)
        self.assertEqual(group_int.all_refs(self.root), refs)
        self.assertEqual(self.git('diff', '--cached', '--name-only').splitlines(),
                         ['prerequisite.txt', 'second.txt', 'tracked.txt'])
        self.assertFalse((self.root / '.git/groupctl-int.lock').exists())
