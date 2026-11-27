---
name: group-collaboration
description: Coordinate human students and local AI coding agents on a shared GitHub repository using groupctl.py (Protocol 0.1). Trigger when the user invokes SYNC, PUB, INT, REV, TEST, STATUS, or FINAL in a project containing .group/group.json.
---

# Group Collaboration Skill (Protocol 0.1)

Use `python -B groupctl.py ...` (or `python3 -B groupctl.py ...`) from the repository root.
Always read `.group/group.json` to discover `group_id`, `canonical_branch`, and `assignment_file`.

## Human Shorthand Commands

| Command | Mechanical `groupctl.py` Steps | AI / Human Judgment Required |
|---|---|---|
| `SYNC` | `sync --discover` then (if clean personal branch & safe) `sync --update` | Assess how new events and canonical changes affect current work and assumptions. Never auto-merge unrelated feature branches. |
| `PUB` | `pub --preflight` → `pub --work-commit` (or `--use-existing`) → `pub --push-work` → `pub --event-commit` → `pub --push-event` | Inspect full diff, exclude secrets/junk, document interface changes and known issues accurately. Publication is only complete after remote verification. |
| `INT <C-ID>` | `int --preflight` → `--ff-only` or (`--prepare-merge` → `--inspect-merge` → `--commit-merge`) → `int --verify` → `int --publish-result` | Inspect actual diff and conflict intents. Resolve conflicts deliberately or publish a `blocker`. Perform real semantic review; never default `semantic_review=pass`. |
| `REV <C-ID>` | `rev --inspect` → `rev --publish --verdict ... --inspected-commit <sha> --diff-inspected` | Inspect real diff, surrounding code, interfaces, regressions, and tests. **Never auto-merge** after a positive review. |
| `TEST [scope]` | `test --person-id <id> [--scope current\|main\|<C-ID>] [--isolated] [--publish ...]` | Discover applicable checks, investigate failures (product vs test vs env), design concrete human checks. Never mark missing/zero tests as `pass`. |
| `STATUS` | `status` (plus `sync --discover` if remote freshness is needed) | Report branch, HEAD, dirty state, canonical relationship, pending changes, attributable tests, blockers, and recommended next action. |
| `FINAL` | `final --prepare --person-id <id> --result-summary ...` → **show `.group/final/candidate.md` to human** → `final --publish --candidate-id <E-ID> --confirmation-file <path> --confirm-human` | Verify canonical commit, unincorporated changes, reviews, blockers, and final test evidence. **Never fabricate human confirmation.** |

## Non-Negotiable Safety Invariants
1. **Protect unfinished work:** never run `reset --hard`, `clean -fd`, or `stash` behind the user's back.
2. **Immutable events:** once committed and pushed, never edit `.group/events/*.json`; publish a new event with `related_events`.
3. **Exact commit binding:** all reviews, test results, integration results, and finalization records must bind to full verified commit SHAs.
4. **Honest evidence:** never claim a push succeeded before remote verification, never treat `not_available` tests as `pass`, and keep any automated demo confirmation explicitly marked `simulation`.
