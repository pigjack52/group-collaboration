"""End-to-end tests for INT --commit-merge, --verify, and --publish-result."""
from contextlib import chdir
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
    proc = subprocess.run(
        [sys.executable, '-B', str(Path(groupctl.__file__).resolve()), *argv],
        cwd=cwd, capture_output=True, text=True,
    )
    return proc


class IntCompleteTests(unittest.TestCase):
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
            'protocol_version': '0.1',
            'group_id': 'group-01',
            'canonical_branch': 'main',
            'assignment_file': '.group/assignment/assignment.md',
            'repository': str(self.remote),
        }), encoding='utf-8')
        (self.repo / '.group/assignment/assignment.md').write_text('# Demo\n', encoding='utf-8')
        for pid in ('student01', 'student02'):
            (self.repo / f'.group/members/{pid}.json').write_text(json.dumps({
                'protocol_version': '0.1',
                'person_id': pid,
                'display_name': pid.title(),
                'joined_at': '2026-11-13T07:47:10Z',
            }), encoding='utf-8')
        (self.repo / 'app.py').write_text('def value():\n    return 1\n', encoding='utf-8')
        (self.repo / 'tests').mkdir()
        (self.repo / 'tests/test_app.py').write_text(
            'import unittest\nimport app\n\n'
            'class AppTests(unittest.TestCase):\n'
            '    def test_value(self):\n'
            '        self.assertGreaterEqual(app.value(), 1)\n',
            encoding='utf-8',
        )
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-m', 'Initial commit')
        git(self.repo, 'push', '-u', 'origin', 'main')
        self.main_sha = git(self.repo, 'rev-parse', 'HEAD')

        # Student02 creates a feature commit and publishes C-101.
        git(self.repo, 'checkout', '-b', 'people/student02/feature')
        (self.repo / 'feature.py').write_text('def extra():\n    return 2\n', encoding='utf-8')
        git(self.repo, 'add', 'feature.py')
        git(self.repo, 'commit', '-m', 'Add feature')
        self.work_sha = git(self.repo, 'rev-parse', 'HEAD')
        self.event_id = 'E-20261127T000001Z-student02-aaaa1111'
        event = {
            'protocol_version': '0.1',
            'event_id': self.event_id,
            'event_type': 'change_published',
            'group_id': 'group-01',
            'person_id': 'student02',
            'created_at': '2026-11-27T00:00:01Z',
            'related_events': [],
            'change_id': 'C-101',
            'branch': 'people/student02/feature',
            'commit': self.work_sha,
            'summary': 'Add feature module.',
            'files_changed': ['feature.py'],
            'interface_changes': [],
            'tests_run': ['unittest'],
            'known_issues': [],
            'proposed_for_integration': True,
        }
        (self.repo / f'.group/events/{self.event_id}.json').write_text(
            json.dumps(event, indent=2) + '\n', encoding='utf-8')
        git(self.repo, 'add', f'.group/events/{self.event_id}.json')
        git(self.repo, 'commit', '-m', 'Publish C-101')
        git(self.repo, 'push', '-u', 'origin', 'people/student02/feature')

        # Student01 creates a diverging personal branch.
        git(self.repo, 'checkout', '-b', 'people/student01/work', 'main')
        (self.repo / 'local.py').write_text('LOCAL = True\n', encoding='utf-8')
        git(self.repo, 'add', 'local.py')
        git(self.repo, 'commit', '-m', 'Student01 local work')
        git(self.repo, 'push', '-u', 'origin', 'people/student01/work')
        self.target_head = git(self.repo, 'rev-parse', 'HEAD')

    def _evidence_args(self):
        preflight = run_cli(
            self.repo, 'int', '--preflight', '--person-id', 'student01',
            '--change-id', 'C-101', '--target-branch', 'people/student01/work',
        )
        self.assertEqual(preflight.returncode, 0, preflight.stdout + preflight.stderr)
        data = json.loads(preflight.stdout)
        return [
            '--person-id', 'student01',
            '--change-id', 'C-101',
            '--target-branch', 'people/student01/work',
            '--expected-head', data['target_sha'],
            '--work-sha', data['candidate_work_sha'],
            '--expected-event-id', data['event_id'],
            '--expected-event-blob', data['event_blob_sha'],
            '--expected-source-ref', data['ancestry_evidence']['source_ref'],
            '--expected-source-tip', data['ancestry_evidence']['source_tip_sha'],
            '--expected-incoming', ','.join(data['incoming_commits']) or 'none',
            '--confirm-reviewed',
        ]

    def test_prepare_inspect_commit_verify_and_publish_result(self):
        ev = self._evidence_args()
        prep = run_cli(self.repo, 'int', '--prepare-merge', *ev)
        self.assertEqual(prep.returncode, 0, prep.stdout + prep.stderr)
        self.assertEqual(json.loads(prep.stdout)['status'], 'pending_commit')

        insp = run_cli(self.repo, 'int', '--inspect-merge', *ev)
        self.assertEqual(insp.returncode, 0, insp.stdout + insp.stderr)
        insp_data = json.loads(insp.stdout)
        self.assertEqual(insp_data['status'], 'awaiting_human_confirmation')
        tree_oid = insp_data['staged_tree_oid']
        self.assertTrue(len(tree_oid) == 40)

        # Wrong expected-tree must be refused without advancing HEAD.
        bad = run_cli(
            self.repo, 'int', '--commit-merge', *ev,
            '--expected-tree', '0' * 40, '--message', 'Integrate C-101',
        )
        self.assertEqual(bad.returncode, 1)
        self.assertEqual(git(self.repo, 'rev-parse', 'HEAD'), self.target_head)

        # Exact expected-tree commits the two-parent merge cleanly.
        committed = run_cli(
            self.repo, 'int', '--commit-merge', *ev,
            '--expected-tree', tree_oid, '--message', 'Integrate C-101',
        )
        self.assertEqual(committed.returncode, 0, committed.stdout + committed.stderr)
        cdata = json.loads(committed.stdout)
        self.assertEqual(cdata['status'], 'local_merge_committed')
        merge_sha = cdata['merge_commit']
        self.assertEqual(cdata['parents'], [self.target_head, self.work_sha])
        self.assertEqual(git(self.repo, 'rev-parse', 'HEAD'), merge_sha)
        self.assertFalse((self.repo / '.git/MERGE_HEAD').exists())

        # Verify Level 1 + Level 2 in isolated worktree.
        ver = run_cli(
            self.repo, 'int', '--verify', '--person-id', 'student01',
            '--change-id', 'C-101', '--target-branch', 'people/student01/work',
            '--merge-commit', merge_sha,
        )
        self.assertEqual(ver.returncode, 0, ver.stdout + ver.stderr)
        vdata = json.loads(ver.stdout)
        self.assertEqual(vdata['status'], 'verified_mechanical_only')
        self.assertTrue(vdata['work_incorporated'])
        self.assertEqual(vdata['verification']['tests'], 'pass')
        self.assertEqual(vdata['verification']['semantic_review'], 'not_performed')

        # Refuses result=success if tests are not_available or semantic_review != pass.
        bad_pub = run_cli(
            self.repo, 'int', '--publish-result', '--person-id', 'student01',
            '--change-id', 'C-101', '--target-branch', 'people/student01/work',
            '--merge-commit', merge_sha, '--result', 'success',
            '--build', 'not_applicable', '--tests', 'not_available',
            '--semantic-review', 'pass', '--summary', 'Should fail guard',
        )
        self.assertEqual(bad_pub.returncode, 1)

        # Push merge commit first or push event commit directly (since target branch is ahead of origin).
        pub = run_cli(
            self.repo, 'int', '--publish-result', '--person-id', 'student01',
            '--change-id', 'C-101', '--target-branch', 'people/student01/work',
            '--merge-commit', merge_sha, '--result', 'success',
            '--build', 'not_applicable', '--tests', 'pass',
            '--semantic-review', 'pass', '--summary', 'Integrated C-101 cleanly.',
            '--remote', 'origin', '--expected-url', str(self.remote), '--confirm-reviewed',
        )
        self.assertEqual(pub.returncode, 0, pub.stdout + pub.stderr)
        pdata = json.loads(pub.stdout)
        self.assertEqual(pdata['status'], 'published_verified')
        self.assertTrue(pdata['verified'])
        self.assertEqual(git(self.remote, 'rev-parse', 'refs/heads/people/student01/work'),
                         pdata['event_commit'])

    def test_verify_detects_missing_tests_as_not_available(self):
        ev = self._evidence_args()
        self.assertEqual(run_cli(self.repo, 'int', '--prepare-merge', *ev).returncode, 0)
        tree_oid = json.loads(run_cli(self.repo, 'int', '--inspect-merge', *ev).stdout)['staged_tree_oid']
        cdata = json.loads(run_cli(
            self.repo, 'int', '--commit-merge', *ev,
            '--expected-tree', tree_oid, '--message', 'Merge C-101',
        ).stdout)
        # Remove tests/ in a follow-up commit and verify it never reports pass.
        (self.repo / 'tests/test_app.py').unlink()
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-m', 'Remove tests')
        no_test_sha = git(self.repo, 'rev-parse', 'HEAD')
        ver = run_cli(
            self.repo, 'int', '--verify', '--person-id', 'student01',
            '--change-id', 'C-101', '--target-branch', 'people/student01/work',
            '--merge-commit', no_test_sha,
        )
        self.assertEqual(ver.returncode, 1)
        vdata = json.loads(ver.stdout)
        self.assertEqual(vdata['verification']['tests'], 'not_available')


if __name__ == '__main__':
    unittest.main()
