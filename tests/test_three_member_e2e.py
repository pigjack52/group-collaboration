"""15-step three-member end-to-end acceptance test (specs/00 section 7)."""
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


class ThreeMemberAcceptanceTests(unittest.TestCase):
    """Reproducible 3-clone + bare remote 15-step demonstration with simulation tagging."""

    def test_fifteen_step_three_member_workflow_and_fresh_clone_recovery(self):
        with TemporaryDirectory(prefix='groupctl-3member-') as tmp:
            base = Path(tmp)
            remote = base / 'remote.git'
            git(base, 'init', '--bare', '-b', 'main', str(remote))

            # Seed repository with three registered members and real project tests.
            seed = base / 'seed'
            git(base, 'clone', str(remote), str(seed))
            git(seed, 'config', 'user.name', 'Seed')
            git(seed, 'config', 'user.email', 'seed@example.com')
            (seed / '.gitignore').write_text('.group/local/*\n!.group/local/.gitkeep\n', encoding='utf-8')
            (seed / '.group/assignment').mkdir(parents=True)
            (seed / '.group/members').mkdir(parents=True)
            (seed / '.group/events').mkdir(parents=True)
            (seed / '.group/final').mkdir(parents=True)
            (seed / '.group/local').mkdir(parents=True)
            (seed / '.group/local/.gitkeep').write_text('', encoding='utf-8')
            (seed / '.group/group.json').write_text(json.dumps({
                'protocol_version': '0.1',
                'group_id': 'group-demo',
                'canonical_branch': 'main',
                'assignment_file': '.group/assignment/assignment.md',
                'repository': str(remote),
            }, indent=2) + '\n', encoding='utf-8')
            (seed / '.group/assignment/assignment.md').write_text(
                '# Three-Member Parser & Renderer Assignment\n', encoding='utf-8')
            for pid, name in (('P17', 'Student A'), ('P22', 'Student B'), ('P31', 'Student C')):
                (seed / f'.group/members/{pid}.json').write_text(json.dumps({
                    'protocol_version': '0.1', 'person_id': pid,
                    'display_name': name, 'joined_at': '2026-11-13T07:47:10Z',
                }, indent=2) + '\n', encoding='utf-8')
            (seed / 'model.py').write_text('VERSION = 1\nFORMAT = "plain"\n', encoding='utf-8')
            (seed / 'tests').mkdir()
            (seed / 'tests/test_model.py').write_text(
                'import unittest\nimport model\n\n'
                'class ModelTests(unittest.TestCase):\n'
                '    def test_version(self):\n'
                '        self.assertGreaterEqual(model.VERSION, 1)\n',
                encoding='utf-8',
            )
            git(seed, 'add', '.')
            git(seed, 'commit', '-m', 'Initial group project')
            git(seed, 'push', '-u', 'origin', 'main')
            main_init = git(seed, 'rev-parse', 'HEAD')

            # Steps 1-2: Three independent clones with separate personal branches.
            clones = {}
            branches = {'P17': 'people/P17/parser', 'P22': 'people/P22/renderer', 'P31': 'people/P31/model-v2'}
            for pid, br in branches.items():
                cdir = base / f'clone_{pid}'
                git(base, 'clone', str(remote), str(cdir))
                git(cdir, 'config', 'user.name', pid)
                git(cdir, 'config', 'user.email', f'{pid}@example.com')
                git(cdir, 'checkout', '-b', br, 'main')
                clones[pid] = cdir

            # Steps 3-4: P17 implements parser.py and publishes C-01 via full PUB workflow.
            p17 = clones['P17']
            (p17 / 'parser.py').write_text('def parse(text):\n    return text.strip().split()\n', encoding='utf-8')
            wc = run_cli(
                p17, 'pub', '--work-commit', '--person-id', 'P17',
                '--expected-branch', branches['P17'], '--expected-head', main_init,
                '--file', 'parser.py', '--message', 'Add parser module', '--confirm-reviewed',
            )
            self.assertEqual(wc.returncode, 0, wc.stdout + wc.stderr)
            p17_work = json.loads(wc.stdout)['commit']
            pw = run_cli(
                p17, 'pub', '--push-work', '--person-id', 'P17',
                '--expected-branch', branches['P17'], '--expected-head', p17_work,
                '--remote', 'origin', '--expected-url', str(remote),
                '--expected-remote-head', 'absent', '--confirm-reviewed',
            )
            self.assertEqual(pw.returncode, 0, pw.stdout + pw.stderr)
            rev_file_17 = base / 'review_p17.json'
            rev_file_17.write_text(json.dumps({
                'summary': 'Add parser module.',
                'files_changed': ['parser.py'],
                'interface_changes': ['parse(text) -> list[str]'],
                'tests_run': ['unittest'],
                'known_issues': [],
                'proposed_for_integration': True,
            }), encoding='utf-8')
            ec = run_cli(
                p17, 'pub', '--event-commit', '--person-id', 'P17',
                '--expected-branch', branches['P17'], '--work-sha', p17_work,
                '--expected-head', p17_work, '--remote', 'origin', '--expected-url', str(remote),
                '--change-id', 'C-01', '--review-file', str(rev_file_17), '--confirm-reviewed',
            )
            self.assertEqual(ec.returncode, 0, ec.stdout + ec.stderr)
            p17_ev_commit = json.loads(ec.stdout)['commit']
            pe = run_cli(
                p17, 'pub', '--push-event', '--person-id', 'P17',
                '--expected-branch', branches['P17'], '--work-sha', p17_work,
                '--expected-head', p17_ev_commit, '--change-id', 'C-01',
                '--remote', 'origin', '--expected-url', str(remote),
                '--expected-remote-head', p17_work, '--confirm-reviewed',
            )
            self.assertEqual(pe.returncode, 0, pe.stdout + pe.stderr)

            # P22 independently implements renderer.py and edits model.py (will later conflict with P31).
            p22 = clones['P22']
            (p22 / 'renderer.py').write_text('def render(tokens):\n    return " ".join(tokens)\n', encoding='utf-8')
            (p22 / 'model.py').write_text('VERSION = 2\nFORMAT = "markdown"\n', encoding='utf-8')
            git(p22, 'add', 'renderer.py', 'model.py')
            git(p22, 'commit', '-m', 'Add renderer and markdown format')
            git(p22, 'push', '-u', 'origin', branches['P22'])

            # Step 5: P22 runs SYNC --discover and discovers P17's C-01.
            sync_p22 = run_cli(p22, 'sync', '--discover')
            self.assertEqual(sync_p22.returncode, 0, sync_p22.stdout + sync_p22.stderr)
            self.assertIn('C-01', sync_p22.stdout)

            # Step 6: P22 runs REV on C-01 (positive review, must NOT auto-merge).
            rev_insp = run_cli(p22, 'rev', '--inspect', '--person-id', 'P22', '--change-id', 'C-01')
            self.assertEqual(rev_insp.returncode, 0, rev_insp.stdout + rev_insp.stderr)
            rev_pub = run_cli(
                p22, 'rev', '--publish', '--person-id', 'P22', '--change-id', 'C-01',
                '--inspected-commit', p17_work, '--diff-inspected', '--verdict', 'accept',
                '--summary', 'Parser API is clean.', '--remote', 'origin',
                '--expected-url', str(remote), '--confirm-reviewed',
            )
            self.assertEqual(rev_pub.returncode, 0, rev_pub.stdout + rev_pub.stderr)
            self.assertFalse((p22 / 'parser.py').exists())

            # Steps 7-8: P22 integrates C-01 cleanly right after SYNC (keeping sync-discover.json cache intact).
            pre1 = json.loads(run_cli(
                p22, 'int', '--preflight', '--person-id', 'P22',
                '--change-id', 'C-01', '--target-branch', branches['P22'],
            ).stdout)
            ev1 = [
                '--person-id', 'P22', '--change-id', 'C-01', '--target-branch', branches['P22'],
                '--expected-head', pre1['target_sha'], '--work-sha', pre1['candidate_work_sha'],
                '--expected-event-id', pre1['event_id'], '--expected-event-blob', pre1['event_blob_sha'],
                '--expected-source-ref', pre1['ancestry_evidence']['source_ref'],
                '--expected-source-tip', pre1['ancestry_evidence']['source_tip_sha'],
                '--expected-incoming', ','.join(pre1['incoming_commits']) or 'none',
                '--confirm-reviewed',
            ]
            prep1 = run_cli(p22, 'int', '--prepare-merge', *ev1)
            self.assertEqual(prep1.returncode, 0, prep1.stdout + prep1.stderr)
            tree1 = json.loads(run_cli(p22, 'int', '--inspect-merge', *ev1).stdout)['staged_tree_oid']
            cm1 = json.loads(run_cli(
                p22, 'int', '--commit-merge', *ev1,
                '--expected-tree', tree1, '--message', 'Integrate C-01 cleanly',
            ).stdout)
            self.assertEqual(cm1['status'], 'local_merge_committed')
            ver1 = json.loads(run_cli(
                p22, 'int', '--verify', '--person-id', 'P22',
                '--change-id', 'C-01', '--target-branch', branches['P22'],
                '--merge-commit', cm1['merge_commit'],
            ).stdout)
            self.assertEqual(ver1['verification']['tests'], 'pass')
            self.assertEqual(ver1['verification']['semantic_review'], 'not_performed')
            ir1 = run_cli(
                p22, 'int', '--publish-result', '--person-id', 'P22',
                '--change-id', 'C-01', '--target-branch', branches['P22'],
                '--merge-commit', cm1['merge_commit'], '--result', 'success',
                '--build', 'not_applicable', '--tests', 'pass', '--semantic-review', 'pass',
                '--summary', 'C-01 integrated without conflict; parser + renderer compatible.',
                '--remote', 'origin', '--expected-url', str(remote), '--confirm-reviewed',
            )
            self.assertEqual(ir1.returncode, 0, ir1.stdout + ir1.stderr)

            # Steps 9-10: P31 introduces deliberate conflict on model.py and publishes C-02.
            p31 = clones['P31']
            (p31 / 'model.py').write_text('VERSION = 3\nFORMAT = "json"\n', encoding='utf-8')
            wc31 = json.loads(run_cli(
                p31, 'pub', '--work-commit', '--person-id', 'P31',
                '--expected-branch', branches['P31'], '--expected-head', main_init,
                '--file', 'model.py', '--message', 'Update model to v3 json', '--confirm-reviewed',
            ).stdout)
            p31_work = wc31['commit']
            self.assertEqual(run_cli(
                p31, 'pub', '--push-work', '--person-id', 'P31',
                '--expected-branch', branches['P31'], '--expected-head', p31_work,
                '--remote', 'origin', '--expected-url', str(remote),
                '--expected-remote-head', 'absent', '--confirm-reviewed',
            ).returncode, 0)
            rev_file_31 = base / 'review_p31.json'
            rev_file_31.write_text(json.dumps({
                'summary': 'Update model to v3 with json format.',
                'files_changed': ['model.py'],
                'interface_changes': ['model.VERSION=3', 'model.FORMAT=json'],
                'tests_run': ['unittest'],
                'known_issues': [],
                'proposed_for_integration': True,
            }), encoding='utf-8')
            ec31 = json.loads(run_cli(
                p31, 'pub', '--event-commit', '--person-id', 'P31',
                '--expected-branch', branches['P31'], '--work-sha', p31_work,
                '--expected-head', p31_work, '--remote', 'origin', '--expected-url', str(remote),
                '--change-id', 'C-02', '--review-file', str(rev_file_31), '--confirm-reviewed',
            ).stdout)
            self.assertEqual(run_cli(
                p31, 'pub', '--push-event', '--person-id', 'P31',
                '--expected-branch', branches['P31'], '--work-sha', p31_work,
                '--expected-head', ec31['commit'], '--change-id', 'C-02',
                '--remote', 'origin', '--expected-url', str(remote),
                '--expected-remote-head', p31_work, '--confirm-reviewed',
            ).returncode, 0)

            # P22 discovers C-02, hits real Git conflict, inspects, resolves deliberately, commits, verifies, publishes.
            self.assertEqual(run_cli(p22, 'sync', '--discover').returncode, 0)
            pre2 = json.loads(run_cli(
                p22, 'int', '--preflight', '--person-id', 'P22',
                '--change-id', 'C-02', '--target-branch', branches['P22'],
            ).stdout)
            ev2 = [
                '--person-id', 'P22', '--change-id', 'C-02', '--target-branch', branches['P22'],
                '--expected-head', pre2['target_sha'], '--work-sha', pre2['candidate_work_sha'],
                '--expected-event-id', pre2['event_id'], '--expected-event-blob', pre2['event_blob_sha'],
                '--expected-source-ref', pre2['ancestry_evidence']['source_ref'],
                '--expected-source-tip', pre2['ancestry_evidence']['source_tip_sha'],
                '--expected-incoming', ','.join(pre2['incoming_commits']) or 'none',
                '--confirm-reviewed',
            ]
            prep2 = run_cli(p22, 'int', '--prepare-merge', *ev2)
            self.assertEqual(prep2.returncode, 1, prep2.stdout + prep2.stderr)
            self.assertEqual(json.loads(prep2.stdout)['status'], 'conflicted', prep2.stdout)
            insp_conf = json.loads(run_cli(p22, 'int', '--inspect-merge', *ev2).stdout)
            self.assertEqual(insp_conf['status'], 'conflicted')
            self.assertEqual(insp_conf['git_state']['unmerged_paths'], ['model.py'])

            # Deliberate resolution preserving v3 + both formats.
            (p22 / 'model.py').write_text('VERSION = 3\nFORMAT = "json+markdown"\n', encoding='utf-8')
            git(p22, 'add', '--', 'model.py')
            insp_ok = json.loads(run_cli(p22, 'int', '--inspect-merge', *ev2).stdout)
            self.assertEqual(insp_ok['status'], 'awaiting_human_confirmation')
            cm2 = json.loads(run_cli(
                p22, 'int', '--commit-merge', *ev2,
                '--expected-tree', insp_ok['staged_tree_oid'],
                '--message', 'Integrate C-02 with deliberate model.py resolution',
            ).stdout)
            self.assertEqual(cm2['status'], 'local_merge_committed')
            ver2 = json.loads(run_cli(
                p22, 'int', '--verify', '--person-id', 'P22',
                '--change-id', 'C-02', '--target-branch', branches['P22'],
                '--merge-commit', cm2['merge_commit'],
            ).stdout)
            self.assertEqual(ver2['verification']['tests'], 'pass')
            ir2 = run_cli(
                p22, 'int', '--publish-result', '--person-id', 'P22',
                '--change-id', 'C-02', '--target-branch', branches['P22'],
                '--merge-commit', cm2['merge_commit'], '--result', 'success',
                '--build', 'not_applicable', '--tests', 'pass', '--semantic-review', 'pass',
                '--conflict', 'model.py',
                '--conflict-resolution-summary', 'Kept VERSION=3 and combined json+markdown formats.',
                '--summary', 'C-02 conflict resolved and verified.',
                '--remote', 'origin', '--expected-url', str(remote), '--confirm-reviewed',
            )
            self.assertEqual(ir2.returncode, 0, ir2.stdout + ir2.stderr)

            # Steps 11-12: Run isolated TEST on combined software and publish test_result.
            t_res = run_cli(
                p22, 'test', '--person-id', 'P22', '--scope', 'current', '--isolated',
                '--publish', '--remote', 'origin', '--expected-url', str(remote), '--confirm-reviewed',
            )
            self.assertEqual(t_res.returncode, 0, t_res.stdout + t_res.stderr)
            self.assertTrue(json.loads(t_res.stdout)['verified'])

            # Step 13: Advance canonical main to the accepted integrated state.
            git(p22, 'checkout', 'main')
            git(p22, 'merge', '--no-ff', branches['P22'], '-m', 'Accept integrated work into canonical main')
            git(p22, 'push', 'origin', 'main')

            # Steps 14-15: Third member (P31) initiates FINAL on canonical main with simulation confirmation.
            git(p31, 'fetch', 'origin')
            git(p31, 'checkout', 'main')
            git(p31, 'merge', '--ff-only', 'origin/main')
            fprep = run_cli(
                p31, 'final', '--prepare', '--person-id', 'P31',
                '--result-summary', 'Three-member parser, renderer, and v3 model integrated.',
                '--simulation',
            )
            self.assertEqual(fprep.returncode, 0, fprep.stdout + fprep.stderr)
            fpdata = json.loads(fprep.stdout)
            self.assertEqual(fpdata['unresolved_concerns'], [])
            self.assertEqual(sorted(fpdata['incorporated_changes']), ['C-01', 'C-02'])

            conf = base / 'sim_confirm.json'
            conf.write_text(json.dumps({
                'candidate_id': fpdata['candidate_id'],
                'canonical_commit': fpdata['canonical_commit'],
                'report_sha256': fpdata['report_sha256'],
                'confirmed_by': 'P31',
                'confirmed_at': '2026-11-27T12:30:00Z',
                'statement': 'Simulated confirmation for 15-step E2E acceptance test.',
                'evidence_mode': 'simulation',
            }), encoding='utf-8')
            fpub = run_cli(
                p31, 'final', '--publish', '--person-id', 'P31',
                '--candidate-id', fpdata['candidate_id'], '--confirmation-file', str(conf),
                '--confirm-human', '--remote', 'origin', '--expected-url', str(remote),
            )
            self.assertEqual(fpub.returncode, 0, fpub.stdout + fpub.stderr)
            fpub_data = json.loads(fpub.stdout)
            self.assertEqual(fpub_data['status'], 'published_verified')
            self.assertEqual(fpub_data['evidence_mode'], 'simulation')

            # Fresh-clone recovery: a 4th clean clone recovers full group state without private chat.
            fresh = base / 'fresh_clone'
            git(base, 'clone', str(remote), str(fresh))
            self.assertTrue((fresh / '.group/final/final_report.md').is_file())
            final_meta = json.loads((fresh / '.group/final/final.json').read_text(encoding='utf-8'))
            self.assertEqual(final_meta['canonical_commit'], fpdata['canonical_commit'])
            self.assertEqual(final_meta['status'], 'completed')
            st = run_cli(fresh, 'status')
            self.assertEqual(st.returncode, 0, st.stdout + st.stderr)
            self.assertIn('final_result=1', st.stdout)
            self.assertIn('integration_result=2', st.stdout)
            self.assertIn('review=1', st.stdout)
            self.assertIn('test_result=1', st.stdout)


if __name__ == '__main__':
    unittest.main()
