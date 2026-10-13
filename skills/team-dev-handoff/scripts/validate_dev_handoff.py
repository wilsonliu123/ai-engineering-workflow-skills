#!/usr/bin/env python3
import re,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from check_upstream_gate import approval_problems  # shared: approved_sha256, approved_by_role, author != approver
REQ=['artifact_id:','artifact_type: dev-handoff','artifact_status:','review_status:','validity:','risk_level:','source_commit:','dev_prep_baseline:']
HEAD=['## 1. Implemented scope','## 2. Implementation identity and changed areas','## 3. Traceability to TASK / REQ / AC','## 4. Design and standards conformance / deviations','## 5. Build, test, lint, typecheck and other executed evidence','## 8. Known limitations, risks and unfinished work','## 10. Handoff summary']
def main():
 p=Path(sys.argv[1]) if len(sys.argv)>1 else None
 if not p or not p.is_file(): print('ERROR: usage validate_dev_handoff.py <dev-delivery.md>'); return 2
 t=p.read_text(); e=approval_problems(p,t)
 for x in REQ:
  if x not in t:e.append('missing '+x)
 for x in HEAD:
  if x not in t:e.append('missing heading '+x)
 if re.search(r'(?mi)^review_status:\s*approved\b',t) and not re.search(r'(?mi)^approval_reference:\s*(?!null|none|pending)\S+',t): e.append('approved requires approval_reference')
 for x in e: print('ERROR:',x)
 if e:return 1
 print('OK: dev handoff valid'); return 0
if __name__=='__main__': raise SystemExit(main())
