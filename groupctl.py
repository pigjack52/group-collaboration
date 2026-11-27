"""Configuration and local event commands for Group Collaboration."""
import argparse
import json
from pathlib import Path
import re
import sys

from group_events import EventError, load_event, read_event, list_events, write_event
from group_git import GitError, repository_root, inspect_status, format_status
from group_status import StatusError, collect_status, format_group_status
from group_sync import discover
from group_update import update
from group_int import fast_forward, inspect_merge, prepare_merge, preflight as int_preflight
from group_int_finish import commit_merge, publish_integration_result, verify_integration
from group_rev import inspect_change, publish_review
from group_final import prepare_final, publish_final
from group_event_publish import commit_event_file, push_and_verify_event
from group_test import run_scoped_tests, run_tests
from group_pub import preflight
from group_pub_commit import work_commit
from group_pub_push import push_work
from group_pub_event import event_commit
from group_pub_event_push import push_event

REQUIRED_FIELDS = (
    "protocol_version", "group_id", "canonical_branch", "assignment_file",
)


class ConfigError(ValueError):
    """A configuration cannot be read or violates the repository contract."""


def load_config(path):
    """Read the contract's required fields, preserving optional fields."""
    try:
        with path.open(encoding="utf-8-sig") as stream:
            config = json.load(stream)
    except FileNotFoundError as exc:
        raise ConfigError(f"Configuration file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"Invalid JSON in {path}: line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    except UnicodeError as exc:
        raise ConfigError(f"Configuration must be UTF-8: {path}") from exc
    except OSError as exc:
        raise ConfigError(f"Cannot read configuration {path}: {exc}") from exc
    if not isinstance(config, dict):
        raise ConfigError(f"Configuration must be a JSON object: {path}")
    missing = [field for field in REQUIRED_FIELDS if field not in config]
    if missing:
        raise ConfigError(f"Missing required fields in {path}: {', '.join(missing)}")
    return config


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="groupctl",
        description="Group Collaboration configuration and local event helper.",
    )
    parser.add_argument("--version", action="version", version="groupctl 0.1 (protocol 0.1)")
    parser.add_argument("--repo", type=Path, help="Target repository directory (defaults to current working directory)")
    commands = parser.add_subparsers(dest="command", required=True)
    config = commands.add_parser("config", help="Read group configuration")
    actions = config.add_subparsers(dest="action", required=True)
    actions.add_parser("show", help="Validate and display .group/group.json")
    events = commands.add_parser("events", help="Validate, create, read, or list local events")
    event_actions = events.add_subparsers(dest="action", required=True)
    for name in ("validate", "create"):
        action = event_actions.add_parser(name, help=f"{name.title()} an event from a JSON file")
        action.add_argument("file", type=Path)
    show = event_actions.add_parser("show", help="Read an event by ID")
    show.add_argument("event_id")
    event_actions.add_parser("list", help="List validated events in filename order")
    ev_pub = event_actions.add_parser("publish", help="Commit and optionally push a validated event JSON")
    ev_pub.add_argument("file", type=Path)
    ev_pub.add_argument("--person-id", required=True)
    ev_pub.add_argument("--remote")
    ev_pub.add_argument("--expected-url")
    ev_pub.add_argument("--expected-remote-head")
    ev_pub.add_argument("--confirm-reviewed", action="store_true")
    git = commands.add_parser("git", help="Read-only local Git inspection")
    git_actions = git.add_subparsers(dest="action", required=True)
    git_actions.add_parser("status", help="Inspect local refs without fetching (not full STATUS)")
    status_cmd = commands.add_parser('status', help='Analyze group records (offline by default; use --fetch for live remote check)')
    status_cmd.add_argument('--fetch', action='store_true', help='Fetch origin metadata to check remote freshness and pending changes')
    sync = commands.add_parser('sync', help='Fetch and discover remote records; no integration')
    modes = sync.add_mutually_exclusive_group(required=True)
    modes.add_argument('--discover', action='store_true')
    modes.add_argument('--update', action='store_true')
    integration = commands.add_parser('int', help='Inspect a published change and local integration target')
    modes = integration.add_mutually_exclusive_group(required=True)
    modes.add_argument('--preflight', action='store_true')
    modes.add_argument('--ff-only', action='store_true')
    modes.add_argument('--prepare-merge', action='store_true')
    modes.add_argument('--inspect-merge', action='store_true', help='Diagnose a pending merge without writing')
    modes.add_argument('--commit-merge', action='store_true', help='Commit a reviewed pending merge with staged tree verification')
    modes.add_argument('--verify', action='store_true', help='Run Level 1 Git and Level 2 isolated test checks on an integration commit')
    modes.add_argument('--publish-result', action='store_true', help='Create, commit, and optionally push an integration_result event')
    integration.add_argument('--person-id', required=True)
    integration.add_argument('--change-id', required=True)
    integration.add_argument('--target-branch', required=True, help='Exact local target branch name')
    integration.add_argument('--expected-head')
    integration.add_argument('--work-sha')
    integration.add_argument('--expected-event-id')
    integration.add_argument('--expected-event-blob')
    integration.add_argument('--expected-source-ref')
    integration.add_argument('--expected-source-tip')
    integration.add_argument('--expected-incoming', help='Comma-separated full T..A SHAs, or none')
    integration.add_argument('--expected-tree', help='Reviewed staged tree OID from --inspect-merge')
    integration.add_argument('--message', help='Merge commit message for --commit-merge')
    integration.add_argument('--merge-commit', help='Exact integration commit SHA for --verify or --publish-result')
    integration.add_argument('--result', choices=('success', 'failed', 'needs_human'))
    integration.add_argument('--build', dest='build_status', default='not_applicable')
    integration.add_argument('--tests', dest='tests_status')
    integration.add_argument('--semantic-review', dest='semantic_review')
    integration.add_argument('--summary')
    integration.add_argument('--conflict', action='append', help='Conflicted file path; repeat for multiple files')
    integration.add_argument('--conflict-resolution-summary')
    integration.add_argument('--remote')
    integration.add_argument('--expected-url')
    integration.add_argument('--expected-remote-head')
    integration.add_argument('--confirm-reviewed', action='store_true')
    integration.add_argument('--confirm-unproposed', action='store_true')
    rev = commands.add_parser('rev', help='Inspect a published change and publish a review event (never merges)')
    rev_modes = rev.add_mutually_exclusive_group(required=True)
    rev_modes.add_argument('--inspect', action='store_true')
    rev_modes.add_argument('--publish', action='store_true')
    rev.add_argument('--person-id', required=True)
    rev.add_argument('--change-id', required=True)
    rev.add_argument('--inspected-commit')
    rev.add_argument('--diff-inspected', action='store_true')
    rev.add_argument('--verdict', choices=('accept', 'accept_with_changes', 'reject', 'needs_more_evidence'))
    rev.add_argument('--summary')
    rev.add_argument('--issue', action='append')
    rev.add_argument('--test-run', action='append')
    rev.add_argument('--remote')
    rev.add_argument('--expected-url')
    rev.add_argument('--expected-remote-head')
    rev.add_argument('--confirm-reviewed', action='store_true')
    final = commands.add_parser('final', help='Prepare and publish human-confirmed group finalization')
    final_modes = final.add_mutually_exclusive_group(required=True)
    final_modes.add_argument('--prepare', action='store_true')
    final_modes.add_argument('--publish', action='store_true')
    final.add_argument('--person-id', required=True)
    final.add_argument('--result-summary')
    final.add_argument('--disposition', action='append', help='CHANGE_ID=reason for unincorporated change')
    final.add_argument('--limitation', action='append')
    final.add_argument('--simulation', action='store_true', help='Explicitly mark generated candidate as simulation')
    final.add_argument('--candidate-id')
    final.add_argument('--confirmation-file', type=Path)
    final.add_argument('--confirm-human', action='store_true')
    final.add_argument('--allow-known-limitations', action='store_true')
    final.add_argument('--remote')
    final.add_argument('--expected-url')
    final.add_argument('--expected-remote-head')
    test = commands.add_parser('test', help='Run unittest validation for current, main, or change_id scope')
    test.add_argument('--person-id', required=True, help='Explicit configured member person_id')
    test.add_argument('--scope', default='current', help='current, main/canonical, or a change_id')
    test.add_argument('--isolated', action='store_true', help='Run in an isolated detached worktree')
    test.add_argument('--publish', action='store_true', help='Commit (and optionally push) the test_result event')
    test.add_argument('--human-check', action='append', help='Concrete human verification check; repeatable')
    test.add_argument('--remote')
    test.add_argument('--expected-url')
    test.add_argument('--expected-remote-head')
    test.add_argument('--confirm-reviewed', action='store_true')
    pub = commands.add_parser('pub', help='Publication preflight, commit and push work or events')
    modes = pub.add_mutually_exclusive_group(required=True)
    modes.add_argument('--preflight', action='store_true')
    modes.add_argument('--work-commit', action='store_true')
    modes.add_argument('--use-existing', action='store_true')
    modes.add_argument('--push-work', action='store_true')
    modes.add_argument('--event-commit', action='store_true')
    modes.add_argument('--push-event', action='store_true')
    pub.add_argument('--person-id', required=True, help='Explicit configured member person_id')
    pub.add_argument('--expected-head', help='Full reviewed HEAD object ID (event commit E for --push-event)')
    pub.add_argument('--expected-branch', help='Reviewed personal branch name')
    pub.add_argument('--file', action='append', help='Literal repository-relative file; repeat to select multiple files')
    pub.add_argument('--message', help='Work commit message')
    pub.add_argument('--remote', help='Explicit configured remote name')
    pub.add_argument('--expected-url', help='Reviewed remote fetch and push URL')
    pub.add_argument('--expected-remote-head', help='Reviewed remote tip object ID (or absent for --push-work)')
    pub.add_argument('--work-sha', help='Full SHA of the published work commit')
    pub.add_argument('--change-id', help='Unique reviewed change ID')
    pub.add_argument('--review-file', type=Path, help='JSON file containing reviewed event details')
    pub.add_argument('--confirm-reviewed', action='store_true',
                     help='Confirm human authorization and review of the actual contribution')
    args = parser.parse_args(argv)
    if args.command == 'int':
        reviewed = (args.expected_head, args.work_sha, args.expected_event_id,
                    args.expected_event_blob, args.expected_source_ref,
                    args.expected_source_tip, args.expected_incoming)
        if (args.ff_only or args.prepare_merge or args.commit_merge) and not (all(reviewed) and args.confirm_reviewed):
            mode = '--ff-only' if args.ff_only else '--prepare-merge' if args.prepare_merge else '--commit-merge'
            integration.error(f'{mode} requires all reviewed evidence options and --confirm-reviewed')
        if args.commit_merge and not (args.expected_tree and args.message and args.message.strip()):
            integration.error('--commit-merge requires --expected-tree and a nonblank --message')
        if args.publish_result and not (args.merge_commit and args.result and args.tests_status
                                        and args.semantic_review and args.summary and args.summary.strip()):
            integration.error('--publish-result requires --merge-commit, --result, --tests, --semantic-review and --summary')
        has_evidence = (any(value is not None for value in reviewed)
                        or args.confirm_reviewed or args.confirm_unproposed)
        if args.preflight and has_evidence:
            integration.error('Reviewed evidence options require --ff-only, --prepare-merge, --inspect-merge or --commit-merge')
        if args.inspect_merge and has_evidence:
            if not (all(reviewed) and args.confirm_reviewed):
                integration.error('--inspect-merge requires the complete reviewed evidence or none')
            oids = [args.expected_head, args.work_sha, args.expected_event_blob,
                    args.expected_source_tip]
            if args.expected_incoming != 'none':
                oids.extend(args.expected_incoming.split(','))
            if any(not re.fullmatch(r'(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})', oid) for oid in oids):
                integration.error('--inspect-merge evidence requires full hexadecimal object IDs')
    if args.command == 'pub':
        push_options = (args.remote, args.expected_url, args.expected_remote_head)
        event_options = (args.work_sha, args.change_id, args.review_file)
        if args.event_commit:
            if not all(event_options) or not all(push_options[:2]):
                pub.error('--event-commit requires --work-sha, --change-id, --review-file, --remote and --expected-url')
            if any(value is not None for value in (args.file, args.message, args.expected_remote_head)):
                pub.error('--event-commit does not accept --file, --message or --expected-remote-head')
        elif args.push_event:
            if not all(event_options[:2]) or not all(push_options):
                pub.error('--push-event requires --work-sha, --change-id, --remote, --expected-url and --expected-remote-head')
            if any(value is not None for value in (args.file, args.message, args.review_file)):
                pub.error('--push-event does not accept --file, --message or --review-file')
        elif any(value is not None for value in event_options):
            pub.error('event options require --event-commit or --push-event')
        if args.push_work and (not all(push_options) or args.file is not None or args.message is not None):
            pub.error('--push-work requires --remote, --expected-url and --expected-remote-head without --file or --message')
        if not (args.push_work or args.event_commit or args.push_event) and any(value is not None for value in push_options):
            pub.error('remote options require --push-work, --event-commit or --push-event')
        commit_options = (args.expected_head, args.expected_branch, args.file, args.message)
        if args.preflight and (any(value is not None for value in commit_options) or args.confirm_reviewed):
            pub.error('work commit options cannot be used with --preflight')
        if not args.preflight and not (args.expected_head and args.expected_branch and args.confirm_reviewed):
            pub.error('PUB write modes require --expected-head, --expected-branch and --confirm-reviewed')
        if args.work_commit and not (args.file and args.message and args.message.strip()):
            pub.error('--work-commit requires --file and a nonblank --message')
        if args.use_existing and (args.file is not None or args.message is not None):
            pub.error('--use-existing selects the reviewed HEAD and does not accept --file or --message')
    # Resolve from --repo when provided, otherwise from the invocation directory.
    base_dir = args.repo.resolve() if args.repo else Path.cwd()
    path = base_dir / ".group" / "group.json"
    try:
        if args.command == 'pub':
            if args.event_commit:
                report = event_commit(base_dir, load_config, args)
                print(json.dumps(report, indent=2, ensure_ascii=True))
                return int(report['result'] != 'local_event_committed')
            if args.push_work or args.push_event:
                operation = push_event if args.push_event else push_work
                report = operation(base_dir, load_config, args)
                print(json.dumps(report, indent=2, ensure_ascii=True))
                return int(not report['verified'])
            if not args.preflight:
                report = work_commit(base_dir, load_config, args)
                print(json.dumps(report, indent=2, ensure_ascii=True))
                return int(report['result'] not in ('created', 'existing'))
            root = repository_root(base_dir)
            report = preflight(root, load_config(root / '.group' / 'group.json'), args.person_id)
            print(json.dumps(report, indent=2, ensure_ascii=True))
            return int(bool(report['blockers']))
        if args.command == 'int':
            if args.ff_only:
                report = fast_forward(base_dir, load_config, args)
            elif args.prepare_merge:
                report = prepare_merge(base_dir, load_config, args)
            elif args.inspect_merge:
                report = inspect_merge(base_dir, load_config, args)
            elif args.commit_merge:
                report = commit_merge(base_dir, load_config, args)
            elif args.verify:
                report = verify_integration(base_dir, load_config, args)
            elif args.publish_result:
                report = publish_integration_result(base_dir, load_config, args)
            else:
                report = int_preflight(base_dir, load_config, args.person_id,
                                       args.change_id, args.target_branch)
            print(json.dumps(report, indent=2, ensure_ascii=True))
            return int(bool(report['blockers']))
        if args.command == 'rev':
            report = publish_review(base_dir, load_config, args) if args.publish else inspect_change(base_dir, load_config, args)
            print(json.dumps(report, indent=2, ensure_ascii=True))
            return int(bool(report['blockers']))
        if args.command == 'final':
            report = publish_final(base_dir, load_config, args) if args.publish else prepare_final(base_dir, load_config, args)
            print(json.dumps(report, indent=2, ensure_ascii=True))
            return int(bool(report['blockers']))
        if args.command == 'test':
            # Stable Chinese output when redirected from Windows PowerShell.
            if hasattr(sys.stdout, 'reconfigure'):
                sys.stdout.reconfigure(encoding='utf-8')
            root = repository_root(base_dir)
            return run_scoped_tests(
                root, load_config(root / '.group' / 'group.json'), args.person_id,
                scope=args.scope, isolated=args.isolated, publish=args.publish,
                remote=args.remote, expected_url=args.expected_url,
                expected_remote_head=args.expected_remote_head,
                confirm_reviewed=args.confirm_reviewed,
                human_checks=args.human_check,
            )
        if args.command == 'sync':
            root = repository_root(base_dir)
            operation = update if args.update else discover
            return operation(root, load_config(root / '.group' / 'group.json'))
        if args.command == "status":
            root = repository_root(base_dir)
            config = load_config(root / ".group" / "group.json")
            report = collect_status(root, config, fetch=args.fetch)
            print(format_group_status(report))
            return 0
        if args.command == "git":
            root = repository_root(base_dir)
            config_path = root / ".group" / "group.json"
            canonical = load_config(config_path)["canonical_branch"] if config_path.exists() else "main"
            status = inspect_status(root, canonical)
            print(format_status(status))
            if status.canonical_commit is None:
                print(f"groupctl: error: Canonical branch {canonical!r} unavailable", file=sys.stderr)
                return 1
            return 0
        if args.command == "config":
            result = load_config(path)
        elif args.action in ("validate", "create"):
            result = load_event(args.file)
            if args.action == "create":
                write_event(base_dir, result)
        elif args.action == "publish":
            event = load_event(args.file)
            if event['event_type'] == 'change_published':
                raise EventError("Use 'groupctl pub' to publish change_published events with work-commit verification.")
            root = repository_root(base_dir)
            commit_rep = commit_event_file(root, load_config, args.person_id, event)
            result = dict(commit_rep, publication='local_committed', verified=False)
            if args.remote:
                if not (args.expected_url and args.confirm_reviewed):
                    raise GitError('events publish --remote requires --expected-url and --confirm-reviewed.')
                push_rep = push_and_verify_event(
                    root, branch=commit_rep['branch'], commit=commit_rep['commit'],
                    parent=commit_rep['parent'], event_id=event['event_id'],
                    event_path=commit_rep['event_path'], event_blob=commit_rep['event_blob'],
                    remote=args.remote, expected_url=args.expected_url,
                    expected_remote_head=args.expected_remote_head,
                )
                result.update(publication=push_rep['result'], verified=push_rep['verified'], push=push_rep)
        elif args.action == "show":
            result = read_event(base_dir, args.event_id)
        else:
            result = list_events(base_dir)
    except (ConfigError, EventError, GitError, StatusError) as exc:
        print(f"groupctl: error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
