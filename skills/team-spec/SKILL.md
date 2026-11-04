---
name: team-spec
description: Use when starting a new feature, User Story, change request, or bug-driven requirement that needs scope definition, testable acceptance criteria (AC-*), conditional UI/UX state design, and technical architecture design synced to Jira and .ai-workflow/active/<JIRA-KEY>.json before development begins.
---

# Team Spec (Station 1 — Unified Specification, UX & Technical Design)

## Overview

Station 1 of the 4-station Jira-centered workflow. Consolidates product requirements, conditional UI/UX state specification, technical architecture design, testability checks, and inline **"抓大放小"** review into a single pass. Syncs the approved specification directly to the Jira `User Story` and `.ai-workflow/active/<JIRA-KEY>.json` without creating per-feature markdown files in the Git repository.

Read `references/common-workflow-governance.md` and `references/spec-method.md` before drafting.

## Hard Boundaries

- **No Per-Feature Markdown Files in Git:** Never create `docs/requirements/`, `docs/ux/`, or `docs/design/` files. The Jira `User Story` is the permanent asset; `.ai-workflow/active/<JIRA-KEY>.json` is the gitignored active state.
- **Living System Assets Only:** You may update `docs/shared/glossary.md` (when new domain terms arise) or create `docs/shared/adr/ADR-<NNN>-<slug>.md` (only for long-lived, cross-module, hard-to-reverse architectural decisions).
- **No Empty Placeholders:** Include UI states/wireframes/copy **only** when `has_ui` is true; include cross-boundary API contracts **only** when `has_api` is true.
- **Do Not Modify Product Code:** Station 1 produces the approved specification; implementation belongs to `team-dev`.

## Quick Reference: Inline "抓大放小" Review Principle

| Decision Category | Handling Rule | Output Location |
|---|---|---|
| **放小 (Conventional Details)** | Pagination defaults, standard HTTP error codes, skeleton/empty UI states, standard input trim/length rules, routine component choices | Decided automatically by AI and listed in the **AI 預設細節清單 (Auto-Decided Defaults)** table |
| **抓大 (Material Decisions)** | Irreversible schema/architecture risk, direct business-rule contradiction, or major scope/cost/security trade-off | Ask **at most 3 questions** in one round, each with a **建議方案 (Recommended Option)** and trade-off |

## Workflow

### 1. Inspect repository & determine change surface
1. Verify `.ai-workflow/team-config.json` exists (if missing, run or recommend `$team-init`).
2. Read `AGENTS.md`, `docs/shared/engineering-standards.md`, optional `docs/shared/glossary.md` / `docs/shared/adr/`, and relevant existing source files.
3. Resolve the Jira Story key (`<JIRA-KEY>`, e.g., `PROJ-101`).
4. Classify the **Change Surface** from codebase facts:
   - `has_ui`: Does this feature add or modify user-facing screens, components, or copy?
   - `has_api`: Does this feature add or modify HTTP/gRPC/GraphQL endpoints, events, or cross-service contracts?
   - `has_data`: Does this feature add or modify database tables, migrations, caches, or persistent state?

### 2. Draft the unified specification in one pass
Follow `references/spec-method.md` to prepare:
1. **Scope (In-Scope & Out-of-Scope):** Explicit boundaries and non-goals.
2. **Testable Acceptance Criteria (`AC-001..N`):**
   - Every criterion must be objectively verifiable (clear trigger, observable outcome, boundary/error behavior).
   - Built-in testability check: eliminate vague adjectives ("fast", "user-friendly") before showing the user.
3. **Technical Design Summary:**
   - Affected modules/files, runtime flow, data/migration impact, and security/permission enforcement points.
   - **Conditional UI Spec (`has_ui: true` only):** Screen inventory, situation states (`normal`, `loading`, `empty`, `invalid`, `403/404`), ASCII wireframe or Figma link, and key UI copy.
   - **Conditional API Contract (`has_api: true` only):** Endpoint method/path, request/response fields, validation rules, and error codes.
4. **Auto-Decided Defaults ("放小"):** List 3–8 routine engineering/UX choices AI made automatically based on repository conventions so the user does not have to answer trivial questions.

### 3. Present inline "抓大放小" review
Present the structured specification in Traditional Chinese (繁體中文):
- **Case A (1 to 3 Material Questions exist):** Show the Spec Summary + **AI 預設細節清單**, followed by **at most 3 Material Questions** (each with a recommended answer and trade-offs). Wait for the user's answer.
- **Case B (0 Material Questions exist):** Show the Spec Summary + **AI 預設細節清單** and ask for one-step confirmation to approve and sync to Jira.

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
