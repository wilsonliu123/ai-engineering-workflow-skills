#!/usr/bin/env python3
"""Append privacy-minimized workflow telemetry to a repo-local JSONL stream.

This utility is intentionally best-effort. It never edits engineering artifacts.
"""
from __future__ import annotations
import argparse, hashlib, json, os, subprocess, sys, uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "1.0"
ALLOWED_EVENTS = {
    "skill_started", "skill_completed", "artifact_created", "artifact_updated",
    "validation_passed", "validation_failed", "gate_passed", "gate_blocked",
    "artifact_became_stale", "review_started", "review_resumed", "review_interrupted",
    "review_item_presented", "review_item_decided", "review_completed", "status_changed",
    "change_requested", "finding_created", "qa_case_executed", "release_decision_recorded",
    "qa_requirements_reviewed", "qa_plan_completed", "qa_execution_assignment_started", "qa_execution_assigned",
    "qa_execution_handoff_created", "qa_evidence_import_started", "qa_evidence_normalized",
    "qa_manual_evidence_recorded", "qa_evidence_import_failed", "qa_final_assessment_completed",
}
ALLOWED_FIELDS = {
    "artifact_id", "artifact_type", "risk_level", "artifact_status", "review_status", "validity",
    "gate_result", "reason_code", "validation_result", "question_class", "decision", "final_decision",
    "review_cycle", "questions_presented", "changes_requested", "finding_severity", "finding_category",
    "test_case_id", "test_result", "release_alignment", "upstream_artifact_id", "upstream_artifact_type",
    "review_session_id", "result", "duration_ms", "count", "source_commit", "tested_commit", "candidate_id",
    "qa_mode", "execution_type", "evidence_source_type", "evidence_result", "evidence_count",
}
FORBIDDEN_KEY_FRAGMENTS = ("prompt", "message", "content", "diff", "secret", "token", "email", "customer")

def now_iso(): return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')

def repo_root() -> Path:
    p = Path.cwd().resolve()
    cp = subprocess.run(["git","-C",str(p),"rev-parse","--show-toplevel"], text=True, capture_output=True)
    if cp.returncode == 0 and cp.stdout.strip(): return Path(cp.stdout.strip())
    return p

def git_commit(root: Path) -> str:
    cp = subprocess.run(["git","-C",str(root),"rev-parse","HEAD"], text=True, capture_output=True)
    return cp.stdout.strip() if cp.returncode == 0 and cp.stdout.strip() else "unknown"

def fingerprint(root: Path) -> str:
    cp = subprocess.run(["git","-C",str(root),"config","--get","remote.origin.url"], text=True, capture_output=True)
    seed = cp.stdout.strip() if cp.returncode == 0 and cp.stdout.strip() else str(root)
    return hashlib.sha256(seed.encode()).hexdigest()[:16]

def load_cfg(root: Path):
    cfg={"enabled": True, "path": ".ai-workflow/telemetry/events.jsonl"}
    p=root/'.ai-workflow/config.json'
    if p.exists():
        try:
            raw=json.loads(p.read_text(encoding='utf-8'))
            cfg.update(raw.get('telemetry',{}))
        except Exception:
            pass
    env=os.environ.get('AI_WORKFLOW_TELEMETRY','').strip().lower()
    if env in {'0','false','off','disabled'}: cfg['enabled']=False
    return cfg

def parse_value(s: str):
    lo=s.lower()
    if lo in {'true','false'}: return lo=='true'
    if lo in {'null','none'}: return None
    try: return int(s)
    except ValueError: pass
    try: return float(s)
    except ValueError: return s[:160]

def parse_fields(items):
    out={}
    for item in items or []:
        if '=' not in item: raise ValueError(f"field must be key=value: {item}")
        k,v=item.split('=',1)
        k=k.strip()
        if k not in ALLOWED_FIELDS or any(x in k.lower() for x in FORBIDDEN_KEY_FRAGMENTS):
            raise ValueError(f"field not allowed: {k}")
        out[k]=parse_value(v.strip())
    return out

def append(root: Path, event: dict):
    cfg=load_cfg(root)
    if not cfg.get('enabled', True): return
    p=Path(cfg.get('path','.ai-workflow/telemetry/events.jsonl'))
    if not p.is_absolute(): p=root/p
    p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('a',encoding='utf-8') as f:
        f.write(json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(',',':'))+'\n')

def session_path(root: Path, sid: str):
    return root/'.ai-workflow/telemetry/sessions'/f'{sid}.json'

def make_base(root, sid, skill, ev, meta=None):
    meta = meta or {}
    return {"schema_version":SCHEMA_VERSION,"timestamp":now_iso(),"event_id":str(uuid.uuid4()),
            "session_id":sid,"event":ev,"skill":skill,"repo_fingerprint":meta.get('repo_fingerprint') or fingerprint(root),"git_commit":meta.get('git_commit') or git_commit(root)}

def cmd_start(a):
    root=repo_root(); cfg=load_cfg(root)
    if not cfg.get('enabled', True):
        print('telemetry-disabled')
        return
    sid=a.session_id or f"{a.skill}-{uuid.uuid4().hex[:12]}"
    fields=parse_fields(a.field)
    if a.artifact_id: fields['artifact_id']=a.artifact_id
    if a.artifact_type: fields['artifact_type']=a.artifact_type
    if a.risk_level: fields['risk_level']=a.risk_level
    st={"session_id":sid,"skill":a.skill,"started_at":now_iso(),"start_epoch_ms":int(datetime.now(timezone.utc).timestamp()*1000),"fields":fields,"repo_fingerprint":fingerprint(root),"git_commit":git_commit(root)}
    sp=session_path(root,sid); sp.parent.mkdir(parents=True,exist_ok=True); sp.write_text(json.dumps(st,sort_keys=True),encoding='utf-8')
    e=make_base(root,sid,a.skill,'skill_started',st); e.update(fields); append(root,e); print(sid)

def load_session(root,sid):
    p=session_path(root,sid)
    if not p.exists(): raise FileNotFoundError(f"telemetry session not found: {sid}")
    return json.loads(p.read_text(encoding='utf-8')),p

def cmd_event(a):
    if a.session_id == 'telemetry-disabled': return
    if a.event not in ALLOWED_EVENTS or a.event in {'skill_started','skill_completed'}: raise ValueError('unsupported event for event command')
    root=repo_root(); st,_=load_session(root,a.session_id); e=make_base(root,a.session_id,st['skill'],a.event,st); e.update(st.get('fields',{})); e.update(parse_fields(a.field)); append(root,e)

def cmd_complete(a):
    if a.session_id == 'telemetry-disabled': return
    root=repo_root(); st,p=load_session(root,a.session_id); now_ms=int(datetime.now(timezone.utc).timestamp()*1000)
    fields=parse_fields(a.field); fields['result']=a.result; fields['duration_ms']=max(0,now_ms-int(st.get('start_epoch_ms',now_ms)))
    e=make_base(root,a.session_id,st['skill'],'skill_completed',st); e.update(st.get('fields',{})); e.update(fields); append(root,e)
    st['completed_at']=now_iso(); st['result']=a.result; st['duration_ms']=fields['duration_ms']; p.write_text(json.dumps(st,sort_keys=True),encoding='utf-8')

def main():
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='cmd',required=True)
    s=sub.add_parser('start'); s.add_argument('--skill',required=True); s.add_argument('--session-id'); s.add_argument('--artifact-id'); s.add_argument('--artifact-type'); s.add_argument('--risk-level'); s.add_argument('--field',action='append',default=[])
    e=sub.add_parser('event'); e.add_argument('--session-id',required=True); e.add_argument('--event',required=True); e.add_argument('--field',action='append',default=[])
    c=sub.add_parser('complete'); c.add_argument('--session-id',required=True); c.add_argument('--result',choices=['success','blocked','failed'],required=True); c.add_argument('--field',action='append',default=[])
    a=ap.parse_args()
    try:
        {'start':cmd_start,'event':cmd_event,'complete':cmd_complete}[a.cmd](a)
    except Exception as exc:
        # Callers must treat this as a telemetry warning, not a lifecycle failure.
        print(f"telemetry warning: {type(exc).__name__}: {exc}",file=sys.stderr)
        return 2
    return 0
if __name__=='__main__': raise SystemExit(main())
