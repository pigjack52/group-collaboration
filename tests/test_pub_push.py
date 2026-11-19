"""Exercise PUB pushes only against disposable local bare repositories."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import group_pub_push
from group_git import GitError
from groupctl import load_config
import test_pub


REF = 'refs/heads/' + test_pub.BRANCH


class PushTests(unittest.TestCase):
    git = test_pub.PubTests.git
    commit = test_pub.PubTests.commit
    snapshot = test_pub.PubTests.snapshot

    def setUp(self):
        test_pub.PubTests.setUp(self)
        self.remote = Path(self.enterContext(TemporaryDirectory(prefix='pub remote '))) / 'shared.git'
        self.url = self.remote.as_posix()
        self.git('init', '--bare', self.url)
        self.git('remote', 'add', 'origin', self.url)
        self.git('push', self.url, f'{self.base}:{REF}', f'{self.base}:refs/heads/main')
        self.file.write_text('reviewed work\n', encoding='utf-8')
        self.head = self.commit('reviewed work')
        self.args = argparse.Namespace(person_id='P01', expected_branch=test_pub.BRANCH,
                                       expected_head=self.head, expected_remote_head=self.base,
                                       remote='origin', expected_url=self.url, confirm_reviewed=True)

    def remote_git(self, *args):
        return self.git('--git-dir', self.url, *args)

    def assert_tip(self, head):
        self.assertEqual(self.remote_git('rev-parse', REF), head)
        self.assertEqual(self.remote_git('rev-parse', 'refs/heads/main'), self.base)

    def run_push(self, result, **options):
        before = self.snapshot()
        args = argparse.Namespace(**(vars(self.args) | options))
        report = group_pub_push.push_work(self.root, load_config, args)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(report['result'], result, report)
        self.assertEqual(report['verified'], result in ('verified', 'already_present'), report)
        return report

    def cli(self, *extra, expected=0):
        before = self.snapshot()
        command = [sys.executable, '-B', str(test_pub.CLI), 'pub', '--push-work',
                   '--person-id', 'P01', '--expected-branch', test_pub.BRANCH,
                   '--expected-head', self.head, '--remote', 'origin', '--expected-url', self.url,
                   '--expected-remote-head', self.base, *extra]
        execution = subprocess.run(command, cwd=self.root / '.group', env=self.env,
                                   capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(execution.returncode, expected, execution.stderr or execution.stdout)
        self.assertEqual(self.snapshot(), before)
        return json.loads(execution.stdout) if execution.stdout else None

    def hook(self, name, script):
        hook = self.remote / 'hooks' / name
        hook.write_bytes(('#!/bin/sh\n' + script + '\n').encode('ascii'))
        hook.chmod(0o755)

    def test_cli_push_verification_and_retry_preserve_unselected_content(self):
        self.git('tag', '-a', 'local-tag', '-m', 'not selected')
        self.git('config', 'push.followTags', 'true')
        self.git('config', 'remote.origin.mirror', 'true')
        self.file.write_text('pending staged\n', encoding='utf-8')
        self.git('add', 'tracked.txt')
        self.file.write_text('pending unstaged\n', encoding='utf-8')
        (self.root / 'private.txt').write_text('not selected\n', encoding='utf-8')
        self.cli(expected=2)
        pushed = self.cli('--confirm-reviewed')
        self.assertEqual(pushed['result'], 'verified')
        self.assertEqual(pushed['remote_head_before'], self.base)
        self.assertEqual(pushed['remote_head_after'], self.head)
        self.assertEqual(pushed['remote_head_verified'], self.head)
        self.assert_tip(self.head)
        self.assertEqual(self.remote_git('tag', '--list'), '')
        self.hook('pre-receive', 'exit 1')
        retry = self.cli('--confirm-reviewed')
        self.assertEqual(retry['result'], 'already_present')
        self.assertEqual(retry['remote_head_verified'], self.head)
        self.assertNotIn('push_exit', retry)

    def test_create_absent_personal_branch(self):
        self.remote_git('update-ref', '-d', REF)
        self.run_push('verified', expected_remote_head='absent')
        self.assert_tip(self.head)

    def test_wrong_identity_branch_or_sha_never_contacts_remote(self):
        with patch.object(group_pub_push, 'remote_head') as query:
            for options in (dict(person_id='P02'), dict(expected_branch='people/P01/wrong'),
                            dict(expected_head=self.base), dict(expected_head=self.head[:7])):
                with self.subTest(options=options):
                    self.run_push('not_executed', **options)
            query.assert_not_called()
        self.assert_tip(self.base)

    def test_canonical_branch_protection_even_with_personal_branch_name(self):
        config = self.root / '.group/group.json'
        settings = json.loads(config.read_text(encoding='utf-8'))
        settings['canonical_branch'] = test_pub.BRANCH
        config.write_text(json.dumps(settings), encoding='utf-8')
        canonical = self.commit('configure canonical ref')
        report = self.run_push('not_executed', expected_head=canonical)
        self.assertIn('canonical', report['error'])
        self.assert_tip(self.base)

    def test_unfinished_operation(self):
        (self.root / '.git/MERGE_HEAD').write_text(self.base + '\n', encoding='ascii')
        report = self.run_push('not_executed')
        self.assertIn('Unfinished Git operation', report['error'])
        self.assert_tip(self.base)

    def test_live_tip_differs_from_reviewed_tip(self):
        report = self.run_push('not_executed', expected_remote_head='absent')
        self.assertIn('differs', report['error'])
        self.assert_tip(self.base)

    def test_remote_history_diverged(self):
        tree = self.git('rev-parse', self.base + '^{tree}')
        divergent = self.git('commit-tree', tree, '-p', self.base, '-m', 'other writer')
        self.git('push', self.url, f'{divergent}:{REF}')
        report = self.run_push('not_executed', expected_remote_head=divergent)
        self.assertIn('not an ancestor', report['error'])
        self.assert_tip(divergent)

    def test_remote_changes_before_push_advertisement_to_older_ancestor(self):
        older = self.git('rev-parse', self.base + '^')
        send = group_pub_push.send_update

        def change_then_send(*args):
            self.remote_git('update-ref', REF, older, self.base)
            return send(*args)

        with patch.object(group_pub_push, 'send_update', side_effect=change_then_send):
            self.assertIn('advertisement', self.run_push('push_rejected')['error'])
        self.assert_tip(older)

    def test_remote_changes_after_advertisement(self):
        older = self.git('rev-parse', self.base + '^')
        self.hook('pre-receive', 'unset GIT_QUARANTINE_PATH GIT_OBJECT_DIRECTORY '
                  f'GIT_ALTERNATE_OBJECT_DIRECTORIES\ngit update-ref {REF} {older} {self.base}')
        self.run_push('push_rejected')
        self.assert_tip(older)

    def test_server_refuses_push(self):
        self.hook('pre-receive', 'exit 1')
        self.run_push('push_rejected')
        self.assert_tip(self.base)

    def test_push_succeeds_but_query_fails(self):
        query = group_pub_push.remote_head

        def fail_after_push(*args):
            if self.remote_git('rev-parse', REF) == self.head:
                raise GitError('Live remote branch query failed.')
            return query(*args)

        with patch.object(group_pub_push, 'remote_head', side_effect=fail_after_push):
            self.assertEqual(self.run_push('verification_failed')['push_exit'], 0)
        self.assert_tip(self.head)
        self.run_push('already_present')

    def test_push_succeeds_but_post_query_observes_another_commit(self):
        self.hook('post-receive', f'git update-ref {REF} {self.base} {self.head}')
        report = self.run_push('verification_failed')
        self.assertEqual(report['push_exit'], 0)
        self.assertEqual(report['remote_head_after'], self.base)
        self.assert_tip(self.base)

    def test_missing_remote_and_unreachable_destination(self):
        self.run_push('not_executed', remote='missing')
        missing = (self.remote.parent / 'missing.git').as_posix()
        self.git('remote', 'set-url', 'origin', missing)
        report = self.run_push('not_executed', expected_url=missing)
        self.assertIn('query failed', report['error'])
        self.assert_tip(self.base)

    def test_mismatched_push_url(self):
        self.git('remote', 'set-url', '--push', 'origin',
                 (self.remote.parent / 'other.git').as_posix())
        self.assertIn('one reviewed URL', self.run_push('not_executed')['error'])
        self.assert_tip(self.base)

    def test_symbolic_personal_remote_ref_cannot_update_canonical(self):
        self.remote_git('symbolic-ref', REF, 'refs/heads/main')
        self.run_push('not_executed')
        self.assert_tip(self.base)
