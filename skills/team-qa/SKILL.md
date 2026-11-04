---
name: team-qa
description: Use when planning QA test cases (TC-001..N) mapped to acceptance criteria with AI pre-filled automated/manual execution types (plan mode, which can run in parallel with development), or when verifying test execution results, importing JUnit XML/JSON test reports, and filing linked Jira Bug tickets on failure (verify mode).
---

# Team QA (Station 3b — Streamlined QA Plan & Verify)

## Overview

Station 3b of the 4-station Jira-centered workflow. Merges test planning, execution type assignment, evidence intake (`team-qa-evidence`), and final QA assessment into two streamlined modes: `plan` and `verify`. Syncs the Test Plan table, AC/TC Verification table, and any linked `Bug` tickets directly to Jira and `.ai-workflow/active/<JIRA-KEY>.json`.

Read `references/common-workflow-governance.md` and `references/qa-method.md` before running.

## Explicit Modes & QA Fast-Track

| Mode | When to Run | Key Action |
|---|---|---|
| **`plan`** | Anytime after `team-spec` is `approved` (**can run in parallel with `team-dev`**) | Generate `TC-001..N` mapped to `AC-*` with AI pre-filled `automated`/`manual` execution types for 1-step human confirmation; sync via `jira_sync.py record-qa --mode plan` |
| **`verify`** | After `team-review` approves the candidate commit | Import JUnit XML / JSON results, pass `--all-pass` after suite verification, or record manual outcomes via `jira_sync.py record-qa --mode verify`; if any TC fails, file linked Jira `Bug` tickets via `jira_sync.py create-bug` |
| **QA Fast-Track** | In `verify` mode when `qa.test_cases` is empty | `record-qa --mode verify` automatically derives `TC-001..N` 1:1 from Story `AC-001..N` without requiring a separate `--mode plan` pass first |

## Hard Boundaries

- **No Per-Feature Markdown Files in Git:** Never create `docs/qa/` files or commit `.ai-workflow/evidence/` files. Test plans, verification tables, and Bug tickets live in Jira and `.ai-workflow/active/<JIRA-KEY>.json`.
- **Never Invent Test Execution:** Missing evidence is recorded as `not-executed`, never `pass`. Only use `--all-pass` when the automated/manual test suite covering the Story's ACs has actually been executed and verified to pass.
- **AI Pre-Fills, Human Confirms ("抓大放小"):** In `plan` mode, AI pre-fills the recommended `automated` vs. `manual` execution type for every `TC-*` in one summary table instead of interviewing the human case-by-case. The human can confirm the whole table in one step or override specific rows.

## Mode 1: `plan` — Parallel Test Case Checklist (`TC-001..N`)

1. **Read Story Specification:**
   - Load `.ai-workflow/active/<JIRA-KEY>.json` (`AC-001..N`, change surface `has_ui`/`has_api`/`has_data`, design summary, security/boundary rules) and `docs/shared/engineering-standards.md`.
2. **Generate Traceable Test Cases (`TC-001..N`):**
   - Every `AC-*` must be covered by at least one `TC-*`.
   - Include happy path, boundary/invalid input, authz/permission (`401`/`403`), and error/recovery cases where applicable.
   - Pre-fill `execution_type`:
     - `automated` for API contracts, business logic, data validation, permissions, and deterministic UI flows.
     - `manual` only for visual layout inspection, external hardware/third-party sandbox flows, or exploratory UX checks.
3. **Present Table for Quick Confirmation & Sync:**
   Show the `TC-001..N` table in Traditional Chinese (繁體中文) for quick human confirmation (or override), then run (prefer `.ai-workflow/bin/jira_sync.py` or fall back to `<skill-dir>/scripts/jira_sync.py`):

   ```bash
   python3 .ai-workflow/bin/jira_sync.py --repo-root . record-qa \
     --key "<JIRA-KEY>" \
     --mode plan \
     --tc "TC-001:AC-001:automated:<Title and expected outcome>" \
     --tc "TC-002:AC-002:manual:<Title and expected outcome>"
   ```
   *(Colons inside `<Title and expected outcome>` are preserved automatically. Or pass `--cases-json '<JSON array>'`.)*

## Mode 2: `verify` — Result Intake, QA Fast-Track, Bug Loop & Release Handoff

1. **Collect Real Execution Outcomes:**
   - **Automated tests:** Ingest JUnit XML (`--junit-xml <path>`) or JSON results (`--results-json <path>`) containing `TC-###` IDs, or run the test suite and pass `--all-pass` once all tests pass.
   - **Manual tests:** Present any remaining `manual` `TC-*` items concisely and record the human QA tester's actual `pass` / `fail` / `blocked` observation.
   - **QA Fast-Track:** If `qa.test_cases` is empty when `verify` runs, `jira_sync.py` automatically generates `TC-001..N` 1:1 from `AC-001..N` before applying results.
2. **Record Verification Table on Jira (`record-qa --mode verify`):**

   ```bash
   python3 .ai-workflow/bin/jira_sync.py --repo-root . record-qa \
     --key "<JIRA-KEY>" \
     --mode verify \
     [--commit "<tested-commit-sha>"] \
     [--junit-xml "<path/to/junit.xml>"] \
     [--result "TC-001=pass" --result "TC-002=pass"] \
     [--all-pass]
   ```

3. **Branch on Verification Outcome:**
   - **If Any Test Fails (`fail` or `blocked`):**
     Immediately create a linked Jira `Bug` ticket for each failure using `jira_sync.py create-bug`:

     ```bash
     python3 .ai-workflow/bin/jira_sync.py --repo-root . create-bug \
       --key "<JIRA-KEY>" \
       --tc-id "<TC-002>" \
       --ac-id "<AC-001>" \
       --title "<Concise defect summary>" \
       --severity "<blocker|major|minor>" \
       --steps "<Reproduction steps>" \
       --expected "<Expected behavior>" \
       --actual "<Actual observed behavior>" \
       [--commit "<tested-commit-sha>"]
     ```
     `create-bug` automatically links the Bug to `<JIRA-KEY>`, transitions the Story back to `In Progress`, and notifies/reassigns the `developer` role from `team-config.json` with `@mention` so `$team-dev` (`implement` mode) can fix it.
   - **If All Tests Pass (`qa_status: "passed"` and 0 open Bugs):**
     `record-qa --mode verify` automatically posts the passing AC/TC Verification Table to Jira and executes a role-aware handoff to `release_manager` (`Ready for Release` + `@mention` if a different person; auto-consolidated if `same_person: true`, where AI can proactively auto-chain into `$team-release`).

## Red Flags — STOP

- Marking a test `pass` without an actual test run report or explicit human QA result.
- Asking the human one message per test case just to choose `automated` vs. `manual`.
- Recommending `team-release` while any `TC-*` is `fail`, `blocked`, `not-executed`, or while any `Bug` is `open`.
