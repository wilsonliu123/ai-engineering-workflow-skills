# QA Evidence Contract

## Result states

- `pass`: expected result was actually observed for the stated test and target.
- `fail`: observed result contradicted the expected result.
- `blocked`: execution could not complete because of an identified blocker.
- `not-executed`: no valid execution evidence exists.

Do not translate missing evidence into pass.

## Minimum fields

- `evidence_id`
- `test_case_id`
- `source_type`
- `source_tool`
- `result`
- `target.commit`
- `target.build`
- `target.environment`
- `executed_at`
- `source_reference`
- `defect_reference`
- `metrics`
- `notes`
- `baseline.requirements.{path,sha256}` and `baseline.test_plan.{path,sha256}` — content the run was judged against; written by the scripts from `--requirements` / `--test-plan`, never by hand
- `validity`: `current` | `void`
- `void_reason` (required when `void`); optional `superseded_by`

## Baseline and void rules

- Always pass `--requirements docs/requirements/<slug>.md --test-plan docs/qa/<slug>/test-cases.md` when importing or recording evidence.
- When requirements or test cases change after a run, affected evidence is stale. Mark it with `scripts/void_evidence.py --reason "<why>" [--superseded-by EVD-xxx] <files>`; never delete or edit results.
- `team-qa` `validate_qa.py --evidence-dir` rejects current evidence whose baseline no longer matches, evidence without a baseline, void evidence without a reason, and evidence whose `target.commit` differs from `tested_commit`. Void evidence is kept but excluded.

## Performance metrics

Preserve only observed metrics. Common examples:

- `p50_latency_ms`
- `p95_latency_ms`
- `p99_latency_ms`
- `throughput_rps`
- `error_rate_pct`
- `concurrency`
- `duration_seconds`
- `cpu_pct`
- `memory_mb`
- `recovery_seconds`
