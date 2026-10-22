# Common Artifact Governance

All lifecycle artifacts must use a consistent governance model.

## Canonical metadata

Primary stage artifacts should include YAML frontmatter with these fields when applicable:

```yaml
artifact_id: <stable-id>
artifact_type: <requirements|requirements-testability|design|test-spec|qa-execution-matrix|qa-execution-run|dev-prep|dev-handoff|code-review|qa-report|release-handoff>
feature: <feature-slug>
artifact_status: <draft|complete|superseded>
review_status: <not-started|in-progress|changes-requested|approved|rejected>   # only these five; there is no `in-review`
validity: <current|stale|unknown>
risk_level: <low|medium|high|critical>
owner_role: <role>
reviewer_roles:
  - <role>
source_commit: <git-sha-or-unknown>
approval_reference: <session path or external record, or null>
approved_by_role: <role that approved; set only with approval>
delegation_reference: <who delegated approval authority, or null>
independent_reviewer_role: <required when approved_by_role == owner_role>
approved_sha256: <written by check_upstream_gate.py --stamp-approved>
upstream: <written by check_upstream_gate.py --record>
proceed_at_risk: <optional: human reference allowing work before upstream approval>
```

Initial values for every new artifact: `artifact_status: draft`, `review_status: not-started`, `validity: current`. Set `artifact_status: complete` when content is review-ready; `review_status` stays `not-started` until a human review starts (`in-progress`).

Use `artifact_type` only (no `document_type`); always include `feature`.

Do not copy hashes into other frontmatter fields (e.g. `requirements_approved_sha256`): `approved_sha256` (own content) and `upstream:` (written by `--record`) are the only hash fields; validators BLOCK any other `*_sha256` key.

Do not use a single `status` field as both completion state and human approval state.

## Shared engineering standards

`docs/shared/engineering-standards.md` is a governed upstream, not an exempt input. It carries the same canonical frontmatter (`artifact_type: engineering-standards`, `owner_role: Tech Lead`, `review_status`, `validity`), is approved once at repository level by an independent reviewer, and is `--record`ed like any other upstream. Editing it after approval makes every recording downstream STALE until re-recorded.

Requirements are upstream of UX and of the engineering standards, so in a requirements document `ux_reference:` and `engineering_standards:` are references only: never `--record` them there, and the gate does not treat them as declared upstreams.

## Approval gate

A downstream stage may enter its authoritative execution mode only when every required upstream package satisfies:

- `artifact_status: complete`
- `review_status: approved`
- `validity: current`

If any required upstream package fails this gate, stop authoritative downstream work. You may perform non-authoritative impact analysis or draft preparation only when clearly labeled as such.

## Approval authority

AI may recommend, summarize, ask questions, and record a human decision. AI must never invent approval.

Only update `review_status` to `approved`, `rejected`, or `changes-requested` after an explicit human decision or an authoritative external approval record is provided. Record the approval reference when available.

Structured approval (enforced by `check_upstream_gate.py` and every stage validator):

- `approved_by_role` names the approving role; `delegation_reference` records delegated authority (e.g. "delegated by <owner> in <channel/date>").
- Author ≠ approver: if `approved_by_role` equals `owner_role`, an `independent_reviewer_role` (a different role that actually reviewed, e.g. QA) is required and must be disclosed in QA report and release handoff.
- Prior authorization (e.g. "QA decides execution types") counts as an external approval record: cite it in `approval_reference` / `decision_source`.

## No human available

When a decision needs a human and none is reachable, do not choose silently: record it as an Assumption plus a Blocking OQ naming the decision owner, keep `artifact_status: draft` if the OQ is Blocking, and ask for confirmation at review. Any `Blocking: Yes` open question ⇒ `artifact_status: draft`.

## Proceed at risk

A human may allow downstream work to start before an upstream is approved by adding `proceed_at_risk: <reference>` to the upstream (which must be `complete` + `current`). The gate then reports `AT-RISK` and passes; a downstream artifact cannot be approved while any upstream is still at risk (gate BLOCKs). Record the risk in the downstream risks section.

## Commits in a shared repository

Each stage commits only its own output paths (`git add <paths>`, never `git add -A`). Configure a repo-local git identity once (`git config user.name/user.email`) during dev-prep environment setup.

## Artifact language

All generated markdown lifecycle artifacts (e.g. `docs/requirements/`, `docs/ux/`, `docs/design/`, `docs/dev-prep/`, `docs/dev-delivery/`, `docs/review/`, `docs/qa/`, `docs/release/`, and `docs/workflow/reviews/`) must have their descriptive prose, explanations, acceptance criteria details, and notes written in Traditional Chinese (繁體中文) by default.
Preserve canonical template headings (or `<Canonical Heading> — <中文標題>`), YAML frontmatter keys, technical identifiers, code syntax, schema fields, commit SHAs, URLs, and stable IDs (e.g. `REQ-*`, `AC-*`, `S01`, `TC-*`, `DEF-*`, `ADR-*`) in their standard technical/English format to maintain automated validator compatibility and end-to-end traceability.

## Package-level gate

When a stage produces multiple files, one primary artifact controls the package gate:

- Requirements: `docs/requirements/<feature-slug>.md`
- Design: `docs/design/<feature-slug>/sad.md`
- Dev prep: `docs/dev-prep/<feature-slug>/index.md`
- Dev handoff: `docs/dev-delivery/<feature-slug>/dev-delivery.md`
- Code review: `docs/review/<feature-slug>/code-review.md`
- QA planning: `docs/qa/<feature-slug>/test-spec.md`
- QA result: `docs/qa/<feature-slug>/qa-report.md`
- Release: `docs/release/<feature-slug>/release-handoff.md`
- UX (wireframes, screenshots-of-record, UI acceptance): `docs/ux/<feature-slug>/` — referenced by requirements/design `ux_reference`; gated as a directory.

Supporting artifacts with their own frontmatter (e.g. `requirements-testability.md`, `test-cases.md`) record their upstreams with `--record` so they go stale too. `requirements-testability.md` needs no separate human approval; its conclusion feeds the requirements review. Scope added to requirements during or after review re-triggers it (the gate reports STALE).

Supporting files cannot independently authorize downstream execution.

## Baseline and stale detection

Content baselines are machine-maintained by `scripts/check_upstream_gate.py` (byte-identical copy in every gated skill). Do not hand-write hashes.

- On approval of any primary artifact, run `check_upstream_gate.py --stamp-approved <artifact>`; it writes `approved_sha256`. If content later changes while still `approved`, the gate reports `STALE`.
- When a downstream stage starts authoritative work, run `check_upstream_gate.py --record <downstream.md> <upstream ...>`; it writes `upstream: [{path, sha256}]` into the downstream frontmatter.
- Every later gate run uses `--downstream <downstream.md> <upstream ...>`; any content change reports `STALE` and exits non-zero.
- The first stage (requirements) has no upstream, so it never runs `--record`/`--downstream`. Its baseline is `approved_sha256` written by `--stamp-approved`; use `--hash <artifact>` only to print the current hash for a review note or to compare by hand.
- Hashes ignore YAML frontmatter. `sad.md` and dev-prep `index.md` hash their whole package directory (attachments included). A directory argument (e.g. wireframes and UI acceptance under `docs/ux/<feature-slug>/`) is hashed as a whole, so UX artifacts are covered without extra fields.

An `approved` artifact without `approved_sha256` is reported `BLOCK`. Migration from v0.4: after confirming the approval still applies to the current content, run `--stamp-approved` once per approved document.

Directory packages (e.g. `docs/ux/<feature-slug>/`) carry governance in `<dir>/README.md` frontmatter (`artifact_status`, `review_status`, `validity`, approval fields). `--stamp-approved <dir>` hashes the whole directory (README frontmatter excluded) and writes `approved_sha256` into the README. A directory without README.md, or a missing directory, BLOCKs.

Upstream not created yet: `--record <downstream> <missing-upstream>` stores `sha256: pending`. Every gate run then reports `PENDING` and exits non-zero (never PASS) until `--record` is re-run after the upstream exists and is approved. Re-running `--record` adds or updates entries and keeps the others.

Upstream not approved yet: `--record` stores its hash with `status: at-risk`, prints `AT-RISK` and exits non-zero; other upstreams are recorded normally. The gate keeps reporting `AT-RISK` (or `STALE` if it changed) until it is approved, stamped and re-recorded. Rejected, superseded or stale upstreams still BLOCK.

- Same-stage co-review order (e.g. test-spec and test-cases reviewed together): approve and `--stamp-approved` the upstream first, re-run `--record` on the downstream, then approve the downstream.

Declared upstreams: in `--record` / `--downstream` mode the gate reads the downstream's frontmatter keys `requirements`, `design`, `test_spec`, `test_cases`, `test_plan`, `execution_matrix`, `review`, `dev_prep`, `dev_delivery`, `ux`, `ux_reference`, `source_requirements` and BLOCKs if a declared path is neither passed on the command line nor recorded. `--downstream` always re-checks every recorded upstream too.

On `STALE`: preserve the historical approval, set the downstream artifact `validity: stale`, assess impact, re-review as required, then re-run `--record` (and `--stamp-approved` after re-approval). This replaces hand-written "approval carried forward" notes; a purely editorial upstream change still needs one re-record, noted in one line in the downstream change log.

Never attach an old approval to changed content silently.

## Changes requested loop

When review requests content changes:

1. Set the reviewed artifact `review_status: changes-requested`.
2. Return content modification to the owning stage skill.
3. Update the artifact and its source baseline.
4. Re-enter document review.
5. Do not let `team-doc-review` rewrite substantive source content on behalf of the owning stage skill.

## Severity vocabulary

Findings and defects (code review, QA defects, release blockers) use one scale: `blocker` (unsafe to proceed), `major` (must fix or explicitly accept before release), `minor`, `note`. `risk_level` (`low|medium|high|critical`) is a separate artifact-level field and is never used for findings.

## Risk propagation

Requirements establish the initial risk level. Downstream stages may raise risk when new technical, security, data, migration, compatibility, or operational concerns are discovered. Do not lower a previously approved risk level without explicit human confirmation.

Higher risk must result in deeper human review and stronger evidence expectations.

## Running bundled scripts

Scripts live in the skill directory; run them from the repository root with the skill path, e.g. `python3 <skill-dir>/scripts/check_upstream_gate.py ...`. Paths in arguments and recorded baselines are repository-relative.

## Migrating v0.4 artifacts

1. Add `approved_by_role` / `delegation_reference` (and `independent_reviewer_role` where the author approved) to approved artifacts, from the existing review sessions.
2. After confirming each approval still applies, run `--stamp-approved` once per approved artifact (incl. ADRs and design attachments' package).
3. Run `--record` for each downstream artifact; re-import evidence with `--requirements --test-plan --design`; regenerate `result_counts` with `validate_qa.py --emit-table`.

## Observability invariant

Workflow telemetry is non-authoritative and best-effort. Telemetry availability or failure must never change artifact content, `artifact_status`, `review_status`, `validity`, approval authority, or stage-gate decisions. Use `references/observability-protocol.md` for the event contract.


## QA evidence boundary

QA plans, execution assignments, actual test execution, normalized evidence, and final QA assessment are distinct lifecycle facts:

- `team-qa` defines what to test.
- QA humans choose automated/manual/hybrid/deferred execution.
- Existing QA tools/agents/humans perform actual tests.
- `team-qa-evidence` normalizes actual tool/manual results.
- `team-qa final-assessment` interprets normalized evidence.

Missing evidence must never be treated as pass.
