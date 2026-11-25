# 更新日誌（Changelog）

> **語言**: [English](./CHANGELOG.md) | **繁體中文**

## v1.6.1 — 2026-11-25 — 資安強化、SAST 零告警與程式碼庫外部憑證儲存

- **Semgrep SAST 零告警修復（Issue #21 / PR #22、Issue #23 / PR #24）：**
  - 將 `parse_junit_xml_results` 中的 `xml.etree.ElementTree` 替換為純標準函式庫的 fail-closed XML 掃描器，拒絕 `<!DOCTYPE`/`<!ENTITY`、NUL 位元組與重複屬性，同時安全處理註解與 `<![CDATA[...]]>` 區塊（解決 `use-defused-xml-parse`）。
  - 將全部 4 處 `urllib.request.urlopen` 呼叫點與 `http.client.HTTPSConnection` 替換為共用的純標準函式庫 `_http_request` + `_VerifiedTLSConnection(http.client.HTTPConnection)`，強制執行 `http`/`https` scheme 驗證、`\A...\Z` CR/LF 方法／標頭防護、拒絕 userinfo／反斜線／非 ASCII 字元、`ssl.create_default_context()`（`CERT_REQUIRED`、`check_hostname=True`、`TLSv1.2+`，拒絕包含 `ssl.TLSVersion.MINIMUM_SUPPORTED` 在內的降級或弱化 context、ALPN `http/1.1`）、無洩漏 socket 關閉、IPv6 預設埠 `Host:` 標頭一致性，以及跨來源／降級重導向時自動移除敏感標頭（解決 `dynamic-urllib-use-detected` 與 `httpsconnection-detected` CWE-295，達成 **0 筆 Semgrep 告警**與 **0 處 `# nosemgrep`**）。
- **程式碼庫外部預設憑證儲存與純環境變數驗證（Issue #23 / PR #25）：**
  - 將預設本機憑證儲存位置移至 Git 程式碼庫外部的 `$XDG_CONFIG_HOME/ai-workflow/credentials/<repo-id>.json`（預設 `~/.config/ai-workflow/credentials/`，可透過 `AI_WORKFLOW_CREDENTIALS_DIR` 覆寫），採用 `0700` 目錄權限、原子式 `0600` 寫入、跨 worktree 共用標準 git common-dir、無點號 slug 淨化與讀寫符號連結（symlink）拒絕機制，消除 Gitleaks／非 git 目錄密鑰掃描器的誤報。
  - 在 `auth` 與 `init` 新增 `--credential-store {user,repo,env}`，並支援隱式純環境變數驗證（磁碟零寫入；支援 `JIRA_BASE_URL` 搭配 `JIRA_URL` 回退、`JIRA_EMAIL`、`JIRA_API_TOKEN`、`JIRA_EMAIL_<ROLE>`、`JIRA_API_TOKEN_<ROLE>` 與 `QASE_API_TOKEN`），嚴格遵循 `env > user_config > legacy repo` 優先順序，並對既有的 `.ai-workflow/credentials.json` / `.env` 檔案維持 100% 向下相容。
  - 在 `README.md` 與 `skills/team-init/SKILL.md` 新增選用的 SAST / Secret Scanner 指引，且絕不自動修改下游專案的 `.semgrepignore` / `.gitleaksignore`；維持單檔 `jira_sync.py` 發佈模式以確保獨立 Skill 的可攜性。
- **驗證與文件：**
  - 新增 `test_39_issue21_sast_remediation`、`test_40_issue23_verified_tls_connection` 與 `test_41_issue23_out_of_repo_credentials`（`tools/test_workflow.py` 共 **41/41** 項自動化測試通過），`./tools/check_shared.sh` 8/8 份 Skill 套件位元組完全一致。
  - 新增繁體中文文件：`README.zh-TW.md` 與 `CHANGELOG.zh-TW.md`。

## v1.6.0 — 2026-11-23 — CI/CD 交付管線對齊（Phase A）

- **選填 `delivery_pipeline` Schema 與純標準函式庫 CI 自動偵測（`team-init` / `jira_sync.py`）：**
  - 於 `team-config-template.json` 與 `DEFAULT_TEAM_CONFIG` 新增選填的 `delivery_pipeline` 區塊（`integration_branch`、`production_branch`、`environments`、`jira_deployment_sync`）與安全空預設值，並透過 `_sanitize_delivery_pipeline()` 淨化（嚴格 git ref 驗證、`"branch"`/`"tag"` 觸發白名單、嚴格布林解析使 `"false"`/`"0"`/`"no"` 絕不被誤判為真，且不外洩字面 `"None"`）。
  - 新增純標準函式庫 `_detect_ci_delivery_pipeline(repo_root)`，掃描 `.gitlab-ci.yml` 與 `.github/workflows/*.yml|*.yaml`（可容忍二進位檔、無效 UTF-8、無法讀取或格式錯誤的檔案），以偵測 `deployment_tier`（`development`/`testing`/`staging`/`production`）、分支／Tag 觸發規則（`qa-*`、`uat-*`、`release`/`develop`/`main`）、`jira_deployment_sync`、去重後的本機 lint/test 檢查提示（`detected_ci_checks`），以及 `suggested_qa_handoff_mode`（偵測到專屬 QA/testing 部署觸發時建議 `"gated_by_deploy"`）。
  - 在 `init --inspect` 與 `init` 回報 `detected_delivery_pipeline`、`detected_ci_checks` 與 `suggested_qa_handoff_mode`，並在 `init` 新增選填附加旗標（`--integration-branch`、`--production-branch`、`--qa-tag-prefix`、`--uat-tag-prefix`、`--jira-deployment-sync`）。
- **整合分支基準解析（`team-dev`、`team-review`、`status`）：**
  - 新增 `_resolve_base_branch(repo_root, config)`，有設定時優先採用 `delivery_pipeline.integration_branch`（本機不存在該 ref 時優雅回退至 `main`/`master`），並整合至 `_detect_git_changed_files` 與 `status`（僅於設定時顯示 `integration_branch` / `base_branch`）。
  - 更新 `team-dev`（在五項離開關卡納入對齊 CI 的本機 lint/test 檢查）與 `team-review`，有設定時自 `integration_branch` 切出分支並以之作為 diff 基準。
- **QA 部署 Ref 追蹤與自然語言 `qa_deploy_hint`（`promote-qa`、`record-qa`、`status`）：**
  - 在 `promote-qa` 新增 `--deploy-ref`（搭配 `--environment`），並在 `record-qa --mode verify` 新增 `--deploy-ref`，將 `deploy_ref`/`environment` 持久化於 `story["qa_deploy_gate"]`，將 `verified_deploy_ref` 持久化於 `story["qa"]`（省略時自動繼承對齊的 `qa_deploy_gate.deploy_ref`、在 Jira 留言以合法 ADF 呈現、在冪等執行 `promote-qa` 時乾淨保留／更新中繼資料而不重複寫入 Jira，並於新一輪 `candidate_commit` 審查週期清除過期的 `deploy_ref`/`environment`）。
  - 當處於 `awaiting_qa_deploy` 且已設定 `delivery_pipeline.environments.qa`（Tag 或分支觸發）時，於 `evaluate_story_next_step` 加入 `qa_deploy_hint`，同時嚴格維護 `can_auto_chain_next_station: false`、`requires_user_confirmation_for_next_station: true` 以及零 AI 自動打 Tag／自動合併原則。
- **輕量級非阻斷多階段發布提醒（`team-release`、`close-story`、`status`）：**
  - 只要 `delivery_pipeline` 設定了 `uat`/`staging` 環境，或設定了不同於 `integration_branch` 的 `production_branch`，便在 `evaluate_story_next_step`（第四站）與 `close-story` 輸出嚴格非阻斷的 `release_checklist_reminders`（未設定時為 `[]`；絕不阻斷 `close-story`，亦不引入額外 UAT 狀態機）。
- **驗證與向下相容性：** 對未設定 `delivery_pipeline` 的專案維持 100% 向下相容；`tools/test_workflow.py` 擴充至 38 項測試，8 份 Skill 套件位元組完全一致（`./tools/check_shared.sh` 8/8 OK）。
- **訓練簡報同步（`docs/training-manual.pptx`）：** 重建並同步情境式訓練簡報至 `v1.6.0`（18 張投影片 + 更新 `docs/ppt-assets/` 雙欄對話＋狀態圖解），涵蓋 `team-plan` / `epic-decompose`、嚴格 HITL / SoD Clean Stop、`commit_policy`、`qa_handoff_mode` + `promote-qa` / `record-qa --deploy-ref`、`triage-bug`、`delivery_pipeline` 對齊、16 個 CLI 子命令、8 個 Skills 與 38 項自動化測試。

## v1.5.0 — 2026-11-23 — 既有 Story 規格補齊與平台中立 Epic 拆解

- **既有 Story 補齊模式與 `has_data` 界定（PR #17，Issue #2 範圍）：**
  - 新增 `sync-story --backfill`、`--preserve-owner`、`--risks` 與 `--open-questions-json`，可在不變更 `current_owner_role`、Jira Assignee、Jira 狀態或既有生命週期欄位（`tasks`、`review`、`qa`、`bugs`、`qa_deploy_gate`、`triage_closed` 等）的前提下，為既有 Jira Story 補齊或豐富規格內容。
  - 強制執行補齊安全防護：拒絕 `--backfill --status approved`（含巢狀 `spec.spec_status: "approved"`）、`--backfill --create`、404 自動建立回退、重複 `Q-###` ID 與非布林 `blocking` 值；對未追蹤的既有文字遵守描述漂移防護，並於同步後驗證 `last_synced_description_hash`。
  - 僅在 `--risks` 或 `--open-questions-json` 非空時，以嚴格 ADF 於 Story 描述中渲染選填的 `Risks & Open Questions`（`風險與待確認問題`）區塊（簡單 Story 自動省略）。
  - 在 `team-spec/SKILL.md` 與 `spec-method.md` 釐清 `has_data` 定義（僅限 schema/migration/儲存格式/資料模型變更；單純呼叫既有持久化 API 屬於 `has_data: false`），正式規範五類證據分離與三態完成回報（`material_backfilled`、`spec_pending_confirmation`、`approved`），並堅守 Jira-only 邊界（Story 描述不放驗證情境表，Git 不留 `analysis.md` 或單一功能流程 Markdown）。
- **平台中立 Epic 拆解與進度彙整（PR #17，Issue #5 Phase 1）：**
  - 新增 `epic-decompose`（`--epic`、`--stories-json`、`--apply`、`--issue-type`），強制在明確確認前先執行 `dry_run: true` 預覽（零寫入），提供拓樸 `execution_waves` 計算、空白標題／重複 ID／相依循環／`story_points`／404 Epic 驗證、跨機器遠端快照重複偵測與部分失敗追蹤。
  - 加上 `--apply` 時依拓樸波次順序建立子 Stories，使下游 `depends_on_keys` 能解析為真實上游 Jira Key；優先透過 `fields.parent` 關聯 Epic，失敗時優雅回退至 `team-config.json` 設定的 `epic_link_field`（若 HTTP 400 則輸出未關聯警告），並建立 `.ai-workflow/active/<NEW-KEY>.json`（`spec_status: "draft"`）。
  - 新增 `status --epic <EPIC-KEY>` 進度彙整，支援 `parent` / `"Epic Link"` JQL 回退、快取遠端上游相依查詢、次要 worktree 合併、在地化 `status_mapping.todo` 處理、`close-story` 同層 `resolved_depends_on_keys` 傳遞，以及 HITL 停止關卡（`can_auto_chain_next_station: false`、`requires_user_confirmation: true`）。已於 `team-plan`（`decompose` 模式）與 `team-next-step` 完整記載，核心 `team-*` Skills 保持嚴格平台中立，不耦合 `run-workflow` 或 Phase 2 多 Story 子代理執行。
- **驗證：** 將 `tools/test_workflow.py` 擴充至 36 項端對端與模擬 Jira/Qase 測試，涵蓋 8 份位元組一致 Skill 套件中的 16 個 CLI 子命令。

## v1.4.0 — 2026-11-23 — Commit 與 Sub-task 治理、部署閘道式 QA 交接、Bug 分類與根因紀錄

- **Commit 關卡、專案 Commit 規範與結構化 Sub-tasks（PR #15，Issues #3、#7、#8）：**
  - 於 `team-config.json` 新增 `commit_policy`（`init --commit-policy`），預設為 `"confirm_before_commit"`（本機測試通過後先暫停等待人工確認，再執行 `git commit` 與 `complete-task`），並提供 `"auto_on_test_pass"` 顯式選項；另新增選填 `commit_message_pattern`（`init --commit-message-pattern`）於 `complete-task` 輸出非阻斷 `commit_message_warnings`。
  - 在 `team-dev`、`team-review` 與 `complete-task` 統一強制專案 Commit 規範優先順序（優先讀取 `docs/shared/engineering-standards.md` §4，回退至 `AGENTS.md`）與雙 Jira Key 追蹤（只要存在 `jira_subtask_key` 就需同時包含 `<STORY-KEY>` + `<SUBTASK-KEY>`）。
  - 升級 `create-subtasks`（`format_subtask_description`）以輸出結構化的 Jira Sub-task 描述（`description`、`implementation_steps`、目標 `files`、`ac_coverage`、`depends_on`、`verification`）。
- **多環境 QA 部署解耦（PR #16，Issue #4）：**
  - 新增選填 `qa_handoff_mode`（預設 `"immediate"`，可透過 `init --qa-handoff-mode` 設為 `"gated_by_deploy"`）與 `status_mapping.dev_done`（`init --status-dev-done`）。
  - 在 `"gated_by_deploy"` 模式下，`handoff --review-status approved` 會轉換至 `dev_done`、寫入綁定 commit 的 `qa_deploy_gate`（`status: "awaiting_qa_deploy"`）並暫緩 QA 重新指派／`@mention`；`record-qa --mode plan` 仍可平行執行，而 `record-qa --mode verify` 會被閘道擋下，直到 CI/CD 將 Jira 轉為 `in_qa`（經由 `status`/`pull` 同步）或顯式執行新子命令 `promote-qa`（`--key`、`--from-role`、`--tag`、`--environment`、`--note`、`--to-assignee`）。
- **Jira Bug 分類與受控發布關卡（PR #16，Issue #11）：**
  - 新增 `triage-bug` 子命令（`--key`、`--bug-id`、`--resolution`、`--reason`、`--decided-by`、`--duplicate-of`、`--waive-tcs`、`--tc-disposition`）與 `team-qa` 模式 4（`triage`），支援 `not-a-bug`、`wont-fix`、`duplicate`、`cannot-reproduce` 與 `fixed`（含重新開啟時將即時 Jira 狀態移出 `Done`）及 `fields.resolution` 同步（若 Jira 轉換畫面拒絕 `fields.resolution` 會優雅重試）。
  - 在 `close-story` 與 `status` 新增經一致性校驗、可稽核的 `waived` / `not-applicable` TC 發布關卡（絕不將豁免的 TC 標記為 `pass`），於發布摘要加入 **Bug Triage & Risk Acceptance Disclosure**，並在 `status` 支援 `triage_closed` 處理與篩選。
- **Bug 修復根因與解法紀錄（PR #16，Issue #12）：**
  - 新增 `complete-task --root-cause` 與 `--solution`，持久化於 active JSON（`bug_report` / `bug_fix_summary` / bug 項目）並呈現在 Jira 完成留言中而不覆寫原回報者描述；若修復 Bug 時未填寫則輸出非阻斷 `bug_fix_warnings`。

## v1.3.1 — 2026-11-23 — 行內 Markdown ADF 解析與嚴格 HITL / SoD Clean Stop

- **行內 Markdown 轉 Jira ADF（PR #14，Issue #6）：**
  - 升級 `jira_sync.py` 的 ADF 轉換器，支援將行內粗體、斜體、行內程式碼、連結、刪除線與巢狀格式解析為合法 Jira ADF marks，確保零空白文字節點且無不合法的 mark 組合。
- **嚴格 HITL 確認關卡與職責分離 Clean Stop（PR #14，Issues #9、#13）：**
  - 移除所有站點中未經確認的 `same_person: true` 自動串接；`same_person: true` 現在僅用於抑制重複的 Jira 自我 `@mention` 雜訊（`handoff_mode: "auto-consolidated"`），並暫停提出一個簡短確認問題。
  - 強制執行跨人員職責分離 Clean Stop（`same_person: false`），禁止提示下一角色的 slash 指令；輸出明確治理旗標（`can_auto_chain_next_station: false`、`requires_user_confirmation_for_next_station: true`、`cross_person_clean_stop`、`next_station_prompt_allowed`、`next_station_owner`）；新增 `team-qa` 入口身分護欄（`qa_identity_guardrail`），並在全部 8 個 Skills 強制執行 `Stop & Yield on Fix` 硬邊界。
  - *（註：Issue #10 為維護 Jira-only 第一性原理資產邊界，已以 `wontfix` 關閉。）*

## v1.3.0 — 2026-11-13 — Epic/Sprint 規劃層

- 新增 `team-plan`，支援 Epic 上下文、Future/Active Sprint 規劃、先預覽再套用的成員異動，以及唯讀 Sprint 狀態查詢。
- 在共用純標準函式庫 Jira CLI 新增 `sprint-plan` 與 `sprint-status`，包含看板解析、JQL／明確 Issue 挑選與每批 50 筆的 Issue 搬移批次處理。
- 更新既有 Sprint 計畫時，若有提供 Sprint 名稱／目標／日期欄位，會先更新 Jira Sprint 欄位再搬移 Issues。
- 將 `team-next-step`、共用治理規範、README 與套件檢查從 7 個擴充為 8 個自給自足的 Skills。
- 更新 `docs/training-manual.md` 以記錄 `team-plan`、13 個 CLI 子命令與 18 項測試套件。

## v1.2.0 — 2026-11-11 — 多人協作、Qase 與媒體同步、QA `report` 模式、動態手冊與 `run-workflow`

- **互動式驗證、漸進式角色認領與 `1-to-N` 角色成員池（`acf5c4a`、`0e99eac`、`b66a91f`、`45728e4`）：**
  - **`team-init`（`init` / `auth`）：** 新增前置檢查 `init --inspect`、互動式角色／憑證詢問、專屬於已加入 `.gitignore` 之 `.ai-workflow/credentials.json`（`0600`）的各角色 Jira API Token 解析，以及隱藏 Jira Issue Property（`ai_workflow_state`）同步。
  - **協作者上手與漸進式認領：** 專案建立者可僅先初始化自身角色（`claimed_roles` 對比 `unclaimed_roles`）；clone 程式碼庫的隊友可透過 `jira_sync.py auth --role <roles> [--specialty <tag>]` 綁定憑證並認領／加入角色，而不會覆寫既有角色成員（`1-to-N` `roles.<role>.members` 陣列）。當已設定即時 Jira 但缺少本機憑證時，寫入類命令會以 `requires_member_auth: true` 阻斷，而非靜默退回離線模式。
  - **端對端 Jira 自動指派與預設繁體中文（`zh-TW`）產出：** 在所有站點自動呼叫 `PUT /rest/api/3/issue/{key}/assignee` — 包括 Story 指派（`draft` 給 `pm`、`approved` 給 `developer`）、依 `specialty`（`backend`/`frontend` 等）配對的各 Sub-task 開發者指派、自 `roles.reviewer.members` 自動挑選同儕審查者，以及透過 `tc_id -> ac_id -> TASK-###` 追溯的 Bug 指派。將**繁體中文（Traditional Chinese）**設為產生 Jira 描述與留言的預設語言。
- **雙向 Jira Web UI 校對與遠端 Story 自動發現（`0e99eac`、`45728e4`）：**
  - `status`、`pull` 與所有寫入型站點命令皆會執行即時 Jira 狀態校對（`reconcile_story_with_jira`）：透過 `POST /rest/api/3/search/jql` 發現新建立的非 Done 專案 Stories、同步 Jira Web UI 手動新增或改狀態的 Sub-task／關聯 Bug、在手動改 Assignee 時更新 `story.role_owners`，並防止覆寫人工修改過的 Story 描述（`last_synced_description_hash`、`--accept-remote-description`、`--force-overwrite`）。
- **`team-dev` 領域專長 Skill 掛鉤與審查對齊離開關卡（`15f9938`）：**
  - 新增 Bring-Your-Own-Skill 組合指引（在 `engineering-standards.md` 之外同時載入環境中的 TDD、UI/UX 或框架 Skills），並加入與 `$team-review` 對齊的四點完成前離開關卡（模組複用、紅轉綠邊界測試、變更面完整性、乾淨驗證與靜態檢查）。
- **`team-qa` Qase TestOps 後端、媒體附件與 `report` 模式（`4883300`、`5561779`、`4dd2a07`）：**
  - **記憶式雙 QA 後端（`jira-comment` / `qase`）：** 透過 `record-qa --inspect` 一次選擇並保存於 `team-config.json`；`qase` 模式經由 Qase TestOps API v1（`credentials.json` 中的 `qase_api_token`）同步 Suites、Test Cases、Test Runs、批次結果與外部 Issue 連結，並消除 `plan` 階段的自我 `@mention`。
  - **媒體證據同步（`--attach` / `--tc-attach`）：** 將截圖、錄影與日誌上傳至 Jira `Bug` 附件；當 `qa_test_backend == "qase"` 時，同步附加至 Qase Test Run Results 與關聯的 Qase Defects（並於 `complete-task` 自動標記為已解決）。
  - **全新 `report` 模式（探索式與獨立線上 Bug）：** 支援開立未列於腳本的探索式 Story 缺陷（`create-bug --key <STORY-KEY>` 不帶 `--tc-id` → 追加 `TC-EXP-###` 而不影響已通過的 `TC-001..N`），以及登錄或納管獨立線上／客服 `Bug` 單（`create-bug --standalone [--adopt --key <BUG-KEY>]`），直接走輕量 `team-dev -> team-review -> team-qa (verify) -> team-release` 流程，無須 `team-spec` 或 Sub-task 儀式。
- **`team-release` 依規模自適應的動態 HTML 手冊工作流（`029b6c5`）：**
  - 新增四層決策矩陣（`0 -> 1` 全新建立、大型功能、小型增強、修補／重構略過），維護位於 `docs/manuals/<manual-name>/<manual-name>.html`（`./screenshots/`、`./diagrams/`）的獨立深色側邊欄 HTML 手冊與 5 項品質關卡，同時在 `close-story` 維持 `<candidate-commit-sha>` 關卡對齊。
- **系統 Skill `skills/system/run-workflow`（`05138bc`）：**
  - 新增系統 Skill，記載動態 Python 多代理工作流腳本（`run_workflow` 工具），涵蓋 `agent`、`parallel`、`pipeline`、`phase`、`trajectory`、多輪代理控制代碼與 `call_tool`。

## v1.1.0 — 2026-11-04 ~ 2026-11-09 — 規模自適應護欄、跨機器還原、三層 QA 與情境訓練套件

- **跨機器狀態還原、豐富 ADF 與零 CLI 體驗（`4b68164`、`28848ff`）：**
  - 新增從即時 Jira Issues 自動進行跨機器與 Git worktree 狀態還原、原生 Atlassian Document Format（ADF）表格／面板／徽章、`complete-task` 自動從 Git 偵測 `--commit` 與 `--files`、逗號分隔多 ID 完成（`TASK-001,TASK-002` / `REV-001,REV-002`）、`sync-story` 增量 `AC-###` upsert、QA `--tc` / `--result` 保留冒號與備註，以及情境感知的 `status` 提示。
- **規模自適應規格／開發軌道與三層 QA 防假通過護欄（`3d42ef3`）：**
  - **`team-spec`（`one-pass` 對比 `draft`）：** 為複雜／模糊需求新增多輪迭代 `draft` 深入模式與六維度 AC 完整性檢核表。
  - **Dev Fast-Track 複雜度護欄：** 僅限小型 Story（`<= 3` 條 AC 且風險為 `low`/`medium`）允許單步 `complete-task` 快速通道（自動建立 `TASK-001`）；較大或高風險 Story 必須先執行 `create-subtasks`（`plan` 模式）。
  - **三層混合式 QA（`automated` / `external` / `manual`）：** 保護 `external` 與 `manual` 測試案例不被 `--all-pass` 覆蓋，使未執行的外部／手動檢查維持 `not-executed`（`qa_status: "incomplete"`），直到透過報告或人工確認驗證為止。
- **情境式訓練手冊與 16 頁簡報（`c5e48d6`、`5bf9cdc`、`543c005`、`48946a4`、`5775bb9`）：**
  - 新增完整的繁體中文情境式訓練文件 `docs/training-manual.md`、獨立版 `docs/training-manual.html`、`docs/training-manual.pdf`、適用 Google Docs 的 `docs/training-manual.docx`、標註雙欄圖解（`docs/screenshots/`）與 16 頁訓練簡報（`docs/training-manual.pptx` + `docs/ppt-assets/`）。

## v1.0.0 — 2026-11-04 — 四站式 Jira 核心小團隊工作流（Issue #1）

將原本 11 個重度依賴 Markdown 的 Skills 精簡整合為 **7 個以 Jira 為核心的流線型 Skills**，專為一人可能身兼多職的小型團隊量身打造：

- **`team-init`（新增）：** 初始化 `.ai-workflow/team-config.json`（支援多角色合併的角色與人員對應、Jira 專案 Key、Base URL 與狀態對應）、確保 `.gitignore` 排除 `.ai-workflow/active/`，並將 `AGENTS.md` 中的工程規範抽離為 `docs/shared/engineering-standards.md`，同時把 `AGENTS.md` 中重複的規則精簡為參考連結。
- **`team-next-step`（更新）：** 唯讀路由器，檢查 `.ai-workflow/team-config.json`、`.ai-workflow/active/<JIRA-KEY>.json` 與 Jira 狀態（`jira_sync.py status`），推薦精確的下一站點、模式與負責人；同步更新 `evals/evals.json` 以符合 7-Skill 工作流。
- **`team-spec`（第一站 — 取代 `team-requirements`、`team-ux`、`team-design`、`team-doc-review`）：** 一次性的變更面分析，產出範圍、可測試的 `AC-001..N`、技術設計摘要、條件式 UI 狀態／線框／文案（`has_ui`）與條件式 API 契約（`has_api`）。內建**「抓大放小」**即時審查（常規細節由 AI 自行決策；最多只問 3 個關鍵問題）並透過 `jira_sync.py sync-story` 直接同步至 Jira `User Story`。
- **`team-dev`（第二站 — 取代 `team-dev-prep` 並加入實作執行）：** 與 QA 測試規劃解耦。`plan` 模式將核准規格拆解為 `TASK-001..N` 並建立 Jira `Sub-task`（`create-subtasks`）；`implement` 模式載入最小必要上下文、實作程式碼與測試，並透過 `complete-task` 在 `.ai-workflow/active/<JIRA-KEY>.json` 與 Jira 同步標記任務／Bug 為 `completed`（`Done`）。
- **`team-review`（第三站 a — 合併 `team-dev-handoff` 與 `team-review`）：** 檢視實際程式碼 diff、已完成任務證據、AC 與 `docs/shared/engineering-standards.md`（回退至 `AGENTS.md`）。將整合後的交接與三軸審查摘要張貼至 Jira，並依角色執行交接（`jira_sync.py handoff`：同一人時自動整合；不同人時轉換狀態、更新 Assignee 並 `@mention` 通知）。
- **`team-qa`（第三站 b — 合併 `team-qa` 與 `team-qa-evidence`）：** 精簡為 `plan` 模式（平行產出 `TC-001..N` 檢核清單，由 AI 預填 `automated`/`manual` 執行類型）與 `verify` 模式（透過 `record-qa` 匯入 JUnit XML、JSON 或手動結果；失敗時自動透過 `create-bug` 建立關聯 Jira `Bug` 單並退回開發者）。
- **`team-release`（第四站 — 更新）：** 驗證全部 5 項就緒關卡與零個未關閉 Bug、張貼發布摘要、透過 `jira_sync.py close-story` 將 Jira Story 轉為 `Done` 並刪除 `.ai-workflow/active/<JIRA-KEY>.json`，確保 Git 中不遺留任何單一功能流程檔。
- **共用檔案與工具：** `scripts/jira_sync.py`（支援即時 Jira REST API v3 與離線模式的純標準函式庫 Python 3 CLI）與 `references/common-workflow-governance.md` 在全部 7 個 Skills 中保持位元組完全一致，並由 `tools/check_shared.sh` 與 `tools/test_workflow.py` 驗證。

---

歷史 Issue ID 來源（v0.4–v0.7）：TeamBoard 驗證報告（`wilsonliu123/team-board` 中的 `docs/skill-validation/VALIDATION-REPORT.md`）與團隊筆記：D-xx（全端）、T-xx（Tech Leader）、UX-xx / UX-Sxx（UI/UX）、F-xx（PM）、「QA: topic」（QA 筆記，未編號）。

## v0.7.0 — 最終發布（KnowBase 實測回合）

- #8（殘留項目）：`frontend-template.md`、`backend-template.md`、`interface-template.md` frontmatter 新增 `review_status: not-started` 與 `validity: current`（隨 `sad.md` 一同審查；驗證器原本已讀取這些檔案的 `validity`）。`risk_level` 僅保留於 `sad.md`。
- #9（殘留項目）：team-design SKILL.md 明定 ADR 僅能存放於 `docs/shared/adr/`，即使外部指令提及 `docs/adr/` 亦然；當 `docs/adr/ADR-*.md` 存在時 `validate_design.py` 會失敗，避免這些 ADR 繞過已核准 ADR 檢查。
- #11（殘留項目）：team-design SKILL.md 明定僅因缺少工程規範而受阻的草稿不得進入審查；須先取得工程規範核准，再將設計設為 `complete`。
- #15：仍維持延後（原因見下方）。
- 自我檢查：`tools/check_shared.sh` OK；關卡情境（stamp、record、`--downstream` 帶參數／不帶參數、AT-RISK、鏈路 STALE、無紀錄 BLOCK）於全部 9 份副本行為完全一致；在 KnowBase `400f790` 重新執行 `validate_requirements`、`validate_design`、`validate_qa` 與 `validate_dev_prep` 全數 OK；`validate_review`、`validate_release`、`validate_dev_handoff` 與 `validate_review_session` 未重跑（無準備輸入）。

### 全部 30 項 KnowBase 問題最終狀態（#1–#29 加上 #18b）

| # | 狀態 | Commit | 備註 |
|---|---|---|---|
| 1 | fixed | `2419045` | team-requirements SKILL 標題從 v0.4 調整為 v0.6 |
| 2 | fixed | `2419045` | 第一階段無 `--record`；基準改由 `--stamp-approved` 建立 |
| 3 | fixed | `a752712` | UX 目錄上游納入狀態關卡（README.md） |
| 4 | fixed | `a752712` | 對缺失上游執行 `--record` 時寫入 `sha256: pending` |
| 5 | fixed | `a752712` | 關卡會檢查 frontmatter 宣告的所有上游 |
| 6 | fixed | `a752712` | 從治理規則移除 `in-review`（保留五種審查狀態） |
| 7 | fixed | `e2e715c` | 未核准的同階段上游記錄為 at-risk |
| 8 | fixed | `6ea24e2 + v0.7.0` | 標準鍵名（僅用 `artifact_type`、`reviewer_roles`）；v0.7.0：frontend/backend/interface 範本新增 `review_status`、`validity` |
| 9 | fixed | `6ea24e2 + v0.7.0` | 路徑為 `docs/shared/adr/`；v0.7.0：SKILL.md 明確記載，且 `validate_design` 對位於 `docs/adr/` 的 ADR 報錯 |
| 10 | fixed | `e2e715c` | UX 上游記錄為 at-risk，使設計可在 UX 尚未存在前宣告 `ux_reference` |
| 11 | fixed | `6ea24e2 + v0.7.0` | `validate_design` 對 draft／缺少工程規範／Blocking OQ 報錯，並檢查完整 REQ/AC 覆蓋率；v0.7.0：SKILL.md 說明因缺少規範受阻的草稿如何進入審查 |
| 12 | fixed | `b7e2f51` | `validate_qa` 對 stale/unknown 報錯 |
| 13 | fixed | `9ebbbe8` | 所有驗證器共用 `validity_problems()` |
| 14 | fixed | `444f68e` | 驗證器重新計算已記錄上游基準的雜湊值 |
| 15 | deferred | — | 見下方「延後項目」：需新增 UX skill 或 `validate_ux.py`；本回合不新增 Skill |
| 16 | fixed | `5f064ed` | 臨時自創的 `*_sha256` 欄位一律 BLOCK |
| 17 | fixed | `3133ff1` | 支援 Unicode 的角色名稱正規化 |
| 18 | fixed | `a9dfdea` | `engineering-standards` 納入受治理上游（文件） |
| 18b | fixed | `291b4bc` | `DECLARED_KEYS` 納入 `engineering_standards` |
| 19 | fixed | `27e9619` | `GOTOOLCHAIN=local` 不屬於版本鎖定 |
| 20 | fixed | `76cb23c` | 需求文件的 `ux_reference`/`engineering_standards` 為參考而非上游 |
| 21 | fixed | `b5d62dc` | 移除 dev-prep 的 `.gitignore` 檢查 |
| 22 | closed | `b5d62dc` | status 模式 BLOCK 對比 record 模式 AT-RISK 屬預期行為；已補齊文件 |
| 23 | fixed | `b5d62dc` | 接受 `- Enabling work: <reason>` |
| 24 | fixed | `c5f8f89` | 在 team-dev-prep 定義編碼階段逐任務契約（不新增 Skill） |
| 25 | fixed | `57b0642` | 修正交互參照（step 4） |
| 26 | fixed | `6606ea6` | test-cases 新增設計約束覆蓋表 |
| 27 | fixed | `f004576` | XSS 測試案例分別驗證跳脫與 CSP |
| 28 | fixed | `45c8a13` | `--downstream` 不帶參數時讀取已記錄的上游 |
| 29 | fixed | `45c8a13` | 單層鏈路 STALE 檢查；完整遞移遍歷延後 |

## v0.6.8

- KnowBase #17：`check_upstream_gate.py` 的 `norm()` 原本僅保留 `[a-z0-9]`，導致純中文角色名稱（如「全栈工程师」）被壓縮為空字串 `''` 而誤判不同角色相同，繞過「作者不得自審」檢查。現改為支援 Unicode（`[\W_]`、`casefold`、去除全形括號）。同步套用於全部 9 份副本。
- KnowBase #18：治理文件現明定 `docs/shared/engineering-standards.md` 為受治理上游（標準 frontmatter、一次核准、納入記錄、編輯後變為 STALE），不再處於未定義狀態。僅更新文件，關卡腳本未變。
- KnowBase #19：team-dev-prep 曾誤以 `GOTOOLCHAIN=local` 作為版本鎖定範例，但該設定會停用工具鏈下載並改用本機 Go。範例現改為要求在 `make setup` 安裝確切版本工具鏈，並在 `make ci` 加入 `go version` 檢查。
- KnowBase #18b：關卡 `DECLARED_KEYS` 現已包含 `engineering_standards`，下游若宣告了工程規範卻未記錄則會被 BLOCK；`engineering_standards: unavailable` 則略過。同步套用於全部 9 份副本。
- KnowBase #20：在需求文件中，關卡不再將 `ux_reference:` / `engineering_standards:` 視為宣告上游（兩者皆位於需求下游，記錄會造成循環相依）。治理規範明定其僅為參考；其他產物類型不受影響。
- KnowBase #21：team-dev-prep 不再要求合併 `.gitignore.ai-workflow`（該檔案從未存在）；保留 git-identity 檢查。
- KnowBase #22：關卡使用說明明確記載純 status 模式對未核准上游回傳 BLOCK，而 `--record`/`--downstream` 回傳 AT-RISK 屬刻意設計。同步套用於全部 9 份副本。
- KnowBase #23：`validate_dev_prep.py` 接受 `- Enabling work: <reason>` 取代 REQ 引用，與 SKILL.md 一致；task-template 同步新增該行。
- KnowBase #24：當時尚未獨立出編碼 Skill。在不新增 Skill 的前提下，team-dev-prep 的「Handoff to the coding stage」定義了逐任務契約：入口關卡（對 dev-prep index 執行 `--downstream`）、逐任務證據、審查紀錄路徑、E2E 執行路徑（僅正式路徑算數）與 `docs/ux-acceptance/` 下的 UX 驗收。team-dev-handoff step 4 引用此契約；驗證器未變。
- KnowBase #25：編碼契約第 2 項原指向 team-dev-handoff「§5」，實際應為 workflow step 4。僅修正文字。

## v0.6.7 — KnowBase #16

- 沒有任何 Skill 範本定義 `requirements_approved_sha256`；那是會過期的臨時副本欄位。共用 `validity_problems()` 現會 BLOCK 除了 `approved_sha256` 以外的所有 frontmatter `*_sha256` 鍵名；治理規範明定僅允許 `approved_sha256` 與 `upstream:` 作為雜湊欄位。

## v0.6.11

- #28：`check_upstream_gate.py --downstream <down.md>` 不帶上游參數時，現會直接檢查其 `upstream:` frontmatter 中已記錄的上游（先前會印出使用說明，與「passed or recorded」矛盾）；若未記錄任何上游則 BLOCK。`--record` 仍維持要求明確傳入清單（與使用說明一致）。同步套用於全部 9 份副本。
- #29：關卡會重新計算每個上游本身所記錄的 `upstream:` 基準雜湊（單層）；若有變更則回報 `STALE: chain <up> -> <its upstream>` 並在 status、`--record` 與 `--downstream` 模式下失敗（exit 1）。延後項目：完整遞移遍歷。同步套用於全部 9 份副本。

## v0.6.10

- #27：team-qa step 2 明定 XSS 測試案例須分開驗證跳脫與 CSP（無 CSP 正對照組、以 DOM 斷言作為主要證據、零 `securitypolicyviolation`）。

## v0.6.9

- #26：team-qa test-cases 步驟必須在 `## Design constraint coverage` 列出所有設計資安約束（對應 TC 或 `n/a: reason`）；由 `validate_qa.py` 強制檢查，範本已同步更新。

## 延後項目（不在範圍內；修復期間不新增 Skill）

- v0.7.0 延後項目：KnowBase #15 需要新增 UX Skill 或不屬於任何 Skill 的 `validate_ux.py`，兩者皆等同新增 Skill，違反該回合規則。關卡的狀態／雜湊檢查仍涵蓋 UX 套件，待未來另立 UX Skill 時再行處理。同時延後：完整遞移鏈路遍歷（#29）、引用但缺失的 ADR 檔案尚未報錯（#9）。

- KnowBase #15 — 針對 UX 套件的 `validate_ux.py`（本組 Skills 尚無 UX Skill）。UI/UX 提出的候選檢查項：(1) README 狀態矩陣逐格對應需求 §5，不得留白，N/A 須附與需求一致的理由；(2) README §1 以完整 ID 列出所有畫面相關 AC（例如 `AC-033`，而非 `/033`）；(3) 不得殘留【提案】文案，非需求原文提供的文案須引用決策中的 PD ID；(4) README 列出的每張 PNG/SVG 皆須存在。在此期間：gate/`--downstream` 涵蓋治理與雜湊檢查，`validate_design.py` 涵蓋下游的畫面、情境矩陣與完整 REQ/AC 覆蓋檢查。

## v0.6.6 — KnowBase #14

- 共用 `validity_problems()`（全部 9 份副本）同時重新計算每個已記錄 `upstream:` 基準的雜湊：上游已變更 => STALE、`pending` 狀態 => PENDING、路徑缺失 => BLOCK。現在每個階段的驗證器即使負責人尚未手動標記 `validity: stale`，結果也與 `check_upstream_gate.py --downstream` 完全一致。

## v0.6.5 — KnowBase #13（並全面掃描同類缺口）

- 在 `check_upstream_gate.py`（全部 9 份副本）新增共用 `validity_problems()`：`validity: stale|unknown` => BLOCK。
- 由每個階段的驗證器與 `approval_problems()` 一併呼叫：requirements、design（sad + frontend/backend/interface）、dev-prep、review、QA（所有 QA 檔案含 test-cases，取代 v0.6.4 的局部檢查）、dev-handoff、release。
- `validate_review_session.py` 未更動：審查工作階段不含 `validity` 欄位。

## v0.6.4 — KnowBase #12

- 當任何 QA 產物的 `validity` 為 `stale` 或 `unknown` 時，`validate_qa.py` 現會失敗（exit 1）；過去曾誤報 OK。

## v0.6.3 — KnowBase 實測修復（skill-issues #8、#9、#11）

- #11（重要）：當 `sad.md` 為 `draft`、缺少 `docs/shared/engineering-standards.md` 或 §8.3 存在 `Blocking: Yes` 待確認問題時，`validate_design.py` 現會失敗；REQ/AC 覆蓋檢查會跨 sad/frontend/backend/interface 逐一驗證需求中的每個 ID 並列出缺失 ID（過去僅需任意一筆符合）。
- #8：範本統一使用標準治理鍵名：僅用 `artifact_type`（不用 `document_type`）、`reviewer_roles` 清單（不用 `reviewer_role`）。
- #9：無需修改 Skill：所有 Skill 檔案與 `validate_design.py` 原本即使用 `docs/shared/adr/`；`docs/adr/` 來自委派指令而非 Skill 本身。

## v0.5 — 2026-10-13

### P0-1 關卡比對上游內容（報告 §3，P0-1）
修復：D-15、T-01、D-05、D-07、D-18、T-05、F-18、F-20、UX-02、UX-07、UX-S11、QA：刻意注入／核准後竄改。
- `check_upstream_gate.py`（team-design、team-dev-prep、team-dev-handoff、team-review、team-qa、team-release 共用同一檔案）：
  - `--stamp-approved <artifact>` 寫入 `approved_sha256`；後續在 `approved` 狀態下修改內容 → `STALE`，exit 1。
  - `--record <downstream> <upstream...>` 通過關卡後，將 `upstream: [{path, sha256}]` 寫入下游 frontmatter。
  - `--downstream <downstream> <upstream...>` → 任一上游變更回傳 `STALE`（exit 1）；未記錄基準回傳 `BLOCK`。
  - 雜湊計算忽略 frontmatter；`sad.md` / `index.md` 會對整個套件（含附件）計算雜湊；目錄（如 `docs/ux/<slug>/`）作為整體單位計算雜湊，無需新欄位即可涵蓋線框圖與 UI 驗收。
  - 純 `check_upstream_gate.py <upstream...>` 維持原有行為（若存在 `approved_sha256` 欄位亦一併檢查）。
- `common-artifact-governance.md`（全部 9 份副本）：「Baseline and stale detection」改為說明由腳本維護的欄位，取代手寫的核准沿用備註。
- SKILL.md 關卡指引（5 個受管 Skills 加 team-qa）改用 `--record` / `--downstream`；team-doc-review 於核准時寫入 `approved_sha256`。

### P0-2 證據綁定基準並可作廢（報告 §3，P0-2）
修復：QA：rev3 證據作廢（EVD-024/025/009）、QA：`validate_qa` 未檢查證據 commit、根本原因 3。
- `normalize_evidence.py` / `write_manual_evidence.py`：`--requirements` / `--test-plan` 記錄 `baseline.{requirements,test_plan}.{path,sha256}`；紀錄帶有 `validity: current|void` 與 `void_reason`。
- 新增 `void_evidence.py`（約 15 行，位於 team-qa-evidence），以原因與選填 `superseded_by` 將證據標記為作廢。
- `validate_qa.py --evidence-dir <dir>` 拒絕：基準內容已變更的證據、無基準的證據、無作廢理由的作廢證據，以及 `target.commit` ≠ `tested_commit`。作廢紀錄會保留但排除於統計外；未帶 `--evidence-dir` 時維持原有行為。
- 同步更新 evidence-contract.md、team-qa-evidence SKILL.md 與 team-qa 模式 5。

### P2（原 P0-3）`decided_by`
修復：QA：執行指派（已由剑秋於 10/13 釐清：原設計正確）。
- `execution-matrix-template.md` 新增 `decided_by` 欄位；缺少時 `validate_qa.py` 印出 WARN（而非錯誤），流程不變。

### P1/P2 小型修復
- 所有驗證器在參數錯誤時印出用法說明（T-02、QA：`validate_qa` 接受目錄參數；同時註明「請於程式碼庫根目錄執行」）。
- `normalize_evidence.py`：EVD 編號接續既有最大編號（QA：EVD 編號覆寫問題）。未帶 TC ID 的測試改為印出 WARN 並累計 `dropped_without_tc`（D-08、QA：正規表達式靜默丟棄測試）。`executed_at` 設為 `unknown` 而非匯入時間。補齊 TC 正規表達式與 Go 命名說明（D-16、T-06）。
- 歸檔至 `evidence/<sha>/` 正式納入 team-qa-evidence 標準步驟（UX-S10、UX-S16、QA：run2）。
- qa-report 範本新增 §8.1 已知限制、§8.2 可接受的非缺陷問題（QA：final-assessment、QA：template）。
- team-requirements 術語表列出保持不翻譯的產品專有名詞（UX-S14、D-10）。

### QA / Tech Leader 審查後續修正
- `check_upstream_gate.py`（6 份相同副本）：處於 `approved` 但缺少 `approved_sha256` 的上游，不論是否使用 `--downstream` 現一律回報 `BLOCK`（exit 1）並提示 `--stamp-approved`。**遷移方式：** 對每份已核准的 v0.4 文件執行一次 `--stamp-approved`；在此之前，針對 v0.4 產物的所有關卡皆會 BLOCK（預期行為）。
- `validate_qa.py`：當 `qa-report.md` 存在時，必填 `--evidence-dir`。**遷移方式：** 缺少基準的證據須以 `--requirements` / `--test-plan` 重新匯入。
- team-qa SKILL.md：同步對 test-cases 基準執行 `--record` / `--downstream`；新增遷移說明，qa-report 範本與治理文件皆記載此兩條規則。

## v0.6.2

- `check_upstream_gate.py --record`：若上游存在但尚未核准（draft、in progress 或 changes requested），現會記錄其雜湊值並標記 `status: at-risk`，腳本印出 AT-RISK 並以 exit 1 結束。Pending 處理不變。
- 關卡與 `--downstream`：
  - 遇到 at-risk 項目時回傳 AT-RISK 並 exit 1。
  - 若 at-risk 上游的雜湊值已變更，結果為 STALE。
  - 一旦上游獲得核准，關卡會提示重新執行 `--record` 以清除該狀態。
- 被拒絕（rejected）與已作廢（void）的上游仍維持 BLOCK。
- 治理規則：同一階段有兩份產物同時審查時，先核准並蓋章（stamp）上游，再重新記錄（re-record）下游，最後核准下游。
- 測試：將 QA test-cases 案例（skill-issues #7）加入 `knowbase-gate.sh`，執行紀錄位於 `docs/experiments/stale-gate.md`。

## v0.6.1 — 2026-10-13（KnowBase 即時修復，全部 9 份 `check_upstream_gate.py` 副本）

- 目錄型上游（如 `docs/ux/<slug>/`）現由 `<dir>/README.md` frontmatter 取得治理資訊，比照檔案進行狀態與核准檢查。`--stamp-approved <dir>` 會寫入該 README；雜湊涵蓋整個目錄並排除 README frontmatter。缺少 README 或目錄時回傳 BLOCK（先前目錄一律通過狀態檢查）。
- 對尚不存在的上游執行 `--record` 會儲存 `sha256: pending`；關卡回報 `PENDING` 並 exit 1，直到上游建立後重新執行 `--record` 新增或更新項目。
- 在 `--record` / `--downstream` 模式下，關卡會讀取下游 frontmatter 中宣告的所有上游（`requirements`、`design`、`test_spec`、`test_cases`、`test_plan`、`execution_matrix`、`review`、`dev_prep`、`dev_delivery`、`ux`、`ux_reference`、`source_requirements`）。若已宣告的上游既未傳入也未記錄則回傳 BLOCK；`--downstream` 亦會重新檢查所有已記錄上游。
- 治理文件（全部副本）：記載上述三條規則，並明定 `review_status` 僅有五種值（無 `in-review`）。
- 實驗腳本：新增 `knowbase-gate.sh`；調整 `migrate-teamboard.sh`（UX README frontmatter、test-cases 記錄 test-spec）。

## v0.6 — 2026-10-13：修復所有延後項目

Commits：`22612ad` core · `a8c6fc3` requirements · `04058f2` design · `674628e` dev-prep/handoff · `471bc5a` review/release · `791380e` QA+evidence · `36620ef` doc-review/telemetry · `8bb8d1e` fixes · `cdcf367` experiment。較早提交：`8bd5b73` P0-1 · `dbbd1b6` P0-2 · `68beea9` decided_by · `4a088c5` small fixes · `025dc0b` gate/QA blockers。

**從 v0.4 遷移：** 請參閱 `common-artifact-governance.md` 中的「Migrating v0.4 artifacts」以及實作範例 `docs/experiments/migrate-teamboard.sh`。

### Issue → commit

| Issue IDs | 修復內容 | Commit |
|---|---|---|
| D-15, T-01, D-05, D-07, D-18, F-18, F-20, UX-02, UX-07, UX-S11, QA: injection experiment / post-approval edits (a4f891d, d322f75, 02f2daf, 3ca9aa4) | 內容雜湊關卡、套件與 UX 目錄雜湊、`approved_sha256` | 8bd5b73, 025dc0b |
| QA: rev3 evidence void, QA: evidence commit check, report root cause 3 | 證據基準與作廢機制；`validate_qa` 拒絕不合規證據 | dbbd1b6, 025dc0b |
| Hash unification, evidence binds sad.md, `--evidence-dir` usage | 統一為單一 `content_hash()` 供各處匯入；證據綁定設計基準 | 22612ad |
| "approved but changed" in stage validators | 所有階段驗證器納入 `approval_problems()` | 22612ad |
| F-11, F-19, F-20, QA: delegated approver = author | `approved_by_role`、`delegation_reference`、`independent_reviewer_role`（強制執行） | 22612ad, 8bb8d1e |
| D-04, D-14, QA: execution-handoff with unapproved upstream | `proceed_at_risk` + AT-RISK；處於風險狀態時下游不可核准；執行前置檢查 | 22612ad, 674628e, 791380e |
| F-02, F-04, F-03, F-07, QA: unconfirmed assumptions | 初始值；Blocking OQ ⇒ draft（驗證）；無人工介入規則；僅用 `artifact_type` + feature | 22612ad, a8c6fc3 |
| F-15, F-16, F-17, QA: testability approval/stale | testability 無獨立核准並透過 `--record` 偵測過期；範圍變更會重新觸發 | 22612ad |
| F-21, D-18 (shared worktree), T-08, QA: git identity | `git add` 僅加入自身路徑；環境步驟設定 repo-local 身分 | 22612ad, 674628e |
| F-22 | `review_mode` 批次模式 | 22612ad, 36620ef |
| P2-5, QA: duplication | 維持每 Skill 獨立副本（自給自足）；新增 `tools/check_shared.sh` 漂移防護；修復 team-dev-handoff `review-protocol.md` 既有漂移 | 22612ad |
| F-05, F-14, QA: traceability consistency (G-03) | 多重 Covers、未知 REQ、Must→AC、§13 == Covers | a8c6fc3 |
| F-06, F-10, F-12, F-13, UX-01, UX-06, QA: five situations / screen list | 需求範本新增畫面清單、五情境矩陣（403/404）、交付約束、決策、`ux_reference` | a8c6fc3 |
| D-10, UX-S14 | 需求為文案唯一權威來源；術語表含不翻譯專有名詞 | 4a088c5, a8c6fc3, 04058f2 |
| UX-03, UX-04, UX-05, UX-08 | 前端情境矩陣與指引；`validate_design` 檢查畫面／矩陣／附件 | 04058f2 |
| D-03 | ADR 隨設計核准並蓋章；加入驗證器 | 04058f2, 36620ef |
| T-04 | 審查產生的設計變更路由回 team-design | 04058f2, 471bc5a |
| D-01, D-06, D-13, F-00, F-01, QA: environment | dev-prep step 8a + index §5a（僅檢查；`make setup` 由首個編碼任務負責） | 674628e |
| D-02 | `validate_dev_prep`：REQ 引用、覆蓋率、相依性與循環檢查 | 674628e |
| D-09 | dev-delivery `upstream:` 納入 test plan 與 UX | 674628e |
| T-02 | 參數用法說明訊息 | 4a088c5 |
| T-03 | `validate_review`：`head_commit` 須存在且其後無較新程式碼提交 | 471bc5a |
| T-05 | 以重新蓋章／重新記錄取代沿用備註；加入編修規則 | 22612ad, 36620ef |
| T-06, D-16 | QA 擁有 TC ID／子測試名稱；`Automated test name` 欄位；審查透過 QA 對應回歸 | 471bc5a, 791380e |
| T-07, D-12, F-08 | `emit_event` `end` 別名、current-session 檔案、補齊呼叫文件 | 36620ef |
| T-09 | 增量重審 §9a | 471bc5a |
| QA: severity names | 統一嚴重度分級 blocker/major/minor/note | 471bc5a |
| D-08, QA: regex drop / numbering / executed_at / skipped | WARN + 計數、延續編號、unknown 時間、skipped→not-executed | 4a088c5, 791380e |
| QA: flaky, partial, negative (CI-must-fail), report numbers vs evidence, void listing, generated table, decided_by strict, AC coverage, test-cases fields, testability conclusion/rounds/closed gaps, reviewer roles, Mode 4/5 upstream sets, manual attachments, evidence-index schema, commit normalized JSON | `validate_qa` + 範本 + `qa-method` 全面強化 | 791380e, 8bb8d1e |
| P2-2 (generated table, trim test-cases) | `--emit-table`；省略選填欄位 | 791380e |
| D-11, D-17, D-19 | `qa-method` 測試資料／隔離／伺服器日誌規範 | 791380e |
| UX-S9, UX-S12, UX-S13, UX-S15 | ui-acceptance 範本、`<Screen>-<situation>` 命名、手動負責人＋證據連結、互動狀態證據 | 791380e |
| UX-S10, UX-S16, QA: run2 archive | `evidence/<sha>/` 歸檔納入標準步驟 | 4a088c5, 791380e |
| Pre-existing review-session failures | 已完成的審查工作階段不再要求 `current_item` / Remaining items（Skill 缺陷） | 36620ef |
| P0-3 → P2 | `decided_by`（改為錯誤等級） | 68beea9, 791380e |
| F-09 | 無需修改（關卡原本即會阻斷未核准上游） | — |

### 刻意保留的未實作項目與理由
- **改用單一共用副本取代各 Skill 複製（P2-5）：** 刻意不採用。每個 Skill 必須維持自給自足（各自獨立打包為 zip），因此約束條件為保持各副本位元組完全一致，並由 `tools/check_shared.sh` 強制把關。
- **「REQ 規則 → TC」數值索引（核心缺陷中的 QA 建議 5）：** 未新增，因為那會成為必須人工維護的索引。影響分析已由內容雜湊關卡（STALE）、證據綁定與「每條 AC 皆有對應 TC」檢查涵蓋。
- **舊版 TeamBoard 審查工作階段：** 有 5 份舊工作階段缺少 `review_session_status`、`artifact_path` 或 `## Resolved items`。這屬於既有內容撰寫問題而非 Skill 缺陷，因此在人工補齊前仍會驗證失敗；至於 Skill 本身的缺陷（對已完成工作階段要求待審佇列）則已修復。

### 歷史紀錄：兩種雜湊格式（已於 22612ad 解決）
證據的 `requirements` 雜湊與關卡的 `approved_sha256` 雖然都在移除 frontmatter 後計算，但原本採用不同演算法：`check_upstream_gate.py` 對每個檔案計算 `name + \0 + sha256(body) + \n`（以同時支援目錄），而 `validate_qa.py` / 證據匯入則直接對內文計算雜湊。兩者各自都能正確偵測過期，僅無法互相比較。曾延後整合為單一函式。

已於 `22612ad` 解決：雜湊函式已統一；證據綁定 `sad.md`（設計變更僅由關卡捕捉）；用法說明行仍將 `[--evidence-dir]` 置於方括號中。
