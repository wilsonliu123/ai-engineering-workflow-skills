#!/usr/bin/env bash
# P2-5: shared files are duplicated per skill on purpose (each skill must be self-contained);
# this check fails if any copy drifts. Run before every commit.
cd "$(dirname "$0")/../skills" || exit 2; rc=0
for f in scripts/check_upstream_gate.py scripts/emit_event.py scripts/summarize_metrics.py references/common-artifact-governance.md references/observability-protocol.md references/review-protocol.md; do
  n=$(ls */$f 2>/dev/null | xargs -r md5sum | awk '{print $1}' | sort -u | wc -l)
  c=$(ls */$f 2>/dev/null | wc -l)
  if [ "$n" -gt 1 ]; then echo "DRIFT: $f ($n variants across $c copies)"; rc=1; else echo "OK: $f ($c identical copies)"; fi
done; exit $rc
