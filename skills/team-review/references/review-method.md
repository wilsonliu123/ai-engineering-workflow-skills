# Three-Axis Code Review & Handoff Method

## 1. Combined Handoff + Code Review Structure

Station 3a combines what was previously two separate documents (`dev-delivery.md` and `code-review.md`) into one concise, high-signal Jira Story comment and `.ai-workflow/active/<JIRA-KEY>.json` `review` record:

1. **Implementation & Test Evidence (Dev Handoff):**
   - Candidate Commit SHA (`candidate_commit`)
   - Completed Sub-tasks (`TASK-001..N`) and changed files
   - Executed test suites and results from `complete-task`
   - **QA External Tool / Environment Hints:** Explicitly flag which `AC-*` items are already covered by local `automated` tests vs. which require `external` tool runs (Playwright, Postman, CI/Staging, third-party sandbox) or `manual` human verification in Station 3b.
2. **Three-Axis Verification (Code Review):**
   - **AC-by-AC Traceability:** Every `AC-001..N` explicitly mapped to implementing code locations and test cases (any unimplemented AC is an automatic `major` or `blocker` finding).
   - **Design Conformance:** Boundaries, API contracts, UI states, and data safety.
   - **Standards & Security:** Compliance with `docs/shared/engineering-standards.md` (or `AGENTS.md` fallback).

---

## 2. Severity Scale ("抓大放小")

| Severity | Meaning | Blocks Handoff to QA? |
|---|---|---|
| **`blocker`** | Severe security vulnerability, data loss/corruption risk, crash, or core `AC-*` completely broken | **Yes** (`changes-requested`) |
| **`major`** | Partial `AC-*` omission, broken API/UI contract, missing test for critical path, or architecture boundary violation | **Yes** (`changes-requested`) |
| **`minor`** | Localized non-critical edge case or maintainability improvement that does not break any `AC-*` | **No** (include in summary) |
| **`note`** | Informational observation or QA focus hint | **No** (include in summary) |

### Example `--findings-json` Payload

```json
[
  {
    "id": "REV-001",
    "severity": "major",
    "axis": "ac-conformance",
    "location": "src/api/routes.py:42",
    "ac_id": "AC-003",
    "observation": "Export endpoint returns 500 instead of 403 when user lacks tenant export permission.",
    "status": "open"
  }
]
```
