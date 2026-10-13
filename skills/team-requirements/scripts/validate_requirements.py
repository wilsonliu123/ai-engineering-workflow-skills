#!/usr/bin/env python3
import re,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from check_upstream_gate import approval_problems, validity_problems  # shared: approved_sha256, approved_by_role, author != approver
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
 if len(sys.argv)!=2:print('Usage: validate_requirements.py <docs/requirements/<slug>.md>  (run from repo root)');return 2
 p=Path(sys.argv[1]); e=[]
 if not p.is_file(): print("ERROR: file not found"); return 1
 t=p.read_text(); gov(t,e); e+=approval_problems(p,t)+validity_problems(p,t)
 if fm(t,"artifact_type") not in {"requirements",None}: e.append("artifact_type must be requirements")
 for h in HEAD:
  if h not in t:e.append("missing heading: "+h)
 req=set(re.findall(r"(?m)^###\s+(REQ-\d{3,})\b",t)); ac=set(re.findall(r"(?m)^###\s+(AC-\d{3,})\b",t))
 if not req:e.append("no REQ IDs")
 if not ac:e.append("no AC IDs")
 if not fm(t,"feature"):e.append("missing feature in frontmatter")
 # AC -> REQ (multi-value Covers, F-05/F-14)
 ac_cov={}
 for m in re.finditer(r"(?ms)^###\s+(AC-\d{3,})\b(.*?)(?=^##)",t):
  c=re.search(r"(?m)^- \*\*Covers:\*\*\s*(.+)$",m.group(2))
  ac_cov[m.group(1)]=set(re.findall(r"REQ-\d{3,}",c.group(1))) if c else set()
  if not ac_cov[m.group(1)]:e.append(f"{m.group(1)} has no Covers: REQ")
  for r in ac_cov[m.group(1)]-req:e.append(f"{m.group(1)} covers unknown {r}")
 # Must REQ needs >=1 AC
 for m in re.finditer(r"(?ms)^###\s+(REQ-\d{3,})\b(.*?)(?=^##)",t):
  if re.search(r"(?m)^- \*\*Priority:\*\*\s*Must",m.group(2)) and not any(m.group(1) in v for v in ac_cov.values()):e.append(f"Must requirement {m.group(1)} has no acceptance criterion")
 # §13 traceability must match Covers
 sec=re.search(r"(?ms)^## 13\. Traceability(.*?)(?=^## |\Z)",t)
 if sec:
  rows={}
  for line in sec.group(1).splitlines():
   c=[x.strip() for x in line.strip().strip("|").split("|")]
   if c and re.fullmatch(r"REQ-\d{3,}",c[0]):rows[c[0]]=set(re.findall(r"AC-\d{3,}",c[1] if len(c)>1 else ""))
  for r in sorted(req):
   want={a for a,v in ac_cov.items() if r in v}
   if r not in rows:e.append(f"traceability table missing {r}")
   elif rows[r]!=want:e.append(f"traceability {r} lists {sorted(rows[r])} but Covers give {sorted(want)}")
 # Blocking OQ => draft (F-04)
 if fm(t,"artifact_status")=="complete" and re.search(r"(?mi)^- OQ-\d+:.*Blocking:\s*Yes\b",t):e.append("Blocking: Yes open question requires artifact_status: draft")
 if fm(t,"document_type"):print("WARN: document_type is redundant; use artifact_type only")
 for x in e:print("ERROR:",x)
 if e:return 1
 print("OK: requirements valid");return 0
if __name__=='__main__':raise SystemExit(main())