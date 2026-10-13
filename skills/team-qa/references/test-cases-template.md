# Test Cases Template

Use for `docs/qa/<feature-slug>/test-cases.md`.

```markdown
---
artifact_id: QATC-<feature-slug>
artifact_type: test-cases
feature: <feature-slug>
artifact_status: draft
review_status: not-started   # approved together with test-spec (one review session)
validity: current
owner_role: QA
reviewer_roles:
  - QA Lead
requirements: docs/requirements/<feature-slug>.md
test_spec: docs/qa/<feature-slug>/test-spec.md
source_commit: <commit-or-unknown>
---

# Test Cases — <Feature Title>

## Coverage index
| Test case | REQ | AC | Class | Subtype | Priority | Execution assignment |
|---|---|---|---|---|---|---|

## TC-001 — <Title>
- **REQ:** REQ-001
- **AC:** AC-001
- **Trace:** <optional non-AC source: REQ-012 S4/network-loss, backend.md §11, D-003>
- **Automated test name:** <exact name containing TC-001, set by QA; empty for manual>
- **Proposed layer:** <unit | api | e2e | manual — planning hint only>
- **Priority:** high
- **Class:** functional / non-functional
- **Subtype:** feature / api / integration / permission / boundary / regression / performance-baseline / load / stress / spike / soak / scalability / reliability / security / accessibility / compatibility / other
- **Execution assignment:** human-assignment-pending
- **Purpose:**
- **Preconditions:**
- **Test data / role:**
- **Steps / scenario:**
  1. ...
- **Expected result / threshold:**
- **Observable metrics:** <omit when none>
- **Cleanup / reset:** <omit when none>
- **Notes:** <omit when none>
```

Do not add runtime result fields. Do not let AI choose the execution assignment (`Proposed layer` is a hint; the matrix records the decision). Omit optional fields instead of writing "—". Every AC must appear in at least one TC (validator enforced).
