---
name: team-dev
description: Use when an approved Jira Story specification needs to be decomposed into verifiable development Sub-tasks (plan mode), or when implementing the next pending task or open Bug fix, running verification tests, and checking off task completion in Jira and .ai-workflow/active/<JIRA-KEY>.json (implement mode).
---

# Team Dev (Station 2 — Task Decomposition & AI Coding Execution)

## Overview

Station 2 of the 4-station Jira-centered workflow. Operates in two explicit modes (`plan` and `implement`) and is decoupled from QA test planning so development is never blocked waiting for QA documents.

Read `references/common-workflow-governance.md` and `references/dev-method.md` before starting.

## Explicit Modes & Dev Fast-Track

| Mode | Trigger Condition | Action & CLI Command |
|---|---|---|
| **`plan`** | Story `spec_status == "approved"` and `tasks` is empty or needs multi-task decomposition | Decompose spec into vertical slices (`TASK-001..N`) and run `jira_sync.py create-subtasks` |
| **`implement`** | Open `Bug` tickets (`BUG-*`), pending `TASK-001..N`, or open Review Findings (`REV-###`) exist | Pick next eligible item by priority, load minimal context, write code & tests, verify, and run `jira_sync.py complete-task` |
| **Dev Fast-Track** | Small / single-task Story where `spec_status == "approved"` and `tasks` is empty | Skip `create-subtasks`; implement, test, commit, and run `complete-task --key <KEY> --task-id TASK-001` directly (auto-creates `TASK-001` covering all Story `AC-*` and marks it completed) |

If no mode is specified by the user, infer `plan` if `.ai-workflow/active/<JIRA-KEY>.json` has no `tasks` (or use **Dev Fast-Track** if it is a small 1-task feature), and `implement` if tasks, open bugs, or review findings already exist. When running on a new machine or Git worktree where local `.ai-workflow/active/<JIRA-KEY>.json` is missing, `jira_sync.py` automatically hydrates it from the main worktree or remote Jira issue.

## Mode 1: `plan` — Task Decomposition & Jira Sub-tasks

1. **Read Upstream Context:**
   - Load `.ai-workflow/active/<JIRA-KEY>.json` (Scope, `AC-001..N`, Technical Design, conditional UI/API specs, Auto-Decided Defaults) and `docs/shared/engineering-standards.md` (fallback: `AGENTS.md`).
   - Inspect the repository's actual directory structure, entry points, and test runner commands.
2. **Decompose into Small Verifiable Slices (`TASK-001..N`):**
   - Prefer vertical slices with observable verification over vague layer batches.
   - Every task must map to at least one `AC-*` (or state `"Enabling work"`), list target files, declare `depends_on` task IDs (no cycles), and define an executable verification command.
3. **Sync Sub-tasks to Jira & Active State:**
   Run `jira_sync.py create-subtasks` automatically in the background (prefer `.ai-workflow/bin/jira_sync.py` or fall back to `<skill-dir>/scripts/jira_sync.py`):

   ```bash
   python3 .ai-workflow/bin/jira_sync.py --repo-root . create-subtasks \
     --key "<JIRA-KEY>" \
     --task "TASK-001:<Title>:<AC-001,AC-002>:<Verification command>" \
     --task "TASK-002:<Title>:<AC-003>:<Verification command>"
   ```
   *(Colons inside `<Verification command>` such as `pytest tests/a.py::test_b` are preserved automatically. Or pass `--tasks-json '<JSON>'` when specifying `depends_on` and `files`.)*

## Mode 2: `implement` — Minimal-Context Coding & Task Check-Off

1. **Select Next Actionable Item (Strict 3-Tier Priority):**
   - **Priority 1 (Open Bugs `BUG-*`):** If `.ai-workflow/active/<JIRA-KEY>.json` has any `bugs` with `status == "open"`, fix the highest-priority open Bug first.
   - **Priority 2 (Pending Tasks `TASK-###`):** Select the first `TASK-###` with `status == "pending"` whose `depends_on` tasks are all `completed` (or use **Dev Fast-Track** `TASK-001` if `tasks` is empty on a small story).
   - **Priority 3 (Open Review Findings `REV-###`):** When `review.status == "changes-requested"`, remediate all open `blocker`/`major` findings in `review.findings` (such as `REV-001`).
2. **Minimal Context Loading (Token & Focus Hygiene):**
   - Read **only**:
     1. `AGENTS.md` and `docs/shared/engineering-standards.md`
     2. The active item (`BUG-*`, `TASK-###`, or `REV-###`) and its linked `AC-*` / design section in `.ai-workflow/active/<JIRA-KEY>.json`
     3. The specific source and test files touched by this task.
   - Do not load unrelated feature histories or full repository dumps.
3. **Implement Code & Automated Tests:**
   - Follow repository conventions and `docs/shared/engineering-standards.md`.
   - Write or update unit/integration/E2E tests covering the task's `AC-*` (or reproducing the `BUG-*` / `REV-###`). Include `TC-###` identifiers in test names when QA test cases are already planned.
   - Run the verification test suite and confirm it passes before checking off the task.
4. **Stop & Escalate Conditions:**
   - Stop and ask the user if implementation requires contradicting an approved `AC-*`, breaking a public API contract, or introducing an unapproved destructive migration.
5. **Check Off Task Completion (`complete-task` with Git Auto-Detection & Multi-ID Support):**
   Immediately after tests pass and changes are committed, run `jira_sync.py complete-task` so both `.ai-workflow/active/<JIRA-KEY>.json` and Jira are updated (`--commit` and `--files` are auto-detected from Git if omitted, and `--task-id` supports comma-separated IDs like `TASK-001,TASK-002` or `REV-001,REV-002`):

   ```bash
   python3 .ai-workflow/bin/jira_sync.py --repo-root . complete-task \
     --key "<JIRA-KEY>" \
     --task-id "<TASK-001 | BUG-KEY | REV-001,REV-002>" \
     --test-result "<executed test command and pass summary>" \
     [--commit "<git-commit-sha>"] \
     [--files "<comma-separated-changed-files>"]
   ```

6. **Advance or Auto-Chain to `team-review`:**
   - If `remaining_tasks` or `remaining_bugs` remain non-empty, continue to the next item.
   - When `ready_for_review: true` (0 remaining tasks, 0 open bugs, and 0 open blocking review findings), announce that Station 2 is complete. If `developer` and `reviewer` map to the same person (`same_person: true`), proactively auto-chain into `$team-review` (Station 3a) or ask in one line whether to proceed immediately.

## Red Flags — STOP

- Claiming a task is done without running `jira_sync.py complete-task` with a real `--commit`, `--files`, and `--test-result`.
- Creating `docs/dev-prep/` or `docs/dev-delivery/` markdown files in the repository.
- Blocking `team-dev` (`plan` or `implement`) because QA has not run `team-qa` (`plan` mode) yet.
