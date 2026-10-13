---
name: team-qa-evidence
description: "Normalize QA execution results into structured evidence records that team-qa can reliably assess. Use after real QA execution has occurred, either to import supported or explicitly mapped tool-generated reports (for example JUnit-style or generic JSON results from Playwright/pytest/API/performance toolchains) or to interview a human QA tester one test case at a time for manual results. This skill never decides which cases should be automated versus manual, never executes product tests, never invents pass/fail, and never approves QA or release."
---

# Team QA Evidence

## Purpose

Convert real QA execution outcomes into normalized, traceable evidence without changing the underlying test result. Keep tool output and human observations separate from final QA judgment.

## Hard boundaries

- Do not choose whether a test case should be automated, manual, hybrid, or deferred. That decision belongs to the human QA assignment step in `team-qa`.
- Do not execute product tests unless the user explicitly supplies a tool action and asks for execution outside this skill's normal scope. The normal scope is evidence intake and normalization only.
- Do not infer `pass` from code inspection, test existence, logs without a result, or absence of an error message.
- Do not invent run IDs, screenshots, defect IDs, build IDs, environment data, or tester identities.
- Do not rewrite requirements, design, test cases, or QA conclusions.
- If a tool format is unsupported or ambiguous, mark it unsupported and require a parser/adapter or human clarification. Never guess.

## Inputs

Always resolve:

1. `docs/qa/<feature-slug>/test-cases.md`
2. `docs/qa/<feature-slug>/execution-matrix.md` when present
3. Exact target identity when known: build, commit, environment, component version
4. One of the following evidence sources:
   - tool report/log/result file or external run reference
   - human QA observations collected interactively

## Outputs

Write normalized evidence under:

`.ai-workflow/evidence/<feature-slug>/normalized/`

Use one JSON file per evidence record, for example `EVD-001.json`.

Also maintain a lightweight index when practical:

`.ai-workflow/evidence/<feature-slug>/evidence-index.json`

Do not commit raw tool output by default. Exception (standard step): before final-assessment, copy the raw reports and screenshots the evidence points to into `evidence/<tested-commit-sha>/` and commit them with `git add -f evidence/<sha>/` (they are usually gitignored and overwritten by the next CI run). Point `source_reference` there. Exclude databases or secrets unless the repo is private and the data is synthetic.

## Normalized evidence contract

Every record must contain:

```json
{
  "evidence_id": "EVD-001",
  "test_case_id": "TC-001",
  "source_type": "automated|manual|hybrid",
  "source_tool": "playwright|pytest|junit|newman|k6|manual|other",
  "result": "pass|fail|blocked|not-executed",
  "target": {
    "commit": "abc123-or-unknown",
    "build": "build-123-or-unknown",
    "environment": "staging-or-unknown"
  },
  "executed_at": "ISO-8601-or-unknown",
  "source_reference": "path, URL, run ID, ticket, or note reference",
  "defect_reference": null,
  "metrics": {},
  "notes": "short factual note only",
  "baseline": {
    "requirements": {"path": "docs/requirements/<slug>.md", "sha256": "<script-computed>"},
    "test_plan": {"path": "docs/qa/<slug>/test-cases.md", "sha256": "<script-computed>"}
  },
  "validity": "current|void",
  "void_reason": null
}
```

`metrics` may include performance values such as latency percentiles, throughput, concurrency, error rate, CPU, memory, duration, or recovery time when the source actually provides them.

## Mode A — Tool import

Use when QA provides an automated-test, API-test, load-test, stress-test, CI, or similar machine-generated result.

1. Identify the report format and source tool.
2. Validate that the report can be tied to a target version/environment and one or more test cases.
3. Use a deterministic parser when available, always with `--requirements` and `--test-plan` so the evidence is bound to its baseline. To retire evidence after a requirement/test change, use `scripts/void_evidence.py`; never delete it.
4. Normalize only observed results.
5. Preserve a reference to the raw source.
6. If the parser cannot map a result to a test case, leave it unmapped and report the gap rather than fabricating a mapping.

Bundled `scripts/normalize_evidence.py` supports:

- JUnit-style XML with test case names that contain a `TC-###` identifier (regex `\bTC-\d{3,}\b`; e.g. Go `TestTC017_x` does NOT match, use `TestX/TC-017_y` subtests). Tests without an id are dropped with a `WARN` line and counted as `dropped_without_tc`.
- Numbering continues after the highest existing `EVD-###.json` in `--output-dir`; nothing is overwritten.
- Generic JSON arrays/objects containing `test_case_id` and `result` or `status`.
- Manual evidence JSON created by this skill.

Other formats require an explicit adapter or manual mapping.

## Mode B — Manual interview

Use when the execution matrix assigns one or more test cases to human QA.

Ask one test case at a time. Show only the information needed to answer:

- test case ID and title
- intended behavior / expected result
- target build and environment

Ask the human for exactly one outcome:

- Pass
- Fail
- Blocked
- Not executed

If `Fail`, ask for defect/evidence reference when available. If `Blocked`, ask for the blocker. If `Pass`, do not demand unnecessary prose.

Record the answer as normalized evidence. Do not reinterpret the human's selected outcome.

If the interview is interrupted, save progress under:

`.ai-workflow/evidence/<feature-slug>/manual-session.json`

Resume from the next unanswered assigned case.

## Evidence quality rules

An evidence record is suitable for final QA assessment only when:

- it maps to a known `TC-###`,
- result is one of the allowed states,
- the tested target is known or the limitation is explicit,
- source/reference is sufficient to trace the observation.

A missing record means `not-executed` / `evidence-missing`, never implicit pass.

## Performance and stress evidence

Treat performance testing as a broad non-functional category. Stress testing is one subtype, alongside load, spike, soak/endurance, scalability/capacity, and related performance verification.

When normalizing performance evidence, preserve the metrics needed by the test case's acceptance threshold. Never invent a threshold; thresholds come from requirements/test cases.

## Observability

When the repository observability protocol is present, emit best-effort events such as `qa_evidence_import_started`, `qa_evidence_normalized`, `qa_manual_evidence_recorded`, and `qa_evidence_import_failed`. Telemetry failure must not change evidence or QA decisions.

## Bundled resources

- `references/evidence-contract.md` — normalized evidence schema and result rules.
- `scripts/normalize_evidence.py` — deterministic normalizer for supported result formats.
- `scripts/write_manual_evidence.py` — deterministic writer for a human QA outcome collected by interview.
- `scripts/void_evidence.py` — marks evidence `validity: void` with `void_reason` (kept, excluded from assessment).
