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

HEAD=["## 1. Review Scope and Evidence","## 2. Engineering Standards Review","## 3. Requirement and Acceptance-Criteria Review","## 4. Architecture and Design Review","## 6. Findings","## 8. Test and CI Evidence","## 10. Human Review Summary"]
def main():
 if len(sys.argv)!=4:print('Usage: validate_review.py <docs/review/<slug>/code-review.md> <docs/requirements/<slug>.md> <docs/design/<slug>/sad.md>  (run from repo root)');return 2
 p=Path(sys.argv[1]); e=[]
 if not p.is_file():print("ERROR: missing review");return 1
 t=p.read_text(); gov(t,e); e+=approval_problems(p,t)
 for h in HEAD:
  if h not in t:e.append("missing heading: "+h)
 for k in ["base_commit","head_commit","review_target"]:
  if not fm(t,k):e.append("missing "+k)
 sev=re.findall(r"(?m)^- Severity:\s*(\S+)",t)
 for s in sev:
  if s not in {"blocker","major","minor","note","blocker|major|minor|note"} and not s.startswith("blocker"):e.append("invalid finding severity: "+s)
 # T-03: head_commit must be the reviewed code state
 import subprocess
 h=(fm(t,"head_commit") or "").split()[0] if fm(t,"head_commit") else ""
 if h and h!="unknown" and not h.startswith("<"):
  g=lambda *a: subprocess.run(["git",*a],capture_output=True,text=True)
  if g("rev-parse","--git-dir").returncode==0:
   if g("cat-file","-e",h+"^{commit}").returncode!=0:e.append(f"head_commit {h} is not a commit in this repository")
   else:
    later=g("log","--format=%h %s",f"{h}..HEAD","--","." ,":(exclude)docs",":(exclude)evidence",":(exclude).ai-workflow",":(exclude)reports").stdout.strip()
    if later:e.append(f"code changed after reviewed head_commit {h} (re-review the delta, then update head_commit): "+"; ".join(later.splitlines()[:5]))
 for x in e:print("ERROR:",x)
 if e:return 1
 print("OK: code review valid");return 0
if __name__=='__main__':raise SystemExit(main())