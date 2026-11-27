"""Tests for REV --inspect and --publish (never auto-merges)."""
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


class RevTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.remote = base / 'remote.git'
        git(base, 'init', '--bare', '-b', 'main', str(self.remote))
        self.repo = base / 'work'
        git(base, 'clone', str(self.remote), str(self.repo))
        git(self.repo, 'config', 'user.name', 'Reviewer')
        git(self.repo, 'config', 'user.email', 'rev@example.com')

        (self.repo / '.group/assignment').mkdir(parents=True)
        (self.repo / '.group/members').mkdir(parents=True)
        (self.repo / '.group/events').mkdir(parents=True)
        (self.repo / '.group/group.json').write_text(json.dumps({
            'protocol_version': '0.1', 'group_id': 'group-01',
            'canonical_branch': 'main', 'assignment_file': '.group/assignment/assignment.md',
            'repository': str(self.remote),
        }), encoding='utf-8')
        (self.repo / '.group/assignment/assignment.md').write_text('# Assignment\n', encoding='utf-8')
        for pid in ('student01', 'student02'):
            (self.repo / f'.group/members/{pid}.json').write_text(json.dumps({
                'protocol_version': '0.1', 'person_id': pid,
                'display_name': pid, 'joined_at': '2026-11-13T07:47:10Z',
            }), encoding='utf-8')
        (self.repo / 'base.txt').write_text('base\n', encoding='utf-8')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-m', 'init')
        git(self.repo, 'push', '-u', 'origin', 'main')

        git(self.repo, 'checkout', '-b', 'people/student01/parser')
        (self.repo / 'parser.py').write_text('def parse(x):\n    return x.strip()\n', encoding='utf-8')
        git(self.repo, 'add', 'parser.py')
        git(self.repo, 'commit', '-m', 'Add parser')
        self.work_sha = git(self.repo, 'rev-parse', 'HEAD')
        ev_id = 'E-20261127T010000Z-student01-12345678'
        (self.repo / f'.group/events/{ev_id}.json').write_text(json.dumps({
            'protocol_version': '0.1', 'event_id': ev_id, 'event_type': 'change_published',
            'group_id': 'group-01', 'person_id': 'student01', 'created_at': '2026-11-27T01:00:00Z',
            'related_events': [], 'change_id': 'C-22', 'branch': 'people/student01/parser',
            'commit': self.work_sha, 'summary': 'Add parser function.',
            'files_changed': ['parser.py'], 'interface_changes': ['parse(x)'],
            'tests_run': ['unittest'], 'known_issues': [], 'proposed_for_integration': True,
        }, indent=2) + '\n', encoding='utf-8')
        git(self.repo, 'add', f'.group/events/{ev_id}.json')
        git(self.repo, 'commit', '-m', 'Publish C-22')
        git(self.repo, 'push', '-u', 'origin', 'people/student01/parser')

        git(self.repo, 'checkout', '-b', 'people/student02/review', 'main')
        git(self.repo, 'push', '-u', 'origin', 'people/student02/review')

    def test_inspect_and_publish_review_never_merges(self):
        insp = run_cli(self.repo, 'rev', '--inspect', '--person-id', 'student02', '--change-id', 'C-22')
        self.assertEqual(insp.returncode, 0, insp.stdout + insp.stderr)
        idata = json.loads(insp.stdout)
        self.assertEqual(idata['status'], 'ready_for_review')
        self.assertEqual(idata['work_commit'], self.work_sha)
        self.assertEqual(idata['actual_files_changed'], ['parser.py'])
        self.assertIn('def parse(x):', idata['diff'])
        self.assertFalse(idata['merge_performed'])

        # Wrong inspected_commit rejected.
        bad = run_cli(
            self.repo, 'rev', '--publish', '--person-id', 'student02', '--change-id', 'C-22',
            '--inspected-commit', '0' * 40, '--diff-inspected', '--verdict', 'accept',
            '--summary', 'Wrong commit',
        )
        self.assertEqual(bad.returncode, 1)

        # Positive review publishes cleanly and does NOT merge parser.py into student02's branch.
        pub = run_cli(
            self.repo, 'rev', '--publish', '--person-id', 'student02', '--change-id', 'C-22',
            '--inspected-commit', self.work_sha, '--diff-inspected', '--verdict', 'accept',
            '--summary', 'Parser implementation looks clean.', '--test-run', 'inspected diff',
            '--remote', 'origin', '--expected-url', str(self.remote), '--confirm-reviewed',
        )
        self.assertEqual(pub.returncode, 0, pub.stdout + pub.stderr)
        pdata = json.loads(pub.stdout)
        self.assertEqual(pdata['status'], 'published_verified')
        self.assertTrue(pdata['verified'])
        self.assertFalse(pdata['merge_performed'])
        self.assertFalse((self.repo / 'parser.py').exists())
        self.assertEqual(git(self.remote, 'rev-parse', 'refs/heads/people/student02/review'),
                         pdata['event_commit'])


if __name__ == '__main__':
    unittest.main()
