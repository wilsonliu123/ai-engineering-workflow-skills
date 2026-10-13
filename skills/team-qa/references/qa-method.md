# QA Method

## Lifecycle model

1. Requirements testability review.
2. Test planning from approved Requirements + Design.
3. Human execution assignment: QA humans choose automated/manual/hybrid/deferred one case at a time.
4. Actual execution by existing QA agents/tools/humans outside `team-qa`.
5. Evidence normalization by `team-qa-evidence`.
6. Final assessment by `team-qa`.

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
