# Clarification method

Use this method to turn vague requests into explicit decisions without making the user do research the agent can do itself.

## 1. Classify uncertainty

For each unclear point, classify it as one of:

- **Fact**: discoverable from the repo, existing docs, source, configuration, or available tools. Investigate it yourself.
- **Decision**: requires product/business/technical ownership. Ask the user or the designated role.
- **Assumption**: a temporary working belief. State it explicitly; do not upgrade it to fact.
- **Conflict**: two authoritative inputs disagree. Surface the conflict and request a decision on which source governs.

## 2. Ask decisions in dependency order

Represent decisions as a dependency tree. A question is ready only when the decisions it depends on are settled.

In each round:

1. Ask all currently ready, material questions together.
2. Keep the number manageable; combine tightly related questions.
3. For each question, include a recommended answer and the consequence of the main alternatives.
4. Do not ask for facts that can be discovered from the repo or tools.
5. After answers arrive, recompute what is now ready.

Stop asking when remaining uncertainty is non-blocking and can be recorded as an open question, or when all material decisions are settled.

## 3. What counts as material

Treat an unresolved point as material when it can change any of:

- user-visible behavior or business rules;
- authorization, data access, privacy, audit, or security boundaries;
- required inputs/outputs or integration contracts;
- acceptance criteria;
- data ownership or lifecycle;
- non-functional targets that affect design;
- in-scope versus out-of-scope work.

## 4. Do not collapse uncertainty

Avoid these failure modes:

- choosing a "reasonable default" for a business rule without confirmation;
- writing a target such as latency, capacity, or retention with no source;
- treating existing buggy behavior as the requirement;
- turning an implementation idea into a product requirement unless confirmed;
- asking the user to inspect code or documentation the agent can inspect.
