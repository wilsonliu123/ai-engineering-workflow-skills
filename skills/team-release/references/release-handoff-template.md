# Release Handoff Template

Use this structure as the default. Remove clearly inapplicable subsections only when the reason is obvious or recorded.

```markdown
---
artifact_id: RELEASE-<feature-slug>
artifact_type: release-handoff
feature: <feature-slug>
artifact_status: draft
review_status: not-started
validity: current
risk_level: <low|medium|high|critical>
approval_reference: null
release_owner_role: Release Manager
business_acceptance_role: Product Owner
technical_approver_role: Tech Lead
qa_signoff_role: QA Lead
source_commit: <commit-sha-or-unknown>
engineering_standards: docs/shared/engineering-standards.md
release_candidate: <build/tag/artifact-id-or-unknown>
version_alignment: <aligned|changed-after-review|changed-after-qa|partially-aligned|unknown>
---

# Release Handoff — <Feature Name>

## 1. Delivery intent and scope

### 1.1 Handoff type
- Internal handoff / Release candidate / Customer delivery / Production deployment handoff / Post-deployment record

### 1.2 In-scope requirements
- `REQ-...`

### 1.3 Out of scope / deferred
- Item, rationale, and approved decision reference if any.

## 2. Release candidate identity

| Component | Candidate identity | Reviewed identity | QA-tested identity | Alignment |
| --- | --- | --- | --- | --- |
| Source / PR | ... | ... | ... | ... |
| Frontend | ... | ... | ... | ... |
| Backend | ... | ... | ... | ... |
| DB migration | ... | ... | ... | ... |
| Config / manifest | ... | ... | ... | ... |

### 2.1 Alignment conclusion
- `aligned` / `changed-after-review` / `changed-after-qa` / `partially-aligned` / `unknown`

### 2.2 Required revalidation actions
- None / explicit actions.

## 3. Requirement and acceptance disposition

| REQ / AC | Disposition | Evidence / source | Notes |
| --- | --- | --- | --- |
| AC-... | verified / implemented-unverified / deferred / blocked / unknown | ... | ... |

## 4. Review evidence

- Review document: `docs/review/.../code-review.md`
- Reviewed version: ...
- Open blocker/major findings (severity scale from common-artifact-governance.md): ...
- Review limitations / uncovered scope: ...

## 5. QA evidence

- QA report: `docs/qa/.../qa-report.md`
- Tested version/environment: ...
- Result summary: pass / fail / blocked / not-executed counts
- Unresolved defects: ...
- Coverage gaps / mocked dependencies / limitations: ...

## 6. Delivery artifacts and documentation

| Artifact / document | Location / identity | Status | Notes |
| --- | --- | --- | --- |
| Build/package | ... | ready / missing / N/A | ... |
| User/admin guide | ... | current / update-needed / N/A | ... |
| Developer/integration guide | ... | current / update-needed / N/A | ... |
| Deployment/runbook | ... | current / update-needed / N/A | ... |
| Release notes | ... | current / update-needed / N/A | ... |

## 7. Deployment, migration, and rollback readiness

### 7.1 Deployment prerequisites
- ...

### 7.2 Configuration / secrets / environment changes
- Reference approved secure location; do not include secrets.

### 7.3 Data migration
- Migration ID / order / compatibility / validation.

### 7.4 Rollback or forward-fix strategy
- What is planned; distinguish tested vs untested recovery.

### 7.5 Health checks / observability
- Metrics, logs, alerts, smoke checks, and owner role.

## 8. Known limitations, standards exceptions, and residual risk

| ID | Type | Description | Impact | Owner role | Decision / follow-up |
| --- | --- | --- | --- | --- | --- |
| REL-RISK-001 | defect / unverified / operational / dependency / documentation | ... | ... | ... | ... |

## 9. Unresolved blockers and decision points

- Blocker or decision needed
- Responsible role
- Required evidence/action

## 10. Human review / acceptance records

| Decision | Required role | Actual record / reference | Status |
| --- | --- | --- | --- |
| Business acceptance | Product Owner | external reference or pending | pending / accepted / rejected |
| Technical approval | Tech Lead | external reference or pending | pending / accepted / rejected |
| QA sign-off | QA Lead | external reference or pending | pending / accepted / rejected |
| Release/deployment authorization | Release Manager / SRE | external reference or pending | pending / accepted / rejected |

> Do not fill `accepted` without an actual decision record.

## 11. Handoff summary

- What is ready.
- What is not ready.
- What must happen next.
- Residual risk the receiving/approving role must understand.
```
