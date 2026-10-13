---
name: team-doc-review
description: Run or resume a human-in-the-loop review of standardized engineering artifacts. Use when a requirements, design, dev-prep, dev-handoff, code-review, QA, or release document needs human approval, when a review was interrupted, or when an external approval/rejection must be synchronized. This skill scans the artifact and authoritative upstream sources, asks one material decision at a time, persists resumable review state under docs/workflow/reviews/, and may update only governance metadata after explicit human decisions; it never self-approves or rewrites substantive source content.
---
# Team Document Review

Reduce human review load by surfacing only high-value decisions and material risks, one at a time.

## Governance
Read `references/common-artifact-governance.md` and `references/review-method.md`.

## Review modes
- **Start/resume review:** inspect artifact, upstream sources, standards, baseline and any existing review session.
- **Decision synchronization:** when an explicit human decision or authoritative external record already exists, update governance metadata to reflect it.

## Workflow
1. Identify the primary artifact and verify its `artifact_status`. If not `complete`, do not approve it; report that source work is incomplete.
2. Load authoritative upstream artifacts and exact baselines. If required upstream gates are invalid/stale, surface that before document approval.
3. Load or create `docs/workflow/reviews/<artifact-id>.review.md` using `references/review-session-template.md`.
4. Scan all issues internally, rank by risk and dependency, and store the queue in the review session.
5. Present exactly one unresolved critical/material item to the human (default `review_mode: one-at-a-time`). In `review_mode: batch` (approver is a delegate, or `risk_level: low`) present the full ranked list once and record all decisions together. Include topic, evidence, why judgment is needed, options/trade-offs, and recommendation when useful.
6. Record the human item-level decision. In one-at-a-time mode continue one item at a time. Do not dump the remaining queue unless explicitly asked.
7. Keep minor/informational observations for the final recap unless they combine into a material risk.
8. When all required items are resolved, present a short recap and ask for one final document-level decision: approve, request changes, or reject.
9. After explicit final decision only, update the reviewed artifact's `review_status` and `approval_reference` when available. Do not change substantive sections. On approval, also run `check_upstream_gate.py --stamp-approved <artifact>` to record `approved_sha256`; also set `approved_by_role`, `delegation_reference`, and `independent_reviewer_role` when the approver is the author. When approving a design, update and stamp each ADR it cites in the same commit. Editorial fixes after approval: re-stamp after the human confirms they are editorial, and note it in the session's resolved items.
10. If changes are requested, route substantive edits to the owning stage skill. The review session records requested changes and remains traceable.
11. If interrupted, persist `review_status: in-progress` and `current_item`; next invocation resumes from the next unresolved item.
12. If the artifact changed since the session baseline, mark the session stale, determine which prior decisions are affected, and re-review those items before final approval.

## Risk-based depth
- low: ask only genuinely material decisions.
- medium: confirm meaningful scope, AC, architecture or evidence changes.
- high/critical: explicitly inspect security/authorization, sensitive data, migrations/data loss, backward compatibility/contracts, tenant isolation, financial impact, irreversible architecture and recovery when relevant.

## Metadata changes allowed
This skill may update only review/governance metadata and review session files after explicit decisions: `review_status`, `approval_reference`, and review session decision records. It must not rewrite requirements, design, task, review finding, QA result, or release substance.

## Output behavior
The normal response is interactive and contains one question at a time. Do not generate a long approval report unless the human asks for one.

## Workflow observability (v0.3)

Read `references/observability-protocol.md` before execution. Start a telemetry session with `scripts/emit_event.py` when the skill begins, emit the stage-specific events defined by the protocol at meaningful lifecycle points, and attempt a `skill_completed` event before returning. Telemetry is best-effort and **must never block or change** artifact generation, validation, approval, gate, or handoff behavior. Never log prompt text, code/document content, secrets, customer data, or personal identities.
