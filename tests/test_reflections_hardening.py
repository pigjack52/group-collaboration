"""Regression tests for reflection-driven hardening (items 1-5)."""
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
        raise AssertionError(f'git {args} failed in {cwd}: {res.stderr}')
    return res.stdout.strip()


def run_cli(cwd, *argv):
    return subprocess.run(
        [sys.executable, '-B', str(Path(groupctl.__file__).resolve()), *argv],
        cwd=cwd, capture_output=True, text=True,
    )


class ReflectionHardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.outside = base / 'outside'
        self.outside.mkdir()
        self.remote = base / 'remote.git'
        git(base, 'init', '--bare', '-b', 'main', str(self.remote))
        self.repo = base / 'team_repo'
        git(base, 'clone', str(self.remote), str(self.repo))
        git(self.repo, 'config', 'user.name', 'Student 01')
        git(self.repo, 'config', 'user.email', 'student01@example.com')

        (self.repo / '.gitignore').write_text('.group/local/*\n!.group/local/.gitkeep\n*.tmp\n', encoding='utf-8')
        (self.repo / '.group/assignment').mkdir(parents=True)
        (self.repo / '.group/members').mkdir(parents=True)
        (self.repo / '.group/events').mkdir(parents=True)
        (self.repo / '.group/final').mkdir(parents=True)
        (self.repo / '.group/local').mkdir(parents=True)
        (self.repo / '.group/local/.gitkeep').write_text('', encoding='utf-8')
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
        (self.repo / 'app.py').write_text('X = 1\n', encoding='utf-8')
        (self.repo / 'tests').mkdir()
        (self.repo / 'tests/test_app.py').write_text(
            'import unittest\nimport app\n\n'
            'class T(unittest.TestCase):\n'
            '    def test_x(self):\n'
            '        self.assertEqual(app.X, 1)\n',
            encoding='utf-8',
        )
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-m', 'init')
        git(self.repo, 'push', '-u', 'origin', 'main')

    def test_version_and_external_repo_flag(self):
        ver = run_cli(self.outside, '--version')
        self.assertEqual(ver.returncode, 0)
        self.assertIn('protocol 0.1', ver.stdout)

        cfg = run_cli(self.outside, '--repo', str(self.repo), 'config', 'show')
        self.assertEqual(cfg.returncode, 0, cfg.stdout + cfg.stderr)
        self.assertEqual(json.loads(cfg.stdout)['group_id'], 'group-01')

    def test_sync_cache_allowed_in_int_while_other_ignored_still_blocked(self):
        # Student02 publishes C-201 on another branch.
        git(self.repo, 'checkout', '-b', 'people/student02/feat')
        (self.repo / 'feat.py').write_text('F = 2\n', encoding='utf-8')
        git(self.repo, 'add', 'feat.py')
        git(self.repo, 'commit', '-m', 'feat')
        wsha = git(self.repo, 'rev-parse', 'HEAD')
        ev_id = 'E-20261127T040000Z-student02-cccc3333'
        (self.repo / f'.group/events/{ev_id}.json').write_text(json.dumps({
            'protocol_version': '0.1', 'event_id': ev_id, 'event_type': 'change_published',
            'group_id': 'group-01', 'person_id': 'student02', 'created_at': '2026-11-27T04:00:00Z',
            'related_events': [], 'change_id': 'C-201', 'branch': 'people/student02/feat',
            'commit': wsha, 'summary': 'Add feat', 'files_changed': ['feat.py'],
            'interface_changes': [], 'tests_run': [], 'known_issues': [],
            'proposed_for_integration': True,
        }), encoding='utf-8')
        git(self.repo, 'add', f'.group/events/{ev_id}.json')
        git(self.repo, 'commit', '-m', 'pub C-201')
        git(self.repo, 'push', '-u', 'origin', 'people/student02/feat')

        # Student01 diverges, runs sync --discover (creates .group/local/sync-discover.json), then INT.
        git(self.repo, 'checkout', '-b', 'people/student01/work', 'main')
        (self.repo / 'mine.py').write_text('M = 1\n', encoding='utf-8')
        git(self.repo, 'add', 'mine.py')
        git(self.repo, 'commit', '-m', 'mine')
        git(self.repo, 'push', '-u', 'origin', 'people/student01/work')

        self.assertEqual(run_cli(self.repo, 'sync', '--discover').returncode, 0)
        self.assertTrue((self.repo / '.group/local/sync-discover.json').is_file())

        # status --fetch lists pending remote change C-201.
        st = run_cli(self.outside, '--repo', str(self.repo), 'status', '--fetch')
        self.assertEqual(st.returncode, 0, st.stdout + st.stderr)
        self.assertIn('Remote metadata: fetched from origin', st.stdout)
        self.assertIn('C-201', st.stdout)

        pre = json.loads(run_cli(
            self.repo, 'int', '--preflight', '--person-id', 'student01',
            '--change-id', 'C-201', '--target-branch', 'people/student01/work',
        ).stdout)
        ev = [
            '--person-id', 'student01', '--change-id', 'C-201', '--target-branch', 'people/student01/work',
            '--expected-head', pre['target_sha'], '--work-sha', pre['candidate_work_sha'],
            '--expected-event-id', pre['event_id'], '--expected-event-blob', pre['event_blob_sha'],
            '--expected-source-ref', pre['ancestry_evidence']['source_ref'],
            '--expected-source-tip', pre['ancestry_evidence']['source_tip_sha'],
            '--expected-incoming', ','.join(pre['incoming_commits']) or 'none',
            '--confirm-reviewed',
        ]
        # Other ignored file MUST still block prepare-merge.
        (self.repo / 'other.tmp').write_text('ignored\n', encoding='utf-8')
        blocked = run_cli(self.repo, 'int', '--prepare-merge', *ev)
        self.assertEqual(blocked.returncode, 1)
        (self.repo / 'other.tmp').unlink()

        # With ONLY .group/local/sync-discover.json present, prepare -> inspect -> commit succeeds!
        prep = run_cli(self.repo, 'int', '--prepare-merge', *ev)
        self.assertEqual(prep.returncode, 0, prep.stdout + prep.stderr)
        insp = run_cli(self.repo, 'int', '--inspect-merge', *ev)
        self.assertEqual(insp.returncode, 0, insp.stdout + insp.stderr)
        tree_oid = json.loads(insp.stdout)['staged_tree_oid']
        cm = run_cli(self.repo, 'int', '--commit-merge', *ev,
                     '--expected-tree', tree_oid, '--message', 'Merge C-201')
        self.assertEqual(cm.returncode, 0, cm.stdout + cm.stderr)

    def test_human_check_and_events_publish_blocks_change_published(self):
        git(self.repo, 'checkout', '-b', 'people/student01/hc', 'main')
        res = run_cli(
            self.repo, 'test', '--person-id', 'student01', '--scope', 'current', '--isolated',
            '--human-check', 'Open UI and confirm button highlight.',
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        data = json.loads(res.stdout)
        self.assertEqual(data['event']['human_checks_required'], ['Open UI and confirm button highlight.'])

        # events publish must reject change_published.
        bad_ev = self.outside / 'cp.json'
        bad_ev.write_text(json.dumps({
            'protocol_version': '0.1', 'event_id': 'E-20261127T050000Z-student01-dddd4444',
            'event_type': 'change_published', 'group_id': 'group-01', 'person_id': 'student01',
            'created_at': '2026-11-27T05:00:00Z', 'related_events': [],
            'change_id': 'C-999', 'branch': 'people/student01/hc', 'commit': git(self.repo, 'rev-parse', 'HEAD'),
            'summary': 'Bypass attempt', 'files_changed': ['app.py'], 'interface_changes': [],
            'tests_run': [], 'known_issues': [], 'proposed_for_integration': True,
        }), encoding='utf-8')
        rej = run_cli(self.repo, 'events', 'publish', str(bad_ev), '--person-id', 'student01')
        self.assertEqual(rej.returncode, 1)
        self.assertIn('groupctl pub', rej.stderr)

    def test_final_resolved_blocker_and_post_prepare_drift_detection(self):
        # Record a blocker AND a finding that resolves it.
        blk_id = 'E-20261127T060000Z-student01-blk11111'
        res_id = 'E-20261127T060100Z-student01-fnd11111'
        (self.repo / f'.group/events/{blk_id}.json').write_text(json.dumps({
            'protocol_version': '0.1', 'event_id': blk_id, 'event_type': 'blocker',
            'group_id': 'group-01', 'person_id': 'student01', 'created_at': '2026-11-27T06:00:00Z',
            'related_events': [], 'summary': 'Temp blocker', 'blocking_change_id': 'GROUP',
            'requested_action': 'Fix it',
        }), encoding='utf-8')
        (self.repo / f'.group/events/{res_id}.json').write_text(json.dumps({
            'protocol_version': '0.1', 'event_id': res_id, 'event_type': 'finding',
            'group_id': 'group-01', 'person_id': 'student01', 'created_at': '2026-11-27T06:01:00Z',
            'related_events': [blk_id], 'summary': 'Blocker resolved.',
            'evidence': ['Verified fix'], 'resolves_events': [blk_id],
        }), encoding='utf-8')
        git(self.repo, 'add', '.group/events')
        git(self.repo, 'commit', '-m', 'Add blocker and resolution')
        git(self.repo, 'push', 'origin', 'main')

        prep = run_cli(self.repo, 'final', '--prepare', '--person-id', 'student01',
                       '--result-summary', 'Ready', '--simulation')
        self.assertEqual(prep.returncode, 0, prep.stdout + prep.stderr)
        pdata = json.loads(prep.stdout)
        self.assertEqual(pdata['unresolved_concerns'], [])
        self.assertEqual(pdata['resolved_blockers'], [blk_id])

        # Introduce a NEW open blocker on disk after --prepare; final --publish must refuse!
        new_blk = 'E-20261127T060500Z-student02-blk22222'
        (self.repo / f'.group/events/{new_blk}.json').write_text(json.dumps({
            'protocol_version': '0.1', 'event_id': new_blk, 'event_type': 'blocker',
            'group_id': 'group-01', 'person_id': 'student02', 'created_at': '2026-11-27T06:05:00Z',
            'related_events': [], 'summary': 'Late critical blocker', 'blocking_change_id': 'GROUP',
            'requested_action': 'Stop finalization',
        }), encoding='utf-8')
        conf = self.outside / 'confirm.json'
        conf.write_text(json.dumps({
            'candidate_id': pdata['candidate_id'],
            'canonical_commit': pdata['canonical_commit'],
            'report_sha256': pdata['report_sha256'],
            'confirmed_by': 'student01',
            'confirmed_at': '2026-11-27T13:00:00Z',
            'statement': 'Simulated confirmation',
            'evidence_mode': 'simulation',
        }), encoding='utf-8')
        pub = run_cli(
            self.repo, 'final', '--publish', '--person-id', 'student01',
            '--candidate-id', pdata['candidate_id'], '--confirmation-file', str(conf),
            '--confirm-human',
        )
        self.assertEqual(pub.returncode, 1)
        self.assertIn('New open blocker', pub.stdout)


if __name__ == '__main__':
    unittest.main()
