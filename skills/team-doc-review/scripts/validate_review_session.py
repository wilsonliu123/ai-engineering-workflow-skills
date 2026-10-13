#!/usr/bin/env python3
import re,sys
from pathlib import Path
def fm(t,k):
 m=re.search(rf"(?m)^{re.escape(k)}:\s*(.+?)\s*$",t); return m.group(1).strip() if m else None
def main():
 if len(sys.argv)!=2:print('Usage: validate_review_session.py <docs/workflow/reviews/<id>.review.md>  (run from repo root)');return 2
 p=Path(sys.argv[1])
 if not p.is_file(): print('ERROR: file missing'); return 1
 t=p.read_text(); e=[]
 for k in ['artifact_id','artifact_path','artifact_baseline','review_session_status','final_decision']:
  if not fm(t,k):e.append('missing '+k+':')
 st=fm(t,'review_session_status'); fd=fm(t,'final_decision')
 if st and st not in {'in-progress','completed','stale'}:e.append('invalid review_session_status')
 if fd and fd not in {'pending','approved','changes-requested','rejected'}:e.append('invalid final_decision')
 if '## Resolved items' not in t:e.append('missing ## Resolved items')
 if st!='completed':
  # open sessions must be resumable (current_item + remaining queue); completed ones need neither
  if not fm(t,'current_item'):e.append('missing current_item: (required while session is open)')
  if '## Remaining items' not in t:e.append('missing ## Remaining items (required while session is open)')
 else:
  if fd=='pending':e.append('completed session needs a final_decision')
  if fd=='approved' and not fm(t,'approved_by_role'):e.append('approved session needs approved_by_role')
 if fm(t,'review_mode') and fm(t,'review_mode') not in {'one-at-a-time','batch'}:e.append('invalid review_mode')
 for x in e: print('ERROR:',x)
 if e:return 1
 print('OK: review session valid'); return 0
if __name__=='__main__':raise SystemExit(main())
