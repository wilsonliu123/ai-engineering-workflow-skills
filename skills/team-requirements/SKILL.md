---
name: team-requirements
description: Create or update the team's standardized software requirements specification in a repository. Use when a product idea, change request, bug-driven requirement, or loosely described feature must be clarified, normalized, reviewed, and written to docs/requirements/{feature-slug}.md before architecture/design work begins. The skill reads existing repo context, identifies missing facts and decisions, interviews the user only for decisions that cannot be derived, and produces an AI-readable and human-reviewable requirements document. It does not modify product code, approve requirements, or start implementation.
---

# Team Requirements

## Lifecycle governance (v0.4, authoritative)

Read `references/common-artifact-governance.md` before creating or updating lifecycle artifacts. If older wording in this skill conflicts with that reference, the v0.4 governance reference takes precedence.

Use `artifact_status`, `review_status`, `validity`, `risk_level`, source baselines, and package-level gates. Do not use a single `status` field for both completion and human approval.

After producing a complete Requirements/AC draft, hand it first to `team-qa` in `requirements-review` mode for a testability check before final human Requirements approval. If that QA review finds blocking testability gaps, route them back into this skill and revise the Requirements source; do not proceed to final approval. When the testability review has no blocking gap, enter the normal review protocol in `references/review-protocol.md`. Ask one material human decision at a time. If interrupted, persist the review session under `docs/workflow/reviews/` so `team-doc-review` can resume later. Only an explicit human decision or authoritative external record may change `review_status` to `approved`, `changes-requested`, or `rejected`.

Turn an incomplete request into one authoritative requirements document that both humans and downstream agents can rely on.

## Workflow observability (v0.4)

Read `references/observability-protocol.md` before execution. Start a telemetry session with `scripts/emit_event.py` when the skill begins, emit the stage-specific events defined by the protocol at meaningful lifecycle points, and attempt a `skill_completed` event before returning. Telemetry is best-effort and **must never block or change** artifact generation, validation, approval, gate, or handoff behavior. Never log prompt text, code/document content, secrets, customer data, or personal identities.

## Operating boundary

- Write only requirements documentation. Do not modify product code, tests, build files, deployment configuration, or runtime state.
- Default output path: `docs/requirements/{feature-slug}.md`.
- Update the existing requirement file for the same feature instead of creating version-suffixed copies.
- Let Git provide history. Do not increment a document version on every edit.
- Record responsibility by role, not by a person's name. Actual reviewer identity belongs in the repo's review/PR/issue history.
- Never invent approval, execution evidence, business rules, performance targets, permissions, or product decisions.

## Workflow

1. **Locate context**
   - Find the repo root and inspect `AGENTS.md` or equivalent repo guidance if present.
   - Read `docs/shared/engineering-standards.md` when present. Treat it as the canonical repository-wide engineering rules; `docs/shared/engineering-standards.zh-TW.md` is a translation unless the repository explicitly declares otherwise.
   - Inspect existing requirements, glossary/domain docs, relevant architecture docs, and relevant code when they can answer factual questions.
   - If the canonical engineering standards file is missing, record the governance gap. This alone does not block requirements discovery, but do not invent repository-wide engineering rules.
   - Determine whether a requirements file for the same feature already exists.

2. **Build the decision map**
   - Separate what is already known into: facts, confirmed decisions, assumptions, and open decisions.
   - Resolve facts from the repository or available tools instead of asking the user.
   - For open decisions, identify dependency order: ask only questions whose prerequisites are already settled.
   - If many decisions depend on one another, work in rounds. In each round, ask the currently answerable decisions together and give a recommended answer with trade-offs.
   - Do not proceed past a critical unresolved decision by silently choosing an answer.
   - Follow `references/clarification-method.md` for the interview method.

3. **Draft or update the requirements document**
   - Use `references/requirements-template.md` as the required structure.
   - Preserve confirmed upstream facts and decisions; do not rewrite them into different meaning.
   - Use stable IDs for requirements and acceptance criteria. Reuse existing IDs when updating; do not renumber for cosmetic reasons.
   - Write observable outcomes, not implementation instructions, unless an implementation constraint is itself a confirmed requirement.
   - Prefer links/paths to authoritative upstream material over duplicated text.

4. **Check AI readability and consistency**
   - Make scope, constraints, forbidden behavior, open questions, and acceptance criteria explicit.
   - Remove explanatory filler that does not change downstream behavior.
   - Make every critical instruction discoverable from a clear heading or pointer.
   - Distinguish normative statements from examples and commentary.
   - Confirm that acceptance criteria are testable without reading the author's mind.

5. **Validate the artifact**
   - Run `scripts/validate_requirements.py <requirements-file>`.
   - Fix structural errors before presenting the result.
   - If critical decisions remain open, keep `artifact_status: draft` and list blockers.
   - If the document is complete enough for human review, set `review_status: in-progress`.
   - Never set `review_status: approved` unless the user explicitly confirms approval or an authoritative approval record is provided.

6. **QA testability gate, then human review**
   - Run/hand off to `team-qa requirements-review` against the reviewable Requirements/AC draft.
   - If QA identifies blocking ambiguity or missing measurable criteria, return to this skill and revise the Requirements source.
   - After the QA testability gate is clear, enter the normal human document review.
   - Do not start architecture/design or implementation.
   - The next stage may consume the document only after `artifact_status: complete`, `review_status: approved`, and `validity: current`.

## Update behavior

When modifying an existing requirements file:

- Treat it as the source of truth for that feature's requirements.
- Preserve stable `REQ-*` and `AC-*` identifiers unless the underlying requirement is deliberately removed.
- Mark removed behavior explicitly in the change summary; do not silently delete meaning that downstream docs may reference.
- Identify downstream artifacts that may be stale when an approved requirement changes, but do not edit those artifacts in this skill.

## Engineering-standards integration

- `docs/shared/engineering-standards.md` is the canonical shared engineering source when it exists.
- Use it to identify repository-wide non-functional constraints that materially affect the requirement, such as security, data isolation, compatibility, observability, testing evidence, or AI-governance rules.
- Do not copy generic engineering rules into the feature requirement unless the rule becomes a feature-specific product requirement. Reference the standard instead.
- If a human-approved feature requirement intentionally conflicts with a MUST rule, expose the conflict and required exception path; do not resolve it inside this skill.

## Required references

- Read `references/requirements-template.md` before writing the document.
- Read `references/clarification-method.md` when requirements are incomplete, ambiguous, contradictory, or large enough to require multiple decision rounds.

## Shared glossary lifecycle

Maintain `docs/shared/glossary.md` on demand. Create it when the first material domain term needs a canonical definition. Update it when requirements work discovers a new canonical term, alias, ambiguity, or changed domain meaning. Do not put requirements or implementation notes in the glossary. Reuse existing terms instead of creating synonyms.

Use `references/glossary-template.md`. Glossary updates are supporting shared context; they do not replace the requirements package gate.

## v0.3 completion and invalidation
When content is complete enough for review, set `artifact_status: complete`, `review_status: not-started`, and `validity: current`, then pass through the QA requirements-testability gate before final human review. When updating a previously approved requirement, compare its prior baseline and identify downstream Design, Dev Prep, Test Spec, and other artifacts whose recorded requirements baseline no longer matches. Report them as stale candidates; do not silently preserve downstream readiness.
