"""Tests for multi-scope isolated TEST (main, change_id, publish) and STATUS next action."""
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

import groupctl


def git(cwd, *args):
    res = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True)
    if res.returncode != 0:
        raise AssertionError(f'git {args} failed: {res.stderr}')
    return res.stdout.strip()


def run_cli(cwd, *argv):
    return subprocess.run(
        [sys.executable, '-B', str(Path(groupctl.__file__).resolve()), *argv],
        cwd=cwd, capture_output=True, text=True,
    )


class TestScopesAndStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.remote = base / 'remote.git'
        git(base, 'init', '--bare', '-b', 'main', str(self.remote))
        self.repo = base / 'work'
        git(base, 'clone', str(self.remote), str(self.repo))
        git(self.repo, 'config', 'user.name', 'Student 01')
        git(self.repo, 'config', 'user.email', 'student01@example.com')

        (self.repo / '.group/assignment').mkdir(parents=True)
        (self.repo / '.group/members').mkdir(parents=True)
        (self.repo / '.group/events').mkdir(parents=True)
        (self.repo / '.group/group.json').write_text(json.dumps({
            'protocol_version': '0.1', 'group_id': 'group-01',
            'canonical_branch': 'main', 'assignment_file': '.group/assignment/assignment.md',
            'repository': str(self.remote),
        }), encoding='utf-8')
        (self.repo / '.group/assignment/assignment.md').write_text('# Demo\n', encoding='utf-8')
        for pid in ('student01', 'student02'):
            (self.repo / f'.group/members/{pid}.json').write_text(json.dumps({
                'protocol_version': '0.1', 'person_id': pid,
                'display_name': pid, 'joined_at': '2026-11-13T07:47:10Z',
            }), encoding='utf-8')
        (self.repo / 'calc.py').write_text('def add(a, b):\n    return a + b\n', encoding='utf-8')
        (self.repo / 'tests').mkdir()
        (self.repo / 'tests/test_calc.py').write_text(
            'import unittest\nimport calc\n\n'
            'class CalcTests(unittest.TestCase):\n'
            '    def test_add(self):\n'
            '        self.assertEqual(calc.add(2, 3), 5)\n',
            encoding='utf-8',
        )
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-m', 'Initial')
        git(self.repo, 'push', '-u', 'origin', 'main')
        self.main_sha = git(self.repo, 'rev-parse', 'HEAD')

        git(self.repo, 'checkout', '-b', 'people/student02/broken')
        (self.repo / 'calc.py').write_text('def add(a, b):\n    return a - b\n', encoding='utf-8')
        git(self.repo, 'add', 'calc.py')
        git(self.repo, 'commit', '-m', 'Break add')
        self.broken_sha = git(self.repo, 'rev-parse', 'HEAD')
        ev_id = 'E-20261127T020000Z-student02-bbbb2222'
        (self.repo / f'.group/events/{ev_id}.json').write_text(json.dumps({
            'protocol_version': '0.1', 'event_id': ev_id, 'event_type': 'change_published',
            'group_id': 'group-01', 'person_id': 'student02', 'created_at': '2026-11-27T02:00:00Z',
            'related_events': [], 'change_id': 'C-99', 'branch': 'people/student02/broken',
            'commit': self.broken_sha, 'summary': 'Broken calc change.',
            'files_changed': ['calc.py'], 'interface_changes': [],
            'tests_run': [], 'known_issues': [], 'proposed_for_integration': True,
        }, indent=2) + '\n', encoding='utf-8')
        git(self.repo, 'add', f'.group/events/{ev_id}.json')
        git(self.repo, 'commit', '-m', 'Publish C-99')
        git(self.repo, 'push', '-u', 'origin', 'people/student02/broken')

        git(self.repo, 'checkout', '-b', 'people/student01/work', 'main')
        git(self.repo, 'push', '-u', 'origin', 'people/student01/work')

    def test_main_and_change_scopes_isolated_and_publishable(self):
        # Leave an untracked note in student01's worktree; isolated scope must not disturb it.
        (self.repo / 'scratch.txt').write_text('keep me\n', encoding='utf-8')

        # 1. TEST --scope main (isolated worktree, should pass on main_sha)
        res_main = run_cli(
            self.repo, 'test', '--person-id', 'student01', '--scope', 'main',
            '--publish', '--remote', 'origin', '--expected-url', str(self.remote), '--confirm-reviewed',
        )
        self.assertEqual(res_main.returncode, 0, res_main.stdout + res_main.stderr)
        data_main = json.loads(res_main.stdout)
        self.assertEqual(data_main['event']['scope'], 'canonical')
        self.assertEqual(data_main['event']['commit'], self.main_sha)
        self.assertEqual(data_main['event']['automated_result'], 'pass')
        self.assertTrue(data_main['verified'])
        self.assertEqual((self.repo / 'scratch.txt').read_text(encoding='utf-8'), 'keep me\n')

        # 2. TEST --scope C-99 (isolated worktree, should fail accurately on broken_sha)
        res_chg = run_cli(self.repo, 'test', '--person-id', 'student01', '--scope', 'C-99')
        self.assertEqual(res_chg.returncode, 1, res_chg.stdout + res_chg.stderr)
        data_chg = json.loads(res_chg.stdout)
        self.assertEqual(data_chg['event']['scope'], 'C-99')
        self.assertEqual(data_chg['event']['commit'], self.broken_sha)
        self.assertEqual(data_chg['event']['automated_result'], 'fail')
        self.assertEqual(data_chg['event']['failed'], 1)

        # 3. STATUS includes Recommended next action.
        st = run_cli(self.repo, 'status')
        self.assertEqual(st.returncode, 0, st.stdout + st.stderr)
        self.assertIn('Recommended next action:', st.stdout)


if __name__ == '__main__':
    unittest.main()
