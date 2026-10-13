# Common Artifact Governance

All lifecycle artifacts must use a consistent governance model.

## Canonical metadata

Primary stage artifacts should include YAML frontmatter with these fields when applicable:

```yaml
artifact_id: <stable-id>
artifact_type: <requirements|requirements-testability|design|test-spec|qa-execution-matrix|qa-execution-run|dev-prep|dev-handoff|code-review|qa-report|release-handoff>
feature: <feature-slug>
artifact_status: <draft|complete|superseded>
review_status: <not-started|in-progress|changes-requested|approved|rejected>
validity: <current|stale|unknown>
risk_level: <low|medium|high|critical>
owner_role: <role>
reviewer_roles:
  - <role>
source_commit: <git-sha-or-unknown>
approval_reference: <external-record-or-null>
```

Do not use a single `status` field as both completion state and human approval state.

## Approval gate

A downstream stage may enter its authoritative execution mode only when every required upstream package satisfies:

- `artifact_status: complete`
- `review_status: approved`
- `validity: current`

If any required upstream package fails this gate, stop authoritative downstream work. You may perform non-authoritative impact analysis or draft preparation only when clearly labeled as such.

## Approval authority

AI may recommend, summarize, ask questions, and record a human decision. AI must never invent approval.

Only update `review_status` to `approved`, `rejected`, or `changes-requested` after an explicit human decision or an authoritative external approval record is provided. Record the approval reference when available.

## Package-level gate

When a stage produces multiple files, one primary artifact controls the package gate:

- Requirements: `docs/requirements/<feature-slug>.md`
- Design: `docs/design/<feature-slug>/sad.md`
- Dev prep: `docs/dev-prep/<feature-slug>/index.md`
- Dev handoff: `docs/dev-delivery/<feature-slug>/dev-delivery.md`
- Code review: `docs/review/<feature-slug>/code-review.md`
- QA planning: `docs/qa/<feature-slug>/test-spec.md`
- QA result: `docs/qa/<feature-slug>/qa-report.md`
- Release: `docs/release/<feature-slug>/release-handoff.md`

Supporting files cannot independently authorize downstream execution.

## Baseline and stale detection

Record exact upstream document paths and Git baselines whenever available. If an approved upstream artifact changes after a downstream artifact was created or approved, preserve the historical approval but mark the downstream artifact `validity: stale` until impact is assessed and required re-review completes.

Never attach an old approval to changed content silently.

## Changes requested loop

When review requests content changes:

1. Set the reviewed artifact `review_status: changes-requested`.
2. Return content modification to the owning stage skill.
3. Update the artifact and its source baseline.
4. Re-enter document review.
5. Do not let `team-doc-review` rewrite substantive source content on behalf of the owning stage skill.

## Risk propagation

Requirements establish the initial risk level. Downstream stages may raise risk when new technical, security, data, migration, compatibility, or operational concerns are discovered. Do not lower a previously approved risk level without explicit human confirmation.

Higher risk must result in deeper human review and stronger evidence expectations.

## Observability invariant

Workflow telemetry is non-authoritative and best-effort. Telemetry availability or failure must never change artifact content, `artifact_status`, `review_status`, `validity`, approval authority, or stage-gate decisions. Use `references/observability-protocol.md` for the event contract.


## QA evidence boundary

QA plans, execution assignments, actual test execution, normalized evidence, and final QA assessment are distinct lifecycle facts:

- `team-qa` defines what to test.
- QA humans choose automated/manual/hybrid/deferred execution.
- Existing QA tools/agents/humans perform actual tests.
- `team-qa-evidence` normalizes actual tool/manual results.
- `team-qa final-assessment` interprets normalized evidence.

Missing evidence must never be treated as pass.
