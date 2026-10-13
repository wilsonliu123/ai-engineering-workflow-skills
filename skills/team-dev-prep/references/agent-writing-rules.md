# Agent-Readable Writing Rules

These rules adapt agent-oriented writing practices to this team's development workflow.

## Write for execution

- Start with the outcome and boundaries.
- Use direct, concrete instructions.
- Prefer repository paths, IDs, commands, and observable results over vague prose.
- Keep requirements/design canonical upstream; use links and IDs rather than repeating large blocks.
- Put global context in `index.md`; put local context in the task brief.

## Preserve information type

Label or phrase content so the reader can distinguish:

- **Fact** — observed in code/config or stated in an authoritative document.
- **Approved decision** — already reviewed/accepted upstream.
- **Constraint** — must be obeyed.
- **Proposal** — not yet approved; must not be treated as instruction.
- **Open item** — unresolved and may block implementation.

## Make stopping conditions explicit

Coding AI should stop and request confirmation when implementation appears to require changing:

- an AC or externally visible behavior,
- an approved architecture boundary,
- a public interface/schema,
- security/authorization policy,
- data ownership or retention rules,
- migration/compatibility guarantees,
- scope beyond the current task.

## Optimize context without losing safety

Do not shorten away behavior-critical constraints. Remove duplication, not meaning.

A good task brief lets a fresh AI session answer:

1. What am I changing?
2. Why am I changing it?
3. Which sources are authoritative?
4. What must I not change?
5. How do I prove completion?
6. When must I stop and ask?
