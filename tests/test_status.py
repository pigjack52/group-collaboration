"""Group status CLI evidence checks; all fixture mutations use temporary Git repos."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import test_git as fixtures
from test_events import sample
from group_events import EventError, write_event
from group_status import collect_status, format_group_status

CLI = Path(__file__).resolve().parents[1] / "groupctl.py"


class StatusTests(unittest.TestCase):
    git = fixtures.GitTests.git
    commit = fixtures.GitTests.commit
    upstream = fixtures.GitTests.upstream

    def setUp(self):
        fixtures.GitTests.setUp(self)
        self.config = dict(protocol_version="0.1", group_id="G-01",
                           canonical_branch="main", assignment_file="assignment.md")
        self.group = self.root / ".group"
        self.group.mkdir()
        (self.group / "group.json").write_text(json.dumps(self.config), encoding="utf-8")
        self.members = self.group / "members"
        self.members.mkdir()
        self.member = self.members / "P01.json"
        self.member.write_text(json.dumps(dict(protocol_version="0.1", person_id="P01",
            display_name="Member", joined_at="2026-11-13T00:00:00Z")), encoding="utf-8")
        self.commit("group metadata")

    def cli(self, cwd=None):
        return subprocess.run([sys.executable, "-B", str(CLI), "status"],
                              cwd=cwd or self.root, capture_output=True,
                              text=True, encoding="utf-8")

    def event(self, kind, identifier, **fields):
        event = dict(sample(kind), event_id=identifier, **fields)
        write_event(self.root, event)
        return event

    def test_no_events_and_no_upstream(self):
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        for text in ("Events: 0", "change_published=0", "not yet recorded",
                     "ahead/behind unavailable", "Working tree: clean",
                     "Current operator identity: unknown", "no fetch performed"):
            self.assertIn(text, result.stdout)

    def test_all_evidence_is_scoped_and_linked(self):
        head = self.git("rev-parse", "HEAD")
        self.event("change_published", "E-change", change_id="C-1", commit=head)
        self.event("review", "E-review", change_id="C-1", verdict="accept")
        self.event("integration_result", "E-integrate", change_id="C-1",
                   target_branch="other", merge_commit="different", result="success")
        self.event("test_result", "E-test", commit=head, scope="unit", automated_result="pass")
        self.event("blocker", "E-block", blocking_change_id="C-1",
                   summary="Waiting for decision", requested_action="Ask owner")
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        for text in ("Events: 5", "review records: E-review",
                     "integration_result records: E-integrate",
                     "test_result records: E-test", "blocker records: E-block",
                     "Test E-test: automated=pass",
                     "Tests for exact HEAD commit: no attributable result; unknown",
                     "Actual integration into HEAD/canonical: unknown",
                     "Waiting for decision", "other", "different"):
            self.assertIn(text, result.stdout)

    def test_accept_without_integration_is_unknown(self):
        self.event("change_published", "E-change", change_id="C-1")
        self.event("review", "E-review", change_id="C-1", verdict="accept")
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("integration_result records: not yet recorded", result.stdout)
        self.assertIn("Actual integration into HEAD/canonical: unknown", result.stdout)

    def test_related_tests_do_not_cover_head(self):
        self.event("change_published", "E-change", change_id="C-1", commit="other-commit")
        self.event("finding", "E-context", related_events=["E-change"])
        self.event("test_result", "E-test", related_events=["E-context"],
                   commit="test-commit", automated_result="pass")
        text = self.cli().stdout
        self.assertIn("test_result records: E-test", text)
        self.assertIn("Tests for exact HEAD commit: no attributable result; unknown", text)
        self.assertIn("related_events does not extend coverage", text)

    def test_historical_test_event_and_unverified_attribution(self):
        head = self.git("rev-parse", "HEAD")
        historic = self.event("test_result", "E-historical", commit=head,
                              automated_result="pass", failed=1)
        path = self.group / "events/E-historical.json"
        original = path.read_bytes()
        self.event("test_result", "E-unverified", commit=head, automated_result="fail",
                   failed=1, execution={"state_verified": False})
        status = self.cli()
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("Tests for exact HEAD commit: no attributable result; unknown", status.stdout)
        self.assertIn("Test E-historical: automated=pass", status.stdout)
        self.assertIn("Evidence conflict: pass with a nonzero reported failure count", status.stdout)
        self.assertIn("Counts: unknown; actual case totals not established", status.stdout)
        self.assertIn("Test E-unverified: automated=fail", status.stdout)
        self.assertIn("Git state: verification failed; commit attribution unconfirmed", status.stdout)
        with self.assertRaisesRegex(EventError, "refusing to overwrite"):
            write_event(self.root, dict(historic, automated_result="fail"))
        self.assertEqual(path.read_bytes(), original)

    def test_invalid_event_reports_path_and_field(self):
        path = write_event(self.root, sample())
        data = json.loads(path.read_text(encoding="utf-8"))
        del data["summary"]
        path.write_text(json.dumps(data), encoding="utf-8")
        result = self.cli()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn(str(path), result.stderr)
        self.assertIn("summary", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_invalid_members(self):
        valid = json.loads(self.member.read_text(encoding="utf-8"))
        cases = [[], dict(valid, person_id="someone-else"),
                 dict(valid, joined_at="yesterday"), dict(valid, joined_at="2026-01-01"),
                 dict(valid, display_name=12), dict(valid, protocol_version="9")]
        for key in valid:
            case = dict(valid)
            del case[key]
            cases.append(case)
        for case in cases:
            with self.subTest(case=case):
                self.member.write_text(json.dumps(case), encoding="utf-8")
                result = self.cli()
                self.assertEqual(result.returncode, 1)
                self.assertIn(str(self.member), result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_member_bad_json_encoding_and_duplicates(self):
        for payload in (b"{", b"\xff", b'{"person_id":"a","person_id":"b"}'):
            self.member.write_bytes(payload)
            result = self.cli()
            self.assertEqual(result.returncode, 1)
            self.assertIn(str(self.member), result.stderr)

    def test_group_mismatch(self):
        self.event("finding", "E-other", group_id="different")
        result = self.cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn("E-other.json", result.stderr)
        self.assertIn("group_id", result.stderr)

    def test_missing_links_authors_and_change_are_warnings(self):
        self.event("review", "E-review", person_id="absent", change_id="C-absent",
                   related_events=["E-missing"])
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        for text in ("WARNING:", "E-review.json", "E-missing", "no member record",
                     "no change_published record"):
            self.assertIn(text, result.stdout)

    def test_dirty_and_upstream_counts(self):
        self.upstream()
        self.file.write_text("changed", encoding="utf-8")
        self.git("add", "tracked.txt")
        self.file.write_text("changed again", encoding="utf-8")
        (self.root / "new.txt").write_text("new", encoding="utf-8")
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        for text in ("Working tree: dirty", 'Staged: "tracked.txt"',
                     'Unstaged: "tracked.txt"', 'Untracked: "new.txt"',
                     "Ahead: 0; behind: 0 (local refs)"):
            self.assertIn(text, result.stdout)

    def test_read_only_from_subdirectory(self):
        before = {p.relative_to(self.root): p.read_bytes()
                  for p in self.root.rglob("*") if p.is_file()}
        result = self.cli(self.members)
        after = {p.relative_to(self.root): p.read_bytes()
                 for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, after)

    def test_non_git_error(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.cli(directory)
        self.assertEqual(result.returncode, 1)
        self.assertIn("not a git repository", result.stderr.lower())

    def test_missing_canonical_and_members_warn(self):
        self.git("branch", "-m", "feature")
        self.member.unlink()
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WARNING: Canonical branch main: unknown", result.stdout)
        self.assertIn("WARNING: Members: not yet recorded", result.stdout)

    def test_cycles_and_duplicate_changes_do_not_infer_progress(self):
        self.event("change_published", "E-one", change_id="C-1", related_events=["E-two"])
        self.event("change_published", "E-two", change_id="C-1", related_events=["E-one"])
        report = collect_status(self.root, self.config)
        text = format_group_status(report)
        self.assertIn("Duplicate change_id C-1", text)
        self.assertEqual(text.count("Actual integration into HEAD/canonical: unknown"), 2)

    def test_conflicting_tests_are_retained(self):
        head = self.git("rev-parse", "HEAD")
        self.event("test_result", "E-pass", commit=head, automated_result="pass")
        self.event("test_result", "E-fail", commit=head, automated_result="fail")
        text = self.cli().stdout
        self.assertIn("E-fail=fail, E-pass=pass", text)
        self.assertIn("Potentially conflicting TEST declarations", text)
        self.assertIn("no winner inferred", text)

    def test_human_observations_require_evidence_and_attribution(self):
        head = self.git("rev-parse", "HEAD")
        observations = [
            {"check": "Selection", "observed": "Z stayed selected", "evidence": "capture.png"},
            {"check": "Focus", "observed": "Visible", "evidence": ""},
        ]
        for identifier, verified in (("E-confirmed", True), ("E-unverified", False)):
            self.event("test_result", identifier, commit=head,
                       human_checks_required=["Selection", "Focus"],
                       execution={"state_verified": verified, "human_observations": observations})
        text = self.cli().stdout
        self.assertEqual(text.count("Human validation: observation and evidence reference recorded"), 1)
        self.assertIn('Human validation: pending evidence for ["Focus"]', text)
        self.assertIn('Human validation: pending evidence for ["Selection", "Focus"]', text)

    def test_invalid_config_and_directory(self):
        (self.group / "group.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self.cli().returncode, 1)
        (self.group / "group.json").write_text(json.dumps(self.config), encoding="utf-8")
        events = self.group / "events"
        events.write_text("not a directory", encoding="utf-8")
        result = self.cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn(str(events), result.stderr)


if __name__ == "__main__":
    unittest.main()
