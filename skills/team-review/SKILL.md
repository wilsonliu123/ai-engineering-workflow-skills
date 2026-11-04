---
name: team-review
description: Use when development Sub-tasks (TASK-001..N) or Bug fixes are completed and the actual code diff, task evidence, acceptance criteria, and engineering standards need to be reviewed and handed off to QA via Jira.
---

# Team Review (Station 3a — Combined Dev Handoff & Code Review)

## Overview

Station 3a of the 4-station Jira-centered workflow. Merges post-coding development handoff (`team-dev-handoff`) and three-axis code review (`team-review`) into one step. Inspects the actual git diff, completed task evidence in `.ai-workflow/active/<JIRA-KEY>.json`, the Story's `AC-*` and design summary, and `docs/shared/engineering-standards.md` (with automatic fallback to `AGENTS.md` if `engineering-standards.md` is absent). Posts the combined handoff & review summary to the Jira Story and executes role-aware notification handoff to QA.

Read `references/common-workflow-governance.md` and `references/review-method.md` before reviewing.

## Hard Boundaries

- **No Per-Feature Markdown Files in Git:** Never create `docs/dev-delivery/` or `docs/review/` files. The combined handoff & review record is stored in `.ai-workflow/active/<JIRA-KEY>.json` and posted as a structured comment on the Jira `User Story`.
- **Do Not Silently Edit Product Code During Review:** Report `blocker` / `major` defects and route them back to `team-dev` (`implement` mode).
- **Evidence Integrity:** Distinguish static test inspection from actual test execution recorded in completed tasks (`test_result`).

## Workflow

### 1. Verify preflight & inspect actual code diff
1. Load `.ai-workflow/active/<JIRA-KEY>.json` and confirm all `tasks` (`TASK-001..N`) are `completed` and `candidate_commit` is recorded.
2. Inspect the actual git commit / diff (`git show <candidate_commit>` or `git diff`) and verify that changed files match the completed task records.
3. Load `docs/shared/engineering-standards.md`.
   - **Automatic Fallback:** If `docs/shared/engineering-standards.md` does not exist, fall back to `AGENTS.md` and repository-native linter/test configurations without deadlocking the team, and note `standards_source: "AGENTS.md (fallback)"` in the review summary.

### 2. Perform three-axis "抓大放小" review
Follow `references/review-method.md` to evaluate:
1. **Axis 1 — Requirement & AC Conformance:** Does the implementation satisfy every `AC-*` without unapproved scope creep or missing error/boundary handling?
2. **Axis 2 — Architecture & Design Conformance:** Does the code honor the Story's design summary, conditional UI states (`has_ui`), API contracts (`has_api`), and any referenced `docs/shared/adr/`?
3. **Axis 3 — Engineering Standards & Security:** Are authz checks, input validation, injection/XSS protections, error handling, and automated test coverage sound?

Apply **"抓大放小"**:
- Only `blocker` (security/data-loss/broken core AC) and `major` (incomplete AC, missing critical test, broken contract) findings block handoff (`--review-status changes-requested`).
- `minor` and `note` observations are included in the summary for visibility but do not block QA handoff when tests pass.

### 3. Sync combined handoff & review to Jira (`jira_sync.py handoff`)
Run `scripts/jira_sync.py handoff` from the repository root:

```bash
python3 <skill-dir>/scripts/jira_sync.py --repo-root . handoff \
  --key "<JIRA-KEY>" \
  --from-role reviewer \
  --to-role qa \
  --review-status "<approved|changes-requested>" \
  --commit "<candidate-commit-sha>" \
  --summary "<Combined Dev Handoff (tasks, commits, test evidence, QA focus areas) + 3-Axis Review Summary>" \
  [--findings-json '<JSON array of findings>']
```

### 4. Multi-role notification behavior
`jira_sync.py handoff` inspects `.ai-workflow/team-config.json`:
- **If `--review-status changes-requested`:** Automatically routes back to `developer` (transitions Story to `In Progress`, reassigns to Developer, and `@mention`s Developer if a different person).
- **If `--review-status approved` and `reviewer` != `qa` (Different People):**
  1. Transitions the Jira Story status to `In QA` (`status_mapping.in_qa`).
  2. Reassigns the Jira Story assignee to the QA owner's `jira_account_id`.
  3. Posts the combined Handoff & Review comment with `@<QA Owner>` mention, triggering Jira's native email/push notification.
- **If `--review-status approved` and `reviewer` == `qa` (Same Person):**
  - Records `handoff_mode: "auto-consolidated"` without self-notification spam and immediately recommends `$team-qa` (`verify` mode, or `plan` mode if `TC-*` are not yet planned).
- **If `developer` == `reviewer` (Same Person):**
  - `jira_sync.py` automatically records `self_reviewed_with_ai: true` in the review record and Jira comment for audit transparency.

## Red Flags — STOP

- Approving a review (`--review-status approved`) while open `blocker` or `major` findings exist.
- Reviewing stale code when `candidate_commit` does not match the actual working tree / HEAD commit.
- Writing `docs/review/` or `docs/dev-delivery/` files into the Git repository.
