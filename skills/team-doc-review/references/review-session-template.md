# Review Session Template
`current_item` is required while open. `final_decision` is `pending`, `approved`, `changes-requested`, or `rejected`. `review_mode` is `one-at-a-time` or `batch` (delegated approver or low risk).

```markdown
---
artifact_id: <artifact-id>
artifact_path: <path>
artifact_baseline: <git-sha-or-content-baseline>
review_session_status: in-progress
current_item: <REVQ-001-or-null>
final_decision: pending
review_mode: one-at-a-time
approved_by_role: null
delegation_reference: null
---
# Review Session — <artifact-id>
## Resolved items
| Item | Topic | Decision | Human record / note |
|---|---|---|---|
## Remaining items
<!-- May be removed once review_session_status: completed. -->
### REVQ-001 — <topic>
- Class: critical-decision | material-change | minor | informational
- Question: <the exact, answerable question presented to the human>
- Evidence: <artifact path + section/item; current proposal or explicitly missing fact>
- Why human judgment is needed:
- Options / trade-offs: <practical consequences; identify alternatives requiring source changes>
- AI recommendation: <recommended course, reason and cost/risk; not a human decision>
## Checks and covered decisions
<!-- Keep verified facts, blockers requiring correction/evidence, and applicable prior human decision references here. These are not extra questions. -->
## Final recap
```
