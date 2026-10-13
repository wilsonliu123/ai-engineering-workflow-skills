# Release Method

## Purpose

Create a final handoff from the software lifecycle without turning document completeness into false release confidence.

## Core principles

1. **Identity before readiness.** Evidence applies only to the exact version/configuration it covered.
2. **Aggregate, do not reinterpret.** Release documentation summarizes upstream requirements, review, QA, and operational evidence; it does not silently change their conclusions.
3. **Evidence over assertion.** A claim such as "verified", "deployed", "accepted", or "rollback tested" requires an actual source record.
4. **Keep decision authority human.** The skill prepares the decision packet; authorized roles decide release/acceptance.
5. **Expose residual risk.** Failed, blocked, unexecuted, deferred, waived, unknown, and not-applicable are distinct states.
6. **Prefer references to duplication.** Link to canonical requirements/design/review/QA/manuals instead of copying whole sections.
7. **Do not let version drift inherit old evidence.** Any post-review or post-QA change requires explicit impact assessment and, where relevant, re-review/retest.

## Readiness sequence

1. Resolve intended handoff/release target.
2. Lock candidate identity.
3. Compare candidate identity with reviewed identity.
4. Compare candidate identity with QA-tested identity.
5. Check in-scope REQ/AC disposition.
6. Inventory unresolved review findings and QA defects/gaps.
7. Check required manuals and operational artifacts.
8. Surface migration/configuration/rollback/observability needs.
9. List residual risk, limitations, and required human decisions.
10. Produce a concise handoff packet for review.

## Scope disposition guidance

For each in-scope requirement or AC, use one of:

- `verified` — upstream evidence explicitly supports implementation and required validation.
- `implemented-unverified` — implementation is claimed/observed but required verification evidence is missing.
- `deferred` — intentionally moved out of this delivery with an external approved decision reference.
- `blocked` — cannot be completed/validated because of a known blocker.
- `unknown` — insufficient evidence to classify.

Do not use `verified` merely because code exists or a developer says "done" without the required evidence.

## Version-alignment guidance

Treat a release candidate as aligned only when all required independently versioned components match the versions covered by the relevant review/QA evidence, or when later changes are proven non-impacting by an explicit impact/revalidation record.

Examples of identity sources:

- Git commit SHA
- pull request head SHA
- build ID
- container/image digest
- package checksum/version
- frontend bundle version
- backend service version
- database migration ID
- deployment manifest/config revision

If identity cannot be established, classify alignment as `unknown`.

## Human-decision guidance

Typical roles (adapt to the team's actual model):

- Product Owner — business scope/acceptance
- Tech Lead — technical/architecture acceptance
- QA Lead — QA evidence review
- Release Manager / SRE — deployment readiness and execution authority
- Security/Compliance reviewer — only when required by scope/risk

The skill may name these roles as required reviewers. It must not invent the person currently filling a role.
