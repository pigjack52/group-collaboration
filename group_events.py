"""Protocol 0.1 event validation and immutable repository storage (stdlib only)."""
import json
import os
from datetime import datetime
from pathlib import Path
import re


class EventError(Exception):
    """An event cannot be read, validated, or stored."""


COMMON = {
    "protocol_version": "text", "event_id": "id", "event_type": "text",
    "group_id": "text", "person_id": "text", "created_at": "timestamp",
    "related_events": "ids",
}
# The contract gives examples, rather than explicit required-field lists for
# these payloads. This implementation validates their complete example shape.
PAYLOADS = {
    "change_published": dict(change_id="text", branch="text", commit="text",
        summary="text", files_changed="strings", interface_changes="strings",
        tests_run="strings", known_issues="strings", proposed_for_integration="bool"),
    "finding": dict(summary="text", evidence="strings"),
    "question": dict(question_id="text", text="text", blocking="bool"),
    "answer": dict(question_id="text", text="text"),
    "review": dict(change_id="text", verdict="text", summary="text",
        issues="strings", tests_run="strings"),
    "test_result": dict(scope="text", commit="text", commands="strings",
        automated_result="text", passed="count", failed="count", skipped="count",
        human_checks_required="strings", summary="text"),
    "integration_result": dict(change_id="text", target_branch="text", result="text",
        merge_commit="text", conflicts="strings", conflict_resolution_summary="text",
        verification="verification", summary="text"),
    "blocker": dict(summary="text", blocking_change_id="text", requested_action="text"),
    # Local names for the concepts in sections 12-13; not mandated wire names.
    "final_candidate": dict(canonical_commit="text", candidate_report_path="text",
        tests_evidence="strings", known_issues="strings"),
    "final_result": dict(canonical_commit="text", final_report_path="text",
        test_evidence="strings", known_limitations="strings", initiated_by="text"),
}
ENUMS = {
    "review": ("verdict", {"accept", "accept_with_changes", "reject", "needs_more_evidence"}),
    "test_result": ("automated_result", {"pass", "fail", "partial", "not_available"}),
    "integration_result": ("result", {"success", "failed", "needs_human"}),
}


def _identifier(value):
    # Portable single filename component, including on Windows. IDs are recommended,
    # not prescribed by the contract; do not require the example timestamp pattern.
    return (isinstance(value, str) and len(value) <= 180
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value) is not None
            and not value.endswith(".")
            and value.split(".")[0].upper() not in
            {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))})


def _check(value, kind, field):
    valid = False
    if kind == "text":
        valid = isinstance(value, str) and bool(value.strip())
    elif kind == "id":
        valid = _identifier(value)
    elif kind in ("strings", "ids"):
        valid = isinstance(value, list) and all(
            _identifier(item) if kind == "ids" else
            isinstance(item, str) and bool(item.strip()) for item in value)
    elif kind == "bool":
        valid = type(value) is bool
    elif kind == "count":
        valid = type(value) is int and value >= 0
    elif kind == "timestamp":
        if isinstance(value, str) and re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value):
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
                valid = True
            except ValueError:
                pass
    elif kind == "verification":
        if isinstance(value, dict):
            for key in ("build", "tests", "semantic_review"):
                if key not in value:
                    raise EventError(f"Missing required field: {field}.{key}")
                _check(value[key], "text", f"{field}.{key}")
            valid = True
    if not valid:
        raise EventError(f"Invalid field {field}: expected {kind}")


def validate_event(event):
    """Validate the local protocol profile, retaining extension fields."""
    if not isinstance(event, dict):
        raise EventError("Event must be a JSON object")
    for field, kind in COMMON.items():
        if field not in event:
            raise EventError(f"Missing required field: {field}")
        _check(event[field], kind, field)
    if event["protocol_version"] != "0.1":
        raise EventError("Unsupported protocol_version: expected 0.1")
    event_type = event["event_type"]
    if event_type not in PAYLOADS:
        raise EventError(f"Unsupported event_type: {event_type}")
    for field, kind in PAYLOADS[event_type].items():
        if field not in event:
            raise EventError(f"Missing required field for {event_type}: {field}")
        _check(event[field], kind, field)
    if event_type in ENUMS:
        field, choices = ENUMS[event_type]
        if event[field] not in choices:
            raise EventError(f"Invalid {field}: expected one of {', '.join(sorted(choices))}")
    return event


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EventError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def _constant(value):
    raise EventError(f"Invalid JSON constant: {value}")


def load_event(path):
    path = Path(path)
    try:
        with path.open(encoding="utf-8-sig") as stream:
            event = json.load(stream, object_pairs_hook=_pairs, parse_constant=_constant)
    except json.JSONDecodeError as exc:
        raise EventError(f"Invalid JSON in {path}: line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
    except UnicodeError as exc:
        raise EventError(f"Event must be UTF-8: {path}") from exc
    except OSError as exc:
        raise EventError(f"Cannot read event {path}: {exc}") from exc
    return validate_event(event)


def read_event(root, event_id):
    _check(event_id, "id", "event_id")
    path = Path(root) / ".group" / "events" / f"{event_id}.json"
    event = load_event(path)
    if event["event_id"] != event_id:
        raise EventError(f"Event ID does not match filename: {path}")
    return event


def list_events(root):
    directory = Path(root) / ".group" / "events"
    try:
        if not directory.exists():
            return []
        if not directory.is_dir():
            raise EventError(f"Events path must be a directory: {directory}")
        return [read_event(root, path.stem) for path in sorted(directory.glob("*.json"))]
    except OSError as exc:
        raise EventError(f"Cannot list events {directory}: {exc}") from exc


def write_event(root, event):
    validate_event(event)
    try:
        payload = json.dumps(event, ensure_ascii=True, indent=2, allow_nan=False) + "\n"
    except (ValueError, TypeError) as exc:
        raise EventError(f"Event is not JSON serializable: {exc}") from exc
    directory = Path(root) / ".group" / "events"
    path = directory / f'{event["event_id"]}.json'
    try:
        directory.mkdir(parents=True, exist_ok=True)
        # Exclusive creation prevents overwrites even when concurrent writers race.
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            try:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            except OSError:
                # Never remove a pre-existing event; only this newly created file.
                stream.close()
                path.unlink()
                raise
    except FileExistsError as exc:
        raise EventError(f"Event already exists; refusing to overwrite: {path}") from exc
    except OSError as exc:
        raise EventError(f"Cannot write event {path}: {exc}") from exc
    return path
