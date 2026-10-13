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
