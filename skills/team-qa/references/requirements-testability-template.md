# Requirements Testability Review Template

Use for `docs/qa/<feature-slug>/requirements-testability.md`.

```markdown
---
artifact_id: QAREQ-<feature-slug>
artifact_type: requirements-testability
feature: <feature-slug>
artifact_status: draft
review_status: not-started
validity: current
risk_level: <low|medium|high|critical>
approval_reference: null
owner_role: QA
reviewer_roles:   # requirement owner consumes this; no separate approval needed
  - Product Owner
review_round: 1
requirements: docs/requirements/<feature-slug>.md
upstream: <written by check_upstream_gate.py --record>
source_commit: <commit-or-unknown>
---

# Requirements Testability Review — <Feature>

## 1. Scope reviewed
## 2. Testability summary
## 2a. Situation coverage check
Five situations (normal, invalid input, no permission, empty, network loss) per screen/endpoint present in the requirement? List gaps as blocking.
## 3. Blocking gaps
| ID | REQ / AC | Testability issue | Why it blocks objective verification | Route back to |
|---|---|---|---|---|
## 3a. Closed gaps (previous rounds)
| ID | Closed in round | How |
|---|---|---|
## 4. Non-blocking clarifications
## 5. Non-functional criteria needing measurable targets
## 6. Conclusion for requirements review
Conclusion: pass | pass-with-conditions | block
<!-- block when any blocking gap is open; conditions listed explicitly. -->
```
