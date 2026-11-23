# Development Decomposition & Minimal-Context Execution Method

## 1. Decomposing a Spec into Verifiable Sub-tasks (`plan` mode)

A good `TASK-###` in `.ai-workflow/active/<JIRA-KEY>.json` (and its synced Jira Sub-task description) lets a fresh AI coding session or teammate answer 5 questions immediately:
1. **What am I changing and how?** (`title`, `description`, `implementation_steps`, and target `files`)
2. **Which ACs does this satisfy?** (`ac_coverage`: e.g., `["AC-001", "AC-002"]`, or `["Enabling work"]`)
3. **What must finish before this task?** (`depends_on`: e.g., `["TASK-001"]`)
4. **How do I prove it works?** (`verification`: exact test command and expected outcome)
5. **When must I stop and ask?** (any change that alters an approved `AC-*`, public schema, or auth boundary)

### Example `--tasks-json` Payload

Populate concrete `description`, `implementation_steps`, and `files` on every task so `jira_sync.py create-subtasks` renders detailed Implementation Steps and backticked Target Files in Jira Sub-task descriptions:

```json
[
  {
    "id": "TASK-001",
    "title": "Add CSV export service and unit tests for AC-001, AC-002",
    "description": "Implement RFC 4180 streaming CSV serializer with UTF-8 BOM support.",
    "implementation_steps": [
      "Create `CsvExporter.stream_rows()` with UTF-8 BOM header and cell escaping",
      "Add unit tests covering empty dataset, special characters, and 10k row boundary"
    ],
    "ac_coverage": ["AC-001", "AC-002"],
    "depends_on": [],
    "files": ["src/export/csv_exporter.py", "tests/test_csv_exporter.py"],
    "verification": "pytest tests/test_csv_exporter.py"
  },
  {
    "id": "TASK-002",
    "title": "Wire /api/v1/export endpoint with authz check for AC-003",
    "description": "Expose export route enforcing tenant permission guard before streaming.",
    "implementation_steps": [
      "Register `GET /api/v1/export` in router and call `CsvExporter.stream_rows()`",
      "Enforce tenant export permission check returning HTTP 403 when unauthorized"
    ],
    "ac_coverage": ["AC-003"],
    "depends_on": ["TASK-001"],
    "files": ["src/api/routes.py", "tests/test_export_api.py"],
    "verification": "pytest tests/test_export_api.py"
  }
]
```

---

## 2. Minimal-Context Implementation Loop (`implement` mode)

### 2.1 Execution Priority Order
When entering `implement` mode, select the target work item in this strict order:
1. **Priority 1 — Open Bugs (`BUG-*`):** Any defect in `story["bugs"]` with `status == "open"`.
2. **Priority 2 — Pending Tasks (`TASK-###`):** The next `TASK-###` in `story["tasks"]` with `status == "pending"` whose `depends_on` tasks are all `completed`.
   - **Dev Fast-Track (Small Stories Only — Complexity Guard):** If `story["tasks"]` is empty `[]` and `spec_status == "approved"`, Dev Fast-Track (`complete-task --key <KEY> --task-id TASK-001`) is **only allowed when `len(acceptance_criteria) <= 3` and `risk_level in ("low", "medium")`**. If the Story has `> 3` ACs or `high`/`critical` risk, `complete-task` rejects Fast-Track and requires `create-subtasks` (`plan` mode) first so complex features are never implemented as an unreviewed monolith.
3. **Priority 3 — Open Review Findings (`REV-###`):** When `review.status == "changes-requested"`, remediate every open `blocker`/`major` finding in `review.findings` (e.g., `REV-001`, `REV-002`) and check them off via `complete-task --key <KEY> --task-id REV-001,REV-002`.

### 2.2 Step-by-Step Loop
For each task, open Bug, or Review Finding:

1. **Read Only What Applies:**
   - `.ai-workflow/active/<JIRA-KEY>.json` (auto-hydrated from main Git worktree or remote Jira if missing locally)
   - `docs/shared/engineering-standards.md` (or `AGENTS.md` if absent)
   - Target files listed in `files`
2. **Compose Specialist Skills & Test-First Coding:**
   - **Load matching craft skills:** Pair `team-dev` with the developer's installed domain skills — for example TDD / evidence-first coding for core logic, UI/UX & frontend skills when `change_surface.has_ui` is true, and language/framework testing skills — rather than treating `team-dev` as the sole active skill.
   - **Reuse before writing:** Inspect sibling modules in the target directory for existing utilities, error envelopes, auth middleware, and shared types.
   - **Red-to-Green verification:** Write or update automated tests asserting both happy-path behavior and boundary/rejection cases (`400`/`401`/`403`/`409`, empty inputs, rollback/concurrency invariants) for every linked `AC-*`, `BUG-*`, or `REV-###`, see them fail before the fix/feature is wired, then implement the minimal production code to make them pass.
3. **Implement, Run Local Tests & Pass Static Checks (Station 2 Exit Gate):**
   Verify the technical checks in §2.3 below (codebase fit, test depth, API/data/UI completeness, and green test + linter/typecheck commands) so Station 3a (`$team-review`) never rejects the commit for preventable gaps.
4. **Human-in-the-Loop Commit Gate (`commit_policy`):**
   - Explicitly separate **"Implement & Run Local Tests"** from **"Git Commit & Task Check-Off (`complete-task`)"**.
   - Under default `"commit_policy": "confirm_before_commit"` (or when unset in `.ai-workflow/team-config.json`), **STOP** once local tests pass, report the changed files and test results to the user, and wait for user verification/confirmation before running `git commit` and `complete-task`.
   - Proceed directly to Step 5 without pausing ONLY if the user explicitly authorized committing in their prompt OR `.ai-workflow/team-config.json` explicitly sets `"commit_policy": "auto_on_test_pass"`.
   - Never fabricate a commit SHA or run `complete-task` before a real `git commit` exists.
5. **Project-Compliant Git Commit & Deterministic Task Check-Off (`complete-task`):**
   - Read `docs/shared/engineering-standards.md` §4 (fallback: project `AGENTS.md`) and write the commit message strictly following the project's declared template and language conventions.
   - Whenever the task has a `jira_subtask_key`, include **both `<STORY-KEY>` and `<SUBTASK-KEY>`** in the commit message (in the subject if compatible with the project template, or in the body/trailer otherwise) so Jira's Development Panel links commits to both parent Story and Sub-task.
   - After creating the real `git commit`, run `python3 .ai-workflow/bin/jira_sync.py complete-task --key <KEY> --task-id <ID[,ID2]> --test-result "<summary>"` (when fixing a `standalone_bug` or Story-linked `BUG-*`, always extract and pass `--root-cause "<why the bug occurred>"` and `--solution "<code fix strategy>"` so Jira comments render `**問題原因 (Root Cause)**` & `**解決辦法 (Solution)**` and active JSON persists them for downstream Review/QA/Release; omitting either on a bug emits a non-blocking `bug_fix_warnings` reminder).
   - `--commit` (from `git rev-parse --short HEAD`) and `--files` (from `git show` / `git diff`) are detected automatically when omitted, and `commit_message_warnings` surfaces any non-blocking regex (`commit_message_pattern`) or missing Jira-key reminders.
   - This updates `.ai-workflow/active/<JIRA-KEY>.json` (`status: "completed"`, `commit`, `changed_files`, `test_result`, plus `root_cause`/`solution` and `bug_fix_summary` on bugs), updates `candidate_commit`, transitions `review.status` to `"remediated"` once all open blocking `REV-*` findings are resolved, posts a completion comment to the Jira `Sub-task` (or `Bug`), and transitions it to `Done`.

### 2.3 Station 2 Exit Gate Checklist (Aligned with `$team-review` 3-Axis Audit)

Complete every applicable item before `git commit` and `jira_sync.py complete-task`:

| Dimension | Required Condition Before Commit & `complete-task` |
|---|---|
| **Codebase Fit** | Reuses existing project conventions, directory layout (`docs/shared/engineering-standards.md` / `AGENTS.md`), shared types, and config variables. |
| **Test Depth (`AC-*` / `TC-###`)** | Automated tests cover every linked `AC-*` happy path **plus** at least one negative/boundary path; `TC-###` IDs appear in test names when QA cases exist. |
| **API & Data (`has_api` / `has_data`)** | State-mutating routes enforce AuthN/AuthZ, validate input bounds/schemas, protect against injection/XSS, and preserve transactional consistency. |
| **Frontend UI (`has_ui`)** | Implements all states specified in `design.ui_spec` (`Normal`, `Loading`, `Empty`, `Invalid`/`Error`, and optimistic-update rollback where applicable). |
| **Static & Runtime Green** | Both the task's `verification` test command and repository static checks (for example `tsc --noEmit` or project linter) exit `0`. |
| **Commit Confirmation Gate (`commit_policy`)** | Under default `confirm_before_commit`, user has verified and confirmed committing (unless user explicitly authorized committing or `team-config.json` sets `auto_on_test_pass`). Real commit exists before `complete-task`. |
| **Commit Format & Dual Jira Keys** | Commit message strictly follows `docs/shared/engineering-standards.md` §4 (fallback: `AGENTS.md`) and includes both `<STORY-KEY>` and `<SUBTASK-KEY>` when a Jira Sub-task exists (subject if template-compatible, or body/trailer otherwise). |
| **Bug Root Cause & Solution (`BUG-*` / `standalone_bug`)** | When completing a Bug fix, pass `--root-cause` and `--solution` to `complete-task` so the root cause and fix strategy are persisted in active JSON and rendered in the Jira completion comment without altering the original reporter Description. |
