"""Current clean HEAD unittest evidence; local only, not the full TEST procedure."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid

from group_events import EventError, validate_event, write_event
from group_git import GitError, _git, inspect_status
from group_status import read_members

TIMEOUT = 120
OUTPUT_LIMIT = 12000
SUMMARY_LIMIT = 2000
DIAGNOSTIC_LIMIT = 50
DIAGNOSTIC_TEXT_LIMIT = 2000
COUNT_FIELDS = ('passed', 'failed', 'skipped', 'ran')
MARKERS = ('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'rebase-merge',
           'rebase-apply', 'sequencer', 'BISECT_LOG')

# Fixed code, never shell input. The separate result file avoids parsing test prose.
RUNNER = r'''
import json, platform, sys, unittest

environment = dict(
    python_implementation=platform.python_implementation(),
    python_version=platform.python_version(),
    os_system=platform.system() or None,
    os_release=platform.release() or None,
    framework='unittest',
    framework_version=getattr(unittest, '__version__', None),
    runner='groupctl unittest runner',
    runner_sha256=hashlib.sha256(RUNNER.encode('utf-8')).hexdigest(),
)
diagnostics = []
diagnostic_total = 0

def record(kind, test=None, err=None):
    global diagnostic_total
    diagnostic_total += 1
    if len(diagnostics) >= DIAGNOSTIC_LIMIT:
        return
    test_id = message = None
    try:
        if test is not None:
            test_id = test.id()
    except Exception:
        pass
    try:
        if err:
            message = str(err[1])
    except Exception:
        pass
    diagnostics.append(dict(
        kind=kind, test_id=test_id,
        exception_type=f'{err[0].__module__}.{err[0].__qualname__}' if err else None,
        message=message,
    ))

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
        record('assertion_failure', test, err)
        super().addFailure(test, err)
    def addError(self, test, err):
        self.bad = True
        record('error', test, err)
        super().addError(test, err)
    def addUnexpectedSuccess(self, test):
        self.bad = True
        record('unexpected_success', test)
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
            kind = 'assertion_failure' if issubclass(err[0], test.failureException) else 'error'
            record(kind, subtest, err)
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

def save_result(**outcome):
    with open(sys.argv[1], 'w', encoding='utf-8') as stream:
        json.dump(dict(outcome, environment=environment, diagnostics=diagnostics,
                       diagnostic_total=diagnostic_total), stream)

try:
    suite = unittest.defaultTestLoader.discover('tests')
    result = unittest.TextTestRunner(verbosity=2, resultclass=Result).run(suite)
except Exception:
    record('runner_error', err=sys.exc_info())
    save_result()
    raise
save_result(**dict(counts, ran=result.testsRun, successful=result.wasSuccessful()))
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
        for spelling in {str(path), str(path).replace('\\', '/'), repr(str(path))[1:-1]}:
            value = re.sub(re.escape(spelling), lambda match: label, value,
                           flags=re.IGNORECASE if os.name == 'nt' else 0)
    return value


PRIVATE_PATH = re.compile(
    r'''(?P<quote>["'])(?:[A-Za-z]:[\\/]|\\\\|/(?!/))[^"'\r\n]+(?P=quote)'''
    r'''|(?<![\w/:>\\])(?:[A-Za-z]:[\\/]|\\\\|/(?!/))[^\s"'<>|,;)\]]+'''
)


def clean_text(value, replacements):
    value = redact_paths(value, replacements)
    return PRIVATE_PATH.sub(lambda match: (
        (match['quote'] or '') + '<absolute-path>' + (match['quote'] or '')), value)


def capture_output(evidence, stdout, stderr, replacements):
    streams = {
        name: clean_text((data or b'').decode('utf-8', errors='replace'), replacements)
        for name, data in (('stdout', stdout), ('stderr', stderr))
    }
    combined = streams['stdout'] + streams['stderr']
    evidence['output_tail'] = combined[-OUTPUT_LIMIT:]
    evidence['output'] = dict(
        rule='tail of redacted text; Unicode characters; combined order stdout then stderr',
        char_limit=OUTPUT_LIMIT, summary_char_limit=SUMMARY_LIMIT,
        combined_truncated=len(combined) > OUTPUT_LIMIT,
        summary_truncated=len(combined) > SUMMARY_LIMIT,
        **{name: dict(tail=value[-OUTPUT_LIMIT:], truncated=len(value) > OUTPUT_LIMIT)
           for name, value in streams.items()},
    )
    return combined[-SUMMARY_LIMIT:]


def read_diagnostics(payload, replacements):
    records = payload.get('diagnostics')
    total = payload.get('diagnostic_total')
    if not isinstance(records, list) or type(total) is not int:
        return None
    if total < 0 or len(records) != min(total, DIAGNOSTIC_LIMIT):
        return None
    entries = []
    for record in records:
        if not isinstance(record, dict):
            return None
        entry = {}
        shortened = []
        for field in ('kind', 'test_id', 'exception_type', 'message'):
            value = record.get(field)
            if value is not None:
                if not isinstance(value, str):
                    return None
                value = clean_text(value, replacements)
                if len(value) > DIAGNOSTIC_TEXT_LIMIT:
                    shortened.append(field)
                    value = value[:DIAGNOSTIC_TEXT_LIMIT]
            entry[field] = value
        entries.append(dict(entry, truncated_fields=shortened))
    return dict(entries=entries, total=total, entry_limit=DIAGNOSTIC_LIMIT,
                char_limit=DIAGNOSTIC_TEXT_LIMIT,
                rule='first entries in callback order; prefix of redacted text in Unicode characters',
                entries_truncated=len(entries) < total,
                truncated=len(entries) < total or any(entry['truncated_fields'] for entry in entries))


def run_tests(root, config, person_id, *, human_checks=None):
    root = Path(root)
    if config.get('protocol_version') != '0.1':
        raise EventError('Unsupported configuration protocol_version')
    if person_id not in {m['person_id'] for m in read_members(root)}:
        raise EventError('person_id is not a configured group member')
    checks = [c.strip() for c in (human_checks or []) if isinstance(c, str) and c.strip()]
    before = snapshot(root, config['canonical_branch'])
    now = datetime.now(timezone.utc)
    event = dict(protocol_version='0.1',
                 event_id=f'E-{now:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex}',
                 event_type='test_result', group_id=config['group_id'],
                 person_id=person_id, created_at=now.isoformat().replace('+00:00', 'Z'),
                 related_events=[], scope='current_branch', commit=before[1],
                 commands=[], automated_result='not_available', passed=0, failed=0,
                 skipped=0, human_checks_required=checks, summary='Validation not started.')
    validate_event(event)
    detail = ''
    evidence = dict(branch=before[0], counts_available=False, returncode=None,
                    completed=False, successful=None, semantic_review='not_performed',
                    environment=None, diagnostics=None, output=None,
                    counts=dict(status='unavailable', **dict.fromkeys(COUNT_FIELDS)))
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
                        (Path(tempfile.gettempdir()), '<system-temp>'),
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
                evidence['counts']['status'] = 'unknown'
                detail = capture_output(evidence, result.stdout, result.stderr, replacements)
                detail = detail or 'Runner returned no output.'
                if result_file.exists():
                    counts = json.loads(result_file.read_text(encoding='utf-8'),
                                        object_pairs_hook=unique_fields)
                    if not isinstance(counts, dict):
                        raise ValueError('Invalid runner result object')
                    environment = counts.get('environment')
                    if isinstance(environment, dict):
                        evidence['environment'] = environment
                    evidence['diagnostics'] = read_diagnostics(counts, replacements)
                    if type(counts.get('successful')) is bool:
                        evidence['successful'] = counts['successful']
                    if (any(type(counts.get(k)) is not int or counts[k] < 0
                            for k in COUNT_FIELDS)
                            or sum(counts[k] for k in ('passed', 'failed', 'skipped')) != counts['ran']):
                        detail = 'Invalid or missing runner counts; result inconclusive. ' + detail
                    else:
                        event.update({k: counts[k] for k in ('passed', 'failed', 'skipped')})
                        evidence['counts_available'] = True
                        evidence['ran'] = counts['ran']
                        evidence['counts'] = dict(status='observed', **{k: counts[k] for k in COUNT_FIELDS})
                        event['automated_result'], explanation = classify_result(counts, result.returncode)
                        detail = explanation + detail
                else:
                    detail = 'Runner did not produce counts; result inconclusive. ' + detail
        except subprocess.TimeoutExpired as exc:
            evidence['started'] = True
            evidence['counts']['status'] = 'unknown'
            event['automated_result'] = 'partial'
            detail = f'Test timeout after {TIMEOUT}s; counts unavailable. '
            detail += capture_output(evidence, exc.stdout, exc.stderr, replacements)
        except (OSError, ValueError) as exc:
            detail = f'Cannot complete test runner: {exc}; counts may be unavailable. ' + detail
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
    event['summary'] = ('Current-branch unittest only; AI semantic review not performed. '
                        + clean_text(detail, replacements))
    event['execution'] = evidence  # Optional extension, specs/03 section 14.
    # Logs and exception messages can contain the same private paths as argv.
    event = redact_paths(event, replacements)
    path = write_event(root, event)
    print(json.dumps(event, ensure_ascii=True, indent=2))
    print('本機事件已建立，尚未 Commit／Push: ' + str(path))
    print('AI semantic review: NOT PERFORMED; not the full TEST procedure.')
    return 0 if event['automated_result'] == 'pass' else 1


def run_scoped_tests(root, config, person_id, scope='current', *, isolated=False,
                     publish=False, remote=None, expected_url=None,
                     expected_remote_head=None, confirm_reviewed=False,
                     human_checks=None):
    """Run TEST for current, main/canonical, or <change_id> scope using an isolated worktree."""
    if scope in ('current', 'current_branch') and not isolated and not publish:
        return run_tests(root, config, person_id, human_checks=human_checks)

    from group_event_publish import (
        collect_known_events, commit_event_file, new_event_id, push_and_verify_event, utc_now,
    )
    from group_int import resolve_work
    from group_int_finish import run_isolated_unittest
    from groupctl import load_config

    root = Path(root)
    if config.get('protocol_version') != '0.1':
        raise EventError('Unsupported configuration protocol_version')
    if person_id not in {m['person_id'] for m in read_members(root)}:
        raise EventError('person_id is not a configured group member')
    checks = [c.strip() for c in (human_checks or []) if isinstance(c, str) and c.strip()]

    canonical = config['canonical_branch']
    state = inspect_status(root, canonical, allow_missing_upstream=True)
    related = []
    source_ref = None
    recorded_branch = state.branch

    if scope in ('current', 'current_branch'):
        if not state.branch:
            raise GitError('Detached HEAD refused; a current branch is required')
        target_sha = state.head
        event_scope = 'current_branch'
        source_ref = 'refs/heads/' + state.branch
    elif scope in ('main', 'canonical', canonical):
        if not state.canonical_commit:
            raise GitError(f'Canonical branch {canonical!r} is unavailable locally.')
        target_sha = state.canonical_commit
        event_scope = 'canonical'
        source_ref = state.canonical_ref
        recorded_branch = canonical
    else:
        known = collect_known_events(root, config['group_id'])
        matches = [e for e in known.values()
                   if e['event_type'] == 'change_published'
                   and e['change_id'].casefold() == scope.casefold()]
        if not matches:
            raise EventError(f'Unknown TEST scope or change_id not found: {scope}')
        if len(matches) > 1:
            raise EventError(f'Conflicting change_published events for {scope}')
        ch = matches[0]
        target_sha = resolve_work(root, ch['commit'], len(state.head))
        event_scope = ch['change_id']
        source_ref = 'refs/remotes/origin/' + ch['branch']
        recorded_branch = ch['branch']
        related = [ch['event_id']]

    outcome = run_isolated_unittest(root, target_sha)
    now, created_at = utc_now()
    event = validate_event(dict(
        protocol_version='0.1',
        event_id=new_event_id(person_id, now),
        event_type='test_result',
        group_id=config['group_id'],
        person_id=person_id,
        created_at=created_at,
        related_events=related,
        scope=event_scope,
        commit=target_sha,
        commands=outcome['commands'],
        automated_result=outcome['automated_result'],
        passed=outcome['passed'],
        failed=outcome['failed'],
        skipped=outcome['skipped'],
        human_checks_required=checks,
        summary=f'Isolated worktree unittest ({event_scope} @ {target_sha[:12]}): {outcome["summary"]}',
        execution=dict(
            branch=recorded_branch,
            source_ref=source_ref,
            isolated_worktree=True,
            state_verified=True,
            counts_available=outcome['automated_result'] != 'not_available',
            ran=outcome['ran'],
            counts=dict(
                status='observed' if outcome['automated_result'] != 'not_available' else 'unavailable',
                ran=outcome['ran'], passed=outcome['passed'],
                failed=outcome['failed'], skipped=outcome['skipped'],
            ),
            semantic_review='not_performed',
        ),
    ))

    output_report = dict(event=event, publication='local_unpublished', verified=False)
    if publish:
        commit_rep = commit_event_file(
            root, load_config, person_id, event,
            message=f'Record test_result ({event_scope}: {outcome["automated_result"]})',
            check_related=True,
        )
        output_report.update(
            publication='local_committed',
            event_commit=commit_rep['commit'],
            event_path=commit_rep['event_path'],
        )
        if remote:
            if not (expected_url and confirm_reviewed):
                raise GitError('Pushing test_result requires --remote, --expected-url, and --confirm-reviewed.')
            push_rep = push_and_verify_event(
                root, branch=commit_rep['branch'], commit=commit_rep['commit'],
                parent=commit_rep['parent'], event_id=event['event_id'],
                event_path=commit_rep['event_path'], event_blob=commit_rep['event_blob'],
                remote=remote, expected_url=expected_url,
                expected_remote_head=expected_remote_head,
            )
            output_report.update(
                publication=push_rep['result'],
                verified=push_rep['verified'],
                push=push_rep,
            )
    else:
        path = write_event(root, event)
        output_report['event_path'] = str(path.relative_to(root).as_posix())

    print(json.dumps(output_report, ensure_ascii=True, indent=2))
    return 0 if outcome['automated_result'] == 'pass' and (not remote or output_report['verified']) else 1


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] != '--unittest-runner':
        sys.exit('Internal fixed unittest runner; use groupctl test --person-id ID')
    sys.argv = [sys.argv[0], sys.argv[2]]
    exec(compile(RUNNER, '<groupctl unittest runner>', 'exec'))
