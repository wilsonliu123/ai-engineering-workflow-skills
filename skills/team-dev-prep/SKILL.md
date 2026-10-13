---
name: team-dev-prep
description: Prepare the team's standardized AI-coding handoff package from approved requirements, approved design, an approved QA test specification, repository state, and shared engineering standards. Use after solution design is approved and before RD/AI coding begins. This skill creates documentation only under docs/dev-prep/, decomposes work into small verifiable tasks, gives each task an agent-readable context brief with explicit scope, source documents, acceptance criteria, constraints, validation requirements, and stop/escalation conditions, and never writes product code, changes tests, approves its own package, merges, or deploys.
---

# Team Development Preparation

## Lifecycle governance (v0.4, authoritative)

Read `references/common-artifact-governance.md` before creating or updating lifecycle artifacts. If older wording in this skill conflicts with that reference, the v0.4 governance reference takes precedence.

Use `artifact_status`, `review_status`, `validity`, `risk_level`, source baselines, and package-level gates. Do not use a single `status` field for both completion and human approval.

After producing a complete artifact package, immediately enter the review protocol in `references/review-protocol.md` when the user is present. Ask one material question at a time. Do not batch all decisions into one message. If the review is interrupted, persist the review session under `docs/workflow/reviews/` so `team-doc-review` can resume later. Only an explicit human decision or authoritative external record may change `review_status` to `approved`, `changes-requested`, or `rejected`.

Turn approved requirements and design into a small, explicit, repository-grounded task package that an RD or Coding AI can execute without relying on hidden conversation context.

## Hard boundaries

- Write documentation only. Do not modify product code, tests, migrations, infrastructure, build configuration, runtime configuration, or generated application artifacts.
- Do not reinterpret or silently relax approved requirements, AC, architecture decisions, interface contracts, security rules, or engineering standards.
- Do not invent missing implementation decisions when they would change externally observable behavior, architecture, data ownership, security, compatibility, or acceptance criteria.
- Do not mark the package `approved`. The skill may set `artifact_status: complete` when content is complete, but human `review_status` remains `not-started`/`in-progress` until an explicit human decision.
- Do not begin implementation. This skill ends when the development-preparation package is ready for human review.

## Repository contract

Use repository-relative paths.

### Primary inputs

1. `docs/requirements/<feature-slug>.md` — reviewed/approved requirement and AC source of truth.
2. `docs/design/<feature-slug>/sad.md` — approved design index.
3. `docs/qa/<feature-slug>/test-spec.md` — approved/current QA verification contract.
4. `docs/qa/<feature-slug>/test-cases.md` — planned test coverage; execution assignment may still be pending.
5. Applicable design attachments under `docs/design/<feature-slug>/` such as `frontend.md`, `backend.md`, and `interface.md`.
6. Relevant source code, tests, schemas, configuration, and repository conventions needed to identify the actual implementation surface.
7. `docs/shared/engineering-standards.md` — canonical repository-wide engineering rules and required input for a package intended to be ready for coding.
8. `docs/shared/glossary.md`, `docs/shared/adr/`, and other approved shared references when relevant.

If the required requirement, design package, or QA test specification is missing, not approved according to the team's normal review mechanism, or contains a critical unresolved question that changes implementation scope, report the gap and do not produce a handoff marked ready for coding.

If `docs/shared/engineering-standards.md` is missing, you may draft the task package, but it MUST remain blocked from coding handoff. Do not infer repository-wide coding, architecture, security, testing, or AI-execution rules from personal preference.

### Standard outputs

Create or update:

`docs/dev-prep/<feature-slug>/`

Always create:

- `index.md` — authoritative development-preparation index, context map, task dependency map, and handoff status.
- `tasks/` — one Markdown task brief per independently executable task.

Use stable task identifiers such as `TASK-001`, `TASK-002`, and keep them stable when wording changes. Do not renumber existing tasks merely because ordering changes.

Do not create duplicate copies of requirements or design documents. Link to the canonical sources.

## Workflow

### 1. Resolve the feature identity and approved baselines

Reuse the same `<feature-slug>` used by requirements and design. Record the exact requirement path, design path, and repository baseline commit when available.

Establish whether the inputs are sufficiently approved for implementation preparation. If approval evidence is outside the files, reference the PR/issue/review location when available instead of fabricating approval metadata.

### 2. Read the full upstream context before decomposing

Read:

- the canonical engineering standards at `docs/shared/engineering-standards.md`,
- the approved QA test specification and planned test cases,
- scope and non-scope,
- REQ and AC identifiers,
- approved design decisions and constraints,
- changed interfaces and data models,
- security/permission rules,
- migration/compatibility requirements,
- validation expectations,
- planned QA coverage and any non-functional test thresholds from the approved Test Spec,
- open questions and known risks,
- relevant implementation patterns already present in the repository.

Inspect the repository to identify actual code/test entry points. Do not guess file paths or modules when the repository can answer the question.

### 3. Build an implementation map

Use `references/dev-prep-method.md`.

Break the change into the smallest practical, independently verifiable slices. Prefer vertical slices that produce observable progress over layer-only batches when the feature permits it.

For each candidate task, determine:

- which REQ/AC it advances,
- which approved design constraints it must honor,
- affected modules or interfaces,
- upstream task dependencies,
- what can be verified when the task is complete,
- what must not be changed by the task.

Avoid tasks that are merely vague activities such as “implement backend” or “finish frontend”. A task should have a bounded outcome and a verification path.

### 4. Separate global context from task-local context

Put repository-wide and feature-wide context in `index.md`. Put only task-specific instructions in each task brief.

Do not paste the same requirement/design prose into every task. Reference canonical documents and quote only small, behavior-critical fragments when necessary for unambiguous execution.

Use progressive disclosure: each task should tell the Coding AI what it must read before acting and what it may ignore unless a condition occurs.

### 5. Write `index.md`

Follow `references/index-template.md`.

The index must include:

- source documents and repository baseline,
- package status and role-based ownership,
- feature scope summary and non-scope,
- implementation surfaces identified in the repo,
- global invariants that every task must obey,
- task list and dependency graph/order,
- shared validation commands or locations when known,
- unresolved items and blockers,
- handoff/readiness summary.

The index is a map, not an encyclopedia.

### 6. Write one task brief per executable slice

Follow `references/task-template.md`.

Every task must state:

- stable task ID and concise outcome,
- linked REQ/AC identifiers,
- exact upstream documents/sections that are authoritative,
- repository areas to inspect first,
- in-scope and explicitly out-of-scope changes,
- approved constraints and interface/data expectations,
- required tests/checks/evidence,
- stop-and-escalate conditions,
- completion artifacts expected from the RD/Coding AI.

If a task depends on another task, name the dependency explicitly rather than relying on document order.

### 7. Optimize the package for Coding AI consumption

Use the principles in `references/agent-writing-rules.md`.

Ensure:

- instructions are concrete and action-oriented,
- authoritative sources are linked, not duplicated,
- facts, approved decisions, proposals, and unknowns are not mixed,
- task scope is narrow enough to reason about,
- completion criteria are observable,
- conditions requiring human confirmation are explicit,
- no task implies permission to change requirements/design merely to make coding easier.

### 8. Validate traceability and package integrity

Before handoff, verify:

- every task maps to at least one REQ/AC or explicitly states why it is enabling work,
- every in-scope AC is covered by one or more tasks or explicitly deferred by approved scope,
- task dependencies do not form a cycle,
- task briefs do not contradict SAD/interface definitions,
- security, migration, compatibility, and data constraints are represented where applicable,
- validation requirements are actionable and not merely “test it”,
- unresolved blockers are visible rather than hidden in prose.

Run:

`scripts/validate_dev_prep.py docs/dev-prep/<feature-slug> docs/requirements/<feature-slug>.md docs/design/<feature-slug>/sad.md`

when execution is available.

### 9. Finish in `review` state

Set the package primary artifact to `artifact_status: complete` when complete enough for human review; otherwise `artifact_status: draft`. Set `review_status: not-started` before review begins.

Summarize for the reviewer:

- task count and dependency structure,
- uncovered or deferred ACs,
- major implementation risks,
- unresolved blockers,
- documents created/updated,
- whether the package is ready to hand to RD/AI Coding after human approval.

Do not self-record human approval.

## Status and metadata rules

Git history is the primary version history. Do not auto-increment document versions for routine edits.

Use role-based responsibility, for example:

- `owner_role: Tech Lead`
- `reviewer_role: Senior Engineer`

Record concrete people in the actual PR/issue/review system when available, not as mandatory template data.

When possible, record the repository baseline commit and the exact source document paths. If unknown, use `unknown` rather than inventing values.

## Source-of-truth rules

- Requirement intent and AC remain canonical in `docs/requirements/`.
- Architecture, frontend/backend design, and interface decisions remain canonical in `docs/design/`.
- `docs/shared/engineering-standards.md` is the canonical repository-wide engineering standards source. Repository-native tool configuration remains authoritative for machine-enforced details referenced by that standard.
- `docs/dev-prep/<feature-slug>/index.md` is the authoritative index for implementation preparation, but it must reference upstream truth rather than redefine it.
- Task briefs define execution scope for the current task; they do not override requirements or design.

## Handoff to the coding stage

After human approval, RD/Coding AI should begin from `docs/dev-prep/<feature-slug>/index.md`, select an approved task, then read the task brief and its referenced authoritative sources before modifying code.

The coding stage may use any team-approved AI coding tool or personal workflow. The standardized contract is the task package and its upstream sources, not the individual's prompting style.

## Bundled resources

- `references/dev-prep-method.md` — decomposition and dependency method.
- `references/index-template.md` — development-preparation index layout.
- `references/task-template.md` — Coding AI task brief layout.
- `references/agent-writing-rules.md` — concise agent-readable writing rules adapted to this team's workflow.
- `scripts/validate_dev_prep.py` — package structure and traceability checks.

## v0.2 preflight gate
Require both Requirements and Design primary artifacts to be `complete + approved + current`. Record exact requirement and design baselines in `index.md`. If either gate fails or an applicable ADR is not approved/current, do not produce a coding-ready package. Draft analysis may be created only as non-authoritative.

The Dev Prep package is coding-authoritative only when its own `artifact_status: complete`, `review_status: approved`, and `validity: current`. Coding tasks are supporting files; approval is package-level through `index.md`.

## Deterministic upstream gate check
When execution is available, run `scripts/check_upstream_gate.py` against every required upstream primary artifact before authoritative downstream work. A non-zero exit blocks authoritative progression. Do not override the script result by interpretation; resolve the upstream governance state first.

## Workflow observability (v0.4)

Read `references/observability-protocol.md` before execution. Start a telemetry session with `scripts/emit_event.py` when the skill begins, emit the stage-specific events defined by the protocol at meaningful lifecycle points, and attempt a `skill_completed` event before returning. Telemetry is best-effort and **must never block or change** artifact generation, validation, approval, gate, or handoff behavior. Never log prompt text, code/document content, secrets, customer data, or personal identities.
