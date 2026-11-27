"""PUB preflight CLI coverage using disposable local Git repositories."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

import test_git as fixtures

CLI = Path(__file__).resolve().parents[1] / 'groupctl.py'
BRANCH = 'people/P01/parser'


class PubTests(unittest.TestCase):
    git = fixtures.GitTests.git
    commit = fixtures.GitTests.commit

    def setUp(self):
        fixtures.GitTests.setUp(self)
        group = self.root / '.group'
        (group / 'members').mkdir(parents=True)
        self.member = group / 'members/P01.json'
        self.member.write_text(json.dumps(dict(protocol_version='0.1', person_id='P01',
                                                display_name='Fixture', joined_at='2026-11-14T00:00:00Z')),
                               encoding='utf-8')
        (group / 'group.json').write_text(json.dumps(dict(protocol_version='0.1', group_id='G-test',
                                                          canonical_branch='main', assignment_file='assignment.md')),
                                         encoding='utf-8')
        self.base = self.commit('group metadata')
        self.git('switch', '-c', BRANCH)

    def snapshot(self):
        # Include HEAD, index, refs/reflogs, object storage, and every fixture file byte for byte.
        files = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        refs = self.git('for-each-ref', '--format=%(refname) %(objectname)')
        return files, refs

    def cli(self, *args, expected=0):
        before = self.snapshot()
        result = subprocess.run([sys.executable, '-B', str(CLI), 'pub', *args],
                                cwd=self.root / '.group', env=self.env, capture_output=True,
                                text=True, encoding='utf-8', timeout=30)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(result.returncode, expected, result.stderr or result.stdout)
        return result

    def report(self, expected=0, person='P01'):
        result = self.cli('--preflight', '--person-id', person, expected=expected)
        report = json.loads(result.stdout)
        self.assertEqual(report['result'], 'blocked' if expected else 'review_required')
        self.assertEqual(bool(report['blockers']), bool(expected))
        return report

    def test_personal_branch_and_evidence_limits(self):
        report = self.report()
        facts = report['facts']
        self.assertEqual(facts['group_id'], 'G-test')
        self.assertEqual(facts['person_id'], 'P01')
        self.assertEqual(facts['member_descriptor'], '.group/members/P01.json')
        self.assertEqual(facts['head'], self.base)
        self.assertEqual(facts['branch'], BRANCH)
        self.assertTrue(facts['branch_owner_matches'])
        self.assertFalse(facts['on_canonical'])
        self.assertIsNone(facts['ahead'])
        self.assertEqual(facts['comparison']['source'], 'local_canonical')
        self.assertEqual(facts['comparison']['net_changed_paths'], [])
        self.assertIn('No secret scan', report['unverified']['secrets'])
        self.assertIn('No tests run', report['unverified']['tests'])
        self.assertIn('unverified', report['unverified']['permissions'])

    def test_dirty_lists_every_candidate_without_selecting(self):
        self.file.write_text('staged\n', encoding='utf-8')
        (self.root / 'extra.txt').write_text('other work\n', encoding='utf-8')
        self.git('add', 'tracked.txt', 'extra.txt')
        self.file.write_text('unstaged\n', encoding='utf-8')
        (self.root / '新 檔.txt').write_text('pending\n', encoding='utf-8')
        facts = self.report()['facts']
        self.assertEqual(facts['staged'], ['extra.txt', 'tracked.txt'])
        self.assertEqual(facts['unstaged'], ['tracked.txt'])
        self.assertEqual(facts['untracked'], ['新 檔.txt'])

    def test_local_upstream_and_commit_range(self):
        self.git('remote', 'add', 'origin', str(self.root / 'offline.git'))
        ref = 'refs/remotes/origin/' + BRANCH
        self.git('update-ref', ref, self.base)
        self.git('branch', '--set-upstream-to=origin/' + BRANCH)
        self.file.write_text('local\n', encoding='utf-8')
        head = self.commit('local')
        facts = self.report()['facts']
        self.assertEqual((facts['upstream'], facts['ahead'], facts['behind']), (ref, 1, 0))
        self.assertEqual(facts['comparison']['history_range'], f'{self.base}..{head}')
        self.assertEqual(facts['comparison']['net_changed_paths'], ['tracked.txt'])
        self.git('update-ref', '-d', ref)
        facts = self.report()['facts']
        self.assertEqual(facts['upstream'], ref)
        self.assertFalse(facts['upstream_ref_available'])
        self.assertIsNone(facts['ahead'])

    def test_wrong_or_missing_member(self):
        report = self.report(expected=1, person='P02')
        self.assertIn('person_id has no registered member descriptor.', report['blockers'])
        self.assertIn('Personal branch owner differs from person_id.', report['blockers'])
        self.git('branch', '-m', 'people/P02/parser')
        self.assertFalse(self.report(expected=1)['facts']['branch_owner_matches'])

    def test_invalid_member(self):
        self.member.write_text('{', encoding='utf-8')
        report = self.report(expected=1)
        self.assertIsNone(report['facts']['member_descriptor'])
        self.assertIn('Invalid member', report['blockers'][0])

    def test_canonical_and_detached(self):
        self.git('switch', 'main')
        self.assertTrue(self.report(expected=1)['facts']['on_canonical'])
        self.git('switch', '--detach')
        report = self.report(expected=1)
        self.assertIsNone(report['facts']['branch'])
        self.assertIn('Detached HEAD', report['blockers'][0])

    def test_conflict_and_merge_in_progress(self):
        self.git('switch', 'main')
        self.file.write_text('other side\n', encoding='utf-8')
        self.commit('other side')
        self.git('switch', BRANCH)
        self.file.write_text('personal side\n', encoding='utf-8')
        self.commit('personal side')
        merge = subprocess.run(['git', 'merge', 'main'], cwd=self.root, env=self.env,
                               capture_output=True, timeout=30)
        self.assertEqual(merge.returncode, 1)
        report = self.report(expected=1)
        self.assertEqual(report['facts']['unmerged_paths'], ['tracked.txt'])
        self.assertIn('MERGE_HEAD', report['facts']['git_operations'])
        self.git('add', 'tracked.txt')
        facts = self.report(expected=1)['facts']
        self.assertEqual(facts['unmerged_paths'], [])
        self.assertIn('MERGE_HEAD', facts['git_operations'])

    def test_hidden_index_flags(self):
        for flag, tag in (('assume-unchanged', 'h'), ('skip-worktree', 'S')):
            with self.subTest(flag=flag):
                self.git('update-index', '--' + flag, 'tracked.txt')
                facts = self.report(expected=1)['facts']
                self.assertEqual(facts['hidden_index_flags'], [{'path': 'tracked.txt', 'flag': tag}])
                self.git('update-index', '--no-' + flag, 'tracked.txt')

    def test_unborn_head_and_required_arguments(self):
        self.cli('--person-id', 'P01', expected=2)
        self.git('symbolic-ref', 'HEAD', 'refs/heads/people/P01/unborn')
        result = self.cli('--preflight', '--person-id', 'P01', expected=1)
        self.assertIn('HEAD^{commit}', result.stderr)

    def test_index_lock_blocks_without_removing_it(self):
        (self.root / '.git/index.lock').write_bytes(b'fixture lock')
        facts = self.report(expected=1)['facts']
        self.assertTrue(facts['index_lock_present'])

    def test_status_does_not_run_a_required_external_filter(self):
        (self.root / '.gitattributes').write_text('tracked.txt filter=fixture\n', encoding='utf-8')
        self.commit('configure filter attribute')
        self.git('config', 'filter.fixture.clean', 'fixture-command-that-does-not-exist')
        self.git('config', 'filter.fixture.required', 'true')
        self.file.write_text('modified after filter configuration\n', encoding='utf-8')
        facts = self.report()['facts']
        self.assertTrue(facts['external_filters_disabled'])
        self.assertEqual(facts['unstaged'], ['tracked.txt'])
