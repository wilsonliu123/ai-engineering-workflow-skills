# Changelog

Source of issue IDs: TeamBoard validation report (`docs/skill-validation/VALIDATION-REPORT.md` in wilsonliu123/team-board) and the team notes: D-xx (full-stack), T-xx (Tech Leader), UX-xx / UX-Sxx (UI/UX), F-xx (PM), "QA: topic" (QA notes, unnumbered).

Rules followed: no new skills, no new workflow steps beyond what is listed, scripts shared across skills stay byte-identical.

## v0.5 — 2026-10-13

### P0-1 Gate compares upstream content (report §3, P0-1)
Fixes: D-15, T-01, D-05, D-07, D-18, T-05, F-18, F-20, UX-02, UX-07, UX-S11, QA: deliberate-injection / post-approval edits.
- `check_upstream_gate.py` (same file in team-design, team-dev-prep, team-dev-handoff, team-review, team-qa, team-release):
  - `--stamp-approved <artifact>` writes `approved_sha256`; later content changes under `approved` → `STALE`, exit 1.
  - `--record <downstream> <upstream...>` gates, then writes `upstream: [{path, sha256}]` into the downstream frontmatter.
  - `--downstream <downstream> <upstream...>` → `STALE`, exit 1 when any upstream changed; `BLOCK` when no baseline was recorded.
  - Hash ignores frontmatter; `sad.md` / `index.md` hash their whole package (attachments); a directory (e.g. `docs/ux/<slug>/`) is hashed as a unit, so wireframes and UI acceptance are covered without new fields.
  - Plain `check_upstream_gate.py <upstream...>` behaves as before (plus the `approved_sha256` check when that field is present).
- `common-artifact-governance.md` (all 9 copies): "Baseline and stale detection" now describes the script-maintained fields; it replaces hand-written approval carry-forward notes.
- SKILL.md gate instructions (5 gated skills plus team-qa) use `--record` / `--downstream`; team-doc-review stamps `approved_sha256` on approval.

### P0-2 Evidence bound to baseline, voidable (report §3, P0-2)
Fixes: QA: rev3 evidence void (EVD-024/025/009), QA: validate_qa does not check the evidence commit, root cause 3.
- `normalize_evidence.py` / `write_manual_evidence.py`: `--requirements` / `--test-plan` record `baseline.{requirements,test_plan}.{path,sha256}`; records carry `validity: current|void` and `void_reason`.
- New `void_evidence.py` (≈15 lines, team-qa-evidence) marks evidence void with a reason and an optional `superseded_by`.
- `validate_qa.py --evidence-dir <dir>` rejects: evidence whose baseline content changed, evidence without a baseline, void evidence without a reason, and `target.commit` ≠ `tested_commit`. Void records are kept and excluded. Without `--evidence-dir` it behaves as before.
- evidence-contract.md, team-qa-evidence SKILL.md, and team-qa Mode 5 updated.

### P2 (former P0-3) `decided_by`
Fixes: QA: execution-assignment (clarified by 剑秋 on 10/13: the design is correct).
- `execution-matrix-template.md` adds the `decided_by` field. `validate_qa.py` prints a WARN (not an error) when it is missing. The process is unchanged.

### P1/P2 small fixes
- Usage line on bad arguments for every validator (T-02, QA: validate_qa takes a directory; also states "run from repo root").
- `normalize_evidence.py`: EVD numbering continues after the highest existing file (QA: EVD numbering overwrites). Tests without a TC id get a WARN and a `dropped_without_tc` count (D-08, QA: regex drops tests silently). `executed_at` is `unknown` rather than the import time. The TC regex and Go naming are documented (D-16, T-06).
- Archiving to `evidence/<sha>/` is now a standard step in team-qa-evidence (UX-S10, UX-S16, QA: run2).
- qa-report template: §8.1 Known limitations, §8.2 Accepted non-defect issues (QA: final-assessment, QA: template).
- team-requirements glossary: list product terms that stay untranslated (UX-S14, D-10).

### Follow-up after QA / Tech Leader review
- `check_upstream_gate.py` (6 identical copies): an `approved` upstream without `approved_sha256` now reports `BLOCK` (exit 1) with a `--stamp-approved` hint, whether or not `--downstream` is used. **Migration:** run `--stamp-approved` once per approved v0.4 document; before that, every gate on v0.4 artifacts BLOCKs (intended).
- `validate_qa.py`: when `qa-report.md` exists, `--evidence-dir` is required. **Migration:** evidence without a baseline must be re-imported with `--requirements` / `--test-plan`.
- team-qa SKILL.md: also `--record` / `--downstream` the test-cases baseline; migration note added. The qa-report template and governance doc mention both rules.

## v0.6.2

- `check_upstream_gate.py --record`: an upstream that exists but is not approved (draft, in progress, or changes requested) is now recorded with its hash and `status: at-risk`. The script prints AT-RISK and exits 1. Pending handling is unchanged.
- Gate and `--downstream`:
  - An at-risk entry gives AT-RISK and exits 1.
  - If the at-risk upstream's hash has changed, the result is STALE.
  - Once the upstream is approved, the gate asks you to re-run `--record`, which clears the status.
- Rejected and void upstreams still BLOCK.
- Governance: when two artifacts of the same stage are reviewed together, approve and stamp the upstream first, then re-record the downstream, then approve the downstream.
- Tests: the QA test-cases case (skill-issues #7) is added to `knowbase-gate.sh`, and its log is in `docs/experiments/stale-gate.md`.

## v0.6.1 — 2026-10-13 (KnowBase live fixes, check_upstream_gate.py in all 9 copies)
- A directory upstream (e.g. `docs/ux/<slug>/`) now takes its governance from `<dir>/README.md` frontmatter. It is status- and approval-checked like a file. `--stamp-approved <dir>` writes into the README; the hash covers the whole directory and excludes the README frontmatter. A missing README or a missing directory BLOCKs. Previously a directory always passed the status check.
- `--record` on an upstream that does not exist yet stores `sha256: pending`. The gate reports `PENDING` and exits 1 until `--record` is re-run after the upstream exists. Re-running `--record` adds or updates entries.
- In `--record` / `--downstream` mode the gate reads the upstreams declared in the downstream frontmatter (`requirements`, `design`, `test_spec`, `test_cases`, `test_plan`, `execution_matrix`, `review`, `dev_prep`, `dev_delivery`, `ux`, `ux_reference`, `source_requirements`). It BLOCKs if a declared upstream is neither passed nor recorded. `--downstream` also re-checks every recorded upstream.
- Governance (all copies): documents the three rules above, and states that `review_status` has only five values (no `in-review`).
- Experiments: `knowbase-gate.sh` added; `migrate-teamboard.sh` adapted (UX README frontmatter, test-cases records test-spec).

## v0.6 — 2026-10-13: every deferred item fixed

Commits: `22612ad` core · `a8c6fc3` requirements · `04058f2` design · `674628e` dev-prep/handoff · `471bc5a` review/release · `791380e` QA+evidence · `36620ef` doc-review/telemetry · `8bb8d1e` fixes · `cdcf367` experiment. Earlier: `8bd5b73` P0-1 · `dbbd1b6` P0-2 · `68beea9` decided_by · `4a088c5` small fixes · `025dc0b` gate/QA blockers.

**Migration from v0.4:** see "Migrating v0.4 artifacts" in `common-artifact-governance.md`, and the worked example `docs/experiments/migrate-teamboard.sh`.

### Issue → commit

| Issue IDs | Fix | Commit |
|---|---|---|
| D-15, T-01, D-05, D-07, D-18, F-18, F-20, UX-02, UX-07, UX-S11, QA: injection experiment / post-approval edits (a4f891d, d322f75, 02f2daf, 3ca9aa4) | content-hash gate, package and UX-directory hashing, approved_sha256 | 8bd5b73, 025dc0b |
| QA: rev3 evidence void, QA: evidence commit check, report root cause 3 | evidence baseline + void; validate_qa rejects | dbbd1b6, 025dc0b |
| Hash unification, evidence binds sad.md, `--evidence-dir` usage | one content_hash() imported everywhere; design baseline | 22612ad |
| "approved but changed" in stage validators | approval_problems() in every validator | 22612ad |
| F-11, F-19, F-20, QA: delegated approver = author | approved_by_role, delegation_reference, independent_reviewer_role (enforced) | 22612ad, 8bb8d1e |
| D-04, D-14, QA: execution-handoff with unapproved upstream | proceed_at_risk + AT-RISK; downstream cannot be approved while at risk; pre-check runs | 22612ad, 674628e, 791380e |
| F-02, F-04, F-03, F-07, QA: unconfirmed assumptions | initial values; Blocking OQ ⇒ draft (validated); no-human rule; artifact_type only + feature | 22612ad, a8c6fc3 |
| F-15, F-16, F-17, QA: testability approval/stale | testability has no separate approval and goes stale via `--record`; scope changes re-trigger it | 22612ad |
| F-21, D-18 (shared worktree), T-08, QA: git identity | git add own paths; repo-local identity in env step | 22612ad, 674628e |
| F-22 | review_mode batch | 22612ad, 36620ef |
| P2-5, QA: duplication | kept per-skill (self-contained skills); `tools/check_shared.sh` drift guard; fixed pre-existing drift in team-dev-handoff review-protocol.md | 22612ad |
| F-05, F-14, QA: traceability consistency (G-03) | multi-Covers, unknown REQ, Must→AC, §13 == Covers | a8c6fc3 |
| F-06, F-10, F-12, F-13, UX-01, UX-06, QA: five situations / screen list | requirements template: screens, situation matrix (403/404), delivery constraints, decisions, ux_reference | a8c6fc3 |
| D-10, UX-S14 | copy canonical in requirements; glossary incl. untranslated terms | 4a088c5, a8c6fc3, 04058f2 |
| UX-03, UX-04, UX-05, UX-08 | frontend situation matrix + guidance; validate_design checks screens/matrix/attachments | 04058f2 |
| D-03 | ADR approved + stamped with design; validator | 04058f2, 36620ef |
| T-04 | design change from review routed to team-design | 04058f2, 471bc5a |
| D-01, D-06, D-13, F-00, F-01, QA: environment | dev-prep step 8a + index §5a (check only; make setup owned by first coding task) | 674628e |
| D-02 | validate_dev_prep: REQ citations, coverage, deps, cycles | 674628e |
| D-09 | dev-delivery `upstream:` incl. test plan and UX | 674628e |
| T-02 | usage messages | 4a088c5 |
| T-03 | validate_review: head_commit exists and no later code commits | 471bc5a |
| T-05 | carry-forward notes replaced by re-stamp/re-record; editorial rule | 22612ad, 36620ef |
| T-06, D-16 | QA owns TC IDs/sub-test names; `Automated test name` field; review maps regressions via QA | 471bc5a, 791380e |
| T-07, D-12, F-08 | emit_event `end` alias, current-session file, documented invocation | 36620ef |
| T-09 | incremental re-review §9a | 471bc5a |
| QA: severity names | one scale blocker/major/minor/note | 471bc5a |
| D-08, QA: regex drop / numbering / executed_at / skipped | WARN + count, continuing numbering, unknown time, skipped→not-executed | 4a088c5, 791380e |
| QA: flaky, partial, negative (CI-must-fail), report numbers vs evidence, void listing, generated table, decided_by strict, AC coverage, test-cases fields, testability conclusion/rounds/closed gaps, reviewer roles, Mode 4/5 upstream sets, manual attachments, evidence-index schema, commit normalized JSON | validate_qa + templates + qa-method | 791380e, 8bb8d1e |
| P2-2 (generated table, trim test-cases) | `--emit-table`; optional fields omitted | 791380e |
| D-11, D-17, D-19 | qa-method test data / isolation / server logs | 791380e |
| UX-S9, UX-S12, UX-S13, UX-S15 | ui-acceptance template, `<Screen>-<situation>` naming, manual owner + evidence link, interaction-state evidence | 791380e |
| UX-S10, UX-S16, QA: run2 archive | `evidence/<sha>/` standard step | 4a088c5, 791380e |
| Pre-existing review-session failures | completed sessions don't need current_item / Remaining items (skill bug) | 36620ef |
| P0-3 → P2 | decided_by (now an error) | 68beea9, 791380e |
| F-09 | no change needed (gate already blocked unapproved upstream) | — |

### Remaining, with justification
- **One shared copy instead of duplicates (P2-5):** not done, on purpose. Each skill must stay self-contained (each ships its own zip), and the constraint was to keep the copies byte-identical. `tools/check_shared.sh` enforces that.
- **"REQ rule → TC" numeric index (QA suggestion 5 in the core defect):** not added. It would be a hand-maintained index. Impact analysis is covered by the content-hash gate (STALE), evidence binding, and the every-AC-has-a-TC check.
- **Legacy TeamBoard review sessions:** 5 sessions miss `review_session_status`, `artifact_path` or `## Resolved items`. That is authoring content, not a skill defect, so they still fail until someone edits them. The skill bug (requiring an open-session queue on completed sessions) is fixed.

### History: two hash formats (resolved in 22612ad)
The evidence `requirements` hash and the gate's `approved_sha256` are both computed with frontmatter stripped, but with different algorithms: `check_upstream_gate.py` hashes `name + \0 + sha256(body) + \n` per file (so it also works on directories), while `validate_qa.py` / evidence import hash the body directly. Each detects staleness correctly on its own; the values are just not comparable to each other. Deferred: unify into one function.

Resolved in 22612ad: hash unified; evidence binds `sad.md` (design changes are caught by the gate only); usage line still shows `[--evidence-dir]` in brackets.
