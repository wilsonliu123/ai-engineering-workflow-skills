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

HEAD=["## 1. Design summary","## 2. Current-state context","## 3. Proposed architecture","## 4. Data and interfaces","## 7. Design decisions and alternatives","## 8. Risks, assumptions, and open questions","## 10. Design validation points"]
def main():
 if len(sys.argv)!=3:print('Usage: validate_design.py <docs/design/<slug>> <docs/requirements/<slug>.md>  (run from repo root)');return 2
 d=Path(sys.argv[1]); rp=Path(sys.argv[2]); s=d/'sad.md'; e=[]
 if not s.is_file() or not rp.is_file():print("ERROR: missing sad or requirements");return 1
 t=s.read_text(); r=rp.read_text(); gov(t,e); e+=approval_problems(s,t)+validity_problems(s,t)
 for h in HEAD:
  if h not in t:e.append("missing heading: "+h)
 # review readiness (KnowBase #11): draft, Blocking OQ, or missing standards => not reviewable
 if fm(t,"artifact_status")=="draft":e.append("sad.md artifact_status is draft: not ready for review (set complete when review-ready)")
 if not Path("docs/shared/engineering-standards.md").is_file():e.append("docs/shared/engineering-standards.md missing: design must stay draft (team-design SKILL.md)")
 oq=re.search(r"(?ms)^### 8\.3.*?(?=^## |\Z)",t)
 for line in (oq.group(0).splitlines() if oq else []):
  if re.search(r"Blocking\s*[:：]\s*Yes",line,re.I):e.append("Blocking open question: "+line.strip()[:120])
 # full coverage: every REQ/AC id in the requirement must appear somewhere in the design package
 alltxt=t+"".join((d/a).read_text() for a in ['frontend.md','backend.md','interface.md'] if (d/a).is_file())
 for pre in ("REQ","AC"):
  ids=set(re.findall(rf"\b{pre}-[A-Z0-9_-]+\b",r)); miss=sorted(ids-set(re.findall(rf"\b{pre}-[A-Z0-9_-]+\b",alltxt)))
  if miss:e.append(f"design does not cover {pre} ids: "+", ".join(miss))
 # attachments (D-05/UX-05): every optional doc must carry feature + artifact_status
 for a in ['frontend.md','backend.md','interface.md']:
  ap=d/a
  if ap.is_file():
   at=ap.read_text()
   if not fm(at,'artifact_status'):e.append(f"{a} missing artifact_status")
   e+=validity_problems(ap,at)
 fe=d/'frontend.md'
 screens=sorted(set(re.findall(r"(?m)^\|\s*(S\d+[a-z]?)\s*\|",r)))
 if fe.is_file():
  ft=fe.read_text()
  for sid in screens:
   if not re.search(rf"\b{sid}\b",ft):e.append(f"frontend.md does not cover screen {sid}")
  if '### Situation coverage' in r and 'Situation matrix' not in ft:e.append("frontend.md lacks the situation matrix required by the requirement")
 # ADRs cited by an approved SAD must be approved (D-03)
 if fm(t,'review_status')=='approved':
  for adr in sorted(set(re.findall(r"\bADR-\d{3}\b",t))):
   hits=list(Path('docs/shared/adr').glob(adr+'*.md'))
   if hits and fm(hits[0].read_text(),'review_status')!='approved':e.append(f"{adr} cited by approved SAD is not approved ({hits[0]})")
 for x in e:print("ERROR:",x)
 if e:return 1
 print("OK: design valid");return 0
if __name__=='__main__':raise SystemExit(main())