#!/usr/bin/env python3
import argparse, json
from pathlib import Path
from datetime import datetime, timezone

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

ALLOWED={"pass","fail","blocked","not-executed"}

def main():
 p=argparse.ArgumentParser()
 p.add_argument('--evidence-id',required=True);p.add_argument('--test-case-id',required=True)
 p.add_argument('--result',required=True,choices=sorted(ALLOWED));p.add_argument('--output',required=True)
 p.add_argument('--commit',default='unknown');p.add_argument('--build',default='unknown');p.add_argument('--environment',default='unknown')
 p.add_argument('--source-reference',default='manual-interview');p.add_argument('--defect-reference');p.add_argument('--notes',default='')
 p.add_argument('--requirements');p.add_argument('--test-plan')
 a=p.parse_args()
 d={"evidence_id":a.evidence_id,"test_case_id":a.test_case_id,"source_type":"manual","source_tool":"manual","result":a.result,
    "target":{"commit":a.commit,"build":a.build,"environment":a.environment},"executed_at":datetime.now(timezone.utc).isoformat(),
    "source_reference":a.source_reference,"defect_reference":a.defect_reference,"metrics":{},"notes":a.notes[:500],
    "baseline":baseline(a.requirements,a.test_plan),"validity":"current","void_reason":None}
 out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
 print(str(out));return 0
if __name__=='__main__':raise SystemExit(main())
