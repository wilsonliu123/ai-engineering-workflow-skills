---
name: team-release
description: Use when a Jira Story has passed Spec, Sub-task implementation, Code Review, and QA Verification with zero open Bugs and is ready for final release reconciliation, scale-aware HTML living manual generation/update under docs/manuals/<manual-name>/, Jira Story closure (Done), and cleanup of temporary local .ai-workflow/active/<JIRA-KEY>.json scaffolding.
---

# Team Release (Station 4 — Release Verification, Scale-Aware HTML Manual Sync, Jira Closure & Scaffolding Cleanup)

## Overview

Station 4 of the 4-station Jira-centered workflow. Verifies that Spec, Sub-tasks, Code Review, and QA Verification are all complete and aligned on the candidate commit with zero open Bugs, performs **scale-aware Living HTML Manual synchronization** under `docs/manuals/<manual-name>/<manual-name>.html` (distilling the `user-manual-writing` standard), posts the final Release Summary to the Jira `User Story`, transitions the Jira Story status to `Done`, and removes `.ai-workflow/active/<JIRA-KEY>.json` so the Git repository stays 100% free of per-feature process files.

Read `references/common-workflow-governance.md` and `references/release-method.md` before closing a Story.

## Hard Boundaries

- **No Per-Feature Process Markdown Files in Git:** Never create `docs/release/` files. Per-Story release records belong in the Jira `User Story`.
- **HTML-Only Living Manual Layout:** Product/API manuals live exclusively under `docs/manuals/<manual-name>/<manual-name>.html` (with portable relative `./screenshots/` and `./diagrams/` inside that same `<manual-name>/` folder). Do not emit `.md`, `.pdf`, or `.docx` manual files unless the user explicitly asks, and never perform unsolicited Git branch/tag/push operations.
- **Strict Gate Enforcement:** Never close a Story or delete `.ai-workflow/active/<JIRA-KEY>.json` if any Sub-task is incomplete, Code Review or QA is unapproved/failed, open Bugs remain, or the candidate commit drifted after Review/QA.
- **No Unsolicited Production Deployment:** Closing the Jira Story and recording the release summary does not execute external production deployment scripts unless the user explicitly requests deployment.

## Workflow

### 1. Reconcile gates and commit alignment
1. Inspect `.ai-workflow/active/<JIRA-KEY>.json` (or run `jira_sync.py status --key <JIRA-KEY>`).
2. Confirm all 5 release gates hold for the verified candidate commit (`<candidate-commit-sha>`):
   - `spec_status == "approved"`
   - All `tasks` (`TASK-001..N`) have `status == "completed"`
   - `review.status == "approved"` and `review.reviewed_commit == <candidate-commit-sha>`
   - `qa.status == "passed"` and `qa.tested_commit == <candidate-commit-sha>`
   - Zero open `Bug` tickets (`bugs` with `status not in ("completed", "closed")`)
3. Check whether any **System-Wide Living Assets** (`AGENTS.md`, `docs/shared/engineering-standards.md`, `docs/shared/glossary.md`, `docs/shared/adr/`) were updated during this feature.

### 2. Scale-aware Living HTML Manual sync (`docs/manuals/<manual-name>/`)
Evaluate the Story's user-facing impact (`change_surface`, `acceptance_criteria`, `tasks`, `bugs`) and existing folders under `docs/manuals/` using the **4-Tier Decision Matrix** in `references/release-method.md`:

| Delivery Scenario | `docs/manuals/<manual-name>/` Action |
|---|---|
| **① `0 -> 1` Greenfield / First Manual** | **Ask the user for `<manual-name>`** (strip `.html` if included), then generate `docs/manuals/<manual-name>/<manual-name>.html` + `./screenshots/` + `./diagrams/` using the full 3-layer source reconciliation, 4 screenshot quality gates, anti-stub tables/steps, and dark-sidebar HTML template (or API/state-machine HTML manual when `has_ui: false`). |
| **② Major Feature (`新增大塊需求`)** | Inspect `docs/manuals/*/`. Confirm with the user whether to **incrementally update** an existing `docs/manuals/<existing-name>/<existing-name>.html` or **create a new folder** `docs/manuals/<new-name>/<new-name>.html` (ask for `<new-name>`). Reconcile only new/changed routes/tabs/modals, capture/audit only affected screenshots, and surgically update affected `<section>`s, sidebar TOC, and Appendix Coverage Matrix. |
| **③ Minor Enhancement (`局部小需求`)** | Inspect existing `docs/manuals/<manual-name>/<manual-name>.html`. Update affected field table, step text, or screenshot **only if** existing manual content became stale/contradictory; otherwise `Skip`. |
| **④ Bug Fix / Internal Refactor (`修正問題`)** | **Default `Skip` (`0` manual edits)** unless the fix intentionally changed documented user-facing workflow or Troubleshooting steps. |

**Mandatory `user-manual-writing` Essence (when creating or updating HTML manuals):**
- **Bring-Your-Own-Skill Hook:** If `user-manual-writing` (or `drawio-skill`) is installed in the environment, read its guidelines/templates as needed while strictly enforcing HTML-only output at `docs/manuals/<manual-name>/<manual-name>.html` and **portable relative asset paths** (`./screenshots/...`, `./diagrams/...` — never leave machine-local `file:///Users/...` URIs or multi-MB Base64 blobs in HTML). If not installed, follow the self-contained protocol and HTML shell in `references/release-method.md`.
- **5 Non-Negotiable Quality Gates:** (1) 3-Layer Source Coverage Reconciliation (L1 routes & role-gated menus, L2 Tabs/Drawers/Modals, L3 global controls & verbatim UI/i18n labels), (2) 4 Screenshot Gates (`has_ui: true`: role-matched error-free UI, expanded secondary drawers/modals with seed data, multimodal `read_file` visual audit + SHA-256 deduplication, visual callout badges + 3-element `<figcaption>`), (3) Decision-useful Architecture / State-Machine Diagrams (apply the diagram checks in `references/release-method.md`; no CJK ASCII `+---+` boxes), (4) Anti-Stub Rule (every screenshot section has a field/control `<table>` or numbered `<ol>`), (5) Dark-Sidebar CJK-Safe HTML Layout.
- **Candidate Commit Anchor Preservation:** Keep `<candidate-commit-sha>` (`story.candidate_commit`) as the `--commit` anchor passed to `close-story` so Gate 3 (`reviewed_commit`) and Gate 4 (`tested_commit`) remain aligned even when `docs/manuals/<manual-name>/` is updated during Station 4. Record the manual action (`created` / `updated` / `skipped` + path) and diagram decision (`created` / `updated` / `unchanged`) in `--notes`.

### 3. Run `close-story` to finalize Jira & clean up active state
Execute `jira_sync.py close-story` from the repository root (prefer `.ai-workflow/bin/jira_sync.py` or fall back to `<skill-dir>/scripts/jira_sync.py`; pass `--commit "<candidate-commit-sha>"` explicitly if a docs commit changed `HEAD` after QA, and `--version` defaults to `v1.0.0` when omitted):

```bash
python3 .ai-workflow/bin/jira_sync.py --repo-root . close-story \
  --key "<JIRA-KEY>" \
  [--version "<version-or-tag>"] \
  --commit "<candidate-commit-sha>" \
  --notes "<Release summary: delivered scope, HTML manual status (docs/manuals/<name>/<name>.html or skipped), migration/rollback notes, residual risks>"
```

What `jira_sync.py close-story` does deterministically:
1. Reconciles live Jira state first (catching any Sub-tasks reopened or Bugs newly linked in Jira Web UI) and validates all 5 release readiness gates (exits non-zero with `unmet_gates` if any check fails).
2. Assigns the Story to the acting `release_manager` and posts the final Release Summary comment in Traditional Chinese by default (version, verified commit SHA, AC/Sub-task/TC coverage counts with `qa_breakdown` across `automated`/`external`/`manual`, and release notes) to the Jira `User Story`.
3. Transitions the Jira `User Story` to `Done` (`status_mapping.done`).
4. **Deletes `.ai-workflow/active/<JIRA-KEY>.json`** (including any main worktree mirror) so no temporary per-feature scaffolding remains on disk.

### 4. Present final delivery report
Summarize the completed release in Traditional Chinese (繁體中文):
- Jira Story `<JIRA-KEY>` transitioned to `Done`.
- Release version/tag and verified commit SHA.
- Total ACs, Sub-tasks, and QA Test Cases verified (plus any closed Bugs resolved along the way).
- Living HTML Manual status (`docs/manuals/<manual-name>/<manual-name>.html` created / incrementally updated / skipped with reason).
- Confirmation that `.ai-workflow/active/<JIRA-KEY>.json` has been cleaned up.

## Red Flags — STOP

- Manually deleting `.ai-workflow/active/<JIRA-KEY>.json` when `jira_sync.py close-story` reports `blocked: true`.
- Releasing a feature code commit SHA that differs from `review.reviewed_commit` or `qa.tested_commit`.
- Inventing a new manual folder name under `docs/manuals/` without asking the user for `<manual-name>`.
- Emitting `.md`, `.pdf`, or `.docx` manual duplicates, hardcoding `file:///Users/...` local image paths in `<manual-name>.html`, or writing `docs/release/<feature>/release-handoff.md` into Git.
