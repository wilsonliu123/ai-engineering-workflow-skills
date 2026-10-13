# SAD template

Use this structure for `docs/design/<feature-slug>/sad.md`.

```markdown
---
artifact_id: DESIGN-<feature-slug>
artifact_type: design
feature: <feature-slug>
artifact_status: draft
review_status: not-started
validity: current
risk_level: <low|medium|high|critical>
approval_reference: null
owner_role: Tech Lead
reviewer_roles:
  - Architect
source_requirements: docs/requirements/<feature-slug>.md
engineering_standards: docs/shared/engineering-standards.md
codebase_commit: <git-sha-or-unknown>
---

# <Feature> — System Architecture Design

## 1. Design summary
### 1.1 Objective
### 1.2 In scope
### 1.3 Out of scope
### 1.4 Requirement and AC traceability

## 2. Current-state context
### 2.1 Relevant existing architecture
### 2.2 Affected modules and dependencies
### 2.3 Constraints and reusable patterns
- Cite applicable MUST/SHOULD rules from `docs/shared/engineering-standards.md`; do not duplicate the full standard.

## 3. Proposed architecture
### 3.1 System boundary and context
### 3.2 Component/module responsibilities
### 3.3 Allowed and prohibited dependencies
### 3.4 Main runtime flows
### 3.5 Failure and recovery flows

## 4. Data and interfaces
### 4.1 Data ownership and model impact
### 4.2 Persistence/migration impact
### 4.3 Interface changes and canonical specification
### 4.4 Compatibility and versioning

## 5. Cross-cutting concerns
### 5.1 Identity, authorization, and security
### 5.2 Error handling, timeout, retry, idempotency
### 5.3 Observability and audit
### 5.4 Performance, capacity, and reliability

## 6. Deployment and change strategy
### 6.1 Configuration/environment impact
### 6.2 Migration or rollout
### 6.3 Rollback or forward-fix

## 7. Design decisions and alternatives
### 7.1 Accepted decisions
### 7.2 Proposals awaiting review
### 7.3 Rejected alternatives and reasons

## 8. Risks, assumptions, and open questions
### 8.1 Risks
### 8.2 Assumptions
### 8.3 Open questions / blockers

## 9. Specialized design documents
- Frontend: ./frontend.md | Not applicable
- Backend: ./backend.md | Not applicable
- Interface: ./interface.md or canonical schema path | Not applicable

## 10. Design validation points
- Architectural checks for code review
- Behaviors/interfaces that QA must be able to observe

## 11. Human review record
Do not fabricate approval. Link or record actual review references only when provided.
```
