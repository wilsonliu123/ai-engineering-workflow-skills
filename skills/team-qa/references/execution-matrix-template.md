# Execution Matrix Template

Use for `docs/qa/<feature-slug>/execution-matrix.md`.

This file records human QA decisions. `decided_by` names who made them, so an agent standing in for human QA is distinguishable from an AI self-selection. AI asks one test case at a time and does not recommend or preselect an execution type.

```markdown
---
artifact_id: QAEXEC-<feature-slug>
artifact_type: qa-execution-matrix
feature: <feature-slug>
artifact_status: draft
review_status: not-started
validity: current
owner_role: QA
reviewer_role: QA Lead
decided_by: <who chose execution types: named human QA, or "agent acting as human QA (<role>)">
test_spec: docs/qa/<feature-slug>/test-spec.md
test_cases: docs/qa/<feature-slug>/test-cases.md
source_commit: <commit-or-unknown>
---

# QA Execution Matrix — <Feature>

| Test Case | Class / Subtype | Human decision | Tool / method selected by human | Owner role | Deferred reason/reference |
|---|---|---|---|---|---|

## Assignment completeness
- Assigned:
- Deferred:
- Pending human decision:

## Notes
```
