#!/usr/bin/env python3
import re,sys
from pathlib import Path

def val(t,k):
 m=re.search(rf'(?m)^{re.escape(k)}:\s*(.+?)\s*$',t); return m.group(1).strip() if m else None

def main():
 if len(sys.argv)<2:
  print('Usage: check_upstream_gate.py <artifact.md> [artifact.md ...]'); return 2
 bad=[]
 for raw in sys.argv[1:]:
  p=Path(raw)
  if not p.is_file(): bad.append(f'{p}: missing'); continue
  t=p.read_text(encoding='utf-8')
  got={k:val(t,k) for k in ['artifact_status','review_status','validity']}
  if got != {'artifact_status':'complete','review_status':'approved','validity':'current'}:
   bad.append(f"{p}: gate failed ({got})")
 if bad:
  for x in bad: print('BLOCK:',x)
  return 1
 print('PASS: all upstream artifacts are complete, approved, and current'); return 0
if __name__=='__main__': raise SystemExit(main())
