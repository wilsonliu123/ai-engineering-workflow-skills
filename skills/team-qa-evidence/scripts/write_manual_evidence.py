#!/usr/bin/env python3
import argparse, json
from pathlib import Path
from datetime import datetime, timezone

ALLOWED={"pass","fail","blocked","not-executed"}

def main():
 p=argparse.ArgumentParser()
 p.add_argument('--evidence-id',required=True);p.add_argument('--test-case-id',required=True)
 p.add_argument('--result',required=True,choices=sorted(ALLOWED));p.add_argument('--output',required=True)
 p.add_argument('--commit',default='unknown');p.add_argument('--build',default='unknown');p.add_argument('--environment',default='unknown')
 p.add_argument('--source-reference',default='manual-interview');p.add_argument('--defect-reference');p.add_argument('--notes',default='')
 a=p.parse_args()
 d={"evidence_id":a.evidence_id,"test_case_id":a.test_case_id,"source_type":"manual","source_tool":"manual","result":a.result,
    "target":{"commit":a.commit,"build":a.build,"environment":a.environment},"executed_at":datetime.now(timezone.utc).isoformat(),
    "source_reference":a.source_reference,"defect_reference":a.defect_reference,"metrics":{},"notes":a.notes[:500]}
 out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
 print(str(out));return 0
if __name__=='__main__':raise SystemExit(main())
