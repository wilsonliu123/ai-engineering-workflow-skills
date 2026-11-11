# Streamlined QA Planning, Verification & Bug Loop Method

## 1. Parallel Test Case Planning (`plan` mode)

`team-qa` (`plan` mode) can run anytime after `team-spec` is approved — in parallel with `team-dev` (`plan` and `implement`).

### Coverage Rules
1. **100% AC Traceability:** Every `AC-001..N` in `.ai-workflow/active/<JIRA-KEY>.json` must map to at least one `TC-001..N`.
2. **Security & Boundary Coverage:**
   - When `has_api` or `has_data` is true, include boundary/invalid-input cases and unauthorized (`401`/`403`) access cases.
   - When `has_ui` is true, include key situation states (`normal`, `empty`, `invalid`/`error`) and XSS/escaping checks where user input is rendered.
3. **AI Pre-Filled 3-Tier Execution Type ("抓大放小"):**
   - Pre-fill `execution_type` as `"automated"`, `"external"`, or `"manual"` for every test case based on technical feasibility:
     - `"automated"`: Local unit/integration tests AI can execute directly in the repo.
     - `"external"`: Tests requiring external tools, CI/Staging environments, Playwright/Cypress against remote URLs, Postman/Newman, JMeter, or third-party sandboxes.
     - `"manual"`: Human visual/UX inspection or physical exploratory verification.
   - Present the complete table once so the human QA owner can approve in 1 reply or adjust specific rows.

---

## 2. Verification, Anti-False-Pass & Evidence Intake (`verify` mode)

`jira_sync.py record-qa --mode verify` supports four input mechanisms (which can be combined across one or more runs):

1. **Local Automated Fast-Fill (`--all-pass`):**
   - Marks **only** test cases with `execution_type == "automated"` as `pass` after AI executes and verifies the local test suite.
   - **Anti-False-Pass Guardrail:** Any test case with `execution_type in ("external", "manual")` is **never** auto-passed by `--all-pass`; it remains `not-executed` (`qa_status: "incomplete"`) and is listed in `pending_external_tcs` / `pending_manual_tcs` until verified via JUnit XML, JSON results, or `--result`.
2. **JUnit XML (`--junit-xml <path>`):**
   - Automatically matches `<testcase>` elements whose `name` or `classname` contains `TC-\d{3,}` (e.g., `test_export_csv_TC-001` or Playwright/Postman JUnit report `TC-002`).
   - Maps `<failure>` / `<error>` → `fail`, `<skipped>` → `not-executed`, otherwise → `pass` (using worst-outcome precedence for parameterized tests).
3. **JSON Results (`--results-json <path-or-json>`):**
   - Accepts either a dictionary `{"TC-001": "pass", "TC-002": "fail"}` or a list of objects `[{"id": "TC-002", "result": "fail", "attachments": ["artifacts/fail.png"]}]` from external test runners.
4. **CLI Result & Media Flags (`--result TC-002=fail:說明` & `--tc-attach TC-002=artifacts/fail.png`):**
   - Used to record verified outcomes and bind screenshots, screen recordings, or logs from `external` tool runs (e.g., Playwright) or `manual` QA verification.

---

## 3. Dual QA Backend (`jira-comment` vs `qase`)

`team-qa` remembers the project's chosen test management backend in `.ai-workflow/team-config.json` (`qa_test_backend`):

1. **`jira-comment` Mode:**
   - Posts the full Markdown Test Plan table (`plan` mode) and AC/TC Verification table (`verify` mode) directly as rich ADF comments on the Jira Story.
   - Media attachments (`--attach` / inherited `--tc-attach`) are uploaded directly to the Jira `Bug` ticket's Attachments (`POST /rest/api/3/issue/{bugKey}/attachments`) without calling Qase.
2. **`qase` Mode (Qase TestOps API v1):**
   - Stores non-secret metadata (`qase.project_code`, `qase.base_url`, `qase.app_url`, `qase.jira_integration_type`) in `.ai-workflow/team-config.json` and stores `qase_api_token` exclusively in gitignored `.ai-workflow/credentials.json` (`0600`).
   - **`plan` Mode:** Idempotently creates or updates a Qase Suite for `<JIRA-KEY>`, updates existing cases via `PATCH /v1/case/{code}/{id}` when `qase_case_id` is already recorded, bulk-creates new cases via `POST /v1/case/{code}/bulk` (mapping `automated -> 2`, `external -> 1`, `manual -> 0`), attaches cases to the Jira issue via `POST /v1/case/{code}/external-issue/attach` (recording a non-blocking warning if Qase's Jira integration app is not enabled), and posts a concise 3-line summary + Qase Suite URL on Jira.
   - **`verify` Mode & Dual Media Sync:** Ensures all test cases exist in Qase (including QA Fast-Track stories), uploads any `--tc-attach` / `attachments` media via `POST /v1/attachment/{code}` (caching SHA-256 -> Qase `hash`), creates a Qase Test Run (`POST /v1/run/{code}`), links the run to Jira (`POST /v1/run/{code}/external-issue`), submits bulk results including `"attachments": [<hash>]` (`POST /v1/result/{code}/{run_id}/bulk`), completes the run (`POST /v1/run/{code}/{run_id}/complete`) when `qa_status == "passed"`, and posts a concise verification summary + Qase Run URL on Jira.

---

## 4. Closed-Loop Defect Routing, Exploratory Bugs & Standalone Production Bugs (`verify` & `report` modes)

`jira_sync.py create-bug` powers three defect workflows cleanly:

1. **Planned Test Case Failure (`verify` mode — `--key <STORY-KEY> --tc-id <TC-002>`):**
   - Marks `TC-002` as `fail`, inherits any media recorded on `TC-002` via `--tc-attach`, uploads all `--attach` screenshots/videos/logs to the new Jira `Bug` Attachments (`X-Atlassian-Token: no-check`) and Qase (`POST /v1/defect/{code}` + Run Result when `qa_test_backend == "qase"`), links the Bug to `<STORY-KEY>`, and hands `<STORY-KEY>` back to the owning `developer`.
   - When the developer fixes the bug (`complete-task --key <STORY-KEY> --task-id <BUG-KEY>`), the Jira Bug is closed, the Qase Defect is resolved (`PATCH /v1/defect/{code}/{id}/resolve`), and `candidate_commit` updates for re-verification.

2. **Exploratory / Ad-Hoc Story Defect (`report` mode — `--key <STORY-KEY>` with `--tc-id` omitted):**
   - When QA finds an extra defect during Story testing that is NOT in `TC-001..N`, omitting `--tc-id` keeps all passing `TC-001..N` untouched (`result == "pass"`) and automatically appends `TC-EXP-001` (`TC-EXP-002`, ..., `execution_type: "manual"` by default, `result: "fail"`).
   - Syncs `TC-EXP-001` into the Story's Qase Suite when `qa_test_backend == "qase"`, files the linked Jira `Bug` + Qase Defect + media attachments, and blocks `close-story` until both the Bug is fixed and `TC-EXP-001` is verified `pass`.

3. **Standalone Production / User-Reported Bug (`report` mode — `--standalone` or `--standalone --adopt --key <BUG-KEY>`):**
   - Files a standalone Jira `Bug` without a parent Story link (or adopts an existing CS/user Jira `Bug` via `--adopt --key <BUG-KEY>` **without overwriting its original Jira description**), uploads `--attach` evidence to Jira + Qase Defect, and initializes `.ai-workflow/active/<BUG-KEY>.json` (`issue_kind: "standalone_bug"`).
   - **Lightweight 4-Station Closure:** Skips `team-spec` and creates zero redundant Jira `Sub-task`s (`jira_subtask_key: None`). Flows directly through `team-dev (implement)` (`complete-task --key <BUG-KEY>`, posting the fix comment on `<BUG-KEY>` without prematurely closing it) $\rightarrow$ `team-review` $\rightarrow$ `team-qa (verify)` (resolving Qase Defect on `pass`, or uploading failure media onto `<BUG-KEY>` and reopening `TASK-001` on `fail` without nested Bugs) $\rightarrow$ `team-release` (`close-story --key <BUG-KEY>` transitions `<BUG-KEY>` to `Done` and deletes `.ai-workflow/active/<BUG-KEY>.json`).
