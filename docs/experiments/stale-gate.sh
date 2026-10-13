#!/usr/bin/env bash
# Re-run of the "deliberately change an approved requirement" experiment (report §3, 360dba1)
# on a throw-away COPY of TeamBoard. Usage: stale-gate.sh <skills-dir> <team-board-repo>
set -u
S=$(cd "$1" && pwd); ORIG=$(cd "$2" && pwd); W=/tmp/tb-stale-exp
rm -rf $W && cp -r "$ORIG" $W && cd $W
G=$S/team-design/scripts/check_upstream_gate.py
F=team-board; RQ=docs/requirements/$F.md; SAD=docs/design/$F/sad.md; TS=docs/qa/$F/test-spec.md; TC=docs/qa/$F/test-cases.md; IDX=docs/dev-prep/$F/index.md; UX=docs/ux/$F
EV=/tmp/tb-stale-exp-evidence; rm -rf $EV
run(){ echo "\$ $*"; "$@" 2>&1 | sed 's/^/  /'; echo "  exit=${PIPESTATUS[0]}"; }
echo "## 1. Record baselines (approval stamp + downstream baselines)"
run python3 $G --stamp-approved $RQ
run python3 $G --record $SAD $RQ $UX
run python3 $G --record $TS $RQ $SAD
run python3 $G --record $IDX $RQ $SAD
run python3 $S/team-qa-evidence/scripts/normalize_evidence.py evidence/2d29e13/junit/go.xml --format junit --tool go --output-dir $EV/go --commit 2d29e13 --executed-at 2026-10-13T10:15:00+08:00 --requirements $RQ --test-plan $TC
run python3 $S/team-qa/scripts/validate_qa.py docs/qa/$F --evidence-dir $EV
echo "## 2. Baseline gates PASS"
run python3 $G --downstream $SAD $RQ $UX
run python3 $G --downstream $TS $RQ $SAD
echo "## 3. Deliberately change the approved requirement (title 1-120 -> 1-100); frontmatter untouched"
sed -i 's/title (required, 1–120 chars trimmed)/title (required, 1–100 chars trimmed)/' $RQ
git diff --stat -- $RQ | sed 's/^/  /'; grep -o 'title (required, 1–[0-9]* chars trimmed)' $RQ | sed 's/^/  /'
echo "## 4. Original v0.4 gate (status fields only)"
git -C $S/.. show 5c6ce39:skills/team-design/scripts/check_upstream_gate.py > /tmp/old_gate.py
run python3 /tmp/old_gate.py $RQ $SAD
echo "## 5. New gate"
run python3 $G $RQ
run python3 $G --downstream $SAD $RQ $UX
run python3 $G --downstream $TS $RQ $SAD
run python3 $G --downstream $IDX $RQ $SAD
echo "## 6. UX change is detected too (wireframe directory)"
echo "<!-- edit -->" >> $UX/ui-acceptance.md
run python3 $G --downstream $SAD $RQ $UX
echo "## 7. Final-assessment rejects evidence produced against the old requirement"
run python3 $S/team-qa/scripts/validate_qa.py docs/qa/$F --evidence-dir $EV
echo "## 8. Void the stale run, but forget void_reason on one record -> rejected"
run python3 $S/team-qa-evidence/scripts/void_evidence.py --reason "requirements changed (title 120->100) after run" $EV/go/*.json
python3 - "$EV/go/EVD-001.json" <<'PY'
import json,sys;p=sys.argv[1];d=json.load(open(p));d['void_reason']=None;json.dump(d,open(p,'w'),indent=2)
PY
run python3 $S/team-qa/scripts/validate_qa.py docs/qa/$F --evidence-dir $EV
echo "## 9. Fix reason, re-import against the new baseline (numbering continues, no overwrite) -> OK"
run python3 $S/team-qa-evidence/scripts/void_evidence.py --reason "requirements changed after run" $EV/go/EVD-001.json
run python3 $S/team-qa-evidence/scripts/normalize_evidence.py evidence/2d29e13/junit/go.xml --format junit --tool go --output-dir $EV/go --commit 2d29e13 --executed-at 2026-10-13T10:15:00+08:00 --requirements $RQ --test-plan $TC
run python3 $S/team-qa/scripts/validate_qa.py docs/qa/$F --evidence-dir $EV
echo "## 10. Evidence from a different commit is rejected"
run python3 $S/team-qa-evidence/scripts/write_manual_evidence.py --evidence-id EVD-M999 --test-case-id TC-071 --result pass --commit 6ece96d --output $EV/manual/EVD-M999.json --requirements $RQ --test-plan $TC
run python3 $S/team-qa/scripts/validate_qa.py docs/qa/$F --evidence-dir $EV
echo "## 11. Original repo untouched"
echo "  $(git -C "$ORIG" status --short | wc -l) changed files in $ORIG"
