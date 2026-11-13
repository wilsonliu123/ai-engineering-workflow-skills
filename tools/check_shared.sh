#!/usr/bin/env bash
# Shared files are intentionally duplicated per skill so each skill zip is self-contained.
# This check fails if any copy drifts or if a skill is missing a required shared file.
set -euo pipefail
cd "$(dirname "$0")/../skills" || exit 2
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
exit $rc
