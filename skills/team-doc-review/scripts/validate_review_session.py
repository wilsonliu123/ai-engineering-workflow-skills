#!/usr/bin/env python3
import re,sys
from pathlib import Path
def main():
 if len(sys.argv)!=2:print('Usage: validate_review_session.py <docs/workflow/reviews/<id>.review.md>  (run from repo root)');return 2
 p=Path(sys.argv[1]);
 if not p.is_file(): print('ERROR: file missing'); return 1
 t=p.read_text(); e=[]
 for x in ['artifact_id:','artifact_path:','artifact_baseline:','review_session_status:','current_item:','final_decision:','## Resolved items','## Remaining items']:
  if x not in t:e.append('missing '+x)
 for x in e: print('ERROR:',x)
 if e:return 1
 print('OK: review session valid'); return 0
if __name__=='__main__':raise SystemExit(main())
