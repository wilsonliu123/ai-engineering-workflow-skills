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

For each task or open Bug:

1. **Read Only What Applies:**
   - `.ai-workflow/active/<JIRA-KEY>.json`
   - `docs/shared/engineering-standards.md` (or `AGENTS.md` if absent)
   - Target files listed in `files`
2. **Test-First / Evidence-First Coding:**
   - Write or update automated tests that directly assert the behavior required by the linked `AC-*` or reproduce the `BUG-*`.
   - Implement the minimal, clean production code that satisfies the tests and adheres to `docs/shared/engineering-standards.md`.
3. **Deterministic Task Check-Off:**
   - Commit the change (or obtain the current HEAD commit SHA) and run `jira_sync.py complete-task`.
   - This updates `.ai-workflow/active/<JIRA-KEY>.json` (`status: "completed"`, `commit`, `changed_files`, `test_result`), updates `candidate_commit`, posts a completion comment to the Jira `Sub-task` (or `Bug`), and transitions it to `Done`.
