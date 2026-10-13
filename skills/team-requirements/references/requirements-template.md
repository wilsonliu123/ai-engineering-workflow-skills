# Requirements document template

Use this structure for `docs/requirements/<feature-slug>.md`. Sections marked **Required** must remain even when the value is `None` or `Not applicable`, with a reason where useful.

```markdown
---
artifact_id: REQDOC-<feature-slug>
artifact_type: requirements
document_type: requirements
artifact_status: draft
review_status: not-started
validity: current
risk_level: <low|medium|high|critical>
approval_reference: null
owner_role: Product Owner
reviewer_roles:
  - Tech Lead
  - QA
source_commit: <git commit, or "unavailable" when no git context exists>
engineering_standards: docs/shared/engineering-standards.md | unavailable
related_work_item: <issue/ticket/url/id if available, otherwise omit>
---

# <Feature or change name>

## 1. Summary
<!-- Required. One concise paragraph: problem, target users, intended outcome. -->

## 2. Goals and non-goals
### Goals
- ...
### Non-goals
- ...

## 3. Users and roles
<!-- Required. Actors affected by the change and their relevant permissions/responsibilities. -->

## 4. Scope
### In scope
- ...
### Out of scope
- ...

## 5. User and business flows
<!-- Required when the feature changes a workflow. Describe normal and important alternate/error flows. -->

## 6. Functional requirements
### REQ-001 — <short title>
- **Requirement:** ...
- **Business rules:** ...
- **Forbidden behavior:** ...
- **Priority:** Must / Should / May

<!-- Continue REQ-002, REQ-003... Reuse existing IDs on updates. -->

## 7. Acceptance criteria
### AC-001 — <observable outcome>
- **Covers:** REQ-001
- **Given:** ...
- **When:** ...
- **Then:** ...

<!-- Criteria must be observable/testable. Avoid implementation-specific assertions unless implementation itself is constrained. -->

## 8. Data and permissions
### Data
- Inputs, outputs, ownership, lifecycle, sensitivity, retention constraints.
### Permissions
- Who may view, create, update, delete, execute, export, administer, or approve.

## 9. Non-functional requirements
<!-- Include only sourced or confirmed requirements. Use "TBD" for undecided targets. -->
- Performance / capacity:
- Availability / recovery:
- Security / audit:
- Compatibility / accessibility / localization:
- Observability:

## 10. Dependencies and constraints
- Reference applicable repository-wide constraints from `docs/shared/engineering-standards.md` instead of duplicating them.
- External systems, contracts, platform constraints, regulatory constraints, deadlines if authoritative.

## 11. Assumptions
- A-001: <explicit assumption and impact if false>

## 12. Open questions and blockers
- OQ-001: <question> — Owner role: <role> — Blocking: Yes/No

## 13. Traceability
| Requirement | Acceptance criteria | Source / rationale |
|---|---|---|
| REQ-001 | AC-001 | <user request / issue / existing policy / decision> |

## 14. Change summary
<!-- Describe material changes from the prior committed version. For a new document, say "Initial draft". -->

## 15. Review checklist
- [ ] Scope and non-goals are explicit.
- [ ] Every Must requirement has at least one acceptance criterion.
- [ ] Permissions and sensitive-data behavior are explicit where relevant.
- [ ] Non-functional targets are sourced, confirmed, or marked TBD.
- [ ] Critical open questions are visible and not silently assumed.
- [ ] The document contains no implementation detail that is merely an unapproved suggestion.
```

## Governance state rules

- `artifact_status: draft`: critical content is still incomplete.
- `artifact_status: complete`: content is complete enough for human review.
- `review_status: not-started|in-progress|changes-requested|approved|rejected`: human review lifecycle.
- `validity: current|stale|unknown`: whether the artifact still matches its authoritative upstream/baseline context.
- `review_status: approved` requires an explicit human decision or authoritative external record and an `approval_reference` when available.

## Git metadata

`source_commit` identifies the repository state used while preparing the document. If the working tree has uncommitted changes relevant to the analysis, note that fact in the change summary. Do not invent a commit hash.

Git history is the document's revision history. Do not add a manual semantic version merely because the file was edited.
