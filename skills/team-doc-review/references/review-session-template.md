# Review Session Template
```markdown
---
artifact_id: <artifact-id>
artifact_path: <path>
artifact_baseline: <git-sha-or-content-baseline>
review_session_status: in-progress
current_item: <REVQ-001-or-null>   # required while open
final_decision: pending   # pending|approved|changes-requested|rejected
review_mode: one-at-a-time   # or batch (delegated approver or low risk)
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
- Evidence:
- Why human judgment is needed:
- Options / trade-offs:
- AI recommendation:
## Final recap
```
