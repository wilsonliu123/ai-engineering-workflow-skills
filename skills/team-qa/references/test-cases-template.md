# Test Cases Template

Use for `docs/qa/<feature-slug>/test-cases.md`.

```markdown
---
feature: <feature-slug>
artifact_status: complete
validity: current
owner_role: QA
reviewer_role: QA Lead
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
- **Observable metrics when applicable:**
- **Cleanup / reset:**
- **Notes:**
```

Do not add runtime result fields. Do not let AI choose the execution assignment.
