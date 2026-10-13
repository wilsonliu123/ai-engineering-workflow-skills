#!/usr/bin/env python3
"""Upstream gate: governance status + content staleness (sha256).

Usage:
  check_upstream_gate.py <upstream> [<upstream> ...]                       status gate (+ approved_sha256 check)
  check_upstream_gate.py --downstream <down.md> <upstream> [...]           also require each upstream to match
                                                                           the baseline recorded in <down.md>
  check_upstream_gate.py --record <down.md> <upstream> [...]               gate, then write `upstream:` baselines
                                                                           into <down.md> frontmatter
  check_upstream_gate.py --stamp-approved <artifact>                       write approved_sha256 at approval time
  check_upstream_gate.py --hash <path>                                     print content hash

The same content_hash() is imported by the validators and the QA evidence scripts, so every
recorded sha256 (approved_sha256, upstream:, evidence baseline) is comparable.

Approval rules for an `approved` upstream (structured fields, see common-artifact-governance.md):
  approved_sha256 must match content; approved_by_role is required; if approved_by_role equals
  owner_role (author approving own work) an independent_reviewer_role different from owner_role
  is required. An upstream that is not approved but carries `proceed_at_risk: <reference>`
  passes with AT-RISK (work may start); a downstream that is itself approved while any upstream
  is still at risk is BLOCKed in --downstream mode.

<upstream> is a primary .md artifact or a directory (e.g. docs/ux/<slug>/).
Content hash: YAML frontmatter of .md files is ignored (governance edits do not count).
sad.md / index.md hash their whole package directory (attachments included); a
directory hashes every file under it. Exit: 0 PASS, 1 BLOCK/STALE, 2 usage.
"""
import hashlib,re,sys
from pathlib import Path

PKG_PRIMARY={'sad.md','index.md'}
STATUS_OK={'artifact_status':'complete','review_status':'approved','validity':'current'}

def val(t,k):
 m=re.search(rf'(?m)^{re.escape(k)}:\s*(.+?)\s*$',t); return m.group(1).strip().strip('"\'') if m else None

def split_fm(t):
 m=re.match(r'---\r?\n(.*?)\r?\n---\r?\n?',t,re.S)
 return (m.group(1),t[m.end():]) if m else ('',t)

def body_bytes(f):
 b=f.read_bytes()
 if f.suffix=='.md':
  try: return split_fm(b.decode('utf-8'))[1].encode('utf-8')
  except UnicodeDecodeError: return b
 return b

def content_hash(p):
 p=Path(p)
 root=p.parent if p.is_file() and p.name in PKG_PRIMARY else p
 files=sorted(x for x in root.rglob('*') if x.is_file()) if root.is_dir() else [root]
 h=hashlib.sha256()
 for f in files:
  h.update((f.relative_to(root).as_posix() if root.is_dir() else f.name).encode()+b'\0')
  h.update(hashlib.sha256(body_bytes(f)).hexdigest().encode()+b'\n')
 return h.hexdigest()

def recorded(down):
 fm=split_fm(Path(down).read_text(encoding='utf-8'))[0]
 m=re.search(r'(?ms)^upstream:\s*\n((?:[ \t]+.*\n?)*)',fm)
 out={}
 if m:
  for path,sha in re.findall(r'-\s*path:\s*(\S+)\s*\n\s+sha256:\s*([0-9a-f]{64})',m.group(1)): out[path]=sha
 return out

def set_fm(f,key,block):
 t=Path(f).read_text(encoding='utf-8'); fm,body=split_fm(t)
 if not t.startswith('---'): raise SystemExit(f'ERROR: {f} has no YAML frontmatter')
 fm=re.sub(rf'(?ms)^{key}:.*?(?=^\S|\Z)','',fm+'\n').rstrip('\n')
 Path(f).write_text(f'---\n{fm}\n{block}\n---\n{body}',encoding='utf-8')

def norm(x): return re.sub(r'[^a-z0-9]','',(x or '').lower())

def approval_problems(p,t=None):
 """Shared approval checks (also used by stage validators). Returns BLOCK/STALE strings."""
 p=Path(p); t=t if t is not None else p.read_text(encoding='utf-8'); bad=[]
 if val(t,'review_status')!='approved': return bad
 ap=val(t,'approved_sha256')
 if not ap: bad.append(f'BLOCK: {p}: approved but no approved_sha256 (run check_upstream_gate.py --stamp-approved {p} once)')
 elif ap!=content_hash(p): bad.append(f'STALE: {p}: content changed after approval (approved_sha256 {ap[:12]} != current {content_hash(p)[:12]})')
 by=val(t,'approved_by_role')
 if not by or by.startswith('<'): bad.append(f'BLOCK: {p}: approved but no approved_by_role')
 elif norm(by)==norm(val(t,'owner_role')):
  ind=val(t,'independent_reviewer_role')
  if not ind or ind.startswith('<') or norm(ind)==norm(by): bad.append(f'BLOCK: {p}: author approved own artifact (approved_by_role == owner_role); set independent_reviewer_role to a different role that reviewed it')
 return bad

def at_risk(t):
 r=val(t,'proceed_at_risk')
 return r if r and r.lower() not in ('null','none','no','false') and not r.startswith('<') else None

def status_problems(p,risky=None):
 if not p.exists(): return [f'BLOCK: {p}: missing']
 if p.is_dir(): return []  # UX/asset directories carry no governance status
 t=p.read_text(encoding='utf-8'); bad=[]
 got={k:val(t,k) for k in STATUS_OK}
 if got!=STATUS_OK:
  if at_risk(t) and got['artifact_status']=='complete' and got['validity']=='current' and got['review_status'] in ('not-started','in-progress'):
   print(f'AT-RISK: {p}: not yet approved; proceeding under proceed_at_risk: {at_risk(t)}')
   if risky is not None: risky.append(p)
  else: bad.append(f'BLOCK: {p}: gate failed ({got})')
 return bad+approval_problems(p,t)

def main(a):
 if not a or a[0] in ('-h','--help'): print(__doc__); return 2
 if a[0]=='--hash' and len(a)==2: print(content_hash(a[1])); return 0
 if a[0]=='--stamp-approved' and len(a)==2:
  h=content_hash(a[1]); set_fm(a[1],'approved_sha256',f'approved_sha256: {h}'); print(f'STAMPED: {a[1]} approved_sha256={h}'); return 0
 mode=down=None
 if a[0] in ('--downstream','--record'):
  if len(a)<3: print(__doc__); return 2
  mode,down,a=a[0],Path(a[1]),a[2:]
 if any(x.startswith('--') for x in a): print(__doc__); return 2
 ups=[Path(x) for x in a]; bad=[]; risky=[]
 for p in ups: bad+=status_problems(p,risky)
 if mode=='--downstream':
  if not down.is_file(): bad.append(f'BLOCK: {down}: downstream missing')
  else:
   if risky and val(down.read_text(encoding='utf-8'),'review_status')=='approved': bad.append(f'BLOCK: {down}: approved while upstream still at risk ({", ".join(map(str,risky))}); approve upstream first')
   rec=recorded(down)
   for p in ups:
    if not p.exists(): continue
    k=p.as_posix()
    if k not in rec: bad.append(f'BLOCK: {down}: no recorded baseline for {k} (run --record)')
    elif rec[k]!=content_hash(p): bad.append(f'STALE: {k} changed since {down} recorded it ({rec[k][:12]} -> {content_hash(p)[:12]}); mark {down} validity: stale and re-assess')
 if bad:
  for x in bad: print(x)
  return 1
 if mode=='--record':
  if not down.is_file(): print(f'BLOCK: {down}: downstream missing'); return 1
  rec=recorded(down); rec.update({p.as_posix():content_hash(p) for p in ups})
  set_fm(down,'upstream','upstream:\n'+''.join(f'  - path: {k}\n    sha256: {v}\n' for k,v in rec.items()).rstrip('\n'))
  print(f'RECORDED: {len(ups)} upstream baseline(s) in {down}')
 print('PASS: all upstream artifacts are complete, approved, and current'+(' and match recorded baselines' if mode else ''))
 return 0
if __name__=='__main__': raise SystemExit(main(sys.argv[1:]))
