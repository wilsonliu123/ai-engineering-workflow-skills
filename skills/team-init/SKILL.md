---
name: team-init
description: Use when onboarding a repository to the team's AI engineering workflow, when .ai-workflow/team-config.json or .ai-workflow/credentials.json is missing or needs updating, when configuring role-to-person mappings, Jira URL, account email, or API token, or when extracting and deduplicating docs/shared/engineering-standards.md from AGENTS.md.
---

# Team Init (Project, Jira Credentials & Role Initialization)

## Overview

Initialize a repository for the 4-station Jira-centered small-team workflow through an **interactive inquiry-first flow**. Before writing any configuration, confirm the project's **Jira Base URL & Project Key**, **Jira Account Email & API Token** (stored in gitignored `.ai-workflow/credentials.json` with `0600` permissions), and **Role-to-Person Mappings** (`pm`, `tech_lead`, `developer`, `reviewer`, `qa`, `release_manager`), then extract and deduplicate `docs/shared/engineering-standards.md` from `AGENTS.md`.

Read `references/common-workflow-governance.md` for the authoritative asset boundary and multi-role handoff rules.

## When to Use

- Onboarding a new or existing repository to the `ai-engineering-workflow` skill suite.
- `team-next-step` or any station skill reports that `.ai-workflow/team-config.json` is missing.
- Configuring or updating the Jira project URL (`jira_base_url`), Jira account email (`JIRA_EMAIL`), or API token (`JIRA_API_TOKEN`).
- Adding, changing, or consolidating team members and their accounts across roles (`pm`, `tech_lead`, `developer`, `reviewer`, `qa`, `release_manager`).
- Extracting engineering rules from `AGENTS.md` into `docs/shared/engineering-standards.md` so the two files never diverge.

**When NOT to use:**
- Routine per-feature work when `.ai-workflow/team-config.json`, `.ai-workflow/credentials.json`, and `docs/shared/engineering-standards.md` are already configured (use `team-spec` or `team-next-step`).

## Quick Reference

| Output / Target | Purpose | Committed to Git? |
|---|---|---|
| `.ai-workflow/team-config.json` | Project key, Jira base URL, status/issue-type mapping, and role-to-person mapping (`name`, `email`, `jira_account_id`) | **Yes** |
| `.ai-workflow/credentials.json` | Local Jira authentication credentials (`jira_base_url`, `jira_email`, `jira_api_token`, `chmod 0600`) auto-loaded by `jira_sync.py` | **No (Gitignored)** |
| `.ai-workflow/bin/jira_sync.py` | Project-local unified CLI wrapper (works even when skills are installed globally) | **Yes** |
| `.gitignore` | Ignores `.ai-workflow/active/`, `.ai-workflow/credentials.json`, and `.ai-workflow/.env` | **Yes** |
| `docs/shared/engineering-standards.md` | Canonical repository-wide engineering, testing, and security standards | **Yes** |
| `AGENTS.md` | Trimmed of duplicated standards; links to `docs/shared/engineering-standards.md` | **Yes** |

## Zero-CLI Burden & Single-Command Principle

- **Humans Never Need to Type CLI Flags:** Always ask the user in natural language (or via the interactive question tool), then assemble and execute `jira_sync.py` commands automatically in the background based on their confirmed answers.
- **Unified Project CLI Path:** Once `init` runs, `.ai-workflow/bin/jira_sync.py` is installed directly in the repository so subsequent stations can run `python3 .ai-workflow/bin/jira_sync.py ...` (or fall back to `python3 <skill-dir>/scripts/jira_sync.py ...`).
- **Only One Command to Remember (`$team-next-step`):** Remind the user that after initialization, they only ever need `$team-next-step` (or natural language `"下一步"`) to navigate the entire 4-station lifecycle.

## Workflow

### Step 1: Inspect existing repository state (`init --inspect`)
1. Run pre-init inspection to check whether `.ai-workflow/team-config.json`, `.ai-workflow/credentials.json`, `AGENTS.md`, `docs/shared/engineering-standards.md`, and `.gitignore` exist:
   ```bash
   python3 <skill-dir>/scripts/jira_sync.py --repo-root . init --inspect
   ```
2. Auto-detect existing stack conventions, local unit/integration test commands (`pytest`, `vitest`, `go test`), **external test tools / E2E / CI pipelines** (Playwright, Cypress, Postman/Newman, JMeter, GitHub Actions / GitLab CI), and linter rules from the repository (`package.json`, `pyproject.toml`, `go.mod`, `Makefile`, `AGENTS.md`). Do not interrogate the user over routine tech-stack details that can be read from the repo.

### Step 2: Mandatory Interactive Inquiry Gate (STOP & Confirm with User)

**HARD GATE — DO NOT SKIP:**
If any of the following items have not been explicitly provided or confirmed by the user in the current conversation, you **MUST STOP and ask the user** before executing `jira_sync.py init` (use the interactive question tool if available, or present a concise numbered inquiry in Traditional Chinese):

1. **專案 Jira URL 與 Project Key (Project URL & Project Key)**
   - 請問專案的 Jira Base URL（例如 `https://your-org.atlassian.net`）與 Project Key（例如 `PROJ` / `TEAM`）為何？
   - *(若團隊確定不使用遠端 Jira、僅走純本地離線模式，請使用者明確回覆「離線模式」)*。
2. **預設 Jira 認證帳號與 API Token (Default Jira Account Email & API Token)**
   - 請提供預設用於呼叫 Jira REST API v3 的登入帳號 Email（`JIRA_EMAIL`）與 `JIRA_API_TOKEN`（當某角色未設定專屬 API Token 時，系統會以此預設帳號作為 fallback）。
3. **六大角色定義、對應人員與各角色專屬 API Token (Role-to-Person, Email & Per-Role API Token)**
   - 請逐一確認以下 6 個角色分別由哪位成員擔任（包含 **姓名 `name`**、**信箱 `email`**、**該成員的 Jira `api_token`**，以及選填的 **`jira_account_id`**）：
     - `pm`（產品經理 / 規格定義 — Station 1 `team-spec`）
     - `tech_lead`（技術負責人 / 架構與規範）
     - `developer`（開發工程師 / 任務實作 — Station 2 `team-dev`）
     - `reviewer`（程式碼審查員 — Station 3a `team-review`）
     - `qa`（品質工程師 / 測試規劃與驗證 — Station 3b `team-qa`）
     - `release_manager`（發佈管理員 / 上線與結案 — Station 4 `team-release`）
   - **為什麼需要確認各角色的 API Token？** Jira REST API v3 的留言（Comment）與狀態變更發送人頭像，永遠取決於當次 HTTP 請求所使用的 API Token 帳號。為不同人員擔任的角色配置各自的 API Token 後，`team-spec`（`pm`）、`team-dev`（`developer`）、`team-review`（`reviewer`）、`team-qa`（`qa`）、`team-release`（`release_manager`）在寫入 Jira 評論與交接時，就會**直接以該角色成員本人的 Jira 帳號身分發送**，且初始化時會自動透過各角色的 Token 呼叫 `/rest/api/3/myself` 精準綁定其 `jira_account_id`。
   - **安全儲存承諾**：所有 API Token（包含預設 Token 與各角色專屬 `role_credentials`）皆**只會寫入受 `.gitignore` 保護的 `.ai-workflow/credentials.json`（權限 `0600`）**；即便在 `--roles-json` 中傳入 `api_token`，`jira_sync.py init` 也會自動將其剝離轉存至 `credentials.json`，**絕不會寫入 Git 追蹤的 `.ai-workflow/team-config.json`**。
   - **一人兼任多角色自動共用**：若同一位成員（相同 Email）兼任多個角色，只需提供一次該成員的 API Token，系統會自動在同 Email 的角色間共用 Token，並在相鄰站點為同一人時標記 `auto-consolidated`（同時仍會將 Jira 單據 `Assignee` 指派給該成員，避免單據顯示為 `Unassigned`）。
4. **Jira 狀態與單據類型映射（選填確認）**
   - 列出預設狀態映射（`To Do`、`In Progress`、`In Review`、`In QA`、`Ready for Release`、`Done`）與單據類型（`Story`、`Sub-task`、`Bug`），詢問是否直接沿用預設值或需自訂。

**WAIT for the user's response before proceeding to Step 3.**

### Step 3: Run deterministic initialization (`jira_sync.py init`)
Once the user has confirmed the values in Step 2, execute `scripts/jira_sync.py init` from the repository root (or run with `--interactive` if executing directly in an interactive terminal):

```bash
python3 <skill-dir>/scripts/jira_sync.py --repo-root . init \
  --project-key "<PROJECT_KEY>" \
  --jira-url "<JIRA_BASE_URL>" \
  --jira-email "<JIRA_EMAIL>" \
  --jira-token "<JIRA_API_TOKEN>" \
  --roles-json '<JSON_MAPPING>' \
  --role-tokens-json '<ROLE_TOKENS_JSON>'
```

*(Note: Per-role tokens can be passed either via `--role-tokens-json '{"pm": "...", "qa": "..."}'` or inline as `"api_token"` inside each role object in `--roles-json`; `jira_sync.py init` automatically strips any `"api_token"` from `.ai-workflow/team-config.json` and stores them in `.ai-workflow/credentials.json` under `role_credentials`.)*

What `jira_sync.py init` enforces automatically:
- Creates or updates `.ai-workflow/team-config.json` (template: `references/team-config-template.json`) with confirmed `project_key`, `jira_base_url`, `roles` (strictly `name`, `email`, `jira_account_id` — never tokens), `status_mapping`, and `issue_type_mapping`.
- Saves default `jira_email`/`jira_api_token` and per-role `role_credentials` into `.ai-workflow/credentials.json` (`chmod 0600`) so each station command (`sync-story`, `create-subtasks`, `complete-task`, `handoff`, `record-qa`, `create-bug`, `close-story`) automatically authenticates with that station role's Jira account.
- When live credentials are provided, verifies Jira connectivity (`GET /rest/api/3/myself`) for both default and per-role tokens (`role_verifications`) and automatically populates missing `jira_account_id` fields from the role token's `/myself` response or `GET /rest/api/3/user/search`.
- Installs `.ai-workflow/bin/jira_sync.py` (`chmod 0755`) and returns `"cli_path": ".ai-workflow/bin/jira_sync.py"`.
- Appends `.ai-workflow/active/`, `.ai-workflow/credentials.json`, and `.ai-workflow/.env` to `.gitignore` if not already present.
- Extracts engineering/coding/testing/security sections from `AGENTS.md` into `docs/shared/engineering-standards.md` (or creates a starter from `references/engineering-standards-template.md` if `AGENTS.md` has none) and **replaces the duplicated sections in `AGENTS.md` with a reference link** to `docs/shared/engineering-standards.md`.

### Step 4: Verify and summarize
1. Verify that `docs/shared/engineering-standards.md` reflects the repository's actual stack, test commands, and security boundaries. Refine the extracted markdown if needed.
2. Present a concise initialization summary in Traditional Chinese (繁體中文):
   - **專案連線與憑證狀態**：確認 `project_key`、`jira_base_url`、`jira_mode`（`live` 或 `offline`）、連線驗證結果（`jira_verification`）以及憑證已受 `.gitignore` 保護（`.ai-workflow/credentials.json`）。
   - **角色定義與交接整併表 (`role_consolidation`)**：列出各角色負責人、帳號與哪些相鄰站點為同一人自動整併（`auto-consolidated`）、哪些跨人交接會觸發 Jira `@mention` + 指派 + 狀態流轉。
   - **工程規範收斂結果**：`docs/shared/engineering-standards.md` 與 `AGENTS.md` 去重狀態。
   - **下一步指引**：提示使用者後續只需輸入 `$team-next-step`（或 `$team-spec`）即可啟動第一個 Jira Story。

## Red Flags — STOP

- **Running `jira_sync.py init` with dummy defaults (`PROJ`, empty URL, `PM Owner`, `Developer`) without asking the user first:** STOP immediately. Always ask the user to confirm Project URL, Project Key, Jira Account/Token, and Role definitions before running `init`.
- **Hardcoding secrets in tracked files:** Never write `JIRA_API_TOKEN` or passwords into `.ai-workflow/team-config.json`, `AGENTS.md`, or any Git-tracked file. Always pass `--jira-token` so it is stored exclusively in gitignored `.ai-workflow/credentials.json` (`0600`) or environment variables.
- **Duplicating rules across `AGENTS.md` and `engineering-standards.md`:** Always keep detailed engineering standards in `docs/shared/engineering-standards.md` and leave only a pointer in `AGENTS.md`.
- **Creating per-feature `docs/` folders:** Never create `docs/requirements/`, `docs/design/`, or `docs/qa/` directories; per-feature artifacts belong in Jira and `.ai-workflow/active/<JIRA-KEY>.json`.
