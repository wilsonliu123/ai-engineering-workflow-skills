---
name: team-dev
description: Use when an approved Jira Story specification needs to be decomposed into verifiable development Sub-tasks (plan mode), or when implementing the next pending task or open Bug fix, running verification tests, and checking off task completion in Jira and .ai-workflow/active/<JIRA-KEY>.json (implement mode).
---

# Team Dev (Station 2 — Task Decomposition & AI Coding Execution)

## Overview

Station 2 of the 4-station Jira-centered workflow. Operates in two explicit modes (`plan` and `implement`) and is decoupled from QA test planning so development is never blocked waiting for QA documents.

Read `references/common-workflow-governance.md` and `references/dev-method.md` before starting.

## Explicit Modes

| Mode | Trigger Condition | Action & CLI Command |
|---|---|---|
| **`plan`** | Story `spec_status == "approved"` and `tasks` is empty or needs updating | Decompose spec into vertical slices (`TASK-001..N`) and run `jira_sync.py create-subtasks` |
| **`implement`** | Open `Bug` tickets exist, or pending `TASK-001..N` exist | Pick next eligible item, load minimal context, write code & tests, verify, and run `jira_sync.py complete-task` |

If no mode is specified by the user, infer `plan` if `.ai-workflow/active/<JIRA-KEY>.json` has no `tasks`, and `implement` if tasks or open bugs already exist.

## Mode 1: `plan` — Task Decomposition & Jira Sub-tasks

1. **Read Upstream Context:**
   - Load `.ai-workflow/active/<JIRA-KEY>.json` (Scope, `AC-001..N`, Technical Design, conditional UI/API specs, Auto-Decided Defaults) and `docs/shared/engineering-standards.md` (fallback: `AGENTS.md`).
   - Inspect the repository's actual directory structure, entry points, and test runner commands.
2. **Decompose into Small Verifiable Slices (`TASK-001..N`):**
   - Prefer vertical slices with observable verification over vague layer batches.
   - Every task must map to at least one `AC-*` (or state `"Enabling work"`), list target files, declare `depends_on` task IDs (no cycles), and define an executable verification command.
3. **Sync Sub-tasks to Jira & Active State:**
   Run `scripts/jira_sync.py create-subtasks`:

   ```bash
   python3 <skill-dir>/scripts/jira_sync.py --repo-root . create-subtasks \
     --key "<JIRA-KEY>" \
     --task "TASK-001:<Title>:<AC-001,AC-002>:<Verification command>" \
     --task "TASK-002:<Title>:<AC-003>:<Verification command>"
   ```
   *(Or pass `--tasks-json '<JSON>'` when specifying `depends_on` and `files`.)*

## Mode 2: `implement` — Minimal-Context Coding & Task Check-Off

1. **Select Next Actionable Item:**
   - **Priority 1 (Open Bugs):** If `.ai-workflow/active/<JIRA-KEY>.json` has any `bugs` with `status == "open"`, fix the highest-priority open Bug first.
   - **Priority 2 (Pending Tasks):** Select the first `TASK-###` with `status == "pending"` whose `depends_on` tasks are all `completed`.
2. **Minimal Context Loading (Token & Focus Hygiene):**
   - Read **only**:
     1. `AGENTS.md` and `docs/shared/engineering-standards.md`
     2. The active item (`TASK-###` or `BUG-*`) and its linked `AC-*` / design section in `.ai-workflow/active/<JIRA-KEY>.json`
     3. The specific source and test files touched by this task.
   - Do not load unrelated feature histories or full repository dumps.
3. **Implement Code & Automated Tests:**
   - Follow repository conventions and `docs/shared/engineering-standards.md`.
   - Write or update unit/integration/E2E tests covering the task's `AC-*` (or reproducing the `BUG-*`). Include `TC-###` identifiers in test names when QA test cases are already planned.
   - Run the verification test suite and confirm it passes before checking off the task.
4. **Stop & Escalate Conditions:**
   - Stop and ask the user if implementation requires contradicting an approved `AC-*`, breaking a public API contract, or introducing an unapproved destructive migration.
5. **Check Off Task Completion (`complete-task`):**
   Immediately after tests pass and changes are committed (or staged with a commit SHA), run `scripts/jira_sync.py complete-task` so both `.ai-workflow/active/<JIRA-KEY>.json` and the Jira `Sub-task` / `Bug` are marked `completed` (`Done`):

   ```bash
   python3 <skill-dir>/scripts/jira_sync.py --repo-root . complete-task \
     --key "<JIRA-KEY>" \
     --task-id "<TASK-001 or BUG-KEY>" \
     --commit "<git-commit-sha>" \
     --files "<comma-separated-changed-files>" \
     --test-result "<executed test command and pass summary>"
   ```

6. **Advance or Hand Off to `team-review`:**
   - If `remaining_tasks` or `remaining_bugs` remain non-empty, continue to the next item (or report progress if the user asked for single-task execution).
   - When `ready_for_review: true` (0 remaining tasks and 0 open bugs), announce that Station 2 is complete and recommend `$team-review` (Station 3a).

## Red Flags — STOP

- Claiming a task is done without running `jira_sync.py complete-task` with a real `--commit`, `--files`, and `--test-result`.
- Creating `docs/dev-prep/` or `docs/dev-delivery/` markdown files in the repository.
- Blocking `team-dev` (`plan` or `implement`) because QA has not run `team-qa` (`plan` mode) yet.
