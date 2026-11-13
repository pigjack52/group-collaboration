"""Configuration and local event commands for Group Collaboration."""
import argparse
import json
from pathlib import Path
import sys

from group_events import EventError, load_event, read_event, list_events, write_event

REQUIRED_FIELDS = (
    "protocol_version", "group_id", "canonical_branch", "assignment_file",
)


class ConfigError(Exception):
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
    args = parser.parse_args(argv)
    # Resolve from the invocation directory, never from the installed tool.
    path = Path.cwd() / ".group" / "group.json"
    try:
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
    except (ConfigError, EventError) as exc:
        print(f"groupctl: error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())