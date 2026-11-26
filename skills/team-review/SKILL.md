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
- **`Stop & Yield on Fix`:** If the user reports a manual-verification bug or tweak during or after review, after updating code and running local tests you **MUST STOP** and report the fix for user verification — strictly forbidden from autonomously running `handoff`, `record-qa`, or `close-story`.
- **Evidence Integrity:** Distinguish static test inspection from actual test execution recorded in completed tasks (`test_result`).

## Workflow

### 1. Verify preflight & inspect actual code diff
1. Load `.ai-workflow/active/<JIRA-KEY>.json` and confirm all `tasks` (`TASK-001..N`) are `completed` and `candidate_commit` is recorded.
2. Inspect the actual git commit / diff (`git show <candidate_commit>` or `git diff <base_branch>...<candidate_commit>`) relative to `delivery_pipeline.integration_branch` when configured in `.ai-workflow/team-config.json` (fallback: `main`/`master`), and verify that changed files match the completed task records.
3. Load `docs/shared/engineering-standards.md`.
   - **Automatic Fallback:** If `docs/shared/engineering-standards.md` does not exist, fall back to `AGENTS.md` and repository-native linter/test configurations without deadlocking the team, and note `standards_source: "AGENTS.md (fallback)"` in the review summary.

### 2. Perform three-axis "抓大放小" review & QA Environment Hints
Follow `references/review-method.md` to evaluate:
1. **Axis 1 — Requirement & AC-by-AC Traceability:** Does the implementation satisfy **every single `AC-001..N`** (checked one by one against concrete functions/files and automated tests) without unapproved scope creep or missing boundary handling?
2. **Axis 2 — Architecture & Design Conformance:** Does the code honor the Story's design summary, conditional UI states (`has_ui`), API contracts (`has_api`), and any referenced `docs/shared/adr/`?
3. **Axis 3 — Engineering Standards, Security, Provenance & Commit Message Compliance:** Are authz checks, input validation, injection/XSS protections, error handling, and automated test coverage sound?
   - **Mandatory Incremental `$team-provenance-audit` Check:** Whenever the git diff touches dependencies, lockfiles (`package.json`, `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`, `go.mod`, `go.sum`, `pom.xml`, `build.gradle*`, `requirements*.txt`, `pyproject.toml`, `Cargo.toml`, `Podfile*`, etc.), vendored/pasted external source code, third-party SDKs, `.jar`/`.war`/`.aar` archives, minified/obfuscated JS, or native binaries, run an incremental `$team-provenance-audit` check (`static_scan.py` reusing the out-of-repo SQLite checkpoint + provenance verification + `gate.py --repo .`) and record the status in the review summary. Flag any confirmed prohibited component as a `blocker` finding (`--review-status changes-requested`) and any unresolved supply-chain provenance gap before handing off to QA.
   - Also inspect candidate commit message(s) (`git log -1 --format=%B <candidate_commit>` / task commits) against `docs/shared/engineering-standards.md` §4 (fallback: project `AGENTS.md`) to verify adherence to the project's Commit template and inclusion of both `<STORY-KEY>` and `<SUBTASK-KEY>` whenever a Jira Sub-task exists (flag non-compliance as a `minor` finding or commit-amend recommendation without hardcoding any single format).
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

### 4. Multi-role notification, SoD Clean Stop & Human Confirmation Behavior
`jira_sync.py handoff` inspects `.ai-workflow/team-config.json` and always returns `can_auto_chain_next_station: false` and `requires_user_confirmation_for_next_station: true`:
- **If `--review-status changes-requested`:** Automatically routes back to `developer` (transitions Story to `In Progress`, reassigns to Developer, and `@mention`s Developer if a different person). If `same_person == false` (`cross_person_clean_stop: true`), enforce **Clean Stop** without prompting `/team-dev`; if `same_person == true`, report the findings concisely, ask one short question whether to proceed to `$team-dev`, then **STOP**.
- **If `--review-status approved` and `qa_handoff_mode == "gated_by_deploy"` (Multi-Environment CI/CD Decoupling):**
  1. Transitions the Jira Story to `status_mapping.dev_done` (if configured; if `dev_done` is blank, keeps current Jira status with a clear warning and **never** transitions to `in_qa` prematurely).
  2. Records `candidate_commit`, review summary, and `qa_deploy_gate: {status: "awaiting_qa_deploy"}` in `.ai-workflow/active/<JIRA-KEY>.json`, and posts a **Dev Completed / Awaiting QA Deploy** comment on Jira **without** reassigning to QA and **without** `@mention`ing QA yet (`qa_notified: false`, `qa_deploy_status: "awaiting_qa_deploy"`).
  3. **Unblocking QA after Deploy:** Once the QA environment is deployed (after human-confirmed branch merge or `qa-*` tag creation — AI never auto-tags, auto-merges, or pushes releases), either (a) your CI/CD pipeline transitions Jira to `status_mapping.in_qa` (auto-detected by `status` / `pull` via `reconcile_story_with_jira`), or (b) run `python3 .ai-workflow/bin/jira_sync.py --repo-root . promote-qa --key "<JIRA-KEY>" [--deploy-ref "<TAG-OR-BUILD>"] [--environment "<ENV>"] [--note "<TEXT>"]` to record `deploy_ref` / `environment` traceability, transition to `in_qa`, assign & `@mention` QA, and unblock `team-qa (verify)` (still respecting SoD Clean Stop / confirmation rules; `jira_sync.py` never runs `git tag` or CI/CD directly). On a subsequent fix cycle (`handoff --review-status approved` on a new `candidate_commit`), `qa_deploy_gate` resets and clears stale `deploy_ref` / `environment`.
  4. During `awaiting_qa_deploy`, QA may still run `team-qa (plan)` in parallel while `team-qa (verify)` stays gated.
- **If `--review-status approved` (default `qa_handoff_mode == "immediate"`) and `reviewer` != `qa` (Different People, `same_person: false`, `cross_person_clean_stop: true`, `next_station_prompt_allowed: false`):**
  1. Transitions the Jira Story status to `In QA` (`status_mapping.in_qa`).
  2. Reassigns the Jira Story assignee to the QA owner's `jira_account_id`.
  3. Posts the combined Handoff & Review comment with `@<QA Owner>` mention, triggering Jira's native email/push notification.
  4. **Mandatory Clean Stop (SoD Hard Rule):** Summarize the review outcome, handoff target (`to_person` / `next_station_owner`), and `target_jira_status`, and **immediately end the turn**. Strictly **FORBID** prompting, suggesting, or asking `"是否立即執行 /team-qa？"` (or mentioning `/team-qa` as a call to action for the developer/reviewer).
- **If `--review-status approved` and `reviewer` == `qa` (Same Person, `same_person: true`, `cross_person_clean_stop: false`, `next_station_prompt_allowed: true`):**
  - `same_person: true` ONLY suppresses self-`@mention` notification spam (`handoff_mode: "auto-consolidated"`) — it **never** authorizes auto-chaining into `$team-qa` or running `record-qa`.
  - Report the Station 3a review summary concisely, ask **one short question** whether the user wants to proceed to `$team-qa`, and **STOP** to wait for explicit user authorization.
- **If `developer` == `reviewer` (Same Person):**
  - `jira_sync.py` automatically records `self_reviewed_with_ai: true` in the review record and Jira comment for audit transparency.

## Red Flags — STOP

- Approving a review (`--review-status approved`) while open `blocker` or `major` findings exist.
- Reviewing stale code when `candidate_commit` does not match the actual working tree / HEAD commit.
- Writing `docs/review/` or `docs/dev-delivery/` files into the Git repository.
- **Cross-Person SoD Violation:** Prompting or suggesting `/team-qa` (e.g., `"是否立即執行 /team-qa？"`) when `same_person == false` (`cross_person_clean_stop: true` / `next_station_prompt_allowed: false`).
- **Unconfirmed Auto-Chaining / `Stop & Yield on Fix` Violation:** Auto-chaining into `$team-qa` or autonomously invoking `handoff`, `record-qa`, or `close-story` without explicit user authorization (including immediately after fixing a user-reported bug/tweak).
