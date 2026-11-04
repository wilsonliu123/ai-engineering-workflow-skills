---
name: team-release
description: Use when a Jira Story has passed Spec, Sub-task implementation, Code Review, and QA Verification with zero open Bugs and is ready for final release reconciliation, Jira Story closure (Done), and cleanup of temporary local .ai-workflow/active/<JIRA-KEY>.json scaffolding.
---

# Team Release (Station 4 — Release Verification, Jira Closure & Scaffolding Cleanup)

## Overview

Station 4 of the 4-station Jira-centered workflow. Verifies that Spec, Sub-tasks, Code Review, and QA Verification are all complete and aligned on the candidate commit with zero open Bugs. Posts the final Release Summary to the Jira `User Story`, transitions the Jira Story status to `Done`, and removes `.ai-workflow/active/<JIRA-KEY>.json` so the Git repository stays 100% free of per-feature process files.

Read `references/common-workflow-governance.md` and `references/release-method.md` before closing a Story.

## Hard Boundaries

- **No Per-Feature Markdown Files in Git:** Never create `docs/release/` files. The final release record is posted to the Jira `User Story`.
- **Strict Gate Enforcement:** Never close a Story or delete `.ai-workflow/active/<JIRA-KEY>.json` if any Sub-task is incomplete, Code Review or QA is unapproved/failed, open Bugs remain, or the candidate commit drifted after Review/QA.
- **No Unsolicited Production Deployment:** Closing the Jira Story and recording the release summary does not execute external production deployment scripts unless the user explicitly requests deployment.

## Workflow

### 1. Reconcile gates and commit alignment
1. Inspect `.ai-workflow/active/<JIRA-KEY>.json` (or run `jira_sync.py status --key <JIRA-KEY>`).
2. Confirm all 5 release gates hold for the release candidate commit (`<commit-sha>`):
   - `spec_status == "approved"`
   - All `tasks` (`TASK-001..N`) have `status == "completed"`
   - `review.status == "approved"` and `review.reviewed_commit == <commit-sha>`
   - `qa.status == "passed"` and `qa.tested_commit == <commit-sha>`
   - Zero open `Bug` tickets (`bugs` with `status != "completed"`)
3. Check whether any **System-Wide Living Assets** (`AGENTS.md`, `docs/shared/engineering-standards.md`, `docs/shared/glossary.md`, `docs/shared/adr/`) were updated during this feature and ensure they are clean and committed.

### 2. Run `close-story` to finalize Jira & clean up active state
Execute `scripts/jira_sync.py close-story` from the repository root:

```bash
python3 <skill-dir>/scripts/jira_sync.py --repo-root . close-story \
  --key "<JIRA-KEY>" \
  --version "<version-or-tag>" \
  --commit "<candidate-commit-sha>" \
  --notes "<Release summary: delivered scope, migration/rollback notes, residual risks>"
```

What `jira_sync.py close-story` does deterministically:
1. Validates all 5 release readiness gates (exits non-zero with `unmet_gates` if any check fails).
2. Posts the final Release Summary comment (version, verified commit SHA, AC/Sub-task/TC coverage counts, and release notes) to the Jira `User Story`.
3. Transitions the Jira `User Story` to `Done` (`status_mapping.done`).
4. **Deletes `.ai-workflow/active/<JIRA-KEY>.json`** so no temporary per-feature scaffolding remains on disk.

### 3. Present final delivery report
Summarize the completed release in Traditional Chinese (繁體中文):
- Jira Story `<JIRA-KEY>` transitioned to `Done`.
- Release version/tag and verified commit SHA.
- Total ACs, Sub-tasks, and QA Test Cases verified (plus any closed Bugs resolved along the way).
- Confirmation that `.ai-workflow/active/<JIRA-KEY>.json` has been cleaned up and the Git working tree is clean.

## Red Flags — STOP

- Manually deleting `.ai-workflow/active/<JIRA-KEY>.json` when `jira_sync.py close-story` reports `blocked: true`.
- Releasing a commit SHA that differs from `review.reviewed_commit` or `qa.tested_commit`.
- Writing `docs/release/<feature>/release-handoff.md` into the Git repository.
