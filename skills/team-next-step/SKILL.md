---
name: team-next-step
description: Use when the user asks what to do next for a feature or Jira Story, when workflow progress or blockers need to be inspected across .ai-workflow/active/<JIRA-KEY>.json and Jira, or when determining which station skill, mode, and role owns the next action.
---

# Team Next Step (Planning and Workflow Router)

## Overview

Read-only router for the Epic/Sprint planning layer and the 4-station, 8-skill Jira-centered engineering workflow. Inspect `.ai-workflow/team-config.json`, `.ai-workflow/active/<JIRA-KEY>.json`, and Jira planning plus Story/Sub-task/Bug state, then return one concrete next action, its owning skill and mode, the responsible role/person, and the completion condition. Respond in the user's language (default: Traditional Chinese / 繁體中文).

Read `references/common-workflow-governance.md` for the authoritative 4-station governance rules.

## Scope & Hard Boundaries

- **Single Entry Point (`$team-next-step`):** Team members never need to memorize the 8 skill names or `jira_sync.py` CLI flags. `$team-next-step` is the universal compass for planning and delivery.
- **Zero-CLI Burden & Auto-Hydration:** Execute `jira_sync.py status` automatically in the background. When `.ai-workflow/active/<JIRA-KEY>.json` is missing on a teammate's machine or secondary Git worktree, `jira_sync.py status --key <JIRA-KEY>` automatically hydrates the local state from the main Git worktree or remote Jira issue before routing.
- **Read-only inspection (except automatic state hydration):** Do not modify repository source code or transition Jira statuses during routing.
- If the user's prompt explicitly authorizes executing the next step (or if `same_person: true` auto-chaining is authorized), present the routing recommendation first, then invoke the selected skill within that authorization. Otherwise stop after the recommendation.
- Never guess which Jira Story to route by file modification time when multiple active stories exist in `.ai-workflow/active/` and the user did not specify one.
- Route explicit planning requests such as `建立 Epic`, `規劃 Sprint`, `查看 Backlog`, or `把 Story 放進 Sprint` to `team-plan` before Story delivery stations.
- Missing Sprint membership alone does not block `team-spec` or the four delivery stations unless the project has explicitly configured a sprint-required policy.

## Workflow

### 1. Check project initialization & auto-hydrate state
Run the status inspector from the repository root (prefer `.ai-workflow/bin/jira_sync.py`, or fall back to `<skill-dir>/scripts/jira_sync.py`):

```bash
python3 .ai-workflow/bin/jira_sync.py --repo-root . status [--key <JIRA-KEY>] [--fetch-remote]
# Or run explicit bidirectional reconciliation across active/remote Stories:
python3 .ai-workflow/bin/jira_sync.py --repo-root . pull [--key <JIRA-KEY>]
```

- If `.ai-workflow/team-config.json` does not exist (`initialized: false`), immediately recommend `$team-init`.
- If `requires_member_auth: true` (`credentials_missing: true` — a teammate cloned a live Jira repository whose `.ai-workflow/credentials.json` is gitignored), immediately display `claimed_roles` and `unclaimed_roles` from `status`, ask **which role(s) they own** (`--role`, e.g. `developer`, `qa`, or `reviewer,qa`; supports appending multiple members per role and optional `--specialty`) plus their **personal Jira Email + API Token** (and Qase Token if claiming `qa` when `qase_required: true`), run `jira_sync.py auth --role <ROLES> [--specialty <SPEC>] --jira-email <EMAIL> --jira-token <TOKEN>`, and then immediately re-run `status`!
- **Bidirectional Live Jira Reconciliation & Standalone Bug Discovery:** Whenever live credentials are configured, `status` and `pull` automatically discover newly created Jira Stories **and unlinked standalone production `Bug` tickets** (`POST /rest/api/3/search/jql`, excluding Story-linked child Bugs so child defects stay under their parent Story) and reconcile manual Jira Web UI edits (Sub-tasks added/completed/reopened/reassigned, linked Bugs added/closed, Story status/assignee changes, and Story Description `AC-###` additions). Standalone `Bug` tickets hydrate as `issue_kind: "standalone_bug"` (preserving their original reporter description) and route directly to `team-dev` (`implement`).
- If multiple `.ai-workflow/active/*.json` files exist and no `<JIRA-KEY>` was specified (`ambiguous: true`), ask one concise question listing the active Jira keys so the user can choose.

### 2. Evaluate station state and blockers

Follow this strict priority order (matching `jira_sync.py status`):

| Priority | Observed State / Blocker | Recommended Skill & Mode | Responsible Role |
|---|---|---|---|
| **0** | `.ai-workflow/team-config.json` missing | `team-init` | Tech Lead / PM |
| **0b** | `team-config.json` has `jira_base_url`, but local `.ai-workflow/credentials.json` is missing (`requires_member_auth: true`) | `team-init` (`auth` mode) | Current Collaborator |
| **1** | Explicit Epic/Sprint/Backlog planning request, or a required board/Sprint choice is missing | `team-plan` (`epic` / `sprint` / `commit` / `status`) | `pm` |
| **2** | No active Story yet, or `spec_status != "approved"` | `team-spec` | `pm` (+ `tech_lead`) |
| **3** | Open Bug ticket (`bugs` with `status == "open"`) from QA or Review | `team-dev` (`implement` mode) | `developer` |
| **4** | Spec approved, but no `tasks` (`TASK-001..N`) planned yet | `team-dev` (`plan` mode) *(parallel: `team-qa` `plan` mode)* | `developer` |
| **5** | Pending `TASK-001..N` (`status != "completed"`) | `team-dev` (`implement` mode) | `developer` |
| **6** | Code review requested changes (`review.status == "changes-requested"`) | `team-dev` (`implement` mode) | `developer` |
| **7** | All `tasks` completed, `review.status != "approved"`, or `reviewed_commit != candidate_commit` | `team-review` | `reviewer` |
| **8** | Review approved, but `qa.test_cases` (`TC-001..N`) not planned yet | `team-qa` (`plan` mode) | `qa` |
| **9** | Review approved & `TC-*` planned, but `qa.status != "passed"` or `tested_commit != candidate_commit` | `team-qa` (`verify` mode — surfaces `pending_automated_tcs`, `pending_external_tcs`, and `pending_manual_tcs`) | `qa` |
| **10** | Spec approved + Tasks completed + Review approved + QA passed + 0 open Bugs + commits aligned | `team-release` | `release_manager` |
| **11** | Jira Story is already `Done` and `.ai-workflow/active/<JIRA-KEY>.json` is removed | No remaining action for this Story | — |

### 3. Parallel & fallback rules

- **QA Plan Parallelism:** Once `team-spec` is approved, `team-qa` (`plan` mode) can run in parallel with `team-dev` (`plan` and `implement` modes). Development never blocks waiting for QA test case planning.
- **Engineering Standards Fallback:** If `docs/shared/engineering-standards.md` is missing during `team-review`, note that `team-review` will fall back to `AGENTS.md` (or recommend `team-init` if the team wants to extract `docs/shared/engineering-standards.md` first).
- **Candidate Commit Drift:** If product code changed (`candidate_commit` updated) after `team-review` or `team-qa` passed, flag the commit mismatch (`reviewed_commit` / `tested_commit` vs `candidate_commit`) and route back to `team-review` / `team-qa` (`verify` mode) before allowing `team-release`.

## Output Format

Return these five items in the user's language (default: 繁體中文):

1. **目前狀態 (Current State):** Jira Story key (`<JIRA-KEY>`), current station, and verified progress or blocker.
2. **判斷依據 (Evidence):** Key fields from `.ai-workflow/active/<JIRA-KEY>.json` / `jira_sync.py status` (e.g., `spec_status`, completed/pending `TASK-*`, `review.status`, `qa.status`, open `BUG-*`, commit alignment).
3. **下一步行動 (Next Action):** Exact skill name (`team-init`, `team-plan`, `team-spec`, `team-dev`, `team-review`, `team-qa`, or `team-release`), explicit mode (`epic` / `sprint` / `commit` / `status` / `plan` / `implement` / `verify` when applicable), and responsible role + person from `team-config.json` (noting whether the transition is same-person auto-consolidated or cross-person Jira notification).
4. **執行指令 / Prompt (Ready-to-Use Input):** Copyable prompt or CLI command for the next step.
5. **完成條件 (Done When):** The observable state change in Jira and `.ai-workflow/active/<JIRA-KEY>.json` that completes the recommended step.
