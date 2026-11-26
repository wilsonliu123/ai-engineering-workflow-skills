#!/usr/bin/env bash
# Shared files are intentionally duplicated per skill so each skill zip is self-contained.
# This check fails if any copy drifts or if a skill is missing a required shared file.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR/skills" || exit 2
rc=0
EXPECTED_SKILLS=8

for f in scripts/jira_sync.py references/common-workflow-governance.md; do
  files=(*/$f)
  c=${#files[@]}
  if [ "$c" -ne "$EXPECTED_SKILLS" ]; then
    echo "DRIFT: $f (found $c copies, expected $EXPECTED_SKILLS)"
    rc=1
    continue
  fi
  n=$(shasum -a 256 "${files[@]}" | awk '{print $1}' | sort -u | wc -l | tr -d ' ')
  if [ "$n" -gt 1 ]; then
    echo "DRIFT: $f ($n variants across $c copies)"
    rc=1
  else
    echo "OK: $f ($c identical copies)"
  fi
done

# Verify standalone read-only 9th skill: team-provenance-audit (never bundles jira_sync.py)
PA_DIR="team-provenance-audit"
if [ ! -f "$PA_DIR/SKILL.md" ]; then
  echo "MISSING: $PA_DIR/SKILL.md"
  rc=1
fi
if [ -f "$PA_DIR/scripts/jira_sync.py" ]; then
  echo "UNEXPECTED: $PA_DIR/scripts/jira_sync.py must not be bundled in standalone audit skill"
  rc=1
fi
for s in check_env.py static_scan.py fetch_reference.py fingerprint.py gate.py; do
  if [ ! -f "$PA_DIR/scripts/$s" ]; then
    echo "MISSING: $PA_DIR/scripts/$s"
    rc=1
  fi
done
if git -C "$ROOT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if [ -n "$(git -C "$ROOT_DIR" ls-files -- 'skills/team-provenance-audit/*__pycache__*' 'skills/team-provenance-audit/*.pyc')" ]; then
    echo "DIRTY: $PA_DIR has tracked __pycache__ or *.pyc in git index"
    rc=1
  fi
elif find "$PA_DIR" -name '__pycache__' -o -name '*.pyc' | grep -q .; then
  echo "DIRTY: $PA_DIR contains __pycache__ or *.pyc"
  rc=1
fi
if [ "$rc" -eq 0 ]; then
  echo "OK: $PA_DIR (standalone 9th skill, 5 scripts verified, clean)"
fi
exit $rc
