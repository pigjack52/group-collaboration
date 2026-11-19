"""Current clean HEAD unittest evidence; local only, not the full TEST procedure."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

from group_events import EventError, validate_event, write_event
from group_git import GitError, _git, inspect_status
from group_status import read_members

TIMEOUT = 120
MARKERS = ('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'rebase-merge',
           'rebase-apply', 'sequencer', 'BISECT_LOG')

# Fixed code, never shell input. The separate result file avoids parsing test prose.
RUNNER = r'''
import json, sys, unittest
class Result(unittest.TextTestResult):
    def startTest(self, test):
        self.bad = False
        self.omitted = False
        self.passed = False
        super().startTest(test)
    def addSuccess(self, test):
        self.passed = True
        super().addSuccess(test)
    def addFailure(self, test, err):
        self.bad = True
        super().addFailure(test, err)
    def addError(self, test, err):
        self.bad = True
        super().addError(test, err)
    def addUnexpectedSuccess(self, test):
        self.bad = True
        super().addUnexpectedSuccess(test)
    def addSkip(self, test, reason):
        self.omitted = True
        super().addSkip(test, reason)
    def addExpectedFailure(self, test, err):
        self.omitted = True
        super().addExpectedFailure(test, err)
    def addSubTest(self, test, subtest, err):
        if err is not None:
            self.bad = True
        super().addSubTest(test, subtest, err)
    def stopTest(self, test):
        if self.bad:
            counts['failed'] += 1
        elif self.omitted:
            counts['skipped'] += 1
        elif self.passed:
            counts['passed'] += 1
        super().stopTest(test)
counts = dict(passed=0, failed=0, skipped=0)
suite = unittest.defaultTestLoader.discover('tests')
result = unittest.TextTestRunner(verbosity=2, resultclass=Result).run(suite)
with open(sys.argv[1], 'w', encoding='utf-8') as stream:
    json.dump(dict(counts, ran=result.testsRun, successful=result.wasSuccessful()), stream)
sys.exit(0 if result.wasSuccessful() else 1)
'''


def snapshot(root, canonical):
    state = inspect_status(root, canonical, allow_missing_upstream=True)
    if not state.branch:
        raise GitError('Detached HEAD refused; a current branch is required')
    if not state.clean:
        raise GitError('Dirty staged/unstaged/untracked state; TEST refused')
    for marker in MARKERS:
        location, _ = _git(root, 'rev-parse', '--git-path', marker)
        if (root / location).exists():
            raise GitError('Unfinished Git operation: ' + marker)
    # Do not let assume-unchanged/skip-worktree hide modified tracked content.
    flags, _ = _git(root, 'ls-files', '-v', '-z')
    if any(entry and (entry[0].islower() or entry[0] == 'S')
           for entry in flags.split('\0')):
        raise GitError('Hidden index flags (assume-unchanged/skip-worktree) refused')
    return state.branch, state.head


def execute(command, root):
    return subprocess.run(command, cwd=root, capture_output=True,
                          timeout=TIMEOUT, shell=False,
                          env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8'))


def unique_fields(pairs):
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError('Duplicate runner result fields')
    return fields


def classify_result(counts, returncode):
    successful = counts.get('successful')
    inconsistent = (type(successful) is not bool
                    or returncode != (0 if successful is True else 1)
                    or (successful is True and counts['failed'] > 0))
    if counts['failed'] > 0:
        return 'fail', 'Inconsistent runner evidence; failed cases observed. ' if inconsistent else ''
    if inconsistent:
        return 'partial', 'Inconsistent runner evidence; result inconclusive. '
    if not successful:
        # unittest can report failures outside a test case (e.g. setUpClass).
        return 'fail', ''
    if not counts['ran']:
        return 'not_available', 'Zero tests; not_available. '
    if not counts['passed']:
        return 'partial', 'No successful cases. '
    return 'pass', ''


def redact_paths(value, replacements):
    """Remove known machine locations, retaining repository-relative suffixes."""
    if isinstance(value, dict):
        return {key: redact_paths(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_paths(item, replacements) for item in value]
    if not isinstance(value, str):
        return value
    for path, label in sorted(replacements, key=lambda item: len(str(item[0])), reverse=True):
        for spelling in {str(path), str(path).replace('\\', '/')}:
            value = value.replace(spelling, label)
    return value


def run_tests(root, config, person_id):
    root = Path(root)
    if config.get('protocol_version') != '0.1':
        raise EventError('Unsupported configuration protocol_version')
    if person_id not in {m['person_id'] for m in read_members(root)}:
        raise EventError('person_id is not a configured group member')
    before = snapshot(root, config['canonical_branch'])
    now = datetime.now(timezone.utc)
    event = dict(protocol_version='0.1',
                 event_id=f'E-{now:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex}',
                 event_type='test_result', group_id=config['group_id'],
                 person_id=person_id, created_at=now.isoformat().replace('+00:00', 'Z'),
                 related_events=[], scope='current_branch', commit=before[1],
                 commands=[], automated_result='not_available', passed=0, failed=0,
                 skipped=0, human_checks_required=[], summary='Validation not started.')
    validate_event(event)
    detail = ''
    evidence = dict(branch=before[0], counts_available=False, returncode=None,
                    completed=False, successful=None, semantic_review='not_performed')
    with tempfile.TemporaryDirectory(prefix='groupctl-test-') as directory:
        result_file = Path(directory) / 'result.json'
        command = [sys.executable, '-B', str(Path(__file__).resolve()),
                   '--unittest-runner', str(result_file)]
        # This is the actual invocation with path operands explicitly redacted,
        # not a claim that a different, portable command was executed.
        evidence.update(argv=['<python-executable>', '-B', '<group-test-runner>',
                              '--unittest-runner', '<temporary-result-json>'],
                        command_representation='actual invocation; path operands redacted',
                        cwd='repository root', started=False,
                        path_roles={'<python-executable>': 'current sys.executable',
                                    '<group-test-runner>': 'group_test.py from the invoking tool installation',
                                    '<temporary-result-json>': 'temporary counts file; deleted after execution'})
        replacements = [(result_file, '<temporary-result-json>'),
                        (Path(directory), '<temporary-directory>'),
                        (Path(__file__).resolve(), '<group-test-runner>'),
                        (sys.executable, '<python-executable>'),
                        (root.resolve(), '<repository>'),
                        (Path.home(), '<user-home>'),
                        (Path(sys.base_prefix), '<python-installation>')]
        event['reproduction'] = dict(
            cwd='repository root at the recorded commit in a separate checkout',
            command='python -B -m unittest discover -s tests -v',
            explanation='Equivalent test discovery and verbosity; not the command executed here. '
                        'Use python3 if needed. The internal runner additionally records per-case counts '
                        '(expected failures count as skipped), enforces a timeout, and verifies clean Git state. '
                        'This reproduction command does not create a TEST event.')
        try:
            if not (root / 'tests').is_dir():
                detail = 'No tests directory; no validation executed.'
            else:
                event['commands'] = [subprocess.list2cmdline(evidence['argv'])]
                result = execute(command, root)
                evidence['started'] = True
                evidence['completed'] = True
                evidence['returncode'] = result.returncode
                output = (result.stdout + result.stderr).decode('utf-8', errors='replace')
                evidence['output_tail'] = output[-12000:]
                detail = output[-2000:] or 'Runner returned no output.'
                if result_file.exists():
                    counts = json.loads(result_file.read_text(encoding='utf-8'),
                                        object_pairs_hook=unique_fields)
                    if not isinstance(counts, dict):
                        raise ValueError('Invalid runner result object')
                    if type(counts.get('successful')) is bool:
                        evidence['successful'] = counts['successful']
                    if (any(type(counts.get(k)) is not int or counts[k] < 0
                            for k in ('passed', 'failed', 'skipped', 'ran'))
                            or sum(counts[k] for k in ('passed', 'failed', 'skipped')) != counts['ran']):
                        raise ValueError('Invalid runner counts')
                    event.update({k: counts[k] for k in ('passed', 'failed', 'skipped')})
                    evidence['counts_available'] = True
                    evidence['ran'] = counts['ran']
                    event['automated_result'], explanation = classify_result(counts, result.returncode)
                    detail = explanation + detail
                else:
                    detail = 'Runner did not produce counts; result inconclusive. ' + detail
        except subprocess.TimeoutExpired as exc:
            evidence['started'] = True
            event['automated_result'] = 'partial'
            detail = f'Test timeout after {TIMEOUT}s; counts unavailable. '
            evidence['output_tail'] = ((exc.stdout or b'') + (exc.stderr or b'')).decode(
                'utf-8', errors='replace')[-12000:]
        except (OSError, ValueError) as exc:
            detail = f'Cannot complete test runner: {exc}; counts may be unavailable.'
            if isinstance(exc, OSError) and not evidence['started']:
                event['commands'] = []
    try:
        if snapshot(root, config['canonical_branch']) != before:
            raise GitError('Branch or HEAD changed during tests')
        evidence['state_verified'] = True
    except GitError as exc:
        evidence['state_verified'] = False
        if event['automated_result'] == 'pass':
            event['automated_result'] = 'partial'
        detail = (f'State verification invalid: {exc}. '
                  'The recorded commit is the starting HEAD; attribution of the observations is unverified. '
                  + detail)
    event['summary'] = ('Current-branch unittest only; AI semantic review not performed. ' + detail)
    event['execution'] = evidence  # Optional extension, specs/03 section 14.
    # Logs and exception messages can contain the same private paths as argv.
    event = redact_paths(event, replacements)
    path = write_event(root, event)
    print(json.dumps(event, ensure_ascii=True, indent=2))
    print('本機事件已建立，尚未 Commit／Push: ' + str(path))
    print('AI semantic review: NOT PERFORMED; not the full TEST procedure.')
    return 0 if event['automated_result'] == 'pass' else 1


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] != '--unittest-runner':
        sys.exit('Internal fixed unittest runner; use groupctl test --person-id ID')
    sys.argv = [sys.argv[0], sys.argv[2]]
    exec(compile(RUNNER, '<groupctl unittest runner>', 'exec'))
