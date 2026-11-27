"""Local group STATUS analysis. Event declarations are never Git proof."""
from collections import Counter
from dataclasses import dataclass
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


def collect_status(root, config, *, fetch=False):
    """Collect validated inputs without writing; optionally fetch remote metadata when fetch=True."""
    root = Path(root)
    for field in ("group_id", "canonical_branch"):
        if not isinstance(config.get(field), str) or not config[field].strip():
            raise StatusError(f"{root / '.group/group.json'}: invalid {field}")
    remote_fetched = False
    remote_pending_changes = []
    if fetch:
        import os
        from group_git import _git
        from group_sync import event_entries, read_discovered_event
        pre = inspect_status(root, config["canonical_branch"], allow_missing_upstream=True)
        if pre.origin_exists:
            _git(root, '-c', 'core.hooksPath=' + os.devnull, 'fetch',
                 '--no-tags', '--no-recurse-submodules', '--no-write-fetch-head',
                 '--prune', '--no-prune-tags', '--refmap=', 'origin',
                 '+refs/heads/*:refs/remotes/origin/*')
            remote_fetched = True
    git = inspect_status(root, config["canonical_branch"])
    if remote_fetched:
        from group_git import _git
        from group_sync import event_entries, read_discovered_event
        raw_refs, _ = _git(root, 'for-each-ref', '--format=%(refname) %(objectname)',
                           'refs/remotes/origin/')
        seen_changes = {}
        for line in raw_refs.splitlines():
            if line.startswith('refs/remotes/origin/HEAD '):
                continue
            ref, tip = line.split(' ', 1)
            for path, mode, kind, blob in event_entries(root, tip):
                try:
                    ev = read_discovered_event(root, path, mode, kind, blob, config["group_id"])
                except Exception:
                    continue
                if ev['event_type'] == 'change_published':
                    seen_changes.setdefault(ev['change_id'], ev)
        for cid, ev in sorted(seen_changes.items()):
            incorporated = False
            if git.canonical_commit:
                _, code = _git(root, 'merge-base', '--is-ancestor', ev['commit'],
                               git.canonical_commit, allowed=(0, 1, 128))
                incorporated = code == 0
            if not incorporated:
                remote_pending_changes.append(ev)
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
    return dict(config=config, git=git, members=members, events=events, warnings=warnings,
                remote_fetched=remote_fetched, remote_pending_changes=remote_pending_changes)


def _quoted(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


@dataclass(frozen=True)
class TestEvidence:
    event: dict

    @property
    def execution(self):
        execution = self.event.get("execution")
        return execution if isinstance(execution, dict) else {}

    @property
    def attribution(self):
        return self.execution.get("state_verified")

    def counts(self):
        execution = self.execution
        fields = ("passed", "failed", "skipped")
        counts = execution.get("counts")
        if "counts" not in execution and execution.get("counts_available") is True:
            # Older runner events had only the flag and the envelope counts.
            counts = {key: self.event[key] for key in fields}
            counts.update(status="observed", ran=execution.get("ran"))
        if isinstance(counts, dict):
            status = counts.get("status")
            if status == "unavailable" and execution.get("counts_available") is not True:
                return "unavailable", None
            if (status == "observed" and execution.get("counts_available") is not False
                    and all(type(counts.get(key)) is int and counts[key] == self.event[key]
                            for key in fields)
                    and type(counts.get("ran")) is int
                    and counts["ran"] == sum(counts[key] for key in fields)):
                return "observed", counts
        return "unknown", None

    def matches_scope(self, branch, *, canonical=False):
        scope = self.event["scope"]
        recorded_branch = self.execution.get("branch")
        if canonical:
            return recorded_branch == branch and scope in ("current_branch", "canonical", "main")
        if scope == "current_branch" or recorded_branch is not None:
            return branch is not None and recorded_branch == branch
        return True

    def supports_pass(self):
        _, counts = self.counts()
        execution = self.execution
        return (counts is not None and counts["passed"] > 0 and counts["failed"] == 0
                and all(execution.get(key) is not False for key in ("started", "completed", "successful"))
                and execution.get("returncode") in (None, 0)
                and bool(self.event["commands"]))

    def format(self):
        event, execution = self.event, self.execution
        result = event["automated_result"]
        status, counts = self.counts()
        count_detail = ("; ".join(f"{key}={counts[key]}" for key in
                                  ("ran", "passed", "failed", "skipped")) if counts else
                        "actual case totals not established")
        git_state = "unknown; commit attribution unconfirmed"
        if self.attribution is True:
            git_state = "verified by recorded pre/post snapshots"
        elif self.attribution is False:
            git_state = "verification failed; commit attribution unconfirmed"
        diagnostics = execution.get("diagnostics")
        diagnostic_status = "unavailable"
        if isinstance(diagnostics, dict):
            diagnostic_status = "recorded in event"
            if diagnostics.get("truncated") is True:
                diagnostic_status += " (truncated)"
        environment = execution.get("environment")
        environment_status = "recorded in event" if isinstance(environment, dict) and environment else "unavailable"
        review = ("not performed" if execution.get("semantic_review") == "not_performed"
                  else "not established by STATUS")
        observations = execution.get("human_observations")
        recorded = [
            item for item in observations
            if isinstance(item, dict) and all(isinstance(item.get(key), str) and item[key].strip()
                                             for key in ("check", "observed", "evidence"))
        ] if isinstance(observations, list) and self.attribution is True else []
        observed_checks = {item["check"] for item in recorded}
        pending = [check for check in event["human_checks_required"] if check not in observed_checks]
        lines = [
            f"Test {event['event_id']}: automated={result}; scope={_quoted(event['scope'])}; "
            f"commit={_quoted(event['commit'])}; branch={_quoted(execution.get('branch'))}",
            f"  Git state: {git_state}",
            f"  Counts: {status}; {count_detail}",
            f"  Environment: {environment_status}; diagnostics: {diagnostic_status}",
            f"  AI semantic review: {review}; full TEST completion: not established",
            "  Publication: unconfirmed (local event; remote content not checked)",
        ]
        if result == "fail" or (counts and counts["failed"]):
            failure = "observed by runner" if counts and counts["failed"] else "reported by event"
            lines.append(f"  Failure: {failure}; root cause undetermined by STATUS")
        elif event["failed"]:
            lines.append("  Failure count reported in event; observation and root cause unconfirmed")
        if result == "pass" and event["failed"]:
            lines.append("  Evidence conflict: pass with a nonzero reported failure count")
        elif result == "pass" and not self.supports_pass():
            lines.append("  Evidence limit: reported pass lacks consistent execution evidence")
        if not pending and not recorded:
            lines.append("  Human validation: assessment unknown")
        if pending:
            lines.append(f"  Human validation: pending evidence for {_quoted(pending)}")
        for item in recorded:
            lines.append(f"  Human validation: observation and evidence reference recorded "
                         f"(unverified by STATUS): {_quoted(item)}")
        return lines


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
    tests = [TestEvidence(event) for event in events if event["event_type"] == "test_result"]
    if tests:
        lines.append("TEST evidence (automated values are event declarations):")
    outcomes = {}
    for test in tests:
        lines.extend(test.format())
        event = test.event
        outcomes.setdefault((event["commit"], event["scope"]), []).append(event)
    for (commit, scope), records in outcomes.items():
        if len({event["automated_result"] for event in records}) > 1:
            declarations = ", ".join(f"{event['event_id']}={event['automated_result']}" for event in records)
            lines.append(f"Potentially conflicting TEST declarations for commit={_quoted(commit)} "
                         f"scope={_quoted(scope)}: {declarations}; no winner inferred")
    for label, commit, branch in (("HEAD", git.head, git.branch),
                                  ("canonical", git.canonical_commit, config["canonical_branch"])):
        attributed = [
            f"{test.event['event_id']}={test.event['automated_result']}" for test in tests
            if test.event["commit"] == commit and test.attribution is True
            and test.matches_scope(branch, canonical=label == "canonical")
            and (test.event["automated_result"] != "pass" or test.supports_pass())
        ]
        lines.append(f"Tests for exact {label} commit: " +
                     (", ".join(attributed) or "no attributable result; unknown"))
    blockers = [e["event_id"] for e in events if e["event_type"] == "blocker"]
    lines.append("Blocker records: " + (", ".join(blockers) or "not yet recorded"))
    lines += [
        "Blocker resolution: unknown (no resolution inferred from later events)",
        "Evidence limits: review accept is not integration; integration_result is a declaration.",
        "Test results apply only to their recorded commit/scope; related_events does not extend coverage.",
        "Event order does not establish latest progress; conflicting records are retained.",
    ]
    if report.get("remote_fetched"):
        lines.append("Remote metadata: fetched from origin (live snapshot at command time)")
        pending = report.get("remote_pending_changes") or []
        if pending:
            summary = ", ".join(
                f"{ev['change_id']} ({ev['person_id']} @ {ev['commit'][:12]})"
                for ev in pending
            )
            lines.append("Remote pending changes (not in canonical): " + summary)
        else:
            lines.append("Remote pending changes (not in canonical): (none)")
    lines.append("Recommended next action: " + recommended_next_action(report))
    lines.extend("WARNING: " + warning for warning in report["warnings"])
    return "\n".join(lines)


def recommended_next_action(report):
    git = report["git"]
    events = report["events"]
    if not git.clean:
        return "Inspect and protect uncommitted local changes before syncing or publishing."
    blockers = [e for e in events if e["event_type"] == "blocker"]
    if blockers:
        return f"Resolve or address recorded blocker(s): {', '.join(e['event_id'] for e in blockers)}."
    if git.behind and git.behind > 0:
        return "Run SYNC to incorporate remote updates safely."
    pending = report.get("remote_pending_changes") or []
    if pending:
        ids = ", ".join(ev["change_id"] for ev in pending)
        return f"Review or integrate pending remote change(s): {ids}."
    tests = [TestEvidence(e) for e in events if e["event_type"] == "test_result"]
    head_tested = any(
        t.event["commit"] == git.head and t.attribution is True
        and t.event["automated_result"] == "pass" and t.supports_pass()
        for t in tests
    )
    if not head_tested:
        return "Run TEST on the current HEAD to establish attributable test evidence."
    if git.branch and git.branch.startswith("people/"):
        return "Review changes and run PUB when ready, or SYNC to check teammate updates."
    return "Run SYNC to check remote state, or FINAL when canonical work is ready."
