---
name: team-next-step
description: Inspect the current feature's workflow evidence and recommend one concrete next action, its owning skill or role, and its completion condition.
---

# Team Next Step

Turn the current feature state into one actionable recommendation. Respond in the user's language (default: Traditional Chinese / 繁體中文).

## Scope

This is a manually invoked, read-only router for the team's engineering workflow. Inspect existing artifacts and evidence; leave files, approvals, baselines, and runtime state unchanged. Reading another skill for routing does not start that skill's workflow.

If the user's request also authorizes execution, finish the recommendation first, then continue through the selected skill or responsible role within that authorization. Otherwise, end after the recommendation.

## 1. Identify the work

Resolve the target repository, feature, and intended outcome from the request, conversation, and referenced artifacts. Reuse the existing feature slug. If several features are plausible and context does not select one, ask which feature; directory order or modification time is not a selection rule.

Read repository guidance and any current workflow summary as navigation aids. Verify the summary against its underlying artifacts. Inspect only the selected feature and its shared dependencies.

Find the existing team skills through the available skill catalog or sibling directories in this bundle. Read `team-requirements/references/common-artifact-governance.md` from the resolved skill location for the authoritative governance rules. Load a destination's `SKILL.md` and relevant references after narrowing the candidate action; apply its entry gates and ownership rules together with common governance, which takes precedence over older stage wording and the routing map below. If a required skill or source is unavailable, state the missing dependency and keep that part of the recommendation conditional.

For a QA-related action, read `team-qa/SKILL.md` and its `references/qa-method.md` for the mode and handoff rules; for result intake, also read `team-qa-evidence/SKILL.md`. Keep detailed QA procedures in those owning skills.

**Done:** one feature and outcome are identified, or one question names the ambiguity that prevents a reliable choice.

## 2. Establish the state

Start with the primary artifacts in the routing map, their governance metadata, and their links. Then read the relevant content, upstream sources, review sessions under `docs/workflow/reviews/`, implementation identity, and evidence needed to distinguish the candidate actions.

Check completion (`artifact_status`), human decision (`review_status`), and freshness (`validity` and content baselines) separately. File presence, an approval label, or a previous chat summary alone does not establish readiness. Follow linked engineering standards, ADRs, and UX packages when they are required dependencies.

For stages requiring approved inputs, reuse `team-requirements/scripts/check_upstream_gate.py` from its resolved skill directory, running from the target repository root. Apply approval gates only where the destination requires them: `requirements-review` intentionally consumes reviewable requirements before human approval.

```text
python3 <team-requirements-dir>/scripts/check_upstream_gate.py <required-upstream> ...
python3 <team-requirements-dir>/scripts/check_upstream_gate.py --downstream <existing-artifact> <required-upstream> ...
```

Use the first form to check required inputs for a stage that has no artifact yet. Use the second to check an existing artifact's recorded and declared dependencies, also supplying required inputs named by the destination skill. Check the candidate artifact itself when its approval is needed downstream. These are inspection forms; `--record` and `--stamp-approved` belong to the owning stage or approval workflow.

Check the full required input set, including governed shared artifacts even when a feature document does not record them. Before authoritative design, explicitly gate `docs/shared/engineering-standards.md` as well as Requirements and any other required upstream packages identified by the design skill. A PASS for Requirements alone clears only Requirements. A stage validator checking document structure or file existence does not establish approval of those inputs.

Treat `BLOCK`, `STALE`, and `PENDING` as reasons to resolve a dependency. Preserve a documented `AT-RISK` authorization only where the destination permits it; describe the action as at risk, and respect gates requiring clean approval. A zero exit code with `AT-RISK` is not ordinary approval.

For implementation, review, QA, and delivery, compare the actual candidate with the reviewed/tested identities. For QA, distinguish testability review, test planning, human execution assignment, a candidate-bound execution run, actual results, normalized evidence, and final assessment. Read evidence validity, case mapping, target identity, and baselines; JSON file presence does not prove coverage or a pass. Telemetry is navigation data, not authoritative progress.

When a check cannot run or evidence is missing, name the unverified fact. Resolve it with available read-only inspection; if it would change the selected action, recommend obtaining that fact before progression.

**Done:** the recommendation has concrete source evidence, verified prerequisites, and explicitly identified uncertainties.

## 3. Select the action

Choose the action that removes the current obstacle to the user's outcome. Resolve an unmet required input as the primary next action before recommending authoritative downstream work; honor the explicit AT-RISK exception above. Apply these rules before advancing through the map:

- **Content incomplete or changes requested:** return the substantive work to its owning stage. A rejected artifact needs the recorded rejection disposition or a scope decision before work resumes. `team-doc-review` records decisions; it does not rewrite source content.
- **Source changed or baseline stale:** if the upstream itself needs content revision or renewed approval, resolve that first with its owner. If the upstream is already valid but a downstream records its old baseline, select the first affected downstream owner for impact assessment and revalidation. Resume dependent work after the affected gates are valid. Retain historical approvals; the router does not refresh hashes or carry approval forward.
- **Content ready, decision pending:** recommend `team-doc-review` to start/resume review or synchronize an existing authoritative decision. Verify the review session still matches the content. Missing approval metadata or stamps require confirmation of the applicable decision, not new substantive work by default.
- **Requirements testability:** before final requirements approval or recommending design, verify a current, non-blocking `requirements-testability.md`, even if Requirements already says `approved`. Missing or stale testability goes to `team-qa requirements-review`; blocking testability findings go to `team-requirements`. When the result is already current and non-blocking, explain that requirement QA is complete and continue checking the remaining prerequisites. The testability result needs no separate human approval.
- **Execution decisions pending:** start or resume human QA assignment through `team-qa execution-assignment`, preserving current decisions and any saved session. Collect actual manual test results through `team-qa-evidence manual-interview`, resuming its saved session when present. Assignment and result intake are distinct from document approval.
- **Unresolved findings:** route implementation defects to RD/Coding AI, requirement changes to `team-requirements`, and design changes to `team-design`. Resolve evidence problems through QA evidence intake. A complete or approved review/QA report can still contain blocking failures; inspect its findings and any authorized disposition before recommending progression.
- **Shared dependency not ready:** an existing file with missing governance, pending approval, or a stale baseline still needs resolution. Route missing content or unknown content readiness to its owner, such as the Tech Lead for engineering standards or the UX owner (`team-ux`) for a UX package. Route complete/current content awaiting approval to `team-doc-review`, including decision synchronization when authoritative approval evidence already exists. Name the blocking artifact, its unresolved state, and the check that will clear it; reuse the existing artifact and owner.
- **Explicit draft-only request:** when the user specifically wants early draft preparation despite an unmet gate, recommend only that non-authoritative work. Keep the blocked prerequisite visible and retain `draft` scope in both the invocation prompt and completion condition. Formal completion, review readiness, and downstream handoff remain gated.

### Routing map

Paths below are relative to the target repository; `<feature>` is the selected slug. Each row assumes its required predecessors are usable. Use the destination skill to verify the exact gate.

| Observed need | Evidence / artifact | Recommended owner or action |
|---|---|---|
| Clarify or revise scope and acceptance criteria | `docs/requirements/<feature>.md` | `team-requirements` |
| Check a reviewable requirement's testability | `docs/qa/<feature>/requirements-testability.md` | `team-qa requirements-review` |
| Start/resume document review or synchronize a decision | Primary artifact and matching review session | `team-doc-review` |
| Standardize or bridge UI/UX after approved requirements (features with UI) | `docs/ux/<feature>/README.md` | `team-ux` |
| Design after current requirement testability clearance and all required governance gates | `docs/design/<feature>/sad.md` | `team-design` |
| Plan verification from approved requirements and design | `docs/qa/<feature>/test-spec.md`, `test-cases.md` | `team-qa test-planning` |
| Prepare coding tasks from approved requirements, design, and Test Spec | `docs/dev-prep/<feature>/index.md` | `team-dev-prep` |
| Implement an eligible task or fix an implementation finding | Dev-prep task brief, actual changes, task evidence | RD/Coding AI using the `team-dev-prep` coding contract; no separate coding skill |
| Record completed implementation and real build/test evidence | `docs/dev-delivery/<feature>/dev-delivery.md` | `team-dev-handoff` |
| Review the implementation matching an approved handoff | `docs/review/<feature>/code-review.md` | `team-review` |
| Record human choices for test execution | `docs/qa/<feature>/execution-matrix.md` | `team-qa execution-assignment` |
| Bind a review-ready candidate to an assigned QA run | `docs/qa/<feature>/execution-run.md` | `team-qa execution-handoff` |
| Execute assigned cases with no actual results yet | Execution run, matrix, case expectations | Assigned QA role or toolchain; neither `team-qa` nor `team-qa-evidence` executes tests |
| Normalize existing tool reports or collect actual human results | `.ai-workflow/evidence/<feature>/normalized/`, raw reports, manual session | `team-qa-evidence tool-import` or `manual-interview` |
| Assess results against the tested candidate and current baselines | Normalized evidence and `docs/qa/<feature>/qa-report.md` | `team-qa final-assessment` |
| Reconcile delivery after required review/QA gates and findings are resolved | `docs/release/<feature>/release-handoff.md` and candidate identity | `team-release` |

QA assignment can proceed after planning while development continues. It is required before the affected QA execution, not before dev-prep or coding. Prefer the next unmet dependency on the requested work's path; mention an independent parallel action only when it helps that outcome.

If results exist but are unimported, recommend intake rather than rerunning tests. If evidence is incomplete, identify whether execution, intake, mapping, or revalidation is missing; an explicitly requested final assessment may report missing coverage without claiming success. A changed candidate requires impact assessment and affected review/testing before old evidence can support delivery.

If all work required for the stated outcome is evidenced and complete, say so. Release-document approval proves approval of that record; actual deployment or recipient acceptance requires its own evidence when included in the user's goal. Recommend the responsible release role for that external action only when it is in scope.

**Done:** one primary action addresses the actual blocker or next usable stage, with the correct skill mode or external role. If the outcome is complete, the evidence supports completion instead.

## 4. Return the recommendation

Keep the normal answer to these five items, translated into the user's language:

- **Current state:** feature and verified progress, including any blocker or uncertainty.
- **Evidence:** the decisive file/section, review record, version, or check result.
- **Next action:** one concrete action with the owning skill and explicit mode, or responsible role.
- **Inputs:** exact existing paths/identities and a ready-to-use prompt for the selected skill or AI-assisted preparation.
- **Done when:** the observable result that completes this action, including any remaining approval gate.

For QA-related recommendations, use the five items above to name the relevant completed step and the first missing or stale step, explain why it is needed, and name its exact mode or external executor. Say whether the next action prepares documents, records a human decision, executes tests, imports results, or assesses evidence. Explain any parallel timing that affects the requested work; execution assignment pending does not itself block coding. Describe only the relevant handoff, using verified evidence, rather than listing the whole QA lifecycle.

When document preparation belongs to a role with no dedicated team skill, explain that distinction and name what AI can prepare versus what a human must decide. Give a copyable general AI prompt instead of ending at “ask the Tech Lead.” Adapt this example to the user's language, actual repository, artifact, and verified gaps:

> Prepare `<artifact-path>` for review in `<repository>`. Check its applicability against repository facts, fill verifiable content and governance fields, and list decisions needing a human. Keep unresolved blocking questions in draft and report whether the document is ready for `team-doc-review`. Do not approve it or start downstream work.

This prompt prepares the source document; `team-doc-review` handles its human review once ready. Providing the prompt remains part of the read-only recommendation and does not execute it.

Carry the same gate limits through the next action, its invocation prompt, and its completion condition. For prerequisite resolution, describe completion of that prerequisite; for an explicit early draft, describe a draft. A blocker stated in the evidence cannot become permission for formal downstream completion elsewhere in the response.

When one missing decision prevents selection, ask that question in place of a guessed next action. When the outcome is complete, replace the action with “No remaining action for this outcome” and cite the completion evidence. List other blockers only if they affect the recommendation; a full workflow audit is outside the default response.

**Done:** the user can take the next action without reconstructing context, and no state or approval was changed by this routing pass.
