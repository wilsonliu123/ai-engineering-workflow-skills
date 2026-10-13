#!/usr/bin/env python3
import json,re,sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent))
from check_upstream_gate import content_hash, approval_problems  # one hash algorithm for gate, evidence and validate_qa
def baseline(req,plan,design):
 """Evidence baseline: requirements, test-cases and design package the run was judged against."""
 if not (req and plan and design): return None
 return {k:{"path":Path(v).as_posix(),"sha256":content_hash(v)} for k,v in (("requirements",req),("test_plan",plan),("design",design))}

def fm(text,key):
 m=re.search(rf"(?m)^{re.escape(key)}:\s*(.+?)\s*$",text); return m.group(1).strip() if m else None

def gov(text, errors):
 for k in ["artifact_status","validity"]:
  if not fm(text,k): errors.append(f"missing governance metadata: {k}")
 if fm(text,"artifact_status") not in {"draft","complete","superseded"}: errors.append("invalid artifact_status")
 if fm(text,"validity") not in {"current","stale","unknown"}: errors.append("invalid validity")
 if fm(text,"review_status") and fm(text,"review_status") not in {"not-started","in-progress","changes-requested","approved","rejected"}: errors.append("invalid review_status")
 if fm(text,"review_status")=="approved" and (fm(text,"approval_reference") or "").lower() in {"","null","none","pending"}: errors.append("approved artifact requires approval_reference")

RESULTS=['pass','fail','blocked','partial','not-executed']
def aggregate(rs):
 for r in ('fail','blocked','partial'):
  if r in rs: return r
 return 'pass' if 'pass' in rs else 'not-executed'

def check_evidence(ev_dir, tested_commit, e, out):
 """Reject evidence whose baseline no longer matches, void without reason, wrong commit, or conflicting (flaky)."""
 files=sorted(Path(ev_dir).rglob('*.json'))
 recs=[(f,json.loads(f.read_text())) for f in files]
 recs=[(f,d) for f,d in recs if isinstance(d,dict) and 'evidence_id' in d]
 if not recs: e.append(f'no normalized evidence under {ev_dir}'); return
 used=void=0
 for f,d in recs:
  if d.get('validity','current')=='void':
   void+=1; out['void'].append(f.relative_to(ev_dir).with_suffix('').as_posix())
   if not d.get('void_reason'): e.append(f'{f}: void evidence needs void_reason')
   continue
  if d.get('validity','current')!='current': e.append(f'{f}: invalid evidence validity {d.get("validity")!r}'); continue
  b=d.get('baseline')
  if not b: e.append(f'{f}: evidence has no baseline (re-import with --requirements/--test-plan/--design, or void it)'); continue
  for k in ('requirements','test_plan','design'):
   x=b.get(k) or {}
   if not x.get('path') or not Path(x['path']).is_file(): e.append(f'{f}: baseline {k} path missing: {x.get("path")}')
   elif content_hash(x['path'])!=x.get('sha256'): e.append(f'{f}: STALE evidence: {x["path"]} changed since this evidence was produced; void it or re-run')
  c=(d.get('target') or {}).get('commit') or 'unknown'
  if tested_commit and c!='unknown' and not (c.startswith(tested_commit) or tested_commit.startswith(c)): e.append(f'{f}: evidence commit {c} != tested_commit {tested_commit}')
  used+=1
  if d.get('result') not in RESULTS: e.append(f'{f}: invalid result {d.get("result")!r}'); continue
  out['by_tc'].setdefault(d.get('test_case_id'),[]).append((d.get('result'),c,f.relative_to(ev_dir).with_suffix('').as_posix()))
 for tcid,rs in out['by_tc'].items():
  for c in {x[1] for x in rs}:
   s={x[0] for x in rs if x[1]==c}
   if 'pass' in s and 'fail' in s: e.append(f'{tcid}: conflicting pass and fail on commit {c} (flaky?): rerun per the flaky rule and void superseded records')
 print(f'INFO: evidence usable={used} void(excluded)={void}')

def main():
 a=sys.argv[1:]; ev=None; table='--emit-table' in a
 a=[x for x in a if x!='--emit-table']
 if '--evidence-dir' in a:
  i=a.index('--evidence-dir'); ev=a[i+1] if i+1<len(a) else None; a=a[:i]+a[i+2:]
 if len(a)!=1 or ev=='' or (ev is None and '--evidence-dir' in sys.argv):
  print('Usage: validate_qa.py <docs/qa/<feature-slug>> --evidence-dir <normalized-evidence-dir> [--emit-table]   (run from repo root; --evidence-dir may be omitted only before qa-report.md exists; --emit-table prints the §3 results table and result_counts generated from evidence)'); return 2
 root=Path(a[0]); e=[]
 paths={n:root/n for n in ['requirements-testability.md','test-spec.md','test-cases.md','execution-matrix.md','execution-run.md','qa-report.md']}
 if not any(p.is_file() for p in paths.values()): e.append('no QA artifacts')
 for n,p in paths.items():  # KnowBase #12: stale/unknown artifacts are not reviewable
  if p.is_file() and fm(p.read_text(),'validity') in ('stale','unknown'): e.append(f'{n}: validity is {fm(p.read_text(),"validity")}; re-run check_upstream_gate.py --record and set validity: current before review')
 if paths['requirements-testability.md'].is_file():
  t=paths['requirements-testability.md'].read_text(); gov(t,e);e+=approval_problems(paths['requirements-testability.md'],t)
  for h in ['## 2. Testability summary','## 3. Blocking gaps','## 5. Non-functional criteria needing measurable targets']:
   if h not in t:e.append('requirements-testability missing '+h)
  c=re.search(r'(?mi)^[-*\s]*\**conclusion:?\**\s*`?(pass-with-conditions|pass|block)\b',t)
  if not c:e.append('requirements-testability needs "Conclusion: pass | pass-with-conditions | block" in §6')
  g=re.search(r'(?ms)^## 3\. Blocking gaps(.*?)(?=^## )',t)
  rows=[l for l in (g.group(1).splitlines() if g else []) if re.match(r'\s*\|\s*G-?\w+',l)]
  if c and c.group(1).lower()=='pass' and rows:e.append('requirements-testability lists blocking gaps but concludes pass')
 if paths['test-spec.md'].is_file():
  t=paths['test-spec.md'].read_text();gov(t,e);e+=approval_problems(paths['test-spec.md'],t)
  for h in ['## 1. Purpose and scope','## 2. Risk assessment','## 5. Acceptance coverage plan','## 6. Entry criteria','## 7. Completion criteria']:
   if h not in t:e.append('test-spec missing '+h)
 if paths['test-cases.md'].is_file():
  t=paths['test-cases.md'].read_text(); tcs=sorted(set(re.findall(r'(?m)^##\s+(TC-\d{3,})\b',t)))
  if '## Coverage index' not in t:e.append('test-cases missing coverage index')
  for k in ('artifact_id','review_status','artifact_status'):
   if not fm(t,k):e.append('test-cases missing '+k)
  rq=fm(t,'requirements')
  if rq and Path(rq.split()[0]).is_file():
   acs=set(re.findall(r'(?m)^###\s+(AC-\d{3,})\b',Path(rq.split()[0]).read_text()))
   for x in sorted(acs-set(re.findall(r'\bAC-\d{3,}\b',t))):e.append(f'{x} has no test case')
  if re.search(r'(?im)^- \*\*Execution assignment:\*\*\s*(manual|automated|hybrid)\s*$',t): e.append('test-cases must not contain AI-selected execution assignment before human assignment')
 if paths['execution-matrix.md'].is_file():
  t=paths['execution-matrix.md'].read_text();gov(t,e);e+=approval_problems(paths['execution-matrix.md'],t)
  if '# QA Execution Matrix' not in t:e.append('execution-matrix missing title')
  if 'Human decision' not in t:e.append('execution-matrix must record human decision')
  if not fm(t,'decided_by') or fm(t,'decided_by').startswith('<'):e.append('execution-matrix needs decided_by (who chose execution types)')
 if paths['execution-run.md'].is_file():
  t=paths['execution-run.md'].read_text();gov(t,e);e+=approval_problems(paths['execution-run.md'],t)
  if not fm(t,'tested_commit') and not fm(t,'tested_build'):e.append('execution-run needs candidate identity')
 if paths['qa-report.md'].is_file():
  t=paths['qa-report.md'].read_text();gov(t,e);e+=approval_problems(paths['qa-report.md'],t)
  for h in ['## 1. Tested identity and scope','## 2. Execution summary','## 3. Test results','## 4. Acceptance coverage','## 10. QA conclusion for human review']:
   if h not in t:e.append('qa-report missing '+h)
  if not fm(t,'tested_commit') and not fm(t,'tested_build'):e.append('qa-report needs tested identity')
 if paths['qa-report.md'].is_file() and not ev: e.append('qa-report.md exists: --evidence-dir <normalized-evidence-dir> is required (final-assessment must check evidence baselines)')
 if ev:
  tc=None; out={'void':[],'by_tc':{}}
  for n in ('qa-report.md','execution-run.md'):
   if paths[n].is_file(): tc=fm(paths[n].read_text(),'tested_commit'); break
  check_evidence(ev,(tc or '').split()[0] if tc else None,e,out)
  tcs=tcs if paths['test-cases.md'].is_file() else sorted(out['by_tc'])
  for x in sorted(set(out['by_tc'])-set(tcs)):e.append(f'evidence for unknown test case {x}')
  res={x:aggregate([r[0] for r in out['by_tc'].get(x,[])]) for x in tcs}
  counts={k:sum(1 for v in res.values() if v==k) for k in RESULTS}
  cl='result_counts: '+' '.join(f'{k}={counts[k]}' for k in RESULTS)
  if table:
   print('| Test case | Result | Evidence |\n|---|---|---|')
   for x in tcs: print(f"| {x} | {res[x]} | {', '.join(r[2] for r in out['by_tc'].get(x,[])) or '—'} |")
   print(cl)
  if paths['qa-report.md'].is_file():
   rt=paths['qa-report.md'].read_text()
   if (fm(rt,'result_counts') or '').split()!=cl.split()[1:]:e.append(f'qa-report result_counts ({fm(rt,"result_counts")}) != evidence ({cl[15:]}); regenerate with --emit-table')
   s8=re.search(r'(?ms)^## 8\..*?(?=^## 9|\Z)',rt); s8=s8.group(0) if s8 else ''
   for v in out['void']:
    if v not in s8:e.append(f'void evidence {v} not listed in qa-report §8.3')
 for x in e:print('ERROR:',x)
 if e:return 1
 print('OK: QA artifacts valid');return 0
if __name__=='__main__':raise SystemExit(main())
