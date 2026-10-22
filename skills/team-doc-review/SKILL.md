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
3. Load or create `docs/workflow/reviews/<artifact-id>.review.md` using `references/review-session-template.md`. Before resuming changed content, set `review_session_status: stale` and identify which prior decisions need re-review; retain unaffected decisions. Keep the session stale while affected items remain unresolved. After re-review against the current content, record the new session baseline and return to `in-progress` before the final document decision.
4. Scan the artifact for both material problems and consequential proposals awaiting human acceptance. Apply the selection rules in `references/review-method.md`, rank by risk and dependency, and store concrete, answerable questions in the review session. Separate verified facts and minor observations from decisions; reuse applicable recorded human decisions.
5. State how many material decisions remain, then present the highest-priority unresolved question using the decision format in `references/review-method.md` (default `review_mode: one-at-a-time`). In `review_mode: batch` (approver is a delegate, or `risk_level: low`), or when the human explicitly asks to see all questions, show the ranked questions together. Each question must name the actual proposal, source location, consequence, recommendation and alternatives; a topic label or general request to review the document is insufficient.
6. Record the human item-level decision. In one-at-a-time mode continue one item at a time. Keep the remaining queue in the session unless batch mode or the human explicitly asks to see it.
7. Keep minor/informational observations for the final recap unless they combine into a material risk.
8. When all required items are resolved, present a short recap and ask for one final document-level decision: approve, request changes, or reject. If no material questions remain, explain why, citing applicable prior human decisions and summarizing the checks; then request final approval without inventing extra questions. A pending question or failed required gate prevents final approval.
9. After explicit final decision only, update the reviewed artifact's `review_status` and `approval_reference` when available. Do not change substantive sections. On approval, also run `check_upstream_gate.py --stamp-approved <artifact>` to record `approved_sha256`; also set `approved_by_role`, `delegation_reference`, and `independent_reviewer_role` when the approver is the author. When approving a design, update and stamp each ADR it cites in the same commit. Editorial fixes after approval: re-stamp after the human confirms they are editorial, and note it in the session's resolved items.
10. If changes are requested, route substantive edits to the owning stage skill. The review session records requested changes and remains traceable.
11. If interrupted, persist the current `review_session_status` and `current_item`; next invocation resumes from the next unresolved item.

## Risk-based depth
- low: ask only genuinely material decisions.
- medium: confirm meaningful scope, AC, architecture or evidence changes.
- high/critical: explicitly inspect security/authorization, sensitive data, migrations/data loss, backward compatibility/contracts, tenant isolation, financial impact, irreversible architecture and recovery when relevant.

## Metadata changes allowed
Review session files may store scan evidence, queued questions and progress before a human answers. Record decisions and update artifact review/governance metadata only from explicit human decisions, following step 9. This skill must not rewrite requirements, design, task, review finding, QA result, or release substance.

## Output behavior
- Default output language: Write review session files (`docs/workflow/reviews/*.review.md`) and interactive review questions in Traditional Chinese (繁體中文) by default.
In start/resume mode, when a material question remains, the response contains that concrete question, not only a scan summary, a session link, or a promise to ask later. Wait for the human's answer and keep `current_item` on it. Resume by showing the saved question with its context; for older session items without a `Question` field, reconstruct it from the source and saved evidence while preserving its ID and prior decisions. Repeat a resolved item only when its decision is affected by a changed baseline. Keep the remaining queue in the session unless batch mode or the human requests it. Decision synchronization uses the applicable explicit human record without inventing another question.

## Workflow observability (v0.3)

Read `references/observability-protocol.md` before execution. Start a telemetry session with `scripts/emit_event.py` when the skill begins, emit the stage-specific events defined by the protocol at meaningful lifecycle points, and attempt a `skill_completed` event before returning. Telemetry is best-effort and **must never block or change** artifact generation, validation, approval, gate, or handoff behavior. Never log prompt text, code/document content, secrets, customer data, or personal identities.
