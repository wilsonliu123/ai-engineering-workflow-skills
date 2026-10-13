---
name: team-dev-handoff
description: Create the team's standardized post-coding development handoff from an approved development-preparation package, the actual implementation revision, and real build/test evidence. Use after RD/AI coding finishes and before formal code review. This skill writes documentation only under docs/dev-delivery/, records what was actually implemented, deviations from approved design, execution evidence, known limitations, and required developer/user/operations guide updates; it never changes product code or invents test/approval facts.
---
# Team Dev Handoff

Create a factual bridge between implementation and review/QA. Do not describe intended implementation as if it actually happened.

## Lifecycle governance (authoritative)
Read `references/common-artifact-governance.md`. The primary gate is `docs/dev-delivery/<feature-slug>/dev-delivery.md`.

Preflight requires `docs/dev-prep/<feature-slug>/index.md` with `artifact_status: complete`, `review_status: approved`, and `validity: current`. Also read approved requirements, design, `docs/shared/engineering-standards.md`, relevant ADRs/glossary, and the actual implementation change.

If the dev-prep gate fails, stop authoritative handoff creation. You may report the missing gate only.

## Output
Create/update:
- `docs/dev-delivery/<feature-slug>/dev-delivery.md` (required)
- `developer-guide.md` when developers/integrators need new operational knowledge
- `user-guide.md` when user-visible behavior changed
- `operations-guide.md` when deployment/config/migration/monitoring behavior changed

Do not create empty optional guides.

## Workflow
1. Resolve exact implementation identity: PR/commit/head, local change scope, build/artifact identity when available.
2. Compare actual changes to dev-prep tasks, requirements/AC, SAD/interfaces, ADRs, and engineering standards.
3. Record implemented scope, omitted/deferred scope, and any implementation/design deviation. Do not silently normalize deviations.
4. Record only commands/tests/checks actually executed, with result and revision/environment when known. “Test exists” is not “test passed.”
5. Identify documentation updates caused by the implementation and produce only those required guides.
6. Record known limitations, unresolved risks, migration/config changes, and areas code review/QA should emphasize.
7. Set `artifact_status: complete` when factual handoff is complete, otherwise `draft`; set `review_status: not-started`; set `validity: current` relative to the implementation baseline.
8. Validate with `scripts/validate_dev_handoff.py`.
9. Immediately enter the Human Review Protocol in `references/review-protocol.md`. Ask one material question at a time. If interrupted, persist session state under `docs/workflow/reviews/`.

## Stop conditions
Stop and ask for clarification when the implementation scope cannot be identified, the implementation revision conflicts with approved upstream baselines, or material deviations lack an authoritative decision.

## Boundaries
Do not edit source code, tests, CI, requirements, design, or dev-prep. If a content change is required upstream, record it and route back to the owning stage skill.

## Deterministic upstream gate check
When execution is available, run `scripts/check_upstream_gate.py` against every required upstream primary artifact before authoritative downstream work. A non-zero exit blocks authoritative progression. Do not override the script result by interpretation; resolve the upstream governance state first.

## Workflow observability (v0.3)

Read `references/observability-protocol.md` before execution. Start a telemetry session with `scripts/emit_event.py` when the skill begins, emit the stage-specific events defined by the protocol at meaningful lifecycle points, and attempt a `skill_completed` event before returning. Telemetry is best-effort and **must never block or change** artifact generation, validation, approval, gate, or handoff behavior. Never log prompt text, code/document content, secrets, customer data, or personal identities.
