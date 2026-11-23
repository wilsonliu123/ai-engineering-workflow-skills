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
| **`plan`** | Story `spec_status == "approved"` and (`len(acceptance_criteria) > 3`, `risk_level in ("high", "critical")`, or multi-task decomposition needed) | Decompose spec into vertical slices (`TASK-001..N`) and run `jira_sync.py create-subtasks` |
| **`implement`** | Open `Bug` tickets (`BUG-*`), pending `TASK-001..N`, or open Review Findings (`REV-###`) exist | Pick next eligible item by priority, load minimal context, write code & tests, verify locally, then pause for user confirmation under default `commit_policy: "confirm_before_commit"` before `git commit` and `jira_sync.py complete-task` |
| **Dev Fast-Track** | **Small Story only** (`len(acceptance_criteria) <= 3` AND `risk_level in ("low", "medium")`) where `spec_status == "approved"` and `tasks` is empty | Skip `create-subtasks`; implement, verify locally, confirm commit per `commit_policy`, and run `complete-task --key <KEY> --task-id TASK-001` directly (auto-creates `TASK-001` covering all Story `AC-*` and marks it completed). Stories with `> 3` ACs or `high`/`critical` risk are blocked from Fast-Track and MUST run `plan` (`create-subtasks`) first! |

If no mode is specified by the user, infer `plan` if `.ai-workflow/active/<JIRA-KEY>.json` has no `tasks` (or use **Dev Fast-Track** if it is a small 1-task feature), and `implement` if tasks, open bugs, or review findings already exist. When running on a new machine or Git worktree where local `.ai-workflow/active/<JIRA-KEY>.json` is missing, `jira_sync.py` automatically hydrates it from the main worktree or remote Jira issue.

## Mode 1: `plan` — Task Decomposition & Jira Sub-tasks

1. **Read Upstream Context:**
   - Load `.ai-workflow/active/<JIRA-KEY>.json` (Scope, `AC-001..N`, Technical Design, conditional UI/API specs, Auto-Decided Defaults) and `docs/shared/engineering-standards.md` (fallback: `AGENTS.md`).
   - Inspect the repository's actual directory structure, entry points, and test runner commands.
2. **Decompose into Small Verifiable Slices (`TASK-001..N`):**
   - Prefer vertical slices with observable verification over vague layer batches.
   - Every task must map to at least one `AC-*` (or state `"Enabling work"`), list concrete target `files`, include actionable `description` and/or `implementation_steps`, declare `depends_on` task IDs (no cycles), and define an executable `verification` command.
3. **Sync Sub-tasks to Jira & Active State:**
   Run `jira_sync.py create-subtasks` automatically in the background (prefer `.ai-workflow/bin/jira_sync.py` or fall back to `<skill-dir>/scripts/jira_sync.py`). Prefer `--tasks-json` with concrete `description`, `implementation_steps`, and `files` so every Jira Sub-task renders full Implementation Steps, Target Files, AC coverage, and Verification Command (not just a bare skeleton):

   ```bash
   python3 .ai-workflow/bin/jira_sync.py --repo-root . create-subtasks \
     --key "<JIRA-KEY>" \
     --tasks-json '<JSON array with id, title, description, implementation_steps, ac_coverage, depends_on, files, verification>' \
     [--assignee "<default-dev-email-or-specialty>"] \
     [--task-assignee "TASK-001=<dev-email-or-specialty>"]
   ```
   *(For quick single-line tasks you may still pass `--task "TASK-001:<Title>:<AC-001,AC-002>:<Verification command>"`, where colons inside `<Verification command>` such as `pytest tests/a.py::test_b` are preserved automatically. Every created Jira Sub-task is **automatically assigned** via `PUT /rest/api/3/issue/{subKey}/assignee` to its owning developer — using explicit `--task-assignee`/`"assignee"`, automatic `specialty` matching across `roles.developer.members` such as `backend` vs `frontend`, or the sole configured `developer` — so Sub-tasks never appear Unassigned in Jira!)*

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
3. **Compose Specialist Craft Skills & Pass the Exit Gate:**
   - **Bring-Your-Own-Skill Hook:** `team-dev` orchestrates task selection, spec alignment, and Jira check-off — it does not replace domain coding skills. Before writing code, actively load and apply any matching specialist skill available in the developer's environment (for example TDD / evidence-first coding, frontend UI/UX design, React/Go/Python framework guidance, or testing patterns) or explicitly named by the developer, alongside `docs/shared/engineering-standards.md` (fallback: `AGENTS.md`).
   - **Pre-Checkoff Exit Gate (Aligned with `$team-review`):** Complete coding only when all five conditions hold (see `references/dev-method.md` §2.3 for surface checks):
     1. **Module reuse:** Existing project helpers, types, and config constants are reused without duplicate implementations or hardcoded environment values.
     2. **Red-to-Green boundary tests:** Automated tests verify both the happy path and at least one boundary/error case for every linked `AC-*` (including `TC-###` IDs in test names when planned).
     3. **Change-surface completeness:** `has_api`/`has_data` changes enforce AuthN/AuthZ, input validation, and transactional integrity; `has_ui` changes implement `Normal`, `Loading`, `Empty`, and `Error` states from `design.ui_spec`.
     4. **Clean verification & static checks:** Both the verification test command and the repository's typecheck/linter command exit `0`.
     5. **Commit Format Compliance & Dual Jira Key Traceability:** Before `git commit`, read `docs/shared/engineering-standards.md` §4 (fallback: project `AGENTS.md`) and strictly follow the project's declared Commit Message template and language conventions — never substitute an unapproved format. Whenever the task has a `jira_subtask_key`, include **both `<STORY-KEY>` and `<SUBTASK-KEY>`** in the commit message (in the subject if compatible with the project template, or in the commit body/trailer otherwise) so Jira's Development Panel links commits to both parent Story and Sub-task.
4. **Stop & Escalate Conditions (Spec Gap Escalation Loop):**
   - Stop and ask the user if implementation uncovers a missing edge-case `AC-*`, requires contradicting an approved `AC-*`, breaks a public API contract, or introduces an unapproved destructive migration.
   - When a genuine spec gap or new AC is discovered during coding, update the Story specification first via `jira_sync.py sync-story` (`$team-spec`) so the new `AC-*` is formally tracked before completing the task.
5. **Human-in-the-Loop Commit Gate (`commit_policy`) — Separate Local Testing from Git Commit:**
   - **Default (`"commit_policy": "confirm_before_commit"`, or unset in `.ai-workflow/team-config.json`):** After implementing the task/bugfix and confirming local automated tests & static checks pass, **STOP before running `git commit` and `complete-task`**. Report the modified files, concise change summary, and exact local test results to the user, and wait for user manual verification / explicit confirmation to commit.
   - **Opt-In Direct Commit:** Proceed directly to Step 6 (`git commit` + `complete-task`) without pausing **only** if:
     1. The user explicitly authorized committing in their current prompt, **OR**
     2. `.ai-workflow/team-config.json` explicitly sets `"commit_policy": "auto_on_test_pass"`.
   - **Preserve `candidate_commit` Integrity:** Never fabricate a commit SHA or invoke `complete-task` before a real `git commit` exists.
6. **Git Commit (Project Standard + Dual Jira Keys) & Task Check-Off (`complete-task`):**
   Only after Step 5 authorizes committing:
   1. Create the real `git commit` following `docs/shared/engineering-standards.md` §4 (fallback: `AGENTS.md`) and including **both `<STORY-KEY>` and `<SUBTASK-KEY>`** when `jira_subtask_key` exists.
   2. Immediately run `jira_sync.py complete-task` so both `.ai-workflow/active/<JIRA-KEY>.json` and Jira are updated (`--commit` and `--files` are auto-detected from Git if omitted, `--task-id` supports comma-separated IDs like `TASK-001,TASK-002` or `REV-001,REV-002`, and `commit_message_warnings` reports any non-blocking format or Jira-key reminders):

   ```bash
   python3 .ai-workflow/bin/jira_sync.py --repo-root . complete-task \
     --key "<JIRA-KEY-OR-STANDALONE-BUG-KEY>" \
     --task-id "<TASK-001 | BUG-KEY | REV-001,REV-002>" \
     --test-result "<executed test command and pass summary>" \
     [--root-cause "<bug root cause analysis — REQUIRED for standalone_bug / BUG-* fixes>"] \
     [--solution "<bug fix strategy / solution — REQUIRED for standalone_bug / BUG-* fixes>"] \
     [--commit "<git-commit-sha>"] \
     [--files "<comma-separated-changed-files>"]
   ```
   - **Bug Fix Root Cause & Solution Backfill (`standalone_bug` / `BUG-*`):** Whenever completing a `standalone_bug` or a Story-linked `BUG-*` fix, extract and pass concrete `--root-cause` (why the defect happened) and `--solution` (what code strategy fixed it) so `jira_sync.py` persists them in `.ai-workflow/active/<JIRA-KEY>.json` (`bug_report` / `bug_fix_summary` / bug entry) and renders `**問題原因 (Root Cause)**` and `**解決辦法 (Solution)**` into the structured Jira completion comment for Station 3a (`team-review`), Station 3b (`team-qa`), and Station 4 (`team-release`). Omitting either flag on a bug fix emits a non-blocking reminder in `bug_fix_warnings`. Never rewrite the original Jira Bug Description placeholders — keep reporter descriptions intact and record Root Cause & Solution via structured comments + local JSON.
   *(For a standalone production Bug `issue_kind == "standalone_bug"`, `--task-id` defaults to `TASK-001` and also accepts `<BUG-KEY>` itself; `complete-task` posts the fix, root cause, solution & regression-test comment on `<BUG-KEY>` without prematurely closing `<BUG-KEY>` before Code Review, QA, and Release.)*

7. **Station Completion & Human-in-the-Loop Boundary (`Stop & Yield on Fix`):**
   - If `remaining_tasks` or `remaining_bugs` remain non-empty for the currently authorized dev work, continue to the next item (respecting `commit_policy` on each task).
   - **`Stop & Yield on Fix` (Hard Boundary):** When fixing a bug, defect, or tweak reported by the user during manual verification or feedback, after updating code and running local tests you **MUST STOP** and report the fix + test results for user verification (and wait for confirmation before committing under `confirm_before_commit`). Never autonomously invoke `handoff`, `record-qa`, or `close-story` in that turn.
   - When `ready_for_review: true` (0 remaining tasks, 0 open bugs, and 0 open blocking review findings), announce that Station 2 is complete (`can_auto_chain_next_station` is ALWAYS `false`):
     - **Same person (`developer` == `reviewer`, `same_person: true`, `next_station_prompt_allowed: true`):** Report the Station 2 summary concisely, ask one short question whether the user wants to proceed to `$team-review` (Station 3a), then **STOP** and wait for explicit user authorization. Never auto-chain into `$team-review`.
     - **Different person (`developer` != `reviewer`, `same_person: false`, `cross_person_clean_stop: true`):** Enforce **Clean Stop** — report that Station 2 is complete and ready for `next_station_owner`, and **strictly forbid** prompting or suggesting that the developer run `/team-review` (or `/team-qa`).

## Red Flags — STOP

- Auto-committing code or running `complete-task` immediately after local tests pass when `commit_policy` is `"confirm_before_commit"` (default) and the user has not explicitly authorized committing.
- Claiming a task is done or running `complete-task` with a fabricated/uncommitted SHA before a real `git commit` exists.
- Ignoring `docs/shared/engineering-standards.md` §4 (fallback: `AGENTS.md`) Commit conventions or omitting `<SUBTASK-KEY>` when a Jira Sub-task exists.
- Creating `docs/dev-prep/` or `docs/dev-delivery/` markdown files in the repository.
- Blocking `team-dev` (`plan` or `implement`) because QA has not run `team-qa` (`plan` mode) yet.
- **`Stop & Yield on Fix` violation:** After fixing a user-reported bug or tweak, autonomously running `handoff`, `record-qa`, or `close-story` instead of stopping to let the user verify the fix.
- Auto-chaining into `$team-review` without explicit user authorization, or prompting `$team-review` when `same_person == false`.
