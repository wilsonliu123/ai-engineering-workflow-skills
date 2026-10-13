# Design method

Use this method to turn requirements into a repository-grounded design without copying any external workflow verbatim.

## Start from evidence

Inspect the requirement and relevant code before asking questions. Prefer facts that can be established from the repository over questions to humans.

## Find the decisions that matter

Focus clarification on choices that materially change one or more of:

- externally observable behavior,
- data ownership or persistence,
- authorization/security boundary,
- cross-service or frontend/backend contract,
- compatibility or migration strategy,
- reliability, performance, cost, or operational burden,
- difficult-to-reverse architecture.

Do not burden reviewers with low-value questions that can be resolved by established project patterns.

## Explore alternatives proportionally

For a reversible, low-risk choice, recommend one option and explain the assumption.

For a difficult-to-reverse or high-impact choice:

1. state the decision question,
2. identify constraints and known facts,
3. present the preferred option,
4. give at least one viable alternative,
5. compare meaningful trade-offs,
6. record what still requires human confirmation.

## Preserve uncertainty

Use explicit labels: Fact, Decision, Proposal, Assumption, Open question. Never erase uncertainty merely to make the design look complete.

## Keep the design implementable

A design should define boundaries, responsibilities, contracts, data and runtime behavior sufficiently for the next stage to create implementation tasks, while avoiding line-by-line coding instructions.
