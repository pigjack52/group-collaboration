"""Two students and a bare remote, entirely local; exercise the real CLI."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_events import sample

CLI = Path(__file__).resolve().parents[1] / 'groupctl.py'


class SyncTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_TERMINAL_PROMPT='0')
        context = patch.dict(os.environ, env, clear=True)
        context.start()
        self.addCleanup(context.stop)
        self.remote = self.base / 'remote.git'
        self.a = self.base / 'a'
        self.b = self.base / 'b'
        self.git(self.base, 'init', '--bare', str(self.remote))
        self.git(self.base, 'init', '-b', 'main', str(self.a))
        self.identity(self.a)
        (self.a / '.group').mkdir()
        (self.a / '.group/group.json').write_text(json.dumps(dict(
            protocol_version='0.1', group_id='G-01', canonical_branch='main',
            assignment_file='assignment.md')), encoding='utf-8')
        (self.a / '.gitignore').write_text('.group/local/\n', encoding='utf-8')
        (self.a / 'work.txt').write_text('base\n', encoding='utf-8')
        self.commit(self.a)
        self.git(self.a, 'remote', 'add', 'origin', str(self.remote))
        self.git(self.a, 'push', 'origin', 'main')
        self.git(self.base, 'clone', '-b', 'main', str(self.remote), str(self.b))
        self.identity(self.b)
        self.git(self.a, 'checkout', '-b', 'people/P01/work')
        self.git(self.b, 'checkout', '-b', 'people/P02/work')

    def git(self, root, *args):
        result = subprocess.run(['git', *args], cwd=root, capture_output=True,
                                text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def identity(self, root):
        self.git(root, 'config', 'user.name', 'Fixture')
        self.git(root, 'config', 'user.email', 'fixture@example.test')
        self.git(root, 'config', 'core.autocrlf', 'false')

    def commit(self, root):
        self.git(root, 'add', '.')
        self.git(root, 'commit', '-m', 'fixture')
        return self.git(root, 'rev-parse', 'HEAD')

    def publish(self, event=None, name='E-peer'):
        directory = self.b / '.group/events'
        directory.mkdir(exist_ok=True)
        path = directory / (name + '.json')
        path.write_text(json.dumps(event or dict(sample(), event_id=name,
                                                person_id='P02')), encoding='utf-8')
        tip = self.commit(self.b)
        self.git(self.b, 'push', 'origin', 'HEAD')
        return tip

    def sync(self, expected=0):
        result = subprocess.run([sys.executable, '-B', str(CLI), 'sync', '--discover'],
                                cwd=self.a, capture_output=True, text=True,
                                encoding='utf-8')
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        return result.stdout

    def test_remote_only_event_commit_dirty_and_repeat(self):
        self.sync()
        tip = self.publish()
        work = self.a / 'work.txt'
        work.write_text('staged\n', encoding='utf-8')
        self.git(self.a, 'add', 'work.txt')
        work.write_text('unstaged\n', encoding='utf-8')
        (self.a / 'new.txt').write_bytes(b'untracked')
        before = (self.git(self.a, 'status', '--porcelain'),
                  self.git(self.a, 'rev-parse', 'HEAD'),
                  self.git(self.a, 'diff', '--cached'), work.read_bytes())
        output = self.sync()
        self.assertIn(tip, output)
        self.assertIn('New event [refs/remotes/origin/people/P02/work', output)
        self.assertIn('"event_type": "finding"', output)
        self.assertFalse((self.a / '.group/events/E-peer.json').exists())
        after = (self.git(self.a, 'status', '--porcelain'),
                 self.git(self.a, 'rev-parse', 'HEAD'),
                 self.git(self.a, 'diff', '--cached'), work.read_bytes())
        self.assertEqual(before, after)
        self.assertEqual((self.a / 'new.txt').read_bytes(), b'untracked')
        self.assertIn('New events: 0', self.sync())
        self.assertNotIn('New event [', self.sync())

    def test_canonical_new_commit(self):
        self.sync()
        self.git(self.b, 'checkout', 'main')
        (self.b / 'work.txt').write_text('remote canonical', encoding='utf-8')
        tip = self.commit(self.b)
        self.git(self.b, 'push', 'origin', 'main')
        output = self.sync()
        self.assertIn(tip, output)
        self.assertIn('refs/remotes/origin/main', output)
        self.assertEqual((self.a / 'work.txt').read_text(), 'base\n')

    def test_no_origin(self):
        self.git(self.a, 'remote', 'remove', 'origin')
        self.assertIn('No origin', self.sync(1))

    def test_fetch_failure(self):
        self.git(self.a, 'remote', 'set-url', 'origin', str(self.base / 'absent.git'))
        self.assertIn('failed', self.sync(1))
        self.assertFalse((self.a / '.group/local/sync-discover.json').exists())

    def test_invalid_event_does_not_advance_receipts(self):
        self.publish({'bad': True})
        self.assertIn('Invalid event', self.sync(1))
        self.assertIn('Invalid event', self.sync(1))
        self.assertFalse((self.a / '.group/local/sync-discover.json').exists())

    def test_deleted_remote_branch_with_current_upstream(self):
        self.publish()
        self.sync()
        self.git(self.a, 'branch', '--set-upstream-to=origin/people/P02/work')
        self.git(self.b, 'push', 'origin', '--delete', 'people/P02/work')
        self.assertIn('Remote branch deleted', self.sync(1))

    def test_rewritten_history(self):
        self.publish()
        self.sync()
        self.git(self.b, 'checkout', 'main')
        (self.b / 'other').write_text('replacement', encoding='utf-8')
        self.commit(self.b)
        self.git(self.b, 'push', '--force', 'origin', 'HEAD:people/P02/work')
        self.assertIn('History rewritten', self.sync(1))

    def test_mutated_immutable_event(self):
        self.publish()
        self.sync()
        self.publish(dict(sample(), event_id='E-peer', summary='changed'))
        output = self.sync(1)
        self.assertIn('Published event modified/deleted', output)
        self.assertIn('Immutable event changed', output)

    def test_conflicting_declarations(self):
        for name, verdict in [('E-review1', 'accept'), ('E-review2', 'reject')]:
            self.publish(dict(sample('review'), event_id=name, change_id='C-peer',
                              verdict=verdict), name)
        self.assertIn('Potentially conflicting review', self.sync(1))

    def test_missing_canonical(self):
        self.git(self.b, 'push', 'origin', '--delete', 'main')
        self.assertIn('Canonical remote branch unavailable', self.sync(1))

    def test_conflicting_test_results_for_same_commit(self):
        for name, result in [('E-test1', 'pass'), ('E-test2', 'fail')]:
            self.publish(dict(sample('test_result'), event_id=name,
                              automated_result=result), name)
        self.assertIn('Conflicting evidence', self.sync(1))

    def test_malformed_cache(self):
        self.sync()
        (self.a / '.group/local/sync-discover.json').write_text('[]', encoding='utf-8')
        self.assertIn('invalid discovery cache', self.sync(1))


if __name__ == '__main__':
    unittest.main()
