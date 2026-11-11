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

### 2. Perform three-axis "抓大放小" review & QA Environment Hints
Follow `references/review-method.md` to evaluate:
1. **Axis 1 — Requirement & AC-by-AC Traceability:** Does the implementation satisfy **every single `AC-001..N`** (checked one by one against concrete functions/files and automated tests) without unapproved scope creep or missing boundary handling?
2. **Axis 2 — Architecture & Design Conformance:** Does the code honor the Story's design summary, conditional UI states (`has_ui`), API contracts (`has_api`), and any referenced `docs/shared/adr/`?
3. **Axis 3 — Engineering Standards & Security:** Are authz checks, input validation, injection/XSS protections, error handling, and automated test coverage sound?
4. **QA External Tool & Environment Hints:** In the handoff `--summary`, explicitly call out any scenarios that cannot be verified by local unit tests alone and require `external` tools (e.g., Playwright/Cypress E2E, Postman/Newman, Staging webhook/OAuth sandbox, JMeter) or `manual` visual checks in Station 3b (`team-qa`).

Apply **"抓大放小"**:
- Only `blocker` (security/data-loss/broken core AC) and `major` (incomplete AC, missing critical test, broken contract) findings block handoff (`--review-status changes-requested`).
- `minor` and `note` observations are included in the summary for visibility but do not block QA handoff when tests pass.

### 3. Sync combined handoff & review to Jira (`jira_sync.py handoff`)
Run `jira_sync.py handoff` from the repository root (prefer `.ai-workflow/bin/jira_sync.py` or fall back to `<skill-dir>/scripts/jira_sync.py`):

```bash
python3 .ai-workflow/bin/jira_sync.py --repo-root . handoff \
  --key "<JIRA-KEY>" \
  --from-role reviewer \
  --to-role qa \
  [--to-assignee "<qa-member-email-or-name>"] \
  --review-status "<approved|changes-requested>" \
  [--commit "<candidate-commit-sha>"] \
  --summary "<Combined Dev Handoff (tasks, commits, test evidence, QA focus areas) + 3-Axis Review Summary>" \
  [--findings-json '<JSON array of findings>']
```

- **Peer Review Auto-Selection (Multi-Member `reviewer` Pool):** When `roles.reviewer.members` has 2+ members, `handoff` automatically selects a reviewer who did **not** author the Story's tasks (`self_reviewed_with_ai: false`), avoiding self-review whenever a peer is available. Pass `--to-assignee` when handing off to a specific QA member in a multi-QA pool (`story.role_owners.qa`).
- **Automatic `REV-001` Safeguard:** If `--review-status changes-requested` is passed and `--findings-json` is omitted (or contains no open `blocker`/`major` item), `jira_sync.py` automatically creates an open `REV-001` finding from `--summary` so `$team-dev` (`implement` mode) can remediate and check it off via `complete-task --task-id REV-001`.

### 4. Multi-role notification & Auto-Chaining behavior
`jira_sync.py handoff` inspects `.ai-workflow/team-config.json`:
- **If `--review-status changes-requested`:** Automatically routes back to `developer` (transitions Story to `In Progress`, reassigns to Developer, and `@mention`s Developer if a different person).
- **If `--review-status approved` and `reviewer` != `qa` (Different People):**
  1. Transitions the Jira Story status to `In QA` (`status_mapping.in_qa`).
  2. Reassigns the Jira Story assignee to the QA owner's `jira_account_id`.
  3. Posts the combined Handoff & Review comment with `@<QA Owner>` mention, triggering Jira's native email/push notification.
- **If `--review-status approved` and `reviewer` == `qa` (Same Person, `same_person: true`):**
  - Records `handoff_mode: "auto-consolidated"` without self-notification spam; **proactively auto-chain** into `$team-qa` (`verify` mode with QA Fast-Track, or `plan` mode) or ask in one line whether to proceed immediately.
- **If `developer` == `reviewer` (Same Person):**
  - `jira_sync.py` automatically records `self_reviewed_with_ai: true` in the review record and Jira comment for audit transparency.

## Red Flags — STOP

- Approving a review (`--review-status approved`) while open `blocker` or `major` findings exist.
- Reviewing stale code when `candidate_commit` does not match the actual working tree / HEAD commit.
- Writing `docs/review/` or `docs/dev-delivery/` files into the Git repository.
