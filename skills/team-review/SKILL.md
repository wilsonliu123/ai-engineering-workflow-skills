---
name: team-review
description: Create or update the team's standardized code-review document for an implemented feature by inspecting the actual repository change and checking it against engineering standards, approved requirements/acceptance criteria, approved SAD/design/interface documents, and implementation-preparation scope. Use after RD/AI coding has produced a reviewable code change and before QA execution or release. This skill writes review documentation only under docs/review/, preserves evidence and traceability, distinguishes static code observations from executed test evidence, and never modifies product code, silently changes upstream specifications, approves its own review, merges, or deploys.
---

# Team Review

## Lifecycle governance (v0.3, authoritative)

Read `references/common-artifact-governance.md` before creating or updating lifecycle artifacts. If older wording in this skill conflicts with that reference, the v0.3 governance reference takes precedence.

Use `artifact_status`, `review_status`, `validity`, `risk_level`, source baselines, and package-level gates. Do not use a single `status` field for both completion and human approval.

After producing a complete artifact package, immediately enter the review protocol in `references/review-protocol.md` when the user is present. Ask one material question at a time. Do not batch all decisions into one message. If the review is interrupted, persist the review session under `docs/workflow/reviews/` so `team-doc-review` can resume later. Only an explicit human decision or authoritative external record may change `review_status` to `approved`, `changes-requested`, or `rejected`.

Produce a repository-grounded review record for the exact code change being reviewed. Verify the implementation on three independent axes: engineering standards, requirement/AC conformance, and design/architecture conformance. Record findings with evidence and limitations so humans can make the final review decision.

## Hard boundaries

- Write review documentation only. Do not modify product code, tests, migrations, infrastructure, configuration, generated artifacts, requirements, design documents, or development-preparation documents.
- Do not silently "fix" code while reviewing. Findings belong in the review document; remediation is a separate development action.
- Do not change requirements or design to make the implementation appear conformant. Record the mismatch and route it to the appropriate upstream owner.
- Do not mark the review `approved`. The skill may set `artifact_status: complete` when content is complete, but human `review_status` remains `not-started`/`in-progress` until an explicit human decision.
- Do not claim runtime behavior, test success, performance, security, or deployment readiness unless supported by actual execution evidence available to the review.
- Do not treat absence of a finding as proof that unreviewed code or behavior is correct.

## Repository contract

Use repository-relative paths.

### Primary inputs

1. The exact implementation change under review: a PR/commit range when supplied, otherwise the relevant working-tree/staged/untracked changes that can be identified safely.
2. `docs/requirements/<feature-slug>.md` — requirement and AC source of truth.
3. `docs/design/<feature-slug>/sad.md` plus applicable frontend/backend/interface design documents.
4. `docs/dev-prep/<feature-slug>/index.md` and applicable task briefs when present.
5. `docs/shared/engineering-standards.md` — canonical repository-wide engineering rules, plus repository-native lint/style/type/static-analysis configuration referenced by or consistent with those rules.
6. Relevant tests, schemas, CI results, build/test reports, and other execution evidence when available.

If the code scope cannot be identified precisely, required upstream documents are missing, or the feature identity is ambiguous, report the gap instead of producing a falsely complete review.

If `docs/shared/engineering-standards.md` is missing, the engineering-standards axis is not fully evaluable. Keep the review `draft`, state that limitation, and do not present a complete conformance conclusion. Repository-native machine checks may still be reported as evidence, but they do not replace the missing canonical standard.

### Standard output

Create or update:

`docs/review/<feature-slug>/code-review.md`

This file is the authoritative review record for the reviewed code scope. Git history is the primary history of changes to the review document.

## Workflow

### 1. Resolve the feature and review scope

Reuse the same `<feature-slug>` used by requirements/design/dev-prep.

Identify exactly what code is being reviewed. Prefer an explicit PR or commit range. If reviewing local changes, account for the relevant working tree, staged changes, and untracked files; do not rely only on a committed diff when the implementation has not yet been committed.

Record in the review document:

- review target/reference,
- base and head commits when known,
- whether staged/unstaged/untracked changes were included,
- files/modules reviewed,
- any known exclusions or inaccessible evidence.

A partial scope must produce a partial-scope conclusion.

### 2. Load authoritative upstream context

Read the complete requirement and relevant ACs, then the approved/reviewed design package and development-preparation package. Read `docs/shared/engineering-standards.md` and inspect the repository-native machine rules and existing code patterns needed to understand the change.

Build a review checklist from actual source material rather than generic preferences.

Keep these categories separate:

- **Requirement intent** — what observable behavior and AC must hold.
- **Design constraints** — how the approved architecture/interfaces/data/security model constrain implementation.
- **Engineering standards** — repository/team conventions and maintainability rules.
- **Implementation evidence** — what the actual code/tests/CI show.

### 3. Review engineering-standards conformance

Use `references/review-method.md`.

Check relevant areas such as:

- naming, structure, readability, complexity, duplication,
- module/layer boundaries and dependency direction where encoded as engineering standards,
- error handling, resource handling, logging, and observability conventions,
- input validation and security hygiene,
- dependency/configuration changes,
- repository conventions and generated-code boundaries,
- test quality and documentation synchronization.

Prefer repository-specific rules over personal style preferences. Do not create findings for merely subjective choices when no standard, defect, or material maintainability risk exists.

### 4. Review requirement and AC conformance

For each in-scope REQ/AC affected by the code change, determine whether the implementation appears to:

- implement the required behavior,
- omit a required behavior,
- contradict the required behavior,
- implement behavior beyond approved scope,
- depend on runtime evidence that cannot be proven from static review.

Map findings to REQ/AC identifiers. Static code inspection may support a conformance observation, but it does not replace QA execution for observable runtime behavior.

### 5. Review design/architecture conformance

Compare the implementation against the SAD and applicable frontend/backend/interface documents.

Check, when relevant:

- component/module responsibilities,
- dependency direction and boundaries,
- API/event/schema contracts,
- data ownership and persistence assumptions,
- authorization/security enforcement points,
- state transitions and error flows,
- migration/compatibility strategy,
- observability/reliability behavior,
- explicit architecture invariants.

If implementation differs from design, classify the difference as either:

- a likely implementation defect,
- an intentional deviation with existing approval evidence,
- or a design/requirement question that must be resolved upstream.

Do not silently normalize an undocumented deviation.

### 6. Assess implementation and test quality

Review whether the code is reasonably robust for the specified behavior, including edge cases and failure paths relevant to the change.

Review tests for:

- meaningful behavioral coverage rather than implementation mirroring,
- coverage of relevant ACs and important failure/permission paths,
- deterministic setup and useful assertions,
- missing tests for changed risk areas,
- misleading tests that pass without proving the requirement.

Record test/CI execution separately from static test-code review:

- **Observed test code** — tests exist and appear to target behavior.
- **Executed evidence** — a command/CI job actually ran against a specific revision and produced a result.

Never convert the first into the second.

### 7. Record evidence-based findings

Follow `references/review-template.md`.

Each actionable finding must include:

- stable finding ID such as `REV-001`,
- severity: `blocker`, `major`, `minor`, or `note`,
- axis/category,
- concrete code/file location,
- violated or affected source (REQ/AC/design/standard) when applicable,
- observation,
- impact/risk,
- recommended disposition,
- status (`open`, `resolved`, `accepted-risk`, or `not-applicable`) only when supported by actual review state.

Use severity based on impact, not stylistic preference:

- `blocker` — unsafe to proceed; likely severe security/data loss/correctness issue or a hard violation of a must-have requirement.
- `major` — significant functional, architectural, security, reliability, or maintainability problem that should be resolved before normal handoff.
- `minor` — localized issue with limited impact that should be corrected but does not normally invalidate the whole change.
- `note` — non-blocking observation, follow-up, or improvement suggestion.

Do not manufacture findings to fill every section.

### 8. Validate coverage and limitations

Before handoff, verify:

- reviewed code scope is stated precisely,
- every relevant finding points to evidence/location,
- unknown/unavailable evidence is labeled rather than inferred,
- relevant REQ/ACs are considered or explicitly outside the reviewed scope,
- relevant design constraints are considered,
- test execution claims name the evidence/source,
- unresolved blockers/majors are visible in the summary,
- no human approval is fabricated.

Run:

`scripts/validate_review.py docs/review/<feature-slug>/code-review.md docs/requirements/<feature-slug>.md docs/design/<feature-slug>/sad.md`

when execution is available.

### 9. Finish in `review` state

Set the primary artifact to `artifact_status: complete` when complete enough for human review; otherwise `artifact_status: draft`. Set `review_status: not-started` before review begins.

Summarize for the human reviewer:

- exact reviewed scope,
- count of open findings by severity,
- requirement/design conformance concerns,
- test/CI evidence actually available,
- unreviewed or unverifiable areas,
- whether blockers/majors remain before QA handoff.

Do not self-record human approval or merge readiness as a final decision.

## Status and metadata rules

Git history is the primary version history. Do not auto-increment document versions for routine edits.

Use role-based responsibility, for example:

- `owner_role: Reviewer`
- `reviewer_role: Tech Lead`

Record concrete people in the actual PR/issue/review system when available rather than requiring names in the template.

Record the reviewed base/head commit or PR reference when known. Use `unknown` rather than inventing values.

## Source-of-truth rules

- Requirement intent and AC remain canonical in `docs/requirements/`.
- Architecture and interface decisions remain canonical in `docs/design/`.
- Development-preparation scope remains canonical in `docs/dev-prep/`.
- `docs/shared/engineering-standards.md` remains canonical for written repository-wide engineering rules. Repository-native formatter/linter/type/static/architecture/security configuration remains authoritative for its machine-enforced details.
- `docs/review/<feature-slug>/code-review.md` records review observations and evidence; it does not redefine upstream truth.
- A resolved finding does not automatically mean the entire review is approved.

## Handoff to QA

After the implementation is corrected as needed and the team completes its normal human review/approval process, `team-qa` may consume:

- the reviewed implementation version,
- approved requirement/AC,
- design package,
- relevant review findings and limitations,
- available build/test evidence.

QA must independently validate runtime behavior; it must not treat this code-review document as proof that acceptance criteria pass.

## Bundled resources

- `references/review-method.md` — three-axis, evidence-based review method.
- `references/review-template.md` — standardized code-review document layout.
- `scripts/validate_review.py` — structural, traceability, and evidence-language checks.

## v0.2 preflight gate
Formal code review requires Requirements, Design, Dev Prep, and Dev Handoff primary artifacts to be complete, approved, and current, and the reviewed code identity must match the Dev Handoff implementation baseline. If a factual Dev Handoff exists but is not yet approved/current, stop formal review and request completion of that gate.

Read `docs/dev-delivery/<feature-slug>/dev-delivery.md` and use its actual implementation evidence, deviations, and review-focus notes. The code-review document is complete only for the explicitly reviewed code scope.

## Deterministic upstream gate check
When execution is available, run `scripts/check_upstream_gate.py --downstream <this-stage-primary> <upstream ...>` against every required upstream primary artifact (and `docs/ux/<feature-slug>/` when used) before authoritative downstream work; on first creation use `--record` instead, which gates and then writes the content baselines. A non-zero exit (`BLOCK` or `STALE`) blocks authoritative progression. Do not override the script result by interpretation; resolve the upstream governance state first.

## Workflow observability (v0.3)

Read `references/observability-protocol.md` before execution. Start a telemetry session with `scripts/emit_event.py` when the skill begins, emit the stage-specific events defined by the protocol at meaningful lifecycle points, and attempt a `skill_completed` event before returning. Telemetry is best-effort and **must never block or change** artifact generation, validation, approval, gate, or handoff behavior. Never log prompt text, code/document content, secrets, customer data, or personal identities.
