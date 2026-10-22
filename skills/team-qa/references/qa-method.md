# QA Method

## Lifecycle model

1. `requirements-review`: Requirements testability review.
2. `test-planning`: test planning from approved Requirements + Design.
3. `execution-assignment`: human QA chooses automated/manual/hybrid/deferred after planning and before execution; this preparation may run in parallel with development.
4. `execution-handoff`: bind the candidate build/commit/environment to the planned run.
5. Actual execution by existing QA agents/tools/humans outside `team-qa`.
6. Evidence normalization by `team-qa-evidence`.
7. `final-assessment`: assessment by `team-qa` from normalized execution evidence.

## Principles

- Trace from approved REQ/AC, not from implementation behavior.
- Separate what should be tested from how humans choose to execute it.
- Separate execution from evidence normalization and final assessment.
- A test case is a plan; evidence proves execution.
- Missing evidence is not executed, never pass.
- Preserve tested build/commit/environment identity.
- Humans own execution-mode choice and final acceptance.
- Performance testing is broad; stress testing is one subtype.

## Performance planning

Create performance/load/stress/spike/soak/scalability cases only when approved requirements, design constraints, engineering standards, or documented risk justify them. Never invent numeric thresholds. If a meaningful threshold is missing, raise an open question instead of creating a false oracle.

## Flaky results

If the same TC passes and fails on the same commit, rerun it up to two more times in a clean state. Record every run. If results still disagree, report `blocked` with note `flaky`, void none of them, and raise a defect; `validate_qa.py` rejects unresolved pass+fail conflicts on one commit. A TC is never `pass` on a "best of N" basis.

## Partial execution

Use `partial` when only part of a TC's steps/paths was observed (e.g. happy path seen, failure path not run). `partial` never counts as pass; state what was not observed in `notes`.

## Interaction states

Hover, focus, tooltip and similar states are not provable by static screenshots. Prefer an automated E2E assertion on the state; otherwise attach a short recording (`--attachment`). Code inspection alone is `not-executed`.

## Test data and isolation

Use realistic fixtures (display names, non-ASCII text, long strings) so UI defects are not fixture artifacts. For E2E with a single shared server, isolate by unique per-test data (prefix/UUID) instead of a fresh database per case; say so in test-spec §4. Ensure server logs (e.g. Playwright `webServer` stdout) reach CI output so startup warnings can be evidenced.

## Test naming

The importer maps tests by regex `\bTC-\d{3,}\b` in the test or class name (e.g. Go subtest `t.Run("TC-017/rejects-empty", ...)`; `TestTC017_x` does not match). QA owns TC IDs and sub-test names; record the exact automated test name per TC in test-cases (`Automated test name`) before coding starts. Review findings that need a regression test are mapped to a TC by QA.
