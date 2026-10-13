#!/usr/bin/env bash
# Follow-up checks (a/b/c). Usage: followup.sh <skills-dir> <team-board-repo>
S=$(cd "$1" && pwd); ORIG=$(cd "$2" && pwd); W=/tmp/tb-followup; EV=/tmp/tb-followup-ev
rm -rf $W $EV && cp -r "$ORIG" $W && cd $W
G=$S/team-qa/scripts/check_upstream_gate.py; Q=$S/team-qa/scripts/validate_qa.py; N=$S/team-qa-evidence/scripts/normalize_evidence.py
F=team-board; RQ=docs/requirements/$F.md; SAD=docs/design/$F/sad.md; TS=docs/qa/$F/test-spec.md; TC=docs/qa/$F/test-cases.md
run(){ echo "\$ $*"; "$@" 2>&1 | grep -v 'WARN: dropped' | sed 's/^/  /'; echo "  exit=${PIPESTATUS[0]}"; }
echo "## (b) approved upstream without approved_sha256 (unmodified v0.4 artifacts)"
run python3 $G $RQ $SAD
run python3 $G --downstream $TS $RQ $SAD
echo "## (c) consistent state: stamp, record, fresh evidence"
run python3 $G --stamp-approved $RQ
run python3 $G --stamp-approved $SAD
run python3 $G --record $TS $RQ $SAD
run python3 $G --record $TC $RQ $SAD
run python3 $G --downstream $TS $RQ $SAD
run python3 $G --downstream $TC $RQ $SAD
run python3 $N evidence/2d29e13/junit/go.xml --format junit --tool go --output-dir $EV/go --commit 2d29e13 --executed-at 2026-10-13T10:15:00+08:00 --requirements $RQ --test-plan $TC
run python3 $Q docs/qa/$F --evidence-dir $EV
echo "## (a) stale state (requirement edited) and validate_qa without --evidence-dir"
sed -i 's/title (required, 1–120 chars trimmed)/title (required, 1–100 chars trimmed)/' $RQ
run python3 $G --downstream $TC $RQ $SAD
run python3 $Q docs/qa/$F
run python3 $Q docs/qa/$F --evidence-dir $EV
echo "## original repo: $(git -C "$ORIG" status --short | wc -l) changed files"
