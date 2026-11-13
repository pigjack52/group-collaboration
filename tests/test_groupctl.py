"""Exercise the real CLI in isolated repositories using only the stdlib."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CLI = Path(__file__).resolve().parents[1] / "groupctl.py"
VALID = {
    "protocol_version": "0.1",
    "group_id": "group-01",
    "canonical_branch": "main",
    "assignment_file": ".group/assignment/assignment.md",
}


class CLITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / ".group" / "group.json"
        self.path.parent.mkdir()

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(CLI), *args],
            cwd=self.root, capture_output=True, text=True, encoding="utf-8",
        )

    def write_config(self, config, encoding="utf-8"):
        self.path.write_text(json.dumps(config), encoding=encoding)

    def assert_error(self, expected):
        result = self.run_cli("config", "show")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn(expected, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_success_preserves_optional_fields_and_file(self):
        config = dict(VALID, repository="https://example.test/repo", note="群組")
        self.write_config(config)
        original = self.path.read_bytes()
        result = self.run_cli("config", "show")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), config)
        self.assertEqual(self.path.read_bytes(), original)

    def test_utf8_bom(self):
        self.write_config(VALID, encoding="utf-8-sig")
        result = self.run_cli("config", "show")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), VALID)

    def test_missing_file(self):
        self.assert_error("Configuration file not found")

    def test_invalid_json(self):
        self.path.write_text('{"group_id":', encoding="utf-8")
        self.assert_error("Invalid JSON")
        self.assert_error("line 1")

    def test_each_required_field(self):
        for field in VALID:
            with self.subTest(field=field):
                config = dict(VALID)
                del config[field]
                self.write_config(config)
                self.assert_error(field)

    def test_all_missing_fields(self):
        self.write_config({})
        for field in VALID:
            self.assert_error(field)

    def test_non_object_json(self):
        for value in ([], None, "text", 17):
            with self.subTest(value=value):
                self.write_config(value)
                self.assert_error("must be a JSON object")

    def test_invalid_encoding(self):
        self.path.write_bytes(b"\xff\xfe\xff")
        self.assert_error("must be UTF-8")

    def test_unreadable_file_is_directory(self):
        self.path.mkdir()
        self.assert_error("Cannot read configuration")

    def test_help_without_config(self):
        for args in (("--help",), ("config", "--help"), ("config", "show", "--help")):
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("usage:", result.stdout)
        result = self.run_cli("--help")
        self.assertIn("config", result.stdout)
        self.assertNotIn("SYNC", result.stdout)

    def test_invalid_and_unimplemented_commands(self):
        for args in ((), ("config",), ("config", "unknown"), ("SYNC",),
                     ("config", "show", "--unknown")):
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("error:", result.stderr)


if __name__ == "__main__":
    unittest.main()