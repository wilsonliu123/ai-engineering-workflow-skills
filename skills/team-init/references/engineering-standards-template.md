---
artifact_id: ENG-STANDARDS
artifact_type: engineering-standards
validity: current
owner_role: Tech Lead
---
# Shared Engineering Standards (工程標準規範)

本文件為專案唯一權威的工程規範來源（Single Source of Truth）。`AGENTS.md` 僅保留索引連結，不重複維護以下規則。

## 1. Architecture & Module Boundaries (架構與模組邊界)
- 優先沿用專案既有模組結構與設計模式，不新增未經授權的平行抽象層。
- 跨模組、影響資料相容性或難以逆轉的重大技術決策，須建立 `docs/shared/adr/ADR-<NNN>-<slug>.md`。

## 2. Testing & Evidence Requirements (測試與驗證標準)
- 每個開發子任務（`TASK-001..N`）與缺陷修復（`BUG-*`）完成前，必須執行自動化測試並透過 `jira_sync.py complete-task` 記錄 Commit SHA、變更檔案與真實測試結果。
- 測試案例（`TC-001..N`）必須完整對應驗收條件（`AC-001..N`），並區分為三種執行類型：
  - `automated`：本機 AI 可直接執行的單元與整合測試（如 `pytest`、`vitest`、`go test`）。
  - `external`：需依賴外部工具、CI/Staging 環境或第三方沙盒的測試（如 Playwright/Cypress E2E、Postman/Newman、JMeter、實機測試）。
  - `manual`：需人工進行視覺體驗、探索式或跨系統核對的測試。
- 嚴格防假通過（Anti-False-Pass）：`--all-pass` 僅能將 `automated` 測試標記為 `pass`；`external` 與 `manual` 測試案例必須匯入報告或顯式回填結果，未執行或缺少證據一律標記為 `not-executed`，嚴禁視為 `pass`。

## 3. Security & Data Hygiene (安全與資料衛生)
- 嚴禁在程式碼、設定檔（含 `.ai-workflow/team-config.json`）、日誌或版本控制中寫入 API Token、密碼或私鑰。
- 所有外部輸入與跨邊界介面必須執行結構驗證、權限檢查（AuthN/AuthZ）與輸出跳脫處理。

## 4. Git Commit & PR Standards (Git 提交與 PR 規範)
- 本節為專案 Git Commit 格式、語言、前綴/Emoji 與 Pull Request 慣例的唯一真相來源（Single Source of Truth；若尚未收斂，fallback 參照專案根目錄 `AGENTS.md`）。工作流技能不硬編碼單一格式，請在此維護專案專屬的 Commit 模板與 PR 規則。
- **雙重 Jira 票號溯源（Dual Jira Key Traceability）**：無論專案採用何種 Commit 格式，只要任務已建立 Jira Sub-task（`jira_subtask_key`），Commit 訊息必須同時包含父級 Story Key（`<STORY-KEY>`）與子任務 Key（`<SUBTASK-KEY>`）——若與專案標題模板相容則置於標題，否則置於 Commit 內文或 trailer——以讓 Jira Development Panel 自動將提交同時關聯至父 Story 與子任務。
- **提交確認門禁（`commit_policy`）**：預設為 `.ai-workflow/team-config.json` 的 `"commit_policy": "confirm_before_commit"`（完成實作與本地測試後停下回報，待使用者驗證確認後再執行 `git commit` 與 `complete-task`）；僅於使用者明確授權或團隊設定 `"auto_on_test_pass"` 時才自動提交。可選設 `"commit_message_pattern"`（正規表達式）於 `complete-task` 提供非阻塞格式檢查提醒。
