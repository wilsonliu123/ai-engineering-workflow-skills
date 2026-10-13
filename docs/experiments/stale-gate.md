# Experiment: deliberately change an approved requirement (re-run, 2026-10-13)

Purpose: repeat the validation-report §3 experiment (`360dba1`, title limit change with frontmatter untouched)
against the fixed scripts and confirm that gates now report `STALE` and that final-assessment rejects
stale / void / wrong-commit evidence.

- Target: throw-away copy of TeamBoard (`cp -r` to `/tmp/tb-stale-exp`, HEAD `20fd395`); the original repo was not modified (step 11).
- Script: [`stale-gate.sh`](stale-gate.sh) (`bash docs/experiments/stale-gate.sh skills /path/to/team-board`). `$S` = `skills/`.
- Regression: [`regress.sh`](regress.sh) runs every validator and gate on the unmodified TeamBoard; output before and after the fixes is identical except that bare-argument calls now print a usage line (exit code unchanged: 2). The 5 `validate_review_session.py` failures (missing `current_item` / `## Remaining items`) exist in v0.4 too and are not caused by these changes.

## Result

| Check | v0.4 | now |
|---|---|---|
| Requirement edited after approval, status fields untouched | PASS (exit 0) | `STALE` via `approved_sha256` (exit 1) |
| Design / test-spec / dev-prep gate after that edit | PASS | `STALE ... changed since <downstream> recorded it` (exit 1) |
| Wireframe / UI-acceptance edit (`docs/ux/<slug>/`) | not tracked | `STALE` (exit 1) |
| Evidence produced against the old requirement | accepted | rejected, 40 × `STALE evidence` |
| Void evidence without reason | n/a (no field) | rejected |
| Evidence for another commit | accepted | rejected (`6ece96d != tested_commit 2d29e13`) |
| Re-import after void | overwrote EVD-001.. | continues at EVD-041, then OK |

Side finding: the Go JUnit report has 48 testcases but only 40 carry a `TC-###` id. The importer used to drop the 8 silently; it now prints a `WARN` line for each one and reports `dropped_without_tc: 8`.

## Full log (repetitive per-file lines collapsed)

```text
## 1. Record baselines (approval stamp + downstream baselines)
$ python3 $S/team-design/scripts/check_upstream_gate.py --stamp-approved docs/requirements/team-board.md
  STAMPED: docs/requirements/team-board.md approved_sha256=112210833162a2636d3cda08a1525416c4ba5658826cff011b2b97ccdaa9d33c
  exit=0
$ python3 $S/team-design/scripts/check_upstream_gate.py --record docs/design/team-board/sad.md docs/requirements/team-board.md docs/ux/team-board
  RECORDED: 2 upstream baseline(s) in docs/design/team-board/sad.md
  PASS: all upstream artifacts are complete, approved, and current and match recorded baselines
  exit=0
$ python3 $S/team-design/scripts/check_upstream_gate.py --record docs/qa/team-board/test-spec.md docs/requirements/team-board.md docs/design/team-board/sad.md
  RECORDED: 2 upstream baseline(s) in docs/qa/team-board/test-spec.md
  PASS: all upstream artifacts are complete, approved, and current and match recorded baselines
  exit=0
$ python3 $S/team-design/scripts/check_upstream_gate.py --record docs/dev-prep/team-board/index.md docs/requirements/team-board.md docs/design/team-board/sad.md
  RECORDED: 2 upstream baseline(s) in docs/dev-prep/team-board/index.md
  PASS: all upstream artifacts are complete, approved, and current and match recorded baselines
  exit=0
$ python3 $S/team-qa-evidence/scripts/normalize_evidence.py evidence/2d29e13/junit/go.xml --format junit --tool go --output-dir $EV/go --commit 2d29e13 --executed-at 2026-10-13T10:15:00+08:00 --requirements docs/requirements/team-board.md --test-plan docs/qa/team-board/test-cases.md
  WARN: dropped test without TC-### id: teamboard/internal/httpapi TestAuth
  WARN: dropped test without TC-### id: teamboard/internal/httpapi TestProjects
  WARN: dropped test without TC-### id: teamboard/internal/httpapi TestAuthorization
  WARN: dropped test without TC-### id: teamboard/internal/httpapi TestSessionCookieRenewal
  WARN: dropped test without TC-### id: teamboard/internal/httpapi TestRemoveMemberRollback
  WARN: dropped test without TC-### id: teamboard/internal/httpapi TestTasks
  WARN: dropped test without TC-### id: teamboard/internal/httpapi TestHistory
  WARN: dropped test without TC-### id: teamboard/internal/httpapi TestAudit
  {"normalized": 40, "dropped_without_tc": 8, "first_id": "EVD-001", "output_dir": "$EV/go"}
  exit=0
$ python3 $S/team-qa/scripts/validate_qa.py docs/qa/team-board --evidence-dir $EV
  INFO: evidence usable=40 void(excluded)=0
  OK: QA artifacts valid
  exit=0
## 2. Baseline gates PASS
$ python3 $S/team-design/scripts/check_upstream_gate.py --downstream docs/design/team-board/sad.md docs/requirements/team-board.md docs/ux/team-board
  PASS: all upstream artifacts are complete, approved, and current and match recorded baselines
  exit=0
$ python3 $S/team-design/scripts/check_upstream_gate.py --downstream docs/qa/team-board/test-spec.md docs/requirements/team-board.md docs/design/team-board/sad.md
  PASS: all upstream artifacts are complete, approved, and current and match recorded baselines
  exit=0
## 3. Deliberately change the approved requirement (title 1-120 -> 1-100); frontmatter untouched
   docs/requirements/team-board.md | 3 ++-
   1 file changed, 2 insertions(+), 1 deletion(-)
  title (required, 1–100 chars trimmed)
## 4. Original v0.4 gate (status fields only)
$ python3 /tmp/old_gate.py docs/requirements/team-board.md docs/design/team-board/sad.md
  PASS: all upstream artifacts are complete, approved, and current
  exit=0
## 5. New gate
$ python3 $S/team-design/scripts/check_upstream_gate.py docs/requirements/team-board.md
  STALE: docs/requirements/team-board.md: content changed after approval (approved_sha256 112210833162 != current 8ae62da00ef0)
  exit=1
$ python3 $S/team-design/scripts/check_upstream_gate.py --downstream docs/design/team-board/sad.md docs/requirements/team-board.md docs/ux/team-board
  STALE: docs/requirements/team-board.md: content changed after approval (approved_sha256 112210833162 != current 8ae62da00ef0)
  STALE: docs/requirements/team-board.md changed since docs/design/team-board/sad.md recorded it (112210833162 -> 8ae62da00ef0); mark docs/design/team-board/sad.md validity: stale and re-assess
  exit=1
$ python3 $S/team-design/scripts/check_upstream_gate.py --downstream docs/qa/team-board/test-spec.md docs/requirements/team-board.md docs/design/team-board/sad.md
  STALE: docs/requirements/team-board.md: content changed after approval (approved_sha256 112210833162 != current 8ae62da00ef0)
  STALE: docs/requirements/team-board.md changed since docs/qa/team-board/test-spec.md recorded it (112210833162 -> 8ae62da00ef0); mark docs/qa/team-board/test-spec.md validity: stale and re-assess
  exit=1
$ python3 $S/team-design/scripts/check_upstream_gate.py --downstream docs/dev-prep/team-board/index.md docs/requirements/team-board.md docs/design/team-board/sad.md
  STALE: docs/requirements/team-board.md: content changed after approval (approved_sha256 112210833162 != current 8ae62da00ef0)
  STALE: docs/requirements/team-board.md changed since docs/dev-prep/team-board/index.md recorded it (112210833162 -> 8ae62da00ef0); mark docs/dev-prep/team-board/index.md validity: stale and re-assess
  exit=1
## 6. UX change is detected too (wireframe directory)
$ python3 $S/team-design/scripts/check_upstream_gate.py --downstream docs/design/team-board/sad.md docs/requirements/team-board.md docs/ux/team-board
  STALE: docs/requirements/team-board.md: content changed after approval (approved_sha256 112210833162 != current 8ae62da00ef0)
  STALE: docs/requirements/team-board.md changed since docs/design/team-board/sad.md recorded it (112210833162 -> 8ae62da00ef0); mark docs/design/team-board/sad.md validity: stale and re-assess
  STALE: docs/ux/team-board changed since docs/design/team-board/sad.md recorded it (319b0e1d0789 -> ba7131e7cd54); mark docs/design/team-board/sad.md validity: stale and re-assess
  exit=1
## 7. Final-assessment rejects evidence produced against the old requirement
$ python3 $S/team-qa/scripts/validate_qa.py docs/qa/team-board --evidence-dir $EV
  INFO: evidence usable=40 void(excluded)=0
  ERROR: $EV/go/EVD-001.json: STALE evidence: docs/requirements/team-board.md changed since this evidence was produced; void it or re-run
  ... (39 more identical lines for other EVD files)
  exit=1
## 8. Void the stale run, but forget void_reason on one record -> rejected
$ python3 $S/team-qa-evidence/scripts/void_evidence.py --reason requirements changed (title 120->100) after run $EV/go/EVD-001.json $EV/go/EVD-002.json $EV/go/EVD-003.json $EV/go/EVD-004.json $EV/go/EVD-005.json $EV/go/EVD-006.json $EV/go/EVD-007.json $EV/go/EVD-008.json $EV/go/EVD-009.json $EV/go/EVD-010.json $EV/go/EVD-011.json $EV/go/EVD-012.json $EV/go/EVD-013.json $EV/go/EVD-014.json $EV/go/E
  VOID: $EV/go/EVD-001.json
  ... (39 more identical lines for other EVD files)
  exit=0
$ python3 $S/team-qa/scripts/validate_qa.py docs/qa/team-board --evidence-dir $EV
  INFO: evidence usable=0 void(excluded)=40
  ERROR: $EV/go/EVD-001.json: void evidence needs void_reason
  exit=1
## 9. Fix reason, re-import against the new baseline (numbering continues, no overwrite) -> OK
$ python3 $S/team-qa-evidence/scripts/void_evidence.py --reason requirements changed after run $EV/go/EVD-001.json
  VOID: $EV/go/EVD-001.json
  exit=0
$ python3 $S/team-qa-evidence/scripts/normalize_evidence.py evidence/2d29e13/junit/go.xml --format junit --tool go --output-dir $EV/go --commit 2d29e13 --executed-at 2026-10-13T10:15:00+08:00 --requirements docs/requirements/team-board.md --test-plan docs/qa/team-board/test-cases.md
  WARN: dropped test without TC-### id: teamboard/internal/httpapi TestAuth
  WARN: dropped test without TC-### id: teamboard/internal/httpapi TestProjects
  WARN: dropped test without TC-### id: teamboard/internal/httpapi TestAuthorization
  WARN: dropped test without TC-### id: teamboard/internal/httpapi TestSessionCookieRenewal
  WARN: dropped test without TC-### id: teamboard/internal/httpapi TestRemoveMemberRollback
  WARN: dropped test without TC-### id: teamboard/internal/httpapi TestTasks
  WARN: dropped test without TC-### id: teamboard/internal/httpapi TestHistory
  WARN: dropped test without TC-### id: teamboard/internal/httpapi TestAudit
  {"normalized": 40, "dropped_without_tc": 8, "first_id": "EVD-041", "output_dir": "$EV/go"}
  exit=0
$ python3 $S/team-qa/scripts/validate_qa.py docs/qa/team-board --evidence-dir $EV
  INFO: evidence usable=40 void(excluded)=40
  OK: QA artifacts valid
  exit=0
## 10. Evidence from a different commit is rejected
$ python3 $S/team-qa-evidence/scripts/write_manual_evidence.py --evidence-id EVD-M999 --test-case-id TC-071 --result pass --commit 6ece96d --output $EV/manual/EVD-M999.json --requirements docs/requirements/team-board.md --test-plan docs/qa/team-board/test-cases.md
  $EV/manual/EVD-M999.json
  exit=0
$ python3 $S/team-qa/scripts/validate_qa.py docs/qa/team-board --evidence-dir $EV
  INFO: evidence usable=41 void(excluded)=40
  ERROR: $EV/manual/EVD-M999.json: evidence commit 6ece96d != tested_commit 2d29e13
  exit=1
## 11. Original repo untouched
  0 changed files in /workspace/skill-validation/team-board
```

## Follow-up (commit 025dc0b): stricter gate and validate_qa

Script: [`followup.sh`](followup.sh), run on a fresh copy at `/tmp/tb-followup`.

The log above predates `025dc0b`. With the stricter gate, steps 1–2 of `stale-gate.sh` would also need `--stamp-approved` on `sad.md` first.

**Regression note:** on the unmodified TeamBoard every `check_upstream_gate.py` call in `regress.sh` now exits 1 with `BLOCK: ... approved but no approved_sha256`. This is intended. v0.4 artifacts have no hash, so to migrate, run `--stamp-approved` once per approved document after confirming the approval still applies. Likewise, `validate_qa.py docs/qa/team-board` without `--evidence-dir` now fails because `qa-report.md` exists.

```text
## (b) approved upstream without approved_sha256 (unmodified v0.4 artifacts)
$ python3 $S/team-qa/scripts/check_upstream_gate.py docs/requirements/team-board.md docs/design/team-board/sad.md
  BLOCK: docs/requirements/team-board.md: approved but no approved_sha256 (run check_upstream_gate.py --stamp-approved docs/requirements/team-board.md once)
  BLOCK: docs/design/team-board/sad.md: approved but no approved_sha256 (run check_upstream_gate.py --stamp-approved docs/design/team-board/sad.md once)
  exit=1
$ python3 $S/team-qa/scripts/check_upstream_gate.py --downstream docs/qa/team-board/test-spec.md docs/requirements/team-board.md docs/design/team-board/sad.md
  BLOCK: docs/requirements/team-board.md: approved but no approved_sha256 (run check_upstream_gate.py --stamp-approved docs/requirements/team-board.md once)
  BLOCK: docs/design/team-board/sad.md: approved but no approved_sha256 (run check_upstream_gate.py --stamp-approved docs/design/team-board/sad.md once)
  BLOCK: docs/qa/team-board/test-spec.md: no recorded baseline for docs/requirements/team-board.md (run --record)
  BLOCK: docs/qa/team-board/test-spec.md: no recorded baseline for docs/design/team-board/sad.md (run --record)
  exit=1
## (c) consistent state: stamp, record, fresh evidence
$ python3 $S/team-qa/scripts/check_upstream_gate.py --stamp-approved docs/requirements/team-board.md
  STAMPED: docs/requirements/team-board.md approved_sha256=112210833162a2636d3cda08a1525416c4ba5658826cff011b2b97ccdaa9d33c
  exit=0
$ python3 $S/team-qa/scripts/check_upstream_gate.py --stamp-approved docs/design/team-board/sad.md
  STAMPED: docs/design/team-board/sad.md approved_sha256=321c7b692cbee4e3e08a8e396f4165619c9bf7f3b49bb12270d60e32e6216c1f
  exit=0
$ python3 $S/team-qa/scripts/check_upstream_gate.py --record docs/qa/team-board/test-spec.md docs/requirements/team-board.md docs/design/team-board/sad.md
  RECORDED: 2 upstream baseline(s) in docs/qa/team-board/test-spec.md
  PASS: all upstream artifacts are complete, approved, and current and match recorded baselines
  exit=0
$ python3 $S/team-qa/scripts/check_upstream_gate.py --record docs/qa/team-board/test-cases.md docs/requirements/team-board.md docs/design/team-board/sad.md
  RECORDED: 2 upstream baseline(s) in docs/qa/team-board/test-cases.md
  PASS: all upstream artifacts are complete, approved, and current and match recorded baselines
  exit=0
$ python3 $S/team-qa/scripts/check_upstream_gate.py --downstream docs/qa/team-board/test-spec.md docs/requirements/team-board.md docs/design/team-board/sad.md
  PASS: all upstream artifacts are complete, approved, and current and match recorded baselines
  exit=0
$ python3 $S/team-qa/scripts/check_upstream_gate.py --downstream docs/qa/team-board/test-cases.md docs/requirements/team-board.md docs/design/team-board/sad.md
  PASS: all upstream artifacts are complete, approved, and current and match recorded baselines
  exit=0
$ python3 $S/team-qa-evidence/scripts/normalize_evidence.py evidence/2d29e13/junit/go.xml --format junit --tool go --output-dir $EV/go --commit 2d29e13 --executed-at 2026-10-13T10:15:00+08:00 --requirements docs/requirements/team-board.md --test-plan docs/qa/team-board/test-cases.md
  {"normalized": 40, "dropped_without_tc": 8, "first_id": "EVD-001", "output_dir": "$EV/go"}
  exit=0
$ python3 $S/team-qa/scripts/validate_qa.py docs/qa/team-board --evidence-dir $EV
  INFO: evidence usable=40 void(excluded)=0
  OK: QA artifacts valid
  exit=0
## (a) stale state (requirement edited) and validate_qa without --evidence-dir
$ python3 $S/team-qa/scripts/check_upstream_gate.py --downstream docs/qa/team-board/test-cases.md docs/requirements/team-board.md docs/design/team-board/sad.md
  STALE: docs/requirements/team-board.md: content changed after approval (approved_sha256 112210833162 != current 8ae62da00ef0)
  STALE: docs/requirements/team-board.md changed since docs/qa/team-board/test-cases.md recorded it (112210833162 -> 8ae62da00ef0); mark docs/qa/team-board/test-cases.md validity: stale and re-assess
  exit=1
$ python3 $S/team-qa/scripts/validate_qa.py docs/qa/team-board
  ERROR: qa-report.md exists: --evidence-dir <normalized-evidence-dir> is required (final-assessment must check evidence baselines)
  exit=1
$ python3 $S/team-qa/scripts/validate_qa.py docs/qa/team-board --evidence-dir $EV
  INFO: evidence usable=40 void(excluded)=0
  ERROR: $EV/go/EVD-001.json: STALE evidence: docs/requirements/team-board.md changed since this evidence was produced; void it or re-run
  ... (39 more identical STALE evidence lines)
  exit=1
## original repo: 0 changed files
```


## v0.6 re-run: full migration, consistent state, stale + evidence checks (all deferred items fixed)

Script: [`migrate-teamboard.sh`](migrate-teamboard.sh) on a fresh copy at `/tmp/tb-v6`; the original repo is unchanged. It supersedes `stale-gate.sh` / `followup.sh`, whose v0.4 fixtures now BLOCK on missing `approved_by_role` (intended).

**Regression on the unmodified TeamBoard (`regress.sh`):** 19 calls exit 1 and the 2 no-argument calls exit 2 (usage). No call raises a traceback. Every failure comes from the migration requirements (`approved_sha256`, `approved_by_role`, `--evidence-dir`, `upstream:`) or from legacy content gaps (no environment section, no testability conclusion, review sessions missing `review_session_status` / `## Resolved items`). The skill bug behind the v0.4 review-session failures is fixed: a completed session no longer needs `current_item` or `## Remaining items`.

**Result after migration:**
- Every validator returns OK except `validate_release.py`.
- Every `--downstream` gate passes.
- Gate and evidence hashes are identical (`112210833162…` for requirements).
- `validate_release.py` correctly BLOCKs: the release handoff was written and approved by the PM, with no independent reviewer. This is the real author == approver deviation from report §4.2. It is not cleared artificially.

**Stale requirement:** the gate reports STALE and `validate_requirements` fails. `validate_qa` without `--evidence-dir` fails, and with it all 82 evidence records are rejected.
**Design attachment edit (`interface.md`):** all 82 records are rejected, because the evidence is now bound to the design package.
**Void + flaky + count mismatch:** all three are rejected.

```text
## M1. Structured approval fields (facts from the v0.4 review sessions: all approvals by PM, delegated by 剑秋)
  done
## M2. Content gaps the new validators flag, filled from facts already in the repo
  done
## M3. Stamp approvals once (after confirming approvals still apply)
  STAMPED: docs/requirements/team-board.md approved_sha256=112210833162a2636d3cda08a1525416c4ba5658826cff011b2b97ccdaa9d33c
  STAMPED: docs/design/team-board/sad.md approved_sha256=321c7b692cbee4e3e08a8e396f4165619c9bf7f3b49bb12270d60e32e6216c1f
  STAMPED: docs/shared/adr/ADR-001-session-auth.md approved_sha256=f152c0c5f245f35c7d72b37fdb6c3ccef69109010004fc6a0141f2628c8f47fa
  STAMPED: docs/dev-prep/team-board/index.md approved_sha256=8e2d2454cf94b63a47cf40b0909ce9d6321276cbcd9005513435f62e4090aba0
  STAMPED: docs/qa/team-board/test-spec.md approved_sha256=ef29429bb3a1d52aee3bd50bb29c2f526862affe32530c90b7edd1b66a956738
  STAMPED: docs/qa/team-board/test-cases.md approved_sha256=6ae4c8ab9bb11721ea906cd682ab96855338ab4a01539b6c442b362b3ff4a026
  STAMPED: docs/qa/team-board/execution-matrix.md approved_sha256=0290670c0eaffdc239da153fdac67663ef50ba2c87be1aacad9732e6c2e17f14
  STAMPED: docs/dev-delivery/team-board/dev-delivery.md approved_sha256=a5fc32aa1a2f3f85e907284fad9ad51ac041f55783d3ecefe5e4b16984d9921b
  STAMPED: docs/review/team-board/code-review.md approved_sha256=4fd2e085c8fe86691cc9d410c8b4bb426b15b54a9b285aafcc30252d1e1c8d40
  STAMPED: docs/qa/team-board/qa-report.md approved_sha256=f9112186a2dda931edc4ea8b2de2fb63d6fb8cfb41e67ba740fcb9a735dceb8e
  STAMPED: docs/release/team-board/release-handoff.md approved_sha256=9dfc35b8c31fef493e6127192d110d0b8cf47d4acf3abb82eb30b1acc7c2cc0b
## M4. Record upstream baselines
$ python3 $S/team-qa/scripts/check_upstream_gate.py --record docs/design/team-board/sad.md docs/requirements/team-board.md docs/ux/team-board
  RECORDED: 2 upstream baseline(s) in docs/design/team-board/sad.md
  PASS: all upstream artifacts are complete, approved, and current and match recorded baselines
  exit=0
$ python3 $S/team-qa/scripts/check_upstream_gate.py --record docs/qa/team-board/test-spec.md docs/requirements/team-board.md docs/design/team-board/sad.md
  RECORDED: 2 upstream baseline(s) in docs/qa/team-board/test-spec.md
  PASS: all upstream artifacts are complete, approved, and current and match recorded baselines
  exit=0
$ python3 $S/team-qa/scripts/check_upstream_gate.py --record docs/qa/team-board/test-cases.md docs/requirements/team-board.md docs/design/team-board/sad.md
  RECORDED: 2 upstream baseline(s) in docs/qa/team-board/test-cases.md
  PASS: all upstream artifacts are complete, approved, and current and match recorded baselines
  exit=0
$ python3 $S/team-qa/scripts/check_upstream_gate.py --record docs/dev-prep/team-board/index.md docs/requirements/team-board.md docs/design/team-board/sad.md docs/qa/team-board/test-spec.md
  RECORDED: 3 upstream baseline(s) in docs/dev-prep/team-board/index.md
  PASS: all upstream artifacts are complete, approved, and current and match recorded baselines
  exit=0
$ python3 $S/team-qa/scripts/check_upstream_gate.py --record docs/dev-delivery/team-board/dev-delivery.md docs/requirements/team-board.md docs/design/team-board/sad.md docs/dev-prep/team-board/index.md docs/qa/team-board/test-spec.md docs/qa/team-board/test-cases.md docs/ux/team-board
  RECORDED: 6 upstream baseline(s) in docs/dev-delivery/team-board/dev-delivery.md
  PASS: all upstream artifacts are complete, approved, and current and match recorded baselines
  exit=0
$ python3 $S/team-qa/scripts/check_upstream_gate.py --record docs/review/team-board/code-review.md docs/requirements/team-board.md docs/design/team-board/sad.md docs/dev-prep/team-board/index.md docs/dev-delivery/team-board/dev-delivery.md
  RECORDED: 4 upstream baseline(s) in docs/review/team-board/code-review.md
  PASS: all upstream artifacts are complete, approved, and current and match recorded baselines
  exit=0
$ python3 $S/team-qa/scripts/check_upstream_gate.py --record docs/qa/team-board/qa-report.md docs/requirements/team-board.md docs/design/team-board/sad.md docs/qa/team-board/test-spec.md docs/qa/team-board/test-cases.md docs/qa/team-board/execution-matrix.md docs/dev-delivery/team-board/dev-delivery.md docs/review/team-board/code-review.md docs/ux/team-board
  RECORDED: 8 upstream baseline(s) in docs/qa/team-board/qa-report.md
  PASS: all upstream artifacts are complete, approved, and current and match recorded baselines
  exit=0
$ python3 $S/team-qa/scripts/check_upstream_gate.py --record docs/release/team-board/release-handoff.md docs/qa/team-board/qa-report.md docs/review/team-board/code-review.md docs/dev-delivery/team-board/dev-delivery.md
  RECORDED: 3 upstream baseline(s) in docs/release/team-board/release-handoff.md
  PASS: all upstream artifacts are complete, approved, and current and match recorded baselines
  exit=0
## M5. Re-import run2 evidence with baselines (requirements, test-cases, design)
$ python3 $S/team-qa-evidence/scripts/normalize_evidence.py evidence/2d29e13/junit/go.xml --format junit --tool go --output-dir .ai-workflow/evidence/team-board/normalized-v6/run2-2d29e13/go --commit 2d29e13 --executed-at 2026-10-13T10:15:00+08:00 --requirements docs/requirements/team-board.md --test-plan docs/qa/team-board/test-cases.md --design docs/design/team-board/sad.md
  {"normalized": 40, "dropped_without_tc": 8, "first_id": "EVD-001", "output_dir": ".ai-workflow/evidence/team-board/normalized-v6/run2-2d29e13/go"}
  exit=0
$ python3 $S/team-qa-evidence/scripts/normalize_evidence.py evidence/2d29e13/junit/vitest.xml --format junit --tool vitest --output-dir .ai-workflow/evidence/team-board/normalized-v6/run2-2d29e13/vitest --commit 2d29e13 --executed-at 2026-10-13T10:15:00+08:00 --requirements docs/requirements/team-board.md --test-plan docs/qa/team-board/test-cases.md --design docs/design/team-board/sad.md
  {"normalized": 4, "dropped_without_tc": 0, "first_id": "EVD-001", "output_dir": ".ai-workflow/evidence/team-board/normalized-v6/run2-2d29e13/vitest"}
  exit=0
$ python3 $S/team-qa-evidence/scripts/normalize_evidence.py evidence/2d29e13/junit/playwright.xml --format junit --tool playwright --output-dir .ai-workflow/evidence/team-board/normalized-v6/run2-2d29e13/playwright --commit 2d29e13 --executed-at 2026-10-13T10:15:00+08:00 --requirements docs/requirements/team-board.md --test-plan docs/qa/team-board/test-cases.md --design docs/design/team-board/sad.
  {"normalized": 35, "dropped_without_tc": 0, "first_id": "EVD-001", "output_dir": ".ai-workflow/evidence/team-board/normalized-v6/run2-2d29e13/playwright"}
  exit=0
$ python3 $S/team-qa-evidence/scripts/write_manual_evidence.py --evidence-id EVD-M002 --test-case-id TC-069 --result pass --output .ai-workflow/evidence/team-board/normalized-v6/manual-2d29e13/EVD-M002.json --commit 2d29e13 --attachment docs/ux/team-board/ui-acceptance.md@dedc160 --requirements docs/requirements/team-board.md --test-plan docs/qa/team-board/test-cases.md --design docs/design/team-b
  .ai-workflow/evidence/team-board/normalized-v6/manual-2d29e13/EVD-M002.json
  exit=0
$ python3 $S/team-qa-evidence/scripts/write_manual_evidence.py --evidence-id EVD-M003 --test-case-id TC-071 --result pass --output .ai-workflow/evidence/team-board/normalized-v6/manual-2d29e13/EVD-M003.json --commit 2d29e13 --attachment docs/ux/team-board/ui-acceptance.md@dedc160 --requirements docs/requirements/team-board.md --test-plan docs/qa/team-board/test-cases.md --design docs/design/team-b
  .ai-workflow/evidence/team-board/normalized-v6/manual-2d29e13/EVD-M003.json
  exit=0
$ python3 $S/team-qa-evidence/scripts/write_manual_evidence.py --evidence-id EVD-M004 --test-case-id TC-073 --result pass --output .ai-workflow/evidence/team-board/normalized-v6/manual-2d29e13/EVD-M004.json --commit 2d29e13 --attachment docs/ux/team-board/ui-acceptance.md@dedc160 --requirements docs/requirements/team-board.md --test-plan docs/qa/team-board/test-cases.md --design docs/design/team-b
  .ai-workflow/evidence/team-board/normalized-v6/manual-2d29e13/EVD-M004.json
  exit=0
## M6. Generate result_counts from evidence and write it into the QA report
  generated: result_counts: pass=77 fail=0 blocked=0 partial=0 not-executed=0

## C. Consistent state
$ python3 $S/team-requirements/scripts/validate_requirements.py docs/requirements/team-board.md
  WARN: document_type is redundant; use artifact_type only
  OK: requirements valid
  exit=0
$ python3 $S/team-design/scripts/validate_design.py docs/design/team-board docs/requirements/team-board.md
  OK: design valid
  exit=0
$ python3 $S/team-dev-prep/scripts/validate_dev_prep.py docs/dev-prep/team-board docs/requirements/team-board.md docs/design/team-board/sad.md
  OK: dev prep valid
  exit=0
$ python3 $S/team-dev-handoff/scripts/validate_dev_handoff.py docs/dev-delivery/team-board/dev-delivery.md
  OK: dev handoff valid
  exit=0
$ python3 $S/team-review/scripts/validate_review.py docs/review/team-board/code-review.md docs/requirements/team-board.md docs/design/team-board/sad.md
  OK: code review valid
  exit=0
$ python3 $S/team-qa/scripts/validate_qa.py docs/qa/team-board --evidence-dir .ai-workflow/evidence/team-board/normalized-v6
  INFO: evidence usable=82 void(excluded)=0
  OK: QA artifacts valid
  exit=0
$ python3 $S/team-release/scripts/validate_release.py docs/release/team-board/release-handoff.md
  ERROR: BLOCK: docs/release/team-board/release-handoff.md: author approved own artifact (approved_by_role == owner_role); set independent_reviewer_role to a different role that reviewed it
  exit=1
$ python3 $S/team-qa/scripts/check_upstream_gate.py --downstream docs/design/team-board/sad.md docs/requirements/team-board.md docs/ux/team-board
  PASS: all upstream artifacts are complete, approved, and current and match recorded baselines
  exit=0
$ python3 $S/team-qa/scripts/check_upstream_gate.py --downstream docs/qa/team-board/test-cases.md docs/requirements/team-board.md docs/design/team-board/sad.md
  PASS: all upstream artifacts are complete, approved, and current and match recorded baselines
  exit=0
$ python3 $S/team-qa/scripts/check_upstream_gate.py --downstream docs/qa/team-board/qa-report.md docs/requirements/team-board.md docs/design/team-board/sad.md docs/qa/team-board/test-spec.md docs/qa/team-board/test-cases.md docs/qa/team-board/execution-matrix.md docs/dev-delivery/team-board/dev-delivery.md docs/review/team-board/code-review.md docs/ux/team-board
  PASS: all upstream artifacts are complete, approved, and current and match recorded baselines
  exit=0
$ python3 $S/team-qa/scripts/check_upstream_gate.py --downstream docs/release/team-board/release-handoff.md docs/qa/team-board/qa-report.md docs/review/team-board/code-review.md docs/dev-delivery/team-board/dev-delivery.md
  PASS: all upstream artifacts are complete, approved, and current and match recorded baselines
  exit=0
  hash comparability: gate --hash of requirements = evidence baseline sha256?
  evidence: 112210833162a2636d3cda08a1525416c4ba5658826cff011b2b97ccdaa9d33c
  gate:     112210833162a2636d3cda08a1525416c4ba5658826cff011b2b97ccdaa9d33c

## S. Stale experiment: change approved requirement (title 1-120 -> 1-100)
$ python3 $S/team-qa/scripts/check_upstream_gate.py --downstream docs/qa/team-board/test-cases.md docs/requirements/team-board.md docs/design/team-board/sad.md
  STALE: docs/requirements/team-board.md: content changed after approval (approved_sha256 112210833162 != current 8ae62da00ef0)
  STALE: docs/requirements/team-board.md changed since docs/qa/team-board/test-cases.md recorded it (112210833162 -> 8ae62da00ef0); mark docs/qa/team-board/test-cases.md validity: stale and re-assess
  exit=1
$ python3 $S/team-requirements/scripts/validate_requirements.py docs/requirements/team-board.md
  WARN: document_type is redundant; use artifact_type only
  ERROR: STALE: docs/requirements/team-board.md: content changed after approval (approved_sha256 112210833162 != current 8ae62da00ef0)
  exit=1
$ python3 $S/team-qa/scripts/validate_qa.py docs/qa/team-board
  ERROR: qa-report.md exists: --evidence-dir <normalized-evidence-dir> is required (final-assessment must check evidence baselines)
  exit=1
$ python3 $S/team-qa/scripts/validate_qa.py docs/qa/team-board --evidence-dir .ai-workflow/evidence/team-board/normalized-v6
  INFO: evidence usable=82 void(excluded)=0
  ERROR: .ai-workflow/evidence/team-board/normalized-v6/manual-2d29e13/EVD-M002.json: STALE evidence: docs/requirements/team-board.md changed since this evidence was produced; void it or re-run
  ... (81 more STALE evidence lines)
  exit=1
## S2. Design attachment edit makes evidence stale too (design binding)
$ python3 $S/team-qa/scripts/validate_qa.py docs/qa/team-board --evidence-dir .ai-workflow/evidence/team-board/normalized-v6
  INFO: evidence usable=82 void(excluded)=0
  ERROR: .ai-workflow/evidence/team-board/normalized-v6/manual-2d29e13/EVD-M002.json: STALE evidence: docs/design/team-board/sad.md changed since this evidence was produced; void it or re-run
  ... (81 more STALE evidence lines)
  exit=1
## S3. Void + flaky + count mismatch
$ python3 $S/team-qa/scripts/validate_qa.py docs/qa/team-board --evidence-dir .ai-workflow/evidence/team-board/normalized-v6
  INFO: evidence usable=82 void(excluded)=1
  ERROR: TC-002: conflicting pass and fail on commit 2d29e13 (flaky?): rerun per the flaky rule and void superseded records
  ERROR: qa-report result_counts (pass=77 fail=0 blocked=0 partial=0 not-executed=0) != evidence (pass=75 fail=1 blocked=0 partial=0 not-executed=1); regenerate with --emit-table
  ERROR: void evidence run2-2d29e13/go/EVD-001 not listed in qa-report §8.3
  exit=1
## original repo: 0 changed files
```
