"""REV inspection and structured review event publication (never auto-merges)."""
from group_event_publish import (
    collect_known_events, commit_event_file, new_event_id, push_and_verify_event, utc_now,
)
from group_events import EventError, validate_event
from group_git import GitError, _git, repository_root
from group_int import GIT, ancestor, resolve_work
from group_pub_commit import commit_details
from group_status import StatusError, read_members


ALLOWED_VERDICTS = ('accept', 'accept_with_changes', 'reject', 'needs_more_evidence')


def _locate_change(root, config, change_id):
    if not change_id or not change_id.strip():
        raise EventError('A nonblank change_id is required.')
    known = collect_known_events(root, config['group_id'])
    matches = [e for e in known.values()
               if e['event_type'] == 'change_published'
               and e['change_id'].casefold() == change_id.casefold()]
    if not matches:
        raise EventError(f'No change_published event found for {change_id}.')
    if len(matches) > 1:
        raise EventError(f'Conflicting change_published events found for {change_id}.')
    event = matches[0]
    head_sha, _ = _git(root, *GIT, 'rev-parse', '--verify', 'HEAD^{commit}')
    work_sha = resolve_work(root, event['commit'], len(head_sha))
    return event, work_sha, known


def _diff_for_work(root, event, work_sha):
    base = event.get('base_commit')
    base_source = 'declared_base_commit'
    if isinstance(base, str) and base.strip():
        try:
            base_sha = resolve_work(root, base, len(work_sha))
            if not ancestor(root, base_sha, work_sha):
                raise EventError('Declared base_commit is not an ancestor of work commit.')
        except (EventError, GitError):
            base_sha = None
    else:
        base_sha = None
    if base_sha is None:
        _, parents = commit_details(root, work_sha)
        if not parents:
            diff_text, _ = _git(root, *GIT, 'show', '--no-ext-diff', '--no-textconv',
                                '--format=', work_sha, '--')
            changed, _ = _git(root, *GIT, 'diff-tree', '--root', '--no-commit-id',
                              '-r', '--name-only', '-z', work_sha, '--')
            return None, 'root_commit', list(filter(None, changed.split('\0'))), diff_text
        base_sha = parents[0]
        base_source = 'first_parent'
    diff_text, _ = _git(root, *GIT, 'diff', '--no-ext-diff', '--no-textconv',
                        base_sha, work_sha, '--')
    changed, _ = _git(root, *GIT, 'diff', '--no-ext-diff', '--no-textconv',
                      '--name-only', '-z', base_sha, work_sha, '--')
    return base_sha, base_source, list(filter(None, changed.split('\0'))), diff_text


def inspect_change(directory, load_config, args):
    """Read-only inspection of a published change and its real Git diff for REV."""
    report = dict(
        operation='rev_inspect', status='blocked', blockers=[],
        change_id=args.change_id, person_id=args.person_id,
        merge_performed=False,
    )
    try:
        root = repository_root(directory)
        config = load_config(root / '.group/group.json')
        members = {m['person_id'] for m in read_members(root)}
        if args.person_id not in members:
            raise EventError(f'Unknown member person_id: {args.person_id}')
        event, work_sha, known = _locate_change(root, config, args.change_id)
        base_sha, base_source, actual_files, diff_text = _diff_for_work(root, event, work_sha)
        related_reviews = [
            dict(event_id=e['event_id'], person_id=e['person_id'], verdict=e['verdict'], summary=e['summary'])
            for e in known.values()
            if e['event_type'] == 'review' and e['change_id'].casefold() == args.change_id.casefold()
        ]
        related_tests = [
            dict(event_id=e['event_id'], scope=e['scope'], commit=e['commit'],
                 automated_result=e['automated_result'])
            for e in known.values()
            if e['event_type'] == 'test_result' and e['commit'] == work_sha
        ]
        report.update(
            status='ready_for_review',
            event_id=event['event_id'],
            author=event['person_id'],
            branch=event['branch'],
            work_commit=work_sha,
            diff_base=base_sha,
            diff_base_source=base_source,
            declared_summary=event['summary'],
            declared_files_changed=event['files_changed'],
            actual_files_changed=actual_files,
            interface_changes=event['interface_changes'],
            tests_run=event['tests_run'],
            known_issues=event['known_issues'],
            proposed_for_integration=event['proposed_for_integration'],
            existing_reviews=related_reviews,
            tests_for_work_commit=related_tests,
            diff=diff_text,
        )
    except (EventError, GitError, OSError, StatusError, ValueError) as exc:
        report['blockers'].append(str(exc))
    return report


def publish_review(directory, load_config, args):
    """Create, commit, and optionally push+verify a review event. Never merges code."""
    report = dict(
        operation='rev_publish', status='blocked', verified=False,
        change_id=args.change_id, person_id=args.person_id,
        merge_performed=False, blockers=[],
    )
    try:
        if not args.diff_inspected:
            raise EventError('Review requires explicit --diff-inspected attestation.')
        if args.verdict not in ALLOWED_VERDICTS:
            raise EventError(f'Invalid verdict: expected one of {", ".join(ALLOWED_VERDICTS)}')
        if not args.summary or not args.summary.strip():
            raise EventError('A nonblank --summary is required.')

        root = repository_root(directory)
        config = load_config(root / '.group/group.json')
        members = {m['person_id'] for m in read_members(root)}
        if args.person_id not in members:
            raise EventError(f'Unknown member person_id: {args.person_id}')

        event_pub, work_sha, _ = _locate_change(root, config, args.change_id)
        if not args.inspected_commit or args.inspected_commit.lower() != work_sha.lower():
            raise EventError(
                f'--inspected-commit must exactly match the published work commit {work_sha}.'
            )

        now, created_at = utc_now()
        event = validate_event(dict(
            protocol_version='0.1',
            event_id=new_event_id(args.person_id, now),
            event_type='review',
            group_id=config['group_id'],
            person_id=args.person_id,
            created_at=created_at,
            related_events=[event_pub['event_id']],
            change_id=args.change_id,
            verdict=args.verdict,
            summary=args.summary,
            issues=list(args.issue or []),
            tests_run=list(args.test_run or []),
            inspected_commit=work_sha,
            diff_inspected=True,
        ))
        commit_rep = commit_event_file(
            root, load_config, args.person_id, event,
            message=f'Record review ({args.verdict}) for {args.change_id}',
            check_related=True,
        )
        report.update(
            event_id=event['event_id'],
            event_path=commit_rep['event_path'],
            event_commit=commit_rep['commit'],
            event_blob=commit_rep['event_blob'],
            verdict=args.verdict,
            publication='local_committed',
            status='local_event_committed',
        )
        if args.remote:
            if not (args.expected_url and args.confirm_reviewed):
                raise GitError('Pushing review requires --remote, --expected-url, and --confirm-reviewed.')
            push_rep = push_and_verify_event(
                root, branch=commit_rep['branch'], commit=commit_rep['commit'],
                parent=commit_rep['parent'], event_id=event['event_id'],
                event_path=commit_rep['event_path'], event_blob=commit_rep['event_blob'],
                remote=args.remote, expected_url=args.expected_url,
                expected_remote_head=args.expected_remote_head,
            )
            report['push'] = push_rep
            report['verified'] = push_rep['verified']
            report['publication'] = push_rep['result']
            report['status'] = push_rep['result']
    except (EventError, GitError, OSError, StatusError, ValueError) as exc:
        report['blockers'].append(str(exc))
    return report
