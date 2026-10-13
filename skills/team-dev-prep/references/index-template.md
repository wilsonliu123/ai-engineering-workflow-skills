# Development Preparation Index Template

Use this layout for `docs/dev-prep/<feature-slug>/index.md`.

```markdown
---
artifact_id: DEVPREP-<feature-slug>
artifact_type: dev-prep
document_type: development-preparation-index
feature: <feature-slug>
artifact_status: draft
review_status: not-started
validity: current
risk_level: <low|medium|high|critical>
approval_reference: null
owner_role: Tech Lead
reviewer_role: Senior Engineer
requirements_source: docs/requirements/<feature-slug>.md
design_source: docs/design/<feature-slug>/sad.md
engineering_standards: docs/shared/engineering-standards.md
source_commit: <git-sha-or-unknown>
---

# <Feature> — Development Preparation

## 1. Purpose and Handoff Scope
### 1.1 Intended outcome
### 1.2 In scope
### 1.3 Explicitly out of scope

## 2. Authoritative Inputs
### 2.1 Requirements and AC
### 2.2 Design documents
### 2.3 Engineering standards / ADR / glossary
- Canonical engineering standards: `docs/shared/engineering-standards.md`
### 2.4 Repository baseline

## 3. Repository Implementation Map
### 3.1 Likely code entry points
### 3.2 Existing patterns to reuse
### 3.3 Relevant tests / fixtures / schemas
### 3.4 Areas that must not be changed without confirmation

## 4. Global Implementation Invariants
- Security / authorization
- Data ownership / integrity
- Interface compatibility
- Migration / rollback / forward-fix
- Observability / error-handling
- Other approved constraints

## 5. Task Plan
| Task ID | Outcome | REQ/AC | Depends on | Status |
|---|---|---|---|---|
| TASK-001 | ... | REQ-..., AC-... | none | draft |

## 6. Validation Baseline
### 6.1 Shared build/test/check commands or locations
### 6.2 Evidence expected from implementation
### 6.3 QA-relevant notes

## 7. Risks, Blockers, and Open Items
### 7.1 Blockers
### 7.2 Known implementation risks
### 7.3 Questions requiring upstream clarification

## 8. Human Review Summary
### 8.1 Ready for coding?
### 8.2 Conditions before handoff
### 8.3 Review record reference
```

Use the common artifact governance model. The skill may complete content, but only an explicit human decision may set `review_status: approved`.
