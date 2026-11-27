"""Real Git fixtures are confined to temporary directories; no network access."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from group_git import GitError, inspect_status

CLI = Path(__file__).resolve().parents[1] / "groupctl.py"


class GitTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.env = os.environ.copy()
        # Ignore user/global configuration and any inherited repository routing.
        for key in list(self.env):
            if key.startswith("GIT_"):
                del self.env[key]
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_TERMINAL_PROMPT="0")
        self.environment = patch.dict(os.environ, self.env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.test")
        self.git("config", "core.autocrlf", "false")
        self.file = self.root / "tracked.txt"
        self.file.write_text("base\n", encoding="utf-8")
        self.commit("base")

    def git(self, *args):
        result = subprocess.run(["git", *args], cwd=self.root, env=self.env,
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def commit(self, message):
        self.git("add", "--all")
        self.git("commit", "-m", message)
        return self.git("rev-parse", "HEAD")

    def cli(self, cwd=None):
        return subprocess.run([sys.executable, "-B", str(CLI), "git", "status"],
                              cwd=cwd or self.root, capture_output=True,
                              text=True, encoding="utf-8")

    def upstream(self):
        self.git("remote", "add", "origin", str(self.root / "offline.git"))
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.git("branch", "--set-upstream-to=origin/main")

    def test_clean_without_upstream(self):
        state = inspect_status(self.root)
        self.assertTrue(state.clean)
        self.assertEqual(state.branch, "main")
        self.assertEqual(state.head, self.git("rev-parse", "HEAD"))
        self.assertIsNone(state.upstream)
        self.assertIsNone(state.ahead)
        self.assertIsNone(state.behind)
        self.assertFalse(state.origin_exists)
        self.assertEqual(state.canonical_ref, "refs/heads/main")
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ahead/behind unavailable", result.stdout)

    def test_dirty_categories_and_unicode(self):
        self.file.write_text("staged\n", encoding="utf-8")
        self.git("add", "tracked.txt")
        self.file.write_text("unstaged\n", encoding="utf-8")
        name = "空 白.txt"
        (self.root / name).write_text("new", encoding="utf-8")
        state = inspect_status(self.root)
        self.assertFalse(state.clean)
        self.assertEqual(state.staged, ("tracked.txt",))
        self.assertEqual(state.unstaged, ("tracked.txt",))
        self.assertEqual(state.untracked, (name,))

    def test_staged_rename_and_unstaged_delete(self):
        self.git("mv", "tracked.txt", "renamed file.txt")
        self.assertEqual(inspect_status(self.root).staged,
                         ("tracked.txt -> renamed file.txt",))
        (self.root / "renamed file.txt").unlink()
        self.assertTrue(inspect_status(self.root).unstaged)

    def test_upstream_equal_and_read_only(self):
        self.upstream()
        before = {p.relative_to(self.root): p.read_bytes()
                  for p in self.root.rglob("*") if p.is_file()}
        state = inspect_status(self.root)
        result = self.cli()
        after = {p.relative_to(self.root): p.read_bytes()
                 for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((state.ahead, state.behind), (0, 0))
        self.assertTrue(state.origin_exists)
        self.assertEqual(state.upstream, "refs/remotes/origin/main")
        self.assertIn("may be stale", result.stdout)
        self.assertIn("not the full STATUS", result.stdout)

    def test_ahead(self):
        self.upstream()
        self.file.write_text("ahead", encoding="utf-8")
        self.commit("ahead")
        state = inspect_status(self.root)
        self.assertEqual((state.ahead, state.behind), (1, 0))
        self.assertNotEqual(state.head, state.canonical_commit)

    def test_behind(self):
        self.upstream()
        base = self.git("rev-parse", "HEAD")
        self.file.write_text("remote", encoding="utf-8")
        tip = self.commit("remote")
        self.git("update-ref", "refs/remotes/origin/main", tip)
        self.git("reset", "--hard", base)
        state = inspect_status(self.root)
        self.assertEqual((state.ahead, state.behind), (0, 1))

    def test_diverged(self):
        self.upstream()
        base = self.git("rev-parse", "HEAD")
        self.file.write_text("remote", encoding="utf-8")
        tip = self.commit("remote")
        self.git("update-ref", "refs/remotes/origin/main", tip)
        self.git("reset", "--hard", base)
        self.file.write_text("local", encoding="utf-8")
        self.commit("local")
        state = inspect_status(self.root)
        self.assertEqual((state.ahead, state.behind), (1, 1))

    def test_non_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(GitError, "failed"):
                inspect_status(directory)
            result = self.cli(directory)
            self.assertEqual(result.returncode, 1)
            self.assertIn("not a git repository", result.stderr.lower())
            self.assertNotIn("Traceback", result.stderr)

    def test_configured_canonical_from_subdirectory(self):
        self.git("branch", "accepted")
        group = self.root / ".group"
        group.mkdir()
        (group / "group.json").write_text(json.dumps(dict(
            protocol_version="0.1", group_id="G", canonical_branch="accepted",
            assignment_file="assignment.md")), encoding="utf-8")
        result = self.cli(group)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("refs/heads/accepted", result.stdout)

    def test_missing_canonical(self):
        self.git("branch", "-m", "feature")
        state = inspect_status(self.root)
        self.assertIsNone(state.canonical_commit)
        result = self.cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Canonical branch", result.stderr)
        self.assertIn("unavailable", result.stdout)

    def test_missing_upstream_ref_is_error(self):
        self.upstream()
        self.git("update-ref", "-d", "refs/remotes/origin/main")
        with self.assertRaisesRegex(GitError, "rev-list"):
            inspect_status(self.root)
        self.assertEqual(self.cli().returncode, 1)

    def test_detached_head(self):
        self.git("checkout", "--detach")
        state = inspect_status(self.root)
        self.assertIsNone(state.branch)
        self.assertIsNone(state.upstream)
        self.assertIn("detached HEAD", self.cli().stdout)

    def test_unborn_head(self):
        self.git("checkout", "--orphan", "empty")
        with self.assertRaisesRegex(GitError, "HEAD"):
            inspect_status(self.root)

    def test_git_execution_errors(self):
        for failure in (FileNotFoundError("git missing"),
                        subprocess.TimeoutExpired("git", 30)):
            with patch("group_git.subprocess.run", side_effect=failure):
                with self.assertRaisesRegex(GitError, "Cannot run git"):
                    inspect_status(self.root)

    def test_invalid_canonical_name(self):
        for name in (None, "", 12, "bad..name"):
            with self.subTest(name=name), self.assertRaises(GitError):
                inspect_status(self.root, name)


if __name__ == "__main__":
    unittest.main()