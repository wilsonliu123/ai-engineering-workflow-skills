#!/usr/bin/env python3
import argparse, json, re, sys, xml.etree.ElementTree as ET
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

def tc_id(s):
    m=re.search(r"\bTC-\d{3,}\b", s or "")
    return m.group(0) if m else None

def norm_status(s):
    x=(s or "").strip().lower().replace("_","-")
    mp={"passed":"pass","success":"pass","ok":"pass","failed":"fail","error":"fail","skipped":"blocked","skip":"blocked","not-run":"not-executed","notexecuted":"not-executed"}
    x=mp.get(x,x)
    return x if x in ALLOWED else None

def record(eid, tc, result, tool, src, args, metrics=None, notes="", defect=None, source_type="automated"):
    return {
        "evidence_id": eid,
        "test_case_id": tc,
        "source_type": source_type,
        "source_tool": tool,
        "result": result,
        "target": {"commit": args.commit, "build": args.build, "environment": args.environment},
        "executed_at": args.executed_at or datetime.now(timezone.utc).isoformat(),
        "source_reference": src,
        "defect_reference": defect,
        "metrics": metrics or {},
        "notes": notes[:500],
        "baseline": args.baseline,
        "validity": "current",
        "void_reason": None
    }

def parse_junit(path,args):
    root=ET.parse(path).getroot(); out=[]; i=1
    for case in root.iter("testcase"):
        tc=tc_id((case.get("name") or "")+" "+(case.get("classname") or ""))
        if not tc: continue
        if case.find("failure") is not None or case.find("error") is not None: st="fail"
        elif case.find("skipped") is not None: st="blocked"
        else: st="pass"
        out.append(record(f"EVD-{i:03d}",tc,st,args.tool or "junit",str(path),args))
        i+=1
    return out

def walk_json(obj):
    if isinstance(obj,dict):
        if "test_case_id" in obj and ("result" in obj or "status" in obj): yield obj
        for v in obj.values(): yield from walk_json(v)
    elif isinstance(obj,list):
        for v in obj: yield from walk_json(v)

def parse_json(path,args):
    data=json.loads(path.read_text()); out=[]; i=1
    for item in walk_json(data):
        tc=tc_id(str(item.get("test_case_id")))
        st=norm_status(str(item.get("result",item.get("status",""))))
        if not tc or not st: continue
        out.append(record(f"EVD-{i:03d}",tc,st,args.tool or str(item.get("source_tool","other")),str(path),args,
                          metrics=item.get("metrics") if isinstance(item.get("metrics"),dict) else {},
                          notes=str(item.get("notes","")),defect=item.get("defect_reference"),
                          source_type=str(item.get("source_type","automated"))))
        i+=1
    return out

def main():
    p=argparse.ArgumentParser()
    p.add_argument("input"); p.add_argument("--format",choices=["junit","json"],required=True)
    p.add_argument("--output-dir",required=True); p.add_argument("--tool")
    p.add_argument("--commit",default="unknown"); p.add_argument("--build",default="unknown"); p.add_argument("--environment",default="unknown"); p.add_argument("--executed-at")
    p.add_argument("--requirements",help="requirements primary artifact the run was judged against (baseline)")
    p.add_argument("--test-plan",help="test-cases.md the run was judged against (baseline)")
    a=p.parse_args(); path=Path(a.input)
    a.baseline=baseline(a.requirements,a.test_plan)
    if a.baseline is None: print("WARN: no --requirements/--test-plan; evidence has no baseline and validate_qa --evidence-dir will reject it",file=sys.stderr)
    try: rows=parse_junit(path,a) if a.format=="junit" else parse_json(path,a)
    except Exception as e:
        print(f"ERROR: {e}",file=sys.stderr); return 2
    if not rows:
        print("ERROR: no supported test-case evidence found",file=sys.stderr); return 3
    od=Path(a.output_dir); od.mkdir(parents=True,exist_ok=True)
    for i,r in enumerate(rows,1):
        r["evidence_id"]=f"EVD-{i:03d}"
        (od/f"EVD-{i:03d}.json").write_text(json.dumps(r,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({"normalized":len(rows),"output_dir":str(od)})); return 0
if __name__=="__main__": raise SystemExit(main())
