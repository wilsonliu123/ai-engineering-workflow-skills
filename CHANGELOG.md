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

## Deferred (not done: would add complexity or is out of scope for script/template fixes)
- P1-1 stronger validators (multi-value Covers, Must→AC, traceability table, dev-prep dependency cycles, design state matrix, QA AC coverage, report counts vs evidence, review head_commit vs range): F-05, F-14, D-02, UX-05, T-03, QA: validate_qa.
- "approved but content changed" check inside each stage validator: the gate covers it; the validators have diverged copies of `gov()`.
- P1-2 structured approval fields and the author ≠ approver rule (F-11, F-19, F-20).
- P1-3 batch review in team-doc-review (F-22).
- P1-4 environment/setup step and per-stage `git add` scope (D-01, D-06, D-13, T-08, F-21).
- P1-5 remainder: severity vocabulary, `.gitignore.ai-workflow` merge note, `review_status` initial value (F-00, F-02, F-07). emit_event `end`/`complete` (D-12, T-07): the skill docs already say `complete`; no change needed.
- P2-1 template additions (screen list, five-situation checklist, frontend state alignment: UX-03/04/06/08, F-06/F-10/F-12).
- P2-2 qa-report table generated from evidence; trimming the test-cases template.
- P2-3 risk-accepted early start, ADR status on design approval, design-change flow after review (D-03, D-04, D-14, T-04).
- P2-4 flaky rules, partial-execution state, negative-test guidance, interaction-state evidence (UX-S15), external-attachment field for manual evidence.
- P2-5 single shared script copy (deliberately kept duplicated; identical-content rule documented in README).
- P2-6 items (T-09, D-11, D-16 registry, D-17, D-19, F-01). UX-S9/S12/S13 (visual-acceptance template, screenshot naming, manual TC owner).

## Pre-existing, unchanged
- In v0.4, `validate_review_session.py` fails on 5 of the 6 TeamBoard review sessions (missing `current_item`, `## Resolved/Remaining items`). It still does; nothing here changes that.
