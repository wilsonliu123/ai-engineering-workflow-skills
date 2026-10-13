#!/usr/bin/env python3
"""Summarize repo-local workflow telemetry without reading engineering document contents."""
from __future__ import annotations
import argparse, json
from collections import Counter, defaultdict
from pathlib import Path

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--events',default='.ai-workflow/telemetry/events.jsonl'); ap.add_argument('--output'); a=ap.parse_args()
    p=Path(a.events); events=[]
    if p.exists():
        for n,line in enumerate(p.read_text(encoding='utf-8').splitlines(),1):
            if not line.strip(): continue
            try: events.append(json.loads(line))
            except Exception: pass
    skill_complete=[e for e in events if e.get('event')=='skill_completed']
    by_skill=Counter(e.get('skill','unknown') for e in skill_complete)
    results=Counter(e.get('result','unknown') for e in skill_complete)
    gate_blocks=Counter(e.get('reason_code','unspecified') for e in events if e.get('event')=='gate_blocked')
    review_presented=[e for e in events if e.get('event')=='review_item_presented']
    review_decided=[e for e in events if e.get('event')=='review_item_decided']
    review_completed=[e for e in events if e.get('event')=='review_completed']
    findings=Counter((e.get('finding_severity','unknown'),e.get('finding_category','unknown')) for e in events if e.get('event')=='finding_created')
    qa=Counter(e.get('test_result','unknown') for e in events if e.get('event')=='qa_case_executed')
    qa_assign=Counter(e.get('execution_type','unknown') for e in events if e.get('event')=='qa_execution_assigned')
    qa_evidence=Counter(e.get('evidence_result','unknown') for e in events if e.get('event') in {'qa_evidence_normalized','qa_manual_evidence_recorded'})
    stale=sum(1 for e in events if e.get('event')=='artifact_became_stale')
    interrupted=sum(1 for e in events if e.get('event')=='review_interrupted')
    durations=[e.get('duration_ms') for e in skill_complete if isinstance(e.get('duration_ms'),(int,float))]
    starts=[e for e in events if e.get('event')=='skill_started']
    repos=set(e.get('repo_fingerprint') for e in events if e.get('repo_fingerprint'))
    changes_requested=sum(1 for e in events if e.get('event')=='change_requested')
    data={
      'events':len(events),'skill_completions':sum(by_skill.values()),'skills':dict(by_skill),'results':dict(results),
      'human_load':{'review_items_presented':len(review_presented),'review_items_decided':len(review_decided),'reviews_completed':len(review_completed),'reviews_interrupted':interrupted,
                    'avg_questions_per_completed_review': round(len(review_presented)/len(review_completed),2) if review_completed else None},
      'flow_quality':{'gate_blocked':sum(gate_blocks.values()),'gate_blocked_reasons':dict(gate_blocks),'artifacts_became_stale':stale,'changes_requested':changes_requested},
      'engineering_quality':{'findings':[{"severity":k[0],"category":k[1],"count":v} for k,v in sorted(findings.items())],'qa_results':dict(qa)},
      'adoption':{'unique_repositories':len(repos),'skill_starts':len(starts),'skill_starts_by_name':dict(Counter(e.get('skill','unknown') for e in starts)),'review_interruptions':interrupted},
      'runtime':{'avg_skill_duration_ms':round(sum(durations)/len(durations),1) if durations else None},
    }
    out=json.dumps(data,ensure_ascii=False,indent=2,sort_keys=True)
    if a.output: Path(a.output).write_text(out+'\n',encoding='utf-8')
    else: print(out)
if __name__=='__main__': main()
