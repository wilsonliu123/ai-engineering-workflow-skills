# Code Review Document Template

Create at `docs/review/<feature-slug>/code-review.md`.

```markdown
---
artifact_id: CODEREVIEW-<feature-slug>
artifact_type: code-review
feature: <feature-slug>
artifact_status: draft
review_status: not-started
validity: current
risk_level: <low|medium|high|critical>
approval_reference: null
owner_role: Reviewer
reviewer_roles:
  - Tech Lead
requirements_path: docs/requirements/<feature-slug>.md
design_path: docs/design/<feature-slug>/sad.md
dev_prep_path: docs/dev-prep/<feature-slug>/index.md | not-applicable
engineering_standards: docs/shared/engineering-standards.md
review_target: <PR-number/url-or-description>
base_commit: <git-sha-or-unknown>
head_commit: <git-sha-or-unknown>
---

# Code Review — <Feature title>

## 1. Review Scope and Evidence
### 1.1 Change set reviewed
State PR/commit range or local-change scope. Explicitly state whether staged, unstaged, and untracked changes were included when relevant.

### 1.2 Authoritative inputs
- Requirements / AC
- SAD / design attachments
- Dev-prep tasks
- Engineering standards: `docs/shared/engineering-standards.md`
- Repository-native machine rules (formatter/linter/type/static/architecture/security checks)

### 1.3 Evidence available
List static code evidence, tests/CI/build reports actually executed or supplied, and unavailable evidence.

### 1.4 Exclusions and limitations
State anything not reviewed, inaccessible, or not proven.

## 2. Engineering Standards Review
Summarize conformance and material deviations. Reference finding IDs instead of duplicating finding text.

## 3. Requirement and Acceptance-Criteria Review
For relevant REQ/AC, record implementation observations and any gaps. Distinguish static evidence from runtime validation still required by QA.

## 4. Architecture and Design Review
Cover module boundaries, interfaces, data/security constraints, state/error flows, compatibility/migration, and design deviations that apply.

## 5. Implementation and Test Quality
### 5.1 Correctness and edge cases
### 5.2 Security / authorization / data integrity
### 5.3 Reliability / error handling / observability
### 5.4 Test-code quality and coverage
### 5.5 Documentation / configuration synchronization

## 6. Findings
Use one block per finding.

### REV-001 — <Short title>
- Severity: blocker | major | minor | note
- Axis: standards | requirement | design | implementation | test | documentation
- Status: open | resolved | accepted-risk | not-applicable
- Location: `<path>:<line-or-symbol>`
- Related source: `AC-...`, `REQ-...`, design section, standard, or `not-applicable`
- Observation: <what the code/evidence shows>
- Impact: <why this matters>
- Recommended disposition: <fix / clarify upstream / add evidence / accept by authorized human>

Do not create empty findings merely to populate this section. If no actionable finding exists, state that explicitly.

## 7. Traceability and Coverage Summary
Summarize which relevant REQ/AC and design constraints were reviewed, and which remain outside scope or require runtime QA.

## 8. Test and CI Evidence
List only actual execution evidence. For each item include revision/environment when known and result. If none was available, say so explicitly.

## 9. Open Risks and Unverified Areas
List unresolved assumptions, missing evidence, partial review areas, and anything QA/humans must pay attention to.

### 9a. Re-review (incremental) — repeat per round
- Round / date:
- Previous head_commit → new head_commit (diff reviewed: `git diff <prev>..<new>`):
- Findings re-checked (REV-xxx → resolved / still open, evidence):
- New findings in the delta:
<!-- Update head_commit in frontmatter to the new commit; validate_review.py fails if code changed after head_commit. -->

## 10. Human Review Summary
- Open blocker findings: <count>
- Open major findings: <count>
- Open minor findings: <count>
- Open notes: <count>
- Runtime validation still required: <yes/no + scope>
- Human decision required: <what must be decided>
- Handoff state: <not-ready | ready-for-human-review | ready-for-qa-after-human-approval>
```

Do not write `review_status: approved` unless an explicit human decision or authoritative external approval exists and is being synchronized.
