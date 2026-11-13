# ai-engineering-workflow skills (v1.3 — Epic/Sprint Planning + 4-Station Jira-Centered Small-Team Workflow)

Eight Jira-centered engineering workflow skills — plus a dynamic multi-agent orchestration system skill — designed for small and growing teams where one person may hold multiple roles, one role may be shared by multiple members (`1-to-N` role pools), and **Jira** serves as the single permanent planning and per-feature lifecycle asset.

## Planning + 4-Station Workflow Skills (`skills/team-*`)

| Station | Skill | Modes | Purpose |
|---|---|---|---|
| **Init** | `skills/team-init/` | `init` / `auth` | Interactive inquiry-first setup of `.ai-workflow/team-config.json`, `.ai-workflow/bin/jira_sync.py`, and `docs/shared/engineering-standards.md`. Supports **progressive role claiming** (`claimed_roles` / `unclaimed_roles`), **1-to-N multi-member role pools** (`specialty` tags), and non-destructive collaborator **`auth`** onboarding (all Jira/Qase tokens stored exclusively in gitignored `.ai-workflow/credentials.json` with `0600` permissions). |
| **Planning** | `skills/team-plan/` | `epic` / `sprint` / `commit` / `status` | Plans Epic context and Future/Active Sprint membership with preview-before-apply safeguards. Keeps Epic as the cross-Sprint container and routes selected Stories/Tasks/Bugs into the existing delivery workflow. |
| **Router** | `skills/team-next-step/` | — | Read-only single-command entry (`$team-next-step`) inspecting `team-config.json`, `.ai-workflow/active/<JIRA-KEY>.json`, and live Jira state (auto-discovering active Stories via JQL and hydrating/reconciling cross-machine or Jira Web UI edits) to recommend the exact next station, mode, and owner. |
| **Station 1** | `skills/team-spec/` | `one-pass` / `draft` | Scale-aware unified specification & design: small/clear stories finish in one pass (**"抓大放小"**, ≤ 3 material questions); complex/ambiguous stories iterate via multi-round `draft` deep-dives + 6-Dimension AC Completeness Checklist. Supports incremental AC upsert, Jira description drift guard, and conditional UI (`has_ui`) / API (`has_api`) specs synced to the Jira `User Story`. |
| **Station 2** | `skills/team-dev/` | `plan` / `implement` | Decomposes approved specs into verifiable slices (`TASK-001..N` → Jira `Sub-task` with specialty-matched developer auto-assignment) or runs **Dev Fast-Track** (guarded to `<= 3` ACs & `low`/`medium` risk). Implements code & tests in strict priority (`BUG-*` > `TASK-###` > `REV-###`), composes specialist craft skills (Bring-Your-Own-Skill hook), enforces a 4-check review-aligned exit gate, and checks off completion via `complete-task`. |
| **Station 3a** | `skills/team-review/` | — | Combined Dev Handoff & 3-axis Code Review against ACs, design, and `docs/shared/engineering-standards.md` (fallback: `AGENTS.md`). Automatically routes to a peer reviewer in `roles.reviewer.members` when available (or records transparent AI self-review in solo teams), transitions Jira status, and `@mention`s the next owner on cross-person handoffs. |
| **Station 3b** | `skills/team-qa/` | `plan` / `verify` / `report` | Parallel **3-tier QA** (`automated` / `external` / `manual` with anti-false-pass guardrails) across a remembered **`jira-comment`** or **`qase`** (Qase TestOps API v1) backend. **`verify`** ingests JUnit XML/JSON/manual results and files linked Jira `Bug` tickets + media attachments (`--attach`) synced to Jira & Qase Runs/Defects. **`report`** handles unscripted exploratory Story bugs (`TC-EXP-###`) and standalone production/CS `Bug` intake (`--standalone` / `--adopt`). |
| **Station 4** | `skills/team-release/` | — | Reconciles live Jira state, verifies all 5 release gates and zero open Bugs on the candidate commit, runs **scale-aware Living HTML Manual sync** under `docs/manuals/<manual-name>/<manual-name>.html`, posts the Release Summary, transitions the Jira Story (or standalone Bug) to `Done`, and deletes `.ai-workflow/active/<JIRA-KEY>.json`. |

## System Orchestration Skill (`skills/system/`)

| Skill | Purpose |
|---|---|
| `skills/system/run-workflow/` | Instructions and patterns for writing dynamic Python multi-agent orchestration scripts with the `run_workflow` tool (`agent`, `parallel`, `pipeline`, `phase`, `trajectory`, and `call_tool`), suited for fan-out/aggregate tasks and staged agent pipelines. |

## Core Workflow Capabilities

- **Zero-CLI Single-Entry UX & `zh-TW` Default Prose:** Humans interact in plain natural language or invoke `$team-next-step` at any point; AI executes `.ai-workflow/bin/jira_sync.py` in the background and writes human-facing specs, reviews, QA tables, and release notes in **Traditional Chinese (繁體中文)** by default.
- **Epic/Sprint Planning Layer:** `$team-plan` previews Epic context, creates or inspects Future/Active Sprints, and assigns selected Story/Task/Bug issues only after explicit confirmation. Sprint membership does not replace Story delivery gates.
- **Cross-Machine Hydration & Bidirectional Jira Web UI Reconciliation:** Syncs structured state via hidden Jira Issue Property (`ai_workflow_state`) and automatically reconciles manual Jira Web UI changes (Sub-tasks added/reopened, linked Bugs, Assignee updates, and Story Description edits) before station actions.
- **1-to-N Role Pools, End-to-End Auto-Assignment & Smart Handoff:** Supports both multi-role individuals (auto-consolidated same-person chaining without self-`@mention` noise) and multi-member teams (automatic Story/Sub-task/Bug assignee resolution, specialty matching, and peer-review selection).
- **Lightweight Standalone Production Bug Track:** Standalone or adopted CS/production `Bug` tickets registered via `$team-qa` (`report --standalone`) bypass `team-spec` and Sub-task ceremony, flowing directly through `team-dev -> team-review -> team-qa (verify) -> team-release`.

## First-Principles Asset Boundary

- **Per-Feature Lifecycle Assets (Jira + Gitignored Local Scaffolding):**
  - User Stories, ACs, Sub-tasks, Code Review summaries, QA Test Plans & Verification tables, Bug tickets, and media evidence live permanently in **Jira** (and optionally **Qase TestOps**).
  - `.ai-workflow/active/<JIRA-KEY>.json` (gitignored) stores active machine-readable state and is automatically deleted on release (`jira_sync.py close-story`). `.ai-workflow/credentials.json` (`0600`, gitignored) stores personal/per-role Jira and Qase API tokens. No per-feature process markdown files (`docs/requirements/`, `docs/design/`, `docs/qa/`, `docs/release/`, etc.) are ever committed to Git.
- **System-Wide Living Assets (Committed to Git):**
  - `.ai-workflow/team-config.json` and `.ai-workflow/bin/jira_sync.py` (shared team/status mapping and stdlib-only CLI wrapper).
  - `AGENTS.md` and `docs/shared/engineering-standards.md` (canonical engineering, security, and testing rules).
  - Optional `docs/shared/glossary.md`, `docs/shared/adr/ADR-<NNN>-<slug>.md`, and portable dark-sidebar product/API manuals under `docs/manuals/<manual-name>/<manual-name>.html` (`./screenshots/`, `./diagrams/`).

## Training & Onboarding Materials (`docs/`)

- **Scenario-Driven Training Manual:** `docs/training-manual.md`, standalone `docs/training-manual.html`, printable `docs/training-manual.pdf`, and Google Docs-ready `docs/training-manual.docx` (with annotated dual-pane terminal + Jira illustrations in `docs/screenshots/`).
- **Training Slide Deck:** 16-slide scenario presentation in `docs/training-manual.pptx` (`docs/ppt-assets/`).

## Shared Files & Verification

- `scripts/jira_sync.py` (dependency-free Python 3 stdlib CLI supporting live Jira REST API v3, Jira Agile Sprint endpoints, Qase TestOps API v1, and offline mode) and `references/common-workflow-governance.md` are intentionally duplicated across all 8 `team-*` skills so each packaged skill `.zip` is self-contained.
- Run `./tools/check_shared.sh` to verify all 8 copies remain byte-identical.
- Run `python3 tools/test_workflow.py` to execute the 18-test end-to-end lifecycle and mock Jira/Qase REST API suite.
- Routing and QA evaluation scenarios live in `skills/team-next-step/evals/evals.json` and `skills/team-qa/evals/evals.json`.
