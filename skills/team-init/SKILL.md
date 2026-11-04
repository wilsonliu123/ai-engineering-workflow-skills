---
name: team-init
description: Use when onboarding a repository to the team's AI engineering workflow, configuring or updating role-to-person mappings and Jira integration settings in .ai-workflow/team-config.json, or extracting and deduplicating docs/shared/engineering-standards.md from AGENTS.md before starting feature work.
---

# Team Init (Project & Role Initialization)

## Overview

Initialize a repository once for the 4-station Jira-centered small-team workflow. Establish role-to-person mappings (supporting one person holding multiple roles), Jira project/status configuration, `.gitignore` hygiene for temporary active state, and a single source of truth for `docs/shared/engineering-standards.md` without duplicating rules in `AGENTS.md`.

Read `references/common-workflow-governance.md` for the authoritative asset boundary and multi-role handoff rules.

## When to Use

- Onboarding a new or existing repository to the `ai-engineering-workflow` skill suite.
- `team-next-step` or any station skill reports that `.ai-workflow/team-config.json` is missing.
- Adding, changing, or consolidating team members across roles (`pm`, `tech_lead`, `developer`, `reviewer`, `qa`, `release_manager`).
- Extracting engineering rules from `AGENTS.md` into `docs/shared/engineering-standards.md` so the two files never diverge.

**When NOT to use:**
- Routine per-feature work when `.ai-workflow/team-config.json` and `docs/shared/engineering-standards.md` are already configured (use `team-spec` or `team-next-step`).

## Quick Reference

| Output / Target | Purpose | Committed to Git? |
|---|---|---|
| `.ai-workflow/team-config.json` | Project key, Jira base URL, status mapping, and role-to-person mapping | Yes |
| `.gitignore` | Ignores `.ai-workflow/active/` temporary per-feature JSON scaffolding | Yes |
| `docs/shared/engineering-standards.md` | Canonical repository-wide engineering, testing, and security standards | Yes |
| `AGENTS.md` | Trimmed of duplicated standards; links to `docs/shared/engineering-standards.md` | Yes |

## Workflow

### 1. Inspect existing repository state
1. Check whether `.ai-workflow/team-config.json`, `AGENTS.md`, `docs/shared/engineering-standards.md`, and `.gitignore` exist.
2. Apply the **"抓大放小"** principle:
   - Auto-detect existing stack conventions, test commands, and linter rules from the repository (`package.json`, `pyproject.toml`, `go.mod`, `Makefile`, `AGENTS.md`).
   - Ask the user **at most 3 concise setup questions** (or 1 combined question) if not already provided:
     1. **Jira Project Key & Base URL** (e.g., `PROJ`, `https://your-org.atlassian.net`; default `PROJ` if offline/local).
     2. **Role-to-Person Mapping**: Who holds `pm`, `tech_lead`, `developer`, `reviewer`, `qa`, and `release_manager`? Explicitly note that **one person can hold multiple roles** (when adjacent stations belong to the same person, the workflow auto-consolidates handoffs without redundant notifications or self-approval ceremonies).
     3. **Custom Jira Status Names** (only if different from standard `To Do`, `In Progress`, `In Review`, `In QA`, `Ready for Release`, `Done`).

### 2. Run deterministic initialization
Execute the bundled `scripts/jira_sync.py init` command from the repository root:

```bash
python3 <skill-dir>/scripts/jira_sync.py --repo-root . init \
  --project-key "<PROJECT_KEY>" \
  --jira-url "<JIRA_BASE_URL>" \
  --roles-json '<JSON_MAPPING>'
```

What `jira_sync.py init` enforces automatically:
- Creates or updates `.ai-workflow/team-config.json` (template: `references/team-config-template.json`).
- Appends `.ai-workflow/active/` to `.gitignore` if not already present.
- Extracts engineering/coding/testing/security sections from `AGENTS.md` into `docs/shared/engineering-standards.md` (or creates a starter from `references/engineering-standards-template.md` if `AGENTS.md` has none) and **replaces the duplicated sections in `AGENTS.md` with a reference link** to `docs/shared/engineering-standards.md`.

### 3. Verify and summarize
1. Verify that `docs/shared/engineering-standards.md` reflects the repository's actual stack, test commands, and security boundaries. Refine the extracted markdown if needed.
2. Present a concise initialization summary in Traditional Chinese (繁體中文):
   - Role consolidation map (which roles are shared by the same person vs. which role transitions will trigger Jira `@mention` + assignee + status notifications).
   - How to enable live Jira REST API v3 sync via environment variables (`JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`) vs. zero-config offline mode.
   - Next step: run `$team-spec` (or `$team-next-step`) to start the first Jira Story.

## Red Flags — STOP

- **Hardcoding secrets:** Never write `JIRA_API_TOKEN` or passwords into `.ai-workflow/team-config.json` or `AGENTS.md`.
- **Duplicating rules across `AGENTS.md` and `engineering-standards.md`:** Always keep detailed engineering standards in `docs/shared/engineering-standards.md` and leave only a pointer in `AGENTS.md`.
- **Creating per-feature `docs/` folders:** Never create `docs/requirements/`, `docs/design/`, or `docs/qa/` directories; per-feature artifacts belong in Jira and `.ai-workflow/active/<JIRA-KEY>.json`.
