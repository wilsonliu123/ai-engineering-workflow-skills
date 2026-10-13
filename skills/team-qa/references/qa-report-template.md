# QA Report Template

Use this layout for `docs/qa/<feature-slug>/qa-report.md`.

```markdown
---
artifact_id: QAREPORT-<feature-slug>
artifact_type: qa-report
feature: <feature-slug>
artifact_status: draft
review_status: not-started
validity: current
risk_level: <low|medium|high|critical>
approval_reference: null
owner_role: QA
reviewer_role: QA Lead
requirements: docs/requirements/<feature-slug>.md
test_spec: docs/qa/<feature-slug>/test-spec.md
test_cases: docs/qa/<feature-slug>/test-cases.md
execution_matrix: docs/qa/<feature-slug>/execution-matrix.md
evidence_root: .ai-workflow/evidence/<feature-slug>/normalized/
review: docs/review/<feature-slug>/code-review.md
tested_commit: <commit-or-unknown>
tested_build: <build-or-unknown>
test_environment: <environment-or-unknown>
---

# QA Report — <Feature Title>

## 1. Tested identity and scope
### 1.1 Build / commit / component versions
### 1.2 Environment and configuration
### 1.3 Execution window / run references
### 1.4 Included and excluded scope
### 1.5 Real vs mocked dependencies

## 2. Execution summary
| Status | Count |
|---|---:|
| pass | 0 |
| fail | 0 |
| blocked | 0 |
| not-executed | 0 |
| not-applicable | 0 |
| waived | 0 |

## 3. Test results
| TC | REQ / AC | Status | Actual result | Evidence | Defect |
|---|---|---|---|---|---|

## 4. Acceptance coverage
| AC | Planned coverage | Executed coverage | Outcome | Gap / note |
|---|---|---|---|---|

## 5. Defects and blockers
### QA-DEF-001 — <Title>
- **Severity:**
- **TC / REQ / AC:**
- **Tested version:**
- **Preconditions:**
- **Reproduction:**
- **Expected:**
- **Actual:**
- **Evidence:**
- **External ticket:**
- **Status:** open / fixed-pending-retest / retest-pass / retest-fail / accepted-risk
- **Root-cause note:** <evidence-based or explicitly hypothesis>

## 6. Retest and regression

## 7. Non-functional results

## 8. Evidence coverage and limitations
Summarize mapped evidence, missing evidence, unsupported evidence formats, and tested-target identity gaps.

### 8.1 Known limitations
Numbered list, copied verbatim into release notes: untested areas (browsers, performance, accessibility), missing evidence, accepted risks with scope, process deviations.

### 8.2 Accepted non-defect issues
Issues accepted by a named decision (who, reference) that are not defects; do not log them as QA-DEF.

## 9. Residual risks and decision points

## 10. QA conclusion for human review
Before concluding, `validate_qa.py docs/qa/<feature-slug> --evidence-dir <normalized-dir>` must pass (`--evidence-dir` is mandatory once this report exists).
State only what the evidence supports. Do not self-approve release.
```
