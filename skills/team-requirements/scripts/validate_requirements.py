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

HEAD=["## 1. Summary","## 2. Goals and non-goals","## 4. Scope","## 6. Functional requirements","## 7. Acceptance criteria","## 12. Open questions and blockers","## 13. Traceability"]
def main():
 if len(sys.argv)!=2:return 2
 p=Path(sys.argv[1]); e=[]
 if not p.is_file(): print("ERROR: file not found"); return 1
 t=p.read_text(); gov(t,e)
 if fm(t,"artifact_type") not in {"requirements",None}: e.append("artifact_type must be requirements")
 for h in HEAD:
  if h not in t:e.append("missing heading: "+h)
 req=set(re.findall(r"(?m)^###\s+(REQ-\d{3,})\b",t)); ac=set(re.findall(r"(?m)^###\s+(AC-\d{3,})\b",t))
 if not req:e.append("no REQ IDs")
 if not ac:e.append("no AC IDs")
 covers=set(re.findall(r"(?m)^- \*\*Covers:\*\*\s*(REQ-\d{3,})\b",t))
 if covers-req:e.append("AC references unknown REQ")
 for x in e:print("ERROR:",x)
 if e:return 1
 print("OK: requirements valid");return 0
if __name__=='__main__':raise SystemExit(main())