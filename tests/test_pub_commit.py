"""Work commit coverage. Every Git write uses a TemporaryDirectory fixture."""
import argparse
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

import group_pub_commit
from groupctl import load_config
import test_pub


class WorkCommitTests(unittest.TestCase):
    setUp = test_pub.PubTests.setUp
    git = test_pub.PubTests.git
    commit = test_pub.PubTests.commit
    snapshot = test_pub.PubTests.snapshot
    cli = test_pub.PubTests.cli

    def run_pub(self, *options, person='P01', head=None, branch=None, expected=0):
        result = subprocess.run(
            [sys.executable, '-B', str(test_pub.CLI), 'pub', '--person-id', person,
             '--expected-head', head or self.base, '--expected-branch', branch or test_pub.BRANCH,
             '--confirm-reviewed', *options],
            cwd=self.root / '.group', env=self.env, capture_output=True,
            text=True, encoding='utf-8', timeout=30,
        )
        self.assertEqual(result.returncode, expected, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def create(self, *files, expected=0, **options):
        arguments = [flag for file in files for flag in ('--file', file)]
        return self.run_pub('--work-commit', '--message', 'Reviewed work', *arguments,
                            expected=expected, **options)

    def test_selected_files_and_unselected_work_survive(self):
        self.file.write_text('keep editing\n', encoding='utf-8')
        for name in ('new[1].txt', 'new1.txt', 'private.txt'):
            (self.root / name).write_text(name + '\n', encoding='utf-8')
        report = self.create('new[1].txt')
        self.assertEqual(report['result'], 'created')
        self.assertEqual(report['publication'], 'unpublished')
        self.assertEqual(report['commit'], self.git('rev-parse', 'HEAD'))
        self.assertEqual(report['parents'], [self.base])
        self.assertEqual(report['files'], ['new[1].txt'])
        self.assertEqual(self.git('diff', '--name-only', self.base, 'HEAD'), 'new[1].txt')
        self.assertEqual(self.git('show', 'HEAD:new[1].txt'), 'new[1].txt')
        self.assertEqual(self.git('log', '-1', '--format=%s'), 'Reviewed work')
        self.assertEqual(self.file.read_text(), 'keep editing\n')
        self.assertEqual((self.root / 'private.txt').read_text(), 'private.txt\n')
        self.assertEqual(report['git_status']['unstaged'], ['tracked.txt'])
        self.assertEqual(report['git_status']['untracked'], ['new1.txt', 'private.txt'])
        self.assertEqual(self.git('rev-parse', 'main'), self.base)

    def test_selected_staged_edit_and_unstaged_deletion(self):
        (self.root / 'edit.txt').write_text('base\n', encoding='utf-8')
        head = self.commit('second fixture file')
        (self.root / 'edit.txt').write_text('staged\n', encoding='utf-8')
        self.git('add', 'edit.txt')
        self.file.unlink()
        report = self.create('edit.txt', 'tracked.txt', head=head)
        self.assertEqual(report['files'], ['edit.txt', 'tracked.txt'])
        self.assertEqual(self.git('diff', '--name-status', head, 'HEAD'), 'M\tedit.txt\nD\ttracked.txt')
        self.assertEqual(self.git('status', '--short'), '')

    def test_unselected_staged_file_refuses_without_writes(self):
        (self.root / 'staged.txt').write_text('staged\n', encoding='utf-8')
        self.git('add', 'staged.txt')
        self.file.write_text('work\n', encoding='utf-8')
        before = self.snapshot()
        report = self.create('tracked.txt', expected=1)
        self.assertEqual(report['result'], 'refused')
        self.assertIn('Unselected staged paths', report['error'])
        self.assertEqual(self.snapshot(), before)

    def test_stale_head_and_retry_use_existing(self):
        self.file.write_text('work\n', encoding='utf-8')
        created = self.create('tracked.txt')
        before = self.snapshot()
        self.assertIn('differs', self.create('tracked.txt', expected=1)['error'])
        existing = self.run_pub('--use-existing', head=created['commit'])
        self.assertEqual(existing['result'], 'existing')
        self.assertEqual(existing['commit'], created['commit'])
        self.assertEqual(existing['files'], ['tracked.txt'])
        self.assertEqual(self.snapshot(), before)

    def test_stale_preflight_refuses_after_another_commit(self):
        self.assertEqual(test_pub.PubTests.report(self)['facts']['head'], self.base)
        self.file.write_text('other work\n', encoding='utf-8')
        self.commit('other work')
        self.file.write_text('new work\n', encoding='utf-8')
        self.assertIn('differs', self.create('tracked.txt', expected=1)['error'])

    def test_wrong_person_or_canonical_refuses(self):
        self.file.write_text('work\n', encoding='utf-8')
        self.assertIn('owner differs', self.create('tracked.txt', person='P02', expected=1)['error'])
        self.git('switch', 'main')
        self.assertIn('canonical', self.create('tracked.txt', branch='main', expected=1)['error'])

    def test_local_group_metadata_cannot_override_committed_canonical(self):
        config = self.root / '.group/group.json'
        settings = json.loads(config.read_text())
        settings['canonical_branch'] = test_pub.BRANCH
        config.write_text(json.dumps(settings), encoding='utf-8')
        head = self.commit('canonical is a personal-shaped ref')
        settings['canonical_branch'] = 'main'
        config.write_text(json.dumps(settings), encoding='utf-8')
        self.file.write_text('work\n', encoding='utf-8')
        before = self.snapshot()
        self.assertIn('metadata differs', self.create('tracked.txt', head=head, expected=1)['error'])
        self.assertEqual(self.snapshot(), before)

    def test_conflict_and_unfinished_merge_refuse(self):
        self.git('switch', 'main')
        self.file.write_text('main\n', encoding='utf-8')
        self.commit('main work')
        self.git('switch', test_pub.BRANCH)
        self.file.write_text('personal\n', encoding='utf-8')
        head = self.commit('personal work')
        merge = subprocess.run(['git', 'merge', 'main'], cwd=self.root, env=self.env,
                               capture_output=True, timeout=30)
        self.assertEqual(merge.returncode, 1)
        before = self.snapshot()
        error = self.create('tracked.txt', head=head, expected=1)['error']
        self.assertIn('Unresolved Git conflicts', error)
        self.assertIn('Unfinished Git operation', error)
        self.assertEqual(self.snapshot(), before)

    def test_commit_object_failure_preserves_real_index_and_files(self):
        self.file.write_text('work\n', encoding='utf-8')
        self.git('config', '--unset', 'user.name')
        self.git('config', '--unset', 'user.email')
        self.git('config', 'user.useConfigOnly', 'true')
        index = self.root / '.git/index'
        before = index.read_bytes()
        report = self.create('tracked.txt', expected=1)
        self.assertEqual((report['result'], report['phase']), ('partial', 'creating_object'))
        self.assertEqual(report['git_status']['head'], self.base)
        self.assertEqual(index.read_bytes(), before)
        self.assertEqual(self.file.read_text(), 'work\n')
        self.assertFalse(index.with_suffix('.lock').exists())

    def test_active_hook_refuses_before_writes(self):
        hooks = self.root / 'custom-hooks'
        hooks.mkdir()
        self.git('config', 'core.hooksPath', 'custom-hooks')
        hook = hooks / 'pre-commit'
        hook.write_text('#!/bin/sh\nexit 1\n', encoding='utf-8')
        hook.chmod(0o755)
        self.file.write_text('work\n', encoding='utf-8')
        before = self.snapshot()
        self.assertIn('Active Git hooks', self.create('tracked.txt', expected=1)['error'])
        self.assertEqual(self.snapshot(), before)

    def test_lock_and_mixed_selection_refuse_without_writes(self):
        self.file.write_text('staged\n', encoding='utf-8')
        self.git('add', 'tracked.txt')
        self.file.write_text('more work\n', encoding='utf-8')
        before = self.snapshot()
        self.assertIn('both staged', self.create('tracked.txt', expected=1)['error'])
        self.assertEqual(self.snapshot(), before)
        (self.root / '.git/index.lock').write_bytes(b'other writer')
        before = self.snapshot()
        self.assertIn('index lock', self.create('tracked.txt', expected=1)['error'])
        self.assertEqual(self.snapshot(), before)

    def test_work_commit_requires_review_confirmation(self):
        self.file.write_text('work\n', encoding='utf-8')
        result = self.cli('--work-commit', '--person-id', 'P01', '--expected-head', self.base,
                          '--expected-branch', test_pub.BRANCH, '--file', 'tracked.txt',
                          '--message', 'Reviewed work', expected=2)
        self.assertIn('--confirm-reviewed', result.stderr)

    def test_compare_and_swap_preserves_concurrent_branch_tip(self):
        self.file.write_text('work\n', encoding='utf-8')
        other_head = self.git('rev-parse', self.base + '^')
        args = argparse.Namespace(expected_head=self.base, expected_branch=test_pub.BRANCH,
                                  person_id='P01', use_existing=False, work_commit=True,
                                  file=['tracked.txt'], message='Reviewed work')
        original = group_pub_commit._git

        def move_branch(root, *command, **kwargs):
            if 'update-ref' in command:
                self.git('update-ref', 'refs/heads/' + test_pub.BRANCH, other_head, self.base)
            return original(root, *command, **kwargs)

        index = self.root / '.git/index'
        before = index.read_bytes()
        with patch.object(group_pub_commit, '_git', side_effect=move_branch):
            report = group_pub_commit.work_commit(self.root, load_config, args)
        self.assertEqual((report['result'], report['phase']), ('partial', 'updating_ref'))
        self.assertEqual(self.git('rev-parse', 'HEAD'), other_head)
        self.assertEqual(index.read_bytes(), before)

    def test_symbolic_head_race_detected_before_index_installation(self):
        self.file.write_text('work\n', encoding='utf-8')
        args = argparse.Namespace(expected_head=self.base, expected_branch=test_pub.BRANCH,
                                  person_id='P01', use_existing=False, work_commit=True,
                                  file=['tracked.txt'], message='Reviewed work')
        original = group_pub_commit._git

        def move_head(root, *command, **kwargs):
            if 'update-ref' in command:
                self.git('symbolic-ref', 'HEAD', 'refs/heads/main')
            return original(root, *command, **kwargs)

        index = self.root / '.git/index'
        before = index.read_bytes()
        with patch.object(group_pub_commit, '_git', side_effect=move_head):
            report = group_pub_commit.work_commit(self.root, load_config, args)
        self.assertEqual((report['result'], report['phase']), ('partial', 'ref_updated'))
        self.assertEqual(self.git('rev-parse', test_pub.BRANCH), report['commit'])
        self.assertEqual(self.git('rev-parse', 'main'), self.base)
        self.assertEqual(index.read_bytes(), before)
