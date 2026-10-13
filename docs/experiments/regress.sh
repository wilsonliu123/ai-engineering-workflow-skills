#!/usr/bin/env bash
# Run every validator/gate against a TeamBoard checkout. Usage: regress.sh <skills-dir> <team-board-dir>
S=$(cd "$1" && pwd); cd "$2" || exit 2
F=team-board; RQ=docs/requirements/$F.md
run(){ echo "\$ $*"; "$@" 2>&1 | sed 's/^/  /'; echo "  exit=${PIPESTATUS[0]}"; }
run python3 $S/team-requirements/scripts/validate_requirements.py $RQ
run python3 $S/team-design/scripts/validate_design.py docs/design/$F $RQ
run python3 $S/team-dev-prep/scripts/validate_dev_prep.py docs/dev-prep/$F $RQ docs/design/$F/sad.md
run python3 $S/team-dev-handoff/scripts/validate_dev_handoff.py docs/dev-delivery/$F/dev-delivery.md
run python3 $S/team-review/scripts/validate_review.py docs/review/$F/code-review.md x y
run python3 $S/team-qa/scripts/validate_qa.py docs/qa/$F
run python3 $S/team-release/scripts/validate_release.py docs/release/$F/release-handoff.md
for r in docs/workflow/reviews/*.md; do run python3 $S/team-doc-review/scripts/validate_review_session.py $r; done
for g in team-design team-dev-prep team-dev-handoff team-review team-qa team-release; do
  run python3 $S/$g/scripts/check_upstream_gate.py $RQ docs/design/$F/sad.md docs/qa/$F/qa-report.md
done
run python3 $S/team-review/scripts/validate_review.py
run python3 $S/team-qa/scripts/validate_qa.py
