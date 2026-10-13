# Human Review Protocol

Use this protocol after a stage artifact reaches `artifact_status: complete`.

1. Scan the complete artifact and its authoritative upstream sources.
2. Classify potential review items as `critical-decision`, `material-change`, `minor`, or `informational`.
3. Persist review state at `docs/workflow/reviews/<artifact-id>.review.md` with artifact path, artifact baseline, current item, resolved item decisions, remaining items, and final decision.
4. Default: present exactly one unresolved `critical-decision` or `material-change` item at a time. Batch mode: when the approver is a delegate, or `risk_level` is low, present the whole item list at once and record all decisions in one pass (emit one `review_item_decided` per item only if the human actually decided it individually). Include: topic, why human judgment is needed, evidence/source links, options and trade-offs, AI recommendation when useful, and a clear request for the human decision.
5. Do not interrupt the human for minor/informational items unless they collectively change risk or correctness. Summarize them at the end.
6. Item decisions may be `accepted`, `rejected`, `change-requested`, `deferred`, or `needs-context`.
7. After all blocking/material items are resolved, present a short final recap and ask for the document-level decision: `approve`, `request changes`, or `reject`.
8. Only after that explicit final decision update the source artifact's `review_status`, `approval_reference`, `approved_by_role`, `delegation_reference` (if delegated) and, when the approver is the author, `independent_reviewer_role`; then run `check_upstream_gate.py --stamp-approved <artifact>`.
9. If content changes after review began, compare the artifact baseline (`check_upstream_gate.py --hash`). Revalidate affected decisions; never attach old approval silently to changed content.
10. If review is interrupted, leave `review_status: in-progress` and preserve session state. Manual invocation of `team-doc-review` resumes from the next unresolved item.

Risk depth:
- low: ask only genuine material decisions; otherwise go quickly to final recap.
- medium: review scope/AC/design changes and meaningful risks.
- high/critical: explicitly cover security, authorization, sensitive data, migration/data loss, compatibility/contracts, tenant isolation, financial impact, irreversible architecture, and operational recovery when relevant.
