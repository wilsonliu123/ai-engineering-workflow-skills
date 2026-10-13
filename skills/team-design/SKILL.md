---
name: team-design
description: Create or update the team's standardized system design documents from an reviewed requirements document and the current repository. Use for solution architecture, SAD creation, frontend/backend design, interface design, or preparing an approved requirement for the development-preparation stage. This skill writes design documentation only under docs/design/, inspects the codebase and shared engineering standards, preserves REQ/AC traceability, records assumptions and unresolved decisions explicitly, and never modifies product code, approves its own design, merges, or deploys.
---

# Team Design

## Lifecycle governance (v0.4, authoritative)

Read `references/common-artifact-governance.md` before creating or updating lifecycle artifacts. If older wording in this skill conflicts with that reference, the v0.4 governance reference takes precedence.

Use `artifact_status`, `review_status`, `validity`, `risk_level`, source baselines, and package-level gates. Do not use a single `status` field for both completion and human approval.

After producing a complete artifact package, immediately enter the review protocol in `references/review-protocol.md` when the user is present. Ask one material question at a time. Do not batch all decisions into one message. If the review is interrupted, persist the review session under `docs/workflow/reviews/` so `team-doc-review` can resume later. Only an explicit human decision or authoritative external record may change `review_status` to `approved`, `changes-requested`, or `rejected`.

Turn a reviewed requirement into a repository-grounded design package that humans can review and the next AI/RD stage can consume without relying on hidden chat context.

## Hard boundaries

- Write documentation only. Do not modify product code, tests, infrastructure, migrations, generated application artifacts, or runtime configuration.
- Do not silently invent business rules, security policy, performance targets, data-retention rules, or externally visible behavior.
- Do not mark a design `approved`. The skill may set `artifact_status: complete` when content is complete, but human `review_status` remains `not-started`/`in-progress` until an explicit human decision.
- Do not treat a prototype, proposed interface, or inferred code behavior as an approved requirement.
- When a critical decision is unresolved, document it and stop the affected part of the design rather than guessing.

## Repository contract

Use repository-relative paths.

### Primary inputs

1. `docs/requirements/<feature-slug>.md` — reviewed requirement and AC source of truth.
2. Existing source code, tests, configuration, schemas, and architecture material relevant to the change.
3. `docs/shared/engineering-standards.md` — canonical repository-wide engineering rules. This is required for a design intended for downstream handoff.
4. `docs/shared/glossary.md` and `docs/shared/adr/` when present and relevant.
5. Existing interface specifications or design documents that the change extends.

If the requirement file is missing, has unresolved critical decisions, or is not human-approved and current, report the gap before producing a design intended for handoff.

If `docs/shared/engineering-standards.md` is missing, you may produce a `draft` design to expose assumptions, but do not mark the package ready for development preparation. Record the missing standards as a blocker. Do not substitute the Traditional Chinese translation as a second source of truth unless the repository explicitly declares it canonical.

### Standard outputs

Create a directory:

`docs/design/<feature-slug>/`

Always create or update:

- `sad.md` — system/solution architecture design and authoritative design index.

Create only when applicable:

- `frontend.md` — user-flow, state, component, and frontend technical design.
- `backend.md` — service/module, data, reliability, and backend technical design.
- `interface.md` — cross-boundary API/event/interface contract or a pointer to the canonical machine-readable specification.

Do not create empty placeholder files for non-applicable areas. `sad.md` must state which optional documents apply.

UX artifacts (wireframes, copy deck, UI acceptance) live in `docs/ux/<feature-slug>/` and are referenced by `ux_reference`; they are produced by the UX role, not by this skill. Gate them as a directory (`check_upstream_gate.py --record docs/design/<slug>/sad.md docs/requirements/<slug>.md docs/ux/<slug>`).

## Workflow

### 1. Resolve the work item

Determine `<feature-slug>` from the referenced requirement. Reuse the same stable feature identity across requirement and design paths. Never create a second feature identity merely because wording changed.

### 2. Read before designing

Read the complete requirement, especially scope, non-scope, REQ IDs, AC IDs, risks, assumptions, and open questions. Then read `docs/shared/engineering-standards.md` and inspect the relevant repository areas before proposing architecture.

Establish:

- current system behavior and boundaries,
- affected modules and interfaces,
- reusable patterns already present in the repository,
- architectural constraints and standards,
- likely migration/compatibility impact,
- places where the requirement and current system conflict.

Prefer extending proven repository patterns over inventing parallel abstractions unless a documented reason justifies the change.

### 3. Separate facts, decisions, proposals, and unknowns

Classify design-relevant information as:

- **Fact** — directly supported by requirement, code, config, or approved documentation.
- **Decision** — already approved and traceable to a source.
- **Proposal** — design recommendation awaiting review.
- **Open question** — missing information that changes architecture or observable behavior.

Never rewrite a proposal or inference as a fact.

### 4. Clarify only material decisions

Use the method in `references/design-method.md` when choices are unclear. Investigate facts from the repo first; ask humans only for decisions that cannot be derived safely.

For a material design choice, present the preferred option with trade-offs and at least one realistic alternative when the choice is difficult to reverse or meaningfully affects cost, security, data, compatibility, or user behavior.

### 5. Write `sad.md`

Follow `references/sad-template.md`.

The SAD must:

- cite the requirement source and repository baseline,
- map the proposed design to REQ/AC identifiers,
- describe system boundaries, component responsibilities, main runtime flows, data and interface impact,
- identify security, observability, migration, compatibility, rollback/forward-fix implications,
- record proposals, accepted decisions, assumptions, and unresolved questions separately,
- link all applicable frontend/backend/interface documents,
- define design-level validation points for later review/QA.

Keep the SAD focused on this change. For an existing system, reference stable existing architecture instead of re-documenting the whole platform.

### 6. Write specialized design documents when applicable

- For user-facing or client-side changes, use `references/frontend-template.md`.
- For service, data, integration, or server-side changes, use `references/backend-template.md`.
- For a changed cross-boundary interface, use `references/interface-template.md` or reference the canonical OpenAPI/AsyncAPI/schema file if the repo already maintains one.

Each specialized document must link back to `sad.md` and the relevant REQ/AC IDs.

### 7. Check traceability and consistency

Before handoff, verify:

- every in-scope REQ has a design response or an explicit reason it requires no design change,
- important ACs are supported by observable design behavior,
- frontend and backend assumptions agree on shared interfaces,
- interface fields, errors, permissions, and state transitions are not defined differently in multiple documents,
- non-functional requirements have corresponding design treatment or are clearly marked unresolved,
- no open question is hidden inside confident prose.

Run `scripts/validate_design.py docs/design/<feature-slug> docs/requirements/<feature-slug>.md` when execution is available.

### 8. Finish in `review` state

Set `artifact_status: complete` when the package is complete; otherwise use `artifact_status: draft`. Set `review_status: not-started` before review begins.

Summarize for the human reviewer:

- major design decisions/proposals,
- deviations from existing architecture,
- unresolved blockers,
- documents created/updated,
- whether the package is ready for `team-qa test-planning` after human approval.

Do not self-record human approval.

## Status and metadata rules

Git history is the primary version history. Do not auto-increment document versions for routine edits.

Use role-based responsibility in document metadata, for example:

- `owner_role: Tech Lead`
- `reviewer_role: Architect`

Record concrete people only in the actual PR/issue/review system when available.

When possible, record the repository baseline commit and the exact requirement path used. If unavailable, use `unknown` rather than inventing a value.

## Source-of-truth rules

- Requirement intent and AC live in `docs/requirements/`; design documents reference them rather than duplicating them in full.
- `sad.md` is the authoritative index for this feature's design package.
- Shared interface definitions have one canonical source. Do not maintain divergent frontend and backend copies.
- `docs/shared/engineering-standards.md` remains canonical for repository-wide engineering rules; this skill may cite it but must not fork generic rules into feature documents.

## Handoff to the next stage

`team-qa` in `test-planning` mode is the next mandatory lifecycle step after the required design documents are human-approved/current. It reads the approved Requirements + Design and produces the QA Test Spec/Test Cases before `team-dev-prep` is finalized.

`team-dev-prep` should then read the approved Requirements, approved Design, and approved/current QA Test Spec to create task-specific coding context. This skill does not generate implementation code or perform the development-preparation stage itself.

## Bundled resources

- `references/design-method.md` — repository-grounded design and clarification method.
- `references/sad-template.md` — SAD layout.
- `references/frontend-template.md` — frontend design layout.
- `references/backend-template.md` — backend design layout.
- `references/interface-template.md` — interface design layout.
- `scripts/validate_design.py` — structural and traceability checks for the design package.

## ADR lifecycle

Create an ADR under `docs/shared/adr/` only when a design decision is long-lived, cross-module, difficult to reverse, involves material trade-offs, changes a core boundary/contract, or requires an exception to engineering standards. Do not create ADRs for routine implementation choices.

Use `references/adr-template.md`. The SAD must reference applicable ADRs. ADRs require human review; an unapproved ADR cannot authorize downstream implementation. ADRs are approved together with the design: the design review session lists each ADR as an item, and the approval step updates and stamps the ADR (`review_status`, `approved_by_role`, `--stamp-approved`) in the same commit. `validate_design.py` fails when an approved SAD cites an unapproved ADR.

## Design change requested by code review

When `team-review` finds that the design must change, the reviewer records the finding only. The design owner updates the design through this skill (new commit), the design returns to `review_status: in-progress` → re-approval, and downstream artifacts go STALE through the gate; no hand-written carry-forward notes.

## v0.2 preflight gate
Before authoritative design work, require the Requirements primary artifact to satisfy `artifact_status: complete`, `review_status: approved`, `validity: current`. Record the exact requirements baseline commit/content identity. If the requirement is not approved/current, stop authoritative design; only non-authoritative impact analysis may proceed.

Propagate `risk_level` from Requirements and raise it if design introduces additional architecture, security, data, migration, compatibility, or operational risk. Do not lower it without explicit human confirmation. When an approved Design is later regenerated because upstream requirements changed, set downstream Dev Prep and affected QA planning to stale through impact reporting.

## Deterministic upstream gate check
When execution is available, run `scripts/check_upstream_gate.py --downstream <this-stage-primary> <upstream ...>` against every required upstream primary artifact (and `docs/ux/<feature-slug>/` when used) before authoritative downstream work; on first creation use `--record` instead, which gates and then writes the content baselines. A non-zero exit (`BLOCK` or `STALE`) blocks authoritative progression. Do not override the script result by interpretation; resolve the upstream governance state first.

## Workflow observability (v0.4)

Read `references/observability-protocol.md` before execution. Start a telemetry session with `scripts/emit_event.py` when the skill begins, emit the stage-specific events defined by the protocol at meaningful lifecycle points, and attempt a `skill_completed` event before returning. Telemetry is best-effort and **must never block or change** artifact generation, validation, approval, gate, or handoff behavior. Never log prompt text, code/document content, secrets, customer data, or personal identities.
