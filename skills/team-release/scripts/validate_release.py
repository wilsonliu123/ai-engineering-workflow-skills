#!/usr/bin/env python3
import re,sys
from pathlib import Path
def fm(text,key):
 m=re.search(rf"(?m)^{re.escape(key)}:\s*(.+?)\s*$",text); return m.group(1).strip() if m else None
def gov(text, errors):
 for k in ["artifact_status","review_status","validity","risk_level"]:
  if not fm(text,k): errors.append(f"missing governance metadata: {k}")
 if fm(text,"artifact_status") not in {"draft","complete","superseded"}: errors.append("invalid artifact_status")
 if fm(text,"review_status") not in {"not-started","in-progress","changes-requested","approved","rejected"}: errors.append("invalid review_status")
 if fm(text,"validity") not in {"current","stale","unknown"}: errors.append("invalid validity")
 if fm(text,"risk_level") not in {"low","medium","high","critical","<low|medium|high|critical>"}: errors.append("invalid risk_level")
 if fm(text,"review_status")=="approved" and (fm(text,"approval_reference") or "").lower() in {"","null","none","pending"}: errors.append("approved artifact requires approval_reference")

HEAD=["## 1. Delivery intent and scope","## 2. Release candidate identity","## 4. Review evidence","## 5. QA evidence","## 8. Known limitations, standards exceptions, and residual risk","## 10. Human review / acceptance records","## 11. Handoff summary"]
def main():
 if len(sys.argv)!=2:print('Usage: validate_release.py <release-handoff.md>  (run from repo root)');return 2
 p=Path(sys.argv[1]);e=[]
 if not p.is_file():print('ERROR: missing release');return 1
 t=p.read_text();gov(t,e)
 for h in HEAD:
  if h not in t:e.append('missing heading: '+h)
 if not fm(t,'release_candidate'):e.append('missing release_candidate')
 for x in e:print('ERROR:',x)
 if e:return 1
 print('OK: release valid');return 0
if __name__=='__main__':raise SystemExit(main())