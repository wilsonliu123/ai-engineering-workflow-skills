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
