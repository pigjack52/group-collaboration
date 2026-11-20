"""Configuration and local event commands for Group Collaboration."""
import argparse
import json
from pathlib import Path
import sys

from group_events import EventError, load_event, read_event, list_events, write_event
from group_git import GitError, repository_root, inspect_status, format_status
from group_status import StatusError, collect_status, format_group_status
from group_sync import discover
from group_update import update
from group_int import fast_forward, prepare_merge, preflight as int_preflight
from group_test import run_tests
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
    git = commands.add_parser("git", help="Read-only local Git inspection")
    git_actions = git.add_subparsers(dest="action", required=True)
    git_actions.add_parser("status", help="Inspect local refs without fetching (not full STATUS)")
    commands.add_parser('status', help='Analyze local group records without fetching (not full STATUS)')
    sync = commands.add_parser('sync', help='Fetch and discover remote records; no integration')
    modes = sync.add_mutually_exclusive_group(required=True)
    modes.add_argument('--discover', action='store_true')
    modes.add_argument('--update', action='store_true')
    integration = commands.add_parser('int', help='Inspect a published change and local integration target')
    modes = integration.add_mutually_exclusive_group(required=True)
    modes.add_argument('--preflight', action='store_true')
    modes.add_argument('--ff-only', action='store_true')
    modes.add_argument('--prepare-merge', action='store_true')
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
    integration.add_argument('--confirm-reviewed', action='store_true')
    integration.add_argument('--confirm-unproposed', action='store_true')
    test = commands.add_parser('test', help='Test current clean HEAD; create a local unpublished event')
    test.add_argument('--person-id', required=True, help='Explicit configured member person_id')
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
        if (args.ff_only or args.prepare_merge) and not (all(reviewed) and args.confirm_reviewed):
            mode = '--ff-only' if args.ff_only else '--prepare-merge'
            integration.error(f'{mode} requires all reviewed evidence options and --confirm-reviewed')
        if args.preflight and (any(value is not None for value in reviewed)
                               or args.confirm_reviewed or args.confirm_unproposed):
            integration.error('Reviewed evidence options require --ff-only or --prepare-merge')
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
    # Resolve from the invocation directory, never from the installed tool.
    path = Path.cwd() / ".group" / "group.json"
    try:
        if args.command == 'pub':
            if args.event_commit:
                report = event_commit(Path.cwd(), load_config, args)
                print(json.dumps(report, indent=2, ensure_ascii=True))
                return int(report['result'] != 'local_event_committed')
            if args.push_work or args.push_event:
                operation = push_event if args.push_event else push_work
                report = operation(Path.cwd(), load_config, args)
                print(json.dumps(report, indent=2, ensure_ascii=True))
                return int(not report['verified'])
            if not args.preflight:
                report = work_commit(Path.cwd(), load_config, args)
                print(json.dumps(report, indent=2, ensure_ascii=True))
                return int(report['result'] not in ('created', 'existing'))
            root = repository_root(Path.cwd())
            report = preflight(root, load_config(root / '.group' / 'group.json'), args.person_id)
            print(json.dumps(report, indent=2, ensure_ascii=True))
            return int(bool(report['blockers']))
        if args.command == 'int':
            if args.ff_only:
                report = fast_forward(Path.cwd(), load_config, args)
            elif args.prepare_merge:
                report = prepare_merge(Path.cwd(), load_config, args)
            else:
                report = int_preflight(Path.cwd(), load_config, args.person_id,
                                       args.change_id, args.target_branch)
            print(json.dumps(report, indent=2, ensure_ascii=True))
            return int(bool(report['blockers']))
        if args.command == 'test':
            # Stable Chinese output when redirected from Windows PowerShell.
            if hasattr(sys.stdout, 'reconfigure'):
                sys.stdout.reconfigure(encoding='utf-8')
            root = repository_root(Path.cwd())
            return run_tests(root, load_config(root / '.group' / 'group.json'), args.person_id)
        if args.command == 'sync':
            root = repository_root(Path.cwd())
            operation = update if args.update else discover
            return operation(root, load_config(root / '.group' / 'group.json'))
        if args.command == "status":
            root = repository_root(Path.cwd())
            config = load_config(root / ".group" / "group.json")
            report = collect_status(root, config)
            print(format_group_status(report))
            return 0
        if args.command == "git":
            root = repository_root(Path.cwd())
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
                write_event(Path.cwd(), result)
        elif args.action == "show":
            result = read_event(Path.cwd(), args.event_id)
        else:
            result = list_events(Path.cwd())
    except (ConfigError, EventError, GitError, StatusError) as exc:
        print(f"groupctl: error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
