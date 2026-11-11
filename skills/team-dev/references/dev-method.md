# Development Decomposition & Minimal-Context Execution Method

## 1. Decomposing a Spec into Verifiable Sub-tasks (`plan` mode)

A good `TASK-###` in `.ai-workflow/active/<JIRA-KEY>.json` lets a fresh AI coding session answer 5 questions immediately:
1. **What am I changing?** (`title` and `files`)
2. **Which ACs does this satisfy?** (`ac_coverage`: e.g., `["AC-001", "AC-002"]`, or `["Enabling work"]`)
3. **What must finish before this task?** (`depends_on`: e.g., `["TASK-001"]`)
4. **How do I prove it works?** (`verification`: exact test command and expected outcome)
5. **When must I stop and ask?** (any change that alters an approved `AC-*`, public schema, or auth boundary)

### Example `--tasks-json` Payload

```json
[
  {
    "id": "TASK-001",
    "title": "Add CSV export service and unit tests for AC-001, AC-002",
    "ac_coverage": ["AC-001", "AC-002"],
    "depends_on": [],
    "files": ["src/export/csv_exporter.py", "tests/test_csv_exporter.py"],
    "verification": "pytest tests/test_csv_exporter.py"
  },
  {
    "id": "TASK-002",
    "title": "Wire /api/v1/export endpoint with authz check for AC-003",
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
3. **Pass the Station 2 Exit Gate (Pre-Review Shift-Left):**
   Verify the four checks in §2.3 below so Station 3a (`$team-review`) never rejects the commit for preventable gaps.
4. **Deterministic Task Check-Off (with Git Auto-Detection & Multi-ID Support):**
   - Commit the change and run `python3 .ai-workflow/bin/jira_sync.py complete-task --key <KEY> --task-id <ID[,ID2]> --test-result "<summary>"`.
   - `--commit` (from `git rev-parse --short HEAD`) and `--files` (from `git show` / `git diff`) are detected automatically when omitted.
   - This updates `.ai-workflow/active/<JIRA-KEY>.json` (`status: "completed"`, `commit`, `changed_files`, `test_result`), updates `candidate_commit`, transitions `review.status` to `"remediated"` once all open blocking `REV-*` findings are resolved, posts a completion comment to the Jira `Sub-task` (or `Bug`), and transitions it to `Done`.

### 2.3 Station 2 Exit Gate Checklist (Aligned with `$team-review` 3-Axis Audit)

Complete every applicable item before running `jira_sync.py complete-task`:

| Dimension | Required Condition Before `complete-task` |
|---|---|
| **Codebase Fit** | Reuses existing project conventions, directory layout (`docs/shared/engineering-standards.md` / `AGENTS.md`), shared types, and config variables. |
| **Test Depth (`AC-*` / `TC-###`)** | Automated tests cover every linked `AC-*` happy path **plus** at least one negative/boundary path; `TC-###` IDs appear in test names when QA cases exist. |
| **API & Data (`has_api` / `has_data`)** | State-mutating routes enforce AuthN/AuthZ, validate input bounds/schemas, protect against injection/XSS, and preserve transactional consistency. |
| **Frontend UI (`has_ui`)** | Implements all states specified in `design.ui_spec` (`Normal`, `Loading`, `Empty`, `Invalid`/`Error`, and optimistic-update rollback where applicable). |
| **Static & Runtime Green** | Both the task's `verification` test command and repository static checks (for example `tsc --noEmit` or project linter) exit `0`. |
