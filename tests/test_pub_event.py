"""Local event commit tests using disposable worktrees and bare remotes."""
import argparse
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

from group_events import EventError, load_event
from group_git import GitError
from group_pub_event import event_commit
from groupctl import load_config
import test_pub_push


class EventCommitTests(unittest.TestCase):
    git = test_pub_push.PushTests.git
    commit = test_pub_push.PushTests.commit
    snapshot = test_pub_push.PushTests.snapshot
    remote_git = test_pub_push.PushTests.remote_git
    assert_tip = test_pub_push.PushTests.assert_tip

    def setUp(self):
        test_pub_push.PushTests.setUp(self)
        self.review = dict(summary='Update the tracked fixture text.', files_changed=['tracked.txt'],
                           interface_changes=[], tests_run=[], known_issues=[],
                           proposed_for_integration=False)
        self.review_file = self.root / 'review.json'
        self.write_review()
        self.args.work_sha = self.head
        self.args.change_id = 'C-fixture'
        self.args.review_file = self.review_file

    def write_review(self):
        self.review_file.write_text(json.dumps(self.review), encoding='utf-8')

    def publish_work(self):
        self.git('push', self.url, f'{self.head}:{test_pub_push.REF}')

    def run_event(self, result, **options):
        args = argparse.Namespace(**(vars(self.args) | options))
        report = event_commit(self.root, load_config, args)
        self.assertEqual(report['result'], result, report)
        self.assertEqual(report['publication'], 'unpublished')
        return report

    def refuse(self, **options):
        before = self.snapshot()
        report = self.run_event('event_validation_failed', **options)
        self.assertEqual(self.snapshot(), before)
        return report

    def test_cli_creates_exact_event_commit_and_preserves_unselected_files(self):
        self.publish_work()
        self.file.write_bytes(b'pending unstaged\n')
        command = [sys.executable, '-B', str(test_pub_push.test_pub.CLI),
                   'pub', '--event-commit', '--person-id', self.args.person_id,
                   '--expected-branch', self.args.expected_branch, '--expected-head', self.head,
                   '--work-sha', self.head, '--change-id', self.args.change_id,
                   '--review-file', str(self.review_file), '--remote', 'origin',
                   '--expected-url', self.url]
        execution = subprocess.run(command, cwd=self.root, capture_output=True, text=True, timeout=60)
        self.assertEqual(execution.returncode, 2)
        self.assertFalse((self.root / '.group/events').exists())
        execution = subprocess.run(command + ['--confirm-reviewed'], cwd=self.root,
                                   capture_output=True, text=True, timeout=60)
        self.assertEqual(execution.returncode, 0, execution.stderr or execution.stdout)
        report = json.loads(execution.stdout)
        self.assertEqual(report['result'], 'local_event_committed')
        self.assertEqual(report['remote_work_verified'], self.head)
        self.assertEqual(report['publication'], 'unpublished')
        commit = report['commit']
        self.assertNotEqual(commit, self.head)
        self.assertEqual(self.git('rev-parse', 'HEAD', 'HEAD^', 'HEAD^{tree}').splitlines(),
                         [commit, self.head, report['tree']])
        path = report['event_path']
        self.assertEqual(self.git('diff', '--name-status', self.head, commit), f'A\t{path}')
        event = load_event(self.root / path)
        self.assertEqual(json.loads(self.git('show', f'{commit}:{path}')), event)
        self.assertEqual(event, dict(self.review, protocol_version='0.1', group_id='G-test',
                                     event_type='change_published', person_id='P01',
                                     event_id=report['event_id'], created_at=event['created_at'],
                                     related_events=[], change_id='C-fixture', commit=self.head,
                                     branch=self.args.expected_branch))
        self.assertRegex(event['event_id'], r'^E-\d{8}T\d{6}Z-P01-[0-9a-f]{32}$')
        self.assertEqual(path, f'.group/events/{event["event_id"]}.json')
        self.assertEqual(self.git('status', '--short').splitlines(),
                         ['M tracked.txt', '?? review.json'])
        self.assertEqual(self.file.read_bytes(), b'pending unstaged\n')
        self.assertEqual(json.loads(self.review_file.read_text()), self.review)
        self.assert_tip(self.head)
        self.refuse()

    def test_stale_remote_tracking_ref_does_not_prove_publication(self):
        self.git('update-ref', 'refs/remotes/origin/' + self.args.expected_branch, self.head)
        self.assertIn('live remote', self.refuse()['error'])
        self.assert_tip(self.base)

    def test_rejects_identity_branch_and_sha_mismatches(self):
        self.publish_work()
        with patch('group_pub_event.remote_head') as remote:
            for overrides in (dict(person_id='unknown'), dict(expected_branch='people/P01/wrong'),
                              dict(work_sha=self.base), dict(expected_head=self.base),
                              dict(work_sha=self.base, expected_head=self.base),
                              dict(work_sha=self.head[:7], expected_head=self.head[:7])):
                with self.subTest(overrides=overrides):
                    self.refuse(**overrides)
            remote.assert_not_called()

    def test_rejects_unreviewed_file_list_and_invalid_payload(self):
        self.publish_work()
        self.review['files_changed'] = ['invented.txt']
        self.write_review()
        self.assertIn('files_changed', self.refuse()['error'])
        self.review['files_changed'] = ['tracked.txt']
        self.review['proposed_for_integration'] = 'false'
        self.write_review()
        self.assertIn('proposed_for_integration', self.refuse()['error'])

    def test_rejects_unrelated_staged_file(self):
        self.publish_work()
        self.git('add', '--', 'review.json')
        self.assertIn('staged', self.refuse()['error'])

    def test_rejects_file_blocking_event_directory(self):
        self.publish_work()
        (self.root / '.group/events').write_bytes(b'leave this alone')
        self.assertIn('Unsafe event namespace', self.refuse()['error'])

    def test_refuses_duplicate_on_another_known_branch(self):
        self.publish_work()
        self.run_event('local_event_committed')
        self.git('branch', 'people/P01/earlier-event')
        self.git('switch', '-c', 'people/P01/new-work', self.head)
        self.file.write_bytes(b'new change\n')
        # The review remains outside the reviewed work diff.
        self.git('commit', '-am', 'new work')
        head = self.git('rev-parse', 'HEAD')
        branch = 'people/P01/new-work'
        self.git('push', self.url, f'{head}:refs/heads/{branch}')
        self.assertIn('already has a publication event',
                      self.refuse(work_sha=head, expected_head=head, expected_branch=branch)['error'])

    def test_distinguishes_write_failure(self):
        self.publish_work()
        before = self.snapshot()
        with patch('group_pub_event.write_event', side_effect=EventError('injected write failure')):
            report = self.run_event('event_creation_failed')
        self.assertIn('injected write failure', report['error'])
        self.assertEqual(self.snapshot(), before)

    def test_commit_failure_preserves_event_and_blocks_retry(self):
        self.publish_work()
        from group_pub_commit import _git

        def fail_commit(root, *args, **options):
            if 'commit-tree' in args:
                raise GitError('injected commit-tree failure')
            return _git(root, *args, **options)

        with patch('group_pub_commit._git', side_effect=fail_commit):
            report = self.run_event('partial')
        self.assertEqual(report['failure'], 'commit_failed')
        self.assertIn('injected commit-tree failure', report['error'])
        self.assertEqual(report['phase'], 'creating_object')
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.head)
        self.assertEqual(load_event(self.root / report['event_path'])['commit'], self.head)
        self.assertIn('already has a publication event', self.refuse()['error'])
        self.assertIn('already has a publication event', self.refuse(change_id='C-other')['error'])
        self.assert_tip(self.head)

    def test_index_install_failure_preserves_local_event_commit(self):
        self.publish_work()
        with patch('group_pub_commit.os.replace', side_effect=OSError('injected index install failure')):
            report = self.run_event('partial')
        self.assertEqual(report['phase'], 'ref_updated')
        self.assertEqual(report['failure'], 'post_ref_update_failed')
        self.assertIn('injected index install failure', report['error'])
        self.assertEqual(self.git('rev-parse', 'HEAD', 'HEAD^').splitlines(),
                         [report['commit'], self.head])
        self.assertEqual(self.git('diff', '--cached', '--name-status'),
                         f'D\t{report["event_path"]}')
        self.assertTrue((self.root / report['event_path']).is_file())
        self.refuse()
        self.assert_tip(self.head)
