#!/usr/bin/env bash
# v0.6.1 gate checks on a throw-away clone of know-base (never pushed). Usage: knowbase-gate.sh <skills-dir>
S=$(cd "$1" && pwd); G=$S/team-qa/scripts/check_upstream_gate.py; W=/tmp/kb-gate
rm -rf $W && gh repo clone wilsonliu123/know-base $W -- -q && cd $W || exit 2
r(){ echo "\$ check_upstream_gate.py $*"; python3 $G "$@" 2>&1 | sed 's/^/  /'; echo "  exit=${PIPESTATUS[0]}"; }
mkdir -p docs/design/know-base
printf -- '---\nartifact_id: DESIGN-know-base\nartifact_type: design\nfeature: know-base\nartifact_status: draft\nreview_status: not-started\nvalidity: current\nowner_role: Tech Lead\nrequirements: docs/requirements/know-base.md\n---\n# draft\n' > docs/design/know-base/sad.md
echo "## (a) design gate over docs/ux/know-base (README review_status: in-review)"
r --record docs/design/know-base/sad.md docs/requirements/know-base.md docs/ux/know-base
echo "## (a2) directory without README / missing directory"
cp -r docs/ux/know-base /tmp/kb-noreadme && rm /tmp/kb-noreadme/README.md; r /tmp/kb-noreadme; rm -rf /tmp/kb-noreadme; r docs/ux/missing
rm docs/design/know-base/sad.md
echo "## (b) test-spec --record while sad.md does not exist"
r --record docs/qa/know-base/test-spec.md docs/requirements/know-base.md docs/design/know-base/sad.md
sed -n '/^upstream:/,/^---/p' docs/qa/know-base/test-spec.md | sed 's/^/  /'
r --downstream docs/qa/know-base/test-spec.md docs/requirements/know-base.md
echo "## (b2) sad.md created and approved (test fixture), then re-record"
printf -- '---\nartifact_id: DESIGN-know-base\nartifact_type: design\nfeature: know-base\nartifact_status: complete\nreview_status: approved\nvalidity: current\nowner_role: Tech Lead\napproval_reference: fixture\napproved_by_role: PM\nrequirements: docs/requirements/know-base.md\n---\n# fixture\n' > docs/design/know-base/sad.md
python3 $G --stamp-approved docs/design/know-base/sad.md >/dev/null
r --downstream docs/qa/know-base/test-spec.md docs/requirements/know-base.md
r --record docs/qa/know-base/test-spec.md docs/requirements/know-base.md docs/design/know-base/sad.md
r --downstream docs/qa/know-base/test-spec.md docs/requirements/know-base.md
echo "## (c) committed test-spec declares design: but it is neither passed nor recorded"
git checkout -q docs/qa/know-base/test-spec.md
r --downstream docs/qa/know-base/test-spec.md docs/requirements/know-base.md
