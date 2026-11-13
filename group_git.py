"""Read-only Git snapshots using existing local refs; never contacts a remote."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess


class GitError(Exception):
    """Git state is unavailable or a Git command failed."""


def _git(root, *args, allowed=(0,)):
    env = os.environ.copy()
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_NO_LAZY_FETCH="1", LC_ALL="C")
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", *args], cwd=root, env=env,
            capture_output=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitError(f"Cannot run git {' '.join(args)}: {exc}") from exc
    if result.returncode not in allowed:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise GitError(f"git {' '.join(args)} failed ({result.returncode}): {detail}")
    return result.stdout.decode("utf-8", errors="surrogateescape").rstrip("\n"), result.returncode


@dataclass(frozen=True)
class GitStatus:
    root: Path
    branch: str | None
    head: str
    staged: tuple[str, ...]
    unstaged: tuple[str, ...]
    untracked: tuple[str, ...]
    origin_exists: bool
    upstream: str | None
    ahead: int | None
    behind: int | None
    canonical_ref: str | None
    canonical_commit: str | None

    @property
    def clean(self):
        return not (self.staged or self.unstaged or self.untracked)


def repository_root(path):
    root, _ = _git(path, "rev-parse", "--show-toplevel")
    return Path(root)


def inspect_status(path, canonical_branch="main"):
    """Inspect a working tree. Missing optional refs are represented by None."""
    root = repository_root(path)
    if not isinstance(canonical_branch, str) or not canonical_branch:
        raise GitError("canonical_branch must be a non-empty branch name")
    _git(root, "check-ref-format", "refs/heads/" + canonical_branch)
    branch, code = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD", allowed=(0, 1))
    branch = branch if code == 0 else None
    head, _ = _git(root, "rev-parse", "--verify", "HEAD^{commit}")
    raw, _ = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all",
                  "--ignore-submodules=none")
    staged, unstaged, untracked = [], [], []
    records = iter(raw.split("\0"))
    for record in records:
        if not record:
            continue
        xy, name = record[:2], record[3:]
        # With -z a rename/copy has destination then a separate source path.
        if "R" in xy or "C" in xy:
            source = next(records)
            name = source + " -> " + name
        if xy == "??":
            untracked.append(name)
        else:
            if xy[0] != " ":
                staged.append(name)
            if xy[1] != " ":
                unstaged.append(name)
    remotes, _ = _git(root, "remote")
    upstream = None
    ahead = behind = None
    if branch:
        upstream, _ = _git(root, "for-each-ref", "--format=%(upstream)",
                           "refs/heads/" + branch)
        upstream = upstream or None
        if upstream:
            counts, _ = _git(root, "rev-list", "--left-right", "--count",
                             "HEAD..." + upstream, "--")
            ahead, behind = map(int, counts.split())
    canonical_ref = canonical_commit = None
    for ref in ("refs/remotes/origin/" + canonical_branch, "refs/heads/" + canonical_branch):
        _, code = _git(root, "show-ref", "--verify", "--quiet", ref, allowed=(0, 1))
        if code == 0:
            canonical_commit, _ = _git(root, "rev-parse", "--verify", ref + "^{commit}")
            canonical_ref = ref
            break
    return GitStatus(root, branch, head, tuple(staged), tuple(unstaged),
                     tuple(untracked), "origin" in remotes.splitlines(), upstream,
                     ahead, behind, canonical_ref, canonical_commit)


def format_status(status):
    lines = [
        "Read-only Git snapshot (not the full STATUS procedure)",
        "Remote data: local refs only; no fetch performed; may be stale, not live GitHub state.",
        f"Branch: {status.branch or '(detached HEAD)'}",
        f"HEAD: {status.head}",
        f"Working tree: {'clean' if status.clean else 'dirty'}",
    ]
    for label, paths in (("Staged", status.staged), ("Unstaged", status.unstaged),
                         ("Untracked", status.untracked)):
        lines.append(f"{label}: " + (", ".join(json.dumps(p, ensure_ascii=True) for p in paths) or "(none)"))
    lines += [
        f"Origin: {'present' if status.origin_exists else 'absent'}",
        f"Upstream: {status.upstream or '(none); ahead/behind unavailable'}",
    ]
    if status.upstream:
        lines.append(f"Ahead: {status.ahead}; behind: {status.behind} (local refs)")
    lines.append(
        f"Canonical: {status.canonical_ref} {status.canonical_commit}"
        if status.canonical_commit else "Canonical: unavailable (no matching local or origin ref)"
    )
    return "\n".join(lines)