# Common Workflow Governance (v1.0 — 4-Station Jira-Centered Workflow)

All 7 skills in the `ai-engineering-workflow` suite share this governance contract. If any older wording conflicts with this file, this governance reference takes precedence.

---

## 1. First-Principles Asset Boundary

To prevent repository bloat and review fatigue in small teams, artifacts are strictly partitioned into **Per-Feature Lifecycle Assets** (owned by Jira) and **System-Wide Living Assets** (owned by Git):

### 1.1 Per-Feature Lifecycle Assets (Jira + Temporary Active State)
- **Authoritative Permanent Asset:** The **Jira User Story** (`<JIRA-KEY>`), its **Sub-tasks** (`TASK-001..N`), linked **Bugs** (`BUG-*`), and structured Jira comments (Spec & Design, Dev Handoff & Code Review, QA Test Plan & Verification Table, Release Summary).
- **Temporary Local Scaffolding:** `.ai-workflow/active/<JIRA-KEY>.json` (gitignored).
  - Holds the structured machine-readable state during active development of `<JIRA-KEY>`.
  - Synchronized with Jira at every station via `scripts/jira_sync.py`.
  - **Automatically deleted** when `team-release` runs `jira_sync.py close-story --key <JIRA-KEY>`, keeping the Git repository 100% free of per-feature process markdown files (`docs/requirements/`, `docs/design/`, `docs/ux/`, `docs/dev-prep/`, `docs/dev-delivery/`, `docs/review/`, `docs/qa/`, `docs/release/`, `docs/workflow/reviews/` are never created).

### 1.2 System-Wide Living Assets (Committed to Git)
Only system-wide rules and long-lived architectural records live in the Git repository:
- `AGENTS.md` — concise AI agent entry guide, build/test commands, and link to `docs/shared/engineering-standards.md`.
- `docs/shared/engineering-standards.md` — canonical repository-wide engineering, security, testing, and architecture rules (extracted and deduplicated from `AGENTS.md` by `team-init`).
- `docs/shared/glossary.md` *(optional, created on demand)* — canonical domain terminology and untranslated product terms.
- `docs/shared/adr/ADR-<NNN>-<slug>.md` *(optional, created on demand)* — Architecture Decision Records for long-lived, cross-module, or hard-to-reverse technical decisions.

---

## 2. The 4-Station, 7-Skill Architecture

| Station | Skill | Modes | Primary Responsibility | Jira & Local State Action |
|---|---|---|---|---|
| **Init** | `team-init` | — | Configure `.ai-workflow/team-config.json`, `.gitignore`, and extract `docs/shared/engineering-standards.md` from `AGENTS.md` | `jira_sync.py init` |
| **Router** | `team-next-step` | — | Read-only inspection of `team-config.json`, `.ai-workflow/active/<JIRA-KEY>.json`, and Jira state to recommend the exact next action | `jira_sync.py status` |
| **Station 1** | `team-spec` | — | One-pass Scope + `AC-*` + conditional UI/API Design + inline "抓大放小" review | `jira_sync.py sync-story` |
| **Station 2** | `team-dev` | `plan` / `implement` | Decompose into `TASK-001..N` (`Sub-task`), implement code & tests, and check off completed tasks/bugs | `jira_sync.py create-subtasks` / `complete-task` |
| **Station 3a** | `team-review` | — | Combined Dev Handoff & Code Review against ACs, design, and `engineering-standards.md` (fallback: `AGENTS.md`) | `jira_sync.py handoff` |
| **Station 3b** | `team-qa` | `plan` / `verify` | Parallel `TC-001..N` planning with AI pre-filled execution types; verify test results or file linked Jira `Bug` tickets | `jira_sync.py record-qa` / `create-bug` |
| **Station 4** | `team-release` | — | Verify all gates + zero open Bugs, post release summary, transition Story to `Done`, delete `.ai-workflow/active/<JIRA-KEY>.json` | `jira_sync.py close-story` |

---

## 3. Multi-Role Awareness & Smart Handoff Notification

Small teams frequently assign one person to multiple roles (e.g., PM + Release Manager, or Tech Lead + Developer). Role assignments live in `.ai-workflow/team-config.json` under `roles` (`pm`, `tech_lead`, `developer`, `reviewer`, `qa`, `release_manager`).

1. **Same-Person Consolidation (`from_role` person == `to_role` person):**
   - When the current station owner and the next station owner resolve to the **same person** (matching `jira_account_id`, `email`, or `name`), `jira_sync.py` records an `auto-consolidated` transition.
   - Do **not** pause for redundant handoff approval ceremonies or send self-`@mention` notifications.
   - **Independent Review Safeguard:** For `team-review`, if `developer` and `reviewer` map to the same person and `independent_reviewer` is configured in `team-config.json`, route to `independent_reviewer`; if it is a solo/tiny team with no separate reviewer, perform the AI three-axis code review and record `self_reviewed_with_ai: true` transparently in the Jira review comment.
2. **Cross-Person Handoff (`from_role` person != `to_role` person):**
   - When the next station belongs to a **different person**, `jira_sync.py` executes a 3-part Jira handoff:
     1. **Transition Status:** Moves the Jira issue to the target status mapped in `team-config.json` (e.g., `In QA`, `In Progress`, `Ready for Release`).
     2. **Reassign Issue:** Updates the Jira issue assignee to the target person's `jira_account_id`.
     3. **Post `@mention` Comment:** Posts the station summary with an `@mention` of the next owner, triggering Jira's native email/push notification.

---

## 4. Inline "抓大放小" (Focus on Material Risks, Auto-Default Minor Details)

Human review bandwidth is scarce. Every skill must enforce the **"抓大放小"** principle:

- **放小 (Auto-Default Routine Details):**
  - AI must never interrogate humans over conventional engineering, UI state, copy wording, or test-method details that have safe defaults under `docs/shared/engineering-standards.md` or existing codebase conventions.
  - Instead, AI makes the decision, applies it, and lists it in a concise **"AI 預設細節清單 (Auto-Decided Defaults)"** table so the human can review at a glance or override any item.
- **抓大 (Surface Only Material Decisions — Max 3 Questions):**
  - Ask the human **only** when a choice involves:
    1. Irreversible architecture, schema, or data-migration risk.
    2. Direct contradiction in product scope, business rules, or security policy.
    3. Major scope, cost, compatibility, or user-experience trade-off.
  - Ask **at most 3 material questions** in a single round. Every question must include a **Recommended Option (建議方案)** and a 1-sentence trade-off explanation.
  - If there are 0 material questions, present the summary and Auto-Decided Defaults directly for confirmation.

---

## 5. Jira Sync Modes & Credentials

`scripts/jira_sync.py` uses only the Python 3 standard library and operates in two modes:

1. **Live Jira REST API v3 Mode:**
   - Activated automatically when `JIRA_BASE_URL` (or `jira_base_url` in `team-config.json`), `JIRA_EMAIL`, and `JIRA_API_TOKEN` environment variables are present (and `--offline` is not set).
   - Syncs Stories, Sub-tasks, linked Bugs, status transitions, assignee changes, and ADF comments directly to Jira while updating `.ai-workflow/active/<JIRA-KEY>.json`.
2. **Local / Offline Mode:**
   - Activated automatically when Jira API credentials are not set in the environment (or when `--offline` is passed).
   - Updates `.ai-workflow/active/<JIRA-KEY>.json` deterministically and outputs the formatted Jira Markdown/payload so teams can work offline, in CI sandboxes, or paste directly into Jira.

**Security Rule:** Never hardcode `JIRA_API_TOKEN` or secrets in `team-config.json`, code, or Git commits. Read credentials exclusively from environment variables.

---

## 6. Language & Traceability Conventions

- **Default Output Language:** Write all human-facing descriptions, acceptance criteria, task summaries, review findings, QA verification tables, and release notes in **Traditional Chinese (繁體中文)** by default, unless the user requests another language.
- **Technical Identifiers:** Preserve Jira keys (`PROJ-123`), requirement/AC IDs (`AC-001`), task IDs (`TASK-001`), test case IDs (`TC-001`), review finding IDs (`REV-001`), ADR IDs (`ADR-001`), commit SHAs, file paths, and code identifiers in standard ASCII format.
- **Evidence Integrity:** Never fabricate git commits, test execution results, or human approvals. "Test code exists" is never "test passed." Missing test evidence is recorded as `not-executed`, never `pass`.
