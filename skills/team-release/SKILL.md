---
name: team-release
description: "Create and maintain the team's standardized release and delivery documentation by reconciling approved scope, exact implementation/build identity, code review results, QA evidence, manuals, operational notes, known limitations, and human decision records. Use when preparing a release candidate, internal handoff, customer delivery, deployment package, or final delivery record after implementation/review/QA. This skill writes release documentation only under docs/release/, never changes product code, deploys, approves release, invents sign-off, or treats mismatched/untested versions as validated."
---

# Team Release

## Lifecycle governance (v0.3, authoritative)

Read `references/common-artifact-governance.md` before creating or updating lifecycle artifacts. If older wording in this skill conflicts with that reference, the v0.3 governance reference takes precedence.

Use `artifact_status`, `review_status`, `validity`, `risk_level`, source baselines, and package-level gates. Do not use a single `status` field for both completion and human approval.

After producing a complete artifact package, immediately enter the review protocol in `references/review-protocol.md` when the user is present. Ask one material question at a time. Do not batch all decisions into one message. If the review is interrupted, persist the review session under `docs/workflow/reviews/` so `team-doc-review` can resume later. Only an explicit human decision or authoritative external record may change `review_status` to `approved`, `changes-requested`, or `rejected`.

Create an evidence-linked release handoff that answers: **what is being delivered, which exact versions were reviewed/tested, what remains risky or incomplete, and what human decision is still required?**

## Hard boundaries

- Write release/delivery documentation only. Do not modify product code, tests, requirements, design, QA results, CI/CD configuration, infrastructure, deployment configuration, or external systems.
- Default output language: Write release handoffs, release notes, runbooks, and checklists in Traditional Chinese (繁體中文) by default, preserving version numbers, commit SHAs, package URLs, and command lines.
- Do not deploy, publish, merge, tag, approve, or sign off a release.
- Never invent approval, recipient acceptance, environment readiness, test results, rollback success, screenshots, run IDs, release IDs, or artifact hashes.
- Do not convert a QA/review observation into `pass` or `approved` unless the source artifact explicitly supports it.
- If the candidate build differs from the version reviewed or tested, flag the mismatch and require impact assessment/revalidation; do not silently reuse prior evidence.
- Do not rewrite upstream requirements/design to make the delivered implementation appear compliant.
- The skill may set `artifact_status: complete` when content is complete, but `review_status: approved` may only be recorded after an explicit human decision or authoritative external approval record.

## Repository contract

Use repository-relative paths.

### Primary upstream inputs

1. `docs/requirements/<feature-slug>.md` — authoritative scope, REQ, AC, constraints.
2. `docs/design/<feature-slug>/` — SAD and applicable frontend/backend/interface design.
3. `docs/shared/engineering-standards.md` — canonical repository-wide release, evidence, security, observability, compatibility, and AI-governance rules.
4. `docs/dev-prep/<feature-slug>/` — implementation task scope and declared completion evidence, when available.
5. Exact source/build/release candidate identity — commit SHA, PR, build ID, package/image digest, or equivalent.
6. `docs/review/<feature-slug>/code-review.md` — review scope/findings and exact reviewed identity.
7. `docs/qa/<feature-slug>/qa-report.md` — QA scope/results and exact tested identity.
8. Applicable manuals/operational documents already maintained in the repository.
9. Actual deployment/readiness/acceptance records when available.

### Standard output

Create or update:

`docs/release/<feature-slug>/release-handoff.md`

Use Git history as the primary change history. Do not auto-increment a document version for routine edits.

## Workflow

### 1. Resolve release identity and intended handoff

Reuse the same `<feature-slug>` used upstream.

Determine whether this is:

- an internal handoff,
- a release candidate readiness review,
- a customer delivery package,
- a production deployment handoff,
- or a post-deployment delivery record.

Record the intended target and boundaries. Do not infer that production deployment is intended merely because QA passed.

### 2. Load authoritative upstream artifacts

Read complete in-scope requirements/AC first, then `docs/shared/engineering-standards.md`, design, review, QA, manuals, and exact implementation/build identity.

Keep these information classes distinct:

- **Intended scope** — from requirements/design.
- **Implemented candidate** — exact code/build/artifact identity.
- **Verification evidence** — code review and QA evidence.
- **Operational readiness** — deployment/configuration/migration/rollback/monitoring evidence.
- **Human decision** — approval, acceptance, or waiver made outside this skill.

### 3. Reconcile version identity before summarizing readiness

Establish, as applicable:

- source commit / PR,
- frontend build/version,
- backend build/version,
- container/package/artifact digest,
- database migration version,
- configuration/schema version,
- reviewed identity,
- QA-tested identity,
- target environment identity.

Classify alignment as:

- `aligned` — the delivery candidate is the same identity covered by required review/QA evidence.
- `changed-after-review` — candidate changed after review.
- `changed-after-qa` — candidate changed after QA.
- `partially-aligned` — only some components match verified versions.
- `unknown` — identity cannot be established.

Any state other than `aligned` must be called out prominently with required next action. Do not carry forward a release-ready conclusion automatically.

### 4. Reconcile scope and requirement coverage

Use `references/release-method.md`.

For each in-scope REQ/AC, determine from upstream records whether it is:

- implemented and verified,
- implemented but not verified,
- intentionally deferred with human-approved reference,
- blocked,
- or unresolved/unknown.

Do not infer completion from source-code presence alone.

### 5. Summarize review and QA evidence without re-evaluating it

Extract, do not invent:

- review scope and status,
- blocker/critical/major findings still open,
- QA executed scope and result distribution,
- blocked/not-executed coverage,
- unresolved defects,
- waivers/accepted risks with explicit external references.

If review or QA documents are missing, incomplete, or refer to another version, state this as a release-readiness gap.

If `docs/shared/engineering-standards.md` is missing, the release handoff may be drafted but MUST surface the missing canonical standard as a governance/readiness gap. Do not infer repository-wide release/security/operational rules from memory or local convention alone.

### 6. Check delivery documentation and operational readiness

When relevant, verify the presence and applicability of:

- user guide / admin guide,
- developer/integration guide,
- deployment/runbook,
- configuration/environment notes,
- migration steps,
- rollback or forward-fix plan,
- health checks,
- observability/alerts,
- support/escalation notes,
- release notes/change summary.

Do not require irrelevant documents merely to fill a checklist. Mark `not-applicable` with rationale when justified.

### 7. Record known limitations and residual risk

Separate:

- known product limitations,
- known defects,
- unverified areas,
- environment/dependency constraints,
- operational risks,
- documentation gaps,
- external dependencies or deadlines.

Never hide an unresolved issue inside prose. Give it a stable identifier where useful (e.g. `REL-RISK-001`).

### 8. Record human decision points

Use role-based ownership by default, such as:

- `release_owner_role: Release Manager`
- `business_acceptance_role: Product Owner`
- `technical_approver_role: Tech Lead`
- `qa_signoff_role: QA Lead`

If an actual approval/acceptance record exists, preserve its external reference and actor identity exactly as evidence. Do not manufacture names or approvals.

### 9. Produce/update `release-handoff.md`

Follow `references/release-handoff-template.md`.

The document must include:

- exact release candidate identity,
- scope and requirement summary,
- review and QA evidence references,
- version-alignment result,
- delivery artifacts/manuals,
- deployment/migration/rollback notes when applicable,
- known limitations and residual risks,
- unresolved blockers/gaps,
- required human decisions and handoff roles,
- actual approval/acceptance references when supplied.

Use concise links/references rather than copying entire upstream documents.

### 10. Determine document readiness state

The skill may set its own document state to:

- `draft` — information is incomplete or reconciliation has material gaps.
- `review` — the handoff package is complete enough for human release/acceptance review.

Do not set `approved`, `released`, `deployed`, or `accepted` unless those are quoted from an actual external record; even then, store them as observed release facts, not as this skill's autonomous decision.

### 11. Validate the handoff

Run:

`scripts/validate_release.py docs/release/<feature-slug>/release-handoff.md`

When upstream documents are available, additionally inspect that every cited file/path exists and that the candidate/review/QA version identities are explicitly recorded.

## Completion criteria

A `team-release` run is complete when:

- `release-handoff.md` exists at the standard path;
- the exact candidate identity is recorded;
- review/QA evidence is linked or explicitly missing;
- version alignment is stated;
- scope, residual risks, known limitations, and unverified areas are visible;
- operational/manual artifacts are referenced as applicable;
- required human decision roles are clear;
- no deployment, approval, acceptance, or test evidence has been invented;
- the validator passes or remaining validation errors are reported transparently.


## Engineering-standards source of truth

- `docs/shared/engineering-standards.md` is canonical for repository-wide engineering rules.
- `docs/shared/engineering-standards.zh-TW.md` is a synchronized translation unless the repository explicitly declares another precedence rule.
- The release handoff should summarize unresolved standards exceptions or operational MUST-rule gaps; it should not copy the full standard.
- Repository-native CI/CD, scanner, policy, and deployment configuration remains authoritative for machine-enforced details referenced by the standard.

## Bundled resources

- `references/release-method.md` — release reconciliation and version-alignment method.
- `references/release-handoff-template.md` — standardized release/delivery handoff layout.
- `scripts/validate_release.py` — release-handoff structural and claim-safety validator.

## v0.2 preflight gate
Release handoff preparation requires an exact release candidate identity. Authoritative release readiness requires approved/current Requirements, Design, Dev Prep, Dev Handoff, Code Review, and QA Report whose baselines align with that candidate. Any mismatch makes release `validity: stale` or `unknown` and must produce explicit revalidation actions.

The Release artifact may become human-approved as a document only after the review protocol; that approval records acceptance of the handoff record and does not itself mean production deployment occurred. Deployment/release execution remains a separate real-world fact that must be evidenced.

## Deterministic upstream gate check
When execution is available, run `scripts/check_upstream_gate.py --downstream <this-stage-primary> <upstream ...>` against every required upstream primary artifact (and `docs/ux/<feature-slug>/` when used) before authoritative downstream work; on first creation use `--record` instead, which gates and then writes the content baselines. A non-zero exit (`BLOCK` or `STALE`) blocks authoritative progression. Do not override the script result by interpretation; resolve the upstream governance state first.

## Workflow observability (v0.3)

Read `references/observability-protocol.md` before execution. Start a telemetry session with `scripts/emit_event.py` when the skill begins, emit the stage-specific events defined by the protocol at meaningful lifecycle points, and attempt a `skill_completed` event before returning. Telemetry is best-effort and **must never block or change** artifact generation, validation, approval, gate, or handoff behavior. Never log prompt text, code/document content, secrets, customer data, or personal identities.
