"""Shared safe event creation, isolated-index commit, and remote verification."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import uuid4

from group_events import EventError, _identifier, load_event, parse_event, validate_event, write_event
from group_git import GitError, _git, inspect_status, repository_root
from group_pub import index_safety, status_config
from group_pub_commit import DIFF, HOOKS, commit_details, paths
from group_pub_event import EVENTS, regular_path
from group_pub_push import (
    NETWORK_ENV, OBJECT_ID, TRANSPORT, check_destination, check_literal_url,
    remote_head, send_update,
)
from group_status import StatusError, read_members
from group_sync import event_entries, read_discovered_event


def utc_now():
    now = datetime.now(timezone.utc)
    return now, now.strftime('%Y-%m-%dT%H:%M:%SZ')


def new_event_id(person_id, now=None):
    if not _identifier(person_id):
        raise EventError('person_id must be a portable identifier.')
    stamp = now or datetime.now(timezone.utc)
    return f'E-{stamp:%Y%m%dT%H%M%SZ}-{person_id}-{uuid4().hex[:8]}'


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()


def collect_known_events(root, group_id):
    """Collect validated events from local disk, local heads, and origin tracking refs."""
    root = Path(root)
    known = {}
    directory = root / EVENTS
    if directory.is_dir():
        for path in sorted(directory.glob('*.json')):
            event = load_event(path)
            if event['group_id'] == group_id:
                known[event['event_id']] = event
    tips, _ = _git(root, 'for-each-ref', '--format=%(objectname)', 'refs/heads', 'refs/remotes/origin')
    seen_blobs = set()
    for tip in sorted(set(filter(None, tips.splitlines()))):
        for path, mode, kind, blob in event_entries(root, tip):
            key = (path, blob)
            if key in seen_blobs:
                continue
            seen_blobs.add(key)
            try:
                event = read_discovered_event(root, path, mode, kind, blob, group_id)
            except (EventError, GitError):
                continue
            known.setdefault(event['event_id'], event)
    return known


def validate_related_events(root, event, group_id, *, known=None):
    related = event.get('related_events', [])
    if event['event_id'] in related:
        raise EventError('Event cannot reference itself in related_events.')
    if not related:
        return
    if known is None:
        known = collect_known_events(root, group_id)
    missing = [item for item in related if item not in known]
    if missing:
        raise EventError('Unknown related_events IDs: ' + ', '.join(missing))
    if event['event_type'] == 'answer':
        questions = [known[item] for item in related if known[item]['event_type'] == 'question']
        if questions and all(q['question_id'] != event['question_id'] for q in questions):
            raise EventError('Answer related_events does not match question_id.')


def _check_commit_preconditions(root, load_config, person_id, *, allow_canonical=False, own_lock=False):
    config = load_config(root / '.group/group.json')
    if config.get('protocol_version') != '0.1' or not isinstance(config.get('group_id'), str) or not config['group_id'].strip():
        raise EventError('Invalid group.json protocol_version or group_id.')
    canonical = config['canonical_branch']
    members = {m['person_id'] for m in read_members(root)}
    if person_id not in members:
        raise EventError(f'person_id is not a registered group member: {person_id}')
    options, filters_disabled = status_config(root)
    if filters_disabled:
        raise GitError('Configured external Git filters require a separate commit workflow.')
    state = inspect_status(root, canonical, allow_missing_upstream=True,
                           status_config=options, submodules='dirty')
    if not state.branch:
        raise GitError('Detached HEAD refused; a checked-out branch is required.')
    if state.branch.casefold() in (canonical.casefold(), 'main'):
        if not allow_canonical:
            raise GitError('Canonical/main branch is prohibited for this event commit.')
    else:
        parts = state.branch.split('/', 2)
        if len(parts) != 3 or parts[0] != 'people' or parts[1] != person_id or not parts[2]:
            raise GitError('Personal branch must be named people/<person_id>/<topic> owned by person_id.')
    safety, blockers = index_safety(root)
    if own_lock:
        blockers = [b for b in blockers if b != 'Git index lock present.']
    if blockers:
        raise GitError(' '.join(blockers))
    hooks_dir, _ = _git(root, 'rev-parse', '--git-path', 'hooks')
    active = [name for name in HOOKS if (root / hooks_dir / name).is_file()
              and os.access(root / hooks_dir / name, os.X_OK)]
    if active:
        raise GitError('Active Git hooks require a separate commit workflow: ' + ', '.join(active))
    shared_index, _ = _git(root, 'rev-parse', '--shared-index-path')
    sparse, _ = _git(root, 'config', '--bool', '--get', 'core.sparseCheckout', allowed=(0, 1))
    if shared_index or sparse == 'true':
        raise GitError('Split indexes and sparse checkouts require a separate commit workflow.')
    staged = paths(_git(root, *DIFF, '--cached', '--ita-visible-in-index', state.head, '--')[0])
    if staged:
        raise GitError('Event commit requires an empty staged change set.')
    return config, state, options


def commit_event_file(root, load_config, person_id, event, *, message=None,
                      allow_canonical=False, extra_files=None, check_related=True):
    """Write an immutable event JSON and commit it (plus optional extra_files) via isolated index."""
    root = repository_root(root)
    validate_event(event)
    config, state, _ = _check_commit_preconditions(root, load_config, person_id,
                                                   allow_canonical=allow_canonical)
    if event['group_id'] != config['group_id'] or event['person_id'] != person_id:
        raise EventError('Event group_id or person_id does not match repository/operator.')
    if check_related:
        validate_related_events(root, event, config['group_id'])

    event_rel = f'{EVENTS}/{event["event_id"]}.json'
    selected = [event_rel] + list(extra_files or [])
    if len(set(selected)) != len(selected):
        raise GitError('Duplicate paths specified for event commit.')
    commit_msg = (message or f'Record {event["event_type"]} event {event["event_id"]}') + '\n'

    index_path, _ = _git(root, 'rev-parse', '--git-path', 'index')
    index = root / index_path
    lock = index.with_name(index.name + '.lock')
    ref = 'refs/heads/' + state.branch
    head = state.head
    report = dict(phase='validation', branch=state.branch, parent=head,
                  event_id=event['event_id'], event_path=event_rel, files=sorted(selected))

    with TemporaryDirectory(prefix='groupctl-evcommit-') as temporary:
        private_index = Path(temporary) / 'index'
        env = {'GIT_INDEX_FILE': str(private_index)}
        empty_hooks = Path(temporary) / 'empty-hooks'
        empty_hooks.mkdir()
        git_opts = ('--no-replace-objects', '-c', 'core.fsmonitor=false', '-c', 'core.splitIndex=false',
                    '-c', f'core.hooksPath={empty_hooks.as_posix()}')
        with lock.open('xb') as locked_index:
            try:
                _, rechecked, _ = _check_commit_preconditions(
                    root, load_config, person_id, allow_canonical=allow_canonical, own_lock=True)
                if rechecked.branch != state.branch or rechecked.head != head:
                    raise GitError('Branch or HEAD changed before event write.')
                _, ignored = _git(root, 'check-ignore', '-q', '--', event_rel, allowed=(0, 1))
                if ignored == 0:
                    raise EventError('Event path is Git-ignored.')
                directory = root / EVENTS
                regular_path(directory.parent, os.path.isdir if False else __import__('stat').S_ISDIR)
                if directory.exists():
                    regular_path(directory, __import__('stat').S_ISDIR)
                report['phase'] = 'writing_event'
                write_event(root, event)
                report['phase'] = 'event_written'
                raw = (json.dumps(event, ensure_ascii=True, indent=2, allow_nan=False) + '\n').encode('utf-8')
                expected_blob, _ = _git(root, 'hash-object', '--stdin', input_bytes=raw)
                report['event_blob'] = expected_blob

                if index.exists():
                    shutil.copy2(index, private_index)
                else:
                    _git(root, *git_opts, 'read-tree', head, env_overrides=env)
                report['phase'] = 'isolated_index'
                _git(root, *git_opts, '--literal-pathspecs', 'add', '--', *sorted(selected),
                     env_overrides=env)
                tree, _ = _git(root, *git_opts, 'write-tree', env_overrides=env)
                actual = paths(_git(root, *git_opts, *DIFF, head, tree, '--')[0])
                if actual != set(selected):
                    raise GitError('Candidate tree differs from expected event files: ' + repr(sorted(actual)))
                entry, _ = _git(root, *git_opts, '--literal-pathspecs', 'ls-tree', '-r', '-z',
                                tree, '--', event_rel)
                if paths(entry) != {f'100644 blob {expected_blob}\t{event_rel}'}:
                    raise GitError('Candidate tree does not contain the exact event blob.')

                with private_index.open('rb') as contents:
                    shutil.copyfileobj(contents, locked_index)
                locked_index.flush()
                os.fsync(locked_index.fileno())
                st = private_index.stat()
                os.utime(lock, ns=(st.st_atime_ns, st.st_mtime_ns))

                report['phase'] = 'creating_object'
                commit, _ = _git(root, *git_opts, 'commit-tree', tree, '-p', head, '-F', '-',
                                 input_bytes=commit_msg.encode('utf-8'))
                if commit_details(root, commit) != (tree, [head]):
                    raise GitError('Candidate commit tree or parent could not be verified.')
                current_ref, _ = _git(root, 'symbolic-ref', '--no-recurse', '--quiet', 'HEAD')
                if current_ref != ref:
                    raise GitError('Current branch changed before ref update.')
                report['phase'] = 'updating_ref'
                _git(root, *git_opts, 'update-ref', '--no-deref', '-m',
                     f'groupctl {event["event_type"]} event commit', ref, commit, head)
                report.update(phase='ref_updated', commit=commit, tree=tree)
                cur_ref, _ = _git(root, 'symbolic-ref', '--no-recurse', '--quiet', 'HEAD')
                cur_head, _ = _git(root, 'rev-parse', '--verify', 'HEAD^{commit}')
                if (cur_ref, cur_head) != (ref, commit):
                    raise GitError('HEAD or branch changed around ref update; index installation skipped.')
                locked_index.close()
                os.replace(lock, index)
                report['phase'] = 'verified'
            finally:
                locked_index.close()
                if report['phase'] != 'verified':
                    lock.unlink(missing_ok=True)
    return report


def push_and_verify_event(root, *, branch, commit, parent, event_id, event_path, event_blob,
                          remote, expected_url, expected_remote_head=None):
    """Push an event commit to remote and independently verify via fresh bare fetch."""
    root = repository_root(root)
    ref = 'refs/heads/' + branch
    _git(root, 'check-ref-format', ref)
    report = dict(result='not_executed', verified=False, remote=remote, remote_url=expected_url,
                  branch=branch, commit=commit, event_id=event_id, event_path=event_path,
                  event_blob=event_blob)
    args = SimpleNamespace(remote=remote, expected_url=expected_url, expected_head=commit)
    check_destination(root, args)
    hooks_dir, _ = _git(root, 'rev-parse', '--git-path', 'hooks')
    pre_push = root / hooks_dir / 'pre-push'
    if pre_push.is_file() and os.access(pre_push, os.X_OK):
        raise GitError('An active pre-push hook requires a separate push workflow.')

    old = remote_head(root, expected_url, ref, len(commit))
    report['remote_head_before'] = old
    if old == commit:
        _verify_remote_event_commit(root, args, ref, commit, event_id, event_path, event_blob, report)
        report['result'] = 'already_published'
        return report

    if expected_remote_head is not None:
        expected_old = None if expected_remote_head == 'absent' else expected_remote_head
        if old != expected_old:
            raise GitError('Live remote tip differs from expected_remote_head.')
    elif old is not None:
        _, is_anc = _git(root, '--no-replace-objects', 'merge-base', '--is-ancestor',
                         old, commit, allowed=(0, 1))
        if is_anc != 0:
            raise GitError('Remote branch has diverged from local event commit; synchronize first.')

    try:
        send_update(root, args, ref, old, report)
    except GitError:
        try:
            report['remote_head_after'] = remote_head(root, expected_url, ref, len(commit))
            if report['result'] == 'verification_failed' and report['remote_head_after'] != commit:
                report['result'] = 'partial'
        except GitError as exc:
            report['remote_observation_error'] = str(exc)
        raise

    _verify_remote_event_commit(root, args, ref, commit, event_id, event_path, event_blob, report)
    report['result'] = 'published_verified'
    return report


def _verify_remote_event_commit(root, args, ref, commit, event_id, event_path, event_blob, report):
    observed = remote_head(root, args.expected_url, ref, len(commit))
    report['remote_head_after'] = observed
    if observed != commit:
        raise GitError('Post-push live query did not find the event commit at remote tip.')
    with TemporaryDirectory(prefix='groupctl-ev-verify-') as directory:
        bare = Path(directory)
        obj_fmt = 'sha1' if len(commit) == 40 else 'sha256'
        _git(bare, 'init', '--bare', '--template=', f'--object-format={obj_fmt}')
        empty_hooks = bare / 'empty-hooks'
        empty_hooks.mkdir()
        check_literal_url(bare, args.expected_url)
        _git(bare, '--no-replace-objects', *TRANSPORT,
             '-c', f'core.hooksPath={empty_hooks.as_posix()}', '-c', 'fetch.fsckObjects=true',
             'fetch', '--no-tags', '--no-recurse-submodules', '--no-auto-maintenance',
             '--no-write-fetch-head', '--refmap=', '--', args.expected_url,
             f'{ref}:refs/heads/verified', env_overrides=NETWORK_ENV)
        fetched, _ = _git(bare, 'show-ref', '--hash', '--verify', 'refs/heads/verified')
        if fetched != commit:
            raise GitError('Remote branch changed during verification fetch.')
        entry, _ = _git(bare, '--no-replace-objects', '--literal-pathspecs',
                        'ls-tree', '-r', '-z', fetched, '--', event_path)
        if paths(entry) != {f'100644 blob {event_blob}\t{event_path}'}:
            raise EventError('Fetched remote commit does not contain the exact event blob.')
        raw, _ = _git(bare, '--no-replace-objects', 'cat-file', 'blob', event_blob, raw=True)
        parsed = parse_event(raw.decode('utf-8'))
        if parsed['event_id'] != event_id:
            raise EventError('Fetched remote event_id mismatch.')
    report.update(verified=True, remote_head_verified=commit, remote_blob_verified=event_blob)
