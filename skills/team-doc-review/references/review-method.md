# Review Method

## Select decisions

Review both defects and proposed commitments: a complete, internally consistent artifact can still contain material decisions awaiting human acceptance. Compare its proposals with authoritative upstream sources and recorded human decisions. For each relevant area below, either identify an unresolved decision or cite the applicable decision that already covers it:

- Requirements: included/excluded scope, meaningful AC thresholds, user-visible behavior and unresolved product assumptions.
- Design: architecture and contract trade-offs, security/data boundaries, compatibility, migration/recovery and operating cost.
- Dev prep/handoff and code review: material deviations from approved scope/design, ownership or delivery constraints, unresolved findings and evidence limits.
- QA and release: coverage or evidence gaps, deferred testing, residual risk, rollout/rollback and acceptance conditions.

Use only areas relevant to the artifact. Rank blockers and irreversible/high-impact choices first, then scope/AC changes, risk acceptance and unresolved assumptions. Group duplicate issues that share one decision; split choices that can be answered independently. Formatting and wording stay in the recap unless they change meaning or combine into a material risk. An existing applicable human decision resolves an item; an AI-authored proposal does not.

Resolve factual questions from available sources and checks before asking the human. Report a deterministic failure as a finding with its required correction and owning stage; when human direction is needed, ask about the actual unresolved choice. Human preference cannot turn missing evidence into pass or override a required governance gate. Missing information must be named as unknown, with its decision owner or required evidence, rather than invented to make a question look complete.

Selection is complete when every identified material proposal/problem is either queued as a concrete question, covered by a cited human decision, or recorded as a blocker requiring correction/evidence. A clean validator result proves checked facts; it does not accept the document's consequential proposals. Never infer approval from silence or from “no issues found.”

## Present a decision

Use the human's language and include enough context to answer without reopening the whole document:

1. **Question (`REVQ-###`):** ask whether to accept a specific proposal or choose a specific unresolved direction. Name the actual behavior, threshold or trade-off; “Is the scope/architecture reasonable?” is too broad.
2. **Source and current proposal:** cite the artifact path and section/item, and quote or accurately summarize what it currently commits to. Separate missing facts from verified ones.
3. **Why this matters:** state the practical consequence and why human authority or judgment is needed.
4. **Recommendation and alternatives:** recommend one course with its reason and cost/risk; describe meaningful alternatives and their consequences. An alternative that changes scope/design must be labeled as requiring the owning stage to revise and re-review the artifact.

Show the unresolved count and the current question now, then wait. Record only the human's actual answer; accepting one item does not approve the whole document. If the human requests clarification, explain the same item without recording a decision or advancing the queue.

Example, only when grounded in the reviewed document: “`docs/requirements/cards.md`, AC-03, proposes a fixed limit of 20 cards per session with no setting. Do you accept this limit for the first version? Recommend accepting: it matches the stated short-session goal and limits implementation/testing cost. Requiring a configurable limit would add settings behavior and tests, so return that change to `team-requirements` before approval.” Adapt the facts to the actual artifact; this is a format example, not a required product policy.
