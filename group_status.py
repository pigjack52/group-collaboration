"""Local group STATUS analysis. Event declarations are never Git proof."""
from collections import Counter
from datetime import datetime
import json
from pathlib import Path

from group_events import EventError, PAYLOADS, read_event
from group_git import inspect_status, format_status


class StatusError(Exception):
    """Invalid local group data, with its source path."""


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _files(directory):
    try:
        if not directory.exists():
            return []
        if not directory.is_dir():
            raise StatusError(f"Expected directory: {directory}")
        return sorted(directory.glob("*.json"))
    except OSError as exc:
        raise StatusError(f"Cannot list {directory}: {exc}") from exc


def read_members(root):
    members = []
    for path in _files(Path(root) / ".group" / "members"):
        try:
            member = json.loads(path.read_text(encoding="utf-8-sig"),
                                object_pairs_hook=_pairs)
            if not isinstance(member, dict):
                raise ValueError("member must be a JSON object")
            for field in ("protocol_version", "person_id", "display_name", "joined_at"):
                if not isinstance(member.get(field), str) or not member[field].strip():
                    raise ValueError(f"missing or invalid field: {field}")
            if member["protocol_version"] != "0.1":
                raise ValueError("unsupported protocol_version")
            if member["person_id"] != path.stem:
                raise ValueError("person_id does not match filename")
            joined = datetime.fromisoformat(member["joined_at"].replace("Z", "+00:00"))
            if joined.tzinfo is None:
                raise ValueError("joined_at requires a timezone")
            members.append(member)
        except (OSError, ValueError, UnicodeError) as exc:
            raise StatusError(f"Invalid member {path}: {exc}") from exc
    return members


def collect_status(root, config):
    """Collect validated inputs without writing or fetching."""
    root = Path(root)
    for field in ("group_id", "canonical_branch"):
        if not isinstance(config.get(field), str) or not config[field].strip():
            raise StatusError(f"{root / '.group/group.json'}: invalid {field}")
    git = inspect_status(root, config["canonical_branch"])
    members = read_members(root)
    events = []
    for path in _files(root / ".group" / "events"):
        try:
            event = read_event(root, path.stem)
        except EventError as exc:
            raise StatusError(f"Invalid event {path}: {exc}") from exc
        if event["group_id"] != config["group_id"]:
            raise StatusError(f"Event {path}: group_id differs from group.json")
        events.append(event)
    warnings = []
    if not members:
        warnings.append("Members: not yet recorded")
    if git.canonical_commit is None:
        warnings.append(f"Canonical branch {config['canonical_branch']}: unknown (no local ref)")
    ids = {e["event_id"] for e in events}
    people = {m["person_id"] for m in members}
    changes = Counter(e["change_id"] for e in events if e["event_type"] == "change_published")
    for change, count in sorted(changes.items()):
        if count > 1:
            warnings.append(f"Duplicate change_id {change}: ambiguous declarations; no resolution inferred")
    for event in events:
        source = root / ".group/events" / (event["event_id"] + ".json")
        if event["person_id"] not in people:
            warnings.append(f"{source}: author {event['person_id']} has no member record")
        for related in event["related_events"]:
            if related not in ids:
                warnings.append(f"{source}: related event {related} not found")
        change = event.get("change_id", event.get("blocking_change_id"))
        if change and event["event_type"] != "change_published" and change not in changes:
            warnings.append(f"{source}: change {change} has no change_published record")
    return dict(config=config, git=git, members=members, events=events, warnings=warnings)


def _quoted(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def format_group_status(report):
    config, git, events = report["config"], report["git"], report["events"]
    lines = [
        "Group STATUS: read-only local analysis; not the full AI STATUS procedure",
        f"Group: {_quoted(config['group_id'])}; canonical branch: {_quoted(config['canonical_branch'])}",
        format_status(git),
        "Members: " + (", ".join(
            f"{_quoted(m['person_id'])} ({_quoted(m['display_name'])})"
            for m in report["members"]) or "not yet recorded"),
        "Current operator identity: unknown (member list does not identify the operator)",
        f"Events: {len(events)} (local files; publication/push not verified)",
        "Event counts: " + ", ".join(
            f"{kind}={sum(e['event_type'] == kind for e in events)}"
            for kind in PAYLOADS),
    ]
    by_id = {e["event_id"]: e for e in events}

    def ancestors(event):
        pending = list(event["related_events"])
        found = set()
        while pending:
            identifier = pending.pop()
            if identifier in found:
                continue
            found.add(identifier)
            if identifier in by_id:
                pending.extend(by_id[identifier]["related_events"])
        return found

    for event in events:
        # Keep event ID, author, time, relationships and all payload evidence visible.
        lines.append("Event: " + _quoted(event))
    changes = [e for e in events if e["event_type"] == "change_published"]
    lines.append("Published change declarations: " + (str(len(changes)) if changes else "not yet recorded"))
    for change in changes:
        lines.append(f"Change {_quoted(change['change_id'])} [{change['event_id']}]: "
                     f"commit={_quoted(change['commit'])}; summary={_quoted(change['summary'])}")
        related = [
            e for e in events if e is not change and (
                e.get("change_id") == change["change_id"]
                or e.get("blocking_change_id") == change["change_id"]
                or change["event_id"] in ancestors(e)
                or (e["event_type"] == "test_result" and e["commit"] == change["commit"])
            )
        ]
        for kind in ("review", "integration_result", "test_result", "blocker"):
            selected = [e for e in related if e["event_type"] == kind]
            lines.append(f"  {kind} records: " + (
                ", ".join(e["event_id"] for e in selected) or "not yet recorded"))
        lines.append("  Actual integration into HEAD/canonical: unknown (Git ancestry not verified)")
    for label, commit in (("HEAD", git.head), ("canonical", git.canonical_commit)):
        tests = [e for e in events if e["event_type"] == "test_result" and e["commit"] == commit]
        attributed = []
        for event in tests:
            execution = event.get("execution")
            if isinstance(execution, dict) and execution.get("state_verified") is False:
                lines.append(f"Test {event['event_id']}={event['automated_result']}: "
                             f"recorded {label} commit attribution unverified")
            else:
                attributed.append(f"{event['event_id']}={event['automated_result']}")
        lines.append(f"Tests for exact {label} commit: " +
                     (", ".join(attributed) or "not yet recorded; unknown"))
    blockers = [e["event_id"] for e in events if e["event_type"] == "blocker"]
    lines.append("Blocker records: " + (", ".join(blockers) or "not yet recorded"))
    lines += [
        "Blocker resolution: unknown (no resolution inferred from later events)",
        "Evidence limits: review accept is not integration; integration_result is a declaration.",
        "Test results apply only to their recorded commit/scope; related_events does not extend coverage.",
        "Event order does not establish latest progress; conflicting records are retained.",
    ]
    lines.extend("WARNING: " + warning for warning in report["warnings"])
    return "\n".join(lines)
