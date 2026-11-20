"""Exercise event publication against disposable worktrees and bare remotes."""
import argparse
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

import group_pub_event_push as publication
from group_git import GitError
from groupctl import load_config
import test_pub_event
import test_pub_push
from test_pub_push import REF


class EventPushTests(unittest.TestCase):
    git = test_pub_push.PushTests.git
    commit = test_pub_push.PushTests.commit
    snapshot = test_pub_push.PushTests.snapshot
    remote_git = test_pub_push.PushTests.remote_git
    assert_tip = test_pub_push.PushTests.assert_tip
    hook = test_pub_push.PushTests.hook
    write_review = test_pub_event.EventCommitTests.write_review
    publish_work = test_pub_event.EventCommitTests.publish_work
    run_event = test_pub_event.EventCommitTests.run_event

    def setUp(self):
        test_pub_event.EventCommitTests.setUp(self)
        self.publish_work()
        event = self.run_event('local_event_committed')
        self.event_path = event['event_path']
        self.event_id = event['event_id']
        self.event_sha = event['commit']
        self.args.expected_head = self.event_sha
        self.args.expected_remote_head = self.head

    def push(self, result, **options):
        args = argparse.Namespace(**(vars(self.args) | options))
        before = self.snapshot()
        report = publication.push_event(self.root, load_config, args)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(report['result'], result, report)
        self.assertEqual(report['verified'], result in ('published_verified', 'already_published'))
        return report

    def replace_event(self, raw):
        (self.root / self.event_path).write_text(raw, encoding='utf-8')
        self.git('add', '--', self.event_path)
        self.git('commit', '--amend', '--no-edit')
        self.args.expected_head = self.git('rev-parse', 'HEAD')

    def test_cli_publishes_and_retry_verifies_without_touching_local_state(self):
        self.file.write_text('pending staged\n', encoding='utf-8')
        self.git('add', 'tracked.txt')
        self.file.write_text('pending unstaged\n', encoding='utf-8')
        (self.root / self.event_path).write_text('uncommitted local data', encoding='utf-8')
        command = [sys.executable, '-B', str(test_pub_push.test_pub.CLI), 'pub', '--push-event',
                   '--person-id', self.args.person_id, '--expected-branch', self.args.expected_branch,
                   '--expected-head', self.event_sha, '--work-sha', self.head, '--change-id',
                   self.args.change_id, '--remote', 'origin', '--expected-url', self.url,
                   '--expected-remote-head', self.head]
        before = self.snapshot()
        for extra, code in (([], 2), (['--confirm-reviewed'], 0)):
            execution = subprocess.run(command + extra, cwd=self.root, env=self.env,
                                       capture_output=True, text=True, timeout=90)
            self.assertEqual(execution.returncode, code, execution.stderr or execution.stdout)
            self.assertEqual(self.snapshot(), before)
        report = json.loads(execution.stdout)
        self.assertEqual(report['result'], 'published_verified')
        self.assertEqual(report['push_exit'], 0)
        self.assertEqual(report['event_id'], self.event_id)
        self.assert_tip(self.event_sha)
        self.assertEqual(self.remote_git('rev-parse', self.event_sha + '^'), self.head)
        self.assertEqual(self.remote_git('diff', '--name-status', self.head, self.event_sha),
                         f'A\t{self.event_path}')
        event = json.loads(self.remote_git('show', f'{self.event_sha}:{self.event_path}'))
        self.assertEqual((event['event_id'], event['change_id'], event['branch'], event['commit']),
                         (self.event_id, self.args.change_id, self.args.expected_branch, self.head))
        self.assertEqual(report['remote_blob_verified'],
                         self.remote_git('rev-parse', f'{self.event_sha}:{self.event_path}'))
        with patch.object(publication, 'send_update') as send:
            self.push('already_published')
            self.push('already_published', expected_remote_head=self.event_sha)
            send.assert_not_called()

    def test_remote_has_not_received_work_or_has_diverged(self):
        self.remote_git('update-ref', REF, self.base, self.head)
        self.push('not_executed')
        self.assert_tip(self.base)
        tree = self.git('rev-parse', self.base + '^{tree}')
        divergent = self.git('commit-tree', tree, '-p', self.base, '-m', 'other work')
        self.git('push', self.url, f'{divergent}:{REF}')
        self.push('not_executed')
        self.assert_tip(divergent)

    def test_wrong_parent(self):
        tree = self.git('rev-parse', 'HEAD^{tree}')
        wrong = self.git('commit-tree', tree, '-p', self.base, '-m', 'wrong parent')
        self.git('update-ref', REF, wrong, self.event_sha)
        self.assertIn('parent', self.push('not_executed', expected_head=wrong)['error'])
        self.assert_tip(self.head)

    def test_invalid_json(self):
        self.replace_event('{')
        self.assertIn('Invalid JSON', self.push('not_executed')['error'])
        self.assert_tip(self.head)

    def test_reviewed_files_must_match_the_actual_work_diff(self):
        event = json.loads((self.root / self.event_path).read_bytes())
        event['files_changed'] = ['different.txt']
        self.replace_event(json.dumps(event))
        with patch.object(publication, 'send_update') as send:
            report = self.push('not_executed')
            send.assert_not_called()
        self.assertIn('files_changed must exactly match', report['error'])
        self.assert_tip(self.head)

    def test_wrong_change_or_work_sha(self):
        self.assertIn('does not match', self.push('not_executed', change_id='C-other')['error'])
        event = json.loads((self.root / self.event_path).read_text(encoding='utf-8'))
        self.replace_event(json.dumps(event | {'commit': self.base}))
        self.assertIn('does not match', self.push('not_executed')['error'])
        self.assert_tip(self.head)

    def test_extra_commit_content_is_refused(self):
        self.file.write_text('unexpected change', encoding='utf-8')
        self.git('add', 'tracked.txt')
        self.git('commit', '--amend', '--no-edit')
        self.push('not_executed', expected_head=self.git('rev-parse', 'HEAD'))
        self.assert_tip(self.head)

    def test_unreviewed_or_wrong_destination(self):
        for options in (dict(confirm_reviewed=False), dict(person_id='P02'),
                        dict(expected_branch='people/P01/other'),
                        dict(expected_url=(self.remote.parent / 'wrong.git').as_posix()),
                        dict(expected_remote_head=self.event_sha)):
            with self.subTest(options=options):
                self.push('not_executed', **options)
        self.assert_tip(self.head)

    def test_canonical_branch_is_protected(self):
        self.git('branch', 'published-fixture', self.event_sha)
        self.git('switch', 'main')
        self.git('merge', '--ff-only', 'published-fixture')
        self.assertIn('canonical', self.push('not_executed', expected_branch='main')['error'])
        self.assert_tip(self.head)

    def test_unfinished_operation_is_refused(self):
        (self.root / '.git/MERGE_HEAD').write_text(self.base, encoding='ascii')
        self.assertIn('Unfinished Git operation', self.push('not_executed')['error'])
        self.assert_tip(self.head)

    def test_duplicate_visible_on_another_remote_branch(self):
        original = (self.root / self.event_path).read_text(encoding='utf-8')
        event = json.loads(original)
        for index, fields in enumerate(({'commit': self.base}, {'change_id': 'C-other'})):
            with self.subTest(fields=fields):
                self.git('switch', '-c', f'duplicate-{index}', self.head)
                path = self.root / '.group/events/E-other.json'
                path.parent.mkdir(exist_ok=True)
                path.write_text(json.dumps(event | fields | {'event_id': 'E-other'}), encoding='utf-8')
                duplicate = self.commit('duplicate')
                ref = f'refs/heads/people/P01/another-{index}'
                self.git('push', self.url, f'{duplicate}:{ref}')
                self.git('switch', self.args.expected_branch)
                self.assertIn('Remote event already uses', self.push('not_executed')['error'])
                self.assert_tip(self.head)
                self.remote_git('update-ref', '-d', ref)

    def test_receive_advertisement_race_refuses_even_older_ancestor(self):
        send = publication.send_update

        def race(*args):
            self.remote_git('update-ref', REF, self.base, self.head)
            return send(*args)

        with patch.object(publication, 'send_update', side_effect=race):
            self.assertIn('advertisement', self.push('push_rejected')['error'])
        self.assert_tip(self.base)

    def test_server_compare_and_swap_detects_race_after_advertisement(self):
        self.hook('pre-receive', 'unset GIT_QUARANTINE_PATH GIT_OBJECT_DIRECTORY '
                  f'GIT_ALTERNATE_OBJECT_DIRECTORIES\ngit update-ref {REF} {self.base} {self.head}')
        self.push('push_rejected')
        self.assert_tip(self.base)

    def test_server_rejects_push(self):
        self.hook('pre-receive', 'exit 1')
        self.push('push_rejected')
        self.assert_tip(self.head)

    def test_remote_symbolic_personal_ref_cannot_update_canonical(self):
        self.remote_git('update-ref', 'refs/heads/main', self.head, self.base)
        self.remote_git('symbolic-ref', REF, 'refs/heads/main')
        self.push('not_executed')
        self.assertEqual(self.remote_git('rev-parse', 'refs/heads/main'), self.head)

    def test_uncertain_push_with_no_observed_event_is_partial(self):
        def lose_response(root, args, ref, old, report):
            report['result'] = 'verification_failed'
            raise GitError('Injected transport failure before acknowledgement.')

        with patch.object(publication, 'send_update', side_effect=lose_response):
            self.assertEqual(self.push('partial')['remote_head_after'], self.head)
        self.assert_tip(self.head)

    def test_branch_moves_after_successful_push(self):
        self.hook('post-receive', f'git update-ref {REF} {self.head} {self.event_sha}')
        report = self.push('verification_failed')
        self.assertEqual(report['push_exit'], 0)
        self.assertEqual(report['remote_head_after'], self.head)
        self.assert_tip(self.head)

    def test_query_failure_after_success_then_retry(self):
        query = publication.remote_head

        def fail_if_pushed(*args):
            if self.remote_git('rev-parse', REF) == self.event_sha:
                raise GitError('Injected live query failure.')
            return query(*args)

        with patch.object(publication, 'remote_head', side_effect=fail_if_pushed):
            self.assertEqual(self.push('verification_failed')['push_exit'], 0)
        self.assert_tip(self.event_sha)
        self.push('already_published')

    def test_fetched_blob_content_mismatch_cannot_verify_push_or_retry(self):
        read = publication.read_event_blob

        def mismatch(repository, blob):
            raw, event = read(repository, blob)
            if 'groupctl-event-verify-' in repository.name:
                # Simulate altered bytes reaching verification with the same parsed JSON.
                return raw + b'\n', event
            return raw, event

        with patch.object(publication, 'read_event_blob', side_effect=mismatch):
            report = self.push('verification_failed')
            self.assertEqual(report['push_exit'], 0)
            self.assertIn('differs', report['error'])
            self.assertNotIn('push_exit', self.push('verification_failed'))
        self.assert_tip(self.event_sha)
