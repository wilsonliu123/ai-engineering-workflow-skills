#!/usr/bin/env python3
"""Mark normalized evidence as void (kept for history, excluded from final assessment).
Usage: void_evidence.py --reason "<why>" [--superseded-by EVD-xxx] <EVD-file.json> [...]"""
import argparse,json
from pathlib import Path
def main():
 p=argparse.ArgumentParser(usage=__doc__); p.add_argument('files',nargs='+'); p.add_argument('--reason',required=True); p.add_argument('--superseded-by')
 a=p.parse_args()
 for f in a.files:
  d=json.loads(Path(f).read_text()); d['validity']='void'; d['void_reason']=a.reason
  if a.superseded_by: d['superseded_by']=a.superseded_by
  Path(f).write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n'); print('VOID:',f)
 return 0
if __name__=='__main__': raise SystemExit(main())
