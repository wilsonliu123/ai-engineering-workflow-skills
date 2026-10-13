#!/usr/bin/env python3
import re,sys
from pathlib import Path

def fm(text,key):
 m=re.search(rf"(?m)^{re.escape(key)}:\s*(.+?)\s*$",text); return m.group(1).strip() if m else None

def gov(text, errors):
 for k in ["artifact_status","validity"]:
  if not fm(text,k): errors.append(f"missing governance metadata: {k}")
 if fm(text,"artifact_status") not in {"draft","complete","superseded"}: errors.append("invalid artifact_status")
 if fm(text,"validity") not in {"current","stale","unknown"}: errors.append("invalid validity")
 if fm(text,"review_status") and fm(text,"review_status") not in {"not-started","in-progress","changes-requested","approved","rejected"}: errors.append("invalid review_status")
 if fm(text,"review_status")=="approved" and (fm(text,"approval_reference") or "").lower() in {"","null","none","pending"}: errors.append("approved artifact requires approval_reference")

def main():
 if len(sys.argv)<2:return 2
 root=Path(sys.argv[1]); e=[]
 paths={n:root/n for n in ['requirements-testability.md','test-spec.md','test-cases.md','execution-matrix.md','execution-run.md','qa-report.md']}
 if not any(p.is_file() for p in paths.values()): e.append('no QA artifacts')
 if paths['requirements-testability.md'].is_file():
  t=paths['requirements-testability.md'].read_text(); gov(t,e)
  for h in ['## 2. Testability summary','## 3. Blocking gaps','## 5. Non-functional criteria needing measurable targets']:
   if h not in t:e.append('requirements-testability missing '+h)
 if paths['test-spec.md'].is_file():
  t=paths['test-spec.md'].read_text();gov(t,e)
  for h in ['## 1. Purpose and scope','## 2. Risk assessment','## 5. Acceptance coverage plan','## 6. Entry criteria','## 7. Completion criteria']:
   if h not in t:e.append('test-spec missing '+h)
 if paths['test-cases.md'].is_file():
  t=paths['test-cases.md'].read_text()
  if '## Coverage index' not in t:e.append('test-cases missing coverage index')
  if re.search(r'(?im)^- \*\*Execution assignment:\*\*\s*(manual|automated|hybrid)\s*$',t): e.append('test-cases must not contain AI-selected execution assignment before human assignment')
 if paths['execution-matrix.md'].is_file():
  t=paths['execution-matrix.md'].read_text();gov(t,e)
  if '# QA Execution Matrix' not in t:e.append('execution-matrix missing title')
  if 'Human decision' not in t:e.append('execution-matrix must record human decision')
 if paths['execution-run.md'].is_file():
  t=paths['execution-run.md'].read_text();gov(t,e)
  if not fm(t,'tested_commit') and not fm(t,'tested_build'):e.append('execution-run needs candidate identity')
 if paths['qa-report.md'].is_file():
  t=paths['qa-report.md'].read_text();gov(t,e)
  for h in ['## 1. Tested identity and scope','## 2. Execution summary','## 3. Test results','## 4. Acceptance coverage','## 10. QA conclusion for human review']:
   if h not in t:e.append('qa-report missing '+h)
  if not fm(t,'tested_commit') and not fm(t,'tested_build'):e.append('qa-report needs tested identity')
 for x in e:print('ERROR:',x)
 if e:return 1
 print('OK: QA artifacts valid');return 0
if __name__=='__main__':raise SystemExit(main())
