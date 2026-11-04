# Release Gate Verification & Story Closure Method

## 1. The 5 Deterministic Release Gates

`jira_sync.py close-story` enforces five mandatory checks before transitioning a Jira Story to `Done`:

| Gate | Required Condition in `.ai-workflow/active/<JIRA-KEY>.json` | Action if Unmet |
|---|---|---|
| **1. Spec Gate** | `spec_status == "approved"` | Route to `$team-spec` |
| **2. Sub-task Gate** | Non-empty `tasks` and every task has `status == "completed"` | Route to `$team-dev` (`plan` or `implement`) |
| **3. Review & Commit Alignment** | `review.status == "approved"` and `review.reviewed_commit == --commit` | Route to `$team-review` |
| **4. QA & Commit Alignment** | `qa.status == "passed"` and `qa.tested_commit == --commit` | Route to `$team-qa` (`verify`) |
| **5. Zero Open Bugs** | Every item in `bugs` has `status == "completed"` or `"closed"` | Route to `$team-dev` (`implement`) |

---

## 2. First-Principles Cleanup

Why does `close-story` delete `.ai-workflow/active/<JIRA-KEY>.json` upon completion?
- During active development, `.ai-workflow/active/<JIRA-KEY>.json` provides fast, offline-capable local context for AI coding and verification without querying Jira on every token.
- Once the feature is released and the Jira `User Story` is `Done`, Jira holds the complete permanent audit trail (Specification, Sub-tasks, Code Review summary, QA Verification table, Bug history, and Release Summary).
- Deleting `.ai-workflow/active/<JIRA-KEY>.json` guarantees zero dead process files accumulate in the workspace or repository.
