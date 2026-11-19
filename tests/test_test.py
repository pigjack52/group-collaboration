"""TEST evidence checks, confined to disposable repositories; no network."""
import contextlib
import io
import json
import platform
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import group_test
from group_events import EventError, load_event
from group_git import GitError
import test_git as fixtures

CLI = Path(__file__).resolve().parents[1] / 'groupctl.py'


class TestCommandTests(unittest.TestCase):
    git = fixtures.GitTests.git
    commit = fixtures.GitTests.commit

    def setUp(self):
        fixtures.GitTests.setUp(self)
        self.config = dict(protocol_version='0.1', group_id='G-test',
                           canonical_branch='main', assignment_file='assignment.md')
        group = self.root / '.group'
        (group / 'members').mkdir(parents=True)
        (group / 'group.json').write_text(json.dumps(self.config), encoding='utf-8')
        (group / 'members/P01.json').write_text(json.dumps(dict(
            protocol_version='0.1', person_id='P01', display_name='Tester',
            joined_at='2026-11-14T00:00:00Z')), encoding='utf-8')
        self.suite('self.assertTrue(True)')

    def suite(self, body):
        (self.root / 'tests').mkdir(exist_ok=True)
        (self.root / 'tests/test_example.py').write_text(
            'import unittest\nclass Example(unittest.TestCase):\n'
            ' def test_example(self):\n  ' + body + '\n', encoding='utf-8')
        self.head = self.commit('fixture suite')

    def cli(self, *args, cwd=None):
        return subprocess.run([sys.executable, '-B', str(CLI), 'test', *args],
                              cwd=cwd or self.root, capture_output=True,
                              text=True, encoding='utf-8', timeout=30)

    def events(self):
        return [load_event(p) for p in (self.root / '.group/events').glob('*.json')]

    def status(self):
        before = self.git('status', '--porcelain')
        result = subprocess.run([sys.executable, '-B', str(CLI), 'status'],
                                cwd=self.root, capture_output=True, text=True,
                                encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git('status', '--porcelain'), before)
        return result.stdout

    def direct(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = group_test.run_tests(self.root, self.config, 'P01')
        return code, output.getvalue()

    def runner_result(self, payload, returncode):
        def execute(command, root):
            if payload is not None:
                Path(command[-1]).write_text(payload, encoding='utf-8')
            return subprocess.CompletedProcess(command, returncode, b'', b'')
        return execute

    def test_success_real_cli_and_evidence(self):
        before_refs = self.git('show-ref')
        result = self.cli('--person-id', 'P01', cwd=self.root / 'tests')
        self.assertEqual(result.returncode, 0, result.stderr)
        event, = self.events()
        self.assertEqual((event['person_id'], event['group_id'], event['commit']),
                         ('P01', 'G-test', self.head))
        self.assertEqual((event['automated_result'], event['passed'], event['failed'], event['skipped']),
                         ('pass', 1, 0, 0))
        self.assertEqual(event['execution']['argv'][0], '<python-executable>')
        self.assertEqual(event['execution']['argv'][1], '-B')
        self.assertEqual(event['execution']['argv'][3], '--unittest-runner')
        self.assertEqual(event['commands'], [subprocess.list2cmdline(event['execution']['argv'])])
        self.assertTrue(event['execution']['started'])
        self.assertTrue(event['execution']['completed'])
        self.assertTrue(event['execution']['successful'])
        self.assertTrue(event['execution']['state_verified'])
        self.assertEqual(event['execution']['ran'], 1)
        evidence = event['execution']
        self.assertEqual(evidence['counts'], dict(status='observed', ran=1, passed=1, failed=0, skipped=0))
        self.assertEqual(evidence['environment']['python_version'], platform.python_version())
        self.assertEqual(evidence['environment']['os_system'], platform.system())
        self.assertEqual(evidence['environment']['framework'], 'unittest')
        self.assertRegex(evidence['environment']['runner_sha256'], r'^[a-f0-9]{64}$')
        self.assertEqual(evidence['diagnostics']['entries'], [])
        self.assertFalse(evidence['output']['summary_truncated'])
        self.assertIn('redacted', event['execution']['command_representation'])
        self.assertIn('本機事件已建立，尚未 Commit／Push', result.stdout)
        self.assertIn('NOT PERFORMED', result.stdout)
        self.assertEqual(self.git('show-ref'), before_refs)
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.head)
        self.assertEqual(self.git('diff', '--cached'), '')
        self.assertIn('.group/events/', self.git('status', '--short'))
        status = self.status()
        for evidence_line in (
            f"Tests for exact HEAD commit: {event['event_id']}=pass",
            "Git state: verified by recorded pre/post snapshots",
            "Counts: observed; ran=1; passed=1; failed=0; skipped=0",
            "Environment: recorded in event; diagnostics: recorded in event",
            "AI semantic review: not performed; full TEST completion: not established",
            "Human validation: assessment unknown",
            "Publication: unconfirmed (local event; remote content not checked)",
        ):
            self.assertIn(evidence_line, status)

    def test_assertion_failure(self):
        self.suite('self.assertEqual(1, 2)')
        result = self.cli('--person-id', 'P01')
        self.assertEqual(result.returncode, 1, result.stderr)
        event, = self.events()
        self.assertEqual((event['automated_result'], event['passed'], event['failed']), ('fail', 0, 1))
        self.assertIn('AssertionError', event['summary'])
        entry, = event['execution']['diagnostics']['entries']
        self.assertEqual(entry, dict(kind='assertion_failure',
                                     test_id='test_example.Example.test_example',
                                     exception_type='builtins.AssertionError',
                                     message='1 != 2', truncated_fields=[]))
        status = self.status()
        self.assertIn(f"Tests for exact HEAD commit: {event['event_id']}=fail", status)
        self.assertIn("Failure: observed by runner; root cause undetermined by STATUS", status)
        self.assertIn('"exception_type": "builtins.AssertionError"', status)

    def test_error(self):
        self.suite("raise RuntimeError('fixture error')")
        self.assertEqual(self.cli('--person-id', 'P01').returncode, 1)
        event, = self.events()
        self.assertEqual((event['automated_result'], event['failed']), ('fail', 1))
        self.assertIn('RuntimeError', event['summary'])
        entry, = event['execution']['diagnostics']['entries']
        self.assertEqual((entry['kind'], entry['exception_type'], entry['message']),
                         ('error', 'builtins.RuntimeError', 'fixture error'))

    def test_skip_only_is_partial(self):
        self.suite("self.skipTest('fixture skip')")
        self.assertEqual(self.cli('--person-id', 'P01').returncode, 1)
        event, = self.events()
        self.assertEqual((event['automated_result'], event['skipped']), ('partial', 1))
        self.assertEqual(event['execution']['counts']['skipped'], 1)
        self.assertEqual(event['execution']['diagnostics']['total'], 0)
        self.assertIn(f"Tests for exact HEAD commit: {event['event_id']}=partial", self.status())

    def test_zero_tests(self):
        (self.root / 'tests/test_example.py').write_text('# empty\n', encoding='utf-8')
        self.commit('empty suite')
        self.assertEqual(self.cli('--person-id', 'P01').returncode, 1)
        event, = self.events()
        self.assertEqual(event['automated_result'], 'not_available')
        self.assertEqual(event['passed'], 0)
        self.assertIn('Zero tests', event['summary'])
        self.assertEqual(event['execution']['counts'],
                         dict(status='observed', passed=0, failed=0, skipped=0, ran=0))
        status = self.status()
        self.assertIn(f"Tests for exact HEAD commit: {event['event_id']}=not_available", status)
        self.assertIn("Counts: observed; ran=0; passed=0; failed=0; skipped=0", status)

    def test_cannot_start(self):
        with patch('group_test.execute', side_effect=OSError('cannot start fixture')):
            code, output = self.direct()
        self.assertEqual(code, 1)
        event, = self.events()
        self.assertEqual(event['automated_result'], 'not_available')
        self.assertFalse(event['execution']['counts_available'])
        self.assertFalse(event['execution']['started'])
        self.assertEqual(event['execution']['counts']['status'], 'unavailable')
        self.assertIsNone(event['execution']['counts']['ran'])
        self.assertIsNone(event['execution']['environment'])
        self.assertEqual(event['commands'], [])
        self.assertIn('cannot start fixture', event['summary'])

    def test_timeout(self):
        self.suite("import time; print('before timeout', flush=True); time.sleep(20)")
        with patch('group_test.TIMEOUT', 3):
            code, _ = self.direct()
        self.assertEqual(code, 1)
        self.assertEqual(self.events()[0]['automated_result'], 'partial')
        self.assertFalse(self.events()[0]['execution']['completed'])
        self.assertIn('timeout', self.events()[0]['summary'])
        evidence = self.events()[0]['execution']
        self.assertEqual(evidence['counts']['status'], 'unknown')
        self.assertIsNone(evidence['counts']['ran'])
        self.assertIsNone(evidence['diagnostics'])
        self.assertIn('before timeout', evidence['output']['stdout']['tail'])
        self.assertIn("Counts: unknown; actual case totals not established", self.status())

    def test_contradictory_runner_results(self):
        cases = ((1, True, 1, 'fail'), (1, False, 0, 'fail'),
                 (1, True, 0, 'fail'), (0, False, 0, 'partial'),
                 (0, True, 1, 'partial'), (0, None, 0, 'partial'))
        for index, (failed, successful, returncode, outcome) in enumerate(cases):
            with self.subTest(failed=failed, successful=successful, returncode=returncode):
                payload = json.dumps(dict(passed=1, failed=failed, skipped=0,
                                          ran=1 + failed, successful=successful))
                with patch('group_test.execute', side_effect=self.runner_result(payload, returncode)):
                    code, output = self.direct()
                event = json.JSONDecoder().raw_decode(output)[0]
                self.assertEqual(code, 1)
                stored = load_event(self.root / '.group/events' / (event['event_id'] + '.json'))
                self.assertEqual(stored, event)
                self.assertEqual((event['automated_result'], event['failed']), (outcome, failed))
                self.assertEqual(event['execution']['returncode'], returncode)
                self.assertIs(event['execution']['successful'], successful)
                self.assertIn('Inconsistent runner evidence', event['summary'])
                self.commit(f'contradiction fixture {index}')

    def test_missing_or_corrupt_counts(self):
        cases = (None, '{', '[]',
                 '{"passed":true,"failed":0,"skipped":0,"ran":1,"successful":true}',
                 '{"passed":1,"failed":0,"skipped":0,"ran":2,"successful":true}',
                 '{"passed":1,"failed":1,"failed":0,"skipped":0,"ran":1,"successful":true}',
                 '{"passed":1,"skipped":0,"ran":1,"successful":true}')
        for index, payload in enumerate(cases):
            with self.subTest(payload=payload):
                with patch('group_test.execute', side_effect=self.runner_result(payload, 0)):
                    code, output = self.direct()
                self.assertEqual(code, 1)
                event = json.JSONDecoder().raw_decode(output)[0]
                self.assertEqual(event['automated_result'], 'not_available')
                self.assertFalse(event['execution']['counts_available'])
                self.assertEqual(event['execution']['counts'],
                                 dict(status='unknown', passed=None, failed=None, skipped=None, ran=None))
                self.commit(f'invalid counts fixture {index}')

    def test_runner_exit_without_result(self):
        self.suite('import os; os._exit(7)')
        self.assertEqual(self.cli('--person-id', 'P01').returncode, 1)
        event, = self.events()
        self.assertEqual(event['automated_result'], 'not_available')
        self.assertEqual(event['execution']['returncode'], 7)
        self.assertEqual(event['execution']['counts']['status'], 'unknown')

    def test_runner_exception_preserves_environment_and_diagnostic(self):
        (self.root / 'tests/test_example.py').write_text(
            'def load_tests(*args):\n'
            ' class BrokenSuite:\n'
            '  def __call__(self, result): raise RuntimeError("suite escaped runner")\n'
            ' return BrokenSuite()\n', encoding='utf-8')
        self.commit('fixture runner exception')
        self.assertEqual(self.direct()[0], 1)
        event, = self.events()
        evidence = event['execution']
        self.assertEqual(event['automated_result'], 'not_available')
        self.assertEqual(evidence['counts']['status'], 'unknown')
        self.assertEqual(evidence['environment']['framework'], 'unittest')
        entry, = evidence['diagnostics']['entries']
        self.assertEqual((entry['kind'], entry['test_id'], entry['message']),
                         ('runner_error', None, 'suite escaped runner'))

    def test_output_and_diagnostics_limits_after_redaction(self):
        self.suite(
            "import sys\n"
            "  print('a' * 12001 + 'stdout end')\n"
            "  print('b' * 12001 + 'stderr end', file=sys.stderr)\n"
            "  for i in range(52):\n"
            "   with self.subTest(i=i):\n"
            "    self.fail(str(__file__) + ' ' + 'c' * 2200)"
        )
        self.assertEqual(self.direct()[0], 1)
        event, = self.events()
        evidence = event['execution']
        self.assertEqual(event['failed'], 1)
        for stream in ('stdout', 'stderr'):
            output = evidence['output'][stream]
            self.assertTrue(output['truncated'])
            self.assertEqual(len(output['tail']), evidence['output']['char_limit'])
        self.assertTrue(evidence['output']['combined_truncated'])
        self.assertTrue(evidence['output']['summary_truncated'])
        diagnostics = evidence['diagnostics']
        self.assertEqual((diagnostics['total'], len(diagnostics['entries'])), (52, 50))
        self.assertTrue(diagnostics['entries_truncated'])
        first = diagnostics['entries'][0]
        self.assertIn('(i=0)', first['test_id'])
        self.assertIn('<repository>', first['message'])
        self.assertEqual(len(first['message']), diagnostics['char_limit'])
        self.assertEqual(first['truncated_fields'], ['message'])

    def test_no_success_callback_cannot_pass(self):
        (self.root / 'tests/test_example.py').write_text(
            'import unittest\nclass Example(unittest.TestCase):\n'
            ' def test_example(self): pass\n'
            ' def run(self, result):\n'
            '  result.startTest(self)\n  result.stopTest(self)\n', encoding='utf-8')
        self.commit('runner without case outcome')
        self.assertEqual(self.cli('--person-id', 'P01').returncode, 1)
        event, = self.events()
        self.assertEqual(event['automated_result'], 'not_available')
        self.assertFalse(event['execution']['counts_available'])

    def test_dirty_refused_and_preserved(self):
        for kind in ('unstaged', 'staged', 'untracked'):
            with self.subTest(kind=kind):
                path = self.root / ('new.txt' if kind == 'untracked' else 'tracked.txt')
                path.write_text(kind, encoding='utf-8')
                if kind == 'staged':
                    self.git('add', 'tracked.txt')
                before = self.git('status', '--porcelain')
                result = self.cli('--person-id', 'P01')
                self.assertEqual(result.returncode, 1)
                self.assertIn('Dirty', result.stderr)
                self.assertEqual(self.git('status', '--porcelain'), before)
                self.assertEqual(self.events(), [])
                self.commit('fixture dirty case')

    def test_explicit_known_identity_required(self):
        for args, code in [((), 2), (('--person-id', 'unknown'), 1),
                           (('--person-id', 'P01', 'main'), 2)]:
            self.assertEqual(self.cli(*args).returncode, code)
            self.assertEqual(self.events(), [])

    def test_detached_and_unfinished_refused(self):
        for marker in group_test.MARKERS:
            location = self.root / '.git' / marker
            location.write_text(self.head, encoding='ascii')
            result = self.cli('--person-id', 'P01')
            self.assertEqual(result.returncode, 1)
            self.assertIn('Unfinished Git operation', result.stderr)
            location.unlink()
        self.git('checkout', '--detach')
        self.assertIn('Detached HEAD', self.cli('--person-id', 'P01').stderr)
        self.assertEqual(self.events(), [])

    def test_unique_ids_and_no_overwrite(self):
        self.assertEqual(self.direct()[0], 0)
        original, = (self.root / '.group/events').glob('*.json')
        original_bytes = original.read_bytes()
        self.commit('fixture event')
        self.assertEqual(self.direct()[0], 0)
        self.assertEqual(len(self.events()), 2)
        self.assertEqual(original.read_bytes(), original_bytes)
        event = load_event(original)
        with self.assertRaisesRegex(EventError, 'refusing to overwrite'):
            group_test.write_event(self.root, event)
        self.assertEqual(original.read_bytes(), original_bytes)

    def test_write_failure_not_success(self):
        output = io.StringIO()
        with patch('group_test.write_event', side_effect=EventError('fixture write failure')):
            with contextlib.redirect_stdout(output), self.assertRaises(EventError):
                group_test.run_tests(self.root, self.config, 'P01')
        self.assertNotIn('本機事件已建立', output.getvalue())
        self.assertEqual(self.events(), [])

    def test_real_cli_write_failure(self):
        (self.root / '.group/events').write_text('blocked directory', encoding='utf-8')
        self.commit('fixture blocked events')
        result = self.cli('--person-id', 'P01')
        self.assertEqual(result.returncode, 1)
        self.assertIn('refusing to overwrite', result.stderr)
        self.assertNotIn('本機事件已建立', result.stdout)
        self.assertEqual((self.root / '.group/events').read_text(), 'blocked directory')

    def test_missing_tests_directory(self):
        (self.root / 'tests/test_example.py').unlink()
        (self.root / 'tests').rmdir()
        self.commit('fixture no tests')
        self.assertEqual(self.direct()[0], 1)
        event, = self.events()
        self.assertEqual(event['automated_result'], 'not_available')
        self.assertEqual(event['commands'], [])

    def test_unborn_head_refused(self):
        self.git('checkout', '--orphan', 'empty')
        self.assertEqual(self.cli('--person-id', 'P01').returncode, 1)
        self.assertEqual(self.events(), [])

    def test_mixed_counts(self):
        (self.root / 'tests/test_example.py').write_text(
            'import unittest\nclass Example(unittest.TestCase):\n'
            ' def test_pass(self): pass\n'
            ' def test_fail(self): self.fail("bad")\n'
            ' def test_error(self): raise ValueError("error")\n'
            ' def test_skip(self): self.skipTest("skip")\n', encoding='utf-8')
        self.commit('fixture mixed')
        self.assertEqual(self.direct()[0], 1)
        event, = self.events()
        self.assertEqual((event['passed'], event['failed'], event['skipped']), (1, 2, 1))

    def test_content_changes_in_test_invalidate_pass(self):
        self.suite("from pathlib import Path; Path('tracked.txt').write_text('changed')")
        self.assertEqual(self.cli('--person-id', 'P01').returncode, 1)
        event, = self.events()
        self.assertEqual(event['automated_result'], 'partial')
        self.assertFalse(event['execution']['state_verified'])
        self.assertEqual((self.root / 'tracked.txt').read_text(), 'changed')
        status = self.status()
        self.assertIn("Git state: verification failed; commit attribution unconfirmed", status)
        self.assertIn("Tests for exact HEAD commit: no attributable result; unknown", status)

    def test_status_does_not_promote_personal_or_locally_committed_evidence(self):
        branch = 'people/P01/topic'
        self.git('switch', '-c', branch)
        self.assertEqual(self.cli('--person-id', 'P01').returncode, 0)
        event, = self.events()
        status = self.status()
        self.assertIn(f"Tests for exact HEAD commit: {event['event_id']}=pass", status)
        self.assertIn("Tests for exact canonical commit: no attributable result; unknown", status)

        local_tip = self.commit('record local evidence')
        self.git('remote', 'add', 'origin', str(self.root / 'offline.git'))
        self.git('update-ref', f'refs/remotes/origin/{branch}', local_tip)
        self.git('branch', f'--set-upstream-to=origin/{branch}')
        self.suite('self.assertEqual(2, 2)')
        status = self.status()
        self.assertIn(f"Test {event['event_id']}: automated=pass", status)
        self.assertIn("Tests for exact HEAD commit: no attributable result; unknown", status)
        self.assertIn("Tests for exact canonical commit: no attributable result; unknown", status)
        self.assertIn("Publication: unconfirmed (local event; remote content not checked)", status)

    def test_head_change_invalidates_pass(self):
        execute = group_test.execute
        def moving(command, root):
            result = execute(command, root)
            (root / 'tracked.txt').write_text('new commit', encoding='utf-8')
            self.commit('fixture concurrent HEAD change')
            return result
        with patch('group_test.execute', side_effect=moving):
            self.assertEqual(self.direct()[0], 1)
        event, = self.events()
        self.assertEqual(event['commit'], self.head)
        self.assertEqual(event['automated_result'], 'partial')

    def test_failed_cases_survive_invalid_git_state(self):
        self.suite('self.fail("observed fixture failure")')
        execute = group_test.execute
        for change_head in (False, True):
            with self.subTest(change_head=change_head):
                def invalidate(command, root):
                    result = execute(command, root)
                    (root / 'tracked.txt').write_text(f'changed in fixture {change_head}', encoding='utf-8')
                    if change_head:
                        self.commit('fixture concurrent HEAD change')
                    return result
                starting_head = self.git('rev-parse', 'HEAD')
                with patch('group_test.execute', side_effect=invalidate):
                    code, output = self.direct()
                event = json.JSONDecoder().raw_decode(output)[0]
                self.assertEqual(code, 1)
                self.assertEqual((event['automated_result'], event['failed']), ('fail', 1))
                self.assertEqual(event['commit'], starting_head)
                self.assertFalse(event['execution']['state_verified'])
                self.assertIn('attribution of the observations is unverified', event['summary'])
                self.assertIn('observed fixture failure', event['execution']['output_tail'])
                self.assertEqual(event['execution']['diagnostics']['entries'][0]['message'],
                                 'observed fixture failure')
                self.commit('retain fixture evidence')

    def test_only_read_only_git_commands(self):
        from group_git import _git
        commands = []
        def observed(root, *args, **kwargs):
            commands.append(args[0])
            return _git(root, *args, **kwargs)
        with patch('group_git._git', side_effect=observed), patch('group_test._git', side_effect=observed):
            self.assertEqual(self.direct()[0], 0)
        self.assertTrue(set(commands) <= {'rev-parse', 'check-ref-format', 'symbolic-ref',
                                        'status', 'remote', 'for-each-ref', 'show-ref',
                                        'rev-list', 'ls-files'})

    def test_hidden_index_flags_refused(self):
        self.git('update-index', '--assume-unchanged', 'tracked.txt')
        (self.root / 'tracked.txt').write_text('hidden change', encoding='utf-8')
        self.assertIn('Hidden index flags', self.cli('--person-id', 'P01').stderr)
        self.assertEqual(self.events(), [])

    def test_subtest_failures_count_case_once(self):
        self.suite('with self.subTest(i=1): self.fail("subtest")')
        self.assertEqual(self.direct()[0], 1)
        self.assertEqual(self.events()[0]['failed'], 1)

    def test_post_inspection_failure_not_pass(self):
        snapshot = group_test.snapshot
        with patch('group_test.snapshot', side_effect=[snapshot(self.root, 'main'), GitError('inspection failed')]):
            self.assertEqual(self.direct()[0], 1)
        self.assertEqual(self.events()[0]['automated_result'], 'partial')

    def test_record_matches_actual_runner_with_only_paths_redacted(self):
        actual = group_test.execute
        calls = []
        def recording(command, root):
            calls.append(list(command))
            return actual(command, root)
        with patch('group_test.execute', side_effect=recording):
            self.assertEqual(self.direct()[0], 0)
        command, = calls
        event, = self.events()
        self.assertEqual(command[:4], [sys.executable, '-B', str(Path(group_test.__file__).resolve()),
                                       '--unittest-runner'])
        self.assertEqual(Path(command[4]).name, 'result.json')
        self.assertFalse(Path(command[4]).exists())
        self.assertEqual(event['execution']['argv'], ['<python-executable>', '-B',
                         '<group-test-runner>', '--unittest-runner', '<temporary-result-json>'])
        self.assertEqual(event['execution']['returncode'], 0)
        self.assertNotIn(event['reproduction']['command'], event['commands'])

    def test_reproduction_runs_same_suite_without_event(self):
        self.suite('self.assertEqual(1, 2)')
        self.assertEqual(self.direct()[0], 1)
        event, = self.events()
        repro = event['reproduction']
        self.assertIn('repository root', repro['cwd'])
        self.assertIn('not the command executed', repro['explanation'])
        self.assertEqual(repro['command'], 'python -B -m unittest discover -s tests -v')
        result = subprocess.run([sys.executable, *repro['command'].split()[1:]],
                                cwd=self.root, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, event['execution']['returncode'])
        self.assertIn(b'AssertionError', result.stderr)
        self.assertEqual(len(self.events()), 1)

    def test_failure_logs_hide_known_local_paths_and_keep_diagnostics(self):
        self.suite(r'''self.fail(str(__file__) + r' "D:\Private User\fixture.log" /home/fixture-user/tmp.log')''')
        actual = group_test.execute
        observed = []
        def logging(command, root):
            observed.extend([str(root.resolve()), command[0], command[2], command[4],
                             str(Path(command[4]).parent), str(Path.home())])
            result = actual(command, root)
            result.stderr += ('\n' + '\n'.join(observed + [p.replace('\\', '/') for p in observed])).encode()
            return result
        with patch('group_test.execute', side_effect=logging):
            self.assertEqual(self.direct()[0], 1)
        event, = self.events()
        serialized = json.dumps(event, ensure_ascii=False)
        for path in observed:
            self.assertNotIn(json.dumps(path, ensure_ascii=False)[1:-1], serialized)
            self.assertNotIn(path.replace('\\', '/'), serialized)
        self.assertIn('AssertionError', event['execution']['output_tail'])
        self.assertIn('tests', event['execution']['output_tail'])
        diagnostic = event['execution']['diagnostics']['entries'][0]['message']
        self.assertIn('<repository>', diagnostic)
        self.assertIn('"<absolute-path>" <absolute-path>', diagnostic)
        self.assertNotIn('Private User', serialized)
        self.assertNotIn('fixture-user', serialized)
        self.assertEqual((event['commit'], event['failed'], event['execution']['returncode']),
                         (self.head, 1, 1))

    def test_start_error_redacts_private_path(self):
        with patch('group_test.execute', side_effect=OSError(str(Path.home() / 'private' / 'python'))):
            self.assertEqual(self.direct()[0], 1)
        event, = self.events()
        self.assertNotIn(str(Path.home()), event['summary'])
        self.assertIn('<user-home>', event['summary'])


if __name__ == '__main__':
    unittest.main()
