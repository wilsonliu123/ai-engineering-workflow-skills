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
   - Accepts either a dictionary `{"TC-001": "pass", "TC-002": "fail"}` or a list of objects `[{"id": "TC-001", "result": "pass"}]` from external test runners.
4. **CLI Result Flags (`--result TC-002=pass --result TC-003=pass`):**
   - Used to record verified outcomes of `external` tool runs or `manual` test cases confirmed by the human QA tester in chat.

---

## 3. Closed-Loop Defect Routing (`create-bug`)

When any test case fails during `verify` mode:
1. Run `jira_sync.py create-bug` with `--tc-id`, `--ac-id`, `--title`, `--severity`, `--steps`, `--expected`, `--actual`, and `--commit`.
2. `create-bug` creates a Jira `Bug` issue, links it to the parent `User Story`, records it in `.ai-workflow/active/<JIRA-KEY>.json` under `bugs` (`status: "open"`), and triggers a role-aware handoff back to `developer`.
3. When the developer fixes the bug in `team-dev` (`implement` mode), they run `jira_sync.py complete-task --key <JIRA-KEY> --task-id <BUG-KEY> --commit <new-sha> ...`, which closes the Bug and updates `candidate_commit` for re-verification.
