"""Publish one reviewed event commit and verify objects fetched from its remote."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from group_events import EventError, _identifier, parse_event
from group_git import GitError, _git, repository_root
from group_pub_commit import commit_details, paths
from group_pub_event import EVENTS, check_work_files
from group_pub_push import (
    NETWORK_ENV, OBJECT_ID, TRANSPORT, check_destination, check_literal_url,
    check_local, remote_head, send_update,
)


def read_event_blob(repository, blob):
    raw, _ = _git(repository, '--no-replace-objects', 'cat-file', 'blob', blob, raw=True)
    return raw, parse_event(raw.decode('utf-8'))


def inspect_event_commit(repository, args, group_id):
    work, head = args.work_sha, args.expected_head
    tree, parents = commit_details(repository, head)
    if parents != [work]:
        raise EventError('The event commit must have exactly the reviewed work commit as its parent.')
    kind, _ = _git(repository, '--no-replace-objects', 'cat-file', '-t', work)
    if kind != 'commit':
        raise EventError('The reviewed work SHA must identify a commit object.')
    diff, _ = _git(repository, '--no-replace-objects', 'diff-tree', '--no-commit-id',
                   '-r', '-t', '--raw', '--no-abbrev', '--no-renames', '--ignore-submodules=none',
                   '-z', work, head, '--')
    fields = iter(diff.split('\0')[:-1])
    files = []
    for metadata, path in zip(fields, fields, strict=True):
        old_mode, mode, old_blob, blob, change = metadata.split()
        if (path in ('.group', EVENTS) and mode == '040000'
                and old_mode in (':000000', ':040000')):
            continue
        files.append((old_mode, mode, old_blob, blob, change, path))
    if len(files) != 1:
        raise EventError('The event commit must only add one event file.')
    old_mode, mode, old_blob, blob, change, path = files[0]
    if (old_mode, mode, old_blob, change) != (':000000', '100644', '0' * len(head), 'A'):
        raise EventError('The event commit must add a regular event file.')
    raw, event = read_event_blob(repository, blob)
    if path != f'{EVENTS}/{event["event_id"]}.json':
        raise EventError('Event ID does not match its committed path.')
    expected = dict(event_type='change_published', group_id=group_id, person_id=args.person_id,
                    branch=args.expected_branch, change_id=args.change_id, commit=work)
    if any(event.get(field) != value for field, value in expected.items()):
        raise EventError('Committed event does not match the reviewed group, person, branch, change or work SHA.')
    check_work_files(repository, work, event['files_changed'])
    return tree, path, blob, raw, event


def fetch_remote(repository, args, refspec):
    """Use an empty object store with no access to the working repository's objects."""
    object_format = 'sha1' if len(args.expected_head) == 40 else 'sha256'
    _git(repository, 'init', '--bare', '--template=', f'--object-format={object_format}')
    hooks = repository / 'empty-hooks'
    hooks.mkdir()
    check_literal_url(repository, args.expected_url)
    try:
        _git(repository, '--no-replace-objects', *TRANSPORT,
             '-c', f'core.hooksPath={hooks.as_posix()}', '-c', 'fetch.fsckObjects=true',
             'fetch', '--no-tags', '--no-recurse-submodules', '--no-auto-maintenance',
             '--no-write-fetch-head', '--refmap=', '--', args.expected_url, refspec,
             env_overrides=NETWORK_ENV)
    except GitError as exc:
        raise GitError('Could not fetch remote Git objects for event inspection.') from exc


def check_remote_events(repository, expected):
    tips, _ = _git(repository, 'for-each-ref', '--format=%(objectname)', 'refs/heads')
    seen = set()
    for tip in sorted(set(tips.splitlines())):
        entries, _ = _git(repository, '--no-replace-objects', 'ls-tree', '-rz', tip, '--', EVENTS)
        for entry in sorted(paths(entries) - seen):
            seen.add(entry)
            metadata, path = entry.split('\t', 1)
            mode, kind, blob = metadata.split()
            if not path.casefold().endswith('.json'):
                continue
            if mode not in ('100644', '100755') or kind != 'blob':
                raise EventError(f'Unsafe remote event path: {path}')
            _, event = read_event_blob(repository, blob)
            if path != f'{EVENTS}/{event["event_id"]}.json':
                raise EventError(f'Remote event ID does not match its path: {path}')
            if (event['event_id'].casefold() == expected['event_id'].casefold()
                    or (event['event_type'] == 'change_published' and (
                        event['change_id'].casefold() == expected['change_id'].casefold()
                        or (event['branch'], event['commit']) == (expected['branch'], expected['commit'])
                    ))):
                raise EventError(f'Remote event already uses this event ID, change ID or branch/work SHA: {path}')


def fetched_head(repository, ref):
    oid, _ = _git(repository, 'show-ref', '--hash', '--verify', ref)
    return oid


def verify_remote(root, args, ref, group_id, local_event, report):
    head = args.expected_head
    check_destination(root, args)
    report['remote_head_after'] = remote_head(root, args.expected_url, ref, len(head))
    if report['remote_head_after'] != head:
        raise GitError('Verification query did not find E at the remote branch tip.')
    with TemporaryDirectory(prefix='groupctl-event-verify-') as directory:
        repository = Path(directory)
        fetch_remote(repository, args, f'{ref}:refs/heads/verified')
        if fetched_head(repository, 'refs/heads/verified') != head:
            raise GitError('Remote branch changed between verification query and fetch.')
        if inspect_event_commit(repository, args, group_id) != local_event:
            raise EventError('Fetched event commit tree, blob or event content differs from reviewed E.')
    report['remote_head_after'] = remote_head(root, args.expected_url, ref, len(head))
    if report['remote_head_after'] != head:
        raise GitError('Remote branch changed during verification.')
    report.update(verified=True, remote_head_verified=head, remote_blob_verified=local_event[2])


def push_event(directory, load_config, args):
    work, head, reviewed_old = args.work_sha, args.expected_head, args.expected_remote_head
    report = dict(operation='pub_push_event', result='not_executed', verified=False,
                  publication_scope='change_published_event', person_id=args.person_id,
                  branch=args.expected_branch, remote=args.remote, work_commit=work,
                  event_commit=head, change_id=args.change_id, expected_remote_head=reviewed_old)
    try:
        if not args.confirm_reviewed:
            raise GitError('Explicit review and push authorization are required.')
        if (any(not oid or not OBJECT_ID.fullmatch(oid) or len(oid) != len(head)
                or oid == '0' * len(head) for oid in (head, work, reviewed_old))
                or work == head or reviewed_old not in (work, head)):
            raise GitError('Specify distinct full work and event SHAs and a reviewed remote tip of A or E.')
        if not _identifier(args.change_id) or not _identifier(args.person_id):
            raise EventError('Change ID and person ID must be portable identifiers.')
        local_env, _ = _git(directory, 'rev-parse', '--local-env-vars')
        if (set(local_env.splitlines()).intersection(os.environ)
                or {'GIT_NAMESPACE', 'GIT_GRAFT_FILE', 'GIT_SHALLOW_FILE'}.intersection(os.environ)):
            raise GitError('Repository-local Git environment overrides are unsupported.')
        root = repository_root(directory)
        ref = 'refs/heads/' + args.expected_branch
        _git(root, 'check-ref-format', ref)
        group_id = check_local(root, load_config, args)['group_id']
        local_event = inspect_event_commit(root, args, group_id)
        _, path, blob, _, event = local_event
        report.update(event_id=event['event_id'], event_path=path, event_blob=blob)
        old = remote_head(root, args.expected_url, ref, len(head))
        report['remote_head_before'] = old
        if old == head:
            report['result'] = 'verification_failed'
            verify_remote(root, args, ref, group_id, local_event, report)
            report['result'] = 'already_published'
            return report
        if old != work or reviewed_old != work:
            raise GitError('The live remote tip differs from the reviewed work commit A.')
        with TemporaryDirectory(prefix='groupctl-event-scan-') as directory:
            repository = Path(directory)
            fetch_remote(repository, args, 'refs/heads/*:refs/heads/*')
            if fetched_head(repository, ref) != work:
                raise GitError('The remote branch changed before duplicate inspection.')
            check_remote_events(repository, event)
        check_local(root, load_config, args)
        if remote_head(root, args.expected_url, ref, len(head)) != work:
            raise GitError('The remote branch changed before push.')
        try:
            send_update(root, args, ref, work, report)
        except GitError:
            # Keep the transport failure as the primary result and record any live observation.
            try:
                report['remote_head_after'] = remote_head(root, args.expected_url, ref, len(head))
                if report['result'] == 'verification_failed' and report['remote_head_after'] != head:
                    report['result'] = 'partial'
            except GitError as exc:
                report['remote_observation_error'] = str(exc)
            raise
        verify_remote(root, args, ref, group_id, local_event, report)
        report['result'] = 'published_verified'
    except (GitError, EventError, OSError, ValueError) as exc:
        report['error'] = str(exc)
    return report
