# QA Execution Run Template

Use for `docs/qa/<feature-slug>/execution-run.md`.

```markdown
---
artifact_id: QARUN-<feature-slug>
artifact_type: qa-execution-run
feature: <feature-slug>
artifact_status: complete
validity: current
owner_role: QA
tested_commit: <commit-or-unknown>
tested_build: <build-or-unknown>
test_environment: <environment-or-unknown>
---

# QA Execution Run — <Feature>

## 1. Candidate identity
## 2. Test-plan and execution-matrix baselines
## 3. In-scope cases
## 4. Environment / dependency notes
## 5. Known blockers
## 6. Evidence collection handoff
Machine-generated reports -> `team-qa-evidence` tool-import.
Manual observations -> `team-qa-evidence` manual-interview.
```
