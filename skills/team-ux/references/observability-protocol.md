# Workflow Observability Protocol (v0.3)

Workflow telemetry is a **non-authoritative, best-effort side channel** for measuring how the engineering workflow behaves. It must never change product semantics, artifact content, approval authority, stage-gate decisions, or validation outcomes.

## Storage

By default write raw events to:

```text
.ai-workflow/telemetry/events.jsonl
```

Create the directory on demand. Session state used only for telemetry timing/correlation may be stored under:

```text
.ai-workflow/telemetry/sessions/
```

Raw telemetry should normally be ignored by Git. A repository may override the location through `.ai-workflow/config.json`.

## Safety and privacy

Never record:
- prompt or conversation text,
- source code or code diffs,
- requirement/design/document prose,
- secrets, credentials, tokens, personal names, emails, or customer data,
- full stack traces that may contain source or secret material.

Record only structured metadata needed for workflow measurement: IDs, artifact type, stage, timestamps, status transitions, risk level, counts, categories, reason codes, test-case IDs/results, finding severity/category, and version-alignment state.

## Non-blocking rule

Telemetry failure is never a stage failure. If the event writer cannot write an event, continue the owning skill's normal work. Surface a concise telemetry warning only when useful. Do not change artifact status, human-review status, validity, or gate decisions because telemetry is unavailable.

## Event writer

Use the bundled deterministic writer:

```bash
python scripts/emit_event.py start --skill <skill-name> [--artifact-id <id>] [--artifact-type <type>] [--risk-level <level>]
python scripts/emit_event.py event --session-id <session-id> --event <event-name> [--field key=value ...]
python scripts/emit_event.py complete --session-id <session-id> --result <success|blocked|failed> [--field key=value ...]
```

The `start` command prints the session ID and stores it in `.ai-workflow/telemetry/current-session`; `event` and `complete` (alias `end`) use it when `--session-id` is omitted. Run the script by its path inside the skill (e.g. `python3 <skill-dir>/scripts/emit_event.py`) from the repository root. On failure it prints `telemetry warning: ...` to stderr and exits 2; report the warning, never stop the stage. Always attempt `complete` before returning from a normally completed invocation.

## Common events

Allowed lifecycle events include:

- `skill_started`, `skill_completed`
- `artifact_created`, `artifact_updated`
- `validation_passed`, `validation_failed`
- `gate_passed`, `gate_blocked`
- `artifact_became_stale`
- `review_started`, `review_resumed`, `review_interrupted`
- `review_item_presented`, `review_item_decided`, `review_completed`
- `status_changed`, `change_requested`
- `finding_created`
- `qa_case_executed`
- `release_decision_recorded`

## Minimum emission points by stage

### team-requirements
Emit start/completion; artifact created/updated; validation outcome; glossary update when one occurs (as `artifact_updated` with `artifact_type=glossary`); stale-candidate detection as `artifact_became_stale`; and review handoff when review begins.

### team-design
Emit start/completion; upstream gate result; design artifact created/updated; ADR creation/update as `artifact_created`/`artifact_updated` with `artifact_type=adr`; validation outcome; and review handoff.

### team-dev-prep
Emit start/completion; upstream gate result; dev-prep artifact created/updated; validation outcome; and review handoff.

### team-dev-handoff
Emit start/completion; upstream gate result; delivery artifact created/updated; evidence/validation outcome; and review handoff when document review is required.

### team-review
Emit start/completion; upstream gate result; review artifact created/updated; each material finding as `finding_created` using severity/category only; validation outcome; and human decision handoff.

### team-qa
Emit start/completion; upstream gate result; test-spec/QA-report artifact writes; each executed test case as `qa_case_executed` with test-case ID and result only; validation outcome; and human acceptance handoff.

### team-release
Emit start/completion; upstream gate result; release-handoff artifact created/updated; version-alignment state; validation outcome; and explicit human release decision as `release_decision_recorded`.

### team-doc-review
Emit start/completion; `review_started` or `review_resumed`; one `review_item_presented` per human question; one `review_item_decided` after each explicit human answer; `change_requested` when applicable; `review_interrupted` if the session stops before final decision; `review_completed` after the human's final document-level decision; and `status_changed` when governance metadata is synchronized.

## Measurement guidance

Raw events are measurement evidence, not engineering source of truth. Derive metrics from events rather than asking people to reconstruct history manually. Use `scripts/summarize_metrics.py` when present to create a local summary.

## QA v0.4 events

Recommended additional events:

- `qa_requirements_reviewed`
- `qa_plan_completed`
- `qa_execution_assignment_started`
- `qa_execution_assigned`
- `qa_execution_handoff_created`
- `qa_evidence_import_started`
- `qa_evidence_normalized`
- `qa_manual_evidence_recorded`
- `qa_evidence_import_failed`
- `qa_final_assessment_completed`

For `qa_execution_assigned`, record only test-case ID, chosen execution class (`automated|manual|hybrid|deferred`), and role/tool category when non-sensitive. Do not record the human's identity. For evidence events, record IDs/counts/result categories, never raw reports or screenshots.
