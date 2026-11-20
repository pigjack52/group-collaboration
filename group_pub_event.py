"""Record one reviewed work publication as a separate local event commit."""
from datetime import datetime, timezone
import json
import os
import stat
from uuid import uuid4

from group_events import EventError, _identifier, _pairs, load_event, parse_event, validate_event, write_event
from group_git import GitError, _git, repository_root
from group_pub_commit import DIFF, check_write_settings, commit_details, observe, paths, write_commit
from group_pub_push import OBJECT_ID, check_target, remote_head


EVENTS = '.group/events'
REVIEW_FIELDS = {'summary', 'files_changed', 'interface_changes', 'tests_run',
                 'known_issues', 'proposed_for_integration'}


def regular_path(path, kind, *, missing_ok=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing_ok:
            return
        raise
    if not kind(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise EventError(f'Unsafe event namespace path: {path.name}')


def check_event_namespace(root, event):
    directory = root / EVENTS
    regular_path(directory.parent, stat.S_ISDIR)
    regular_path(directory, stat.S_ISDIR, missing_ok=True)

    def check(prior, path):
        if path != f'{EVENTS}/{prior["event_id"]}.json':
            raise EventError(f'Event ID does not match path: {path}')
        if prior['event_id'].casefold() == event['event_id'].casefold():
            raise EventError(f'Event ID already exists: {path}')
        if prior['event_type'] == 'change_published' and (
            prior['change_id'].casefold() == event['change_id'].casefold()
            or (prior['branch'], prior['commit']) == (event['branch'], event['commit'])
        ):
            raise EventError(f'Change ID or work commit already has a publication event: {path}')

    # Include ignored and untracked events so an interrupted write blocks retries.
    if directory.exists():
        for path in directory.iterdir():
            if path.suffix.casefold() == '.json':
                regular_path(path, stat.S_ISREG)
                check(load_event(path), f'{EVENTS}/{path.name}')

    # Tracking refs supply duplicate evidence only. The live query verifies A.
    tips, _ = _git(root, 'for-each-ref', '--format=%(objectname)', 'refs/heads', 'refs/remotes')
    seen = set()
    for tip in set(tips.splitlines()) | {event['commit']}:
        entries, _ = _git(root, '--no-replace-objects', 'ls-tree', '-rz', tip, '--', EVENTS)
        for entry in paths(entries):
            if entry in seen:
                continue
            seen.add(entry)
            metadata, path = entry.split('\t', 1)
            mode, kind, blob = metadata.split()
            if not path.casefold().endswith('.json'):
                continue
            if mode not in ('100644', '100755') or kind != 'blob':
                raise EventError(f'Unsafe committed event path: {path}')
            raw, _ = _git(root, '--no-replace-objects', 'cat-file', 'blob', blob)
            check(parse_event(raw), path)


def check_index(root, head):
    staged = paths(_git(root, *DIFF, '--cached', '--ita-visible-in-index', head, '--')[0])
    unstaged = paths(_git(root, '-c', 'core.fsmonitor=false', *DIFF, '--')[0])
    if staged:
        raise GitError('Event commit requires an empty staged change set.')
    if any(path.casefold().startswith(EVENTS + '/') or path.casefold() in ('.group', EVENTS)
           for path in unstaged):
        raise GitError('Resolve working-tree changes to tracked events before creating a new event.')


def check_work_files(root, work, reviewed_files):
    _, parents = commit_details(root, work)
    if len(parents) != 1:
        raise EventError('The reviewed work must be a single-parent commit.')
    changed = paths(_git(root, '--no-replace-objects', *DIFF, parents[0], work, '--')[0])
    if not changed or sorted(reviewed_files) != sorted(changed):
        raise EventError('Reviewed files_changed must exactly match the nonempty work commit diff.')


def event_commit(directory, load_config, args):
    work = args.work_sha
    report = dict(operation='pub_event_commit', result='event_validation_failed', phase='validation',
                  publication='unpublished', work_commit=work, expected_head=args.expected_head,
                  branch=args.expected_branch, remote=args.remote, change_id=args.change_id)
    try:
        if not args.confirm_reviewed or not OBJECT_ID.fullmatch(work) or args.expected_head != work:
            raise GitError('Explicit review and matching full work SHA and expected HEAD are required.')
        if not _identifier(args.change_id) or not _identifier(args.person_id):
            raise EventError('Change ID and person ID must be portable identifiers.')
        local_env, _ = _git(directory, 'rev-parse', '--local-env-vars')
        if set(local_env.splitlines()).intersection(os.environ) or 'GIT_NAMESPACE' in os.environ:
            raise GitError('Repository-local Git environment overrides are unsupported.')
        root = repository_root(directory)
        ref = 'refs/heads/' + args.expected_branch
        _git(root, 'check-ref-format', ref)
        check_target(root, load_config, args)
        review = json.loads(args.review_file.read_text(encoding='utf-8-sig'), object_pairs_hook=_pairs)
        if not isinstance(review, dict) or review.keys() != REVIEW_FIELDS:
            raise EventError('Review JSON must contain exactly the six reviewed contribution fields.')
        config = load_config(root / '.group/group.json')
        now = datetime.now(timezone.utc)
        event = validate_event(dict(
            protocol_version=config['protocol_version'], group_id=config['group_id'],
            event_id=f'E-{now:%Y%m%dT%H%M%SZ}-{args.person_id}-{uuid4().hex}',
            event_type='change_published', person_id=args.person_id, related_events=[],
            created_at=now.strftime('%Y-%m-%dT%H:%M:%SZ'), change_id=args.change_id,
            branch=args.expected_branch, commit=work, **review,
        ))
        check_work_files(root, work, event['files_changed'])
        path = f'{EVENTS}/{event["event_id"]}.json'
        args = type(args)(**(vars(args) | dict(file=[path], message=f'Publish change {args.change_id}\n')))

        def prepare_event():
            check_index(root, work)
            check_event_namespace(root, event)
            _, ignored = _git(root, 'check-ignore', '-q', '--', path, allowed=(0, 1))
            if ignored == 0:
                raise EventError('The new event path is Git-ignored.')
            if remote_head(root, args.expected_url, ref, len(work)) != work:
                raise GitError('The live remote personal branch does not point to the reviewed work SHA.')
            report['remote_work_verified'] = work
            check_target(root, load_config, args, own_lock=True)
            check_index(root, work)
            check_event_namespace(root, event)
            report.update(phase='writing_event', event_id=event['event_id'], event_path=path)
            write_event(root, event)
            report.update(phase='event_written', event_written=True)
            raw = (json.dumps(event, ensure_ascii=True, indent=2, allow_nan=False) + '\n').encode('utf-8')
            blob, _ = _git(root, 'hash-object', '--stdin', input_bytes=raw)
            return blob

        write_commit(root, load_config, args, report, prepare_event=prepare_event)
        commit, tree = report['commit'], report['tree']
        facts = observe(root, load_config, args)
        check_write_settings(root, facts['facts'])
        if (facts['blockers'] or (facts['facts']['branch'], facts['facts']['head']) != (args.expected_branch, commit)
                or not OBJECT_ID.fullmatch(commit) or len(commit) != len(work) or commit == work
                or commit_details(root, commit) != (tree, [work])
                or paths(_git(root, '--no-replace-objects', *DIFF, work, commit, '--')[0]) != {path}
                or path in facts['facts']['untracked']):
            raise GitError('Final event commit, branch or index could not be verified.')
        check_index(root, commit)
        report.update(result='local_event_committed', phase='verified')
    except (GitError, EventError, OSError, ValueError) as exc:
        phase = report['phase']
        if phase == 'writing_event':
            report['result'] = 'event_creation_failed'
        elif phase != 'validation':
            failure = {'ref_updated': 'post_ref_update_failed', 'index_installed': 'verification_failed'}
            report.update(result='partial', failure=failure.get(phase, 'commit_failed'))
        report['error'] = str(exc)
    return report
