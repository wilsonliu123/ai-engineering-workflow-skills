# Team Review Method

Use this method to keep review systematic, source-grounded, and useful to both humans and downstream AI.

## 1. Establish the exact change set first

Do not start with generic code-quality commentary. First identify the actual change under review.

Prefer, in order:

1. explicit PR or base/head commit range,
2. explicit list of commits/files supplied by the user,
3. local working tree plus staged/untracked changes when the implementation is still local.

For local review, consider these surfaces as applicable:

- committed delta against the chosen base,
- staged changes,
- unstaged changes,
- new untracked implementation files.

Never say “the change is clean” when part of the change was not visible.

## 2. Build three independent review lenses

### A. Standards lens

Ask: does the implementation comply with repository/team engineering rules and remain maintainable?

Use actual standards/configuration/repository patterns. Avoid personal-preference findings.

### B. Requirement lens

Ask: does the implementation satisfy the approved requirement and AC without omitting or expanding observable behavior?

Trace observations to `REQ-*` and `AC-*` where possible.

### C. Design lens

Ask: does the implementation respect the approved SAD, interface/data/security constraints, module boundaries, and key design decisions?

A well-written implementation can still be wrong if it implements the wrong contract or violates the architecture.

## 3. Distinguish evidence types

Use explicit evidence labels when useful:

- **Static evidence** — source code, configuration, schemas, tests inspected.
- **Execution evidence** — test/build/CI command results tied to a revision/environment.
- **External evidence** — logs, screenshots, security scan, benchmark, or other supplied artifact.
- **Unavailable evidence** — required proof is missing or inaccessible.

Do not infer execution from the mere existence of a test file.

## 4. Prefer behavior and risk over style

A useful finding explains a concrete defect, non-conformance, or material risk. Focus on:

- correctness,
- security/authorization,
- data integrity,
- architecture/interface conformance,
- failure handling,
- compatibility/migration,
- test adequacy,
- maintainability where there is a concrete cost/risk.

Avoid low-value churn over formatting that an automated formatter owns or stylistic alternatives with no team rule or meaningful impact.

## 5. Handle upstream conflicts explicitly

When code, requirement, design, and current repository behavior conflict:

1. identify the conflicting sources,
2. state which source is authoritative for the questioned behavior,
3. record the mismatch,
4. route it to the appropriate owner if the authority is unclear.

Do not rewrite the review into “pass” by changing the interpretation of the requirement or design.

## 6. Review tests as claims, not decoration

For changed behavior, ask:

- What behavior does the test actually prove?
- Does it derive expected output independently, or merely mirror implementation logic?
- Does it cover the relevant permission/error/boundary path?
- Can it pass while the AC is still broken?
- Is the test deterministic and isolated enough to be trusted?

A test can exist and still be weak evidence.

## 7. Keep conclusions scoped

Use wording that reflects evidence:

- “No blocker found in the reviewed diff” is scoped.
- “The feature is correct” is usually too broad for static review.
- “CI run X passed on commit Y” is evidence-based.
- “Tests pass” is unsupported if no run was observed.

The final human reviewer decides approval/merge according to team policy.
