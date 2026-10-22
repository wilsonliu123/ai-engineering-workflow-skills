# UI Acceptance Template

Use for `docs/ux/<feature-slug>/ui-acceptance.md` (owned by UI/UX, pre-populated during UX design, executed during manual UI test runs). It is part of the gated `docs/ux/<slug>/` package and is attached to manual evidence (`write_manual_evidence.py --attachment docs/ux/<slug>/ui-acceptance.md@<commit>`).

```markdown
---
artifact_id: UIACC-<feature-slug>
artifact_type: ui-acceptance
feature: <feature-slug>
artifact_status: draft
validity: current
owner_role: UI/UX
tested_commit: <commit-or-pending>
screenshots: evidence/<tested-commit>/screenshots/
upstream: <written by check_upstream_gate.py --record (requirements, design package)>
---
# UI Acceptance — <Feature>

## 1. Coverage matrix
Screenshot names follow `<ScreenID>-<situation>[-<detail>].png` (situations: normal, loading, invalid, 403, 404, empty, offline) so each matrix cell maps to a file.
| Screen | normal | loading | invalid | 403 | 404 | empty | offline |
|---|---|---|---|---|---|---|---|
| S01 | planned | planned | planned | planned | planned | planned | planned |
| S02 | planned | planned | planned | planned | planned | planned | planned |

## 2. Test cases executed
| TC | Result (pass/fail/blocked/partial/not-executed) | Screenshots / recording | Notes |
|---|---|---|---|
| TC-001 | not-executed | S01-normal.png | Verify default cards grid layout |
| TC-002 | not-executed | S01-empty.png | Verify empty state illustration and CTA |

## 3. Interaction states (not provable by screenshot)
| Requirement | How verified (E2E assertion / recording) | Result |
|---|---|---|
| AC-001 | Tab focus cycles correctly through inputs | not-executed |

## 4. Findings
| ID | Severity (blocker/major/minor/note) | Screen | Description | Status |
|---|---|---|---|---|
```
