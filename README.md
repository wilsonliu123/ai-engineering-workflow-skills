# ai-engineering-workflow skills (v1.0 — 4-Station Jira-Centered Small-Team Workflow)

Seven AI-agent skills designed for small engineering teams where one person may hold multiple roles and **Jira** serves as the single permanent lifecycle asset:

| Station | Skill | Modes | Purpose |
|---|---|---|---|
| **Init** | `skills/team-init/` | `init` / `auth` | Interactive inquiry-first setup: confirm Jira URL, Project Key, role-to-person mappings, and default/per-role Jira API tokens (saved exclusively to gitignored `.ai-workflow/credentials.json`), plus a fast **`auth`** mode for teammates cloning an existing repo to bind only their personal Jira/Qase tokens without modifying `team-config.json`. |
| **Router** | `skills/team-next-step/` | — | Read-only router that inspects `team-config.json`, `.ai-workflow/active/<JIRA-KEY>.json` (auto-discovering active Jira Stories via JQL on a fresh clone), and Jira state to recommend the exact next station, mode, and owner. |
| **Station 1** | `skills/team-spec/` | — | Unified specification & design (merges `team-requirements`, `team-ux`, `team-design`, and inline `team-doc-review`). Produces Scope, testable `AC-*`, conditional UI/API design, and **"抓大放小"** review in one pass; syncs to the Jira `User Story`. |
| **Station 2** | `skills/team-dev/` | `plan` / `implement` | Decomposes approved specs into verifiable tasks (`TASK-001..N` → Jira `Sub-task`), implements code & tests with minimal context, and checks off completed tasks/bugs via `complete-task`. |
| **Station 3a** | `skills/team-review/` | — | Combined Dev Handoff & 3-axis Code Review against ACs, design, and `docs/shared/engineering-standards.md` (fallback: `AGENTS.md`). Auto-transitions Jira Story and `@mention`s QA when roles belong to different people. |
| **Station 3b** | `skills/team-qa/` | `plan` / `verify` | Parallel test planning (`TC-001..N` with AI pre-filled `automated`/`external`/`manual` execution types) and result verification across a remembered **`jira-comment`** or **`qase`** (Qase TestOps) backend. Automatically files linked Jira `Bug` tickets on failure and routes back to Developer. |
| **Station 4** | `skills/team-release/` | — | Verifies all 5 readiness gates and zero open Bugs, posts the Release Summary, transitions the Jira Story to `Done`, and deletes `.ai-workflow/active/<JIRA-KEY>.json`. |

## First-Principles Asset Boundary

- **Per-Feature Lifecycle Assets (Jira + Temporary Active JSON):**
  - User Stories, ACs, Sub-tasks, Code Review summaries, QA Test Plans & Verification tables, and Bug tickets live permanently in **Jira**.
  - During active development, `.ai-workflow/active/<JIRA-KEY>.json` (gitignored) stores machine-readable state and is automatically deleted on release (`jira_sync.py close-story`). No per-feature markdown files (`docs/requirements/`, `docs/design/`, `docs/qa/`, etc.) are committed to Git.
- **System-Wide Living Assets (Committed to Git):**
  - `AGENTS.md`, `docs/shared/engineering-standards.md`, and optional `docs/shared/glossary.md` / `docs/shared/adr/`.

## Shared Files & Verification

- `scripts/jira_sync.py` (dependency-free Python 3 stdlib CLI supporting both live Jira REST API v3 and offline mode) and `references/common-workflow-governance.md` are intentionally duplicated across all 7 skills so each packaged skill `.zip` is self-contained.
- Run `./tools/check_shared.sh` to verify all 7 copies remain byte-identical.
- Run `python3 tools/test_workflow.py` to execute the end-to-end workflow and mock Jira REST API v3 test suite.
- Routing and QA scenarios live in `skills/team-next-step/evals/evals.json` and `skills/team-qa/evals/evals.json`.
