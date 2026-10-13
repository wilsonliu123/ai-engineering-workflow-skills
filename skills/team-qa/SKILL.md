---
name: team-qa
description: "Run the team's standardized QA workflow across explicit lifecycle modes: requirements-review checks requirement/AC testability, test-planning creates traceable functional and non-functional test plans/cases, execution-assignment records human QA decisions about automated/manual/hybrid/deferred execution, execution-handoff binds a candidate build to the planned run, and final-assessment turns normalized execution evidence into a QA report. Use across the lifecycle, not only at final QA. This skill never executes tests, chooses automation on behalf of humans, invents evidence, rewrites upstream requirements/design, or self-approves release."
---

# Team QA

## Purpose

Keep QA involved from requirement testability through final evidence-based assessment while preserving strict boundaries between planning, human execution choice, actual execution, evidence normalization, and final QA judgment.

Read `references/common-artifact-governance.md`, `references/qa-method.md`, and `references/observability-protocol.md` when applicable.

## Hard boundaries

- Do not execute product tests in this skill.
- Do not decide whether a test case is automated, manual, hybrid, or deferred. A human QA/QA Lead makes that choice.
- Do not claim pass/fail without normalized execution evidence tied to the test case and tested target.
- Do not turn test existence, code inspection, or expected behavior into execution evidence.
- Do not rewrite Requirements, AC, Design, Engineering Standards, code, CI, or release decisions.
- Do not self-approve Requirements, QA, or Release.
- Do not treat performance testing as identical to stress testing. Stress is one subtype of performance testing.

## Repository contract

Primary sources:

- `docs/requirements/<feature-slug>.md`
- `docs/design/<feature-slug>/sad.md` and relevant design attachments
- `docs/shared/engineering-standards.md`
- `docs/dev-prep/<feature-slug>/` when available
- `docs/dev-delivery/<feature-slug>/dev-delivery.md` when available
- `docs/review/<feature-slug>/code-review.md` for final QA
- `.ai-workflow/evidence/<feature-slug>/normalized/` for final evidence

Outputs under `docs/qa/<feature-slug>/`:

- `requirements-testability.md` — requirements-review mode
- `test-spec.md` — test-planning mode
- `test-cases.md` — test-planning mode
- `execution-matrix.md` — execution-assignment mode; human decisions only
- `execution-run.md` — execution-handoff snapshot for a candidate build
- `qa-report.md` — final-assessment mode

## Explicit modes

Prefer explicit invocation:

- `requirements-review`
- `test-planning`
- `execution-assignment`
- `execution-handoff`
- `final-assessment`

If no mode is supplied, infer only when repository state is unambiguous; otherwise ask which mode is intended.

## Mode 1 — requirements-review

Run after `team-requirements` produces a reviewable Requirements/AC draft and before final human Requirements approval.

Goal: determine whether Requirements and AC can later be objectively verified.

1. Read Requirements, AC, scope, exceptions, and relevant glossary terms.
2. Identify testability gaps only: ambiguous outcomes, missing boundaries, undefined error behavior, missing measurable non-functional criteria, or contradictory AC.
3. Do not write full test cases yet.
4. Produce `requirements-testability.md` using `references/requirements-testability-template.md`.
5. If blocking gaps exist, route them back to `team-requirements`; do not silently fix the source document.

This review informs the human Requirements decision. It does not approve Requirements.

## Mode 2 — test-planning

Run after Requirements and Design are approved/current and before development preparation is finalized.

Goal: define what must be tested and what evidence would prove it, without deciding the execution method.

1. Read approved/current Requirements/AC, Design, interfaces, and Engineering Standards.
2. Build risk-based coverage for every in-scope AC.
3. Produce `test-spec.md` and `test-cases.md`.
4. Include functional and non-functional verification where Requirements, Design, Engineering Standards, or risk justify it.
5. For performance-related cases distinguish, when applicable: performance baseline, load, stress, spike, soak/endurance, scalability/capacity.
6. Never invent load targets, latency limits, availability goals, or other thresholds. If missing, create an open question.
7. Do not choose manual/automated/hybrid execution. Mark each planned case `human-assignment-pending`.

Use `references/test-spec-template.md` and `references/test-cases-template.md`.

## Mode 3 — execution-assignment

Run after test planning and before QA execution begins. It may happen before development completes so QA can prepare tools/environments.

Goal: record the human QA team's decision for every planned test case:

- `automated`
- `manual`
- `hybrid`
- `deferred`

AI must not recommend or select the execution type. Present one test case at a time with its objective, expected result, test class/subtype, and constraints, then ask the human QA/QA Lead to choose.

For automated/hybrid cases, ask the human to record the intended tool/method if known. For deferred cases, require a human-provided reason/reference.

Persist interrupted progress under `docs/workflow/reviews/<feature-slug>-qa-execution-assignment.md` and resume from the next undecided case.

Produce/update `execution-matrix.md` using `references/execution-matrix-template.md`.

## Mode 4 — execution-handoff

Run when a real candidate build/commit/environment is ready for QA execution.

Produce `execution-run.md` containing:

- candidate build/commit/component versions,
- environment,
- approved/current test plan baseline,
- execution matrix baseline,
- in-scope cases for this run,
- known blockers/limitations.

This is an execution contract, not execution evidence.

## Actual QA execution — outside team-qa

The real QA tester or toolchain executes assigned cases using existing team practice, including AI agents + Playwright/other automation tools, API/integration tools, performance/load/stress tools, or manual human testing.

Raw reports/observations go to `team-qa-evidence`:

- `tool-import` for machine-generated reports,
- `manual-interview` for human test results.

`team-qa-evidence` normalizes the results into `.ai-workflow/evidence/<feature>/normalized/`.

## Mode 5 — final-assessment

Run after execution has occurred and normalized evidence is available.

1. Resolve candidate build/commit/environment and approved/current upstream baselines.
2. Read `test-cases.md`, `execution-matrix.md`, normalized evidence, and relevant review/dev-handoff documents.
3. Map each in-scope TC to evidence.
4. Allowed states: `pass`, `fail`, `blocked`, `not-executed`, `not-applicable`, and `waived` only with explicit human authorization/reference.
5. Missing evidence is `not-executed` / `evidence-missing`, never pass.
6. For performance cases compare observed metrics only against approved thresholds recorded in Requirements/Test Cases.
7. Produce/update `qa-report.md` using `references/qa-report-template.md`.
8. Enter the human document review protocol. Human QA acceptance/release decisions remain human-owned.

## Test taxonomy

Functional examples: feature behavior, API/contract, integration, permission/authorization, validation/boundary, error/recovery, regression.

Non-functional examples: performance baseline, load, stress, spike, soak/endurance, scalability/capacity, reliability/resilience, security, accessibility, compatibility, observability.

Not every feature requires every non-functional category.

## Source-of-truth and gates

- Requirements/AC remain authoritative in `docs/requirements/`.
- Design remains authoritative in `docs/design/`.
- QA plans define verification intent, not product behavior.
- Execution Matrix records human execution decisions, not AI recommendations.
- Normalized evidence is observation data, not release approval.
- QA Report is the canonical evidence summary for the tested candidate.

`team-dev-prep` is not ready for coding until approved/current Requirements, approved/current Design, and approved/current QA Test Spec exist. The execution matrix should be substantially assigned before QA execution; any unassigned in-scope case is a visible planning gap.

## Validation

When execution is available run `scripts/validate_qa.py docs/qa/<feature-slug>`.

Before authoritative planning or final assessment, run `scripts/check_upstream_gate.py --downstream docs/qa/<feature-slug>/test-spec.md docs/requirements/<feature-slug>.md docs/design/<feature-slug>/sad.md` (use `--record` when the test spec is first created). `BLOCK` or `STALE` (non-zero exit) stops authoritative QA work.

## Observability

Emit best-effort events such as `qa_requirements_reviewed`, `qa_plan_completed`, `qa_execution_assigned`, `qa_execution_handoff_created`, and `qa_final_assessment_completed`. Telemetry failure must never affect QA artifacts or decisions.

## Bundled resources

- `references/qa-method.md`
- `references/requirements-testability-template.md`
- `references/test-spec-template.md`
- `references/test-cases-template.md`
- `references/execution-matrix-template.md`
- `references/execution-run-template.md`
- `references/qa-report-template.md`
- `scripts/validate_qa.py`
- `scripts/check_upstream_gate.py`
