#!/usr/bin/env python3
import argparse, json, sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0,str(Path(__file__).resolve().parent))
from check_upstream_gate import content_hash  # one hash algorithm for gate, evidence and validate_qa
def baseline(req,plan,design):
 """Evidence baseline: requirements, test-cases and design package the run was judged against."""
 if not (req and plan and design): return None
 return {k:{"path":Path(v).as_posix(),"sha256":content_hash(v)} for k,v in (("requirements",req),("test_plan",plan),("design",design))}

ALLOWED={"pass","fail","blocked","not-executed"}

def main():
 p=argparse.ArgumentParser()
 p.add_argument('--evidence-id',required=True);p.add_argument('--test-case-id',required=True)
 p.add_argument('--result',required=True,choices=sorted(ALLOWED));p.add_argument('--output',required=True)
 p.add_argument('--commit',default='unknown');p.add_argument('--build',default='unknown');p.add_argument('--environment',default='unknown')
 p.add_argument('--source-reference',default='manual-interview');p.add_argument('--defect-reference');p.add_argument('--notes',default='')
 p.add_argument('--requirements');p.add_argument('--test-plan');p.add_argument('--design')
 a=p.parse_args()
 d={"evidence_id":a.evidence_id,"test_case_id":a.test_case_id,"source_type":"manual","source_tool":"manual","result":a.result,
    "target":{"commit":a.commit,"build":a.build,"environment":a.environment},"executed_at":datetime.now(timezone.utc).isoformat(),
    "source_reference":a.source_reference,"defect_reference":a.defect_reference,"metrics":{},"notes":a.notes[:500],
    "baseline":baseline(a.requirements,a.test_plan,a.design),"validity":"current","void_reason":None}
 out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
 print(str(out));return 0
if __name__=='__main__':raise SystemExit(main())
