#!/usr/bin/env python3
import json,re,sys
from pathlib import Path

# --- content hash: keep identical in normalize_evidence.py, write_manual_evidence.py, validate_qa.py ---
import hashlib,re as _re
def _split_fm(t):
 m=_re.match(r'---\r?\n(.*?)\r?\n---\r?\n?',t,_re.S)
 return (m.group(1),t[m.end():]) if m else ('',t)
def content_hash(p):
 b=Path(p).read_bytes()
 if str(p).endswith('.md'): b=_split_fm(b.decode('utf-8'))[1].encode('utf-8')
 return hashlib.sha256(b).hexdigest()
def baseline(req,plan):
 """Evidence baseline: which requirements / test-cases content the run was judged against."""
 if not req or not plan: return None
 return {"requirements":{"path":req,"sha256":content_hash(req)},"test_plan":{"path":plan,"sha256":content_hash(plan)}}
# --- end content hash ---

def fm(text,key):
 m=re.search(rf"(?m)^{re.escape(key)}:\s*(.+?)\s*$",text); return m.group(1).strip() if m else None

def gov(text, errors):
 for k in ["artifact_status","validity"]:
  if not fm(text,k): errors.append(f"missing governance metadata: {k}")
 if fm(text,"artifact_status") not in {"draft","complete","superseded"}: errors.append("invalid artifact_status")
 if fm(text,"validity") not in {"current","stale","unknown"}: errors.append("invalid validity")
 if fm(text,"review_status") and fm(text,"review_status") not in {"not-started","in-progress","changes-requested","approved","rejected"}: errors.append("invalid review_status")
 if fm(text,"review_status")=="approved" and (fm(text,"approval_reference") or "").lower() in {"","null","none","pending"}: errors.append("approved artifact requires approval_reference")

def check_evidence(ev_dir, tested_commit, e):
 """P0-2: reject evidence whose baseline no longer matches, or that is void without a reason."""
 files=sorted(Path(ev_dir).rglob('*.json'))
 recs=[(f,json.loads(f.read_text())) for f in files]
 recs=[(f,d) for f,d in recs if isinstance(d,dict) and 'evidence_id' in d]
 if not recs: e.append(f'no normalized evidence under {ev_dir}'); return
 used=void=0
 for f,d in recs:
  if d.get('validity','current')=='void':
   void+=1
   if not d.get('void_reason'): e.append(f'{f}: void evidence needs void_reason')
   continue
  if d.get('validity','current')!='current': e.append(f'{f}: invalid evidence validity {d.get("validity")!r}'); continue
  b=d.get('baseline')
  if not b: e.append(f'{f}: evidence has no baseline (re-import with --requirements/--test-plan, or void it)'); continue
  for k in ('requirements','test_plan'):
   x=b.get(k) or {}
   if not x.get('path') or not Path(x['path']).is_file(): e.append(f'{f}: baseline {k} path missing: {x.get("path")}')
   elif content_hash(x['path'])!=x.get('sha256'): e.append(f'{f}: STALE evidence: {x["path"]} changed since this evidence was produced; void it or re-run')
  c=(d.get('target') or {}).get('commit') or 'unknown'
  if tested_commit and c!='unknown' and not (c.startswith(tested_commit) or tested_commit.startswith(c)): e.append(f'{f}: evidence commit {c} != tested_commit {tested_commit}')
  used+=1
 print(f'INFO: evidence usable={used} void(excluded)={void}')

def main():
 a=sys.argv[1:]; ev=None
 if '--evidence-dir' in a:
  i=a.index('--evidence-dir'); ev=a[i+1] if i+1<len(a) else None; a=a[:i]+a[i+2:]
 if len(a)!=1 or ev=='' or (ev is None and '--evidence-dir' in sys.argv):
  print('Usage: validate_qa.py <docs/qa/<feature-slug>> [--evidence-dir <normalized-evidence-dir>]  (run from repo root)'); return 2
 root=Path(a[0]); e=[]
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
  if not fm(t,'decided_by') or fm(t,'decided_by').startswith('<'):print('WARN: execution-matrix has no decided_by (who chose execution types)')
 if paths['execution-run.md'].is_file():
  t=paths['execution-run.md'].read_text();gov(t,e)
  if not fm(t,'tested_commit') and not fm(t,'tested_build'):e.append('execution-run needs candidate identity')
 if paths['qa-report.md'].is_file():
  t=paths['qa-report.md'].read_text();gov(t,e)
  for h in ['## 1. Tested identity and scope','## 2. Execution summary','## 3. Test results','## 4. Acceptance coverage','## 10. QA conclusion for human review']:
   if h not in t:e.append('qa-report missing '+h)
  if not fm(t,'tested_commit') and not fm(t,'tested_build'):e.append('qa-report needs tested identity')
 if ev:
  tc=None
  for n in ('qa-report.md','execution-run.md'):
   if paths[n].is_file(): tc=fm(paths[n].read_text(),'tested_commit'); break
  check_evidence(ev,(tc or '').split()[0] if tc else None,e)
 for x in e:print('ERROR:',x)
 if e:return 1
 print('OK: QA artifacts valid');return 0
if __name__=='__main__':raise SystemExit(main())
