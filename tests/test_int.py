"""INT preflight tests using temporary Git repositories and the real CLI."""
import json
import subprocess
import sys
import unittest

from test_events import sample
import test_pub


TARGET = test_pub.BRANCH
SOURCE = 'people/P02/work'
SOURCE_REF = 'refs/remotes/origin/' + SOURCE


class IntPreflightTests(unittest.TestCase):
    git = test_pub.PubTests.git
    commit = test_pub.PubTests.commit
    snapshot = test_pub.PubTests.snapshot

    def setUp(self):
        test_pub.PubTests.setUp(self)
        member = json.loads(self.member.read_text(encoding='utf-8'))
        member.update(person_id='P02')
        self.member.with_name('P02.json').write_text(json.dumps(member), encoding='utf-8')
        (self.root / '.gitignore').write_bytes(b'.group/local/\n')
        self.base = self.commit('register publisher')
        self.git('update-ref', 'refs/heads/main', self.base)
        self.git('switch', '-c', SOURCE)
        self.file.write_bytes(b'published first change\n')
        self.commit('first work commit')
        (self.root / 'second.txt').write_bytes(b'published second change\n')
        self.work = self.commit('second work commit')
        self.event_dir = self.root / '.group/events'
        self.event_dir.mkdir()
        self.event = dict(sample('change_published'), event_id='E-int', group_id='G-test',
                          person_id='P02', change_id='C-int', branch=SOURCE,
                          commit=self.work, files_changed=['tracked.txt', 'second.txt'])
        self.tip = self.record(self.event)
        self.git('update-ref', SOURCE_REF, self.tip)
        self.git('switch', TARGET)

    def record(self, event, filename=None):
        path = self.event_dir / (filename or event['event_id'] + '.json')
        path.write_text(json.dumps(event), encoding='utf-8')
        return self.commit('publication fixture')

    def replace_publication(self, **fields):
        self.git('switch', SOURCE)
        self.tip = self.record(dict(self.event, **fields))
        self.git('update-ref', SOURCE_REF, self.tip)
        self.git('switch', TARGET)

    def run_preflight(self, status='review_required', *, target=TARGET, person='P01'):
        before = self.snapshot()
        result = subprocess.run(
            [sys.executable, '-B', str(test_pub.CLI), 'int', '--preflight', '--person-id', person,
             '--change-id', 'C-int', '--target-branch', target],
            cwd=self.root / '.group', env=self.env, capture_output=True, text=True,
            encoding='utf-8', timeout=45)
        self.assertEqual(self.snapshot(), before)
        expected = int(status not in ('review_required', 'history_contains_candidate'))
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], status, report)
        self.assertEqual(bool(report['blockers']), bool(expected), report)
        return report

    def test_unique_multi_commit_candidate_with_advanced_source_and_old_receipt(self):
        cache = self.root / '.group/local/sync-discover.json'
        cache.parent.mkdir()
        cache.write_text(json.dumps({'seen': {'E-int': json.dumps(self.event)},
                                     'refs': {SOURCE_REF: self.tip}}), encoding='utf-8')
        self.git('remote', 'add', 'origin', str(self.root / 'unavailable.git'))
        report = self.run_preflight()
        blob = self.git('rev-parse', f'{self.tip}:.group/events/E-int.json')
        self.assertEqual(report['candidate_event'], self.event)
        self.assertEqual(report['candidate_work_sha'], self.work)
        self.assertEqual(report['target_sha'], self.base)
        self.assertEqual(report['target_kind'], 'personal')
        self.assertEqual(report['source_refs'], [dict(ref=SOURCE_REF, tip_sha=self.tip,
                                                     event_path='.group/events/E-int.json',
                                                     event_blob_sha=blob)])
        self.assertEqual(report['event_blob_sha'], blob)
        ancestry = report['ancestry_evidence']
        self.assertTrue(ancestry['work_is_ancestor_of_source'])
        self.assertNotEqual(ancestry['source_tip_sha'], self.work)
        self.assertFalse(ancestry['work_is_ancestor_of_target'])
        self.assertEqual(ancestry['target_work_merge_bases'], [self.base])
        self.assertIn('may be stale', report['unverified']['remote_state'])
        self.assertIn('not verified', report['unverified']['contribution_scope'])

    def test_identical_event_on_multiple_refs_is_one_candidate(self):
        self.git('update-ref', 'refs/remotes/origin/main', self.tip)
        report = self.run_preflight()
        self.assertEqual(report['event_id'], 'E-int')
        self.assertEqual({item['ref'] for item in report['source_refs']},
                         {SOURCE_REF, 'refs/remotes/origin/main'})

    def test_same_change_id_has_conflicting_events(self):
        self.git('switch', SOURCE)
        tip = self.record(dict(self.event, event_id='E-other', commit=self.base))
        self.git('update-ref', SOURCE_REF, tip)
        self.git('switch', TARGET)
        report = self.run_preflight('candidate_conflict')
        self.assertEqual(len(report['conflicting_candidates']), 2)
        self.assertIsNone(report['candidate_work_sha'])

    def test_same_event_id_with_different_blob_is_conflicting(self):
        self.git('update-ref', 'refs/remotes/origin/main', self.tip)
        self.replace_publication(change_id='C-other')
        report = self.run_preflight('candidate_conflict')
        self.assertEqual(len(report['conflicting_candidates']), 2)

    def test_missing_candidate_commit(self):
        self.replace_publication(commit='0' * len(self.work))
        report = self.run_preflight('insufficient_evidence')
        self.assertIn('missing', ' '.join(report['blockers']))

    def test_candidate_object_is_not_commit(self):
        self.replace_publication(commit=self.git('rev-parse', f'{self.work}:second.txt'))
        report = self.run_preflight('insufficient_evidence')
        self.assertIn('not a Git commit', ' '.join(report['blockers']))

    def test_abbreviated_work_sha_resolves_to_full_commit(self):
        self.replace_publication(commit=self.work[:12])
        self.assertEqual(self.run_preflight()['candidate_work_sha'], self.work)

    def test_work_is_not_reachable_from_declared_source(self):
        self.file.write_bytes(b'local only\n')
        local = self.commit('unreachable work')
        self.replace_publication(commit=local)
        report = self.run_preflight('insufficient_evidence')
        self.assertFalse(report['ancestry_evidence']['work_is_ancestor_of_source'])

    def test_source_ref_must_exist_even_if_event_is_on_canonical(self):
        self.git('update-ref', 'refs/remotes/origin/main', self.tip)
        self.git('update-ref', '-d', SOURCE_REF)
        report = self.run_preflight('insufficient_evidence')
        self.assertIn('no direct origin tracking ref', ' '.join(report['blockers']))

    def test_event_path_group_and_source_author_are_checked(self):
        for fields, detail in (
            (dict(event_id='E-wrong'), 'event ID does not match filename'),
            (dict(group_id='G-other'), 'group_id differs'),
            (dict(person_id='P01'), 'own the declared source branch'),
        ):
            with self.subTest(fields=fields):
                self.git('switch', SOURCE)
                path = self.event_dir / 'E-int.json'
                path.write_text(json.dumps(dict(self.event, **fields)), encoding='utf-8')
                tip = self.commit('replace publication fixture')
                self.git('update-ref', SOURCE_REF, tip)
                self.git('switch', TARGET)
                report = self.run_preflight('insufficient_evidence')
                self.assertIn(detail, ' '.join(report['blockers']))

    def test_target_history_already_contains_work(self):
        self.git('merge', '--ff-only', self.work)
        report = self.run_preflight('history_contains_candidate')
        self.assertTrue(report['ancestry_evidence']['work_is_ancestor_of_target'])
        self.assertIn('Not assessed', report['unverified']['semantic_integration'])

    def test_missing_target_branch(self):
        report = self.run_preflight('target_unsafe', target='people/P01/missing')
        self.assertIsNone(report['target_sha'])

    def test_dirty_index_worktree_and_untracked(self):
        (self.root / 'staged.txt').write_bytes(b'staged\n')
        self.git('add', 'staged.txt')
        self.file.write_bytes(b'unstaged\n')
        (self.root / 'extra.txt').touch()
        report = self.run_preflight('target_unsafe')
        self.assertEqual(report['worktree']['staged'], ['staged.txt'])
        self.assertEqual(report['worktree']['unstaged'], ['tracked.txt'])
        self.assertEqual(report['worktree']['untracked'], ['extra.txt'])

    def test_active_merge_and_unmerged_index(self):
        self.file.write_bytes(b'conflicting local work\n')
        self.commit('conflicting local work')
        result = subprocess.run(['git', 'merge', '--no-commit', self.work], cwd=self.root,
                                env=self.env, capture_output=True)
        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        report = self.run_preflight('target_unsafe')
        self.assertIn('MERGE_HEAD', report['worktree']['git_operations'])
        self.assertEqual(report['worktree']['unmerged_paths'], ['tracked.txt'])

    def test_index_lock_and_hidden_flag(self):
        self.git('update-index', '--assume-unchanged', 'tracked.txt')
        (self.root / '.git/index.lock').touch()
        report = self.run_preflight('target_unsafe')
        self.assertTrue(report['worktree']['index_lock_present'])
        self.assertEqual(report['worktree']['hidden_index_flags'][0]['path'], 'tracked.txt')

    def test_canonical_target_reports_sha_and_preserves_refs(self):
        report = self.run_preflight('target_unsafe', target='main')
        self.assertEqual(report['target_sha'], self.base)
        self.assertEqual(report['target_kind'], 'canonical')
        self.git('switch', 'main')
        report = self.run_preflight(target='main')
        self.assertEqual(report['target_sha'], self.base)
        self.assertEqual(report['target_kind'], 'canonical')

    def test_personal_target_must_belong_to_operator(self):
        self.run_preflight('target_unsafe', person='P02')
