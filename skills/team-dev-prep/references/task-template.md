# Coding Task Brief Template

Use one file per task under `docs/dev-prep/<feature-slug>/tasks/`.
Recommended filename: `TASK-001-short-outcome.md`.

```markdown
---
artifact_type: coding-task-brief
feature: <feature-slug>
task_id: TASK-001
artifact_status: complete
validity: current
owner_role: Developer
reviewer_roles:
  - Tech Lead
source_commit: <git-sha-or-unknown>
---

# TASK-001 — <Outcome>

## 1. Outcome
Describe the observable result of this task in one or two paragraphs.

## 2. Traceability
- Requirements: REQ-...
- Enabling work: <only when no REQ applies: why this task is needed>
- Acceptance criteria: AC-...
- Design constraints: <links/sections>
- Interface/schema: <canonical source if applicable>

## 3. Must Read Before Coding
List only the authoritative files/sections needed to begin safely.

## 4. Repository Starting Points
- Code/modules to inspect first
- Existing patterns to reuse
- Relevant tests/fixtures/schemas

## 5. In Scope
Concrete changes allowed by this task.

## 6. Out of Scope / Do Not Change
Explicit boundaries, especially public behavior, security, data contracts, or unrelated refactors.

## 7. Implementation Constraints
Approved architecture, interface, data, security, compatibility, and reliability constraints that apply.

## 8. Verification Requirements
### 8.1 Required automated checks/tests
### 8.2 Required behavior/evidence
### 8.3 Documentation updates, if any

## 9. Stop and Escalate If
List discoveries that require human or upstream document review before continuing.

## 10. Completion Handoff
Provide after implementation:
- changed code/PR reference,
- tests/checks actually run and results,
- deviations or unresolved items,
- evidence location,
- downstream documentation that needs update.

## 11. Dependencies
- Blocking tasks: TASK-...
- External dependencies: ...
```

Do not insert large copies of requirements or design text. Link to canonical sources.
