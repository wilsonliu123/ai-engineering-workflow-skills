# ai-engineering-workflow skills（v1.6.1 — CI/CD 交付管線對齊 + Epic/Sprint 規劃 + 四站式 Jira 核心工作流）

> **語言**: [English](./README.md) | **繁體中文** · [Changelog (EN)](./CHANGELOG.md) | [更新日誌 (繁體中文)](./CHANGELOG.zh-TW.md)

八個以 Jira 為核心的工程工作流 Skill，搭配一個動態多代理協作系統 Skill，專為小型與成長中團隊設計：支援一人身兼多職、多人共用同一角色（`1-to-N` 角色成員池），並以 **Jira** 作為唯一的永久規劃與單一功能生命週期資產庫。

## 規劃層 + 四站式工作流 Skills（`skills/team-*`）

| 站點 | Skill | 模式 | 用途 |
|---|---|---|---|
| **初始化（Init）** | `skills/team-init/` | `init` / `auth` | 以互動詢問優先的方式建立 `.ai-workflow/team-config.json`（含 `commit_policy`、`commit_message_pattern`、`qa_handoff_mode`、`status_mapping.dev_done`、`epic_link_field` 與選填 `delivery_pipeline`）、`.ai-workflow/bin/jira_sync.py` 與 `docs/shared/engineering-standards.md`。以純標準函式庫自動掃描 `.gitlab-ci.yml` 與 `.github/workflows/*.yml`，回報 `detected_delivery_pipeline`、`detected_ci_checks` 與 `suggested_qa_handoff_mode`。支援**漸進式角色認領**（`claimed_roles` / `unclaimed_roles`）、**`1-to-N` 多人角色成員池**（`specialty` 專長標籤），以及非破壞性的協作者 **`auth`** 憑證綁定（環境變數優先，或儲存於程式碼庫外部的 `~/.config/ai-workflow/credentials/` 並設定 `0600` 權限；舊版已加入 `.gitignore` 的 `.ai-workflow/credentials.json` 維持完全向下相容）。 |
| **規劃層（Planning）** | `skills/team-plan/` | `epic` / `decompose` / `sprint` / `commit` / `status` | 規劃 Epic 上下文、平台中立的 Epic 拆解為 Story 流程（`epic-decompose`，強制先執行 `dry_run: true` 預覽再依拓樸波次建立），以及 Future/Active Sprint 成員管理（先預覽再套用的安全防護）。讓 Epic 維持跨 Sprint 容器角色，並將選定的 Stories/Tasks/Bugs 導流至既有交付工作流，不將 `run-workflow` 或多 Story 子代理耦合進核心 `team-*`。 |
| **路由器（Router）** | `skills/team-next-step/` | — | 唯讀單一入口（`$team-next-step`），檢查 `team-config.json`、`.ai-workflow/active/<JIRA-KEY>.json`、Epic 進度（`status --epic <EPIC-KEY>`）與即時 Jira 狀態（透過 JQL 自動發現進行中的 Stories 與獨立 Bugs、同步 Jira Web UI 手動修改、在設定時顯示 `integration_branch`/`base_branch`，並回報 `awaiting_qa_deploy` + `qa_deploy_hint`、`triage_closed` 與非阻斷的 `release_checklist_reminders`），以推薦精確的下一站點、模式與負責人。 |
| **第一站（Station 1）** | `skills/team-spec/` | `one-pass` / `draft` / `backfill` | 依規模自適應的整合式規格與設計：小型明確需求一次完成（**「抓大放小」**，最多 3 個關鍵問題）；複雜或模糊需求透過多輪 `draft` 深入對齊與六維度 AC 完整性檢核表迭代；既有的 Story 支援 **`--backfill`** / `--preserve-owner`，可選填 `Risks & Open Questions`（`--risks`、`--open-questions-json`）並遵循五類證據分層紀律，且不更動負責人、Assignee、狀態或生命週期欄位。支援增量 AC upsert、Jira 描述漂移防護、嚴格 `has_data` 範圍界定（僅限 schema/migration/儲存格式/資料模型變更），以及條件式 UI（`has_ui`）/ API（`has_api`）規格，透過行內 Markdown ADF 同步至 Jira。 |
| **第二站（Station 2）** | `skills/team-dev/` | `plan` / `implement` | 將核准規格拆解為可驗證切片（`TASK-001..N` → Jira `Sub-task`，具備結構化的 Implementation Steps / Target Files / Verification 與依專長自動指派開發者），或執行 **Dev Fast-Track**（嚴格限定 `<= 3` 條 AC 且風險為 `low`/`medium`）。有設定 `delivery_pipeline.integration_branch` 時自該分支切出並進行 diff 比對（否則回退至 `main`/`master`），依嚴格優先順序（`BUG-*` > `TASK-###` > `REV-###`）實作程式碼與測試，整合領域專長 Skill（Bring-Your-Own-Skill 掛鉤），強制執行與審查對齊的五項離開關卡（含對齊 CI 的本機 lint/test 檢查，以及 `docs/shared/engineering-standards.md` §4 / `AGENTS.md` 的 Commit 格式與雙 Jira Key `<STORY-KEY>` + `<SUBTASK-KEY>`），於人工確認 Commit 關卡暫停（`commit_policy`：預設 `"confirm_before_commit"`，可選 `"auto_on_test_pass"`），並透過 `complete-task` 勾選完成（修補 Bug 時支援 `--root-cause` / `--solution` 回填，以及非阻斷的 `commit_message_warnings` / `bug_fix_warnings`）。 |
| **第三站 a（Station 3a）** | `skills/team-review/` | — | 結合開發交接與三軸程式碼審查，依 AC、設計、`docs/shared/engineering-standards.md`（回退至 `AGENTS.md`）以及相對於 `integration_branch`（若有設定）的 Commit / 雙 Jira Key 合規性進行檢核。若有可用成員則自動路由至 `roles.reviewer.members` 中的同儕審查者（單人團隊則透明記錄 AI 自我審查），支援 `"immediate"` 與 `"gated_by_deploy"`（`dev_done` -> `promote-qa [--deploy-ref <ref>] [--environment <env>]` / CI/CD `in_qa`）兩種 QA 交接模式，並嚴格執行 HITL 確認與跨人員職責分離 Clean Stop（絕不自動打 Tag 或自動合併）。 |
| **第三站 b（Station 3b）** | `skills/team-qa/` | `plan` / `verify` / `report` / `triage` | 平行**三層式 QA**（`automated` / `external` / `manual`，具防假通過護欄與 `qa_identity_guardrail`），支援記憶式 **`jira-comment`** 或 **`qase`**（Qase TestOps API v1）後端。**`plan`** 即使在 `awaiting_qa_deploy` 期間仍可平行執行；**`verify`**（在 `gated_by_deploy` 下受部署閘道管制）可匯入 JUnit XML/JSON/手動測試結果，追蹤選填 `--deploy-ref`（`verified_deploy_ref` 會自動繼承對齊的 `qa_deploy_gate.deploy_ref`），並建立關聯 Jira `Bug` 單與媒體附件（`--attach`）同步至 Jira 與 Qase Runs/Defects；**`report`** 處理未列於腳本的探索式 Story 缺陷（`TC-EXP-###`）與獨立線上／客服 `Bug` 單收件（`--standalone` / `--adopt`）；**`triage`**（`triage-bug`）處理經人工授權的非程式碼修復類 Bug 結案（`Not a Bug`、`Won't Fix`、`Duplicate`、`Cannot Reproduce`）或 `Fixed` 重新開啟，並附帶可稽核的 `waived` / `not-applicable` TC 處置。 |
| **第四站（Station 4）** | `skills/team-release/` | — | 校對即時 Jira 狀態，針對候選 commit 驗證全部 5 項發布關卡（含經一致性校驗且可稽核的 `waived` / `not-applicable` TC 豁免與零個未關閉 Bug），在多階段 `delivery_pipeline` 程式碼庫中顯示非阻斷的 `release_checklist_reminders`（UAT / 獨立 `production_branch`），於 `docs/manuals/<manual-name>/<manual-name>.html` 執行**依規模自適應的動態 HTML 手冊同步**，張貼發布摘要（適用時包含 **Bug Triage & Risk Acceptance Disclosure**），將 `resolved_depends_on_keys` 傳遞給同 Epic 下的其他 Story，將 Jira Story（或獨立 Bug）轉換至 `Done`，並刪除 `.ai-workflow/active/<JIRA-KEY>.json`。 |

## 系統編排 Skill（`skills/system/`）

| Skill | 用途 |
|---|---|
| `skills/system/run-workflow/` | 使用 `run_workflow` 工具撰寫動態 Python 多代理編排腳本的指引與模式（`agent`、`parallel`、`pipeline`、`phase`、`trajectory` 與 `call_tool`），適用於扇出／彙整任務與分階段代理管線。 |

## 核心工作流能力

- **零 CLI 單一入口體驗、行內 ADF 與預設繁體中文（`zh-TW`）產出：** 使用者可隨時以自然語言互動或呼叫 `$team-next-step`；AI 會在背景執行 `.ai-workflow/bin/jira_sync.py`，將行內 Markdown（粗體、斜體、行內程式碼、連結、刪除線、巢狀格式）轉換為合法的 Jira ADF，並預設以**繁體中文（Traditional Chinese）**撰寫面向人類的規格、審查報告、QA 驗證表與發布說明。
- **平台中立的 Epic/Sprint 規劃層：** `$team-plan` 可預覽 Epic 上下文、透過 `epic-decompose` 將既有 Epic 拆解為按波次排序的子 Stories（`dry_run: true` 預覽 -> 明確確認 -> `--apply`，並支援 `parent` -> `epic_link_field` 回退）、以 `status --epic <EPIC-KEY>` 追蹤跨 Story 進度，並管理 Future/Active Sprint 成員。Sprint 成員關係不會取代 Story 交付關卡，且 Phase 1 在所有客戶端皆嚴格保持平台中立。
- **跨機器狀態還原與雙向 Jira Web UI 校對：** 透過隱藏的 Jira Issue Property（`ai_workflow_state`）同步結構化狀態，並在各站點動作前自動校對 Jira Web UI 上的手動變更（新增／重開 Sub-tasks、關聯 Bugs、Assignee 更新、CI/CD `in_qa` 狀態轉換與 Story 描述修改）。
- **`1-to-N` 角色成員池、嚴格 HITL 關卡與 SoD Clean Stop：** 同時支援一人身兼多職（`same_person: true` 僅透過 `auto-consolidated` 抑制自我 `@mention` 雜訊，**絕不**自動串接下一站 — 必定暫停並要求明確確認，強制 `can_auto_chain_next_station: false`）與多人協作團隊（自動解析 Story/Sub-task/Bug Assignee、專長配對、同儕審查者挑選、`qa_identity_guardrail`、`Stop & Yield on Fix` 以及跨人員職責分離 Clean Stop）。
- **可設定的 Commit 與部署治理（`team-config.json`）：**
  - `commit_policy`：`"confirm_before_commit"`（預設 — 本機測試通過後先暫停，待人工確認再執行 `git commit` 與 `complete-task`）或 `"auto_on_test_pass"`（顯式啟用）；可選填 `commit_message_pattern` 正規表達式提供非阻斷的格式提醒。
  - `qa_handoff_mode`：`"immediate"`（預設）或 `"gated_by_deploy"`，搭配 `status_mapping.dev_done` 與 `promote-qa [--deploy-ref <tag-or-build>] [--environment <env>]`，將開發完成與多環境 QA 部署解耦。
  - `delivery_pipeline`：選填的多階段 CI/CD 對齊設定（`integration_branch`、`production_branch`、含 `trigger`/`ref`/`prefix`/`deployment_tier` 的 `environments`、`jira_deployment_sync`；於 `team-init` 期間自動從 `.gitlab-ci.yml` / `.github/workflows/*.yml` 偵測，並可透過 `init --integration-branch`、`--production-branch`、`--qa-tag-prefix`、`--uat-tag-prefix`、`--jira-deployment-sync` 設定）。驅動 `_resolve_base_branch`、`qa_deploy_hint`、`record-qa --mode verify --deploy-ref`（`verified_deploy_ref`）與非阻斷的 `release_checklist_reminders`，同時嚴格禁止 AI 自動打 Tag、自動合併或自動推送。未設定 `delivery_pipeline` 的專案維持 100% 向下相容。
  - `epic_link_field`：選填的自訂 Jira Epic Link 欄位 ID，作為 `epic-decompose` 在 `fields.parent` 被拒絕時的回退選項。
- **輕量級獨立線上 Bug 軌道與 Bug 分類（Triage）：** 透過 `$team-qa`（`report --standalone`）登錄或納管的獨立客服／線上 `Bug` 單，可略過 `team-spec` 與 Sub-task 儀式，直接走 `team-dev -> team-review -> team-qa (verify) -> team-release`（或在無須修改程式碼時，透過人工授權的 `$team-qa` `triage` / `triage-bug` 乾淨結案）。

## 第一性原理資產邊界

- **單一功能生命週期資產（Jira + 已加入 `.gitignore` 的本機暫存檔）：**
  - User Stories、ACs、Sub-tasks、程式碼審查摘要、QA 測試計畫與驗證表、Bug 單與媒體證據永久保存於 **Jira**（以及選用的 **Qase TestOps**）。
  - `.ai-workflow/active/<JIRA-KEY>.json`（已加入 `.gitignore`）保存進行中的機器可讀狀態，並於發布完成時自動刪除（`jira_sync.py close-story`）。個人或各角色的 Jira 與 Qase API Token 透過執行期環境變數（`JIRA_BASE_URL`、`JIRA_EMAIL`、`JIRA_API_TOKEN`、`QASE_API_TOKEN`）提供，或儲存於 Git 程式碼庫外部的 `$XDG_CONFIG_HOME/ai-workflow/credentials/`（預設 `~/.config/ai-workflow/credentials/`，目錄 `0700`、檔案 `0600`）；舊版已加入 `.gitignore` 的 `.ai-workflow/credentials.json` 維持 100% 向下相容。任何單一功能的流程 Markdown 檔案（`docs/requirements/`、`docs/design/`、`docs/qa/`、`docs/release/`、`analysis.md` 等）絕不提交進 Git。
- **系統層級動態常駐資產（提交至 Git）：**
  - `.ai-workflow/team-config.json` 與 `.ai-workflow/bin/jira_sync.py`（團隊共用設定／狀態對應與純標準函式庫 CLI 工具）。
  - `AGENTS.md` 與 `docs/shared/engineering-standards.md`（標準工程、資安、測試與 Git Commit/PR 規範）。
  - 選用的 `docs/shared/glossary.md`、`docs/shared/adr/ADR-<NNN>-<slug>.md`，以及位於 `docs/manuals/<manual-name>/<manual-name>.html`（`./screenshots/`、`./diagrams/`）的可攜式深色側邊欄產品／API 手冊。

## 資安與 SAST / Secret Scanner 說明

- **純標準函式庫 SAST 零告警強化（`v1.6.1`）：**
  - `jira_sync.py` 採用純標準函式庫的 fail-closed JUnit XML 掃描器（取代 `xml.etree.ElementTree`，拒絕 `<!DOCTYPE`/`<!ENTITY`、NUL 位元組與重複屬性，徹底消除 XXE/DTD 風險），並以共用的純標準函式庫 `_VerifiedTLSConnection` + `_http_request` 取代 `urllib.request.urlopen` 與 `http.client.HTTPSConnection`（強制 `CERT_REQUIRED`、`check_hostname=True`、`TLSv1.2+`、ALPN `http/1.1`，以及跨來源／降級重導向時自動移除敏感認證標頭），達成 **0 筆 Semgrep 告警**與 **0 處 `# nosemgrep`** 豁免註解。
- **程式碼庫外部憑證儲存與純環境變數驗證：**
  - `jira_sync.py init` 與 `jira_sync.py auth` 支援 `--credential-store {user,repo,env}`，預設（`user`）會將持久化 Token 儲存於 **Git 程式碼庫外部**的 `$XDG_CONFIG_HOME/ai-workflow/credentials/<repo-id>.json`（預設 `~/.config/ai-workflow/credentials/<repo-id>.json`，可透過 `AI_WORKFLOW_CREDENTIALS_DIR` 覆寫），強制使用 `0700` 目錄權限、`0600` 原子寫入並拒絕符號連結（symlink）。同一個程式碼庫的所有 Git worktree 共用同一份標準憑證檔。
  - 若要採用零憑證檔流程（CI 或本機密鑰管理工具），只需設定 `JIRA_BASE_URL`（支援 `JIRA_URL` 回退）、`JIRA_EMAIL`、`JIRA_API_TOKEN`（以及選填的 `JIRA_EMAIL_<ROLE>`、`JIRA_API_TOKEN_<ROLE>`、`QASE_API_TOKEN`），並執行 `jira_sync.py auth --credential-store env`（或省略 `--jira-token`），磁碟上不會寫入任何憑證檔。
  - 讀取優先順序嚴格為：**執行期環境變數 > 使用者設定（`~/.config/ai-workflow/credentials/`） > 舊版程式碼庫內 `.ai-workflow/credentials.json` > 舊版 `.ai-workflow/.env`**。
- **舊版 `.ai-workflow/credentials.json` 相容性與遷移：**
  - 既有程式碼庫中的 `.ai-workflow/credentials.json` 或 `.ai-workflow/.env` 維持 100% 向下相容（且仍保留 `--credential-store repo` 作為顯式選擇）。遷移至環境變數或預設的程式碼庫外部使用者憑證目錄後，即可安全刪除程式碼庫內的舊 `.ai-workflow/credentials.json`，消除 Gitleaks／密鑰掃描器的誤報。
- **選用的團隊自管 `.semgrepignore` 指引：**
  - `team-init` **絕不會**在下游程式碼庫中建立或修改 `.semgrepignore` 或 `.gitleaksignore`。
  - `.ai-workflow/bin/jira_sync.py` 刻意以單一純標準函式庫 Python 檔案形式發佈，讓每個打包後的 Skill 都能自給自足、零第三方相依（模組化重構已納入未來主版本追蹤）。若你的程式碼庫執行 Semgrep 或其他 SAST 掃描器，且希望將此內嵌工作流輔助腳本排除於應用程式碼掃描額度之外，團隊可自行視需要將 `.ai-workflow/bin/jira_sync.py` 加入由專案自行管理的 `.semgrepignore`。

## 文件、訓練與上手教材（`docs/`）

- **程式碼庫文件（EN / 繁體中文）：** [README.md](./README.md) · [README.zh-TW.md](./README.zh-TW.md) · [CHANGELOG.md](./CHANGELOG.md) · [CHANGELOG.zh-TW.md](./CHANGELOG.zh-TW.md)。
- **情境式訓練手冊（`v1.6.1`）：** [docs/training-manual.md](./docs/training-manual.md)（標準 Markdown 原始檔）以及同步至 `v1.6.1` 的匯出版本 [docs/training-manual.html](./docs/training-manual.html)、[docs/training-manual.pdf](./docs/training-manual.pdf) 與 [docs/training-manual.docx](./docs/training-manual.docx)（附帶 `docs/screenshots/` 中的終端機 + Jira 雙欄標註圖解）。
- **訓練簡報（`v1.6.1`）：** 共 18 張投影片的情境式簡報 [docs/training-manual.pptx](./docs/training-manual.pptx)（`docs/ppt-assets/`），已同步至 `v1.6.1`（涵蓋 `team-plan` Epic/Sprint 規劃、嚴格 HITL / SoD Clean Stop、`commit_policy`、`qa_handoff_mode`、`triage-bug`、CI/CD `delivery_pipeline` 對齊、SAST 零告警強化與程式碼庫外部預設憑證）。

## 共用檔案與驗證

- `scripts/jira_sync.py`（無第三方相依的 Python 3 標準函式庫 CLI，包含 **16 個子命令**：`init`、`auth`、`sprint-plan`、`sprint-status`、`epic-decompose`、`status`、`pull`、`sync-story`、`create-subtasks`、`complete-task`、`handoff`、`promote-qa`、`record-qa`、`create-bug`、`triage-bug`、`close-story`，支援即時 Jira REST API v3、Jira Agile Sprint 端點、Qase TestOps API v1 與離線模式）與 `references/common-workflow-governance.md` 刻意在全部 8 個 `team-*` Skills 中各保留一份相同副本，讓每個打包的 Skill `.zip` 皆可獨立運作。
- 執行 `./tools/check_shared.sh` 驗證全部 8 份副本保持位元組完全一致。
- 執行 `python3 tools/test_workflow.py` 執行 **41 項測試**的端對端生命週期與模擬 Jira/Qase REST API 測試套件。
- 路由與 QA 評測情境位於 `skills/team-next-step/evals/evals.json`、`skills/team-plan/evals/evals.json` 與 `skills/team-qa/evals/evals.json`。
