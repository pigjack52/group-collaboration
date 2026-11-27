"""Tests for FINAL --prepare and --publish with candidate-bound human confirmation."""
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


class FinalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.remote = base / 'remote.git'
        git(base, 'init', '--bare', '-b', 'main', str(self.remote))
        self.repo = base / 'work'
        git(base, 'clone', str(self.remote), str(self.repo))
        git(self.repo, 'config', 'user.name', 'Student 03')
        git(self.repo, 'config', 'user.email', 'student03@example.com')

        (self.repo / '.group/assignment').mkdir(parents=True)
        (self.repo / '.group/members').mkdir(parents=True)
        (self.repo / '.group/events').mkdir(parents=True)
        (self.repo / '.group/final').mkdir(parents=True)
        (self.repo / '.group/group.json').write_text(json.dumps({
            'protocol_version': '0.1', 'group_id': 'group-01',
            'canonical_branch': 'main', 'assignment_file': '.group/assignment/assignment.md',
            'repository': str(self.remote),
        }), encoding='utf-8')
        (self.repo / '.group/assignment/assignment.md').write_text('# Group Assignment\n', encoding='utf-8')
        for pid in ('student01', 'student02', 'student03'):
            (self.repo / f'.group/members/{pid}.json').write_text(json.dumps({
                'protocol_version': '0.1', 'person_id': pid,
                'display_name': pid.title(), 'joined_at': '2026-11-13T07:47:10Z',
            }), encoding='utf-8')
        (self.repo / 'app.py').write_text('def ok():\n    return True\n', encoding='utf-8')
        (self.repo / 'tests').mkdir()
        (self.repo / 'tests/test_ok.py').write_text(
            'import unittest\nimport app\n\n'
            'class OkTests(unittest.TestCase):\n'
            '    def test_ok(self):\n'
            '        self.assertTrue(app.ok())\n',
            encoding='utf-8',
        )
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-m', 'Initial canonical')
        git(self.repo, 'push', '-u', 'origin', 'main')
        self.canon_sha = git(self.repo, 'rev-parse', 'HEAD')

    def test_prepare_and_publish_with_confirmation_and_tamper_guard(self):
        # 1. Prepare candidate on canonical branch by any recognized member (student03).
        prep = run_cli(
            self.repo, 'final', '--prepare', '--person-id', 'student03',
            '--result-summary', 'Completed group collaboration tool.',
            '--simulation',
        )
        self.assertEqual(prep.returncode, 0, prep.stdout + prep.stderr)
        pdata = json.loads(prep.stdout)
        self.assertEqual(pdata['status'], 'candidate_ready_for_human_review')
        self.assertEqual(pdata['canonical_commit'], self.canon_sha)
        self.assertEqual(pdata['test_outcome']['automated_result'], 'pass')
        cand_id = pdata['candidate_id']
        report_hash = pdata['report_sha256']

        # 2. Tampered confirmation hash must be rejected.
        conf_path = Path(self.tmp.name) / 'confirm.json'
        conf_path.write_text(json.dumps({
            'candidate_id': cand_id,
            'canonical_commit': self.canon_sha,
            'report_sha256': '0' * 64,
            'confirmed_by': 'student03',
            'confirmed_at': '2026-11-27T12:00:00Z',
            'statement': 'Simulated confirmation for test.',
            'evidence_mode': 'simulation',
        }), encoding='utf-8')
        bad = run_cli(
            self.repo, 'final', '--publish', '--person-id', 'student03',
            '--candidate-id', cand_id, '--confirmation-file', str(conf_path),
            '--confirm-human',
        )
        self.assertEqual(bad.returncode, 1)

        # 3. Valid candidate-bound confirmation publishes and verifies remotely.
        conf_path.write_text(json.dumps({
            'candidate_id': cand_id,
            'canonical_commit': self.canon_sha,
            'report_sha256': report_hash,
            'confirmed_by': 'student03',
            'confirmed_at': '2026-11-27T12:00:00Z',
            'statement': 'Simulated confirmation for automated test.',
            'evidence_mode': 'simulation',
        }), encoding='utf-8')
        pub = run_cli(
            self.repo, 'final', '--publish', '--person-id', 'student03',
            '--candidate-id', cand_id, '--confirmation-file', str(conf_path),
            '--confirm-human', '--remote', 'origin', '--expected-url', str(self.remote),
        )
        self.assertEqual(pub.returncode, 0, pub.stdout + pub.stderr)
        fdata = json.loads(pub.stdout)
        self.assertEqual(fdata['status'], 'published_verified')
        self.assertTrue(fdata['verified'])
        self.assertEqual(fdata['final_status'], 'completed')
        self.assertEqual(fdata['evidence_mode'], 'simulation')

        final_json = json.loads((self.repo / '.group/final/final.json').read_text(encoding='utf-8'))
        self.assertEqual(final_json['canonical_commit'], self.canon_sha)
        self.assertEqual(final_json['initiated_by'], 'student03')
        self.assertEqual(final_json['status'], 'completed')
        self.assertEqual(final_json['evidence_mode'], 'simulation')
        self.assertIn('## Canonical Commit', (self.repo / '.group/final/final_report.md').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
