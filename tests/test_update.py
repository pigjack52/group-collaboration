"""Real update operations in disposable clones and a local bare remote."""
import subprocess
import sys
import unittest

import test_sync

CLI = test_sync.CLI


class UpdateTests(unittest.TestCase):
    setUp = test_sync.SyncTests.setUp
    git = test_sync.SyncTests.git
    identity = test_sync.SyncTests.identity
    commit = test_sync.SyncTests.commit

    def incoming(self, path='incoming.txt', content='canonical\n'):
        self.git(self.b, 'checkout', 'main')
        (self.b / path).write_text(content, encoding='utf-8', newline='\n')
        tip = self.commit(self.b)
        self.git(self.b, 'push', 'origin', 'main')
        return tip

    def update(self, expected):
        result = subprocess.run([sys.executable, '-B', str(CLI), 'sync', '--update'],
                                cwd=self.a, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        return result.stdout

    def suite(self, failing=False):
        (self.b / 'tests').mkdir()
        (self.b / 'tests/test_quick.py').write_text(
            'import unittest\nclass Quick(unittest.TestCase):\n'
            ' def test_behavior(self): self.assertEqual(1, ' + ('2' if failing else '1') + ')\n', encoding='utf-8', newline='\n')

    def test_fast_forward_and_repeat(self):
        self.suite()
        tip = self.incoming()
        output = self.update(0)
        self.assertIn('Tests: pass', output)
        self.git(self.a, 'merge-base', '--is-ancestor', tip, 'HEAD')
        self.assertEqual(self.git(self.a, 'rev-parse', 'HEAD'), tip)
        self.assertIn('No update needed', self.update(0))
        self.assertEqual(self.git(self.a, 'rev-parse', 'HEAD'), tip)
        self.assertFalse((self.a / '.git/MERGE_HEAD').exists())

    def test_dirty_categories_preserved(self):
        self.incoming()
        for kind in ('untracked', 'unstaged', 'staged'):
            with self.subTest(kind=kind):
                path = self.a / ('new.txt' if kind == 'untracked' else 'work.txt')
                path.write_text(kind, encoding='utf-8')
                if kind == 'staged':
                    self.git(self.a, 'add', 'work.txt')
                before = self.git(self.a, 'status', '--porcelain')
                self.assertIn('Dirty', self.update(1))
                self.assertEqual(self.git(self.a, 'status', '--porcelain'), before)
                self.assertEqual(path.read_text(), kind)
                if kind == 'untracked':
                    path.unlink()

    def test_diverged_nonconflicting_pending_no_commit(self):
        (self.a / 'personal.txt').write_text('mine', encoding='utf-8')
        head = self.commit(self.a)
        self.suite()
        tip = self.incoming()
        output = self.update(1)
        self.assertIn('Tests: pass', output)
        self.assertIn('canonical incorporated: False', output)
        self.assertEqual(self.git(self.a, 'rev-parse', 'HEAD'), head)
        self.assertEqual(self.git(self.a, 'rev-parse', 'MERGE_HEAD'), tip)
        self.assertEqual((self.a / 'personal.txt').read_text(), 'mine')
        self.assertTrue((self.a / 'incoming.txt').exists())
        self.git(self.a, 'merge', '--abort')
        self.assertEqual(self.git(self.a, 'status', '--porcelain'), '')

    def test_conflict_and_abort(self):
        (self.a / 'work.txt').write_text('personal\n', encoding='utf-8')
        head = self.commit(self.a)
        self.incoming('work.txt', 'remote\n')
        output = self.update(1)
        self.assertIn('Conflicts:\nwork.txt', output)
        self.assertIn('git merge --abort', output)
        self.assertEqual(self.git(self.a, 'rev-parse', 'HEAD'), head)
        self.assertIn('<<<<<<<', (self.a / 'work.txt').read_text())
        self.git(self.a, 'merge', '--abort')
        self.assertEqual((self.a / 'work.txt').read_text(), 'personal\n')

    def test_failed_tests_not_success(self):
        self.suite(True)
        tip = self.incoming()
        output = self.update(1)
        self.assertIn('Tests: fail', output)
        self.assertNotIn('Git update and quick validation passed', output)
        self.git(self.a, 'merge-base', '--is-ancestor', tip, 'HEAD')

    def test_missing_validation(self):
        self.incoming()
        self.assertIn('Tests: not_available', self.update(1))

    def test_no_origin(self):
        self.git(self.a, 'remote', 'remove', 'origin')
        self.assertIn('No origin', self.update(1))

    def test_fetch_failure(self):
        self.git(self.a, 'remote', 'set-url', 'origin', str(self.base / 'absent.git'))
        head = self.git(self.a, 'rev-parse', 'HEAD')
        self.assertIn('failed', self.update(1))
        self.assertEqual(self.git(self.a, 'rev-parse', 'HEAD'), head)

    def test_missing_remote_canonical(self):
        self.git(self.b, 'push', 'origin', '--delete', 'main')
        self.assertIn('failed', self.update(1))

    def test_detached_and_canonical_refused(self):
        self.git(self.a, 'checkout', '--detach')
        self.assertIn('branch refused', self.update(1))
        self.git(self.a, 'checkout', 'main')
        self.assertIn('branch refused', self.update(1))

    def test_unrelated_personal_branch_not_integrated(self):
        (self.b / 'peer.txt').write_text('private', encoding='utf-8')
        self.commit(self.b)
        self.git(self.b, 'push', 'origin', 'HEAD')
        self.assertIn('No update needed', self.update(0))
        self.assertFalse((self.a / 'peer.txt').exists())

    def test_configured_canonical(self):
        import json
        for root in (self.a, self.b):
            path = root / '.group/group.json'
            config = json.loads(path.read_text())
            config['canonical_branch'] = 'accepted'
            path.write_text(json.dumps(config), encoding='utf-8')
            self.commit(root)
        self.git(self.b, 'checkout', '-b', 'accepted')
        (self.a / 'personal.txt').write_text('local only', encoding='utf-8')
        self.commit(self.a)
        self.suite()
        tip = self.commit(self.b)
        self.git(self.b, 'push', 'origin', 'accepted')
        # Histories diverged by local configuration: prepare merge, never commit.
        output = self.update(1)
        self.assertIn('refs/remotes/origin/accepted@' + tip, output)
        self.assertEqual(self.git(self.a, 'rev-parse', 'MERGE_HEAD'), tip)
        self.git(self.a, 'merge', '--abort')

    def test_zero_tests_not_pass(self):
        (self.b / 'tests').mkdir()
        (self.b / 'tests/test_empty.py').write_text('# no tests\n', encoding='utf-8')
        self.incoming()
        self.assertIn('Tests: not_available', self.update(1))

    def test_unfinished_operation_refused(self):
        location = self.a / '.git/CHERRY_PICK_HEAD'
        location.write_text(self.git(self.a, 'rev-parse', 'HEAD') + '\n', encoding='ascii')
        self.assertIn('Unfinished Git operation', self.update(1))
