# Portable AI Agent Collaboration Entry Point (Protocol 0.1)

This repository implements the **Group Collaboration System** (`groupctl.py`, Protocol 0.1) according to `specs/00_group_collaboration_assignment.md` through `specs/07_testing_and_finalization.md`.

Any AI coding agent (Codex, Gemini, Claude, Antigravity, or other CLI-capable agents) should read:
1. [AGENTS.md](AGENTS.md) — Repository rules and full procedures for `SYNC`, `PUB`, `INT`, `REV`, `TEST`, `STATUS`, and `FINAL`.
2. [.agents/skills/group-collaboration/SKILL.md](.agents/skills/group-collaboration/SKILL.md) — Portable Agent Skill specification.
3. `.group/group.json` and `.group/assignment/assignment.md` — Active group ID, canonical branch, and assignment scope.

## Core Principles
- **Mechanical facts via `groupctl.py` / Git; engineering judgment via AI + human.**
- **Never rely on private chat history** as the protocol; use immutable `.group/events/*.json` and exact commit SHAs.
- **Git merge success != correctness:** always verify Git ancestry, conflict markers, automated tests (in isolated worktrees), and explicit semantic compatibility.
- **Conservative writes:** never force-push shared branches, discard uncommitted work, overwrite published events, default `semantic_review=pass`, or fabricate human `FINAL` confirmation.
