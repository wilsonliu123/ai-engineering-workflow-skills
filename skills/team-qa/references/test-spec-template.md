# Test Specification Template

Use for `docs/qa/<feature-slug>/test-spec.md`.

```markdown
---
artifact_id: TESTSPEC-<feature-slug>
artifact_type: test-spec
feature: <feature-slug>
artifact_status: draft
review_status: not-started
validity: current
risk_level: <low|medium|high|critical>
approval_reference: null
owner_role: QA
reviewer_role: QA Lead
requirements: docs/requirements/<feature-slug>.md
design: docs/design/<feature-slug>/sad.md
engineering_standards: docs/shared/engineering-standards.md
source_commit: <commit-or-unknown>
---

# Test Specification — <Feature Title>

## 1. Purpose and scope
## 2. Risk assessment
## 3. Test strategy
### 3.1 Functional coverage
### 3.2 Non-functional coverage
### 3.3 Performance taxonomy when applicable
Distinguish performance baseline, load, stress, spike, soak/endurance, and scalability/capacity. Do not invent thresholds.
## 4. Environment and test data
## 5. Acceptance coverage plan
| AC | Planned test cases | Test type | Priority | Notes |
|---|---|---|---|---|
## 6. Entry criteria
## 7. Completion criteria
## 8. Exclusions and constraints
## 9. Open questions
## 10. Human review notes
```

Do not choose manual/automated execution in this file.
