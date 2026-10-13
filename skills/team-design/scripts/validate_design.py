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

HEAD=["## 1. Design summary","## 2. Current-state context","## 3. Proposed architecture","## 4. Data and interfaces","## 7. Design decisions and alternatives","## 8. Risks, assumptions, and open questions","## 10. Design validation points"]
def main():
 if len(sys.argv)!=3:print('Usage: validate_design.py <docs/design/<slug>> <docs/requirements/<slug>.md>  (run from repo root)');return 2
 d=Path(sys.argv[1]); rp=Path(sys.argv[2]); s=d/'sad.md'; e=[]
 if not s.is_file() or not rp.is_file():print("ERROR: missing sad or requirements");return 1
 t=s.read_text(); r=rp.read_text(); gov(t,e)
 for h in HEAD:
  if h not in t:e.append("missing heading: "+h)
 req=set(re.findall(r"\bREQ-[A-Z0-9_-]+\b",r)); ac=set(re.findall(r"\bAC-[A-Z0-9_-]+\b",r))
 if req and not (set(re.findall(r"\bREQ-[A-Z0-9_-]+\b",t))&req):e.append("SAD lacks REQ traceability")
 if ac and not (set(re.findall(r"\bAC-[A-Z0-9_-]+\b",t))&ac):e.append("SAD lacks AC traceability")
 for x in e:print("ERROR:",x)
 if e:return 1
 print("OK: design valid");return 0
if __name__=='__main__':raise SystemExit(main())