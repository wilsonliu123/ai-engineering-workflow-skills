---
name: team-qa
description: Use when planning QA test cases (TC-001..N) mapped to acceptance criteria with AI pre-filled automated/manual execution types (plan mode, which can run in parallel with development), or when verifying test execution results, importing JUnit XML/JSON test reports, and filing linked Jira Bug tickets on failure (verify mode).
---

# Team QA (Station 3b — Streamlined QA Plan & Verify)

## Overview

Station 3b of the 4-station Jira-centered workflow. Merges test planning, execution type assignment, evidence intake (`team-qa-evidence`), and final QA assessment into two streamlined modes: `plan` and `verify`. Supports **two remembered QA backends** (`qa_test_backend` in `.ai-workflow/team-config.json`):
- **`jira-comment`:** Stores the full Test Plan and AC/TC Verification tables directly in Jira Story comments.
- **`qase`:** Synchronizes Test Suites, Test Cases (`TC-001..N`), Test Runs, bulk execution results, and Jira External Issue links directly to **Qase TestOps** (`https://api.qase.io/v1`), while posting a concise 3-line summary and Qase Suite/Run link on the Jira Story.

Uses the configured `qa` role identity and per-role Jira API token (`role_credentials.qa` in `.ai-workflow/credentials.json`, without self-`@mention`ing QA during `plan`), and stores `qase_api_token` exclusively in gitignored `.ai-workflow/credentials.json` (`0600`).

Read `references/common-workflow-governance.md` and `references/qa-method.md` before running.

## Pre-Flight Gate: One-Time QA Backend Choice (`jira-comment` vs `qase`)

Before running `record-qa` (`plan` or `verify`), inspect the current QA backend configuration:

```bash
python3 .ai-workflow/bin/jira_sync.py --repo-root . record-qa --inspect [--key "<JIRA-KEY>"]
```

- **If `requires_qa_backend_choice` is `true` (or if `qa_test_backend` is `"qase"` and `qase_has_token` is `false` in live mode):**
  **STOP and ask the user once** before executing `record-qa`:
  1. 請問專案的測試案例（Test Cases）與測試執行紀錄要放在 **Jira 評論（`jira-comment`）** 還是同步至 **Qase TestOps（`qase`）**？（確認後會記憶在 `.ai-workflow/team-config.json`，後續 `plan` 與 `verify` 自動沿用，不再重複詢問。）
  2. 若選擇 **`qase`**：請一併確認 **Qase Project Code**（例如 `AP`，若與 Jira `project_key` 相同可直接沿用）與 **Qase API Token**（只會安全寫入受 `.gitignore` 保護的 `.ai-workflow/credentials.json`，權限 `0600`）。
- **Once confirmed:** Pass `--qa-backend <jira-comment|qase>` (and `--qase-project-code "<CODE>" --qase-token "<TOKEN>"` when `qase` is chosen) on your `record-qa` call. `jira_sync.py` persists the choice in `.ai-workflow/team-config.json` and `.ai-workflow/credentials.json`.
- **If `qa_backend_configured` is already `true`:** Do **not** ask again — proceed directly using the remembered `qa_test_backend` unless the user explicitly asks to switch backends.

## Explicit Modes, 3-Tier Execution Model & Anti-False-Pass Guardrail

| Mode | When to Run | Key Action |
|---|---|---|
| **`plan`** | Anytime after `team-spec` is `approved` (**can run in parallel with `team-dev`**) | Generate `TC-001..N` mapped to `AC-*` with AI pre-filled **3-tier execution types (`automated` / `external` / `manual`)** for 1-step human confirmation; sync via `jira_sync.py record-qa --mode plan` |
| **`verify`** | After `team-review` approves the candidate commit | Verify `automated` tests locally (`--all-pass` or `--junit-xml`), ingest `external` tool reports (`--junit-xml` / `--results-json` / `--result`), and record `manual` human check results (`--result TC-ID=pass\|fail`); file linked Jira `Bug` tickets on any failure via `jira_sync.py create-bug` |
| **QA Fast-Track** | In `verify` mode when `qa.test_cases` is empty | `record-qa --mode verify` automatically derives `TC-001..N` (`automated`) 1:1 from Story `AC-001..N` without requiring a separate `--mode plan` pass first |

### The 3-Tier Hybrid QA Execution Types

| Execution Type | Scope & Examples | How `verify` Mode Records Pass/Fail |
|---|---|---|
| **`automated`** | Local unit, component, or integration tests that AI can execute directly in the workspace (`pytest`, `vitest`, `go test`) | AI runs the test command locally; once green, `--all-pass` or `--junit-xml` marks `automated` TCs as `pass` |
| **`external`** | Tests requiring external tools, CI/Staging environments, real browsers/devices, or third-party sandboxes (Playwright/Cypress against Staging, Postman/Newman, JMeter, payment/webhook sandboxes) | **Protected against `--all-pass`!** Remains `not-executed` (`qa_status: "incomplete"`) until an external report (`--junit-xml`, `--results-json`) or explicit result (`--result TC-ID=pass`) is provided |
| **`manual`** | Human visual UX inspection, physical device checks, or cross-system exploratory verification | **Protected against `--all-pass`!** AI presents a concise checklist and records `pass`/`fail` only after human confirmation (`--result TC-ID=pass`) |

## Hard Boundaries

- **No Per-Feature Markdown Files in Git:** Never create `docs/qa/` files or commit `.ai-workflow/evidence/` files. Test plans, verification tables, and Bug tickets live in Jira and `.ai-workflow/active/<JIRA-KEY>.json`.
- **Strict Anti-False-Pass Guardrail:** Missing evidence is recorded as `not-executed`, never `pass`. In `jira_sync.py record-qa --mode verify`, `--all-pass` **ONLY** marks `execution_type == "automated"` test cases as `pass`; any `external` or `manual` test cases without explicit results stay `not-executed` (`qa_status: "incomplete"`), preventing false passes on tests that AI cannot run locally.
- **AI Pre-Fills, Human Confirms ("抓大放小"):** In `plan` mode, AI pre-fills the recommended `automated` / `external` / `manual` execution type for every `TC-*` in one summary table instead of interviewing the human case-by-case. The human can confirm the whole table in one step or override specific rows.

## Mode 1: `plan` — Parallel Test Case Checklist (`TC-001..N`)

1. **Read Story Specification:**
   - Load `.ai-workflow/active/<JIRA-KEY>.json` (`AC-001..N`, change surface `has_ui`/`has_api`/`has_data`, design summary, security/boundary rules) and `docs/shared/engineering-standards.md`.
2. **Generate Traceable Test Cases (`TC-001..N`):**
   - Every `AC-*` must be covered by at least one `TC-*`.
   - Include happy path, boundary/invalid input, authz/permission (`401`/`403`), and error/recovery cases where applicable.
   - Pre-fill `execution_type` using the 3-tier classification:
     - `automated` for local unit/integration tests covering API contracts, business logic, data validation, and permissions.
     - `external` for E2E browser suites against Staging (Playwright/Cypress), API collection runners (Postman/Newman), load/stress tests (JMeter/k6), or third-party sandbox callbacks.
     - `manual` only for human visual layout inspection, physical hardware, or exploratory UX checks.
3. **Present Table for Quick Confirmation & Sync:**
   Show the `TC-001..N` table in Traditional Chinese (繁體中文) for quick human confirmation (or override), then run (prefer `.ai-workflow/bin/jira_sync.py` or fall back to `<skill-dir>/scripts/jira_sync.py`):

   ```bash
   python3 .ai-workflow/bin/jira_sync.py --repo-root . record-qa \
     --key "<JIRA-KEY>" \
     --mode plan \
     [--qa-backend "<jira-comment|qase>"] \
     [--qase-project-code "<QASE_CODE>" --qase-token "<QASE_API_TOKEN>"] \
     --tc "TC-001:AC-001:automated:<Title and expected outcome>" \
     --tc "TC-002:AC-002:external:<Title and external tool/env requirement>" \
     --tc "TC-003:AC-003:manual:<Title and human check steps>"
   ```
   *(Colons inside `<Title and expected outcome>` are preserved automatically. Or pass `--cases-json '<JSON array>'`. When `qa_test_backend == "qase"`, `record-qa --mode plan` idempotently creates/updates the Qase Suite `<JIRA-KEY>` and Test Cases, links `<JIRA-KEY>` via Qase External Issue integration when enabled, stores `qase_suite_id` and per-TC `qase_case_id` in `<JIRA-KEY>.json`, and posts a clean summary + Qase Suite URL on Jira.)*

## Mode 2: `verify` — 3-Tier Result Intake, Anti-False-Pass & Bug Loop

1. **Collect Real Execution Outcomes Across All 3 Tiers:**
   - **Tier 1 — `automated` (Local AI Execution):** Run the project's local test suite (`pytest`, `vitest`, etc.) or ingest `--junit-xml` / `--results-json`. Passing `--all-pass` marks all un-overridden `automated` test cases as `pass` while leaving `external` and `manual` test cases untouched (`not-executed`).
   - **Tier 2 — `external` (External Tools / CI / Staging):** If `pending_external_tcs` exist, either invoke the configured CLI/MCP runner if reachable, or prompt the user to provide the external test report (`--junit-xml <path>`, `--results-json <path>`) or confirmation (`--result TC-002=pass`).
   - **Tier 3 — `manual` (Human Verification):** If `pending_manual_tcs` exist, present the step-by-step manual verification checklist to the user and record their reported `pass` / `fail` / `blocked` outcome via `--result TC-003=pass`.
   - **QA Fast-Track:** If `qa.test_cases` is empty when `verify` runs on a purely automated story, `jira_sync.py` automatically generates `TC-001..N` (`automated`) 1:1 from `AC-001..N` before applying results.
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
     `record-qa --mode verify` automatically records the verification results (creating a Qase Test Run, submitting bulk results, and completing the Run when `qa_test_backend == "qase"`, or posting the full AC/TC table when `qa_test_backend == "jira-comment"`) and executes a role-aware handoff to `release_manager` (`Ready for Release` + `@mention` if a different person; auto-consolidated if `same_person: true`, where AI can proactively auto-chain into `$team-release`).

## Red Flags — STOP

- Running `record-qa` when `requires_qa_backend_choice` is `true` without asking the user to choose `jira-comment` vs `qase` first.
- Writing `QASE_API_TOKEN` or `JIRA_API_TOKEN` into Git-tracked `.ai-workflow/team-config.json` (tokens belong exclusively in gitignored `.ai-workflow/credentials.json`).
- Marking a test `pass` without an actual test run report or explicit human QA result.
- Asking the human one message per test case just to choose `automated` vs. `manual`.
- Recommending `team-release` while any `TC-*` is `fail`, `blocked`, `not-executed`, or while any `Bug` is `open`.
