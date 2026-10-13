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

HEAD=["## 1. Purpose and Handoff Scope","## 2. Authoritative Inputs","## 5. Task Plan","## 6. Validation Baseline","## 7. Risks, Blockers, and Open Items"]
def main():
 if len(sys.argv)!=4:print('Usage: validate_dev_prep.py <docs/dev-prep/<slug>> <requirements.md> <sad.md>  (run from repo root)');return 2
 d=Path(sys.argv[1]); req=Path(sys.argv[2]); sad=Path(sys.argv[3]); idx=d/'index.md'; e=[]
 if not idx.is_file() or not req.is_file() or not sad.is_file():print("ERROR: missing input");return 1
 t=idx.read_text(); gov(t,e)
 for h in HEAD:
  if h not in t:e.append("missing heading: "+h)
 tasks=list((d/'tasks').glob('*.md')) if (d/'tasks').is_dir() else []
 if not tasks:e.append("no task briefs")
 tids=[]
 for p in tasks:
  x=p.read_text(); m=re.search(r"(?m)^task_id:\s*(TASK-\d{3})",x)
  if not m:e.append(p.name+" missing task_id")
  else:tids.append(m.group(1))
 if len(tids)!=len(set(tids)):e.append("duplicate task IDs")
 for x in e:print("ERROR:",x)
 if e:return 1
 print("OK: dev prep valid");return 0
if __name__=='__main__':raise SystemExit(main())