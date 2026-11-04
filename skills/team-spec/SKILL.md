---
name: team-spec
description: Use when starting a new feature, User Story, change request, or bug-driven requirement that needs scope definition, testable acceptance criteria (AC-*), conditional UI/UX state design, and technical architecture design synced to Jira and .ai-workflow/active/<JIRA-KEY>.json before development begins.
---

# Team Spec (Station 1 — Unified Specification, UX & Technical Design)

## Overview

Station 1 of the 4-station Jira-centered workflow. Consolidates product requirements, conditional UI/UX state specification, technical architecture design, **6-Dimension AC Completeness Checks**, and **Scale-Aware "抓大放小"** review into a unified specification flow:
- **Small / Clear Requirements (One-Pass Track):** Drafts Scope, `AC-*`, conditional UI/API design, and Auto-Decided Defaults in one pass (`<= 3` material questions) -> `--status approved`.
- **Medium / Large or Ambiguous Requirements (Iterative `draft` Deep-Dive Track):** Uses multi-round `--status draft` checkpoints (asking 3–5 focused business/boundary questions per round across multiple rounds, accumulating all confirmed `material_decisions`, and verifying the 6-Dimension AC Checklist) before marking `--status approved`.

Syncs the specification directly to the Jira `User Story` and `.ai-workflow/active/<JIRA-KEY>.json` without creating per-feature markdown files in the Git repository.

Read `references/common-workflow-governance.md` and `references/spec-method.md` before drafting.

## Hard Boundaries

- **No Per-Feature Markdown Files in Git:** Never create `docs/requirements/`, `docs/ux/`, or `docs/design/` files. The Jira `User Story` is the permanent asset; `.ai-workflow/active/<JIRA-KEY>.json` is the gitignored active state.
- **Living System Assets Only:** You may update `docs/shared/glossary.md` (when new domain terms arise) or create `docs/shared/adr/ADR-<NNN>-<slug>.md` (only for long-lived, cross-module, hard-to-reverse architectural decisions).
- **No Empty Placeholders:** Include UI states/wireframes/copy **only** when `has_ui` is true; include cross-boundary API contracts **only** when `has_api` is true.
- **Never Rush Large Features in One Pass:** For complex, multi-role, cross-module, or high-risk features, always iterate via `sync-story --status draft` until all 6 AC dimensions are complete.
- **Do Not Modify Product Code:** Station 1 produces the approved specification; implementation belongs to `team-dev`.

## Quick Reference: Scale-Aware "抓大放小" Review Principle

| Decision Category / Scale | Handling Rule | Output Location |
|---|---|---|
| **放小 (Conventional Details)** | Pagination defaults, standard HTTP error codes, skeleton/empty UI states, standard input trim/length rules, routine component choices | Decided automatically by AI and listed in the **AI 預設細節清單 (Auto-Decided Defaults)** table |
| **抓大 — Small / Clear Track** | Small feature (`<= 3` ACs, clear scope, low/medium risk) with 0–3 material decisions | One-pass review (at most 3 questions with **建議方案**), then `sync-story --status approved` |
| **抓大 — Large / Complex Track** | Complex/ambiguous feature (`> 3` ACs, multi-role, schema migration, external integration, or `high`/`critical` risk) | Multi-round **Iterative `draft` Deep-Dive** (`sync-story --status draft`, 3–5 focused questions per round, unlimited accumulated `material_decisions`) until 6-Dimension AC Checklist is 100% covered, then `--status approved` |

## Workflow

### 1. Inspect repository & determine change surface
1. Verify `.ai-workflow/team-config.json` exists (if missing, run or recommend `$team-init`).
2. Read `AGENTS.md`, `docs/shared/engineering-standards.md`, optional `docs/shared/glossary.md` / `docs/shared/adr/`, and relevant existing source files.
3. Resolve the Jira Story key (`<JIRA-KEY>`, e.g., `PROJ-101`).
4. Classify the **Change Surface** from codebase facts:
   - `has_ui`: Does this feature add or modify user-facing screens, components, or copy?
   - `has_api`: Does this feature add or modify HTTP/gRPC/GraphQL endpoints, events, or cross-service contracts?
   - `has_data`: Does this feature add or modify database tables, migrations, caches, or persistent state?

### 2. Draft the unified specification & check the 6-Dimension AC Checklist
Follow `references/spec-method.md` to prepare:
1. **Scope (In-Scope & Out-of-Scope):** Explicit boundaries and non-goals.
2. **Testable Acceptance Criteria (`AC-001..N`) Checked Against All 6 Dimensions:**
   - Every criterion must be objectively verifiable (clear trigger, observable outcome, boundary/error behavior).
   - Verify the **6-Dimension AC Completeness Checklist** (`references/spec-method.md` §2):
     1. Happy Path (核心主流程)
     2. Role / AuthZ / Tenant Isolation (角色權限與租戶隔離)
     3. Input Boundary & Idempotency (輸入邊界、格式與冪等性)
     4. State Transitions & Concurrency (狀態流轉與併發衝突)
     5. External Dependency Failure & Compensation (外部依賴失敗、超時與補償回滾)
     6. Legacy Data Compatibility & Migration (舊資料相容性與遷移影響)
3. **Technical Design Summary:**
   - Affected modules/files, runtime flow, data/migration impact, and security/permission enforcement points.
   - **Conditional UI Spec (`has_ui: true` only):** Screen inventory, situation states (`normal`, `loading`, `empty`, `invalid`, `403/404`), ASCII wireframe or Figma link, and key UI copy.
   - **Conditional API Contract (`has_api: true` only):** Endpoint method/path, request/response fields, validation rules, and error codes.
4. **Auto-Decided Defaults ("放小"):** List 3–8 routine engineering/UX choices AI made automatically based on repository conventions so the user does not have to answer trivial questions.

### 3. Present Scale-Aware "抓大放小" review (`One-Pass` vs. `Iterative Draft`)
Present the structured specification in Traditional Chinese (繁體中文):
- **Track 1 — Small / Clear Requirement (`<= 3` Material Questions):**
  - If 1–3 material questions exist, show the Spec Summary + **AI 預設細節清單**, followed by **1–3 Material Questions** (each with a recommended answer and trade-offs). Wait for the user's answer, then sync as `--status approved`.
  - If 0 material questions exist, show the Spec Summary + **AI 預設細節清單** and ask for one-step confirmation to approve.
- **Track 2 — Medium / Large or Ambiguous Requirement (Iterative `draft` Deep-Dive):**
  - Persist the current specification draft via `jira_sync.py sync-story --status draft` (recording any decisions confirmed so far).
  - Present the current draft summary, highlight which of the **6 AC Dimensions** still have open ambiguities, and ask **3–5 focused business/boundary questions** for this round (each with a recommended option).
  - After each user reply, run `sync-story --status draft` to accumulate the newly confirmed `--material-decision` items and refined `--ac` list. Repeat until all ambiguities are resolved and the 6-Dimension AC Checklist is complete, then run `sync-story --status approved`.

### 4. Sync approved spec to Jira & `.ai-workflow/active/<JIRA-KEY>.json`
Once the user confirms approval in natural language (e.g., `"確認"`, `"核准"`), run `jira_sync.py sync-story` automatically in the background (**Zero-CLI Burden** — never ask the human to type CLI flags). Prefer `.ai-workflow/bin/jira_sync.py` (or fall back to `<skill-dir>/scripts/jira_sync.py`):

```bash
python3 .ai-workflow/bin/jira_sync.py --repo-root . sync-story \
  --key "<JIRA-KEY>" \
  --title "<Story Title>" \
  --scope-in "<In-Scope Summary>" \
  --scope-out "<Out-of-Scope Summary>" \
  [--has-ui] [--has-api] [--has-data] \
  --risk-level "<low|medium|high|critical>" \
  --ac "AC-001:<Testable criterion 1>" \
  --ac "AC-002:<Testable criterion 2>" \
  --design-summary "<Technical architecture & module impact>" \
  [--ui-spec "<UI states, wireframe, copy>"] \
  [--api-spec "<Endpoint & schema contract>"] \
  --auto-decision "<Conventional detail 1 decided by AI>" \
  [--material-decision "<Confirmed material decision 1>"] \
  --status approved
```

### 5. Role-aware handoff & Auto-Chaining to Station 2 (`team-dev`)
`jira_sync.py sync-story --status approved` automatically embeds the `[ai-workflow-state]` snapshot for cross-machine hydration and checks `.ai-workflow/team-config.json`:
- **Same person (`pm` == `developer`, `same_person: true`):** Auto-consolidates the transition without notification spam; **proactively auto-chain** into `$team-dev` (`plan` mode, or Dev Fast-Track for single-task stories) or ask in one line: `"下一站（Station 2 任務拆解與開發）同由您負責，是否直接繼續執行？"`.
- **Different person (`pm` != `developer`):** Transitions the Jira Story to `In Progress`, reassigns it to the Developer, and posts an `@mention` comment on Jira. Note that `$team-qa` (`plan` mode) can also run in parallel.

## Red Flags — STOP

- Asking more than 3 questions or asking the human to decide routine pagination/toast/status-code details.
- Generating UI state tables for a backend-only change, or API schemas for a pure styling change.
- Writing `docs/requirements/*.md` or `docs/design/*.md` files into the repository.
- Marking `--status approved` before the user confirms the spec in chat.
