#!/usr/bin/env python3
import re,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from check_upstream_gate import approval_problems  # shared: approved_sha256, approved_by_role, author != approver
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
 t=p.read_text();gov(t,e);e+=approval_problems(p,t)
 for h in HEAD:
  if h not in t:e.append('missing heading: '+h)
 if not fm(t,'release_candidate'):e.append('missing release_candidate')
 # release candidate must equal QA tested_commit and review head (when files are at standard paths)
 rc=(fm(t,'release_candidate') or '').split()[0] if fm(t,'release_candidate') else ''
 feat=fm(t,'feature')
 if rc and feat:
  for path,key in ((f'docs/qa/{feat}/qa-report.md','tested_commit'),):
   q=Path(path)
   if q.is_file():
    v=(fm(q.read_text(),key) or '').split()
    if v and not (v[0].startswith(rc) or rc.startswith(v[0])):e.append(f'release_candidate {rc} != {path} {key} {v[0]}')
 if '## 8.' in t and re.search(r'(?ms)^## 8\..*?(?=^## 9)',t) and not re.search(r'(?m)^\s*(\d+\.|-)\s+\S',re.search(r'(?ms)^## 8\..*?(?=^## 9|\Z)',t).group(0)):e.append('section 8 known limitations is empty (copy QA report 8.1)')
 for x in e:print('ERROR:',x)
 if e:return 1
 print('OK: release valid');return 0
if __name__=='__main__':raise SystemExit(main())