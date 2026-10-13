#!/usr/bin/env bash
# Migrate a COPY of TeamBoard (v0.4 artifacts) to v0.6 rules, then show consistent state PASS and the
# stale/evidence experiment. Usage: migrate-teamboard.sh <skills-dir> <team-board-repo>
S=$(cd "$1" && pwd); ORIG=$(cd "$2" && pwd); W=/tmp/tb-v6; EV=$W/.ai-workflow/evidence/team-board/normalized-v6
rm -rf $W && cp -r "$ORIG" $W && cd $W
G=$S/team-qa/scripts/check_upstream_gate.py; Q=$S/team-qa/scripts/validate_qa.py
F=team-board; RQ=docs/requirements/$F.md; SAD=docs/design/$F/sad.md; IDX=docs/dev-prep/$F/index.md
TS=docs/qa/$F/test-spec.md; TC=docs/qa/$F/test-cases.md; EM=docs/qa/$F/execution-matrix.md; QR=docs/qa/$F/qa-report.md
DD=docs/dev-delivery/$F/dev-delivery.md; CR=docs/review/$F/code-review.md; RH=docs/release/$F/release-handoff.md
UX=docs/ux/$F; ADR=docs/shared/adr/ADR-001-session-auth.md
run(){ echo "\$ $*"; "$@" 2>&1 | grep -v 'WARN: dropped' | sed "s#$S#\$S#g; s#$W/##g" | sed 's/^/  /'; echo "  exit=${PIPESTATUS[0]}"; }
setfm(){ python3 -c "import sys;sys.path.insert(0,'$S/team-qa/scripts');from check_upstream_gate import set_fm;set_fm(sys.argv[1],sys.argv[2],sys.argv[2]+': '+sys.argv[3])" "$@"; }

echo "## M1. Structured approval fields (facts from the v0.4 review sessions: all approvals by PM, delegated by 剑秋)"
for f in $RQ $SAD docs/design/$F/frontend.md docs/design/$F/backend.md docs/design/$F/interface.md $IDX $TS $TC $EM $DD $CR $QR $RH $ADR; do
  setfm $f approved_by_role "PM (delegated)"; setfm $f delegation_reference "剑秋, group chat 2026-10-13"; done
setfm $RQ owner_role "PM"   # requirements author was the PM (F-19)
setfm $RQ independent_reviewer_role "QA (requirements-testability, pass-with-conditions @ ade3c57)"
echo "  done"
echo "## M2. Content gaps the new validators flag, filled from facts already in the repo"
python3 - <<'PY'
import re
p='docs/qa/team-board/requirements-testability.md'; t=open(p).read()
t=t.replace('## 6. Conclusion for requirements review','## 6. Conclusion for requirements review\nConclusion: pass-with-conditions',1); open(p,'w').write(t)
p='docs/dev-prep/team-board/index.md'; t=open(p).read()
t=t.replace('## 6. Validation Baseline','''## 5a. Environment prerequisites
| Tool / version (pinned) | Needed for | Present? | Owning task |
|---|---|---|---|
| Go 1.24 (GOTOOLCHAIN=local) | backend build/test | yes | TASK-001 |
| Node 20 | frontend, Vitest, Playwright | yes | TASK-001 |
| make, gotestsum | make ci | no (installed ad hoc, D-01) | TASK-001 |
| Playwright Chromium | E2E | no (installed ad hoc, D-01) | TASK-008 |

## 6. Validation Baseline''',1); open(p,'w').write(t)
PY
echo "  done"
echo "## M3. Stamp approvals once (after confirming approvals still apply)"
for f in $RQ $SAD $ADR $IDX $TS $TC $EM $DD $CR $QR $RH; do run python3 $G --stamp-approved $f; done 2>&1 | grep -E "STAMPED|exit=[^0]"
echo "## M4. Record upstream baselines"
run python3 $G --record $SAD $RQ $UX
run python3 $G --record $TS $RQ $SAD
run python3 $G --record $TC $RQ $SAD
run python3 $G --record $IDX $RQ $SAD $TS
run python3 $G --record $DD $RQ $SAD $IDX $TS $TC $UX
run python3 $G --record $CR $RQ $SAD $IDX $DD
run python3 $G --record $QR $RQ $SAD $TS $TC $EM $DD $CR $UX
run python3 $G --record $RH $QR $CR $DD
echo "## M5. Re-import run2 evidence with baselines (requirements, test-cases, design)"
B="--commit 2d29e13 --executed-at 2026-10-13T10:15:00+08:00 --requirements $RQ --test-plan $TC --design $SAD"
for s in go vitest playwright; do run python3 $S/team-qa-evidence/scripts/normalize_evidence.py evidence/2d29e13/junit/$s.xml --format junit --tool $s --output-dir $EV/run2-2d29e13/$s $B; done
for m in M002:TC-069 M003:TC-071 M004:TC-073; do id=${m%%:*}; tc=${m##*:}
  r=$(python3 -c "import json;print(json.load(open('.ai-workflow/evidence/team-board/normalized/manual-2d29e13/EVD-$id.json'))['result'])")
  run python3 $S/team-qa-evidence/scripts/write_manual_evidence.py --evidence-id EVD-$id --test-case-id $tc --result $r --output $EV/manual-2d29e13/EVD-$id.json --commit 2d29e13 --attachment docs/ux/$F/ui-acceptance.md@dedc160 --requirements $RQ --test-plan $TC --design $SAD; done
echo "## M6. Generate result_counts from evidence and write it into the QA report"
rc=$(python3 $Q docs/qa/$F --evidence-dir $EV --emit-table 2>/dev/null | grep '^result_counts:' | cut -d' ' -f2-)
echo "  generated: result_counts: $rc"; setfm $QR result_counts "$rc"

echo; echo "## C. Consistent state"
run python3 $S/team-requirements/scripts/validate_requirements.py $RQ
run python3 $S/team-design/scripts/validate_design.py docs/design/$F $RQ
run python3 $S/team-dev-prep/scripts/validate_dev_prep.py docs/dev-prep/$F $RQ $SAD
run python3 $S/team-dev-handoff/scripts/validate_dev_handoff.py $DD
run python3 $S/team-review/scripts/validate_review.py $CR $RQ $SAD
run python3 $Q docs/qa/$F --evidence-dir $EV
run python3 $S/team-release/scripts/validate_release.py $RH
run python3 $G --downstream $SAD $RQ $UX
run python3 $G --downstream $TC $RQ $SAD
run python3 $G --downstream $QR $RQ $SAD $TS $TC $EM $DD $CR $UX
run python3 $G --downstream $RH $QR $CR $DD
echo "  hash comparability: gate --hash of requirements = evidence baseline sha256?"
python3 -c "import json,glob;print('  evidence:',json.load(open(sorted(glob.glob('$EV/run2-2d29e13/go/*.json'))[0]))['baseline']['requirements']['sha256'])"
echo "  gate:     $(python3 $G --hash $RQ)"

echo; echo "## S. Stale experiment: change approved requirement (title 1-120 -> 1-100)"
sed -i 's/title (required, 1–120 chars trimmed)/title (required, 1–100 chars trimmed)/' $RQ
run python3 $G --downstream $TC $RQ $SAD
run python3 $S/team-requirements/scripts/validate_requirements.py $RQ
run python3 $Q docs/qa/$F
run python3 $Q docs/qa/$F --evidence-dir $EV
echo "## S2. Design attachment edit makes evidence stale too (design binding)"
git checkout -q -- $RQ; python3 $G --stamp-approved $RQ >/dev/null
echo "<!-- edit -->" >> docs/design/$F/interface.md
run python3 $Q docs/qa/$F --evidence-dir $EV
git checkout -q -- docs/design/$F/interface.md
echo "## S3. Void + flaky + count mismatch"
python3 $S/team-qa-evidence/scripts/void_evidence.py --reason "demo void" $EV/run2-2d29e13/go/EVD-001.json >/dev/null
cp $EV/run2-2d29e13/go/EVD-002.json $EV/run2-2d29e13/go/EVD-999.json
python3 - "$EV/run2-2d29e13/go/EVD-999.json" <<'PY'
import json,sys;p=sys.argv[1];d=json.load(open(p));d['evidence_id']='EVD-999';d['result']='fail';json.dump(d,open(p,'w'),indent=2)
PY
run python3 $Q docs/qa/$F --evidence-dir $EV
echo "## original repo: $(git -C "$ORIG" status --short | wc -l) changed files"
