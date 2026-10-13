# Development Preparation Method

## Goal

Translate approved requirements and design into bounded execution units that can be handed to an RD or Coding AI without hidden context.

## Decomposition principles

1. **Start from outcomes, not layers.** Prefer tasks that complete a coherent behavior or verification slice. Use layer-only tasks when infrastructure, schema, or mechanical refactoring genuinely requires it.
2. **Keep batches small.** A task should be small enough that a reviewer can understand the intended change and its evidence without reconstructing the entire feature.
3. **Make dependencies explicit.** Record blocking tasks by stable task ID.
4. **Preserve source truth.** A task references REQ/AC and design constraints; it never rewrites them.
5. **Give every task a proof path.** State which tests, checks, observed behaviors, or artifacts demonstrate completion.
6. **Bound the change.** Name what the task may change and what it must not change.
7. **Escalate ambiguity.** If implementation requires changing an approved requirement, architecture boundary, public interface, security rule, or data contract, stop and request confirmation.

## Task readiness questions

Before writing a task, answer:

- What observable outcome is expected?
- Which REQ/AC does it serve?
- Which design decisions constrain it?
- Which repository areas must be inspected first?
- What dependencies must exist first?
- What changes are explicitly outside scope?
- How will the implementer prove completion?
- What discovery would require a human or upstream document change?

If these cannot be answered, keep the task in draft and record the gap.

## Dependency guidance

Use direct dependencies only. Avoid copying the entire upstream chain into every task.

Example:

- `TASK-001` — establish canonical backend authorization behavior.
- `TASK-002` — expose frontend action using the approved interface. Depends on `TASK-001` only if the interface cannot be implemented/tested independently.
- `TASK-003` — end-to-end regression coverage. Depends on the slices whose behavior it exercises.

Do not create artificial serial dependencies when tasks can safely proceed in parallel.
