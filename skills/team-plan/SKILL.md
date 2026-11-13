---
name: team-plan
description: Use when the user wants to create, plan, inspect, or update a Jira Epic or Sprint, define a Sprint goal or date range, choose Story/Task/Bug issues from a backlog, place issues into a Sprint, or review current Sprint membership. Trigger on requests such as 規劃 Sprint、建立下一個 Sprint、把 Story 放進 Sprint、查看 Backlog、整理 Epic 進度 or sprint goal/date planning. Use this skill before the Story delivery stations when the request changes planning scope rather than implementation state.
---

# Team Plan (Epic and Sprint Planning Layer)

Read `references/common-workflow-governance.md` before changing Jira planning state. This skill owns planning context; `team-spec`, `team-dev`, `team-review`, `team-qa`, and `team-release` continue to own one Story's delivery lifecycle.

## Planning model

- **Epic** is the product container and can span multiple Sprints.
- **Sprint** is a board timebox with a name, goal, dates, and membership.
- **Story, Task, and Bug** are Sprint members by default.
- **Sub-task** follows its parent Story and is not planned as an independent Sprint item.
- **Jira is the source of truth.** Do not create per-feature planning Markdown files or copy full Sprint state into `.ai-workflow/active/<JIRA-KEY>.json`.

## Modes

Choose the smallest mode that matches the request:

| Mode | Use | Result |
|---|---|---|
| `epic` | Adopt or create the Epic context | Epic key, goal, scope, and candidate Story boundary |
| `sprint` | Create or inspect a Future/Active Sprint | Sprint payload and candidate membership |
| `commit` | Put selected issues into an existing or newly created Sprint | Applied issue keys and Jira Sprint id |
| `status` | Read current board/Sprint state | Sprint state, issue counts, and exclusions |

If an Epic already exists in Jira, adopt its key. Do not create a duplicate Epic. The current repository does not provide a separate Epic-create command; keep Epic creation in Jira or add it to this planning layer only when explicitly requested.

## Workflow

### 1. Inspect planning context

1. Load `.ai-workflow/team-config.json` and credentials through the project CLI.
2. Resolve the Jira Scrum board from `jira_board_id` or `--board-id`.
3. If multiple Scrum boards match the project, ask the user to choose the board. Never guess.
4. List Future and Active Sprints before proposing a new one.

Use the project-local CLI when available:

```bash
python3 .ai-workflow/bin/jira_sync.py --repo-root . sprint-status --board-id "<BOARD-ID>"
```

### 2. Build the Sprint preview

Collect candidate issues from the user's work descriptions, an Epic key, or a user-supplied JQL query. Treat Jira keys as an internal write target; accept them directly only when the user already provides them. Resolve descriptions to Jira issues before presenting the preview. By default:

- Exclude Epic and Sub-task issues.
- Exclude completed issues (`Done`, `Closed`, `Resolved`).
- Keep Story, Task, and Bug issues visible with their exclusion reason when they are not selected.
- Do not alter Story status, assignee, review, QA, or release fields.

When a description matches multiple Issues, show the candidates and ask the user to choose. Do not guess from similar summaries. Use the Jira Issue Summary as the primary display text and include a short Description or AC excerpt only when it helps distinguish matches.

Preview a new Future Sprint:

```bash
python3 .ai-workflow/bin/jira_sync.py --repo-root . sprint-plan \
  --board-id "<BOARD-ID>" \
  --name "<SPRINT-NAME>" \
  --goal "<SPRINT-GOAL>" \
  --start-date "<ISO-8601>" \
  --end-date "<ISO-8601>" \
  --issue-key "<STORY-KEY>"
```

Preview an existing Sprint membership change with `--sprint-id`. Use `--epic-key` or `--jql` when the candidate list should be discovered from Jira.

### 3. Apply only after explicit confirmation

Live Jira mutation requires a clear user confirmation after the preview. Then rerun the same command with `--apply`:

```bash
python3 .ai-workflow/bin/jira_sync.py --repo-root . sprint-plan \
  --sprint-id "<SPRINT-ID>" \
  --name "<NEW-SPRINT-NAME>" \
  --issue-key "<STORY-KEY>" \
  --apply
```

When creating a new Sprint, omit `--sprint-id`; the CLI creates a Future Sprint first, then moves selected issues in batches of at most 50. When `--sprint-id` is present, provided name/goal/date fields update that Sprint before membership changes. If the user has not confirmed, leave `dry_run: true` and make no external write.

### 4. Route each Story into delivery

After the Sprint membership is decided:

- Route each new or changed Story to `team-spec`.
- Once its spec is approved, route to `team-dev` in `plan` mode.
- Continue through `team-review`, `team-qa`, and `team-release` using the existing gates.
- A Story may be specified before Sprint membership is committed when the Sprint is still being shaped; missing Sprint membership alone does not block Story work unless the project has explicitly adopted a sprint-required policy.

## Output contract

Return these items in Traditional Chinese by default:

1. **規劃範圍**：Project Key, Board id, Epic key, Sprint id/name/state.
2. **候選項目**：每個 Issue 的 user-facing description/summary, Jira key, type, status, included/excluded, reason.
3. **執行狀態**：`dry_run` or `applied`; never imply a write occurred when it did not.
4. **下一步**：the exact Story skill and mode, or the missing planning decision.
5. **完成條件**：for preview, a reviewable payload; for apply, the Jira Sprint id and all moved issue keys.

## Offline mode

When `--offline` is explicit or Jira credentials are unavailable, output the board id, Sprint payload, candidate issue list, and intended move batches. Do not claim that a Sprint or membership was written.

## Done when

- Preview: the user can review the selected and excluded issues, Sprint payload, and `dry_run: true`.
- Apply: Jira returns a Sprint id, all selected issues are moved successfully, and the response lists the exact moved keys.
- Status: the response identifies the board and Sprint state without changing Jira or Story workflow state.
