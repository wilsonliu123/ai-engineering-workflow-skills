# Dev Delivery Template
```markdown
---
artifact_id: DEV-<feature-slug>
artifact_type: dev-handoff
feature: <feature-slug>
artifact_status: draft
review_status: not-started
validity: current
risk_level: <low|medium|high|critical>
owner_role: Developer
reviewer_roles: [Tech Lead]
source_commit: <implementation-head-or-unknown>
requirements_baseline: <path@commit>
design_baseline: <path@commit>
dev_prep_baseline: <path@commit>
approval_reference: null
---
# <Feature> — Development Delivery
## 1. Implemented scope
## 2. Implementation identity and changed areas
## 3. Traceability to TASK / REQ / AC
## 4. Design and standards conformance / deviations
## 5. Build, test, lint, typecheck and other executed evidence
## 6. Configuration, dependency, schema and migration changes
## 7. Documentation updates
## 8. Known limitations, risks and unfinished work
## 9. Review and QA focus
## 10. Handoff summary
```
